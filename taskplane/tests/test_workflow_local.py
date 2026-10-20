"""Real local adapter tests; observations are cooperative, not host attestation."""
from copy import deepcopy
import json
import os
import pytest
from taskplane import workflow as w, workflow_host as h, workflow_local as local
from taskplane.tests.test_workflow_evidence import prepare


def binding_runtime_available():
    return (os.open in os.supports_dir_fd and hasattr(os, 'O_DIRECTORY')
            and hasattr(os, 'O_NOFOLLOW'))


def setup(workspace, *, entry="product", standalone=False):
    workspace=workspace.resolve()
    state, _, _=prepare(workspace)
    (workspace/"build-check.txt").write_text("Fixture Build check passed")
    (workspace/"check.txt").write_text("Observed fixture check result")
    c=h.Controller(workspace,"root",h.installed_adapter("codex"))
    s=c.start({"scope":state["scope"],"request_reference":"conversation/user-1",
               "entry":entry,"standalone":standalone})
    from taskplane.tests.test_worker_runtime import parent_pair
    parent_pair(c)
    return c,c.report(s['run'], diagnostics=False)


def displaced_database(tmp_path, *, approved=False):
    import hashlib
    c, state = setup(tmp_path)
    if approved:
        state = decide(c, submit(c, state))
    target = c._path()
    raw = target.read_bytes()
    candidate = target.with_name(target.stem + ' 2.json')
    candidate.write_bytes(raw)
    target.unlink()
    return c, state, target, candidate, raw, hashlib.sha256(raw).hexdigest()


def test_numbered_database_recovery_preserves_exact_history_and_is_idempotent(tmp_path):
    c, state, target, candidate, raw, checksum = displaced_database(tmp_path, approved=True)
    marker = target.with_name(target.stem + '.initialized.json')
    before = marker.read_bytes()
    with pytest.raises(w.Refusal, match='incomplete'):
        c.report()
    if not binding_runtime_available():
        with pytest.raises(w.Refusal, match='no-follow directory operations'):
            c.recover_initialization(candidate.name, checksum, state['run'], state['revision'], 'conversation/recovery')
        assert not target.exists() and candidate.read_bytes() == raw and marker.read_bytes() == before
        return
    result = c.recover_initialization(candidate.name, checksum, state['run'], state['revision'], 'conversation/recovery')
    assert result['status'] == 'restored' and result['approvals_changed'] is False
    assert target.read_bytes() == candidate.read_bytes() == raw and marker.read_bytes() == before
    current = c.report()
    assert current['revision'] == state['revision'] and current['decisions'] == state['decisions']
    assert current['visits'] == state['visits']
    assert c.recover_initialization(candidate.name, checksum, state['run'], state['revision'], 'conversation/retry')['status'] == 'already_restored'


@pytest.mark.parametrize('bad', ['hash', 'root', 'workspace', 'run', 'revision', 'boolean',
    'marker', 'missing_marker', 'corrupt', 'symlink', 'path', 'reference', 'existing', 'child'])
def test_numbered_database_recovery_refuses_without_changing_control_files(tmp_path, bad):
    import hashlib
    c, state, target, candidate, raw, checksum = displaced_database(tmp_path)
    marker = target.with_name(target.stem + '.initialized.json')
    source, run, revision, reference = candidate.name, state['run'], state['revision'], 'conversation/recovery'
    if bad == 'hash': checksum = '0' * 64
    elif bad in {'root', 'workspace'}:
        data = json.loads(raw); data[bad] = 'foreign'; candidate.write_text(json.dumps(data))
        checksum = hashlib.sha256(candidate.read_bytes()).hexdigest()
    elif bad == 'run': run = 'foreign'
    elif bad == 'revision': revision += 1
    elif bad == 'boolean': revision = True
    elif bad == 'marker': marker.write_text('{}')
    elif bad == 'missing_marker': marker.unlink()
    elif bad == 'corrupt':
        candidate.write_text('{'); checksum = hashlib.sha256(candidate.read_bytes()).hexdigest()
    elif bad == 'symlink':
        other = tmp_path/'other.json'; other.write_bytes(raw); candidate.unlink(); candidate.symlink_to(other)
    elif bad == 'path': source = '../' + source
    elif bad == 'reference': reference = ' '
    elif bad == 'existing': target.write_text('{}')
    else: c.principal = 'child'
    before = {p.name: p.read_bytes() for p in target.parent.glob('*.json')}
    with pytest.raises((w.Refusal, OSError, ValueError)):
        c.recover_initialization(source, checksum, run, revision, reference)
    assert {p.name: p.read_bytes() for p in target.parent.glob('*.json')} == before


def test_numbered_database_recovery_never_overwrites_a_racing_writer(tmp_path, monkeypatch):
    c, state, target, candidate, raw, checksum = displaced_database(tmp_path)
    marker = target.with_name(target.stem + '.initialized.json')
    before = marker.read_bytes()
    publications = []
    link = os.link
    def collision(source, destination, **kwargs):
        publications.append(destination)
        if destination == target.name:
            target.write_bytes(b'concurrent state')
            raise FileExistsError('fixture concurrent writer')
        return link(source, destination, **kwargs)
    monkeypatch.setattr(h.os, 'link', collision)
    with pytest.raises(w.Refusal) as caught:
        c.recover_initialization(candidate.name, checksum, state['run'], state['revision'], 'conversation/recovery')
    if not binding_runtime_available():
        assert caught.value.reason == 'workspace_binding'
        assert 'no-follow directory operations' in str(caught.value)
        assert not publications and not target.exists()
        assert candidate.read_bytes() == raw and marker.read_bytes() == before
        return
    assert isinstance(caught.value.__cause__, FileExistsError)
    assert target.read_bytes() == b'concurrent state' and candidate.read_bytes() == raw


@pytest.mark.parametrize('tool,key', [('Bash', 'command'), ('exec_command', 'cmd')])
@pytest.mark.parametrize('broken', ['missing', 'corrupt'])
def test_unavailable_state_keeps_exact_diagnostics_and_recovery_reachable(tmp_path, tool, key, broken, capsys):
    import shlex, sys
    from pathlib import Path
    from taskplane import flow
    c, state, target, candidate, raw, checksum = displaced_database(tmp_path)
    if broken == 'corrupt': target.write_text('{')
    runtime = [sys.executable, str(Path(flow.__file__).with_name('tp.py'))]
    diagnose = [*runtime, 'flow', 'diagnose', '--workspace', str(tmp_path)]
    recover = [*runtime, 'flow', 'recover', '--workspace', str(tmp_path), '--recover-from', candidate.name,
               '--expected-sha256', checksum, '--run', state['run'], '--expected-revision', str(state['revision']),
               '--request-reference', 'conversation/recovery']
    def event(words, **extra):
        return {'hook_event_name':'PreToolUse', 'cwd':str(tmp_path), 'session_id':'root',
                'tool_name':tool, 'tool_input':{key:shlex.join(words)}, **extra}
    before = candidate.read_bytes()
    for words in (diagnose, [*diagnose, '--full'], recover):
        assert flow.hook(event(words), governor=c).get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
    assert flow.main(['diagnose','--workspace',str(tmp_path)], compact=True, governor=c) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['schema'] == 'taskplane.diagnostics/v1' and result['status'] == 'blocked'
    assert result['initialization']['database_exists'] == (broken == 'corrupt')
    assert result['initialization']['marker_exists'] is True and result['workflow_error']
    for words in ([*runtime,'flow','report','--workspace',str(tmp_path)], [*diagnose,'--output','app.py'],
                  [*diagnose,'--workspace',str(tmp_path)], [*diagnose,';','touch','app.py'],
                  [*runtime,'flow','diagnose','--workspace',str(tmp_path.parent)],
                  [sys.executable,str(tmp_path/'tp.py'),'flow','diagnose','--workspace',str(tmp_path)]):
        with pytest.raises(w.Refusal): flow.hook(event(words), governor=c)
    with pytest.raises(w.Refusal): flow.hook(event(diagnose, parent_session_id='parent'), governor=c)
    assert candidate.read_bytes() == before


def test_recovery_cli_restores_only_expected_native_root(tmp_path, capsys, monkeypatch):
    from taskplane import flow
    c, state, target, candidate, raw, checksum = displaced_database(tmp_path)
    monkeypatch.setattr(flow, 'observed_parent', lambda *args: None)
    args=['recover','--workspace',str(tmp_path),'--recover-from',candidate.name,
          '--expected-sha256',checksum,'--run',state['run'],'--expected-revision',str(state['revision']),
          '--request-reference','conversation/recovery']
    if not binding_runtime_available():
        assert flow.main(args,compact=True,governor=c) == 2
        refusal = json.loads(capsys.readouterr().out)
        assert refusal['reason'] == 'workspace_binding'
        assert 'no-follow directory operations' in refusal['detail']
        assert not target.exists() and candidate.read_bytes() == raw
        return
    assert flow.main(args,compact=True,governor=c) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'restored'
    assert target.read_bytes() == raw


