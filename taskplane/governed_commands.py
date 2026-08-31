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

try:
    import checkpoint_boundary
except ImportError:  # package import path
    from taskplane import checkpoint_boundary

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
from taskplane import owned_cleanup
from taskplane.settings import load_settings
from taskplane import taskplane_lite as contract_engine


RESULT_SCHEMA = "taskplane.governed-command-result/v1"
IDENTITY_SCHEMA = "taskplane.governed-command-identity/v1"
DISPATCH_INTENT_SCHEMA = \
    "taskplane.native-agent-dispatch-intent-telemetry/v1"
CHECKPOINT_BOUNDARY_SCHEMA = \
    "taskplane.semantic-checkpoint-boundary/v1"
CHECKPOINT_EXECUTION_RECEIPT_SCHEMA = \
    "taskplane.semantic-checkpoint-execution-receipt/v1"
CHECKPOINT_AUTHORIZATION_SCHEMA = \
    "taskplane.semantic-checkpoint-authorization/v1"
_CAPTURE_LIMIT = MAX_EVENT_OUTPUT + 1
_CHECKPOINT_TIMEOUT_SECONDS = 600.0
_CHECKPOINT_REAP_SECONDS = 2.0
# A checkpoint may consume only a dependency whose work exists. Keep this
# closed projection local to the durable command boundary: importing the loop
# merely to read its identical constant creates governed_commands <-> loop,
# growing the measured orchestration SCC and weakening the command boundary.
_CHECKPOINT_DEPENDENCY_SATISFIED = frozenset({
    "passed", "done", "external",
})
_HANDLE_FIELDS = frozenset({"authorization", "handle"})
_ACTION_FIELDS = {
    "dispatch": frozenset({
        "authorization", "consumer", "host", "payload", "run_id",
        "task_id", "wave_id",
    }),
    "launch": frozenset({
        "authorization", "argv", "cwd", "deadline", "host", "run_id",
        "task_id", "attempt", "wave_id",
    }),
    # Deliberately semantic: no caller-authored argv, cwd, environment,
    # executable, receipt, or sandbox path crosses this boundary.
    "checkpoint": frozenset({
        "authorization", "checkpoint_authority", "run_id", "task_id",
        "attempt",
    }),
    "wait": _HANDLE_FIELDS | {"consumer", "timeout"},
    "reconnect": _HANDLE_FIELDS,
    "show": _HANDLE_FIELDS,
    "cancel": _HANDLE_FIELDS,
    "interrupt": _HANDLE_FIELDS,
    "handoff": _HANDLE_FIELDS,
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


def command_runtime_root(workspace: str) -> Path:
    """Public path adapter used by runtime/resource lifecycle composition."""
    return _runtime_root(workspace)


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
        missing = {
            "authorization", "argv", "run_id", "task_id",
        } - set(value)
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
    if action in {"launch", "checkpoint"} and "attempt" in value and (
            isinstance(value["attempt"], bool) or
            not isinstance(value["attempt"], int) or
            int(value["attempt"]) < 1):
        raise GovernedCommandError("governed command attempt is invalid")
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
                    "schema", "authority_fingerprint",
                    "checkpoint_authorization_fingerprint",
                    "plan_fingerprint", "task_fingerprint",
                    "selection_fingerprint", "contract_fingerprint",
                    "step", "target_sha"} or
                value["semantic"].get("schema") !=
                CHECKPOINT_BOUNDARY_SCHEMA or
                any(not re.fullmatch(r"[0-9a-f]{64}", str(
                    value["semantic"].get(field) or ""))
                    for field in (
                        "authority_fingerprint",
                        "checkpoint_authorization_fingerprint",
                        "plan_fingerprint", "task_fingerprint",
                        "selection_fingerprint", "contract_fingerprint")) or
                not re.fullmatch(r"[0-9a-f]{40,64}", str(
                    value["semantic"].get("target_sha") or "")) or
                value["semantic"].get("step") not in {"execute", "fix"}))):
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


def _checkpoint_selected_task(
        state: Mapping[str, object], task_id: str) -> dict:
    """Return only the Plan-selected task that may submit a checkpoint now."""
    step = str(state.get("step") or "")
    if step not in {"execute", "fix"}:
        raise GovernedCommandError(
            "semantic checkpoint authorization is valid only for the "
            "current execute/fix submission step")
    tasks = state.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise GovernedCommandError(
            "semantic checkpoint requires a non-empty current Plan")
    matches = [row for row in tasks
               if isinstance(row, Mapping) and row.get("id") == task_id]
    if len(matches) != 1:
        raise GovernedCommandError(
            "semantic checkpoint task is not unique in current Plan")
    current_index = state.get("current_task", 0)
    current = (tasks[current_index]
               if isinstance(current_index, int) and
               0 <= current_index < len(tasks) else None)
    selected = matches[0]
    parallel_running = (
        step == "execute" and state.get("parallel") is True and
        selected.get("status") == "running")
    if selected is not current and not parallel_running:
        raise GovernedCommandError(
            "semantic checkpoint task is not the current Plan-selected task")
    allowed_statuses = ({"pending", "running"} if step == "execute" else
                        {"pending", "running", "built"})
    if str(selected.get("status") or "pending") not in allowed_statuses:
        raise GovernedCommandError(
            "semantic checkpoint current task is not ready for submission")
    by_id = {str(row.get("id")): row for row in tasks
             if isinstance(row, Mapping) and row.get("id")}
    unsatisfied = [str(dependency)
                   for dependency in selected.get("deps") or []
                   if str((by_id.get(str(dependency)) or {}).get("status") or
                          "") not in _CHECKPOINT_DEPENDENCY_SATISFIED]
    if unsatisfied:
        raise GovernedCommandError(
            "semantic checkpoint current task is not ready; unmet "
            "dependencies: " + ", ".join(sorted(unsatisfied)))
    return dict(selected)


_CHECKPOINT_TASK_RUNTIME_FIELDS = frozenset({
    "_build_failed", "_submission", "convergence_boundaries",
    "convergence_history", "convergence_revision", "evaluation",
    "fix_cycles", "reanchor_authority", "status", "target_commit",
    "workspace",
})


def _checkpoint_task_projection(task: Mapping) -> dict:
    return {str(key): value for key, value in task.items()
            if key not in _CHECKPOINT_TASK_RUNTIME_FIELDS}


def _checkpoint_selection_fingerprint(
        state: Mapping[str, object], task: Mapping) -> str:
    tasks = state.get("tasks") or []
    by_id = {str(row.get("id")): row for row in tasks
             if isinstance(row, Mapping) and row.get("id")}
    material = {
        "step": str(state.get("step") or ""),
        "parallel": state.get("parallel") is True,
        "current_task": state.get("current_task", 0),
        "selected_task": str(task.get("id") or ""),
        "selected_status": str(task.get("status") or "pending"),
        "dependency_statuses": {
            str(dependency): str(
                (by_id.get(str(dependency)) or {}).get("status") or "")
            for dependency in task.get("deps") or []
        },
    }
    return _canonical_digest(material)


