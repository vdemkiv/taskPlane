"""Local admission fixtures only; no installed host/tool compatibility certification."""
import pytest

from taskplane import workflow as w, workflow_local as local, worker_runtime as workers
from taskplane.tests.test_worker_runtime import setup, reserve
from taskplane.tests.test_workflow_local import setup as dashboard_setup, submit


@pytest.mark.parametrize('host,tool,args,alternative', [
    ('claude', 'spawn_agent', None, 'Agent/Task'),
    ('claude', 'collaborationspawn_agent', None, 'Agent/Task'),
    ('claude', 'list_agents', {}, 'Agent/Task'),
    ('claude', 'wait_agent', {'timeout_ms': 10000}, 'Agent/Task'),
    ('codex', 'Agent', None, 'collaboration'),
    ('codex', 'Task', None, 'collaboration'),
])
def test_foreign_worker_tools_refuse_before_admission(tmp_path, host, tool, args, alternative):
    c, state = setup(tmp_path)
    c.adapter.name = host
    item = reserve(c, state)
    if args is None:
        args = ({'prompt': item['message'], 'description': 'bounded fixture'}
                if host == 'codex' else
                {'task_name': item['grant']['task_name'], 'message': item['message'], 'fork_turns': 'none'})
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match=alternative) as exc:
        c.guard({'tool_name': tool, 'call_id': 'wrong-host', 'tool_input': args}, state['run'])
    assert exc.value.reason == 'unsupported_authority'
    assert c._path().read_bytes() == before
    assert c.report()['workers'][item['grant']['grant_id']]['state'] == 'prepared'


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_dashboard_host_mismatch_keeps_supported_link_and_checkpoint(tmp_path, host):
    c, state = dashboard_setup(tmp_path)
    state = submit(c, state)
    c.adapter.name = host
    harness = local.Harness(tmp_path, c.root)
    event = {'tool_name': 'mcp__codex_app__open_in_codex', 'tool_input': {
        'target': {'type': 'file', 'path': str(tmp_path/'.taskplane/dashboard.html')}}}
    before = c._path().read_bytes()
    if host == 'codex':
        c.guard(event, state['run'])
    else:
        with pytest.raises(w.Refusal, match='flow present') as exc:
            c.guard(event, state['run'])
        assert exc.value.reason == 'unsupported_authority'
    assert c._path().read_bytes() == before
    assert harness.read()['presentation']['outcome'] == 'linked'
    assert harness.presentation_valid(state)
    assert c.report()['decisions'] == state['decisions']


@pytest.mark.parametrize('host,operation', [
    ('claude', 'recover-unbound'), ('claude', 'recover-unavailable'),
    ('codex', 'recover-launch-denied'),
])
def test_recovery_host_preflight_preserves_grant_and_offers_status(tmp_path, host, operation):
    c, state = setup(tmp_path)
    c.adapter.name = host
    item = reserve(c, state)
    before = c._path().read_bytes()
    status = c.worker(state['run'], 'status')['recovery']
    assert status['operations'][operation]['status'] == 'unsupported_host'
    with pytest.raises(w.Refusal, match='Inspect flow worker --operation status') as exc:
        c.worker(state['run'], operation, revision=state['revision'], grant=item['grant']['grant_id'])
    assert exc.value.reason == 'unsupported_authority'
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('host,operation', [
    ('codex', 'recover-unbound'), ('codex', 'recover-unavailable'),
    ('claude', 'recover-launch-denied'),
])
def test_matching_recovery_host_still_requires_evidence(tmp_path, host, operation):
    c, state = setup(tmp_path)
    c.adapter.name = host
    item = reserve(c, state)
    before = c._path().read_bytes()
    status = c.worker(state['run'], 'status')['recovery']
    assert status['operations'][operation]['status'] == 'requires_evidence'
    with pytest.raises(w.Refusal):
        c.worker(state['run'], operation, revision=state['revision'], grant=item['grant']['grant_id'])
    assert c._path().read_bytes() == before


@pytest.mark.parametrize('kind', [None, 'agent', 'session', 'main', 'name', True])
def test_claude_host_backfill_is_display_only_and_cannot_change_target(kind):
    args = {'to': 'child', 'message': 'Scoped progress', 'summary': 'Progress',
            'type': 'message', 'recipient': 'child', 'content': 'Scoped progress'}
    if kind is not None:
        args['recipient_kind'] = kind
    if kind in (None, 'agent'):
        assert workers._claude_control_target('SendMessage', args) == 'child'
        with pytest.raises(w.Refusal):
            workers._claude_control_target('SendMessage', {**args, 'recipient': 'foreign'})
        with pytest.raises(w.Refusal):
            workers._claude_control_target('SendMessage', {**args, 'content': 'different message'})
    else:
        with pytest.raises(w.Refusal):
            workers._claude_control_target('SendMessage', args)


def test_claude_backfilled_message_retains_owned_attempt_and_does_not_join(tmp_path, monkeypatch):
    from taskplane import flow
    from taskplane.tests.test_claude_interactive_recovery import Interactive
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    args = {'to': host.child_id, 'message': 'Scoped progress', 'summary': 'Progress',
            'type': 'message', 'recipient': host.child_id, 'recipient_kind': 'agent',
            'content': 'Scoped progress'}
    event = host.root_control('SendMessage', args)
    flow.hook(event, governor=host.controller)
    flow.hook({**event, 'hook_event_name': 'PostToolUse', 'tool_response': {'success': True}},
              governor=host.controller)
    current = host.controller.report()
    assert current['worker_controls'][event['tool_use_id']]['grant'] == host.grant
    assert host.row()['state'] == 'running'
    assert not host.row().get('terminal_status')
    assert not current.get('task_results')


