"""Durable, bounded, non-gating progress snapshots.

The writer may derive a compact snapshot from the ordered audit stream.  The
status reader deliberately cannot access the graph, review kernel, or loop: it
performs one bounded read of that snapshot and projects only persisted facts.
"""

from __future__ import annotations

import json
import hashlib
import os
import statistics
import tempfile
import time
from collections.abc import Mapping, Sequence
from typing import Any


EVENT_SCHEMA = "taskplane.progress-event/v1"
SNAPSHOT_SCHEMA = "taskplane.progress-snapshot/v1"
STATUS_SCHEMA = "taskplane.status-progress/v1"
STATES = frozenset(("executing", "tool-wait", "agent-wait", "human-wait",
                    "resumed", "complete", "failed", "cancelled"))
DEFAULT_MAX_BYTES = 64 * 1024
DEFAULT_ETA_MAX_AGE_SECONDS = 300.0
MAX_HISTORY = 32


def snapshot_path(workspace: str, *, state_dir: str | None = None) -> str:
    """Canonical non-authoritative progress snapshot location."""
    directory = state_dir or os.path.join(os.path.abspath(workspace),
                                          ".taskplane")
    return os.path.join(directory, "progress.json")


def _existing_snapshot(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) and value.get("schema") == \
            SNAPSHOT_SCHEMA else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _trace_state(event: str, phase: str) -> str:
    if event in {"loop_done", "loop_complete"} or phase == "done":
        return "complete"
    if event in {"loop_failed", "loop_aborted"} or phase == "failed":
        return "failed"
    if event in {"subagent_stop", "loop_gate", "loop_resume"}:
        return "resumed"
    if phase in {"design_approval", "plan_approval", "selection", "signoff",
                 "escalated"}:
        return "human-wait"
    if event in {"subagent_start", "loop_wave"}:
        return "agent-wait"
    if event in {"tool_start", "pre_tool_use"}:
        return "tool-wait"
    return "executing"


def record_trace_event(workspace: str, event: str, data: Mapping[str, Any],
                       *, observed_at: float | None = None,
                       state_dir: str | None = None) -> dict[str, Any]:
    """Increment the durable read model from the production audit path.

    The snapshot is presentation-only and deliberately best effort. The audit
    record remains authoritative; a malformed prior snapshot is replaced from
    the current event rather than blocking the governed transition.
    """
    path = snapshot_path(workspace, state_dir=state_dir)
    prior = _existing_snapshot(path)
    prior_identity = prior.get("identity") if prior else {}
    prior_active = prior.get("active") if prior else {}
    moment = float(observed_at if observed_at is not None else time.time())
    fallback = hashlib.sha256(os.path.realpath(workspace).encode("utf-8")) \
        .hexdigest()[:16]
    phase = str(data.get("step") or data.get("phase") or event)
    identity = {
        "workflow_id": str(data.get("workflow_id") or
                           prior_identity.get("workflow_id") or
                           f"workspace-{fallback}"),
        "run_id": str(data.get("run_id") or prior_identity.get("run_id") or
                      "active-loop"),
        "sequence": int(prior_identity.get("sequence") or 0) + 1,
    }
    active = {
        "owner": str(data.get("owner") or prior_active.get("owner") or
                     "taskplane"),
        "agent": str(data.get("agent_id") or data.get("agent") or
                     data.get("task") or prior_active.get("agent") or
                     "taskplane"),
        "phase": phase,
    }
    state = _trace_state(str(event), phase)
    focus_started = (prior.get("focus_started_at") if prior and
                     prior_active.get("phase") == phase and
                     prior.get("state") == state else moment)
    token_value = data.get("observed_tokens", data.get("tokens"))
    if isinstance(token_value, bool) or not isinstance(token_value, int) \
            or token_value < 0:
        token_value = prior.get("observed_tokens") if prior else None
    value = {
        "schema": SNAPSHOT_SCHEMA, "identity": identity, "active": active,
        "state": state, "focus_started_at": focus_started,
        "updated_at": moment, "observed_tokens": token_value,
        "eta_evidence": (prior.get("eta_evidence") if prior else {
            "comparable_key": None, "completed_durations": [],
            "bounded_work": None, "updated_at": moment}),
    }
    _atomic_write(path, value)
    return value


