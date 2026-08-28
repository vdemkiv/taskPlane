import json
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from taskplane.command_runtime import (
    BindingMismatch,
    CommandRuntime,
    InvalidTransition,
    InterruptedWait,
)


@pytest.fixture
def runtime(tmp_path):
    return CommandRuntime(str(tmp_path / "commands"), workspace="repo-a",
                          authorization="actor-a", clock=lambda: 1000.0)


def test_silent_command_delivers_only_one_meaningful_terminal_event(runtime):
    handle = runtime.create(command_fingerprint="build", binding={"pid": 7})
    runtime.transition(handle, "running")
    assert runtime.pending(handle, consumer="model") is None

    runtime.append_output(handle, "compiling\n")
    runtime.append_output(handle, "compiling\n")
    assert runtime.pending(handle, consumer="model") is None

    runtime.transition(handle, "succeeded", exit_code=0)
    candidate = runtime.pending(handle, consumer="model")
    event = runtime.receive(
        handle, consumer="model", delivery_key=candidate["delivery_key"])
    assert event["state"] == "succeeded"
    assert event["exit_code"] == 0
    assert event["delivery_key"]
    assert runtime.pending(handle, consumer="model") is None


@pytest.mark.parametrize("state", [
    "failed", "cancelled", "timed_out", "approval_required",
    "input_required",
])
def test_attention_and_terminal_states_are_delivered_once(runtime, state):
    handle = runtime.create(command_fingerprint=state, binding={"id": state})
    runtime.transition(handle, "running")
    runtime.transition(handle, state)
    first = runtime.pending(handle, consumer="model")
    second = runtime.pending(handle, consumer="model")
    assert second is None
    assert runtime.receive(
        handle, consumer="model", delivery_key=first["delivery_key"])
    assert runtime.pending(handle, consumer="model") is None


@pytest.mark.parametrize("attention", ["approval_required", "input_required"])
@pytest.mark.parametrize("first", ["attention", "terminal"])
def test_concurrent_terminal_attention_orderings_preserve_both_events(
        runtime, attention, first):
    handle = runtime.create(command_fingerprint="state-race",
                            binding={"pid": 15})
    runtime.transition(handle, "running")
    barrier = threading.Barrier(2)
    first_done = threading.Event()
    first_state = attention if first == "attention" else "succeeded"
    second_state = "succeeded" if first == "attention" else attention

    def transition(state):
        barrier.wait()
        if state == second_state:
            assert first_done.wait(timeout=2)
        event = runtime.transition(handle, state)
        if state == first_state:
            first_done.set()
        return event

    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(transition, (attention, "succeeded")))

    snapshot = runtime.snapshot(handle)
    states = [event["state"] for event in snapshot["events"]]
    assert states == [first_state, second_state]
    assert snapshot["state"] == "succeeded"
    assert len({event["revision"] for event in snapshot["events"]}) == 2

    wakes = []
    while (candidate := runtime.pending(handle, consumer="model")) is not None:
        wakes.append(runtime.receive(
            handle, consumer="model",
            delivery_key=candidate["delivery_key"]))
    assert [wake["state"] for wake in wakes] == states
    assert runtime.snapshot(handle)["metrics"]["model_delivery_count"] == 2

    # Replayed host notifications and post-receipt restart remain idempotent.
    assert runtime.transition(handle, attention)["delivery_key"] == next(
        wake["delivery_key"] for wake in wakes if wake["state"] == attention)
    resumed = CommandRuntime(str(runtime.root), workspace="repo-a",
                             authorization="actor-a", clock=lambda: 1001.0)
    assert resumed.pending(handle, consumer="model") is None


def test_delivery_claim_is_atomic_for_concurrent_consumers(runtime):
    handle = runtime.create(command_fingerprint="race", binding={"pid": 12})
    runtime.transition(handle, "succeeded")

    with ThreadPoolExecutor(max_workers=8) as workers:
        claims = list(workers.map(
            lambda _: runtime.pending(handle, consumer="model"), range(8)))

    delivered = [claim for claim in claims if claim is not None]
    assert len(delivered) == 1
    lease = runtime.snapshot(handle)["delivery_leases"]["model"]
    assert lease["delivery_key"] == delivered[0]["delivery_key"]


