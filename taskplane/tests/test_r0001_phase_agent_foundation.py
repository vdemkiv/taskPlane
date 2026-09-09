"""T10 available foundation proof, with simulated host/authority ports.

The actual local registry, runtime, knowledge, telemetry and continuation
producers are exercised. This does not claim fresh-install J0, native worker
completion, phase cutover, or the future T16B/T21 W01-W34 aggregate.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from taskplane import loop, tp, settings
from taskplane.tests.test_r0001_orchestrator_authority import setup


def _registry():
    root = Path(__file__).resolve().parents[2]
    rows = [json.loads(value) for value in settings.load_settings().phase_definitions]
    return settings.load_phase_registry(rows,
        skills={row["skill_ref"]: (root / row["skill_ref"]).read_bytes() for row in rows},
        validator_inventory={"taskplane.stage_entities.validate_stage": "taskplane.stage/v1"},
        artifact_schemas={"stage": "taskplane.stage/v1"})


def _receipt(request, record_property, *, edge, output, mutation="none"):
    record_property("foundation_case", json.dumps({
        "selector": request.node.nodeid, "case_id": request.node.callspec.id,
        "candidate_fingerprint": output.get("candidate_fingerprint"),
        "producer_edge": edge, "mutation": mutation, "collected": True,
        "executed": True, "outcome": "accepted" if mutation == "none" else "refused",
        "evidence_reference": output.get("fingerprint"),
        "evidence_mode": "local-production-with-simulated-host-and-authority",
    }, sort_keys=True))


@pytest.mark.parametrize("usage", ["measured", "unavailable"], ids=str)
def test_foundation_pipeline_positive(tmp_path, monkeypatch, request, record_property, usage):
    # W03 executes the real settings producer and the same resolver in both
    # directions; it never substitutes a consumer-created registry result.
    produced_registry = _registry()
    assert produced_registry.admit("build", ()).id == "build"
    inputs, registry, store, revision, ports, events = setup(
        tmp_path, monkeypatch, unavailable=usage == "unavailable")
    runtime_output = inputs.runtime_receipt["payload"]
    evaluation = loop.phase_evaluator_request(inputs, registry, store, ports.authorize)
    assert evaluation["evaluation_lenses"] == []
    result = loop.continue_phase_result(inputs, registry, store, revision, ports)
    output = tp.phase_continuation_output(result, inputs=inputs, registry=registry,
        store=store, revision=revision, authorize=ports.authorize)
    accepted = loop.require_phase_continuation(
        output, inputs, registry, store, revision, ports.authorize)
    assert accepted
    assert output["continuation"] == {"kind": "advance", "successors": ["evaluate"]}
    assert output["host_kind_version"] == "simulated:local-test"
    assert output["telemetry"]["usage_status"] == usage
    assert events.index("gate") < events.index("knowledge")
    assert inputs.runtime_receipt["payload"] == runtime_output
    _receipt(request, record_property, edge="runtime->telemetry->loop->tp->continuation",
             output=runtime_output)


@pytest.mark.parametrize("edge", ["registry-resolver", "runtime-telemetry",
    "knowledge-telemetry", "loop-tp", "telemetry-tp", "commit-tp", "tp-continuation"], ids=str)
def test_foundation_one_edge_severed_matrix(tmp_path, monkeypatch, request, record_property, edge):
    if edge == "registry-resolver":
        registry = _registry()
        produced = registry.admit("build", ())
        disconnected = replace(registry, phases=tuple(
            phase for phase in registry.phases if phase is not produced))
        with pytest.raises(settings.SettingsError, match="unknown phase id"):
            disconnected.admit("build", ())
        assert registry.admit("build", ()) is produced
        record_property("foundation_case", json.dumps({
            "selector": request.node.nodeid, "case_id": edge, "producer_edge": "W03",
            "mutation": "remove-only-produced-build-definition", "collected": True,
            "executed": True, "outcome": "refused", "evidence_mode": "local-production",
            "candidate_fingerprint": registry.definition_set_fingerprint,
            "evidence_reference": produced.to_dict()["fingerprint"],
        }, sort_keys=True))
        return
    inputs, registry, store, revision, ports, _ = setup(tmp_path, monkeypatch)
    result = loop.continue_phase_result(inputs, registry, store, revision, ports)

    def consume(value, bound_inputs=inputs):
        return tp.phase_continuation_output(value, inputs=bound_inputs, registry=registry,
            store=store, revision=revision, authorize=ports.authorize)

    output = consume(result)
    assert loop.require_phase_continuation(output, inputs, registry, store, revision, ports.authorize)
    if edge == "tp-continuation":
        with pytest.raises(ValueError):
            loop.require_phase_continuation(None, inputs, registry, store, revision, ports.authorize)
    else:
        severed, bound_inputs = result, inputs
        if edge == "runtime-telemetry":
            bound_inputs = replace(inputs, runtime_receipt={})
        elif edge == "knowledge-telemetry":
            bound_inputs = replace(inputs, knowledge_receipts=())
        elif edge == "loop-tp":
            severed = None
        else:
            severed = dict(result)
            severed.pop("telemetry_readiness" if edge == "telemetry-tp" else "committed_result")
        with pytest.raises(ValueError):
            consume(severed, bound_inputs)
    # Restore the original edge without changing run, candidate, package,
    # host conditions or producer bytes: the identical consumer accepts again.
    assert consume(result) == output
    _receipt(request, record_property, edge=edge,
        output=inputs.runtime_receipt["payload"], mutation="remove-one-produced-edge")
