import json

from taskplane.settings import load_settings


def test_legacy_and_version_forms_migrate_without_second_authority(tmp_path):
    legacy = {
        "version": 0,
        "stage_models": {"build": "inherit"},
        "stage_reasoning": {"build": "high"},
        "lens_routes": {"product": ["product"], "build": []},
        "lens_counts": {"product": 1, "build": 0},
        "build_shards": 1,
        "build_concurrency": "native",
        "test_backend": "pytest",
        "test_selection": "targeted",
        "test_shards": 1,
        "test_cache": True,
        "timeouts": {"task_seconds": 600},
        "budgets": {"max_actions": 60},
        "workflow_transport": "native",
        "worker_inheritance": {"model": True, "reasoning": True},
        "cleanup": {"worktrees": "after-merge", "artifacts_days": 30},
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    migrated = load_settings(path)

    assert migrated.schema == "taskplane.operational-settings/v1"
    assert migrated.build.concurrency == "native"
    assert migrated.receipt["migration"] == {
        "from": "taskplane.operational-settings/v0", "to": migrated.schema}
    assert migrated.receipt["overlay"] is None
