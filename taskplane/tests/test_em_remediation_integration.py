"""Exact-candidate FINAL-I proof for the complete R-0002 inventory.

FINAL-I is an integration join, not the independent FINAL-EVAL.  These tests
therefore prove that all 72 rows and their ancestry are ready for that later
evaluation while keeping the accepted high exceptions visibly non-green.
"""
from __future__ import annotations

import copy
import subprocess
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))

from taskplane import remediation_trace  # noqa: E402


EXPECTED_IDS = tuple(
    [f"H-{number:02d}" for number in range(1, 35)] +
    [f"M-{number:02d}" for number in range(1, 29)] +
    [f"L-{number:02d}" for number in range(1, 11)]
)
EXCEPTION_IDS = {
    "H-03", "H-04", "H-05", "H-06", "H-07", "H-08", "H-14",
    "H-15", "H-19", "H-22", "H-23", "H-25", "H-26", "H-30",
    "H-34",
}


def _head() -> str:
    return subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True,
    ).stdout.strip()


def _status() -> str:
    return subprocess.run(
        ["/usr/bin/git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    ).stdout.strip()


def test_ac1_exact_72_row_design_plan_inventory() -> None:
    candidate = _head()
    assert _status() == ""

    inventory = remediation_trace.build_final_inventory(
        str(ROOT), candidate_sha=candidate)
    assert remediation_trace.verify_final_inventory(str(ROOT), inventory) == \
        inventory
    assert inventory["candidate_sha"] == candidate
    assert inventory["counts"] == {
        "total": 72, "high": 34, "medium": 28, "low": 10,
    }
    assert tuple(row["id"] for row in inventory["rows"]) == EXPECTED_IDS
    assert len({row["review_row_fingerprint"]
                for row in inventory["rows"]}) == 72
    assert inventory["canonical_review"] == {
        "path": ".em-review/findings.json",
        "sha256": (
            "74745ab55c2d0313c9c4271697f2ee024a3e3966ea46f4323a18c9b26f5f6041"
        ),
        "retained_snapshot_path": (
            ".em-review/remediation/final-integration/findings-snapshot.json"
        ),
        "retained_snapshot_sha256": (
            "7f68603d889fc932a7f022c4df4b53e48317ce71fbc3608f4d27704d5a2f30ab"
        ),
    }
    lows = [row for row in inventory["rows"] if row["severity"] == "low"]
    assert len(lows) == 10
    assert all(row["low_companion"]["wave"] == row["wave"] for row in lows)
    assert all(row["low_companion"]["mode"] in {
        "shared-owner", "pairwise-disjoint"} for row in lows)
    assert all(row["owner"] and row["boundaries"] and row["task"] and
               row["dependency_class"] and row["evidence"]
               for row in inventory["rows"])

    for mutation in (
        lambda value: value["rows"].pop(),
        lambda value: value["rows"][0].update({"task": "M2-A"}),
        lambda value: value["rows"][-1]["low_companion"].update(
            {"mode": "low-only-tail"}),
        lambda value: value["counts"].update({"high": 33}),
    ):
        forged = copy.deepcopy(inventory)
        mutation(forged)
        with pytest.raises(remediation_trace.RemediationTraceError):
            remediation_trace.verify_final_inventory(str(ROOT), forged)
    assert _head() == candidate
    assert _status() == ""


def test_ac8_exact_candidate_final_evidence() -> None:
    candidate = _head()
    assert _status() == ""

    evidence = remediation_trace.build_final_integration_evidence(
        str(ROOT), candidate_sha=candidate)
    assert remediation_trace.verify_final_integration_evidence(
        str(ROOT), evidence) == evidence
    assert evidence["candidate_sha"] == candidate
    assert evidence["candidate_tree"] == subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD^{tree}"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True,
    ).stdout.strip()
    assert evidence["disposition"] == \
        "ready-for-independent-final-evaluation"
    assert evidence["strict_ac5_status"] == "not-satisfied"
    assert evidence["strict_ac8_status"] == \
        "pending-independent-final-evaluation"
    assert evidence["counts"] == {
        "total_trace_rows": 72,
        "high": 34,
        "medium": 28,
        "low": 10,
        "independently_green_high": 19,
        "attributed_non_independent_exceptions": 15,
        "focused_integrated_awaiting_final_evaluation": 38,
    }
    assert {row["id"] for row in evidence["exceptions"]} == {
        "H1-I-selector-receipt-authority", "H3-C-retention-gaps",
    }
    assert all(row["independently_green"] is False
               for row in evidence["exceptions"])
    dispositions = evidence["finding_dispositions"]
    assert tuple(row["finding_id"] for row in dispositions) == EXPECTED_IDS
    assert {row["finding_id"] for row in dispositions
            if row["status"] == "accepted-exception"} == EXCEPTION_IDS
    assert all(row["independent"] is False for row in dispositions
               if row["finding_id"] in EXCEPTION_IDS)
    assert len([row for row in dispositions
                if row["status"] == "independently-green"]) == 19
    assert len([row for row in dispositions if row["status"] ==
                "focused-integration-green-awaiting-final-evaluation"]) == 38
    assert {row["task_id"] for row in evidence["focused_integration"]} == {
        "M1-I", "M2-I",
    }
    assert evidence["final_evaluation"] == {
        "task_id": "FINAL-EVAL",
        "status": "not-run-by-FINAL-I",
        "independent_evaluator": "required",
        "exact_candidate": "required",
        "focused_selector": "taskplane/tests/test_em_remediation_integration.py",
        "full_suite": "python3 -m pytest taskplane/tests -q",
        "full_suite_status": "pending",
    }

    for mutation in (
        lambda value: value.update({"strict_ac5_status": "satisfied"}),
        lambda value: value["exceptions"][0].update(
            {"independently_green": True}),
        lambda value: value["task_commits"].update(
            {"M1-I": value["task_commits"]["M1-A"]}),
        lambda value: value["finding_dispositions"].pop(),
        lambda value: value["exact_candidate_inputs"][0].update(
            {"sha256": "0" * 64}),
        lambda value: value["final_evaluation"].update(
            {"full_suite_status": "passed"}),
    ):
        forged = copy.deepcopy(evidence)
        mutation(forged)
        with pytest.raises(remediation_trace.RemediationTraceError):
            remediation_trace.verify_final_integration_evidence(
                str(ROOT), forged)
    assert _head() == candidate
    assert _status() == ""
