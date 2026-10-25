"""Cross-component invariants for the accepted deep-review remediation."""
from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from taskplane import flow, flow_journal, snapshot_retention as retention
from taskplane.tests.test_farm_telemetry import publish, catalog, measured, counts
from taskplane.tests.test_native_session_meter import _write_segment
from taskplane.tests.test_workflow_local import setup


def test_journal_registers_references_before_publication(tmp_path, monkeypatch):
    target = publish(tmp_path, detached=True)
    flow.append(tmp_path, {'kind': 'progress', 'run': 'r', 'session': 'root', 'artifact': str(target)})
    assert 'flow-journal' in catalog(tmp_path)['pairs'][target.stem]['pins']
    before = (tmp_path / flow.JOURNAL).read_bytes()

    def refuse(*args, **kwargs):
        raise ValueError('reference unavailable')

    monkeypatch.setattr(retention, 'register_references', refuse)
    with pytest.raises(ValueError, match='reference unavailable'):
        flow.append(tmp_path, {'kind': 'progress', 'run': 'r', 'session': 'root'})
    assert (tmp_path / flow.JOURNAL).read_bytes() == before


def test_attachment_reference_after_preview_is_registered(tmp_path):
    target = publish(tmp_path, detached=True)
    body = 'x' * 49000 + '\n' + target.as_uri()
    (tmp_path / 'evidence.txt').write_text(body)
    result = flow.capture_attachments(tmp_path, {'evidence': ['evidence.txt', 'missing.txt']})
    preview = result['evidence']['evidence.txt']
    assert preview['limited'] and target.name not in preview['text']
    assert 'flow-attachment' in catalog(tmp_path)['pairs'][target.stem]['pins']
    assert result['evidence']['missing.txt'] is None


@pytest.mark.parametrize('kind', ['tasks', 'reviews', 'evidence'])
def test_attachment_registration_failure_is_not_swallowed(tmp_path, kind):
    missing = 'snapshot-' + 'a'*16 + '-' + 'b'*64 + '.html'
    path = tmp_path / 'evidence.json'
    path.write_text(json.dumps({'artifact': missing}))
    args = {kind: [path.name] if kind == 'evidence' else path.name}
    with pytest.raises((ValueError, OSError)):
        flow.capture_attachments(tmp_path, args)
    assert not (tmp_path / flow.JOURNAL).exists()


def test_opaque_attachment_and_ordinary_snapshot_word_remain_usable(tmp_path):
    (tmp_path / 'binary.dat').write_bytes(b'\xff\x00opaque')
    (tmp_path / 'prose.md').write_text('The snapshot-v1.json fixture is not a native snapshot.')
    result = flow.capture_attachments(tmp_path, {'evidence': ['binary.dat', 'prose.md']})
    assert all(value is not None for value in result['evidence'].values())


@pytest.mark.parametrize('key', ['discovery_status', 'replay_status', 'detail_status',
                               'aggregate_status', 'journal_status', 'journal_detail_status'])
def test_partial_accounting_cannot_be_labeled_fresh(key):
    result = measured(10)
    assert flow.usage_measurement(result, 'now')['status'] == 'fresh'
    result['token_coverage'][key] = 'partial'
    value = flow.usage_measurement(result, 'now')
    assert value['status'] == 'partial' and value['measured_at'] is None


@pytest.mark.parametrize('unavailable', [False, True])
def test_report_preserves_aggregate_instead_of_detail_subtotal(tmp_path, monkeypatch, unavailable):
    controller, state = setup(tmp_path)
    flow.append(tmp_path, {'kind': 'start', 'run': state['run'], 'session': 'root',
                           'phase': 'product', 'at': state['started_at']})
    accounting = measured(10)
    accounting['tokens'] = None if unavailable else counts(200)
    accounting['token_coverage'] = {'detail_status': 'partial', 'measured_sessions': 20,
        'unmeasured_sessions': 3, 'partial_sessions': 2}
    if unavailable:
        accounting['token_coverage']['aggregate_status'] = 'partial'
    monkeypatch.setattr(flow, 'reconcile_usage', lambda *a, **kw: deepcopy(accounting))
    before = controller._path().read_bytes()
    result = flow.report(tmp_path, state['run'], governor=controller)
    assert result['tokens'] == accounting['tokens']
    assert result['token_coverage']['measured_sessions'] == 20
    assert result['token_coverage']['unmeasured_sessions'] == 3
    assert result['usage_measurement']['status'] == 'partial'
    assert result['snapshot']['measurement_status'] == 'partial'
    assert result['snapshot']['measurement_at'] is None
    assert controller._path().read_bytes() == before


