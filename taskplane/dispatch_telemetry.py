"""Native dispatch observation, token budgets, and delivery telemetry.

Codex owns agent concurrency and lifecycle. Taskplane binds observed native
dispatches to deterministic intents, aggregates provider usage, and stops only
the next dispatch when a delivery budget is reached.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

try:
    from . import delivery_policy
    from .delivery_ports import Clock, canonical_json, content_fingerprint
except ImportError:  # pragma: no cover - direct module loading
    import delivery_policy  # type: ignore
    from delivery_ports import Clock, canonical_json, content_fingerprint  # type: ignore


LEDGER_SCHEMA = "taskplane.dispatch-telemetry-ledger/v1"
RECEIPT_SCHEMA = "taskplane.dispatch-telemetry/v1"
EVENT_SCHEMA = "taskplane.dispatch-event/v1"
BUDGET_SCHEMA = "taskplane.wave-budget/v1"
DISPATCH_BINDING_SCHEMA = "taskplane.dispatch-telemetry-binding/v1"
USAGE_INTEGRITY_SCHEMA = "taskplane.dispatch-usage-integrity/v1"
DISPATCH_SCREEN_SCHEMA = "taskplane.native-dispatch-budget-screen/v1"
CYCLE_DECISION_SCHEMA = "taskplane.fix-evaluate-cycle-decision/v1"

THREAD_TYPES = frozenset({"main", "worker", "lens", "evaluator", "guardian"})
EVENT_KINDS = frozenset({
    "progress", "complete", "attention", "failed", "cancelled",
    "partial-host",
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
_BINDING_FIELDS = frozenset({
    "schema", *_DISPATCH_FIELDS, "usage", "usage_source_fingerprint",
    "usage_integrity_fingerprint", "finalized_receipt_fingerprint",
})
_STABLE_DISPATCH_IDENTITY_FIELDS = frozenset({
    "dispatch_id", "thread_id", "thread_type", "task_id", "dependencies",
    "shared_owner",
})


class DispatchTelemetryError(delivery_policy.DeliveryPolicyError):
    """Telemetry input is incomplete, contradictory, or over its bound."""


def _nonnegative_number(value: object, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DispatchTelemetryError(f"{label} must be numeric")
    if isinstance(value, float) and not math.isfinite(value):
        raise DispatchTelemetryError(f"{label} must be finite")
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


def _sha256_fingerprint(value: object, label: str) -> str:
    fingerprint = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise DispatchTelemetryError(f"{label} must be one SHA-256 fingerprint")
    return fingerprint


def _usage_integrity_fingerprint(
        ledger: Mapping[str, Any], binding: Mapping[str, Any],
        usage: Mapping[str, Any], source_fingerprint: object) -> str:
    """Bind canonical counters to their ledger and native dispatch identity."""
    identity = _identity({field: ledger.get(field) for field in _IDENTITY_FIELDS})
    dispatch = {field: binding.get(field) for field in _DISPATCH_FIELDS}
    # Validation is deliberately repeated at the integrity boundary.  A
    # retained digest cannot bless mutated dispatch identity or malformed
    # observations merely because the outer ledger still parses as JSON.
    _receipt(dispatch, {field: 0 for field in _USAGE_FIELDS})
    material = {
        "schema": USAGE_INTEGRITY_SCHEMA,
        "ledger_identity": identity,
        "dispatch": dispatch,
        "usage": _usage(usage),
        "source_fingerprint": _sha256_fingerprint(
            source_fingerprint, "usage source fingerprint"),
    }
    return content_fingerprint(material)


def _validate_receipt_integrity(row: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute one final receipt from its canonical identity and counters."""
    dispatch = {field: row.get(field) for field in _DISPATCH_FIELDS}
    usage = {field: row.get(field) for field in _USAGE_FIELDS}
    expected = _receipt(dispatch, usage)
    if any(row.get(field) != value for field, value in expected.items()):
        raise DispatchTelemetryError(
            "final dispatch usage integrity fingerprint mismatched")
    return expected


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
        "dispatches": [],
        "bindings": [],
        "evidence_head": None,
    }


