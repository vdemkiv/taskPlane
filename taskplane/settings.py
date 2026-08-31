"""Canonical, typed and effect-free operational settings.

This module owns interpretation only: it reads JSON, validates and freezes the
result, and emits a deterministic receipt.  It never dispatches work, selects
host-native authority, reads secrets, or mutates process state.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from taskplane.authority import DECISION_SCHEMA
from taskplane.settings_legacy import (
    CURRENT_SCHEMA, LEGACY_SCHEMA, LegacySettingsError,
    migrate_legacy_settings,
)


DEFAULT_SETTINGS_PATH = Path(__file__).with_name("operational-settings.json")
RECEIPT_SCHEMA = "taskplane.operational-settings-receipt/v1"
STAGES = ("product", "design", "plan", "build", "evaluate", "fix")
REASONING = frozenset(("inherit", "low", "medium", "high", "xhigh", "max", "ultra"))
TEST_SELECTIONS = frozenset(("targeted", "affected", "all"))
_SECRET_PARTS = ("secret", "password", "credential", "private_key", "access_token", "api_key")
_LEGACY_FLAT_MARKERS = frozenset({
    "stage_models", "stage_reasoning", "lens_routes", "lens_counts",
    "build_shards", "build_concurrency", "test_backend", "test_selection",
    "test_shards", "test_cache", "timeouts", "budgets",
    "workflow_transport", "worker_inheritance",
})
_LEGACY_ENV_TIERS = {
    "CHEAP": ("evaluate", "fix"),
    "STANDARD": ("build",),
    "DEEP": ("product", "design", "plan"),
}
REQUIRED_DASHBOARD_LIFECYCLE_EVENTS = (
    "gate", "submit", "next_action", "approve", "select", "resolve",
    "replan", "handle_host_input", "cleanup_replay", "retro",
    "worker_terminal",
)

_TOP = frozenset({
    "schema", "stages", "lenses", "build", "tests", "limits", "workflow",
    "cleanup", "runtime", "dashboard", "overrides", "observability",
})
_SHAPE: dict[tuple[str, ...], frozenset[str]] = {
    (): _TOP,
    ("stages",): frozenset(STAGES),
    ("stages", "*"): frozenset(("model", "reasoning")),
    ("lenses",): frozenset(("routing", "counts")),
    ("lenses", "routing"): frozenset(STAGES),
    ("lenses", "counts"): frozenset(STAGES),
    ("build",): frozenset(("shards", "concurrency")),
    ("tests",): frozenset(("backend", "selection", "shards", "cache")),
    ("limits",): frozenset(("timeouts", "budgets")),
    ("limits", "timeouts"): frozenset(("task_seconds", "subprocess_seconds", "wait_seconds")),
    ("limits", "budgets"): frozenset(("max_actions", "max_tokens", "max_cost_usd")),
    ("workflow",): frozenset(("transport", "worker_inheritance")),
    ("workflow", "worker_inheritance"): frozenset(("model", "reasoning")),
    ("cleanup",): frozenset(("worktrees", "artifacts_days")),
    ("runtime",): frozenset((
        "audit_every", "inline_max_bytes", "orphan_ttl_seconds",
        "obligations", "runnability", "review_max_attempts")),
    ("dashboard",): frozenset(("refresh",)),
    ("dashboard", "refresh"): frozenset((
        "lifecycle_events", "session_event", "replay_on_session_start")),
    ("overrides",): frozenset(("safe_paths", "governance_paths")),
    ("observability",): frozenset(("receipt", "include_values")),
}


class SettingsError(ValueError):
    """Settings are malformed, contradictory, or insufficiently authorized."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SettingsError("settings must contain portable JSON values") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _freeze(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class StageSettings:
    model: str | None
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "reasoning": self.reasoning}


@dataclass(frozen=True)
class LensSettings:
    routing: Mapping[str, tuple[str, ...]]
    counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "routing": {key: list(value) for key, value in self.routing.items()},
            "counts": dict(self.counts),
        }


@dataclass(frozen=True)
class BuildSettings:
    shards: int
    concurrency: str | int

    def to_dict(self) -> dict[str, Any]:
        return {"shards": self.shards, "concurrency": self.concurrency}


@dataclass(frozen=True)
class TestSettings:
    backend: str
    selection: str
    shards: int
    cache: bool

    def to_dict(self) -> dict[str, Any]:
        return {"backend": self.backend, "selection": self.selection,
                "shards": self.shards, "cache": self.cache}


