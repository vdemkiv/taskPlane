"""A chat history fork is not a delegated worker or transferred approval."""
import json
import pytest

from taskplane import flow, workflow as w
from taskplane.tests.test_worker_runtime import setup, reserve, launch


def metadata(home, session='fork', *, worker=False, source='vscode', suffix=''):
    folder = home / 'sessions'
    folder.mkdir(parents=True, exist_ok=True)
    payload = {'id': session, 'forked_from_id': 'root', 'source': source,
               'thread_source': 'agent_forked_thread',
               'history_base': {'thread_id': 'root', 'end_ordinal_exclusive': 10, 'end_byte_offset': 100}}
    if worker:
        payload.update(parent_thread_id='root', thread_source='subagent', source={
            'subagent': {'thread_spawn': {'parent_thread_id': 'root', 'agent_path': '/root/worker'}}})
    path = folder / f'rollout-{session}{suffix}.jsonl'
    path.write_text(json.dumps({'type': 'session_meta', 'payload': payload}) + '\n')
    return path


@pytest.mark.parametrize('prior_denial', [False, True])
def test_new_fork_read_and_cli_ignore_parent_run_and_prior_misattribution(tmp_path, monkeypatch, capsys, prior_denial):
    c, state = setup(tmp_path, count=1)
    home = tmp_path / 'native'
    metadata(home)
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('CODEX_THREAD_ID', 'fork')
    flow.append(tmp_path, {'kind': 'start', 'run': state['run'], 'session': 'root', 'phase': 'product'})
    if prior_denial:
        flow.append(tmp_path, {'kind': 'hook', 'root': 'root', 'session': 'fork', 'run': state['run'],
                              'event': 'PreToolUse', 'outcome': 'denied'})
    before = c._path().read_bytes()
    assert flow.observed_parent({}, 'fork') == 'root'  # History stays available for usage accounting.
    assert flow.independent_fork({}, 'fork')
    selected, current = flow.select_controller(tmp_path, 'fork', 'root', legacy={'session': 'root'})
    assert selected.root == selected.principal == 'fork' and not current.get('run')
    assert flow.main(['report', '--workspace', str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'no_workflow'
    event = {'host': 'codex', 'session_id': 'fork', 'cwd': str(tmp_path), 'hook_event_name': 'PreToolUse',
             'tool_name': 'Read', 'call_id': 'fixture-fork-read', 'tool_input': {'file_path': 'input.py'}}
    journal = (tmp_path / '.taskplane/flow-events.jsonl').read_bytes()
    flow.hook(event)
    assert event['taskplane_observed_binding']['root'] == event['taskplane_observed_binding']['principal'] == 'fork'
    assert 'parent_session_id' not in event
    assert (tmp_path / '.taskplane/flow-events.jsonl').read_bytes() == journal
    assert c._path().read_bytes() == before


def test_fork_can_initialize_its_own_scope_without_adopting_parent(tmp_path, monkeypatch, capsys):
    c, state = setup(tmp_path, count=1)
    metadata(tmp_path / 'native')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('CODEX_THREAD_ID', 'fork')
    flow.append(tmp_path, {'kind': 'start', 'run': state['run'], 'session': 'root', 'phase': 'product'})
    before = c._path().read_bytes()
    scope = {'criteria': ['FORK'], 'paths': {phase: ['own-' + phase + '.json'] for phase in w.PHASES},
             'verification_inputs': ['input.py']}
    (tmp_path / 'fork-scope.json').write_text(json.dumps(scope))
    assert flow.main(['activate', '--workspace', str(tmp_path), '--phase', 'tp-go',
                      '--request-reference', 'fixture/fork-request']) == 0
    capsys.readouterr()
    assert flow.main(['start', '--workspace', str(tmp_path), '--standalone', '--phase', 'product',
                      '--scope', 'fork-scope.json', '--request-reference', 'fixture/fork-request']) == 0
    created = json.loads(capsys.readouterr().out)['workflow']
    assert created['root'] == 'fork' and created['run'] != state['run']
    assert created['decisions'] == {} and not created.get('workers')
    assert created['scope']['criteria'] == ['FORK']
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('shape', ['worker', 'unknown', 'conflicting', 'unreadable'])
def test_missing_or_conflicting_fork_proof_does_not_exempt_a_child(tmp_path, monkeypatch, capsys, shape):
    c, state = setup(tmp_path, count=1)
    home = tmp_path / 'native'
    path = metadata(home, worker=shape == 'worker', source='unknown' if shape == 'unknown' else 'vscode')
    if shape == 'conflicting':
        metadata(home, worker=True, suffix='-conflict')
    if shape == 'unreadable':
        (path.parent / 'broken-fork.jsonl').write_text('invalid metadata')
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('CODEX_THREAD_ID', 'fork')
    assert not flow.independent_fork({}, 'fork')
    assert flow.main(['report', '--workspace', str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'scope_violation'
    event = {'host': 'codex', 'session_id': 'fork', 'cwd': str(tmp_path), 'hook_event_name': 'PreToolUse',
             'tool_name': 'Read', 'tool_input': {'file_path': 'input.py'}}
    with pytest.raises(w.Refusal, match='Unbound native child'):
        flow.hook(event)


def test_exact_worker_binding_still_wins_over_a_fork_label(tmp_path, monkeypatch):
    c, state = setup(tmp_path, count=1)
    prepared = reserve(c, state)
    launch(c, state, prepared, 'fork')
    metadata(tmp_path / 'native')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'native'))
    selected, current = flow.select_controller(tmp_path, 'fork', 'root')
    assert selected.root == 'root' and selected.principal == 'fork'
    assert current['workers'][prepared['grant']['grant_id']]['worker_id'] == 'fork'
    with pytest.raises(w.Refusal, match='consume'):
        selected.guard({'tool_name': 'Read', 'tool_input': {'file_path': 'input.py'}}, state['run'])
