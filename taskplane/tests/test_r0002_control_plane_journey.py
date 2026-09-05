"""Public journey: Design selection becomes exact native host work."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from taskplane import design_host_transport, loop, run_artifacts


def test_design_worker_brief_carries_its_own_native_contract_and_result():
    worker = {
        "lens": "solution-design", "task_name": "native_design_worker",
        "task_slot": "design-lens-solution-design",
        "output": "design/lenses/solution-design.json",
        "contract": {"read_only": True,
                     "write_allow": ["design/lenses/solution-design.json"]},
    }
    plan = {"fingerprint": "a" * 64, "candidate_fingerprint": "b" * 64}
    brief = design_host_transport.design_worker_brief(plan, worker)

    assert brief["contract_bootstrap"] == {
        "schema": "taskplane.worker-contract-bootstrap/v1",
        "task_slot": worker["task_slot"],
        "worker_identity": worker["task_name"],
        "environment": {"TASKPLANE_TASK": worker["task_slot"]},
        "activation": "pending_subagent_start_binding",
    }
    assert "lease" not in brief  # native lifecycle already owns this child
    assert brief["result_path"] == worker["output"]
    assert brief["result_schema"]["$id"] == "taskplane.design-lens-result/v1"
    assert brief["result_template"]["team_plan_fingerprint"] == plan["fingerprint"]
    assert "outcome" not in brief["result_template"]  # never invent judgment
    material = {**brief["result_template"], "outcome": "pass", "findings": []}
    result = {**material, "fingerprint": hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")).hexdigest()}
    design_host_transport.validate_design_worker_result(plan, worker, result)
    for field in brief["result_schema"]["required"]:
        broken = {key: value for key, value in result.items() if key != field}
        with pytest.raises(ValueError, match="result"):
            design_host_transport.validate_design_worker_result(plan, worker, broken)
    with pytest.raises(ValueError, match="result"):
        design_host_transport.validate_design_worker_result(
            plan, worker, {**result, "schema": "taskplane.lens-slot-output/v2"})


def test_design_plan_metadata_round_trips_through_actual_consumer(tmp_path, monkeypatch):
    state = {"design_fingerprint": "c" * 64, "requirement_id": "R-0004"}
    descriptor = loop._plan_output_contract(state)
    target = tmp_path / descriptor["path"]
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(descriptor["template"]), encoding="utf-8")
    monkeypatch.setattr(loop.tp, "git_head", lambda _ws: "d" * 40)
    accepted = loop._plan_delivery_mode_from_file(str(tmp_path), state, apply=False)
    assert accepted["mode"] == "build"
    assert accepted["automatic_lenses"] == []
    assert accepted["plan_authority"] == "design:" + "c" * 64
    assert descriptor["approval_granted"] is False
    for field in ("delivery_mode", "automatic_lenses", "plan_authority"):
        broken = dict(descriptor["template"])
        del broken[field]
        target.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(loop.delivery_policy.DeliveryPolicyError):
            loop._plan_delivery_mode_from_file(str(tmp_path), state, apply=False)


def test_build_brief_preserves_exact_approved_obligations_without_runtime_state():
    task = {"id": "t1", "scope": ["src/a.py"], "tests": "pytest -q",
            "criteria": ["only A1"], "contracts": ["contract:a"],
            "acceptance_refs": ["A1"], "test_contract": {"selected": ["A1"]},
            "test_strategy_authority": {"path": "design/tests.json"},
            "status": "running", "workspace": "/private/predecessor",
            "lease": "private-lease"}
    brief = loop._build_task_brief(task)
    for field in ("criteria", "contracts", "acceptance_refs", "test_contract",
                  "test_strategy_authority"):
        assert brief[field] == task[field]
    assert not {"status", "workspace", "lease"}.intersection(brief)
    brief["criteria"].append("must not mutate the approved task")
    assert task["criteria"] == ["only A1"]
    completion = loop._build_completion_brief(task, parallel=True)
    assert completion["submit"] == ["loop", "submit", "pass", "--task", "t1"]
    assert completion["quality_admission"]["required_before_submit"] is True
    assert completion["quality_admission"]["strategy_reference"] == \
        task["test_strategy_authority"]


def test_fresh_design_creates_exact_runstore_owned_artifact_parent(
        tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private_store = tmp_path / "private-store"
    state = {
        "run_id": "loop-r0002-fresh", "baseline": "a" * 40,
        "goal": "fresh governed design",
    }
    monkeypatch.setattr(loop.runtime_storage, "load_workspace_locator",
                        lambda _ws: None)
    monkeypatch.setenv("TASKPLANE_HOME", str(private_store))
    monkeypatch.setattr(loop.tp, "git_head", lambda _ws: "a" * 40)

    root, binding, reference = loop._ensure_run_artifacts(
        str(workspace), state, settings_digest="b" * 64,
        stage_instance_id="design-stage-fresh",
        candidate_fingerprint="c" * 64, requirement_id="R-0002",
        requirement_fingerprint="d" * 64)

    owner = loop.run_store_engine.RunStore(
        home=str(private_store)).load(state["run_id"])
    assert Path(root) == private_store / "runs" / state["run_id"] / \
        "artifacts"
    assert owner["paths"]["artifacts"] == root
    assert owner["target"]["run_id"] == state["run_id"]
    assert owner["repository"]["checkout"] == str(workspace.resolve())
    assert binding["repository_id"] == owner["repository"]["repo_id"]
    assert run_artifacts.verify_manifest(
        root, expected_binding=binding)["zero_unindexed_files"] is True
    assert reference == run_artifacts.manifest_locator_reference()

    run_artifacts.publish_artifact(
        root, "dashboard", {"status": "ready"},
        metadata={"producer": "control-plane-journey"})

    replay = loop._ensure_run_artifacts(
        str(workspace), state, settings_digest="b" * 64,
        stage_instance_id="design-stage-fresh",
        candidate_fingerprint="c" * 64, requirement_id="R-0002",
        requirement_fingerprint="d" * 64)
    assert replay == (root, binding, reference)

    with pytest.raises(run_artifacts.RunArtifactError,
                       match="another current binding"):
        loop._ensure_run_artifacts(
            str(workspace), state, settings_digest="b" * 64,
            stage_instance_id="design-stage-fresh",
            candidate_fingerprint="e" * 64, requirement_id="R-0002",
            requirement_fingerprint="d" * 64)


def test_control_plane_refreshes_verification_but_rejects_tampered_identity(
        tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private_store = tmp_path / "private-store"
    state = {
        "run_id": "loop-r0002-refresh", "baseline": "a" * 40,
        "goal": "refresh governed artifacts", "step": "design",
    }
    monkeypatch.setattr(loop.runtime_storage, "load_workspace_locator",
                        lambda _ws: None)
    monkeypatch.setenv("TASKPLANE_HOME", str(private_store))
    monkeypatch.setattr(loop.tp, "git_head", lambda _ws: "a" * 40)

    prepared = loop._prepare_run_control_plane(str(workspace), state)
    root = private_store / "runs" / state["run_id"] / "artifacts"
    run_artifacts.publish_artifact(
        root, "dashboard", {"status": "ready"},
        metadata={"producer": "control-plane-refresh"})

    persisted = {**state, **prepared}
    assert loop._prepare_run_control_plane(str(workspace), persisted) == \
        prepared

    tampered_locator = {
        **persisted,
        "run_artifacts": {
            **persisted["run_artifacts"],
            "verification_fingerprint": "0" * 64,
        },
    }
    with pytest.raises(run_artifacts.RunArtifactError,
                       match="changed at run_artifacts"):
        loop._prepare_run_control_plane(str(workspace), tampered_locator)

    tampered_binding = {
        **persisted,
        "run_artifact_binding": {
            **persisted["run_artifact_binding"],
            "fingerprint": "0" * 64,
        },
    }
    with pytest.raises(run_artifacts.RunArtifactError,
                       match="run artifact binding fingerprint is stale"):
        loop._prepare_run_control_plane(str(workspace), tampered_binding)


def test_dynamic_design_team_creates_one_portable_authorized_worker_per_lens(
        tmp_path, monkeypatch) -> None:
    activated = []
    registered = []
    role = {
        "schema": "taskplane.role-reference/v1",
        "path": "agents/tp-lens.md", "bytes": 12,
        "sha256": "a" * 64, "fingerprint": "b" * 64,
    }
    binding = {
        "run_id": "run-r0002", "stage_instance_id": "design-stage-1",
        "requirement": "R-0002", "requirement_fingerprint": "c" * 64,
        "candidate_fingerprint": "d" * 64,
        "settings_digest": "e" * 64, "catalog_fingerprint": "f" * 64,
        "decomposition_fingerprint": "1" * 64,
    }
    state = {
        "design_control_plane_binding": binding,
        "run_artifact_binding": {"fingerprint": "2" * 64},
    }
    monkeypatch.setattr(loop.lens_router, "load_catalog", lambda: {
        "lenses": [{"id": "security"}, {"id": "accessibility"}]})
    monkeypatch.setattr(
        loop.lens_router, "lens_brief",
        lambda lens, _catalog: {"lens": lens, "tier": "quick"})
    monkeypatch.setattr(loop.tp, "portable_role_reference", lambda _role: role)
    def dispatch_fields(*args, **kwargs):
        settings = kwargs["settings_context"]
        design = settings.stages["design"]
        return {
            "task_name": loop.tp.dispatch_task_name(
                args[0], args[1], args[2], namespace=kwargs.get("namespace")),
            "role_marker": "role:tp-lens", "model_tier": args[3],
            "model": design.model, "reasoning_effort": design.reasoning,
            "settings_digest": settings.digest,
        }

    monkeypatch.setattr(loop.tp, "dispatch_fields", dispatch_fields)
    monkeypatch.setattr(loop, "_run_artifact_root", lambda *_a: str(tmp_path))
    monkeypatch.setattr(loop.tp, "prepare_worker_contract", lambda _ws, c, **_k: {
        **c, "task_slot": c["task_id"], "worker_scoped": True,
        "worker_lifecycle": {"status": "pending"},
    })
    monkeypatch.setattr(loop.tp, "attach_design_lens_host_authority",
                        lambda contract, *_a, **_k: contract)
    monkeypatch.setattr(loop.tp, "activate",
                        lambda _ws, contract, **_k: activated.append(contract))

    def register(_ws, plan, **kwargs):
        registered.append((plan, kwargs))
        return {
            "schema": "taskplane.design-lens-host-authority/v1",
            "team_plan_fingerprint": plan["fingerprint"],
            "workers": {
                worker["lens"]: {
                    "task_name": worker["task_name"],
                    "task_slot": worker["task_slot"],
                    "assignment_receipt": {"signature": "host"},
                } for worker in plan["workers"]
            },
        }

    monkeypatch.setattr(loop.tp, "register_design_lens_dispatch_plan", register)
    route = {
        "dispatchable_selected": ["security", "accessibility"],
        "route_fingerprint": hashlib.sha256(b"route").hexdigest(),
    }

    owner = loop.tp.dispatch_fields(
        "step", "tp-designer", "design", "deep",
        namespace="run-r0002", settings_context=loop.operational_settings.load_settings())
    evidence = {"approved_requirement": {"id": "R-0002"},
                "acceptance": ["native worker receives its task input"]}
    plan = loop._design_team_plan(
        str(tmp_path), state, route, owner, stage_evidence=evidence)
    assert plan["stage_evidence"] == evidence
    brief = loop.tp.design_worker_brief(plan, plan["workers"][0])
    assert brief["stage_evidence"] == evidence
    brief["stage_evidence"]["acceptance"].append("detached")
    assert plan["stage_evidence"] == evidence

    assert plan["selected"] == ["security", "accessibility"]
    assert len(activated) == len(registered[0][0]["workers"]) == 2
    assert all(worker["role_reference"] == role for worker in plan["workers"])
    assert all("role_instructions" not in worker for worker in plan["workers"])
    assert all(worker["dispatch_intent"]["task_slot"] == worker["task_slot"]
               for worker in plan["workers"])
    effective = loop.operational_settings.load_settings()
    assert all(worker["model"] == effective.stages["design"].model and
               worker["reasoning_effort"] ==
               effective.stages["design"].reasoning and
               worker["dispatch_intent"]["settings_digest"] ==
               effective.digest for worker in plan["workers"])
    assert all(contract["task_id"].startswith("design-lens-")
               for contract in activated)
    assert registered[0][1]["artifact_binding"] == \
        state["run_artifact_binding"]

    replay = loop._design_team_plan(
        str(tmp_path), {**state, "design_team_plan": plan}, route, owner,
        stage_evidence=evidence)
    assert replay == plan
    assert len(registered) == 1
    retry = loop._design_team_plan(
        str(tmp_path), state, route, {"task_name": owner["task_name"] + "_retry"},
        stage_evidence=evidence)
    assert {w["task_name"] for w in plan["workers"]}.isdisjoint(
        w["task_name"] for w in retry["workers"])
    assert plan["dispatch_namespace"] == owner["task_name"]
