"""Canonical immutable delivery stages and their lifecycle transitions.

This module owns the pure ``taskplane.stage/v1`` value contract.  Persistence
and locking belong to :mod:`taskplane.run_store`; the helpers here make every
prospective head deterministic and fully validated before it can be indexed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import copy
from datetime import datetime
import json
import math
import re
from typing import Final, TypeAlias

if __package__:
    from . import review_evidence
    from . import storage as runtime_storage
else:
    import review_evidence
    import storage as runtime_storage


SCHEMA: Final[str] = "taskplane.stage/v1"
SUMMARY_SCHEMA: Final[str] = "taskplane.stage-summary/v1"
PROJECTION_SCHEMA: Final[str] = "taskplane.active-stage-projection/v1"
LINEAGE_SCHEMA: Final[str] = "taskplane.stage-lineage/v1"
AUTHORITY_SCHEMA: Final[str] = "taskplane.stage-authority-binding/v1"
MAX_INPUT_MANIFEST_BYTES: Final[int] = 64 * 1024
MAX_STAGE_SUMMARY_BYTES: Final[int] = 16 * 1024
MAX_COLLECTION_ITEMS: Final[int] = 64
MAX_REASON_BYTES: Final[int] = 4 * 1024
TERMINAL_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        "done",
        "closed",
        "discarded",
    }
)
STAGE_STATES: Final[frozenset[str]] = frozenset({"active", "terminal"})

JsonObject: TypeAlias = dict[str, object]
_IDENTIFIER: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_KIND: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_FINGERPRINT: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
# Preserve legacy IDs and namespaced/versioned IDs within the same body bound.
_CONTRACT: Final[re.Pattern[str]] = re.compile(
    r"^contract:(?=.{1,128}$)(?:[a-z][a-z0-9-]*|"
    r"[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+/v[1-9][0-9]*)$"
)
_PORTABLE_REFERENCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "kind",
        "fingerprint",
        "digest",
        "bytes",
        "locator",
        "transport",
    }
)
_REQUIREMENT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "revision",
        "fingerprint",
    }
)
_DESIGN_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "revision",
        "fingerprint",
    }
)
_AUTHORITY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "run_id",
        "repository_id",
        "repository_key",
        "worktree_id",
        "target_revision",
        "worktree_revision",
        "requirement_id",
        "requirement_revision",
        "design_revision",
        "design_fingerprint",
        "actor",
        "session_id",
        "authority_revision",
        "authority_fingerprint",
    }
)
_TERMINAL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "actor",
        "terminalized_at",
        "reason_code",
        "reason",
        "completed_deliverables",
        "completion_evidence",
    }
)
_STAGE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "run_id",
        "stage_id",
        "requirement",
        "design",
        "stage_kind",
        "parent_stage_ids",
        "predecessor_stage_ids",
        "input_manifest_ref",
        "execution_root_id",
        "deliverables",
        "selected_artifacts",
        "budget",
        "dependencies",
        "contracts",
        "authority",
        "state",
        "outcome",
        "default_consumable",
        "terminal",
        "created_at",
        "aggregate_revision",
        "fingerprint",
    }
)
_SPLIT_SPEC_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "stage_kind",
        "selected_artifacts",
        "dependencies",
        "budget",
        "deliverables",
        "contracts",
        "input_manifest_ref",
    }
)
_LINEAGE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "parent_stage_id",
        "child_stage_id",
        "predecessor_stage_ids",
        "handoff_fingerprint",
        "split_operation_id",
        "fingerprint",
    }
)

# Additive value contracts only. Their domain owners still validate DAG,
# capabilities, effect truth, CAS and progression before activating a writer.
PHASE_DEFINITION_SCHEMA: Final[str] = "taskplane.phase-definition/v1"
AGENT_RUNTIME_SCHEMA: Final[str] = "taskplane.agent-runtime/v1"
KNOWLEDGE_UPDATE_SCHEMA: Final[str] = "taskplane.knowledge-update/v1"
KNOWLEDGE_APPLY_SCHEMA: Final[str] = "taskplane.knowledge-apply-receipt/v1"
HANDOFF_V2_SCHEMA: Final[str] = "taskplane.stage-handoff/v2"
_PHASE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "role",
        "skill_ref",
        "skill_content_fingerprint",
        "consumes",
        "produces",
        "domain_validator_refs",
        "validator_inventory_fingerprint",
        "capability_requirements",
        "working_lenses",
        "evaluation_lenses",
        "budget",
        "model_tier",
        "gate",
        "predecessors",
        "successors",
        "edge_conditions",
        "entry",
        "terminal",
        "telemetry_scope",
    }
)
_RUNTIME_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "run_id",
        "phase_id",
        "attempt_id",
        "operation_id",
        "candidate_fingerprint",
        "definition_set_fingerprint",
        "phase_definition_fingerprint",
        "skill_content_fingerprint",
        "validator_identities",
        "validator_inventory_fingerprint",
        "consumed_artifact_schema_versions",
        "produced_artifact_schema_versions",
        "capability_set_fingerprint",
        "sealed_package_fingerprint",
        "knowledge_fingerprint",
        "authority_fingerprint",
        "host_kind_version",
        "nonce_digest",
        "lease_id",
        "fencing_token",
        "deadline",
        "budget",
        "start_identity",
        "progress_identity",
        "terminal_identity",
        "effect_state",
        "collected_output_references",
        "produces_conformance",
        "knowledge_proposals",
        "evaluator_dispatch_eligibility",
        "retry_class",
        "status",
        "reason_code",
        "continuation",
    }
)
_KNOWLEDGE_UPDATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "base_knowledge_fingerprint",
        "run_id",
        "phase_id",
        "attempt_id",
        "operation_id",
        "candidate_fingerprint",
        "finding_or_observation_reference",
        "scope",
        "content_class",
        "content",
        "supersedes",
    }
)
_KNOWLEDGE_APPLY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "base_fingerprint",
        "current_fingerprint",
        "new_fingerprint",
        "proposal_fingerprint",
        "gate_receipt_fingerprint",
        "operation_id",
        "writer_id",
        "fencing_token",
        "retention_class",
        "applied_at",
        "outcome",
        "continuation",
    }
)
_HANDOFF_V2_ADDITIONS: Final[frozenset[str]] = frozenset(
    {
        "phase_result",
        "produced_artifacts",
        "inherited_artifacts",
        "knowledge_apply_receipts",
        "unresolved_issues",
    }
)
_CONTRACT_FIELDS: Final[Mapping[str, frozenset[str]]] = {
    PHASE_DEFINITION_SCHEMA: _PHASE_FIELDS,
    AGENT_RUNTIME_SCHEMA: _RUNTIME_FIELDS,
    KNOWLEDGE_UPDATE_SCHEMA: _KNOWLEDGE_UPDATE_FIELDS,
    KNOWLEDGE_APPLY_SCHEMA: _KNOWLEDGE_APPLY_FIELDS,
}
_CONTRACT_SELF_FIELDS: Final[Mapping[str, str]] = {
    KNOWLEDGE_UPDATE_SCHEMA: "proposal_fingerprint",
    KNOWLEDGE_APPLY_SCHEMA: "seal",
}


def _strict_json(value: object, depth: int = 0) -> None:
    """Reject lossy JSON coercions before hashing, including nested keys."""
    if depth > 32:
        raise StageValidationError("contract nesting exceeds 32 levels")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise StageValidationError("contract field names must be strings")
        for child in value.values():
            _strict_json(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise StageValidationError("contract collection is too large")
        for child in value:
            _strict_json(child, depth + 1)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise StageValidationError("contract must contain JSON values")
    elif isinstance(value, float) and not math.isfinite(value):
        raise StageValidationError("contract numbers must be finite")


def _contract_json(value: Mapping[str, object]) -> bytes:
    _strict_json(value)
    try:
        data = review_evidence.canonical_bytes(value)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise StageValidationError("contract must be canonical UTF-8 JSON") from exc
    if len(data) > MAX_INPUT_MANIFEST_BYTES:
        raise StageValidationError("contract exceeds 65536 bytes")
    return data


def read_contract_json(
    data: str | bytes, *, store: review_evidence.ArtifactStore | None = None
) -> JsonObject:
    """Read bounded UTF-8 JSON without accepting duplicate field authority."""
    if not isinstance(data, (str, bytes)) or len(data) > MAX_INPUT_MANIFEST_BYTES:
        raise StageValidationError("contract JSON input is invalid or oversized")

    def pairs(items: list[tuple[str, object]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in items:
            if key in result:
                raise StageValidationError(f"duplicate contract field: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            data.decode("utf-8") if isinstance(data, bytes) else data, object_pairs_hook=pairs
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StageValidationError(f"invalid contract JSON: {exc}") from exc
    return validate_contract(value, store=store)


def _contract_budget(value: object) -> None:
    row = _closed(value, frozenset({"tokens", "wall_ms", "attempts", "corrections"}), "budget")
    for key, count in row.items():
        if key == "tokens" and count is None:
            continue  # Explicit run-scoped opt-out; usage is still required.
        if type(count) is not int or count < 0:
            raise StageValidationError(f"budget {key} must be non-negative integer")


def _contract_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise StageValidationError(f"{label} must be a list")
    return value


def _contract_strings(value: object, label: str) -> list[str]:
    # Ordered definitions and validators retain order; do not sort a DAG.
    rows = [_bounded_text(item, label) for item in _contract_list(value, label)]
    if len(rows) != len(set(rows)):
        raise StageValidationError(f"{label} contains duplicates")
    return rows


def _continuation(value: object) -> None:
    row = _closed(value, frozenset({"kind", "phase_id"}), "continuation")
    if row["kind"] not in {
        "continue",
        "evaluate",
        "wait",
        "reconcile",
        "retry",
        "fresh-package",
        "terminal",
        "hold",
    }:
        raise StageValidationError("unsupported continuation kind")
    if row["phase_id"] is not None:
        _identifier(row["phase_id"], "continuation phase id")


def _schema_artifacts(value: object, label: str, *, references: bool = False) -> None:
    identities = []
    for item in _contract_list(value, label):
        fields = {"artifact_class", "artifact_schema_version"}
        if references:
            fields.add("reference")
        row = _closed(item, frozenset(fields), label)
        identities.append(_bounded_text(row["artifact_class"], "artifact class"))
        _bounded_text(row["artifact_schema_version"], "artifact schema version")
        if references:
            _portable_reference(row["reference"], "artifact reference")
    if len(identities) != len(set(identities)):
        raise StageValidationError(f"{label} contains duplicate artifact classes")


def _validate_phase_definition(row: Mapping[str, object]) -> None:
    for key in ("id", "role"):
        _identifier(row[key], key)
    for key in ("skill_ref", "model_tier", "gate"):
        _bounded_text(row[key], key)
    for key in (
        "domain_validator_refs",
        "capability_requirements",
        "working_lenses",
        "evaluation_lenses",
        "predecessors",
        "successors",
        "telemetry_scope",
    ):
        _contract_strings(row[key], key)
    for key in ("entry", "terminal"):
        if type(row[key]) is not bool:
            raise StageValidationError(f"{key} must be boolean")
    _contract_budget(row["budget"])
    for relation in ("consumes", "produces"):
        identities = []
        for artifact in _contract_list(row[relation], relation):
            fields = {"artifact_class", "artifact_schema_version", "required"}
            fields.update(
                {"knowledge_scope", "knowledge_fingerprint_required"}
                if relation == "consumes"
                else {"cardinality"}
            )
            item = _closed(artifact, frozenset(fields), relation)
            identities.append(_bounded_text(item["artifact_class"], "artifact class"))
            _bounded_text(item["artifact_schema_version"], "artifact schema version")
            if type(item["required"]) is not bool:
                raise StageValidationError("artifact required must be boolean")
            if relation == "consumes":
                _contract_strings(item["knowledge_scope"], "knowledge scope")
                if type(item["knowledge_fingerprint_required"]) is not bool:
                    raise StageValidationError("knowledge fingerprint required must be boolean")
            elif item["cardinality"] not in {"one", "many"}:
                raise StageValidationError("unsupported artifact cardinality")
        if len(identities) != len(set(identities)):
            raise StageValidationError(f"{relation} contains duplicate artifact classes")
    endpoints = []
    for edge in _contract_list(row["edge_conditions"], "edge conditions"):
        item = _closed(edge, frozenset({"successor", "condition"}), "edge condition")
        endpoints.append(_identifier(item["successor"], "edge successor"))
        _bounded_text(item["condition"], "edge condition")
    successors = _contract_strings(row["successors"], "successors")
    if len(endpoints) != len(set(endpoints)) or set(endpoints) != set(successors):
        raise StageValidationError("edge conditions must match successors exactly")


def _validate_runtime_result(
    row: Mapping[str, object], *, store: review_evidence.ArtifactStore | None = None
) -> None:
    for key in ("run_id", "phase_id", "attempt_id", "operation_id", "lease_id"):
        _identifier(row[key], key)
    _bounded_text(row["host_kind_version"], "host kind/version")
    _timestamp(row["deadline"], "deadline")
    _contract_budget(row["budget"])
    if type(row["fencing_token"]) is not int or row["fencing_token"] < 1:
        raise StageValidationError("fencing token must be positive")
    for key in ("start_identity", "terminal_identity"):
        if row[key] is not None:
            _bounded_text(row[key], key)
    for key in ("validator_identities", "progress_identity"):
        _contract_strings(row[key], key)
    for key in ("consumed_artifact_schema_versions", "produced_artifact_schema_versions"):
        _schema_artifacts(row[key], key)
    _references(row["collected_output_references"], "collected output references")
    for proposal in _contract_list(row["knowledge_proposals"], "knowledge proposals"):
        if not isinstance(proposal, dict) or proposal.get("schema") != KNOWLEDGE_UPDATE_SCHEMA:
            raise StageValidationError("unsupported knowledge proposal")
        validate_contract(proposal, store=store)
    for key in ("produces_conformance", "evaluator_dispatch_eligibility"):
        if type(row[key]) is not bool:
            raise StageValidationError(f"{key} must be boolean")
    if row["effect_state"] not in {"none", "effect_free", "applied", "uncertain", "reconciled"}:
        raise StageValidationError("unsupported effect state")
    if row["retry_class"] not in {"none", "effect_free", "attempt_bound", "reconcile", "permanent"}:
        raise StageValidationError("unsupported retry class")
    _continuation(row["continuation"])
    if row["status"] == "accepted":
        if (
            row["reason_code"] is not None
            or not row["produces_conformance"]
            or not row["start_identity"]
            or not row["terminal_identity"]
        ):
            raise StageValidationError("accepted result requires conforming terminal evidence")
    elif row["status"] == "refused":
        _identifier(row["reason_code"], "refusal reason code")
        if row["evaluator_dispatch_eligibility"]:
            raise StageValidationError("refused result cannot be evaluator eligible")
    else:
        raise StageValidationError("unsupported runtime result status")


def validate_contract(
    value: object, *, store: review_evidence.ArtifactStore | None = None
) -> JsonObject:
    """Validate T01 closed value schemas without minting domain authority.

    The incumbent stage/handoff validators remain the only v1 owners. New
    shapes are additive and do not activate their producers. A seal/fingerprint
    is content identity; authentication requires stage_handoff.verify_contract.
    """
    if not isinstance(value, Mapping):
        raise StageValidationError("contract must be an object")
    value = dict(json.loads(_contract_json(dict(value))))
    schema = value.get("schema")
    if schema == SCHEMA:
        return validate_stage(value)
    if not isinstance(schema, str) or schema not in _CONTRACT_FIELDS:
        raise StageValidationError("unsupported contract schema")
    self_field = _CONTRACT_SELF_FIELDS.get(schema, "fingerprint")
    row = _closed(value, _CONTRACT_FIELDS[schema] | {"schema", self_field}, "contract")
    for key, item in row.items():
        if key.endswith("fingerprint") or key in {"nonce_digest", "seal"}:
            if key == "new_fingerprint" and item is None:
                continue
            _fingerprint(item, key)
    material = {key: item for key, item in row.items() if key != self_field}
    if review_evidence.content_fingerprint(material) != row[self_field]:
        raise StageIntegrityError("contract fingerprint mismatch")
    if schema == PHASE_DEFINITION_SCHEMA:
        _validate_phase_definition(row)
    elif schema == AGENT_RUNTIME_SCHEMA:
        _validate_runtime_result(row, store=store)
    elif schema == KNOWLEDGE_UPDATE_SCHEMA:
        for key in ("run_id", "phase_id", "attempt_id", "operation_id"):
            _identifier(row[key], key)
        _bounded_text(row["finding_or_observation_reference"], "observation reference")
        _contract_strings(row["scope"], "knowledge scope")
        _contract_strings(row["supersedes"], "supersedes")
        if row["content_class"] not in {"fact", "evidence-reference"}:
            raise StageValidationError("unsupported knowledge content class")
        _bounded_text(row["content"], "knowledge content", maximum=4096)
    elif schema == KNOWLEDGE_APPLY_SCHEMA:
        for key in ("operation_id", "writer_id", "retention_class"):
            _identifier(row[key], key)
        _timestamp(row["applied_at"], "knowledge apply time")
        if type(row["fencing_token"]) is not int or row["fencing_token"] < 1:
            raise StageValidationError("fencing token must be positive")
        if row["outcome"] not in {"applied", "replay", "conflict", "rejected", "tombstoned"}:
            raise StageValidationError("unsupported knowledge apply outcome")
        if row["outcome"] in {"conflict", "rejected"} and row["new_fingerprint"] is not None:
            raise StageValidationError("non-applied knowledge result has a new fingerprint")
        if row["outcome"] in {"applied", "replay", "tombstoned"} and row["new_fingerprint"] is None:
            raise StageValidationError("applied knowledge result lacks new fingerprint")
        _continuation(row["continuation"])
    return value


def create_contract(
    value: Mapping[str, object], *, store: review_evidence.ArtifactStore | None = None
) -> JsonObject:
    """Produce a detached closed value; no persistence or progression effects."""
    if not isinstance(value, Mapping):
        raise StageValidationError("contract must be an object")
    result = json.loads(_contract_json(dict(value)))
    schema = result.get("schema")
    if not isinstance(schema, str):
        raise StageValidationError("unsupported contract schema")
    self_field = _CONTRACT_SELF_FIELDS.get(schema, "fingerprint")
    # Supplied identities must validate, never silently repair stale records.
    if self_field not in result:
        result[self_field] = review_evidence.content_fingerprint(result)
    return validate_contract(result, store=store)


def canonical_contract_bytes(
    value: Mapping[str, object], *, store: review_evidence.ArtifactStore | None = None
) -> bytes:
    """Canonical UTF-8, sorted string keys, compact separators, finite numbers."""
    return _contract_json(validate_contract(value, store=store))


class StageValidationError(ValueError):
    """A stage value violates the closed canonical schema."""


class StageIntegrityError(StageValidationError):
    """A stage or derived projection does not match its fingerprint."""


class StageLifecycleError(StageValidationError):
    """A requested lifecycle transition is not permitted."""


class SplitValidationError(StageLifecycleError):
    """A split cannot produce a complete, isolated child set."""


def _closed(
    value: object, fields: frozenset[str], label: str, *, optional: frozenset[str] = frozenset()
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise StageValidationError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise StageValidationError(f"{label} field names must be strings")
    keys = set(value)
    unknown = keys - fields
    missing = fields - keys - optional
    if unknown:
        raise StageValidationError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise StageValidationError(f"{label} has missing fields: {', '.join(sorted(missing))}")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise StageValidationError(f"{label} is invalid")
    text = value.strip()
    if text != value or not _IDENTIFIER.fullmatch(text):
        raise StageValidationError(f"{label} is invalid")
    return text


def _path_id(value: object, label: str) -> str:
    """Validate an identity that is also a canonical storage component."""
    try:
        return runtime_storage.validate_stage_path_id(value, label)
    except runtime_storage.StorageIdentityError as exc:
        raise StageValidationError(str(exc)) from exc


def _bounded_text(value: object, label: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise StageValidationError(f"{label} is invalid")
    text = value.strip()
    if text != value or not text or len(text.encode("utf-8")) > maximum:
        raise StageValidationError(f"{label} is invalid")
    return text


def _revision(value: object, label: str) -> str:
    return _bounded_text(value, label, maximum=128)


def _fingerprint(value: object, label: str) -> str:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value):
        raise StageValidationError(f"{label} is invalid")
    return value


def _timestamp(value: object, label: str) -> str:
    text = _bounded_text(value, label, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StageValidationError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise StageValidationError(f"{label} needs a timezone")
    return text


def _strings(
    values: object, label: str, *, pattern: re.Pattern[str] | None = None, allow_empty: bool = True
) -> list[str]:
    if isinstance(values, (str, bytes, Mapping)):
        raise StageValidationError(f"{label} must be a list")
    try:
        rows = list(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise StageValidationError(f"{label} must be a list") from exc
    if len(rows) > MAX_COLLECTION_ITEMS:
        raise StageValidationError(f"{label} contains at most {MAX_COLLECTION_ITEMS} entries")
    result: list[str] = []
    for raw in rows:
        if not isinstance(raw, str):
            raise StageValidationError(f"{label} entries must be strings")
        text = raw.strip()
        if (
            text != raw
            or not text
            or len(text.encode("utf-8")) > 256
            or (pattern is not None and not pattern.fullmatch(text))
        ):
            raise StageValidationError(f"{label} contains an invalid entry")
        result.append(text)
    if not allow_empty and not result:
        raise StageValidationError(f"{label} must not be empty")
    if len(set(result)) != len(result):
        raise StageValidationError(f"{label} contains duplicate entries")
    return sorted(result)


def _path_ids(values: object, label: str) -> list[str]:
    rows = _strings(values, label)
    return sorted(_path_id(row, f"{label} entry") for row in rows)


def _plain_mapping(
    value: object, label: str, *, allow_empty: bool = False, max_bytes: int = 8 * 1024
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise StageValidationError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise StageValidationError(f"{label} field names must be strings")
    if not allow_empty and not value:
        raise StageValidationError(f"{label} must not be empty")
    try:
        data = review_evidence.canonical_bytes(value)
        result = json.loads(data.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StageValidationError(f"{label} must be canonical JSON") from exc
    if len(data) > max_bytes:
        raise StageValidationError(f"{label} exceeds {max_bytes} bytes")
    return result


def _requirement(value: object) -> dict[str, object]:
    row = _closed(value, _REQUIREMENT_FIELDS, "requirement")
    return {
        "id": _identifier(row.get("id"), "requirement id"),
        "revision": _revision(row.get("revision"), "requirement revision"),
        "fingerprint": _fingerprint(row.get("fingerprint"), "requirement fingerprint"),
    }


def _design(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    row = _closed(value, _DESIGN_FIELDS, "design")
    return {
        "revision": _revision(row.get("revision"), "design revision"),
        "fingerprint": _fingerprint(row.get("fingerprint"), "design fingerprint"),
    }


def _portable_reference(
    value: object, label: str, *, manifest_bound: bool = False
) -> dict[str, object]:
    row = _closed(value, _PORTABLE_REFERENCE_FIELDS, label)
    if (
        row.get("schema") != "taskplane.artifact-reference/v1"
        or row.get("transport") != "artifact-reference"
    ):
        raise StageValidationError(f"{label} schema is invalid")
    kind = _bounded_text(row.get("kind"), f"{label} kind", maximum=64)
    if not _KIND.fullmatch(kind):
        raise StageValidationError(f"{label} kind is invalid")
    fingerprint = _fingerprint(row.get("fingerprint"), f"{label} fingerprint")
    digest = _fingerprint(row.get("digest"), f"{label} digest")
    byte_count = row.get("bytes")
    if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
        raise StageValidationError(f"{label} byte count is invalid")
    if manifest_bound and byte_count > MAX_INPUT_MANIFEST_BYTES:
        raise StageValidationError(f"input manifest exceeds {MAX_INPUT_MANIFEST_BYTES} bytes")
    locator = f"artifact://{kind}/{fingerprint}"
    if row.get("locator") != locator:
        raise StageValidationError(f"{label} locator is invalid")
    return {
        "schema": "taskplane.artifact-reference/v1",
        "kind": kind,
        "fingerprint": fingerprint,
        "digest": digest,
        "bytes": byte_count,
        "locator": locator,
        "transport": "artifact-reference",
    }


def _references(values: object, label: str, *, allow_empty: bool = True) -> list[dict[str, object]]:
    if isinstance(values, (str, bytes, Mapping)):
        raise StageValidationError(f"{label} must be a list")
    try:
        rows = list(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise StageValidationError(f"{label} must be a list") from exc
    if len(rows) > MAX_COLLECTION_ITEMS:
        raise StageValidationError(f"{label} contains at most {MAX_COLLECTION_ITEMS} references")
    result = [_portable_reference(row, f"{label} reference") for row in rows]
    identities = [(str(row["kind"]), str(row["fingerprint"])) for row in result]
    if len(set(identities)) != len(identities):
        raise StageValidationError(f"{label} contains duplicate references")
    if not allow_empty and not result:
        raise StageValidationError(f"{label} must not be empty")
    return sorted(result, key=lambda row: (str(row["kind"]), str(row["fingerprint"])))


def _budget(value: object) -> dict[str, object]:
    result = _plain_mapping(value, "budget", max_bytes=4 * 1024)

    def check(item: object) -> None:
        if isinstance(item, bool) or item is None:
            return
        if isinstance(item, (int, float)):
            if not math.isfinite(float(item)) or item < 0:
                raise StageValidationError("budget values must be non-negative")
            return
        if isinstance(item, str):
            if not item.strip() or len(item.encode("utf-8")) > 256:
                raise StageValidationError("budget values are invalid")
            return
        if isinstance(item, list):
            for child in item:
                check(child)
            return
        if isinstance(item, dict):
            for child in item.values():
                check(child)
            return
        raise StageValidationError("budget values are invalid")

    check(result)
    return result


def _authority(
    value: object,
    *,
    run_id: str,
    requirement: Mapping[str, object],
    design: Mapping[str, object] | None,
) -> dict[str, object]:
    row = _closed(value, _AUTHORITY_FIELDS, "authority")
    if row.get("schema") != AUTHORITY_SCHEMA:
        raise StageValidationError("authority schema is invalid")
    checked: dict[str, object] = {
        "schema": AUTHORITY_SCHEMA,
        "run_id": _path_id(row.get("run_id"), "authority run id"),
        "repository_id": _bounded_text(row.get("repository_id"), "authority repository id"),
        "repository_key": _bounded_text(row.get("repository_key"), "authority repository key"),
        "worktree_id": _bounded_text(row.get("worktree_id"), "authority worktree id"),
        "target_revision": _revision(row.get("target_revision"), "authority target revision"),
        "worktree_revision": _revision(row.get("worktree_revision"), "authority worktree revision"),
        "requirement_id": _identifier(row.get("requirement_id"), "authority requirement id"),
        "requirement_revision": _revision(
            row.get("requirement_revision"), "authority requirement revision"
        ),
        "design_revision": None,
        "design_fingerprint": None,
        "actor": _identifier(row.get("actor"), "authority actor"),
        "session_id": _identifier(row.get("session_id"), "authority session id"),
        "authority_revision": row.get("authority_revision"),
        "authority_fingerprint": _fingerprint(
            row.get("authority_fingerprint"), "authority fingerprint"
        ),
    }
    authority_revision = checked["authority_revision"]
    if (
        isinstance(authority_revision, bool)
        or not isinstance(authority_revision, int)
        or authority_revision < 0
    ):
        raise StageValidationError("authority revision is invalid")
    if row.get("design_revision") is not None:
        checked["design_revision"] = _revision(
            row.get("design_revision"), "authority design revision"
        )
    if row.get("design_fingerprint") is not None:
        checked["design_fingerprint"] = _fingerprint(
            row.get("design_fingerprint"), "authority design fingerprint"
        )
    if (
        checked["run_id"] != run_id
        or checked["requirement_id"] != requirement.get("id")
        or checked["requirement_revision"] != requirement.get("revision")
    ):
        raise StageValidationError("authority identity does not match stage")
    expected_design_revision = design.get("revision") if design else None
    expected_design_fingerprint = design.get("fingerprint") if design else None
    if (
        checked["design_revision"] != expected_design_revision
        or checked["design_fingerprint"] != expected_design_fingerprint
    ):
        raise StageValidationError("authority design does not match stage")
    return checked


def request_fingerprint(request: Mapping[str, object]) -> str:
    """Return the stable semantic identity for an idempotent stage command."""
    if not isinstance(request, Mapping):
        raise StageValidationError("stage request must be an object")
    try:
        return review_evidence.content_fingerprint(request)
    except (TypeError, ValueError) as exc:
        raise StageValidationError("stage request must be canonical JSON") from exc


def stage_fingerprint(stage: Mapping[str, object]) -> str:
    """Return a stage's semantic fingerprint, excluding its self-reference."""
    if not isinstance(stage, Mapping):
        raise StageValidationError("stage must be an object")
    material = {str(key): value for key, value in stage.items() if str(key) != "fingerprint"}
    try:
        return review_evidence.content_fingerprint(material)
    except (TypeError, ValueError) as exc:
        raise StageValidationError("stage must be canonical JSON") from exc


