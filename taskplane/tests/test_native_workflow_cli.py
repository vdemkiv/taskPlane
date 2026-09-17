"""Production subprocess conformance for cooperative native workflow profiles.

Decision messages are explicit test observations; these are not live human or
host-containment certifications. No trusted owner/FixtureHost is injected.
"""
from copy import deepcopy
import json
import inspect
import os
from pathlib import Path
import subprocess
import sys
import pytest
from taskplane import depgraph, workflow as w
from taskplane.tests.test_workflow_evidence import prepare
from taskplane.tests.test_workflow_local import decision, check_historical_finish_preserves_active

ROOT=Path(__file__).resolve().parents[2]


def cli(workspace,host,*args,root=ROOT,code=0):
    env={k:v for k,v in os.environ.items() if k not in {
        'CODEX_THREAD_ID','CODEX_SESSION_ID','TASKPLANE_CLAUDE_SESSION_ID','CLAUDE_SESSION_ID'}}
    env['CODEX_THREAD_ID' if host=='codex' else 'TASKPLANE_CLAUDE_SESSION_ID']='root'
    result=subprocess.run([sys.executable,'-B',str(root/'taskplane/tp.py'),'flow',*args,
                           '--workspace',str(workspace)],cwd=workspace,env=env,capture_output=True,text=True)
    assert result.returncode==code,(result.stdout,result.stderr)
    return json.loads(result.stdout)


def create(workspace):
    workspace.mkdir(parents=True,exist_ok=True)
    initial,_,_=prepare(workspace)
    (workspace/'build-check.txt').write_text('Fixture Build check passed')
    (workspace/'check.txt').write_text('Observed fixture check result')
    (workspace/'.taskplane/scope.json').write_text(json.dumps(initial['scope']))
    return initial['scope']


def output(workspace,state,change=None):
    phase=w.current(state)['phase']
    _,out,_=prepare(workspace,phase)
    out.update(run=state['run'],visit=w.current(state)['id'])
    if change:out['route_change']=change
    path=phase+'.json';(workspace/path).write_text(json.dumps(out))
    depgraph.scan(str(workspace),decompose=True,strict=True)
    return path


def exercise(workspace,host,root=ROOT):
    create(workspace)
    report=cli(workspace,host,'start','--scope','.taskplane/scope.json','--request-reference','test/user-request',root=root)
    state=report['workflow'];assert state['workflow_available'] and not state['authority_verified']
    for i,phase in enumerate(w.PHASES):
        assert state['phase']==phase
        if phase=='build':(workspace/'app.py').write_text('value = 2\n')
        target=output(workspace,state)
        state=cli(workspace,host,'submit','--output',target,'--tasks','tasks.json',
                  '--expected-revision',str(state['revision']),root=root)['workflow']
        cli(workspace,host,'finish','--expected-revision',str(state['revision']),root=root,code=2)
        value=decision(state,event='test-human-'+phase)
        bad=deepcopy(value);bad['source']['automatic']=True
        cli(workspace,host,'decide','--decision-json',json.dumps(bad),
            '--expected-revision',str(state['revision']),root=root,code=2)
        state=cli(workspace,host,'decide','--decision-json',json.dumps(value),
                  '--expected-revision',str(state['revision']),root=root)['workflow']
        assert state['status']=='approved' and not state['authority_verified']
        if phase!='retro':
            cli(workspace,host,'progress','--phase',w.PHASES[i+1],
                '--expected-revision',str(state['revision']),root=root,code=2)
            state=cli(workspace,host,'advance','--phase',w.PHASES[i+1],
                      '--expected-revision',str(state['revision']),root=root)['workflow']
        else:
            state=cli(workspace,host,'finish','--expected-revision',str(state['revision']),root=root)['workflow']
    assert state['status']=='accepted' and len(state['decisions'])==7
    assert all(d['assurance']=='observed' for d in state['decisions'].values())
    page=(workspace/'.taskplane/dashboard.html').read_text()
    assert 'Workflow gates active; host-wide protection unavailable' in page
    assert 'Dependency graph' in page and 'Task decomposition' in page
    restarted=cli(workspace,host,'report','--run',state['run'],root=root)['workflow']
    assert restarted['status']=='accepted' and restarted['coverage']['process_census']=='unknown'
    return state


