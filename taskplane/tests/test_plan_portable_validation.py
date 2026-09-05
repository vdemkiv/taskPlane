"""Sealed Plan/graph inputs reuse the native policy without private state."""
from __future__ import annotations

import copy

import pytest

from taskplane import design_contract as dc


@pytest.fixture
def graph():
    return {
        "modules": {"core": {}, "src/core/api.py": {},
                    "contract:api": {}, "svc:client": {}, "req:R-TEST": {}},
        "edges": [
            {"from": "svc:client", "to": "contract:api", "kind": "consumes"},
            {"from": "contract:api", "to": "core", "kind": "provides"},
            {"from": "req:R-TEST", "to": "svc:client", "kind": "planned"},
        ],
        "meta": {"content_fingerprint": "a" * 64,
                 "module_ids": {"src/core": "core"}},
    }


@pytest.fixture
def plan_inputs(graph):
    policy = {"local_depth": 3, "boundary_mode": "contract-only",
              "contract_depth": 2, "requirement_depth": 1}
    contract = {
        "requirement": "R-TEST", "contracts": [{"id": "contract:api"}],
        "graph": {
            "proposed_modules": ["core", "src/core/api.py", "new"],
            "proposed_edges": [{"from": "core", "to": "contract:api",
                                "kind": "provides"}],
            "depth_policy": copy.deepcopy(policy),
        },
        "acceptance_map": [{"criterion": "The interface remains compatible",
                            "tests": ["taskplane/tests/test_plan_portable_validation.py::"
                                      "test_snapshot_plan_reuses_native_conformance"]}],
    }
    tasks = [{"id": "T-1", "scope": ["src/core/api.py"],
              "new_modules": ["new"], "contracts": ["contract:api"],
              "design_edges": ["core->contract:api:provides"],
              "acceptance_refs": ["The interface remains compatible"],
              "criteria": ["The task's incremental completion criterion"],
              "impact_policy": copy.deepcopy(policy)}]
    return contract, tasks, graph


def _forbidden(*_args, **_kwargs):
    raise AssertionError("snapshot validation must not read or refresh private state")


@pytest.mark.parametrize("case, expected", [
    ("valid", None),
    ("module", "approved design modules"),
    ("contract", "approved design contracts"),
    ("edge", "approved design edges"),
    ("depth", "dependency depth policy is narrower"),
    ("acceptance", "acceptance_refs are not Design-declared"),
    ("selector", "design acceptance tests are invalid"),
    ("stabilization", "third Plan return requires exactly one"),
    ("acceptance-wave", "acceptance wave outcome_ownership must be an object"),
])
def test_snapshot_plan_reuses_native_conformance(
        plan_inputs, monkeypatch, case, expected):
    contract, tasks, graph = plan_inputs
    history = []
    if case == "module":
        tasks[0]["new_modules"] = []
    elif case == "contract":
        tasks[0]["contracts"] = []
    elif case == "edge":
        tasks[0]["design_edges"] = []
    elif case == "depth":
        tasks[0]["impact_policy"]["contract_depth"] = 0
    elif case == "acceptance":
        tasks[0]["acceptance_refs"] = ["Foreign acceptance"]
    elif case == "selector":
        contract["acceptance_map"][0]["tests"] = []
    elif case == "stabilization":
        history = [{"reason": "first return"}, {"reason": "second return"}]
    elif case == "acceptance-wave":
        contract["requirement"] = "R-0013"
    state = {"design_required": True, "design_fingerprint": "b" * 64,
             "requirement_id": contract["requirement"], "tasks": tasks,
             "replan_history": history}
    monkeypatch.setattr(dc, "design_contract", lambda _: (contract, []))
    monkeypatch.setattr(dc.depgraph, "load", lambda _: graph)
    native = dc.design_plan_errors("unused-workspace", state)
    assert bool(native) is (expected is not None), native
    if expected:
        assert expected in " ".join(native)
    before = copy.deepcopy((contract, tasks, graph, history))
    monkeypatch.setattr(dc, "design_contract", _forbidden)
    monkeypatch.setattr(dc.reqs, "get_requirement", _forbidden)
    monkeypatch.setattr(dc.depgraph, "load", _forbidden)
    monkeypatch.setattr(dc.depgraph, "scan", _forbidden)
    assert dc.design_plan_artifact_errors(
        contract, tasks=tasks, graph=graph, replan_history=history) == native
    assert (contract, tasks, graph, history) == before


