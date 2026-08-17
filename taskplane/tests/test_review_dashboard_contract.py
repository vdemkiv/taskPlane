import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import dashboard  # noqa: E402


def _meta():
    return {
        "title": "Engineering review",
        "gate": True,
        "gate_title": "review complete — approve or request changes",
        "gate_buttons": [
            {"label": "Approve review", "prompt": "Approve review run abc",
             "primary": True},
            {"label": "Request changes", "prompt": "Request changes for review run abc"},
        ],
        "revision_identity": {
            "target_fingerprint": "target", "context_fingerprint": "context",
            "findings_fingerprint": "findings", "canonical_revision": 2,
        },
        "diagnostic_fingerprints": {
            "engine": "taskplane/2.17.1", "routing_policy": "policy",
            "graph": "graph", "routing_decision": "routing",
        },
        "review_execution": {
            "static_only": True,
            "dynamic_validation": {"status": "declined", "detail": "human chose static"},
            "functionality_render": {"status": "declined", "detail": "human chose static"},
        },
        "dor_evidence": {
            "status": "degraded", "specification_source": "pr_commits",
            "checks": [
                {"check": "specification artifact", "status": "found",
                 "detail": "2 PR commits"},
                {"check": "acceptance criteria", "status": "missing",
                 "detail": "none supplied"},
            ],
            "requirements": ["Add analytics"],
            "acceptance": ["AnalyticsSummary is added"],
            "review_directives": [{"text": "Security vulnerabilities",
                                   "source": "README.md"}],
            "requested_lenses": {"security": ["Security vulnerabilities"]},
            "commits": [{"sha": "1234567890", "subject": "Add analytics"}],
        },
        "requirements_validation": {
            "status": "blocked",
            "counts": {"met": 0, "partial": 1, "not_met": 0,
                       "cannot_verify": 0},
            "criteria": [{
                "id": "AC-1", "criterion": "Add analytics",
                "status": "partial", "gate": "block",
                "validation_mode": "static",
                "evidence": ["changed files: src/Analytics.tsx"],
                "related_findings": [{"severity": "high",
                                      "title": "Analytics crashes",
                                      "file": "src/Analytics.tsx"}],
            }],
        },
        "review_notes": [{
            "lens": "testability", "title": "resize path not exercised",
            "file": "src/Header.test.tsx", "line": 192,
            "scenario": "the observer callback has no assertion",
        }],
        "graph_fragment": '<div id="tp-review-graph">graph</div>',
        "clean_evidence": [{
            "lens": "architecture", "file": "src/Header.tsx", "line": 94,
            "claim": "checked ownership and invalidation boundaries",
        }],
    }


def test_canonical_dashboard_contains_graph_notes_evidence_fingerprints_and_gate():
    html = dashboard.render_findings([], _meta())
    assert html.count('id="tp-review-graph"') == 1
    assert "resize path not exercised" in html
    assert "src/Header.test.tsx:192" in html
    assert "checked ownership and invalidation boundaries" in html
    assert "taskplane/2.17.1" in html
    assert "STATIC-ONLY" in html
    assert 'id="tp-review-dor"' in html
    assert "DEGRADED · source: pr commits" in html
    assert "acceptance criteria" in html
    assert "Add analytics" in html
    assert "AnalyticsSummary is added" in html
    assert "Security vulnerabilities" in html
    assert "→ security" in html
    assert 'id="tp-requirements-validation"' in html
    assert "requirements validation — implementation result" in html
    assert "Analytics crashes" in html
    assert html.count('id="tp-review-gate"') == 1
    assert "Approve review" in html
    assert "Request changes" in html
    assert "onclick=" not in html
    assert 'data-tp-prompt="Approve review run abc"' in html
    assert 'data-tpf-toggle=' not in html  # no finding cards in this fixture
    assert 'addEventListener("click"' in html


def test_review_controls_use_csp_safe_delegated_actions():
    html = dashboard.render_findings([{
        "severity": "high", "title": "broken", "scenario": "fails",
        "fix": "repair", "file": "src/app.py", "line": 3,
    }], _meta())

    assert "onclick=" not in html
    assert 'data-sev="high"' in html
    assert 'data-tpf-toggle="0"' in html
    assert 'data-tp-prompt="Approve review run abc"' in html


def test_legacy_dynamic_run_does_not_claim_render_was_human_declined():
    meta = _meta()
    meta["review_execution"] = {
        "selection": "dynamic", "static_only": False,
        "dynamic_validation": {"status": "executed", "detail": "build passed"},
        "functionality_render": {
            "status": "declined",
            "detail": "human did not select inline rendering",
        },
    }

    html = dashboard.render_findings([], meta)

    assert "human did not select inline rendering" not in html
    assert "not_selected" in html
    assert "not included in the selected dynamic review mode" in html
    assert 'document.getElementById("tp-inline-review-root")' in html


def test_inline_review_style_defines_every_svg_palette_variable():
    css = dashboard.inline_review_style()
    for name in ("--line", "--changed-bg", "--accent", "--text-primary",
                 "--text-secondary", "--surface-0", "--surface-2"):
        assert name in css
    assert "#tp-inline-review-root" in css
    assert "light-dark(" in css
