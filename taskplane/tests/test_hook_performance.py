"""Hook admission must not rebuild presentation-only worker context/status."""
from copy import deepcopy

import pytest

from taskplane import flow, workflow as w, workflow_retention
from taskplane.tests.test_worker_runtime import setup, reserve, launch, consume, complete
from taskplane.tests.test_workflow_host import controller, submit


def no_diagnostics(monkeypatch, adapter):
    def unexpected(*args, **kwargs):
        pytest.fail('Hook admission rebuilt full status diagnostics')
    monkeypatch.setattr(adapter, 'decorate', unexpected)
    monkeypatch.setattr(workflow_retention, 'capacity', unexpected)


@pytest.mark.parametrize('name', ['PreToolUse', 'PostToolUse', 'SessionStart', 'UserPromptSubmit', 'Stop'])
def test_hooks_skip_diagnostics_and_preserve_current_binding(tmp_path, monkeypatch, name):
    c, state = setup(tmp_path, count=1)
    prepared = reserve(c, state)
    launch(c, state, prepared)
    consume(c, state, prepared['grant'], 'native-0')
    complete(c, state, prepared, 'native-0')
    no_diagnostics(monkeypatch, c.adapter)
    event = dict(hook_event_name='PreToolUse', cwd=str(tmp_path), session_id='root',
                 tool_name='Read', tool_input={'file_path': 'input.py'}, call_id='fixture-hook')
    flow.hook(dict(event), governor=c)
    if name != 'PreToolUse':
        flow.hook({**event, 'hook_event_name': name}, governor=c)
    current = c.report(diagnostics=False)
    assert current['run'] == state['run'] and current['revision'] == state['revision']
    if name == 'PostToolUse':
        assert current['parent_hook_readiness']['matched_call'] == event['call_id']
    with pytest.raises(w.Refusal, match='outside'):
        flow.hook({**event, 'call_id': 'fixture-denial', 'tool_name': 'Write',
                   'tool_input': {'path': 'outside.py'}}, governor=c)


def test_light_report_retains_evidence_drift_and_default_diagnostics(tmp_path):
    c, host, state = controller(tmp_path)
    state = submit(c, state)
    detailed = c.report()
    light = c.report(diagnostics=False)
    assert 'storage' in detailed and 'storage' not in light
    assert all(detailed[key] == value for key, value in light.items())
    (c.workspace / 'product.json').write_text('{}')
    before = host.store.read_bytes()
    assert c.report(diagnostics=False)['invalidation_pending'] is True
    with pytest.raises(w.Refusal):
        c.guard({'tool_name': 'Write', 'tool_input': {'path': 'product.json'}}, state['run'])
    assert host.store.read_bytes() == before


def test_light_child_guard_still_requires_delivered_context_and_own_paths(tmp_path, monkeypatch):
    c, state = setup(tmp_path, count=1)
    prepared = reserve(c, state)
    launch(c, state, prepared)
    from taskplane.workflow_host import Controller, installed_adapter
    child = Controller(tmp_path, 'root', installed_adapter('codex'), principal='native-0')
    no_diagnostics(monkeypatch, child.adapter)
    event = {'tool_name': 'Write', 'tool_input': {'path': 'T0.md'}}
    with pytest.raises(w.Refusal, match='consume'):
        child.guard(event, state['run'])
    # Context delivery is explicit work and keeps its full validation contract.
    monkeypatch.undo()
    child = consume(c, state, prepared['grant'], 'native-0')
    no_diagnostics(monkeypatch, child.adapter)
    child.guard(event, state['run'])
    with pytest.raises(w.Refusal, match='outside'):
        child.guard({**event, 'tool_input': {'path': 'outside.py'}}, state['run'])


@pytest.mark.parametrize('blocker', ['process', 'worker', None])
def test_quiescence_does_not_reconstruct_context(tmp_path, monkeypatch, blocker):
    c, state = setup(tmp_path, count=1)
    state = deepcopy(state)
    if blocker == 'process':
        state['observed_handles'] = {'123': {'state': 'running'}}
    if blocker == 'worker':
        state['workers'] = {'fixture': {'state': 'unknown'}}
    no_diagnostics(monkeypatch, c.adapter)
    assert c.adapter.can_seal(state) is (blocker is None)
