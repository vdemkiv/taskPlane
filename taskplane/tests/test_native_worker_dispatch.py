"""Historical native shapes: a canonical name alone is not a session ID."""
import json
from pathlib import Path
import sys
import shlex
from copy import deepcopy

import pytest

from taskplane import worker_runtime as wr, workflow as w
from taskplane.tests.test_worker_runtime import setup, reserve, launch, consume
from taskplane.tests.test_farm_workers import requires_native_reader
from taskplane.tests.test_claude_invocation import launched, command_event, execute  # noqa: F401


@pytest.mark.parametrize('words, expected', [
    ([], None), (['--operation', 'claim'], 'claim'), (['--operation=claim'], 'claim'),
    (['--run=r', '--operation=claim', '--grant', 'g'], 'claim'),
])
def test_exact_single_option_forms(words, expected):
    from taskplane import claude_invocation, runtime_command
    assert runtime_command.single_option(words, '--operation') == expected
    assert claude_invocation._option(words, '--operation') == expected


MALFORMED_OPERATIONS = [
    ['--operation'], ['--operation='], ['--operation', ''], ['--operation', '--grant'],
    ['--operation=--claim'], ['--operation', 'claim', '--operation=status'],
    ['--operation=claim', '--operation'], ['--operation=claim', '--operation=claim'],
    ['--oper=claim'], ['--operation', 'claim', '--oper=status'],
    ['--', '--operation=claim'],
]


@pytest.mark.parametrize('words', MALFORMED_OPERATIONS)
def test_exact_single_option_refuses_malformed_controls(words):
    from taskplane import claude_invocation, runtime_command
    for extract in (runtime_command.single_option, claude_invocation._option):
        with pytest.raises(ValueError):
            extract(words, '--operation')


@pytest.mark.parametrize('words, admitted', [
    (['--operation', 'claim'], True), (['--operation=claim'], True),
    (['--operation=status'], False), (['--operation', 'join'], False),
    *((words, False) for words in MALFORMED_OPERATIONS),
])
def test_worker_guard_claim_option_equivalence(tmp_path, words, admitted):
    from taskplane import workflow_host as host
    state = w.new_state(str(tmp_path), 'root', 'run',
                        {'criteria': ['AC'], 'paths': {phase: [] for phase in w.PHASES}},
                        entry='engineering', standalone=True)
    state['workflow_available'] = True
    state['workers'] = {'g': {'worker_id': 'child', 'prepared_at': '2026-01-01'}}
    controller = host.Controller(tmp_path, 'root', host.installed_adapter('codex'), principal='child')
    command = [str(Path(sys.executable).resolve()), str(Path(host.__file__).with_name('tp.py')),
               'flow', 'worker', *words, '--workspace', str(tmp_path), '--run', 'run', '--grant', 'g']
    event = dict(tool_name='exec_command', cwd=str(tmp_path), tool_input={'cmd': shlex.join(command)})
    before = deepcopy(state)
    if admitted:
        controller._guard(event, state)
    else:
        with pytest.raises(w.Refusal):
            controller._guard(event, state)
    assert state == before


@requires_native_reader
@pytest.mark.parametrize('equals', [False, True])
def test_claim_spellings_reach_actual_claude_child_identity(launched, equals):
    from taskplane import flow
    controller, state, item, _ = launched
    event = command_event(launched)
    if equals:
        words = shlex.split(event['tool_input']['command'])
        index = words.index('--operation')
        words[index:index + 2] = ['--operation=claim']
        event['tool_input']['command'] = shlex.join(words)
    updated = flow.hook(event)['hookSpecificOutput']['updatedInput']
    completed = execute(updated, controller.workspace)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    claimed = json.loads(completed.stdout)
    assert claimed['grant_id'] == item['grant']['grant_id']
    assert execute(updated, controller.workspace).returncode == 2
    flow.hook({**event, 'tool_input': updated, 'hook_event_name': 'PostToolUse'})
    sha = claimed['context']['handoff_ref']['sha256']
    for index in range(10):
        context = command_event(launched, action='context', call=f'context-{index}', extra=['--drain', sha])
        output = flow.hook(context)['hookSpecificOutput']['updatedInput']
        completed = execute(output, controller.workspace)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        delivered = json.loads(completed.stdout)
        flow.hook({**context, 'tool_input': output, 'hook_event_name': 'PostToolUse'})
        if delivered['done']:
            break
    else:
        pytest.fail('Context did not finish in bounded fixture')
    worker = controller.report()['workers'][item['grant']['grant_id']]
    assert worker['worker_id'] == 'child'
    assert worker['state'] == 'running' and worker['context_receipt']
    assert wr.readiness(worker)['status'] == 'ready'


