"""Public journey: terminal telemetry and Retro seal before transition."""
from __future__ import annotations

import json
import io
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from taskplane import loop, requirements, run_artifacts
from taskplane import tp as tp_cli


def _legacy_execute_workspace(tmp_path, monkeypatch, name: str):
    workspace = tmp_path / name
    workspace.mkdir()
    (workspace / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Taskplane Test"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "add", "owned.py"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "legacy base"],
                   cwd=workspace, check=True)
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / f"private-{name}"))
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "legacy-terminal-owner")
    monkeypatch.delenv("TASKPLANE_TASK", raising=False)
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    tasks = [
        {"id": "LEGACY-A", "status": "pending", "deps": [],
         "scope": ["owned.py"],
         "legacy_evidence": {"source": "2.18.4", "preserve": True}},
        {"id": "LEGACY-B", "status": "pending", "deps": ["LEGACY-A"],
         "scope": ["owned.py"]},
    ]
    state = {
        "governance_revision": 2,
        "run_id": f"legacy-{name}",
        "baseline": loop.tp.git_head(str(workspace)),
        "settings_digest": "a" * 64,
        "goal": "truthfully close an upgraded legacy run",
        "step": "execute", "tasks": tasks, "current_task": 0,
        "max_fix_cycles": 2, "checkpoints": ["plan", "em"],
        "legacy_evidence": {"design": "approved-before-upgrade"},
    }
    loop.save(str(workspace), state)
    return str(workspace), state


def test_legacy_execute_run_migrates_only_for_attributable_non_normal_terminal_and_retro_stays_truthful(
        tmp_path, monkeypatch) -> None:
    workspace, legacy = _legacy_execute_workspace(
        tmp_path, monkeypatch, "legacy-journey")
    preserved_tasks = json.loads(json.dumps(legacy["tasks"]))
    preserved_evidence = json.loads(json.dumps(legacy["legacy_evidence"]))

    terminal = loop.terminalize_run(
        workspace, "interruption", by="orchestrator")
    stored = loop.load(workspace)

    assert "error" not in terminal, terminal
    assert stored["step"] == "failed"
    assert stored["tasks"] == preserved_tasks
    assert stored["legacy_evidence"] == preserved_evidence
    migration = stored["legacy_terminal_control_plane_migration"]
    assert migration["execution_status"] == "unproven"
    assert migration["task_statuses_preserved"] == [
        {"id": "LEGACY-A", "status": "pending"},
        {"id": "LEGACY-B", "status": "pending"},
    ]
    assert stored["run_artifact_binding"]["candidate"][
        "execution_status"] == "unproven"
    assert stored["terminal_metrics"]["status"] == "unavailable"
    assert "wave_metrics_receipt" not in stored
    assert not ({"total_tokens", "uncached_input_tokens", "effective_tokens"}
                & set(stored["wave_metrics_unavailable"]))
    assert stored["terminal_cleanup"]["cleanup_status"] == "clean"
    assert stored["terminal_cleanup"]["leak_count"] == 0

    retrospective = loop.retro(workspace)
    closed = loop.load(workspace)

    assert "error" not in retrospective, retrospective
    assert closed["step"] == "failed"
    assert closed["tasks"] == preserved_tasks
    assert retrospective["tasks"] == [
        {"id": "LEGACY-A", "status": "pending", "fix_cycles": 0},
        {"id": "LEGACY-B", "status": "pending", "fix_cycles": 0},
    ]
    assert retrospective["execution_metrics"]["active_worker_seconds"] == 0
    assert retrospective["execution_metrics"]["delivery_wall_seconds"] == 0
    assert retrospective["wave_metrics"]["token_usage"][
        "status"] == "unavailable"


