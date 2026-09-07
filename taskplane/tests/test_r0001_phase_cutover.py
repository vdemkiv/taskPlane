"""T15C opt-in bridge, crash custody, rollback and package-retention checks.

Host authorship and current authority are simulated in isolated workspaces.
The ordinary Product signing/telemetry path is connected; the authentic Retro
adapter completion and severances do not prove an all-phase native journey.
Missing Retro prerequisites refuse, and the native diagnostic stays non-ready.
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


def test_normal_accepted_output_has_host_runtime_signature(tmp_path, monkeypatch, record_property):
    """Actual preparation/hooks/collection; host input and authority simulated."""
    ws, _, stage, artifacts, _, _ = _normal_phase_workspace(tmp_path, monkeypatch)
    requested = loop.next_action(ws)
    assert "error" not in requested, requested
    assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
    _authored_requirement(ws, stage)
    _emit_host_hook(ws, requested, "SubagentStop", monkeypatch)
    completion = loop.next_action(ws)["phase_runtime"]["completion"]
    assert completion is not None
    signed = artifacts.read(completion["runtime_receipt"])
    assert signed["payload"] == artifacts.read(completion["runtime_result"])
    material = artifacts.read(completion["preparation"])
    policy = design_host_transport.runtime_receipt_authority(loop.tp, ws,
        bindings=material["bindings"], freshness=material["freshness"],
        now=int(__import__("time").time()))
    assert policy.verify(signed, store=artifacts)["payload"] == signed["payload"]
    worker_key = loop.tp._worker_contract_authority(ws, create=False)
    assert signed["key_id"] != worker_key["key_id"]
    assert worker_key["secret"].hex() not in json.dumps(signed)
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")


def test_normal_runtime_telemetry_uses_native_ledger(tmp_path, monkeypatch, record_property):
    from taskplane.tests.test_native_session_meter import _write_segment
    ws, _, stage, artifacts, _, _ = _normal_phase_workspace(tmp_path, monkeypatch)
    requested = loop.next_action(ws)
    expected = loop.tp.peek_expectation(ws, requested["task_name"], strict=False)
    loop.record_native_dispatch_observation(ws, expected=expected,
        native_task_name=requested["task_name"])
    assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
    _authored_requirement(ws, stage)
    transcript = tmp_path / "simulated-host.jsonl"
    _write_segment(transcript, session_id="simulated-worker", parent="simulated-root",
        total=100, cached=20, output=10)
    assert _emit_host_hook(ws, requested, "SubagentStop", monkeypatch,
        agent_transcript_path=str(transcript)) == 0
    completion = loop.next_action(ws)["phase_runtime"]["completion"]
    inputs, ref = loop._phase_bridge_telemetry(ws, completion)
    telemetry = artifacts.read(ref)
    assert inputs.runtime_receipt["payload"]["attempt_id"] == requested["dispatch_intent"]["intent_id"]
    assert telemetry["terminal_outcome"] == "complete"
    assert telemetry["usage_status"] == "measured"
    assert telemetry["token_counts_when_available"]["total_tokens"] == 100
    assert inputs.ledger == loop.load(ws)["dispatch_telemetry"]
    assert telemetry["knowledge_fingerprint_consumed"] == inputs.runtime_receipt["payload"]["knowledge_fingerprint"]
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")


@pytest.fixture(scope="module")
def accepted_producer_chain(tmp_path_factory):
    """One actual producer run; immutable evidence reused by isolated negatives."""
    from taskplane.tests.test_native_session_meter import _write_segment
    root = tmp_path_factory.mktemp("runtime-signing-local-host")
    with pytest.MonkeyPatch.context() as patch:
        ws, store, stage, artifacts, _, _ = _normal_phase_workspace(root, patch)
        requested = loop.next_action(ws)
        expected = loop.tp.peek_expectation(ws, requested["task_name"], strict=False)
        loop.record_native_dispatch_observation(ws, expected=expected, native_task_name=requested["task_name"])
        assert _emit_host_hook(ws, requested, "SubagentStart", patch) == 0
        _authored_requirement(ws, stage)
        transcript = root / "simulated-provider.jsonl"
        _write_segment(transcript, session_id="simulated-worker", parent="simulated-root",
            total=100, cached=20, output=10)
        assert _emit_host_hook(ws, requested, "SubagentStop", patch, agent_transcript_path=str(transcript)) == 0
        completion = loop.next_action(ws)["phase_runtime"]["completion"]
        inputs, telemetry_ref = loop._phase_bridge_telemetry(ws, completion)
        material = artifacts.read(completion["preparation"])
        policy = loop._phase_bridge_signing(ws, material)
        yield ws, store, stage, artifacts, completion, inputs, telemetry_ref, material, policy


@pytest.mark.parametrize("field", ["run_id", "candidate_fingerprint", "phase_id", "attempt_id",
    "operation_id", "sealed_package_fingerprint", "phase_definition_fingerprint", "definition_set_fingerprint",
    "knowledge_fingerprint", "authority_fingerprint", "host_kind_version", "nonce_digest", "deadline"])
def test_runtime_signing_exact_admission_binding(accepted_producer_chain, field, record_property):
    from dataclasses import replace
    _, _, _, _, _, inputs, _, _, policy = accepted_producer_chain
    changed = replace(policy, bindings={**policy.bindings, field: "foreign"})
    with pytest.raises(ValueError, match="admitted attempt bindings"):
        changed.verify(inputs.runtime_receipt)
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")


@pytest.mark.parametrize("case", ["missing", "unadmitted", "foreign-purpose", "foreign-owner",
    "foreign-key", "not-yet-valid", "expired", "stale", "disabled", "historical"])
def test_runtime_signing_trust_and_lifetime(accepted_producer_chain, monkeypatch, case):
    from dataclasses import replace
    ws, _, _, _, _, inputs, _, material, policy = accepted_producer_chain
    signed = inputs.runtime_receipt
    if case in {"not-yet-valid", "expired", "stale", "disabled", "historical", "foreign-key"}:
        key = policy.keys[policy.key_id]
        if case == "not-yet-valid":
            policy = replace(policy, now=key.not_before - 1)
        elif case in {"expired", "historical"}:
            policy = replace(policy, now=key.not_after + 1)
        elif case == "stale":
            policy = replace(policy, freshness={**policy.freshness, "source_tree": "0" * 40})
        elif case == "disabled":
            policy = replace(policy, keys={key.key_id: replace(key, status="revoked", changed_at=policy.now)})
        else:
            foreign = replace(key, key_id="foreign-runtime-key", secret=b"foreign-purpose-secret-material!!")
            signed = stage_handoff.sign_contract(signed["payload"], key=foreign,
                issued_at=signed["issued_at"], expires_at=signed["expires_at"], freshness=policy.freshness)
            policy = replace(policy, keys={**policy.keys, foreign.key_id: foreign})
        if case == "historical":
            assert policy.verify(signed, historical=True)["authority_valid"] is False
        else:
            with pytest.raises(ValueError):
                policy.verify(signed)
        return
    original = loop.tp.load_json
    path = str(Path(loop.tp.tp_dir(ws)) / "runtime-receipt-authority.json")
    raw = original(path)
    if case == "missing":
        raw = None
    elif case == "unadmitted":
        raw["admissions"] = {}
    elif case == "foreign-purpose":
        raw["purpose"] = "worker-lifecycle"
    else:
        raw["workspace"] = "foreign"
    monkeypatch.setattr(loop.tp, "load_json", lambda filename, *args, **kwargs:
        raw if str(filename) == path else original(filename, *args, **kwargs))
    with pytest.raises(ValueError):
        loop._phase_bridge_signing(ws, material)


def test_host_policy_disabling_retains_historical_key(accepted_producer_chain, tmp_path):
    _, _, _, _, _, inputs, _, material, policy = accepted_producer_chain
    # Fresh local host custody; no prior/test key is supplied to admission.
    args = dict(bindings=material["bindings"], freshness=material["freshness"], now=policy.now)
    with pytest.raises(ValueError, match="authority"):
        design_host_transport.runtime_receipt_authority(loop.tp, str(tmp_path), **args,
            admit=True, authorize=lambda: False)
    admitted = design_host_transport.runtime_receipt_authority(loop.tp, str(tmp_path), **args,
        admit=True, authorize=lambda: None)
    receipt = admitted.sign(inputs.runtime_receipt["payload"])
    design_host_transport.disable_runtime_receipt_authority(loop.tp, str(tmp_path), **args,
        authorize=lambda: None, status="retired", changed_at=policy.now + 1)
    retired = design_host_transport.runtime_receipt_authority(loop.tp, str(tmp_path), **args)
    with pytest.raises(ValueError, match="disabled"):
        retired.verify(receipt)
    assert retired.verify(receipt, historical=True)["authority_valid"] is False
    assert retired.keys[admitted.key_id].secret == admitted.keys[admitted.key_id].secret


def test_authentic_runtime_chain_reaches_retro_seal_boundary(accepted_producer_chain, monkeypatch):
    from taskplane import delivery_ports, retro, wave_metrics
    ws, store, stage, artifacts, completion, inputs, _, _, _ = accepted_producer_chain
    evidence = wave_metrics.produce_terminal_evidence(dispatch_ledger=inputs.ledger,
        clock=delivery_ports.SystemClock(), candidate_fingerprint=inputs.runtime_receipt["payload"]["candidate_fingerprint"],
        evaluator_summary=retro.evaluator_summary([]), settings_digest="b" * 64)
    sealed = wave_metrics.seal_terminal_metrics(evidence, dispatch_ledger=inputs.ledger,
        clock=delivery_ports.SystemClock(), candidate_fingerprint=inputs.runtime_receipt["payload"]["candidate_fingerprint"],
        archive_upper_bound_tokens=None)
    current = loop.load(ws)
    monkeypatch.setattr(loop, "load", lambda workspace: {**current,
        "wave_metrics_evidence": evidence, "wave_metrics_receipt": sealed})
    # Explicit bounded consumer context, not an all-phase/native journey.
    context = {"artifacts": artifacts, "store": store, "run_id": stage["run_id"],
        "stage": {"input_manifest_ref": completion["handoff"]}}
    domain, kwargs = loop._phase_bridge_retro_inputs(ws, context)
    assert retro._phase_telemetry(artifacts, **kwargs)[1] == sealed
    assert loop._phase_bridge_retro_inputs(ws, context, domain)[0] == domain


@pytest.mark.parametrize("edge", ["connected", "nonce", "ledger", "knowledge", "output", "terminal-hook",
    "telemetry", "terminal-evidence", "terminal-metrics"])
def test_authentic_runtime_retro_completion_and_severances(accepted_producer_chain, tmp_path, monkeypatch, edge, record_property):
    """Actual accepted Retro adapter completion; host/authority stay simulated.

    The raw stage candidate is a bounded consumer input, not claimed as an
    Engineering judgment or proof of a complete all-phase lifecycle journey.
    """
    from taskplane import agent_runtime, delivery_ports, producer_observation, retro, wave_metrics
    ws, store, stage, artifacts, completion, inputs, _, material, _ = accepted_producer_chain
    evidence = wave_metrics.produce_terminal_evidence(dispatch_ledger=inputs.ledger,
        clock=delivery_ports.SystemClock(), candidate_fingerprint=material["bindings"]["candidate_fingerprint"],
        evaluator_summary=retro.evaluator_summary([]), settings_digest="b" * 64)
    seal = wave_metrics.seal_terminal_metrics(evidence, dispatch_ledger=inputs.ledger,
        clock=delivery_ports.SystemClock(), candidate_fingerprint=material["bindings"]["candidate_fingerprint"],
        archive_upper_bound_tokens=None)
    state = loop.load(ws)
    monkeypatch.setattr(loop, "load", lambda workspace: {**state,
        "wave_metrics_evidence": evidence, "wave_metrics_receipt": seal})
    context = {"artifacts": artifacts, "store": store, "run_id": stage["run_id"],
        "stage": {"input_manifest_ref": completion["handoff"]}}
    domain, kwargs = loop._phase_bridge_retro_inputs(ws, context)
    registry, validators = loop._phase_bridge_registry({"definition_source": "agents/spec-phase-definitions.json",
        "definition_set_fingerprint": material["bindings"]["definition_set_fingerprint"]})
    definition = registry.admit("retro", ()).to_dict()
    candidate = agent_runtime.Artifact("stage", "taskplane.stage/v1", artifacts.put("stage", stage))
    package = (candidate,)
    knowledge = review_evidence.canonical_bytes(artifacts.read(material["knowledge_reference"]))
    envelope = delivery_ports.dispatch_envelope("retro", definition["role"], "T15C-local", definition["model_tier"],
        role_instructions="Author only the declared bounded candidate; no publication or gate authority.",
        requested_model=None, requested_effort=None, settings_digest="b" * 64)
    envelope["terminal_evidence_fingerprints"] = [kwargs[key]["fingerprint"] for key in
        ("telemetry_ref", "terminal_evidence_ref", "terminal_metrics_ref")]
    source = design_host_transport.phase_nonce_source(loop.tp, ws, stage["run_id"])
    binding = {**material["nonce_bindings"], "phase_id": "retro", "attempt_id": "local-retro-" + edge,
        "operation_id": "local-retro-" + edge, "phase_definition_fingerprint": definition["fingerprint"],
        "sealed_package_fingerprint": agent_runtime.package_fingerprint(package, knowledge, envelope)}
    nonce = source.issue(binding)
    bindings = {**material["bindings"], **{key: value for key, value in binding.items()
        if key in material["bindings"] and key != "deadline"},
        "skill_content_fingerprint": definition["skill_content_fingerprint"],
        "nonce_digest": nonce.receipt["nonce_digest"],
        "consumed_artifact_schema_versions": [{"artifact_class": "stage", "artifact_schema_version": "taskplane.stage/v1"}],
        "produced_artifact_schema_versions": [{"artifact_class": "stage", "artifact_schema_version": "taskplane.stage/v1"}]}
    dispatch = agent_runtime.Dispatch(bindings, package, knowledge, nonce, binding, envelope)
    runtime = agent_runtime.AgentRuntime(registry, artifacts, source, delivery_ports.SystemClock(),
        lambda *args: "simulated-retro", lambda *args: None, validators,
        lambda: {"tokens": 100, "wall_ms": 0, "attempts": 0, "corrections": 0},
        lambda reason: {"kind": "hold" if reason else "evaluate", "phase_id": "retro"})
    if edge in {"knowledge", "output", "telemetry", "terminal-evidence", "terminal-metrics"}:
        reference = {"knowledge": material["knowledge_reference"],
            "output": inputs.runtime_receipt["payload"]["collected_output_references"][0],
            "telemetry": domain["telemetry_ref"], "terminal-evidence": domain["terminal_evidence_ref"],
            "terminal-metrics": domain["terminal_metrics_ref"]}[edge]
        read = review_evidence.ArtifactStore.read
        def severed(self, ref):
            if ref["fingerprint"] == reference["fingerprint"]:
                raise ValueError("independently severed " + edge)
            return read(self, ref)
        monkeypatch.setattr(review_evidence.ArtifactStore, "read", severed)
    elif edge == "ledger":
        state["dispatch_telemetry"] = {**inputs.ledger, "bindings": [], "dispatches": []}
    elif edge in {"nonce", "terminal-hook"}:
        method = "recover" if edge == "nonce" else "phase_hooks"
        def severed(*args, **kwargs):
            raise producer_observation.ProducerObservationError("independently severed " + edge)
        monkeypatch.setattr(producer_observation.AttemptNonceSource, method, severed)
    if edge != "connected":
        with pytest.raises((ValueError, OSError, producer_observation.ProducerObservationError)):
            loop._phase_bridge_retro_inputs(ws, context, domain)
        assert source.effect_state(binding) == "issued"
    else:
        retro.prepare_retro_phase(runtime, dispatch, **kwargs)
        source.reserve_dispatch(nonce, binding)
        outputs = loop.store_spec_phase_outputs(artifacts, definition, {"stage": stage})
        observation = agent_runtime.Observation("simulated-retro-start", (), "simulated-retro-stop", "reconciled", outputs)
        receipt = retro.complete_retro_phase(runtime, dispatch, observation, **kwargs)
        accepted = retro.read_retro_phase(artifacts, receipt, **kwargs)
        assert accepted["runtime_result"]["status"] == "accepted"
        assert accepted["runtime_result"]["phase_id"] == "retro"
        assert accepted["terminal_metrics_fingerprint"] == seal["fingerprint"]
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")


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


def _terminal_caller_workspace(tmp_path, monkeypatch):
    """Actual package producers seed the bounded normal Engineering caller.

    Operational stage candidates are simulated host input, not substantive
    Design/Build/Evaluate judgments. No receipt, key, route or verdict is
    inserted. This is not a whole-wave success or native-readiness fixture.
    """
    from datetime import datetime, timezone
    from taskplane import agent_runtime, delivery_ports, settings
    from taskplane.tests.test_stage_loop_integration import _initialize_real_new_run
    ws, store, stage, _ = _initialize_real_new_run(tmp_path, monkeypatch, stage_kind="engineering")
    artifacts = review_evidence.ArtifactStore(ws)
    candidate_fingerprint = loop.load(ws)["run_artifact_binding"]["candidate"]["fingerprint"]
    rows = json.loads(settings.DEFAULT_SETTINGS_PATH.read_text())["phase_definitions"]
    root = Path(__file__).resolve().parents[2]
    registry = settings.load_phase_registry(rows,
        skills={row["skill_ref"]: (root / row["skill_ref"]).read_bytes() for row in rows},
        validator_inventory={"taskplane.stage_entities.validate_stage": "taskplane.stage/v1"},
        artifact_schemas={"stage": "taskplane.stage/v1"})
    knowledge_ref = artifacts.put("phase-knowledge", {"facts": []})
    knowledge = review_evidence.canonical_bytes(artifacts.read(knowledge_ref))
    source = design_host_transport.phase_nonce_source(loop.tp, ws, stage["run_id"])
    source.activate_key()
    predecessor = None
    packages = {}
    for phase in ("product", "design", "plan", "build", "evaluate"):
        definition = registry.admit(phase, ()).to_dict()
        package = () if predecessor is None else loop.consume_phase_handoff(artifacts, predecessor,
            registry=registry, phase_id=phase, expected_run_id=stage["run_id"], expected_candidate_fingerprint=candidate_fingerprint,
            expected_authority_revision=stage["authority"]["authority_revision"],
            expected_authority_fingerprint=stage["authority"]["authority_fingerprint"]).artifacts
        envelope = delivery_ports.dispatch_envelope(phase, definition["role"], "local-prerequisite-" + phase,
            definition["model_tier"], role_instructions="Simulated raw stage candidate only; no substantive judgment.",
            requested_model=None, requested_effort=None, settings_digest="b" * 64)
        deadline = datetime.fromtimestamp(source.clock.wall_time() + 1200, timezone.utc).timestamp()
        binding = {"run_id": stage["run_id"], "phase_id": phase, "attempt_id": "seed-" + phase,
            "operation_id": "seed-" + phase, "candidate_fingerprint": candidate_fingerprint,
            "definition_set_fingerprint": registry.definition_set_fingerprint,
            "phase_definition_fingerprint": definition["fingerprint"],
            "sealed_package_fingerprint": agent_runtime.package_fingerprint(package, knowledge, envelope),
            "knowledge_fingerprint": hashlib.sha256(knowledge).hexdigest(),
            "authority_fingerprint": stage["authority"]["authority_fingerprint"],
            "host_kind": "simulated", "host_version": "local-test", "deadline": deadline}
        nonce = source.issue(binding)
        bindings = {key: value for key, value in binding.items() if key not in {"host_kind", "host_version", "deadline"}}
        bindings.update(skill_content_fingerprint=definition["skill_content_fingerprint"],
            validator_identities=definition["domain_validator_refs"], validator_inventory_fingerprint=definition["validator_inventory_fingerprint"],
            capability_set_fingerprint=registry.capability_set_fingerprint, host_kind_version="simulated:local-test",
            nonce_digest=nonce.receipt["nonce_digest"], lease_id="seed-" + phase, fencing_token=1,
            deadline=datetime.fromtimestamp(deadline, timezone.utc).isoformat(), budget=definition["budget"])
        for relation, name in (("consumes", "consumed_artifact_schema_versions"), ("produces", "produced_artifact_schema_versions")):
            bindings[name] = [{key: row[key] for key in ("artifact_class", "artifact_schema_version")} for row in definition[relation]]
        outputs = loop.store_spec_phase_outputs(artifacts, definition, {"stage": stage})
        runtime = agent_runtime.AgentRuntime(registry, artifacts, source, delivery_ports.SystemClock(),
            lambda *args: "simulated-seed", lambda *args: agent_runtime.Observation(
                "simulated-seed-start-" + phase, (), "simulated-seed-stop-" + phase, "effect_free", outputs),
            {"taskplane.stage_entities.validate_stage": stage_entities.validate_stage},
            lambda: {"tokens": 100, "wall_ms": 0, "attempts": 0, "corrections": 0},
            lambda reason: {"kind": "hold" if reason else "evaluate", "phase_id": phase})
        dispatch = agent_runtime.Dispatch(bindings, package, knowledge, nonce, binding, envelope)
        result = runtime.run(dispatch)
        assert result["status"] == "accepted", result.get("reason_code")
        authority = stage["authority"]
        predecessor = loop.produce_phase_handoff(artifacts, registry=registry, phase_result=result, dispatch=dispatch,
            predecessor=predecessor, producer_stage_id="seed-stage-" + phase,
            requirement=stage["requirement"], design=stage["design"], authorization={"actor": authority["actor"],
                "session_id": authority["session_id"], "authorized_at": stage["created_at"], "operation_id": "seed-" + phase,
                "authority_record": {"schema": "taskplane.authority-record-reference/v1",
                    "authority_schema": "taskplane.consolidated-authorization/v1", "revision": authority["authority_revision"],
                    "fingerprint": authority["authority_fingerprint"]}})
        packages[phase] = predecessor
    import inspect
    values = {key: stage[key] for key in inspect.signature(stage_entities.create_stage).parameters}
    values["input_manifest_ref"] = review_evidence.portable_artifact_reference(artifacts, predecessor)
    values["selected_artifacts"] = artifacts.read(predecessor)["selected_artifacts"]
    stage = stage_entities.create_stage(**values)
    started = loop.stage_command(ws, "start", {"schema": "taskplane.stage-command/v1", "stage": stage,
        "expected_revision": 1, "operation_id": "start-local-engineering", "expected_predecessor_fingerprints": {},
        "foreground": True, "authority": stage["authority"]})
    assert "error" not in started, started
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "enabled")
    state = loop.load(ws)
    state.update(step="em", design_fingerprint=stage["design"]["fingerprint"],
        plan_fingerprint=packages["plan"]["fingerprint"])
    loop.save(ws, state)
    context = loop._stage_loop_context(ws, state)
    configuration = {"definition_source": "taskplane/operational-settings.json",
        "definition_set_fingerprint": registry.definition_set_fingerprint, "knowledge_reference": knowledge_ref,
        "candidate_fingerprint": candidate_fingerprint, "target_revision": stage["authority"]["target_revision"],
        "host_kind": "simulated", "host_version": "local-test", "output_paths": {
            "engineering": {"stage": loop.runtime_storage.review_public_path(ws, "phase-stage.json")},
            "retro": {"stage": "retro/phase-stage.json"}}}
    stage_migration.change_phase_routing(store, stage["run_id"], owner="agent-runtime", configuration=configuration,
        expected_previous=None, expected_revision=store.load(stage["run_id"])["revision"],
        operation_id="local-terminal-caller-routing", validate_authority=lambda fresh: loop._phase_bridge_authorize(ws, context, fresh))
    return ws, store, stage, artifacts


def test_normal_engineering_to_retro_public_caller(tmp_path, monkeypatch, record_property):
    from taskplane.tests.test_native_session_meter import _write_segment
    ws, store, stage, artifacts = _terminal_caller_workspace(tmp_path, monkeypatch)
    requested = loop.next_action(ws)
    assert "error" not in requested, requested
    assert requested["phase_runtime"]["status"] == "pending"
    expected = loop.tp.peek_expectation(ws, requested["task_name"], strict=False)
    loop.record_native_dispatch_observation(ws, expected=expected, native_task_name=requested["task_name"])
    # The simulated host finishes the incumbent dispatch-screening commit;
    # telemetry binding alone does not mark its emitted intent as started.
    assert loop.tp.commit_dispatch_verification(ws, requested["task_name"],
        expected["model"], expected, True, expected["reasoning_effort"], strict=True)
    assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
    path = Path(loop.runtime_storage.review_public_path(ws, "phase-stage.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stage))
    # Honest non-judgment, submitted by the genuine incumbent owner; no PASS
    # verdict or review evidence is forged to bootstrap this consumer test.
    submitted = loop.submit(ws, "fail", note="Local caller fixture does not perform substantive Engineering review.")
    assert submitted.get("submitted") is True, submitted
    from taskplane import tp as cli
    stop_status = cli._submission_stop_check(_host_event(ws, requested, "SubagentStop"))
    assert not stop_status or not stop_status.get("block"), stop_status
    transcript = tmp_path / "simulated-engineering.jsonl"
    _write_segment(transcript, session_id="simulated-engineering", parent="simulated-root", total=100, cached=20, output=10)
    assert _emit_host_hook(ws, requested, "SubagentStop", monkeypatch, agent_transcript_path=str(transcript)) == 0
    completion = loop.next_action(ws)["phase_runtime"]["completion"]
    assert artifacts.read(completion["runtime_receipt"])["payload"]["phase_id"] == "engineering"
    # Simulated authority chooses a failure retrospective. The incumbent
    # lifecycle creates the successor from the real collected handoff; no
    # Engineering PASS, review acceptance or publication grant is supplied.
    import inspect
    from datetime import datetime, timezone
    state = loop.load(ws)
    context = loop._stage_loop_context(ws, state)
    handoff = artifacts.read(completion["handoff"])
    successor_values = {key: stage[key] for key in inspect.signature(stage_entities.create_stage).parameters}
    successor_values.update(stage_id="stage-local-failure-retro", stage_kind="retro",
        execution_root_id="execution-stage-local-failure-retro", predecessor_stage_ids=[stage["stage_id"]],
        input_manifest_ref=review_evidence.portable_artifact_reference(artifacts, completion["handoff"]),
        selected_artifacts=handoff["selected_artifacts"], deliverables=["retrospective"],
        created_at=datetime.now(timezone.utc).isoformat())
    successor = stage_entities.create_stage(**successor_values)
    context["lifecycle"].terminalize_and_start(stage["stage_id"], successor,
        expected_head_fingerprint=stage["fingerprint"], expected_revision=context["manifest"]["revision"],
        operation_id="simulated-authority-failure-retro", outcome="done", actor=stage["authority"]["actor"],
        terminalized_at=successor["created_at"], completed_deliverables=stage["deliverables"],
        completion_evidence=handoff["evidence_references"])
    with loop.mutate(ws) as current:
        current["terminal_metrics"] = loop._seal_terminal_metrics_before_retro(ws, current)
        assert current["terminal_metrics"]["status"] == "measured", current["terminal_metrics"]
        current["step"] = "retro"
    requested = loop.next_action(ws)
    assert "error" not in requested, requested
    expected = loop.tp.peek_expectation(ws, requested["task_name"], strict=False)
    loop.record_native_dispatch_observation(ws, expected=expected, native_task_name=requested["task_name"])
    assert loop.tp.commit_dispatch_verification(ws, requested["task_name"],
        expected["model"], expected, True, expected["reasoning_effort"], strict=True)
    assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
    retro_output = Path(ws) / "retro/phase-stage.json"
    retro_output.parent.mkdir()
    retro_output.write_text(json.dumps(successor))
    transcript = tmp_path / "simulated-retro.jsonl"
    _write_segment(transcript, session_id="simulated-retro", parent="simulated-root", total=120, cached=10, output=15)
    assert _emit_host_hook(ws, requested, "SubagentStop", monkeypatch, agent_transcript_path=str(transcript)) == 0
    terminal = loop.next_action(ws)["phase_runtime"]["completion"]
    assert artifacts.read(terminal["runtime_receipt"])["payload"]["phase_id"] == "retro"
    assert artifacts.read(terminal["retro_receipt"])["runtime_result"]["status"] == "accepted"
    assert loop._phase_bridge_retro_completion(ws, loop.load(ws)) == terminal
    domain = artifacts.read(terminal["preparation"])["domain"]
    original_metrics = artifacts.read(domain["terminal_metrics_ref"])
    # Final accounting is newly produced from the current complete census,
    # including Retro itself, never substituted with its predecessor's seal.
    with loop.mutate(ws) as current:
        final_metrics = loop._seal_terminal_metrics_before_retro(ws, current)
        assert final_metrics["status"] == "measured", final_metrics
        assert final_metrics["fingerprint"] != original_metrics["fingerprint"]
    assert loop._phase_bridge_retro_completion(ws, loop.load(ws)) == terminal
    assert artifacts.read(domain["terminal_metrics_ref"]) == original_metrics
    assert submitted["submission"]["outcome"] == "fail"
    assert artifacts.read(completion["handoff"]) == handoff
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority; no substantive Engineering verdict")


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
