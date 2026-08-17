import copy

import pytest

import review
import review_evidence as evidence


def _envelope(store, *, payload_size=32_000):
    return evidence.create_envelope(
        store,
        target={"fingerprint": "target-a", "head": "abc"},
        diff={"files": ["app.py"], "changed_symbols": ["app.run"],
              "patch": "x" * payload_size},
        impact={"touched": ["app.py"], "total_impacted": 1},
        graph_quality={"status": "complete"},
        runnability={"status": "available"},
        requirement={"id": "R-test", "text": "review it"},
        acceptance=["works"], contracts=["contract:test"],
    )


def _v3_slot(store):
    envelope = _envelope(store)
    view = evidence.create_scoped_view(
        store, envelope, slot_id="deep.security", lens_ids=["security"],
        relevant_files=["app.py"], canonical_revision=1,
        routing_fingerprint="routing-a", producer="lens-slot")
    lease = review._create_verified_v3_lease(
        store, envelope, view, slot_id="deep.security",
        lens_ids=["security"], canonical_revision=1)
    return envelope, view, lease


def test_v3_reference_manifest_is_verified_before_lease(tmp_path):
    store = evidence.ArtifactStore(str(tmp_path))
    _, view_ref, lease_ref = _v3_slot(store)
    view = store.read(view_ref)
    lease = store.read(lease_ref)

    assert lease["view_fingerprint"] == view["view_fingerprint"]
    assert lease["reference_manifest_fingerprint"] == \
        view["reference_manifest_fingerprint"]
    assert lease["producer"] == "lens-slot"

    tampered = copy.deepcopy(view)
    tampered["reference_manifest"][0]["reference"]["digest"] = "0" * 64
    tampered_ref = store.put("view", tampered)
    with pytest.raises(evidence.ProvenanceError, match="(reference|view).*mismatch"):
        review._create_verified_v3_lease(
            store, _envelope(store), tampered_ref,
            slot_id="deep.security", lens_ids=["security"],
            canonical_revision=1)


@pytest.mark.parametrize("field", [
    "view_fingerprint", "reference_manifest_fingerprint", "lease_fingerprint",
    "target_fingerprint", "producer", "canonical_revision", "slot_id",
])
def test_collection_rejects_every_bound_identity(tmp_path, field):
    store = evidence.ArtifactStore(str(tmp_path))
    _, _, lease_ref = _v3_slot(store)
    lease = store.read(lease_ref)
    result_ref = evidence.write_slot_result(
        store, lease_ref, authored_slot=lease["slot_id"],
        lens_ids=lease["lens_ids"], findings=[])
    row = store.read(result_ref)
    row.update({key: lease[key] for key in (
        "reference_manifest_fingerprint", "producer")})
    row[field] = (row.get(field) + 1 if field == "canonical_revision"
                  else "tampered")
    bad_ref = store.put("slot-result", row)
    with pytest.raises(evidence.ProvenanceError):
        review._collect_verified_slot_results(store, [lease_ref], [bad_ref])


def test_nonzero_selected_slots_cannot_succeed_as_zero():
    with pytest.raises(review.ReviewKernelError, match="slot conservation"):
        review._assert_slot_conservation(
            selected=["security"], prepared=[], dispatched=[], collected=[])


def test_slot_conservation_requires_identical_identities():
    expected = ["deep.security", "light-sweep"]
    assert review._assert_slot_conservation(
        selected=expected, prepared=reversed(expected),
        dispatched=expected, collected=expected) == sorted(expected)
    with pytest.raises(review.ReviewKernelError, match="slot conservation"):
        review._assert_slot_conservation(
            selected=expected, prepared=expected,
            dispatched=["deep.security"], collected=expected)
