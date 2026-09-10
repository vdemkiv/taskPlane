"""Remediation evidence keeps task identity and immutable receipt boundaries."""
from pathlib import Path

import pytest

from taskplane import remediation_trace as trace


@pytest.mark.parametrize("role", ["build", "evaluate"])
def test_identity_is_bound_to_role_task_and_session(role):
    identity = trace.agent_identity(role=role, agent_id="worker", task_name="attempt-1",
        task_id="T1", session_id="session-1")
    assert trace._validate_identity(identity, role=role, task_id="T1") == identity
    for replacement in ({"task_id": "T2"}, {"session_id": "session-2"},
                        {"role": "evaluate" if role == "build" else "build"}):
        with pytest.raises(trace.RemediationTraceError):
            trace._validate_identity({**identity, **replacement}, role=role, task_id="T1")


def test_receipt_replay_is_idempotent_and_collision_preserves_original(tmp_path):
    receipt = trace._receipt({"schema": trace.BUILD_RECEIPT_SCHEMA, "task_id": "T1"},
                             "receipt_fingerprint")
    options = {"kind": "build", "fingerprint_field": "receipt_fingerprint"}
    path = trace._write_receipt(tmp_path, receipt, **options)
    original = Path(path).read_bytes()
    assert trace._write_receipt(tmp_path, receipt, **options) == path
    assert trace._read_receipt(path, **options) == receipt
    with pytest.raises(trace.RemediationTraceError, match="collision"):
        trace._write_receipt(tmp_path, {**receipt, "task_id": "T2"}, **options)
    assert Path(path).read_bytes() == original


def test_receipt_reader_rejects_redirected_or_renamed_evidence(tmp_path):
    receipt = trace._receipt({"schema": trace.BUILD_RECEIPT_SCHEMA}, "receipt_fingerprint")
    options = {"kind": "build", "fingerprint_field": "receipt_fingerprint"}
    path = Path(trace._write_receipt(tmp_path, receipt, **options))
    renamed = tmp_path / "build-wrong.json"
    renamed.write_bytes(path.read_bytes())
    with pytest.raises(trace.RemediationTraceError, match="content-addressed"):
        trace._read_receipt(renamed, **options)
    linked = tmp_path / "linked"
    linked.symlink_to(path)
    with pytest.raises(trace.RemediationTraceError, match="path is invalid"):
        trace._read_receipt(linked, **options)


@pytest.mark.parametrize("amount,total", [(True, 5), (-1, 3), (2, 9)])
def test_debt_cost_rejects_boolean_negative_or_inconsistent_totals(amount, total):
    cost = {"unit": "days", "backfill": amount, "migration": 1, "compatibility": 1,
            "operator_reteaching": 1, "other": 1, "total": total, "basis": "owned estimate"}
    with pytest.raises(trace.RemediationTraceError):
        trace._priced_cost(cost, "now")
