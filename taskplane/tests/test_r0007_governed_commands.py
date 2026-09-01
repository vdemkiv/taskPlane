import hashlib
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
from taskplane import checkpoint
from taskplane import command_adapters
from taskplane.command_adapters import CommandAdapter, HostLaunch
from taskplane.command_runtime import CommandRuntime

from taskplane.command_runtime import MAX_EVENT_OUTPUT


def _json_stdout(capsys):
    return json.loads(capsys.readouterr().out)


def _minimized(label, value):
    raw = str(value)
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
    return (f"[REDACTED]\n[{label}_MINIMIZED bytes="
            f"{len(raw.encode('utf-8', 'replace'))} sha256={digest}]")


def _direct_cli(cli, workspace, *arguments):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(cli), *arguments], cwd=workspace, env=environment,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=15, check=False)
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
    assert completed["event"]["output_delta"] == \
        _minimized("OUTPUT", "direct-ok\n")


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
    assert reconnected["event"]["reason"] == _minimized(
        "REASON", "detached_worker_ownership_lost")
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

    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    cancelled = subprocess.run(
        [str(cli), "command", "cancel", "--workspace", str(workspace),
         "--authorization", "agent:lost", launched["handle"]],
        cwd=workspace, env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15, check=False)
    assert cancelled.returncode != 0
    assert "process ownership no longer matches" in cancelled.stderr
    still_attended = _direct_cli(
        cli, workspace, "command", "reconnect", "--workspace",
        str(workspace), "--authorization", "agent:lost",
        launched["handle"])
    assert still_attended["event"]["state"] == "input_required"
    assert still_attended["event"]["revision"] == \
        reconnected["event"]["revision"]


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
        "attempt": 1,
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


@pytest.mark.parametrize("qualified", [False, True])
def test_launch_rejects_version_qualified_python_inline_writes(
        tmp_path, qualified):
    workspace = tmp_path / "versioned-python-repo"
    workspace.mkdir()
    _activate_command_contract(workspace)
    identity = {
        "schema": governed_commands.IDENTITY_SCHEMA,
        "run_id": "run-r0007", "task_id": "versioned-python",
    }
    basename = f"python{sys.version_info.major}.{sys.version_info.minor}"
    program = (str(Path(sys.executable).with_name(basename))
               if qualified else basename)

    with pytest.raises(governed_commands.GovernedCommandError,
                       match="opaque interpreter"):
        governed_commands._governed_launch_authority(
            str(workspace), str(workspace), [
                program, "-c", "open('escaped.txt', 'w').write('opaque')",
            ], identity)


@pytest.mark.parametrize("arguments", [
    ["--version"],
    ["-c", "print('read only')"],
])
def test_launch_preserves_safe_version_qualified_python_commands(
        tmp_path, arguments):
    workspace = tmp_path / "safe-versioned-python-repo"
    workspace.mkdir()
    _activate_command_contract(workspace)
    identity = {
        "schema": governed_commands.IDENTITY_SCHEMA,
        "run_id": "run-r0007", "task_id": "safe-versioned-python",
    }
    program = f"python{sys.version_info.major}.{sys.version_info.minor}"

    authority = governed_commands._governed_launch_authority(
        str(workspace), str(workspace), [program, *arguments], identity)
    assert authority["identity"] == identity


def test_version_qualified_python_keeps_analyzer_policy_parity():
    program = f"python{sys.version_info.major}.{sys.version_info.minor}"
    read_only = contract_engine.build_contract("review", read_only=True)

    allowed, reason = contract_engine.screen_tool(
        read_only, "exec_command",
        {"cmd": f'{program} -c "print(1)"'}, None)
    assert allowed is False
    assert "every shell command tool is blocked" in reason
    allowed, reason = contract_engine.screen_tool(
        read_only, "exec_command", {
            "cmd": (f'{program} -c "open(\'escaped.txt\', '
                    "'w').write('opaque')\"")
        }, None)
    assert allowed is False
    assert "read-only" in reason

    _, opaque = contract_engine._analyze(
        f'xargs {program} -c "print(1)"')
    assert opaque is not None
    assert opaque[0] == "destructive"
    assert contract_engine.deny_violation(
        f'{program} -c "print(\'git push\')"', ["git push"]) == "git push"
    assert contract_engine._analyze(
        f"{program} taskplane/tp.py findings")[1][0] == "launcher"


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


