from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "ci-runtime" / "contract.json"
SPEC = importlib.util.spec_from_file_location(
    "ci_local_runtime_contract", ROOT / "scripts" / "ci_local.py",
)


def _runner():
    assert SPEC is not None and SPEC.loader is not None
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def _browser():
    return {
        "executable": "/opt/chromium/chrome",
        "version": "Chromium 131.0.0",
        "flags": ["--headless=new", "--disable-gpu"],
        "fixture_server": "1" * 64,
        "snapshot": "2" * 64,
        "dashboard_artifact": "3" * 64,
        "selectors": "4" * 64,
    }


def test_browser_cell_is_required_isolated_candidate_bound_and_cleanup_safe(
    tmp_path,
):
    runner = _runner()
    contract = json.loads(FIXTURE.read_text(encoding="utf-8"))
    runtime = runner.build_authoritative_ci_runtime(
        source_sha=runner._git("rev-parse", "HEAD"),
        event="pull_request",
        ref="482",
        run_id="9001",
        browser=_browser(),
    )
    browsers = [
        cell for cell in runtime["plan"]["cells"] if cell["kind"] == "browser"
    ]
    assert len(browsers) == 1
    browser = browsers[0]
    assert browser["id"] == "dashboard-browser"
    assert browser["matrix"] == "browser"
    assert browser["execution"] == "ci-only"
    assert browser["selectors"] == contract["browser_selectors"]
    assert browser["candidate_fingerprint"] == runtime["candidate"]["fingerprint"]
    assert browser["source_sha"] == runtime["candidate"]["source_sha"]
    assert browser["browser_fingerprint"] == runtime["candidate"]["browser_fingerprint"]
    assert browser["cleanup"]["registered_before_run"] is True
    assert browser["cleanup"]["outcomes"] == [
        "success", "failure", "cancellation", "interruption", "timeout", "handoff",
    ]
    assert all(
        not set(browser["selectors"]).intersection(cell["selectors"])
        for cell in runtime["plan"]["cells"] if cell["id"] != browser["id"]
    )

    target, registration = runner._owned_cell_root(
        runtime, browser["id"], tmp_path,
    )
    target.mkdir()
    (target / "owned.txt").write_text("owned\n", encoding="utf-8")
    cleanup = runner._cleanup_ci_cell_root(target, registration)
    assert cleanup["status"] == "clean"
    assert cleanup["leak_count"] == 0
    assert not target.exists()

    unsafe = tmp_path / "taskplane-ci-unowned"
    unsafe.mkdir()
    with pytest.raises(runner.RunnerError, match="ambiguous or unowned"):
        runner._cleanup_ci_cell_root(unsafe, registration)
    assert unsafe.exists()
