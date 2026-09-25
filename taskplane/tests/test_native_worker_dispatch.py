"""Historical native shapes: a canonical name alone is not a session ID."""
import json
from pathlib import Path
import sys
import shlex

import pytest

from taskplane import worker_runtime as wr, workflow as w
from taskplane.tests.test_worker_runtime import setup, reserve, launch, consume


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
    metadata(logs/'native-0.jsonl', s, item['grant'], 'native-0')
    flow.append(workspace, dict(kind='start', run=s['run'], session='root', phase='product'))
    event = dict(host='codex', cwd=str(workspace), session_id='root', agent_id='native-0',
                 agent_type='default', hook_event_name='PreToolUse', tool_name='Write',
                 call_id='before-context', tool_input={'path':'T0.md'})
    with pytest.raises(w.Refusal, match='consume'): flow.hook(event)
    consume(c, s, item['grant'], 'native-0')
    flow.hook({**event, 'call_id':'own-write'})
    with pytest.raises(w.Refusal, match='outside'):
        flow.hook({**event, 'call_id':'sibling-write', 'tool_input':{'path':'T1.md'}})
    flow.hook({**event, 'call_id':'parent-progress', 'tool_name':'collaborationsend_message',
               'tool_input':{'target':'/root', 'message':'Scoped progress'}})
    flow.hook({**event, 'hook_event_name':'PostToolUse', 'call_id':'own-write'})
    rows = [r for r in flow.read_events(workspace) if r.get('kind') == 'hook']
    assert all(r['session'] == 'native-0' for r in rows)
    assert all(r['binding_observation']['principal'] == 'native-0' for r in rows)
    assert next(r for r in rows if r['call_id'] == 'sibling-write')['outcome'] == 'denied'
    assert next(r for r in rows if r['call_id'] == 'parent-progress')['outcome'] == 'admitted'
    root_event = {k:v for k,v in event.items() if k not in {'agent_id','agent_type'}}
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


def test_claude_start_stop_contract_and_child_control_refusal(tmp_path):
    c,s=setup(tmp_path);item=reserve(c,s);row=item['grant']
    event={'hook_event_name':'PreToolUse','tool_name':'Agent','tool_use_id':'claude-call',
           'tool_input':{'prompt':item['message'],'description':'bounded review','subagent_type':'general-purpose'}}
    c.guard(event,s['run'])
    c.observe({'hook_event_name':'SubagentStart','tool_use_id':'claude-call','agent_id':'claude-child'},s['run'])
    worker=consume(c,s,row,'claude-child')
    engine=Path(w.__file__).with_name('tp.py')
    for action in ['start','submit','decide','policy','advance','attach','retire','present']:
        command=shlex.join([sys.executable,str(engine),'flow',action,'--workspace',str(tmp_path)])
        with pytest.raises(w.Refusal):worker.guard({'tool_name':'exec_command','tool_input':{'cmd':command}},s['run'])
    c.observe({'hook_event_name':'SubagentStop','agent_id':'claude-child'},s['run'])
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
