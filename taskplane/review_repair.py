"""Deterministic, equivalence-bounded review result normalization.

The review kernel owns redundant verdict/count summaries because they are
mechanically derivable from the admitted findings.  This module never edits a
finding or checked evidence.  If that substance is malformed, only the leased
slot is scheduled for another producer run and already valid siblings remain
reusable.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

import review_recovery


def _blocking_counts(findings: Iterable[Mapping]) -> dict[str, int]:
    # Import lazily: review.py uses this module at its collector boundary.
    import review

    return review.blocking_findings_by_lens(findings)


def normalize_slot_result(result: Mapping, lease: Mapping, *,
                          canonical_findings: Iterable[Mapping],
                          actor: str = "review-kernel") -> dict:
    """Normalize only verdict/count metadata from canonical findings."""
    recovered = review_recovery.recover_summary_or_plan_retry(
        result, lease, blocking_by_lens=_blocking_counts(canonical_findings),
        attempts={}, actor=actor)
    if recovered.get("status") == "retry":
        return recovered
    audit = dict(recovered.get("audit") or {})
    audit.update({
        "derivation_authority": "canonical-admissible-findings/v1",
        "equivalence": "proven",
    })
    return dict(recovered, audit=audit)


def normalize_or_plan_retry(
        result: Mapping, lease: Mapping, *,
        canonical_findings: Iterable[Mapping], leases: Iterable[Mapping],
        valid_results: Mapping, attempts: Mapping,
        actor: str = "review-kernel") -> dict:
    """Normalize metadata or return a retry plan for this exact slot only."""
    recovered = review_recovery.recover_summary_or_plan_retry(
        result, lease, blocking_by_lens=_blocking_counts(canonical_findings),
        attempts=attempts, valid_results=valid_results, leases=leases,
        actor=actor)
    if recovered.get("status") != "retry":
        audit = dict(recovered.get("audit") or {})
        audit.update({
            "derivation_authority": "canonical-admissible-findings/v1",
            "equivalence": "proven",
        })
        return dict(recovered, audit=audit)
    return recovered