@pytest.mark.parametrize('package_host,adapter', [('claude','claude'), ('openai','codex')])
def test_extracted_packages_recover_displaced_state_through_declared_hooks(tmp_path, package_host, adapter):
    import subprocess, sys, zipfile
    from pathlib import Path
    from scripts.package_plugin import package, ROOT
    built = package(package_host, tmp_path/'archives')
    extracted = tmp_path/'installed'
    with zipfile.ZipFile(built['archive']) as archive:
        for member in archive.namelist():
            assert archive.read(member) == (ROOT/member).read_bytes()
        archive.extractall(extracted)
    # Only the extracted runtime is importable in this isolated child. Events are
    # fixture observations, never claims about live Cowork or Windows coverage.
    probe = r'''
import hashlib, json, os, shlex, subprocess, sys
from pathlib import Path
root, workspace, host = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(root))
from taskplane import workflow_host as h, workflow as w
workspace.mkdir()
(workspace/'app.py').write_text('value = 1\n')
scope = {'criteria':['RECOVERY'], 'paths':{p:[p+'.json'] for p in w.PHASES}, 'verification_inputs':['app.py']}
c = h.Controller(workspace, 'package-root', h.installed_adapter(host))
state = c.start({'scope':scope, 'request_reference':'isolated-package-fixture'})
assert c.adapter.name == host
target = c._path()
raw = target.read_bytes()
candidate = target.with_name(target.stem+' 2.json')
candidate.write_bytes(raw)
target.unlink()  # Deliberate interruption in an isolated temporary test workspace.
hooks = json.loads((root/'hooks/hooks.json').read_text())['hooks']
binding_supported = (os.open in os.supports_dir_fd and hasattr(os, 'O_DIRECTORY')
                     and hasattr(os, 'O_NOFOLLOW'))
def guarded(args):
    # Match the declared hook's interpreter identity. Windows py -3 can select
    # a different interpreter from the Python process that runs pytest.
    python = ['py', '-3'] if os.name == 'nt' else [sys.executable]
    argv = [*python, str(root/'taskplane/tp.py'), 'flow', *args, '--workspace', str(workspace)]
    tool, key = ('Bash','command') if host == 'claude' else ('exec_command','cmd')
    identity = {'session_id':'package-root'} if host == 'claude' else {'thread_id':'package-root'}
    event = {'hook_event_name':'PreToolUse','cwd':str(workspace), **identity,
             'tool_name':tool, 'tool_input':{key:shlex.join(argv)}}
    launcher = hooks['PreToolUse'][0]['hooks'][0]['commandWindows' if os.name == 'nt' else 'command']
    observed = subprocess.run(launcher, shell=True,
        cwd=workspace, input=json.dumps(event), text=True, capture_output=True)
    assert observed.returncode == 0, (observed.stdout, observed.stderr)
    assert json.loads(observed.stdout).get('hookSpecificOutput',{}).get('permissionDecision') != 'deny', observed.stdout
    result = subprocess.run(argv, cwd=workspace, text=True, capture_output=True)
    if args[0] == 'recover' and not binding_supported:
        refusal = json.loads(result.stdout)
        assert result.returncode != 0 and refusal['reason'] == 'workspace_binding', (result.stdout, result.stderr)
        assert refusal['detail'] == 'This runtime lacks no-follow directory operations required for workspace binding.'
        assert not target.exists() and candidate.read_bytes() == raw
        return refusal
    assert result.returncode == 0, (result.stdout, result.stderr)
    return json.loads(result.stdout)
diagnosis = guarded(['diagnose'])
assert diagnosis['status'] == 'blocked' and diagnosis['initialization']['marker_exists']
assert diagnosis['initialization']['database_exists'] is False
if not binding_supported:
    # The installed CLI must report its actual platform limitation. Recovery
    # cannot claim success or write state through an unsupported binding API.
    guarded(['recover','--recover-from',candidate.name,'--expected-sha256',hashlib.sha256(raw).hexdigest(),
             '--run',state['run'],'--expected-revision',str(state['revision']),
             '--request-reference','isolated-package-fixture/recovery'])
    raise SystemExit(0)
result = guarded(['recover','--recover-from',candidate.name,'--expected-sha256',hashlib.sha256(raw).hexdigest(),
                  '--run',state['run'],'--expected-revision',str(state['revision']),
                  '--request-reference','isolated-package-fixture/recovery'])
assert result['status'] == 'restored' and target.read_bytes() == candidate.read_bytes() == raw
assert c.report()['run'] == state['run'] and c.report()['revision'] == state['revision']
assert c.report()['decisions'] == state['decisions']
'''
    env = {k:v for k,v in os.environ.items() if not k.startswith(
        ('CODEX_', 'CLAUDE_', 'TASKPLANE_', 'PLUGIN_ROOT', 'PYTHONPATH'))}
    env.update(PLUGIN_ROOT=str(extracted), CLAUDE_PLUGIN_ROOT=str(extracted),
               PATH=str(Path(sys.executable).parent)+os.pathsep+env['PATH'])
    env['CODEX_THREAD_ID' if adapter == 'codex' else 'TASKPLANE_CLAUDE_SESSION_ID'] = 'package-root'
    checked = subprocess.run([sys.executable,'-I','-B','-c',probe,str(extracted),str(tmp_path/'workspace'),adapter],
                             cwd=tmp_path,env=env,text=True,capture_output=True)
    assert checked.returncode == 0, (checked.stdout, checked.stderr)


@pytest.mark.parametrize('bad', ['marker_json', 'database_symlink', 'marker_symlink'])
def test_diagnose_remains_reachable_for_invalid_initialization_files(tmp_path, bad, capsys):
    import shlex, sys
    from pathlib import Path
    from taskplane import flow
    c, _ = setup(tmp_path)
    target = c._path(); marker = target.with_name(target.stem + '.initialized.json')
    if bad == 'marker_json': marker.write_text('{')
    else:
        selected = target if bad == 'database_symlink' else marker
        backup = tmp_path/'original'; backup.write_bytes(selected.read_bytes())
        selected.unlink(); selected.symlink_to(backup)
    event={'hook_event_name':'PreToolUse','cwd':str(tmp_path),'session_id':'root','tool_name':'Bash',
           'tool_input':{'command':shlex.join([sys.executable,str(Path(flow.__file__).with_name('tp.py')),
               'flow','diagnose','--workspace',str(tmp_path)])}}
    assert flow.hook(event,governor=c).get('hookSpecificOutput',{}).get('permissionDecision') != 'deny'
    assert flow.main(['diagnose','--workspace',str(tmp_path)],compact=True,governor=c) == 0
    assert json.loads(capsys.readouterr().out)['workflow_error']


def present(c, s):
    from taskplane import flow
    flow.publish_dashboard(c.workspace,s['run'],governor=c,select=True)
    local.Harness(c.workspace,c.root).present(s,'.taskplane/dashboard.html','linked','Fixture native artifact link; no visual-host claim.')


def submit(c,s, *, presentation=True):
    from taskplane.tests.test_workflow_evidence import bound_output
    from taskplane.tests.test_worker_runtime import parent_pair
    from taskplane.worker_runtime import parent_readiness
    if s['index'] == 0 and parent_readiness(c.report(s['run'], diagnostics=False))['status'] != 'ready':
        parent_pair(c)
    target = bound_output(c, s)
    submitted=c.apply("submit",s["run"],expected_revision=s["revision"],output=target,tasks="tasks.json")
    if presentation: present(c,submitted)
    return submitted


@pytest.mark.parametrize('standalone', [False, True])
def test_approved_phase_can_advance_without_dashboard_handoff(tmp_path, standalone):
    c,s=setup(tmp_path,standalone=standalone);s=submit(c,s,presentation=False)
    accepted=decide(c,s)
    action='finish' if standalone else 'advance'
    assert c.apply(action,s['run'],expected_revision=accepted['revision'],phase='design')




def test_sealed_ordinary_open_is_available_without_claiming_visual_verification(tmp_path):
    c,s=setup(tmp_path);s=submit(c,s);harness=local.Harness(tmp_path,c.root)
    target=(tmp_path/'.taskplane/dashboard.html').as_uri()
    event={'tool_name':'mcp__codex_app__open_in_codex','tool_input':{'target':{'type':'browser','url':target}}}
    c.guard(event,s['run'])
    assert harness.read()['presentation']['outcome']=='linked'
    for url in [target+'#other','https://example.org/']:
        event['tool_input']['target']['url']=url
        c.guard(event,s['run'])
    event['tool_input']={'target':{'type':'file','path':str(tmp_path/'README.md')}}
    c.guard(event,s['run'])
    assert harness.read()['presentation']['outcome']=='linked'


def test_control_json_allows_literal_conditions_but_never_shell_expansion():
    import shlex
    words=['python3','tp.py','flow','auto-decide','--assessment-json',json.dumps({'instruction':'Keep $5 and `literal code`; do not change it.'})]
    assert local.command_words({'tool_input':{'cmd':shlex.join(words)}})==words
    for command in ['python3 tp.py "$(touch unexpected)"','python3 tp.py `touch unexpected`',
                    'python3 tp.py; touch unexpected','python3 tp.py\ntouch unexpected']:
        assert local.command_words({'tool_input':{'cmd':command}})==[]


def decision(s, *, text="Approved", event="message-1", candidate="approved"):
    from datetime import datetime, timezone
    import time
    presented = datetime.now(timezone.utc).isoformat()
    # Windows clocks can return the same timestamp for consecutive reads. This
    # fixture represents a later response; keep the runtime's strict ordering.
    for _ in range(1000):
        observed = datetime.now(timezone.utc).isoformat()
        if observed > presented:
            break
        time.sleep(0.001)
    else:
        raise AssertionError("Fixture clock did not advance after presentation")
    binding=w.binding(s,w.current(s)["packet"])
    return {"schema":"taskplane.observed-decision/v1","event_id":event,"choice": candidate,
            "binding":binding,"excerpt":text,"recorder":"root_orchestrator",
            "source":{"kind":"conversation","reference":event,"conversation":s["root"],"actor":"user",
                      "automatic":False,"observed_at":observed},
            "presentation":{"checkpoint":binding["checkpoint"],"reference":"assistant/presentation",
                            "at":presented,"text":f"Approve this {w.current(s)['phase']} checkpoint?"}}