@pytest.mark.parametrize("scope, overlay", [
    (["src/core/api.py"], True),
    (["src/core/*.py"], False),
    (["src/core/"], False),
    (["src/core/../core/api.py"], False),
    (["/src/core/api.py"], False),
    (["src/core/unknown.py"], False),
])
def test_snapshot_scope_preserves_declared_ids_and_exact_file_overlays(
        graph, monkeypatch, scope, overlay):
    monkeypatch.setattr(dc.depgraph, "load", lambda _: graph)
    native = dc.depgraph.scope_modules("unused-workspace", scope)
    assert ("src/core/api.py" in native) is overlay
    if overlay:
        assert "core" in native
    monkeypatch.setattr(dc.depgraph, "load", _forbidden)
    assert dc.depgraph.scope_modules_from_graph(graph, scope) == native


@pytest.mark.parametrize("case, expected", [
    ("valid", None),
    ("boundary", "invalid graph boundary_mode"),
    ("depth", "invalid dependency depth policy"),
    ("new", "new/unknown graph modules were not declared"),
    ("distributed", "distributed/system work must declare"),
    ("contract", "contracts are not recorded"),
    ("contract-prefix", "contract ids need contract: or resource: prefixes"),
    ("quality", "graph scan quality is degraded"),
])
def test_snapshot_readiness_has_native_errors_and_impact_without_private_reads(
        graph, monkeypatch, case, expected):
    task = {"id": "T-1", "scope": ["src/core/**"],
            "contracts": ["contract:api"], "new_modules": ["core"],
            "impact_policy": {"local_depth": 3, "contract_depth": 2}}
    if case == "boundary":
        task["impact_policy"]["boundary_mode"] = "unbounded"
    elif case == "depth":
        task["impact_policy"]["local_depth"] = "invalid"
    elif case == "new":
        task["scope"] = ["src/new/**"]
        task["new_modules"] = []
    elif case == "distributed":
        task["type"] = "distributed"
        task["contracts"] = []
    elif case == "contract":
        task["contracts"] = ["contract:missing"]
    elif case == "contract-prefix":
        task["contracts"] = ["foreign:api"]
    elif case == "quality":
        graph["meta"]["graph_scan_quality"] = {
            "schema": dc.depgraph.GRAPH_SCAN_QUALITY_SCHEMA,
            "degraded": True, "failures": [{"reason": "fixture scan failure"}]}
    calls = []
    original_impact = dc.depgraph.impact

    def recorded_impact(workspace, modules, *, policy):
        calls.append((workspace, modules, policy))
        return original_impact(workspace, modules, policy=policy)

    monkeypatch.setattr(dc.depgraph, "scan", lambda _: graph)
    monkeypatch.setattr(dc.depgraph, "load", lambda _: graph)
    monkeypatch.setattr(dc.depgraph, "impact", recorded_impact)
    native = dc.depgraph.readiness("unused-workspace", [task])
    assert calls and calls[0][0] == "unused-workspace"
    assert native["passed"] is (expected is None), native
    if expected:
        assert expected in " ".join(native["errors"])
    if case == "valid":
        assert native["warnings"]  # Existing-module declaration stays a warning.
        assert native["tasks"][0]["impact"]["total_impacted"] > 0
    before = copy.deepcopy((graph, task))
    monkeypatch.setattr(dc.depgraph, "scan", _forbidden)
    monkeypatch.setattr(dc.depgraph, "load", _forbidden)
    monkeypatch.setattr(dc.depgraph, "impact", _forbidden)
    assert dc.depgraph.readiness_from_graph(graph, [task]) == native
    assert (graph, task) == before


