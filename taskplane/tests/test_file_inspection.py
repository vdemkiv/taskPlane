"""Real file reads and adversarial admission checks for Codex review access."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest

import file_inspection as files
import taskplane_lite as kernel
import tp as cli


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for key in list(os.environ):
        if (key.startswith(('LD_', 'DYLD_', 'BASH_FUNC_', 'TASKPLANE_')) and key not in {'TASKPLANE_HOME', 'TASKPLANE_HOST_HOME'}) or key in {
                'ENV', 'BASH_ENV', 'SHELLOPTS', 'BASHOPTS', 'CODEX_THREAD_ID', 'CLAUDE_SESSION_ID'}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'source.py').write_text('first\nimport os; os.remove("source.py")\nlast\n')
    return str(tmp_path)


def request(workspace, **changes):
    return files.tool_input(kernel._TP_CLI_PATH, workspace,
        {'operation': 'read', 'path': 'source.py', **changes})


def test_codex_tools_activate_readonly_contract_and_use_same_write_wall(workspace, monkeypatch):
    monkeypatch.setenv('CODEX_THREAD_ID', 'codex-file-review')
    kernel.record_entry_tools(['exec_command', 'apply_patch'])
    contract = kernel.build_contract('review', read_only=True, write_allow=['artifacts/**'])
    assert kernel.review_file_tool_readiness(contract)['read_transport'] == 'engine_inspection'
    kernel.activate(workspace, contract, snapshot=None)
    assert kernel.screen_tool(contract, 'exec_command', request(workspace), workspace)[0]
    assert not kernel.screen_tool(contract, 'exec_command', {'cmd': 'cat source.py'}, workspace)[0]
    assert not kernel.screen_tool(contract, 'apply_patch',
        {'command': '*** Begin Patch\n*** Delete File: source.py\n*** End Patch'}, workspace)[0]
    assert kernel.screen_tool(contract, 'apply_patch',
        {'command': '*** Begin Patch\n*** Add File: artifacts/result.json\n+{}\n*** End Patch'}, workspace)[0]


def test_real_isolated_cli_reads_lists_and_searches_without_executing_source(workspace):
    before = Path(workspace, 'source.py').read_bytes()
    for operation, extra, expected in (
        ('read', {'start': 2, 'limit': 1}, {'line': 2, 'text': 'import os; os.remove("source.py")'}),
        ('search', {'pattern': 'last'}, {'line': 3, 'text': 'last'}),
        ('list', {'path': '.'}, {'name': 'source.py', 'directory': False, 'symlink': False}),
    ):
        arguments = request(workspace, operation=operation, **extra)
        assert kernel.screen_tool(kernel.build_contract('review', read_only=True),
                                  'exec_command', arguments, workspace)[0]
        completed = subprocess.run(['/bin/sh', '-c', arguments['cmd']], cwd=workspace,
                                   capture_output=True, text=True, timeout=15)
        assert completed.returncode == 0, completed.stderr + completed.stdout
        result = json.loads(completed.stdout)
        assert expected in result.get('lines', result.get('entries', []))
    assert Path(workspace, 'source.py').read_bytes() == before
    assert not list(Path(workspace).rglob('__pycache__'))


def test_inspection_never_runs_path_git_or_python_startup_code(workspace, monkeypatch):
    marker = Path(workspace, 'executed')
    malicious = Path(workspace, 'git')
    malicious.write_text('#!/bin/sh\ntouch ' + str(marker) + '\n')
    malicious.chmod(0o755)
    Path(workspace, 'sitecustomize.py').write_text('open(' + repr(str(marker)) + ', "w").close()')
    monkeypatch.setenv('PATH', workspace)
    monkeypatch.setenv('PYTHONPATH', workspace)
    arguments = request(workspace)
    assert kernel.screen_tool(kernel.build_contract('review', read_only=True),
                              'exec_command', arguments, workspace)[0]
    completed = subprocess.run(['/bin/sh', '-c', arguments['cmd']], cwd=workspace,
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)['lines'][0]['text'] == 'first'
    assert not marker.exists()


@pytest.mark.parametrize('alter', [
    lambda a: {**a, 'cmd': a['cmd'] + '; touch source.py'},
    lambda a: {**a, 'cmd': a['cmd'] + ' > source.py'},
    lambda a: {**a, 'cmd': 'env ' + a['cmd']},
    lambda a: {**a, 'cmd': a['cmd'].replace(' -I ', ' ')},
    lambda a: {**a, 'cmd': a['cmd'].replace(' -S ', ' ')},
    lambda a: {**a, 'cmd': a['cmd'].replace(' -B ', ' ')},
    lambda a: {**a, 'cmd': a['cmd'].replace(' inspect ', ' clear ')},
    lambda a: {**a, 'cmd': a['cmd'].replace('exec ', '', 1)},
    lambda a: {**a, 'shell': '/bin/bash'},
    lambda a: {**a, 'login': True},
    lambda a: {**a, 'tty': True},
    lambda a: {**a, 'sandbox_permissions': 'require_escalated'},
    lambda a: {**a, 'environment': {'PATH': '/tmp'}},
    lambda a: {**a, 'workdir': '/'},
])
def test_noncanonical_invocations_stay_denied(workspace, alter):
    contract = kernel.build_contract('review', read_only=True)
    assert not kernel.screen_tool(contract, 'exec_command', alter(request(workspace)), workspace)[0]


@pytest.mark.parametrize('name', ['LD_PRELOAD', 'DYLD_INSERT_LIBRARIES', 'BASH_ENV', 'ENV', 'BASH_FUNC_exec%%'])
def test_hostile_environment_refuses_transport(workspace, monkeypatch, name):
    monkeypatch.setenv(name, 'untrusted')
    assert not files.launch_supported()
    assert not kernel.screen_tool(kernel.build_contract('review', read_only=True),
        'exec_command', request(workspace), workspace)[0]


def test_inspection_does_not_bypass_contract_tools_deny_rules_or_release_meter(workspace):
    arguments = request(workspace)
    contract = kernel.build_contract('no reads', read_only=True, tools=['Grep'])
    assert not kernel.screen_tool(contract, 'exec_command', arguments, workspace)[0]
    contract = kernel.build_contract('no inspection', read_only=True, deny_extra=['inspect'])
    assert not kernel.screen_tool(contract, 'exec_command', arguments, workspace)[0]
    assert not cli._is_release_command(arguments['cmd'])


def test_checkout_substitute_and_symlink_engine_are_refused(workspace):
    substitute = Path(workspace, 'tp.py')
    substitute.write_text('raise SystemExit("substitute")')
    arguments = files.tool_input(str(substitute), workspace, {'operation': 'read', 'path': 'source.py'})
    assert files.invocation('exec_command', arguments, str(substitute), workspace) is None
    link = Path(workspace, 'alias.py')
    link.symlink_to(kernel._TP_CLI_PATH)
    assert files.invocation('exec_command', request(workspace), str(link), workspace) is None


def test_output_is_bounded_and_special_files_are_refused(workspace):
    source = Path(workspace, 'large.txt')
    source.write_bytes(b'x' * (files.MAX_BYTES + 100))
    result = files.inspect(files.decode(files.encode({'operation': 'read', 'path': str(source)})), workspace)
    assert result['truncated'] and result['lines'] == []
    fifo = Path(workspace, 'fifo')
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match='regular files'):
        files.inspect(files.decode(files.encode({'operation': 'read', 'path': str(fifo)})), workspace)


def test_unknown_requests_are_not_command_or_write_authority():
    for value in ({'operation': 'write', 'path': 'source.py'},
                  {'operation': 'read', 'path': 'source.py', 'command': 'rm source.py'},
                  {'operation': 'read', 'path': 'source.py', 'limit': True}):
        with pytest.raises(ValueError):
            files.encode(value)


def test_plain_onboarding_retains_current_file_readiness_and_returns_failure(workspace, monkeypatch, capsys):
    monkeypatch.setenv('CODEX_THREAD_ID', 'codex-file-review')
    kernel.record_entry_tools(['apply_patch'])
    args = argparse.Namespace(workspace=workspace, json=True, out=None)
    with mock.patch.object(cli, '_onboard_report', return_value={'ready': True, 'checks': []}):
        assert cli.cmd_onboard(args) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['workspace_ready'] and not report['ready'] and not report['review_ready']
    assert report['next_action'] == 'review_file_tools_unavailable'
