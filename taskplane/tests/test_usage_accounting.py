"""Accounting regressions use cumulative native counters, never billing estimates."""
from copy import deepcopy
import json

import pytest

from taskplane import flow, flow_dashboard, flow_usage, workflow as w
from taskplane import context_views
from taskplane.context import Store


def counts(value):
    return dict(input_tokens=value, cached_input_tokens=0, uncached_input_tokens=value,
                output_tokens=0, reasoning_tokens=0, total_tokens=value)


def unavailable_start_report(tmp_path, monkeypatch, *, historical=False, initial_at=None):
    """Real journal/report fixture with recoverable native counters and stale observations."""
    from taskplane.tests.test_workflow_host import controller
    from taskplane.tests.test_history_bounds import _codex_catalog_fixture
    from taskplane.tests.test_native_session_meter import _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path.resolve()))
    c, host, s = controller(tmp_path)
    s['started_at'] = '2026-09-01T00:00:10Z'
    native = tmp_path.resolve() / 'root.jsonl'
    records = [{'type': 'session_meta', 'payload': {'id': 'root',
                'timestamp': '2026-09-01T00:00:00Z'}}]
    for total, ordinal, second in [(100, 1, 5), (120, 2, 15), (160, 3, 25)]:
        record = _usage_record(total, ordinal, at=f'2026-09-01T00:00:{second:02}Z')
        record['payload']['thread_id'] = 'root'
        records.append(record)
    native.write_text(''.join(json.dumps(row) + '\n' for row in records))
    _codex_catalog_fixture(tmp_path.resolve(), [('root', native)])
    run = {'kind': 'start', 'run': s['run'], 'session': 'root', 'host': 'codex',
           'at': s['started_at'], 'usage': None, 'usage_status': 'unavailable',
           'transcript_path': str(native)}
    flow.append(c.workspace, run)
    flow.append(c.workspace, flow.usage_point(s, {'sessions': []}, observed_at=initial_at or run['at']))
    s['index'] = 1
    s['revision'] = 1
    flow.append(c.workspace, flow.usage_point(s, measured(800, status='partial'),
        observed_at='2026-09-01T00:00:20Z', previous_revision=0))
    if historical:
        flow.append(c.workspace, {**run, 'run': 'successor', 'at': '2026-09-01T00:00:30Z'})
    s = {**c.report(s['run']), **s, 'phase': w.current(s)['phase']}
    monkeypatch.setattr(c, 'report', lambda run_id=None: deepcopy(
        {**s, 'run': 'successor'} if historical and run_id is None else s))
    return c, host, s, run, native


def append_root_counter(native, total, second, ordinal=4):
    from taskplane.tests.test_native_session_meter import _usage_record
    record = _usage_record(total, ordinal, at=f'2026-09-01T00:00:{second:02}Z')
    record['payload']['thread_id'] = 'root'
    with native.open('a') as stream:
        stream.write(json.dumps(record) + '\n')


def test_unavailable_start_native_proof_recovers_actual_report_and_dashboard(tmp_path, monkeypatch):
    from taskplane import native_session_meter as meter
    c, host, s, run, native = unavailable_start_report(tmp_path, monkeypatch)
    original = (c.workspace / flow.JOURNAL).read_bytes()
    authority = host.store.read_bytes()
    proof = meter.read_owned_interval([native], 'root', start=flow_usage._time(run['at']),
                                      workspace=c.workspace)
    assert proof['status'] == 'measured' and proof['usage'] == counts(60)
    for _ in range(2):
        report = flow.report(c.workspace, s['run'], governor=c)
        assert report['tokens'] == counts(60), report['sessions']
        visit = w.current(s)['id']
        assert report['phase_usage']['visits'][visit]['tokens'] == counts(40)
        assert report['phase_usage']['visits'][visit]['status'] == 'partial'
        conserved(report['phase_usage'])
        html = flow_dashboard.render(str(c.workspace), report)
        assert 'Current visit tokens</span><b>40</b>' in html
    assert (c.workspace / flow.JOURNAL).read_bytes() == original
    assert host.store.read_bytes() == authority


@pytest.mark.parametrize('initial_at', [None, '2026-09-01T00:00:08.500000Z'])
def test_recovered_historical_visit_stops_at_successor_with_early_initial_anchor(tmp_path, monkeypatch, initial_at):
    c, _, s, run, native = unavailable_start_report(tmp_path, monkeypatch, historical=True, initial_at=initial_at)
    append_root_counter(native, 220, 35)
    for _ in range(2):
        report = flow.report(c.workspace, s['run'], governor=c)
        assert report['historical']
        assert report['tokens'] == counts(60)
        assert report['phase_usage']['visits'][w.current(s)['id']]['tokens'] == counts(40)
        assert report['sessions'][0]['attribution_recovery']['end_exclusive'] == flow_usage._time('2026-09-01T00:00:30Z')
        conserved(report['phase_usage'])


def test_recovered_visit_append_and_finish_keep_follow_up_separate(tmp_path, monkeypatch):
    c, _, s, run, native = unavailable_start_report(tmp_path, monkeypatch)
    s['finished'] = True
    s['revision'] = 2
    flow.append(c.workspace, flow.usage_point(s, measured(60),
        observed_at='2026-09-01T00:00:27Z', previous_revision=1))
    append_root_counter(native, 180, 35)
    for total in (180, 190, 190):
        if total == 190 and native.read_text().count('00:00:40Z') == 0:
            append_root_counter(native, total, 40, ordinal=5)
        report = flow.report(c.workspace, s['run'], governor=c)
        usage = report['phase_usage']
        assert report['tokens'] == counts(total - 100)
        assert usage['visits'][w.current(s)['id']]['tokens'] == counts(40)
        assert usage['non_phase']['follow_up']['tokens'] == counts(total - 160)
        conserved(usage)


@pytest.mark.parametrize('complete_total', [120, 1300])
def test_recovered_visit_replaces_overlap_but_preserves_genuine_reset_refusal(tmp_path, monkeypatch, complete_total):
    c, _, s, _, _ = unavailable_start_report(tmp_path, monkeypatch)
    s['revision'] = 2
    flow.append(c.workspace, flow.usage_point(s, measured(complete_total - 100),
        observed_at='2026-09-01T00:00:22Z', previous_revision=1))
    report = flow.report(c.workspace, s['run'], governor=c)
    usage = report['phase_usage']
    if complete_total == 120:
        assert usage['visits'][w.current(s)['id']]['tokens'] == counts(40)
        assert any(i['reason'] == 'replaced_by_owned_interval' for i in usage['intervals'])
    else:
        assert not usage['visits']
        assert usage['unallocated'] == counts(60)
        assert any(i['reason'] == 'counter_reset_or_total_conflict' for i in usage['intervals'])
    conserved(usage)


@pytest.mark.parametrize('defect', ['missing_entry', 'missing_transition', 'foreign', 'before_run',
                                   'out_of_order', 'finished_without_exit', 'wrong_phase'])