def _checkpoint_workspace(tmp_path):
    workspace = tmp_path / "checkpoint-repo"
    proof = workspace / "taskplane" / "tests" / "test_focused.py"
    proof.parent.mkdir(parents=True)
    proof.write_text("def test_focused():\n    assert True\n", encoding="utf-8")
    (workspace / "taskplane" / "checkpoint.py").write_text(
        "CHECKPOINT_FIXTURE = True\n", encoding="utf-8")
    (workspace / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "e@e"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "t"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "checkpoint proof"],
                   cwd=workspace, check=True)
    contract = contract_engine.build_contract(
        "checkpoint-task", scope=[str(workspace)], tools=["exec_command"],
        plan_minted=True)
    contract_engine.activate(str(workspace), contract, snapshot=None)
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True,
        encoding="utf-8", errors="replace").strip()
    argv = ["python3", "-m", "pytest", "-q",
            "taskplane/tests/test_focused.py"]
    spec = {
        "schema": checkpoint.CHECKPOINT_SCHEMA,
        "checkpoint_id": "cp-r0010-ac-1",
        "phase": "build",
        "ac_ids": ["AC-1"],
        "predecessor_checkpoint_ids": [],
        "worktree_revision": revision,
        "declared_scope": ["taskplane/checkpoint.py", "taskplane/tests/**"],
        "focused_proof": {
            "path": "taskplane/tests/test_focused.py", "argv": argv,
        },
        "ratchet_baseline": {"cycle_count": 0},
    }
    return workspace, spec, argv


def _checkpoint_command_result(workspace, argv, *, state="succeeded",
                               exit_code=0):
    identity = {
        "schema": governed_commands.IDENTITY_SCHEMA,
        "run_id": "run-r0010", "task_id": "checkpoint-task",
    }
    command_fingerprint = governed_commands._canonical_digest(argv)
    runtime_fingerprint = hashlib.sha256(
        command_fingerprint.encode("utf-8")).hexdigest()
    revision = argv[argv.index(checkpoint._PYTEST_REVISION_OPTION) + 1]
    output = checkpoint._OBSERVED_REVISION_PREFIX + revision + "\n"
    output += "1 passed\n" if state == "succeeded" else "1 failed\n"
    output_bytes = output.encode("utf-8")
    output_digest = hashlib.sha256(output_bytes).hexdigest()
    event = {
        "schema": "taskplane.command-event/v1", "handle": "a" * 32,
        "revision": 4, "state": state, "reason": state,
        "exit_code": exit_code, "elapsed_ms": 12, "output_delta": output,
        "artifact": {"path": "artifacts/output.log",
                     "sha256": output_digest, "bytes": len(output_bytes),
                     "truncated": False},
        "delivery_key": "delivery", "identity": identity,
        "delivery_receipt": {
            "schema": "taskplane.command-delivery-receipt/v1",
            "consumer": "checkpoint:cp-r0010-ac-1",
            "delivery_key": "delivery", "revision": 4,
        },
    }
    snapshot = {
        "schema": "taskplane.command-state/v1", "handle": "a" * 32,
        "workspace_fingerprint": hashlib.sha256(
            str(workspace.resolve()).encode("utf-8")).hexdigest(),
        "authorization_fingerprint": "b" * 64,
        "command_fingerprint": runtime_fingerprint,
        "state": state, "revision": 4, "identity": identity,
        "exit_code": exit_code, "reason": state, "artifact": event["artifact"],
        "output_summary": output, "output_digest": output_digest,
        "metrics": {"output_redactions": 0},
    }
    return {
        "schema": governed_commands.RESULT_SCHEMA, "action": "wait",
        "handle": "a" * 32, "identity": identity,
        "lifecycle_states": ["created", "running", state],
        "snapshot": snapshot, "event": event,
    }


