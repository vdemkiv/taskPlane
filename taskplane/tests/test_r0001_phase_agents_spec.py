"""T11 local producer connections; host authorship and authority are simulated.

Actual registry, nonce, runtime collection, package writer/readers and quality
authority run here. This is not J0, J1, J6, native completion or W01-W34 proof.
"""
from dataclasses import replace
import copy
import hashlib
import json
from pathlib import Path

import pytest

from taskplane import agent_runtime, delivery_ports, loop, producer_observation
from taskplane import review_evidence, settings, stage_entities, stage_handoff, test_strategy


ROOT = Path(__file__).resolve().parents[2]
SELECTOR = "taskplane/tests/test_r0001_phase_agents_spec.py::test_definition_drives_stateless_product_design_plan"


def _registry():
    rows = json.loads((ROOT / "agents/spec-phase-definitions.json").read_text())
    return settings.load_phase_registry(rows,
        skills={row["skill_ref"]: (ROOT / row["skill_ref"]).read_bytes() for row in rows},
        validator_inventory={"taskplane.loop.validate_spec_phase_artifact": "spec-phase/v1"},
        artifact_schemas={item["artifact_class"]: item["artifact_schema_version"]
            for row in rows for relation in ("consumes", "produces") for item in row[relation]})


def _strategy():
    # Authored Design input, sealed by the actual strategy producer. No upstream
    # receipt or missing handoff is fabricated by this seed.
    return test_strategy.seal_strategy({
        "schema": "taskplane.test-strategy/v1",
        "acceptance_criteria": [{"id": "AC-T11", "selectors": [SELECTOR]}],
        "producers": [{"id": "spec-package", "path": "taskplane/loop.py", "slice": "T11",
            "consumers": ["taskplane/stage_handoff.py"],
            "severed_edges": [{"consumer": "taskplane/stage_handoff.py", "mutation": "remove-package",
                "selector": "taskplane/tests/test_r0001_phase_agents_spec.py::test_severed_product_design_plan_binding_fails_independently"}],
            "interface_kind": "in-process", "interface_fixtures": [], "freshness_inputs": ["candidate", "definition", "package"]}],
        "failure_policy": {"classes": list(test_strategy.FAILURE_CLASSES), "correction_requires": list(test_strategy.CORRECTION_FIELDS)},
        "validation": {"layers": list(test_strategy.VALIDATION_LAYERS), "fingerprint_inputs": list(test_strategy.FINGERPRINT_INPUTS),
            "reuse_unchanged_green": "cite", "broad_local_default": "refuse", "authoritative_matrix_runs": 1},
    })


def _authority():
    return {"actor": "human:simulated", "session_id": "simulated-session", "authorized_at": "2026-09-06T00:00:00Z",
        "operation_id": "package-op", "authority_record": {"schema": "taskplane.authority-record-reference/v1",
            "authority_schema": "taskplane.consolidated-authorization/v1", "revision": 1, "fingerprint": "f" * 64}}


