from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ci_local", ROOT / "scripts" / "ci_local.py")
PACKAGE_SPEC = importlib.util.spec_from_file_location(
    "package_openai", ROOT / "scripts" / "package_openai.py",
)


def _runner():
    assert SPEC is not None and SPEC.loader is not None
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def _package_module():
    assert PACKAGE_SPEC is not None and PACKAGE_SPEC.loader is not None
    module = importlib.util.module_from_spec(PACKAGE_SPEC)
    PACKAGE_SPEC.loader.exec_module(module)
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
        "release-surface", "release-history",
        "unittest-canary", "loop-cost", "import-cycle-current",
        "generated-lens-drift", "generated-cli-drift", "package-openai",
        "package-claude", "ruff", "mypy", "host-platform",
    }.union(runner.PYTEST_CHECK_IDS) == set(ids)


def test_complete_pytest_inventory_is_partitioned_exactly_once_and_balanced():
    runner = _runner()
    files = runner.pytest_inventory()
    partitions = runner.PYTEST_PARTITIONS
    assigned = [path for partition in partitions for path in partition]

    assert len(partitions) == len(runner.SHARDS) == 4
    assert all(partitions)
    assert all(list(partition) == sorted(partition) for partition in partitions)
    assert sorted(assigned) == sorted(files)
    assert len(assigned) == len(set(assigned))
    assert all(path.endswith(".py") and "/test_" in path for path in assigned)
    loads = [sum(runner.PYTEST_WEIGHTS[path] for path in row) for row in partitions]
    assert max(loads) - min(loads) <= max(runner.PYTEST_WEIGHTS.values())
    for check_id, partition in zip(runner.PYTEST_CHECK_IDS, partitions):
        check = runner.CHECKS[check_id]
        assert check.argv == (runner.PYTHON, "-m", "pytest", *partition, "-q")
        assert "taskplane/tests" not in check.argv


def test_pytest_partition_rejects_missing_stale_duplicate_and_unbalanced_rows():
    runner = _runner()
    with pytest.raises(runner.RunnerError, match="weight inventory"):
        runner.partition_pytest_files(("a.py", "b.py"), {"a.py": 1}, 2)
    with pytest.raises(runner.RunnerError, match="duplicate"):
        runner.partition_pytest_files(("a.py", "a.py"), {"a.py": 1}, 2)
    with pytest.raises(runner.RunnerError, match="nonempty"):
        runner.partition_pytest_files(("a.py",), {"a.py": 1}, 2)


def test_top_level_runner_refuses_recursion():
    runner = _runner()
    with pytest.raises(runner.RunnerError, match="recursive"):
        runner.main(["--json"], environ={runner.RECURSION_GUARD: "1"})


def test_runner_provides_portable_validated_package_root(tmp_path):
    runner = _runner()
    shard = tmp_path / "runner-owned"
    shard.mkdir()
    (shard / "home").mkdir()
    (shard / "tmp").mkdir()
    env = runner._safe_env(shard)
    assert env[runner.PACKAGE_TEMP_ROOT] == str(shard.resolve())

    package = _package_module()
    nested_output = shard / "openai-a"
    roots = package.approved_output_roots(env)
    assert shard.resolve() in roots
    package.require_approved_output(nested_output, roots)
    outside = Path(tmp_path.anchor) / "package-output-escape"
    assert not any(outside.resolve().is_relative_to(root) for root in roots)
    with pytest.raises(package.PackageError, match="approved temporary root"):
        package.require_approved_output(outside, roots)


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
    graph_job = workflow.split("  wave3-contracts:", 1)[1].split(
        "\n  pushed-sha-proof:", 1,
    )[0]
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in \
        graph_job
    assert "fetch-depth: 1" in graph_job
    assert "persist-credentials: false" in graph_job
    assert "synthetic_merge_substitutes" in workflow
    assert "pushed SHA delivery proof" in workflow


def test_import_cycle_check_names_current_checkout_inputs_explicitly():
    runner = _runner()
    check = runner.CHECKS["import-cycle-current"]
    assert check.argv[2:] == (
        "--root", ".", "--policy",
        "taskplane/tests/fixtures/import-cycles.json", "--check",
    )
