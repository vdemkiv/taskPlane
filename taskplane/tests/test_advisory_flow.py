"""Behavioral checks for observation without delivery vetoes."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from taskplane import flow, live_telemetry
from taskplane.tests.test_native_session_meter import _write_segment


ROOT = Path(__file__).resolve().parents[2]


def start(workspace, session="root"):
    from taskplane.tests.test_workflow_local import setup
    return setup(workspace)[1]


def event(workspace, transcript=None, **values):
    return {"cwd": str(workspace), "session_id": "root", "hook_event_name": "PreToolUse",
            "tool_name": "exec_command", "tool_input": {"cmd": "private command"},
            "transcript_path": str(transcript) if transcript else None, **values}


def observe(state, payload):
    return flow._observe_hook({**payload, '_telemetry_state': state})


def test_native_counters_are_deltas_and_private_content_is_not_recorded(tmp_path):
    run = start(tmp_path)
    transcript = tmp_path / "native.jsonl"
    _write_segment(transcript, session_id="root", total=1000, cached=100, output=100)
    assert observe(run, event(tmp_path, transcript, tool_use_id="a")) == {}
    _write_segment(transcript, session_id="root", total=1400, cached=200, output=150)
    observe(run, event(tmp_path, transcript, tool_use_id="b"))
    observe(run, event(tmp_path, transcript, tool_use_id="b"))
    result = live_telemetry.summary(live_telemetry.read(tmp_path, run), run)
    assert result["tokens"]["total_tokens"] == 400
    assert result["tokens"]["cached_input_tokens"] == 100
    assert result["tool_calls"] == 2
    journal = live_telemetry.path(tmp_path, run["run"]).read_text()
    assert "private command" not in journal and "private conversation" not in journal


def test_missing_usage_is_unknown_and_repetition_is_only_advice(tmp_path):
    run = start(tmp_path)
    result = {}
    for i in range(4):
        result = observe(run, event(tmp_path, tool_use_id=str(i)))
    assert "advisory" in result["hookSpecificOutput"]["additionalContext"]
    assert "permissionDecision" not in json.dumps(result)
    report = live_telemetry.summary(live_telemetry.read(tmp_path, run), run)
    assert report["tokens"] is None
    assert report["token_coverage"]["unmeasured_sessions"] == 1


def test_unselected_hooks_do_not_create_observations(tmp_path):
    assert flow._observe_hook(event(tmp_path, session_id="unrelated")) == {}
    assert not (tmp_path / flow.JOURNAL).exists()
    assert not list((tmp_path / '.taskplane').glob('live-telemetry-*.json'))


def test_child_counter_attaches_to_parent_flow(tmp_path):
    run = start(tmp_path)
    transcript = tmp_path / "child.jsonl"
    _write_segment(transcript, session_id="child", parent="root", total=500, output=50)
    observe(run, event(tmp_path, transcript, session_id="child"))
    _write_segment(transcript, session_id="child", parent="root", total=900, output=80)
    observe(run, event(tmp_path, transcript, session_id="child"))
    report = live_telemetry.summary(live_telemetry.read(tmp_path, run), run)
    assert report["tokens"]["total_tokens"] == 400
    assert report["token_coverage"]["unmeasured_sessions"] == 1


def test_malformed_events_and_storage_errors_never_block(tmp_path, capsys):
    with patch("sys.stdin", io.StringIO("not json")):
        assert flow.run_hook() == 2
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    start(tmp_path)
    with patch("sys.stdin", io.StringIO(json.dumps(event(tmp_path, hook_event_name="PostToolUse")))), \
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


def test_progress_is_observation_and_cannot_accept_a_stage(tmp_path, capsys):
    from taskplane import workflow
    from taskplane.tests.test_workflow_local import setup
    controller, state = setup(tmp_path)
    with patch.dict(os.environ, {"CODEX_THREAD_ID": "root"}):
        assert flow.main(["progress", "--phase", "product", "--note", "work produced",
                          "--workspace", str(tmp_path)], governor=controller) == 0
        report = json.loads(capsys.readouterr().out)
        assert workflow.current(report['workflow'])['decision'] == 'not_requested'
        assert report['run'] == state['run']
        for action in (["progress", "--phase", "build"], ["finish"]):
            assert flow.main([*action, "--workspace", str(tmp_path), '--expected-revision', str(state['revision'])], governor=controller) == 2
            assert json.loads(capsys.readouterr().out)["reason"] in {"approval_required", "state_unavailable"}
    assert not any(row["kind"] == "finish" for row in flow.read_events(tmp_path))


def test_large_usage_and_activity_only_generate_advice(tmp_path):
    run = start(tmp_path)
    transcript = tmp_path / "native.jsonl"
    _write_segment(transcript, session_id="root", total=1000, output=100)
    observe(run, event(tmp_path, transcript, tool_use_id="baseline"))
    _write_segment(transcript, session_id="root", total=2_000_000, output=100_000)
    for i in range(25):
        response = observe(run, event(tmp_path, transcript, tool_use_id=str(i)))
        assert "permissionDecision" not in json.dumps(response)
        assert response.get("decision") != "block"
    report = live_telemetry.summary(live_telemetry.read(tmp_path, run), run)
    assert report["tokens"]["total_tokens"] == 1_999_000
    assert len(report["advice"]) == 3
