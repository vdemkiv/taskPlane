"""Native dispatch observation, token budgets, and delivery telemetry.

Codex owns agent concurrency and lifecycle. Taskplane binds observed native
dispatches to deterministic intents, aggregates provider usage, and stops only
the next dispatch when a delivery budget is reached.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat as stat_runtime
from collections.abc import Mapping, MutableMapping, Sequence
from typing import TYPE_CHECKING, Any

# Mypy checks the package imports as the authoritative typed boundary.  At
# runtime Taskplane also supports loading this file directly from the
# ``taskplane/`` directory, where relative imports have no package context.
# Choose that compatibility mode explicitly instead of hiding the two import
# shapes behind unscoped type suppressions.
if TYPE_CHECKING or __package__:
    from .delivery_policy import DeliveryPolicyError
    from .delivery_ports import Clock, canonical_json, content_fingerprint
    from .spend import normalize_usage
else:  # pragma: no cover - direct module loading
    from delivery_policy import DeliveryPolicyError
    from delivery_ports import Clock, canonical_json, content_fingerprint
    from spend import normalize_usage


LEDGER_SCHEMA = "taskplane.dispatch-telemetry-ledger/v1"
RECEIPT_SCHEMA = "taskplane.dispatch-telemetry/v1"
EVENT_SCHEMA = "taskplane.dispatch-event/v1"
BUDGET_SCHEMA = "taskplane.wave-budget/v1"
DISPATCH_BINDING_SCHEMA = "taskplane.dispatch-telemetry-binding/v1"
USAGE_INTEGRITY_SCHEMA = "taskplane.dispatch-usage-integrity/v1"
DISPATCH_SCREEN_SCHEMA = "taskplane.native-dispatch-budget-screen/v1"
CYCLE_DECISION_SCHEMA = "taskplane.fix-evaluate-cycle-decision/v1"
TRANSCRIPT_PROJECTION_SCHEMA = "taskplane.transcript-usage-checkpoint/v1"
USAGE_CAPABILITY_SCHEMA = "taskplane.host-usage-capability/v1"
LENS_ROUTE_TELEMETRY_SCHEMA = "taskplane.lens-route-telemetry/v1"

MAX_LENS_ROUTE_REASON_BYTES = 512
MAX_LENS_ROUTE_ARTIFACT_BYTES = 128 * 1024
MAX_LENS_ROUTE_TOKENS = 150_000_000
MAX_LENS_ROUTE_RUNTIME_MS = 28_800 * 1000

# Incremental totals are useful only inside the engine instance that observed
# them.  A persisted checkpoint from another process is deliberately treated
# as an untrusted cache miss and recomputed from the bounded transcript.  This
# process-private key prevents a caller from editing totals (or another bound
# field), recomputing a public digest, and turning the edit into host-observed
# usage truth.
_TRANSCRIPT_CHECKPOINT_AUTHORITY = secrets.token_bytes(32)

MAX_TRANSCRIPT_PROJECTION_BYTES = 64 * 1024 * 1024
MAX_TRANSCRIPT_USAGE_IDENTITIES = 100_000

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
_LENS_ROUTE_TELEMETRY_FIELDS = frozenset({
    "schema", "stage", "target_pseudonym", "route_fingerprint",
    "selected_count", "lenses", "totals", "terminal_status",
    "redactions", "fingerprint",
})
_LENS_ROUTE_METRIC_FIELDS = frozenset({
    "estimated_tokens", "actual_tokens", "runtime_ms", "cache_reused",
    "invalidation_cause",
})
_LENS_ROUTE_ROW_FIELDS = frozenset({"lens", "reason"}) | \
    _LENS_ROUTE_METRIC_FIELDS
_LENS_ROUTE_TOTAL_FIELDS = frozenset({
    "estimated_tokens", "actual_tokens", "runtime_ms",
    "cache_reused_count", "invalidation_count",
})
_LENS_ROUTE_STAGES = frozenset({"product", "design", "plan"})
_LENS_ROUTE_TERMINAL_ALIASES = {
    "success": "success", "pass": "success", "passed": "success",
    "complete": "success", "completed": "success",
    "failed": "failed", "fail": "failed", "failure": "failed",
    "cancelled": "cancelled", "canceled": "cancelled",
    "interrupted": "interrupted", "interruption": "interrupted",
    "handoff": "handoff", "handed-off": "handoff",
    "handed_off": "handoff",
}
_LENS_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,127}")
_REASON_CODE = re.compile(r"[a-z0-9][a-z0-9._:-]*", re.IGNORECASE)
_REDACTED_REASON_CODE = re.compile(r"redacted-content:[0-9a-f]{64}")
_PRIVATE_REASON = re.compile(
    r"(?i)(?:\b(?:authorization|password|secret|token|api[_-]?key)\s*[=:]"
    r"|\b(?:sk|gh[opsu]|xox[baprs])-[a-z0-9_-]{8,}"
    r"|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}"
    r"|(?:^|[\s=])/(?:[^/\s]+/)+[^\s]*"
    r"|(?:^|[\s=])[a-z]:\\[^\s]+)")


class DispatchTelemetryError(DeliveryPolicyError):
    """Telemetry input is incomplete, contradictory, or over its bound."""


def _transcript_usage_row(row: Mapping[str, Any]) \
        -> tuple[dict[str, Any] | None, str | None]:
    """Return one provider usage block and its stable message identity."""
    containers = []
    for key in ("message", "response", "payload", "event"):
        value = row.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    containers.append(row)
    for container in containers:
        usage = container.get("usage")
        if not isinstance(usage, dict):
            continue
        identity = next((str(container.get(key)) for key in (
            "id", "message_id", "response_id", "request_id")
            if container.get(key) not in (None, "")), None)
        if identity is None:
            identity = next((str(row.get(key)) for key in (
                "id", "message_id", "response_id", "request_id")
                if row.get(key) not in (None, "")), None)
        return usage, identity
    return None, None


def _unavailable_transcript_projection(
        provider: str, reason: str, *, byte_limit: int,
        bytes_read: int = 0) -> dict[str, Any]:
    return {
        "schema": TRANSCRIPT_PROJECTION_SCHEMA,
        "status": "unavailable", "provider": str(provider),
        "reason": str(reason), "bytes_read": int(bytes_read),
        "byte_limit": int(byte_limit), "effective_tokens": None,
        "usage": None, "source_fingerprint": None,
    }


def _checkpoint_authority(value: bytes | None) -> bytes:
    authority = (_TRANSCRIPT_CHECKPOINT_AUTHORITY if value is None else value)
    if not isinstance(authority, bytes) or len(authority) < 32:
        raise DispatchTelemetryError(
            "transcript checkpoint authority is invalid")
    return authority


def _seal_transcript_checkpoint(
        checkpoint: Mapping[str, Any], authority: bytes) -> dict:
    """Content-address and authenticate every checkpoint field as one unit."""
    sealed = dict(checkpoint)
    sealed["authority_id"] = hashlib.sha256(authority).hexdigest()
    content_sha256 = content_fingerprint(sealed)
    sealed["content_sha256"] = content_sha256
    sealed["authenticator"] = hmac.new(
        authority,
        (TRANSCRIPT_PROJECTION_SCHEMA + "\0" + content_sha256).encode(
            "utf-8"), hashlib.sha256).hexdigest()
    return sealed


def _authorized_transcript_checkpoint(
        checkpoint: Mapping[str, Any], authority: bytes) -> bool:
    """Accept only an intact checkpoint minted by this engine instance."""
    try:
        candidate = dict(checkpoint)
        authenticator = candidate.pop("authenticator")
        content_sha256 = candidate.pop("content_sha256")
    except (KeyError, TypeError, ValueError):
        return False
    if not isinstance(content_sha256, str) or re.fullmatch(
            r"[0-9a-f]{64}", content_sha256) is None or \
            not isinstance(authenticator, str) or re.fullmatch(
                r"[0-9a-f]{64}", authenticator) is None:
        return False
    if candidate.get("authority_id") != hashlib.sha256(
            authority).hexdigest() or not hmac.compare_digest(
                content_fingerprint(candidate), content_sha256):
        return False
    expected = hmac.new(
        authority,
        (TRANSCRIPT_PROJECTION_SCHEMA + "\0" + content_sha256).encode(
            "utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(authenticator, expected)


def project_transcript_usage(
        path: str, *, provider: str,
        checkpoint: Mapping[str, Any] | None = None,
        checkpoint_authority: bytes | None = None,
        byte_limit: int = MAX_TRANSCRIPT_PROJECTION_BYTES
        ) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Project one selected transcript with a content-bound checkpoint.

    Every call reads at most ``byte_limit`` bytes and authenticates the entire
    previously consumed prefix before reusing totals.  Stable path/device/
    inode/size/timestamps alone are never treated as proof that an earlier
    prefix survived a same-inode rewrite.
    """
    if isinstance(byte_limit, bool) or not isinstance(byte_limit, int) or \
            byte_limit <= 0 or byte_limit > MAX_TRANSCRIPT_PROJECTION_BYTES:
        raise DispatchTelemetryError(
            "transcript projection byte limit is invalid")
    authority = _checkpoint_authority(checkpoint_authority)
    selected = os.path.realpath(str(path or ""))
    try:
        with open(selected, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat_runtime.S_ISREG(before.st_mode):
                return _unavailable_transcript_projection(
                    provider, "selected transcript is not a regular file",
                    byte_limit=byte_limit), None
            if before.st_size > byte_limit:
                return _unavailable_transcript_projection(
                    provider, "selected transcript exceeds the byte cap",
                    byte_limit=byte_limit), None
            payload = stream.read(before.st_size + 1)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        return _unavailable_transcript_projection(
            provider, exc.__class__.__name__, byte_limit=byte_limit), None
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns",
                     "st_ctime_ns")
    if len(payload) != before.st_size or any(
            getattr(before, field) != getattr(after, field)
            for field in stable_fields):
        return _unavailable_transcript_projection(
            provider, "selected transcript changed during projection",
            byte_limit=byte_limit, bytes_read=len(payload)), None

    path_fingerprint = hashlib.sha256(selected.encode("utf-8")).hexdigest()
    prior = dict(checkpoint) if isinstance(checkpoint, Mapping) else {}
    reusable_shape = _authorized_transcript_checkpoint(prior, authority) and \
        prior.get("schema") == TRANSCRIPT_PROJECTION_SCHEMA and \
        prior.get("path_fingerprint") == path_fingerprint and \
        prior.get("provider") == str(provider) and \
        prior.get("device") == int(before.st_dev) and \
        prior.get("inode") == int(before.st_ino) and \
        isinstance(prior.get("offset"), int) and \
        not isinstance(prior.get("offset"), bool) and \
        0 <= int(prior["offset"]) <= before.st_size and \
        isinstance(prior.get("size"), int) and \
        not isinstance(prior.get("size"), bool) and \
        0 <= int(prior["size"]) <= before.st_size and \
        isinstance(prior.get("mtime_ns"), int) and \
        not isinstance(prior.get("mtime_ns"), bool) and \
        isinstance(prior.get("ctime_ns"), int) and \
        not isinstance(prior.get("ctime_ns"), bool) and \
        isinstance(prior.get("consumed_prefix_sha256"), str) and \
        re.fullmatch(r"[0-9a-f]{64}",
                     str(prior.get("consumed_prefix_sha256"))) is not None and \
        isinstance(prior.get("totals"), Mapping) and \
        isinstance(prior.get("seen_identity_hashes"), list) and \
        len(prior["seen_identity_hashes"]) <= \
        MAX_TRANSCRIPT_USAGE_IDENTITIES and all(
            isinstance(value, str) and
            re.fullmatch(r"[0-9a-f]{64}", value)
            for value in prior["seen_identity_hashes"])
    reusable = False
    if reusable_shape:
        candidate_offset = int(prior["offset"])
        prefix_digest = hashlib.sha256(
            payload[:candidate_offset]).hexdigest()
        size_progress = int(prior["size"]) <= before.st_size
        timestamp_progress = (
            int(before.st_mtime_ns) >= int(prior["mtime_ns"]) and
            int(before.st_ctime_ns) >= int(prior["ctime_ns"]))
        unchanged_metadata = (
            int(prior["size"]) != before.st_size or
            (int(before.st_mtime_ns) == int(prior["mtime_ns"]) and
             int(before.st_ctime_ns) == int(prior["ctime_ns"])))
        empty_regrowth = candidate_offset == 0 and int(
            prior["size"]) != before.st_size
        reusable = bool(
            size_progress and timestamp_progress and unchanged_metadata and
            not empty_regrowth and
            prefix_digest == prior["consumed_prefix_sha256"])
    if reusable:
        offset = int(prior["offset"])
        try:
            totals = {key: _nonnegative_integer(
                prior["totals"].get(key, 0), f"checkpoint.{key}")
                for key in (
                    "uncached_input_tokens", "cached_input_tokens",
                    "cache_creation_tokens", "output_tokens",
                    "reasoning_tokens", "raw_total_tokens",
                    "effective_tokens", "messages", "duplicates_removed")}
        except DispatchTelemetryError:
            return _unavailable_transcript_projection(
                provider, "transcript usage checkpoint is malformed",
                byte_limit=byte_limit), None
        seen = set(str(value) for value in prior["seen_identity_hashes"])
    else:
        offset = 0
        totals = {key: 0 for key in (
            "uncached_input_tokens", "cached_input_tokens",
            "cache_creation_tokens", "output_tokens", "reasoning_tokens",
            "raw_total_tokens", "effective_tokens", "messages",
            "duplicates_removed")}
        seen: set[str] = set()

    appended = payload[offset:]

    complete_end = appended.rfind(b"\n") + 1
    complete = appended[:complete_end]
    for raw in complete.splitlines():
        if len(raw) > 2 * 1024 * 1024:
            return _unavailable_transcript_projection(
                provider, "selected transcript record exceeds the byte cap",
                byte_limit=byte_limit, bytes_read=len(payload)), None
        try:
            row = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError):
            return _unavailable_transcript_projection(
                provider, "selected transcript has a malformed complete record",
                byte_limit=byte_limit, bytes_read=len(payload)), None
        if not isinstance(row, Mapping):
            continue
        usage, identity = _transcript_usage_row(row)
        if usage is None:
            continue
        identity_fingerprint = hashlib.sha256(
            identity.encode("utf-8")).hexdigest() if identity else None
        if identity_fingerprint and identity_fingerprint in seen:
            totals["duplicates_removed"] += 1
            continue
        normalized = normalize_usage(usage, provider=str(provider))
        if normalized.get("available") is not True:
            return _unavailable_transcript_projection(
                provider, str(normalized.get("reason") or
                              "provider usage is unavailable"),
                byte_limit=byte_limit, bytes_read=len(payload)), None
        if identity_fingerprint:
            if len(seen) >= MAX_TRANSCRIPT_USAGE_IDENTITIES:
                return _unavailable_transcript_projection(
                    provider, "selected transcript identity cap exceeded",
                    byte_limit=byte_limit, bytes_read=len(payload)), None
            seen.add(identity_fingerprint)
        for key in ("uncached_input_tokens", "cached_input_tokens",
                    "cache_creation_tokens", "output_tokens",
                    "reasoning_tokens", "raw_total_tokens",
                    "effective_tokens"):
            totals[key] += int(normalized.get(key, 0))
        totals["messages"] += 1

    next_offset = offset + complete_end
    consumed_prefix_sha256 = hashlib.sha256(
        payload[:next_offset]).hexdigest()
    checkpoint_row = _seal_transcript_checkpoint({
        "schema": TRANSCRIPT_PROJECTION_SCHEMA,
        "path_fingerprint": path_fingerprint, "provider": str(provider),
        "device": int(before.st_dev), "inode": int(before.st_ino),
        "mode": int(before.st_mode), "size": int(before.st_size),
        "mtime_ns": int(before.st_mtime_ns),
        "ctime_ns": int(before.st_ctime_ns),
        "offset": int(next_offset),
        "consumed_prefix_sha256": consumed_prefix_sha256,
        "totals": totals, "seen_identity_hashes": sorted(seen),
    }, authority)
    usage = {
        "input_tokens": totals["cached_input_tokens"] +
        totals["uncached_input_tokens"],
        "cached_input_tokens": totals["cached_input_tokens"],
        "uncached_input_tokens": totals["uncached_input_tokens"],
        "output_tokens": totals["output_tokens"],
        "reasoning_tokens": totals["reasoning_tokens"],
        "total_tokens": totals["raw_total_tokens"],
    }
    if totals["messages"] == 0:
        return _unavailable_transcript_projection(
            provider, "selected transcript has no provider usage totals",
            byte_limit=byte_limit, bytes_read=len(payload)), checkpoint_row
    source_fingerprint = content_fingerprint({
        "schema": TRANSCRIPT_PROJECTION_SCHEMA,
        "path_fingerprint": path_fingerprint,
        "device": int(before.st_dev), "inode": int(before.st_ino),
        "offset": int(next_offset),
        "consumed_prefix_sha256": consumed_prefix_sha256,
        "usage": usage,
    })
    return {
        "schema": TRANSCRIPT_PROJECTION_SCHEMA, "status": "available",
        "provider": str(provider), "reason": None,
        "path_fingerprint": path_fingerprint,
        "device": int(before.st_dev), "inode": int(before.st_ino),
        "offset": int(next_offset), "size": int(before.st_size),
        "bytes_read": len(payload), "byte_limit": int(byte_limit),
        "messages": totals["messages"],
        "duplicates_removed": totals["duplicates_removed"],
        "effective_tokens": totals["effective_tokens"],
        "usage": usage, "source_fingerprint": source_fingerprint,
    }, checkpoint_row


