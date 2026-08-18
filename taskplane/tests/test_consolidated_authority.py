from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import authority  # noqa: E402
import loop  # noqa: E402
import review_dor  # noqa: E402


BASE = {
    "requirement": "R-0001",
    "acceptance": ["one approval", "final sign-off"],
    "target": {"repository": "repo-1", "revision": "abc123"},
    "scope": ["taskplane/loop.py", "taskplane/authority.py"],
    "contracts": {"contract:authorization": "one bounded packet"},
    "design": {"decision": "ledger", "format_fingerprint": "old"},
    "plan": {"tasks": ["t1"], "format_fingerprint": "old"},
    "dynamic_validation": "declared checks",
    "sandbox": "ordinary scoped writes",
    "recovery": {"attempts": 3, "gate_weakening": False},
    "evaluation": "routed lenses and collection",
    "artifact_delivery": ["json", "markdown"],
    "execution_bounds": {"parallel": True, "external_effects": False},
}


def approved_packet():
    packet = authority.create_packet(BASE)
    receipt = authority.approve(
        packet, actor="user-7", thread="thread-9", authenticated=True)
    return packet, receipt


def test_one_receipt_derives_unchanged_routine_stage_authority():
    packet, receipt = approved_packet()

    result = authority.derive(
        packet, receipt, stage="evaluate", current=BASE,
        actor="user-7", thread="thread-9")

    assert result["authorized"] is True
    assert result["receipt_fingerprint"] == receipt["fingerprint"]
    assert result["evolution"] == "unchanged"


@pytest.mark.parametrize("field,value,reason", [
    ("acceptance", ["changed criterion"], "acceptance_changed"),
    ("scope", ["taskplane/**"], "scope_changed"),
    ("contracts", {"contract:authorization": "weaker"},
     "contract_meaning_changed"),
    ("execution_bounds", {"parallel": True, "external_effects": True},
     "authority_changed"),
])
def test_material_changes_require_new_human_authority(field, value, reason):
    packet, receipt = approved_packet()
    current = {**BASE, field: value}

    result = authority.derive(
        packet, receipt, stage="execute", current=current,
        actor="user-7", thread="thread-9")

    assert result["authorized"] is False
    assert result["evolution"] == "material-contract"
    assert reason in result["reasons"]


def test_format_only_and_non_material_how_changes_do_not_prompt():
    packet, receipt = approved_packet()
    format_only = {
        **BASE,
        "design": {"decision": "ledger", "format_fingerprint": "new"},
        "plan": {"tasks": ["t1"], "format_fingerprint": "new"},
    }
    non_material = {
        **BASE,
        "design": {"decision": "ledger with renamed helper"},
    }

    byte_result = authority.derive(
        packet, receipt, stage="plan", current=format_only,
        actor="user-7", thread="thread-9")
    how_result = authority.derive(
        packet, receipt, stage="execute", current=non_material,
        actor="user-7", thread="thread-9")

    assert byte_result["authorized"] is True
    assert byte_result["evolution"] == "byte-only"
    assert how_result["authorized"] is True
    assert how_result["evolution"] == "non-material"


@pytest.mark.parametrize("actor,thread,authenticated,reason", [
    ("user-7", "wrong", True, "wrong_thread"),
    ("wrong", "thread-9", True, "wrong_actor"),
    ("user-7", "thread-9", False, "unauthenticated"),
])
def test_silence_or_wrong_identity_never_expands_authority(
        actor, thread, authenticated, reason):
    packet = authority.create_packet(BASE)
    receipt = authority.approve(
        packet, actor="user-7", thread="thread-9", authenticated=True)
    candidate = dict(receipt, authenticated=authenticated)

    result = authority.derive(
        packet, candidate, stage="execute", current=BASE,
        actor=actor, thread=thread)

    assert result["authorized"] is False
    assert reason in result["reasons"]


