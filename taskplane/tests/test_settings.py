import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from taskplane.settings import (
    DEFAULT_SETTINGS_PATH,
    STAGES,
    SettingsError,
    load_settings,
)


def _write(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_valid_canonical_settings_load_typed():
    settings = load_settings(DEFAULT_SETTINGS_PATH)

    assert settings.schema == "taskplane.operational-settings/v2"
    assert settings.stages["build"].reasoning == "high"
    assert settings.lenses.counts["build"] == 0
    assert settings.build.shards == 1
    assert settings.tests.backend == "ci"
    assert settings.tests.shards == 1
    assert settings.lenses.routing["plan"] == (
        "architecture", "project-management", "testability")
    assert settings.lenses.counts["plan"] == 4
    assert settings.lenses.policy_for("plan").dynamic is True
    assert settings.lenses.policy_for("product").to_dict() == {
        "stage": "product", "mandatory": ["product"], "max_count": 3,
        "dynamic": True,
    }
    assert settings.stages["engineering"].reasoning == "high"
    assert settings.lenses.policy_for("engineering").to_dict() == {
        "stage": "engineering", "mandatory": [], "max_count": 0,
        "dynamic": False,
    }
    assert settings.tests.cache is True
    assert settings.tests.cache_max_age_seconds == 24 * 60 * 60
    assert settings.limits.timeouts["task_seconds"] == 1200
    assert settings.limits.budgets["lens_deep_max_actions"] == 45
    assert settings.limits.budgets["lens_sweep_max_actions"] == 30
    assert settings.limits.budgets["target_tokens"] == 12_000_000
    assert settings.limits.budgets["max_tokens"] == 17_000_000
    assert settings.limits.timeouts["lens_wait_seconds"] == 1800
    assert settings.limits.timeouts["lens_minimum_wait_seconds"] == 300
    assert settings.limits.timeouts["subprocess_seconds"] == 300
    assert settings.workflow.transport == "native"
    assert settings.workflow.worker_inheritance["context"] == "none"
    assert len(settings.digest) == 64
    with pytest.raises(FrozenInstanceError):
        settings.build.shards = 2
    with pytest.raises(TypeError):
        settings.stages["build"] = settings.stages["plan"]


def test_root_session_settings_are_required_exact_and_shipped_values_load(
        tmp_path):
    settings = load_settings(DEFAULT_SETTINGS_PATH)

    assert settings.workflow.root_session.to_dict() == {
        "resume": "forbidden",
        "seed": "digest-only",
        "seed_budget_tokens": 40_000,
        "root_budget_tokens": 40_000_000,
    }
    assert settings.workflow.root_session.consumer_projection(
        "root-seed.prepare") == {
            "resume": "forbidden",
            "seed": "digest-only",
            "seed_budget_tokens": 40_000,
            "root_budget_tokens": 40_000_000,
        }

    canonical = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    invalid_values = [None, True, False, 0, -1, 1.5, "40000"]
    for field in ("seed_budget_tokens", "root_budget_tokens"):
        for value in invalid_values:
            malformed = json.loads(json.dumps(canonical))
            malformed["workflow"]["root_session"][field] = value
            with pytest.raises(SettingsError):
                load_settings(_write(tmp_path, malformed))

    for field, value in (("resume", "allowed"), ("seed", "embedded")):
        malformed = json.loads(json.dumps(canonical))
        malformed["workflow"]["root_session"][field] = value
        with pytest.raises(SettingsError, match=field):
            load_settings(_write(tmp_path, malformed))

    inverted = json.loads(json.dumps(canonical))
    inverted["workflow"]["root_session"]["seed_budget_tokens"] = \
        inverted["workflow"]["root_session"]["root_budget_tokens"]
    with pytest.raises(SettingsError, match="must be below"):
        load_settings(_write(tmp_path, inverted))

    legacy = json.loads(json.dumps(canonical))
    legacy["schema"] = "taskplane.operational-settings/v1"
    legacy["workflow"].pop("root_session")
    migrated = load_settings(_write(tmp_path, legacy))
    assert migrated.workflow.root_session == settings.workflow.root_session
    assert migrated.receipt["migration"]["from"] == \
        "taskplane.operational-settings/v1"
    assert migrated.receipt["migration"]["to"] == settings.schema
    expected_legacy_digest = hashlib.sha256(json.dumps(
        legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")).hexdigest()
    assert migrated.receipt["migration"]["legacy_digest"] == \
        expected_legacy_digest
    assert migrated.receipt["migration"]["inserted"] == [
        "workflow.root_session.resume",
        "workflow.root_session.seed",
        "workflow.root_session.seed_budget_tokens",
        "workflow.root_session.root_budget_tokens",
    ]
    assert migrated.receipt["migration"]["legacy_digest"] != migrated.digest
    expected_effective_digest = hashlib.sha256(json.dumps(
        migrated.to_dict(), sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
    assert migrated.digest == expected_effective_digest


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

    null_meter = json.loads(
        DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    null_meter["limits"]["budgets"]["max_tokens"] = None
    with pytest.raises(SettingsError, match="must be non-null"):
        load_settings(_write(tmp_path, null_meter))

    inverted_meter = json.loads(
        DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    inverted_meter["limits"]["budgets"]["target_tokens"] = 18_000_000
    with pytest.raises(SettingsError, match="must be below"):
        load_settings(_write(tmp_path, inverted_meter))


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_route_is_validated_against_the_packaged_catalog(
        tmp_path, stage):
    value = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    value["lenses"]["routing"][stage] = ["not-a-catalog-lens"]
    value["lenses"]["counts"][stage] = 1

    with pytest.raises(
            SettingsError,
            match=rf"lenses\.routing\.{stage} contains unknown catalog ids"):
        load_settings(_write(tmp_path, value))


def test_stage_inheritance_is_normalized_before_dispatch(tmp_path):
    value = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    value["stages"]["engineering"] = {
        "model": "inherit", "reasoning": "inherit"}

    effective = load_settings(_write(tmp_path, value))

    assert effective.stages["engineering"].model is None
    assert effective.stages["engineering"].reasoning is None
    assert effective.stages["engineering"].to_dict() == {
        "model": None, "reasoning": None}


def test_non_executable_settings_fail_at_load_time(tmp_path):
    value = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    value["lenses"]["counts"]["plan"] = 5
    with pytest.raises(SettingsError, match="plan cannot exceed 4"):
        load_settings(_write(tmp_path, value))

    value = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    value["lenses"]["counts"]["plan"] = 2
    with pytest.raises(SettingsError, match="plan cannot exceed its maximum"):
        load_settings(_write(tmp_path, value))

    value = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    value["workflow"]["worker_inheritance"]["reasoning"] = False
    with pytest.raises(SettingsError, match="cannot disable"):
        load_settings(_write(tmp_path, value))

    value = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    value["observability"]["receipt"] = False
    with pytest.raises(SettingsError, match="cannot be disabled"):
        load_settings(_write(tmp_path, value))


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
        load_settings(path, overlay={"lenses": {"counts": {"product": 2}}})

    authority = {
        "schema": "taskplane.human-decision/v1",
        "authorized": True,
        "authority_requested": "gate_weakening",
        "actor": "human:operator",
        "thread": "thread-1",
        "revision": "rev-1",
    }
    weakened = load_settings(
        path, overlay={"lenses": {"counts": {"product": 2}}},
        authority=authority)
    assert weakened.lenses.counts["product"] == 2
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
