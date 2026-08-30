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


@pytest.fixture(scope="module")
def h1_receipt_bundle(tmp_path_factory):
    """Real producer/evaluator helpers issue all 13 exact-SHA receipts."""
    workspace = tmp_path_factory.mktemp("h1-workspace") / "repository"
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks",
                    str(ROOT), str(workspace)], check=True)
    accepted_design = subprocess.run(
        ["git", "show", "fe5df7b:design/contract.json"], cwd=ROOT,
        check=True, capture_output=True, text=True,
        encoding="utf-8").stdout
    (workspace / "design" / "contract.json").write_text(
        accepted_design, encoding="utf-8")
    subprocess.run(["git", "config", "user.email", "e@e"], cwd=workspace,
                   check=True)
    subprocess.run(["git", "config", "user.name", "fixture"], cwd=workspace,
                   check=True)
    subprocess.run(["git", "add", "design/contract.json"], cwd=workspace,
                   check=True)
    subprocess.run(["git", "commit", "-qm", "fixture R-0002 authority"],
                   cwd=workspace, check=True)
    candidate_sha = _head(workspace)
    receipt_directory = tmp_path_factory.mktemp("h1-receipts")
    evaluate_receipts = []
    finding_map = {
        row["id"]: row for row in json.loads(
            (workspace / "design" / "contract.json").read_text(
                encoding="utf-8"))["finding_map"]
    }
    for finding_id in remediation_trace.H1_FINDING_IDS:
        task_id = finding_map[finding_id]["task"]
        producer = remediation_trace.agent_identity(
            role="build", agent_id=f"builder-{finding_id}",
            task_name=f"h1-build-{finding_id}", task_id=task_id,
            session_id="h1-receipt-fixture")
        evaluator = remediation_trace.agent_identity(
            role="evaluate", agent_id=f"evaluator-{finding_id}",
            task_name=f"h1-evaluate-{finding_id}", task_id=task_id,
            session_id="h1-receipt-fixture")
        build_path = remediation_trace.produce_build_receipt(
            str(workspace), receipt_directory, candidate_sha=candidate_sha,
            finding_id=finding_id, producer_identity=producer)
        evaluate_receipts.append(
            remediation_trace.produce_evaluate_receipt(
                str(workspace), receipt_directory,
                build_receipt_path=build_path,
                evaluator_identity=evaluator))

    trace = remediation_trace.build_h1_trace(
        str(workspace), candidate_sha=candidate_sha,
        evaluate_receipt_paths=evaluate_receipts)
    return {
        "workspace": workspace,
        "candidate_sha": candidate_sha,
        "directory": receipt_directory,
        "evaluate_receipts": evaluate_receipts,
        "trace": trace,
        "finding_map": finding_map,
    }


def test_ac2_h1_contracts_close_at_one_sha(h1_receipt_bundle):
    candidate_sha = h1_receipt_bundle["candidate_sha"]
    trace = h1_receipt_bundle["trace"]

    assert remediation_trace.verify_h1_trace(
        str(h1_receipt_bundle["workspace"]), trace) == trace
    assert trace["candidate_sha"] == candidate_sha
    assert trace["required_finding_ids"] == list(
        remediation_trace.H1_FINDING_IDS)
    assert trace["receipt_count"] == 13
    assert trace["result_count"] == 13
    assert all(row["candidate_sha"] == candidate_sha
               for row in trace["results"])
    assert all(row["outcome"] == "closed" and row["independent"] is True
               for row in trace["results"])
    assert all(row["production_boundary"]["contracts"]
               for row in trace["results"])
    assert all(len(row["production_boundary"]["source_sha256"]) == 64 and
               len(row["production_boundary"]["selector_sha256"]) == 64
               for row in trace["results"])
    assert all(row["selector_execution"]["outcome"] == "passed" and
               row["selector_execution"]["exit_code"] == 0 and
               len(row["selector_execution"]["output_sha256"]) == 64
               for row in trace["results"])
    assert all(row["producer_identity"]["identity_fingerprint"] !=
               row["evaluator_identity"]["identity_fingerprint"]
               for row in trace["results"])

    forged = copy.deepcopy(trace)
    forged["results"][0]["candidate_sha"] = "0" * 40
    with pytest.raises(remediation_trace.RemediationTraceError):
        remediation_trace.verify_h1_trace(
            str(h1_receipt_bundle["workspace"]), forged)


def test_h1_trace_rejects_missing_and_replayed_receipts(h1_receipt_bundle):
    receipts = h1_receipt_bundle["evaluate_receipts"]
    candidate_sha = h1_receipt_bundle["candidate_sha"]
    with pytest.raises(remediation_trace.RemediationTraceError,
                       match="one unique receipt"):
        remediation_trace.build_h1_trace(
            str(h1_receipt_bundle["workspace"]), candidate_sha=candidate_sha,
            evaluate_receipt_paths=receipts[:-1])
    with pytest.raises(remediation_trace.RemediationTraceError,
                       match="one unique receipt"):
        remediation_trace.build_h1_trace(
            str(h1_receipt_bundle["workspace"]), candidate_sha=candidate_sha,
            evaluate_receipt_paths=[*receipts[:-1], receipts[0]])
    with pytest.raises(remediation_trace.RemediationTraceError,
                       match="receipt path"):
        remediation_trace.build_h1_trace(
            str(h1_receipt_bundle["workspace"]), candidate_sha=candidate_sha,
            evaluate_receipt_paths=[{} for _ in receipts])