def _checkpoint_runtime_argv(workspace, spec):
    return checkpoint.validate_checkpoint_spec(
        str(workspace), spec)["focused_proof"]["argv"]


def _checkpoint_submit_task(spec):
    return {
        "id": "checkpoint-task",
        "scope": list(spec["declared_scope"]),
        "tests": "true",
        "criteria": ["AC-1"],
        "checkpoint": {
            "checkpoint_id": spec["checkpoint_id"],
            "phase": spec["phase"],
            "ac_ids": list(spec["ac_ids"]),
            "predecessor_checkpoint_ids": [],
            "focused_proof": {
                "path": spec["focused_proof"]["path"],
                "argv": list(spec["focused_proof"]["argv"]),
            },
            "ratchet_baseline": dict(spec["ratchet_baseline"]),
        },
    }


def _save_checkpoint_submit_loop(workspace, task):
    loop.save(str(workspace), {
        "governance_revision": 2,
        "submission_required": True,
        "graph_governance": False,
        "goal": "checkpoint wiring",
        "parallel": False,
        "step": "execute",
        "tasks": [task],
        "current_task": 0,
    })


def _launch_engine_authorized_checkpoint(workspace, *, run_id="loop",
                                         task_id="checkpoint-task"):
    lifecycle = "loop-submit-checkpoint:" + task_id
    capability = governed_commands.mint_semantic_checkpoint_authorization(
        str(workspace), lifecycle_authorization=lifecycle,
        run_id=run_id, task_id=task_id)
    launched = governed_commands.execute(str(workspace), "checkpoint", {
        "authorization": lifecycle,
        "checkpoint_authority": capability,
        "run_id": run_id,
        "task_id": task_id,
    })
    return lifecycle, capability, launched


def test_submit_checkpoint_runs_live_runtime_and_mints_receipt(
        tmp_path, monkeypatch):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    task = _checkpoint_submit_task(spec)
    _save_checkpoint_submit_loop(workspace, task)
    monkeypatch.setattr(loop.runtime_eval, "guide_loop", lambda *a, **k: {
        "schema": "taskplane.runtime-guidance/v1",
        "status": "on_path", "step": "execute",
    })

    submitted = loop.submit(str(workspace), "pass")

    assert submitted["submitted"] is True
    receipt = submitted["submission"]["checkpoint_receipt"]
    assert receipt["producer"] == "taskplane.checkpoint-engine/v1"
    assert receipt["worktree_revision"] == spec["worktree_revision"]
    assert receipt["identity"]["checkpoint_id"] == spec["checkpoint_id"]
    assert receipt["verdict"] == "green"


def test_submit_checkpoint_missing_proof_refuses_by_name_before_runtime(
        tmp_path, monkeypatch):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    task = _checkpoint_submit_task(spec)
    missing = "taskplane/tests/missing_submit_checkpoint.py"
    task["checkpoint"]["focused_proof"] = {
        "path": missing,
        "argv": ["python3", "-m", "pytest", "-q", missing],
    }
    _save_checkpoint_submit_loop(workspace, task)
    monkeypatch.setattr(loop.runtime_eval, "guide_loop", lambda *a, **k: {
        "schema": "taskplane.runtime-guidance/v1",
        "status": "on_path", "step": "execute",
    })
    monkeypatch.setattr(
        loop.governed_commands, "execute",
        lambda *a, **k: pytest.fail("runtime started before checkpoint preflight"))

    refused = loop.submit(str(workspace), "pass")

    assert refused["submitted"] is False
    assert missing in refused["error"]
    assert loop.load(str(workspace)).get("_submission") is None


