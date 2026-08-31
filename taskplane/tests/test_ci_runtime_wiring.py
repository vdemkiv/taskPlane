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
    tmp_path, monkeypatch,
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
    assert target.name.startswith("taskplane-ci-")
    assert len(target.name) <= 18
    other_target, _ = runner._owned_cell_root(runtime, "pytest-1", tmp_path)
    assert other_target.name != target.name
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

    runtime_path = tmp_path / "runtime.json"
    runner._atomic_write_json(runtime_path, runtime)
    for outcome in ("cancellation", "interruption", "handoff"):
        outcome_root = tmp_path / outcome
        outcome_root.mkdir()
        receipt_path = outcome_root / "receipt.json"
        assert runner.run_authoritative_ci_cell(
            runtime_path, "pytest-1", receipt_path,
            environ={**runner.os.environ, "RUNNER_TEMP": str(outcome_root)},
            forced_outcome=outcome,
        ) == 1
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        cell = next(row for row in runtime["plan"]["cells"]
                    if row["id"] == "pytest-1")
        runner.validate_authoritative_ci_cell_receipt(receipt, runtime, cell)
        assert receipt["outcome"] == outcome
        assert receipt["classification"] is None
        assert receipt["cleanup"]["outcome"] == outcome
        assert receipt["cleanup"]["leak_count"] == 0
        assert all(not Path(path).exists()
                   for path in receipt["cleanup"]["resources"])

    drifted_browser = {**runtime["candidate"]["browser"], "version": "drifted"}
    monkeypatch.setattr(runner, "_browser_identity", lambda _env: drifted_browser)
    browser_root = tmp_path / "browser-drift"
    browser_root.mkdir()
    browser_receipt = browser_root / "receipt.json"
    assert runner.run_authoritative_ci_cell(
        runtime_path, "dashboard-browser", browser_receipt,
        environ={**runner.os.environ, "RUNNER_TEMP": str(browser_root)},
    ) == 1
    mismatch = json.loads(browser_receipt.read_text(encoding="utf-8"))
    assert mismatch["classification"] == "environment"
    assert mismatch["cleanup"]["leak_count"] == 0
    with pytest.raises(runner.RunnerError, match="executing-runner identity"):
        runner.validate_authoritative_ci_cell_receipt(mismatch, runtime, browser)