def test_expired_durable_claim_replays_after_crash_then_acks(tmp_path):
    now = [1000.0]
    root = str(tmp_path / "commands")
    first_runtime = CommandRuntime(
        root, workspace="repo-a", authorization="actor-a",
        clock=lambda: now[0], delivery_lease_seconds=30)
    handle = first_runtime.create(command_fingerprint="crash",
                                  binding={"pid": 13})
    first_runtime.transition(handle, "failed")
    first = first_runtime.pending(handle, consumer="model")

    resumed = CommandRuntime(
        root, workspace="repo-a", authorization="actor-a",
        clock=lambda: now[0], delivery_lease_seconds=30)
    assert resumed.pending(handle, consumer="model") is None
    now[0] = 1030.0
    replay = resumed.pending(handle, consumer="model")
    assert replay["delivery_key"] == first["delivery_key"]
    wake = resumed.receive(handle, consumer="model",
                           delivery_key=replay["delivery_key"])
    assert wake["delivery_key"] == first["delivery_key"]
    assert resumed.pending(handle, consumer="model") is None
    assert resumed.snapshot(handle)["metrics"]["model_delivery_count"] == 1


def test_crash_after_receive_uses_receipt_to_prevent_second_model_wake(
        tmp_path):
    root = str(tmp_path / "commands")
    first_runtime = CommandRuntime(
        root, workspace="repo-a", authorization="actor-a")
    handle = first_runtime.create(command_fingerprint="receipt-crash",
                                  binding={"pid": 16})
    first_runtime.transition(handle, "succeeded")
    candidate = first_runtime.pending(handle, consumer="model")
    wake = first_runtime.receive(
        handle, consumer="model", delivery_key=candidate["delivery_key"])
    assert wake is not None

    resumed = CommandRuntime(root, workspace="repo-a",
                             authorization="actor-a")
    assert resumed.pending(handle, consumer="model") is None
    assert resumed.receive(
        handle, consumer="model", delivery_key=candidate["delivery_key"]) \
        is None
    assert resumed.snapshot(handle)["metrics"]["model_delivery_count"] == 1


def test_large_output_is_redacted_stored_once_and_summary_is_bounded(runtime):
    handle = runtime.create(command_fingerprint="logs", binding={"pid": 8})
    secret = "sk-" + "x" * 80
    runtime.append_output(handle, (secret + "\n") * 600)
    runtime.transition(handle, "failed", exit_code=1)
    candidate = runtime.pending(handle, consumer="model")
    event = runtime.receive(
        handle, consumer="model", delivery_key=candidate["delivery_key"])
    assert len(event["output_delta"].encode()) <= 16 * 1024
    assert secret not in event["output_delta"]
    assert event["artifact"]["sha256"]
    artifact = runtime.read_artifact(handle)
    assert secret not in artifact
    assert "[REDACTED]" in artifact
    assert len(list((runtime.root / handle / "artifacts").iterdir())) == 1


@pytest.mark.parametrize("secret", [
    "ghp_" + "A" * 36,
    "github_pat_" + "B" * 40,
    "npm_" + "C" * 36,
    "xoxb-" + "D" * 36,
    "AKIA" + "E" * 16,
    "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
    "Bearer " + "z" * 32,
])
def test_common_credentials_are_redacted_from_summary_and_artifact(
        runtime, secret):
    handle = runtime.create(command_fingerprint="credentials",
                            binding={"pid": 14})
    runtime.append_output(handle, "credential=" + secret)
    runtime.transition(handle, "failed", reason="failed with " + secret)
    candidate = runtime.pending(handle, consumer="model")
    event = runtime.receive(
        handle, consumer="model", delivery_key=candidate["delivery_key"])
    assert secret not in event["output_delta"]
    assert secret not in event["reason"]
    assert secret not in runtime.read_artifact(handle)
    assert "[REDACTED]" in event["output_delta"]


def test_wait_interrupt_preserves_command_and_resume_does_not_relaunch(runtime):
    handle = runtime.create(command_fingerprint="long", binding={"pid": 9})
    runtime.transition(handle, "running")
    with pytest.raises(InterruptedWait):
        runtime.wait_next(handle, consumer="model", interrupted=lambda: True)
    assert runtime.snapshot(handle)["state"] == "running"

    resumed = CommandRuntime(str(runtime.root), workspace="repo-a",
                             authorization="actor-a", clock=lambda: 1001.0)
    assert resumed.reconnect(handle, binding={"pid": 9})["state"] == "running"
    assert resumed.snapshot(handle)["metrics"]["launch_count"] == 1
    assert resumed.snapshot(handle)["metrics"]["reconnect_count"] == 1


