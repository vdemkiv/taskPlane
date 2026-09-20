"""Observed policy fixtures do not certify live human origin or host permissions."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from taskplane import workflow as w, workflow_approval as approval, workflow_host as h
from taskplane.tests.test_workflow_local import setup, submit, decision, decide
from taskplane.tests.test_native_workflow_cli import cli, create, output, ROOT


def authorization(s, mode='autonomous', event='policy-user-1'):
    return {'schema': approval.SCHEMA, 'event_id': event, 'mode': mode,
            'binding': approval.policy_binding(s), 'recorder': 'root_orchestrator',
            'excerpt': 'For this task, run autonomously and auto-approve phases after required checks pass.'
                if mode == 'autonomous' else 'Return to manual approval.',
            'source': {'kind': 'conversation', 'reference': event, 'conversation': s['root'],
                       'actor': 'user', 'automatic': False,
                       'observed_at': datetime.now(timezone.utc).isoformat()},
            'allowed_phases': list(w.PHASES) if mode == 'autonomous' else [],
            'stop_phases': [], 'conditions': []}


def assessment(s):
    packet = w.current(s)['packet']
    return {'schema': approval.ASSESSMENT, 'binding': w.binding(s, packet),
            'policy_digest': s['approval_policy']['digest'],
            'conditions': [{'id': c['id'], 'status': 'pass', 'explanation': 'Fixture evidence reviewed against the original instructions.',
                            'evidence': [next(iter(packet['manifest']))]} for c in s['approval_policy']['conditions']]}


def set_policy(c, s, request=None):
    return c.apply('policy', s['run'], expected_revision=s['revision'],
                   native_reference=json.dumps(request or authorization(s)))


def auto(c, s, value=None, revision=None):
    target = '.taskplane/assessment.json'
    (c.workspace/target).write_text(json.dumps(value or assessment(s)))
    return c.apply('auto-decide', s['run'], expected_revision=s['revision'] if revision is None else revision, output=target)


@pytest.mark.parametrize('bad', ['implicit', 'negated', 'actor', 'tool', 'automatic', 'foreign', 'scope', 'stale', 'conditions', 'future'])
def test_authorization_rejects_missing_or_foreign_consent(tmp_path, bad):
    c,s=setup(tmp_path); p=authorization(s)
    if bad=='implicit': p['excerpt']='Please implement this feature.'
    if bad=='negated': p['excerpt']='Do not auto-approve any phases.'
    if bad=='actor': p['source']['actor']='assistant'
    if bad=='tool': p['source']['kind']='tool_result'
    if bad=='automatic': p['source']['automatic']=True
    if bad=='foreign': p['binding']['run']='foreign'
    if bad=='scope': p['binding']['scope_digest']='foreign'
    if bad=='stale': p['binding']['revision']+=1
    if bad=='conditions': p['conditions']=[{'id':'x','kind':'shell','instruction':'execute me'}]
    if bad=='future': p['source']['observed_at']='2999-01-01T00:00:00+00:00'
    with pytest.raises(w.Refusal): set_policy(c,s,p)
    assert 'approval_policy' not in c.report()


NON_CONSENT = [
    'No automatic approvals for this task.',
    'Auto-approval is not authorized for this task.',
    'Implement an option to auto-approve phases.',
    'For this task, implement auto-approval of phases.',
    'Example: For this task, auto-approve phases.',
    'Example:\nFor this task, auto-approve phases.',
    'Document "For this task, auto-approve phases." in README.',
    '`For this task, auto-approve phases.`',
    '> For this task, auto-approve phases.',
    'For this task, auto-approve phases? I have not decided.',
    'For this task, auto-approve phases. No automatic approvals are authorized.',
    'For this task, auto-approve phases. Keep manual approval.',
    'For this task, auto-approve phases is an example, not permission.',
    'If I approve later, auto-approve phases.',
]


def exercise_nonconsent_policy(c, state, excerpts):
    """A mistaken mode classification cannot turn these excerpts into consent."""
    store=c.adapter.control_path(c.workspace,c.root);before=store.read_bytes()
    for text in excerpts:
        request=authorization(state);request['excerpt']=text
        try:set_policy(c,state,request)
        except w.Refusal as exc:assert exc.reason=='approval_required'
        else:raise AssertionError('Non-consent admitted: '+text)
        assert store.read_bytes()==before
    assert not c.report().get('approval_policy')


@pytest.mark.parametrize('text',NON_CONSENT)
def test_nonconsent_never_enables_automatic_approval(tmp_path,text):
    c,state=setup(tmp_path)
    exercise_nonconsent_policy(c,state,[text])


@pytest.mark.parametrize('text',[
    'For this task, run autonomously and auto-approve phases after required checks pass.',
    'Use taskplane to implement the settings page. For this task, auto-approve phases after required checks pass; stop before Retro.',
    'For this task, auto-approve Product, Design, Plan and Build after required checks pass. Stop before Evaluate.',
    'Please automatically approve phases for this run after required checks pass.',
    'I authorize you to auto-approve phases for this task after required checks pass.',
])
def test_affirmative_run_instructions_enable_policy(tmp_path,text):
    c,state=setup(tmp_path);request=authorization(state);request['excerpt']=text
    state=set_policy(c,state,request)
    assert state['approval_policy']['provenance']['excerpt']==text
    assert not state['decisions']


def exercise_nonconsent_cli(workspace,host,root=ROOT):
    create(workspace)
    state=cli(workspace,host,'start','--scope','.taskplane/scope.json','--request-reference','test/ENG-F02',root=root)['workflow']
    for text in NON_CONSENT:
        request=authorization(state);request['excerpt']=text
        result=cli(workspace,host,'policy','--policy-json',json.dumps(request),
                   '--expected-revision',str(state['revision']),root=root,code=2)
        assert result['reason']=='approval_required'
    after=cli(workspace,host,'report','--run',state['run'],root=root)['workflow']
    assert after['revision']==state['revision'] and not after.get('approval_policy') and not after['decisions']


@pytest.mark.parametrize('host',['codex','claude'])
def test_native_cli_rejects_nonconsent(tmp_path,host):
    exercise_nonconsent_cli(tmp_path/'workspace',host)


def test_default_manual_and_policy_does_not_approve(tmp_path):
    c,s=setup(tmp_path); s=submit(c,s)
    with pytest.raises(w.Refusal): auto(c,s,{'schema':approval.ASSESSMENT,'binding':w.binding(s,w.current(s)['packet'])})
    s=set_policy(c,s)
    assert w.current(s)['decision']=='awaiting_human_approval' and not s['decisions']
    accepted=auto(c,s)
    d=next(iter(accepted['decisions'].values()))
    assert d['kind']=='policy' and not d['human'] and d['automatic']
    assert h.Controller(c.workspace,'root',h.installed_adapter('codex')).report()['status']=='approved'


def test_policy_and_decision_idempotency_revocation_and_race(tmp_path):
    c,s=setup(tmp_path); p=authorization(s); s=set_policy(c,s,p)
    assert set_policy(c,s,p)==s
    conflict=deepcopy(p); conflict['excerpt']+=' Changed conditions.'
    with pytest.raises(w.Refusal,match='replay'): set_policy(c,s,conflict)
    s=submit(c,s); value=assessment(s); accepted=auto(c,s,value)
    assert auto(c,accepted,value,revision=s['revision'])==accepted
    changed=deepcopy(value);changed['conditions'][0]['explanation']='Conflicting retry'
    with pytest.raises(w.Refusal,match='replay'):auto(c,accepted,changed)
    s=c.apply('advance',accepted['run'],expected_revision=accepted['revision'],phase='design')
    s=submit(c,s); old=assessment(s)
    revoked=set_policy(c,s,authorization(s,'manual','revoke-1'))
    with pytest.raises(w.Refusal):auto(c,revoked,old,revision=s['revision'])
    assert len(c.report()['decisions'])==1 and c.report()['approval_policy']['mode']=='manual'


@pytest.mark.parametrize('bad',['unknown','missing','unsealed','stop','live','route','scope','drift'])
def test_auto_approval_preserves_evidence_scope_and_conditions(tmp_path,bad):
    c,s=setup(tmp_path); p=authorization(s)
    if bad=='stop':p['stop_phases']=['product']
    s=set_policy(c,s,p);s=submit(c,s);a=assessment(s)
    if bad=='unknown':a['conditions'][0]['status']='unknown'
    if bad=='missing':a['conditions']=[]
    if bad=='unsealed':a['conditions'][0]['evidence']=['tasks.json']
    if bad=='live': c.observe({'hook_event_name':'PostToolUse','tool_name':'exec_command','tool_input':{},'tool_response':{'session_id':'running'}},s['run'])
    if bad=='route':
        changed=deepcopy(s);changed['visits'][0]['packet']['route_change']={'kind':'delivery'}
        a=assessment(changed)
        with pytest.raises(w.Refusal):approval.automatic_decision(changed,a)
        return
    if bad=='scope':
        changed=deepcopy(s);changed['scope']['paths']['build'].append('extra.py');a=assessment(changed)
        with pytest.raises(w.Refusal):approval.automatic_decision(changed,a)
        return
    if bad=='drift':(c.workspace/'product.json').write_text('{}')
    with pytest.raises(w.Refusal):auto(c,s,a)
    assert not c.report()['decisions']


def test_human_rejection_suspends_policy_and_protected_host_refuses(tmp_path):
    c,s=setup(tmp_path);s=set_policy(c,s);s=submit(c,s)
    s=decide(c,s,decision(s,text='rejected'))
    assert s['policy_suspension']
    s=submit(c,s)
    with pytest.raises(w.Refusal,match='intervention'):auto(c,s)
    protected=deepcopy(s);protected['profile']='protected_host'
    with pytest.raises(w.Refusal):approval.authorize(protected,authorization(protected))
    with pytest.raises(w.Refusal):approval.automatic_decision(protected,assessment(protected))



@pytest.mark.parametrize('phase,output', [
    ('build', {'build_checks': [{'name': 'tests', 'status': 'fail'}], 'known_gaps': []}),
    ('build', {'build_checks': [{'name': 'tests', 'status': 'unknown'}], 'known_gaps': []}),
    ('build', {'build_checks': [{'name': 'tests', 'status': 'pass'}], 'known_gaps': ['Unresolved']}),
    ('evaluate', {'criterion_results': {'AC1': {'status': 'unknown'}}, 'unknowns_and_failures': []}),
    ('evaluate', {'criterion_results': {'AC1': {'status': 'pass'}}, 'unknowns_and_failures': ['Unresolved']}),
    ('engineering', {'findings': [{'severity': 'high'}]}),
    ('engineering', {'findings': [{'severity': 'medium', 'blocking': True}]}),
])
def test_automatic_approval_blocks_unresolved_phase_evidence(tmp_path, phase, output):
    c,s=setup(tmp_path);s=set_policy(c,s);s=submit(c,s)
    # Exercise the policy gate with an already-submitted packet; submission has
    # its own phase schema validation tests.
    stage=s['visits'][s['index']];stage['phase']=phase;stage['packet']['output']=output
    with pytest.raises(w.Refusal, match='checks|Evaluation|blockers'):
        approval.automatic_decision(s,assessment(s))


def test_required_named_check_and_hook_authorization(tmp_path):
    from taskplane import flow
    c,s=setup(tmp_path);p=authorization(s)
    p['conditions']=[{'id':'tests','kind':'required_check','check':'suite','instruction':'Require the named suite.'}]
    p['recorder']='native_prompt_hook';p['source']['kind']='native_prompt'
    flow.hook({'hook_event_name':'UserPromptSubmit','cwd':str(tmp_path),'thread_id':'root','taskplane_policy':p},governor=c)
    s=c.report();assert s['approval_policy']['mode']=='autonomous'
    s=submit(c,s)
    with pytest.raises(w.Refusal,match='Named required check'):auto(c,s)
    stage=w.current(s);stage['packet']['output']['build_checks']=[{'name':'suite','status':'pass'}]
    assert approval.automatic_decision(s,assessment(s))['kind']=='policy'

def exercise_autonomous(workspace, host, root=ROOT):
    create(workspace)
    state=cli(workspace,host,'start','--scope','.taskplane/scope.json','--request-reference','test/autonomy',root=root)['workflow']
    state=cli(workspace,host,'policy','--policy-json',json.dumps(authorization(state)),
              '--expected-revision',str(state['revision']),root=root)['workflow']
    for i,phase in enumerate(w.PHASES):
        if phase=='design':
            state=cli(workspace,host,'policy','--policy-json',json.dumps(authorization(state,'manual','pause-user')),
                      '--expected-revision',str(state['revision']),root=root)['workflow']
        target=output(workspace,state)
        # The common fixture has placeholder gap strings. Actual passing evidence has no unresolved gaps.
        out=json.loads((workspace/target).read_text())
        if phase=='build':out['known_gaps']=[]
        if phase=='evaluate':out['unknowns_and_failures']=[]
        (workspace/target).write_text(json.dumps(out))
        from taskplane import depgraph
        depgraph.scan(str(workspace),decompose=True,strict=True)
        state=cli(workspace,host,'submit','--output',target,'--tasks','tasks.json',
                  '--expected-revision',str(state['revision']),root=root)['workflow']
        if phase=='design':
            (workspace/'.taskplane/assessment.json').write_text(json.dumps(assessment(state)))
            cli(workspace,host,'auto-decide','--assessment','.taskplane/assessment.json',
                '--expected-revision',str(state['revision']),root=root,code=2)
            state=cli(workspace,host,'policy','--policy-json',json.dumps(authorization(state,event='resume-user')),
                      '--expected-revision',str(state['revision']),root=root)['workflow']
        (workspace/'.taskplane/assessment.json').write_text(json.dumps(assessment(state)))
        state=cli(workspace,host,'auto-decide','--assessment','.taskplane/assessment.json',
                  '--expected-revision',str(state['revision']),root=root)['workflow']
        if phase!='retro':
            state=cli(workspace,host,'advance','--phase',w.PHASES[i+1],
                      '--expected-revision',str(state['revision']),root=root)['workflow']
        else:
            state=cli(workspace,host,'finish','--expected-revision',str(state['revision']),root=root)['workflow']
    assert state['status']=='accepted' and len(state['decisions'])==7
    assert all(d['kind']=='policy' and d['automatic'] and not d['human'] for d in state['decisions'].values())
    assert cli(workspace,host,'report','--run',state['run'],root=root)['workflow']['status']=='accepted'
    return state


@pytest.mark.parametrize('host',['codex','claude'])
def test_seven_phase_autonomous_native_journey(tmp_path,host):
    exercise_autonomous(tmp_path.resolve(),host)


def test_dashboard_distinguishes_pending_policy_and_current_human_checkpoint(tmp_path):
    from taskplane import flow, flow_dashboard
    c,s=setup(tmp_path);s=set_policy(c,s);s=submit(c,s)
    model=flow.report(tmp_path,s['run'],governor=c)
    assert 'Awaiting policy assessment' in flow_dashboard.render(str(tmp_path),model)
    model['workflow']['visits'][0]['decision']='approved'
    model['workflow']['decisions']={
        'old-policy': {'kind':'policy','binding':{'visit':w.current(s)['id'],'checkpoint':'superseded-checkpoint'}},
        'current-human': {'kind':'human','binding':{'visit':w.current(s)['id'],'checkpoint':w.current(s)['packet']['checkpoint']}}}
    page=flow_dashboard.render(str(tmp_path),model)
    assert 'Automatically approved' not in page and 'Human decision: Approved' in page
