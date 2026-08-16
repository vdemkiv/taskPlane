import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import dashboard  # noqa: E402


def test_review_gate_is_standalone_and_cannot_be_answered_by_preflight_state():
    html = dashboard.render_findings([], {
        "gate": True,
        "gate_title": "review complete — approve or request changes",
        "gate_buttons": [
            {"label": "Approve review", "prompt": "Approve review run 123",
             "primary": True},
            {"label": "Request changes", "prompt": "Request changes for review run 123"},
        ],
        "review_execution": {
            "decided_by": "remote-source-approval",
            "dynamic_validation": {"status": "executed"},
            "functionality_render": {"status": "executed"},
        },
    })
    assert html.count('id="tp-review-gate"') == 1
    assert "Approve review run 123" in html
    assert "remote-source-approval" not in html
    assert "Approve this flow" not in html

