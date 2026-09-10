"""Simulated host observations; real loop persistence, nonce and recovery ports."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from taskplane import delivery_ports as ports, loop_recovery as recovery
from taskplane.tests.test_r0001_agent_runtime import _setup
import loop


def _owner(tmp_path, monkeypatch):
    import subprocess
    from taskplane import run_store, storage
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    identity = storage.resolve_repository_identity(str(tmp_path))
    store = run_store.RunStore()
    store.create(identity, run_id="run-1", checkout=str(tmp_path),
        host={"kind": "simulated", "session_id": "lease-test"}, target={"kind": "workspace"})
    storage.write_workspace_locator(str(tmp_path), identity=identity,
        layout=storage.resolve_layout(identity, home=store.home, run_id="run-1"), run_id="run-1")
    loop.save(str(tmp_path), {"run_id": "run-1", "step": "execute"})
    runtime, dispatch, calls = _setup(tmp_path)
    authority = {"valid": True}
    usage = {"tokens": 0, "wall_ms": 0, "attempts": 0, "corrections": 0,
             "heartbeats": 0, "telemetry": 0}
    owner = recovery.LeaseRecovery(str(tmp_path), mutate_state=loop.mutate,
        clock=runtime.clock, authorize=lambda lease: authority["valid"],
        usage=lambda: usage, limits={key: 10 for key in usage})
    lease = ports.AttemptLease("lease-1", "run-1", "build", "attempt-1", "op-1",
        "owner-1", 100, 150, 140, ("host:worker",), 1)
    return owner, lease, runtime, dispatch, calls, authority, usage


def _dispatch(runtime, dispatch):
    return lambda action: runtime.nonce.dispatch(dispatch.issued, dispatch.nonce_bindings, action)


@pytest.mark.parametrize("case", ["connected", "concurrent-cas", "stale-cas", "changed-input", "foreign-run", "restart"])
def test_stale_lease_admission_cas(tmp_path, monkeypatch, case):
    owner, lease, runtime, dispatch, calls, _, _ = _owner(tmp_path, monkeypatch)
    admitted = owner.admit(lease, expected_fence=0)
    if case == "connected":
        assert owner.admit(lease, expected_fence=1) == admitted
    elif case == "concurrent-cas":
        def renew(expiry):
            try:
                return owner.admit(replace(lease, fencing_token=2, expires_at=expiry), expected_fence=1)
            except recovery.LeaseRefusal as exc:
                return exc.result["reason"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(renew, (160, 170)))
        assert results.count("lease_cas_conflict") == 1
        admitted = next(value for value in results if isinstance(value, ports.AttemptLease))
    elif case == "restart":
        owner = recovery.LeaseRecovery(str(tmp_path), mutate_state=loop.mutate,
            clock=runtime.clock, authorize=lambda lease: True,
            usage=lambda: {"tokens": 0}, limits={"tokens": 10})
        assert owner.admit(lease, expected_fence=1) == admitted
    else:
        proposal = replace(lease, owner="other") if case == "changed-input" else lease
        if case == "foreign-run":
            proposal = replace(lease, run_id="foreign")
        with pytest.raises(recovery.LeaseRefusal):
            owner.admit(proposal, expected_fence=0 if case == "stale-cas" else 1)
    assert calls == []
    assert owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch),
        action=lambda bound: calls.append(bound.attempt_id) or "started") == "started"
    assert calls == ["attempt-1"]


@pytest.mark.parametrize("case", ["connected", "sever-recovery-output", "uncertain", "deadline", "heartbeat", "historic"])
def test_effect_free_expired_lease_reclaim(tmp_path, monkeypatch, case):
    owner, lease, runtime, dispatch, calls, _, _ = _owner(tmp_path, monkeypatch)
    admitted = owner.admit(lease, expected_fence=0)
    if case == "uncertain":
        owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch), action=lambda bound: calls.append("launch"))
    if case == "historic":
        with loop.mutate(str(tmp_path)) as state:
            state["attempt_lease"].pop("effects")
    runtime.clock.advance(51 if case not in {"deadline", "heartbeat"} else 40)
    renewed = replace(lease, issued_at=151, expires_at=190, heartbeat_deadline=180, fencing_token=2)
    if case in {"uncertain", "historic"}:
        with pytest.raises(recovery.LeaseRefusal) as caught:
            owner.admit(renewed, expected_fence=1)
        assert caught.value.result["continuation"] == "observe_wait_reconcile"
        assert caught.value.result["effects"]["host:worker"] == "uncertain"
    elif case in {"deadline", "heartbeat"}:
        if case == "deadline":
            runtime.clock.advance(10)
        with pytest.raises(recovery.LeaseRefusal):
            owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch), action=lambda bound: calls.append("launch"))
    else:
        admitted = owner.admit(renewed, expected_fence=1)
        assert admitted.attempt_id == lease.attempt_id
        if case == "sever-recovery-output":
            admitted = replace(admitted, fencing_token=1)
            with pytest.raises(recovery.LeaseRefusal):
                owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch), action=lambda bound: calls.append("launch"))
        else:
            owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch), action=lambda bound: calls.append("launch"))
    assert calls == (["launch"] if case in {"connected", "uncertain"} else [])


@pytest.mark.parametrize("case", ["connected", "sever-delivery-output", "missing-terminal", "not-released", "pending", "uncertain", "foreign", "stale-fence", "cancel"])
def test_replacement_requires_terminal_released_receipt(tmp_path, monkeypatch, case):
    owner, lease, runtime, dispatch, calls, _, _ = _owner(tmp_path, monkeypatch)
    admitted = owner.admit(lease, expected_fence=0)
    owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch), action=lambda bound: calls.append("launch"))
    if case == "cancel":
        owner.cancel(admitted)
    observation = ports.observe_lease_terminal(admitted, lambda bound: {
        "terminal_identity": "simulated-stop-1", "released": True,
        "effects": {"host:worker": "observed"}})
    if case == "sever-delivery-output":
        observation = None
    elif case == "missing-terminal":
        observation = replace(observation, terminal_identity=None)
    elif case == "not-released":
        observation = replace(observation, released=False)
    elif case in {"pending", "uncertain"}:
        observation = replace(observation, effects={"host:worker": case})
    elif case == "foreign":
        observation = replace(observation, lease=replace(lease, operation_id="other"))
    replacement = replace(lease, lease_id="lease-2", attempt_id="attempt-2", operation_id="op-2", fencing_token=2)
    if case == "stale-fence":
        replacement = replace(replacement, fencing_token=1)
    if case == "connected":
        owner.reconcile(admitted, observation)
        assert owner.admit(replacement, expected_fence=1) == replacement
        assert loop.load(str(tmp_path))["attempt_lease"]["history"][0]["effects"] == {"host:worker": "observed"}
        binding = {**dispatch.nonce_bindings, "attempt_id": replacement.attempt_id,
                   "operation_id": replacement.operation_id}
        issued = runtime.nonce.issue(binding)
        owner.execute(replacement,
            nonce_action=lambda action: runtime.nonce.dispatch(issued, binding, action),
            action=lambda bound: calls.append((bound.attempt_id, bound.fencing_token)))
    else:
        with pytest.raises(recovery.LeaseRefusal):
            if case != "cancel":
                owner.reconcile(admitted, observation)
            owner.admit(replacement, expected_fence=1)
    assert calls == (["launch", ("attempt-2", 2)] if case == "connected" else ["launch"])


@pytest.mark.parametrize("case", ["connected", "revoked", "nonce-disabled", "symlink-swap", "cancel", "tokens", "wall_ms", "attempts", "corrections", "heartbeats", "telemetry", "repeated-failure"])
def test_pre_effect_revalidation_detects_revocation_or_symlink_swap(tmp_path, monkeypatch, case):
    owner, lease, runtime, dispatch, calls, authority, usage = _owner(tmp_path, monkeypatch)
    admitted = owner.admit(lease, expected_fence=0)
    target = tmp_path / "input"
    target.write_text("original")
    pin = ports.EffectPath.capture(target)
    def at_boundary(action):
        # Change one input AFTER the optimistic admission/revalidation, so
        # only the final check inside the nonce boundary can stop the effect.
        if case == "revoked":
            authority["valid"] = False
        elif case == "nonce-disabled":
            runtime.nonce.disable_key()
        elif case == "symlink-swap":
            other = tmp_path / "other"
            other.write_text("other")
            target.unlink()
            target.symlink_to(other)
        elif case == "cancel":
            owner.cancel(admitted)
        elif case in usage:
            usage[case] = 10
        elif case == "repeated-failure":
            owner.record_failure(admitted, "artifact", "a" * 64)
            owner.record_failure(admitted, "artifact", "a" * 64)
        return _dispatch(runtime, dispatch)(action)
    if case == "connected":
        assert owner.execute(admitted, nonce_action=at_boundary,
            action=lambda bound: calls.append("effect") or "ok", paths=(pin,)) == "ok"
    else:
        with pytest.raises((recovery.LeaseRefusal, ports.DeliveryPortError, ValueError)):
            owner.execute(admitted, nonce_action=at_boundary,
                action=lambda bound: calls.append("effect"), paths=(pin,))
    assert calls == (["effect"] if case == "connected" else [])
    # A second caller cannot launch uncertain work, even after local success.
    if case == "connected":
        with pytest.raises(recovery.LeaseRefusal):
            owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch), action=lambda bound: calls.append("duplicate"))
        assert calls == ["effect"]


@pytest.mark.parametrize("case", ["same-attempt", "artifact", "setup", "authority", "uncertain-wait", "restart-uncertain"])
def test_pickup_preserves_effect_truth_and_names_repair(tmp_path, monkeypatch, case):
    owner, lease, runtime, dispatch, calls, authority, _ = _owner(tmp_path, monkeypatch)
    admitted = owner.admit(lease, expected_fence=0)
    if case in {"artifact", "setup"}:
        owner.record_failure(admitted, case, "a" * 64)
    elif case == "authority":
        authority["valid"] = False
    elif case in {"uncertain-wait", "restart-uncertain"}:
        owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch), action=lambda bound: calls.append("launch"))
        if case == "restart-uncertain":
            owner = recovery.LeaseRecovery(str(tmp_path), mutate_state=loop.mutate,
                clock=runtime.clock, authorize=lambda lease: True,
                usage=lambda: {"tokens": 0}, limits={"tokens": 10})
    result = owner.pickup(admitted)
    expected = {"same-attempt": "retry_same_attempt", "artifact": "artifact_correction",
                "setup": "setup_repair", "authority": "request_authority",
                "uncertain-wait": "observe_wait_reconcile", "restart-uncertain": "observe_wait_reconcile"}
    assert result["continuation"] == expected[case]
    assert result["attempt_id"] == admitted.attempt_id
    assert result["operation_id"] == admitted.operation_id
    if case in {"uncertain-wait", "restart-uncertain"}:
        waiter = ports.RecordedEventWaiter([[]], runtime.clock)
        assert owner.wait(admitted, waiter) == ()
        assert waiter.invocations == [({"mechanism": "host_terminal_event", "operation_id": "op-1"}, ("op-1",))]
        assert result["effects"] == {"host:worker": "uncertain"}
        assert calls == ["launch"]
