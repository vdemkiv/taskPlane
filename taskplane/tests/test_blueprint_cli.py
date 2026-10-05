"""Workflow data CLI fixtures; these do not demonstrate native host execution."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import tp
from taskplane.tests.test_blueprint import definition


@pytest.fixture
def source(tmp_path):
    (tmp_path / 'main.py').write_text('answer = 42\n')
    (tmp_path / 'definition.json').write_text(json.dumps(definition()))
    (tmp_path / 'inputs.json').write_text(json.dumps({
        'source_files': ['main.py'], 'output_prefix': 'reports/first'}))
    return tmp_path


def invoke(source, capsys, action, *options, code=0):
    actual = tp.main(['workflow', action, '--workspace', str(source), *options])
    output = capsys.readouterr()
    assert actual == code, output
    return json.loads(output.out)


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_readonly_commands_precede_all_persistence(source, monkeypatch, capsys):
    before = files(source)
    from taskplane import context_handoff, workflow_host
    def fail_context(self, workspace, state, task=None, *, consumer=None, persist=True, snapshot=None):
        pytest.fail('persistent context')
    monkeypatch.setattr(context_handoff.Session, '__init__', fail_context)
    monkeypatch.setattr(workflow_host.Controller, 'start', lambda *a, **k: pytest.fail('run started'))
    assert invoke(source, capsys, 'validate', '--definition', 'definition.json')['status'] == 'valid'
    incomplete = invoke(source, capsys, 'preview', '--definition', 'definition.json')
    assert incomplete['status'] == 'incomplete' and not incomplete['runnable']
    ready = invoke(source, capsys, 'preview', '--definition', 'definition.json', '--inputs', 'inputs.json')
    assert ready['status'] == 'ready' and ready['effects']['workers_dispatched'] == 0
    assert invoke(source, capsys, 'catalog')['compatible']
    assert files(source) == before


def test_compile_save_are_data_only_and_no_clobber(source, capsys):
    saved = invoke(source, capsys, 'save', '--definition', 'definition.json',
                   '--out', 'workflows/change-risk-review.0.1.0.workflow.json')
    assert saved['status'] == 'created'
    package = invoke(source, capsys, 'compile', '--definition', saved['path'], '--inputs', 'inputs.json',
                     '--out', '.taskplane/bootstrap/workflow-first')
    assert package['status'] == 'created'
    assert package['start_arguments'][:2] == ['flow', 'start']
    assert sorted(p.name for p in (source / '.taskplane').iterdir()) == ['bootstrap']
    before = files(source)
    assert invoke(source, capsys, 'compile', '--definition', saved['path'], '--inputs', 'inputs.json',
                  '--out', '.taskplane/bootstrap/workflow-first')['status'] == 'unchanged'
    assert files(source) == before
    changed = definition(); changed['description'] += ' New content.'
    (source / 'definition.json').write_text(json.dumps(changed))
    error = invoke(source, capsys, 'save', '--definition', 'definition.json',
                   '--out', saved['path'], code=2)
    assert error['diagnostics'][0]['code'] == 'published_version_conflict'


@pytest.mark.parametrize('options', [
    ['--workspace', 'another'], ['--work', '.'], ['--definition', 'a', '--definition', 'b'],
    ['--definition=x', '--definition=y'], ['--definition', 'definition.json', '--out', 'side-effect'],
    ['--definition', 'definition.json', '--execute', 'yes'],
])
def test_closed_cli_grammar(source, capsys, options):
    before = files(source)
    error = invoke(source, capsys, 'validate', *options, code=2)
    assert error['diagnostics'][0]['code'] == 'invalid_arguments'
    assert set(error['diagnostics'][0]) == {'code', 'location', 'message', 'remedy'}
    assert files(source) == before


@pytest.mark.parametrize('raw', ['{"source_files":[],"source_files":[]}', '{"x":NaN}', '{"x":1e999}', '[]'])
def test_strict_inputs(source, capsys, raw):
    (source / 'inputs.json').write_text(raw)
    invoke(source, capsys, 'preview', '--definition', 'definition.json', '--inputs', 'inputs.json', code=2)
    assert not (source / '.taskplane').exists()


def test_explicit_workspace_and_subprocess_journey(source):
    executable = str(Path(tp.__file__).resolve())
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CODEX_', 'CLAUDE_', 'TASKPLANE_'))}
    for arguments, code in [(['workflow', 'catalog'], 2),
                            (['workflow', 'validate', '--workspace=' + str(source), '--definition=definition.json'], 0)]:
        result = subprocess.run([sys.executable, executable, *arguments], cwd=source, env=env,
                                capture_output=True, text=True)
        assert result.returncode == code, result.stderr
        assert json.loads(result.stdout)['status'] == ('valid' if code == 0 else 'invalid')
    assert not (source / '.taskplane').exists()


def test_check_missing_run_does_not_create_a_store(source, capsys):
    before = files(source)
    result = invoke(source, capsys, 'check', '--run', 'absent', code=2)
    assert result['diagnostics'][0]['code'] == 'state_unavailable'
    assert files(source) == before and not (source / '.taskplane').exists()
