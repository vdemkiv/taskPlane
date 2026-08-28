import re
import threading

import pytest

from taskplane.command_adapters import CommandAdapter, HostLaunch
from taskplane.command_runtime import CommandRuntime, InterruptedWait
from taskplane.eval_drivers import CodexAdapter


@pytest.fixture(params=["claude", "codex"])
def adapter(tmp_path, request):
    runtime = CommandRuntime(
        str(tmp_path / "commands"), workspace="repo-a",
        authorization="actor-a")
    launches = []

    def launch(command, cwd):
        launches.append((command, cwd))
        return HostLaunch(binding={"process": 41, "session": "host-private"})

    return CommandAdapter(host=request.param, runtime=runtime,
                          launcher=launch), launches


def test_hosts_expose_the_same_opaque_bound_lifecycle(adapter):
    command_adapter, launches = adapter
    handle = command_adapter.launch(["python", "-m", "build"], cwd="/repo")

    assert launches == [(["python", "-m", "build"], "/repo")]
    assert re.fullmatch(r"[0-9a-f]{32}", handle)
    assert handle not in {"41", "host-private"}
    snapshot = command_adapter.snapshot(handle)
    assert snapshot["state"] == "running"
    assert "process" not in str(snapshot)
    assert "session" not in str(snapshot)

    command_adapter.notify(handle, {"status": "completed", "exit_code": 0})
    event = command_adapter.wait_next(handle, consumer="model")
    assert event["state"] == "succeeded"
    assert event["exit_code"] == 0
    assert command_adapter.wait_next(
        handle, consumer="model", timeout=0) is None


@pytest.mark.parametrize(("host_status", "state"), [
    ("error", "failed"),
    ("timeout", "timed_out"),
    ("cancelled", "cancelled"),
    ("approval", "approval_required"),
    ("input", "input_required"),
])
def test_host_states_are_normalized_once(adapter, host_status, state):
    command_adapter, _ = adapter
    handle = command_adapter.launch("check", cwd="/repo")
    command_adapter.notify(handle, {
        "status": host_status, "exit_code": 7, "reason": "host detail",
        "output": "bounded result",
    })

    event = command_adapter.wait_next(handle, consumer="model")
    assert event["state"] == state
    assert "host detail" not in event["reason"]
    assert "REASON_MINIMIZED" in event["reason"]
    assert event["reason_code"] is None
    assert "bounded result" not in event["output_delta"]
    assert "OUTPUT_MINIMIZED" in event["output_delta"]
    assert command_adapter.wait_next(
        handle, consumer="model", timeout=0) is None


def test_no_event_host_uses_one_runtime_blocking_wait_and_zero_model_polls(
        tmp_path):
    runtime = CommandRuntime(
        str(tmp_path / "commands"), workspace="repo-a",
        authorization="actor-a")
    wait_calls = []
    original_wait = runtime.wait_next

    def blocking_wait(*args, **kwargs):
        wait_calls.append((args, kwargs))
        return original_wait(*args, **kwargs)

    runtime.wait_next = blocking_wait
    command_adapter = CommandAdapter(
        host="codex", runtime=runtime,
        launcher=lambda command, cwd: HostLaunch(binding={"process": 42}))
    handle = command_adapter.launch("slow", cwd="/repo")

    producer = threading.Thread(target=lambda: command_adapter.notify(
        handle, {"status": "completed", "exit_code": 0}))
    producer.start()
    event = command_adapter.wait_next(handle, consumer="model", timeout=1)
    producer.join()

    assert event["state"] == "succeeded"
    assert len(wait_calls) == 1
    assert command_adapter.snapshot(handle)["metrics"][
        "unchanged_model_polls"] == 0


def test_interrupt_cancel_and_reconnect_do_not_relaunch(adapter):
    command_adapter, launches = adapter
    handle = command_adapter.launch("slow", cwd="/repo")

    with pytest.raises(InterruptedWait):
        command_adapter.wait_next(
            handle, consumer="model", interrupted=lambda: True)
    assert command_adapter.snapshot(handle)["state"] == "running"
    assert command_adapter.reconnect(handle)["state"] == "running"
    assert len(launches) == 1

    first = command_adapter.cancel(handle)
    second = command_adapter.cancel(handle)
    assert first["delivery_key"] == second["delivery_key"]
    assert command_adapter.wait_next(handle, consumer="model")[
        "state"] == "cancelled"


def test_native_wait_is_consumed_once_before_canonical_delivery(tmp_path):
    runtime = CommandRuntime(
        str(tmp_path / "commands"), workspace="repo-a",
        authorization="actor-a")
    native_waits = []

    def native_wait(binding, timeout, interrupted):
        native_waits.append((binding, timeout, interrupted))
        return {"type": "authorization_required", "message": "approve"}

    command_adapter = CommandAdapter(
        host="claude", runtime=runtime,
        launcher=lambda command, cwd: HostLaunch(binding={"session": "s1"}),
        native_wait=native_wait)
    handle = command_adapter.launch("deploy", cwd="/repo")

    event = command_adapter.wait_next(handle, consumer="model", timeout=3)
    assert event["state"] == "approval_required"
    assert "approve" not in event["reason"]
    assert "REASON_MINIMIZED" in event["reason"]
    assert event["reason_code"] is None
    assert len(native_waits) == 1


def test_existing_synchronous_native_run_api_is_unchanged(tmp_path):
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        from taskplane.eval_drivers import ProcessOutcome
        return ProcessOutcome("success", 0, stdout=b"ok", pid=123)

    result = CodexAdapter(executable="codex", runner=runner).run(
        {"task": "review"}, cwd=str(tmp_path))

    assert result["status"] == "success"
    assert result["stdout"] == "ok"
    assert calls[0]["argv"][-1] == "-"
