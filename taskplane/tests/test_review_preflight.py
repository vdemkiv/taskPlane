import os
import sys
import tempfile

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import review  # noqa: E402


def _host_receipt(*, action_id, response, actor="human", run_id="run-1"):
    return {
        "schema": "taskplane.review-user-action-receipt/v1",
        "host_observed": True,
        "source": "codex-session:user-message",
        "receipt_id": f"receipt-{action_id}-{response}",
        "run_id": run_id,
        "action_id": action_id,
        "response": response,
        "actor": actor,
    }


def _start_review_without_execution_choice():
    ws = tempfile.mkdtemp(prefix="tp-review-preflight-")
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "service.py"), "w",
              encoding="utf-8") as stream:
        stream.write("def changed():\n    return 2\n")
    opened = review.start_review(
        ws,
        target={"fingerprint": "target-1", "head": "abc123"},
        graph={
            "meta": {"scanned_head": "abc123",
                     "content_fingerprint": "graph-1"},
            "modules": {"src": {"files": ["src/service.py"]}},
            "edges": [],
        },
        impact={"touched": ["src"], "impacted": {},
                "total_impacted": 1, "unknown": []},
        diff={"files": ["src/service.py"],
              "changed_symbols": ["changed"],
              "patch_artifact": {"fingerprint": "diff-1"}},
        runnability={"summary": "available"},
        requirement={"id": "R-1", "text": "safe change"},
        acceptance=["works"], contracts=["contract:api"],
        task_type="review",
    )
    return ws, opened


def test_review_preflight_exposes_one_structured_choice_without_side_effects():
    row = review.review_execution_preflight()
    assert row["schema"] == "taskplane.review-execution-preflight/v1"
    assert row["status"] == "needs_user"
    assert row["static_only"] is True
    assert row["side_effects_started"] is False
    assert [choice["response"] for choice in row["action"]["choices"]] == [
        "static", "dynamic", "dynamic-render"]
    assert row["action"]["choices"][1]["requires"] == [
        "dependency-install", "process-execution"]
    assert row["action"]["choices"][2]["requires"] == [
        "dependency-install", "process-execution", "browser-access"]


def test_review_preflight_records_declined_unavailable_and_executed_evidence():
    pending = review.review_execution_preflight(run_id="run-1")
    static = review.review_execution_preflight(
        selection="static", run_id="run-1",
        approval_receipt=_host_receipt(
            action_id=pending["action"]["id"], response="static"))
    assert static["status"] == "configured"
    assert static["static_only"] is True
    assert static["dynamic_validation"]["status"] == "declined"
    assert static["functionality_render"]["status"] == "declined"

    selected = review.review_execution_preflight(
        selection="dynamic-render", run_id="run-1",
        approval_receipt=_host_receipt(
            action_id=pending["action"]["id"], response="dynamic-render"))
    assert selected["static_only"] is False
    assert selected["dynamic_validation"]["status"] == "selected"
    assert selected["functionality_render"]["status"] == "selected"
    assert selected["side_effects_started"] is False

    executed = review.record_review_execution_evidence(
        selected, kind="dynamic_validation", status="executed",
        detail="npm test: 42 passed",
        approval_receipt=_host_receipt(
            action_id=selected["dynamic_validation"]["action_id"],
            response="executed"))
    assert executed["dynamic_validation"]["status"] == "executed"
    assert executed["dynamic_validation"]["detail"] == "npm test: 42 passed"
    assert executed["functionality_render"]["status"] == "selected"


def test_pending_review_execution_choice_blocks_dispatch_and_collection():
    ws, opened = _start_review_without_execution_choice()

    assert opened["status"] == "needs_user"
    assert opened["slots"] == []
    with pytest.raises(review.ReviewKernelError,
                       match="pending human selection"):
        review.collect_review(ws, publish=False, run_id=opened["run_id"])

    ready = review.configure_review_execution(
        ws, selection="static", run_id=opened["run_id"],
        approval_receipt=_host_receipt(
            run_id=opened["run_id"],
            action_id=opened["review_execution"]["action"]["id"],
            response="static"))
    assert ready["status"] == "ready"
    assert ready["review_execution"]["selection"] == "static"
    assert ready["slots"]


def test_static_human_choice_cannot_be_overwritten_by_evidence():
    pending = review.review_execution_preflight(run_id="run-1")
    static = review.review_execution_preflight(
        selection="static", run_id="run-1",
        approval_receipt=_host_receipt(
            action_id=pending["action"]["id"], response="static"))

    with pytest.raises(review.ReviewKernelError, match="declined by the human"):
        review.record_review_execution_evidence(
            static, kind="functionality_render", status="selected")
    with pytest.raises(review.ReviewKernelError, match="host-observed"):
        review.record_review_execution_evidence(
            static, kind="functionality_render", status="executed")


def test_caller_controlled_identity_is_not_human_approval():
    with pytest.raises(review.ReviewKernelError, match="host-observed"):
        review.review_execution_preflight(
            selection="dynamic", decided_by="model-supplied --by")