def validate_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(ledger, Mapping) or ledger.get("schema") != LEDGER_SCHEMA:
        raise DispatchTelemetryError("dispatch telemetry ledger is invalid")
    _identity({field: ledger.get(field) for field in _IDENTITY_FIELDS})
    _nonnegative_number(ledger.get("started_at"), "started_at")
    _nonnegative_integer(ledger.get("revision"), "revision")
    rows = ledger.get("dispatches")
    if not isinstance(rows, list):
        raise DispatchTelemetryError(
            "dispatch telemetry rows must be a list")
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or row.get("schema") != RECEIPT_SCHEMA:
            raise DispatchTelemetryError("dispatch telemetry row is invalid")
        _validate_receipt_integrity(row)
        dispatch_id = str(row.get("dispatch_id") or "")
        if not dispatch_id or dispatch_id in ids:
            raise DispatchTelemetryError("dispatch telemetry identity is duplicated")
        ids.add(dispatch_id)
    bindings = ledger.get("bindings", [])
    if not isinstance(bindings, list):
        raise DispatchTelemetryError("dispatch telemetry bindings must be a list")
    binding_ids: set[str] = set()
    receipt_fingerprints = {
        str(row.get("fingerprint") or ""): row for row in rows
    }
    for binding in bindings:
        if not isinstance(binding, Mapping) or \
                binding.get("schema") != DISPATCH_BINDING_SCHEMA:
            raise DispatchTelemetryError("dispatch telemetry binding is invalid")
        if set(binding) != _BINDING_FIELDS:
            raise DispatchTelemetryError(
                "dispatch telemetry binding must use its closed schema")
        _receipt(
            {field: binding.get(field) for field in _DISPATCH_FIELDS},
            {field: 0 for field in _USAGE_FIELDS},
        )
        dispatch_id = str(binding.get("dispatch_id") or "")
        if not dispatch_id or dispatch_id in binding_ids:
            raise DispatchTelemetryError(
                "dispatch telemetry binding identity is duplicated")
        binding_ids.add(dispatch_id)
        usage = binding.get("usage")
        source = binding.get("usage_source_fingerprint")
        integrity = binding.get("usage_integrity_fingerprint")
        if usage is None:
            if source is not None or integrity is not None:
                raise DispatchTelemetryError(
                    "empty dispatch usage cannot retain integrity evidence")
        else:
            normalized = _usage(usage)
            expected_integrity = _usage_integrity_fingerprint(
                ledger, binding, normalized, source)
            if integrity != expected_integrity:
                raise DispatchTelemetryError(
                    "active dispatch usage integrity fingerprint mismatched")
        finalized = binding.get("finalized_receipt_fingerprint")
        if finalized is not None:
            if usage is None:
                raise DispatchTelemetryError(
                    "finalized dispatch telemetry has no bound usage")
            finalized = _sha256_fingerprint(
                finalized, "finalized receipt fingerprint")
            receipt = receipt_fingerprints.get(finalized)
            if receipt is None:
                raise DispatchTelemetryError(
                    "finalized dispatch telemetry receipt is missing")
            if receipt.get("dispatch_id") != dispatch_id:
                raise DispatchTelemetryError(
                    "finalized dispatch telemetry identity mismatched")
            if any(receipt.get(field) != binding.get(field)
                   for field in _STABLE_DISPATCH_IDENTITY_FIELDS):
                raise DispatchTelemetryError(
                    "finalized dispatch telemetry identity mismatched")
            if normalized != {field: receipt.get(field)
                              for field in _USAGE_FIELDS}:
                raise DispatchTelemetryError(
                    "finalized dispatch usage disagrees with active evidence")
    return dict(ledger)


def _usage(value: Mapping[str, Any]) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != _USAGE_FIELDS:
        raise DispatchTelemetryError(
            "observed usage requires exactly the dispatch token counters")
    normalized = {
        field: _nonnegative_integer(value.get(field), f"usage.{field}")
        for field in _USAGE_FIELDS
    }
    if normalized["cached_input_tokens"] + \
            normalized["uncached_input_tokens"] != \
            normalized["input_tokens"]:
        raise DispatchTelemetryError(
            "cached and uncached input do not reconcile")
    if normalized["total_tokens"] < normalized["input_tokens"] + \
            normalized["output_tokens"]:
        raise DispatchTelemetryError("total tokens do not reconcile")
    return normalized


