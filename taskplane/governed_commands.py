"""Supported composition root for durable governed host commands.

The durable lifecycle remains owned by :mod:`command_runtime`; host launch,
wait, reconnect, and cancellation remain owned by :mod:`command_adapters`.
This module validates the closed public request and joins those owners for the
CLI and Evaluate-Loop roots.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
from typing import Mapping

# ``tp.py`` is a supported direct executable and imports this module from the
# taskplane directory.  Make the namespace package discoverable before using
# package-qualified imports so this path and ``python -m taskplane.tp`` share
# one dependency graph instead of mixing top-level and package modules.
if not __package__:
    package_root = str(Path(__file__).resolve().parent.parent)
    if package_root not in sys.path:
        sys.path.insert(0, package_root)

from taskplane.command_adapters import (
    CommandAdapter,
    HostLaunch,
    cancel_detached_process,
    detached_process_binding,
    detached_process_groups_supported,
    detached_process_is_live,
)
from taskplane.command_runtime import (
    CommandRuntime,
    MAX_EVENT_OUTPUT,
    TERMINAL_STATES,
)
from taskplane import taskplane_lite as contract_engine


RESULT_SCHEMA = "taskplane.governed-command-result/v1"
IDENTITY_SCHEMA = "taskplane.governed-command-identity/v1"
DISPATCH_INTENT_SCHEMA = \
    "taskplane.native-agent-dispatch-intent-telemetry/v1"
_CAPTURE_LIMIT = MAX_EVENT_OUTPUT + 1
_HANDLE_FIELDS = frozenset({"authorization", "handle"})
_ACTION_FIELDS = {
    "dispatch": frozenset({
        "authorization", "consumer", "host", "payload", "run_id",
        "task_id", "wave_id",
    }),
    "launch": frozenset({
        "authorization", "argv", "cwd", "deadline", "host", "run_id",
        "task_id", "wave_id",
    }),
    "wait": _HANDLE_FIELDS | {"consumer", "timeout"},
    "reconnect": _HANDLE_FIELDS,
    "show": _HANDLE_FIELDS,
    "cancel": _HANDLE_FIELDS,
}
_IDENTITY_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


class GovernedCommandError(RuntimeError):
    pass


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as target:
            json.dump(dict(value), target, sort_keys=True, separators=(",", ":"))
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _runtime_root(workspace: str) -> Path:
    return Path(workspace).resolve() / ".taskplane" / "command-runtime-v1"


def _dispatch_intent_root(workspace: str) -> Path:
    return (Path(workspace).resolve() / ".taskplane" /
            "dispatch-intent-telemetry-v1")


def _closed_request(action: str, request: object) -> dict:
    allowed = _ACTION_FIELDS.get(str(action))
    if allowed is None:
        raise GovernedCommandError(f"unsupported governed command action {action!r}")
    if not isinstance(request, Mapping):
        raise GovernedCommandError("governed command request must be an object")
    value = dict(request)
    unknown = set(value) - allowed
    if unknown:
        raise GovernedCommandError(
            "governed command request has unknown fields: " +
            ", ".join(sorted(unknown)))
    if action == "launch":
        missing = {"authorization", "argv", "run_id", "task_id"} - set(value)
    elif action == "dispatch":
        missing = {
            "authorization", "consumer", "payload", "run_id", "task_id",
        } - set(value)
    else:
        missing = _HANDLE_FIELDS - set(value)
    if missing:
        raise GovernedCommandError(
            "governed command request is missing: " + ", ".join(sorted(missing)))
    if not str(value.get("authorization") or "").strip():
        raise GovernedCommandError("governed command authorization is required")
    if action in {"launch", "dispatch"}:
        for field in ("run_id", "task_id"):
            identity = value.get(field)
            if (not isinstance(identity, str) or
                    _IDENTITY_COMPONENT.fullmatch(identity) is None):
                raise GovernedCommandError(
                    f"governed command {field} is invalid")
    return value


def _validated_cwd(workspace: str, requested: object) -> str:
    root = Path(workspace).resolve()
    cwd = Path(str(requested or root)).resolve()
    if cwd != root and root not in cwd.parents:
        raise GovernedCommandError("governed command cwd escapes the workspace")
    if not cwd.is_dir():
        raise GovernedCommandError("governed command cwd is unavailable")
    return str(cwd)


def _control_path(root: Path, handle: str) -> Path:
    return root / handle / "control.json"


def _read_control(root: Path, handle: str) -> dict:
    try:
        value = json.loads(_control_path(root, handle).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GovernedCommandError("durable command control binding is unavailable") from exc
    if (not isinstance(value, dict) or set(value) != {
            "schema", "host", "binding", "binding_digest"} or
            value.get("schema") != "taskplane.governed-command-control/v1" or
            not isinstance(value.get("binding"), Mapping) or
            _canonical_digest(value["binding"]) !=
            value.get("binding_digest")):
        raise GovernedCommandError("durable command control binding is invalid")
    return value


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")).hexdigest()


def _workspace_rooted_screen_command(
        workspace: str, cwd: str, argv: list[str]) -> tuple[str, object]:
    """Return direct argv with concrete write targets rooted for screening.

    Contract paths are relative to the governed workspace, while a command's
    relative path arguments resolve from its requested cwd.  Screening the raw
    argv against either base can therefore reinterpret one side.  Rewrite only
    targets identified by the command analyzer to their canonical absolute
    paths; subprocess execution still receives the original argv unchanged.
    """
    command = shlex.join(argv)
    targets, opaque = contract_engine._analyze(command)
    rooted = list(argv)
    used: set[int] = set()

    for target in targets:
        target = str(target)
        resolved = os.path.realpath(
            target if os.path.isabs(target) else os.path.join(cwd, target))
        matched = False
        for index, argument in enumerate(rooted[1:], start=1):
            if index in used:
                continue
            replacement = None
            if argument == target:
                replacement = resolved
            elif argument.endswith("=" + target):
                replacement = argument[:-len(target)] + resolved
            elif argument in {"-o" + target, "-O" + target,
                              "-t" + target}:
                replacement = argument[:2] + resolved
            if replacement is not None:
                rooted[index] = replacement
                used.add(index)
                matched = True
                break
        if not matched:
            raise GovernedCommandError(
                "governed command write target cannot be rooted safely")
    return shlex.join(rooted), opaque


def _raw_command_policy_denial(
        contract: Mapping, command: str) -> str | None:
    """Apply every active contract's deny policy to the original argv."""
    members = contract.get("_union")
    if members:
        for member in members:
            reason = _raw_command_policy_denial(member, command)
            if reason:
                return (f"[{member.get('task_id', '?')}] {reason} "
                        f"(most-restrictive union of {len(members)} active "
                        "contracts)")
        return None
    coding = contract.get("coding") or {}
    pattern = contract_engine.deny_violation(
        command, (coding.get("command_policy") or {}).get("deny") or [])
    if pattern:
        return f"command matches deny pattern '{pattern}'"
    return None


