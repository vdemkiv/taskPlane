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
           'at': '2026-09-01T00:00:10Z'}
    saved = {'kind': 'usage', 'run': 'run', 'measurement': measured(0)}
    closing = {'kind': 'start', 'run': 'next', 'session': 'root',
               'at': '2026-09-01T00:00:20Z', 'usage': counts(200), 'usage_status': 'observed'}
    closing['usage'].update(cached_input_tokens=20, uncached_input_tokens=180)
    result = flow_usage.reconcile(run, [saved, closing])
    root = result['sessions'][0]
    assert root['usage'] is None and root['status'] == 'partial'
    assert root['errors']
