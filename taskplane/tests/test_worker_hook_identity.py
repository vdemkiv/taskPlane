"""Current Codex hook profiles are not native task names.

These are protocol fixtures, not host lifecycle or release receipts.
"""
from __future__ import annotations

import json
import io

import pytest

from taskplane import taskplane_lite as tp
from taskplane import spend, tp as cli


NAME = "tp_step_product_pm_deadbeef"


def _contract(root):
    contract = tp.prepare_worker_contract(
        str(root), tp.build_contract("PM: identity", read_only=True),
        stage="pm", task="pm", task_name=NAME,
        role_marker="taskplane-role:tp-product", now=10)
    tp.activate(str(root), contract, task_slot_override=contract["task_slot"])
    return contract


def _parent(root, *, child="child-1", name=NAME, started_at=11000,
            parent="parent-1", duplicate=False):
    path = root / "parent.jsonl"
    activity = {
        "type": "event_msg", "payload": {
            "type": "item_completed", "thread_id": parent,
            "turn_id": "parent-turn", "started_at_ms": started_at,
            "item": {"type": "SubAgentActivity", "kind": "started",
                     "agent_thread_id": child, "agent_path": "/root/" + name}}}
    rows = [{"type": "session_meta", "payload": {
        "id": parent, "cwd": str(root), "source": "cli"}}, activity]
    if duplicate:
        rows.append(activity)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _child(root, *, child="child-1", parent="parent-1", name=NAME):
    path = root / "child.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": child, "cwd": str(root), "parent_thread_id": parent,
        "agent_path": "/root/" + name,
        "source": {"subagent": {"thread_spawn": {
            "parent_thread_id": parent, "agent_path": "/root/" + name}}}}}) + "\n",
        encoding="utf-8")
    return path


def _start(root, path):
    return {"hook_event_name": "SubagentStart", "cwd": str(root),
            "session_id": "parent-1", "agent_id": "child-1",
            "agent_type": "default", "turn_id": "parent-turn",
            "transcript_path": str(path)}


def test_profile_start_child_actions_and_stop_keep_one_exact_owner(tmp_path):
    contract = _contract(tmp_path)
    start = _start(tmp_path, _parent(tmp_path))
    bound = tp.bind_worker_contract_event(str(tmp_path), start, now=12)
    assert bound["slot"] == contract["task_slot"]
    assert bound["contract"]["worker_lifecycle"]["owner"] == {
        "session_id": "parent-1", "agent_id": "child-1", "task_name": NAME}
    child_path = _child(tmp_path)
    action = {"hook_event_name": "PreToolUse", "cwd": str(tmp_path),
              "session_id": "child-1", "turn_id": "child-turn",
              "transcript_path": str(child_path), "tool_name": "apply_patch"}
    assert tp.load_active_for_event(str(tmp_path), action)["task_id"] == contract["task_id"]
    assert tp.load_active_for_event(str(tmp_path), {
        "session_id": "parent-1", "turn_id": "parent-turn",
        "cwd": str(tmp_path), "transcript_path": start["transcript_path"]}) is None
    stop = {**start, "hook_event_name": "SubagentStop",
            "agent_transcript_path": str(child_path)}
    result = tp.terminalize_worker_contract(
        str(tmp_path), stop, outcome="success", submission_status="valid", now=13)
    assert result["terminal_receipt"]["authority"] == "host-lifecycle"
    assert result["released"] is True


def test_child_metadata_alone_never_activates_pending_contract(tmp_path):
    contract = _contract(tmp_path)
    event = {"hook_event_name": "PreToolUse", "cwd": str(tmp_path),
             "session_id": "child-1", "turn_id": "child-turn",
             "transcript_path": str(_child(tmp_path))}
    with pytest.raises(tp.StateError, match="before its SubagentStart"):
        tp.load_active_for_event(str(tmp_path), event)
    with pytest.raises(tp.StateError, match="SubagentStart"):
        tp.bind_worker_contract_event(str(tmp_path), event)
    assert tp.worker_contract_for_stage(str(tmp_path), stage="pm", task="pm")[
        "contract"]["worker_lifecycle"] == contract["worker_lifecycle"]