def create_stage(
    *,
    run_id: str,
    stage_id: str,
    requirement: Mapping[str, object],
    design: Mapping[str, object] | None,
    stage_kind: str,
    parent_stage_ids: Iterable[str],
    predecessor_stage_ids: Iterable[str],
    input_manifest_ref: Mapping[str, object],
    execution_root_id: str,
    deliverables: Iterable[str],
    budget: Mapping[str, object],
    dependencies: Iterable[str],
    contracts: Iterable[str],
    authority: Mapping[str, object],
    created_at: str,
    selected_artifacts: Iterable[Mapping[str, object]] = (),
) -> JsonObject:
    """Create one canonical active aggregate with immutable lineage inputs."""
    run = _path_id(run_id, "run id")
    stage = _path_id(stage_id, "stage id")
    requirement_row = _requirement(requirement)
    design_row = _design(design)
    parents = _path_ids(parent_stage_ids, "parent stage ids")
    predecessors = _path_ids(predecessor_stage_ids, "predecessor stage ids")
    if stage in set(parents) | set(predecessors):
        raise StageValidationError("a stage cannot be its own ancestor")
    kind = _bounded_text(stage_kind, "stage kind", maximum=64)
    if not _KIND.fullmatch(kind):
        raise StageValidationError("stage kind is invalid")
    deliverable_rows = _strings(deliverables, "deliverables")
    dependency_rows = _path_ids(dependencies, "dependencies")
    if stage in dependency_rows:
        raise StageValidationError("a stage cannot depend on itself")
    contract_rows = _strings(contracts, "contracts", pattern=_CONTRACT)
    body: JsonObject = {
        "schema": SCHEMA,
        "run_id": run,
        "stage_id": stage,
        "requirement": requirement_row,
        "design": design_row,
        "stage_kind": kind,
        "parent_stage_ids": parents,
        "predecessor_stage_ids": predecessors,
        "input_manifest_ref": _portable_reference(
            input_manifest_ref, "input manifest reference", manifest_bound=True
        ),
        "execution_root_id": _path_id(execution_root_id, "execution root id"),
        "deliverables": deliverable_rows,
        "selected_artifacts": _references(selected_artifacts, "selected artifacts"),
        "budget": _budget(budget),
        "dependencies": dependency_rows,
        "contracts": contract_rows,
        "authority": _authority(
            authority, run_id=run, requirement=requirement_row, design=design_row
        ),
        "state": "active",
        "outcome": None,
        "default_consumable": True,
        "terminal": None,
        "created_at": _timestamp(created_at, "stage creation time"),
        "aggregate_revision": 1,
    }
    if body["execution_root_id"] != f"execution-{stage}":
        raise StageValidationError("execution root id must be deterministically bound to stage id")
    body["fingerprint"] = stage_fingerprint(body)
    return validate_stage(body)


