"""Public amendment producers over isolated real runs; host facts are simulated."""
import copy
from pathlib import Path
import subprocess

import pytest

from taskplane import loop, phase_amendment, phase_records, requirements, review_evidence
from taskplane.tests.phase_fixture import _supporting_pristine_phase_run
from taskplane.tests.test_stage_loop_integration import (
    collected_lens_design, collected_product_handoff, parallel_phase_mode,
)


def proposal(ws, phase="product"):
    selected = phase_amendment.candidate(loop, ws, phase, loop.load(ws)["requirement_id"])
    assert not selected.get("error"), selected
    return dict(phase=phase, by="human:simulated", reason="Use the human's smaller approved scope",
                requirement_id=selected["requirement_id"],
                expected_stage_fingerprint=selected["stage_fingerprint"],
                requirement_fingerprint=selected["requirement_fingerprint"],
                candidate_fingerprint=selected["candidate_fingerprint"], worker_stopped=True)


@pytest.fixture
def run(tmp_path, monkeypatch):
    return _supporting_pristine_phase_run(tmp_path, monkeypatch)


@pytest.mark.parametrize("terminal_step", ["done", "failed"])
def test_retro_accepts_closed_run_with_retained_amendment(run, terminal_step):
    ws, store, run_id, _ = run
    assert not phase_amendment.amend(loop, ws, **proposal(ws)).get("error")
    context = loop._stage_loop_context(ws, loop.load(ws))
    stage = context["stage"]
    context["lifecycle"].terminalize(
        run_id, stage_id=stage["stage_id"],
        expected_head_fingerprint=stage["fingerprint"],
        expected_revision=context["manifest"]["revision"],
        operation_id="human-manual-completion", outcome="closed",
        actor="human:simulated", terminalized_at="2026-09-11T13:00:00Z",
        reason_code="human_manual_completion", reason="Finish outside governed stages",
    )
    with loop.mutate(ws) as state:
        state["step"] = terminal_step
    before = store.load(run_id)
    state = loop.load(ws)
    assert state["phase_amendment"]
    assert loop._stage_loop_context(ws, state)["stage"] is None
    assert phase_amendment.current(loop, ws, state) is None
    assert loop._phase_bridge_pending(ws, state) is None
    assert loop._phase_bridge_retro_completion(ws, state) is None
    assert store.load(run_id) == before
    report = loop.retro(ws)
    assert not report.get("error"), report
    assert loop.load(ws)["step"] == terminal_step
    assert loop.load(ws)["phase_amendment"] == state["phase_amendment"]
    assert store.load(run_id)["stage_heads"] == before["stage_heads"]
    assert loop.retro(ws).get("replayed") is True
    assert loop.load(ws)["step"] == terminal_step


def test_product_amendment_rebinds_scope_and_preserves_history_idempotently(run):
    ws, store, run_id, requirement = run
    old = store.load(run_id)
    original = loop._stage_loop_context(ws, loop.load(ws))["stage"]
    requirements.amend_requirement(ws, requirement["id"], acceptance=["Only the requested setup"])
    args = proposal(ws)
    before_preview = store.load(run_id)
    assert proposal(ws) == args
    assert store.load(run_id) == before_preview
    result = phase_amendment.amend(loop, ws, **args)
    assert not result.get("error"), result
    state = loop.load(ws)
    assert state["step"] == "design"
    context = loop._phase_bridge_context(ws, state)
    assert context["stage"]["stage_id"] != original["stage_id"]
    assert context["stage"]["requirement"]["fingerprint"] == args["requirement_fingerprint"]
    assert context["stage"]["authority"]["authority_revision"] == original["authority"]["authority_revision"] + 1
    retired = loop._indexed_stage(store, store.load(run_id), run_id, original["stage_id"])
    assert retired["outcome"] == "closed"
    assert retired["requirement"] == original["requirement"]
    assert phase_records.phase_records(store.load(run_id)) == phase_records.phase_records(old)
    saved = store.load(run_id)
    repeated = phase_amendment.amend(loop, ws, **args)
    assert repeated.get("replay") is True, repeated
    assert store.load(run_id) == saved
    package = loop.phase_harness.input_package(loop, context)
    assert package.read("requirement")["acceptance_criteria"] == ["Only the requested setup"]
    packet = package.read("lens-evidence")
    assert packet["entries"] == []
    assert len(packet["human_amendments"]) == 1
    amendment = phase_amendment.current(loop, ws, state)
    assert review_evidence.ArtifactStore(ws).read(amendment["previous_workflow"]) == old["workflow"]
    handoff = loop._verified_stage_handoff(context["lifecycle"], store, context["manifest"], context["stage"])
    assert handoff["requirement"] == context["stage"]["requirement"]