def test_human_boundary_is_closed_and_names_requested_authority():
    decision = authority.human_boundary(
        "external_publication", fact="new production URL",
        consequence="publishes outside the sandbox")

    assert decision == {
        "human_required": True,
        "reason": "external_publication",
        "new_fact": "new production URL",
        "consequence": "publishes outside the sandbox",
        "authority_requested": "external_publication",
    }
    assert authority.human_boundary("routine_collection")["human_required"] is False


def test_north_star_is_conditional_structured_advice_and_never_a_gate():
    trigger = review_dor.north_star_advice(
        ["This irreversible direction has high opportunity cost"],
        advice={
            "alignment": "aligned", "leverage": "medium",
            "reversibility": "low", "opportunity_cost": "high",
            "coherence": "consistent", "sharpest_tension": "speed vs lock-in",
            "recommendation": "prototype first",
        })
    skipped = review_dor.north_star_advice(["Rename a local helper"])

    assert trigger["invoked"] is True
    assert trigger["blocking"] is False
    assert set(trigger["advice"]) == set(review_dor.NORTH_STAR_FIELDS)
    assert skipped == {"invoked": False, "blocking": False, "reasons": []}


def test_incomplete_north_star_note_is_rejected_without_gating_delivery():
    result = review_dor.north_star_advice(
        ["Strategic ambiguity remains"], advice={"alignment": "unknown"})

    assert result["invoked"] is True
    assert result["blocking"] is False
    assert "advice_incomplete" in result["reasons"]


def test_loop_derives_consolidated_stage_authority_only_when_rollout_enabled(
        monkeypatch):
    packet, receipt = approved_packet()
    state = {"authority_packet": packet, "authority_receipt": receipt}
    monkeypatch.setattr(loop, "_authorization_fields", lambda ws, st: BASE)

    monkeypatch.delenv("TASKPLANE_CONSOLIDATED_FLOW", raising=False)
    assert loop._derive_consolidated_authority("/repo", state, "execute") is None

    monkeypatch.setenv("TASKPLANE_CONSOLIDATED_FLOW", "1")
    result = loop._derive_consolidated_authority("/repo", state, "execute")
    assert result["authorized"] is True
    assert result["stage"] == "execute"


def test_one_receipt_covers_all_ten_routine_flows():
    packet, receipt = approved_packet()
    trace = authority.routine_flow_trace(
        packet, receipt, current=BASE, actor="user-7", thread="thread-9")
    assert trace["authorized"] is True
    assert tuple(trace["stages"]) == authority.ROUTINE_FLOWS
    assert {row["receipt_fingerprint"] for row in trace["stages"].values()} \
        == {receipt["fingerprint"]}


@pytest.mark.parametrize("stage,evidence", [
    ("product", {"requirement": 1, "acceptance": [1], "contracts": [1],
                 "dependencies": [1], "nfrs": [1], "score": 1}),
    ("design", {"contracts": [1], "graph": [1],
                "acceptance_mapping": [1], "lenses": [1]}),
    ("plan", {"contracts": [1], "graph": [1],
              "acceptance_mapping": [1], "tasks": [1]}),
])
def test_definition_gates_pass_mechanically_without_human(stage, evidence):
    result = authority.mechanical_definition_gate(stage, evidence)
    assert result == {"stage": stage, "passed": True,
                      "human_required": False, "blocker": None}


@pytest.mark.parametrize("stage,missing", [
    ("product", "acceptance"), ("design", "lenses"), ("plan", "graph")])
def test_definition_gaps_name_non_human_blocker(stage, missing):
    result = authority.mechanical_definition_gate(stage, {})
    assert result["passed"] is False
    assert result["human_required"] is False
    assert missing in result["blocker"]