@pytest.mark.parametrize("damage", ("partial", "foreign", "ambiguous"))
def test_legacy_terminal_migration_refuses_partial_foreign_or_ambiguous_control_plane_state(
        tmp_path, monkeypatch, damage) -> None:
    workspace, legacy = _legacy_execute_workspace(
        tmp_path, monkeypatch, f"legacy-{damage}")
    preserved_tasks = json.loads(json.dumps(legacy["tasks"]))
    state = loop.load(workspace)
    if damage == "partial":
        state["run_start_step"] = "plan"
        loop.save(workspace, state)
    elif damage == "ambiguous":
        state["legacy_terminal_control_plane_migration"] = {}
        loop.save(workspace, state)
    else:
        root = loop._ensure_run_artifact_parent(workspace, state)
        identity = loop.runtime_storage.resolve_repository_identity(workspace)
        foreign = run_artifacts.create_binding(
            repository_id=identity.repo_id, run_id=state["run_id"],
            stage_id="foreign", stage_instance_id="foreign-instance",
            candidate={"id": "foreign", "fingerprint": "f" * 64},
            settings_digest=state["settings_digest"],
            source_fingerprint="e" * 64)
        run_artifacts.create_manifest(root, binding=foreign)

    refused = loop.terminalize_run(
        workspace, "interruption", by="orchestrator")
    current = loop.load(workspace)

    assert "failed closed" in refused["error"]
    assert current["step"] == "execute"
    assert current["tasks"] == preserved_tasks
    assert "whole_run_terminal" not in current
    if damage == "partial":
        assert "partial or ambiguous" in refused["error"]
    elif damage == "ambiguous":
        assert "partial or ambiguous" in refused["error"]
    else:
        assert "another binding" in refused["error"]
    assert "run_artifact_binding" not in current


def test_legacy_terminal_migration_crash_replays_one_binding_without_inventing_execution(
        tmp_path, monkeypatch) -> None:
    workspace, legacy = _legacy_execute_workspace(
        tmp_path, monkeypatch, "legacy-crash")
    preserved_tasks = json.loads(json.dumps(legacy["tasks"]))
    production_save = loop.save
    attempts = 0

    def fail_first_state_commit(save_workspace, state):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("crash before legacy migration state commit")
        production_save(save_workspace, state)

    monkeypatch.setattr(loop, "save", fail_first_state_commit)
    crashed = loop.terminalize_run.__wrapped__(
        workspace, "handoff", by="orchestrator")
    unchanged = loop.load(workspace)
    artifact_root = loop._run_artifact_root(workspace, unchanged)
    first_binding = run_artifacts.load_manifest(artifact_root)["binding"]

    assert "failed closed" in crashed["error"]
    assert unchanged["step"] == "execute"
    assert unchanged["tasks"] == preserved_tasks
    assert "run_artifact_binding" not in unchanged

    monkeypatch.setattr(loop, "save", production_save)
    recovered = loop.terminalize_run.__wrapped__(
        workspace, "handoff", by="orchestrator")
    stored = loop.load(workspace)
    manifest = run_artifacts.load_manifest(
        loop._run_artifact_root(workspace, stored))

    assert "error" not in recovered, recovered
    assert stored["step"] == "failed"
    assert stored["tasks"] == preserved_tasks
    assert stored["run_artifact_binding"] == first_binding
    assert manifest["binding"] == first_binding
    assert len(manifest["classes"]["agent-activity"]["entries"]) == 1
    assert len(manifest["classes"]["telemetry"]["entries"]) == 1
    assert len(manifest["classes"]["retro"]["entries"]) == 1
    assert len(manifest["classes"]["cleanup"]["entries"]) == 1
    assert stored["legacy_terminal_control_plane_migration"][
        "execution_status"] == "unproven"
    replayed = loop.replay_terminal_intent(workspace)
    assert replayed["replayed"] is True
    assert replayed["fingerprint"] == recovered["fingerprint"]