@requires_native_reader
@pytest.mark.parametrize('option', ['--run', '--grant', '--task', '--operation'])
def test_claude_invocation_refuses_trailing_duplicate_option(launched, option):
    from taskplane import flow
    action = 'context' if option == '--task' else 'claim'
    event = command_event(launched, action=action, extra=[option])
    with pytest.raises(w.Refusal) as failure:
        flow.hook(event)
    assert failure.value.result()['reason'] == 'scope_violation'
    controller, _, item, _ = launched
    assert not controller.report()['workers'][item['grant']['grant_id']].get('claimed_at')


def metadata(path, state, row, child='native-uuid'):
    path.write_text(json.dumps({'type':'session_meta','payload':{'id':child,'timestamp':wr.now(),
        'parent_thread_id':state['root'],'source':{'subagent':{'thread_spawn':{
        'parent_thread_id':state['root'],'agent_path':'/root/'+row['task_name']}}}}})+'\n')


def test_desktop_child_actor_guards_scope_and_records_actual_binding(tmp_path, monkeypatch):
    from taskplane import flow
    home = tmp_path/'codex'; logs = home/'sessions'; logs.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    workspace = tmp_path/'repo'; workspace.mkdir()
    c, s = setup(workspace); item = reserve(c, s); launch(c, s, item)
    transcript = logs/'native-0.jsonl'
    metadata(transcript, s, item['grant'], 'native-0')
    flow.append(workspace, dict(kind='start', run=s['run'], session='root', phase='product'))
    event = dict(host='codex', cwd=str(workspace), session_id='root', agent_id='native-0',
                 agent_type='default', hook_event_name='PreToolUse', tool_name='Write',
                 call_id='before-context', tool_input={'path':'T0.md'},
                 transcript_path=str(transcript))
    with pytest.raises(w.Refusal, match='consume'): flow.hook(event)
    consume(c, s, item['grant'], 'native-0')
    flow.hook({**event, 'call_id':'own-write'})
    with pytest.raises(w.Refusal, match='outside'):
        flow.hook({**event, 'call_id':'sibling-write', 'tool_input':{'path':'T1.md'}})
    flow.hook({**event, 'call_id':'parent-progress', 'tool_name':'collaborationsend_message',
               'tool_input':{'target':'/root', 'message':'Scoped progress'}})
    flow.hook({**event, 'hook_event_name':'PostToolUse', 'call_id':'own-write'})
    # Hook entry derives executing bytes itself; caller-provided runtime identity
    # cannot replace them. A matched automatic pair combines with claim/context.
    readiness_event = {**event, 'call_id':'readiness-pair', 'taskplane_runtime_identity':{'member_sha256':{'forged':'value'}}}
    flow.hook(readiness_event)
    flow.hook({**readiness_event, 'hook_event_name':'PostToolUse'})
    readiness = wr.readiness(c.report()['workers'][item['grant']['grant_id']])
    assert readiness['status'] == 'ready'
    assert readiness['runtime_root'] == str(Path(__file__).resolve().parents[2])
    rows = [r for r in flow.read_events(workspace) if r.get('kind') == 'hook']
    assert all(r['session'] == 'native-0' for r in rows)
    assert all(r['binding_observation']['principal'] == 'native-0' for r in rows)
    assert next(r for r in rows if r['call_id'] == 'sibling-write')['outcome'] == 'denied'
    assert next(r for r in rows if r['call_id'] == 'parent-progress')['outcome'] == 'admitted'
    root_event = {k:v for k,v in event.items() if k not in {'agent_id','agent_type','transcript_path'}}
    flow.hook({**root_event, 'call_id':'root-write', 'tool_input':{'path':'T1.md'}})
    assert flow.read_events(workspace)[-1]['binding_observation']['principal'] == 'root'


