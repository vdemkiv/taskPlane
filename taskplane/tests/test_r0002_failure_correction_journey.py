"""Public journey: an evaluator red is classified before correction."""
from __future__ import annotations

import json
import subprocess

from taskplane import evaluation_output, failure_routing, loop


def _record(candidate, failure_class: str) -> dict:
    evidence = {"selector": "test_current_contract", "returncode": 1}
    return {
        "schema": failure_routing.FAILURE_RECORD_SCHEMA_ID,
        "id": "failure-1", "source": "pytest", "stage": "evaluate",
        "repro": "run the exact selector", "evidence": evidence,
        "evidence_digest": failure_routing.evidence_digest(evidence),
        "class": failure_class, "reason": "observed semantic failure",
        "owner": "evaluation", "cluster": "current-contract",
        "route": failure_routing.route_for_class(failure_class),
        "candidate": candidate,
    }


def test_non_product_failure_inventory_cannot_open_product_fix(
        tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.setattr(loop.tp, "git_head", lambda _ws: "a" * 40)
    task = {"id": "CONTROL-PLANE-HOST-WIRING"}
    candidate = loop._failure_candidate_identity(str(workspace), task)
    verdict = {
        "schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": task["id"], "requirement": "R-0002", "verdict": "fail",
        "evaluation": {"status": "complete", "reason_code": "none",
                       "detail": "typed red"},
        "criteria": [{"criterion": "classification", "status": "not-met",
                      "evidence": "failure-1"}],
        "graph": {"dispositions": [], "requirements_checked": [],
                  "contracts_checked": []},
        "failures": [_record(candidate, "test")],
    }
    path = tmp_path / "verdict.json"
    path.write_text(json.dumps(verdict), encoding="utf-8")
    monkeypatch.setattr(loop.runtime_storage, "evaluation_path",
                        lambda _ws: str(path))

    errors, _value, decision = loop._evaluation_failure_routing(
        str(workspace), {}, task)

    assert errors == []
    assert decision["next"] == "test-correction"
    assert decision["product_fix_allowed"] is False
    assert decision["records"][0]["evidence"] == {
        "returncode": 1, "selector": "test_current_contract"}


def test_build_red_is_durably_unknown_and_held_before_evaluate(
        tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "build-red"
    workspace.mkdir()
    (workspace / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Taskplane Test"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "add", "owned.py"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=workspace,
                   check=True)
    state = {
        "run_id": "run-build-red", "step": "execute",
        "goal": "classify before correction", "baseline": loop.tp.git_head(
            str(workspace)), "current_task": 0, "max_fix_cycles": 2,
        "checkpoints": ["plan", "em"], "submission_required": False,
        "parallel": False,
        "tasks": [{"id": "BUILD-RED", "status": "running",
                   "scope": ["owned.py"], "fix_cycles": 0}],
    }
    loop.save(str(workspace), state)
    monkeypatch.setattr(
        loop.tp, "release_worker_contracts_for_gate", lambda *_a, **_k: [])
    monkeypatch.setattr(loop.tp, "clear", lambda *_a, **_k: None)

    result = loop.gate(str(workspace), "fail", note="selector red")
    stored = loop.load(str(workspace))

    assert result["step"] == "evaluate"
    routing = stored["tasks"][0]["failure_routing"]
    assert routing["next"] == "hold"
    assert routing["hold_required"] is True
    assert routing["product_fix_allowed"] is False
    assert routing["records"][0]["class"] == "unknown"
    assert routing["records"][0]["stage"] == "execute"
    assert stored["_build_failed"] is True