def _governed_launch_authority(
        workspace: str, cwd: str, argv: list[str], identity: Mapping,
        *, expected: Mapping | None = None) -> dict:
    """Screen direct argv against one exact active contract at each boundary."""
    contract = contract_engine.load_active(workspace)
    if not isinstance(contract, dict):
        raise GovernedCommandError(
            "governed command launch requires an exact active contract or "
            "signed lease")
    raw_denial = _raw_command_policy_denial(contract, shlex.join(argv))
    if raw_denial:
        raise GovernedCommandError(
            "governed command launch is outside its active contract: "
            f"{raw_denial}")
    command, opaque = _workspace_rooted_screen_command(
        workspace, cwd, argv)
    if opaque and opaque[0] == "interpreter":
        raise GovernedCommandError(
            "governed command launch rejects opaque interpreter or script argv")
    allowed, reason = contract_engine.screen_tool(
        contract, "exec_command", {"cmd": command}, workspace)
    if not allowed:
        raise GovernedCommandError(
            f"governed command launch is outside its active contract: {reason}")
    proof = {
        "schema": "taskplane.governed-command-launch-authority/v1",
        "workspace": str(Path(workspace).resolve()),
        "cwd": str(Path(cwd).resolve()),
        "identity": dict(identity),
        "contract_fingerprint": _canonical_digest(contract),
        "command_fingerprint": _canonical_digest(argv),
    }
    proof["fingerprint"] = _canonical_digest(proof)
    if expected is not None and dict(expected) != proof:
        raise GovernedCommandError(
            "governed command authority changed before worker execution")
    return proof