@pytest.mark.parametrize('parent,extra', [
    (None, {}), ('foreign', {}), ('root', {'parent_session_id':'foreign'}),
    ('root', {'session_id':'foreign'}), ('root', {'environment':'foreign'}),
])
def test_desktop_child_actor_without_consistent_lineage_never_reaches_root_guard(monkeypatch, parent, extra):
    from taskplane import flow
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    if 'environment' in extra: monkeypatch.setenv('CODEX_THREAD_ID', extra['environment'])
    monkeypatch.setattr(flow, 'observed_parent', lambda *_: parent)
    guarded = []
    monkeypatch.setattr(flow, '_hook', lambda event, **_: guarded.append(event) or {})
    monkeypatch.setattr(flow, '_observe_hook', lambda *_, **__: {})
    event = dict(host='codex', session_id='root', agent_id='native-child', hook_event_name='PreToolUse')
    event.update({k:v for k,v in extra.items() if k != 'environment'})
    with pytest.raises(w.Refusal, match='lineage'): flow.hook(event)
    assert not guarded


@pytest.mark.parametrize('name', ['SubagentStart', 'SubagentStop'])
def test_desktop_lifecycle_child_target_does_not_replace_observing_parent(monkeypatch, name):
    from taskplane import flow
    monkeypatch.setenv('CODEX_THREAD_ID', 'native-child')
    monkeypatch.setattr(flow, 'observed_parent', lambda *_: 'root')
    observed = []
    monkeypatch.setattr(flow, '_hook', lambda event, **_: observed.append(event) or {})
    monkeypatch.setattr(flow, '_observe_hook', lambda *_, **__: {})
    flow.hook(dict(host='codex', session_id='root', agent_id='native-child', hook_event_name=name))
    assert flow.session_id(observed[0]) == 'root'


@pytest.mark.parametrize('prefix', ['', 'collaboration.', 'functions.collaboration.', 'collaboration'])
def test_historical_name_only_spawn_and_list_status_resolve_actual_uuid(tmp_path,monkeypatch,prefix):
    c,s=setup(tmp_path); home=tmp_path/'native';logs=home/'sessions';logs.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME',str(home))
    item=reserve(c,s); row=item['grant']
    event={'hook_event_name':'PreToolUse','tool_name':prefix+'spawn_agent','call_id':'native-call',
           'tool_input':{'task_name':row['task_name'],'message':item['message'],'fork_turns':'none'}}
    c.guard(event,s['run'])
    # Native result exists before metadata. The grant stays unresolved.
    c.observe({**event,'hook_event_name':'PostToolUse','tool_response':json.dumps({'task_name':'/root/'+row['task_name']})},s['run'])
    assert c.report()['workers'][row['grant_id']]['worker_id'] is None
    assert not c.adapter.can_seal(c.report())
    metadata(logs/'child.jsonl',s,row)
    c.observe({**event,'hook_event_name':'PostToolUse','tool_response':{'task_name':'/root/'+row['task_name']}},s['run'])
    actual=c.report()['workers'][row['grant_id']]
    assert actual['worker_id']=='native-uuid'
    consume(c,s,row,'native-uuid')
    c.observe({'hook_event_name':'PostToolUse','tool_name':prefix+'list_agents','call_id':'native-status',
               'tool_response':{'agents':[{'agent_name':'/root/'+row['task_name'],
                                           'agent_status':{'completed':'Private result is not retained in lifecycle state'}}]}},s['run'])
    assert c.report()['workers'][row['grant_id']]['state']=='result_pending'
    assert 'Private result' not in json.dumps(c.report()['workers'])


