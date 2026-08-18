import copy
import os
import sys

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

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
        "producer": "lens-slot",
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
            "lens": "frontend", "verdict": "fail", "blockers": 1,
            "checked_evidence": [],
        }],
        "findings": [{
            "lens": "frontend", "kind": "violation", "severity": "major",
            "class": "regression", "file": "client/src/App.tsx", "line": 42,
            "title": "Timeline controls lost their accessible label",
            "scenario": "A keyboard user cannot identify the date range control",
            "fix": "Restore the accessible label",
            "declares": "Date range controls remain accessible to keyboard users.",
        }],
    }


def _authority():
    return [{
        "id": "AC-4",
        "text": "Date-range controls remain accessible to keyboard users",
        "source_fingerprint": "dor-1",
    }]


def test_free_form_declaration_is_canonicalized_without_substantive_rerun():
    result = _result()

    recovered = review_recovery.repair_slot_result(
        result, _lease(), declarations=_authority(), actor="review-kernel")

    assert recovered["status"] == "repaired"
    assert recovered["producer_rerun_required"] is False
    assert recovered["result"]["findings"][0]["declares"] == "AC-4"
    assert recovered["result"]["findings"][0]["title"] == \
        result["findings"][0]["title"]
    audit = recovered["audit"]
    assert audit["schema"] == "taskplane.review-repair-audit/v1"
    assert audit["rule"] == "lease-derived-metadata/v1"
    assert audit["before_fingerprint"] != audit["after_fingerprint"]
    assert audit["equivalence_fingerprint_before"] == \
        audit["equivalence_fingerprint_after"]
    assert audit["changes"] == [{
        "path": "findings[0].declares",
        "before": "Date range controls remain accessible to keyboard users.",
        "after": "AC-4",
        "derived_from": "declaration:AC-4@dor-1",
    }]


@pytest.mark.parametrize("field", [
    "findings", "lens_results", "target_fingerprint", "producer", "slot_id",
])
def test_substantive_or_authority_tamper_is_rejected(field):
    result = _result()
    lease = _lease()
    if field == "findings":
        result["findings"][0]["title"] = "invented replacement"
        expected = copy.deepcopy(result)
        expected["findings"][0]["title"] = "Timeline controls lost their accessible label"
    elif field == "lens_results":
        result["lens_results"][0]["blockers"] = 0
        expected = copy.deepcopy(result)
        expected["lens_results"][0]["blockers"] = 1
    elif field == "producer":
        result["producer"] = "different-producer"
        expected = result
    else:
        result[field] = "different"
        expected = result

    with pytest.raises(review_recovery.RepairRejected):
        review_recovery.repair_slot_result(
            result, lease, declarations=_authority(),
            expected_result=expected)


def test_unverifiable_declaration_is_rejected_and_names_affected_slot():
    result = _result()
    result["findings"][0]["declares"] = "A completely unrelated promise"

    rejected = review_recovery.attempt_slot_repair(
        result, _lease(), declarations=_authority())

    assert rejected["status"] == "rejected"
    assert rejected["affected_slot_ids"] == ["deep.frontend"]
    assert rejected["producer_rerun_required"] is True
    assert rejected["result"] is None


def test_missing_schema_and_provenance_are_copied_only_from_lease():
    result = _result()
    for key in ("schema", "lease_fingerprint", "view_fingerprint",
                "canonical_revision", "authored_by"):
        result.pop(key)

    recovered = review_recovery.repair_slot_result(
        result, _lease(), declarations=_authority())

    assert recovered["result"]["schema"] == "taskplane.lens-slot-output/v2"
    assert recovered["result"]["lease_fingerprint"] == "lease-frontend"
    assert recovered["result"]["view_fingerprint"] == "view-1"
    assert recovered["result"]["canonical_revision"] == 1
    assert recovered["result"]["authored_by"] == "lens-slot"


def test_retry_plan_calls_only_affected_producers_and_preserves_valid_results():
    leases = [_lease("deep.security"), _lease("deep.frontend"),
              _lease("deep.quality")]
    leases[0].update(lease_fingerprint="lease-security",
                     lens_ids=["security"], view_fingerprint="view-security")
    leases[2].update(lease_fingerprint="lease-quality",
                     lens_ids=["quality"], view_fingerprint="view-quality")
    valid = {
        "deep.security": "result-security",
        "deep.quality": "result-quality",
    }

    plan = review_recovery.plan_affected_retries(
        leases, valid_results=valid,
        failures=[{"slot_id": "deep.frontend", "reason": "unverifiable"}],
        attempts={"deep.frontend": 0})

    assert plan["affected_slot_ids"] == ["deep.frontend"]
    assert [row["slot_id"] for row in plan["producer_calls"]] == [
        "deep.frontend"]
    assert plan["reused_results"] == valid
    assert plan["reused_result_count"] == 2


def test_retry_and_repair_are_idempotent_and_never_duplicate_findings():
    first = review_recovery.repair_slot_result(
        _result(), _lease(), declarations=_authority())
    replay = review_recovery.repair_slot_result(
        first["result"], _lease(), declarations=_authority())
    plan = review_recovery.plan_affected_retries(
        [_lease()], valid_results={"deep.frontend": "result-frontend"},
        failures=[], attempts={})

    assert replay["status"] == "unchanged"
    assert replay["result"] == first["result"]
    assert replay["audit"]["before_fingerprint"] == \
        replay["audit"]["after_fingerprint"]
    assert plan["producer_calls"] == []
    combined = review_recovery.merge_findings_once([
        {"result_fingerprint": "result-frontend",
         "findings": first["result"]["findings"]},
        {"result_fingerprint": "result-frontend",
         "findings": first["result"]["findings"]},
    ])
    assert len(combined) == 1


def test_retry_exhaustion_is_stable_and_does_not_touch_valid_slots():
    plan = review_recovery.plan_affected_retries(
        [_lease()], valid_results={},
        failures=[{"slot_id": "deep.frontend", "reason": "invalid"}],
        attempts={"deep.frontend": 2})

    assert plan["status"] == "unavailable"
    assert plan["producer_calls"] == []
    assert plan["exhausted_slot_ids"] == ["deep.frontend"]
