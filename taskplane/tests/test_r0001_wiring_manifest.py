"""T16B production edge probes and explicit unresolved proof obligations.

The policy tests remain consumer-unit evidence. Boundary probes reuse the
incumbent production owners with explicitly simulated external host and human
authority inputs; they do not claim native journeys or actual publication.
Missing production output remains an executable failure, never a skip.
"""
from dataclasses import replace
import copy
import hashlib
import json
from pathlib import Path

import pytest

from taskplane import settings, wiring_closure as wiring
from taskplane import (agent_runtime, dispatch_telemetry, graph_decomposition, loop,
    plan_topology, review_evidence, stage_entities, stage_handoff, stage_migration,
    terminal_truth, tp, run_store)
from taskplane.tests.test_r0001_agent_runtime import _setup
from taskplane.tests.test_r0001_knowledge_governance import setup_owner, proposal, apply, consume, SCOPE
from taskplane.tests.test_r0001_orchestrator_authority import setup as continuation_setup
from taskplane.tests.test_r0001_phase_agents_spec import _journey, _consume
from taskplane.tests.test_r0001_telemetry_seal import sources
from taskplane.tests.test_r0001_phase_cutover import (
    accepted_producer_chain)
from taskplane.tests.test_r0001_lease_retry import _owner, _dispatch
from taskplane import delivery_ports, design_host_transport, loop_recovery
from taskplane import authority, review


ROOT = Path(__file__).resolve().parents[2]
PLAN = json.loads((ROOT / "plan/tasks.json").read_text())
ROWS = {row["id"]: row for row in PLAN["wiring_manifest"]}

# These are unresolved production obligations, never skips or policy passes.
UNRESOLVED = {
    "W19": "spec-phase-definitions evaluate produces stage/v1, but collect_evaluator_attempts requires an evaluator judgment",
}


def policy_pair():
    # This is a labeled policy-unit observation. The actual wiring validator's
    # output crosses into the policy consumer; no future phase result is made.
    artifact = json.dumps(wiring.validate_plan_wiring_manifest(
        PLAN["wiring_manifest"], task_ids=[task["id"] for task in PLAN["tasks"]]),
        sort_keys=True).encode()
    positive = wiring.BoundaryObservation(
        selector=ROWS["W03"]["positive_selector"], behavior="policy-unit:manifest-admission",
        identity=("policy-run", "policy-attempt", "policy-operation", "candidate"),
        conditions_fingerprint="a" * 64, status="executed", outcome="accepted",
        output=artifact, artifact_reference="policy-unit:actual-manifest-output",
        changed_edges=(), refusal_edge=None, evidence_class="consumer-unit")
    negative = replace(positive, selector=ROWS["W03"]["severed_selector"],
        outcome="refused", changed_edges=("W03",), refusal_edge="W03")
    # Owner verification is a trusted composition port, exercised here with a
    # double that recognizes only the original observations. It is not a host
    # receipt issuer, nor proof of the W03 registry/resolver journey.
    def verify(observed):
        if observed not in (positive, negative):
            raise ValueError("observation is not retained by producer owner")
    return positive, negative, verify


def test_policy_requires_owner_verification_even_for_identical_bytes():
    positive, negative, verify = policy_pair()
    report = wiring.assess_boundary_pair(ROWS["W03"], positive, negative, verify_owner=verify)
    assert report == ["positive:evidence-class", "negative:evidence-class"]
    copied = replace(positive, artifact_reference="consumer-created-copy")
    assert copied.output == positive.output
    assert "positive:producer-provenance" in wiring.assess_boundary_pair(
        ROWS["W03"], copied, negative, verify_owner=verify)
    assert "positive:producer-provenance" in wiring.assess_boundary_pair(
        ROWS["W03"], positive, negative)


