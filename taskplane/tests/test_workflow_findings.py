"""Replacement finding identity regressions; no host or approval simulation needed."""
from copy import deepcopy

import pytest

from taskplane import workflow as w, workflow_evidence as e


def review(run, findings, inherited=None):
    state = w.new_state("/fixture", "root", run, {"criteria": ["AC1"], "paths": {phase: [] for phase in w.PHASES}})
    state["inherited_findings"] = deepcopy(inherited or {})
    stage = state["visits"][state["index"]]
    stage["packet"] = {"visit": stage["id"], "output": {"findings": deepcopy(findings)},
                       "finding_evidence": {"findings.md": {"sha256": run}}}
    return state


def declarations():
    return [{"id": f"ENG-F{i}", "title": f"Finding {i}", "evidence": "findings.md",
             "owner": "reviewer", "required_evidence": ["runtime check"], "criteria": ["AC1"]}
            for i in range(1, 12)]


def test_replacement_redeclarations_count_once_and_retain_original_evidence():
    old = review("original", declarations())
    inherited = e.carry_findings(old)
    state = review("replacement", declarations(), inherited)
    before = deepcopy(state)
    summary = e.outcome_summary(state)
    assert len(summary["findings"]) == len(summary["unresolved_findings"]) == 11
    for row in summary["findings"]:
        assert row["origin_run"] == row["origin"]["run"] == "original"
        assert row["required_evidence"] == ["runtime check"]
        assert row["evidence_refs"] == [{"sha256": "original"}, {"sha256": "replacement"}]
        assert row["disposition"] == "open" and row["verification"] == "unverified"
    # Polling and repeated replacement do not grow the register or mutate state.
    assert e.outcome_summary(state) == summary and state == before
    again = review("third", declarations(), e.carry_findings(state))
    assert len(e.finding_register(again)) == 11
    assert {row["origin_run"] for row in e.finding_register(again)} == {"original"}


@pytest.mark.parametrize("origin,count", [("original", 1), ("replacement", 2), ("independent", 2)])
def test_explicit_origin_wins_even_for_reused_id(origin, count):
    inherited = e.carry_findings(review("original", declarations()[:1]))
    state = review("replacement", [{**declarations()[0], "origin_run": origin}], inherited)
    rows = e.finding_register(state)
    assert len(rows) == count
    assert {row["origin_run"] for row in rows} == {"original", origin}


def test_ambiguous_inherited_id_is_not_merged_and_new_ids_stay_new():
    inherited = e.carry_findings(review("original", declarations()[:1]))
    other = deepcopy(inherited["findings"][0])
    other["origin_run"] = "independent"
    inherited["findings"].append(other)
    state = review("replacement", declarations()[:2], inherited)
    rows = e.finding_register(state)
    assert len(rows) == 4
    assert {row["origin_run"] for row in rows if row["id"] == "ENG-F1"} == {
        "original", "independent", "replacement"}
    assert [row["origin_run"] for row in rows if row["id"] == "ENG-F2"] == ["replacement"]


def test_legacy_inheritance_and_redeclaration_cannot_remove_obligations_or_resolve():
    inherited = e.carry_findings(review("original", declarations()[:1]))["findings"]
    inherited[0].update(disposition="resolved", verification="runtime_verified")
    current = {"id": "ENG-F1", "owner": "different", "required_evidence": [],
               "disposition": "resolved", "verification": "runtime_verified"}
    state = review("replacement", [current], inherited)
    w.current(state)["packet"]["output"]["finding_updates"] = [
        {"id": "ENG-F1", "origin_run": "original", "disposition": "resolved"}]
    rows = e.finding_register(state)
    assert len(rows) == 1
    assert rows[0]["owner"] == "reviewer" and rows[0]["required_evidence"] == ["runtime check"]
    assert rows[0]["disposition"] == "open" and rows[0]["verification"] == "unverified"
    assert rows[0]["update_history"] == []