@pytest.mark.parametrize('prefix', ['', 'collaboration.', 'functions.collaboration.', 'collaboration'])
@pytest.mark.parametrize('status_field,status', [
    ('status', 'errored'),
    ('agent_status', {'errored': 'Selected model is at capacity. Please try a different model.'}),
])
@pytest.mark.parametrize('initial', ['bootstrapping', 'running', 'cancel_requested'])
def test_codex_errored_closes_live_attempt_as_failure(tmp_path, prefix, status_field, status, initial):
    c, s = setup(tmp_path)
    item = reserve(c, s); row = item['grant']; launch(c, s, item)
    if initial != 'bootstrapping':
        consume(c, s, row, 'native-0')
    if initial == 'cancel_requested':
        c.guard({'tool_name': prefix + 'interrupt_agent', 'tool_input': {'target': 'native-0'}}, s['run'])
    assert c.report()['workers'][row['grant_id']]['state'] == initial
    assert not c.adapter.can_seal(c.report())
    poll = {'hook_event_name': 'PreToolUse', 'tool_name': prefix + 'list_agents',
            'call_id': 'error-poll', 'tool_input': {}}
    c.guard(poll, s['run'])
    response = {'agents': [{'agent_name': '/root/' + row['task_name'], status_field: status}]}
    event = {**poll, 'hook_event_name': 'PostToolUse', 'tool_response': json.dumps(response)}
    c.observe(event, s['run'])
    failed = c.report()['workers'][row['grant_id']]
    reference = 'error-poll/' + row['grant_id']
    assert failed['state'] == failed['terminal_status'] == 'failed'
    assert failed['ended_at'] and failed['events'][reference] == 'failed'
    errors = [v for v in failed['reconciliation_events'].values() if v['kind'] == 'native_terminal_error']
    assert [v['value'] for v in errors] == [{'status': 'errored', 'reference': reference}]
    assert c.adapter.can_seal(c.report())
    assert not wr.result_valid(tmp_path, c.report(), 'T0')
    assert 'Selected model' not in json.dumps(failed)  # Lifecycle stores a reference, not arbitrary result text.
    c.observe(event, s['run'])
    assert c.report()['workers'][row['grant_id']] == failed
    next_poll = {**poll, 'call_id': 'error-poll-again'}
    c.guard(next_poll, s['run'])
    c.observe({**next_poll, 'hook_event_name': 'PostToolUse', 'tool_response': response}, s['run'])
    repeated = c.report()['workers'][row['grant_id']]
    assert repeated['state'] == 'failed' and repeated['ended_at'] == failed['ended_at']
    assert repeated['events'] == failed['events']
    assert not c.report().get('task_results')


@pytest.mark.parametrize('status', [None, 'unknown', 'error', 'running', {},
                                    {'errored': 'capacity', 'completed': 'ambiguous'}])
def test_unknown_or_ambiguous_codex_status_does_not_join(tmp_path, status):
    c, s = setup(tmp_path); item = reserve(c, s); launch(c, s, item)
    before = c.report()['workers']
    c.observe({'hook_event_name': 'PostToolUse', 'tool_name': 'collaboration.list_agents',
               'tool_response': {'agents': [{'agent_id': 'native-0', 'agent_status': status}]}}, s['run'])
    assert c.report()['workers'] == before
    assert not c.adapter.can_seal(c.report())


@pytest.mark.parametrize('target', ['foreign-worker', '/foreign/T0', 'root', ''])
def test_codex_error_for_foreign_identity_does_not_join(tmp_path, target):
    c, s = setup(tmp_path); item = reserve(c, s); launch(c, s, item)
    before = c.report()['workers']
    c.observe({'hook_event_name': 'PostToolUse', 'tool_name': 'list_agents',
               'tool_response': {'agents': [{'agent_id': target, 'agent_status': {'errored': 'capacity'}}]}}, s['run'])
    assert c.report()['workers'] == before


@pytest.mark.parametrize('prefix', ['evil.', 'evilcollaboration'])
def test_foreign_status_tool_cannot_close_codex_error(tmp_path, prefix):
    c, s = setup(tmp_path); item = reserve(c, s); launch(c, s, item)
    before = c.report()['workers']
    c.observe({'hook_event_name': 'PostToolUse', 'tool_name': prefix + 'list_agents',
               'tool_response': {'agents': [{'agent_id': 'native-0', 'status': 'errored'}]}}, s['run'])
    assert c.report()['workers'] == before


@pytest.mark.parametrize('parent', [None, 'foreign'])
def test_codex_error_cannot_bind_name_without_root_lineage(tmp_path, monkeypatch, parent):
    c, s = setup(tmp_path); item = reserve(c, s); row = item['grant']
    home = tmp_path / 'native'; logs = home / 'sessions'; logs.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    event = {'tool_name': 'spawn_agent', 'call_id': 'name-only', 'tool_input': {
        'task_name': row['task_name'], 'message': item['message'], 'fork_turns': 'none'}}
    c.guard(event, s['run'])
    metadata(logs / 'foreign.jsonl', {**s, 'root': parent}, row)
    c.observe({**event, 'hook_event_name': 'PostToolUse',
               'tool_response': {'task_name': '/root/' + row['task_name']}}, s['run'])
    before = c.report()['workers']
    c.observe({'hook_event_name': 'PostToolUse', 'tool_name': 'list_agents', 'tool_response': {
        'agents': [{'agent_name': '/root/' + row['task_name'], 'agent_status': {'errored': 'capacity'}}]}}, s['run'])
    assert c.report()['workers'] == before
    assert before[row['grant_id']]['worker_id'] is None


