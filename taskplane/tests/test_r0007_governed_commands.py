import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import governed_commands
import loop
import tp
import taskplane_lite as contract_engine
from taskplane import command_adapters
from taskplane.command_adapters import CommandAdapter, HostLaunch
from taskplane.command_runtime import CommandRuntime

from taskplane.command_runtime import MAX_EVENT_OUTPUT


def _json_stdout(capsys):
    return json.loads(capsys.readouterr().out)


def _direct_cli(cli, workspace, *arguments):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(cli), *arguments], cwd=workspace, env=environment,
        capture_output=True, text=True, timeout=15, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _activate_command_contract(workspace):
    contract = contract_engine.build_contract(
        "governed-command-test", scope=[str(workspace)],
        tools=["exec_command"], plan_minted=True)
    contract_engine.activate(str(workspace), contract, snapshot=None)
    return contract


def test_direct_executable_launches_and_waits_without_pythonpath(tmp_path):
    taskplane_root = Path(__file__).resolve().parents[1]
    cli = taskplane_root / "tp.py"
    workspace = tmp_path / "direct-repo"
    workspace.mkdir()
    _activate_command_contract(workspace)

    launched = _direct_cli(
        cli, workspace, "command", "launch", "--workspace", str(workspace),
        "--authorization", "agent:direct", "--run-id", "run-direct",
        "--task-id", "direct-startup", "--host", "codex", "--",
        "/usr/bin/printf", "direct-ok\n")
    assert launched["lifecycle_states"] == ["created", "running"]

    completed = _direct_cli(
        cli, workspace, "loop", "command", "wait", "--workspace",
        str(workspace), "--authorization", "agent:direct", "--consumer",
        "executor:direct", "--timeout", "10", launched["handle"])
    assert completed["event"]["state"] == "succeeded"
    assert completed["event"]["output_delta"] == "direct-ok\n"


def test_direct_reconnect_emits_attention_when_detached_worker_is_lost(
        tmp_path):
    taskplane_root = Path(__file__).resolve().parents[1]
    cli = taskplane_root / "tp.py"
    workspace = tmp_path / "lost-worker-repo"
    workspace.mkdir()
    _activate_command_contract(workspace)

    launched = _direct_cli(
        cli, workspace, "command", "launch", "--workspace", str(workspace),
        "--authorization", "agent:lost", "--run-id", "run-lost",
        "--task-id", "lost-worker", "--host", "codex", "--",
        "/bin/sh", "-c", "kill -9 \"$PPID\"")

    reconnected = None
    for _ in range(30):
        reconnected = _direct_cli(
            cli, workspace, "command", "reconnect", "--workspace",
            str(workspace), "--authorization", "agent:lost",
            launched["handle"])
        if reconnected["event"]["state"] == "input_required":
            break
        time.sleep(0.05)

    assert reconnected is not None
    assert reconnected["event"]["state"] == "input_required"
    assert reconnected["event"]["reason"] == \
        "detached_worker_ownership_lost"
    assert reconnected["snapshot"]["state"] == "input_required"
    assert reconnected["lifecycle_states"] == [
        "created", "running", "input_required",
    ]
    replayed = _direct_cli(
        cli, workspace, "command", "reconnect", "--workspace",
        str(workspace), "--authorization", "agent:lost",
        launched["handle"])
    assert replayed["event"]["revision"] == reconnected["event"]["revision"]
    assert replayed["lifecycle_states"] == reconnected["lifecycle_states"]

    cancelled = _direct_cli(
        cli, workspace, "command", "cancel", "--workspace", str(workspace),
        "--authorization", "agent:lost", launched["handle"])
    assert cancelled["event"]["state"] == "cancelled"
    assert cancelled["lifecycle_states"] == [
        "created", "running", "input_required", "cancelled",
    ]
    replayed_cancel = _direct_cli(
        cli, workspace, "command", "cancel", "--workspace", str(workspace),
        "--authorization", "agent:lost", launched["handle"])
    assert replayed_cancel["event"]["revision"] == \
        cancelled["event"]["revision"]
    assert replayed_cancel["lifecycle_states"] == \
        cancelled["lifecycle_states"]


