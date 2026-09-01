import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from taskplane.settings import (
    DEFAULT_SETTINGS_PATH,
    SettingsError,
    load_settings,
)


def _write(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_valid_canonical_settings_load_typed():
    settings = load_settings(DEFAULT_SETTINGS_PATH)

    assert settings.schema == "taskplane.operational-settings/v1"
    assert settings.stages["build"].reasoning == "high"
    assert settings.lenses.counts["build"] == 0
    assert settings.build.shards == 1
    assert settings.tests.backend == "ci"
    assert settings.tests.shards == 1
    assert settings.lenses.counts["plan"] == 3
    assert settings.tests.cache is True
    assert settings.tests.cache_max_age_seconds == 24 * 60 * 60
    assert settings.limits.budgets["lens_deep_max_actions"] == 45
    assert settings.limits.budgets["lens_sweep_max_actions"] == 30
    assert settings.limits.timeouts["lens_wait_seconds"] == 1800
    assert settings.limits.timeouts["lens_minimum_wait_seconds"] == 300
    assert settings.limits.timeouts["subprocess_seconds"] == 300
    assert settings.workflow.transport == "native"
    assert len(settings.digest) == 64
    with pytest.raises(FrozenInstanceError):
        settings.build.shards = 2
    with pytest.raises(TypeError):
        settings.stages["build"] = settings.stages["plan"]


def test_invalid_or_unknown_settings_fail_closed(tmp_path):
    canonical = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))

    malformed = dict(canonical)
    malformed["unexpected"] = True
    with pytest.raises(SettingsError, match="unknown"):
        load_settings(_write(tmp_path, malformed))

    unsafe = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    unsafe["lenses"]["counts"]["build"] = 1
    with pytest.raises(SettingsError, match="zero lens"):
        load_settings(_write(tmp_path, unsafe))

    conflicting = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    conflicting["workflow"]["transport"] = "local-scheduler"
    with pytest.raises(SettingsError, match="native"):
        load_settings(_write(tmp_path, conflicting))

    invalid_wait = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    invalid_wait["limits"]["timeouts"]["lens_minimum_wait_seconds"] = 2000
    with pytest.raises(SettingsError, match="cannot exceed"):
        load_settings(_write(tmp_path, invalid_wait))

    invalid_cache_age = json.loads(
        DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    invalid_cache_age["tests"]["cache_max_age_seconds"] = -1
    with pytest.raises(SettingsError, match="finite number"):
        load_settings(_write(tmp_path, invalid_cache_age))


def test_precedence_migration_and_safe_override_contract(tmp_path):
    base = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    base["tests"]["selection"] = "affected"
    path = _write(tmp_path, base)

    effective = load_settings(
        path, overlay={"tests": {"selection": "targeted", "cache": False}})
    assert effective.tests.selection == "targeted"
    assert effective.tests.cache is False
    assert effective.receipt["precedence"] == ["defaults", "file", "overlay"]
    assert effective.receipt["overlay"]["applied"] == [
        "tests.cache", "tests.selection"]

    safer_cache = load_settings(
        path, overlay={"tests": {"cache_max_age_seconds": 0}})
    assert safer_cache.tests.cache_max_age_seconds == 0

    with pytest.raises(SettingsError, match="exact authority"):
        load_settings(path, overlay={"lenses": {"counts": {"product": 0}}})

    authority = {
        "schema": "taskplane.human-decision/v1",
        "authorized": True,
        "authority_requested": "gate_weakening",
        "actor": "human:operator",
        "thread": "thread-1",
        "revision": "rev-1",
    }
    weakened = load_settings(
        path, overlay={"lenses": {"counts": {"product": 0}}},
        authority=authority)
    assert weakened.lenses.counts["product"] == 0
    assert weakened.receipt["overlay"]["authority_fingerprint"]

    with pytest.raises(SettingsError, match="exact authority"):
        load_settings(path, overlay={
            "tests": {"cache_max_age_seconds": 2 * 24 * 60 * 60}})
    extended = load_settings(
        path, overlay={
            "tests": {"cache_max_age_seconds": 2 * 24 * 60 * 60}},
        authority=authority)
    assert extended.tests.cache_max_age_seconds == 2 * 24 * 60 * 60

    custom = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    custom["tests"]["backend"] = "local"
    custom["tests"]["cache_max_age_seconds"] = 3600
    custom_path = _write(tmp_path, custom)
    with pytest.raises(SettingsError, match="exact authority"):
        load_settings(custom_path, overlay={"tests": {"backend": "ci"}})
    with pytest.raises(SettingsError, match="exact authority"):
        load_settings(custom_path, overlay={
            "tests": {"cache_max_age_seconds": 7200}})


def test_one_release_cache_environment_aliases_are_safe_and_receipted():
    disabled = load_settings(environment={"TASKPLANE_NO_SUITE_CACHE": "1"})
    assert disabled.tests.cache is False
    assert disabled.receipt["environment"]["applied"] == ["tests.cache"]
    assert disabled.receipt["environment"]["adapter"] == \
        "legacy-environment/v1"

    shorter = load_settings(environment={
        "TASKPLANE_SUITE_CACHE_MAX_AGE": "3600"})
    assert shorter.tests.cache_max_age_seconds == 3600
    assert shorter.receipt["environment"]["authority_fingerprint"] is None

    with pytest.raises(SettingsError, match="exact authority"):
        load_settings(environment={
            "TASKPLANE_SUITE_CACHE_MAX_AGE": "172800"})
    with pytest.raises(SettingsError, match="finite number"):
        load_settings(environment={
            "TASKPLANE_SUITE_CACHE_MAX_AGE": "not-a-number"})
