"""Closed dispatch, event, budget, and delivery-metric projections.

This owner is deliberately host-neutral.  Transition adapters supply observed
usage and an injected clock; this module validates, aggregates, and persists
the resulting facts without starting workers or mutating loop authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

try:
    from . import delivery_policy, plan_topology
    from .delivery_ports import Clock, canonical_json, content_fingerprint
except ImportError:  # pragma: no cover - direct module loading
    import delivery_policy  # type: ignore
    import plan_topology  # type: ignore
    from delivery_ports import Clock, canonical_json, content_fingerprint  # type: ignore


LEDGER_SCHEMA = "taskplane.dispatch-telemetry-ledger/v1"
RECEIPT_SCHEMA = "taskplane.dispatch-telemetry/v1"
EVENT_SCHEMA = "taskplane.dispatch-event/v1"
BUDGET_SCHEMA = "taskplane.wave-budget/v1"
SCHEDULER_PROJECTION_SCHEMA = "taskplane.scheduler-progress/v1"

THREAD_TYPES = frozenset({"main", "worker", "lens", "evaluator", "guardian"})
EVENT_KINDS = frozenset({
    "progress", "complete", "attention", "failed", "cancelled",
    "partial-host",
})
TERMINAL_EVENT_KINDS = frozenset({
    "complete", "attention", "failed", "cancelled", "partial-host",
})
MAX_EVENT_BYTES = 64 * 1024
MAX_EVENTS = 256
WAVE_BUDGET_CEILINGS = {
    "elapsed_seconds": 28_800,
    "sessions": 60,
    "total_tokens": 150_000_000,
    "uncached_input_tokens": 25_000_000,
}

_IDENTITY_FIELDS = frozenset({
    "run_id", "source_sha", "design_fingerprint", "plan_fingerprint",
})
_DISPATCH_FIELDS = frozenset({
    "dispatch_id", "thread_id", "thread_type", "task_id", "dependencies",
    "shared_owner", "started_at", "ended_at", "wait_duration_seconds",
    "correction_count", "events",
})
_USAGE_FIELDS = frozenset({
    "input_tokens", "cached_input_tokens", "uncached_input_tokens",
    "output_tokens", "reasoning_tokens", "total_tokens",
})


class DispatchTelemetryError(delivery_policy.DeliveryPolicyError):
    """Telemetry input is incomplete, contradictory, or over its bound."""


def _nonnegative_number(value: object, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DispatchTelemetryError(f"{label} must be numeric")
    if value < 0:
        raise DispatchTelemetryError(f"{label} cannot be negative")
    return value


def _nonnegative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DispatchTelemetryError(f"{label} must be a non-negative integer")
    return value


def _identity(values: Mapping[str, object]) -> dict[str, str]:
    unknown = set(values).difference(_IDENTITY_FIELDS)
    missing = _IDENTITY_FIELDS.difference(values)
    if unknown or missing:
        raise DispatchTelemetryError(
            "telemetry identity requires exactly run/source/Design/Plan: "
            f"missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    identity = {field: str(values[field] or "").strip()
                for field in _IDENTITY_FIELDS}
    if any(not value for value in identity.values()):
        raise DispatchTelemetryError("telemetry identity values are required")
    return identity


def new_ledger(*, run_id: str, source_sha: str, design_fingerprint: str,
               plan_fingerprint: str, started_at: int | float) -> dict[str, Any]:
    """Create one append-only wave ledger bound to exact delivery identity."""
    identity = _identity({
        "run_id": run_id, "source_sha": source_sha,
        "design_fingerprint": design_fingerprint,
        "plan_fingerprint": plan_fingerprint,
    })
    return {
        "schema": LEDGER_SCHEMA,
        **identity,
        "started_at": _nonnegative_number(started_at, "started_at"),
        "revision": 0,
        "session_reservations": [],
        "dispatches": [],
        "evidence_head": None,
    }


def validate_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(ledger, Mapping) or ledger.get("schema") != LEDGER_SCHEMA:
        raise DispatchTelemetryError("dispatch telemetry ledger is invalid")
    _identity({field: ledger.get(field) for field in _IDENTITY_FIELDS})
    _nonnegative_number(ledger.get("started_at"), "started_at")
    _nonnegative_integer(ledger.get("revision"), "revision")
    rows = ledger.get("dispatches")
    reservations = ledger.get("session_reservations")
    if not isinstance(rows, list) or not isinstance(reservations, list):
        raise DispatchTelemetryError(
            "dispatch telemetry rows and reservations must be lists")
    reservation_ids = []
    for reservation in reservations:
        if not isinstance(reservation, Mapping) or set(reservation) != {
                "dispatch_id", "thread_type", "reserved_at"}:
            raise DispatchTelemetryError("dispatch session reservation is invalid")
        reservation_ids.append(str(reservation.get("dispatch_id") or ""))
        if str(reservation.get("thread_type") or "") not in THREAD_TYPES:
            raise DispatchTelemetryError("dispatch reservation thread type is invalid")
        _nonnegative_number(reservation.get("reserved_at"), "reserved_at")
    if any(not value for value in reservation_ids) or \
            len(set(reservation_ids)) != len(reservation_ids):
        raise DispatchTelemetryError("dispatch session reservation is duplicated")
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or row.get("schema") != RECEIPT_SCHEMA:
            raise DispatchTelemetryError("dispatch telemetry row is invalid")
        dispatch_id = str(row.get("dispatch_id") or "")
        if not dispatch_id or dispatch_id in ids:
            raise DispatchTelemetryError("dispatch telemetry identity is duplicated")
        ids.add(dispatch_id)
    return dict(ledger)


def wave_usage(ledger: Mapping[str, Any], clock: Clock) -> dict[str, int | float]:
    """Return the exact four binding counters consumed before dispatch."""
    validate_ledger(ledger)
    now = _nonnegative_number(clock.wall_time(), "clock.wall_time")
    started = float(ledger["started_at"])
    if now < started:
        raise DispatchTelemetryError("clock moved before wave start")
    dispatches = ledger.get("dispatches") or []
    return {
        "elapsed_seconds": now - started,
        "sessions": len(ledger.get("session_reservations") or []),
        "total_tokens": sum(int(row["total_tokens"]) for row in dispatches),
        "uncached_input_tokens": sum(
            int(row["uncached_input_tokens"]) for row in dispatches
        ),
    }


def reserve_session(ledger: MutableMapping[str, Any], *, dispatch_id: str,
                    thread_type: str, reserved_at: int | float) -> dict[str, Any]:
    """Atomically-accountable, idempotent pre-dispatch session reservation."""
    validate_ledger(ledger)
    dispatch_id = str(dispatch_id or "").strip()
    thread_type = str(thread_type or "").strip()
    if not dispatch_id:
        raise DispatchTelemetryError("dispatch reservation id is required")
    if thread_type not in THREAD_TYPES:
        raise DispatchTelemetryError(
            f"unknown dispatch thread type: {thread_type}")
    row = {
        "dispatch_id": dispatch_id, "thread_type": thread_type,
        "reserved_at": _nonnegative_number(reserved_at, "reserved_at"),
    }
    existing = next((value for value in ledger["session_reservations"]
                     if value["dispatch_id"] == dispatch_id), None)
    if existing is not None:
        if existing["thread_type"] != thread_type:
            raise DispatchTelemetryError("dispatch reservation id collision")
        return dict(existing)
    ledger["session_reservations"].append(row)
    ledger["revision"] = int(ledger["revision"]) + 1
    return dict(row)


def budget_projection(ledger: Mapping[str, Any], clock: Clock, *,
                      overrides: Mapping[str, int | float] | None = None) \
        -> dict[str, Any]:
    """Project binding totals; equality at any ceiling stops new dispatch."""
    usage = wave_usage(ledger, clock)
    if overrides is not None:
        if not isinstance(overrides, Mapping):
            raise DispatchTelemetryError("budget overrides must be a mapping")
        unknown = set(overrides).difference(WAVE_BUDGET_CEILINGS)
        if unknown:
            raise DispatchTelemetryError(
                f"unknown budget override fields: {sorted(unknown)}")
        for field, value in overrides.items():
            usage[field] = _nonnegative_number(value, f"budget.{field}")
    triggered = [
        {"field": field, "observed": usage[field], "ceiling": ceiling}
        for field, ceiling in WAVE_BUDGET_CEILINGS.items()
        if usage[field] >= ceiling
    ]
    return {
        "schema": BUDGET_SCHEMA,
        "status": "human_scope_review" if triggered else "within_budget",
        "dispatch_allowed": not triggered,
        "usage": usage,
        "ceilings": dict(WAVE_BUDGET_CEILINGS),
        "triggered": triggered,
    }


def dispatch_event(*, dispatch_id: str, thread_id: str, thread_type: str,
                   task_id: str, sequence: int, kind: str,
                   at: int | float, payload: Mapping[str, Any] | None = None) \
        -> dict[str, Any]:
    """Create one bounded, content-addressed worker/runtime event."""
    strings = {
        "dispatch_id": str(dispatch_id or "").strip(),
        "thread_id": str(thread_id or "").strip(),
        "task_id": str(task_id or "").strip(),
    }
    if any(not value for value in strings.values()):
        raise DispatchTelemetryError("dispatch event identity is required")
    thread_type = str(thread_type or "").strip()
    kind = str(kind or "").strip()
    if thread_type not in THREAD_TYPES:
        raise DispatchTelemetryError(f"unknown dispatch thread type: {thread_type}")
    if kind not in EVENT_KINDS:
        raise DispatchTelemetryError(f"unknown dispatch event kind: {kind}")
    material = {
        "schema": EVENT_SCHEMA,
        **strings,
        "thread_type": thread_type,
        "sequence": _nonnegative_integer(sequence, "event.sequence"),
        "kind": kind,
        "at": _nonnegative_number(at, "event.at"),
        "payload": dict(payload or {}),
    }
    raw = canonical_json(material)
    if len(raw) > MAX_EVENT_BYTES:
        raise DispatchTelemetryError("dispatch event exceeds 64 KiB")
    material["fingerprint"] = content_fingerprint(raw)
    return material


def _normalized_events(dispatch: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_events = dispatch.get("events")
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
        raise DispatchTelemetryError("dispatch events must be a list")
    if len(raw_events) > MAX_EVENTS:
        raise DispatchTelemetryError("dispatch event queue exceeds 256")
    normalized = []
    for event in raw_events:
        if not isinstance(event, Mapping):
            raise DispatchTelemetryError("dispatch event must be a mapping")
        kind = str(event.get("kind") or "")
        if kind not in EVENT_KINDS:
            raise DispatchTelemetryError(f"unknown dispatch event kind: {kind}")
        sequence = _nonnegative_integer(event.get("sequence"), "event.sequence")
        normalized.append({"kind": kind, "sequence": sequence})
    if len({row["sequence"] for row in normalized}) != len(normalized):
        raise DispatchTelemetryError("dispatch event sequence is duplicated")
    return sorted(normalized, key=lambda row: row["sequence"])


def _receipt(dispatch: Mapping[str, Any], usage: Mapping[str, Any]) -> dict[str, Any]:
    unknown_dispatch = set(dispatch).difference(_DISPATCH_FIELDS)
    missing_dispatch = _DISPATCH_FIELDS.difference(dispatch)
    unknown_usage = set(usage).difference(_USAGE_FIELDS)
    missing_usage = _USAGE_FIELDS.difference(usage)
    if unknown_dispatch or missing_dispatch or unknown_usage or missing_usage:
        raise DispatchTelemetryError(
            "dispatch telemetry is closed: "
            f"dispatch_missing={sorted(missing_dispatch)} "
            f"dispatch_unknown={sorted(unknown_dispatch)} "
            f"usage_missing={sorted(missing_usage)} "
            f"usage_unknown={sorted(unknown_usage)}"
        )
    strings = {field: str(dispatch.get(field) or "").strip()
               for field in ("dispatch_id", "thread_id", "thread_type",
                             "task_id")}
    if any(not value for value in strings.values()):
        raise DispatchTelemetryError("dispatch identity is required")
    if strings["thread_type"] not in THREAD_TYPES:
        raise DispatchTelemetryError(
            f"unknown dispatch thread type: {strings['thread_type']}")
    dependencies = dispatch.get("dependencies")
    if not isinstance(dependencies, list) or any(
            not str(value or "").strip() for value in dependencies):
        raise DispatchTelemetryError("dispatch dependencies must be task ids")
    shared_owner = dispatch.get("shared_owner")
    if shared_owner is not None and not str(shared_owner).strip():
        raise DispatchTelemetryError("dispatch shared_owner cannot be blank")
    started = _nonnegative_number(dispatch.get("started_at"), "started_at")
    ended = _nonnegative_number(dispatch.get("ended_at"), "ended_at")
    if ended < started:
        raise DispatchTelemetryError("dispatch ended before it started")
    normalized_usage = {
        field: _nonnegative_integer(usage.get(field), f"usage.{field}")
        for field in _USAGE_FIELDS
    }
    if normalized_usage["cached_input_tokens"] + \
            normalized_usage["uncached_input_tokens"] != \
            normalized_usage["input_tokens"]:
        raise DispatchTelemetryError("cached and uncached input do not reconcile")
    if normalized_usage["total_tokens"] < \
            normalized_usage["input_tokens"] + normalized_usage["output_tokens"]:
        raise DispatchTelemetryError("total tokens do not reconcile")
    material = {
        "schema": RECEIPT_SCHEMA,
        **strings,
        "dependencies": sorted(set(str(value) for value in dependencies)),
        "shared_owner": (str(shared_owner) if shared_owner is not None else None),
        **normalized_usage,
        "started_at": started,
        "ended_at": ended,
        "duration_seconds": ended - started,
        "wait_duration_seconds": _nonnegative_number(
            dispatch.get("wait_duration_seconds"), "wait_duration_seconds"),
        "correction_count": _nonnegative_integer(
            dispatch.get("correction_count"), "correction_count"),
        "events": _normalized_events(dispatch),
    }
    material["fingerprint"] = content_fingerprint(material)
    return material


def admit(ledger: MutableMapping[str, Any], dispatch: Mapping[str, Any],
          usage: Mapping[str, Any], clock: Clock, evidence_store: Any = None) \
        -> dict[str, Any]:
    """Append one exact dispatch receipt only while all budgets allow it."""
    validate_ledger(ledger)
    budget = budget_projection(ledger, clock)
    if not budget["dispatch_allowed"]:
        return {
            "schema": "taskplane.dispatch-telemetry-admission/v1",
            "status": "stop_for_human_scope_review",
            "receipt": None,
            "budget": budget,
        }
    receipt = _receipt(dispatch, usage)
    existing = next((row for row in ledger["dispatches"]
                     if row["dispatch_id"] == receipt["dispatch_id"]), None)
    if existing is not None:
        if existing != receipt:
            raise DispatchTelemetryError("dispatch id collision")
        return {
            "schema": "taskplane.dispatch-telemetry-admission/v1",
            "status": "duplicate", "receipt": dict(existing),
            "budget": budget_projection(ledger, clock),
        }
    reserve_session(
        ledger, dispatch_id=receipt["dispatch_id"],
        thread_type=receipt["thread_type"], reserved_at=receipt["started_at"])
    evidence_fingerprint = None
    if evidence_store is not None:
        prepared = evidence_store.prepare(
            "telemetry", f"dispatch-{receipt['fingerprint']}", receipt,
            expected_head=ledger.get("evidence_head"),
        )
        committed = json.loads(evidence_store.commit(prepared))
        evidence_fingerprint = committed["fingerprint"]
    if evidence_fingerprint:
        receipt["evidence_fingerprint"] = evidence_fingerprint
        ledger["evidence_head"] = evidence_fingerprint
    ledger["dispatches"].append(receipt)
    ledger["revision"] = int(ledger["revision"]) + 1
    return {
        "schema": "taskplane.dispatch-telemetry-admission/v1",
        "status": "admitted", "receipt": dict(receipt),
        "budget": budget_projection(ledger, clock),
    }


def scheduler_projection(state: Mapping[str, Any]) -> dict[str, Any]:
    """Bound the scheduler facts consumed by progress and Retro adapters."""
    if not isinstance(state, Mapping):
        raise DispatchTelemetryError("scheduler state must be a mapping")
    statuses = state.get("statuses")
    events = state.get("events")
    if not isinstance(statuses, Mapping) or not isinstance(events, list):
        raise DispatchTelemetryError("scheduler statuses/events are unavailable")
    return {
        "schema": SCHEDULER_PROJECTION_SCHEMA,
        "ready": sorted(task for task, status in statuses.items()
                        if status == "ready"),
        "held": sorted(task for task, status in statuses.items()
                       if status not in plan_topology.TERMINAL_STATUSES
                       and status not in {"ready", "in_flight"}),
        "running": sorted(task for task, status in statuses.items()
                          if status == "in_flight"),
        "attention": sorted(task for task, status in statuses.items()
                            if status == "attention"),
        "events": [dict(row) for row in events[-MAX_EVENTS:]
                   if isinstance(row, Mapping)],
        "execution_metrics": plan_topology.execution_metrics(state),
    }


def retro_projection(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact performance measures included in the Retro report."""
    return plan_topology.execution_metrics(state)