def test_journal_pagination_updates_snapshot_measurement_consistently(tmp_path, monkeypatch):
    controller, state = setup(tmp_path)
    flow.append(tmp_path, {'kind': 'start', 'run': state['run'], 'session': 'root',
                           'phase': 'product', 'at': state['started_at']})
    monkeypatch.setattr(flow, 'reconcile_usage', lambda *a, **kw: measured(10))
    original = flow_journal.projection

    def partial(workspace):
        journal = original(workspace)
        journal.detail_partial = True
        return journal

    monkeypatch.setattr(flow_journal, 'projection', partial)
    result = flow.report(tmp_path, state['run'], governor=controller)
    assert result['token_coverage']['journal_detail_status'] == 'partial'
    assert result['usage_measurement']['status'] == result['snapshot']['measurement_status'] == 'partial'
    assert result['snapshot']['measurement_at'] is None


def test_counter_and_parent_use_verified_paths_without_recursive_discovery(tmp_path, monkeypatch):
    home = tmp_path / 'codex'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    path = home / 'child.jsonl'
    _write_segment(path, session_id='child', total=40, parent='root')

    def no_glob(*args, **kwargs):
        pytest.fail('startup must not recursively inventory native transcripts')

    monkeypatch.setattr(Path, 'glob', no_glob)
    event = {'host': 'codex', 'transcript_path': str(path)}
    with flow.codex_cache.action_budget(tmp_path) as budget:
        result = flow.counter(event, 'child')
        before = budget.source_bytes
        assert flow.observed_parent(event, 'child') == 'root'
        assert budget.source_bytes > before
        assert result['usage']['total_tokens'] == 40
        assert result['session_paths'] == {'child': [str(path)]}
        assert result['discovery_errors']  # Missing catalog is explicit, not a scan fallback.
        missing = flow.counter({'host': 'codex'}, 'missing')
        assert missing['usage'] is None and missing['usage_status'] == 'unavailable'


def test_counter_does_not_persist_foreign_transcript_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = tmp_path / 'foreign.jsonl'
    _write_segment(path, session_id='foreign', total=500)
    with flow.codex_cache.action_budget(tmp_path):
        result = flow.counter({'host': 'codex', 'transcript_path': str(path)}, 'root')
    assert result['session_paths'] == {'root': []}
    assert result['usage'] is None and result['discovery_errors']


@pytest.mark.parametrize('partial', [False, True])
def test_missing_source_replays_recorded_usage_across_restart(tmp_path, monkeypatch, partial):
    import subprocess
    import sys
    from taskplane import flow_usage
    from taskplane.tests.test_harness_review_regressions import native

    home = tmp_path / 'codex'
    (home / 'sessions').mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    child = home / 'sessions' / 'child.jsonl'
    rows = native(child, 'child', [(5, 100), (10, 150), (15, 170)], 'root', owned=True)
    if partial:
        del rows[2]['payload']['usage']
        child.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    run = dict(run='recovery', session='root', at='2026-09-01T00:00:10Z', worker_sessions=['child'])
    before = flow_usage.reconcile(run, [], workspace=tmp_path)
    prior = next(s for s in before['sessions'] if s['session'] == 'child')
    archived = child.with_suffix('.away')
    child.rename(archived)
    for _ in range(2):
        result = flow_usage.reconcile(run, [], workspace=tmp_path)
        row = next(s for s in result['sessions'] if s['session'] == 'child')
        assert row['usage'] == prior['usage']
        assert row['measured_at'] == prior['measured_at']
        assert row['recovery_status'] == 'recorded; native counter unavailable'
        assert (row['status'] == 'partial') == partial
        assert result['tokens'] == before['tokens']
        assert result['token_coverage']['action_work']['records'] == 0
    script = ('import json,sys; from taskplane.flow_usage import reconcile; '
              'print(json.dumps(reconcile(json.loads(sys.argv[1]), [], workspace=sys.argv[2])))')
    restarted = json.loads(subprocess.check_output(
        [sys.executable, '-c', script, json.dumps(run), str(tmp_path)], text=True))
    assert restarted['tokens'] == before['tokens']
    assert next(s for s in restarted['sessions'] if s['session'] == 'child')['measured_at'] == prior['measured_at']
    # The same validated physical source returns and contributes new usage once.
    archived.rename(child)
    next_row = deepcopy(rows[-1])
    next_row.update(ordinal=4, timestamp='2026-09-01T00:00:20Z')
    next_row['payload'].update(response_id='child-4', usage=counts(10), thread_token_usage=counts(180))
    with child.open('a') as stream:
        stream.write(json.dumps(next_row) + '\n')
    for _ in range(2):
        result = flow_usage.reconcile(run, [], workspace=tmp_path)
        assert result['tokens']['total_tokens'] == before['tokens']['total_tokens'] + 10
        row = next(s for s in result['sessions'] if s['session'] == 'child')
        assert 'recovery_status' not in row
        assert (row['status'] == 'partial') == partial


