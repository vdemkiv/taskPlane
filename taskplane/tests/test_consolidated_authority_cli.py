"""Production CLI coverage for consolidated authority host dispatch."""
from __future__ import annotations

import io
import json
import sys

import authority
import loop
import tp
from taskplane.tests.test_consolidated_authority import host_receipt


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

    def fake_handle(ws, event, *, host_receipt):
        captured.update(ws=ws, event=event, host_receipt=host_receipt)
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
    host_receipt = {"schema": "taskplane.host-input-receipt/v1",
                    "signature": "trusted"}
    monkeypatch.setattr(loop, "handle_host_input", fake_handle)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setenv("TASKPLANE_HOST_INPUT_RECEIPT",
                       json.dumps(host_receipt))

    assert tp.main(["loop", "--workspace", "/repo", "host-input"]) == 0
    assert json.loads(capsys.readouterr().out) == {"authorized": True}
    assert captured == {"ws": "/repo", "event": event,
                        "host_receipt": host_receipt}


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
    preview_receipt = host_receipt(preview, event_id="preview-1")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(preview)))
    monkeypatch.setenv("TASKPLANE_HOST_INPUT_RECEIPT",
                       json.dumps(preview_receipt))
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
            ("authenticated", False, "host_receipt_unauthenticated")):
        event = {**decision, field: value}
        # Trusted receipt identity is independent of caller body claims. A
        # receipt for the wrong identity/revision must fail the loop binding.
        receipt_actor = value if field == "actor" else "user-7"
        receipt_thread = value if field == "thread" else "thread-9"
        receipt_revision = value if field == "revision" else "r1"
        receipt = host_receipt(
            event, actor=receipt_actor, thread=receipt_thread,
            revision=receipt_revision, event_id=f"decision-{field}")
        if field == "authenticated":
            receipt["authenticated"] = False
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
        monkeypatch.setenv("TASKPLANE_HOST_INPUT_RECEIPT",
                           json.dumps(receipt))
        assert tp.main(["loop", "--workspace", str(tmp_path),
                        "host-input"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["authorized"] is False
        assert reason in result["reasons"]


def test_loop_host_input_rejects_forgeable_stdin_identity(monkeypatch,
                                                          capsys):
    event = {
        "type": "human_decision", "reason": "final_signoff",
        "response": {"decision": "approve", "authenticated": True},
        "actor": "admin", "thread": "trusted", "revision": "r1",
        "authenticated": True, "consumed": False,
    }
    monkeypatch.delenv("TASKPLANE_HOST_INPUT_RECEIPT", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))

    @loop.contextlib.contextmanager
    def fake_mutate(ws):
        yield {}

    monkeypatch.setattr(loop, "mutate", fake_mutate)

    assert tp.main(["loop", "--workspace", "/repo", "host-input"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["authorized"] is False
    assert result["reasons"] == ["host_receipt_required"]


def test_loop_host_input_rejects_non_object_event(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))

    assert tp.main(["loop", "--workspace", "/repo", "host-input"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "host event must be a JSON object"}
