"""R-0003 t03: entry, stage, gate, status, and projection integration."""
from __future__ import annotations

import json
import os

import dashboard
import host_capabilities as hc
import loop
import runtime_eval
import taskplane_lite as lite
import tp as cli


def _snapshot(workspace: str, *, live: bool):
    rows = {}
    if live:
        rows = {
            "native_plugin_hooks_loaded": hc.Observation(
                status="supported", source="runtime-hook:native",
                confidence="high", reason="entry PreToolUse executed"),
            "managed_policy_permission": hc.Observation(
                status="supported", source="runtime-hook:execution",
                confidence="high", reason="hook command executed"),
        }
    return hc.probe_snapshot(
        workspace, host="claude", install_context="personal",
        native_installed=True, bridge_configured=False,
        observations=rows, session_id="claude-session",
        now="2026-08-20T12:00:00Z")


def _strict(monkeypatch, tmp_path, *, live: bool):
    workspace = str(tmp_path / "repo")
    os.makedirs(workspace)
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-session")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(
        cli, "_host_capability_snapshot",
        lambda ws, install_context=None: _snapshot(ws, live=live))
    return workspace


def test_strict_unproven_new_refuses_before_contract_state(
        monkeypatch, tmp_path, capsys):
    workspace = _strict(monkeypatch, tmp_path, live=False)

    code = cli.main(["new", "goal", "--workspace", workspace])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["schema"] == "taskplane.enforcement-refusal/v1"
    assert payload["enforcement"]["status"] == "unproven"
    assert lite.load_active(workspace) is None
    assert loop.load(workspace) is None


def test_live_entry_uses_one_snapshot_and_projects_same_evidence(
        monkeypatch, tmp_path, capsys):
    workspace = _strict(monkeypatch, tmp_path, live=True)
    calls = {"snapshot": 0}

    def observed(ws, install_context=None):
        calls["snapshot"] += 1
        return _snapshot(ws, live=True)

    monkeypatch.setattr(cli, "_host_capability_snapshot", observed)
    assert cli.main(["new", "goal", "--workspace", workspace]) == 0
    capsys.readouterr()
    contract = lite.load_active(workspace)

    assert calls == {"snapshot": 1}
    assert contract["enforcement"]["status"] == "live"
    assert cli.main(["status", "--workspace", workspace]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["enforcement"]["evidence_id"] == \
        contract["enforcement"]["evidence_id"]


def test_advisory_requires_by_and_is_persisted_on_loop_and_dashboard(
        monkeypatch, tmp_path, capsys):
    workspace = _strict(monkeypatch, tmp_path, live=False)
    args = ["loop", "--workspace", workspace, "init", "goal",
            "--advisory"]

    assert cli.main(args) == 1
    assert loop.load(workspace) is None
    capsys.readouterr()
    assert cli.main(args + ["--by", "Dana"] ) == 0
    payload = json.loads(capsys.readouterr().out)
    state = loop.load(workspace)
    decision = state["enforcement"]["current"]

    assert payload["enforcement"]["evidence_id"] == decision["evidence_id"]
    assert decision["status"] == "advisory"
    assert decision["advisory"]["actor"] == "Dana"
    status = loop.status(workspace)
    assert status["enforcement"]["current"]["evidence_id"] == \
        decision["evidence_id"]
    rendered = dashboard.widget(workspace)
    assert "screen enforcement: advisory" in rendered
    assert "acknowledged by Dana" in rendered


def test_mid_run_loss_blocks_gate_until_explicit_advisory(
        monkeypatch, tmp_path, capsys):
    workspace = _strict(monkeypatch, tmp_path, live=True)
    loop.init(workspace, "goal")
    live, refusal = cli._enforcement_check(workspace)
    assert refusal is None
    loop.record_enforcement(workspace, live)
    called = {"gate": 0}

    def fake_gate(ws, outcome, note="", task_id=None, rid=None):
        called["gate"] += 1
        return {"step": "plan", "status": loop.status(ws)}

    monkeypatch.setattr(loop, "gate", fake_gate)
    monkeypatch.setattr(
        lite, "screen_liveness",
        lambda ws: {"governed": True, "hook_seen": False,
                    "warning": "contract has ZERO screen activity"})
    base = ["loop", "--workspace", workspace, "gate", "pass"]

    assert cli.main(base) == 1
    refused = json.loads(capsys.readouterr().out)
    assert refused["enforcement"]["status"] == "unproven"
    assert called["gate"] == 0

    assert cli.main(base + ["--advisory", "--by", "Dana"]) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert called["gate"] == 1
    assert accepted["enforcement"]["status"] == "advisory"
    assert loop.load(workspace)["enforcement"]["current"]["advisory"][
        "actor"] == "Dana"


def test_runtime_projection_retains_exact_authority_identity(
        monkeypatch, tmp_path):
    workspace = _strict(monkeypatch, tmp_path, live=False)
    base, _ = cli._enforcement_check(workspace)
    decision = cli.enforcement_kernel.acknowledge_advisory(
        base, actor="Dana", acknowledged_at="2026-08-20T12:01:00Z")

    projected = runtime_eval.enforcement_projection(decision)

    assert projected["status"] == "advisory"
    assert projected["evidence_id"] == decision["evidence_id"]
    assert projected["advisory"]["actor"] == "Dana"