@pytest.mark.parametrize("foreign_owner", ["store", "lifecycle"])
def test_amendment_uses_the_actual_flat_launcher_store_owner(run, monkeypatch, foreign_owner):
    import loop as launcher_loop

    ws, store, run_id, _ = run
    assert not launcher_loop.__package__
    assert launcher_loop.run_store_engine.RunStore is not loop.run_store_engine.RunStore
    context = launcher_loop._stage_loop_context(ws, launcher_loop.load(ws))
    assert isinstance(context["store"], launcher_loop.run_store_engine.RunStore)
    assert not isinstance(context["store"], loop.run_store_engine.RunStore)
    packaged = loop._stage_loop_context(ws, loop.load(ws))
    assert type(context["lifecycle"]) is context["stage_entities"].StageLifecycle
    assert type(context["lifecycle"]) is not type(packaged["lifecycle"])
    args = proposal(ws)
    before = store.load(run_id)
    snapshot = phase_amendment._snapshot

    def foreign_context(*values, **kwargs):
        selected = snapshot(*values, **kwargs)
        # The other module's real owner is still foreign to this runtime.
        selected["context"][foreign_owner] = packaged[foreign_owner]
        return selected

    with monkeypatch.context() as patch:
        patch.setattr(phase_amendment, "_snapshot", foreign_context)
        refused = phase_amendment.amend(launcher_loop, ws, **args)
    expected = "run store" if foreign_owner == "store" else "stage lifecycle"
    assert refused["error"] == f"amendment requires the {expected} owner", refused
    assert store.load(run_id) == before
    result = phase_amendment.amend(launcher_loop, ws, **args)
    assert not result.get("error"), result
    assert launcher_loop.load(ws)["step"] == "design"
    saved = store.load(run_id)
    assert phase_amendment.amend(launcher_loop, ws, **args).get("replay") is True
    assert store.load(run_id) == saved
    assert phase_records.phase_records(saved) == phase_records.phase_records(before)


def _commit_source_change(ws, path):
    source = Path(ws) / path
    source.write_text(source.read_text() + "\n# Current approved implementation revision\n")
    for args in (("add", path), ("-c", "user.name=Fixture", "-c",
            "user.email=fixture@example.invalid", "commit", "-qm", "approved source change")):
        subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)
    return loop.tp.git_head(ws)


def test_design_amendment_binds_real_committed_revision_with_strict_parent_authority(collected_lens_design):
    ws, store, run_id, artifacts, _ = collected_lens_design
    state = loop.load(ws)
    parent = loop._phase_bridge_context(ws, state)
    old_stage = copy.deepcopy(parent["stage"])
    old_records = phase_records.phase_records(store.load(run_id))
    args = proposal(ws, "design")
    revision = _commit_source_change(ws, "app.py")
    assert revision != old_stage["authority"]["worktree_revision"]
    # The ordinary phase path remains strict. Only the explicit human
    # amendment can propose the observed new revision to that same validator.
    with pytest.raises(RuntimeError, match="binding changed: worktree_revision"):
        loop._phase_bridge_authorize(ws, parent, store.load(run_id))
    result = phase_amendment.amend(loop, ws, **args)
    assert not result.get("error"), result
    current = loop._phase_bridge_context(ws, loop.load(ws))
    assert current["stage"]["authority"]["worktree_revision"] == revision
    loop._phase_bridge_authorize(ws, current, store.load(run_id))
    for field in ("actor", "repository_id", "repository_key", "worktree_id",
                  "run_id", "target_revision"):
        assert current["stage"]["authority"][field] == old_stage["authority"][field]
    decision = phase_amendment.current(loop, ws, loop.load(ws))
    assert decision["authority"] == current["stage"]["authority"]
    assert artifacts.read(decision["previous_workflow"]) == state
    assert phase_records.phase_records(store.load(run_id)) == old_records
    retired = loop._indexed_stage(store, store.load(run_id), run_id, old_stage["stage_id"])
    assert retired["authority"] == old_stage["authority"]
    assert retired["outcome"] == "closed" and retired["terminal"]["completed_deliverables"] == []
    before = store.load(run_id)
    assert phase_amendment.amend(loop, ws, **args).get("replay") is True
    assert store.load(run_id) == before
    assert loop.load(ws)["step"] == "design_approval"