def validate_stage(stage: Mapping[str, object]) -> JsonObject:
    """Validate and return a detached canonical ``taskplane.stage/v1`` value."""
    row = _closed(stage, _STAGE_FIELDS, "stage", optional=frozenset({"fingerprint"}))
    if row.get("schema") != SCHEMA:
        raise StageValidationError("unsupported stage schema")
    run_id = _path_id(row.get("run_id"), "run id")
    stage_id = _path_id(row.get("stage_id"), "stage id")
    requirement = _requirement(row.get("requirement"))
    design = _design(row.get("design"))
    kind = _bounded_text(row.get("stage_kind"), "stage kind", maximum=64)
    if not _KIND.fullmatch(kind):
        raise StageValidationError("stage kind is invalid")
    parents = _path_ids(row.get("parent_stage_ids"), "parent stage ids")
    predecessors = _path_ids(row.get("predecessor_stage_ids"), "predecessor stage ids")
    dependencies = _path_ids(row.get("dependencies"), "dependencies")
    if stage_id in set(parents) | set(predecessors) | set(dependencies):
        raise StageValidationError("a stage cannot refer to itself")
    deliverables = _strings(row.get("deliverables"), "deliverables")
    contracts = _strings(row.get("contracts"), "contracts", pattern=_CONTRACT)
    selected_artifacts = _references(row.get("selected_artifacts"), "selected artifacts")
    for label, canonical in (
        ("parent stage ids", parents),
        ("predecessor stage ids", predecessors),
        ("deliverables", deliverables),
        ("selected artifacts", selected_artifacts),
        ("dependencies", dependencies),
        ("contracts", contracts),
    ):
        field = label.replace(" ", "_")
        if field == "selected_artifacts":
            supplied = row.get(field)
        elif field == "parent_stage_ids":
            supplied = row.get(field)
        elif field == "predecessor_stage_ids":
            supplied = row.get(field)
        else:
            supplied = row.get(field)
        if supplied != canonical:
            raise StageValidationError(f"{label} are not in canonical order")
    input_manifest = _portable_reference(
        row.get("input_manifest_ref"), "input manifest reference", manifest_bound=True
    )
    execution_root_id = _path_id(row.get("execution_root_id"), "execution root id")
    if execution_root_id != f"execution-{stage_id}":
        raise StageValidationError("execution root id must be deterministically bound to stage id")
    budget = _budget(row.get("budget"))
    authority = _authority(
        row.get("authority"), run_id=run_id, requirement=requirement, design=design
    )
    created_at = _timestamp(row.get("created_at"), "stage creation time")
    aggregate_revision = row.get("aggregate_revision")
    if (
        isinstance(aggregate_revision, bool)
        or not isinstance(aggregate_revision, int)
        or aggregate_revision < 1
    ):
        raise StageValidationError("aggregate revision is invalid")

    state = row.get("state")
    outcome = row.get("outcome")
    default_consumable = row.get("default_consumable")
    terminal = row.get("terminal")
    if state not in STAGE_STATES or not isinstance(default_consumable, bool):
        raise StageValidationError("stage lifecycle is invalid")
    canonical_terminal: dict[str, object] | None = None
    if state == "active":
        if outcome is not None or terminal is not None or default_consumable is not True:
            raise StageValidationError("active stage has terminal state")
    else:
        if outcome not in TERMINAL_OUTCOMES:
            raise StageValidationError("terminal stage outcome is invalid")
        terminal_row = _closed(terminal, _TERMINAL_FIELDS, "terminal attribution")
        actor = _identifier(terminal_row.get("actor"), "terminal actor")
        terminalized_at = _timestamp(terminal_row.get("terminalized_at"), "terminal time")
        reason_code = terminal_row.get("reason_code")
        reason = terminal_row.get("reason")
        completed = _strings(terminal_row.get("completed_deliverables"), "completed deliverables")
        if not set(completed).issubset(deliverables):
            raise StageValidationError("completed deliverables were not declared")
        completion_evidence = _references(
            terminal_row.get("completion_evidence"), "completion evidence"
        )
        if outcome == "done":
            if completed != deliverables or not completion_evidence:
                raise StageLifecycleError("done requires all deliverables and completion evidence")
            if reason_code is not None or reason is not None or default_consumable is not True:
                raise StageValidationError("done terminal state is invalid")
        else:
            reason_code = _identifier(reason_code, "terminal reason code")
            reason = _bounded_text(reason, "terminal reason", maximum=MAX_REASON_BYTES)
            if default_consumable is not False:
                raise StageValidationError(f"{outcome} stage cannot be consumed by default")
        canonical_terminal = {
            "actor": actor,
            "terminalized_at": terminalized_at,
            "reason_code": reason_code,
            "reason": reason,
            "completed_deliverables": completed,
            "completion_evidence": completion_evidence,
        }
        if terminal != canonical_terminal:
            raise StageValidationError("terminal attribution is not canonical")

    canonical: JsonObject = {
        "schema": SCHEMA,
        "run_id": run_id,
        "stage_id": stage_id,
        "requirement": requirement,
        "design": design,
        "stage_kind": kind,
        "parent_stage_ids": parents,
        "predecessor_stage_ids": predecessors,
        "input_manifest_ref": input_manifest,
        "execution_root_id": execution_root_id,
        "deliverables": deliverables,
        "selected_artifacts": selected_artifacts,
        "budget": budget,
        "dependencies": dependencies,
        "contracts": contracts,
        "authority": authority,
        "state": state,
        "outcome": outcome,
        "default_consumable": default_consumable,
        "terminal": canonical_terminal,
        "created_at": created_at,
        "aggregate_revision": aggregate_revision,
    }
    expected = stage_fingerprint(canonical)
    supplied = row.get("fingerprint")
    if supplied is not None and supplied != expected:
        raise StageIntegrityError("stage fingerprint mismatch")
    canonical["fingerprint"] = expected
    # Validate the bounded read seam while the full aggregate is in hand.
    _bounded_stage_summary(canonical)
    return copy.deepcopy(canonical)


