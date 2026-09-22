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
            # Fixture HTML can change twice within HTTP's timestamp resolution.
            # Fetch current bytes so conditional caching cannot hide a decision.
            self.call("Network.enable")
            self.call("Network.setCacheDisabled", {"cacheDisabled": True})
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



def test_shared_delivery_dashboard_and_graph_work_in_a_real_browser(tmp_path, monkeypatch):
    from taskplane import depgraph, flow, workflow as w
    from taskplane.tests.test_workflow_host import controller, native
    from taskplane.tests.test_workflow_delivery import output
    c, host, initial = controller(tmp_path)
    workspace = c.workspace
    def command(*args):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            return flow.main([*args, "--workspace", str(workspace)], governor=c)
    for name, content in {"src/api/main.py": "from src.data import store\n",
                          "src/data/store.py": "VALUE = 1\n"}.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setenv("CODEX_THREAD_ID", "root")
    assert command("start", "--goal", "Ship a small product") == 0
    depgraph.scan(str(workspace), decompose=True)
    (workspace / "tasks.json").write_text(json.dumps({"tasks": [
        {"id": "T1", "title": "Build product", "dependencies": [],
         "paths": ["src/api/main.py"], "status": "done", "verification": "Product tests passed"}]}))
    (workspace / "review.md").write_text('<script>window.badEvidence=true</script> Reviewed dependency impact')
    (workspace / "reviews.json").write_text(json.dumps([{"lens": "quality", "agent": "/root/quality",
        "phase": "engineering", "evidence": "review.md"}]))
    for i, phase in enumerate(w.PHASES):
        s = c.report()
        target = output(c, s)
        assert command("submit", "--output", target, "--tasks", "tasks.json", "--expected-revision", str(s["revision"])) == 0
        s = c.report()
        key = native(host, s, key="browser-human-"+phase)
        assert command("decide", "--native-event", key, "--expected-revision", str(s["revision"])) == 0
        s = c.report()
        if phase != "retro":
            assert command("advance", "--phase", w.PHASES[i+1], "--expected-revision", str(s["revision"])) == 0
    assert command("finish", "--expected-revision", str(s["revision"]), "--note", "Verified through retro") == 0
    tasks = json.loads((workspace/"tasks.json").read_text())
    tasks["tasks"][0]["title"] = "Build product"
    (workspace/"tasks.json").write_text(json.dumps(tasks))
    depgraph.scan(str(workspace), decompose=True)
    assert command("attach", "--run", initial["run"], "--tasks", "tasks.json", "--reviews", "reviews.json", "--evidence", "review.md") == 0
    config = _json_fixture("environment.json")
    with _LoopbackServer(workspace) as server, _RealBrowser(tmp_path, config) as browser:
        browser.navigate(server.url(".taskplane/dashboard.html"))
        browser.wait_for("document.querySelectorAll('.tp-flow .stage').length", 7)
        assert browser.evaluate("document.querySelectorAll('.stage.recorded').length") == 7
        assert browser.evaluate("document.querySelector('#workflow').textContent.includes('Human decision: Approved')")
        assert browser.evaluate("document.querySelector('#native-support').textContent.includes('Process revocation')")
        assert browser.evaluate("document.querySelectorAll('#native-support tbody tr').length") == 4
        assert browser.evaluate("document.querySelector('#decomposition').textContent.includes('T1')")
        assert not browser.evaluate("document.querySelector('#decomposition').textContent.includes('Build product')")
        assert browser.evaluate("document.querySelector('#lenses').textContent.includes('quality')")
        assert browser.evaluate("document.body.textContent.includes('Verified through retro')")
        assert browser.evaluate("window.badEvidence === undefined")
        for width in (390, 768, 1280):
            browser.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 1000,
                                                                  "deviceScaleFactor": 1, "mobile": False})
            assert browser.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), browser.evaluate("JSON.stringify([...document.querySelectorAll('body *')].filter(e=>!e.closest('.table-wrap')&&e.getBoundingClientRect().right>innerWidth+1).map(e=>[e.tagName,e.className,e.getBoundingClientRect().right]).slice(0,20))")
        assert browser.evaluate("document.querySelector('#dependencies iframe').getBoundingClientRect().height > 300")
        assert browser.evaluate("document.querySelector('#dependencies iframe').srcdoc.includes('Module dependency graph')")
        graph = workspace / ".taskplane/graph.html"
        depgraph.to_html(str(workspace), out=str(graph))
        browser.navigate(server.url(".taskplane/graph.html"))
        browser.wait_for("document.querySelectorAll('svg .node').length >= 2")
        assert browser.evaluate("document.querySelectorAll('svg .edge').length") >= 1
        assert browser.evaluate("document.querySelector('.node').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})); document.querySelector('#inspector').textContent.includes('Depends on')")
        assert browser.evaluate("getComputedStyle(document.querySelector('.node rect')).strokeWidth") != "0px"
        # Render the actual shipped local profile, without a trusted test owner.
        from taskplane.tests.test_native_workflow_cli import create, cli, output as local_output
        from taskplane.tests.test_workflow_local import decision
        local_workspace = workspace/'native-example'
        create(local_workspace)
        local_state = cli(local_workspace, 'codex', 'start', '--standalone', '--phase', 'product',
                          '--scope', '.taskplane/scope.json', '--request-reference', 'browser/test-request')['workflow']
        local_target = local_output(local_workspace, local_state)
        local_state = cli(local_workspace, 'codex', 'submit', '--output', local_target,
                          '--tasks', 'tasks.json', '--expected-revision', str(local_state['revision']))['workflow']
        browser.navigate(server.url('native-example/.taskplane/dashboard.html'))
        browser.wait_for("document.body.textContent.includes('Workflow gates active; host-wide protection unavailable')")
        assert browser.evaluate("document.querySelector('#workflow').textContent.includes('Awaiting human approval')")
        assert browser.evaluate("[...document.querySelectorAll('#native-support tbody tr')].every(r => r.textContent.includes('Unverified'))")
        cli(local_workspace, 'codex', 'present', '--evidence', '.taskplane/dashboard.html',
            '--presentation', 'verified', '--note',
            'Chrome verified the current native Product checkpoint and unavailable host protections.')
        local_state = cli(local_workspace, 'codex', 'decide', '--decision-json', json.dumps(decision(local_state)),
                          '--expected-revision', str(local_state['revision']))['workflow']
        cli(local_workspace, 'codex', 'finish', '--expected-revision', str(local_state['revision']))
        browser.navigate(server.url('native-example/.taskplane/dashboard.html'))
        browser.wait_for("document.querySelector('#workflow').textContent.includes('Human decision: Approved')")
        assert browser.evaluate("document.body.textContent.includes('host-wide protection unavailable')")