def test_checkpoint_action_rejects_forged_authority_arbitrary_task_and_step(
        tmp_path, monkeypatch):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    current = _checkpoint_submit_task(spec)
    later = _checkpoint_submit_task(spec)
    later["id"] = "later-checkpoint"
    _save_checkpoint_submit_loop(workspace, current)
    state = loop.load(str(workspace))
    state["tasks"].append(later)
    loop.save(str(workspace), state)
    monkeypatch.setattr(
        governed_commands, "_prepare_checkpoint_sandbox",
        lambda *_a, **_k: pytest.fail("forged authority reached launch"))

    with pytest.raises(governed_commands.GovernedCommandError,
                       match="engine-minted authorization"):
        governed_commands.execute(str(workspace), "checkpoint", {
            "authorization": "loop-submit-checkpoint:checkpoint-task",
            "checkpoint_authority": "forged",
            "run_id": "loop", "task_id": "checkpoint-task",
        })
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="current Plan-selected task"):
        governed_commands.mint_semantic_checkpoint_authorization(
            str(workspace),
            lifecycle_authorization="loop-submit-checkpoint:later-checkpoint",
            run_id="loop", task_id="later-checkpoint")

    state = loop.load(str(workspace))
    state["step"] = "evaluate"
    loop.save(str(workspace), state)
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="current execute/fix submission step"):
        governed_commands.mint_semantic_checkpoint_authorization(
            str(workspace),
            lifecycle_authorization="loop-submit-checkpoint:checkpoint-task",
            run_id="loop", task_id="checkpoint-task")


def test_checkpoint_authorization_is_single_use_and_dependency_ready(
        tmp_path, monkeypatch):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    predecessor = _checkpoint_submit_task(spec)
    predecessor["id"] = "predecessor"
    current = _checkpoint_submit_task(spec)
    current["deps"] = ["predecessor"]
    _save_checkpoint_submit_loop(workspace, current)
    state = loop.load(str(workspace))
    state["tasks"] = [predecessor, current]
    state["current_task"] = 1
    loop.save(str(workspace), state)
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="unmet dependencies"):
        governed_commands.mint_semantic_checkpoint_authorization(
            str(workspace),
            lifecycle_authorization="loop-submit-checkpoint:checkpoint-task",
            run_id="loop", task_id="checkpoint-task")

    state["tasks"][0]["status"] = "passed"
    loop.save(str(workspace), state)
    lifecycle = "loop-submit-checkpoint:checkpoint-task"
    capability = governed_commands.mint_semantic_checkpoint_authorization(
        str(workspace), lifecycle_authorization=lifecycle,
        run_id="loop", task_id="checkpoint-task")
    monkeypatch.setattr(
        governed_commands, "_prepare_checkpoint_sandbox",
        lambda *_a, **_k: (_ for _ in ()).throw(
            governed_commands.GovernedCommandUnavailable(
                "test_stop", "stop after capability consumption")))
    first = governed_commands.execute(str(workspace), "checkpoint", {
        "authorization": lifecycle, "checkpoint_authority": capability,
        "run_id": "loop", "task_id": "checkpoint-task",
    })
    assert first["reason_code"] == "test_stop"
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="already consumed"):
        governed_commands.execute(str(workspace), "checkpoint", {
            "authorization": lifecycle, "checkpoint_authority": capability,
            "run_id": "loop", "task_id": "checkpoint-task",
        })


def test_checkpoint_authority_ignores_unrelated_parallel_runtime_progress(
        tmp_path):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    selected = _checkpoint_submit_task(spec)
    selected["status"] = "running"
    unrelated = _checkpoint_submit_task(spec)
    unrelated["id"] = "unrelated-task"
    unrelated["status"] = "running"
    _save_checkpoint_submit_loop(workspace, selected)
    state = loop.load(str(workspace))
    state["parallel"] = True
    state["tasks"].append(unrelated)
    loop.save(str(workspace), state)
    _spec, authority = governed_commands._checkpoint_plan_authority(
        str(workspace), "loop", "checkpoint-task")

    state = loop.load(str(workspace))
    state["tasks"][1]["status"] = "built"
    state["tasks"][1]["target_commit"] = "a" * 40
    loop.save(str(workspace), state)

    assert governed_commands._assert_checkpoint_authority_current(
        str(workspace), authority) == authority