def terminalize_stage(
    stage: Mapping[str, object],
    *,
    outcome: str,
    actor: str,
    terminal_at: str | None = None,
    terminalized_at: str | None = None,
    reason_code: str | None = None,
    reason: str | None = None,
    completed_deliverables: Iterable[str] = (),
    completion_evidence: Iterable[Mapping[str, object]] = (),
) -> JsonObject:
    """Return the sole legal active-to-terminal revision of a stage."""
    current = validate_stage(stage)
    if current["state"] != "active":
        raise StageLifecycleError("terminal stage cannot transition again")
    terminal_outcome = str(outcome or "")
    if terminal_outcome not in TERMINAL_OUTCOMES:
        raise StageLifecycleError("terminal outcome is invalid")
    if terminal_at is not None and terminalized_at is not None and terminal_at != terminalized_at:
        raise StageValidationError("terminal time is ambiguous")
    at = terminalized_at if terminalized_at is not None else terminal_at
    terminal: JsonObject = {
        "actor": _identifier(actor, "terminal actor"),
        "terminalized_at": _timestamp(at, "terminal time"),
        "reason_code": reason_code,
        "reason": reason,
        "completed_deliverables": _strings(completed_deliverables, "completed deliverables"),
        "completion_evidence": _references(completion_evidence, "completion evidence"),
    }
    if terminal["actor"] != current["authority"]["actor"]:
        raise StageLifecycleError("terminal actor does not match stage authority")
    updated = copy.deepcopy(current)
    updated.update(
        {
            "state": "terminal",
            "outcome": terminal_outcome,
            "default_consumable": terminal_outcome == "done",
            "terminal": terminal,
            "aggregate_revision": int(current["aggregate_revision"]) + 1,
        }
    )
    updated["fingerprint"] = stage_fingerprint(updated)
    return validate_stage(updated)


