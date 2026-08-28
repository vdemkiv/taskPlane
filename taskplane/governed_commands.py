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
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
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
CHECKPOINT_BOUNDARY_SCHEMA = \
    "taskplane.semantic-checkpoint-boundary/v1"
CHECKPOINT_EXECUTION_RECEIPT_SCHEMA = \
    "taskplane.semantic-checkpoint-execution-receipt/v1"
_CAPTURE_LIMIT = MAX_EVENT_OUTPUT + 1
_CHECKPOINT_TIMEOUT_SECONDS = 600.0
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
    # Deliberately semantic: no caller-authored argv, cwd, environment,
    # executable, receipt, or sandbox path crosses this boundary.
    "checkpoint": frozenset({"authorization", "run_id", "task_id"}),
    "wait": _HANDLE_FIELDS | {"consumer", "timeout"},
    "reconnect": _HANDLE_FIELDS,
    "show": _HANDLE_FIELDS,
    "cancel": _HANDLE_FIELDS,
}
_IDENTITY_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


class GovernedCommandError(RuntimeError):
    pass


class GovernedCommandUnavailable(GovernedCommandError):
    """A required host boundary cannot be established without fallback."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


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
    if action in {"launch", "checkpoint"}:
        missing = {"authorization", "argv", "run_id", "task_id"} - set(value)
        if action == "checkpoint":
            missing.discard("argv")
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
    if action in {"launch", "checkpoint", "dispatch"}:
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
    required = {"schema", "host", "binding", "binding_digest"}
    if (not isinstance(value, dict) or not required.issubset(value) or
            set(value) - required - {"semantic"} or
            value.get("schema") != "taskplane.governed-command-control/v1" or
            not isinstance(value.get("binding"), Mapping) or
            _canonical_digest(value["binding"]) !=
            value.get("binding_digest") or
            ("semantic" in value and (
                not isinstance(value["semantic"], Mapping) or
                set(value["semantic"]) != {
                    "schema", "authority_fingerprint"} or
                value["semantic"].get("schema") !=
                CHECKPOINT_BOUNDARY_SCHEMA or
                not re.fullmatch(r"[0-9a-f]{64}", str(
                    value["semantic"].get("authority_fingerprint") or ""))))):
        raise GovernedCommandError("durable command control binding is invalid")
    return value


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")).hexdigest()


def _regular_file_binding(path: str | Path, *, label: str) -> dict:
    """Bind one non-symlink regular file to its bytes and stable stat data."""
    lexical = Path(path)
    try:
        lexical_stat = lexical.lstat()
        resolved = lexical.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise GovernedCommandUnavailable(
            "checkpoint_boundary_unavailable",
            f"semantic checkpoint {label} is unavailable") from exc
    if stat.S_ISLNK(lexical_stat.st_mode) or not stat.S_ISREG(
            resolved_stat.st_mode):
        raise GovernedCommandUnavailable(
            "checkpoint_boundary_unavailable",
            f"semantic checkpoint {label} must be a non-symlink regular file")
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise GovernedCommandUnavailable(
            "checkpoint_boundary_unavailable",
            f"semantic checkpoint {label} could not be fingerprinted") from exc
    material = {
        "path": str(resolved),
        "sha256": digest.hexdigest(),
        "bytes": resolved_stat.st_size,
        "device": resolved_stat.st_dev,
        "inode": resolved_stat.st_ino,
        "mode": stat.S_IMODE(resolved_stat.st_mode),
        "mtime_ns": resolved_stat.st_mtime_ns,
    }
    return {**material, "fingerprint": _canonical_digest(material)}


def _recheck_regular_file_binding(binding: Mapping, *, label: str) -> None:
    current = _regular_file_binding(str(binding.get("path") or ""), label=label)
    if current != dict(binding):
        raise GovernedCommandUnavailable(
            "checkpoint_boundary_changed",
            f"semantic checkpoint {label} changed before launch")


def _checkpoint_environment() -> dict[str, str]:
    """Return the literal environment used by both broker and proof child."""
    return {
        "HOME": "/tmp/taskplane-checkpoint-empty-home",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TZ": "UTC",
    }


def _git_output(workspace: str, *args: str,
                executable: str = "git") -> str:
    try:
        result = subprocess.run(
            [executable, *args], cwd=workspace,
            env=_checkpoint_environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GovernedCommandUnavailable(
            "checkpoint_repository_unavailable",
            "semantic checkpoint repository identity is unavailable") from exc
    if result.returncode != 0:
        raise GovernedCommandUnavailable(
            "checkpoint_repository_unavailable",
            "semantic checkpoint repository identity is unavailable")
    return result.stdout.strip()


def _checkpoint_plan_authority(
        workspace: str, run_id: str, task_id: str, *,
        state_path: str | None = None) -> tuple[dict, dict]:
    """Derive a checkpoint solely from the current persisted Plan task."""
    # Lazy imports avoid widening the module-level loop dependency cycle.
    from taskplane import checkpoint as checkpoint_engine
    from taskplane import loop as loop_engine
    import pytest

    if state_path is None:
        state = loop_engine.load(workspace)
        selected_state_path = Path(loop_engine._loop_path(workspace))
        if not selected_state_path.is_file():
            selected_state_path = Path(loop_engine._legacy_loop_path(workspace))
    else:
        selected_state_path = Path(state_path)
        try:
            state = json.loads(selected_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GovernedCommandError(
                "semantic checkpoint current Plan state is unavailable") from exc
    if not isinstance(state, Mapping):
        raise GovernedCommandError(
            "semantic checkpoint requires current governed loop state")
    expected_run_id = str(
        state.get("run_id") or state.get("requirement_id") or "loop")
    if run_id != expected_run_id:
        raise GovernedCommandError(
            "semantic checkpoint run identity does not match current Plan")
    tasks = state.get("tasks")
    matches = ([row for row in tasks
                if isinstance(row, Mapping) and row.get("id") == task_id]
               if isinstance(tasks, list) else [])
    if len(matches) != 1:
        raise GovernedCommandError(
            "semantic checkpoint task is not unique in current Plan")
    task = dict(matches[0])
    declaration = task.get("checkpoint")
    if not isinstance(declaration, Mapping):
        raise GovernedCommandError(
            "semantic checkpoint task has no Plan checkpoint declaration")
    reserved = sorted(set(declaration) & {
        "schema", "worktree_revision", "declared_scope", "receipt",
        "producer", "result",
    })
    if reserved:
        raise GovernedCommandError(
            "semantic checkpoint declaration contains engine-owned fields: "
            + ", ".join(reserved))
    revision = _git_output(workspace, "rev-parse", "HEAD")
    spec = checkpoint_engine.validate_checkpoint_spec(workspace, {
        **dict(declaration),
        "schema": checkpoint_engine.CHECKPOINT_SCHEMA,
        "worktree_revision": revision,
        "declared_scope": list(task.get("scope") or []),
    })
    authorized_argv = list(spec["focused_proof"]["argv"])
    executable = Path(sys.executable).resolve(strict=True)
    git_executable = shutil.which("git", path=os.defpath)
    if not git_executable:
        raise GovernedCommandUnavailable(
            "checkpoint_boundary_unavailable",
            "semantic checkpoint Git executable is unavailable")
    git_executable = str(Path(git_executable).resolve(strict=True))
    # ``-P`` keeps the sandbox checkout from shadowing the engine-owned
    # checkpoint plugin during interpreter startup.  Pytest still collects
    # the exact sandbox selector, while the plugin bytes come from the bound
    # engine PYTHONPATH below.
    runtime_argv = [str(executable), "-P", "-m", "pytest",
                    *authorized_argv[1:]]
    environment = _checkpoint_environment()
    active_contract = contract_engine.load_active(workspace)
    if not isinstance(active_contract, Mapping):
        raise GovernedCommandError(
            "semantic checkpoint requires an exact active contract")
    proof_path = Path(workspace) / spec["focused_proof"]["path"]
    material = {
        "schema": CHECKPOINT_BOUNDARY_SCHEMA,
        "workspace": str(Path(workspace).resolve()),
        "source_sha": revision,
        "run_id": run_id,
        "task_id": task_id,
        "step": str(state.get("step") or ""),
        "plan_fingerprint": str(state.get("plan_fingerprint") or
                                _canonical_digest(tasks)),
        "task_fingerprint": _canonical_digest(task),
        "state_binding": _regular_file_binding(
            selected_state_path, label="current Plan state"),
        "active_contract_fingerprint": _canonical_digest(active_contract),
        "checkpoint_id": spec["checkpoint_id"],
        "authorized_argv": authorized_argv,
        "runtime_argv": runtime_argv,
        "runtime_environment": environment,
        "runtime_environment_fingerprint": _canonical_digest(environment),
        "executable_binding": _regular_file_binding(
            executable, label="runtime executable"),
        "git_binding": _regular_file_binding(
            git_executable, label="Git executable"),
        "engine_bindings": {
            "governed_commands": _regular_file_binding(
                __file__, label="governed command engine"),
            "checkpoint": _regular_file_binding(
                checkpoint_engine.__file__, label="checkpoint engine"),
            "pytest": _regular_file_binding(
                pytest.__file__, label="pytest engine"),
        },
        "proof_binding": _regular_file_binding(
            proof_path, label="focused proof"),
    }
    authority = {**material, "fingerprint": _canonical_digest(material)}
    return spec, authority


def _assert_checkpoint_authority_current(
        workspace: str, authority: Mapping) -> dict:
    _spec, current = _checkpoint_plan_authority(
        workspace, str(authority.get("run_id") or ""),
        str(authority.get("task_id") or ""),
        state_path=str((authority.get("state_binding") or {}).get("path") or
                       ""))
    if current != dict(authority):
        raise GovernedCommandUnavailable(
            "checkpoint_plan_changed",
            "semantic checkpoint Plan authority changed before launch")
    _recheck_regular_file_binding(
        authority["executable_binding"], label="runtime executable")
    _recheck_regular_file_binding(
        authority["git_binding"], label="Git executable")
    for name, binding in authority["engine_bindings"].items():
        _recheck_regular_file_binding(binding, label=f"{name} engine")
    _recheck_regular_file_binding(
        authority["proof_binding"], label="focused proof")
    return current


def _prepare_checkpoint_sandbox(workspace: str, authority: Mapping) -> str:
    """Clone the exact committed candidate outside the reviewed checkout."""
    parent = Path(tempfile.gettempdir()) / "taskplane-checkpoint-sandboxes-v1"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    sandbox = Path(tempfile.mkdtemp(prefix="checkpoint-", dir=parent)) / "repo"
    environment = dict(authority["runtime_environment"])
    deadline = time.time() + 60.0

    def run(argv: list[str], cwd: str | None = None) -> None:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise GovernedCommandUnavailable(
                "checkpoint_sandbox_timeout",
                "semantic checkpoint sandbox preparation timed out")
        try:
            process = subprocess.Popen(
                argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, close_fds=True)
            stdout, stderr = process.communicate(timeout=remaining)
            result = subprocess.CompletedProcess(
                argv, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired as exc:
            _terminate_semantic_process_tree(process, deadline=deadline)
            raise GovernedCommandUnavailable(
                "checkpoint_sandbox_timeout",
                "semantic checkpoint sandbox preparation timed out") from exc
        except OSError as exc:
            raise GovernedCommandUnavailable(
                "checkpoint_sandbox_unavailable",
                "semantic checkpoint sandbox could not be established") from exc
        if result.returncode != 0:
            raise GovernedCommandUnavailable(
                "checkpoint_sandbox_unavailable",
                "semantic checkpoint sandbox could not be established")

    try:
        git_executable = str(authority["git_binding"]["path"])
        _recheck_regular_file_binding(
            authority["git_binding"], label="Git executable")
        run([git_executable, "clone", "--quiet", "--no-hardlinks", "--no-local",
             "--no-checkout", workspace, str(sandbox)])
        run([git_executable, "checkout", "--quiet", "--detach",
             str(authority["source_sha"])], cwd=str(sandbox))
        run([git_executable, "remote", "set-url", "--push", "origin",
             "taskplane-disabled://semantic-checkpoint"], cwd=str(sandbox))
        observed = _git_output(
            str(sandbox), "rev-parse", "HEAD", executable=git_executable)
        if observed != authority["source_sha"]:
            raise GovernedCommandUnavailable(
                "checkpoint_sandbox_mixed",
                "semantic checkpoint sandbox is not at the exact Plan SHA")
        sandbox_proof = sandbox / str(
            authority["proof_binding"]["path"])
        # The source proof binding uses an absolute source path.  Bind the
        # sandbox bytes independently and compare content, not inode/path.
        sandbox_proof = sandbox / Path(
            str(authority["proof_binding"]["path"])).relative_to(
                Path(workspace).resolve())
        sandbox_binding = _regular_file_binding(
            sandbox_proof, label="sandbox focused proof")
        if (sandbox_binding["sha256"] !=
                authority["proof_binding"]["sha256"]):
            raise GovernedCommandUnavailable(
                "checkpoint_sandbox_mixed",
                "semantic checkpoint sandbox proof differs from exact Plan SHA")
        return str(sandbox)
    except BaseException:
        shutil.rmtree(sandbox.parent, ignore_errors=True)
        raise


def _checkpoint_receipt_path(root: Path, handle: str) -> Path:
    return root / handle / "semantic-checkpoint-receipt.json"


def semantic_checkpoint_execution_evidence(
        workspace: str, authorization: str, handle: str) -> dict:
    """Return one sealed, post-completion semantic execution receipt."""
    root = _runtime_root(str(Path(workspace).resolve()))
    _read_control(root, handle)
    try:
        receipt = json.loads(
            _checkpoint_receipt_path(root, handle).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GovernedCommandError(
            "semantic checkpoint execution receipt is unavailable") from exc
    digest = receipt.get("receipt_digest") if isinstance(receipt, dict) else None
    material = {key: value for key, value in receipt.items()
                if key != "receipt_digest"} if isinstance(receipt, dict) else {}
    if (receipt.get("schema") != CHECKPOINT_EXECUTION_RECEIPT_SCHEMA or
            digest != _canonical_digest(material) or
            receipt.get("handle") != handle or
            receipt.get("workspace") != str(Path(workspace).resolve()) or
            receipt.get("authorization_fingerprint") != hashlib.sha256(
                authorization.encode("utf-8")).hexdigest() or
            receipt.get("state") != "succeeded" or
            receipt.get("exit_code") != 0):
        raise GovernedCommandError(
            "semantic checkpoint execution receipt is invalid")
    return dict(receipt)


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
    executable = os.path.basename(argv[0])
    if (re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable) and
            len(argv) >= 3 and argv[1] == "-c" and
            not contract_engine.inline_python_reads_only(argv[2])):
        raise GovernedCommandError(
            "governed command launch rejects opaque interpreter or script argv")
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

    if action == "checkpoint":
        if not detached_process_groups_supported():
            return {
                "schema": RESULT_SCHEMA,
                "action": "checkpoint",
                "status": "unavailable",
                "reason_code": "checkpoint_process_tree_unavailable",
                "error": ("semantic checkpoint requires detached process-tree "
                          "ownership; no process was started"),
            }
        try:
            spec, authority = _checkpoint_plan_authority(
                workspace, str(value["run_id"]), str(value["task_id"]))
            sandbox = _prepare_checkpoint_sandbox(workspace, authority)
        except GovernedCommandUnavailable as exc:
            return {
                "schema": RESULT_SCHEMA,
                "action": "checkpoint",
                "status": "unavailable",
                "reason_code": exc.reason_code,
                "error": str(exc),
            }
        identity = {
            "schema": IDENTITY_SCHEMA,
            "run_id": value["run_id"],
            "task_id": value["task_id"],
        }
        authorization = str(value["authorization"])
        root = _runtime_root(workspace)
        token = secrets.token_hex(16)
        handoff = root / "handoffs" / f"{token}.json"
        started: dict[str, object] = {}
        deadline = time.time() + _CHECKPOINT_TIMEOUT_SECONDS

        def launch(command: object, command_cwd: str) -> HostLaunch:
            del command, command_cwd
            # The outer broker is subject to the same content binding and
            # literal environment as the proof it launches.
            _recheck_regular_file_binding(
                authority["engine_bindings"]["governed_commands"],
                label="governed command engine")
            _recheck_regular_file_binding(
                authority["executable_binding"], label="runtime executable")
            process = subprocess.Popen(
                [str(authority["executable_binding"]["path"]),
                 str(Path(__file__).resolve()), "_worker", str(handoff)],
                cwd=sandbox, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
                env=dict(authority["runtime_environment"]))
            binding = detached_process_binding(process, token=token)
            started.update({"process": process, "binding": binding})
            return HostLaunch(binding=binding)

        adapter = _adapter(
            workspace, authorization, host="codex", launcher=launch)
        try:
            handle = adapter.launch(
                list(spec["focused_proof"]["argv"]), cwd=sandbox,
                deadline=deadline, identity=identity)
            binding = dict(started["binding"])
            control = {
                "schema": "taskplane.governed-command-control/v1",
                "host": "codex", "binding": binding,
                "binding_digest": _canonical_digest(binding),
                "semantic": {
                    "schema": CHECKPOINT_BOUNDARY_SCHEMA,
                    "authority_fingerprint": authority["fingerprint"],
                },
            }
            _atomic_json(_control_path(root, handle), control)
            _atomic_json(handoff, {
                "schema": "taskplane.governed-command-handoff/v1",
                "kind": "semantic-checkpoint",
                "workspace": workspace,
                "authorization": authorization,
                "root": str(root), "handle": handle,
                "argv": list(spec["focused_proof"]["argv"]),
                "cwd": sandbox, "deadline": deadline,
                "identity": identity, "authority": authority,
            })
            return _snapshot_result(
                "checkpoint", adapter, handle,
                semantic={
                    "schema": CHECKPOINT_BOUNDARY_SCHEMA,
                    "checkpoint_id": spec["checkpoint_id"],
                    "source_sha": authority["source_sha"],
                    "authority_fingerprint": authority["fingerprint"],
                })
        except GovernedCommandUnavailable as exc:
            shutil.rmtree(Path(sandbox).parent, ignore_errors=True)
            return {
                "schema": RESULT_SCHEMA,
                "action": "checkpoint",
                "status": "unavailable",
                "reason_code": exc.reason_code,
                "error": str(exc),
            }
        except OSError:
            shutil.rmtree(Path(sandbox).parent, ignore_errors=True)
            return {
                "schema": RESULT_SCHEMA,
                "action": "checkpoint",
                "status": "unavailable",
                "reason_code": "checkpoint_process_launch_unavailable",
                "error": ("semantic checkpoint process boundary could not "
                          "be established; no proof process was started"),
            }

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
        if control.get("semantic") and isinstance(event, Mapping) and \
                event.get("state") in TERMINAL_STATES:
            receipt_path = _checkpoint_receipt_path(root, handle)
            receipt_deadline = time.monotonic() + 2.0
            while not receipt_path.is_file() and \
                    time.monotonic() < receipt_deadline:
                time.sleep(0.01)
            if not receipt_path.is_file():
                raise GovernedCommandError(
                    "semantic checkpoint terminal receipt is unavailable")
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


def _terminate_semantic_process_tree(
        process: subprocess.Popen, *, deadline: float) -> None:
    """Terminate and reap a proof's complete POSIX process group in-budget."""
    if process.poll() is not None:
        return
    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        pgid = None
    if pgid is None:
        raise GovernedCommandUnavailable(
            "checkpoint_process_tree_unavailable",
            "semantic checkpoint proof process ownership was lost")
    os.killpg(pgid, signal.SIGTERM)
    remaining = max(0.0, min(1.0, deadline - time.time()))
    try:
        process.wait(timeout=remaining)
        return
    except subprocess.TimeoutExpired:
        pass
    os.killpg(pgid, signal.SIGKILL)
    remaining = max(0.0, deadline - time.time())
    try:
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        raise GovernedCommandUnavailable(
            "checkpoint_process_reap_timeout",
            "semantic checkpoint proof process tree did not reap in-budget") \
            from exc


