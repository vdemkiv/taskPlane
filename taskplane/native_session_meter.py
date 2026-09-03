"""Bounded native Codex session counters and lineage.

Codex writes cumulative counters to ``token_count`` events.  Those counters,
not reconstructed message usage, are the provider-owned meter.  This module
reads only the current session metadata record and a bounded tail containing
the newest complete counter.  It never reads conversation content into the
Taskplane ledger.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from typing import Any


SNAPSHOT_SCHEMA = "taskplane.native-session-counter/v1"
AGGREGATE_SCHEMA = "taskplane.native-session-wave/v1"
ROOT_OBSERVATION_SCHEMA = "taskplane.root-session-observation/v1"
ROOT_WATERMARK_SCHEMA = "taskplane.root-session-watermark/v1"
ROOT_METER_SCHEMA = "taskplane.root-session-incremental-meter/v1"
MAX_METADATA_BYTES = 256 * 1024
MAX_COUNTER_TAIL_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
MAX_ROOT_OBSERVATIONS = 256
MAX_ROOT_WATERMARK_BYTES = 16 * 1024
_ROOT_WATERMARK_FIELDS = frozenset({
    "schema", "session_role", "session_pseudonym",
    "source_identity_fingerprint", "status_receipt_fingerprint",
    "last_sequence", "last_observation_fingerprint", "turns",
    "first_observed_input_tokens", "peak_context_tokens", "usage",
    "context_rent_tokens", "resumed", "terminal_reason", "fingerprint",
    "authenticator",
})
_AVAILABLE_ROOT_METER_FIELDS = frozenset({
    "schema", "status", "reason_code", "session_role",
    "session_pseudonym", "turns", "first_observed_input_tokens",
    "peak_context_tokens", "usage", "context_rent_tokens", "resumed",
    "status_receipt_fingerprint", "terminal_reason", "watermark",
    "fingerprint",
})


class NativeSessionMeterError(ValueError):
    """The native counter or its lineage cannot be proven safely."""


class _RootObservationError(NativeSessionMeterError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def fingerprint(value: object) -> str:
    """Return the canonical public fingerprint used by meter fixtures."""
    return _fingerprint(value)


def _nonnegative(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NativeSessionMeterError(f"{label} is not a non-negative integer")
    return value


def _session_metadata(prefix: bytes) -> tuple[dict[str, Any], bytes]:
    for raw in prefix.splitlines():
        if not raw.strip():
            continue
        if len(raw) > MAX_RECORD_BYTES:
            raise NativeSessionMeterError("native session metadata is oversized")
        try:
            row = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(row, Mapping) or row.get("type") != "session_meta":
            continue
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        session_id = str(payload.get("id") or "").strip()
        root_id = str(payload.get("session_id") or session_id).strip()
        if not session_id or not root_id:
            raise NativeSessionMeterError("native session identity is missing")
        parent = str(
            payload.get("forked_from_id")
            or payload.get("parent_thread_id")
            or ""
        ).strip() or None
        source = payload.get("source")
        thread_source = str(payload.get("thread_source") or "").strip()
        agent_path = None
        if isinstance(source, Mapping):
            subagent = source.get("subagent")
            spawn = subagent.get("thread_spawn") if isinstance(
                subagent, Mapping
            ) else None
            if isinstance(spawn, Mapping):
                agent_path = str(spawn.get("agent_path") or "").strip() or None
        metadata = {
            "session_id": session_id,
            "root_session_id": root_id,
            "parent_session_id": parent,
            "thread_source": thread_source or "unknown",
            "agent_path": agent_path,
            "started_at": str(payload.get("timestamp") or row.get(
                "timestamp") or ""),
            "resumed": isinstance(payload.get("history_base"), Mapping),
        }
        return metadata, raw
    raise NativeSessionMeterError("current native session metadata is unavailable")


def _latest_counter(tail: bytes) -> tuple[dict[str, Any], bytes]:
    # The first tail record may start mid-line.  It cannot be authenticated as
    # a complete JSON event, so discard it unless the tail begins at byte zero.
    lines = tail.splitlines()
    for raw in reversed(lines):
        if not raw.strip() or len(raw) > MAX_RECORD_BYTES:
            continue
        try:
            row = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(row, Mapping) or row.get("type") != "event_msg":
            continue
        payload = row.get("payload")
        if not isinstance(payload, Mapping) or payload.get("type") != \
                "token_count":
            continue
        info = payload.get("info")
        total = info.get("total_token_usage") if isinstance(
            info, Mapping
        ) else None
        if not isinstance(total, Mapping):
            continue
        input_tokens = _nonnegative(total.get("input_tokens"), "input_tokens")
        cached = _nonnegative(
            total.get("cached_input_tokens"), "cached_input_tokens"
        )
        output = _nonnegative(total.get("output_tokens"), "output_tokens")
        reasoning = _nonnegative(
            total.get("reasoning_output_tokens", 0),
            "reasoning_output_tokens",
        )
        total_tokens = _nonnegative(total.get("total_tokens"), "total_tokens")
        if cached > input_tokens:
            raise NativeSessionMeterError(
                "cached input exceeds native input tokens"
            )
        if total_tokens != input_tokens + output:
            raise NativeSessionMeterError(
                "native total tokens do not reconcile"
            )
        ordinal = row.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise NativeSessionMeterError("native counter ordinal is invalid")
        return {
            "ordinal": ordinal,
            "observed_at": str(row.get("timestamp") or ""),
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached,
                "uncached_input_tokens": input_tokens - cached,
                "output_tokens": output,
                "reasoning_tokens": reasoning,
                "total_tokens": total_tokens,
            },
        }, raw
    raise NativeSessionMeterError("native session has no complete token counter")


def read_snapshot(path: str) -> dict[str, Any]:
    """Read one current native session identity and cumulative counter."""
    selected = os.path.realpath(str(path or ""))
    try:
        with open(selected, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise NativeSessionMeterError(
                    "native session source is not a regular file"
                )
            prefix = stream.read(min(before.st_size, MAX_METADATA_BYTES))
            tail_offset = max(0, before.st_size - MAX_COUNTER_TAIL_BYTES)
            stream.seek(tail_offset)
            tail = stream.read(MAX_COUNTER_TAIL_BYTES + 1)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise NativeSessionMeterError(
            f"native session source is unavailable: {exc.__class__.__name__}"
        ) from exc
    if len(tail) > MAX_COUNTER_TAIL_BYTES or any(
        getattr(before, field) != getattr(after, field)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    ):
        raise NativeSessionMeterError("native session changed during metering")
    if tail_offset:
        # The bounded read begins inside an unknown record. Never attempt to
        # authenticate a parseable nested JSON fragment as a complete event.
        _partial, separator, tail = tail.partition(b"\n")
        if not separator:
            raise NativeSessionMeterError(
                "native counter tail contains no complete record")
    metadata, metadata_record = _session_metadata(prefix)
    counter, counter_record = _latest_counter(tail)
    source = {
        "path_fingerprint": hashlib.sha256(
            selected.encode("utf-8")
        ).hexdigest(),
        "device": int(before.st_dev),
        "inode": int(before.st_ino),
        "size": int(before.st_size),
        "metadata_record_sha256": hashlib.sha256(metadata_record).hexdigest(),
        "counter_record_sha256": hashlib.sha256(counter_record).hexdigest(),
    }
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        **metadata,
        **counter,
        "source": source,
    }
    snapshot["source_identity_fingerprint"] = _fingerprint({
        "session_id": metadata["session_id"],
        "path_fingerprint": source["path_fingerprint"],
        "device": source["device"],
        "inode": source["inode"],
    })
    snapshot["fingerprint"] = _fingerprint(snapshot)
    return snapshot


def validate_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a detached native session snapshot."""
    if not isinstance(value, Mapping) or value.get("schema") != SNAPSHOT_SCHEMA:
        raise NativeSessionMeterError("native session snapshot schema is invalid")
    snapshot = dict(value)
    fingerprint = snapshot.pop("fingerprint", None)
    if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(
        fingerprint
    ) or fingerprint != _fingerprint(snapshot):
        raise NativeSessionMeterError(
            "native session snapshot fingerprint is invalid"
        )
    if not str(snapshot.get("session_id") or "").strip():
        raise NativeSessionMeterError("native session identity is missing")
    if not isinstance(snapshot.get("source_identity_fingerprint"), str) or \
            _FINGERPRINT.fullmatch(
                snapshot["source_identity_fingerprint"]) is None:
        raise NativeSessionMeterError(
            "native session source identity is invalid")
    usage = snapshot.get("usage")
    if not isinstance(usage, Mapping):
        raise NativeSessionMeterError("native session usage is missing")
    normalized = {
        key: _nonnegative(usage.get(key), key)
        for key in (
            "input_tokens", "cached_input_tokens", "uncached_input_tokens",
            "output_tokens", "reasoning_tokens", "total_tokens",
        )
    }
    if normalized["cached_input_tokens"] + normalized[
        "uncached_input_tokens"
    ] != normalized["input_tokens"] or normalized["total_tokens"] != \
            normalized["input_tokens"] + normalized["output_tokens"]:
        raise NativeSessionMeterError("native session usage does not reconcile")
    return dict(value)


