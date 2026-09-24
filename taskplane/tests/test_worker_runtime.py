"""Native adapter contracts; fixtures never count as live host execution."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from taskplane import workflow as w, workflow_host as h, worker_runtime as wr
from taskplane.context_handoff import Session, consume_required


def setup(tmp_path, count=4, scoped_input=False):
    names = [f'T{i}' for i in range(count)]
    paths = [f'{name}.md' for name in [*names, 'DEP']]
    scope = {'criteria':['AC'], 'paths':{p:[p+'.json'] for p in w.PHASES}, 'verification_inputs':['input.py']}
    scope['paths']['product'] += paths
    if scoped_input:
        scope['paths']['product'].append('input.py')
    (tmp_path/'input.py').write_text('value = 1\n')
    rows = [dict(id=name, phase='product', owner='planned-native/reviewer', paths=[name+'.md'],
                 dependencies=[], criteria=['AC'], verification='Inspect fixture input') for name in names]
    rows.append(dict(id='DEP', phase='product', owner='planned-native/verifier', paths=['DEP.md'],
                     dependencies=names, criteria=['AC'], verification='Verify predecessors'))
    (tmp_path/'tasks.json').write_text(json.dumps({'tasks':rows}))
    c = h.Controller(tmp_path, 'root', h.installed_adapter('codex'))
    state = c.start(dict(scope=scope, entry='product', standalone=True, tasks='tasks.json', request_reference='fixture/request'))
    return c, state


def reserve(c, s, task='T0', slots=5, **extra):
    return c.worker(s['run'], 'prepare', revision=s['revision'], task=task,
                    request={'capacity':dict(host_slots=slots, includes_root=True, reference='fixture/capacity'), **extra})


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
    c.guard(dict(tool_name='spawn_agent',call_id='spawn',tool_input=dict(task_name=row['task_name'],message=item['message'])), s['run'])
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
        wr.admit(state,dict(tool_name='spawn_agent',call_id='spawn',tool_input=dict(task_name=row['task_name'],message=item['message'])))


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
