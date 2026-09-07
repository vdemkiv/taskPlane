"""T15C opt-in bridge, crash custody, rollback and package-retention checks.

Host authorship and current authority are simulated in isolated workspaces.
The ordinary Product path is connected; adapter/package fixtures do not prove
an all-phase native journey. Retro's missing production telemetry bridge stays
an explicit refusal, and the native diagnostic remains non-ready.
"""
import pytest
import io
import json
import hashlib
from pathlib import Path

from taskplane import loop, review_evidence, run_store, stage_entities, stage_handoff, stage_migration, storage
from taskplane.tests.test_r0001_phase_agents_spec import _journey
from taskplane.tests.test_r0001_native_entry import _request, _snapshot
from taskplane import design_host_transport
from taskplane import taskplane_lite


def _normal_phase_workspace(tmp_path, monkeypatch, *, stage_kind="product"):
    from taskplane.tests.test_stage_loop_integration import _start_real_stage_loop
    from taskplane.tests.test_r0001_phase_agents_spec import _registry
    ws, store, stage, _ = _start_real_stage_loop(tmp_path, monkeypatch, stage_kind=stage_kind)
    artifacts = review_evidence.ArtifactStore(ws)
    registry = _registry()
    configuration = {"definition_source": "agents/spec-phase-definitions.json",
        "definition_set_fingerprint": registry.definition_set_fingerprint,
        "knowledge_reference": artifacts.put("phase-knowledge", {"facts": []}),
        "candidate_fingerprint": "a" * 64, "target_revision": stage["authority"]["target_revision"],
        "host_kind": "simulated", "host_version": "local-test",
        "output_paths": {"product": {"requirement": "specs/requirement.json"},
            "design": {"design": "design/contract.json", "test-strategy": "design/test-strategy.json"},
            "plan": {"plan-task": "plan/phase-task.json"}}}
    current = store.load(stage["run_id"])
    context = loop._stage_loop_context(ws, loop.load(ws))
    authorize = lambda fresh: loop._phase_bridge_authorize(ws, context, fresh)
    receipt = stage_migration.change_phase_routing(store, stage["run_id"],
        owner="agent-runtime", configuration=configuration, expected_previous=None,
        expected_revision=current["revision"], operation_id="enable-phase-runtime", validate_authority=authorize)
    return ws, store, stage, artifacts, receipt, authorize


def test_normal_command_phase_dispatch(tmp_path, monkeypatch):
    ws, _, _, _, _, _ = _normal_phase_workspace(tmp_path, monkeypatch)
    requested = loop.next_action(ws)
    assert "error" not in requested, requested
    assert requested["phase_runtime"]["status"] == "pending"
    assert loop.next_action(ws)["phase_runtime"]["status"] == "pending"


def test_normal_retro_missing_telemetry_cannot_fall_through_to_legacy_seal(tmp_path, monkeypatch):
    ws, store, stage, _, _, _ = _normal_phase_workspace(tmp_path, monkeypatch, stage_kind="retro")
    state = loop.load(ws)
    state["step"] = "retro"
    loop.save(ws, state)
    before = store.load(stage["run_id"])
    assert "sealed terminal telemetry" in loop.next_action(ws)["error"]
    assert "telemetry-gated completion" in loop.retro(ws)["error"]
    assert store.load(stage["run_id"]) == before
    assert loop.load(ws) == state


def _host_event(ws, requested, kind):
    """Explicit simulated native boundary, never native proof."""
    name = requested["task_name"]
    event = {"hook_event_name": kind, "cwd": ws, "session_id": "simulated-session",
        "turn_id": "simulated-turn", "agent_id": "simulated-worker", "agent_type": name,
        "task_name": name, "outcome": "success", "usage": {"total_tokens": 100}}
    identity = taskplane_lite.hook_event_identity(ws,
        "subagent-start" if kind == "SubagentStart" else "subagent-stop", event)
    event["_taskplane_hook_claim_id"] = hashlib.sha256(identity.encode()).hexdigest()
    return event


def _emit_host_hook(ws, requested, kind, monkeypatch, **overrides):
    from taskplane import tp as cli
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    # A native hook is a host event, not this builder's governed CLI process.
    monkeypatch.delenv("TASKPLANE_TASK", raising=False)
    event = _host_event(ws, requested, kind)
    event.update(overrides)
    identity = taskplane_lite.hook_event_identity(ws,
        "subagent-start" if kind == "SubagentStart" else "subagent-stop", event)
    event["_taskplane_hook_claim_id"] = hashlib.sha256(identity.encode()).hexdigest()
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    function = cli.cmd_subagent_start if kind == "SubagentStart" else cli.cmd_subagent_stop
    return function(None)