@pytest.mark.parametrize("case,expected", [
    ("stale", "negative:foreign-or-stale"), ("foreign", "negative:foreign-or-stale"),
    ("multi-edge", "negative:single-edge"), ("ceremonial", "negative:single-edge"),
    ("non-failing", "negative:non-failing"),
    ("different-behavior", "negative:different-public-behavior"),
    ("different-conditions", "negative:changed-nontarget-conditions"),
    ("wrong-selector", "negative:selector"), ("missing", "negative:probe-missing"),
    ("unexecutable", "negative:probe-unavailable"), ("unattributable", "negative:unattributable"),
    ("missing-output", "positive:missing-upstream-output"),
    ("consumer-unit", "positive:evidence-class"),
], ids=lambda value: value)
def test_boundary_policy_refuses_each_independent_gap(case, expected):
    positive, negative, verify = policy_pair()
    if case in {"stale", "foreign"}:
        negative = replace(negative, identity=(case, *negative.identity[1:]))
    elif case == "multi-edge":
        negative = replace(negative, changed_edges=("W03", "W04"))
    elif case == "ceremonial":
        negative = replace(negative, changed_edges=())
    elif case == "non-failing":
        negative = replace(negative, outcome="accepted")
    elif case == "different-behavior":
        negative = replace(negative, behavior="other")
    elif case == "different-conditions":
        negative = replace(negative, conditions_fingerprint="b" * 64)
    elif case == "wrong-selector":
        negative = replace(negative, selector=positive.selector)
    elif case == "missing":
        negative = None
    elif case == "unexecutable":
        negative = replace(negative, status="unavailable")
    elif case == "unattributable":
        negative = replace(negative, refusal_edge="W04")
    elif case == "missing-output":
        positive = replace(positive, output=None)
    else:
        positive = replace(positive, evidence_class="consumer-unit")
    assert expected in wiring.assess_boundary_pair(
        ROWS["W03"], positive, negative, verify_owner=verify)


def test_compound_proof_gaps_do_not_short_circuit():
    positive, negative, verify = policy_pair()
    negative = replace(negative, changed_edges=("W03", "W04"), outcome="accepted",
                       status="unavailable", refusal_edge=None)
    findings = wiring.assess_boundary_pair(ROWS["W03"], positive, negative, verify_owner=verify)
    assert {"negative:probe-unavailable", "negative:single-edge", "negative:non-failing",
            "negative:unattributable"} <= set(findings)


def _registry_probe(*, severed):
    """Actual local W03 owner output; no host, grant or phase result is faked."""
    definitions = [json.loads(value) for value in settings.load_settings().phase_definitions]
    skills = {row["skill_ref"]: (ROOT / row["skill_ref"]).read_bytes() for row in definitions}
    registry = settings.load_phase_registry(definitions, skills=skills,
        validator_inventory={"taskplane.stage_entities.validate_stage": "taskplane.stage/v1"},
        artifact_schemas={"stage": "taskplane.stage/v1"})
    produced = next(phase for phase in registry.phases if phase.id == "build")
    assert registry.admit("build", ()) is produced
    if severed:
        # Remove only the actual build definition at the registry/resolver edge;
        # the same phase ID, request, settings, skills and fingerprints remain.
        disconnected = replace(registry, phases=tuple(phase for phase in registry.phases
                                                     if phase is not produced))
        with pytest.raises(settings.SettingsError, match="unknown phase id"):
            disconnected.admit("build", ())
    else:
        assert registry.admit("build", ()).canonical_bytes == produced.canonical_bytes


def _runtime_pair(edge_id, tmp_path, *, severed):
    runtime, dispatch, calls = _setup(tmp_path)
    if edge_id == "W04":
        # The resolver output itself is removed; definition bytes are not rebuilt.
        if severed:
            runtime.registry = replace(runtime.registry, phases=tuple(
                row for row in runtime.registry.phases if row.id != "build"))
        result = runtime.run(dispatch)
    elif edge_id == "W05":
        result = runtime.run(replace(dispatch, package=()) if severed else dispatch)
    elif edge_id == "W06":
        prepared = runtime.prepare(dispatch)
        if severed:
            prepared = agent_runtime.PreparedDispatch(replace(dispatch,
                envelope={**dispatch.envelope, "model_tier": "unapproved"}))
        result = runtime.run(prepared.dispatch)
    else:
        prepared = runtime.prepare(dispatch)
        runtime.nonce.reserve_dispatch(dispatch.issued, dispatch.nonce_bindings)
        observed = runtime.observe("simulated-worker-1")
        result = runtime.complete(prepared,
            replace(observed, terminal_identity=None) if severed else observed)
    assert result["status"] == ("refused" if severed else "accepted"), result
    if severed:
        assert result["reason_code"] == (
            "terminal_evidence_missing" if edge_id == "W10" else "package_mismatch")
        assert "launch" not in calls
    return result["fingerprint"]


