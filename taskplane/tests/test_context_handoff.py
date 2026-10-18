"""Fresh-process durable brief, relevant freshness and unavailable inputs."""
from copy import deepcopy
import json
import subprocess
import sys
import pytest
from taskplane.context_handoff import Brief
from taskplane import workflow as w
from taskplane.tests.test_workflow_evidence import prepare


def test_brief_resumes_without_receipt_or_progress_ledger(tmp_path):
    state, _, _ = prepare(tmp_path)
    state['goal'] = 'Preserve human phase decisions.'
    before = deepcopy(state)
    brief = Brief(tmp_path, state)
    value = brief.descriptor()
    assert value['schema'] == 'taskplane.brief/v1'
    assert value['brief']['goal'] == state['goal']
    assert value['brief']['context_is_authority'] is False
    assert state == before
    reference = value['reference']
    code = "from pathlib import Path;from taskplane.context import Store;import json;print(json.dumps(Store(Path(" + repr(str(tmp_path)) + ")).resolve(" + repr(reference) + ")))"
    result = subprocess.run([sys.executable, '-c', code], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == value['brief']
    assert not (tmp_path/'.taskplane/context-v1/progress').exists()
    assert not (tmp_path/'.taskplane/context-v1/delivery').exists()


def test_unavailable_input_is_visible_without_inventing_evidence(tmp_path):
    state, _, _ = prepare(tmp_path)
    state['scope']['verification_inputs'].append('future.py')
    value = Brief(tmp_path, state).descriptor()
    assert value['unknown_inputs'] == [{'path':'future.py', 'status':'unavailable', 'reason':'Input has not been produced.'}]


def test_unrelated_files_and_progress_do_not_change_task_inputs(tmp_path):
    state, _, _ = prepare(tmp_path)
    row = {'id':'T','phase':'product','paths':['product.json'],'criteria':['AC1'],
           'read_inputs':['app.py'],'dependencies':[], 'owner':'root','verification':'Review source'}
    state['initial_context_tasks'] = [row]
    old = Brief(tmp_path,state,'T')
    (tmp_path/'unrelated.md').write_text('External work')
    state['revision'] += 7
    assert Brief(tmp_path,state,'T',snapshot=old.frozen).body['inputs'] == old.body['inputs']
    (tmp_path/'app.py').write_text('changed = True')
    with pytest.raises(w.Refusal,match='Relevant'):
        Brief(tmp_path,state,'T',snapshot=old.frozen)


def test_task_selection_and_paths_do_not_expand_authority(tmp_path):
    state, _, _ = prepare(tmp_path)
    with pytest.raises(w.Refusal): Brief(tmp_path,state,'unassigned')
    state['scope']['verification_inputs'] = ['../outside']
    with pytest.raises(w.Refusal): Brief(tmp_path,state)