def usage_capability(usage: Mapping[str, Any] | None, *,
                     reason: str | None = None) -> dict[str, Any]:
    """Describe token-budget truth without turning absence into enforcement."""
    if not isinstance(usage, Mapping):
        return {
            "schema": USAGE_CAPABILITY_SCHEMA, "status": "unavailable",
            "budget_claim": False, "enforcement": "not-enforced",
            "observed_tokens": None,
            "reason": str(reason or "host token totals are unavailable"),
        }
    normalized = _usage(usage)
    return {
        "schema": USAGE_CAPABILITY_SCHEMA, "status": "available",
        "budget_claim": True, "enforcement": "host-observed",
        "observed_tokens": normalized["total_tokens"], "reason": None,
    }


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


def _route_counter(value: object, label: str, maximum: int) -> int:
    normalized = _nonnegative_integer(value, label)
    if normalized > maximum:
        kind = "token" if "token" in label else "runtime"
        raise DispatchTelemetryError(
            f"{label} exceeds the lens-route {kind} bound")
    return normalized


def _lens_id(value: object, label: str = "lens") -> str:
    if not isinstance(value, str):
        raise DispatchTelemetryError(
            f"{label} must be a bounded lowercase lens id")
    normalized = value.strip()
    if _LENS_ID.fullmatch(normalized) is None:
        raise DispatchTelemetryError(
            f"{label} must be a bounded lowercase lens id")
    return normalized