def _runtime(workspace: str, authorization: str) -> CommandRuntime:
    return CommandRuntime(str(_runtime_root(workspace)), workspace=workspace,
                          authorization=authorization)


def _adapter(workspace: str, authorization: str, *, host: str,
             launcher, binding: Mapping[str, object] | None = None) \
        -> CommandAdapter:
    adapter = CommandAdapter(
        host=host, runtime=_runtime(workspace, authorization),
        launcher=launcher, canceller=cancel_detached_process)
    if binding is not None:
        adapter.restore_binding(
            str(binding["handle"]), dict(binding["binding"]))
    return adapter


def _snapshot_result(action: str, adapter: CommandAdapter, handle: str,
                     **extra) -> dict:
    snapshot = adapter.snapshot(handle)
    return {
        "schema": RESULT_SCHEMA,
        "action": action,
        "handle": handle,
        "identity": snapshot.get("identity"),
        "lifecycle_states": [
            row.get("state") for row in snapshot.get("lifecycle") or []
        ],
        "snapshot": snapshot,
        **extra,
    }


def execute(workspace: str, action: str, request: object) -> dict:
    """Execute one closed lifecycle action through adapter and runtime."""
    workspace = str(Path(workspace).resolve())
    value = _closed_request(action, request)
    authorization = str(value["authorization"])
    root = _runtime_root(workspace)

    if action == "dispatch":
        payload = value.get("payload")
        consumer = value.get("consumer")
        if not isinstance(payload, Mapping):
            raise GovernedCommandError(
                "governed native dispatch payload must be an object")
        if not isinstance(consumer, str) or not consumer.strip():
            raise GovernedCommandError(
                "governed native dispatch consumer is required")
        host = str(value.get("host") or "")
        if host != "native-agent":
            raise GovernedCommandError(
                "governed native dispatch intent requires native-agent host")
        identity = {
            "schema": IDENTITY_SCHEMA,
            "run_id": value["run_id"],
            "task_id": value["task_id"],
        }
        material = {
            "schema": DISPATCH_INTENT_SCHEMA,
            "kind": "dispatch_intent",
            "transport": "native_agent",
            "identity": identity,
            "intended_consumer": consumer,
            "wave_id": value.get("wave_id"),
            "payload_fingerprint": _canonical_digest(dict(payload)),
            "wait_policy": dict(payload.get("wait_policy") or {}),
            "evidence": {
                "authoritative": False,
                "host_observed": False,
                "execution_observed": False,
                "delivery_observed": False,
                "may_satisfy_execution_gate": False,
                "may_satisfy_delivery_gate": False,
            },
        }
        intent_id = "intent-" + _canonical_digest(material)[:32]
        telemetry = {**material, "intent_id": intent_id}
        telemetry_path = _dispatch_intent_root(workspace) / f"{intent_id}.json"
        _atomic_json(telemetry_path, telemetry)
        return {**telemetry, "telemetry_path": str(telemetry_path)}

    if action == "launch":
        argv = value.get("argv")
        if (not isinstance(argv, (list, tuple)) or not argv or
                any(not isinstance(item, str) or not item for item in argv)):
            raise GovernedCommandError("governed command launch requires direct argv")
        cwd = _validated_cwd(workspace, value.get("cwd"))
        host = str(value.get("host") or "codex")
        if not detached_process_groups_supported():
            raise GovernedCommandError(
                "governed detached commands are unsupported on this host; "
                "no process was started")
        identity = {"schema": IDENTITY_SCHEMA,
                    "run_id": value["run_id"],
                    "task_id": value["task_id"]}
        authority = _governed_launch_authority(
            workspace, cwd, list(argv), identity)
        token = secrets.token_hex(16)
        handoff = root / "handoffs" / f"{token}.json"
        started: dict[str, object] = {}

        def launch(command: object, command_cwd: str) -> HostLaunch:
            worker_env = dict(os.environ)
            package_root = str(Path(__file__).resolve().parent.parent)
            prior_pythonpath = worker_env.get("PYTHONPATH", "")
            worker_env["PYTHONPATH"] = package_root + (
                os.pathsep + prior_pythonpath if prior_pythonpath else "")
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "_worker",
                 str(handoff)], cwd=workspace, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True, env=worker_env)
            binding = detached_process_binding(process, token=token)
            started.update({"process": process, "binding": binding})
            return HostLaunch(binding=binding)

        adapter = _adapter(workspace, authorization, host=host, launcher=launch)
        handle = adapter.launch(
            list(argv), cwd=cwd, deadline=value.get("deadline"),
            wave_id=value.get("wave_id"), identity=identity)
        binding = dict(started["binding"])
        control = {"schema": "taskplane.governed-command-control/v1",
                   "host": host, "binding": binding,
                   "binding_digest": _canonical_digest(binding)}
        _atomic_json(_control_path(root, handle), control)
        _atomic_json(handoff, {
            "schema": "taskplane.governed-command-handoff/v1",
            "workspace": workspace, "authorization": authorization,
            "root": str(root), "handle": handle, "argv": list(argv),
            "cwd": cwd, "deadline": value.get("deadline"),
            "identity": identity, "authority": authority,
        })
        return _snapshot_result("launch", adapter, handle)

    handle = str(value["handle"])
    control = _read_control(root, handle)
    binding = {"handle": handle, "binding": dict(control["binding"])}
    adapter = _adapter(
        workspace, authorization, host=str(control["host"]),
        launcher=lambda command, cwd: HostLaunch(binding={"unreachable": True}),
        binding=binding)
    if action == "wait":
        event = adapter.wait_next(
            handle, consumer=str(value.get("consumer") or "model"),
            timeout=(None if value.get("timeout") is None
                     else float(value["timeout"])))
        return _snapshot_result("wait", adapter, handle, event=event)
    if action == "reconnect":
        event = adapter.reconnect(
            handle, binding=dict(control["binding"]),
            ownership_check=detached_process_is_live)
        return _snapshot_result("reconnect", adapter, handle, event=event)
    if action == "cancel":
        event = adapter.cancel(handle)
        return _snapshot_result("cancel", adapter, handle, event=event)
    return _snapshot_result("show", adapter, handle,
                            lifecycle=adapter.snapshot(handle).get("lifecycle") or [])


