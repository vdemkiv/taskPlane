"""Durable exact-owned cleanup with evidence preservation and replay.

The manifest is deletion authority, not a hint: a resource is reserved before
creation and activated only after its stable identity has been observed.  A
terminal outcome and its evidence are sealed before any destructive action.
Every cleanup revalidates all resources first and refuses the whole action if
even one target is ambiguous or no longer has its activated identity.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePath
import shutil
import signal
import stat
import tempfile
import time
from typing import Callable, Mapping, Sequence


MANIFEST_SCHEMA = "taskplane.owned-resource-manifest/v1"
RECEIPT_SCHEMA = "taskplane.cleanup-receipt/v1"
_TERMINAL_OUTCOMES = frozenset({
    "success", "failure", "cancellation", "interruption", "timeout",
    "handoff", "recovery",
})
_RESOURCE_KINDS = frozenset({
    "worktree", "worker-contract", "process-group", "cache",
    "generated-state", "test-artifact",
})
_DIGEST = frozenset("0123456789abcdef")


class OwnedCleanupError(RuntimeError):
    """The cleanup protocol could not establish exact destructive authority."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def file_sha256(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def receipt_digest(receipt: Mapping[str, object]) -> str:
    return _digest({key: copy.deepcopy(value) for key, value in receipt.items()
                    if key != "receipt_digest"})