@pytest.mark.parametrize("field,foreign", [
    ("repository_id", "github.com/foreign/repository"),
    ("run_id", "foreign-run"),
    ("authority_fingerprint", "f" * 64),
])
def test_revision_amendment_refuses_foreign_live_identity(run, monkeypatch, field, foreign):
    ws, store, run_id, _ = run
    args = proposal(ws)
    _commit_source_change(ws, "README.md")
    resolve = loop._current_stage_authority
    def foreign_fact(*values, **kwargs):
        observed = resolve(*values, **kwargs)
        return {**observed, field: foreign}
    # Substitute one observed fact, never the real strict authority validator.
    monkeypatch.setattr(loop, "_current_stage_authority", foreign_fact)
    before = store.load(run_id)
    result = phase_amendment.amend(loop, ws, **args)
    assert "binding changed: " + field in result["error"], result
    assert store.load(run_id) == before


def test_revision_amendment_refuses_commit_movement_inside_transaction(run, monkeypatch):
    ws, store, run_id, _ = run
    args = proposal(ws)
    approved_revision = _commit_source_change(ws, "README.md")
    scan = phase_amendment._workers
    def commit_after_authorization(*values, **kwargs):
        workers = scan(*values, **kwargs)
        assert _commit_source_change(ws, "README.md") != approved_revision
        return workers
    monkeypatch.setattr(phase_amendment, "_workers", commit_after_authorization)
    before = store.load(run_id)
    result = phase_amendment.amend(loop, ws, **args)
    assert "source revision changed during amendment" in result["error"], result
    assert store.load(run_id) == before


@pytest.mark.parametrize("changed", ["actor", "candidate", "stage", "requirement", "worker"])
def test_amendment_rejects_wrong_authority_without_changing_run(run, monkeypatch, changed):
    ws, store, run_id, _ = run
    args = proposal(ws)
    if changed == "actor": args["by"] = "human:foreign"
    if changed == "candidate": args["candidate_fingerprint"] = "a" * 64
    if changed == "stage": args["expected_stage_fingerprint"] = "b" * 64
    if changed == "requirement": args["requirement_fingerprint"] = "c" * 64
    if changed == "worker": monkeypatch.setattr(loop.tp, "task_slot", lambda: "worker")
    before = store.load(run_id)
    result = phase_amendment.amend(loop, ws, **args)
    assert result.get("error"), result
    assert store.load(run_id) == before


def test_live_worker_requires_actual_stop_even_with_human_flag(run):
    ws, store, run_id, _ = run
    dispatched = loop.next_action(ws)
    assert not dispatched.get("error"), dispatched
    loop.tp.bind_worker_contract_event(ws, {"session_id": "simulated", "agent_id": "live-child",
        "agent_type": dispatched["obligations"]["task_name"], "task_name": dispatched["obligations"]["task_name"]})
    args = proposal(ws)
    before = store.load(run_id)
    result = phase_amendment.amend(loop, ws, **args)
    assert result.get("needs_stop"), result
    assert result["needs_stop"][0]["owner"]["agent_id"] == "live-child"
    assert store.load(run_id) == before


def test_pending_worker_is_retired_as_amendment_without_native_pass(run):
    ws, _, _, _ = run
    dispatched = loop.next_action(ws)
    slot = dispatched["obligations"]["contract_bootstrap"]["task_slot"]
    result = phase_amendment.amend(loop, ws, **proposal(ws))
    assert not result.get("error"), result
    assert not Path(loop.tp.active_contract_path(ws, slot)).exists()
    retired = loop.tp.load_json(loop.tp._worker_terminal_path(ws, slot))
    assert retired["outcome"] == "interruption"
    assert retired["authority"] == "orphan-recovery"
    assert retired["submission_status"].startswith("human-authorized-phase-amendment:")
    with pytest.raises(loop.tp.StateError, match="quarantine lacks its exact native terminal"):
        loop.tp.released_worker_contract(ws, slot)


