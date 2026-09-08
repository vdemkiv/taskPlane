"""Failure classification must not collect acceptance-suite success."""
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from taskplane import loop


@pytest.fixture
def evidence_workspace(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "source.py").write_text("value = 1\n")
    (root / ".gitignore").write_text(".taskplane/\n.eval/\n")
    for args in (["init", "-q"], ["add", "source.py", ".gitignore"],
            ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    ws = str(root)
    monkeypatch.delenv("TASKPLANE_TASK", raising=False)
    monkeypatch.delenv("TASKPLANE_NO_SUITE_CACHE", raising=False)
    tests = shlex.quote(sys.executable) + " -c " + shlex.quote("print('actual small suite')")
    task = {"id":"T19", "req":"R-0001", "status":"pending", "scope":["source.py"],
        "tests":tests, "criteria":["genuine evidence remains mandatory"]}
    state = {"run_id":"generated-loop", "step":"evaluate", "current_task":0,
        "tasks":[task], "baseline":loop.tp.git_head(ws), "_build_failed":True,
        "review_kernel_runs":{"evaluate:T19":{"run_id":"a" * 32, "workspace":ws}}}
    task["failure_routing"] = loop._detected_build_failure_routing(ws, task,
        {"outcome":"fail", "fingerprint":loop.tp.workspace_fingerprint(ws)}, "execute")
    loop.save(ws, state)
    classification = loop._failed_build_classification(ws, state, task, evaluator_attempt_id="a" * 32)
    contract = loop.tp.build_contract("Independent failed Build classifier", read_only=True)
    contract["failure_classification"] = classification
    contract = loop.tp.prepare_worker_contract(ws, contract, stage="evaluate", task="T19",
        task_name="tp_step_evaluator_generated", role_marker="taskplane-role:tp-evaluator")
    loop.tp.activate(ws, contract, snapshot=loop.tp.git_head(ws), task_slot_override=contract["task_slot"])
    monkeypatch.setenv("TASKPLANE_TASK", contract["task_slot"])
    return ws, root, state, contract


@pytest.mark.parametrize("existing_verdict", [False, True])
def test_classification_evidence_write_never_runs_or_cites_acceptance_suite(evidence_workspace, monkeypatch, existing_verdict):
    ws, root, state, _ = evidence_workspace
    path = root / ".eval/verdict.json"
    old = b'{"verdict":"fail","note":"honest historical finding"}'
    if existing_verdict:
        path.parent.mkdir()
        path.write_bytes(old)
    for name in ("run_suite_command", "suite_cache_lookup", "suite_cache_store", "_suite_cache_key"):
        monkeypatch.setattr(loop.tp, name, lambda *a, **k:pytest.fail("classification touched acceptance suite"))
    result = loop.evidence(ws, write=True)
    assert not result.get("error"), result
    assert result["failure_classification"]["mode"] == "failure-classification-only"
    assert result["acceptance_allowed"] is False
    assert result["suite"] == {"command":state["tasks"][0]["tests"], "status":"not-run-classification-only",
        "returncode":None, "cited":False, "acceptance_evidence":False}
    assert result["verdict"] == result["verdict_template"]["verdict"] == ""
    assert "PASS" in result["note"]
    assert loop._load_raw(ws) == state
    if existing_verdict:
        assert path.read_bytes() == old and result["written"] is False
    else:
        assert json.loads(path.read_text())["verdict"] == ""


@pytest.mark.parametrize("damage", ["missing-contract", "missing-classification", "boolean", "incomplete",
    "foreign-run", "foreign-task", "stale-attempt", "missing-binding", "foreign-workspace", "candidate",
    "missing-detection", "changed-detection", "historical-pass", "not-failed"])
def test_classification_refuses_invalid_binding_before_suite_or_verdict_write(evidence_workspace, monkeypatch, damage):
    ws, root, state, contract = evidence_workspace
    active = copy.deepcopy(loop.tp.load_active(ws))
    classification = active["failure_classification"]
    if damage == "missing-contract": Path(loop.tp.active_contract_path(ws, contract["task_slot"])).unlink()
    elif damage == "missing-classification": active.pop("failure_classification")
    elif damage == "boolean": active["failure_classification"] = True
    elif damage == "incomplete": classification.pop("detected_failure")
    elif damage == "foreign-run": classification["run_id"] = "foreign"
    elif damage == "foreign-task": classification["task_id"] = "T20"
    elif damage == "stale-attempt": state["review_kernel_runs"]["evaluate:T19"]["run_id"] = "b" * 32
    elif damage == "missing-binding": state.pop("review_kernel_runs")
    elif damage == "foreign-workspace": state["review_kernel_runs"]["evaluate:T19"]["workspace"] = str(root.parent)
    elif damage == "candidate":
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "--allow-empty", "-qm", "new candidate"], cwd=ws, check=True, capture_output=True)
    elif damage == "missing-detection": state["tasks"][0].pop("failure_routing")
    elif damage == "changed-detection": state["tasks"][0]["failure_routing"]["fingerprint"] = "f" * 64
    elif damage == "historical-pass":
        state["tasks"][0]["failure_routing"]["records"][0]["evidence"]["submission_outcome"] = "pass"
    elif damage == "not-failed": state.pop("_build_failed")
    if damage != "missing-contract":
        loop.tp.atomic_write_json(loop.tp.active_contract_path(ws, contract["task_slot"]), active)
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "run_suite_command", lambda *a, **k:pytest.fail("invalid classification ran suite"))
    result = loop.evidence(ws, write=True)
    assert "classification" in result.get("error", ""), result
    assert not (root / ".eval/verdict.json").exists()
    assert loop._load_raw(ws) == state


@pytest.mark.parametrize("source", ["direct", "cache", "uncached"])
def test_ordinary_evidence_retains_real_suite_and_citation_paths(evidence_workspace, monkeypatch, source):
    ws, _, state, contract = evidence_workspace
    state.pop("_build_failed")
    active = loop.tp.load_active(ws)
    active.pop("failure_classification")
    loop.tp.atomic_write_json(loop.tp.active_contract_path(ws, contract["task_slot"]), active)
    tests = state["tasks"][0]["tests"]
    env = {k:v for k,v in os.environ.items() if k != "TASKPLANE_TASK"}
    if source == "direct":
        state["_suite_evidence"] = {"T19":{"schema":"taskplane.suite-evidence/v1", "command":tests,
            "key":loop.tp._suite_cache_key(ws, tests, env), "returncode":0, "tail":"real prior test fixture", "duration_s":1}}
    if source == "cache":
        loop.tp.suite_cache_store(ws, tests, env, returncode=0, tail="real prior test fixture", duration_s=1)
    loop.save(ws, state)
    original = loop.tp.run_suite_command
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(loop.tp, "run_suite_command", run)
    result = loop.evidence(ws)
    assert not result.get("error"), result
    assert result["suite"]["returncode"] == 0
    assert result["suite"]["cited"] is (source != "uncached")
    assert len(calls) == (1 if source == "uncached" else 0)
    if source == "uncached": assert "actual small suite" in result["suite"]["tail"]
    assert "failure_classification" not in result
