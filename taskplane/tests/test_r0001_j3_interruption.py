"""J3 shared recovery coverage: simulated host/authority, real durable owners.

This exercises the shared lease/nonce entrypoints for each phase and role.
It does not establish complete phase owner/reviewer setup-record reconciliation
or native event delivery. Those canonical journey selectors remain outstanding.
"""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from taskplane import delivery_ports, loop_recovery, producer_observation
from taskplane.tests.test_r0001_lease_retry import _owner
import loop


PHASES = ("product", "design", "plan", "build", "evaluate", "engineering", "retro")


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