def _authored_requirement(ws, stage):
    # Simulated producer authors only its own declared raw candidate. The
    # production hook must create all artifacts, receipts and handoffs.
    path = Path(ws) / "specs/requirement.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"schema": "taskplane.requirement/v1",
        "id": stage["requirement"]["id"], "acceptance_criteria": ["real bridge collection"]}))


def test_atomic_phase_cutover(tmp_path, monkeypatch):
    ws, store, stage, artifacts, _, _ = _normal_phase_workspace(tmp_path, monkeypatch)
    requested = loop.next_action(ws)
    assert "error" not in requested, requested
    assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
    _authored_requirement(ws, stage)
    _emit_host_hook(ws, requested, "SubagentStop", monkeypatch)
    pending = loop.next_action(ws)
    assert pending["phase_runtime"]["status"] == "collected", pending
    collected = pending["phase_runtime"]["completion"]
    result = artifacts.read(collected["runtime_result"])
    assert result["status"] == "accepted"
    material = artifacts.read(requested["phase_runtime"]["reference"])
    source = design_host_transport.phase_nonce_source(loop.tp, ws, stage["run_id"])
    issued = source.recover(material["nonce_bindings"])
    assert source.phase_hooks(issued, material["nonce_bindings"])[1]["tokens"] == 100
    assert store.load(stage["run_id"])["stage_heads"][stage["stage_id"]]["summary"]["state"] == "active"
    state = loop.load(ws)
    completion = loop._stage_loop_gate_completion(ws, state, step="pm", outcome="pass")
    transitioned = loop._stage_loop_transition(ws, state, from_step="pm", to_step="design", completion=completion)
    from taskplane.tests.test_stage_loop_integration import _successor_handoff
    successor, handoff = _successor_handoff(ws, store, transitioned)
    assert handoff["schema"] == "taskplane.stage-handoff/v2"
    assert successor["input_manifest_ref"]["fingerprint"] == collected["handoff"]["fingerprint"]
    dispatch = taskplane_lite.stage_runtime_dispatch(successor, transitioned, handoff, successor["selected_artifacts"])
    assert dispatch["startup"]["input_handoff"]["phase_result"] == result


@pytest.mark.parametrize("sever", ["missing-start", "missing-terminal", "foreign-worker",
    "unknown-usage", "output-mismatch", "missing-output", "missing-preparation"])
def test_normal_phase_connection_fails_closed(tmp_path, monkeypatch, sever):
    ws, _, stage, _, _, _ = _normal_phase_workspace(tmp_path, monkeypatch)
    requested = loop.next_action(ws)
    assert "phase_runtime" in requested, requested
    if sever != "missing-start":
        assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
    if sever != "missing-output":
        _authored_requirement(ws, stage)
    if sever == "output-mismatch":
        (Path(ws) / "specs/requirement.json").write_text('{"schema":"foreign/v1"}')
    if sever == "missing-preparation":
        Path(requested["phase_runtime"]["reference"]["path"]).unlink()
    if sever != "missing-terminal":
        overrides = ({"agent_id": "foreign-worker"} if sever == "foreign-worker" else
            {"usage": None} if sever == "unknown-usage" else {})
        _emit_host_hook(ws, requested, "SubagentStop", monkeypatch, **overrides)
    held = loop.next_action(ws)
    assert "task_name" not in held
    assert "error" in held or held["phase_runtime"]["status"] == "pending", held
    with pytest.raises((ValueError, OSError)):
        loop._phase_bridge_gate_check(ws, loop.load(ws))


