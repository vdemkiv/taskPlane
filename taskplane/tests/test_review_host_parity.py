"""R-0009 host parity and bounded inline review presentation."""
from __future__ import annotations

import copy
import json

import pytest

from taskplane import dashboard, review_artifacts


def _model(*, findings: int = 3, canonical: bool = True) -> dict:
    rows = []
    for index in range(findings):
        rows.append({
            "id": f"F-{index:03d}",
            "lens": ("security", "frontend", "performance")[index % 3],
            "severity": ("high", "medium", "low")[index % 3],
            "title": f"Finding {index}",
            "file": f"src/module-{index % 7}.py",
            "line": index + 1,
            "rationale": "Why the observed behavior violates the requirement.",
            "scenario": "A concrete reproduction and impact path. " + "x" * 240,
            "action": "Apply the bounded corrective action and rerun its check.",
            "evidence": [{"kind": "source", "reference": f"ev-{index}"}],
            "provenance": [{"slot_id": f"slot-{index % 3}",
                            "result": f"result-{index}"}],
        })
    complete = bool(canonical)
    return {
        "schema": review_artifacts.ARTIFACT_MODEL_SCHEMA,
        "revision": {
            "id": "revision-2", "fingerprint": "a" * 64,
            "target_revision": "abc123", "disposition": (
                "canonical" if complete else "provisional"),
            "status": "complete" if complete else "incomplete",
            "supersedes": "revision-1",
        },
        "dor": {
            "status": "ready",
            "sources": [{
                "kind": "pr_body", "identity": "PR-1",
                "revision": "abc123", "status": "available",
                "provenance_ref": "source-1",
            }],
            "objectives": ["Ship the requested review change"],
            "clarifications": [],
        },
        "criteria": [
            {"id": "AC1", "text": "The review is lossless",
             "verdict": "pass", "rationale": "all formats round trip",
             "evidence": ["artifact-set"], "verification": "round-trip",
             "responsible": "artifact-publisher"},
            {"id": "AC2", "text": "Every lens is resolved",
             "verdict": "pass" if complete else "unproven",
             "rationale": "all slots returned" if complete else "slot pending",
             "evidence": ["slot-ledger"], "verification": "collection",
             "responsible": "review-kernel"},
        ],
        "slots": [
            {"slot_id": "slot-0", "lens_ids": ["security"],
             "status": "valid", "result_fingerprint": "b" * 64},
            {"slot_id": "slot-1", "lens_ids": ["frontend"],
             "status": "valid" if complete else "missing",
             "result_fingerprint": "c" * 64 if complete else None},
            {"slot_id": "slot-2", "lens_ids": ["performance"],
             "status": "valid", "result_fingerprint": "d" * 64},
        ],
        "findings": rows,
        "validation": {
            "status": "executed", "submitted_pr": "build_failed",
            "sandbox": "passed_after_repair", "evidence": ["command-1"],
        },
        "collection": {
            "status": "complete" if complete else "incomplete",
            "expected": 3, "collected": 3 if complete else 2,
            "gaps": [] if complete else ["slot-1"],
        },
        "provenance": {
            "target_fingerprint": "e" * 64,
            "context_fingerprint": "f" * 64,
            "run_id": "run-1", "host_transport": "fixture",
        },
        "gate": {
            "status": "awaiting-human" if complete else "blocked",
            "approval_enabled": complete,
            "reason": "human disposition" if complete else "incomplete review",
            "actions": ["approve", "request-changes"],
            "consent": {"mode": "dynamic-render",
                        "scope_fingerprint": "9" * 64},
        },
    }


def _semantic(pages: list[dict]) -> list[dict]:
    return [{key: value for key, value in page.items() if key != "transport"}
            for page in pages]


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_each_host_gets_complete_accessible_bounded_pages(host):
    pages = dashboard.render_review_model_paged(
        _model(findings=120), host=host)

    assert len(pages) > 3
    assert all(len(page["html"].encode("utf-8")) <= 14_000
               for page in pages)
    joined = "\n".join(page["html"] for page in pages)
    for finding in _model(findings=120)["findings"]:
        assert f'id="tp-{finding["id"]}"' in joined
        assert finding["rationale"] in joined
        assert finding["action"] in joined
    for landmark in (
            "Definition of Ready", "Acceptance criteria", "Lens status",
            "Dynamic validation", "Provenance", "Provisional gaps",
            "Gate reason"):
        assert landmark in joined
    assert 'role="navigation"' in joined
    assert 'aria-label="Filter findings"' in joined
    assert 'function tpReviewKey' in joined
    assert "sessionStorage" in joined
    assert 'data-model-fingerprint=' in joined
    assert 'data-receipt=' in joined