@pytest.mark.parametrize('defect', ['normalized', 'foreign', 'duplicate', 'result'])
def test_claude_unjoinable_launch_refuses_before_admission_and_can_abandon(tmp_path, monkeypatch, defect):
    from copy import deepcopy
    from taskplane import flow
    from taskplane.tests.test_claude_interactive_recovery import Interactive
    host = Interactive(tmp_path, monkeypatch)
    item = reserve(host.controller, host.state)
    grant = item['grant']['grant_id']
    event = host.root_control('Agent', {'prompt': item['message'], 'description': 'preflight fixture'})
    call = dict(type='assistant', sessionId='root', cwd=str(tmp_path), timestamp=workers.now(),
        message={'content': [dict(type='tool_use', id=event['tool_use_id'], name='Agent',
                                 input=deepcopy(event['tool_input']))]})
    if defect == 'normalized':
        # Historical shape: the observable transcript retains the unsupported
        # string, whereas the hook may omit it. Do not coerce either into proof.
        call['message']['content'][0]['input']['run_in_background'] = 'true'
    if defect == 'foreign':
        call['cwd'] += '-foreign'
    host.append(call)
    if defect == 'duplicate':
        host.append({**call, 'uuid': 'competing-native-record'})
    if defect == 'result':
        host.append(dict(sessionId='root', cwd=str(tmp_path), timestamp=workers.now(),
            message={'content': [dict(type='tool_result', tool_use_id=event['tool_use_id'], content='unadmitted')]}))
    before = host.controller.report()
    with pytest.raises(w.Refusal, match='unknown attempt' if defect == 'result' else 'grant remains prepared') as exc:
        flow.hook(event, governor=host.controller)
    assert exc.value.reason == 'worker_readiness'
    current = host.controller.report()
    row = current['workers'][grant]
    if defect == 'result':
        assert row['state'] == 'unknown' and row['call_id'] is None
        assert row['preflight_blocker']['unadmitted_result']
        status = host.controller.worker(host.state['run'], 'status')
        assert status['attempts'][0]['launch_blocker'] == row['preflight_blocker']
        assert current['decisions'] == before['decisions']
        assert current.get('task_results') == before.get('task_results')
        with pytest.raises(w.Refusal, match='Only an unlaunched reservation'):
            host.controller.worker(host.state['run'], 'abandon', revision=host.state['revision'], grant=grant)
        return
    assert row == before['workers'][grant]
    assert row['state'] == 'prepared' and row['call_id'] is None and row['worker_id'] is None
    assert current['decisions'] == before['decisions']
    assert current.get('task_results') == before.get('task_results')
    assert not current.get('worker_controls')
    # A refusal at this pre-dispatch point does not create the historical
    # unknown-worker join barrier. It does not authorize any escaped execution.
    if defect not in {'result', 'duplicate'}:
        host.controller.worker(host.state['run'], 'abandon', revision=host.state['revision'], grant=grant)
        assert host.controller.report()['workers'][grant]['state'] == 'failed'
        assert host.controller.report()['workers'][grant]['terminal_status'] == 'not_launched'


def test_claude_preflight_rejected_call_can_be_corrected_without_new_grant(tmp_path, monkeypatch):
    from taskplane import flow
    from taskplane.tests.test_claude_interactive_recovery import Interactive
    host = Interactive(tmp_path, monkeypatch)
    item = reserve(host.controller, host.state)
    args = {'prompt': item['message'], 'description': 'corrected current-schema launch'}
    mismatched = host.root_control('Agent', args)
    host.append(dict(type='assistant', sessionId='root', cwd=str(tmp_path), timestamp=workers.now(),
        message={'content': [dict(type='tool_use', id=mismatched['tool_use_id'], name='Agent',
                                 input={**args, 'run_in_background': 'true'})]}))
    with pytest.raises(w.Refusal, match='conflict'):
        flow.hook(mismatched, governor=host.controller)
    corrected = host.root_control('Agent', args)
    host.append(dict(type='assistant', sessionId='root', cwd=str(tmp_path), timestamp=workers.now(),
        message={'content': [dict(type='tool_use', id=corrected['tool_use_id'], name='Agent', input=args)]}))
    flow.hook(corrected, governor=host.controller)
    row = host.controller.report()['workers'][item['grant']['grant_id']]
    assert row['state'] == 'launch_pending' and row['call_id'] == corrected['tool_use_id']
    assert row['launch_preflight']['status'] == 'matched'
    assert row['worker_id'] is None and not row.get('identity_observation')


def test_claude_child_message_reports_supported_handback_without_delivery(tmp_path, monkeypatch):
    from taskplane import flow
    from taskplane.tests.test_claude_interactive_recovery import Interactive
    host = Interactive(tmp_path, monkeypatch)
    host.launch()
    host.ready()
    before = host.row()
    with pytest.raises(w.Refusal, match='native final handback') as exc:
        flow.hook(host.event('SendMessage', {'to': 'main', 'message': 'Progress'}))
    assert exc.value.reason == 'unsupported_authority'
    after = host.row()
    assert after['state'] == before['state'] == 'running'
    assert not after.get('terminal_status')
    assert not host.controller.report().get('task_results')