def test_supported_cli_and_loop_run_one_real_durable_command(tmp_path, capsys):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _activate_command_contract(workspace)
    output_size = MAX_EVENT_OUTPUT + 4096

    rc = tp.main([
        "command", "launch", "--workspace", str(workspace),
        "--authorization", "agent:c1", "--run-id", "run-r0007",
        "--task-id", "c1-governed-command-runtime", "--host", "codex",
        "--", "/usr/bin/printf", "x" * output_size,
    ])
    assert rc == 0
    launched = _json_stdout(capsys)
    assert launched["schema"] == "taskplane.governed-command-result/v1"
    assert launched["action"] == "launch"
    assert launched["identity"] == {
        "schema": "taskplane.governed-command-identity/v1",
        "run_id": "run-r0007",
        "task_id": "c1-governed-command-runtime",
    }
    assert launched["lifecycle_states"] == ["created", "running"]

    rc = tp.main([
        "loop", "command", "wait", "--workspace", str(workspace),
        "--authorization", "agent:c1", "--consumer", "executor:c1",
        "--timeout", "10", launched["handle"],
    ])
    assert rc == 0
    completed = _json_stdout(capsys)
    event = completed["event"]
    assert event["schema"] == "taskplane.command-event/v1"
    assert event["state"] == "succeeded"
    assert event["handle"] == launched["handle"]
    assert event["identity"] == launched["identity"]
    assert event["delivery_receipt"] == {
        "schema": "taskplane.command-delivery-receipt/v1",
        "consumer": "executor:c1",
        "delivery_key": event["delivery_key"],
        "revision": event["revision"],
    }
    assert len(event["output_delta"].encode()) <= MAX_EVENT_OUTPUT
    assert event["artifact"]["truncated"] is True

    shown = loop.governed_command(str(workspace), "show", {
        "authorization": "agent:c1", "handle": launched["handle"],
    })
    assert [row["state"] for row in shown["lifecycle"]] == [
        "created", "running", "succeeded",
    ]
    assert shown["snapshot"]["metrics"]["launch_count"] == 1

    reconnected = loop.governed_command(str(workspace), "reconnect", {
        "authorization": "agent:c1", "handle": launched["handle"],
    })
    assert reconnected["event"]["state"] == "succeeded"
    assert reconnected["snapshot"]["metrics"]["launch_count"] == 1


def test_cancel_survives_cli_reconstruction_and_is_delivered(tmp_path, capsys):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _activate_command_contract(workspace)
    assert tp.main([
        "command", "launch", "--workspace", str(workspace),
        "--authorization", "agent:c1", "--run-id", "run-r0007",
        "--task-id", "cancel-case", "--host", "codex", "--",
        "/bin/sleep", "60",
    ]) == 0
    launched = _json_stdout(capsys)

    assert tp.main([
        "command", "cancel", "--workspace", str(workspace),
        "--authorization", "agent:c1", launched["handle"],
    ]) == 0
    cancelled = _json_stdout(capsys)
    assert cancelled["event"]["state"] == "cancelled"

    delivered = loop.governed_command(str(workspace), "wait", {
        "authorization": "agent:c1", "handle": launched["handle"],
        "consumer": "executor:cancel", "timeout": 1,
    })
    assert delivered["event"]["state"] == "cancelled"
    assert delivered["event"]["delivery_receipt"]["consumer"] == \
        "executor:cancel"


