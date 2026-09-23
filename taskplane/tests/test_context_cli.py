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
    assert full['workflow']['context_contract'] == 'bounded/v1'
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
