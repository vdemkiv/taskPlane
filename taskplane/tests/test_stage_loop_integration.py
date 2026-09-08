"""The legacy loop opts into bounded v4 stage dispatch explicitly."""
from __future__ import annotations

import copy
import contextlib
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from taskplane import loop, taskplane_lite
from taskplane.settings import load_settings
from tests.root_session_fixture import open_delivery_root


def _content_inventory(root: Path) -> dict[str, str]:
    """Bind a no-mutation assertion to names and semantic file content."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(
            path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _workspace(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "README.md").write_text("stage loop\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
    subprocess.run([
        "git", "-c", "user.name=Taskplane", "-c",
        "user.email=taskplane@example.invalid", "commit", "-qm", "base",
    ], cwd=workspace, check=True)
    return str(workspace)


def test_stage_native_first_worker_identity_is_run_scoped_and_replay_stable(tmp_path, monkeypatch):
    """Real init/next producers; simulated host metadata, no native J1 claim."""
    from taskplane import requirements, review_evidence, run_store, stage_migration, storage
    from taskplane.tests.test_r0001_phase_agents_spec import _registry

    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "same-host-session")
    names = []
    for ordinal in (1, 2):
        root = tmp_path / str(ordinal)
        root.mkdir()
        ws = _workspace(root)
        source = (Path(ws) / "README.md").read_bytes()
        store = run_store.RunStore()
        identity = storage.resolve_repository_identity(ws)
        initial = store.create(identity, run_id=f"run-product-{ordinal}", checkout=ws,
            host={"kind":"codex", "session_id":"same-host-session"},
            target={"kind":"workspace", "revision":loop.tp.git_head(ws)})
        run_id = initial["run_id"]
        storage.write_workspace_locator(ws, identity=identity,
            layout=storage.resolve_layout(identity, home=store.home, run_id=run_id), run_id=run_id)
        requirement = requirements.record_requirement(ws, "Same Product goal",
            functional=["preserve the native attempt identity"],
            acceptance=["two runs cannot collide in one retained host task tree"])
        initialized = loop.init(ws, "Same Product goal", requirement_id=requirement["id"], by="human:simulated")
        assert not initialized.get("error"), initialized
        authority = initialized["_stage_native_root_authority"]
        artifacts = review_evidence.ArtifactStore(ws)
        def authorize(current):
            assert current["run_id"] == run_id
            assert loop._stage_native_init_authority(ws, requirement["id"], "human:simulated") == authority
        stage_migration.change_phase_routing(store, run_id, owner="agent-runtime",
            configuration={"definition_source":"agents/spec-phase-definitions.json",
                "definition_set_fingerprint":_registry().definition_set_fingerprint,
                "knowledge_reference":artifacts.put("phase-knowledge", {"facts":[]}),
                "candidate_fingerprint":review_evidence.content_fingerprint({"revision":authority["target_revision"]}),
                "target_revision":authority["target_revision"], "host_kind":"simulated",
                "host_version":"supporting-test", "output_paths":{"product":{"requirement":"specs/requirement.json"}}},
            expected_previous=None, expected_revision=store.load(run_id)["revision"],
            operation_id="select-product-runtime", validate_authority=authorize)
        first = loop.next_action(ws)
        assert not first.get("error"), first
        names.append(first["task_name"])
        state = loop.load(ws)
        manifest = store.load(run_id)
        slot = Path(loop.tp.active_contract_path(ws, first["contract_bootstrap"]["task_slot"]))
        contract_bytes = slot.read_bytes()
        repeated = loop.next_action(ws)
        assert repeated["phase_runtime"]["reference"] == first["phase_runtime"]["reference"]
        assert repeated["phase_runtime"]["operation_id"] == first["phase_runtime"]["operation_id"]
        assert "task_name" not in repeated  # Pickup is observation, not another launch.
        assert loop.load(ws)["worker_dispatch_sequences"] == state["worker_dispatch_sequences"] == {"pm:pm":1}
        assert slot.read_bytes() == contract_bytes
        assert json.loads(contract_bytes)["worker_lifecycle"]["expected_task_name"] == first["task_name"]
        assert store.load(run_id) == manifest
        assert (Path(ws) / "README.md").read_bytes() == source
    assert names[0] != names[1], "fresh runs reused the same host-global first Product name"


def _initialize_real_new_run(tmp_path, monkeypatch, *, stage_kind="product",
                             stage_id=None,
                             goal="exercise the real stage loop",
                             parallel=False):
    from taskplane import requirements as reqs
    from taskplane.tests.test_stage_cross_host import (
        _real_loop_stage, _record_bootstrap_requirement)

    tmp_path.mkdir(parents=True, exist_ok=True)
    stage_id = stage_id or f"stage-{stage_kind}-loop-root"
    workspace, store, stage = _real_loop_stage(
        tmp_path, stage_kind=stage_kind, stage_id=stage_id)
    requirement = _record_bootstrap_requirement(workspace, ordinal=4)
    requirement = reqs.amend_requirement(
        str(workspace), requirement["id"],
        nfr={
            "reliability": "stage transitions fail closed without evidence",
            "security": "stage output stays inside governed storage",
            "architecture": "the immutable stage aggregate owns transitions",
        })
    assert requirement["id"] == stage["requirement"]["id"] == "R-0004"
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "cross-host-session")
    initialized = loop.init(
        str(workspace), goal, requirement_id=requirement["id"],
        parallel=parallel, by=stage["authority"]["actor"])
    assert "error" not in initialized, initialized
    return str(workspace), store, stage, initialized


@pytest.fixture
def collected_product_handoff(tmp_path, monkeypatch):
    """Actual producers/collector on a small Git target; host events simulated."""
    from taskplane import review_evidence, stage_migration
    from taskplane.tests import test_stage_cross_host as cross_host
    from taskplane.tests.test_r0001_j1_native import _supporting_pristine_phase_run
    from taskplane.tests.test_r0001_phase_cutover import _emit_host_hook, _host_event
    git = cross_host._git
    def with_python_input(workspace, *args):
        if args == ("add", "."):
            (workspace / "app.py").write_text("def greet(name):\n    return f'Hello {name}'\n")
        return git(workspace, *args)
    monkeypatch.setattr(cross_host, "_git", with_python_input)
    route = stage_migration.change_phase_routing
    def with_design_destination(*args, **kwargs):
        configuration = copy.deepcopy(kwargs["configuration"])
        configuration["output_paths"]["design"] = {
            "design": "design/contract.json", "test-strategy": "design/test-strategy.json"}
        return route(*args, **{**kwargs, "configuration": configuration})
    monkeypatch.setattr(stage_migration, "change_phase_routing", with_design_destination)
    record = cross_host._record_bootstrap_requirement
    def with_context(workspace):
        requirement = record(workspace)
        requirement = loop.reqs.amend_requirement(str(workspace), requirement["id"],
            context_files=["app.py"], nfr={"security":"retain exact authority",
                "architecture":"reuse existing owners", "reliability":"preserve accepted evidence"})
        loop.depgraph.link_requirement(str(workspace), requirement["id"], ["app.py"], kind="planned", replace=True)
        return requirement
    monkeypatch.setattr(cross_host, "_record_bootstrap_requirement", with_context)
    ws, store, run_id, requirement = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    requested = loop.next_action(ws)
    assert not requested.get("error"), requested
    assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
    path = Path(ws) / requested["phase_runtime"]["outputs"]["requirement"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema":"taskplane.requirement/v1", "id":requirement["id"],
        "title":requirement["title"], "acceptance_criteria":requirement["acceptance"]}))
    stop = _host_event(ws, requested, "SubagentStop")
    slot = requested["contract_bootstrap"]["task_slot"]
    contract = loop.tp.load_json(loop.tp.active_contract_path(ws, slot))
    collected = loop.observe_phase_runtime_hook(ws, contract, stop)
    assert collected["status"] == "collected", collected
    loop.tp.terminalize_worker_contract(ws, stop, outcome="success", submission_status="not_required")
    completion = loop.next_action(ws)["phase_runtime"]["completion"]
    assert completion is not None
    artifacts = review_evidence.ArtifactStore(ws)
    material = artifacts.read(completion["preparation"])
    return ws, store, run_id, completion, material, artifacts


def test_collected_product_gate_preserves_signed_handoff_after_graph_refresh(collected_product_handoff, monkeypatch):
    import types
    ws, store, run_id, completion, material, artifacts = collected_product_handoff
    original = {key:artifacts.read(completion[key]) for key in ("preparation", "runtime_result", "runtime_receipt")}
    impact = artifacts.read(material["impact_reference"])
    # Only the producer's metadata clock advances; the public gate repeats the
    # same requirement edge update between its two existing verifier calls.
    monkeypatch.setattr(loop.depgraph, "time", types.SimpleNamespace(time=lambda: impact["graph"]["updated_at"] + 10))
    gated = loop.gate(ws, "pass")
    assert not gated.get("error"), gated
    assert gated["step"] == "design"
    assert {key:artifacts.read(completion[key]) for key in original} == original
    from taskplane import stage_migration
    retained = stage_migration.phase_records(store.load(run_id))
    assert any(row["operation"] == "phase_collect" and row["result"] == completion for row in retained.values())


def test_collected_product_uses_selected_successor_before_legacy_design_policy(collected_product_handoff):
    ws, _, _, completion, _, artifacts = collected_product_handoff
    assert loop.load(ws)["design_required"] is False
    gated = loop.gate(ws, "pass")
    assert not gated.get("error"), gated
    assert gated["step"] == "design"
    assert loop.load(ws)["design_required"] is True
    requested = loop.next_action(ws)
    assert not requested.get("error"), requested
    assert requested["role"] == "tp-design"
    assert requested["phase_runtime"]["status"] == "pending"
    material = artifacts.read(requested["phase_runtime"]["reference"])
    assert artifacts.read(material["predecessor"]) == artifacts.read(completion["handoff"])
    assert [row["artifact_class"] for row in material["package"]] == ["requirement"]
    refused = loop.gate(ws, "pass")
    assert "matching terminal and collected output" in refused["error"]
    assert loop.load(ws)["step"] == "design"


@pytest.fixture
def historical_product_plan_correction(collected_product_handoff, monkeypatch):
    from taskplane import stage_entities
    ws, store, run_id, completion, _, artifacts = collected_product_handoff
    context = loop._phase_bridge_context
    def historical_optional_design(*args, **kwargs):
        value = context(*args, **kwargs)
        if value and value["stage"]["stage_kind"] == "product":
            # Reproduce the old optional-Design successor choice only. The
            # admitted registry and signed Product artifacts remain original.
            value = {**value, "definition": {**value["definition"],
                "edge_conditions": [{"condition": "accepted", "successor": "plan"}]}}
        return value
    with monkeypatch.context() as patch:
        patch.setattr(loop, "_phase_bridge_context", historical_optional_design)
        assert loop.gate(ws, "pass")["step"] == "plan"
    refused = loop.next_action(ws)
    assert "not a declared predecessor" in refused["error"]
    current = store.load(run_id)
    plan = context(ws, loop.load(ws))["stage"]
    product = loop._indexed_stage(store, current, run_id, plan["predecessor_stage_ids"][0])
    closed = loop.stage_command(ws, "terminalize", {
        "schema": "taskplane.stage-command/v1", "run_id": run_id,
        "stage_id": plan["stage_id"], "expected_head_fingerprint": plan["fingerprint"],
        "expected_revision": current["revision"], "operation_id": "close-incorrect-plan",
        "outcome": "closed", "actor": plan["authority"]["actor"],
        "terminalized_at": plan["created_at"], "reason_code": "incorrect_successor",
        "reason": "Historical optional-Design routing skipped required predecessor",
        "authority": plan["authority"]})
    assert not closed.get("error"), closed
    design = stage_entities.create_stage(run_id=run_id, stage_id="correct-design",
        requirement=product["requirement"], design=product["design"], stage_kind="design",
        parent_stage_ids=[], predecessor_stage_ids=[product["stage_id"]],
        input_manifest_ref=plan["input_manifest_ref"], execution_root_id="execution-correct-design",
        deliverables=["design-contract"], budget=product["budget"], dependencies=[],
        contracts=product["contracts"], authority=product["authority"], created_at=plan["created_at"],
        selected_artifacts=artifacts.read(completion["handoff"])["selected_artifacts"])
    request = {"schema": "taskplane.stage-command/v1", "stage": design,
        "expected_revision": store.load(run_id)["revision"], "operation_id": "start-correct-design",
        "expected_predecessor_fingerprints": {product["stage_id"]: product["fingerprint"]},
        "foreground": True, "authority": design["authority"]}
    return ws, store, run_id, completion, artifacts, request


@pytest.mark.parametrize("interrupted", [False, True], ids=["normal", "after-stage-commit"])
def test_public_stage_start_projects_corrected_design_and_replays(historical_product_plan_correction, monkeypatch, interrupted):
    ws, store, run_id, completion, artifacts, request = historical_product_plan_correction
    original = {key: artifacts.read(completion[key]) for key in
                ("preparation", "runtime_result", "runtime_receipt", "handoff")}
    if interrupted:
        def missed_projection(*args, **kwargs):
            raise OSError("interrupted singleton projection")
        with monkeypatch.context() as patch:
            patch.setattr(loop, "save", missed_projection)
            refused = loop.stage_command(ws, "start", request)
        assert "interrupted singleton projection" in refused["error"]
        assert loop.load(ws)["step"] == "plan"
        committed = store.load(run_id)
        assert "correct-design" in committed["stage_heads"]
    started = loop.stage_command(ws, "start", request)
    assert not started.get("error"), started
    if interrupted:
        assert store.load(run_id) == committed
    assert loop.load(ws)["step"] == "design"
    assert loop.load(ws)["design_required"] is True
    before = store.load(run_id)
    assert loop.stage_command(ws, "start", request) == started
    assert store.load(run_id) == before
    requested = loop.next_action(ws)
    assert not requested.get("error"), requested
    assert requested["role"] == "tp-design"
    assert requested["phase_runtime"]["status"] == "pending"
    assert {key: artifacts.read(completion[key]) for key in original} == original


@pytest.mark.parametrize("case", ["background", "stale-revision", "foreign-authority", "worker", "stale-foreground"])
def test_public_stage_start_projection_refuses_unrelated_changes(historical_product_plan_correction, monkeypatch, case):
    ws, store, run_id, _, _, request = historical_product_plan_correction
    before = copy.deepcopy(loop.load(ws))
    if case == "background":
        request["foreground"] = False
    elif case == "stale-revision":
        request["expected_revision"] -= 1
    elif case == "foreign-authority":
        request["authority"] = {**request["authority"], "actor": "human:foreign"}
    elif case == "worker":
        monkeypatch.setattr(loop.tp, "_active_worker_contracts", lambda ws: [("foreign-slot", {})])
    else:
        project = loop._project_bound_stage_start
        def close_before_projection(ws, store, lifecycle, stage, **kwargs):
            if kwargs.get("receipt") is None:
                return project(ws, store, lifecycle, stage, **kwargs)
            closed = loop.stage_command(ws, "terminalize", {
                "schema": "taskplane.stage-command/v1", "run_id": run_id,
                "stage_id": stage["stage_id"], "expected_head_fingerprint": stage["fingerprint"],
                "expected_revision": store.load(run_id)["revision"], "operation_id": "close-before-projection",
                "outcome": "closed", "actor": stage["authority"]["actor"],
                "terminalized_at": stage["created_at"], "reason_code": "superseded",
                "reason": "Current foreground changed before singleton projection",
                "authority": stage["authority"]})
            assert not closed.get("error"), closed
            return project(ws, store, lifecycle, stage, **kwargs)
        monkeypatch.setattr(loop, "_project_bound_stage_start", close_before_projection)
    manifest_before = store.load(run_id)
    result = loop.stage_command(ws, "start", request)
    if case == "background":
        assert not result.get("error"), result
    else:
        assert result.get("error"), result
    if case in {"stale-revision", "foreign-authority", "worker"}:
        assert store.load(run_id) == manifest_before
    assert loop.load(ws) == before


@pytest.mark.parametrize("changed", ["source", "graph-content", "graph-quality", "impact", "policy", "binding", "recorded-impact", "signature"])
def test_product_handoff_freshness_still_rejects_content_changes(collected_product_handoff, monkeypatch, changed):
    ws, _, _, completion, material, artifacts = collected_product_handoff
    signed = artifacts.read(completion["runtime_receipt"])
    original_material = copy.deepcopy(material)
    if changed == "source":
        (Path(ws) / "README.md").write_text("Changed implementation source\n")
        subprocess.run(["git", "add", "README.md"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-qm", "changed source"], cwd=ws, check=True)
    elif changed in {"graph-content", "graph-quality", "impact", "policy"}:
        producer = loop.depgraph.impact
        def changed_impact(*args, **kwargs):
            value = copy.deepcopy(producer(*args, **kwargs))
            if changed == "graph-content": value["graph"]["content_fingerprint"] = "f" * 64
            elif changed == "graph-quality": value["graph"].setdefault("graph_scan_quality", {})["degraded"] = True
            elif changed == "impact": value["total_impacted"] += 1
            else: value["policy"]["local_depth"] += 1
            return value
        monkeypatch.setattr(loop.depgraph, "impact", changed_impact)
    elif changed == "binding":
        material["bindings"]["candidate_fingerprint"] = "f" * 64
    elif changed == "recorded-impact":
        altered = artifacts.read(material["impact_reference"])
        altered["total_impacted"] += 1
        material["impact_reference"] = artifacts.put("phase-impact", altered)
    else:
        signed["signature"] = "corrupt"
    with pytest.raises(ValueError):
        loop._phase_bridge_signing(ws, material).verify(signed, store=artifacts)
    assert artifacts.read(completion["preparation"]) == original_material


def _start_real_stage_loop(tmp_path, monkeypatch, *, stage_kind="product",
                           stage_id=None, goal="exercise the real stage loop"):
    workspace, store, stage, _initialized = _initialize_real_new_run(
        tmp_path, monkeypatch, stage_kind=stage_kind, stage_id=stage_id,
        goal=goal)
    started = loop.stage_command(str(workspace), "start", {
        "schema": "taskplane.stage-command/v1",
        "stage": stage,
        "expected_revision": 1,
        "operation_id": f"start-{stage['stage_id']}",
        "expected_predecessor_fingerprints": {},
        "foreground": True,
        "authority": stage["authority"],
        "declared_scope": {
            "scope_paths": ["README.md"],
            "out_of_scope_paths": ["taskplane/loop.py"],
        },
    })
    assert "error" not in started, started
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "enabled")
    return str(workspace), store, stage, started


def _successor_handoff(ws, store, receipt):
    successor_head = receipt["result"]["successor_head"]
    successor_id = successor_head["summary"]["stage_id"]
    manifest = store.load(successor_head["summary"]["run_id"])
    successor = store.read_stage_object(
        manifest["run_id"], manifest["stage_heads"][successor_id]["object"])
    _entities, lifecycle = loop._stage_lifecycle(
        ws, store, manifest, successor["authority"])
    handoff = loop._verified_stage_handoff(
        lifecycle, store, manifest, successor)
    return successor, handoff


def _handoff_payload_text(ws, handoff):
    from taskplane import review_evidence

    artifact_store = review_evidence.ArtifactStore(ws)
    payloads = [artifact_store.read(reference) for reference in
                handoff["selected_artifacts"] +
                handoff["evidence_references"]]
    return json.dumps(payloads, sort_keys=True)


def test_disabled_loop_stage_context_does_not_open_a_locator(
        monkeypatch) -> None:
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "disabled")
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: (_ for _ in ()).throw(AssertionError("locator opened")))

    assert loop._stage_loop_context("/repo") is None


def test_unmigrated_loop_stage_context_is_a_read_only_noop(monkeypatch) -> None:
    manifest = {"schema": "taskplane.run/v3", "run_id": "legacy", "revision": 9}
    before = copy.deepcopy(manifest)

    class Store:
        def load(self, _run_id):
            return manifest

    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "enabled")
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: {"run_id": "legacy", "home": "/run-store"})
    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: Store())

    assert loop._stage_loop_context("/repo") is None
    assert manifest == before


def test_disabled_migrated_loop_refuses_mutation_without_state_change(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state_root = tmp_path / "state-root"
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    loop.init(ws, "preserve the migrated run")
    specs = tmp_path / "repo" / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text("migrated run\n", encoding="utf-8")
    before = copy.deepcopy(loop.load(ws))
    manifest = {
        "schema": "taskplane.run/v4", "run_id": "run-r0004",
        "revision": 4, "stage_heads": {}, "stage_operations": {},
        "active_stage_projection": {
            "active_stage_ids": [], "foreground_stage_id": None,
        },
    }

    class Store:
        @staticmethod
        def load(run_id):
            assert run_id == "run-r0004"
            return copy.deepcopy(manifest)

    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: {"run_id": "run-r0004", "home": "/run-store"})
    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: Store())

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "error" in refused
    assert "disabled" in refused["error"]
    assert loop.load(ws) == before


@pytest.mark.parametrize("corruption", ["locator", "store"])
def test_corrupt_stage_locator_or_store_refuses_without_singleton_mutation(
        tmp_path, monkeypatch, corruption) -> None:
    ws = _workspace(tmp_path)
    state_root = tmp_path / "corrupt-state-root"
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    loop.init(ws, "preserve singleton on stage metadata corruption")
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nDo not mutate through corrupt stage state.\n",
        encoding="utf-8")
    before = copy.deepcopy(loop.load(ws))
    state_path = Path(loop._loop_path(ws))
    before_bytes = state_path.read_bytes()
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)

    if corruption == "locator":
        monkeypatch.setattr(
            loop.runtime_storage, "load_workspace_locator",
            lambda _ws: (_ for _ in ()).throw(
                RuntimeError("corrupt stage locator")))
    else:
        monkeypatch.setattr(
            loop.runtime_storage, "load_workspace_locator",
            lambda _ws: {"run_id": "run-corrupt", "home": "/run-store"})

        class CorruptStore:
            @staticmethod
            def load(_run_id):
                raise RuntimeError("corrupt stage manifest")

        monkeypatch.setattr(
            loop, "_stage_store", lambda *_args: CorruptStore())

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "error" in refused
    assert "stage" in refused["error"].lower()
    assert "corrupt" in refused["error"].lower() or \
        "unavailable" in refused["error"].lower()
    assert loop.load(ws) == before
    assert state_path.read_bytes() == before_bytes


def test_new_run_can_start_after_normal_loop_init_and_replay_once(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, first = _start_real_stage_loop(
        tmp_path / "explicit-replay", monkeypatch,
        stage_id="stage-product-explicit-replay")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    request = {
        "schema": "taskplane.stage-command/v1", "stage": stage,
        "expected_revision": 1,
        "operation_id": "start-stage-product-explicit-replay",
        "expected_predecessor_fingerprints": {}, "foreground": True,
        "authority": stage["authority"],
        "declared_scope": {
            "scope_paths": ["README.md"],
            "out_of_scope_paths": ["taskplane/loop.py"],
        },
    }
    second = loop.stage_command(ws, "start", request)

    assert second == first
    manifest = store.load(stage["run_id"])
    assert list(manifest["stage_operations"]).count(
        "start-stage-product-explicit-replay") == 1


def test_pristine_new_run_next_bootstraps_once_before_public_dispatch(
        tmp_path, monkeypatch) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    worker_clock = iter(range(1_800_000_000, 1_800_001_000))
    monkeypatch.setattr(
        taskplane_lite._time, "time", lambda: next(worker_clock))
    observed = []
    real_dispatch = loop._stage_dispatch

    def observe_committed_root(store, lifecycle, receipt, stage, **kwargs):
        manifest = store.load(stage["run_id"])
        summary = manifest["stage_heads"][stage["stage_id"]]["summary"]
        assert manifest["schema"] == "taskplane.run/v4"
        assert summary["state"] == "active"
        assert manifest["stage_operations"][receipt["operation_id"]] == receipt
        observed.append(stage["stage_id"])
        return real_dispatch(
            store, lifecycle, receipt, stage, **kwargs)

    monkeypatch.setattr(loop, "_stage_dispatch", observe_committed_root)
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    workspace, store, initial = _real_pristine_run(pristine)
    requirement = _record_bootstrap_requirement(workspace)
    ws = str(workspace)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    initialized = loop.init(
        ws, "bootstrap Product without caller stage JSON", parallel=True,
        requirement_id=requirement["id"], by="human:vdemkiv")
    assert "error" not in initialized, initialized
    specs = workspace / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nBootstrap Product before any stage dispatch.\n",
        encoding="utf-8")
    refused_wave = loop.wave(ws)

    assert "first `loop next`" in refused_wave["error"]
    assert store.load(initial["run_id"])["schema"] == "taskplane.run/v3"
    assert initialized["_stage_native_new_run_pristine"] is True
    assert initialized["_stage_native_root_authority"]["actor"] == \
        "human:vdemkiv"

    first = loop.next_action.__wrapped__(ws)

    assert "error" not in first, first
    dispatch = first["stage_runtime_dispatch"]
    root_id = dispatch["startup"]["stage_id"]
    assert observed == [root_id]
    manifest = store.load(initial["run_id"])
    assert manifest["schema"] == "taskplane.run/v4"
    assert manifest["stage_heads"][root_id]["summary"]["state"] == "active"
    assert loop.load(ws)["_stage_run_binding"]["root_stage_id"] == root_id

    second = loop.next_action.__wrapped__(ws)

    assert "error" not in second, second
    assert second["stage_runtime_dispatch"]["startup"]["stage_id"] == root_id
    assert store.load(initial["run_id"])["active_stage_projection"][
        "active_stage_ids"] == [root_id]


def test_cold_plan_next_ignores_unselected_foreign_repo_inputs_and_replays_root(tmp_path, monkeypatch):
    from taskplane.tests.test_stage_cross_host import _real_pristine_run, _record_bootstrap_requirement
    workspace, store, initial = _real_pristine_run(tmp_path)
    foreign = {
        "design/contract.json":{"requirement":"R-0002", "summary":"foreign database migration"},
        "plan/tasks.json":{"requirement":"R-0002", "tasks":[{"id":"foreign", "scope":["foreign.py"],
            "tests":"foreign suite"}], "plan_route":{"selected":["architecture", "security", "testability", "cost-finops"]}},
    }
    for relative, value in foreign.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    subprocess.run(["git", "add", "design/contract.json", "plan/tasks.json"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "unrelated checked-in predecessor artifacts"], cwd=workspace, check=True)
    preserved = {relative:(workspace / relative).read_bytes() for relative in foreign}
    requirement = _record_bootstrap_requirement(workspace)
    requirement = loop.reqs.amend_requirement(str(workspace), requirement["id"], nfr={
        "security":"local source only", "architecture":"reuse existing owners"})
    assert loop.reqs.product_dor(requirement)["passed"]
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    (workspace / "current-spec.md").write_text("Current bounded requirement, not the checked-in predecessor Plan.")
    initialized = loop.init(str(workspace), "current bounded Plan", spec_path="current-spec.md",
        requirement_id=requirement["id"], by="human:vdemkiv")
    assert initialized["step"] == "plan", initialized
    # Stop at the mapper boundary after real root/handoff admission. No native
    # worker or fake applicability/acceptance receipt is needed for this test.
    observed = []
    def mapper(*args, **kwargs):
        observed.append(kwargs)
        raise ValueError("test stopped at current Plan mapper boundary")
    monkeypatch.setattr(loop, "_focused_stage_route", mapper)
    for _ in range(2):
        result = loop.next_action.__wrapped__(str(workspace))
        assert "current Plan mapper boundary" in result.get("error", ""), result
    assert len(observed) == 2
    for row in observed:
        assert row["stage"] == "plan"
        assert row["mandatory_lenses"] == ["architecture", "project-management", "testability"]
        assert row["maximum_lenses"] == 4
        assert row["evidence"]["approved_design"] == {}
        assert row["evidence"]["task_scopes"] == row["evidence"]["selectors"] == {}
        assert row["evidence"]["approved_product"]["id"] == requirement["id"]
        assert "foreign" not in json.dumps(row["evidence"])
    manifest = store.load(initial["run_id"])
    assert len(manifest["stage_heads"]) == 1
    context = loop._stage_loop_context(str(workspace), loop.load(str(workspace)))
    handoff = loop._verified_stage_handoff(context["lifecycle"], store, manifest, context["stage"])
    assert handoff["design"] is None and handoff["requirement"]["id"] == requirement["id"]
    assert {relative:(workspace / relative).read_bytes() for relative in foreign} == preserved


@pytest.fixture
def native_plan_lens_action(tmp_path, monkeypatch):
    from taskplane.tests.test_stage_cross_host import _real_pristine_run, _record_bootstrap_requirement
    workspace, store, initial = _real_pristine_run(tmp_path)
    requirement = _record_bootstrap_requirement(workspace)
    loop.reqs.amend_requirement(str(workspace), requirement["id"], nfr={
        "security":"review local trust boundaries", "architecture":"reuse current owners"})
    (workspace / "current-spec.md").write_text("Plan the current local change with independent quick reviews.")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    # Simulated applicability provider; production policy still chooses its
    # canonical mandatory set and any fourth risk. No host completion here.
    monkeypatch.setattr(loop.lens_router, "route", lambda *a, **k:{"lenses":[], "context":{"status":"ready"}})
    initialized = loop.init(str(workspace), "current Plan with native quick lenses", spec_path="current-spec.md",
        requirement_id=requirement["id"], by="human:vdemkiv")
    assert initialized["step"] == "plan", initialized
    action = loop.next_action.__wrapped__(str(workspace))
    assert not action.get("error"), action
    return str(workspace), workspace, store, initial, action


def test_public_plan_action_emits_exact_native_lens_team_and_replays_once(native_plan_lens_action):
    ws, _, store, initial, action = native_plan_lens_action
    assert "plan_lens_dispatches" in action, "selected Plan lenses have no native contracts or dispatch briefs"
    workers = action["plan_lens_dispatches"]
    plan = action["plan_team_plan"]
    assert len(workers) in {3, 4}
    assert {row["lens"] for row in workers} == set(action["focused_route"]["dispatchable_selected"])
    assert len({row["task_name"] for row in workers}) == len({row["task_slot"] for row in workers}) == len(workers)
    for worker in workers:
        assert worker["output"] == f"plan/lenses/{worker['lens']}.json"
        assert worker["dispatch_intent"]["schema"] == "taskplane.plan-lens-dispatch-intent/v1"
        contract = loop.tp.load_json(loop.tp.active_contract_path(ws, worker["task_slot"]))
        assert contract["worker_lifecycle"]["stage"] == "plan-lens"
        assert contract["worker_lifecycle"]["expected_task_name"] == worker["task_name"]
        assert contract["write_allow"] == [worker["output"]]
        assert "plan_host_authority" in contract["worker_lifecycle"]
    before_queue = loop.tp._load_queue_strict(loop.tp._dispatch_path(ws, "expected_dispatch.json"))
    repeated = loop.next_action.__wrapped__(ws)
    assert repeated["plan_team_plan"] == plan
    lens_queue = lambda rows: [row for row in rows if row.get("kind") == "plan-lens"]
    assert lens_queue(loop.tp._load_queue_strict(loop.tp._dispatch_path(ws, "expected_dispatch.json"))) == lens_queue(before_queue)
    assert len(store.load(initial["run_id"])["stage_heads"]) == 1
    assert "phase_runtime" not in action


def _finish_plan_lenses(ws, workspace, action, *, usage="unavailable"):
    """Simulate only native dispatch/start/Stop; use real lifecycle owners."""
    from taskplane.tests.test_r0002_cross_host_journey import _digest
    plan = action["plan_team_plan"]
    events = []
    for index, worker in enumerate(plan["workers"]):
        expected = loop.tp.peek_expectation(ws, worker["task_name"], strict=True)
        loop.tp.record_design_dispatch_assignment_activity(ws, expected)
        loop.record_native_dispatch_observation(ws, expected=expected,
            native_task_name=worker["task_name"], observed_at=100 + index)
        assert loop.tp.commit_dispatch_verification(ws, worker["task_name"], worker["model"],
            expected, True, worker["reasoning_effort"], strict=True)
        event = {"cwd":ws, "session_id":"pristine-session", "agent_id":f"plan-child-{index}",
            "agent_type":worker["task_name"], "task_name":worker["task_name"], "turn_id":f"turn-{index}"}
        bound = loop.tp.bind_worker_contract_event(ws, event)
        loop.tp.record_design_worker_start_activity(ws, bound, event)
        material = {"schema":"taskplane.plan-lens-result/v1", "lens":worker["lens"],
            "worker_identity":worker["task_name"], "team_plan_fingerprint":plan["fingerprint"],
            "candidate_fingerprint":plan["candidate_fingerprint"], "outcome":"pass", "findings":[]}
        path = workspace / worker["output"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**material, "fingerprint":_digest(material)}))
        if usage == "measured":
            loop.record_observed_dispatch_usage(ws, task_id=worker["lens"],
                native_task_name=worker["task_name"], source_fingerprint="a" * 64,
                normalized_usage={"schema":loop.spend.USAGE_SCHEMA, "available":True,
                    "cached_input_tokens":60, "uncached_input_tokens":40, "output_tokens":10,
                    "raw_total_tokens":110, "reasoning_tokens":5})
            sealed = loop.finalize_observed_dispatch_usage(ws, task_id=worker["lens"],
                native_task_name=worker["task_name"], ended_at=110 + index, outcome="success")
            event["usage_reference"] = {"schema":"taskplane.native-dispatch-usage-reference/v1",
                "dispatch_receipt":sealed["receipt"]}
        elif usage != "missing":
            sealed = loop.finalize_observed_dispatch_usage(ws, task_id=worker["lens"],
                native_task_name=worker["task_name"], ended_at=110 + index, outcome="success",
                usage_unavailable=True, unavailable_reason="simulated host has no counter provider")
            assert sealed["status"] == "unavailable"
            assert sealed["binding"]["usage"] is None
        terminal = loop.tp.terminalize_worker_contract(ws, {**event, "outcome":"success"},
            outcome="success", submission_status="not_required")
        assert terminal
        events.append(event)
    return events


@pytest.mark.parametrize("started", [False, True])
def test_session_start_preserves_current_plan_lens_slots(native_plan_lens_action, started):
    ws, _, _, _, action = native_plan_lens_action
    worker = action["plan_lens_dispatches"][0]
    if started:
        loop.tp.bind_worker_contract_event(ws, {"cwd":ws, "session_id":"pristine-session",
            "agent_id":"current-plan-child", "agent_type":worker["task_name"],
            "task_name":worker["task_name"], "turn_id":"turn-plan"})
    paths = [Path(loop.tp.active_contract_path(ws, row["task_slot"])) for row in action["plan_lens_dispatches"]]
    before = {str(path):path.read_bytes() for path in paths}
    assert loop.tp.sweep_completed_worker_contracts(ws, loop_state=loop.load(ws)) == []
    assert {str(path):path.read_bytes() for path in paths} == before
    assert loop.resume(ws)["step"] == "plan"


@pytest.mark.parametrize("stage", ["design-lens", "unrecognized-worker"])
@pytest.mark.parametrize("started", [False, True])
def test_session_start_does_not_guess_lens_or_unknown_stage_completion(tmp_path, stage, started):
    from taskplane.tests.test_worker_contract_lifecycle import _active_worker, _event
    contract = _active_worker(tmp_path, stage=stage, task="architecture")
    if started:
        loop.tp.bind_worker_contract_event(str(tmp_path), _event(tmp_path), now=11)
    path = Path(loop.tp.active_contract_path(str(tmp_path), contract["task_slot"]))
    before = path.read_bytes()
    assert loop.tp.sweep_completed_worker_contracts(str(tmp_path), loop_state={"step":"design", "tasks":[]}) == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("receipt_kind", ["missing", "tampered", "native"])
def test_session_start_terminal_flag_requires_authenticated_receipt(tmp_path, receipt_kind):
    from taskplane.tests.test_worker_contract_lifecycle import _active_worker, _event
    ws = str(tmp_path)
    contract = _active_worker(tmp_path, stage="plan-lens", task="architecture")
    slot = contract["task_slot"]
    loop.tp.bind_worker_contract_event(ws, _event(tmp_path), now=11)
    if receipt_kind != "missing":
        loop.tp.record_worker_terminal(ws, slot, event=_event(tmp_path), outcome="failure",
            submission_status="not_required", now=12)
    path = Path(loop.tp.active_contract_path(ws, slot))
    current = json.loads(path.read_text())
    current["worker_lifecycle"]["status"] = "terminal"
    if receipt_kind == "tampered":
        current["worker_lifecycle"]["terminal"]["signature"] = "tampered"
    path.write_text(json.dumps(current))
    before = path.read_bytes()
    if receipt_kind == "native":
        released = loop.tp.sweep_completed_worker_contracts(ws, loop_state={"step":"plan"})
        assert len(released) == 1
        assert released[0]["outcome"] == "failure"
        assert loop.tp.released_worker_contract(ws, slot)["worker_lifecycle"]["terminal"]["authority"] == "host-lifecycle"
    else:
        with pytest.raises(loop.tp.StateError):
            loop.tp.sweep_completed_worker_contracts(ws, loop_state={"step":"plan"})
        assert path.read_bytes() == before


def _write_reviewed_plan(ws, workspace):
    requirement = loop.reqs.get_requirement(ws, loop.load(ws)["requirement_id"])
    tasks = {"tasks":[{"id":"t01", "scope":["README.md"], "tests":"python3 -c 'assert True'",
        "deps":[], "new_modules":["(root)"], "criteria":requirement["acceptance"], "acceptance_refs":requirement["acceptance"],
        "req":requirement["id"], "status":"pending"}]}
    (workspace / "plan" / "tasks.json").write_text(json.dumps(tasks))
    (workspace / "plan" / "plan.md").write_text("# Plan\n\nImplement the current bounded requirement.\n")


@pytest.mark.parametrize("usage", ["unavailable", "measured"])
def test_native_plan_results_collect_then_public_gate_with_unknown_usage(native_plan_lens_action, usage):
    ws, workspace, _, _, action = native_plan_lens_action
    refused = loop.gate.__wrapped__(ws, "pass")
    assert refused["error"] == "Plan lens collection is incomplete"
    _finish_plan_lenses(ws, workspace, action, usage=usage)
    assert loop._design_team_errors(ws, loop.load(ws), stage="plan") == []
    _write_reviewed_plan(ws, workspace)
    gated = loop.gate.__wrapped__(ws, "pass")
    assert not gated.get("error"), gated
    assert gated["step"] == "plan_approval"
    bindings = loop.load(ws)["dispatch_telemetry"]["bindings"]
    assert all((row["usage"] is None) == (usage == "unavailable") for row in bindings)


def test_plan_collection_does_not_mix_same_lens_from_design_team(native_plan_lens_action):
    from taskplane.tests.test_r0002_cross_host_journey import _worker, _plan, _complete_worker
    ws, workspace, _, _, action = native_plan_lens_action
    _finish_plan_lenses(ws, workspace, action)
    plan = action["plan_team_plan"]
    # Both teams use the incumbent whole-run artifact owner. This transport
    # fixture does not claim a Design stage approval or native acceptance.
    worker = _worker(plan["selected"][0], run_id=plan["run_id"], stage_id=plan["stage_instance_id"],
        candidate=plan["candidate_fingerprint"], settings=plan["settings_digest"])
    design = _plan([worker], run_id=plan["run_id"], stage_id=plan["stage_instance_id"],
        candidate=plan["candidate_fingerprint"], settings=plan["settings_digest"])
    state = loop.load(ws)
    authority = loop.tp.register_design_lens_dispatch_plan(ws, design,
        artifact_root=loop._run_artifact_root(ws, state), artifact_binding=state["run_artifact_binding"])
    _complete_worker(workspace, design, authority, worker, index=100)
    collected = loop.tp.validate_design_lens_dispatch_completion(ws, plan, plan["host_authority"])
    assert collected["valid"], collected
    # New Design bytes still invalidate the original Plan source binding.
    assert loop._design_team_errors(ws, loop.load(ws), stage="plan")


def test_plan_team_registration_interrupted_replay_preserves_bound_owner(native_plan_lens_action):
    ws, _, _, _, action = native_plan_lens_action
    worker = action["plan_lens_dispatches"][0]
    event = {"cwd":ws, "session_id":"pristine-session", "agent_id":"already-started-child",
        "agent_type":worker["task_name"], "task_name":worker["task_name"], "turn_id":"started-turn"}
    loop.tp.bind_worker_contract_event(ws, event)
    slot_path = Path(loop.tp.active_contract_path(ws, worker["task_slot"]))
    before = slot_path.read_bytes()
    with loop.mutate(ws) as state:
        state.pop("plan_team_plan")  # Simulate interruption before the loop's team write.
    repeated = loop.next_action.__wrapped__(ws)
    assert repeated.get("plan_team_plan") == action["plan_team_plan"], repeated
    assert slot_path.read_bytes() == before


@pytest.mark.parametrize("corruption", ["missing", "foreign", "stale", "duplicate", "route", "usage", "changes-required", "result-bytes", "requirement"])
def test_native_plan_collection_refuses_independent_corruption(native_plan_lens_action, corruption):
    from taskplane.tests.test_r0002_cross_host_journey import _digest
    ws, workspace, _, _, action = native_plan_lens_action
    _finish_plan_lenses(ws, workspace, action, usage="missing" if corruption == "usage" else "unavailable")
    worker = action["plan_lens_dispatches"][0]
    path = workspace / worker["output"]
    if corruption == "missing":
        path.unlink()
    elif corruption in {"foreign", "changes-required", "result-bytes"}:
        result = json.loads(path.read_text())
        if corruption == "result-bytes":
            result["findings"] = [{"summary":"Changed after actual terminal"}]
        else:
            result["worker_identity" if corruption == "foreign" else "outcome"] = "foreign-worker" if corruption == "foreign" else "changes-required"
        result["fingerprint"] = _digest({key:value for key,value in result.items() if key != "fingerprint"})
        path.write_text(json.dumps(result))
    elif corruption == "stale":
        (workspace / "README.md").write_text("Unreviewed changed source\n")
    elif corruption == "requirement":
        loop.reqs.amend_requirement(ws, loop.load(ws)["requirement_id"], nfr={"security":"new unreviewed boundary"})
    elif corruption in {"duplicate", "route"}:
        with loop.mutate(ws) as state:
            plan = state["plan_team_plan"]
            if corruption == "duplicate":
                plan["workers"].append(copy.deepcopy(plan["workers"][0]))
            else:
                plan["route_fingerprint"] = "f" * 64
            plan["fingerprint"] = _digest({key:value for key,value in plan.items() if key not in {"fingerprint", "host_authority"}})
    refused = loop.gate.__wrapped__(ws, "pass")
    assert refused.get("error") == "Plan lens collection is incomplete", refused
    assert loop.load(ws)["step"] == "plan"


@pytest.fixture
def current_focused_plan_inputs(tmp_path, monkeypatch):
    from taskplane import review_evidence
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "disabled")
    ws = _workspace(tmp_path)
    requirement = loop.reqs.record_requirement(ws, "Current input", functional=["bounded scope"], acceptance=["current evidence"])
    root = Path(ws)
    design = {"requirement":requirement["id"], "summary":"Current selected solution"}
    plan = {"requirement":requirement["id"], "tasks":[{"id":"t1", "req":requirement["id"],
        "scope":["README.md"], "tests":"true", "acceptance_refs":["current evidence"]}],
        "plan_route":{"selected":["architecture", "project-management", "testability", "security"]}}
    for relative, value in (("design/contract.json", design), ("plan/tasks.json", plan)):
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(value))
    (root / "design/design.md").write_text("Current Design narrative")
    state = {"step":"plan", "requirement_id":requirement["id"], "design_required":True,
        "design_fingerprint":loop._design_evidence_fingerprint(ws),
        "plan_fingerprint":review_evidence.content_fingerprint(plan)}
    return ws, root, state, design, plan


def test_current_bound_plan_selected_choices_do_not_replace_mandatory_settings(current_focused_plan_inputs):
    ws, _, state, design, plan = current_focused_plan_inputs
    evidence, mandatory = loop._focused_stage_evidence(ws, state, "plan")
    assert mandatory is None
    assert evidence["approved_design"]["summary"] == design["summary"]
    assert evidence["plan_route"] == plan["plan_route"]
    assert evidence["selectors"] == {"t1":"true"}
    assert evidence["task_to_ac_coverage"] == {"t1":["current evidence"]}
    catalog = {row["id"] for row in loop.lens_router.load_catalog()["lenses"]}
    policy = load_settings(environment={}).lenses.policy_for("plan", catalog_ids=catalog)
    assert list(policy.mandatory) == ["architecture", "project-management", "testability"]
    assert policy.max_count == 4


@pytest.mark.parametrize("kind", ["design", "plan"])
@pytest.mark.parametrize("damage", ["missing", "corrupt", "stale", "foreign", "symlink"])
def test_bound_plan_inputs_refuse_bad_provenance(current_focused_plan_inputs, kind, damage):
    from taskplane import review_evidence
    ws, root, state, _, _ = current_focused_plan_inputs
    path = root / ("design/contract.json" if kind == "design" else "plan/tasks.json")
    if damage == "missing": path.unlink()
    elif damage == "corrupt": path.write_text("{not JSON")
    elif damage == "symlink":
        other = root / "aliased.json"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    else:
        value = json.loads(path.read_text())
        value["requirement" if damage == "foreign" else "summary"] = "R-9999" if damage == "foreign" else "changed"
        path.write_text(json.dumps(value))
        if damage == "foreign":
            # A matching content hash must not excuse a foreign requirement.
            state[kind + "_fingerprint"] = (loop._design_evidence_fingerprint(ws, value) if kind == "design"
                else review_evidence.content_fingerprint(value))
    before = _content_inventory(root)
    with pytest.raises((ValueError, OSError)):
        loop._focused_stage_evidence(ws, state, "plan")
    assert _content_inventory(root) == before


@pytest.mark.parametrize("damage", [None, "selected-missing", "selected-corrupt", "requirement-stale", "foreign-design"])
def test_plan_consumes_only_verified_current_design_handoff(tmp_path, monkeypatch, damage):
    from taskplane.tests.test_stage_cross_host import _real_pristine_run, _record_bootstrap_requirement
    workspace, store, _ = _real_pristine_run(tmp_path)
    ws = str(workspace)
    requirement = _record_bootstrap_requirement(workspace)
    (workspace / "current-spec.md").write_text("Current Design input")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    state = loop.init(ws, "current Design into Plan", spec_path="current-spec.md", design=True,
        requirement_id=requirement["id"], by="human:vdemkiv")
    assert state["step"] == "design", state
    loop._stage_bootstrap_pristine_root(ws, state)
    state = loop.load(ws)
    design_dir = workspace / "design"
    design_dir.mkdir()
    design = {"requirement":"R-9999" if damage == "foreign-design" else requirement["id"],
        "summary":"Selected immutable Design"}
    (design_dir / "contract.json").write_text(json.dumps(design))
    (design_dir / "design.md").write_text("Selected narrative")
    # Exercise real output/handoff owners, not a native Design acceptance claim.
    completion = loop._stage_loop_gate_completion(ws, state, step="design", outcome="pass")
    receipt = loop._stage_loop_transition(ws, state, from_step="design", to_step="plan", completion=completion)
    _, handoff = _successor_handoff(ws, store, receipt)
    state["step"] = "plan"
    loop.save(ws, state)
    context = loop._stage_loop_context(ws, state)
    if damage in {"selected-missing", "selected-corrupt"}:
        reference = handoff["selected_artifacts"][0]
        path = Path(context["lifecycle"]._artifact_store()._path(reference["kind"], reference["fingerprint"]))
        if damage == "selected-missing": path.unlink()
        else: path.write_text("corrupt retained selected artifact")
    elif damage == "requirement-stale":
        loop.reqs.amend_requirement(ws, requirement["id"], functional=["unrelated changed requirement"])
    # The source filename is not selection authority. Its unrelated later bytes
    # must neither substitute for nor corrupt the immutable handoff's content.
    (design_dir / "contract.json").write_text('{"requirement":"R-8888","summary":"unselected repository content"}')
    source_before = _content_inventory(design_dir)
    if damage:
        with pytest.raises((ValueError, OSError)):
            loop._focused_stage_evidence(ws, state, "plan")
    else:
        evidence, mandatory = loop._focused_stage_evidence(ws, state, "plan")
        assert evidence["approved_design"]["summary"] == "Selected immutable Design"
        assert evidence["task_scopes"] == {} and mandatory is None
    assert _content_inventory(design_dir) == source_before


def test_new_run_refuses_unmarked_existing_singleton(
        tmp_path, monkeypatch) -> None:
    from taskplane.tests.test_stage_cross_host import _real_loop_stage

    unmarked_root = tmp_path / "unmarked"
    unmarked_root.mkdir()
    unmarked_ws, unmarked_store, unmarked_stage = _real_loop_stage(
        unmarked_root, stage_kind="product",
        stage_id="stage-product-unmarked-root")
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    loop.init(str(unmarked_ws), "pre-existing singleton")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")

    refused = loop.stage_command(str(unmarked_ws), "start", {
        "schema": "taskplane.stage-command/v1", "stage": unmarked_stage,
        "expected_revision": 1, "operation_id": "start-too-late",
        "expected_predecessor_fingerprints": {}, "foreground": True,
        "authority": unmarked_stage["authority"],
    })

    assert refused["enabled"] is False
    assert "existing singleton" in refused["error"] or \
        "pristine" in refused["error"]
    assert unmarked_store.load(unmarked_stage["run_id"])["schema"] == \
        "taskplane.run/v3"


def test_dispatch_uses_one_deterministic_verified_resume_attempt(
        monkeypatch) -> None:
    calls = []
    stage = {
        "run_id": "run-r0004", "stage_id": "stage-build-001",
        "fingerprint": "f" * 64, "stage_kind": "build",
        "selected_artifacts": [],
    }

    class Lifecycle:
        def resume_stage(self, run_id, **kwargs):
            calls.append((run_id, kwargs))
            return {"operation": "resume_stage", "result": {
                "attempt_id": kwargs["attempt_id"]}}

    context = {
        "store": object(), "manifest": {"revision": 7},
        "lifecycle": Lifecycle(), "stage": stage,
    }
    monkeypatch.setattr(
        loop, "_stage_loop_context", lambda _ws, *_a, **_k: context)
    monkeypatch.setattr(
        loop, "_stage_dispatch",
        lambda _store, _lifecycle, receipt, current, **kwargs: {
            "schema": "taskplane.stage-dispatch/v1",
            "receipt": receipt["operation"],
            "stage": current["stage_id"],
            "attempt": kwargs["attempt_id"],
            "scope": kwargs["declared_scope"],
        })
    state = {"step": "execute"}
    scope = {"scope_paths": ["taskplane/loop.py"],
             "out_of_scope_paths": []}

    first = loop._stage_loop_dispatch(
        "/repo", state, slot="t06-loop", declared_scope=scope)
    second = loop._stage_loop_dispatch(
        "/repo", state, slot="t06-loop", declared_scope=scope)

    assert first == second
    assert first["schema"] == "taskplane.stage-dispatch/v1"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][1]["operation_id"].startswith("loop-dispatch-")
    assert calls[0][1]["attempt_id"].startswith("attempt-")


def test_same_stage_kind_transition_does_not_mutate_stage_store(
        monkeypatch) -> None:
    monkeypatch.setattr(
        loop, "_stage_loop_context",
        lambda _ws: (_ for _ in ()).throw(AssertionError("store opened")))

    assert loop._stage_loop_transition(
        "/repo", {"step": "design_approval"},
        from_step="design", to_step="design_approval") is None


def test_terminal_transition_replays_the_existing_operation(monkeypatch) -> None:
    class StageEntities:
        @staticmethod
        def request_fingerprint(_value):
            return "a" * 64

    receipt = {"schema": "taskplane.stage-operation-receipt/v1",
               "operation_id": "loop-transition-" + "a" * 32}
    context = {
        "run_id": "run-r0004", "store": object(), "stage": None,
        "lifecycle": None, "stage_entities": StageEntities,
        "manifest": {"stage_operations": {
            receipt["operation_id"]: receipt}},
    }
    monkeypatch.setattr(
        loop, "_stage_loop_context", lambda _ws, *_a, **_k: context)
    monkeypatch.setattr(loop.tp, "verify_stage_receipt", lambda value: value)

    assert loop._stage_loop_transition(
        "/repo", {"step": "done"},
        from_step="retro", to_step="done") is receipt


def test_terminal_transition_refuses_predecessor_input_as_completion_evidence(
        monkeypatch) -> None:
    stage = {
        "run_id": "run-r0004", "stage_id": "stage-retro-001",
        "fingerprint": "f" * 64, "stage_kind": "retro",
        "deliverables": ["retrospective"],
        "authority": {"actor": "human:owner"},
        "created_at": "2026-08-21T20:00:00Z",
    }

    class StageEntities:
        @staticmethod
        def request_fingerprint(_value):
            return "a" * 64

    class Lifecycle:
        @staticmethod
        def terminalize(*_args, **_kwargs):
            raise AssertionError("predecessor input evidence was reused")

    context = {
        "run_id": stage["run_id"], "store": object(), "stage": stage,
        "lifecycle": Lifecycle(), "stage_entities": StageEntities,
        "manifest": {"revision": 7, "stage_operations": {}},
    }
    predecessor_handoff = {
        "evidence_references": [{
            "schema": "taskplane.artifact-reference/v1",
            "kind": "input-evidence", "fingerprint": "e" * 64,
        }],
        "authorization": {"authorized_at": "2026-08-21T19:00:00Z"},
    }
    monkeypatch.setattr(
        loop, "_stage_loop_context", lambda _ws, *_a, **_k: context)
    monkeypatch.setattr(
        loop, "_verified_stage_handoff", lambda *_a: predecessor_handoff)

    with pytest.raises(ValueError, match="completion"):
        loop._stage_loop_transition(
            "/repo", {"step": "done"},
            from_step="retro", to_step="done")


def test_selection_stage_failure_rolls_back_the_legacy_choice(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "choose one bounded variant", parallel=True)
    state.update({
        "step": "selection", "ab": True,
        "baseline": loop.tp.git_head(ws),
        "tasks": [
            {"id": "variant-a", "variant": "A", "scope": ["a/**"],
             "status": "passed"},
            {"id": "variant-b", "variant": "B", "scope": ["b/**"],
             "status": "passed"},
        ],
    })
    loop.save(ws, state)
    before = copy.deepcopy(loop.load(ws))
    monkeypatch.setattr(loop, "reconcile_authority_effects", lambda _ws: {})
    monkeypatch.setattr(loop, "status", lambda _ws: {})
    monkeypatch.setattr(
        loop.authority_engine, "build_selection",
        lambda *_a, **_k: {"authorized": True, "reasons": []})

    def refuse(*_args, **kwargs):
        assert kwargs == {"from_step": "selection", "to_step": "em"}
        raise RuntimeError("selection stage refused")

    monkeypatch.setattr(loop, "_stage_loop_transition", refuse)

    result = loop.select(ws, "A")

    assert "stage-native" in result["error"]
    assert loop.load(ws) == before


@pytest.mark.parametrize(
    ("decision", "to_step"), [("retry", "fix"), ("abort", "failed")])
def test_resolve_stage_failure_rolls_back_retry_or_abort(
        tmp_path, monkeypatch, decision, to_step) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "resolve one bounded escalation")
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3,
        }],
    })
    loop.save(ws, state)
    before = copy.deepcopy(loop.load(ws))
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    def refuse(*_args, **kwargs):
        assert kwargs == {"from_step": "escalated", "to_step": to_step}
        raise RuntimeError("recovery stage refused")

    monkeypatch.setattr(loop, "_stage_loop_transition", refuse)

    result = loop.resolve(ws, decision)

    assert "stage-native" in result["error"]
    assert loop.load(ws) == before


def test_replan_stage_failure_preserves_the_frozen_plan(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "replan one bounded task")
    state.update({
        "step": "execute", "baseline": loop.tp.git_head(ws),
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "running",
        }],
    })
    loop.save(ws, state)
    before = copy.deepcopy(loop.load(ws))
    monkeypatch.setattr(loop.tp, "clear", lambda _ws: None)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *_a, **_k: {})

    def refuse(*_args, **kwargs):
        assert kwargs == {"from_step": "execute", "to_step": "plan"}
        raise RuntimeError("replan stage refused")

    monkeypatch.setattr(loop, "_stage_loop_transition", refuse)

    result = loop.replan(ws, by="human:owner", reason="invalid task graph")

    assert "stage-native" in result["error"]
    assert loop.load(ws) == before


def test_retro_attaches_one_idempotent_terminal_receipt(monkeypatch) -> None:
    receipt = {"operation": "terminalize", "operation_id": "retro-done"}
    monkeypatch.setattr(
        loop.retro_engine, "run", lambda *_args, **_kwargs: {"goal": "done"})
    monkeypatch.setattr(loop, "load", lambda _ws: {"step": "done"})
    calls = []

    def transition(*_args, **kwargs):
        calls.append(kwargs)
        return receipt

    monkeypatch.setattr(loop, "_stage_loop_transition", transition)

    first = loop.retro("/repo")
    second = loop.retro("/repo")

    assert first["stage_transition"] == receipt
    assert second == first
    assert calls == [
        {"from_step": "retro", "to_step": "done"},
        {"from_step": "retro", "to_step": "done"},
    ]


def test_next_action_attaches_stage_runtime_dispatch(tmp_path, monkeypatch) \
        -> None:
    ws = _workspace(tmp_path)
    loop.init(ws, "define the bounded handoff")
    marker = {"schema": "taskplane.stage-dispatch/v1", "startup": {}}
    monkeypatch.setattr(loop, "_stage_loop_dispatch", lambda *_a, **_k: marker)

    result = loop.next_action.__wrapped__(ws)

    assert result["step"] == "pm"
    assert result["stage_runtime_dispatch"] is marker


def test_wave_emits_native_intent_without_stage_runtime_dispatch(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "build the bounded handoff", spec_path="spec.md",
                      parallel=True)
    state.update({
        "step": "execute",
        "tasks": [{"id": "t01", "scope": ["README.md"],
                   "tests": "true", "deps": [], "status": "pending"}],
    })
    loop.save(ws, state)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("native delivery must not use StageLifecycle")

    monkeypatch.setattr(loop, "_stage_loop_dispatch", forbidden)
    monkeypatch.setattr(loop, "_stage_loop_wave_dispatches", forbidden)

    authority = open_delivery_root(ws)
    result = loop.wave(ws, root_observation_authority=authority)

    assert len(result["wave"]) == 1
    assert result["wave"][0]["dispatch_intent"]["intent_id"]
    assert "stage_runtime_dispatch" not in result["wave"][0]
    assert result["wait_invocation"]["outstanding_members"] == ["t01"]
    assert not (loop.load(ws) or {}).get("_stage_bindings")


def test_parallel_wave_emits_one_native_set_without_execution_roots(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "dispatch two independent roots", parallel=True)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [
            {"id": "t01", "scope": ["a/**"], "tests": "true",
             "deps": [], "status": "pending"},
            {"id": "t02", "scope": ["b/**"], "tests": "true",
             "deps": [], "status": "pending"},
        ],
    })
    loop.save(ws, state)

    monkeypatch.setattr(
        loop, "_stage_loop_wave_dispatches",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Taskplane child-stage scheduler was invoked")))

    authority = open_delivery_root(ws)
    result = loop.wave(ws, root_observation_authority=authority)

    assert [entry["task"]["id"] for entry in result["wave"]] == [
        "t01", "t02"]
    assert len({entry["dispatch_intent"]["intent_id"]
                for entry in result["wave"]}) == 2
    assert result["wait_invocation"]["outstanding_members"] == [
        "t01", "t02"]
    assert result["held"] == []
    encoded = json.dumps(result, sort_keys=True).lower()
    assert "stage_runtime_dispatch" not in encoded
    assert "execution_root" not in encoded
    assert "tranche" not in encoded
    assert not (loop.load(ws) or {}).get("_stage_bindings")


def test_parallel_wave_honors_configured_build_concurrency(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "dispatch two independent roots", parallel=True)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [
            {"id": "t01", "scope": ["a/**"], "tests": "true",
             "deps": [], "status": "pending"},
            {"id": "t02", "scope": ["b/**"], "tests": "true",
             "deps": [], "status": "pending"},
        ],
    })
    loop.save(ws, state)
    effective = load_settings(environment={})
    capped = replace(
        effective, build=replace(effective.build, concurrency=1))
    monkeypatch.setattr(
        loop.operational_settings, "load_settings", lambda **_kwargs: capped)

    authority = open_delivery_root(ws)
    result = loop.wave(ws, root_observation_authority=authority)

    assert [entry["task"]["id"] for entry in result["wave"]] == ["t01"]
    assert result["held"] == [{
        "task": "t02",
        "reason": "configured build concurrency cap — next wave",
        "shared_owner": "settings",
    }]


def test_real_wave_recovers_task_bindings_after_post_split_crash(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "split-recovery", monkeypatch, stage_kind="build",
        stage_id="stage-build-split-parent")
    state = loop.load(ws)
    ready = [
        {"id": "t01", "scope": ["a/**"], "tests": "true",
         "deps": [], "status": "pending"},
        {"id": "t02", "scope": ["b/**"], "tests": "true",
         "deps": [], "status": "pending"},
    ]
    state.update({
        "step": "execute", "parallel": True, "current_task": 0,
        "tasks": ready,
    })
    loop.save(ws, state)
    real_mutate = loop.mutate

    @contextlib.contextmanager
    def crash_before_binding_commit(_workspace):
        raise RuntimeError("crash after split before singleton binding")
        yield

    monkeypatch.setattr(loop, "mutate", crash_before_binding_commit)
    with pytest.raises(RuntimeError, match="after split"):
        loop._stage_loop_wave_dispatches(ws, state, ready)
    split = store.load(stage["run_id"])
    assert len(split["active_stage_projection"]["active_stage_ids"]) == 2
    assert "_stage_bindings" not in loop.load(ws)

    monkeypatch.setattr(loop, "mutate", real_mutate)
    recovered = loop._stage_loop_wave_dispatches(
        ws, loop.load(ws), ready)

    assert set(recovered) == {"t01", "t02"}
    bindings = loop.load(ws)["_stage_bindings"]
    child_ids = {bindings[task_id]["build"] for task_id in recovered}
    assert len(child_ids) == 2
    assert child_ids == set(
        store.load(stage["run_id"])["active_stage_projection"][
            "active_stage_ids"])
    assert {
        dispatch["startup"]["stage_id"] for dispatch in recovered.values()
    } == child_ids


def test_gate_rolls_back_on_stage_failure_then_returns_transition_receipt(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    loop.init(ws, "define the bounded handoff")
    specs = tmp_path / "repo" / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text("bounded handoff\n", encoding="utf-8")
    monkeypatch.setattr(
        loop, "_stage_loop_transition",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("stage refused")))

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "stage-native loop transition failed closed" in refused["error"]
    assert loop.load(ws)["step"] == "pm"

    receipt = {"operation": "terminalize_and_start",
               "operation_id": "pm-to-plan"}
    monkeypatch.setattr(
        loop, "_stage_loop_transition", lambda *_a, **_k: receipt)
    accepted = loop.gate.__wrapped__(ws, "pass")

    assert accepted["stage_transition"] is receipt
    assert loop.load(ws)["step"] == "plan"


def test_real_product_gate_and_plan_approval_seal_exact_outputs(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "gate-approve", monkeypatch,
        stage_id="stage-product-gate-root")
    spec_path = "specs/spec.md"
    spec = "# Product\n\nShip the bounded stage journey.\n"
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(spec, encoding="utf-8")

    gated = loop.gate.__wrapped__(ws, "pass")

    assert "error" not in gated, gated
    assert gated["step"] == "plan"
    assert gated["stage_transition"]["operation"] == \
        "terminalize_and_start"
    _plan_stage, product_handoff = _successor_handoff(
        ws, store, gated["stage_transition"])
    product_payloads = _handoff_payload_text(ws, product_handoff)
    assert spec_path in product_payloads
    assert hashlib.sha256(spec.encode()).hexdigest() in product_payloads

    plan_dir = Path(ws) / "plan"
    plan_dir.mkdir()
    plan_text = "# Plan\n\nImplement the bounded handoff.\n"
    tasks_value = {"tasks": [{
        "id": "t01", "scope": ["README.md"], "tests": "true",
        "deps": [], "status": "pending",
    }]}
    (plan_dir / "plan.md").write_text(plan_text, encoding="utf-8")
    (plan_dir / "tasks.json").write_text(
        json.dumps(tasks_value, sort_keys=True) + "\n", encoding="utf-8")
    state = loop.load(ws)
    state.update({
        "step": "plan_approval", "tasks": tasks_value["tasks"],
        "graph_dor": {"ready": True, "blockers": []},
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop, "_design_current_errors", lambda *_a: [])
    monkeypatch.setattr(loop, "_refinement_report", lambda *_a: [])
    monkeypatch.setattr(loop.tp, "plan_task_id_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_consolidated_enabled", lambda: False)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *_a, **_k: {})

    approved = loop.approve(ws, by="human:owner")

    assert "error" not in approved, approved
    assert approved["step"] == "execute"
    assert approved["stage_transition"]["operation"] == \
        "terminalize_and_start"
    build_stage, plan_handoff = _successor_handoff(
        ws, store, approved["stage_transition"])
    plan_payloads = _handoff_payload_text(ws, plan_handoff)
    assert build_stage["stage_kind"] == "build"
    for path, content in (
            ("plan/plan.md", plan_text),
            ("plan/tasks.json", json.dumps(tasks_value, sort_keys=True) + "\n")):
        assert path in plan_payloads
        assert hashlib.sha256(content.encode()).hexdigest() in plan_payloads


@pytest.mark.parametrize(
    ("stage_kind", "from_step", "to_step", "outputs"), [
        ("design", "design", "plan", {
            "design/design.md": "# Design\n\nA bounded immutable stage.\n",
            "design/contract.json": "{\"schema\":\"test-design\"}\n",
        }),
    ])
def test_real_stage_completion_seals_design_outputs(
        tmp_path, monkeypatch, stage_kind, from_step, to_step, outputs) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / stage_kind, monkeypatch, stage_kind=stage_kind,
        stage_id=f"stage-{stage_kind}-output-root")
    workspace = Path(ws)
    for relative, content in outputs.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    state = loop.load(ws)
    state["step"] = from_step
    loop.save(ws, state)

    completion = loop._stage_loop_gate_completion(
        ws, state, step=from_step, outcome="pass")
    receipt = loop._stage_loop_transition(
        ws, state, from_step=from_step, to_step=to_step,
        completion=completion)

    assert receipt["operation"] == "terminalize_and_start"
    successor, handoff = _successor_handoff(ws, store, receipt)
    assert successor["stage_kind"] == "plan"
    payloads = _handoff_payload_text(ws, handoff)
    for relative, content in outputs.items():
        assert relative in payloads
        assert hashlib.sha256(content.encode()).hexdigest() in payloads


def test_real_build_completion_propagates_exact_target_commit_and_outputs(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "build-output", monkeypatch, stage_kind="build",
        stage_id="stage-build-output-root")
    workspace = Path(ws)
    build_text = "stage journey\nreal build output\n"
    (workspace / "README.md").write_text(build_text, encoding="utf-8")
    target_commit = loop.tp.git_head(ws)
    state = loop.load(ws)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "tests": "true",
            "deps": [], "status": "running", "target_commit": target_commit,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(
        loop.runtime_eval, "guide_loop",
        lambda *_a, **_k: {"status": "on_path", "recovered": False})
    submitted = loop.submit(ws, "pass")
    assert "error" not in submitted, submitted
    state = loop.load(ws)

    completion = loop._stage_loop_gate_completion(
        ws, state, step="execute", outcome="pass",
        submission=submitted["submission"])
    receipt = loop._stage_loop_transition(
        ws, state, from_step="execute", to_step="evaluate",
        completion=completion)

    assert receipt["operation"] == "terminalize_and_start"
    successor, handoff = _successor_handoff(ws, store, receipt)
    assert successor["stage_kind"] == "evaluate"
    assert handoff["target"] is not None
    assert handoff["commit"] is not None
    assert handoff["commit"]["sha"] == target_commit
    assert handoff["commit"]["target_fingerprint"] == \
        handoff["target"]["fingerprint"]
    payloads = _handoff_payload_text(ws, handoff)
    assert "README.md" in payloads
    assert hashlib.sha256(build_text.encode()).hexdigest() in payloads
    assert stage["authority"]["target_revision"] == target_commit


def test_retro_retries_sealed_report_after_real_terminalization_failure(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "retro-retry", monkeypatch, stage_kind="retro",
        stage_id="stage-retro-retry-root")
    state = loop.load(ws)
    state["step"] = "retro"
    loop.save(ws, state)
    report = {
        "goal": state["goal"],
        "graph_true_up": {"changed": False},
        "evaluator_summary": loop.retro_engine.evaluator_summary([]),
    }
    computations = []

    def sealed_retro(workspace, *, load_state, mutate_state, **_kwargs):
        current = load_state(workspace)
        if (current.get("retro") or {}).get("status") == "complete":
            return report
        computations.append("computed")
        with mutate_state(workspace) as locked:
            locked["retro"] = {
                "id": "retro-001", "status": "complete", "report": report,
            }
            locked["step"] = "done"
        return report

    real_lifecycle_factory = loop._stage_lifecycle
    terminal_attempts = []

    class FailOnceLifecycle:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def terminalize(self, *args, **kwargs):
            terminal_attempts.append(kwargs["operation_id"])
            if len(terminal_attempts) == 1:
                raise RuntimeError("simulated terminal store interruption")
            return self._wrapped.terminalize(*args, **kwargs)

    def lifecycle_factory(*args, **kwargs):
        entities, lifecycle = real_lifecycle_factory(*args, **kwargs)
        return entities, FailOnceLifecycle(lifecycle)

    monkeypatch.setattr(loop.retro_engine, "run", sealed_retro)
    monkeypatch.setattr(loop, "_stage_lifecycle", lifecycle_factory)

    refused = loop.retro(ws)
    prepared = loop.load(ws)

    assert "stage-native" in refused["error"]
    assert prepared["step"] == "retro"
    assert prepared["_retro_terminal_step"] == "done"
    assert prepared["retro"]["status"] == "complete"
    assert prepared["retro"]["report"] == report
    assert store.load(stage["run_id"])["active_stage_projection"][
        "active_stage_ids"] == [stage["stage_id"]]

    completed = loop.retro(ws)

    assert "error" not in completed, completed
    assert completed["stage_transition"]["operation"] == "terminalize"
    assert loop.load(ws)["step"] == "done"
    assert "_retro_terminal_step" not in loop.load(ws)
    assert computations == ["computed"]
    assert len(terminal_attempts) == 2
    assert terminal_attempts[0] == terminal_attempts[1]
    assert store.load(stage["run_id"])["active_stage_projection"][
        "active_stage_ids"] == []


@pytest.mark.parametrize("mode", ["enabled", "disabled"])
def test_bound_v4_missing_locator_refuses_mutation_byte_identically(
        tmp_path, monkeypatch, mode) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / mode, monkeypatch,
        stage_id=f"stage-product-bound-{mode}")
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nPreserve a bound v4 singleton.\n",
        encoding="utf-8")
    state = loop.load(ws)
    assert state["_stage_run_binding"]["run_schema"] == \
        "taskplane.run/v4"
    state_path = Path(loop._loop_path(ws))
    before = state_path.read_bytes()

    state_root = state_path.parents[2]
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", mode)
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator", lambda _ws: None)

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "error" in refused
    assert "locator" in refused["error"].lower()
    assert state_path.read_bytes() == before


def test_selection_transition_seals_the_declared_control_output(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "selection-output", monkeypatch, stage_kind="evaluate",
        stage_id="stage-evaluate-selection-root")
    revision = loop.tp.git_head(ws)
    state = loop.load(ws)
    state.update({
        "step": "selection", "ab": True, "baseline": revision,
        "tasks": [
            {"id": "variant-a", "variant": "A", "scope": ["a/**"],
             "status": "passed"},
            {"id": "variant-b", "variant": "B", "scope": ["b/**"],
             "status": "passed"},
        ],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop, "reconcile_authority_effects", lambda _ws: {})
    monkeypatch.setattr(loop, "status", lambda _ws: {})
    monkeypatch.setattr(
        loop.authority_engine, "build_selection",
        lambda *_a, **_k: {"authorized": True, "reasons": []})

    selected = loop.select(ws, "A", note="ship the bounded winner")

    assert "error" not in selected, selected
    assert selected["stage_transition"]["operation"] == \
        "terminalize_and_start"
    successor, handoff = _successor_handoff(
        ws, store, selected["stage_transition"])
    assert successor["stage_kind"] == "engineering"
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-selection-result/v1" in payloads
    assert "variant-a" in payloads
    assert "ship the bounded winner" in payloads


def test_resolve_transition_seals_the_declared_control_output(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "resolve-output", monkeypatch,
        stage_kind="engineering", stage_id="stage-engineering-resolve-root")
    state = loop.load(ws)
    evidence = {"selector": "public acceptance journey", "returncode": 1}
    product_failure = loop.failure_routing.route_failure_records([{
        "schema": "taskplane.failure-record/v1",
        "id": "failure-t01-product",
        "source": "evaluate",
        "stage": "evaluate",
        "repro": "public acceptance journey still fails",
        "evidence": evidence,
        "evidence_digest": loop.failure_routing.evidence_digest(evidence),
        "class": "product",
        "reason": "the current product behavior violates acceptance",
        "owner": "task:t01",
        "cluster": "acceptance",
        "route": "fix",
        "candidate": {"id": "t01@candidate", "fingerprint": "b" * 64},
    }])
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3, "failure_routing": product_failure,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    resolved = loop.resolve(ws, "retry")

    assert "error" not in resolved, resolved
    assert resolved["stage_transition"]["operation"] == \
        "terminalize_and_start"
    successor, handoff = _successor_handoff(
        ws, store, resolved["stage_transition"])
    assert successor["stage_kind"] == "build"
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-resolution-result/v1" in payloads
    assert '"decision": "retry"' in payloads
    assert '"resulting_step": "fix"' in payloads


def test_unclassified_escalated_retry_cannot_enter_product_fix(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "resolve-unclassified", monkeypatch,
        stage_kind="engineering",
        stage_id="stage-engineering-resolve-unclassified")
    state = loop.load(ws)
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    resolved = loop.resolve(ws, "retry")

    assert "error" not in resolved, resolved
    successor, handoff = _successor_handoff(
        ws, store, resolved["stage_transition"])
    assert successor["stage_kind"] == "evaluate"
    payloads = _handoff_payload_text(ws, handoff)
    assert '"resulting_step": "evaluate"' in payloads


def test_replan_transition_seals_the_declared_control_output(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "replan-output", monkeypatch, stage_kind="build",
        stage_id="stage-build-replan-root")
    state = loop.load(ws)
    state.update({
        "step": "execute", "baseline": loop.tp.git_head(ws),
        "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "running",
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "clear", lambda _ws: None)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *_a, **_k: {})

    replanned = loop.replan(
        ws, by="human:owner", reason="replace the invalid task graph")

    assert "error" not in replanned, replanned
    manifest = store.load("run-cross-host-loop")
    receipts = [receipt for receipt in manifest["stage_operations"].values()
                if receipt.get("operation") == "terminalize_and_start"]
    assert len(receipts) == 1
    successor, handoff = _successor_handoff(ws, store, receipts[0])
    assert successor["stage_kind"] == "plan"
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-replan-result/v1" in payloads
    assert "replace the invalid task graph" in payloads
    assert '"to_step": "plan"' in payloads


@pytest.mark.parametrize(
    "source_case", ["absolute", "outside", "intermediate-symlink"])
def test_stage_output_sealing_rejects_untrusted_sources_without_capture(
        tmp_path, monkeypatch, source_case) -> None:
    from taskplane import review_evidence

    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / source_case, monkeypatch, stage_kind="engineering",
        stage_id=f"stage-engineering-hostile-{source_case}")
    workspace = Path(ws)
    if source_case == "absolute":
        source = str(workspace / "README.md")
    elif source_case == "outside":
        outside = workspace.parent / "outside.txt"
        outside.write_text("outside stage output\n", encoding="utf-8")
        source = "../outside.txt"
    else:
        outside = workspace.parent / "linked-output"
        outside.mkdir()
        (outside / "secret.txt").write_text(
            "symlinked stage output\n", encoding="utf-8")
        (workspace / "linked").symlink_to(outside, target_is_directory=True)
        source = "linked/secret.txt"

    state = loop.load(ws)
    state["step"] = "em"
    loop.save(ws, state)
    artifact_store = review_evidence.ArtifactStore(ws)
    before = artifact_store.references("stage-output")
    manifest_before = copy.deepcopy(store.load(stage["run_id"]))
    completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-test-decision/v1",
        step="em", outcome="approved", result={"approved": True})
    completion["_stage_output"]["sources"] = [{
        "path": source, "required": True,
    }]

    with pytest.raises(ValueError):
        loop._stage_loop_transition(
            ws, state, from_step="em", to_step="retro",
            completion=completion)

    assert artifact_store.references("stage-output") == before
    assert store.load(stage["run_id"]) == manifest_before


def test_replan_transition_identity_includes_each_predecessor_head(
        tmp_path, monkeypatch) -> None:
    ws, _store, _stage, _started = _start_real_stage_loop(
        tmp_path / "replan-heads", monkeypatch, stage_kind="build",
        stage_id="stage-build-replan-head-one")
    state = loop.load(ws)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "tests": "true",
            "deps": [], "status": "running",
        }],
    })
    loop.save(ws, state)
    replan_completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-replan-result/v1",
        step="execute", outcome="replan",
        result={"from_step": "execute", "to_step": "plan",
                "by": "human:owner", "reason": "replace invalid graph"})

    first = loop._stage_loop_transition(
        ws, state, from_step="execute", to_step="plan",
        completion=replan_completion, force=True)
    state.update({"step": "plan", "tasks": None, "current_task": 0})
    loop.save(ws, state)

    plan_dir = Path(ws) / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.md").write_text(
        "# Plan\n\nTry the corrected graph.\n", encoding="utf-8")
    tasks = [{
        "id": "t01", "scope": ["README.md"], "tests": "true",
        "deps": [], "status": "running",
    }]
    (plan_dir / "tasks.json").write_text(
        json.dumps({"tasks": tasks}, sort_keys=True) + "\n",
        encoding="utf-8")
    state.update({"step": "plan", "tasks": tasks, "current_task": 0})
    loop.save(ws, state)
    plan_completion = loop._stage_loop_gate_completion(
        ws, state, step="plan", outcome="pass")
    advanced = loop._stage_loop_transition(
        ws, state, from_step="plan", to_step="execute",
        completion=plan_completion)
    state["step"] = "execute"
    loop.save(ws, state)

    second = loop._stage_loop_transition(
        ws, state, from_step="execute", to_step="plan",
        completion=replan_completion, force=True)

    assert first["operation_id"] != second["operation_id"]
    assert first["result"]["predecessor_head"]["summary"]["stage_id"] == \
        "stage-build-replan-head-one"
    assert second["result"]["predecessor_head"]["summary"]["stage_id"] == \
        advanced["result"]["successor_head"]["summary"]["stage_id"]


def test_partial_wave_split_retry_reuses_children_and_resumes_only_missing(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "partial-split", monkeypatch, stage_kind="build",
        stage_id="stage-build-partial-split-parent")
    ready = [
        {"id": "t01", "scope": ["a/**"], "tests": "true",
         "deps": [], "status": "pending"},
        {"id": "t02", "scope": ["b/**"], "tests": "true",
         "deps": [], "status": "pending"},
    ]
    state = loop.load(ws)
    state.update({
        "step": "execute", "parallel": True, "current_task": 0,
        "tasks": ready,
    })
    loop.save(ws, state)
    real_dispatch = loop._stage_loop_dispatch
    attempted = []
    first_dispatch = {}

    def interrupt_second_dispatch(*args, **kwargs):
        attempted.append(str(kwargs.get("stage_id") or ""))
        if len(attempted) == 2:
            raise RuntimeError("crash after the first child resume")
        result = real_dispatch(*args, **kwargs)
        first_dispatch["runtime"] = copy.deepcopy(result)
        return result

    monkeypatch.setattr(
        loop, "_stage_loop_dispatch", interrupt_second_dispatch)
    with pytest.raises(RuntimeError, match="first child resume"):
        loop._stage_loop_wave_dispatches(ws, state, ready)

    after_crash = store.load(stage["run_id"])
    bindings = copy.deepcopy(loop.load(ws)["_stage_bindings"])
    child_ids = {bindings[task_id]["build"] for task_id in ("t01", "t02")}
    roots = {
        child_id: store.read_stage_object(
            stage["run_id"], after_crash["stage_heads"][child_id]["object"]
        )["execution_root_id"]
        for child_id in child_ids
    }
    split_operations = {
        operation_id for operation_id, receipt in
        after_crash["stage_operations"].items()
        if receipt.get("operation") == "split_stage"
    }
    resumed_before_retry = {
        operation_id for operation_id, receipt in
        after_crash["stage_operations"].items()
        if receipt.get("operation") == "resume_stage"
    }
    assert len(split_operations) == 1
    assert len(resumed_before_retry) == 1

    monkeypatch.setattr(loop, "_stage_loop_dispatch", real_dispatch)
    retried = loop._stage_loop_wave_dispatches(
        ws, loop.load(ws), ready)

    assert set(retried) == {"t01", "t02"}
    assert retried["t01"] == first_dispatch["runtime"]
    final = store.load(stage["run_id"])
    assert loop.load(ws)["_stage_bindings"] == bindings
    assert set(final["active_stage_projection"]["active_stage_ids"]) == \
        child_ids
    assert {
        child_id: store.read_stage_object(
            stage["run_id"], final["stage_heads"][child_id]["object"]
        )["execution_root_id"]
        for child_id in child_ids
    } == roots
    assert {
        operation_id for operation_id, receipt in
        final["stage_operations"].items()
        if receipt.get("operation") == "split_stage"
    } == split_operations
    resumed_after_retry = {
        operation_id for operation_id, receipt in
        final["stage_operations"].items()
        if receipt.get("operation") == "resume_stage"
    }
    assert resumed_before_retry < resumed_after_retry
    assert len(resumed_after_retry) == 2


def test_new_run_init_rejects_spaced_actor_without_state_or_run_mutation(
        tmp_path, monkeypatch) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    root = tmp_path / "spaced-actor"
    root.mkdir()
    workspace, store, initial = _real_pristine_run(root)
    requirement = _record_bootstrap_requirement(workspace)
    state_path = Path(loop._loop_path(str(workspace)))
    before_run = copy.deepcopy(store.load(initial["run_id"]))
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")

    refused = loop.init(
        str(workspace), "reject ambiguous actor authority",
        requirement_id=requirement["id"], by="Volodymyr Demkiv")

    assert refused["refused"] is True
    assert "actor" in refused["error"]
    assert "match" in refused["error"]
    assert not state_path.exists()
    assert store.load(initial["run_id"]) == before_run


@pytest.mark.parametrize(
    ("step", "force"), [("pm", True), ("done", False), ("done", True)])
def test_new_run_init_refuses_any_existing_singleton_even_force_or_terminal(
        tmp_path, monkeypatch, step, force) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _record_bootstrap_requirement)

    root = tmp_path / f"existing-{step}-{force}"
    root.mkdir()
    workspace = Path(_workspace(root))
    requirement = _record_bootstrap_requirement(workspace)
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    existing = loop.init(str(workspace), "preserve prior singleton history")
    assert "error" not in existing, existing
    if step == "done":
        existing["step"] = "done"
        loop.save(str(workspace), existing)
    state_path = Path(loop._loop_path(str(workspace)))
    before_state = state_path.read_bytes()
    before_files = sorted(path.name for path in state_path.parent.iterdir())
    store_root = Path(os.environ["TASKPLANE_HOME"])
    before_store = _content_inventory(store_root)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")

    refused = loop.init(
        str(workspace), "never replace existing singleton history",
        requirement_id=requirement["id"], by="human:vdemkiv", force=force)

    assert refused["refused"] is True
    assert "existing singleton" in refused["error"]
    assert state_path.read_bytes() == before_state
    assert sorted(path.name for path in state_path.parent.iterdir()) == \
        before_files
    assert _content_inventory(store_root) == before_store


@pytest.mark.parametrize("mode", ["enabled", "disabled"])
def test_bound_v4_refuses_same_id_stale_cloned_store_byte_identically(
        tmp_path, monkeypatch, mode) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / f"stale-{mode}", monkeypatch,
        stage_id=f"stage-product-stale-store-{mode}")
    state_path = Path(loop._loop_path(ws))
    state_root = state_path.parents[2]
    before_state = state_path.read_bytes()
    before_original = copy.deepcopy(store.load(stage["run_id"]))
    original_locator = loop.runtime_storage.load_workspace_locator(ws)
    alternate_home = tmp_path / f"alternate-home-{mode}"
    source_run = Path(store.home) / "runs" / stage["run_id"]
    cloned_run = alternate_home / "runs" / stage["run_id"]
    cloned_run.parent.mkdir(parents=True)
    shutil.copytree(source_run, cloned_run)
    cloned_manifest_path = cloned_run / "manifest.json"
    before_clone = cloned_manifest_path.read_bytes()
    identity = loop.runtime_storage.resolve_repository_identity(ws)
    layout = loop.runtime_storage.resolve_layout(
        identity, home=str(alternate_home), run_id=stage["run_id"])
    stale_locator = {
        **copy.deepcopy(original_locator), "home": layout.home,
        "paths": {
            "state": layout.state_root, "graph": layout.graph_root,
            "evidence": layout.evidence_root, "lenses": layout.lens_root,
            "artifacts": layout.artifact_root,
        },
    }
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: copy.deepcopy(stale_locator))
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", mode)

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "store identity changed" in refused["error"]
    assert state_path.read_bytes() == before_state
    assert store.load(stage["run_id"]) == before_original
    assert cloned_manifest_path.read_bytes() == before_clone


def test_stage_output_rejects_caller_controlled_source_workspace(
        tmp_path, monkeypatch) -> None:
    from taskplane import review_evidence

    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "caller-source", monkeypatch, stage_kind="engineering",
        stage_id="stage-engineering-caller-source")
    state = loop.load(ws)
    state["step"] = "em"
    loop.save(ws, state)
    alternate = tmp_path / "caller-controlled-source"
    alternate.mkdir()
    (alternate / "result.txt").write_text(
        "caller-selected bytes\n", encoding="utf-8")
    completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-test-decision/v1",
        step="em", outcome="approved", result={"approved": True})
    completion["_stage_output"].update({
        "source_workspace": str(alternate),
        "sources": [{"path": "result.txt", "required": True}],
    })
    artifact_store = review_evidence.ArtifactStore(ws)
    before_artifacts = artifact_store.references("stage-output")
    before_run = copy.deepcopy(store.load(stage["run_id"]))
    opened = []
    monkeypatch.setattr(
        loop, "_before_stage_output_component_open",
        lambda *_args: opened.append(_args))

    with pytest.raises(ValueError, match="workspace"):
        loop._stage_loop_transition(
            ws, state, from_step="em", to_step="retro",
            completion=completion)

    assert opened == []
    assert artifact_store.references("stage-output") == before_artifacts
    assert store.load(stage["run_id"]) == before_run


def test_stage_output_intermediate_symlink_swap_hook_fails_closed(
        tmp_path, monkeypatch) -> None:
    from taskplane import review_evidence

    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "symlink-swap", monkeypatch, stage_kind="engineering",
        stage_id="stage-engineering-symlink-swap")
    workspace = Path(ws)
    safe = workspace / "safe"
    safe.mkdir()
    (safe / "result.txt").write_text(
        "validated output\n", encoding="utf-8")
    outside = tmp_path / "swap-target"
    outside.mkdir()
    (outside / "result.txt").write_text(
        "substituted output\n", encoding="utf-8")
    state = loop.load(ws)
    state["step"] = "em"
    loop.save(ws, state)
    completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-test-decision/v1",
        step="em", outcome="approved", result={"approved": True})
    completion["_stage_output"]["sources"] = [{
        "path": "safe/result.txt", "required": True,
    }]
    artifact_store = review_evidence.ArtifactStore(ws)
    before_artifacts = artifact_store.references("stage-output")
    before_run = copy.deepcopy(store.load(stage["run_id"]))
    swaps = []

    def swap_intermediate(root, relative_path, component_index):
        if relative_path == "safe/result.txt" and component_index == 0 and \
                not swaps:
            swaps.append((root, relative_path, component_index))
            safe.rename(workspace / "safe-validated")
            safe.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        loop, "_before_stage_output_component_open", swap_intermediate)

    with pytest.raises((ValueError, OSError)):
        loop._stage_loop_transition(
            ws, state, from_step="em", to_step="retro",
            completion=completion)

    assert swaps == [(ws, "safe/result.txt", 0)]
    assert artifact_store.references("stage-output") == before_artifacts
    assert store.load(stage["run_id"]) == before_run


def test_same_kind_resolve_seals_decision_through_new_engineering_head(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "same-kind-resolve", monkeypatch,
        stage_kind="engineering", stage_id="stage-engineering-resolve-skip")
    state = loop.load(ws)
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    resolved = loop.resolve(ws, "skip")

    assert "error" not in resolved, resolved
    assert resolved["step"] == "em"
    receipt = resolved["stage_transition"]
    assert receipt["operation"] == "terminalize_and_start"
    successor, handoff = _successor_handoff(ws, store, receipt)
    assert successor["stage_kind"] == "engineering"
    assert successor["stage_id"] != stage["stage_id"]
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-resolution-result/v1" in payloads
    assert '"decision": "skip"' in payloads
    assert '"resulting_step": "em"' in payloads


def test_transition_reconciles_receipt_after_stage_commit_before_singleton(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "transition-crash", monkeypatch,
        stage_id="stage-product-transition-crash")
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nReconcile the committed Product transition.\n",
        encoding="utf-8")
    state_path = Path(loop._loop_path(ws))
    before_state = state_path.read_bytes()
    real_save = loop.save
    failures = []

    def fail_first_plan_singleton(workspace, value):
        if value.get("step") == "plan" and not failures:
            failures.append("post-stage/pre-singleton")
            raise RuntimeError("crash before singleton transition commit")
        return real_save(workspace, value)

    monkeypatch.setattr(loop, "save", fail_first_plan_singleton)
    with pytest.raises(RuntimeError, match="before singleton"):
        loop.gate.__wrapped__(ws, "pass")

    assert state_path.read_bytes() == before_state
    after_stage_commit = store.load(stage["run_id"])
    prior = [
        receipt for receipt in after_stage_commit["stage_operations"].values()
        if receipt.get("operation") == "terminalize_and_start"
    ]
    assert len(prior) == 1
    monkeypatch.setattr(loop, "save", real_save)

    retried = loop.gate.__wrapped__(ws, "pass")

    assert "error" not in retried, retried
    assert retried["step"] == "plan"
    assert retried["stage_transition"]["operation_id"] == \
        prior[0]["operation_id"]
    final = store.load(stage["run_id"])
    assert len([
        receipt for receipt in final["stage_operations"].values()
        if receipt.get("operation") == "terminalize_and_start"
    ]) == 1


def test_native_wave_ignores_historical_stage_replay_state(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "wave-pre-output", monkeypatch, stage_kind="build",
        stage_id="stage-build-wave-pre-output")
    tasks = [
        {"id": "t01", "scope": ["a/**"], "tests": "true",
         "deps": [], "status": "pending"},
        {"id": "t02", "scope": ["b/**"], "tests": "true",
         "deps": [], "status": "pending"},
    ]
    state = loop.load(ws)
    state.update({
        "step": "execute", "parallel": True, "current_task": 0,
        "tasks": tasks,
    })
    loop.save(ws, state)
    historical_manifest = copy.deepcopy(store.load(stage["run_id"]))
    monkeypatch.setattr(
        loop, "_stage_loop_wave_dispatches",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("historical StageLifecycle replay was invoked")))

    authority = open_delivery_root(ws)
    first = loop.wave(ws, root_observation_authority=authority)
    second = loop.wave(ws, root_observation_authority=authority)

    for emitted in (first, second):
        assert "error" not in emitted, emitted
        assert [entry["task"]["id"] for entry in emitted["wave"]] == [
            "t01", "t02"]
        assert emitted["wait_invocation"]["outstanding_members"] == [
            "t01", "t02"]
        assert emitted["held"] == []
        encoded = json.dumps(emitted, sort_keys=True).lower()
        assert "stage_runtime_dispatch" not in encoded
        assert "execution_root" not in encoded
        assert "replay" not in encoded
        assert "tranche" not in encoded
    assert [entry["dispatch_intent"]["intent_id"]
            for entry in first["wave"]] == [
                entry["dispatch_intent"]["intent_id"]
                for entry in second["wave"]]
    assert not (loop.load(ws) or {}).get("_stage_bindings")
    assert store.load(stage["run_id"]) == historical_manifest
