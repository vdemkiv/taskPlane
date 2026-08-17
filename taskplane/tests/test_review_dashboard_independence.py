"""R-0008: dashboard growth is presentation-only.

These are metamorphic tests: the sealed review semantics stay fixed while the
dashboard representation grows from a fitting fragment to multi-megabyte input
that must be paginated.  Rendering is deliberately exercised only after the
canonical routing/lease/collection/gate tuple has been captured.
"""
from __future__ import annotations

import copy
import hashlib
import json

import dashboard


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _sealed_semantics():
    return {
        "routing": {"security": "deep", "qa": "light"},
        "leases": ["deep.security", "light-sweep"],
        "dispatch": ["deep.security", "light-sweep"],
        "collection": ["deep.security", "light-sweep"],
        "findings": [{
            "severity": "high", "title": "Missing authorization",
            "scenario": "an unauthenticated caller deletes an event",
            "fix": "require authorization", "file": "server/events.py",
            "line": 42,
        }],
        "gate": {"ready": False, "blocking": 1},
    }


def _meta(semantics, **extra):
    return {
        "title": "Engineering review",
        "routing_decision": semantics["routing"],
        "revision_identity": {
            "target_fingerprint": "target", "context_fingerprint": "context",
            "findings_fingerprint": _fingerprint(semantics["findings"]),
            "canonical_revision": 3,
        },
        "gate": True,
        "gate_title": "changes required",
        **extra,
    }


def test_multimegabyte_dashboard_and_pagination_cannot_mutate_review_semantics():
    sealed = _sealed_semantics()
    before = copy.deepcopy(sealed)
    semantic_fingerprint = _fingerprint(sealed)

    # The display-only material is intentionally much larger than the old
    # ReviewKernel 16 KiB scoped-view limit. It must never be fed back into
    # routing, leasing, dispatch, collection, findings, or gate computation.
    display_only = "dashboard detail " * 280_000
    findings = copy.deepcopy(sealed["findings"])
    findings[0]["scenario"] += display_only
    pages = dashboard.render_findings_paged(
        findings, _meta(sealed, subtitle=display_only), budget=16 * 1024)

    assert len(display_only.encode("utf-8")) > 4 * 1024 * 1024
    assert len(pages) > 1
    assert all(len(page["html"].encode("utf-8")) <= 16 * 1024
               for page in pages)
    assert sealed == before
    assert _fingerprint(sealed) == semantic_fingerprint


def test_page_budget_is_a_presentation_choice_not_a_semantic_input():
    sealed = _sealed_semantics()
    findings = [
        {**sealed["findings"][0], "title": f"Finding {index}",
         "scenario": "evidence " * 500}
        for index in range(120)
    ]
    before = _fingerprint(sealed)

    variants = [dashboard.render_findings_paged(
        copy.deepcopy(findings), _meta(sealed), budget=budget)
        for budget in (8 * 1024, 32 * 1024, 128 * 1024)]

    assert len({len(pages) for pages in variants}) > 1
    assert all(pages for pages in variants)
    assert _fingerprint(sealed) == before


def test_fitting_review_retains_exact_single_fragment_behavior():
    sealed = _sealed_semantics()
    findings = copy.deepcopy(sealed["findings"])
    meta = _meta(sealed)

    full = dashboard.render_findings(findings, meta)
    pages = dashboard.render_findings_paged(findings, meta, budget=1_000_000)

    assert pages == [{"title": "Engineering review", "html": full}]


def test_fitting_decision_uses_utf8_bytes_not_unicode_codepoints():
    sealed = _sealed_semantics()
    findings = copy.deepcopy(sealed["findings"])
    findings[0]["scenario"] = "é" * 10_000

    meta = _meta(sealed)
    full = dashboard.render_findings(findings, meta)
    # This budget fits the Python character count but not the serialized
    # UTF-8 representation. The public guarantee is expressed in bytes.
    budget = len(full) + 1
    assert len(full.encode("utf-8")) > budget
    pages = dashboard.render_findings_paged(findings, meta, budget=budget)

    assert all(len(page["html"].encode("utf-8")) <= budget
               for page in pages)
