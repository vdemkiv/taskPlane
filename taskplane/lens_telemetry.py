"""Post-review per-lens quality telemetry from sealed canonical revisions."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

import spend


SCHEMA = "taskplane.lens-quality-telemetry/v1"
DEFINITION_VERSION = 1


def _nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) \
        and value >= 0 else 0


def _finding_identity(finding: dict[str, Any]) -> str:
    supplied = str(
        finding.get("fingerprint") or finding.get("id") or "").strip()
    if supplied:
        return supplied
    # Canonical producer findings predate explicit ids.  Derive a semantic
    # identity without lens/provenance so overlap remains comparable.
    material = {key: finding.get(key) for key in
                ("kind", "file", "title", "scenario")}
    if not any(value not in (None, "") for value in material.values()):
        return ""
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _usage_projection(raw: dict[str, Any]) -> dict[str, Any]:
    provider = str(raw.get("provider") or "unknown")
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    rates = raw.get("rates_per_million")
    projected = spend.provider_cost_projection(
        usage, provider=provider,
        rates_per_million=rates if isinstance(rates, dict) else None)
    return projected


def build_lens_telemetry(
        sealed_revision: dict[str, Any], *,
        lifecycle: dict[str, dict[str, Any]] | None = None,
        usage_by_lens: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Aggregate quality metrics without exposing findings across lenses.

    Only a sealed canonical revision is accepted.  The returned object contains
    counts and availability facts, never finding content or a review verdict;
    consequently observing telemetry cannot affect routing floors or outcome.
    """
    if (not isinstance(sealed_revision, dict)
            or sealed_revision.get("sealed") is not True
            or _nonnegative_int(sealed_revision.get("canonical_revision")) < 1):
        raise ValueError("lens telemetry requires a sealed canonical revision")

    lifecycle_rows = lifecycle if isinstance(lifecycle, dict) else {}
    usage_rows = usage_by_lens if isinstance(usage_by_lens, dict) else {}
    raw_slots = sealed_revision.get("slots")
    slots = raw_slots if isinstance(raw_slots, list) else []
    raw_findings = sealed_revision.get("findings")
    findings = raw_findings if isinstance(raw_findings, list) else []

    lens_names = {str(row.get("lens") or "").strip() for row in slots
                  if isinstance(row, dict)}
    lens_names.update(str(row.get("lens") or "").strip() for row in findings
                      if isinstance(row, dict))
    lens_names.update(str(name).strip() for name in lifecycle_rows)
    lens_names.update(str(name).strip() for name in usage_rows)
    lens_names.discard("")

    identities_by_lens: dict[str, list[str]] = defaultdict(list)
    admissible_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in findings:
        if not isinstance(raw, dict):
            continue
        lens = str(raw.get("lens") or "").strip()
        if not lens:
            continue
        all_rows[lens].append(raw)
        identity = _finding_identity(raw)
        if raw.get("admissible") is not False and identity:
            admissible_rows[lens].append(raw)
            identities_by_lens[lens].append(identity)

    lens_presence = Counter()
    for identities in identities_by_lens.values():
        lens_presence.update(set(identities))

    metrics = {}
    for lens in sorted(lens_names):
        lens_slots = [row for row in slots if isinstance(row, dict)
                      and str(row.get("lens") or "").strip() == lens]
        routing = {
            key: sum(row.get(key) is True for row in lens_slots)
            for key in ("eligible", "selected", "promoted", "collected")
        }
        rows = all_rows[lens]
        admissible = admissible_rows[lens]
        identities = identities_by_lens[lens]
        distinct = set(identities)
        overlap = {identity for identity in distinct
                   if lens_presence[identity] > 1}
        unique = distinct - overlap
        finding_metrics = {
            "admissible": len(admissible),
            "confirmed": sum(row.get("confirmed") is True
                             for row in admissible),
            "unique": len(unique),
            "overlap": len(overlap),
            "duplicate": len(identities) - len(distinct),
            "invalidated": sum(row.get("invalidated") is True for row in rows),
            "false_positive": sum(row.get("false_positive") is True
                                  for row in rows),
        }
        life = lifecycle_rows.get(lens)
        life = life if isinstance(life, dict) else {}
        infrastructure_available = life.get("infrastructure_available") is True
        unavailable_reason = None if infrastructure_available else str(
            life.get("unavailable_reason") or "infrastructure telemetry unavailable")
        projected = _usage_projection(usage_rows.get(lens, {}))
        metrics[lens] = {
            "routing": routing,
            "findings": finding_metrics,
            "execution": {
                "retries": _nonnegative_int(life.get("retries")),
                "repairs": _nonnegative_int(life.get("repairs")),
                "latency_ms": _nonnegative_int(life.get("latency_ms")),
                "infrastructure": {"available": infrastructure_available,
                                   "reason": unavailable_reason},
            },
            "tokens": projected["usage"],
            "cost": projected["cost"],
        }

    return {
        "schema": SCHEMA,
        "source": {"canonical_revision": sealed_revision["canonical_revision"],
                   "sealed": True},
        "definitions": {
            "version": DEFINITION_VERSION,
            "denominators": {
                "selected": "eligible slots",
                "promoted": "eligible slots",
                "collected": "selected or promoted slots",
                "confirmed": "admissible findings",
                "unique": "distinct admissible finding identities",
                "overlap": "distinct admissible identities present in multiple lenses",
            },
        },
        "lenses": metrics,
    }
