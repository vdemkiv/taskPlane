"""Native session boundaries using isolated stores and simulated host records."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import codex_identity, loop, storage, taskplane_lite
from taskplane.tests.phase_fixture import _normal_phase_workspace, _emit_host_hook
from taskplane.tests.test_native_terminal_telemetry import _write_codex_transcript


PARENT = "01a07d5a-864e-7e23-913d-42bb850e742b"
CHILD = "01a07da3-a886-7261-aae9-1126caff4b6c"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def phase_child(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", PARENT)
    monkeypatch.delenv("TASKPLANE_TASK", raising=False)
    taskplane_lite.record_entry_tools(["Read", "Grep", "Glob", "Write", "Edit"])
    ws, store, stage, artifacts, route, authorize = _normal_phase_workspace(tmp_path, monkeypatch)
    action = loop.next_action(ws)
    assert action["obligations"]["dispatch_allowed"], json.dumps({k: v for k, v in action["obligations"].items() if k in {"error", "reason", "reason_code", "awaiting", "status"}})
    home = tmp_path / "native-host"
    path = home / "sessions" / datetime.now(timezone.utc).strftime("%Y/%m/%d") / f"rollout-test-{CHILD}.jsonl"
    path.parent.mkdir(parents=True)
    _write_codex_transcript(path, label="phase-child", input_tokens=10,
        cached_tokens=0, output_tokens=0, event={"agent_id": CHILD,
            "session_id": PARENT, "cwd": ws, "task_name": action["obligations"]["task_name"]})
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert _emit_host_hook(ws, action, "SubagentStart", monkeypatch,
        session_id=PARENT, agent_id=CHILD, agent_transcript_path=str(path)) == 0
    return ws, store, stage, action


def test_native_child_cli_reads_parent_stage_without_changing_host_identity(phase_child, monkeypatch):
    ws, store, stage, action = phase_child
    monkeypatch.setenv("CODEX_THREAD_ID", CHILD)
    assert storage.load_workspace_locator(ws) is None
    before = dict(os.environ)
    with codex_identity.bind_command(ws):
        assert storage.load_workspace_locator(ws)["run_id"] == stage["run_id"]
        assert taskplane_lite.task_slot() == action["obligations"]["contract_bootstrap"]["environment"]["TASKPLANE_TASK"]
        assert os.environ["CODEX_THREAD_ID"] == CHILD
        assert loop.phase_harness.read_input(loop, ws, action["stage_runtime_dispatch"])["run_id"] == stage["run_id"]
    assert dict(os.environ) == before
    assert storage.load_workspace_locator(ws) is None
    # Exercise the same CLI entry as the real failed child, with no prompt-set slot.
    result = subprocess.run([sys.executable, str(ROOT / "taskplane/tp.py"),
        "stage", "read-input", "--request", "-", "--workspace", ws],
        input=json.dumps(action["stage_runtime_dispatch"]), cwd=ws,
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["run_id"] == stage["run_id"]


@pytest.mark.parametrize("damage", ["wrong-slot", "unbound", "bad-signature", "other-workspace"])
def test_child_binding_cannot_adopt_unrelated_authority(phase_child, monkeypatch, tmp_path, damage):
    ws, store, stage, action = phase_child
    slot = action["obligations"]["contract_bootstrap"]["environment"]["TASKPLANE_TASK"]
    path = Path(taskplane_lite.active_contract_path(ws, slot))
    contract = json.loads(path.read_text())
    if damage == "unbound":
        contract["worker_lifecycle"]["status"] = "pending"
        contract["worker_lifecycle"]["owner"] = None
    if damage == "bad-signature":
        contract["worker_lifecycle"]["release_action"]["signature"] = "0" * 64
    path.write_text(json.dumps(contract))
    if damage == "wrong-slot":
        monkeypatch.setenv("TASKPLANE_TASK", "task_foreign")
    monkeypatch.setenv("CODEX_THREAD_ID", CHILD)
    if damage == "other-workspace":
        # Metadata mismatch never selects the parent's namespace.
        with codex_identity.bind_command(str(tmp_path / "other")):
            assert codex_identity.command_worker() is None
    else:
        with pytest.raises((ValueError, taskplane_lite.StateError)):
            with codex_identity.bind_command(ws):
                pytest.fail("admitted foreign worker authority")
        result = subprocess.run([sys.executable, str(ROOT / "taskplane/tp.py"),
            "stage", "read-input", "--request", "-", "--workspace", ws],
            input=json.dumps(action["stage_runtime_dispatch"]), cwd=ws,
            text=True, capture_output=True, timeout=30)
        assert result.returncode == 1 and "Traceback" not in result.stderr
        assert json.loads(result.stdout)["dispatch_allowed"] is False
    assert codex_identity.command_worker() is None
    assert os.environ["CODEX_THREAD_ID"] == CHILD


@pytest.mark.parametrize("contents", [None, "{broken-json", "[]", "{}"])
def test_stopped_child_without_valid_output_is_released_and_can_retry(phase_child, monkeypatch, contents):
    ws, store, stage, action = phase_child
    operation = action["obligations"]["phase_operation"]
    state = loop._load_raw(ws)
    context = loop._phase_bridge_context(ws, state)
    candidate = context["configuration"]["candidate_fingerprint"]
    actor = state["_stage_native_root_authority"]["actor"]
    # Human retry must not replace an active native child.
    assert "reconcile" in loop.resolve(ws, "retry", by=actor, phase_operation=operation,
        candidate_fingerprint=candidate, worker_stopped=True)["error"]
    output = Path(ws) / "specs/requirement.json"
    if contents is not None:
        output.parent.mkdir(exist_ok=True)
        output.write_text(contents)
    native_path = next((Path(os.environ["CODEX_HOME"]) / "sessions").glob(f"*/*/*/*-{CHILD}.jsonl"))
    _write_codex_transcript(native_path, label="phase-child", input_tokens=100,
        cached_tokens=0, output_tokens=10, event={"agent_id": CHILD,
            "session_id": PARENT, "cwd": ws, "task_name": action["obligations"]["task_name"]})
    assert _emit_host_hook(ws, action, "SubagentStop", monkeypatch,
        session_id=PARENT, agent_id=CHILD, agent_transcript_path=str(native_path),
        omit_lens_results=True) == 2  # Missing semantic output is still a refusal.
    result = loop.resolve(ws, "reconcile", phase_operation=operation)
    assert result["reason_code"] == "phase_candidate_unavailable", result
    assert result["accepted"] is False and result["worker_released"] is True
    assert loop._load_raw(ws)["step"] == "pm"
    assert operation + "-complete" not in store.load(stage["run_id"])["phase_records"]
    slot = action["obligations"]["contract_bootstrap"]["environment"]["TASKPLANE_TASK"]
    assert not Path(taskplane_lite.active_contract_path(ws, slot)).exists()
    assert taskplane_lite.released_worker_contract(ws, slot)["worker_lifecycle"]["owner"]["agent_id"] == CHILD
    monkeypatch.setenv("CODEX_THREAD_ID", CHILD)
    with pytest.raises(ValueError, match="no active"):
        with codex_identity.bind_command(ws):
            pytest.fail("retired child inherited its parent's run")
    monkeypatch.setenv("CODEX_THREAD_ID", PARENT)
    assert loop.resolve(ws, "retry", by="human:other", phase_operation=operation,
        candidate_fingerprint=candidate, worker_stopped=True).get("error")
    retried = loop.resolve(ws, "retry", by=actor, phase_operation=operation,
        candidate_fingerprint=candidate, worker_stopped=True)
    assert retried.get("resolved") == "retry", retried
    replay = loop.resolve(ws, "retry", by=actor, phase_operation=operation,
        candidate_fingerprint=candidate, worker_stopped=True)
    assert replay["replay"] is True
    fresh = loop.next_action(ws)
    assert fresh["obligations"]["dispatch_allowed"], fresh
    assert fresh["obligations"]["phase_operation"] != operation
    assert loop._load_raw(ws)["step"] == "pm"
