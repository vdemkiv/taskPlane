"""Measured, bounded decisions for review fix cycles.

The policy is deliberately independent of the review verdict.  It compares
sealed review facts and tells the loop whether another already-authorized fix
cycle is justified or whether a named human boundary has been reached.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any


SCHEMA = "taskplane.review-fix-convergence/v1"


def _finding_ids(revision: dict[str, Any]) -> set[str]:
    findings = revision.get("findings")
    if not isinstance(findings, list):
        return set()
    result = set()
    for raw in findings:
        if not isinstance(raw, dict) or raw.get("admissible") is False:
            continue
        finding_id = str(raw.get("id") or raw.get("fingerprint") or "").strip()
        if finding_id:
            result.add(finding_id)
    return result


def _metric(revision: dict[str, Any], key: str) -> int:
    value = revision.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) \
        and value >= 0 else 0


def _fingerprint(finding_ids: Iterable[str], *, evidence: int,
                 tests: int) -> str:
    payload = {"findings": sorted(set(finding_ids)), "evidence": evidence,
               "tests": tests}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _history_fingerprints(history: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("current_fingerprint") or "") for row in history
            if isinstance(row, dict) and row.get("current_fingerprint")]


def _is_oscillating(history: list[dict[str, Any]], previous: str,
                    current: str) -> bool:
    """Detect an A->B->A->B loop without treating a simple retry as one."""
    transitions = [
        (str(row.get("previous_fingerprint") or ""),
         str(row.get("current_fingerprint") or ""))
        for row in history if isinstance(row, dict)
        and row.get("previous_fingerprint") and row.get("current_fingerprint")
    ]
    if len(transitions) < 2:
        return False
    return transitions[-2:] == [(previous, current), (current, previous)]


def evaluate_fix_cycle(
        previous_revision: dict[str, Any], current_revision: dict[str, Any],
        *, cycle: int, previously_closed: set[str] | None = None,
        history: list[dict[str, Any]] | None = None,
        max_cycles: int | None = None, human_stop: bool = False,
        unsafe_recovery: bool = False, scope_changed: bool = False,
        authority_changed: bool = False,
        previous_fingerprint: str | None = None,
        current_fingerprint: str | None = None) -> dict[str, Any]:
    """Record one fix comparison and return a deterministic loop decision.

    Safe measured progress has no implicit three-cycle ceiling.  Explicit task
    bounds and human/safety/authority boundaries always win.  Two consecutive
    cycles without any finding, test, or evidence progress escalate.
    """
    prior_ids = _finding_ids(previous_revision)
    current_ids = _finding_ids(current_revision)
    closed_history = set(previously_closed or set())
    closed = prior_ids - current_ids
    persistent = prior_ids & current_ids
    regressed = (current_ids - prior_ids) & closed_history
    new = (current_ids - prior_ids) - regressed

    prior_evidence = _metric(previous_revision, "acceptance_evidence_complete")
    current_evidence = _metric(current_revision, "acceptance_evidence_complete")
    prior_tests = _metric(previous_revision, "tests_passed")
    current_tests = _metric(current_revision, "tests_passed")
    finding_progress = len(closed) > len(new) + len(regressed)
    test_progress = current_tests > prior_tests
    evidence_progress = current_evidence > prior_evidence
    measurable = finding_progress or test_progress or evidence_progress
    progress = {"findings": finding_progress, "tests": test_progress,
                "evidence": evidence_progress, "measurable": measurable}

    prior_fp = previous_fingerprint or _fingerprint(
        prior_ids, evidence=prior_evidence, tests=prior_tests)
    current_fp = current_fingerprint or _fingerprint(
        current_ids, evidence=current_evidence, tests=current_tests)
    rows = history if isinstance(history, list) else []

    reason = "measurable_convergence" if measurable else "bounded_no_progress_retry"
    decision = "continue"
    boundaries = (
        (human_stop, "human_stop"),
        (unsafe_recovery, "unsafe_recovery"),
        (scope_changed, "scope_changed"),
        (authority_changed, "authority_changed"),
    )
    boundary_reason = next((name for active, name in boundaries if active), None)
    if boundary_reason:
        decision, reason = "escalate", boundary_reason
    elif (isinstance(max_cycles, int) and not isinstance(max_cycles, bool)
          and max_cycles > 0 and cycle >= max_cycles):
        decision, reason = "escalate", "task_cycle_bound"
    elif _is_oscillating(rows, prior_fp, current_fp):
        decision, reason = "escalate", "oscillation"
    elif current_fp in _history_fingerprints(rows):
        decision, reason = "escalate", "repeated_fingerprint"
    elif len(current_ids) > len(prior_ids) and not test_progress \
            and not evidence_progress:
        decision, reason = "escalate", "worsening"
    elif not measurable:
        prior_no_progress = bool(rows and isinstance(rows[-1], dict)
                                 and isinstance(rows[-1].get("progress"), dict)
                                 and rows[-1]["progress"].get("measurable") is False)
        if prior_no_progress:
            decision, reason = "escalate", "no_progress"

    return {
        "schema": SCHEMA,
        "cycle": max(0, int(cycle)),
        "findings": {"closed": sorted(closed),
                     "persistent": sorted(persistent),
                     "regressed": sorted(regressed), "new": sorted(new)},
        "progress": progress,
        "evidence": {"previous": prior_evidence, "current": current_evidence},
        "tests": {"previous": prior_tests, "current": current_tests},
        "previous_fingerprint": prior_fp,
        "current_fingerprint": current_fp,
        "decision": decision,
        "reason": reason,
    }
