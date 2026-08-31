"""Closed, redacted and non-cumulative delivery-wave metrics receipts.

This module is the sole counting boundary for delivery metrics.  Renderers,
Retro, Engineering, and release policy consume the sealed receipt or a bounded
projection of it; they never walk traces, archives, DOM state, or CI reruns.
"""

from __future__ import annotations

import copy
from datetime import datetime
import math
import re
from collections.abc import Mapping
from typing import Any

try:
    from .ci_policy import DECLARED_TARGETS as CI_TARGETS
    from .delivery_ports import content_fingerprint
    from .dispatch_telemetry import WAVE_BUDGET_CEILINGS
except ImportError:  # pragma: no cover - direct module loading
    from ci_policy import DECLARED_TARGETS as CI_TARGETS
    from delivery_ports import content_fingerprint
    from dispatch_telemetry import WAVE_BUDGET_CEILINGS


EVIDENCE_SCHEMA = "taskplane.wave-metrics-evidence/v1"
RECEIPT_SCHEMA = "taskplane.wave-metrics-receipt/v1"
PROJECTION_SCHEMA = "taskplane.wave-metrics-projection/v1"

SOURCE_NAMES = (
    "settings", "ci", "dashboard_publication", "cleanup", "portfolio",
    "token_usage", "sessions", "worktrees", "dispatch",
)

CEILING_DEFINITIONS = {
    "total_tokens": (
        "token_total_observed", WAVE_BUDGET_CEILINGS["total_tokens"]),
    "uncached_input_tokens": (
        "token_uncached_observed",
        WAVE_BUDGET_CEILINGS["uncached_input_tokens"]),
    "sessions": ("planned_sessions", WAVE_BUDGET_CEILINGS["sessions"]),
    "active_delivery_hours": (
        "active_delivery_hours",
        WAVE_BUDGET_CEILINGS["elapsed_seconds"] / 3600),
}

# Approved R-0001 measurement vocabulary.  These are product acceptance facts,
# not operational defaults; configurable CI ceilings continue to be owned by
# ci_policy and are supplied in its sealed evidence digest.
METRIC_DEFINITIONS: dict[str, dict[str, Any]] = {
    "settings_spread_files": {
        "baseline": 258, "target": 0, "comparison": "max",
        "unit": "files", "source": "settings"},
    "settings_ownership_percent": {
        "baseline": None, "target": 100, "comparison": "min",
        "unit": "percent", "source": "settings"},
    "duplicate_defaults": {
        "baseline": None, "target": 0, "comparison": "max",
        "unit": "defaults", "source": "settings"},
    "suite_files": {
        "baseline": 266, "target": 230, "comparison": "max",
        "unit": "files", "source": "portfolio"},
    "suite_cases": {
        "baseline": 4909, "target": 4200, "comparison": "max",
        "unit": "cases", "source": "portfolio"},
    "suite_loc": {
        "baseline": 95601, "target": None, "comparison": "record",
        "unit": "lines", "source": "portfolio"},
    "redundant_families_removed": {
        "baseline": 0, "target": 6, "comparison": "min",
        "unit": "families", "source": "portfolio"},
    "exact_feedback_p95_seconds": {
        "baseline": None, "target": 60, "comparison": "max",
        "unit": "seconds", "source": "ci"},
    "proportional_feedback_p95_minutes": {
        "baseline": None, "target": 5, "comparison": "max",
        "unit": "minutes", "source": "ci"},
    "ci_first_matrix_hours": {
        "baseline": 31.617, "target": CI_TARGETS["first_matrix_hours"],
        "comparison": "max",
        "unit": "hours", "source": "ci"},
    "ci_matrix_count": {
        "baseline": 12, "target": CI_TARGETS["matrix_count_max"],
        "comparison": "max",
        "unit": "matrices", "source": "ci"},
    "ci_red_matrices": {
        "baseline": 9, "target": 0, "comparison": "max",
        "unit": "matrices", "source": "ci"},
    "ci_critical_path_minutes": {
        "baseline": 15, "target": CI_TARGETS["p95_minutes"],
        "comparison": "max", "unit": "minutes", "source": "ci"},
    "ci_p50_minutes": {
        "baseline": None, "target": CI_TARGETS["p50_minutes"],
        "comparison": "max", "unit": "minutes", "source": "ci"},
    "ci_p95_minutes": {
        "baseline": 15, "target": CI_TARGETS["p95_minutes"],
        "comparison": "max", "unit": "minutes", "source": "ci"},
    "ci_runner_minutes": {
        "baseline": 38, "target": CI_TARGETS["runner_minutes_max"],
        "comparison": "max",
        "unit": "runner-minutes", "source": "ci"},
    "ci_parallelism_factor": {
        "baseline": 2.59, "target": CI_TARGETS["parallelism_min"],
        "comparison": "min",
        "unit": "factor", "source": "ci"},
    "cleanup_leak_count": {
        "baseline": None, "target": 0, "comparison": "max",
        "unit": "leaks", "source": "cleanup"},
    "stale_worktrees": {
        "baseline": 132, "target": 0, "comparison": "max",
        "unit": "worktrees", "source": "worktrees"},
    "stale_state_gb": {
        "baseline": 17.3, "target": 0, "comparison": "max",
        "unit": "gigabytes", "source": "worktrees"},
    "active_worktrees": {
        "baseline": None, "target": "active_shards_plus_one",
        "comparison": "dynamic-max", "unit": "worktrees",
        "source": "worktrees"},
    "token_total_observed": {
        "baseline": 540_300_000, "target": 100_000_000,
        "comparison": "max", "unit": "tokens", "source": "token_usage"},
    "token_uncached_observed": {
        "baseline": None, "target": 15_000_000, "comparison": "max",
        "unit": "tokens", "source": "token_usage"},
    "token_archive_upper_bound": {
        "baseline": 1_292_000_000, "target": None,
        "comparison": "record", "unit": "tokens", "source": "token_usage"},
    "active_delivery_hours": {
        "baseline": None, "target": 8, "comparison": "max",
        "unit": "hours", "source": "dispatch"},
    "end_to_end_wave_hours": {
        "baseline": 40.583, "target": 12, "comparison": "max",
        "unit": "hours", "source": "dispatch"},
    "planned_sessions": {
        "baseline": None, "target": 24, "comparison": "max",
        "unit": "sessions", "source": "sessions"},
}

