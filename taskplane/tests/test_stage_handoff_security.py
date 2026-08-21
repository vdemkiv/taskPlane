"""Handoff validation fails closed at every untrusted artifact boundary."""
from __future__ import annotations

from collections.abc import Iterator
import copy

import pytest

from taskplane import review_evidence, stage_handoff
from taskplane.tests.test_stage_handoff import _manifest


def test_tampered_artifact_digest_and_byte_count_are_rejected(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)

    for field, replacement, message in (
        ("digest", "0" * 64, "digest"),
        ("bytes", 999999, "byte length"),
    ):
        tampered = copy.deepcopy(manifest)
        tampered["selected_artifacts"][0][field] = replacement
        tampered["fingerprint"] = stage_handoff.manifest_fingerprint(tampered)
        with pytest.raises(review_evidence.ArtifactIntegrityError,
                           match=message):
            stage_handoff.validate_manifest(store, tampered)


def test_host_paths_and_undeclared_context_cannot_enter_manifest(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)
    reference = manifest["selected_artifacts"][0]
    reference["path"] = "/tmp/attacker-controlled.json"
    manifest["fingerprint"] = stage_handoff.manifest_fingerprint(manifest)

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="artifact reference.*unknown fields"):
        stage_handoff.validate_manifest(store, manifest)

    for forbidden in ("agents", "conversations", "event_logs", "tools",
                      "secrets", "approvals"):
        manifest = _manifest(store)
        manifest[forbidden] = ["injected"]
        manifest["fingerprint"] = stage_handoff.manifest_fingerprint(manifest)
        with pytest.raises(stage_handoff.HandoffValidationError,
                           match="unknown fields"):
            stage_handoff.validate_manifest(store, manifest)


def test_required_exclusions_are_explicit(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    exclusions = set(stage_handoff.REQUIRED_EXCLUSIONS)
    exclusions.remove("predecessor-conversations")

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="missing required exclusions"):
        _manifest(store, exclusions=sorted(exclusions))

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="evidence references are incomplete"):
        _manifest(store, evidence_references=[])


def test_reference_count_and_manifest_bytes_are_bounded(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    reference = store.put("delivery", {"value": 1})

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="at most 64"):
        _manifest(store, selected_artifacts=[reference] * 65)

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="65536 bytes"):
        _manifest(store, deliverables=[f"{index:04d}-" + "x" * 195
                                      for index in range(400)])


def test_reference_generator_stops_at_the_pre_materialization_bound(
        tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    reference = store.put("delivery", {"value": 1})
    pulled = 0

    def unbounded_references() -> Iterator[dict[str, object]]:
        nonlocal pulled
        while True:
            pulled += 1
            if pulled > stage_handoff.MAX_ARTIFACT_REFERENCES + 1:
                raise AssertionError("reference iterator was over-consumed")
            yield reference

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="at most 64"):
        _manifest(store, selected_artifacts=unbounded_references())
    # _manifest supplies one evidence reference, so the 64th selected
    # artifact is the 65th combined entry and triggers rejection immediately.
    assert pulled == stage_handoff.MAX_ARTIFACT_REFERENCES


def test_stale_authority_and_discarded_default_consumption_are_rejected(
        tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)

    with pytest.raises(stage_handoff.StaleAuthorityError,
                       match="authority revision"):
        stage_handoff.validate_manifest(
            store, manifest, expected_authority_revision=8)

    with pytest.raises(stage_handoff.HandoffValidationError,
                       match="discarded"):
        _manifest(store, producer_outcome="discarded")


def test_read_requires_matching_trusted_authority_fingerprint(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)
    reference = stage_handoff.store_manifest(store, manifest)

    with pytest.raises(TypeError, match="expected_authority_fingerprint"):
        stage_handoff.read_manifest(
            store, reference, expected_authority_revision=7)
    with pytest.raises(stage_handoff.StaleAuthorityError,
                       match="authority fingerprint"):
        stage_handoff.read_manifest(
            store, reference, expected_authority_revision=7,
            expected_authority_fingerprint="f" * 64)
    assert stage_handoff.read_manifest(
        store, reference, expected_authority_revision=7,
        expected_authority_fingerprint="a" * 64) == manifest


def test_manifest_fingerprint_tampering_is_rejected_before_use(tmp_path) -> None:
    store = review_evidence.ArtifactStore(str(tmp_path))
    manifest = _manifest(store)
    manifest["authorization"]["actor"] = "attacker"

    with pytest.raises(stage_handoff.HandoffIntegrityError,
                       match="fingerprint mismatch"):
        stage_handoff.validate_manifest(store, manifest)
