import copy

import pytest

import review
import review_evidence as evidence


def _slot(tmp_path):
    store = evidence.ArtifactStore(str(tmp_path))
    envelope = evidence.create_envelope(
        store, target={"fingerprint": "target-a", "head": "abc"},
        diff={"files": ["app.py"], "patch": "x" * 24_000},
        impact={"touched": ["app.py"], "detail": "i" * 12_000},
        graph_quality={"status": "complete", "detail": "g" * 12_000},
        runnability={"status": "available", "detail": "r" * 12_000},
        requirement={"id": "R-test", "text": "review " * 2_000},
        acceptance=["works"], contracts=["contract:test"],
        change={"type": "security"})
    view = evidence.create_scoped_view(
        store, envelope, slot_id="deep.security", lens_ids=["security"],
        relevant_files=["app.py"], canonical_revision=1,
        routing_fingerprint="route-a", producer="lens-slot")
    return store, envelope, view


def _recomputed_ref(store, view_ref, mutate):
    row = store.read(view_ref)
    mutate(row)
    base = {key: value for key, value in row.items()
            if key not in ("integrity", "view_fingerprint")}
    fingerprint = evidence.content_fingerprint(base)
    row["integrity"] = {"algorithm": "sha256", "fingerprint": fingerprint}
    row["view_fingerprint"] = fingerprint
    return store.put("view", row, fingerprint=fingerprint)


@pytest.mark.parametrize("mutation", [
    lambda row: row["inline_sections"].update(
        {"diff": {"files": ["safe.py"], "patch": "altered"}}),
    lambda row: row["inline_sections"].update(
        {next(iter(row["reference_manifest"]))["section"]: {}}),
    lambda row: row["reference_manifest"].append(
        copy.deepcopy(row["reference_manifest"][0])),
    lambda row: row["reference_manifest"].reverse(),
    lambda row: row["reference_manifest"].pop(),
    lambda row: row["omissions"].clear(),
    lambda row: row["omissions"].append({"section": "undeclared"}),
])
def test_recomputed_tampered_partition_is_rejected(tmp_path, mutation):
    store, envelope, view = _slot(tmp_path)
    bad = _recomputed_ref(store, view, mutation)
    with pytest.raises(evidence.ProvenanceError):
        review._verify_v3_view(store, envelope, bad)


def test_canonical_partition_is_complete_disjoint_and_verified(tmp_path):
    store, envelope, view_ref = _slot(tmp_path)
    view = review._verify_v3_view(store, envelope, view_ref)
    inline = set(view["inline_sections"])
    referenced = {row["section"] for row in view["reference_manifest"]}
    omitted = {row["section"] for row in view["omissions"]}
    assert inline.isdisjoint(referenced)
    assert inline | referenced == evidence.REVIEW_EVIDENCE_SECTIONS
    assert omitted == referenced


def test_recomputed_oversized_view_is_rejected_before_lease(tmp_path):
    store, envelope_ref, view_ref = _slot(tmp_path)
    envelope = store.read(envelope_ref)

    def inline_large_diff(row):
        row["inline_sections"]["diff"] = evidence.frame_review_evidence(
            "diff", envelope["diff"])
        row["reference_manifest"] = [
            item for item in row["reference_manifest"]
            if item["section"] != "diff"]
        row["reference_manifest_fingerprint"] = evidence.content_fingerprint(
            row["reference_manifest"])
        row["omissions"] = [item for item in row["omissions"]
                            if item["section"] != "diff"]

    oversized = _recomputed_ref(store, view_ref, inline_large_diff)
    assert len(evidence.canonical_bytes(store.read(oversized))) > \
        evidence.MAX_SCOPED_VIEW_BYTES
    with pytest.raises(evidence.ProvenanceError, match="byte bound"):
        review._verify_v3_view(store, envelope_ref, oversized)


def test_same_target_revision_cross_envelope_reference_is_rejected(tmp_path):
    store, first_envelope, first_view = _slot(tmp_path)
    second_envelope = evidence.create_envelope(
        store, target={"fingerprint": "target-a", "head": "def"},
        diff={"files": ["app.py"], "patch": "y" * 24_000},
        impact={"touched": ["app.py"], "detail": "i" * 12_000},
        graph_quality={"status": "complete", "detail": "g" * 12_000},
        runnability={"status": "available", "detail": "r" * 12_000},
        requirement={"id": "R-test", "text": "review " * 2_000},
        acceptance=["works"], contracts=["contract:test"],
        change={"type": "security"})
    second_view_ref = evidence.create_scoped_view(
        store, second_envelope, slot_id="deep.security",
        lens_ids=["security"], relevant_files=["app.py"],
        canonical_revision=1, routing_fingerprint="route-a",
        producer="lens-slot")
    second_view = store.read(second_view_ref)
    replacement = copy.deepcopy(next(
        item for item in second_view["reference_manifest"]
        if item["section"] == "diff"))

    def substitute_valid_other_context(row):
        row["reference_manifest"] = [
            replacement if item["section"] == "diff" else item
            for item in row["reference_manifest"]]
        row["reference_manifest_fingerprint"] = evidence.content_fingerprint(
            row["reference_manifest"])
        row["omissions"] = [{
            "section": item["section"],
            "reason": "referenced outside the bounded producer view",
            "bytes": item["content_bytes"],
            "digest": item["reference"]["digest"],
        } for item in row["reference_manifest"]]

    substituted = _recomputed_ref(
        store, first_view, substitute_valid_other_context)
    with pytest.raises(evidence.ProvenanceError, match="another context"):
        evidence.resolve_evidence_reference(
            store, replacement["reference"],
            target_fingerprint="target-a", canonical_revision=1,
            allowed_sections={"diff"},
            context_fingerprint=store.read(first_envelope)[
                "context_fingerprint"])
    with pytest.raises(evidence.ProvenanceError, match="another context"):
        review._verify_v3_view(store, first_envelope, substituted)
