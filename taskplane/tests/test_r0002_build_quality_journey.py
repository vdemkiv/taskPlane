"""Public journey: current Build evidence is required before Evaluate."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from types import SimpleNamespace

from taskplane import build_quality, loop, run_artifacts, test_strategy
from taskplane import tp as tp_cli
from taskplane.tests.test_build_quality import (
    FIXTURE_PATH, PRODUCER_ID, _advance, _exact, _radius, _static, _strategy,
)


def test_recorded_build_quality_is_current_then_severs_when_candidate_moves(
        tmp_path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "owned.py"], cwd=workspace, check=True)
    subprocess.run([
        "git", "-c", "user.name=Taskplane", "-c",
        "user.email=taskplane@example.invalid", "commit", "-qm", "base",
    ], cwd=workspace, check=True)
    store = tmp_path / "store"
    monkeypatch.setattr(loop.tp, "external_store_root", lambda _ws: str(store))
    state = {
        "run_id": "run-r0002", "step": "execute", "current_task": 0,
        "tasks": [{
            "id": "QUALITY", "scope": ["owned.py"],
            "test_contract": {"changed_producers": ["owned.py"]},
        }],
    }
    loop.save(str(workspace), state)
    task = state["tasks"][0]
    strategy = _strategy()
    binding = loop._build_quality_binding(
        str(workspace), state, task, "execute")
    receipt = build_quality.begin_receipt(
        strategy, binding=binding, criterion_ids=["AC-TST1"],
        changed_producer_ids=[PRODUCER_ID],
        changed_paths=["taskplane/test_strategy.py", FIXTURE_PATH])
    receipt = _advance(strategy, receipt, "static", _static(receipt), "local")
    receipt = _advance(
        strategy, receipt, "exact-selector", _exact(receipt), "local")
    receipt = _advance(
        strategy, receipt, "changed-radius", _radius(receipt), "ci")
    receipt = _advance(
        strategy, receipt, "proportional-suite",
        {"scope": ["taskplane/tests/test_test_strategy_contract.py"],
         "passed": True}, "ci")
    artifact_root = tmp_path / "artifacts"
    artifact_binding = run_artifacts.create_binding(
        repository_id="repo-r0002", run_id="run-r0002", stage_id="design",
        stage_instance_id="design-stage", candidate={
            "id": "design", "fingerprint": "1" * 64},
        settings_digest=binding["settings_digest"],
        source_fingerprint="2" * 64)
    run_artifacts.create_manifest(artifact_root, binding=artifact_binding)
    monkeypatch.setattr(loop, "_run_artifact_root",
                        lambda *_a: str(artifact_root))

    recorded = loop.record_build_quality(
        str(workspace), "QUALITY", strategy=strategy, receipt=receipt)

    assert recorded["admitted"] is True
    current = loop.load(str(workspace))
    assert loop._build_quality_errors(
        str(workspace), current, current["tasks"][0], "execute") == []
    strategy_path = tmp_path / "strategy.json"
    receipt_path = tmp_path / "receipt.json"
    strategy_path.write_text(json.dumps(strategy), encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setitem(sys.modules, "loop", loop)
    exit_code = tp_cli.cmd_loop(SimpleNamespace(
        workspace=str(workspace), loop_action="build-quality",
        task="QUALITY", strategy=str(strategy_path),
        receipt=str(receipt_path), stage="execute"))
    replayed = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert replayed["admitted"] is True
    manifest = run_artifacts.load_manifest(artifact_root)
    matching = [entry for entry in manifest["classes"]["validation"]["entries"]
                if (entry.get("metadata") or {}).get("receipt_fingerprint") ==
                receipt["fingerprint"]]
    assert len(matching) == 1

    (workspace / "owned.py").write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "owned.py"], cwd=workspace, check=True)
    subprocess.run([
        "git", "-c", "user.name=Taskplane", "-c",
        "user.email=taskplane@example.invalid", "commit", "-qm", "move",
    ], cwd=workspace, check=True)
    assert "another active stage" in loop._build_quality_errors(
        str(workspace), current, current["tasks"][0], "execute")[0]


def test_record_build_quality_refuses_strategy_outside_approved_design_plan_scope(
        tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "owned.py"], cwd=workspace, check=True)
    subprocess.run([
        "git", "-c", "user.name=Taskplane", "-c",
        "user.email=taskplane@example.invalid", "commit", "-qm", "base",
    ], cwd=workspace, check=True)
    store = tmp_path / "store"
    monkeypatch.setattr(loop.tp, "external_store_root", lambda _ws: str(store))

    approved = _strategy()
    approved_selector = next(
        row for row in approved["acceptance_criteria"]
        if row["id"] == "AC-TST1")["selectors"][0]
    design = workspace / "design"
    design.mkdir()
    strategy_path = design / "test-strategy.json"
    strategy_path.write_text(json.dumps(approved), encoding="utf-8")
    (design / "contract.json").write_text(json.dumps({
        "acceptance_map": [{
            "criterion": "Approved test behavior",
            "tests": [approved_selector],
        }],
        "test_strategy": {"authority": {
            "schema": "taskplane.design-test-strategy-reference/v1",
            "path": "design/test-strategy.json",
            "strategy_fingerprint": approved[
                "contract_fingerprint_sha256"],
        }},
    }), encoding="utf-8")
    task = {
        "id": "QUALITY", "scope": ["owned.py"],
        "tests": f"python3 -m pytest -q {approved_selector}",
        "criteria": ["Approved test behavior"],
        "acceptance_refs": ["Approved test behavior"],
        "test_contract": {"changed_producers": ["owned.py"]},
        "test_strategy_authority": {
            "schema": "taskplane.plan-test-strategy-reference/v1",
            "path": "design/test-strategy.json",
            "strategy_fingerprint": approved[
                "contract_fingerprint_sha256"],
            "criterion_ids": ["AC-TST1"],
            "changed_producer_ids": [PRODUCER_ID],
        },
    }
    state = {
        "run_id": "run-strategy-authority", "step": "execute",
        "current_task": 0, "design_required": True,
        "design_fingerprint": "d" * 64, "tasks": [task],
    }
    task["test_strategy_authority_receipt"] = \
        loop._seal_task_test_strategy_authority(
            str(workspace), state, task)
    loop.save(str(workspace), state)

    substitute = copy.deepcopy(approved)
    next(row for row in substitute["acceptance_criteria"]
         if row["id"] == "AC-TST1")["selectors"] = [
             "taskplane/tests/test_settings.py::"
             "test_non_executable_settings_fail_at_load_time"]
    substitute = test_strategy.seal_strategy(substitute)
    binding = loop._build_quality_binding(
        str(workspace), state, task, "execute")
    receipt = build_quality.begin_receipt(
        substitute, binding=binding, criterion_ids=["AC-TST1"],
        changed_producer_ids=[PRODUCER_ID],
        changed_paths=["taskplane/test_strategy.py", FIXTURE_PATH])
    receipt = _advance(
        substitute, receipt, "static", _static(receipt), "local")
    receipt = _advance(
        substitute, receipt, "exact-selector", _exact(receipt), "local")
    receipt = _advance(
        substitute, receipt, "changed-radius", _radius(receipt), "ci")
    receipt = _advance(
        substitute, receipt, "proportional-suite",
        {"scope": ["taskplane/tests/test_settings.py"], "passed": True},
        "ci")

    refused = loop.record_build_quality(
        str(workspace), "QUALITY", strategy=substitute, receipt=receipt)

    assert "approved Design/Plan test strategy" in refused["error"]
    current = loop.load(str(workspace))
    assert current["tasks"][0]["test_strategy_authority_receipt"] == \
        task["test_strategy_authority_receipt"]
    assert "test_strategy" not in current["tasks"][0]
