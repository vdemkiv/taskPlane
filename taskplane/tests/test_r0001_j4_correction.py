"""J4 classified review -> bounded correction -> current candidate admission.

Host work, test-domain source and scoped authorization are explicitly simulated;
the production runtime, immutable review, recovery and continuation owners run.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from taskplane import agent_runtime, delivery_ports, evaluation_output, failure_routing, loop, loop_recovery, review_evidence
from taskplane.tests import test_r0001_telemetry_seal as telemetry_fixture
from taskplane.tests import test_r0001_orchestrator_authority as continuation_fixture
from taskplane.tests.test_r0001_agent_runtime import _setup as runtime_setup
from taskplane.tests.test_stage_entities import _stage, _authority
from taskplane.tests.test_r0001_orchestrator_authority import setup
from taskplane.tests.test_r0001_lease_retry import _owner


def _corrected_journey(tmp_path, monkeypatch, case=None):
    initial, registry, store, _, ports, _ = setup(tmp_path / "initial", monkeypatch)
    rejected = _review(store, initial, [{"class": "regression", "severity": "low",
        "title": "The test-domain value is 1; the declared behavior requires 2", "file": "owned.py"}])
    with pytest.raises(ValueError, match="blockers"):
        loop.continue_phase_result(initial, registry, store, rejected, ports)
    sealed = review_evidence.sealed_current_revision(store, rejected["revision"])
    prior_bytes = Path(rejected["results"][0]["path"]).read_bytes()
    task = {"id": "correction-task"}
    workspace = str(tmp_path / "initial")
    monkeypatch.setattr(loop.tp, "git_head", lambda ws: "a" * 40)
    observed = {"review_revision": rejected["revision"], "findings_fingerprint": sealed["findings_fingerprint"]}
    failure = {"schema": failure_routing.FAILURE_RECORD_SCHEMA_ID, "id": "F-J4", "source": "review",
        "stage": "evaluate", "repro": "read current observed value", "evidence": observed,
        "evidence_digest": failure_routing.evidence_digest(observed), "class": "product",
        "reason": "current collected review proves the declared behavior missing", "owner": "bounded-owner",
        "cluster": "value", "route": "fix", "candidate": loop._failure_candidate_identity(workspace, task)}
    verdict = {"schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID, "task": task["id"], "requirement": "R-0001",
        "verdict": "fail", "evaluation": {"status": "complete", "reason_code": "none", "detail": "current defect"},
        "criteria": [{"criterion": "value is 2", "status": "not-met", "evidence": "F-J4"}],
        "graph": {"dispositions": [], "requirements_checked": [], "contracts_checked": []}, "failures": [failure]}
    evaluation_path = tmp_path / "initial" / "evaluation.json"
    evaluation_path.write_text(json.dumps(verdict))
    monkeypatch.setattr(loop.runtime_storage, "evaluation_path", lambda ws: str(evaluation_path))
    errors, _, routing = loop._evaluation_failure_routing(workspace, {}, task)
    assert errors == [] and routing["product_fix_allowed"] is True and routing["next"] == "fix"
    source = tmp_path / "owned.py"
    source.write_text("VALUE = 1\n")
    corrected_fingerprint = review_evidence.content_fingerprint({"source": "VALUE = 2\n", "failure": observed})
    correction_calls, refusals = [], []
    # These are initial inputs to a new runtime attempt. No collected result,
    # signature, Plan artifact or review result is rewritten by this fixture.
    def corrected_setup(root):
        runtime, dispatch, calls = runtime_setup(root)
        binding = {**dispatch.nonce_bindings, "candidate_fingerprint": corrected_fingerprint,
            "attempt_id": "corrected-attempt", "operation_id": "corrected-operation"}
        issued = runtime.nonce.issue(binding)
        dispatch = replace(dispatch, nonce_bindings=binding, issued=issued,
            bindings={**dispatch.bindings, "candidate_fingerprint": corrected_fingerprint,
                "attempt_id": binding["attempt_id"], "operation_id": binding["operation_id"],
                "nonce_digest": issued.receipt["nonce_digest"]})
        lease = delivery_ports.AttemptLease("correction-lease", "run-1", "build", "corrected-attempt",
            "corrected-operation", "bounded-owner", 100, 190, 180, ("workspace:owned.py",), 1)
        loop.save(str(root), {"run_id": "run-1", "step": "fix"})
        allowed = {"value": True}
        owner = loop_recovery.LeaseRecovery(str(root), mutate_state=loop.mutate, clock=runtime.clock,
            authorize=lambda current: current == lease and allowed["value"] and routing["product_fix_allowed"],
            usage=lambda: {"attempts": 0}, limits={"attempts": 3})
        owner.admit(lease, expected_fence=0)
        owner.record_failure(lease, "artifact", sealed["findings_fingerprint"])
        assert owner.pickup(lease)["continuation"] == "artifact_correction"
        if case == "unchanged_failure":
            owner.record_failure(lease, "artifact", sealed["findings_fingerprint"])
        elif case == "unbounded_correction":
            for index in range(3):
                owner.record_failure(lease, "artifact", review_evidence.content_fingerprint(index))
        elif case == "missing_authority":
            allowed["value"] = False
        launch, observe = runtime.launch, runtime.observe
        def effect(bound):
            correction_calls.append(bound.operation_id)
            source.write_text("VALUE = 2\n")
            return launch(dispatch.envelope, dispatch.package,
                agent_runtime.ToolBoundary(
                    lambda: runtime._budget(dispatch.bindings), registry=runtime.registry,
                    phase_id="build", run_id="run-1", capability=None))
        def corrected_launch(*args):
            try:
                return owner.execute(lease, nonce_action=lambda action: action(),
                    action=effect, paths=(delivery_ports.EffectPath.capture(source),))
            except loop_recovery.LeaseRefusal as exc:
                refusals.append(exc.result)
                raise
        runtime.launch = corrected_launch
        def collect(identity):
            observation = observe(identity)
            current_source = runtime.store.put("source", {"value": source.read_text()})
            stage = _stage(run_id="run-1", authority=_authority(run_id="run-1"),
                selected_artifacts=[review_evidence.portable_artifact_reference(runtime.store, current_source)])
            return replace(observation, outputs=(agent_runtime.Artifact("stage", "taskplane.stage/v1",
                runtime.store.put("stage", stage)),))
        runtime.observe = collect
        return runtime, dispatch, calls
    if case in {"unchanged_failure", "unbounded_correction", "missing_authority"}:
        runtime, dispatch, calls = corrected_setup(tmp_path / "corrected")
        refused = runtime.run(dispatch)
        assert refused["status"] == "refused"
        assert refused["evaluator_dispatch_eligibility"] is False
        assert [row["reason"] for row in refusals] == [{"unchanged_failure": "repeated_fingerprint",
            "unbounded_correction": "retry_budget_exhausted", "missing_authority": "authority_revoked"}[case]]
        assert calls == correction_calls == [] and source.read_text() == "VALUE = 1\n"
        return None
    def current_sources(root, patch, **kwargs):
        from taskplane import dispatch_telemetry as telemetry, stage_handoff
        runtime, dispatch, calls = corrected_setup(root)
        result = runtime.run(dispatch)
        assert result["status"] == "accepted" and calls == ["launch", "observe"]
        freshness = {**telemetry_fixture.FRESHNESS, "candidate_sha": "b" * 40}
        signed = stage_handoff.sign_contract(result, key=telemetry_fixture.KEY, issued_at=100,
            expires_at=1000, freshness=freshness)
        ledger = telemetry.new_ledger(run_id="run-1", source_sha="b" * 40,
            design_fingerprint="design", plan_fingerprint="plan", started_at=100)
        event = telemetry.dispatch_event(dispatch_id="corrected-attempt", thread_id="simulated-worker-1",
            thread_type="worker", task_id="build", sequence=1, kind="progress", at=105)
        telemetry.bind_dispatch(ledger, dict(dispatch_id="corrected-attempt", thread_id="simulated-worker-1",
            thread_type="worker", task_id="build", dependencies=[], shared_owner=None,
            started_at=100, ended_at=110, wait_duration_seconds=0, correction_count=1, events=[event]))
        telemetry.terminalize_unavailable(ledger, dispatch_id="corrected-attempt", ended_at=110,
            outcome="complete", reason="simulated-host-has-no-token-usage")
        return telemetry.AttemptTelemetryInputs(ledger=ledger, runtime_receipt=signed,
            nonce_source=runtime.nonce, nonce=dispatch.issued, nonce_bindings=dispatch.nonce_bindings,
            knowledge_proposals=(), knowledge_receipts=(), trusted_keys={telemetry_fixture.KEY.key_id: telemetry_fixture.KEY},
            freshness=freshness, now=120, event_deliveries=tuple(ledger["bindings"][0]["events"]))
    with monkeypatch.context() as changed:
        changed.setattr(continuation_fixture, "sources", current_sources)
        current, registry, current_store, revision, ports, events = setup(tmp_path / "corrected", changed)
    assert source.read_text() == "VALUE = 2\n"
    assert correction_calls == ["corrected-operation"]
    assert current.runtime_receipt["payload"]["candidate_fingerprint"] == corrected_fingerprint
    assert initial.runtime_receipt["payload"]["candidate_fingerprint"] != corrected_fingerprint
    if case == "stale_corrected_candidate":
        current = replace(current, freshness={**current.freshness, "candidate_sha": "a" * 40})
    elif case == "pass_with_blocker":
        revision = _review(current_store, current, [{"class": "regression", "severity": "low",
            "title": "Reported pass still has a current blocker", "file": "owned.py"}])
    if case is not None:
        with pytest.raises((ValueError, PermissionError), match="blockers" if case == "pass_with_blocker" else "freshness|candidate"):
            loop.continue_phase_result(current, registry, current_store, revision, ports)
        assert "knowledge" not in events
        return None
    accepted = loop.continue_phase_result(current, registry, current_store, revision, ports)
    assert accepted["continuation"]["kind"] == "advance"
    assert Path(rejected["results"][0]["path"]).read_bytes() == prior_bytes
    return corrected_fingerprint


def test_authorized_bounded_correction_admits_only_current_corrected_candidate(tmp_path, monkeypatch, record_property):
    assert _corrected_journey(tmp_path, monkeypatch)
    record_property("evidence_mode", "simulated-host-and-authority-actual-failure-routing-bounded-effect-runtime-review-continuation")


@pytest.mark.parametrize("case", ["pass_with_blocker", "unchanged_failure", "unbounded_correction", "stale_corrected_candidate", "missing_authority"])
def test_pass_with_blocker_or_unchanged_retry_is_refused_and_stops(tmp_path, monkeypatch, record_property, case):
    assert _corrected_journey(tmp_path, monkeypatch, case) is None
    record_property("evidence_mode", "simulated-host-and-authority-actual-failure-routing-bounded-effect-runtime-review-continuation")


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
    record_property("coverage_limit", "supporting single-boundary check; canonical correction is separate")


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