@dataclass(frozen=True)
class LimitSettings:
    timeouts: Mapping[str, int]
    budgets: Mapping[str, int | float | None]

    def to_dict(self) -> dict[str, Any]:
        return {"timeouts": dict(self.timeouts), "budgets": dict(self.budgets)}


@dataclass(frozen=True)
class WorkflowSettings:
    transport: str
    worker_inheritance: Mapping[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {"transport": self.transport,
                "worker_inheritance": dict(self.worker_inheritance)}


@dataclass(frozen=True)
class CleanupSettings:
    worktrees: str
    artifacts_days: int

    def to_dict(self) -> dict[str, Any]:
        return {"worktrees": self.worktrees, "artifacts_days": self.artifacts_days}


@dataclass(frozen=True)
class RuntimeSettings:
    audit_every: int
    inline_max_bytes: int
    orphan_ttl_seconds: int
    obligations: str
    runnability: str
    review_max_attempts: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_every": self.audit_every,
            "inline_max_bytes": self.inline_max_bytes,
            "orphan_ttl_seconds": self.orphan_ttl_seconds,
            "obligations": self.obligations,
            "runnability": self.runnability,
            "review_max_attempts": self.review_max_attempts,
        }


@dataclass(frozen=True)
class DashboardRefreshSettings:
    lifecycle_events: tuple[str, ...]
    session_event: str
    replay_on_session_start: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "lifecycle_events": list(self.lifecycle_events),
            "session_event": self.session_event,
            "replay_on_session_start": self.replay_on_session_start,
        }


@dataclass(frozen=True)
class DashboardSettings:
    refresh: DashboardRefreshSettings

    def to_dict(self) -> dict[str, Any]:
        return {"refresh": self.refresh.to_dict()}


@dataclass(frozen=True)
class OverrideSettings:
    safe_paths: tuple[str, ...]
    governance_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"safe_paths": list(self.safe_paths),
                "governance_paths": list(self.governance_paths)}


@dataclass(frozen=True)
class ObservabilitySettings:
    receipt: bool
    include_values: bool

    def to_dict(self) -> dict[str, Any]:
        return {"receipt": self.receipt, "include_values": self.include_values}


@dataclass(frozen=True)
class OperationalSettings:
    stages: Mapping[str, StageSettings]
    lenses: LensSettings
    build: BuildSettings
    tests: TestSettings
    limits: LimitSettings
    workflow: WorkflowSettings
    cleanup: CleanupSettings
    runtime: RuntimeSettings
    dashboard: DashboardSettings
    overrides: OverrideSettings
    observability: ObservabilitySettings
    digest: str
    receipt: Mapping[str, Any]
    schema: str = CURRENT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "stages": {key: value.to_dict() for key, value in self.stages.items()},
            "lenses": self.lenses.to_dict(), "build": self.build.to_dict(),
            "tests": self.tests.to_dict(), "limits": self.limits.to_dict(),
            "workflow": self.workflow.to_dict(), "cleanup": self.cleanup.to_dict(),
            "runtime": self.runtime.to_dict(),
            "dashboard": self.dashboard.to_dict(),
            "overrides": self.overrides.to_dict(),
            "observability": self.observability.to_dict(),
        }


def _plain_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _reject_secrets(value: object, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).lower()
            if any(part in name for part in _SECRET_PARTS):
                raise SettingsError("secret-bearing setting is forbidden: " + ".".join(path + (str(key),)))
            _reject_secrets(item, path + (str(key),))
    elif isinstance(value, list):
        for item in value:
            _reject_secrets(item, path)


def _shape_key(path: tuple[str, ...]) -> tuple[str, ...]:
    if len(path) == 2 and path[0] == "stages":
        return ("stages", "*")
    return path


def _validate_keys(value: object, path: tuple[str, ...] = ()) -> None:
    if not isinstance(value, Mapping):
        return
    allowed = _SHAPE.get(_shape_key(path))
    if allowed is not None:
        unknown = sorted(str(key) for key in value if str(key) not in allowed)
        if unknown:
            label = ".".join(path) or "settings"
            raise SettingsError(f"unknown {label} keys: " + ", ".join(unknown))
    for key, item in value.items():
        _validate_keys(item, path + (str(key),))


