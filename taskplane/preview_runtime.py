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


def _stream_digest(path: Path, *, deadline: float,
                   byte_limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while True:
                _deadline_check(deadline)
                chunk = stream.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > byte_limit:
                    raise _StartupBoundExceeded(
                        f"preview file exceeds byte limit: {path.name}")
                digest.update(chunk)
    except OSError as exc:
        raise _StartupBoundExceeded(
            f"preview source file is unreadable: {path.name}") from exc
    return digest.hexdigest(), total


def _bounded_manifest(root: Path, *, limits: Mapping[str, int],
                      exclude_generated: bool,
                      deadline: float | None = None) -> tuple[list[dict], str]:
    """Return one bounded, streaming snapshot without following symlinks."""
    deadline = (time.monotonic() + limits["startup_seconds"]
                if deadline is None else deadline)
    pending = [Path(".")]
    rows: list[dict] = []
    total_bytes = 0
    observed_entries = 0
    while pending:
        _deadline_check(deadline)
        relative_parent = pending.pop()
        parent = root if relative_parent == Path(".") else root / relative_parent
        try:
            entries = []
            with os.scandir(parent) as iterator:
                for entry in iterator:
                    _deadline_check(deadline)
                    if exclude_generated and \
                            entry.name in EXCLUDED_PREVIEW_DIRECTORIES:
                        continue
                    observed_entries += 1
                    if observed_entries > limits["startup_entries"]:
                        raise _StartupBoundExceeded(
                            "preview source exceeds entry limit")
                    entries.append(entry)
            entries.sort(key=lambda row: row.name)
        except OSError as exc:
            raise _StartupBoundExceeded(
                "preview source inventory is unreadable") from exc
        for entry in entries:
            _deadline_check(deadline)
            relative = (relative_parent / entry.name
                        if relative_parent != Path(".") else Path(entry.name))
            if entry.is_symlink():
                raise _StartupBoundExceeded(
                    f"preview source contains a symlink: {relative.as_posix()}")
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise _StartupBoundExceeded(
                    f"preview source entry is unreadable: {relative.as_posix()}") \
                    from exc
            if stat.S_ISDIR(metadata.st_mode):
                row = {"path": relative.as_posix(), "kind": "directory"}
                pending.append(relative)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_size > limits["startup_file_bytes"]:
                    raise _StartupBoundExceeded(
                        f"preview file exceeds byte limit: {relative.as_posix()}")
                file_digest, observed_size = _stream_digest(
                    Path(entry.path), deadline=deadline,
                    byte_limit=limits["startup_file_bytes"])
                if observed_size != metadata.st_size:
                    raise _StartupBoundExceeded(
                        f"preview source changed during inventory: "
                        f"{relative.as_posix()}")
                total_bytes += observed_size
                if total_bytes > limits["startup_total_bytes"]:
                    raise _StartupBoundExceeded(
                        "preview source exceeds total-byte limit")
                row = {"path": relative.as_posix(), "kind": "file",
                       "bytes": observed_size, "sha256": file_digest}
            else:
                raise _StartupBoundExceeded(
                    f"preview source contains a special entry: "
                    f"{relative.as_posix()}")
            rows.append(row)
    rows.sort(key=lambda row: str(row["path"]))
    return rows, _digest(rows)


def _materialize_manifest(source: Path, sandbox: Path, rows: Sequence[Mapping],
                          *, limits: Mapping[str, int], deadline: float) -> None:
    copied_bytes = 0
    for row in rows:
        _deadline_check(deadline)
        relative = Path(str(row["path"]))
        source_path = source / relative
        destination = sandbox / relative
        if row["kind"] == "directory":
            destination.mkdir(parents=True, exist_ok=False)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        observed = 0
        try:
            with source_path.open("rb") as reader, destination.open("xb") as writer:
                while True:
                    _deadline_check(deadline)
                    chunk = reader.read(HASH_CHUNK_BYTES)
                    if not chunk:
                        break
                    observed += len(chunk)
                    copied_bytes += len(chunk)
                    if observed > limits["startup_file_bytes"] or \
                            copied_bytes > limits["startup_total_bytes"]:
                        raise _StartupBoundExceeded(
                            "preview materialization exceeds byte limit")
                    writer.write(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise _StartupBoundExceeded(
                f"preview materialization failed: {relative.as_posix()}") from exc
        if observed != row.get("bytes") or digest.hexdigest() != row.get("sha256"):
            raise _StartupBoundExceeded(
                f"preview source changed during materialization: "
                f"{relative.as_posix()}")
        shutil.copystat(source_path, destination, follow_symlinks=False)


def _path_fingerprint(root: Path, *, limits: Mapping[str, int],
                      exclude_generated: bool = False) -> str:
    return _bounded_manifest(
        root, limits=limits, exclude_generated=exclude_generated)[1]


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

        preview_id = secrets.token_hex(16)
        sandbox = self._path(preview_id).parent / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=False)
        try:
            startup_deadline = time.monotonic() + \
                startup_limits["startup_seconds"]
            source_manifest, source_fingerprint = _bounded_manifest(
                source, limits=startup_limits, exclude_generated=True,
                deadline=startup_deadline)
            _materialize_manifest(
                source, sandbox, source_manifest, limits=startup_limits,
                deadline=startup_deadline)
            materialized_manifest, materialized_fingerprint = _bounded_manifest(
                sandbox, limits=startup_limits, exclude_generated=False,
                deadline=startup_deadline)
            if materialized_manifest != source_manifest or \
                    materialized_fingerprint != source_fingerprint:
                raise _StartupBoundExceeded(
                    "pinned source materialization differs from inventory")
        except _StartupBoundExceeded as exc:
            shutil.rmtree(sandbox, ignore_errors=True)
            self._deny("unavailable", str(exc), target=target,
                       revision=revision)
        except Exception:
            shutil.rmtree(sandbox, ignore_errors=True)
            raise
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


def launch_preview_request(request: Mapping[str, object]) -> dict:
    """Execute one closed flow request through its production preview edge.

    This is the host-neutral composition root used by orchestration adapters
    and by this module's executable entry point.  Keeping the flow dispatch
    here makes all three supported paths live while preserving the stronger
    flow-specific entry points as the only launch authorities.
    """
    if not isinstance(request, Mapping) or set(request) != _PREVIEW_REQUEST_FIELDS:
        raise PreviewDenied("denied", "preview request fields are invalid")
    flow = request.get("flow")
    entrypoints = {
        "design": launch_design_preview,
        "build": launch_build_preview,
        "dynamic_review": launch_dynamic_review_preview,
    }
    entry = entrypoints.get(flow) if isinstance(flow, str) else None
    if entry is None:
        raise PreviewDenied("denied", "preview request flow is invalid")
    kwargs = {name: value for name, value in request.items() if name != "flow"}
    return entry(**kwargs)


def _request_file(path: str | Path) -> Mapping[str, object]:
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
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Launch a governed preview from a bounded JSON request."""
    parser = argparse.ArgumentParser(prog="python -m taskplane.preview_runtime")
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    result = launch_preview_request(_request_file(args.request))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "AUDIT_SCHEMA", "MAX_STARTUP_ENTRIES", "MAX_STARTUP_FILE_BYTES",
    "MAX_STARTUP_SECONDS", "MAX_STARTUP_TOTAL_BYTES", "PreviewDenied",
    "PreviewError", "PreviewRuntime", "SCHEMA", "launch_build_preview",
    "launch_design_preview", "launch_dynamic_review_preview",
    "launch_preview_request", "launch_working_preview", "main",
]


if __name__ == "__main__":  # pragma: no cover - exercised as an installed CLI
    raise SystemExit(main())
