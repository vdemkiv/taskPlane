"""Incidental hook cwd cannot erase actual governed execution scope."""
import uuid

import pytest

from taskplane import flow, workflow as w
from taskplane.tests.test_workflow_local import setup, supported_build


def denied(event):
    try:
        result = flow._hook(event)
    except w.Refusal:
        return True
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


@pytest.mark.parametrize("structured", [False, True])
def test_incidental_cwd_still_protects_execution_workspace(tmp_path, monkeypatch, structured):
    work = tmp_path / "governed"
    work.mkdir()
    setup(work)
    incidental = tmp_path / "incidental"
    incidental.mkdir()
    monkeypatch.setattr(flow, "observed_parent", lambda *args: None)
    event = {"hook_event_name": "PreToolUse", "cwd": str(incidental),
             "session_id": "root", "call_id": "fixture-" + uuid.uuid4().hex}
    if structured:
        event.update(tool_name="write_file", tool_input={
            "file_path": str(work / "forbidden.txt"), "content": "not authorized"})
    else:
        event.update(tool_name="exec_command", tool_input={
            "cmd": "touch forbidden.txt", "workdir": str(work)})
    assert denied(event), "Governed execution must not fall back to incidental unselected cwd"
    assert not (work / "forbidden.txt").exists()


@pytest.mark.parametrize("host,tool,key", [("codex", "exec_command", "cmd"), ("claude", "Bash", "command")])
def test_owned_absolute_shell_target_and_conflicting_roots(tmp_path, monkeypatch, host, tool, key):
    work = tmp_path / "governed"
    work.mkdir()
    supported_build(work, host)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(flow, "observed_parent", lambda *args: None)
    event = {"hook_event_name": "PreToolUse", "cwd": str(outside),
             "session_id": "root", "call_id": "fixture-" + uuid.uuid4().hex,
             "tool_name": tool, "tool_input": {key: "touch " + str(work / "app.py")}}
    assert not denied(event)
    other = tmp_path / "other"
    other.mkdir()
    setup(other)
    event["tool_input"][key] += " " + str(other / "product.json")
    assert denied(event)


def test_nested_execution_read_remains_bound_to_run(tmp_path):
    from taskplane import runtime_command
    setup(tmp_path)
    child = tmp_path / "nested"
    child.mkdir()
    event = {"tool_name": "exec_command", "cwd": str(child), "tool_input": {"cmd": "git status --short"}}
    assert runtime_command.hook_workspace(event, child) == tmp_path