def _semantic_checkpoint_worker(handoff: Mapping, runtime: CommandRuntime,
                                handle: str, captured: bytearray) -> int:
    authority = dict(handoff.get("authority") or {})
    workspace = str(handoff["workspace"])
    sandbox = str(handoff["cwd"])
    deadline = float(handoff["deadline"])
    state = "failed"
    returncode = 1
    reason = "semantic checkpoint failed"
    try:
        _assert_checkpoint_authority_current(workspace, authority)
        source_root = Path(workspace).resolve()
        sandbox_root = Path(sandbox).resolve()
        if source_root == sandbox_root or source_root in sandbox_root.parents:
            raise GovernedCommandUnavailable(
                "checkpoint_sandbox_mixed",
                "semantic checkpoint must execute outside reviewed source")
        if _git_output(
                sandbox, "rev-parse", "HEAD",
                executable=str(authority["git_binding"]["path"])) != \
                authority["source_sha"]:
            raise GovernedCommandUnavailable(
                "checkpoint_sandbox_mixed",
                "semantic checkpoint sandbox moved before launch")
        proof_relative = Path(
            str(authority["proof_binding"]["path"])).relative_to(source_root)
        sandbox_proof = _regular_file_binding(
            sandbox_root / proof_relative, label="sandbox focused proof")
        if sandbox_proof["sha256"] != authority["proof_binding"]["sha256"]:
            raise GovernedCommandUnavailable(
                "checkpoint_sandbox_mixed",
                "semantic checkpoint proof changed before launch")
        # This is intentionally the last operation before Popen.  No PATH
        # lookup or caller-controlled executable participates in the launch.
        _recheck_regular_file_binding(
            authority["executable_binding"], label="runtime executable")
        process = subprocess.Popen(
            list(authority["runtime_argv"]), cwd=sandbox,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
            env=dict(authority["runtime_environment"]))

        def drain() -> None:
            assert process.stdout is not None
            while chunk := process.stdout.read(8192):
                remaining = _CAPTURE_LIMIT - len(captured)
                if remaining > 0:
                    captured.extend(chunk[:remaining])

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        execution_deadline = max(time.time(), deadline - 2.0)
        timed_out = False
        while process.poll() is None:
            if time.time() >= execution_deadline:
                timed_out = True
                _terminate_semantic_process_tree(process, deadline=deadline)
                break
            time.sleep(0.02)
        returncode = process.wait(timeout=max(0.0, deadline - time.time()))
        reader.join(timeout=max(0.0, deadline - time.time()))
        if reader.is_alive():
            raise GovernedCommandUnavailable(
                "checkpoint_output_reap_timeout",
                "semantic checkpoint output reader did not finish in-budget")
        state = ("timed_out" if timed_out else
                 "succeeded" if returncode == 0 else "failed")
        reason = ("semantic checkpoint deadline elapsed" if timed_out else
                  state)
    except BaseException as exc:
        reason = (f"semantic checkpoint unavailable: {exc.reason_code}"
                  if isinstance(exc, GovernedCommandUnavailable) else
                  f"semantic checkpoint failed: {type(exc).__name__}: {exc}")
    finally:
        if captured:
            runtime.append_output(
                handle, captured.decode("utf-8", errors="replace"))
        if runtime.snapshot(handle)["state"] not in TERMINAL_STATES:
            runtime.transition(
                handle, state, exit_code=returncode, reason=reason)
        snapshot = runtime.snapshot(handle)
        receipt_material = {
            "schema": CHECKPOINT_EXECUTION_RECEIPT_SCHEMA,
            "workspace": workspace,
            "authorization_fingerprint": hashlib.sha256(
                str(handoff["authorization"]).encode("utf-8")).hexdigest(),
            "handle": handle,
            "identity": dict(handoff["identity"]),
            "source_sha": authority.get("source_sha"),
            "checkpoint_id": authority.get("checkpoint_id"),
            "authority_fingerprint": authority.get("fingerprint"),
            "sandbox_fingerprint": hashlib.sha256(
                sandbox.encode("utf-8")).hexdigest(),
            "authorized_command_fingerprint": _canonical_digest(
                authority.get("authorized_argv")),
            "runtime_argv": list(authority.get("runtime_argv") or []),
            "runtime_command_fingerprint": _canonical_digest(
                authority.get("runtime_argv")),
            "runtime_environment": dict(
                authority.get("runtime_environment") or {}),
            "runtime_environment_fingerprint": authority.get(
                "runtime_environment_fingerprint"),
            "executable_binding_fingerprint": (
                authority.get("executable_binding") or {}).get("fingerprint"),
            "git_binding_fingerprint": (
                authority.get("git_binding") or {}).get("fingerprint"),
            "engine_bindings_fingerprint": _canonical_digest(
                authority.get("engine_bindings")),
            "proof_sha256": (
                authority.get("proof_binding") or {}).get("sha256"),
            "output_sha256": snapshot.get("output_digest"),
            "state": snapshot.get("state"),
            "exit_code": snapshot.get("exit_code"),
        }
        _atomic_json(
            _checkpoint_receipt_path(Path(handoff["root"]), handle),
            {**receipt_material,
             "receipt_digest": _canonical_digest(receipt_material)})
        shutil.rmtree(Path(sandbox).parent, ignore_errors=True)
    return 0 if state == "succeeded" else 1


def _worker(path: str) -> int:
    handoff = _read_handoff(Path(path))
    runtime = CommandRuntime(
        str(handoff["root"]), workspace=str(handoff["workspace"]),
        authorization=str(handoff["authorization"]))
    handle = str(handoff["handle"])
    captured = bytearray()

    if handoff.get("kind") == "semantic-checkpoint":
        return _semantic_checkpoint_worker(handoff, runtime, handle, captured)

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