def _merge(base: Mapping[str, Any], change: Mapping[str, Any]) -> dict[str, Any]:
    result = {str(key): item for key, item in base.items()}
    for key, value in change.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _leaf_paths(value: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> list[str]:
    result: list[str] = []
    for key, item in value.items():
        path = prefix + (str(key),)
        if isinstance(item, Mapping):
            result.extend(_leaf_paths(item, path))
        else:
            result.append(".".join(path))
    return sorted(result)


def _matches(pattern: str, path: str) -> bool:
    expected, actual = pattern.split("."), path.split(".")
    return len(expected) == len(actual) and all(
        left == "*" or left == right for left, right in zip(expected, actual))


def _exact_authority(authority: object) -> str | None:
    if not isinstance(authority, Mapping):
        return None
    required = (
        authority.get("schema") == DECISION_SCHEMA,
        authority.get("authorized") is True,
        authority.get("authority_requested") in {"gate_weakening", "major_authority_change"},
        bool(str(authority.get("actor") or "").strip()),
        bool(str(authority.get("thread") or "").strip()),
        bool(str(authority.get("revision") or "").strip()),
    )
    return _digest(dict(authority)) if all(required) else None


def _positive_int(value: object, label: str, *, zero: bool = False) -> int:
    minimum = 0 if zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SettingsError(f"{label} must be an integer >= {minimum}")
    return value


def _validate_and_type(data: Mapping[str, Any], receipt: Mapping[str, Any]) -> OperationalSettings:
    if data.get("schema") != CURRENT_SCHEMA:
        raise SettingsError("unsupported operational settings schema")
    stages_raw = _plain_mapping(data.get("stages"), "stages")
    stages: dict[str, StageSettings] = {}
    for name in STAGES:
        row = _plain_mapping(stages_raw.get(name), f"stages.{name}")
        model = row.get("model")
        if model == "inherit":
            model = None
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise SettingsError(f"stages.{name}.model must be inherit or a non-empty string")
        reasoning = row.get("reasoning")
        if reasoning not in REASONING:
            raise SettingsError(f"stages.{name}.reasoning is unsupported")
        stages[name] = StageSettings(model=model, reasoning=str(reasoning))

    lenses_raw = _plain_mapping(data.get("lenses"), "lenses")
    routes_raw = _plain_mapping(lenses_raw.get("routing"), "lenses.routing")
    counts_raw = _plain_mapping(lenses_raw.get("counts"), "lenses.counts")
    routing: dict[str, tuple[str, ...]] = {}
    counts: dict[str, int] = {}
    for name in STAGES:
        route = routes_raw.get(name)
        if not isinstance(route, list) or not all(isinstance(item, str) and item.strip() for item in route):
            raise SettingsError(f"lenses.routing.{name} must be a string list")
        if len(set(route)) != len(route):
            raise SettingsError(f"lenses.routing.{name} contains conflicting duplicates")
        routing[name] = tuple(route)
        counts[name] = _positive_int(counts_raw.get(name), f"lenses.counts.{name}", zero=True)
    if routing["build"] or counts["build"] != 0:
        raise SettingsError("build must preserve the zero lens worker invariant")

    build_raw = _plain_mapping(data.get("build"), "build")
    build_shards = _positive_int(build_raw.get("shards"), "build.shards")
    concurrency = build_raw.get("concurrency")
    if concurrency != "native" and (isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1):
        raise SettingsError("build.concurrency must be native or a positive integer")

    tests_raw = _plain_mapping(data.get("tests"), "tests")
    backend = tests_raw.get("backend")
    if backend not in {"pytest", "command"}:
        raise SettingsError("tests.backend is unsupported")
    selection = tests_raw.get("selection")
    if selection not in TEST_SELECTIONS:
        raise SettingsError("tests.selection is unsupported")
    if not isinstance(tests_raw.get("cache"), bool):
        raise SettingsError("tests.cache must be boolean")

    limits_raw = _plain_mapping(data.get("limits"), "limits")
    timeouts_raw = _plain_mapping(limits_raw.get("timeouts"), "limits.timeouts")
    timeouts = {key: _positive_int(timeouts_raw.get(key), f"limits.timeouts.{key}")
                for key in ("task_seconds", "subprocess_seconds", "wait_seconds")}
    budgets_raw = _plain_mapping(limits_raw.get("budgets"), "limits.budgets")
    budgets: dict[str, int | float | None] = {
        "max_actions": _positive_int(budgets_raw.get("max_actions"), "limits.budgets.max_actions")}
    for key in ("max_tokens", "max_cost_usd"):
        value = budgets_raw.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0):
            raise SettingsError(f"limits.budgets.{key} must be null or positive")
        budgets[key] = value

    workflow_raw = _plain_mapping(data.get("workflow"), "workflow")
    if workflow_raw.get("transport") != "native":
        raise SettingsError("workflow.transport must preserve native host authority")
    inheritance_raw = _plain_mapping(workflow_raw.get("worker_inheritance"), "workflow.worker_inheritance")
    if any(not isinstance(inheritance_raw.get(key), bool) for key in ("model", "reasoning")):
        raise SettingsError("workflow worker inheritance values must be boolean")

    cleanup_raw = _plain_mapping(data.get("cleanup"), "cleanup")
    if cleanup_raw.get("worktrees") not in {"after-merge", "retain", "manual"}:
        raise SettingsError("cleanup.worktrees is unsupported")
    artifacts_days = _positive_int(cleanup_raw.get("artifacts_days"), "cleanup.artifacts_days", zero=True)

    runtime_raw = _plain_mapping(data.get("runtime"), "runtime")
    audit_every = _positive_int(runtime_raw.get("audit_every"),
                                "runtime.audit_every")
    inline_max_bytes = _positive_int(runtime_raw.get("inline_max_bytes"),
                                     "runtime.inline_max_bytes", zero=True)
    orphan_ttl_seconds = _positive_int(runtime_raw.get("orphan_ttl_seconds"),
                                       "runtime.orphan_ttl_seconds")
    obligations = runtime_raw.get("obligations")
    if obligations not in {"enforce", "advisory"}:
        raise SettingsError("runtime.obligations is unsupported")
    runnability = runtime_raw.get("runnability")
    if runnability not in {"probe", "disabled"}:
        raise SettingsError("runtime.runnability is unsupported")
    review_max_attempts = _positive_int(runtime_raw.get("review_max_attempts"),
                                        "runtime.review_max_attempts")

    dashboard_raw = _plain_mapping(data.get("dashboard"), "dashboard")
    refresh_raw = _plain_mapping(dashboard_raw.get("refresh"),
                                 "dashboard.refresh")
    lifecycle_events = refresh_raw.get("lifecycle_events")
    if not isinstance(lifecycle_events, list) or not lifecycle_events or not all(
            isinstance(item, str) and item.strip()
            for item in lifecycle_events):
        raise SettingsError(
            "dashboard.refresh.lifecycle_events must be a non-empty string list")
    if len(set(lifecycle_events)) != len(lifecycle_events):
        raise SettingsError(
            "dashboard.refresh.lifecycle_events contains duplicates")
    if frozenset(lifecycle_events) != frozenset(
            REQUIRED_DASHBOARD_LIFECYCLE_EVENTS):
        raise SettingsError(
            "dashboard.refresh.lifecycle_events must exactly match required "
            "governed transitions")
    session_event = refresh_raw.get("session_event")
    if not isinstance(session_event, str) or not session_event.strip():
        raise SettingsError("dashboard.refresh.session_event must be a string")
    replay_on_session_start = refresh_raw.get("replay_on_session_start")
    if not isinstance(replay_on_session_start, bool):
        raise SettingsError(
            "dashboard.refresh.replay_on_session_start must be boolean")

    overrides_raw = _plain_mapping(data.get("overrides"), "overrides")
    safe_paths = overrides_raw.get("safe_paths")
    governance_paths = overrides_raw.get("governance_paths")
    for value, label in ((safe_paths, "safe_paths"), (governance_paths, "governance_paths")):
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise SettingsError(f"overrides.{label} must be a string list")
    observable_raw = _plain_mapping(data.get("observability"), "observability")
    if any(not isinstance(observable_raw.get(key), bool) for key in ("receipt", "include_values")):
        raise SettingsError("observability values must be boolean")
    if observable_raw["include_values"]:
        raise SettingsError("observability cannot include raw setting values")

    normalized = {
        "schema": CURRENT_SCHEMA,
        "stages": {key: value.to_dict() for key, value in stages.items()},
        "lenses": {"routing": {key: list(value) for key, value in routing.items()}, "counts": counts},
        "build": {"shards": build_shards, "concurrency": concurrency},
        "tests": {"backend": backend, "selection": selection,
                  "shards": _positive_int(tests_raw.get("shards"), "tests.shards"),
                  "cache": tests_raw["cache"]},
        "limits": {"timeouts": timeouts, "budgets": budgets},
        "workflow": {"transport": "native", "worker_inheritance": dict(inheritance_raw)},
        "cleanup": {"worktrees": cleanup_raw["worktrees"], "artifacts_days": artifacts_days},
        "runtime": {
            "audit_every": audit_every,
            "inline_max_bytes": inline_max_bytes,
            "orphan_ttl_seconds": orphan_ttl_seconds,
            "obligations": obligations,
            "runnability": runnability,
            "review_max_attempts": review_max_attempts,
        },
        "dashboard": {"refresh": {
            "lifecycle_events": list(lifecycle_events),
            "session_event": session_event,
            "replay_on_session_start": replay_on_session_start,
        }},
        "overrides": {"safe_paths": list(safe_paths), "governance_paths": list(governance_paths)},
        "observability": dict(observable_raw),
    }
    digest = _digest(normalized)
    sealed_receipt = dict(receipt)
    sealed_receipt["settings_digest"] = digest
    return OperationalSettings(
        stages=_freeze(stages),
        lenses=LensSettings(_freeze(routing), _freeze(counts)),
        build=BuildSettings(build_shards, concurrency),
        tests=TestSettings(backend, selection, normalized["tests"]["shards"], tests_raw["cache"]),
        limits=LimitSettings(_freeze(timeouts), _freeze(budgets)),
        workflow=WorkflowSettings("native", _freeze(dict(inheritance_raw))),
        cleanup=CleanupSettings(cleanup_raw["worktrees"], artifacts_days),
        runtime=RuntimeSettings(
            audit_every, inline_max_bytes, orphan_ttl_seconds, obligations,
            runnability, review_max_attempts),
        dashboard=DashboardSettings(DashboardRefreshSettings(
            tuple(lifecycle_events), session_event, replay_on_session_start)),
        overrides=OverrideSettings(tuple(safe_paths), tuple(governance_paths)),
        observability=ObservabilitySettings(observable_raw["receipt"], False),
        digest=digest, receipt=_freeze(sealed_receipt),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SettingsError(f"cannot read valid settings JSON: {exc}") from exc
    return _plain_mapping(raw, "settings")


def load_settings(path: str | Path = DEFAULT_SETTINGS_PATH, *,
                  overlay: Mapping[str, Any] | None = None,
                  environment: Mapping[str, str] | None = None,
                  authority: Mapping[str, Any] | None = None,
                  host_capabilities: object | None = None) -> OperationalSettings:
    """Load defaults < file < legacy environment < receipted overlay.

    ``host_capabilities`` is accepted to make the boundary explicit, but does
    not alter settings or receipts. Host support is negotiated later by the
    existing capability authority, preserving cross-host portability. Only
    the closed one-release environment alias table is interpreted here.
    """
    del host_capabilities
    defaults = _read_json(DEFAULT_SETTINGS_PATH)
    raw = _read_json(Path(path))
    _reject_secrets(raw)
    migration: dict[str, str] | None = None
    if (raw.get("schema") == LEGACY_SCHEMA or raw.get("version") == 0
            or any(key in raw for key in _LEGACY_FLAT_MARKERS)):
        try:
            raw, migration = migrate_legacy_settings(raw)
        except LegacySettingsError as exc:
            raise SettingsError(str(exc)) from exc
    elif "version" in raw:
        raise SettingsError("unknown settings version")
    _validate_keys(raw)
    effective = _merge(defaults, raw)
    precedence = ["defaults", "file"]
    environment_receipt: dict[str, Any] | None = None
    if environment is not None:
        environment_overlay: dict[str, Any] = {"stages": {}}
        applied: list[str] = []
        for tier, stages in _LEGACY_ENV_TIERS.items():
            for field, prefix in (("model", "TASKPLANE_MODEL_"),
                                  ("reasoning", "TASKPLANE_REASONING_")):
                name = prefix + tier
                if name not in environment:
                    continue
                value = str(environment[name]).strip()
                if field == "model" and value in {"", "inherit"}:
                    value = "inherit"
                if not value:
                    continue
                for stage in stages:
                    environment_overlay["stages"].setdefault(stage, {})[
                        field] = value
                    applied.append(f"stages.{stage}.{field}")
        runtime_overlay: dict[str, Any] = {}
        integer_aliases = {
            "TASKPLANE_AUDIT_EVERY": "audit_every",
            "TASKPLANE_INLINE_MAX": "inline_max_bytes",
            "TASKPLANE_ORPHAN_TTL": "orphan_ttl_seconds",
            "TASKPLANE_ORPHAN_TTL_SECONDS": "orphan_ttl_seconds",
        }
        for name, field in integer_aliases.items():
            if name not in environment:
                continue
            raw_value = str(environment[name]).strip()
            try:
                value = int(raw_value)
            except ValueError as exc:
                raise SettingsError(
                    f"legacy environment {name} must be an integer") from exc
            if field in runtime_overlay:
                raise SettingsError(
                    "duplicate legacy environment aliases for runtime." +
                    field)
            runtime_overlay[field] = value
            applied.append(f"runtime.{field}")
        enum_aliases = {
            "TASKPLANE_OBLIGATIONS": (
                "obligations", {"on": "enforce", "enforce": "enforce",
                                "off": "advisory", "0": "advisory",
                                "false": "advisory", "advisory": "advisory"}),
            "TASKPLANE_RUNNABILITY": (
                "runnability", {"on": "probe", "probe": "probe",
                                "off": "disabled", "0": "disabled",
                                "false": "disabled", "no": "disabled"}),
        }
        for name, (field, aliases) in enum_aliases.items():
            if name not in environment:
                continue
            raw_value = str(environment[name]).strip().lower()
            if raw_value not in aliases:
                raise SettingsError(
                    f"legacy environment {name} is unsupported")
            runtime_overlay[field] = aliases[raw_value]
            applied.append(f"runtime.{field}")
        if runtime_overlay:
            environment_overlay["runtime"] = runtime_overlay
        if environment_overlay["stages"] or runtime_overlay:
            paths = _leaf_paths(environment_overlay)
            safe_patterns = defaults["overrides"]["safe_paths"]
            governance = [path for path in paths if not any(
                _matches(pattern, path) for pattern in safe_patterns)]
            authority_fingerprint = None
            if governance:
                authority_fingerprint = _exact_authority(authority)
                if authority_fingerprint is None:
                    raise SettingsError(
                        "governance-weakening environment override requires "
                        "exact authority")
            effective = _merge(effective, environment_overlay)
            precedence.append("environment")
            environment_receipt = {
                "applied": sorted(applied),
                "adapter": "legacy-tier-environment/v1",
                "authority_fingerprint": authority_fingerprint,
            }
    overlay_receipt: dict[str, Any] | None = None
    if overlay is not None:
        supplied = _plain_mapping(overlay, "overlay")
        _reject_secrets(supplied)
        _validate_keys(supplied)
        paths = _leaf_paths(supplied)
        # The overlay cannot widen its own authority by replacing the file's
        # safe-path declaration.  Classification is pinned to the shipped
        # canonical policy, while the file copy remains observable settings.
        safe_patterns = defaults["overrides"]["safe_paths"]
        governance = [path for path in paths if not any(
            _matches(pattern, path) for pattern in safe_patterns)]
        authority_fingerprint = None
        if governance:
            authority_fingerprint = _exact_authority(authority)
            if authority_fingerprint is None:
                raise SettingsError("governance-weakening override requires exact authority")
        effective = _merge(effective, supplied)
        precedence.append("overlay")
        overlay_receipt = {"applied": paths,
                           "authority_fingerprint": authority_fingerprint}
    _reject_secrets(effective)
    _validate_keys(effective)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "precedence": precedence,
        "migration": migration,
        "environment": environment_receipt,
        "overlay": overlay_receipt,
    }
    return _validate_and_type(effective, receipt)


def settings_digest(settings: OperationalSettings | Mapping[str, Any]) -> str:
    """Return the portable digest of an effective settings value."""
    value = settings.to_dict() if isinstance(settings, OperationalSettings) else dict(settings)
    return _digest(value)


def settings_receipt(settings: OperationalSettings) -> dict[str, Any]:
    """Return the deterministic, secret-free observability receipt."""
    if not isinstance(settings, OperationalSettings):
        raise TypeError("settings must be OperationalSettings")
    return dict(settings.receipt)


__all__ = [
    "BuildSettings", "CleanupSettings", "DashboardRefreshSettings",
    "DashboardSettings", "DEFAULT_SETTINGS_PATH",
    "LensSettings", "LimitSettings", "ObservabilitySettings",
    "OperationalSettings", "OverrideSettings", "RuntimeSettings",
    "REQUIRED_DASHBOARD_LIFECYCLE_EVENTS", "SettingsError",
    "StageSettings", "TestSettings", "WorkflowSettings", "load_settings",
    "settings_digest", "settings_receipt",
]
