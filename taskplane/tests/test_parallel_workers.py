"""Capacity and writer conflicts are admission contracts, not live timing proof."""
import pytest
from taskplane import worker_runtime as wr, workflow as w
from taskplane.tests.test_worker_runtime import setup, reserve, launch, consume


def test_explicit_limits_above_two_and_root_slot_accounting():
    assert wr.capacity(dict(host_slots=13, includes_root=True,reference='observed host'))==12
    assert wr.capacity(dict(host_slots=8,includes_root=False,resource_limit=5,reference='observed host'))==5
    assert wr.capacity(dict(host_slots=8,includes_root=True,configured_limit=3,reference='observed host'))==3
    with pytest.raises(w.Refusal):wr.capacity(None)


def test_live_worker_and_child_command_both_join_before_result(tmp_path):
    c,s=setup(tmp_path);item=reserve(c,s);row=item['grant'];launch(c,s,item)
    consume(c,s,row,'native-0')
    c.observe({'hook_event_name':'PostToolUse','session_id':'native-0','tool_name':'exec_command',
               'tool_input':{},'tool_response':{'session_id':12}},s['run'])
    c.observe({'hook_event_name':'SubagentStop','agent_id':'native-0'},s['run'])
    assert not c.adapter.can_seal(c.report())
    (tmp_path/'T0.md').write_text('fixture')
    request=dict(outputs=['T0.md'],checks=[dict(name='fixture',status='pass',evidence='T0.md')])
    with pytest.raises(w.Refusal,match='commands'):c.worker(s['run'],'accept-result',task='T0',grant=row['grant_id'],revision=s['revision'],request=request)
    c.observe({'hook_event_name':'PostToolUse','session_id':'native-0','tool_name':'write_stdin',
               'tool_input':{'session_id':12},'tool_response':{'session_id':12,'exit_code':0}},s['run'])
    assert c.adapter.can_seal(c.report())
    c.worker(s['run'],'accept-result',task='T0',grant=row['grant_id'],revision=s['revision'],request=request)
    c.observe({'hook_event_name':'SubagentStop','agent_id':'native-0'},s['run'])
    assert c.report()['workers'][row['grant_id']]['state']=='accepted'


def test_capacity_reduction_blocks_launch_and_root_cannot_take_reserved_output(tmp_path):
    c,s=setup(tmp_path)
    items=[reserve(c,s,'T'+str(i)) for i in range(3)]
    c.worker(s['run'],'capacity',revision=s['revision'],request={
        'capacity':dict(host_slots=2,includes_root=False,reference='observed reduction')})
    with pytest.raises(w.Refusal,match='capacity reduced'):launch(c,s,items[0])
    with pytest.raises(w.Refusal,match='outside'):
        c.guard({'tool_name':'Write','tool_input':{'path':'T0.md'}},s['run'])
    c.worker(s['run'],'abandon',revision=s['revision'],grant=items[-1]['grant']['grant_id'])
    launch(c,s,items[0])
    assert c.worker(s['run'],'status')['live']==1
    c.guard({'tool_name':'Write','tool_input':{'path':'T2.md'}},s['run'])


def test_root_prerequisite_fingerprints_are_rechecked_before_native_launch(tmp_path):
    c,s=setup(tmp_path)
    import json
    rows=json.loads((tmp_path/'tasks.json').read_text())
    rows['tasks'][0]['owner']='root'
    rows['tasks'][1]['dependencies']=['T0']
    (tmp_path/'.taskplane/root-tasks.json').write_text(json.dumps(rows))
    s=c.update_tasks(s['run'],s['revision'],'.taskplane/root-tasks.json')
    (tmp_path/'T0.md').write_text('verified prerequisite')
    request=dict(outputs=['T0.md'],checks=[dict(name='inspection',status='pass',evidence='T0.md')])
    c.worker(s['run'],'accept-result',revision=s['revision'],task='T0',request=request)
    assert c.report()['task_results']['T0']['worker_id'] is None
    item=reserve(c,s,'T1')
    (tmp_path/'T0.md').write_text('changed after verification')
    with pytest.raises(w.Refusal,match='prerequisites changed'):launch(c,s,item)
