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
    assert html.count('id="tp-review-gate"') == 1
    assert "Approve review" in html
    assert "Request changes" in html

