"""Bounded, content-addressed handoff manifests for isolated stages.

The manifest is a closed control-plane value.  It never carries artifact
bodies or host paths and every referenced artifact is verified before a
successor can use the manifest.
"""

from __future__ import annotations

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
_CONTRACT: Final[re.Pattern[str]] = re.compile(r"^contract:[a-z][a-z0-9-]{0,127}$")
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
    body["fingerprint"] = manifest_fingerprint(body)
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
        from . import stage_entities as entities
    else:
        import stage_entities as flat_entities

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
        from . import stage_entities as entities
    else:
        import stage_entities as flat_entities

        entities = flat_entities
    row = entities.create_contract(value)
    if row["schema"] != entities.KNOWLEDGE_UPDATE_SCHEMA:
        raise HandoffValidationError("unsupported knowledge proposal")
    if row["scope"] != expected_scope or len(expected_scope) != 2:
        raise HandoffValidationError("knowledge scope mismatch")
    if (not expected_scope[0].startswith("repository:")
            or expected_scope[1] != "phase:" + str(row["phase_id"])):
        raise HandoffValidationError("knowledge repository/phase scope is invalid")
    _identifier(expected_scope[0][len("repository:"):], "knowledge repository")
    reference = row["finding_or_observation_reference"]
    if not isinstance(reference, str) or not re.fullmatch(
            r"artifact://(?:finding|observation)/[0-9a-f]{64}", reference):
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
    receipt: Mapping[str, object], *, proposal: Mapping[str, object],
    trusted_keys: Mapping[str, SigningKey], expected_freshness: Mapping[str, object],
    now: int,
) -> JsonObject:
    """Consume the owner's actual authenticated receipt bound to one proposal."""
    if __package__:
        from . import stage_entities as entities
    else:
        import stage_entities as flat_entities

        entities = flat_entities
    source = entities.validate_contract(proposal)
    if source["schema"] != entities.KNOWLEDGE_UPDATE_SCHEMA:
        raise HandoffValidationError("unsupported knowledge proposal")
    verified = verify_contract(receipt, trusted_keys=trusted_keys,
        expected_schema=entities.KNOWLEDGE_APPLY_SCHEMA,
        expected_freshness=expected_freshness, now=now)
    result = verified["payload"]
    if not isinstance(result, dict):
        raise HandoffValidationError("knowledge receipt is invalid")
    for receipt_field, proposal_field in (
        ("proposal_fingerprint", "proposal_fingerprint"),
        ("operation_id", "operation_id"), ("base_fingerprint", "base_knowledge_fingerprint"),
    ):
        if result[receipt_field] != source[proposal_field]:
            raise HandoffValidationError("knowledge receipt proposal binding mismatch")
    if result["outcome"] == "applied" and result["base_fingerprint"] != result["current_fingerprint"]:
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
        from . import stage_entities as entities
    else:
        import stage_entities as flat_entities

        entities = flat_entities
    payload = entities.validate_contract(value, store=store)
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
        from . import stage_entities as entities
    else:
        import stage_entities as flat_entities

        entities = flat_entities
    payload = entities.validate_contract(row["payload"], store=store)
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