def test_h1_trace_rejects_forged_outcomes_labels_and_git_identity(
        h1_receipt_bundle):
    trace = h1_receipt_bundle["trace"]
    mutations = []

    forged_status = copy.deepcopy(trace)
    forged_status["evaluate_receipts"][0]["outcome"] = "failed"
    mutations.append(forged_status)

    forged_role = copy.deepcopy(trace)
    forged_role["evaluate_receipts"][0]["evaluator_identity"]["role"] = \
        "build"
    mutations.append(forged_role)

    forged_git = copy.deepcopy(trace)
    forged_git["evaluate_receipts"][0]["build_receipt"]["git_evidence"][
        "executable_sha256"] = "0" * 64
    mutations.append(forged_git)

    forged_output = copy.deepcopy(trace)
    forged_output["evaluate_receipts"][0]["selector_execution"][
        "output_sha256"] = "0" * 64
    mutations.append(forged_output)

    for forged in mutations:
        with pytest.raises(remediation_trace.RemediationTraceError):
            remediation_trace.verify_h1_trace(
                str(h1_receipt_bundle["workspace"]), forged)


def test_h1_receipts_reject_wrong_sha_and_duplicate_native_identity(
        h1_receipt_bundle):
    first_id = remediation_trace.H1_FINDING_IDS[0]
    task_id = h1_receipt_bundle["finding_map"][first_id]["task"]
    producer = remediation_trace.agent_identity(
        role="build", agent_id="duplicate-agent", task_name="build-duplicate",
        task_id=task_id, session_id="h1-duplicate-fixture")
    with pytest.raises(remediation_trace.RemediationTraceError,
                       match="exact current HEAD"):
        remediation_trace.produce_build_receipt(
            str(h1_receipt_bundle["workspace"]),
            h1_receipt_bundle["directory"],
            candidate_sha="0" * 40, finding_id=first_id,
            producer_identity=producer)

    build_path = remediation_trace.produce_build_receipt(
        str(h1_receipt_bundle["workspace"]), h1_receipt_bundle["directory"],
        candidate_sha=h1_receipt_bundle["candidate_sha"],
        finding_id=first_id, producer_identity=producer)
    duplicated_evaluator = remediation_trace.agent_identity(
        role="evaluate", agent_id="duplicate-agent",
        task_name="evaluate-duplicate", task_id=task_id,
        session_id="h1-duplicate-fixture")
    with pytest.raises(remediation_trace.RemediationTraceError,
                       match="not independent"):
        remediation_trace.produce_evaluate_receipt(
            str(h1_receipt_bundle["workspace"]),
            h1_receipt_bundle["directory"],
            build_receipt_path=build_path,
            evaluator_identity=duplicated_evaluator)


def test_h1_git_evidence_ignores_ambient_path_config_and_repository_redirects(
        h1_receipt_bundle, tmp_path, monkeypatch):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    marker = tmp_path / "shadow-git-ran"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
    fake_git.chmod(0o755)
    evil = tmp_path / "evil"
    evil.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=evil, check=True)
    hostile_config = tmp_path / "hostile.gitconfig"
    hostile_config.write_text(
        f"[core]\n\tworktree = {evil}\n\tfsmonitor = true\n",
        encoding="utf-8")
    hostile = {
        "PATH": str(fake_bin),
        "GIT_DIR": str(evil / ".git"),
        "GIT_WORK_TREE": str(evil),
        "GIT_INDEX_FILE": str(tmp_path / "evil-index"),
        "GIT_OBJECT_DIRECTORY": str(evil / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(
            evil / ".git" / "objects"),
        "GIT_CONFIG_GLOBAL": str(hostile_config),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.worktree",
        "GIT_CONFIG_VALUE_0": str(evil),
    }
    for key, value in hostile.items():
        monkeypatch.setenv(key, value)

    trace = remediation_trace.build_h1_trace(
        str(h1_receipt_bundle["workspace"]),
        candidate_sha=h1_receipt_bundle["candidate_sha"],
        evaluate_receipt_paths=h1_receipt_bundle["evaluate_receipts"])

    assert trace == h1_receipt_bundle["trace"]
    assert Path(trace["git_evidence"]["executable_path"]).is_absolute()
    assert Path(trace["git_evidence"]["executable_path"]) != fake_git
    assert len(trace["git_evidence"]["identity_fingerprint"]) == 64
    assert not marker.exists()


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
