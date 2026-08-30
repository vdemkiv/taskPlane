from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ci_local", ROOT / "scripts" / "ci_local.py")


def _runner():
    assert SPEC is not None and SPEC.loader is not None
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def test_closed_inventory_is_complete_unique_and_sharded_once():
    runner = _runner()
    ids = [check.id for check in runner.INVENTORY]
    assignments = [check_id for shard in runner.SHARDS for check_id in shard]

    assert runner.SCHEMA == "taskplane.local-ci-equivalent/v1"
    assert 3 <= len(runner.SHARDS) <= 5
    assert len(ids) == len(set(ids))
    assert sorted(assignments) == sorted(ids)
    assert len(assignments) == len(set(assignments))
    assert {
        "compile-import", "version-verify", "zero-token-corpus",
        "pytest-complete", "release-surface", "release-history",
        "unittest-canary", "loop-cost", "import-cycle-history",
        "generated-lens-drift", "generated-cli-drift", "package-openai",
        "package-claude", "ruff", "mypy", "host-platform",
    } == set(ids)


def test_top_level_pytest_is_reachable_but_runner_refuses_recursion():
    runner = _runner()
    pytest_check = next(check for check in runner.INVENTORY if check.id == "pytest-complete")
    assert pytest_check.argv == (runner.PYTHON, "-m", "pytest", "taskplane/tests", "-q")
    with pytest.raises(runner.RunnerError, match="recursive"):
        runner.main(["--json"], environ={runner.RECURSION_GUARD: "1"})


def test_receipt_collection_fails_closed_on_missing_duplicate_and_malformed():
    runner = _runner()
    expected = ("a", "b")
    base = {
        "check_id": "a", "argv": ["python3", "-V"], "status": "passed",
        "duration_ms": 1, "output_digest": "0" * 64,
    }
    with pytest.raises(runner.RunnerError, match="missing"):
        runner.validate_results(expected, [base])
    with pytest.raises(runner.RunnerError, match="duplicate"):
        runner.validate_results(("a",), [base, base])
    malformed = dict(base, output_digest="bad")
    with pytest.raises(runner.RunnerError, match="malformed"):
        runner.validate_results(("a",), [malformed])


def test_remote_required_never_reports_full_green():
    runner = _runner()
    results = [
        {"check_id": "host-platform", "argv": ["host-platform"],
         "status": "remote-required", "duration_ms": 0,
         "output_digest": "0" * 64},
    ]
    report = runner.build_report("a" * 40, results, checkout_mutated=False)
    assert report["status"] == "local-green/remote-required"
    assert report["full_green"] is False


def test_json_encoding_is_canonical_and_receipts_are_sha_bound():
    runner = _runner()
    report = runner.build_report(
        "a" * 40,
        [{"check_id": "a", "argv": ["python3", "-V"], "status": "passed",
          "duration_ms": 0, "output_digest": "0" * 64}],
        checkout_mutated=False,
    )
    assert report["receipts"][0]["source_sha"] == "a" * 40
    assert report["receipts"][0]["inventory_version"] == runner.INVENTORY_VERSION
    encoded = runner.canonical_json(report)
    assert encoded == json.dumps(report, sort_keys=True, separators=(",", ":"))


def test_cleanup_rejects_unowned_or_symlink_roots(tmp_path):
    runner = _runner()
    owned = tmp_path / (runner.TEMP_PREFIX + "owned")
    owned.mkdir()
    runner.cleanup_root(owned, tmp_path)
    assert not owned.exists()
    outside = tmp_path.parent / (runner.TEMP_PREFIX + "outside")
    outside.mkdir(exist_ok=True)
    try:
        with pytest.raises(runner.RunnerError, match="containment"):
            runner.cleanup_root(outside, tmp_path)
    finally:
        outside.rmdir()


def test_pr_workflow_binds_all_blocking_jobs_to_exact_head_sha():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "exact PR head SHA blocking proof" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" in workflow
    assert "github.event.pull_request.head.sha || github.sha" in workflow
    assert "synthetic_merge_substitutes" in workflow
    assert "pushed SHA delivery proof" in workflow