_DIGEST = re.compile(r"[0-9a-f]{64}")
_PRIVATE_TEXT = re.compile(
    r"(?:^|[\s=])/(?:[^/\s]+/)+[^\s]*|[A-Za-z]:\\|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"(?i:password|secret|credential|api[_-]?key|access[_-]?token)"
)


class WaveMetricsError(ValueError):
    """Wave metrics are incomplete, cumulative, unsafe, or contradictory."""


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WaveMetricsError(f"{label} must be an object")
    return {str(key): copy.deepcopy(item) for key, item in value.items()}


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected.difference(value))
        unknown = sorted(set(value).difference(expected))
        raise WaveMetricsError(
            f"{label} fields are incomplete (missing={missing}, unknown={unknown})")


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise WaveMetricsError(f"{label} must be a sha256 digest")
    return value


def _time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise WaveMetricsError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WaveMetricsError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise WaveMetricsError(f"{label} must include a timezone")
    return parsed


def _number(value: object, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            not math.isfinite(float(value)) or value < 0:
        raise WaveMetricsError(f"{label} must be a finite non-negative number")
    return value


def _redaction_check(value: object) -> None:
    if isinstance(value, str):
        if _PRIVATE_TEXT.search(value):
            raise WaveMetricsError("wave metrics must not expose paths, identity, or secrets")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _redaction_check(str(key))
            _redaction_check(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _redaction_check(item)


def _normalize_sources(raw: object, candidate: str, opened: str,
                       closed: str) -> dict[str, dict[str, Any]]:
    sources = _mapping(raw, "sources")
    _exact_keys(sources, set(SOURCE_NAMES), "sources")
    normalized = {}
    expected = {
        "digest", "candidate_fingerprint", "interval_opened_at",
        "interval_closed_at", "counting",
    }
    for name in SOURCE_NAMES:
        row = _mapping(sources[name], f"source {name}")
        _exact_keys(row, expected, f"source {name}")
        if row["candidate_fingerprint"] != candidate or \
                row["interval_opened_at"] != opened or \
                row["interval_closed_at"] != closed:
            raise WaveMetricsError(f"source {name} is outside the closed candidate interval")
        if row["counting"] != "non-cumulative":
            raise WaveMetricsError(f"source {name} must be non-cumulative")
        normalized[name] = {**row, "digest": _digest(row["digest"], f"source {name}")}
    return normalized


def _result(actual: int | float, definition: Mapping[str, Any],
            active_shards: int) -> tuple[object, bool | None]:
    comparison = definition["comparison"]
    target = definition["target"]
    if comparison == "record":
        return target, None
    if comparison == "dynamic-max":
        dynamic = active_shards + 1
        return dynamic, actual <= dynamic
    if comparison == "max":
        return target, actual <= target
    if comparison == "min":
        return target, actual >= target
    raise WaveMetricsError("unsupported metric comparison")


def seal_wave_receipt(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and seal one exact candidate/run interval.

    Callers supply already closed producer facts.  Unknown fields fail rather
    than being silently retained because arbitrary producer payloads are the
    usual route for paths, host identity, cumulative archives, or reruns to
    leak into a metric receipt.
    """
    value = _mapping(evidence, "wave metrics evidence")
    _exact_keys(value, {
        "schema", "run", "sources", "actuals", "samples", "usage_truth",
        "cleanup", "worktrees", "ceilings", "serializations",
    }, "wave metrics evidence")
    if value["schema"] != EVIDENCE_SCHEMA:
        raise WaveMetricsError("wave metrics evidence schema is unsupported")

    run = _mapping(value["run"], "run")
    _exact_keys(run, {
        "run_fingerprint", "candidate_fingerprint", "status", "opened_at",
        "integration_ready_at", "closed_at",
    }, "run")
    if run["status"] != "closed":
        raise WaveMetricsError("wave metrics require a closed run interval")
    run_fp = _digest(run["run_fingerprint"], "run fingerprint")
    candidate = _digest(run["candidate_fingerprint"], "candidate fingerprint")
    opened = _time(run["opened_at"], "opened_at")
    ready = _time(run["integration_ready_at"], "integration_ready_at")
    closed = _time(run["closed_at"], "closed_at")
    if not opened <= ready <= closed:
        raise WaveMetricsError("integration_ready_at must be inside the closed interval")
    sources = _normalize_sources(
        value["sources"], candidate, run["opened_at"], run["closed_at"])

    actuals = _mapping(value["actuals"], "actuals")
    _exact_keys(actuals, set(METRIC_DEFINITIONS), "actuals")
    samples = _mapping(value["samples"], "samples")
    _exact_keys(samples, set(METRIC_DEFINITIONS), "samples")
    worktrees = _mapping(value["worktrees"], "worktrees")
    _exact_keys(worktrees, {"active_shards", "active_worktrees"}, "worktrees")
    active_shards = int(_number(worktrees["active_shards"], "active shards"))
    if active_shards != worktrees["active_shards"]:
        raise WaveMetricsError("active shards must be an integer")
    if actuals["active_worktrees"] != worktrees["active_worktrees"]:
        raise WaveMetricsError("active worktree metric contradicts worktree evidence")

    metrics = {}
    for name, definition in METRIC_DEFINITIONS.items():
        actual = _number(actuals[name], name)
        sample = _mapping(samples[name], f"sample {name}")
        _exact_keys(sample, {"size", "method"}, f"sample {name}")
        size = _number(sample["size"], f"sample size {name}")
        if size == 0 or not isinstance(sample["method"], str) or not sample["method"].strip():
            raise WaveMetricsError(f"sample {name} must name a non-empty method")
        target, passed = _result(actual, definition, active_shards)
        metrics[name] = {
            "baseline": definition["baseline"], "target": target,
            "actual": actual, "unit": definition["unit"],
            "comparison": definition["comparison"], "passed": passed,
            "sample_size": size, "counting_method": sample["method"],
            "source_digest": sources[definition["source"]]["digest"],
        }

    cleanup = _mapping(value["cleanup"], "cleanup")
    _exact_keys(cleanup, {"leak_count", "status"}, "cleanup")
    leak_count = _number(cleanup["leak_count"], "cleanup leak count")
    if int(leak_count) != leak_count or cleanup["status"] not in {"clean", "attention"}:
        raise WaveMetricsError("cleanup evidence is invalid")
    if metrics["cleanup_leak_count"]["actual"] != leak_count:
        raise WaveMetricsError("cleanup metric contradicts cleanup evidence")

    usage = _mapping(value["usage_truth"], "usage truth")
    _exact_keys(usage, {"billing", "observed", "archive_upper_bound"}, "usage truth")
    billing = _mapping(usage["billing"], "billing truth")
    _exact_keys(billing, {"status", "value", "source_digest"}, "billing truth")
    if billing["status"] not in {"available", "unavailable"} or \
            (billing["status"] == "unavailable" and billing["value"] is not None):
        raise WaveMetricsError("unavailable billing truth cannot claim a value")
    if billing["value"] is not None:
        _number(billing["value"], "billing value")
    observed = _mapping(usage["observed"], "observed usage")
    _exact_keys(observed, {"total_tokens", "uncached_input_tokens", "source_digest"},
                "observed usage")
    archive = _mapping(usage["archive_upper_bound"], "archive upper bound")
    _exact_keys(archive, {"total_tokens", "relation", "source_digest"},
                "archive upper bound")
    for row, label in ((billing, "billing"), (observed, "observed"),
                       (archive, "archive")):
        _digest(row["source_digest"], f"{label} source digest")
        if row["source_digest"] != sources["token_usage"]["digest"]:
            raise WaveMetricsError(f"{label} truth is not bound to token evidence")
    if archive["relation"] != "upper-bound-not-billing":
        raise WaveMetricsError("archive usage must remain an upper bound, not billing truth")
    for key in ("total_tokens", "uncached_input_tokens"):
        _number(observed[key], f"observed {key}")
    _number(archive["total_tokens"], "archive total tokens")
    if observed["total_tokens"] != metrics["token_total_observed"]["actual"] or \
            observed["uncached_input_tokens"] != metrics["token_uncached_observed"]["actual"] or \
            archive["total_tokens"] != metrics["token_archive_upper_bound"]["actual"]:
        raise WaveMetricsError("token metrics contradict their truth classes")

    ceilings_raw = value["ceilings"]
    if not isinstance(ceilings_raw, list) or not ceilings_raw:
        raise WaveMetricsError("wave metrics require explicit ceilings")
    ceilings = []
    names = set()
    unexplained = []
    for item in ceilings_raw:
        row = _mapping(item, "ceiling")
        _exact_keys(row, {"name", "observed", "ceiling", "classification"}, "ceiling")
        name = row["name"]
        if not isinstance(name, str) or not name or name in names:
            raise WaveMetricsError("ceiling names must be unique")
        names.add(name)
        observed_value = _number(row["observed"], f"ceiling {name} observed")
        ceiling_value = _number(row["ceiling"], f"ceiling {name}")
        breached = observed_value >= ceiling_value
        classification = row["classification"]
        if classification is not None and (not isinstance(classification, str)
                                            or not classification.strip()):
            raise WaveMetricsError("ceiling classification is invalid")
        if breached and classification is None:
            unexplained.append(name)
        ceilings.append({**row, "breached": breached})
    if names != set(CEILING_DEFINITIONS):
        raise WaveMetricsError("wave metrics ceilings are incomplete")
    for row in ceilings:
        metric_name, approved_ceiling = CEILING_DEFINITIONS[row["name"]]
        if row["observed"] != metrics[metric_name]["actual"] or \
                row["ceiling"] != approved_ceiling:
            raise WaveMetricsError(
                f"ceiling {row['name']} contradicts approved metric evidence")

    serializations_raw = value["serializations"]
    if not isinstance(serializations_raw, list) or not serializations_raw:
        raise WaveMetricsError("wave metrics must name every serialization")
    serializations = []
    serialization_names = set()
    for item in serializations_raw:
        row = _mapping(item, "serialization")
        _exact_keys(row, {"name", "reason"}, "serialization")
        if any(not isinstance(row[key], str) or not row[key].strip()
               for key in ("name", "reason")) or row["name"] in serialization_names:
            raise WaveMetricsError("serialization names and reasons must be unique and non-empty")
        serialization_names.add(row["name"])
        serializations.append(row)

    signoff = {
        "ready": leak_count == 0 and not unexplained,
        "blocking_reasons": (["owned-cleanup-leaks"] if leak_count else []) +
                            (["unclassified-ceiling-breach"] if unexplained else []),
        "unexplained_ceilings": unexplained,
    }
    material = {
        "schema": RECEIPT_SCHEMA,
        "run": {**run, "run_fingerprint": run_fp,
                "candidate_fingerprint": candidate},
        "sources": sources,
        "metrics": metrics,
        "usage_truth": usage,
        "cleanup": cleanup,
        "worktrees": worktrees,
        "ceilings": ceilings,
        "serializations": serializations,
        "signoff": signoff,
        "redaction": {"paths": "omitted", "host_identity": "omitted",
                      "raw_logs": "omitted"},
    }
    _redaction_check(material)
    return {**material, "fingerprint": content_fingerprint(material)}


def validate_wave_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Return an intact sealed receipt or fail closed on drift or re-sealing."""
    value = _mapping(receipt, "wave metrics receipt")
    if value.get("schema") != RECEIPT_SCHEMA:
        raise WaveMetricsError("wave metrics receipt schema is unsupported")
    fingerprint = value.pop("fingerprint", None)
    if fingerprint != content_fingerprint(value):
        raise WaveMetricsError("wave metrics receipt fingerprint mismatch")
    _redaction_check(value)
    try:
        rebuilt = seal_wave_receipt({
            "schema": EVIDENCE_SCHEMA,
            "run": copy.deepcopy(value["run"]),
            "sources": copy.deepcopy(value["sources"]),
            "actuals": {name: row["actual"]
                        for name, row in value["metrics"].items()},
            "samples": {
                name: {"size": row["sample_size"],
                       "method": row["counting_method"]}
                for name, row in value["metrics"].items()
            },
            "usage_truth": copy.deepcopy(value["usage_truth"]),
            "cleanup": copy.deepcopy(value["cleanup"]),
            "worktrees": copy.deepcopy(value["worktrees"]),
            "ceilings": [
                {key: item[key] for key in (
                    "name", "observed", "ceiling", "classification")}
                for item in value["ceilings"]
            ],
            "serializations": copy.deepcopy(value["serializations"]),
        })
    except (KeyError, TypeError, WaveMetricsError) as exc:
        raise WaveMetricsError(
            "wave metrics receipt semantics are invalid") from exc
    original = {**value, "fingerprint": fingerprint}
    if rebuilt != original:
        raise WaveMetricsError("wave metrics receipt semantics are invalid")
    return original


def consumer_projection(receipt: Mapping[str, Any], *, consumer: str) -> dict[str, Any]:
    """Project one sealed receipt without source reads or metric recounting."""
    if consumer not in {"dashboard", "retro", "engineering", "release"}:
        raise WaveMetricsError("unsupported wave metrics consumer")
    sealed = validate_wave_receipt(receipt)
    material = {
        "schema": PROJECTION_SCHEMA, "consumer": consumer,
        "receipt_fingerprint": sealed["fingerprint"],
        "candidate_fingerprint": sealed["run"]["candidate_fingerprint"],
        "integration_ready_at": sealed["run"]["integration_ready_at"],
        "signoff": copy.deepcopy(sealed["signoff"]),
        "metrics": copy.deepcopy(sealed["metrics"]),
        "source_digests": {name: row["digest"]
                           for name, row in sealed["sources"].items()},
    }
    return {**material, "fingerprint": content_fingerprint(material)}


__all__ = [
    "CEILING_DEFINITIONS", "EVIDENCE_SCHEMA", "METRIC_DEFINITIONS", "PROJECTION_SCHEMA",
    "RECEIPT_SCHEMA", "SOURCE_NAMES", "WaveMetricsError",
    "consumer_projection", "seal_wave_receipt", "validate_wave_receipt",
]
