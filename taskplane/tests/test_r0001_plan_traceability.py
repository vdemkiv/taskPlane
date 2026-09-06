from __future__ import annotations

from collections import Counter
import copy
import json
from pathlib import Path

import pytest

from taskplane import plan_topology


ROOT = Path(__file__).resolve().parents[2]

_SOURCE_LIMITS = {
    "local_depth": 3,
    "max_fanout": 2,
    "max_elapsed_ms": 10,
    "parsers": ["declarative", "python-ast", "runtime-probe"],
    "languages": ["data", "python", "runtime"],
    "policy": "approved",
}
_BOUND_SOURCE = [
    {"id": "file:taskplane/depgraph.py", "kind": "file"},
    {"id": "module:taskplane", "kind": "module"},
    {"id": "symbol:depgraph.scan", "kind": "symbol"},
    {"id": "configuration:components.yaml", "kind": "configuration"},
    {
        "id": "contract:taskplane-source-touchpoint-coverage-v1",
        "kind": "contract",
    },
    {"id": "runtime:tp-graph-scan-strict", "kind": "runtime"},
]


def _source_observations() -> dict[str, dict]:
    observations = {}
    for row in _BOUND_SOURCE:
        kind = row["kind"]
        observations[row["id"]] = {
            "verified": True,
            "state": "present",
            "source_fingerprint": f"sha256:{kind}",
            "parser": "python-ast" if kind in {"file", "module", "symbol"}
            else "runtime-probe" if kind == "runtime"
            else "declarative",
            "language": "python" if kind in {"file", "module", "symbol"}
            else "runtime" if kind == "runtime"
            else "data",
            "policy": "approved",
            "depth": 1,
            "fanout": 1,
            "elapsed_ms": 1,
        }
    return observations


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


def test_bound_source_coverage_is_deterministic_complete_and_verified_once(
    monkeypatch,
):
    depgraph = plan_topology._depgraph
    observations = _source_observations()
    calls = Counter()

    def verify(bound_input):
        calls[bound_input["id"]] += 1
        return copy.deepcopy(observations[bound_input["id"]])

    receipt = depgraph.build_source_touchpoint_coverage(
        "source-tree-a",
        _BOUND_SOURCE,
        limits=_SOURCE_LIMITS,
        verifier=verify,
    )

    assert receipt["schema"] == "taskplane.source-touchpoint-coverage/v1"
    assert receipt["status"] == "complete"
    assert receipt["complete"] is True
    assert receipt["stopping_conditions"] == []
    assert receipt["limits"] == _SOURCE_LIMITS
    assert set(receipt["touchpoints"]) == {
        row["id"] for row in _BOUND_SOURCE
    }
    assert set(receipt["kinds"]) == {
        "file",
        "module",
        "symbol",
        "configuration",
        "contract",
        "runtime",
    }
    assert calls == Counter({row["id"]: 1 for row in _BOUND_SOURCE})

    replay_calls = Counter()

    def replay_verify(bound_input):
        replay_calls[bound_input["id"]] += 1
        return copy.deepcopy(observations[bound_input["id"]])

    replay = depgraph.build_source_touchpoint_coverage(
        "source-tree-a",
        list(reversed(_BOUND_SOURCE)),
        limits=dict(reversed(list(_SOURCE_LIMITS.items()))),
        verifier=replay_verify,
    )
    assert replay == receipt
    assert replay_calls == Counter({row["id"]: 1 for row in _BOUND_SOURCE})

    changed_observations = copy.deepcopy(observations)
    changed_observations[_BOUND_SOURCE[0]["id"]]["source_fingerprint"] = (
        "sha256:changed"
    )
    changed = depgraph.build_source_touchpoint_coverage(
        "source-tree-b",
        _BOUND_SOURCE,
        limits=_SOURCE_LIMITS,
        verifier=lambda row: copy.deepcopy(changed_observations[row["id"]]),
    )
    assert changed["fingerprint"] != receipt["fingerprint"]

    with pytest.raises(ValueError, match="source tree"):
        depgraph.derive_verified_source(
            "unused", {"meta": {"scanned_head": "source-tree-b"}}, receipt
        )

    expected = ([{"id": "taskplane::core"}], {"components": 1})
    with monkeypatch.context() as patch:
        patch.setattr(
            depgraph.graph_decomposition,
            "derive",
            lambda workspace, graph, previous: expected,
        )
        assert depgraph.derive_verified_source(
            "unused",
            {"meta": {"scanned_head": "source-tree-a"}},
            receipt,
        ) == expected


@pytest.mark.parametrize(
    ("field", "value", "stop_reason"),
    [
        ("verified", False, "unverified"),
        ("state", "missing", "missing"),
        ("state", "ambiguous", "ambiguous"),
        ("state", "unsupported", "unsupported"),
        ("state", "truncated", "truncated"),
        ("state", "rejected", "rejected"),
        ("parser", "unknown-parser", "parser"),
        ("language", "unknown-language", "language"),
        ("policy", "denied", "policy"),
        ("depth", 4, "depth"),
        ("fanout", 3, "fan-out"),
        ("elapsed_ms", 11, "time"),
    ],
    ids=(
        "unverified",
        "missing",
        "ambiguous",
        "unsupported",
        "truncated",
        "rejected",
        "parser",
        "language",
        "policy",
        "depth",
        "fan-out",
        "time",
    ),
)
def test_source_coverage_stop_reason_is_partial_and_blocks_decomposition(
    monkeypatch, field, value, stop_reason
):
    depgraph = plan_topology._depgraph
    observations = _source_observations()
    target = _BOUND_SOURCE[0]["id"]
    observations[target][field] = value
    calls = Counter()

    def verify(bound_input):
        calls[bound_input["id"]] += 1
        return copy.deepcopy(observations[bound_input["id"]])

    receipt = depgraph.build_source_touchpoint_coverage(
        "source-tree-a",
        _BOUND_SOURCE,
        limits=_SOURCE_LIMITS,
        verifier=verify,
    )

    assert receipt["status"] == "partial"
    assert receipt["complete"] is False
    assert stop_reason in receipt["touchpoints"][target]["stop_reasons"]
    assert {row["reason"] for row in receipt["stopping_conditions"]} >= {
        stop_reason
    }
    assert calls == Counter({row["id"]: 1 for row in _BOUND_SOURCE})
    with pytest.raises(ValueError, match="source coverage is partial"):
        depgraph.require_complete_source_coverage(receipt)
    with monkeypatch.context() as patch:
        patch.setattr(
            depgraph.graph_decomposition,
            "derive",
            lambda *_args, **_kwargs: pytest.fail(
                "partial coverage reached decomposition"
            ),
        )
        with pytest.raises(ValueError, match="source coverage is partial"):
            depgraph.derive_verified_source(
                "unused",
                {"meta": {"scanned_head": "source-tree-a"}},
                receipt,
            )


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

    with monkeypatch.context() as patch:
        patch.setattr(
            plan_topology,
            "PLAN_TRACEABILITY_PRODUCER_OWNER",
            "",
        )
        with pytest.raises(
            plan_topology.PlanTopologyError,
            match="plan_topology.py owner is required",
        ):
            plan_topology.build_plan_owner_inventory(design, plan)