def decide(c,s,value=None):
    result = c.apply("decide",s["run"],expected_revision=s["revision"],native_reference=json.dumps(value or decision(s)))
    stage = w.current(result)
    if stage['decision'] == 'approved' and (stage['phase'] == 'plan' or stage.get('packet', {}).get('route_change')):
        from taskplane.tests.test_worker_runtime import parent_pair
        from taskplane.worker_runtime import parent_readiness
        if parent_readiness(result)['status'] != 'ready':
            parent_pair(c)
    return result


def supported_build(tmp_path, host):
    from taskplane.tests.test_worker_runtime import parent_pair
    initial, _, _ = prepare(tmp_path)
    c = h.Controller(tmp_path, 'root', h.installed_adapter(host))
    state = c.start({'scope': initial['scope'], 'request_reference': 'fixture/supported-build'})
    for phase in ('design', 'plan', 'build'):
        state = submit(c, state)
        state = decide(c, state, decision(state, event='fixture/accept/' + str(state['revision'])))
        state = c.apply('advance', state['run'], expected_revision=state['revision'], phase=phase)
    parent_pair(c)
    return c, c.report(state['run'], diagnostics=False)


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('tool,key', [('Bash', 'command'), ('exec_command', 'cmd')])
def test_supported_shell_allows_owned_effects_and_refuses_unowned_effects(tmp_path, host, tool, key):
    import subprocess
    c, state = supported_build(tmp_path, host)
    owned = {'tool_name': tool, 'cwd': str(tmp_path), 'tool_input': {key: 'echo candidate > app.py'}}
    c.guard(owned, state['run'])
    completed = subprocess.run(owned['tool_input'][key], shell=True, cwd=tmp_path, check=False)
    assert completed.returncode == 0 and (tmp_path/'app.py').read_text().strip() == 'candidate'
    for command in ['touch sibling.py', 'printf denied > sibling.py', 'rm ../outside.py',
                    'python -c "open(\"sibling.py\",\"w\").write(\"bad\")"',
                    'touch app.py; touch sibling.py']:
        with pytest.raises(w.Refusal):
            c.guard({'tool_name': tool, 'cwd': str(tmp_path), 'tool_input': {key: command}}, state['run'])
    assert not (tmp_path/'sibling.py').exists()


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_hook_readiness_and_structured_scope_are_required_before_build_writes(tmp_path, host):
    c, state = supported_build(tmp_path, host)
    c.guard({'tool_name': 'Write', 'tool_input': {'path': 'app.py'}}, state['run'])
    with pytest.raises(w.Refusal, match='outside'):
        c.guard({'tool_name': 'Write', 'tool_input': {'path': 'sibling.py'}}, state['run'])
    # Missing setup is checked outside the tool hook at the Build transition.
    broken = deepcopy(state)
    broken.pop('parent_hook_readiness', None)
    with pytest.raises(w.Refusal, match='Preventive hook admission'):
        c._guard({'tool_name': 'Write', 'tool_input': {'path': 'app.py'}}, broken)
    broken = deepcopy(state)
    broken['parent_hook_readiness']['root'] = 'another-session'
    assert local.Harness(tmp_path, 'root').readiness(broken)['status'] != 'active'


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_candidate_hook_source_is_editable_but_live_uninstall_has_no_recovery_escape(tmp_path, host):
    c, state = supported_build(tmp_path, host)
    # app.py represents the candidate source owned by this accepted task.
    c.guard({'tool_name': 'Edit', 'tool_input': {'file_path': 'app.py'}}, state['run'])
    uninstall = {'tool_name': 'mcp__codex_app__uninstall_plugin', 'tool_input': {'plugin': 'taskplane'}}
    assert not local.Harness(tmp_path, 'root').recovery_action(uninstall, state)
    with pytest.raises(w.Refusal):
        c.guard(uninstall, state['run'])
    for target in ('../live/hooks.json', '../live/taskplane/tp.py'):
        with pytest.raises(w.Refusal):
            c.guard({'tool_name': 'Write', 'tool_input': {'path': target}}, state['run'])


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_missing_hooks_block_build_transition_outside_the_preventive_hook(tmp_path, host):
    from taskplane.tests.test_worker_runtime import parent_pair
    initial, _, _ = prepare(tmp_path)
    c = h.Controller(tmp_path, 'root', h.installed_adapter(host))
    state = c.start({'scope': initial['scope'], 'request_reference': 'fixture/missing-hooks'})
    for phase in ('design', 'plan'):
        state = submit(c, state)
        state = decide(c, state, decision(state, event='fixture/phase/'+phase))
        state = c.apply('advance', state['run'], expected_revision=state['revision'], phase=phase)
    state = submit(c, state)
    state = c.apply('decide', state['run'], expected_revision=state['revision'],
        native_reference=json.dumps(decision(state, event='fixture/accept-plan')))
    before = deepcopy(state['decisions'])
    with pytest.raises(w.Refusal, match='Build needs an admitted automatic'):
        c.apply('advance', state['run'], expected_revision=state['revision'], phase='build')
    assert c.report()['decisions'] == before and w.current(c.report())['decision'] == 'approved'
    parent_pair(c)
    state = c.apply('advance', state['run'], expected_revision=state['revision'], phase='build')
    assert w.current(state)['phase'] == 'build'


def test_plain_repair_approval_reopens_build_and_preserves_history(tmp_path):
    c, state = setup(tmp_path)
    for next_phase in w.PHASES[1:6]:
        state = submit(c, state)
        state = decide(c, state, decision(state, event='accept-before-' + next_phase))
        state = c.apply('advance', state['run'], expected_revision=state['revision'], phase=next_phase)
    previous_build = next(v for v in state['visits'] if v['phase'] == 'build')
    previous_decisions = deepcopy(state['decisions'])
    _, output, _ = prepare(tmp_path, 'engineering')
    output.update(run=state['run'], visit=w.current(state)['id'], route_change={'kind': 'repair'})
    (tmp_path / 'engineering.json').write_text(json.dumps(output))
    state = c.apply('submit', state['run'], expected_revision=state['revision'],
                    output='engineering.json', tasks='tasks.json')
    present(c, state)
    response = decision(state, text='approve repair', event='approve-the-repair')
    accepted = decide(c, state, response)
    assert accepted['decisions']['approve-the-repair']['provenance']['excerpt'] == 'approve repair'
    assert all(accepted['decisions'][key] == value for key, value in previous_decisions.items())
    historical = next(v for v in accepted['visits'] if v['id'] == previous_build['id'])
    assert historical['superseded'] and historical['decision'] == 'approved'
    present(c, accepted)
    repaired = c.apply('advance', accepted['run'], expected_revision=accepted['revision'], phase='build')
    assert w.current(repaired)['phase'] == 'build'
    assert w.current(repaired)['id'] != previous_build['id']
    assert w.current(repaired)['decision'] == 'not_requested'


@pytest.mark.parametrize('completed', [False, True])
def test_scoped_repair_uses_controller_and_preserves_prior_approvals(tmp_path, completed):
    from taskplane.tests.test_worker_runtime import parent_pair
    from taskplane.tests.test_workflow_evidence import bound_output
    c, state = setup(tmp_path)
    for phase in w.PHASES:
        if phase == 'build' and not completed:
            state = submit(c, state)
            break
        state = submit(c, state)
        state = decide(c, state, decision(state, event='fixture/initial/' + phase))
        if phase != 'retro':
            state = c.apply('advance', state['run'], expected_revision=state['revision'],
                            phase=w.PHASES[w.PHASES.index(phase) + 1])
        else:
            state = c.apply('finish', state['run'], expected_revision=state['revision'])
    original = deepcopy(state)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal) as malformed:
        c.apply('repair', state['run'], expected_revision=state['revision'], native_reference='{')
    assert malformed.value.reason == 'invalid_evidence' and c._path().read_bytes() == before
    request = {'decision': decision(state, event='fixture/scoped-repair'), 'tasks': ['T1']}
    state = c.apply('repair', state['run'], expected_revision=state['revision'],
                    native_reference=json.dumps(request))
    assert not state['finished'] and w.current(state)['phase'] == 'build'
    assert state['repair_scope']['tasks'] == ['T1']
    assert all(state['decisions'][key] == value for key, value in original['decisions'].items())
    for phase in ('product', 'design', 'plan'):
        before = next(v for v in original['visits'] if v['phase'] == phase)
        assert next(v for v in state['visits'] if v['id'] == before['id']) == before
    with pytest.raises(w.Refusal, match='Preventive hook admission'):
        c.guard({'tool_name': 'Write', 'tool_input': {'path': 'app.py'}}, state['run'])
    parent_pair(c)
    state = c.report()
    c.guard({'tool_name': 'Write', 'tool_input': {'path': 'app.py'}}, state['run'])
    import shlex
    check = w.accepted_plan(state)['packet']['output']['verification_strategy']['checks'][0]
    event = {'tool_name': 'exec_command', 'tool_input': {
        'cmd': shlex.join(check['command']), 'workdir': str(tmp_path)}}
    c.guard(event, state['run'])
    with pytest.raises(w.Refusal):
        c.guard({'tool_name': 'Write', 'tool_input': {'path': check['evidence_outputs'][0]}}, state['run'])
    with pytest.raises(w.Refusal):
        c.adapter.guard_command(event, state, [p for p in w.phase_write_paths(state) if p != 'app.py'])
    (tmp_path/'app.py').write_text('value = 2\n')
    for phase in w.PHASES[3:]:
        target = bound_output(c, state)
        state = c.apply('submit', state['run'], expected_revision=state['revision'],
                        output=target, tasks='tasks.json')
        state = decide(c, state, decision(state, event='fixture/repaired/' + phase))
        if phase != 'retro':
            state = c.apply('advance', state['run'], expected_revision=state['revision'],
                            phase=w.PHASES[w.PHASES.index(phase) + 1])
    state = c.apply('finish', state['run'], expected_revision=state['revision'])
    assert state['finished'] and (tmp_path/'app.py').read_text() == 'value = 2\n'


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_repair_needs_fresh_authority_and_retries_do_not_reopen_work(tmp_path, host):
    c, state = supported_build(tmp_path, host)
    state = submit(c, state)
    old_approval = decision(state, event='fixture/build-only')
    state = decide(c, state, old_approval)
    before = c.report(state['run'], diagnostics=False)
    with pytest.raises(w.Refusal, match='earlier phase decision'):
        c.apply('repair', state['run'], expected_revision=state['revision'],
                native_reference=json.dumps({'decision': old_approval, 'tasks': ['T1']}))
    assert c.report(state['run'], diagnostics=False) == before

    request = {'decision': decision(state, event='fixture/repair-request'), 'tasks': ['T1']}
    repaired = c.apply('repair', state['run'], expected_revision=state['revision'],
                       native_reference=json.dumps(request))
    assert w.current(repaired)['phase'] == 'build'
    assert len(repaired['visits']) == len(state['visits']) + 4
    assert repaired['decisions'] == state['decisions']
    assert repaired['visits'][:3] == state['visits'][:3]
    # A lost response may be retried with the original revision and provenance.
    retry = c.apply('repair', state['run'], expected_revision=state['revision'],
                    native_reference=json.dumps(request))
    assert retry == repaired
    before = c.report(state['run'], diagnostics=False)
    request['tasks'] = []
    with pytest.raises(w.Refusal, match='Conflicting repair retry'):
        c.apply('repair', state['run'], expected_revision=state['revision'],
                native_reference=json.dumps(request))
    assert c.report(state['run'], diagnostics=False) == before


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_repair_cannot_reactivate_a_superseded_run(tmp_path, host):
    c, state = supported_build(tmp_path, host)
    state = submit(c, state)
    replacement = c.start({'replace_run': state['run'], 'expected_revision': state['revision'],
        'request_reference': 'fixture/replacement', 'scope': state['scope'],
        'entry': 'product', 'standalone': True, 'tasks': 'tasks.json'})
    c.apply('retire', replacement['run'], expected_revision=replacement['revision'],
        native_reference=json.dumps({'request_reference': 'fixture/end-replacement',
                                     'reason': 'The replacement was ended.'}))
    previous = c.report(state['run'], diagnostics=False)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match='This run was replaced'):
        c.apply('repair', state['run'], expected_revision=previous['revision'],
            native_reference=json.dumps({'decision': decision(previous, event='fixture/invalid-repair'),
                                         'tasks': ['T1']}))
    assert c._path().read_bytes() == before
    assert c.report().get('run') is None


