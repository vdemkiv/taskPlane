import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import loop
import review
import runtime_eval
from taskplane.host_native import HostSurfaceSnapshot


def snapshot(sequence, state="running", *, persistent=True):
    return HostSurfaceSnapshot.create(
        workflow_id="wf-1", run_id="run-1", target="repo", revision="abc123",
        sequence=sequence, stage="execute", state=state,
        values={"persistent": persistent, "active_work": 2,
                "completed_work": sequence, "attention": []},
    )


def test_pip_is_one_ordered_session_and_closes_once():
    session = loop.NativeProgressSession()
    assert session.publish(snapshot(0))["transition"] == "open"
    assert session.publish(snapshot(1))["transition"] == "update"
    assert session.publish(snapshot(2, "completed"))["transition"] == "close"
    assert session.publish(snapshot(2, "completed"))["transition"] == "duplicate"
    with pytest.raises(ValueError, match="closed"):
        session.publish(snapshot(3))
    assert loop.NativeProgressSession().publish(
        snapshot(0, "completed", persistent=False))["transition"] == "none"


@pytest.mark.parametrize("usage,status", [
    ({"raw_total_tokens": 100, "effective_tokens": 40}, "observed"),
    ({"raw_total_tokens": 100}, "partial"),
    ({}, "unavailable"),
    ({"raw_total_tokens": "bad", "effective_tokens": -1}, "malformed"),
])
def test_tokens_remain_truthful(usage, status):
    row = runtime_eval.observed_token_projection(
        usage, provider="codex", source="host-telemetry", scope="run")
    assert row["status"] == status
    if status != "observed":
        assert row.get("raw_tokens") != 0
        assert row.get("effective_tokens") != 0
    assert row["provider"] == "codex"
    assert len(row["fingerprint"]) == 64


def test_agent_topology_has_stable_identity_retry_and_no_phantoms():
    events = [
        {"task_id": "t1", "slot_id": "s1", "agent_id": "a1", "role": "executor",
         "scope": ["a.py"], "wave": "w1", "state": "running"},
        {"task_id": "t1", "slot_id": "s1", "agent_id": "a1", "role": "executor",
         "scope": ["a.py"], "wave": "w1", "state": "failed", "outcome": "failed"},
        {"task_id": "t1", "slot_id": "s1", "agent_id": "a2", "role": "fixer",
         "scope": ["a.py"], "wave": "w2", "state": "running", "retry_of": "a1"},
    ]
    graph = loop.project_agent_topology(events)
    assert [node["agent_id"] for node in graph["nodes"]] == ["a1", "a2"]
    assert graph["nodes"][0]["state"] == "failed"
    assert {tuple(edge.values()) for edge in graph["edges"]} >= {
        ("a1", "a2", "retry")}


def test_decision_context_is_complete_and_bounded_to_two_actions():
    decision = review.native_approval_decision(
        decision_id="plan-1", kind="plan", reason="Ready",
        target="repo", revision="abc123", evidence=["plan.md"],
        consequences=["execute begins"], owner="human", approvable=True,
        actions=[{"id": "approve", "label": "Approve"},
                 {"id": "reject", "label": "Reject"},
                 {"id": "edit", "label": "Edit"}],
    )
    assert len(decision["actions"]) == 2
    assert decision["detail_action"]["id"] == "view-details"
    for key in ("decision_id", "reason", "target", "revision", "evidence",
                "consequences", "owner", "approvable"):
        assert key in decision


def test_authenticated_receipt_advances_exactly_once_and_rejects_stale():
    secret = b"host-owned-test-key"
    ledger = review.NativeApprovalLedger(secret)
    decision = review.native_approval_decision(
        decision_id="review-1", kind="review", reason="Evidence complete",
        target="repo", revision="abc123", evidence=["report.json"],
        consequences=["gate advances"], owner="human", approvable=True,
        actions=[{"id": "approve", "label": "Approve"}],
    )
    receipt = ledger.issue(decision, action="approve", actor="user-7",
                           authenticated=True, nonce="n-1", now=100)
    assert ledger.consume(receipt, decision, now=101)["advanced"] is True
    assert ledger.consume(receipt, decision, now=101)["advanced"] is False
    stale = dict(decision, revision="def456")
    with pytest.raises(review.ReviewKernelError, match="binding"):
        review.NativeApprovalLedger(secret).consume(receipt, stale, now=101)
    with pytest.raises(review.ReviewKernelError, match="authenticated"):
        review.NativeApprovalLedger(secret).issue(
            decision, action="approve", actor="bot", authenticated=False,
            nonce="n-2", now=100)