def _run(tmp_path, store, registry, phase, authored, predecessor=None, *, state=None, driver=None):
    definition = registry.admit(phase, ()).to_dict()
    package = () if predecessor is None else loop.consume_phase_handoff(
        store, predecessor, registry=registry, phase_id=phase, expected_authority_revision=1,
        expected_authority_fingerprint="f" * 64, expected_run_id="run-t11", expected_candidate_fingerprint="a" * 64)
    knowledge = b"sealed scoped knowledge"
    envelope = delivery_ports.dispatch_envelope(phase, definition["role"], "T11", definition["model_tier"],
        role_instructions="Consume only the supplied package; emit only declared candidates and proposals.",
        requested_model=None, requested_effort="high", settings_digest="b" * 64)
    artifacts = () if predecessor is None else package.artifacts
    clock = delivery_ports.FakeClock(wall_time=100)
    (tmp_path / phase).mkdir()
    nonce = producer_observation.AttemptNonceSource(delivery_ports.LocatorEvidenceStore(
        tmp_path / phase, "repository", "run-t11"), key=b"k" * 32, clock=clock)
    nonce.activate_key()
    binding = dict(run_id="run-t11", phase_id=phase, attempt_id="attempt-" + phase, operation_id="op-" + phase,
        candidate_fingerprint="a" * 64, definition_set_fingerprint=registry.definition_set_fingerprint,
        phase_definition_fingerprint=definition["fingerprint"],
        sealed_package_fingerprint=agent_runtime.package_fingerprint(artifacts, knowledge, envelope),
        knowledge_fingerprint=hashlib.sha256(knowledge).hexdigest(), authority_fingerprint="f" * 64,
        host_kind="simulated", host_version="local-test", deadline=200.0)
    issued = nonce.issue(binding)
    bindings = {key: value for key, value in binding.items() if key not in {"host_kind", "host_version", "deadline"}}
    bindings.update(skill_content_fingerprint=definition["skill_content_fingerprint"],
        validator_identities=definition["domain_validator_refs"], validator_inventory_fingerprint=definition["validator_inventory_fingerprint"],
        capability_set_fingerprint=registry.capability_set_fingerprint, host_kind_version="simulated:local-test",
        nonce_digest=issued.receipt["nonce_digest"], lease_id="lease-" + phase, fencing_token=1,
        deadline="1970-01-01T00:03:20Z", budget=definition["budget"])
    for relation, name in (("consumes", "consumed_artifact_schema_versions"), ("produces", "produced_artifact_schema_versions")):
        bindings[name] = [{key: row[key] for key in ("artifact_class", "artifact_schema_version")} for row in definition[relation]]
    calls = []

    def launch(envelope, inputs, boundary):
        assert inputs == artifacts
        assert boundary.environment == {}
        calls.append("launch")
        return "simulated-" + phase

    def observe(identity):
        assert identity == "simulated-" + phase
        # The simulated host authors domain candidates. Production code seals
        # Plan authority and stores declared outputs before runtime collection.
        candidates = loop.produce_spec_phase_candidates(store, definition, authored,
            package=package, state=state, workspace=str(tmp_path / "source"))
        outputs = loop.store_spec_phase_outputs(store, definition, candidates)
        calls.append("observe")
        return agent_runtime.Observation("simulated-start-" + phase, (), "simulated-stop-" + phase, "effect_free", outputs)

    runtime = agent_runtime.AgentRuntime(registry, store, nonce, clock, launch, observe,
        {"taskplane.loop.validate_spec_phase_artifact": loop.validate_spec_phase_artifact},
        lambda: {"tokens": 0, "wall_ms": 0, "attempts": 0, "corrections": 0},
        lambda reason: {"kind": "hold" if reason else "evaluate", "phase_id": phase})
    dispatch = agent_runtime.Dispatch(bindings, artifacts, knowledge, issued, binding, envelope)
    result = runtime.run(dispatch) if driver is None else driver(runtime, dispatch)
    assert result["status"] == "accepted", result
    assert calls == ["launch", "observe"]
    reference = loop.produce_phase_handoff(store, registry=registry, phase_result=result, dispatch=dispatch,
        predecessor=predecessor, authorization=_authority(), producer_stage_id="stage-" + phase,
        requirement={"id": "R-T11", "revision": "1", "fingerprint": "c" * 64},
        design=None if state is None else {"revision": "1", "fingerprint": state["design_fingerprint"]})
    return reference, result


def _journey(tmp_path, *, seam_selectors=None, driver=None):
    # Small real source inputs to the incumbent scanner, independent of the
    # simulated host. No intermediate graph or seam receipt is authored here.
    for directory, content in (("provider", "VALUE = 1\n"),
            ("consumer", "from provider import value\nVALUE = 1\n")):
        folder = tmp_path / "source" / directory
        folder.mkdir(parents=True)
        (folder / ("value.py" if directory == "provider" else "use.py")).write_text(content)
    store = review_evidence.ArtifactStore(str(tmp_path / "artifacts"))
    registry = _registry()
    strategy = _strategy()
    product, _ = _run(tmp_path, store, registry, "product", {"requirement": {
        "schema": "taskplane.requirement/v1", "id": "R-T11", "acceptance_criteria": ["Quality reaches fresh Build"]}}, driver=driver)
    design = {"schema": "taskplane.design/v1", "requirement": "R-T11",
        "seam_contracts": [{"producer": "provider", "consumer": "consumer", "kind": "imports",
            "producer_symbol": "provider.value", "consumer_symbol": "consumer.use", "schema_version": "python-module/v1",
            "cardinality": "one", "positive": (seam_selectors or [SELECTOR])[0],
            "severed": (seam_selectors or [None,
                "taskplane/tests/test_r0001_phase_agents_spec.py::test_severed_product_design_plan_binding_fails_independently"])[1]}],
        "acceptance_map": [{"criterion": "Quality reaches fresh Build", "tests": [SELECTOR]}],
        "test_strategy": {"authority": {"schema": "taskplane.design-test-strategy-reference/v1",
            "path": "design/test-strategy.json", "strategy_fingerprint": strategy["contract_fingerprint_sha256"]}}}
    state = {"run_id": "run-t11", "design_required": True, "design_fingerprint": review_evidence.content_fingerprint(design)}
    design_ref, result = _run(tmp_path, store, registry, "design", {"design": design, "test-strategy": strategy}, product, state=state, driver=driver)
    task = {"id": "T11", "tests": "python3 -m pytest -q " + SELECTOR,
        "criteria": ["Quality reaches fresh Build"], "acceptance_refs": ["Quality reaches fresh Build"],
        "test_contract": {"changed_producers": ["taskplane/loop.py"]},
        "test_strategy_authority": {"schema": "taskplane.plan-test-strategy-reference/v1",
            "path": "design/test-strategy.json", "strategy_fingerprint": strategy["contract_fingerprint_sha256"],
            "criterion_ids": ["AC-T11"], "changed_producer_ids": ["spec-package"]}}
    plan_ref, _ = _run(tmp_path, store, registry, "plan", {"plan-task": task}, design_ref, state=state, driver=driver)
    return store, registry, state, design_ref, plan_ref, result


