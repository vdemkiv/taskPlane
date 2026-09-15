import copy
import hashlib
import json
from pathlib import Path

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


def test_native_lens_brief_resolves_overflow_without_reconstructing_paths(tmp_path):
    import lens
    store = evidence.ArtifactStore(str(tmp_path))
    routing = {"lenses": [dict(row, tier="sweep" if row["id"] == "architecture" else "n/a",
                              evidence=["selected fixture"], negative_evidence=["outside fixture"])
                          for row in lens.load_catalog()["lenses"]]}
    plan = review.prepare_lens_plan(store, _envelope(store), routing, phase="design",
        binding={"run_id": "fixture", "operation_id": "design-overflow"})
    slot = store.read(plan)["slots"][0]
    brief = store.read(slot["brief"])
    view = store.read(slot["view"])
    reads = {row["section"]: row["reference"] for row in brief["evidence_reads"]}
    assert reads and len(reads) == len(view["reference_manifest"])
    for row in view["reference_manifest"]:
        portable = row["reference"]["artifact"]
        native = reads[row["section"]]
        assert "path" not in portable and "relative_path" not in portable
        assert not Path(native["relative_path"]).is_absolute()
        assert {key: value for key, value in native.items() if key != "relative_path"} == portable
        # Simulate the worker's allowed file read using only its delivered brief.
        raw = (tmp_path / native["relative_path"]).read_bytes()
        assert len(raw) == native["bytes"]
        assert hashlib.sha256(raw).hexdigest() == native["digest"]
        record = json.loads(raw)
        assert record["content"] == evidence.resolve_evidence_reference(
            store, row["reference"], target_fingerprint=view["target_fingerprint"],
            context_fingerprint=view["context_fingerprint"],
            canonical_revision=view["canonical_revision"], allowed_sections={row["section"]})
    assert "evidence_reads" in brief["prompt"]


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


def test_all_26_lenses_share_protocol_and_preserve_full_results(tmp_path):
    import lens
    from taskplane.tests.phase_fixture import write_lens_results
    store = evidence.ArtifactStore(str(tmp_path))
    routing = {"lenses": [dict(row, tier="sweep", evidence=["catalog conservation fixture"])
        for row in lens.load_catalog()["lenses"]]}
    plan = review.prepare_lens_plan(store, _envelope(store), routing, phase="review",
        binding={"run_id": "fixture", "operation_id": "review-one"})
    slots = store.read(plan)["slots"]
    assert len(slots) == len({slot["result_path"] for slot in slots}) == 26
    briefs = [store.read(slot["brief"]) for slot in slots]
    assert len({evidence.content_fingerprint(brief["result_schema"]) for brief in briefs}) == 1
    assert {brief["methodology"]["id"] for brief in briefs} == {row["id"] for row in routing["lenses"]}
    write_lens_results(store, plan, findings=[{"kind": "note", "severity": "info", "class": "observation",
        "file": "app.py", "line": 1, "title": "fixture note", "scenario": "retained limitation", "fix": "fixture only"}])
    collected = review.collect_lens_plan(store, plan)
    value = store.read(collected["collection"])
    assert collected["status"] == "complete"
    assert len(collected["validations"]) == len(value["results"]) == 26
    assert all(row["notes"] and row["lens_results"][0]["checked_evidence"] for row in value["results"])
    assert review.collect_lens_plan(store, plan) == collected


def test_same_input_parallel_phase_plans_cannot_share_results(tmp_path):
    import lens
    from taskplane.tests.phase_fixture import write_lens_results
    store = evidence.ArtifactStore(str(tmp_path))
    routing = {"lenses": [dict(row, tier="sweep" if row["id"] == "security" else "n/a",
        evidence=["selected fixture"], negative_evidence=["not selected"])
        for row in lens.load_catalog()["lenses"]]}
    envelope = _envelope(store)
    plans = [review.prepare_lens_plan(store, envelope, routing, phase=phase,
        binding={"run_id": "fixture", "operation_id": phase}) for phase in ("design", "plan")]
    write_lens_results(store, plans[0])
    assert review.collect_lens_plan(store, plans[0])["status"] == "complete"
    assert review.collect_lens_plan(store, plans[1])["status"] == "incomplete"
    slots = [store.read(plan)["slots"][0] for plan in plans]
    from pathlib import Path
    Path(tmp_path / slots[1]["result_path"]).write_bytes(Path(tmp_path / slots[0]["result_path"]).read_bytes())
    value = store.read(review.collect_lens_plan(store, plans[1])["collection"])
    assert value["status"] == "incomplete" and "lease" in value["gaps"][0]["reason"]
