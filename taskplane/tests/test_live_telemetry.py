"""Live warning/statistics behavior uses aggregates, never analysis history."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from taskplane import flow, live_telemetry as live, workflow as w
from taskplane.tests.test_workflow_local import setup


def event(ws, call='one', name='PreToolUse', **values):
    return {'cwd': str(ws), 'thread_id': 'root', 'hook_event_name': name,
            'tool_name': 'Read', 'tool_input': {'file_path': 'app.py'}, 'call_id': call, **values}


def test_active_hooks_never_access_journal_and_keep_original_gates(tmp_path, monkeypatch):
    c, state = setup(tmp_path, entry='engineering', standalone=True)
    (tmp_path / flow.JOURNAL).write_bytes(b'\xff\n')
    def forbidden(*args, **kwargs):
        pytest.fail('Active operation accessed the analysis journal')
    monkeypatch.setattr(flow, 'read_events', forbidden)
    monkeypatch.setattr(flow, 'append', forbidden)
    monkeypatch.setattr(flow, 'summarize', forbidden)
    monkeypatch.setattr(flow, 'counter', lambda *_: {'usage': None, 'usage_status': 'unavailable'})
    for name in ('PreToolUse', 'PostToolUse', 'SessionStart', 'UserPromptSubmit', 'Stop'):
        flow.hook(event(tmp_path, name, name), governor=c)
    with pytest.raises(w.Refusal):
        flow.hook(event(tmp_path, 'write', tool_name='Write', tool_input={'path': 'outside.py'}), governor=c)
    value = live.read(tmp_path, state)
    assert value['observation_count'] == 6
    assert value['outcomes']['denied'] == 1
    assert (tmp_path / flow.JOURNAL).read_bytes() == b'\xff\n'


def test_warnings_duplicates_unknown_usage_and_progress_reset(tmp_path):
    c, state = setup(tmp_path)
    row = {'session': 'root', 'event': 'PreToolUse', 'tool': 'Read',
           'action': 'same', 'outcome': 'admitted', 'usage': None}
    responses = [live.observe(tmp_path, state, {**row, 'call_id': str(i)}) for i in range(4)]
    assert len(responses[-1]) == 1 and '4+' in responses[-1][0]
    assert live.observe(tmp_path, state, {**row, 'call_id': '3'}) == []
    value = live.read(tmp_path, state)
    assert live.summary(value, state)['tokens'] is None
    assert value['tool_calls'] == 4
    live.progress(tmp_path, state, 'progress', 'Delivered the next result', {}, {})
    assert live.summary(live.read(tmp_path, state), state)['advice'] == []
    assert not (tmp_path / flow.JOURNAL).exists()


def test_concurrent_aggregate_writers_keep_counts_across_readers(tmp_path):
    _, state = setup(tmp_path)
    def write(i):
        return live.observe(tmp_path, state, {'session': 'root', 'event': 'PreToolUse',
                            'tool': 'Read', 'action': str(i), 'call_id': str(i), 'usage': None})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(24)))
    assert live.summary(live.read(Path(str(tmp_path)), state), state)['tool_calls'] == 24


def test_corrupt_telemetry_cannot_replace_gate_decision(tmp_path):
    c, state = setup(tmp_path)
    live.path(tmp_path, state['run']).write_text('{broken')
    assert flow.hook(event(tmp_path), governor=c) == {}
    with pytest.raises(w.Refusal):
        flow.hook(event(tmp_path, tool_name='Write', tool_input={'path': 'outside.py'}), governor=c)
    view = flow.report(tmp_path, state['run'], governor=c)
    assert view['workflow']['run'] == state['run']
    assert any('telemetry unavailable' in error for error in view['evidence_errors'])


def test_counter_deltas_and_other_warning_thresholds(tmp_path):
    _, state = setup(tmp_path)
    live.initialize(tmp_path, state, {'usage': {'total_tokens': 100}})
    for i in range(20):
        live.observe(tmp_path, state, {'session': 'root', 'event': 'PreToolUse',
            'tool': 'spawn_agent' if i < 6 else 'Read', 'action': str(i), 'call_id': str(i),
            'usage': {'total_tokens': 100100}})
    result = live.summary(live.read(tmp_path, state), state)
    assert result['tokens']['total_tokens'] == 100000
    assert len(result['advice']) == 3


def test_retry_usage_never_requires_analysis_history(tmp_path, monkeypatch):
    from taskplane import worker_runtime, flow_usage
    _, state = setup(tmp_path)
    def forbidden(*args):
        pytest.fail('Retry accounting accessed analysis history')
    monkeypatch.setattr(flow, 'read_events', forbidden)
    observed = []
    def native(run):
        observed.append((run['run'], run['session'], run['at']))
        return {'token_coverage': dict.fromkeys(
            ('unmeasured_sessions', 'partial_sessions', 'discovery_errors', 'inventory_errors'), 0),
            'sessions': [{'session': 'root', 'role': 'orchestrator',
                          'usage': {'input_tokens': 10, 'output_tokens': 2, 'cached_input_tokens': 1}}]}
    monkeypatch.setattr(flow_usage, 'owned_intervals', native)
    result = worker_runtime._retry_usage(tmp_path, state, {'binding': {'run': state['run']}})
    assert result['coverage'] == 'complete' and result['input'] == 10
    assert observed == [(state['run'], 'root', state['started_at'])]
    monkeypatch.setattr(live, 'read', forbidden)
    # A real source failure stays unknown; no journal fallback or invented zero.
    def unavailable(*args):
        raise OSError('Native usage unavailable')
    monkeypatch.setattr(flow_usage, 'owned_intervals', unavailable)
    assert worker_runtime._retry_usage(tmp_path, state, {'binding': {'run': state['run']}})['coverage'] == 'unknown'


def test_terminal_sample_is_not_replaced_by_later_hook_usage(tmp_path):
    from copy import deepcopy
    from datetime import datetime, timezone
    _, state = setup(tmp_path)
    live.initialize(tmp_path, state, {'usage': {'total_tokens': 100}})
    ended = deepcopy(state)
    ended.update(finished=True, finished_at=datetime.now(timezone.utc).isoformat())
    live.close(tmp_path, ended, {'usage': {'total_tokens': 150}, 'usage_status': 'observed'})
    live.observe(tmp_path, ended, {'session': 'root', 'event': 'PostToolUse',
        'call_id': 'late', 'usage': {'total_tokens': 999}, 'tool': 'Read'})
    value = live.read(tmp_path, ended)
    assert live.summary(value, ended)['tokens']['total_tokens'] == 50
    assert value['metadata']['closing_counter']['at'] == ended['finished_at']
    live.close(tmp_path, ended, {'usage': {'total_tokens': 999}})
    assert live.read(tmp_path, ended)['metadata']['closing_counter']['usage']['total_tokens'] == 150


@pytest.mark.parametrize('damage', ['sessions', 'sample', 'metadata', 'counts', 'missing',
    'measurement', 'measurement_sessions', 'measurement_row', 'measurement_usage', 'measurement_errors'])
def test_malformed_advisory_shapes_preserve_admission_and_refusal(tmp_path, damage):
    import json
    from taskplane.tests.test_usage_accounting import measured
    c, state = setup(tmp_path)
    value = live.empty(state)
    if damage == 'sessions': value['sessions'] = []
    elif damage == 'sample': value['sessions']['root'] = None
    elif damage == 'metadata': value['metadata'] = None
    elif damage == 'counts': value['recent_tools'] = 'many'
    elif damage.startswith('measurement'):
        value['measurement'] = measured(20)
        if damage == 'measurement': value['measurement'] = ['invalid']
        elif damage == 'measurement_sessions': value['measurement']['sessions'] = {'root': {}}
        elif damage == 'measurement_row': value['measurement']['sessions'] = [None]
        elif damage == 'measurement_usage': value['measurement']['sessions'][0]['usage'] = [20]
        else: value['measurement']['sessions'][0]['errors'] = [None]
    else: del value['outcomes']
    live.path(tmp_path, state['run']).write_text(json.dumps(value))
    assert flow.hook(event(tmp_path), governor=c) == {}
    with pytest.raises(w.Refusal) as failure:
        flow.hook(event(tmp_path, tool_name='Write', tool_input={'path': 'outside.py'}), governor=c)
    assert failure.value.reason == 'scope_violation'
    assert flow.report(tmp_path, state['run'], governor=c)['evidence_errors']


def test_saved_measurement_recovers_when_native_source_disappears(tmp_path, monkeypatch):
    from taskplane import flow_usage
    from taskplane.tests.test_usage_accounting import counts, measured
    c, state = setup(tmp_path)
    live.initialize(tmp_path, state, {'usage': counts(100), 'usage_status': 'observed'})
    live.remember_measurement(tmp_path, state, measured(20), state['started_at'])
    monkeypatch.setattr(flow_usage, '_codex_sessions', lambda *args: ([], 0))
    view = flow.report(tmp_path, state['run'], governor=c)
    assert view['tokens'] == counts(20)
    assert view['sessions'][0]['status'].startswith('recorded')
    assert view['workflow']['run'] == state['run']
    assert not view['evidence_errors']


def test_unexpected_native_reconciliation_failure_keeps_controller_report(tmp_path, monkeypatch):
    c, state = setup(tmp_path)
    def broken(*args, **kwargs):
        raise AttributeError('Malformed optional native observation')
    monkeypatch.setattr(flow, 'reconcile_usage', broken)
    view = flow.report(tmp_path, state['run'], governor=c)
    assert view['workflow']['run'] == state['run']
    assert view['tokens'] is None
    assert view['evidence_errors']
    assert c.report(state['run'], diagnostics=False)['revision'] == state['revision']


def test_failed_progress_storage_is_reported_without_mutating_authority(tmp_path, monkeypatch, capsys):
    import json
    c, state = setup(tmp_path)
    before = c.report(state['run'], diagnostics=False)
    def unavailable(*args, **kwargs):
        raise OSError('Storage unavailable')
    monkeypatch.setenv('CODEX_THREAD_ID', 'root')
    monkeypatch.setattr(live, 'progress', unavailable)
    assert flow.main(['progress', '--note', 'Work delivered', '--workspace', str(tmp_path)], governor=c) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['workflow']['observation_errors']
    assert c.report(state['run'], diagnostics=False) == before
    assert not live.path(tmp_path, state['run']).exists()


def test_unexpected_advisory_exception_cannot_replace_gate_result(tmp_path, monkeypatch):
    c, _ = setup(tmp_path)
    def broken(*args, **kwargs):
        raise AttributeError('Malformed advisory value')
    monkeypatch.setattr(flow, '_observe_hook', broken)
    assert flow.hook(event(tmp_path), governor=c) == {}
    with pytest.raises(w.Refusal) as failure:
        flow.hook(event(tmp_path, tool_name='Write', tool_input={'path': 'outside.py'}), governor=c)
    assert failure.value.reason == 'scope_violation'


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_required_budget_uses_native_owned_intervals_without_optional_baseline(tmp_path, monkeypatch, host):
    import json, sys
    from copy import deepcopy
    from datetime import datetime, timedelta
    from taskplane import worker_runtime, worker_results as results, native_session_meter
    from taskplane.tests.test_worker_results import runtime
    from taskplane.tests.test_claude_flow import message, write
    from taskplane.tests.test_native_session_meter import _write_segment
    controller, state = setup(tmp_path)
    started = datetime.fromisoformat(state['started_at'])
    before = (started - timedelta(seconds=2)).isoformat()
    after = (started + timedelta(seconds=1)).isoformat()
    if host == 'claude':
        path = tmp_path / 'claude/projects/project/root.jsonl'
        write(path, [message(at=before), message('current', at=after)])
        monkeypatch.setenv('TASKPLANE_CLAUDE_SESSION_ID', 'root')
        monkeypatch.setenv('TASKPLANE_CLAUDE_TRANSCRIPT', str(path))
        total = 142
    else:
        monkeypatch.delenv('TASKPLANE_CLAUDE_SESSION_ID', raising=False)
        monkeypatch.delenv('TASKPLANE_CLAUDE_TRANSCRIPT', raising=False)
        monkeypatch.delenv('CLAUDE_SESSION_ID', raising=False)
        monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
        path = tmp_path / 'codex/sessions/root.jsonl'
        path.parent.mkdir(parents=True)
        _write_segment(path, session_id='root', total=100, cached=20, output=10)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows: row['timestamp'] = before
        rows[0]['payload']['timestamp'] = before
        current = deepcopy(rows[-1]);current.update(timestamp=after, ordinal=8)
        current['payload']['info']['total_token_usage'].update(input_tokens=180,
            cached_input_tokens=40, output_tokens=20, total_tokens=200)
        path.write_text(''.join(json.dumps(row) + '\n' for row in [*rows, current]))
        total = 100
    # Prime the ordinary report with an intentionally incorrect advisory baseline.
    # Required accounting must use native intervals even in the same process.
    live.initialize(tmp_path, state, {'host': host, 'transcript_path': str(path),
        'usage': dict.fromkeys(native_session_meter.USAGE_KEYS, 0), 'usage_status': 'observed'})
    advisory = flow.report(tmp_path, state['run'], governor=controller)
    if sys.platform in ('darwin', 'linux'):
        assert advisory['tokens']['total_tokens'] == 2 * total
    def forbidden(*args, **kwargs):
        pytest.fail('Required budget accounting accessed optional observations')
    monkeypatch.setattr(live, 'read', forbidden)
    monkeypatch.setattr(flow, 'read_events', forbidden)
    observed = []
    for payload in (None, b'{bad', json.dumps({**live.empty(state), 'metadata': {'usage': {'total_tokens': 0}}}).encode()):
        target = live.path(tmp_path, state['run'])
        if payload is None: target.unlink(missing_ok=True)
        else: target.write_bytes(payload)
        observed.append(worker_runtime._retry_usage(tmp_path, state, {'binding': {'run': state['run']}}))
    assert observed[0] == observed[1] == observed[2]
    usage = observed[0]
    if host == 'codex' and sys.platform not in ('darwin', 'linux'):
        assert usage['coverage'] == 'unknown'  # Codex requires unsupported directory discovery here.
        return
    assert usage['coverage'] == 'complete' and usage['input'] + usage['output'] == total
    budget = results.new_budget({'run': state['run']}, {'schema': results.POLICY, 'token_cap': total}, runtime=runtime())
    budget = results.observe_budget(budget, now_ms=1, usage=usage)
    assert budget['coverage_known'] and budget['token_highwater'] == total
    assert not results.budget_admission(budget, tokens_proposed=1)['dispatch_admitted']
    path.unlink()
    assert worker_runtime._retry_usage(tmp_path, state, {'binding': {'run': state['run']}})['coverage'] == 'unknown'
