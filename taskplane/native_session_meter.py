"""Read native Codex counters and lineage without retaining conversation content."""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Iterator, Mapping, Sequence
from copy import deepcopy
import math
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
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "uncached_input_tokens",
              "output_tokens", "reasoning_tokens", "total_tokens")


def starting_baseline(run: Mapping[str, Any]) -> dict[str, int] | None:
    """Accept observed counters, or complete coherent legacy status-less ones.

    A partial subtotal cannot become exact just because native history recovers.
    Empty/missing categories are not evidence of a zero starting counter.
    """
    usage = run.get("usage")
    if (run.get("usage_status", "observed") != "observed"
            or not isinstance(usage, dict)
            or any(type(usage.get(k)) is not int or usage[k] < 0 for k in USAGE_KEYS)):
        return None
    if (usage["input_tokens"] != usage["cached_input_tokens"] + usage["uncached_input_tokens"]
            or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]
            or usage["reasoning_tokens"] > usage["output_tokens"]):
        return None
    return {k: usage[k] for k in USAGE_KEYS}

class NativeSessionMeterError(ValueError):
    """The native counter or its lineage cannot be proven safely."""

class CounterChronologyError(NativeSessionMeterError):
    """Observed counters conflict; a saved baseline must not hide the conflict."""

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