def _authorization_pair(*, severed):
    from taskplane.tests.test_consolidated_authority import approved_packet, BASE
    packet, receipt = approved_packet()
    current = authority.derive(packet, receipt, stage="product", current=BASE,
        actor="user-7", thread="thread-9")
    assert current["authorized"] is True
    if severed:
        damaged = dict(receipt)
        damaged.pop("packet_fingerprint")
        refused = authority.derive(packet, damaged, stage="product", current=BASE,
            actor="user-7", thread="thread-9")
        assert refused["authorized"] is False
        assert "stale_or_replayed_receipt" in refused["reasons"]
    return receipt["fingerprint"]


def _evaluator_pair(edge_id, tmp_path, *, severed):
    runtime, original, calls = _setup(tmp_path)
    definition = runtime.registry.admit("evaluate", ()).to_dict()
    envelope = delivery_ports.dispatch_envelope("evaluate", definition["role"], "T16B",
        definition["model_tier"], role_instructions="Read the sealed evidence.",
        requested_model=None, requested_effort="high", settings_digest="b" * 64)
    envelope["evaluation_lens_set_fingerprint"] = review_evidence.content_fingerprint([])
    binding = {**original.nonce_bindings, "phase_id": "evaluate", "attempt_id": "evaluate-attempt",
        "operation_id": "evaluate-operation", "phase_definition_fingerprint": definition["fingerprint"],
        "sealed_package_fingerprint": agent_runtime.package_fingerprint(original.package, original.knowledge, envelope)}
    issued = runtime.nonce.issue(binding)
    dispatch = replace(original, envelope=envelope, nonce_bindings=binding, issued=issued,
        bindings={**original.bindings, **{k: v for k, v in binding.items()
            if k in original.bindings and k != "deadline"},
            "skill_content_fingerprint": definition["skill_content_fingerprint"],
            "nonce_digest": issued.receipt["nonce_digest"]})
    # The simulated host receives the real definition/immutable evidence.
    # Its output stays the actual stage producer; no fake judgment is inserted.
    runtime.launch = lambda *args: calls.append("launch") or "simulated-worker-1"
    runtime.continuation = lambda reason: {"kind": "hold" if reason else "evaluate", "phase_id": "evaluate"}
    selected = review.precommit_evaluator_selection(runtime, [dispatch], binding={
        **{key: dispatch.bindings[key] for key in ("run_id", "phase_id", "candidate_fingerprint")},
        "candidate_sha": "1" * 40, "source_tree": "2" * 40,
        "impact_manifest_fingerprint": "3" * 64, "task_id": "T16B", "requirement_id": "R-0001",
        "design_fingerprint": "4" * 64, "plan_fingerprint": "5" * 64, "settings_digest": "b" * 64})
    assert review.prepare_evaluator_phase(runtime, dispatch, selection_ref=selected)
    if edge_id == "W19":
        result = review.run_evaluator_phase(runtime, dispatch, selection_ref=selected)
        assert result["status"] == "accepted"
        collected = review_evidence.collect_evaluator_attempts(runtime.store, selected)
        assert collected["admissible"], f"W19 product contract mismatch: {collected['gaps']}"
        if severed:
            # Only reachable once the normal evaluator produces an admissible
            # judgment. Never use a hand-written judgment to reach this edge.
            reference = result["collected_output_references"][0]
            path = Path(runtime.store.root) / reference["kind"] / (reference["fingerprint"] + ".json")
            path.unlink()
            refused = review_evidence.collect_evaluator_attempts(runtime.store, selected)
            assert not refused["admissible"] and refused["gaps"]
    elif severed:
        if edge_id == "W17":
            selected_input = None
        else:
            selected_input = selected
            reference = dispatch.package[0].reference
            (Path(runtime.store.root) / reference["kind"] / (reference["fingerprint"] + ".json")).unlink()
        with pytest.raises(ValueError):
            review.prepare_evaluator_phase(runtime, dispatch, selection_ref=selected_input)
        assert calls == []
    else:
        assert review.run_evaluator_phase(runtime, dispatch, selection_ref=selected)["status"] == "accepted"
        assert calls == ["launch", "observe"]
    return selected["fingerprint"]


