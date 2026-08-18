import copy
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import review_evidence  # noqa: E402
import review_recovery  # noqa: E402


def _lease(slot="deep.frontend"):
    return {
        "schema": "taskplane.slot-lease/v1",
        "lease_fingerprint": "lease-frontend",
        "slot_id": slot,
        "lens_ids": ["frontend"],
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
        "view_fingerprint": "view-1",
        "canonical_revision": 1,
    }


def _result():
    lease = _lease()
    return {
        "schema": "taskplane.lens-slot-output/v2",
        **{key: lease[key] for key in (
            "lease_fingerprint", "slot_id", "lens_ids",
            "target_fingerprint", "context_fingerprint",
            "view_fingerprint", "canonical_revision")},
        "authored_by": "lens-slot",
        "lens_results": [{
            "lens": "frontend", "verdict": "pass", "blockers": 0,
            "checked_evidence": [{"path": "client/src/App.tsx", "line": 42}],
        }],
        "findings": [{
            "lens": "frontend", "kind": "defect", "severity": "major",
            "class": "regression", "file": "client/src/App.tsx", "line": 42,
            "title": "Timeline controls lost their accessible label",
            "scenario": "A keyboard user cannot identify the date range control",
            "fix": "Restore the accessible label",
        }],
    }


def test_summary_only_contradiction_is_repaired_without_rerunning_producer():
    original = _result()

    recovered = review_recovery.recover_summary_or_plan_retry(
        original, _lease(), blocking_by_lens={"frontend": 1},
        attempts={"deep.frontend": 0})

    assert recovered["status"] == "repaired"
    assert recovered["producer_rerun_required"] is False
    assert recovered["result"]["lens_results"][0]["verdict"] == "fail"
    assert recovered["result"]["lens_results"][0]["blockers"] == 1
    assert recovered["result"]["findings"] == original["findings"]
    assert recovered["result"]["lens_results"][0]["checked_evidence"] == \
        original["lens_results"][0]["checked_evidence"]
    assert recovered["audit"]["changes"] == [
        {"path": "lens_results[0].blockers", "before": 0, "after": 1,
         "derived_from": "canonical-blocking-findings"},
        {"path": "lens_results[0].verdict", "before": "pass", "after": "fail",
         "derived_from": "canonical-blocking-findings"},
    ]


def test_evidence_guard_rejects_any_change_beyond_summary_fields():
    before = _result()
    after = copy.deepcopy(before)
    after["findings"][0]["title"] = "rewritten"

    try:
        review_evidence.assert_summary_only_repair(before, after)
    except review_evidence.ProvenanceError as exc:
        assert "review substance" in str(exc)
    else:
        raise AssertionError("substantive change was accepted")


def test_unsafe_summary_repair_schedules_only_the_affected_slot():
    result = _result()
    result["lens_results"][0]["checked_evidence"] = "not-a-list"

    recovery = review_recovery.recover_summary_or_plan_retry(
        result, _lease(), blocking_by_lens={"frontend": 1},
        leases=[_lease("deep.frontend"), dict(
            _lease("deep.security"), lease_fingerprint="lease-security",
            lens_ids=["security"], view_fingerprint="view-security")],
        valid_results={"deep.security": "result-security"},
        attempts={"deep.frontend": 0})

    assert recovery["status"] == "retry"
    assert recovery["producer_rerun_required"] is True
    assert recovery["affected_slot_ids"] == ["deep.frontend"]
    assert [row["slot_id"] for row in recovery["retry_plan"]["producer_calls"]] \
        == ["deep.frontend"]
    assert recovery["retry_plan"]["reused_results"] == {
        "deep.security": "result-security"}


def test_repaired_summary_is_idempotent_and_collectable():
    first = review_recovery.recover_summary_or_plan_retry(
        _result(), _lease(), blocking_by_lens={"frontend": 1}, attempts={})
    replay = review_recovery.recover_summary_or_plan_retry(
        first["result"], _lease(), blocking_by_lens={"frontend": 1}, attempts={})

    assert replay["status"] == "unchanged"
    assert replay["result"] == first["result"]
    assert replay["audit"]["changes"] == []
