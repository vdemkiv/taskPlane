"""Production CLI coverage for consolidated authority host dispatch."""
from __future__ import annotations

import io
import json
import sys

import authority
import loop
import tp
from taskplane.tests.test_consolidated_authority import host_event


def test_loop_authorize_dispatches_every_routine_flow(monkeypatch, capsys):
    calls = []

    def fake_authorize(ws, flow):
        calls.append((ws, flow))
        return {"authorized": True, "flow": flow}

    monkeypatch.setattr(loop, "authorize_routine_flow", fake_authorize)

    for flow in authority.ROUTINE_FLOWS:
        assert tp.main(["loop", "--workspace", "/repo", "authorize",
                        flow]) == 0
        assert json.loads(capsys.readouterr().out) == {
            "authorized": True, "flow": flow}

    assert calls == [("/repo", flow) for flow in authority.ROUTINE_FLOWS]


def test_loop_host_input_dispatches_bound_human_decision(monkeypatch,
                                                         capsys):
    captured = {}

    def fake_handle(ws, event, *, host_event):
        captured.update(ws=ws, event=event, host_event=host_event)
        return {"authorized": True}

    event = {
        "type": "human_decision",
        "reason": "destructive",
        "response": {"decision": "approve", "authenticated": True},
        "actor": "user-7",
        "thread": "thread-9",
        "revision": "r1",
        "fact": "remove generated cache",
        "consequence": "cannot be restored",
        "authenticated": True,
    }
    observed = {"schema": "taskplane.host-session-event/v1",
                "source": "test-host-session"}
    monkeypatch.setattr(loop, "handle_host_input", fake_handle)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setenv("TASKPLANE_HOST_SESSION_EVENT",
                       json.dumps(observed))

    assert tp.main(["loop", "--workspace", "/repo", "host-input"]) == 0
    assert json.loads(capsys.readouterr().out) == {"authorized": True}
    assert captured == {"ws": "/repo", "event": event,
                        "host_event": observed}


def test_loop_host_input_uses_current_target_and_rejects_bad_identity(
        monkeypatch, tmp_path, capsys):
    state = {
        "requirement_id": "R-1",
        "baseline": "r1",
        "authority_target_revision": "r1",
        "authority_receipt": {"actor": "user-7", "thread": "thread-9"},
    }

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield state

    monkeypatch.setattr(loop, "mutate", fake_mutate)
    monkeypatch.setattr(loop.tp, "trace", lambda *args, **kwargs: None)

    preview = {
        "type": "preview_feedback", "text": "increase spacing",
        "actor": "user-7", "authenticated": True,
        "change_kind": "cosmetic",
    }
    preview_observation = host_event(preview, event_ref="preview-1")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(preview)))
    monkeypatch.setenv("TASKPLANE_HOST_SESSION_EVENT",
                       json.dumps(preview_observation))
    assert tp.main(["loop", "--workspace", str(tmp_path),
                    "host-input"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["target"] == {"revision": "r1"}
    assert result["requirement"] == "R-1"

    decision = {
        "type": "human_decision", "reason": "final_signoff",
        "response": {"decision": "approve", "authenticated": True},
        "actor": "user-7", "thread": "thread-9", "revision": "r1",
        "fact": "review complete", "consequence": "ship",
        "authenticated": True,
    }
    for field, value, reason in (
            ("actor", "other", "wrong_actor"),
            ("thread", "other", "wrong_thread"),
            ("revision", "old", "wrong_revision"),
            ("target", {"revision": "old"}, "wrong_target")):
        event = {**decision, field: value}
        # Trusted session metadata, not caller body labels, supplies identity.
        receipt_actor = value if field == "actor" else "user-7"
        receipt_thread = value if field == "thread" else "thread-9"
        receipt_revision = value if field == "revision" else "r1"
        observed = host_event(
            event, actor=receipt_actor, thread=receipt_thread,
            revision=receipt_revision, event_ref=f"decision-{field}")
        if field == "target":
            observed["target"] = value
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
        monkeypatch.setenv("TASKPLANE_HOST_SESSION_EVENT",
                           json.dumps(observed))
        assert tp.main(["loop", "--workspace", str(tmp_path),
                        "host-input"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["authorized"] is False
        assert reason in result["reasons"]


def test_loop_host_input_rejects_body_identity_without_session_observation(
        monkeypatch, capsys):
    event = {
        "type": "human_decision", "reason": "final_signoff",
        "response": {"decision": "approve", "authenticated": True},
        "actor": "admin", "thread": "trusted", "revision": "r1",
        "authenticated": True, "consumed": False,
    }
    monkeypatch.delenv("TASKPLANE_HOST_SESSION_EVENT", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield {}

    monkeypatch.setattr(loop, "mutate", fake_mutate)

    assert tp.main(["loop", "--workspace", "/repo", "host-input"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["authorized"] is False
    assert result["reasons"] == ["host_session_event_required"]


def test_loop_host_input_rejects_non_object_event(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))

    assert tp.main(["loop", "--workspace", "/repo", "host-input"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "host event must be a JSON object"}
