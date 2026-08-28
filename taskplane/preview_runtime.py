"""Governed, host-neutral lifecycle for interactive working previews.

This module is an authority for *whether* a preview may exist and for its
audit trail. Host adapters remain responsible for rendering a browser or side
panel and :mod:`taskplane.command_adapters` remains responsible for isolated
process launch. A preview never grants either layer authority over workflow
state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import threading
import time
from typing import Callable, Mapping, Protocol, Sequence


SCHEMA = "taskplane.host-preview/v1"
AUDIT_SCHEMA = "taskplane.host-preview-audit/v1"
VALID_FLOWS = frozenset({"design", "build", "dynamic_review"})
TERMINAL_OUTCOMES = frozenset({
    "build_failed", "timed_out", "attempted_push", "escaped_path",
    "external_network", "public_exposure", "teardown_failed", "denied",
    "unavailable",
})
MAX_LIFETIME_SECONDS = 3600
MAX_CPU_SECONDS = 1800
MAX_MEMORY_BYTES = 4 * 1024 * 1024 * 1024
MAX_STARTUP_ENTRIES = 100_000
MAX_STARTUP_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_STARTUP_FILE_BYTES = 512 * 1024 * 1024
MAX_STARTUP_SECONDS = 30
HASH_CHUNK_BYTES = 1024 * 1024
EXCLUDED_PREVIEW_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", ".tox", ".venv", "__pycache__",
    "node_modules",
})


class PreviewError(RuntimeError):
    """Base preview lifecycle error."""


class PreviewDenied(PreviewError):
    def __init__(self, outcome: str, message: str):
        super().__init__(message)
        self.outcome = outcome


class SurfaceTransport(Protocol):
    """Trusted host bridge for an integrated browser/side-panel surface."""

    def __call__(self, surface: str, sandbox: str,
                 preview: Mapping[str, object]) -> Mapping[str, object]: ...


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class _StartupBoundExceeded(PreviewError):
    """A source inventory or materialization crossed a startup budget."""


def _startup_limits(limits: Mapping) -> dict[str, int]:
    names = {
        "startup_entries": MAX_STARTUP_ENTRIES,
        "startup_total_bytes": MAX_STARTUP_TOTAL_BYTES,
        "startup_file_bytes": MAX_STARTUP_FILE_BYTES,
        "startup_seconds": MAX_STARTUP_SECONDS,
    }
    if any(isinstance(limits.get(name, maximum), bool)
           for name, maximum in names.items()):
        raise _StartupBoundExceeded(
            "preview startup limits are invalid")
    try:
        bounded = {name: int(limits.get(name, maximum))
                   for name, maximum in names.items()}
    except (TypeError, ValueError) as exc:
        raise _StartupBoundExceeded(
            "preview startup limits are invalid") from exc
    if any(value <= 0 or value > names[name]
           for name, value in bounded.items()):
        raise _StartupBoundExceeded(
            "preview startup limits exceed policy")
    if bounded["startup_file_bytes"] > bounded["startup_total_bytes"]:
        raise _StartupBoundExceeded(
            "preview per-file limit exceeds total-byte limit")
    return bounded


def _deadline_check(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise _StartupBoundExceeded("preview startup time limit exceeded")


def _entry_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(metadata.st_dev), int(metadata.st_ino), int(metadata.st_mode),
        int(metadata.st_size), int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def _root_descriptor(root: Path) -> int:
    """Open one directory identity without following a replaced root link."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY") or \
            os.open not in getattr(os, "supports_dir_fd", set()):
        raise _StartupBoundExceeded(
            "preview descriptor confinement is unavailable on this host")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | \
        getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        before = root.lstat()
        descriptor = os.open(root, flags)
        after = os.fstat(descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise _StartupBoundExceeded(
            "preview source root is unavailable") from exc
    if not stat.S_ISDIR(after.st_mode) or \
            _entry_identity(before) != _entry_identity(after):
        os.close(descriptor)
        raise _StartupBoundExceeded(
            "preview source root changed during descriptor binding")
    return descriptor


def _open_relative(root_descriptor: int, relative: Path, *,
                   directory: bool,
                   identities: Mapping[str, tuple[int, int, int, int, int, int]] |
                   None = None) -> int:
    """Open a confined relative entry one no-follow path component at a time."""
    parts = relative.parts
    if relative == Path(".") or not parts:
        return os.dup(root_descriptor)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise _StartupBoundExceeded("preview manifest path is not confined")
    current = os.dup(root_descriptor)
    try:
        traversed: list[str] = []
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            if not last or directory:
                flags |= getattr(os, "O_DIRECTORY", 0)
            next_descriptor = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = next_descriptor
            traversed.append(part)
            if identities is not None:
                expected = identities.get(Path(*traversed).as_posix())
                if expected is None or \
                        _entry_identity(os.fstat(current)) != expected:
                    raise _StartupBoundExceeded(
                        "preview manifest ancestor identity changed")
        metadata = os.fstat(current)
        expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected_kind(metadata.st_mode):
            raise _StartupBoundExceeded(
                "preview manifest entry changed type")
        return current
    except _StartupBoundExceeded:
        os.close(current)
        raise
    except OSError as exc:
        os.close(current)
        raise _StartupBoundExceeded(
            "preview manifest entry is unavailable") from exc


def _stream_digest(descriptor: int, *, deadline: float,
                   byte_limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            _deadline_check(deadline)
            chunk = os.read(descriptor, HASH_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > byte_limit:
                raise _StartupBoundExceeded(
                    "preview file exceeds byte limit")
            digest.update(chunk)
    except OSError as exc:
        raise _StartupBoundExceeded(
            "preview source file is unreadable") from exc
    return digest.hexdigest(), total


def _bounded_manifest(root: Path, *, limits: Mapping[str, int],
                      exclude_generated: bool,
                      deadline: float | None = None) -> \
        tuple[list[dict], str, dict[str, tuple[int, int, int, int, int, int]]]:
    """Return a bounded snapshot plus descriptor-bound source identities."""
    deadline = (time.monotonic() + limits["startup_seconds"]
                if deadline is None else deadline)
    pending = [Path(".")]
    rows: list[dict] = []
    identities: dict[str, tuple[int, int, int, int, int, int]] = {}
    total_bytes = 0
    observed_entries = 0
    root_descriptor = _root_descriptor(root)
    try:
        identities["."] = _entry_identity(os.fstat(root_descriptor))
        while pending:
            _deadline_check(deadline)
            relative_parent = pending.pop()
            parent_descriptor = _open_relative(
                root_descriptor, relative_parent, directory=True,
                identities=identities)
            try:
                if _entry_identity(os.fstat(parent_descriptor)) != \
                        identities[relative_parent.as_posix()]:
                    raise _StartupBoundExceeded(
                        "preview source directory changed during inventory")
                try:
                    entries = []
                    with os.scandir(parent_descriptor) as iterator:
                        for entry in iterator:
                            _deadline_check(deadline)
                            if exclude_generated and entry.name in \
                                    EXCLUDED_PREVIEW_DIRECTORIES:
                                continue
                            observed_entries += 1
                            if observed_entries > limits["startup_entries"]:
                                raise _StartupBoundExceeded(
                                    "preview source exceeds entry limit")
                            entries.append(entry)
                    entries.sort(key=lambda value: value.name)
                except OSError as exc:
                    raise _StartupBoundExceeded(
                        "preview source inventory is unreadable") from exc
                for entry in entries:
                    _deadline_check(deadline)
                    relative = (relative_parent / entry.name
                                if relative_parent != Path(".")
                                else Path(entry.name))
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise _StartupBoundExceeded(
                            "preview source entry is unreadable: "
                            f"{relative.as_posix()}") from exc
                    if stat.S_ISLNK(metadata.st_mode):
                        raise _StartupBoundExceeded(
                            "preview source contains a symlink: "
                            f"{relative.as_posix()}")
                    identity = _entry_identity(metadata)
                    if stat.S_ISDIR(metadata.st_mode):
                        descriptor = os.open(
                            entry.name,
                            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                            getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=parent_descriptor)
                        try:
                            if _entry_identity(os.fstat(descriptor)) != identity:
                                raise _StartupBoundExceeded(
                                    "preview source directory changed during "
                                    "inventory")
                        finally:
                            os.close(descriptor)
                        row = {"path": relative.as_posix(),
                               "kind": "directory"}
                        pending.append(relative)
                    elif stat.S_ISREG(metadata.st_mode):
                        if metadata.st_size > limits["startup_file_bytes"]:
                            raise _StartupBoundExceeded(
                                "preview file exceeds byte limit: "
                                f"{relative.as_posix()}")
                        descriptor = os.open(
                            entry.name,
                            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=parent_descriptor)
                        try:
                            if _entry_identity(os.fstat(descriptor)) != identity:
                                raise _StartupBoundExceeded(
                                    "preview source changed during inventory")
                            file_digest, observed_size = _stream_digest(
                                descriptor, deadline=deadline,
                                byte_limit=limits["startup_file_bytes"])
                            if _entry_identity(os.fstat(descriptor)) != identity:
                                raise _StartupBoundExceeded(
                                    "preview source changed during inventory")
                        finally:
                            os.close(descriptor)
                        if observed_size != metadata.st_size:
                            raise _StartupBoundExceeded(
                                "preview source changed during inventory: "
                                f"{relative.as_posix()}")
                        total_bytes += observed_size
                        if total_bytes > limits["startup_total_bytes"]:
                            raise _StartupBoundExceeded(
                                "preview source exceeds total-byte limit")
                        row = {"path": relative.as_posix(), "kind": "file",
                               "bytes": observed_size, "sha256": file_digest}
                    else:
                        raise _StartupBoundExceeded(
                            "preview source contains a special entry: "
                            f"{relative.as_posix()}")
                    identities[relative.as_posix()] = identity
                    rows.append(row)
            except OSError as exc:
                raise _StartupBoundExceeded(
                    "preview source changed during inventory") from exc
            finally:
                os.close(parent_descriptor)
    finally:
        os.close(root_descriptor)
    rows.sort(key=lambda row: str(row["path"]))
    return rows, _digest(rows), identities


def _materialize_manifest(source: Path, sandbox: Path, rows: Sequence[Mapping],
                          identities: Mapping[str, tuple[int, int, int, int, int, int]],
                          *, limits: Mapping[str, int], deadline: float) -> None:
    copied_bytes = 0
    root_descriptor = _root_descriptor(source)
    try:
        if _entry_identity(os.fstat(root_descriptor)) != identities.get("."):
            raise _StartupBoundExceeded(
                "preview source root changed before materialization")
        for row in rows:
            _deadline_check(deadline)
            relative = Path(str(row["path"]))
            destination = sandbox / relative
            expected_identity = identities.get(relative.as_posix())
            if expected_identity is None:
                raise _StartupBoundExceeded(
                    "preview manifest identity is incomplete")
            descriptor = _open_relative(
                root_descriptor, relative,
                directory=row["kind"] == "directory",
                identities=identities)
            try:
                if _entry_identity(os.fstat(descriptor)) != expected_identity:
                    raise _StartupBoundExceeded(
                        "preview source identity changed before materialization: "
                        f"{relative.as_posix()}")
                if row["kind"] == "directory":
                    destination.mkdir(parents=True, exist_ok=False)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                observed = 0
                destination_descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                    getattr(os, "O_NOFOLLOW", 0), 0o600)
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    while True:
                        _deadline_check(deadline)
                        chunk = os.read(descriptor, HASH_CHUNK_BYTES)
                        if not chunk:
                            break
                        observed += len(chunk)
                        copied_bytes += len(chunk)
                        if observed > limits["startup_file_bytes"] or \
                                copied_bytes > limits["startup_total_bytes"]:
                            raise _StartupBoundExceeded(
                                "preview materialization exceeds byte limit")
                        view = memoryview(chunk)
                        while view:
                            written = os.write(destination_descriptor, view)
                            if written <= 0:
                                raise OSError("short preview write")
                            view = view[written:]
                        digest.update(chunk)
                    os.fchmod(
                        destination_descriptor,
                        stat.S_IMODE(expected_identity[2]) & 0o777)
                finally:
                    os.close(destination_descriptor)
                if _entry_identity(os.fstat(descriptor)) != expected_identity:
                    raise _StartupBoundExceeded(
                        "preview source changed during materialization: "
                        f"{relative.as_posix()}")
                if observed != row.get("bytes") or \
                        digest.hexdigest() != row.get("sha256"):
                    raise _StartupBoundExceeded(
                        "preview source changed during materialization: "
                        f"{relative.as_posix()}")
            finally:
                os.close(descriptor)
    except OSError as exc:
        raise _StartupBoundExceeded(
            "preview materialization failed") from exc
    finally:
        os.close(root_descriptor)


def _path_fingerprint(root: Path, *, limits: Mapping[str, int],
                      exclude_generated: bool = False) -> str:
    return _bounded_manifest(
        root, limits=limits, exclude_generated=exclude_generated)[1]


def _bounded_remove_tree(path: Path, *, deadline: float) -> None:
    """Remove one owned preview scope without following links or overrunning."""
    if not os.path.lexists(path):
        return
    _deadline_check(deadline)
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            path.unlink()
            return
        with os.scandir(path) as iterator:
            names = sorted(entry.name for entry in iterator)
        for name in names:
            _deadline_check(deadline)
            child = path / name
            child_metadata = child.lstat()
            if stat.S_ISDIR(child_metadata.st_mode) and \
                    not stat.S_ISLNK(child_metadata.st_mode):
                _bounded_remove_tree(child, deadline=deadline)
            else:
                child.unlink()
        path.rmdir()
    except (OSError, _StartupBoundExceeded) as exc:
        raise _StartupBoundExceeded(
            "preview cleanup was not completed within the startup budget") from exc


def _saved_startup_limits(preview: Mapping[str, object]) -> dict[str, int] | None:
    value = preview.get("startup_limits")
    if not isinstance(value, Mapping) or set(value) != {
            "startup_entries", "startup_total_bytes", "startup_file_bytes",
            "startup_seconds"}:
        return None
    try:
        return _startup_limits(value)
    except _StartupBoundExceeded:
        return None


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PreviewRuntime:
    """Register private, pinned, bounded previews and seal their evidence."""

    def __init__(self, root: str | Path, *, workspace: str | Path,
                 authorization: str, clock: Callable[[], float] | None = None,
                 surface_transport: SurfaceTransport | None = None,
                 process_teardown: Callable[[str, object], bool] | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("preview workspace must be a directory")
        workspace_descriptor = _root_descriptor(self.workspace)
        try:
            self._workspace_identity = _entry_identity(
                os.fstat(workspace_descriptor))
        finally:
            os.close(workspace_descriptor)
        self._authorization = _digest(str(authorization))
        self._clock = clock or time.time
        self._surface_transport = surface_transport
        self._process_teardown = process_teardown
        self._audit_path = self.root / "audit.json"

    def _audit(self, *, preview_id: str | None, outcome: str,
               detail: str, target: str | None = None,
               revision: int | None = None) -> dict:
        row = {
            "schema": AUDIT_SCHEMA, "preview_id": preview_id,
            "outcome": outcome, "detail": str(detail)[:512],
            "target": target, "revision": revision,
            "at": float(self._clock()),
        }
        rows = self.audit()
        rows.append(row)
        _atomic_json(self._audit_path, rows)
        return row

    def audit(self) -> list[dict]:
        try:
            value = json.loads(self._audit_path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, ValueError):
            return []

    def _deny(self, outcome: str, detail: str, *, target: object = None,
              revision: object = None) -> None:
        self._audit(preview_id=None, outcome=outcome, detail=detail,
                    target=str(target) if target else None,
                    revision=int(revision) if isinstance(revision, int) else None)
        raise PreviewDenied(outcome, detail)

    def _path(self, preview_id: str) -> Path:
        if not isinstance(preview_id, str) or len(preview_id) != 32 or any(
                char not in "0123456789abcdef" for char in preview_id):
            raise PreviewError("preview identity is invalid")
        return self.root / "previews" / preview_id / "snapshot.json"

    def _load(self, preview_id: str) -> dict:
        try:
            value = json.loads(self._path(preview_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PreviewError("preview is unavailable") from exc
        if value.get("schema") != SCHEMA:
            raise PreviewError("preview schema is unsupported")
        return value

    def sandbox_path(self, preview_id: str) -> Path:
        """Return the registered disposable cwd to trusted launch adapters."""
        preview = self._load(preview_id)
        path = self._path(preview_id).parent / "sandbox"
        fingerprint = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
        if fingerprint != preview.get("sandbox_id") or not path.is_dir():
            raise PreviewError("registered preview sandbox is unavailable")
        return path

    def _save(self, preview: dict) -> dict:
        _atomic_json(self._path(preview["preview_id"]), preview)
        return json.loads(json.dumps(preview))

    @staticmethod
    def _supported(capabilities: Mapping, name: str) -> bool:
        row = capabilities.get(name)
        return (isinstance(row, Mapping) and row.get("status") == "supported"
                and bool(str(row.get("source") or "").strip()))

    def register(self, *, flow: str, target: str, revision: int,
                 source_root: str | Path, authorization: str,
                 capabilities: Mapping, limits: Mapping,
                 network_allowlist: list[str] | tuple[str, ...],
                 visibility: str = "private") -> dict:
        if flow not in VALID_FLOWS:
            self._deny("denied", "preview flow is not authorized", target=target,
                       revision=revision)
        if _digest(str(authorization)) != self._authorization:
            self._deny("denied", "preview authorization does not match session",
                       target=target, revision=revision)
        if not str(target).strip() or not isinstance(revision, int) or revision < 0:
            self._deny("denied", "preview target pin is invalid", target=target,
                       revision=revision)
        source = Path(source_root).resolve()
        if source != self.workspace:
            self._deny("escaped_path", "preview source escapes registered workspace",
                       target=target, revision=revision)
        if visibility != "private":
            self._deny("public_exposure", "preview must be private by default",
                       target=target, revision=revision)
        if network_allowlist:
            self._deny("external_network", "external network is denied by policy",
                       target=target, revision=revision)
        if not self._supported(capabilities, "sandbox"):
            self._deny("unavailable", "native sandbox capability is unavailable",
                       target=target, revision=revision)
        surfaces = [name for name in ("side_panel", "browser", "hosting")
                    if self._supported(capabilities, name)]
        if not surfaces:
            self._deny("unavailable", "browser, side panel, and hosting unavailable",
                       target=target, revision=revision)
        try:
            bounded = {
                "lifetime_seconds": int(limits["lifetime_seconds"]),
                "cpu_seconds": int(limits["cpu_seconds"]),
                "memory_bytes": int(limits["memory_bytes"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            self._deny("denied", "preview resource limits are incomplete",
                       target=target, revision=revision)
            raise AssertionError from exc
        maxima = (MAX_LIFETIME_SECONDS, MAX_CPU_SECONDS, MAX_MEMORY_BYTES)
        if any(value <= 0 or value > maximum
               for value, maximum in zip(bounded.values(), maxima)):
            self._deny("denied", "preview resource limits exceed policy",
                       target=target, revision=revision)
        try:
            startup_limits = _startup_limits(limits)
        except _StartupBoundExceeded as exc:
            self._deny("unavailable", str(exc), target=target,
                       revision=revision)

        startup_deadline = time.monotonic() + \
            startup_limits["startup_seconds"]
        # Inventory creates no state.  Once an owned preview scope exists,
        # preparation stops early enough to leave a bounded cleanup reserve
        # inside this same aggregate deadline.
        cleanup_reserve = min(
            1.0, max(0.01, startup_limits["startup_seconds"] / 10.0))
        preparation_deadline = startup_deadline - cleanup_reserve
        preview_id = secrets.token_hex(16)
        preview_scope = self._path(preview_id).parent
        sandbox = preview_scope / "sandbox"
        try:
            source_manifest, source_fingerprint, source_identities = \
                _bounded_manifest(
                source, limits=startup_limits, exclude_generated=True,
                deadline=preparation_deadline)
            if source_identities.get(".") != self._workspace_identity:
                raise _StartupBoundExceeded(
                    "preview source no longer matches registered workspace")
            _deadline_check(preparation_deadline)
            sandbox.mkdir(parents=True, exist_ok=False)
            _materialize_manifest(
                source, sandbox, source_manifest, source_identities,
                limits=startup_limits, deadline=preparation_deadline)
            materialized_manifest, materialized_fingerprint, _ = \
                _bounded_manifest(
                sandbox, limits=startup_limits, exclude_generated=False,
                deadline=preparation_deadline)
            if materialized_manifest != source_manifest or \
                    materialized_fingerprint != source_fingerprint:
                raise _StartupBoundExceeded(
                    "pinned source materialization differs from inventory")
        except Exception as exc:
            detail = str(exc) if isinstance(exc, _StartupBoundExceeded) else \
                f"preview preparation failed: {exc.__class__.__name__}: {exc}"
            try:
                _bounded_remove_tree(preview_scope, deadline=startup_deadline)
                if os.path.lexists(preview_scope):
                    raise _StartupBoundExceeded(
                        "preview cleanup left an owned startup scope")
            except _StartupBoundExceeded as cleanup_exc:
                detail = f"{detail}; {cleanup_exc}"
            self._deny("unavailable", detail, target=target,
                       revision=revision)
        now = float(self._clock())
        preview = {
            "schema": SCHEMA, "preview_id": preview_id, "flow": flow,
            "target": str(target), "revision": revision,
            "state": "registered", "outcome": "registered",
            "surface": surfaces[0], "surface_fallbacks": surfaces[1:],
            "source_fingerprint": source_fingerprint,
            "source_content_fingerprint": source_fingerprint,
            "materialized_fingerprint": materialized_fingerprint,
            "source_root_fingerprint": _digest(str(source)),
            "authorization_fingerprint": self._authorization,
            "visibility": "private", "push_disabled": True,
            "network": {"mode": "deny", "allowlist": []},
            "limits": bounded, "startup_limits": startup_limits,
            "startup_inventory": {
                "entries": len(source_manifest),
                "regular_file_bytes": sum(
                    int(row.get("bytes") or 0) for row in source_manifest),
            },
            "registered_at": now,
            "deadline": now + bounded["lifetime_seconds"],
            # Path stays private; adapters prove their cwd by this digest.
            "sandbox_id": hashlib.sha256(
                str(sandbox).encode("utf-8")).hexdigest(),
            "events": [], "teardown": {"attempted": False},
        }
        self._audit(preview_id=preview_id, outcome="registered",
                    detail=f"{flow} preview registered on {surfaces[0]}",
                    target=str(target), revision=revision)
        return self._save(preview)


    def open(self, preview_id: str) -> dict:
        preview = self._load(preview_id)
        if preview["state"] != "registered":
            raise PreviewError("only a registered preview can open")
        if float(self._clock()) > preview["deadline"]:
            return self.record_outcome(preview_id, "timed_out")
        if self._surface_transport is None:
            return self.record_outcome(preview_id, "unavailable")
        sandbox = self.sandbox_path(preview_id)
        startup_limits = _saved_startup_limits(preview)
        if startup_limits is None:
            return self.record_outcome(preview_id, "unavailable")
        try:
            sandbox_fingerprint = _path_fingerprint(
                sandbox, limits=startup_limits)
        except _StartupBoundExceeded:
            return self.record_outcome(preview_id, "unavailable")
        if sandbox_fingerprint != preview["materialized_fingerprint"]:
            return self.record_outcome(preview_id, "escaped_path")
        try:
            result = dict(self._surface_transport(
                preview["surface"], str(sandbox), dict(preview)))
        except Exception as exc:
            self._audit(preview_id=preview_id, outcome="unavailable",
                        detail=f"surface transport failed: {exc}",
                        target=preview["target"], revision=preview["revision"])
            return self.record_outcome(preview_id, "unavailable")
        if (result.get("schema") != "taskplane.host-preview-surface/v1" or
                result.get("surface") != preview["surface"] or
                not str(result.get("binding") or "").strip()):
            return self.record_outcome(preview_id, "unavailable")
        preview["state"] = "open"
        preview["outcome"] = "open"
        preview["surface_binding_fingerprint"] = _digest(result)
        surface_ownership = result.get("process_ownership")
        if self._process_teardown is not None and not isinstance(
                surface_ownership, Mapping):
            return self.record_outcome(preview_id, "unavailable")
        if isinstance(surface_ownership, Mapping):
            preview.setdefault("process_ownership", []).append(
                dict(surface_ownership))
        preview["events"].append({"kind": "opened", "at": self._clock(),
                                  "surface": preview["surface"],
                                  "transport": _digest(result)})
        self._audit(preview_id=preview_id, outcome="open", detail="preview opened",
                    target=preview["target"], revision=preview["revision"])
        return self._save(preview)

    def observe(self, preview_id: str, *, interaction: str, result: str) -> dict:
        preview = self._load(preview_id)
        if preview["state"] != "open":
            raise PreviewError("preview observation requires an open preview")
        evidence = {
            "schema": "taskplane.host-preview-evidence/v1",
            "preview_id": preview_id, "target": preview["target"],
            "revision": preview["revision"],
            "interaction": str(interaction)[:256], "result": str(result)[:1024],
            "at": float(self._clock()),
        }
        evidence["fingerprint"] = _digest(evidence)
        preview["events"].append(evidence)
        self._save(preview)
        return evidence

    def bind_command(self, preview_id: str, *, handle: str,
                     binding_digest: str, process_ownership: Mapping) -> dict:
        """Durably bind the isolated command lifecycle to this preview."""
        preview = self._load(preview_id)
        if (preview["state"] != "registered" or not handle or
                not binding_digest or process_ownership.get("schema") !=
                "taskplane.preview-process-ownership/v1"):
            raise PreviewError("preview command lifecycle binding is invalid")
        preview["command_lifecycle"] = {
            "handle_fingerprint": _digest(handle),
            "process_group_binding": str(binding_digest),
            "bound_at": float(self._clock()),
        }
        preview.setdefault("process_ownership", []).append(
            dict(process_ownership))
        return self._save(preview)

    def record_stage(self, preview_id: str, *, stage: str, outcome: str,
                     detail: str = "") -> dict:
        """Seal a bounded operational receipt without changing workflow truth."""
        if stage not in {"build", "repair", "network", "interaction"}:
            raise PreviewError("preview stage is unsupported")
        if outcome not in {"started", "succeeded", "failed", "denied"}:
            raise PreviewError("preview stage outcome is unsupported")
        preview = self._load(preview_id)
        if preview["state"] not in {"registered", "open"}:
            raise PreviewError("terminal preview cannot record a stage")
        receipt = {
            "schema": "taskplane.host-preview-stage/v1", "stage": stage,
            "outcome": outcome, "detail": str(detail)[:512],
            "at": float(self._clock()), "target": preview["target"],
            "revision": preview["revision"],
        }
        receipt["fingerprint"] = _digest(receipt)
        preview["events"].append(receipt)
        self._audit(preview_id=preview_id, outcome=f"{stage}_{outcome}",
                    detail=receipt["detail"] or f"{stage} {outcome}",
                    target=preview["target"], revision=preview["revision"])
        self._save(preview)
        return receipt

    def _teardown(self, preview: dict, outcome: str) -> None:
        sandbox = self._path(preview["preview_id"]).parent / "sandbox"
        # The runtime creates an empty registration scope; process-tree cleanup
        # is delegated to the isolation launcher before this bounded removal.
        processes_stopped = (self._process_teardown is None or
                             self._process_teardown(
                                 preview["preview_id"],
                                 preview.get("process_ownership")))
        removed = False
        try:
            if processes_stopped:
                shutil.rmtree(sandbox)
                removed = True
        except OSError:
            removed = False
        preview["teardown"] = {
            "attempted": True,
            "outcome": "succeeded" if removed else "failed",
            "at": float(self._clock()),
            "trigger": outcome,
            "processes_stopped": processes_stopped,
        }

    def expire(self, preview_id: str) -> dict:
        """Deadline callback: fail and reap a still-live preview."""
        preview = self._load(preview_id)
        if preview["state"] in {"registered", "open"}:
            return self.record_outcome(preview_id, "timed_out")
        return preview

    def arm_deadline(self, preview_id: str) -> None:
        preview = self._load(preview_id)
        remaining = max(0.0, float(preview["deadline"]) - float(self._clock()))
        timer = threading.Timer(remaining, self.expire, args=(preview_id,))
        timer.daemon = True
        timer.start()

    def record_outcome(self, preview_id: str, outcome: str) -> dict:
        if outcome not in TERMINAL_OUTCOMES:
            raise PreviewError("preview outcome is unsupported")
        preview = self._load(preview_id)
        preview["state"] = "failed"
        preview["outcome"] = outcome
        self._teardown(preview, outcome)
        if outcome == "teardown_failed":
            preview["teardown"]["outcome"] = "failed"
        self._audit(preview_id=preview_id, outcome=outcome,
                    detail=f"preview failed: {outcome}", target=preview["target"],
                    revision=preview["revision"])
        return self._save(preview)

    def close(self, preview_id: str) -> dict:
        preview = self._load(preview_id)
        if preview["state"] not in {"registered", "open"}:
            raise PreviewError("terminal preview cannot close successfully")
        startup_limits = _saved_startup_limits(preview)
        if startup_limits is None:
            return self.record_outcome(preview_id, "unavailable")
        try:
            source_fingerprint = _path_fingerprint(
                self.workspace, limits=startup_limits,
                exclude_generated=True)
        except _StartupBoundExceeded:
            return self.record_outcome(preview_id, "unavailable")
        if source_fingerprint != preview["source_fingerprint"]:
            return self.record_outcome(preview_id, "escaped_path")
        self._teardown(preview, "closed")
        if preview["teardown"]["outcome"] != "succeeded":
            preview["state"] = "failed"
            preview["outcome"] = "teardown_failed"
        else:
            preview["state"] = "closed"
            preview["outcome"] = "succeeded"
        self._audit(preview_id=preview_id, outcome=preview["outcome"],
                    detail="preview teardown completed", target=preview["target"],
                    revision=preview["revision"])
        return self._save(preview)


def launch_working_preview(*, flow: str, host: str, state_root: str | Path,
                           source_root: str | Path, authorization: str,
                           target: str, revision: int, capabilities: Mapping,
                           command: object, limits: Mapping) -> dict:
    """Production entry point shared by design, build, and dynamic review."""
    from taskplane.command_adapters import (
        CommandAdapter, native_surface_transport,
        os_preview_isolation_launcher, teardown_preview_processes,
    )
    from taskplane.command_runtime import CommandRuntime

    root = Path(state_root)
    preview_runtime = PreviewRuntime(
        root / "previews", workspace=source_root,
        authorization=authorization,
        surface_transport=native_surface_transport,
        process_teardown=teardown_preview_processes)
    preview = preview_runtime.register(
        flow=flow, target=target, revision=revision, source_root=source_root,
        authorization=authorization, capabilities=capabilities, limits=limits,
        network_allowlist=[])
    command_runtime = CommandRuntime(
        str(root / "commands"), workspace=str(Path(source_root).resolve()),
        authorization=authorization)
    adapter = CommandAdapter(
        host=host, runtime=command_runtime,
        launcher=lambda *_: (_ for _ in ()).throw(
            ValueError("ordinary launcher is forbidden for previews")),
        review_isolation_launcher=os_preview_isolation_launcher)
    sandbox = preview_runtime.sandbox_path(preview["preview_id"])
    try:
        handle = adapter.launch_preview(command, cwd=str(sandbox),
                                        preview=preview)
        preview_runtime.bind_command(
            preview["preview_id"], handle=handle,
            binding_digest=command_runtime.snapshot(handle)["binding_digest"],
            process_ownership=adapter.preview_process_ownership(handle))
        opened = preview_runtime.open(preview["preview_id"])
    except Exception as exc:
        preview_runtime.record_stage(
            preview["preview_id"], stage="interaction", outcome="failed",
            detail=f"preview startup failed: {exc}")
        preview_runtime.record_outcome(preview["preview_id"], "unavailable")
        raise
    if opened["state"] != "open":
        raise PreviewDenied(opened["outcome"], "native preview did not open")
    preview_runtime.arm_deadline(preview["preview_id"])
    return {"schema": "taskplane.working-preview-launch/v1",
            "flow": flow, "preview": opened, "command_handle": handle}


def launch_design_preview(**kwargs) -> dict:
    """Production design-flow preview entry."""
    return launch_working_preview(flow="design", **kwargs)


def launch_build_preview(**kwargs) -> dict:
    """Production build-flow preview entry."""
    return launch_working_preview(flow="build", **kwargs)


def launch_dynamic_review_preview(**kwargs) -> dict:
    """Production dynamic-review preview entry."""
    return launch_working_preview(flow="dynamic_review", **kwargs)


_PREVIEW_REQUEST_FIELDS = frozenset({
    "flow", "host", "state_root", "source_root", "authorization", "target",
    "revision", "capabilities", "command", "limits",
})


def _normalize_preview_request(request: Mapping[str, object]) -> dict:
    """Validate the installed preview request contract before host launch.

    The contract is a closed JSON object with exactly the fields in
    ``_PREVIEW_REQUEST_FIELDS``.  ``flow`` is one of the three governed
    preview flows; ``host`` is a supported Taskplane host; paths,
    authorization and target are non-empty strings; revision is a non-negative
    integer; capabilities and limits are objects; and command is a non-empty,
    shell-free argv array of NUL-free strings.  Resource and capability values
    are then enforced by :class:`PreviewRuntime` and the host isolation seam.
    """
    if not isinstance(request, Mapping) or set(request) != _PREVIEW_REQUEST_FIELDS:
        raise PreviewDenied("denied", "preview request fields are invalid")
    normalized = dict(request)
    if normalized.get("flow") not in VALID_FLOWS:
        raise PreviewDenied("denied", "preview request flow is invalid")
    if normalized.get("host") not in {"claude", "codex"}:
        raise PreviewDenied("denied", "preview request host is invalid")
    for name in ("state_root", "source_root", "authorization", "target"):
        value = normalized.get(name)
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise PreviewDenied(
                "denied", f"preview request {name} is invalid")
    revision = normalized.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PreviewDenied("denied", "preview request revision is invalid")
    if not isinstance(normalized.get("capabilities"), Mapping) or \
            not isinstance(normalized.get("limits"), Mapping):
        raise PreviewDenied(
            "denied", "preview request capabilities and limits must be objects")
    command = normalized.get("command")
    if not isinstance(command, list) or not command or any(
            not isinstance(item, str) or not item or "\x00" in item
            for item in command):
        raise PreviewDenied(
            "denied", "preview request command must be direct argv")
    executable = os.path.basename(command[0]).lower()
    if executable in {"sh", "bash", "zsh", "dash", "fish", "cmd",
                      "cmd.exe", "powershell", "pwsh"}:
        raise PreviewDenied(
            "denied", "preview request command cannot use a shell wrapper")
    return normalized


def launch_preview_request(request: Mapping[str, object]) -> dict:
    """Execute one closed flow request through its production preview edge.

    This is the host-neutral composition root used by orchestration adapters
    and by this module's executable entry point.  Keeping the flow dispatch
    here makes all three supported paths live while preserving the stronger
    flow-specific entry points as the only launch authorities.
    """
    normalized = _normalize_preview_request(request)
    flow = normalized["flow"]
    entrypoints = {
        "design": launch_design_preview,
        "build": launch_build_preview,
        "dynamic_review": launch_dynamic_review_preview,
    }
    entry = entrypoints[flow]
    kwargs = {name: value for name, value in normalized.items()
              if name != "flow"}
    return entry(**kwargs)


def load_preview_request(path: str | Path) -> dict:
    """Load and validate one bounded regular-file preview request."""
    source = Path(path)
    try:
        metadata = source.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PreviewDenied("denied", "preview request must be a regular file")
        if metadata.st_size > 1024 * 1024:
            raise PreviewDenied("denied", "preview request exceeds byte limit")
        value = json.loads(source.read_text(encoding="utf-8"))
    except PreviewDenied:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PreviewDenied("denied", "preview request is unavailable") from exc
    if not isinstance(value, Mapping):
        raise PreviewDenied("denied", "preview request must contain an object")
    return _normalize_preview_request(value)


def main(argv: Sequence[str] | None = None) -> int:
    """Launch a governed preview from a bounded JSON request."""
    parser = argparse.ArgumentParser(prog="python -m taskplane.preview_runtime")
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    result = launch_preview_request(load_preview_request(args.request))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "AUDIT_SCHEMA", "MAX_STARTUP_ENTRIES", "MAX_STARTUP_FILE_BYTES",
    "MAX_STARTUP_SECONDS", "MAX_STARTUP_TOTAL_BYTES", "PreviewDenied",
    "PreviewError", "PreviewRuntime", "SCHEMA", "launch_build_preview",
    "launch_design_preview", "launch_dynamic_review_preview",
    "launch_preview_request", "launch_working_preview",
    "load_preview_request", "main",
]


if __name__ == "__main__":  # pragma: no cover - exercised as an installed CLI
    raise SystemExit(main())