def _hook_pair(edge_id, chain, *, severed):
    ws, store, stage, artifacts, completion, inputs, _, _, _ = chain
    preparation = completion["preparation"]
    material = artifacts.read(preparation)
    source = design_host_transport.phase_nonce_source(loop.tp, ws, stage["run_id"])
    issued = source.recover(material["nonce_bindings"])
    if edge_id == "W28":
        routing = stage_migration.phase_routing(store.load(stage["run_id"]))
        assert routing["result_fingerprint"] == material["routing"]
        if severed:
            # Remove only the selector's produced routing receipt in the
            # reader input; a broken record cannot select a second producer.
            current = store.load(stage["run_id"])
            damaged = copy.deepcopy(current)
            damaged["phase_records"][routing["operation_id"]]["result"].pop("owner")
            with pytest.raises(stage_migration.MigrationIntegrityError):
                stage_migration.phase_routing(damaged)
        return routing["result_fingerprint"]
    if edge_id == "W07" and severed:
        path = Path(preparation["path"])
        original = path.read_bytes()
        path.unlink()
        try:
            with pytest.raises((ValueError, OSError)):
                loop._phase_bridge_telemetry(ws, completion)
        finally:
            path.write_bytes(original)
        return preparation["fingerprint"]
    loop._phase_bridge_telemetry(ws, completion)
    start, terminal = source.phase_hooks(issued, material["nonce_bindings"])
    assert start["sequence"] == 1 and terminal["sequence"] == 2
    assert start["owner"] == terminal["owner"]
    # Lifecycle Stop records host terminality; validated authored artifacts
    # are independently collected by the runtime completion owner.
    assert inputs.runtime_receipt["payload"]["collected_output_references"]
    if severed:
        # W08: signature edge; W09: verified event -> retained ledger edge.
        path = source._hook_path(issued, "terminal" if edge_id == "W08" else "start")
        original = path.read_bytes()
        if edge_id == "W08":
            damaged = json.loads(original)
            damaged.pop("signature")
            path.write_text(json.dumps(damaged))
        else:
            path.unlink()
        try:
            with pytest.raises(ValueError, match="signature|missing_start"):
                source.phase_hooks(issued, material["nonce_bindings"])
        finally:
            path.write_bytes(original)
        assert source.phase_hooks(issued, material["nonce_bindings"]) == (start, terminal)
    return preparation["fingerprint"]


def _lease_pair(edge_id, tmp_path, monkeypatch, *, severed):
    owner, lease, runtime, dispatch, calls, _, _ = _owner(tmp_path, monkeypatch)
    admitted = owner.admit(lease, expected_fence=0)
    owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch),
        action=lambda bound: calls.append(bound.attempt_id))
    if edge_id == "W24":
        owner.cancel(admitted)
    observed = delivery_ports.observe_lease_terminal(admitted, lambda bound: {
        "terminal_identity": "simulated-stop-1", "released": True,
        "effects": {"host:worker": "observed"}})
    if edge_id == "W24" and severed:
        with pytest.raises(loop_recovery.LeaseRefusal, match="terminal_released_receipt_required"):
            owner.reconcile(admitted, None)
        assert calls == ["attempt-1"]
        return dispatch.bindings["sealed_package_fingerprint"]
    owner.reconcile(admitted, observed)
    replacement = replace(lease, lease_id="lease-2", attempt_id="attempt-2",
        operation_id="op-2", fencing_token=2)
    replacement = owner.admit(replacement, expected_fence=1)
    binding = {**dispatch.nonce_bindings, "attempt_id": replacement.attempt_id,
        "operation_id": replacement.operation_id}
    issued = runtime.nonce.issue(binding)
    if severed:
        with pytest.raises(loop_recovery.LeaseRefusal, match="stale_lease"):
            owner.execute(replace(replacement, fencing_token=1),
                nonce_action=lambda action: runtime.nonce.dispatch(issued, binding, action),
                action=lambda bound: calls.append(bound.attempt_id))
    else:
        owner.execute(replacement,
            nonce_action=lambda action: runtime.nonce.dispatch(issued, binding, action),
            action=lambda bound: calls.append(bound.attempt_id))
    assert calls == (["attempt-1"] if severed else ["attempt-1", "attempt-2"])
    return dispatch.bindings["sealed_package_fingerprint"]