def test_cli_and_loop_composition_roots_call_the_governed_runtime(
        tmp_path, capsys, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    calls = []

    def invoke(workspace_arg, action, request):
        calls.append((Path(workspace_arg), action, dict(request)))
        return {"schema": "sentinel", "action": action}

    monkeypatch.setattr(tp.governed_command_engine, "execute", invoke)
    assert tp.main([
        "command", "show", "--workspace", str(workspace),
        "--authorization", "agent:c1", "0" * 32,
    ]) == 0
    assert _json_stdout(capsys) == {"schema": "sentinel", "action": "show"}
    assert calls[-1][1] == "show"

    monkeypatch.setattr(loop.governed_commands, "execute", invoke)
    assert loop.governed_command(str(workspace), "show", {
        "authorization": "agent:c1", "handle": "0" * 32,
    }) == {"schema": "sentinel", "action": "show"}
    assert calls[-1][1] == "show"


@pytest.mark.parametrize("field,value", [
    ("run_id", None),
    ("task_id", None),
    ("run_id", ""),
    ("task_id", "   "),
    ("run_id", "run with spaces"),
    ("task_id", 7),
])
def test_loop_launch_rejects_invalid_identity_before_runtime_creation(
        tmp_path, field, value):
    workspace = tmp_path / "invalid-loop-identity"
    workspace.mkdir()
    request = {
        "authorization": "agent:c1", "argv": [sys.executable, "-c", "pass"],
        "run_id": "run-r0007", "task_id": "governed-command",
    }
    request[field] = value
    with pytest.raises(governed_commands.GovernedCommandError,
                       match=field + " is invalid"):
        loop.governed_command(str(workspace), "launch", request)
    assert not (workspace / ".taskplane" / "command-runtime-v1").exists()


@pytest.mark.parametrize("missing", ["run_id", "task_id"])
def test_loop_launch_rejects_missing_identity_before_runtime_creation(
        tmp_path, missing):
    workspace = tmp_path / "missing-loop-identity"
    workspace.mkdir()
    request = {
        "authorization": "agent:c1", "argv": [sys.executable, "-c", "pass"],
        "run_id": "run-r0007", "task_id": "governed-command",
    }
    request.pop(missing)
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="request is missing"):
        loop.governed_command(str(workspace), "launch", request)
    assert not (workspace / ".taskplane" / "command-runtime-v1").exists()


def test_supported_cli_rejects_blank_and_malformed_identity(tmp_path, capsys):
    workspace = tmp_path / "invalid-cli-identity"
    workspace.mkdir()
    prefix = [
        "command", "launch", "--workspace", str(workspace),
        "--authorization", "agent:c1", "--run-id",
    ]
    suffix = [
        "--task-id", "governed-command", "--host", "codex", "--",
        sys.executable, "-c", "pass",
    ]
    for run_id in ("", "run with spaces"):
        assert tp.main([*prefix, run_id, *suffix]) == 70
        assert "governed command run_id is invalid" in capsys.readouterr().err
    assert not (workspace / ".taskplane" / "command-runtime-v1").exists()


def test_launch_rejects_ungoverned_and_opaque_argv_before_spawn(
        tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    request = {
        "authorization": "agent:c1", "run_id": "run-r0007",
        "task_id": "secure-launch", "argv": ["/usr/bin/printf", "ok"],
    }
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="exact active contract"):
        governed_commands.execute(str(workspace), "launch", request)
    _activate_command_contract(workspace)
    request["argv"] = [
        sys.executable, "-c", "open('escaped.txt', 'w').write('opaque')"]
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="opaque interpreter"):
        governed_commands.execute(str(workspace), "launch", request)
    assert not (workspace / ".taskplane" / "command-runtime-v1").exists()


def test_launch_authority_roots_subdirectory_targets_at_workspace(tmp_path):
    workspace = tmp_path / "repo"
    subdirectory = workspace / "sub"
    subdirectory.mkdir(parents=True)
    identity = {"schema": governed_commands.IDENTITY_SCHEMA,
                "run_id": "run-r0007", "task_id": "cwd-rooting"}
    argv = ["touch", "allowed.txt"]

    wrong_root = contract_engine.build_contract(
        "wrong-root", scope=["allowed.txt"], tools=["exec_command"],
        plan_minted=True)
    contract_engine.activate(
        str(workspace), wrong_root, snapshot=None)
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="outside its active contract"):
        governed_commands._governed_launch_authority(
            str(workspace), str(subdirectory), argv, identity)

    correct_root = contract_engine.build_contract(
        "correct-root", scope=["sub/allowed.txt"], tools=["exec_command"],
        plan_minted=True)
    contract_engine.activate(
        str(workspace), correct_root, snapshot=None)
    authority = governed_commands._governed_launch_authority(
        str(workspace), str(subdirectory), argv, identity)
    assert governed_commands._governed_launch_authority(
        str(workspace), str(subdirectory), argv, identity,
        expected=authority) == authority


