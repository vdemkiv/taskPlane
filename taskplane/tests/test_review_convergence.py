"""Review convergence: structural admissibility and adjudication memory."""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import defect_claim  # noqa: E402
import review  # noqa: E402
import review_evidence  # noqa: E402
import yield_meter  # noqa: E402


CLAIM = {
    "trigger": "submit a review result with a stale lease identity",
    "outcome": "the canonical revision accepts evidence from another run",
    "repro": "start two review runs and submit the first result to the second",
}


def finding(**overrides):
    row = {
        "lens": "code-quality", "kind": "defect", "severity": "high",
        "class": "regression", "file": "src/service.py", "line": 10,
        "title": "stale lease crosses review runs",
        "scenario": "two concurrent review runs finish out of order",
        "fix": "bind the result to its exact run and lease", "claim": CLAIM,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("category", sorted(defect_claim.NOTE_CATEGORIES))
def test_named_inadmissible_categories_route_to_notes(category):
    row = finding(kind="note", claim=None, note_category=category,
                  severity="blocker")
    assert defect_claim.admissibility(row, resolver=lambda _value: False) == {
        "kind": "note", "admissible": False, "reason": category}


def test_complete_claim_is_a_defect_even_when_producer_calls_it_a_note():
    result = defect_claim.admissibility(finding(kind="note"))
    assert result["kind"] == "defect"
    assert result["admissible"] is True


def test_violation_must_name_a_resolvable_declaration():
    row = finding(kind="violation", claim=None,
                  declares="reference:lenses/references/go-engineering.md#Security")
    assert defect_claim.admissibility(row, resolver=lambda _value: False)[
        "kind"] == "note"
    assert defect_claim.admissibility(row, resolver=lambda _value: True) == {
        "kind": "violation", "admissible": True,
        "reason": "declared-standard"}


def test_reference_declaration_resolves_only_an_actual_heading():
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    assert defect_claim.declaration_resolves(
        root, "reference:lenses/references/go-engineering.md#Security")
    assert not defect_claim.declaration_resolves(
        root, "reference:lenses/references/go-engineering.md#Imaginary")


def test_not_a_defect_is_durable_and_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "store"))
    ws = str(tmp_path / "repo")
    os.makedirs(ws)
    row = finding()
    yield_meter.record_findings(ws, [row], caught_at="em", review_id="one")
    fp = yield_meter.fingerprint(row)
    assert yield_meter.record_disposition(
        ws, fp, "not-a-defect", by="human")["verdict"] == "not-a-defect"
    assert yield_meter.settled_findings(
        ws, files=["src/service.py"])[0]["disposition"] == "not-a-defect"
    assert yield_meter.settled_findings(ws, files=["src/other.py"]) == []


def test_settled_recurrence_is_refused_until_new_evidence_is_named(tmp_path):
    ws = str(tmp_path)
    store = review_evidence.ArtifactStore(ws)
    row = finding()
    settled = store.put("settled-findings", {
        "schema": "taskplane.settled-findings/v1", "scope_files": [row["file"]],
        "count": 1, "rows": [{"fp": yield_meter.fingerprint(row),
                                "disposition": "not-a-defect"}]})
    brief = {"settled_findings": settled}
    with pytest.raises(review_evidence.ProvenanceError,
                       match="recurrence requires named new evidence"):
        review._adjudicate_findings(ws, store, brief, [row])
    admitted, notes = review._adjudicate_findings(
        ws, store, brief,
        [{**row, "recurrence": "the resolving change was reverted in this diff"}])
    assert len(admitted) == 1
    assert notes == []


def test_notes_are_counted_but_not_returned_as_findings(monkeypatch, tmp_path):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "store"))
    ws = str(tmp_path / "repo")
    os.makedirs(ws)
    row = finding(kind="note", claim=None, note_category="review-meta")
    admitted, notes = review._adjudicate_findings(
        ws, review_evidence.ArtifactStore(ws), {}, [row])
    assert admitted == []
    assert yield_meter.record_notes(ws, notes, caught_at="review") == 1
    report = yield_meter.report(ws)
    assert report["findings"] == 0
    assert report["notes"] == 1


def test_review_brief_carries_bounded_settled_set_by_reference(monkeypatch,
                                                               tmp_path):
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "store"))
    ws = str(tmp_path / "repo")
    os.makedirs(os.path.join(ws, "src"))
    row = finding()
    yield_meter.record_findings(ws, [row], caught_at="em", review_id="first")
    yield_meter.record_disposition(
        ws, yield_meter.fingerprint(row), "not-a-defect", by="human")
    opened = review.start_review(
        ws,
        target={"fingerprint": "target-one", "head": "abc123"},
        graph={"meta": {"scanned_head": "abc123",
                         "content_fingerprint": "graph-one"},
               "modules": {"src": {"files": ["src/service.py"]}},
               "edges": []},
        impact={"touched": ["src"], "impacted": {},
                "total_impacted": 1, "unknown": []},
        diff={"files": ["src/service.py"], "changed_symbols": ["changed"]},
        runnability={"summary": "available"},
        requirement={"id": "R-0001", "text": "safe change"},
        acceptance=["works"], contracts=["contract:api"],
    )
    assert opened["status"] == "ready"
    state = review._load_state(ws, opened["run_id"])
    store = review_evidence.ArtifactStore(ws)
    for slot in state["slots"]:
        brief = store.read(slot["brief"])
        assert "rows" not in brief
        settled = store.read(brief["settled_findings"])
        assert settled["count"] == 1
        assert settled["rows"][0]["fp"] == yield_meter.fingerprint(row)
        assert brief["settled_findings"]["bytes"] < 16 * 1024


def test_frozen_two_pass_scenario_converges_after_settlement(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    with open(os.path.join(root, "evals", "frozen-pr-9464",
                           "review-convergence.json"), encoding="utf-8") as stream:
        scenario = json.load(stream)
    row = scenario["finding"]
    store = review_evidence.ArtifactStore(str(tmp_path))
    first, notes = review._adjudicate_findings(str(tmp_path), store, {}, [row])
    assert len(first) == scenario["expect"]["first_pass_admissible"]
    assert notes == []

    settled = store.put("settled-findings", {
        "schema": "taskplane.settled-findings/v1",
        "scope_files": [row["file"]], "count": 1,
        "rows": [{"fp": yield_meter.fingerprint(row),
                  "disposition": "resolved"}],
    })
    refused = 0
    try:
        second, _notes = review._adjudicate_findings(
            str(tmp_path), store, {"settled_findings": settled}, [row])
    except review_evidence.ProvenanceError:
        refused, second = 1, []
    assert len(second) == scenario["expect"]["second_pass_new_admissible"]
    assert refused == scenario["expect"]["second_pass_refused_recurrences"]
