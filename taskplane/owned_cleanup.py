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
from contextlib import contextmanager
from typing import Callable, Mapping, Sequence

try:
    import fcntl as _file_lock
except ImportError:  # pragma: no cover - exercised by windows-latest
    _file_lock = None
    import msvcrt as _windows_lock


MANIFEST_SCHEMA = "taskplane.owned-resource-manifest/v1"
RECEIPT_SCHEMA = "taskplane.cleanup-receipt/v1"
PUBLICATION_REPLAY_SCHEMA = "taskplane.dashboard-publication-replay/v1"
CLEANUP_EVIDENCE_SCHEMA = "taskplane.cleanup-consumer-evidence/v1"
PUBLICATION_SOURCE_SCHEMA = "taskplane.cleanup-publication-source/v1"
PUBLICATION_ATTESTATION_SCHEMA = \
    "taskplane.owned-cleanup-publication-attestation/v1"
PUBLICATION_RECEIPT_SCHEMA = "taskplane.owned-cleanup-publication-receipt/v1"
_TERMINAL_OUTCOMES = frozenset({
    "success", "failure", "cancellation", "interruption", "timeout",
    "handoff", "recovery",
})
_RESOURCE_KINDS = frozenset({
    "worktree", "worker-contract", "process-group", "cache",
    "generated-state", "test-artifact",
})
_DIGEST = frozenset("0123456789abcdef")
_PUBLICATION_PUBLISHER = None


class OwnedCleanupError(RuntimeError):
    """The cleanup protocol could not establish exact destructive authority."""


def configure_publication_publisher(publisher) -> None:
    """Inject the canonical dashboard adapter at a composition root."""
    global _PUBLICATION_PUBLISHER
    if publisher is not None and not callable(publisher):
        raise TypeError("cleanup publication publisher must be callable")
    _PUBLICATION_PUBLISHER = publisher


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _dashboard_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _lock_file(handle) -> None:
    if _file_lock is not None:
        _file_lock.flock(handle.fileno(), _file_lock.LOCK_EX)
        return
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    _windows_lock.locking(handle.fileno(), _windows_lock.LK_LOCK, 1)


def _unlock_file(handle) -> None:
    if _file_lock is not None:
        _file_lock.flock(handle.fileno(), _file_lock.LOCK_UN)
        return
    handle.seek(0)
    _windows_lock.locking(handle.fileno(), _windows_lock.LK_UNLCK, 1)


@contextmanager
def _manifest_lock(path: Path, *, suffix: str = ".lock"):
    """Serialize one durable transition; process death releases the lock."""
    lock_path = path.with_name(path.name + suffix)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        _lock_file(handle)
        try:
            yield
        finally:
            _unlock_file(handle)


def _save_manifest(path: Path, manifest: dict, *,
                   expected_revision: int) -> dict:
    """Revision-checked atomic replacement; callers must hold the lock."""
    current = load_manifest(path)
    if current["revision"] != expected_revision:
        raise OwnedCleanupError(
            f"manifest revision conflict: expected {expected_revision}, "
            f"observed {current['revision']}")
    value = copy.deepcopy(manifest)
    value["revision"] = expected_revision + 1
    value["manifest_digest"] = _manifest_digest(value)
    _atomic_json(path, value)
    return value


def _mutate_manifest(path: Path,
                     mutate: Callable[[dict], object]) -> tuple[dict, object]:
    with _manifest_lock(path):
        manifest = load_manifest(path)
        revision = manifest["revision"]
        result = mutate(manifest)
        committed = _save_manifest(
            path, manifest, expected_revision=revision)
        return committed, result


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
    root_path = Path(root)
    current = Path(root_path.anchor)
    for part in root_path.parts[1:]:
        current = current / part
        if os.path.lexists(current) and stat.S_ISLNK(current.lstat().st_mode):
            raise OwnedCleanupError("resource containment root is symlinked")
    relative = os.path.relpath(target, root)
    for part in PurePath(relative).parts:
        current = current / part
        if not os.path.lexists(current):
            break
        if stat.S_ISLNK(current.lstat().st_mode):
            raise OwnedCleanupError("resource path is symlinked")


def _directory_content_identity(path: str) -> str:
    """Hash an immutable owned directory without following any links."""
    rows = []
    for root, directories, files in os.walk(path, topdown=True,
                                            followlinks=False):
        directories.sort()
        files.sort()
        for name in [*directories, *files]:
            candidate = os.path.join(root, name)
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode):
                raise OwnedCleanupError("owned directory contains a symlink")
            relative = os.path.relpath(candidate, path)
            if stat.S_ISDIR(info.st_mode):
                rows.append({"path": relative, "type": "directory",
                             "mode": stat.S_IMODE(info.st_mode)})
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink > 1:
                    raise OwnedCleanupError(
                        "owned directory contains a hard link")
                rows.append({"path": relative, "type": "file",
                             "mode": stat.S_IMODE(info.st_mode),
                             "bytes": info.st_size,
                             "sha256": file_sha256(candidate)})
            else:
                raise OwnedCleanupError(
                    "owned directory contains an unsupported target")
    return _digest(rows)


def _path_identity(path: str, *, include_directory_content: bool = False) -> dict:
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
    elif include_directory_content:
        value["content_digest"] = _directory_content_identity(path)
    return value


def _evidence_labels(value: Sequence[str]) -> list[str]:
    if any(not isinstance(item, str) for item in value):
        raise OwnedCleanupError("resource evidence references are invalid")
    labels = list(value)
    if (not labels or len(labels) != len(set(labels)) or
            any(not label or not label.replace("-", "").replace("_", "").
                isalnum() for label in labels)):
        raise OwnedCleanupError("resource evidence references are invalid")
    return labels