def test_codex_error_preserves_revoked_attempt(tmp_path):
    c, s = setup(tmp_path); item = reserve(c, s); launch(c, s, item)
    # Isolated lifecycle fixture represents an already revoked unavailable attempt.
    state = c.report(); row = state['workers'][item['grant']['grant_id']]
    row.update(state='failed', terminal_status='unavailable', revoked_at=wr.now())
    before = deepcopy(row)
    event = {'hook_event_name': 'PostToolUse', 'tool_name': 'list_agents', 'call_id': 'late-error',
             'tool_response': {'agents': [{'agent_id': 'native-0', 'status': 'errored'}]}}
    wr.observe(state, event)
    assert {k: row[k] for k in before} == before
    assert not row.get('ended_at')
    assert {v['kind'] for v in row['reconciliation_events'].values()} == {'native_terminal_error', 'late_terminal'}
    after = deepcopy(row); wr.observe(state, event)
    assert row == after


def test_codex_error_cannot_accept_and_fresh_retry_ignores_stale_polls(tmp_path):
    c, s = setup(tmp_path); item = reserve(c, s); launch(c, s, item)
    consume(c, s, item['grant'], 'native-0')
    poll = {'hook_event_name': 'PreToolUse', 'tool_name': 'collaboration.list_agents',
            'call_id': 'old-poll', 'tool_input': {}}
    c.guard(poll, s['run'])
    response = {'agents': [{'agent_id': 'native-0', 'agent_status': {'errored': 'capacity'}}]}
    c.observe({**poll, 'hook_event_name': 'PostToolUse', 'tool_response': response}, s['run'])
    failed = c.report()['workers'][item['grant']['grant_id']]
    (tmp_path / 'T0.md').write_text('Fixture result is present even though its worker failed.\n')
    request = {'outputs': ['T0.md'], 'checks': [{'name': 'Fixture check', 'status': 'pass', 'evidence': 'T0.md'}]}
    with pytest.raises(w.Refusal, match='not joined'):
        c.worker(s['run'], 'accept-result', revision=s['revision'], task='T0',
                 grant=item['grant']['grant_id'], request=request)
    assert not wr.result_valid(tmp_path, c.report(), 'T0')
    with pytest.raises(w.Refusal, match='prerequisites'):
        reserve(c, s, task='DEP')
    retry = reserve(c, s, worker_id='native-0', retry_reason='Native capacity failure; retry as a fresh attempt.')
    dispatch = {'tool_name': 'followup_task', 'call_id': 'retry-call', 'tool_input': {
        'target': 'native-0', 'message': retry['message']}}
    c.guard(dispatch, s['run'])
    c.observe({**dispatch, 'hook_event_name': 'PostToolUse', 'tool_response': {}}, s['run'])
    consume(c, s, retry['grant'], 'native-0')
    before = c.report()['workers']
    c.observe({**poll, 'hook_event_name': 'PostToolUse', 'tool_response': response}, s['run'])
    c.observe({**poll, 'call_id': 'uncorrelated', 'hook_event_name': 'PostToolUse', 'tool_response': response}, s['run'])
    assert c.report()['workers'] == before
    assert before[retry['grant']['grant_id']]['state'] == 'running'
    fresh = {**poll, 'call_id': 'fresh-poll'}
    c.guard(fresh, s['run'])
    c.observe({**fresh, 'hook_event_name': 'PostToolUse',
               'tool_response': {'agents': [{'agent_id': 'native-0', 'status': 'completed'}]}}, s['run'])
    result = c.worker(s['run'], 'accept-result', revision=s['revision'], task='T0',
                      grant=retry['grant']['grant_id'], request=request)
    assert result['grant'] == retry['grant']['grant_id'] != item['grant']['grant_id']
    assert wr.result_valid(tmp_path, c.report(), 'T0')
    assert c.report()['workers'][item['grant']['grant_id']] == failed


