"""Real-browser checks of disposable rendered snapshots, not installed-host proof."""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

import pytest

from taskplane import flow
from taskplane.tests.test_native_acceptance_persistence import accepted_fixture


def _browser_executable():
    override = os.environ.get("TASKPLANE_BROWSER_EXECUTABLE")
    if override:
        executable = Path(override)
        assert executable.is_absolute() and executable.is_file() and os.access(executable, os.X_OK), (
            "Browser override must name an existing absolute executable"
        )
        return str(executable)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        executable = shutil.which(name)
        if executable:
            return executable
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "Google/Chrome/Application/chrome.exe")
            candidates.append(Path(os.environ[variable]) / "Chromium/Application/chrome.exe")
    for executable in candidates:
        if executable.is_file() and os.access(executable, os.X_OK):
            return str(executable)
    pytest.fail("Required browser check needs Chrome/Chromium; set TASKPLANE_BROWSER_EXECUTABLE")


class _CDP:
    """The synchronous CDP operations this test needs, over a stdlib WebSocket."""

    def __init__(self, url):
        endpoint = urlsplit(url)
        assert endpoint.scheme == "ws" and endpoint.hostname in ("127.0.0.1", "localhost")
        self.socket = socket.create_connection((endpoint.hostname, endpoint.port), timeout=10)
        self.reader = self.socket.makefile("rb")
        self.sequence = 0
        self.errors = []
        self.requests = []
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            resource = endpoint.path + ("?" + endpoint.query if endpoint.query else "")
            self.socket.sendall((
                f"GET {resource} HTTP/1.1\r\nHost: {endpoint.netloc}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode("ascii"))
            assert self.reader.readline().split()[1] == b"101", "Chrome rejected WebSocket upgrade"
            headers = {}
            while True:
                line = self.reader.readline()
                assert line, "Chrome closed during WebSocket upgrade"
                if line == b"\r\n":
                    break
                name, value = line.decode("ascii").split(":", 1)
                headers[name.lower()] = value.strip()
            expected = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()).decode("ascii")
            assert headers.get("sec-websocket-accept") == expected
        except BaseException:
            self.close()
            raise

    def close(self):
        self.reader.close()
        self.socket.close()

    def _read(self, size):
        value = self.reader.read(size)
        if len(value) != size:
            raise ConnectionError("Chrome closed its debugging connection")
        return value

    def _send(self, payload, opcode=1):
        size = len(payload)
        header = bytes([0x80 | opcode])
        if size < 126:
            header += bytes([0x80 | size])
        elif size <= 65535:
            header += bytes([0x80 | 126]) + struct.pack(">H", size)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", size)
        mask = os.urandom(4)
        self.socket.sendall(header + mask + bytes(byte ^ mask[index % 4]
                                                 for index, byte in enumerate(payload)))

    def _receive(self):
        fragments = bytearray()
        while True:
            first, second = self._read(2)
            opcode = first & 15
            size = second & 127
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if second & 0x80 else None
            payload = self._read(size)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 8:
                raise ConnectionError("Chrome closed its debugging WebSocket")
            if opcode == 9:
                self._send(payload, opcode=10)
                continue
            if opcode == 10:
                continue
            assert opcode in (0, 1), "Unexpected Chrome WebSocket frame"
            fragments.extend(payload)
            if first & 0x80:
                return json.loads(fragments)

    def command(self, method, **params):
        self.sequence += 1
        self._send(json.dumps({"id": self.sequence, "method": method, "params": params}).encode())
        deadline = time.monotonic() + 10
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Chrome did not answer {method}")
            self.socket.settimeout(remaining)
            message = self._receive()
            if message.get("method") == "Runtime.exceptionThrown":
                self.errors.append(message["params"]["exceptionDetails"])
            elif message.get("method") == "Network.requestWillBeSent":
                self.requests.append(message["params"]["request"]["url"])
            if message.get("id") == self.sequence:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    def evaluate(self, expression):
        result = self.command("Runtime.evaluate", expression=expression,
                              returnByValue=True, awaitPromise=True)
        assert "exceptionDetails" not in result, result.get("exceptionDetails")
        return result["result"].get("value")

    def wait_for(self, expression):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if self.evaluate(expression):
                    return
            except RuntimeError as error:
                if not any(text in str(error) for text in (
                    "Cannot find context", "Execution context was destroyed"
                )):
                    raise
            time.sleep(0.05)
        pytest.fail(f"Browser condition did not become true: {expression}")

    def focus(self, selector):
        selector = json.dumps(selector)
        assert self.evaluate(f"""(() => {{
            const element = document.querySelector({selector});
            element?.focus();
            return !!element && document.activeElement === element;
        }})()""")

    def enter(self):
        self.command("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter",
                     windowsVirtualKeyCode=13, text="\r", unmodifiedText="\r")
        self.command("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter",
                     windowsVirtualKeyCode=13)


@contextmanager
def _chromium(tmp_path):
    executable = _browser_executable()
    profile = tmp_path / "chrome-profile"
    profile.mkdir()
    client = None
    # Chromium puts its singleton Unix socket beneath TMPDIR. The retained CI
    # evidence path can exceed sockaddr_un's limit, even with a short profile
    # name. Give only the browser a private short temporary root; retain its
    # profile, diagnostics and screenshots in the original evidence directory.
    with (
        tempfile.TemporaryDirectory(prefix="tpc-", dir="/tmp" if os.name == "posix" else None) as browser_tmp,
        (tmp_path / "chrome.log").open("wb") as log,
    ):
        browser_env = dict(os.environ, TMPDIR=browser_tmp, TEMP=browser_tmp, TMP=browser_tmp)
        process = subprocess.Popen([
            executable, "--headless=new", "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank",
        ], stdout=log, stderr=subprocess.STDOUT, env=browser_env)
        try:
            deadline = time.monotonic() + 15
            port_file = profile / "DevToolsActivePort"
            opener = build_opener(ProxyHandler({}))
            while True:
                assert process.poll() is None, f"Chrome exited; inspect {tmp_path / 'chrome.log'}"
                if port_file.is_file():
                    lines = port_file.read_text().splitlines()
                    if lines and lines[0].isdigit():
                        with opener.open(f"http://127.0.0.1:{int(lines[0])}/json/list", timeout=5) as response:
                            targets = json.load(response)
                        pages = [item for item in targets if item.get("type") == "page"]
                        if pages:
                            client = _CDP(pages[0]["webSocketDebuggerUrl"])
                            break
                assert time.monotonic() < deadline, f"Chrome startup failed; inspect {tmp_path / 'chrome.log'}"
                time.sleep(0.05)
            yield client
        finally:
            try:
                if client is not None:
                    try:
                        client.command("Browser.close")
                    except (OSError, RuntimeError):
                        # Chrome may close the socket before acknowledging shutdown.
                        pass
                    finally:
                        client.close()
            finally:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def _visible(browser, selector):
    return browser.evaluate(f"""(() => {{
        const element = document.querySelector({json.dumps(selector)});
        if (!element) return false;
        const box = element.getBoundingClientRect(), style = getComputedStyle(element);
        return box.width > 0 && box.height > 0 && style.display !== 'none'
            && style.visibility !== 'hidden';
    }})()""")


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_graph_readable_scale_unicode_and_filtered_keyboard_focus(tmp_path, theme, record_property):
    from taskplane import depgraph
    ids = ['components/frontend-team-😀tools'] + ['module-' + str(i) for i in range(11)]
    graph = {'modules': {key: {'kind': 'module', 'files': 1} for key in ids},
             'edges': [{'from': ids[i], 'to': ids[i + 1]} for i in range(11)], 'components': []}
    document = depgraph._HTML.replace('__DATA__', json.dumps(graph)).replace('__TITLE__', 'Fixture')
    target = tmp_path / 'graph.html'
    target.write_text(document)
    with _chromium(tmp_path) as browser:
        record_property('browser', browser.command('Browser.getVersion')['product'])
        browser.command('Page.enable')
        browser.command('Runtime.enable')
        # A headless page can retain activeElement while its document has no
        # system focus. Give keyboard interaction a focused document so CSS
        # :focus/:focus-visible are exercised as they are in a foreground tab.
        browser.command('Emulation.setFocusEmulationEnabled', enabled=True)
        browser.command('Emulation.setEmulatedMedia', features=[{'name': 'prefers-color-scheme', 'value': theme}])
        browser.command('Page.navigate', url=target.as_uri())
        browser.wait_for("document.readyState === 'complete' && document.querySelectorAll('.node').length === 12")
        metrics = browser.evaluate("""(() => {const svg=document.querySelector('svg'), text=document.querySelector('.node text');
            return {scale:svg.getBoundingClientRect().width/svg.viewBox.baseVal.width,
                    font:parseFloat(getComputedStyle(text).fontSize), label:text.textContent,
                    scroll:document.querySelector('#wrap').scrollWidth, width:document.querySelector('#wrap').clientWidth};})()""")
        assert metrics['scale'] * metrics['font'] >= 12
        assert metrics['scroll'] > metrics['width']
        assert '😀' in metrics['label'] and '�' not in metrics['label']
        metrics['label'].encode('utf-8')
        browser.evaluate("const input=document.querySelector('#search'); input.value='no-match'; input.dispatchEvent(new Event('input'))")
        browser.focus('.node')
        assert browser.evaluate("document.hasFocus() && document.querySelector('.node').matches(':focus')")
        assert browser.evaluate("getComputedStyle(document.querySelector('.node')).opacity") == '1'
        browser.enter()
        assert ids[0] in browser.evaluate("document.querySelector('#inspector h2').textContent")


def _static_notice_visible(browser):
    return browser.evaluate("""[...document.querySelectorAll('body *')].some(element => {
        const box = element.getBoundingClientRect(), style = getComputedStyle(element);
        return element.textContent.trim() === 'Static snapshot — freshness is not monitored.'
            && box.width > 0 && box.height > 0 && style.visibility !== 'hidden';
    })""")


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 900},
                                     {"width": 390, "height": 844}], ids=["desktop", "mobile"])
