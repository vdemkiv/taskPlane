"""Root phase authority stays usable when callback coverage is unavailable."""
import json
import shlex
import sys
from pathlib import Path

import pytest

from taskplane import flow, workflow as w
from taskplane.tests.test_workflow_local import setup, submit, decide, decision


def remove_readiness(c):
    db = json.loads(c._path().read_text())
    db["runs"][db["active"]].pop("parent_hook_readiness", None)
    c._path().write_text(json.dumps(db))
    return c.report(diagnostics=False)


def test_first_submission_preserves_missing_callback_coverage(tmp_path):
    from taskplane.tests.test_workflow_evidence import bound_output
    c, _ = setup(tmp_path)
    state = remove_readiness(c)
    target = bound_output(c, state)
    result = c.apply("submit", state["run"], expected_revision=state["revision"],
                     output=target, tasks="tasks.json")
    assert result['visits'][0]['decision'] == 'awaiting_human_approval'
    assert not result.get('parent_hook_readiness') and not result['decisions']
    assert local_readiness(c, result)['parent_hook']['status'] == 'missing'


def local_readiness(c, state):
    from taskplane.workflow_local import Harness
    return Harness(c.workspace, c.root).readiness(state)


def test_positive_first_decision_retains_actual_approval_without_pair(tmp_path):
    c, state = setup(tmp_path)
    state = submit(c, state)
    state = remove_readiness(c)
    result = decide(c, state)
    assert result['visits'][0]['decision'] == 'approved'
    assert not result.get('parent_hook_readiness')
    assert local_readiness(c, result)['status'] == 'active'
    assert local_readiness(c, result)['parent_hook']['status'] == 'missing'


def test_unobserved_callbacks_do_not_approve_or_authorize_unowned_writes(tmp_path):
    c, state = setup(tmp_path)
    state = remove_readiness(c)
    state = submit(c, state)
    with pytest.raises(w.Refusal, match='(?i)(approval|approved|accept)'):
        c.apply('advance', state['run'], expected_revision=state['revision'], phase='design')
    with pytest.raises(w.Refusal):
        c.guard({'tool_name':'Write','tool_input':{'file_path':str(tmp_path/'unowned.txt')}}, state['run'])
    assert c.report()['visits'][0]['decision'] == 'awaiting_human_approval'


def test_missing_callbacks_do_not_hide_live_processes(tmp_path):
    c, state = setup(tmp_path)
    state = remove_readiness(c)
    db = json.loads(c._path().read_text())
    db['runs'][state['run']]['observed_handles']['live'] = {
        'state':'running','revision':state['revision'],'visit':state['visits'][0]['id']}
    c._path().write_text(json.dumps(db))
    from taskplane.tests.test_workflow_evidence import bound_output
    target = bound_output(c, state)
    with pytest.raises(w.Refusal, match='Live tool processes'):
        c.apply('submit', state['run'], expected_revision=state['revision'], output=target,tasks='tasks.json')


def test_root_can_reach_approved_build_scope_without_callback_observations(tmp_path):
    c, state = setup(tmp_path)
    for next_phase in ['design','plan','build']:
        state = remove_readiness(c)
        state = submit(c,state)
        state = decide(c,state,decision(state,event='approve-before-'+next_phase))
        state = c.apply('advance',state['run'],expected_revision=state['revision'],phase=next_phase)
    assert w.current(state)['phase'] == 'build'
    assert local_readiness(c,state)['parent_hook']['status'] == 'missing'
    assert all(v['decision'] == 'approved' for v in state['visits'][:state['index']])


def test_negative_decision_remains_available_without_pair(tmp_path):
    c, state = setup(tmp_path)
    state = submit(c, state)
    state = remove_readiness(c)
    result = decide(c, state, decision(state, text="Changes requested", candidate="changes_requested"))
    assert result["visits"][0]["decision"] == "changes_requested"


def test_existing_scanner_admitted_before_first_submission(tmp_path):
    c, state = setup(tmp_path)
    args = [sys.executable, str(Path(flow.__file__).with_name("tp.py")),
            "graph", "--workspace", str(tmp_path), "scan", "--decompose", "--strict"]
    event = {"tool_name": "exec_command", "cwd": str(tmp_path),
             "tool_input": {"cmd": shlex.join(args)}}
    c._guard(event, {**state, "workflow_available": True})


@pytest.mark.parametrize("defect", ["automatic", "run", "visit", "root", "workspace", "runtime", "initialization"])
def test_foreign_or_manual_pair_remains_missing_after_approval(tmp_path, defect):
    from taskplane.tests.test_worker_runtime import parent_pair
    c, state = setup(tmp_path)
    state = submit(c, state)
    db = json.loads(c._path().read_text())
    active = db["runs"][state["run"]]
    active["parent_hook_readiness"][defect] = False if defect == "automatic" else "foreign"
    c._path().write_text(json.dumps(db))
    before = c.report(diagnostics=False)
    assert local_readiness(c, before)['parent_hook']['status'] == 'missing'
    approved = decide(c, before)
    assert approved['visits'][0]['decision'] == 'approved'
    assert local_readiness(c, approved)['parent_hook']['status'] == 'missing'
    parent_pair(c)
    recovered = c.report(diagnostics=False)
    assert recovered["run"] == state["run"]
    assert recovered["visits"][0]["packet"] == state["visits"][0]["packet"]
    assert recovered["visits"][0]["decision"] == "approved"
    assert local_readiness(c, recovered)['parent_hook']['status'] == 'ready'
