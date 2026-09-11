"""Bounded, content-addressed handoff manifests for isolated stages.

The manifest is a closed control-plane value.  It never carries artifact
bodies or host paths and every referenced artifact is verified before a
successor can use the manifest.
"""

from __future__ import annotations
import copy
import sys

if __package__:
    from . import stage_values as _dispatch_stage_values
else:
    import stage_values as _dispatch_stage_values
if __package__:
    from .primitives import canonical_bytes as canonical_json_bytes, manifest_record
else:
    from primitives import canonical_bytes as canonical_json_bytes, manifest_record

if __package__:
    from . import stage_artifacts
else:
    import stage_artifacts

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import hmac
import json
import re
from typing import Final, TypeAlias

if __package__:
    from . import review_evidence
    from . import storage as runtime_storage
else:
    import review_evidence
    import storage as runtime_storage


SCHEMA: Final[str] = "taskplane.stage-handoff/v1"
MAX_MANIFEST_BYTES: Final[int] = 64 * 1024
MAX_ARTIFACT_REFERENCES: Final[int] = 64
TERMINAL_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        "done",
        "closed",
        "discarded",
    }
)
REQUIRED_EXCLUSIONS: Final[frozenset[str]] = frozenset(
    {
        "predecessor-agents",
        "predecessor-conversations",
        "predecessor-event-logs",
        "predecessor-tool-transcripts",
        "predecessor-leases",
        "predecessor-runtime-state",
        "undeclared-paths",
        "undeclared-tools",
        "secrets",
        "approvals",
    }
)

JsonObject: TypeAlias = dict[str, object]
_IDENTIFIER: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REPOSITORY_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_FINGERPRINT: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_COMMIT: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
# Preserve legacy IDs and namespaced/versioned IDs within the same body bound.
_CONTRACT: Final[re.Pattern[str]] = re.compile(
    r"^contract:(?=.{1,128}$)(?:[a-z][a-z0-9-]*|"
    r"[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+/v[1-9][0-9]*)$"
)
_MANIFEST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "producer",
        "requirement",
        "design",
        "target",
        "commit",
        "contracts",
        "deliverables",
        "evidence_references",
        "selected_artifacts",
        "exclusions",
        "authorization",
        "fingerprint",
    }
)
_AUTHORITY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "actor",
        "session_id",
        "authorized_at",
        "operation_id",
        "authority_record",
        "nonconsumable_reuse",
    }
)
_AUTHORITY_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "authority_schema",
        "revision",
        "fingerprint",
    }
)
_NONCONSUMABLE_REUSE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "producer_outcome",
        "authority_fingerprint",
    }
)
SIGNATURE_SCHEMA: Final[str] = "taskplane.contract-signature/v1"
SIGNATURE_ALGORITHM: Final[str] = "HMAC-SHA256"
_SIGNATURE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "algorithm",
        "key_id",
        "payload_schema",
        "payload",
        "issued_at",
        "expires_at",
        "freshness",
        "signature",
    }
)
_FRESHNESS_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "candidate_sha",
        "source_tree",
        "impact_manifest_fingerprint",
    }
)


class HandoffValidationError(ValueError):
    """The handoff does not satisfy its closed boundary contract."""


class HandoffIntegrityError(HandoffValidationError):
    """The canonical manifest identity does not match its content."""


class StaleAuthorityError(HandoffValidationError):
    """The handoff was authorized against an obsolete authority revision."""


