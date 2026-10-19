"""Production subprocess conformance for cooperative native workflow profiles.

Decision messages are explicit test observations; these are not live human or
host-containment certifications. No trusted owner/FixtureHost is injected.
"""
from copy import deepcopy
import json
import inspect
import os
import shlex
from pathlib import Path
import subprocess
import sys
import uuid
import pytest
from taskplane import depgraph, workflow as w
from taskplane.tests.test_workflow_evidence import prepare
from taskplane.tests.test_workflow_local import decision, check_historical_finish_preserves_active

ROOT=Path(__file__).resolve().parents[2]
CLAUDE_TRANSPORT_SUPPORTED = os.name == 'posix' and sys.platform in {'linux', 'darwin'}


def fixture_environment(workspace, host, root, environment=None):
    env={k:v for k,v in os.environ.items() if not k.startswith(
        ('CODEX_', 'CLAUDE_', 'TASKPLANE_', 'PLUGIN_ROOT'))}
    host_data=workspace.parent/(workspace.name+'-host-data')
    env.update(CODEX_HOME=str(host_data/'codex'),CLAUDE_CONFIG_DIR=str(host_data/'claude'))
    env.update(environment or {})  # Explicit fixture observations only; no ambient session identity.
    if host == 'claude':
        # Both worker and usage readers use the explicit isolated config root.
        env['HOME'] = str(host_data/'home')
        transcript = Path(env['CLAUDE_CONFIG_DIR'])/'projects/fixture/root.jsonl'
        transcript.parent.mkdir(parents=True, exist_ok=True)
        if not transcript.exists():
            transcript.write_text(json.dumps({'sessionId': 'root', 'cwd': str(workspace),
                                              'isSidechain': False}) + '\n')
    env['CODEX_THREAD_ID' if host=='codex' else 'TASKPLANE_CLAUDE_SESSION_ID']='root'
    env.update(PLUGIN_ROOT=str(root),CLAUDE_PLUGIN_ROOT=str(root),
               PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'])
    return env


def cli(workspace,host,*args,root=ROOT,code=0,environment=None,entry="flow"):
    if entry == 'flow' and host == 'claude' and args[0] in {'context', 'submit'} and code == 0 and not CLAUDE_TRANSPORT_SUPPORTED:
        pytest.skip('Successful Claude context transport requires macOS or Linux; refusal is tested separately')
    workspace, root = Path(workspace).resolve(), Path(root).resolve()
    env = fixture_environment(workspace, host, root, environment)
    transcript = Path(env['CLAUDE_CONFIG_DIR'])/'projects/fixture/root.jsonl'
    # Match the declared Windows hook launcher; py -3 may select a different
    # installed interpreter from the one running pytest.
    python=['py','-3'] if os.name=='nt' else [str(Path(sys.executable).resolve())]
    argv=[*python,str(root/'taskplane/tp.py'),entry,*args,
          *(['--full'] if entry == 'flow' and not (host == 'claude' and args[0] == 'context') else []),
          '--workspace',str(workspace)]
    hooks=json.loads((root/'hooks/hooks.json').read_text())['hooks']
    call_id = 'fixture-' + uuid.uuid4().hex
    def hook(name, **extra):
        identity=({'session_id':'root', 'transcript_path':str(transcript)}
                  if host=='claude' else {'thread_id':'root'})
        tool,key=('Bash','command') if host=='claude' else ('exec_command','cmd')
        event={'hook_event_name':name,'cwd':str(workspace),'tool_use_id':call_id,**identity,
               'tool_name':tool,'tool_input':{key:shlex.join(argv)},**extra}
        command=hooks[name][0]['hooks'][0]['commandWindows' if os.name=='nt' else 'command']
        result=subprocess.run(command,shell=True,cwd=workspace,env=env,input=json.dumps(event),
                              capture_output=True,text=True)
        assert result.returncode==0,(result.stdout,result.stderr)
        return json.loads(result.stdout)
    before=hook('PreToolUse')
    if before.get('hookSpecificOutput',{}).get('permissionDecision')=='deny':
        assert code==2,before
        return {**before,'hook_refusal':True}  # The denied operation is never executed.
    updated = before.get('hookSpecificOutput', {}).get('updatedInput')
    if entry == 'flow' and host == 'claude' and args[0] == 'context':
        assert isinstance(updated, dict) and isinstance(updated.get('command'), str), before
    executed = shlex.split(updated['command']) if updated is not None else argv
    result=subprocess.run(executed,cwd=workspace,env=env,capture_output=True,text=True)
    assert result.returncode==code,(result.stdout,result.stderr)
    post = hook('PostToolUse', **({'tool_input': updated} if updated is not None else {}),
                tool_response={'exit_code':result.returncode})
    assert post.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny', post
    return json.loads(result.stdout) if entry == "flow" else result.stdout.strip()


def exercise_unsupported_claude_context(workspace, root=ROOT):
    """A real unsupported host must refuse context, never forge a receipt."""
    create(workspace)
    state = cli(workspace, 'claude', 'start', '--scope', '.taskplane/scope.json',
                '--request-reference', 'fixture/unsupported-transport', root=root)['workflow']
    refused = cli(workspace, 'claude', 'context', '--run', state['run'], root=root, code=2)
    assert refused['hook_refusal']
    specific = refused['hookSpecificOutput']
    assert specific['permissionDecision'] == 'deny' and 'updatedInput' not in specific
    assert 'unsupported' in specific['permissionDecisionReason'] or 'unavailable' in specific['permissionDecisionReason']
    current = cli(workspace, 'claude', 'report', root=root)['workflow']
    assert current['revision'] == state['revision'] and current['decisions'] == state['decisions']


@pytest.mark.skipif(CLAUDE_TRANSPORT_SUPPORTED, reason='Exercises a host without Claude invocation transport')
def test_unsupported_claude_context_refuses_through_declared_hooks(tmp_path):
    exercise_unsupported_claude_context(tmp_path)


def handoff(workspace,host,root=ROOT):
    cli(workspace,host,root=root,entry="dashboard")
    if host=='claude':
        # Claude has no Codex opener. A linked artifact is a supported handoff;
        # this fixture makes no claim that a live Claude view was displayed.
        cli(workspace,host,'present','--evidence','.taskplane/dashboard.html','--presentation','linked',
            '--note','Claude fixture artifact linked; live display is unverified.',root=root)
        return
    # Exercise the declared opener guard using the real native target shape.
    # The queued tool result is a fixture, never a live-display assertion.
    env={k:v for k,v in os.environ.items() if not k.startswith(
        ('CODEX_', 'CLAUDE_', 'TASKPLANE_', 'PLUGIN_ROOT'))}
    env.update(PLUGIN_ROOT=str(root),CLAUDE_PLUGIN_ROOT=str(root),CODEX_THREAD_ID='root',
               PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'])
    hooks=json.loads((root/'hooks/hooks.json').read_text())['hooks']
    for name in ('PreToolUse','PostToolUse'):
        event={'hook_event_name':name,'cwd':str(workspace),'thread_id':'root','session_id':'root',
               'tool_name':'mcp__codex_app__open_in_codex',
               'tool_input':{'target':{'type':'browser','url':(workspace/'.taskplane/dashboard.html').as_uri()}}}
        if name=='PostToolUse':event['tool_response']={'status':'queued','threadId':'root'}
        command=hooks[name][0]['hooks'][0]['commandWindows' if os.name=='nt' else 'command']
        r=subprocess.run(command,shell=True,cwd=workspace,env=env,input=json.dumps(event),capture_output=True,text=True)
        assert r.returncode==0,(r.stdout,r.stderr)
        assert json.loads(r.stdout).get('hookSpecificOutput',{}).get('permissionDecision')!='deny',r.stdout
    cli(workspace,host,'present','--evidence','.taskplane/dashboard.html','--presentation','linked',
        '--note','Fixture native artifact linked; visual host verification unavailable.',root=root)


def create(workspace):
    workspace.mkdir(parents=True,exist_ok=True)
    initial,_,_=prepare(workspace)
    initial['scope']['execution_contract'] = 'ownership/v1'
    (workspace/'build-check.txt').write_text('Fixture Build check passed')
    (workspace/'check.txt').write_text('Observed fixture check result')
    (workspace/'.taskplane/scope.json').write_text(json.dumps(initial['scope']))
    return initial['scope']


@pytest.mark.parametrize('host',['codex','claude'])
def test_declared_surface_matches_runtime_and_automatic_hooks(tmp_path,host):
    create(tmp_path)
    state=cli(tmp_path,host,'start','--standalone','--phase','product',
              '--scope','.taskplane/scope.json','--request-reference','fixture/surface')['workflow']
    from taskplane import workflow_host
    controller=workflow_host.Controller(tmp_path,'root',workflow_host.installed_adapter(host))
    assert state['root']=='root' and state['workspace']==str(tmp_path.resolve())
    assert state['profile']=='native_workflow'
    cli(tmp_path,host,'report')
    database=controller._read(controller._path())
    admissions=[row for row in database['admissions'].values() if row['run']==state['run']]
    assert admissions and all(row['host']==host and row['root']=='root' for row in admissions)
    assert any(row['state']=='completed' and row['automatic'] for row in admissions)


@pytest.mark.parametrize('host',['codex','claude'])
def test_native_deactivate_only_uninitialized_engagement(tmp_path,host):
    create(tmp_path)
    cli(tmp_path,host,'activate','--phase','taskplane','--request-reference','fixture/select')
    cli(tmp_path,host,'deactivate',code=2)
    result=cli(tmp_path,host,'deactivate','--request-reference','fixture/user-recover',
               '--note','Clear inactive selection')
    assert result['harness']['status']=='inactive'
    state=cli(tmp_path,host,'start','--standalone','--phase','product',
              '--scope','.taskplane/scope.json','--request-reference','fixture/new-work')['workflow']
    result=cli(tmp_path,host,'deactivate','--request-reference','fixture/user',
               '--note','Active work must retain its guard',code=2)
    assert result['reason']=='approval_required' and 'active workflow' in result['detail']
    after=cli(tmp_path,host,'report')['workflow']
    assert after['run']==state['run'] and after['revision']==state['revision']
    cli(tmp_path,host,'deactivate','--profile','protected_host',
        '--request-reference','fixture/user','--note','Cannot downgrade',code=2)


def output(workspace,state,change=None):
    from taskplane.tests.test_workflow_evidence import bound_output
    selected = Path(state.get('runtime_identity', {}).get('root', ROOT))
    # Capture with the same selected runtime, interpreter and isolated host
    # environment as the actual hook. A pytest environment is different evidence.
    source = ('import sys,json\nfrom pathlib import Path\nfrom copy import deepcopy\n'
              'from types import SimpleNamespace\nsys.path.insert(0,sys.argv[1])\n'
              'from taskplane import workflow as w,workflow_evidence as e\n'
              + inspect.getsource(prepare) + '\n' + inspect.getsource(bound_output)
              + '\npayload=json.load(sys.stdin)\nprint(bound_output(SimpleNamespace(workspace=Path.cwd()),payload))\n')
    python = ['py', '-3'] if os.name == 'nt' else [str(Path(sys.executable).resolve())]
    transcript = Path(workspace).parent/(Path(workspace).name+'-host-data')/'claude/projects/fixture/root.jsonl'
    host = 'claude' if transcript.is_file() else 'codex'
    env = fixture_environment(Path(workspace).resolve(), host, selected)
    result = subprocess.run([*python, '-B', '-c', source, str(selected)], cwd=workspace, env=env,
                            input=json.dumps(state), text=True, capture_output=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    path = result.stdout.strip()
    if change:
        out = json.loads((workspace/path).read_text())
        out['route_change'] = change
        (workspace/path).write_text(json.dumps(out))
    return path


def exercise_correction_cli(workspace, host, root=ROOT):
    create(workspace)
    state=cli(workspace,host,'start','--scope','.taskplane/scope.json','--request-reference','test/review-correction',root=root)['workflow']
    target=output(workspace,state)
    state=cli(workspace,host,'submit','--output',target,'--tasks','tasks.json',
              '--expected-revision',str(state['revision']),root=root)['workflow']
    for index, choice in enumerate(('Changes requested', 'Rejected')):
        old_checkpoint=w.current(state)['packet']['checkpoint']
        state=cli(workspace,host,'decide','--decision-json',json.dumps(decision(state,text=choice,candidate=('changes_requested' if index==0 else 'rejected'),event=f'correction-{index}')),
                  '--expected-revision',str(state['revision']),root=root)['workflow']
        for edit in range(2):
            data=json.loads((workspace/target).read_text())
            data['scope']=f'Correction {index}, edit {edit}'
            (workspace/target).write_text(json.dumps(data))
            report=cli(workspace,host,'report',root=root)['workflow']
            assert not report.get('invalidation_pending')
        state=cli(workspace,host,'submit','--output',target,'--tasks','tasks.json',
                  '--expected-revision',str(state['revision']),root=root)['workflow']
        assert any(row.get('packet',{}).get('checkpoint')==old_checkpoint for row in state['history'])


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_native_correction_resubmission(tmp_path, host):
    exercise_correction_cli(tmp_path/'workspace',host)


def exercise(workspace,host,root=ROOT):
    create(workspace)
    report=cli(workspace,host,'start','--scope','.taskplane/scope.json','--request-reference','test/user-request',root=root)
    state=report['workflow'];assert state['profile']=='native_workflow'
    for i,phase in enumerate(w.PHASES):
        assert w.current(state)['phase']==phase
        if phase=='build':(workspace/'app.py').write_text('value = 2\n')
        target=output(workspace,state)
        state=cli(workspace,host,'submit','--output',target,'--tasks','tasks.json',
                  '--expected-revision',str(state['revision']),root=root)['workflow']
        cli(workspace,host,'finish','--expected-revision',str(state['revision']),root=root,code=2)
        if phase=='retro':handoff(workspace,host,root=root)
        value=decision(state,event='test-human-'+phase)
        bad=deepcopy(value);bad['source']['automatic']=True
        cli(workspace,host,'decide','--decision-json',json.dumps(bad),
            '--expected-revision',str(state['revision']),root=root,code=2)
        state=cli(workspace,host,'decide','--decision-json',json.dumps(value),
                  '--expected-revision',str(state['revision']),root=root)['workflow']
        assert w.current(state)['decision']=='approved' and state['profile']=='native_workflow'
        if phase!='retro':
            cli(workspace,host,'progress','--phase',w.PHASES[i+1],
                '--expected-revision',str(state['revision']),root=root,code=2)
            state=cli(workspace,host,'advance','--phase',w.PHASES[i+1],
                      '--expected-revision',str(state['revision']),root=root)['workflow']
        else:
            state=cli(workspace,host,'finish','--expected-revision',str(state['revision']),root=root)['workflow']
    assert state['finished'] and len(state['decisions'])==7
    assert all(d['assurance']=='observed' for d in state['decisions'].values())
    assert not (workspace/'.taskplane/flow-events.jsonl').exists()
    cli(workspace,host,'--run',state['run'],root=root,entry='dashboard')
    page=(workspace/'.taskplane/dashboard.html').read_text()
    assert 'Taskplane' in page and state['run'] in page
    assert 'Dependency graph' in page and 'Task decomposition' in page
    restarted=cli(workspace,host,'report','--run',state['run'],root=root)['workflow']
    assert restarted['status']=='accepted' and restarted['coverage']['process_census']=='unknown'
    return state


def exercise_state_repairs(workspace, host, root=ROOT):
    """Run both repaired state invariants against the selected actual runtime."""
    create(workspace)
    state = cli(workspace,host,'start','--scope','.taskplane/scope.json',
                '--request-reference','test/size-limit',root=root)['workflow']
    # Revalidate the newly selected parent source before taking the authority
    # baseline, so the rejected submit must preserve its entire pinned proof.
    cli(workspace,host,'report',root=root)
    target = output(workspace,state)
    small = (workspace/target).read_bytes()
    out = json.loads(small)
    # Exceed the real cap even with compact output; escaped UTF-8 byte accounting
    # must refuse before replacing the previous readable state.
    out['supporting_notes'] = '\u03bb' * (8 * 1024 * 1024 // 6 + 1)
    (workspace/target).write_text(json.dumps(out))
    # Resolve the actual selected runtime's store path without a source fallback.
    path_probe = subprocess.run([sys.executable,'-I','-B','-c',
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
    def authority(raw):
        value = json.loads(raw)
        # Hook admissions/readiness are observational commits. Their storage
        # generation advances even when the submitted controller mutation is
        # refused; decisions, revision and pinned proof must remain identical.
        value.pop('storage_generation', None)
        value.pop('admissions', None)
        for state in value.get('runs', {}).values():
            state.pop('parent_hook_readiness', None)
        return value
    after = store.read_bytes()
    assert json.loads(after)['storage_generation'] > json.loads(before)['storage_generation']
    assert authority(after) == authority(before)
    resumed = cli(workspace,host,'report',root=root)['workflow']
    assert resumed['run'] == state['run'] and resumed['revision'] == state['revision']
    (workspace/target).write_bytes(small)
    accepted = cli(workspace,host,'submit','--output',target,'--tasks','tasks.json',
                   '--expected-revision',str(state['revision']),root=root)['workflow']
    assert w.current(accepted)['decision'] == 'awaiting_human_approval'
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
    child = subprocess.run([sys.executable,'-I','-B','-c',script,str(root),str(replay_ws),host],
                           cwd=workspace.parent,capture_output=True,text=True)
    assert child.returncode == 0, child.stdout + child.stderr
    assert 'NGEM-F01 NGEM-F02 repaired state checks passed' in child.stdout
    assert cli(replay_ws,host,'report',root=root)['workflow']['run'] == b['run']


@pytest.mark.parametrize('host',['codex','claude'])
def test_native_state_repairs_through_production_interfaces(tmp_path,host):
    exercise_state_repairs(tmp_path.resolve()/'native',host)


def exercise_repeated_repair_capacity(workspace, host, root=ROOT, *, stress=True):
    """Retain eleven real checkpoints across two accepted repair routes."""
    create(workspace)
    state = cli(workspace, host, 'start', '--scope', '.taskplane/scope.json',
                '--request-reference', 'fixture/repeated-repair-capacity', root=root)['workflow']
    # Resolve the store with the selected archive runtime, never a source adapter.
    probe = subprocess.run([sys.executable, '-I', '-B', '-c',
        "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "from taskplane import workflow_host as h; "
        "assert Path(h.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()); "
        "c=h.Controller(Path(sys.argv[2]),'root',h.installed_adapter(sys.argv[3])); "
        "print(c.adapter.control_path(c.workspace,c.root))", str(root), str(workspace), host],
        capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    store = Path(probe.stdout.strip())
    previous_packets, previous_decisions = {}, {}
    visits = ['product', 'design', 'plan', 'build', 'evaluate', 'build', 'evaluate',
              'build', 'evaluate', 'engineering', 'retro']
    largest_pretty, largest_compact = 0, 0
    for index, phase in enumerate(visits):
        assert w.current(state)['phase'] == phase
        change = {'kind':'repair', 'findings':['fixture-capacity'],
                  'reason':'Retain observed evidence during a repeated repair',
                  'proposed_paths':['app.py']} if phase == 'evaluate' and index < 8 else None
        target = output(workspace, state, change)
        out = json.loads((workspace/target).read_text())
        # Each checkpoint fits independently. Repeated structured evidence and
        # retained graph/task contexts push the old indented store past 8 MiB.
        out['retained_check_details'] = [
            {'check':f'fixture-{i}', 'observations':{'status':'pass', 'detail':['retained', 'é']}}
            for i in range(3000 if stress else 3)]
        (workspace/target).write_text(json.dumps(out))
        state = cli(workspace, host, 'submit', '--output', target, '--tasks', 'tasks.json',
                    '--expected-revision', str(state['revision']), root=root)['workflow']
        state = cli(workspace, host, 'decide', '--decision-json',
                    json.dumps(decision(state, event=f'fixture-capacity-{index}')),
                    '--expected-revision', str(state['revision']), root=root)['workflow']
        for key, packet in previous_packets.items():
            assert next(v['packet'] for v in state['visits'] if v['id'] == key) == packet
        assert all(state['decisions'][key] == value for key, value in previous_decisions.items())
        previous_packets = {v['id']:v['packet'] for v in state['visits'] if v['packet']}
        previous_decisions = deepcopy(state['decisions'])
        persisted = json.loads(store.read_bytes())
        pretty = (json.dumps(persisted, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()
        compact = (json.dumps(persisted, sort_keys=True, separators=(',', ':'),
                              ensure_ascii=True, allow_nan=False) + '\n').encode()
        largest_pretty = max(largest_pretty, len(pretty))
        largest_compact = max(largest_compact, len(compact))
        if len(pretty) > 8 * 1024 * 1024:
            assert store.read_bytes() == compact
        if index == 0:
            # A pre-existing indented store stays readable; its next ordinary
            # transition compacts it without changing checkpoint/decision data.
            store.write_bytes(pretty)
            assert cli(workspace, host, 'report', root=root)['workflow']['decisions'] == previous_decisions
        state = cli(workspace, host, 'advance' if index < len(visits)-1 else 'finish',
                    *(['--phase', visits[index+1]] if index < len(visits)-1 else []),
                    '--expected-revision', str(state['revision']), root=root)['workflow']
        resumed = cli(workspace, host, 'report', '--run', state['run'], root=root)['workflow']
        assert resumed['revision'] == state['revision'] and not resumed.get('invalidation_pending')
    assert largest_compact < 8 * 1024 * 1024
    if stress:
        assert largest_pretty > 8 * 1024 * 1024
    assert state['finished'] and len(state['decisions']) == len(previous_packets) == 11
    assert sum('route_change' in item for item in state['history']) == 2
    if os.name == 'posix':
        assert store.stat().st_mode & 0o777 == 0o600
    return {'host':host, 'checkpoints':11, 'repair_routes':2, 'largest_pretty_bytes':largest_pretty,
            'largest_compact_bytes':largest_compact, 'prior_packets_and_decisions_preserved':True}


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_native_repeated_repairs_preserve_checkpoint_history(tmp_path, host):
    # Routine integration checks all eleven transitions and both repair routes.
    # The capacity suite repeats them with the actual >8 MiB historical payload.
    exercise_repeated_repair_capacity((tmp_path/'repairs').resolve(), host, stress=False)


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.taskplane_capacity
def test_native_repeated_repair_history_fits_without_losing_evidence(tmp_path, host):
    exercise_repeated_repair_capacity((tmp_path/'repairs').resolve(), host)


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
    assert cli(ws,'codex','finish','--expected-revision',str(state['revision']))['workflow']['finished']


def test_engineering_findings_can_start_product_with_accepted_extension(tmp_path):
    ws=tmp_path.resolve();scope=create(ws)
    state=cli(ws,'codex','start','--phase','engineering','--standalone','--scope','.taskplane/scope.json',
              '--request-reference','test/findings')['workflow']
    target=output(ws,state,{'kind':'delivery','scope':scope})
    state=cli(ws,'codex','submit','--output',target,'--tasks','tasks.json','--expected-revision',str(state['revision']))['workflow']
    state=cli(ws,'codex','decide','--decision-json',json.dumps(decision(state)),
              '--expected-revision',str(state['revision']))['workflow']
    state=cli(ws,'codex','advance','--phase','product','--expected-revision',str(state['revision']))['workflow']
    assert w.current(state)['phase']=='product' and state['visits'][0]['superseded']
    assert state['history'][-1]['route_change']['kind']=='delivery'


def test_native_start_needs_scope_and_never_upgrades_legacy_observations(tmp_path):
    ws=tmp_path.resolve();create(ws)
    assert cli(ws,'codex','start',code=2)['reason']=='invalid_evidence'
    assert cli(ws,'codex','start','--profile','protected_host',code=2)['reason']=='unsupported_authority'
    from taskplane import flow
    flow.append(ws,{'kind':'start','run':'old','session':'root','phase':'retro'})
    flow.append(ws,{'kind':'finish','run':'old','session':'root','phase':'retro'})
    assert cli(ws,'codex','report','--history','--run','old')['status']=='legacy_unverified'
    state=cli(ws,'codex','start','--scope','.taskplane/scope.json','--request-reference','test/new')['workflow']
    assert state['run']!='old' and w.current(state)['phase']=='product' and state['decisions']=={}


def test_phase_usage_reconciles_work_review_and_resume_without_double_counting():
    from taskplane import flow
    from taskplane.tests.test_workflow_evidence import prepare
    # Pure observation fixtures: values are cumulative, not independent token bills.
    scope={'criteria':['AC1'],'paths':{p:[] for p in w.PHASES}}
    state=w.new_state('/fixture','root','usage-run',scope)
    def measurement(value):
        native={'input_tokens':100+value,'cached_input_tokens':80,'uncached_input_tokens':20+value,
                'output_tokens':10,'reasoning_tokens':2,'total_tokens':110+value}
        usage={'input_tokens':value,'cached_input_tokens':0,'uncached_input_tokens':value,
               'output_tokens':0,'reasoning_tokens':0,'total_tokens':value}
        return {'sessions':[{'session':'root','role':'orchestrator','status':'measured',
                             'native_usage':native,'usage':usage}],
                'tokens':usage,'token_coverage':{}}
    points=[flow.usage_point(state,measurement(0))]
    state['revision']=1;state['visits'][0]['decision']='awaiting_human_approval'
    points.append(flow.usage_point(state,measurement(10),previous_revision=0))
    state['revision']=2;state['visits'][0]['decision']='approved'
    points.append(flow.usage_point(state,measurement(15),previous_revision=1))
    state['revision']=3;state['index']=1
    points.append(flow.usage_point(state,measurement(20),previous_revision=2))
    product=state['visits'][0]['id'];design=state['visits'][1]['id']
    result=flow.phase_usage(points,state,measurement(30))
    assert result['status']=='measured'
    assert result['visits'][product]['buckets']['work']['total_tokens']==10
    assert result['visits'][product]['buckets']['review']['total_tokens']==10
    assert result['visits'][design]['tokens']['total_tokens']==10
    assert result['attributed']['total_tokens']==30 and result['unallocated']['total_tokens']==0
    # Lost committed-boundary observation cannot allocate that interval to an invented phase.
    lost=flow.phase_usage([points[0],points[3]],state,measurement(30))
    assert lost['status']=='partial' and lost['unallocated']['total_tokens']==20
    assert any('Missing transition' in g for g in lost['gaps'])
    # A separately observed repair visit aggregates without overwriting its earlier visit.
    state['visits'].append(w.visit('design'));state['index']=len(state['visits'])-1;state['revision']=4
    repeated=points+[flow.usage_point(state,measurement(30),previous_revision=3)]
    result=flow.phase_usage(repeated,state,measurement(35))
    assert result['phases']['design']['tokens']['total_tokens']==15
    assert len(result['visits'])==3


def test_phase_usage_missing_reset_partial_and_late_child_are_explicit():
    from taskplane import flow
    state=w.new_state('/fixture','root','usage-run',{'criteria':['A'],'paths':{p:[] for p in w.PHASES}})
    def measured(value):
        return {'sessions':[{'session':'root','role':'orchestrator','status':'measured','native_usage':{'total_tokens':value}}],
                'tokens':{'total_tokens':value-100},'token_coverage':{}}
    initial=flow.usage_point(state,measured(100))
    missing=flow.phase_usage([],state,measured(110))
    assert missing['status']=='unknown' and missing['unallocated']['total_tokens']==10
    reset=flow.phase_usage([initial],state,measured(5))
    assert reset['status']=='partial' and reset['unallocated'] is None and not reset['attributed']
    child=measured(110);child['tokens']['total_tokens']=17
    child['sessions'].append({'session':'child','role':'lens','status':'measured','native_usage':{'total_tokens':7}})
    late=flow.phase_usage([initial],state,child)
    assert late['attributed']['total_tokens']==10 and late['unallocated']['total_tokens']==7
    assert any('child: missing' in g for g in late['gaps'])
    child['sessions'][0]['status']='recorded; native counter unavailable'
    assert flow.phase_usage([initial],state,child)['status']=='partial'


def test_snapshot_run_selection_publication_and_captured_graph(tmp_path,monkeypatch):
    from taskplane import flow, workflow_host as host
    from taskplane.tests.test_workflow_local import setup, submit, decide
    c,s=setup(tmp_path,standalone=True)
    monkeypatch.setenv('CODEX_THREAD_ID','root')
    flow.append(c.workspace,{'kind':'start','session':'root','run':s['run'],'phase':'product','at':s['started_at'],'goal':'first run'})
    graph=depgraph.load(str(c.workspace));flow.record_scan(c.workspace,graph)
    s=decide(c,submit(c,s));s=c.apply('finish',s['run'],expected_revision=s['revision'])
    first=flow.report(c.workspace,s['run'],governor=c)
    assert first['historical'] and first['graph']['status']=='at checkpoint'
    target=flow.publish_dashboard(c.workspace,s['run'],select=True,governor=c)
    old=target.read_bytes()
    frozen={p:p.read_bytes() for p in (c.workspace/'.taskplane').glob('snapshot-*.html')}
    new=host.Controller(c.workspace,'other-task',host.installed_adapter('codex'))
    other=new.start({'scope':s['scope'],'goal':'second run','request_reference':'test/second-task'})
    flow.append(c.workspace,{'kind':'start','session':'other-task','run':other['run'],'phase':'product','at':other['started_at'],'goal':'second run'})
    # The current task remains on its own run, even with a later unrelated start.
    assert flow.report(c.workspace, s['run'])['run']==s['run']
    detached=flow.publish_dashboard(c.workspace,other['run'],governor=new)
    assert detached!=target and target.read_bytes()==old
    selected=flow.publish_dashboard(c.workspace,other['run'],select=True,governor=new)
    assert selected==target and other['run'] in target.read_text()
    assert 'second run' in target.read_text() and 'Static snapshot' in target.read_text()
    saved=list((c.workspace/'.taskplane').glob('snapshot-*.json'))
    assert len(saved)>=2
    original_graph=first['graph']['fingerprint']
    (c.workspace/'unrelated.py').write_text('import json\n')
    depgraph.scan(str(c.workspace),decompose=True)
    historical=flow.report(c.workspace,s['run'],governor=c)
    assert historical['graph']['fingerprint']==original_graph
    assert all(path.read_bytes()==raw for path,raw in frozen.items())
    assert historical['tasks']==first['tasks']
    current=flow.report(c.workspace,other['run'],governor=new)
    assert current['graph']['status']=='unverified or stale'
    # No run in an unbound task is preferable to silently showing someone else's latest work.
    monkeypatch.setenv('CODEX_THREAD_ID','unbound-task')
    assert flow.report(c.workspace) is None


def test_graph_receipt_rejects_foreign_workspace_and_dirty_source(tmp_path):
    from taskplane import flow
    from taskplane.tests.test_workflow_local import setup
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    ca,sa=setup(a);cb,sb=setup(b)
    graph=depgraph.scan(str(a),decompose=True,strict=True);flow.record_scan(a,graph)
    assert flow.graph_view(a,sa,False)['status']=='current'
    (a/'app.py').write_text('import os\n')
    assert flow.graph_view(a,sa,False)['status']=='unverified or stale'
    (b/'.taskplane/graph-receipt.json').write_bytes((a/'.taskplane/graph-receipt.json').read_bytes())
    assert flow.graph_view(b,sb,False)['status']=='unverified or stale'


def test_snapshot_refuses_inconsistent_revision_without_replacing_entry(tmp_path,monkeypatch):
    from taskplane import flow
    from taskplane.tests.test_workflow_local import setup
    c,s=setup(tmp_path)
    flow.append(c.workspace,{'kind':'start','session':'root','run':s['run'],'phase':'product','at':s['started_at']})
    good=flow.report(c.workspace,s['run'],governor=c)
    target=flow.publish_dashboard(c.workspace,s['run'],governor=c,supplied=good,select=True)
    before=target.read_bytes()
    stale=deepcopy(good);stale['workflow']['revision']+=1
    monkeypatch.setattr(flow,'report',lambda *a,**k:deepcopy(stale))
    with pytest.raises(w.Refusal,match='changed during publication'):
        flow.publish_dashboard(c.workspace,s['run'],governor=c)
    assert target.read_bytes()==before


def exercise_dashboard_publication_order(c, state, *, select=False):
    """Delay an actual renderer while a newer same-revision snapshot publishes."""
    from threading import Event, Thread
    from unittest.mock import patch
    from taskplane import flow, flow_dashboard, live_telemetry
    workspace=c.workspace
    older=flow.report(workspace,state['run'],governor=c);older['tokens']={'total_tokens':100}
    entered=Event();release=Event();errors=[];published=[]
    original_render=flow_dashboard.render
    def delayed(ws,model):
        if model is older:
            entered.set();assert release.wait(10)
        return original_render(ws,model)
    def old_writer():
        try:published.append(flow.publish_dashboard(workspace,state['run'],governor=c,supplied=older,select=select))
        except BaseException as exc:errors.append(repr(exc))
    with patch.object(flow_dashboard,'render',delayed):
        writer=Thread(target=old_writer);writer.start();assert entered.wait(10)
        try:
            live_telemetry.progress(workspace,state,'progress','Newer observation',{}, {})
            newer=flow.report(workspace,state['run'],governor=c);newer['tokens']={'total_tokens':200}
            assert older['snapshot']['revision']==newer['snapshot']['revision']
            target=flow.publish_dashboard(workspace,state['run'],governor=c,supplied=newer,select=True)
            latest=target.read_bytes()
        finally:release.set();writer.join(10)
    assert not writer.is_alive() and not errors,errors
    assert target.read_bytes()==latest, 'Older writer replaced newer dashboard'
    assert published[0]!=target and published[0].is_file(), 'Older snapshot must remain independently accessible'
    selected=json.loads(target.with_suffix('.selection.json').read_text())
    model=json.loads(Path(selected['snapshot']).with_suffix('.json').read_text())
    assert selected['digest']==newer['snapshot']['digest']==model['snapshot']['digest']
    assert any(m['note']=='Newer observation' for m in model['milestones'])
    assert model['tokens']['total_tokens']==200
    assert 'Run tokens</span><b>200' in target.read_text()
    return target, model


@pytest.mark.parametrize('select',[False,True])
def test_delayed_same_revision_publisher_keeps_newest_snapshot(tmp_path,select):
    from taskplane.tests.test_workflow_local import setup
    c,state=setup(tmp_path)
    exercise_dashboard_publication_order(c,state,select=select)


@pytest.mark.parametrize('case',['slow_reader','later_counter_read','older_journal','legacy_selection'])
def test_publication_orders_observations_without_a_phase_revision(tmp_path,case):
    from taskplane import flow
    from taskplane.tests.test_workflow_local import setup
    c,state=setup(tmp_path)
    flow.append(tmp_path,{'kind':'start','run':state['run'],'session':'root','phase':'product','at':state['started_at']})
    newer=flow.report(tmp_path,state['run'],governor=c)
    newer['snapshot'].update(captured_at='2026-09-18T00:02:00+00:00',generated_at='2026-09-18T00:02:01+00:00',observation_count=2)
    newer['tokens']={'total_tokens':200}
    target=flow.publish_dashboard(tmp_path,state['run'],governor=c,supplied=newer,select=True)
    incoming=deepcopy(newer);incoming['tokens']={'total_tokens':100}
    incoming['snapshot'].update(captured_at='2026-09-18T00:01:00+00:00',generated_at='2026-09-18T00:03:00+00:00')
    if case=='older_journal':incoming['snapshot'].update(observation_count=1,captured_at='2026-09-18T00:04:00+00:00')
    if case=='later_counter_read':incoming['snapshot']['captured_at']='2026-09-18T00:04:00+00:00'
    if case=='legacy_selection':
        selection=target.with_suffix('.selection.json');value=json.loads(selection.read_text())
        value.pop('captured_at',None);value.pop('observation_count',None);selection.write_text(json.dumps(value))
    before=target.read_bytes()
    flow.publish_dashboard(tmp_path,state['run'],governor=c,supplied=incoming,select=True)
    assert (target.read_bytes()!=before)==(case=='later_counter_read')


def test_optional_graph_failure_preserves_committed_decision(tmp_path, monkeypatch, capsys):
    from taskplane import flow
    from taskplane.tests.test_workflow_local import setup, submit, decision
    c,state=setup(tmp_path);state=submit(c,state)
    monkeypatch.setattr(flow.depgraph,'load',lambda *_: (_ for _ in ()).throw(OSError('graph unavailable')))
    assert flow.main(['decide','--workspace',str(tmp_path),'--decision-json',json.dumps(decision(state)),
                     '--expected-revision',str(state['revision'])],governor=c)==0
    result=json.loads(capsys.readouterr().out)
    assert w.current(result['workflow'])['decision']=='approved'
    assert flow.graph_view(tmp_path,c.report(),False)['status']=='unavailable'
    assert c.report()['status']=='approved'


def exercise_counter_freshness(workspace, host, root=ROOT):
    """EV-F01: retain saved data time, or report unsupported native inventory."""
    from unittest.mock import patch
    from taskplane.tests.test_native_session_meter import _write_segment
    from taskplane.tests.test_claude_flow import message, write
    create(workspace)
    logs=workspace.parent/(workspace.name+'-logs');logs.mkdir()
    path=logs/'sessions/root.jsonl' if host=='codex' else logs/'projects/fixture/root.jsonl'
    path.parent.mkdir(parents=True)
    environment={'CODEX_HOME':str(logs)} if host=='codex' else {'TASKPLANE_CLAUDE_TRANSCRIPT':str(path)}
    def observe(row):
        env={k:v for k,v in os.environ.items() if not k.startswith(('CODEX_','CLAUDE_','TASKPLANE_','PLUGIN_ROOT'))}
        env.update(environment)
        env['CODEX_THREAD_ID' if host=='codex' else 'TASKPLANE_CLAUDE_SESSION_ID']='root'
        result=subprocess.run([sys.executable,'-I','-B','-c',
            "import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "from taskplane import flow; "
            "assert Path(flow.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()); "
            "row=json.loads(sys.argv[3]); "
            "row.update(flow.counter({'session_id':'root','transcript_path':sys.argv[4]},'root')) "
            "if sys.argv[5]=='claude' and row['kind']=='start' else None; "
            "flow.append(Path(sys.argv[2]),row)",str(root),str(workspace),json.dumps(row),str(path),host],
            env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    with patch.dict(os.environ,environment):
        if host=='codex':_write_segment(path,session_id='root',total=100)
        else:write(path,[message()])
        started=cli(workspace,host,'start','--scope','.taskplane/scope.json','--request-reference','test/EV-F01',root=root,environment=environment)
        initial=cli(workspace,host,'report',root=root,environment=environment)
        baseline=initial['sessions'][0]['native_usage']
        observe({'kind':'start','session':'root','run':started['run'],'phase':'product',
                 'at':started['workflow']['started_at'],'usage':baseline,'usage_status':'observed',
                 'host':host,'transcript_path':str(path) if host=='claude' else None})
        if host=='codex':
            counter=json.loads(path.read_text().splitlines()[-1]);counter['ordinal']=8
            counter['timestamp']='2026-09-01T00:00:08Z'
            counter['payload']['info']['total_token_usage'].update(input_tokens=129,total_tokens=130)
            with path.open('a') as stream:stream.write(json.dumps(counter)+'\n')
        else:write(path,[message(),message('m2')])
        cli(workspace,host,'progress','--phase','product','--note','Observed fixture interval',root=root,environment=environment)
        measured=cli(workspace,host,'report',root=root,environment=environment)
        observe({'kind':'usage','run':started['run'],'session':'root','measurement':{'sessions':measured['sessions']}})
        measured_at=measured['sessions'][0]['measured_at']
        unsupported_inventory = host == 'codex' and sys.platform not in ('darwin', 'linux')
        if not unsupported_inventory:
            assert measured['tokens']['total_tokens']==(30 if host=='codex' else 142)
        path.unlink()
        saved=cli(workspace,host,'report','--run',initial['run'],root=root,environment=environment)
        if unsupported_inventory:
            # The real CLI cannot discover Codex counters on this host. Neither
            # progress nor saved reports may invent a measurement or its time.
            for result in (initial, measured, saved):
                assert result['tokens'] is None and result['native_tokens'] is None
                session = result['sessions'][0]
                assert session['usage'] is None and session['native_usage'] is None
                assert session['status'] == 'partial' and session['attribution_unknown']
                assert session['measured_at'] is None
                refresh = result['usage_refresh']
                assert not refresh['complete'] and not refresh['fully_discovered']
                assert 'native_directory_accounting_unavailable' in refresh['discovery_errors']
                assert refresh['directory_bytes'] == refresh['discovery_entries'] == 0
                clock = result['usage_measurement']
                assert clock['status'] == 'unavailable'
                assert clock['measured_at'] is clock['oldest_at'] is clock['newest_at'] is None
                assert result['snapshot']['measurement_at'] is None
                assert result['phase_usage']['measurement_at'] is None
            return saved
        assert saved['sessions'][0]['status'].startswith('recorded')
        assert saved['sessions'][0]['measured_at']==measured_at
        expected_time=None if saved['token_coverage']['discovery_errors'] else measured_at
        assert saved['snapshot']['measurement_at']==expected_time
        assert saved['usage_measurement']['status']==('partial' if expected_time is None else 'recorded')
        assert saved['usage_measurement']['oldest_at']==saved['usage_measurement']['newest_at']==measured_at
        assert saved['usage_measurement']['attempted_at']>measured_at
        assert saved['phase_usage']['measurement_at']==expected_time
        return saved


@pytest.mark.parametrize('host',['codex','claude'])
def test_saved_counters_keep_their_measurement_time(tmp_path,host):
    exercise_counter_freshness(tmp_path/'workspace',host)


@pytest.mark.parametrize('case',['fresh','recorded','mixed','missing','reset','unknown_time','discovery'])
def test_measurement_clock_distinguishes_coverage(tmp_path,monkeypatch,case):
    from taskplane import flow
    from taskplane.tests.test_workflow_local import setup
    c,state=setup(tmp_path)
    old='2026-09-01T00:00:00+00:00'
    def session(sid,status,stamp=old):
        return {'session':sid,'agent':sid,'role':'orchestrator' if sid=='root' else 'lens',
                'usage':{'total_tokens':30},'native_usage':{'total_tokens':130},
                'status':status,'measured_at':stamp}
    sessions=[session('root','measured')]
    if case=='recorded':sessions=[session('root','recorded; native counter unavailable')]
    if case=='mixed':sessions.append(session('child','recorded; native counter unavailable'))
    if case=='missing':sessions[0].update(status='unavailable',usage=None,native_usage=None)
    if case=='reset':sessions[0]['native_usage']={'total_tokens':1}
    if case=='unknown_time':sessions=[session('root','recorded; native counter unavailable',None)]
    from taskplane import live_telemetry
    live_telemetry.initialize(tmp_path, state, {'usage': {'total_tokens': 100}})
    monkeypatch.setattr(flow.flow_usage,'reconcile',lambda *_,**kw:{'sessions':deepcopy(sessions),'tokens':{'total_tokens':30},'token_coverage':{'discovery_errors':1 if case=='discovery' else 0}})
    result=flow.report(tmp_path,state['run'],governor=c)
    clock=result['usage_measurement']
    expected={'fresh':'fresh','recorded':'recorded','mixed':'mixed','missing':'unavailable','reset':'unavailable','unknown_time':'partial','discovery':'partial'}[case]
    assert clock['status']==expected
    if case=='fresh':assert clock['measured_at']==clock['attempted_at']
    elif case=='recorded':assert clock['measured_at']==old
    else:assert clock['measured_at'] is None
    if case=='mixed':
        assert clock['oldest_at']==old and clock['newest_at']==clock['attempted_at']
    if case in ('missing','reset'):assert result['sessions'][0]['measured_at'] is None
    assert result['snapshot']['measurement_at']==clock['measured_at']


def exercise_harness_entry(workspace,host,phase='engineering',root=ROOT,*,prompt=None,standalone=True):
    """Start with declared hooks before fixture evidence or skill/start calls."""
    import shlex,shutil
    workspace.mkdir(parents=True,exist_ok=True)
    hooks=json.loads((root/'hooks/hooks.json').read_text())['hooks']
    env={k:v for k,v in os.environ.items() if not k.startswith(('CODEX_','CLAUDE_','TASKPLANE_','PLUGIN_ROOT'))}
    host_data=workspace.parent/(workspace.name+'-host-data')
    env.update(CODEX_HOME=str(host_data/'codex'),CLAUDE_CONFIG_DIR=str(host_data/'claude'))
    env.update(PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'])
    bash=shutil.which('bash')
    if os.name == 'nt':
        # Windows' bash.exe may be a WSL launcher with no distribution installed.
        git=Path(shutil.which('git') or '')
        bash=next((str(parent/'bin/bash.exe') for parent in git.parents
                   if (parent/'bin/bash.exe').is_file()),None)
    if host=='claude':env.update(CLAUDE_PLUGIN_ROOT=str(root),CLAUDE_ENV_FILE=str(workspace/'.taskplane/fixture-env'))
    else:env.update(PLUGIN_ROOT=str(root),CLAUDE_PLUGIN_ROOT=str(root),CODEX_THREAD_ID='root')
    identity={'session_id':'root'} if host=='claude' else {'thread_id':'root'}
    def hook(event,**payload):
        data={'hook_event_name':event,'cwd':str(workspace),'transcript_path':str(workspace/'fixture-log.jsonl'),**identity,**payload}
        proc=subprocess.run(hooks[event][0]['hooks'][0]['commandWindows' if os.name == 'nt' else 'command'],shell=True,cwd=workspace,env=env,
                            input=json.dumps(data),capture_output=True,text=True)
        assert proc.returncode==0,(proc.stdout,proc.stderr)
        return json.loads(proc.stdout)
    def command(*args,code=0,cli_entry='flow'):
        launcher=['py','-3'] if os.name == 'nt' else [sys.executable]
        words=[*launcher,str(root/'taskplane/tp.py'),cli_entry,*args,'--workspace',str(workspace)]
        if cli_entry == 'flow': words.append('--full')
        key='command' if host=='claude' else 'cmd';tool='Bash' if host=='claude' else 'exec_command'
        before=hook('PreToolUse',tool_name=tool,tool_input={key:shlex.join(words)})
        assert before.get('hookSpecificOutput',{}).get('permissionDecision')!='deny',before
        if host=='claude':
            # Exercise the actual SessionStart exports rather than injecting a test root.
            assert bash, 'The Claude adapter fixture requires Git Bash on Windows'
            argv=[bash,'-c','. "$1"; shift; exec "$@"','fixture','.taskplane/fixture-env',*words]
        else:argv=words
        proc=subprocess.run(argv,cwd=workspace,env=env,capture_output=True,text=True)
        assert proc.returncode==code,(proc.stdout,proc.stderr)
        return json.loads(proc.stdout) if cli_entry == 'flow' else proc.stdout.strip()
    assert hook('SessionStart')=={}
    prompt=prompt or {'engineering':'Use taskplane to review this code without a delivery flow.',
            'design':'Use taskplane to design this change.', 'product':'Use taskplane to define product requirements.'}[phase]
    selected=hook('UserPromptSubmit',prompt=prompt)
    # Natural language routing advice creates neither a workflow nor consent.
    assert not list((workspace/'.taskplane').glob('workflow-*.json'))
    if phase is None:
        return selected
    create(workspace)
    (workspace/'.taskplane/dashboard.html').unlink()
    state=command('start',*(['--standalone'] if standalone else []),'--phase',phase,
                  '--scope','.taskplane/scope.json','--request-reference','fixture/actual-entry-request')['workflow']
    assert state['root']=='root' and w.current(state)['phase']==phase
    assert len(state['visits'])==(1 if standalone else 7)
    assert not state.get('approval_policy') and not state['decisions']
    assert state['run'] in (workspace/'.taskplane/dashboard.html').read_text()
    patch='*** Begin Patch\n*** Update File: app.py\n@@\n-value = 1\n+value = 2\n*** End Patch'
    payload={'tool_name':'Write','tool_input':{'file_path':str(workspace/'app.py'),'content':'value=2'}} if host=='claude' else {
        'tool_name':'apply_patch','tool_input':{'command':patch}}
    assert hook('PreToolUse',**payload)['hookSpecificOutput']['permissionDecision']=='deny'
    assert 'permissionDecision' not in hook('PreToolUse',tool_name='Read',tool_input={
        'file_path':str(workspace/'app.py')}).get('hookSpecificOutput',{})
    target=output(workspace,state)
    state=command('submit','--output',target,'--tasks','tasks.json',
                  '--expected-revision',str(state['revision']))['workflow']
    assert w.current(state)['decision']=='awaiting_human_approval' and not state['decisions']
    # A useful pending result can be resumed without a display or hook-pair receipt.
    report=command('report','--run',state['run'])
    assert report['workflow']['run']==state['run'] and not report['workflow']['decisions']
    resumed=hook('SessionStart',source='resume')
    assert phase in resumed['hookSpecificOutput']['additionalContext']
    assert command('report','--run',state['run'])['workflow']['revision']==state['revision']
    return state


@pytest.mark.parametrize('host',['codex','claude'])
@pytest.mark.parametrize('phase',['product','design','engineering'])
def test_declared_hooks_preserve_standalone_scope_and_approval(tmp_path,host,phase):
    exercise_harness_entry((tmp_path/'workspace').resolve(),host,phase)


def shipped_execution_prompts():
    """Use the actual UI/documentation contract instead of test-only phrasings."""
    prompts=json.loads((ROOT/'.codex-plugin/plugin.json').read_text())['interface']['defaultPrompt']
    assert len(prompts)==3, 'Give every shipped menu prompt an explicit route expectation'
    assert all(len(prompt)<=128 for prompt in prompts), 'Codex ignores menu prompts longer than 128 characters'
    return [('menu-design',prompts[0],'design',True),
            ('menu-build',prompts[1],'product',False),
            ('menu-review',prompts[2],'engineering',True)]



@pytest.mark.parametrize('host',['codex','claude'])
@pytest.mark.parametrize('name,prompt,phase,standalone',shipped_execution_prompts(),
                         ids=[row[0] for row in shipped_execution_prompts()])
def test_shipped_execution_prompts_preserve_requested_route(tmp_path,host,name,prompt,phase,standalone):
    exercise_harness_entry((tmp_path/name).resolve(),host,phase,prompt=prompt,standalone=standalone)
