"""T12 local production composition, with simulated host/authority and faults.

The real registry, runtime, stage/artifact producers, nonce ledger and loop
lease persistence are exercised. These are not native or J0/J1/J6/W01-W34
journey claims. Fault wrappers invoke incumbent producers unchanged.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path

import pytest

import loop
from taskplane import build_c, delivery_ports as ports, loop_recovery as recovery
from taskplane import producer_observation, stage_entities
from taskplane.tests.test_r0001_lease_retry import _owner


def _build(tmp_path, monkeypatch):
    owner, lease, runtime, dispatch, calls, authority, usage = _owner(tmp_path, monkeypatch)
    admitted = owner.admit(lease, expected_fence=0)
    fences = []

    def launch(bound, envelope, package, boundary):
        # Read actual durable producer state at the external-effect boundary.
        assert loop.load(str(tmp_path))["attempt_lease"]["effects"] == {"host:worker": "uncertain"}
        nonce_state = json.loads(runtime.nonce._path.read_text())
        assert nonce_state["attempts"][bound.operation_id]["effect_state"] == "dispatch_uncertain"
        fences.append((bound.attempt_id, bound.operation_id, bound.fencing_token))
        return runtime.launch(envelope, package, boundary)

    def terminal(bound):
        return ports.observe_lease_terminal(bound, lambda lease: {
            "terminal_identity": "simulated-stop-1", "released": True,
            "effects": {"host:worker": "observed"}})

    options = dict(lease_owner=owner, lease=admitted, launch=launch, observe_terminal=terminal)
    return runtime, dispatch, options, calls, fences, authority, usage


def _run(runtime, dispatch, options):
    result = build_c.run_build_phase(runtime, dispatch, **options)
    assert stage_entities.validate_contract(result) == result
    return result


@pytest.mark.parametrize("case", ["connected", "missing-lease", "stale-fence", "foreign-run",
    "foreign-phase", "foreign-attempt", "foreign-operation", "unadmitted-lease"], ids=str)
def test_build_effect_requires_fence(tmp_path, monkeypatch, record_property, case):
    runtime, dispatch, options, calls, fences, _, _ = _build(tmp_path, monkeypatch)
    if case == "missing-lease":
        options["lease"] = None
    elif case == "stale-fence":
        options["lease"] = replace(options["lease"], fencing_token=2)
    elif case.startswith("foreign-"):
        field = {"foreign-run": "run_id", "foreign-phase": "phase_id",
            "foreign-attempt": "attempt_id", "foreign-operation": "operation_id"}[case]
        options["lease"] = replace(options["lease"], **{field: "foreign"})
    elif case == "unadmitted-lease":
        with loop.mutate(str(tmp_path)) as state:
            state.pop("attempt_lease")
    result = _run(runtime, dispatch, options)
    if case == "connected":
        assert result["status"] == "accepted"
        assert result["effect_state"] == "reconciled"
        assert result["continuation"] == {"kind": "evaluate", "phase_id": "build"}
        assert fences == [("attempt-1", "op-1", 1)]
        assert calls == ["launch", "observe"]
        assert loop.load(str(tmp_path))["attempt_lease"]["released"] is True
        assert result["collected_output_references"]
    else:
        assert result["status"] == "refused"
        assert result["evaluator_dispatch_eligibility"] is False
        assert calls == fences == []
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")
    record_property("producer_reference", dispatch.package[0].reference["fingerprint"])
    record_property("case_id", case)


@pytest.mark.parametrize("case", ["connected", "revoked", "cancelled", "deadline", "heartbeat",
    "tokens", "wall_ms", "attempts", "corrections", "heartbeats", "telemetry",
    "repeated-failure", "nonce-disabled", "symlink-swap", "sever-package-bytes",
    "definition-changed", "knowledge-changed"], ids=str)
def test_build_pre_effect_revalidation(tmp_path, monkeypatch, record_property, case):
    runtime, dispatch, options, calls, fences, authority, usage = _build(tmp_path, monkeypatch)
    target = tmp_path / "input"
    target.write_text("bound input")
    options["paths"] = (ports.EffectPath.capture(target),)
    original_dispatch = runtime.nonce.dispatch

    def at_boundary(issued, binding, action):
        # Fault after the runtime's optimistic input checks; the actual nonce
        # producer still reserves the operation before calling the adapter.
        if case == "revoked":
            authority["valid"] = False
        elif case == "cancelled":
            options["lease_owner"].cancel(options["lease"])
        elif case in {"deadline", "heartbeat"}:
            runtime.clock.advance(50 if case == "deadline" else 40)
        elif case in usage:
            usage[case] = 10
        elif case == "repeated-failure":
            for _ in range(2):
                options["lease_owner"].record_failure(options["lease"], "artifact", "a" * 64)
        elif case == "nonce-disabled":
            runtime.nonce.disable_key()
        elif case == "symlink-swap":
            other = tmp_path / "other"
            other.write_text("other")
            target.unlink()
            target.symlink_to(other)
        elif case == "sever-package-bytes":
            ref = dispatch.package[0].reference
            (Path(runtime.store.root) / ref["kind"] / (ref["fingerprint"] + ".json")).write_bytes(b"{}")
        elif case == "definition-changed":
            runtime.registry = None
        elif case == "knowledge-changed":
            # Mutable original envelope is part of the sealed package.
            dispatch.envelope["role_instructions"] = "changed sealed instructions"
        return original_dispatch(issued, binding, action)

    monkeypatch.setattr(runtime.nonce, "dispatch", at_boundary)
    result = _run(runtime, dispatch, options)
    assert result["status"] == ("accepted" if case == "connected" else "refused")
    assert calls == (["launch", "observe"] if case == "connected" else [])
    assert len(fences) == (1 if case == "connected" else 0)
    if case != "connected":
        assert result["evaluator_dispatch_eligibility"] is False
        assert result["continuation"]["kind"] == "hold"
    record_property("producer_edge", "build_c.run_build_phase -> fenced host launch")
    record_property("case_id", case)


class ProcessLoss(BaseException):
    """Uncaught process-loss simulation; no product handler can repair it."""


@pytest.mark.parametrize("case", ["before-nonce", "after-nonce", "before-lease-save",
    "after-lease-save", "before-host", "after-host", "during-observation",
    "after-reconciliation"], ids=str)
def test_build_effect_crash_boundaries(tmp_path, monkeypatch, record_property, case):
    runtime, dispatch, options, calls, fences, _, _ = _build(tmp_path, monkeypatch)
    original_dispatch = runtime.nonce.dispatch
    original_mutate = options["lease_owner"].mutate_state
    original_launch, original_observe = options["launch"], runtime.observe

    def nonce_fault(issued, bindings, action):
        if case == "before-nonce":
            raise ProcessLoss()
        def callback():
            if case == "after-nonce":
                raise ProcessLoss()
            return action()
        return original_dispatch(issued, bindings, callback)

    @contextmanager
    def save_fault(workspace):
        changed = False
        with original_mutate(workspace) as state:
            before = dict(state["attempt_lease"]["effects"])
            yield state
            changed = before != state["attempt_lease"]["effects"]
            if changed and case == "before-lease-save":
                raise ProcessLoss()
        if changed and case == "after-lease-save":
            raise ProcessLoss()

    def launch_fault(*args):
        if case == "before-host":
            raise ProcessLoss()
        identity = original_launch(*args)
        if case == "after-host":
            raise ProcessLoss()
        return identity

    def observe_fault(identity):
        if case == "during-observation":
            raise ProcessLoss()
        return original_observe(identity)

    original_reconcile = options["lease_owner"].reconcile
    def reconcile_fault(*args):
        original_reconcile(*args)
        if case == "after-reconciliation":
            raise ProcessLoss()

    with monkeypatch.context() as faults:
        faults.setattr(runtime.nonce, "dispatch", nonce_fault)
        faults.setattr(options["lease_owner"], "mutate_state", save_fault)
        faults.setattr(options["lease_owner"], "reconcile", reconcile_fault)
        faults.setattr(runtime, "observe", observe_fault)
        with pytest.raises(ProcessLoss):
            _run(runtime, dispatch, {**options, "launch": launch_fault})

    # A fresh nonce/lease reader sees only incumbent durable records.
    runtime.nonce = producer_observation.AttemptNonceSource(
        ports.LocatorEvidenceStore(tmp_path, "repository", "run-1"), key=b"k" * 32, clock=runtime.clock)
    options["lease_owner"] = recovery.LeaseRecovery(str(tmp_path), mutate_state=loop.mutate,
        clock=runtime.clock, authorize=lambda lease: True,
        usage=lambda: {"tokens": 0}, limits={"tokens": 10})
    before = list(calls)
    result = _run(runtime, dispatch, options)
    if case == "before-nonce":
        assert result["status"] == "accepted"
        assert calls == ["launch", "observe"]
    else:
        assert result["status"] == "refused"
        assert calls == before
        assert result["retry_class"] == "reconcile"
        assert result["continuation"]["kind"] == "hold"
    assert len(fences) <= 1
    record_property("case_id", case)
    record_property("evidence_mode", "local-persistence-with-simulated-process-loss")


@pytest.mark.parametrize("case", ["missing-terminal", "unreleased", "uncertain", "pending",
    "foreign-operation", "different-terminal", "observation-unavailable", "launch-error",
    "false-effect-free", "missing-output"], ids=str)
def test_build_uncertain_effect_holds_for_reconciliation(tmp_path, monkeypatch, record_property, case):
    runtime, dispatch, options, calls, fences, _, _ = _build(tmp_path, monkeypatch)
    terminal = options["observe_terminal"]
    def incomplete(bound):
        value = terminal(bound)
        if case == "missing-terminal":
            return None
        if case == "unreleased":
            return replace(value, released=False)
        if case in {"uncertain", "pending"}:
            return replace(value, effects={"host:worker": case})
        if case == "foreign-operation":
            return replace(value, lease=replace(bound, operation_id="other"))
        if case == "different-terminal":
            return replace(value, terminal_identity="other-terminal")
        if case == "observation-unavailable":
            raise OSError("private host diagnostics")
        return value
    options["observe_terminal"] = incomplete
    if case == "launch-error":
        original = options["launch"]
        def lost(*args):
            original(*args)
            raise OSError("private host diagnostics")
        options["launch"] = lost
    elif case in {"false-effect-free", "missing-output"}:
        original = runtime.observe
        runtime.observe = lambda identity: replace(original(identity), **(
            {"effect_state": "uncertain"} if case == "false-effect-free" else {"outputs": ()}))
    result = _run(runtime, dispatch, options)
    assert result["status"] == "refused"
    assert result["evaluator_dispatch_eligibility"] is False
    assert result["effect_state"] == ("reconciled" if case == "missing-output" else "uncertain")
    assert result["continuation"]["kind"] == "hold"
    assert "private host diagnostics" not in str(result)
    before = list(calls)
    again = _run(runtime, dispatch, options)
    assert again["status"] == "refused"
    assert calls == before
    assert fences == [("attempt-1", "op-1", 1)]
    assert again["attempt_id"] == result["attempt_id"]
    assert again["operation_id"] == result["operation_id"]
    record_property("case_id", case)
