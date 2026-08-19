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


def test_one_receipt_covers_all_ten_production_flow_entry_points(monkeypatch):
    packet, receipt = approved_packet()
    state = {"authority_packet": packet, "authority_receipt": receipt}
    monkeypatch.setattr(loop, "load", lambda ws: state)
    monkeypatch.setattr(loop, "_authorization_fields", lambda ws, st: BASE)
    monkeypatch.setattr(loop.tp, "trace", lambda *args, **kwargs: None)
    monkeypatch.setenv("TASKPLANE_CONSOLIDATED_FLOW", "1")

    results = {flow: loop.authorize_routine_flow("/repo", flow)
               for flow in authority.ROUTINE_FLOWS}

    assert all(row["authorized"] for row in results.values())
    assert tuple(results) == authority.ROUTINE_FLOWS
    assert {row["receipt_fingerprint"] for row in results.values()} \
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


@pytest.mark.parametrize("missing", ["contracts", "dependencies", "nfrs"])
def test_product_gate_blocks_missing_required_evidence(missing):
    requirement = {
        "id": "R-gap", "title": "Incomplete requirement",
        "acceptance": ["works"], "contracts": ["c"],
        "dependencies": ["d"], "nfrs": ["n"], "score": 1,
    }
    requirement[missing] = []

    result = loop._product_definition_gate(requirement)

    assert result["passed"] is False
    assert missing in result["blocker"]


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


def test_host_input_calls_production_human_decision_boundary(monkeypatch):
    packet, receipt = approved_packet()
    secret = "33" * 32
    state = {"authority_packet": packet, "authority_receipt": receipt,
             "authority_target_revision": "r1"}
    event = {
        "type": "human_decision", "reason": "destructive",
        "response": {"decision": "approve"},
        "fact": "remove generated cache", "consequence": "irreversible",
    }
    host_receipt = authority.HostInputAuthority(secret).issue(
        event, actor="user-7", thread="thread-9", revision="r1",
        event_id="decision-1")

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop, "_host_input_signing_key", lambda ws: secret)
    result = loop.handle_host_input(
        "/repo", event, host_receipt=host_receipt)

    assert result["authorized"] is True


def test_host_input_calls_production_preview_persistence(monkeypatch):
    secret = "44" * 32
    state = {"requirement_id": "R-1", "authority_target_revision": "r1"}
    event = {
        "type": "preview_feedback", "text": "increase spacing",
        "change_kind": "cosmetic",
    }
    host_receipt = authority.HostInputAuthority(secret).issue(
        event, actor="user-7", thread="thread-9", revision="r1",
        event_id="preview-1")

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop, "_host_input_signing_key", lambda ws: secret)
    monkeypatch.setattr(loop.tp, "trace", lambda *args, **kwargs: None)
    result = loop.handle_host_input(
        "/repo", event, host_receipt=host_receipt)

    assert result["accepted"] is True
    assert state["preview_changes"] == [result]
    assert list(state["consumed_host_events"]) == ["preview-1"]


def test_host_input_rejects_caller_asserted_identity_without_engine_receipt(
        monkeypatch):
    state = {}

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop, "_host_input_signing_key",
                        lambda ws: "11" * 32)

    result = loop.handle_host_input("/repo", {
        "type": "preview_feedback", "text": "expand scope",
        "actor": "admin", "authenticated": True, "change_kind": "scope",
    })

    assert result["accepted"] is False
    assert "host_receipt_required" in result["reasons"]


def test_host_input_rejects_forged_receipt_identity_and_event():
    secret = "12" * 32
    event = {"type": "preview_feedback", "text": "increase spacing",
             "change_kind": "cosmetic"}
    codec = authority.HostInputAuthority(secret)
    receipt = codec.issue(
        event, actor="user-7", thread="thread-9", revision="r1",
        event_id="preview-1")

    forged_identity = {**receipt, "actor": "admin"}
    identity = codec.verify(event, forged_identity)
    changed_event = codec.verify(
        {**event, "text": "expand scope"}, receipt)

    assert identity["authenticated"] is False
    assert "host_receipt_unauthenticated" in identity["reasons"]
    assert changed_event["authenticated"] is False
    assert "host_event_mismatch" in changed_event["reasons"]


def test_human_decision_receipt_is_consumed_atomically_and_cannot_replay(
        monkeypatch):
    secret = "22" * 32
    event = {
        "type": "human_decision", "reason": "destructive",
        "response": {"decision": "approve"},
        "fact": "remove generated cache", "consequence": "irreversible",
    }
    receipt = authority.HostInputAuthority(secret).issue(
        event, actor="user-7", thread="thread-9", revision="r1",
        event_id="decision-1")
    packet, approval = approved_packet()
    state = {"authority_packet": packet, "authority_receipt": approval,
             "authority_target_revision": "r1",
             "consumed_host_decisions": {}}

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop, "_host_input_signing_key", lambda ws: secret)

    first = loop.handle_host_input("/repo", event, host_receipt=receipt)
    replay = loop.handle_host_input("/repo", event, host_receipt=receipt)

    assert first["authorized"] is True
    assert replay["authorized"] is False
    assert "replayed_decision" in replay["reasons"]
    assert list(state["consumed_host_decisions"]) == ["decision-1"]


