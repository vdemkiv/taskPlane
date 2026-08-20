"""R-0003 t05: hooks, contracts, status, dashboard, and audit adapters."""
from __future__ import annotations

from argparse import Namespace
import io
import json
import os
import sys

import collision
import dashboard
import runtime_eval
import taskplane_lite as kernel
import tp as cli


def _event(workspace, *, skill=None, agent=None):
    tool_input = {}
    if skill is not None:
        tool_input["skill"] = skill
    if agent is not None:
        tool_input["subagent_type"] = agent
    return {"cwd": str(workspace), "tool_input": tool_input}


def _invoke(monkeypatch, capsys, fn, event):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert fn(Namespace()) == 0
    return capsys.readouterr().out.strip()


def _activate(workspace, *, advisory=False):
    contract = kernel.build_contract("test", scope=["**"])
    contract["enforcement"] = {
        "status": "advisory" if advisory else "live",
        "evidence_id": "evidence",
    }
    kernel.activate(str(workspace), contract, snapshot=None)
    return contract


def test_both_manifests_route_skill_to_dedicated_screen():
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    for name in ("hooks/hooks.json", ".codex/hooks.json"):
        with open(os.path.join(root, name), encoding="utf-8") as handle:
            manifest = json.load(handle)
        rows = manifest["hooks"]["PreToolUse"]
        skill = [row for row in rows if row.get("matcher") == "Skill"]
        assert len(skill) == 1
        assert "screen-skill" in skill[0]["hooks"][0]["command"]
        assert "screen-skill" in skill[0]["hooks"][0]["commandWindows"]


def test_skill_screen_noops_without_exact_governed_state(
        tmp_path, monkeypatch, capsys):
    output = _invoke(
        monkeypatch, capsys, cli.cmd_screen_skill,
        _event(tmp_path, skill="orchestrator-supaconductor:go"))
    assert output == ""
    assert collision.load_ledger(str(tmp_path)) is None


def test_known_skill_denied_and_helpers_silent_mid_run(
        tmp_path, monkeypatch, capsys):
    contract = _activate(tmp_path)
    denied = json.loads(_invoke(
        monkeypatch, capsys, cli.cmd_screen_skill,
        _event(tmp_path, skill="orchestrator-supaconductor:go")))
    reason = denied["hookSpecificOutput"]["permissionDecisionReason"]
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert contract["task_id"] in reason
    assert "step=contract" in reason
    assert "tp loop next" in reason

    silent = _invoke(monkeypatch, capsys, cli.cmd_screen_skill,
                     _event(tmp_path, skill="docx"))
    assert silent == ""
    ledger = collision.load_ledger(str(tmp_path))
    assert ledger["counts"]["denied_skills"] == 1
    assert all(row["identity"] != "docx" for row in ledger["identities"])


def test_unknown_skill_advises_then_strict_denies(
        tmp_path, monkeypatch, capsys):
    _activate(tmp_path)
    advised = json.loads(_invoke(
        monkeypatch, capsys, cli.cmd_screen_skill,
        _event(tmp_path, skill="unknown-plugin:workflow")))
    assert "systemMessage" in advised
    monkeypatch.setenv("TASKPLANE_SKILL_STRICT", "1")
    denied = json.loads(_invoke(
        monkeypatch, capsys, cli.cmd_screen_skill,
        _event(tmp_path, skill="unknown-plugin:workflow")))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_foreign_agent_denial_is_independent_of_dispatch_tier_flag(
        tmp_path, monkeypatch, capsys):
    _activate(tmp_path)
    monkeypatch.delenv("TASKPLANE_ENFORCE_DISPATCH", raising=False)
    denied = json.loads(_invoke(
        monkeypatch, capsys, cli.cmd_screen_dispatch,
        _event(tmp_path,
               agent="orchestrator-supaconductor:conductor-orchestrator")))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "orchestrator-supaconductor" in \
        denied["hookSpecificOutput"]["permissionDecisionReason"]


def test_malformed_dispatch_fails_closed_in_governed_workspace_even_unset(
        tmp_path, monkeypatch, capsys):
    _activate(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TASKPLANE_ENFORCE_DISPATCH", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert cli.cmd_screen_dispatch(Namespace()) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "malformed hook input" in \
        output["hookSpecificOutput"]["permissionDecisionReason"]


def test_advisory_records_observation_without_claiming_denial(
        tmp_path, monkeypatch, capsys):
    _activate(tmp_path, advisory=True)
    output = json.loads(_invoke(
        monkeypatch, capsys, cli.cmd_screen_skill,
        _event(tmp_path, skill="orchestrator-supaconductor:go")))
    assert "systemMessage" in output
    assert "would deny" in output["systemMessage"]
    assert "permissionDecision" not in json.dumps(output)
    assert collision.load_ledger(str(tmp_path))["counts"][
        "observed_invocations"] == 1


def test_activation_excludes_signed_root_and_exact_override_is_attributable(
        tmp_path):
    root = tmp_path / "conductor"
    root.mkdir()
    (root / "metadata.json").write_text("{}", encoding="utf-8")
    (root / "message-bus").mkdir()
    ordinary = kernel.build_contract("ordinary", scope=["**"])
    kernel.activate(str(tmp_path), ordinary, snapshot=None)
    assert "conductor/**" in ordinary["coding"]["out_of_scope_paths"]
    assert ordinary["foreign_state"]["excluded_roots"] == ["conductor"]
    kernel.clear(str(tmp_path))

    override = kernel.build_contract("override", scope=["**"])
    override["foreign_state_override"] = {
        "roots": ["conductor"], "actor": "human@example.com"}
    kernel.activate(str(tmp_path), override, snapshot=None)
    assert "conductor/**" not in override["coding"]["out_of_scope_paths"]
    assert override["foreign_state"]["overrides"] == [{
        "root": "conductor", "actor": "human@example.com"}]


def test_unsigned_same_named_root_is_not_added_to_contract(tmp_path):
    (tmp_path / "conductor").mkdir()
    contract = _activate(tmp_path)
    assert "foreign_state" not in contract
    assert "conductor/**" not in contract["coding"]["out_of_scope_paths"]


def test_status_and_dashboard_read_durable_record_without_rediscovery(
        tmp_path, monkeypatch, capsys):
    _activate(tmp_path)
    decision = collision.classify(
        "skill", "unknown:workflow", governed=True, run_id="run",
        step="execute")
    collision.persist(str(tmp_path), decision=decision, observed_at=1)
    monkeypatch.setattr(collision, "discover_state_roots",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            AssertionError("status rediscovered")))
    assert cli.cmd_status(Namespace(workspace=str(tmp_path))) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["foreign_interference"]["counts"][
        "advised_invocations"] == 1
    html = dashboard.widget(str(tmp_path))
    assert "foreign interference" in html
    assert "unknown:workflow" in html


def test_runtime_projection_headlines_only_nonzero_interference():
    clean = runtime_eval.foreign_interference_projection(None)
    assert clean["headline"] is False
    ledger = collision.empty_ledger(run_id="run")
    ledger = collision.record(
        ledger, collision.classify(
            "agent", "orchestrator-supaconductor:worker", governed=True,
            run_id="run", step="execute"), observed_at=1)
    projected = runtime_eval.foreign_interference_projection(ledger)
    assert projected["headline"] is True
    assert projected["counts"]["denied_agents"] == 1
    assert projected["identities"] == [
        "orchestrator-supaconductor:worker"]