def test_local_profile_is_available_without_certifying_host(tmp_path):
    c,s=setup(tmp_path)
    report=c.report()
    assert report["workflow_available"] and not report["authority_verified"]
    assert all(report["capabilities"][k] is False for k in ("human_origin","protected_store","tool_containment","process_tracking"))
    assert report["coverage"]["process_census"]=="unknown"
    assert h.Controller(c.workspace,"root",h.installed_adapter("codex")).report()["run"]==s["run"]
    protected=h.Controller(c.workspace,"root",h.installed_adapter("codex","protected_host"))
    assert not protected.availability()["workflow_available"]
    with pytest.raises(w.Refusal,match="unverified"):protected.start({})


@pytest.mark.parametrize("bad",["missing","marker","corrupt","identity","profile","symlink"])
def test_local_initialized_state_cannot_silently_reset(tmp_path,bad):
    c,s=setup(tmp_path)
    path=c.adapter.control_path(c.workspace,c.root)
    if bad=="missing":path.unlink()
    elif bad=="marker":path.with_name(c.adapter.markername).unlink()
    elif bad=="corrupt":path.write_text("{")
    elif bad=="symlink":
        backup=path.with_suffix(".saved");path.rename(backup);path.symlink_to(backup)
    else:
        value=json.loads(path.read_text());value["root" if bad=="identity" else "profile"]="wrong";path.write_text(json.dumps(value))
    with pytest.raises((w.Refusal,ValueError)):c.start({})


@pytest.mark.parametrize("bad",["actor","automatic","source","choice","ordering","binding","boolean_revision","no_presentation","actor_only"])
def test_observed_decision_rejects_invalid_provenance_and_binding(tmp_path,bad):
    c,s=setup(tmp_path);s=submit(c,s);value=decision(s)
    if bad=="actor":value["source"]["actor"]="assistant"
    elif bad=="automatic":value["source"]["automatic"]=True
    elif bad=="source":value["source"]["kind"]="tool_result"
    elif bad=="choice":value["choice"]="ambiguous"
    elif bad=="ordering":value["source"]["observed_at"]="2026-09-16T22:00:00+00:00"
    elif bad=="binding":value["binding"]["root"]="another"
    elif bad=="boolean_revision":value["binding"]["revision"]=True
    elif bad=="no_presentation":value.pop("presentation")
    else:value={"human":True,"actor":"human","choice":"approved"}
    with pytest.raises(w.Refusal):decide(c,s,value)
    assert c.report()["status"]=="awaiting_human_approval"


def test_decision_restart_replay_changes_requested_and_drift(tmp_path):
    c,s=setup(tmp_path);s=submit(c,s);value=decision(s,text="Changes requested",candidate="changes_requested")
    s=decide(c,s,value);assert w.current(s)["decision"]=="changes_requested"
    s=submit(c,s);value=decision(s,event="message-2")
    accepted=decide(c,s,value)
    assert accepted["decisions"]["message-2"]["assurance"]=="observed"
    resumed=h.Controller(c.workspace,"root",h.installed_adapter("codex"))
    assert decide(resumed,s,value)==accepted
    conflict=deepcopy(value);conflict["source"]["reference"]="different"
    with pytest.raises(w.Refusal,match="replay"):decide(resumed,s,conflict)
    (tmp_path/"product.json").write_text("{}")
    with pytest.raises(w.Refusal,match="changed"):
        resumed.apply("advance",s["run"],expected_revision=accepted["revision"],phase="design")
    assert resumed.report()["status"]=="stale"






def test_known_processes_block_seal_and_stale_input(tmp_path):
    c,s=setup(tmp_path)
    event={"hook_event_name":"PostToolUse","tool_name":"exec_command","tool_input":{},"tool_response":{"session_id":123}}
    c.observe(event,s["run"])
    assert not c.adapter.can_seal(c.report())
    c.guard({"tool_name":"write_stdin","tool_input":{"session_id":123}},s["run"])
    with pytest.raises(w.Refusal,match="Live tool processes"):submit(c,s)
    event["tool_response"]["exit_code"]=0;c.observe(event,s["run"])
    with pytest.raises(w.Refusal,match="terminal"):c.guard({"tool_name":"write_stdin","tool_input":{"session_id":123}},s["run"])
    s=submit(c,s);assert w.current(s)["decision"]=="awaiting_human_approval"
    with pytest.raises(w.Refusal,match="sealed"):c.guard({"tool_name":"Write","tool_input":{"path":"product.json"}},s["run"])


def test_structured_scope_and_exact_control_command_at_gate(tmp_path):
    c,s=setup(tmp_path)
    c.guard({"tool_name":"Write","tool_input":{"path":"product.json"}},s["run"])
    with pytest.raises(w.Refusal,match="outside"):c.guard({"tool_name":"Write","tool_input":{"path":"app.py"}},s["run"])
    s=submit(c,s)
    import sys,shlex
    from pathlib import Path
    command=shlex.join([sys.executable,str(Path(h.__file__).with_name("tp.py")),"flow","report","--workspace",str(c.workspace)])
    c.guard({"tool_name":"exec_command","tool_input":{"cmd":command}},s["run"])
    with pytest.raises(w.Refusal,match="sealed"):
        c.guard({"tool_name":"Write","tool_input":{"path":"product.json"}},s["run"])




def test_local_concurrent_replay_commits_one_decision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    c,s=setup(tmp_path);s=submit(c,s);value=decision(s)
    def record(_):
        resumed=h.Controller(c.workspace,"root",h.installed_adapter("codex"))
        return decide(resumed,s,value)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(record,range(8)))
    assert all(result==results[0] for result in results)
    assert len(c.report()["decisions"])==1
    assert c.report()["revision"]==s["revision"]+1


def test_interrupted_first_initialization_resumes_with_pending_proof(tmp_path,monkeypatch):
    initial,_,_=prepare(tmp_path)
    c=h.Controller(tmp_path.resolve(),"root",h.installed_adapter("codex"))
    original=local.primitives.atomic_json
    def interrupted(path,value,**kwargs):
        if str(path).endswith(c.adapter.filename):
            raise OSError("simulated interruption after marker")
        return original(path,value,**kwargs)
    monkeypatch.setattr(local.primitives,"atomic_json",interrupted)
    with pytest.raises(OSError,match="interruption"):
        c.start({"scope":initial["scope"],"request_reference":"test/start"})
    monkeypatch.setattr(local.primitives,"atomic_json",original)
    with pytest.raises(w.Refusal,match="pending"):
        c.report()
    resumed = c.start({"scope":initial["scope"],"request_reference":"test/start"})
    assert resumed["revision"] == 0 and resumed["decisions"] == {}