def test_unproven_visit_boundaries_leave_recovered_run_unresolved(tmp_path, monkeypatch, defect):
    c, _, s, run, _ = unavailable_start_report(tmp_path, monkeypatch)
    points = [json.loads(line) for line in (c.workspace / flow.JOURNAL).read_text().splitlines()
              if json.loads(line)['kind'] == 'usage_boundary']
    if defect == 'missing_entry':
        points = points[1:]
    elif defect == 'missing_transition':
        points[1]['previous_revision'] = -1
    elif defect == 'foreign':
        points[1]['run'] = 'foreign'
    elif defect == 'before_run':
        points[1]['observed_at'] = '2026-09-01T00:00:09Z'
    elif defect == 'out_of_order':
        points.reverse()
    elif defect == 'finished_without_exit':
        s['finished'] = True
    else:
        points[1]['phase'] = 'build'
    measurement = flow_usage.reconcile(run, [], workspace=c.workspace, phase_recovery=True)
    assert measurement['tokens'] == counts(60)
    result = flow.phase_usage(points, s, measurement, run=run)
    assert w.current(s)['id'] not in result['visits']
    assert result['unallocated'] == counts(60)
    conserved(result)


@pytest.mark.parametrize('defect', ['partial', 'foreign_window', 'reset', 'missing_component', 'source_unavailable'])
def test_unavailable_start_rejects_invalid_current_interval_proof(tmp_path, monkeypatch, defect):
    from taskplane import native_session_meter as meter
    c, _, _, run, native = unavailable_start_report(tmp_path, monkeypatch)
    proof = meter.read_owned_interval([native], 'root', start=flow_usage._time(run['at']), workspace=c.workspace)
    if defect == 'partial':
        proof.update(replay_status='partial', status='partial', errors=['reduction_pending'])
    elif defect == 'foreign_window':
        proof['interval']['start'] = 0
    elif defect == 'reset':
        proof['attribution_unknown'] = True
    elif defect == 'missing_component':
        del proof['usage']['cached_input_tokens']
    else:
        proof['source_unavailable'] = True
    monkeypatch.setattr(meter, 'read_owned_interval', lambda *_args, **_kwargs: deepcopy(proof))
    result = flow_usage.reconcile(run, [], workspace=c.workspace)
    assert result['tokens'] is None
    assert result['sessions'][0]['usage'] is None
    assert 'attribution_recovery' not in result['sessions'][0]


def test_recovery_label_without_current_native_proof_cannot_bypass_reconciler(monkeypatch):
    forged = measured(60)['sessions']
    forged[0]['attribution_recovery'] = {'basis': 'recovered owned native interval'}
    monkeypatch.setattr(flow_usage, '_codex_sessions', lambda *_: (deepcopy(forged), 0))
    result = flow_usage._reconcile({'run': 'run', 'session': 'root', 'usage': None,
                                   'usage_status': 'unavailable', 'at': '2026-09-01T00:00:10Z'}, [])
    assert result['tokens'] is None
    assert result['sessions'][0]['attribution_unknown']
    assert 'attribution_recovery' not in result['sessions'][0]


def test_recovered_first_visit_clips_early_initial_anchor_to_run_start(tmp_path, monkeypatch):
    c, _, s, run, _ = unavailable_start_report(tmp_path, monkeypatch, initial_at='2026-09-01T00:00:08Z')
    first = next(json.loads(line) for line in (c.workspace / flow.JOURNAL).read_text().splitlines()
                 if json.loads(line)['kind'] == 'usage_boundary')
    s['index'], s['revision'] = 0, 0
    measurement = flow_usage.reconcile(run, [], workspace=c.workspace, phase_recovery=True)
    result = flow.phase_usage([first], s, measurement, run=run)
    assert result['visits'][w.current(s)['id']]['tokens'] == counts(60)
    recovered = next(i for i in result['intervals'] if i.get('attribution_recovery'))
    assert recovered['attribution_recovery']['start'] == flow_usage._time(run['at'])
    conserved(result)


@pytest.mark.parametrize('defect', ['foreign', 'reset', 'missing', 'malformed'])
def test_recovered_report_does_not_restore_invalid_or_missing_native_source(tmp_path, monkeypatch, defect):
    c, _, s, _, native = unavailable_start_report(tmp_path, monkeypatch)
    assert flow.report(c.workspace, s['run'], governor=c)['tokens'] == counts(60)
    if defect == 'foreign':
        native.write_text(native.read_text().replace('root', 'foreign'))
    elif defect == 'reset':
        append_root_counter(native, 50, 35)
    elif defect == 'missing':
        native.unlink()
    else:
        with native.open('a') as stream:
            stream.write('{"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":-1}}}}\n')
    report = flow.report(c.workspace, s['run'], governor=c)
    assert report['tokens'] is None
    assert not report['phase_usage']['visits']
    root = next(item for item in report['sessions'] if item.get('session') == 'root')
    assert root['attribution_unknown'] and 'attribution_recovery' not in root


@pytest.mark.parametrize('defect', ['host', 'counter'])
def test_recovered_run_rejects_conflicting_complete_closing_boundary(tmp_path, monkeypatch, defect):
    c, _, _, run, _ = unavailable_start_report(tmp_path, monkeypatch)
    closing = {**run, 'run': 'next', 'at': '2026-09-01T00:00:30Z',
               'usage': counts(170 if defect == 'counter' else 160), 'usage_status': 'observed'}
    if defect == 'host':
        closing['host'] = 'claude'
    report = flow_usage.reconcile(run, [closing], workspace=c.workspace)
    assert report['tokens'] is None
    assert report['sessions'][0]['attribution_unknown']


def test_recovered_visit_does_not_add_a_partially_overlapping_observed_interval(tmp_path, monkeypatch):
    c, _, s, run, _ = unavailable_start_report(tmp_path, monkeypatch)
    measurement = flow_usage.reconcile(run, [], workspace=c.workspace, phase_recovery=True)
    points = [json.loads(line) for line in (c.workspace / flow.JOURNAL).read_text().splitlines()
              if json.loads(line)['kind'] == 'usage_boundary']
    recovery = flow_usage.recover_selected_visit(points, s, measurement, run)
    assert recovery is not None
    prior = deepcopy(s)
    prior['index'], prior['revision'] = 0, 0
    a = flow.usage_point(prior, measured(0), observed_at='2026-09-01T00:00:15Z')
    b = flow.usage_point(s, measured(60), observed_at='2026-09-01T00:00:25Z', previous_revision=99)
    endpoint = deepcopy(b)
    endpoint['previous_revision'] = b['revision']
    result = flow_usage.phase_accounting([a, b], endpoint, s, measurement, recovered_visit=recovery)
    assert not any(i.get('attribution_recovery') for i in result['intervals'])
    assert result['unallocated'] == counts(60)
    assert any('inseparable' in gap for gap in result['gaps'])
    conserved(result)


