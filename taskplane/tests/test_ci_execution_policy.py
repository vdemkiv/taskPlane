import copy
import json
from pathlib import Path

import pytest

from taskplane.ci_policy import (
    CIPolicyError,
    advance_validation,
    build_ci_plan,
    evaluate_ci_metrics,
    freeze_candidate,
)


FIXTURES = Path(__file__).parent / "fixtures" / "ci-policy"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _candidate():
    return freeze_candidate(_fixture("candidate.json"))


def test_validation_progression_requires_one_authoritative_ci_run():
    candidate = _candidate()
    evidence = None
    for layer, execution in (
        ("static", "local"),
        ("exact-selector", "local"),
        ("changed-radius", "ci"),
        ("proportional-suite", "ci"),
        ("authoritative-ci", "ci"),
    ):
        evidence = advance_validation(
            candidate, layer, execution=execution, prior=evidence
        )

    assert evidence["completed"] == [
        "static",
        "exact-selector",
        "changed-radius",
        "proportional-suite",
        "authoritative-ci",
    ]
    assert evidence["authoritative"] is True
    assert evidence["matrix_runs"] == 1

    tampered = copy.deepcopy(evidence)
    tampered["completed"].pop()
    tampered["matrix_runs"] = 0
    with pytest.raises(CIPolicyError, match="validation evidence is stale"):
        advance_validation(
            candidate,
            "authoritative-ci",
            execution="ci",
            prior=tampered,
        )

    with pytest.raises(CIPolicyError, match="broad local"):
        advance_validation(
            candidate,
            "changed-radius",
            execution="local",
            prior={
                "schema": "taskplane.ci-validation/v1",
                "candidate_fingerprint": candidate["fingerprint"],
                "completed": ["static", "exact-selector"],
                "cited_unchanged_green": [],
                "matrix_runs": 0,
            },
        )

def test_ci_shards_cleanup_and_candidate_freeze_are_authoritative():
    candidate = _candidate()
    plan = build_ci_plan(candidate, _fixture("ci-plan.json"))

    assert plan["candidate_frozen_before_cells"] is True
    assert plan["candidate_fingerprint"] == candidate["fingerprint"]
    assert plan["source_sha"] == candidate["source_sha"]
    assert plan["max_parallel"] >= 4
    assert plan["validation_domains"] == [
        "primary", "compatibility-quality-package", "browser",
    ]
    assert plan["cancellation"] == {
        "group": "pull-request-482",
        "cancel_in_progress": True,
        "scope": "same-pr-heads-only",
    }
    assert plan["serializations"] == [
        {
            "name": "package-index", "cells": ["package"],
            "reason": "package provenance consumes the built archive",
        }
    ]

    occupied_selectors = set()
    for cell in plan["cells"]:
        assert cell["candidate_fingerprint"] == candidate["fingerprint"]
        assert cell["timeout_seconds"] <= 600
        assert not occupied_selectors.intersection(cell["selectors"])
        occupied_selectors.update(cell["selectors"])
        assert cell["cleanup"]["registered_before_run"] is True
        assert cell["cleanup"]["outcomes"] == [
            "success",
            "failure",
            "cancellation",
            "interruption",
            "timeout",
            "handoff",
        ]

    unsafe = _fixture("ci-plan.json")
    unsafe["run"]["ref_kind"] = "protected-main"
    unsafe["run"]["cancel_in_progress"] = True
    with pytest.raises(CIPolicyError, match="protected-main"):
        build_ci_plan(candidate, unsafe)

    wrong_test_shards = _fixture("ci-plan.json")
    wrong_test_shards["settings"]["tests"]["shards"] = 3
    with pytest.raises(CIPolicyError, match="pytest cells"):
        build_ci_plan(candidate, wrong_test_shards)

    stale_plan = _fixture("ci-plan.json")
    stale_plan["cells"][0]["selectors"] = [
        "taskplane/tests/test_loop.py::test_different_selector"
    ]
    with pytest.raises(CIPolicyError, match="frozen shard plan"):
        build_ci_plan(candidate, stale_plan)


def test_ci_metrics_meet_declared_targets():
    metrics = evaluate_ci_metrics(_fixture("metrics.json"))

    assert metrics["passed"] is True
    assert metrics["values"] == {
        "first_validation_hours": 1.5,
        "p50_minutes": 6.0,
        "p95_minutes": 8.0,
        "runner_minutes": 28.0,
        "parallelism": 4.667,
    }
    assert all(check["passed"] for check in metrics["checks"])

    over_budget = _fixture("metrics.json")
    over_budget["cells"][0]["duration_minutes"] = 7.0
    over_budget["authoritative_elapsed_minutes"] = 7.0
    failed = evaluate_ci_metrics(over_budget)
    assert failed["passed"] is False
    runner_check = next(
        check for check in failed["checks"] if check["name"] == "runner_minutes"
    )
    assert runner_check == {
        "name": "runner_minutes", "value": 29.0, "target": 28.0,
        "direction": "max", "passed": False,
    }


def test_dashboard_browser_shard_is_disjoint_bounded_and_candidate_bound():
    candidate = _candidate()
    plan = build_ci_plan(candidate, _fixture("ci-plan.json"))
    browser = next(cell for cell in plan["cells"] if cell["kind"] == "browser")
    ordinary = [cell for cell in plan["cells"] if cell["kind"] != "browser"]

    assert browser["id"] == "dashboard-browser"
    assert browser["execution"] == "ci-only"
    assert browser["candidate_fingerprint"] == candidate["fingerprint"]
    assert browser["browser_fingerprint"] == candidate["browser_fingerprint"]
    assert browser["timeout_seconds"] <= 600
    assert set(browser["browser_environment"]) == {
        "executable",
        "version",
        "flags",
        "fixture_server",
        "snapshot",
        "dashboard_artifact",
        "selectors",
    }
    assert all(
        not set(browser["selectors"]).intersection(cell["selectors"])
        for cell in ordinary
    )

    duplicated = _fixture("ci-plan.json")
    duplicated["cells"][0]["selectors"].append(browser["selectors"][0])
    with pytest.raises(CIPolicyError, match="selector overlap"):
        build_ci_plan(candidate, duplicated)
