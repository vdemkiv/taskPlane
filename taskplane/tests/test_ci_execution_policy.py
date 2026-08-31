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
    reuse_terminal_matrix,
    seal_terminal_matrix,
)


FIXTURES = Path(__file__).parent / "fixtures" / "ci-policy"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _candidate():
    return freeze_candidate(_fixture("candidate.json"))


def test_validation_progression_and_terminal_matrix_reuse():
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

    plan = build_ci_plan(candidate, _fixture("ci-plan.json"))
    terminal = seal_terminal_matrix(candidate, plan, _fixture("green-cells.json"))
    exact_reuse = reuse_terminal_matrix(terminal, candidate)
    assert exact_reuse["terminal_reusable"] is True
    assert exact_reuse["matrix_runs"] == 0
    assert exact_reuse["rerun_cells"] == []
    assert set(exact_reuse["cited_unchanged_green"]) == {
        cell["id"] for cell in terminal["cells"]
    }

    changed = _fixture("candidate.json")
    changed["fingerprints"]["tests"] = "f" * 64
    invalidated = reuse_terminal_matrix(terminal, freeze_candidate(changed))
    assert invalidated["terminal_reusable"] is False
    assert set(invalidated["rerun_cells"]) == {
        cell["id"] for cell in terminal["cells"]
    }
    assert invalidated["cited_unchanged_green"] == []


def test_ci_shards_cleanup_and_candidate_freeze_are_authoritative():
    candidate = _candidate()
    plan = build_ci_plan(candidate, _fixture("ci-plan.json"))

    assert plan["candidate_frozen_before_cells"] is True
    assert plan["candidate_fingerprint"] == candidate["fingerprint"]
    assert plan["source_sha"] == candidate["source_sha"]
    assert plan["max_parallel"] >= 4
    assert len(plan["matrices"]) <= 3
    assert plan["cancellation"] == {
        "group": "pull-request-482",
        "cancel_in_progress": True,
        "scope": "same-pr-heads-only",
    }
    assert plan["terminal_aggregate"]["needs"] == [
        cell["id"] for cell in plan["cells"]
    ]
    assert plan["serializations"] == [
        {"name": "package-index", "cells": ["package"]}
    ]

    occupied_selectors = set()
    occupied_paths = set()
    for cell in plan["cells"]:
        assert cell["candidate_fingerprint"] == candidate["fingerprint"]
        assert cell["timeout_seconds"] <= 600
        assert not occupied_selectors.intersection(cell["selectors"])
        assert not occupied_paths.intersection(cell["paths"])
        occupied_selectors.update(cell["selectors"])
        occupied_paths.update(cell["paths"])
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
        "first_matrix_hours": 1.5,
        "matrix_count": 2,
        "p50_minutes": 6.0,
        "p95_minutes": 8.0,
        "runner_minutes": 29.0,
        "parallelism": 4.833,
    }
    assert all(check["passed"] for check in metrics["checks"])

    over_budget = _fixture("metrics.json")
    over_budget["cells"][0]["duration_minutes"] = 9.0
    over_budget["authoritative_elapsed_minutes"] = 9.0
    failed = evaluate_ci_metrics(over_budget)
    assert failed["passed"] is False
    assert next(
        check for check in failed["checks"] if check["name"] == "runner_minutes"
    )["passed"] is False


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
        and not set(browser["paths"]).intersection(cell["paths"])
        for cell in ordinary
    )

    terminal = seal_terminal_matrix(candidate, plan, _fixture("green-cells.json"))
    browser_drift = copy.deepcopy(_fixture("candidate.json"))
    browser_drift["browser"]["version"] = "Chromium 131.0.1"
    reuse = reuse_terminal_matrix(terminal, freeze_candidate(browser_drift))
    assert reuse["terminal_reusable"] is False
    assert reuse["rerun_cells"] == ["dashboard-browser"]
    assert set(reuse["cited_unchanged_green"]) == {
        cell["id"] for cell in ordinary
    }

    duplicated = _fixture("ci-plan.json")
    duplicated["cells"][0]["selectors"].append(browser["selectors"][0])
    with pytest.raises(CIPolicyError, match="selector overlap"):
        build_ci_plan(candidate, duplicated)