def _session_metadata(prefix: bytes, *, validate_history: bool = True) -> tuple[dict[str, Any], bytes]:
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
        if validate_history and history is not None and (
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
        budget = _ACTIVE_BUDGET.get()
        if budget is not None:
            if not budget.available():
                raise NativeSessionMeterError("aggregate usage budget exhausted")
            budget.parsed_records += 1
            budget.max_record_bytes = max(budget.max_record_bytes, len(raw))
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

def _snapshot(path: str, *, at_or_before: float | None = None,
                  allow_unsequenced: bool = False) -> dict[str, Any]:
    """Read one identity and bounded counter, optionally at an authenticated stop."""
    selected = os.path.realpath(str(path or ""))
    try:
        with _safe_open(path) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise NativeSessionMeterError("native session source is not a regular file")
            prefix = _bounded_read(stream, min(before.st_size, MAX_METADATA_BYTES))
            tail_offset = max(0, before.st_size - MAX_COUNTER_TAIL_BYTES)
            stream.seek(tail_offset)
            tail = _bounded_read(stream, min(MAX_COUNTER_TAIL_BYTES, before.st_size-tail_offset))
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
    budget = _ACTIVE_BUDGET.get()
    assert budget is not None
    if not budget.available():
        raise NativeSessionMeterError("aggregate usage budget exhausted")
    budget.parsed_records += 1
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

def _logical_snapshot(paths: Sequence[str | Path], session_id: str, *,
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
        raise CounterChronologyError("native task segment lineage disagrees")
    thread = [s for s in snapshots if s.get("counter_scope") == "thread"]
    if not thread and errors:
        raise NativeSessionMeterError("legacy task segments are incomplete")
    usage = aggregate(snapshots)["usage"]
    return {"session_id": session_id, "usage": usage,
            "parent_session_id": next(iter(parents), None),
            "agent_path": next(iter(agents), None), "partial": bool(errors),
            "measured_at": max(snapshots, key=_counter_order).get("observed_at"),
            "cutoff": at_or_before, "cutoff_semantics": "at_or_before"}



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

def _counter_order(row: Mapping[str, Any]) -> tuple[float, int]:
    try:
        at = datetime.fromisoformat(str(row.get("observed_at") or "").replace("Z", "+00:00"))
        if at.tzinfo is None:
            raise ValueError("timezone unavailable")
    except ValueError as exc:
        raise CounterChronologyError("native counter chronology is unavailable") from exc
    return at.timestamp(), _nonnegative(row.get("ordinal"), "ordinal")


def _chronological_counter(rows: Sequence[dict[str, Any]], label: str) -> dict[str, Any]:
    ordered = sorted(rows, key=_counter_order)
    for before, after in zip(ordered, ordered[1:]):
        a, b = _counter_order(before), _counter_order(after)
        if (a == b or (a[0] == b[0] and
                any(row.get("ordinal_basis") != "native" for row in (before, after)))):
            if before["usage"] != after["usage"]:
                raise CounterChronologyError(f"conflicting {label} counter boundary")
        if any(after["usage"][key] < before["usage"][key] for key in USAGE_KEYS):
            raise CounterChronologyError(f"{label} counter moved backwards")
    return ordered[-1]


def aggregate(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Sum sessions once, respecting thread totals versus reset segment counters."""
    by_source: dict[tuple[int, int], list[dict[str, Any]]] = {}
    thread_history: dict[str, list[dict[str, Any]]] = {}
    for raw in snapshots:
        row = validate_snapshot(raw)
        session_id = str(row["session_id"])
        source_id = (row["source"]["device"], row["source"]["inode"])
        prior = by_source.setdefault(source_id, [])
        if prior and prior[0]["session_id"] != session_id:
            raise CounterChronologyError("native session source identity changed owners")
        prior.append(row)
        if row.get("counter_scope") == "thread":
            thread_history.setdefault(session_id, []).append(row)
    ordered_segments = [_chronological_counter(by_source[key], "native physical-segment")
                        for key in sorted(by_source)]
    latest_threads = {sid: _chronological_counter(rows, "native thread")
                      for sid, rows in thread_history.items()}
    sessions: dict[str, list[dict[str, Any]]] = {}
    for row in ordered_segments:
        sessions.setdefault(str(row["session_id"]), []).append(row)
    for rows in sessions.values():
        rows.sort(key=lambda row: (*_counter_order(row), bool(row.get("resumed"))))
        for field in ("parent_session_id", "agent_path"):
            if len({row[field] for row in rows if row.get(field)}) > 1:
                raise CounterChronologyError("native task segment lineage disagrees")
        # Archive/copy inventories can expose an identical thread observation
        # through multiple physical files. Exact native record hashes prove
        # that duplicate without inventing restart provenance for other files.
        distinct = []
        seen_thread_records = set()
        for row in rows:
            source = row["source"]
            record = (source["metadata_record_sha256"], source["counter_record_sha256"])
            if row.get("counter_scope") == "thread":
                if record in seen_thread_records:
                    continue
                seen_thread_records.add(record)
            distinct.append(row)
        if any(not row.get("resumed") for row in distinct[1:]):
            raise CounterChronologyError("native source replacement has no restart evidence")
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
            latest = latest_threads[str(rows[0]["session_id"])]
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

# Advisory incremental reconciliation. These limits apply to a whole refresh,
# never independently to each file or parser stage.
import contextlib
import contextvars
import time

MAX_RECORD_BYTES = 1024 * 1024
PARSER_VERSION = 6
APPEND_ONLY_TRUST = ('Native identity and completed historical bytes are assumed append-only. '
                     'Prefix/tail validation is not a cryptographic proof of the middle. '
                     'Changed identity, truncation or suspicious metadata makes coverage unknown.')
_ACTIVE_BUDGET: contextvars.ContextVar[UsageBudget | None] = contextvars.ContextVar('taskplane_usage_budget', default=None)


class UsageBudget:
    """Shared, measured cooperative admission budget; filesystem calls may overrun time."""
    def __init__(self, *, bytes_limit: int = 16 * 1024 * 1024, records_limit: int = 100000,
                 entries_limit: int = 2000, seconds_limit: float = 1.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        for value, maximum in ((bytes_limit, 16*1024*1024), (records_limit, 100000), (entries_limit, 2000)):
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError('usage budget may lower, never exceed, aggregate caps')
        if type(seconds_limit) not in (int, float) or not math.isfinite(seconds_limit) or not 0 <= seconds_limit <= 1:
            raise ValueError('usage clock budget must be finite and at most one second')
        self.clock = clock
        self.started = clock()
        self.bytes_limit, self.records_limit = bytes_limit, records_limit
        self.entries_limit, self.seconds_limit = entries_limit, seconds_limit
        self.read_bytes = self.parsed_records = self.discovery_entries = 0
        self.validation_bytes = self.appended_bytes = self.unrelated_scans = 0
        self.directory_bytes = self.directory_flag_bytes = 0
        self.max_record_bytes = self.max_prefix_bytes = self.max_tail_bytes = 0
        self.exhausted = False

    def available(self) -> bool:
        if (self.read_bytes >= self.bytes_limit or self.parsed_records >= self.records_limit
                or self.discovery_entries >= self.entries_limit
                or self.clock() - self.started >= self.seconds_limit):
            self.exhausted = True
        return not self.exhausted

    def read(self, stream: BinaryIO, amount: int, *, kind: str = 'appended') -> bytes:
        if not self.available():
            return b''
        data = stream.read(min(amount, self.bytes_limit - self.read_bytes, 65536))
        self.read_bytes += len(data)
        if kind in ('prefix', 'tail'):
            self.validation_bytes += len(data)
            setattr(self, 'max_' + kind + '_bytes', max(getattr(self, 'max_' + kind + '_bytes'), len(data)))
        elif kind == 'appended':
            self.appended_bytes += len(data)
        self.available()
        return data

    def report(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in ('read_bytes', 'parsed_records',
            'discovery_entries', 'max_record_bytes', 'exhausted', 'unrelated_scans',
            'validation_bytes', 'max_prefix_bytes', 'max_tail_bytes', 'appended_bytes',
            'directory_bytes', 'directory_flag_bytes')} | {
                'elapsed_seconds': self.clock() - self.started}


@contextlib.contextmanager
def usage_refresh(budget: UsageBudget | None = None) -> Iterator[UsageBudget]:
    """Wrap an entire CLI operation; nested readers reuse the enclosing budget."""
    existing = _ACTIVE_BUDGET.get()
    selected = existing or budget or UsageBudget()
    token = _ACTIVE_BUDGET.set(selected)
    try:
        yield selected
    finally:
        _ACTIVE_BUDGET.reset(token)


def _instant(value: Any) -> float:
    at = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if at.tzinfo is None:
        raise ValueError('timestamp requires timezone')
    return at.timestamp()


def _usage(raw: Any, host: str) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise ValueError('missing usage')
    if host == 'Claude':
        # Anthropic input excludes cache reads and creation; the shared input
        # includes both, and total adds output exactly once.
        uncached = _nonnegative(raw.get('input_tokens'), 'input') + _nonnegative(raw.get('cache_creation_input_tokens'), 'cache creation')
        cached = _nonnegative(raw.get('cache_read_input_tokens'), 'cache read')
        inp = uncached + cached
    else:
        inp = _nonnegative(raw.get('input_tokens'), 'input')
        cached = _nonnegative(raw.get('cached_input_tokens'), 'cache')
        uncached = inp - cached
    out = _nonnegative(raw.get('output_tokens'), 'output')
    reasoning = _nonnegative(raw.get('reasoning_output_tokens', raw.get('reasoning_tokens', 0)), 'reasoning')
    if uncached < 0 or reasoning > out or host != 'Claude' and raw.get('total_tokens') != inp + out:
        raise ValueError('usage does not reconcile')
    return dict(zip(USAGE_KEYS, (inp, cached, uncached, out, reasoning, inp + out)))


def _safe_open(path: str | Path) -> BinaryIO:
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlinked native source')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError('native source is not regular')
    return os.fdopen(fd, 'rb')


class IncrementalUsageReader:
    """Resumable inventory and one-pass owned intervals, retaining no prompt text.

    A reader may be reused across refreshes. Its detached checkpoint contains only
    identities, offsets, digests and compact counter/dedup state. Incomplete raw
    records stay in memory; restoring a checkpoint rereads their last complete
    boundary under the next budget. Directory cookies are valid only while the
    directory identity/mtime is unchanged; changed discovery stays unknown.
    """
    def __init__(self, host: str, root: str, roots: Sequence[str | Path] = (), *,
                 start: float | None = None, end: float | None = None,
                 known: Iterable[str] = (), checkpoint: dict[str, Any] | None = None,
                 expected_root: str | None = None) -> None:
        if host not in ('Codex', 'Claude'):
            raise ValueError('unknown native host format')
        self.host, self.root, self.start, self.end = host, root, start, end
        self.expected_root = expected_root
        self.roots = [str(Path(p).absolute()) for p in roots]
        self.known = set(known) | {root}
        binding = {'host': host, 'root': root, 'roots': self.roots,
                   'start': start, 'end': end, 'known': sorted(self.known), 'parser': PARSER_VERSION,
                   'expected_root': expected_root}
        self.binding = _fingerprint(binding)
        self.state: dict[str, Any] = {
            'schema': 'taskplane.usage-checkpoint-set/v1', 'binding': self.binding,
            'host_format': host, 'parser_version': PARSER_VERSION,
            'lineage_digest': _fingerprint([root, sorted(self.known)]),
            'interval_digest': _fingerprint([start, end]), 'files': {},
            'directories': [{'path': p, 'cookie': 0} for p in self.roots],
            'fully_discovered': False, 'discovery_errors': [], 'invalidated_sessions': {},
            'inventory_diagnostics': [], 'inventory_errors': 0, 'serial': 0}
        self.checkpoint_invalidated: str | None = None
        if checkpoint is not None:
            try:
                self._validate_checkpoint(checkpoint)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                self.checkpoint_invalidated = 'incompatible_or_malformed_checkpoint'
            else:
                self.state = deepcopy(checkpoint)
        self._pending: dict[str, bytes] = {}

    def checkpoint(self) -> dict[str, Any]:
        """Detached observational cache; callers must also bind its outer run ID."""
        return deepcopy(self.state)

    def close(self) -> None:
        self._pending.clear()

    def _validate_checkpoint(self, value: dict[str, Any]) -> None:
        def natural(raw: Any) -> None:
            if type(raw) is not int or raw < 0:
                raise ValueError('invalid checkpoint integer')

        def normalized(raw: Any) -> None:
            if not isinstance(raw, dict) or set(raw) != set(USAGE_KEYS):
                raise ValueError('invalid checkpoint usage')
            for count in raw.values(): natural(count)
            if (raw['input_tokens'] != raw['cached_input_tokens'] + raw['uncached_input_tokens']
                    or raw['total_tokens'] != raw['input_tokens'] + raw['output_tokens']
                    or raw['reasoning_tokens'] > raw['output_tokens']):
                raise ValueError('invalid checkpoint token categories')

        def observation(raw: Any, counter: bool = False) -> None:
            if not isinstance(raw, list) or len(raw) != (3 if counter else 2):
                raise ValueError('invalid checkpoint observation')
            if type(raw[0]) not in (int, float) or not math.isfinite(raw[0]):
                raise ValueError('invalid checkpoint timestamp')
            if counter: natural(raw[1])
            normalized(raw[-1])

        for key in ('schema', 'binding', 'host_format', 'parser_version', 'lineage_digest', 'interval_digest'):
            if value.get(key) != self.state[key]:
                raise ValueError('foreign checkpoint binding')
        natural(value['serial'])
        natural(value['inventory_errors'])
        diagnostics = value['inventory_diagnostics']
        if (not isinstance(diagnostics, list) or len(diagnostics) > 32
                or value['inventory_errors'] < len(diagnostics)
                or any(not isinstance(item, dict) or item.get('scope') != 'unrelated_inventory'
                       or not isinstance(item.get('session'), str) for item in diagnostics)):
            raise ValueError('invalid inventory diagnostics')
        if type(value['fully_discovered']) is not bool or not isinstance(value['files'], dict):
            raise ValueError('invalid checkpoint inventory')
        if not isinstance(value['discovery_errors'], list) or any(not isinstance(e, str) for e in value['discovery_errors']):
            raise ValueError('invalid checkpoint discovery errors')
        if not isinstance(value['invalidated_sessions'], dict) or any(
                not isinstance(sid, str) or not isinstance(reason, str)
                for sid, reason in value['invalidated_sessions'].items()):
            raise ValueError('invalid checkpoint lost ownership')
        if not isinstance(value['directories'], list):
            raise ValueError('invalid checkpoint directories')
        for directory in value['directories']:
            if not isinstance(directory, dict) or not isinstance(directory.get('path'), str):
                raise ValueError('invalid checkpoint directory')
            if not any(Path(directory['path']).is_relative_to(root) for root in self.roots):
                raise ValueError('foreign checkpoint directory')
            natural(directory['cookie'])
            if not isinstance(directory.get('pending', []), list) or any(
                    not isinstance(name, str) or Path(name).name != name for name in directory.get('pending', []) if name not in ('.', '..')):
                raise ValueError('invalid checkpoint directory page')
        if value['fully_discovered'] and value['directories']:
            raise ValueError('checkpoint discovery disagrees')
        for path, file in value['files'].items():
            if not isinstance(file, dict) or file.get('path') != path or not Path(path).is_absolute():
                raise ValueError('invalid checkpoint file')
            for key in ('host_format', 'parser_version', 'lineage_digest', 'interval_digest'):
                if file.get(key) != self.state[key]: raise ValueError('foreign checkpoint file')
            if file.get('schema') != 'taskplane.usage-checkpoint/v1':
                raise ValueError('invalid file checkpoint schema')
            for key in ('size', 'last_complete_record_offset', 'continuation'):
                natural(file[key])
            natural(file.get('counter_revision', 0))
            if file['last_complete_record_offset'] > file['size']:
                raise ValueError('checkpoint offset exceeds size')
            if file['coverage'] not in ('complete', 'partial', 'unknown', 'unrelated'):
                raise ValueError('invalid checkpoint coverage')
            if not isinstance(file['errors'], list) or any(not isinstance(e, str) for e in file['errors']):
                raise ValueError('invalid checkpoint errors')
            if file['size']:
                if not isinstance(file['file_identity'], list) or len(file['file_identity']) != 2:
                    raise ValueError('invalid checkpoint identity')
                for part in file['file_identity']: natural(part)
                natural(file['mtime_ns'])
                for key in ('prefix_digest', 'tail_digest'):
                    if not isinstance(file[key], str) or _FINGERPRINT.fullmatch(file[key]) is None:
                        raise ValueError('invalid checkpoint digest')
            meta = file.get('metadata')
            if meta is not None and (not isinstance(meta, dict) or not isinstance(meta.get('session_id'), str)):
                raise ValueError('invalid checkpoint metadata')
            if meta is not None:
                if not isinstance(file.get('metadata_identity'), list) or len(file['metadata_identity']) != 2:
                    raise ValueError('invalid checkpoint metadata identity')
                for part in file['metadata_identity']: natural(part)
                natural(file['metadata_size']); natural(file['metadata_mtime_ns'])
            if not isinstance(file['counter_state'], dict) or not isinstance(file['dedup_state'], dict):
                raise ValueError('invalid checkpoint counters')
            for item in file['counter_state'].values(): observation(item, True)
            for key, item in file['dedup_state'].items():
                if not isinstance(key, str) or not key: raise ValueError('invalid response identity')
                if self.host == 'Codex': observation(item)
                else:
                    if (not isinstance(item, dict) or not {'before', 'after'} <= set(item)
                            or set(item) - {'before', 'after', 'unknown_before', 'decreased'}
                            or any(type(item.get(flag, False)) is not bool for flag in ('unknown_before', 'decreased'))):
                        raise ValueError('invalid message checkpoint')
                    for endpoint in (item['before'], item['after']):
                        if endpoint is not None: observation(endpoint)
        summaries = value.get('session_summaries', {})
        if not isinstance(summaries, dict):
            raise ValueError('invalid checkpoint summaries')
        for sid, summary in summaries.items():
            if not isinstance(sid, str) or not isinstance(summary, dict):
                raise ValueError('invalid checkpoint summary identity')
            row = summary['row']
            if not isinstance(row, dict) or row.get('session') != sid:
                raise ValueError('foreign checkpoint summary')
            signature = summary['signature']
            if not isinstance(signature, list): raise ValueError('invalid summary signature')
            for entry in signature:
                if not isinstance(entry, list) or len(entry) != 3:
                    raise ValueError('invalid summary file signature')
                source = value['files'].get(entry[0])
                if source is None:
                    if sid not in value['invalidated_sessions']: raise ValueError('foreign summary source')
                elif (source.get('metadata') or {}).get('session_id') != sid:
                    raise ValueError('foreign summary owner')
                natural(entry[1])
                if source is not None and entry[1] > source.get('counter_revision', 0):
                    raise ValueError('summary is ahead of native parser')
                if not isinstance(entry[2], list) or any(not isinstance(e,str) for e in entry[2]):
                    raise ValueError('invalid summary errors')
            for key in ('usage', 'native_usage', 'baseline'):
                if row.get(key) is not None: normalized(row[key])
            if row['status'] not in ('measured','partial','unavailable') or type(row['attribution_unknown']) is not bool:
                raise ValueError('invalid summary coverage')
            if not isinstance(row['errors'], list) or any(not isinstance(e,str) for e in row['errors']):
                raise ValueError('invalid summary errors')
            if row['interval'] != {'start':self.start,'end_exclusive':self.end}:
                raise ValueError('foreign summary interval')
            natural(row['responses'])
            if row['measured_at'] is not None: _instant(row['measured_at'])

    def _entry(self, directory: dict[str, Any], budget: UsageBudget) -> str | None:
        """Meter raw directory pages; kernel offsets are durable across opens.

        telldir cookies belong to an open DIR object and cannot be checkpointed.
        Darwin __getdirentries64 and Linux getdents64 expose kernel offsets;
        unsupported ABIs remain unknown. Scandir cannot expose actual native
        page bytes and therefore cannot satisfy this accounting contract.
        """
        path = directory['path']
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or Path(path).is_symlink():
            raise ValueError('invalid inventory directory')
        identity = [info.st_dev, info.st_ino, info.st_mtime_ns]
        if directory.get('identity') not in (None, identity):
            self.state['discovery_errors'].append('inventory_changed_during_discovery')
            directory.update(cookie=0, pending=[])
        directory['identity'] = identity
        pending = directory.setdefault('pending', [])
        if pending:
            return str(pending.pop(0))
        import sys
        if sys.platform in ('darwin', 'linux'):
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            name = '__getdirentries64' if sys.platform == 'darwin' else 'getdents64'
            call = getattr(libc, name, None)
            if call is not None:
                # Darwin SDK sys/dirent.h: 64-bit inode/seekoff, reclen at16,
                # namlen at18, type at20, name at21, max name buffer1024.
                # Its old compatibility API truncates long UTF-8 names.
                # Linux getdents64 has a name at19 and reclen at16.
                minimum = 32 if sys.platform == 'darwin' else 24
                if budget.entries_limit-budget.discovery_entries < 64:
                    budget.exhausted = True
                    return ''
                amount = min(65536, budget.bytes_limit-budget.read_bytes,
                             (budget.entries_limit-budget.discovery_entries)*minimum)
                # Reserve enough for the maximum Darwin dirent (1056 bytes),
                # or a Linux NAME_MAX entry, before interpreting zero as EOF.
                if amount < (2048 if sys.platform == 'darwin' else 512):
                    budget.exhausted = True
                    return ''
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
                try:
                    os.lseek(fd, directory.get('cookie', 0), os.SEEK_SET)
                    buffer = ctypes.create_string_buffer(amount)
                    # Recent Darwin kernels also write four flag bytes beyond
                    # the returned dirent payload. Observe and charge them;
                    # old kernels leave the sentinel unchanged.
                    if sys.platform == 'darwin':
                        for flag_offset in range(amount-4, amount):
                            buffer[flag_offset] = b'\xff'
                    call.restype = ctypes.c_ssize_t
                    if sys.platform == 'darwin':
                        call.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_longlong)]
                        base = ctypes.c_longlong()
                        size = call(fd, buffer, amount, ctypes.byref(base))
                    else:
                        call.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
                        size = call(fd, buffer, amount)
                    page = buffer.raw
                    flag_bytes = (4 if sys.platform == 'darwin' and size <= amount-4
                                  and page[-4:] != b'\xff' * 4 else 0)
                    observed = max(size, 0) + flag_bytes
                    budget.read_bytes += observed
                    budget.directory_bytes += observed
                    budget.directory_flag_bytes += flag_bytes
                    if size < 0:
                        import errno
                        if ctypes.get_errno() == errno.EINVAL:
                            budget.exhausted = True
                            return ''  # Next entry did not fit this refresh.
                        raise OSError(ctypes.get_errno(), name)
                    directory['cookie'] = os.lseek(fd, 0, os.SEEK_CUR)
                finally:
                    os.close(fd)
                raw = page[:size]
                offset = 0
                while offset < size:
                    length_at = offset + 16
                    length = int.from_bytes(raw[length_at:length_at+2], sys.byteorder)
                    if length < minimum or offset+length > size:
                        raise ValueError('invalid native directory page')
                    name_at = offset + (21 if sys.platform == 'darwin' else 19)
                    pending.append(os.fsdecode(raw[name_at:offset+length].split(b'\0',1)[0]))
                    offset += length
                budget.discovery_entries += len(pending)
                budget.unrelated_scans += len(pending)
                budget.available()
                return str(pending.pop(0)) if pending else None
        self.state['discovery_errors'].append('native_directory_accounting_unavailable')
        return None

    def _discover(self, budget: UsageBudget) -> None:
        while self.state['directories'] and budget.available():
            directory = self.state['directories'][0]
            try:
                name = self._entry(directory, budget)
                if name is None:
                    self.state['directories'].pop(0)
                    continue
                if name in ('', '.', '..'):
                    continue
                path = Path(directory['path']) / name
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    self.state['directories'].append({'path': str(path), 'cookie': 0})
                elif name.endswith('.jsonl'):
                    self.add_file(path)
            except FileNotFoundError:
                self.state['directories'].pop(0)
            except (OSError, ValueError):
                self.state['discovery_errors'].append('inventory_unavailable')
                self.state['directories'].pop(0)
        self.state['fully_discovered'] = not self.state['directories'] and not self.state['discovery_errors']

    def add_file(self, path: str | Path, *, session: str | None = None, agent: str | None = None) -> None:
        name = str(Path(path).absolute())
        if name not in self.state['files']:
            self.state['files'][name] = {'schema': 'taskplane.usage-checkpoint/v1',
                **{key: self.state[key] for key in ('host_format', 'parser_version', 'lineage_digest', 'interval_digest')},
                'file_identity': None, 'mtime_ns': None, 'prefix_digest': None, 'tail_digest': None, 'path': name, 'session': session, 'agent': agent,
                'last_complete_record_offset': 0, 'size': 0, 'counter_state': {},
                'dedup_state': {}, 'errors': [], 'coverage': 'unknown', 'continuation': 0}

    def _metadata(self, file: dict[str, Any], stream: BinaryIO, budget: UsageBudget) -> bool:
        before = os.fstat(stream.fileno())
        stream.seek(0)
        data = b''
        while b'\n' not in data and len(data) < MAX_RECORD_BYTES and budget.available():
            part = budget.read(stream, min(4096, MAX_RECORD_BYTES - len(data)), kind='metadata')
            if not part:
                break
            data += part
        if b'\n' not in data:
            file['coverage'] = 'unknown'
            return False
        raw = data.split(b'\n', 1)[0]
        budget.parsed_records += 1
        budget.max_record_bytes = max(budget.max_record_bytes, len(raw) + 1)
        if self.host == 'Codex':
            try:
                meta, _ = _session_metadata(raw)
            except NativeSessionMeterError:
                # Identity and lineage may still prove that malformed restart
                # metadata belongs to an unrelated inventory entry. This row
                # is diagnostic only and must never admit its counters.
                meta, _ = _session_metadata(raw, validate_history=False)
                meta['validation_error'] = 'native_metadata_invalid'
                file['errors'].append('native_metadata_invalid')
        else:
            row = json.loads(raw)
            sid = row.get('sessionId')
            if not sid:
                raise ValueError('missing Claude identity')
            agent = row.get('agentId')
            meta = {'session_id': agent or sid, 'root_session_id': sid,
                    'parent_session_id': sid if agent else None,
                    'started_at': row.get('timestamp'), 'agent_path': agent,
                    'resumed': False, 'forked_history': False}
            if self.expected_root is not None and sid != self.expected_root:
                meta['validation_error'] = 'native_parent_mismatch'
                file['errors'].append('native_parent_mismatch')
                self.known.add(meta['session_id'])
        file['metadata'] = meta
        file['session'] = meta['session_id']
        file['metadata_identity'] = [before.st_dev, before.st_ino]
        file['metadata_size'] = before.st_size
        file['metadata_mtime_ns'] = before.st_mtime_ns
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            file['errors'].append('native_source_changed')
        # Reuse all bytes already read for discovery; selected files do not
        # replay metadata or parse the same first record a second time.
        self._pending[file['path']] = data
        return True

    def _consume(self, file: dict[str, Any], row: dict[str, Any]) -> None:
        meta = file['metadata']; sid = meta['session_id']
        state = file['counter_state']; dedup = file['dedup_state']
        if self.host == 'Codex':
            payload = row.get('payload', {})
            native = row.get('type') == 'token_usage_record'
            legacy = row.get('type') == 'event_msg' and payload.get('type') == 'token_count'
            if not (native or legacy) or native and payload.get('thread_id') != sid or legacy and meta.get('forked_history'):
                return
            file['counter_revision'] = file.get('counter_revision', 0) + 1
            at = _instant(row['timestamp'])
            if self.end is not None and at >= self.end:
                return
            raw = payload.get('thread_token_usage') if native else payload.get('info', {}).get('total_token_usage')
            if raw is not None:
                value = _usage(raw, self.host)
                ordinal = _nonnegative(row.get('ordinal', 0), 'ordinal')
                counter_key = str((at, ordinal))
                item = [at, ordinal, value]
                if counter_key in state and state[counter_key] != item:
                    file['errors'].append('conflicting_counter')
                state[counter_key] = item
            if native and 'usage' in payload:
                key = payload.get('response_id')
                if not isinstance(key, str) or not key:
                    raise ValueError('missing_response_identity')
                item = [at, _usage(payload['usage'], self.host)]
                if key in dedup and dedup[key] != item:
                    file['errors'].append('conflicting_response')
                dedup[key] = item
        else:
            if row.get('sessionId') != meta['root_session_id'] or (row.get('agentId') or None) != meta.get('agent_path'):
                return
            message = row.get('message')
            if row.get('type') != 'assistant' or not isinstance(message, dict) or message.get('model') == '<synthetic>':
                return
            key = message.get('id') or row.get('requestId')
            if not key:
                raise ValueError('missing_message_identity')
            file['counter_revision'] = file.get('counter_revision', 0) + 1
            at = _instant(row['timestamp'])
            if self.end is not None and at >= self.end:
                return
            item = dedup.setdefault(key, {'before': None, 'after': None})
            slot = 'before' if self.start is not None and at < self.start else 'after'
            try:
                value = _usage(message.get('usage'), self.host)
            except ValueError:
                if slot == 'before':
                    item['unknown_before'] = True
                raise
            old = item[slot]
            if old is not None:
                lower, upper = (old[1], value) if at >= old[0] else (value, old[1])
                if any(upper[k] < lower[k] for k in USAGE_KEYS):
                    file['errors'].append('counter_decreased')
                    item['decreased'] = True
            if old is None or at >= old[0]:
                item[slot] = [at, value]

    def _file(self, file: dict[str, Any], budget: UsageBudget) -> None:
        try:
            with _safe_open(file['path']) as stream:
                before = os.fstat(stream.fileno())
                identity = [before.st_dev, before.st_ino]
                old_size = file.get('size', 0)
                old_offset = file['last_complete_record_offset']
                prior_identity = file.get('file_identity') or file.get('metadata_identity')
                prior_size = old_size or file.get('metadata_size', 0)
                prior_mtime = file.get('mtime_ns') if old_size else file.get('metadata_mtime_ns')
                changed = prior_identity not in (None, identity) or before.st_size < prior_size
                suspicious = prior_size == before.st_size and prior_mtime not in (None, before.st_mtime_ns)
                if changed or suspicious:
                    # Replaying the replacement cannot prove the lost history.
                    previous_owner = (file.get('metadata') or {}).get('session_id')
                    if previous_owner in self.known:
                        self.state['invalidated_sessions'][previous_owner] = (
                            'native_identity_reset' if changed else 'uncertain_middle_mutation')
                    file.update(counter_state={}, dedup_state={}, last_complete_record_offset=0,
                                metadata=None, coverage='unknown')
                    file['counter_revision'] = file.get('counter_revision', 0) + 1
                    file['errors'] = ['native_identity_reset' if changed else 'uncertain_middle_mutation']
                    self._pending.pop(file['path'], None)
                if not file.get('metadata'):
                    if not self._metadata(file, stream, budget):
                        return
                meta = file['metadata']
                if meta.get('validation_error'):
                    file['coverage'] = 'unknown'
                    return
                owned = meta['session_id'] in self.known or meta.get('parent_session_id') in self.known
                if not owned:
                    file['coverage'] = 'unrelated'
                    self._pending.pop(file['path'], None)
                    return
                self.known.add(meta['session_id'])
                # Validate at most two 4 KiB windows at the prior complete size.
                # Newly discovered metadata bytes are already counted separately.
                window_bytes = []
                if old_offset and not changed and not suspicious:
                    windows = []
                    for kind, offset, size in [('prefix', 0, min(4096, old_offset)),
                                              ('tail', max(0, old_offset - 4096), min(4096, old_offset))]:
                        if not budget.available():
                            file['coverage'] = 'partial'; return
                        stream.seek(offset)
                        data = budget.read(stream, size, kind=kind)
                        if len(data) != size:
                            file['coverage'] = 'partial'; return
                        windows.append(hashlib.sha256(data).hexdigest())
                        window_bytes.append(data)
                    if windows != [file.get('prefix_digest'), file.get('tail_digest')]:
                        file.update(counter_state={}, dedup_state={}, last_complete_record_offset=0)
                        file['errors'].append('uncertain_middle_mutation')
                        self._pending.pop(file['path'], None)
                        window_bytes = []
                offset = file['last_complete_record_offset']
                pending = self._pending.pop(file['path'], b'')
                stream.seek(offset + len(pending))
                first = not offset
                prefix = bytearray()
                tail = bytearray()
                # New validation windows are collected from the parse bytes, not
                # reread; warm cost remains old windows plus appended bytes.
                if window_bytes and offset >= old_offset:
                    prefix = bytearray(window_bytes[0])
                    tail = bytearray(window_bytes[1])
                while budget.available():
                    if b'\n' not in pending:
                        part = budget.read(stream, min(65536, MAX_RECORD_BYTES - len(pending)))
                        pending += part
                        if not part:
                            break
                    if b'\n' not in pending:
                        if len(pending) >= MAX_RECORD_BYTES:
                            file['errors'].append('oversized_record')
                            break
                        continue
                    raw, pending = pending.split(b'\n', 1)
                    consumed = raw + b'\n'
                    if len(consumed) > MAX_RECORD_BYTES:
                        file['errors'].append('oversized_record'); break
                    if not budget.available():
                        pending = consumed + pending; break
                    budget.parsed_records += 1
                    budget.max_record_bytes = max(budget.max_record_bytes, len(consumed))
                    if len(prefix) < 4096:
                        prefix.extend(consumed[:4096 - len(prefix)])
                    tail.extend(consumed); del tail[:-4096]
                    offset += len(consumed)
                    try:
                        row = json.loads(raw)
                        if isinstance(row, dict):
                            self._consume(file, row)
                    except (ValueError, TypeError, KeyError, AttributeError):
                        file['errors'].append('invalid_counter_record')
                    first = False
                file['last_complete_record_offset'] = offset
                self._pending[file['path']] = pending
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    file['errors'].append('native_source_changed')
                complete = offset == before.st_size and not pending
                # Store windows only for fully consumed bytes. Raw transcript
                # content is never serialized, even the validation windows.
                if offset:
                    file.update(size=before.st_size, mtime_ns=before.st_mtime_ns,
                                file_identity=identity, prefix_digest=hashlib.sha256(prefix).hexdigest(),
                                tail_digest=hashlib.sha256(tail).hexdigest())
                file['coverage'] = 'complete' if complete and not file['errors'] else 'partial'
                file['continuation'] = offset
                file['errors'] = sorted(set(file['errors']))
        except (OSError, ValueError):
            file['errors'] = sorted(set(file['errors'] + ['native_source_unavailable']))
            file['coverage'] = 'unknown'

    def _sessions(self, budget: UsageBudget) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for file in self.state['files'].values():
            meta = file.get('metadata') or {}
            if meta.get('session_id') not in self.known or file.get('coverage') == 'unrelated':
                continue
            grouped.setdefault(meta['session_id'], []).append(file)
        result = []
        zero = dict.fromkeys(USAGE_KEYS, 0)
        cache = self.state.setdefault('session_summaries', {})
        for sid, files in grouped.items():
            signature = [[f['path'], f.get('counter_revision', 0), f['errors']] for f in files]
            cached = cache.get(sid)
            current_complete = self.state['fully_discovered'] and all(f['coverage'] == 'complete' for f in files)
            if cached and cached['signature'] == signature:
                row = deepcopy(cached['row'])
                if not current_complete:
                    row.update(status='partial', attribution_unknown=True)
                result.append(row)
                continue
            if not budget.available():
                meta = files[0]['metadata']
                row = deepcopy(cached['row']) if cached else {
                    'session': sid, 'agent': meta.get('agent_path') or 'lens',
                    'role': 'orchestrator' if sid == self.root else 'lens',
                    'usage': None, 'native_usage': None, 'errors': [], 'measured_at': None,
                    'measurement_source': 'native_sample', 'basis': 'incremental owned interval [start, end)',
                    'attribution_schema': 'taskplane.owned-interval/v1', 'responses': 0, 'baseline': None,
                    'interval': {'start': self.start, 'end_exclusive': self.end}}
                row.update(status='partial', attribution_unknown=True,
                           errors=sorted(set(row['errors'] + ['aggregation_budget_exhausted'])))
                result.append(row)
                continue
            errors = sorted({e for f in files for e in f['errors']})
            complete = self.state['fully_discovered'] and all(f['coverage'] == 'complete' for f in files)
            native, usage, measured = None, None, None
            baseline = None
            baseline_observed = False
            response_count = 0
            if self.host == 'Codex':
                values: dict[str, Any] = {}
                responses: dict[str, Any] = {}
                for file in files:
                    for key, item in file['counter_state'].items():
                        if key in values and values[key] != item: errors.append('conflicting_counter')
                        values[key] = item
                    for key, item in file['dedup_state'].items():
                        if key in responses and responses[key] != item: errors.append('conflicting_response')
                        responses[key] = item
                ordered = sorted(values.values(), key=lambda item: (item[0], item[1]))
                if any(any(b[2][k] < a[2][k] for k in USAGE_KEYS) for a,b in zip(ordered, ordered[1:])):
                    errors.append('counter_reset')
                if ordered:
                    native = ordered[-1][2]; measured = ordered[-1][0]
                prior = [item[2] for item in ordered if self.start is not None and item[0] < self.start]
                baseline_observed = bool(prior)
                baseline = prior[-1] if prior else zero if not any(f['metadata'].get('resumed') for f in files) else None
                if responses:
                    usage = {k: sum(v[k] for at,v in responses.values() if self.start is None or at >= self.start) for k in USAGE_KEYS}
                    response_count = sum(self.start is None or at >= self.start for at,_ in responses.values())
                    if baseline is None: errors.append('resumed_baseline_unavailable')
                    elif native is not None and any(usage[k] != native[k]-baseline[k] for k in USAGE_KEYS):
                        errors.append('owned_counter_mismatch')
                elif native is not None and baseline is not None and not errors:
                    usage = {k: native[k] - baseline[k] for k in USAGE_KEYS}
                unresumed = [f for f in files if not f['metadata'].get('resumed')]
                if len(unresumed) > 1:
                    if any(f['counter_state'] != unresumed[0]['counter_state'] for f in unresumed[1:]):
                        errors.append('native_source_replacement_without_restart')
                if errors and not responses: usage = None
            else:
                messages: dict[str, Any] = {}
                for file in files:
                    for key, value in file['dedup_state'].items():
                        if key in messages and messages[key] != value: errors.append('conflicting_message_segment')
                        messages[key] = value
                native = dict(zero); usage = dict(zero)
                native_seen = owned_seen = False
                baseline_observed = any(value['before'] for value in messages.values())
                for value in messages.values():
                    before, after = value['before'], value['after']
                    last = after or before
                    if last:
                        native_seen = True
                        measured = max(measured or last[0], last[0])
                        for k in USAGE_KEYS: native[k] += last[1][k]
                    if after:
                        response_count += 1
                        if value.get('decreased'):
                            continue
                        if value.get('unknown_before'):
                            errors.append('message_boundary_unavailable')
                            continue
                        base = before[1] if before else zero
                        if any(after[1][k] < base[k] for k in USAGE_KEYS):
                            errors.append('counter_decreased')
                            continue
                        for k in USAGE_KEYS: usage[k] += after[1][k] - base[k]
                        owned_seen = True
                    elif before:
                        owned_seen = True  # Observed message, with no interval increment.
                if not native_seen: native = None
                if not owned_seen: usage = None
            meta = files[0]['metadata']
            result.append({'session': sid, 'agent': meta.get('agent_path') or ('orchestrator' if sid == self.root else 'lens'),
                'role': 'orchestrator' if sid == self.root else 'host_approval_review' if meta.get('thread_source') == 'guardian_review' else 'lens',
                'usage': usage, 'native_usage': native,
                'status': 'partial' if errors or not complete else 'measured' if native is not None else 'unavailable',
                'errors': sorted(set(errors)), 'attribution_unknown': bool(errors or not complete),
                'measured_at': datetime.fromtimestamp(measured, timezone.utc).isoformat() if measured is not None else None,
                'measurement_source': 'native_sample', 'basis': 'incremental owned interval [start, end)',
                'responses': response_count, 'baseline': baseline,
                'baseline_observed': baseline_observed, 'started_at': meta.get('started_at'),
                'attribution_schema': 'taskplane.owned-interval/v1',
                'interval': {'start': self.start, 'end_exclusive': self.end}})
            # Cache the intrinsic sample status; per-refresh validation coverage
            # is applied separately, so an exhausted pass cannot freeze it.
            cached_row = deepcopy(result[-1])
            if not errors and native is not None:
                cached_row.update(status='measured', attribution_unknown=False)
            cache[sid] = {'signature': deepcopy(signature), 'row': cached_row}
        for sid, reason in self.state['invalidated_sessions'].items():
            row = next((item for item in result if item['session'] == sid), None)
            if row is None:
                # An owned file may be replaced by unrelated metadata and be
                # pruned from the inventory. Its lost ownership remains known.
                row = {'session': sid, 'agent': 'orchestrator' if sid == self.root else 'lens',
                    'role': 'orchestrator' if sid == self.root else 'lens', 'usage': None,
                    'native_usage': None, 'errors': [], 'measured_at': None,
                    'measurement_source': 'native_sample', 'basis': 'owned source invalidated',
                    'attribution_schema': 'taskplane.owned-interval/v1', 'responses': 0, 'baseline': None,
                    'interval': {'start': self.start, 'end_exclusive': self.end}}
                result.append(row)
            row.update(status='partial', attribution_unknown=True,
                       errors=sorted(set(row['errors'] + [reason])))
        # A known worker remains part of coverage even when discovery finds no
        # readable file for it. Absence cannot certify complete token coverage.
        observed = {row['session'] for row in result}
        for sid in sorted(self.known - observed):
            result.append({'session': sid, 'agent': 'orchestrator' if sid == self.root else 'lens',
                'role': 'orchestrator' if sid == self.root else 'lens', 'usage': None,
                'native_usage': None, 'status': 'partial', 'attribution_unknown': True,
                'errors': ['native counter unavailable'], 'measured_at': None,
                'measurement_source': 'native_sample', 'basis': 'known session; native counter unavailable',
                'attribution_schema': 'taskplane.owned-interval/v1', 'responses': 0, 'baseline': None,
                'interval': {'start': self.start, 'end_exclusive': self.end}})
        return deepcopy(result)

    def refresh(self, budget: UsageBudget | None = None) -> dict[str, Any]:
        with usage_refresh(budget) as shared:
            previous = self.state.get('continuation')
            for file in self.state['files'].values():
                if file.get('coverage') == 'complete':
                    file['coverage'] = 'partial'
            self._discover(shared)
            # Metadata inventory is a separate bounded stage. Resolving the
            # transitive closure never rereads unrelated transcript content.
            for file in self.state['files'].values():
                if not shared.available(): break
                if not file.get('metadata'):
                    try:
                        with _safe_open(file['path']) as stream:
                            self._metadata(file, stream, shared)
                    except (OSError, ValueError):
                        file['coverage'] = 'unknown'; file['errors'] = ['native_metadata_unavailable']
            while True:
                found = self.known | {f['metadata']['session_id'] for f in self.state['files'].values()
                    if f.get('metadata') and (f['metadata'].get('parent_session_id') in self.known
                        or self.expected_root is not None and f['metadata'].get('validation_error') == 'native_parent_mismatch')}
                if found == self.known: break
                self.known = found
            for file in self.state['files'].values():
                if not shared.available(): break
                if (file.get('metadata') or {}).get('session_id') in self.known:
                    self._file(file, shared)
                elif file.get('metadata'):
                    file['coverage'] = 'unrelated'; self._pending.pop(file['path'], None)
            metadata_done = all(f.get('metadata') for f in self.state['files'].values())
            self.state['fully_discovered'] = not self.state['directories'] and metadata_done and not self.state['discovery_errors']
            if self.state['fully_discovered']:
                for file in self.state['files'].values():
                    meta = file['metadata']
                    if meta['session_id'] not in self.known and meta.get('validation_error'):
                        self.state['inventory_errors'] += 1
                        if len(self.state['inventory_diagnostics']) < 32:
                            self.state['inventory_diagnostics'].append({
                                'scope': 'unrelated_inventory', 'session': meta['session_id'],
                                'reason': meta['validation_error']})
                self.state['files'] = {p:f for p,f in self.state['files'].items()
                    if f['metadata']['session_id'] in self.known}
            sessions = self._sessions(shared)
            complete = (self.state['fully_discovered'] and not self.state['discovery_errors']
                and bool(sessions) and all(s['status'] == 'measured' for s in sessions)
                and shared.available())
            self.state['serial'] += 1
            continuation = None if complete else _fingerprint([self.binding, self.state['serial'],
                [(p, f['last_complete_record_offset']) for p,f in self.state['files'].items()]])
            self.state['continuation'] = continuation
            known_usage = [s['usage'] for s in sessions if s['usage'] is not None]
            totals = ({k: sum(usage[k] for usage in known_usage) for k in USAGE_KEYS}
                      if known_usage else None)
            attribution = {s['session']: s['usage'] for s in sessions}
            return {'sessions': sessions, **shared.report(), 'complete': complete,
                    'fully_discovered': self.state['fully_discovered'], 'continuation': continuation,
                    'discovery_errors': list(self.state['discovery_errors']),
                    'inventory_diagnostics': deepcopy(self.state['inventory_diagnostics']),
                    'inventory_errors': self.state['inventory_errors'],
                    'resumed_from': previous, 'totals': totals, 'attribution': attribution,
                    'checkpoint_invalidated': self.checkpoint_invalidated,
                    'trust_assumptions': APPEND_ONLY_TRUST, 'coverage': 'complete' if complete else 'partial'}


def run_sessions(rows: list[dict[str, Any]], root: str, known: Iterable[str], *,
                 start: float | None, end: float | None) -> list[dict[str, Any]]:
    """Keep declared workers and descendants with evidence of this run's work.

    A lifetime-only counter from an older discovered worker does not establish
    ownership. Explicit workers remain visible even when their counter is absent.
    """
    declared = set(known) | {root}
    selected = []
    for row in rows:
        try:
            created = _instant(row.get('started_at'))
        except (TypeError, ValueError):
            created = None
        born = (created is not None and (start is None or created >= start)
                and (end is None or created < end))
        if (row['session'] in declared or row['role'] == 'host_approval_review'
                or 'native_parent_mismatch' in row.get('errors', [])):
            if (row['session'] != root and row['role'] != 'host_approval_review'
                    and start is not None and not born and not row.get('baseline_observed')
                    and not row.get('responses') and row.get('usage') is not None):
                row.update(usage=None, status='partial', attribution_unknown=True,
                           errors=sorted(set(row.get('errors', []) + ['owned_baseline_unavailable'])))
            selected.append(row)
            continue
        if born or row.get('responses') or row.get('baseline_observed') and row.get('usage') and any(row['usage'].values()):
            selected.append(row)
    return selected


def _bounded_read(stream: BinaryIO, amount: int) -> bytes:
    budget = _ACTIVE_BUDGET.get()
    assert budget is not None
    data = bytearray()
    while len(data) < amount and budget.available():
        part = budget.read(stream, amount-len(data), kind='snapshot')
        if not part: break
        data.extend(part)
    if len(data) != amount:
        raise NativeSessionMeterError('aggregate usage budget exhausted')
    return bytes(data)


def read_snapshot(path: str, *, at_or_before: float | None = None,
                  allow_unsequenced: bool = False) -> dict[str, Any]:
    with usage_refresh():
        return _snapshot(path, at_or_before=at_or_before, allow_unsequenced=allow_unsequenced)


def read_logical_snapshot(paths: Sequence[str | Path], session_id: str, *,
                          at_or_before: float | None = None) -> dict[str, Any]:
    with usage_refresh():
        return _logical_snapshot(paths, session_id, at_or_before=at_or_before)


def read_owned_interval(paths: Sequence[str | Path], session_id: str, *,
                        start: float, end: float | None = None) -> dict[str, Any]:
    reader = IncrementalUsageReader('Codex', session_id, start=start, end=end)
    for path in paths: reader.add_file(path)
    with usage_refresh(UsageBudget(bytes_limit=min(MAX_REPLAY_BYTES, 16*1024*1024))):
        result = reader.refresh()
    row = next((s for s in result['sessions'] if s['session'] == session_id), None)
    row = row or {'usage': None, 'native_usage': None, 'status': 'partial',
                  'errors': ['native counter unavailable'], 'measured_at': None}
    return {**row, 'bytes_read': result['read_bytes'],
            'basis': 'incremental owned interval [start, end)',
            'interval': {'start': start, 'end_exclusive': end},
            'usage_refresh': result, 'checkpoint': reader.checkpoint()}
