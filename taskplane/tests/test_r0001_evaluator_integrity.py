"""T13 local composition; simulated host/authority, actual incumbent producers.

Consumer fixtures are not upstream Build/Design authority, real native evidence,
or J0/J1/J6/full-wiring proof. No evaluator or review worker is dispatched.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from taskplane import agent_runtime, delivery_ports, review, review_evidence, stage_entities, settings
from taskplane import evaluation_output, failure_routing
from taskplane.tests.test_r0001_agent_runtime import _setup


def _spec_evaluator(tmp_path, phase="evaluate"):
    """Actual declared producer/collector; only worker authorship/host are simulated."""
    from taskplane import loop
    from taskplane.tests.test_r0001_phase_agents_spec import _registry
    from taskplane.tests.test_stage_entities import _stage, _authority

    runtime, original, calls = _setup(tmp_path)
    runtime.registry = _registry()
    definition = runtime.registry.admit(phase, ()).to_dict()
    evidence = {"schema": "taskplane.failure-evidence/v1", "mode": "simulated-worker-authorship",
        "detail": "This local connection probe does not establish native acceptance."}
    failure = failure_routing.validate_failure_record({
        "schema": failure_routing.FAILURE_RECORD_SCHEMA_ID, "id": "native-proof-unavailable", "stage": "evaluate",
        "source": "simulated-worker", "repro": "Inspect the local probe's absent native proof.",
        "evidence": evidence, "evidence_digest": failure_routing.evidence_digest(evidence),
        "class": "unknown", "reason": "Native acceptance is not established by this local probe.",
        "owner": "orchestrator", "cluster": "native-proof", "route": "hold",
        "candidate": {"id": "local-candidate", "fingerprint": original.bindings["candidate_fingerprint"]}})
    runtime.fixture_judgment = {"schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID, "task": "T13",
        "requirement": "R-0001", "verdict": "fail", "criteria": [{"criterion": "FP-AC03",
            "status": "cannot-verify", "evidence": "No native acceptance in this simulated-host probe."}],
        "evaluation": {"status": "complete", "reason_code": "none", "detail": "Independent failure remains owed."},
        "graph": {"dispositions": [], "requirements_checked": [], "contracts_checked": []}, "failures": [failure]}
    if phase == "engineering":
        evaluation_output.validate_evaluator_value(runtime.fixture_judgment)
        judgment = agent_runtime.Artifact("judgment", evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
            runtime.store.put("judgment", runtime.fixture_judgment))
        original = replace(original, package=(*original.package, judgment))
    envelope = delivery_ports.dispatch_envelope(phase, definition["role"], "T13",
        definition["model_tier"], role_instructions="Read the sealed evidence.",
        requested_model=None, requested_effort="high", settings_digest="b" * 64)
    envelope["evaluation_lens_set_fingerprint"] = review_evidence.content_fingerprint([])
    nonce_binding = {**original.nonce_bindings, "phase_id": phase,
        "operation_id": phase + "-operation", "attempt_id": phase + "-attempt",
        "phase_definition_fingerprint": definition["fingerprint"],
        "definition_set_fingerprint": runtime.registry.definition_set_fingerprint,
        "sealed_package_fingerprint": agent_runtime.package_fingerprint(original.package, original.knowledge, envelope)}
    issued = runtime.nonce.issue(nonce_binding)
    bindings = {**original.bindings, **{key: value for key, value in nonce_binding.items()
        if key in original.bindings and key != "deadline"}, "nonce_digest": issued.receipt["nonce_digest"],
        "skill_content_fingerprint": definition["skill_content_fingerprint"],
        "validator_identities": definition["domain_validator_refs"],
        "validator_inventory_fingerprint": definition["validator_inventory_fingerprint"],
        "capability_set_fingerprint": runtime.registry.capability_set_fingerprint,
        "consumed_artifact_schema_versions": [{key: row[key] for key in
            ("artifact_class", "artifact_schema_version")} for row in definition["consumes"]],
        "produced_artifact_schema_versions": [{key: row[key] for key in
            ("artifact_class", "artifact_schema_version")} for row in definition["produces"]]}
    dispatch = replace(original, bindings=bindings, nonce_bindings=nonce_binding, issued=issued, envelope=envelope)
    runtime.validators = {"taskplane.loop.validate_spec_phase_artifact": loop.validate_spec_phase_artifact}
    runtime.launch = lambda *args: calls.append("launch") or "simulated-worker-1"
    runtime.continuation = lambda reason: {"kind": "hold" if reason else "evaluate", "phase_id": phase}
    def observe(identity):
        calls.append("observe")
        stage = _stage(run_id=bindings["run_id"], stage_kind=phase,
            authority=_authority(run_id=bindings["run_id"]))
        outputs = loop.store_spec_phase_outputs(runtime.store, definition,
            {"stage": stage, "judgment": runtime.fixture_judgment})
        return agent_runtime.Observation(identity, (), "simulated-stop", "effect_free", outputs)
    runtime.observe = observe
    return runtime, dispatch, calls


def _evaluator(tmp_path):
    runtime, dispatch, calls = _setup(tmp_path)
    # Consumer-only definition fixture: exercise the real registry and runtime
    # with the incumbent evaluator output schema. Not upstream phase authority.
    rows = json.loads(settings.DEFAULT_SETTINGS_PATH.read_text())["phase_definitions"]
    rows[4]["produces"] = [{"artifact_class": "judgment", "artifact_schema_version":
        evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID, "cardinality": "one", "required": True}]
    rows[4]["domain_validator_refs"] = ["consumer-fixture-validator"]
    inventory = {"taskplane.stage_entities.validate_stage": "taskplane.stage/v1",
        "consumer-fixture-validator": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID}
    for row in rows:
        row["validator_inventory_fingerprint"] = review_evidence.content_fingerprint(inventory)
    rows = [stage_entities.create_contract({k: v for k, v in row.items() if k != "fingerprint"}) for row in rows]
    root = Path(__file__).resolve().parents[2]
    runtime.registry = settings.load_phase_registry(rows,
        skills={row["skill_ref"]: (root / row["skill_ref"]).read_bytes() for row in rows},
        validator_inventory=inventory, artifact_schemas={"stage": "taskplane.stage/v1",
            "judgment": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID}, available_capabilities=[])
    definition = runtime.registry.admit("evaluate", ()).to_dict()
    envelope = delivery_ports.dispatch_envelope("evaluate", definition["role"], "T13",
        definition["model_tier"], role_instructions="Read the sealed candidate.",
        requested_model=None, requested_effort="high", settings_digest="b" * 64)
    envelope["evaluation_lens_set_fingerprint"] = review_evidence.content_fingerprint([])
    nonce_binding = {**dispatch.nonce_bindings, "phase_id": "evaluate",
        "operation_id": "evaluate-op", "attempt_id": "evaluate-attempt",
        "phase_definition_fingerprint": definition["fingerprint"],
        "definition_set_fingerprint": runtime.registry.definition_set_fingerprint,
        "sealed_package_fingerprint": agent_runtime.package_fingerprint(
            dispatch.package, dispatch.knowledge, envelope)}
    issued = runtime.nonce.issue(nonce_binding)
    bindings = {**dispatch.bindings, **{key: value for key, value in nonce_binding.items()
        if key in dispatch.bindings and key != "deadline"}, "nonce_digest": issued.receipt["nonce_digest"],
        "skill_content_fingerprint": definition["skill_content_fingerprint"],
        "capability_set_fingerprint": runtime.registry.capability_set_fingerprint,
        "validator_identities": definition["domain_validator_refs"],
        "validator_inventory_fingerprint": definition["validator_inventory_fingerprint"],
        "produced_artifact_schema_versions": [{"artifact_class": "judgment",
            "artifact_schema_version": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID}]}
    dispatch = replace(dispatch, bindings=bindings, nonce_bindings=nonce_binding,
        issued=issued, envelope=envelope)
    def launch(envelope, artifacts, boundary):
        calls.append("launch")
        return "simulated-worker-1"
    runtime.launch = launch
    def validator(value):
        if value["schema"] == "taskplane.stage/v1":
            return stage_entities.validate_stage(value)
        # Compatibility reader preserves original model bytes. Substantive
        # admission is tested through collect_evaluator_attempts, never mocked.
        return evaluation_output.read_evaluator_value(value)
    runtime.validators = {"consumer-fixture-validator": validator}
    failure_evidence = {"schema": "taskplane.failure-evidence/v1",
        "mode": "consumer-fixture", "detail": "No integrated seam proof is supplied by this fixture."}
    failure = failure_routing.validate_failure_record({
        "schema": failure_routing.FAILURE_RECORD_SCHEMA_ID, "id": "fixture-gap", "stage": "evaluate",
        "source": "consumer-fixture", "repro": "Inspect this fixture's integrated seam evidence.",
        "evidence": failure_evidence, "evidence_digest": failure_routing.evidence_digest(failure_evidence),
        "class": "unknown", "reason": "Required integrated proof is outside the consumer fixture.",
        "owner": "orchestrator", "cluster": "seam-evidence", "route": "hold",
        "candidate": {"id": "candidate-fixture", "fingerprint": dispatch.bindings["candidate_fingerprint"]}})
    judgment = {"schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID, "task": "T13",
        "requirement": "R-0001", "verdict": "fail", "criteria": [{"criterion": "FP-AC03",
            "status": "cannot-verify", "evidence": "Consumer fixture has no integrated seam proof."}],
        "evaluation": {"status": "complete", "reason_code": "none", "detail": "Simulated judgment."},
        "graph": {"dispositions": [], "requirements_checked": [], "contracts_checked": []}, "failures": [failure]}
    def observe(identity):
        calls.append("observe")
        ref = runtime.store.put("evaluator-judgment", judgment)
        return agent_runtime.Observation(identity, (), "simulated-stop", "effect_free",
            (agent_runtime.Artifact("judgment", judgment["schema"], ref),))
    runtime.observe = observe
    runtime.fixture_judgment = judgment
    return runtime, dispatch, calls


def _binding(dispatch):
    return {**{key: dispatch.bindings[key] for key in ("run_id", "phase_id", "candidate_fingerprint")},
        "candidate_sha": "1" * 40, "source_tree": "2" * 40,
        "impact_manifest_fingerprint": "3" * 64, "task_id": "T13", "requirement_id": "R-0001",
        "design_fingerprint": "4" * 64, "plan_fingerprint": "5" * 64, "settings_digest": "b" * 64}


def _select(runtime, dispatches):
    return review.precommit_evaluator_selection(runtime, dispatches, binding=_binding(dispatches[0]))


@pytest.mark.parametrize("phase", ["evaluate", "engineering"])
@pytest.mark.parametrize("damage", ["none", "missing-judgment", "foreign-task", "foreign-requirement",
    "invalid-failure", "pass-without-child-evidence", "foreign-stage"])
def test_declared_evaluator_output_reaches_collector_without_promoting_failure(tmp_path, phase, damage):
    runtime, dispatch, calls = _spec_evaluator(tmp_path, phase)
    selected = _select(runtime, [dispatch])
    if damage == "foreign-task":
        runtime.fixture_judgment["task"] = "foreign"
    elif damage == "foreign-requirement":
        runtime.fixture_judgment["requirement"] = "foreign"
    elif damage == "invalid-failure":
        runtime.fixture_judgment["failures"][0].pop("evidence_digest")
    elif damage == "pass-without-child-evidence":
        runtime.fixture_judgment.update(verdict="pass", failures=[])
    elif damage in {"missing-judgment", "foreign-stage"}:
        original = runtime.observe
        def observe(identity):
            observed = original(identity)
            if damage == "missing-judgment":
                return replace(observed, outputs=tuple(row for row in observed.outputs if row.artifact_class == "stage"))
            from taskplane.tests.test_stage_entities import _stage, _authority
            foreign = _stage(run_id="foreign", stage_kind=phase, authority=_authority(run_id="foreign"))
            return replace(observed, outputs=tuple(
                agent_runtime.Artifact("stage", "taskplane.stage/v1", runtime.store.put("stage", foreign))
                if row.artifact_class == "stage" else row for row in observed.outputs))
        runtime.observe = observe
    result = review.run_evaluator_phase(runtime, dispatch, selection_ref=selected)
    if damage == "foreign-stage" and result["status"] == "accepted":
        with pytest.raises(ValueError, match="stage metadata is foreign"):
            review_evidence.collect_evaluator_attempts(runtime.store, selected)
        return
    collected = review_evidence.collect_evaluator_attempts(runtime.store, selected)
    assert collected["admissible"] is collected["progression_authority"] is False
    if damage == "none":
        assert result["status"] == "accepted", result.get("reason_code")
        assert collected["gaps"] == []
        assert len(collected["attempts"][0]["judgments"]) == 1
        assert len(result["collected_output_references"]) == 2
        assert collected["unfavorable_attempts"] == [dispatch.bindings["attempt_id"]]
    else:
        assert collected["gaps"] or result["status"] == "refused"


def _second(runtime, dispatch):
    binding = {**dispatch.nonce_bindings, "attempt_id": "replacement-attempt", "operation_id": "replacement-op"}
    issued = runtime.nonce.issue(binding)
    return replace(dispatch, nonce_bindings=binding, issued=issued,
        bindings={**dispatch.bindings, "attempt_id": binding["attempt_id"],
            "operation_id": binding["operation_id"], "nonce_digest": issued.receipt["nonce_digest"]})


@pytest.mark.parametrize("case", ["connected", "missing-selection", "changed-package", "foreign-attempt",
    "candidate_sha", "source_tree", "impact_manifest_fingerprint", "changed-selection", "deleted-selection",
    "working_lenses", "nonce_secret", "lifecycle_capability", "mutable_knowledge", "foreign-lens-set",
    "deleted-at-effect"])
def test_evaluator_selection_committed_before_dispatch(tmp_path, monkeypatch, case):
    runtime, dispatch, calls = _evaluator(tmp_path)
    if case in {"working_lenses", "nonce_secret", "lifecycle_capability", "mutable_knowledge", "foreign-lens-set"}:
        if case == "foreign-lens-set":
            dispatch.envelope["evaluation_lens_set_fingerprint"] = "0" * 64
        else:
            dispatch.envelope[case] = "forbidden"
        with pytest.raises(ValueError, match="lens authority"):
            _select(runtime, [dispatch])
        assert calls == []
        return
    selected = _select(runtime, [dispatch])
    if case == "deleted-at-effect":
        original_dispatch = runtime.nonce.dispatch
        def sever(issued, binding, action):
            def effect():
                Path(selected["path"]).unlink()
                return action()
            return original_dispatch(issued, binding, effect)
        monkeypatch.setattr(runtime.nonce, "dispatch", sever)
        result = review.run_evaluator_phase(runtime, dispatch, selection_ref=selected)
        assert result["status"] == "refused"
        assert calls == []
        return
    if case in {"candidate_sha", "source_tree", "impact_manifest_fingerprint", "changed-selection"}:
        binding = _binding(dispatch)
        dispatches = [dispatch]
        if case == "changed-selection":
            dispatches = [_second(runtime, dispatch)]
        else:
            binding[case] = "0" * len(binding[case])
        with pytest.raises(ValueError, match="collision|altered"):
            review.precommit_evaluator_selection(runtime, dispatches, binding=binding)
    else:
        if case == "missing-selection":
            selected = None
        elif case == "changed-package":
            dispatch.envelope["role_instructions"] = "different"
        elif case == "foreign-attempt":
            dispatch = _second(runtime, dispatch)
        elif case == "deleted-selection":
            Path(selected["path"]).unlink()
        original = runtime.launch
        def launch(*args):
            persisted = review_evidence.read_evaluator_selection(runtime.store, selected)
            assert persisted["assignments"] == [dict(dispatch.bindings)]
            assert runtime.store.references("evaluator-attempt")
            return original(*args)
        runtime.launch = launch
        if case == "connected":
            result = review.run_evaluator_phase(runtime, dispatch, selection_ref=selected)
            assert result["status"] == "accepted", result
            assert calls == ["launch", "observe"]
            assert result["host_kind_version"] == "simulated:local-test"
        else:
            with pytest.raises(ValueError):
                review.run_evaluator_phase(runtime, dispatch, selection_ref=selected)
    if case != "connected":
        assert calls == []


@pytest.mark.parametrize("case", ["connected", "pending", "launch-loss", "replay", "deleted-start",
    "deleted-result", "altered-result", "deleted-output"])
def test_all_evaluator_attempts_retained(tmp_path, case):
    runtime, first, calls = _evaluator(tmp_path)
    second = _second(runtime, first)
    selected = _select(runtime, [first, second])
    original = runtime.launch
    if case == "launch-loss":
        def lose(*args):
            calls.append("launch")
            raise OSError("simulated lost observation")
        runtime.launch = lose
    first_result = review.run_evaluator_phase(runtime, first, selection_ref=selected)
    runtime.launch = original
    if case != "pending":
        review.run_evaluator_phase(runtime, second, selection_ref=selected)
    if case == "replay":
        before = list(calls)
        assert review.run_evaluator_phase(runtime, first, selection_ref=selected) == first_result
        assert calls == before
    key = review_evidence.evaluator_attempt_key(selected, first.bindings)
    if case in {"deleted-start", "deleted-result", "altered-result"}:
        kind = "evaluator-attempt" if case == "deleted-start" else "evaluator-attempt-result"
        path = Path(runtime.store.root) / kind / (key + ".json")
        if case == "altered-result":
            value = json.loads(path.read_text())
            value["attempt_id"] = "forged"
            path.write_text(json.dumps(value))
        else:
            path.unlink()
    elif case == "deleted-output":
        ref = first_result["collected_output_references"][0]
        (Path(runtime.store.root) / ref["kind"] / (ref["fingerprint"] + ".json")).unlink()
    if case in {"altered-result", "deleted-output"}:
        with pytest.raises(ValueError):
            review_evidence.collect_evaluator_attempts(runtime.store, selected)
        return
    collected = review_evidence.collect_evaluator_attempts(runtime.store, selected)
    assert [row["attempt_id"] for row in collected["attempts"]] == ["evaluate-attempt", "replacement-attempt"]
    assert collected["admissible"] is False
    assert collected["progression_authority"] is False
    if case in {"pending", "deleted-start", "deleted-result"}:
        assert any(row["reason"] == "terminal_evidence_missing" for row in collected["gaps"])
    elif case == "launch-loss":
        assert first_result["effect_state"] == "uncertain"
        assert "evaluate-attempt" in collected["unfavorable_attempts"]
    else:
        assert collected["gaps"] == []
        assert collected["unfavorable_attempts"] == ["evaluate-attempt", "replacement-attempt"]


@pytest.mark.parametrize("case", ["later-pass", "missing-judgment", "foreign-task", "foreign-requirement",
    "replace-result", "changed-selection", "caller-subset"])
def test_unfavorable_judgment_cannot_be_suppressed(tmp_path, case):
    runtime, first, calls = _evaluator(tmp_path)
    second = _second(runtime, first)
    selected = _select(runtime, [first, second])
    unfavorable = review.run_evaluator_phase(runtime, first, selection_ref=selected)
    refs = runtime.store.references("evaluator-attempt-result")
    original_bytes = Path(refs[0]["path"]).read_bytes()
    if case == "foreign-task":
        runtime.fixture_judgment["task"] = "foreign"
    elif case == "foreign-requirement":
        runtime.fixture_judgment["requirement"] = "foreign"
    elif case == "missing-judgment":
        observe = runtime.observe
        runtime.observe = lambda identity: replace(observe(identity), outputs=())
    else:
        runtime.fixture_judgment["verdict"] = "pass"
    review.run_evaluator_phase(runtime, second, selection_ref=selected)
    if case == "replace-result":
        altered = {**unfavorable, "reason_code": "hidden"}
        with pytest.raises(ValueError, match="collision|altered"):
            runtime.store.put("evaluator-attempt-result", altered, fingerprint=refs[0]["fingerprint"])
    elif case == "changed-selection":
        with pytest.raises(ValueError, match="collision|altered"):
            _select(runtime, [second])
    elif case == "caller-subset":
        with pytest.raises(TypeError):
            review_evidence.collect_evaluator_attempts(runtime.store, selected, result_refs=[])
    collected = review_evidence.collect_evaluator_attempts(runtime.store, selected)
    assert "evaluate-attempt" in collected["unfavorable_attempts"]
    assert len(collected["attempts"]) == 2
    assert collected["admissible"] is False
    assert Path(refs[0]["path"]).read_bytes() == original_bytes


@pytest.mark.parametrize("capability", ["lifecycle:gate", "lifecycle:next", "lifecycle:dispatch",
    "lifecycle:commit", "shell:git commit", "write:source", "lifecycle:select-evaluator"])
def test_evaluator_cannot_progress_or_commit(tmp_path, capability):
    runtime, dispatch, calls = _evaluator(tmp_path)
    selected = _select(runtime, [dispatch])
    effects = []
    def launch(envelope, artifacts, boundary):
        assert boundary.environment == {}
        assert "working_lenses" not in envelope
        boundary.call(capability, lambda: effects.append("unauthorized"))
        return "unreachable"
    runtime.launch = launch
    result = review.run_evaluator_phase(runtime, dispatch, selection_ref=selected)
    assert result["status"] == "refused"
    assert result["reason_code"] == ("lifecycle_api_attempt" if capability.startswith("lifecycle:") else "capability_denied")
    assert result["evaluator_dispatch_eligibility"] is False
    assert calls == effects == []
    assert review_evidence.collect_evaluator_attempts(runtime.store, selected)["admissible"] is False
