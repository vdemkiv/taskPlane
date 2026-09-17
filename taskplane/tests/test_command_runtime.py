from copy import deepcopy
import json
import pytest
from taskplane import command_runtime as c, workflow as w


class Processes:
    def __init__(self):self.records={};self.complete=True;self.extra=[];self.cancelled=[]
    def read_process(self,handle):return deepcopy(self.records[handle])
    def process_census(self,root,run):
        return {'root':root,'run':run,'complete':self.complete,'active_handles':self.extra+[h for h,r in self.records.items() if r['binding']['run']==run and (r['state'] not in c.TERMINAL or not r['tree_quiescent'])]}
    def cancel_process(self,handle,binding):self.cancelled.append(handle)


def runtime(tmp_path):
    ws=tmp_path/'workspace';ws.mkdir()
    store=tmp_path/'commands.json';store.write_text(json.dumps({'schema':'taskplane.native-commands/v1','workspace':str(ws),'root':'root','handles':{}}))
    owner=Processes();binding={'workspace':str(ws),'root':'root','run':'run','visit':'visit','revision':2}
    owner.records['handle']={'handle':'handle','binding':binding,'process_start':'native-start-1','state':'running','tree_quiescent':False,'output':'PRIVATE'}
    rt=c.CommandRuntime(store,workspace=ws,root='root',observer=owner)
    return rt,owner,binding


def test_bound_handle_interrupt_reconnect_and_terminal_replay(tmp_path):
    rt,owner,binding=runtime(tmp_path)
    r=rt.create('handle',binding);assert r['revision']==0
    assert rt.reconnect('handle',binding)==r
    for wrong in [binding|{'revision':3},binding|{'visit':'other'}]:
        with pytest.raises(w.Refusal):rt.reconnect('handle',wrong)
    owner.records['handle']['state']='input_required'
    with pytest.raises(w.Refusal):rt.reconnect('handle',binding)
    owner.records['handle'].update(state='completed',tree_quiescent=True)
    r=rt.snapshot('handle');assert rt.snapshot('handle')==r
    assert rt.quiescent('run')
    assert 'PRIVATE' not in rt.path.read_text()
    owner.records['handle']['state']='running'
    with pytest.raises(w.Refusal):rt.snapshot('handle')


def test_cancel_revokes_input_but_does_not_manufacture_exit(tmp_path):
    rt,owner,binding=runtime(tmp_path);r=rt.create('handle',binding)
    result=rt.cancel('handle',expected_revision=r['revision'])
    assert result['revoked'] and result['state']=='running' and not rt.quiescent('run')
    with pytest.raises(w.Refusal):rt.reconnect('handle',binding)
    owner.records['handle'].update(state='cancelled',tree_quiescent=True)
    assert rt.quiescent('run') and owner.cancelled==['handle']
    with pytest.raises(w.Refusal):rt.cancel('handle',expected_revision=0)


def test_unknown_process_or_reused_start_identity_blocks_seal(tmp_path):
    rt,owner,binding=runtime(tmp_path);rt.create('handle',binding)
    owner.records['handle'].update(state='completed',tree_quiescent=True)
    assert rt.quiescent('run')
    owner.extra=['background-child'];assert not rt.quiescent('run')
    owner.extra=[];owner.complete=False;assert not rt.quiescent('run')
    owner.complete=True;owner.records['handle']['process_start']='reused'
    assert not rt.quiescent('run')
    with pytest.raises(w.Refusal):rt.create('handle',binding)


def test_restart_preserves_revocation_and_corruption_never_resets(tmp_path):
    rt,owner,binding=runtime(tmp_path);r=rt.create('handle',binding);rt.cancel('handle',expected_revision=r['revision'])
    restarted=c.CommandRuntime(rt.path,workspace=rt.workspace,root=rt.root,observer=owner)
    with pytest.raises(w.Refusal):restarted.reconnect('handle',binding)
    rt.path.write_text('{broken')
    with pytest.raises(w.Refusal):restarted.create('handle',binding)
    assert rt.path.read_text()=='{broken'


def test_lost_native_ownership_cannot_be_cancelled(tmp_path):
    rt,owner,binding=runtime(tmp_path);rt.create('handle',binding)
    owner.records['handle']['binding']=binding|{'run':'other'}
    with pytest.raises(w.Refusal):rt.cancel('handle',expected_revision=0)
    assert owner.cancelled==[]
