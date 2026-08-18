"""Canonical bounded recovery decisions for governed Taskplane work.

Recovery is deliberately a pure decision core.  Callers persist the returned
record alongside their own durable state, which keeps retries attributable and
allows every host adapter to apply the same policy without becoming an
authority source.
"""
from __future__ import annotations

from collections.abc import Sequence


ROUTINE_FAILURES = frozenset({
    "transient", "metadata", "evaluator", "collection", "artifact",
    "render", "setup", "network", "checkout",
})
MAX_ROUTINE_ATTEMPTS = 3


def _decision(*, status: str, reason: str, failure_class: str,
              attempt: int) -> dict:
    return {
        "schema": "taskplane.recovery-decision/v1",
        "status": status,
        "reason": reason,
        "attempt": attempt,
        "failure_class": failure_class,
    }


def _non_convergence_reason(fingerprints: Sequence[str],
                            progress: Sequence[float]) -> str | None:
    """Return a stable reason when another automatic retry is unjustified."""
    if len(fingerprints) >= 2 and fingerprints[-1] == fingerprints[-2]:
        return "repeated_fingerprint"
    if len(fingerprints) >= 3 and fingerprints[-1] == fingerprints[-3]:
        return "oscillation"
    if len(progress) >= 3:
        recent = [float(value) for value in progress[-3:]]
        if recent[0] == recent[1] == recent[2]:
            return "no_progress"
        if recent[0] > recent[1] > recent[2]:
            return "worsening"
        if recent[0] == recent[2] != recent[1]:
            return "oscillation"
    return None


def decide_recovery(*, failure_class: str, attempt: int,
                    fingerprints: Sequence[str] = (),
                    progress: Sequence[float] = (), safe: bool = True,
                    authority_changed: bool = False,
                    replan_required: bool = False,
                    max_routine_attempts: int = MAX_ROUTINE_ATTEMPTS) -> dict:
    """Classify one failed attempt without expanding existing authority.

    ``progress`` is a monotonic benefit signal: larger values mean measurable
    convergence.  It may extend a routine retry beyond the ordinary budget,
    but repetition, oscillation, worsening, safety, authority, and replanning
    always win over that extension.
    """
    kind = str(failure_class or "unknown").strip().lower()
    number = int(attempt)
    if number < 1:
        raise ValueError("recovery attempt must be positive")
    if not safe:
        return _decision(status="escalate", reason="unsafe_recovery",
                         failure_class=kind, attempt=number)
    if authority_changed:
        return _decision(status="escalate", reason="authority_change",
                         failure_class=kind, attempt=number)
    if replan_required:
        return _decision(status="escalate", reason="replan_required",
                         failure_class=kind, attempt=number)
    if kind not in ROUTINE_FAILURES:
        return _decision(status="escalate", reason="non_routine_failure",
                         failure_class=kind, attempt=number)

    stalled = _non_convergence_reason(fingerprints, progress)
    if stalled:
        return _decision(status="escalate", reason=stalled,
                         failure_class=kind, attempt=number)
    if number <= int(max_routine_attempts):
        return _decision(status="recover", reason="routine_retry",
                         failure_class=kind, attempt=number)
    if len(progress) >= 2 and float(progress[-1]) > float(progress[-2]):
        return _decision(status="recover", reason="measurable_convergence",
                         failure_class=kind, attempt=number)
    return _decision(status="escalate", reason="retry_budget_exhausted",
                     failure_class=kind, attempt=number)


SETUP_CLASSES = frozenset({
    "self-repairable", "authority-required", "host-policy",
    "external-unavailable",
})


def validate_setup_check(check: object) -> dict:
    """Validate the minimum, secret-free onboarding check contract."""
    if not isinstance(check, dict):
        raise ValueError("setup check must be an object")
    check_id = str(check.get("id") or "").strip()
    classification = str(check.get("classification") or "").strip()
    if not check_id or classification not in SETUP_CLASSES:
        raise ValueError("setup check id or classification is invalid")
    return {"id": check_id, "classification": classification,
            "detail": str(check.get("detail") or "")[:800]}