@pytest.mark.parametrize("event", ["complete", "missing-start", "missing-terminal", "output-mismatch"])
def test_external_runtime_preparation_and_completion(tmp_path, event):
    """Runtime unit seam; the host observation port is explicitly simulated."""
    from dataclasses import replace
    from taskplane.tests.test_r0001_agent_runtime import _setup
    runtime, dispatch, calls = _setup(tmp_path)
    prepared = runtime.prepare(dispatch)
    assert calls == []
    assert runtime.nonce.effect_state(dispatch.nonce_bindings) == "issued"
    runtime.nonce.reserve_dispatch(dispatch.issued, dispatch.nonce_bindings)
    with pytest.raises(ValueError):
        runtime.nonce.reserve_dispatch(dispatch.issued, dispatch.nonce_bindings)
    observed = runtime.observe("simulated-worker-1")
    if event == "missing-start":
        observed = replace(observed, start_identity=None)
    elif event == "missing-terminal":
        observed = replace(observed, terminal_identity=None)
    elif event == "output-mismatch":
        observed = replace(observed, outputs=())
    result = runtime.complete(prepared, observed)
    assert result["status"] == ("accepted" if event == "complete" else "refused")
    assert calls == ["observe"]


class SimulatedCrash(BaseException):
    """Process loss at an incumbent persistence boundary, not a host result."""


@pytest.mark.parametrize("domain", ["build", "evaluate", "retro"])
@pytest.mark.parametrize("terminal", ["complete", "severed"])
def test_async_domain_adapters_preserve_prerequisites(tmp_path, monkeypatch, domain, terminal):
    """Bounded adapter fixtures, not an all-phase or native journey claim."""
    from dataclasses import replace
    from taskplane import build_c, review, retro
    if domain == "build":
        from taskplane.tests.test_r0001_build_phase_agent import _build
        runtime, dispatch, options, calls, _, _, _ = _build(tmp_path, monkeypatch)
        prepared = build_c.prepare_build_phase(runtime, dispatch,
            lease_owner=options["lease_owner"], lease=options["lease"])
    elif domain == "evaluate":
        from taskplane.tests.test_r0001_evaluator_integrity import _evaluator, _select
        runtime, dispatch, calls = _evaluator(tmp_path)
        selected = _select(runtime, [dispatch])
        prepared = review.prepare_evaluator_phase(runtime, dispatch, selection_ref=selected)
        assert runtime.store.references("evaluator-attempt")
        assert runtime.store.references("evaluator-attempt-result") == []
    else:
        from taskplane.tests.test_r0001_publication_grant import _retro_inputs
        runtime, dispatch, calls, options = _retro_inputs(tmp_path, monkeypatch)
        prepared = retro.prepare_retro_phase(runtime, dispatch, **options)
        assert runtime.store.references("retro-phase") == []
    assert prepared.dispatch == dispatch
    assert calls == []
    runtime.nonce.reserve_dispatch(dispatch.issued, dispatch.nonce_bindings)
    observation = runtime.observe("simulated-worker-1")
    if domain == "build":
        released = options["observe_terminal"](options["lease"])
        if terminal == "severed":
            released = replace(released, released=False)
        action = lambda: build_c.complete_build_phase(runtime, dispatch, observation,
            lease_owner=options["lease_owner"], lease=options["lease"], terminal=released)
    elif domain == "evaluate":
        if terminal == "severed":
            observation = replace(observation, terminal_identity=None)
        result = review.complete_evaluator_phase(runtime, dispatch, observation, selection_ref=selected)
        assert result["status"] == ("accepted" if terminal == "complete" else "refused")
        assert len(runtime.store.references("evaluator-attempt-result")) == (1 if terminal == "complete" else 0)
        assert runtime.store.references("evaluator-attempt")
        return
    else:
        if terminal == "severed":
            Path(options["terminal_metrics_ref"]["path"]).unlink()
        action = lambda: retro.complete_retro_phase(runtime, dispatch, observation, **options)
    if terminal == "severed":
        with pytest.raises((ValueError, OSError)):
            action()
    else:
        result = action()
        if domain == "retro":
            result = runtime.store.read(result)["runtime_result"]
        assert result["status"] == "accepted"
    assert calls == ["observe"]


