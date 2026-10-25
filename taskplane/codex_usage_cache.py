"""Bounded, disposable Codex catalog and interval accounting.

Only normalized identity, usage and fingerprints are stored. Native files are
append-only sources; fixed anchors detect common replacement/truncation, not
arbitrary hidden middle edits. Budgets bound source bytes and logical SQL work,
not wall-clock time or SQLite's internal I/O. Catalog coverage excludes orphans.
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
from typing import Any, Iterator, Sequence

if __package__:
    from . import native_session_meter as _package_meter
    from . import claude_usage_cache as _package_locks
    meter = _package_meter
    native_locks = _package_locks
else:
    import native_session_meter as _flat_meter
    import claude_usage_cache as _flat_locks
    meter = _flat_meter
    native_locks = _flat_locks

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 1024
MAX_ROWS = 8192
MAX_ENTRIES = 128
MAX_VM_STEPS = 2_000_000
MAX_RECOVERY_BYTES = 8 * 1024 * 1024
ANCHOR_BYTES = 256
SCHEMA = 1


class Exhausted(ValueError):
    """The shared action allowance has no capacity for this operation."""


@dataclass
class Budget:
    max_bytes: int = MAX_BYTES
    max_records: int = MAX_RECORDS
    max_rows: int = MAX_ROWS
    max_entries: int = MAX_ENTRIES
    max_vm_steps: int = MAX_VM_STEPS
    max_recovery_bytes: int = MAX_RECOVERY_BYTES
    source_bytes: int = 0
    records: int = 0
    rows: int = 0
    entries: int = 0
    files: int = 0
    vm_steps: int = 0
    recovery_bytes: int = 0
    workspace: Path | None = None
    discovery_status: str = 'unavailable'
    replay_status: str = 'complete'
    catalog_status: str = 'unavailable'

    def take(self, name: str, count: int = 1) -> None:
        limit = {'source_bytes': min(self.max_bytes, meter.MAX_REPLAY_BYTES),
                 'records': self.max_records, 'rows': self.max_rows,
                 'entries': self.max_entries, 'files': self.max_entries,
                 'vm_steps': self.max_vm_steps, 'recovery_bytes': self.max_recovery_bytes}[name]
        if count < 0 or getattr(self, name) + count > limit:
            raise Exhausted(name)
        setattr(self, name, getattr(self, name) + count)

    @property
    def remaining_bytes(self) -> int:
        return max(0, min(self.max_bytes, meter.MAX_REPLAY_BYTES) - self.source_bytes)

    def coverage(self) -> dict[str, Any]:
        return {'discovery_status': self.discovery_status, 'catalog_status': self.catalog_status,
                'replay_status': self.replay_status, 'native_inventory_complete': False,
                'action_work': {k: getattr(self, k) for k in ('source_bytes', 'records', 'rows',
                               'entries', 'files', 'vm_steps', 'recovery_bytes')}}


_CURRENT: ContextVar[Budget | None] = ContextVar('codex_usage_budget', default=None)


@contextmanager
def action_budget(workspace: str | Path | None = None, *, budget: Budget | None = None) -> Iterator[Budget]:
    current = _CURRENT.get()
    if current is not None:
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


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'), sort_keys=True, allow_nan=False)


def _load(value: str) -> Any:
    if len(value) > 32768:
        raise ValueError('oversized cache metadata')
    try:
        decoded = json.loads(value)
    except (ValueError, RecursionError) as exc:
        raise ValueError('corrupt cache metadata') from exc
    if not isinstance(decoded, dict):
        raise ValueError('invalid cache metadata shape')
    return decoded


def _hash(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _home() -> Path:
    return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).resolve()


def cache_path(workspace: Path | None = None) -> Path:
    name = 'codex-usage-' + _hash([str(_home()), str(workspace), SCHEMA]) + '.sqlite3'
    if workspace is not None:
        if __package__:
            from .storage import runtime_file as _package_runtime_file
            runtime_file = _package_runtime_file
        else:
            from storage import runtime_file as _flat_runtime_file
            runtime_file = _flat_runtime_file
        return runtime_file(str(workspace), name)
    getuid = getattr(os, 'getuid', None)
    uid = getuid() if callable(getuid) else None
    user = str(uid) if uid is not None else _hash(str(Path.home()))
    base = Path(tempfile.gettempdir()).resolve() / ('taskplane-codex-usage-' + user)
    base.mkdir(mode=0o700, exist_ok=True)
    _safe(base)
    info = base.stat()
    if (not stat.S_ISDIR(info.st_mode)
            or uid is not None and info.st_uid != uid
            or os.name != 'nt' and info.st_mode & 0o077):
        raise ValueError('unsafe standalone cache directory')
    return base / name


def _safe(path: Path) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlinked native/cache source')


def _one(db: sqlite3.Connection, budget: Budget, sql: str, args: tuple[Any, ...] = ()) -> Any:
    budget.take('rows')
    return db.execute(sql, args).fetchone()


def _rows(db: sqlite3.Connection, budget: Budget, sql: str, args: tuple[Any, ...] = ()) -> list[Any]:
    result = []
    for row in db.execute(sql, args):
        budget.take('rows')
        result.append(row)
    return result


def _vm(db: sqlite3.Connection, budget: Budget) -> None:
    def progress() -> int:
        try:
            budget.take('vm_steps', 100)
            return 0
        except Exhausted:
            return 1
    db.set_progress_handler(progress, 100)


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, meta TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paths(path TEXT PRIMARY KEY, source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations(seq INTEGER PRIMARY KEY AUTOINCREMENT, gen TEXT NOT NULL,
 pos INTEGER NOT NULL, data TEXT NOT NULL, UNIQUE(gen,pos));
CREATE INDEX IF NOT EXISTS observations_generation ON observations(gen,seq);
CREATE TABLE IF NOT EXISTS queries(id TEXT PRIMARY KEY, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS query_sources(query TEXT NOT NULL, gen TEXT NOT NULL, cursor INTEGER NOT NULL,
 PRIMARY KEY(query,gen));
CREATE TABLE IF NOT EXISTS counters(query TEXT NOT NULL, scope TEXT NOT NULL, at REAL NOT NULL,
 ordinal INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(query,scope,at,ordinal));
CREATE TABLE IF NOT EXISTS responses(query TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(query,id));
CREATE TABLE IF NOT EXISTS discovery(id TEXT NOT NULL, path TEXT NOT NULL, parent TEXT, meta TEXT NOT NULL,
 PRIMARY KEY(id,path));
CREATE INDEX IF NOT EXISTS discovery_parent ON discovery(parent,id);
CREATE TABLE IF NOT EXISTS cursors(id TEXT PRIMARY KEY, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members(run TEXT NOT NULL, id TEXT NOT NULL, child TEXT NOT NULL,
 PRIMARY KEY(run,id));
CREATE TABLE IF NOT EXISTS results(run TEXT NOT NULL, id TEXT NOT NULL, value TEXT NOT NULL,
 PRIMARY KEY(run,id));
CREATE TABLE IF NOT EXISTS totals(run TEXT PRIMARY KEY, value TEXT NOT NULL);
'''


@contextmanager
def _database(budget: Budget) -> Iterator[sqlite3.Connection]:
    path = cache_path(budget.workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe(path)
    lock = path.with_suffix('.lock')
    _safe(lock)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    db = None
    stream = None
    release = None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('nonregular cache lock')
        stream = os.fdopen(fd, 'r+b', closefd=False)
        try:
            release = native_locks._acquire_lock(stream)
        except ImportError as exc:
            raise ValueError('cache_lock_unsupported') from exc
        for suffix in ('-journal', '-wal', '-shm'):
            sidecar = Path(str(path) + suffix)
            _safe(sidecar)
            if sidecar.exists():
                budget.take('recovery_bytes', sidecar.stat().st_size)
        if path.exists() and not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('nonregular usage cache')
        try:
            db = sqlite3.connect(str(path), timeout=0)
            if getattr(db, 'setlimit', None) is not None:
                getattr(db, 'setlimit')(getattr(sqlite3, 'SQLITE_LIMIT_LENGTH'), 1024 * 1024)
            _vm(db, budget)
            db.execute('PRAGMA cache_size=-256')
            version = _one(db, budget, 'PRAGMA user_version')[0]
            if version not in (0, SCHEMA):
                raise ValueError('incompatible usage cache')
            db.executescript(_SCHEMA)
            db.execute(f'PRAGMA user_version={SCHEMA}')
        except sqlite3.DatabaseError as exc:
            if db is not None:
                db.close()
                db = None
            # Recovery is only for malformed derived storage, never locks or a
            # VM interruption. Report this attempt as partial; rebuild next time.
            if 'malformed' in str(exc) or 'not a database' in str(exc):
                suffix = '.corrupt-' + uuid.uuid4().hex
                for item in (path, *(Path(str(path) + s) for s in ('-journal', '-wal', '-shm'))):
                    if item.exists():
                        os.replace(item, Path(str(item) + suffix))
            raise
        yield db
    finally:
        try:
            if db is not None:
                db.close()
        finally:
            try:
                if release is not None:
                    release()
            finally:
                if stream is not None:
                    stream.close()
                os.close(fd)


def _read(stream: Any, size: int, budget: Budget, *, line: bool = False) -> bytes:
    if size > budget.remaining_bytes:
        raise Exhausted('source_bytes')
    data = stream.readline(size) if line else stream.read(size)
    budget.take('source_bytes', len(data))
    return bytes(data)


def _anchor(stream: Any, offset: int, budget: Budget) -> str:
    stream.seek(max(0, offset - ANCHOR_BYTES))
    return hashlib.sha256(_read(stream, min(offset, ANCHOR_BYTES), budget)).hexdigest()


def _instant(value: Any) -> float:
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None or not math.isfinite(parsed.timestamp()):
        raise ValueError('native timestamp lacks timezone')
    return parsed.timestamp()


def _observation(raw: bytes, metadata: dict[str, Any], position: int) -> dict[str, Any] | None:
    record = json.loads(raw)
    if not isinstance(record, dict):
        return None
    payload = record.get('payload')
    if not isinstance(payload, dict):
        return None
    native = record.get('type') == 'token_usage_record'
    legacy = record.get('type') == 'event_msg' and payload.get('type') == 'token_count'
    if not native and not legacy:
        return None
    if native and payload.get('thread_id') != metadata['session_id']:
        return None
    if legacy and metadata.get('forked_history'):
        return None
    at = _instant(record.get('timestamp'))
    ordinal = record.get('ordinal')
    basis = 'native'
    if ordinal is None:
        ordinal, basis = position, 'physical position'
    meter._nonnegative(ordinal, 'ordinal')
    total = payload.get('thread_token_usage') if native else payload.get('info', {}).get('total_token_usage')
    response = payload.get('response_id')
    if native and 'usage' in payload and (not isinstance(response, str) or not response):
        raise ValueError('missing_response_identity')
    return {'at': at, 'observed_at': record['timestamp'], 'ordinal': ordinal, 'ordinal_basis': basis,
            'counter_scope': 'thread' if native else 'segment',
            'usage': meter.owned_counts(total) if total is not None else None,
            'response': _hash(response) if native and 'usage' in payload else None,
            'delta': meter.owned_counts(payload['usage']) if native and 'usage' in payload else None,
            'record_hash': hashlib.sha256(raw.rstrip(b'\r\n')).hexdigest()}


def _ingest(db: sqlite3.Connection, path: Path, session: str, budget: Budget) -> dict[str, Any]:
    budget.take('files')
    _safe(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except (FileNotFoundError, PermissionError):
        # Replay already admitted observations, including their fatal state.
        # Absence is not a new measurement or permission to trust a saved total.
        row = _one(db, budget, '''SELECT substr(meta,1,32769) FROM sources
            JOIN paths ON sources.id=paths.source WHERE paths.path=?''', (str(path),))
        if not row:
            raise
        cached: dict[str, Any] = _load(row[0])
        if cached['metadata']['session_id'] != session:
            raise meter.CounterChronologyError('foreign cached source identity')
        return {**cached, 'unavailable': True}
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError('native source is not regular')
        raw_meta = _read(stream, min(st.st_size, meter.MAX_METADATA_BYTES), budget, line=True)
        metadata, record = meter._session_metadata(raw_meta)
        if len(_dump(metadata)) > 16384:
            raise ValueError('oversized native metadata')
        if metadata['session_id'] != session:
            raise meter.CounterChronologyError('foreign native source identity')
        identity = _hash([st.st_dev, st.st_ino, session])
        row = _one(db, budget, 'SELECT substr(meta,1,32769) FROM sources WHERE id=?', (identity,))
        prior_path = _one(db, budget, 'SELECT source FROM paths WHERE path=?', (str(path),))
        meta = _load(row[0]) if row else None
        metadata_hash = hashlib.sha256(record).hexdigest()
        changed = False
        if meta:
            changed = (meta['metadata_hash'] != metadata_hash or meta['offset'] > st.st_size
                       or meta['size'] > st.st_size)
            if not changed:
                changed = (meta['prefix'] != _anchor(stream, meta['prefix_size'], budget)
                           or meta['anchor'] != _anchor(stream, meta['offset'], budget))
        if meta is None or changed:
            meta = {'gen': uuid.uuid4().hex, 'offset': 0, 'size': st.st_size, 'prefix_size': 0,
                    'prefix': hashlib.sha256(b'').hexdigest(), 'anchor': hashlib.sha256(b'').hexdigest(),
                    'metadata_hash': metadata_hash, 'metadata': metadata, 'dev': st.st_dev,
                    'ino': st.st_ino, 'errors': [], 'discard': False, 'latest': None}
            if changed or prior_path and prior_path[0] != identity:
                meta['errors'].append('source_generation_changed')
        # The source cursor and normalized rows are one transaction. A record
        # needs a complete-record allowance; fragments are never persisted.
        target = st.st_size
        stream.seek(meta['offset'])
        initial_bytes, initial_records = budget.source_bytes, budget.records
        byte_share = max(meter.MAX_RECORD_BYTES + 6 * ANCHOR_BYTES + 1, budget.max_bytes // 2)
        record_share = max(1, budget.max_records // 2)
        while (meta['offset'] < target and budget.records < budget.max_records
               and budget.source_bytes - initial_bytes < byte_share
               and budget.records - initial_records < record_share):
            room = budget.remaining_bytes - 2 * ANCHOR_BYTES
            if room <= 0:
                break
            position = stream.tell()
            if meta['discard']:
                raw = _read(stream, min(room, meter.MAX_RECORD_BYTES, target - position), budget, line=True)
                meta['offset'] = stream.tell()
                meta['discard'] = not raw.endswith(b'\n')
                continue
            limit = min(meter.MAX_RECORD_BYTES + 1, target - position)
            if room < limit:
                break
            raw = _read(stream, limit, budget, line=True)
            if len(raw) > meter.MAX_RECORD_BYTES:
                meta['discard'] = not raw.endswith(b'\n')
                meta['errors'] = sorted(set(meta['errors']) | {'oversized_record'})
                meta['offset'] = stream.tell()
                continue
            if not raw.endswith(b'\n'):
                break
            budget.take('records')
            try:
                observation = _observation(raw, metadata, position)
                if observation is not None:
                    db.execute('INSERT OR IGNORE INTO observations(gen,pos,data) VALUES(?,?,?)',
                               (meta['gen'], position, _dump(observation)))
                    if observation['usage'] is not None and (meta['latest'] is None or
                            meter._counter_order(observation) >= meter._counter_order(meta['latest'])):
                        meta['latest'] = observation
            except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
                meta['errors'] = sorted(set(meta['errors']) | {'invalid_counter_record'})
            meta['offset'] = stream.tell()
        after = os.fstat(stream.fileno())
        if (st.st_dev, st.st_ino) != (after.st_dev, after.st_ino) or after.st_size < target:
            raise ValueError('native source changed during replay')
        # Recheck old anchors before publishing any newly read observations.
        if row and not changed:
            old = _load(row[0])
            if old['anchor'] != _anchor(stream, old['offset'], budget):
                raise ValueError('native source changed during replay')
        meta['prefix_size'] = min(meta['offset'], ANCHOR_BYTES)
        meta['prefix'] = _anchor(stream, meta['prefix_size'], budget)
        meta['anchor'] = _anchor(stream, meta['offset'], budget)
        meta['size'] = target
        meta['complete'] = meta['offset'] == target and not meta['discard']
        db.execute('INSERT OR REPLACE INTO sources VALUES(?,?)', (identity, _dump(meta)))
        db.execute('INSERT OR REPLACE INTO paths VALUES(?,?)', (str(path), identity))
        return meta


def _counter(db: sqlite3.Connection, query: str, scope: str, sample: dict[str, Any], state: dict[str, Any], budget: Budget) -> None:
    key = (query, scope, sample['at'], sample['ordinal'])
    old = _one(db, budget, 'SELECT substr(data,1,32769) FROM counters WHERE query=? AND scope=? AND at=? AND ordinal=?', key)
    if old:
        if _load(old[0])['usage'] != sample['usage']:
            state['fatal'] = sorted(set(state['fatal']) | {'conflicting_counter'})
        return
    neighbors = [
        _one(db, budget, '''SELECT substr(data,1,32769) FROM counters WHERE query=? AND scope=? AND (at,ordinal)<(?,?)
            ORDER BY at DESC,ordinal DESC LIMIT 1''', key),
        _one(db, budget, '''SELECT substr(data,1,32769) FROM counters WHERE query=? AND scope=? AND (at,ordinal)>(?,?)
            ORDER BY at,ordinal LIMIT 1''', key)]
    for before, after in (( _load(neighbors[0][0]) if neighbors[0] else None, sample),
                          (sample, _load(neighbors[1][0]) if neighbors[1] else None)):
        if before is not None and after is not None:
            try:
                meter.counter_pair(before, after, 'native replay')
            except meter.CounterChronologyError as exc:
                code = 'counter_reset' if 'backwards' in str(exc) else 'conflicting_counter'
                state['fatal'] = sorted(set(state['fatal']) | {code})
    db.execute('INSERT INTO counters VALUES(?,?,?,?,?)', (*key, _dump(sample)))


def _reduce(db: sqlite3.Connection, query: str, sources: list[dict[str, Any]], start: float, end: float | None,
            budget: Budget) -> dict[str, Any]:
    saved = _one(db, budget, 'SELECT substr(state,1,32769) FROM queries WHERE id=?', (query,))
    state = _load(saved[0]) if saved else {'sum': dict.fromkeys(meter.USAGE_KEYS, 0), 'responses': 0,
                                         'owned': False, 'fatal': [], 'complete': False}
    complete = True
    for meta in sources:
        gen = meta['gen']
        prior = _one(db, budget, 'SELECT cursor FROM query_sources WHERE query=? AND gen=?', (query, gen))
        cursor = prior[0] if prior else 0
        # Reserve enough SQL rows for each observation's identity and neighbors.
        limit = max(0, min(256, (budget.max_rows - budget.rows - 32) // 8))
        batch = _rows(db, budget, 'SELECT seq,substr(data,1,32769) FROM observations WHERE gen=? AND seq>? ORDER BY seq LIMIT ?',
                      (gen, cursor, limit))
        for seq, encoded in batch:
            sample = _load(encoded)
            if end is None or sample['at'] < end:
                if sample['usage'] is not None:
                    _counter(db, query, 'thread' if sample['counter_scope'] == 'thread' else gen, sample, state, budget)
                if sample['response'] is not None:
                    state['owned'] = True
                    prior_response = _one(db, budget, 'SELECT substr(data,1,32769) FROM responses WHERE query=? AND id=?',
                                          (query, sample['response']))
                    data = {'at': sample['at'], 'usage': sample['delta']}
                    if prior_response and _load(prior_response[0]) != data:
                        state['fatal'] = sorted(set(state['fatal']) | {'conflicting_response'})
                    elif not prior_response:
                        db.execute('INSERT INTO responses VALUES(?,?,?)', (query, sample['response'], _dump(data)))
                        if sample['at'] >= start:
                            state['responses'] += 1
                            for key in meter.USAGE_KEYS:
                                state['sum'][key] += sample['delta'][key]
            cursor = seq
        more = _one(db, budget, 'SELECT seq FROM observations WHERE gen=? AND seq>? LIMIT 1', (gen, cursor))
        complete = complete and not more
        db.execute('INSERT OR REPLACE INTO query_sources VALUES(?,?,?)', (query, gen, cursor))
    state['complete'] = complete
    db.execute('INSERT OR REPLACE INTO queries VALUES(?,?)', (query, _dump(state)))
    return state


def _result(db: sqlite3.Connection, query: str, sources: list[dict[str, Any]], state: dict[str, Any],
            start: float, end: float | None, budget: Budget) -> dict[str, Any]:
    errors = set(state['fatal'])
    snapshots = []
    for meta in sources:
        errors.update(meta['errors'])
        if not meta['complete']:
            errors.add('replay_pending')
        latest = meta['latest']
        if latest is not None:
            snapshots.append({**meta['metadata'], **latest, 'source': {'device': meta['dev'], 'inode': meta['ino'],
                'metadata_record_sha256': meta['metadata_hash'], 'counter_record_sha256': latest['record_hash']}})
    fatal = bool(state['fatal']) or bool(errors & {'source_generation_changed', 'invalid_counter_record'})
    try:
        meter.validate_segment_lineage(snapshots)
    except meter.CounterChronologyError as exc:
        errors.add(str(exc))
        fatal = True
    if not state['complete']:
        errors.add('reduction_pending')
    thread = _one(db, budget, 'SELECT substr(data,1,32769) FROM counters WHERE query=? AND scope=? ORDER BY at DESC,ordinal DESC LIMIT 1',
                  (query, 'thread'))
    scope = 'thread' if thread else sources[0]['gen'] if len(sources) == 1 else ''
    if not scope and sources:
        errors.add('legacy_segment_boundaries_unknown')
    latest = thread or _one(db, budget,
            'SELECT substr(data,1,32769) FROM counters WHERE query=? AND scope=? ORDER BY at DESC,ordinal DESC LIMIT 1', (query, scope))
    before = _one(db, budget, '''SELECT substr(data,1,32769) FROM counters WHERE query=? AND scope=? AND at<?
                  ORDER BY at DESC,ordinal DESC LIMIT 1''', (query, scope, start))
    native = _load(latest[0]) if latest else None
    if not thread and len(sources) > 1:
        legacy: list[dict[str, Any]] = []
        for source in sources:
            row = _one(db, budget, 'SELECT substr(data,1,32769) FROM counters WHERE query=? AND scope=? ORDER BY at DESC,ordinal DESC LIMIT 1',
                       (query, source['gen']))
            if row:
                legacy.append(_load(row[0]))
        if len(legacy) == len(sources):
            last_legacy = max(legacy, key=meter._counter_order)
            native = dict(last_legacy)
            native['usage'] = {k: sum(row['usage'][k] for row in legacy) for k in meter.USAGE_KEYS}
    baseline = _load(before[0])['usage'] if before else None
    resumed = any(s['metadata']['resumed'] for s in sources)
    if baseline is None and sources and not resumed and not errors:
        baseline = dict.fromkeys(meter.USAGE_KEYS, 0)
    usage, basis = None, 'unavailable interval'
    if state['owned']:
        usage, basis = dict(state['sum']), 'owned response replay [start, end)'
        if resumed and baseline is None:
            errors.add('resumed_baseline_unavailable')
        if baseline is not None and native is not None and any(
                usage[k] != native['usage'][k] - baseline[k] for k in meter.USAGE_KEYS):
            # Missing response usage can leave a valid recorded subtotal below
            # the cumulative delta. Preserve it as partial; do not infer the gap.
            errors.add('owned_counter_mismatch')
    elif baseline is not None and native is not None and not errors:
        usage = {k: native['usage'][k] - baseline[k] for k in meter.USAGE_KEYS}
        if any(v < 0 for v in usage.values()):
            errors.add('counter_reset')
            fatal = True
        basis = 'owned session cumulative boundaries [start, end)'
    if fatal or 'source_generation_changed' in errors or not state['complete']:
        usage = None
    if not sources:
        errors.add('native_source_unavailable')
    unavailable = any(s.get('unavailable') for s in sources)
    return {'usage': usage, 'native_usage': native['usage'] if native else None, 'baseline': baseline,
            'status': 'partial' if errors else 'recorded; native counter unavailable' if unavailable and usage is not None
                      else 'measured' if usage is not None else 'unavailable',
            'basis': basis, 'errors': sorted(errors), 'responses': state['responses'],
            'attribution_unknown': fatal, 'replay_status': 'partial' if errors else 'complete',
            'measured_at': datetime.fromtimestamp(native['at'], timezone.utc).isoformat() if native else None,
            'source_unavailable': unavailable,
            'interval': {'start': start, 'end_exclusive': end}}


def interval(paths: Sequence[str | Path], session: str, *, start: float, end: float | None, budget: Budget) -> dict[str, Any]:
    before = budget.source_bytes
    fallback: dict[str, Any] = {'usage': None, 'native_usage': None, 'baseline': None, 'status': 'partial', 'basis': 'unavailable interval',
                'errors': [], 'responses': 0, 'measured_at': None, 'attribution_unknown': False,
                'interval': {'start': start, 'end_exclusive': end}, 'replay_status': 'partial'}
    try:
        if min(budget.max_bytes, meter.MAX_REPLAY_BYTES) < meter.MAX_RECORD_BYTES + meter.MAX_METADATA_BYTES + 6 * ANCHOR_BYTES + 1:
            raise ValueError('budget_too_small')
        names = []
        for value in paths:
            budget.take('entries')
            path = str(Path(value).absolute())
            if path not in names:
                names.append(path)
        sources = []
        with _database(budget) as db:
            for name in sorted(names):
                try:
                    with db:
                        sources.append(_ingest(db, Path(name), session, budget))
                except (meter.CounterChronologyError, meter.NativeIdentityError):
                    # A failed identity check must also invalidate the cached
                    # source, so later disappearance cannot resurrect it.
                    with db:
                        prior = _one(db, budget, '''SELECT sources.id,substr(meta,1,32769)
                            FROM sources JOIN paths ON sources.id=paths.source WHERE paths.path=?''', (name,))
                        if prior:
                            invalid = _load(prior[1])
                            invalid['errors'] = sorted(set(invalid['errors']) | {'invalid_counter_record'})
                            db.execute('UPDATE sources SET meta=? WHERE id=?', (_dump(invalid), prior[0]))
                    raise
            # Hardlinks and archive aliases share the same physical generation.
            sources = list({s['gen']: s for s in sources}.values())
            query = _hash([SCHEMA, session, start, end, sorted(s['gen'] for s in sources)])
            with db:
                state = _reduce(db, query, sources, start, end, budget)
            fallback = _result(db, query, sources, state, start, end, budget)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        fallback['errors'] = [str(exc)[:200] or type(exc).__name__]
        fallback['attribution_unknown'] = isinstance(exc, (meter.CounterChronologyError, meter.NativeIdentityError))
    fallback['bytes_read'] = budget.source_bytes - before
    if fallback['replay_status'] != 'complete':
        budget.replay_status = 'partial'
    return fallback


def _metadata(path: Path, expected: str, budget: Budget) -> dict[str, Any]:
    budget.take('files')
    _safe(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError('native metadata is not regular')
        raw = _read(stream, min(st.st_size, meter.MAX_METADATA_BYTES), budget, line=True)
    meta, _ = meter._session_metadata(raw)
    if meta['session_id'] != expected:
        raise meter.CounterChronologyError('catalog identity disagrees with native metadata')
    if len(_dump(meta)) > 16384 or len(str(path)) > 4096:
        raise ValueError('oversized native metadata')
    return {**meta, 'path': str(path)}


@contextmanager
def _catalog(budget: Budget) -> Iterator[sqlite3.Connection]:
    # Exact, locally verified compatibility profile. No inventory/glob fallback.
    path = _home() / 'state_5.sqlite'
    _safe(path)
    if not path.is_file():
        raise ValueError('native_catalog_unavailable')
    journal = Path(str(path) + '-journal')
    _safe(journal)
    if journal.exists() and journal.stat().st_size:
        raise ValueError('native_catalog_recovery_required')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0)
    try:
        if getattr(db, 'setlimit', None) is not None:
            getattr(db, 'setlimit')(getattr(sqlite3, 'SQLITE_LIMIT_LENGTH'), 65536)
        _vm(db, budget)
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA cache_size=-256')
        fields = _rows(db, budget, 'SELECT substr(name,1,128),substr(type,1,32),pk FROM pragma_table_info(?) LIMIT 32', ('threads',))
        columns = {row[0]: (row[1].upper(), row[2]) for row in fields}
        if (columns.get('id') != ('TEXT', 1) or columns.get('rollout_path', ('', 0))[0] != 'TEXT'
                or sum(bool(value[1]) for value in columns.values()) != 1):
            raise ValueError('unsupported_native_catalog_schema')
        for sql, args in [('SELECT substr(id,1,513),substr(rollout_path,1,4097) FROM threads WHERE id=?', ('',)),
                          ('SELECT substr(id,1,513),substr(rollout_path,1,4097) FROM threads WHERE id>? AND id<=? ORDER BY id LIMIT ?', ('', '~', 1))]:
            plans = _rows(db, budget, 'EXPLAIN QUERY PLAN ' + sql, args)
            detail = ' '.join(str(row[-1]).upper() for row in plans)
            if 'SEARCH' not in detail or 'SCAN ' in detail or 'TEMP B-TREE' in detail:
                raise ValueError('unsupported_native_catalog_query_plan')
        yield db
    finally:
        db.close()


def _remember(db: sqlite3.Connection, sid: str, name: str, budget: Budget) -> None:
    if not isinstance(sid, str) or len(sid) > 512 or not isinstance(name, str) or len(name) > 4096:
        raise ValueError('oversized_catalog_row')
    path = Path(name)
    if not path.is_absolute():
        raise ValueError('relative_catalog_path')
    meta = _metadata(path, sid, budget)
    db.execute('INSERT OR REPLACE INTO discovery VALUES(?,?,?,?)',
               (sid, str(path), meta.get('parent_session_id'), _dump(meta)))


def discover(run: dict[str, Any], budget: Budget) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Advance one finite indexed sweep and return a bounded lineage page.

    The catalog can omit legacy/orphan files: discovery remains qualified even
    when this finite catalog sweep is complete. Known paths work without it.
    """
    diagnostics: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    budget.discovery_status = 'partial'
    key = _hash(['catalog', run.get('run'), run['session']])
    known = [run['session'], *run.get('worker_sessions', [])]
    try:
        with _database(budget) as db:
            state_row = _one(db, budget, 'SELECT substr(state,1,32769) FROM cursors WHERE id=?', (key,))
            state: dict[str, Any] = _load(state_row[0]) if state_row else {'last': '', 'upper': None, 'known': 0, 'service': ''}
            exact = dict(run.get('session_paths') or {})
            if run.get('transcript_path'):
                exact[run['session']] = [run['transcript_path']]
            # Bounded known-ID page, rotating independently of the catalog sweep.
            capacity = max(1, min(16, budget.max_entries // 4))
            admitted = []
            for i in range(min(capacity, len(known))):
                sid = str(known[(state['known'] + i) % len(known)])
                budget.take('entries')
                admitted.append(sid)
                names = exact.get(sid, [])
                if isinstance(names, (str, Path)):
                    names = [names]
                for name in names:
                    budget.take('entries')
                    try:
                        with db:
                            _remember(db, sid, str(name), budget)
                    except (OSError, ValueError) as exc:
                        diagnostics.append({'session': sid, 'reason': str(exc)[:200]})
            state['known'] = (state['known'] + len(admitted)) % max(1, len(known))
            try:
                with _catalog(budget) as catalog:
                    st = (_home() / 'state_5.sqlite').stat()
                    identity = [st.st_dev, st.st_ino]
                    if state.get('catalog') != identity:
                        state.update(catalog=identity, last='', upper=None)
                    for sid in admitted:
                        row = _one(catalog, budget, 'SELECT substr(id,1,513),substr(rollout_path,1,4097) FROM threads WHERE id=?', (sid,))
                        if row and sid not in exact:
                            budget.take('entries')
                            try:
                                with db:
                                    _remember(db, row[0], row[1], budget)
                            except (OSError, ValueError) as exc:
                                diagnostics.append({'session': sid, 'reason': str(exc)[:200]})
                    if state['upper'] is None:
                        upper = _one(catalog, budget, 'SELECT substr(id,1,513) FROM threads ORDER BY id DESC LIMIT 1')
                        upper_id = str(upper[0]) if upper else ''
                        if len(upper_id) > 512:
                            raise ValueError('oversized_catalog_identity')
                        state['upper'] = upper_id
                    allowance = min(32, max(1, budget.max_entries // 4), budget.max_entries - budget.entries)
                    page = _rows(catalog, budget, '''SELECT substr(id,1,513),substr(rollout_path,1,4097) FROM threads
                        WHERE id>? AND id<=? ORDER BY id LIMIT ?''', (state['last'], state['upper'], allowance))
                    for sid, name in page:
                        budget.take('entries')
                        if budget.remaining_bytes < meter.MAX_METADATA_BYTES + meter.MAX_RECORD_BYTES + 4096:
                            break
                        try:
                            with db:
                                _remember(db, sid, name, budget)
                        except (OSError, ValueError) as exc:
                            diagnostics.append({'session': str(sid)[:512], 'reason': str(exc)[:200]})
                        state['last'] = sid
                    done = state['last'] == state['upper'] or not page
                    budget.catalog_status = 'complete' if done else 'partial'
                    if done:
                        state.update(last='', upper=None)
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                diagnostics.append({'reason': str(exc)[:200], 'scope': 'catalog'})
                budget.catalog_status = 'partial'
            # Persist sweep progress even when a later bounded result query runs
            # out of work. Native catalog and derived DB are never one writer.
            with db:
                db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)', (key, _dump(state)))
            # Persist the parent expansion frontier. Indexed pages handle a
            # child observed before its parent without rebuilding the lineage.
            with db:
                db.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)', (key, run['session'], ''))
                for _ in range(min(4, capacity)):
                    parent = _one(db, budget, 'SELECT id,child FROM members WHERE run=? AND id>? ORDER BY id LIMIT 1',
                                  (key, state.get('parent', '')))
                    if parent is None:
                        state['parent'] = ''
                        parent = _one(db, budget, 'SELECT id,child FROM members WHERE run=? ORDER BY id LIMIT 1', (key,))
                    children = _rows(db, budget, 'SELECT DISTINCT id FROM discovery WHERE parent=? AND id>? ORDER BY id LIMIT ?',
                                     (parent[0], parent[1], capacity))
                    for child in children:
                        evidence = _one(db, budget, 'SELECT substr(meta,1,32769) FROM discovery WHERE id=? LIMIT 1', (child[0],))
                        meta = _load(evidence[0])
                        try:
                            born = _instant(meta['started_at'])
                            start = _instant(run['at']) if run.get('at') else None
                        except ValueError:
                            continue
                        if (child[0] not in known and meta.get('thread_source') != 'guardian_review'
                                and start is not None and born < start):
                            continue
                        if run.get('discovery_cutoff') is not None and born >= run['discovery_cutoff']:
                            continue
                        db.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)', (key, child[0], ''))
                    cursor = children[-1][0] if len(children) == capacity else ''
                    db.execute('UPDATE members SET child=? WHERE run=? AND id=?', (cursor, key, parent[0]))
                    state['parent'] = parent[0]
            members = _rows(db, budget, 'SELECT id FROM members WHERE run=? AND id>? ORDER BY id LIMIT ?',
                            (key, state['service'], capacity))
            if not members and state['service']:
                state['service'] = ''
                members = _rows(db, budget, 'SELECT id FROM members WHERE run=? ORDER BY id LIMIT ?', (key, capacity))
            ids = [row[0] for row in members]
            if members:
                state['service'] = members[-1][0]
            if run['session'] not in ids:
                ids.append(run['session'])
            for sid in ids:
                rows = _rows(db, budget, 'SELECT substr(meta,1,32769) FROM discovery WHERE id=? LIMIT ?', (sid, capacity + 1))
                if len(rows) > capacity:
                    diagnostics.append({'session': sid, 'reason': 'segment_detail_limit'})
                    # Never prove replacement from a silently shortened set.
                    continue
                selected.extend(_load(row[0]) for row in rows)
            with db:
                db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)', (key, _dump(state)))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        diagnostics.append({'reason': str(exc)[:200], 'scope': 'discovery'})
    # Orphan discovery has no bounded portable manifest; never imply completeness.
    diagnostics.append({'reason': 'native_catalog_does_not_cover_orphan_or_legacy_segments', 'scope': 'inventory'})
    return selected, diagnostics


