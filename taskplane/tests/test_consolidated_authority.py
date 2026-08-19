from __future__ import annotations

import os
import copy
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import authority  # noqa: E402
import loop  # noqa: E402
import review_dor  # noqa: E402


def host_event(event, *, actor="user-7", thread="thread-9",
               revision="r1", event_ref="event-1"):
    """Fake the trusted local host/session adapter without cryptography."""
    return {
        "schema": authority.HOST_SESSION_EVENT_SCHEMA,
        "event_fingerprint": authority._fingerprint(
            authority._host_event_payload(event)),
        "event_ref": event_ref, "actor": actor, "thread": thread,
        "revision": revision, "target": {"revision": revision},
        "source": "test-host-session",
    }


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
    state = {"authority_packet": packet, "authority_receipt": receipt,
             "authority_target_revision": "r1"}
    event = {
        "type": "human_decision", "reason": "destructive",
        "response": {"decision": "approve"},
        "fact": "remove generated cache", "consequence": "irreversible",
    }
    observed = host_event(event, event_ref="decision-1")

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    result = loop.handle_host_input(
        "/repo", event, host_event=observed)

    assert result["authorized"] is True


def test_host_input_calls_production_preview_persistence(monkeypatch):
    state = {"requirement_id": "R-1", "authority_target_revision": "r1",
             "authority_receipt": {"actor": "user-7",
                                   "thread": "thread-9"}}
    event = {
        "type": "preview_feedback", "text": "increase spacing",
        "change_kind": "cosmetic",
        # These body labels are intentionally inert. Attribution comes from
        # the separate trusted-session observation below.
        "actor": "body-admin", "thread": "body-thread",
        "revision": "body-revision", "event_id": "body-event",
        "authenticated": False,
    }
    observed = host_event(event, event_ref="preview-1")

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop.tp, "trace", lambda *args, **kwargs: None)
    result = loop.handle_host_input(
        "/repo", event, host_event=observed)

    assert result["accepted"] is True
    assert result["actor"] == "user-7"
    assert state["preview_changes"][0]["fingerprint"] == result["fingerprint"]
    event_id = authority.HostSessionAdapter().observe(event, observed)["event_id"]
    assert event_id != event["event_id"]
    assert list(state["consumed_host_events"]) == [event_id]


def test_host_input_rejects_caller_asserted_identity_without_host_observation(
        monkeypatch):
    state = {}

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)

    result = loop.handle_host_input("/repo", {
        "type": "preview_feedback", "text": "expand scope",
        "actor": "admin", "authenticated": True, "change_kind": "scope",
    })

    assert result["accepted"] is False
    assert "host_session_event_required" in result["reasons"]


def test_host_session_observation_binds_identity_target_and_event():
    event = {"type": "preview_feedback", "text": "increase spacing",
             "change_kind": "cosmetic"}
    observed = host_event(event, event_ref="preview-1")

    adapter = authority.HostSessionAdapter()
    identity = adapter.observe(
        event, observed, expected_actor="admin", expected_thread="thread-9",
        expected_revision="r1", expected_target={"revision": "r1"})
    changed_event = adapter.observe(
        {**event, "text": "expand scope"}, observed,
        expected_actor="user-7", expected_thread="thread-9",
        expected_revision="r1", expected_target={"revision": "r1"})

    assert identity["attributed"] is False
    assert "wrong_actor" in identity["reasons"]
    assert changed_event["attributed"] is False
    assert "host_event_mismatch" in changed_event["reasons"]


def test_human_session_event_is_consumed_atomically_and_cannot_replay(
        monkeypatch):
    event = {
        "type": "human_decision", "reason": "destructive",
        "response": {"decision": "approve"},
        "fact": "remove generated cache", "consequence": "irreversible",
    }
    observed = host_event(event, event_ref="decision-1")
    packet, approval = approved_packet()
    state = {"authority_packet": packet, "authority_receipt": approval,
             "authority_target_revision": "r1",
             "consumed_host_decisions": {}}

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)

    first = loop.handle_host_input("/repo", event, host_event=observed)
    replay = loop.handle_host_input("/repo", event, host_event=observed)

    assert first["authorized"] is True
    assert replay["authorized"] is False
    assert "replayed_decision" in replay["reasons"]
    event_id = authority.HostSessionAdapter().observe(event, observed)["event_id"]
    assert list(state["consumed_host_decisions"]) == [event_id]