@pytest.mark.parametrize("change", [
    {"child": "foreign-child"}, {"parent": "foreign-parent"},
    {"name": "tp_step_product_pm_foreign"}, {"started_at": 9000},
    {"duplicate": True}, {"name": "nested/" + NAME},
])
def test_unmatched_ambiguous_or_old_start_never_binds(tmp_path, change):
    contract = _contract(tmp_path)
    with pytest.raises((tp.StateError, ValueError)):
        tp.bind_worker_contract_event(
            str(tmp_path), _start(tmp_path, _parent(tmp_path, **change)), now=12)
    assert tp.worker_contract_for_stage(str(tmp_path), stage="pm", task="pm")[
        "contract"]["worker_lifecycle"] == contract["worker_lifecycle"]


def test_no_transcript_no_profile_based_guess(tmp_path):
    _contract(tmp_path)
    with pytest.raises((tp.StateError, ValueError)):
        tp.bind_worker_contract_event(str(tmp_path), _start(tmp_path, "missing"), now=12)


def test_foreign_child_stop_does_not_release_owner(tmp_path):
    contract = _contract(tmp_path)
    start = _start(tmp_path, _parent(tmp_path))
    tp.bind_worker_contract_event(str(tmp_path), start, now=12)
    stop = {**start, "hook_event_name": "SubagentStop",
            "agent_transcript_path": str(_child(tmp_path, child="foreign-child"))}
    with pytest.raises((tp.StateError, ValueError)):
        tp.terminalize_worker_contract(
            str(tmp_path), stop, outcome="success", submission_status="valid", now=13)
    assert tp.worker_contract_for_stage(str(tmp_path), stage="pm", task="pm")[
        "contract"]["worker_lifecycle"]["status"] == "active"
    assert tp.load_active(str(tmp_path)) is None
    assert contract["worker_lifecycle"]["status"] == "pending"


def test_child_terminal_usage_prefers_child_over_common_parent_transcript():
    assert spend.event_transcript({
        "hook_event_name": "SubagentStop", "transcript_path": "parent.jsonl",
        "agent_transcript_path": "child.jsonl"}) == "child.jsonl"
    assert spend.event_transcript({
        "hook_event_name": "PreToolUse", "transcript_path": "parent.jsonl",
        "agent_transcript_path": "child.jsonl"}) == "parent.jsonl"


def test_actual_hook_shape_screens_child_and_never_binds_root(tmp_path, monkeypatch, capsys):
    contract = _contract(tmp_path)
    start = _start(tmp_path, _parent(tmp_path))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(start)))
    assert cli.cmd_subagent_start(None) == 0
    assert "Governed subagent lifecycle is active" in capsys.readouterr().out
    action = {"hook_event_name": "PreToolUse", "cwd": str(tmp_path),
              "session_id": "child-1", "turn_id": "child-turn",
              "transcript_path": str(_child(tmp_path)), "tool_name": "Write",
              "tool_input": {"file_path": str(tmp_path / "app.py"), "content": "no"}}
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(action)))
    assert cli.cmd_screen(None) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "block"
    assert tp.load_active(str(tmp_path)) is None
    assert tp.worker_contract_for_stage(str(tmp_path), stage="pm", task="pm")[
        "slot"] == contract["task_slot"]


def test_native_start_replay_is_idempotent_without_another_owner(tmp_path):
    _contract(tmp_path)
    event = _start(tmp_path, _parent(tmp_path))
    first = tp.bind_worker_contract_event(str(tmp_path), event, now=12)
    replay = tp.bind_worker_contract_event(str(tmp_path), event, now=13)
    assert replay == {**first, "replay": True}
