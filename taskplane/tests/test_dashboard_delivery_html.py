"""Static dashboard delivery is one closed, freshness-aware document."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from taskplane import views


class _DocumentCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.doctypes = 0
        self.tags = {"html": 0, "head": 0, "body": 0}

    def handle_decl(self, decl):
        if decl.casefold().strip() == "doctype html":
            self.doctypes += 1

    def handle_starttag(self, tag, attrs):
        if tag in self.tags:
            self.tags[tag] += 1


def test_delivery_contains_exactly_one_doctype_html_head_and_body(tmp_path):
    model = {
        "schema": "taskplane.dashboard-delivery-model/v1",
        "identity": {"workflow_id": "wf", "run_id": "run",
                     "target": "repo", "revision": "abc", "sequence": 4},
        "gate": {"status": "awaiting-human", "approval_enabled": True},
        "design_graph": {"nodes": ["views"], "edges": []},
    }
    result = views.deliver_dashboard(
        str(tmp_path), model,
        html_renderer=lambda _canonical: (
            '<main aria-label="Taskplane dashboard">current</main>'
            '<button data-dashboard-action="approve">approve</button>'))

    path = Path(result["artifacts"]["html"]["path"])
    document = path.read_text(encoding="utf-8")
    parser = _DocumentCounter()
    parser.feed(document)

    assert parser.doctypes == 1
    assert parser.tags == {"html": 1, "head": 1, "body": 1}
    assert views.decode_dashboard_artifact(
        "html", document.encode("utf-8")) == model
    dom_freshness = result["publication_receipt"]["dom_freshness"]
    assert {key: dom_freshness[key] for key in (
        "status", "html_document_count", "canonical_sha256",
        "actions_enabled",
    )} == {
        "status": "verified",
        "html_document_count": 1,
        "canonical_sha256": result["semantic_sha256"],
        "actions_enabled": False,
    }
    assert len(dom_freshness["fingerprint"]) == 64
    assert 'data-dashboard-freshness="unverified"' in document
    assert "taskplaneDashboardApplyHead" in document