def test_preview_session_event_rejects_stale_revision_and_replay_atomically(
        monkeypatch):
    event = {"type": "preview_feedback", "text": "increase spacing",
             "change_kind": "cosmetic"}
    observed = host_event(event, event_ref="preview-1")
    state = {"requirement_id": "R-1", "authority_target_revision": "r2",
             "authority_receipt": {"actor": "user-7",
                                   "thread": "thread-9"},
             "consumed_host_events": {}}

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop.tp, "trace", lambda *args, **kwargs: None)

    stale = loop.handle_host_input("/repo", event, host_event=observed)
    state["authority_target_revision"] = "r1"
    accepted = loop.handle_host_input("/repo", event, host_event=observed)
    replay = loop.handle_host_input("/repo", event, host_event=observed)

    assert stale["accepted"] is False
    assert "wrong_revision" in stale["reasons"]
    assert accepted["accepted"] is True
    assert replay["accepted"] is False
    assert "replayed_event" in replay["reasons"]
    event_id = authority.HostSessionAdapter().observe(event, observed)["event_id"]
    assert list(state["consumed_host_events"]) == [event_id]


def test_workspace_state_needs_no_host_signing_authority(
        monkeypatch):
    saved = {}
    monkeypatch.setattr(loop, "load", lambda ws: None)
    monkeypatch.setattr(loop, "save", lambda ws, state: saved.update(state))
    monkeypatch.setattr(loop.tp, "trace", lambda *args, **kwargs: None)

    result = loop.init("/repo", "secure host input")

    assert not hasattr(authority, "issue_host_input_receipt")
    assert "host_input_secret" not in saved
    assert "host_input_secret" not in result


def test_production_has_no_crypto_signer_verifier_or_key_path():
    assert not hasattr(authority, "HostInputAuthority")
    assert not hasattr(authority, "HostInputVerifier")
    assert not hasattr(authority, "_HOST_RECEIPT_RSA_N")
    assert not hasattr(authority, "_host_receipt_signature_valid")
    assert not hasattr(loop, "_host_input_signing_key")
    assert not hasattr(loop, "_host_input_key_path")


def test_reconciliation_rejects_symlink_substitution(monkeypatch, tmp_path):
    trace_root = tmp_path / "workspace" / ".taskplane"
    trace_root.mkdir(parents=True)
    outside = tmp_path / "attacker-trace.jsonl"
    outside.write_text(
        '{"authority_effect_id":"selection:r1:a"}\n', encoding="utf-8")
    linked = trace_root / "trace.jsonl"
    linked.symlink_to(outside)
    monkeypatch.setattr(loop.tp, "tp_dir", lambda ws: str(trace_root))
    monkeypatch.setattr(loop.tp, "trace_paths", lambda ws: [str(linked)])

    before = outside.read_text(encoding="utf-8")
    assert loop._trace_effect_seen("/workspace", "selection:r1:a") is False
    with pytest.raises(OSError):
        loop._append_authority_trace(
            "/workspace", "loop_select",
            {"authority_effect_id": "selection:r1:a"})
    assert outside.read_text(encoding="utf-8") == before


def test_preview_replay_reconciles_failed_durable_trace_effect(monkeypatch):
    event = {"type": "preview_feedback", "text": "increase spacing",
             "change_kind": "cosmetic"}
    observed_event = host_event(event, event_ref="preview-reconcile")
    state = {"requirement_id": "R-1", "authority_target_revision": "r1",
             "authority_receipt": {"actor": "user-7",
                                   "thread": "thread-9"}}
    observed = set()
    attempts = []

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    def flaky_trace(ws, event_name, data):
        attempts.append(data["authority_effect_id"])
        if len(attempts) == 1:
            raise OSError("trace temporarily unavailable")
        observed.add(data["authority_effect_id"])

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop, "_trace_effect_seen",
                        lambda ws, effect_id: effect_id in observed)
    monkeypatch.setattr(loop, "_append_authority_trace", flaky_trace)

    accepted = loop.handle_host_input("/repo", event, host_event=observed_event)
    replay = loop.handle_host_input("/repo", event, host_event=observed_event)

    event_id = authority.HostSessionAdapter().observe(
        event, observed_event)["event_id"]
    effect = state["authority_effect_outbox"][
        f"preview:{event_id}"]
    assert accepted["accepted"] is True
    assert replay["accepted"] is False
    assert replay["reasons"] == ["replayed_event"]
    assert attempts == [f"preview:{event_id}"] * 2
    assert effect["status"] == "delivered"