def _manifest_digest(manifest: Mapping[str, object]) -> str:
    return _digest({key: copy.deepcopy(value) for key, value in manifest.items()
                    if key != "manifest_digest"})


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            json.dump(value, target, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _save_manifest(path: Path, manifest: dict) -> dict:
    value = copy.deepcopy(manifest)
    value["manifest_digest"] = _manifest_digest(value)
    _atomic_json(path, value)
    return value


def _closed_owner(value: object) -> dict:
    required = {
        "repository_id", "workspace_fingerprint", "settings_digest",
        "run_id", "task_id", "attempt",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise OwnedCleanupError("cleanup owner identity is incomplete")
    if (not all(isinstance(value[key], str) and value[key]
                for key in required - {"attempt"}) or
            not all(len(value[key]) == 64 and set(value[key]) <= _DIGEST
                    for key in ("workspace_fingerprint", "settings_digest")) or
            isinstance(value["attempt"], bool) or
            not isinstance(value["attempt"], int) or value["attempt"] < 1):
        raise OwnedCleanupError("cleanup owner identity is invalid")
    return copy.deepcopy(value)


def _relative_name(value: object) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise OwnedCleanupError("resource relative name is invalid")
    path = PurePath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise OwnedCleanupError("resource containment is invalid")
    return str(path)


def _absolute_lexical(path: str | os.PathLike[str]) -> str:
    value = os.path.abspath(os.fspath(path))
    if not os.path.isabs(value):
        raise OwnedCleanupError("resource containment root must be absolute")
    return value


def _target(resource: Mapping[str, object]) -> str:
    root = _absolute_lexical(str(resource.get("containment_root") or ""))
    relative = _relative_name(resource.get("relative_name"))
    target = os.path.abspath(os.path.join(root, relative))
    try:
        contained = os.path.commonpath((root, target)) == root
    except ValueError as exc:
        raise OwnedCleanupError("resource containment is invalid") from exc
    if not contained or target == root:
        raise OwnedCleanupError("resource containment is invalid")
    return target


def _assert_no_symlink_path(root: str, target: str) -> None:
    current = Path(root)
    if os.path.lexists(current) and stat.S_ISLNK(current.lstat().st_mode):
        raise OwnedCleanupError("resource containment root is symlinked")
    relative = os.path.relpath(target, root)
    for part in PurePath(relative).parts:
        current = current / part
        if not os.path.lexists(current):
            break
        if stat.S_ISLNK(current.lstat().st_mode):
            raise OwnedCleanupError("resource path is symlinked")


def _path_identity(path: str) -> dict:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise OwnedCleanupError("resource target is symlinked")
    if stat.S_ISREG(info.st_mode):
        kind = "file"
    elif stat.S_ISDIR(info.st_mode):
        kind = "directory"
    else:
        raise OwnedCleanupError("resource target type is unsupported")
    parent = os.stat(os.path.dirname(path), follow_symlinks=False)
    value = {
        "schema": "taskplane.owned-path-identity/v1",
        "type": kind,
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": stat.S_IMODE(info.st_mode),
        "links": info.st_nlink,
        "parent_device": parent.st_dev,
        "parent_inode": parent.st_ino,
    }
    if kind == "file":
        value.update(size=info.st_size, sha256=file_sha256(path))
    return value


def create_manifest(path: str | os.PathLike[str], *, repository_id: str,
                    workspace_fingerprint: str, settings_digest: str,
                    run_id: str, task_id: str, attempt: int,
                    evidence_root: str | os.PathLike[str]) -> dict:
    """Publish the manifest root before any owned resource is created."""
    manifest_path = Path(path).absolute()
    if os.path.lexists(manifest_path):
        raise OwnedCleanupError("owned resource manifest already exists")
    owner = _closed_owner({
        "repository_id": repository_id,
        "workspace_fingerprint": workspace_fingerprint,
        "settings_digest": settings_digest,
        "run_id": run_id,
        "task_id": task_id,
        "attempt": attempt,
    })
    evidence = _absolute_lexical(evidence_root)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "owner": owner,
        "evidence_root": evidence,
        "resources": {},
        "terminal": None,
        "journal": [],
        "created_at_ns": time.time_ns(),
    }
    return _save_manifest(manifest_path, manifest)


def load_manifest(path: str | os.PathLike[str]) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OwnedCleanupError("owned resource manifest is unavailable") from exc
    if (not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA or
            value.get("manifest_digest") != _manifest_digest(value) or
            not isinstance(value.get("resources"), dict) or
            not isinstance(value.get("journal"), list)):
        raise OwnedCleanupError("owned resource manifest is invalid or tampered")
    _closed_owner(value.get("owner"))
    return copy.deepcopy(value)


def reserve_resource(path: str | os.PathLike[str], *, kind: str,
                     containment_root: str | os.PathLike[str],
                     relative_name: str, creator_nonce: str,
                     stable_identity: Mapping[str, object],
                     evidence_refs: Sequence[str] = (),
                     dependencies: Sequence[str] = (),
                     policy: Mapping[str, object] | None = None) -> str:
    """CAS-style append of one exact reservation before resource creation."""
    manifest_path = Path(path).absolute()
    manifest = load_manifest(manifest_path)
    if manifest.get("terminal") is not None:
        raise OwnedCleanupError("terminal manifest cannot reserve resources")
    if kind not in _RESOURCE_KINDS:
        raise OwnedCleanupError("resource kind is invalid")
    if not isinstance(creator_nonce, str) or not creator_nonce:
        raise OwnedCleanupError("resource creator nonce is required")
    if not isinstance(stable_identity, Mapping) or not stable_identity:
        raise OwnedCleanupError("resource stable identity is required")
    root = _absolute_lexical(containment_root)
    relative = _relative_name(relative_name)
    owner = copy.deepcopy(manifest["owner"])
    material = {
        "owner": owner, "kind": kind, "containment_root": root,
        "relative_name": relative, "creator_nonce": creator_nonce,
    }
    resource_id = "res-" + _digest(material)[:32]
    if resource_id in manifest["resources"]:
        raise OwnedCleanupError("resource reservation is ambiguous")
    missing = set(dependencies) - set(manifest["resources"])
    if missing:
        raise OwnedCleanupError("resource dependency is not reserved")
    candidate = os.path.abspath(os.path.join(root, relative))
    for existing in manifest["resources"].values():
        if _target(existing) == candidate:
            raise OwnedCleanupError("multiple resources address the same target")
    manifest["resources"][resource_id] = {
        "resource_id": resource_id,
        **material,
        "stable_identity": copy.deepcopy(dict(stable_identity)),
        "observed_identity": None,
        "evidence_refs": [str(item) for item in evidence_refs],
        "dependencies": [str(item) for item in dependencies],
        "policy": copy.deepcopy(dict(policy or {})),
        "state": "reserved",
    }
    _save_manifest(manifest_path, manifest)
    return resource_id


def activate_resource(path: str | os.PathLike[str], resource_id: str, *,
                      observed_identity: Mapping[str, object] | None = None) -> dict:
    """Activate a reservation using the identity observed after creation."""
    manifest_path = Path(path).absolute()
    manifest = load_manifest(manifest_path)
    resource = manifest["resources"].get(resource_id)
    if not isinstance(resource, dict) or resource.get("state") != "reserved":
        raise OwnedCleanupError("resource reservation is unavailable")
    target = _target(resource)
    _assert_no_symlink_path(str(resource["containment_root"]), target)
    if not os.path.lexists(target):
        raise OwnedCleanupError("reserved resource was not created")
    path_identity = _path_identity(target)
    if resource["kind"] == "process-group":
        if not isinstance(observed_identity, Mapping):
            raise OwnedCleanupError("process resource requires a stable binding")
        binding = copy.deepcopy(dict(observed_identity))
        required = {"schema", "pid", "pgid", "started", "token"}
        if (set(binding) != required or binding.get("schema") !=
                "taskplane.detached-command-binding/v1" or
                not str(binding.get("started") or "") or
                not str(binding.get("token") or "")):
            raise OwnedCleanupError("process resource binding is invalid")
        observed = {"path": path_identity, "process": binding}
    else:
        if observed_identity is not None:
            raise OwnedCleanupError("filesystem identity is observed internally")
        observed = path_identity
    resource["observed_identity"] = observed
    resource["state"] = "active"
    manifest["journal"].append({
        "event": "activated", "resource_id": resource_id,
        "identity_digest": _digest(observed),
    })
    _save_manifest(manifest_path, manifest)
    return copy.deepcopy(resource)


def _copy_evidence(source: Path, destination: Path) -> dict:
    if stat.S_ISLNK(source.lstat().st_mode) or not source.is_file():
        raise OwnedCleanupError("cleanup evidence must be a non-symlink file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    _fsync_directory(destination.parent)
    return {
        "name": source.name,
        "source_sha256": file_sha256(source),
        "sealed_path": str(destination),
        "sha256": file_sha256(destination),
        "bytes": destination.stat().st_size,
    }


def seal_terminal(path: str | os.PathLike[str], *, outcome: str,
                  evidence: Mapping[str, str | os.PathLike[str]]) -> dict:
    """Seal original terminal truth and evidence before cleanup can start."""
    manifest_path = Path(path).absolute()
    manifest = load_manifest(manifest_path)
    if manifest.get("terminal") is not None:
        return copy.deepcopy(manifest["terminal"])
    if outcome not in _TERMINAL_OUTCOMES:
        raise OwnedCleanupError("cleanup terminal outcome is invalid")
    if not isinstance(evidence, Mapping) or not evidence:
        raise OwnedCleanupError("cleanup requires durable evidence")
    evidence_root = Path(str(manifest["evidence_root"])).absolute()
    evidence_root.mkdir(parents=True, exist_ok=True)
    sealed = []
    for label, raw_source in sorted(evidence.items()):
        if not isinstance(label, str) or not label:
            raise OwnedCleanupError("cleanup evidence label is invalid")
        source = Path(raw_source).absolute()
        digest = file_sha256(source)
        destination = evidence_root / (
            f"{manifest['owner']['run_id']}-{manifest['owner']['task_id']}-"
            f"{label}-{digest[:16]}.evidence")
        if destination.exists():
            if file_sha256(destination) != digest:
                raise OwnedCleanupError("sealed cleanup evidence conflicts")
            row = {"name": source.name, "source_sha256": digest,
                   "sealed_path": str(destination), "sha256": digest,
                   "bytes": destination.stat().st_size}
        else:
            row = _copy_evidence(source, destination)
        row["label"] = label
        sealed.append(row)
    terminal_material = {
        "outcome": outcome,
        "owner": copy.deepcopy(manifest["owner"]),
        "evidence": sealed,
    }
    terminal = {**terminal_material,
                "terminal_digest": _digest(terminal_material)}
    manifest["terminal"] = terminal
    manifest["journal"].append({
        "event": "terminal-sealed", "outcome": outcome,
        "terminal_digest": terminal["terminal_digest"],
    })
    _save_manifest(manifest_path, manifest)
    return copy.deepcopy(terminal)


def _ordered_resources(resources: Mapping[str, dict]) -> list[dict]:
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(resource_id: str) -> None:
        if resource_id in visiting:
            raise OwnedCleanupError("resource dependency graph is ambiguous")
        if resource_id in visited:
            return
        visiting.add(resource_id)
        resource = resources[resource_id]
        order.append(resource_id)
        for dependency in resource.get("dependencies") or []:
            if dependency not in resources:
                raise OwnedCleanupError("resource dependency is missing")
            visit(str(dependency))
        visiting.remove(resource_id)
        visited.add(resource_id)

    dependencies = {
        str(dependency)
        for resource in resources.values()
        for dependency in (resource.get("dependencies") or [])
    }
    # Start with reverse-dependency roots (resources no other cleanup action
    # depends on), then recurse inward.  Defer independent worktrees so they
    # remain last even when reservations were serialized in another order.
    roots = [identifier for identifier in resources
             if identifier not in dependencies]
    remainder = [identifier for identifier in resources
                 if identifier in dependencies]
    identifiers = sorted([*roots, *remainder], key=lambda item: (
        item in remainder, resources[item].get("kind") == "worktree"))
    for identifier in identifiers:
        visit(identifier)
    return [resources[identifier] for identifier in order]


def _current_process_started(pid: int) -> str:
    try:
        from command_adapters import _pid_start_identity
    except ImportError:
        from taskplane.command_adapters import _pid_start_identity
    return str(_pid_start_identity(pid))


def _process_status(binding: Mapping[str, object]) -> str:
    try:
        pid = int(binding["pid"])
        os.kill(pid, 0)
    except ProcessLookupError:
        return "absent"
    except (KeyError, TypeError, ValueError, PermissionError, OSError):
        return "ambiguous"
    try:
        if (os.getpgid(pid) != int(binding["pgid"]) or
                _current_process_started(pid) != str(binding["started"])):
            return "reused"
    except (OSError, ValueError):
        return "ambiguous"
    return "live"


def _precheck(resource: Mapping[str, object], owner: Mapping[str, object],
              duplicate_targets: set[str]) -> tuple[bool, str, bool]:
    try:
        if _closed_owner(resource.get("owner")) != dict(owner):
            raise OwnedCleanupError("resource has a foreign owner")
        if resource.get("kind") not in _RESOURCE_KINDS:
            raise OwnedCleanupError("resource kind is invalid")
        if resource.get("state") != "active":
            raise OwnedCleanupError("resource is not activated")
        target = _target(resource)
        if target in duplicate_targets:
            raise OwnedCleanupError("resource target is ambiguous")
        root = str(resource["containment_root"])
        _assert_no_symlink_path(root, target)
        if not os.path.lexists(target):
            raise OwnedCleanupError("resource was relocated or disappeared")
        observed = resource.get("observed_identity")
        path_observed = (observed or {}).get("path") \
            if resource.get("kind") == "process-group" else observed
        if _path_identity(target) != path_observed:
            raise OwnedCleanupError("resource identity changed or is dirty")
        current_path_identity = _path_identity(target)
        if (current_path_identity.get("type") == "file" and
                current_path_identity.get("links", 1) > 1):
            raise OwnedCleanupError("resource target is hard-linked")
        if resource.get("kind") == "process-group":
            status = _process_status((observed or {}).get("process") or {})
            if status in {"reused", "ambiguous"}:
                raise OwnedCleanupError("process PID generation is reused or ambiguous")
        if resource.get("kind") == "worker-contract" and \
                resource.get("policy", {}).get("active") is not False:
            raise OwnedCleanupError("worker contract is still active")
        if resource.get("kind") == "worktree":
            policy = resource.get("policy") or {}
            if not isinstance(policy.get("merge_receipt"), dict) or \
                    not isinstance(policy.get("lifecycle"), dict):
                raise OwnedCleanupError("worktree cleanup proof is unavailable")
            try:
                import worktree_cleanup
            except ImportError:
                from taskplane import worktree_cleanup
            proof = worktree_cleanup.eligibility(
                policy["merge_receipt"], lifecycle=policy["lifecycle"])
            if proof.get("outcome") != "pending":
                raise OwnedCleanupError(
                    "worktree cleanup refused: " + str(proof.get("reason")))
        return True, "eligible", True
    except (OwnedCleanupError, OSError, ValueError) as exc:
        target = None
        try:
            target = _target(resource)
        except OwnedCleanupError:
            pass
        exists = bool(target and os.path.lexists(target))
        return False, str(exc), exists


def _assert_tree_removable(path: str) -> None:
    for root, directories, files in os.walk(path, topdown=True,
                                            followlinks=False):
        for name in [*directories, *files]:
            candidate = os.path.join(root, name)
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode):
                raise OwnedCleanupError("owned directory contains a symlink")
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise OwnedCleanupError("owned directory contains a hard link")


def _remove_filesystem_target(target: str) -> None:
    info = os.lstat(target)
    if stat.S_ISDIR(info.st_mode):
        _assert_tree_removable(target)
        shutil.rmtree(target)
    elif stat.S_ISREG(info.st_mode):
        if info.st_nlink > 1:
            raise OwnedCleanupError("resource target is hard-linked")
        os.unlink(target)
    else:
        raise OwnedCleanupError("resource target type is unsupported")


def _clean_resource(resource: Mapping[str, object]) -> str:
    target = _target(resource)
    kind = str(resource["kind"])
    if kind == "worktree":
        try:
            import worktree_cleanup
        except ImportError:
            from taskplane import worktree_cleanup
        policy = resource["policy"]
        result = worktree_cleanup.cleanup(
            policy["merge_receipt"], lifecycle=policy["lifecycle"])
        if result.get("outcome") not in {"removed", "already-clean"}:
            raise OwnedCleanupError(
                "worktree cleanup failed: " + str(result.get("reason")))
        return str(result["outcome"])
    if kind == "process-group":
        binding = resource["observed_identity"]["process"]
        status = _process_status(binding)
        if status == "live":
            os.killpg(int(binding["pgid"]), signal.SIGTERM)
            deadline = time.monotonic() + 2.0
            while _process_status(binding) == "live" and time.monotonic() < deadline:
                time.sleep(0.01)
            if _process_status(binding) == "live":
                raise OwnedCleanupError("owned process group did not terminate")
        elif status != "absent":
            raise OwnedCleanupError("owned process group identity changed")
    _remove_filesystem_target(target)
    return "removed"


def _receipt_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(manifest_path.name + ".cleanup-receipt.json")


def _journal_event(path: str | os.PathLike[str], event: Mapping[str, object]) \
        -> dict:
    """Durably append one cleanup action boundary before returning."""
    manifest_path = Path(path).absolute()
    manifest = load_manifest(manifest_path)
    row = copy.deepcopy(dict(event))
    if row.get("event") not in {
            "action-started", "action-cleaned", "action-refused"} or \
            row.get("resource_id") not in manifest["resources"]:
        raise OwnedCleanupError("cleanup journal event is invalid")
    manifest["journal"].append(row)
    return _save_manifest(manifest_path, manifest)


def _journal_states(manifest: Mapping[str, object]) -> dict[str, str]:
    states: dict[str, str] = {}
    for event in manifest.get("journal") or []:
        if event.get("event") in {
                "action-started", "action-cleaned", "action-refused"}:
            states[str(event.get("resource_id"))] = str(event["event"])
    return states


def _resource_is_absent(resource: Mapping[str, object]) -> bool:
    try:
        target_absent = not os.path.lexists(_target(resource))
    except OwnedCleanupError:
        return False
    if resource.get("kind") != "process-group":
        return target_absent
    binding = (resource.get("observed_identity") or {}).get("process") or {}
    return target_absent and _process_status(binding) == "absent"


def _load_receipt(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OwnedCleanupError("cleanup receipt is unavailable") from exc
    if (not isinstance(value, dict) or value.get("schema") != RECEIPT_SCHEMA or
            value.get("receipt_digest") != receipt_digest(value)):
        raise OwnedCleanupError("cleanup receipt is invalid or tampered")
    return value


def cleanup_manifest(path: str | os.PathLike[str]) -> dict:
    """Revalidate, clean in reverse dependencies, and prove zero leaks."""
    manifest_path = Path(path).absolute()
    prior = _load_receipt(_receipt_path(manifest_path))
    if prior is not None:
        return copy.deepcopy(prior)
    manifest = load_manifest(manifest_path)
    terminal = manifest.get("terminal")
    if not isinstance(terminal, dict) or terminal.get("terminal_digest") != \
            _digest({key: value for key, value in terminal.items()
                     if key != "terminal_digest"}):
        raise OwnedCleanupError("original terminal outcome is not sealed")
    resources = manifest["resources"]
    ordered = _ordered_resources(resources)
    journal_states = _journal_states(manifest)
    counts: dict[str, int] = {}
    targets: dict[str, str | None] = {}
    for resource in ordered:
        try:
            target = _target(resource)
        except OwnedCleanupError:
            target = None
        targets[str(resource.get("resource_id"))] = target
        if target is not None:
            counts[target] = counts.get(target, 0) + 1
    duplicates = {target for target, count in counts.items() if count > 1}
    preflight = []
    for resource in ordered:
        resource_id = str(resource.get("resource_id"))
        journal_state = journal_states.get(resource_id)
        replay_clean = (journal_state in {"action-started", "action-cleaned"}
                        and _resource_is_absent(resource))
        if replay_clean:
            eligible, reason, exists = True, "durable action postcheck is absent", False
        else:
            eligible, reason, exists = _precheck(
                resource, manifest["owner"], duplicates)
            if journal_state == "action-cleaned" and exists:
                eligible = False
                reason = "cleaned resource reappeared after durable postcheck"
        preflight.append({
            "resource_id": resource_id,
            "kind": resource.get("kind"), "eligible": eligible,
            "reason": reason, "exists": exists,
            "replay_clean": replay_clean,
        })

    results = []
    if any(not row["eligible"] for row in preflight):
        for row in preflight:
            results.append({
                "resource_id": row["resource_id"], "kind": row["kind"],
                "status": "refused" if not row["eligible"] else "preserved",
                "reason": row["reason"] if not row["eligible"] else
                          "transaction preserved after another refusal",
            })
    else:
        for resource in ordered:
            resource_id = str(resource["resource_id"])
            proof = next(row for row in preflight
                         if row["resource_id"] == resource_id)
            if proof["replay_clean"]:
                if journal_states.get(resource_id) != "action-cleaned":
                    _journal_event(manifest_path, {
                        "event": "action-cleaned", "resource_id": resource_id,
                        "postcheck": "absent-after-recovery",
                    })
                results.append({"resource_id": resource_id,
                                "kind": resource["kind"],
                                "status": "cleaned",
                                "reason": "recovered exact absent postcheck"})
                continue
            eligible, reason, _ = _precheck(resource, manifest["owner"], set())
            if not eligible:
                results.append({"resource_id": resource_id,
                                "kind": resource["kind"],
                                "status": "refused", "reason": reason})
                break
            try:
                _journal_event(manifest_path, {
                    "event": "action-started", "resource_id": resource_id,
                    "identity_digest": _digest(resource["observed_identity"]),
                })
                action = _clean_resource(resource)
                _journal_event(manifest_path, {
                    "event": "action-cleaned", "resource_id": resource_id,
                    "postcheck": "absent",
                })
                results.append({"resource_id": resource_id,
                                "kind": resource["kind"],
                                "status": "cleaned", "reason": action})
            except (OwnedCleanupError, OSError) as exc:
                _journal_event(manifest_path, {
                    "event": "action-refused", "resource_id": resource_id,
                    "reason": str(exc),
                })
                results.append({"resource_id": resource_id,
                                "kind": resource["kind"],
                                "status": "refused", "reason": str(exc)})
                break
        completed = {row["resource_id"] for row in results}
        for resource in ordered:
            if resource["resource_id"] not in completed:
                results.append({"resource_id": resource["resource_id"],
                                "kind": resource["kind"],
                                "status": "preserved",
                                "reason": "transaction stopped after refusal"})

    leaks = []
    for resource in ordered:
        target = targets.get(str(resource.get("resource_id")))
        live = bool(target and os.path.lexists(target))
        result = next(row for row in results
                      if row["resource_id"] == resource["resource_id"])
        if resource.get("kind") == "process-group":
            binding = (resource.get("observed_identity") or {}).get("process") or {}
            live = live or _process_status(binding) != "absent"
        # An unresolved/refused identity is a leak even when its former exact
        # path is absent: relocation and containment failure are specifically
        # cases where scanning for a replacement path would invent authority.
        if live or result["status"] != "cleaned":
            leaks.append({"resource_id": resource["resource_id"],
                          "kind": resource["kind"],
                          "reason": result["reason"]})
    # Bind the receipt to the final journaled manifest, not the pre-action
    # snapshot. This makes action/postcheck recovery evidence replay-stable.
    manifest = load_manifest(manifest_path)
    terminal = manifest["terminal"]
    material = {
        "schema": RECEIPT_SCHEMA,
        "manifest_digest": manifest["manifest_digest"],
        "terminal_digest": terminal["terminal_digest"],
        "original_outcome": terminal["outcome"],
        "owner": copy.deepcopy(manifest["owner"]),
        "evidence": copy.deepcopy(terminal["evidence"]),
        "resources": results,
        "leaks": leaks,
        "leak_count": len(leaks),
        "cleanup_status": "clean" if not leaks and all(
            row["status"] == "cleaned" for row in results) else "attention",
        "replay_key": _digest({
            "manifest_digest": manifest["manifest_digest"],
            "terminal_digest": terminal["terminal_digest"],
        }),
    }
    receipt = {**material, "receipt_digest": _digest(material)}
    _atomic_json(_receipt_path(manifest_path), receipt)
    return copy.deepcopy(receipt)


def seal_and_cleanup(path: str | os.PathLike[str], *, outcome: str,
                     evidence: Mapping[str, str | os.PathLike[str]]) -> dict:
    """Idempotent terminal callback: first outcome wins; callbacks replay."""
    manifest_path = Path(path).absolute()
    prior = _load_receipt(_receipt_path(manifest_path))
    if prior is not None:
        return copy.deepcopy(prior)
    seal_terminal(manifest_path, outcome=outcome, evidence=evidence)
    return cleanup_manifest(manifest_path)


def _rewrite_for_test(path: str | os.PathLike[str],
                      mutate: Callable[[dict], None]) -> None:
    """Test seam for constructing validly encoded hostile manifests."""
    manifest_path = Path(path).absolute()
    manifest = load_manifest(manifest_path)
    mutate(manifest)
    _save_manifest(manifest_path, manifest)