def _checkpoint_plan_state_paths(workspace: str) -> tuple[Path, Path]:
    """Return the canonical and pre-migration loop-state paths without
    importing the loop composition root.

    The location rule mirrors loop.state_dir: repo-store mode is the explicit
    single-writer exception, otherwise per-user external state wins once it
    exists and the unmigrated in-repository knowledge path remains readable.
    """
    filename = "loop.json"
    if contract_engine.store_env() == "repo":
        state_root = Path(contract_engine.kb_root(workspace)) / "state"
    else:
        external = Path(contract_engine.external_store_root(workspace)) / \
            "knowledge" / "state"
        unmigrated = Path(workspace) / "knowledge" / "state"
        state_root = (external if (external / filename).exists() or
                      not (unmigrated / filename).exists() else unmigrated)
    return (state_root / filename,
            Path(contract_engine.tp_dir(workspace)) / filename)


def _checkpoint_plan_authority(
        workspace: str, run_id: str, task_id: str, *,
        state_path: str | None = None) -> tuple[dict, dict]:
    """Derive a checkpoint solely from the current persisted Plan task."""
    # The checkpoint validator is intentionally lazy; unlike the loop
    # composition root it has no reverse dependency on governed commands.
    from taskplane import checkpoint as checkpoint_engine
    import pytest

    if state_path is None:
        current_state_path, legacy_state_path = \
            _checkpoint_plan_state_paths(workspace)
        selected_state_path = current_state_path
        if not selected_state_path.is_file():
            selected_state_path = legacy_state_path
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
    task = _checkpoint_selected_task(state, task_id)
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
    ps_executable = shutil.which("ps", path=os.defpath)
    if not ps_executable:
        raise GovernedCommandUnavailable(
            "checkpoint_process_tree_unavailable",
            "semantic checkpoint process-tree inspection is unavailable")
    ps_executable = str(Path(ps_executable).resolve(strict=True))
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
    state_file_binding = _regular_file_binding(
        selected_state_path, label="current Plan state")
    plan_projection = [_checkpoint_task_projection(row)
                       for row in tasks if isinstance(row, Mapping)]
    task_projection = _checkpoint_task_projection(task)
    material = {
        "schema": CHECKPOINT_BOUNDARY_SCHEMA,
        "workspace": str(Path(workspace).resolve()),
        "source_sha": revision,
        "run_id": run_id,
        "task_id": task_id,
        "step": str(state.get("step") or ""),
        "current_task_index": int(state.get("current_task", 0)),
        "approved_plan_fingerprint": str(
            state.get("plan_fingerprint") or ""),
        "plan_fingerprint": _canonical_digest(plan_projection),
        "task_fingerprint": _canonical_digest(task_projection),
        "selection_fingerprint": _checkpoint_selection_fingerprint(
            state, task),
        "state_binding": {"path": state_file_binding["path"]},
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
        "process_inspector_binding": _regular_file_binding(
            ps_executable, label="process inspector"),
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
        workspace: str, authority: Mapping, *,
        use_bound_state_path: bool = False) -> dict:
    _spec, current = _checkpoint_plan_authority(
        workspace, str(authority.get("run_id") or ""),
        str(authority.get("task_id") or ""),
        state_path=(str((authority.get("state_binding") or {}).get("path") or
                        "") if use_bound_state_path else None))
    if current != dict(authority):
        raise GovernedCommandUnavailable(
            "checkpoint_plan_changed",
            "semantic checkpoint Plan authority changed before launch")
    _recheck_regular_file_binding(
        authority["executable_binding"], label="runtime executable")
    _recheck_regular_file_binding(
        authority["git_binding"], label="Git executable")
    _recheck_regular_file_binding(
        authority["process_inspector_binding"], label="process inspector")
    for name, binding in authority["engine_bindings"].items():
        _recheck_regular_file_binding(binding, label=f"{name} engine")
    _recheck_regular_file_binding(
        authority["proof_binding"], label="focused proof")
    return current


def _checkpoint_authorization_root(workspace: str) -> Path:
    return _runtime_root(workspace) / "semantic-checkpoint-authorizations-v1"


def _checkpoint_authorization_path(
        workspace: str, token: str, *, consumed: bool = False) -> Path:
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    state = "consumed" if consumed else "issued"
    return _checkpoint_authorization_root(workspace) / state / \
        f"{token_digest}.json"


