"""Disposable, bounded Claude usage index. No transcript content is persisted.

The source is an append-only native log. Fixed prefix/cursor anchors detect common
replacement/truncation cases, not arbitrary edits to the historical middle. Each
source/filter gets an isolated SQLite database; generations and interval reducers
are indexed and never loaded wholesale. Old generations are left for offline GC.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import uuid
from typing import Any, BinaryIO, Callable, Iterator

from taskplane.native_session_meter import USAGE_KEYS
from taskplane.spend import normalize_usage

MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 1024
MAX_RECORD_BYTES = 1024 * 1024
MAX_ROWS = 4096
MAX_ENTRIES = 128
MAX_VM_STEPS = 2_000_000
ANCHOR_BYTES = 256
MAX_RECOVERY_BYTES = 8 * 1024 * 1024
SCHEMA = 1


@dataclass
class Budget:
    max_bytes: int = MAX_BYTES
    max_records: int = MAX_RECORDS
    max_rows: int = MAX_ROWS
    max_entries: int = MAX_ENTRIES
    max_vm_steps: int = MAX_VM_STEPS
    max_recovery_bytes: int = MAX_RECOVERY_BYTES
    source_bytes: int = 0
    recovery_bytes: int = 0
    records: int = 0
    rows: int = 0
    entries: int = 0
    vm_steps: int = 0
    workspace: Path | None = None
    discovery_incomplete: bool = False
    # At most max_entries snapshots/identities, never historical transcript rows.
    files: int = 0

    def take(self, name: str, count: int = 1) -> None:
        limit = {'source_bytes': self.max_bytes, 'records': self.max_records,
                 'rows': self.max_rows, 'entries': self.max_entries,
                 'vm_steps': self.max_vm_steps, 'files': self.max_entries,
                 'recovery_bytes': self.max_recovery_bytes}[name]
        if getattr(self, name) + count > limit:
            raise Exhausted(name)
        setattr(self, name, getattr(self, name) + count)


class Exhausted(ValueError):
    pass


_CURRENT: ContextVar[Budget | None] = ContextVar('claude_usage_budget', default=None)


@contextmanager
def action_budget(workspace: str | Path | None = None, *, budget: Budget | None = None) -> Iterator[Budget]:
    current = _CURRENT.get()
    if current is not None:
        # A caller may learn the validated workspace after entering a CLI action.
        if current.workspace is None and workspace is not None:
            current.workspace = Path(workspace).resolve()
        yield current
        return
    current = budget or Budget()
    if workspace is not None:
        current.workspace = Path(workspace).resolve()
    token = _CURRENT.set(current)
    try:
        yield current
    finally:
        _CURRENT.reset(token)


def timestamp(value: Any) -> float | None:
    try:
        value = datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def cache_path(path: str | Path, session: str, agent: str | None, workspace: Path | None = None) -> Path:
    binding = [str(Path(path).resolve()), session, agent, str(Path(workspace).resolve()) if workspace else None, SCHEMA]
    name = 'claude-usage-' + hashlib.sha256(json.dumps(binding).encode()).hexdigest() + '.sqlite3'
    if workspace is not None:
        from taskplane.storage import runtime_file
        return runtime_file(str(workspace), name)
    # Standalone readers never write alongside native logs or infer a workspace.
    base = Path(tempfile.gettempdir()) / ('taskplane-usage-' + str(getattr(os, 'getuid', lambda: 0)()))
    base.mkdir(mode=0o700, exist_ok=True)
    if base.is_symlink() or hasattr(os, 'getuid') and base.stat().st_uid != os.getuid():
        raise ValueError('unsafe standalone cache directory')
    return base / name


def rotation(path: Path, session: str, budget: Budget) -> int:
    """A fixed-size advisory cursor rotates the bounded candidate page fairly.

    Directory APIs do not provide portable durable enumeration cookies. Oversize
    inventories are explicitly incomplete; supplied worker/path identities remain
    preferred, while this cursor prevents one large root from starving known
    candidates across fresh processes.
    """
    location = cache_path(path, session, 'discovery-cursor', budget.workspace).with_suffix('.cursor')
    location.parent.mkdir(parents=True, exist_ok=True)
    if location.is_symlink():
        return 0
    try:
        with location.open('rb') as stream:
            raw = stream.read(32)
        value = int(raw)
        if not 0 <= value < 2 ** 63:
            value = 0
    except (OSError, ValueError):
        value = 0
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=location.name, dir=location.parent)
        with os.fdopen(fd, 'w') as stream:
            stream.write(str((value + 1) % (2 ** 63)))
        os.replace(temporary, location)
    except OSError:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return value


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'), allow_nan=False)


def _load(value: str) -> Any:
    if len(value) > 16384:
        raise ValueError('oversized cache metadata')
    return json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite metadata')))


def _vector(raw: str) -> dict[str, int]:
    value = _load(raw)
    if (not isinstance(value, dict) or set(value) != set(USAGE_KEYS)
            or any(type(value[k]) is not int or value[k] < 0 for k in USAGE_KEYS)):
        raise ValueError('invalid cached token vector')
    return {k: value[k] for k in USAGE_KEYS}


def _checked_contribution(raw: str) -> dict[str, Any]:
    value = _load(raw)
    if (not isinstance(value, dict) or set(value) != {'value', 'active', 'error', 'time'}
            or type(value['active']) is not bool
            or value['error'] not in (None, 'counter_decreased', 'missing_message_usage', 'missing_message_boundary')
            or value['time'] is not None and (type(value['time']) not in (int, float) or not math.isfinite(value['time']))):
        raise ValueError('invalid cached contribution')
    if value['value'] is not None:
        value['value'] = _vector(_dump(value['value']))
    return dict(value)


def _zero() -> dict[str, int]:
    return dict.fromkeys(USAGE_KEYS, 0)


def _value(raw: Any) -> dict[str, int] | None:
    try:
        usage = normalize_usage(raw if isinstance(raw, dict) else {}, provider='claude')
    except (ValueError, TypeError, OverflowError):
        return None
    if not usage['available']:
        return None
    uncached = usage['uncached_input_tokens'] + usage['cache_creation_tokens']
    value = dict(input_tokens=uncached + usage['cached_input_tokens'],
                 uncached_input_tokens=uncached, cached_input_tokens=usage['cached_input_tokens'],
                 output_tokens=usage['output_tokens'], reasoning_tokens=usage['reasoning_tokens'],
                 total_tokens=usage['raw_total_tokens'])
    return value if len(_dump(value)) <= 8192 else None


def _read(stream: BinaryIO, size: int, budget: Budget) -> bytes:
    size = min(size, budget.max_bytes - budget.source_bytes)
    if size <= 0:
        raise Exhausted('source_bytes')
    data = stream.read(size)
    budget.take('source_bytes', len(data))
    return data


def _anchor(stream: BinaryIO, offset: int, budget: Budget) -> str:
    if not offset:
        return ''
    stream.seek(max(0, offset - ANCHOR_BYTES))
    amount = min(offset, ANCHOR_BYTES)
    if budget.max_bytes - budget.source_bytes < amount:
        raise Exhausted('source_bytes')
    return hashlib.sha256(_read(stream, amount, budget)).hexdigest()


class _Lines:
    """Finite chunk reads with one small lookahead buffer, charged when read."""
    def __init__(self, stream: BinaryIO, target: int, budget: Budget) -> None:
        self.stream, self.target, self.budget = stream, target, budget
        self.buffer = b''

    def readline(self, limit: int) -> bytes:
        parts = []
        remaining = limit
        while remaining:
            if not self.buffer:
                size = min(65536, self.target - self.stream.tell(),
                           self.budget.max_bytes - self.budget.source_bytes - 2 * ANCHOR_BYTES)
                if size <= 0:
                    break
                self.buffer = _read(self.stream, size, self.budget)
                if not self.buffer:
                    break
            end = self.buffer.find(b'\n', 0, remaining)
            size = end + 1 if end >= 0 else min(remaining, len(self.buffer))
            parts.append(self.buffer[:size])
            self.buffer = self.buffer[size:]
            remaining -= size
            if end >= 0:
                break
        return b''.join(parts)


def _schema(db: sqlite3.Connection) -> None:
    db.executescript('''
    CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS samples (
      gen TEXT, mid TEXT, at REAL, pos INTEGER, value TEXT, bad INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(gen,mid,at,pos)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS valid_samples ON samples(gen,mid,at,pos) WHERE value IS NOT NULL;
    CREATE INDEX IF NOT EXISTS bad_samples ON samples(gen,mid,at,pos) WHERE bad=1;
    CREATE TABLE IF NOT EXISTS changes (gen TEXT, rev INTEGER, mid TEXT, PRIMARY KEY(gen,rev)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS queries (gen TEXT, qid TEXT, body TEXT, PRIMARY KEY(gen,qid)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS contributions (
      gen TEXT, qid TEXT, mid TEXT, body TEXT, PRIMARY KEY(gen,qid,mid)) WITHOUT ROWID;
    ''')


def _one(db: sqlite3.Connection, budget: Budget, sql: str, args: tuple[Any, ...] = ()) -> Any:
    budget.take('rows')
    return db.execute(sql, args).fetchone()


def _decreased(a: dict[str, int] | None, b: dict[str, int] | None) -> int:
    return int(a is not None and b is not None and any(b[k] < a[k] for k in USAGE_KEYS))


def _sample(db: sqlite3.Connection, budget: Budget, meta: dict[str, Any], mid: str, at: float | None, pos: int, value: dict[str, int] | None) -> None:
    gen = meta['gen']
    # NULL times sort first in the legacy reducer. A finite sentinel is safe for
    # Python datetime's supported year range and keeps tuple seeks indexable.
    at = at if at is not None else -1e100
    encoded = _dump(value) if value is not None else None
    bad = 0
    if value is not None:
        prior = _one(db, budget, '''SELECT substr(value,1,16385) FROM samples
          WHERE gen=? AND mid=? AND value IS NOT NULL AND (at,pos)<(?,?)
          ORDER BY at DESC,pos DESC LIMIT 1''', (gen, mid, at, pos))
        following = _one(db, budget, '''SELECT at,pos,substr(value,1,16385) FROM samples
          WHERE gen=? AND mid=? AND value IS NOT NULL AND (at,pos)>(?,?)
          ORDER BY at,pos LIMIT 1''', (gen, mid, at, pos))
        bad = _decreased(_vector(prior[0]) if prior else None, value)
        if following:
            db.execute('UPDATE samples SET bad=? WHERE gen=? AND mid=? AND at=? AND pos=?',
                       (_decreased(value, _vector(following[2])), gen, mid, following[0], following[1]))
    db.execute('INSERT INTO samples VALUES(?,?,?,?,?,?)', (gen, mid, at, pos, encoded, bad))
    meta['rev'] += 1
    db.execute('INSERT INTO changes VALUES(?,?,?)', (gen, meta['rev'], mid))


def _consume(db: sqlite3.Connection, budget: Budget, meta: dict[str, Any], raw: bytes, pos: int, session: str, agent: str | None) -> None:
    try:
        row = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        meta['gaps']['malformed_record'] = True
        return
    if not isinstance(row, dict) or row.get('sessionId') != session:
        return
    if (agent and row.get('agentId') != agent) or (not agent and (row.get('agentId') or row.get('isSidechain'))):
        return
    meta['matched'] = True
    at = timestamp(row.get('timestamp'))
    if at is not None:
        meta['started'] = at if meta['started'] is None else min(meta['started'], at)
    message = row.get('message')
    if row.get('type') != 'assistant' or not isinstance(message, dict) or message.get('model') == '<synthetic>':
        return
    mid = message.get('id') or row.get('requestId')
    if not mid:
        return
    # Hash identifiers to bound index keys; never store content or arbitrary IDs.
    if not isinstance(mid, (str, int, float, bool)):
        meta['gaps']['invalid_message_identity'] = True
        return
    if isinstance(mid, float) and not math.isfinite(mid):
        meta['gaps']['invalid_message_identity'] = True
        return
    mid = hashlib.sha256(_dump(mid).encode()).hexdigest()
    if at is None:
        meta['missing_time'] = True
    _sample(db, budget, meta, mid, at, pos, _value(message.get('usage')))


def _fresh(st: os.stat_result, binding: str, gap: bool = False) -> dict[str, Any]:
    return dict(binding=binding, schema=SCHEMA, gen=uuid.uuid4().hex, dev=st.st_dev, ino=st.st_ino,
                offset=0, size=st.st_size, mtime=st.st_mtime_ns, ctime=st.st_ctime_ns,
                prefix='', anchor='', prefix_size=0, rev=0, matched=False,
                started=None, missing_time=False, discard=False,
                gaps={'source_generation_changed': True} if gap else {})


def _ingest(db: sqlite3.Connection, budget: Budget, stream: BinaryIO, path: Path, session: str, agent: str | None, binding: str) -> dict[str, Any]:
    st = os.fstat(stream.fileno())
    if not stat.S_ISREG(st.st_mode):
        raise ValueError('source must be regular')
    row = _one(db, budget, 'SELECT substr(body,1,16385) FROM meta WHERE id=1')
    meta = _load(row[0]) if row else _fresh(st, binding)
    required = {'binding','schema','gen','offset','rev','dev','ino','size','mtime','ctime','prefix','anchor',
                'prefix_size','matched','started','missing_time','discard','gaps'}
    if (not isinstance(meta, dict) or set(meta) != required or meta['schema'] != SCHEMA
            or meta['binding'] != binding
            or not isinstance(meta['gen'], str) or len(meta['gen']) != 32
            or any(type(meta[k]) is not int or meta[k] < 0 for k in
                   ('offset', 'size', 'rev', 'dev', 'ino', 'mtime', 'ctime', 'prefix_size'))
            or meta['offset'] > meta['size'] or meta['prefix_size'] != min(meta['offset'], ANCHOR_BYTES)
            or any(type(meta[k]) is not bool for k in ('matched', 'missing_time', 'discard'))
            or not isinstance(meta['gaps'], dict)
            or set(meta['gaps']) - {'source_generation_changed', 'malformed_record', 'oversized_record', 'invalid_message_identity'}
            or meta['started'] is not None and (type(meta['started']) not in (float, int) or not math.isfinite(meta['started']))
            or any(not isinstance(meta[k], str) or len(meta[k]) != (64 if meta['offset'] else 0)
                   for k in ('anchor', 'prefix'))):
        raise ValueError('invalid cache checkpoint')
    changed = (meta['dev'], meta['ino']) != (st.st_dev, st.st_ino) or st.st_size < meta['offset']
    if not changed and meta['offset']:
        changed = _anchor(stream, meta['prefix_size'], budget) != meta['prefix']
        changed = changed or _anchor(stream, meta['offset'], budget) != meta['anchor']
        changed = changed or (st.st_size == meta['size'] and
                              (st.st_mtime_ns, st.st_ctime_ns) != (meta['mtime'], meta['ctime']))
    if changed:
        meta = _fresh(st, binding, gap=True)
    target = st.st_size
    stream.seek(meta['offset'])
    lines = _Lines(stream, target, budget)
    while meta['offset'] < target and budget.records < budget.max_records:
        # Reserve enough work for indexed insertion + metadata/query publication.
        if budget.max_rows - budget.rows < 16 or budget.max_bytes - budget.source_bytes <= 2 * ANCHOR_BYTES and not lines.buffer:
            break
        start = meta['offset']
        limit = min(MAX_RECORD_BYTES + 1, target - start,
                    budget.max_bytes - budget.source_bytes - 2 * ANCHOR_BYTES + len(lines.buffer))
        raw = lines.readline(limit)
        if not raw:
            break
        budget.take('records')
        if meta['discard']:
            meta['offset'] += len(raw)
            if raw.endswith(b'\n'):
                meta['discard'] = False
            continue
        if len(raw) > MAX_RECORD_BYTES:
            meta['gaps']['oversized_record'] = True
            meta['discard'] = not raw.endswith(b'\n')
            meta['offset'] += len(raw)
            continue
        if not raw.endswith(b'\n'):
            # A short quota fragment must not be mistaken for an oversized row.
            break
        _consume(db, budget, meta, raw, start, session, agent)
        meta['offset'] += len(raw)
    after = os.fstat(stream.fileno())
    current = path.stat()
    if ((after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
            or after.st_size < target
            or (after.st_size == target and (after.st_mtime_ns, after.st_ctime_ns) != (st.st_mtime_ns, st.st_ctime_ns))):
        raise ValueError('source changed during ingestion')
    meta['prefix_size'] = min(meta['offset'], ANCHOR_BYTES)
    meta['prefix'] = _anchor(stream, meta['prefix_size'], budget)
    meta['anchor'] = _anchor(stream, meta['offset'], budget)
    meta.update(size=target, mtime=st.st_mtime_ns, ctime=st.st_ctime_ns)
    db.execute('INSERT OR REPLACE INTO meta VALUES(1,?)', (_dump(meta),))
    return meta


def _contribution(db: sqlite3.Connection, budget: Budget, gen: str, mid: str, start: float | None, cutoff: float | None) -> dict[str, Any]:
    lower = -1e99 if start is not None or cutoff is not None else -1e100
    upper = cutoff if cutoff is not None else 1e100
    args = (gen, mid, lower, upper)
    final = _one(db, budget, '''SELECT at,substr(value,1,16385) FROM samples
        WHERE gen=? AND mid=? AND at>=? AND at<? ORDER BY at DESC,pos DESC LIMIT 1''', args)
    if final is None:
        return {'value': None, 'active': False, 'error': None, 'time': None}
    latest = _one(db, budget, '''SELECT at FROM samples WHERE gen=? AND mid=? AND at>=? AND at<?
        AND value IS NOT NULL ORDER BY at DESC,pos DESC LIMIT 1''', args)
    # When bounded, missing-time records are excluded. A decrease edge whose
    # predecessor has no timestamp must also be excluded from bounded queries.
    bad = _one(db, budget, '''SELECT at,pos,substr(value,1,16385) FROM samples WHERE gen=? AND mid=? AND bad=1
        AND at>=? AND at<? ORDER BY at,pos LIMIT 1''', args)
    if bad and lower > -1e100:
        predecessor = _one(db, budget, '''SELECT at FROM samples WHERE gen=? AND mid=?
            AND value IS NOT NULL AND (at,pos)<(?,?) ORDER BY at DESC,pos DESC LIMIT 1''',
                           (gen, mid, bad[0], bad[1]))
        if predecessor and predecessor[0] < lower:
            bad = _one(db, budget, '''SELECT at,pos,substr(value,1,16385) FROM samples WHERE gen=? AND mid=? AND bad=1
                AND (at,pos)>(?,?) AND at<? ORDER BY at,pos LIMIT 1''',
                       (gen, mid, bad[0], bad[1], upper))
    at = latest[0] if latest and latest[0] > -1e99 else None
    if bad:
        return {'value': None, 'active': False, 'error': 'counter_decreased', 'time': at}
    value = _vector(final[1]) if final[1] is not None else None
    active = start is None or final[0] >= start
    error = None
    if start is None:
        if value is None:
            error = 'missing_message_usage'
    elif active:
        before = _one(db, budget, '''SELECT substr(value,1,16385) FROM samples WHERE gen=? AND mid=? AND at>=? AND at<?
            ORDER BY at DESC,pos DESC LIMIT 1''', (gen, mid, lower, min(start, upper)))
        baseline = (_vector(before[0]) if before[0] is not None else None) if before else _zero()
        if baseline is None or value is None:
            error = 'missing_message_boundary'
            value = None
        else:
            value = {k: value[k] - baseline[k] for k in USAGE_KEYS}
    else:
        value = None
    return {'value': value, 'active': active, 'error': error, 'time': at}


def _query(db: sqlite3.Connection, budget: Budget, meta: dict[str, Any], start: float | None, cutoff: float | None) -> dict[str, Any]:
    gen = meta['gen']
    qid = hashlib.sha256(_dump([start, cutoff]).encode()).hexdigest()
    row = _one(db, budget, 'SELECT substr(body,1,16385) FROM queries WHERE gen=? AND qid=?', (gen, qid))
    state = _load(row[0]) if row else dict(rev=0, total=_zero(), measured=0, active=0, errors={}, time=None)
    if (not isinstance(state, dict) or set(state) != {'rev','total','measured','active','errors','time'}
            or any(type(state[k]) is not int or state[k] < 0 for k in ('rev','measured','active'))
            or state['rev'] > meta['rev'] or not isinstance(state['errors'], dict)
            or set(state['errors']) - {'counter_decreased','missing_message_usage','missing_message_boundary'}
            or any(type(v) is not int or v < 0 for v in state['errors'].values())
            or state['time'] is not None and (type(state['time']) not in (int,float) or not math.isfinite(state['time']))):
        raise ValueError('invalid cached query state')
    state['total'] = _vector(_dump(state['total']))
    while state['rev'] < meta['rev'] and budget.max_rows - budget.rows >= 16:
        change = _one(db, budget, 'SELECT rev,substr(mid,1,65) FROM changes WHERE gen=? AND rev>? ORDER BY rev LIMIT 1',
                      (gen, state['rev']))
        if not change:
            raise ValueError('missing reducer change')
        old = _one(db, budget, 'SELECT substr(body,1,16385) FROM contributions WHERE gen=? AND qid=? AND mid=?',
                   (gen, qid, change[1]))
        value = _contribution(db, budget, gen, change[1], start, cutoff)
        for item, sign in [(_checked_contribution(old[0]) if old else None, -1), (value, 1)]:
            if item is None:
                continue
            if item['value'] is not None:
                state['measured'] += sign
                for key in USAGE_KEYS:
                    state['total'][key] += sign * item['value'][key]
            state['active'] += sign * bool(item['active'])
            if item['error']:
                state['errors'][item['error']] = state['errors'].get(item['error'], 0) + sign
        if value['time'] is not None:
            state['time'] = max(state['time'], value['time']) if state['time'] is not None else value['time']
        db.execute('INSERT OR REPLACE INTO contributions VALUES(?,?,?,?)', (gen, qid, change[1], _dump(value)))
        state['rev'] = change[0]
    db.execute('INSERT OR REPLACE INTO queries VALUES(?,?,?)', (gen, qid, _dump(state)))
    errors = {k for k, v in state['errors'].items() if v}
    errors.update(meta['gaps'])
    if (start is not None or cutoff is not None) and meta['missing_time']:
        errors.add('missing_message_timestamp')
    pending = meta['offset'] < meta['size'] or state['rev'] < meta['rev']
    if pending:
        errors.add('history_pending')
    total = state['total'] if state['measured'] or (start is not None and not errors and meta['matched']) else None
    return dict(usage=total, started=meta['started'],
                measured_at=datetime.fromtimestamp(state['time'], timezone.utc).isoformat() if state['time'] is not None else None,
                interval={'start': start, 'end_exclusive': cutoff}, partial=bool(errors), errors=sorted(errors),
                active=bool(state['active']), coverage={'complete': not errors,
                'source_generation': gen, 'offset': meta['offset'], 'target_bytes': meta['size'],
                'backlog_bytes': meta['size'] - meta['offset'], 'query_revision': state['rev'],
                'source_revision': meta['rev'], 'pending_updates': meta['rev'] - state['rev']})


def unavailable(start: float | None, cutoff: float | None, reason: str) -> dict[str, Any]:
    return dict(usage=None, started=None, measured_at=None,
                interval={'start': start, 'end_exclusive': cutoff}, partial=True, errors=[reason], active=False,
                coverage={'complete': False, 'reason': reason})


def read_snapshot(path: Path, session: str, *, agent: str | None = None, start: float | None = None, cutoff: float | None = None, workspace: str | Path | None = None) -> dict[str, Any]:
    with action_budget(workspace) as budget:
        try:
            budget.take('files')
            return _snapshot(Path(path), session, agent, start, cutoff, budget)
        except FileNotFoundError:
            raise
        except Exhausted:
            return unavailable(start, cutoff, 'action_budget_exhausted')
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, OverflowError):
            return unavailable(start, cutoff, 'cache_or_source_unavailable')


def _acquire_lock(lock: BinaryIO, platform: str = os.name) -> Callable[[], None]:
    """Nonblocking native file locks; never fall back to an unlocked cache."""
    if platform == 'nt':
        import msvcrt
        try:
            locking = getattr(msvcrt, 'locking')
            nonblocking = getattr(msvcrt, 'LK_NBLCK')
            unlock = getattr(msvcrt, 'LK_UNLCK')
        except AttributeError as exc:
            raise ImportError('Windows file locking API unavailable') from exc
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b'\0')
            lock.flush()
        lock.seek(0)
        locking(lock.fileno(), nonblocking, 1)
        def release_windows() -> None:
            lock.seek(0)
            locking(lock.fileno(), unlock, 1)
        return release_windows
    import fcntl
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    def release_posix() -> None:
        fcntl.flock(lock, fcntl.LOCK_UN)
    return release_posix


def _snapshot(path: Path, session: str, agent: str | None, start: float | None, cutoff: float | None, budget: Budget) -> dict[str, Any]:
    location = cache_path(path, session, agent, budget.workspace)
    location.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(str(location) + '.lock')
    if (lock_path.exists() or lock_path.is_symlink()) and not stat.S_ISREG(lock_path.lstat().st_mode):
        raise ValueError('cache files must be regular')
    with lock_path.open('a+b') as lock:
        try:
            release = _acquire_lock(lock)
        except ImportError:
            return unavailable(start, cutoff, 'cache_lock_unsupported')
        except OSError:
            return unavailable(start, cutoff, 'cache_busy')
        db = None
        try:
            # Inspect recovery bytes only after acquiring the lifetime lock. A
            # previous writer can leave a larger hot journal before releasing
            # its lock (including on crash); a pre-lock stat is not a bound.
            try:
                for suffix in ('', '-journal', '-wal', '-shm'):
                    candidate = Path(str(location) + suffix)
                    if candidate.exists() or candidate.is_symlink():
                        if not stat.S_ISREG(candidate.lstat().st_mode):
                            raise ValueError('cache files must be regular')
                        if suffix:
                            size = candidate.stat().st_size
                            if size > MAX_RECOVERY_BYTES:
                                raise ValueError('cache recovery requires maintenance')
                            budget.take('recovery_bytes', size)
            except Exhausted:
                return unavailable(start, cutoff, 'action_budget_exhausted')
            except (OSError, ValueError):
                return unavailable(start, cutoff, 'cache_or_source_unavailable')
            db = sqlite3.connect(location, timeout=0)
            setlimit = getattr(db, 'setlimit', None)
            if setlimit is not None:  # Python 3.11+; SQL projection also caps blobs.
                setlimit(getattr(sqlite3, 'SQLITE_LIMIT_LENGTH'), 32768)
            db.execute('PRAGMA cache_size=-512')
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA busy_timeout=0')
            def progress() -> int:
                try:
                    budget.take('vm_steps', 100)
                    return 0
                except Exhausted:
                    return 1
            db.set_progress_handler(progress, 100)
            _schema(db)
            db.execute('BEGIN IMMEDIATE')
            # O_NONBLOCK prevents a caller-supplied FIFO from hanging before
            # fstat can reject it. Native files are never opened for writing.
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(descriptor, 'rb', buffering=0) as stream:
                meta = _ingest(db, budget, stream, path, session, agent, location.name)
            # The source checkpoint is independently durable. If a later interval
            # query hits the VM cap, the next process resumes metadata reduction
            # rather than replaying the same source batch indefinitely.
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            result = _query(db, budget, meta, start, cutoff)
            db.commit()
            if not meta['matched'] and meta['offset'] == meta['size'] and not meta['gaps']:
                return unavailable(start, cutoff, 'session_identity_unavailable')
            return result
        except (sqlite3.DatabaseError, ValueError, KeyError, TypeError) as exc:
            if db is not None:
                db.close()
                db = None
            # Replace only corrupt/incompatible derived databases, without an
            # integrity scan. Never rename a merely busy/interrupted database.
            corrupt = isinstance(exc, (ValueError, KeyError, TypeError)) or (getattr(exc, 'sqlite_errorcode', None) in (11, 26)
                or str(exc) in ('file is not a database', 'database disk image is malformed'))
            if corrupt and location.exists():
                # The advisory lock covers all our readers/writers. Quarantine
                # sidecars together with the main file; stale recovery bytes may
                # never attach to the replacement derived generation.
                for suffix in ('-journal', '-wal', '-shm', ''):
                    candidate = Path(str(location) + suffix)
                    if candidate.exists():
                        os.replace(candidate, Path(str(candidate) + '.invalid'))
            raise
        finally:
            if db is not None:
                db.close()
            release()
