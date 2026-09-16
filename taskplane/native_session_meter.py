"""Read native Codex counters and lineage without retaining conversation content."""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
if __package__:
    from . import primitives as _package_primitives
    _json_primitives = _package_primitives
else:
    import primitives as _flat_primitives
    _json_primitives = _flat_primitives


SNAPSHOT_SCHEMA = "taskplane.native-session-counter/v1"

AGGREGATE_SCHEMA = "taskplane.native-session-wave/v1"

MAX_METADATA_BYTES = 256 * 1024

MAX_COUNTER_TAIL_BYTES = 4 * 1024 * 1024

MAX_RECORD_BYTES = 2 * 1024 * 1024

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")

class NativeSessionMeterError(ValueError):
    """The native counter or its lineage cannot be proven safely."""

def _canonical(value: object) -> bytes:
    return _json_primitives.canonical_bytes(value, ensure_ascii=True)

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
        history = payload.get("history_base")
        if history is not None and (
            not isinstance(history, Mapping)
            or history.get("thread_id") != session_id
            or any(
                type(history.get(field)) is not int or history[field] < 0
                for field in ("end_ordinal_exclusive", "end_byte_offset")
            )
        ):
            raise NativeSessionMeterError("native restart identity is invalid")
        parent = (
            str(payload.get("forked_from_id") or payload.get("parent_thread_id") or "").strip()
            or None
        )
        source = payload.get("source")
        thread_source = str(payload.get("thread_source") or "").strip()
        agent_path = None
        if isinstance(source, Mapping):
            subagent = source.get("subagent")
            spawn = subagent.get("thread_spawn") if isinstance(subagent, Mapping) else None
            if isinstance(spawn, Mapping):
                agent_path = str(spawn.get("agent_path") or "").strip() or None
                parent = parent or str(spawn.get("parent_thread_id") or "").strip() or None
        metadata = {
            "session_id": session_id,
            "root_session_id": root_id,
            "parent_session_id": parent,
            "thread_source": thread_source or "unknown",
            "agent_path": agent_path,
            "started_at": str(payload.get("timestamp") or row.get("timestamp") or ""),
            "resumed": isinstance(payload.get("history_base"), Mapping),
        }
        return metadata, raw
    raise NativeSessionMeterError("current native session metadata is unavailable")

def _latest_counter(tail: bytes, *, at_or_before: float | None = None,
                    session_id: str | None = None,
                    allow_unsequenced: bool = False) -> tuple[dict[str, Any], bytes]:
    # The first tail record may start mid-line.  It cannot be authenticated as
    # a complete JSON event, so discard it unless the tail begins at byte zero.
    lines = tail.splitlines()
    candidates = []
    for position, raw in reversed(list(enumerate(lines))):
        if not raw.strip() or len(raw) > MAX_RECORD_BYTES:
            continue
        try:
            row = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(row, Mapping):
            continue
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        native_record = row.get("type") == "token_usage_record"
        if native_record:
            if session_id and payload.get("thread_id") != session_id:
                continue
            total = payload.get("thread_token_usage")
        elif row.get("type") == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            total = info.get("total_token_usage") if isinstance(info, Mapping) else None
        else:
            continue
        if not isinstance(total, Mapping):
            continue
        if at_or_before is not None:
            try:
                instant = datetime.fromisoformat(str(row.get("timestamp") or "").replace("Z", "+00:00"))
            except ValueError as exc:
                raise NativeSessionMeterError("native counter timestamp is invalid") from exc
            if instant.tzinfo is None:
                raise NativeSessionMeterError("native counter timestamp requires a timezone")
            if instant.timestamp() > at_or_before:
                continue
        input_tokens = _nonnegative(total.get("input_tokens"), "input_tokens")
        cached = _nonnegative(total.get("cached_input_tokens"), "cached_input_tokens")
        output = _nonnegative(total.get("output_tokens"), "output_tokens")
        reasoning = _nonnegative(
            total.get("reasoning_output_tokens", 0),
            "reasoning_output_tokens",
        )
        total_tokens = _nonnegative(total.get("total_tokens"), "total_tokens")
        if cached > input_tokens:
            raise NativeSessionMeterError("cached input exceeds native input tokens")
        if total_tokens != input_tokens + output:
            raise NativeSessionMeterError("native total tokens do not reconcile")
        ordinal = row.get("ordinal")
        ordinal_basis = "native"
        if ordinal is None and allow_unsequenced:
            if not native_record:
                continue
            # Some host review logs omit ordinals. This is usable for advisory
            # display only; authenticated metering keeps the strict default.
            ordinal = position
            ordinal_basis = "bounded tail position"
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise NativeSessionMeterError("native counter ordinal is invalid")
        candidate = {
            "ordinal": ordinal,
            "observed_at": str(row.get("timestamp") or ""),
            "counter_scope": "thread" if native_record else "segment",
            "ordinal_basis": ordinal_basis,
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached,
                "uncached_input_tokens": input_tokens - cached,
                "output_tokens": output,
                "reasoning_tokens": reasoning,
                "total_tokens": total_tokens,
            },
        }, raw
        candidates.append((native_record, candidate))
        # Provider thread totals survive resume; legacy token_count can lag.
        if native_record:
            return candidate
    if candidates:
        return candidates[0][1]
    raise NativeSessionMeterError("native session has no complete token counter")

