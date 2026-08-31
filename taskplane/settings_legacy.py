"""One-release migration into the canonical operational settings schema.

Migration is deliberately pure.  It changes representation, not authority,
and therefore neither consumes nor manufactures an authorization receipt.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


LEGACY_SCHEMA = "taskplane.operational-settings/v0"
CURRENT_SCHEMA = "taskplane.operational-settings/v1"

_FLAT_KEYS = frozenset({
    "schema", "version", "stage_models", "stage_reasoning", "lens_routes",
    "lens_counts", "build_shards", "build_concurrency", "test_backend",
    "test_selection", "test_shards", "test_cache", "timeouts", "budgets",
    "workflow_transport", "worker_inheritance", "cleanup",
})
_NESTED_KEYS = frozenset({
    "schema", "version", "stages", "lenses", "build", "tests", "limits",
    "workflow", "cleanup", "runtime", "dashboard", "overrides",
    "observability",
})


class LegacySettingsError(ValueError):
    """A legacy settings document cannot be migrated unambiguously."""


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LegacySettingsError(f"legacy {label} must be an object")
    return {str(key): item for key, item in value.items()}


def _test_execution_backend(value: object) -> object:
    """Translate the former runner-kind field into execution location."""
    return "local" if value in {"pytest", "command"} else value


def migrate_legacy_settings(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Migrate exactly v0, rejecting mixed, future, and unknown forms."""
    source = _mapping(raw, "settings")
    schema = source.get("schema")
    version = source.get("version")
    if schema not in (None, LEGACY_SCHEMA) or version not in (None, 0):
        raise LegacySettingsError("only the one-release v0 settings form is supported")
    # ``cleanup`` has the same nested representation in both v0 forms and is
    # therefore not a discriminator.  Only shape-exclusive keys may classify
    # the document; otherwise an ordinary flat document looks contradictory.
    shared = _FLAT_KEYS & _NESTED_KEYS
    flat = any(key in source for key in _FLAT_KEYS - shared)
    nested = any(key in source for key in _NESTED_KEYS - shared)
    if schema is None and version is None and not flat:
        raise LegacySettingsError(
            "unversioned legacy settings require a recognized flat key")
    if flat and nested:
        raise LegacySettingsError("legacy flat and versioned forms conflict")
    allowed = _FLAT_KEYS if flat else _NESTED_KEYS
    unknown = sorted(set(source) - allowed)
    if unknown:
        raise LegacySettingsError("unknown legacy settings: " + ", ".join(unknown))

    if nested:
        migrated = {key: value for key, value in source.items()
                    if key not in {"schema", "version"}}
        tests = migrated.get("tests")
        if isinstance(tests, Mapping) and "backend" in tests:
            migrated["tests"] = dict(tests)
            migrated["tests"]["backend"] = _test_execution_backend(
                tests["backend"])
        migrated["schema"] = CURRENT_SCHEMA
    else:
        models = _mapping(source.get("stage_models", {}), "stage_models")
        reasoning = _mapping(source.get("stage_reasoning", {}), "stage_reasoning")
        stage_names = sorted(set(models) | set(reasoning))
        stages = {
            name: {key: value for key, value in {
                "model": models.get(name), "reasoning": reasoning.get(name),
            }.items() if value is not None}
            for name in stage_names
        }
        cleanup = _mapping(source.get("cleanup", {}), "cleanup")
        migrated = {
            "schema": CURRENT_SCHEMA,
            "stages": stages,
            "lenses": {
                "routing": _mapping(source.get("lens_routes", {}), "lens_routes"),
                "counts": _mapping(source.get("lens_counts", {}), "lens_counts"),
            },
            "build": {key: value for key, value in {
                "shards": source.get("build_shards"),
                "concurrency": source.get("build_concurrency"),
            }.items() if value is not None},
            "tests": {key: value for key, value in {
                "backend": _test_execution_backend(
                    source.get("test_backend")),
                "selection": source.get("test_selection"),
                "shards": source.get("test_shards"),
                "cache": source.get("test_cache"),
            }.items() if value is not None},
            "limits": {
                "timeouts": _mapping(source.get("timeouts", {}), "timeouts"),
                "budgets": _mapping(source.get("budgets", {}), "budgets"),
            },
            "workflow": {key: value for key, value in {
                "transport": source.get("workflow_transport"),
                "worker_inheritance": source.get("worker_inheritance"),
            }.items() if value is not None},
            "cleanup": {
                "worktrees": cleanup.get("worktrees"),
                "artifacts_days": cleanup.get("artifacts_days"),
            },
        }
        migrated["cleanup"] = {
            key: value for key, value in migrated["cleanup"].items()
            if value is not None
        }
    return migrated, {"from": LEGACY_SCHEMA, "to": CURRENT_SCHEMA}


__all__ = [
    "CURRENT_SCHEMA", "LEGACY_SCHEMA", "LegacySettingsError",
    "migrate_legacy_settings",
]
