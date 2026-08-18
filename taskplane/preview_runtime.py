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
import time
from typing import Callable, Mapping


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


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _path_fingerprint(root: Path) -> str:
    """Fingerprint names and bytes without following links outside ``root``."""
    rows: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
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
                 authorization: str, clock: Callable[[], float] | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("preview workspace must be a directory")
        self._authorization = _digest(str(authorization))
        self._clock = clock or time.time
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
        now = float(self._clock())
        preview = {
            "schema": SCHEMA, "preview_id": preview_id, "flow": flow,
            "target": str(target), "revision": revision,
            "state": "registered", "outcome": "registered",
            "surface": surfaces[0], "surface_fallbacks": surfaces[1:],
            "source_fingerprint": _path_fingerprint(source),
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
        preview["state"] = "open"
        preview["outcome"] = "open"
        preview["events"].append({"kind": "opened", "at": self._clock()})
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
        removed = False
        try:
            sandbox.rmdir()
            removed = True
        except OSError:
            removed = False
        preview["teardown"] = {
            "attempted": True,
            "outcome": "succeeded" if removed else "failed",
            "at": float(self._clock()),
            "trigger": outcome,
        }

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