def test_recovered_report_cold_queries_resume_stable_windows_under_shared_budget(tmp_path, monkeypatch):
    import sqlite3
    from taskplane import codex_usage_cache as cache
    from taskplane.tests.test_native_session_meter import _usage_record
    c, _, s, _, native = unavailable_start_report(tmp_path, monkeypatch)
    with native.open('a') as stream:
        for number in range(1, 601):
            row = _usage_record(160 + number, 3 + number, at=f'2026-09-01T00:00:26.{number:03}Z')
            row['payload']['thread_id'] = 'root'
            stream.write(json.dumps(row) + '\n')
    counts_seen, cursors, reports = [], [], []
    for _ in range(12):
        budget = cache.Budget()
        with cache.action_budget(c.workspace, budget=budget):
            report = flow.report(c.workspace, s['run'], governor=c)
        reports.append(report)
        assert budget.source_bytes <= budget.max_bytes and budget.records <= budget.max_records
        assert budget.rows <= budget.max_rows and budget.vm_steps <= budget.max_vm_steps
        assert report['token_coverage']['action_work']['rows'] == budget.rows
        with sqlite3.connect(cache.cache_path(c.workspace)) as db:
            counts_seen.append(db.execute('SELECT count(*) FROM queries').fetchone()[0])
            cursors.append(tuple(db.execute('SELECT query,cursor FROM query_sources ORDER BY query').fetchall()))
        visit = report['phase_usage']['visits'].get(w.current(s)['id'])
        if visit is not None and visit['tokens'] == counts(640):
            break
    assert reports[0]['tokens'] is None
    assert len(reports) > 2 and len(set(cursors)) > 2
    assert max(counts_seen) == 2  # One run and one selected visit, not a new wall-clock window per report.
    assert reports[-1]['tokens'] == counts(660)
    assert reports[-1]['phase_usage']['visits'][w.current(s)['id']]['tokens'] == counts(640)
    conserved(reports[-1]['phase_usage'])
    again = flow.report(c.workspace, s['run'], governor=c)
    assert again['tokens'] == reports[-1]['tokens']
    assert again['phase_usage']['visits'] == reports[-1]['phase_usage']['visits']


def test_recovered_lifecycle_measurement_keeps_only_small_provenance(tmp_path, monkeypatch):
    c, _, s, run, _ = unavailable_start_report(tmp_path, monkeypatch)
    measurement = flow.reconcile_usage(run, [], s, workspace=c.workspace)
    assert measurement['tokens'] == counts(60)
    assert '_root_recovery' not in measurement
    root = measurement['sessions'][0]
    assert root['attribution_recovery']['start'] == flow_usage._time(run['at'])
    assert not any(key.startswith('_') for key in root)
    point = flow.usage_point(s, measurement)
    assert point['sessions'][0] == root


def test_recovered_subtotal_does_not_hide_a_missing_child(tmp_path, monkeypatch):
    c, _, s, run, _ = unavailable_start_report(tmp_path, monkeypatch)
    flow.append(c.workspace, {'kind': 'attach', 'run': run['run'],
        'artifacts': {'reviews': 'captured.json'},
        'captured': {'reviews': [{'agent': 'missing-child'}]}})
    report = flow.report(c.workspace, s['run'], governor=c)
    assert report['tokens'] == counts(60)
    assert report['token_coverage']['unmeasured_sessions'] == 1
    assert report['phase_usage']['visits'][w.current(s)['id']]['tokens'] == counts(40)
    assert report['phase_usage']['visits'][w.current(s)['id']]['status'] == 'partial'
    assert report['phase_usage']['status'] == 'partial'
    conserved(report['phase_usage'])


@pytest.mark.parametrize('message_bytes', [100, 2 * 1024 * 1024 + 100, 12 * 1024 * 1024])
def test_compaction_size_only_preserves_measured_interval(tmp_path, monkeypatch, message_bytes):
    from taskplane import native_session_meter as meter
    from taskplane import codex_usage_cache as cache
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path.resolve()))
    before = _usage_record(100, 1, at='2026-09-01T00:00:05Z')
    after = _usage_record(145, 2)
    compacted = {'type': 'compacted', 'payload': {'message': 'x' * message_bytes,
                 'latest_token_usage_record': after, 'replacement_history': [before, after]}}
    path = _usage_file(tmp_path.resolve() / 'child.jsonl', [before, compacted, after])
    results = []
    for _ in range(8):
        budget = cache.Budget()
        result = meter.read_owned_interval([path], 'child', start=cache._instant('2026-09-01T00:00:10Z'),
                                           workspace=tmp_path.resolve(), budget=budget)
        assert budget.source_bytes <= cache.MAX_BYTES
        assert budget.records <= cache.MAX_RECORDS
        results.append(result)
    assert results[-1]['status'] == 'measured', results[-1]
    assert results[-1]['usage'] == counts(45)
    assert results[-1]['native_usage'] == counts(145)
    assert results[-2]['usage'] == results[-1]['usage']
    if message_bytes > cache.MAX_BYTES:
        assert results[0]['status'] == 'partial'


def _compaction_fixture(tmp_path, monkeypatch, *, terminated=True):
    from taskplane import native_session_meter as meter, codex_usage_cache as cache
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path.resolve()))
    monkeypatch.setattr(meter, 'MAX_RECORD_BYTES', 512)
    monkeypatch.setattr(meter, 'MAX_METADATA_BYTES', 256)
    before = _usage_record(100, 1, at='2026-09-01T00:00:05Z')
    after = _usage_record(145, 2)
    path = _usage_file(tmp_path.resolve() / 'native.jsonl', [before])
    raw = json.dumps({'payload': {'message': 'é\\"' * 6000, 'copied': after},
                      'type': 'compacted'}, ensure_ascii=False).encode()
    with path.open('ab') as stream:
        stream.write(raw + (b'\n' if terminated else b''))
        if terminated:
            stream.write(json.dumps(after).encode() + b'\n')

    def read():
        budget = cache.Budget(max_bytes=4096)
        result = meter.read_owned_interval([path], 'child', start=cache._instant('2026-09-01T00:00:10Z'),
                                           workspace=tmp_path.resolve(), budget=budget)
        assert budget.source_bytes <= 4096
        return result

    return path, after, read


def test_compaction_restart_cursor_state_and_torn_append(tmp_path, monkeypatch):
    import sqlite3
    from taskplane import codex_usage_cache as cache
    path, after, read = _compaction_fixture(tmp_path, monkeypatch, terminated=False)
    offsets = []
    for _ in range(30):
        result = read()
        with sqlite3.connect(cache.cache_path(tmp_path.resolve())) as db:
            encoded = db.execute('SELECT meta FROM sources').fetchone()[0]
        assert len(encoded) < 32768
        meta = json.loads(encoded)
        offsets.append(meta['offset'])
        assert result['status'] == 'partial' and 'replay_pending' in result['errors']
        assert result['usage'] is None
        assert 'message' not in json.dumps(meta.get('compaction', {}))
        if meta['offset'] == path.stat().st_size:
            break
    assert len(offsets) > 1 and offsets == sorted(set(offsets))
    # Complete syntax without newline is still an uncommitted record.
    assert not meta['complete'] and meta['compaction']['state']['root'] == 'done'
    assert read()['status'] == 'partial'
    with path.open('ab') as stream:
        stream.write(b'\n' + json.dumps(after).encode() + b'\n')
    result = read()
    assert result['status'] == 'measured' and result['usage'] == counts(45)
    assert read()['usage'] == counts(45)


