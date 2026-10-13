from copy import deepcopy
import json

import pytest

from taskplane import workflow as w, workflow_collision
from taskplane.tests.test_workflow_local import setup, submit, decide


@pytest.mark.parametrize('host,tool', [('codex', 'write_file'), ('claude', 'Write')])
def test_other_active_root_cannot_overwrite_sealed_output(tmp_path, host, tool):
    owner, state = setup(tmp_path); state = decide(owner, submit(owner, state))
    db = json.loads(owner._path().read_text()); other = deepcopy(db)
    other['root'] = 'other-root'
    run = other['runs'][other['active']]; run['root'] = 'other-root'
    foreign = tmp_path / '.taskplane' / ('workflow-' + 'f' * 32 + '.json')
    foreign.write_text(json.dumps(other))
    current = deepcopy(state); current['visits'][current['index']]['decision'] = 'not_requested'
    current['workflow_available'] = True
    owner.adapter.name = host
    with pytest.raises(w.Refusal, match='Another active run has sealed'):
        owner._guard({'tool_name': tool, 'tool_input': {'file_path': 'product.json', 'content': 'overwrite'}}, current)
    assert json.loads((tmp_path / 'product.json').read_text())['run'] == state['run']
    assert workflow_collision.sealed_paths(tmp_path, current)['product.json']['root'] == 'other-root'
    run['finished'] = True; foreign.write_text(json.dumps(other))
    assert workflow_collision.sealed_paths(tmp_path, current) == {}
