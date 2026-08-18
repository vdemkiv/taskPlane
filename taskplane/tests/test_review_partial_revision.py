import os
import sys

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
sys.path.insert(0, os.path.join(ROOT, "taskplane", "tests"))

import review  # noqa: E402
import review_evidence  # noqa: E402
import runtime_eval  # noqa: E402


def _lease(slot):
    return {
        "schema": "taskplane.slot-lease/v1",
        "lease_fingerprint": f"lease-{slot}",
        "slot_id": slot,
        "lens_ids": [slot],
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
        "view_fingerprint": f"view-{slot}",
        "canonical_revision": 1,
    }


def _finding(slot, title="shared defect"):
    return {
        "lens": slot, "kind": "defect", "severity": "high",
        "class": "regression", "file": "src/app.py", "line": 10,
        "title": title, "scenario": "the changed request loses data",
        "fix": "preserve the request data",
    }


def _records(tmp_path, slots=("security", "frontend", "quality")):
    store = review_evidence.ArtifactStore(str(tmp_path))
    lease_refs, result_refs = [], []
    for slot in slots:
        lease = _lease(slot)
        lease_ref = store.put("lease", lease)
        result = {
            "schema": "taskplane.slot-result/v1",
            **{key: lease[key] for key in (
                "lease_fingerprint", "slot_id", "lens_ids",
                "target_fingerprint", "context_fingerprint",
                "view_fingerprint", "canonical_revision")},
            "authored_by": "lens-slot",
            "source": f".em-review/results/{slot}.json",
            "findings": [_finding(slot)],
        }
        result["result_fingerprint"] = review_evidence.content_fingerprint(result)
        result_ref = store.put("slot-result", result,
                               fingerprint=result["result_fingerprint"])
        lease_refs.append(lease_ref)
        result_refs.append(result_ref)
    return store, lease_refs, result_refs


@pytest.mark.parametrize("valid_count", [0, 1, 2, 3])
def test_partial_collection_preserves_every_valid_slot_and_names_every_gap(
        tmp_path, valid_count):
    store, leases, results = _records(tmp_path)
    missing = [store.read(ref)["slot_id"] for ref in leases[valid_count:]]
    gaps = [{"slot_id": slot, "reason": "schema mismatch",
             "producer_task": f"producer-{slot}",
             "result_path": f".em-review/results/{slot}.json"}
            for slot in missing]

    collection = review_evidence.collect_partial_slot_results(
        store, leases, results[:valid_count], gaps=gaps)

    assert collection["status"] == (
        "complete" if valid_count == len(leases) else "incomplete")
    assert collection["collected_slot_ids"] == sorted(
        store.read(ref)["slot_id"] for ref in results[:valid_count])
    assert [row["slot_id"] for row in collection["gaps"]] == sorted(missing)
    assert collection["completeness"] == {
        "expected": 3, "collected": valid_count,
        "missing": 3 - valid_count, "complete": valid_count == 3,
    }


def test_provisional_revision_is_idempotent_and_supersedes_only_when_changed(
        tmp_path):
    store, leases, results = _records(tmp_path)
    envelope = store.put("envelope", {
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
    })
    gaps = [{"slot_id": "quality", "reason": "invalid summary"}]
    partial = review_evidence.collect_partial_slot_results(
        store, leases, results[:2], gaps=gaps)

    first = review.build_review_revision(
        store, envelope, partial, prior_provisional=None)
    replay = review.build_review_revision(
        store, envelope, partial, prior_provisional=first)
    changed = review_evidence.collect_partial_slot_results(
        store, leases, results[:1], gaps=[
            {"slot_id": "frontend", "reason": "invalid summary"},
            {"slot_id": "quality", "reason": "invalid summary"},
        ])
    successor = review.build_review_revision(
        store, envelope, changed, prior_provisional=first)

    assert first["artifact"]["fingerprint"] == replay["artifact"]["fingerprint"]
    assert replay["supersedes_provisional"] is None
    assert successor["supersedes_provisional"] == first["artifact"]["fingerprint"]
    assert first["approval"] == {"enabled": False,
                                  "reason": "review evidence is incomplete"}


