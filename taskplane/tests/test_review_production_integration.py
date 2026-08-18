"""Production wiring for the canonical R-0009 review contracts."""
from __future__ import annotations

from taskplane import dashboard, review, review_artifacts, review_dor


def _production_inputs(tmp_path, *, complete=True):
    dor = review_dor.discover([{
        "kind": "pr_body", "identity": "PR-7", "revision": "abc123",
        "content": "Add bounded review artifacts\n- Preserve every finding",
    }], target_revision="abc123")
    revision = {
        "canonical_revision": 2,
        "target_fingerprint": "a" * 64,
        "context_fingerprint": "b" * 64,
        "findings_fingerprint": "c" * 64,
        "findings": [{"id": "F-1", "lens": "security", "severity": "high",
                      "title": "Finding", "rationale": "reason",
                      "action": "fix", "provenance": [{"slot_id": "s1"}]}],
        "disposition": "canonical" if complete else "provisional",
        "completeness": {"complete": complete, "expected": 1,
                         "collected": 1 if complete else 0},
        "gaps": [] if complete else [{"slot_id": "s1", "reason": "invalid"}],
        "approval": {"enabled": complete},
    }
    state = {
        "run_id": "run-7", "target": {"head": "abc123"},
        "slots": [{"slot_id": "s1", "lens_ids": ["security"]}],
        "review_execution": {
            "selection": "dynamic-render", "status": "configured",
            "dynamic_validation": {"status": "executed"},
            "functionality_render": {"status": "executed"},
            "consent": {"mode": "dynamic-render", "scope_fingerprint": "d" * 64},
        },
    }
    validation = {"schema": "taskplane.review-requirements-validation/v1",
                  "status": "pass", "criteria": []}
    return state, revision, dor, validation


def test_production_model_preserves_canonical_truth_and_disables_partial_gate(tmp_path):
    state, revision, dor, validation = _production_inputs(tmp_path, complete=False)

    model = review.production_review_model(
        state, revision, dor=dor, requirements_validation=validation)

    assert model["schema"] == review_artifacts.ARTIFACT_MODEL_SCHEMA
    assert model["revision"]["disposition"] == "provisional"
    assert model["dor"]["sources"][0]["identity"] == "PR-7"
    assert model["collection"]["gaps"][0]["slot_id"] == "s1"
    assert model["gate"]["approval_enabled"] is False
    assert model["findings"] == revision["findings"]


def test_production_publication_is_lossless_and_inline_is_bounded(tmp_path):
    state, revision, dor, validation = _production_inputs(tmp_path)
    result = review.publish_production_review(
        str(tmp_path), state, revision, dor=dor,
        requirements_validation=validation, host="codex")

    assert result["publication"]["status"] == "published"
    assert result["publication"]["finding_count"] == 1
    assert result["inline_pages"]
    assert all(len(page["html"].encode("utf-8")) <=
               dashboard.REVIEW_INLINE_PAGE_BUDGET
               for page in result["inline_pages"])
    assert result["inline_pages"][0]["transport"]["host"] == "codex"
    assert review_artifacts.parse_artifact(
        "json", (tmp_path / result["publication"]["artifacts"]["json"]
                 ["relative_path"]).read_bytes())["findings"] == revision["findings"]


def test_boolean_only_sandbox_claim_is_never_promoted_to_validation_evidence():
    execution = {"selection": "dynamic", "dynamic_validation": {
        "status": "executed", "sandbox": {"push_disabled": True}}}

    projected = review.production_validation_projection(execution)

    assert projected["status"] == "unverified"
    assert projected["sandbox_binding"] is None
    assert projected["legacy_push_disabled_claim"] is True


def test_real_collector_publishes_canonical_artifact_set_and_inline_pages():
    from taskplane.tests.test_review_routing import TestSelectiveReviewKernel

    fixture = TestSelectiveReviewKernel()
    fixture.setUp()
    try:
        opened = fixture._start()
        fixture._write_slot_results(run_id=opened["run_id"])
        collected = review.collect_review(
            fixture.ws, publish=False, run_id=opened["run_id"])
        state = review._load_state(fixture.ws, opened["run_id"])

        assert collected["artifact_set"]["status"] == "published"
        assert collected["inline_page_count"] >= 1
        assert state["production_review"]["model"]["revision"][
            "disposition"] == "canonical"
        assert state["production_review"]["publication"][
            "finding_count"] == len(state["revision"]["findings"])
    finally:
        fixture.tearDown()
