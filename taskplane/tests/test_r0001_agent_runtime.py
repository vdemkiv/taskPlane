"""Simulated host boundary, actual registry/nonce/artifact/result producers.

No native readiness or complete Design→Plan→Build journey is claimed here.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from taskplane import agent_runtime as runtime
from taskplane import delivery_ports, producer_observation, review_evidence, settings, stage_entities
from taskplane.tests.test_stage_entities import _authority, _stage


def _setup(tmp_path: Path, *, local_read: bool = False,
           foreign_stage: bool = False) -> tuple[runtime.AgentRuntime, runtime.Dispatch, list[str]]:
    root = Path(__file__).resolve().parents[2]
    rows = json.loads(settings.DEFAULT_SETTINGS_PATH.read_text())["phase_definitions"]
    capabilities = [f"root:{tmp_path / 'input'}"] if local_read else []
    if local_read:
        rows[3]["capability_requirements"] = capabilities
        rows[3] = stage_entities.create_contract({key: value for key, value in rows[3].items()
                                                 if key != "fingerprint"})
    registry = settings.load_phase_registry(rows,
        skills={row["skill_ref"]: (root / row["skill_ref"]).read_bytes() for row in rows},
        validator_inventory={"taskplane.stage_entities.validate_stage": "taskplane.stage/v1"},
        artifact_schemas={"stage": "taskplane.stage/v1"}, available_capabilities=capabilities)
    definition = registry.admit("build", ()).to_dict()
    clock = delivery_ports.FakeClock(wall_time=100)
    store = review_evidence.ArtifactStore(str(tmp_path / "artifacts"))
    knowledge = b"Bounded current knowledge"
    stage = _stage() if foreign_stage else _stage(run_id="run-1", authority=_authority(run_id="run-1"))
    artifact = runtime.Artifact("stage", "taskplane.stage/v1",
        review_evidence.portable_artifact_reference(store, store.put("stage", stage)))
    package = (artifact,)
    envelope = delivery_ports.dispatch_envelope("execute", str(definition["role"]), "T05", str(definition["model_tier"]),
        role_instructions="Consume the supplied artifact.", requested_model=None,
        requested_effort="high", settings_digest="b" * 64)
    fingerprint = runtime.package_fingerprint(package, knowledge, envelope)
    nonce = producer_observation.AttemptNonceSource(
        delivery_ports.LocatorEvidenceStore(tmp_path, "repository", "run-1"),
        key=b"k" * 32, clock=clock)
    nonce.activate_key()
    binding = dict(run_id="run-1", phase_id="build", attempt_id="attempt-1", operation_id="op-1",
        candidate_fingerprint="a" * 64, definition_set_fingerprint=registry.definition_set_fingerprint,
        phase_definition_fingerprint=definition["fingerprint"], sealed_package_fingerprint=fingerprint,
        knowledge_fingerprint=hashlib.sha256(knowledge).hexdigest(), authority_fingerprint="f" * 64,
        host_kind="simulated", host_version="local-test", deadline=200.0)
    issued = nonce.issue(binding)
    bindings = {key: value for key, value in binding.items()
                if key not in {"host_kind", "host_version", "deadline"}}
    bindings.update(skill_content_fingerprint=definition["skill_content_fingerprint"],
        validator_identities=definition["domain_validator_refs"],
        validator_inventory_fingerprint=definition["validator_inventory_fingerprint"],
        consumed_artifact_schema_versions=[{"artifact_class": "stage", "artifact_schema_version": "taskplane.stage/v1"}],
        produced_artifact_schema_versions=[{"artifact_class": "stage", "artifact_schema_version": "taskplane.stage/v1"}],
        capability_set_fingerprint=registry.capability_set_fingerprint,
        host_kind_version="simulated:local-test", nonce_digest=issued.receipt["nonce_digest"],
        lease_id="lease-1", fencing_token=1, deadline="1970-01-01T00:03:20Z", budget=definition["budget"])
    dispatch = runtime.Dispatch(bindings, package, knowledge, issued, binding, envelope)
    calls: list[str] = []

    def launch(envelope: dict[str, object], artifacts: tuple[runtime.Artifact, ...],
               boundary: runtime.ToolBoundary) -> str:
        calls.append("launch")
        assert envelope["role"] == definition["role"]
        assert artifacts == package
        assert boundary.environment == {}
        if local_read:
            assert boundary.call(capabilities[0], lambda: "local-read-result") == "local-read-result"
        return "simulated-worker-1"

    def observe(identity: str) -> runtime.Observation:
        calls.append("observe")
        assert identity == "simulated-worker-1"
        # Actual stage producer and content-addressed writer supply collected bytes.
        result = _stage(run_id="run-1", authority=_authority(run_id="run-1"),
            stage_id="stage-result-001", execution_root_id="execution-stage-result-001")
        output = runtime.Artifact("stage", "taskplane.stage/v1", store.put("stage", result))
        return runtime.Observation("simulated-start-1", ("simulated-progress-1",),
            "simulated-stop-1", "effect_free", (output,))

    facade = runtime.AgentRuntime(registry=registry, store=store, nonce=nonce, clock=clock,
        launch=launch, observe=observe,
        validators={"taskplane.stage_entities.validate_stage": stage_entities.validate_stage},
        usage=lambda: {"tokens": 0, "wall_ms": 0, "attempts": 0, "corrections": 0},
        continuation=lambda reason: {"kind": "evaluate" if reason is None else "hold", "phase_id": "build"})
    if local_read:
        facade.capability = delivery_ports.RecordedTaskDispatchCapabilityFactory().create(
            run_id="run-1", source_sha="a" * 40, design_fingerprint="design",
            plan_fingerprint="plan", task_id="T05", stage="build",
            reservation_fingerprint="reservation", predecessor_fingerprint=None,
            allowed_tools=("read",), read_paths=(str(tmp_path / "input"),))
    return facade, dispatch, calls


@pytest.mark.parametrize("case", ["connected", "authorized-local-read", "missing-input", "altered-input", "stale-package",
    "foreign-input", "knowledge", "definition", "model", "instructions", "missing-terminal", "missing-output", "budget", "launch-loss"])
def test_agent_runtime_wraps_incumbent_dispatch(tmp_path: Path, case: str) -> None:
    facade, dispatch, calls = _setup(tmp_path, local_read=case == "authorized-local-read", foreign_stage=case == "foreign-input")
    expected = "package_mismatch"
    if case == "missing-input":
        dispatch = replace(dispatch, package=())
    elif case == "altered-input":
        # Sever bytes emitted by the real producer, immediately before its consumer.
        reference = dispatch.package[0].reference
        path = Path(facade.store.root) / str(reference["kind"]) / (str(reference["fingerprint"]) + ".json")
        path.write_bytes(b"{}")
    elif case == "stale-package":
        dispatch = replace(dispatch, bindings={**dispatch.bindings, "sealed_package_fingerprint": "0" * 64})
    elif case == "knowledge":
        dispatch = replace(dispatch, knowledge=b"changed knowledge")
        expected = "knowledge_fingerprint_mismatch"
    elif case == "definition":
        dispatch = replace(dispatch, bindings={**dispatch.bindings, "phase_definition_fingerprint": "0" * 64})
    elif case == "model":
        dispatch = replace(dispatch, envelope={**dispatch.envelope, "model_tier": "unapproved"})
    elif case == "instructions":
        dispatch = replace(dispatch, envelope={**dispatch.envelope, "role_instructions": "Different instructions"})
    elif case in {"missing-terminal", "missing-output"}:
        original = facade.observe
        facade.observe = lambda identity: replace(original(identity), **(
            {"terminal_identity": None} if case == "missing-terminal" else {"outputs": ()}))
        expected = "terminal_evidence_missing" if case == "missing-terminal" else "produces_nonconforming"
    elif case == "budget":
        facade.usage = lambda: {"tokens": 10**12, "wall_ms": 0, "attempts": 0, "corrections": 0}
        expected = "budget_exhausted"
    elif case == "launch-loss":
        def lose(envelope: dict[str, object], artifacts: tuple[runtime.Artifact, ...],
                 boundary: runtime.ToolBoundary) -> str:
            calls.append("launch")
            raise OSError("private host error must not leak")
        facade.launch = lose
        expected = "observation_unavailable"
    result = facade.run(dispatch)
    assert stage_entities.validate_contract(result) == result
    if case in {"connected", "authorized-local-read"}:
        assert result["status"] == "accepted"
        assert calls == ["launch", "observe"]
        assert result["host_kind_version"] == "simulated:local-test"
        assert result["nonce_digest"] == dispatch.issued.receipt["nonce_digest"]
        assert facade.nonce.effect_state(dispatch.nonce_bindings) == "dispatched"
        assert result["continuation"] == {"kind": "evaluate", "phase_id": "build"}
        for reference in result["collected_output_references"]:
            assert stage_entities.validate_stage(facade.store.read(reference))["stage_id"] == "stage-result-001"
    else:
        assert result["status"] == "refused"
        assert result["reason_code"] == expected
        assert result["evaluator_dispatch_eligibility"] is False
        assert "private host error" not in json.dumps(result)
        if case == "launch-loss":
            assert result["effect_state"] == "uncertain"
        if case not in {"missing-terminal", "missing-output", "launch-loss"}:
            assert calls == []


@pytest.mark.parametrize("case", ["severed-output", "gate", "successor", "lenses", "lifecycle-tool"])
def test_agent_runtime_has_no_forbidden_authority(tmp_path: Path, case: str) -> None:
    facade, dispatch, calls = _setup(tmp_path)
    if case == "severed-output":
        result = facade.run(dispatch)
        assert result["status"] == "accepted"
        result["terminal_identity"] = None
        with pytest.raises(stage_entities.StageValidationError):
            stage_entities.validate_contract(result)
        return
    if case == "lifecycle-tool":
        def launch(envelope: dict[str, object], artifacts: tuple[runtime.Artifact, ...],
                   boundary: runtime.ToolBoundary) -> str:
            boundary.call("lifecycle:approve", lambda: "forbidden")
            return "unreachable"
        facade.launch = launch
    else:
        dispatch = replace(dispatch, bindings={**dispatch.bindings, case: "agent-authority"})
    result = facade.run(dispatch)
    assert result["status"] == "refused"
    assert result["reason_code"] == ("lifecycle_api_attempt" if case == "lifecycle-tool" else "package_mismatch")
    assert result["evaluator_dispatch_eligibility"] is False
    assert calls == []


@pytest.mark.parametrize("capability", ["network:http://127.0.0.1", "network:http://169.254.169.254",
    "network:https://[::1]", "network:https://public.example/rebind-to-private",
    "environment:AWS_SECRET_ACCESS_KEY", "environment:*", "dependency:pip:package@latest",
    "dependency:https://example.com/unpinned.py", "root:/tmp/undeclared"])
def test_runtime_blocks_ssrf_dns_rebinding_inherited_secrets_and_unpinned_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability: str,
) -> None:
    facade, dispatch, calls = _setup(tmp_path)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-inherit")
    def launch(envelope: dict[str, object], artifacts: tuple[runtime.Artifact, ...],
               boundary: runtime.ToolBoundary) -> str:
        assert boundary.environment == {}
        boundary.call(capability, lambda: calls.append("unsafe-effect"))
        return "unreachable"
    facade.launch = launch
    result = facade.run(dispatch)
    assert result["status"] == "refused"
    assert result["reason_code"] == "capability_denied"
    assert calls == []
    assert "must-not-inherit" not in json.dumps(result)
