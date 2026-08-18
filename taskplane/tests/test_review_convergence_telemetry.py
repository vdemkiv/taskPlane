from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lens_telemetry  # noqa: E402
import review_convergence  # noqa: E402
import runtime_eval  # noqa: E402
import spend  # noqa: E402


def revision(*finding_ids: str, evidence: int = 0, tests: int = 0) -> dict:
    return {
        "sealed": True,
        "canonical_revision": 2,
        "findings": [
            {"id": finding_id, "lens": "security", "admissible": True,
             "confirmed": True}
            for finding_id in finding_ids
        ],
        "acceptance_evidence_complete": evidence,
        "tests_passed": tests,
    }


def test_convergence_classifies_closed_persistent_regressed_and_new():
    result = review_convergence.evaluate_fix_cycle(
        revision("closed", "persistent", evidence=1, tests=3),
        revision("persistent", "regressed", "new", evidence=2, tests=4),
        cycle=4, previously_closed={"regressed"})

    assert result["findings"] == {
        "closed": ["closed"],
        "persistent": ["persistent"],
        "regressed": ["regressed"],
        "new": ["new"],
    }
    assert result["progress"] == {
        "findings": False,
        "tests": True,
        "evidence": True,
        "measurable": True,
    }
    assert result["decision"] == "continue"


def test_safe_measurable_convergence_can_continue_beyond_three_cycles():
    prior = revision("a", "b", "c", evidence=3, tests=5)
    current = revision("b", "c", evidence=4, tests=5)

    result = review_convergence.evaluate_fix_cycle(prior, current, cycle=7)

    assert result["decision"] == "continue"
    assert result["reason"] == "measurable_convergence"


def test_runtime_calls_the_convergence_policy_without_changing_review_state():
    previous = revision("a", "b", evidence=1)
    current = revision("b", evidence=2)
    before = copy.deepcopy((previous, current))

    result = runtime_eval.review_fix_convergence_projection(
        previous, current, cycle=5)

    assert result["decision"] == "continue"
    assert result["findings"]["closed"] == ["a"]
    assert (previous, current) == before


@pytest.mark.parametrize("flag,reason", [
    ({"human_stop": True}, "human_stop"),
    ({"unsafe_recovery": True}, "unsafe_recovery"),
    ({"scope_changed": True}, "scope_changed"),
    ({"authority_changed": True}, "authority_changed"),
])
def test_human_owned_boundaries_escalate_immediately(flag, reason):
    result = review_convergence.evaluate_fix_cycle(
        revision("a"), revision(), cycle=1, **flag)

    assert result["decision"] == "escalate"
    assert result["reason"] == reason


def test_task_bound_repetition_worsening_and_oscillation_escalate():
    bounded = review_convergence.evaluate_fix_cycle(
        revision("a"), revision(), cycle=3, max_cycles=3)
    repeated = review_convergence.evaluate_fix_cycle(
        revision("a"), revision("a"), cycle=2,
        history=[{"current_fingerprint": "same", "progress": {"measurable": False}}],
        current_fingerprint="same")
    worsening = review_convergence.evaluate_fix_cycle(
        revision("a"), revision("a", "b"), cycle=2)
    oscillating = review_convergence.evaluate_fix_cycle(
        revision("a"), revision("b"), cycle=4,
        history=[
            {"previous_fingerprint": "x", "current_fingerprint": "y"},
            {"previous_fingerprint": "y", "current_fingerprint": "x"},
        ], previous_fingerprint="x", current_fingerprint="y")

    assert bounded["reason"] == "task_cycle_bound"
    assert repeated["reason"] == "repeated_fingerprint"
    assert worsening["reason"] == "worsening"
    assert oscillating["reason"] == "oscillation"
    assert {bounded["decision"], repeated["decision"],
            worsening["decision"], oscillating["decision"]} == {"escalate"}


def test_two_no_progress_cycles_escalate_but_one_can_retry():
    one = review_convergence.evaluate_fix_cycle(
        revision("a"), revision("a"), cycle=1)
    two = review_convergence.evaluate_fix_cycle(
        revision("a"), revision("a"), cycle=2,
        history=[{"progress": {"measurable": False},
                  "current_fingerprint": "prior"}],
        current_fingerprint="current")

    assert one["decision"] == "continue"
    assert one["reason"] == "bounded_no_progress_retry"
    assert two["decision"] == "escalate"
    assert two["reason"] == "no_progress"


def sealed_review() -> dict:
    return {
        "schema": "taskplane.review-revision/v3",
        "sealed": True,
        "canonical_revision": 9,
        "findings": [
            {"id": "S1", "fingerprint": "same", "lens": "security",
             "admissible": True, "confirmed": True},
            {"id": "S2", "fingerprint": "security-only", "lens": "security",
             "admissible": True, "confirmed": False},
            {"id": "S3", "fingerprint": "security-only", "lens": "security",
             "admissible": True, "confirmed": False},
            {"id": "A1", "fingerprint": "same", "lens": "architecture",
             "admissible": True, "confirmed": True},
            {"id": "A2", "fingerprint": "invalid", "lens": "architecture",
             "admissible": False, "invalidated": True},
            {"id": "A3", "fingerprint": "false", "lens": "architecture",
             "admissible": False, "false_positive": True},
        ],
        "slots": [
            {"lens": "security", "eligible": True, "selected": True,
             "promoted": False, "collected": True},
            {"lens": "architecture", "eligible": True, "selected": False,
             "promoted": True, "collected": True},
            {"lens": "qa", "eligible": True, "selected": True,
             "promoted": False, "collected": False},
        ],
    }