def test_native_snapshot_tokens_policy_graph_and_static_reload(tmp_path, monkeypatch):
    """Independent synthetic fixture; never serves the user's denied file dashboard."""
    from copy import deepcopy
    from taskplane import depgraph, flow, flow_dashboard
    from taskplane.tests.test_workflow_local import setup, submit
    from taskplane.tests.test_workflow_autonomy import set_policy, auto
    workspace=tmp_path/'fixture';workspace.mkdir()
    c,state=setup(workspace,standalone=True)
    monkeypatch.setenv('CODEX_THREAD_ID','root')
    flow.append(workspace,{'kind':'start','run':state['run'],'session':'root','phase':'product',
                           'goal':'Synthetic dashboard journey','at':state['started_at']})
    state=set_policy(c,state);state=submit(c,state);state=auto(c,state)
    model=flow.report(workspace,state['run'],governor=c)
    visit=state['visits'][0]['id']
    model['tokens']={'input_tokens':120,'cached_input_tokens':100,'uncached_input_tokens':20,'output_tokens':30,'total_tokens':150}
    model['token_coverage']={'measured_sessions':1,'unmeasured_sessions':1,'partial_sessions':1,'discovery_errors':2,'basis':'Synthetic run boundary'}
    model['sessions']=[{'session':'root','agent':'orchestrator','role':'orchestrator','status':'measured',
        'measured_at':'2026-09-17T00:01:00+00:00','usage':model['tokens']}]
    model['phase_usage']={'visits':{visit:{'phase':'product','tokens':{'total_tokens':100},'buckets':{'work':{'total_tokens':80},'review':{'total_tokens':20}},'status':'partial'}},
        'phases':{'product':{'tokens':{'total_tokens':100},'status':'partial'}},'unallocated':{'total_tokens':50},
        'gaps':['Synthetic missing child boundary'],'measurement_at':'2026-09-17T00:01:00+00:00'}
    target=flow.publish_dashboard(workspace,state['run'],governor=c,supplied=model,select=True)
    config=_json_fixture('environment.json')
    with _LoopbackServer(workspace) as server, _RealBrowser(tmp_path,config) as browser:
        browser.navigate(server.url('.taskplane/dashboard.html'))
        assert browser.evaluate("document.querySelector('.metrics').textContent.includes('Current visit tokens100')")
        assert browser.evaluate("document.querySelector('.metrics').textContent.includes('Run tokens150')")
        assert browser.evaluate("document.querySelector('.metrics').getBoundingClientRect().top < 700")
        assert browser.evaluate("document.querySelector('#workflow').textContent.includes('Automatically approved')")
        assert browser.evaluate("document.querySelector('#telemetry').textContent.includes('Discovery errors: 2')")
        assert browser.evaluate("document.querySelector('#telemetry').textContent.includes('Unallocated run usage: 50')")
        assert browser.evaluate("document.querySelector('#telemetry').textContent.includes('Synthetic missing child boundary')")
        assert browser.evaluate("document.querySelector('#snapshot-identity').textContent.includes('"+state['run']+"')")
        assert browser.evaluate("document.querySelectorAll('#dependencies iframe').length") == 2
        assert browser.evaluate("document.querySelector('#dependencies').textContent.includes('Planned task scope')")
        # Actual rendered graph uses the captured model even if live graph files later differ.
        doc=flow_dashboard.graph_document(model['graph'],['app.py'],planned=True)
        graph_path=workspace/'captured-fixture.html';graph_path.write_text(doc)
        browser.navigate(server.url('captured-fixture.html'))
        browser.wait_for("document.querySelectorAll('svg .node').length > 0")
        assert browser.evaluate("document.querySelector('.node').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})); document.querySelector('#inspector').textContent.includes('planned scope')")
        browser.navigate(server.url('.taskplane/dashboard.html'))
        old_identity=browser.evaluate("document.querySelector('#snapshot-identity').textContent")
        updated=deepcopy(model);updated['tokens']=None;updated['phase_usage']={'gaps':['No phase boundaries'], 'visits':{}}
        from datetime import datetime,timezone
        updated['snapshot']['captured_at']=updated['snapshot']['generated_at']=datetime.now(timezone.utc).isoformat()
        flow.publish_dashboard(workspace,state['run'],governor=c,supplied=updated,select=True)
        assert browser.evaluate("document.querySelector('#snapshot-identity').textContent")==old_identity
        assert browser.evaluate("document.body.textContent.includes('freshness is not monitored')")
        browser.evaluate("document.querySelector('#snapshot-identity button').click()")
        browser.wait_for("document.querySelector('#snapshot-identity').textContent.includes("+json.dumps(updated['snapshot']['generated_at'])+")")
        assert browser.evaluate("document.querySelector('.metrics').textContent.includes('Run tokensUnknown')")
        assert browser.evaluate("document.querySelector('#telemetry').textContent.includes('No phase boundaries')")
        for width in (390,768,1280):
            browser.call('Emulation.setDeviceMetricsOverride',{'width':width,'height':1000,'deviceScaleFactor':1,'mobile':False})
            assert browser.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), browser.evaluate("JSON.stringify([...document.querySelectorAll('body *')].filter(e=>!e.closest('.table-wrap')&&e.getBoundingClientRect().right>innerWidth+1).map(e=>[e.tagName,e.className,e.getBoundingClientRect().right]).slice(0,20))")
        # EV-F01: actual DOM labels distinguish saved data from read attempts.
        old_time='2026-09-17T00:01:00+00:00';attempt='2026-09-17T00:03:00+00:00'
        for coverage in ('fresh','recorded','mixed','unavailable'):
            clock_model=deepcopy(model)
            root_session=deepcopy(model['sessions'][0])
            root_session.update(status='measured' if coverage=='fresh' else 'recorded; native counter unavailable',
                                measured_at=attempt if coverage=='fresh' else old_time)
            clock_model['sessions']=[root_session]
            if coverage=='mixed':
                clock_model['sessions'].append({'session':'child','agent':'child','role':'lens','status':'measured','measured_at':attempt,
                    'usage':{'input_tokens':0,'cached_input_tokens':0,'uncached_input_tokens':0,'output_tokens':5,'total_tokens':5}})
            if coverage=='unavailable':root_session.update(status='unavailable',usage=None,measured_at=None)
            values=[session['usage'] for session in clock_model['sessions'] if session['usage'] is not None]
            clock_model['tokens']={key:sum(value.get(key,0) for value in values) for key in values[0]} if values else None
            clock_model['token_coverage']['discovery_errors']=0
            clock_model['usage_measurement']=flow.usage_measurement(clock_model,attempt)
            clock_model['phase_usage']={'visits':{},'gaps':['Synthetic timestamp fixture']}
            clock_data=clock_model['usage_measurement']
            clock_model['snapshot'].update(measurement_at=clock_data['measured_at'],measurement_status=coverage,
                measurement_oldest_at=clock_data['oldest_at'],measurement_newest_at=clock_data['newest_at'],measurement_attempted_at=attempt)
            clock_model['snapshot']['captured_at']=clock_model['snapshot']['generated_at']=datetime.now(timezone.utc).isoformat()
            flow.publish_dashboard(workspace,state['run'],governor=c,supplied=clock_model,select=True)
            browser.navigate(server.url('.taskplane/dashboard.html')+'?coverage='+coverage)
            expected=attempt if coverage=='fresh' else old_time if coverage=='recorded' else 'Unknown'
            assert browser.evaluate("document.querySelector('#usage-measurement').textContent.includes("+json.dumps('Counter measurement: '+expected+' · Coverage: '+coverage)+")")
            assert browser.evaluate("document.querySelector('#usage-measurement').textContent.includes("+json.dumps('Last read attempt: '+attempt)+")")
            assert browser.evaluate("document.querySelector('#snapshot-identity').textContent.includes("+json.dumps('Counter measurement: '+expected)+")")
            if coverage=='mixed':
                assert browser.evaluate("document.querySelector('#usage-measurement').textContent.includes("+json.dumps(old_time+' → '+attempt)+")")
        # This fixture verifies DOM behavior; it does not claim the host opened a user's task.


