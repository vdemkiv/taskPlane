import json

import pytest

from taskplane.command_runtime import (
    BindingMismatch,
    CommandRuntime,
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
    event = runtime.pending(handle, consumer="model")
    assert event["state"] == "succeeded"
    assert event["exit_code"] == 0
    assert event["delivery_key"]
    runtime.ack(handle, consumer="model", delivery_key=event["delivery_key"])
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
    assert first == second
    runtime.ack(handle, consumer="model", delivery_key=first["delivery_key"])
    assert runtime.pending(handle, consumer="model") is None


def test_large_output_is_redacted_stored_once_and_summary_is_bounded(runtime):
    handle = runtime.create(command_fingerprint="logs", binding={"pid": 8})
    secret = "sk-" + "x" * 80
    runtime.append_output(handle, (secret + "\n") * 600)
    runtime.transition(handle, "failed", exit_code=1)
    event = runtime.pending(handle, consumer="model")
    assert len(event["output_delta"].encode()) <= 16 * 1024
    assert secret not in event["output_delta"]
    assert event["artifact"]["sha256"]
    artifact = runtime.read_artifact(handle)
    assert secret not in artifact
    assert "[REDACTED]" in artifact
    assert len(list((runtime.root / handle / "artifacts").iterdir())) == 1


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
    assert event["reason"] == "binding_lost"
    assert runtime.reconnect(handle, binding=None) == event
    assert runtime.snapshot(handle)["metrics"]["launch_count"] == 1


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
