"""Browser startup must drain diagnostics and retain failures without leaking."""

import sys

import pytest

from taskplane.tests import test_dashboard_browser as harness


def _python_browser(tmp_path, monkeypatch, script):
    monkeypatch.setattr(harness, "_declared_browser", lambda _config:
                        (sys.executable, "fixture process", "test"))
    return harness._RealBrowser(tmp_path, {"flags": ["-c", script]})


def test_noisy_startup_reports_exit_without_blocking_on_stderr(tmp_path, monkeypatch):
    browser = _python_browser(tmp_path, monkeypatch,
        "import sys; sys.stderr.write('x' * 1048576 + '\\nstartup refused\\n'); "
        "sys.stderr.flush(); raise SystemExit(23)")
    with pytest.raises(harness.BrowserEnvironmentError, match="startup refused") as error:
        browser.__enter__()
    assert "exited before DevTools" in str(error.value)
    assert browser.process.poll() == 23


def test_startup_timeout_preserves_diagnostics_and_reaps_process(tmp_path, monkeypatch):
    browser = _python_browser(tmp_path, monkeypatch,
        "import sys, time; sys.stderr.write('startup stalled\\n'); "
        "sys.stderr.flush(); time.sleep(60)")
    with pytest.raises(harness.BrowserEnvironmentError, match="startup stalled") as error:
        browser.__enter__()
    assert "DevTools within 12 seconds" in str(error.value)
    assert browser.process.poll() is not None