def test_selection_replay_reconciles_failed_kb_effect_once(monkeypatch):
    state = {"step": "selection", "goal": "choose", "baseline": "r1",
             "authority_target_revision": "r1", "tasks": [
                 {"id": "a", "variant": "A", "scope": ["a.py"]},
                 {"id": "b", "variant": "B", "scope": ["b.py"]},
             ]}
    traced = set()
    decisions = set()
    kb_attempts = []

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop.tp, "git_head", lambda ws: "r1")
    monkeypatch.setattr(loop, "_trace_effect_seen",
                        lambda ws, effect_id: effect_id in traced)
    monkeypatch.setattr(loop, "_append_authority_trace",
                        lambda ws, event, data:
                        traced.add(data["authority_effect_id"]))
    monkeypatch.setattr(loop, "_kb_effect_seen",
                        lambda ws, effect_id: effect_id in decisions)

    def flaky_kb(ws, *, links, **kwargs):
        effect_id = links["authority_effect"]
        kb_attempts.append(effect_id)
        if len(kb_attempts) == 1:
            raise OSError("KB temporarily unavailable")
        decisions.add(effect_id)

    monkeypatch.setattr(loop.kb, "record_decision", flaky_kb)
    monkeypatch.setattr(loop, "status", lambda ws: {"step": state["step"]})

    selected = loop.select("/repo", "a")
    replay = loop.select("/repo", "a")

    effect_id = "selection:r1:a"
    assert selected["selection"]["choice"] == "a"
    assert "selection only" in replay["error"]
    assert kb_attempts == [effect_id, effect_id]
    assert decisions == {effect_id}
    assert state["authority_effect_outbox"][effect_id]["status"] == "delivered"


def test_loop_load_generally_flushes_pending_authority_outbox(monkeypatch):
    effect_id = "selection:r1:a"
    state = {"step": "em", "authority_effect_outbox": {
        effect_id: {
            "status": "pending",
            "trace": {"delivered": False, "event": "loop_select",
                      "data": {"choice": "a"}},
            "kb": None,
        }}}
    observed = set()

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "_load_raw", lambda ws: state)
    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop, "_trace_effect_seen",
                        lambda ws, wanted: wanted in observed)
    monkeypatch.setattr(
        loop, "_append_authority_trace",
        lambda ws, event, data: observed.add(data["authority_effect_id"]))

    loaded = loop.load("/repo")

    assert loaded["step"] == "em"
    assert state["authority_effect_outbox"][effect_id]["status"] == "delivered"


def test_selection_revision_fence_rolls_back_post_commit_git_change(
        monkeypatch, tmp_path):
    original = {"step": "selection", "goal": "choose", "baseline": "r1",
                "authority_target_revision": "r1", "tasks": [
                    {"id": "a", "variant": "A", "scope": []},
                    {"id": "b", "variant": "B", "scope": []},
                ]}
    stored = copy.deepcopy(original)
    heads = iter(["r1", "r1", "r2"])

    @loop.contextlib.contextmanager
    def fake_lock(path):
        yield

    def fake_save(ws, state):
        stored.clear()
        stored.update(copy.deepcopy(state))

    monkeypatch.setattr(loop, "reconcile_authority_effects",
                        lambda ws: {"delivered": 0, "pending": 0})
    monkeypatch.setattr(loop, "_state_dir", lambda ws: str(tmp_path))
    monkeypatch.setattr(loop, "_load_raw", lambda ws: copy.deepcopy(stored))
    monkeypatch.setattr(loop, "save", fake_save)
    monkeypatch.setattr(loop.tp, "file_lock", fake_lock)
    monkeypatch.setattr(loop.tp, "git_head", lambda ws: next(heads))

    result = loop.select("/repo", "a")

    assert "during the locked selection commit" in result["error"]
    assert result["expected_revision"] == "r1"
    assert result["actual_revision"] == "r2"
    assert stored == original


def test_select_rejects_stale_checkout_and_resumed_current_selection(
        monkeypatch):
    base = {"step": "selection", "goal": "choose", "baseline": "r1",
            "authority_target_revision": "r1", "tasks": [
                {"id": "a", "variant": "A", "scope": []},
                {"id": "b", "variant": "B", "scope": []},
            ]}
    monkeypatch.setattr(loop, "load", lambda ws: dict(base))
    monkeypatch.setattr(loop, "reconcile_authority_effects",
                        lambda ws: {"delivered": 0, "pending": 0})

    @loop.contextlib.contextmanager
    def stale_mutate(ws):
        yield dict(base)

    monkeypatch.setattr(loop, "mutate", stale_mutate)
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
