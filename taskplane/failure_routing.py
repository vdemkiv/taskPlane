"""Canonical, fail-closed routing for governed failure correction.

An observed failure is not authority to edit code.  Correction authority is
derived only from a complete failure record whose classification and route
agree.  The evidence digest binds the record to reproduction evidence; it is
not itself a claim that the implementation is correct.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import TypedDict, cast


FAILURE_RECORD_SCHEMA_ID = "taskplane.failure-record/v1"
FAILURE_ROUTING_SCHEMA_ID = "taskplane.failure-routing/v1"

FAILURE_CLASSES = (
    "product",
    "test",
    "infrastructure",
    "environment",
    "mixed",
    "unknown",
)
FAILURE_ROUTES = (
    "fix",
    "test-correction",
    "infrastructure-recovery",
    "environment-recovery",
    "hold",
)
CLASS_ROUTES = {
    "product": "fix",
    "test": "test-correction",
    "infrastructure": "infrastructure-recovery",
    "environment": "environment-recovery",
    "mixed": "hold",
    "unknown": "hold",
}
FAILURE_STAGES = (
    "product",
    "design",
    "plan",
    "build",
    "execute",
    "fix",
    "evaluate",
    "engineering",
    "em",
    "ci",
    "lifecycle",
    "cleanup",
    "release",
)

_RECORD_FIELDS: frozenset[str] = frozenset({
    "schema",
    "id",
    "source",
    "stage",
    "repro",
    "evidence",
    "evidence_digest",
    "class",
    "reason",
    "owner",
    "cluster",
    "route",
    "candidate",
})
_CANDIDATE_FIELDS: frozenset[str] = frozenset({"id", "fingerprint"})


class CandidateIdentity(TypedDict):
    id: str
    fingerprint: str


FailureRecord = TypedDict("FailureRecord", {
    "schema": str,
    "id": str,
    "source": str,
    "stage": str,
    "repro": str,
    "evidence": dict[str, object],
    "evidence_digest": str,
    "class": str,
    "reason": str,
    "owner": str,
    "cluster": str,
    "route": str,
    "candidate": CandidateIdentity,
})


class FailureRoutingError(ValueError):
    """A failure record cannot authorize a correction route."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _object(
        properties: dict[str, object], required: list[str]
) -> dict[str, object]:
    return {
        "type": "object",
        "required": required,
        "additionalProperties": False,
        "properties": properties,
    }


def failure_record_schema() -> dict[str, object]:
    """Return the portable schema embedded in evaluator contracts."""
    string = {"type": "string", "minLength": 1}
    digest = {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
    }
    candidate = _object({
        "id": string,
        "fingerprint": digest,
    }, ["id", "fingerprint"])
    return {
        "$id": FAILURE_RECORD_SCHEMA_ID,
        **_object({
            "schema": {"const": FAILURE_RECORD_SCHEMA_ID},
            "id": string,
            "source": string,
            "stage": {"enum": list(FAILURE_STAGES)},
            "repro": string,
            "evidence": {"type": "object", "minProperties": 1},
            "evidence_digest": digest,
            "class": {"enum": list(FAILURE_CLASSES)},
            "reason": string,
            "owner": string,
            "cluster": string,
            "route": {"enum": list(FAILURE_ROUTES)},
            "candidate": candidate,
        }, sorted(_RECORD_FIELDS)),
    }


def route_for_class(failure_class: object) -> str:
    """Return the only route a classification may authorize."""
    if not isinstance(failure_class, str):
        raise FailureRoutingError(
            "failure_class",
            f"unsupported failure class {failure_class!r}",
        )
    try:
        return CLASS_ROUTES[failure_class]
    except KeyError:
        raise FailureRoutingError(
            "failure_class",
            f"unsupported failure class {failure_class!r}",
        ) from None


