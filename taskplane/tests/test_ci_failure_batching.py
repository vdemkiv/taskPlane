import copy
import json
from pathlib import Path

import pytest

from taskplane.ci_failure_batching import (
    FailureBatchError,
    build_correction_wave,
    build_failure_inventory,
    consolidate_plan_returns,
)


FIXTURES = Path(__file__).parent / "fixtures" / "ci-policy"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_red_matrix_is_classified_once_and_corrected_as_one_wave():
    matrix = _fixture("red-matrix.json")
    classifications = _fixture("failure-classifications.json")

    inventory = build_failure_inventory(matrix, classifications)
    assert inventory["complete"] is True
    assert inventory["classification_passes"] == 1
    assert [failure["class"] for failure in inventory["failures"]] == [
        "product",
        "test",
        "infrastructure",
        "environment",
    ]
    assert all(
        all(failure[field] for field in ("reason", "owner", "cluster"))
        for failure in inventory["failures"]
    )

    wave = build_correction_wave(inventory)
    assert wave["wave_count"] == 1
    assert wave["candidate_fingerprint"] == matrix["candidate_fingerprint"]
    assert wave["failure_inventory_fingerprint"] == inventory["fingerprint"]
    assert wave["rerun_cells"] == [
        "unit-core",
        "quality",
        "package",
        "dashboard-browser",
    ]
    assert wave["cited_unchanged_green"] == ["compatibility"]
    assert set(wave["clusters"]) == {
        "dashboard-state",
        "fixture-contract",
        "package-cache",
        "browser-runtime",
    }
    assert all(correction["evidence_fingerprint"] for correction in wave["corrections"])

    incomplete = copy.deepcopy(classifications)
    incomplete.pop()
    with pytest.raises(FailureBatchError, match="complete classified inventory"):
        build_failure_inventory(matrix, incomplete)

    already_classified = copy.deepcopy(matrix)
    already_classified["classification_receipt"] = inventory["fingerprint"]
    with pytest.raises(FailureBatchError, match="classified exactly once"):
        build_failure_inventory(already_classified, classifications)

    missing_change = copy.deepcopy(classifications)
    missing_change[0]["correction"] = {}
    with pytest.raises(FailureBatchError, match="changed product or evidence"):
        build_correction_wave(build_failure_inventory(matrix, missing_change))


def test_third_plan_return_consolidates_coupled_surfaces():
    returns = _fixture("plan-returns.json")

    assert consolidate_plan_returns(returns[:2])["successors"] == []
    consolidated = consolidate_plan_returns(returns)
    assert consolidated["return_count"] == 3
    assert consolidated["consolidated"] is True
    assert consolidated["successors"] == [
        {
            "id": "PLAN-STABILIZATION",
            "type": "stabilization",
            "status": "pending",
            "predecessors": ["return-1", "return-2", "return-3"],
            "coupled_surfaces": [
                "acceptance-allocation",
                "dashboard-contract",
                "dependency-overlay",
                "dispatch-policy",
            ],
        }
    ]

    reused = consolidate_plan_returns(
        returns,
        existing_successors=consolidated["successors"],
    )
    assert reused["successors"] == consolidated["successors"]
    assert reused["created"] is False

    duplicate = copy.deepcopy(returns)
    duplicate[2]["id"] = "return-2"
    with pytest.raises(FailureBatchError, match="unique"):
        consolidate_plan_returns(duplicate)