@pytest.mark.parametrize("mutation", ["plan", "contract"])
def test_checkpoint_post_proof_authority_rejects_mid_run_mutation(
        tmp_path, mutation):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    proof = workspace / spec["focused_proof"]["path"]
    proof.write_text(
        "import time\n\ndef test_focused():\n"
        "    time.sleep(0.5)\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", spec["focused_proof"]["path"]],
                   cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "slow checkpoint proof"],
                   cwd=workspace, check=True)
    task = _checkpoint_submit_task(spec)
    _save_checkpoint_submit_loop(workspace, task)
    lifecycle, _capability, launched = \
        _launch_engine_authorized_checkpoint(workspace)

    if mutation == "plan":
        state = loop.load(str(workspace))
        state["tasks"][0]["criteria"].append("mutated-mid-proof")
        loop.save(str(workspace), state)
    else:
        active = contract_engine.load_active(str(workspace))
        active["budget"]["note"] = "mutated-mid-proof"
        Path(contract_engine.active_contract_path(str(workspace))).write_text(
            json.dumps(active), encoding="utf-8")
    observed = governed_commands.execute(str(workspace), "wait", {
        "authorization": lifecycle, "handle": launched["handle"],
        "consumer": "checkpoint:cp-r0010-ac-1", "timeout": 10,
    })

    assert observed["snapshot"]["state"] == "failed"
    assert observed["snapshot"]["reason"] == _minimized(
        "REASON", "semantic checkpoint unavailable: checkpoint_plan_changed")
    with pytest.raises(governed_commands.GovernedCommandError):
        governed_commands.semantic_checkpoint_execution_evidence(
            str(workspace), lifecycle, launched["handle"])


def test_submit_checkpoint_rejects_caller_authored_receipt(
        tmp_path, monkeypatch):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    task = _checkpoint_submit_task(spec)
    task["checkpoint"]["receipt"] = {"verdict": "green"}
    _save_checkpoint_submit_loop(workspace, task)
    monkeypatch.setattr(loop.runtime_eval, "guide_loop", lambda *a, **k: {
        "schema": "taskplane.runtime-guidance/v1",
        "status": "on_path", "step": "execute",
    })
    monkeypatch.setattr(
        loop.governed_commands, "execute",
        lambda *a, **k: pytest.fail("forged receipt reached runtime"))

    refused = loop.submit(str(workspace), "pass")

    assert refused["submitted"] is False
    assert "engine-owned fields: receipt" in refused["error"]
    assert loop.load(str(workspace)).get("_submission") is None


def test_submit_checkpoint_red_blocks_receipt_and_later_submission(
        tmp_path, monkeypatch):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    proof = workspace / spec["focused_proof"]["path"]
    proof.write_text("def test_focused():\n    assert False\n", encoding="utf-8")
    subprocess.run(["git", "add", spec["focused_proof"]["path"]],
                   cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "red checkpoint proof"],
                   cwd=workspace, check=True)
    task = _checkpoint_submit_task(spec)
    _save_checkpoint_submit_loop(workspace, task)
    monkeypatch.setattr(loop.runtime_eval, "guide_loop", lambda *a, **k: {
        "schema": "taskplane.runtime-guidance/v1",
        "status": "on_path", "step": "execute",
    })

    refused = loop.submit(str(workspace), "pass")

    assert refused["submitted"] is False
    assert "later phases stopped" in refused["error"]
    assert loop.load(str(workspace)).get("_submission") is None


def _run_governed_checkpoint_command(workspace, argv, task_id):
    authorization = "agent:checkpoint"
    launched = governed_commands.execute(str(workspace), "launch", {
        "authorization": authorization,
        "argv": argv,
        "run_id": "run-r0010",
        "task_id": task_id,
    })
    return governed_commands.execute(str(workspace), "wait", {
        "authorization": authorization,
        "handle": launched["handle"],
        "consumer": "checkpoint:cp-r0010-ac-1",
        "timeout": 10,
    })


