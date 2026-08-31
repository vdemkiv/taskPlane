"""One-inventory, one-wave correction policy for red CI candidates."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


MATRIX_SCHEMA = "taskplane.ci-matrix/v1"
INVENTORY_SCHEMA = "taskplane.ci-failure-inventory/v1"
WAVE_SCHEMA = "taskplane.ci-correction-wave/v1"
STABILIZATION_SCHEMA = "taskplane.plan-return-consolidation/v1"
FAILURE_CLASSES = ("product", "test", "infrastructure", "environment")
CLASSIFICATION_FIELDS = ("class", "reason", "owner", "cluster")

_DIGEST = re.compile(r"[0-9a-f]{64}")


class FailureBatchError(ValueError):
    """Failure evidence cannot authorize a correction wave."""


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
        raise FailureBatchError("failure evidence must be portable JSON") from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FailureBatchError(f"{label} must be an object")
    return {str(key): copy.deepcopy(item) for key, item in value.items()}


def _strings(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise FailureBatchError(f"{label} must be a list")
    if not allow_empty and not value:
        raise FailureBatchError(f"{label} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise FailureBatchError(f"{label} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise FailureBatchError(f"{label} must be unique")
    return list(value)


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise FailureBatchError(f"{label} must be a SHA-256 digest")
    return value


def build_failure_inventory(
    matrix: Mapping[str, Any], classifications: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Join every direct red once with one complete closed classification."""

    raw = _mapping(matrix, "red matrix")
    if raw.get("schema") != MATRIX_SCHEMA or raw.get("status") != "red":
        raise FailureBatchError("failure inventory requires one red CI matrix")
    candidate = _digest(raw.get("candidate_fingerprint"), "candidate fingerprint")
    if raw.get("classification_receipt"):
        raise FailureBatchError("a red matrix must be classified exactly once")
    failures_raw = raw.get("failures")
    if not isinstance(failures_raw, list) or not failures_raw:
        raise FailureBatchError("red matrix must enumerate every direct failure")
    direct: list[dict[str, Any]] = []
    failure_ids: set[str] = set()
    failing_cells: set[str] = set()
    for value in failures_raw:
        row = _mapping(value, "direct failure")
        failure_id = row.get("id")
        if not isinstance(failure_id, str) or not failure_id or failure_id in failure_ids:
            raise FailureBatchError("direct failure ids must be non-empty and unique")
        failure_ids.add(failure_id)
        for field in ("cell", "selector"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise FailureBatchError(f"direct failure {failure_id} needs {field}")
        _digest(row.get("output_digest"), f"direct failure {failure_id} output")
        failing_cells.add(row["cell"])
        direct.append(row)

    if not isinstance(classifications, Sequence) or isinstance(classifications, (str, bytes)):
        raise FailureBatchError("classifications must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for value in classifications:
        classification = _mapping(value, "failure classification")
        failure_id = classification.get("id")
        if not isinstance(failure_id, str) or not failure_id or failure_id in by_id:
            raise FailureBatchError("classification ids must be non-empty and unique")
        by_id[failure_id] = classification
    if set(by_id) != failure_ids:
        missing = sorted(failure_ids - set(by_id))
        extra = sorted(set(by_id) - failure_ids)
        detail = ", ".join(missing + extra)
        raise FailureBatchError(
            "one complete classified inventory is required"
            + (f": {detail}" if detail else "")
        )

    failures: list[dict[str, Any]] = []
    for row in direct:
        classification = by_id[row["id"]]
        missing = [field for field in CLASSIFICATION_FIELDS if not classification.get(field)]
        if missing:
            raise FailureBatchError(
                f"failure {row['id']} must be classified before correction; missing "
                + ", ".join(missing)
            )
        if classification["class"] not in FAILURE_CLASSES:
            raise FailureBatchError(f"failure {row['id']} has an unknown class")
        failures.append(
            {
                **row,
                **{field: classification[field] for field in CLASSIFICATION_FIELDS},
                "correction": copy.deepcopy(classification.get("correction") or {}),
            }
        )

    green_cells = _strings(raw.get("green_cells", []), "unchanged green cells", allow_empty=True)
    if failing_cells.intersection(green_cells):
        raise FailureBatchError("a cell cannot be both failed and unchanged green")
    payload = {
        "schema": INVENTORY_SCHEMA,
        "candidate_fingerprint": candidate,
        "matrix_schema": MATRIX_SCHEMA,
        "complete": True,
        "classification_passes": 1,
        "failures": failures,
        "unchanged_green_cells": green_cells,
    }
    return {**payload, "fingerprint": _fingerprint(payload)}


def _validate_inventory(value: Mapping[str, Any]) -> dict[str, Any]:
    inventory = _mapping(value, "failure inventory")
    if inventory.get("schema") != INVENTORY_SCHEMA or inventory.get("complete") is not True:
        raise FailureBatchError("correction requires a complete failure inventory")
    if inventory.get("classification_passes") != 1:
        raise FailureBatchError("failure inventory must be classified exactly once")
    expected = _fingerprint({key: item for key, item in inventory.items() if key != "fingerprint"})
    if inventory.get("fingerprint") != expected:
        raise FailureBatchError("failure inventory fingerprint is stale")
    _digest(inventory.get("candidate_fingerprint"), "candidate fingerprint")
    return inventory


def build_correction_wave(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Authorize one correction wave after all classes and changes are known."""

    checked = _validate_inventory(inventory)
    if checked.get("correction_wave_receipt"):
        raise FailureBatchError("a failure inventory can authorize exactly one correction wave")
    failures = checked.get("failures")
    if not isinstance(failures, list) or not failures:
        raise FailureBatchError("correction wave requires direct failures")

    allowed_kinds = {
        "product": {"product-bytes", "evidence-bytes"},
        "test": {"product-bytes", "evidence-bytes"},
        "infrastructure": {"infrastructure-recovery"},
        "environment": {"environment-recovery"},
    }
    rerun_cells: list[str] = []
    clusters: dict[str, list[str]] = {}
    corrections: list[dict[str, str]] = []
    for value in failures:
        failure = _mapping(value, "classified failure")
        correction = _mapping(failure.get("correction"), f"failure {failure.get('id')} correction")
        kind = correction.get("kind")
        condition = correction.get("condition")
        failure_class = failure.get("class")
        if (
            kind not in allowed_kinds.get(str(failure_class), set())
            or not isinstance(condition, str)
            or not condition.strip()
        ):
            raise FailureBatchError(
                f"failure {failure.get('id')} needs changed product or evidence bytes "
                "or a named recovered infrastructure/environment condition"
            )
        if kind in {"product-bytes", "evidence-bytes"}:
            before = _digest(
                correction.get("before"),
                f"failure {failure.get('id')} prior correction evidence",
            )
            after = _digest(
                correction.get("after"),
                f"failure {failure.get('id')} changed correction evidence",
            )
            if before == after:
                raise FailureBatchError(
                    f"failure {failure.get('id')} has no changed product or evidence bytes"
                )
        else:
            _digest(
                correction.get("receipt"),
                f"failure {failure.get('id')} recovery receipt",
            )
        cell = str(failure.get("cell") or "")
        cluster = str(failure.get("cluster") or "")
        if not cell or not cluster:
            raise FailureBatchError("classified failures need cell and cluster")
        if cell not in rerun_cells:
            rerun_cells.append(cell)
        clusters.setdefault(cluster, []).append(str(failure.get("id") or ""))
        corrections.append(
            {
                "failure": str(failure["id"]),
                "kind": str(kind),
                "condition": condition,
                "evidence_fingerprint": _fingerprint(correction),
            }
        )

    cited = _strings(
        checked.get("unchanged_green_cells", []),
        "unchanged green cells",
        allow_empty=True,
    )
    if set(cited).intersection(rerun_cells):
        raise FailureBatchError("unchanged green cells must not rerun")
    payload = {
        "schema": WAVE_SCHEMA,
        "candidate_fingerprint": checked["candidate_fingerprint"],
        "failure_inventory_fingerprint": checked["fingerprint"],
        "wave_count": 1,
        "rerun_cells": rerun_cells,
        "cited_unchanged_green": cited,
        "clusters": clusters,
        "corrections": corrections,
    }
    return {**payload, "fingerprint": _fingerprint(payload)}


def consolidate_plan_returns(
    returns: Sequence[Mapping[str, Any]],
    *,
    existing_successors: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """On the third return, create or reuse one coupled stabilization task."""

    if not isinstance(returns, Sequence) or isinstance(returns, (str, bytes)):
        raise FailureBatchError("Plan returns must be a list")
    rows: list[dict[str, Any]] = []
    ids: list[str] = []
    surfaces: set[str] = set()
    for value in returns:
        row = _mapping(value, "Plan return")
        return_id = row.get("id")
        if not isinstance(return_id, str) or not return_id:
            raise FailureBatchError("Plan return ids must be non-empty")
        ids.append(return_id)
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise FailureBatchError(f"Plan return {return_id} needs a reason")
        surfaces.update(_strings(row.get("coupled_surfaces"), f"Plan return {return_id} surfaces"))
        rows.append(row)
    if len(ids) != len(set(ids)):
        raise FailureBatchError("Plan return ids must be unique")

    existing = list(existing_successors or [])
    if len(rows) < 3:
        if existing:
            raise FailureBatchError("stabilization cannot precede the third Plan return")
        payload = {
            "schema": STABILIZATION_SCHEMA,
            "return_count": len(rows),
            "consolidated": False,
            "created": False,
            "successors": [],
        }
        return {**payload, "fingerprint": _fingerprint(payload)}

    successor = {
        "id": "PLAN-STABILIZATION",
        "type": "stabilization",
        "status": "pending",
        "predecessors": ids,
        "coupled_surfaces": sorted(surfaces),
    }
    if existing:
        normalized = [_mapping(value, "stabilization successor") for value in existing]
        if normalized != [successor]:
            raise FailureBatchError("the third Plan return permits one stabilization successor")
        created = False
    else:
        normalized = [successor]
        created = True
    payload = {
        "schema": STABILIZATION_SCHEMA,
        "return_count": len(rows),
        "consolidated": True,
        "created": created,
        "successors": normalized,
    }
    return {**payload, "fingerprint": _fingerprint(payload)}


__all__ = [
    "CLASSIFICATION_FIELDS",
    "FAILURE_CLASSES",
    "FailureBatchError",
    "build_correction_wave",
    "build_failure_inventory",
    "consolidate_plan_returns",
]