def _read_handoff(path: Path) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        if isinstance(value, dict) and value.get("schema") == \
                "taskplane.governed-command-handoff/v1":
            path.unlink(missing_ok=True)
            return value
        raise GovernedCommandError("detached command handoff is invalid")
    raise GovernedCommandError("detached command handoff timed out")


def _worker(path: str) -> int:
    handoff = _read_handoff(Path(path))
    runtime = CommandRuntime(
        str(handoff["root"]), workspace=str(handoff["workspace"]),
        authorization=str(handoff["authorization"]))
    handle = str(handoff["handle"])
    captured = bytearray()

    try:
        _governed_launch_authority(
            str(handoff["workspace"]), str(handoff["cwd"]),
            list(handoff["argv"]), dict(handoff["identity"]),
            expected=dict(handoff["authority"]))
        process = subprocess.Popen(
            list(handoff["argv"]), cwd=str(handoff["cwd"]),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)

        def drain() -> None:
            assert process.stdout is not None
            while chunk := process.stdout.read(8192):
                remaining = _CAPTURE_LIMIT - len(captured)
                if remaining > 0:
                    captured.extend(chunk[:remaining])

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        deadline = handoff.get("deadline")
        timed_out = False
        while process.poll() is None:
            if deadline is not None and time.time() >= float(deadline):
                timed_out = True
                process.terminate()
                break
            time.sleep(0.02)
        try:
            returncode = process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait()
        reader.join(timeout=1)
        if captured:
            runtime.append_output(handle, captured.decode("utf-8", errors="replace"))
        if runtime.snapshot(handle)["state"] in TERMINAL_STATES:
            return 0
        if timed_out:
            runtime.transition(handle, "timed_out", exit_code=returncode,
                               reason="governed command deadline elapsed")
        else:
            runtime.transition(handle, "succeeded" if returncode == 0 else "failed",
                               exit_code=returncode)
        return 0
    except BaseException as exc:
        if runtime.snapshot(handle)["state"] not in TERMINAL_STATES:
            runtime.transition(handle, "failed",
                               reason=f"detached worker failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "_worker":
        raise SystemExit(_worker(sys.argv[2]))
    raise SystemExit("governed_commands.py is an internal worker entry point")
