"""Pure policy for frozen-candidate, CI-first authoritative validation.

The workflow and local adapters consume this module; neither is allowed to
invent a candidate, widen a timeout, overlap selectors, or reinterpret a
green receipt.  All functions are effect-free so a proposed plan can be
validated before a runner, browser, cache, or cleanup resource is created.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from taskplane import build_quality
elif __package__:
    from . import build_quality
else:  # pragma: no cover - direct CLI module loading
    import build_quality


CANDIDATE_SCHEMA = "taskplane.ci-candidate/v1"
PLAN_SCHEMA = "taskplane.ci-plan/v1"
VALIDATION_SCHEMA = build_quality.VALIDATION_SCHEMA
METRICS_SCHEMA = "taskplane.ci-metrics/v1"

VALIDATION_LAYERS = build_quality.VALIDATION_LAYERS
FINGERPRINT_INPUTS = (
    "source",
    "tests",
    "settings",
    "inventory",
    "selector",
    "radius",
    "shard-plan",
    "runner",
    "environment",
)
BROWSER_INPUTS = (
    "executable",
    "version",
    "flags",
    "fixture_server",
    "snapshot",
    "dashboard_artifact",
    "selectors",
)
TERMINAL_OUTCOMES = (
    "success",
    "failure",
    "cancellation",
    "interruption",
    "timeout",
    "handoff",
)
DECLARED_TARGETS = {
    "first_validation_hours": 2.0,
    "p50_minutes": 10.0,
    "p95_minutes": 15.0,
    "runner_minutes_max": 28.0,
    "parallelism_min": 4.0,
}

_HEX = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


class CIPolicyError(ValueError):
    """A proposed validation action cannot acquire CI authority."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CIPolicyError("CI policy values must be portable JSON") from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CIPolicyError(f"{label} must be an object")
    return {str(key): copy.deepcopy(item) for key, item in value.items()}


