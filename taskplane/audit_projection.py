"""Closed, bounded projections for append-only audit traces.

This stdlib-only leaf owns the privacy-safe record shape, persistence,
rotation, and retention.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from collections.abc import Mapping
from typing import Any, cast
import hashlib
import json
import os
import stat
import secrets
import re
import time as _time


_AUDIT_TEXT_MAX_CHARS = 2048
_AUDIT_COLLECTION_MAX_ITEMS = 64


def root_hygiene_projection(receipt: Mapping[str, object]) -> dict[str, object]:
    """Return the bounded audit view of the canonical root seal."""
    from taskplane import wave_metrics
    return wave_metrics.root_hygiene_projection(receipt, consumer="audit")


_AUDIT_IDENTITY_FIELDS = frozenset({
    "actor", "agent", "agent_id", "agent_type", "approved_by", "by",
    "email", "host", "host_id", "host_session_id", "host_turn_id",
    "hostname", "human", "session", "session_id", "thread", "thread_id",
    "turn_id", "user", "username", "validator", "workstation",
})
_AUDIT_FREE_TEXT_FIELDS = frozenset({
    "block", "blockers", "command", "commands", "context_docs",
    "conversation", "conversations", "diff", "dor_blockers", "dor_warnings",
    "error", "errors", "files", "goal", "lessons", "missing", "note",
    "notices", "observations", "output", "patch", "path", "paths",
    "prompt", "prompts", "reason", "reasons", "scope", "snapshot",
    "title", "touched", "transcript", "transcripts", "warnings",
    "write_allow",
})
_AUDIT_BOOLEAN_FIELDS = frozenset({"dor_ready"})
_AUDIT_COLLECTION_FIELDS = frozenset({"lenses"})
_AUDIT_LITERAL_FIELDS = frozenset({
    "action", "action_id", "age_s", "approval_enabled", "archived_tasks",
    "archived_to",
    "authority_effect_id", "authorized", "blocking", "capability_source",
    "ceiling_usd", "changed_from", "collected_slots", "contract_id", "count",
    "criteria", "cycle", "decision", "denials", "design_only",
    "dispatch_pending", "dor_passed", "effective", "effective_breadth",
    "emit", "engine_off_reason", "engine_ran", "evidence_id",
    "exact_route_verified", "failure_code", "fingerprint", "first_step",
    "flow", "from_step", "gate", "graph_fingerprint", "graph_modules",
    "graph_quality_status", "held", "human_required", "id", "impacted",
    "kernel_status", "key", "kind", "max", "max_age_s", "max_fix_cycles",
    "migrated", "mode", "model", "modules", "old", "open", "operation",
    "operation_id", "outcome", "passed", "pending", "permission_mode",
    "produced_by", "produced_in", "read_only", "receipt",
    "receipt_fingerprint", "receipt_id", "recorded_key",
    "registry_fingerprint", "registry_version", "replay", "requirement",
    "requirement_id", "resolution", "restored", "retro_id", "review_id",
    "requested_breadth", "returncode", "reviews", "reviews_completed",
    "role", "routing_complete", "routing_counts", "routing_mode", "run_id",
    "seconds", "seconds_saved", "selection", "sha256", "shared_with",
    "slot", "slots", "spent_usd", "stage", "stage_id", "status",
    "generation", "lens_count", "step", "store", "stuck", "submitted",
    "suite_cited", "tags", "task", "task_id", "task_slot", "tier",
    "topology_fingerprint", "track", "triggered", "used", "via",
    "workflow_id",
})
_AUDIT_CLOSED_LITERAL_VALUES = {
    "requested_breadth": frozenset({"all", "routed"}),
    "effective_breadth": frozenset({"all", "routed"}),
    "engine_off_reason": frozenset({
        "forced-all", "signals-disabled", "catalog-stage-profiles-missing",
        "stage-not-requested", "mapper-unavailable", "engine-not-engaged",
    }),
}
_AUDIT_BOUNDED_INTEGER_VALUES = {
    "generation": (0, 99999),
    "lens_count": (0, 26),
    "returncode": (-2147483648, 4294967295),
}
_AUDIT_LITERAL_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,256}$")
_AUDIT_RELATIVE_PATH_RE = re.compile(
    r"^(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]{1,256}$")


def _audit_pseudonym(value: object) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()
    return "anon:" + digest[:20]


def _audit_minimized(value: object) -> dict[str, object]:
    encoded = json.dumps(value, sort_keys=True, default=str,
                         separators=(",", ":")).encode("utf-8", "replace")
    return {"schema": "taskplane.audit-minimized/v1",
            "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def _sanitize_audit_key(value: object) -> str:
    raw = str(value)
    normalized = raw.strip().lower()
    allowed = _AUDIT_IDENTITY_FIELDS | _AUDIT_FREE_TEXT_FIELDS | \
        _AUDIT_BOOLEAN_FIELDS | _AUDIT_COLLECTION_FIELDS | \
        _AUDIT_LITERAL_FIELDS | {"event", "ts", "schema"}
    if normalized in allowed:
        return normalized
    return "field:" + hashlib.sha256(
        raw.encode("utf-8", "replace")).hexdigest()[:20]


def _sanitize_audit_value(
        value: object, *, key: str = "", depth: int = 0) -> object:
    """Return a bounded, JSON-safe audit projection."""
    normalized_key = str(key).strip().lower()
    if normalized_key == "archived_to" and isinstance(value, str) and \
            _AUDIT_RELATIVE_PATH_RE.fullmatch(value) and \
            ".." not in value.split("/"):
        return value
    if normalized_key in _AUDIT_IDENTITY_FIELDS and value is not None:
        return _audit_pseudonym(value)
    if normalized_key in _AUDIT_FREE_TEXT_FIELDS and value is not None:
        return _audit_minimized(value)
    if normalized_key in _AUDIT_BOOLEAN_FIELDS:
        return value if isinstance(value, bool) else _audit_minimized(value)
    if normalized_key in _AUDIT_COLLECTION_FIELDS:
        if depth >= 6:
            return _audit_minimized(value)
        if isinstance(value, (list, tuple, set)):
            return [_sanitize_audit_value(
                        item, key=normalized_key, depth=depth + 1)
                    for item in list(value)[:_AUDIT_COLLECTION_MAX_ITEMS]]
        if isinstance(value, str) and value in {"deep", "light", "n/a"}:
            return value
        return _audit_minimized(value)
    closed_values = _AUDIT_CLOSED_LITERAL_VALUES.get(normalized_key)
    if closed_values is not None:
        if isinstance(value, str) and value in closed_values:
            return value
        return _audit_minimized(value)
    integer_bounds = _AUDIT_BOUNDED_INTEGER_VALUES.get(normalized_key)
    if integer_bounds is not None:
        if (isinstance(value, int) and not isinstance(value, bool) and
                integer_bounds[0] <= value <= integer_bounds[1]):
            return value
        return _audit_minimized(value)
    if depth >= 6:
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, dict):
        rows = sorted(value.items(), key=lambda row: str(row[0]))
        return {_sanitize_audit_key(child_key): _sanitize_audit_value(
                    child_value, key=str(child_key), depth=depth + 1)
                for child_key, child_value in
                rows[:_AUDIT_COLLECTION_MAX_ITEMS]}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_audit_value(item, key=normalized_key,
                                      depth=depth + 1)
                for item in list(value)[:_AUDIT_COLLECTION_MAX_ITEMS]]
    if isinstance(value, str):
        if normalized_key in _AUDIT_LITERAL_FIELDS and \
                _AUDIT_LITERAL_RE.fullmatch(value):
            return value
        return _audit_minimized(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _audit_minimized(value)


def audit_record(
        event: object, data: Mapping[object, object] | None = None, *,
        observed_at: float | None = None) -> dict[str, object]:
    """Create the one closed, minimized record accepted by every trace sink."""
    payload = dict(data or {})
    root_receipt = payload.pop("root_hygiene_receipt", None)
    root_projection = (root_hygiene_projection(root_receipt)
                       if isinstance(root_receipt, Mapping) else None)
    event_text = str(event)
    safe_event = (event_text if _AUDIT_LITERAL_RE.fullmatch(event_text)
                  else "event:" + hashlib.sha256(
                      event_text.encode("utf-8", "replace")).hexdigest()[:20])
    rec: dict[str, object] = {
        "schema": "taskplane.audit-event/v2", "event": safe_event,
        "ts": float(_time.time() if observed_at is None else observed_at),
    }
    rec.update({_sanitize_audit_key(key):
                _sanitize_audit_value(value, key=str(key))
                for key, value in payload.items()})
    if root_projection is not None:
        rec["root_hygiene"] = root_projection
    return rec

if TYPE_CHECKING or __package__:
    from .primitives import StateError, file_lock, _fsync_directory, _ensure_self_ignored
    from .storage import tp_dir
else:
    from primitives import StateError, file_lock, _fsync_directory, _ensure_self_ignored
    from storage import tp_dir


_TRACE_FAILED_WARNED = False


_TRACE_MAX_BYTES = 5 * 1024 * 1024


_TRACE_ARCHIVE_RETENTION_SECONDS = 7 * 24 * 60 * 60


_TRACE_ARCHIVE_MAX_FILES = 8


_TRACE_ARCHIVE_MAX_BYTES = 40 * 1024 * 1024


def _reserve_trace_archive(path: str) -> "str | None":
    """Claim the next unused `trace.jsonl.<n>`, atomically.

    O_CREAT|O_EXCL is the claim: two processes rotating at once cannot both
    win the same n, so neither can land on top of the other's history. The
    empty placeholder is then replaced by the real file.
    """
    n = 1
    directory = os.path.dirname(path) or "."
    prefix = os.path.basename(path) + "."
    try:
        n = max([int(name[len(prefix):]) for name in os.listdir(directory)
                 if name.startswith(prefix) and
                 name[len(prefix):].isdigit()] or [0]) + 1
    except OSError:
        pass
    while n < 100000:
        dest = f"{path}.{n}"
        try:
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            n += 1
            continue
        except OSError:
            return None
        os.close(fd)
        return dest
    return None


def _maybe_rotate_trace(path: str) -> "str | None":
    """The archive path this call created, or None if it did not rotate."""
    try:
        if os.path.getsize(path) <= _TRACE_MAX_BYTES:
            return None
        dest = _reserve_trace_archive(path)
        if dest is None:
            return None      # cannot archive without overwriting: do not
        os.replace(path, dest)
        return dest
    except OSError:
        return None


def _purge_trace_archive(path: str) -> None:
    directory = os.path.dirname(path) or "."
    staged = os.path.join(
        directory, ".privacy-purge-" + os.path.basename(path) + "-" +
        secrets.token_hex(8))
    os.replace(path, staged)
    os.unlink(staged)


def _enforce_trace_retention_locked(path: str, observed_at: float) -> dict[str, Any]:
    """Bound rotated audit history while leaving the active trace intact."""
    directory = os.path.dirname(path) or "."
    prefix = os.path.basename(path) + "."
    candidates = []
    try:
        names = os.listdir(directory)
    except OSError:
        names = []
    for name in names:
        suffix = name[len(prefix):] if name.startswith(prefix) else ""
        if not suffix.isdigit():
            continue
        archive = os.path.join(directory, name)
        try:
            info = os.lstat(archive)
            if not stat.S_ISREG(info.st_mode):
                raise OSError("audit archive is not a regular file")
            candidates.append((float(info.st_mtime), int(suffix),
                               int(info.st_size), archive))
        except OSError:
            if os.path.lexists(archive):
                _purge_trace_archive(archive)
    retained = 0
    retained_bytes = 0
    removed = 0
    for modified_at, _suffix, size, archive in sorted(
            candidates, reverse=True):
        expired = modified_at + _TRACE_ARCHIVE_RETENTION_SECONDS <= observed_at
        excess = (retained >= _TRACE_ARCHIVE_MAX_FILES or
                  retained_bytes + size > _TRACE_ARCHIVE_MAX_BYTES)
        if expired or excess:
            _purge_trace_archive(archive)
            removed += 1
        else:
            retained += 1
            retained_bytes += size
    if removed:
        _fsync_directory(directory)
    return {"removed": removed, "retained": retained,
            "retained_bytes": retained_bytes,
            "retention_seconds": _TRACE_ARCHIVE_RETENTION_SECONDS,
            "max_files": _TRACE_ARCHIVE_MAX_FILES,
            "max_bytes": _TRACE_ARCHIVE_MAX_BYTES}


def enforce_trace_retention(workspace: str, *, now: float | None = None,
                            _lock_held: bool = False) -> dict[str, Any]:
    path = os.path.join(tp_dir(workspace), "trace.jsonl")
    observed_at = float(_time.time() if now is None else now)
    if _lock_held:
        return _enforce_trace_retention_locked(path, observed_at)
    with file_lock(path + ".retention"):
        return _enforce_trace_retention_locked(path, observed_at)


def trace_paths(workspace: str) -> list[str]:
    """Retained trace files for this workspace, OLDEST first, active last.

    Rotation splits one logical audit trace across files; a consumer that
    reads only `trace.jsonl` is reading the tail of the record and cannot
    tell. Anything mining a whole track's history (retro, cost analysis)
    reads this instead.
    """
    d = tp_dir(workspace)
    base = os.path.join(d, "trace.jsonl")
    archives = []
    try:
        for name in os.listdir(d):
            if not name.startswith("trace.jsonl."):
                continue
            suffix = name[len("trace.jsonl."):]
            if suffix.isdigit():
                archives.append((int(suffix), os.path.join(d, name)))
    except OSError:
        pass
    out = [p for _n, p in sorted(archives)]
    if os.path.exists(base):
        out.append(base)
    return out


def trace(workspace: str, event: str, **data: Any) -> None:
    import time
    global _TRACE_FAILED_WARNED
    # Every record carries a monotonic wall-clock ts so the mission-control
    # feed can order events across parallel worker trace files by TIME, not
    # by which file they happened to be concatenated from.
    rec = audit_record(event, cast(Mapping[object, object], data), observed_at=time.time())
    try:
        d = tp_dir(workspace)
        os.makedirs(d, exist_ok=True)
        _ensure_self_ignored(d)
        path = os.path.join(d, "trace.jsonl")
        with file_lock(path + ".retention"):
            enforce_trace_retention(workspace, _lock_held=True)
            if os.path.islink(path):
                raise OSError("audit trace is a symlink")
            archived_to = _maybe_rotate_trace(path)
            with open(path, "a", encoding="utf-8") as f:
                if archived_to:
                    rotation = audit_record("trace_rotated", {
                        "archived_to": os.path.relpath(archived_to, workspace).replace("\\", "/"),
                        "note": "earlier events moved to bounded archive",
                    }, observed_at=time.time())
                    f.write(json.dumps(rotation, default=str) + "\n")
                f.write(json.dumps(rec, default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
            enforce_trace_retention(workspace, _lock_held=True)
    except (OSError, StateError) as e:
        # NEVER crash the hook over a broken audit log — but never go dark
        # silently either: one stderr warning per process (v2.3.0).
        if not _TRACE_FAILED_WARNED:
            _TRACE_FAILED_WARNED = True
            import sys
            print(f"taskplane: WARNING — audit trace write failed ({e}); "
                  "governance events are NO LONGER being recorded for "
                  f"{workspace}. Fix the .taskplane dir (disk/permissions) "
                  "before trusting this session's audit trail.",
                  file=sys.stderr)
        return

    # Keep the cheap status read model current from the same production event
    # path. It is presentation-only: snapshot damage or an unavailable disk
    # must never turn into authority or block the audit transition above.
    try:
        import progress
        progress.record_trace_event(
            workspace, event, rec, observed_at=rec["ts"], state_dir=d)
    except Exception:
        pass