@pytest.mark.parametrize("response,actor,thread,revision,consumed,reason", [
    (None, "user-7", "thread-9", "r1", False,
     "missing_or_ambiguous_response"),
    ("approved", "user-7", "thread-9", "r1", False,
     "missing_or_ambiguous_response"),
    ({"decision": "approve", "authenticated": True}, "user-7", "thread-9",
     "old", False, "wrong_revision"),
    ({"decision": "approve", "authenticated": True}, "user-7", "thread-9",
     "r1", True, "replayed_decision"),
])
def test_human_decisions_fail_closed_for_silence_ambiguity_stale_and_replay(
        response, actor, thread, revision, consumed, reason):
    result = authority.decision_input(
        "final_signoff", response, fact="review complete",
        consequence="ship", actor=actor, thread=thread, revision=revision,
        expected_actor="user-7", expected_thread="thread-9",
        expected_revision="r1", consumed=consumed)
    assert result["authorized"] is False
    assert reason in result["reasons"]
    assert result["new_fact"] and result["consequence"]
    assert result["authority_requested"] == "final_signoff"


def test_product_gate_invokes_persists_nonblocking_north_star_advice():
    requirement = {
        "id": "R-1", "title": "Irreversible direction",
        "acceptance": ["works"], "contracts": ["c"],
        "dependencies": ["d"], "nfrs": ["n"], "score": 1,
        "north_star_advice": {field: "recorded"
                              for field in review_dor.NORTH_STAR_FIELDS},
    }
    result = loop._product_definition_gate(requirement)
    assert result["passed"] is True
    assert result["north_star"]["invoked"] is True
    assert result["north_star"]["blocking"] is False


@pytest.mark.parametrize("variants,selected,revision,authorized,human", [
    ([{"id": "a"}], None, "r1", True, False),
    ([{"id": "a"}, {"id": "b"}], "b", "r1", True, True),
    ([{"id": "a"}, {"id": "b"}], "x", "r1", False, True),
    ([{"id": "a"}, {"id": "b"}], "a", "old", False, True),
])
def test_build_selection_is_only_for_explicit_ab_and_rejects_stale_input(
        variants, selected, revision, authorized, human):
    result = authority.build_selection(
        variants, selected=selected, revision=revision, expected_revision="r1")
    assert result["authorized"] is authorized
    assert result["human_required"] is human


@pytest.mark.parametrize("kind,material", [
    ("cosmetic", False), ("behavioral", False),
    ("acceptance", True), ("scope", True),
])
def test_preview_feedback_is_attributable_and_scoped(kind, material):
    result = authority.preview_change(
        "make the state visible", actor="user-7", authenticated=True,
        requirement="R-1", target={"revision": "r1"}, kind=kind)
    assert result["accepted"] is True
    assert result["actor"] == "user-7"
    assert result["material"] is material
    assert result["reauthorization_required"] is material


def test_unauthenticated_preview_feedback_cannot_change_scope():
    result = authority.preview_change(
        "expand it", actor="", authenticated=False,
        requirement="R-1", target={"revision": "r1"}, kind="scope")
    assert result["accepted"] is False
    assert "unauthenticated" in result["reasons"]


def test_loop_human_boundary_uses_current_receipt_and_revision():
    packet, receipt = approved_packet()
    state = {"authority_packet": packet, "authority_receipt": receipt,
             "authority_target_revision": "r1"}
    result = loop.request_human_decision(
        state, "destructive",
        {"decision": "approve", "authenticated": True},
        actor="user-7", thread="thread-9", revision="r1",
        fact="delete generated cache", consequence="cannot be restored")
    assert result["authorized"] is True
    assert result["authority_requested"] == "destructive"


def test_loop_preview_feedback_projects_current_requirement_and_target():
    state = {"requirement_id": "R-1", "baseline": "r1"}
    result = loop._preview_feedback(
        state, "increase spacing", actor="user-7", authenticated=True,
        kind="cosmetic")
    assert result["accepted"] is True
    assert result["requirement"] == "R-1"
    assert result["target"] == {"revision": "r1"}