def test_preview_receipt_rejects_stale_revision_and_replay_atomically(
        monkeypatch):
    secret = "23" * 32
    event = {"type": "preview_feedback", "text": "increase spacing",
             "change_kind": "cosmetic"}
    receipt = authority.HostInputAuthority(secret).issue(
        event, actor="user-7", thread="thread-9", revision="r1",
        event_id="preview-1")
    state = {"requirement_id": "R-1", "authority_target_revision": "r2",
             "consumed_host_events": {}}

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop, "_host_input_signing_key", lambda ws: secret)
    monkeypatch.setattr(loop.tp, "trace", lambda *args, **kwargs: None)

    stale = loop.handle_host_input("/repo", event, host_receipt=receipt)
    state["authority_target_revision"] = "r1"
    accepted = loop.handle_host_input("/repo", event, host_receipt=receipt)
    replay = loop.handle_host_input("/repo", event, host_receipt=receipt)

    assert stale["accepted"] is False
    assert "wrong_revision" in stale["reasons"]
    assert accepted["accepted"] is True
    assert replay["accepted"] is False
    assert "replayed_event" in replay["reasons"]
    assert list(state["consumed_host_events"]) == ["preview-1"]


def test_workspace_state_cannot_disclose_or_mint_host_signing_authority(
        monkeypatch):
    saved = {}
    created = []
    monkeypatch.setattr(loop, "load", lambda ws: None)
    monkeypatch.setattr(loop, "save", lambda ws, state: saved.update(state))
    monkeypatch.setattr(loop.tp, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop, "_host_input_signing_key",
                        lambda ws, create=False: created.append(create) or
                        "24" * 32)

    result = loop.init("/repo", "secure host input")

    assert not hasattr(authority, "issue_host_input_receipt")
    assert "host_input_secret" not in saved
    assert "host_input_secret" not in result
    assert created == [True]


def test_host_signing_key_is_external_private_state(monkeypatch, tmp_path):
    private_root = tmp_path / "engine-private"
    monkeypatch.setattr(loop.tp, "external_store_root",
                        lambda ws: str(private_root))

    secret = loop._host_input_signing_key("/workspace", create=True)
    path = loop._host_input_key_path("/workspace")

    assert len(secret) == 64
    assert path.startswith(str(private_root))
    assert not path.startswith("/workspace/")
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(os.path.dirname(path)).st_mode & 0o777 == 0o700


def test_select_rejects_stale_checkout_and_resumed_current_selection(
        monkeypatch):
    base = {"step": "selection", "goal": "choose", "baseline": "r1",
            "authority_target_revision": "r1", "tasks": [
                {"id": "a", "variant": "A", "scope": []},
                {"id": "b", "variant": "B", "scope": []},
            ]}
    monkeypatch.setattr(loop, "load", lambda ws: dict(base))
    monkeypatch.setattr(loop.tp, "git_head", lambda ws: "r2")
    stale = loop.select("/repo", "a")
    assert "stale" in stale["error"].lower()
    assert stale["expected_revision"] == "r1"
    assert stale["actual_revision"] == "r2"

    saved = {}

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        state = dict(base)
        yield state
        saved.update(state)

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop.tp, "git_head", lambda ws: "r1")
    monkeypatch.setattr(loop.tp, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop, "status", lambda ws: {"step": "em"})
    resumed = loop.select("/repo", "a", note="resumed choice")
    assert resumed["step"] == "em"
    assert resumed["selection"]["revision"] == "r1"
    assert saved["selection"]["choice"] == "a"


def test_select_revalidates_revision_under_lock_before_side_effects(
        monkeypatch):
    state = {"step": "selection", "goal": "choose", "baseline": "r1",
             "authority_target_revision": "r1", "tasks": [
                 {"id": "a", "variant": "A", "scope": []},
                 {"id": "b", "variant": "B", "scope": []},
             ]}
    locked = False
    effects = []

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        nonlocal locked
        locked = True
        try:
            yield state
        finally:
            locked = False

    monkeypatch.setattr(loop, "load", lambda ws: state)
    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop.tp, "git_head",
                        lambda ws: "r2" if locked else "r1")
    monkeypatch.setattr(loop.tp, "trace",
                        lambda *args, **kwargs: effects.append("trace"))
    monkeypatch.setattr(loop.kb, "record_decision",
                        lambda *args, **kwargs: effects.append("kb"))

    result = loop.select("/repo", "a")

    assert "stale" in result["error"].lower()
    assert result["expected_revision"] == "r1"
    assert result["actual_revision"] == "r2"
    assert "selection" not in state
    assert state["step"] == "selection"
    assert effects == []