def test_native_prompt_needs_envelope_and_journal_failure_keeps_decision(tmp_path,monkeypatch):
    from taskplane import flow
    c,s=setup(tmp_path);s=submit(c,s)
    event={"cwd":str(c.workspace),"session_id":"root","hook_event_name":"UserPromptSubmit","prompt":"Approve"}
    result=flow.hook(event)
    assert "prompt alone" in result["hookSpecificOutput"]["additionalContext"]
    assert c.report()["status"]=="awaiting_human_approval"
    def failed(*args,**kwargs):raise OSError("simulated optional observation failure")
    monkeypatch.setattr(flow,"_observe_hook",failed)
    value=decision(s);value["source"]["kind"]="native_prompt";value["recorder"]="native_prompt_hook"
    event["taskplane_decision"]=value
    flow.hook(event)
    resumed=h.Controller(c.workspace,"root",h.installed_adapter("codex"))
    assert resumed.report()["status"]=="approved"
    assert resumed.report()["phase"]=="product"


def check_store_byte_boundary(c, monkeypatch, payload, margin):
    """Bound the exact next-generation commit without writing a sizing probe."""
    from taskplane import primitives, storage
    target = c.adapter.control_path(c.workspace, c.root)
    before = target.read_bytes()
    original = json.loads(before)
    generation = storage.storage_generation(original)
    candidate = deepcopy(original)
    candidate['size_probe'] = payload
    committed = deepcopy(candidate)
    committed['storage_generation'] = generation + 1
    measured = storage.encode_controller(committed)
    assert target.read_bytes() == before
    monkeypatch.setattr(local, 'MAX_BYTES', len(measured) + margin)
    with primitives.file_lock(str(target)):
        if margin < 0:
            with pytest.raises(w.Refusal, match='size limit|byte limit'):
                c._write(target, candidate)
            assert target.read_bytes() == before
            assert storage.storage_generation(candidate) == generation
        else:
            c._write(target, candidate)
            assert target.read_bytes() == measured
            assert storage.storage_generation(candidate) == generation + 1
            assert len(measured) <= local.MAX_BYTES
    resumed = h.Controller(c.workspace, c.root, c.adapter).report()
    active = original['runs'][original['active']]
    assert resumed['run'] == active['run'] and resumed['revision'] == active['revision']
    assert resumed['status'] == 'not_requested'