def merge_report(run: dict[str, Any], result: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Replace serviced contributions atomically; retain other pages' subtotals."""
    key = _hash(['run', run.get('run'), run['session'], run.get('usage'), run.get('usage_status'),
                 result['ownership_interval']])
    try:
        with _database(budget) as db:
            row = _one(db, budget, 'SELECT substr(value,1,32769) FROM totals WHERE run=?', (key,))
            total = _load(row[0]) if row else {'tokens': {}, 'native_tokens': {}, 'host_approval_tokens': {},
                'measured_sessions': 0, 'unmeasured_sessions': 0, 'partial_sessions': 0, 'sessions': 0,
                'token_values': 0, 'native_values': 0, 'approval_values': 0, 'detail_cursor': ''}
            with db:
                for session in result['sessions']:
                    sid = session.get('session')
                    if not sid:
                        continue
                    old = _one(db, budget, 'SELECT substr(value,1,32769) FROM results WHERE run=? AND id=?', (key, sid))
                    previous = _load(old[0]) if old else None
                    if previous and previous.get('attribution_unknown') and session.get('recovery_status'):
                        session.clear()
                        session.update(previous)
                    for sign, value in ((-1, previous), (1, session)):
                        if value is None:
                            continue
                        approval = value.get('role') == 'host_approval_review'
                        total['sessions'] += sign
                        if not approval:
                            total['measured_sessions' if value.get('usage') is not None else 'unmeasured_sessions'] += sign
                            total['partial_sessions'] += sign * (value.get('status') == 'partial')
                        for field, source, counter in (
                                ('host_approval_tokens' if approval else 'tokens', 'usage',
                                 'approval_values' if approval else 'token_values'),
                                ('native_tokens', 'native_usage', 'native_values')):
                            if field == 'native_tokens' and approval:
                                continue
                            vector = value.get(source)
                            if vector is not None:
                                total[counter] += sign
                                for category in meter.USAGE_KEYS:
                                    total[field][category] = total[field].get(category, 0) + sign * vector[category]
                    db.execute('INSERT OR REPLACE INTO results VALUES(?,?,?)', (key, sid, _dump(session)))
                db.execute('INSERT OR REPLACE INTO totals VALUES(?,?)', (key, _dump(total)))
            capacity = min(128, max(1, budget.max_rows - budget.rows - 4))
            page = _rows(db, budget, 'SELECT id,substr(value,1,32769) FROM results WHERE run=? AND id>? ORDER BY id LIMIT ?',
                         (key, total['detail_cursor'], capacity))
            if not page and total['detail_cursor']:
                page = _rows(db, budget, 'SELECT id,substr(value,1,32769) FROM results WHERE run=? ORDER BY id LIMIT ?', (key, capacity))
            total['detail_cursor'] = page[-1][0] if page else ''
            with db:
                db.execute('UPDATE totals SET value=? WHERE run=?', (_dump(total), key))
            current = {s.get('session') for s in result['sessions']}
            for sid, encoded in page:
                if sid not in current:
                    session = _load(encoded)
                    # A cached contribution is conserved, never a fresh phase boundary.
                    session['status'] = 'partial' if session['status'] == 'partial' else 'recorded; native counter unavailable'
                    result['sessions'].append(session)
            for name, counter in (('tokens', 'token_values'), ('native_tokens', 'native_values'),
                                  ('host_approval_tokens', 'approval_values')):
                result[name] = total[name] if total[counter] else None
            coverage = result['token_coverage']
            coverage.update({name: total[name] for name in ('measured_sessions', 'unmeasured_sessions', 'partial_sessions')})
            coverage['unmeasured_sessions'] += sum(s.get('session') is None for s in result['sessions'])
            coverage['detail_status'] = 'complete' if len(current - {None}) == total['sessions'] else 'partial'
            if coverage['detail_status'] != 'complete':
                budget.replay_status = 'partial'
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        # A page subtotal must never masquerade as the entire conserved run.
        result['tokens'] = None
        result['native_tokens'] = None
        result['host_approval_tokens'] = None
        result['token_coverage']['aggregate_status'] = 'partial'
        result['token_coverage']['aggregate_error'] = str(exc)[:200]
        budget.replay_status = 'partial'
    return result


def known_paths(session: str, *, path: str | Path | None = None,
                workspace: str | Path | None = None) -> tuple[list[str], list[str]]:
    """Bounded exact native-counter lookup; returned files are identity-checked.

    Existing cached physical segments remain candidates when a catalog pointer
    advances. Missing catalog/orphan coverage is diagnostic, never a glob scan.
    """
    paths, errors = [], []
    with action_budget(workspace) as budget:
        try:
            with _database(budget) as db:
                if path:
                    budget.take('entries')
                    with db:
                        _remember(db, session, str(Path(path).absolute()), budget)
                try:
                    with _catalog(budget) as catalog:
                        row = _one(catalog, budget, 'SELECT substr(rollout_path,1,4097) FROM threads WHERE id=?', (session,))
                        if row:
                            budget.take('entries')
                            with db:
                                _remember(db, session, row[0], budget)
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                    errors.append(str(exc)[:200])
                limit = min(32, budget.max_entries - budget.entries)
                rows = _rows(db, budget, 'SELECT path FROM discovery WHERE id=? ORDER BY path LIMIT ?', (session, limit + 1))
                if len(rows) > limit:
                    raise Exhausted('native_segment_limit')
                for row in rows:
                    budget.take('entries')
                    paths.append(row[0])
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            errors.append(str(exc)[:200])
    return paths, errors


def read_metadata(path: str | Path, session: str, *, workspace: str | Path | None = None) -> dict[str, Any]:
    """Read verified native lineage within the current shared action allowance."""
    with action_budget(workspace) as budget:
        return _metadata(Path(path), session, budget)
