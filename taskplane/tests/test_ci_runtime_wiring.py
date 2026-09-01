from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from taskplane import run_artifacts


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "ci_local_receipt_contract", ROOT / "scripts" / "ci_local.py",
)


def _runner():
    assert SPEC is not None and SPEC.loader is not None
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def _runtime(runner):
    return runner.build_authoritative_ci_runtime(
        source_sha=runner._git("rev-parse", "HEAD"),
        event="pull_request", ref="482", run_id="9001",
    )


@pytest.mark.parametrize("outcome", ["cancellation", "interruption", "handoff"])
def test_terminal_outcomes_preserve_durable_evidence_and_remove_only_owned_state(
    tmp_path, outcome,
):
    runner = _runner()
    runtime = _runtime(runner)
    runtime_path = tmp_path / "runtime.json"
    runner._atomic_write_json(runtime_path, runtime)
    execution_root = tmp_path / outcome
    execution_root.mkdir()
    receipt_path = execution_root / "receipt.json"

    assert runner.run_authoritative_ci_cell(
        runtime_path, "pytest-1", receipt_path,
        environ={**runner.os.environ, "RUNNER_TEMP": str(execution_root)},
        forced_outcome=outcome,
    ) == 1
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    cell = next(row for row in runtime["plan"]["cells"]
                if row["id"] == "pytest-1")
    runner.validate_authoritative_ci_cell_receipt(receipt, runtime, cell)

    assert receipt["schema"] == runner.CI_DIRECT_CELL_SCHEMA
    assert receipt["classification"] is None
    assert receipt["failure_routing"]["next"] == "hold"
    assert receipt["failure_routing"]["records"][0]["class"] == "unknown"
    assert receipt["cleanup"]["outcome"] == outcome
    assert receipt["cleanup"]["leak_count"] == 0
    assert receipt["cleanup"]["durable_artifacts_preserved"] is True
    assert all(not Path(path).exists() for path in receipt["cleanup"]["resources"])
    verified = run_artifacts.verify_durable_reference(receipt["run_artifacts"])
    assert verified["readable"] is True
    assert verified["artifact_count"] >= 3


def test_receipt_rejects_tampered_failure_and_cleanup_evidence(tmp_path):
    runner = _runner()
    runtime = _runtime(runner)
    runtime_path = tmp_path / "runtime.json"
    runner._atomic_write_json(runtime_path, runtime)
    receipt_path = tmp_path / "receipt.json"
    assert runner.run_authoritative_ci_cell(
        runtime_path, "pytest-1", receipt_path,
        environ={**runner.os.environ, "RUNNER_TEMP": str(tmp_path)},
        forced_outcome="handoff",
    ) == 1
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    cell = next(row for row in runtime["plan"]["cells"]
                if row["id"] == "pytest-1")

    tampered = deepcopy(receipt)
    tampered["failure_routing"]["records"][0]["class"] = "product"
    tampered["receipt"] = runner._sha256_json({
        key: value for key, value in tampered.items() if key != "receipt"
    })
    with pytest.raises((runner.RunnerError, ValueError)):
        runner.validate_authoritative_ci_cell_receipt(tampered, runtime, cell)

    tampered = deepcopy(receipt)
    tampered["cleanup"]["leak_count"] = 1
    tampered["cleanup"]["fingerprint"] = runner._sha256_json({
        key: value for key, value in tampered["cleanup"].items()
        if key != "fingerprint"
    })
    tampered["receipt"] = runner._sha256_json({
        key: value for key, value in tampered.items() if key != "receipt"
    })
    with pytest.raises(runner.RunnerError, match="cleanup"):
        runner.validate_authoritative_ci_cell_receipt(tampered, runtime, cell)
