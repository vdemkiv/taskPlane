"""Opt-in test-run token policy; host observations here are simulated."""
import json
from pathlib import Path

import pytest

from taskplane import loop, phase_records, review_evidence, stage_entities
from taskplane.tests.phase_fixture import (
    _supporting_pristine_phase_run, _emit_host_hook, _authored_requirement, phase_pending,
)
from taskplane.tests.test_r0001_agent_runtime import _setup


@pytest.mark.parametrize("usage,accepted,reason", [
    (2_285_008, True, None), (10**12, True, None),
    (None, False, "observation_unavailable"),
    (-1, False, "budget_exhausted"), (True, False, "budget_exhausted"),
])
def test_uncapped_phase_still_requires_valid_usage(tmp_path, usage, accepted, reason):
    facade, dispatch, calls = _setup(tmp_path, token_limit=None)
    facade.usage = lambda: {"tokens": usage, "wall_ms": 0, "attempts": 0, "corrections": 0}
    result = facade.run(dispatch)
    assert result["budget"]["tokens"] is None
    assert result["status"] == ("accepted" if accepted else "refused")
    if accepted:
        assert calls == ["launch", "observe"]
    else:
        assert result["reason_code"] == reason
        assert calls == []


@pytest.mark.parametrize("field,value", [("wall_ms", 1_200_000), ("attempts", 1), ("corrections", 1)])
def test_uncapped_tokens_preserve_other_phase_limits(tmp_path, field, value):
    facade, dispatch, calls = _setup(tmp_path, token_limit=None)
    facade.usage = lambda: {"tokens": 2_285_008, "wall_ms": 0, "attempts": 0,
                            "corrections": 0, field: value}
    result = facade.run(dispatch)
    assert result["reason_code"] == "budget_exhausted"
    assert calls == []


def test_uncapped_registry_is_explicit_and_preserves_shipped_defaults():
    root = Path(__file__).resolve().parents[2]
    path = root / "agents/spec-phase-definitions.json"
    before = path.read_bytes()
    source = {"definition_source": "agents/spec-phase-definitions.json"}
    ordinary, _ = loop._phase_bridge_registry(source)
    selected, _ = loop._phase_bridge_registry({**source, "phase_token_limit": None})
    assert all(p.to_dict()["budget"]["tokens"] == 100_000 for p in ordinary.phases)
    assert all(p.to_dict()["budget"]["tokens"] is None for p in selected.phases)
    for original, uncapped in zip(ordinary.phases, selected.phases):
        left, right = original.to_dict(), uncapped.to_dict()
        left.pop("fingerprint")
        right.pop("fingerprint")
        left["budget"]["tokens"] = None
        assert left == right
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="definition set changed"):
        loop._phase_bridge_registry({**source, "phase_token_limit": None,
            "definition_set_fingerprint": ordinary.definition_set_fingerprint})


@pytest.mark.parametrize("invalid", [False, -1, "unlimited"])
def test_invalid_run_override_is_refused(invalid):
    with pytest.raises(ValueError, match="phase_token_limit"):
        loop._phase_bridge_registry({"definition_source": "agents/spec-phase-definitions.json",
                                    "phase_token_limit": invalid})


@pytest.mark.parametrize("field", ["wall_ms", "attempts", "corrections"])
def test_non_token_limits_cannot_be_omitted(field):
    root = Path(__file__).resolve().parents[2]
    row = json.loads((root / "agents/spec-phase-definitions.json").read_text())[0]
    row["budget"][field] = None
    with pytest.raises(stage_entities.StageValidationError, match="budget"):
        stage_entities.create_contract({k: v for k, v in row.items() if k != "fingerprint"})


def test_explicit_test_run_collects_above_default_without_changing_normal_policy(tmp_path, monkeypatch):
    ws, store, run_id, requirement = _supporting_pristine_phase_run(
        tmp_path, monkeypatch, phase_tokens_unlimited=True)
    route = phase_records.phase_routing(store.load(run_id))
    assert route["result"]["configuration"]["phase_token_limit"] is None
    action = loop.next_action(ws)
    assert not action.get("error"), action
    assert not action.get("obligations", {}).get("error"), action
    _emit_host_hook(ws, action, "SubagentStart", monkeypatch)
    context = loop._phase_bridge_context(ws, loop.load(ws))
    _authored_requirement(ws, context["stage"])
    _emit_host_hook(ws, action, "SubagentStop", monkeypatch,
                    usage={"total_tokens": 2_285_008})
    completed = phase_pending(ws)["completion"]
    assert completed is not None
    artifacts = review_evidence.ArtifactStore(ws)
    result = artifacts.read(completed["runtime_result"])
    assert result["status"] == "accepted"
    assert result["budget"]["tokens"] is None
    assert completed["resource_usage"]["tokens"] == 2_285_008
    assert completed["resource_usage"]["advisory"] is False
    prior = store.load(run_id)
    assert loop.init(ws, "cannot replace", requirement_id=requirement["id"],
                     by="human:simulated", phase_tokens_unlimited=False)["refused"]
    assert store.load(run_id) == prior