@pytest.mark.parametrize("sever", ["connected", "missing-predecessor"])
def test_successive_declared_stage_selection_retains_lineage(tmp_path, sever):
    """Actual package producers; synchronous simulated host, not native proof."""
    from taskplane.tests.test_r0001_phase_agents_spec import _run
    artifacts, registry, state, _, predecessor, _ = _journey(tmp_path)
    authority = {"schema": "taskplane.stage-authority-binding/v1", "run_id": "run-t11",
        "repository_id": "github.com/vdemkiv/taskplane", "repository_key": "github.com-vdemkiv-taskplane-43a0a10bba",
        "worktree_id": "simulated-worktree", "target_revision": "1" * 40, "worktree_revision": "1" * 40,
        "requirement_id": "R-T11", "requirement_revision": "1", "design_revision": "1",
        "design_fingerprint": state["design_fingerprint"], "actor": "human:simulated", "session_id": "simulated-session",
        "authority_revision": 1, "authority_fingerprint": "f" * 64}
    retained = {}
    for phase in ("build", "evaluate", "engineering", "retro"):
        previous = artifacts.read(predecessor)
        retained[predecessor["path"]] = Path(predecessor["path"]).read_bytes()
        value = stage_entities.create_stage(run_id="run-t11", stage_id="stage-" + phase,
            requirement=previous["requirement"], design=previous["design"], stage_kind=phase,
            parent_stage_ids=[], predecessor_stage_ids=[previous["producer"]["stage_id"]],
            input_manifest_ref=review_evidence.portable_artifact_reference(artifacts, predecessor),
            execution_root_id="execution-stage-" + phase, deliverables=["stage"],
            selected_artifacts=previous["selected_artifacts"], budget={"tokens": 1000},
            dependencies=[], contracts=[], authority=authority, created_at="2026-09-06T00:00:00Z")
        if sever == "missing-predecessor" and phase == "evaluate":
            Path(predecessor["path"]).unlink()
            with pytest.raises((ValueError, OSError)):
                _run(tmp_path, artifacts, registry, phase, {"stage": value}, predecessor, state=state)
            return
        reference, result = _run(tmp_path, artifacts, registry, phase, {"stage": value}, predecessor, state=state)
        output = artifacts.read(reference)
        assert len([row for row in output["produced_artifacts"] + output["inherited_artifacts"]
            if row["artifact_class"] == "stage"]) == 1
        assert output["produced_artifacts"][0]["reference"]["fingerprint"] == result["collected_output_references"][0]["fingerprint"]
        assert predecessor["fingerprint"] in {row["fingerprint"] for row in output["evidence_references"]}
        assert all(Path(path).read_bytes() == data for path, data in retained.items())
        predecessor = reference


@pytest.mark.parametrize("boundary", ["before-routing", "after-routing", "before-reservation",
    "after-reservation", "before-collection", "after-collection"])
def test_atomic_cutover_crash_boundaries(tmp_path, monkeypatch, boundary):
    from taskplane import producer_observation
    original_commit = run_store.RunStore.commit
    fired = []
    def commit(store, run_id, *, expected_revision, changes):
        rows = changes.get("phase_records", {})
        selected = any(row["operation"] == ("phase_routing" if "routing" in boundary else "phase_collect")
            for row in rows.values())
        if selected and not fired and ("routing" in boundary or "collection" in boundary):
            fired.append(boundary)
            if boundary.startswith("before"):
                raise SimulatedCrash(boundary)
            result = original_commit(store, run_id, expected_revision=expected_revision, changes=changes)
            raise SimulatedCrash(boundary)
        return original_commit(store, run_id, expected_revision=expected_revision, changes=changes)
    if "routing" in boundary:
        monkeypatch.setattr(run_store.RunStore, "commit", commit)
        with pytest.raises(SimulatedCrash):
            _normal_phase_workspace(tmp_path, monkeypatch)
        ws = str(tmp_path / "loop-workspace")
        context = loop._stage_loop_context(ws, loop.load(ws))
        route = stage_migration.phase_routing(context["manifest"])
        assert (route is None) == boundary.startswith("before")
        requested = loop.next_action(ws)
        assert "error" not in requested, requested
        assert ("phase_runtime" in requested) == boundary.startswith("after")
        return
    ws, store, stage, _, _, _ = _normal_phase_workspace(tmp_path, monkeypatch)
    if "reservation" in boundary:
        reserve = producer_observation.AttemptNonceSource.reserve_dispatch
        def reserve_fault(source, issued, bindings):
            if boundary.startswith("after"):
                reserve(source, issued, bindings)
            raise SimulatedCrash(boundary)
        monkeypatch.setattr(producer_observation.AttemptNonceSource, "reserve_dispatch", reserve_fault)
        with pytest.raises(SimulatedCrash):
            loop.next_action(ws)
        held = loop.next_action(ws)
        assert held["phase_runtime"]["status"] == "pending"
        assert "task_name" not in held
        return
    requested = loop.next_action(ws)
    assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
    _authored_requirement(ws, stage)
    monkeypatch.setattr(run_store.RunStore, "commit", commit)
    with pytest.raises(SimulatedCrash):
        _emit_host_hook(ws, requested, "SubagentStop", monkeypatch)
    held = loop.next_action(ws)
    assert held["phase_runtime"]["status"] == ("pending" if boundary.startswith("before") else "collected")
    assert "task_name" not in held
    _emit_host_hook(ws, requested, "SubagentStop", monkeypatch)
    assert loop.next_action(ws)["phase_runtime"]["status"] == "collected"
    assert len([r for r in stage_migration.phase_records(store.load(stage["run_id"])).values()
        if r["operation"] == "phase_collect"]) == 1


