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
    from taskplane import worker_runtime
    _, state = setup(tmp_path)
    def forbidden(*args):
        pytest.fail('Retry accounting accessed analysis history')
    monkeypatch.setattr(flow, 'read_events', forbidden)
    observed = []
    def native(run, events, current):
        observed.append((run['run'], events, current['root']))
        return {'token_coverage': dict.fromkeys(
            ('unmeasured_sessions', 'partial_sessions', 'discovery_errors', 'inventory_errors'), 0),
            'sessions': [{'session': 'root', 'role': 'orchestrator',
                          'usage': {'input_tokens': 10, 'output_tokens': 2, 'cached_input_tokens': 1}}]}
    monkeypatch.setattr(flow, 'reconcile_usage', native)
    result = worker_runtime._retry_usage(tmp_path, state, {'binding': {'run': state['run']}})
    assert result['coverage'] == 'complete' and result['input'] == 10
    assert observed == [(state['run'], [], 'root')]
    monkeypatch.setattr(flow, 'reconcile_usage', forbidden)
    # A real source failure stays unknown; no journal fallback or invented zero.
    def unavailable(*args):
        raise OSError('Native usage unavailable')
    monkeypatch.setattr(flow, 'reconcile_usage', unavailable)
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