def test_dashboard_browser_functional_contract(tmp_path, viewport, record_property):
    controller, state, grant = accepted_fixture(tmp_path)
    before = controller.report(diagnostics=False)
    model = flow.report(tmp_path, state["run"], governor=controller)
    assert model is not None and model["run"] == state["run"]
    target = flow.publish_dashboard(tmp_path, state["run"], governor=controller,
                                    supplied=model, output=tmp_path / "dashboard.html")
    source_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    with _chromium(tmp_path) as browser:
        version = browser.command("Browser.getVersion")
        browser.command("Page.enable")
        browser.command("Runtime.enable")
        browser.command("Network.enable")
        browser.command("Network.setBlockedURLs", urls=["http://*", "https://*"])
        browser.command("Emulation.setDeviceMetricsOverride", **viewport, deviceScaleFactor=1,
                        mobile=False)
        browser.command("Emulation.setEmulatedMedia", features=[
            {"name": "prefers-reduced-motion", "value": "reduce"}])
        navigation = browser.command("Page.navigate", url=target.as_uri())
        assert not navigation.get("errorText"), navigation
        browser.wait_for("document.readyState === 'complete' && !!document.querySelector('main.tp-flow')")
        assert _visible(browser, "main.tp-flow")
        assert _visible(browser, "#snapshot-identity")
        identity = browser.evaluate("document.querySelector('#snapshot-identity').textContent")
        assert state["run"] in identity
        assert str(tmp_path.resolve()) in identity
        assert model["snapshot"]["digest"] in identity
        assert _static_notice_visible(browser)
        nav = 'nav[aria-label="Dashboard sections"], [role="navigation"][aria-label="Dashboard sections"]'
        links = browser.evaluate(f"""[...document.querySelector({json.dumps(nav)}).querySelectorAll('a')]
            .map(element => element.getAttribute('href'))""")
        assert links == ["#workflow", "#decomposition", "#dependencies", "#lenses", "#telemetry", "#evidence"]
        for href in links:
            browser.focus(f':is({nav}) a[href="{href}"]')
            browser.enter()
            browser.wait_for(f"location.hash === {json.dumps(href)}")
            assert browser.evaluate(f"document.querySelectorAll({json.dumps(href)}).length") == 1
        initial = browser.evaluate("document.querySelector('details#native-support').open")
        browser.focus("details#native-support summary")
        browser.enter()
        assert browser.evaluate("document.querySelector('details#native-support').open") is not initial
        browser.enter()
        assert browser.evaluate("document.querySelector('details#native-support').open") is initial
        assert browser.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        assert browser.evaluate("document.getAnimations().filter(a => a.playState === 'running').length") == 0
        layout = browser.evaluate("""({viewport: innerWidth,
            document: document.documentElement.scrollWidth, body: document.body.scrollWidth})""")
        assert max(layout["document"], layout["body"]) <= layout["viewport"] + 1

        # Reload the generated file through its actual keyboard-operable UI.
        # A new snapshot digest is evidence of regeneration, never live polling.
        regenerated = flow.report(tmp_path, state["run"], governor=controller)
        assert regenerated is not None
        flow.publish_dashboard(tmp_path, state["run"], governor=controller,
                               supplied=regenerated, output=target)
        assert regenerated["snapshot"]["digest"] != model["snapshot"]["digest"]
        assert browser.evaluate("""(() => {
            const button = [...document.querySelectorAll('button')].find(element =>
                (element.getAttribute('aria-label') || element.textContent).trim() === 'Reload generated snapshot');
            button?.focus();
            return !!button && document.activeElement === button;
        })()""")
        browser.enter()
        browser.wait_for("document.readyState === 'complete' && "
                         "document.querySelector('#snapshot-identity')?.textContent.includes("
                         + json.dumps(regenerated["snapshot"]["digest"]) + ")")
        assert state["run"] in browser.evaluate("document.querySelector('#snapshot-identity').textContent")
        assert _static_notice_visible(browser)
        browser.evaluate("scrollTo(0, 0)")
        assert browser.evaluate("document.getAnimations().filter(a => a.playState === 'running').length") == 0
        metrics = browser.command("Page.getLayoutMetrics")
        pixels = base64.b64decode(browser.command("Page.captureScreenshot", format="png",
            captureBeyondViewport=True, fromSurface=True, clip={
                "x": 0, "y": 0, "width": viewport["width"],
                "height": max(viewport["height"], metrics["cssContentSize"]["height"]), "scale": 1})["data"])
        screenshot = tmp_path / ("dashboard-" + str(viewport["width"]) + ".png")
        screenshot.write_bytes(pixels)
        assert pixels == screenshot.read_bytes() and pixels[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", pixels[16:24])
        assert width == viewport["width"] and height >= viewport["height"]
        # Embed raw pixels in test evidence so CI temp cleanup cannot lose them.
        record_property("dashboard_browser_evidence", json.dumps({
            "kind": "fixture_browser_execution", "installed_runtime_verified": False,
            "run": state["run"], "browser_version": version, "viewport": viewport,
            "source_sha256": source_sha256, "final_source_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "snapshot_digest": regenerated["snapshot"]["digest"],
            "runtime_errors": browser.errors, "requests": browser.requests,
            "screenshot": {"path": str(screenshot), "sha256": hashlib.sha256(pixels).hexdigest(),
                           "bytes": len(pixels), "base64": base64.b64encode(pixels).decode("ascii")}}))
        assert not browser.errors
        assert not any(url.startswith(("http://", "https://")) for url in browser.requests)
    after = controller.report(diagnostics=False)
    assert after["workers"][grant]["state"] == "accepted"
    assert after["task_results"] == before["task_results"]
