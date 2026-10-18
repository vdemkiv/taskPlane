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


def test_derived_storage_failure_preserves_committed_status(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs): raise OSError('fixture storage failure')
    monkeypatch.setattr(Store, 'put', fail)
    payload = {'status': 'approved', 'run': 'one', 'revision': 4}
    flow.emit(payload, tmp_path, 'decide')
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'approved' and result['binding']['revision'] == 4
    assert result['details'] is None and 'Do not replay' in result['next_action']



def test_cli_brief_is_receipt_free_and_available_in_a_fresh_process(tmp_path):
    create(tmp_path)
    start,_=command(tmp_path,'start','--scope','.taskplane/scope.json','--request-reference','fixture/request')
    assert start['workflow']['context_contract']=='brief/v1'
    value,_=command(tmp_path,'context','--run',start['run'])
    assert value['schema']=='taskplane.brief/v1'
    assert value['brief']['context_is_authority'] is False
    assert value['brief']['inputs']
    assert Store(tmp_path).resolve(value['reference'])==value['brief']
    assert not (tmp_path/'.taskplane/context-v1/progress').exists()


@pytest.mark.parametrize('option',['--consume','--read-required','--drain','--startup-request'])
def test_retired_context_protocol_is_not_a_cli_option(tmp_path,option):
    result=subprocess.run([sys.executable,str(ROOT/'taskplane/tp.py'),'flow','context',option,'obsolete',
        '--workspace',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==2 and 'unrecognized arguments' in result.stderr
    assert not (tmp_path/'.taskplane').exists()