@pytest.mark.parametrize('change', ['truncate', 'replace', 'anchor', 'missing', 'progress'])
def test_compaction_interrupted_source_changes_stay_conservative(tmp_path, monkeypatch, change):
    import sqlite3
    from taskplane import codex_usage_cache as cache
    path, _, read = _compaction_fixture(tmp_path, monkeypatch)
    assert read()['status'] == 'partial'
    if change == 'truncate':
        with path.open('r+b') as stream:
            stream.truncate(1000)
    elif change == 'replace':
        replacement = path.with_suffix('.replacement')
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
    elif change == 'anchor':
        with sqlite3.connect(cache.cache_path(tmp_path.resolve())) as db:
            offset = json.loads(db.execute('SELECT meta FROM sources').fetchone()[0])['offset']
        with path.open('r+b') as stream:
            stream.seek(offset - 10)
            stream.write(b'X')
    elif change == 'missing':
        path.unlink()
    else:
        with sqlite3.connect(cache.cache_path(tmp_path.resolve())) as db:
            meta = json.loads(db.execute('SELECT meta FROM sources').fetchone()[0])
            meta['compaction']['offset'] -= 1
            db.execute('UPDATE sources SET meta=?', (json.dumps(meta),))
    for _ in range(30):
        result = read()
    assert result['status'] != 'measured' and result['usage'] is None
    assert result['errors']
    if change in ('truncate', 'replace', 'anchor'):
        assert 'source_generation_changed' in result['errors']
    if change == 'progress':
        assert result['errors'] == ['invalid_compaction_state']


def test_compaction_schema_rebuild_preserves_poisoned_old_cache(tmp_path, monkeypatch):
    import hashlib
    import sqlite3
    from taskplane import codex_usage_cache as cache
    _, _, read = _compaction_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(cache, 'SCHEMA', 1)
    read()
    old = cache.cache_path(tmp_path.resolve())
    # Existing v1 caches contain this sticky diagnosis; migration must replay,
    # rather than editing it or trusting its observation/reducer cursors.
    with sqlite3.connect(old) as db:
        meta = json.loads(db.execute('SELECT meta FROM sources').fetchone()[0])
        meta['errors'] = ['oversized_record']
        meta.pop('compaction')
        meta['discard'] = True
        db.execute('UPDATE sources SET meta=?', (json.dumps(meta),))
    fingerprint = hashlib.sha256(old.read_bytes()).hexdigest()
    monkeypatch.setattr(cache, 'SCHEMA', 2)
    assert cache.cache_path(tmp_path.resolve()) != old
    assert read()['status'] == 'partial'
    for _ in range(30):
        result = read()
    assert result['status'] == 'measured' and result['usage'] == counts(45)
    assert hashlib.sha256(old.read_bytes()).hexdigest() == fingerprint


@pytest.mark.parametrize('suffix,error', [
    (b',"x":[1,]}}\n', 'invalid_compaction_record'),
    (b'},"type":"event_msg"}\n', 'ambiguous_compaction_record'),
    (b'},"payload":{}}\n', 'ambiguous_compaction_record'),
    (b'}} trailing\n', 'invalid_compaction_record'),
    (b',"x":"\xff"}}\n', 'invalid_compaction_record'),
])
def test_compaction_malformed_after_large_payload_is_not_hidden(tmp_path, monkeypatch, suffix, error):
    from taskplane import native_session_meter as meter, codex_usage_cache as cache
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path.resolve()))
    monkeypatch.setattr(meter, 'MAX_RECORD_BYTES', 512)
    path = _usage_file(tmp_path.resolve() / 'bad.jsonl', [
        _usage_record(100, 1, at='2026-09-01T00:00:05Z')])
    with path.open('ab') as stream:
        stream.write(b'{"type":"compacted","payload":{"message":"' + b'x' * 10000 + b'"' + suffix)
        stream.write(json.dumps(_usage_record(145, 2)).encode() + b'\n')
    for _ in range(3):
        result = meter.read_owned_interval([path], 'child', start=cache._instant('2026-09-01T00:00:10Z'),
                                           workspace=tmp_path.resolve())
    assert result['status'] == 'partial' and result['usage'] is None
    assert result['native_usage'] == counts(145)
    assert error in result['errors']


@pytest.mark.parametrize('kind', ['token_usage_record', 'event_msg', 'future_record'])
def test_oversized_usage_and_unknown_types_remain_explicit(tmp_path, monkeypatch, kind):
    from taskplane import native_session_meter as meter, codex_usage_cache as cache
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    monkeypatch.setenv('CODEX_HOME', str(tmp_path.resolve()))
    monkeypatch.setattr(meter, 'MAX_RECORD_BYTES', 512)
    record = _usage_record(1000, 3)
    record['type'] = kind
    record['payload']['message'] = 'x' * 10000
    path = _usage_file(tmp_path.resolve() / 'usage.jsonl', [
        _usage_record(100, 1, at='2026-09-01T00:00:05Z'), record, _usage_record(145, 2)])
    for _ in range(3):
        result = meter.read_owned_interval([path], 'child', start=cache._instant('2026-09-01T00:00:10Z'),
                                           workspace=tmp_path.resolve())
    assert result['status'] == 'partial' and result['usage'] is None
    assert 'oversized_record' in result['errors']


def test_compaction_record_allowance_is_charged_once(tmp_path, monkeypatch):
    from taskplane import native_session_meter as meter, codex_usage_cache as cache
    path, _, _ = _compaction_fixture(tmp_path, monkeypatch)
    work = []
    for _ in range(30):
        budget = cache.Budget(max_bytes=4096, max_records=1)
        result = meter.read_owned_interval([path], 'child', start=cache._instant('2026-09-01T00:00:10Z'),
                                           workspace=tmp_path.resolve(), budget=budget)
        work.append(budget.records)
        assert budget.records <= 1
        if result['status'] == 'measured':
            break
    assert result['usage'] == counts(45)
    assert sum(work) == 4  # metadata, before, one compaction, after


def measured(value, *, status='measured'):
    return {'sessions': [{'session': 'root', 'agent': 'orchestrator', 'role': 'orchestrator', 'status': status,
                         'native_usage': counts(100 + value), 'usage': counts(value)}],
            'tokens': counts(value), 'token_coverage': {}}


def state():
    return w.new_state('/fixture', 'root', 'usage-run', {'criteria': ['A'], 'paths': {p: [] for p in w.PHASES}})