def split_child_id(run_id: str, parent_stage_id: str, operation_id: str, ordinal: int) -> str:
    """Derive an independently addressable child id from one split request."""
    run = _path_id(run_id, "run id")
    parent = _path_id(parent_stage_id, "parent stage id")
    operation = _identifier(operation_id, "split operation id")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise SplitValidationError("child ordinal is invalid")
    identity = request_fingerprint(
        {
            "schema": "taskplane.split-child-identity/v1",
            "run_id": run,
            "parent_stage_id": parent,
            "operation_id": operation,
            "ordinal": ordinal,
        }
    )
    return f"stage-{identity[:32]}"


def _lineage_row(
    *,
    parent_stage_id: str | None,
    child_stage_id: str,
    input_manifest_ref: Mapping[str, object],
    operation_id: str,
    predecessor_stage_ids: Iterable[str] = (),
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema": LINEAGE_SCHEMA,
        "parent_stage_id": (
            _path_id(parent_stage_id, "lineage parent stage id")
            if parent_stage_id is not None
            else None
        ),
        "child_stage_id": child_stage_id,
        "predecessor_stage_ids": _path_ids(
            predecessor_stage_ids,
            "lineage predecessor stage ids",
        ),
        "handoff_fingerprint": input_manifest_ref["fingerprint"],
        "split_operation_id": operation_id,
    }
    body["fingerprint"] = request_fingerprint(body)
    return body


