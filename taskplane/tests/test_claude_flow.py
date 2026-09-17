"""Claude and Codex share one flow without sharing transcript formats."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from taskplane import claude_flow_usage as claude, flow

ROOT = Path(__file__).resolve().parents[2]


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
