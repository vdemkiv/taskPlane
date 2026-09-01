from __future__ import annotations

import copy
import json
import re

import pytest

from taskplane import loop_status, settings as operational_settings, tp
from taskplane.settings import DEFAULT_SETTINGS_PATH, SettingsError, load_settings


_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _workspace_entries(workspace):
    return sorted(path.relative_to(workspace).as_posix()
                  for path in workspace.rglob("*"))


def test_public_cli_refuses_invalid_settings_before_flow_state_or_artifacts(
        tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    taskplane_home = tmp_path / "taskplane-home"
    monkeypatch.setenv("TASKPLANE_HOME", str(taskplane_home))

    def invalid_settings(*_args, **_kwargs):
        raise SettingsError("invalid canonical settings")

    monkeypatch.setattr(
        operational_settings, "load_settings", invalid_settings)
    result = tp.main([
        "loop", "--workspace", str(workspace), "init", "settings flow",
    ])

    assert result == 1
    assert "operational settings are invalid" in capsys.readouterr().err
    assert _workspace_entries(workspace) == []
    assert not taskplane_home.exists()


def test_dashboard_transition_refuses_invalid_settings_without_artifacts(
        tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transitioned = False

    @loop_status.with_dashboard
    def gate(_workspace):
        nonlocal transitioned
        transitioned = True
        return {"outcome": "success"}

    def invalid_settings(*_args, **_kwargs):
        raise SettingsError("invalid canonical settings")

    monkeypatch.setattr(
        operational_settings, "load_settings", invalid_settings)
    with pytest.raises(SettingsError, match="invalid canonical settings"):
        gate(str(workspace))
    assert transitioned is False
    assert _workspace_entries(workspace) == []


def test_dashboard_refresh_policy_has_one_settings_owner_and_digest(
        tmp_path, monkeypatch):
    settings = load_settings()
    assert settings.dashboard.refresh.session_event == "session_recovery"
    assert settings.dashboard.refresh.replay_on_session_start is True
    assert "gate" in settings.dashboard.refresh.lifecycle_events
    assert "worker_terminal" in settings.dashboard.refresh.lifecycle_events
    assert "terminalize_run" in settings.dashboard.refresh.lifecycle_events
    assert _DIGEST.fullmatch(settings.digest)

    changed = copy.deepcopy(json.loads(DEFAULT_SETTINGS_PATH.read_text(
        encoding="utf-8")))
    changed["dashboard"]["refresh"]["session_event"] = "resume_dashboard"
    changed_path = tmp_path / "settings.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    assert load_settings(changed_path).digest != settings.digest

    missing = copy.deepcopy(changed)
    missing["dashboard"]["refresh"]["lifecycle_events"].remove(
        "worker_terminal")
    missing_path = tmp_path / "missing-dashboard-event.json"
    missing_path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(SettingsError, match="lifecycle_events"):
        load_settings(missing_path)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(loop_status, "_select_dashboard_source", lambda _ws: {
        "mode": "v4", "status": "ready", "run_id": "run-settings",
        "target": "delivery", "revision": "1", "state": {"step": "plan"},
        "source_fingerprint": "f" * 64, "evidence": [],
    })
    publication = loop_status.refresh_dashboard_snapshot(
        str(workspace), event_type="gate", committed_at=1)
    assert publication["snapshot"]["values"]["settings_digest"] == \
        settings.digest
    assert set(publication["surfaces"].values()) == {
        publication["snapshot"]["fingerprint"]}