def test_partial_revision_deduplicates_findings_without_losing_provenance(tmp_path):
    store, leases, results = _records(tmp_path, slots=("security", "frontend"))
    envelope = store.put("envelope", {
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
    })
    collection = review_evidence.collect_partial_slot_results(
        store, leases, results, gaps=[])
    revision = review.build_review_revision(store, envelope, collection)

    assert revision["disposition"] == "canonical"
    assert revision["approval"]["enabled"] is True
    assert len(revision["findings"]) == 1
    assert {row["slot_id"] for row in revision["findings"][0]["provenance"]} == {
        "security", "frontend"}


def test_machine_projection_exposes_incompleteness_without_finding_payload(tmp_path):
    store, leases, results = _records(tmp_path)
    envelope = store.put("envelope", {
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
    })
    collection = review_evidence.collect_partial_slot_results(
        store, leases, results[:1], gaps=[
            {"slot_id": "frontend", "reason": "schema mismatch"},
            {"slot_id": "quality", "reason": "missing result"},
        ])
    revision = review.build_review_revision(store, envelope, collection)

    projection = runtime_eval.review_revision_projection(revision)

    assert projection["status"] == "incomplete"
    assert projection["approval_enabled"] is False
    assert projection["finding_count"] == 1
    assert projection["gap_slot_ids"] == ["frontend", "quality"]
    assert "findings" not in projection


def test_gap_identity_rejects_duplicates_or_unknown_slots(tmp_path):
    store, leases, results = _records(tmp_path)
    with pytest.raises(review_evidence.ProvenanceError, match="duplicate gap"):
        review_evidence.collect_partial_slot_results(
            store, leases, results[:1], gaps=[
                {"slot_id": "frontend", "reason": "bad"},
                {"slot_id": "frontend", "reason": "bad again"},
                {"slot_id": "quality", "reason": "bad"},
            ])
    with pytest.raises(review_evidence.ProvenanceError, match="unexpected slot"):
        review_evidence.collect_partial_slot_results(
            store, leases, results, gaps=[
                {"slot_id": "unknown", "reason": "bad"}])


def test_collection_publishes_provisional_then_supersedes_it_after_repair():
    from test_review_routing import TestSelectiveReviewKernel

    fixture = TestSelectiveReviewKernel()
    fixture.setUp()
    started = fixture._start()
    fixture._write_slot_results(run_id=started["run_id"])
    state = review._load_state(fixture.ws, started["run_id"])
    broken_path = os.path.join(fixture.ws, state["slots"][0]["result_path"])
    with open(broken_path, encoding="utf-8") as stream:
        original = stream.read()
    with open(broken_path, "w", encoding="utf-8") as stream:
        stream.write(original + "\n")

    incomplete = review.collect_review(
        fixture.ws, publish=False, run_id=started["run_id"])
    replay = review.collect_review(
        fixture.ws, publish=False, run_id=started["run_id"])

    assert incomplete["status"] == "incomplete"
    assert incomplete["approval"]["enabled"] is False
    assert incomplete["completeness"]["collected"] == len(state["slots"]) - 1
    assert replay["findings"]["fingerprint"] == incomplete["findings"]["fingerprint"]
    assert review_evidence._read_current(
        review_evidence.ArtifactStore(fixture.ws)) is None
    with pytest.raises(review.ReviewKernelError,
                       match="collected canonical revision"):
        review.signoff_review(
            fixture.ws, decision="approve", by="human",
            run_id=started["run_id"])

    with open(broken_path, "w", encoding="utf-8") as stream:
        stream.write(original)
    completed = review.collect_review(
        fixture.ws, publish=False, run_id=started["run_id"])
    final_state = review._load_state(fixture.ws, started["run_id"])

    assert completed["status"] == "complete"
    assert final_state["revision"]["approval"]["enabled"] is True
    assert final_state["revision"]["supersedes_provisional"] == \
        incomplete["findings"]["fingerprint"]