def _strings(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise CIPolicyError(f"{label} must be a non-empty string list")
    if len(value) != len(set(value)):
        raise CIPolicyError(f"{label} contains duplicates")
    return list(value)


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise CIPolicyError(f"{label} must be positive")
    return float(value)


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise CIPolicyError(f"{label} must be a SHA-256 digest")
    return value


def freeze_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and freeze the inputs shared by every authoritative cell.

    Browser runtime identity is sealed separately.  That lets executable or
    browser-version drift invalidate only the isolated browser cell without
    pretending that the source/settings candidate itself changed.
    """

    raw = _mapping(value, "candidate")
    source_sha = raw.get("source_sha")
    if not isinstance(source_sha, str) or not _HEX.fullmatch(source_sha):
        raise CIPolicyError("candidate source_sha must be a full hexadecimal SHA")

    fingerprints = _mapping(raw.get("fingerprints"), "candidate fingerprints")
    if set(fingerprints) != set(FINGERPRINT_INPUTS):
        raise CIPolicyError(
            "candidate fingerprints must bind source, tests, settings, inventory, "
            "selector, radius, shard-plan, runner, and environment"
        )
    for name in FINGERPRINT_INPUTS:
        _require_digest(fingerprints[name], f"candidate fingerprint {name}")

    browser = _mapping(raw.get("browser"), "browser environment")
    if set(browser) != set(BROWSER_INPUTS):
        raise CIPolicyError("browser environment fingerprint inputs are incomplete")
    if not isinstance(browser["executable"], str) or not browser["executable"].startswith("/"):
        raise CIPolicyError("browser executable must be an absolute declared path")
    if not isinstance(browser["version"], str) or not browser["version"].strip():
        raise CIPolicyError("browser version must be declared")
    browser["flags"] = _strings(browser["flags"], "browser flags")
    for name in ("fixture_server", "snapshot", "dashboard_artifact", "selectors"):
        if not isinstance(browser[name], str) or not browser[name].strip():
            raise CIPolicyError(f"browser {name} must be declared")

    payload = {
        "schema": CANDIDATE_SCHEMA,
        "source_sha": source_sha,
        "fingerprints": {name: fingerprints[name] for name in FINGERPRINT_INPUTS},
    }
    return {
        **payload,
        "fingerprint": _fingerprint(payload),
        "browser": browser,
        "browser_fingerprint": _fingerprint(browser),
        "frozen": True,
    }


def _validated_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = _mapping(value, "candidate")
    rebuilt = freeze_candidate(candidate)
    if candidate.get("fingerprint") != rebuilt["fingerprint"]:
        raise CIPolicyError("candidate fingerprint is absent or stale")
    if candidate.get("browser_fingerprint") != rebuilt["browser_fingerprint"]:
        raise CIPolicyError("browser fingerprint is absent or stale")
    if candidate.get("frozen") is not True:
        raise CIPolicyError("candidate must be frozen before validation")
    return rebuilt


def advance_validation(
    candidate: Mapping[str, Any],
    layer: str,
    *,
    execution: str,
    prior: Mapping[str, Any] | None = None,
    unchanged_green: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt a frozen CI candidate to the canonical quality progression."""
    frozen = _validated_candidate(candidate)
    try:
        return build_quality.advance_progression(
            frozen["fingerprint"],
            layer,
            execution=execution,
            prior=prior,
            unchanged_green=unchanged_green,
        )
    except build_quality.BuildQualityError as exc:
        raise CIPolicyError(str(exc)) from None


def _settings_payload(declaration: Mapping[str, Any]) -> dict[str, Any]:
    settings = declaration.get("settings")
    to_dict = getattr(settings, "to_dict", None)
    if callable(to_dict):
        typed = _mapping(to_dict(), "settings")
        typed["digest"] = getattr(settings, "digest", None)
        return typed
    return _mapping(settings, "settings")


def build_ci_plan(
    candidate: Mapping[str, Any], declaration: Mapping[str, Any]
) -> dict[str, Any]:
    """Close a settings-derived, disjoint and cleanup-bound CI plan."""

    frozen = _validated_candidate(candidate)
    raw = _mapping(declaration, "CI plan declaration")
    settings = _settings_payload(raw)
    if settings.get("digest") != frozen["fingerprints"]["settings"]:
        raise CIPolicyError("CI plan settings digest does not match the candidate")
    build = _mapping(settings.get("build"), "settings.build")
    tests = _mapping(settings.get("tests"), "settings.tests")
    limits = _mapping(settings.get("limits"), "settings.limits")
    timeouts = _mapping(limits.get("timeouts"), "settings.limits.timeouts")
    timeout_ceiling = int(_positive_number(timeouts.get("task_seconds"), "task timeout"))

    raw_cells = raw.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells:
        raise CIPolicyError("CI plan must contain cells")
    test_shards = tests.get("shards")
    pytest_cells = sum(
        1 for value in raw_cells
        if isinstance(value, Mapping) and value.get("kind") == "pytest"
    )
    if (
        isinstance(test_shards, bool)
        or not isinstance(test_shards, int)
        or test_shards != pytest_cells
    ):
        raise CIPolicyError(
            "pytest cells must equal the settings-derived test shard count"
        )

    concurrency = build.get("concurrency")
    if concurrency == "native":
        max_parallel = len(raw_cells)
    elif isinstance(concurrency, int) and not isinstance(concurrency, bool) and concurrency > 0:
        max_parallel = min(concurrency, len(raw_cells))
    else:
        raise CIPolicyError("settings-derived concurrency is invalid")
    if len(raw_cells) >= 4 and max_parallel < 4:
        raise CIPolicyError("four disjoint shards require at least 4x parallelism")

    domains = _strings(
        raw.get("validation_domains"), "CI validation domains")

    seen_ids: set[str] = set()
    selector_runtimes: dict[str, set[str]] = {}
    seen_resources: set[str] = set()
    cells: list[dict[str, Any]] = []
    browsers = 0
    for index, value in enumerate(raw_cells):
        cell = _mapping(value, f"CI cell {index}")
        cell_id = cell.get("id")
        if not isinstance(cell_id, str) or not cell_id or cell_id in seen_ids:
            raise CIPolicyError("CI cell ids must be non-empty and unique")
        seen_ids.add(cell_id)
        if cell.get("validation_domain") not in domains:
            raise CIPolicyError(
                f"CI cell {cell_id} names an unknown validation domain")
        runtime = cell.get("runtime")
        if runtime is not None and (
            not isinstance(runtime, str) or not runtime.strip()
        ):
            raise CIPolicyError(f"CI cell {cell_id} runtime is invalid")
        runtime_identity = runtime if isinstance(runtime, str) else ""
        selectors = _strings(cell.get("selectors"), f"CI cell {cell_id} selectors")
        for selector in selectors:
            prior_runtimes = selector_runtimes.setdefault(selector, set())
            if prior_runtimes and (
                not runtime_identity or "" in prior_runtimes
                or runtime_identity in prior_runtimes
            ):
                raise CIPolicyError(
                    "selector overlap across CI cells: " + selector)
            prior_runtimes.add(runtime_identity)
        paths = _strings(cell.get("paths"), f"CI cell {cell_id} paths")
        timeout = int(_positive_number(cell.get("timeout_seconds"), f"CI cell {cell_id} timeout"))
        if timeout > timeout_ceiling:
            raise CIPolicyError(f"CI cell {cell_id} timeout exceeds settings ceiling")
        resources = _strings(cell.get("cleanup_resources"), f"CI cell {cell_id} cleanup resources")
        resource_overlap = seen_resources.intersection(resources)
        if resource_overlap:
            raise CIPolicyError("cleanup resources must have one owning cell")
        seen_resources.update(resources)

        kind = cell.get("kind")
        if not isinstance(kind, str) or not kind:
            raise CIPolicyError(f"CI cell {cell_id} needs a kind")
        normalized = {
            "id": cell_id,
            "kind": kind,
            "validation_domain": cell["validation_domain"],
            "selectors": selectors,
            "paths": paths,
            "timeout_seconds": timeout,
            "candidate_fingerprint": frozen["fingerprint"],
            "source_sha": frozen["source_sha"],
            "cleanup": {
                "resources": resources,
                "registered_before_run": True,
                "outcomes": list(TERMINAL_OUTCOMES),
            },
        }
        excluded = cell.get("excluded_selectors")
        if excluded is not None:
            excluded_selectors = _strings(
                excluded, f"CI cell {cell_id} excluded selectors")
            if kind != "pytest":
                raise CIPolicyError(
                    "only the authoritative pytest suite may exclude a "
                    "native-only selector")
            normalized["excluded_selectors"] = excluded_selectors
        if runtime is not None:
            normalized["runtime"] = runtime
        if kind == "browser":
            browsers += 1
            if cell.get("execution") != "ci-only":
                raise CIPolicyError("browser cell must be CI-only")
            normalized.update(
                {
                    "execution": "ci-only",
                    "browser_environment": copy.deepcopy(frozen["browser"]),
                    "browser_fingerprint": frozen["browser_fingerprint"],
                }
            )
        cells.append(normalized)
    if browsers != 1:
        raise CIPolicyError("the dashboard browser selectors require one isolated cell")

    serializations_raw = raw.get("serializations")
    if not isinstance(serializations_raw, list):
        raise CIPolicyError("named serializations must be a list")
    serializations: list[dict[str, Any]] = []
    names: set[str] = set()
    for value in serializations_raw:
        row = _mapping(value, "named serialization")
        name = row.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise CIPolicyError("serialization names must be non-empty and unique")
        names.add(name)
        members = _strings(row.get("cells"), f"serialization {name} cells")
        if not set(members).issubset(seen_ids):
            raise CIPolicyError(f"serialization {name} names an unknown cell")
        if len(members) == len(cells):
            raise CIPolicyError("global serialization is forbidden")
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise CIPolicyError(
                f"serialization {name} requires a concrete reason")
        serializations.append({
            "name": name, "cells": members, "reason": reason,
        })

    run = _mapping(raw.get("run"), "CI run")
    group = run.get("group")
    if not isinstance(group, str) or not group:
        raise CIPolicyError("CI cancellation group must be named")
    ref_kind = run.get("ref_kind")
    if ref_kind not in {"pull-request", "protected-main", "release"}:
        raise CIPolicyError("CI ref kind is unsupported")
    cancel = run.get("cancel_in_progress")
    if not isinstance(cancel, bool):
        raise CIPolicyError("cancel_in_progress must be boolean")
    if ref_kind in {"protected-main", "release"} and cancel:
        raise CIPolicyError(f"{ref_kind} runs must never cancel in progress")
    if ref_kind == "pull-request" and not cancel:
        raise CIPolicyError("superseded pull-request heads must cancel within their group")
    if ref_kind == "pull-request" and (
        run.get("event") != "pull_request" or not group.startswith("pull-request-")
    ):
        raise CIPolicyError("pull-request cancellation must stay in its exact PR group")
    cancellation = {
        "group": group,
        "cancel_in_progress": cancel,
        "scope": "same-pr-heads-only" if ref_kind == "pull-request" else "never",
    }

    payload = {
        "schema": PLAN_SCHEMA,
        "candidate_fingerprint": frozen["fingerprint"],
        "source_sha": frozen["source_sha"],
        "settings_digest": settings["digest"],
        "candidate_frozen_before_cells": True,
        "validation_domains": domains,
        "max_parallel": max_parallel,
        "cancellation": cancellation,
        "serializations": serializations,
        "cells": cells,
    }
    if frozen["fingerprints"]["shard-plan"] != _fingerprint(raw):
        raise CIPolicyError("CI declaration does not match the frozen shard plan fingerprint")
    return {**payload, "fingerprint": _fingerprint(payload)}


def _parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise CIPolicyError(f"{label} must be an ISO-8601 timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CIPolicyError(f"{label} must be an ISO-8601 timestamp") from exc


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def evaluate_ci_metrics(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate non-cumulative CI timing against the approved hard targets."""

    raw = _mapping(evidence, "CI metrics evidence")
    ready = _parse_time(raw.get("integration_ready_at"), "integration_ready_at")
    started = _parse_time(
        raw.get("first_validation_started_at"),
        "first_validation_started_at",
    )
    if started < ready:
        raise CIPolicyError(
            "first validation cannot start before integration is ready")
    first_hours = (started - ready).total_seconds() / 3600.0

    domain_ids = _strings(
        raw.get("validation_domain_ids"), "validation domain ids")
    durations_raw = raw.get("validation_domain_durations_minutes")
    if not isinstance(durations_raw, list) or not durations_raw:
        raise CIPolicyError(
            "validation domain durations must be a non-empty list")
    durations = [
        _positive_number(value, "validation domain duration")
        for value in durations_raw
    ]
    if len(durations) != len(domain_ids):
        raise CIPolicyError(
            "each validation domain id requires one elapsed duration")
    cells_raw = raw.get("cells")
    if not isinstance(cells_raw, list) or not cells_raw:
        raise CIPolicyError("CI metrics need cell durations")
    cell_ids: set[str] = set()
    cell_durations: list[float] = []
    for value in cells_raw:
        cell = _mapping(value, "CI metrics cell")
        cell_id = cell.get("id")
        if not isinstance(cell_id, str) or not cell_id or cell_id in cell_ids:
            raise CIPolicyError("CI metrics cell ids must be unique")
        cell_ids.add(cell_id)
        cell_durations.append(
            _positive_number(cell.get("duration_minutes"), f"cell {cell_id} duration")
        )
    elapsed = _positive_number(
        raw.get("authoritative_elapsed_minutes"), "authoritative elapsed minutes"
    )
    if elapsed < max(cell_durations):
        raise CIPolicyError(
            "authoritative elapsed time cannot be shorter than one of its cells"
        )

    configured = _mapping(raw.get("targets", DECLARED_TARGETS), "CI targets")
    if set(configured) != set(DECLARED_TARGETS):
        raise CIPolicyError("CI metrics targets are incomplete")
    targets = {
        key: _positive_number(configured[key], f"CI target {key}")
        for key in DECLARED_TARGETS
    }
    for key in (
        "first_validation_hours",
        "p50_minutes",
        "p95_minutes",
        "runner_minutes_max",
    ):
        if targets[key] > DECLARED_TARGETS[key]:
            raise CIPolicyError(f"CI target {key} cannot weaken the approved ceiling")
    if targets["parallelism_min"] < DECLARED_TARGETS["parallelism_min"]:
        raise CIPolicyError("CI target parallelism_min cannot weaken the approved floor")

    runner_minutes = sum(cell_durations)
    parallelism = runner_minutes / elapsed
    values = {
        "first_validation_hours": round(first_hours, 3),
        "p50_minutes": round(_nearest_rank(durations, 0.50), 3),
        "p95_minutes": round(_nearest_rank(durations, 0.95), 3),
        "runner_minutes": round(runner_minutes, 3),
        "parallelism": round(parallelism, 3),
    }
    comparisons = (
        ("first_validation_hours", values["first_validation_hours"], targets["first_validation_hours"], "max"),
        ("p50_minutes", values["p50_minutes"], targets["p50_minutes"], "max"),
        ("p95_minutes", values["p95_minutes"], targets["p95_minutes"], "max"),
        ("runner_minutes", values["runner_minutes"], targets["runner_minutes_max"], "max"),
        ("parallelism", values["parallelism"], targets["parallelism_min"], "min"),
    )
    checks = []
    for name, value, target, direction in comparisons:
        passed = value <= target if direction == "max" else value >= target
        checks.append(
            {
                "name": name,
                "value": value,
                "target": target,
                "direction": direction,
                "passed": passed,
            }
        )
    payload = {
        "schema": METRICS_SCHEMA,
        "values": values,
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
    }
    return {**payload, "fingerprint": _fingerprint(payload)}


__all__ = [
    "BROWSER_INPUTS",
    "CIPolicyError",
    "DECLARED_TARGETS",
    "FINGERPRINT_INPUTS",
    "TERMINAL_OUTCOMES",
    "VALIDATION_LAYERS",
    "advance_validation",
    "build_ci_plan",
    "evaluate_ci_metrics",
    "freeze_candidate",
]
