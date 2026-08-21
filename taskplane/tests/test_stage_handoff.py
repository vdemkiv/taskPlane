"""Canonical stage handoff manifests preserve the complete bounded contract."""
from __future__ import annotations

import pytest

from taskplane import review_evidence, stage_handoff


def _authority(revision: int = 7) -> dict[str, object]:
    return {
        "actor": "human:vdemkiv",
        "session_id": "codex-thread-1",
        "authorized_at": "2026-08-20T18:00:00Z",
        "operation_id": "handoff-op-1",
        "authority_record": {
            "schema": "taskplane.authority-record-reference/v1",
            "authority_schema": "taskplane.consolidated-authorization/v1",
            "revision": revision,
            "fingerprint": "a" * 64,
        },
    }


def _manifest(store: review_evidence.ArtifactStore, **changes: object) -> dict:
    evidence = store.put("review-evidence", {"verdict": "pass"})
    deliverable = store.put("delivery", {"commit": "1" * 40})
    values: dict[str, object] = {
        "producer_stage_id": "stage-build-001",
        "producer_outcome": "done",
        "requirement": {
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        "design": {"revision": "2", "fingerprint": "c" * 64},
        "target": {"repository_id": "github.com/vdemkiv/taskplane",
                   "fingerprint": "d" * 64},
        "commit": {"sha": "1" * 40, "target_fingerprint": "d" * 64},
        "contracts": {
            "provided": ["contract:stage-artifact-handoff"],
            "consumed": ["contract:review-evidence-binding"],
            "changed": ["contract:consolidated-authorization"],
        },
        "deliverables": ["commit", "declared-tests"],
        "evidence_references": [evidence],
        "selected_artifacts": [deliverable],
        "exclusions": list(stage_handoff.REQUIRED_EXCLUSIONS),
        "authorization": _authority(),
    }
    values.update(changes)
    return stage_handoff.create_manifest(store, **values)


def test_manifest_records_complete_identity_and_redacted_artifacts(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))

    manifest = _manifest(store)

    assert manifest["schema"] == "taskplane.stage-handoff/v1"
    assert manifest["producer"] == {
        "stage_id": "stage-build-001", "outcome": "done"
    }
    assert manifest["requirement"]["id"] == "R-0004"
    assert manifest["design"]["fingerprint"] == "c" * 64
    assert manifest["target"]["repository_id"] == \
        "github.com/vdemkiv/taskplane"
    assert manifest["commit"]["sha"] == "1" * 40
    assert manifest["contracts"]["provided"] == \
        ["contract:stage-artifact-handoff"]
    assert manifest["deliverables"] == ["commit", "declared-tests"]
    assert manifest["authorization"]["actor"] == "human:vdemkiv"
    assert manifest["authorization"]["authorized_at"].endswith("Z")
    assert manifest["authorization"]["authority_record"]["revision"] == 7
    assert set(manifest["exclusions"]) >= stage_handoff.REQUIRED_EXCLUSIONS

    references = manifest["evidence_references"] + \
        manifest["selected_artifacts"]
    assert all("path" not in reference for reference in references)
    assert all("relative_path" not in reference for reference in references)
    assert all(reference["locator"].startswith("artifact://")
               for reference in references)
    assert stage_handoff.validate_manifest(
        store, manifest, expected_authority_revision=7) == manifest
    assert stage_handoff.manifest_fingerprint(manifest) == \
        manifest["fingerprint"]


def test_canonical_manifest_is_deterministic_and_content_addressed(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    first = _manifest(store)
    second = _manifest(store)

    assert first == second
    first_ref = stage_handoff.store_manifest(store, first)
    second_ref = stage_handoff.store_manifest(store, second)
    assert first_ref == second_ref
    assert stage_handoff.read_manifest(
        store, first_ref, expected_authority_revision=7,
        expected_authority_fingerprint="a" * 64) == first


def test_target_and_commit_are_jointly_optional(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))

    without_git_identity = _manifest(store, target=None, commit=None)
    assert without_git_identity["target"] is None
    assert without_git_identity["commit"] is None

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="target and commit"):
        _manifest(store, commit=None)


def test_closed_schema_rejects_unknown_or_incomplete_input(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)
    manifest["conversation"] = "must not cross the boundary"

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="unknown fields"):
        stage_handoff.validate_manifest(store, manifest)

    manifest = _manifest(store)
    del manifest["requirement"]
    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="missing fields"):
        stage_handoff.validate_manifest(store, manifest)


@pytest.mark.parametrize("outcome", ["closed", "discarded"])
def test_explicit_nonconsumable_reuse_survives_shared_store_and_read(
        tmp_path, outcome: str) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(
        store, producer_outcome=outcome, allow_nonconsumable_reuse=True)

    assert manifest["authorization"]["nonconsumable_reuse"] == {
        "schema": "taskplane.nonconsumable-reuse-authorization/v1",
        "producer_outcome": outcome,
        "authority_fingerprint": "a" * 64,
    }
    reference = review_evidence.store_stage_handoff(store, manifest)
    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="cannot be consumed by default"):
        review_evidence.read_stage_handoff(
            store, reference, expected_authority_revision=7,
            expected_authority_fingerprint="a" * 64)
    assert review_evidence.read_stage_handoff(
        store, reference, expected_authority_revision=7,
        expected_authority_fingerprint="a" * 64,
        allow_nonconsumable_reuse=True) == manifest


def test_new_module_constants_have_pinned_quality_annotations() -> None:
    expected = {
        "SCHEMA", "MAX_MANIFEST_BYTES", "MAX_ARTIFACT_REFERENCES",
        "TERMINAL_OUTCOMES", "REQUIRED_EXCLUSIONS", "_IDENTIFIER",
        "_REPOSITORY_ID", "_FINGERPRINT", "_COMMIT", "_CONTRACT",
        "_MANIFEST_FIELDS", "_AUTHORITY_FIELDS",
        "_AUTHORITY_RECORD_FIELDS", "_NONCONSUMABLE_REUSE_FIELDS",
    }

    assert expected <= set(stage_handoff.__annotations__)