@pytest.mark.parametrize('defect', ['reset', 'conflict', 'foreign', 'generation',
                                  'restart-foreign', 'restart-ordinal', 'restart-offset'])
def test_invalid_native_source_stays_invalid_after_disappearance(tmp_path, monkeypatch, defect):
    from taskplane import native_session_meter as meter
    from taskplane.tests.test_harness_review_regressions import native

    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = tmp_path / 'native.jsonl'
    rows = native(path, 'child', [(5, 100), (15, 170)], 'root', owned=True)
    def read():
        return meter.read_owned_interval([path], 'child', start=0, workspace=tmp_path)
    assert read()['usage']['total_tokens'] == 170
    if defect.startswith('restart-'):
        history = dict(thread_id='child', end_ordinal_exclusive=1, end_byte_offset=1)
        history[{'restart-foreign': 'thread_id', 'restart-ordinal': 'end_ordinal_exclusive',
                 'restart-offset': 'end_byte_offset'}[defect]] = 'foreign' if defect == 'restart-foreign' else -1
        rows[0]['payload']['history_base'] = history
        path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    elif defect == 'foreign':
        rows[0]['payload']['id'] = 'foreign'
        path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    elif defect == 'generation':
        path.write_text(json.dumps(rows[0]) + '\n')
    else:
        bad = deepcopy(rows[-1])
        if defect == 'reset':
            bad.update(ordinal=3, timestamp='2026-09-01T00:00:20Z')
            bad['payload'].update(thread_token_usage=counts(150), response_id='new', usage=counts(1))
        else:
            bad['payload']['usage'] = counts(1)
        with path.open('a') as stream:
            stream.write(json.dumps(bad) + '\n')
    invalid = read()
    assert invalid['usage'] is None and invalid['attribution_unknown']
    path.unlink()
    for _ in range(2):
        missing = read()
        assert missing['usage'] is None and missing['attribution_unknown']
    script = ('import json,sys; from taskplane.native_session_meter import read_owned_interval; '
              'print(json.dumps(read_owned_interval([sys.argv[1]], "child", start=0, workspace=sys.argv[2])))')
    restarted = json.loads(subprocess.check_output([sys.executable, '-c', script, str(path), str(tmp_path)], text=True))
    assert restarted['usage'] is None and restarted['attribution_unknown']


@pytest.mark.parametrize('field,value', [('thread_id', 'foreign'), ('end_ordinal_exclusive', -1),
                                        ('end_byte_offset', True)])
def test_invalid_restart_never_revives_run_aggregate(tmp_path, monkeypatch, field, value):
    from taskplane import flow_usage
    from taskplane.tests.test_harness_review_regressions import native
    home = tmp_path / 'codex'
    (home / 'sessions').mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    path = home / 'sessions/child.jsonl'
    rows = native(path, 'child', [(5, 100), (15, 170)], 'root', owned=True)
    run = dict(run='invalid-restart', session='root', at='2026-09-01T00:00:00Z', worker_sessions=['child'])
    assert flow_usage.reconcile(run, [], workspace=tmp_path)['tokens']['total_tokens'] == 170
    history = dict(thread_id='child', end_ordinal_exclusive=1, end_byte_offset=1)
    history[field] = value
    rows[0]['payload']['history_base'] = history
    path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    assert flow_usage.reconcile(run, [], workspace=tmp_path)['tokens'] is None
    path.unlink()
    for _ in range(2):
        assert flow_usage.reconcile(run, [], workspace=tmp_path)['tokens'] is None
    script = ('import json,sys; from taskplane.flow_usage import reconcile; '
              'print(json.dumps(reconcile(json.loads(sys.argv[1]), [], workspace=sys.argv[2])))')
    restarted = json.loads(subprocess.check_output([sys.executable, '-c', script, json.dumps(run), str(tmp_path)], text=True))
    assert restarted['tokens'] is None