@requires_native_reader
def test_claude_start_stop_contract_and_child_control_refusal(tmp_path, monkeypatch):
    import re
    from taskplane import flow
    c,s=setup(tmp_path)
    c.adapter.name = 'claude'
    item=reserve(c,s);row=item['grant']
    event={'hook_event_name':'PreToolUse','tool_name':'Agent','tool_use_id':'claude-call',
           'tool_input':{'prompt':item['message'],'description':'bounded review','subagent_type':'general-purpose',
                         'run_in_background':True}}
    c.guard(event,s['run'])
    home=tmp_path/'native-home'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    parent=home/'.claude/projects'/re.sub(r'[^A-Za-z0-9]', '-', str(tmp_path))/'root.jsonl'
    parent.parent.mkdir(parents=True)
    called=wr.now()
    header=dict(sessionId='root', agentId='claude-child', isSidechain=True, cwd=str(tmp_path), timestamp=wr.now())
    records=[dict(type='assistant', sessionId='root', cwd=str(tmp_path), timestamp=called,
                  message={'content':[dict(type='tool_use', id='claude-call', name='Agent', input=event['tool_input'])]}),
             dict(type='user', sessionId='root', cwd=str(tmp_path), timestamp=wr.now(),
                  message={'content':[dict(type='tool_result', tool_use_id='claude-call', content='untrusted text')]},
                  toolUseResult=dict(isAsync=True, status='async_launched', agentId='claude-child'))]
    parent.write_text(''.join(json.dumps(r)+'\n' for r in records))
    child=parent.with_suffix('')/'subagents/agent-claude-child.jsonl'
    child.parent.mkdir(parents=True)
    child.write_text(json.dumps(header)+'\n')
    c.observe({'hook_event_name':'SubagentStart','host':'claude','session_id':'root',
               'tool_use_id':'claude-call','agent_id':'claude-child'},s['run'])
    worker=consume(c,s,row,'claude-child')
    monkeypatch.setenv('CLAUDE_SESSION_ID', 'root')
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    flow.append(tmp_path, dict(kind='start', run=s['run'], session='root', phase='product', host='claude'))
    # Real Claude hooks may omit host. Adding the normalized child thread_id
    # must not make the second host selection treat this as a Codex event.
    child_event=dict(hook_event_name='PreToolUse', cwd=str(tmp_path), session_id='root',
        agent_id='claude-child', tool_use_id='child-write', tool_name='Write',
        tool_input={'path':'T0.md'}, transcript_path=str(parent))
    flow.hook(child_event)
    flow.hook({**child_event, 'hook_event_name':'PostToolUse'})
    recorded=flow.read_events(tmp_path)[-1]
    assert recorded['binding_observation']['principal']=='claude-child'
    assert recorded['admission']['principal']=='claude-child'
    with pytest.raises(w.Refusal, match='outside'):
        flow.hook({**child_event, 'tool_use_id':'sibling-write', 'tool_input':{'path':'T1.md'}})
    engine=Path(w.__file__).with_name('tp.py')
    for action in ['start','submit','decide','policy','advance','attach','retire','present']:
        command=shlex.join([sys.executable,str(engine),'flow',action,'--workspace',str(tmp_path)])
        with pytest.raises(w.Refusal):worker.guard({'tool_name':'exec_command','tool_input':{'cmd':command}},s['run'])
    c.observe({'hook_event_name':'SubagentStop','host':'claude','session_id':'root','agent_id':'claude-child'},s['run'])
    assert c.report()['workers'][row['grant_id']]['state']=='result_pending'


def test_no_suffix_alias_and_reused_attempt_needs_new_context(tmp_path):
    c,s=setup(tmp_path);item=reserve(c,s);row=item['grant']
    with pytest.raises(w.Refusal):c.guard({'tool_name':'evil.spawn_agent','tool_input':{}},s['run'])
    launch(c,s,item)
    worker=consume(c,s,row,'native-0')
    c.observe({'hook_event_name':'SubagentStop','agent_id':'native-0'},s['run'])
    next_item=reserve(c,s,worker_id='native-0')
    event={'tool_name':'followup_task','call_id':'followup','tool_input':{'target':'native-0','message':next_item['message']}}
    c.guard(event,s['run'])
    c.observe({**event,'hook_event_name':'PostToolUse','tool_response':{'agent_id':'native-0'}},s['run'])
    with pytest.raises(w.Refusal,match='consume'):worker.guard({'tool_name':'Write','tool_input':{'path':'T0.md'}},s['run'])


