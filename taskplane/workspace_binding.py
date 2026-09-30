"""Cooperative selected-folder mapping and independent execution observations.

Probe matches and declared references are not host attestation. This module never
imports the controller or grants workflow/worker authority.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
from typing import Any, Iterator, NoReturn, cast
import uuid

from . import primitives, workflow as w

REQUEST_SCHEMA = "taskplane.workspace-request/v1"
BINDING_SCHEMA = "taskplane.workspace-binding/v1"
BINDING_FILE = "workspace-binding.json"
PENDING_FILE = ".taskplane-workspace-recovery-pending.json"
MAX_BYTES = 8 * 1024 * 1024
REQUEST_BYTES = 64 * 1024
MAX_FILES = 20000
MAX_TOTAL_BYTES = 512 * 1024 * 1024
POLICIES = {"any", "local", "darwin-local"}
LOCATIONS = {"local", "remote", "unknown"}
FIELDS = {"surface", "host_root", "execution_root", "policy", "execution", "worker", "probe"}


class MissingBinding(w.Refusal):
    """First-run setup, never a corrupt binding or a lost frozen contract."""

    def __init__(self, root: Path):
        self.root = root
        super().__init__("workspace_binding", "Selected workspace needs an explicit binding before Taskplane state can be created.")

    def guidance(self) -> str:
        return ("Taskplane setup is pending. Tell the user what is missing; do not silently stop. "
                "Keep the original request, route and lens count in conversation. "
                "If a project folder is already selected, inspect that same folder and its actual execution path; "
                "do not ask to select it again. Otherwise ask the user to select the project folder. "
                "Use the installed workspace inspect and workspace bind interfaces with observed mapping/probe evidence "
                "(bind accepts --request-json without a preliminary file write). "
                "If the host cannot expose the mapping or required execution capability, name that exact blocker. "
                "After binding, activate and start the original requested route, preserving its lens count. "
                "A standalone review starts at Engineering; do not redirect it to tp-go. "
                "A status-only request stays read-only: explain pending setup and the earlier request; "
                "unreadable run history is unknown, not proof that no run exists.")

    def result(self) -> dict[str, Any]:
        return {**super().result(), "onboarding": {"state": "binding_required",
                "candidate_workspace": str(self.root), "history": "unknown", "state_created": False,
                "guidance": self.guidance()},
                "next_action": self.guidance()}


def _fail(detail: str, reason: str = "workspace_binding") -> NoReturn:
    raise w.Refusal(reason, detail)


def _text(value: Any, label: str, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        _fail(f"{label} must be a nonempty bounded string.")
    return value


def _absolute(value: str | Path) -> Path:
    raw = _text(str(value), "Workspace path", 4096)
    path = Path(raw)
    if ".." in path.parts or (os.name != "nt" and "\\" in raw):
        _fail("Workspace paths cannot contain traversal or backslashes.")
    return path.absolute()


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    """Pin every directory component without following a swapped parent link."""
    if (os.open not in os.supports_dir_fd or not hasattr(os, "O_DIRECTORY")
            or not hasattr(os, "O_NOFOLLOW")):
        _fail("This runtime lacks no-follow directory operations required for workspace binding.")
    fd = -1
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(path.anchor, flags)
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except OSError as exc:
        raise w.Refusal("workspace_binding", f"Directory unavailable or symlinked: {path}") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _root(workspace: str | Path, *, strict: bool = False) -> Path:
    root = _absolute(workspace)
    # Legacy local workflows retain their existing path/platform behavior.
    # Secure descriptor operations are mandatory once binding is selected.
    if (not strict and not _required(root) and not _exists(root / ".taskplane" / BINDING_FILE)
            and not _exists(root / PENDING_FILE)):
        if not root.is_dir():
            _fail(f"Workspace directory is unavailable: {root}")
        return root.resolve()
    with _directory(root):
        pass
    return root


def _exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise w.Refusal("workspace_binding", f"Cannot inspect {path}.") from exc


def _alias(path: Path) -> None:
    # A native namespace may not exist in this runtime. Existing components may
    # never be symlinks; absence alone does not prove the mapping.
    for part in reversed((path, *path.parents)):
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            break
        if not stat.S_ISDIR(mode):
            _fail(f"Host alias is not an ordinary directory: {part}")


def _read(path: Path, limit: int = MAX_BYTES) -> bytes:
    try:
        with _directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                    _fail(f"Expected a bounded ordinary file: {path}")
                raw = stream.read(limit + 1)
                if len(raw) > limit:
                    _fail(f"File exceeds its byte limit: {path}")
                return raw
    except OSError as exc:
        raise w.Refusal("workspace_binding", f"Cannot safely read ordinary file: {path}") from exc


def _object(path: Path, limit: int = MAX_BYTES) -> dict[str, Any]:
    try:
        value = json.loads(_read(path, limit))
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, w.Refusal):
            raise
        raise w.Refusal("workspace_binding", f"Invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        _fail(f"Expected a JSON object: {path}")
    return cast(dict[str, Any], value)


def _write(path: Path, value: dict[str, Any], *, exclusive: bool = True) -> None:
    raw = primitives.canonical_bytes(value, trailing_newline=True)
    temporary = f".{path.name}.{uuid.uuid4().hex}.tmp"
    with _directory(path.parent) as parent:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if exclusive:
                os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
            else:
                os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            os.unlink(temporary, dir_fd=parent)


def _relative(value: Any) -> str:
    raw = _text(value, "Relative path", 4096)
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or "\\" in raw or raw in {".", ""}:
        _fail("Path must be workspace-relative without traversal.")
    return path.as_posix()


def _identity(root: Path) -> dict[str, int]:
    with _directory(root) as fd:
        info = os.fstat(fd)
    return {"device": info.st_dev, "inode": info.st_ino}


def _observation(value: Any, name: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"location", "reference"}:
        _fail(f"{name} needs separate location and observation reference fields.")
    if not isinstance(value["location"], str) or value["location"] not in LOCATIONS:
        _fail(f"Unknown {name} location.")
    return {"location": value["location"], "reference": _text(value["reference"], name + " reference")}


def _request(root: Path, request: dict[str, Any], *, recovery: bool = False) -> dict[str, Any]:
    extra = {"expected_project_id", "request_reference"} if recovery else set()
    if not isinstance(request, dict) or set(request) != FIELDS | {"schema"} | extra:
        _fail("Workspace request has missing or unsupported fields.")
    if request["schema"] != REQUEST_SCHEMA:
        _fail("Unsupported workspace request schema.")
    if not isinstance(request["surface"], str) or request["surface"] not in {"cowork", "claude-code", "codex", "other"}:
        _fail("Unsupported workspace surface.")
    if not isinstance(request["policy"], str) or request["policy"] not in POLICIES:
        _fail("Unsupported workspace execution policy.")
    for key in ("host_root", "execution_root"):
        raw = _text(request[key], key, 4096)
        if not Path(raw).is_absolute() or str(_absolute(raw)) != raw:
            _fail(f"{key} must be a normalized absolute path.")
    if request["execution_root"] != str(root):
        _fail("Selected execution root does not match the requested workspace.")
    host = Path(request["host_root"])
    _alias(host)
    if host != root and (host.is_relative_to(root) or root.is_relative_to(host)):
        _fail("Host and execution aliases may not overlap as nested directories.")
    execution = _observation(request["execution"], "Execution")
    worker = _observation(request["worker"], "Worker")
    probe = request["probe"]
    if not isinstance(probe, dict) or set(probe) != {"path", "sha256", "host_reference"}:
        _fail("Supply an explicit host-side probe path, SHA-256 and host reference.")
    probe_path = _relative(probe["path"])
    if Path(probe_path).parts[0] == ".taskplane":
        _fail("The host probe must be outside runtime state.")
    digest = _text(probe["sha256"], "Host probe SHA-256", 64)
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        _fail("Host probe SHA-256 is invalid.")
    host_reference = _text(probe["host_reference"], "Host probe observation reference")
    if host_reference in {execution["reference"], worker["reference"]}:
        _fail("Host-side probe observation must be separate from execution and worker observations.")
    if hashlib.sha256(_read(root / probe_path, REQUEST_BYTES)).hexdigest() != digest:
        _fail("Execution-side probe does not match the declared host-side digest.")
    result = {key: request[key] for key in FIELDS}
    result.update(execution=execution, worker=worker,
                  probe={"path": probe_path, "sha256": digest, "host_reference": host_reference})
    _policy(result, worker=False)
    return result


def _runtime(name: str) -> dict[str, str]:
    prefix = "TASKPLANE_" + name.upper()
    location = os.environ.get(prefix + "_LOCATION", "unknown")
    reference = os.environ.get(prefix + "_REFERENCE", "")
    if location not in LOCATIONS or len(reference) > 1024 or "\0" in reference:
        _fail(f"Malformed runtime {name} observation.", "execution_policy")
    if location != "unknown" and not reference.strip():
        _fail(f"Runtime {name} observation needs {prefix}_REFERENCE.", "execution_policy")
    return {"location": location, "reference": reference}


def _policy(binding: dict[str, Any], *, worker: bool) -> None:
    selected = os.environ.get("TASKPLANE_WORKSPACE_POLICY")
    if selected and selected not in POLICIES:
        _fail("Unknown TASKPLANE_WORKSPACE_POLICY.", "execution_policy")
    ranks = {"any": 0, "local": 1, "darwin-local": 2}
    policy = max((binding["policy"], selected or "any"), key=lambda x: ranks[x])
    for name in ("execution", "worker") if worker else ("execution",):
        observed = _runtime(name)
        declared = binding[name]
        if observed["location"] != "unknown" and observed["location"] != declared["location"]:
            _fail(f"Runtime {name} location conflicts with the workspace binding.", "execution_policy")
        if policy != "any" and (declared["location"] != "local"
                                or observed["location"] != "local" or not observed["reference"].strip()):
            _fail(f"Policy {policy} requires a current local {name} observation and reference; "
                  "a mounted folder is insufficient.", "execution_policy")
    if policy == "darwin-local" and platform.system() != "Darwin":
        _fail("Policy darwin-local requires the executing platform to be Darwin.", "execution_policy")


def _required(root: Path, event: dict[str, Any] | None = None) -> bool:
    surface = os.environ.get("TASKPLANE_SURFACE", "").lower()
    if event and isinstance(event.get("surface"), str):
        surface = event["surface"].lower()
    suspicious = any(root == Path(base) or root.is_relative_to(base)
                     for base in ("/sessions", "/mnt/data", "/mnt/workspace", "/home/claude"))
    return bool(surface == "cowork" or os.environ.get("TASKPLANE_WORKSPACE")
                or os.environ.get("TASKPLANE_WORKSPACE_POLICY") or suspicious)


def load(workspace: str | Path, allow_relocated: bool = False) -> dict[str, Any] | None:
    """Read a binding without initialization. Relocated reads grant no authority."""
    root = _root(workspace)
    if _exists(root / PENDING_FILE):
        _fail("Workspace recovery was interrupted; preserve the pending record and recovery archive.")
    path = root / ".taskplane" / BINDING_FILE
    if (root / ".taskplane").is_symlink():
        _fail("Runtime state cannot follow a symlink.")
    if not _exists(path):
        return None
    if _exists(root / ".taskplane"):
        with _directory(path.parent):
            pass
    value = _object(path, REQUEST_BYTES)
    fields = FIELDS | {"schema", "project_id", "digest", "execution_identity", "created_at"}
    if set(value) != fields or value.get("schema") != BINDING_SCHEMA:
        _fail("Workspace binding schema or fields are invalid.")
    if not re.fullmatch(r"[a-f0-9]{32}", _text(value.get("project_id"), "Project ID", 32)):
        _fail("Workspace project identity is invalid.")
    digest = primitives.content_fingerprint({k: v for k, v in value.items() if k != "digest"})
    if value.get("digest") != digest:
        _fail("Workspace binding digest changed or is corrupt.")
    identity = value.get("execution_identity")
    if not isinstance(identity, dict) or set(identity) != {"device", "inode"} or any(
            type(item) is not int or item < 0 for item in identity.values()):
        _fail("Workspace directory identity is invalid.")
    _text(value.get("created_at"), "Binding creation time", 64)
    relocated = value["execution_root"] != str(root) or identity != _identity(root)
    if relocated and not allow_relocated:
        _fail("Workspace binding was relocated or copied; inspect and explicitly recover inactive history.")
    # Validate persisted syntax even when its former root/probe is unavailable.
    if (not isinstance(value.get("surface"), str)
            or value["surface"] not in {"cowork", "claude-code", "codex", "other"}
            or not isinstance(value.get("policy"), str) or value["policy"] not in POLICIES):
        _fail("Stored workspace surface or policy is invalid.")
    for key in ("host_root", "execution_root"):
        raw = _text(value[key], key, 4096)
        if not Path(raw).is_absolute() or str(_absolute(raw)) != raw:
            _fail(f"Stored {key} is not a normalized absolute path.")
    _observation(value["execution"], "Execution")
    _observation(value["worker"], "Worker")
    probe = value["probe"]
    if not isinstance(probe, dict) or set(probe) != {"path", "sha256", "host_reference"}:
        _fail("Stored host probe is invalid.")
    _relative(probe["path"])
    if not re.fullmatch(r"[a-f0-9]{64}", _text(probe["sha256"], "Probe digest", 64)):
        _fail("Stored host probe digest is invalid.")
    _text(probe["host_reference"], "Host observation")
    return value


def _missing_binding(root: Path) -> NoReturn:
    """Only a root without controller history qualifies for first-run guidance."""
    store = root / ".taskplane"
    if _exists(store):
        with _directory(store):
            pass
        try:
            for count, entry in enumerate(store.iterdir(), 1):
                if count > MAX_FILES:
                    _fail("Controller inventory exceeds its entry bound; preserve existing state.")
                # Canonical stores, initialization markers, restoration copies and
                # archive indexes all belong to controller history. No JSON needs
                # to be trusted or loaded to rule out first-run onboarding.
                if entry.name.startswith("workflow-"):
                    _fail("Existing workflow state has no workspace binding; preserve existing state and "
                          "restore the original binding before resuming. Use workspace inspect to diagnose; "
                          "normal first-run bind/start cannot recover an active or unverified history.")
        except OSError as exc:
            raise w.Refusal("workspace_binding", "Cannot inspect controller history; preserve existing state.") from exc
    raise MissingBinding(root)


def ensure(workspace: str | Path, worker: bool = False,
           expected: dict[str, Any] | str | None = None) -> dict[str, Any] | None:
    """Revalidate mapping, probe, policy and an optional frozen run contract."""
    root = _root(workspace)
    value = load(root)
    if value is None:
        if expected is not None:
            _fail("Workspace binding required by the frozen run contract is missing; preserve existing state.")
        if _required(root):
            _missing_binding(root)
        return None
    request = {"schema": REQUEST_SCHEMA, **{k: value[k] for k in FIELDS}}
    _request(root, request)
    _policy(value, worker=worker)
    if expected is not None:
        valid = value["digest"] == expected if isinstance(expected, str) else all(
            value[key] == expected.get(key) for key in ("project_id", "digest"))
        if not valid:
            _fail("Workspace binding differs from the frozen run contract.")
    return value


def resolve_workspace(workspace: str | Path | None,
                      event: dict[str, Any] | None = None) -> Path:
    """Select one root. Hook cwd is incidental when an explicit root is configured."""
    configured = os.environ.get("TASKPLANE_WORKSPACE")
    incidental = (event or {}).get("cwd") or os.getcwd()
    selected = configured or workspace or incidental
    if not isinstance(selected, (str, Path)):
        _fail("Selected workspace is not a path.")
    root = _root(selected)
    value = ensure(root)
    if configured and workspace is not None:
        explicit = _absolute(workspace)
        aliases = {str(root)} | ({value["host_root"]} if value else set())
        if str(explicit) not in aliases:
            _fail("Explicit workspace conflicts with TASKPLANE_WORKSPACE and its declared host alias.")
    if value is None and _required(root, event):
        _missing_binding(root)
    return root


def relative_path(workspace: str | Path, value: str) -> str:
    """Translate only the bound aliases; never expand the caller's exact scope."""
    root = _root(workspace)
    binding = ensure(root)
    raw = _text(value, "Target path", 4096)
    path = Path(raw)
    # Native Windows separators normalize here; POSIX backslashes remain literal
    # and are still refused. Scope paths always use the POSIX representation.
    normalized = path.as_posix()
    if ".." in path.parts or "\\" in normalized:
        _fail("Target contains traversal or backslashes.", "scope_violation")
    if path.is_absolute():
        aliases = [root] + ([Path(binding["host_root"])] if binding else [])
        matches = [path.relative_to(alias) for alias in aliases if path.is_relative_to(alias)]
        if not matches:
            _fail("Target is outside the selected workspace aliases.", "scope_violation")
        relative = _relative(matches[0].as_posix())
    else:
        if path.anchor:
            _fail("Target must not be drive-relative or root-relative.", "scope_violation")
        relative = _relative(normalized)
    target = root / relative
    # Nonexistent leaf paths are valid write targets, but existing components may
    # never redirect subsequent exact-scope checks through a link.
    for part in [root / Path(*Path(relative).parts[:i]) for i in range(1, len(Path(relative).parts) + 1)]:
        if part.is_symlink():
            _fail("Workspace target cannot follow a symlink.", "scope_violation")
    if not target.resolve().is_relative_to(root):
        _fail("Target escaped the selected workspace.", "scope_violation")
    return relative