def _assert_evidence_external(manifest: Mapping[str, object],
                              target: str | None = None) -> None:
    evidence = _absolute_lexical(str(manifest.get("evidence_root") or ""))
    evidence_path = Path(evidence)
    current = Path(evidence_path.anchor)
    for part in evidence_path.parts[1:]:
        current = current / part
        if os.path.lexists(current) and stat.S_ISLNK(current.lstat().st_mode):
            raise OwnedCleanupError("cleanup evidence root is symlinked")
    if target is not None:
        targets = [target]
    else:
        targets = []
        for resource in (manifest.get("resources") or {}).values():
            try:
                targets.append(_target(resource))
            except OwnedCleanupError:
                # An unresolvable target is never deleted. Preserve terminal
                # evidence first, then let the cleanup precheck emit the exact
                # containment refusal and leak receipt.
                continue
    for candidate in targets:
        if candidate is None:
            continue
        try:
            if os.path.commonpath((candidate, evidence)) == candidate:
                raise OwnedCleanupError(
                    "cleanup evidence root is inside a deletable target")
        except ValueError as exc:
            raise OwnedCleanupError(
                "cleanup evidence externality is invalid") from exc


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
        "revision": 0,
        "created_at_ns": time.time_ns(),
    }
    _assert_evidence_external(manifest)
    with _manifest_lock(manifest_path):
        if os.path.lexists(manifest_path):
            raise OwnedCleanupError("owned resource manifest already exists")
        value = copy.deepcopy(manifest)
        value["manifest_digest"] = _manifest_digest(value)
        _atomic_json(manifest_path, value)
        return value


def load_manifest(path: str | os.PathLike[str]) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OwnedCleanupError("owned resource manifest is unavailable") from exc
    if (not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA or
            value.get("manifest_digest") != _manifest_digest(value) or
            not isinstance(value.get("resources"), dict) or
            not isinstance(value.get("journal"), list) or
            isinstance(value.get("revision"), bool) or
            not isinstance(value.get("revision"), int) or
            value["revision"] < 0):
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
    if kind not in _RESOURCE_KINDS:
        raise OwnedCleanupError("resource kind is invalid")
    if not isinstance(creator_nonce, str) or not creator_nonce:
        raise OwnedCleanupError("resource creator nonce is required")
    if not isinstance(stable_identity, Mapping) or not stable_identity:
        raise OwnedCleanupError("resource stable identity is required")
    root = _absolute_lexical(containment_root)
    relative = _relative_name(relative_name)
    labels = _evidence_labels(evidence_refs)
    manifest_path = Path(path).absolute()
    resource_id = ""

    def mutate(manifest: dict) -> None:
        nonlocal resource_id
        if manifest.get("terminal") is not None:
            raise OwnedCleanupError("terminal manifest cannot reserve resources")
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
        _assert_evidence_external(manifest, candidate)
        for existing in manifest["resources"].values():
            if _target(existing) == candidate:
                raise OwnedCleanupError(
                    "multiple resources address the same target")
        manifest["resources"][resource_id] = {
            "resource_id": resource_id,
            **material,
            "stable_identity": copy.deepcopy(dict(stable_identity)),
            "stable_identity_digest": None,
            "observed_identity": None,
            "evidence_refs": labels,
            "dependencies": [str(item) for item in dependencies],
            "policy": copy.deepcopy(dict(policy or {})),
            "state": "reserved",
        }

    _mutate_manifest(manifest_path, mutate)
    return resource_id


def activate_resource(path: str | os.PathLike[str], resource_id: str, *,
                      observed_identity: Mapping[str, object] | None = None) -> dict:
    """Activate a reservation using the identity observed after creation."""
    manifest_path = Path(path).absolute()
    activated: dict = {}

    def mutate(manifest: dict) -> None:
        nonlocal activated
        resource = manifest["resources"].get(resource_id)
        if not isinstance(resource, dict) or resource.get("state") != "reserved":
            raise OwnedCleanupError("resource reservation is unavailable")
        target = _target(resource)
        _assert_no_symlink_path(str(resource["containment_root"]), target)
        if not os.path.lexists(target):
            raise OwnedCleanupError("reserved resource was not created")
        path_identity = _path_identity(
            target,
            include_directory_content=resource["kind"] in {
                "cache", "generated-state", "test-artifact"},
        )
        if resource["kind"] == "process-group":
            if not isinstance(observed_identity, Mapping):
                raise OwnedCleanupError(
                    "process resource requires a stable binding")
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
                raise OwnedCleanupError(
                    "filesystem identity is observed internally")
            observed = path_identity
        stable_digest = _digest(resource["stable_identity"])
        resource["stable_identity_digest"] = stable_digest
        resource["observed_identity"] = observed
        resource["state"] = "active"
        manifest["journal"].append({
            "event": "activated", "resource_id": resource_id,
            "identity_digest": _digest(observed),
            "stable_identity_digest": stable_digest,
        })
        activated = copy.deepcopy(resource)

    _mutate_manifest(manifest_path, mutate)
    return activated


def abandon_resource(path: str | os.PathLike[str], resource_id: str) -> dict:
    """Close a reservation proven never to have created its target.

    This is the failure-before-create half of reserve-before-use.  An existing
    path is never inferred to be owned and must instead be activated with its
    exact kind-specific identity before cleanup can touch it.
    """
    manifest_path = Path(path).absolute()
    abandoned: dict = {}

    def mutate(manifest: dict) -> None:
        nonlocal abandoned
        resource = manifest["resources"].get(resource_id)
        if not isinstance(resource, dict) or resource.get("state") != "reserved":
            raise OwnedCleanupError("resource reservation is unavailable")
        target = _target(resource)
        _assert_no_symlink_path(str(resource["containment_root"]), target)
        if os.path.lexists(target):
            raise OwnedCleanupError(
                "existing reserved resource requires exact activation")
        resource["state"] = "absent"
        manifest["journal"].append({
            "event": "reservation-absent", "resource_id": resource_id,
        })
        abandoned = copy.deepcopy(resource)

    _mutate_manifest(manifest_path, mutate)
    return abandoned