def _nonempty_text(value: object, field: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FailureRoutingError(
            "failure_field", f"{field} must be a non-empty string")
    if value != value.strip():
        raise FailureRoutingError(
            "failure_field", f"{field} must not have surrounding whitespace")
    if len(value) > maximum or any(ord(char) < 32 for char in value):
        raise FailureRoutingError(
            "failure_field", f"{field} is not a safe bounded string")
    return value


def _digest(value: object, field: str) -> str:
    value = _nonempty_text(value, field, maximum=64)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise FailureRoutingError(
            "failure_digest", f"{field} must be a lowercase SHA-256 digest")
    return value


def canonical_evidence_bytes(value: object) -> bytes:
    """Return the portable bytes whose digest is stored in a failure record."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FailureRoutingError(
            "failure_evidence", "failure evidence must be portable JSON"
        ) from exc


def evidence_digest(value: object) -> str:
    """Fingerprint exact evidence bytes using the failure contract encoding."""
    return hashlib.sha256(canonical_evidence_bytes(value)).hexdigest()


def validate_failure_evidence(
        value: object, claimed_digest: object) -> dict[str, object]:
    """Verify that embedded evidence is non-empty and matches its digest."""
    if not isinstance(value, Mapping) or not value:
        raise FailureRoutingError(
            "failure_evidence",
            "failure evidence must be a non-empty JSON object",
        )
    if any(not isinstance(key, str) for key in value):
        raise FailureRoutingError(
            "failure_evidence", "failure evidence keys must be strings"
        )
    evidence = {str(key): deepcopy(item) for key, item in value.items()}
    digest = _digest(claimed_digest, "evidence_digest")
    if evidence_digest(evidence) != digest:
        raise FailureRoutingError(
            "failure_evidence_mismatch",
            "failure evidence does not match evidence_digest",
        )
    return evidence


def validate_candidate_identity(value: object) -> CandidateIdentity:
    if not isinstance(value, Mapping):
        raise FailureRoutingError(
            "candidate_identity", "candidate must be an identity mapping")
    if set(value) != _CANDIDATE_FIELDS:
        raise FailureRoutingError(
            "candidate_identity",
            "candidate must contain exactly id and fingerprint",
        )
    return {
        "id": _nonempty_text(value.get("id"), "candidate.id", maximum=512),
        "fingerprint": _digest(
            value.get("fingerprint"), "candidate.fingerprint"),
    }


def validate_failure_record(
        value: object, *, expected_stage: str | None = None,
        expected_candidate: object | None = None) -> FailureRecord:
    """Validate one record without guessing omitted or contradictory data."""
    if not isinstance(value, Mapping):
        raise FailureRoutingError(
            "failure_record_type", "failure record must be a mapping")
    if set(value) != _RECORD_FIELDS:
        missing = sorted(_RECORD_FIELDS - set(value))
        extra = sorted(set(value) - _RECORD_FIELDS)
        raise FailureRoutingError(
            "failure_record_shape",
            f"failure record has missing={missing} extra={extra}",
        )
    if value.get("schema") != FAILURE_RECORD_SCHEMA_ID:
        raise FailureRoutingError(
            "failure_schema", "failure record has an unsupported schema")

    failure_class = value.get("class")
    route = value.get("route")
    required_route = route_for_class(failure_class)
    if route != required_route:
        raise FailureRoutingError(
            "failure_route",
            f"class {failure_class!r} requires route {required_route!r}",
        )

    stage = value.get("stage")
    if stage not in FAILURE_STAGES:
        raise FailureRoutingError(
            "failure_stage", f"unsupported failure stage {stage!r}")
    if expected_stage is not None and stage != expected_stage:
        raise FailureRoutingError(
            "failure_stage",
            f"failure stage {stage!r} does not match {expected_stage!r}",
        )

    candidate = validate_candidate_identity(value.get("candidate"))
    if expected_candidate is not None:
        expected = validate_candidate_identity(expected_candidate)
        if candidate != expected:
            raise FailureRoutingError(
                "candidate_mismatch",
                "failure record belongs to a different candidate",
            )

    evidence = validate_failure_evidence(
        value.get("evidence"), value.get("evidence_digest"))
    validated: FailureRecord = {
        "schema": FAILURE_RECORD_SCHEMA_ID,
        "id": _nonempty_text(value.get("id"), "id", maximum=512),
        "source": _nonempty_text(
            value.get("source"), "source", maximum=512),
        "stage": cast(str, stage),
        "repro": _nonempty_text(value.get("repro"), "repro"),
        "evidence": evidence,
        "evidence_digest": evidence_digest(evidence),
        "class": cast(str, failure_class),
        "reason": _nonempty_text(value.get("reason"), "reason"),
        "owner": _nonempty_text(value.get("owner"), "owner", maximum=512),
        "cluster": _nonempty_text(
            value.get("cluster"), "cluster", maximum=512),
        "route": cast(str, route),
        "candidate": candidate,
    }
    return validated


def validate_failure_records(
        values: Iterable[object], *, require_nonempty: bool = True,
        expected_stage: str | None = None,
        expected_candidate: object | None = None) -> list[FailureRecord]:
    """Validate a same-candidate inventory and reject ambiguous identity."""
    if isinstance(values, (str, bytes, dict)) or not isinstance(values, Iterable):
        raise FailureRoutingError(
            "failure_inventory_type", "failure inventory must be an iterable")
    rows = list(values)
    if require_nonempty and not rows:
        raise FailureRoutingError(
            "empty_failure_inventory", "failure inventory must not be empty")

    validated: list[FailureRecord] = []
    seen: set[str] = set()
    candidate: object | None = expected_candidate
    for row in rows:
        item = validate_failure_record(
            row,
            expected_stage=expected_stage,
            expected_candidate=candidate,
        )
        if item["id"] in seen:
            raise FailureRoutingError(
                "duplicate_failure", f"duplicate failure id {item['id']!r}")
        seen.add(item["id"])
        if candidate is None:
            candidate = item["candidate"]
        validated.append(item)
    return validated


def route_failure_records(values: Iterable[object]) -> dict[str, object]:
    """Admit complete records and expose only class-owned correction routes.

    Any mixed or unknown record holds the entire inventory.  Product Fix is
    exclusive: every record in the inventory must be a product failure.
    Otherwise each known class retains its own route; test, infrastructure,
    and environment failures can authorize only their owned correction path.
    """
    records = validate_failure_records(values)
    grouped: dict[str, list[str]] = {
        route: [] for route in FAILURE_ROUTES}
    for record in records:
        grouped[record["route"]].append(record["id"])

    hold = bool(grouped["hold"])
    active = [route for route, ids in grouped.items() if ids]
    exclusive_product = not hold and active == ["fix"]
    if hold:
        next_route = "hold"
    elif len(active) == 1:
        next_route = active[0]
    else:
        next_route = "split"

    return {
        "schema": FAILURE_ROUTING_SCHEMA_ID,
        "candidate": deepcopy(records[0]["candidate"]),
        "records": deepcopy(records),
        "routes": deepcopy(grouped),
        "next": next_route,
        "admitted": not hold,
        "product_fix_allowed": exclusive_product,
        "test_correction_allowed": not hold and bool(
            grouped["test-correction"]),
        "infrastructure_recovery_required": not hold and bool(
            grouped["infrastructure-recovery"]),
        "environment_recovery_required": not hold and bool(
            grouped["environment-recovery"]),
        "hold_required": hold,
    }
