"""Disposable, bounded projection of the append-only observation journal.

No value here is workflow authority. A complete prefix is distinguished from a
complete source, and paged details never masquerade as a complete history.
Continuity checks assume append-only writers: fixed anchors cannot authenticate
an arbitrary undetected rewrite in the middle of a growing file.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid
from typing import Any, BinaryIO, Callable, Iterator, cast

from . import storage

# Per-action byte allowances, independent of retained history size. The source
# window fits a maximum record plus continuity anchors; query/reducer windows
# also cover its normalized copies and reads without raising row or VM quotas.
SOURCE_BYTES = 16 * 1024 * 1024
RECORD_BYTES = 8 * 1024 * 1024
RECORDS = 1024
QUERY_ROWS = 4096
QUERY_BYTES = 64 * 1024 * 1024
REDUCER_ROWS = 4096
VM_STEPS = 4_000_000
LOCK_SECONDS = .05
RECOVERY_BYTES = 16 * 1024 * 1024
ANCHOR_BYTES = 256
SCHEMA = 3
REDUCER_BYTES = 128 * 1024 * 1024
TOTAL_PARTS = ('last_at', 'first_phase', 'last_phase', 'finished_at', 'outcome')
CHILD_PARTS = ('run', 'session', 'kind', 'child', 'agent_transcript_path')
# A child is a closed five-part object, not a history-sized aggregate. A
# fresh fields call can prefetch at most FIELD_PAGE_LIMIT + 1 manifests (each
# with a SHA-256 key and two integer positions), then hydrate all five parts.
# Include JSON member punctuation as well as names. The query allowance also
# retains its two-cell reservation before the final EOF probe. This bounds a
# complete supported item even when every part is RECORD_BYTES, without a new
# source limit or part-level API. Other quotas can defer an already-used budget.
FIELD_PAGE_LIMIT = 1024
CHILD_MANIFEST_BYTES = len(json.dumps({'_parts': sorted(CHILD_PARTS)}, separators=(',', ':')).encode())
CHILD_FIELD_BYTES = CHILD_MANIFEST_BYTES + 64 + 2 * 8
CHILD_ITEM_BYTES = len(CHILD_PARTS) * RECORD_BYTES + sum(len(k) + 4 for k in CHILD_PARTS) + 2
OUTPUT_BYTES = CHILD_ITEM_BYTES + (FIELD_PAGE_LIMIT + 1) * CHILD_FIELD_BYTES
QUERY_RESERVATION_BYTES = 2 * (RECORD_BYTES + 1) + 128
QUERY_BYTES = max(QUERY_BYTES, OUTPUT_BYTES + QUERY_RESERVATION_BYTES)
CACHE = 'flow-journal.sqlite3'


class Limited(ValueError):
    pass


class SourceLimit(ValueError):
    """A deterministic unsupported source representation; advance with a gap."""


class RepresentationError(RuntimeError):
    """A writer invariant failed; rebuilding unchanged input cannot repair it."""


class CacheCorrupt(RuntimeError):
    """Derived state is invalid; never treat this as an invalid source record."""


class QueryResult(list[tuple[Any, ...]]):
    """List-compatible rows with local status, independent of sticky coverage."""

    def __init__(self, rows: list[tuple[Any, ...]], reason: str = 'complete') -> None:
        super().__init__(rows)
        self.reason = reason
        self.complete = reason == 'complete'


def _counters(value: Any, limit: int | None = 32) -> bool:
    return (isinstance(value, dict) and (limit is None or len(value) <= limit)
            and all(type(v) is int and v >= 0 for v in value.values()))


def _source(row: dict[str, Any]) -> None:
    """Validate nested containers before either reducers or readers use them."""
    if row.get('kind') == 'start' and 'run' not in row:
        raise ValueError('missing start run')
    for key in ('run', 'session', 'root', 'child', 'call_id', 'event', 'action', 'closure_id'):
        _key(row.get(key))
    if row.get('tool') is not None and not isinstance(row['tool'], str):
        raise ValueError('invalid tool')
    for key in ('admission', 'artifacts', 'captured', 'usage'):
        if row.get(key) is not None and not isinstance(row[key], dict):
            raise ValueError('invalid ' + key)
    artifacts, captured = row.get('artifacts') or {}, row.get('captured') or {}
    if 'evidence' in artifacts and not isinstance(artifacts['evidence'], list):
        raise ValueError('invalid artifact evidence')
    if 'evidence' in captured and not isinstance(captured['evidence'], dict):
        raise ValueError('invalid captured evidence')
    reviews = captured.get('reviews', [])
    if not isinstance(reviews, (list, dict)):
        raise ValueError('invalid reviews')
    groups = reviews.values() if isinstance(reviews, dict) else [reviews]
    if any(not isinstance(group, list) or any(not isinstance(v, dict) for v in group)
           for group in groups):
        raise ValueError('invalid review group')
    if row.get('usage') is not None and not _counters(row['usage']):
        raise ValueError('invalid counters')


def _decode(raw: Any, shape: str) -> Any:
    """Bound and validate persisted JSON at every reducer/query boundary."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8')
        if not isinstance(raw, str) or len(raw.encode()) > (8192 if shape == 'metadata' else RECORD_BYTES):
            raise ValueError('body size/type')
        value = json.loads(raw)
        if shape == 'totals':
            if not isinstance(value, dict) or not _empty().keys() <= value.keys():
                raise ValueError('total object/fields')
            for key, default in _empty().items():
                if type(default) is int and (type(value[key]) is not int or value[key] < 0):
                    raise ValueError('total counter')
                if type(default) is bool and type(value[key]) is not bool:
                    raise ValueError('total flag')
            if (value['tokens'] != {} or any(value[k] is not None for k in TOTAL_PARTS)):
                raise ValueError('total tokens')
        elif shape == 'counter':
            if (not isinstance(value, dict) or set(value) != {'first', 'maximum'}
                    or any(type(v) is not int or v < 0 for v in value.values())
                    or value['maximum'] < value['first']):
                raise ValueError('counter fields')
        elif shape == 'counter-total':
            if type(value) is not int or value < 0:
                raise ValueError('counter total')
        elif shape == 'child':
            if (not isinstance(value, dict) or set(value) != {'_parts'} or not isinstance(value['_parts'], list)
                    or len(value['_parts']) != len(set(value['_parts']))
                    or any(k not in CHILD_PARTS for k in value['_parts'])):
                raise ValueError('child manifest')
        elif shape == 'samples':
            if (not isinstance(value, dict) or set(value) != {'first', 'maximum', 'count'}
                    or type(value.get('count')) is not int or not 0 <= value['count'] <= 32
                    or not _counters(value['maximum'])):
                raise ValueError('sample object')
            if value['first'] not in (None, {}) or value['maximum'] != {}:
                raise ValueError('normalized sample marker')
            first, maximum = value['first'], value['maximum']
            if first is None:
                if maximum or value['count'] != 0:
                    raise ValueError('unmeasured maximum')
            elif (value['count'] == 0 or not _counters(first) or first.keys() != maximum.keys()
                  or any(maximum[k] < v for k, v in first.items())):
                raise ValueError('sample counters')
        elif shape in ('event', 'start', 'session', 'child', 'reviewer', 'latest', 'post-completion', 'milestone'):
            if not isinstance(value, dict):
                raise ValueError('event object')
            _source(value)
            if shape == 'start' and (value.get('kind') != 'start' or 'run' not in value):
                raise ValueError('start fields')
        elif shape == 'captured:reviews':
            _source({'captured': {'reviews': value}})
        elif shape == 'closure' and value is not None:
            raise ValueError('closure body')
        return value
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError) as exc:
        raise CacheCorrupt('invalid_cache_body:' + shape) from exc