def _legacy_pair(tmp_path, *, severed):
    from taskplane.tests.test_stage_handoff import _manifest
    store = review_evidence.ArtifactStore(str(tmp_path))
    original = _manifest(store)
    reference = stage_handoff.store_manifest(store, original)
    produced = store.read(reference)
    raw = stage_entities.canonical_contract_bytes(produced, store=store)
    retained = stage_migration.read_compatible_contract(raw, store=store)
    assert retained.payload == produced
    assert retained.source_bytes == raw
    assert not retained.progression_authority
    if severed:
        with pytest.raises(ValueError):
            stage_migration.read_compatible_contract(b"", store=store)
    return reference["fingerprint"]


def _knowledge_pair(edge_id, tmp_path, monkeypatch, *, severed):
    owner = setup_owner(tmp_path, monkeypatch)
    value = proposal(owner)
    if edge_id == "W12":
        damaged = dict(value)
        damaged.pop("finding_or_observation_reference")
        if severed:
            with pytest.raises(ValueError):
                apply(owner, damaged)
            assert owner.knowledge_snapshot("workspace", scope=SCOPE)["lineage"] == []
        else:
            assert consume(apply(owner, value), value)["outcome"] == "applied"
        return value["proposal_fingerprint"]
    if edge_id == "W13":
        receipt = apply(owner, value, **({"authorize": None} if severed else {}))
        assert consume(receipt, value)["outcome"] == ("rejected" if severed else "applied")
        assert bool(owner.knowledge_snapshot("workspace", scope=SCOPE)["lineage"]) is not severed
        return value["proposal_fingerprint"]
    receipt = apply(owner, value)
    assert consume(receipt, value)["outcome"] == "applied"
    if edge_id == "W14":
        # Read the actual immutable append through a fresh owner. Sever only
        # its durable lineage file; no synthetic store result is supplied.
        path = tmp_path / "knowledge" / "governed-updates.json"
        original = path.read_bytes()
        if severed:
            path.write_bytes(b"{}")
            try:
                with pytest.raises(run_store.RunStoreError, match="knowledge state is invalid"):
                    owner.knowledge_snapshot("workspace", scope=SCOPE)
            finally:
                path.write_bytes(original)
        assert len(owner.knowledge_snapshot("workspace", scope=SCOPE)["lineage"]) == 1
    elif severed:
        damaged = copy.deepcopy(receipt)
        damaged["payload"].pop("seal")
        with pytest.raises(ValueError):
            consume(damaged, value)
    return value["proposal_fingerprint"]


def _continuation_pair(edge_id, tmp_path, monkeypatch, *, severed):
    inputs, registry, store, revision, ports, _ = continuation_setup(tmp_path, monkeypatch)
    result = loop.continue_phase_result(inputs, registry, store, revision, ports)
    output = tp.phase_continuation_output(result, inputs=inputs, registry=registry,
        store=store, revision=revision, authorize=ports.authorize)
    assert loop.require_phase_continuation(output, inputs, registry, store, revision, ports.authorize)
    if severed:
        with pytest.raises(ValueError):
            loop.require_phase_continuation(None, inputs, registry, store, revision, ports.authorize)
    return result["committed_result"]["fingerprint"]


def _telemetry_pair(edge_id, tmp_path, monkeypatch, *, severed):
    inputs = sources(tmp_path, monkeypatch)
    receipt = dispatch_telemetry.produce_attempt_telemetry(inputs)
    ready = terminal_truth.attempt_telemetry_readiness(receipt, inputs)
    assert terminal_truth.require_attempt_telemetry(ready, receipt, inputs, consumer="seal") == receipt
    if severed:
        if edge_id == "W20":
            with pytest.raises(ValueError):
                dispatch_telemetry.produce_attempt_telemetry(replace(inputs, runtime_receipt={}))
        else:
            blocked = terminal_truth.attempt_telemetry_readiness(None, inputs)
            assert blocked["ready"] is False
            with pytest.raises(terminal_truth.TerminalTruthError):
                terminal_truth.require_attempt_telemetry(blocked, None, inputs, consumer="seal")
    return receipt["fingerprint"]