def _closed(value: object, fields: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise HandoffValidationError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise HandoffValidationError(f"{label} field names must be strings")
    keys = set(value)
    unknown = keys - fields
    missing = fields - keys
    if unknown:
        raise HandoffValidationError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise HandoffValidationError(f"{label} has missing fields: {', '.join(sorted(missing))}")
    return value


def _identifier(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise HandoffValidationError(f"{label} is invalid")
    return text


def _fingerprint(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _FINGERPRINT.fullmatch(text):
        raise HandoffValidationError(f"{label} is invalid")
    return text


def _revision(value: object, label: str) -> str:
    if isinstance(value, bool):
        raise HandoffValidationError(f"{label} is invalid")
    text = str(value or "").strip()
    if not text or len(text) > 128:
        raise HandoffValidationError(f"{label} is invalid")
    return text


def _strings(
    values: object, label: str, *, pattern: re.Pattern[str] | None = None, allow_empty: bool = False
) -> list[str]:
    if not isinstance(values, list):
        raise HandoffValidationError(f"{label} must be a list")
    result: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise HandoffValidationError(f"{label} entries must be strings")
        value = raw.strip()
        if not value or len(value) > 256 or (pattern and not pattern.fullmatch(value)):
            raise HandoffValidationError(f"{label} contains an invalid entry")
        result.append(value)
    if not allow_empty and not result:
        raise HandoffValidationError(f"{label} must not be empty")
    if len(set(result)) != len(result):
        raise HandoffValidationError(f"{label} contains duplicate entries")
    return sorted(result)


def _portable_references(
    store: review_evidence.ArtifactStore, references: Iterable[dict[str, object]], label: str
) -> list[dict[str, object]]:
    if isinstance(references, (str, bytes, Mapping)):
        raise HandoffValidationError(f"{label} must be a list")
    result = [
        review_evidence.portable_artifact_reference(store, reference) for reference in references
    ]
    identities = [(row["kind"], row["fingerprint"]) for row in result]
    if len(set(identities)) != len(identities):
        raise HandoffValidationError(f"{label} contains duplicate references")
    return sorted(result, key=lambda row: (str(row["kind"]), str(row["fingerprint"])))


def _bounded_reference_inputs(
    evidence_references: Iterable[dict[str, object]],
    selected_artifacts: Iterable[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Collect at most the first 65 combined references before rejecting."""
    groups = (
        ("evidence references", evidence_references),
        ("selected artifacts", selected_artifacts),
    )
    collected: list[list[dict[str, object]]] = [[], []]
    count = 0
    for index, (label, references) in enumerate(groups):
        if isinstance(references, (str, bytes, Mapping)):
            raise HandoffValidationError(f"{label} must be a list")
        try:
            iterator = iter(references)
        except TypeError as exc:
            raise HandoffValidationError(f"{label} must be a list") from exc
        for reference in iterator:
            count += 1
            if count > MAX_ARTIFACT_REFERENCES:
                raise HandoffValidationError(
                    f"handoff contains at most {MAX_ARTIFACT_REFERENCES} artifact references"
                )
            collected[index].append(reference)
    return collected[0], collected[1]


def _validate_timestamp(value: object) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HandoffValidationError("authorization time is invalid") from exc
    if parsed.tzinfo is None:
        raise HandoffValidationError("authorization time needs a timezone")
    return text


def canonical_artifact_store(workspace: str) -> review_evidence.ArtifactStore:
    """Resolve the existing canonical run artifact boundary for a workspace."""
    runtime_storage.load_workspace_locator(workspace)
    return review_evidence.ArtifactStore(workspace)


def create_manifest(
    store: review_evidence.ArtifactStore,
    *,
    producer_stage_id: str,
    producer_outcome: str,
    requirement: Mapping[str, object],
    design: Mapping[str, object] | None,
    target: Mapping[str, object] | None,
    commit: Mapping[str, object] | None,
    contracts: Mapping[str, object],
    deliverables: Iterable[str],
    evidence_references: Iterable[dict[str, object]],
    selected_artifacts: Iterable[dict[str, object]],
    exclusions: Iterable[str],
    authorization: Mapping[str, object],
    allow_nonconsumable_reuse: bool = False,
) -> JsonObject:
    """Create and fully verify one deterministic stage handoff manifest.

    Closed or discarded results require the explicit reuse flag.  That
    decision is persisted inside the attributable authorization record so a
    later store/read cycle cannot lose it or infer it from the outcome.
    """
    evidence_rows, artifact_rows = _bounded_reference_inputs(
        evidence_references, selected_artifacts
    )
    contract_rows = _closed(contracts, frozenset({"provided", "consumed", "changed"}), "contracts")
    canonical_contracts = {
        relation: _strings(
            contract_rows.get(relation),
            f"contracts {relation}",
            pattern=_CONTRACT,
            allow_empty=True,
        )
        for relation in ("provided", "consumed", "changed")
    }
    canonical_deliverables = _strings(list(deliverables), "deliverables")
    canonical_exclusions = _strings(list(exclusions), "exclusions")
    portable_evidence = _portable_references(store, evidence_rows, "evidence references")
    portable_artifacts = _portable_references(store, artifact_rows, "selected artifacts")
    outcome = str(producer_outcome)
    if allow_nonconsumable_reuse and outcome not in {"closed", "discarded"}:
        raise HandoffValidationError(
            "nonconsumable reuse applies only to closed or discarded stages"
        )
    canonical_authorization = dict(authorization)
    authority_record = canonical_authorization.get("authority_record")
    authority_fingerprint = (
        authority_record.get("fingerprint") if isinstance(authority_record, Mapping) else None
    )
    canonical_authorization["nonconsumable_reuse"] = (
        {
            "schema": "taskplane.nonconsumable-reuse-authorization/v1",
            "producer_outcome": outcome,
            "authority_fingerprint": authority_fingerprint,
        }
        if allow_nonconsumable_reuse
        else None
    )
    body: JsonObject = {
        "schema": SCHEMA,
        "producer": {"stage_id": str(producer_stage_id), "outcome": outcome},
        "requirement": dict(requirement),
        "design": dict(design) if design is not None else None,
        "target": dict(target) if target is not None else None,
        "commit": dict(commit) if commit is not None else None,
        "contracts": canonical_contracts,
        "deliverables": canonical_deliverables,
        "evidence_references": portable_evidence,
        "selected_artifacts": portable_artifacts,
        "exclusions": canonical_exclusions,
        "authorization": canonical_authorization,
    }
    body = manifest_record(body)
    return validate_manifest(store, body, allow_nonconsumable_reuse=allow_nonconsumable_reuse)


def manifest_fingerprint(manifest: Mapping[str, object]) -> str:
    """Return the semantic fingerprint, excluding its self-reference."""
    material = {str(key): value for key, value in manifest.items() if str(key) != "fingerprint"}
    return review_evidence.content_fingerprint(material)


def _validate_portable_references(
    store: review_evidence.ArtifactStore, value: object, label: str
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise HandoffValidationError(f"{label} must be a list")
    identities: list[tuple[str, str]] = []
    for reference in value:
        try:
            review_evidence.verify_portable_artifact_reference(store, reference)
        except review_evidence.ArtifactIntegrityError as exc:
            if "unknown fields" in str(exc):
                raise HandoffValidationError(
                    "artifact reference has unknown fields: " + str(exc).split(": ", 1)[-1]
                ) from exc
            raise
        identities.append((str(reference["kind"]), str(reference["fingerprint"])))
    if len(set(identities)) != len(identities):
        raise HandoffValidationError(f"{label} contains duplicate references")
    canonical = sorted(value, key=lambda row: (str(row["kind"]), str(row["fingerprint"])))
    if value != canonical:
        raise HandoffValidationError(f"{label} is not in canonical order")
    return value


def validate_manifest(
    store: review_evidence.ArtifactStore,
    manifest: Mapping[str, object],
    *,
    expected_authority_revision: int | None = None,
    expected_authority_fingerprint: str | None = None,
    allow_nonconsumable_reuse: bool = False,
) -> JsonObject:
    """Validate schema, authority, artifact integrity, and numeric bounds."""
    if isinstance(manifest, Mapping) and manifest.get("schema") == "taskplane.stage-handoff/v2":
        checked = validate_v2_manifest(store, manifest)
        authority = checked["authorization"]["authority_record"]
        if (
            expected_authority_revision is not None
            and authority["revision"] != expected_authority_revision
        ):
            raise StaleAuthorityError("v2 handoff authority revision is stale")
        if (
            expected_authority_fingerprint is not None
            and authority["fingerprint"] != expected_authority_fingerprint
        ):
            raise StaleAuthorityError("v2 handoff authority fingerprint is stale")
        if checked["producer"]["outcome"] != "done":
            raise HandoffValidationError("current phase packages require a done producer")
        _validate_complete_v2_outputs(store, checked)
        return checked
    if isinstance(manifest, Mapping) and manifest.get("schema") != SCHEMA:
        raise HandoffValidationError("unsupported handoff manifest schema")
    row = _closed(manifest, _MANIFEST_FIELDS, "handoff manifest")
    if manifest_fingerprint(row) != row.get("fingerprint"):
        raise HandoffIntegrityError("handoff manifest fingerprint mismatch")

    producer = _closed(row.get("producer"), frozenset({"stage_id", "outcome"}), "producer")
    _identifier(producer.get("stage_id"), "producer stage id")
    outcome = str(producer.get("outcome") or "")
    if outcome not in TERMINAL_OUTCOMES:
        raise HandoffValidationError("producer outcome is not terminal")

    requirement = _closed(
        row.get("requirement"), frozenset({"id", "revision", "fingerprint"}), "requirement"
    )
    _identifier(requirement.get("id"), "requirement id")
    _revision(requirement.get("revision"), "requirement revision")
    _fingerprint(requirement.get("fingerprint"), "requirement fingerprint")

    design = row.get("design")
    if design is not None:
        design_row = _closed(design, frozenset({"revision", "fingerprint"}), "design")
        _revision(design_row.get("revision"), "design revision")
        _fingerprint(design_row.get("fingerprint"), "design fingerprint")

    target, commit = row.get("target"), row.get("commit")
    if (target is None) != (commit is None):
        raise HandoffValidationError("target and commit must appear together")
    if target is not None and commit is not None:
        target_row = _closed(target, frozenset({"repository_id", "fingerprint"}), "target")
        repository_id = str(target_row.get("repository_id") or "").strip()
        if not _REPOSITORY_ID.fullmatch(repository_id):
            raise HandoffValidationError("target repository id is invalid")
        target_fingerprint = _fingerprint(target_row.get("fingerprint"), "target fingerprint")
        commit_row = _closed(commit, frozenset({"sha", "target_fingerprint"}), "commit")
        if not _COMMIT.fullmatch(str(commit_row.get("sha") or "")):
            raise HandoffValidationError("commit sha is invalid")
        if commit_row.get("target_fingerprint") != target_fingerprint:
            raise HandoffValidationError("commit target fingerprint mismatch")

    contract_rows = _closed(
        row.get("contracts"), frozenset({"provided", "consumed", "changed"}), "contracts"
    )
    for relation in ("provided", "consumed", "changed"):
        values = _strings(
            contract_rows.get(relation),
            f"contracts {relation}",
            pattern=_CONTRACT,
            allow_empty=True,
        )
        if values != contract_rows.get(relation):
            raise HandoffValidationError(f"contracts {relation} is not in canonical order")
    deliverables = _strings(row.get("deliverables"), "deliverables")
    if deliverables != row.get("deliverables"):
        raise HandoffValidationError("deliverables are not in canonical order")
    canonical_exclusions = _strings(row.get("exclusions"), "exclusions")
    if canonical_exclusions != row.get("exclusions"):
        raise HandoffValidationError("exclusions are not in canonical order")
    exclusions = set(canonical_exclusions)
    missing_exclusions = REQUIRED_EXCLUSIONS - exclusions
    if missing_exclusions:
        raise HandoffValidationError(
            "handoff is missing required exclusions: " + ", ".join(sorted(missing_exclusions))
        )

    evidence = _validate_portable_references(
        store, row.get("evidence_references"), "evidence references"
    )
    if not evidence:
        raise HandoffValidationError("handoff evidence references are incomplete")
    artifacts = _validate_portable_references(
        store, row.get("selected_artifacts"), "selected artifacts"
    )
    if len(evidence) + len(artifacts) > MAX_ARTIFACT_REFERENCES:
        raise HandoffValidationError(
            f"handoff contains at most {MAX_ARTIFACT_REFERENCES} artifact references"
        )

    authority = _closed(row.get("authorization"), _AUTHORITY_FIELDS, "authorization")
    _identifier(authority.get("actor"), "authorization actor")
    _identifier(authority.get("session_id"), "authorization session id")
    _identifier(authority.get("operation_id"), "authorization operation id")
    _validate_timestamp(authority.get("authorized_at"))
    authority_record = _closed(
        authority.get("authority_record"), _AUTHORITY_RECORD_FIELDS, "authority record"
    )
    if (
        authority_record.get("schema") != "taskplane.authority-record-reference/v1"
        or authority_record.get("authority_schema") != "taskplane.consolidated-authorization/v1"
    ):
        raise HandoffValidationError("authority record schema is invalid")
    revision = authority_record.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise HandoffValidationError("authority revision is invalid")
    authority_fingerprint = _fingerprint(
        authority_record.get("fingerprint"), "authority fingerprint"
    )
    if expected_authority_revision is not None and revision != expected_authority_revision:
        raise StaleAuthorityError("handoff authority revision is stale")
    if (
        expected_authority_fingerprint is not None
        and authority_fingerprint != expected_authority_fingerprint
    ):
        raise StaleAuthorityError("handoff authority fingerprint is stale")

    reuse_authorization = authority.get("nonconsumable_reuse")
    if reuse_authorization is not None:
        reuse_row = _closed(
            reuse_authorization, _NONCONSUMABLE_REUSE_FIELDS, "nonconsumable reuse authorization"
        )
        if (
            reuse_row.get("schema") != "taskplane.nonconsumable-reuse-authorization/v1"
            or reuse_row.get("producer_outcome") != outcome
            or reuse_row.get("authority_fingerprint") != authority_fingerprint
            or outcome not in {"closed", "discarded"}
        ):
            raise HandoffValidationError("nonconsumable reuse authorization is invalid")
    if outcome in {"closed", "discarded"}:
        if reuse_authorization is None:
            raise HandoffValidationError(
                f"{outcome} stage results lack explicit reuse authorization"
            )
        if not allow_nonconsumable_reuse:
            raise HandoffValidationError(f"{outcome} stage results cannot be consumed by default")

    size = len(review_evidence.canonical_bytes(row))
    if size > MAX_MANIFEST_BYTES:
        raise HandoffValidationError(f"canonical handoff exceeds {MAX_MANIFEST_BYTES} bytes")
    return dict(row)


def store_manifest(
    store: review_evidence.ArtifactStore, manifest: Mapping[str, object]
) -> dict[str, object]:
    """Persist a structurally valid manifest without consuming its results."""
    validated = validate_manifest(store, manifest, allow_nonconsumable_reuse=True)
    return store.put("stage-handoff", validated, fingerprint=str(validated["fingerprint"]))


def read_manifest(
    store: review_evidence.ArtifactStore,
    reference: Mapping[str, object],
    *,
    expected_authority_revision: int,
    expected_authority_fingerprint: str,
    allow_nonconsumable_reuse: bool = False,
) -> JsonObject:
    """Consume a handoff bound to a separately trusted authority identity."""
    value = store.read(dict(reference))
    if not isinstance(value, Mapping):
        raise HandoffValidationError("stored handoff manifest must be an object")
    return validate_manifest(
        store,
        value,
        expected_authority_revision=expected_authority_revision,
        expected_authority_fingerprint=expected_authority_fingerprint,
        allow_nonconsumable_reuse=allow_nonconsumable_reuse,
    )


def validate_v2_manifest(
    store: review_evidence.ArtifactStore, manifest: Mapping[str, object]
) -> JsonObject:
    """Additive v2 schema reader, not a writer switch or migration authority.

    V1 fields are checked by their incumbent validator using a local structural
    projection. The projection is never stored, returned or treated as a v1
    authorization. The original v2 identity is checked independently.
    """
    # Delay this import to retain the stage -> handoff import direction.
    if __package__:
        from . import stage_values as entities
    else:
        import stage_values as flat_entities

        entities = flat_entities
    row = _closed(
        manifest, _MANIFEST_FIELDS | entities._HANDOFF_V2_ADDITIONS, "v2 handoff manifest"
    )
    if row["schema"] != entities.HANDOFF_V2_SCHEMA:
        raise HandoffValidationError("unsupported v2 handoff schema")
    if manifest_fingerprint(row) != row["fingerprint"]:
        raise HandoffIntegrityError("v2 handoff fingerprint mismatch")
    base = {key: value for key, value in row.items() if key in _MANIFEST_FIELDS}
    base["schema"] = SCHEMA
    base["fingerprint"] = manifest_fingerprint(base)
    validate_manifest(store, base, allow_nonconsumable_reuse=True)
    result = row["phase_result"]
    if not isinstance(result, Mapping) or result.get("schema") != entities.AGENT_RUNTIME_SCHEMA:
        raise HandoffValidationError("v2 handoff requires an agent runtime result")
    entities.validate_contract(result, store=store)
    producer = _closed(row["producer"], frozenset({"stage_id", "outcome"}), "producer")
    if producer["outcome"] == "done" and result["status"] != "accepted":
        raise HandoffValidationError("done handoff requires an accepted result")
    selected = set()
    for raw in entities._contract_list(row["selected_artifacts"], "selected artifacts"):
        selected_reference = entities._portable_reference(raw, "selected artifact")
        selected.add((selected_reference["kind"], selected_reference["fingerprint"]))
    seen = set()
    for group in ("produced_artifacts", "inherited_artifacts"):
        entities._schema_artifacts(row[group], group, references=True)
        for artifact in entities._contract_list(row[group], group):
            item = entities._closed(
                artifact,
                frozenset({"artifact_class", "artifact_schema_version", "reference"}),
                group,
            )
            reference = entities._portable_reference(item["reference"], "artifact reference")
            identity = (reference["kind"], reference["fingerprint"])
            if identity not in selected:
                raise HandoffValidationError("v2 artifact is not selected")
            if identity in seen:
                raise HandoffValidationError("v2 artifact has duplicate ownership")
            seen.add(identity)
            review_evidence.verify_portable_artifact_reference(store, reference)
    if seen != selected:
        raise HandoffValidationError("v2 selected artifact lacks schema and ownership")
    for receipt in entities._contract_list(
        row["knowledge_apply_receipts"], "knowledge apply receipts"
    ):
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("schema") != entities.KNOWLEDGE_APPLY_SCHEMA
        ):
            raise HandoffValidationError("unsupported knowledge apply receipt")
        entities.validate_contract(receipt, store=store)
    entities._contract_strings(row["unresolved_issues"], "unresolved issues")
    if len(entities._contract_json(dict(row))) > MAX_MANIFEST_BYTES:
        raise HandoffValidationError("v2 handoff exceeds manifest bound")
    return dict(row)


def create_v2_manifest(
    store: review_evidence.ArtifactStore,
    *,
    phase_result: Mapping[str, object],
    produced_artifacts: Iterable[dict[str, object]],
    inherited_artifacts: Iterable[dict[str, object]],
    producer_stage_id: str,
    producer_outcome: str,
    requirement: Mapping[str, object],
    design: Mapping[str, object] | None,
    target: Mapping[str, object] | None,
    commit: Mapping[str, object] | None,
    contracts: Mapping[str, object],
    deliverables: Iterable[str],
    evidence_references: Iterable[dict[str, object]],
    exclusions: Iterable[str],
    authorization: Mapping[str, object],
    allow_nonconsumable_reuse: bool = False,
    knowledge_apply_receipts: Iterable[dict[str, object]] = (),
    unresolved_issues: Iterable[str] = (),
) -> JsonObject:
    """Explicit complete-package writer; existing lifecycle writes stay v1.

    Collection owns produced bytes. Callers cannot relabel inherited artifacts
    as outputs, omit an output, or publish an uncollected replacement.
    """
    produced, inherited = _bounded_reference_inputs(produced_artifacts, inherited_artifacts)
    selected = []
    for row in produced + inherited:
        reference = row.get("reference")
        if not isinstance(reference, dict):
            raise HandoffValidationError("artifact reference must be an object")
        selected.append(reference)
    base = create_manifest(
        store,
        selected_artifacts=selected,
        producer_stage_id=producer_stage_id,
        producer_outcome=producer_outcome,
        requirement=requirement,
        design=design,
        target=target,
        commit=commit,
        contracts=contracts,
        deliverables=deliverables,
        evidence_references=evidence_references,
        exclusions=exclusions,
        authorization=authorization,
        allow_nonconsumable_reuse=allow_nonconsumable_reuse,
    )
    body = {
        **base,
        "schema": "taskplane.stage-handoff/v2",
        "phase_result": dict(phase_result),
        "produced_artifacts": produced,
        "inherited_artifacts": inherited,
        "knowledge_apply_receipts": list(knowledge_apply_receipts),
        "unresolved_issues": list(unresolved_issues),
    }
    body["fingerprint"] = manifest_fingerprint(body)
    validated = validate_v2_manifest(store, body)
    _validate_complete_v2_outputs(store, validated)
    return validated


def _validate_complete_v2_outputs(
    store: review_evidence.ArtifactStore, manifest: Mapping[str, object]
) -> None:
    """Current package admission, separate from historical structural reading."""
    result = manifest["phase_result"]
    if not isinstance(result, Mapping):
        raise HandoffValidationError("phase result must be an object")
    authorization = _closed(manifest["authorization"], _AUTHORITY_FIELDS, "authorization")
    authority = _closed(
        authorization["authority_record"], _AUTHORITY_RECORD_FIELDS, "authority record"
    )
    if result["authority_fingerprint"] != authority["fingerprint"]:
        raise HandoffValidationError("phase result authority differs from handoff")
    target = manifest["target"]
    if isinstance(target, Mapping) and result["candidate_fingerprint"] != target["fingerprint"]:
        raise HandoffValidationError("phase result candidate differs from handoff")
    produced = _v2_artifact_rows(manifest["produced_artifacts"])
    inherited = _v2_artifact_rows(manifest["inherited_artifacts"])
    actual = _portable_references(
        store, (_v2_reference(row) for row in produced), "produced artifacts"
    )
    collected = _validate_portable_references(
        store,
        sorted(
            _v2_collected_references(result["collected_output_references"]),
            key=lambda row: (str(row["kind"]), str(row["fingerprint"])),
        ),
        "collected outputs",
    )
    if actual != collected:
        raise HandoffValidationError("complete package differs from collected outputs")
    raw_declarations = result["produced_artifact_schema_versions"]
    if not isinstance(raw_declarations, list):
        raise HandoffValidationError("output declarations must be a list")
    declarations = []
    for raw in raw_declarations:
        declaration = _closed(
            raw, frozenset({"artifact_class", "artifact_schema_version"}), "output declaration"
        )
        declarations.append((declaration["artifact_class"], declaration["artifact_schema_version"]))
    classes = set()
    for group, rows in (("produced_artifacts", produced), ("inherited_artifacts", inherited)):
        for row in rows:
            artifact_class = str(row["artifact_class"])
            if artifact_class in classes:
                raise HandoffValidationError("complete package has ambiguous artifact ownership")
            classes.add(artifact_class)
            identity = (row["artifact_class"], row["artifact_schema_version"])
            if group == "produced_artifacts" and identity not in declarations:
                raise HandoffValidationError("output schema is not declared by runtime")
            payload = store.read(_v2_reference(row))
            try:
                stage_artifacts.validate(artifact_class, payload)
            except ValueError as exc:
                raise HandoffValidationError(str(exc)) from exc


def _v2_artifact_rows(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise HandoffValidationError("schema artifacts must be a list")
    return [
        _closed(
            row,
            frozenset({"artifact_class", "artifact_schema_version", "reference"}),
            "schema artifact",
        )
        for row in value
    ]


def _v2_reference(row: Mapping[str, object]) -> dict[str, object]:
    reference = row["reference"]
    if not isinstance(reference, dict):
        raise HandoffValidationError("artifact reference must be an object")
    return dict(reference)


def _v2_collected_references(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise HandoffValidationError("collected outputs must be a list of references")
    return [dict(row) for row in value]


def store_v2_manifest(
    store: review_evidence.ArtifactStore, manifest: Mapping[str, object]
) -> JsonObject:
    """Persist only complete current v2 packages without changing the v1 writer."""
    validated = validate_v2_manifest(store, manifest)
    _validate_complete_v2_outputs(store, validated)
    return store.put("stage-handoff", validated, fingerprint=str(validated["fingerprint"]))


def read_v2_manifest(
    store: review_evidence.ArtifactStore,
    reference: Mapping[str, object],
    *,
    expected_authority_revision: int,
    expected_authority_fingerprint: str,
) -> JsonObject:
    """Consume current v2 bytes under separately supplied authority; no downgrade."""
    value = validate_v2_manifest(store, store.read(dict(reference)))
    authorization = _closed(value["authorization"], _AUTHORITY_FIELDS, "authorization")
    authority = _closed(
        authorization["authority_record"], _AUTHORITY_RECORD_FIELDS, "authority record"
    )
    if (
        authority["revision"] != expected_authority_revision
        or authority["fingerprint"] != expected_authority_fingerprint
    ):
        raise StaleAuthorityError("v2 handoff authority is stale")
    producer = _closed(value["producer"], frozenset({"stage_id", "outcome"}), "producer")
    if producer["outcome"] != "done":
        raise HandoffValidationError("current phase packages require a done producer")
    _validate_complete_v2_outputs(store, value)
    return value


def _seconds(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise HandoffValidationError(f"{label} must be non-negative integer seconds")
    return value


def prepare_knowledge_proposal(
    value: Mapping[str, object], *, expected_scope: list[str]
) -> JsonObject:
    """Validate a bounded fact proposal without writing or granting authority.

    This domain boundary is deliberately stricter than the historical value
    reader: portable facts are structured observations, never strategy prose.
    The existing evidence-reference spelling remains the v1 wire spelling.
    """
    if __package__:
        from . import stage_values as entities
    else:
        import stage_values as flat_entities

        entities = flat_entities
    row = entities.create_contract(value)
    if row["schema"] != entities.KNOWLEDGE_UPDATE_SCHEMA:
        raise HandoffValidationError("unsupported knowledge proposal")
    if row["scope"] != expected_scope or len(expected_scope) != 2:
        raise HandoffValidationError("knowledge scope mismatch")
    if not expected_scope[0].startswith("repository:") or expected_scope[1] != "phase:" + str(
        row["phase_id"]
    ):
        raise HandoffValidationError("knowledge repository/phase scope is invalid")
    _identifier(expected_scope[0][len("repository:") :], "knowledge repository")
    reference = row["finding_or_observation_reference"]
    if not isinstance(reference, str) or not re.fullmatch(
        r"artifact://(?:finding|observation)/[0-9a-f]{64}", reference
    ):
        raise HandoffValidationError("knowledge observation provenance is invalid")
    content = row["content"]
    if not isinstance(content, str):
        raise HandoffValidationError("knowledge content is invalid")
    if row["content_class"] == "evidence-reference":
        if not re.fullmatch(r"artifact://[a-z][a-z0-9-]*/[0-9a-f]{64}", content):
            raise HandoffValidationError("knowledge evidence reference is invalid")
    else:
        try:
            fact = json.loads(content)
        except ValueError as exc:
            raise HandoffValidationError("knowledge requires a structured fact") from exc
        fact = _closed(fact, frozenset({"subject", "predicate", "value"}), "knowledge fact")
        _identifier(fact["subject"], "fact subject")
        _identifier(fact["predicate"], "fact predicate")
        if type(fact["value"]) not in {bool, int}:
            raise HandoffValidationError("knowledge fact requires a boolean or integer observation")
    supersedes = row["supersedes"]
    if not isinstance(supersedes, list):
        raise HandoffValidationError("supersedes must be a list")
    for superseded in supersedes:
        _fingerprint(superseded, "superseded proposal")
    return row


def consume_knowledge_receipt(
    receipt: Mapping[str, object],
    *,
    proposal: Mapping[str, object],
    trusted_keys: Mapping[str, SigningKey],
    expected_freshness: Mapping[str, object],
    now: int,
) -> JsonObject:
    """Consume the owner's actual authenticated receipt bound to one proposal."""
    if __package__:
        from . import stage_values as entities
    else:
        import stage_values as flat_entities

        entities = flat_entities
    source = entities.validate_contract(proposal)
    if source["schema"] != entities.KNOWLEDGE_UPDATE_SCHEMA:
        raise HandoffValidationError("unsupported knowledge proposal")
    verified = verify_contract(
        receipt,
        trusted_keys=trusted_keys,
        expected_schema=entities.KNOWLEDGE_APPLY_SCHEMA,
        expected_freshness=expected_freshness,
        now=now,
    )
    result = verified["payload"]
    if not isinstance(result, dict):
        raise HandoffValidationError("knowledge receipt is invalid")
    for receipt_field, proposal_field in (
        ("proposal_fingerprint", "proposal_fingerprint"),
        ("operation_id", "operation_id"),
        ("base_fingerprint", "base_knowledge_fingerprint"),
    ):
        if result[receipt_field] != source[proposal_field]:
            raise HandoffValidationError("knowledge receipt proposal binding mismatch")
    if (
        result["outcome"] == "applied"
        and result["base_fingerprint"] != result["current_fingerprint"]
    ):
        raise HandoffValidationError("knowledge receipt violates compare-and-swap")
    return result


@dataclass(frozen=True)
class SigningKey:
    """Trusted in-memory key policy supplied by the incumbent host key owner.

    HMAC verification is local to the trusted control plane: verifiers possess
    signing authority and this is not a public-key signature. Secret storage,
    key generation, fencing and emergency effect shutdown belong to that owner.
    No payload, key ID or embedded certificate can establish a trusted key.
    """

    key_id: str
    secret: bytes = field(repr=False)
    not_before: int
    not_after: int
    status: str = "active"
    changed_at: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not _IDENTIFIER.fullmatch(self.key_id):
            raise HandoffValidationError("signing key id is invalid")
        if not isinstance(self.secret, bytes) or len(self.secret) < 32:
            raise HandoffValidationError("signing key requires at least 256 bits")
        _seconds(self.not_before, "key not-before")
        _seconds(self.not_after, "key not-after")
        if self.not_after <= self.not_before:
            raise HandoffValidationError("signing key validity interval is invalid")
        if self.status not in {"active", "retired", "revoked", "compromised"}:
            raise HandoffValidationError("signing key status is invalid")
        if self.status == "active":
            if self.changed_at is not None:
                raise HandoffValidationError("active key has a disable time")
        elif (
            self.changed_at is None
            or _seconds(self.changed_at, "key change time") < self.not_before
        ):
            raise HandoffValidationError("disabled key requires a valid change time")


def _trusted_keys(keys: Mapping[str, SigningKey]) -> dict[str, SigningKey]:
    if not isinstance(keys, Mapping):
        raise HandoffValidationError("trusted keys must be a mapping")
    result = dict(keys)
    for key_id, key in result.items():
        if not isinstance(key, SigningKey) or key.key_id != key_id:
            raise HandoffValidationError("trusted key identity mismatch")
    return result


def rotate_signing_key(
    keys: Mapping[str, SigningKey], key_id: str, replacement: SigningKey, *, at: int
) -> dict[str, SigningKey]:
    """Return a replacement policy retaining verification history; never mutate."""
    result = _trusted_keys(keys)
    _seconds(at, "rotation time")
    current = result.get(key_id)
    if (
        current is None
        or not isinstance(replacement, SigningKey)
        or replacement.key_id in result
        or replacement.status != "active"
        or not replacement.not_before <= at < replacement.not_after
        or hmac.compare_digest(current.secret, replacement.secret)
    ):
        raise HandoffValidationError("signing key rotation is invalid")
    if at < current.not_before or (current.changed_at is not None and at < current.changed_at):
        raise HandoffValidationError("rotation time precedes key history")
    if current.status == "active":
        result[key_id] = replace(current, status="retired", changed_at=at)
    result[replacement.key_id] = replacement
    return result


def disable_signing_key(
    keys: Mapping[str, SigningKey], key_id: str, *, at: int, compromised: bool = False
) -> dict[str, SigningKey]:
    """Pure revocation policy update. T03 atomically applies it with effect fencing."""
    result = _trusted_keys(keys)
    _seconds(at, "key disable time")
    if type(compromised) is not bool:
        raise HandoffValidationError("compromise marker must be boolean")
    current = result.get(key_id)
    if (
        current is None
        or at < current.not_before
        or (current.changed_at is not None and at < current.changed_at)
    ):
        raise HandoffValidationError("key disable history is invalid")
    status = "compromised" if compromised else "revoked"
    if current.status == "compromised" or current.status == status:
        return result  # replay cannot erase or move the original cutoff
    result[key_id] = replace(current, status=status, changed_at=at)
    return result


def _freshness(value: object) -> dict[str, object]:
    row = _closed(value, _FRESHNESS_FIELDS, "signature freshness")
    for key in ("candidate_sha", "source_tree"):
        identity = row[key]
        if not isinstance(identity, str) or not _COMMIT.fullmatch(identity):
            raise HandoffValidationError(f"signature freshness {key} is invalid")
    _fingerprint(row["impact_manifest_fingerprint"], "impact manifest fingerprint")
    return dict(row)


def _signature_bytes(value: Mapping[str, object]) -> bytes:
    material = {key: item for key, item in value.items() if key != "signature"}
    # Domain separation plus authenticated algorithm/schema/key/binding metadata
    # makes substitution and unsigned digest fallback impossible.
    return SIGNATURE_SCHEMA.encode("ascii") + b"\0" + review_evidence.canonical_bytes(material)


def sign_contract(
    value: Mapping[str, object],
    *,
    key: SigningKey,
    issued_at: int,
    expires_at: int,
    freshness: Mapping[str, object],
    store: review_evidence.ArtifactStore | None = None,
) -> JsonObject:
    """Authenticate a validated value; does not publish or activate a producer."""
    _seconds(issued_at, "signature issued-at")
    _seconds(expires_at, "signature expires-at")
    if not isinstance(key, SigningKey) or key.status != "active":
        raise HandoffValidationError("key issuance is disabled")
    if not key.not_before <= issued_at < expires_at <= key.not_after:
        raise HandoffValidationError("signature issuance is outside key validity")
    if __package__:
        from . import stage_values as entities
    else:
        import stage_values as flat_entities

        entities = flat_entities
    payload = (
        validate_manifest(value, store=store)
        if value.get("schema") in {SCHEMA, entities.HANDOFF_V2_SCHEMA}
        else entities.validate_contract(value, store=store)
    )
    result = {
        "schema": SIGNATURE_SCHEMA,
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": key.key_id,
        "payload_schema": payload["schema"],
        "payload": payload,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "freshness": _freshness(freshness),
    }
    result["signature"] = hmac.new(key.secret, _signature_bytes(result), hashlib.sha256).hexdigest()
    return result


def verify_contract(
    value: Mapping[str, object],
    *,
    trusted_keys: Mapping[str, SigningKey],
    expected_schema: str,
    expected_freshness: Mapping[str, object],
    now: int,
    historical: bool = False,
    store: review_evidence.ArtifactStore | None = None,
) -> JsonObject:
    """Verify using out-of-band trust, exact schema and candidate bindings.

    Historical verification reports mathematical integrity and key status but
    always returns authority_valid=False. Revoked/compromised/expired records
    remain readable without becoming current authority or rewriting originals.
    A valid current signature is authentication only; domain gates still apply.
    """
    row = _closed(value, _SIGNATURE_FIELDS, "signed contract")
    if row["schema"] != SIGNATURE_SCHEMA:
        raise HandoffValidationError("unsupported signature schema")
    if row["algorithm"] != SIGNATURE_ALGORITHM:
        raise HandoffValidationError("unsupported signature algorithm; downgrade refused")
    if type(historical) is not bool:
        raise HandoffValidationError("historical mode must be boolean")
    if row["payload_schema"] != expected_schema:
        raise HandoffValidationError("signature payload schema downgrade or mismatch")
    if not isinstance(row["key_id"], str):
        raise HandoffValidationError("untrusted signing key")
    key = _trusted_keys(trusted_keys).get(row["key_id"])
    if key is None:
        raise HandoffValidationError("untrusted signing key")
    freshness = _freshness(row["freshness"])
    if freshness != _freshness(expected_freshness):
        raise HandoffValidationError("signature freshness mismatch")
    if __package__:
        from . import stage_values as entities
    else:
        import stage_values as flat_entities

        entities = flat_entities
    raw_payload = row["payload"]
    payload = (
        validate_manifest(raw_payload, store=store)
        if isinstance(raw_payload, Mapping)
        and raw_payload.get("schema") in {SCHEMA, entities.HANDOFF_V2_SCHEMA}
        else entities.validate_contract(raw_payload, store=store)
    )
    if payload["schema"] != expected_schema:
        raise HandoffValidationError("signature payload schema mismatch")
    signature = row["signature"]
    if not isinstance(signature, str) or not _FINGERPRINT.fullmatch(signature):
        raise HandoffIntegrityError("signature is invalid")
    expected = hmac.new(key.secret, _signature_bytes(row), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HandoffIntegrityError("signature mismatch")
    issued = _seconds(row["issued_at"], "signature issued-at")
    expires = _seconds(row["expires_at"], "signature expires-at")
    _seconds(now, "verification time")
    if not key.not_before <= issued < expires <= key.not_after:
        raise HandoffValidationError("signature issuance is outside key validity")
    if now < issued:
        raise HandoffValidationError("signature is not yet valid")
    if key.changed_at is not None and issued >= key.changed_at:
        raise HandoffValidationError("signature issuance occurred after key disable")
    if not historical:
        if key.status in {"revoked", "compromised"}:
            raise HandoffValidationError(f"signing key is {key.status}")
        if now >= expires:
            raise HandoffValidationError("signature is expired")
    return {
        "payload": payload,
        "signature_valid": True,
        "authority_valid": not historical,
        "key_status": key.status,
        "historical": historical,
        "key_id": key.key_id,
        "freshness": freshness,
    }


@dataclass(frozen=True)
class PhasePackage:
    """Orchestrator-held verified input; agents receive only its artifact values."""

    store: object
    registry: object
    reference: Mapping[str, object]
    phase_id: str
    authority_revision: int
    authority_fingerprint: str
    run_id: str
    candidate_fingerprint: str

    def manifest(self) -> dict:
        value = read_v2_manifest(
            self.store,
            self.reference,
            expected_authority_revision=self.authority_revision,
            expected_authority_fingerprint=self.authority_fingerprint,
        )
        result = value["phase_result"]
        _phase_result_definition(self.registry, result)
        definition = self.registry.admit(self.phase_id, ()).to_dict()
        if result["phase_id"] not in definition["predecessors"]:
            raise ValueError("package producer is not a declared predecessor")
        if (
            result["run_id"] != self.run_id
            or result["candidate_fingerprint"] != self.candidate_fingerprint
        ):
            raise ValueError("package run or candidate differs from current input")
        return value

    @property
    def artifacts(self) -> tuple:
        # The direct CLI's flat loop and the native runtime share one typed
        # artifact identity, not distinct dataclasses with identical fields.
        from taskplane import agent_runtime

        value = self.manifest()
        rows = value["produced_artifacts"] + value["inherited_artifacts"]
        declarations = self.registry.admit(self.phase_id, ()).to_dict()["consumes"]
        selected = []
        for declaration in declarations:
            matches = [
                row for row in rows if row["artifact_class"] == declaration["artifact_class"]
            ]
            if len(matches) > 1 or (declaration["required"] and not matches):
                raise ValueError("package lacks an unambiguous required artifact")
            for row in matches:
                if row["artifact_schema_version"] != declaration["artifact_schema_version"]:
                    raise ValueError("package consumed schema differs from definition")
                selected.append(
                    agent_runtime.Artifact(
                        row["artifact_class"], row["artifact_schema_version"], row["reference"]
                    )
                )
        return tuple(selected)

    def read(self, artifact_class: str) -> dict:
        matches = [
            artifact for artifact in self.artifacts if artifact.artifact_class == artifact_class
        ]
        if len(matches) != 1:
            raise ValueError("package artifact is missing or ambiguous: " + artifact_class)
        return stage_artifacts.read(self.store, dict(matches[0].reference), artifact_class)


def _phase_result_definition(registry: object, result: Mapping[str, object]) -> dict:
    if __package__:
        from . import stage_values as stage_entities
    else:
        import stage_values as stage_entities

    stage_entities.validate_contract(result)
    definition = registry.admit(str(result["phase_id"]), ()).to_dict()
    expected = {
        "definition_set_fingerprint": registry.definition_set_fingerprint,
        "phase_definition_fingerprint": definition["fingerprint"],
        "skill_content_fingerprint": definition["skill_content_fingerprint"],
        "validator_identities": definition["domain_validator_refs"],
        "validator_inventory_fingerprint": definition["validator_inventory_fingerprint"],
        "capability_set_fingerprint": registry.capability_set_fingerprint,
        "budget": definition["budget"],
    }
    for relation, field in (
        ("consumes", "consumed_artifact_schema_versions"),
        ("produces", "produced_artifact_schema_versions"),
    ):
        expected[field] = [
            {key: row[key] for key in ("artifact_class", "artifact_schema_version")}
            for row in definition[relation]
        ]
    if result["status"] != "accepted" or any(
        result[key] != value for key, value in expected.items()
    ):
        raise ValueError("phase result differs from the current definition")
    return definition


def consume_phase_handoff(
    store: object,
    reference: Mapping[str, object],
    *,
    registry: object,
    phase_id: str,
    expected_authority_revision: int,
    expected_authority_fingerprint: str,
    expected_run_id: str,
    expected_candidate_fingerprint: str,
) -> PhasePackage:
    """Explicit package route; no active lifecycle selector is changed here."""
    from taskplane import phase_amendment

    amended = phase_amendment.package(
        store,
        dict(reference),
        registry=registry,
        phase_id=phase_id,
        expected_authority_revision=expected_authority_revision,
        expected_authority_fingerprint=expected_authority_fingerprint,
        expected_run_id=expected_run_id,
        expected_candidate_fingerprint=expected_candidate_fingerprint,
    )
    if amended is not None:
        return amended
    package = PhasePackage(
        store,
        registry,
        copy.deepcopy(dict(reference)),
        phase_id,
        expected_authority_revision,
        expected_authority_fingerprint,
        expected_run_id,
        expected_candidate_fingerprint,
    )
    package.artifacts  # Validate the whole package before exposing any output.
    return package


def produce_phase_handoff(
    store: object,
    *,
    registry: object,
    phase_result: Mapping[str, object],
    dispatch: object,
    predecessor: Mapping[str, object] | None,
    authorization: Mapping[str, object],
    producer_stage_id: str,
    requirement: Mapping[str, object],
    design: Mapping[str, object] | None,
    knowledge_apply_receipts: Iterable[dict] = (),
    unresolved_issues: Iterable[str] = (),
) -> dict:
    """Compose collected outputs and all inherited bytes using the sole writer.

    The active phase runtime uses this exact output boundary. Collection
    grants no evaluation or gate authority.
    """
    if __package__:
        from . import agent_runtime, review_evidence
    else:
        import agent_runtime
        import review_evidence

    definition = _phase_result_definition(registry, phase_result)
    authority = authorization["authority_record"]
    inherited = []
    inputs = ()
    if predecessor is not None:
        package = consume_phase_handoff(
            store,
            predecessor,
            registry=registry,
            phase_id=str(phase_result["phase_id"]),
            expected_authority_revision=authority["revision"],
            expected_authority_fingerprint=authority["fingerprint"],
            expected_run_id=str(phase_result["run_id"]),
            expected_candidate_fingerprint=str(phase_result["candidate_fingerprint"]),
        )
        previous = package.manifest()
        inputs = package.artifacts
        inherited = previous["produced_artifacts"] + previous["inherited_artifacts"]
    elif not definition["entry"]:
        raise ValueError("non-entry package requires its actual predecessor")
    if (
        dispatch.package != inputs
        or any(phase_result.get(key) != value for key, value in dispatch.bindings.items())
        or phase_result["sealed_package_fingerprint"]
        != agent_runtime.package_fingerprint(inputs, dispatch.knowledge, dispatch.envelope)
    ):
        raise ValueError("runtime output differs from its actual input package")
    produced = []
    for reference in phase_result["collected_output_references"]:
        portable = review_evidence.portable_artifact_reference(store, reference)
        payload = store.read(portable)
        matches = [
            row
            for row in definition["produces"]
            if row["artifact_class"] == portable["kind"]
            and row["artifact_schema_version"] == payload.get("schema")
        ]
        if len(matches) != 1:
            raise ValueError("collected output has no unambiguous declaration")
        produced.append(
            {
                "artifact_class": matches[0]["artifact_class"],
                "artifact_schema_version": matches[0]["artifact_schema_version"],
                "reference": portable,
            }
        )
    for declaration in definition["produces"]:
        count = sum(row["artifact_class"] == declaration["artifact_class"] for row in produced)
        if (declaration["required"] and not count) or (
            count > 1 and declaration["cardinality"] != "many"
        ):
            raise ValueError("complete package lacks a declared output")
    overlapping = {row["artifact_class"] for row in inherited} & {
        row["artifact_class"] for row in produced
    }
    for artifact_class in overlapping:
        consumed = [
            row for row in definition["consumes"] if row["artifact_class"] == artifact_class
        ]
        declared = [
            row for row in definition["produces"] if row["artifact_class"] == artifact_class
        ]
        old = [row for row in inherited if row["artifact_class"] == artifact_class]
        fresh = [row for row in produced if row["artifact_class"] == artifact_class]
        if (
            len(consumed) != 1
            or len(declared) != 1
            or len(old) != 1
            or len(fresh) != 1
            or declared[0]["cardinality"] != "one"
            or len(
                {
                    row["artifact_schema_version"]
                    for row in (consumed[0], declared[0], old[0], fresh[0])
                }
            )
            != 1
        ):
            raise ValueError("package replacement is not an unambiguous declared transformation")
    # Selection is not retention: the current collected transformation is the
    # successor's sole input of this class. The exact predecessor handoff below
    # remains immutable evidence, retaining all prior references and bytes.
    inherited = [row for row in inherited if row["artifact_class"] not in overlapping]
    evidence = store.put("phase-result", dict(phase_result))
    value = create_v2_manifest(
        store,
        phase_result=phase_result,
        produced_artifacts=produced,
        inherited_artifacts=inherited,
        knowledge_apply_receipts=knowledge_apply_receipts,
        unresolved_issues=unresolved_issues,
        producer_stage_id=producer_stage_id,
        producer_outcome="done",
        requirement=requirement,
        design=design,
        target=None,
        commit=None,
        contracts={"provided": [], "consumed": [], "changed": []},
        deliverables=[row["artifact_class"] for row in produced],
        evidence_references=[evidence] + ([] if predecessor is None else [dict(predecessor)]),
        exclusions=sorted(REQUIRED_EXCLUSIONS),
        authorization=authorization,
    )
    return store_v2_manifest(store, value)


STAGE_DISPATCH_SCHEMA = "taskplane.stage-dispatch/v1"
STAGE_STARTUP_SCHEMA = "taskplane.stage-startup/v1"
STAGE_RECEIPT_SCHEMA = "taskplane.stage-operation-receipt/v1"
STAGE_AUTHORITY_REFERENCE_SCHEMA = "taskplane.stage-authority-reference/v1"
STAGE_HANDOFF_DISPATCH_SCHEMA = "taskplane.stage-handoff-dispatch/v1"
STAGE_HANDOFF_V2_DISPATCH_SCHEMA = "taskplane.stage-handoff-dispatch/v2"
MAX_STAGE_STARTUP_BYTES = 128 * 1024
MAX_STAGE_RECEIPT_BYTES = 2 * 1024 * 1024
_STAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_STAGE_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_STAGE_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "request_fingerprint",
        "operation",
        "stage_ids",
        "committed_revision",
        "result",
        "result_fingerprint",
    }
)
_STAGE_HANDOFF_FIELDS = frozenset(
    {
        "schema",
        "producer",
        "requirement",
        "design",
        "target",
        "commit",
        "contracts",
        "deliverables",
        "evidence_references",
        "selected_artifacts",
        "exclusions",
        "authorization",
        "fingerprint",
    }
)
_STAGE_DISPATCH_RECEIPTS = frozenset(
    {
        "start_stage",
        "terminalize_and_start",
        "split_stage",
        "resume_stage",
    }
)
_STAGE_RUNTIME_FORBIDDEN_KEYS = frozenset(
    {
        "activecontract",
        "agent",
        "agents",
        "approval",
        "approvals",
        "argv",
        "command",
        "commands",
        "conversation",
        "conversations",
        "credential",
        "credentials",
        "cwd",
        "environment",
        "env",
        "event",
        "events",
        "eventlog",
        "eventlogs",
        "hostpath",
        "lease",
        "leases",
        "log",
        "logs",
        "meter",
        "meters",
        "path",
        "process",
        "prompt",
        "prompts",
        "relativepath",
        "absolutepath",
        "root",
        "runtime",
        "runtimeenvironment",
        "runtimestate",
        "secret",
        "secrets",
        "tool",
        "tools",
        "tooltranscript",
        "tooltranscripts",
        "trace",
        "traces",
        "transcript",
        "transcripts",
        "workspace",
    }
)


class StageDispatchError(ValueError):
    """A stage receipt or bounded startup value is unsafe to dispatch."""


def _stage_modules():
    return _dispatch_stage_values, sys.modules[__name__]


def _json_detach(value, label: str):
    try:
        return json.loads(canonical_json_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StageDispatchError(f"{label} must be canonical JSON") from exc


def _stage_identifier(value, label: str) -> str:
    if not isinstance(value, str) or value.strip() != value or not _STAGE_ID_RE.fullmatch(value):
        raise StageDispatchError(f"{label} is invalid")
    return value


def _stage_fingerprint(value, label: str) -> str:
    if not isinstance(value, str) or not _STAGE_FINGERPRINT_RE.fullmatch(value):
        raise StageDispatchError(f"{label} is invalid")
    return value


def _reject_runtime_context(value, label: str) -> None:
    """Reject predecessor/host runtime channels at the serialization seam."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise StageDispatchError(f"{label} has a non-string field")
            normalized = re.sub(r"[-_. ]", "", key).lower()
            if normalized in _STAGE_RUNTIME_FORBIDDEN_KEYS:
                raise StageDispatchError(f"{label} contains forbidden runtime field {key!r}")
            _reject_runtime_context(child, label)
    elif isinstance(value, list):
        for child in value:
            _reject_runtime_context(child, label)
    elif isinstance(value, str) and re.match(r"^(?:/|[A-Za-z]:[\\/]|\\\\)", value):
        raise StageDispatchError(f"{label} contains an absolute filesystem path")


def verify_stage_receipt(
    receipt: dict, *, expected_operation: str | None = None, expected_stage_id: str | None = None
) -> dict:
    """Verify and detach one persisted v4 operation receipt.

    The RunStore is authoritative for durability.  This boundary rechecks its
    closed schema and content fingerprint immediately before a lifecycle
    result is allowed to become executable startup context.
    """
    if not isinstance(receipt, dict):
        raise StageDispatchError("stage receipt must be an object")
    required = _STAGE_RECEIPT_FIELDS - {"result", "result_fingerprint"}
    optional = {"result", "result_fingerprint"}
    if not required.issubset(receipt) or set(receipt) - (required | optional):
        raise StageDispatchError("stage receipt fields are invalid")
    if ("result" in receipt) != ("result_fingerprint" in receipt):
        raise StageDispatchError("stage receipt result fields are incomplete")
    if receipt.get("schema") != STAGE_RECEIPT_SCHEMA:
        raise StageDispatchError("stage receipt schema is invalid")
    _stage_identifier(receipt.get("operation_id"), "stage receipt operation id")
    _stage_fingerprint(receipt.get("request_fingerprint"), "stage receipt request fingerprint")
    operation = _stage_identifier(receipt.get("operation"), "stage receipt operation")
    if expected_operation is not None and operation != expected_operation:
        raise StageDispatchError(
            f"stage receipt operation is {operation}, expected {expected_operation}"
        )
    stage_ids = receipt.get("stage_ids")
    if not isinstance(stage_ids, list) or any(
        not isinstance(stage_id, str) for stage_id in stage_ids
    ):
        raise StageDispatchError("stage receipt stage ids are invalid")
    checked_ids = [_stage_identifier(value, "stage receipt stage id") for value in stage_ids]
    if checked_ids != sorted(set(checked_ids)):
        raise StageDispatchError("stage receipt stage ids must be sorted and unique")
    if not checked_ids and operation != "rebuild_active_stage_projection":
        raise StageDispatchError("stage receipt stage ids are empty")
    if expected_stage_id is not None:
        expected = _stage_identifier(expected_stage_id, "expected stage id")
        if expected not in checked_ids:
            raise StageDispatchError("stage receipt does not bind the expected stage")
    revision = receipt.get("committed_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise StageDispatchError("stage receipt committed revision is invalid")
    if "result" in receipt:
        try:
            result_bytes = canonical_json_bytes(receipt["result"])
        except (TypeError, ValueError, UnicodeError) as exc:
            raise StageDispatchError("stage receipt result must be canonical JSON") from exc
        if len(result_bytes) > MAX_STAGE_RECEIPT_BYTES:
            raise StageDispatchError("stage receipt result exceeds its bound")
        expected_result = hashlib.sha256(result_bytes).hexdigest()
        if receipt.get("result_fingerprint") != expected_result:
            raise StageDispatchError("stage receipt result fingerprint mismatch")
    checked = _json_detach(receipt, "stage receipt")
    if len(canonical_json_bytes(checked)) > MAX_STAGE_RECEIPT_BYTES:
        raise StageDispatchError("stage receipt exceeds its bound")
    return checked


def _expected_dispatch_head(stage: dict, stage_entities) -> dict:
    payload = canonical_json_bytes(stage) + b"\n"
    stage_id = str(stage["stage_id"])
    fingerprint = str(stage["fingerprint"])
    return {
        "object": {
            "schema": "taskplane.stage-object-ref/v1",
            "stage_id": stage_id,
            "fingerprint": fingerprint,
            "digest": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "locator": f"stages/objects/{stage_id}/{fingerprint}.json",
        },
        "summary": stage_entities.bounded_stage_summary(stage),
    }


def _verify_dispatch_result(stage: dict, receipt: dict, stage_entities) -> None:
    """Bind a dispatch to the operation's exact committed active head."""
    result = receipt.get("result")
    if not isinstance(result, dict):
        raise StageDispatchError("stage dispatch receipt has no bounded result")
    operation = receipt["operation"]
    if operation == "resume_stage":
        # Resume has no new head.  _dispatch_claim validates its exact stage,
        # fingerprint, execution root, attempt id, and attempt claim below.
        return
    stage_id = str(stage["stage_id"])
    if operation == "start_stage":
        head = result.get("head")
    elif operation == "terminalize_and_start":
        head = result.get("successor_head")
    else:
        child_heads = result.get("child_heads")
        head = child_heads.get(stage_id) if isinstance(child_heads, dict) else None
    if head != _expected_dispatch_head(stage, stage_entities):
        raise StageDispatchError("stage dispatch receipt committed head does not match stage")


def _verified_handoff_for_dispatch(stage: dict, handoff: dict, selected_artifacts: list) -> dict:
    entities, stage_handoff = _stage_modules()
    is_v2 = isinstance(handoff, dict) and handoff.get("schema") == entities.HANDOFF_V2_SCHEMA
    fields = _STAGE_HANDOFF_FIELDS | (entities._HANDOFF_V2_ADDITIONS if is_v2 else frozenset())
    if not isinstance(handoff, dict) or set(handoff) != fields:
        raise StageDispatchError("verified handoff fields are invalid")
    if handoff.get("schema") != "taskplane.stage-handoff/v1" and not is_v2:
        raise StageDispatchError("verified handoff schema is invalid")
    if is_v2:
        try:
            entities.validate_contract(handoff["phase_result"])
            selected = set()
            produced = []
            for group in ("produced_artifacts", "inherited_artifacts"):
                entities._schema_artifacts(handoff[group], group, references=True)
                for row in handoff[group]:
                    reference = row["reference"]
                    identity = (reference["kind"], reference["fingerprint"])
                    if identity in selected:
                        raise ValueError("v2 artifact has duplicate ownership")
                    selected.add(identity)
                    if group == "produced_artifacts":
                        produced.append(reference)
            if selected != {
                (ref["kind"], ref["fingerprint"]) for ref in selected_artifacts
            } or sorted(produced, key=lambda ref: (ref["kind"], ref["fingerprint"])) != sorted(
                handoff["phase_result"]["collected_output_references"],
                key=lambda ref: (ref["kind"], ref["fingerprint"]),
            ):
                raise ValueError("v2 package differs from its collected outputs")
            for receipt in handoff["knowledge_apply_receipts"]:
                entities.validate_contract(receipt)
            entities._contract_strings(handoff["unresolved_issues"], "unresolved issues")
        except ValueError as exc:
            raise StageDispatchError("verified v2 handoff is invalid") from exc
        result = handoff["phase_result"]
        producer_outcome = handoff["producer"]["outcome"]
        reuse = handoff["authorization"].get("nonconsumable_reuse")
        consumable = producer_outcome == "done" or (
            producer_outcome in {"closed", "discarded"}
            and reuse
            == {
                "schema": "taskplane.nonconsumable-reuse-authorization/v1",
                "producer_outcome": producer_outcome,
                "authority_fingerprint": stage["authority"]["authority_fingerprint"],
            }
        )
        if (
            result["status"] != "accepted"
            or not consumable
            or result["run_id"] != stage["run_id"]
            or result["authority_fingerprint"] != stage["authority"]["authority_fingerprint"]
        ):
            raise StageDispatchError("verified v2 result binding is invalid")
    try:
        expected = stage_handoff.manifest_fingerprint(handoff)
    except (TypeError, ValueError) as exc:
        raise StageDispatchError("verified handoff is not canonical JSON") from exc
    if handoff.get("fingerprint") != expected:
        raise StageDispatchError("verified handoff fingerprint mismatch")
    handoff_bytes = canonical_json_bytes(handoff)
    if len(handoff_bytes) > 64 * 1024:
        raise StageDispatchError("verified handoff exceeds its bound")
    producer = handoff.get("producer")
    if (
        not isinstance(producer, dict)
        or set(producer) != {"stage_id", "outcome"}
        or producer.get("outcome") not in {"done", "closed", "discarded"}
    ):
        raise StageDispatchError("verified handoff producer is invalid")
    _stage_identifier(producer.get("stage_id"), "handoff producer stage id")
    predecessors = stage.get("predecessor_stage_ids") or []
    if predecessors and producer.get("stage_id") not in predecessors:
        raise StageDispatchError("verified handoff producer is not a stage predecessor")
    if handoff.get("requirement") != stage.get("requirement") or handoff.get("design") != stage.get(
        "design"
    ):
        raise StageDispatchError("verified handoff revision does not match stage")
    exclusions = handoff.get("exclusions")
    if (
        not isinstance(exclusions, list)
        or exclusions != sorted(set(exclusions))
        or not stage_handoff.REQUIRED_EXCLUSIONS.issubset(exclusions)
    ):
        raise StageDispatchError("verified handoff exclusions are invalid")
    evidence = handoff.get("evidence_references")
    if not isinstance(evidence, list) or not evidence:
        raise StageDispatchError("verified handoff evidence references are incomplete")
    authorization = handoff.get("authorization")
    authority_record = (
        authorization.get("authority_record") if isinstance(authorization, dict) else None
    )
    revision = authority_record.get("revision") if isinstance(authority_record, dict) else None
    authority = stage.get("authority")
    if (
        not isinstance(authorization, dict)
        or not isinstance(authority_record, dict)
        or not isinstance(authority, dict)
        or authority_record.get("schema") != "taskplane.authority-record-reference/v1"
        or authority_record.get("authority_schema") != "taskplane.consolidated-authorization/v1"
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or not _STAGE_FINGERPRINT_RE.fullmatch(str(authority_record.get("fingerprint") or ""))
    ):
        raise StageDispatchError("verified handoff authority record is invalid")
    if (
        authorization.get("actor") != authority.get("actor")
        or authorization.get("session_id") != authority.get("session_id")
        or revision != authority.get("authority_revision")
        or authority_record.get("fingerprint") != authority.get("authority_fingerprint")
    ):
        raise StageDispatchError("verified handoff authorization does not match stage authority")
    input_reference = stage.get("input_manifest_ref")
    if (
        not isinstance(input_reference, dict)
        or input_reference.get("fingerprint") != expected
        or input_reference.get("bytes") != len(handoff_bytes)
    ):
        raise StageDispatchError("stage input does not bind the verified handoff")
    if not isinstance(selected_artifacts, list):
        raise StageDispatchError("selected artifacts must be a list")
    detached = _json_detach(selected_artifacts, "selected artifacts")
    if detached != stage.get("selected_artifacts") or detached != handoff.get("selected_artifacts"):
        raise StageDispatchError("selected artifacts do not match stage and handoff")
    _reject_runtime_context(handoff, "verified handoff")
    _reject_runtime_context(detached, "selected artifacts")
    return _json_detach(handoff, "verified handoff")


def _dispatch_claim(stage: dict, receipt: dict, attempt_id: str | None) -> tuple[dict, str | None]:
    run_id = str(stage["run_id"])
    stage_id = str(stage["stage_id"])
    execution_root_id = str(stage["execution_root_id"])
    operation = str(receipt["operation"])
    if operation == "resume_stage":
        result = receipt.get("result")
        if not isinstance(result, dict):
            raise StageDispatchError("resume receipt has no bounded result")
        claim = result.get("claim")
        recorded_attempt = result.get("attempt_id")
        if not isinstance(claim, dict):
            raise StageDispatchError("resume receipt has no attempt claim")
        attempt = _stage_identifier(recorded_attempt, "resume receipt attempt id")
        if attempt_id is not None and _stage_identifier(attempt_id, "stage attempt id") != attempt:
            raise StageDispatchError("resume receipt attempt id mismatch")
        if (
            result.get("stage_id") != stage_id
            or result.get("execution_root_id") != execution_root_id
            or result.get("stage_fingerprint") != stage.get("fingerprint")
        ):
            raise StageDispatchError("resume receipt does not match stage")
        expected_claim = {
            "schema": "taskplane.stage-execution-attempt-claim/v1",
            "run_id": run_id,
            "stage_id": stage_id,
            "execution_root_id": execution_root_id,
            "attempt_id": attempt,
        }
        if claim != expected_claim:
            raise StageDispatchError("resume receipt attempt claim is invalid")
        return expected_claim, attempt
    if attempt_id is not None:
        raise StageDispatchError("only a verified resume receipt may select an attempt")
    return {
        "schema": "taskplane.stage-execution-root-claim/v1",
        "run_id": run_id,
        "stage_id": stage_id,
        "execution_root_id": execution_root_id,
    }, None


def _declared_stage_scope(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"scope_paths", "out_of_scope_paths"}:
        raise StageDispatchError("declared scope needs scope_paths and out_of_scope_paths")
    checked: dict[str, list[str]] = {}
    for field in ("scope_paths", "out_of_scope_paths"):
        rows = value.get(field)
        if (
            not isinstance(rows, list)
            or len(rows) > 64
            or any(
                not isinstance(row, str)
                or not row.strip()
                or row.strip() != row
                or len(row.encode("utf-8")) > 512
                for row in rows
            )
        ):
            raise StageDispatchError(f"declared {field} is invalid")
        if rows != sorted(set(rows)):
            raise StageDispatchError(f"declared {field} must be sorted and unique")
        checked[field] = list(rows)
    _reject_runtime_context(checked, "declared scope")
    return checked


def _stage_authority_reference(authority: dict) -> dict:
    """Project attributable local authority to a pseudonymous reference.

    The caller has already validated the stage aggregate and matched its raw
    actor/session attribution to the verified handoff.  Hashing that complete
    binding preserves a deterministic, cross-host proof link without placing
    the identifying values in agent-facing startup bytes.
    """
    checked = _json_detach(authority, "stage authority")
    return {
        "schema": STAGE_AUTHORITY_REFERENCE_SCHEMA,
        "fingerprint": hashlib.sha256(canonical_json_bytes(checked)).hexdigest(),
    }


def _verify_stage_authority_reference(value) -> dict:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "fingerprint"}
        or value.get("schema") != STAGE_AUTHORITY_REFERENCE_SCHEMA
    ):
        raise StageDispatchError("stage authority reference is invalid")
    _stage_fingerprint(value.get("fingerprint"), "stage authority reference fingerprint")
    # The complete startup serialization below is the closed JSON boundary.
    # Avoid serializing this already closed two-field projection a second
    # time during read-side verification.
    return dict(value)


def _dispatch_handoff_projection(handoff: dict, authority_reference: dict) -> dict:
    """Make a content-addressed handoff projection safe for a stage worker."""
    projected = _json_detach(handoff, "verified handoff")
    source_fingerprint = projected.pop("fingerprint")
    projected["schema"] = (
        STAGE_HANDOFF_V2_DISPATCH_SCHEMA
        if handoff["schema"] == "taskplane.stage-handoff/v2"
        else STAGE_HANDOFF_DISPATCH_SCHEMA
    )
    projected["source_fingerprint"] = source_fingerprint
    projected["authorization"] = _json_detach(authority_reference, "stage authority reference")
    projected["fingerprint"] = hashlib.sha256(canonical_json_bytes(projected)).hexdigest()
    return projected


def _verify_dispatch_handoff_projection(value, authority_reference: dict) -> dict:
    fields = _STAGE_HANDOFF_FIELDS | {"source_fingerprint"}
    is_v2 = isinstance(value, dict) and value.get("schema") == STAGE_HANDOFF_V2_DISPATCH_SCHEMA
    if is_v2:
        entities, _ = _stage_modules()
        fields |= entities._HANDOFF_V2_ADDITIONS
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema")
        not in {STAGE_HANDOFF_DISPATCH_SCHEMA, STAGE_HANDOFF_V2_DISPATCH_SCHEMA}
    ):
        raise StageDispatchError("stage dispatch handoff projection is invalid")
    _stage_fingerprint(value.get("source_fingerprint"), "source handoff fingerprint")
    supplied = _stage_fingerprint(value.get("fingerprint"), "stage dispatch handoff fingerprint")
    payload = dict(value)
    payload.pop("fingerprint")
    expected = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    if supplied != expected:
        raise StageDispatchError("stage dispatch handoff projection fingerprint mismatch")
    if value.get("authorization") != authority_reference:
        raise StageDispatchError("stage dispatch handoff authority reference mismatch")
    # ``payload`` was just canonicalized to verify its fingerprint and the
    # complete startup is canonicalized once more below.  A third detach
    # serialization adds startup cost without strengthening the boundary.
    return dict(value)


def stage_runtime_dispatch(
    stage: dict,
    receipt: dict,
    handoff: dict,
    selected_artifacts: list,
    *,
    attempt_id: str | None = None,
    declared_scope: dict | None = None,
) -> dict:
    """Build the sole bounded context admitted to a native stage worker.

    No workspace path or predecessor execution state is accepted as input.
    The exact startup bytes are obtained with :func:`stage_startup_bytes`.
    """
    stage_entities, _ = _stage_modules()
    try:
        checked_stage = stage_entities.validate_stage(stage)
    except (TypeError, ValueError) as exc:
        raise StageDispatchError(f"stage is invalid: {exc}") from exc
    if checked_stage.get("state") != "active":
        raise StageDispatchError("only an active stage can be dispatched")
    checked_receipt = verify_stage_receipt(
        receipt, expected_stage_id=str(checked_stage["stage_id"])
    )
    if checked_receipt["operation"] not in _STAGE_DISPATCH_RECEIPTS:
        raise StageDispatchError("receipt operation does not create or resume stage execution")
    _verify_dispatch_result(checked_stage, checked_receipt, stage_entities)
    checked_handoff = _verified_handoff_for_dispatch(checked_stage, handoff, selected_artifacts)
    claim, attempt = _dispatch_claim(checked_stage, checked_receipt, attempt_id)
    scope = _declared_stage_scope(declared_scope)
    authority_reference = _stage_authority_reference(checked_stage["authority"])
    dispatch_handoff = _dispatch_handoff_projection(checked_handoff, authority_reference)
    startup = {
        "schema": STAGE_STARTUP_SCHEMA,
        "stage_id": checked_stage["stage_id"],
        "authority": authority_reference,
        "input_manifest_bytes": checked_stage["input_manifest_ref"]["bytes"],
        "input_handoff": dispatch_handoff,
        "selected_artifacts": _json_detach(selected_artifacts, "selected artifacts"),
        "budget": checked_stage["budget"],
        "execution_claim": claim,
        "attempt_id": attempt,
    }
    if scope is not None:
        startup["declared_scope"] = scope
    _reject_runtime_context(startup, "stage startup")
    startup = _json_detach(startup, "stage startup")
    serialized = canonical_json_bytes(startup)
    if len(serialized) > MAX_STAGE_STARTUP_BYTES:
        raise StageDispatchError(f"stage startup exceeds {MAX_STAGE_STARTUP_BYTES} bytes")
    selected_bytes = sum(
        int(reference.get("bytes") or 0) for reference in startup["selected_artifacts"]
    )
    telemetry = {
        # Preserve the size of the verified repository-resident input
        # manifest.  The agent-facing handoff is a privacy projection and is
        # intentionally a different byte sequence.
        "manifest_bytes": checked_stage["input_manifest_ref"]["bytes"],
        "startup_bytes": len(serialized),
        # This is a deterministic budgeting estimate, not provider usage.
        "startup_tokens": (len(serialized) + 3) // 4,
        "selected_ref_count": len(startup["selected_artifacts"]),
        "selected_ref_bytes": selected_bytes,
        "predecessor_root_opens": 0,
    }
    return {
        "schema": STAGE_DISPATCH_SCHEMA,
        "startup": startup,
        "startup_sha256": hashlib.sha256(serialized).hexdigest(),
        "telemetry": telemetry,
    }


def stage_dispatch_payload(
    stage: dict,
    verified_handoff: dict,
    selected_artifacts: list,
    claim: dict,
    *,
    attempt_id: str | None = None,
    declared_scope: dict | None = None,
) -> dict:
    """Preflight bounded startup against one proposed path-free claim.

    This compatibility seam exists only so the loop can prove serialization
    before it commits a lifecycle mutation.  The post-commit dispatch path
    uses :func:`stage_runtime_dispatch` with the durable RunStore receipt.
    """
    if not isinstance(claim, dict):
        raise StageDispatchError("stage execution claim must be an object")
    operation = "resume_stage" if attempt_id is not None else "start_stage"
    stage_entities, _ = _stage_modules()
    checked_stage = stage_entities.validate_stage(stage)
    result = {"head": _expected_dispatch_head(checked_stage, stage_entities)}
    if operation == "resume_stage":
        result = {
            "stage_id": stage.get("stage_id"),
            "attempt_id": attempt_id,
            "execution_root_id": stage.get("execution_root_id"),
            "claim": claim,
            "stage_fingerprint": stage.get("fingerprint"),
        }
    receipt = {
        "schema": STAGE_RECEIPT_SCHEMA,
        "operation_id": "bounded-startup-preflight",
        "request_fingerprint": hashlib.sha256(
            canonical_json_bytes(
                {
                    "stage": stage.get("fingerprint"),
                    "claim": claim,
                }
            )
        ).hexdigest(),
        "operation": operation,
        "stage_ids": [stage.get("stage_id")],
        "committed_revision": 1,
    }
    receipt["result"] = result
    receipt["result_fingerprint"] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
    dispatch = stage_runtime_dispatch(
        stage,
        receipt,
        verified_handoff,
        selected_artifacts,
        attempt_id=attempt_id,
        declared_scope=declared_scope,
    )
    if claim != dispatch["startup"]["execution_claim"]:
        raise StageDispatchError("stage execution claim is invalid")
    return dispatch


def stage_startup_bytes(dispatch: dict) -> bytes:
    """Return and re-verify the byte-identical bounded startup serialization."""
    if (
        not isinstance(dispatch, dict)
        or set(dispatch) != {"schema", "startup", "startup_sha256", "telemetry"}
        or dispatch.get("schema") != STAGE_DISPATCH_SCHEMA
    ):
        raise StageDispatchError("stage dispatch envelope is invalid")
    startup = dispatch.get("startup")
    if not isinstance(startup, dict) or startup.get("schema") != STAGE_STARTUP_SCHEMA:
        raise StageDispatchError("stage startup payload is invalid")
    required = {
        "schema",
        "stage_id",
        "authority",
        "input_manifest_bytes",
        "input_handoff",
        "selected_artifacts",
        "budget",
        "execution_claim",
        "attempt_id",
    }
    fields = frozenset(startup)
    if not required <= fields or fields - required - {"declared_scope", "phase_input"}:
        raise StageDispatchError("stage startup fields are invalid")
    if "phase_input" in startup:
        _stage_modules()[0]._portable_reference(startup["phase_input"], "phase input")
    authority_reference = _verify_stage_authority_reference(startup.get("authority"))
    projected_handoff = _verify_dispatch_handoff_projection(
        startup.get("input_handoff"), authority_reference
    )
    _reject_runtime_context(startup, "stage startup")
    serialized = canonical_json_bytes(startup)
    if len(serialized) > MAX_STAGE_STARTUP_BYTES:
        raise StageDispatchError("stage startup exceeds its bound")
    if dispatch.get("startup_sha256") != hashlib.sha256(serialized).hexdigest():
        raise StageDispatchError("stage startup fingerprint mismatch")
    selected = startup.get("selected_artifacts")
    if not isinstance(selected, list):
        raise StageDispatchError("stage startup selected artifacts are invalid")
    if projected_handoff.get("selected_artifacts") != selected:
        raise StageDispatchError("stage startup handoff selected artifacts mismatch")
    expected_telemetry = {
        "manifest_bytes": startup.get("input_manifest_bytes"),
        "startup_bytes": len(serialized),
        "startup_tokens": (len(serialized) + 3) // 4,
        "selected_ref_count": len(selected) + int("phase_input" in startup),
        "selected_ref_bytes": sum(
            int(row.get("bytes") or 0)
            for row in selected + ([startup["phase_input"]] if "phase_input" in startup else [])
            if isinstance(row, dict)
        ),
        "predecessor_root_opens": 0,
    }
    input_manifest_bytes = startup.get("input_manifest_bytes")
    if (
        isinstance(input_manifest_bytes, bool)
        or not isinstance(input_manifest_bytes, int)
        or input_manifest_bytes < 0
    ):
        raise StageDispatchError("stage startup telemetry mismatch")
    if dispatch.get("telemetry") != expected_telemetry:
        raise StageDispatchError("stage startup telemetry mismatch")
    return serialized


def attach_phase_input(dispatch: dict, reference: dict) -> dict:
    """Bind a verified phase input reference into the bounded startup bytes."""
    stage_startup_bytes(dispatch)
    result = _json_detach(dispatch, "stage dispatch")
    result["startup"]["phase_input"] = _stage_modules()[0]._portable_reference(
        reference, "phase input"
    )
    raw = canonical_json_bytes(result["startup"])
    result["startup_sha256"] = hashlib.sha256(raw).hexdigest()
    references = result["startup"]["selected_artifacts"] + [result["startup"]["phase_input"]]
    result["telemetry"].update(
        startup_bytes=len(raw),
        startup_tokens=(len(raw) + 3) // 4,
        selected_ref_count=len(references),
        selected_ref_bytes=sum(row["bytes"] for row in references),
    )
    stage_startup_bytes(result)
    return result