def test_launch_authority_preserves_raw_deny_for_subdirectory_argv(tmp_path):
    workspace = tmp_path / "repo"
    subdirectory = workspace / "sub"
    subdirectory.mkdir(parents=True)
    identity = {"schema": governed_commands.IDENTITY_SCHEMA,
                "run_id": "run-r0007", "task_id": "raw-deny"}
    argv = ["touch", "forbidden.txt"]

    allowed = contract_engine.build_contract(
        "allowed", scope=["sub/forbidden.txt"], tools=["exec_command"],
        plan_minted=True)
    contract_engine.activate(str(workspace), allowed, snapshot=None)
    authority = governed_commands._governed_launch_authority(
        str(workspace), str(subdirectory), argv, identity)

    denied = contract_engine.build_contract(
        "denied", scope=["sub/forbidden.txt"], tools=["exec_command"],
        deny_extra=["touch forbidden.txt"], plan_minted=True)
    contract_engine.activate(str(workspace), denied, snapshot=None)
    for expected in (None, authority):
        with pytest.raises(
                governed_commands.GovernedCommandError,
                match="deny pattern 'touch forbidden\\.txt'"):
            governed_commands._governed_launch_authority(
                str(workspace), str(subdirectory), argv, identity,
                expected=expected)


def test_worker_authority_recheck_detects_contract_change(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _activate_command_contract(workspace)
    identity = {"schema": governed_commands.IDENTITY_SCHEMA,
                "run_id": "run-r0007", "task_id": "secure-launch"}
    argv = ["/usr/bin/printf", "ok"]
    authority = governed_commands._governed_launch_authority(
        str(workspace), str(workspace), argv, identity)
    contract_engine.activate(str(workspace), contract_engine.build_contract(
        "changed", scope=[str(workspace)], tools=["exec_command"],
        plan_minted=True), snapshot=None)
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="authority changed"):
        governed_commands._governed_launch_authority(
            str(workspace), str(workspace), argv, identity,
            expected=authority)


def test_generic_native_adapter_does_not_require_posix_process_groups(
        tmp_path, monkeypatch):
    runtime = CommandRuntime(
        str(tmp_path / "runtime"), workspace=str(tmp_path),
        authorization="agent:c1")
    calls = []

    def launch(command, cwd):
        calls.append((command, cwd))
        return HostLaunch(binding={"native": "opaque-host-handle"})

    adapter = CommandAdapter(
        host="codex", runtime=runtime,
        launcher=launch)
    monkeypatch.setattr(
        command_adapters, "detached_process_groups_supported", lambda: False)
    handle = adapter.launch(
        {"native": "agent-dispatch"}, cwd=str(tmp_path), identity={
            "schema": governed_commands.IDENTITY_SCHEMA,
            "run_id": "run-r0007", "task_id": "host-neutral"})

    assert calls == [({"native": "agent-dispatch"}, str(tmp_path))]
    assert adapter.snapshot(handle)["state"] == "running"


def test_governed_detached_launch_still_fails_before_spawn(
        tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _activate_command_contract(workspace)
    spawn_calls = []

    def spawn(*args, **kwargs):
        spawn_calls.append((args, kwargs))
        raise AssertionError("governed detached preflight spawned a process")

    monkeypatch.setattr(
        governed_commands, "detached_process_groups_supported", lambda: False)
    monkeypatch.setattr(governed_commands.subprocess, "Popen", spawn)
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="unsupported.*no process"):
        governed_commands.execute(str(workspace), "launch", {
            "authorization": "agent:c1", "run_id": "run-r0007",
            "task_id": "detached-denied", "host": "codex",
            "argv": ["/usr/bin/printf", "ok"],
        })

    assert spawn_calls == []
    assert not (workspace / ".taskplane" / "command-runtime-v1").exists()


def test_cancel_rejects_mutable_binding_before_canceller(tmp_path):
    runtime = CommandRuntime(
        str(tmp_path / "runtime"), workspace=str(tmp_path),
        authorization="agent:c1")
    original = {"schema": "test-binding/v1", "owner": "original"}
    handle = runtime.create(
        command_fingerprint="command", binding=original,
        identity={"schema": governed_commands.IDENTITY_SCHEMA,
                  "run_id": "run-r0007", "task_id": "cancel-owner"})
    runtime.transition(handle, "running")
    calls = []
    adapter = CommandAdapter(
        host="codex", runtime=runtime,
        launcher=lambda command, cwd: HostLaunch(binding=original),
        canceller=lambda binding: calls.append(dict(binding)))
    adapter.restore_binding(
        handle, {"schema": "test-binding/v1", "owner": "tampered"})
    with pytest.raises(OSError, match="immutable runtime ownership"):
        adapter.cancel(handle)
    assert calls == []

