"""Actual shipped CLI defaults; explicit full output remains opt-in."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from taskplane import flow
from taskplane.tests.test_native_workflow_cli import create
from taskplane.context import Store

ROOT = Path(__file__).resolve().parents[2]

def command(workspace, *args, code=0):
    p = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), 'flow', *args,
                        '--workspace', str(workspace)], capture_output=True, text=True,
                       env={**os.environ, 'CODEX_THREAD_ID': 'context-cli-root'})
    assert p.returncode == code, p.stdout+p.stderr
    return json.loads(p.stdout), len(p.stdout.encode())


def test_default_commands_context_and_full_details(tmp_path):
    create(tmp_path)
    start, size = command(tmp_path, 'start', '--scope', '.taskplane/scope.json',
                          '--request-reference', 'fixture/user-request')
    assert size <= 16384 and start['schema'] == 'taskplane.command-summary/v1'
    assert start['context']['status'] == 'ready'
    full, _ = command(tmp_path, 'report', '--full')
    assert full['workflow']['context_contract'] == 'bounded/v2'
    report, size = command(tmp_path, 'report')
    assert Store(tmp_path).resolve(report['details'])['workflow']['run'] == start['run']
    detail, size = command(tmp_path, 'context', '--read', report['details']['sha256'])
    assert detail['page']['sha256'] == report['details']['sha256'] and size <= 16384
    selected, _ = command(tmp_path, 'context', '--run', start['run'])
    consumed, size = command(tmp_path, 'context', '--consume', selected['handoff_ref']['sha256'])
    assert consumed['context_receipt'] and size <= 16384
    for args in [('advance', '--phase', 'design', '--expected-revision', '0'),
                 ('context', '--read', '0'*64), ('context', '--run', 'foreign')]:
        failed, size = command(tmp_path, *args, code=2)
        assert size <= 16384 and failed['errors']['blocking']
    for args in [('activate', '--phase', 'tp-go', '--request-reference', 'fixture/resume'),
                 ('wait', '--note', 'Fixture human input is needed.')]:
        result, size = command(tmp_path, *args)
        assert size <= 16384 and result['schema'] == 'taskplane.command-summary/v1'


def test_derived_storage_failure_preserves_committed_status(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs): raise OSError('fixture storage failure')
    monkeypatch.setattr(Store, 'put', fail)
    payload = {'status': 'approved', 'run': 'one', 'revision': 4}
    flow.emit(payload, tmp_path, 'decide')
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'approved' and result['binding']['revision'] == 4
    assert result['details'] is None and 'Do not replay' in result['next_action']


def test_cli_initial_tasks_and_batch_selection(tmp_path):
    create(tmp_path)
    tasks = {'tasks':[{'id':'OWN','phase':'product','owner':'root','dependencies':[],
                       'paths':['product.json'],'criteria':['AC1'],'verification':'Read required bodies'}]}
    (tmp_path/'initial.json').write_text(json.dumps(tasks))
    result, _ = command(tmp_path, 'start', '--scope', '.taskplane/scope.json', '--tasks', 'initial.json', '--request-reference', 'fixture/initial')
    prepared, _ = command(tmp_path, 'context', '--task', 'OWN')
    batch, size = command(tmp_path, 'context', '--task', 'OWN', '--read-required', prepared['handoff_ref']['sha256'])
    assert size < 16384 and batch['schema'] == 'taskplane.context-read-batch/v1'
    assert batch['pages'] and batch['remaining_required'] == 0
    full, _ = command(tmp_path, 'report', '--full')
    assert full['workflow']['decisions'] == {}
    assert full['workflow']['initial_context_tasks'][0]['id'] == 'OWN'
    failed, _ = command(tmp_path, 'context', '--read-required', '0'*64, code=2)
    assert failed['errors']['blocking']


@pytest.mark.parametrize('args', [
    ['context','--read-required','0'*64,'--page','0'],
    ['context','--read-required','0'*64,'--section',''],
    ['report','--read-required',''],
    ['context','--read-required','0'*64,'--consume','0'*64],
])
def test_batch_arguments_never_silently_ignore_selectors(tmp_path, args):
    result = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), 'flow', *args,
                             '--workspace',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode == 2 and 'error:' in result.stderr
    assert not (tmp_path/'.taskplane').exists()


def test_cli_drain_is_bounded_and_terminal(tmp_path):
    create(tmp_path)
    start, _ = command(tmp_path, 'start', '--scope', '.taskplane/scope.json', '--request-reference', 'fixture/drain')
    key = start['context']['handoff_ref']['sha256']
    result, size = command(tmp_path, 'context', '--drain', key)
    assert size <= 32768 and result['done'] and result['next_action'] is None
    assert result['pages'] and result['remaining_required'] == 0
    final, size = command(tmp_path, 'context', '--drain', key)
    assert final['pages'] == [] and final['done'] and size <= 32768


def test_emitted_context_actions_execute_verbatim_outside_workspace(tmp_path, monkeypatch):
    import shlex
    from taskplane.tests.test_worker_runtime import setup, reserve, launch
    from taskplane.tests.test_native_worker_dispatch import metadata
    from taskplane import workflow_host as h
    workspace = tmp_path/'workspace with spaces'; workspace.mkdir()
    home = tmp_path/'codex'; logs = home/'sessions'; logs.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    c, s = setup(workspace, count=1)
    rows = json.loads((workspace/'tasks.json').read_text())
    rows['tasks'][0]['verification'] = 'Read every required section. ' * 1500
    (workspace/'.taskplane/large-tasks.json').write_text(json.dumps(rows))
    s = c.update_tasks(s['run'], s['revision'], '.taskplane/large-tasks.json')
    item = reserve(c, s); launch(c, s, item)
    metadata(logs/'native-0.jsonl', s, item['grant'], 'native-0')
    worker = h.Controller(workspace, 'root', h.installed_adapter('codex'), principal='native-0')
    claim = worker.worker(s['run'], 'claim', grant=item['grant']['grant_id'])
    action = claim['context']['next_action']
    continuations = []
    for _ in range(20):
        continuations.append(action)
        response = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), *shlex.split(action)],
            cwd=tmp_path, env={**os.environ, 'CODEX_THREAD_ID':'native-0'}, capture_output=True, text=True)
        assert response.returncode == 0, response.stdout + response.stderr
        delivered = json.loads(response.stdout)
        if delivered['remaining_required'] == 0: break
        action = delivered['next_action']
    else: pytest.fail('Bound continuations never delivered all required context')
    assert sum('--read-required' in command for command in continuations) >= 2
    assert c.report()['workers'][item['grant']['grant_id']]['state'] == 'running'
    for task in ('T0', None):
        selected = c.context(s['run'], task=task)
        args = shlex.split(selected['next_action'])
        assert args[args.index('--workspace')+1] == str(workspace)
        assert args[args.index('--run')+1] == s['run']
        assert ('--task' in args) == bool(task)
        action = selected['next_action']
        for _ in range(20):
            delivered = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), *shlex.split(action)],
                cwd=tmp_path, env={**os.environ, 'CODEX_THREAD_ID':'root'}, capture_output=True, text=True)
            assert delivered.returncode == 0, delivered.stdout + delivered.stderr
            body = json.loads(delivered.stdout)
            if not body['remaining_required']: break
            action = body['next_action']
        else: pytest.fail('Root continuation did not finish required inputs')

    bad = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), 'flow', 'context',
        '--workspace', str(workspace), '--run', s['run'], '--task', 'DEP'],
        cwd=tmp_path, env={**os.environ, 'CODEX_THREAD_ID':'native-0'}, capture_output=True, text=True)
    assert bad.returncode == 2
    refused = json.loads(bad.stdout)
    assert refused['context']['status'] == 'blocked'
    assert refused['context']['next_action'] == refused['next_action']
    recovery = shlex.split(refused['next_action'])
    assert recovery[recovery.index('--task')+1] == 'T0'
    fresh = subprocess.run([sys.executable, str(ROOT/'taskplane/tp.py'), *recovery], cwd=tmp_path,
        env={**os.environ, 'CODEX_THREAD_ID':'native-0'}, capture_output=True, text=True)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