def _budget_pair(tmp_path, monkeypatch, *, severed):
    from taskplane.tests.test_native_terminal_telemetry import _configure_root, _root_meter
    inputs = sources(tmp_path, monkeypatch)
    ledger = inputs.ledger
    _configure_root(ledger)
    key = b"local-simulated-observation-key"
    meter = _root_meter(10, authority=key)
    dispatch_telemetry.record_root_meter(ledger, meter, observation_authority=key)
    # An authored dispatch request is input to the actual atomic admission
    # producer. Its pre-start binding reserves one session in wave_usage.
    request = dict(dispatch_id="reserved-attempt", thread_id="reserved-worker", thread_type="worker",
        task_id="T16B", dependencies=[], shared_owner=None, started_at=0, ended_at=0,
        wait_duration_seconds=0, correction_count=0, events=[])
    if severed:
        with pytest.raises(dispatch_telemetry.DispatchTelemetryError, match="atomic screen_dispatch admission"):
            dispatch_telemetry.bind_dispatch(ledger, request)
        assert all(row["dispatch_id"] != request["dispatch_id"] for row in ledger["bindings"])
        return meter["fingerprint"]
    options = dict(current_stage="execute", outstanding_set_fingerprint="8" * 64,
        preserved_context_fingerprint="9" * 64, observation_authority=key,
        admission_operation_id=request["dispatch_id"], dispatch=request)
    before = dispatch_telemetry.wave_usage(ledger, delivery_ports.FakeClock(wall_time=120))["sessions"]
    result = dispatch_telemetry.screen_dispatch(ledger, delivery_ports.FakeClock(wall_time=120), **options)
    assert result["operation_status"] == "admitted", result
    assert result["binding"] in ledger["bindings"]
    assert dispatch_telemetry.wave_usage(ledger, delivery_ports.FakeClock(wall_time=120))["sessions"] == before + 1
    revision = ledger["revision"]
    assert dispatch_telemetry.screen_dispatch(ledger, delivery_ports.FakeClock(wall_time=120), **options)["operation_status"] == "duplicate"
    assert ledger["revision"] == revision
    return result["fingerprint"]


def _publication_pair(tmp_path, monkeypatch, *, severed):
    from taskplane import release_evidence, retro
    from taskplane.tests.test_r0001_publication_grant import _retro_inputs
    runtime, dispatch, _, retro_args = _retro_inputs(tmp_path, monkeypatch)
    retro_ref = retro.run_retro_phase(runtime, dispatch, **retro_args)
    report = retro.read_retro_phase(runtime.store, retro_ref, **retro_args)
    result = report["runtime_result"]
    # The exact immutable output is the local package, not invented tested
    # release bytes. Human authority and repository observations are inputs at
    # the producer-owned external port, explicitly simulated for this edge.
    package = review_evidence.canonical_bytes(runtime.store.read(retro_ref))
    external = {"repository_id": "isolated/repository", "protected_main_commit": report["freshness"]["candidate_sha"],
        "final_signoff": "simulated scoped human signoff", "protected_main": "simulated protected repository observation"}
    binding = {**{field: result[field] for field in ("run_id", "candidate_fingerprint",
        "definition_set_fingerprint", "phase_definition_fingerprint", "knowledge_fingerprint", "authority_fingerprint")},
        **report["freshness"], "repository_id": external["repository_id"],
        "protected_main_commit": external["protected_main_commit"],
        "package_sha256": hashlib.sha256(package).hexdigest(), "version": "2.20.0", "tag": "v2.20.0",
        "channel": "isolated-test", "destination": "local-only-sink", "action": "publish",
        "final_signoff_fingerprint": review_evidence.content_fingerprint(external["final_signoff"]),
        "protected_main_fingerprint": review_evidence.content_fingerprint(external["protected_main"]),
        "predecessor_fingerprint": retro_ref["fingerprint"]}
    approval = {"approval_id": "isolated-publication", "actor": "human:simulated", "action": "publish",
        "binding_fingerprint": delivery_ports.content_fingerprint(binding), "issued_at": 100, "expires_at": 180}
    def authorize(grant):
        return grant["approval"] == approval and grant["binding"] == binding
    grant, issued = release_evidence.issue_publication_grant(runtime.store,
        nonce_source=runtime.nonce, binding=binding, approval=approval, authority_check=authorize)
    effects = []
    args = dict(store=runtime.store, nonce_source=runtime.nonce, grant_ref=None if severed else grant,
        issued=issued, current_binding=lambda: dict(binding), authority_check=authorize,
        package=package, retro_ref=retro_ref, retro_inputs=retro_args,
        publication=lambda payload, bound: effects.append((payload, bound)))
    if severed:
        with pytest.raises(release_evidence.ReleaseEvidenceError, match="publication grant and exact package bytes"):
            release_evidence.consume_publication_grant(**args)
        assert effects == []
    else:
        release_evidence.consume_publication_grant(**args)
        assert effects == [(package, binding)]
        with pytest.raises((ValueError, release_evidence.ReleaseEvidenceError)):
            release_evidence.consume_publication_grant(**args)
        assert effects == [(package, binding)]
    return grant["fingerprint"]


