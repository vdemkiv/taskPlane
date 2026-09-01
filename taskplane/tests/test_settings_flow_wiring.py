from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from taskplane import loop_status
from taskplane.settings import DEFAULT_SETTINGS_PATH, SettingsError, load_settings


ROOT = Path(__file__).resolve().parents[2]
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _flow_binding_violation(value: dict) -> bool:
    settings = value.get("settings")
    return not isinstance(settings, dict) or settings != {
        "source": "taskplane/operational-settings.json",
        "loader": "taskplane.settings.load_settings",
        "binding": "settings_digest",
    }


def test_every_flow_initializes_from_canonical_settings(monkeypatch):
    effective = load_settings(environment={})
    assert _DIGEST.fullmatch(effective.digest)
    flow_paths = sorted((ROOT / "skills").glob("*/flow.json"))
    assert flow_paths
    for path in flow_paths:
        assert not _flow_binding_violation(json.loads(
            path.read_text(encoding="utf-8"))), path

    transitioned = False

    @loop_status.with_dashboard
    def gate(_workspace):
        nonlocal transitioned
        transitioned = True
        return {"outcome": "success"}

    def invalid_settings():
        raise SettingsError("invalid canonical settings")

    monkeypatch.setattr(
        loop_status.operational_settings, "load_settings", invalid_settings)
    with pytest.raises(SettingsError, match="invalid canonical settings"):
        gate("unused-workspace")
    assert transitioned is False


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
