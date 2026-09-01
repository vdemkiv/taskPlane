"""Public journey: Design selection becomes exact native host work."""
from __future__ import annotations

import hashlib
from pathlib import Path

from taskplane import loop, run_artifacts


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
    assert reference["binding_fingerprint"] == binding["fingerprint"]

    replay = loop._ensure_run_artifacts(
        str(workspace), state, settings_digest="b" * 64,
        stage_instance_id="design-stage-fresh",
        candidate_fingerprint="c" * 64, requirement_id="R-0002",
        requirement_fingerprint="d" * 64)
    assert replay == (root, binding, reference)


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
                args[0], args[1], args[2]),
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

    plan = loop._design_team_plan(str(tmp_path), state, route, {})

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