@pytest.mark.parametrize('prefix', ['', 'collaboration.', 'functions.collaboration.', 'collaboration'])
def test_delayed_poll_and_stop_cannot_join_reused_worker_attempt(tmp_path,prefix):
    c,s=setup(tmp_path);item=reserve(c,s);launch(c,s,item)
    consume(c,s,item['grant'],'native-0')
    poll={'hook_event_name':'PreToolUse','tool_name':prefix+'list_agents','call_id':'old-poll','tool_input':{}}
    c.guard(poll,s['run'])
    c.observe({'hook_event_name':'SubagentStop','agent_id':'native-0'},s['run'])
    next_item=reserve(c,s,worker_id='native-0')
    dispatch={'tool_name':prefix+'followup_task','call_id':'new-attempt',
              'tool_input':{'target':'native-0','message':next_item['message']}}
    c.guard(dispatch,s['run'])
    c.observe({**dispatch,'hook_event_name':'PostToolUse','tool_response':{}},s['run'])
    consume(c,s,next_item['grant'],'native-0')
    response={'agents':[{'agent_id':'native-0','status':'completed'}]}
    c.observe({**poll,'hook_event_name':'PostToolUse','tool_response':response},s['run'])
    c.observe({'hook_event_name':'SubagentStop','agent_id':'native-0'},s['run'])
    c.observe({'hook_event_name':'SubagentStop','agent_id':'native-0',
               'call_id':'call/'+item['grant']['grant_id']},s['run'])
    assert c.report()['workers'][next_item['grant']['grant_id']]['state']=='running'
    poll={**poll,'call_id':'new-poll'}
    c.guard(poll,s['run'])
    c.observe({**poll,'hook_event_name':'PostToolUse','tool_response':response},s['run'])
    assert c.report()['workers'][next_item['grant']['grant_id']]['state']=='result_pending'
    assert c.adapter.can_seal(c.report())


def test_flattened_desktop_controls_preserve_scope_and_join_barriers(tmp_path):
    c,s=setup(tmp_path);item=reserve(c,s);launch(c,s,item)
    worker=consume(c,s,item['grant'],'native-0')
    message={'tool_name':'collaborationsend_message','tool_input':{'target':'native-0','message':'Check your assigned evidence.'}}
    c.guard(message,s['run'])
    with pytest.raises(w.Refusal): worker.guard(message,s['run'])
    with pytest.raises(w.Refusal): c.guard({**message,'tool_input':{'target':'foreign','message':'No grant'}},s['run'])
    with pytest.raises(w.Refusal): c.guard({'tool_name':'evilcollaborationspawn_agent','tool_input':{}},s['run'])
    with pytest.raises(w.Refusal): c.guard({'tool_name':'collaborationspawn_agent','tool_input':{}},s['run'])
    c.guard({'tool_name':'collaborationwait_agent','tool_input':{'timeout_ms':10000}},s['run'])
    c.guard({'tool_name':'collaborationinterrupt_agent','tool_input':{'target':'native-0'}},s['run'])
    assert c.report()['workers'][item['grant']['grant_id']]['state']=='cancel_requested'
    assert not c.adapter.can_seal(c.report())


def test_desktop_opaque_message_binds_exact_reserved_name_and_native_identity(tmp_path):
    c,s=setup(tmp_path);item=reserve(c,s,task_name='x'*46);row=item['grant']
    assert len(row['task_name'])==80 and row['task_name'].endswith('__'+row['grant_id'])
    event={'tool_name':'collaborationspawn_agent','call_id':'desktop-opaque',
           'tool_input':{'task_name':row['task_name'],'message':'opaque host-owned message bytes','fork_turns':'none'}}
    for arguments in [
        {**event['tool_input'],'task_name':'unreserved'},
        {**event['tool_input'],'task_name':'wrong__'+row['grant_id']},
        {**event['tool_input'],'message':'Taskplane grant: '+'0'*32},
        {**event['tool_input'],'message':''},
        {**event['tool_input'],'fork_turns':'all'},
    ]:
        with pytest.raises(w.Refusal):c.guard({**event,'tool_input':arguments},s['run'])
    c.guard(event,s['run'])
    assert c.report()['workers'][row['grant_id']]['state']=='launch_pending'
    assert c.report()['workers'][row['grant_id']]['worker_id'] is None
    c.observe({**event,'hook_event_name':'PostToolUse','tool_response':{'agent_id':'desktop-child'}},s['run'])
    worker=consume(c,s,row,'desktop-child')
    worker.guard({'tool_name':'Write','tool_input':{'path':'T0.md'}},s['run'])
    with pytest.raises(w.Refusal):worker.guard({'tool_name':'Write','tool_input':{'path':'T1.md'}},s['run'])


