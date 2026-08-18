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