@pytest.mark.parametrize("point", ["before-dispatch", "pending", "collected"])
def test_exact_phase_rollback_preserves_evidence(tmp_path, monkeypatch, point):
    ws, store, stage, artifacts, route, authorize = _normal_phase_workspace(tmp_path, monkeypatch)
    if point != "before-dispatch":
        requested = loop.next_action(ws)
        if point == "collected":
            assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
            _authored_requirement(ws, stage)
            _emit_host_hook(ws, requested, "SubagentStop", monkeypatch)
            assert loop.next_action(ws)["phase_runtime"]["status"] == "collected"
    before = store.load(stage["run_id"])
    retained = {ref["path"]: Path(ref["path"]).read_bytes() for kind in
        ("phase-preparation", "phase-result", "stage-handoff") for ref in artifacts.references(kind)}
    rollback = stage_migration.change_phase_routing(store, stage["run_id"], owner="incumbent",
        configuration=None, expected_previous=route["result_fingerprint"], expected_revision=before["revision"],
        operation_id="rollback-phase-runtime", validate_authority=authorize)
    after = store.load(stage["run_id"])
    assert stage_migration.phase_routing(after) == rollback
    assert all(after["phase_records"][key] == value for key, value in before["phase_records"].items())
    assert {key: after[key] for key in ("stage_heads", "stage_operations", "lineage", "active_stage_projection")} == \
        {key: before[key] for key in ("stage_heads", "stage_operations", "lineage", "active_stage_projection")}
    assert all(Path(path).read_bytes() == data for path, data in retained.items())
    next_action = loop.next_action(ws)
    if point == "before-dispatch":
        assert "phase_runtime" not in next_action
    else:
        assert next_action["phase_runtime"]["status"] == point
        assert "task_name" not in next_action