def describe(workspace: str | Path) -> dict[str, Any]:
    """Read-only diagnostics, including relocated bindings and policy failures."""
    root = _root(workspace)
    value = load(root, allow_relocated=True)
    result: dict[str, Any] = {"schema": "taskplane.workspace-inspection/v1", "workspace": str(root),
                              "binding": value, "assurance": "cooperative_observation",
                              "host_attestation": False, "live_cowork_verified": False}
    try:
        ensure(root)
        result["status"] = "bound" if value else "unbound"
    except w.Refusal as exc:
        result.update(exc.result())
    return result


def bind(workspace: str | Path, request: dict[str, Any]) -> dict[str, Any]:
    """Create once, only after validating the explicit host and runtime evidence."""
    root = _root(workspace, strict=True)
    fields = _request(root, request)
    prior = load(root)
    if prior:
        if {key: prior[key] for key in FIELDS} != fields:
            _fail("Existing workspace binding differs; it cannot be overwritten.")
        return cast(dict[str, Any], ensure(root))
    _inactive(root, str(root))
    value = _new(root, fields)
    try:
        with _directory(root) as parent:
            try:
                os.mkdir(".taskplane", mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
        _write(root / ".taskplane" / BINDING_FILE, value)
    except OSError as exc:
        raise w.Refusal("workspace_binding", "Workspace binding could not be created exclusively.") from exc
    return value


def _new(root: Path, fields: dict[str, Any], project_id: str | None = None) -> dict[str, Any]:
    value = {"schema": BINDING_SCHEMA, **fields, "project_id": project_id or uuid.uuid4().hex,
             "execution_identity": _identity(root), "created_at": datetime.now(timezone.utc).isoformat()}
    return {**value, "digest": primitives.content_fingerprint(value)}


def _recovery_state(state: dict[str, Any], workspace: str, root: str, run: str,
                    *, historical: bool = False) -> None:
    try:
        if (state.get("schema") != "taskplane.workflow/v1" or state.get("workspace") != workspace
                or state.get("root") != root or state.get("run") != run
                or state.get("profile") != "native_workflow"):
            _fail("Stored workflow identity cannot be validated for recovery.")
        w.validate_state(state)
        retired = state.get("retired")
        if retired is not None and (not isinstance(retired, dict)
                or not all(isinstance(retired.get(key), str) and retired[key].strip()
                           for key in ("request_reference", "reason", "at"))
                or retired.get("accepted") is not False or retired.get("assurance") != "observed"):
            _fail("Stored retirement provenance cannot be validated.")
        if state.get("superseded_by") is not None and not isinstance(state["superseded_by"], str):
            _fail("Stored replacement identity cannot be validated.")
        baseline = state.get("source_baseline")
        if (not isinstance(baseline, dict) or len(baseline) > MAX_FILES
                or not all(isinstance(key, str) and isinstance(value, str) for key, value in baseline.items())):
            _fail("Stored source baseline cannot be validated.")
        if not historical and not (state["finished"] or state.get("retired") or state.get("superseded_by")):
            _fail(f"Workflow {run} is active; retire it in its original binding before recovery.")
        handles = state.get("observed_handles")
        workers = state.get("workers", {})
        if not isinstance(handles, dict) or not isinstance(workers, dict):
            _fail("Stored process or worker inventory cannot be validated.")
        handle_states = {"completed", "failed", "cancelled"} | ({"running"} if historical else set())
        worker_states = {"result_pending", "accepted", "failed", "interrupted"}
        if historical:
            worker_states |= {"prepared", "launch_pending", "bootstrapping", "running", "cancel_requested", "unknown"}
        for handle in handles.values():
            if (not isinstance(handle, dict) or handle.get("state") not in handle_states
                    or type(handle.get("revision")) is not int or not isinstance(handle.get("visit"), str)):
                _fail("A process is live or unknown; retire it in its original binding.")
        for key, worker in workers.items():
            if (not isinstance(worker, dict) or worker.get("grant_id") != key
                    or worker.get("run") != run or worker.get("root") != root
                    or type(worker.get("attempt")) is not int or not isinstance(worker.get("paths"), list)
                    or worker.get("state") not in worker_states):
                _fail("A worker is live or invalid; stop it in its original binding.")
    except (KeyError, TypeError, IndexError) as exc:
        raise w.Refusal("workspace_binding", "Stored workflow cannot be validated; history was left intact.") from exc


def _restoration_history(root: Path, old_root: str, name: str, current: dict[str, Any],
                         copies: set[str], receipts: set[str]) -> None:
    """Recognize completed restorations only; historical flags grant no authority."""
    from . import workflow_local, workflow_retention
    store = root / ".taskplane"
    matched: set[str] = set()
    fields = {"schema", "workspace", "root", "run", "revision", "source", "sha256",
              "request_reference", "state_bytes_preserved", "approvals_changed", "status"}
    for receipt_name in sorted(receipts):
        receipt = _object(store / receipt_name, REQUEST_BYTES)
        source, run, revision = (receipt.get(key) for key in ("source", "run", "revision"))
        reference = receipt.get("request_reference")
        if (set(receipt) != fields or receipt.get("schema") != "taskplane.initialization-recovery/v1"
                or receipt.get("workspace") != old_root or receipt.get("root") != current["root"]
                or receipt.get("status") != "restored" or receipt.get("state_bytes_preserved") is not True
                or receipt.get("approvals_changed") is not False
                or not isinstance(source, str) or source not in copies or source in matched
                or not isinstance(run, str) or not run or type(revision) is not int or revision < 0
                or not isinstance(reference, str) or not 0 < len(reference) <= 512
                or reference != reference.strip()
                or receipt_name != name[:-5] + ".recovery-" + str(receipt.get("sha256")) + ".json"):
            _fail("Restoration receipt is incomplete or does not match its controller and source.")
        raw = _read(store / source)
        if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
            _fail("Historical restoration copy checksum does not match its receipt.")
        try:
            db = json.loads(raw)
            if (not isinstance(db, dict) or any(db.get(key) != current[key]
                    for key in ("schema", "workspace", "root", "profile"))
                    or not isinstance(db.get("runs"), dict) or db.get("active") != run
                    or run not in db["runs"]):
                _fail("Historical restoration controller identity or active run is invalid.")
            workflow_retention.validate_index(db)
            for key in db["runs"].keys() | db.get("archives", {}).keys():
                state = workflow_retention.read(root, db, key)
                _recovery_state(state, old_root, current["root"], key, historical=True)
                workflow_local.LocalWorkflow().validate_state(state)
                latest = workflow_retention.read(root, current, key)
                if (state["revision"] > latest["revision"]
                        or state.get("workspace_contract") != latest.get("workspace_contract")):
                    _fail("Historical restoration run differs from its current controller history.")
            if db["runs"][run]["revision"] != revision:
                _fail("Historical restoration run revision does not match its receipt.")
        except (ValueError, UnicodeError, KeyError, TypeError, IndexError, AttributeError) as exc:
            if isinstance(exc, w.Refusal):
                raise
            raise w.Refusal("workspace_binding", "Historical restoration copy is invalid.") from exc
        matched.add(source)
    if matched != copies:
        _fail("Historical restoration copies require complete matching receipts.")


def _inactive(root: Path, old_root: str) -> None:
    store = root / ".taskplane"
    if not _exists(store):
        return
    with _directory(store):
        pass
    names: set[str] = set()
    for entry in store.iterdir():
        if len(names) >= MAX_FILES:
            _fail("Controller inventory exceeds its entry bound.")
        names.add(entry.name)
    stores = sorted(name for name in names if re.fullmatch(r"workflow-[a-f0-9]{32}\.json", name))
    markers = {name.replace(".initialized.json", ".json") for name in names
               if re.fullmatch(r"workflow-[a-f0-9]{32}\.initialized\.json", name)}
    copies = {name for name in names if re.fullmatch(r"workflow-[a-f0-9]{32} [1-9][0-9]*\.json", name)}
    receipts = {name for name in names
                if re.fullmatch(r"workflow-[a-f0-9]{32}\.recovery-[a-f0-9]{64}\.json", name)}
    if any(name.startswith("workflow-") and name.endswith(".json")
           and name not in stores and name not in copies and name not in receipts
           and name.replace(".initialized.json", ".json") not in markers
           for name in names):
        _fail("Unknown controller filename cannot be validated.")
    if set(stores) != markers:
        _fail("Controller initialization markers are incomplete; preserve the original state.")
    if any(name.split(" ", 1)[0].split(".", 1)[0] + ".json" not in stores for name in copies | receipts):
        _fail("Historical restoration artifacts have no canonical controller.")
    for name in stores:
        db = _object(store / name)
        marker = _object(store / name.replace(".json", ".initialized.json"), REQUEST_BYTES)
        owner = db.get("root")
        if (db.get("schema") != "taskplane.control/v1" or db.get("workspace") != old_root
                or db.get("profile") != "native_workflow" or not isinstance(owner, str) or not owner
                or name != f"workflow-{hashlib.sha256(owner.encode()).hexdigest()[:32]}.json"
                or not isinstance(db.get("runs"), dict) or "active" not in db
                or marker != {"schema": "taskplane.local-initialization/v1", "workspace": old_root,
                              "root": owner, "profile": "native_workflow"}):
            _fail("Controller identity or initialization marker cannot be validated.")
        if db["active"] is not None:
            _fail("A workflow is selected as active; retire it in its original binding before recovery.")
        for run, state in db["runs"].items():
            if not isinstance(state, dict):
                _fail("Stored workflow is invalid.")
            _recovery_state(state, old_root, owner, run)
        # Archived workflow references are structurally and digest validated by
        # their existing read-only reader; this does not create context objects.
        from . import workflow_retention
        workflow_retention.validate_index(db)
        for run in db.get("archives", {}):
            _recovery_state(workflow_retention.read(root, db, run), old_root, owner, run)
        _restoration_history(root, old_root, name, db,
                             {copy for copy in copies if copy.startswith(name[:-5] + " ")},
                             {receipt for receipt in receipts if receipt.startswith(name[:-5] + ".")})


def _manifest(store: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    pending = [store]
    total = count = 0
    while pending:
        parent = pending.pop()
        with _directory(parent):
            pass
        for entry in parent.iterdir():
            count += 1
            if count > MAX_FILES:
                _fail("Recovery inventory exceeds its entry bound.")
            info = entry.lstat()
            if stat.S_ISDIR(info.st_mode):
                pending.append(entry)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > MAX_TOTAL_BYTES:
                    _fail("Recovery inventory exceeds its byte bound.")
                result[entry.relative_to(store).as_posix()] = hashlib.sha256(_read(entry)).hexdigest()
            else:
                _fail(f"Recovery cannot archive a symlink or special file: {entry}")
    return result


def recover(workspace: str | Path, request: dict[str, Any]) -> dict[str, Any]:
    """Archive inactive history unchanged; never move approvals into the new store."""
    root = _root(workspace, strict=True)
    old = load(root, allow_relocated=True)
    if old is None:
        _fail("No prior workspace binding exists to recover.")
    fields = _request(root, request, recovery=True)
    reference = _text(request["request_reference"], "Recovery request provenance")
    if request["expected_project_id"] != old["project_id"]:
        _fail("Recovery expected project ID does not match the preserved binding.")
    if old["execution_root"] == str(root) and old["execution_identity"] == _identity(root):
        _fail("This binding has not relocated; recovery cannot replace its policy or identity.")
    if old["execution_root"] != str(root) and _exists(Path(old["execution_root"])):
        _fail("Original workspace still exists; a copied binding cannot establish relocation continuity.")
    if (fields["probe"]["sha256"] == old["probe"]["sha256"]
            or fields["probe"]["host_reference"] == old["probe"]["host_reference"]):
        _fail("Recovery requires fresh host-side probe bytes and a fresh host observation reference.")
    manifest = _manifest(root / ".taskplane")
    _inactive(root, old["execution_root"])
    value = _new(root, fields, old["project_id"])
    token = uuid.uuid4().hex
    archive = root / (".taskplane-recovery-" + token)
    staging = root / (".taskplane-rebind-" + token)
    record = {"schema": "taskplane.workspace-recovery/v1", "archive": str(archive),
              "request_reference": reference, "project_id": old["project_id"],
              "prior_digest": old["digest"], "digest": value["digest"], "files": manifest,
              "approvals_transferred": False}
    try:
        # All refusal checks above are read-only. Pending survives a crash so a
        # partial rename can never look like a new unbound legacy workspace.
        _write(root / PENDING_FILE, record)
        with _directory(root) as parent:
            os.mkdir(staging.name, mode=0o700, dir_fd=parent)
        _write(staging / BINDING_FILE, value)
        _write(staging / "workspace-recovery.json", record)
        if _manifest(root / ".taskplane") != manifest:
            _fail("Runtime history changed during recovery; pending record preserves the refusal.")
        with _directory(root) as parent:
            os.rename(".taskplane", archive.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.rename(staging.name, ".taskplane", src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
            os.unlink(PENDING_FILE, dir_fd=parent)
            os.fsync(parent)
    except OSError as exc:
        raise w.Refusal("workspace_binding", "Recovery was interrupted; preserve pending record and archive.") from exc
    return {"status": "recovered", "binding": value, "recovery": record,
            "next_action": "Start a new workflow; archived approvals and context identities are historical only."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    for operation in ("inspect", "bind", "recover"):
        command = sub.add_parser(operation)
        command.add_argument("--workspace", required=True)
        if operation != "inspect":
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--request")
            source.add_argument("--request-json", help="Inline request object, at most 64 KiB")
    args = parser.parse_args(argv)
    try:
        if args.operation == "inspect":
            result = describe(args.workspace)
        else:
            if args.request_json is not None:
                if len(args.request_json.encode('utf-8')) > REQUEST_BYTES:
                    _fail("Inline workspace request exceeds 64 KiB.")
                try:
                    request = json.loads(args.request_json)
                except (ValueError, UnicodeError):
                    _fail("Inline workspace request must be a JSON object.")
                if not isinstance(request, dict):
                    _fail("Inline workspace request must be a JSON object.")
            else:
                request = _object(_absolute(args.request), REQUEST_BYTES)
            result = bind(args.workspace, request) if args.operation == "bind" else recover(args.workspace, request)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (w.Refusal, OSError) as exc:
        result = exc.result() if isinstance(exc, w.Refusal) else {
            "status": "blocked", "reason": "workspace_binding", "detail": str(exc)}
        print(json.dumps(result, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
