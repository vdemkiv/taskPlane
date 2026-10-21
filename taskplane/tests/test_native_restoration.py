"""Integration of restored code uses explicit test owners, not certified hosts."""
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import pytest
from taskplane import command_runtime as cr, flow, host_native, primitives, workflow as w, workflow_host as h
from taskplane.tests.test_command_runtime import Processes
from taskplane.tests.test_host_native import Owner, human, native_owner
from taskplane.tests.test_workflow_delivery import output


class NativeOwner(Owner, Processes):
    def __init__(self, base):
        Owner.__init__(self,Path(base.context['workspace']),base.control,base.scope)
        Processes.__init__(self)
        self.native_calls={}
    def command_for(self,event):return deepcopy(self.native_calls[event['tool_use_id']])


def configured(tmp_path):
    base,_=native_owner(tmp_path);owner=NativeOwner(base)
    session=host_native.NativeSession(owner,host='codex',version='test-1',workspace=Path(owner.context['workspace']),root='root')
    store=tmp_path/'commands.json';store.write_text(json.dumps({'schema':'taskplane.native-commands/v1','workspace':str(session.workspace),'root':'root','handles':{}}))
    commands=cr.CommandRuntime(store,workspace=session.workspace,root='root',observer=owner)
    adapter=h.CodexAdapter(session,commands);c=h.Controller(session.workspace,'root',adapter)
    human(owner,'scope',authorization={'scope':owner.scope,'entry':'product','standalone':False})
    initial=c.start({'entry':'product','standalone':False,'native_reference':'scope'})
    return c,owner,initial


def bind_call(owner,event,state,paths,handle=None):
    owner.native_calls[event['tool_use_id']]={'event_digest':primitives.content_fingerprint({'call_id':event['tool_use_id'],'tool':event['tool_name'],'input':event['tool_input']}),
                                             'grant':cr.grant(state),'contained':True,'paths':sorted(paths),'handle':handle}


def test_restored_native_adapter_runs_all_seven_human_gates_and_replays(tmp_path):
    c,owner,s=configured(tmp_path)
    for i,phase in enumerate(w.PHASES):
        target=output(c,s)
        s=c.apply('submit',s['run'],expected_revision=s['revision'],output=target,tasks='tasks.json')
        with pytest.raises(w.Refusal):c.apply('advance',s['run'],expected_revision=s['revision'],phase=w.PHASES[i+1] if i<6 else '')
        key='actual-'+phase;human(owner,key,w.binding(s,w.current(s)['packet']))
        accepted=c.apply('decide',s['run'],expected_revision=s['revision'],native_reference=key)
        assert c.apply('decide',s['run'],expected_revision=s['revision'],native_reference=key)==accepted
        owner.events[key]['choice']='rejected'
        with pytest.raises(w.Refusal):c.apply('decide',s['run'],expected_revision=accepted['revision'],native_reference=key)
        owner.events[key]['choice']='approved'
        s=c.apply('advance' if i<6 else 'finish',s['run'],expected_revision=accepted['revision'],phase=w.PHASES[i+1] if i<6 else '')
    assert s['finished'] and len(s['decisions'])==7
    assert not h.installed_adapter('codex').capabilities()['human_origin']


def test_command_hooks_use_independent_call_binding_and_actual_processes(tmp_path):
    c,owner,s=configured(tmp_path);paths=s['scope']['paths']['product']
    event={'cwd':str(c.workspace),'session_id':'root','hook_event_name':'PreToolUse','tool_use_id':'call1',
           'tool_name':'exec_command','tool_input':{'cmd':'native scoped command'}}
    with pytest.raises(w.Refusal):c.guard(event,s['run'])
    bind_call(owner,event,s,paths)
    c.guard(event,s['run'])
    with pytest.raises(w.Refusal):c.guard(event|{'tool_input':{'cmd':'different command'}},s['run'])
    owner.records['process']={'handle':'process','binding':cr.grant(s),'process_start':'host-process-1','state':'running','tree_quiescent':False}
    owner.native_calls['call1']['handle']='process'
    assert flow.hook(event|{'hook_event_name':'PostToolUse'},governor=c)=={}
    waiting=flow.hook(event|{'hook_event_name':'Stop'},governor=c)
    assert 'quiescence' in waiting['systemMessage'] and 'decision' not in waiting
    target=output(c,s)
    with pytest.raises(w.Refusal):c.apply('submit',s['run'],expected_revision=s['revision'],output=target,tasks='tasks.json')
    stdin=event|{'tool_use_id':'input1','tool_name':'write_stdin','tool_input':{'session_id':'process','chars':'next'}}
    bind_call(owner,stdin,s,paths,'process');c.guard(stdin,s['run'])
    c.adapter.commands.cancel('process',expected_revision=0)
    with pytest.raises(w.Refusal):c.guard(stdin,s['run'])
    owner.records['process'].update(state='cancelled',tree_quiescent=True)
    s=c.apply('submit',s['run'],expected_revision=s['revision'],output=target,tasks='tasks.json')
    with pytest.raises(w.Refusal):c.guard(stdin,s['run'])


def test_native_grant_cannot_use_boolean_as_revision(tmp_path):
    c,owner,s=configured(tmp_path)
    # Use an actual submitted checkpoint with revision 1, so True would compare equal.
    target=output(c,s);s=c.apply('submit',s['run'],expected_revision=s['revision'],output=target,tasks='tasks.json')
    binding=w.binding(s,w.current(s)['packet']);assert binding['revision']==1
    human(owner,'bad',binding|{'revision':True})
    with pytest.raises(w.Refusal):c.apply('decide',s['run'],expected_revision=1,native_reference='bad')


def test_named_installed_hooks_keep_native_error_contract(tmp_path):
    root=Path(__file__).resolve().parents[2]
    hooks=json.loads((root/'hooks/hooks.json').read_text())['hooks']
    for command,event in flow.HOOK_NAMES.items():
        result=subprocess.run([sys.executable,str(root/'taskplane/tp.py'),command],input='[]',capture_output=True,text=True,cwd=tmp_path)
        data=json.loads(result.stdout)
        if event=='PreToolUse':assert result.returncode==0 and data['hookSpecificOutput']['permissionDecision']=='deny'
        elif event=='Stop':assert result.returncode==0 and data.get('systemMessage') and 'decision' not in data
        else:assert result.returncode==2 and data['decision']=='block'
    for event,groups in hooks.items():
        for group in groups:
            for hook in group['hooks']:
                assert any(name in hook['command'] and flow.HOOK_NAMES[name]==event for name in flow.HOOK_NAMES)


def test_full_native_start_requires_opaque_event_reference(tmp_path,capsys):
    c,owner,_=configured(tmp_path)
    # Existing protected state is reused without replacing its original authority.
    assert flow.main(['start','--workspace',str(c.workspace),'--native-event','scope'],governor=c)==0
    assert json.loads(capsys.readouterr().out)['workflow']['authority_verified']


def test_native_support_projection_separates_readiness_from_authority(tmp_path):
    from taskplane import flow_dashboard
    c,_,_=configured(tmp_path)
    report=flow.report(c.workspace,governor=c)
    report['workflow']=h.Controller(c.workspace,'root',h.installed_adapter('codex','protected_host')).availability()
    report['workflow']['native_observations']['plugin_version']='<img src=x onerror=alert(1)>'
    page=flow_dashboard.render(str(c.workspace),report)
    assert 'Host governance unverified' in page and 'Native host support' in page
    assert 'Protected approval storage' in page and 'Process revocation' in page
    assert '<img src=x' not in page and '&lt;img src=x' in page