def test_checkpoint_receipt_refuses_generic_runtime_without_post_proof_receipt(
        tmp_path):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    runtime_argv = _checkpoint_runtime_argv(workspace, spec)
    result = _run_governed_checkpoint_command(
        workspace, runtime_argv, "checkpoint-real-proof")

    assert result["event"]["state"] == "succeeded"
    assert result["event"]["output_delta"].startswith(
        "[REDACTED]\n[OUTPUT_MINIMIZED ")
    with pytest.raises(
            checkpoint.CheckpointReceiptError,
            match="requires an exact semantic post-proof receipt"):
        checkpoint.validate_and_mint(str(workspace), spec, result)


def test_checkpoint_receipt_is_engine_minted_and_exact_revision_bound(tmp_path):
    workspace, spec, argv = _checkpoint_workspace(tmp_path)
    runtime_argv = _checkpoint_runtime_argv(workspace, spec)
    receipt = checkpoint.validate_and_mint(
        str(workspace), spec,
        _checkpoint_command_result(workspace, runtime_argv))

    assert receipt["schema"] == checkpoint.CHECKPOINT_RECEIPT_SCHEMA
    assert receipt["producer"] == "taskplane.checkpoint-engine/v1"
    assert receipt["verdict"] == "green"
    assert receipt["worktree_revision"] == spec["worktree_revision"]
    assert receipt["identity"] == {
        "run_id": "run-r0010", "task_id": "checkpoint-task",
        "checkpoint_id": "cp-r0010-ac-1", "ac_ids": ["AC-1"],
    }
    assert receipt["command"]["argv"] == runtime_argv
    assert receipt["command"]["cwd"] == str(workspace.resolve())
    expected_output = (
        checkpoint._OBSERVED_REVISION_PREFIX + spec["worktree_revision"] +
        "\n1 passed\n").encode()
    assert receipt["output"] == {
        "sha256": hashlib.sha256(expected_output).hexdigest(),
        "bytes": len(expected_output), "truncated": False,
        "redactions": 0,
    }
    assert len(receipt["engine_fingerprint"]) == 64
    assert len(receipt["active_contract_fingerprint"]) == 64
    assert len(receipt["environment_fingerprint"]) == 64
    assert receipt["receipt_digest"] == checkpoint.receipt_digest(receipt)

    subprocess.run(["git", "commit", "--allow-empty", "-qm", "new tip"],
                   cwd=workspace, check=True)
    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="exact HEAD|stale"):
        checkpoint.validate_and_mint(
            str(workspace), spec,
            _checkpoint_command_result(workspace, runtime_argv))


def test_checkpoint_receipt_rejects_caller_forgery_and_red_stops_later_phases(
        tmp_path):
    workspace, spec, argv = _checkpoint_workspace(tmp_path)
    runtime_argv = _checkpoint_runtime_argv(workspace, spec)
    forged = dict(spec)
    forged["receipt"] = {"verdict": "green"}
    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="unknown checkpoint fields.*receipt"):
        checkpoint.validate_and_mint(
            str(workspace), forged,
            _checkpoint_command_result(workspace, runtime_argv))

    forged_result = _checkpoint_command_result(workspace, runtime_argv)
    forged_result["producer"] = "caller"
    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="caller-authored fields.*producer"):
        checkpoint.validate_and_mint(str(workspace), spec, forged_result)

    partial_cleanup = _checkpoint_command_result(workspace, runtime_argv)
    partial_cleanup["cleanup_receipt"] = {}
    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="cleanup envelope is incomplete"):
        checkpoint.validate_and_mint(
            str(workspace), spec, partial_cleanup)

    forged_cleanup = _run_governed_checkpoint_command(
        workspace, runtime_argv, "checkpoint-cleanup-forgery")
    forged_cleanup["cleanup_receipt"]["receipt_digest"] = "0" * 64
    forged_cleanup["cleanup_evidence"]["receipt_digest"] = "0" * 64
    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="cleanup envelope is invalid"):
        checkpoint.validate_and_mint(
            str(workspace), spec, forged_cleanup)

    red = _checkpoint_command_result(
        workspace, runtime_argv, state="failed", exit_code=1)
    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="focused_proof.*failed.*later phases stopped"):
        checkpoint.validate_and_mint(str(workspace), spec, red)


