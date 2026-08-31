"""Worker contracts are child-owned and terminal on every lifecycle path."""

from __future__ import annotations

import json
import os
import types

import pytest

from taskplane import taskplane_lite as tp
from taskplane import tp as cli


def _event(tmp_path, *, name="tp_step_product_pm_deadbeef",
           agent="agent-1", outcome=None):
    event = {
        "hook_event_name": "SubagentStart",
        "cwd": str(tmp_path),
        "session_id": "session-1",
        "turn_id": "turn-1",
        "agent_id": agent,
        "agent_type": name,
        "task_name": name,
    }
    if outcome is not None:
        event["outcome"] = outcome
    return event


def _active_worker(tmp_path, *, stage="pm", task="pm",
                   name="tp_step_product_pm_deadbeef", snapshot=""):
    contract = tp.build_contract(
        "PM: lifecycle", read_only=True, write_allow=["specs/**"])
    contract = tp.prepare_worker_contract(
        str(tmp_path), contract, stage=stage, task=task,
        task_name=name, role_marker="taskplane-role:tp-product", now=10)
    tp.activate(
        str(tmp_path), contract, snapshot=snapshot,
        task_slot_override=contract["task_slot"])
    return contract


def test_pending_worker_slot_never_governs_orchestrator(tmp_path):
    contract = _active_worker(tmp_path)

    assert tp.load_active(str(tmp_path)) is None
    assert not os.path.exists(tp.active_contract_path(str(tmp_path), None))

    start = _event(tmp_path)
    binding = tp.bind_worker_contract_event(str(tmp_path), start, now=11)
    assert binding["slot"] == contract["task_slot"]

    worker = tp.load_active_for_event(str(tmp_path), {
        **start, "hook_event_name": "PreToolUse", "tool_name": "Write"})
    assert worker["task_id"] == contract["task_id"]
    assert worker["worker_lifecycle"]["status"] == "active"
    assert tp.load_active(str(tmp_path)) is None


def test_control_plane_reads_exact_worker_snapshot_without_root_binding(
        tmp_path):
    target = _active_worker(
        tmp_path, stage="execute", task="t1",
        name="tp_step_executor_t1_deadbeef", snapshot="target-head")
    _active_worker(
        tmp_path, stage="evaluate", task="t2",
        name="tp_step_evaluator_t2_deadbeef", snapshot="sibling-head")

    binding = tp.worker_contract_for_stage(
        str(tmp_path), stage="execute", task="t1")

    assert binding["slot"] == target["task_slot"]
    assert tp.snapshot_ref(
        str(tmp_path), task_slot_override=binding["slot"]) == "target-head"
    assert tp.load_active(str(tmp_path)) is None


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("success", "success"),
        ("failed", "failure"),
        ("cancelled", "cancellation"),
        ("interrupted", "interruption"),
        ("handoff", "handoff"),
    ],
)
def test_every_worker_terminal_path_removes_active_slot(
        tmp_path, outcome, expected):
    contract = _active_worker(tmp_path)
    start = _event(tmp_path)
    tp.bind_worker_contract_event(str(tmp_path), start, now=11)
    stop = {**start, "hook_event_name": "SubagentStop", "outcome": outcome}

    result = tp.terminalize_worker_contract(
        str(tmp_path), stop, outcome=outcome,
        submission_status="valid" if outcome == "success" else "missing",
        now=12)

    assert result["released"] is True
    assert result["outcome"] == expected
    assert not os.path.exists(
        tp.active_contract_path(str(tmp_path), contract["task_slot"]))
    quarantine = tmp_path / ".taskplane" / "quarantine" / "contracts"
    rows = list(quarantine.glob("*.json"))
    assert len(rows) == 1
    archived = json.loads(rows[0].read_text(encoding="utf-8"))
    assert archived["worker_lifecycle"]["terminal"]["outcome"] == expected


def test_session_start_sweeps_only_loop_proven_completed_worker(tmp_path):
    completed = _active_worker(tmp_path, stage="pm", task="pm")
    active = _active_worker(
        tmp_path, stage="plan", task="plan",
        name="tp_step_planner_plan_feedface")

    released = tp.sweep_completed_worker_contracts(
        str(tmp_path), loop_state={"step": "plan", "tasks": None}, now=20)

    assert [row["slot"] for row in released] == [completed["task_slot"]]
    assert not os.path.exists(
        tp.active_contract_path(str(tmp_path), completed["task_slot"]))
    assert os.path.exists(
        tp.active_contract_path(str(tmp_path), active["task_slot"]))


def test_session_start_quarantines_completed_legacy_pm_contract_d5810972(
        tmp_path):
    legacy = tp.build_contract(
        "PM: legacy lifecycle", read_only=True,
        write_allow=["specs/**", "docs/**"])
    legacy["task_id"] = "task_d5810972"
    tp.activate(str(tmp_path), legacy, snapshot="legacy-head")

    current = tp.sweep_completed_worker_contracts(
        str(tmp_path), loop_state={"step": "pm", "tasks": None}, now=20)
    assert current == []
    assert os.path.exists(tp.active_contract_path(str(tmp_path), None))

    released = tp.sweep_completed_worker_contracts(
        str(tmp_path), loop_state={"step": "plan", "tasks": None}, now=21)

    assert released[0]["legacy"] is True
    assert not os.path.exists(tp.active_contract_path(str(tmp_path), None))
    assert not os.path.exists(tmp_path / ".taskplane" / "snapshot")
    archived = json.loads(
        (tmp_path / ".taskplane" / "quarantine" / "contracts" /
         "task_d5810972-legacy-21.json").read_text(encoding="utf-8"))
    assert archived["legacy_worker_recovery"]["stage"] == "pm"