def bind_dispatch(
        ledger: MutableMapping[str, Any],
        dispatch: Mapping[str, Any], *,
        usage: Mapping[str, Any] | None = None,
        source_fingerprint: str | None = None) -> dict[str, Any]:
    """Bind one observed native dispatch to its deterministic intent id.

    A host may supply its initial cumulative observation atomically with the
    binding.  A binding without that observation is retained as explicit
    incomplete evidence, but every subsequent budget screen fails closed
    until :func:`observe_usage` supplies the missing counters.
    """
    validate_ledger(ledger)
    unknown = set(dispatch).difference(_DISPATCH_FIELDS)
    missing = _DISPATCH_FIELDS.difference(dispatch)
    if unknown or missing:
        raise DispatchTelemetryError(
            "dispatch binding is closed: "
            f"missing={sorted(missing)} unknown={sorted(unknown)}")
    _receipt(dispatch, {field: 0 for field in _USAGE_FIELDS})
    material = {
        "schema": DISPATCH_BINDING_SCHEMA,
        **dict(dispatch),
        "usage": (_usage(usage) if usage is not None else None),
        "usage_source_fingerprint": (
            _sha256_fingerprint(
                source_fingerprint, "usage source fingerprint")
            if usage is not None else None
        ),
        "usage_integrity_fingerprint": None,
        "finalized_receipt_fingerprint": None,
    }
    if (usage is None) != (source_fingerprint is None):
        raise DispatchTelemetryError(
            "initial usage and its source fingerprint are required together")
    if usage is not None:
        material["usage_integrity_fingerprint"] = \
            _usage_integrity_fingerprint(
                ledger, material, material["usage"],
                material["usage_source_fingerprint"])
    bindings = ledger.setdefault("bindings", [])
    existing = next((row for row in bindings
                     if row["dispatch_id"] == material["dispatch_id"]), None)
    if existing is not None:
        identity_fields = {
            "dispatch_id", "thread_id", "thread_type", "task_id",
            "dependencies", "shared_owner",
        }
        if any(existing.get(field) != material.get(field)
               for field in identity_fields):
            raise DispatchTelemetryError("dispatch binding id collision")
        return dict(existing)
    bindings.append(material)
    ledger["revision"] = int(ledger["revision"]) + 1
    return dict(material)


def observe_usage(
        ledger: MutableMapping[str, Any], *, dispatch_id: str,
        usage: Mapping[str, Any], source_fingerprint: str) -> dict[str, Any]:
    """Persist one monotonic cumulative provider observation."""
    validate_ledger(ledger)
    binding = next((row for row in ledger.get("bindings", [])
                    if row["dispatch_id"] == str(dispatch_id)), None)
    if binding is None:
        raise DispatchTelemetryError("observed usage has no live dispatch binding")
    if binding.get("finalized_receipt_fingerprint"):
        raise DispatchTelemetryError("dispatch usage is already finalized")
    source_fingerprint = _sha256_fingerprint(
        source_fingerprint, "usage source fingerprint")
    prior_source = binding.get("usage_source_fingerprint")
    if prior_source not in (None, source_fingerprint):
        raise DispatchTelemetryError("dispatch usage source changed")
    normalized = _usage(usage)
    prior = binding.get("usage")
    if isinstance(prior, Mapping) and any(
            normalized[field] < int(prior[field]) for field in _USAGE_FIELDS):
        raise DispatchTelemetryError("observed dispatch usage moved backwards")
    binding["usage"] = normalized
    binding["usage_source_fingerprint"] = source_fingerprint
    binding["usage_integrity_fingerprint"] = _usage_integrity_fingerprint(
        ledger, binding, normalized, source_fingerprint)
    ledger["revision"] = int(ledger["revision"]) + 1
    return dict(binding)


