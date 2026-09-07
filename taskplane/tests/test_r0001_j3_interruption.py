"""J3 actual phase packages, runtime admission, review and durable recovery.

Host launches/events and authority are simulated. Native delivery is not proven.
"""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from taskplane import agent_runtime, delivery_ports, loop_recovery, producer_observation, review_evidence
from taskplane.tests.test_r0001_phase_agents_spec import _journey, _run
from taskplane.tests.test_r0001_j2_quality_handoff import _build_stage
from taskplane import loop as phase_loop
from taskplane.tests.test_r0001_lease_retry import _owner
import loop


PHASES = ("product", "design", "plan", "build", "evaluate", "engineering", "retro")


class JourneyRefused(Exception):
    pass


def _phase_journey(tmp_path, driver):
    store, registry, state, _, predecessor, _ = _journey(tmp_path, driver=driver)
    for phase in PHASES[3:]:
        package = phase_loop.consume_phase_handoff(store, predecessor, registry=registry, phase_id=phase,
            expected_authority_revision=1, expected_authority_fingerprint="f" * 64,
            expected_run_id="run-t11", expected_candidate_fingerprint="a" * 64)
        authored = {"stage": _build_stage(package) if phase == "build" else package.read("stage")}
        predecessor, _ = _run(tmp_path, store, registry, phase, authored, predecessor, state=state, driver=driver)
    return predecessor


def _interruption_driver(tmp_path, monkeypatch, target, role, case, records):
    monkeypatch.setattr(loop, "_state_dir", lambda ws: str(tmp_path / "recovery-state"))

    def driver(runtime, dispatch):
        if dispatch.bindings["phase_id"] != target:
            return runtime.run(dispatch)
        # Owner setup uses the real phase definition and all real predecessor
        # artifacts. Reviewer setup consumes this exact completed result.
        result = runtime.run(dispatch) if role == "reviewer" else None
        prepared = runtime.prepare(dispatch)
        operation = dispatch.bindings["operation_id"] if role == "owner" else dispatch.bindings["operation_id"] + "-reviewer"
        attempt = dispatch.bindings["attempt_id"] if role == "owner" else operation
        lease = delivery_ports.AttemptLease(operation, "run-t11", target, attempt,
            operation, role, 100, 190, 180, ("host:" + role,), 1)
        if role == "owner":
            assert lease.operation_id == dispatch.nonce_bindings["operation_id"]
            assert lease.attempt_id == dispatch.nonce_bindings["attempt_id"]
        loop.save(str(tmp_path), {"run_id": "run-t11", "step": target})
        def fresh():
            return loop_recovery.LeaseRecovery(str(tmp_path), mutate_state=loop.mutate,
                clock=runtime.clock, authorize=lambda current: current == lease,
                usage=lambda: {"attempts": 0}, limits={"attempts": 3})
        owner = fresh()
        owner.admit(lease, expected_fence=0)
        owner.record_failure(lease, "setup", review_evidence.content_fingerprint(dispatch.bindings))
        assert fresh().pickup(lease)["continuation"] == "setup_repair"
        assert fresh().pickup(lease)["effects"] == {"host:" + role: "effect_free"}
        calls, output = [], {}
        reviewer = None
        if role == "reviewer":
            envelope = review_evidence.create_envelope(runtime.store,
                target={"fingerprint": result["candidate_fingerprint"]}, diff={}, impact={},
                graph_quality={}, runnability={}, requirement={"id": "R-T11"}, acceptance=["FP-AC14"],
                contracts=[], change={"phase_result_fingerprint": result["fingerprint"]})
            view = review_evidence.create_scoped_view(runtime.store, envelope, slot_id=operation, lens_ids=["direct-evidence"])
            reviewer = review_evidence.create_slot_lease(runtime.store, envelope, view, slot_id=operation, lens_ids=["direct-evidence"])

        def effect(bound):
            calls.append(bound.operation_id)
            if role == "reviewer":
                output["review"] = review_evidence.write_slot_result(runtime.store, reviewer,
                    authored_slot=operation, lens_ids=["direct-evidence"], findings=[],
                    lens_results=[{"lens": "direct-evidence", "verdict": "pass", "checked_evidence": [
                        {"file": "taskplane/loop.py", "line": 1, "claim": "Simulated review of actual phase output " + result["fingerprint"]}]}])
            else:
                output["identity"] = runtime.launch(dispatch.envelope, dispatch.package,
                    agent_runtime.ToolBoundary(lambda: runtime._budget(dispatch.bindings),
                        registry=runtime.registry, phase_id=target, run_id="run-t11", capability=None))
            raise Interrupted()

        def execute():
            return fresh().execute(lease, nonce_action=(lambda action: action()) if role == "reviewer" else
                lambda action: runtime.nonce.dispatch(dispatch.issued, dispatch.nonce_bindings, action), action=effect)
        if case == "cancellation":
            owner.cancel(lease)
            with pytest.raises(loop_recovery.LeaseRefusal):
                execute()
            assert calls == []
            assert fresh().pickup(lease)["continuation"] == "observe_wait_reconcile"
            records.append({"phase": target, "role": role, "launches": 0, "ready": False})
            raise JourneyRefused()
        if case == "partial_write":
            original = loop.save
            fired = []
            def crash_save(*args, **kwargs):
                if not fired:
                    fired.append(True)
                    raise Interrupted()
                return original(*args, **kwargs)
            with monkeypatch.context() as fault:
                fault.setattr(loop, "save", crash_save)
                with pytest.raises(Interrupted):
                    execute()
            assert calls == []
        with pytest.raises(Interrupted):
            execute()
        assert len(calls) == 1
        pending = fresh().pickup(lease)
        assert pending["operation_id"] == operation
        assert pending["effects"] == {"host:" + role: "uncertain"}
        assert pending["continuation"] == "observe_wait_reconcile"
        with pytest.raises(loop_recovery.LeaseRefusal):
            execute()
        if case == "out_of_order":
            foreign = delivery_ports.observe_lease_terminal(replace(lease, operation_id="foreign"),
                lambda current: {"terminal_identity": "simulated-foreign", "released": True,
                    "effects": {"host:" + role: "observed"}})
            with pytest.raises(loop_recovery.LeaseRefusal):
                fresh().reconcile(lease, foreign)
        runtime = replace(runtime, store=review_evidence.ArtifactStore(str(tmp_path / "artifacts")),
            nonce=producer_observation.AttemptNonceSource(delivery_ports.LocatorEvidenceStore(
                tmp_path / target, "repository", "run-t11"),
                key=b"k" * 32, clock=runtime.clock))
        if role == "reviewer":
            collection = review_evidence.collect_slot_results(runtime.store, [reviewer], [output["review"]])
            assert collection == review_evidence.collect_slot_results(runtime.store, [reviewer], [output["review"]])
            revision = review_evidence.commit_revision(runtime.store, envelope, collection)
            assert review_evidence.sealed_current_revision(runtime.store, revision)
        else:
            observation = runtime.observe(output["identity"])
            result = runtime.complete(prepared, observation)
            assert result["status"] == "accepted", result
            assert runtime.complete(prepared, observation) == result
        terminal = delivery_ports.observe_lease_terminal(lease, lambda bound: {
            "terminal_identity": "simulated-terminal-" + operation, "released": True,
            "effects": {"host:" + role: "observed"}})
        fresh().reconcile(lease, terminal)
        fresh().reconcile(lease, terminal)
        assert loop.load(str(tmp_path))["attempt_lease"]["released"] is True
        assert calls == [operation]
        records.append({"phase": target, "role": role, "launches": 1, "ready": True,
            "operation": operation, "result": result["fingerprint"]})
        return result
    return driver


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("role", ("owner", "reviewer"))
def test_owner_reviewer_phase_matrix_reconciles_interruption_and_replay(tmp_path, monkeypatch, record_property, phase, role):
    records = []
    _phase_journey(tmp_path, _interruption_driver(tmp_path, monkeypatch, phase, role, "interruption", records))
    assert records[0]["ready"] is True
    record_property("trace", records)
    record_property("evidence_mode", "simulated-host-and-authority-real-phase-producers-runtime-review-and-recovery")


