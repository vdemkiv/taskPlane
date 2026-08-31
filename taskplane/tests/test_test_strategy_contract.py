import copy
import json
from pathlib import Path

import pytest

from taskplane.test_strategy import (
    StrategyContractError,
    advance_validation,
    classify_failures,
    seal_strategy,
    validate_strategy,
)


FIXTURE = Path(__file__).parent / "fixtures" / "test-strategy" / "r0001.json"
FAILURES = FIXTURE.parent / "failure-classes.json"
SEVERED_EDGES = FIXTURE.parent / "severed-edges.json"


def _strategy():
    return seal_strategy(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_design_and_build_contract_is_complete():
    strategy = _strategy()

    validated = validate_strategy(strategy)

    assert validated["schema"] == "taskplane.test-strategy/v1"
    assert {criterion["id"] for criterion in validated["acceptance_criteria"]} == {
        "AC-SET1",
        "AC-SET2",
        "AC-SET3",
        "AC-SET4",
        "AC-SET5",
        "AC-TST1",
        "AC-TST2",
        "AC-TST3",
        "AC-CI1",
        "AC-CI2",
        "AC-CLN1",
        "AC-CLN2",
        "AC-P0",
        "AC-REL",
        "AC-MET",
        "AC-REG",
    }
    assert all(
        selector.startswith("taskplane/tests/")
        and selector.rsplit("::", 1)[-1].startswith("test_")
        for criterion in validated["acceptance_criteria"]
        for selector in criterion["selectors"]
    )
    assert validated["failure_policy"]["classes"] == [
        "product",
        "test",
        "infrastructure",
        "environment",
    ]
    assert validated["failure_policy"]["correction_requires"] == [
        "class",
        "reason",
        "owner",
        "cluster",
    ]
    failure_fixtures = json.loads(FAILURES.read_text(encoding="utf-8"))
    assert [row["class"] for row in failure_fixtures["failures"]] == [
        "product",
        "test",
        "infrastructure",
        "environment",
    ]

    with pytest.raises(StrategyContractError, match="exact pytest node id"):
        changed = copy.deepcopy(strategy)
        changed["acceptance_criteria"][0]["selectors"] = [
            "taskplane/tests/test_test_strategy_contract.py"
        ]
        validate_strategy(changed)

    with pytest.raises(StrategyContractError, match="classified before correction"):
        classify_failures(
            strategy,
            [{"selector": "taskplane/tests/test_x.py::test_x", "reason": "red"}],
        )

    classified = classify_failures(
        strategy,
        [
            {
                "selector": "taskplane/tests/test_x.py::test_x",
                "class": "product",
                "reason": "behavior differs from AC",
                "owner": "X",
                "cluster": "behavior",
            }
        ],
    )
    assert classified[0]["correction_allowed"] is True

    evidence = None
    for layer in ("static", "exact-selector", "changed-radius", "proportional-suite"):
        evidence = advance_validation(strategy, layer, candidate_sha="a" * 40, prior=evidence)
    with pytest.raises(StrategyContractError, match="authoritative CI matrix"):
        advance_validation(strategy, "authoritative-ci", candidate_sha="b" * 40, prior=evidence)
    terminal = advance_validation(
        strategy, "authoritative-ci", candidate_sha="a" * 40, prior=evidence
    )
    assert terminal["authoritative"] is True
    assert terminal["matrix_runs"] == 1


def test_dashboard_producers_name_consumers_freshness_severed_edges_and_same_slice_fixtures():
    strategy = _strategy()
    dashboard = [
        producer
        for producer in strategy["producers"]
        if producer["id"].startswith("dashboard:")
    ]

    assert {producer["id"] for producer in dashboard} == {
        "dashboard:canonical-snapshot",
        "dashboard:phase-graphs",
        "dashboard:publication",
    }
    assert all(producer["consumers"] for producer in dashboard)
    assert all(producer["fingerprint_sha256"] for producer in dashboard)
    assert all(producer["severed_edges"] for producer in dashboard)
    assert all(
        fixture["slice"] == producer["slice"]
        for producer in dashboard
        for fixture in producer["interface_fixtures"]
    )
    severed_fixtures = json.loads(SEVERED_EDGES.read_text(encoding="utf-8"))
    assert {row["producer"] for row in severed_fixtures["mutations"]} >= {
        producer["id"] for producer in dashboard
    }

    validate_strategy(strategy)

    stale = copy.deepcopy(strategy)
    stale["producers"][0]["consumers"].append("unsealed-consumer")
    with pytest.raises(StrategyContractError, match="stale fingerprint"):
        validate_strategy(stale)

    severed = copy.deepcopy(strategy)
    severed["producers"][0]["consumers"].remove(
        severed["producers"][0]["severed_edges"][0]["consumer"]
    )
    severed = seal_strategy(severed)
    with pytest.raises(StrategyContractError, match="severed edge"):
        validate_strategy(severed)

    wrong_slice = copy.deepcopy(strategy)
    wrong_slice["producers"][0]["interface_fixtures"][0]["slice"] = "other-task"
    wrong_slice = seal_strategy(wrong_slice)
    with pytest.raises(StrategyContractError, match="same slice"):
        validate_strategy(wrong_slice)

    assert strategy["validation"]["layers"] == [
        "static",
        "exact-selector",
        "changed-radius",
        "proportional-suite",
        "authoritative-ci",
    ]
    assert strategy["validation"]["broad_local_default"] == "refuse"
    assert strategy["validation"]["authoritative_matrix_runs"] == 1
    assert strategy["validation"]["reuse_unchanged_green"] == "cite"