def test_concurrent_dashboard_refresh_keeps_newer_visible_state(tmp_path):
    from taskplane.tests.test_workflow_local import setup
    from taskplane.tests.test_native_workflow_cli import exercise_dashboard_publication_order
    workspace=tmp_path/'concurrent';workspace.mkdir()
    c,state=setup(workspace)
    target,model=exercise_dashboard_publication_order(c,state,select=True)
    with _LoopbackServer(workspace) as server, _RealBrowser(tmp_path,_json_fixture('environment.json')) as browser:
        browser.navigate(server.url('.taskplane/dashboard.html'))
        assert browser.evaluate("document.querySelector('.metrics').textContent.includes('Run tokens200')")
        assert browser.evaluate("document.querySelector('#snapshot-identity').textContent.includes("+json.dumps(model['snapshot']['generated_at'])+")")
        selection=json.loads(target.with_suffix('.selection.json').read_text())
        assert selection['digest']==model['snapshot']['digest']



def test_standalone_review_harness_hands_off_native_dashboard(tmp_path):
    from taskplane.tests.test_native_workflow_cli import exercise_harness_entry
    workspace=(tmp_path/'standalone-review').resolve()
    state=exercise_harness_entry(workspace,'claude','engineering')
    with _LoopbackServer(workspace) as server, _RealBrowser(tmp_path,_json_fixture('environment.json')) as browser:
        browser.navigate(server.url('.taskplane/dashboard.html'))
        assert browser.evaluate("document.querySelectorAll('.tp-flow .stage').length")==1
        assert browser.evaluate("document.querySelector('#workflow').textContent.includes('Engineering')")
        assert browser.evaluate("document.querySelector('#workflow').textContent.includes('Awaiting human approval')")
        assert browser.evaluate("document.querySelector('#snapshot-identity').textContent.includes("+json.dumps(state['run'])+")")
        assert browser.evaluate("document.querySelector('#decomposition') !== null")
        assert browser.evaluate("document.querySelector('#dependencies iframe') !== null")
