"""Public journey: dynamic Design workers execute under host authority."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from taskplane import run_artifacts, storage
from taskplane import taskplane_lite as tp


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _repository(tmp_path: Path, name: str = "repository") -> Path:
    workspace = tmp_path / name
    workspace.mkdir()
    (workspace / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Taskplane Test"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=workspace,
                   check=True)
    return workspace


def _worker(lens: str, *, run_id: str, stage_id: str,
            candidate: str, settings: str = "a" * 64) -> dict:
    role = tp.portable_role_reference("tp-lens")
    task_name = tp.dispatch_task_name(
        "lens", "tp-lens", f"design-{lens}")
    output = f"design/lenses/{lens}.json"
    intent = {
        "schema": tp.DESIGN_LENS_DISPATCH_INTENT_SCHEMA,
        "run_id": run_id,
        "stage_instance_id": stage_id,
        "candidate_fingerprint": candidate,
        "lens": lens,
        "task_name": task_name,
        "task_slot": f"design-lens-{lens}",
        "role_reference_fingerprint": role["fingerprint"],
        "model_tier": "deep", "model": None,
        "reasoning_effort": "high", "settings_digest": settings,
        "output": output,
    }
    return {
        "lens": lens, "task_name": task_name,
        "role_marker": tp.role_marker("tp-lens"),
        "role_reference": role, "model_tier": "deep", "model": None,
        "reasoning_effort": "high", "task_slot": f"design-lens-{lens}",
        "output": output,
        "contract": {"read_only": True, "write_allow": [output]},
        "brief": {"question": f"Review {lens}"},
        "dispatch_intent": {**intent, "fingerprint": _digest(intent)},
    }


def _plan(workers: list[dict], *, run_id: str, stage_id: str,
          candidate: str, settings: str) -> dict:
    selected = [worker["lens"] for worker in workers]
    material = {
        "schema": "taskplane.design-team-plan/v1",
        "run_id": run_id, "stage_instance_id": stage_id,
        "requirement": "R-0002", "requirement_fingerprint": "1" * 64,
        "candidate_fingerprint": candidate, "settings_digest": settings,
        "catalog_fingerprint": "2" * 64,
        "decomposition_fingerprint": "3" * 64,
        "route_fingerprint": "4" * 64,
        "selected": selected, "selected_count": len(selected),
        "workers": workers,
        "concurrency": {"mode": "parallel", "waves": [selected]},
        "status": "planned",
    }
    return {**material, "fingerprint": _digest(material)}


def _artifact_store(workspace: Path, plan: dict) -> tuple[Path, dict]:
    identity = storage.resolve_repository_identity(str(workspace))
    layout = storage.resolve_layout(identity, run_id=plan["run_id"])
    Path(layout.run_root).mkdir(parents=True, exist_ok=True)
    binding = run_artifacts.create_binding(
        repository_id=identity.repo_id, run_id=plan["run_id"],
        stage_id="design", stage_instance_id=plan["stage_instance_id"],
        candidate={"fingerprint": plan["candidate_fingerprint"]},
        settings_digest=plan["settings_digest"],
        source_fingerprint="5" * 64)
    run_artifacts.create_manifest(layout.artifact_root, binding=binding)
    return Path(layout.artifact_root), binding


def _complete_worker(workspace: Path, plan: dict, authority: dict,
                     worker: dict, *, index: int) -> None:
    lens = worker["lens"]
    expectation = tp.peek_expectation(
        str(workspace), worker["task_name"], strict=True)
    assert expectation is not None
    tp.record_design_dispatch_assignment_activity(
        str(workspace), expectation)
    assert tp.commit_dispatch_verification(
        str(workspace), worker["task_name"], worker["model"],
        expectation, True, worker["reasoning_effort"], strict=True)

    contract = tp.build_contract(
        f"DESIGN LENS: {lens}", read_only=True,
        write_allow=[worker["output"]], tools=["Read", "Write"])
    contract["task_id"] = worker["task_slot"]
    contract = tp.prepare_worker_contract(
        str(workspace), contract, stage="design-lens", task=lens,
        task_name=worker["task_name"], role_marker=worker["role_marker"])
    identity = storage.resolve_repository_identity(str(workspace))
    root = storage.resolve_layout(
        identity, run_id=plan["run_id"]).artifact_root
    manifest = run_artifacts.load_manifest(root)
    contract = tp.attach_design_lens_host_authority(
        contract, authority["workers"][lens], artifact_root=root,
        artifact_binding=manifest["binding"])
    tp.activate(str(workspace), contract, snapshot=tp.git_head(str(workspace)),
                task_slot_override=worker["task_slot"])
    event = {
        "cwd": str(workspace), "session_id": "session-1",
        "agent_id": f"agent-{index}", "agent_type": worker["task_name"],
        "task_name": worker["task_name"], "turn_id": f"turn-{index}",
    }
    binding = tp.bind_worker_contract_event(str(workspace), event)
    tp.record_design_worker_start_activity(str(workspace), binding, event)

    result_material = {
        "schema": "taskplane.design-lens-result/v1", "lens": lens,
        "worker_identity": worker["task_name"],
        "team_plan_fingerprint": plan["fingerprint"],
        "candidate_fingerprint": plan["candidate_fingerprint"],
        "outcome": "pass", "findings": [],
    }
    output = workspace / worker["output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        **result_material, "fingerprint": _digest(result_material),
    }), encoding="utf-8")
    tp.terminalize_worker_contract(
        str(workspace), {**event, "outcome": "success"},
        outcome="success", submission_status="not_required")


def test_exact_dynamic_design_set_uses_portable_roles_and_host_receipts(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "taskplane-home"))
    workspace = _repository(tmp_path)
    workers = [_worker(
        lens, run_id="run-design", stage_id="design-stage",
        candidate="c" * 64) for lens in ("solution-design", "security")]
    plan = _plan(
        workers, run_id="run-design", stage_id="design-stage",
        candidate="c" * 64, settings="a" * 64)
    root, binding = _artifact_store(workspace, plan)

    authority = tp.register_design_lens_dispatch_plan(
        str(workspace), plan, artifact_root=str(root),
        artifact_binding=binding, now=10)
    repeated = tp.register_design_lens_dispatch_plan(
        str(workspace), plan, artifact_root=str(root),
        artifact_binding=binding, now=999)

    assert repeated == authority
    assert all(not Path(worker["role_reference"]["path"]).is_absolute()
               for worker in workers)
    assert set(authority["workers"]) == set(plan["selected"])

    _complete_worker(workspace, plan, authority, workers[0], index=1)
    incomplete = tp.validate_design_lens_dispatch_completion(
        str(workspace), plan, authority)
    assert incomplete["valid"] is False
    assert any("security" in error for error in incomplete["errors"])

    _complete_worker(workspace, plan, authority, workers[1], index=2)
    complete = tp.validate_design_lens_dispatch_completion(
        str(workspace), plan, authority)
    assert complete["valid"] is True
    assert set(complete["workers"]) == set(plan["selected"])
    manifest = run_artifacts.load_manifest(root)
    event_types = {entry["metadata"]["event_type"] for entry in
                   manifest["classes"]["agent-activity"]["entries"]}
    assert {"assignment", "worker-identity", "start", "progress",
            "terminal"}.issubset(event_types)


def test_foreign_absolute_stale_and_replayed_role_authority_is_inert(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "taskplane-home"))
    workspace = _repository(tmp_path, "source")
    worker = _worker(
        "solution-design", run_id="run-design", stage_id="design-stage",
        candidate="c" * 64)
    plan = _plan(
        [worker], run_id="run-design", stage_id="design-stage",
        candidate="c" * 64, settings="a" * 64)
    root, binding = _artifact_store(workspace, plan)
    authority = tp.register_design_lens_dispatch_plan(
        str(workspace), plan, artifact_root=str(root),
        artifact_binding=binding, now=10)

    unsafe_worker = json.loads(json.dumps(worker))
    unsafe_worker["role_reference"]["path"] = str(
        workspace / "agents" / "tp-lens.md")
    unsafe_plan = _plan(
        [unsafe_worker], run_id="run-design", stage_id="design-stage",
        candidate="c" * 64, settings="a" * 64)
    with pytest.raises(ValueError, match="absolute, foreign, or unsafe"):
        tp.register_design_lens_dispatch_plan(
            str(workspace), unsafe_plan, artifact_root=str(root),
            artifact_binding=binding)

    foreign = _repository(tmp_path, "foreign")
    with pytest.raises(tp.StateError, match="authority is invalid"):
        tp.verify_worker_host_receipt(
            str(foreign), authority["workers"]["solution-design"][
                "assignment_receipt"], event="assignment", plan=plan,
            worker=worker)