@pytest.mark.parametrize('margin', [-1, 0, 1])
@pytest.mark.parametrize('payload', [[['item'] * 40] * 4, {'unicode': ['é', '東京', '🙂'] * 40}])
def test_local_persisted_byte_boundary_preserves_readable_state(tmp_path, monkeypatch, payload, margin):
    c, _ = setup(tmp_path)
    check_store_byte_boundary(c, monkeypatch, payload, margin)


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_compact_write_reads_legacy_state_and_preserves_prior_acceptance(tmp_path, host):
    c, s = setup(tmp_path)
    s = decide(c, submit(c, s))
    c = h.Controller(c.workspace, c.root, h.installed_adapter(host))
    target = c.adapter.control_path(c.workspace, c.root)
    accepted = json.loads(target.read_bytes())
    legacy = (json.dumps(accepted, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()
    target.write_bytes(legacy)
    assert c.report()['status'] == 'approved'
    assert target.read_bytes() == legacy  # Reading does not rewrite old evidence.
    s = c.apply('advance', s['run'], expected_revision=s['revision'], phase='design')
    s = submit(c, s)
    raw = target.read_bytes()
    persisted = json.loads(raw)
    assert raw == (json.dumps(persisted, sort_keys=True, separators=(',', ':'),
                              ensure_ascii=True, allow_nan=False) + '\n').encode()
    assert len(raw) < len((json.dumps(persisted, sort_keys=True, indent=2) + '\n').encode())
    old = accepted['runs'][s['run']]
    current = persisted['runs'][s['run']]
    assert current['decisions'] == old['decisions']
    assert current['visits'][0] == old['visits'][0]
    assert current['scope'] == old['scope']
    resumed = h.Controller(c.workspace, c.root, h.installed_adapter(host)).report()
    assert resumed['status'] == 'awaiting_human_approval' and resumed['revision'] == s['revision']
    assert not resumed.get('invalidation_pending')
    if os.name == 'posix':
        assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('failure_call', [1, 2])
def test_control_write_does_not_acknowledge_failed_file_or_directory_sync(tmp_path, monkeypatch, failure_call):
    from taskplane import primitives, storage
    c, s = setup(tmp_path)
    target = c.adapter.control_path(c.workspace, c.root)
    before = target.read_bytes()
    candidate = json.loads(before)
    candidate['sync_probe'] = 'updated'
    proposed = deepcopy(candidate)
    generation = storage.storage_generation(candidate)
    published = {**deepcopy(candidate), 'storage_generation': generation + 1}
    published_bytes = storage.encode_controller(published)
    original = primitives.os.fsync
    calls = []
    def fail_sync(fd):
        calls.append(fd)
        if len(calls) == failure_call:
            raise OSError('simulated sync failure')
        return original(fd)
    with monkeypatch.context() as patch:
        patch.setattr(primitives.os, 'fsync', fail_sync)
        if failure_call == 2 and os.name == 'nt':
            # Windows flushes directory handles through its native API.
            def fail_directory(path):
                calls.append(path)
                raise OSError('simulated directory flush failure')
            patch.setattr(primitives, '_flush_windows_directory', fail_directory)
        with primitives.file_lock(str(target)), pytest.raises(w.Refusal, match='not acknowledged'):
            c._write(target, candidate)
    assert len(calls) == failure_call
    assert candidate == proposed  # A failed acknowledgment never advances the caller.
    if failure_call == 1:
        assert target.read_bytes() == before
    else:
        assert target.read_bytes() == published_bytes
        with primitives.file_lock(str(target)), pytest.raises(w.Refusal, match='generation changed'):
            c._write(target, candidate)
        assert target.read_bytes() == published_bytes
    assert not list(target.parent.glob('.'+target.name+'.*.tmp'))
    resumed = h.Controller(c.workspace, c.root, c.adapter)
    assert resumed._read(target) == (json.loads(before) if failure_call == 1 else published)
    assert resumed.report()['revision'] == s['revision']


@pytest.mark.parametrize('profile', ['local', 'protected'])
@pytest.mark.skipif(os.name != 'posix', reason='POSIX mode bits and umask; Windows uses ACLs')
def test_control_state_remains_private_under_permissive_umask(tmp_path, profile):
    import os
    import stat
    from taskplane.tests.test_workflow_host import controller
    previous = os.umask(0o022)
    try:
        c = setup(tmp_path)[0] if profile == 'local' else controller(tmp_path)[0]
        target = c.adapter.control_path(c.workspace, c.root)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        state = json.loads(target.read_bytes())
        state['privacy_probe'] = 'updated'
        c._write(target, state)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert json.loads(target.read_bytes()) == state
    finally:
        os.umask(previous)


def check_historical_finish_preserves_active(c, finished, active):
    """Shared by real local, protected fixture and isolated extracted-runtime checks."""
    target = c.adapter.control_path(c.workspace, c.root)
    before = target.read_bytes()
    replay = c.apply('finish', finished['run'], expected_revision=finished['revision'])
    assert replay == finished
    assert target.read_bytes() == before
    db = json.loads(before)
    assert db['active'] == active['run'] and db['runs'][active['run']] == active
    resumed = h.Controller(c.workspace, c.root, c.adapter)
    assert resumed.report()['run'] == active['run']
    assert resumed.report()['revision'] == active['revision']
    resumed.guard({'tool_name':'Write', 'tool_input':{'path':'product.json'}}, active['run'])
    try:
        resumed.guard({'tool_name':'Write', 'tool_input':{'path':'app.py'}}, active['run'])
    except w.Refusal as exc:
        assert exc.reason == 'scope_violation'
    else:
        raise AssertionError('New active run lost its scope guard after historical finish')
    for state, revision, reason in [(active, active['revision'], 'approval_required'),
                                    (finished, finished['revision']-1, 'stale_checkpoint')]:
        try:
            resumed.apply('finish', state['run'], expected_revision=revision)
        except w.Refusal as exc:
            assert exc.reason == reason
        else:
            raise AssertionError('Invalid completion accepted')
        assert target.read_bytes() == before


def test_local_historical_finish_preserves_active_run_and_refusals(tmp_path):
    c, a = setup(tmp_path, standalone=True)
    a = decide(c, submit(c, a))
    a = c.apply('finish', a['run'], expected_revision=a['revision'])
    assert c.report()['status'] == 'no_workflow'
    assert c.apply('finish', a['run'], expected_revision=a['revision']) == a
    b = c.start({'scope':a['scope'], 'request_reference':'test/new-run'})
    check_historical_finish_preserves_active(c, a, b)
    # No output for B overwrites A's evidence. An actual change to A still invalidates A only.
    (c.workspace/'product.json').write_text('{}')
    with pytest.raises(w.Refusal, match='changed'):
        c.apply('finish', a['run'], expected_revision=a['revision'])
    target = c.adapter.control_path(c.workspace, c.root)
    stale = json.loads(target.read_bytes())['runs'][a['run']]
    before = target.read_bytes()
    with pytest.raises(w.Refusal, match='human acceptance'):
        c.apply('finish', a['run'], expected_revision=stale['revision'])
    assert target.read_bytes() == before
    assert c.report()['run'] == b['run'] and c.report()['revision'] == b['revision']


def task_observation_checkpoint(workspace, *, legacy=False, host="codex"):
    """Accepted native Build with the shared task file in its approved write scope."""
    from unittest.mock import patch
    from taskplane import workflow_evidence as evidence, depgraph
    state,_,_=prepare(workspace)
    (workspace/'build-check.txt').write_text('Fixture Build check passed')
    (workspace/'check.txt').write_text('Observed fixture check result')
    task_path='.taskplane/tasks.json'
    state['scope']['paths']['build'].append(task_path)
    c=h.Controller(workspace.resolve(),'root',h.installed_adapter(host))
    s=c.start({'scope':state['scope'],'request_reference':'test/EV-F02'})
    from taskplane.tests.test_worker_runtime import parent_pair
    parent_pair(c)
    raw_manifest=evidence.manifest
    for phase in ('product','design','plan','build'):
        _,out,tasks=prepare(workspace,phase)
        if phase == 'build':
            from taskplane.tests.test_workflow_evidence import bound_output
            out = json.loads((workspace/bound_output(c, s)).read_text())
        tasks['tasks'][0]['paths'].append(task_path)
        (workspace/task_path).write_text(json.dumps(tasks))
        out.update(run=s['run'],visit=w.current(s)['id'])
        if phase=='plan':out.update(write_scope=state['scope']['paths']['build'],task_dag=tasks['tasks'])
        (workspace/(phase+'.json')).write_text(json.dumps(out))
        depgraph.scan(str(workspace),decompose=True,strict=True)
        # Construct an old byte-manifest packet at creation time, never rewrite
        # an accepted packet or its decision to simulate an upgrade.
        def manifest_before_repair(root,paths,*,allow_missing=False,**kwargs):
            return raw_manifest(root,paths,allow_missing=allow_missing)
        with patch.object(evidence,'manifest',manifest_before_repair if legacy else raw_manifest):
            s=c.apply('submit',s['run'],expected_revision=s['revision'],output=phase+'.json',tasks=task_path)
        present(c,s)
        s=decide(c,s,decision(s,event='human-'+phase))
        s=c.apply('advance',s['run'],expected_revision=s['revision'],phase=w.PHASES[w.PHASES.index(phase)+1])
    return c,s,task_path


@pytest.mark.parametrize('field,value',[('status','working'),('started_at','2026-09-17T20:00:00Z'),('completed_at',None),('updated_at','2026-09-17T20:00:01Z'),('elapsed_seconds',1.5)])
def test_task_observation_edits_do_not_stale_accepted_build(tmp_path,field,value):
    c,s,relative=task_observation_checkpoint(tmp_path)
    store=c.adapter.control_path(c.workspace,c.root);before=store.read_bytes()
    path=tmp_path/relative;tasks=json.loads(path.read_text());tasks['tasks'][0][field]=value;path.write_text(json.dumps(tasks))
    report=c.report()
    assert not report.get('invalidation_pending') and report['status']=='not_requested'
    assert next(v for v in report['visits'] if v['phase']=='build')['decision']=='approved'
    assert store.read_bytes()==before


@pytest.mark.parametrize('field,value',[('owner','different'),('dependencies',['unknown']),('paths',['other.py']),('criteria',['AC2']),('verification','Different check'),('title','Different instruction'),('extra_scope',['other.py'])])
def test_task_definition_edits_still_stale_accepted_build(tmp_path,field,value):
    c,s,relative=task_observation_checkpoint(tmp_path)
    path=tmp_path/relative;tasks=json.loads(path.read_text());tasks['tasks'][0][field]=value;path.write_text(json.dumps(tasks))
    assert c.report().get('invalidation_pending')


@pytest.mark.parametrize('change',['wrapper','missing','malformed','source'])
def test_task_manifest_preserves_other_evidence_checks(tmp_path,change):
    c,s,relative=task_observation_checkpoint(tmp_path)
    path=tmp_path/relative
    if change=='wrapper':
        tasks=json.loads(path.read_text());tasks['instructions']='New requirement';path.write_text(json.dumps(tasks))
    elif change=='missing':path.unlink()
    elif change=='malformed':path.write_text('{')
    else:(tmp_path/'app.py').write_text('value = 99\n')
    assert c.report().get('invalidation_pending')


def test_legacy_task_manifest_remains_strict_without_rewriting_acceptance(tmp_path):
    c,s,relative=task_observation_checkpoint(tmp_path,legacy=True)
    store=c.adapter.control_path(c.workspace,c.root);before=store.read_bytes()
    assert not c.report().get('invalidation_pending')
    path=tmp_path/relative;tasks=json.loads(path.read_text());tasks['tasks'][0]['status']='working';path.write_text(json.dumps(tasks))
    assert c.report().get('invalidation_pending')
    assert store.read_bytes()==before


@pytest.mark.parametrize('entry',['taskplane','tp-go','tp-tag','tp-build','tp-product','tp-design','tp-engineering','tp-northstar'])
def test_execution_selection_guards_writes_without_forcing_continuation(tmp_path,entry):
    from taskplane import flow
    event={'cwd':str(tmp_path),'session_id':'root','hook_event_name':'PreToolUse',
           'tool_name':'Skill','tool_input':{'skill':'taskplane:'+entry}}
    selected=flow.hook(event)
    assert 'Taskplane' in selected['hookSpecificOutput']['additionalContext']
    with pytest.raises(w.Refusal) as refused:
        flow.hook(event|{'tool_name':'Write','tool_input':{'file_path':str(tmp_path/'app.py'),'content':'x'}})
    assert refused.value.reason == 'scope_violation'
    stopped=flow.hook(event|{'hook_event_name':'Stop'})
    assert stopped.get('systemMessage') and 'decision' not in stopped
    assert 'decision' not in flow.hook(event|{'hook_event_name':'Stop','stop_hook_active':True})
    assert not list((tmp_path/'.taskplane').glob('workflow-*.json'))


@pytest.mark.parametrize('prompt',[
    'What is Taskplane?', 'Use taskplane help', 'Use taskplane status',
    'Document "Use taskplane to review code." in README.',
    '> Use taskplane to review code.', '```text\nUse taskplane to review code.\n```',
    'Do not use taskplane for this review.', 'Example: Use taskplane to review code.',
    'taskplane help', 'taskplane status', 'taskplane is a workflow plugin.',
    'Document "taskplane build this feature" as an example.',
    '> taskplane build this feature', '```text\ntaskplane build this feature\n```',
    'Do not taskplane build this feature.', 'taskplaner build this feature',
])
def test_harness_does_not_activate_for_nonexecution_requests(tmp_path,prompt):
    from taskplane import flow
    assert flow.hook({'cwd':str(tmp_path),'session_id':'root','hook_event_name':'UserPromptSubmit','prompt':prompt})=={}
    assert flow.hook({'cwd':str(tmp_path),'session_id':'root','hook_event_name':'Stop'})=={}


@pytest.mark.parametrize('prompt',[
    'Use taskplane to review this code.', 'Use Taskplane to design a change.',
    '$tp-engineering review this code', '/taskplane:tp-product define the outcome',
    '[@taskplane](plugin://taskplane@openai-curated-remote) review this code',
])
def test_execution_prompt_arms_session_bound_gate(tmp_path,prompt):
    from taskplane import flow
    result=flow.hook({'cwd':str(tmp_path),'session_id':'root','hook_event_name':'UserPromptSubmit','prompt':prompt})
    assert local.Harness(tmp_path,'root').read()['selected']
    assert result['hookSpecificOutput']['additionalContext']
    assert flow.hook({'cwd':str(tmp_path),'session_id':'unrelated','hook_event_name':'Stop'})=={}
    assert flow.hook({'cwd':str(tmp_path),'session_id':'root','hook_event_name':'Stop'}).get('systemMessage')


def test_harness_resume_reuses_every_phase_and_releases_finished_run(tmp_path):
    from taskplane import flow
    c,s=setup(tmp_path)
    event={'cwd':str(tmp_path),'session_id':'root','hook_event_name':'SessionStart','source':'resume'}
    run=s['run']
    for i,phase in enumerate(w.PHASES):
        result=flow.hook(event,governor=c)
        assert phase in result['hookSpecificOutput']['additionalContext'].casefold()
        assert c.context(run)['binding']['visit'] == w.current(c.report())['id']
        assert run in result['hookSpecificOutput']['additionalContext']
        assert flow.hook(event|{'hook_event_name':'Stop'},governor=c).get('systemMessage')
        s=submit(c,s)
        page=flow.publish_dashboard(c.workspace,s['run'],governor=c,select=True)
        harness=local.Harness(c.workspace,c.root)
        harness.present(c.report(),str(page.relative_to(c.workspace)),'linked','Fixture link provided without display assertion')
        assert flow.hook(event|{'hook_event_name':'Stop'},governor=c)=={}
        s=decide(c,s,decision(s,event='resume-human-'+phase))
        s=c.apply('advance' if i<6 else 'finish',run,expected_revision=s['revision'],phase=w.PHASES[i+1] if i<6 else '')
    assert len(s['decisions'])==7 and s['finished']
    assert flow.hook(event|{'hook_event_name':'Stop'},governor=c)=={}
    selected=flow.hook(event|{'hook_event_name':'UserPromptSubmit','prompt':'Use taskplane to review the next change.'},governor=c)
    assert selected['hookSpecificOutput']['additionalContext']
    assert flow.hook(event|{'hook_event_name':'Stop'},governor=c).get('systemMessage')


@pytest.mark.parametrize('bad',['source','metadata','escape','symlink'])
def test_harness_bootstrap_does_not_grant_source_or_state_access(tmp_path,bad):
    from taskplane import flow
    base={'cwd':str(tmp_path),'session_id':'root','hook_event_name':'UserPromptSubmit','prompt':'Use taskplane to review code.'}
    flow.hook(base)
    path='app.py'
    if bad=='metadata':path='.taskplane/workflow-forged.json'
    if bad=='escape':path='.taskplane/bootstrap/../../app.py'
    if bad=='symlink':
        (tmp_path/'.taskplane/bootstrap').mkdir()
        (tmp_path/'.taskplane/bootstrap/link').symlink_to(tmp_path)
        path='.taskplane/bootstrap/link/app.py'
    tool={'tool_name':'Write','tool_input':{'file_path':str(tmp_path/path),'content':'source'}}
    with pytest.raises(w.Refusal):flow.hook(base|{'hook_event_name':'PreToolUse',**tool})


@pytest.mark.parametrize('absolute', [False, True])
def test_harness_bootstrap_accepts_native_metadata_paths(tmp_path, absolute):
    from pathlib import Path
    from taskplane import flow
    base={'cwd':str(tmp_path),'session_id':'root','hook_event_name':'UserPromptSubmit','prompt':'Use taskplane to review code.'}
    flow.hook(base)
    path=Path('.taskplane/bootstrap/scope.json')
    if absolute:path=tmp_path/path
    result=flow.hook(base|{'hook_event_name':'PreToolUse','tool_name':'Write',
                         'tool_input':{'file_path':str(path),'content':'scope'}})
    assert result.get('hookSpecificOutput',{}).get('permissionDecision') != 'deny'


def test_ordinary_installed_skill_read_does_not_activate_a_workflow(tmp_path):
    from pathlib import Path
    from taskplane import flow
    path=Path(flow.__file__).resolve().parents[1]/'skills/tp-engineering/SKILL.md'
    event={'cwd':str(tmp_path),'session_id':'root','hook_event_name':'PreToolUse','tool_name':'Read','tool_input':{'file_path':str(path)}}
    assert flow.hook(event) == {}
    assert not local.Harness(tmp_path,'root').read().get('selected')
    assert flow.hook(event|{'hook_event_name':'Stop'}) == {}


@pytest.mark.parametrize('tool', ['Read', 'exec_command'])
def test_completed_run_passive_skill_reread_preserves_followup_access(tmp_path, tool):
    from pathlib import Path
    import shlex
    from taskplane import flow
    c,s=setup(tmp_path,standalone=True)
    harness=local.Harness(tmp_path,c.root);harness.bind(s)
    s=decide(c,submit(c,s));s=c.apply('finish',s['run'],expected_revision=s['revision'])
    before=c._path().read_bytes()
    path=Path(flow.__file__).resolve().parents[1]/'skills/taskplane/SKILL.md'
    args={'file_path':str(path)} if tool=='Read' else {'cmd':shlex.join(['cat',str(path)])}
    event={'cwd':str(tmp_path),'session_id':'root','tool_name':tool,'tool_input':args}
    for _ in range(2):
        for name in ('PreToolUse','PostToolUse'):
            flow.hook(event|{'hook_event_name':name},governor=c)
    assert not harness.read()['selected'] and harness.read()['run']==s['run']
    followup=event|{'hook_event_name':'PreToolUse','tool_name':'exec_command','tool_input':{'cmd':'gh auth status'}}
    flow.hook(followup,governor=c)
    assert c._path().read_bytes()==before
    flow.hook(event|{'hook_event_name':'UserPromptSubmit','tool_input':{},
                    'prompt':'Use taskplane to review the next change.'},governor=c)
    flow.hook(event|{'hook_event_name':'PreToolUse'},governor=c)
    flow.hook(followup,governor=c)
    with pytest.raises(w.Refusal):
        flow.hook(followup|{'tool_name':'Write','tool_input':{'file_path':str(tmp_path/'app.py'),'content':'x'}},governor=c)


def test_completed_run_explicit_skill_invocation_still_requires_initialization(tmp_path):
    from taskplane import flow
    c,s=setup(tmp_path,standalone=True)
    harness=local.Harness(tmp_path,c.root);harness.bind(s)
    s=decide(c,submit(c,s));c.apply('finish',s['run'],expected_revision=s['revision'])
    event={'cwd':str(tmp_path),'session_id':'root','hook_event_name':'PreToolUse',
           'tool_name':'Skill','tool_input':{'skill':'taskplane:tp-build'}}
    assert flow.hook(event,governor=c)['hookSpecificOutput']['additionalContext']
    assert harness.read()['selected'] and harness.read()['run'] is None


@pytest.mark.parametrize('host', ['codex', 'claude'])
@pytest.mark.parametrize('tool,args', [
    ('Write', {'file_path': 'app.py', 'content': 'changed'}),
    ('Edit', {'file_path': 'app.py', 'old_string': 'value = 1', 'new_string': 'value = 2'}),
    ('apply_patch', {'patch': '*** Begin Patch\n*** Update File: app.py\n@@\n-value = 1\n+value = 2\n*** End Patch'}),
    ('Bash', {'command': "printf 'changed' > app.py"}),
    ('exec_command', {'cmd': "printf 'changed' > app.py"}),
])
def test_completed_run_followup_never_admits_mutation(tmp_path, host, tool, args):
    from taskplane import flow
    c, state = setup(tmp_path, standalone=True)
    c.adapter.name = host
    harness = local.Harness(tmp_path, c.root)
    harness.bind(state)
    state = decide(c, submit(c, state))
    c.apply('finish', state['run'], expected_revision=state['revision'])
    before = c._path().read_bytes()
    event = {'cwd': str(tmp_path), 'session_id': 'root', 'hook_event_name': 'PreToolUse',
             'tool_name': tool, 'tool_input': args, 'tool_use_id': 'actual-followup-call'}
    with pytest.raises(w.Refusal, match='Enter flow repair'):
        flow.hook(event, governor=c)
    assert c._path().read_bytes() == before
    assert c.report(state['run'], diagnostics=False)['finished']
    assert not harness.read()['selected']


def test_harness_handoff_and_wait_cannot_cross_bindings(tmp_path):
    from taskplane import flow
    c,s=setup(tmp_path);harness=local.Harness(c.workspace,c.root)
    harness.bind(s)
    harness.wait(c.report(),'Need a comparison revision')
    assert 'waiting' in harness.stop({},c.report())['systemMessage']
    s=submit(c,s,presentation=False)
    assert harness.stop({},c.report())=={}
    page=flow.publish_dashboard(c.workspace,s['run'],governor=c,select=True)
    harness.present(c.report(),str(page.relative_to(c.workspace)),'blocked','Fixture link available and host opening unavailable')
    assert harness.stop({},c.report())=={}
    changed=deepcopy(c.report());changed['run']='another-run'
    with pytest.raises(w.Refusal,match='current run'):harness.present(changed,str(page.relative_to(c.workspace)),'verified','An incorrect claim')
    assert 'decision' not in harness.stop({},changed)
    raw=harness.read();raw['workspace']='foreign';harness.path.write_text(json.dumps(raw))
    with pytest.raises(w.Refusal,match='identity'):harness.read()


@pytest.mark.parametrize('host', ['codex', 'claude'])
def test_stop_is_advisory_without_retry_state_or_phase_authority(tmp_path, host):
    from taskplane import flow
    c, state = setup(tmp_path)
    c.adapter.name = host
    harness = local.Harness(tmp_path, c.root)
    harness.bind(state)
    controller_before = c._path().read_bytes()
    harness_before = harness.path.read_bytes()
    # Missing evidence, failed review, and stale evidence all permit a handoff.
    for status in ('not_requested', 'changes_requested', 'rejected', 'stale'):
        pending = deepcopy(state)
        w.current(pending)['decision'] = status
        for event in ({}, {'stop_hook_active': True}):
            result = harness.stop(event, pending)
            assert result.get('systemMessage') and 'decision' not in result
    stale = deepcopy(state)
    stale['invalidation_pending'] = True
    assert 'stale' in harness.stop({}, stale)['systemMessage']
    assert harness.path.read_bytes() == harness_before
    event = {'cwd': str(tmp_path), 'session_id': c.root, 'host': host}
    for _ in range(3):
        result = flow.hook(event | {'hook_event_name': 'Stop'}, governor=c)
        assert result.get('systemMessage') and 'decision' not in result
    with pytest.raises(w.Refusal):
        c.apply('advance', state['run'], expected_revision=state['revision'], phase='design')
    with pytest.raises(w.Refusal):
        c.guard({'tool_name': 'Write', 'tool_input': {'file_path': 'app.py'}}, state['run'])
    assert c._path().read_bytes() == controller_before


def test_wait_survives_read_tools_and_automatic_stop_continuation(tmp_path):
    from taskplane import flow
    c, state = setup(tmp_path)
    harness = local.Harness(tmp_path, c.root)
    harness.bind(state)
    harness.wait(c.report(), 'Awaiting the reported repair decision')
    expected = harness.read()['waiting']
    event = {'cwd': str(tmp_path), 'session_id': c.root}
    flow.hook(event | {'hook_event_name': 'PreToolUse', 'tool_name': 'Read',
        'tool_input': {'file_path': str(tmp_path / 'app.py')}}, governor=c)
    flow.hook(event | {'hook_event_name': 'UserPromptSubmit',
        'prompt': '<hook_prompt hook_run_id="stop:12">Continue</hook_prompt>'}, governor=c)
    assert harness.read()['waiting'] == expected
    assert 'waiting for user input' in flow.hook(event | {'hook_event_name': 'Stop'}, governor=c)['systemMessage']
    flow.hook(event | {'hook_event_name': 'UserPromptSubmit', 'prompt': 'Proceed with changes'}, governor=c)
    assert harness.read()['waiting'] is None


@pytest.mark.parametrize('prompt,entry',[
    ('taskplane build a design tool and review the result.','taskplane'),
    ('Taskplane implement this feature with a product specification.','taskplane'),
    ('Please taskplane design the review page.','tp-design'),
    ('taskplane review the product design.','tp-engineering'),
    ('taskplane audit this implementation.','tp-engineering'),
    ('taskplane product define acceptance criteria.','tp-product'),
])
def test_bare_execution_directive_keeps_its_requested_route(tmp_path,prompt,entry):
    from taskplane import flow
    event={'cwd':str(tmp_path),'session_id':'root','hook_event_name':'UserPromptSubmit','prompt':prompt}
    flow.hook(event)
    harness=local.Harness(tmp_path,'root')
    assert harness.read()['entry']==entry
    assert flow.hook(event|{'hook_event_name':'Stop'}).get('systemMessage')
    assert not list((tmp_path/'.taskplane').glob('workflow-*.json'))


@pytest.mark.parametrize('key', ['command', 'input', 'patch'])
def test_codex_patch_payload_preserves_bootstrap_and_phase_scope(tmp_path, key):
    from taskplane import flow
    base={'cwd':str(tmp_path),'thread_id':'root','hook_event_name':'UserPromptSubmit',
          'prompt':'Use taskplane to define product requirements.'}
    flow.hook(base)
    def patch(*paths):
        return {'tool_name':'apply_patch','tool_input':{key:'*** Begin Patch\n'+''.join(
            '*** Add File: '+path+'\n+{}\n' for path in paths)+'*** End Patch'}}
    allowed='.taskplane/bootstrap/scope.json'
    flow.hook(base|{'hook_event_name':'PreToolUse',**patch(allowed)})
    for bad in ('app.py','.taskplane/workflow-forged.json','.taskplane/bootstrap/../../app.py'):
        with pytest.raises(w.Refusal):
            flow.hook(base|{'hook_event_name':'PreToolUse',**patch(allowed,bad)})
    c,s=setup(tmp_path)
    c.guard(patch('product.json'),s['run'])
    with pytest.raises(w.Refusal,match='outside'):
        c.guard(patch('product.json','app.py'),s['run'])
    s=submit(c,s)
    with pytest.raises(w.Refusal,match='sealed'):
        c.guard(patch('product.json'),s['run'])


def recovery_fixture(tmp_path):
    import subprocess
    source = tmp_path/'source'; source.mkdir()
    def git(*args, cwd=source):
        subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True)
    # A legacy folder run later became a shared repository. Recovery must
    # remain available even though a new run in this checkout is now refused.
    c, s = setup(source); s = submit(c, s)
    git('init', '-q')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-q', '--allow-empty', '-m', 'Fixture')
    destination = tmp_path/'clean'
    git('worktree', 'add', '--detach', str(destination), 'HEAD')
    harness = local.Harness(source, 'root')
    create = {'tool_name':'mcp__codex_app__create_worktree', 'call_id':'create-1',
              'tool_input':{'name':'recovery-fixture', 'ref':'HEAD', 'allowAsync':True}}
    paths = {'worktreeGitRoot':str(destination), 'worktreeWorkspaceRoot':str(destination)}
    return c, s, harness, create, paths, git


def observe_worktree(harness, event, state, payload, wrapper='direct'):
    if wrapper == 'text':
        payload = {'content':[{'type':'text', 'text':json.dumps(payload)}]}
    elif wrapper == 'structured':
        payload = {'structuredContent':payload}
    harness.observe_recovery({**event, 'tool_response':payload}, state)


def pending_worktree(c, s, harness, create):
    c.guard(create, s['run'])
    observe_worktree(harness, create, s, {'type':'pending', 'operationId':'operation-1'})
    assert not harness.read().get('recovery_workspace')
    return {'tool_name':'mcp__codex_app__get_worktree_creation_status', 'call_id':'poll-1',
            'tool_input':{'operationId':'operation-1'}}


@pytest.mark.parametrize('wrapper', ['direct', 'text', 'structured'])
def test_current_async_worktree_recovery_waits_for_correlated_completion(tmp_path, wrapper):
    c, s, harness, create, paths, _ = recovery_fixture(tmp_path)
    poll = pending_worktree(c, s, harness, create)
    pending = harness.read()['recovery_pending']
    assert pending['call_id'] == create['call_id'] and pending['operation_id'] == 'operation-1'
    assert pending['input_digest'] == local.primitives.content_fingerprint(create['tool_input'])
    for index, status in enumerate(['preparing', 'creating', 'registering', 'completed']):
        poll['call_id'] = f'poll-{index}'
        c.guard(poll, s['run'])
        payload = {'operationId':'operation-1', 'status':status, **paths}
        observe_worktree(harness, poll, s, payload, wrapper)
        if status != 'completed':
            assert not harness.read().get('recovery_workspace')
            assert 'poll' not in harness.read()['recovery_pending']
        else:
            assert harness.read()['recovery_workspace']['workspace'] == paths['worktreeWorkspaceRoot']
            assert not harness.read().get('recovery_pending')
    c.guard({'tool_name':'Write', 'tool_input':{'path':paths['worktreeWorkspaceRoot']+'/.taskplane/bootstrap/scope.json'}}, s['run'])
    with pytest.raises(w.Refusal):
        c.guard({'tool_name':'Write', 'tool_input':{'path':paths['worktreeWorkspaceRoot']+'/app.py'}}, s['run'])
    assert c.report()['revision'] == s['revision'] and not c.report()['decisions']


@pytest.mark.parametrize('args', [
    {'allowAsync':True}, {'allowAsync':False, 'ref':'HEAD'}, {'allowAsync':1, 'ref':'HEAD'},
    {'allowAsync':'true', 'ref':'HEAD'}, {'allowAsync':True, 'ref':'main'},
    {'allowAsync':True, 'ref':'HEAD', 'extra':True},
])
def test_current_worktree_requires_literal_async_and_explicit_head(tmp_path, args):
    c, s, harness, create, _, _ = recovery_fixture(tmp_path)
    with pytest.raises(w.Refusal):
        c.guard({**create, 'tool_input':args}, s['run'])
    assert not harness.read().get('recovery_pending')


@pytest.mark.parametrize('legacy', [False, True])
def test_worktree_immediate_result_preserves_legacy_and_current_contracts(tmp_path, legacy):
    c, s, harness, create, paths, _ = recovery_fixture(tmp_path)
    if legacy:
        create['tool_input'] = {'name':'recovery-fixture'}
    c.guard(create, s['run'])
    observe_worktree(harness, create, s, {'type':'created', **paths})
    assert harness.read()['recovery_workspace']['workspace'] == paths['worktreeWorkspaceRoot']


@pytest.mark.parametrize('defect', [
    'wrong-call', 'wrong-input', 'unadmitted-poll', 'foreign-operation', 'missing-operation',
    'failed', 'malformed-status', 'unknown-shape', 'tool-error', 'binding', 'source-head',
])
def test_async_worktree_rejects_uncorrelated_or_failed_completion(tmp_path, defect):
    c, s, harness, create, paths, git = recovery_fixture(tmp_path)
    poll = pending_worktree(c, s, harness, create)
    if defect != 'unadmitted-poll':
        c.guard(poll, s['run'])
    payload = {'operationId':'operation-1', 'status':'completed', **paths}
    state = s
    if defect == 'wrong-call': poll['call_id'] = 'foreign-call'
    elif defect == 'wrong-input': poll['tool_input']['operationId'] = 'foreign'
    elif defect == 'foreign-operation': payload['operationId'] = 'foreign'
    elif defect == 'missing-operation': payload.pop('operationId')
    elif defect == 'failed': payload['status'] = 'failed'
    elif defect == 'malformed-status': payload['status'] = []
    elif defect == 'unknown-shape': payload = {'operationId':'operation-1', 'status':'completed', 'result':paths}
    elif defect == 'tool-error': payload = {'isError':True, **payload}
    elif defect == 'binding': state = {**s, 'revision':s['revision']+1}
    elif defect == 'source-head':
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '-q', '--allow-empty', '-m', 'Changed')
    observe_worktree(harness, poll, state, payload)
    assert not harness.read().get('recovery_workspace')