@pytest.mark.parametrize("case", ("owner_interrupt", "reviewer_interrupt", "cancellation", "duplicate", "out_of_order", "partial_write"))
def test_faults_cancellation_and_duplicate_events_never_false_ready_or_relaunch(tmp_path, monkeypatch, record_property, case):
    records = []
    driver = _interruption_driver(tmp_path, monkeypatch, "build",
        "reviewer" if case == "reviewer_interrupt" else "owner", case, records)
    if case == "cancellation":
        with pytest.raises(JourneyRefused):
            _phase_journey(tmp_path, driver)
    else:
        _phase_journey(tmp_path, driver)
    assert records[0]["launches"] == (0 if case == "cancellation" else 1)
    record_property("trace", records)
    record_property("evidence_mode", "simulated-host-and-authority-real-phase-producers-runtime-review-and-recovery")


def _trace(tmp_path, monkeypatch, phase, role):
    owner, lease, runtime, dispatch, calls, authority, usage = _owner(tmp_path, monkeypatch)
    lease = replace(lease, phase_id=phase, owner=role,
        operation_id=phase + "-" + role, attempt_id=phase + "-" + role + "-attempt")
    binding = {**dispatch.nonce_bindings, "phase_id": phase,
        "operation_id": lease.operation_id, "attempt_id": lease.attempt_id}
    issued = runtime.nonce.issue(binding)
    lease = owner.admit(lease, expected_fence=0)

    def execute(reader, action=None):
        return reader.execute(lease, nonce_action=lambda effect: runtime.nonce.dispatch(issued, binding, effect),
            action=action or (lambda bound: calls.append(bound.operation_id) or "simulated-worker"))

    def fresh():
        runtime.nonce = producer_observation.AttemptNonceSource(
            delivery_ports.LocatorEvidenceStore(tmp_path, "repository", "run-1"),
            key=b"k" * 32, clock=runtime.clock)
        return loop_recovery.LeaseRecovery(str(tmp_path), mutate_state=loop.mutate,
            clock=runtime.clock, authorize=lambda bound: authority["valid"],
            usage=lambda: usage, limits={key: 10 for key in usage})

    return owner, lease, runtime, calls, execute, fresh


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("role", ("owner", "reviewer"))
def test_shared_recovery_matrix_preserves_operation_on_fresh_pickup(tmp_path, monkeypatch, record_property, phase, role):
    owner, lease, runtime, calls, execute, fresh = _trace(tmp_path, monkeypatch, phase, role)
    pickup = fresh().pickup(lease)
    assert pickup["continuation"] == "retry_same_attempt"
    assert pickup["effects"] == {"host:worker": "effect_free"}
    assert execute(owner) == "simulated-worker"
    restarted = fresh()
    pending = restarted.pickup(lease)
    assert pending["operation_id"] == lease.operation_id
    assert pending["attempt_id"] == lease.attempt_id
    assert pending["effects"] == {"host:worker": "uncertain"}
    assert pending["continuation"] == "observe_wait_reconcile"
    waiter = delivery_ports.RecordedEventWaiter([[]], runtime.clock)
    assert restarted.wait(lease, waiter) == ()
    assert waiter.invocations == [({"mechanism": "host_terminal_event", "operation_id": lease.operation_id}, (lease.operation_id,))]
    with pytest.raises(loop_recovery.LeaseRefusal, match="effects_require_reconciliation"):
        execute(restarted)
    terminal = delivery_ports.observe_lease_terminal(lease, lambda bound: {
        "terminal_identity": "simulated-terminal", "released": True, "effects": {"host:worker": "observed"}})
    restarted.reconcile(lease, terminal)
    restarted.reconcile(lease, terminal)
    assert fresh().pickup(lease)["effects"] == {"host:worker": "observed"}
    assert loop.load(str(tmp_path))["attempt_lease"]["released"] is True
    assert calls == [lease.operation_id]
    record_property("evidence_mode", "simulated-host-shared-production-lease-nonce-recovery")
    record_property("coverage_limit", "phase-specific owner/reviewer setup records not covered")