def _consume(store, registry, state, plan_ref):
    package = loop.consume_phase_handoff(store, plan_ref, registry=registry, phase_id="build",
        expected_authority_revision=1, expected_authority_fingerprint="f" * 64,
        expected_run_id="run-t11", expected_candidate_fingerprint="a" * 64)
    task = package.read("plan-task")["task"]
    authority = loop._validated_task_test_strategy_authority("/no-predecessor-workspace", state, task, design_package=package)
    return package, authority


def test_definition_drives_stateless_product_design_plan(tmp_path, record_property):
    store, registry, state, design_ref, plan_ref, result = _journey(tmp_path)
    package, authority = _consume(store, registry, state, plan_ref)
    assert authority["artifact"]["strategy_fingerprint"] == package.read("test-strategy")["contract_fingerprint_sha256"]
    assert authority["selection"]["selectors"] == [SELECTOR]
    assert authority["package_binding"]["candidate_fingerprint"] == "a" * 64
    assert authority["package_binding"]["definition_set_fingerprint"] == registry.definition_set_fingerprint
    assert authority["selection"]["selectors"] == [SELECTOR]
    assert {artifact.artifact_class for artifact in package.artifacts} == {"requirement", "design", "test-strategy", "plan-task",
        "source-coverage", "decomposition", "seam-manifest"}
    assert stage_handoff.read_v2_manifest(store, design_ref, expected_authority_revision=1,
        expected_authority_fingerprint="f" * 64)["phase_result"] == result
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")
    record_property("producer_reference", plan_ref["fingerprint"])


@pytest.mark.parametrize("edge", ["design-bytes", "strategy-bytes", "plan-bytes", "handoff-bytes",
    "missing-design", "missing-strategy", "missing-plan", "authority", "definition", "run", "candidate",
    "plan-selection", "quality-receipt", "design-state", "build-cannot-mint-plan"])
def test_severed_product_design_plan_binding_fails_independently(tmp_path, record_property, edge):
    store, registry, state, design_ref, plan_ref, _ = _journey(tmp_path)
    package, baseline = _consume(store, registry, state, plan_ref)
    changed_registry, changed_state = registry, state
    path, saved = None, None
    if edge.endswith("-bytes") or edge.startswith("missing-"):
        artifact_class = {"design-bytes": "design", "strategy-bytes": "test-strategy", "plan-bytes": "plan-task",
            "missing-design": "design", "missing-strategy": "test-strategy", "missing-plan": "plan-task"}.get(edge)
        ref = plan_ref if artifact_class is None else next(a.reference for a in package.artifacts if a.artifact_class == artifact_class)
        path = Path(store.root) / ref["kind"] / (ref["fingerprint"] + ".json")
        saved = path.read_bytes()
        if edge.startswith("missing-"):
            path.unlink()
        else:
            path.write_bytes(b"{}")
    elif edge == "definition":
        changed_registry = replace(registry, definition_set_fingerprint="0" * 64)
    else:
        changed_state = dict(state)
        if edge == "design-state":
            changed_state["design_fingerprint"] = "0" * 64
    kwargs = dict(expected_authority_revision=1, expected_authority_fingerprint="f" * 64,
        expected_run_id="run-t11", expected_candidate_fingerprint="a" * 64)
    if edge in {"authority", "run", "candidate"}:
        kwargs[{"authority": "expected_authority_fingerprint", "run": "expected_run_id", "candidate": "expected_candidate_fingerprint"}[edge]] = "0" * 64
    with pytest.raises((ValueError, OSError)):
        broken = loop.consume_phase_handoff(store, plan_ref, registry=changed_registry, phase_id="build", **kwargs)
        task = copy.deepcopy(broken.read("plan-task")["task"])
        if edge == "plan-selection":
            task["test_strategy_authority"]["criterion_ids"] = ["missing-criterion"]
        elif edge == "quality-receipt":
            task.pop("test_strategy_authority_receipt")
        elif edge == "build-cannot-mint-plan":
            loop.seal_phase_plan_task(store, broken, changed_state, task)
        loop._validated_task_test_strategy_authority("/no-predecessor-workspace", changed_state, task, design_package=broken)
    if path is not None:
        path.write_bytes(saved)
    assert _consume(store, registry, state, plan_ref)[1] == baseline
    record_property("severed_edge", edge)
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")