def finalize_usage(
        ledger: MutableMapping[str, Any], *, dispatch_id: str,
        ended_at: int | float, clock: Clock,
        events: Sequence[Mapping[str, Any]] | None = None,
        evidence_store: Any = None) -> dict[str, Any]:
    """Admit the final observed counters for one bound live dispatch."""
    validate_ledger(ledger)
    binding = next((row for row in ledger.get("bindings", [])
                    if row["dispatch_id"] == str(dispatch_id)), None)
    if binding is None:
        raise DispatchTelemetryError("final usage has no live dispatch binding")
    if binding.get("usage") is None:
        raise DispatchTelemetryError("final usage has no provider observation")
    if binding.get("finalized_receipt_fingerprint"):
        receipt = next((row for row in ledger["dispatches"]
                        if row["fingerprint"] ==
                        binding["finalized_receipt_fingerprint"]), None)
        if receipt is None:
            raise DispatchTelemetryError("finalized usage receipt is missing")
        return {
            "schema": "taskplane.dispatch-telemetry-admission/v1",
            "status": "duplicate", "receipt": dict(receipt),
            "budget": budget_projection(ledger, clock),
        }
    dispatch = {
        field: binding[field] for field in _DISPATCH_FIELDS
    }
    dispatch["ended_at"] = ended_at
    if events is not None:
        dispatch["events"] = [dict(row) for row in events]
    result = admit(
        ledger, dispatch, dict(binding["usage"]), clock,
        evidence_store=evidence_store)
    if result["status"] in {"admitted", "duplicate"} and result.get("receipt"):
        binding["finalized_receipt_fingerprint"] = \
            result["receipt"]["fingerprint"]
    return result


def wave_usage(ledger: Mapping[str, Any], clock: Clock) -> dict[str, int | float]:
    """Return the exact four binding counters consumed before dispatch.

    Final receipts and current live observations both count.  A finalized
    binding is represented by its receipt only, so the same provider counters
    can never be counted twice.  Any active binding without a finite closed
    observation refuses the screen rather than becoming an invented zero.
    """
    validate_ledger(ledger)
    now = _nonnegative_number(clock.wall_time(), "clock.wall_time")
    started = float(ledger["started_at"])
    if now < started:
        raise DispatchTelemetryError("clock moved before wave start")
    dispatches = list(ledger.get("dispatches") or [])
    bindings = list(ledger.get("bindings") or [])
    receipt_dispatch_ids = {
        str(row.get("dispatch_id") or "") for row in dispatches
    }
    active_usage = []
    for binding in bindings:
        if binding.get("finalized_receipt_fingerprint") or \
                str(binding.get("dispatch_id") or "") in receipt_dispatch_ids:
            continue
        usage = binding.get("usage")
        if usage is None:
            raise DispatchTelemetryError(
                "active native usage is missing before the next dispatch")
        active_usage.append(_usage(usage))
    observed_sessions = {
        str(row.get("thread_id") or "")
        for row in [*bindings, *dispatches]
        if str(row.get("thread_id") or "")
    }
    return {
        "elapsed_seconds": now - started,
        "sessions": len(observed_sessions),
        "total_tokens": sum(int(row["total_tokens"]) for row in dispatches)
        + sum(row["total_tokens"] for row in active_usage),
        "uncached_input_tokens": sum(
            int(row["uncached_input_tokens"]) for row in dispatches
        ) + sum(row["uncached_input_tokens"] for row in active_usage),
    }



def budget_projection(ledger: Mapping[str, Any], clock: Clock, *,
                      overrides: Mapping[str, int | float] | None = None) \
        -> dict[str, Any]:
    """Project binding totals; equality at any ceiling stops new dispatch.

    ``overrides`` are conservative observation floors for deterministic tests
    and host reconciliation.  They can add missing larger truth but can never
    replace or reduce a total already observed from native dispatches.
    """
    usage = wave_usage(ledger, clock)
    if overrides is not None:
        if not isinstance(overrides, Mapping):
            raise DispatchTelemetryError("budget overrides must be a mapping")
        unknown = set(overrides).difference(WAVE_BUDGET_CEILINGS)
        if unknown:
            raise DispatchTelemetryError(
                f"unknown budget override fields: {sorted(unknown)}")
        for field, value in overrides.items():
            floor = _nonnegative_number(value, f"budget.{field}")
            usage[field] = max(usage[field], floor)
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


def _scope_review_checkpoint(
        *, reason: str, source_sha: str, current_stage: str,
        outstanding_set_fingerprint: str,
        observed_usage_fingerprint: str,
        preserved_context_fingerprint: str,
        triggered: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in triggered]
    first = rows[0] if rows else {"observed": None, "ceiling": None}
    checkpoint = {
        "schema": "taskplane.human-scope-checkpoint/v1",
        "measured_value": first.get("observed"),
        "ceiling": first.get("ceiling"),
        "reason_in_user_language": str(reason),
        "source_sha": str(source_sha),
        "current_stage": str(current_stage),
        "outstanding_set_fingerprint": str(outstanding_set_fingerprint),
        "observed_usage_fingerprint": str(observed_usage_fingerprint),
        "preserved_context_fingerprint": str(preserved_context_fingerprint),
        "triggered": rows,
        "actions": [
            {"id": "reduce-scope", "consequence":
             "close this dispatch set and authorize a separate successor wave"},
            {"id": "end-wave", "consequence":
             "record the current wave as stopped with immutable evidence"},
            {"id": "architecture-review", "consequence":
             "return to an attributed architecture or scope decision"},
        ],
        "resume_allowed": False,
    }
    checkpoint["fingerprint"] = content_fingerprint(checkpoint)
    return checkpoint


