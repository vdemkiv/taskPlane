"""Shared public flow fixtures. These do not certify live host or package reload."""
from copy import deepcopy
import json
import pytest

from taskplane import flow, workflow as w, workflow_host as h, workflow_evidence as e
from taskplane.tests.test_workflow_evidence import prepare, bound_output
from taskplane.tests.test_workflow_local import decision
from taskplane.tests.test_worker_runtime import parent_pair


def journey(workspace, host, capsys):
    initial, _, _ = prepare(workspace)
    progressive={'criteria':['AC1'],'paths':{'product':['product.json']},'verification_inputs':['app.py']}
    (workspace/'scope.json').write_text(json.dumps(progressive))
    c=h.Controller(workspace,'root',h.installed_adapter(host))
    assert flow.main(['start','--workspace',str(workspace),'--scope','scope.json',
        '--request-reference','fixture/actual-user-task'],governor=c)==0
    capsys.readouterr()
    state=c.report(diagnostics=False)
    parent_pair(c)
    for phase in w.PHASES:
        assert w.current(state)['phase']==phase
        before=c._path().read_bytes()
        with pytest.raises(w.Refusal):
            c.apply('finish',state['run'],expected_revision=state['revision'])
        assert c._path().read_bytes()==before
        if phase=='build':
            parent_pair(c)
            state=c.report(diagnostics=False)
            assert state['scope']['paths']['build']==initial['scope']['paths']['build']
            c.guard({'tool_name':'Write','tool_input':{'path':'app.py'}},state['run'])
            with pytest.raises(w.Refusal):
                c.guard({'tool_name':'Write','tool_input':{'path':'unplanned.py'}},state['run'])
            (workspace/'app.py').write_text('value = 2  # implemented during approved Build\n')
        _,output,tasks=prepare(workspace,phase)
        if phase=='build':
            output=json.loads((workspace/bound_output(c,state)).read_text())
        output.update(run=state['run'],visit=w.current(state)['id'])
        for row in tasks['tasks']:
            row['owner']='root'
        if phase=='plan':
            output['ownership']={'T1':'root'}
            output['task_dag']=tasks['tasks']
        # The phase's fresh result paths were declared at advance, not all at start.
        output_path=(w.current(state)['artifact_paths'][0] if phase=='build' else state['scope']['paths'][phase][0])
        if phase=='build':
            c.guard({'tool_name':'Write','tool_input':{'path':output_path}},state['run'])
        # Early decomposition is concrete; Plan's inline executable DAG is sufficient.
        task_args=[]
        if phase in ('product','design'):
            task_path=phase+'-tasks.json'
            (workspace/task_path).write_text(json.dumps(tasks))
            task_args=['--tasks',task_path]
        if phase=='plan':
            (workspace/'tasks.json').unlink(missing_ok=True)
        (workspace/output_path).parent.mkdir(parents=True,exist_ok=True)
        (workspace/output_path).write_text(json.dumps(output))
        # Early views do not veto submission; typed checks retain their source context.
        if phase in ('product','design'):
            (workspace/'.taskplane/knowledge/graph.json').unlink(missing_ok=True)
        (workspace/'.taskplane/dashboard.html').unlink(missing_ok=True)
        assert flow.main(['submit','--workspace',str(workspace),'--run',state['run'],
            '--expected-revision',str(state['revision']),'--output',output_path,*task_args],governor=c)==0
        capsys.readouterr()
        state=c.report(diagnostics=False)
        assert (workspace/'.taskplane/dashboard.html').is_file()
        (workspace/'external-note.md').write_text('Unrelated external work')
        c.guard({'tool_name':'Read','tool_input':{'file_path':'external-note.md'}},state['run'])
        c.guard({'tool_name':'exec_command','tool_input':{'cmd':'rg --files'}},state['run'])
        with pytest.raises(w.Refusal):
            c.guard({'tool_name':'Write','tool_input':{'path':phase+'.json'}},state['run'])
        assert c.context(state['run'])['schema']=='taskplane.brief/v1'
        value=decision(state,text='Yes, proceed with the reviewed result.',candidate='approved',event='fixture/'+phase)
        value['presentation']['text']='Reviewed outcome with its evidence and limits.\nApprove this phase to proceed?'
        assert flow.main(['decide','--workspace',str(workspace),'--run',state['run'],
            '--expected-revision',str(state['revision']),'--decision-json',json.dumps(value)],governor=c)==0
        capsys.readouterr()
        state=c.report(diagnostics=False)
        if phase!='retro':
            next_phase=w.PHASES[w.PHASES.index(phase)+1]
            if next_phase=='build':parent_pair(c)
            assert flow.main(['advance','--workspace',str(workspace),'--run',state['run'],
                '--expected-revision',str(state['revision']),'--phase',next_phase],governor=c)==0
            capsys.readouterr();state=c.report(diagnostics=False)
    assert flow.main(['finish','--workspace',str(workspace),'--run',state['run'],
        '--expected-revision',str(state['revision'])],governor=c)==0
    capsys.readouterr();state=c.report(state['run'],diagnostics=False)
    assert state['finished'] and len(state['decisions'])==7
    assert all(d['human'] is True and d['automatic'] is False for d in state['decisions'].values())
    return state


@pytest.mark.parametrize('host',['codex','claude'])
def test_public_seven_phase_flow_has_separate_human_decisions_without_overhead(tmp_path,capsys,host):
    journey(tmp_path,host,capsys)


def test_task_dag_refuses_future_phase_prerequisite_before_publication():
    rows=[{'id':'later','phase':'evaluate','owner':'root','paths':['eval.json'],
           'criteria':['AC'],'dependencies':[],'verification':'Observe'},
          {'id':'build','phase':'build','owner':'root','paths':['app.py'],
           'criteria':['AC'],'dependencies':['later'],'verification':'Check'}]
    with pytest.raises(w.Refusal,match='later phase'):e.task_dag({'tasks':rows},['AC'])
    rows[0]['phase']='plan'
    assert len(e.task_dag({'tasks':rows},['AC']))==2


def test_active_new_run_refuses_package_switch_but_retains_history(tmp_path,monkeypatch):
    from taskplane import host_capabilities
    c=h.Controller(tmp_path,'root',h.installed_adapter('codex'))
    state=c.start({'scope':{'criteria':['AC'],'paths':{'product':['result.json']}},
                   'request_reference':'fixture/task'})
    original=deepcopy(state['runtime_identity'])
    changed={**original,'package_id':'foreign-package'}
    monkeypatch.setattr(host_capabilities,'runtime_identity',lambda **kwargs:changed)
    assert c.report(diagnostics=False)['runtime_identity']==original
    with pytest.raises(w.Refusal,match='installation/package'):
        c.guard({'tool_name':'Write','tool_input':{'path':'result.json'}},state['run'])
    c.guard({'tool_name':'Read','tool_input':{'path':'result.json'}},state['run'])
