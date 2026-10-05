"""Claude and Codex share one flow without sharing transcript formats."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import hashlib
import shlex
import pytest

from taskplane import claude_flow_usage as claude, flow

ROOT = Path(__file__).resolve().parents[2]


def bound_workspace(tmp_path, monkeypatch, policy='any'):
    from taskplane.tests.binding_support import require_binding_runtime
    require_binding_runtime()
    from taskplane import workspace_binding as binding
    for key in list(os.environ):
        if key.startswith(('CODEX_', 'CLAUDE_', 'TASKPLANE_')):
            monkeypatch.delenv(key)
    ws=tmp_path/'selected'; ws.mkdir()
    (ws/'host-proof.txt').write_text('host-created fixture nonce')
    request={'schema':'taskplane.workspace-request/v1','surface':'cowork',
        'host_root':'/Users/fixture/farm-viewer','execution_root':str(ws), 'policy':policy,
        'execution':{'location':'local','reference':'fixture/host-execution'},
        'worker':{'location':'local','reference':'fixture/host-worker'},
        'probe':{'path':'host-proof.txt','sha256':hashlib.sha256((ws/'host-proof.txt').read_bytes()).hexdigest(),
                 'host_reference':'fixture/host-file-observation'}}
    for kind in ('EXECUTION','WORKER'):
        monkeypatch.setenv('TASKPLANE_'+kind+'_LOCATION','local')
        monkeypatch.setenv('TASKPLANE_'+kind+'_REFERENCE','fixture/current-'+kind.lower())
    record=binding.bind(ws,request)
    monkeypatch.setenv('TASKPLANE_WORKSPACE',str(ws))
    monkeypatch.setenv('TASKPLANE_SURFACE','cowork')
    monkeypatch.setenv('TASKPLANE_CLAUDE_SESSION_ID','root')
    return ws,record


def test_cowork_cli_and_hooks_share_selected_root_without_scratch_store(tmp_path,monkeypatch,capsys):
    from taskplane.tests.test_native_workflow_cli import create
    ws,binding=bound_workspace(tmp_path,monkeypatch)
    create(ws)
    scratch=tmp_path/'scratch';scratch.mkdir();monkeypatch.chdir(scratch)
    command=shlex.join([sys.executable,str(ROOT/'taskplane/tp.py'),'flow','start',
        '--standalone','--phase','product','--scope','.taskplane/scope.json',
        '--request-reference','fixture/split-cwd'])
    event={'hook_event_name':'PreToolUse','cwd':str(scratch),'session_id':'root',
           'tool_name':'Bash','tool_input':{'command':command}}
    flow.hook(event)
    assert flow.main(['start','--standalone','--phase','product','--scope','.taskplane/scope.json',
                      '--request-reference','fixture/split-cwd'])==0
    state=json.loads(capsys.readouterr().out)['workflow']
    assert state['workspace']==str(ws.resolve())
    assert state['workspace_contract']=={k:binding[k] for k in ('project_id','digest')}
    event['hook_event_name']='PostToolUse'
    event['taskplane_workspace_contract']={'digest':'caller-forged'}
    flow.hook(event)
    assert flow.read_events(ws)[-1]['workspace_contract']==state['workspace_contract']
    assert not (scratch/'.taskplane').exists()


@pytest.mark.parametrize('tool,key', [('Bash','command'), ('exec_command','cmd')])
@pytest.mark.parametrize('operation', [('flow','report'), ('dashboard',)])
@pytest.mark.parametrize('selection', ['execution', 'alias', 'configured'])
def test_bound_checkpoint_controls_use_selected_workspace(tmp_path,monkeypatch,tool,key,operation,selection):
    from taskplane.tests.test_workflow_local import setup, submit
    from taskplane import workflow as w
    ws,contract=bound_workspace(tmp_path,monkeypatch)
    controller,state=setup(ws); state=submit(controller,state)
    scratch=tmp_path/'scratch'; scratch.mkdir(); monkeypatch.chdir(scratch)
    selected={'execution':str(ws),'alias':contract['host_root'],'configured':None}[selection]
    args=[sys.executable,str(ROOT/'taskplane/tp.py'),*operation]
    if selected is not None: args+=['--workspace',selected]
    event={'hook_event_name':'PreToolUse','cwd':str(scratch),'session_id':'root',
           'tool_name':tool,'tool_input':{key:shlex.join(args)}}
    flow.hook(event,governor=controller)
    assert w.current(controller.report())['decision']=='awaiting_human_approval'
    assert controller.report()['revision']==state['revision']
    assert not (scratch/'.taskplane').exists()


def test_bound_checkpoint_controls_keep_exact_command_boundaries(tmp_path,monkeypatch):
    from taskplane.tests.test_workflow_local import setup, submit
    from taskplane import workflow as w
    ws,contract=bound_workspace(tmp_path,monkeypatch)
    controller,state=setup(ws); submit(controller,state)
    scratch=tmp_path/'scratch'; scratch.mkdir(); monkeypatch.chdir(scratch)
    prefix=[sys.executable,str(ROOT/'taskplane/tp.py')]
    def guard(args):
        return flow.hook({'hook_event_name':'PreToolUse','cwd':str(scratch),'session_id':'root',
                          'tool_name':'Bash','tool_input':{'command':shlex.join(args)}},governor=controller)
    guard([*prefix,'flow','report','--workspace='+contract['host_root']])
    invalid=[
        [*prefix,'flow','report','--workspace',str(scratch)],
        [*prefix,'flow','report','--workspace='+str(scratch)],
        [*prefix,'flow','report','--workspace',str(ws),'--workspace',contract['host_root']],
        [*prefix,'flow','report','--workspace',str(ws),'--workspace='+str(ws)],
        [*prefix,'flow','report','--workspace'],
        [*prefix,'flow','report','--workspace='],
        [*prefix,'dashboard','--out','foreign.html'],
        [*prefix,'dashboard','--run','foreign'],
        [sys.executable,str(scratch/'tp.py'),'flow','report'],
        [sys.executable,str(ROOT/'taskplane/tp.py'),'flow','unsupported'],
    ]
    for args in invalid:
        with pytest.raises(w.Refusal): guard(args)
    monkeypatch.setenv('TASKPLANE_WORKSPACE',str(scratch))
    with pytest.raises(w.Refusal): guard([*prefix,'flow','report'])
    assert not (scratch/'.taskplane').exists()


def test_cowork_missing_selection_refuses_before_any_state_and_abstains_unrelated(tmp_path,monkeypatch,capsys):
    from taskplane import workflow
    for key in list(os.environ):
        if key.startswith(('CODEX_', 'CLAUDE_', 'TASKPLANE_')):monkeypatch.delenv(key)
    monkeypatch.setenv('TASKPLANE_SURFACE','cowork');monkeypatch.chdir(tmp_path)
    unrelated={'hook_event_name':'PreToolUse','cwd':str(tmp_path),'session_id':'root',
               'tool_name':'Bash','tool_input':{'command':'pwd'}}
    assert flow.hook(unrelated)=={}
    selected={**unrelated,'tool_input':{'command':shlex.join(
        [sys.executable,str(ROOT/'taskplane/tp.py'),'flow','activate','--phase','tp-go'])}}
    with pytest.raises(workflow.Refusal):flow.hook(selected)
    assert flow.main(['activate','--phase','tp-go','--request-reference','fixture/selection'],compact=True)==2
    capsys.readouterr()
    assert not (tmp_path/'.taskplane').exists()


@pytest.mark.parametrize('selection', ['missing_configured', 'unbound_configured', 'unbound_cwd'])
@pytest.mark.parametrize('task', [None, 'COW-CONTEXT'])
def test_context_binding_refusal_without_workspace_is_structured_and_writes_nothing(tmp_path, monkeypatch, selection, task):
    scratch = tmp_path/'scratch'; scratch.mkdir(); monkeypatch.chdir(scratch)
    selected = tmp_path/'selected'
    if selection != 'missing_configured': selected.mkdir()
    monkeypatch.setenv('TASKPLANE_SURFACE', 'cowork')
    if selection != 'unbound_cwd': monkeypatch.setenv('TASKPLANE_WORKSPACE', str(selected))
    before = {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    args = [sys.executable, str(ROOT/'taskplane/tp.py'), 'flow', 'context']
    if task is not None: args += ['--task', task]
    result = subprocess.run(args, cwd=scratch, capture_output=True, text=True)
    assert result.returncode == 2 and not result.stderr
    payload = json.loads(result.stdout)
    assert payload['status'] == 'blocked' and payload['reason'] == 'workspace_binding'
    assert 'next_action' not in payload  # An unresolved root cannot supply a corrective command.
    assert {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    assert not (scratch/'.taskplane').exists() and not (selected/'.taskplane').exists()


@pytest.mark.parametrize('selection', ['configured', 'cwd'])
def test_context_refusal_uses_resolved_workspace_in_retry(tmp_path, monkeypatch, capsys, selection):
    from taskplane import workflow as w
    from taskplane.tests.test_workflow_local import setup
    selected, _ = bound_workspace(tmp_path, monkeypatch)
    if selection == 'cwd':
        monkeypatch.delenv('TASKPLANE_WORKSPACE')
    controller, state = setup(selected)
    scratch = tmp_path/'scratch'; scratch.mkdir()
    monkeypatch.chdir(scratch if selection == 'configured' else selected)
    if selection == 'configured': monkeypatch.setenv('TASKPLANE_WORKSPACE', str(selected))
    monkeypatch.setenv('CODEX_THREAD_ID', controller.root)
    def refuse(*args, **kwargs):
        raise w.Refusal('stale_checkpoint', 'Fixture stale context handoff.')
    monkeypatch.setattr(controller, 'context', refuse)
    before = controller._path().read_bytes()
    assert flow.main(['context', '--run', state['run']], governor=controller) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload['reason'] == 'stale_checkpoint'
    assert str(selected) in payload['next_action'] and str(scratch) not in payload['next_action']
    assert controller._path().read_bytes() == before and not (scratch/'.taskplane').exists()


def test_workspace_admin_is_narrow_and_available_before_binding(tmp_path,monkeypatch):
    monkeypatch.setenv('TASKPLANE_SURFACE','cowork');monkeypatch.delenv('TASKPLANE_WORKSPACE',raising=False)
    words=[sys.executable,str(ROOT/'taskplane/tp.py'),'workspace','bind','--workspace',str(tmp_path),
           '--request',str(tmp_path/'request.json')]
    event={'hook_event_name':'PreToolUse','cwd':str(tmp_path),'session_id':'root',
           'tool_name':'Bash','tool_input':{'command':shlex.join(words)}}
    assert flow._workspace_admin(event)
    assert flow.hook(event)=={}
    for command in (shlex.join(words)+'; touch bad',shlex.join(words+['--extra','bad']),
                    shlex.join([sys.executable,str(tmp_path/'tp.py'),*words[2:]])):
        assert not flow._workspace_admin({**event,'tool_input':{'command':command}})
    assert not flow._workspace_admin({**event,'parent_session_id':'parent'})
    assert not (tmp_path/'.taskplane').exists()


def test_selected_workspace_export_is_validated_before_claude_environment_write(tmp_path,monkeypatch):
    ws,_=bound_workspace(tmp_path,monkeypatch,policy='local')
    target=tmp_path/'claude.env';monkeypatch.setenv('CLAUDE_ENV_FILE',str(target))
    event={'hook_event_name':'SessionStart','session_id':'root','cwd':str(tmp_path),
           'transcript_path':str(tmp_path/'transcript.jsonl')}
    claude.bind_session(event)
    assert 'export TASKPLANE_WORKSPACE='+shlex.quote(str(ws.resolve())) in target.read_text()
    before=target.read_bytes();monkeypatch.delenv('TASKPLANE_EXECUTION_REFERENCE')
    from taskplane.workflow import Refusal
    with pytest.raises(Refusal):claude.bind_session(event)
    assert target.read_bytes()==before


def test_legacy_local_runtime_does_not_require_posix_binding_operations(tmp_path,monkeypatch):
    from taskplane import workspace_binding as binding, workflow_host
    from taskplane.tests.test_native_workflow_cli import create
    for key in list(os.environ):
        if key.startswith('TASKPLANE_'):monkeypatch.delenv(key)
    create(tmp_path)
    monkeypatch.setattr(binding.os,'supports_dir_fd',set())
    assert binding.ensure(tmp_path) is None
    assert binding.describe(tmp_path)['status']=='unbound'
    c=workflow_host.Controller(tmp_path,'legacy',workflow_host.installed_adapter('claude'))
    scope=json.loads((tmp_path/'.taskplane/scope.json').read_text())
    assert c.start({'scope':scope,'entry':'product','standalone':True,
                    'request_reference':'fixture/legacy-platform'})['run']
    monkeypatch.setenv('TASKPLANE_SURFACE','cowork')
    from taskplane.workflow import Refusal
    with pytest.raises(Refusal,match='no-follow directory'):binding.ensure(tmp_path)


def test_cloud_scratch_signal_never_establishes_locality():
    from taskplane import workspace_binding as binding
    assert binding._required(Path('/home/claude/project'))


def message(mid='m1', *, at='2026-09-15T00:00:01Z', agent=None, output=10):
    return {'type': 'assistant', 'sessionId': 'root', 'agentId': agent,
            'isSidechain': bool(agent), 'timestamp': at,
            'message': {'id': mid, 'role': 'assistant', 'content': 'private content',
                        'usage': {'input_tokens': 2, 'cache_creation_input_tokens': 30,
                                  'cache_read_input_tokens': 100, 'output_tokens': output}}}


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def setup(tmp_path, monkeypatch):
    ws = tmp_path / 'workspace'
    ws.mkdir()
    transcript = tmp_path / 'claude/projects/project/root.jsonl'
    write(transcript, [message()])
    monkeypatch.setenv('TASKPLANE_CLAUDE_SESSION_ID', 'root')
    monkeypatch.setenv('TASKPLANE_CLAUDE_TRANSCRIPT', str(transcript))
    monkeypatch.setenv('CODEX_THREAD_ID', 'unrelated-codex-parent')
    run = {'kind': 'start', 'host': 'claude', 'session': 'root', 'run': 'shared',
           'goal': 'Shared Claude delivery', 'at': '2026-09-15T00:00:02Z',
           'transcript_path': str(transcript), 'usage': claude.read_snapshot(transcript, 'root')['usage']}
    flow.append(ws, run)
    return ws, transcript


def test_streamed_messages_deduplicate_and_include_cache_writes(tmp_path):
    path = tmp_path / 'root.jsonl'
    rows = [message(output=1), message(), message(), message('m2')]
    rows.append({'type': 'user', 'sessionId': 'root', 'message': {'usage': {'output_tokens': 99999}}})
    write(path, rows)
    snapshot = claude.read_snapshot(path, 'root')
    assert snapshot['usage'] == {'input_tokens': 264, 'cached_input_tokens': 200,
                                 'uncached_input_tokens': 64, 'output_tokens': 20,
                                 'reasoning_tokens': 0, 'total_tokens': 284}
    assert not snapshot['partial']


def test_incomplete_provider_usage_is_unknown_not_zero(tmp_path):
    path = tmp_path / 'root.jsonl'
    row = message()
    del row['message']['usage']['cache_read_input_tokens']
    write(path, [row])
    assert claude.read_snapshot(path, 'root')['usage'] is None
    write(path, [row, message('m2')])
    assert claude.read_snapshot(path, 'root')['partial']


def test_streamed_worker_boundary_increments_conserve_lifetime(tmp_path):
    path = tmp_path / 'root.jsonl'
    write(path, [message('root')])
    child = path.with_suffix('') / 'subagents/agent-reused.jsonl'
    write(child, [message('streamed', agent='reused', output=1, at='2026-09-15T00:00:05Z'),
                  message('streamed', agent='reused', output=10, at='2026-09-15T00:00:10Z'),
                  message('streamed', agent='reused', output=10, at='2026-09-15T00:00:15Z')])
    boundary = claude.timestamp('2026-09-15T00:00:10Z')
    run = {'run': 'first', 'session': 'root', 'host': 'claude', 'transcript_path': str(path),
           'at': '2026-09-15T00:00:00Z', 'usage': claude.read_snapshot(path, 'root')['usage']}
    earlier, _ = claude.sessions(run, [], boundary)
    later, _ = claude.sessions({**run, 'run': 'next', 'at': '2026-09-15T00:00:10Z'}, [], None)
    a = next(s for s in earlier if s['session'] == 'reused')
    b = next(s for s in later if s['session'] == 'reused')
    lifetime = claude.read_snapshot(child, 'root', agent='reused')['usage']
    assert a['usage']['total_tokens'] == 133
    assert b['usage']['total_tokens'] == b['usage']['output_tokens'] == 9
    assert b['usage']['input_tokens'] == b['usage']['cached_input_tokens'] == 0
    assert a['status'] == b['status'] == 'measured'
    assert {key: a['usage'][key] + b['usage'][key] for key in lifetime} == lifetime


def test_streamed_missing_boundary_and_category_decrease_stay_partial(tmp_path):
    path = tmp_path / 'root.jsonl'
    before = message(output=1, at='2026-09-15T00:00:05Z')
    after = message(output=10, at='2026-09-15T00:00:15Z')
    boundary = claude.timestamp('2026-09-15T00:00:10Z')
    del before['message']['usage']['cache_read_input_tokens']
    write(path, [before, after])
    interval = claude.read_snapshot(path, 'root', start=boundary)
    assert interval['usage'] is None and interval['partial']
    assert claude.read_snapshot(path, 'root')['usage']['total_tokens'] == 142
    before = message(output=1, at='2026-09-15T00:00:05Z')
    after['message']['usage']['cache_read_input_tokens'] = 99
    write(path, [before, after])
    interval = claude.read_snapshot(path, 'root', start=boundary)
    assert interval['usage'] is None and interval['partial']
    assert 'counter_decreased' in interval['errors']


def test_inactive_claude_worker_is_zero_only_when_observed(tmp_path):
    path = tmp_path / 'root.jsonl'
    write(path, [message()])
    child = path.with_suffix('') / 'subagents/agent-old.jsonl'
    write(child, [message(agent='old')])
    run = {'run': 'run', 'session': 'root', 'at': '2026-09-15T00:00:10Z',
           'transcript_path': str(path), 'usage': claude.read_snapshot(path, 'root')['usage']}
    sessions, _ = claude.sessions(run, [], None)
    assert [s['session'] for s in sessions] == ['root']
    sessions, _ = claude.sessions({**run, 'worker_sessions': ['old']}, [], None)
    old = next(s for s in sessions if s['session'] == 'old')
    assert old['status'] == 'measured' and old['usage']['total_tokens'] == 0


def test_discovers_lens_counters_and_excludes_other_runs(tmp_path, monkeypatch):
    ws, path = setup(tmp_path, monkeypatch)
    write(path, [message(), message('m2', at='2026-09-15T00:00:03Z')])
    child = path.with_suffix('') / 'subagents/agent-design.jsonl'
    write(child, [message(agent='design', at='2026-09-15T00:00:04Z')])
    write(child.with_name('agent-old.jsonl'), [message(agent='old')])
    # Same folder, wrong native identity: never attribute its counters.
    row = message(agent='other', at='2026-09-15T00:00:04Z')
    row['sessionId'] = 'unrelated'
    write(child.with_name('agent-other.jsonl'), [row])
    report = flow.report(ws)
    assert report['tokens']['total_tokens'] == 284
    assert report['native_tokens']['total_tokens'] == 426
    assert report['token_coverage']['measured_sessions'] == 2
    assert 'old' not in {s['session'] for s in report['sessions']}
    assert next(s for s in report['sessions'] if s['session'] == 'other')['usage'] is None
    assert report['host_approval_tokens'] is None


def test_finished_flow_includes_late_results_until_next_run(tmp_path, monkeypatch):
    ws, path = setup(tmp_path, monkeypatch)
    flow.append(ws, {'kind': 'finish', 'run': 'shared', 'session': 'root', 'note': 'Done'})
    write(path, [message(), message('late', at='2026-09-15T00:00:05Z')])
    assert flow.report(ws)['tokens']['total_tokens'] == 142
    flow.append(ws, {'kind': 'start', 'run': 'next', 'session': 'root', 'at': '2026-09-15T00:00:06Z'})
    write(path, [message(), message('late', at='2026-09-15T00:00:05Z'),
                 message('next', at='2026-09-15T00:00:07Z')])
    write(path.with_suffix('') / 'subagents/agent-next.jsonl',
          [message(agent='next', at='2026-09-15T00:00:07Z')])
    report = flow.report(ws, 'shared')
    assert report['tokens']['total_tokens'] == 142
    assert len(report['sessions']) == 1


def test_claude_binding_overrides_inherited_codex_session(tmp_path, monkeypatch):
    target = tmp_path / 'env'
    monkeypatch.setenv('CLAUDE_ENV_FILE', str(target))
    event = {'hook_event_name': 'SessionStart', 'cwd': str(tmp_path),
             'session_id': 'claude-session', 'transcript_path': "/tmp/a ' quoted.jsonl"}
    assert flow.hook(event) == {}
    assert not (tmp_path / flow.JOURNAL).exists()
    bash = shutil.which('bash')
    if os.name == 'nt':
        # Use Git Bash, not the Windows WSL launcher named bash.exe.
        git = Path(shutil.which('git') or '')
        bash = next((str(parent / 'bin/bash.exe') for parent in git.parents
                     if (parent / 'bin/bash.exe').is_file()), bash)
    assert bash, 'Claude environment binding requires Bash'
    result = subprocess.run([bash, '-c', '. ./env; printf "%s\n%s" "$TASKPLANE_CLAUDE_SESSION_ID" "$TASKPLANE_CLAUDE_TRANSCRIPT"'],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "claude-session\n/tmp/a ' quoted.jsonl"
    monkeypatch.setenv('TASKPLANE_CLAUDE_SESSION_ID', 'claude-session')
    monkeypatch.setenv('CODEX_THREAD_ID', 'inherited-codex')
    assert flow.session_id({}) == 'claude-session'


def test_subagent_stop_retains_usage_without_private_content(tmp_path, monkeypatch, capsys):
    ws, path = setup(tmp_path, monkeypatch)
    child = tmp_path / 'custom/child.jsonl'
    write(child, [message(agent='lens', at='2026-09-15T00:00:03Z')])
    assert flow.hook({'hook_event_name': 'SubagentStop', 'session_id': 'root',
                      'cwd': str(ws), 'transcript_path': str(path), 'agent_id': 'lens',
                      'agent_transcript_path': str(child), 'last_assistant_message': 'private content'}) == {}
    (ws / 'reviews.json').write_text(json.dumps([{'agent': 'lens', 'lens': 'design'}]))
    flow.main(['attach', '--workspace', str(ws), '--reviews', 'reviews.json'])
    report = json.loads(capsys.readouterr().out)
    assert report['token_coverage']['unmeasured_sessions'] == 0
    assert report['tokens']['total_tokens'] == 142
    assert 'private content' not in (ws / flow.JOURNAL).read_text()
    assert 'Task decomposition' in (ws / flow.DASHBOARD).read_text()
    child.unlink()
    assert flow.report(ws)['tokens']['total_tokens'] == 142


def test_claude_hooks_prefer_current_plugin_over_stale_codex_launcher(tmp_path):
    stale = tmp_path / '.taskplane/codex-hook.py'
    stale.parent.mkdir()
    stale.write_text('raise SystemExit(99)')
    env_file = tmp_path / 'env'
    hooks = json.loads((ROOT / 'hooks/hooks.json').read_text())['hooks']
    for name, groups in hooks.items():
        for group in groups:
            result = subprocess.run(group['hooks'][0]['commandWindows' if os.name == 'nt' else 'command'], shell=True, cwd=tmp_path,
                input=json.dumps({'hook_event_name': name, 'cwd': str(tmp_path), 'session_id': 'root',
                                  'transcript_path': str(tmp_path / 'root.jsonl')}),
                capture_output=True, text=True,
                env={**os.environ, 'CLAUDE_PLUGIN_ROOT': str(ROOT), 'PLUGIN_ROOT': '/stale',
                     'CLAUDE_ENV_FILE': str(env_file), 'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']})
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {}
    assert 'TASKPLANE_CLAUDE_SESSION_ID' in env_file.read_text()


def test_claude_cli_start_does_not_install_codex_launcher(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'taskplane/tp.py'), 'flow', 'start',
                             '--workspace', str(tmp_path), '--goal', 'Claude delivery'],
        capture_output=True, text=True, env={**os.environ, 'TASKPLANE_CLAUDE_SESSION_ID': 'root'})
    assert result.returncode == 2
    assert json.loads(result.stdout)['status'] == 'blocked'
    assert json.loads(result.stdout)['reason'] == 'invalid_evidence'  # An exact scope is required.
    assert not (tmp_path / '.taskplane/codex-hook.py').exists()


@pytest.mark.parametrize('event_name', ['PreToolUse', 'PostToolUse'])
def test_unactivated_claude_child_does_not_require_taskplane_store(tmp_path, monkeypatch, event_name):
    from taskplane import workflow_local
    monkeypatch.setenv('CLAUDE_SESSION_ID', 'parent')
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    monkeypatch.delenv('TASKPLANE_WORKSPACE', raising=False)
    event = dict(hook_event_name=event_name, session_id='parent', agent_id='child',
                 cwd=str(tmp_path), tool_name='Bash', tool_use_id='ordinary',
                 tool_input={'command':'pwd'})
    assert flow.hook(event) == {}
    assert not list((tmp_path / '.taskplane').glob('workflow-*'))
    workflow_local.Harness(tmp_path, 'parent').update(selected=True, entry='tp-go')
    from taskplane.workflow import Refusal
    with pytest.raises(Refusal, match='not initialized'):
        flow.hook(event)