@pytest.mark.parametrize(("start", "outcome"), (
    ("product", "cancellation"),
    ("plan", "interruption"),
    ("design", "handoff"),
))
def test_public_non_normal_terminal_journey_is_bound_clean_and_replay_safe(
        tmp_path, monkeypatch, capsys, start, outcome) -> None:
    workspace = tmp_path / f"{start}-{outcome}"
    workspace.mkdir()
    (workspace / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
    spec = workspace / "spec.md"
    if start in {"plan", "design"}:
        spec.write_text("# Approved input\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Taskplane Test"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=workspace,
                   check=True)
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "private"))
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "orchestrator-session")
    monkeypatch.delenv("TASKPLANE_TASK", raising=False)
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    requirement_id = None
    if start == "design":
        requirement_id = requirements.record_requirement(
            str(workspace), "Design terminal journey",
            functional=["design initializes through the governed run"],
            nfr={"security": "no authority broadening",
                 "architecture": "one artifact owner"},
            acceptance=["terminal cleanup proves zero leaks"],
            context_files=["owned.py"])["id"]

    initialized = loop.init(
        str(workspace), "close safely",
        spec_path=("spec.md" if start in {"plan", "design"} else None),
        design=start == "design", requirement_id=requirement_id)

    assert "error" not in initialized
    assert initialized["step"] == (
        "design" if start == "design" else "plan" if start == "plan"
        else "pm")
    assert initialized["run_artifact_binding"]["stage_id"] == start
    cleanup_path = initialized["owned_cleanup_manifest"]
    assert os.path.isfile(cleanup_path)
    assert loop.replay_terminal_intent(str(workspace)) is None
    if start == "design":
        decomposition, _policy = loop._prepare_design_control_plane(
            str(workspace), loop.load(str(workspace)))
        assert decomposition["status"] == "ready"
        design_state = loop.load(str(workspace))
        assert loop._design_control_plane_errors(
            str(workspace), design_state) == []
        assert design_state["design_control_plane_binding"][
            "stage_instance_id"] == design_state["run_artifact_binding"][
                "stage_instance_id"]
        assert design_state["design_control_plane_binding"][
            "design_input_instance_id"].startswith("design-")

    monkeypatch.setitem(sys.modules, "loop", loop)
    exit_code = tp_cli.cmd_loop(
        SimpleNamespace(workspace=str(workspace), loop_action="terminal",
                        outcome=outcome, by="orchestrator"))
    result = json.loads(capsys.readouterr().out)
    stored = loop.load(str(workspace))

    assert exit_code == 0
    assert "error" not in result
    assert result["outcome"] == outcome
    assert stored["step"] == "failed"
    assert stored["whole_run_terminal"]["fingerprint"] == result[
        "fingerprint"]
    assert stored["terminal_metrics"]["status"] in {
        "measured", "unavailable"}
    assert stored["terminal_cleanup"]["cleanup_status"] == "clean"
    assert stored["terminal_cleanup"]["leak_count"] == 0
    manifest = run_artifacts.load_manifest(
        loop._run_artifact_root(str(workspace), stored))
    assert len(manifest["classes"]["agent-activity"]["entries"]) == 1
    assert len(manifest["classes"]["telemetry"]["entries"]) == 1
    assert len(manifest["classes"]["retro"]["entries"]) == 1
    assert len(manifest["classes"]["cleanup"]["entries"]) == 1
    activity = manifest["classes"]["agent-activity"]["entries"][0]
    assert activity["metadata"]["details"]["outcome"] == outcome
    run_root = os.path.dirname(loop._run_artifact_root(
        str(workspace), stored))
    intent = json.loads((Path(run_root) / "terminal" /
                         "whole-run-intent.json").read_text(encoding="utf-8"))
    assert intent["authority"]["kind"] == "orchestrator-host-session"
    assert intent["authority"]["session_id"] == "orchestrator-session"

    replay = loop.replay_terminal_intent(str(workspace))
    assert replay["replayed"] is True
    assert replay["fingerprint"] == result["fingerprint"]

    monkeypatch.setenv("TASKPLANE_TASK", "worker-slot")
    refused = loop.terminalize_run(
        str(workspace), outcome, by="worker")
    assert "orchestrator-only" in refused["error"]
    assert loop.load(str(workspace))["whole_run_terminal"][
        "fingerprint"] == result["fingerprint"]