def _package_pair(edge_id, tmp_path, monkeypatch, *, severed):
    store, registry, state, design_ref, plan_ref, _ = _journey(tmp_path)
    package, _ = _consume(store, registry, state, plan_ref)
    if edge_id in {"W02", "W11", "W27", "W33"}:
        if edge_id == "W27":
            value = package.manifest()
            raw = stage_entities.canonical_contract_bytes(value, store=store)
            assert stage_migration.read_compatible_contract(raw, store=store).payload == value
            if severed:
                with pytest.raises(ValueError):
                    stage_migration.read_compatible_contract(b"", store=store)
            return plan_ref["fingerprint"]
        if edge_id == "W02":
            # Package -> phase resolver: the actual predecessor is needed to
            # resolve the Build consumes inventory.
            target = Path(plan_ref["path"])
        elif edge_id == "W11":
            target = Path(design_ref["path"])
        else:
            reference = next(row.reference for row in package.artifacts if row.artifact_class == "seam-manifest")
            target = Path(store.root) / reference["kind"] / (reference["fingerprint"] + ".json")
        original = target.read_bytes()
        if severed:
            target.unlink()
            try:
                with pytest.raises((ValueError, OSError)):
                    if edge_id == "W11":
                        loop.consume_phase_handoff(store, design_ref, registry=registry, phase_id="plan",
                            expected_authority_revision=1, expected_authority_fingerprint="f" * 64,
                            expected_run_id="run-t11", expected_candidate_fingerprint="a" * 64)
                    else:
                        _consume(store, registry, state, plan_ref)
            finally:
                target.write_bytes(original)
        else:
            assert package.read("seam-manifest")["seams"]
        return plan_ref["fingerprint"]
    graph = plan_topology._depgraph.scan(str(tmp_path / "source"), decompose=True)
    decomposition = plan_topology.dependency_plan_projection(graph)
    manifest = package.read("seam-manifest")
    if edge_id == "W29":
        coverage = graph["meta"]["source_coverage"]
        assert graph_decomposition.require_complete_source_coverage(coverage)["complete"]
        if severed:
            damaged = copy.deepcopy(coverage)
            damaged.pop("fingerprint")
            with pytest.raises(ValueError):
                graph_decomposition.require_complete_source_coverage(damaged)
    elif edge_id == "W30":
        assert graph_decomposition.dependency_decomposition(graph) == decomposition
        if severed:
            damaged = copy.deepcopy(graph)
            damaged["meta"]["source_coverage"] = {}
            with pytest.raises(ValueError):
                graph_decomposition.dependency_decomposition(damaged)
    elif edge_id == "W31":
        contracts = package.read("design")["seam_contracts"]
        produced = plan_topology.produce_dependency_plan(str(tmp_path / "source"),
            binding=manifest["binding"], seam_contracts=contracts)
        assert produced["decomposition"]["tasks"] == decomposition["tasks"]
        assert produced["seam-manifest"]["decomposition_fingerprint"] == produced["decomposition"]["fingerprint"]
        if severed:
            # One-edge removal of an already observed decomposer output at
            # the Plan call site, retaining the identical source and binding.
            with monkeypatch.context() as patch:
                patch.setattr(plan_topology._depgraph.graph_decomposition, "dependency_decomposition", lambda *a, **k: None)
                with pytest.raises((ValueError, TypeError)):
                    plan_topology.produce_dependency_plan(str(tmp_path / "source"),
                        binding=manifest["binding"], seam_contracts=contracts)
    elif edge_id == "W32":
        contracts = package.read("design")["seam_contracts"]
        decomposition = package.read("decomposition")
        assert wiring.build_seam_manifest(decomposition, binding=manifest["binding"], contracts=contracts) == manifest
        if severed:
            with pytest.raises(ValueError, match="missing seam contract"):
                wiring.build_seam_manifest(decomposition, binding=manifest["binding"], contracts=[])
    else:
        assert loop.seal_phase_build_conformance(store, package, str(tmp_path / "source"))["status"] == "conformant"
        if severed:
            path = tmp_path / "source" / "consumer" / "use.py"
            original = path.read_bytes()
            path.write_text("VALUE = 1\n")
            try:
                with pytest.raises(ValueError, match="realized seam conformance.*missing"):
                    loop.seal_phase_build_conformance(store, package, str(tmp_path / "source"))
            finally:
                path.write_bytes(original)
    return manifest["fingerprint"]