def bind_resource_dependency(path: str | os.PathLike[str], resource_id: str,
                             dependency_id: str) -> dict:
    """CAS-bind a later reservation so reverse cleanup order stays exact."""
    manifest_path = Path(path).absolute()
    updated: dict = {}

    def mutate(manifest: dict) -> None:
        nonlocal updated
        if manifest.get("terminal") is not None:
            raise OwnedCleanupError("terminal manifest cannot change dependencies")
        resource = manifest["resources"].get(resource_id)
        if not isinstance(resource, dict) or resource.get("state") != "active":
            raise OwnedCleanupError("active resource is unavailable")
        if dependency_id not in manifest["resources"] or \
                dependency_id == resource_id:
            raise OwnedCleanupError("resource dependency is unavailable")
        dependencies = list(resource.get("dependencies") or [])
        if dependency_id not in dependencies:
            dependencies.append(dependency_id)
            resource["dependencies"] = dependencies
        updated = copy.deepcopy(resource)

    _mutate_manifest(manifest_path, mutate)
    return updated


def update_resource_policy(path: str | os.PathLike[str], resource_id: str, *,
                           expected: Mapping[str, object],
                           replacement: Mapping[str, object]) -> dict:
    """Revision-checked lifecycle attestation, used before terminal cleanup."""
    manifest_path = Path(path).absolute()
    updated: dict = {}

    def mutate(manifest: dict) -> None:
        nonlocal updated
        if manifest.get("terminal") is not None:
            raise OwnedCleanupError("terminal manifest policy is immutable")
        resource = manifest["resources"].get(resource_id)
        if not isinstance(resource, dict) or resource.get("state") != "active":
            raise OwnedCleanupError("active resource is unavailable")
        if dict(resource.get("policy") or {}) != dict(expected):
            raise OwnedCleanupError("resource policy revision conflict")
        resource["policy"] = copy.deepcopy(dict(replacement))
        updated = copy.deepcopy(resource)

    _mutate_manifest(manifest_path, mutate)
    return updated


def write_publication_replay(path: str | os.PathLike[str], *,
                             owner: Mapping[str, object], outcome: str,
                             source_revision: int,
                             source_fingerprint: str,
                             trigger: str) -> dict:
    """Persist an independently replayable dashboard publication obligation."""
    checked_owner = _closed_owner(dict(owner))
    if outcome not in _TERMINAL_OUTCOMES:
        raise OwnedCleanupError("publication replay outcome is invalid")
    if (isinstance(source_revision, bool) or
            not isinstance(source_revision, int) or source_revision < 1 or
            len(source_fingerprint) != 64 or
            set(source_fingerprint) - _DIGEST or
            trigger not in {"terminal", "handoff", "recovery"}):
        raise OwnedCleanupError("publication replay identity is invalid")
    material = {
        "schema": PUBLICATION_REPLAY_SCHEMA,
        "owner": checked_owner,
        "outcome": outcome,
        "source_revision": source_revision,
        "source_fingerprint": source_fingerprint,
        "trigger": trigger,
        "status": "pending",
        "replay_required": True,
    }
    value = {**material, "fingerprint": _digest(material)}
    destination = Path(path).absolute()
    if destination.exists():
        try:
            prior = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OwnedCleanupError(
                "publication replay evidence is unavailable") from exc
        if prior != value:
            raise OwnedCleanupError("publication replay evidence conflicts")
        return copy.deepcopy(value)
    _atomic_json(destination, value)
    return copy.deepcopy(value)


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