def validate_lineage(row: Mapping[str, object]) -> dict[str, object]:
    """Validate one immutable parent-to-child lineage tuple."""
    value = _closed(row, _LINEAGE_FIELDS, "stage lineage")
    if value.get("schema") != LINEAGE_SCHEMA:
        raise StageValidationError("stage lineage schema is invalid")
    canonical: dict[str, object] = {
        "schema": LINEAGE_SCHEMA,
        "parent_stage_id": (
            _path_id(value.get("parent_stage_id"), "lineage parent stage id")
            if value.get("parent_stage_id") is not None
            else None
        ),
        "child_stage_id": _path_id(value.get("child_stage_id"), "lineage child stage id"),
        "predecessor_stage_ids": _path_ids(
            value.get("predecessor_stage_ids"), "lineage predecessor stage ids"
        ),
        "handoff_fingerprint": _fingerprint(
            value.get("handoff_fingerprint"), "lineage handoff fingerprint"
        ),
        "split_operation_id": _identifier(value.get("split_operation_id"), "split operation id"),
    }
    if (
        canonical["parent_stage_id"] is not None
        and canonical["parent_stage_id"] == canonical["child_stage_id"]
    ):
        raise StageValidationError("lineage parent and child must differ")
    expected = request_fingerprint(canonical)
    if value.get("fingerprint") != expected:
        raise StageIntegrityError("stage lineage fingerprint mismatch")
    canonical["fingerprint"] = expected
    return canonical


def _dependency_cycle(dependencies: Mapping[str, list[str]], child_ids: set[str]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dependency in dependencies.get(node, []):
            if dependency in child_ids and visit(dependency):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in child_ids)