def test_opaque_followup_without_visible_grant_remains_refused(tmp_path):
    c,s=setup(tmp_path);item=reserve(c,s);launch(c,s,item)
    c.observe({'hook_event_name':'SubagentStop','agent_id':'native-0'},s['run'])
    reserve(c,s,worker_id='native-0')
    with pytest.raises(w.Refusal,match='prepared grant'):
        c.guard({'tool_name':'collaborationfollowup_task','call_id':'opaque-followup',
                 'tool_input':{'target':'native-0','message':'opaque host-owned message bytes'}},s['run'])


@pytest.mark.parametrize('journal', [True, False])
def test_independently_bound_fork_uses_own_cli_and_hook_authority(tmp_path, monkeypatch, capsys, journal):
    from taskplane import flow, workflow_host as h
    c, s = setup(tmp_path, count=1)
    own_scope = json.loads(json.dumps(s['scope']))
    own_scope['paths']['product'] = ['own.md']
    child = h.Controller(tmp_path, 'fork', h.installed_adapter('codex'))
    own = child.start(dict(scope=own_scope, entry='product', standalone=True, request_reference='fixture/own-request'))
    monkeypatch.setenv('CODEX_THREAD_ID', 'fork')
    monkeypatch.setattr(flow, 'observed_parent', lambda event, actor: 'root' if actor == 'fork' else None)
    if journal:
        flow.append(tmp_path, dict(kind='start', run=s['run'], session='root', phase='product'))
    for extra in ([], ['--run', own['run']]):
        assert flow.main(['report','--workspace',str(tmp_path),*extra]) == 0
        assert json.loads(capsys.readouterr().out)['workflow']['run'] == own['run']
    event = dict(host='codex', session_id='fork', cwd=str(tmp_path), hook_event_name='PreToolUse',
                 tool_name='Write', call_id='fork-write', tool_input={'path':'own.md'})
    flow.hook(event)
    with pytest.raises(w.Refusal): flow.hook({**event, 'tool_input':{'path':'T0.md'}})
    observed = event['taskplane_observed_binding']
    assert observed['root'] == observed['principal'] == 'fork'
    if journal:
        assert flow.read_events(tmp_path)[-1]['binding_observation']['root'] == 'fork'


def test_dual_bound_real_worker_keeps_parent_authority(tmp_path, monkeypatch, capsys):
    from taskplane import flow, workflow_host as h
    c, s = setup(tmp_path, count=1)
    child = h.Controller(tmp_path, 'native-0', h.installed_adapter('codex'))
    own_scope = json.loads(json.dumps(s['scope'])); own_scope['paths']['product'] = ['own.md']
    own = child.start(dict(scope=own_scope, entry='product', standalone=True, request_reference='fixture/own'))
    item = reserve(c, s); launch(c, s, item)
    consume(c, s, item['grant'], 'native-0')
    monkeypatch.setenv('CODEX_THREAD_ID', 'native-0')
    monkeypatch.setattr(flow, 'observed_parent', lambda event, actor: 'root' if actor == 'native-0' else None)
    for action in [['report'], ['report','--run',own['run']], ['advance','--phase','design'],
                   ['attach','--tasks','tasks.json','--update-context','--run',own['run']]]:
        assert flow.main([*action,'--workspace',str(tmp_path)]) == 2
        assert json.loads(capsys.readouterr().out)['reason'] == 'scope_violation'
    event = dict(host='codex', cwd=str(tmp_path), session_id='native-0', hook_event_name='PreToolUse',
                 tool_name='Write', tool_input={'path':'T0.md'})
    flow.hook(event)
    assert event['taskplane_observed_binding']['root'] == 'root'
    with pytest.raises(w.Refusal): flow.hook({**event,'tool_input':{'path':'own.md'}})
    c.observe(dict(hook_event_name='SubagentStop', agent_id='native-0'), s['run'])
    with pytest.raises(w.Refusal): flow.hook(event)
    assert flow.main(['report','--workspace',str(tmp_path)]) == 2
    capsys.readouterr()
