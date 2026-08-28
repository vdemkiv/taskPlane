import copy
import json
from pathlib import Path

import pytest

from taskplane.design_contract import (
    R0013_ACCEPTANCE_OUTCOMES,
    acceptance_wave_errors,
)


ROOT = Path(__file__).resolve().parents[2]
APPROVED_AUTHORITY = (
    ROOT / "taskplane" / "tests" / "fixtures" /
    "r0013-approved-wave-authority.json"
)
APPROVED_SOURCE_REVISION = "00cd4f2c8183e57b6eae3f0cb6b0c580e00fe085"
APPROVED_SOURCE_BLOBS = {
    "design/contract.json": "4e51dcef6ec6b1208875c765df9c4ab61adcd112",
    "plan/tasks.json": "8dd6510f9ca234f79eb034f5183d922d2ec1fa8d",
}


def _approved_inputs():
    retained = json.loads(APPROVED_AUTHORITY.read_text(encoding="utf-8"))
    assert retained["schema"] == \
        "taskplane.r0013-approved-wave-authority/v1"
    assert retained["source_revision"] == APPROVED_SOURCE_REVISION
    assert retained["source_blobs"] == APPROVED_SOURCE_BLOBS
    return ({
        "outcome_ownership": retained["outcome_ownership"],
        "pair_classification": retained["pair_classification"],
    }, retained["tasks"])


def test_exactly_seven_acceptance_outcomes_and_complete_21_pair_map():
    contract, tasks = _approved_inputs()

    assert contract["outcome_ownership"]["acceptance_outcomes"] == list(
        R0013_ACCEPTANCE_OUTCOMES)
    assert len(contract["pair_classification"]["pairs"]) == 21
    assert acceptance_wave_errors(contract, tasks) == []


@pytest.mark.parametrize("mutation, expected", [
    ("ninth", "exactly AC1 through AC7"),
    ("missing-pair", "exactly 21 rows"),
    ("unexplained-serial", "lacks a named dependency or shared owner"),
    ("false-disjoint-design", "false-disjoint Design"),
    ("false-disjoint-plan", "false-disjoint Plan"),
])
def test_ninth_missing_unexplained_serial_or_false_disjoint_plan_is_refused(
        mutation, expected):
    contract, tasks = _approved_inputs()
    contract = copy.deepcopy(contract)
    tasks = copy.deepcopy(tasks)

    if mutation == "ninth":
        contract["outcome_ownership"]["acceptance_outcomes"].append("AC9")
    elif mutation == "missing-pair":
        contract["pair_classification"]["pairs"].pop()
    elif mutation == "unexplained-serial":
        serial = next(
            row for row in contract["pair_classification"]["pairs"]
            if row["disposition"] == "serialized")
        serial["reason"] = "keep these apart"
    elif mutation == "false-disjoint-design":
        lanes = contract["outcome_ownership"]["leaf_lanes"]
        ac1 = next(row for row in lanes if row["outcome"] == "AC1")
        ac2 = next(row for row in lanes if row["outcome"] == "AC2")
        ac2["exclusive_files"].append(ac1["exclusive_files"][0])
    else:
        ac1 = next(row for row in tasks if row["id"].startswith("t01-"))
        ac2 = next(row for row in tasks if row["id"].startswith("t02-"))
        ac2["scope"].append(ac1["scope"][0])

    assert any(expected in error
               for error in acceptance_wave_errors(contract, tasks))


def test_every_independent_pair_is_in_same_available_native_wave():
    contract, tasks = _approved_inputs()
    parallel = [
        row for row in contract["pair_classification"]["pairs"]
        if row["disposition"] == "parallel"
    ]
    assert {row["available_wave"] for row in parallel} == {"leaf-wave-1"}

    changed = copy.deepcopy(contract)
    changed_parallel = [
        row for row in changed["pair_classification"]["pairs"]
        if row["disposition"] == "parallel"
    ]
    changed_parallel[-1]["available_wave"] = "leaf-wave-2"

    assert any("share one available native wave" in error
               for error in acceptance_wave_errors(changed, tasks))


@pytest.mark.parametrize("forged_reason", [
    "shared owner",
    "depends on receipt",
    "shared native-authority-owner",
    "consumes bounded-stage-handoff receipt",
])
def test_serialized_reason_must_name_proven_artifact_or_actual_shared_owner(
        forged_reason):
    contract, tasks = _approved_inputs()
    changed = copy.deepcopy(contract)
    serial = next(
        row for row in changed["pair_classification"]["pairs"]
        if row["left"] == "AC1" and row["right"] == "AC4"
    )
    serial["reason"] = forged_reason

    assert any(
        "proven by approved artifacts" in error
        for error in acceptance_wave_errors(changed, tasks)
    )


def test_serialized_reason_must_name_the_pair_specific_shared_plan_owner():
    contract, tasks = _approved_inputs()
    changed = copy.deepcopy(contract)
    serial = next(
        row for row in changed["pair_classification"]["pairs"]
        if row["left"] == "AC3" and row["right"] == "AC4"
    )
    serial["reason"] = (
        "AC3 zero-lens execution authorization at the shared owner"
    )

    assert any(
        "proven by approved artifacts" in error
        for error in acceptance_wave_errors(changed, tasks)
    )


@pytest.mark.parametrize("generic_reason", [
    "depends on native wave",
    "requires fail closed",
    "depends on governed stage",
])
def test_generic_governance_phrase_cannot_authorize_any_serial_pair(
        generic_reason):
    contract, tasks = _approved_inputs()
    changed = copy.deepcopy(contract)
    serial_rows = [
        row for row in changed["pair_classification"]["pairs"]
        if row["disposition"] == "serialized"
    ]
    for row in serial_rows:
        row["reason"] = generic_reason

    errors = acceptance_wave_errors(changed, tasks)

    assert sum("proven by approved artifacts" in error
               for error in errors) == len(serial_rows) == 10


@pytest.mark.parametrize("left, right, forged_disposition", [
    ("AC1", "AC2", "serialized"),
    ("AC1", "AC4", "parallel"),
])
def test_disposition_cannot_depart_from_closed_approved_pair_map(
        left, right, forged_disposition):
    contract, tasks = _approved_inputs()
    changed = copy.deepcopy(contract)
    row = next(
        row for row in changed["pair_classification"]["pairs"]
        if row["left"] == left and row["right"] == right
    )
    row["disposition"] = forged_disposition
    row["available_wave"] = (
        "leaf-wave-1" if forged_disposition == "parallel" else None
    )

    assert any(
        "approved closed pair map" in error
        for error in acceptance_wave_errors(changed, tasks)
    )


def test_uniform_but_nonfirst_parallel_wave_is_refused():
    contract, tasks = _approved_inputs()
    changed = copy.deepcopy(contract)
    for row in changed["pair_classification"]["pairs"]:
        if row["disposition"] == "parallel":
            row["available_wave"] = "leaf-wave-2"

    errors = acceptance_wave_errors(changed, tasks)

    assert any("exact first native wave leaf-wave-1" in error
               for error in errors)
