"""Read native Codex counters and lineage without retaining conversation content."""
from __future__ import annotations
import codecs
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
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

# Only the oversized compaction path uses this validator. It retains syntax and
# small envelope fields, never discarded strings or copied token counters.
_COMPACTION_DEPTH = 128
_COMPACTION_TOKEN = 512
_COMPACTION_KEYS = 64
_COMPACTION_STATE_BYTES = 8192
_STRING_SPECIAL = re.compile(r'["\\\x00-\x1f]')
_JSON_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z')


class _CompactionValidator:
    """Resumable JSON syntax validation with one deliberately narrow outcome."""

    def __init__(self, saved: Any = None):
        self.state: dict[str, Any] = {
            'version': 1, 'stack': [], 'root': 'value', 'mode': '', 'token': '',
            'capture': '', 'escape': 0, 'utf8': [], 'keys': [], 'key': '',
            'type': '', 'payload': False,
        }
        if saved is not None:
            self._restore(saved)

    def _restore(self, saved: Any) -> None:
        bad = 'invalid_compaction_state'
        if not isinstance(saved, dict) or saved.keys() != self.state.keys():
            raise ValueError(bad)
        if len(json.dumps(saved, separators=(',', ':'))) > _COMPACTION_STATE_BYTES:
            raise ValueError(bad)
        if (saved['version'] != 1 or type(saved['version']) is not int
                or saved['root'] not in ('value', 'active', 'done')
                or saved['mode'] not in ('', 'string', 'primitive')
                or saved['capture'] not in ('', 'key', 'type')
                or type(saved['escape']) is not int or not 0 <= saved['escape'] <= 5
                or type(saved['payload']) is not bool):
            raise ValueError(bad)
        for key in ('token', 'key', 'type'):
            if not isinstance(saved[key], str) or len(saved[key]) > _COMPACTION_TOKEN:
                raise ValueError(bad)
        stack = saved['stack']
        if not isinstance(stack, list) or len(stack) > _COMPACTION_DEPTH:
            raise ValueError(bad)
        for frame in stack:
            if (not isinstance(frame, list) or len(frame) != 2
                    or frame[0] not in ('object', 'array')
                    or frame[1] not in ({'key_or_end', 'key', 'colon', 'value', 'comma_or_end'}
                                       if frame[0] == 'object' else
                                       {'value_or_end', 'value', 'comma_or_end'})):
                raise ValueError(bad)
        keys = saved['keys']
        if (not isinstance(keys, list) or len(keys) > _COMPACTION_KEYS
                or any(not isinstance(k, str) or len(k) > 128 for k in keys)
                or sum(map(len, keys)) > 2048
                or len(set(keys)) != len(keys)):
            raise ValueError(bad)
        utf8 = saved['utf8']
        if (not isinstance(utf8, list) or len(utf8) > 3
                or any(type(b) is not int or not 0 <= b <= 255 for b in utf8)):
            raise ValueError(bad)
        decoder = codecs.getincrementaldecoder('utf-8')()
        try:
            decoder.decode(bytes(utf8))
        except UnicodeDecodeError as exc:
            raise ValueError(bad) from exc
        if decoder.getstate()[0] != bytes(utf8):
            raise ValueError(bad)
        if (bool(stack) != (saved['root'] == 'active')
                or stack and stack[0][0] != 'object'
                or saved['mode'] and not stack
                or saved['mode'] != 'string' and (saved['escape'] or saved['capture'])
                or not saved['mode'] and saved['token']
                or saved['mode'] == 'string' and not saved['capture'] and saved['token']
                or saved['root'] == 'done' and (utf8 or saved['mode'])
                or saved['type'] not in ('', 'compacted')
                or saved['type'] and 'type' not in keys
                or saved['payload'] and 'payload' not in keys):
            raise ValueError(bad)
        if stack:
            kind, expect = stack[-1]
            if (saved['mode'] == 'primitive' and (expect not in ('value', 'value_or_end')
                                                  or not saved['token'])
                    or saved['mode'] == 'string' and expect not in ('key', 'key_or_end', 'value', 'value_or_end')
                    or saved['capture'] == 'key' and (len(stack) != 1 or expect not in ('key', 'key_or_end'))
                    or saved['capture'] == 'type' and (len(stack) != 1 or expect != 'value' or saved['key'] != 'type')
                    or len(stack) == 1 and saved['mode'] == 'string' and expect in ('key', 'key_or_end')
                    and saved['capture'] != 'key'
                    or saved['key'] and saved['key'] not in keys):
                raise ValueError(bad)
        self.state = saved

    def _token(self, text: str) -> None:
        self.state['token'] += text
        if len(self.state['token']) > _COMPACTION_TOKEN:
            raise ValueError('compaction_state_limit')

    def _value_done(self) -> None:
        s = self.state
        if s['stack']:
            s['stack'][-1][1] = 'comma_or_end'
        else:
            s['root'] = 'done'

    def _string_done(self) -> None:
        s = self.state
        frame = s['stack'][-1]
        if frame[0] == 'object' and frame[1] in ('key', 'key_or_end'):
            if s['capture']:
                key = json.loads('"' + s['token'] + '"')
                if key in s['keys']:
                    raise ValueError('ambiguous_compaction_record')
                if (len(key) > 128 or len(s['keys']) >= _COMPACTION_KEYS
                        or sum(map(len, s['keys'])) + len(key) > 2048):
                    raise ValueError('compaction_state_limit')
                s['keys'].append(key)
                s['key'] = key
            frame[1] = 'colon'
        else:
            if s['capture'] == 'type':
                if json.loads('"' + s['token'] + '"') != 'compacted':
                    raise ValueError('oversized_record')
                s['type'] = 'compacted'
            self._value_done()
        s.update(mode='', token='', capture='')

    def feed(self, raw: bytes) -> None:
        s = self.state
        decoder = codecs.getincrementaldecoder('utf-8')()
        decoder.setstate((bytes(s['utf8']), 0))
        text = decoder.decode(raw)
        s['utf8'] = list(decoder.getstate()[0])
        i = 0
        while i < len(text):
            c = text[i]
            if s['mode'] == 'string':
                if s['escape']:
                    if s['capture']:
                        self._token(c)
                    if s['escape'] == 5:
                        if c == 'u':
                            s['escape'] = 4
                        elif c in '"\\/bfnrt':
                            s['escape'] = 0
                        else:
                            raise ValueError('invalid_compaction_record')
                    elif c in '0123456789abcdefABCDEF':
                        s['escape'] -= 1
                    else:
                        raise ValueError('invalid_compaction_record')
                    i += 1
                    continue
                match = _STRING_SPECIAL.search(text, i)
                end = match.start() if match else len(text)
                if s['capture']:
                    self._token(text[i:end])
                i = end
                if not match:
                    continue
                c = text[i]
                if c == '"':
                    self._string_done()
                elif c == '\\':
                    if s['capture']:
                        self._token(c)
                    s['escape'] = 5
                else:
                    raise ValueError('invalid_compaction_record')
                i += 1
                continue
            if s['mode'] == 'primitive':
                if c not in ' \t\r\n,]}':
                    self._token(c)
                    i += 1
                    continue
                if s['token'] not in ('true', 'false', 'null') and not _JSON_NUMBER.fullmatch(s['token']):
                    raise ValueError('invalid_compaction_record')
                s.update(mode='', token='')
                self._value_done()
                continue
            if c in ' \t\r\n':
                i += 1
                continue
            stack = s['stack']
            if not stack:
                if s['root'] != 'value' or c != '{':
                    raise ValueError('invalid_compaction_record')
                s['root'] = 'active'
                stack.append(['object', 'key_or_end'])
                i += 1
                continue
            kind, expect = stack[-1]
            close = '}' if kind == 'object' else ']'
            if c == close and expect in ('key_or_end', 'value_or_end', 'comma_or_end'):
                stack.pop()
                self._value_done()
            elif expect in ('key', 'key_or_end') and c == '"':
                s.update(mode='string', capture='key' if len(stack) == 1 else '')
            elif expect == 'colon' and c == ':':
                stack[-1][1] = 'value'
            elif expect == 'comma_or_end' and c == ',':
                stack[-1][1] = 'key' if kind == 'object' else 'value'
            elif expect in ('value', 'value_or_end'):
                outer = len(stack) == 1
                if outer and s['key'] == 'payload':
                    if c != '{':
                        raise ValueError('invalid_compaction_record')
                    s['payload'] = True
                if outer and s['key'] == 'type' and c != '"':
                    raise ValueError('oversized_record')
                if c in '{[':
                    if len(stack) >= _COMPACTION_DEPTH:
                        raise ValueError('compaction_state_limit')
                    stack.append(['object', 'key_or_end'] if c == '{' else ['array', 'value_or_end'])
                elif c == '"':
                    s.update(mode='string', capture='type' if outer and s['key'] == 'type' else '')
                elif c in '-0123456789tfn':
                    s.update(mode='primitive', token=c)
                else:
                    raise ValueError('invalid_compaction_record')
            else:
                raise ValueError('invalid_compaction_record')
            i += 1

        if len(json.dumps(s, separators=(',', ':'))) > _COMPACTION_STATE_BYTES:
            raise ValueError('compaction_state_limit')

    def finish(self) -> None:
        s = self.state
        if s['root'] != 'done' or s['mode'] or s['utf8']:
            raise ValueError('invalid_compaction_record')
        if s['type'] != 'compacted' or not s['payload']:
            raise ValueError('oversized_record')

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