def test_production_edges_are_explicit_and_mutation_sensitive():
    root = Path(__file__).resolve().parents[1]
    cli_source = (root / "tp.py").read_text(encoding="utf-8")
    loop_source = (root / "loop.py").read_text(encoding="utf-8")
    governed_source = (root / "governed_commands.py").read_text(
        encoding="utf-8")

    assert "governed_command_engine.execute(" in cli_source
    assert "governed_commands.execute(" in loop_source
    assert "CommandAdapter(" in governed_source
    assert "CommandRuntime(" in governed_source


@pytest.mark.parametrize("step,role", [
    ("execute", "tp-executor"),
    ("evaluate", "tp-evaluator"),
    ("fix", "tp-fixer"),
])
def test_normal_native_dispatch_emits_non_authoritative_intent_telemetry(
        tmp_path, step, role):
    workspace = str(tmp_path)
    policy = loop.event_wait_policy(f"{step}:task-1", 1)
    intent = loop._native_dispatch_intent(
        workspace, {"goal": "R-0007 live flow", "baseline": "abc123"},
        step=step, task_id="task-1",
        dispatch={"role": role, "task_name": f"{step}-task-1"},
        wait_policy=policy)

    assert intent["schema"] == \
        "taskplane.native-agent-dispatch-intent-telemetry/v1"
    assert intent["kind"] == "dispatch_intent"
    assert intent["transport"] == "native_agent"
    assert intent["intended_consumer"] == f"{role}:task-1"
    assert intent["evidence"] == {
        "authoritative": False,
        "host_observed": False,
        "execution_observed": False,
        "delivery_observed": False,
        "may_satisfy_execution_gate": False,
        "may_satisfy_delivery_gate": False,
    }
    assert intent["wait_policy"] == policy
    assert not ({"handle", "snapshot", "lifecycle_states", "event",
                 "delivery_receipt"} & set(intent))
    telemetry_path = Path(intent["telemetry_path"])
    assert telemetry_path.is_file()
    assert telemetry_path.parent == \
        tmp_path / ".taskplane" / "dispatch-intent-telemetry-v1"
    assert json.loads(telemetry_path.read_text(encoding="utf-8")) == {
        key: value for key, value in intent.items() if key != "telemetry_path"
    }
    assert not (tmp_path / ".taskplane" / "command-runtime-v1").exists()


def test_normal_flow_wiring_is_mutation_sensitive():
    source = Path(loop.__file__).read_text(encoding="utf-8")
    wave_body = source[source.index("def wave("):source.index("def claim(")]
    next_body = source[
        source.index("def next_action("):source.index("guide = runtime_eval")]
    assert 'entry["dispatch_intent"] = _native_dispatch_intent(' in \
        wave_body
    assert 'result["dispatch_intent"] = _native_dispatch_intent(' in \
        next_body
    assert '"wait_invocation": wave_wait_invocation' in wave_body
    assert 'result["wait_invocation"] = event_wait_invocation(' in next_body
    assert 'if step in {"execute", "evaluate", "fix"}' in next_body
    governed_source = Path(governed_commands.__file__).read_text(
        encoding="utf-8")
    execute_start = governed_source.index("def execute(")
    dispatch_body = governed_source[
        governed_source.index('if action == "dispatch":', execute_start):
        governed_source.index('if action == "launch":', execute_start)]
    assert "subprocess.Popen(" not in dispatch_body
    assert "CommandRuntime(" not in dispatch_body
    assert ".pending(" not in dispatch_body
    assert ".receive(" not in dispatch_body
    assert '"authoritative": False' in dispatch_body
    assert '"host_observed": False' in dispatch_body
    assert '"execution_observed": False' in dispatch_body
    assert '"delivery_observed": False' in dispatch_body