def test_native_session_start_context_invokes_completed_worker_sweep(
        tmp_path, monkeypatch, capsys):
    calls = []

    def sweep(workspace, *, loop_state):
        calls.append((workspace, loop_state))
        return []

    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    monkeypatch.setattr(cli.tp, "sweep_completed_worker_contracts", sweep)
    monkeypatch.setattr(cli.sys, "stdin", types.SimpleNamespace(
        read=lambda: "{}"))

    assert cli.cmd_context(types.SimpleNamespace(
        workspace=str(tmp_path))) == 0
    capsys.readouterr()
    assert len(calls) == 1
    assert calls[0][0] == str(tmp_path)


def test_authenticated_release_refuses_before_terminal_and_tampering(tmp_path):
    contract = _active_worker(tmp_path)
    action = contract["worker_lifecycle"]["release_action"]

    with pytest.raises(tp.StateError, match="terminal receipt"):
        tp.release_worker_contract(
            str(tmp_path), contract["task_slot"], action=action)

    tampered = {**action, "contract_id": "task_tampered"}
    with pytest.raises(tp.StateError, match="signature"):
        tp.release_worker_contract(
            str(tmp_path), contract["task_slot"], action=tampered)

    start = _event(tmp_path)
    tp.bind_worker_contract_event(str(tmp_path), start, now=11)
    terminal = tp.record_worker_terminal(
        str(tmp_path), contract["task_slot"],
        event={**start, "hook_event_name": "SubagentStop"},
        outcome="success", submission_status="valid", now=12)
    result = tp.release_worker_contract(
        str(tmp_path), contract["task_slot"], action=action,
        terminal_receipt=terminal)
    assert result["released"] is True


def test_release_exemption_is_exact_and_does_not_restore_bare_clear():
    assert cli._is_release_command("python3 taskplane/tp.py clear") is False
    assert cli._is_release_command(
        "python3 taskplane/tp.py worker-release --slot task_a "
        "--signed-action abc") is True
    assert cli._is_release_command(
        "python3 taskplane/tp.py worker-release --slot task_a") is False


def test_subagent_stop_quarantines_missing_submission_instead_of_stranding(
        tmp_path, monkeypatch, capsys):
    contract = _active_worker(
        tmp_path, stage="execute", task="t1",
        name="tp_step_executor_t1_deadbeef")
    start = _event(tmp_path, name="tp_step_executor_t1_deadbeef")
    tp.bind_worker_contract_event(str(tmp_path), start, now=11)
    event = {**start, "hook_event_name": "SubagentStop", "outcome": "failed"}
    monkeypatch.setattr(cli.sys, "stdin", types.SimpleNamespace(
        read=lambda: json.dumps(event)))
    monkeypatch.setattr(cli, "_submission_stop_check", lambda *a, **k: {
        "block": True, "status": "missing", "contract_id": contract["task_id"],
        "task": "t1", "stage": "execute", "slot": contract["task_slot"],
        "artifact": "loop submission", "recovery": "orchestrator retry"})

    assert cli.cmd_subagent_stop(types.SimpleNamespace()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "systemMessage" in payload
    assert not os.path.exists(
        tp.active_contract_path(str(tmp_path), contract["task_slot"]))


def test_gate_release_targets_exact_stage_and_task(tmp_path):
    target = _active_worker(
        tmp_path, stage="evaluate", task="t1",
        name="tp_step_evaluator_t1_deadbeef")
    sibling = _active_worker(
        tmp_path, stage="evaluate", task="t2",
        name="tp_step_evaluator_t2_deadbeef")

    released = tp.release_worker_contracts_for_gate(
        str(tmp_path), stage="evaluate", task="t1", now=30)

    assert [row["slot"] for row in released] == [target["task_slot"]]
    assert not os.path.exists(
        tp.active_contract_path(str(tmp_path), target["task_slot"]))
    assert os.path.exists(
        tp.active_contract_path(str(tmp_path), sibling["task_slot"]))


@pytest.mark.parametrize("outcome", [
    "success", "failure", "cancellation", "interruption", "handoff"])
def test_every_worker_terminal_outcome_refreshes_dashboard_component_without_closing_run(
        tmp_path, monkeypatch, outcome):
    contract = _active_worker(tmp_path)
    start = _event(tmp_path)
    tp.bind_worker_contract_event(str(tmp_path), start, now=11)
    calls = []
    monkeypatch.setattr(tp, "_refresh_dashboard_lifecycle",
                        lambda workspace, **kw: calls.append((workspace, kw)))

    receipt = tp.record_worker_terminal(
        str(tmp_path), contract["task_slot"], event=start, outcome=outcome,
        submission_status="terminal", now=12)

    assert receipt["outcome"] == tp.normalize_worker_terminal_outcome(outcome)
    assert len(calls) == 1
    assert calls[0][1]["event_type"] == "worker_terminal"
    assert calls[0][1]["outcome"] == receipt["outcome"]
    assert calls[0][1]["member_terminal"] is True