def screen_dispatch(
        ledger: Mapping[str, Any], clock: Clock, *, current_stage: str,
        outstanding_set_fingerprint: str,
        preserved_context_fingerprint: str,
        overrides: Mapping[str, int | float] | None = None) -> dict[str, Any]:
    """Return the one fail-closed decision consumed before a native start."""
    identity = validate_ledger(ledger)
    required = {
        "current_stage": current_stage,
        "outstanding_set_fingerprint": outstanding_set_fingerprint,
        "preserved_context_fingerprint": preserved_context_fingerprint,
    }
    if any(not str(value or "").strip() for value in required.values()):
        raise DispatchTelemetryError(
            "dispatch budget screen requires stage, outstanding set, and context")
    try:
        budget = budget_projection(ledger, clock, overrides=overrides)
        observed = dict(budget["usage"])
        observed_fingerprint = content_fingerprint(observed)
        reason = "binding native delivery budget reached"
    except DispatchTelemetryError as exc:
        observed = {"status": "unavailable", "reason": str(exc)}
        observed_fingerprint = content_fingerprint(observed)
        budget = {
            "schema": BUDGET_SCHEMA,
            "status": "human_scope_review",
            "dispatch_allowed": False,
            "usage": None,
            "ceilings": dict(WAVE_BUDGET_CEILINGS),
            "triggered": [{"field": "observed_usage", "observed": None,
                           "ceiling": "finite non-null host observation"}],
        }
        reason = "Native usage is missing or malformed; no new task was started."

    result = {
        "schema": DISPATCH_SCREEN_SCHEMA,
        "status": budget["status"],
        "dispatch_allowed": bool(budget["dispatch_allowed"]),
        "source_sha": identity["source_sha"],
        "current_stage": str(current_stage),
        "outstanding_set_fingerprint": str(outstanding_set_fingerprint),
        "observed_usage": observed,
        "observed_usage_fingerprint": observed_fingerprint,
        "budget": budget,
        "checkpoint": None,
    }
    if not result["dispatch_allowed"]:
        result["checkpoint"] = _scope_review_checkpoint(
            reason=reason, source_sha=identity["source_sha"],
            current_stage=str(current_stage),
            outstanding_set_fingerprint=str(outstanding_set_fingerprint),
            observed_usage_fingerprint=observed_fingerprint,
            preserved_context_fingerprint=str(preserved_context_fingerprint),
            triggered=budget["triggered"],
        )
    result["fingerprint"] = content_fingerprint(result)
    return result


def fix_evaluate_cycle_decision(
        failed_cycles: int, *, source_sha: str, task_id: str,
        current_stage: str) -> dict[str, Any]:
    """Stop the second failed Fix/Evaluate cycle for a human decision."""
    count = _nonnegative_integer(failed_cycles, "failed_cycles")
    if not all(str(value or "").strip() for value in
               (source_sha, task_id, current_stage)):
        raise DispatchTelemetryError(
            "cycle decision requires source SHA, task, and current stage")
    stopped = count >= 2
    result = {
        "schema": CYCLE_DECISION_SCHEMA,
        "status": "human_scope_review" if stopped else "within_cycle_limit",
        "dispatch_allowed": not stopped,
        "failed_cycles": count,
        "ceiling_exclusive": 2,
        "source_sha": str(source_sha),
        "task_id": str(task_id),
        "current_stage": str(current_stage),
        "decision_required": (
            "human architecture or scope decision" if stopped else None),
        "actions": (["architecture-review", "reduce-scope", "end-wave"]
                    if stopped else []),
    }
    result["fingerprint"] = content_fingerprint(result)
    return result


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
    normalized_usage = _usage(usage)
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
    """Record observed usage; the resulting budget governs the next spawn."""
    validate_ledger(ledger)
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