def test_checkpoint_receipt_rejects_successful_non_proof_command(tmp_path):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    echo_argv = ["/bin/echo", spec["focused_proof"]["path"]]
    spec["focused_proof"]["argv"] = echo_argv
    echo_result = _run_governed_checkpoint_command(
        workspace, echo_argv, "checkpoint-echo-attack")

    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="focused_proof.*pytest"):
        checkpoint.validate_and_mint(
            str(workspace), spec, echo_result)

    spec["focused_proof"]["argv"] = [
        "pytest", "-pno:taskplane.checkpoint", "-q",
        spec["focused_proof"]["path"],
    ]
    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="exact revision"):
        checkpoint.validate_and_mint(
            str(workspace), spec, echo_result)


def test_checkpoint_receipt_rejects_runtime_result_from_prior_revision(tmp_path):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    old_runtime_argv = _checkpoint_runtime_argv(workspace, spec)
    old_result = _checkpoint_command_result(workspace, old_runtime_argv)

    subprocess.run(["git", "commit", "--allow-empty", "-qm", "new tip"],
                   cwd=workspace, check=True)
    spec["worktree_revision"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True,
        encoding="utf-8", errors="replace").strip()

    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="exact revision"):
        checkpoint.validate_and_mint(str(workspace), spec, old_result)


def test_checkpoint_receipt_rejects_predeclared_future_revision(tmp_path):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    revision_a = spec["worktree_revision"]
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "future tip"],
                   cwd=workspace, check=True)
    revision_b = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True,
        encoding="utf-8", errors="replace").strip()
    subprocess.run(["git", "checkout", "-q", revision_a], cwd=workspace,
                   check=True)

    argv_a = _checkpoint_runtime_argv(workspace, spec)
    argv_claiming_b = [item.replace(revision_a, revision_b)
                       for item in argv_a]
    result_from_a = _run_governed_checkpoint_command(
        workspace, argv_claiming_b, "checkpoint-future-revision-attack")
    assert result_from_a["event"]["state"] == "failed"
    assert result_from_a["event"]["output_delta"].startswith(
        "[REDACTED]\n[OUTPUT_MINIMIZED ")

    subprocess.run(["git", "checkout", "-q", revision_b], cwd=workspace,
                   check=True)
    spec["worktree_revision"] = revision_b
    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="later phases stopped"):
        checkpoint.validate_and_mint(str(workspace), spec, result_from_a)


@pytest.mark.parametrize("change", ["unstaged", "staged", "untracked"])
def test_checkpoint_receipt_rejects_dirty_declared_scope(tmp_path, change):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    runtime_argv = _checkpoint_runtime_argv(workspace, spec)
    if change == "untracked":
        (workspace / "taskplane" / "tests" / "untracked_scope.py").write_text(
            "DIRTY_SCOPE = True\n", encoding="utf-8")
    else:
        (workspace / "taskplane" / "checkpoint.py").write_text(
            "CHECKPOINT_FIXTURE = False\n", encoding="utf-8")
        if change == "staged":
            subprocess.run(["git", "add", "taskplane/checkpoint.py"],
                           cwd=workspace, check=True)

    dirty_result = _run_governed_checkpoint_command(
        workspace, runtime_argv, f"checkpoint-dirty-{change}-scope")
    assert dirty_result["event"]["state"] == "failed"
    with pytest.raises(checkpoint.CheckpointReceiptError,
                       match="declared scope"):
        checkpoint.validate_and_mint(str(workspace), spec, dirty_result)