def create_split(
    parent: Mapping[str, object],
    *,
    operation_id: str,
    child_specs: Iterable[Mapping[str, object]],
    actor: str,
    terminalized_at: str,
    reason: str,
) -> JsonObject:
    """Create a deterministic, isolated child set and close its parent.

    This is a pure prospective transaction.  Its returned values are committed
    together by ``StageLifecycle.split_stage``; an exception leaves the input
    parent byte-for-byte unchanged.
    """
    current = validate_stage(parent)
    if current["state"] != "active":
        raise SplitValidationError("only an active stage can be split")
    operation = _identifier(operation_id, "split operation id")
    if isinstance(child_specs, (str, bytes, Mapping)):
        raise SplitValidationError("child specifications must be a list")
    try:
        specs = list(child_specs)
    except TypeError as exc:
        raise SplitValidationError("child specifications must be a list") from exc
    if len(specs) < 2 or len(specs) > MAX_COLLECTION_ITEMS:
        raise SplitValidationError("a split requires between 2 and 64 children (at least two)")

    canonical_specs: list[dict[str, object]] = []
    fingerprints: set[str] = set()
    for ordinal, raw in enumerate(specs):
        row = _closed(
            raw,
            _SPLIT_SPEC_FIELDS,
            f"child {ordinal} spec",
            optional=frozenset({"deliverables", "contracts"}),
        )
        budget = _budget(row.get("budget"))
        selected = _references(row.get("selected_artifacts"), f"child {ordinal} selected artifacts")
        dependencies = _strings(row.get("dependencies"), f"child {ordinal} dependencies")
        kind = _bounded_text(row.get("stage_kind"), f"child {ordinal} stage kind", maximum=64)
        if not _KIND.fullmatch(kind):
            raise SplitValidationError(f"child {ordinal} stage kind is invalid")
        deliverables = _strings(
            row.get("deliverables", current["deliverables"]), f"child {ordinal} deliverables"
        )
        contracts = _strings(
            row.get("contracts", current["contracts"]),
            f"child {ordinal} contracts",
            pattern=_CONTRACT,
        )
        input_ref = _portable_reference(
            row.get("input_manifest_ref"),
            f"child {ordinal} input manifest reference",
            manifest_bound=True,
        )
        if input_ref["kind"] != "stage-handoff":
            raise SplitValidationError(f"child {ordinal} needs an explicit stage handoff")
        canonical_spec = {
            "stage_kind": kind,
            "selected_artifacts": selected,
            "dependencies": dependencies,
            "budget": budget,
            "deliverables": deliverables,
            "contracts": contracts,
            "input_manifest_ref": input_ref,
        }
        fingerprint = request_fingerprint(canonical_spec)
        if fingerprint in fingerprints:
            raise SplitValidationError("split contains a duplicate child spec")
        fingerprints.add(fingerprint)
        canonical_specs.append(canonical_spec)

    parent_artifacts = {
        (str(row["kind"]), str(row["fingerprint"])) for row in current["selected_artifacts"]
    }
    for ordinal, spec in enumerate(canonical_specs):
        child_artifacts = {
            (str(row["kind"]), str(row["fingerprint"])) for row in spec["selected_artifacts"]
        }
        if not child_artifacts.issubset(parent_artifacts):
            raise SplitValidationError(f"child {ordinal} has an undeclared parent artifact subset")

    child_ids = [
        split_child_id(str(current["run_id"]), str(current["stage_id"]), operation, ordinal)
        for ordinal in range(len(canonical_specs))
    ]
    if len(set(child_ids)) != len(child_ids) or str(current["stage_id"]) in child_ids:
        raise SplitValidationError("split child identity collision")
    child_id_set = set(child_ids)
    external_dependencies = (
        set(current["dependencies"])
        | set(current["parent_stage_ids"])
        | set(current["predecessor_stage_ids"])
        | {str(current["stage_id"])}
    )
    resolved_dependencies: dict[str, list[str]] = {}
    for ordinal, spec in enumerate(canonical_specs):
        resolved: list[str] = []
        for dependency in spec["dependencies"]:
            match = re.fullmatch(r"child:(\d+)", str(dependency))
            value = (
                child_ids[int(match.group(1))]
                if match and int(match.group(1)) < len(child_ids)
                else str(dependency)
            )
            if value == child_ids[ordinal]:
                raise SplitValidationError("a split child cannot depend on itself")
            if value not in child_id_set and value not in external_dependencies:
                raise SplitValidationError(f"child {ordinal} has an unresolved dependency")
            resolved.append(value)
        if len(set(resolved)) != len(resolved):
            raise SplitValidationError(f"child {ordinal} has duplicate dependencies")
        resolved_dependencies[child_ids[ordinal]] = sorted(resolved)
    if _dependency_cycle(resolved_dependencies, child_id_set):
        raise SplitValidationError("split child dependencies contain a cycle")

    children: list[dict[str, object]] = []
    roots: set[str] = set()
    for ordinal, spec in enumerate(canonical_specs):
        child_id = child_ids[ordinal]
        root_id = f"execution-{child_id}"
        if root_id == current["execution_root_id"] or root_id in roots:
            raise SplitValidationError("split execution root collision")
        roots.add(root_id)
        child = create_stage(
            run_id=str(current["run_id"]),
            stage_id=child_id,
            requirement=current["requirement"],
            design=current["design"],
            stage_kind=str(spec["stage_kind"]),
            parent_stage_ids=[str(current["stage_id"])],
            predecessor_stage_ids=[],
            input_manifest_ref=spec["input_manifest_ref"],
            execution_root_id=root_id,
            deliverables=spec["deliverables"],
            budget=spec["budget"],
            dependencies=resolved_dependencies[child_id],
            contracts=spec["contracts"],
            authority=current["authority"],
            created_at=terminalized_at,
            selected_artifacts=spec["selected_artifacts"],
        )
        children.append(child)

    closed_parent = terminalize_stage(
        current,
        outcome="closed",
        actor=actor,
        terminalized_at=terminalized_at,
        reason_code="split",
        reason=reason,
    )
    lineage = [
        validate_lineage(
            _lineage_row(
                parent_stage_id=str(current["stage_id"]),
                child_stage_id=str(child["stage_id"]),
                input_manifest_ref=child["input_manifest_ref"],
                operation_id=operation,
            )
        )
        for child in children
    ]
    lineage.sort(key=lambda row: str(row["child_stage_id"]))
    return {
        "parent": closed_parent,
        "children": children,
        "lineage": lineage,
        "active_stage_ids": sorted(child_ids),
    }


