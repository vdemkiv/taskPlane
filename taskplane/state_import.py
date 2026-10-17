"""Explicit same-root import after loss of ignored local state.

The original database is retained verbatim. A new recovery receipt, never a
fabricated historical initialization marker, authorizes the local store binding.
This cooperative operation does not authenticate the host or accept a phase.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from . import delegated_approval, primitives, storage, workflow as w, workflow_evidence as evidence

SCHEMA = "taskplane.state-import/v1"
REQUEST = "taskplane.state-import-request/v1"
LIMIT = 16 * 1024 * 1024
RECEIPT_LIMIT = 65536
MISSING = ["original initialization marker and transaction", "original harness and presentation",
           "historical context delivery ledgers and unretained objects", "historical flow journal and dashboards"]


def read_regular(path: Path, limit: int = LIMIT) -> bytes:
    w.require(path.is_absolute() and all(not p.is_symlink() for p in (path, *path.parents)),
              "invalid_evidence", "Recovery inputs must be absolute regular paths without symlinks.")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        w.require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "invalid_evidence", "Recovery input is not regular.")
        raw = stream.read(limit + 1)
    w.require(len(raw) <= limit, "invalid_evidence", "Recovery input exceeds its size bound.")
    return raw


def pinned(item: Any, limit: int = LIMIT) -> bytes:
    w.require(isinstance(item, dict) and set(item) == {"path", "sha256"}
              and isinstance(item["path"], str) and isinstance(item["sha256"], str)
              and re.fullmatch(r"[a-f0-9]{64}", item["sha256"]), "invalid_evidence", "Pin each recovery input by SHA-256.")
    raw = read_regular(Path(item["path"]), limit)
    w.require(hashlib.sha256(raw).hexdigest() == item["sha256"], "stale_checkpoint", "Recovery input checksum changed.")
    return raw


def record_path(workspace: Path, root: str) -> Path:
    key = hashlib.sha256(root.encode()).hexdigest()[:32]
    return storage.runtime_file(str(workspace), "state-import-" + key + ".json")


def record(workspace: Path, root: str) -> dict[str, Any] | None:
    path = record_path(workspace, root)
    if not path.exists():
        return None
    try:
        value = json.loads(read_regular(path, RECEIPT_LIMIT))
    except (OSError, ValueError) as exc:
        raise w.Refusal("state_unavailable", "Recovery receipt cannot be read; preserve it for repair.") from exc
    w.require(isinstance(value, dict) and value.get("schema") == SCHEMA
              and value.get("workspace") == str(workspace) and value.get("root") == root
              and value.get("profile") == "native_workflow" and isinstance(value.get("status"), str)
              and value["status"] in {"prepared", "committed"}
              and isinstance(value.get("binding"), dict)
              and set(value["binding"]) == {"workspace", "root", "run", "revision", "visit", "checkpoint", "manifest_digest", "scope_digest"}
              and all(isinstance(value["binding"].get(k), str) and value["binding"][k]
                      for k in ("workspace", "root", "run", "visit", "checkpoint", "manifest_digest", "scope_digest"))
              and type(value["binding"].get("revision")) is int and value["binding"]["revision"] >= 1
              and isinstance(value.get("request_sha256"), str)
              and re.fullmatch(r"[a-f0-9]{64}", value["request_sha256"])
              and isinstance(value.get("original_controller"), dict)
              and value["original_controller"].get("path") == str(workspace / ".taskplane" / "recovery-v1" / value["request_sha256"] / "original-controller.json")
              and isinstance(value.get("digest"), str)
              and value["digest"] == primitives.content_fingerprint({k: v for k, v in value.items() if k != "digest"}),
              "state_unavailable", "Recovery receipt identity or integrity changed.")
    return dict(value)


def initialization(workspace: Path, root: str) -> dict[str, Any] | None:
    receipt = record(workspace, root)
    if receipt is None:
        return None
    w.require(receipt["status"] == "committed", "state_unavailable",
              "State import is incomplete. Retry the exact flow recover --recovery-request request; do not initialize a new run.")
    try:
        raw = pinned(receipt["original_controller"])
        original = json.loads(raw)
        retained = Path(receipt["original_controller"]["path"]).parent
        request = json.loads(pinned({"path": str(retained / "request.json"), "sha256": receipt["request_sha256"]}, 262144))
        w.require(isinstance(request, dict) and request.get("schema") == REQUEST
                  and request.get("host") == receipt.get("host")
                  and primitives.content_fingerprint(request.get("binding")) == primitives.content_fingerprint(receipt["binding"])
                  and request.get("controller", {}).get("sha256") == receipt["original_controller"]["sha256"],
                  "state_unavailable", "Retained recovery request differs from its receipt.")
        pinned({"path": str(retained / "approval-observation.json"), "sha256": request["approval"]["sha256"]}, 16384)
        state = original["runs"][receipt["binding"]["run"]]
        w.require(original.get("root") == root and original.get("workspace") == str(workspace)
                  and original.get("active") == receipt["binding"]["run"]
                  and primitives.content_fingerprint(w.binding(state, w.current(state)["packet"]))
                      == primitives.content_fingerprint(receipt["binding"]),
                  "state_unavailable", "Retained import source differs from the recovered binding.")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise w.Refusal("state_unavailable", "Retained recovery provenance is unavailable; restore its exact preserved files.") from exc
    return receipt


def _sync_published(path: Path, raw: bytes) -> None:
    """Sync the winning destination, which may have been published by a retry."""
    w.require(all(not p.is_symlink() for p in (path, *path.parents)), "scope_violation", "Recovery target follows a symlink.")
    # Windows fsync uses _commit/FlushFileBuffers, which requires a writable
    # handle. Open the existing file without creation or truncation; readback
    # still verifies the exact published bytes before acknowledging durability.
    access = os.O_RDWR if os.name == "nt" else os.O_RDONLY
    fd = os.open(path, access | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        w.require(stat.S_ISREG(info.st_mode) and info.st_size == len(raw)
                  and stream.read(len(raw) + 1) == raw,
                  "state_unavailable", "Recovery cannot overwrite differing state or evidence.")
        os.fsync(stream.fileno())
    primitives._fsync_directory(str(path.parent))


def _publish(path: Path, raw: bytes) -> None:
    """Create-if-absent, including retries; never replace a differing file."""
    w.require(all(not p.is_symlink() for p in (path, *path.parents)), "scope_violation", "Recovery target follows a symlink.")
    if path.exists():
        w.require(read_regular(path) == raw, "state_unavailable", "Recovery cannot overwrite differing state or evidence.")
    # Flush existing ancestry too: a prior interrupted mkdir/link may be visible
    # but not durable. Unsupported directory sync propagates; it is not success.
    primitives._durable_makedirs(str(path.parent), mode=0o700, sync_existing=True)
    if path.exists():
        _sync_published(path, raw)
        return
    fd, name = tempfile.mkstemp(prefix=".state-import-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            w.require(read_regular(path) == raw, "state_unavailable", "Recovery publication raced with differing state.")
        _sync_published(path, raw)
    finally:
        os.unlink(name)


def _receipt(value: dict[str, Any]) -> dict[str, Any]:
    value = {k: v for k, v in value.items() if k != "digest"}
    result = {**value, "digest": primitives.content_fingerprint(value)}
    raw = (json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()
    w.require(len(raw) <= RECEIPT_LIMIT, "state_unavailable",
              "Recovery receipt exceeds its size bound; preserve the complete backup for a supported recovery.")
    return result


def _save_receipt(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    result = _receipt(value)
    primitives._durable_makedirs(str(path.parent), mode=0o700, sync_existing=True)
    primitives.atomic_json(path, result, strict_directory_sync=True)
    return result


def recover(controller: Any, request_path: str, expected_sha256: str, *, mode: str = "inspect") -> dict[str, Any]:
    """Import only an unapproved, quiescent first Product checkpoint in this root."""
    from . import workflow_local as local
    w.require(controller.adapter.profile == "native_workflow" and controller.principal == controller.root,
              "unsupported_authority", "State import belongs to the original native root.")
    w.require(mode in {"inspect", "apply"}, "invalid_evidence", "Choose inspect or apply.")
    raw_request = pinned({"path": request_path, "sha256": expected_sha256}, 262144)
    request = json.loads(raw_request)
    w.require(isinstance(request, dict) and set(request) == {"schema", "host", "binding", "controller", "artifacts", "approval", "request_reference"}
              and request["schema"] == REQUEST and request["host"] == controller.adapter.name
              and isinstance(request["request_reference"], str) and 0 < len(request["request_reference"].strip()) <= 512,
              "invalid_evidence", "Use a same-host recovery request with actual user provenance.")
    original = pinned(request["controller"], local.MAX_BYTES)
    db = controller._validate_database(json.loads(original))
    run = db["active"]
    w.require(run and set(db["runs"]) == {run} and not db.get("archives"), "state_unavailable",
              "This import supports one retained active run; preserve other history for a supported recovery.")
    state = db["runs"][run]
    stage = w.current(state)
    w.require(state["index"] == 0 and stage["phase"] == "product" and stage["decision"] == "awaiting_human_approval"
              and not state["decisions"] and not state.get("approval_policy") and not state.get("policy_events")
              and not any(state.get(k) for k in ("finished", "retired", "superseded_by", "cancellation", "workers", "workspace_contract"))
              and all(v["packet"] is None for v in state["visits"][1:]),
              "state_unavailable", "Import requires the original unapproved Product checkpoint without workers or newer phases.")
    w.require(not any(r.get("state") == "running" for r in state.get("observed_handles", {}).values())
              and all(r.get("state") == "completed" and r.get("host") == request["host"] for r in db.get("admissions", {}).values()),
              "state_unavailable", "Unresolved native attempts or host mismatch prevent import.")
    binding = w.binding(state, stage["packet"])
    w.require(request["binding"] == binding
              and primitives.content_fingerprint(request["binding"]) == primitives.content_fingerprint(binding),
              "stale_checkpoint", "Recovery request differs from the preserved checkpoint.")
    manifest = stage["packet"]["manifest"]
    w.require(all(isinstance(value, str) for value in manifest.values()), "state_unavailable",
              "This bounded import does not support a normalized task manifest; restore the complete original store "
              "or preserve this checkpoint for a recovery that validates task definitions.")
    current_source = local.inventory(controller.workspace)
    before_source = state["source_baseline"]
    w.require(all(before_source.get(p) == current_source.get(p) for p in set(before_source) | set(current_source)
                  if p not in manifest), "stale_checkpoint",
              "Source differs from the original baseline; import cannot silently migrate it.")
    approval = pinned(request["approval"], 16384)
    verified = delegated_approval.verify_checkpoint(state, json.loads(approval), binding, state["decisions"])
    w.require(verified.get("choice") == "approved", "invalid_evidence", "Preserve the original scoped approval evidence.")
    w.require(isinstance(request["artifacts"], dict) and set(request["artifacts"]) == set(manifest),
              "invalid_evidence", "Supply exactly every sealed artifact, without expanding the write set.")
    files: dict[Path, bytes] = {}
    for relative, item in request["artifacts"].items():
        target = evidence.path(controller.workspace, relative)
        # Runtime control files are not phase artifacts, even in a forged manifest.
        parts = Path(relative).parts
        w.require(parts[0] != ".taskplane" or len(parts) >= 3 and parts[1] not in {"context-v1", "recovery-v1", "knowledge"},
                  "scope_violation", "Import cannot write runtime metadata as a sealed artifact.")
        raw = pinned(item)
        w.require(isinstance(manifest[relative], str) and hashlib.sha256(raw).hexdigest() == manifest[relative],
                  "stale_checkpoint", "Artifact does not match the sealed original manifest.")
        if target.exists():
            w.require(read_regular(target) == raw, "stale_checkpoint", "Existing artifact conflicts with the preserved seal.")
        files[target] = raw
    for relative, expected in stage["packet"]["source_manifest"].items():
        target_file = evidence.path(controller.workspace, relative)
        actual = (hashlib.sha256(files[target_file]).hexdigest() if target_file in files
                  else evidence.manifest(controller.workspace, [relative], allow_missing=True,
                                         task_path=relative if isinstance(expected, dict) else None)[relative])
        w.require(actual == expected, "stale_checkpoint", "Verified source differs from the sealed checkpoint.")
    missing_refs: list[dict[str, Any]] = []
    def refs(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("schema") == "taskplane.context-reference/v1":
                missing_refs.append(deepcopy(value))
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)
    refs(state)
    w.require(len(missing_refs) <= 64, "state_unavailable", "Historical context inventory exceeds this bounded import.")
    target = controller._path()
    receipt_path = record_path(controller.workspace, controller.root)
    recovery_dir = controller.workspace / ".taskplane" / "recovery-v1" / expected_sha256
    snapshot = recovery_dir / "original-controller.json"
    candidate = deepcopy(db)
    candidate["runs"][run].pop("parent_hook_readiness", None)  # Old pair remains only in the preserved original.
    restored = (json.dumps(candidate, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    files.update({snapshot: original, recovery_dir / "approval-observation.json": approval,
                  recovery_dir / "request.json": raw_request, target: restored})
    with primitives.file_lock(str(target)) if mode == "apply" else nullcontext():
        previous = record(controller.workspace, controller.root)
        if previous:
            w.require(previous.get("request_sha256") == expected_sha256, "state_unavailable", "A different import already owns this recovery.")
            if previous["status"] == "committed":
                initialization(controller.workspace, controller.root)
                controller._read(target)
                # The last attempt may have replaced the committed receipt and
                # then failed its directory sync. Confirm durability on retry.
                if mode == "apply":
                    primitives._durable_makedirs(str(receipt_path.parent), sync_existing=True)
                    _sync_published(target, read_regular(target))
                    _sync_published(receipt_path, read_regular(receipt_path, RECEIPT_LIMIT))
                return {**previous, "status": "already_imported", "authority": "none"}
        else:
            w.require(not target.exists() and not target.with_name(target.stem + ".initialized.json").exists()
                      and not storage.runtime_file(str(controller.workspace), "initialization-" + target.stem.removeprefix("workflow-") + ".json").exists(),
                      "state_unavailable", "Import requires missing controller and initialization metadata; never replaces a store.")
        for path, raw in files.items():
            w.require(all(not entry.is_symlink() for entry in (path, *path.parents)),
                      "scope_violation", "Recovery target follows a symlink.")
            if path.exists():
                w.require(read_regular(path) == raw, "state_unavailable", "Recovery target differs from this exact import.")
        summary = previous or {"schema": SCHEMA, "status": "prepared", "profile": "native_workflow",
            "workspace": str(controller.workspace), "root": controller.root, "host": request["host"],
            "binding": binding, "request_sha256": expected_sha256, "request_reference": request["request_reference"],
            "observed_at": datetime.now(timezone.utc).isoformat(), "assurance": "local_recovery_observation",
            "host_attested": False, "original_controller": {"path": str(snapshot), "sha256": request["controller"]["sha256"]},
            "restored_controller_sha256": hashlib.sha256(restored).hexdigest(), "sealed_artifacts": manifest,
            "approval_event": verified["event_id"], "approval_recorded": False,
            "missing_history": MISSING, "historical_context_refs_unverified": missing_refs,
            "reconstructed": ["local store binding from verified same-root controller", "fresh harness engagement"],
            "invalidated": ["old automatic parent hook readiness", "old presentation"],
            "next_action": "Observe a fresh automatic hook pair; regenerate and present the same Product checkpoint, record its existing approval separately, then read fresh Design context."}
        _receipt(summary)
        _receipt({**summary, "status": "committed"})
        if mode == "inspect":
            return {**summary, "status": "ready_to_import", "authority": "none",
                    "writes": [str(p) for p in files] + [str(receipt_path), str(local.Harness(controller.workspace, controller.root).path)]}
        if previous is None:
            _save_receipt(receipt_path, summary)  # A durable incomplete receipt blocks new initialization after a crash.
        for path, raw in files.items():
            _publish(path, raw)
        w.require(evidence.changed(controller.workspace, state) is None, "stale_checkpoint", "Restored evidence differs from its sealed source.")
        local.Harness(controller.workspace, controller.root).update(selected=True, entry="taskplane", run=run,
            request_reference=request["request_reference"], presentation=None, waiting=None,
            state_import={"request_sha256": expected_sha256, "historical_engagement": "unavailable"})
        result = _save_receipt(receipt_path, {**summary, "status": "committed"})
        return {**result, "status": "imported", "authority": "none"}