def test_worker_rebind_between_preview_and_commit_is_refused(run, monkeypatch):
    ws, store, run_id, _ = run
    dispatched = loop.next_action(ws)
    args = proposal(ws)
    scan = phase_amendment._workers
    def race(runtime, workspace, state):
        result = scan(runtime, workspace, state)
        loop.tp.bind_worker_contract_event(ws, {"session_id": "simulated", "agent_id": "racing-child",
            "agent_type": dispatched["obligations"]["task_name"], "task_name": dispatched["obligations"]["task_name"]})
        return result
    monkeypatch.setattr(phase_amendment, "_workers", race)
    original_heads = store.load(run_id)["stage_heads"]
    result = phase_amendment.amend(loop, ws, **args)
    assert "worker changed before amendment" in result["error"]
    assert store.load(run_id)["stage_heads"] == original_heads
    assert "phase_amendment" not in loop.load(ws)


def test_amendment_workflow_failure_rolls_back_stage_index(run, monkeypatch):
    ws, store, run_id, _ = run
    args = proposal(ws)
    before = store.load(run_id)
    monkeypatch.setattr(loop, "save", lambda *_: (_ for _ in ()).throw(OSError("interrupted workflow save")))
    result = phase_amendment.amend(loop, ws, **args)
    assert "interrupted workflow save" in result["error"]
    assert store.load(run_id) == before


def test_interrupted_amendment_cleanup_blocks_dispatch_until_exact_replay(run, monkeypatch):
    ws, _, _, _ = run
    loop.next_action(ws)
    args = proposal(ws)
    with monkeypatch.context() as patch:
        patch.setattr(phase_amendment, "_cleanup", lambda *_, **__: (_ for _ in ()).throw(OSError("interrupted cleanup")))
        result = phase_amendment.amend(loop, ws, **args)
    assert "interrupted cleanup" in result["error"]
    with pytest.raises(ValueError, match="cleanup incomplete"):
        loop._phase_bridge_pending(ws, loop.load(ws))
    replayed = phase_amendment.amend(loop, ws, **args)
    assert replayed.get("replay") is True, replayed
    assert loop._phase_bridge_pending(ws, loop.load(ws)) is None


def test_build_effects_must_be_reconciled_then_amendment_preserves_work(run):
    from taskplane import delivery_ports, loop_recovery
    ws, _, run_id, _ = run
    stage_id = loop._stage_loop_context(ws, loop.load(ws))["stage"]["stage_id"]
    owner = loop_recovery.LeaseRecovery(ws, mutate_state=loop.mutate,
        clock=delivery_ports.FakeClock(wall_time=100), authorize=lambda _: True,
        usage=lambda: {"tokens": 0}, limits={"tokens": 10}, stage_id=stage_id)
    lease = delivery_ports.AttemptLease("lease-amend", run_id, "build", "attempt-amend", "op-amend",
        "simulated-owner", 100, 150, 140, ("workspace:work.py",), 1)
    owner.admit(lease, expected_fence=0)
    code = Path(ws) / "work.py"
    owner.execute(lease, nonce_action=lambda action: action(),
                  action=lambda _: code.write_text("keep_real_local_effect = True\n"))
    with loop.mutate(ws) as state:
        state["step"] = "execute"
    blocked = phase_amendment.amend(loop, ws, **proposal(ws))
    assert blocked.get("needs_reconciliation"), blocked
    # A simulated host observation crosses the real existing lease owner.
    owner.reconcile(lease, delivery_ports.observe_lease_terminal(lease, lambda _: {
        "released": True, "terminal_identity": "simulated-stopped-build",
        "effects": {"workspace:work.py": "observed"}}))
    result = phase_amendment.amend(loop, ws, **proposal(ws))
    assert not result.get("error"), result
    assert code.read_text() == "keep_real_local_effect = True\n"
    amendment = phase_amendment.current(loop, ws, loop.load(ws))
    previous = review_evidence.ArtifactStore(ws).read(amendment["previous_workflow"])
    assert previous["attempt_leases"][stage_id]["released"] is True


def test_approved_amendment_candidate_drift_requires_new_amendment(run):
    ws, _, _, requirement = run
    args = proposal(ws)
    assert not phase_amendment.amend(loop, ws, **args).get("error")
    requirements.amend_requirement(ws, requirement["id"], acceptance=["A different change"])
    with pytest.raises(ValueError, match="candidate changed"):
        phase_amendment.current(loop, ws, loop.load(ws))
    changed = proposal(ws)
    assert not phase_amendment.amend(loop, ws, **changed).get("error")
    assert len(loop.load(ws)["phase_amendment_history"]) == 2