@pytest.mark.parametrize('defect', ['operation', 'extra-input', 'binding', 'source-head'])
def test_async_poll_admission_remains_bound_to_original_request(tmp_path, defect):
    c, s, harness, create, _, git = recovery_fixture(tmp_path)
    poll = pending_worktree(c, s, harness, create)
    state = s
    if defect == 'operation': poll['tool_input']['operationId'] = 'foreign'
    elif defect == 'extra-input': poll['tool_input']['other'] = True
    elif defect == 'binding': state = {**s, 'revision':s['revision']+1}
    elif defect == 'source-head':
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '-q', '--allow-empty', '-m', 'Changed')
    assert not harness.recovery_action(poll, state)
    assert not harness.read().get('recovery_workspace')


@pytest.mark.parametrize('asynchronous', [True, False])
@pytest.mark.parametrize('defect', ['relative', 'source', 'subpath', 'symlink', 'other-head'])
def test_every_worktree_destination_requires_identical_checkout_identity(tmp_path, asynchronous, defect):
    from pathlib import Path
    c, s, harness, create, paths, git = recovery_fixture(tmp_path)
    if asynchronous:
        event = pending_worktree(c, s, harness, create)
        c.guard(event, s['run'])
        payload = {'operationId':'operation-1', 'status':'completed', **paths}
    else:
        create['tool_input'] = {'name':'legacy-recovery'}
        event = create; c.guard(event, s['run'])
        payload = {'type':'created', **paths}
    destination = Path(paths['worktreeWorkspaceRoot'])
    if defect == 'relative': payload['worktreeWorkspaceRoot'] = 'clean'
    elif defect == 'source':
        payload.update(worktreeWorkspaceRoot=str(c.workspace), worktreeGitRoot=str(c.workspace))
    elif defect == 'subpath':
        (destination/'subdir').mkdir(); payload['worktreeWorkspaceRoot'] += '/subdir'
    elif defect == 'symlink':
        (tmp_path/'alias').symlink_to(destination, target_is_directory=True)
        payload.update(worktreeWorkspaceRoot=str(tmp_path/'alias'), worktreeGitRoot=str(tmp_path/'alias'))
    elif defect == 'other-head':
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '-q', '--allow-empty', '-m', 'Other HEAD', cwd=destination)
    observe_worktree(harness, event, s, payload)
    assert not harness.read().get('recovery_workspace')
    assert not harness.read().get('recovery_pending')
