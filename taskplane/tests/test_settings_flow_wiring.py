from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from taskplane import loop_status
from taskplane.settings import DEFAULT_SETTINGS_PATH, SettingsError, load_settings


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = json.loads((ROOT / "taskplane" / "settings_inventory.json").read_text(
    encoding="utf-8"))
FIXTURES = Path(__file__).parent / "fixtures" / "settings-inventory"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _surface_paths(pattern: str) -> list[Path]:
    if any(char in pattern for char in "*?["):
        return [path for path in ROOT.glob(pattern) if path.is_file()]
    path = ROOT / pattern
    return [path] if path.is_file() else []


def _surface_text(consumer: dict) -> str:
    paths: list[Path] = []
    for pattern in consumer["surfaces"]:
        paths.extend(_surface_paths(pattern))
    assert paths, consumer["id"]
    return "\n".join(path.read_text(encoding="utf-8", errors="replace")
                     for path in sorted(set(paths)))


def _flow_binding_violation(value: dict) -> bool:
    settings = value.get("settings")
    return not isinstance(settings, dict) or settings != {
        "source": "taskplane/operational-settings.json",
        "loader": "taskplane.settings.load_settings",
        "binding": "settings_digest",
    }


def test_every_flow_initializes_from_canonical_settings():
    assert INVENTORY["authority"]["canonical_source"] == \
        "taskplane/operational-settings.json"
    expected_consumers = {
        "cli-and-loop", "preflight-storage-handoff",
        "hooks-agents-skills-flows", "workflow-transport-and-dispatch",
        "dashboard-state-projection-delivery-host",
        "ci-test-selection-sharding", "cleanup", "metrics",
        "release-and-packages", "legacy-and-cache",
        "tests-fixtures-generators-graph-excluded",
        "repository-docs-and-release-tooling",
    }
    consumers = {row["id"]: row for row in INVENTORY["consumers"]}
    assert set(consumers) == expected_consumers
    assert all(row["initialization"] and row["binding"]
               for row in consumers.values())
    production_consumers = [
        row for row in consumers.values()
        if row["initialization"] != "non-authoritative-test-input"
    ]
    for consumer in production_consumers:
        text = _surface_text(consumer)
        assert all(marker in text for marker in consumer["proof_markers"]), \
            consumer["id"]
    excluded = consumers["tests-fixtures-generators-graph-excluded"]
    assert excluded["binding"] == "negative mutation evidence"

    flow_paths = sorted((ROOT / "skills").glob("*/flow.json"))
    assert flow_paths
    for path in flow_paths:
        assert not _flow_binding_violation(json.loads(
            path.read_text(encoding="utf-8"))), path

    tp_source = (ROOT / "taskplane" / "tp.py").read_text(encoding="utf-8")
    assert tp_source.index(
        "operational_settings.load_settings(environment=os.environ)") < \
        tp_source.index("argparse.ArgumentParser(prog=\"tp.py\")")
    loop_source = (ROOT / "taskplane" / "loop_status.py").read_text(
        encoding="utf-8")
    wrapped = loop_source[loop_source.index("def with_dashboard(fn):"):]
    assert wrapped.index("settings = operational_settings.load_settings()") < \
        wrapped.index("result = fn(ws, *args, **kwargs)")
    dispatch = (ROOT / "taskplane" / "taskplane_lite.py").read_text(
        encoding="utf-8")
    assert "settings_digest=settings.digest" in dispatch

    workflow_paths = sorted((ROOT / "workflows").glob("*-wave.js"))
    assert {path.name for path in workflow_paths} == {
        "execute-wave.js", "evaluate-wave.js", "fix-wave.js",
        "review-wave.js"}
    for path in workflow_paths:
        source = path.read_text(encoding="utf-8")
        assert "requireSettings(args)" in source, path
        assert "settings_digest" in source, path
        assert "settings_digest: settings.digest" in source, path
        assert not re.search(r"maxAttempts\s*:[^\n]+\|\|\s*\d+", source), path

    bad_flow = json.loads((FIXTURES / "missing-digest-flow.json").read_text(
        encoding="utf-8"))
    assert _flow_binding_violation(bad_flow)
    graph_excluded = (FIXTURES / "graph-excluded-generator.py.txt").read_text(
        encoding="utf-8")
    assert "TASKPLANE_SUITE_CACHE_MAX_AGE" in graph_excluded
    assert "TASKPLANE_SUITE_CACHE_MAX_AGE" in \
        INVENTORY["prohibited_direct_environment"]
    indirect = (FIXTURES / "indirect-mapping-environment.py.txt").read_text(
        encoding="utf-8")
    assert "environment.get(\"TASKPLANE_OBLIGATIONS\"" in indirect
    js_default = (FIXTURES / "workflow-js-default.js.txt").read_text(
        encoding="utf-8")
    assert re.search(r"maxAttempts\s*:[^\n]+\|\|\s*\d+", js_default)


def test_dashboard_refresh_policy_has_one_settings_owner_and_digest(
        tmp_path, monkeypatch):
    settings = load_settings()
    assert settings.dashboard.refresh.session_event == "session_recovery"
    assert settings.dashboard.refresh.replay_on_session_start is True
    assert "gate" in settings.dashboard.refresh.lifecycle_events
    assert "worker_terminal" in settings.dashboard.refresh.lifecycle_events
    assert _DIGEST.fullmatch(settings.digest)

    host_contract = json.loads((ROOT / "hooks" / "host-native.json").read_text(
        encoding="utf-8"))
    recovery = host_contract["sessionRecovery"]
    assert recovery == {
        "callback": "taskplane.loop_status.refresh_dashboard_snapshot",
        "settingsKey": "dashboard.refresh",
    }
    runtime = (ROOT / "hooks" / "host_native_runtime.py").read_text(
        encoding="utf-8")
    function = runtime[runtime.index("def recover_session_dashboard("):
                       runtime.index("def _main(")]
    assert "settings.dashboard.refresh" in function
    assert 'event_type="session_recovery"' not in function
    assert "replay=True" not in function

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

    stale = json.loads((FIXTURES / "stale-dashboard-refresh.json").read_text(
        encoding="utf-8"))
    assert "eventType" in stale["sessionRecovery"]
    assert "replay" in stale["sessionRecovery"]
    worker_source = (ROOT / "taskplane" / "taskplane_lite.py").read_text(
        encoding="utf-8")
    lifecycle = worker_source[
        worker_source.index("def _refresh_dashboard_lifecycle("):
        worker_source.index("def record_worker_terminal(")]
    assert "settings.dashboard.refresh.lifecycle_events" in lifecycle

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
