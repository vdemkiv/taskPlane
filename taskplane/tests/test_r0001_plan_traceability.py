from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from taskplane import plan_topology


ROOT = Path(__file__).resolve().parents[2]


def _approved_artifacts() -> tuple[dict, dict]:
    design = json.loads(
        (ROOT / "design" / "contract.json").read_text(encoding="utf-8")
    )
    plan = json.loads(
        (ROOT / "plan" / "tasks.json").read_text(encoding="utf-8")
    )
    return design, plan


def test_traceability_foreign_keys_and_bidirectional_coverage(monkeypatch):
    design, plan = _approved_artifacts()

    receipt = plan_topology.build_plan_traceability(design, plan)

    assert receipt["schema"] == "taskplane.plan-traceability/v1"
    assert receipt["status"] == "closed"
    assert receipt["producer_chain"] == [
        "taskplane/graph_decomposition.py",
        "taskplane/depgraph.py",
        "taskplane/plan_topology.py",
    ]
    assert receipt["counts"] == {
        "criteria": 21,
        "contracts": 18,
        "tasks": 23,
        "journeys": 8,
        "wiring_rows": 34,
        "design_edges": 25,
    }
    assert receipt["depth_policy"] == {
        "local_depth": 3,
        "boundary_mode": "contract-only",
        "contract_depth": 1,
        "requirement_depth": 1,
    }
    assert [row["id"] for row in receipt["wiring_rows"]] == [
        f"W{number:02d}" for number in range(1, 35)
    ]
    assert all(row["task"] in receipt["tasks"] for row in receipt["wiring_rows"])
    assert all(row["tasks"] for row in receipt["criteria"].values())
    assert all(row["tasks"] for row in receipt["contracts"].values())
    assert all(row["criteria"] for row in receipt["tasks"].values())
    assert all(row["contracts"] for row in receipt["tasks"].values())
    assert all(row["task"] in receipt["tasks"] for row in receipt["journeys"].values())
    assert receipt["fingerprint"] == plan_topology.build_plan_traceability(
        design, plan
    )["fingerprint"]

    foreign_criterion = copy.deepcopy(plan)
    foreign_criterion["tasks"][0]["criteria"] = ["FP-AC99 invented"]
    foreign_criterion["tasks"][0]["acceptance_refs"] = ["FP-AC99 invented"]
    with pytest.raises(plan_topology.PlanTopologyError, match="foreign criterion"):
        plan_topology.build_plan_traceability(design, foreign_criterion)

    orphan_contract = copy.deepcopy(plan)
    contract_id = "contract:taskplane.knowledge-apply-receipt/v1"
    for task in orphan_contract["tasks"]:
        task["contracts"] = [
            value for value in task["contracts"] if value != contract_id
        ]
    with pytest.raises(plan_topology.PlanTopologyError, match="orphan contract"):
        plan_topology.build_plan_traceability(design, orphan_contract)

    foreign_wiring_task = copy.deepcopy(plan)
    foreign_wiring_task["wiring_manifest"][-1]["task"] = "T99"
    with pytest.raises(plan_topology.PlanTopologyError, match="foreign task"):
        plan_topology.build_plan_traceability(design, foreign_wiring_task)

    duplicate_wiring = copy.deepcopy(plan)
    duplicate_wiring["wiring_manifest"][-1]["id"] = "W33"
    with pytest.raises(plan_topology.PlanTopologyError, match="W01-W34"):
        plan_topology.build_plan_traceability(design, duplicate_wiring)

    upstream = (
        plan_topology._depgraph.graph_decomposition
        .design_traceability_inventory
    )
    with monkeypatch.context() as patch:
        def severed_decomposition_output(contract):
            inventory = upstream(contract)
            inventory["producer_chain"] = []
            return inventory

        patch.setattr(
            plan_topology._depgraph.graph_decomposition,
            "design_traceability_inventory",
            severed_decomposition_output,
        )
        with pytest.raises(
            plan_topology.PlanTopologyError,
            match="graph_decomposition producer provenance",
        ):
            plan_topology.build_plan_traceability(design, plan)

    with monkeypatch.context() as patch:
        patch.setattr(
            plan_topology._depgraph,
            "design_traceability_inventory",
            upstream,
        )
        with pytest.raises(
            plan_topology.PlanTopologyError,
            match="depgraph producer provenance",
        ):
            plan_topology.build_plan_traceability(design, plan)


def test_plan_owner_inventory_complete(monkeypatch):
    design, plan = _approved_artifacts()

    inventory = plan_topology.build_plan_owner_inventory(design, plan)

    assert inventory["schema"] == "taskplane.plan-owner-inventory/v1"
    assert inventory["status"] == "closed"
    assert inventory["producer_owners"] == {
        "taskplane/plan_topology.py": "plan-traceability-producer-owner",
        "taskplane/wiring_closure.py": "plan-wiring-producer-owner",
    }
    assert len(inventory["task_owners"]) == 23
    assert len(inventory["criterion_owners"]) == 21
    assert len(inventory["journey_owners"]) == 8
    assert len(inventory["responsibility_owners"]) == 11
    assert len(set(inventory["task_owners"].values())) == 23
    assert len(set(inventory["criterion_owners"].values())) == 21
    assert len(set(inventory["journey_owners"].values())) == 8
    assert inventory["task_owners"]["T00"] == "traceability-owner"
    assert inventory["criterion_owners"]["FP-AC01"] == "source-coverage owner"
    assert inventory["journey_owners"]["J7"] == "decomposition pipeline owner"

    missing_owner = copy.deepcopy(plan)
    missing_owner["tasks"][0]["owner"] = ""
    with pytest.raises(plan_topology.PlanTopologyError, match="owner is required"):
        plan_topology.build_plan_owner_inventory(design, missing_owner)

    duplicate_authority = copy.deepcopy(plan)
    duplicate_authority["tasks"][1]["owner"] = duplicate_authority["tasks"][0][
        "owner"
    ]
    with pytest.raises(plan_topology.PlanTopologyError, match="duplicate task authority"):
        plan_topology.build_plan_owner_inventory(design, duplicate_authority)

    with monkeypatch.context() as patch:
        patch.setattr(
            plan_topology._wiring_closure,
            "plan_owner_producer_inventory",
            lambda: {},
        )
        with pytest.raises(
            plan_topology.PlanTopologyError,
            match="wiring_closure producer ownership",
        ):
            plan_topology.build_plan_owner_inventory(design, plan)