def test_transient_metadata_failure_does_not_poison_recoverable_source(tmp_path, monkeypatch):
    from taskplane import native_session_meter as meter
    from taskplane.tests.test_harness_review_regressions import native
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = tmp_path / 'native.jsonl'
    native(path, 'child', [(5, 100), (15, 170)], 'root', owned=True)
    def read():
        return meter.read_owned_interval([path], 'child', start=0, workspace=tmp_path)
    original = read()
    with monkeypatch.context() as patch:
        def unavailable(_):
            raise OSError('temporary read failure')
        patch.setattr(meter, '_session_metadata', unavailable)
        failed = read()
        assert failed['usage'] is None and not failed['attribution_unknown']
    path.unlink()
    recovered = read()
    assert recovered['usage'] == original['usage']
    assert recovered['measured_at'] == original['measured_at']
    assert not recovered['attribution_unknown']


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_missing_cache_does_not_invent_saved_attribution(tmp_path, monkeypatch, damage):
    from taskplane import codex_usage_cache as cache, native_session_meter as meter
    from taskplane.tests.test_harness_review_regressions import native

    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = tmp_path / 'native.jsonl'
    native(path, 'child', [(5, 100), (15, 170)], 'root', owned=True)
    assert meter.read_owned_interval([path], 'child', start=0, workspace=tmp_path)['usage']
    path.unlink()
    if damage == 'missing':
        cache.cache_path(tmp_path).unlink()
    else:
        cache.cache_path(tmp_path).write_bytes(b'corrupt derived state')
    for _ in range(2):
        missing = meter.read_owned_interval([path], 'child', start=0, workspace=tmp_path)
        assert missing['usage'] is None and missing['replay_status'] == 'partial'


def test_missing_source_reduces_each_exact_interval_with_shared_budget(tmp_path, monkeypatch):
    from datetime import datetime
    from taskplane import codex_usage_cache as cache, native_session_meter as meter
    from taskplane.tests.test_harness_review_regressions import native

    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    path = tmp_path / 'native.jsonl'
    native(path, 'child', [(5, 100), (10, 150), (15, 170)], 'root', owned=True)
    stamp = lambda second: datetime.fromisoformat(f'2026-09-01T00:00:{second:02d}+00:00').timestamp()
    assert meter.read_owned_interval([path], 'child', start=0, workspace=tmp_path)['usage']['total_tokens'] == 170
    path.unlink()
    with cache.action_budget(tmp_path) as budget:
        first = meter.read_owned_interval([path], 'child', start=stamp(10), end=stamp(15))
        second = meter.read_owned_interval([path], 'child', start=stamp(15))
        assert first['usage']['total_tokens'] == 50
        assert second['usage']['total_tokens'] == 20
        assert first['source_unavailable'] and second['source_unavailable']
        assert budget.records == budget.source_bytes == 0
        assert budget.rows <= budget.max_rows and budget.vm_steps <= budget.max_vm_steps


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_journal_cannot_mask_failed_discovery_after_cache_loss(tmp_path, monkeypatch, damage):
    from taskplane import codex_usage_cache as cache, flow_usage
    from taskplane.tests.test_harness_review_regressions import native

    home = tmp_path / 'codex'
    (home / 'sessions').mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    path = home / 'sessions/child.jsonl'
    native(path, 'child', [(5, 100), (15, 170)], 'root', owned=True)
    run = dict(run='r', session='root', at='2026-09-01T00:00:10Z', worker_sessions=['child'])
    before = flow_usage.reconcile(run, [], workspace=tmp_path)
    assert before['tokens']['total_tokens'] == 70
    saved = [dict(kind='usage', run='r', measurement=before)]
    path.unlink()
    if damage == 'missing':
        cache.cache_path(tmp_path).unlink()
    else:
        cache.cache_path(tmp_path).write_bytes(b'corrupt')
    for _ in range(2):
        result = flow_usage.reconcile(run, saved, workspace=tmp_path)
        assert result['tokens'] is None
        assert result['token_coverage']['discovery_errors']
        row = next(s for s in result['sessions'] if s['session'] == 'child')
        assert row['usage'] is None and row['status'] == 'unavailable'
