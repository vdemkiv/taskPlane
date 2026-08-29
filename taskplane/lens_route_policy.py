"""Pure focused lens-route policy.

This module owns canonicalization and the closed routing decision contract.
It deliberately performs no filesystem, process, network, clock, or global
state access; stage adapters own context assembly and execution.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, cast


CONTEXT_SCHEMA = "taskplane.lens-route-context/v1"
DECISION_SCHEMA = "taskplane.lens-route-policy/v1"
POLICY_VERSION = "focused-routing/v1"
DISPOSITIONS = frozenset({
    "execute_deep", "execute_light", "covered_by", "not_applicable",
})
ROUTED_STAGES = frozenset({"product", "design", "plan", "evaluate"})
_BOUNDED_STAGES = frozenset({"plan", "evaluate"})
MAX_ARTIFACT_BYTES = 128 * 1024


class LensRoutePolicyError(ValueError):
    """The context or route violates the closed focused-routing contract."""


def _json_value(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LensRoutePolicyError(f"{path}: numbers must be finite")
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise LensRoutePolicyError(
                    f"{path}: JSON object keys must be strings")
            out[key] = _json_value(item, f"{path}.{key}")
        return out
    if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)):
        return [_json_value(item, f"{path}[{index}]")
                for index, item in enumerate(value)]
    raise LensRoutePolicyError(
        f"{path}: value of type {type(value).__name__} is not JSON data")


def canonical_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes after closed value validation."""
    normalized = _json_value(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def catalog_fingerprint(catalog: Sequence[Any]) -> str:
    ids, material = _catalog(catalog)
    return fingerprint({"schema": "taskplane.lens-catalog/v1",
                        "ids": ids, "definitions": material})


def _catalog(catalog: Sequence[Any] | None) -> tuple[list[str], list[Any]]:
    material = list(catalog or ())
    ids = [item if isinstance(item, str) else item.get("id")
           if isinstance(item, Mapping) else None for item in material]
    if len(ids) != 26 or any(not isinstance(item, str) or not item
                             for item in ids) or len(set(ids)) != len(ids):
        raise LensRoutePolicyError(
            "catalog must contain exactly 26 unique non-empty lens ids")
    return cast(list[str], ids), cast(list[Any], _json_value(material))


def _catalog_ids(catalog: Sequence[Any] | None) -> list[str]:
    return _catalog(catalog)[0]


def _strings(value: Any, *, field: str,
             allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value):
        raise LensRoutePolicyError(f"{field} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise LensRoutePolicyError(f"{field} requires evidence")
    return [cast(str, item) for item in value]


def _validate_context(context: Any, ids: list[str], catalog_fp: str,
                      policy_version: str) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise LensRoutePolicyError("context must be an object")
    normalized = cast(dict[str, Any], _json_value(context))
    if normalized.get("schema") != CONTEXT_SCHEMA:
        raise LensRoutePolicyError(f"context schema must be {CONTEXT_SCHEMA}")
    stage = normalized.get("stage")
    if stage not in ROUTED_STAGES:
        raise LensRoutePolicyError(
            "context stage must be product, design, plan, or evaluate")
    if not isinstance(normalized.get("target"), str) or not normalized["target"]:
        raise LensRoutePolicyError("context target must be a non-empty string")
    if normalized.get("policy_version") != policy_version:
        raise LensRoutePolicyError("context policy version mismatch")
    if normalized.get("catalog_fingerprint") != catalog_fp:
        raise LensRoutePolicyError("context catalog fingerprint mismatch")
    return normalized


def _validate_signal_rows(signal_rows: Any,
                          ids: list[str]) -> list[dict[str, Any]]:
    if not isinstance(signal_rows, Sequence) or isinstance(
            signal_rows, (str, bytes, bytearray)):
        raise LensRoutePolicyError("signal rows must be an array")
    indexed: dict[str, dict[str, Any]] = {}
    for raw in signal_rows:
        if not isinstance(raw, Mapping):
            raise LensRoutePolicyError("each signal row must be an object")
        row = cast(dict[str, Any], _json_value(raw))
        lens_id = row.get("id")
        if lens_id not in ids:
            raise LensRoutePolicyError(f"unknown signal lens: {lens_id!r}")
        if lens_id in indexed:
            raise LensRoutePolicyError(f"duplicate signal lens: {lens_id}")
        verdict = row.get("verdict")
        if verdict not in {"deep", "light", "n/a"}:
            raise LensRoutePolicyError(
                f"lens {lens_id}: unsupported signal verdict {verdict!r}")
        score = row.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) \
                or not math.isfinite(float(score)) or not 0 <= score <= 1:
            raise LensRoutePolicyError(
                f"lens {lens_id}: score must be finite and within 0..1")
        evidence = _strings(row.get("evidence"), field=f"lens {lens_id} evidence",
                            allow_empty=True)
        negative = _strings(
            row.get("negative_evidence"),
            field=f"lens {lens_id} negative evidence", allow_empty=True)
        if verdict == "n/a" and not negative:
            raise LensRoutePolicyError(
                f"lens {lens_id}: n/a requires negative evidence")
        if verdict != "n/a" and not evidence:
            raise LensRoutePolicyError(
                f"lens {lens_id}: applicable verdict requires evidence")
        risk_group = row.get("risk_group", lens_id)
        if not isinstance(risk_group, str) or not risk_group.strip():
            raise LensRoutePolicyError(
                f"lens {lens_id}: risk_group must be a non-empty string")
        row["risk_group"] = risk_group
        mandatory = row.get("mandatory", False)
        if not isinstance(mandatory, bool):
            raise LensRoutePolicyError(
                f"lens {lens_id}: mandatory must be a boolean")
        row["mandatory"] = mandatory
        indexed[lens_id] = row
    if set(indexed) != set(ids):
        missing = [lens_id for lens_id in ids if lens_id not in indexed]
        raise LensRoutePolicyError(
            "signal rows must name every catalog lens exactly once; missing: "
            + ", ".join(missing))
    return [indexed[lens_id] for lens_id in ids]


def _rank(row: Mapping[str, Any]) -> tuple[float, bool, str]:
    return (-float(row["score"]), not bool(row.get("mandatory")), row["id"])


def _select(rows: list[dict[str, Any]], context: dict[str, Any]
            ) -> tuple[list[dict[str, Any]], dict[str, str]]:
    stage = context["stage"]
    mandatory: set[str] = {
        str(lens_id) for lens_id in context.get("mandatory_lenses") or ()}
    if stage == "design":
        mandatory.add("solution-design")
    known = {row["id"] for row in rows}
    unknown = sorted(mandatory - known)
    if unknown:
        raise LensRoutePolicyError(
            "mandatory_lenses names unknown lenses: " + ", ".join(unknown))
    for row in rows:
        if row["id"] in mandatory:
            row["mandatory"] = True
        if row["mandatory"] and row["verdict"] == "n/a":
            raise LensRoutePolicyError(
                f"mandatory lens {row['id']} lacks positive evidence")

    positive = [row for row in rows if row["verdict"] != "n/a"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in positive:
        groups.setdefault(row["risk_group"], []).append(row)

    selected: list[dict[str, Any]] = []
    covered: dict[str, str] = {}
    for group_rows in groups.values():
        floors = sorted((row for row in group_rows if row["mandatory"]),
                        key=_rank)
        winners = floors or [sorted(group_rows, key=_rank)[0]]
        selected.extend(winners)
        winner = winners[0]
        for row in group_rows:
            if row not in winners:
                covered[row["id"]] = winner["id"]
    selected.sort(key=_rank)

    if stage in _BOUNDED_STAGES:
        mandatory_selected = [row for row in selected if row["mandatory"]]
        if len(mandatory_selected) > 4:
            return selected, covered
        selected = selected[:4]
        selected_ids = {row["id"] for row in selected}
        # A lower-ranked positive risk that is outside the normal cap is
        # explicitly covered by the fourth selected representative. A stage
        # adapter may instead split before invoking this policy.
        if selected:
            cap_owner = selected[-1]["id"]
            for row in positive:
                if row["id"] not in selected_ids and row["id"] not in covered:
                    covered[row["id"]] = cap_owner
        trivial = context.get("trivial_target")
        if len(selected) < 3:
            if not isinstance(trivial, Mapping) or trivial.get("trivial") is not True:
                raise LensRoutePolicyError(
                    f"non-trivial {stage} route must select at least 3 lenses")
            _strings(trivial.get("negative_evidence"),
                     field="trivial_target negative_evidence")
    return selected, covered


def _lens_input_fingerprint(context: Mapping[str, Any],
                            row: Mapping[str, Any], policy_version: str,
                            catalog_fp: str) -> str:
    relevant = row.get("fingerprint_inputs")
    if relevant is None:
        relevant = {"context": context, "signal": row}
    return fingerprint({
        "schema": "taskplane.lens-input/v1",
        "policy_version": policy_version,
        "catalog_fingerprint": catalog_fp,
        "lens": row["id"],
        "inputs": relevant,
    })


def build_route(context: Any, signal_rows: Any, catalog: Sequence[Any],
                *, policy_version: str = POLICY_VERSION) -> dict[str, Any]:
    """Build and validate one deterministic focused route decision."""
    ids = _catalog_ids(catalog)
    catalog_fp = catalog_fingerprint(catalog)
    ctx = _validate_context(context, ids, catalog_fp, policy_version)
    rows = _validate_signal_rows(signal_rows, ids)
    selected_rows, covered = _select(rows, ctx)
    selected_ids = [row["id"] for row in selected_rows]
    mandatory_ids = [row["id"] for row in selected_rows if row["mandatory"]]
    overflow = ctx["stage"] in _BOUNDED_STAGES and len(mandatory_ids) > 4

    dispositions: list[dict[str, Any]] = []
    selected_set = set(selected_ids)
    for row in rows:
        lens_id = row["id"]
        if lens_id in selected_set:
            disposition = ("execute_deep" if row["verdict"] == "deep"
                           else "execute_light")
            entry = {"lens": lens_id, "disposition": disposition,
                     "evidence": list(row["evidence"]),
                     "reason": f"selected:{row['risk_group']}"}
        elif lens_id in covered:
            entry = {"lens": lens_id, "disposition": "covered_by",
                     "covered_by": covered[lens_id],
                     "evidence": list(row["evidence"]),
                     "reason": f"duplicate-risk:{row['risk_group']}"}
        else:
            entry = {"lens": lens_id, "disposition": "not_applicable",
                     "negative_evidence": list(row["negative_evidence"]),
                     "reason": "no-positive-signal"}
        dispositions.append(entry)

    decision: dict[str, Any] = {
        "schema": DECISION_SCHEMA,
        "status": ("expanded_approval_required" if overflow else "ready"),
        "stage": ctx["stage"],
        "target": ctx["target"],
        "policy_version": policy_version,
        "catalog_fingerprint": catalog_fp,
        "context_fingerprint": fingerprint(ctx),
        "selected": selected_ids,
        "dispatchable_selected": [] if overflow else selected_ids,
        "dispositions": dispositions,
        "lens_input_fingerprints": {
            row["id"]: _lens_input_fingerprint(
                ctx, row, policy_version, catalog_fp) for row in rows
        },
    }
    if "trivial_target" in ctx:
        decision["trivial_target"] = ctx["trivial_target"]
    if overflow:
        decision["overflow"] = {
            "reason": "more-than-four-independent-mandatory-risks",
            "mandatory_lenses": mandatory_ids,
            "additional_lenses": mandatory_ids[4:],
            "requires": "split-or-authenticated-expanded-route",
        }
    decision["route_fingerprint"] = fingerprint(decision)
    validate_route(decision, catalog)
    if len(canonical_bytes(decision)) > MAX_ARTIFACT_BYTES:
        raise LensRoutePolicyError("route artifact exceeds 128 KiB")
    return decision


def validate_route(route: Any,
                   catalog: Sequence[Any]) -> Mapping[str, Any]:
    """Validate an untrusted route artifact and return it unchanged."""
    ids = _catalog_ids(catalog)
    if not isinstance(route, Mapping):
        raise LensRoutePolicyError("route must be an object")
    if route.get("schema") != DECISION_SCHEMA:
        raise LensRoutePolicyError(f"route schema must be {DECISION_SCHEMA}")
    if route.get("stage") not in ROUTED_STAGES:
        raise LensRoutePolicyError("route stage is unsupported")
    if route.get("policy_version") != POLICY_VERSION and not isinstance(
            route.get("policy_version"), str):
        raise LensRoutePolicyError("route policy version is invalid")
    if route.get("catalog_fingerprint") != catalog_fingerprint(catalog):
        raise LensRoutePolicyError("route catalog fingerprint mismatch")

    ledger = route.get("dispositions")
    if not isinstance(ledger, list) or [
            row.get("lens") if isinstance(row, Mapping) else None
            for row in ledger] != ids:
        raise LensRoutePolicyError(
            "disposition ledger must match catalog order exactly once")
    by_id: dict[str, Mapping[str, Any]] = {
        row["lens"]: row for row in ledger}
    executing: list[str] = []
    for row in ledger:
        disposition = row.get("disposition")
        lens_id = row["lens"]
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise LensRoutePolicyError(
                f"lens {lens_id}: disposition reason requires evidence")
        if disposition not in DISPOSITIONS:
            raise LensRoutePolicyError(
                f"lens {lens_id}: unsupported disposition {disposition!r}")
        if disposition in {"execute_deep", "execute_light"}:
            _strings(row.get("evidence"), field=f"lens {lens_id} evidence")
            executing.append(lens_id)
        elif disposition == "not_applicable":
            _strings(row.get("negative_evidence"),
                     field=f"lens {lens_id} negative evidence")
        else:
            _strings(row.get("evidence"), field=f"lens {lens_id} evidence")
            target = row.get("covered_by")
            if target not in by_id or target == lens_id:
                raise LensRoutePolicyError(
                    f"lens {lens_id}: covered_by target is invalid")

    selected = route.get("selected")
    if not isinstance(selected, list) or len(set(selected)) != len(selected) \
            or set(executing) != set(selected):
        raise LensRoutePolicyError(
            "selected set must exactly match execute dispositions in order")

    for lens_id, row in by_id.items():
        if row["disposition"] != "covered_by":
            continue
        seen = {lens_id}
        target = row["covered_by"]
        while by_id[target]["disposition"] == "covered_by":
            if target in seen:
                raise LensRoutePolicyError("covered_by cycle detected")
            seen.add(target)
            target = by_id[target].get("covered_by")
            if target not in by_id:
                raise LensRoutePolicyError("covered_by target is invalid")
        if target not in selected:
            raise LensRoutePolicyError(
                f"lens {lens_id}: covered_by must terminate at a selected lens")

    dispatchable = route.get("dispatchable_selected")
    if route.get("status") == "ready":
        if dispatchable != selected:
            raise LensRoutePolicyError(
                "ready route dispatchable set must equal selected set")
    elif route.get("status") == "expanded_approval_required":
        if dispatchable != [] or not isinstance(route.get("overflow"), Mapping):
            raise LensRoutePolicyError(
                "overflow route must be non-dispatchable and evidenced")
    else:
        raise LensRoutePolicyError("route status is unsupported")

    fingerprints = route.get("lens_input_fingerprints")
    if not isinstance(fingerprints, Mapping) or set(fingerprints) != set(ids) \
            or any(not isinstance(value, str) or len(value) != 64
                   for value in fingerprints.values()):
        raise LensRoutePolicyError(
            "lens input fingerprints must cover catalog order exactly")
    claimed = route.get("route_fingerprint")
    if not isinstance(claimed, str) or len(claimed) != 64:
        raise LensRoutePolicyError("route fingerprint is missing or invalid")
    material = dict(route)
    material.pop("route_fingerprint", None)
    if fingerprint(material) != claimed:
        raise LensRoutePolicyError("route fingerprint mismatch")
    return cast(Mapping[str, Any], route)
