"""Native-layout fixtures for explicit same-session resume; no host certification."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from taskplane import flow, workflow as w, workflow_continuation as continuation, workflow_host as host
from taskplane import claude_worker_observations as native
from taskplane.tests.test_workflow_local import setup, submit, decide

pytestmark = pytest.mark.skipif(not native.supported_reader(), reason='Native no-follow reader unavailable')


def fixture(tmp_path, monkeypatch, *, accepted=False):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    home = tmp_path / 'home'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(home / '.claude'))
    monkeypatch.delenv('TASKPLANE_CLAUDE_TRANSCRIPT', raising=False)
    monkeypatch.setattr(flow, 'observed_parent', lambda *args: None)
    c, state = setup(workspace)
    c.adapter.name = 'claude'
    transcripts = {}
    for session in ('root', 'new-session'):
        path = home / '.claude/projects/fixture' / (session + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'sessionId': session, 'isSidechain': False, 'cwd': str(workspace)}) + '\n')
        transcripts[session] = path
    flow.hook({'hook_event_name': 'SessionStart', 'session_id': 'root',
               'transcript_path': str(transcripts['root']), 'cwd': str(workspace)}, governor=c)
    state = c.report()
    if accepted:
        state = decide(c, submit(c, state))
    request = {'schema': continuation.SCHEMA, 'binding': continuation.binding(state),
               'excerpt': f"continue run {state['run']} at the Product phase",
               'recorder': 'root_orchestrator',
               'source': {'kind': 'conversation', 'actor': 'user', 'automatic': False,
                          'conversation': 'new-session', 'reference': 'user/actual-resume-request',
                          'observed_at': datetime.now(timezone.utc).isoformat()}}
    newcomer = host.Controller(workspace, 'root', host.installed_adapter('claude'), principal='new-session')
    return c, newcomer, state, request, transcripts


def execute(c, state, request, mode='inspect', **kwargs):
    return continuation.resume(c, actor=c.principal, run=state['run'], revision=state['revision'],
                               mode=mode, request=request, **kwargs)


def test_native_resume_retains_owner_run_approval_and_exact_request(tmp_path, monkeypatch):
    c, newcomer, state, request, _ = fixture(tmp_path, monkeypatch, accepted=True)
    before = c._path().read_bytes()
    instruction = execute(newcomer, state, request)
    assert instruction['status'] == 'resume_required'
    assert instruction['native_resume'] == {'cwd': str(c.workspace), 'argv': ['claude', '--resume', 'root']}
    assert instruction['request'] == request
    assert instruction['approvals_changed'] is instruction['ownership_changed'] is instruction['grants_changed'] is False
    with pytest.raises(w.Refusal, match='original root'):
        execute(newcomer, state, request, 'verify')
    verified = execute(c, state, request, 'verify')
    assert verified['status'] == 'resumed' and verified['binding'] == continuation.binding(state)
    assert c._path().read_bytes() == before
    assert c.report()['decisions'] == state['decisions']
    assert c.report()['run'] == state['run'] and c.report()['root'] == 'root'


@pytest.mark.parametrize('bad,reason', [
    ('revision', 'resume_binding'), ('boolean_revision', 'resume_binding'),
    ('scope', 'resume_binding'), ('root', 'resume_binding'), ('workspace', 'resume_binding'),
    ('run', 'resume_binding'), ('visit', 'resume_binding'),
    ('assistant', 'resume_provenance'), ('automatic', 'resume_provenance'),
    ('future', 'resume_provenance'), ('old', 'resume_provenance'),
    ('conditional', 'resume_intent'), ('other_run', 'resume_intent'), ('phase', 'resume_intent'),
    ('quoted', 'resume_intent'), ('generic', 'resume_intent'),
    ('child', 'scope_violation'), ('source_conversation', 'resume_identity'),
    ('native_child', 'resume_identity'), ('native_workspace', 'resume_identity'),
    ('native_foreign', 'resume_identity'), ('symlink', 'resume_identity'),
    ('replaced_source', 'resume_identity'), ('missing_selection', 'resume_identity'),
    ('live_handle', 'resume_live_work'), ('live_worker', 'resume_live_work'),
    ('pending_call', 'resume_live_work'), ('source_drift', 'resume_binding'),
    ('unsupported_host', 'unsupported_authority'),
])
def test_resume_refuses_without_changing_store(tmp_path, monkeypatch, bad, reason):
    c, newcomer, state, request, transcripts = fixture(tmp_path, monkeypatch, accepted=True)
    kwargs = {}
    if bad in {'revision', 'scope', 'root', 'workspace', 'run', 'visit', 'boolean_revision'}:
        key = 'scope_digest' if bad == 'scope' else 'revision' if bad == 'boolean_revision' else bad
        request['binding'][key] = True if bad == 'boolean_revision' else 'foreign'
    elif bad == 'assistant': request['source']['actor'] = 'assistant'
    elif bad == 'automatic': request['source']['automatic'] = True
    elif bad == 'future': request['source']['observed_at'] = '2999-01-01T00:00:00Z'
    elif bad == 'old': request['source']['observed_at'] = '2000-01-01T00:00:00Z'
    elif bad == 'conditional': request['excerpt'] += ' if tests pass'
    elif bad == 'other_run': request['excerpt'] = 'continue run ' + '0' * 32
    elif bad == 'phase': request['excerpt'] = f"continue run {state['run']} at Build"
    elif bad == 'quoted': request['excerpt'] = '"' + request['excerpt'] + '"'
    elif bad == 'generic': request['excerpt'] = 'continue'
    elif bad == 'child': kwargs['parent'] = 'other-root'
    elif bad == 'source_conversation': request['source']['conversation'] = 'other-session'
    elif bad in {'native_child', 'native_workspace', 'native_foreign'}:
        row = json.loads(transcripts['new-session'].read_text())
        row[{'native_child': 'isSidechain', 'native_workspace': 'cwd', 'native_foreign': 'sessionId'}[bad]] = True if bad == 'native_child' else 'foreign'
        transcripts['new-session'].write_text(json.dumps(row) + '\n')
    elif bad == 'symlink':
        path = transcripts['new-session']; backup = path.with_suffix('.backup')
        path.rename(backup); path.symlink_to(backup)
    elif bad == 'replaced_source':
        path = transcripts['root']; raw = path.read_bytes()
        replacement = path.with_suffix('.replacement'); replacement.write_bytes(raw); replacement.replace(path)
    elif bad == 'source_drift': (c.workspace / 'app.py').write_text('Changed sealed source')
    elif bad == 'unsupported_host': newcomer.adapter.name = 'codex'
    else:
        db = json.loads(c._path().read_text()); stored = db['runs'][state['run']]
        if bad == 'missing_selection': stored.pop('claude_transcript_source')
        elif bad == 'live_handle':
            stored['observed_handles']['live'] = {'state': 'running', 'visit': w.current(state)['id'], 'revision': state['revision']}
        elif bad == 'live_worker':
            stored['workers'] = {'grant': {'grant_id': 'grant', 'root': 'root', 'run': state['run'],
                                           'attempt': 1, 'paths': [], 'state': 'running'}}
        elif bad == 'pending_call': db['admissions'] = {'pending': {'run': state['run'], 'state': 'admitted'}}
        c._path().write_text(json.dumps(db))
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal) as refused:
        execute(newcomer, state, request, **kwargs)
    assert refused.value.reason == reason
    assert c._path().read_bytes() == before


def test_resume_cli_inspection_and_verification_are_read_only(tmp_path, monkeypatch, capsys):
    c, newcomer, state, request, _ = fixture(tmp_path, monkeypatch)
    args = ['resume', '--workspace', str(c.workspace), '--run', state['run'],
            '--expected-revision', str(state['revision']), '--resume-json', json.dumps(request)]
    before = c._path().read_bytes()
    assert flow.main([*args, '--resume-mode', 'inspect'], compact=True, governor=newcomer) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'resume_required'
    assert flow.main([*args, '--resume-mode', 'verify'], compact=True, governor=c) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'resumed'
    assert c._path().read_bytes() == before


def test_foreign_report_explains_supported_next_step(tmp_path, monkeypatch, capsys):
    c, newcomer, state, _, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(flow, 'session_id', lambda event: 'new-session')
    before = c._path().read_bytes()
    assert flow.main(['report', '--workspace', str(c.workspace), '--run', state['run']],
                     compact=True, governor=newcomer) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'resume_required' and 'flow resume' in result['next_action']
    assert result['binding'] == continuation.binding(state)
    assert c._path().read_bytes() == before


def test_explicit_foreign_report_does_not_select_requesters_own_run(tmp_path, monkeypatch, capsys):
    c, _, state, _, _ = fixture(tmp_path, monkeypatch)
    own = host.Controller(c.workspace, 'new-session', host.installed_adapter('claude'))
    own_state = own.start({'scope': state['scope'], 'request_reference': 'user/other-task'})
    for item in (state, own_state):
        flow.append(c.workspace, {'kind': 'start', 'run': item['run'], 'session': item['root']})
    monkeypatch.setattr(flow, 'session_id', lambda event: 'new-session')
    monkeypatch.setattr(flow, 'claude_session', lambda event: True)
    before = c._path().read_bytes(), own._path().read_bytes()
    assert flow.main(['report', '--workspace', str(c.workspace), '--run', state['run']], compact=True) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['binding']['run'] == state['run'] and result['binding']['root'] == 'root'
    assert (c._path().read_bytes(), own._path().read_bytes()) == before


def test_resume_command_is_admitted_at_sealed_checkpoint(tmp_path, monkeypatch):
    import shlex
    import sys
    c, _, state, request, transcripts = fixture(tmp_path, monkeypatch, accepted=True)
    argv = [sys.executable, str(Path(flow.__file__).with_name('tp.py')), 'flow', 'resume',
            '--workspace', str(c.workspace), '--run', state['run'], '--expected-revision', str(state['revision']),
            '--resume-mode', 'verify', '--resume-json', json.dumps(request)]
    result = flow.hook({'hook_event_name': 'PreToolUse', 'session_id': 'root', 'cwd': str(c.workspace),
                       'transcript_path': str(transcripts['root']), 'tool_use_id': 'resume-check',
                       'tool_name': 'Bash', 'tool_input': {'command': shlex.join(argv)}}, governor=c)
    assert result.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
