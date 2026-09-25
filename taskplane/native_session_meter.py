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
MAX_REPLAY_BYTES = 512 * 1024 * 1024
MAX_REPLAY_RESPONSES = 250000

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
        declared_parent = str(payload.get("forked_from_id") or payload.get("parent_thread_id") or "").strip()
        history = payload.get("history_base")
        if history is not None and (
            not isinstance(history, Mapping)
            or history.get("thread_id") not in {session_id, declared_parent}
            or not history.get("thread_id")
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
            "forked_history": isinstance(history, Mapping) and history.get("thread_id") != session_id,
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
        if cached > input_tokens or reasoning > output:
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
    if metadata.get("forked_history") and counter.get("counter_scope") != "thread":
        raise NativeSessionMeterError("forked history needs a current-thread counter; inherited legacy usage is unknown")
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

def read_logical_snapshot(paths: Sequence[str | Path], session_id: str, *,
                          at_or_before: float | None = None) -> dict[str, Any]:
    """Reconcile physical segments for one proven native task identity.

    A cumulative thread counter covers earlier segments. Legacy segment counters
    require every matching segment to be readable before they form a baseline.
    """
    snapshots = []
    errors = 0
    for path in sorted({str(Path(p).resolve()) for p in paths}):
        try:
            snapshot = read_snapshot(path, at_or_before=at_or_before, allow_unsequenced=True)
            if snapshot["session_id"] != session_id:
                continue
            snapshots.append(snapshot)
        except (OSError, ValueError):
            errors += 1
    if not snapshots:
        raise NativeSessionMeterError("native task has no readable matching counter")
    parents = {s.get("parent_session_id") for s in snapshots if s.get("parent_session_id")}
    agents = {s.get("agent_path") for s in snapshots if s.get("agent_path")}
    if len(parents) > 1 or len(agents) > 1:
        raise NativeSessionMeterError("native task segment lineage disagrees")
    thread = [s for s in snapshots if s.get("counter_scope") == "thread"]
    if thread:
        usage = max(thread, key=lambda s: s["usage"]["total_tokens"])["usage"]
    elif errors:
        raise NativeSessionMeterError("legacy task segments are incomplete")
    else:
        usage = aggregate(snapshots)["usage"]
    return {"session_id": session_id, "usage": usage,
            "parent_session_id": next(iter(parents), None),
            "agent_path": next(iter(agents), None), "partial": bool(errors)}


def read_owned_interval(paths: Sequence[str | Path], session_id: str, *,
                        start: float, end: float | None = None) -> dict[str, Any]:
    """Bounded historical recovery with [start, end) response ownership.

    Keep only counters/response IDs, never prompts. Prefer identified response
    deltas; cumulative boundary subtraction is a fallback, never added to them.
    Missing/reset/conflicting/truncated data stays partial or unavailable.
    """
    keys = ("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")
    zero = dict.fromkeys(keys, 0)
    responses: dict[str, tuple[float, dict[str, int]]] = {}
    counters: dict[tuple[float, str], dict[str, int]] = {}
    errors: set[str] = set()
    used = 0
    earliest: float | None = None
    resumed = False
    owned = False

    def counts(raw: Any) -> dict[str, int]:
        if not isinstance(raw, dict):
            raise NativeSessionMeterError("Missing owned usage")
        value = {"input_tokens": _nonnegative(raw.get("input_tokens"), "input"),
                 "cached_input_tokens": _nonnegative(raw.get("cached_input_tokens", 0), "cache"),
                 "output_tokens": _nonnegative(raw.get("output_tokens"), "output"),
                 "reasoning_tokens": _nonnegative(raw.get("reasoning_output_tokens", raw.get("reasoning_tokens", 0)), "reasoning"),
                 "total_tokens": _nonnegative(raw.get("total_tokens"), "total")}
        value["uncached_input_tokens"] = value["input_tokens"] - value["cached_input_tokens"]
        if (value["uncached_input_tokens"] < 0 or value["reasoning_tokens"] > value["output_tokens"]
                or value["total_tokens"] != value["input_tokens"] + value["output_tokens"]):
            raise NativeSessionMeterError("Owned usage does not reconcile")
        return value

    for name in sorted({str(Path(p).absolute()) for p in paths}):
        path = Path(name)
        try:
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise NativeSessionMeterError("Symlinked native replay")
            with path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise NativeSessionMeterError("Native replay is not regular")
                metadata, _ = _session_metadata(stream.read(MAX_METADATA_BYTES))
                if metadata["session_id"] != session_id:
                    raise NativeSessionMeterError("Foreign native replay")
                born = datetime.fromisoformat(metadata["started_at"].replace("Z", "+00:00")).timestamp()
                earliest = born if earliest is None else min(earliest, born)
                resumed = resumed or metadata["resumed"]
                stream.seek(0)
                while used < MAX_REPLAY_BYTES:
                    raw = stream.readline(min(MAX_RECORD_BYTES + 1, MAX_REPLAY_BYTES - used))
                    if not raw:
                        break
                    used += len(raw)
                    if not raw.endswith(b"\n"):
                        errors.add("oversized_or_inflight_record")
                        while raw and not raw.endswith(b"\n") and used < MAX_REPLAY_BYTES:
                            raw = stream.readline(min(MAX_RECORD_BYTES, MAX_REPLAY_BYTES - used))
                            used += len(raw)
                        continue
                    try:
                        record = json.loads(raw)
                        payload = record.get("payload", {}) if isinstance(record, dict) else {}
                        native = record.get("type") == "token_usage_record"
                        legacy = record.get("type") == "event_msg" and payload.get("type") == "token_count"
                        if not native and not legacy:
                            continue
                        if native and payload.get("thread_id") != session_id:
                            continue
                        if legacy and metadata.get("forked_history"):
                            continue
                        at = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
                        if at.tzinfo is None:
                            raise ValueError("timestamp lacks timezone")
                        instant = at.timestamp()
                        if end is not None and instant >= end:
                            continue
                        total = payload.get("thread_token_usage") if native else payload.get("info", {}).get("total_token_usage")
                        if total is not None:
                            value = counts(total)
                            key = (instant, "thread" if native else name)
                            if key in counters and counters[key] != value:
                                errors.add("conflicting_counter")
                            counters[key] = value
                        if native and "usage" in payload:
                            owned = True
                            response = payload.get("response_id")
                            if not isinstance(response, str) or not response:
                                errors.add("missing_response_identity")
                                continue
                            value = counts(payload["usage"])
                            if response in responses and responses[response] != (instant, value):
                                errors.add("conflicting_response")
                            responses[response] = (instant, value)
                            if len(responses) > MAX_REPLAY_RESPONSES:
                                errors.add("response_limit")
                                break
                    except (ValueError, KeyError, TypeError, AttributeError):
                        errors.add("invalid_counter_record")
                if used >= MAX_REPLAY_BYTES:
                    errors.add("replay_byte_limit")
                after = os.fstat(stream.fileno())
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    errors.add("native_source_changed")
        except (OSError, ValueError):
            errors.add("native_source_unavailable")
    thread = [(at, v) for (at, kind), v in counters.items() if kind == "thread"]
    samples = sorted(thread or [(at, v) for (at, _), v in counters.items()], key=lambda x: x[0])
    if not thread and len(paths) > 1:
        errors.add("legacy_segment_boundaries_unknown")
    reset = any(any(b[k] < a[k] for k in keys) for (_, a), (_, b) in zip(samples, samples[1:]))
    if reset:
        errors.add("counter_reset")
    prior = [value for at, value in samples if at < start]
    baseline = prior[-1] if prior else zero if earliest is not None and not resumed and not errors else None
    native_usage = samples[-1][1] if samples else None
    usage = None
    basis = "unavailable interval"
    if owned:
        usage = {k: sum(v[k] for at, v in responses.values() if at >= start) for k in keys}
        basis = "owned response replay [start, end)"
        if baseline is None and resumed:
            errors.add("resumed_baseline_unavailable")
        if baseline is not None and native_usage is not None and not reset:
            if any(usage[k] != native_usage[k] - baseline[k] for k in keys):
                errors.add("owned_counter_mismatch")
    elif baseline is not None and native_usage is not None and not reset:
        if all(native_usage[k] >= baseline[k] for k in keys):
            usage = {k: native_usage[k] - baseline[k] for k in keys}
            basis = "owned session cumulative boundaries [start, end)"
    if errors and not owned:
        usage = None
    return {"usage": usage, "native_usage": native_usage, "baseline": baseline,
            "status": "partial" if errors else "measured" if usage is not None else "unavailable",
            "basis": basis, "errors": sorted(errors), "bytes_read": used,
            "responses": sum(at >= start for at, _ in responses.values()),
            "interval": {"start": start, "end_exclusive": end}}


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