class NativeIdentityError(NativeSessionMeterError):
    """Observed identity is invalid; source loss must not revive cached usage."""

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
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        if not isinstance(row, Mapping) or row.get("type") != "session_meta":
            continue
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        session_id = str(payload.get("id") or "").strip()
        root_id = str(payload.get("session_id") or session_id).strip()
        if not session_id or not root_id:
            raise NativeIdentityError("native session identity is missing")
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
            raise NativeIdentityError("native restart identity is invalid")
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
        except (UnicodeDecodeError, ValueError, RecursionError):
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
    if __package__:
        from . import codex_usage_cache as _package_cache
        codex_usage_cache = _package_cache
    else:
        import codex_usage_cache as _flat_cache
        codex_usage_cache = _flat_cache
    with codex_usage_cache.action_budget() as budget:
        return _read_snapshot(path, at_or_before=at_or_before,
                              allow_unsequenced=allow_unsequenced, budget=budget)


def _read_snapshot(path: str, *, at_or_before: float | None,
                   allow_unsequenced: bool, budget: Any) -> dict[str, Any]:
    """Read one identity and bounded counter, optionally at an authenticated stop."""
    selected = os.path.realpath(str(path or ""))
    try:
        budget.take('files')
        if any(p.is_symlink() for p in (Path(path), *Path(path).parents)):
            raise NativeSessionMeterError('symlinked native snapshot')
        fd = os.open(selected, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise NativeSessionMeterError("native session source is not a regular file")
            prefix_size = min(before.st_size, MAX_METADATA_BYTES)
            tail_size = min(before.st_size, MAX_COUNTER_TAIL_BYTES + 1)
            budget.take('source_bytes', prefix_size + tail_size)
            prefix = stream.read(prefix_size)
            tail_offset = max(0, before.st_size - MAX_COUNTER_TAIL_BYTES)
            stream.seek(tail_offset)
            tail = stream.read(tail_size)
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
    if __package__:
        from . import codex_usage_cache as _package_cache
        cache = _package_cache
    else:
        import codex_usage_cache as _flat_cache
        cache = _flat_cache
    with cache.action_budget():
        return _read_logical_snapshot(paths, session_id, at_or_before=at_or_before)


def _read_logical_snapshot(paths: Sequence[str | Path], session_id: str, *,
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


def read_owned_interval(paths: Sequence[str | Path], session_id: str, *,
                        start: float, end: float | None = None,
                        workspace: str | Path | None = None, budget: Any = None) -> dict[str, Any]:
    """Resume metadata-only replay for the exact half-open ownership interval."""
    if __package__:
        from . import codex_usage_cache as _package_cache
        codex_usage_cache = _package_cache
    else:
        import codex_usage_cache as _flat_cache
        codex_usage_cache = _flat_cache
    with codex_usage_cache.action_budget(workspace, budget=budget) as allowance:
        return codex_usage_cache.interval(paths, session_id, start=start, end=end, budget=allowance)


def owned_counts(raw: Any) -> dict[str, int]:
    """Normalize response and cumulative vectors using the same category rules."""
    if not isinstance(raw, Mapping):
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


def counter_pair(before: Mapping[str, Any], after: Mapping[str, Any], label: str) -> None:
    """Validate adjacent canonical boundaries, including every token category."""
    a, b = _counter_order(before), _counter_order(after)
    if (a == b or (a[0] == b[0] and
            any(row.get("ordinal_basis") != "native" for row in (before, after)))):
        if before["usage"] != after["usage"]:
            raise CounterChronologyError(f"conflicting {label} counter boundary")
    if any(after["usage"][key] < before["usage"][key] for key in USAGE_KEYS):
        raise CounterChronologyError(f"{label} counter moved backwards")


def validate_segment_lineage(rows: Sequence[Mapping[str, Any]]) -> None:
    """Prove physical replacement and logical lineage for snapshots and replay."""
    ordered = sorted(rows, key=lambda row: (*_counter_order(row), bool(row.get("resumed"))))
    for field in ("session_id", "parent_session_id", "agent_path"):
        if len({row[field] for row in ordered if row.get(field)}) > 1:
            raise CounterChronologyError("native task segment lineage disagrees")
    distinct, physical, records = [], set(), set()
    for row in ordered:
        source = row["source"]
        identity = (source["device"], source["inode"])
        if identity in physical:
            continue
        physical.add(identity)
        record = (source["metadata_record_sha256"], source["counter_record_sha256"])
        if row.get("counter_scope") == "thread":
            if record in records:
                continue
            records.add(record)
        distinct.append(row)
    if any(not row.get("resumed") for row in distinct[1:]):
        raise CounterChronologyError("native source replacement has no restart evidence")


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
        counter_pair(before, after, label)
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
        validate_segment_lineage(rows)
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
