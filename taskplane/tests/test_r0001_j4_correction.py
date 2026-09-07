"""J4 partial production review/recovery coverage with simulated host authority.

The canonical corrected-candidate journey is not implemented here: current
phase continuation has no correction-grant/selection transition connecting
these review and recovery owners. These bounded checks do not claim that gap
closed, independent Engineering review, or native completion.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from taskplane import loop, loop_recovery, review_evidence
from taskplane.tests.test_r0001_orchestrator_authority import setup
from taskplane.tests.test_r0001_lease_retry import _owner


def _review(store, inputs, findings):
    result = inputs.runtime_receipt["payload"]
    envelope = review_evidence.create_envelope(store,
        target={"fingerprint": result["candidate_fingerprint"], "head": inputs.freshness["candidate_sha"]},
        diff={"files": ["taskplane/loop.py"]}, impact={},
        graph_quality={"status": "complete"}, runnability={},
        requirement={"id": "R-0001"}, acceptance=["FP-AC15"], contracts=[],
        change={"phase_result_fingerprint": result["fingerprint"],
                "definition_set_fingerprint": result["definition_set_fingerprint"]})
    view = review_evidence.create_scoped_view(store, envelope, slot_id="direct", lens_ids=["direct-evidence"])
    lease = review_evidence.create_slot_lease(store, envelope, view, slot_id="direct", lens_ids=["direct-evidence"])
    output = review_evidence.write_slot_result(store, lease, authored_slot="direct",
        lens_ids=["direct-evidence"], findings=findings,
        lens_results=[{"lens": "direct-evidence", "verdict": "pass", "checked_evidence": [
            {"file": "taskplane/loop.py", "line": 1, "claim": "Simulated review of the current result"}]}])
    collection = review_evidence.collect_slot_results(store, [lease], [output])
    revision = review_evidence.commit_revision(store, envelope, collection)
    return {"revision": revision, "envelope": envelope, "leases": [lease], "results": [output]}


@pytest.mark.parametrize("case", ("pass_with_blocker", "missing_authority", "stale_corrected_candidate", "missing_evidence"))
def test_current_collection_and_gate_refuse_one_severed_review_boundary(tmp_path, monkeypatch, record_property, case):
    inputs, registry, store, revision, ports, events = setup(tmp_path, monkeypatch)
    positive = loop.continue_phase_result(inputs, registry, store, revision, ports)
    assert positive["continuation"]["kind"] == "advance"
    original = Path(revision["results"][0]["path"]).read_bytes()
    before = list(events)
    if case == "pass_with_blocker":
        revision = _review(store, inputs, [{"class": "regression", "severity": "low",
            "title": "The reported pass retains an unresolved defect", "file": "taskplane/loop.py"}])
    elif case == "missing_authority":
        ports = replace(ports, gate=lambda definition, reviewed: None)
    elif case == "stale_corrected_candidate":
        inputs = replace(inputs, freshness={**inputs.freshness, "candidate_sha": "9" * 40})
    else:
        Path(revision["results"][0]["path"]).unlink()
    with pytest.raises((ValueError, PermissionError, OSError)):
        loop.continue_phase_result(inputs, registry, store, revision, ports)
    assert events[len(before):].count("knowledge") == 0
    if case != "missing_evidence":
        # Prior results are retained; the new review never overwrites them.
        assert any(Path(ref["path"]).read_bytes() == original for ref in store.references("slot-result"))
    record_property("evidence_mode", "simulated-review-and-authority-actual-collection-continuation")
    record_property("coverage_limit", "no corrected-candidate transition or correction authorization producer")


def test_repeated_actual_review_failure_stops_the_same_attempt(tmp_path, monkeypatch, record_property):
    inputs, _, store, _, _, _ = setup(tmp_path / "review", monkeypatch)
    rejected = _review(store, inputs, [{"class": "regression", "severity": "low",
        "title": "Current candidate is still broken", "file": "taskplane/loop.py"}])
    sealed = review_evidence.sealed_current_revision(store, rejected["revision"])
    fingerprint = sealed["findings_fingerprint"]
    owner, lease, _, _, calls, _, _ = _owner(tmp_path / "recovery", monkeypatch)
    admitted = owner.admit(lease, expected_fence=0)
    owner.record_failure(admitted, "artifact", fingerprint)
    assert owner.pickup(admitted)["continuation"] == "artifact_correction"
    owner.record_failure(admitted, "artifact", fingerprint)
    pending = owner.pickup(admitted)
    assert pending["operation_id"] == admitted.operation_id
    assert pending["attempt_id"] == admitted.attempt_id
    with pytest.raises(loop_recovery.LeaseRefusal):
        owner.execute(admitted, nonce_action=lambda action: action(), action=lambda bound: calls.append("launch"))
    assert calls == []
    record_property("evidence_mode", "simulated-inputs-actual-review-fingerprint-and-recovery")
    record_property("coverage_limit", "shared recovery; not an end-to-end authorized correction")