def test_claude_and_codex_pages_are_semantically_identical():
    claude_model = _model(findings=120)
    claude_model["provenance"]["host_transport"] = "claude-workflow"
    codex_model = copy.deepcopy(claude_model)
    codex_model["provenance"]["host_transport"] = "codex-task"
    claude = dashboard.render_review_model_paged(claude_model, host="claude")
    codex = dashboard.render_review_model_paged(codex_model, host="codex")

    assert _semantic(claude) == _semantic(codex)
    assert {page["transport"]["host"] for page in claude} == {"claude"}
    assert {page["transport"]["host"] for page in codex} == {"codex"}


def test_provisional_revision_preserves_findings_but_cannot_be_approved():
    pages = dashboard.render_review_model_paged(
        _model(findings=12, canonical=False), host="codex")
    joined = "\n".join(page["html"] for page in pages)

    assert "F-011" in joined
    assert "slot-1" in joined
    assert "incomplete review" in joined
    assert 'data-action="approve" disabled' in joined
    assert "human declined" not in joined.lower()
    assert "user declined" not in joined.lower()


def test_absent_consent_is_pending_and_never_invented_as_declined():
    model = _model()
    model["gate"]["consent"] = None

    joined = "\n".join(page["html"] for page in
                       dashboard.render_review_model_paged(model))

    assert "consent pending" in joined.lower()
    assert "declined" not in joined.lower()
    assert 'data-action="approve" disabled' in joined


def test_approval_requires_canonical_complete_gap_free_justified_revision():
    cases = []
    provisional = _model(canonical=False)
    cases.append(provisional)
    with_gap = _model()
    with_gap["collection"]["gaps"] = ["slot-x"]
    cases.append(with_gap)
    unproven = _model()
    unproven["criteria"][0]["verdict"] = "unproven"
    cases.append(unproven)
    invalid_slot = _model()
    invalid_slot["slots"][0]["status"] = "invalid"
    cases.append(invalid_slot)

    for model in cases:
        joined = "\n".join(page["html"] for page in
                           dashboard.render_review_model_paged(model))
        assert 'data-action="approve" disabled' in joined

    ready = "\n".join(page["html"] for page in
                      dashboard.render_review_model_paged(_model()))
    assert 'data-action="approve" disabled' not in ready
    assert 'data-action="approve"' in ready


def test_large_source_evidence_never_becomes_inline_or_producer_input():
    model = _model(findings=120)
    model["dor"]["sources"][0]["content"] = "large-evidence-" * 200_000
    original = copy.deepcopy(model)

    pages = dashboard.render_review_model_paged(model, host="codex")

    assert all(len(page["html"].encode("utf-8")) <= 14_000
               for page in pages)
    assert "large-evidence-large-evidence" not in "".join(
        page["html"] for page in pages)
    assert model == original
    artifact = review_artifacts.sanitize_model(model)["model"]
    assert artifact["dor"]["sources"][0]["content"].startswith(
        "large-evidence-large-evidence")
    assert all(page["model_fingerprint"] == pages[0]["model_fingerprint"]
               for page in pages)


def test_invalid_host_and_too_small_budget_fail_actionably():
    with pytest.raises(ValueError, match="supported review host"):
        dashboard.render_review_model_paged(_model(), host="other")
    with pytest.raises(ValueError, match="at least"):
        dashboard.render_review_model_paged(_model(), budget=1000)


def test_small_existing_finding_dashboard_remains_compatible():
    findings = [{"severity": "high", "title": "Existing finding",
                 "domain": "security", "scenario": "scenario",
                 "fix": "fix"}]
    before = dashboard.render_findings(findings, {"title": "review"})
    dashboard.render_review_model_paged(_model(findings=1))
    after = dashboard.render_findings(findings, {"title": "review"})

    assert after == before
