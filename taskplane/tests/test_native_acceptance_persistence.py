"""Acceptance and native delivery remain distinct, using disposable controllers."""
from copy import deepcopy

from taskplane import worker_runtime as workers
from taskplane.tests.test_worker_runtime import setup, reserve, launch, consume, complete


def accepted_fixture(tmp_path):
    controller, state = setup(tmp_path, count=1)
    prepared = reserve(controller, state)
    launch(controller, state, prepared, 'native-0')
    consume(controller, state, prepared['grant'], 'native-0')
    complete(controller, state, prepared, 'native-0')
    return controller, state, prepared['grant']['grant_id']


def test_repeated_terminal_preserves_exact_independent_acceptance(tmp_path):
    controller, state, grant = accepted_fixture(tmp_path)
    before = controller.report(diagnostics=False)
    for call, status in [('later-complete', 'completed'), ('later-idle', 'idle')]:
        controller.observe({'hook_event_name': 'PostToolUse', 'session_id': 'root', 'call_id': call,
            'tool_name': 'collaboration.list_agents', 'tool_input': {},
            'tool_response': {'agents': [{'agent_id': 'native-0', 'status': status}]}}, state['run'])
    after = controller.report(diagnostics=False)
    assert after['workers'][grant]['state'] == 'accepted'
    assert after['task_results'] == before['task_results']
    assert workers.result_valid(tmp_path, after, 'T0')


def test_conflicting_terminal_retains_history_with_explicit_invalidation(tmp_path):
    controller, state, grant = accepted_fixture(tmp_path)
    before = controller.report(diagnostics=False)
    controller.observe({'hook_event_name': 'PostToolUse', 'session_id': 'root', 'call_id': 'contradictory-terminal',
        'tool_name': 'collaboration.list_agents', 'tool_input': {},
        'tool_response': {'agents': [{'agent_id': 'native-0', 'status': 'failed'}]}}, state['run'])
    after = controller.report(diagnostics=False)
    row = after['workers'][grant]
    assert row['state'] == 'accepted' and row['terminal_status'] == 'completed'
    assert after['task_results'] == before['task_results']
    assert row['acceptance_invalidation']['reason'] == 'contradictory_native_terminal'
    assert not workers.result_valid(tmp_path, after, 'T0')
    stable = deepcopy(row['acceptance_invalidation'])
    workers.terminal(row, 'completed', 'late-agreement')
    assert row['acceptance_invalidation'] == stable


def test_stopped_attempt_late_completion_cannot_restore_startup_permission():
    from taskplane import workflow_write_guard as guard
    # The budget tests exercise genuine-proof validation separately. This test
    # checks that a stop is sticky even when a late terminal event is delivered.
    row = {'state': 'running', 'events': {}, 'stop_requested': {'reason': 'revision'}}
    workers.terminal(row, 'completed', 'late-native-completion')
    assert row['stop_requested'] == {'reason': 'revision'}
    import pytest
    from taskplane import workflow as w
    with pytest.raises(w.Refusal, match='stopped'):
        guard.allow_work(row)
