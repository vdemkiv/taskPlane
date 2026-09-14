"""Startup compatibility is separate from host authority and source protection."""
import argparse
import io
import json
from pathlib import Path
import subprocess
from unittest import mock

import pytest

import storage
import taskplane_lite as kernel
import tp as cli

ROOT = Path(__file__).resolve().parents[2]
ENTRIES = ('taskplane', 'tp-go', 'tp-engineering', 'tp-build', 'tp-design',
           'tp-product', 'tp-northstar', 'tp-status', 'tp-help', 'tp-tag')


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv('CODEX_THREAD_ID', raising=False)
    monkeypatch.delenv('CLAUDE_SESSION_ID', raising=False)
    ws = tmp_path / 'project'
    ws.mkdir()
    subprocess.run(['git', 'init', '-q', str(ws)], check=True)
    (ws / 'README.md').write_text('source\n')
    subprocess.run(['git', '-C', str(ws), 'add', 'README.md'], check=True)
    subprocess.run(['git', '-C', str(ws), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.test', 'commit', '-qm', 'initial'], check=True)
    return str(ws)


def test_initialization_repairs_once_and_preserves_existing_context(project):
    with mock.patch.object(cli, '_install_codex_hooks', wraps=cli._install_codex_hooks) as install:
        first = cli._initialize_entry(project)
        context = Path(kernel.kb_root(project)) / 'context/product.md'
        context.write_text('User-authored context\n')
        graph = Path(kernel.kb_root(project)) / 'graph.json'
        before_graph = graph.read_bytes()
        second = cli._initialize_entry(project)
    assert 'initialize_project' in first['initialization']['repairs']
    assert second['initialization']['repairs'] == []
    assert context.read_text() == 'User-authored context\n'
    assert graph.read_bytes() == before_graph
    assert install.call_count <= 1
    assert kernel.load_active(project) is None
    assert subprocess.run(['git', '-C', project, 'diff', '--exit-code'], capture_output=True).returncode == 0


def test_invalid_run_binding_is_reported_without_setup(project):
    report = {'looks_like_project': True, 'is_git': True, 'has_commit': True,
              'has_context': False, 'run_readiness': {'ready': False}, 'ready': False}
    with mock.patch.object(cli, '_onboard_report', return_value=report), \
            mock.patch.object(cli, 'cmd_init') as initialize, \
            mock.patch.object(cli, '_install_codex_hooks') as install:
        result = cli._initialize_entry(project)
    assert not result['ready']
    initialize.assert_not_called()
    install.assert_not_called()


def test_activation_refuses_incompatible_tools_before_creating_contract(project, monkeypatch):
    monkeypatch.setenv('CODEX_THREAD_ID', 'session-a')
    kernel.record_entry_tools(['exec_command', 'apply_patch'])
    contract = kernel.build_contract('review', read_only=True, write_allow=['.em-review/**'])
    with pytest.raises(ValueError, match='missing Read'):
        kernel.activate(project, contract)
    assert kernel.load_active(project) is None
    assert kernel.review_file_tool_readiness()['ready'] is False


def test_compatible_inventory_does_not_weaken_contract(project, monkeypatch):
    monkeypatch.setenv('CODEX_THREAD_ID', 'session-a')
    kernel.record_entry_tools(['Read', 'Grep', 'Glob', 'Write', 'Bash'])
    contract = kernel.build_contract('review', read_only=True, write_allow=['.em-review/**'])
    kernel.activate(project, contract)
    assert kernel.screen_tool(contract, 'Read', {'file_path': 'README.md'}, project)[0]
    assert not kernel.screen_tool(contract, 'Bash', {'command': 'cat README.md'}, project)[0]
    assert not kernel.screen_tool(contract, 'Write', {'file_path': 'README.md'}, project)[0]
    denied = kernel.build_contract('no writer', read_only=True, write_allow=['.em-review/**'], tools=['Read'])
    with pytest.raises(ValueError, match='Write/Edit'):
        kernel.activate(project, denied)
    assert kernel.load_active(project)['task_id'] == contract['task_id']


def test_inventory_and_contracts_are_session_isolated(project, monkeypatch):
    monkeypatch.setenv('CODEX_THREAD_ID', 'session-a')
    kernel.record_entry_tools(['Read', 'Write'])
    kernel.activate(project, kernel.build_contract('A', read_only=True))
    with storage.hook_session({'session_id': 'session-b', 'turn_id': 'turn-b'}):
        assert not kernel.review_file_tool_readiness()['ready']
        assert kernel.load_active(project) is None
        kernel.record_entry_tools(['exec_command'])
    assert kernel.review_file_tool_readiness()['ready']
    assert kernel.load_active(project)['task'] == 'A'


def test_changed_engine_requires_current_inventory(project, monkeypatch):
    monkeypatch.setenv('CODEX_THREAD_ID', 'session-a')
    kernel.record_entry_tools(['Read', 'Write'])
    with mock.patch.object(kernel, '_entry_engine_fingerprint', return_value='updated'):
        assert not kernel.review_file_tool_readiness()['ready']


def test_omitted_inventory_on_reentry_clears_only_current_session(project, monkeypatch, capsys):
    monkeypatch.setenv('CODEX_THREAD_ID', 'session-a')
    kernel.record_entry_tools(['Read', 'Write'])
    args = argparse.Namespace(workspace=project, initialize=True, json=True, out=None)
    with mock.patch.object(cli, '_initialize_entry', return_value={'ready': True}):
        assert cli.cmd_onboard(args) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report['review_file_tools']['ready']
    assert report['workspace_ready'] is True
    assert report['ready'] is False
    assert report['review_ready'] is False
    assert report['next_action'] == 'review_file_tools_unavailable'


@pytest.mark.parametrize('tools, ready', [
    (['exec_command', 'apply_patch'], False),
    (['Read', 'Write'], True),
])
def test_plain_onboarding_reports_tool_compatibility_without_repeating_setup(
        project, monkeypatch, capsys, tools, ready):
    monkeypatch.setenv('CODEX_THREAD_ID', 'session-a')
    kernel.record_entry_tools(tools)
    args = argparse.Namespace(workspace=project, json=True, out=None)
    with mock.patch.object(cli, '_onboard_report', return_value={
            'ready': True, 'checks': [], 'next_action': 'ready'}), \
            mock.patch.object(cli, '_initialize_entry') as initialize:
        assert cli.cmd_onboard(args) == (0 if ready else 2)
    report = json.loads(capsys.readouterr().out)
    assert report['workspace_ready'] is True
    assert report['ready'] is ready
    assert report['review_ready'] is ready
    assert report['checks'][-1]['ok'] is ready
    assert report['next_action'] == ('ready' if ready else 'review_file_tools_unavailable')
    initialize.assert_not_called()
    assert kernel.load_active(project) is None


def test_existing_inspection_is_reachable_without_budget_telemetry(project, monkeypatch):
    monkeypatch.setenv('CODEX_THREAD_ID', 'session-a')
    kernel.record_entry_tools(['Read', 'Write'])
    kernel.activate(project, kernel.build_contract('review', read_only=True))
    event = {'cwd': project, 'tool_name': 'exec_command',
             'tool_input': {'cmd': f'python3 {ROOT}/taskplane/tp.py status'}}
    with mock.patch.object(cli.sys, 'stdin', io.StringIO(json.dumps(event))), \
            mock.patch.object(cli, '_bounded_transcript_projection', side_effect=AssertionError('inspection must be reachable')), \
            mock.patch('sys.stdout', new_callable=io.StringIO) as output:
        assert cli._screen(None) == 0
    assert 'block' not in output.getvalue()


def test_onboarding_carveout_does_not_admit_other_mutations():
    engine = str(ROOT / 'taskplane/tp.py')
    assert cli._is_release_command(f'python3 {engine} onboard --initialize --json --available-tools Read,Write')
    for suffix in ('--out source.py', '--apply-setup evil.json', '; touch source.py', '--install-codex-hooks'):
        assert not cli._is_release_command(f'python3 {engine} onboard {suffix}')
    assert not cli._is_release_command('python3 /tmp/tp.py onboard --initialize')


@pytest.mark.parametrize('name', ENTRIES)
def test_all_direct_entries_share_initialization(name):
    body = (ROOT / 'skills' / name / 'SKILL.md').read_text()
    assert 'entry-initialization.md' in body
    assert f'entry point `{name}`' in body
    assert not (ROOT / '.mcp.json').exists()
