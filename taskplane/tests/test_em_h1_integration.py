"""H1 exact-candidate join and semantic checkpoint adversarial proofs."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))

import governed_commands  # noqa: E402
import loop  # noqa: E402
import remediation_trace  # noqa: E402
import taskplane_lite as contract_engine  # noqa: E402
from taskplane import checkpoint  # noqa: E402


def _head(workspace: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace,
        text=True, encoding="utf-8", errors="replace").strip()


def test_ac2_h1_contracts_close_at_one_sha():
    candidate_sha = _head(ROOT)
    results = []
    finding_map = {
        row["id"]: row for row in json.loads(
            (ROOT / "design" / "contract.json").read_text(
                encoding="utf-8"))["finding_map"]
    }
    for finding_id in remediation_trace.H1_FINDING_IDS:
        task_id = finding_map[finding_id]["task"]
        results.append(remediation_trace.finding_result(
            str(ROOT), candidate_sha=candidate_sha, finding_id=finding_id,
            status="closed", selector_status="passed",
            builder_identity=f"native-builder:{task_id}",
            evaluator_identity=f"native-evaluator:{task_id}"))

    trace = remediation_trace.build_h1_trace(
        str(ROOT), candidate_sha=candidate_sha, results=results)

    assert remediation_trace.verify_h1_trace(str(ROOT), trace) == trace
    assert trace["candidate_sha"] == candidate_sha
    assert trace["required_finding_ids"] == list(
        remediation_trace.H1_FINDING_IDS)
    assert trace["result_count"] == 13
    assert all(row["candidate_sha"] == candidate_sha
               for row in trace["results"])
    assert all(row["production_boundary"]["contracts"]
               for row in trace["results"])
    assert all(len(row["production_boundary"]["source_sha256"]) == 64 and
               len(row["production_boundary"]["selector_sha256"]) == 64
               for row in trace["results"])

    forged = copy.deepcopy(trace)
    forged["results"][0]["candidate_sha"] = "0" * 40
    with pytest.raises(remediation_trace.RemediationTraceError):
        remediation_trace.verify_h1_trace(str(ROOT), forged)


def _checkpoint_workspace(tmp_path: Path) -> tuple[Path, dict, dict]:
    workspace = tmp_path / "semantic-checkpoint-repo"
    proof = workspace / "taskplane" / "tests" / "test_focused.py"
    proof.parent.mkdir(parents=True)
    proof.write_text("def test_focused():\n    assert True\n", encoding="utf-8")
    (workspace / "taskplane" / "checkpoint.py").write_text(
        "CHECKPOINT_FIXTURE = True\n", encoding="utf-8")
    (workspace / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "e@e"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "t"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "checkpoint proof"],
                   cwd=workspace, check=True)
    task = {
        "id": "checkpoint-task",
        "scope": ["taskplane/checkpoint.py", "taskplane/tests/**"],
        "tests": "true",
        "criteria": ["AC-1"],
        "checkpoint": {
            "checkpoint_id": "cp-h1-semantic",
            "phase": "build",
            "ac_ids": ["AC-1"],
            "predecessor_checkpoint_ids": [],
            "focused_proof": {
                "path": "taskplane/tests/test_focused.py",
                "argv": ["python3", "-m", "pytest", "-q",
                         "taskplane/tests/test_focused.py"],
            },
            "ratchet_baseline": {"cycle_count": 0},
        },
    }
    state = {
        "governance_revision": 2,
        "submission_required": True,
        "graph_governance": False,
        "goal": "semantic checkpoint",
        "run_id": "run-h1-semantic",
        "parallel": False,
        "step": "execute",
        "tasks": [task],
        "current_task": 0,
        "plan_fingerprint": "f" * 64,
    }
    loop.save(str(workspace), state)
    contract = contract_engine.build_contract(
        "checkpoint-task", read_only=True, tools=["Read"])
    contract_engine.activate(str(workspace), contract, snapshot=None)
    return workspace, state, task


def test_h1_semantic_checkpoint_runs_without_reenabling_readonly_shell(
        tmp_path):
    workspace, state, task = _checkpoint_workspace(tmp_path)
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="read-only review contract"):
        governed_commands.execute(str(workspace), "launch", {
            "authorization": "attacker", "argv": ["git", "status"],
            "run_id": state["run_id"], "task_id": task["id"],
        })
    with pytest.raises(governed_commands.GovernedCommandError,
                       match="unknown fields: argv"):
        governed_commands.execute(str(workspace), "checkpoint", {
            "authorization": "attacker",
            "argv": [sys.executable, "-c", "open('owned', 'w').write('x')"],
            "run_id": state["run_id"], "task_id": task["id"],
        })

    receipt = loop._run_submit_checkpoint(
        str(workspace), state, task, str(workspace))

    assert receipt["producer"] == "taskplane.checkpoint-engine/v1"
    assert receipt["verdict"] == "green"
    assert receipt["worktree_revision"] == _head(workspace)
    assert len(receipt["runtime_boundary_receipt_digest"]) == 64
    assert not (workspace / "owned").exists()
    handle = receipt["command"]["handle"]
    boundary = governed_commands.semantic_checkpoint_execution_evidence(
        str(workspace), "loop-submit-checkpoint:checkpoint-task", handle)
    assert boundary["source_sha"] == _head(workspace)
    assert boundary["state"] == "succeeded"
    assert boundary["runtime_argv"][:4] == [
        str(Path(sys.executable).resolve()), "-P", "-m", "pytest"]
    assert boundary["runtime_environment"] == \
        governed_commands._checkpoint_environment()
    assert "sandbox_path" not in boundary


def test_h1_semantic_checkpoint_receipt_tamper_fails_closed(tmp_path):
    workspace, state, task = _checkpoint_workspace(tmp_path)
    receipt = loop._run_submit_checkpoint(
        str(workspace), state, task, str(workspace))
    handle = receipt["command"]["handle"]
    path = (Path(governed_commands._runtime_root(str(workspace))) / handle /
            "semantic-checkpoint-receipt.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_sha"] = "0" * 40
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(governed_commands.GovernedCommandError,
                       match="execution receipt is invalid"):
        governed_commands.semantic_checkpoint_execution_evidence(
            str(workspace), "loop-submit-checkpoint:checkpoint-task", handle)


def test_h1_semantic_checkpoint_reports_unavailable_without_fallback(
        tmp_path, monkeypatch):
    workspace, state, task = _checkpoint_workspace(tmp_path)
    monkeypatch.setattr(
        governed_commands, "detached_process_groups_supported", lambda: False)

    result = governed_commands.execute(str(workspace), "checkpoint", {
        "authorization": "loop-submit-checkpoint:checkpoint-task",
        "run_id": state["run_id"], "task_id": task["id"],
    })

    assert result == {
        "schema": governed_commands.RESULT_SCHEMA,
        "action": "checkpoint",
        "status": "unavailable",
        "reason_code": "checkpoint_process_tree_unavailable",
        "error": ("semantic checkpoint requires detached process-tree "
                  "ownership; no process was started"),
    }
    assert not (workspace / ".taskplane" /
                "command-runtime-v1").exists()