def conserved(result):
    accounting = result['accounting']
    assert accounting['reconciled']
    for key, value in accounting['run'].items():
        assert value == sum((accounting[b] or {}).get(key, 0) for b in ('phase', 'non_phase', 'unresolved'))
    ledger = [i['tokens'] for i in result['intervals'] if i['tokens'] is not None]
    assert sum(v['total_tokens'] for v in ledger) == accounting['run']['total_tokens']


def test_original_missing_revision_is_recovered_only_to_same_build_visit():
    s = state(); s['index'] = 3; s['revision'] = 11
    first = flow.usage_point(s, measured(0), observed_at='2026-09-23T00:04:05Z')
    s['revision'] = 13; s['visits'][3]['decision'] = 'awaiting_human_approval'
    next_point = flow.usage_point(s, measured(3756746), previous_revision=12, observed_at='2026-09-23T00:23:49Z')
    result = flow.phase_usage([first, next_point], s, measured(3756753))
    build = result['visits'][w.current(s)['id']]
    assert build['buckets']['unsegmented']['total_tokens'] == 3756746
    assert build['buckets']['review']['total_tokens'] == 7
    assert build['status'] == 'partial'
    assert result['unallocated']['total_tokens'] == 0
    assert result['intervals'][0]['reason'] == 'missing_revision_within_visit'
    conserved(result)


def test_missing_transition_across_phases_remains_explained_not_guessed():
    s = state(); first = flow.usage_point(s, measured(0))
    s['index'] = 1; s['revision'] = 3
    second = flow.usage_point(s, measured(20), previous_revision=2)
    result = flow.phase_usage([first, second], s, measured(30))
    assert result['unallocated']['total_tokens'] == 20
    assert result['phases']['design']['tokens']['total_tokens'] == 10
    assert result['intervals'][0]['category'] == 'unresolved'
    assert result['intervals'][0]['reason'] == 'missing_transition'
    conserved(result)


def test_follow_up_and_pre_run_are_separate_from_phases():
    s = state(); first = flow.usage_point(s, measured(0))
    s['finished'] = True; s['revision'] = 1
    finish = flow.usage_point(s, measured(10), previous_revision=0)
    result = flow.phase_usage([first, finish], s, measured(25))
    assert result['attributed']['total_tokens'] == 10
    assert result['non_phase']['follow_up']['tokens']['total_tokens'] == 15
    assert result['pre_run']['tokens']['total_tokens'] == 100
    assert not result['pre_run']['included_in_run']
    assert not any('follow_up' in v['buckets'] for v in result['visits'].values())
    conserved(result)


def test_missing_finish_boundary_does_not_claim_follow_up_amount():
    s = state(); first = flow.usage_point(s, measured(0))
    s['finished'] = True; s['revision'] = 2
    finish = flow.usage_point(s, measured(10), previous_revision=1)
    result = flow.phase_usage([first, finish], s, measured(15))
    assert result['unallocated']['total_tokens'] == 10
    assert result['non_phase']['follow_up']['tokens']['total_tokens'] == 5
    assert result['intervals'][0]['reason'] == 'missing_finish_boundary'
    conserved(result)


@pytest.mark.parametrize('boundary_status', ['recorded; native counter unavailable', 'partial'])
def test_saved_or_partial_boundary_cannot_be_treated_as_fresh(boundary_status):
    s = state(); first = flow.usage_point(s, measured(0))
    s['index'] = 1; s['revision'] = 1
    saved = flow.usage_point(s, measured(0, status=boundary_status), previous_revision=0)
    result = flow.phase_usage([first, saved], s, measured(30))
    assert result['unallocated']['total_tokens'] == 30
    assert any(i['reason'] == 'recorded_or_partial_boundary' and i['tokens']['total_tokens'] == 30 for i in result['intervals'])
    conserved(result)


def test_late_session_and_unknown_boundary_have_session_amount_and_reason():
    s = state(); first = flow.usage_point(s, measured(0))
    m = measured(10)
    m['sessions'].append({'session': 'late-child', 'role': 'lens', 'status': 'measured',
                          'native_usage': counts(7), 'usage': counts(7)})
    m['tokens'] = counts(17)
    result = flow.phase_usage([first], s, m)
    assert result['sessions']['late-child']['unresolved']['total_tokens'] == 7
    assert any(i['session'] == 'late-child' and 'late_session' in i['reason'] for i in result['intervals'])
    assert result['attributed']['total_tokens'] == 10
    conserved(result)


def test_no_boundaries_retains_known_amounts_and_does_not_invent_zero_usage():
    result = flow.phase_usage([], state(), measured(19))
    assert result['status'] == 'unknown'
    assert result['unallocated']['total_tokens'] == 19
    conserved(result)
    unknown = flow.phase_usage([], state(), {'sessions': [], 'tokens': None})
    assert unknown['unallocated'] is None and not unknown['accounting']['reconciled']


def test_duplicate_boundaries_do_not_double_count_and_reset_is_explicit():
    s = state(); first = flow.usage_point(s, measured(0))
    s['revision'] = 1
    second = flow.usage_point(s, measured(10), previous_revision=0)
    result = flow.phase_usage([first, deepcopy(first), second, deepcopy(second)], s, measured(20))
    assert result['attributed']['total_tokens'] == 20
    conserved(result)
    reset = flow.phase_usage([first, second], s, measured(-95))
    assert reset['unallocated'] is None
    assert not reset['attributed']
    assert any('reset' in i['reason'] for i in reset['intervals'])


def test_host_review_excluded_and_cached_input_not_added_again():
    s = state(); start = measured(0); end = measured(30)
    start['sessions'][0]['native_usage'].update(cached_input_tokens=50, uncached_input_tokens=50)
    end['sessions'][0]['native_usage'].update(cached_input_tokens=70, uncached_input_tokens=60)
    end['tokens'].update(cached_input_tokens=20, uncached_input_tokens=10)
    end['sessions'][0]['usage'] = end['tokens'].copy()
    end['sessions'].append({'session': 'review', 'role': 'host_approval_review', 'status': 'measured',
                             'native_usage': counts(1000), 'usage': counts(1000)})
    result = flow.phase_usage([flow.usage_point(s, start)], s, end)
    assert result['attributed']['total_tokens'] == 30
    assert 'review' not in result['sessions']
    conserved(result)