def _bounded_reason_code(
        value: object, label: str, *, persisted: bool = False,
) -> tuple[str, int]:
    """Return one reason code without retaining private content."""
    if not isinstance(value, str) or not value.strip():
        raise DispatchTelemetryError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if persisted and _REDACTED_REASON_CODE.fullmatch(normalized) is not None:
        return normalized, 0
    encoded = normalized.encode("utf-8")
    safe = len(encoded) <= MAX_LENS_ROUTE_REASON_BYTES and \
        _REASON_CODE.fullmatch(normalized) is not None and \
        _PRIVATE_REASON.search(normalized) is None and \
        not normalized.lower().startswith("redacted-content:")
    if safe:
        return normalized, 0
    digest = hashlib.sha256(
        ("taskplane.lens-route-reason/v1\0" + normalized).encode(
            "utf-8")).hexdigest()
    return f"redacted-content:{digest}", 1


def _canonical_route_status(value: object) -> str:
    normalized = str(value or "").strip().lower()
    status = _LENS_ROUTE_TERMINAL_ALIASES.get(normalized)
    if status is None:
        raise DispatchTelemetryError(
            "lens-route terminal status must be success, failed, cancelled, "
            "interrupted, or handoff")
    return status


def _route_selection(route: Mapping[str, Any]) \
        -> tuple[str, str, list[str], dict[str, Mapping[str, Any]]]:
    if not isinstance(route, Mapping) or route.get("schema") != \
            "taskplane.lens-route-policy/v1":
        raise DispatchTelemetryError(
            "lens-route telemetry requires a focused route decision")
    stage = str(route.get("stage") or "")
    if stage not in _LENS_ROUTE_STAGES:
        raise DispatchTelemetryError("lens-route telemetry stage is invalid")
    route_fingerprint = _sha256_fingerprint(
        route.get("route_fingerprint"), "route fingerprint")
    raw_selected = route.get(
        "dispatchable_selected", route.get("selected"))
    if not isinstance(raw_selected, list):
        raise DispatchTelemetryError(
            "lens-route selected lenses must be a list")
    selected = [_lens_id(value, "selected lens") for value in raw_selected]
    if len(selected) > 26 or len(set(selected)) != len(selected):
        raise DispatchTelemetryError(
            "lens-route selected lenses must be unique and bounded")
    dispositions = route.get("dispositions")
    if not isinstance(dispositions, list):
        raise DispatchTelemetryError(
            "lens-route dispositions must be a list")
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in dispositions:
        if not isinstance(row, Mapping):
            raise DispatchTelemetryError(
                "lens-route disposition must be a mapping")
        lens = _lens_id(row.get("lens"), "disposition lens")
        if lens in indexed:
            raise DispatchTelemetryError(
                "lens-route disposition lens is duplicated")
        indexed[lens] = row
    if any(
            lens not in indexed or indexed[lens].get("disposition") not in
            {"execute_deep", "execute_light"}
            for lens in selected):
        raise DispatchTelemetryError(
            "lens-route selected dispositions are incomplete or invalid")
    return stage, route_fingerprint, selected, indexed


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


