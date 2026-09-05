"""The Plan readiness owner accepts sealed inputs without a second policy."""
from __future__ import annotations

import copy
import json

import pytest

from taskplane import loop


@pytest.fixture
def inputs():
    criterion = "The interface remains compatible"
    graph = {
        "modules": {"core": {}, "contract:api": {}, "contract:extra": {},
                    "req:R-TEST": {}, "req:R-DEP": {}},
        "edges": [{"from": "core", "to": "contract:api", "kind": "provides"}],
        "meta": {"content_fingerprint": "a" * 64,
                 "module_ids": {"src/core": "core"}},
    }
    requirement = {"id": "R-TEST", "acceptance": [criterion],
                   "contracts": [{"id": "contract:api", "relation": "provides"}],
                   "depends_on": ["R-DEP"], "open_questions": []}
    design = {
        "schema": "taskplane.design/v1", "requirement": "R-TEST",
        "contracts": ["contract:api"],
        "graph": {"proposed_modules": ["core"],
                  "proposed_edges": copy.deepcopy(graph["edges"]),
                  "depth_policy": {"local_depth": 1, "boundary_mode": "stop",
                                   "contract_depth": 0, "requirement_depth": 0}},
        "acceptance_map": [{"criterion": criterion, "tests": [
            "taskplane/tests/test_plan_sealed_inputs.py::test_sealed_plan_readiness_matches_legacy"]}],
    }
    state = {
        "design_required": True, "design_fingerprint": "b" * 64,
        "requirement_id": "R-TEST", "replan_history": [],
        "tasks": [{"id": "T-1", "scope": ["src/core/api.py"], "tests": "true",
                   "req": "R-TEST", "criteria": [criterion],
                   "acceptance_refs": [criterion], "contracts": ["contract:api"],
                   "design_edges": ["core->contract:api:provides"]}],
    }
    sealed = {"requirements_by_id": {
        "R-TEST": requirement,
        "R-DEP": {"id": "R-DEP", "acceptance": [], "contracts": [],
                  "depends_on": [], "open_questions": []}},
        "graph": graph, "approved_design": design, "replan_history": []}
    return state, sealed


def _write_current_artifacts(workspace, state, sealed):
    plan = {"requirement": state["requirement_id"], "delivery_mode": "build",
            "automatic_lenses": [], "plan_authority": "design:" + state["design_fingerprint"],
            "tasks": state["tasks"]}
    for relative, value in [("plan/tasks.json", plan),
                            ("design/contract.json", sealed["approved_design"])]:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")


def _forbidden(*_args, **_kwargs):
    raise AssertionError("sealed Plan must not read or mutate predecessor private state")


def _forbid_private_state(monkeypatch):
    for owner, methods in [(loop, ["load", "mutate", "_design_plan_errors"]),
                           (loop.reqs, ["get_requirement"]),
                           (loop.depgraph, ["load", "scan", "impact", "readiness",
                                            "link_requirement_dep", "record_edge"])]:
        for method in methods:
            monkeypatch.setattr(owner, method, _forbidden)


@pytest.mark.parametrize("case, expected", [
    ("valid", None),
    ("scope", "scope is missing"),
    ("tests", "tests must be one command string"),
    ("timeout", "test timeout"),
    ("criteria", "explicit acceptance criteria"),
    ("requirement", "requirement R-TEST does not exist"),
    ("dependency", "requirement dependency R-DEP does not exist"),
    ("question", "requirement has unresolved questions"),
    ("coverage", "acceptance has no task owner"),
    ("graph", "new/unknown graph modules were not declared"),
    ("design", "approved design edges are not covered"),
    ("stabilization", "third Plan return requires exactly one"),
    ("strategy", "test-strategy authority"),
])
def test_sealed_plan_readiness_matches_legacy(
        tmp_path, inputs, monkeypatch, case, expected):
    state, sealed = inputs
    task = state["tasks"][0]
    if case == "scope":
        task["scope"] = []
    elif case == "tests":
        task["tests"] = None
    elif case == "timeout":
        task["verification_runner"] = {"gate_timeout": {"aggregate_seconds": True}}
    elif case == "criteria":
        task["criteria"] = []
    elif case == "requirement":
        del sealed["requirements_by_id"]["R-TEST"]
    elif case == "dependency":
        del sealed["requirements_by_id"]["R-DEP"]
    elif case == "question":
        task["high_cost"] = True
        sealed["requirements_by_id"]["R-TEST"]["open_questions"] = ["Resolve ownership"]
    elif case == "coverage":
        task["acceptance_refs"] = []
    elif case == "graph":
        task["scope"] = ["src/new/api.py"]
    elif case == "design":
        task["design_edges"] = []
    elif case == "stabilization":
        state["replan_history"] = [{"reason": "first"}, {"reason": "second"}]
        sealed["replan_history"] = copy.deepcopy(state["replan_history"])
    elif case == "strategy":
        task["test_contract"] = {}
    _write_current_artifacts(tmp_path, state, sealed)
    monkeypatch.setattr(loop.tp, "git_head", lambda _: "c" * 40)
    monkeypatch.setattr(loop.reqs, "get_requirement",
                        lambda _ws, rid: sealed["requirements_by_id"].get(rid))
    monkeypatch.setattr(loop.depgraph, "load", lambda _: sealed["graph"])
    monkeypatch.setattr(loop.depgraph, "scan", lambda _: sealed["graph"])
    before = copy.deepcopy((state, sealed))
    legacy = loop._plan_dor_errors(str(tmp_path), state)
    assert bool(legacy) is (expected is not None), legacy
    if expected:
        assert expected in " ".join(legacy)
    _forbid_private_state(monkeypatch)
    assert loop._plan_dor_errors(str(tmp_path), state, sealed_inputs=sealed) == legacy
    assert (state, sealed) == before