def test_report_details_and_dashboard_explain_usage_without_raw_html(tmp_path):
    s = state(); initial = flow.usage_point(s, measured(0))
    s['finished'] = True; s['revision'] = 1
    finish = flow.usage_point(s, measured(10), previous_revision=0)
    ledger = flow.phase_usage([initial, finish], s, measured(25))
    ledger['intervals'][0]['session'] = '<script>private</script>'
    html = flow_dashboard.usage_details({'phase_usage': ledger})
    assert 'Where measured tokens went' in html and 'Before this run (excluded' in html
    assert 'Unsegmented' in html and 'Follow up: 15' in html
    assert '&lt;script&gt;' in html and '<script>private</script>' not in html
    # Compact transport retains reconciliation, with the complete ledger behind a reference.
    result = context_views.summary(Store(tmp_path), {'phase_usage': ledger}, 'report')
    assert result['usage_accounting']['reconciled']
    assert result['usage_accounting']['details']['kind'] == 'usage-accounting'
    assert len(json.dumps(result).encode()) < 16384


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_next_run_baseline_closes_history_when_native_log_is_unavailable(monkeypatch, host):
    start = {'run': 'old', 'session': 'root', 'at': '2026-09-01T00:00:00Z', 'host': host, 'usage': counts(100)}
    closing = {'kind': 'start', 'run': 'next', 'session': 'root', 'at': '2026-09-01T01:00:00Z',
               'host': host, 'usage': counts(180), 'usage_status': 'observed'}
    unavailable = [{'session': 'root', 'agent': 'orchestrator', 'role': 'orchestrator',
                    'usage': None, 'native_usage': None, 'status': 'unavailable'}]
    monkeypatch.setattr(flow_usage, '_codex_sessions', lambda *_: (deepcopy(unavailable), 0))
    monkeypatch.setattr(flow_usage.claude, 'sessions', lambda *_: (deepcopy(unavailable), 0))
    saved = {'kind': 'usage', 'run': 'old', 'measurement': {'sessions': measured(20)['sessions']}}
    result = flow_usage.reconcile(start, [closing, saved])
    assert result['tokens']['total_tokens'] == 80
    assert result['sessions'][0]['measurement_source'] == 'run_boundary'
    assert result['sessions'][0]['measured_at'] == closing['at']
    start['usage_status'] = 'partial'
    assert flow_usage.reconcile(start, [closing, saved])['tokens'] is None
    start['usage_status'] = 'observed'
    # Partial or unrelated observations cannot substitute for the closing baseline.
    closing['usage_status'] = 'partial'
    assert flow_usage.reconcile(start, [closing, saved])['tokens']['total_tokens'] == 20
    closing['usage_status'] = 'observed'; closing['session'] = 'other'
    assert flow_usage.reconcile(start, [closing, saved])['tokens']['total_tokens'] == 20


def test_excess_session_allocation_is_explained_instead_of_silently_clamped():
    s = state(); first = flow.usage_point(s, measured(0))
    m = measured(40); m['tokens'] = counts(10); m['sessions'][0]['usage'] = counts(10)
    result = flow.phase_usage([first], s, m)
    assert result['unallocated']['total_tokens'] == 10
    assert not result['attributed']
    assert any('conflict' in i['reason'] for i in result['intervals'])
    conserved(result)


def test_dashboard_distinguishes_known_empty_bucket_from_unknown():
    s = state(); first = flow.usage_point(s, measured(0))
    result = flow.phase_usage([first], s, measured(10))
    assert 'Outside phases: 0' in flow_dashboard.usage_details({'phase_usage': result})
    result['accounting']['reconciled'] = False
    result['accounting']['non_phase'] = None
    assert 'Outside phases: Unknown' in flow_dashboard.usage_details({'phase_usage': result})
def test_claude_reused_worker_charges_only_current_messages(tmp_path, monkeypatch):
    from taskplane.tests.test_claude_flow import setup, write, message
    from taskplane import flow
    workspace, path = setup(tmp_path, monkeypatch)
    child = path.with_suffix('') / 'subagents/agent-reused.jsonl'
    write(child, [message('old', agent='reused'),
                  message('current', agent='reused', at='2026-09-15T00:00:04Z')])
    report = flow.report(workspace)
    session = next(s for s in report['sessions'] if s['session'] == 'reused')
    assert session['usage']['total_tokens'] == 142
    assert session['native_usage']['total_tokens'] == 284
    assert session['status'] == 'measured'


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('baseline_status,baseline,expected', [
    ('partial', counts(100), None),
    ('unavailable', None, None),
    ('observed', {'total_tokens': 100}, None),
    (None, {'total_tokens': 100}, None),
    (None, counts(100), 50),
    ('observed', counts(100), 50),
    ('observed', counts(0), 150),
])
def test_root_baseline_must_be_complete_before_run_delta(
        tmp_path, monkeypatch, host, baseline_status, baseline, expected):
    from taskplane import claude_flow_usage as claude
    from taskplane.tests.test_native_session_meter import _write_thread_segment
    path = tmp_path / 'sessions/root.jsonl'
    path.parent.mkdir()
    _write_thread_segment(path, total=150, ordinal=5)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.setattr(claude, 'read_snapshot', lambda *_args, **_kwargs:
                        {'usage': counts(150), 'started': 0, 'partial': False})
    run = {'run': 'old', 'session': 'root', 'host': host, 'usage': baseline,
           'at': '2026-09-01T00:00:10Z', 'transcript_path': str(path)}
    if baseline_status is not None:
        run['usage_status'] = baseline_status
    result = flow_usage.reconcile(run, [])
    root = result['sessions'][0]
    assert root['native_usage'] == counts(150)
    if expected is None:
        assert root['usage'] is None and root['status'] == 'partial'
    else:
        assert root['usage'] == counts(expected) and root['status'] == 'measured'


@pytest.mark.parametrize('case,total,partial', [
    ('identified', 220, False),
    ('request_id', 220, False),
    ('missing', 110, True),
    ('empty', 110, True),
    ('null', 110, True),
    ('false', 110, True),
    ('malformed', 110, True),
    ('synthetic', 110, False),
    ('foreign_session', 110, False),
    ('foreign_agent', 110, False),
    ('sidechain', 110, False),
])
def test_claude_message_identity_gap_preserves_only_known_subtotal(tmp_path, case, total, partial):
    from taskplane import claude_usage_cache as cache
    path = tmp_path / 'root.jsonl'
    first = {'type': 'assistant', 'sessionId': 'root', 'timestamp': '2026-09-15T00:00:01Z',
             'message': {'id': 'first', 'model': 'claude', 'usage': {
                 'input_tokens': 100, 'cache_creation_input_tokens': 0,
                 'cache_read_input_tokens': 0, 'output_tokens': 10}}}
    path.write_text(json.dumps(first) + '\n')
    before = cache.read_snapshot(path, 'root', workspace=tmp_path)
    assert before['usage']['total_tokens'] == 110
    assert not before['partial'] and before['coverage']['complete']
    second = deepcopy(first)
    second['message'].pop('id')
    if case == 'identified':
        second['message']['id'] = 'second'
    elif case == 'request_id':
        second['message']['id'] = ''
        second['requestId'] = 'second'
    elif case in ('empty', 'null', 'false', 'malformed'):
        second['message']['id'] = {'empty': '', 'null': None, 'false': False,
                                   'malformed': ['second']}[case]
    elif case == 'synthetic':
        second['message']['model'] = '<synthetic>'
    elif case == 'foreign_session':
        second['sessionId'] = 'other'
    elif case == 'foreign_agent':
        second['agentId'] = 'other'
    elif case == 'sidechain':
        second['isSidechain'] = True
    with path.open('a') as stream:
        stream.write(json.dumps(second) + '\n')
    # Appending a gap must invalidate completeness even without a new sample;
    # reopening the derived cache must retain that diagnosis and known subtotal.
    for _ in range(2):
        result = cache.read_snapshot(path, 'root', workspace=tmp_path)
        assert result['usage']['total_tokens'] == total
        assert result['partial'] is partial
        assert result['coverage']['complete'] is not partial
        assert result['errors'] == (['invalid_message_identity'] if partial else [])