def build_lens_route_telemetry(
        route: Mapping[str, Any], *, target: str, terminal_status: str,
        lens_metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Build one closed, bounded, privacy-safe terminal route receipt."""
    stage, route_fingerprint, selected, dispositions = _route_selection(route)
    if not isinstance(target, str) or not target.strip():
        raise DispatchTelemetryError(
            "lens-route telemetry target is required")
    if len(target.encode("utf-8")) > 64 * 1024:
        raise DispatchTelemetryError(
            "lens-route telemetry target exceeds the input bound")
    if not isinstance(lens_metrics, Mapping) or \
            any(not isinstance(key, str) for key in lens_metrics) or \
            set(lens_metrics) != set(selected):
        raise DispatchTelemetryError(
            "lens-route metrics must name exactly selected lenses")

    rows: list[dict[str, Any]] = []
    redactions = 0
    for lens in selected:
        metric = lens_metrics[lens]
        if not isinstance(metric, Mapping) or \
                set(metric) != _LENS_ROUTE_METRIC_FIELDS:
            raise DispatchTelemetryError(
                f"lens-route metric for {lens} must use its closed schema")
        reason, reason_redactions = _bounded_reason_code(
            dispositions[lens].get("reason"), f"lens {lens} reason")
        cause_value = metric.get("invalidation_cause")
        if cause_value is None:
            cause = None
            cause_redactions = 0
        else:
            cause, cause_redactions = _bounded_reason_code(
                cause_value, f"lens {lens} invalidation cause")
        estimated = _route_counter(
            metric.get("estimated_tokens"),
            f"lens {lens} estimated_tokens", MAX_LENS_ROUTE_TOKENS)
        actual = _route_counter(
            metric.get("actual_tokens"),
            f"lens {lens} actual_tokens", MAX_LENS_ROUTE_TOKENS)
        runtime_ms = _route_counter(
            metric.get("runtime_ms"),
            f"lens {lens} runtime_ms", MAX_LENS_ROUTE_RUNTIME_MS)
        reused = metric.get("cache_reused")
        if not isinstance(reused, bool):
            raise DispatchTelemetryError(
                f"lens {lens} cache_reused must be a boolean")
        if reused and actual:
            raise DispatchTelemetryError(
                "reused lens cannot record actual tokens")
        if reused and cause is not None:
            raise DispatchTelemetryError(
                "reused lens cannot record invalidation")
        rows.append({
            "lens": lens, "reason": reason,
            "estimated_tokens": estimated, "actual_tokens": actual,
            "runtime_ms": runtime_ms, "cache_reused": reused,
            "invalidation_cause": cause,
        })
        redactions += reason_redactions + cause_redactions

    totals = {
        "estimated_tokens": sum(row["estimated_tokens"] for row in rows),
        "actual_tokens": sum(row["actual_tokens"] for row in rows),
        "runtime_ms": sum(row["runtime_ms"] for row in rows),
        "cache_reused_count": sum(
            1 for row in rows if row["cache_reused"]),
        "invalidation_count": sum(
            1 for row in rows if row["invalidation_cause"] is not None),
    }
    if totals["estimated_tokens"] > MAX_LENS_ROUTE_TOKENS or \
            totals["actual_tokens"] > MAX_LENS_ROUTE_TOKENS:
        raise DispatchTelemetryError(
            "lens-route totals exceed the token bound")
    if totals["runtime_ms"] > MAX_LENS_ROUTE_RUNTIME_MS:
        raise DispatchTelemetryError(
            "lens-route totals exceed the runtime bound")
    material: dict[str, Any] = {
        "schema": LENS_ROUTE_TELEMETRY_SCHEMA,
        "stage": stage,
        "target_pseudonym": hashlib.sha256(
            ("taskplane.lens-route-target/v1\0" + target.strip()).encode(
                "utf-8")).hexdigest(),
        "route_fingerprint": route_fingerprint,
        "selected_count": len(selected),
        "lenses": rows,
        "totals": totals,
        "terminal_status": _canonical_route_status(terminal_status),
        "redactions": redactions,
    }
    material["fingerprint"] = content_fingerprint(material)
    if len(canonical_json(material)) > MAX_LENS_ROUTE_ARTIFACT_BYTES:
        raise DispatchTelemetryError(
            "lens-route telemetry artifact exceeds 128 KiB")
    return validate_lens_route_telemetry(material)


def validate_lens_route_telemetry(
        record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an untrusted terminal route receipt without private inputs."""
    if not isinstance(record, Mapping) or \
            record.get("schema") != LENS_ROUTE_TELEMETRY_SCHEMA or \
            set(record) != _LENS_ROUTE_TELEMETRY_FIELDS:
        raise DispatchTelemetryError(
            "lens-route telemetry must use its closed schema")
    fingerprint_value = _sha256_fingerprint(
        record.get("fingerprint"), "lens-route telemetry fingerprint")
    material = {key: value for key, value in record.items()
                if key != "fingerprint"}
    if not hmac.compare_digest(
            content_fingerprint(material), fingerprint_value):
        raise DispatchTelemetryError(
            "lens-route telemetry fingerprint mismatched")
    if record.get("stage") not in _LENS_ROUTE_STAGES:
        raise DispatchTelemetryError("lens-route telemetry stage is invalid")
    _sha256_fingerprint(
        record.get("target_pseudonym"), "target pseudonym")
    _sha256_fingerprint(
        record.get("route_fingerprint"), "route fingerprint")
    if record.get("terminal_status") not in set(
            _LENS_ROUTE_TERMINAL_ALIASES.values()):
        raise DispatchTelemetryError(
            "lens-route telemetry terminal status is invalid")
    rows = record.get("lenses")
    if not isinstance(rows, list):
        raise DispatchTelemetryError(
            "lens-route telemetry lenses must be a list")
    selected_count = _nonnegative_integer(
        record.get("selected_count"), "selected_count")
    if selected_count != len(rows) or selected_count > 26:
        raise DispatchTelemetryError(
            "lens-route selected count mismatched")
    normalized_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    redaction_count = 0
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != _LENS_ROUTE_ROW_FIELDS:
            raise DispatchTelemetryError(
                "lens-route telemetry row must use its closed schema")
        lens = _lens_id(row.get("lens"))
        if lens in seen:
            raise DispatchTelemetryError(
                "lens-route telemetry lens is duplicated")
        seen.add(lens)
        reason, changed = _bounded_reason_code(
            row.get("reason"), f"lens {lens} reason", persisted=True)
        if changed or reason != row.get("reason"):
            raise DispatchTelemetryError(
                "persisted lens-route reason is not privacy-safe")
        cause_value = row.get("invalidation_cause")
        if cause_value is None:
            cause = None
        else:
            cause, changed = _bounded_reason_code(
                cause_value, f"lens {lens} invalidation cause",
                persisted=True)
            if changed or cause != cause_value:
                raise DispatchTelemetryError(
                    "persisted invalidation cause is not privacy-safe")
        estimated = _route_counter(
            row.get("estimated_tokens"),
            f"lens {lens} estimated_tokens", MAX_LENS_ROUTE_TOKENS)
        actual = _route_counter(
            row.get("actual_tokens"),
            f"lens {lens} actual_tokens", MAX_LENS_ROUTE_TOKENS)
        runtime_ms = _route_counter(
            row.get("runtime_ms"),
            f"lens {lens} runtime_ms", MAX_LENS_ROUTE_RUNTIME_MS)
        reused = row.get("cache_reused")
        if not isinstance(reused, bool):
            raise DispatchTelemetryError(
                f"lens {lens} cache_reused must be a boolean")
        if reused and actual:
            raise DispatchTelemetryError(
                "reused lens cannot record actual tokens")
        if reused and cause is not None:
            raise DispatchTelemetryError(
                "reused lens cannot record invalidation")
        normalized_rows.append({
            "lens": lens, "reason": reason,
            "estimated_tokens": estimated, "actual_tokens": actual,
            "runtime_ms": runtime_ms, "cache_reused": reused,
            "invalidation_cause": cause,
        })
        redaction_count += int(
            _REDACTED_REASON_CODE.fullmatch(reason) is not None)
        redaction_count += int(isinstance(cause, str) and
                               _REDACTED_REASON_CODE.fullmatch(cause)
                               is not None)

    expected_totals = {
        "estimated_tokens": sum(
            row["estimated_tokens"] for row in normalized_rows),
        "actual_tokens": sum(
            row["actual_tokens"] for row in normalized_rows),
        "runtime_ms": sum(row["runtime_ms"] for row in normalized_rows),
        "cache_reused_count": sum(
            1 for row in normalized_rows if row["cache_reused"]),
        "invalidation_count": sum(
            1 for row in normalized_rows
            if row["invalidation_cause"] is not None),
    }
    totals = record.get("totals")
    if not isinstance(totals, Mapping) or \
            set(totals) != _LENS_ROUTE_TOTAL_FIELDS or \
            dict(totals) != expected_totals:
        raise DispatchTelemetryError(
            "lens-route telemetry totals mismatched")
    redactions = _nonnegative_integer(
        record.get("redactions"), "redactions")
    if redactions != redaction_count:
        raise DispatchTelemetryError(
            "lens-route telemetry redaction count mismatched")
    if len(canonical_json(record)) > MAX_LENS_ROUTE_ARTIFACT_BYTES:
        raise DispatchTelemetryError(
            "lens-route telemetry artifact exceeds 128 KiB")
    return dict(record)


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


def ledger_usage_capability(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Project whether this ledger has any real host-token observation."""
    validate_ledger(ledger)
    finalized_ids = {
        str(row.get("dispatch_id") or "")
        for row in ledger.get("dispatches") or []
    }
    rows = [
        {field: row.get(field) for field in _USAGE_FIELDS}
        for row in ledger.get("dispatches") or []
    ]
    rows.extend(
        dict(binding["usage"])
        for binding in ledger.get("bindings") or []
        if binding.get("usage") is not None and
        not binding.get("finalized_receipt_fingerprint") and
        str(binding.get("dispatch_id") or "") not in finalized_ids
    )
    if not rows:
        return usage_capability(
            None, reason="no host token totals have been observed")
    normalized = [_usage(row) for row in rows]
    return usage_capability({
        field: sum(row[field] for row in normalized)
        for field in _USAGE_FIELDS
    })



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
    capability = ledger_usage_capability(ledger)
    triggered = [
        {"field": field, "observed": usage[field], "ceiling": ceiling}
        for field, ceiling in WAVE_BUDGET_CEILINGS.items()
        if usage[field] >= ceiling
    ]
    return {
        "schema": BUDGET_SCHEMA,
        "status": "human_scope_review" if triggered else "within_budget",
        "dispatch_allowed": not triggered,
        "budget_claim": bool(capability["budget_claim"]),
        "measurement_status": capability["status"],
        "usage_capability": capability,
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
        capability = dict(budget["usage_capability"])
        reason = "binding native delivery budget reached"
    except DispatchTelemetryError as exc:
        observed = {"status": "unavailable", "reason": str(exc)}
        observed_fingerprint = content_fingerprint(observed)
        capability = usage_capability(None, reason=str(exc))
        budget = {
            "schema": BUDGET_SCHEMA,
            "status": "human_scope_review",
            "dispatch_allowed": False,
            "usage": None,
            "budget_claim": False,
            "measurement_status": "unavailable",
            "usage_capability": capability,
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
        "usage_capability": capability,
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
