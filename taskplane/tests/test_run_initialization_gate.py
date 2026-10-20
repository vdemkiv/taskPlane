"""First checkpoint gates reuse real-hook readiness; fixture calls are not live proof."""
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


def test_first_submission_requires_completed_pair(tmp_path):
    from taskplane.tests.test_workflow_evidence import bound_output
    c, _ = setup(tmp_path)
    state = remove_readiness(c)
    target = bound_output(c, state)
    with pytest.raises(w.Refusal, match="(?i)(initializ|readiness|pre/post|hook pair)"):
        c.apply("submit", state["run"], expected_revision=state["revision"],
                output=target, tasks="tasks.json")
    assert c.report()["visits"][0]["packet"] is None


def test_positive_first_decision_rechecks_pair(tmp_path):
    c, state = setup(tmp_path)
    state = submit(c, state)
    state = remove_readiness(c)
    with pytest.raises(w.Refusal, match="(?i)(initializ|readiness|pre/post|hook pair)"):
        decide(c, state)
    assert not c.report()["decisions"]


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
def test_foreign_or_manual_pair_cannot_approve_then_read_recovers(tmp_path, defect):
    from taskplane.tests.test_worker_runtime import parent_pair
    c, state = setup(tmp_path)
    state = submit(c, state)
    db = json.loads(c._path().read_text())
    active = db["runs"][state["run"]]
    active["parent_hook_readiness"][defect] = False if defect == "automatic" else "foreign"
    c._path().write_text(json.dumps(db))
    before = c.report(diagnostics=False)
    with pytest.raises(w.Refusal, match="Initialization"):
        decide(c, before)
    parent_pair(c)
    recovered = decide(c, c.report(diagnostics=False))
    assert recovered["run"] == state["run"]
    assert recovered["visits"][0]["packet"] == state["visits"][0]["packet"]
    assert recovered["visits"][0]["decision"] == "approved"
