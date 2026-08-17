import json
from pathlib import Path

import evidence
import loop
import runtime_eval


def test_wave_suppresses_child_success_and_emits_one_aggregate():
    wave = loop.command_wave_create("wave-1", ["a", "b", "c"])
    assert loop.command_wave_update(wave, "a", "succeeded") == []
    assert loop.command_wave_update(wave, "b", "succeeded") == []
    events = loop.command_wave_update(wave, "c", "succeeded")
    assert [event["state"] for event in events] == ["wave_completed"]
    assert loop.command_wave_update(wave, "c", "succeeded") == []
    assert wave["ordinary_completion_deliveries"] == 1


def test_attention_remains_visible_after_terminal_completion():
    wave = loop.command_wave_create("wave-1", ["a"])
    assert [e["state"] for e in loop.command_wave_update(
        wave, "a", "succeeded")] == ["wave_completed"]
    events = loop.command_wave_update(wave, "a", "approval_required")
    assert [event["state"] for event in events] == ["approval_required"]
    assert wave["members"]["a"] == "succeeded"
    assert loop.command_wave_update(wave, "a", "approval_required") == []


def test_resume_reuses_bound_handles_and_preserves_interruption():
    wave = loop.command_wave_create(
        "wave-1", ["a", "b"], handles={"a": "handle-a", "b": "handle-b"})
    wave["interrupted"] = True
    encoded = json.loads(json.dumps(wave))
    resumed = loop.command_wave_resume(encoded, ["a", "b"])
    assert resumed["handles"] == {"a": "handle-a", "b": "handle-b"}
    assert resumed["interrupted"] is True
    assert resumed["launches"] == 2


def test_runtime_projection_is_bounded_and_never_manufactures_measurement():
    wave = loop.command_wave_create("wave-1", ["a"])
    projection = runtime_eval.command_wave_projection(
        wave, efficiency={"launches": 1, "model_wakes": 0,
                          "unchanged_model_polls": 0,
                          "polling_raw_tokens": 0,
                          "total_raw_tokens": None},
        artifacts=[{"path": "x" * 9000, "sha256": "abc", "bytes": 5}])
    assert projection["efficiency"]["measurement_status"] == "unproven"
    assert projection["efficiency"]["polling_raw_token_share"] is None
    assert len(projection["artifacts"][0]["path"].encode()) <= 512


def test_evidence_projects_existing_command_wave_only(monkeypatch):
    state = {"command_wave": loop.command_wave_create("wave-1", ["a"])}
    assert evidence.command_wave_evidence(state)["schema"] == \
        "taskplane.command-wave-evidence/v1"
    assert evidence.command_wave_evidence({}) is None
