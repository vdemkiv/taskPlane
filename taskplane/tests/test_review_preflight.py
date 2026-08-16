import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import review  # noqa: E402


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
    static = review.review_execution_preflight(
        selection="static", decided_by="human")
    assert static["status"] == "configured"
    assert static["static_only"] is True
    assert static["dynamic_validation"]["status"] == "declined"
    assert static["functionality_render"]["status"] == "declined"

    selected = review.review_execution_preflight(
        selection="dynamic-render", decided_by="human")
    assert selected["static_only"] is False
    assert selected["dynamic_validation"]["status"] == "selected"
    assert selected["functionality_render"]["status"] == "selected"
    assert selected["side_effects_started"] is False

    executed = review.record_review_execution_evidence(
        selected, kind="dynamic_validation", status="executed",
        detail="npm test: 42 passed")
    assert executed["dynamic_validation"]["status"] == "executed"
    assert executed["dynamic_validation"]["detail"] == "npm test: 42 passed"
    assert executed["functionality_render"]["status"] == "selected"

