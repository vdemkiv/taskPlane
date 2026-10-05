"""Native adapter contracts; fixtures never count as live host execution."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from taskplane import workflow as w, workflow_host as h, worker_runtime as wr
from taskplane.context_handoff import Session, consume_required


def setup(tmp_path, count=4, scoped_input=False, extra_inputs=()):
    names = [f'T{i}' for i in range(count)]
    paths = [f'{name}.md' for name in [*names, 'DEP']]
    scope = {'criteria':['AC'], 'paths':{p:[p+'.json'] for p in w.PHASES}, 'verification_inputs':['input.py']}
    scope['paths']['product'] += paths
    if scoped_input:
        scope['paths']['product'] += ['input.py', *extra_inputs]
    scope['verification_inputs'] += list(extra_inputs)
    for relative in extra_inputs:
        (tmp_path/relative).write_text('unrelated = 1\n')
    (tmp_path/'input.py').write_text('value = 1\n')
    rows = [dict(id=name, phase='product', owner='planned-native/reviewer', paths=[name+'.md'],
                 dependencies=[], criteria=['AC'], verification='Inspect fixture input') for name in names]
    rows.append(dict(id='DEP', phase='product', owner='planned-native/verifier', paths=['DEP.md'],
                     dependencies=names, criteria=['AC'], verification='Verify predecessors'))
    (tmp_path/'tasks.json').write_text(json.dumps({'tasks':rows}))
    c = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
    state = c.start(dict(scope=scope, entry='product', standalone=True, tasks='tasks.json', request_reference='fixture/request'))
    return c, state


def parent_pair(c):
    from taskplane import flow
    import uuid
    event = dict(hook_event_name='PreToolUse', host=c.adapter.name,
                 cwd=str(c.workspace), session_id=c.root, call_id='fixture-parent-' + uuid.uuid4().hex,
                 tool_name='Read', tool_input={'file_path':'input.py'})
    flow._hook(event, governor=c)
    flow._hook({**event, 'hook_event_name':'PostToolUse'}, governor=c)


def reserve(c, s, task='T0', slots=5, **extra):
    parent_pair(c)
    return c.worker(s['run'], 'prepare', revision=s['revision'], task=task,
                    request={'capacity':dict(host_slots=slots, includes_root=True, reference='fixture/capacity'), **extra})


@pytest.mark.parametrize('defect', [None, 'wrong_root', 'wrong_target', 'running', 'old', 'duplicate', 'missing',
    'symlink', 'client_authored', 'foreign_tool', 'live_command', 'missing_reference', 'stale_revision', 'child',
    'null_call_metadata', 'list_result_metadata', 'missing_metadata', 'number_call_timestamp',
    'null_result_timestamp', 'missing_timestamp', 'invalid_timestamp', 'null_arguments', 'invalid_output', 'list_target'])
def test_unavailable_worker_recovery_requires_native_evidence_and_never_accepts(tmp_path, monkeypatch, capsys, defect):
    from datetime import datetime, timezone, timedelta
    c, s = setup(tmp_path, count=1)
    prepared = reserve(c, s); grant = prepared['grant']['grant_id']
    launch(c, s, prepared)
    event = dict(hook_event_name='PreToolUse', tool_name='collaboration.interrupt_agent',
                 call_id='native-missing', session_id='root', tool_input={'target':'native-0'})
    c.guard(event, s['run'])
    c.observe({**event, 'hook_event_name':'PostToolUse','tool_response':{'previous_status':'not_found'}}, s['run'])
    home = tmp_path.parent/(tmp_path.name+'-native-home'); folder=home/'sessions'; folder.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME',str(home))
    stamp = (datetime.now(timezone.utc)-timedelta(hours=2) if defect=='old' else datetime.now(timezone.utc)).isoformat()
    rows = [dict(type='session_meta',payload={'id':'other' if defect=='wrong_root' else 'root'}),
        dict(type='response_item',timestamp=stamp,metadata={'client_authored':defect=='client_authored'},
             payload=dict(type='function_call',name='interrupt_agent',namespace='other' if defect=='foreign_tool' else 'collaboration',
                          call_id='native-missing',arguments=json.dumps({'target':'other' if defect=='wrong_target' else 'native-0'}))),
        dict(type='response_item',timestamp=stamp,metadata={'client_authored':False},
             payload=dict(type='function_call_output',call_id='native-missing',output=json.dumps(
                 {'previous_status':'running' if defect=='running' else 'not_found'})))]
    malformed = {
        'null_call_metadata': (1, 'metadata', None), 'list_result_metadata': (2, 'metadata', []),
        'number_call_timestamp': (1, 'timestamp', 123), 'null_result_timestamp': (2, 'timestamp', None),
        'invalid_timestamp': (1, 'timestamp', 'not-a-timestamp'),
    }
    if defect in malformed:
        index, key, value = malformed[defect]; rows[index][key] = value
    if defect == 'missing_metadata': rows[1].pop('metadata')
    if defect == 'missing_timestamp': rows[2].pop('timestamp')
    if defect == 'null_arguments': rows[1]['payload']['arguments'] = None
    if defect == 'invalid_output': rows[2]['payload']['output'] = 'not-json'
    if defect == 'list_target': rows[1]['payload']['arguments'] = json.dumps({'target': []})
    path=folder/'rollout-root.jsonl'; path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    if defect=='duplicate':path.write_text(path.read_text()+json.dumps(rows[-1])+'\n')
    if defect=='missing':path.unlink()
    if defect=='symlink':
        actual=folder/'actual.log'; path.rename(actual); path.symlink_to(actual)
    if defect=='live_command':
        c.observe(dict(hook_event_name='PostToolUse',tool_name='exec_command',session_id='root',
                       tool_input={'cmd':'test'},tool_response={'session_id':1234}),s['run'])
    if defect=='child':c=h.Controller(tmp_path,'root',h.installed_adapter('codex'),principal='native-0')
    before=c._path().read_bytes()
    kwargs=dict(revision=s['revision']+int(defect=='stale_revision'),grant=grant,
                request={'request_reference':'' if defect=='missing_reference' else 'user/replace-interrupted-run',
                         'call_id':'native-missing'})
    if defect in {*malformed, 'missing_metadata', 'missing_timestamp', 'null_arguments', 'invalid_output', 'list_target'}:
        from taskplane import flow
        monkeypatch.setenv('CODEX_THREAD_ID', 'root')
        assert flow.main(['worker', '--workspace', str(tmp_path), '--run', s['run'],
                          '--operation', 'recover-unavailable', '--grant', grant,
                          '--expected-revision', str(s['revision']), '--worker-json', json.dumps(kwargs['request'])],
                         governor=c) == 2
        failure = json.loads(capsys.readouterr().out)
        assert failure['status'] == 'blocked' and failure['reason'] == 'invalid_evidence'
        assert c._path().read_bytes() == before
        assert c.report()['workers'][grant]['state'] == 'cancel_requested'
        assert not c.report().get('task_results')
    elif defect:
        with pytest.raises((w.Refusal,OSError,ValueError)):
            c.worker(s['run'],'recover-unavailable',**kwargs)
        assert c._path().read_bytes()==before
    else:
        row=c.worker(s['run'],'recover-unavailable',**kwargs)
        assert row['state']=='failed' and row['terminal_status']=='unavailable'
        assert row['recovery']['process_exit']=='unknown' and not row.get('ended_at')
        current=c.report()
        assert wr.joined(current) and not current.get('task_results')
        with pytest.raises(w.Refusal):
            c.worker(s['run'],'accept-result',revision=s['revision'],task='T0',grant=grant,
                     request={'outputs':['T0.md'],'checks':[]})


def test_bound_workers_require_independent_current_location_and_matching_contract(tmp_path,monkeypatch):
    from taskplane.tests.test_claude_flow import bound_workspace
    ws,contract=bound_workspace(tmp_path,monkeypatch,policy='local')
    c,s=setup(ws,count=1)
    monkeypatch.delenv('TASKPLANE_WORKER_REFERENCE')
    with pytest.raises(w.Refusal):reserve(c,s)
    assert not c.report().get('workers')
    monkeypatch.setenv('TASKPLANE_WORKER_REFERENCE','fixture/current-worker')
    prepared=reserve(c,s);row=prepared['grant']
    assert row['workspace_contract']=={k:contract[k] for k in ('project_id','digest')}
    launch(c,s,prepared,'native-local')
    worker=h.Controller(ws,c.root,h.installed_adapter('claude'),principal='native-local')
    monkeypatch.setenv('TASKPLANE_WORKER_LOCATION','remote')
    with pytest.raises(w.Refusal):worker.worker(s['run'],'claim',grant=row['grant_id'])
    monkeypatch.setenv('TASKPLANE_WORKER_LOCATION','local')
    worker.worker(s['run'],'claim',grant=row['grant_id'])
    changed=deepcopy(row);changed['workspace_contract']['digest']='0'*64
    with pytest.raises(w.Refusal,match='contract'):wr.current(s,changed)
    (ws/'host-proof.txt').write_text('changed after prepare')
    with pytest.raises(w.Refusal):worker.worker(s['run'],'claim',grant=row['grant_id'])


def test_bound_host_alias_write_preserves_exact_scope_and_symlink_checks(tmp_path,monkeypatch):
    from taskplane.tests.test_claude_flow import bound_workspace
    ws,_=bound_workspace(tmp_path,monkeypatch)
    c,s=setup(ws,count=1)
    def write(path):
        c.guard({'hook_event_name':'PreToolUse','tool_name':'Write','tool_input':{'file_path':path}},s['run'])
    write('/Users/fixture/farm-viewer/T0.md')
    write(str(ws/'T0.md'))
    for path in ('/Users/fixture/farm-viewer-neighbor/T0.md',
                 '/Users/fixture/farm-viewer/../T0.md','/Users/fixture/farm-viewer/unscoped.py'):
        with pytest.raises(w.Refusal):write(path)
    (ws/'T0.md').symlink_to(tmp_path/'outside')
    with pytest.raises(w.Refusal):write('/Users/fixture/farm-viewer/T0.md')


def launch(c, s, prepared, child='native-0'):
    row = prepared['grant']
    event = dict(hook_event_name='PreToolUse', session_id='root', call_id='call/'+row['grant_id'],
        tool_name='collaboration.spawn_agent', tool_input=dict(task_name=row['task_name'], message=prepared['message'], fork_turns='none'))
    c.guard(event, s['run'])
    c.observe({**event, 'hook_event_name':'PostToolUse', 'tool_response':{'agent_id':child, 'task_name':'/root/'+row['task_name']}}, s['run'])
    return event


def consume(c, s, row, child):
    worker = h.Controller(c.workspace, c.root, h.installed_adapter('codex'), principal=child)
    worker.worker(s['run'], 'claim', grant=row['grant_id'])
    context = worker.context(s['run'], task=row['task_id'])
    result = worker.context(s['run'], task=row['task_id'], consume=context['handoff_ref']['sha256'])
    while result['remaining_required']:
        result = worker.context(s['run'], task=row['task_id'], read_required=context['handoff_ref']['sha256'])
    return worker


def complete(c, s, prepared, child):
    c.observe(dict(hook_event_name='PostToolUse', session_id='root', call_id='status/'+child,
        tool_name='collaboration.list_agents', tool_input={}, tool_response={'agents':[{'agent_id':child,'status':'completed'}]}), s['run'])
    task = prepared['grant']['task_id']
    (c.workspace/(task+'.md')).write_text('Fixture review result\n')
    return c.worker(s['run'], 'accept-result', revision=s['revision'], task=task,
                    grant=prepared['grant']['grant_id'], request=dict(outputs=[task+'.md'],
                    checks=[dict(name='Fixture verification', status='pass', evidence=task+'.md')]))


def scoped(c, s, tmp_path, readiness=False, budget=131072):
    rows = deepcopy(s['initial_context_tasks'])
    for row in rows:
        row.update(execution='native_required', read_inputs=['input.py'], context_budget_bytes=budget, purpose='Focused fixture review')
    rows[1]['read_inputs'] = ['other.py']
    if readiness:
        rows[1]['readiness_after'] = ['T0']
    (tmp_path/'.taskplane/scoped-tasks.json').write_text(json.dumps({'tasks': rows}))
    return c.update_tasks(s['run'], s['revision'], '.taskplane/scoped-tasks.json')


def test_scoped_inputs_preserve_unaffected_native_results(tmp_path):
    c, s = setup(tmp_path, count=2, scoped_input=True, extra_inputs=['other.py'])
    s = scoped(c, s, tmp_path)
    a = reserve(c, s, 'T0')
    launch(c, s, a, 'native-a'); consume(c, s, a['grant'], 'native-a')
    event = dict(session_id='native-a', call_id='ready', tool_name='exec_command',
        taskplane_observed_binding={'root':'root','principal':'native-a'},
        taskplane_runtime_identity=a['grant']['expected_runtime'])
    for hook in ('PreToolUse', 'PostToolUse'):
        c.observe({**event, 'hook_event_name': hook}, s['run'])
    b = reserve(c, s, 'T1')
    assert set(a['grant']['input_manifest']) == {'input.py'}
    assert set(b['grant']['input_manifest']) == {'other.py'}
    launch(c, s, b, 'native-b'); consume(c, s, b['grant'], 'native-b')
    for item, child in [(a, 'native-a'), (b, 'native-b')]:
        complete(c, s, item, child)
    state = c.report()
    (tmp_path/'input.py').write_text('value = 2\n')
    assert not wr.result_valid(tmp_path, state, 'T0')
    assert wr.result_valid(tmp_path, state, 'T1')
    state['revision'] += 1
    assert not wr.result_valid(tmp_path, state, 'T1')  # No receipt/authority transfer.


@pytest.mark.parametrize('fork', [None, 'all', '1'])
def test_spawn_requires_explicit_no_history(tmp_path, fork):
    c, s = setup(tmp_path)
    item = reserve(c, s)
    args = dict(task_name=item['grant']['task_name'], message=item['message'])
    if fork is not None:
        args['fork_turns'] = fork
    with pytest.raises(w.Refusal, match='task-focused'):
        c.guard(dict(hook_event_name='PreToolUse', session_id='root', call_id='bad-fork',
                     tool_name='collaboration.spawn_agent', tool_input=args), s['run'])
    assert c.report()['workers'][item['grant']['grant_id']]['state'] == 'prepared'


@pytest.mark.parametrize('explicit', [False, True])
def test_cohort_requires_claim_context_and_matching_automatic_pair(tmp_path, explicit):
    c, s = setup(tmp_path, count=2, extra_inputs=['other.py'])
    s = scoped(c, s, tmp_path, readiness=explicit)
    if explicit:
        with pytest.raises(w.Refusal, match='startup'):
            reserve(c, s, 'T1')
    a = reserve(c, s, 'T0'); launch(c, s, a, 'native-a')
    consume(c, s, a['grant'], 'native-a')
    with pytest.raises(w.Refusal, match='startup'):
        reserve(c, s, 'T1')
    event = dict(hook_event_name='PreToolUse', session_id='native-a', call_id='child-context',
        tool_name='exec_command', taskplane_observed_binding={'root':'root','principal':'native-a'},
        taskplane_runtime_identity=a['grant']['expected_runtime'])
    c.observe(event, s['run'])
    with pytest.raises(w.Refusal, match='startup'):
        reserve(c, s, 'T1')  # Pre alone is never readiness.
    c.observe({**event, 'hook_event_name':'PostToolUse'}, s['run'])
    b = reserve(c, s, 'T1'); launch(c, s, b, 'native-b')
    status = c.worker(s['run'], 'status')
    assert status['live'] == 2 and status['counts']['launched'] == 2
    assert next(row for row in status['attempts'] if row['task_id'] == 'T0')['readiness']['status'] == 'ready'
    assert status['context_cost']['returned_bytes'] > 0
    c.observe({**event, 'taskplane_runtime_identity':{**a['grant']['expected_runtime'], 'root':'/stale/runtime'}}, s['run'])
    assert wr.readiness(c.report()['workers'][a['grant']['grant_id']])['status'] == 'pending'


def test_scoped_retries_need_a_concrete_reason(tmp_path):
    c, s = setup(tmp_path, count=2, extra_inputs=['other.py'])
    s = scoped(c, s, tmp_path)
    first = reserve(c, s)
    c.worker(s['run'], 'abandon', revision=s['revision'], grant=first['grant']['grant_id'])
    with pytest.raises(w.Refusal, match='retry_reason'):
        reserve(c, s)
    second = reserve(c, s, retry_reason='Reservation abandoned before launch to correct the dispatch payload.')
    assert second['grant']['attempt'] == 2
    counts = c.worker(s['run'], 'status')['counts']
    assert counts == {'tasks':1,'reserved':2,'launched':0,'retries':1,'failed':1}


def test_context_budget_refuses_before_reserving_or_launching(tmp_path):
    c, s = setup(tmp_path, count=2, extra_inputs=['other.py'])
    s = scoped(c, s, tmp_path, budget=16384)
    parent_pair(c)
    s = c.report(s['run'])
    s['goal'] = 'Required normative goal. ' * 5000
    before = deepcopy(s.get('workers', {}))
    with pytest.raises(w.Refusal, match='before launch'):
        wr.prepare(tmp_path, s, 'T0', {'capacity':{'host_slots':3,'includes_root':True,'reference':'fixture'}})
    assert s.get('workers', {}) == before


def test_missing_declared_input_cannot_disappear_from_freshness(tmp_path):
    c, s = setup(tmp_path, count=2, scoped_input=True, extra_inputs=['other.py'])
    s = scoped(c, s, tmp_path)
    (tmp_path/'input.py').unlink()
    with pytest.raises(w.Refusal):
        reserve(c, s, 'T0')
    assert not c.report().get('workers')


@pytest.mark.parametrize('field,value', [
    ('read_inputs', 'input.py'), ('read_inputs', ['input.py','input.py']),
    ('read_inputs', ['../foreign.py']), ('read_inputs', ['undeclared.py']),
    ('readiness_after', ['T0']), ('context_budget_bytes', True),
])
def test_invalid_scoped_task_contract_refuses_publication(tmp_path, field, value):
    c, s = setup(tmp_path)
    rows = deepcopy(s['initial_context_tasks'])
    rows[0][field] = value
    (tmp_path/'.taskplane/scoped-tasks.json').write_text(json.dumps({'tasks': rows}))
    with pytest.raises(w.Refusal):
        c.update_tasks(s['run'], s['revision'], '.taskplane/scoped-tasks.json')


def test_four_concurrent_reservations_and_dependent_acceptance(tmp_path):
    c, s = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        prepared = list(pool.map(lambda i: reserve(c, s, 'T'+str(i)), range(4)))
    assert c.worker(s['run'], 'status')['pending'] == 4
    assert c.report()['revision'] == s['revision']
    with pytest.raises(w.Refusal, match='prerequisites'): reserve(c, s, 'DEP')
    for i, item in enumerate(prepared):
        launch(c, s, item, f'native-{i}')
        consume(c, s, item['grant'], f'native-{i}')
    assert not c.adapter.can_seal(c.report())
    for i, item in enumerate(prepared): complete(c, s, item, f'native-{i}')
    assert c.adapter.can_seal(c.report())
    assert reserve(c, s, 'DEP')['grant']['state'] == 'prepared'
    assert c.worker(s['run'], 'status')['default_limit'] is None


@pytest.mark.parametrize('reader_first', [True, False])
def test_replacement_writer_and_prerequisite_reader_cannot_overlap(tmp_path, reader_first):
    c, s = setup(tmp_path, count=1)
    first = reserve(c, s); launch(c, s, first); consume(c, s, first['grant'], 'native-0')
    complete(c, s, first, 'native-0')
    active_task, blocked_task = ('DEP','T0') if reader_first else ('T0','DEP')
    active = reserve(c, s, active_task)
    launch(c, s, active, 'native-1'); consume(c, s, active['grant'], 'native-1')
    with pytest.raises(w.Refusal, match='read inputs|active writer'):
        reserve(c, s, blocked_task)
    if not reader_first:
        reasons = c.worker(s['run'], 'status')['scheduling']
        assert next(r['reason'] for r in reasons if r['task'] == 'DEP') == 'read/write conflict'
    c.observe({'hook_event_name':'SubagentStop','agent_id':'native-1'}, s['run'])
    assert reserve(c, s, blocked_task)['grant']['state'] == 'prepared'


def test_explicit_source_reader_conflict_and_launch_recheck(tmp_path):
    c, s = setup(tmp_path, count=2, scoped_input=True)
    tasks = json.loads((tmp_path/'tasks.json').read_text())
    tasks['tasks'][0]['paths'] = ['input.py']
    (tmp_path/'.taskplane/tasks-revised.json').write_text(json.dumps(tasks))
    s = c.update_tasks(s['run'], s['revision'], '.taskplane/tasks-revised.json')
    reader = reserve(c, s, 'T1')
    with pytest.raises(w.Refusal, match='read inputs'):
        reserve(c, s, 'T0')
    # Model an old persisted pair admitted before the conflict check existed.
    state = c.report(); legacy_writer = deepcopy(reader['grant'])
    legacy_writer.update(grant_id='legacy', task_id='T0', paths=['input.py'], input_manifest={})
    state['workers']['legacy'] = legacy_writer
    row = reader['grant']
    with pytest.raises(w.Refusal, match='read/write ownership'):
        wr.admit(state, dict(tool_name='spawn_agent', call_id='new-launch',
            tool_input=dict(task_name=row['task_name'], message=reader['message'], fork_turns='none')))


def test_worker_context_paths_root_exemptions_and_identity(tmp_path):
    c, s = setup(tmp_path)
    prepared = reserve(c, s)
    launch(c, s, prepared)
    worker = h.Controller(c.workspace, 'root', h.installed_adapter('codex'), principal='native-0')
    write = dict(tool_name='Write', tool_input={'path':'T0.md'})
    with pytest.raises(w.Refusal, match='consume'): worker.guard(write, s['run'])
    root = Session(c.workspace, c.report(), 'T0')
    receipt, _ = consume_required(root)
    row = c.report()['workers'][prepared['grant']['grant_id']]
    with pytest.raises(w.Refusal): wr.worker_session(c.workspace, c.report(), row).validate(receipt)
    worker = consume(c, s, prepared['grant'], 'native-0')
    worker.guard(write, s['run'])
    worker.guard(dict(tool_name='collaborationsend_message',tool_input={'target':'/root','message':'Bound review progress'}), s['run'])
    with pytest.raises(w.Refusal,match='bound parent'):
        worker.guard(dict(tool_name='collaborationsend_message',tool_input={'target':'unrelated','message':'wrong target'}), s['run'])
    (tmp_path/'T0.md').write_text('edit one')
    worker.guard(write, s['run'])
    with pytest.raises(w.Refusal, match='outside'): worker.guard({**write, 'tool_input':{'path':'T1.md'}}, s['run'])
    with pytest.raises(w.Refusal): worker.apply('advance', s['run'], expected_revision=s['revision'])
    with pytest.raises(w.Refusal): worker.start({})
    with pytest.raises(w.Refusal): worker.update_tasks(s['run'], s['revision'], 'tasks.json')
    with pytest.raises(w.Refusal): worker.context(s['run'], task='T1')
    with pytest.raises(w.Refusal): worker.guard(dict(tool_name='collaboration.list_agents',tool_input={}),s['run'])
    stranger = h.Controller(tmp_path,'root',h.installed_adapter('codex'),principal='stranger')
    with pytest.raises(w.Refusal): stranger.worker(s['run'],'claim',grant=row['grant_id'])


def test_pending_unknown_cancel_and_wait_never_prove_completion(tmp_path):
    c, s = setup(tmp_path)
    item = reserve(c, s)
    event = launch(c, s, item)
    c.guard(dict(tool_name='collaboration.interrupt_agent',tool_input={'target':'native-0'}),s['run'])
    c.observe(dict(hook_event_name='PostToolUse',tool_name='collaboration.interrupt_agent',tool_response={'status':'running'}),s['run'])
    c.observe(dict(hook_event_name='PostToolUse',tool_name='collaboration.wait_agent',tool_response={'status':'timeout'}),s['run'])
    assert not c.adapter.can_seal(c.report())
    c.observe(dict(hook_event_name='PostToolUse',tool_name='collaboration.list_agents',tool_response={'agents':[{'agent_id':'native-0','status':'interrupted'}]}),s['run'])
    assert c.adapter.can_seal(c.report())
    with pytest.raises(w.Refusal): complete(c,s,item,'native-0')
    assert not c.adapter.can_seal(c.report())  # contradictory terminal observations
    event['tool_input']['message'] += '\nChanged request'
    with pytest.raises(w.Refusal, match='replay'): c.guard(event,s['run'])


def test_atomic_capacity_and_failed_store_never_admit(tmp_path, monkeypatch):
    c, s = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        def attempt(i):
            try: return reserve(c,s,'T'+str(i),slots=3)
            except w.Refusal: return None
        result = list(pool.map(attempt, range(4)))
    assert sum(r is not None for r in result) == 2
    assert c.worker(s['run'],'status')['pending'] == 2
    before = deepcopy(c.report()['workers'])
    def fail(*_): raise w.Refusal('state_unavailable','simulated durable storage failure')
    monkeypatch.setattr(c,'_write',fail)
    unreserved = next('T'+str(i) for i,r in enumerate(result) if r is None)
    with pytest.raises(w.Refusal, match='storage'): reserve(c,s,unreserved,slots=6)
    assert c.report()['workers'] == before


def test_read_input_drift_invalidates_result_and_startup_needs_call_correlation(tmp_path):
    c, s = setup(tmp_path)
    item = reserve(c,s)
    row = item['grant']
    event=dict(hook_event_name='PreToolUse',tool_name='collaboration.spawn_agent',call_id='launch',
               tool_input={'task_name':row['task_name'],'message':item['message'],'fork_turns':'none'})
    c.guard(event,s['run'])
    c.observe(dict(hook_event_name='SessionStart',parent_session_id='root',session_id='child'),s['run'])
    assert c.report()['workers'][row['grant_id']]['worker_id'] is None
    c.observe(dict(hook_event_name='SessionStart',parent_session_id='root',parent_tool_call_id='launch',session_id='child'),s['run'])
    consume(c,s,row,'child')
    (tmp_path/'input.py').write_text('changed = 1\n')
    with pytest.raises(w.Refusal): complete(c,s,item,'child')


@pytest.mark.parametrize('identity', ['foreign-child', None])
def test_stop_requires_matching_identity_before_join(tmp_path, identity):
    c, s = setup(tmp_path)
    item = reserve(c, s)
    event = launch(c, s, item)
    consume(c, s, item['grant'], 'native-0')
    c.observe(dict(hook_event_name='SubagentStop', call_id=event['call_id'], agent_id=identity), s['run'])
    assert c.report()['workers'][item['grant']['grant_id']]['state'] == 'unknown'
    assert not c.adapter.can_seal(c.report())
    with pytest.raises(w.Refusal, match='joined'):
        c.worker(s['run'], 'accept-result', revision=s['revision'], task='T0', grant=item['grant']['grant_id'], request={})
    c.observe(dict(hook_event_name='SubagentStop', call_id=event['call_id'], agent_id='native-0'), s['run'])
    assert c.report()['workers'][item['grant']['grant_id']]['state'] == 'result_pending'


def test_stop_before_launch_result_still_needs_real_identity(tmp_path):
    c, s = setup(tmp_path)
    item = reserve(c, s)
    row = item['grant']
    c.guard(dict(tool_name='spawn_agent',call_id='spawn',tool_input=dict(task_name=row['task_name'],message=item['message'],fork_turns='none')), s['run'])
    c.observe(dict(hook_event_name='SubagentStop',call_id='spawn'),s['run'])
    assert not c.adapter.can_seal(c.report())
    c.observe(dict(hook_event_name='SubagentStop',call_id='spawn',agent_id='native-0'),s['run'])
    assert c.adapter.can_seal(c.report())


def root_prerequisite(c, s):
    tasks = json.loads((c.workspace/'tasks.json').read_text())
    tasks['tasks'][0]['owner'] = 'root'
    tasks['tasks'][1]['dependencies'] = ['T0']
    (c.workspace/'.taskplane/review-tasks.json').write_text(json.dumps(tasks))
    s = c.update_tasks(s['run'], s['revision'], '.taskplane/review-tasks.json')
    (c.workspace/'T0.md').write_text('version A')
    request = dict(outputs=['T0.md'],checks=[dict(name='inspect prerequisite',status='pass',evidence='T0.md')])
    c.worker(s['run'],'accept-result',revision=s['revision'],task='T0',request=request)
    return s, request


def test_prerequisite_replacement_requires_join_and_fresh_attempt(tmp_path):
    c,s = setup(tmp_path)
    s,request = root_prerequisite(c,s)
    item = reserve(c,s,'T1')
    frozen = wr.Store(c.workspace).resolve(item['grant']['snapshot'])
    assert any(i['kind']=='prerequisite-artifact' and i['body']['text']=='version A' for i in frozen['inputs'][0])
    launch(c,s,item)
    consume(c,s,item['grant'],'native-0')
    with pytest.raises(w.Refusal,match='outside'):
        c.guard(dict(tool_name='Write',tool_input={'path':'T0.md'}),s['run'])
    with pytest.raises(w.Refusal,match='Join dependent'):
        c.worker(s['run'],'accept-result',revision=s['revision'],task='T0',request=request)
    c.observe(dict(hook_event_name='SubagentStop',agent_id='native-0'),s['run'])
    (tmp_path/'T0.md').write_text('version B')
    c.worker(s['run'],'accept-result',revision=s['revision'],task='T0',request=request)
    with pytest.raises(w.Refusal,match='prerequisite results changed'):
        complete(c,s,item,'native-0')


def test_reaccepted_prerequisite_invalidates_prepared_grant(tmp_path):
    c,s=setup(tmp_path)
    s,request=root_prerequisite(c,s)
    item=reserve(c,s,'T1')
    # Pure admission probe models independently replaced durable prerequisite.
    state=c.report(); state['task_results']['T0']['accepted_at']='new acceptance'
    row=item['grant']
    with pytest.raises(w.Refusal,match='prerequisites changed'):
        wr.admit(state,dict(tool_name='spawn_agent',call_id='spawn',tool_input=dict(task_name=row['task_name'],message=item['message'],fork_turns='none')))


def test_accepted_read_inputs_stay_fresh_transitively(tmp_path):
    c,s=setup(tmp_path,count=1,scoped_input=True)
    item=reserve(c,s); launch(c,s,item); consume(c,s,item['grant'],'native-0')
    result=complete(c,s,item,'native-0')
    assert result['input_manifest'] == item['grant']['input_manifest']
    assert wr.result_valid(tmp_path,c.report(),'T0')
    c.guard(dict(tool_name='Write',tool_input={'path':'input.py'}),s['run'])
    (tmp_path/'input.py').write_text('changed after acceptance')
    assert not wr.result_valid(tmp_path,c.report(),'T0')
    with pytest.raises(w.Refusal,match='prerequisites'): reserve(c,s,'DEP')


@pytest.mark.parametrize('operation', ['prepare', 'consume', 'read', 'read_required'])
@pytest.mark.parametrize('transition,expected', [('stop', 'result_pending'), ('cancel', 'cancel_requested'), ('failed', 'failed')])
def test_locked_context_never_revives_observed_attempt(tmp_path, monkeypatch, operation, transition, expected):
    c, s = setup(tmp_path, count=1)
    item = reserve(c, s); launch(c, s, item)
    worker = consume(c, s, item['grant'], 'native-0')
    descriptor = worker.context(s['run'], task='T0')
    original_report = worker.report
    before = c.report()['workers'][item['grant']['grant_id']]['context_receipt']
    def interleave(run=None, **kwargs):
        snapshot = original_report(run, **kwargs)
        if transition == 'stop':
            c.observe(dict(hook_event_name='SubagentStop', agent_id='native-0', event_id='race-stop'), s['run'])
        elif transition == 'cancel':
            c.guard(dict(tool_name='interrupt_agent', tool_input={'target':'native-0'}), s['run'])
        else:
            c.observe(dict(hook_event_name='PostToolUse', tool_name='list_agents', call_id='race-failed',
                tool_response={'agents':[{'agent_id':'native-0','status':'failed'}]}), s['run'])
        return snapshot
    monkeypatch.setattr(worker, 'report', interleave)
    options = {} if operation == 'prepare' else {operation: descriptor['view_ref' if operation == 'read' else 'handoff_ref']['sha256']}
    with pytest.raises(w.Refusal, match='not live'):
        worker.context(s['run'], task='T0', **options)
    row = c.report()['workers'][item['grant']['grant_id']]
    assert row['state'] == expected and row['context_receipt'] == before
    with pytest.raises(w.Refusal):
        worker.guard(dict(tool_name='Write', tool_input={'path':'T0.md'}), s['run'])
    if transition == 'stop':
        c.observe(dict(hook_event_name='SubagentStop', agent_id='native-0', event_id='race-stop'), s['run'])
        assert c.report()['workers'][item['grant']['grant_id']]['state'] == expected


def test_explicit_native_execution_refuses_grantless_root_result(tmp_path):
    c, s = setup(tmp_path, count=1)
    rows = json.loads((tmp_path/'tasks.json').read_text())
    rows['tasks'][0].update(owner='root', execution='native_required')
    (tmp_path/'.taskplane/typed-tasks.json').write_text(json.dumps(rows))
    s = c.update_tasks(s['run'], s['revision'], '.taskplane/typed-tasks.json')
    (tmp_path/'T0.md').write_text('Root inspection is not independent native work')
    with pytest.raises(w.Refusal, match='native|Native'):
        c.worker(s['run'], 'accept-result', revision=s['revision'], task='T0', request=dict(
            outputs=['T0.md'], checks=[dict(name='inspect',status='pass',evidence='T0.md')]))


@pytest.mark.parametrize('relative', ['config.json', '.taskplane/hidden-input.json'])
@pytest.mark.parametrize('native', [True, False])
def test_suppressed_fallback_reads_pin_native_and_root_results(tmp_path, relative, native):
    if relative.startswith('.taskplane/'):
        (tmp_path/'.taskplane').mkdir()
    c, s = setup(tmp_path, count=1, scoped_input=True, extra_inputs=[relative])
    (tmp_path/relative).write_text('{"tasks":["first"]}')
    if native:
        item = reserve(c, s)
        assert relative in item['grant']['input_manifest']
        launch(c, s, item); consume(c, s, item['grant'], 'native-0')
        result = complete(c, s, item, 'native-0')
    else:
        s, _ = root_prerequisite(c, s)
        result = c.report()['task_results']['T0']
    assert relative in result['input_manifest']
    assert 'T0.md' not in result['input_manifest']
    assert wr.result_valid(tmp_path, c.report(), 'T0')
    (tmp_path/relative).write_text('{"tasks":["changed"]}')
    assert not wr.result_valid(tmp_path, c.report(), 'T0')


@pytest.mark.parametrize('relative', ['input.py', '.taskplane/hidden-input.json'])
@pytest.mark.parametrize('native', [True, False])
def test_missing_fallback_read_refuses_preparation_or_root_acceptance(tmp_path, relative, native):
    extra = [] if relative == 'input.py' else [relative]
    if extra:
        (tmp_path/'.taskplane').mkdir()
    c, s = setup(tmp_path, count=1, scoped_input=True, extra_inputs=extra)
    if not native:
        s, request = root_prerequisite(c, s)
    (tmp_path/relative).unlink()
    with pytest.raises(w.Refusal, match='unavailable'):
        if native:
            reserve(c, s)
        else:
            c.worker(s['run'], 'accept-result', revision=s['revision'], task='T0', request=request)
    if native:
        assert not c.report().get('workers')


def test_owned_fallback_path_does_not_become_its_own_dependency(tmp_path):
    c, s = setup(tmp_path, count=1)
    state = deepcopy(s)
    state['scope']['verification_inputs'].append('T0.md')
    session = Session(tmp_path, state, 'T0')
    manifest = wr.read_input_manifest(tmp_path, session, ['T0.md'])
    assert set(manifest) == {'input.py'}


@pytest.mark.parametrize('native', [True, False])
def test_legacy_incomplete_read_manifests_require_fresh_verification(tmp_path, native):
    c, s = setup(tmp_path, count=1, scoped_input=True, extra_inputs=['config.json'])
    (tmp_path/'config.json').write_text('{"tasks":["first"]}')
    if native:
        item = reserve(c, s)
        launch(c, s, item); consume(c, s, item['grant'], 'native-0')
        complete(c, s, item, 'native-0')
    else:
        s, _ = root_prerequisite(c, s)
    state = c.report()
    assert wr.result_valid(tmp_path, state, 'T0')
    # Model an accepted pre-fix manifest consistently stored on both records.
    result = state['task_results']['T0']
    result['input_manifest'].pop('config.json')
    if native:
        state['workers'][result['grant']]['input_manifest'].pop('config.json')
    assert not wr.result_valid(tmp_path, state, 'T0')
