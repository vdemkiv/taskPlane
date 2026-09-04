"""Bounded, content-addressed handoff manifests for isolated stages.

The manifest is a closed control-plane value.  It never carries artifact
bodies or host paths and every referenced artifact is verified before a
successor can use the manifest.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
import re
from typing import Final, TypeAlias

if __package__:
    from . import review_evidence
    from . import storage as runtime_storage
else:
    import review_evidence
    import storage as runtime_storage


SCHEMA: Final[str] = "taskplane.stage-handoff/v1"
SCHEMA_V1: Final[str] = SCHEMA
SCHEMA_V2: Final[str] = "taskplane.stage-handoff/v2"
MAX_MANIFEST_BYTES: Final[int] = 64 * 1024
MAX_ARTIFACT_REFERENCES: Final[int] = 64
TERMINAL_OUTCOMES: Final[frozenset[str]] = frozenset({
    "done", "closed", "discarded",
})
REQUIRED_EXCLUSIONS: Final[frozenset[str]] = frozenset({
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
})

JsonObject: TypeAlias = dict[str, object]
_IDENTIFIER: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REPOSITORY_ID: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_FINGERPRINT: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_COMMIT: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_CONTRACT: Final[re.Pattern[str]] = re.compile(
    r"^contract:[a-z][a-z0-9-]{0,127}$")
_MANIFEST_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "producer", "requirement", "design", "target", "commit",
    "contracts", "deliverables", "evidence_references",
    "selected_artifacts", "exclusions", "authorization", "fingerprint",
})
_AUTHORITY_FIELDS: Final[frozenset[str]] = frozenset({
    "actor", "session_id", "authorized_at", "operation_id",
    "authority_record", "nonconsumable_reuse",
})
_AUTHORITY_RECORD_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "authority_schema", "revision", "fingerprint",
})
_NONCONSUMABLE_REUSE_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "producer_outcome", "authority_fingerprint",
})


class HandoffValidationError(ValueError):
    """The handoff does not satisfy its closed boundary contract."""


class HandoffIntegrityError(HandoffValidationError):
    """The canonical manifest identity does not match its content."""


class StaleAuthorityError(HandoffValidationError):
    """The handoff was authorized against an obsolete authority revision."""


def _closed(value: object, fields: frozenset[str],
            label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise HandoffValidationError(f"{label} must be an object")
    keys = {str(key) for key in value}
    unknown = keys - fields
    missing = fields - keys
    if unknown:
        raise HandoffValidationError(
            f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise HandoffValidationError(
            f"{label} has missing fields: {', '.join(sorted(missing))}")
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


def _strings(values: object, label: str, *,
             pattern: re.Pattern[str] | None = None,
             allow_empty: bool = False) -> list[str]:
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
        store: review_evidence.ArtifactStore,
        references: Iterable[dict[str, object]],
        label: str) -> list[dict[str, object]]:
    if isinstance(references, (str, bytes, Mapping)):
        raise HandoffValidationError(f"{label} must be a list")
    result = [review_evidence.portable_artifact_reference(store, reference)
              for reference in references]
    identities = [(row["kind"], row["fingerprint"]) for row in result]
    if len(set(identities)) != len(identities):
        raise HandoffValidationError(f"{label} contains duplicate references")
    return sorted(result, key=lambda row: (str(row["kind"]),
                                           str(row["fingerprint"])))


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
                    f"handoff contains at most {MAX_ARTIFACT_REFERENCES} "
                    "artifact references")
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
        store: review_evidence.ArtifactStore, *, producer_stage_id: str,
        producer_outcome: str, requirement: Mapping[str, object],
        design: Mapping[str, object] | None,
        target: Mapping[str, object] | None,
        commit: Mapping[str, object] | None,
        contracts: Mapping[str, object], deliverables: Iterable[str],
        evidence_references: Iterable[dict[str, object]],
        selected_artifacts: Iterable[dict[str, object]],
        exclusions: Iterable[str],
        authorization: Mapping[str, object],
        allow_nonconsumable_reuse: bool = False) -> JsonObject:
    """Create and fully verify one deterministic stage handoff manifest.

    Closed or discarded results require the explicit reuse flag.  That
    decision is persisted inside the attributable authorization record so a
    later store/read cycle cannot lose it or infer it from the outcome.
    """
    evidence_rows, artifact_rows = _bounded_reference_inputs(
        evidence_references, selected_artifacts)
    contract_rows = _closed(
        contracts, frozenset({"provided", "consumed", "changed"}),
        "contracts")
    canonical_contracts = {
        relation: _strings(contract_rows.get(relation), f"contracts {relation}",
                           pattern=_CONTRACT, allow_empty=True)
        for relation in ("provided", "consumed", "changed")
    }
    canonical_deliverables = _strings(list(deliverables), "deliverables")
    canonical_exclusions = _strings(list(exclusions), "exclusions")
    portable_evidence = _portable_references(
        store, evidence_rows, "evidence references")
    portable_artifacts = _portable_references(
        store, artifact_rows, "selected artifacts")
    outcome = str(producer_outcome)
    if allow_nonconsumable_reuse and outcome not in {"closed", "discarded"}:
        raise HandoffValidationError(
            "nonconsumable reuse applies only to closed or discarded stages")
    canonical_authorization = dict(authorization)
    authority_record = canonical_authorization.get("authority_record")
    authority_fingerprint = (
        authority_record.get("fingerprint")
        if isinstance(authority_record, Mapping) else None
    )
    canonical_authorization["nonconsumable_reuse"] = (
        {
            "schema": "taskplane.nonconsumable-reuse-authorization/v1",
            "producer_outcome": outcome,
            "authority_fingerprint": authority_fingerprint,
        }
        if allow_nonconsumable_reuse else None
    )
    body: JsonObject = {
        "schema": SCHEMA,
        "producer": {"stage_id": str(producer_stage_id),
                     "outcome": outcome},
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
    return validate_manifest(
        store, body, allow_nonconsumable_reuse=allow_nonconsumable_reuse)


def manifest_fingerprint(manifest: Mapping[str, object]) -> str:
    """Return the semantic fingerprint, excluding its self-reference."""
    material = {str(key): value for key, value in manifest.items()
                if str(key) != "fingerprint"}
    return review_evidence.content_fingerprint(material)


def _validate_portable_references(
        store: review_evidence.ArtifactStore, value: object,
        label: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise HandoffValidationError(f"{label} must be a list")
    identities: list[tuple[str, str]] = []
    for reference in value:
        try:
            review_evidence.verify_portable_artifact_reference(store, reference)
        except review_evidence.ArtifactIntegrityError as exc:
            if "unknown fields" in str(exc):
                raise HandoffValidationError(
                    "artifact reference has unknown fields: " +
                    str(exc).split(": ", 1)[-1]) \
                    from exc
            raise
        identities.append((str(reference["kind"]),
                           str(reference["fingerprint"])))
    if len(set(identities)) != len(identities):
        raise HandoffValidationError(f"{label} contains duplicate references")
    canonical = sorted(value, key=lambda row: (str(row["kind"]),
                                               str(row["fingerprint"])))
    if value != canonical:
        raise HandoffValidationError(f"{label} is not in canonical order")
    return value


def validate_manifest(
        store: review_evidence.ArtifactStore, manifest: Mapping[str, object], *,
        expected_authority_revision: int | None = None,
        expected_authority_fingerprint: str | None = None,
        allow_nonconsumable_reuse: bool = False) -> JsonObject:
    """Validate schema, authority, artifact integrity, and numeric bounds."""
    row = _closed(manifest, _MANIFEST_FIELDS, "handoff manifest")
    if row.get("schema") != SCHEMA:
        raise HandoffValidationError("unsupported handoff manifest schema")
    if manifest_fingerprint(row) != row.get("fingerprint"):
        raise HandoffIntegrityError("handoff manifest fingerprint mismatch")

    producer = _closed(row.get("producer"),
                       frozenset({"stage_id", "outcome"}), "producer")
    _identifier(producer.get("stage_id"), "producer stage id")
    outcome = str(producer.get("outcome") or "")
    if outcome not in TERMINAL_OUTCOMES:
        raise HandoffValidationError("producer outcome is not terminal")

    requirement = _closed(
        row.get("requirement"), frozenset({"id", "revision", "fingerprint"}),
        "requirement")
    _identifier(requirement.get("id"), "requirement id")
    _revision(requirement.get("revision"), "requirement revision")
    _fingerprint(requirement.get("fingerprint"), "requirement fingerprint")

    design = row.get("design")
    if design is not None:
        design_row = _closed(design, frozenset({"revision", "fingerprint"}),
                             "design")
        _revision(design_row.get("revision"), "design revision")
        _fingerprint(design_row.get("fingerprint"), "design fingerprint")

    target, commit = row.get("target"), row.get("commit")
    if (target is None) != (commit is None):
        raise HandoffValidationError("target and commit must appear together")
    if target is not None and commit is not None:
        target_row = _closed(
            target, frozenset({"repository_id", "fingerprint"}), "target")
        repository_id = str(target_row.get("repository_id") or "").strip()
        if not _REPOSITORY_ID.fullmatch(repository_id):
            raise HandoffValidationError("target repository id is invalid")
        target_fingerprint = _fingerprint(
            target_row.get("fingerprint"), "target fingerprint")
        commit_row = _closed(
            commit, frozenset({"sha", "target_fingerprint"}), "commit")
        if not _COMMIT.fullmatch(str(commit_row.get("sha") or "")):
            raise HandoffValidationError("commit sha is invalid")
        if commit_row.get("target_fingerprint") != target_fingerprint:
            raise HandoffValidationError("commit target fingerprint mismatch")

    contract_rows = _closed(
        row.get("contracts"), frozenset({"provided", "consumed", "changed"}),
        "contracts")
    for relation in ("provided", "consumed", "changed"):
        values = _strings(contract_rows.get(relation), f"contracts {relation}",
                          pattern=_CONTRACT, allow_empty=True)
        if values != contract_rows.get(relation):
            raise HandoffValidationError(
                f"contracts {relation} is not in canonical order")
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
            "handoff is missing required exclusions: " +
            ", ".join(sorted(missing_exclusions)))

    evidence = _validate_portable_references(
        store, row.get("evidence_references"), "evidence references")
    if not evidence:
        raise HandoffValidationError("handoff evidence references are incomplete")
    artifacts = _validate_portable_references(
        store, row.get("selected_artifacts"), "selected artifacts")
    if len(evidence) + len(artifacts) > MAX_ARTIFACT_REFERENCES:
        raise HandoffValidationError(
            f"handoff contains at most {MAX_ARTIFACT_REFERENCES} artifact references")

    authority = _closed(row.get("authorization"), _AUTHORITY_FIELDS,
                        "authorization")
    _identifier(authority.get("actor"), "authorization actor")
    _identifier(authority.get("session_id"), "authorization session id")
    _identifier(authority.get("operation_id"), "authorization operation id")
    _validate_timestamp(authority.get("authorized_at"))
    authority_record = _closed(
        authority.get("authority_record"), _AUTHORITY_RECORD_FIELDS,
        "authority record")
    if authority_record.get("schema") != \
            "taskplane.authority-record-reference/v1" or \
            authority_record.get("authority_schema") != \
            "taskplane.consolidated-authorization/v1":
        raise HandoffValidationError("authority record schema is invalid")
    revision = authority_record.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise HandoffValidationError("authority revision is invalid")
    authority_fingerprint = _fingerprint(
        authority_record.get("fingerprint"), "authority fingerprint")
    if expected_authority_revision is not None and \
            revision != expected_authority_revision:
        raise StaleAuthorityError("handoff authority revision is stale")
    if expected_authority_fingerprint is not None and \
            authority_fingerprint != expected_authority_fingerprint:
        raise StaleAuthorityError("handoff authority fingerprint is stale")

    reuse_authorization = authority.get("nonconsumable_reuse")
    if reuse_authorization is not None:
        reuse_row = _closed(
            reuse_authorization, _NONCONSUMABLE_REUSE_FIELDS,
            "nonconsumable reuse authorization")
        if reuse_row.get("schema") != \
                "taskplane.nonconsumable-reuse-authorization/v1" or \
                reuse_row.get("producer_outcome") != outcome or \
                reuse_row.get("authority_fingerprint") != \
                authority_fingerprint or \
                outcome not in {"closed", "discarded"}:
            raise HandoffValidationError(
                "nonconsumable reuse authorization is invalid")
    if outcome in {"closed", "discarded"}:
        if reuse_authorization is None:
            raise HandoffValidationError(
                f"{outcome} stage results lack explicit reuse authorization")
        if not allow_nonconsumable_reuse:
            raise HandoffValidationError(
                f"{outcome} stage results cannot be consumed by default")

    size = len(review_evidence.canonical_bytes(row))
    if size > MAX_MANIFEST_BYTES:
        raise HandoffValidationError(
            f"canonical handoff exceeds {MAX_MANIFEST_BYTES} bytes")
    return dict(row)


def store_manifest(store: review_evidence.ArtifactStore,
                   manifest: Mapping[str, object]) -> dict[str, object]:
    """Persist a structurally valid manifest without consuming its results."""
    validated = validate_manifest(
        store, manifest, allow_nonconsumable_reuse=True)
    return store.put("stage-handoff", validated,
                     fingerprint=str(validated["fingerprint"]))


def read_manifest(store: review_evidence.ArtifactStore,
                  reference: Mapping[str, object], *,
                  expected_authority_revision: int,
                  expected_authority_fingerprint: str,
                  allow_nonconsumable_reuse: bool = False) -> JsonObject:
    """Consume a handoff bound to a separately trusted authority identity."""
    value = store.read(dict(reference))
    if not isinstance(value, Mapping):
        raise HandoffValidationError("stored handoff manifest must be an object")
    return validate_manifest(
        store, value,
        expected_authority_revision=expected_authority_revision,
        expected_authority_fingerprint=expected_authority_fingerprint,
        allow_nonconsumable_reuse=allow_nonconsumable_reuse)


def _phase_handoff_module():
    """Import the repository-native v2 owner without changing v1 imports."""
    if __package__:
        from . import phase_handoff
    else:
        import phase_handoff
    return phase_handoff


def create_repository_manifest(**values: object) -> JsonObject:
    """Create a closed repository-native v2 handoff.

    The historical ``create_manifest`` remains the private ArtifactStore-backed
    v1 API.  Keeping distinct entry points prevents either schema from being
    silently reinterpreted as the other.
    """
    return _phase_handoff_module().create_manifest(**values)


def validate_repository_manifest(manifest: object) -> JsonObject:
    """Validate one v2 value without consulting private Taskplane state."""
    return _phase_handoff_module().validate_manifest(manifest)


def repository_manifest_fingerprint(manifest: Mapping[str, object]) -> str:
    return _phase_handoff_module().manifest_fingerprint(manifest)


def publish_repository_manifest(workspace: str, manifest: object) -> JsonObject:
    return _phase_handoff_module().publish_manifest(workspace, manifest)


def load_repository_manifest(workspace: str, relative_path: str, *,
                             require_clean: bool = True) -> JsonObject:
    return _phase_handoff_module().load_manifest(
        workspace, relative_path, require_clean=require_clean)