@dataclass
class Budget:
    source_bytes: int = 0
    records: int = 0
    query_rows: int = 0
    query_bytes: int = 0
    reducer_rows: int = 0
    reducer_bytes: int = 0
    output_bytes: int = 0
    vm_steps: int = 0
    recovery_bytes: int = 0
    projections: dict[str, Projection] = field(default_factory=dict)


_active: ContextVar[Budget | None] = ContextVar('journal_budget', default=None)


@contextmanager
def action_budget(workspace: str | Path | None = None) -> Iterator[Budget]:
    """Nested entry shares all quotas, including repeated report/closure calls."""
    existing = _active.get()
    if existing is not None:
        yield existing
        return
    budget = Budget()
    token = _active.set(budget)
    try:
        yield budget
    finally:
        for projection in budget.projections.values():
            projection.close()
        _active.reset(token)


def _json(value: Any) -> str:
    # Keep native Unicode compact; preserve JSON-escaped lone surrogates too.
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, sort_keys=True).encode(
        'utf-8', 'backslashreplace').decode('utf-8')


def _key(value: Any) -> str:
    # JSON type tagging keeps missing/null distinct from strings. Ordinary native
    # identities are strings; non-scalar identities are invalid observations.
    if value is not None and not isinstance(value, (str, int, bool, float)):
        raise ValueError('invalid observation identity')
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _encode(value: Any, shape: str) -> str:
    """Every JSON writer uses the exact reader schema and byte contract."""
    try:
        raw = _json(value)
        _decode(raw, shape)
        return raw
    except (CacheCorrupt, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise RepresentationError('derived_representation:' + shape) from exc


def _empty() -> dict[str, Any]:
    return dict(raw=0, last_at=None, tools=0, admitted=0, denied=0, error=0,
                observed=0, approximate=False, measured=0, unmeasured=0,
                tokens={}, categories=0, epoch=0, recent_tools=0, recent_dispatches=0,
                repetition=0, milestones=0, first_phase=None, last_phase=None,
                finished=False, finished_at=None, outcome=None, closed=False)


class Projection:
    def __init__(self, workspace: str | Path, budget: Budget) -> None:
        self.workspace = Path(workspace).resolve()
        self.budget = budget
        self.db: sqlite3.Connection | None = None
        self.lock: BinaryIO | None = None
        self.release_lock: Callable[[], None] | None = None
        self.meta: dict[str, Any] = {}
        self.error: str | None = None
        self.detail_partial = False
        self.query_failures = 0
        self.usage_complete = False
        self.continuations: dict[str, Any] = {}
        self.queries: dict[str, dict[str, Any]] = {}
        self.source_present: bool | None = None
        self.run_observations: dict[str, bool | None] = {}
        self._open()
        self.refresh()

    def close(self) -> None:
        try:
            if self.db is not None:
                self.db.close()
                self.db = None
        finally:
            try:
                if self.release_lock is not None:
                    self.release_lock()
                    self.release_lock = None
            finally:
                if self.lock is not None:
                    self.lock.close()
                    self.lock = None

    def _progress(self) -> int:
        self.budget.vm_steps += 1000
        return int(self.budget.vm_steps > VM_STEPS)

    def _metadata(self, raw: Any) -> dict[str, Any]:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8')
            if not isinstance(raw, str) or len(raw.encode()) > 8192:
                raise ValueError('metadata size')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('metadata object')
            if value.get('schema') != SCHEMA or value.get('workspace') != str(self.workspace):
                return {}
            if (any(type(value.get(k)) is not int or value[k] < 0
                    for k in ('offset', 'size', 'target', 'prefix_length'))
                    or value['offset'] > value['size'] or value['target'] > value['size']
                    or value['prefix_length'] > ANCHOR_BYTES
                    or type(value.get('mtime')) is not int
                    or not isinstance(value.get('generation'), str) or len(value['generation']) != 32
                    or not isinstance(value.get('identity'), list) or len(value['identity']) != 2
                    or any(type(v) is not int or v < 0 for v in value['identity'])
                    or type(value.get('host_adapter', False)) is not bool
                    or type(value.get('discard')) is not bool
                    or not isinstance(value.get('errors'), list)
                    or any(v not in ('generation_changed', 'oversized_record', 'invalid_record', 'source_limit') for v in value['errors'])
                    or any(not isinstance(value.get(k), str) or len(value[k]) != 64 for k in ('prefix', 'anchor'))):
                raise ValueError('metadata fields')
            return cast(dict[str, Any], value)
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError) as exc:
            raise CacheCorrupt('invalid_cache_metadata') from exc

    def _open(self) -> None:
        path = None
        try:
            path = storage.runtime_file(str(self.workspace), CACHE)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Retain the nonblocking lock until the projection closes: recovery
            # must never rename a database still used by another projection.
            from .claude_usage_cache import _acquire_lock
            self.lock = storage.runtime_file(str(self.workspace), CACHE + '.lock').open('a+b')
            self.release_lock = _acquire_lock(self.lock)
            # SQLite recovery is optional work too. Refuse large hot sidecars;
            # never let connecting trigger recovery of arbitrary historical WAL.
            for suffix in ('-wal', '-journal', '-shm'):
                sidecar = storage.runtime_file(str(self.workspace), CACHE + suffix)
                if sidecar.exists():
                    size = sidecar.stat().st_size
                    if self.budget.recovery_bytes + size > RECOVERY_BYTES:
                        raise Limited('cache_recovery_pending')
                    self.budget.recovery_bytes += size
            self.db = sqlite3.connect(str(path), timeout=LOCK_SECONDS)
            self.db.set_progress_handler(self._progress, 1000)
            self.db.execute('PRAGMA busy_timeout=50')
            self.db.execute('PRAGMA cache_size=-2048')
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS totals (g TEXT, run TEXT, body TEXT, PRIMARY KEY(g,run));
                CREATE TABLE IF NOT EXISTS seen (g TEXT, run TEXT, session TEXT, event TEXT, call TEXT,
                    PRIMARY KEY(g,run,session,event,call));
                CREATE TABLE IF NOT EXISTS samples (g TEXT, run TEXT, session TEXT, body TEXT,
                    PRIMARY KEY(g,run,session));
                CREATE TABLE IF NOT EXISTS counters (g TEXT, run TEXT, session TEXT, category TEXT,
                    name TEXT, body TEXT, PRIMARY KEY(g,run,session,category));
                CREATE TABLE IF NOT EXISTS parts (g TEXT, run TEXT, family TEXT, key TEXT,
                    name TEXT, body TEXT, PRIMARY KEY(g,run,family,key,name));
                CREATE TABLE IF NOT EXISTS actions (g TEXT, run TEXT, epoch INTEGER, action TEXT, n TEXT,
                    PRIMARY KEY(g,run,epoch,action));
                CREATE TABLE IF NOT EXISTS events (g TEXT, pos INTEGER, run TEXT, kind TEXT,
                    owner TEXT, stamp REAL, body TEXT, PRIMARY KEY(g,pos));
                CREATE INDEX IF NOT EXISTS event_run ON events(g,run,pos);
                CREATE INDEX IF NOT EXISTS event_run_kind ON events(g,run,kind,pos);
                CREATE INDEX IF NOT EXISTS event_kind ON events(g,kind,pos);
                CREATE INDEX IF NOT EXISTS start_owner ON events(g,kind,owner,pos);
                CREATE INDEX IF NOT EXISTS start_time ON events(g,kind,owner,stamp,pos);
                CREATE TABLE IF NOT EXISTS lineage (g TEXT, pos INTEGER, owner TEXT, root TEXT,
                    PRIMARY KEY(g,owner,pos));
                CREATE TABLE IF NOT EXISTS closure (g TEXT, id TEXT, kind TEXT, pos INTEGER,
                    PRIMARY KEY(g,id,kind));
                CREATE TABLE IF NOT EXISTS fields (g TEXT, run TEXT, family TEXT, key TEXT,
                    pos INTEGER, body TEXT, rank INTEGER DEFAULT 0, PRIMARY KEY(g,run,family,key));
                CREATE INDEX IF NOT EXISTS fields_page ON fields(g,run,family,pos,rank,key);
            ''')
            row = self.db.execute('SELECT substr(CAST(body AS BLOB),1,8193) FROM meta WHERE id=1').fetchone()
            if row:
                self.meta = self._metadata(row[0])
            self.db.commit()
        except (sqlite3.DatabaseError, OSError, ValueError, ImportError, CacheCorrupt) as exc:
            self._cache_failure(exc)

    def _cache_failure(self, exc: Exception) -> None:
        self.error = 'cache_unavailable:' + str(exc)[:100]
        self.detail_partial = True
        self.usage_complete = False
        self.query_failures += 1
        if self.db is not None:
            try:
                self.db.rollback()
            except sqlite3.DatabaseError:
                pass
            finally:
                self.db.close()
                self.db = None
        # Keep the lock while moving the closed database and its recovery files.
        # Only proven corruption is quarantined; operational failures may retry.
        corrupt = (isinstance(exc, CacheCorrupt)
                   or getattr(exc, 'sqlite_errorcode', None) in (11, 26)
                   or str(exc) in ('file is not a database', 'database disk image is malformed'))
        try:
            if corrupt and self.release_lock is not None:
                quarantine = '.corrupt-' + uuid.uuid4().hex
                for suffix in ('-journal', '-wal', '-shm', ''):
                    candidate = storage.runtime_file(str(self.workspace), CACHE + suffix)
                    if candidate.exists():
                        os.replace(candidate, candidate.with_name(candidate.name + quarantine))
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    @property
    def generation(self) -> str:
        return str(self.meta.get('generation', ''))

    def _read(self, stream: Any, count: int) -> bytes:
        count = min(count, max(0, SOURCE_BYTES - self.budget.source_bytes))
        data: bytes = stream.read(count)
        self.budget.source_bytes += len(data)
        return data

    def _anchor(self, stream: Any, offset: int) -> str:
        count = min(ANCHOR_BYTES, offset)
        if SOURCE_BYTES - self.budget.source_bytes < count:
            raise Limited('source_budget')
        stream.seek(offset - count)
        return hashlib.sha256(self._read(stream, count)).hexdigest()

    def refresh(self) -> None:
        if self.db is None:
            return
        if self.budget.records >= RECORDS:
            try:
                info = storage.runtime_file(str(self.workspace), 'flow-events.jsonl').stat()
                self.source_present = stat.S_ISREG(info.st_mode)
                if self.meta.get('identity') != [info.st_dev,info.st_ino] or info.st_size < self.meta.get('offset',0):
                    self.error = 'source_generation_pending'
                self.meta['size'] = info.st_size
            except (OSError, ValueError):
                self.source_present = None
                self.error = 'source_unavailable'
            return
        committed_meta = {**self.meta, 'errors': list(self.meta.get('errors', []))} if self.meta else {}
        try:
            path = storage.runtime_file(str(self.workspace), 'flow-events.jsonl')
            if not path.exists():
                self.source_present = False
                if not self.meta:
                    self.meta = dict(schema=SCHEMA, workspace=str(self.workspace), generation='', offset=0, size=0, target=0, errors=[])
                self.error = 'source_missing'
                return
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('source_not_regular')
                self.source_present = True
                self.db.execute('BEGIN IMMEDIATE')
                current = self.db.execute('SELECT substr(CAST(body AS BLOB),1,8193) FROM meta WHERE id=1').fetchone()
                if current:
                    self.meta = self._metadata(current[0])
                m = self.meta
                valid = (m.get('schema') == SCHEMA and m.get('workspace') == str(self.workspace)
                         and m.get('identity') == [info.st_dev, info.st_ino]
                         and 0 <= m.get('offset', -1) <= info.st_size)
                if valid:
                    valid = (self._anchor(stream, m['prefix_length']) == m['prefix']
                             and self._anchor(stream, m['offset']) == m['anchor'])
                    if info.st_size == m.get('size') and info.st_mtime_ns != m.get('mtime'):
                        valid = False
                if not valid:
                    gap = bool(m.get('identity'))
                    m = dict(schema=SCHEMA, workspace=str(self.workspace), generation=uuid.uuid4().hex,
                             identity=[info.st_dev, info.st_ino], offset=0, size=info.st_size,
                             prefix_length=min(info.st_size, ANCHOR_BYTES), errors=['generation_changed'] if gap else [],
                             discard=False, target=info.st_size)
                    self.meta = m
                    m['prefix'] = self._anchor(stream, m['prefix_length'])
                m['size'] = info.st_size
                if m['offset'] >= m['target']:
                    m['target'] = info.st_size
                stream.seek(m['offset'])
                while m['offset'] < info.st_size and self.budget.records < RECORDS:
                    remaining = SOURCE_BYTES - self.budget.source_bytes - 2 * ANCHOR_BYTES
                    if remaining <= 0:
                        break
                    pos = m['offset']
                    limit = min(RECORD_BYTES + 1, remaining, info.st_size - pos)
                    raw = stream.readline(limit)
                    self.budget.source_bytes += len(raw)
                    if not raw:
                        break
                    complete = raw.endswith(b'\n')
                    if m['discard']:
                        m['offset'] += len(raw)
                        if complete:
                            m['discard'] = False
                            self.budget.records += 1
                        continue
                    if not complete:
                        if len(raw) > RECORD_BYTES:
                            m['discard'] = True
                            m['offset'] += len(raw)
                            if 'oversized_record' not in m['errors']:
                                m['errors'].append('oversized_record')
                            continue
                        break  # A torn or budget-clipped record is retried whole.
                    self.budget.records += 1
                    try:
                        row = json.loads(raw)
                        if not isinstance(row, dict):
                            raise ValueError('non_object')
                        # Reserve an upper bound for this record's indexed row
                        # work before mutation. Exhaustion leaves the row for a
                        # later action, never skips an accounting contribution.
                        entries = 0
                        for values in (row.get('artifacts') or {}, row.get('captured') or {}):
                            if isinstance(values, dict):
                                entries += len(values)
                                evidence = values.get('evidence', [])
                                if isinstance(evidence, (list, dict)):
                                    entries += len(evidence)
                        captures = row.get('captured') or {}
                        reviews = captures.get('reviews', []) if isinstance(captures, dict) else []
                        groups = reviews.values() if isinstance(reviews, dict) else [reviews]
                        entries += sum(len(group) for group in groups if isinstance(group, list))
                        work = 16 + 2 * min(entries, 258)
                        if self.budget.reducer_rows + work > REDUCER_ROWS:
                            break
                        self.budget.reducer_rows += work
                        # Each row is atomic with its cursor. Invalid reducers do
                        # not leave a half-applied contribution behind.
                        self.db.execute('SAVEPOINT record')
                        try:
                            record_bytes_before = self.budget.reducer_bytes
                            self._consume(pos, row)
                        except Exception:
                            self.db.execute('ROLLBACK TO record')
                            raise
                        finally:
                            self.db.execute('RELEASE record')
                    except Limited as exc:
                        if str(exc) == 'reducer_byte_budget' and record_bytes_before == 0:
                            # Even a fresh byte allowance cannot represent this
                            # observation's reduction work. It is a stable source
                            # complexity gap, not cache corruption or retry debt.
                            if 'source_limit' not in m['errors']:
                                m['errors'].append('source_limit')
                            m['offset'] += len(raw)
                        # The record savepoint rolled back; commit any completed
                        # prefix and retry this observation under a fresh budget.
                        break
                    except SourceLimit:
                        if 'source_limit' not in m['errors']:
                            m['errors'].append('source_limit')
                    except (ValueError, TypeError, KeyError, RecursionError):
                        if 'invalid_record' not in m['errors']:
                            m['errors'].append('invalid_record')
                    m['offset'] += len(raw)
                after = os.fstat(stream.fileno())
                named = path.stat()
                if ((after.st_dev, after.st_ino) != (named.st_dev, named.st_ino)
                        or after.st_size < info.st_size
                        or after.st_size == info.st_size and after.st_mtime_ns != info.st_mtime_ns):
                    raise Limited('source_changed_during_read')
                if not valid or m['offset'] != json.loads(current[0]).get('offset'):
                    m['anchor'] = self._anchor(stream, m['offset'])
                m['mtime'] = info.st_mtime_ns
                metadata = _encode(m, 'metadata')
                try:
                    self._metadata(metadata)
                except CacheCorrupt as exc:
                    raise RepresentationError('derived_representation:metadata') from exc
                self.db.execute('INSERT OR REPLACE INTO meta VALUES(1,?)', (metadata,))
                self.db.commit()
                self.error = None
        except RepresentationError as exc:
            if self.db is not None:
                self.db.rollback()
            self.meta = committed_meta
            self.error = str(exc)
        except CacheCorrupt as exc:
            self.meta = committed_meta
            self._cache_failure(exc)
        except sqlite3.DatabaseError as exc:
            self.meta = committed_meta
            self._cache_failure(exc)
        except (OSError, ValueError) as exc:
            if self.db is not None:
                self.db.rollback()
            self.meta = committed_meta
            self.error = str(exc)[:120]

    def _stored(self, table: str, key: tuple[Any, ...], default: Any) -> Any:
        assert self.db is not None
        sql = {
            'totals': 'SELECT @body@ FROM totals WHERE g=? AND run=?',
            'samples': 'SELECT @body@ FROM samples WHERE g=? AND run=? AND session=?',
        }[table]
        row = self._reducer_query(sql, key, ('body',), charge_row=False)
        return _decode(row[0], table) if row else default

    def _reducer_query(self, sql: str, args: Any, columns: tuple[str, ...], *, charge_row: bool = True) -> tuple[Any, ...] | None:
        """Read at most the remaining encoded-byte allowance, before decoding.

        A clipped cell is budget deferral; a full-sized over-limit cell is true
        cache damage. LIMIT 1 callers bound row materialization independently.
        """
        assert self.db is not None
        remaining = REDUCER_BYTES - self.budget.reducer_bytes
        cap = min(RECORD_BYTES + 1, max(0, remaining - 128) // len(columns))
        if cap < 1 or charge_row and self.budget.reducer_rows >= REDUCER_ROWS:
            raise Limited('reducer_byte_budget' if cap < 1 else 'reducer_row_budget')
        for column in columns:
            sql = sql.replace('@' + column + '@', f'substr(CAST({column} AS BLOB),1,{cap})')
        sql = sql.replace('SELECT category,', 'SELECT substr(CAST(category AS BLOB),1,65),')
        row = self.db.execute(sql, args).fetchone()
        self._charge(rows=int(charge_row))
        if row is None:
            return None
        self._charge(*row)
        if cap <= RECORD_BYTES and any(isinstance(v, bytes) and len(v) >= cap for v in row):
            raise Limited('reducer_byte_budget')
        try:
            return tuple(v.decode('utf-8') if isinstance(v, bytes) else v for v in row)
        except UnicodeError as exc:
            raise CacheCorrupt('invalid_cache_encoding') from exc

    def _charge(self, *values: str | bytes, rows: int = 0) -> None:
        size = sum(len(value) if isinstance(value, bytes) else len(value.encode()) for value in values)
        if self.budget.reducer_bytes + size > REDUCER_BYTES:
            raise Limited('reducer_byte_budget')
        if self.budget.reducer_rows + rows > REDUCER_ROWS:
            raise Limited('reducer_row_budget')
        self.budget.reducer_rows += rows
        self.budget.reducer_bytes += size

    def _write(self, value: Any, shape: str) -> str:
        raw = _encode(value, shape)
        self._charge(raw)
        return raw

    def _part(self, run: str, family: str, key: str, name: str, value: Any) -> None:
        assert self.db is not None
        self._charge(rows=1)
        self.db.execute('INSERT OR REPLACE INTO parts VALUES(?,?,?,?,?,?)',
                        (self.generation, run, family, key, name, self._write(value, 'part')))

    @staticmethod
    def _counter_name(category: Any, raw: Any) -> str:
        name = _decode(raw, 'name')
        if not isinstance(name, str) or _key(name) != category:
            raise CacheCorrupt('invalid_cache_counter_name')
        return name

    def _counter(self, run: str, session: str, name: str, value: Any) -> None:
        assert self.db is not None
        self._charge(rows=1)
        self.db.execute('INSERT OR REPLACE INTO counters VALUES(?,?,?,?,?,?)',
                        (self.generation, run, session, _key(name), self._write(name, 'name'),
                         self._write(value, 'counter' if session else 'counter-total')))

    def _counter_total(self, run: str, name: str) -> int | None:
        assert self.db is not None
        row = self._reducer_query(
            "SELECT @name@,@body@ FROM counters WHERE g=? AND run=? AND session='' AND category=?",
            (self.generation, run, _key(name)), ('name', 'body'))
        if row is None:
            return None
        self._counter_name(_key(name), row[0])
        return cast(int, _decode(row[1], 'counter-total'))

    def _parts(self, run: str, family: str, key: str, names: list[str]) -> dict[str, Any] | None:
        rows = self._query('SELECT name,body FROM parts WHERE g=? AND run=? AND family=? AND key=? LIMIT 7',
                           (self.generation, run, family, key), 7)
        if not rows.complete:
            return None
        values = {name: _decode(raw, 'part') for name, raw in rows}
        if set(values) != set(names):
            raise CacheCorrupt('invalid_cache_parts')
        return values

    def _consume(self, pos: int, row: dict[str, Any]) -> None:
        assert self.db is not None
        _source(row)
        from .worker_runtime import SPAWN
        g, run = self.generation, _key(row.get('run'))
        session = _key(row.get('session'))
        kind = row.get('kind') if isinstance(row.get('kind'), str) else ''
        body = _json(row)
        if len(body.encode()) > RECORD_BYTES:
            raise SourceLimit('source_serialization_limit')
        body = self._write(row, 'event')
        from .flow import measurement_time
        stamp = measurement_time(row.get('at'))
        from datetime import datetime
        numeric = datetime.fromisoformat(stamp).timestamp() if stamp else None
        self.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                        (g, pos, run, kind, session, numeric, body))
        root = row.get('root')
        if isinstance(root, str) and root:
            self.db.execute('INSERT OR REPLACE INTO lineage VALUES(?,?,?,?)', (g,pos,session,_key(root)))
            if row.get('event') == 'SubagentStart' and row.get('child'):
                self.db.execute('INSERT OR REPLACE INTO lineage VALUES(?,?,?,?)', (g,pos,_key(row['child']),_key(root)))
        if row.get('closure_id'):
            self.db.execute('INSERT OR IGNORE INTO fields(g,run,family,key,pos,body) VALUES(?,?,?,?,?,?)',(g,run,'closure',str(row['closure_id']),pos,'null'))
            self.db.execute('INSERT OR IGNORE INTO closure VALUES(?,?,?,?)', (g,_key(row['closure_id']),kind,pos))
        entries = 0
        for family, values in (('artifact', row.get('artifacts') or {}), ('captured', row.get('captured') or {})):
            if not isinstance(values, dict):
                raise ValueError('invalid evidence')
            for key, value in values.items():
                entries += 1
                if entries > 256:
                    raise ValueError('record_reducer_limit')
                if key == 'evidence':
                    items = value.items() if family == 'captured' else ((v,v) for v in value)
                    for rank, (name, entry) in enumerate(items):
                        entries += 1
                        if entries > 256:
                            raise ValueError('record_reducer_limit')
                        self.db.execute('INSERT INTO fields(g,run,family,key,pos,body,rank) VALUES(?,?,?,?,?,?,?) ON CONFLICT(g,run,family,key) DO UPDATE SET body=excluded.body',
                                        (g,run,family+'-evidence',str(name),pos,self._write(entry, family+'-evidence'),rank))
                else:
                    self.db.execute('INSERT OR REPLACE INTO fields(g,run,family,key,pos,body) VALUES(?,?,?,?,?,?)', (g,run,family,str(key),pos,self._write(value, 'captured:reviews' if family == 'captured' and key == 'reviews' else family)))
        # Raw consumers deliberately bypass first-wins call deduplication.
        representatives = []
        if kind == 'hook':
            representatives.append(('session', session, {k:row[k] for k in ('kind','run','session') if k in row}))
        if row.get('child'):
            representatives.append(('child', _key(row['child']), {k:row[k] for k in
                ('run','session','kind','child','agent_transcript_path') if k in row}))
        if kind == 'usage':
            representatives.append(('latest', 'usage', row))
        if kind == 'hook' and (row.get('admission') or {}).get('authority') == 'observation_only':
            representatives.append(('post-completion', str(pos), row))
        reviews = (row.get('captured') or {}).get('reviews', [])
        groups = reviews.values() if isinstance(reviews, dict) else [reviews]
        for group in groups:
            if isinstance(group, list):
                for review in group:
                    entries += 1
                    if entries > 256:
                        raise ValueError('record_reducer_limit')
                    if isinstance(review, dict) and review.get('agent'):
                        representatives.append(('reviewer', _key(review['agent']),
                            {'run':row.get('run'), 'captured':{'reviews':[{'agent':review['agent']}]}}))
        for family, key, value in representatives:
            if family == 'child':
                old = self._reducer_query('SELECT @body@ FROM fields WHERE g=? AND run=? AND family=? AND key=?',
                                          (g,run,family,key), ('body',), charge_row=False)
                names = set()
                if old:
                    names.update(_decode(old[0], 'child')['_parts'])
                for name, item in value.items():
                    self._part(run, family, key, name, item)
                    names.add(name)
                value = {'_parts': sorted(names)}
            self.db.execute('INSERT OR REPLACE INTO fields(g,run,family,key,pos,body) VALUES(?,?,?,?,?,?)',
                            (g,run,family,key,pos,self._write(value, family)))
        total = self._stored('totals',(g,run),_empty())
        total['raw'] += 1
        self._part(run, 'total', '', 'last_at', row.get('at'))
        total['closed'] |= kind in {'finish','retire'}
        unique = True
        if row.get('call_id'):
            unique = bool(self.db.execute('INSERT OR IGNORE INTO seen VALUES(?,?,?,?,?)',
                (g,run,session,_key(row.get('event')),_key(row['call_id']))).rowcount)
        if unique:
            if kind not in {'attach','usage','usage_boundary'}:
                sample = self._stored('samples',(g,run,session),None)
                if sample is None:
                    sample = dict(first=None, maximum={}, count=0)
                    total['unmeasured'] += 1
                value = row.get('usage')
                if value:
                    if not isinstance(value, dict) or len(value) > 32 or any(type(v) is not int or v < 0 for v in value.values()):
                        raise ValueError('invalid counters')
                    if sample['first'] is None:
                        sample = dict(first={}, maximum={}, count=len(value))
                        total['unmeasured'] -= 1
                        total['measured'] += 1
                        for name, initial in value.items():
                            self._counter(run, session, name, dict(first=initial, maximum=initial))
                            previous = self._counter_total(run, name)
                            if previous is None:
                                total['categories'] += 1
                            self._counter(run, '', name, previous if previous is not None else 0)
                    else:
                        category = ''
                        count = 0
                        while True:
                            stored = self._reducer_query(
                                'SELECT category,@name@,@body@ FROM counters WHERE g=? AND run=? AND session=? '
                                'AND category>? ORDER BY category LIMIT 1',
                                (g, run, session, category), ('name', 'body'))
                            if stored is None:
                                if count != sample['count']:
                                    raise CacheCorrupt('invalid_cache_sample_categories')
                                break
                            count += 1
                            if count > 32:
                                raise CacheCorrupt('invalid_cache_sample_categories')
                            category, name, raw = stored
                            name = self._counter_name(category, name)
                            counter = _decode(raw, 'counter')
                            new = max(counter['maximum'], value[name])
                            previous = self._counter_total(run, name)
                            if previous is None:
                                raise CacheCorrupt('invalid_cache_sample_total')
                            self._counter(run, '', name, previous + new - counter['maximum'])
                            counter['maximum'] = new
                            self._counter(run, session, name, counter)
                self.db.execute('INSERT OR REPLACE INTO samples VALUES(?,?,?,?)',
                                (g,run,session,self._write(sample, 'samples')))
            if kind in {'start','progress'}:
                total.update(epoch=pos,recent_tools=0,recent_dispatches=0,repetition=0)
            if kind == 'progress':
                total['milestones'] += 1
                if total['milestones'] == 1:
                    self._part(run, 'total', '', 'first_phase', row.get('phase'))
                self._part(run, 'total', '', 'last_phase', row.get('phase'))
                self.db.execute('INSERT INTO fields(g,run,family,key,pos,body) VALUES(?,?,?,?,?,?)', (g,run,'milestone',str(pos),pos,self._write({k:row[k] for k in ('phase','note','at') if k in row}, 'milestone')))
            if kind == 'finish':
                total['finished'] = True
                self._part(run, 'total', '', 'finished_at', row.get('at'))
                self._part(run, 'total', '', 'outcome', row.get('note'))
            if row.get('event') == 'PreToolUse':
                total['tools'] += 1
                total['recent_tools'] += 1
                total['recent_dispatches'] += row.get('tool') in SPAWN
                if row.get('action'):
                    key = (g,run,total['epoch'],_key(row['action']))
                    prior = self.db.execute('SELECT substr(n,1,66) FROM actions WHERE g=? AND run=? AND epoch=? AND action=?',key).fetchone()
                    if prior and (not isinstance(prior[0], str) or not prior[0].isascii()
                                  or not prior[0].isdigit() or len(prior[0]) > 64):
                        raise CacheCorrupt('invalid_cache_action')
                    n = int(prior[0])+1 if prior else 1
                    encoded = str(n)
                    if len(encoded) > 64:
                        raise RepresentationError('derived_representation:action')
                    self._charge(encoded)
                    self.db.execute('INSERT OR REPLACE INTO actions VALUES(?,?,?,?,?)',(*key,encoded))
                    total['repetition'] = max(total['repetition'], n)
            outcome = row.get('outcome')
            if outcome in ('admitted','denied','error','observed'):
                total[outcome] += 1
            total['approximate'] |= kind == 'hook' and not row.get('call_id')
        self.db.execute('INSERT OR REPLACE INTO totals VALUES(?,?,?)',(g,run,self._write(total, 'totals')))
        if kind == 'start' and row.get('hook_setup') == 'host_adapter':
            self.meta['host_adapter'] = True

    def coverage(self) -> dict[str, Any]:
        m = self.meta
        complete = bool(m and self.source_present and not self.error and m.get('offset') == m.get('size') and not m.get('discard'))
        return dict(status='unavailable' if self.db is None and self.error else 'complete' if complete and not m.get('errors') else 'partial' if m else 'unavailable',
                    caught_up=complete, generation=self.generation or None, offset=m.get('offset',0),
                    target=m.get('target'), source_bytes=m.get('size'),
                    backlog_bytes=max(0,m.get('size',0)-m.get('offset',0)),
                    errors=m.get('errors',[]) + ([self.error] if self.error else []),
                    source_present=self.source_present,
                    details_partial=self.detail_partial, continuations=dict(self.continuations),
                    queries=dict(self.queries),
                    runs={run: {'observed': observed, 'counts_complete': bool(observed and complete and not m.get('errors'))}
                          for run, observed in self.run_observations.items()})

    def _query_identity(self, run: str, family: str, filter_value: Any = None) -> str:
        # Filter values can themselves be maximum-sized admitted strings. Keep
        # transport/coverage identity bounded independently of their length.
        return hashlib.sha256(_json([self.generation, _key(run), family, filter_value]).encode()).hexdigest()

    def _page_status(self, identity: str, cursor: Any, reason: str) -> dict[str, Any]:
        position = list(cursor) if isinstance(cursor, tuple) else cursor
        continuation = None if cursor is None else {'identity': identity, 'position': position}
        status = dict(complete=reason == 'complete', reason=reason, next=cursor, continuation=continuation)
        self.queries[identity] = status
        if cursor is None:
            self.continuations.pop(identity, None)
        else:
            self.continuations[identity] = continuation
        if reason != 'complete':
            self.detail_partial = True
        return status

    def _query_result(self, rows: list[tuple[Any, ...]], reason: str) -> QueryResult:
        if reason != 'complete':
            self.detail_partial = True
            self.query_failures += 1
        return QueryResult(rows, reason)

    def _query(self, sql: str, args: Any = (), limit: int = 256, *, shape: str | None = None) -> QueryResult:
        if self.db is None:
            return self._query_result([], 'unavailable')
        result: list[tuple[Any, ...]] = []
        try:
            body_index = 2 if 'SELECT pos,key,' in sql else 1 if 'SELECT pos,' in sql else 0
            sql = sql.replace('SELECT name,body ', f'SELECT substr(CAST(name AS BLOB),1,65),substr(CAST(body AS BLOB),1,{RECORD_BYTES+1}) ').replace('SELECT category,name,body ', f'SELECT substr(CAST(category AS BLOB),1,65),substr(CAST(name AS BLOB),1,{RECORD_BYTES+1}),substr(CAST(body AS BLOB),1,{RECORD_BYTES+1}) ')
            sql = sql.replace('SELECT body ', f'SELECT substr(CAST(body AS BLOB),1,{RECORD_BYTES+1}) ').replace('SELECT pos,body ', f'SELECT pos,substr(CAST(body AS BLOB),1,{RECORD_BYTES+1}) ').replace('SELECT pos,key,body,rank ', f'SELECT pos,substr(CAST(key AS BLOB),1,{RECORD_BYTES+1}),substr(CAST(body AS BLOB),1,{RECORD_BYTES+1}),rank ')
            sql = sql.replace('SELECT pos,root ', 'SELECT pos,substr(CAST(root AS BLOB),1,65) ')
            cursor = self.db.execute(sql, args)
            for _ in range(limit):
                # Every query projects at most two full-sized cells plus
                # bounded scalar/index columns. Reserve before SQLite returns
                # a row, including one rejected by the output allowance.
                if self.budget.query_rows >= QUERY_ROWS:
                    return self._query_result(result, 'row_budget')
                if QUERY_BYTES - self.budget.query_bytes < QUERY_RESERVATION_BYTES:
                    return self._query_result(result, 'query_budget')
                if self.budget.output_bytes >= OUTPUT_BYTES:
                    return self._query_result(result, 'output_budget')
                row = cursor.fetchone()
                if row is None:
                    return self._query_result(result, 'complete')
                self.budget.query_rows += 1
                size = sum(len(v) if isinstance(v, bytes) else len(v.encode())
                           if isinstance(v, str) else 8 for v in row)
                self.budget.query_bytes += size
                try:
                    row = tuple(v.decode('utf-8') if isinstance(v, bytes) else v for v in row)
                except UnicodeError as exc:
                    raise CacheCorrupt('invalid_cache_encoding') from exc
                if shape is not None:
                    body_shape = 'captured:reviews' if shape == 'captured' and row[1] == 'reviews' else shape
                    _decode(row[body_index], body_shape)
                if size + self.budget.output_bytes > OUTPUT_BYTES:
                    return self._query_result(result, 'output_budget')
                self.budget.output_bytes += size
                result.append(row)
            return self._query_result(result, 'limit')
        except CacheCorrupt as exc:
            self._cache_failure(exc)
            return QueryResult([], 'corrupt')
        except sqlite3.DatabaseError as exc:
            self._cache_failure(exc)
            return QueryResult([], 'unavailable')

    def start(self, run: str | None = None, owner: str | None = None) -> dict[str, Any] | None:
        if run is not None:
            sql = "SELECT body FROM events WHERE g=? AND kind='start' AND run=? ORDER BY pos DESC LIMIT 1"
            params = [_key(run)]
        elif owner is not None:
            sql = "SELECT body FROM events WHERE g=? AND kind='start' AND owner=? ORDER BY pos DESC LIMIT 1"
            params = [_key(owner)]
        else:
            sql = "SELECT body FROM events WHERE g=? AND kind='start' ORDER BY pos DESC LIMIT 1"
            params = []
        rows = self._query(sql, [self.generation,*params], 2, shape='start')
        return cast(dict[str, Any], json.loads(rows[0][0])) if rows else None

    def active(self, session: str, parent: str | None = None) -> dict[str, Any] | None:
        if self.coverage()['status'] != 'complete':
            return None
        owners = {_key(session)} | ({_key(parent)} if parent is not None else set())
        frontier = 9223372036854775807
        # Indexed reverse frontier preserves the legacy single reverse pass,
        # rather than inventing transitive edges that occur later in the log.
        while True:
            candidates: list[tuple[Any, ...]] = []
            for owner in owners:
                rows = self._query('SELECT pos,root FROM lineage WHERE g=? AND owner=? AND pos<? ORDER BY pos DESC LIMIT 1', (self.generation,owner,frontier),2)
                if not rows.complete:
                    return None
                candidates += rows
            if len(owners) > 128:
                return None
            if not candidates:
                break
            position, root = max(candidates)
            owners.add(root)
            frontier = position
        starts: list[tuple[Any, ...]] = []
        for owner in owners:
            rows = self._query('SELECT pos,body FROM events WHERE g=? AND kind=\'start\' AND owner=? ORDER BY pos DESC LIMIT 1',(self.generation,owner),2, shape='start')
            if not rows.complete:
                return None
            starts += rows
        if not starts:
            return None
        run: dict[str, Any] = json.loads(max(starts)[1])
        total = self.total(run['run'])
        return None if total['closed'] or not total.get('_available', True) else run

    def total(self, run: str) -> dict[str, Any]:
        rows = self._query('SELECT body FROM totals WHERE g=? AND run=?',(self.generation,_key(run)),2, shape='totals')
        if not rows:
            self.run_observations[_key(run)] = False if rows.complete else None
            # Global EOF does not prove this selected run ever occurred. In
            # particular a controller-synthesized start is not journal evidence.
            return {**_empty(), '_available': False, '_observed': False,
                    '_tokens_available': False}
        result: dict[str, Any] = json.loads(rows[0][0])
        result['_observed'] = result['raw'] > 0
        self.run_observations[_key(run)] = result['_observed']
        result['_available'] = rows.complete and result['_observed']
        try:
            # Preserve compact last-at/control availability before spending the
            # output allowance on a potentially large category dictionary.
            latest = self._query("SELECT name,body FROM parts WHERE g=? AND run=? AND family='total' AND key='' AND name='last_at'",
                                 (self.generation, _key(run)), 2)
            latest_complete = latest.complete
            for name, raw in latest:
                result[name] = _decode(raw, 'part')
            counters = self._query("SELECT category,name,body FROM counters WHERE g=? AND run=? AND session=''",
                                   (self.generation, _key(run)), QUERY_ROWS)
            tokens = {self._counter_name(category, name): _decode(raw, 'counter-total')
                      for category, name, raw in counters}
            result['_tokens_available'] = counters.complete
            if result['_tokens_available'] and len(tokens) != result['categories']:
                raise CacheCorrupt('invalid_cache_total_categories')
            # Never publish an incomplete counter dictionary as exact totals.
            result['tokens'] = tokens if result['_tokens_available'] else {}
            parts = self._query('SELECT name,body FROM parts WHERE g=? AND run=? AND family=\'total\' AND key=\'\' AND name!=\'last_at\' LIMIT 5',
                                (self.generation, _key(run)), 5)
            for name, raw in parts:
                if name not in TOTAL_PARTS:
                    raise CacheCorrupt('invalid_cache_total_part')
                result[name] = _decode(raw, 'part')
            result['_metadata_available'] = latest_complete and parts.complete
            expected_parts = {'last_at'}
            if result['milestones']:
                expected_parts.update(('first_phase', 'last_phase'))
            if result['finished']:
                expected_parts.update(('finished_at', 'outcome'))
            if result['_metadata_available'] and {name for name, _ in [*latest, *parts]} != expected_parts:
                raise CacheCorrupt('invalid_cache_total_parts')
        except CacheCorrupt as exc:
            self._cache_failure(exc)
            result['_available'] = False
        return result

    def counter_page(self, run: str, *, after: str = '', limit: int = 128,
                     continuation: dict[str, Any] | None = None) -> dict[str, Any]:
        """Page exact categories; local completion is separate from source EOF."""
        limit = min(max(1, limit), FIELD_PAGE_LIMIT)
        identity = self._query_identity(run, 'tokens')
        if continuation is not None and continuation.get('identity') == identity:
            after = continuation.get('position', '')
        if continuation is not None and (continuation.get('identity') != identity or not isinstance(after, str)):
            cursor: str | None = ''
            status = self._page_status(identity, cursor, 'cursor_mismatch')
            return dict(tokens={}, next_category=cursor, complete=False,
                        query=status, query_identity=identity, continuation=status['continuation'], coverage=self.coverage())
        rows = self._query("SELECT category,name,body FROM counters WHERE g=? AND run=? AND session='' "
                           'AND category>? ORDER BY category LIMIT ?',
                           (self.generation, _key(run), after, limit + 1), limit + 1)
        selected = rows[:limit]
        reason = 'limit' if len(rows) > limit else rows.reason
        cursor = (selected[-1][0] if selected else after) if reason != 'complete' else None
        try:
            values = {self._counter_name(category, name): _decode(raw, 'counter-total')
                      for category, name, raw in selected}
        except CacheCorrupt as exc:
            self._cache_failure(exc)
            values, cursor, reason = {}, after, 'corrupt'
        status = self._page_status(identity, cursor, reason)
        return dict(tokens=values, next_category=cursor,
                    complete=status['complete'],
                    query=status, query_identity=identity, continuation=status['continuation'], coverage=self.coverage())

    def fields_page(self, run: str, family: str, after: tuple[int, int, str] | None = None,
                    limit: int = 256, *, continuation: dict[str, Any] | None = None) -> dict[str, Any]:
        """Explicit local result; fields retains its historical tuple interface.

        Use the returned continuation with continuation= for JSON/restart-safe
        generation/run/family binding. Legacy primitive after= values are
        unbound positions; callers must
        compare query_identity and source offset themselves for those values.
        Neither form pins an immutable snapshot across source appends.
        """
        limit = min(max(1, limit), FIELD_PAGE_LIMIT)
        identity = self._query_identity(run, 'fields', family)
        if continuation is not None and continuation.get('identity') == identity:
            after = continuation.get('position')
        initial = after if after is not None else (-1, -1, '')
        valid = (isinstance(initial, (tuple, list)) and len(initial) == 3
                 and type(initial[0]) is int and type(initial[1]) is int and isinstance(initial[2], str))
        if not valid or continuation is not None and continuation.get('identity') != identity:
            cursor: tuple[int, int, str] | None = (-1, -1, '')
            status = self._page_status(identity, cursor, 'cursor_mismatch')
            return dict(items=[], next_position=cursor, query=status, query_identity=identity, continuation=status['continuation'])
        position, rank, key = initial
        rows = self._query('SELECT pos,key,body,rank FROM fields WHERE g=? AND run=? AND family=? AND (pos,rank,key)>(?,?,?) ORDER BY pos,rank,key LIMIT ?',
                           (self.generation,_key(run),family,position,rank,key,limit+1),limit+1, shape=family)
        reason = 'limit' if len(rows) > limit else rows.reason
        output = []
        last = cast(tuple[int, int, str], tuple(initial))
        try:
            for p, k, body, entry_rank in rows[:limit]:
                value = json.loads(body)
                if family == 'child':
                    value = self._parts(_key(run), family, k, _decode(body, 'child')['_parts'])
                    if value is None:
                        reason = 'unavailable' if self.db is None else 'hydration_budget'
                        break
                    _source(value)
                output.append((p, k, value))
                last = (p, entry_rank, k)
        except (CacheCorrupt, ValueError, TypeError, KeyError) as exc:
            self._cache_failure(exc if isinstance(exc, CacheCorrupt) else CacheCorrupt('invalid_cache_child'))
            output, last, reason = [], cast(tuple[int, int, str], tuple(initial)), 'corrupt'
        cursor = last if reason != 'complete' else None
        status = self._page_status(identity, cursor, reason)
        return dict(items=output, next_position=cursor, query=status, query_identity=identity, continuation=status['continuation'])

    def fields(self, run: str, family: str, after: tuple[int, int, str] | None = None, limit: int = 256) -> tuple[list[tuple[int, str, Any]], tuple[int, int, str] | None]:
        page = self.fields_page(run, family, after, limit)
        return page['items'], page['next_position']

    def legacy_only(self) -> bool:
        return bool(self.coverage()['caught_up'] and not self.meta.get('host_adapter'))

    def attachments(self, run: str) -> tuple[dict[str, Any], dict[str, Any]]:
        artifacts: dict[str, Any] = {}
        captured: dict[str, Any] = {}
        for family, target in (('artifact',artifacts), ('captured',captured)):
            values, _ = self.fields(run,family)
            target.update({key:value for _,key,value in values})
            evidence, _ = self.fields(run,family+'-evidence')
            if evidence:
                target['evidence'] = ({key:value for _,key,value in evidence} if family == 'captured'
                                      else [key for _,key,_ in evidence])
        return artifacts,captured

    def post_completion(self, run: str) -> list[dict[str, Any]]:
        rows = self._query('SELECT body FROM fields WHERE g=? AND run=? AND family=\'post-completion\' ORDER BY pos DESC LIMIT 32',
                           (self.generation,_key(run)),33, shape='post-completion')
        return [json.loads(row[0]) for row in reversed(rows)]

    def page(self, run: str, *, after: int = -1, limit: int = 256, kind: str | None = None,
             continuation: dict[str, Any] | None = None) -> dict[str, Any]:
        limit = min(max(1,limit),FIELD_PAGE_LIMIT)
        identity = self._query_identity(run, 'events', kind)
        if continuation is not None and continuation.get('identity') == identity:
            after = continuation.get('position', -1)
        if continuation is not None and (continuation.get('identity') != identity or type(after) is not int):
            cursor: int | None = -1
            status = self._page_status(identity, cursor, 'cursor_mismatch')
            return dict(rows=[], next_offset=cursor, complete=False,
                        query=status, query_identity=identity, continuation=status['continuation'], coverage=self.coverage())
        sql = ('SELECT pos,body FROM events WHERE g=? AND run=? AND pos>? AND kind=? ORDER BY pos LIMIT ?'
               if kind is not None else
               'SELECT pos,body FROM events WHERE g=? AND run=? AND pos>? ORDER BY pos LIMIT ?')
        params = [self.generation,_key(run),after] + ([kind] if kind is not None else []) + [limit+1]
        rows = self._query(sql,params,limit+1, shape='event')
        reason = 'limit' if len(rows) > limit else rows.reason
        selected = rows[:limit]
        cursor = (selected[-1][0] if selected else after) if reason != 'complete' else None
        status = self._page_status(identity, cursor, reason)
        return dict(rows=[json.loads(r[1]) for r in selected], next_offset=cursor,
                    complete=status['complete'],
                    query=status, query_identity=identity, continuation=status['continuation'], coverage=self.coverage())

    def rows(self, run: str) -> list[dict[str, Any]]:
        """Bounded compatibility input; caller must honor the page coverage."""
        return list(self.page(run)['rows'])

    def usage_rows(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        failures_before = self.query_failures
        observed = self._query('SELECT pos FROM events WHERE g=? AND run=? LIMIT 1',
                               (self.generation, _key(run['run'])), 2)
        self.usage_complete = bool(observed) and observed.complete and self.coverage()['status'] == 'complete'
        rows: list[dict[str, Any]] = []
        for family in ('session','child','reviewer','latest'):
            items, cursor = self.fields(run['run'],family,limit=128)
            if cursor is not None:
                self.usage_complete = False
            rows.extend(value for _,_,value in items)
        from .flow import measurement_time
        stamp = measurement_time(run.get('at'))
        if stamp:
            from datetime import datetime
            following = self._query('SELECT body FROM events WHERE g=? AND kind=\'start\' AND owner=? AND stamp>? AND run!=? ORDER BY stamp,pos LIMIT 1',
                                    (self.generation,_key(run.get('session')),datetime.fromisoformat(stamp).timestamp(),_key(run['run'])),2, shape='start')
            rows += [json.loads(r[0]) for r in following]
        self.usage_complete &= self.query_failures == failures_before
        return rows

    def has_closure(self, closure_id: str, kind: str | None = None) -> bool | None:
        if self.coverage()['status'] != 'complete':
            return None
        sql = ('SELECT pos FROM closure WHERE g=? AND id=? AND kind=? LIMIT 1' if kind else
               'SELECT pos FROM closure WHERE g=? AND id=? LIMIT 1')
        rows = self._query(sql,
                           [self.generation,_key(closure_id)]+([kind] if kind else []),2)
        return bool(rows) if rows.complete else None

    def summary(self, run: dict[str, Any]) -> dict[str, Any]:
        t = self.total(run['run'])
        milestones, _ = self.fields(run['run'],'milestone')
        phase = run.get('phase') or t['first_phase'] or 'product'
        warnings = []
        if t['recent_tools'] >= 20:
            warnings.append('20+ tool calls since reported progress: check the next deliverable and simplify.')
        if t['repetition'] >= 4:
            warnings.append('The same action was attempted 4+ times since progress: change approach or report the real blocker.')
        if t['recent_dispatches'] >= 6:
            warnings.append('6+ agent dispatches since progress: check whether the reviews justify their cost.')
        if t['tokens'].get('total_tokens',0) >= 100_000 and not t['milestones']:
            warnings.append('100k+ observed tokens without a milestone: reduce context and focus on a testable result.')
        unavailable = self.db is None or not t.get('_available', True) or not t['raw'] and self.coverage()['status'] != 'complete'
        return dict(mode='advisory',owner='orchestrator',run=run['run'],goal=run.get('goal'),
                    status='finished' if t['finished'] else 'active',outcome=t['outcome'],entry_phase=phase,
                    phase=t['last_phase'] if t['milestones'] else phase,milestones=[v for _,_,v in milestones],
                    started_at=run.get('at'),finished_at=t['finished_at'],tool_calls=None if unavailable else t['tools'],
                    hook_outcomes=dict(attempted=None if unavailable else t['tools'],**{k:None if unavailable else t[k] for k in ('admitted','denied','error','observed')},
                                       coverage='unknown' if unavailable else 'approximate' if t['approximate'] else 'identified'),
                    tokens=t['tokens'] if t['measured'] and not unavailable and t.get('_tokens_available', True) else None,
                    token_coverage=dict(measured_sessions=t['measured'],unmeasured_sessions=t['unmeasured'],
                                        basis='delta between first and latest observed native counters; lower bound'),
                    advice=warnings if self.coverage()['caught_up'] else [],
                    journal_coverage={**self.coverage(), 'run_observed': t.get('_observed', False),
                                      'run_counts_complete': not unavailable and self.coverage()['status'] == 'complete',
                                      'tokens_complete': t.get('_tokens_available', False),
                                      'metadata_complete': t.get('_metadata_available', False)})


def projection(workspace: str | Path) -> Projection:
    budget = _active.get()
    if budget is None:
        # Standalone callers own close(); entry points normally share an action.
        return Projection(workspace, Budget())
    key = str(Path(workspace).resolve())
    if key not in budget.projections:
        budget.projections[key] = Projection(workspace,budget)
    else:
        budget.projections[key].refresh()
    return budget.projections[key]