def _required_pair(edge_id, tmp_path, monkeypatch, request, *, severed):
    if edge_id == "W01":
        return _authorization_pair(severed=severed)
    if edge_id == "W03":
        _registry_probe(severed=severed)
        return settings.load_settings().digest
    if edge_id in {"W04", "W05", "W06", "W10"}:
        return _runtime_pair(edge_id, tmp_path, severed=severed)
    if edge_id in {"W07", "W08", "W09", "W28"}:
        return _hook_pair(edge_id, request.getfixturevalue("accepted_producer_chain"), severed=severed)
    if edge_id in {"W23", "W24"}:
        return _lease_pair(edge_id, tmp_path, monkeypatch, severed=severed)
    if edge_id == "W26":
        return _legacy_pair(tmp_path, severed=severed)
    if edge_id in {"W17", "W18", "W19"}:
        return _evaluator_pair(edge_id, tmp_path, severed=severed)
    if edge_id in {"W12", "W13", "W14", "W15"}:
        return _knowledge_pair(edge_id, tmp_path, monkeypatch, severed=severed)
    if edge_id == "W16":
        return _continuation_pair(edge_id, tmp_path, monkeypatch, severed=severed)
    if edge_id in {"W20", "W21"}:
        return _telemetry_pair(edge_id, tmp_path, monkeypatch, severed=severed)
    if edge_id == "W22":
        return _budget_pair(tmp_path, monkeypatch, severed=severed)
    if edge_id == "W25":
        return _publication_pair(tmp_path, monkeypatch, severed=severed)
    if edge_id in {"W02", "W11", "W27", "W29", "W30", "W31", "W32", "W33", "W34"}:
        return _package_pair(edge_id, tmp_path, monkeypatch, severed=severed)
    row = ROWS[edge_id]
    pytest.fail(f"{edge_id}: {UNRESOLVED[edge_id]}; required actual-producer probe is missing: "
                f"{row['producer']} -> {row['boundary']} -> {row['consumer']} "
                f"(owner {row['task']}); T16 policy-unit evidence cannot satisfy this journey")


@pytest.mark.parametrize("edge_id", wiring.PLAN_WIRING_EDGE_IDS, ids=str)
def test_wiring_production_path(edge_id, tmp_path, monkeypatch, request, record_property):
    output = _required_pair(edge_id, tmp_path, monkeypatch, request, severed=False)
    record_property("wiring_case", json.dumps({"selector": request.node.nodeid,
        "case_id": edge_id, "producer_edge": edge_id, "mutation": "none",
        "collected": True, "executed": True, "outcome": "accepted", "evidence_reference": output,
        "evidence_mode": "local-production-with-simulated-host-and-authority"}, sort_keys=True))


@pytest.mark.parametrize("edge_id", wiring.PLAN_WIRING_EDGE_IDS, ids=str)
def test_wiring_severed_edge_fails_closed(edge_id, tmp_path, monkeypatch, request, record_property):
    output = _required_pair(edge_id, tmp_path, monkeypatch, request, severed=True)
    record_property("wiring_case", json.dumps({"selector": request.node.nodeid,
        "case_id": edge_id, "producer_edge": edge_id, "mutation": "remove-one-produced-edge",
        "collected": True, "executed": True, "outcome": "refused", "evidence_reference": output,
        "evidence_mode": "local-production-with-simulated-host-and-authority"}, sort_keys=True))


def test_missing_plan_observations_report_every_gap():
    gaps = wiring.assess_plan_wiring(PLAN["wiring_manifest"],
        task_ids=[task["id"] for task in PLAN["tasks"]], pairs={})
    assert len(gaps) == 34
    assert set(gaps) == set(wiring.PLAN_WIRING_EDGE_IDS)


def test_all_severed_edges_have_attributable_single_edge_failure():
    # Never turn the detector's successful reporting of missing evidence into
    # acceptance. Every independent W selector above is still collected/run.
    assert not UNRESOLVED, "Global wiring attribution remains blocked: " + json.dumps(UNRESOLVED, sort_keys=True)
