"""Real complete-drain usage grid. No inferred timings or synthetic pass flags.

Run with isolated native homes established by the caller. Fixtures and output
belong outside the source tree. Oracle code reads complete fixtures independently
of the incremental producer and includes exact owned attribution.
"""
from __future__ import annotations
import argparse
import itertools
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from taskplane.native_session_meter import IncrementalUsageReader

HOSTS = ('Codex', 'Claude')
UNRELATED = (0, 1000, 10000)
OWNED = (1, 16, 64)
SIZES = (65536, 1048576, 16777216)
MODES = ('cold', 'unchanged warm', 'appended warm')
KEYS = ('input_tokens', 'cached_input_tokens', 'uncached_input_tokens',
        'output_tokens', 'reasoning_tokens', 'total_tokens')


def encoded(row):
    return (json.dumps(row, separators=(',', ':')) + '\n').encode()


def event(host, sid, number):
    stamp = f'2026-01-01T00:{number // 60:02}:{number % 60:02}Z'
    if host == 'Codex':
        def count(n):
            return {'input_tokens': 10*n, 'cached_input_tokens': 4*n,
                    'output_tokens': 3*n, 'total_tokens': 13*n}
        return {'type': 'token_usage_record', 'timestamp': stamp, 'ordinal': number,
                'payload': {'thread_id': sid, 'response_id': f'{sid}-{number}',
                            'usage': count(1), 'thread_token_usage': count(number)}}
    return {'type': 'assistant', 'sessionId': 'root',
            **({'agentId': sid} if sid != 'root' else {}), 'timestamp': stamp,
            'message': {'id': f'{sid}-{number}', 'model': 'fixture',
                        'usage': {'input_tokens': 6, 'cache_read_input_tokens': 4,
                                  'cache_creation_input_tokens': 0, 'output_tokens': 3}}}


def fixture(directory, host, unrelated, owned, size):
    directory.mkdir(parents=True)
    ids = ['root'] + [f'owned-{i}' for i in range(1, owned)]
    for i, sid in enumerate(ids):
        if host == 'Codex':
            metadata = {'type': 'session_meta', 'timestamp': '2026-01-01T00:00:00Z',
                'payload': {'id': sid, 'timestamp': '2026-01-01T00:00:00Z',
                            **({'parent_thread_id': 'root'} if i else {})}}
        else:
            metadata = {'type': 'user', 'sessionId': 'root',
                        **({'agentId': sid} if i else {}), 'timestamp': '2026-01-01T00:00:00Z'}
        initial = encoded(metadata) + b''.join(encoded(event(host, sid, n)) for n in range(1, 65))
        path = directory / f'{sid}.jsonl'
        with path.open('wb') as stream:
            stream.write(initial)
            remaining = size - len(initial)
            # Actual allocated JSON records, never sparse seek/truncate padding.
            overhead = len(encoded({'type': 'padding', 'padding': ''}))
            while remaining:
                length = min(65536, remaining)
                if 0 < remaining - length < overhead:
                    length -= overhead
                if length < overhead:
                    raise ValueError('fixture size cannot hold a valid JSON padding row')
                data = encoded({'type': 'padding', 'padding': 'x' * (length - overhead)})
                stream.write(data); remaining -= len(data)
        assert path.stat().st_size == size
    for n in range(unrelated):
        sid = f'unrelated-{n}'
        row = ({'type': 'session_meta', 'payload': {'id': sid, 'timestamp': '2026-01-01T00:00:00Z'}}
               if host == 'Codex' else {'type': 'user', 'sessionId': sid, 'timestamp': '2026-01-01T00:00:00Z'})
        (directory / f'{sid}.jsonl').write_bytes(encoded(row))
    return ids


def oracle(directory, host, ids):
    """Complete independent parser; no incremental-reader state/status is used."""
    attribution = {}
    for sid in ids:
        unique = {}
        with (directory / f'{sid}.jsonl').open() as stream:
            for line in stream:
                row = json.loads(line)
                if host == 'Codex' and row.get('type') == 'token_usage_record':
                    payload = row['payload']
                    if payload['thread_id'] != sid:
                        continue
                    raw = payload['usage']; inp=raw['input_tokens']; cache=raw['cached_input_tokens']; out=raw['output_tokens']
                    unique[payload['response_id']] = (inp, cache, inp-cache, out, 0, inp+out)
                elif host == 'Claude' and row.get('type') == 'assistant':
                    message=row['message']; raw=message['usage']
                    cache=raw['cache_read_input_tokens']; uncached=raw['input_tokens']+raw.get('cache_creation_input_tokens',0); out=raw['output_tokens']
                    unique[message['id']] = (uncached+cache, cache, uncached, out, 0, uncached+cache+out)
        attribution[sid] = {key: sum(value[i] for value in unique.values()) for i,key in enumerate(KEYS)}
    return {key:sum(v[key] for v in attribution.values()) for key in KEYS}, attribution


class IncompleteDrain(RuntimeError):
    def __init__(self, refreshes, reason='drain did not complete'):
        self.refreshes = refreshes
        super().__init__(json.dumps({'error':reason,'refreshes':refreshes}))