def read_workspace_status(workspace: str, *, now: float | None = None,
                          state_dir: str | None = None,
                          max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    return read_status_snapshot(
        snapshot_path(workspace, state_dir=state_dir),
        now=float(now if now is not None else time.time()), max_bytes=max_bytes)


def _unavailable(reason: str) -> dict[str, Any]:
    return {"schema": STATUS_SCHEMA, "status": "unavailable",
            "reason": str(reason), "gating": False,
            "tokens": {"status": "unavailable", "used": None},
            "eta": {"status": "unavailable", "reason": str(reason)}}


def _number(value: object, *, minimum: float = 0.0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if result >= minimum else None


def _event(row: Mapping[str, Any]) -> dict[str, Any]:
    if row.get("schema") != EVENT_SCHEMA:
        raise ValueError("unsupported progress event schema")
    sequence = row.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValueError("progress event sequence must be a positive integer")
    observed_at = _number(row.get("observed_at"))
    if observed_at is None:
        raise ValueError("progress event observed_at must be non-negative")
    state = str(row.get("state") or "")
    if state not in STATES:
        raise ValueError(f"unsupported progress state: {state or 'missing'}")
    required = ("workflow_id", "run_id", "owner", "agent", "phase")
    if any(not str(row.get(key) or "").strip() for key in required):
        raise ValueError("progress event requires workflow/run/owner/agent/phase")
    result = dict(row)
    result["observed_at"] = observed_at
    result["state"] = state
    return result


def snapshot_from_events(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reduce already-durable ordered events to one compact status snapshot."""
    rows = [_event(row) for row in events]
    if not rows:
        raise ValueError("at least one progress event is required")
    if any(left["sequence"] >= right["sequence"]
           for left, right in zip(rows, rows[1:])):
        raise ValueError("progress events must have strictly increasing sequence")
    latest = rows[-1]
    comparable_key = str(latest.get("comparable_key") or "")
    comparable = []
    if comparable_key:
        for row in rows:
            if str(row.get("comparable_key") or "") != comparable_key:
                continue
            duration = _number(row.get("completed_duration_seconds"),
                               minimum=0.000001)
            if duration is not None:
                comparable.append(duration)
    comparable = comparable[-MAX_HISTORY:]
    tokens = latest.get("observed_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        tokens = None
    focus_started = _number(latest.get("focus_started_at"))
    if focus_started is None or focus_started > latest["observed_at"]:
        focus_started = latest["observed_at"]

    bounded = None
    completed = _number(latest.get("completed_units"))
    total = _number(latest.get("total_units"), minimum=0.000001)
    unit_seconds = _number(latest.get("observed_unit_seconds"),
                           minimum=0.000001)
    if completed is not None and total is not None and unit_seconds is not None \
            and completed <= total:
        bounded = {"completed_units": completed, "total_units": total,
                   "observed_unit_seconds": unit_seconds}

    return {
        "schema": SNAPSHOT_SCHEMA,
        "identity": {"workflow_id": str(latest["workflow_id"]),
                     "run_id": str(latest["run_id"]),
                     "sequence": latest["sequence"]},
        "active": {"owner": str(latest["owner"]),
                   "agent": str(latest["agent"]),
                   "phase": str(latest["phase"])},
        "state": latest["state"],
        "focus_started_at": focus_started,
        "updated_at": latest["observed_at"],
        "observed_tokens": tokens,
        "eta_evidence": {"comparable_key": comparable_key or None,
                         "completed_durations": comparable,
                         "bounded_work": bounded,
                         "updated_at": latest["observed_at"]},
    }


def _atomic_write(path: str, value: Mapping[str, Any]) -> None:
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    if os.path.lexists(target) and os.path.islink(target):
        raise ValueError("progress snapshot path must not be a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".progress-", suffix=".json",
                                     dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_snapshot_from_events(path: str,
                               events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    snapshot = snapshot_from_events(events)
    _atomic_write(path, snapshot)
    return snapshot


def _eta(snapshot: Mapping[str, Any], *, now: float,
         max_age: float) -> dict[str, Any]:
    evidence = snapshot.get("eta_evidence")
    if not isinstance(evidence, Mapping):
        return {"status": "unavailable",
                "reason": "insufficient comparable history"}
    updated = _number(evidence.get("updated_at"))
    if updated is None or updated > now or now - updated > max_age:
        return {"status": "unavailable", "reason": "observed ETA is stale"}
    elapsed = max(0.0, now - float(snapshot["focus_started_at"]))
    bounded = evidence.get("bounded_work")
    if isinstance(bounded, Mapping):
        completed = _number(bounded.get("completed_units"))
        total = _number(bounded.get("total_units"), minimum=0.000001)
        unit = _number(bounded.get("observed_unit_seconds"), minimum=0.000001)
        if completed is not None and total is not None and unit is not None \
                and completed <= total:
            return {"status": "available",
                    "remaining_seconds": round((total - completed) * unit, 3),
                    "source": "observed:bounded-work", "confidence": "high",
                    "updated_at": updated}
    durations = evidence.get("completed_durations")
    if not isinstance(durations, list) or len(durations) < 2:
        return {"status": "unavailable",
                "reason": "insufficient comparable history"}
    observed = [_number(value, minimum=0.000001) for value in durations]
    if any(value is None for value in observed):
        return {"status": "unavailable",
                "reason": "invalid comparable history"}
    estimate = float(statistics.median(observed))
    return {"status": "available",
            "remaining_seconds": round(max(0.0, estimate - elapsed), 3),
            "source": "observed:comparable-history",
            "confidence": "medium" if len(observed) < 5 else "high",
            "updated_at": updated}


def read_status_snapshot(path: str, *, now: float,
                         max_bytes: int = DEFAULT_MAX_BYTES,
                         eta_max_age_seconds: float =
                         DEFAULT_ETA_MAX_AGE_SECONDS) -> dict[str, Any]:
    """Perform exactly one bounded snapshot read; never raise or gate work."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) \
            or max_bytes < 1024:
        return _unavailable("invalid status read bound")
    try:
        with open(path, "rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return _unavailable("progress snapshot exceeds bounded read")
        snapshot = json.loads(raw.decode("utf-8"))
        if not isinstance(snapshot, dict) or snapshot.get("schema") != \
                SNAPSHOT_SCHEMA:
            return _unavailable("invalid progress snapshot")
        identity = snapshot.get("identity")
        active = snapshot.get("active")
        if not isinstance(identity, dict) or not isinstance(active, dict):
            return _unavailable("invalid progress snapshot identity")
        state = str(snapshot.get("state") or "")
        if state not in STATES:
            return _unavailable("invalid progress snapshot state")
        focus_started = _number(snapshot.get("focus_started_at"))
        if focus_started is None:
            return _unavailable("invalid progress focus time")
        token_value = snapshot.get("observed_tokens")
        tokens = ({"status": "observed", "used": token_value}
                  if isinstance(token_value, int) and not isinstance(token_value, bool)
                  and token_value >= 0 else
                  {"status": "unavailable", "used": None})
        return {
            "schema": STATUS_SCHEMA, "status": "available", "gating": False,
            "identity": identity, "active": active, "state": state,
            "updated_at": snapshot.get("updated_at"),
            "focus_elapsed_seconds": round(max(0.0, float(now) - focus_started), 3),
            "tokens": tokens,
            "eta": _eta(snapshot, now=float(now),
                        max_age=float(eta_max_age_seconds)),
        }
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _unavailable(f"durable progress snapshot unavailable: {exc.__class__.__name__}")