def _validate_publication_replay(path: Path,
                                 owner: Mapping[str, object], *,
                                 outcome: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OwnedCleanupError(
            "publication replay evidence is unavailable") from exc
    if (not isinstance(value, dict) or
            value.get("schema") != PUBLICATION_REPLAY_SCHEMA or
            value.get("owner") != dict(owner) or
            value.get("outcome") != outcome or
            value.get("replay_required") is not True or
            value.get("status") not in {"pending", "published"} or
            value.get("fingerprint") != _digest({
                key: copy.deepcopy(item) for key, item in value.items()
                if key != "fingerprint"
            })):
        raise OwnedCleanupError("publication replay evidence is invalid")
    return value


def _publication_source_identity(source_revision: int,
                                 source_fingerprint: str) -> dict:
    return {
        "schema": PUBLICATION_SOURCE_SCHEMA,
        "source_revision": source_revision,
        "source_fingerprint": source_fingerprint,
    }


def _host_surface_types():
    try:
        from taskplane import host_native
    except ImportError:
        import host_native
    return host_native.HostSurfaceSnapshot, host_native.HostSurfaceEvent


def _load_durable_publication(workspace: str,
                              publication: Mapping[str, object]) -> dict:
    """Authenticate returned snapshot/event against their durable stores."""
    if publication.get("status") == "no_active":
        raise OwnedCleanupError(
            "canonical dashboard publisher has no snapshot for source identity")
    snapshot_value = publication.get("snapshot")
    event_value = publication.get("event")
    if not isinstance(snapshot_value, Mapping) or not isinstance(
            event_value, Mapping):
        raise OwnedCleanupError(
            "canonical dashboard publisher returned no authenticated snapshot")
    HostSurfaceSnapshot, HostSurfaceEvent = _host_surface_types()
    try:
        snapshot = HostSurfaceSnapshot.from_dict(snapshot_value)
        event = HostSurfaceEvent.from_dict(event_value)
    except ValueError as exc:
        raise OwnedCleanupError(
            "canonical dashboard snapshot authentication failed") from exc
    if event.snapshot_fingerprint != snapshot.fingerprint:
        raise OwnedCleanupError(
            "canonical dashboard event does not name returned snapshot")
    surfaces = publication.get("surfaces")
    if not isinstance(surfaces, Mapping) or not surfaces or any(
            value != snapshot.fingerprint for value in surfaces.values()):
        raise OwnedCleanupError(
            "canonical dashboard surfaces do not name returned snapshot")
    try:
        try:
            from taskplane import storage as runtime_storage
        except ImportError:
            import storage as runtime_storage
        durable = runtime_storage.load_dashboard_publication(workspace)
        durable_value = durable.get("current") if isinstance(
            durable, Mapping) else None
        if not isinstance(durable_value, Mapping):
            raise OwnedCleanupError(
                "durable dashboard snapshot is unavailable")
        durable_snapshot = HostSurfaceSnapshot.from_dict(durable_value)
        event_path = Path(runtime_storage.dashboard_snapshot_store_path(
            workspace)).parent / "events.json"
        event_store = json.loads(event_path.read_text(encoding="utf-8"))
        event_values = event_store.get("events") if isinstance(
            event_store, Mapping) else None
        if (not isinstance(event_store, Mapping) or
                event_store.get("schema") != "taskplane.dashboard-events/v1" or
                not isinstance(event_values, list)):
            raise OwnedCleanupError(
                "durable dashboard event journal is invalid")
        durable_events = [HostSurfaceEvent.from_dict(row)
                          for row in event_values]
    except OwnedCleanupError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise OwnedCleanupError(
            "durable dashboard publication authentication failed") \
            from exc
    if durable_snapshot.to_dict() != snapshot.to_dict():
        raise OwnedCleanupError(
            "durable dashboard snapshot does not match returned publication")
    durable_event = next((row for row in durable_events
                          if row.fingerprint == event.fingerprint), None)
    if durable_event is None or durable_event.to_dict() != event.to_dict():
        raise OwnedCleanupError(
            "durable dashboard event does not match returned publication")
    return {
        "snapshot": snapshot.to_dict(), "event": event.to_dict(),
        "snapshot_fingerprint": snapshot.fingerprint,
        "event_fingerprint": event.fingerprint,
    }


def _verify_durable_delivery(delivered: Mapping[str, object], *,
                             durable: Mapping[str, object]) -> dict:
    """Read back delivery and prove it is the exact durable loop snapshot."""
    dashboard = delivered.get("dashboard")
    delivery = dashboard.get("delivery") if isinstance(
        dashboard, Mapping) else None
    artifacts = delivery.get("artifacts") if isinstance(
        delivery, Mapping) else None
    artifact = artifacts.get("json") if isinstance(artifacts, Mapping) else None
    if (not isinstance(artifact, Mapping) or
            artifact.get("status") != "available"):
        raise OwnedCleanupError(
            "canonical dashboard delivery source identity is unavailable")
    path = Path(str(artifact.get("path") or ""))
    if not path.is_file() or path.is_symlink():
        raise OwnedCleanupError(
            "canonical dashboard delivery artifact is unavailable")
    try:
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise OwnedCleanupError(
            "canonical dashboard delivery artifact is invalid") from exc
    if (artifact.get("sha256") != digest or
            artifact.get("bytes") != len(payload) or
            delivery.get("semantic_sha256") != digest or
            not isinstance(value, Mapping)):
        raise OwnedCleanupError(
            "canonical dashboard delivery artifact identity changed")
    HostSurfaceSnapshot, _HostSurfaceEvent = _host_surface_types()
    try:
        snapshot = HostSurfaceSnapshot.from_dict(value)
    except ValueError as exc:
        raise OwnedCleanupError(
            "canonical dashboard delivery snapshot is unauthenticated") from exc
    receipt = delivery.get("publication_receipt")
    receipt_snapshot = receipt.get("snapshot") if isinstance(
        receipt, Mapping) else None
    current = delivery.get("current_head")
    if snapshot.to_dict() != durable.get("snapshot"):
        raise OwnedCleanupError(
            "canonical dashboard delivery substituted the durable snapshot")
    if (delivery.get("status") != "published" or
            not isinstance(receipt_snapshot, Mapping) or
            receipt_snapshot.get("fingerprint") != snapshot.fingerprint or
            receipt_snapshot.get("canonical_sha256") != digest or
            receipt.get("fingerprint") != _dashboard_digest({
                key: copy.deepcopy(item) for key, item in receipt.items()
                if key != "fingerprint"
            }) or
            not isinstance(current, Mapping) or
            current.get("snapshot_fingerprint") != snapshot.fingerprint or
            current.get("receipt_fingerprint") != receipt.get("fingerprint")):
        raise OwnedCleanupError(
            "canonical dashboard delivery did not verify durable identity")
    return {
        "snapshot_fingerprint": snapshot.fingerprint,
        "canonical_sha256": digest,
        "publication_receipt_fingerprint": receipt["fingerprint"],
    }


def _publication_source_attestation(
        obligation: Mapping[str, object], durable: Mapping[str, object]) -> dict:
    """Bind cleanup source truth to the canonical durable publication."""
    material = {
        "schema": PUBLICATION_ATTESTATION_SCHEMA,
        "source": _publication_source_identity(
            int(obligation["source_revision"]),
            str(obligation["source_fingerprint"])),
        "obligation_fingerprint": str(obligation["fingerprint"]),
        "snapshot_fingerprint": str(durable["snapshot_fingerprint"]),
        "event_fingerprint": str(durable["event_fingerprint"]),
    }
    return {**material, "fingerprint": _digest(material)}


def _publication_source_receipt(
        attestation: Mapping[str, object],
        delivered: Mapping[str, object]) -> dict:
    """Acknowledge exact delivery without injecting source into the snapshot."""
    material = {
        "schema": PUBLICATION_RECEIPT_SCHEMA,
        "source": copy.deepcopy(attestation["source"]),
        "attestation_fingerprint": str(attestation["fingerprint"]),
        "snapshot_fingerprint": str(delivered["snapshot_fingerprint"]),
        "canonical_sha256": str(delivered["canonical_sha256"]),
        "publication_receipt_fingerprint": str(
            delivered["publication_receipt_fingerprint"]),
    }
    return {**material, "fingerprint": _digest(material)}


def _verify_publication_source_envelope(
        attestation: Mapping[str, object], receipt: Mapping[str, object], *,
        expected: Mapping[str, object], durable: Mapping[str, object]) -> dict:
    """Authenticate both OWNED-CLEANUP records and extract bound source."""
    attestation_material = {
        key: copy.deepcopy(value) for key, value in attestation.items()
        if key != "fingerprint"
    }
    receipt_material = {
        key: copy.deepcopy(value) for key, value in receipt.items()
        if key != "fingerprint"
    }
    if (attestation.get("schema") != PUBLICATION_ATTESTATION_SCHEMA or
            attestation.get("fingerprint") != _digest(attestation_material) or
            receipt.get("schema") != PUBLICATION_RECEIPT_SCHEMA or
            receipt.get("fingerprint") != _digest(receipt_material) or
            receipt.get("attestation_fingerprint") !=
            attestation.get("fingerprint") or
            attestation.get("source") != expected or
            receipt.get("source") != expected or
            attestation.get("snapshot_fingerprint") !=
            durable.get("snapshot_fingerprint") or
            attestation.get("event_fingerprint") !=
            durable.get("event_fingerprint") or
            receipt.get("snapshot_fingerprint") !=
            durable.get("snapshot_fingerprint")):
        raise OwnedCleanupError(
            "owned cleanup publication envelope did not verify source identity")
    return copy.deepcopy(dict(receipt["source"]))


def publish_canonical_dashboard(
        selected_workspace: str, *, obligation: Mapping[str, object],
        snapshot_publisher: Callable[..., Mapping[str, object]],
        delivery_publisher: Callable[[str, dict], Mapping[str, object]],
        **kwargs) -> dict:
    """Bind injected snapshot/delivery ports to one sealed cleanup source."""
    source_revision = kwargs["source_revision"]
    source_fingerprint = kwargs["source_fingerprint"]
    expected = _publication_source_identity(
        source_revision, source_fingerprint)
    publication = snapshot_publisher(
        selected_workspace,
        **{key: value for key, value in kwargs.items()
           if key not in {"source_revision", "source_fingerprint"}})
    durable = _load_durable_publication(selected_workspace, publication)
    attestation = _publication_source_attestation(obligation, durable)
    delivered = delivery_publisher(selected_workspace, {
        "outcome": obligation["outcome"],
        "dashboard_snapshot": publication,
    })
    reloaded = _load_durable_publication(selected_workspace, publication)
    if (reloaded["snapshot_fingerprint"] != durable["snapshot_fingerprint"] or
            reloaded["event_fingerprint"] != durable["event_fingerprint"]):
        raise OwnedCleanupError(
            "durable dashboard identity changed during delivery")
    delivery = _verify_durable_delivery(delivered, durable=reloaded)
    receipt = _publication_source_receipt(attestation, delivery)
    source_identity = _verify_publication_source_envelope(
        attestation, receipt, expected=expected, durable=reloaded)
    return {
        "source_revision": source_identity["source_revision"],
        "source_fingerprint": source_identity["source_fingerprint"],
        "source_verification": {"attestation": attestation,
                                "receipt": receipt},
        "snapshot_publication": copy.deepcopy(dict(publication)),
        "durable_publication": reloaded,
        "dashboard_delivery": delivered,
    }


def replay_publication(path: str | os.PathLike[str], *, workspace: str,
                       owner: Mapping[str, object], outcome: str,
                       publisher: Callable[..., Mapping[str, object]] | None =
                       None, mark_published: bool = True) -> dict:
    """Replay through the canonical snapshot publisher, never a local copy.

    The obligation remains immutable evidence.  Publication is an idempotent
    side effect keyed by its committed source revision/fingerprint; recovery
    can therefore call this again after the cleanup targets are gone.
    """
    replay_path = Path(path).absolute()
    obligation = _validate_publication_replay(
        replay_path, _closed_owner(dict(owner)), outcome=outcome)
    publisher = publisher or _PUBLICATION_PUBLISHER
    if publisher is None:
        raise OwnedCleanupError(
            "canonical dashboard publisher is not configured")
    published = publisher(
        str(Path(workspace).resolve()),
        event_type="owned_cleanup_" + str(obligation["trigger"]),
        outcome=str(obligation["outcome"]), replay=True,
        source_revision=int(obligation["source_revision"]),
        source_fingerprint=str(obligation["source_fingerprint"]),
        obligation=copy.deepcopy(obligation))
    if (not isinstance(published, Mapping) or
            type(published.get("source_revision")) is not int or
            published.get("source_revision") != obligation["source_revision"] or
            published.get("source_fingerprint") !=
            obligation["source_fingerprint"]):
        raise OwnedCleanupError(
            "canonical dashboard publisher did not verify source identity")
    if mark_published:
        with _manifest_lock(replay_path, suffix=".publication.lock"):
            current = _validate_publication_replay(
                replay_path, owner, outcome=outcome)
            current_material = {
                key: copy.deepcopy(value) for key, value in current.items()
                if key not in {"fingerprint", "publication_fingerprint"}
            }
            current_material["status"] = "published"
            current_material["publication_fingerprint"] = _digest(
                dict(published))
            obligation = {
                **current_material, "fingerprint": _digest(current_material),
            }
            _atomic_json(replay_path, obligation)
    material = {
        "schema": "taskplane.cleanup-publication-replay-result/v1",
        "obligation_fingerprint": obligation["fingerprint"],
        "source_revision": obligation["source_revision"],
        "source_fingerprint": obligation["source_fingerprint"],
        "outcome": obligation["outcome"],
        "publication": copy.deepcopy(dict(published)),
    }
    return {**material, "fingerprint": _digest(material),
            "obligation": copy.deepcopy(obligation)}


def replay_terminal_publication(path: str | os.PathLike[str], *,
                                workspace: str,
                                publisher: Callable[..., Mapping[str, object]] |
                                None = None) -> dict:
    """Replay the sealed obligation for one terminal manifest at startup."""
    manifest = load_manifest(path)
    terminal = _validate_terminal(manifest)
    evidence = next((row for row in terminal["evidence"]
                     if row.get("label") == "publication-replay"), None)
    if not isinstance(evidence, Mapping):
        raise OwnedCleanupError("sealed publication replay is unavailable")
    return replay_publication(
        str(evidence["sealed_path"]), workspace=workspace,
        owner=manifest["owner"], outcome=str(terminal["outcome"]),
        publisher=publisher, mark_published=False)


def seal_terminal(path: str | os.PathLike[str], *, outcome: str,
                  evidence: Mapping[str, str | os.PathLike[str]]) -> dict:
    """Seal original terminal truth and evidence before cleanup can start."""
    if outcome not in _TERMINAL_OUTCOMES:
        raise OwnedCleanupError("cleanup terminal outcome is invalid")
    if not isinstance(evidence, Mapping) or not evidence:
        raise OwnedCleanupError("cleanup requires durable evidence")
    manifest_path = Path(path).absolute()
    terminal_result: dict = {}

    def mutate(manifest: dict) -> None:
        nonlocal terminal_result
        if manifest.get("terminal") is not None:
            terminal_result = copy.deepcopy(manifest["terminal"])
            return
        _assert_evidence_external(manifest)
        required = {"publication-replay"}
        for resource in manifest["resources"].values():
            required.update(_evidence_labels(resource.get("evidence_refs") or []))
        supplied = set(evidence)
        missing = required - supplied
        if missing:
            raise OwnedCleanupError(
                "cleanup evidence references are unresolved: " +
                ", ".join(sorted(missing)))
        evidence_root = Path(str(manifest["evidence_root"])).absolute()
        evidence_root.mkdir(parents=True, exist_ok=True)
        sealed = []
        publication = None
        for label in sorted(_evidence_labels(list(evidence))):
            raw_source = evidence[label]
            source = Path(raw_source).absolute()
            if label == "publication-replay":
                publication = _validate_publication_replay(
                    source, manifest["owner"], outcome=outcome)
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
            sealed_path = Path(row["sealed_path"])
            if (sealed_path.resolve(strict=True).parent !=
                    evidence_root.resolve(strict=True) or
                    sealed_path.stat().st_nlink != 1 or
                    row["source_sha256"] != row["sha256"]):
                raise OwnedCleanupError(
                    "sealed cleanup evidence is not independently external")
            row["label"] = label
            sealed.append(row)
        assert publication is not None
        terminal_material = {
            "outcome": outcome,
            "owner": copy.deepcopy(manifest["owner"]),
            "evidence": sealed,
            "publication_replay": {
                "fingerprint": publication["fingerprint"],
                "status": publication["status"],
                "replay_required": publication["replay_required"],
            },
        }
        terminal_result = {**terminal_material,
                           "terminal_digest": _digest(terminal_material)}
        manifest["terminal"] = copy.deepcopy(terminal_result)
        manifest["journal"].append({
            "event": "terminal-sealed", "outcome": outcome,
            "terminal_digest": terminal_result["terminal_digest"],
        })

    manifest_before = load_manifest(manifest_path)
    if manifest_before.get("terminal") is not None:
        return copy.deepcopy(manifest_before["terminal"])
    _mutate_manifest(manifest_path, mutate)
    return terminal_result


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
        from taskplane.host_native import process_start_identity
    except ImportError:
        from host_native import process_start_identity
    return str(process_start_identity(pid))


def _process_status(binding: Mapping[str, object]) -> str:
    try:
        pid = int(binding["pid"])
        try:
            waited, _status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                return "absent"
        except ChildProcessError:
            pass
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


def _verify_stable_identity(resource: Mapping[str, object], target: str) -> None:
    stable = resource.get("stable_identity")
    if (not isinstance(stable, dict) or not stable or
            resource.get("stable_identity_digest") != _digest(stable)):
        raise OwnedCleanupError("resource stable identity is unverified")
    kind = resource.get("kind")
    observed = resource.get("observed_identity") or {}
    if kind == "process-group":
        binding = observed.get("process") or {}
        binding_matches = (
            stable.get("binding_digest") == _digest(binding)
            if "binding_digest" in stable else
            stable.get("token") == binding.get("token") and
            stable.get("run_id") == resource.get("owner", {}).get("run_id") and
            stable.get("task_id") == resource.get("owner", {}).get("task_id") and
            stable.get("attempt") == resource.get("owner", {}).get("attempt")
        )
        if not binding_matches:
            raise OwnedCleanupError("process stable identity changed")
    elif kind == "worker-contract":
        try:
            snapshot = json.loads(
                (Path(target) / "snapshot.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OwnedCleanupError(
                "worker contract stable identity is unavailable") from exc
        identity = snapshot.get("identity") or {}
        live = {
            "handle": snapshot.get("handle"),
            "run_id": identity.get("run_id"),
            "task_id": identity.get("task_id"),
            "workspace_fingerprint": snapshot.get("workspace_fingerprint"),
            "authorization_fingerprint": snapshot.get(
                "authorization_fingerprint"),
            "command_fingerprint": snapshot.get("command_fingerprint"),
            "binding_digest": snapshot.get("binding_digest"),
            "created_at": snapshot.get("created_at"),
            **({"attempt": identity.get("attempt")}
               if "attempt" in stable else {}),
        }
        if stable != live:
            raise OwnedCleanupError("worker contract stable identity changed")
    elif kind == "worktree":
        try:
            import worktree_cleanup
        except ImportError:
            from taskplane import worktree_cleanup
        policy = resource.get("policy") or {}
        try:
            live = worktree_cleanup.resource_identity(
                policy.get("merge_receipt") or {},
                lifecycle=policy.get("lifecycle") or {})
        except (ValueError, worktree_cleanup.CleanupError) as exc:
            raise OwnedCleanupError(
                "worktree stable identity is unavailable") from exc
        if stable != live or live.get("registration_path") != target:
            raise OwnedCleanupError("worktree stable identity changed")
    elif kind == "generated-state" and {
            "token", "run_id", "task_id", "attempt", "handle",
    }.issubset(stable):
        try:
            live_value = json.loads(Path(target).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OwnedCleanupError(
                "generated-state stable identity is unavailable") from exc
        identity = live_value.get("identity") or {}
        live = {
            "token": Path(target).stem,
            "run_id": identity.get("run_id"),
            "task_id": identity.get("task_id"),
            "attempt": identity.get("attempt"),
            "handle": live_value.get("handle"),
        }
        if stable != live:
            raise OwnedCleanupError("generated-state stable identity changed")
    elif kind == "cache":
        marker = Path(target) / ".taskplane-owned-cache-identity.json"
        try:
            live = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OwnedCleanupError(
                "cache producer/version/input identity is unavailable") from exc
        material = {"schema": "taskplane.owned-cache-live-identity/v1",
                    **stable}
        expected = {**material, "fingerprint": _digest(material)}
        if live != expected:
            raise OwnedCleanupError(
                "cache producer/version/input identity changed")
    elif kind in {"generated-state", "test-artifact"}:
        identity_keys = {"producer", "version"}
        if not identity_keys.issubset(stable) or not any(
                key in stable for key in ("input", "input_digest")) or any(
                not str(stable.get(key) or "").strip()
                for key in identity_keys):
            raise OwnedCleanupError(
                "filesystem producer/version/input identity is incomplete")
        if "sha256" in stable and (
                not Path(target).is_file() or
                stable["sha256"] != file_sha256(target)):
            raise OwnedCleanupError("filesystem stable identity changed")


def _assert_activation_binding(resource: Mapping[str, object],
                               journal: Sequence[Mapping[str, object]]) -> None:
    resource_id = resource.get("resource_id")
    activation = next((event for event in reversed(journal)
                       if event.get("event") == "activated" and
                       event.get("resource_id") == resource_id), None)
    if (not isinstance(activation, Mapping) or
            activation.get("stable_identity_digest") !=
            resource.get("stable_identity_digest") or
            activation.get("identity_digest") !=
            _digest(resource.get("observed_identity"))):
        raise OwnedCleanupError("resource activation identity is unverified")


def _precheck(resource: Mapping[str, object], owner: Mapping[str, object],
              duplicate_targets: set[str], *,
              journal: Sequence[Mapping[str, object]] = ()) \
        -> tuple[bool, str, bool]:
    try:
        if _closed_owner(resource.get("owner")) != dict(owner):
            raise OwnedCleanupError("resource has a foreign owner")
        if resource.get("kind") not in _RESOURCE_KINDS:
            raise OwnedCleanupError("resource kind is invalid")
        if resource.get("state") == "absent":
            target = _target(resource)
            if os.path.lexists(target):
                raise OwnedCleanupError("abandoned resource target appeared")
            return True, "reserved target proven never created", False
        if resource.get("state") != "active":
            raise OwnedCleanupError("resource is not activated")
        _assert_activation_binding(resource, journal)
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
        current_identity = _path_identity(
            target,
            include_directory_content=resource.get("kind") in {
                "cache", "generated-state", "test-artifact"},
        )
        if resource.get("kind") == "worker-contract" and \
                current_identity.get("type") == "directory":
            # Runtime delivery/artifact subdirectories legitimately change a
            # directory's link count. Generation, containment, mode and the
            # exact live snapshot identity below remain immutable authority.
            comparable = {
                key: value for key, value in current_identity.items()
                if key != "links"
            }
            observed_comparable = {
                key: value for key, value in (path_observed or {}).items()
                if key != "links"
            }
        else:
            comparable = current_identity
            observed_comparable = path_observed
        if comparable != observed_comparable:
            raise OwnedCleanupError("resource identity changed or is dirty")
        _verify_stable_identity(resource, target)
        current_path_identity = current_identity
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
    if resource.get("state") == "absent":
        if os.path.lexists(_target(resource)):
            raise OwnedCleanupError("abandoned resource target appeared")
        return "never-created"
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
    row = copy.deepcopy(dict(event))
    manifest_path = Path(path).absolute()

    def mutate(manifest: dict) -> None:
        if row.get("event") not in {
                "action-started", "action-cleaned", "action-refused"} or \
                row.get("resource_id") not in manifest["resources"]:
            raise OwnedCleanupError("cleanup journal event is invalid")
        manifest["journal"].append(row)

    committed, _ = _mutate_manifest(manifest_path, mutate)
    return committed


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
    if resource.get("kind") != "process-group" or \
            resource.get("state") == "absent":
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


def _validate_terminal(manifest: Mapping[str, object]) -> dict:
    terminal = manifest.get("terminal")
    if (not isinstance(terminal, dict) or
            terminal.get("terminal_digest") != _digest({
                key: copy.deepcopy(value) for key, value in terminal.items()
                if key != "terminal_digest"
            })):
        raise OwnedCleanupError("original terminal outcome is not sealed")
    labels = set()
    for row in terminal.get("evidence") or []:
        if not isinstance(row, dict):
            raise OwnedCleanupError("sealed cleanup evidence is invalid")
        sealed = Path(str(row.get("sealed_path") or ""))
        try:
            valid = (sealed.is_file() and not sealed.is_symlink() and
                     sealed.stat().st_nlink == 1 and
                     sealed.stat().st_size == row.get("bytes") and
                     file_sha256(sealed) == row.get("sha256") ==
                     row.get("source_sha256") and
                     sealed.resolve(strict=True).parent ==
                     Path(str(manifest["evidence_root"])).resolve(strict=True))
        except OSError:
            valid = False
        if not valid:
            raise OwnedCleanupError("sealed cleanup evidence is invalid")
        labels.add(row.get("label"))
    required = {"publication-replay"}
    for resource in (manifest.get("resources") or {}).values():
        required.update(_evidence_labels(resource.get("evidence_refs") or []))
    if not required.issubset(labels):
        raise OwnedCleanupError("sealed cleanup evidence references are incomplete")
    replay = terminal.get("publication_replay") or {}
    if (replay.get("replay_required") is not True or
            replay.get("status") not in {"pending", "published"} or
            not isinstance(replay.get("fingerprint"), str)):
        raise OwnedCleanupError("publication replay obligation is unsealed")
    return copy.deepcopy(terminal)


def _validate_receipt_binding(receipt: Mapping[str, object],
                              manifest: Mapping[str, object]) -> None:
    terminal = _validate_terminal(manifest)
    if (receipt.get("manifest_digest") != manifest.get("manifest_digest") or
            receipt.get("manifest_revision") != manifest.get("revision") or
            receipt.get("terminal_digest") != terminal.get("terminal_digest") or
            receipt.get("owner") != manifest.get("owner") or
            receipt.get("replay_key") != _digest({
                "manifest_digest": manifest.get("manifest_digest"),
                "manifest_revision": manifest.get("revision"),
                "terminal_digest": terminal.get("terminal_digest"),
            })):
        raise OwnedCleanupError(
            "cleanup receipt is stale or bound to another manifest revision")


def load_completed_cleanup(path: str | os.PathLike[str]) -> dict:
    """Read and validate one completed cleanup without replaying its actions."""
    manifest_path = Path(path).absolute()
    manifest = load_manifest(manifest_path)
    receipt = _load_receipt(_receipt_path(manifest_path))
    if receipt is None:
        raise OwnedCleanupError("completed cleanup receipt is unavailable")
    _validate_receipt_binding(receipt, manifest)
    terminal = _validate_terminal(manifest)
    return {
        "manifest": copy.deepcopy(manifest),
        "terminal": copy.deepcopy(terminal),
        "receipt": copy.deepcopy(receipt),
    }


def cleanup_consumer_evidence(receipt: Mapping[str, object]) -> dict:
    """Expose a closed, redacted proof for metrics/sign-off/release adapters."""
    value = copy.deepcopy(dict(receipt))
    if (value.get("schema") != RECEIPT_SCHEMA or
            value.get("receipt_digest") != receipt_digest(value) or
            value.get("cleanup_status") not in {"clean", "attention"} or
            isinstance(value.get("leak_count"), bool) or
            not isinstance(value.get("leak_count"), int) or
            value["leak_count"] < 0 or
            value["leak_count"] != len(value.get("leaks") or [])):
        raise OwnedCleanupError("cleanup consumer receipt is invalid")
    material = {
        "schema": CLEANUP_EVIDENCE_SCHEMA,
        "receipt_digest": value["receipt_digest"],
        "manifest_digest": value["manifest_digest"],
        "manifest_revision": value["manifest_revision"],
        "terminal_digest": value["terminal_digest"],
        "owner": copy.deepcopy(value["owner"]),
        "original_outcome": value["original_outcome"],
        "cleanup_status": value["cleanup_status"],
        "leak_count": value["leak_count"],
        "leaks_digest": _digest(value.get("leaks") or []),
        "resource_results_digest": _digest(value.get("resources") or []),
    }
    return {**material, "evidence_digest": _digest(material)}


def cleanup_manifest(path: str | os.PathLike[str]) -> dict:
    """Serialize concurrent callbacks and replay only an exactly bound receipt."""
    manifest_path = Path(path).absolute()
    with _manifest_lock(manifest_path, suffix=".cleanup.lock"):
        return _cleanup_manifest_locked(manifest_path)


def _cleanup_manifest_locked(path: str | os.PathLike[str]) -> dict:
    """Revalidate, clean in reverse dependencies, and prove zero leaks."""
    manifest_path = Path(path).absolute()
    manifest = load_manifest(manifest_path)
    prior = _load_receipt(_receipt_path(manifest_path))
    if prior is not None:
        _validate_receipt_binding(prior, manifest)
        return copy.deepcopy(prior)
    terminal = _validate_terminal(manifest)
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
                resource, manifest["owner"], duplicates,
                journal=manifest["journal"])
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
            current_manifest = load_manifest(manifest_path)
            eligible, reason, _ = _precheck(
                resource, manifest["owner"], set(),
                journal=current_manifest["journal"])
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
        if resource.get("kind") == "process-group" and \
                resource.get("state") != "absent":
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
        "manifest_revision": manifest["revision"],
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
            "manifest_revision": manifest["revision"],
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
    seal_terminal(manifest_path, outcome=outcome, evidence=evidence)
    return cleanup_manifest(manifest_path)


def _rewrite_for_test(path: str | os.PathLike[str],
                      mutate: Callable[[dict], None]) -> None:
    """Test seam for constructing validly encoded hostile manifests."""
    manifest_path = Path(path).absolute()
    _mutate_manifest(manifest_path, lambda manifest: mutate(manifest))