@pytest.mark.parametrize("policy", [
    {"local_depth": 1},
    {"local_depth": 4, "boundary_mode": "stop"},
    {"local_depth": 4, "contract_depth": 2, "requirement_depth": 0},
    {"local_depth": 4, "boundary_mode": "expand", "contract_depth": 4},
])
def test_snapshot_impact_preserves_boundary_and_depth_semantics(graph, monkeypatch, policy):
    monkeypatch.setattr(dc.depgraph, "load", lambda _: graph)
    native = dc.depgraph.impact("unused-workspace", ["core"], policy=policy)
    monkeypatch.setattr(dc.depgraph, "load", _forbidden)
    assert dc.depgraph.impact_from_graph(graph, ["core"], policy=policy) == native


@pytest.mark.parametrize("graph", [None, {}, {"modules": {}}])
def test_readiness_requires_an_explicit_complete_graph_snapshot(graph):
    with pytest.raises(ValueError, match="graph snapshot"):
        dc.depgraph.readiness_from_graph(graph, [])


def test_snapshot_inputs_do_not_turn_missing_tasks_or_history_into_empty_work(plan_inputs):
    contract, tasks, graph = plan_inputs
    with pytest.raises(ValueError, match="explicit list"):
        dc.depgraph.readiness_from_graph(graph, None)
    with pytest.raises(ValueError, match="explicit lists"):
        dc.design_plan_artifact_errors(
            contract, tasks=tasks, graph=graph, replan_history=None)
    with pytest.raises(ValueError, match="graph snapshot"):
        dc.design_plan_artifact_errors(
            contract, tasks=[], graph=None, replan_history=[])


def test_full_plan_preserves_completed_coverage_and_stabilization_successor(plan_inputs):
    contract, tasks, graph = plan_inputs
    tasks[0]["status"] = "passed"
    tasks.append({"id": "T-2", "type": "stabilization", "status": "pending",
                  "scope": ["src/core/**"], "criteria": ["Finish only remaining work"],
                  "acceptance_refs": []})
    history = [{"reason": "first return"}, {"reason": "second return"}]
    before = copy.deepcopy(tasks)
    assert dc.design_plan_artifact_errors(
        contract, tasks=tasks, graph=graph, replan_history=history) == []
    assert tasks == before
    errors = dc.design_plan_artifact_errors(
        contract, tasks=tasks[1:], graph=graph, replan_history=history)
    assert "approved design modules" in " ".join(errors)
    assert "approved design contracts" in " ".join(errors)
    assert "approved design edges" in " ".join(errors)


def test_legacy_graph_wrappers_keep_compatibility_with_partial_graph_records(monkeypatch):
    partial = {"modules": {}, "edges": []}
    monkeypatch.setattr(dc.depgraph, "load", lambda _: partial)
    assert dc.depgraph.scope_modules("unused-workspace", ["src/core/**"]) == ["core"]
    assert dc.depgraph.impact("unused-workspace", ["src/core/api.py"])["unknown"] == ["core"]
    with pytest.raises(ValueError, match="graph snapshot"):
        dc.depgraph.impact_from_graph(partial, ["core"])


def test_native_design_wrapper_keeps_requirement_anchor_and_read_refusals(monkeypatch):
    state = {"design_required": True, "design_fingerprint": "b" * 64,
             "requirement_id": "R-TEST", "tasks": []}
    monkeypatch.setattr(dc.depgraph, "load", _forbidden)
    monkeypatch.setattr(dc, "design_contract", lambda _: (None, ["fixture missing Design"]))
    assert dc.design_plan_errors("unused-workspace", state) == [
        "approved design is unavailable: fixture missing Design"]
    monkeypatch.setattr(dc, "design_contract", lambda _: ({"requirement": "R-FOREIGN"}, []))
    assert "anchored to a different requirement" in dc.design_plan_errors(
        "unused-workspace", state)[0]
