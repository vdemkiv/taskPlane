"""Required real Chrome/Chromium conformance for dashboard delivery.

The harness deliberately uses only the Python standard library.  A missing
declared browser is an environment failure, never a skipped test; launch,
loopback, or DevTools protocol failures are infrastructure failures.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Mapping
import urllib.request


TASKPLANE = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name("fixtures") / "dashboard-browser"
sys.path.insert(0, str(TASKPLANE))

import dashboard  # noqa: E402
import views  # noqa: E402


class BrowserEnvironmentError(RuntimeError):
    """The authoritative browser cell has no usable declared browser."""


class BrowserInfrastructureError(RuntimeError):
    """The declared browser exists, but its bounded harness failed."""


def _json_fixture(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    payload = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(payload).hexdigest()


def _declared_browser(config: Mapping[str, Any]) -> tuple[str, str, str]:
    for name in config["executable_environment"]:
        declared = os.environ.get(str(name))
        if not declared:
            continue
        path = os.path.abspath(declared)
        if not os.path.isfile(path) or not os.access(path, os.X_OK):
            raise BrowserEnvironmentError(
                f"environment failure: {name} declares an unavailable browser: "
                f"{path}"
            )
        return _browser_version(path, str(name))

    for candidate in config["executable_candidates"]:
        path = os.path.abspath(str(candidate))
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return _browser_version(path, "fixture-candidate")
    raise BrowserEnvironmentError(
        "environment failure: no declared Chrome/Chromium executable exists; "
        "set TASKPLANE_BROWSER_EXECUTABLE, CHROME_BIN, or CHROMIUM_BIN"
    )


def _browser_version(path: str, source: str) -> tuple[str, str, str]:
    try:
        result = subprocess.run(
            [path, "--version"], text=True, encoding="utf-8",
            errors="replace", capture_output=True,
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BrowserEnvironmentError(
            f"environment failure: declared browser cannot report a version: {exc}"
        ) from exc
    version = (result.stdout or result.stderr).strip()
    if result.returncode or not version or not any(
            label in version.casefold() for label in ("chrome", "chromium")):
        raise BrowserEnvironmentError(
            "environment failure: declared executable is not a usable "
            f"Chrome/Chromium browser: {path} ({version or result.returncode})"
        )
    return path, version, source


class _QuietHandler(__import__("http.server").server.SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        del args


class _LoopbackServer:
    def __init__(self, root: Path):
        self.root = root
        self.httpd = None
        self.thread = None

    def __enter__(self) -> "_LoopbackServer":
        import http.server

        handler = functools.partial(_QuietHandler, directory=str(self.root))
        try:
            self.httpd = http.server.ThreadingHTTPServer(
                ("127.0.0.1", 0), handler)
        except OSError as exc:
            raise BrowserInfrastructureError(
                f"infrastructure failure: loopback fixture server failed: {exc}"
            ) from exc
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            name="taskplane-dashboard-browser-fixture",
            daemon=True,
        )
        self.thread.start()
        return self

    @property
    def origin(self) -> str:
        assert self.httpd is not None
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def url(self, relative: str) -> str:
        return f"{self.origin}/{relative.lstrip('/')}"

    def __exit__(self, *_exc: object) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)


class _WebSocket:
    """Small RFC 6455 client sufficient for local Chrome DevTools JSON."""

    def __init__(self, url: str):
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        if parsed.scheme != "ws" or parsed.hostname not in {
                "127.0.0.1", "localhost"}:
            raise BrowserInfrastructureError(
                "infrastructure failure: DevTools endpoint is not loopback ws"
            )
        port = parsed.port or 80
        try:
            self.socket = socket.create_connection(
                (parsed.hostname, port), timeout=5)
        except OSError as exc:
            raise BrowserInfrastructureError(
                f"infrastructure failure: DevTools socket failed: {exc}"
            ) from exc
        self.buffer = b""
        key = secrets.token_urlsafe(16)
        target = parsed.path + (("?" + parsed.query) if parsed.query else "")
        request = (
            f"GET {target} HTTP/1.1\r\nHost: {parsed.hostname}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.socket.sendall(request)
        response = self._read_headers()
        if not response.startswith(b"HTTP/1.1 101"):
            self.close()
            raise BrowserInfrastructureError(
                "infrastructure failure: Chrome refused the DevTools websocket"
            )

    def _read_headers(self) -> bytes:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.socket.recv(4096)
            if not chunk:
                break
            data += chunk
            if len(data) > 64 * 1024:
                break
        headers, separator, remainder = data.partition(b"\r\n\r\n")
        if separator:
            self.buffer = remainder
            return headers + separator
        return data

    def _read_exact(self, size: int) -> bytes:
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        while len(data) < size:
            chunk = self.socket.recv(size - len(data))
            if not chunk:
                raise BrowserInfrastructureError(
                    "infrastructure failure: Chrome closed the DevTools socket"
                )
            data += chunk
        return data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = secrets.token_bytes(4)
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(value ^ mask[index % 4]
                       for index, value in enumerate(payload))
        self.socket.sendall(bytes(header) + masked)

    def send_json(self, value: Mapping[str, Any]) -> None:
        self._send_frame(0x1, _canonical(value))

    def receive_json(self, timeout: float) -> dict[str, Any]:
        self.socket.settimeout(timeout)
        fragments: list[bytes] = []
        while True:
            first, second = self._read_exact(2)
            final, opcode = bool(first & 0x80), first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if second & 0x80 else None
            payload = self._read_exact(length)
            if mask is not None:
                payload = bytes(value ^ mask[index % 4]
                                for index, value in enumerate(payload))
            if opcode == 0x8:
                raise BrowserInfrastructureError(
                    "infrastructure failure: Chrome closed DevTools early"
                )
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode in (0x0, 0x1):
                fragments.append(payload)
                if final:
                    try:
                        value = json.loads(b"".join(fragments).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise BrowserInfrastructureError(
                            "infrastructure failure: malformed DevTools response"
                        ) from exc
                    if not isinstance(value, dict):
                        raise BrowserInfrastructureError(
                            "infrastructure failure: non-object DevTools response"
                        )
                    return value

    def close(self) -> None:
        sock = getattr(self, "socket", None)
        if sock is None:
            return
        with contextlib.suppress(OSError):
            self._send_frame(0x8, b"")
        with contextlib.suppress(OSError):
            sock.close()
        self.socket = None


class _RealBrowser:
    def __init__(self, tmp_path: Path, config: Mapping[str, Any]):
        self.tmp_path = tmp_path
        self.config = config
        self.process = None
        self.ws = None
        self.command_id = 0
        self.executable = ""
        self.version = ""
        self.executable_source = ""

    def __enter__(self) -> "_RealBrowser":
        (self.executable, self.version,
         self.executable_source) = _declared_browser(self.config)
        profile = self.tmp_path / "chrome-profile"
        profile.mkdir()
        flags = [str(value) for value in self.config["flags"]]
        command = [
            self.executable, *flags, "--remote-debugging-port=0",
            f"--user-data-dir={profile}", "about:blank",
        ]
        try:
            self.process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            raise BrowserEnvironmentError(
                f"environment failure: declared browser did not launch: {exc}"
            ) from exc
        active = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not active.exists():
            if self.process.poll() is not None:
                stderr = (self.process.stderr.read() if self.process.stderr
                          else "").strip()
                raise BrowserEnvironmentError(
                    "environment failure: declared browser exited before "
                    f"DevTools was ready: {stderr[-1000:]}"
                )
            time.sleep(0.05)
        if not active.exists():
            self._stop_process()
            raise BrowserEnvironmentError(
                "environment failure: declared browser did not expose "
                "DevTools within 12 seconds"
            )
        try:
            port = int(active.read_text(encoding="utf-8").splitlines()[0])
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                targets = json.load(response)
            target = next(item for item in targets if item.get("type") == "page")
            self.ws = _WebSocket(str(target["webSocketDebuggerUrl"]))
            self.call("Page.enable")
            self.call("Runtime.enable")
        except BrowserInfrastructureError:
            self._stop_process()
            raise
        except Exception as exc:
            self._stop_process()
            raise BrowserInfrastructureError(
                f"infrastructure failure: DevTools discovery failed: {exc}"
            ) from exc
        return self

    def call(self, method: str, params: Mapping[str, Any] | None = None,
             *, timeout: float = 8) -> dict[str, Any]:
        if self.ws is None:
            raise BrowserInfrastructureError(
                "infrastructure failure: DevTools is not connected"
            )
        self.command_id += 1
        command_id = self.command_id
        message: dict[str, Any] = {"id": command_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        self.ws.send_json(message)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response = self.ws.receive_json(
                    max(0.05, deadline - time.monotonic()))
            except socket.timeout as exc:
                raise BrowserInfrastructureError(
                    f"infrastructure failure: DevTools timed out in {method}"
                ) from exc
            if response.get("id") != command_id:
                continue
            if "error" in response:
                raise BrowserInfrastructureError(
                    f"infrastructure failure: DevTools {method}: "
                    f"{response['error']}"
                )
            return response
        raise BrowserInfrastructureError(
            f"infrastructure failure: no DevTools response for {method}"
        )

    def evaluate(self, expression: str) -> Any:
        response = self.call("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
        })["result"]
        if response.get("exceptionDetails"):
            raise BrowserInfrastructureError(
                "infrastructure failure: browser JavaScript raised: "
                f"{response['exceptionDetails']}"
            )
        result = response.get("result") or {}
        if result.get("subtype") == "error":
            raise BrowserInfrastructureError(
                f"infrastructure failure: browser JavaScript error: {result}"
            )
        return result.get("value")

    def wait_for(self, expression: str, expected: Any = True,
                 *, timeout: float = 8) -> Any:
        deadline = time.monotonic() + timeout
        last: Any = None
        while time.monotonic() < deadline:
            try:
                last = self.evaluate(expression)
            except BrowserInfrastructureError:
                last = None
            if last == expected:
                return last
            time.sleep(0.05)
        raise BrowserInfrastructureError(
            "infrastructure failure: browser condition timed out: "
            f"{expression!r}; last={last!r}"
        )

    def navigate(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})
        self.wait_for("document.readyState === 'complete'")

    def environment_receipt(
            self, *, fixture_server: Mapping[str, Any], snapshot: Mapping[str, Any],
            dashboard_artifact: bytes, dom: str, svg: str,
            selectors: object) -> dict[str, Any]:
        receipt = {
            "schema": "taskplane.browser-environment-receipt/v1",
            "executable": self.executable,
            "version": self.version,
            "flags": [*self.config["flags"], "--remote-debugging-port=0",
                      "--user-data-dir=<isolated-test-directory>"],
            "fixture_server": _digest(fixture_server),
            "file_fallback": _digest({
                "scheme": "file", "network_refresh_attempted": False,
            }),
            "snapshot": _digest(snapshot),
            "dashboard_artifact": _digest(dashboard_artifact),
            "dom": _digest(dom.encode("utf-8")),
            "svg": _digest(svg.encode("utf-8")),
            "selectors": _digest(selectors),
            "outcome": "passed",
        }
        receipt["fingerprint"] = _digest(receipt)
        return receipt

    def _stop_process(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def __exit__(self, *_exc: object) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None
        self._stop_process()


class _DocumentCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.doctypes = 0
        self.tags = {"html": 0, "head": 0, "body": 0}

    def handle_decl(self, decl: str) -> None:
        if decl.casefold().strip() == "doctype html":
            self.doctypes += 1

    def handle_starttag(self, tag: str, _attrs: object) -> None:
        if tag in self.tags:
            self.tags[tag] += 1


def _snapshot_model(sequence: int, marker: str, *, topology=None) -> dict[str, Any]:
    values: dict[str, Any] = {
        "generated_at": f"2026-08-31T00:00:{sequence:02d}Z",
        "browser_marker": marker,
    }
    if isinstance(topology, Mapping):
        values.update({key: topology[key] for key in (
            "design_graph", "plan_task_dag", "plan_waves")})
    material = {
        "identity": {
            "workflow_id": "wf-browser",
            "run_id": "run-browser",
            "target": "repository",
            "revision": "abc123",
            "sequence": sequence,
        },
        "sequence": sequence,
        "revision": "abc123",
        "state": "active",
        "values": values,
        "gate": {"status": "awaiting-human", "approval_enabled": True},
    }
    return {**material, "fingerprint": _digest(material)}


def _publish(root: Path, sequence: int, marker: str, *, topology=None) -> dict:
    model = _snapshot_model(sequence, marker, topology=topology)
    phase_html = ""
    if isinstance(topology, Mapping):
        phase_html = dashboard.render_phase_dependency_graphs(topology)

    def render(_canonical: str) -> str:
        return (
            '<main id="dashboard-snapshot" data-browser-marker="'
            + marker + '"><h1>Taskplane dashboard</h1>'
            + phase_html
            + '<button id="approve-action" data-dashboard-action="approve">'
              'approve</button></main>'
        )

    result = views.deliver_dashboard(
        str(root), model, html_renderer=render)
    assert result["status"] == "published"
    return {"model": model, "delivery": result}


def _artifact_relative(root: Path, result: Mapping[str, Any]) -> str:
    path = Path(result["delivery"]["artifacts"]["html"]["path"])
    return path.relative_to(root).as_posix()


def _dom_state(browser: _RealBrowser) -> dict[str, Any]:
    value = browser.evaluate("""(() => ({
      url: location.href,
      marker: document.querySelector('#dashboard-snapshot')?.dataset.browserMarker,
      freshness: document.body?.dataset.dashboardFreshness,
      reason: document.body?.dataset.dashboardFreshnessReason,
      disabled: document.querySelector('#approve-action')?.disabled,
      ariaDisabled: document.querySelector('#approve-action')?.getAttribute('aria-disabled')
    }))()""")
    assert isinstance(value, dict)
    return value


def _absolute_head(server: _LoopbackServer, head: Mapping[str, Any]) -> dict:
    value = dict(head)
    if value.get("html_href"):
        value["html_href"] = server.url(str(value["html_href"]))
    return value


def test_real_browser_replaces_dom_only_for_newer_snapshot_and_marks_stale(
        tmp_path):
    config = _json_fixture("environment.json")
    root = tmp_path / "delivery"
    old = _publish(root, 7, "snapshot-7")

    with _LoopbackServer(root) as server, _RealBrowser(tmp_path, config) as browser:
        old_url = server.url(_artifact_relative(root, old))
        browser.navigate(old_url)
        browser.wait_for(
            "document.body.dataset.dashboardFreshness === 'fresh'")
        initial = _dom_state(browser)
        assert initial["marker"] == "snapshot-7"
        assert initial["disabled"] is False

        exact = _absolute_head(server, old["delivery"]["current_head"])
        assert browser.evaluate(
            f"window.taskplaneDashboardApplyHead({_canonical(exact).decode()})"
        ) is True
        assert _dom_state(browser)["url"] == old_url
        assert _dom_state(browser)["marker"] == "snapshot-7"

        older = {**exact, "sequence": 6, "snapshot_fingerprint": "6" * 64}
        assert browser.evaluate(
            f"window.taskplaneDashboardApplyHead({_canonical(older).decode()})"
        ) is False
        stale = _dom_state(browser)
        assert stale["url"] == old_url
        assert stale["marker"] == "snapshot-7"
        assert stale["freshness"] == "stale"
        assert stale["disabled"] is True
        assert stale["ariaDisabled"] == "true"

        browser.navigate(old_url)
        browser.wait_for(
            "document.body.dataset.dashboardFreshness === 'fresh'")
        contradictory = {
            **exact, "snapshot_fingerprint": "f" * 64,
            "html_href": server.url("must-not-replace.html"),
        }
        assert browser.evaluate(
            "window.taskplaneDashboardApplyHead("
            f"{_canonical(contradictory).decode()})"
        ) is False
        contradictory_state = _dom_state(browser)
        assert contradictory_state["url"] == old_url
        assert contradictory_state["freshness"] == "stale"
        assert contradictory_state["disabled"] is True

        browser.navigate(old_url)
        browser.wait_for(
            "document.body.dataset.dashboardFreshness === 'fresh'")
        newer = _publish(root, 8, "snapshot-8")
        newer_head = _absolute_head(server, newer["delivery"]["current_head"])
        assert browser.evaluate(
            f"window.taskplaneDashboardApplyHead({_canonical(newer_head).decode()})"
        ) is False
        browser.wait_for(
            "document.querySelector('#dashboard-snapshot')?.dataset."
            "browserMarker === 'snapshot-8'")
        browser.wait_for(
            "document.body.dataset.dashboardFreshness === 'fresh'")
        replaced = _dom_state(browser)
        assert replaced["url"] == newer_head["html_href"]
        assert replaced["marker"] == "snapshot-8"
        assert replaced["disabled"] is False

        artifact = Path(newer["delivery"]["artifacts"]["html"]["path"])
        receipt = browser.environment_receipt(
            fixture_server=config["fixture_server"],
            snapshot=newer["model"],
            dashboard_artifact=artifact.read_bytes(),
            dom=browser.evaluate("document.documentElement.outerHTML"),
            svg=browser.evaluate(
                "Array.from(document.querySelectorAll('svg'), "
                "item => item.outerHTML).join('\\n')"),
            selectors=config["selectors"],
        )
        assert receipt["schema"] == \
            "taskplane.browser-environment-receipt/v1"
        assert Path(receipt["executable"]).is_absolute()
        assert receipt["version"]
        assert len(receipt["fingerprint"]) == 64


def test_real_browser_svg_graphs_and_single_document_are_truthful(tmp_path):
    config = _json_fixture("environment.json")
    topology = _json_fixture("topology.json")
    root = tmp_path / "delivery"
    published = _publish(root, 9, "topology-9", topology=topology)
    artifact = Path(published["delivery"]["artifacts"]["html"]["path"])
    document = artifact.read_text(encoding="utf-8")
    counter = _DocumentCounter()
    counter.feed(document)
    assert counter.doctypes == 1
    assert counter.tags == {"html": 1, "head": 1, "body": 1}

    with _LoopbackServer(root) as server, _RealBrowser(tmp_path, config) as browser:
        browser.navigate(server.url(_artifact_relative(root, published)))
        browser.wait_for(
            "document.body.dataset.dashboardFreshness === 'fresh'")
        shape = browser.evaluate("""(() => {
          const design = document.querySelector(
            "#tp-design-graph svg[data-phase-graph='tp-design-graph']");
          const plan = document.querySelector(
            "#tp-plan-task-dag svg[data-phase-graph='tp-plan-task-dag']");
          return {
            doctype: document.doctype?.name.toLowerCase(),
            html: document.querySelectorAll('html').length,
            head: document.querySelectorAll('head').length,
            body: document.querySelectorAll('body').length,
            canonical: document.querySelectorAll(
              "script[data-taskplane-canonical='true']").length,
            designSvg: design instanceof SVGSVGElement,
            designNodes: design?.querySelectorAll('rect').length,
            designEdges: design?.querySelectorAll('line').length,
            designDescription: design?.querySelector('desc')?.textContent,
            planSvg: plan instanceof SVGSVGElement,
            planNodes: plan?.querySelectorAll('rect').length,
            planEdges: plan?.querySelectorAll('line').length,
            planDescription: plan?.querySelector('desc')?.textContent,
            waves: document.querySelectorAll('#tp-plan-waves li').length,
            waveApproval: document.querySelector(
              '#tp-plan-waves')?.dataset.planApproval
          };
        })()""")
        assert shape == {
            "doctype": "html",
            "html": 1,
            "head": 1,
            "body": 1,
            "canonical": 1,
            "designSvg": True,
            "designNodes": 4,
            "designEdges": 3,
            "designDescription": (
                "4 source nodes and 3 source edges; 4 nodes and 3 edges "
                "visible in this bounded rendering."
            ),
            "planSvg": True,
            "planNodes": 4,
            "planEdges": 3,
            "planDescription": (
                "4 source nodes and 3 source edges; 4 nodes and 3 edges "
                "visible in this bounded rendering."
            ),
            "waves": 3,
            "waveApproval": "planned",
        }

        browser.navigate(artifact.as_uri())
        browser.wait_for(
            "document.body.dataset.dashboardFreshness === 'unverified'")
        file_state = _dom_state(browser)
        assert file_state["disabled"] is True
        assert "no trusted head bridge" in file_state["reason"]
        assert browser.evaluate(
            "document.querySelectorAll('[data-phase-graph]').length"
        ) == 2

        receipt = browser.environment_receipt(
            fixture_server=config["fixture_server"],
            snapshot=published["model"],
            dashboard_artifact=artifact.read_bytes(),
            dom=browser.evaluate("document.documentElement.outerHTML"),
            svg=browser.evaluate(
                "Array.from(document.querySelectorAll('svg'), "
                "item => item.outerHTML).join('\\n')"),
            selectors=config["selectors"],
        )
        expected_keys = {
            "schema", "executable", "version", "flags", "fixture_server",
            "file_fallback", "snapshot", "dashboard_artifact", "dom", "svg",
            "selectors", "outcome", "fingerprint",
        }
        assert set(receipt) == expected_keys
        assert all(len(receipt[key]) == 64 for key in (
            "fixture_server", "file_fallback", "snapshot",
            "dashboard_artifact", "dom", "svg", "selectors", "fingerprint",
        ))