def _bounded_stage_summary(stage: Mapping[str, object]) -> JsonObject:
    terminal = stage.get("terminal")
    terminal_row = terminal if isinstance(terminal, Mapping) else {}
    design = stage.get("design")
    design_row = design if isinstance(design, Mapping) else None
    input_ref = stage.get("input_manifest_ref")
    input_row = input_ref if isinstance(input_ref, Mapping) else {}
    completion = terminal_row.get("completion_evidence") or []
    body: JsonObject = {
        "schema": SUMMARY_SCHEMA,
        "stage_id": stage["stage_id"],
        "run_id": stage["run_id"],
        "stage_kind": stage["stage_kind"],
        "requirement": copy.deepcopy(stage["requirement"]),
        "design": copy.deepcopy(design_row),
        "state": stage["state"],
        "outcome": stage["outcome"],
        "default_consumable": stage["default_consumable"],
        "parent_stage_ids": copy.deepcopy(stage["parent_stage_ids"]),
        "predecessor_stage_ids": copy.deepcopy(stage["predecessor_stage_ids"]),
        "dependencies": copy.deepcopy(stage["dependencies"]),
        "input_manifest_fingerprint": input_row.get("fingerprint"),
        "execution_root_id": stage["execution_root_id"],
        "deliverables": copy.deepcopy(stage["deliverables"]),
        "completed_deliverables": copy.deepcopy(terminal_row.get("completed_deliverables") or []),
        "completion_evidence_fingerprints": sorted(
            str(row.get("fingerprint")) for row in completion if isinstance(row, Mapping)
        ),
        "actor": terminal_row.get("actor"),
        "terminalized_at": terminal_row.get("terminalized_at"),
        "reason_code": terminal_row.get("reason_code"),
        "reason": terminal_row.get("reason"),
        "aggregate_revision": stage["aggregate_revision"],
        # Preserve both the aggregate vocabulary used by the design and the
        # concise stage vocabulary consumed by early projection adapters.
        "aggregate_fingerprint": stage["fingerprint"],
        "stage_fingerprint": stage["fingerprint"],
    }
    body["fingerprint"] = request_fingerprint(body)
    size = len(review_evidence.canonical_bytes(body))
    if size > MAX_STAGE_SUMMARY_BYTES:
        raise StageValidationError(f"stage summary exceeds {MAX_STAGE_SUMMARY_BYTES} bytes")
    return body


def bounded_stage_summary(stage: Mapping[str, object]) -> JsonObject:
    """Return the <=16 KiB read model without opening an execution tree."""
    return _bounded_stage_summary(validate_stage(stage))


def _state_from_head(stage_id: str, head: Mapping[str, object]) -> tuple[str, str]:
    value: Mapping[str, object] = head
    if isinstance(head.get("summary"), Mapping):
        value = head["summary"]  # type: ignore[assignment]
        if value.get("schema") != SUMMARY_SCHEMA:
            raise StageValidationError("stage head summary schema is invalid")
        if len(review_evidence.canonical_bytes(value)) > MAX_STAGE_SUMMARY_BYTES:
            raise StageValidationError(
                f"stage head summary exceeds {MAX_STAGE_SUMMARY_BYTES} bytes"
            )
        expected = request_fingerprint(
            {key: item for key, item in value.items() if key != "fingerprint"}
        )
        if value.get("fingerprint") != expected:
            raise StageIntegrityError("stage head summary fingerprint mismatch")
        stage_value = _path_id(value.get("stage_id"), "stage head summary id")
        state = str(value.get("state") or "")
        aggregate_fingerprint = _fingerprint(
            value.get("aggregate_fingerprint"), "stage summary aggregate fingerprint"
        )
        if (
            _fingerprint(value.get("stage_fingerprint"), "stage summary fingerprint")
            != aggregate_fingerprint
        ):
            raise StageIntegrityError("stage summary fingerprint aliases disagree")
        reference = head.get("object")
        if (
            not isinstance(reference, Mapping)
            or _fingerprint(reference.get("fingerprint"), "stage object fingerprint")
            != aggregate_fingerprint
        ):
            raise StageIntegrityError("stage object and summary fingerprints disagree")
    elif value.get("schema") == SCHEMA:
        aggregate = validate_stage(value)
        stage_value = str(aggregate["stage_id"])
        state = str(aggregate["state"])
    else:
        # A compact head may expose summary fields directly.
        stage_value = _path_id(value.get("stage_id", stage_id), "stage head id")
        state = str(value.get("state") or "")
        if not _FINGERPRINT.fullmatch(
            str(value.get("stage_fingerprint") or value.get("fingerprint") or "")
        ):
            raise StageValidationError("stage head fingerprint is invalid")
    if stage_value != stage_id or state not in STAGE_STATES:
        raise StageValidationError("stage head identity or state is invalid")
    return stage_value, state


def active_stage_projection(
    stage_heads: Mapping[str, Mapping[str, object]], foreground_stage_id: str | None = None
) -> JsonObject:
    """Derive the replaceable active-stage cache from authoritative heads."""
    if not isinstance(stage_heads, Mapping):
        raise StageValidationError("stage heads must be an object")
    active: list[str] = []
    for raw_id, head in stage_heads.items():
        stage_id = _path_id(raw_id, "stage head id")
        if not isinstance(head, Mapping):
            raise StageValidationError("stage head must be an object")
        _, state = _state_from_head(stage_id, head)
        if state == "active":
            active.append(stage_id)
    active.sort()
    foreground = None
    if foreground_stage_id is not None:
        requested = _path_id(foreground_stage_id, "foreground stage id")
        foreground = requested if requested in active else None
    body: JsonObject = {
        "schema": PROJECTION_SCHEMA,
        "active_stage_ids": active,
        "foreground_stage_id": foreground,
    }
    body["fingerprint"] = request_fingerprint(body)
    return body


# Compatibility name for callers written while R-0004 was being planned.  The
# RunStore method with the same phrase performs the locked repair; this alias
# remains a pure projection and does not persist anything.
rebuild_active_stage_projection = active_stage_projection


def _stage_head(store: object, run_id: str, stage: Mapping[str, object]) -> dict[str, object]:
    checked = validate_stage(stage)
    put = getattr(store, "put_stage_object", None)
    if not callable(put):
        raise StageLifecycleError("run store cannot persist stage objects")
    reference = put(run_id, checked)
    if not isinstance(reference, Mapping):
        raise StageLifecycleError("run store returned an invalid stage object")
    return {
        "object": copy.deepcopy(dict(reference)),
        "summary": bounded_stage_summary(checked),
    }


def _read_indexed_stage(
    store: object,
    run_id: str,
    stage_id: str,
    head: object,
    *,
    expected_fingerprint: str | None = None,
) -> JsonObject:
    if not isinstance(head, Mapping) or set(head) != {"object", "summary"}:
        raise StageLifecycleError(f"stage head {stage_id} is invalid")
    reference = head.get("object")
    summary = head.get("summary")
    if not isinstance(reference, Mapping) or not isinstance(summary, Mapping):
        raise StageLifecycleError(f"stage head {stage_id} is incomplete")
    fingerprint = _fingerprint(reference.get("fingerprint"), "stage head fingerprint")
    if expected_fingerprint is not None and fingerprint != _fingerprint(
        expected_fingerprint, "expected stage fingerprint"
    ):
        raise StageLifecycleError("affected stage head fingerprint changed")
    read = getattr(store, "read_stage_object", None)
    if not callable(read):
        raise StageLifecycleError("run store cannot read stage objects")
    stage = validate_stage(read(run_id, dict(reference)))
    if stage["stage_id"] != stage_id or stage["fingerprint"] != fingerprint:
        raise StageIntegrityError("indexed stage object identity mismatch")
    if bounded_stage_summary(stage) != summary:
        raise StageIntegrityError("indexed stage summary does not match object")
    return stage


def _head_fingerprint(head: object, stage_id: str) -> str:
    if not isinstance(head, Mapping) or not isinstance(head.get("object"), Mapping):
        raise StageLifecycleError(f"stage head {stage_id} is invalid")
    return _fingerprint(head["object"].get("fingerprint"), "stage head fingerprint")
