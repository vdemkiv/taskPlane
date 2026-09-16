"""Behavioral checks for observation without delivery vetoes."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from taskplane import flow
from taskplane.tests.test_native_session_meter import _write_segment


ROOT = Path(__file__).resolve().parents[2]


def start(workspace, session="root"):
    row = {"kind": "start", "run": "run-1", "session": session, "goal": "Ship a fix"}
    flow.append(workspace, row)
    return row


def event(workspace, transcript=None, **values):
    return {"cwd": str(workspace), "session_id": "root", "hook_event_name": "PreToolUse",
            "tool_name": "exec_command", "tool_input": {"cmd": "private command"},
            "transcript_path": str(transcript) if transcript else None, **values}


def test_native_counters_are_deltas_and_private_content_is_not_recorded(tmp_path):
    run = start(tmp_path)
    transcript = tmp_path / "native.jsonl"
    _write_segment(transcript, session_id="root", total=1000, cached=100, output=100)
    assert flow.hook(event(tmp_path, transcript, tool_use_id="a")) == {}
    _write_segment(transcript, session_id="root", total=1400, cached=200, output=150)
    flow.hook(event(tmp_path, transcript, tool_use_id="b"))
    flow.hook(event(tmp_path, transcript, tool_use_id="b"))
    result = flow.summarize(flow.read_events(tmp_path), run)
    assert result["tokens"]["total_tokens"] == 400
    assert result["tokens"]["cached_input_tokens"] == 100
    assert result["tool_calls"] == 2
    journal = (tmp_path / flow.JOURNAL).read_text()
    assert "private command" not in journal and "private conversation" not in journal


def test_missing_usage_is_unknown_and_repetition_is_only_advice(tmp_path):
    run = start(tmp_path)
    result = {}
    for i in range(4):
        result = flow.hook(event(tmp_path, tool_use_id=str(i)))
    assert "advisory" in result["hookSpecificOutput"]["additionalContext"]
    assert "permissionDecision" not in json.dumps(result)
    report = flow.summarize(flow.read_events(tmp_path), run)
    assert report["tokens"] is None
    assert report["token_coverage"]["unmeasured_sessions"] == 1


def test_unrelated_sessions_and_finished_runs_are_inert(tmp_path):
    start(tmp_path)
    assert flow.hook(event(tmp_path, session_id="unrelated")) == {}
    assert len(flow.read_events(tmp_path)) == 1
    flow.append(tmp_path, {"kind": "finish", "run": "run-1", "session": "root"})
    assert flow.hook(event(tmp_path)) == {}
    assert len(flow.read_events(tmp_path)) == 2


def test_child_counter_attaches_to_parent_flow(tmp_path):
    run = start(tmp_path)
    transcript = tmp_path / "child.jsonl"
    _write_segment(transcript, session_id="child", parent="root", total=500, output=50)
    flow.hook(event(tmp_path, transcript, session_id="child"))
    _write_segment(transcript, session_id="child", parent="root", total=900, output=80)
    flow.hook(event(tmp_path, transcript, session_id="child"))
    report = flow.summarize(flow.read_events(tmp_path), run)
    assert report["tokens"]["total_tokens"] == 400
    assert report["token_coverage"]["unmeasured_sessions"] == 1


def test_malformed_events_and_storage_errors_never_block(tmp_path, capsys):
    with patch("sys.stdin", io.StringIO("not json")):
        assert flow.run_hook() == 0
    assert json.loads(capsys.readouterr().out) == {}
    start(tmp_path)
    with patch("sys.stdin", io.StringIO(json.dumps(event(tmp_path)))), \
            patch.object(flow, "append", side_effect=PermissionError):
        assert flow.run_hook() == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_installed_hook_commands_abstain_without_workspace_setup(tmp_path):
    manifest = json.loads((ROOT / "hooks/hooks.json").read_text())
    for rows in manifest["hooks"].values():
        for row in rows:
            command = row["hooks"][0]["commandWindows" if os.name == "nt" else "command"]
            result = subprocess.run(command, shell=True, input=json.dumps(event(tmp_path)),
                                    cwd=tmp_path, capture_output=True, text=True,
                                    env={**os.environ, "PLUGIN_ROOT": str(ROOT)})
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {}


def test_progress_drives_report_without_gate_or_token_ceiling(tmp_path, capsys):
    with patch.dict(os.environ, {"CODEX_THREAD_ID": "root"}):
        for args in (["start", "--goal", "deliver"],
                     ["progress", "--phase", "build", "--note", "implementation ready"],
                     ["finish", "--note", "tests passed"]):
            assert flow.main([*args, "--workspace", str(tmp_path)]) == 0
            report = json.loads(capsys.readouterr().out)
    assert report["status"] == "finished"
    assert report["phase"] == "build"
    assert report["owner"] == "orchestrator"
    assert report["milestones"][0]["note"] == "implementation ready"
    assert report["outcome"] == "tests passed"


def test_large_usage_and_activity_only_generate_advice(tmp_path):
    run = start(tmp_path)
    transcript = tmp_path / "native.jsonl"
    _write_segment(transcript, session_id="root", total=1000, output=100)
    flow.hook(event(tmp_path, transcript, tool_use_id="baseline"))
    _write_segment(transcript, session_id="root", total=2_000_000, output=100_000)
    for i in range(25):
        response = flow.hook(event(tmp_path, transcript, tool_use_id=str(i)))
        assert "permissionDecision" not in json.dumps(response)
        assert response.get("decision") != "block"
    report = flow.summarize(flow.read_events(tmp_path), run)
    assert report["tokens"]["total_tokens"] == 1_999_000
    assert len(report["advice"]) == 3