def exercise_state_repairs(workspace, host, root=ROOT):
    """Run both repaired state invariants against the selected actual runtime."""
    create(workspace)
    state = cli(workspace,host,'start','--scope','.taskplane/scope.json',
                '--request-reference','test/size-limit',root=root)['workflow']
    target = output(workspace,state)
    small = (workspace/target).read_bytes()
    out = json.loads(small)
    out['supporting_notes'] = ['item'] * 350000
    (workspace/target).write_text(json.dumps(out))
    # Resolve the actual selected runtime's store path without a source fallback.
    path_probe = subprocess.run([sys.executable,'-I','-c',
        "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "from taskplane import workflow_host as h; "
        "assert Path(h.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()); "
        "c=h.Controller(Path(sys.argv[2]),'root',h.installed_adapter(sys.argv[3])); "
        "print(c.adapter.control_path(c.workspace,c.root))",str(root),str(workspace),host],
        capture_output=True,text=True)
    assert path_probe.returncode == 0, path_probe.stderr
    store = Path(path_probe.stdout.strip())
    before = store.read_bytes()
    result = cli(workspace,host,'submit','--output',target,'--tasks','tasks.json',
                 '--expected-revision',str(state['revision']),root=root,code=2)
    assert result['reason'] == 'state_unavailable' and 'size limit' in result['detail']
    assert store.read_bytes() == before
    resumed = cli(workspace,host,'report',root=root)['workflow']
    assert resumed['run'] == state['run'] and resumed['revision'] == state['revision']
    (workspace/target).write_bytes(small)
    accepted = cli(workspace,host,'submit','--output',target,'--tasks','tasks.json',
                   '--expected-revision',str(state['revision']),root=root)['workflow']
    assert accepted['status'] == 'awaiting_human_approval'
    replay_ws = workspace.parent/(workspace.name+'-replay')
    scope = create(replay_ws)
    a = cli(replay_ws,host,'start','--standalone','--scope','.taskplane/scope.json',
            '--request-reference','test/finished-A',root=root)['workflow']
    target = output(replay_ws,a)
    a = cli(replay_ws,host,'submit','--output',target,'--tasks','tasks.json',
            '--expected-revision',str(a['revision']),root=root)['workflow']
    a = cli(replay_ws,host,'decide','--decision-json',json.dumps(decision(a)),
            '--expected-revision',str(a['revision']),root=root)['workflow']
    a = cli(replay_ws,host,'finish','--expected-revision',str(a['revision']),root=root)['workflow']
    b = cli(replay_ws,host,'start','--scope','.taskplane/scope.json',
            '--request-reference','test/active-B',root=root)['workflow']
    assert b['scope'] == scope
    cli(replay_ws,host,'finish','--run',a['run'],'--expected-revision',str(a['revision']),root=root,code=2)
    # Child imports only the requested runtime and stdlib; no trusted fixture or source fallback.
    script = '''import sys, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from taskplane import workflow as w, workflow_host as h
assert Path(h.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
''' + inspect.getsource(check_historical_finish_preserves_active) + '''
c = h.Controller(Path(sys.argv[2]), 'root', h.installed_adapter(sys.argv[3]))
db = json.loads(c.adapter.control_path(c.workspace, c.root).read_bytes())
active = db['runs'][db['active']]
finished = next(s for s in db['runs'].values() if s['finished'])
check_historical_finish_preserves_active(c, finished, active)
print('NGEM-F01 NGEM-F02 repaired state checks passed')
'''
    child = subprocess.run([sys.executable,'-I','-c',script,str(root),str(replay_ws),host],
                           cwd=workspace.parent,capture_output=True,text=True)
    assert child.returncode == 0, child.stdout + child.stderr
    assert 'NGEM-F01 NGEM-F02 repaired state checks passed' in child.stdout
    assert cli(replay_ws,host,'report',root=root)['workflow']['run'] == b['run']


@pytest.mark.parametrize('host',['codex','claude'])
def test_native_state_repairs_through_production_interfaces(tmp_path,host):
    exercise_state_repairs(tmp_path.resolve()/'native',host)


@pytest.mark.parametrize('host',['codex','claude'])
def test_both_native_routing_contexts_complete_seven_explicit_gates(tmp_path,host):
    exercise(tmp_path.resolve(),host)


@pytest.mark.parametrize('phase',['product','design','engineering'])
def test_standalone_scope_cannot_silently_grant_build(tmp_path,phase):
    ws=tmp_path.resolve();create(ws)
    state=cli(ws,'codex','start','--phase',phase,'--standalone','--scope','.taskplane/scope.json',
              '--request-reference','test/standalone')['workflow']
    target=output(ws,state)
    state=cli(ws,'codex','submit','--output',target,'--tasks','tasks.json','--expected-revision',str(state['revision']))['workflow']
    state=cli(ws,'codex','decide','--decision-json',json.dumps(decision(state)),
              '--expected-revision',str(state['revision']))['workflow']
    cli(ws,'codex','advance','--phase','build','--expected-revision',str(state['revision']),code=2)
    assert cli(ws,'codex','finish','--expected-revision',str(state['revision']))['workflow']['status']=='accepted'


def test_engineering_findings_can_start_product_with_accepted_extension(tmp_path):
    ws=tmp_path.resolve();scope=create(ws)
    state=cli(ws,'codex','start','--phase','engineering','--standalone','--scope','.taskplane/scope.json',
              '--request-reference','test/findings')['workflow']
    target=output(ws,state,{'kind':'delivery','scope':scope})
    state=cli(ws,'codex','submit','--output',target,'--tasks','tasks.json','--expected-revision',str(state['revision']))['workflow']
    state=cli(ws,'codex','decide','--decision-json',json.dumps(decision(state)),
              '--expected-revision',str(state['revision']))['workflow']
    state=cli(ws,'codex','advance','--phase','product','--expected-revision',str(state['revision']))['workflow']
    assert state['phase']=='product' and state['visits'][0]['superseded']
    assert state['history'][-1]['route_change']['kind']=='delivery'


def test_native_start_needs_scope_and_never_upgrades_legacy_observations(tmp_path):
    ws=tmp_path.resolve();create(ws)
    assert cli(ws,'codex','start',code=2)['reason']=='invalid_evidence'
    assert cli(ws,'codex','start','--profile','protected_host',code=2)['reason']=='unsupported_authority'
    from taskplane import flow
    flow.append(ws,{'kind':'start','run':'old','session':'root','phase':'retro'})
    flow.append(ws,{'kind':'finish','run':'old','session':'root','phase':'retro'})
    assert cli(ws,'codex','report','--run','old')['status']=='legacy_unverified'
    state=cli(ws,'codex','start','--scope','.taskplane/scope.json','--request-reference','test/new')['workflow']
    assert state['run']!='old' and state['phase']=='product' and state['decisions']=={}