def derive_session_role(snapshot: Mapping[str, Any]) -> str:
    """Derive role from the host-recorded native lineage, never a label.

    A child relation or native subagent source is sufficient to prove that a
    session is not the root.  Root is accepted only when all recorded lineage
    fields agree that the session has no parent.
    """
    checked = validate_snapshot(snapshot)
    parent = checked.get("parent_session_id")
    agent_path = checked.get("agent_path")
    source = str(checked.get("thread_source") or "").strip().lower()
    child_source = source == "subagent" or source.startswith("subagent_")
    if parent is not None or agent_path is not None or child_source:
        return "worker"
    if source in {"", "unknown"}:
        raise NativeSessionMeterError(
            "native session lineage cannot prove a root role")
    return "root"


def aggregate(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Sum the latest cumulative counter from every physical segment once."""
    latest_by_source: dict[str, dict[str, Any]] = {}
    session_sources: dict[str, set[str]] = {}
    for raw in snapshots:
        row = validate_snapshot(raw)
        session_id = str(row["session_id"])
        source_id = str(row["source_identity_fingerprint"])
        session_sources.setdefault(session_id, set()).add(source_id)
        prior = latest_by_source.get(source_id)
        if prior is None:
            latest_by_source[source_id] = row
            continue
        if prior["session_id"] != session_id:
            raise NativeSessionMeterError(
                "native session source identity changed owners")
        prior_usage = prior["usage"]
        usage = row["usage"]
        prior_key = (str(prior.get("observed_at") or ""), int(prior["ordinal"]))
        row_key = (str(row.get("observed_at") or ""), int(row["ordinal"]))
        if row_key >= prior_key:
            if any(int(usage[key]) < int(prior_usage[key]) for key in usage):
                raise NativeSessionMeterError(
                    "native physical-segment counter moved backwards"
                )
            latest_by_source[source_id] = row
    ordered_segments = [latest_by_source[key]
                        for key in sorted(latest_by_source)]
    sessions: dict[str, list[dict[str, Any]]] = {}
    for row in ordered_segments:
        sessions.setdefault(str(row["session_id"]), []).append(row)
    usage_keys = (
        "input_tokens", "cached_input_tokens", "uncached_input_tokens",
        "output_tokens", "reasoning_tokens", "total_tokens",
    )
    result = {
        "schema": AGGREGATE_SCHEMA,
        "logical_sessions": len(sessions),
        "physical_segments": len(ordered_segments),
        "usage": {
            key: sum(int(row["usage"][key]) for row in ordered_segments)
            for key in usage_keys
        },
        "sessions": [
            {
                "session_id": session_id,
                "parent_session_id": rows[-1].get("parent_session_id"),
                "root_session_id": rows[-1].get("root_session_id"),
                "segments": len(rows),
                "counter_fingerprints": sorted(
                    row["fingerprint"] for row in rows),
                "total_tokens": sum(
                    int(row["usage"]["total_tokens"]) for row in rows),
            }
            for session_id, rows in sorted(sessions.items())
        ],
    }
    result["fingerprint"] = _fingerprint(result)
    return result


def _observation_authority(authority: bytes) -> bytes:
    if not isinstance(authority, bytes) or len(authority) < 16:
        raise NativeSessionMeterError(
            "root observation authority must contain at least 16 bytes")
    return authority


def _authenticator(schema: str, digest: str, authority: bytes) -> str:
    return hmac.new(
        _observation_authority(authority),
        (schema + "\0" + digest).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def seal_root_observation(
        snapshot: Mapping[str, Any], *, sequence: int, session_role: str,
        status_receipt_fingerprint: str, authority: bytes,
        terminal_reason: str | None = None) -> dict[str, Any]:
    """Seal one host-issued cumulative observation for the root meter.

    The observation carries only the already-bounded native counter snapshot
    and role/status identity.  The HMAC is an adapter boundary, not a public
    checksum that an untrusted caller can recompute.
    """
    checked = validate_snapshot(snapshot)
    derived_role = derive_session_role(checked)
    if derived_role != "root":
        raise NativeSessionMeterError(
            "native session lineage is not root")
    if session_role != derived_role:
        raise NativeSessionMeterError(
            "root meter observation role disagrees with native lineage")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or \
            sequence < 1:
        raise NativeSessionMeterError(
            "root observation sequence must be a positive integer")
    if not isinstance(status_receipt_fingerprint, str) or \
            _FINGERPRINT.fullmatch(status_receipt_fingerprint) is None:
        raise NativeSessionMeterError(
            "root observation status receipt fingerprint is invalid")
    reason = None if terminal_reason is None else str(
        terminal_reason).strip()
    if reason == "" or (reason is not None and len(reason.encode("utf-8")) > 128):
        raise NativeSessionMeterError(
            "root observation terminal reason is invalid")
    material = {
        "schema": ROOT_OBSERVATION_SCHEMA,
        "session_role": "root",
        "sequence": sequence,
        "status_receipt_fingerprint": status_receipt_fingerprint,
        "terminal_reason": reason,
        "snapshot": checked,
    }
    digest = _fingerprint(material)
    return {
        **material,
        "content_sha256": digest,
        "authenticator": _authenticator(
            ROOT_OBSERVATION_SCHEMA, digest, authority),
    }


def _validate_root_observation(
        value: Mapping[str, Any], authority: bytes) -> dict[str, Any]:
    expected = {
        "schema", "session_role", "sequence",
        "status_receipt_fingerprint", "terminal_reason", "snapshot",
        "content_sha256", "authenticator",
    }
    if not isinstance(value, Mapping) or set(value) != expected or \
            value.get("schema") != ROOT_OBSERVATION_SCHEMA:
        raise _RootObservationError(
            "authentication_failed", "root observation schema is invalid")
    material = {key: value[key] for key in expected
                if key not in {"content_sha256", "authenticator"}}
    digest = value.get("content_sha256")
    authenticator = value.get("authenticator")
    if not isinstance(digest, str) or _FINGERPRINT.fullmatch(digest) is None \
            or digest != _fingerprint(material) or \
            not isinstance(authenticator, str) or \
            _FINGERPRINT.fullmatch(authenticator) is None or \
            not hmac.compare_digest(
                authenticator, _authenticator(
                    ROOT_OBSERVATION_SCHEMA, digest, authority)):
        raise _RootObservationError(
            "authentication_failed",
            "root observation authentication is invalid")
    if value.get("session_role") != "root":
        raise _RootObservationError(
            "role_mismatch", "root observation has the wrong role")
    sequence = value.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or \
            sequence < 1:
        raise _RootObservationError(
            "observation_ambiguous", "root observation sequence is invalid")
    status = value.get("status_receipt_fingerprint")
    if not isinstance(status, str) or _FINGERPRINT.fullmatch(status) is None:
        raise _RootObservationError(
            "authentication_failed", "root status receipt is invalid")
    snapshot_value = value.get("snapshot")
    if not isinstance(snapshot_value, Mapping):
        raise _RootObservationError(
            "counter_unreconciled", "root observation snapshot is invalid")
    try:
        snapshot = validate_snapshot(snapshot_value)
    except NativeSessionMeterError as exc:
        raise _RootObservationError(
            "counter_unreconciled", str(exc)) from exc
    try:
        if derive_session_role(snapshot) != "root":
            raise _RootObservationError(
                "role_mismatch", "native session lineage is not root")
    except NativeSessionMeterError as exc:
        raise _RootObservationError("role_mismatch", str(exc)) from exc
    return {**dict(value), "snapshot": snapshot}


def _validate_watermark_shape(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema") != \
            ROOT_WATERMARK_SCHEMA or set(value) != _ROOT_WATERMARK_FIELDS:
        raise _RootObservationError(
            "watermark_invalid", "root meter watermark schema is invalid")
    material = dict(value)
    digest = material.pop("fingerprint", None)
    authenticator = material.pop("authenticator", None)
    if not isinstance(digest, str) or _FINGERPRINT.fullmatch(digest) is None \
            or digest != _fingerprint(material) or \
            not isinstance(authenticator, str) or \
            _FINGERPRINT.fullmatch(authenticator) is None:
        raise _RootObservationError(
            "watermark_invalid", "root meter watermark shape is invalid")
    if material.get("session_role") != "root" or any(
            not isinstance(material.get(field), str) or
            _FINGERPRINT.fullmatch(str(material[field])) is None
            for field in (
                "session_pseudonym", "source_identity_fingerprint",
                "status_receipt_fingerprint",
                "last_observation_fingerprint")):
        raise _RootObservationError(
            "watermark_invalid", "root meter watermark identity is invalid")
    for field in ("last_sequence", "turns", "first_observed_input_tokens",
                  "peak_context_tokens"):
        _nonnegative(material.get(field), field)
    usage = material.get("usage")
    if not isinstance(usage, Mapping):
        raise _RootObservationError(
            "watermark_invalid", "root meter watermark usage is missing")
    normalized = {
        key: _nonnegative(usage.get(key), key)
        for key in (
            "input_tokens", "cached_input_tokens", "uncached_input_tokens",
            "output_tokens", "reasoning_tokens", "total_tokens",
        )
    }
    if normalized["cached_input_tokens"] + normalized[
            "uncached_input_tokens"] != normalized["input_tokens"] or \
            normalized["total_tokens"] != normalized["input_tokens"] + \
            normalized["output_tokens"]:
        raise _RootObservationError(
            "watermark_invalid", "root meter watermark does not reconcile")
    if len(_canonical(value)) > MAX_ROOT_WATERMARK_BYTES:
        raise _RootObservationError(
            "watermark_invalid", "root meter watermark exceeds 16 KiB")
    return dict(value)


def _validate_watermark(
        value: Mapping[str, Any], authority: bytes) -> dict[str, Any]:
    checked = _validate_watermark_shape(value)
    if not hmac.compare_digest(
            str(checked["authenticator"]),
            _authenticator(
                ROOT_WATERMARK_SCHEMA, str(checked["fingerprint"]),
                authority)):
        raise _RootObservationError(
            "watermark_invalid", "root meter watermark is not authentic")
    return checked


def _unavailable(reason_code: str, reason: str) -> dict[str, Any]:
    result = {
        "schema": ROOT_METER_SCHEMA,
        "status": "unavailable",
        "reason_code": str(reason_code),
        "reason": str(reason)[:512],
    }
    result["fingerprint"] = _fingerprint(result)
    return result


def _meter_from_watermark(watermark: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "schema": ROOT_METER_SCHEMA,
        "status": "available",
        "reason_code": None,
        "session_role": "root",
        "session_pseudonym": watermark["session_pseudonym"],
        "turns": watermark["turns"],
        "first_observed_input_tokens": watermark[
            "first_observed_input_tokens"],
        "peak_context_tokens": watermark["peak_context_tokens"],
        "usage": dict(watermark["usage"]),
        "context_rent_tokens": watermark["context_rent_tokens"],
        "resumed": watermark["resumed"],
        "status_receipt_fingerprint": watermark[
            "status_receipt_fingerprint"],
        "terminal_reason": watermark["terminal_reason"],
        "watermark": dict(watermark),
    }
    result["fingerprint"] = _fingerprint(result)
    return result


def validate_root_meter_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate public meter fields against the retained watermark."""
    if not isinstance(value, Mapping) or value.get("schema") != \
            ROOT_METER_SCHEMA:
        raise NativeSessionMeterError("root meter schema is invalid")
    material = dict(value)
    digest = material.pop("fingerprint", None)
    if not isinstance(digest, str) or _FINGERPRINT.fullmatch(digest) is None \
            or digest != _fingerprint(material):
        raise NativeSessionMeterError("root meter fingerprint is invalid")
    if value.get("status") == "unavailable":
        if set(value) != {"schema", "status", "reason_code", "reason",
                          "fingerprint"}:
            raise NativeSessionMeterError(
                "unavailable root meter schema is not closed")
        if not str(value.get("reason_code") or "").strip():
            raise NativeSessionMeterError(
                "unavailable root meter requires a reason code")
        return dict(value)
    if value.get("status") != "available":
        raise NativeSessionMeterError("root meter status is invalid")
    if set(value) != _AVAILABLE_ROOT_METER_FIELDS:
        raise NativeSessionMeterError("available root meter schema is not closed")
    watermark_value = value.get("watermark")
    if not isinstance(watermark_value, Mapping):
        raise NativeSessionMeterError("root meter watermark is invalid")
    try:
        watermark = _validate_watermark_shape(watermark_value)
    except _RootObservationError as exc:
        raise NativeSessionMeterError(str(exc)) from exc
    if _meter_from_watermark(watermark) != dict(value):
        raise NativeSessionMeterError(
            "root meter disagrees with its authenticated watermark")
    return dict(value)


def validate_root_meter(
        value: Mapping[str, Any], *, authority: bytes) -> dict[str, Any]:
    """Validate a meter at its consumer boundary, including its HMAC state."""
    _observation_authority(authority)
    checked = validate_root_meter_projection(value)
    if checked.get("status") == "available":
        try:
            _validate_watermark(checked["watermark"], authority)
        except _RootObservationError as exc:
            raise NativeSessionMeterError(str(exc)) from exc
    return checked


def fold_root_observations(
        observations: Sequence[Mapping[str, Any]], *, authority: bytes,
        prior: Mapping[str, Any] | None = None,
        max_observations: int = MAX_ROOT_OBSERVATIONS) -> dict[str, Any]:
    """Reduce one bounded authenticated interval to an O(1) watermark.

    Exact replay of the last accepted observation is idempotent. Any gap,
    truncation/backwards movement, source replacement, ambiguity, oversized
    interval, or unreconciled counter becomes a typed unavailable result.
    """
    try:
        _observation_authority(authority)
        if isinstance(max_observations, bool) or not isinstance(
                max_observations, int) or max_observations < 1 or \
                max_observations > MAX_ROOT_OBSERVATIONS:
            raise _RootObservationError(
                "observation_overflow", "root observation bound is invalid")
        if not isinstance(observations, Sequence) or isinstance(
                observations, (str, bytes)):
            raise _RootObservationError(
                "observation_ambiguous", "root observations are invalid")
        if len(observations) > max_observations:
            raise _RootObservationError(
                "observation_overflow", "root observation interval overflowed")
        watermark = _validate_watermark(prior, authority) if prior is not None \
            else None
        if not observations:
            if watermark is None:
                raise _RootObservationError(
                    "observation_truncated", "root observation interval is empty")
            return _meter_from_watermark(watermark)

        state = dict(watermark or {})
        previous_usage = dict(state.get("usage") or {
            key: 0 for key in (
                "input_tokens", "cached_input_tokens",
                "uncached_input_tokens", "output_tokens",
                "reasoning_tokens", "total_tokens",
            )
        })
        last_sequence = int(state.get("last_sequence") or 0)
        last_observation = state.get("last_observation_fingerprint")
        turns = int(state.get("turns") or 0)
        first_input = state.get("first_observed_input_tokens")
        peak = int(state.get("peak_context_tokens") or 0)
        source_identity = state.get("source_identity_fingerprint")
        session_pseudonym = state.get("session_pseudonym")
        status_fingerprint = state.get("status_receipt_fingerprint")
        resumed = state.get("resumed")
        terminal_reason = state.get("terminal_reason")

        for raw in observations:
            row = _validate_root_observation(raw, authority)
            sequence = int(row["sequence"])
            observation_fingerprint = str(row["content_sha256"])
            if sequence == last_sequence:
                if observation_fingerprint == last_observation:
                    continue
                raise _RootObservationError(
                    "observation_ambiguous",
                    "root observation sequence has conflicting evidence")
            if sequence < last_sequence:
                raise _RootObservationError(
                    "observation_backwards",
                    "root observation sequence moved backwards")
            if sequence != last_sequence + 1:
                raise _RootObservationError(
                    "observation_gap", "root observation sequence has a gap")

            snapshot = row["snapshot"]
            current_source = snapshot["source_identity_fingerprint"]
            current_session = hmac.new(
                authority,
                ("root-session\0" + str(snapshot["session_id"])).encode(
                    "utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if source_identity not in (None, current_source) or \
                    session_pseudonym not in (None, current_session):
                raise _RootObservationError(
                    "source_replaced", "root observation source was replaced")
            if status_fingerprint not in (
                    None, row["status_receipt_fingerprint"]):
                raise _RootObservationError(
                    "source_replaced", "root status receipt changed")
            if resumed not in (None, snapshot["resumed"]):
                raise _RootObservationError(
                    "observation_ambiguous", "root resume status changed")
            usage = snapshot["usage"]
            if any(int(usage[key]) < int(previous_usage[key])
                   for key in previous_usage):
                raise _RootObservationError(
                    "counter_backwards", "root cumulative counter moved backwards")
            delta = {key: int(usage[key]) - int(previous_usage[key])
                     for key in previous_usage}
            if delta["total_tokens"] <= 0 or \
                    delta["total_tokens"] != delta["input_tokens"] + \
                    delta["output_tokens"] or delta[
                        "cached_input_tokens"] + delta[
                        "uncached_input_tokens"] != delta["input_tokens"]:
                raise _RootObservationError(
                    "counter_unreconciled",
                    "root observation delta is null or unreconciled")
            turns += 1
            first_input = delta["input_tokens"] if first_input is None \
                else first_input
            peak = max(peak, delta["input_tokens"])
            previous_usage = dict(usage)
            last_sequence = sequence
            last_observation = observation_fingerprint
            source_identity = current_source
            session_pseudonym = current_session
            status_fingerprint = row["status_receipt_fingerprint"]
            resumed = bool(snapshot["resumed"])
            terminal_reason = row["terminal_reason"] or terminal_reason

        if turns < 1 or not isinstance(first_input, int) or first_input <= 0:
            raise _RootObservationError(
                "counter_unreconciled", "root meter has no positive first input")
        rent = previous_usage["cached_input_tokens"] / turns
        if not math.isfinite(rent) or rent < 0:
            raise _RootObservationError(
                "counter_unreconciled", "root context rent is invalid")
        material = {
            "schema": ROOT_WATERMARK_SCHEMA,
            "session_role": "root",
            "session_pseudonym": session_pseudonym,
            "source_identity_fingerprint": source_identity,
            "status_receipt_fingerprint": status_fingerprint,
            "last_sequence": last_sequence,
            "last_observation_fingerprint": last_observation,
            "turns": turns,
            "first_observed_input_tokens": first_input,
            "peak_context_tokens": peak,
            "usage": previous_usage,
            "context_rent_tokens": rent,
            "resumed": resumed,
            "terminal_reason": terminal_reason,
        }
        digest = _fingerprint(material)
        watermark = {
            **material,
            "fingerprint": digest,
            "authenticator": _authenticator(
                ROOT_WATERMARK_SCHEMA, digest, authority),
        }
        if len(_canonical(watermark)) > MAX_ROOT_WATERMARK_BYTES:
            raise _RootObservationError(
                "observation_overflow", "root meter watermark exceeds 16 KiB")
        return _meter_from_watermark(watermark)
    except _RootObservationError as exc:
        return _unavailable(exc.reason_code, str(exc))
    except NativeSessionMeterError as exc:
        return _unavailable("authentication_failed", str(exc))
