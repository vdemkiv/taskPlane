from __future__ import annotations

from copy import deepcopy

import depgraph
import design_contract


def _edge_key(row: dict[str, str]) -> str:
    return f"{row['from']}->{row['to']}:{row['kind']}"


def _isolate_plan_validation(monkeypatch, contract: dict) -> None:
    monkeypatch.setattr(design_contract, "design_current_errors",
                        lambda _ws, _state: [])
    monkeypatch.setattr(design_contract, "design_contract",
                        lambda _ws: (deepcopy(contract), []))
    monkeypatch.setattr(design_contract, "acceptance_test_map",
                        lambda _contract: None)


def test_file_granular_overlay_modules_are_covered_by_exact_scopes(
        monkeypatch) -> None:
    overlays = [f"taskplane/overlay_{index:02d}.py" for index in range(20)]
    retained = [f"contract:retained-{index:02d}" for index in range(21)]
    modules = ["taskplane", *overlays, *retained]
    edges = [
        {"from": modules[index % len(modules)],
         "to": modules[(index + 1) % len(modules)],
         "kind": f"authority-{index:02d}"}
        for index in range(58)
    ]
    contract = {
        "requirement": "R-0001",
        "contracts": [],
        "graph": {
            "proposed_modules": modules,
            "proposed_edges": edges,
            "depth_policy": {
                "local_depth": 3,
                "boundary_mode": "contract-only",
                "contract_depth": 1,
                "requirement_depth": 1,
            },
        },
    }
    _isolate_plan_validation(monkeypatch, contract)
    graph = {"modules": {module: {} for module in modules}, "meta": {}}
    monkeypatch.setattr(depgraph, "load", lambda _ws: graph)

    task = {
        "id": "overlay-owner",
        "scope": overlays,
        "new_modules": retained,
        "design_edges": [_edge_key(row) for row in edges],
        "impact_policy": deepcopy(contract["graph"]["depth_policy"]),
        "criteria": [],
        "status": "pending",
    }
    state = {"design_required": True, "tasks": [task]}

    assert design_contract.design_plan_errors("/workspace", state) == []

    directory_only = deepcopy(state)
    directory_only["tasks"][0]["scope"] = ["taskplane/**"]
    errors = design_contract.design_plan_errors("/workspace", directory_only)
    assert any("approved design modules are not covered" in error
               and overlays[0] in error for error in errors)

    confined = deepcopy(state)
    confined["tasks"][0]["scope"] = overlays[:-1]
    errors = design_contract.design_plan_errors("/workspace", confined)
    assert any(overlays[-1] in error for error in errors)

    unknown = deepcopy(state)
    unknown["tasks"][0]["scope"] = ["taskplane/unknown_overlay.py"]
    errors = design_contract.design_plan_errors("/workspace", unknown)
    assert any(overlays[0] in error for error in errors)


def test_third_plan_return_requires_one_stabilization_successor(
        monkeypatch) -> None:
    policy = {
        "local_depth": 0,
        "boundary_mode": "contract-only",
        "contract_depth": 0,
        "requirement_depth": 0,
    }
    contract = {
        "requirement": "R-0001",
        "contracts": [],
        "graph": {"proposed_modules": [], "proposed_edges": [],
                  "depth_policy": policy},
    }
    _isolate_plan_validation(monkeypatch, contract)
    monkeypatch.setattr(depgraph, "scope_modules",
                        lambda _ws, _scope: [])

    ordinary = {
        "id": "ordinary", "scope": ["taskplane/ordinary.py"],
        "new_modules": [], "design_edges": [], "impact_policy": policy,
        "criteria": [], "status": "pending", "type": "implementation",
    }
    state = {
        "design_required": True,
        "replan_history": [{"reason": "first"}, {"reason": "second"}],
        "tasks": [ordinary],
    }

    errors = design_contract.design_plan_errors("/workspace", state)
    assert any("third Plan return" in error and "stabilization" in error
               for error in errors)

    successor = {
        **ordinary, "id": "PLAN-STABILIZATION", "type": "stabilization",
    }
    accepted = deepcopy(state)
    accepted["tasks"].append(successor)
    assert design_contract.design_plan_errors("/workspace", accepted) == []

    duplicate = deepcopy(accepted)
    duplicate["tasks"].append({**successor, "id": "OTHER-STABILIZATION"})
    errors = design_contract.design_plan_errors("/workspace", duplicate)
    assert any("exactly one" in error and "stabilization" in error
               for error in errors)

    not_pending = deepcopy(accepted)
    not_pending["tasks"][-1]["status"] = "running"
    errors = design_contract.design_plan_errors("/workspace", not_pending)
    assert any("pending" in error and "stabilization" in error
               for error in errors)

    below_threshold = deepcopy(state)
    below_threshold["replan_history"] = [{"reason": "first"}]
    assert design_contract.design_plan_errors(
        "/workspace", below_threshold) == []