def test_session_start_replays_only_an_already_persisted_exact_intent(
        tmp_path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "session-replay"
    workspace.mkdir()
    (workspace / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Taskplane Test"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=workspace,
                   check=True)
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "private"))
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "session-recovery-owner")
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    monkeypatch.delenv("TASKPLANE_TASK", raising=False)
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    monkeypatch.setitem(sys.modules, "loop", loop)
    initialized = loop.init(str(workspace), "recover exact intent")
    assert initialized["step"] == "pm"

    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert tp_cli.cmd_context(SimpleNamespace(workspace=str(workspace))) == 0
    capsys.readouterr()
    assert loop.load(str(workspace))["step"] == "pm"

    production_complete = loop._complete_whole_run_terminal
    monkeypatch.setattr(
        loop, "_complete_whole_run_terminal",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("crash")))
    interrupted = loop.terminalize_run.__wrapped__(
        str(workspace), "interruption", by="orchestrator")
    assert "failed closed" in interrupted["error"]
    state = loop.load(str(workspace))
    intent_path, _ = loop._whole_run_terminal_paths(str(workspace), state)
    assert os.path.isfile(intent_path)
    assert state["step"] == "pm"

    monkeypatch.setattr(loop, "_complete_whole_run_terminal",
                        production_complete)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert tp_cli.cmd_context(SimpleNamespace(workspace=str(workspace))) == 0
    output = capsys.readouterr().out
    stored = loop.load(str(workspace))
    assert "replayed exact persisted interruption intent" in output
    assert stored["step"] == "failed"
    assert stored["terminal_cleanup"]["leak_count"] == 0


@pytest.mark.parametrize("crash_window", (
    "receipt-before-stage-transition",
    "stage-transition-before-legacy-state",
))
def test_terminal_receipt_crash_windows_converge_canonical_and_legacy_state(
        tmp_path, monkeypatch, capsys, crash_window) -> None:
    from taskplane.tests.test_stage_loop_integration import (
        _initialize_real_new_run,
    )

    case = tmp_path / crash_window
    monkeypatch.setenv("TASKPLANE_HOME", str(case / "home"))
    workspace, store, initial_stage, initialized = _initialize_real_new_run(
        case, monkeypatch, stage_kind="product",
        goal="converge terminal crash windows")
    monkeypatch.delenv("TASKPLANE_TASK", raising=False)
    monkeypatch.setitem(sys.modules, "loop", loop)
    production_reconcile = loop._reconcile_whole_run_terminal_stage

    if crash_window == "receipt-before-stage-transition":
        def crash(*_args, **_kwargs):
            raise RuntimeError("crash before canonical transition")
    else:
        def crash(*args, **kwargs):
            production_reconcile(*args, **kwargs)
            raise RuntimeError("crash after canonical transition")
    monkeypatch.setattr(loop, "_reconcile_whole_run_terminal_stage", crash)

    exit_code = tp_cli.cmd_loop(SimpleNamespace(
        workspace=workspace, loop_action="terminal", outcome="handoff",
        by=initial_stage["authority"]["actor"]))
    failed = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert "failed closed" in failed["error"]
    interrupted_state = loop.load(workspace)
    assert interrupted_state["step"] == initialized["step"] == "pm"
    intent_path, receipt_path = loop._whole_run_terminal_paths(
        workspace, interrupted_state)
    assert os.path.isfile(intent_path)
    durable_receipt = json.loads(Path(receipt_path).read_text(
        encoding="utf-8"))
    predecessor = durable_receipt["stage_predecessor"]
    assert predecessor["operation_id"].startswith("loop-transition-")

    interrupted_manifest = store.load(initialized["run_id"])
    interrupted_head = interrupted_manifest["stage_heads"][
        predecessor["stage_id"]]
    interrupted_stage = store.read_stage_object(
        initialized["run_id"], interrupted_head["object"])
    expected_before_replay = (
        "active" if crash_window == "receipt-before-stage-transition"
        else "terminal")
    assert interrupted_stage["state"] == expected_before_replay

    monkeypatch.setattr(loop, "_reconcile_whole_run_terminal_stage",
                        production_reconcile)
    recovered = loop.replay_terminal_intent(workspace)
    stored = loop.load(workspace)
    manifest = store.load(initialized["run_id"])
    head = manifest["stage_heads"][predecessor["stage_id"]]
    canonical = store.read_stage_object(
        initialized["run_id"], head["object"])

    assert "error" not in recovered
    assert recovered["fingerprint"] == durable_receipt["fingerprint"]
    assert stored["step"] == "failed"
    assert stored["whole_run_terminal"] == durable_receipt
    assert canonical["state"] == "terminal"
    assert canonical["outcome"] == "closed"
    assert manifest["active_stage_projection"]["active_stage_ids"] == []
    assert manifest["stage_operations"][predecessor[
        "operation_id"]]["operation_id"] == predecessor["operation_id"]

    replayed = loop.replay_terminal_intent(workspace)
    assert replayed["fingerprint"] == durable_receipt["fingerprint"]
    assert loop.load(workspace)["whole_run_terminal"] == durable_receipt
    replay_manifest = store.load(initialized["run_id"])
    assert replay_manifest["stage_heads"] == manifest["stage_heads"]
    assert replay_manifest["stage_operations"] == manifest[
        "stage_operations"]