@pytest.mark.parametrize("embedded_strategy", [True, False])
def test_design_amendment_preserves_old_reviews_and_requires_approval_before_plan(collected_lens_design, monkeypatch, embedded_strategy):
    import json
    from taskplane.tests.phase_fixture import _emit_host_hook, phase_pending, write_lens_results
    ws, store, run_id, artifacts, completion = collected_lens_design
    before = store.load(run_id)
    # The supported inline Design schema embeds its exact test strategy.
    design_path = Path(ws) / "design/contract.json"
    design = json.loads(design_path.read_text())
    if embedded_strategy:
        design["test_strategy"] = json.loads((Path(ws) / "design/test-strategy.json").read_text())
        design.pop("test_strategy_reference")
        design_path.write_text(json.dumps(design))
    args = proposal(ws, "design")
    result = phase_amendment.amend(loop, ws, **args)
    assert not result.get("error"), result
    state = loop.load(ws)
    assert state["step"] == "design_approval"
    assert "design_approved_by" not in state
    assert loop._design_dod_errors(ws, state) == []
    assert phase_records.phase_records(store.load(run_id)) == phase_records.phase_records(before)
    approved = loop.approve(ws, by="human:simulated")
    assert not approved.get("error"), approved
    assert approved["step"] == "plan"
    context = loop._phase_bridge_context(ws, loop.load(ws))
    package = loop.phase_harness.input_package(loop, context)
    assert package.read("design")["requirement"] == state["requirement_id"]
    assert package.read("lens-evidence")["entries"] == []
    assert artifacts.read(completion["runtime_result"])["status"] == "accepted"
    task = {"id": "T1", "scope": ["app.py"], "criteria": package.read("requirement")["acceptance_criteria"],
        "test_contract": {"changed_producers": ["app.py"]},
        "acceptance_refs": package.read("requirement")["acceptance_criteria"], "contracts": [],
        "tests": "python3 -m pytest -q test_app.py::test_greeting", "deps": [],
        "test_strategy_authority": {"schema": "taskplane.plan-test-strategy-reference/v1",
            "path": "design/test-strategy.json",
            "strategy_fingerprint": package.read("test-strategy")["contract_fingerprint_sha256"],
            "criterion_ids": ["AC-T11"], "changed_producer_ids": ["spec-package"]}}
    sealed = loop.seal_phase_plan_task(package.store, package, loop.load(ws), task)
    assert sealed["task"]["test_strategy_authority_receipt"]["package_binding"]["run_id"] == run_id
    dispatched = loop.next_action(ws)
    assert not dispatched.get("error"), dispatched
    assert "stage_runtime_dispatch" in dispatched, dispatched
    inputs = loop.phase_harness.read_input(loop, ws, dispatched["stage_runtime_dispatch"])
    assert inputs["phase_definition"]["id"] == "plan"
    assert inputs["requirement"]["acceptance_criteria"] == loop.reqs.get_requirement(ws, state["requirement_id"])["acceptance"]
    assert _emit_host_hook(ws, dispatched, "SubagentStart", monkeypatch) == 0
    plan_dir = Path(ws) / "plan"
    plan_dir.mkdir(exist_ok=True)
    raw_plan = {"requirement": state["requirement_id"], "delivery_mode": "build", "tasks": [task]}
    if embedded_strategy:
        raw_plan.pop("delivery_mode")  # The authenticated engine owns Build mode.
    (plan_dir / "tasks.json").write_text(json.dumps(raw_plan))
    (plan_dir / "plan.md").write_text("Preserve the greeting and verify its approved selectors.\n")
    assert "test_strategy_authority_receipt" not in task
    selected = loop.phase_harness.collect_lenses(loop, ws, dispatched["stage_runtime_dispatch"], prepare=True)
    assert selected["dispatch"]
    plan = artifacts.read(selected["plan"])
    envelope = artifacts.read(plan["envelope"])
    reviewed_task = envelope["diff"]["phase_inputs"]["candidate"]["plan-task"]["plan"]["tasks"][0]
    assert reviewed_task["test_strategy_authority_receipt"]["package_binding"]["authority_fingerprint"] == context["stage"]["authority"]["authority_fingerprint"]
    assert json.loads((plan_dir / "tasks.json").read_text()) == raw_plan
    write_lens_results(artifacts, selected["plan"])
    # Workers execute across real elapsed time. A later saved scan has new
    # timestamps/duration, while its source/content/edges stay unchanged.
    import time
    from taskplane import phase_harness
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 338)
    graph = loop.depgraph.load(ws)
    graph["meta"]["scanned_at"] += 338
    graph["meta"]["updated_at"] += 338
    coverage = graph["meta"]["source_coverage"]
    for touchpoint in coverage["touchpoints"].values():
        touchpoint["elapsed_ms"] += 157
    coverage["fingerprint"] = review_evidence.content_fingerprint(
        {key: value for key, value in coverage.items() if key != "fingerprint"})
    loop.depgraph.save(ws, graph)
    # A fresh BFS may select a different representative edge to the same
    # reachable node. Collection must consume its reviewed impact instead.
    changed_impact = copy.deepcopy(envelope["impact"])
    changed_impact["graph"] = copy.deepcopy(graph["meta"])
    changed_impact["impacted"] = {"1": [{"module": "app", "via": "README", "kind": "imports"}]}
    monkeypatch.setattr(loop.depgraph, "impact", lambda *args, **kwargs: changed_impact)
    # Reproduce an earlier collector's empty duplicate, retaining original
    # submissions. Selection must use submitted identity, never verdicts.
    with monkeypatch.context() as legacy:
        legacy.setattr(phase_harness, "_prepared_plan", lambda *args: None)
        legacy.setattr(loop.depgraph, "scan", lambda *args, **kwargs: graph)
        duplicate = phase_harness.collect_lenses(loop, ws, dispatched["stage_runtime_dispatch"], prepare=True)
    assert duplicate["plan"]["fingerprint"] != selected["plan"]["fingerprint"]
    material_row = phase_records.phase_records(store.load(run_id))[phase_harness.operation_id(context)]
    material = artifacts.read(material_row["result"]["reference"])
    current_plan = loop.seal_phase_plan_task(package.store, package, loop.load(ws), raw_plan)
    for field in ("candidate_fingerprint", "authority_fingerprint", "operation_id"):
        foreign = copy.deepcopy(material)
        foreign["bindings"][field] = "f" * 64
        assert phase_harness._prepared_plan(loop, context, foreign, current_plan) is None
    changed_plan = copy.deepcopy(current_plan)
    changed_plan["plan"]["tasks"][0]["tests"] += " --verbose"
    assert phase_harness._prepared_plan(loop, context, material, changed_plan) is None
    with monkeypatch.context() as changed_source:
        different_graph = copy.deepcopy(graph)
        different_graph["meta"]["content_fingerprint"] = "f" * 64
        changed_source.setattr(loop.depgraph, "load", lambda *args: different_graph)
        assert phase_harness._prepared_plan(loop, context, material, current_plan) is None
    with monkeypatch.context() as changed_route:
        route = loop._focused_stage_route
        def other_route(*args, **kwargs):
            decision, routing, request = route(*args, **kwargs)
            routing = copy.deepcopy(routing)
            selected_lens = next(row for row in routing["lenses"] if row["tier"] != "n/a")
            selected_lens.setdefault("evidence", []).append("A changed authorized routing basis")
            return decision, routing, request
        changed_route.setattr(loop, "_focused_stage_route", other_route)
        rerouted = phase_harness.prepare_lenses(loop, context, material, inputs,
            copy.deepcopy(envelope["diff"]["phase_inputs"]["candidate"]))
        assert rerouted["fingerprint"] != selected["plan"]["fingerprint"]
    monkeypatch.setattr(loop.depgraph, "scan", lambda *args, **kwargs:
        pytest.fail("a prepared Plan must not rescan after its review workers execute"))
    collected = phase_harness.collect_lenses(loop, ws, dispatched["stage_runtime_dispatch"])
    assert collected["status"] == "complete", collected
    assert collected["report"] == artifacts.read(collected["collection"])
    reused = phase_harness.collect_lenses(loop, ws, dispatched["stage_runtime_dispatch"], prepare=True)
    assert reused["dispatch"] == []
    assert reused["report"] == collected["report"]
    selected_portable = review_evidence.portable_artifact_reference(artifacts, selected["plan"])
    assert collected["plan"] == selected_portable
    assert artifacts.read(collected["plan"])["slots"] == plan["slots"]
    assert _emit_host_hook(ws, dispatched, "SubagentStop", monkeypatch, omit_lens_results=True) == 0
    new_completion = phase_pending(ws)["completion"]
    assert artifacts.read(new_completion["runtime_result"])["status"] == "accepted"
    handoff = artifacts.read(new_completion["handoff"])
    lens_ref = next(row["reference"] for row in handoff["produced_artifacts"] if row["artifact_class"] == "lens-evidence")
    packet = artifacts.read(lens_ref)
    assert len(packet["human_amendments"]) == 1
    assert len(packet["entries"]) == 1  # Only the new Plan reviews; no old Design passes.
    assert packet["entries"][0]["plan"] == selected_portable
    result = artifacts.read(new_completion["runtime_result"])
    for row in result["collected_output_references"]:
        if row["kind"] in envelope["diff"]["phase_inputs"]["candidate"]:
            assert artifacts.read(row) == envelope["diff"]["phase_inputs"]["candidate"][row["kind"]]
    before_projection = (plan_dir / "tasks.json").read_bytes()
    mode = loop._plan_delivery_mode_from_file(ws, loop.load(ws), apply=False)
    assert mode["mode"] == "build" and mode["automatic_lenses"] == []
    assert mode["plan_authority"] == "phase:" + run_id + ":" + context["stage"]["stage_id"] + ":" + context["stage"]["authority"]["authority_fingerprint"]
    assert (plan_dir / "tasks.json").read_bytes() == before_projection


