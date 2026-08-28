"""Closed, bounded projections for append-only audit traces.

This stdlib-only leaf owns the privacy-safe record shape. The enforcement
kernel retains ownership of trace persistence, rotation, and retention.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
import time as _time


_AUDIT_TEXT_MAX_CHARS = 2048
_AUDIT_COLLECTION_MAX_ITEMS = 64
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
                for key, value in (data or {}).items()})
    return rec
