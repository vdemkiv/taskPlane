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


def test_authenticated_receipt_advances_exactly_once_and_rejects_stale(
        tmp_path):
    secret = b"host-owned-test-key"
    ledger_path = str(tmp_path / "approvals.json")
    ledger = review.NativeApprovalLedger(secret, state_path=ledger_path)
    decision = review.native_approval_decision(
        decision_id="review-1", kind="review", reason="Evidence complete",
        target="repo", revision="abc123", evidence=["report.json"],
        consequences=["gate advances"], owner="human", approvable=True,
        actions=[{"id": "approve", "label": "Approve"}],
    )
    receipt = ledger.issue(decision, action="approve", actor="user-7",
                           authenticated=True, nonce="n-1", now=100)
    consume = {"actor": "user-7", "authenticated": True, "now": 101}
    assert ledger.consume(receipt, decision, **consume)["advanced"] is True
    assert ledger.consume(receipt, decision, **consume)["advanced"] is False
    stale = dict(decision, revision="def456")
    with pytest.raises(review.ReviewKernelError, match="stale"):
        review.NativeApprovalLedger(secret, state_path=ledger_path).consume(
            receipt, stale, **consume)
    with pytest.raises(review.ReviewKernelError, match="authenticated"):
        review.NativeApprovalLedger(secret).issue(
            decision, action="approve", actor="bot", authenticated=False,
            nonce="n-2", now=100)


def test_receipt_revalidates_current_decision_action_and_actor_authority(
        tmp_path):
    secret = b"host-owned-test-key"
    ledger_path = str(tmp_path / "approvals.json")
    decision = review.native_approval_decision(
        decision_id="plan-1", kind="plan", reason="Ready",
        target="repo", revision="abc123", evidence=["plan.md"],
        consequences=["execute begins"], owner="human", approvable=True,
        actions=[{"id": "approve", "label": "Approve"}],
    )
    receipt = review.NativeApprovalLedger(secret, state_path=ledger_path).issue(
        decision, action="approve", actor="user-7", authenticated=True,
        nonce="n-1", now=100)
    current = dict(decision, approvable=False)
    with pytest.raises(review.ReviewKernelError, match="disabled"):
        review.NativeApprovalLedger(secret, state_path=ledger_path).consume(
            receipt, current, actor="user-7", authenticated=True, now=101)

    replacement = review.native_approval_decision(
        decision_id="plan-1", kind="plan", reason="Ready",
        target="repo", revision="abc123", evidence=["plan.md"],
        consequences=["execute begins"], owner="human", approvable=True,
        actions=[{"id": "request-changes", "label": "Request changes"}],
    )
    with pytest.raises(review.ReviewKernelError, match="not offered"):
        review.NativeApprovalLedger(secret, state_path=ledger_path).consume(
            receipt, replacement, actor="user-7", authenticated=True, now=101)
    with pytest.raises(review.ReviewKernelError, match="actor"):
        review.NativeApprovalLedger(secret, state_path=ledger_path).consume(
            receipt, decision, actor="user-8", authenticated=True, now=101)


def test_receipt_consumption_survives_restart_and_corruption_fails_closed(
        tmp_path):
    secret = b"host-owned-test-key"
    ledger_path = str(tmp_path / "approvals.json")
    decision = review.native_approval_decision(
        decision_id="review-1", kind="review", reason="Evidence complete",
        target="repo", revision="abc123", evidence=["report.json"],
        consequences=["gate advances"], owner="human", approvable=True,
        actions=[{"id": "approve", "label": "Approve"}],
    )
    first = review.NativeApprovalLedger(secret, state_path=ledger_path)
    receipt = first.issue(
        decision, action="approve", actor="user-7", authenticated=True,
        nonce="restart-1", now=100)
    consume = {"actor": "user-7", "authenticated": True, "now": 101}
    assert first.consume(receipt, decision, **consume)["advanced"] is True
    resumed = review.NativeApprovalLedger(secret, state_path=ledger_path)
    assert resumed.consume(receipt, decision, **consume) == {
        "advanced": False, "status": "duplicate",
        "receipt_id": receipt["receipt_id"],
    }

    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(review.ReviewKernelError, match="unavailable"):
        review.NativeApprovalLedger(
            secret, state_path=str(corrupt_path)).consume(
                receipt, decision, **consume)