def test_claude_message_identity_gap_replays_legacy_cache(tmp_path, monkeypatch):
    import hashlib
    import sqlite3
    from taskplane import claude_usage_cache as cache
    path = tmp_path / 'root.jsonl'
    first = {'type': 'assistant', 'sessionId': 'root', 'timestamp': '2026-09-15T00:00:01Z',
             'message': {'id': 'first', 'usage': {'input_tokens': 100,
                 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0,
                 'output_tokens': 10}}}
    missing = deepcopy(first)
    missing['message'].pop('id')
    source = (json.dumps(first) + '\n' + json.dumps(missing) + '\n').encode()
    path.write_bytes(source)
    with monkeypatch.context() as legacy:
        legacy.setattr(cache, 'SCHEMA', 1)
        cache.read_snapshot(path, 'root', workspace=tmp_path)
        old = cache.cache_path(path, 'root', None, tmp_path)
        # Version 1 had advanced past the unidentified row without recording a
        # gap. Reproduce that valid checkpoint, including its cached subtotal.
        with sqlite3.connect(old) as db:
            meta = json.loads(db.execute('SELECT body FROM meta').fetchone()[0])
            meta['gaps'] = {}
            db.execute('UPDATE meta SET body=?', (json.dumps(meta),))
        before = cache.read_snapshot(path, 'root', workspace=tmp_path)
        assert before['usage']['total_tokens'] == 110 and before['coverage']['complete']
    fingerprint = hashlib.sha256(old.read_bytes()).hexdigest()
    for _ in range(2):
        result = cache.read_snapshot(path, 'root', workspace=tmp_path)
        assert result['usage']['total_tokens'] == 110
        assert result['partial'] and not result['coverage']['complete']
        assert result['errors'] == ['invalid_message_identity']
    assert cache.cache_path(path, 'root', None, tmp_path) != old
    assert hashlib.sha256(old.read_bytes()).hexdigest() == fingerprint
    assert path.read_bytes() == source


def test_claude_recovered_prerun_counter_does_not_become_run_usage(tmp_path):
    from taskplane.tests.test_claude_flow import message, write
    from taskplane import claude_flow_usage as claude
    path = tmp_path / 'root.jsonl'
    incomplete = message('missing')
    del incomplete['message']['usage']['cache_read_input_tokens']
    write(path, [message('first'), incomplete])
    before = claude.read_snapshot(path, 'root')
    assert before['partial'] and before['usage']['total_tokens'] == 142
    run = {'run': 'run', 'session': 'root', 'host': 'claude',
           'at': '2026-09-15T00:00:10Z', 'transcript_path': str(path),
           'usage': before['usage'], 'usage_status': 'partial'}
    write(path, [message('first'), message('missing')])
    report = flow_usage.reconcile(run, [])
    assert report['tokens'] is None
    assert report['native_tokens']['total_tokens'] == 284
    assert report['sessions'][0]['status'] == 'partial'


@pytest.mark.parametrize('new_total,new_cached', [(30, 0), (150, 10), (150, 0)])
def test_resumed_codex_reset_is_not_restored_from_saved_or_closing_counts(
        tmp_path, monkeypatch, new_total, new_cached):
    from taskplane.tests.test_native_session_meter import _write_thread_segment
    folder = tmp_path / 'sessions'
    folder.mkdir()
    _write_thread_segment(folder / 'first.jsonl', total=100, cached=20, ordinal=5)
    _write_thread_segment(folder / 'resumed.jsonl', total=new_total, cached=new_cached,
                          resumed=True, ordinal=15)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    baseline = counts(100)
    baseline.update(cached_input_tokens=20, uncached_input_tokens=80)
    run = {'run': 'run', 'session': 'root', 'usage': baseline, 'usage_status': 'observed',
           'at': '2026-09-01T00:00:10Z',
           'session_paths': {'root': [str(folder / 'first.jsonl'), str(folder / 'resumed.jsonl')]}}
    saved = {'kind': 'usage', 'run': 'run', 'measurement': measured(0)}
    closing = {'kind': 'start', 'run': 'next', 'session': 'root',
               'at': '2026-09-01T00:00:20Z', 'usage': counts(200), 'usage_status': 'observed'}
    closing['usage'].update(cached_input_tokens=20, uncached_input_tokens=180)
    result = flow_usage.reconcile(run, [saved, closing])
    root = result['sessions'][0]
    assert root['usage'] is None and root['status'] == 'partial'
    assert root['errors']


def test_codex_reconcile_preserves_fatal_lineage_over_replay_saved_totals(tmp_path, monkeypatch):
    from taskplane.tests.test_native_session_meter import _usage_file, _usage_record
    from taskplane.tests.test_history_bounds import _codex_native
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    root = _codex_native(tmp_path / 'root.jsonl', 'root', total=10)
    first = _usage_file(tmp_path / 'first.jsonl', [_usage_record(10, 1)])
    second = _usage_file(tmp_path / 'second.jsonl', [_usage_record(20, 2)])
    run = {'run': 'run', 'session': 'root', 'usage': counts(0), 'at': '2026-09-01T00:00:00Z',
           'worker_sessions': ['child'], 'transcript_path': str(root),
           'session_paths': {'child': [str(first), str(second)]}}
    saved = {'kind': 'usage', 'run': 'run', 'measurement': {'sessions': [
        {'session': 'child', 'agent': 'child', 'role': 'lens', 'usage': counts(20),
         'native_usage': counts(20), 'status': 'measured',
         'attribution_schema': 'taskplane.owned-interval/v1',
         'interval': {'start': flow_usage._time(run['at']), 'end_exclusive': None}}]}}
    report = flow_usage.reconcile(run, [saved])
    child = next(s for s in report['sessions'] if s['session'] == 'child')
    assert child['usage'] is None and child['status'] == 'partial' and child['attribution_unknown']
    assert any('restart evidence' in e for e in child['errors'])
    assert report['tokens']['total_tokens'] == 10
    assert report['token_coverage']['partial_sessions'] == 1


def test_phase_rejects_attribution_unknown_even_if_legacy_status_says_measured():
    s = state()
    baseline = flow.usage_point(s, measured(0))
    current = measured(20)
    current['sessions'][0]['attribution_unknown'] = True
    point = flow.usage_point(s, current)
    # Cover both preserved and malformed/legacy point callers.
    point['sessions'][0]['attribution_unknown'] = True
    result = flow_usage.phase_accounting([baseline], point, s, current)
    assert not result['attributed']
    assert all(item['category'] != 'phase' for item in result['intervals'])