@pytest.mark.parametrize("field", [
    "requirements_by_id", "graph", "approved_design", "replan_history"])
@pytest.mark.parametrize("change", ["missing", "wrong-type"])
def test_sealed_plan_refuses_missing_or_malformed_input_before_reads(
        inputs, monkeypatch, field, change):
    state, sealed = inputs
    if change == "missing":
        del sealed[field]
    else:
        sealed[field] = None
    _forbid_private_state(monkeypatch)
    monkeypatch.setattr(loop, "_plan_delivery_mode_from_file", _forbidden)
    with pytest.raises(ValueError, match="sealed Plan"):
        loop._plan_dor_errors("unread-workspace", state, sealed_inputs=sealed)


@pytest.mark.parametrize("field", [
    "acceptance", "contracts", "depends_on", "open_questions"])
@pytest.mark.parametrize("change", ["missing", "wrong-type"])
def test_sealed_plan_requires_complete_selected_requirement_records(
        inputs, monkeypatch, field, change):
    state, sealed = inputs
    requirement = sealed["requirements_by_id"]["R-TEST"]
    if change == "missing":
        del requirement[field]
    else:
        requirement[field] = "cannot-default-to-empty"
    _forbid_private_state(monkeypatch)
    monkeypatch.setattr(loop, "_plan_delivery_mode_from_file", _forbidden)
    with pytest.raises(ValueError, match="sealed Plan requirement records require explicit"):
        loop._plan_dor_errors("unread-workspace", state, sealed_inputs=sealed)


@pytest.mark.parametrize("case", [
    "extra-field", "requirement-id", "graph-fields", "foreign-design",
    "missing-tasks", "task-type", "apply"])
def test_sealed_plan_refuses_ambiguous_inputs_and_runtime_application(
        inputs, monkeypatch, case):
    state, sealed = inputs
    if case == "extra-field":
        sealed["runtime_root"] = "not-authorized"
    elif case == "requirement-id":
        sealed["requirements_by_id"]["R-TEST"]["id"] = "R-OTHER"
    elif case == "graph-fields":
        del sealed["graph"]["meta"]
    elif case == "foreign-design":
        sealed["approved_design"]["requirement"] = "R-OTHER"
    elif case == "missing-tasks":
        del state["tasks"]
    elif case == "task-type":
        state["tasks"] = [None]
    _forbid_private_state(monkeypatch)
    monkeypatch.setattr(loop, "_plan_delivery_mode_from_file", _forbidden)
    before = copy.deepcopy((state, sealed))
    with pytest.raises(ValueError, match="sealed Plan"):
        loop._plan_dor_errors("unread-workspace", state, case == "apply", sealed_inputs=sealed)
    assert (state, sealed) == before


def test_legacy_apply_still_merges_contracts_and_records_graph_authority(
        tmp_path, inputs, monkeypatch):
    state, sealed = inputs
    task = state["tasks"][0]
    task["contracts"] = [{"id": "contract:api", "relation": "consumes"}, "contract:extra"]
    _write_current_artifacts(tmp_path, state, sealed)
    monkeypatch.setattr(loop.tp, "git_head", lambda _: "c" * 40)
    monkeypatch.setattr(loop.reqs, "get_requirement",
                        lambda _ws, rid: sealed["requirements_by_id"].get(rid))
    monkeypatch.setattr(loop.depgraph, "load", lambda _: sealed["graph"])
    monkeypatch.setattr(loop.depgraph, "scan", lambda _: sealed["graph"])
    linked, recorded = [], []
    monkeypatch.setattr(loop.depgraph, "link_requirement_dep",
                        lambda *args: linked.append(args))
    monkeypatch.setattr(loop.depgraph, "record_edge",
                        lambda *args, **kwargs: recorded.append((args, kwargs)))
    assert loop._plan_dor_errors(str(tmp_path), state, True) == []
    assert task["contracts"] == [
        {"id": "contract:api", "relation": "provides"}, "contract:extra"]
    assert linked == [(str(tmp_path), "R-TEST", "R-DEP")]
    assert recorded == [((str(tmp_path), "req:R-TEST", "contract:api"),
                         {"kind": "provides", "confidence": "high"})]
    assert state["graph_dor"]["passed"] is True
    assert state["delivery_mode_receipt"]["mode"] == "build"
    assert task["impact_policy"] == loop.depgraph.impact_policy(task)