def mint_semantic_checkpoint_authorization(
        workspace: str, *, lifecycle_authorization: str,
        run_id: str, task_id: str) -> str:
    """Mint one opaque, single-use checkpoint launch authorization.

    The public lifecycle authorization remains stable so durable wait and
    reconnect keep their compatibility contract.  This private capability is
    narrower: one current Plan task, one source SHA, and one execute/fix step.
    """
    workspace = str(Path(workspace).resolve())
    if not str(lifecycle_authorization or "").strip():
        raise GovernedCommandError(
            "semantic checkpoint lifecycle authorization is required")
    _spec, authority = _checkpoint_plan_authority(
        workspace, str(run_id), str(task_id))
    token = "tp-checkpoint-v1." + secrets.token_hex(32)
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    material = {
        "schema": CHECKPOINT_AUTHORIZATION_SCHEMA,
        "workspace": workspace,
        "run_id": str(run_id),
        "task_id": str(task_id),
        "step": authority["step"],
        "target_sha": authority["source_sha"],
        "plan_fingerprint": authority["plan_fingerprint"],
        "task_fingerprint": authority["task_fingerprint"],
        "selection_fingerprint": authority["selection_fingerprint"],
        "contract_fingerprint": authority["active_contract_fingerprint"],
        "authority_fingerprint": authority["fingerprint"],
        "lifecycle_authorization_fingerprint": hashlib.sha256(
            lifecycle_authorization.encode("utf-8")).hexdigest(),
        "token_fingerprint": token_digest,
        "issued_at_ns": time.time_ns(),
    }
    record = {**material, "record_digest": _canonical_digest(material)}
    root = _checkpoint_authorization_root(workspace)
    for directory in (root, root / "issued", root / "consumed"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError as exc:
            raise GovernedCommandUnavailable(
                "checkpoint_authorization_unavailable",
                "semantic checkpoint authorization store is unavailable") \
                from exc
    path = _checkpoint_authorization_path(workspace, token)
    _atomic_json(path, record)
    path.chmod(0o600)
    return token


def _consume_semantic_checkpoint_authorization(
        workspace: str, *, lifecycle_authorization: str,
        run_id: str, task_id: str, token: object) -> tuple[dict, dict]:
    """Consume and revalidate one engine-minted checkpoint capability."""
    if not isinstance(token, str) or not re.fullmatch(
            r"tp-checkpoint-v1\.[0-9a-f]{64}", token):
        raise GovernedCommandError(
            "semantic checkpoint requires an engine-minted authorization")
    issued = _checkpoint_authorization_path(workspace, token)
    consumed = _checkpoint_authorization_path(
        workspace, token, consumed=True)
    try:
        path_stat = issued.lstat()
        record = json.loads(issued.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        reason = ("was already consumed" if consumed.is_file() else
                  "was not engine-minted")
        raise GovernedCommandError(
            f"semantic checkpoint authorization {reason}") from exc
    except (OSError, ValueError) as exc:
        raise GovernedCommandError(
            "semantic checkpoint authorization is unavailable") from exc
    material = ({key: value for key, value in record.items()
                 if key != "record_digest"}
                if isinstance(record, Mapping) else {})
    current_token_fingerprint = hashlib.sha256(
        token.encode("utf-8")).hexdigest()
    if (not isinstance(record, Mapping) or
            not stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode) or
            stat.S_IMODE(path_stat.st_mode) & 0o077 or
            record.get("schema") != CHECKPOINT_AUTHORIZATION_SCHEMA or
            record.get("record_digest") != _canonical_digest(material) or
            record.get("workspace") != str(Path(workspace).resolve()) or
            record.get("run_id") != run_id or
            record.get("task_id") != task_id or
            record.get("token_fingerprint") != current_token_fingerprint or
            record.get("lifecycle_authorization_fingerprint") !=
            hashlib.sha256(lifecycle_authorization.encode("utf-8")).hexdigest()):
        raise GovernedCommandError(
            "semantic checkpoint authorization is invalid")
    spec, authority = _checkpoint_plan_authority(workspace, run_id, task_id)
    expected = {
        "step": authority["step"],
        "target_sha": authority["source_sha"],
        "plan_fingerprint": authority["plan_fingerprint"],
        "task_fingerprint": authority["task_fingerprint"],
        "selection_fingerprint": authority["selection_fingerprint"],
        "contract_fingerprint": authority["active_contract_fingerprint"],
        "authority_fingerprint": authority["fingerprint"],
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise GovernedCommandError(
            "semantic checkpoint authorization is stale")
    try:
        os.replace(issued, consumed)
    except FileNotFoundError as exc:
        raise GovernedCommandError(
            "semantic checkpoint authorization was already consumed") from exc
    return spec, authority


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
    workspace = str(Path(workspace).resolve())
    root = _runtime_root(workspace)
    try:
        control = _read_control(root, handle)
        receipt = json.loads(
            _checkpoint_receipt_path(root, handle).read_text(encoding="utf-8"))
        if not isinstance(receipt, Mapping):
            raise ValueError("receipt is not an object")
        identity = receipt.get("identity") or {}
        _spec, authority = _checkpoint_plan_authority(
            workspace, str(identity.get("run_id") or ""),
            str(identity.get("task_id") or ""))
        runtime = CommandRuntime(
            str(root), workspace=workspace, authorization=authorization)
        snapshot = runtime.snapshot(handle)
    except (OSError, ValueError, GovernedCommandError, KeyError) as exc:
        raise GovernedCommandError(
            "semantic checkpoint execution receipt is unavailable") from exc
    digest = receipt.get("receipt_digest")
    material = {key: value for key, value in receipt.items()
                if key != "receipt_digest"}
    semantic = control.get("semantic") or {}
    expected_semantic = {
        "schema": CHECKPOINT_BOUNDARY_SCHEMA,
        "authority_fingerprint": authority["fingerprint"],
        "checkpoint_authorization_fingerprint": receipt.get(
            "checkpoint_authorization_fingerprint"),
        "plan_fingerprint": authority["plan_fingerprint"],
        "task_fingerprint": authority["task_fingerprint"],
        "selection_fingerprint": authority["selection_fingerprint"],
        "contract_fingerprint": authority["active_contract_fingerprint"],
        "step": authority["step"],
        "target_sha": authority["source_sha"],
    }
    if (receipt.get("schema") != CHECKPOINT_EXECUTION_RECEIPT_SCHEMA or
            digest != _canonical_digest(material) or
            receipt.get("handle") != handle or
            receipt.get("workspace") != workspace or
            receipt.get("authorization_fingerprint") != hashlib.sha256(
                authorization.encode("utf-8")).hexdigest() or
            semantic != expected_semantic or
            receipt.get("control_fingerprint") != _canonical_digest(control) or
            receipt.get("authority_fingerprint") != authority["fingerprint"] or
            receipt.get("plan_fingerprint") != authority["plan_fingerprint"] or
            receipt.get("task_fingerprint") != authority["task_fingerprint"] or
            receipt.get("selection_fingerprint") !=
            authority["selection_fingerprint"] or
            receipt.get("contract_fingerprint") !=
            authority["active_contract_fingerprint"] or
            receipt.get("target_sha") != authority["source_sha"] or
            receipt.get("source_sha") != authority["source_sha"] or
            receipt.get("step") != authority["step"] or
            receipt.get("checkpoint_id") != authority["checkpoint_id"] or
            receipt.get("post_authority_verified") is not True or
            receipt.get("runtime_argv") != authority["runtime_argv"] or
            receipt.get("runtime_environment") !=
            authority["runtime_environment"] or
            receipt.get("runtime_environment_fingerprint") !=
            authority["runtime_environment_fingerprint"] or
            receipt.get("executable_binding_fingerprint") !=
            authority["executable_binding"]["fingerprint"] or
            receipt.get("git_binding_fingerprint") !=
            authority["git_binding"]["fingerprint"] or
            receipt.get("process_inspector_binding_fingerprint") !=
            authority["process_inspector_binding"]["fingerprint"] or
            receipt.get("engine_bindings_fingerprint") !=
            _canonical_digest(authority["engine_bindings"]) or
            receipt.get("proof_sha256") != authority["proof_binding"]["sha256"] or
            snapshot.get("identity") != receipt.get("identity") or
            snapshot.get("output_digest") != receipt.get("output_sha256") or
            snapshot.get("state") != receipt.get("state") or
            snapshot.get("exit_code") != receipt.get("exit_code") or
            receipt.get("state") != "succeeded" or
            receipt.get("exit_code") != 0):
        raise GovernedCommandError(
            "semantic checkpoint execution receipt is invalid")
    return dict(receipt)


# The runtime is the sole producer for this evidence.  Register a late-bound
# callable so monkeypatching/replacement at the composition root remains
# observable while checkpoint receipt minting keeps no reverse import edge.
checkpoint_boundary.register_execution_evidence_loader(
    lambda workspace, authorization, handle:
        semantic_checkpoint_execution_evidence(
            workspace, authorization, handle))


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


def _runtime(workspace: str, authorization: str, *,
             owned_cleanup_context: Mapping[str, object] | None = None) \
        -> CommandRuntime:
    return CommandRuntime(str(_runtime_root(workspace)), workspace=workspace,
                          authorization=authorization,
                          owned_cleanup_context=owned_cleanup_context)


def _resolved_cleanup_attempt(workspace: str, *, run_id: str, task_id: str,
                              supplied: object = None) -> int:
    """Use caller lineage when present, otherwise advance durable manifests."""
    if supplied is not None:
        if isinstance(supplied, bool) or not isinstance(supplied, int) or \
                supplied < 1:
            raise GovernedCommandError("owned cleanup attempt is invalid")
        return supplied
    manifests = (Path(workspace).resolve() / ".taskplane" /
                 "owned-cleanup-v1" / "manifests")
    attempts = []
    for candidate in sorted(manifests.glob("*.json")) \
            if manifests.is_dir() else []:
        if re.fullmatch(r"[0-9a-f]{32}\.json", candidate.name) is None:
            continue
        manifest = owned_cleanup.load_manifest(candidate)
        owner = manifest["owner"]
        if owner.get("run_id") == run_id and owner.get("task_id") == task_id:
            attempts.append(int(owner["attempt"]))
    return max(attempts, default=0) + 1


def prepare_owned_cleanup(workspace: str, authorization: str, *,
                          run_id: str, task_id: str, attempt: int,
                          token: str) -> dict:
    """Reserve the process marker before the detached process is created."""
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise GovernedCommandError("owned cleanup attempt is invalid")
    workspace_path = Path(workspace).resolve()
    root = workspace_path / ".taskplane" / "owned-cleanup-v1"
    manifest = root / "manifests" / f"{token}.json"
    evidence_root = root / "evidence" / token
    owner = {
        "repository_id": "repo-" + _canonical_digest(
            str((workspace_path / ".git").resolve()))[:32],
        "workspace_fingerprint": _canonical_digest(str(workspace_path)),
        "settings_digest": load_settings().digest,
        "run_id": str(run_id),
        "task_id": str(task_id),
        "attempt": attempt,
    }
    owned_cleanup.create_manifest(
        manifest, evidence_root=evidence_root, **owner)
    marker = root / "processes" / f"{token}.json"
    process_resource_id = owned_cleanup.reserve_resource(
        manifest,
        kind="process-group",
        containment_root=marker.parent,
        relative_name=marker.name,
        creator_nonce="governed-command:" + token,
        stable_identity={
            "token": token, "run_id": str(run_id), "task_id": str(task_id),
            "attempt": attempt,
        },
        evidence_refs=("terminal-state", "handoff", "publication-replay"),
    )
    return {
        "manifest": str(manifest),
        "owner": owner,
        "process_marker": str(marker),
        "process_resource_id": process_resource_id,
        "handoff_path": str(
            _runtime_root(str(workspace_path)) / "handoffs" / f"{token}.json"),
        "authorization": authorization,
        "workspace": str(workspace_path),
    }


_prepare_owned_cleanup = prepare_owned_cleanup


def activate_owned_process(context: Mapping[str, object],
                           binding: Mapping[str, object]) -> None:
    marker = Path(str(context["process_marker"]))
    _atomic_json(marker, {
        "schema": "taskplane.owned-process-marker/v1",
        "binding": dict(binding),
        "binding_digest": _canonical_digest(dict(binding)),
    })
    owned_cleanup.activate_resource(
        str(context["manifest"]), str(context["process_resource_id"]),
        observed_identity=dict(binding))


_activate_owned_process = activate_owned_process


def _runtime_cleanup_context(context: Mapping[str, object]) -> dict:
    return {
        "manifest": str(context["manifest"]),
        "process_resource_id": str(context["process_resource_id"]),
        "handoff_path": str(context["handoff_path"]),
    }


def reserve_owned_handoff(context: Mapping[str, object], *,
                          token: str, run_id: str, task_id: str,
                          attempt: int, handle: str) -> str:
    handoff = Path(str(context["handoff_path"]))
    return owned_cleanup.reserve_resource(
        str(context["manifest"]), kind="generated-state",
        containment_root=handoff.parent, relative_name=handoff.name,
        creator_nonce="governed-handoff:" + token,
        stable_identity={
            "token": token, "run_id": run_id, "task_id": task_id,
            "attempt": attempt, "handle": handle,
        },
        evidence_refs=("terminal-state", "handoff", "publication-replay"),
        dependencies=(str(context["process_resource_id"]),),
    )


_reserve_owned_handoff = reserve_owned_handoff


def publish_owned_handoff(context: Mapping[str, object], *, token: str,
                          run_id: str, task_id: str, attempt: int,
                          handle: str, payload: Mapping[str, object]) -> str:
    """Reserve-before-write and activate one durable handoff resource."""
    resource_id = reserve_owned_handoff(
        context, token=token, run_id=run_id, task_id=task_id,
        attempt=attempt, handle=handle)
    _atomic_json(Path(str(context["handoff_path"])), payload)
    owned_cleanup.activate_resource(str(context["manifest"]), resource_id)
    return resource_id


def _terminal_outcome(state: str) -> str:
    return {
        "succeeded": "success", "failed": "failure",
        "timed_out": "timeout", "cancelled": "cancellation",
        "interrupted": "interruption", "handed_off": "handoff",
    }[state]


def _cleanup_workspace(manifest_path: str) -> str:
    path = Path(manifest_path).resolve()
    if path.parent.name != "manifests" or \
            path.parent.parent.name != "owned-cleanup-v1" or \
            path.parent.parent.parent.name != ".taskplane":
        raise owned_cleanup.OwnedCleanupError(
            "owned cleanup manifest workspace is ambiguous")
    return str(path.parents[3])


def finalize_owned_result(
        result: dict, *, trigger: str, outcome: str | None = None,
        publisher=None) -> dict:
    """Seal, canonically publish, clean, and expose one terminal result."""
    snapshot = dict(result.get("snapshot") or {})
    state = str(snapshot.get("state") or "")
    binding = snapshot.get("owned_cleanup")
    recovery_lost = (trigger == "recovery" and state == "input_required" and
                     snapshot.get("reason_code") ==
                     "detached_worker_ownership_lost")
    if (state not in TERMINAL_STATES and not recovery_lost and outcome is None) or \
            not isinstance(binding, Mapping):
        return result
    manifest_path = str(binding["manifest"])
    try:
        manifest = owned_cleanup.load_manifest(manifest_path)
        workspace = _cleanup_workspace(manifest_path)
        if manifest.get("terminal") is not None:
            try:
                publication = owned_cleanup.replay_terminal_publication(
                    manifest_path, workspace=workspace, publisher=publisher)
            except Exception as exc:
                publication = {"status": "pending", "replay_required": True,
                               "error": str(exc)}
            receipt = owned_cleanup.cleanup_manifest(manifest_path)
            return {
                **result, "cleanup_receipt": receipt,
                "cleanup_evidence":
                    owned_cleanup.cleanup_consumer_evidence(receipt),
                "publication_result": publication,
                "publication_replay": dict(
                    manifest["terminal"]["publication_replay"]),
            }
        worker_id = str(binding["worker_resource_id"])
        worker = manifest["resources"].get(worker_id) or {}
        if worker.get("policy") == {"active": True}:
            owned_cleanup.update_resource_policy(
                manifest_path, worker_id, expected={"active": True},
                replacement={"active": False})
        elif worker.get("policy") != {"active": False}:
            raise owned_cleanup.OwnedCleanupError(
                "worker cleanup policy is unavailable or ambiguous")
        replay_source = (Path(manifest_path).parent.parent / "publication" /
                         (snapshot["handle"] + ".json"))
        selected_outcome = (str(outcome) if outcome is not None else
                            "recovery" if recovery_lost else
                            _terminal_outcome(state))
        replay = owned_cleanup.write_publication_replay(
            replay_source, owner=manifest["owner"],
            outcome=selected_outcome,
            source_revision=int(snapshot["revision"]),
            source_fingerprint=_canonical_digest(snapshot), trigger=trigger)
        owned_cleanup.seal_terminal(
            manifest_path, outcome=selected_outcome, evidence={
                "terminal-state": (
                    Path(str(binding["handoff_path"])).parent.parent /
                    snapshot["handle"] / "snapshot.json"),
                "handoff": str(binding["handoff_path"]),
                "publication-replay": replay_source,
            })
        try:
            publication = owned_cleanup.replay_publication(
                replay_source, workspace=workspace, owner=manifest["owner"],
                outcome=selected_outcome, publisher=publisher)
            replay = dict(publication["obligation"])
        except Exception as exc:
            publication = {"status": "pending", "replay_required": True,
                           "error": str(exc)}
        receipt = owned_cleanup.cleanup_manifest(manifest_path)
        return {
            **result, "cleanup_receipt": receipt,
            "cleanup_evidence": owned_cleanup.cleanup_consumer_evidence(
                receipt),
            "publication_result": publication,
            "publication_replay": replay,
        }
    except (owned_cleanup.OwnedCleanupError, OSError, ValueError, KeyError) as exc:
        # The command terminal result remains authoritative. Cleanup failure
        # is explicit secondary attention evidence for recovery/sign-off.
        return {**result, "cleanup_error": str(exc),
                "cleanup_status": "attention"}


_finalize_owned_result = finalize_owned_result


def unwind_owned_failure(
        context: Mapping[str, object], *, error: object,
        outcome: str = "failure", trigger: str = "terminal",
        process_binding: Mapping[str, object] | None = None,
        publisher=None) -> dict:
    """Durably unwind every reservation after a preparation/launch failure."""
    manifest_path = str(context["manifest"])
    manifest = owned_cleanup.load_manifest(manifest_path)
    if manifest.get("terminal") is not None:
        receipt = owned_cleanup.cleanup_manifest(manifest_path)
        return {"schema": RESULT_SCHEMA, "action": "failure-unwind",
                "error": str(error), "cleanup_receipt": receipt,
                "cleanup_evidence":
                    owned_cleanup.cleanup_consumer_evidence(receipt)}
    for resource_id, resource in list(manifest["resources"].items()):
        if resource.get("state") != "reserved":
            continue
        target = Path(str(resource["containment_root"])) / str(
            resource["relative_name"])
        if resource.get("kind") == "process-group" and \
                process_binding is not None:
            activate_owned_process(context, process_binding)
        elif not os.path.lexists(target):
            owned_cleanup.abandon_resource(manifest_path, resource_id)
        else:
            owned_cleanup.activate_resource(manifest_path, resource_id)
        manifest = owned_cleanup.load_manifest(manifest_path)
    manifest = owned_cleanup.load_manifest(manifest_path)
    for resource_id, resource in list(manifest["resources"].items()):
        if resource.get("kind") == "worker-contract" and \
                resource.get("state") == "active" and \
                resource.get("policy") == {"active": True}:
            owned_cleanup.update_resource_policy(
                manifest_path, resource_id, expected={"active": True},
                replacement={"active": False})
    manifest = owned_cleanup.load_manifest(manifest_path)
    root = Path(manifest_path).parent.parent
    token = Path(manifest_path).stem
    failure_root = root / "recovery" / token
    terminal_source = failure_root / "terminal-state.json"
    handoff_source = failure_root / "handoff.json"
    _atomic_json(terminal_source, {
        "schema": "taskplane.owned-cleanup-failure/v1",
        "owner": manifest["owner"], "outcome": outcome,
        "error": str(error),
    })
    _atomic_json(handoff_source, {
        "schema": "taskplane.owned-cleanup-recovery-handoff/v1",
        "owner": manifest["owner"], "trigger": trigger,
    })
    replay_source = root / "publication" / f"{token}-recovery.json"
    replay = owned_cleanup.write_publication_replay(
        replay_source, owner=manifest["owner"], outcome=outcome,
        source_revision=max(1, int(manifest["revision"])),
        source_fingerprint=_canonical_digest({
            "owner": manifest["owner"], "outcome": outcome,
            "error": str(error), "revision": manifest["revision"],
        }), trigger=trigger)
    owned_cleanup.seal_terminal(
        manifest_path, outcome=outcome, evidence={
            "terminal-state": terminal_source, "handoff": handoff_source,
            "publication-replay": replay_source,
        })
    try:
        publication = owned_cleanup.replay_publication(
            replay_source, workspace=str(context.get("workspace") or
                                         _cleanup_workspace(manifest_path)),
            owner=manifest["owner"], outcome=outcome, publisher=publisher)
        replay = dict(publication["obligation"])
    except Exception as exc:
        publication = {"status": "pending", "replay_required": True,
                       "error": str(exc)}
    receipt = owned_cleanup.cleanup_manifest(manifest_path)
    return {
        "schema": RESULT_SCHEMA, "action": "failure-unwind",
        "error": str(error), "cleanup_receipt": receipt,
        "cleanup_evidence": owned_cleanup.cleanup_consumer_evidence(receipt),
        "publication_replay": replay, "publication_result": publication,
    }


def recover_owned_cleanup(
        workspace: str, *, run_id: str, task_id: str, before_attempt: int,
        publisher=None) -> dict:
    """Recover older-attempt manifests at the governed startup boundary."""
    if isinstance(before_attempt, bool) or not isinstance(before_attempt, int) \
            or before_attempt < 1:
        raise GovernedCommandError("owned cleanup recovery attempt is invalid")
    root = (Path(workspace).resolve() / ".taskplane" /
            "owned-cleanup-v1" / "manifests")
    recovered = []
    for manifest_path in sorted(root.glob("*.json")) if root.is_dir() else []:
        if re.fullmatch(r"[0-9a-f]{32}\.json", manifest_path.name) is None:
            continue
        manifest = owned_cleanup.load_manifest(manifest_path)
        owner = manifest["owner"]
        if (owner.get("run_id") != run_id or owner.get("task_id") != task_id or
                int(owner.get("attempt") or 0) >= before_attempt):
            continue
        if manifest.get("terminal") is None:
            row = unwind_owned_failure(
                {"manifest": str(manifest_path), "workspace": workspace},
                error="startup recovered an abandoned older attempt",
                outcome="recovery", trigger="recovery", publisher=publisher)
        else:
            try:
                publication = owned_cleanup.replay_terminal_publication(
                    manifest_path, workspace=workspace, publisher=publisher)
            except Exception as exc:
                publication = {"status": "pending", "replay_required": True,
                               "error": str(exc)}
            receipt = owned_cleanup.cleanup_manifest(manifest_path)
            row = {
                "schema": RESULT_SCHEMA, "action": "startup-recovery",
                "cleanup_receipt": receipt,
                "cleanup_evidence":
                    owned_cleanup.cleanup_consumer_evidence(receipt),
                "publication_result": publication,
            }
        recovered.append(row)
    return {"schema": "taskplane.owned-cleanup-recovery/v1",
            "run_id": run_id, "task_id": task_id,
            "before_attempt": before_attempt, "recovered": recovered,
            "leak_count": sum(int(row["cleanup_receipt"]["leak_count"])
                              for row in recovered)}


def _safe_failure_unwind(context: Mapping[str, object], *, error: object,
                         process_binding=None) -> dict:
    """Keep cleanup evidence secondary to the original production failure."""
    try:
        return unwind_owned_failure(
            context, error=error, process_binding=process_binding)
    except Exception as cleanup_error:
        return {
            "schema": RESULT_SCHEMA, "action": "failure-unwind",
            "error": str(error), "cleanup_status": "attention",
            "cleanup_error": str(cleanup_error),
        }


def owned_process_resource(workspace: str, handle: str,
                           binding: Mapping[str, object], *,
                           run_id: str, task_id: str, attempt: int) -> dict:
    """Return exact process/control identity for orchestrator reservation.

    PID, process name, or the runtime path alone are deliberately
    insufficient.  The descriptor binds the reconnect token, process start
    generation, group, run/task identity, handle, and control location.
    """
    required = {"schema", "pid", "pgid", "started", "token"}
    value = dict(binding)
    if (set(value) != required or value.get("schema") !=
            "taskplane.detached-command-binding/v1" or
            not str(value.get("token") or "") or
            not str(value.get("started") or "")):
        raise GovernedCommandError("owned process binding is invalid")
    if not re.fullmatch(r"[0-9a-f]{32}", str(handle)):
        raise GovernedCommandError("owned process handle is invalid")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise GovernedCommandError("owned process attempt is invalid")
    root = _runtime_root(str(Path(workspace).resolve()))
    return {
        "kind": "process-group",
        "containment_root": str(root),
        "relative_name": str(handle),
        "stable_identity": {
            "handle": str(handle), "run_id": str(run_id),
            "task_id": str(task_id), "attempt": attempt, "binding_digest":
                _canonical_digest(value),
        },
        "observed_identity": value,
    }


def _adapter(workspace: str, authorization: str, *, host: str,
             launcher, binding: Mapping[str, object] | None = None,
             owned_cleanup_context: Mapping[str, object] | None = None) \
        -> CommandAdapter:
    adapter = CommandAdapter(
        host=host, runtime=_runtime(
            workspace, authorization,
            owned_cleanup_context=owned_cleanup_context),
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
    if action in {"launch", "checkpoint"}:
        supplied_attempt = value.get("attempt")
        value["attempt"] = _resolved_cleanup_attempt(
            workspace, run_id=str(value["run_id"]),
            task_id=str(value["task_id"]), supplied=supplied_attempt)
        if int(value["attempt"]) > 1:
            recover_owned_cleanup(
                workspace, run_id=str(value["run_id"]),
                task_id=str(value["task_id"]),
                before_attempt=int(value["attempt"]))
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
            spec, authority = _consume_semantic_checkpoint_authorization(
                workspace,
                lifecycle_authorization=str(value["authorization"]),
                run_id=str(value["run_id"]),
                task_id=str(value["task_id"]),
                token=value.get("checkpoint_authority"))
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
            "attempt": value["attempt"],
        }
        authorization = str(value["authorization"])
        checkpoint_authorization_fingerprint = hashlib.sha256(
            str(value["checkpoint_authority"]).encode("utf-8")).hexdigest()
        root = _runtime_root(workspace)
        token = secrets.token_hex(16)
        handoff = root / "handoffs" / f"{token}.json"
        cleanup_context = _prepare_owned_cleanup(
            workspace, authorization, run_id=str(value["run_id"]),
            task_id=str(value["task_id"]), attempt=int(value["attempt"]),
            token=token)
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
            _activate_owned_process(cleanup_context, binding)
            return HostLaunch(binding=binding)

        try:
            adapter = _adapter(
                workspace, authorization, host="codex", launcher=launch,
                owned_cleanup_context=
                    _runtime_cleanup_context(cleanup_context))
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
                    "checkpoint_authorization_fingerprint":
                        checkpoint_authorization_fingerprint,
                    "plan_fingerprint": authority["plan_fingerprint"],
                    "task_fingerprint": authority["task_fingerprint"],
                    "selection_fingerprint":
                        authority["selection_fingerprint"],
                    "contract_fingerprint":
                        authority["active_contract_fingerprint"],
                    "step": authority["step"],
                    "target_sha": authority["source_sha"],
                },
            }
            _atomic_json(_control_path(root, handle), control)
            publish_owned_handoff(
                cleanup_context, token=token,
                run_id=str(value["run_id"]), task_id=str(value["task_id"]),
                attempt=int(value["attempt"]), handle=handle, payload={
                "schema": "taskplane.governed-command-handoff/v1",
                "kind": "semantic-checkpoint",
                "workspace": workspace,
                "authorization": authorization,
                "root": str(root), "handle": handle,
                "argv": list(spec["focused_proof"]["argv"]),
                "cwd": sandbox, "deadline": deadline,
                "identity": identity, "authority": authority,
                "checkpoint_authorization_fingerprint":
                    checkpoint_authorization_fingerprint,
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
            unwind = _safe_failure_unwind(
                cleanup_context, error=exc,
                process_binding=(dict(started["binding"])
                                 if "binding" in started else None))
            return {
                "schema": RESULT_SCHEMA,
                "action": "checkpoint",
                "status": "unavailable",
                "reason_code": exc.reason_code,
                "error": str(exc),
                **{key: unwind[key] for key in (
                    "cleanup_receipt", "cleanup_evidence",
                    "publication_replay", "publication_result")
                   if key in unwind},
                **({"cleanup_error": unwind["cleanup_error"],
                    "cleanup_status": "attention"}
                   if "cleanup_error" in unwind else {}),
            }
        except OSError as exc:
            shutil.rmtree(Path(sandbox).parent, ignore_errors=True)
            unwind = _safe_failure_unwind(
                cleanup_context, error=exc,
                process_binding=(dict(started["binding"])
                                 if "binding" in started else None))
            return {
                "schema": RESULT_SCHEMA,
                "action": "checkpoint",
                "status": "unavailable",
                "reason_code": "checkpoint_process_launch_unavailable",
                "error": ("semantic checkpoint process boundary could not "
                          "be established; no proof process was started"),
                **{key: unwind[key] for key in (
                    "cleanup_receipt", "cleanup_evidence",
                    "publication_replay", "publication_result")
                   if key in unwind},
                **({"cleanup_error": unwind["cleanup_error"],
                    "cleanup_status": "attention"}
                   if "cleanup_error" in unwind else {}),
            }
        except Exception as exc:
            unwind = _safe_failure_unwind(
                cleanup_context, error=exc,
                process_binding=(dict(started["binding"])
                                 if "binding" in started else None))
            try:
                exc.cleanup_result = unwind
            except Exception:
                pass
            raise

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
                    "task_id": value["task_id"],
                    "attempt": value["attempt"]}
        authority = _governed_launch_authority(
            workspace, cwd, list(argv), identity)
        token = secrets.token_hex(16)
        handoff = root / "handoffs" / f"{token}.json"
        cleanup_context = _prepare_owned_cleanup(
            workspace, authorization, run_id=str(value["run_id"]),
            task_id=str(value["task_id"]), attempt=int(value["attempt"]),
            token=token)
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
            _activate_owned_process(cleanup_context, binding)
            return HostLaunch(binding=binding)

        try:
            adapter = _adapter(
                workspace, authorization, host=host, launcher=launch,
                owned_cleanup_context=
                    _runtime_cleanup_context(cleanup_context))
            handle = adapter.launch(
                list(argv), cwd=cwd, deadline=value.get("deadline"),
                wave_id=value.get("wave_id"), identity=identity)
            binding = dict(started["binding"])
            control = {"schema": "taskplane.governed-command-control/v1",
                       "host": host, "binding": binding,
                       "binding_digest": _canonical_digest(binding)}
            _atomic_json(_control_path(root, handle), control)
            publish_owned_handoff(
                cleanup_context, token=token, run_id=str(value["run_id"]),
                task_id=str(value["task_id"]),
                attempt=int(value["attempt"]), handle=handle, payload={
                "schema": "taskplane.governed-command-handoff/v1",
                "workspace": workspace, "authorization": authorization,
                "root": str(root), "handle": handle, "argv": list(argv),
                "cwd": cwd, "deadline": value.get("deadline"),
                "identity": identity, "authority": authority,
            })
            return _snapshot_result("launch", adapter, handle)
        except Exception as exc:
            unwind = _safe_failure_unwind(
                cleanup_context, error=exc,
                process_binding=(dict(started["binding"])
                                 if "binding" in started else None))
            try:
                exc.cleanup_result = unwind
            except Exception:
                pass
            raise

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
        return finalize_owned_result(
            _snapshot_result("wait", adapter, handle, event=event),
            trigger="terminal")
    if action == "reconnect":
        event = adapter.reconnect(
            handle, binding=dict(control["binding"]),
            ownership_check=detached_process_is_live)
        return finalize_owned_result(
            _snapshot_result("reconnect", adapter, handle, event=event),
            trigger="recovery")
    if action in {"cancel", "interrupt", "handoff"}:
        event = adapter.cancel(handle)
        selected_outcome = {
            "cancel": "cancellation", "interrupt": "interruption",
            "handoff": "handoff",
        }[action]
        return finalize_owned_result(
            _snapshot_result(action, adapter, handle, event=event),
            trigger="handoff" if action == "handoff" else "terminal",
            outcome=selected_outcome)
    return finalize_owned_result(
        _snapshot_result(
            "show", adapter, handle,
            lifecycle=adapter.snapshot(handle).get("lifecycle") or []),
        trigger="recovery")


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
            return value
        raise GovernedCommandError("detached command handoff is invalid")
    raise GovernedCommandError("detached command handoff timed out")


def _semantic_process_rows(authority: Mapping) -> list[dict]:
    """Read one bounded POSIX process table through the bound inspector."""
    binding = authority.get("process_inspector_binding") or {}
    _recheck_regular_file_binding(binding, label="process inspector")
    if sys.platform == "darwin":
        import ctypes
        import ctypes.util

        library = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
        libproc = ctypes.CDLL(library, use_errno=True)
        libproc.proc_listpids.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        libproc.proc_listpids.restype = ctypes.c_int
        libproc.proc_pidinfo.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
            ctypes.c_void_p, ctypes.c_int]
        libproc.proc_pidinfo.restype = ctypes.c_int
        pids = (ctypes.c_int * 65536)()
        size = int(libproc.proc_listpids(
            1, 0, ctypes.byref(pids), ctypes.sizeof(pids)))
        if size <= 0 or size >= ctypes.sizeof(pids):
            raise GovernedCommandUnavailable(
                "checkpoint_process_tree_unavailable",
                "semantic checkpoint process-tree inspection failed")
        rows = []
        for pid in pids[:size // ctypes.sizeof(ctypes.c_int)]:
            if pid <= 0:
                continue
            buffer = ctypes.create_string_buffer(256)
            observed = int(libproc.proc_pidinfo(
                int(pid), 3, 0, ctypes.byref(buffer), ctypes.sizeof(buffer)))
            if observed < 136:
                continue
            rows.append({
                "pid": int.from_bytes(buffer.raw[12:16], sys.byteorder),
                "ppid": int.from_bytes(buffer.raw[16:20], sys.byteorder),
                "pgid": int.from_bytes(buffer.raw[100:104], sys.byteorder),
                "started": buffer.raw[120:136].hex(),
            })
        if rows:
            return rows
    proc_root = Path("/proc")
    if proc_root.is_dir():
        rows = []
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "stat").read_text(encoding="utf-8")
                suffix = raw[raw.rindex(")") + 2:].split()
                rows.append({
                    "pid": int(entry.name), "ppid": int(suffix[1]),
                    "pgid": int(suffix[2]), "started": suffix[19],
                })
            except (OSError, ValueError, IndexError):
                continue
        if rows:
            return rows
    try:
        result = subprocess.run(
            [str(binding["path"]), "-axo", "pid=,ppid=,pgid=,lstart="],
            env=_checkpoint_environment(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            text=True, encoding="utf-8", errors="replace", timeout=2)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GovernedCommandUnavailable(
            "checkpoint_process_tree_unavailable",
            "semantic checkpoint process-tree inspection failed") from exc
    if result.returncode != 0 or len(result.stdout) > 4 * 1024 * 1024:
        raise GovernedCommandUnavailable(
            "checkpoint_process_tree_unavailable",
            "semantic checkpoint process-tree inspection failed")
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 8:
            continue
        try:
            pid, ppid, pgid = map(int, fields[:3])
        except ValueError:
            continue
        rows.append({
            "pid": pid, "ppid": ppid, "pgid": pgid,
            "started": " ".join(fields[3:8]),
        })
    if not rows:
        raise GovernedCommandUnavailable(
            "checkpoint_process_tree_unavailable",
            "semantic checkpoint process-tree inspection returned no state")
    return rows


def _semantic_descendant_ownership(
        process: subprocess.Popen, *, pgid: int,
        authority: Mapping) -> tuple[dict[int, dict], bool]:
    rows = _semantic_process_rows(authority)
    by_parent: dict[int, list[dict]] = {}
    for row in rows:
        by_parent.setdefault(row["ppid"], []).append(row)
    descendants: dict[int, dict] = {}
    frontier = [int(process.pid)]
    while frontier:
        parent = frontier.pop()
        for row in by_parent.get(parent, []):
            if row["pid"] not in descendants:
                descendants[row["pid"]] = row
                frontier.append(row["pid"])
    escaped = any(row["pgid"] != pgid for row in descendants.values())
    return descendants, escaped


def _owned_semantic_pid_live(identity: Mapping, authority: Mapping) -> bool:
    return any(row["pid"] == identity.get("pid") and
               row["started"] == identity.get("started")
               for row in _semantic_process_rows(authority))


def _semantic_group_live(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise GovernedCommandUnavailable(
            "checkpoint_process_tree_unavailable",
            "semantic checkpoint process-group ownership is unverifiable") \
            from exc


def _signal_semantic_group(pgid: int, selected_signal: signal.Signals) -> None:
    try:
        os.killpg(pgid, selected_signal)
    except ProcessLookupError:
        pass


def _signal_owned_semantic_pids(
        owned: Mapping[int, Mapping], selected_signal: signal.Signals,
        authority: Mapping) -> None:
    for pid, identity in sorted(owned.items(), reverse=True):
        if _owned_semantic_pid_live(identity, authority):
            try:
                os.kill(int(pid), selected_signal)
            except ProcessLookupError:
                pass


def _terminate_semantic_process_tree(
        process: subprocess.Popen, *, deadline: float,
        pgid: int | None = None, owned: Mapping[int, Mapping] | None = None,
        authority: Mapping | None = None) -> None:
    """Terminate and verify the complete observed proof tree in-budget.

    Process-group cleanup handles ordinary descendants.  The continuously
    captured identity ledger additionally owns a descendant that attempted a
    new group/session, so such an escape is killed and the proof fails closed.
    """
    selected_pgid = int(pgid if pgid is not None else process.pid)
    identities = dict(owned or {})
    if authority is not None:
        _signal_owned_semantic_pids(identities, signal.SIGTERM, authority)
    _signal_semantic_group(selected_pgid, signal.SIGTERM)
    cleanup_deadline = min(deadline, time.time() + _CHECKPOINT_REAP_SECONDS)
    while time.time() < cleanup_deadline:
        root_live = process.poll() is None
        group_live = _semantic_group_live(selected_pgid)
        owned_live = (authority is not None and any(
            _owned_semantic_pid_live(identity, authority)
            for identity in identities.values()))
        if not root_live and not group_live and not owned_live:
            return
        time.sleep(0.01)
    if authority is not None:
        _signal_owned_semantic_pids(identities, signal.SIGKILL, authority)
    _signal_semantic_group(selected_pgid, signal.SIGKILL)
    while time.time() < deadline:
        root_live = process.poll() is None
        group_live = _semantic_group_live(selected_pgid)
        owned_live = (authority is not None and any(
            _owned_semantic_pid_live(identity, authority)
            for identity in identities.values()))
        if not root_live and not group_live and not owned_live:
            return
        time.sleep(0.01)
    raise GovernedCommandUnavailable(
        "checkpoint_process_reap_timeout",
        "semantic checkpoint proof process tree did not reap in-budget")


def _semantic_checkpoint_worker(handoff: Mapping, runtime: CommandRuntime,
                                handle: str, captured: bytearray) -> int:
    authority = dict(handoff.get("authority") or {})
    workspace = str(handoff["workspace"])
    sandbox = str(handoff["cwd"])
    deadline = float(handoff["deadline"])
    state = "failed"
    returncode = 1
    reason = "semantic checkpoint failed"
    process = None
    reader = None
    proof_pgid = None
    owned: dict[int, dict] = {}
    proof_completed = False
    post_authority_verified = False
    control_fingerprint = None
    cancellation_requested = threading.Event()
    previous_handlers = {}

    def request_cancellation(_signum, _frame):
        cancellation_requested.set()

    try:
        for selected_signal in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[selected_signal] = signal.getsignal(selected_signal)
            signal.signal(selected_signal, request_cancellation)
        _assert_checkpoint_authority_current(
            workspace, authority, use_bound_state_path=True)
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
        proof_pgid = int(os.getpgid(process.pid))
        if proof_pgid != int(process.pid):
            raise GovernedCommandUnavailable(
                "checkpoint_process_tree_unavailable",
                "semantic checkpoint proof has no owned process group")

        def drain() -> None:
            assert process.stdout is not None
            while chunk := process.stdout.read(8192):
                remaining = _CAPTURE_LIMIT - len(captured)
                if remaining > 0:
                    captured.extend(chunk[:remaining])

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        execution_deadline = max(
            time.time(), deadline - _CHECKPOINT_REAP_SECONDS)
        timed_out = False
        escaped = False
        while True:
            descendants, observed_escape = _semantic_descendant_ownership(
                process, pgid=proof_pgid, authority=authority)
            if len(descendants) > 512:
                raise GovernedCommandUnavailable(
                    "checkpoint_process_tree_unavailable",
                    "semantic checkpoint descendant limit was exceeded")
            owned.update(descendants)
            escaped = escaped or observed_escape
            if escaped:
                raise GovernedCommandUnavailable(
                    "checkpoint_process_tree_escape",
                    "semantic checkpoint descendant attempted to escape its "
                    "owned process group")
            if cancellation_requested.is_set():
                state = "cancelled"
                reason = "semantic checkpoint was cancelled"
                break
            observed_returncode = process.poll()
            if observed_returncode is not None:
                returncode = int(observed_returncode)
                proof_completed = True
                break
            if time.time() >= execution_deadline:
                timed_out = True
                state = "timed_out"
                reason = "semantic checkpoint deadline elapsed"
                break
            time.sleep(0.005)
        if not timed_out and state != "cancelled":
            state = "succeeded" if returncode == 0 else "failed"
            reason = state
    except BaseException as exc:
        state = "failed"
        reason = (f"semantic checkpoint unavailable: {exc.reason_code}"
                  if isinstance(exc, GovernedCommandUnavailable) else
                  f"semantic checkpoint failed: {type(exc).__name__}: {exc}")
    finally:
        if process is not None:
            try:
                if process.poll() is not None:
                    returncode = int(process.returncode)
                    proof_completed = True
                _terminate_semantic_process_tree(
                    process, deadline=deadline, pgid=proof_pgid,
                    owned=owned, authority=authority)
            except BaseException as exc:
                state = "failed"
                reason = (f"semantic checkpoint unavailable: {exc.reason_code}"
                          if isinstance(exc, GovernedCommandUnavailable) else
                          "semantic checkpoint process cleanup failed: "
                          f"{type(exc).__name__}: {exc}")
            if reader is not None:
                reader.join(timeout=max(0.0, deadline - time.time()))
                if reader.is_alive():
                    state = "failed"
                    reason = ("semantic checkpoint unavailable: "
                              "checkpoint_output_reap_timeout")
        if proof_completed:
            try:
                _assert_checkpoint_authority_current(
                    workspace, authority, use_bound_state_path=True)
                control = _read_control(Path(handoff["root"]), handle)
                semantic = control.get("semantic") or {}
                expected_semantic = {
                    "schema": CHECKPOINT_BOUNDARY_SCHEMA,
                    "authority_fingerprint": authority["fingerprint"],
                    "checkpoint_authorization_fingerprint": str(
                        handoff["checkpoint_authorization_fingerprint"]),
                    "plan_fingerprint": authority["plan_fingerprint"],
                    "task_fingerprint": authority["task_fingerprint"],
                    "selection_fingerprint":
                        authority["selection_fingerprint"],
                    "contract_fingerprint":
                        authority["active_contract_fingerprint"],
                    "step": authority["step"],
                    "target_sha": authority["source_sha"],
                }
                if semantic != expected_semantic:
                    raise GovernedCommandUnavailable(
                        "checkpoint_control_changed",
                        "semantic checkpoint control binding changed before "
                        "receipt")
                control_fingerprint = _canonical_digest(control)
                post_authority_verified = True
            except BaseException as exc:
                state = "failed"
                reason = (f"semantic checkpoint unavailable: {exc.reason_code}"
                          if isinstance(exc, GovernedCommandUnavailable) else
                          "semantic checkpoint post-proof authority failed: "
                          f"{type(exc).__name__}: {exc}")
        for selected_signal, previous in previous_handlers.items():
            signal.signal(selected_signal, previous)
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
            "checkpoint_authorization_fingerprint": handoff.get(
                "checkpoint_authorization_fingerprint"),
            "plan_fingerprint": authority.get("plan_fingerprint"),
            "task_fingerprint": authority.get("task_fingerprint"),
            "selection_fingerprint": authority.get(
                "selection_fingerprint"),
            "contract_fingerprint": authority.get(
                "active_contract_fingerprint"),
            "control_fingerprint": control_fingerprint,
            "target_sha": authority.get("source_sha"),
            "step": authority.get("step"),
            "post_authority_verified": post_authority_verified,
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
            "process_inspector_binding_fingerprint": (
                authority.get("process_inspector_binding") or {}).get(
                    "fingerprint"),
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