def drain(reader, *, limit=20000, observer=None):
    refreshes = []
    for _ in range(limit):
        result = reader.refresh()
        raw = {k:v for k,v in result.items() if k not in ('sessions', 'trust_assumptions', 'coverage')}
        refreshes.append(raw)
        if observer is not None: observer(raw)
        if result['complete']:
            return refreshes
        if 'native_directory_accounting_unavailable' in result.get('discovery_errors', []):
            raise IncompleteDrain(refreshes, 'unsupported_native_directory_api')
        if not raw['exhausted'] and not any(raw[key] for key in ('read_bytes','parsed_records','discovery_entries')):
            raise IncompleteDrain(refreshes, 'immutable_fixture_drain_made_no_progress')
        if len(refreshes) > 1 and raw['resumed_from'] != refreshes[-2]['continuation']:
            raise AssertionError('producer lost its actual continuation')
    raise IncompleteDrain(refreshes)


def peak_rss_bytes():
    # ru_maxrss is a measured process high-water mark (not a per-sample delta).
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)


def samples(directory, *, hosts=HOSTS, unrelated_values=UNRELATED, owned_values=OWNED,
            sizes=SIZES, repetitions=5):
    directory.mkdir(parents=True, exist_ok=True)
    evidence_path = directory / 'refresh-evidence.ndjson'
    def emit(observation):
        # Outside the candidate tree; each measured refresh survives pytest
        # interruption independently of record_property finalization.
        with evidence_path.open('a') as evidence:
            evidence.write(json.dumps(observation)+'\n'); evidence.flush()
    for host, unrelated, owned, size in itertools.product(hosts, unrelated_values, owned_values, sizes):
        base = directory / f'{host}-{unrelated}-{owned}-{size}'
        ids = fixture(base, host, unrelated, owned, size)
        expected, attributed = oracle(base, host, ids)
        for mode in MODES:
            append_directory = directory / f'append-{host}-{unrelated}-{owned}-{size}'
            for repetition in range(repetitions):
                active = base
                if mode == 'appended warm':
                    active = append_directory
                    # Immutable unrelated files can be reused; owned files need
                    # independent real copies because this mode appends data.
                    if repetition == 0:
                        active.mkdir()
                        for path in base.iterdir():
                            if path.stem not in ids:
                                (active / path.name).hardlink_to(path)
                    for sid in ids:
                        shutil.copyfile(base / f'{sid}.jsonl', active / f'{sid}.jsonl')
                coordinate = {'host':host,'unrelated_sessions':unrelated,'owned_sessions':owned,
                    'bytes_per_owned_session':size,'mode':mode,'repetition':repetition}
                def observe(raw, stage='measurement'):
                    emit({'kind':'refresh','coordinate':coordinate,'stage':stage,'refresh':raw})
                reader = IncrementalUsageReader(host, 'root', [active], start=1767225600.0)
                if mode != 'cold':
                    drain(reader, observer=lambda raw: observe(raw, 'setup'))
                oracle_totals, oracle_attribution = expected, attributed
                if mode == 'appended warm':
                    for sid in ids:
                        with (active / f'{sid}.jsonl').open('ab') as stream:
                            stream.write(encoded(event(host, sid, 65)))
                    oracle_totals, oracle_attribution = oracle(active, host, ids)
                began = time.monotonic(); cpu_began = time.process_time()
                failure = None
                try:
                    refreshes = drain(reader, observer=observe)
                except IncompleteDrain as exc:
                    refreshes = exc.refreshes; failure = str(exc)
                elapsed = time.monotonic() - began
                cpu_elapsed = time.process_time() - cpu_began
                last = refreshes[-1]
                sample = {'sample_id': f'{host}/{unrelated}/{owned}/{size}/{mode}/{repetition}',
                    'measurement_class': 'real', 'host': host, 'unrelated_sessions': unrelated,
                    'owned_sessions': owned, 'bytes_per_owned_session': size,
                    'mode': mode, 'repetition': repetition, 'elapsed_seconds': elapsed,
                    'cpu_seconds': cpu_elapsed, 'error': failure,
                    'timing_basis': 'wall clock includes append/flush of raw measurement evidence',
                    'cache_basis': 'reader checkpoint cold or warm; OS page cache uncontrolled',
                    'peak_rss_bytes': peak_rss_bytes(), 'complete': last['complete'],
                    'totals': last['totals'], 'oracle_totals': oracle_totals,
                    'attribution': last['attribution'], 'oracle_attribution': oracle_attribution,
                    'refreshes': refreshes, 'rss_basis': 'measured process high-water mark',
                    'oracle_provenance': 'independent complete fixture JSONL parser'}
                emit({'kind':'sample','sample':sample})
                yield sample
                reader.close()
                if failure: raise IncompleteDrain(refreshes)
            if mode == 'appended warm': shutil.rmtree(append_directory)
        shutil.rmtree(base)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, required=True)
    args=parser.parse_args()
    args.fixtures.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Append each finished drain immediately; an interrupted/failed run keeps
    # its actual completed evidence and must fail the 810-coordinate validator.
    with args.output.open('w') as stream:
        for sample in samples(args.fixtures):
            stream.write(json.dumps(sample)+'\n'); stream.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