@pytest.mark.parametrize("declaration,expected_error", [
    ({"automatic_lenses": [], "plan_authority": "phase:current"}, None),
    ({"delivery_mode": "evaluate"}, "delivery_mode=build"),
    ({"delivery_mode": None}, "delivery_mode=build"),
    ({"automatic_lenses": ["architecture"]}, "automatic_lenses=\\[\\]"),
])
def test_plan_mode_projection_preserves_explicit_declarations(tmp_path, monkeypatch, declaration, expected_error):
    import json
    from types import SimpleNamespace
    path = tmp_path / "plan/tasks.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"requirement": "R-0001", **declaration}))
    original = path.read_bytes()
    state = {"requirement_id": "R-0001", "design_fingerprint": "d" * 64}
    build = {"working_lenses": [], "evaluation_lenses": []}
    context = {"run_id": "run-current", "stage": {"stage_kind": "plan", "stage_id": "stage-current",
        "authority": {"authority_fingerprint": "a" * 64}},
        "registry": SimpleNamespace(admit=lambda *args: SimpleNamespace(to_dict=lambda: build))}
    checked = []
    monkeypatch.setattr(loop, "_phase_bridge_context", lambda *args: context)
    monkeypatch.setattr(loop, "_phase_bridge_gate_check", lambda *args: checked.append(True))
    monkeypatch.setattr(loop.tp, "git_head", lambda *args: "b" * 40)
    if expected_error:
        with pytest.raises(loop.delivery_policy.DeliveryPolicyError, match=expected_error):
            loop._plan_delivery_mode_from_file(str(tmp_path), state, apply=True)
        assert "delivery_mode_receipt" not in state
    else:
        receipt = loop._plan_delivery_mode_from_file(str(tmp_path), state, apply=True)
        assert receipt["mode"] == "build"
        assert receipt["plan_authority"] == declaration["plan_authority"]
    assert checked == [True]
    assert path.read_bytes() == original


