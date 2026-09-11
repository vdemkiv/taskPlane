"""R-0003 t03: entry, stage, gate, status, and projection integration."""
from __future__ import annotations

import pytest
import json
import os
import subprocess

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
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Taskplane Test"],
                   cwd=workspace, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "base"],
                   cwd=workspace, check=True)
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-session")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "claude-session")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    from taskplane import storage, run_store, requirements
    store = run_store.RunStore()
    identity = storage.resolve_repository_identity(workspace)
    store.create(identity, run_id="enforcement-run", checkout=workspace,
        host={"kind": "claude", "session_id": "claude-session"},
        target={"kind": "workspace", "revision": lite.git_head(workspace)})
    storage.write_workspace_locator(workspace, identity=identity,
        layout=storage.resolve_layout(identity, home=store.home, run_id="enforcement-run"),
        run_id="enforcement-run")
    requirements.record_requirement(workspace, "Enforced delivery",
        functional=["preserve exact host enforcement evidence"],
        acceptance=["unproven hooks require explicit advisory acknowledgement"])
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


def test_advisory_cannot_bypass_hook_evidence(monkeypatch, tmp_path, capsys):
    workspace = _strict(monkeypatch, tmp_path, live=False)
    args = ["loop", "--workspace", workspace, "init", "goal",
            "--req", "R-0001", "--advisory"]
    for identity in ([], ["--by", "Dana"]):
        assert cli.main(args + identity) == 1
        payload = json.loads(capsys.readouterr().out)
        assert "harness bypass is disabled" in payload["error"]
        assert loop.load(workspace) is None


def test_saved_advisory_cannot_bypass_fresh_hook_check(monkeypatch, tmp_path):
    workspace = _strict(monkeypatch, tmp_path, live=False)
    base, _ = cli._enforcement_check(workspace)
    saved = cli.enforcement_kernel.acknowledge_advisory(base, actor="Dana")
    decision, refusal = cli._enforcement_check(workspace, saved=saved)
    assert refusal and decision["status"] == "unproven"
    assert decision["mode"] == "strict"


def test_every_host_requires_strict_enforcement(monkeypatch):
    monkeypatch.delenv("TASKPLANE_ENFORCE_SCREEN", raising=False)
    for host in ("codex", "claude", "unknown"):
        assert cli._screen_enforcement_mode(host) == "strict"
    for mode in ("off", "warn"):
        monkeypatch.setenv("TASKPLANE_ENFORCE_SCREEN", mode)
        with pytest.raises(cli.enforcement_kernel.EnforcementError, match="bypass is disabled"):
            cli._screen_enforcement_mode("codex")


def test_mid_run_loss_still_blocks_gate_with_explicit_advisory(
        monkeypatch, tmp_path, capsys):
    workspace = _strict(monkeypatch, tmp_path, live=True)
    initialized = loop.init(workspace, "goal", requirement_id="R-0001", by="Dana")
    assert "error" not in initialized, initialized
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

    assert cli.main(base + ["--advisory", "--by", "Dana"]) == 1
    refused = json.loads(capsys.readouterr().out)
    assert called["gate"] == 0
    assert "harness bypass is disabled" in refused["error"]


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