def test_semantic_sidecar_rejects_redigest_and_replay_against_current_state(
        tmp_path):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    task = _checkpoint_submit_task(spec)
    _save_checkpoint_submit_loop(workspace, task)
    receipt = loop._run_submit_checkpoint(
        str(workspace), loop.load(str(workspace)), task, str(workspace))
    lifecycle = "loop-submit-checkpoint:checkpoint-task"
    handle = receipt["command"]["handle"]
    sealed = governed_commands._sealed_runtime_evidence(
        governed_commands._runtime_root(str(workspace)), handle)
    path = sealed["evidence"]["semantic-checkpoint-receipt"]
    original = json.loads(path.read_text(encoding="utf-8"))

    redigested = dict(original)
    redigested["plan_fingerprint"] = "0" * 64
    material = {key: value for key, value in redigested.items()
                if key != "receipt_digest"}
    redigested["receipt_digest"] = \
        governed_commands._canonical_digest(material)
    path.write_text(json.dumps(redigested), encoding="utf-8")
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="execution receipt"):
        governed_commands.semantic_checkpoint_execution_evidence(
            str(workspace), lifecycle, handle)

    path.write_text(json.dumps(original), encoding="utf-8")
    state = loop.load(str(workspace))
    state["tasks"][0]["criteria"].append("advanced-after-receipt")
    loop.save(str(workspace), state)
    with pytest.raises(governed_commands.GovernedCommandError):
        governed_commands.semantic_checkpoint_execution_evidence(
            str(workspace), lifecycle, handle)


def _semantic_descendant_workspace(tmp_path, *, escape_group):
    workspace, spec, _ = _checkpoint_workspace(tmp_path)
    proof = workspace / spec["focused_proof"]["path"]
    marker = "tp-semantic-descendant-" + tmp_path.name
    pid_path = tmp_path / "semantic-descendant.pid"
    proof.write_text(
        "import subprocess, sys, time\n\n"
        "def test_focused():\n"
        "    child = subprocess.Popen(\n"
        "        [sys.executable, '-c', 'import time; time.sleep(60)',\n"
        f"         {marker!r}],\n"
        f"        start_new_session={escape_group!r})\n"
        f"    open({str(pid_path)!r}, 'w').write(str(child.pid))\n"
        "    time.sleep(0.35)\n"
        "    assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", spec["focused_proof"]["path"]],
                   cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "descendant checkpoint proof"],
                   cwd=workspace, check=True)
    task = _checkpoint_submit_task(spec)
    task["checkpoint"]["focused_proof"]["argv"].insert(4, "-s")
    _save_checkpoint_submit_loop(workspace, task)
    return workspace, task, pid_path


def _assert_process_absent(pid_path):
    assert pid_path.is_file()
    pid = int(pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail(f"semantic checkpoint descendant {pid} leaked")


def test_semantic_checkpoint_reaps_success_descendants(tmp_path, monkeypatch):
    workspace, task, pid_path = _semantic_descendant_workspace(
        tmp_path, escape_group=False)
    receipt = loop._run_submit_checkpoint(
        str(workspace), loop.load(str(workspace)), task, str(workspace))
    assert receipt["verdict"] == "green"
    handle = receipt["command"]["handle"]
    runtime_root = governed_commands._runtime_root(str(workspace))
    assert not (runtime_root / handle).exists()
    monkeypatch.setattr(
        governed_commands.owned_cleanup, "cleanup_manifest",
        lambda *_args, **_kwargs: pytest.fail(
            "terminal replay re-ran destructive cleanup"))
    replayed = governed_commands.execute(str(workspace), "show", {
        "authorization": "loop-submit-checkpoint:checkpoint-task",
        "handle": handle,
    })
    assert replayed["snapshot"]["state"] == "succeeded"
    assert replayed["cleanup_evidence"]["leak_count"] == 0
    _assert_process_absent(pid_path)


def test_semantic_checkpoint_kills_and_refuses_setsid_descendant(tmp_path):
    workspace, _task, pid_path = _semantic_descendant_workspace(
        tmp_path, escape_group=True)
    lifecycle, _capability, launched = \
        _launch_engine_authorized_checkpoint(workspace)
    observed = governed_commands.execute(str(workspace), "wait", {
        "authorization": lifecycle, "handle": launched["handle"],
        "consumer": "checkpoint:cp-r0010-ac-1", "timeout": 10,
    })
    snapshot = observed["snapshot"]
    assert snapshot["state"] == "failed"
    assert snapshot["reason"] == _minimized(
        "REASON",
        "semantic checkpoint unavailable: checkpoint_process_tree_escape")
    _assert_process_absent(pid_path)