def read_snapshot(path: str, *, at_or_before: float | None = None,
                  allow_unsequenced: bool = False) -> dict[str, Any]:
    """Read one identity and bounded counter, optionally at an authenticated stop."""
    selected = os.path.realpath(str(path or ""))
    try:
        with open(selected, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise NativeSessionMeterError("native session source is not a regular file")
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
            raise NativeSessionMeterError("native counter tail contains no complete record")
    metadata, metadata_record = _session_metadata(prefix)
    counter, counter_record = _latest_counter(tail, at_or_before=at_or_before,
                                               session_id=metadata["session_id"],
                                               allow_unsequenced=allow_unsequenced)
    source = {
        "path_fingerprint": hashlib.sha256(selected.encode("utf-8")).hexdigest(),
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
    snapshot["source_identity_fingerprint"] = _fingerprint(
        {
            "session_id": metadata["session_id"],
            "path_fingerprint": source["path_fingerprint"],
            "device": source["device"],
            "inode": source["inode"],
            "metadata_record_sha256": source["metadata_record_sha256"],
        }
    )
    snapshot["fingerprint"] = _fingerprint(snapshot)
    return snapshot

def validate_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a detached native session snapshot."""
    if not isinstance(value, Mapping) or value.get("schema") != SNAPSHOT_SCHEMA:
        raise NativeSessionMeterError("native session snapshot schema is invalid")
    snapshot = dict(value)
    fingerprint = snapshot.pop("fingerprint", None)
    if (
        not isinstance(fingerprint, str)
        or not _FINGERPRINT.fullmatch(fingerprint)
        or fingerprint != _fingerprint(snapshot)
    ):
        raise NativeSessionMeterError("native session snapshot fingerprint is invalid")
    if not str(snapshot.get("session_id") or "").strip():
        raise NativeSessionMeterError("native session identity is missing")
    if (
        not isinstance(snapshot.get("source_identity_fingerprint"), str)
        or _FINGERPRINT.fullmatch(snapshot["source_identity_fingerprint"]) is None
    ):
        raise NativeSessionMeterError("native session source identity is invalid")
    usage = snapshot.get("usage")
    if not isinstance(usage, Mapping):
        raise NativeSessionMeterError("native session usage is missing")
    normalized = {
        key: _nonnegative(usage.get(key), key)
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "uncached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
        )
    }
    if (
        normalized["cached_input_tokens"] + normalized["uncached_input_tokens"]
        != normalized["input_tokens"]
        or normalized["total_tokens"] != normalized["input_tokens"] + normalized["output_tokens"]
    ):
        raise NativeSessionMeterError("native session usage does not reconcile")
    return dict(value)

def aggregate(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Sum sessions once, respecting thread totals versus reset segment counters."""
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
            raise NativeSessionMeterError("native session source identity changed owners")
        prior_usage = prior["usage"]
        usage = row["usage"]
        prior_key = (str(prior.get("observed_at") or ""), int(prior["ordinal"]))
        row_key = (str(row.get("observed_at") or ""), int(row["ordinal"]))
        if row_key >= prior_key:
            if any(int(usage[key]) < int(prior_usage[key]) for key in usage):
                raise NativeSessionMeterError("native physical-segment counter moved backwards")
            latest_by_source[source_id] = row
    ordered_segments = [latest_by_source[key] for key in sorted(latest_by_source)]
    sessions: dict[str, list[dict[str, Any]]] = {}
    for row in ordered_segments:
        sessions.setdefault(str(row["session_id"]), []).append(row)
    for rows in sessions.values():
        rows.sort(
            key=lambda row: (
                str(row.get("observed_at") or ""),
                row["ordinal"],
                bool(row.get("resumed")),
            )
        )
        if any(not row.get("resumed") for row in rows[1:]):
            raise NativeSessionMeterError("native source replacement has no restart evidence")
    usage_keys = (
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    def session_usage(rows: list[dict[str, Any]]) -> dict[str, int]:
        thread_rows = [row for row in rows if row.get("counter_scope") == "thread"]
        if thread_rows:
            # A provider thread total already includes prior physical segments.
            latest = max(thread_rows, key=lambda row: row["usage"]["total_tokens"])
            return {key: int(latest["usage"][key]) for key in usage_keys}
        return {key: sum(int(row["usage"][key]) for row in rows) for key in usage_keys}

    by_session = {sid: session_usage(rows) for sid, rows in sessions.items()}
    result = {
        "schema": AGGREGATE_SCHEMA,
        "logical_sessions": len(sessions),
        "physical_segments": len(ordered_segments),
        "usage": {
            key: sum(usage[key] for usage in by_session.values()) for key in usage_keys
        },
        "sessions": [
            {
                "session_id": session_id,
                "parent_session_id": rows[-1].get("parent_session_id"),
                "root_session_id": rows[-1].get("root_session_id"),
                "segments": len(rows),
                "counter_fingerprints": sorted(row["fingerprint"] for row in rows),
                "total_tokens": by_session[session_id]["total_tokens"],
            }
            for session_id, rows in sorted(sessions.items())
        ],
    }
    result["fingerprint"] = _fingerprint(result)
    return result
