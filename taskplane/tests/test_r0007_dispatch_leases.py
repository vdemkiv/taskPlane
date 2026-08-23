"""R-0007: governed review spawns are auditable by exact lease."""
from pathlib import Path

import pytest

import review


def test_governed_spawn_without_live_matching_lease_is_blocked(tmp_path):
    contract = {"bootstrap_lease_fingerprint": "a" * 64,
                "task_slot": "review-a", "read_only": True,
                "task": "review lens slot", "write_allow": ["result.json"]}
    event = {"agent_id": "child-1", "turn_id": "turn-1"}
    with pytest.raises(review.ReviewKernelError,
                       match="dispatch-lease-mismatch"):
        review.register_slot_producer(
            str(tmp_path), event=event, contract=contract,
            task_slot="review-a")
    audit = review.record_dispatch_audit(
        str(tmp_path), contract=contract, event=event, status="blocked",
        reason="no matching lease")
    assert audit["evidence_eligible"] is False
    assert audit["expected_lease_fingerprint"] == "a" * 64


def test_matching_audit_is_evidence_eligible(tmp_path):
    contract = {"bootstrap_lease_fingerprint": "a" * 64,
                "task_slot": "review-a"}
    assignment = {"lease_fingerprint": "a" * 64}
    audit = review.record_dispatch_audit(
        str(tmp_path), contract=contract,
        event={"agent_id": "child", "turn_id": "turn"},
        status="authorized", reason="exact match", assignment=assignment)
    assert audit["evidence_eligible"] is True