class Interrupted(BaseException):
    pass


@pytest.mark.parametrize("case", ("owner_interrupt", "reviewer_interrupt", "cancellation", "duplicate", "out_of_order", "partial_write"))
def test_shared_recovery_faults_hold_without_a_second_effect(tmp_path, monkeypatch, record_property, case):
    # The identical effect entrypoint succeeds before each independent fault.
    positive_root = tmp_path / "positive"
    owner, lease, _, calls, execute, _ = _trace(positive_root, monkeypatch, "build", "owner")
    assert execute(owner) == "simulated-worker"
    assert calls == [lease.operation_id]
    owner, lease, _, calls, execute, fresh = _trace(tmp_path / "fault", monkeypatch,
        "build", "reviewer" if case == "reviewer_interrupt" else "owner")
    if case == "cancellation":
        owner.cancel(lease)
    elif case == "partial_write":
        mutate = owner.mutate_state
        @contextmanager
        def interrupted_write(workspace):
            with mutate(workspace) as state:
                yield state
                raise Interrupted()
        owner.mutate_state = interrupted_write
        with pytest.raises(Interrupted):
            execute(owner)
        owner.mutate_state = mutate
        # Before atomic commit, replay repairs without a lost host effect.
        assert execute(fresh()) == "simulated-worker"
    elif case in {"owner_interrupt", "reviewer_interrupt"}:
        def interrupted_effect(bound):
            calls.append(bound.operation_id)
            raise Interrupted()
        with pytest.raises(Interrupted):
            execute(owner, interrupted_effect)
    else:
        assert execute(owner) == "simulated-worker"
        if case == "out_of_order":
            foreign = delivery_ports.observe_lease_terminal(replace(lease, operation_id="foreign"),
                lambda bound: {"terminal_identity": "simulated-foreign", "released": True,
                    "effects": {"host:worker": "observed"}})
            with pytest.raises(loop_recovery.LeaseRefusal):
                owner.reconcile(lease, foreign)
    before = list(calls)
    pending = fresh().pickup(lease)
    assert pending["continuation"] == "observe_wait_reconcile"
    with pytest.raises(loop_recovery.LeaseRefusal):
        execute(fresh())
    assert calls == before
    assert len(calls) == (0 if case == "cancellation" else 1)
    record_property("evidence_mode", "simulated-host-and-faults-real-durable-recovery")
