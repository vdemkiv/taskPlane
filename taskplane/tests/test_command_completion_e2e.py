"""Single deterministic validation of event-driven command completion."""

import json

import pytest

import loop
import runtime_eval
import spend
from taskplane.command_adapters import CommandAdapter, HostLaunch
from taskplane.command_runtime import CommandRuntime, InterruptedWait


def test_silent_command_end_to_end_completion_resume_attention_and_efficiency(
        tmp_path):
    now = [10_000.0]
    root = tmp_path / "commands"
    binding = {"process": 73, "session": "durable-host-session"}
    launches = []

    def launch(command, cwd):
        launches.append((command, cwd))
        return HostLaunch(binding=binding)

    runtime = CommandRuntime(
        str(root), workspace="repo", authorization="reviewer",
        clock=lambda: now[0])
    adapter = CommandAdapter(host="codex", runtime=runtime, launcher=launch)
    handle = adapter.launch("five-minute-validation", cwd="/repo",
                            wave_id="validation-wave")

    # A caller can stop waiting without killing or relaunching the command.
    with pytest.raises(InterruptedWait):
        adapter.wait_next(handle, consumer="model", interrupted=lambda: True)
    silent = adapter.snapshot(handle)
    assert silent["state"] == "running"
    assert silent["metrics"]["model_delivery_count"] == 0
    assert silent["metrics"]["unchanged_model_polls"] == 0
    assert launches == [("five-minute-validation", "/repo")]

    # A process restart reattaches to the durable handle and binding.
    restarted_runtime = CommandRuntime(
        str(root), workspace="repo", authorization="reviewer",
        clock=lambda: now[0])
    restarted = CommandAdapter(
        host="codex", runtime=restarted_runtime, launcher=launch)
    assert restarted.reconnect(handle, binding=binding)["state"] == "running"
    assert restarted.snapshot(handle)["metrics"] == {
        "launch_count": 1,
        "reconnect_count": 1,
        "model_delivery_count": 0,
        "unchanged_model_polls": 0,
        "output_redactions": 0,
    }

    # More than five silent minutes elapse on a fake clock: no wall-clock
    # sleep and no model turn occurs until the meaningful terminal event.
    now[0] += 301
    restarted.notify(handle, {
        "status": "completed", "exit_code": 0,
        "output": "validated\n" + ("x" * 20_000),
    })
    completed = restarted.wait_next(handle, consumer="model", timeout=0)
    assert completed["state"] == "succeeded"
    assert completed["elapsed_ms"] >= 300_000
    assert len(completed["output_delta"].encode()) <= 16 * 1024
    assert completed["artifact"] == restarted.snapshot(handle)["artifact"]
    assert completed["artifact"]["path"] == "artifacts/output.log"
    assert completed["artifact"]["truncated"] is True
    assert restarted.wait_next(handle, consumer="model", timeout=0) is None
    assert restarted.snapshot(handle)["metrics"]["model_delivery_count"] == 1
    assert len(launches) == 1

    # Human attention observed on either side of completion stays visible,
    # while the wave emits no more than one ordinary aggregate completion.
    wave = loop.command_wave_create(
        "validation-wave", ["main", "approval", "input"], handles={
            "main": handle, "approval": "approval-handle",
            "input": "input-handle",
        })
    events = []
    events += loop.command_wave_update(wave, "main", "succeeded")
    events += loop.command_wave_update(wave, "approval", "succeeded")
    events += loop.command_wave_update(wave, "approval", "approval_required")
    events += loop.command_wave_update(wave, "input", "input_required")
    encoded = json.loads(json.dumps(wave))
    resumed_wave = loop.command_wave_resume(encoded, list(wave["members"]))
    assert loop.command_wave_update(
        resumed_wave, "input", "input_provided") == []
    events += loop.command_wave_update(resumed_wave, "input", "succeeded")
    states = [event["state"] for event in events]
    assert states.count("approval_required") == 1
    assert states.count("input_required") == 1
    assert states.count("wave_completed") == 1
    assert resumed_wave["ordinary_completion_deliveries"] == 1
    assert resumed_wave["handles"] == wave["handles"]

    counters = {
        "launches": 1, "elapsed_ms": completed["elapsed_ms"],
        "meaningful_wakes": 1, "model_wakes": 1,
        "unchanged_model_polls": 0,
        "polling_raw_tokens": 5, "total_raw_tokens": 10_000,
        "avoided_polling_raw_tokens": 995,
        "baseline_polling_raw_tokens": 1_000,
        "timeouts": 0, "cancellations": 0,
    }
    efficiency = spend.command_efficiency(counters)
    projection = runtime_eval.command_wave_projection(
        resumed_wave, efficiency=counters,
        artifacts=[completed["artifact"]])
    assert efficiency["gate"] == {"status": "pass", "failures": []}
    assert efficiency["polling_token_reduction"] >= .90
    assert efficiency["unchanged_model_polls"] == 0
    assert efficiency["polling_raw_token_share"] < .01
    assert projection["efficiency"]["model_wakes"] == 1
    assert projection["efficiency"]["unchanged_model_polls"] == 0
    assert projection["artifacts"] == [completed["artifact"]]
    unsafe = runtime_eval.command_wave_projection(
        resumed_wave, artifacts=[
            {"path": "/private/build/output.log"},
            {"path": "../outside/output.log"},
            {"path": "TOKEN=secret-value/artifact.log"},
        ])
    assert [row["path"] for row in unsafe["artifacts"]] == [
        "<redacted-path>", "<redacted-path>", "<redacted>",
    ]

    # Adverse endings and reconnect audit failures fail safe and deduplicate.
    timeout_handle = restarted.launch("timeout", cwd="/repo")
    timeout = restarted.notify(timeout_handle, {"status": "timeout"})
    assert timeout["state"] == "timed_out"
    assert restarted.notify(timeout_handle, {"status": "timeout"}) == timeout
    cancel_handle = restarted.launch("cancel", cwd="/repo")
    cancelled = restarted.cancel(cancel_handle)
    assert cancelled["state"] == "cancelled"
    assert restarted.cancel(cancel_handle) == cancelled
    audit_handle = restarted.launch("audit", cwd="/repo")
    audit_failure = restarted_runtime.reconnect(audit_handle, binding=None)
    assert audit_failure["state"] == "failed"
    assert audit_failure["reason_code"] == "binding_lost"
    assert "binding_lost" not in audit_failure["reason"]
