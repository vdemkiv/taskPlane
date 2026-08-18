"""Governed, host-neutral lifecycle for interactive working previews.

This module is an authority for *whether* a preview may exist and for its
audit trail. Host adapters remain responsible for rendering a browser or side
panel and :mod:`taskplane.command_adapters` remains responsible for isolated
process launch. A preview never grants either layer authority over workflow
state.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import time
from typing import Callable, Mapping, Protocol


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


def _path_fingerprint(root: Path, *, exclude_vcs: bool = False) -> str:
    """Fingerprint names and bytes without following links outside ``root``."""
    rows: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if exclude_vcs and relative.split("/", 1)[0] == ".git":
            continue
        if path.is_symlink():
            rows.append((relative, f"link:{os.readlink(path)}"))
        elif path.is_file():
            rows.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return _digest(rows)


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
                 process_teardown: Callable[[str], bool] | None = None):
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

        preview_id = secrets.token_hex(16)
        sandbox = self._path(preview_id).parent / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=False)
        # Materialize the pinned input.  Never link back into the source: the
        # descendant process tree may write freely inside this disposable copy.
        try:
            for child in source.iterdir():
                # A preview is a pinned content copy, never a repository.
                # Omitting VCS metadata makes remote mutation/push impossible
                # even for an arbitrary interpreter inside the sandbox.
                if child.name == ".git":
                    continue
                destination = sandbox / child.name
                if child.is_symlink():
                    resolved = child.resolve()
                    if source not in (resolved, *resolved.parents):
                        raise PreviewDenied(
                            "escaped_path", "source contains an escaping link")
                if child.is_dir():
                    shutil.copytree(child, destination, symlinks=True)
                else:
                    shutil.copy2(child, destination, follow_symlinks=False)
        except Exception:
            shutil.rmtree(sandbox, ignore_errors=True)
            raise
        materialized_fingerprint = _path_fingerprint(sandbox)
        source_fingerprint = _path_fingerprint(source)
        source_content_fingerprint = _path_fingerprint(source, exclude_vcs=True)
        if materialized_fingerprint != source_content_fingerprint:
            shutil.rmtree(sandbox, ignore_errors=True)
            self._deny("escaped_path", "pinned source materialization failed",
                       target=target, revision=revision)
        now = float(self._clock())
        preview = {
            "schema": SCHEMA, "preview_id": preview_id, "flow": flow,
            "target": str(target), "revision": revision,
            "state": "registered", "outcome": "registered",
            "surface": surfaces[0], "surface_fallbacks": surfaces[1:],
            "source_fingerprint": source_fingerprint,
            "source_content_fingerprint": source_content_fingerprint,
            "materialized_fingerprint": materialized_fingerprint,
            "source_root_fingerprint": _digest(str(source)),
            "authorization_fingerprint": self._authorization,
            "visibility": "private", "push_disabled": True,
            "network": {"mode": "deny", "allowlist": []},
            "limits": bounded, "registered_at": now,
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
        if _path_fingerprint(sandbox) != preview["materialized_fingerprint"]:
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
                     binding_digest: str) -> dict:
        """Durably bind the isolated command lifecycle to this preview."""
        preview = self._load(preview_id)
        if preview["state"] != "registered" or not handle or not binding_digest:
            raise PreviewError("preview command lifecycle binding is invalid")
        preview["command_lifecycle"] = {
            "handle_fingerprint": _digest(handle),
            "process_group_binding": str(binding_digest),
            "bound_at": float(self._clock()),
        }
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
                             self._process_teardown(preview["preview_id"]))
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
        if _path_fingerprint(self.workspace) != preview["source_fingerprint"]:
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
            binding_digest=command_runtime.snapshot(handle)["binding_digest"])
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