def lifecycle() -> dict[str, dict]:
    return {
        "security": {"retries": 1, "repairs": 2, "latency_ms": 125,
                     "infrastructure_available": True},
        "architecture": {"retries": 0, "repairs": 0, "latency_ms": 75,
                         "infrastructure_available": True},
        "qa": {"retries": 2, "repairs": 0, "latency_ms": 500,
               "infrastructure_available": False,
               "unavailable_reason": "provider outage"},
    }


def usage() -> dict[str, dict]:
    return {
        "security": {
            "provider": "codex",
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 40},
                      "total_tokens": 120},
            "rates_per_million": {"uncached_input": 10, "cached_input": 1,
                                  "output": 20},
        },
        "architecture": {
            "provider": "claude",
            "usage": {"input_tokens": 60, "cache_read_input_tokens": 40,
                      "cache_creation_input_tokens": 10, "output_tokens": 10,
                      "total_tokens": 120},
        },
    }


def test_lens_telemetry_has_versioned_complete_golden_arithmetic():
    report = lens_telemetry.build_lens_telemetry(
        sealed_review(), lifecycle=lifecycle(), usage_by_lens=usage())

    assert report["schema"] == "taskplane.lens-quality-telemetry/v1"
    assert report["source"] == {"canonical_revision": 9, "sealed": True}
    assert report["definitions"]["version"] == 1
    security = report["lenses"]["security"]
    architecture = report["lenses"]["architecture"]
    assert security["routing"] == {
        "eligible": 1, "selected": 1, "promoted": 0, "collected": 1}
    assert security["findings"] == {
        "admissible": 3, "confirmed": 1, "unique": 1, "overlap": 1,
        "duplicate": 1, "invalidated": 0, "false_positive": 0}
    assert architecture["findings"] == {
        "admissible": 1, "confirmed": 1, "unique": 0, "overlap": 1,
        "duplicate": 0, "invalidated": 1, "false_positive": 1}
    assert security["execution"] == {
        "retries": 1, "repairs": 2, "latency_ms": 125,
        "infrastructure": {"available": True, "reason": None}}
    assert security["tokens"]["raw_total_tokens"] == 120
    assert security["tokens"]["effective_tokens"] == 164
    assert security["cost"] == {"available": True, "usd": 0.00104,
                                "reason": None}


def test_unavailable_is_distinct_from_zero_and_missing_denominators():
    report = lens_telemetry.build_lens_telemetry(
        sealed_review(), lifecycle=lifecycle(), usage_by_lens=usage())

    qa = report["lenses"]["qa"]
    architecture = report["lenses"]["architecture"]
    assert qa["tokens"]["available"] is False
    assert qa["tokens"]["raw_total_tokens"] is None
    assert qa["execution"]["infrastructure"] == {
        "available": False, "reason": "provider outage"}
    assert architecture["tokens"]["available"] is True
    assert architecture["cost"]["available"] is False
    assert architecture["cost"]["usd"] is None
    assert "rates" in architecture["cost"]["reason"]
    assert report["definitions"]["denominators"]["selected"] == "eligible slots"


def test_telemetry_rejects_unsealed_or_draft_revisions_without_leaking_findings():
    draft = sealed_review()
    draft["sealed"] = False
    draft["drafts"] = {"security": [{"secret": "cross-lens"}]}

    with pytest.raises(ValueError, match="sealed canonical revision"):
        lens_telemetry.build_lens_telemetry(
            draft, lifecycle=lifecycle(), usage_by_lens=usage())


def test_telemetry_is_information_isolated_and_does_not_mutate_verdict_or_floors():
    source = sealed_review()
    source["verdict"] = "request_changes"
    source["mandatory_floors"] = ["architecture", "code-quality", "security", "qa"]
    original = copy.deepcopy(source)

    report = lens_telemetry.build_lens_telemetry(
        source, lifecycle=lifecycle(), usage_by_lens=usage())

    assert source == original
    assert "verdict" not in report
    assert "mandatory_floors" not in report
    assert isinstance(report["lenses"]["security"]["findings"], dict)
    assert "S1" not in repr(report)


def test_disabled_telemetry_has_identical_review_outcome_and_no_metrics():
    source = sealed_review()
    enabled_outcome = runtime_eval.review_outcome_with_lens_telemetry(
        source, lifecycle=lifecycle(), usage_by_lens=usage(), enabled=True)
    disabled_outcome = runtime_eval.review_outcome_with_lens_telemetry(
        source, lifecycle=lifecycle(), usage_by_lens=usage(), enabled=False)

    assert enabled_outcome["review"] == disabled_outcome["review"] == source
    assert enabled_outcome["telemetry"] is not None
    assert disabled_outcome["telemetry"] is None


def test_spend_provider_cost_projection_preserves_provider_semantics():
    codex = spend.provider_cost_projection(
        usage()["security"]["usage"], provider="codex",
        rates_per_million=usage()["security"]["rates_per_million"])
    unavailable = spend.provider_cost_projection(
        {"input_tokens": 5}, provider="codex", rates_per_million={})

    assert codex["usage"]["uncached_input_tokens"] == 60
    assert codex["usage"]["cached_input_tokens"] == 40
    assert codex["cost"]["usd"] == 0.00104
    assert unavailable["usage"]["available"] is False
    assert unavailable["cost"]["usd"] is None