def test_public_signoff_sets_attributable_metrics_truth_before_retro(
        tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "signoff"
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
        "step": "signoff", "run_id": "run-signoff", "goal": "seal truth",
        "baseline": loop.tp.git_head(str(workspace)), "tasks": [],
        "current_task": 0, "max_fix_cycles": 2,
        "checkpoints": ["plan", "em"],
        "signoff_evidence": {"schema": "test-signoff", "dod": {
            "passed": True, "errors": [], "notices": []}},
        "run_artifact_binding": {
            "candidate": {"fingerprint": "a" * 64}},
    }
    loop.save(str(workspace), state)
    monkeypatch.setattr(loop, "_signoff_dod", lambda *_a: {
        "passed": True, "errors": [], "notices": []})
    monkeypatch.setattr(loop, "_stage_loop_gate_completion",
                        lambda *_a, **_k: {})
    monkeypatch.setattr(loop, "_stage_loop_transition",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *_a, **_k: None)
    calls = []
    production_sealer = loop._seal_terminal_metrics_before_retro

    def seal(metrics_workspace, current):
        assert metrics_workspace == str(workspace)
        calls.append(current["step"])
        return production_sealer(metrics_workspace, current)

    monkeypatch.setattr(loop, "_seal_terminal_metrics_before_retro", seal)

    result = loop.approve.__wrapped__(str(workspace), by="night-mode")
    stored = loop.load(str(workspace))

    assert result["step"] == "retro"
    assert calls == ["signoff"]
    assert stored["step"] == "retro"
    assert stored["terminal_metrics"]["status"] == "unavailable"
    assert stored["wave_metrics_unavailable"]["schema"] == \
        "taskplane.wave-metrics-unavailable/v1"
    assert stored["wave_metrics_unavailable"]["candidate_fingerprint"] == \
        "a" * 64
    assert "wave_metrics_receipt" not in stored
    assert not ({"total_tokens", "uncached_input_tokens", "effective_tokens"}
                & set(stored["wave_metrics_unavailable"]))
