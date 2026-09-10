import copy
import json
from pathlib import Path

import pytest

from taskplane import build_quality, test_strategy
from taskplane.test_strategy import (
    StrategyContractError,
    seal_strategy,
    validate_strategy,
)


ROOT = Path(__file__).resolve().parents[2]
SETTINGS_CONTRACT = (Path(__file__).parent / "fixtures" / "test-strategy" /
                     "settings-contract.json")
FAILURES = (Path(__file__).parent / "fixtures" / "test-strategy" /
            "failure-classes.json")
SEVERED_EDGES = (Path(__file__).parent / "fixtures" / "test-strategy" /
                 "severed-edges.json")
PORTFOLIO = ROOT / "taskplane" / "test_portfolio.json"


def _strategy():
    fixture = json.loads(SETTINGS_CONTRACT.read_text(encoding="utf-8"))
    return seal_strategy(fixture["strategy"])


def test_strategy_validates_contract_and_refuses_nonexact_selector():
    strategy = _strategy()
    validated = validate_strategy(strategy)
    assert validated["schema"] == "taskplane.test-strategy/v1"
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

    assert test_strategy.VALIDATION_LAYERS is build_quality.VALIDATION_LAYERS
    assert not hasattr(test_strategy, "advance_validation")
    assert not hasattr(test_strategy, "classify_failures")


def test_dashboard_producers_name_consumers_freshness_severed_edges_and_same_slice_fixtures():
    strategy = _strategy()
    portfolio = json.loads(PORTFOLIO.read_text(encoding="utf-8"))
    fixture_consumers = {
        fixture["path"]: fixture["consumer_selectors"]
        for fixture in portfolio["retained_fixtures"]
    }
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
    assert all((ROOT / producer["path"]).is_file() for producer in strategy["producers"])
    assert all(
        (ROOT / consumer).is_file()
        for producer in strategy["producers"]
        for consumer in producer["consumers"]
        if consumer.startswith(("taskplane/", "hooks/"))
    )
    assert all(producer["fingerprint_sha256"] for producer in dashboard)
    assert all(producer["severed_edges"] for producer in dashboard)
    assert all(
        fixture["slice"] == producer["slice"]
        for producer in dashboard
        for fixture in producer["interface_fixtures"]
    )
    assert all(
        (ROOT / fixture["path"]).is_file()
        and fixture_consumers.get(fixture["path"])
        for producer in strategy["producers"]
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

    in_process = copy.deepcopy(strategy)
    in_process["producers"][0]["interface_kind"] = "in-process"
    in_process["producers"][0]["interface_fixtures"] = []
    validate_strategy(seal_strategy(in_process))

    serialized_without_fixture = copy.deepcopy(strategy)
    serialized_without_fixture["producers"][0]["interface_kind"] = "serialized"
    serialized_without_fixture["producers"][0]["interface_fixtures"] = []
    with pytest.raises(StrategyContractError, match="must name interface fixtures"):
        validate_strategy(seal_strategy(serialized_without_fixture))

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
