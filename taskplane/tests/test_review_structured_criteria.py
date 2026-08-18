"""R-0010 structured acceptance criteria remain canonical end to end."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import review  # noqa: E402
import review_dor  # noqa: E402


def target(**extra):
    return {"head": "head-1", "base": "base-1", "changed_files": [], **extra}


def test_structured_only_criteria_are_canonical_with_stable_provenance(tmp_path):
    criteria = [
        {"id": "copy", "text": "Copy reports failure", "source": "PR-7"},
        {"id": "cache", "criterion": "Add server-side caching",
         "source_identity": "PR-7", "source_revision": "comment-3"},
    ]
    dor = review.review_dor_evidence(
        str(tmp_path), target(), requirement={"id": "R-7", "text": ""},
        acceptance=criteria)

    assert dor["status"] == "ready"
    assert dor["acceptance"] == ["Copy reports failure", "Add server-side caching"]
    assert [row["id"] for row in dor["canonical"]["criteria"]] == ["copy", "cache"]
    assert [row["source_identity"] for row in dor["canonical"]["criteria"]] == [
        "PR-7", "PR-7"]
    assert [row["source_revision"] for row in dor["canonical"]["criteria"]] == [
        "head-1", "comment-3"]


def test_duplicate_identity_is_deduped_and_conflict_fails_closed(tmp_path):
    dor = review.review_dor_evidence(
        str(tmp_path), target(), requirement={"text": "unrelated context"},
        acceptance=[{"id": "one", "text": "Keep ordering"},
                    {"id": "one", "text": "Keep ordering"},
                    {"id": "two", "text": "Keep ordering"}])
    assert [row["id"] for row in dor["canonical"]["criteria"]] == ["one", "two"]

    with pytest.raises(ValueError, match="identity conflicts"):
        review.review_dor_evidence(
            str(tmp_path), target(), requirement={},
            acceptance=[{"id": "same", "text": "First"},
                        {"id": "same", "text": "Second"}])
    for malformed in ({"id": "missing-text"}, 42, "  "):
        with pytest.raises(ValueError, match="acceptance criter"):
            review.review_dor_evidence(
                str(tmp_path), target(), requirement={}, acceptance=[malformed])


def test_reordering_does_not_change_structured_identity(tmp_path):
    values = [{"id": "a", "text": "Add alpha"},
              {"id": "b", "text": "Add beta"}]
    first = review.review_dor_evidence(
        str(tmp_path), target(), requirement={}, acceptance=values)
    second = review.review_dor_evidence(
        str(tmp_path), target(), requirement={}, acceptance=list(reversed(values)))
    first_rows = {row["id"]: row for row in first["canonical"]["criteria"]}
    second_rows = {row["id"]: row for row in second["canonical"]["criteria"]}
    assert first_rows == second_rows


def test_criteria_flow_into_validation_model_and_approval_gate(tmp_path):
    dor = review.review_dor_evidence(
        str(tmp_path), target(), requirement={},
        acceptance=[{"id": "alpha", "text": "Add Alpha.ts",
                     "source_identity": "PR-9", "revision": "head-1"}])
    validation = review.evaluate_review_requirements(
        dor, {"files": ["src/Alpha.ts"], "patch": "+ Alpha"}, [],
        {"dynamic_validation": {"status": "executed"}})
    assert validation["status"] == "pass"
    assert validation["criteria"][0]["id"] == "alpha"
    assert validation["criteria"][0]["source_identity"] == "PR-9"

    state = {"run_id": "run-1", "target": {"head": "head-1"},
             "slots": [], "review_execution": {}}
    revision = {"canonical_revision": 1, "disposition": "canonical",
                "completeness": {"complete": True, "expected": 0,
                                 "collected": 0},
                "gaps": [], "approval": {"enabled": True},
                "findings": [], "findings_fingerprint": "f",
                "target_fingerprint": "t", "context_fingerprint": "c"}
    model = review.production_review_model(
        state, revision, dor=dor, requirements_validation=validation)
    criterion = model["criteria"][0]
    assert criterion["id"] == "alpha"
    assert criterion["text"] == "Add Alpha.ts"
    assert criterion["verdict"] == "pass"
    assert criterion["source_identity"] == "PR-9"
    assert model["gate"]["approval_enabled"] is True

    ledger = review_dor.criterion_ledger([{
        **criterion, "status": "pass", "rationale": "matched diff",
        "evidence_ref": "artifact:diff", "verification_method": "review",
        "responsible": "review-kernel",
    }], revision="head-1")
    assert ledger["criteria"][0]["source_identity"] == "PR-9"
    assert ledger["approvable"] is True

    published = review.publish_production_review(
        str(tmp_path), state, revision, dor=dor,
        requirements_validation=validation)
    assert published["status"] == "published"
    assert published["model"]["criteria"] == model["criteria"]
    assert any("Add Alpha.ts" in page["html"]
               for page in published["inline_pages"])
    for artifact in published["publication"]["artifacts"].values():
        path = tmp_path / artifact["relative_path"]
        assert b"Add Alpha.ts" in path.read_bytes()

    validation["criteria"][0]["status"] = "cannot_verify"
    validation["status"] = "needs_evidence"
    blocked = review.production_review_model(
        state, revision, dor=dor, requirements_validation=validation)
    assert blocked["gate"]["approval_enabled"] is False