def test_retained_v2_lifecycle_startup(tmp_path, record_property):
    artifacts, _, _, design_ref, plan_ref, _ = _journey(tmp_path)
    package = stage_handoff.read_v2_manifest(artifacts, plan_ref,
        expected_authority_revision=1, expected_authority_fingerprint="f" * 64)
    identity = storage.identity_from_remote("https://github.com/vdemkiv/taskplane.git")
    store = run_store.RunStore(home=str(tmp_path / "managed"))
    initial = store.create(identity, run_id="run-t11", checkout=str(tmp_path),
        host={"kind": "simulated", "session_id": "simulated-session"},
        target={"kind": "workspace"})
    authority = {
        "schema": "taskplane.stage-authority-binding/v1", "run_id": "run-t11",
        "repository_id": "github.com/vdemkiv/taskplane",
        "repository_key": "github.com-vdemkiv-taskplane-43a0a10bba",
        "worktree_id": "simulated-worktree", "target_revision": "1" * 40,
        "worktree_revision": "1" * 40, "requirement_id": package["requirement"]["id"],
        "requirement_revision": package["requirement"]["revision"],
        "design_revision": package["design"]["revision"],
        "design_fingerprint": package["design"]["fingerprint"],
        "actor": "human:simulated", "session_id": "simulated-session",
        "authority_revision": 1, "authority_fingerprint": "f" * 64,
    }

    def check_authority(expected, current):
        assert expected == current == authority

    lifecycle = stage_entities.StageLifecycle(store, artifact_store=artifacts,
        authority_resolver=lambda manifest: authority,
        authority_validator=check_authority)

    def stage(phase, reference, predecessors):
        return stage_entities.create_stage(run_id="run-t11", stage_id="stage-" + phase,
            requirement=package["requirement"], design=package["design"], stage_kind=phase,
            parent_stage_ids=[], predecessor_stage_ids=predecessors,
            input_manifest_ref=review_evidence.portable_artifact_reference(artifacts, reference),
            execution_root_id="execution-stage-" + phase, deliverables=["plan-task"],
            selected_artifacts=package["selected_artifacts"], budget={"tokens": 1000},
            dependencies=[], contracts=[], authority=authority,
            created_at="2026-09-06T00:00:00Z")

    predecessor = stage("plan", design_ref, [])
    lifecycle.start_stage(predecessor, expected_revision=initial["revision"],
        operation_id="start-simulated-plan")
    before = store.load("run-t11")
    record_property("evidence_mode", "blocking-regression-simulated-host-and-authority")
    record_property("producer_reference", plan_ref["fingerprint"])
    record_property("producer_edge", "produce_phase_handoff -> StageLifecycle.terminalize_and_start")
    loop._preflight_stage_dispatch(stage("build", plan_ref, ["stage-plan"]), package)
    try:
        receipt = lifecycle.terminalize_and_start("stage-plan", stage("build", plan_ref, ["stage-plan"]),
            expected_head_fingerprint=predecessor["fingerprint"], expected_revision=before["revision"],
            operation_id="consume-actual-v2-package", outcome="done", actor=authority["actor"],
            terminalized_at="2026-09-06T00:01:00Z", completed_deliverables=["plan-task"],
            completion_evidence=package["evidence_references"])
    except Exception:
        assert store.load("run-t11") == before
        assert stage_handoff.read_v2_manifest(artifacts, plan_ref,
            expected_authority_revision=1, expected_authority_fingerprint="f" * 64) == package
        raise
    assert receipt["operation"] == "terminalize_and_start"
    successor = stage("build", plan_ref, ["stage-plan"])
    dispatch = taskplane_lite.stage_runtime_dispatch(successor, receipt, package,
        successor["selected_artifacts"])
    startup = taskplane_lite.stage_startup_bytes(dispatch)
    projected = dispatch["startup"]["input_handoff"]
    assert projected["schema"] == taskplane_lite.STAGE_HANDOFF_V2_DISPATCH_SCHEMA
    assert projected["source_fingerprint"] == package["fingerprint"]
    assert projected["phase_result"] == package["phase_result"]
    assert dispatch["telemetry"]["manifest_bytes"] == plan_ref["bytes"]
    assert dispatch["telemetry"]["predecessor_root_opens"] == 0
    assert b"human:simulated" not in startup
    assert b"simulated-session" not in startup
    assert stage_handoff.read_manifest(artifacts, plan_ref,
        expected_authority_revision=1, expected_authority_fingerprint="f" * 64) == package


@pytest.mark.parametrize("case", ["v2-current", "stale-authority", "retained-read"])
def test_mixed_version_matrix(tmp_path, case):
    artifacts, _, _, _, reference, _ = _journey(tmp_path)
    assert stage_migration.active_producer_schema("taskplane.stage-handoff") == stage_handoff.SCHEMA
    if case == "stale-authority":
        with pytest.raises(stage_handoff.StaleAuthorityError):
            stage_handoff.read_manifest(artifacts, reference,
                expected_authority_revision=2, expected_authority_fingerprint="f" * 64)
    elif case == "retained-read":
        original = artifacts.read(reference)
        retained = stage_migration.read_compatible_contract(review_evidence.canonical_bytes(original), store=artifacts)
        assert retained.payload == original
        assert retained.progression_authority is False
    else:
        original = stage_handoff.read_manifest(artifacts, reference,
            expected_authority_revision=1, expected_authority_fingerprint="f" * 64)
        assert stage_handoff.store_manifest(artifacts, original) == reference


def test_native_entry_diagnostic_refuses_without_real_capability(tmp_path, record_property):
    """Diagnostic refusal only; this cannot earn supported-entry/native proof."""
    request = _request(tmp_path)
    prepared = design_host_transport.prepare_native_entry(request, _snapshot(request, stable=True))
    observation = prepared.observation
    record_property("evidence_mode", "simulated-capability-input-production-native-preflight")
    record_property("missing_capabilities", str(observation["missing_capabilities"]))
    assert observation["native_identity_claimed"] is False
    assert observation["effect_state"] == "none"
    assert observation["ready"] is False
    assert observation["success"] is False
    assert observation["action"] == "refusal"
    assert observation["evidence_mode"] == "degraded_observation"
    assert "real_canary" in observation["missing_capabilities"]