def test_codex_declared_unfound_reviewer_remains_unknown_without_accumulating(tmp_path, monkeypatch):
    from taskplane.tests.test_history_bounds import _codex_native
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    root = _codex_native(tmp_path / 'root.jsonl', 'root', total=10)
    run = {'run': 'r', 'session': 'root', 'usage': counts(0), 'at': '2026-09-01T00:00:00Z',
           'transcript_path': str(root)}
    for _ in range(3):
        result = flow_usage.reconcile(run, [], expected=['unfound-reviewer'])
        assert result['token_coverage']['unmeasured_sessions'] == 1
        assert result['tokens']['total_tokens'] == 10


def test_review_container_reconcile_excludes_coverage_identity(monkeypatch):
    expected = []
    monkeypatch.setattr(flow_usage, 'reconcile', lambda _run, _rows, agents, **_kwargs: expected.extend(agents))
    envelope = {'reviews': [{'agent': 'reviewer'}, {'agent': 'reviewer'}],
                'coverage': [{'agent': 'metadata-only'}], 'status': 'incomplete'}
    flow.reconcile_usage({'run': 'r'}, [{'run': 'r', 'captured': {'reviews': envelope}}], {})
    assert expected == ['reviewer']


@pytest.mark.parametrize('container', ['list', 'grouped', 'envelope'])
def test_review_container_report_preserves_review_phase(tmp_path, container):
    from taskplane.tests.test_em_remediation import journal_fixture
    from taskplane.tests.test_workflow_host import controller
    c, _, s = controller(tmp_path)
    review = {'agent': 'root', 'phase': 'engineering'}
    reviews = ([review] if container == 'list' else {'build': [review]} if container == 'grouped' else
               {'reviews': [review], 'coverage': [{'agent': 'metadata-only'}], 'status': 'incomplete'})
    journal_fixture(c.workspace, [dict(kind='start', run=s['run'], session='root'),
        dict(kind='attach', run=s['run'], artifacts={'reviews': 'captured.json'}, captured={'reviews': reviews})])
    report = flow.report(c.workspace, s['run'], governor=c)
    assert report['reviews'] == [{**review, 'phase': 'build' if container == 'grouped' else 'engineering',
                                  'coverage_status': 'legacy_unverified'}]
    assert not any(error.startswith('reviews:') for error in report['evidence_errors'])


@pytest.mark.parametrize('compaction_bytes', [0, 2 * 1024 * 1024 + 100])
def test_review_container_native_report_transition_and_dashboard_recover(tmp_path, monkeypatch, compaction_bytes):
    from taskplane.tests.test_workflow_host import controller
    from taskplane.tests.test_workflow_evidence import prepare
    from taskplane.tests.test_cli_delivery import run_cli
    from taskplane.tests.test_history_bounds import _codex_native, _codex_catalog_fixture
    from taskplane.tests.test_native_session_meter import _usage_record
    from taskplane import flow_journal as journal

    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    c, host, s = controller(tmp_path)
    native = _codex_native(tmp_path / 'root.jsonl', 'root', total=100)
    _codex_catalog_fixture(tmp_path, [('root', native)])
    flow.append(c.workspace, {'kind': 'start', 'run': s['run'], 'session': 'root',
        'at': '2026-09-01T00:00:00Z', 'usage': counts(100), 'usage_status': 'observed',
        'transcript_path': str(native)})
    baseline = flow.report(c.workspace, s['run'], governor=c)
    assert baseline['tokens'] == counts(0)
    flow.append(c.workspace, flow.usage_point(s, baseline))
    if compaction_bytes:
        with native.open('a') as stream:
            stream.write(json.dumps({'type': 'compacted', 'payload': {
                'message': 'x' * compaction_bytes, 'latest_token_usage_record': _usage_record(100, 1)}}) + '\n')
    envelope = {'reviews': [{'agent': 'root', 'phase': 'engineering'}],
                'coverage': [{'agent': 'metadata-only', 'status': 'missing'}], 'status': 'incomplete'}
    attached = c.workspace / 'reviews.json'
    attached.write_text(json.dumps(envelope))
    artifacts = {'reviews': 'reviews.json'}
    flow.append(c.workspace, {'kind': 'attach', 'run': s['run'], 'artifacts': artifacts,
                             'captured': flow.capture_attachments(c.workspace, artifacts)})
    original = (c.workspace / flow.JOURNAL).read_bytes()
    authority = host.store.read_bytes()

    def advance_native(total):
        row = _usage_record(total, total)
        row['payload']['thread_id'] = 'root'
        with native.open('a') as stream:
            stream.write(json.dumps(row) + '\n')

    for index, total in enumerate((130, 145, 145)):
        if index < 2:
            advance_native(total)
        report = flow.report(c.workspace, s['run'], governor=c)
        assert report['tokens'] == counts(total - 100)
        assert report['sessions'][0]['status'] == 'measured'
        assert report['sessions'][0]['measured_at']
        # The native catalog cannot certify orphan discovery, even when all known counters are fresh.
        assert report['usage_measurement']['status'] == 'partial'
        assert any(d['reason'] == 'native_catalog_does_not_cover_orphan_or_legacy_segments'
                   for d in report['token_coverage']['discovery_diagnostics'])
        assert report['token_coverage']['unmeasured_sessions'] == 0
        assert report['reviews'] == [{**envelope['reviews'][0], 'coverage_status': 'legacy_unverified'}]
        conserved(report['phase_usage'])
        visit = w.current(s)['id']
        assert report['phase_usage']['visits'][visit]['tokens'] == counts(total - 100)
        html = flow_dashboard.render(str(c.workspace), {**report, 'snapshot': {'visit': visit}})
        assert f'Current visit tokens</span><b>{total - 100}</b>' in html
    assert (c.workspace / flow.JOURNAL).read_bytes() == original
    assert host.store.read_bytes() == authority

    # Submit is an actual control revision transition: its measured endpoint must survive indexing.
    _, output, _ = prepare(c.workspace, 'product')
    output.update(run=s['run'], visit=visit)
    (c.workspace / 'product.json').write_text(json.dumps(output))
    run_cli(c.workspace, 'flow', 'submit', '--workspace', str(c.workspace), '--run', s['run'],
            '--expected-revision', str(s['revision']), '--output', 'product.json', '--tasks', 'tasks.json', governor=c)
    with journal.action_budget():
        view = journal.projection(c.workspace)
        points = view.page(s['run'], kind='usage_boundary')['rows']
        assert view.coverage()['status'] == 'complete'
    endpoint = points[-1]
    assert endpoint['revision'] == s['revision'] + 1 and endpoint['previous_revision'] == s['revision']
    assert endpoint['sessions'][0]['status'] == 'measured'
    assert endpoint['sessions'][0]['native_usage'] == counts(145)
    assert endpoint['sessions'][0]['usage'] == counts(45)
    assert (c.workspace / flow.JOURNAL).read_bytes().startswith(original)