@pytest.mark.parametrize("step", ["execute", "evaluate", "em", "signoff", "plan_approval"])
def test_late_amendment_preserves_worktree_results_and_invalidates_approvals(run, step, monkeypatch):
    ws, store, run_id, _ = run
    # The isolated test seeds a workflow origin only; it claims no Build/review success.
    worktree = Path(ws).parent / "retained-worktree"
    worktree.mkdir()
    code = worktree / "work.py"
    code.write_text("user_work = 'keep me'\n")
    with loop.mutate(ws) as state:
        state.update(step=step, tasks=[{"id": "T1", "workspace": str(worktree), "status": "passed",
                     "evaluation": {"verdict": "simulated-old-result"}}],
                     design_approved_by="human:simulated", design_fingerprint="d" * 64,
                     plan_fingerprint="e" * 64, signoff_evidence={"stale": True})
    baseline = loop.tp.git_head(ws)
    source = Path(ws) / "work.py"
    source.write_text("built = True\n")
    subprocess.run(["git", "add", "work.py"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "Simulated built source"], cwd=ws, check=True)
    built = loop.tp.git_head(ws)
    with loop.mutate(ws) as state:
        state["baseline"] = baseline
    old = loop.load(ws)
    result = phase_amendment.amend(loop, ws, **proposal(ws))
    assert not result.get("error"), result
    current = loop.load(ws)
    assert current["step"] == "design"
    assert current["tasks"] == []
    assert not {"design_approved_by", "design_fingerprint", "plan_fingerprint", "signoff_evidence"} & current.keys()
    receipt = phase_amendment.current(loop, ws, current)
    assert review_evidence.ArtifactStore(ws).read(receipt["previous_workflow"])["tasks"] == old["tasks"]
    assert code.read_text() == "user_work = 'keep me'\n"
    # Simulate renewed Plan's execution snapshot, without changing source again.
    # Review must retain the original comparison, not hide the built file.
    current["baseline"] = built
    current["tasks"] = [{"id": "T1", "req": current["requirement_id"], "scope": ["work.py"]}]
    for consumer in ("evaluate", "em", "signoff"):
        assert loop._review_baseline(ws, current, consumer) == baseline
    assert "work.py" in loop._diff_files(ws, loop._review_baseline(ws, current, "evaluate"))
    observed = []
    monkeypatch.setattr(loop.depgraph, "scope_modules", lambda *args: [])
    monkeypatch.setattr(loop.depgraph, "completion", lambda workspace, files, **kw: observed.append(files) or {})
    loop._task_graph_dod(ws, current, current["tasks"][0])
    assert observed == [["work.py"]]
    links = []
    monkeypatch.setattr(loop.depgraph, "link_requirement", lambda workspace, rid, files, **kw: links.append(files))
    monkeypatch.setattr(loop.depgraph, "scan", lambda *args: {})
    loop._true_up_graph(ws, current)
    assert links == [["work.py"]]
    monkeypatch.setattr(loop, "_engineering_review_errors", lambda *args, **kw: [])
    monkeypatch.setattr(loop.kb, "lint", lambda *args: [])
    terminal = loop._compute_signoff_dod(ws, current)
    assert terminal["baseline"] == baseline
    assert not any(error.startswith("diff_scope:") for error in terminal["errors"])
    excluded = copy.deepcopy(current)
    excluded["tasks"][0]["scope"] = ["other.py"]
    refused = loop._compute_signoff_dod(ws, excluded)
    assert any(error.startswith("diff_scope:") and "work.py" in error for error in refused["errors"])
    assert current["baseline"] == built  # Execution authority stays current.
    foreign = copy.deepcopy(current)
    foreign["phase_amendment"]["stage_id"] = "foreign-stage"
    with pytest.raises(ValueError, match="comparison"):
        loop._review_baseline(ws, foreign, "evaluate")
    # A new unchanged-source amendment makes the old projection stale.
    with loop.mutate(ws) as state:
        state["baseline"] = built
    assert not phase_amendment.amend(loop, ws, **proposal(ws)).get("error")
    latest = loop.load(ws)
    assert loop._review_baseline(ws, latest, "signoff") == baseline
    stale = copy.deepcopy(latest)
    stale["phase_amendment"] = current["phase_amendment"]
    with pytest.raises(ValueError, match="comparison"):
        loop._review_baseline(ws, stale, "evaluate")


@pytest.mark.parametrize("malformed", ["lifecycle", "tasks"])
def test_amendment_refuses_malformed_boundary_without_writes(run, monkeypatch, malformed):
    ws, store, run_id, _ = run
    args = proposal(ws)
    before = store.load(run_id)
    if malformed == "lifecycle":
        resolve = loop._stage_loop_context
        def invalid_context(*values, **kwargs):
            return {**resolve(*values, **kwargs), "lifecycle": object()}
        monkeypatch.setattr(loop, "_stage_loop_context", invalid_context)
        expected = "stage lifecycle owner"
    else:
        load = loop.load
        def invalid_workflow(*values, **kwargs):
            return {**load(*values, **kwargs), "tasks": ["not a task object"]}
        monkeypatch.setattr(loop, "load", invalid_workflow)
        expected = "tasks must be an object"
    result = phase_amendment.amend(loop, ws, **args)
    assert expected in result["error"], result
    assert result["dispatch_allowed"] is False
    assert store.load(run_id) == before