def test_handle_is_bound_and_lost_binding_fails_safe(runtime):
    handle = runtime.create(command_fingerprint="safe", binding={"pid": 10})
    other = CommandRuntime(str(runtime.root), workspace="repo-b",
                           authorization="actor-a")
    with pytest.raises(BindingMismatch):
        other.snapshot(handle)

    event = runtime.reconnect(handle, binding=None)
    assert event["state"] == "failed"
    assert event["reason_code"] == "binding_lost"
    assert "binding_lost" not in event["reason"]
    assert "REASON_MINIMIZED" in event["reason"]
    assert runtime.reconnect(handle, binding=None) == event
    assert runtime.snapshot(handle)["metrics"]["launch_count"] == 1


def test_cancel_refuses_lost_ownership_without_consuming_attention(runtime):
    binding = {"pid": 19}
    handle = runtime.create(command_fingerprint="lost-owner", binding=binding)
    runtime.transition(handle, "running")
    attention = runtime.reconnect(
        handle, binding=binding, ownership_check=lambda _: False)

    with pytest.raises(
            InvalidTransition, match="process ownership no longer matches"):
        runtime.cancel(handle)

    preserved = runtime.snapshot(handle)
    assert preserved["state"] == "input_required"
    assert preserved["revision"] == attention["revision"]
    assert preserved["reason_code"] == "detached_worker_ownership_lost"
    assert "detached_worker_ownership_lost" not in preserved["reason"]
    candidate = runtime.pending(handle, consumer="model")
    assert candidate["delivery_key"] == attention["delivery_key"]
    assert runtime.receive(
        handle, consumer="model",
        delivery_key=candidate["delivery_key"])["state"] == "input_required"
    assert runtime.pending(handle, consumer="model") is None


def test_reason_codes_are_closed_machine_data_while_reasons_are_minimized(
        runtime):
    handle = runtime.create(command_fingerprint="reason-code",
                            binding={"pid": 17})
    event = runtime.transition(
        handle, "input_required", reason="private operator detail",
        reason_code="repeated_fingerprint")
    assert event["reason_code"] == "repeated_fingerprint"
    assert "private operator detail" not in event["reason"]
    assert runtime.snapshot(handle)["reason_code"] == "repeated_fingerprint"
    with pytest.raises(ValueError, match="closed runtime reason"):
        runtime.transition(handle, "failed", reason_code="private_detail")


def test_known_pre_field_reason_code_is_recovered_without_restoring_text(
        runtime):
    handle = runtime.create(command_fingerprint="legacy-reason",
                            binding={"pid": 18})
    runtime.reconnect(handle, binding=None)
    path = runtime.root / handle / "snapshot.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot.pop("reason_code")
    for field in ("events", "lifecycle"):
        for row in snapshot[field]:
            row.pop("reason_code", None)
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    restarted = CommandRuntime(
        str(runtime.root), workspace="repo-a", authorization="actor-a",
        clock=lambda: 1001.0)
    recovered = restarted.snapshot(handle)
    assert recovered["reason_code"] == "binding_lost"
    assert recovered["events"][-1]["reason_code"] == "binding_lost"
    assert "binding_lost" not in recovered["reason"]


def test_corrupt_snapshot_recovers_from_fsynced_transition(runtime):
    handle = runtime.create(command_fingerprint="recover", binding={"pid": 11})
    runtime.transition(handle, "running")
    (runtime.root / handle / "snapshot.json").write_text("{broken")
    recovered = runtime.snapshot(handle)
    assert recovered["state"] == "running"
    assert recovered["metrics"]["launch_count"] == 1


def test_run_store_can_revision_check_command_references(tmp_path):
    from taskplane.run_store import RevisionConflict, RunStore

    store = RunStore(home=str(tmp_path / "home"))
    manifest = {"schema": "taskplane.run/v3", "run_id": "r1",
                "revision": 1, "commands": {"handles": [], "waves": []}}
    path_obj = tmp_path / "home" / "runs" / "r1"
    path_obj.mkdir(parents=True)
    (path_obj / "manifest.json").write_text(json.dumps(manifest))

    updated = store.reference_command("r1", expected_revision=1,
                                      handle="opaque", wave_id="wave-a")
    assert updated["commands"] == {"handles": ["opaque"],
                                    "waves": ["wave-a"]}
    with pytest.raises(RevisionConflict):
        store.reference_command("r1", expected_revision=1, handle="again")
