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
    from . import stage_handoff
    from . import storage as runtime_storage
else:
    import review_evidence
    import stage_handoff
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
TERMINAL_OUTCOMES: Final[frozenset[str]] = frozenset({
    "done", "closed", "discarded",
})
STAGE_STATES: Final[frozenset[str]] = frozenset({"active", "terminal"})

JsonObject: TypeAlias = dict[str, object]
_IDENTIFIER: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_KIND: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_FINGERPRINT: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_CONTRACT: Final[re.Pattern[str]] = re.compile(
    r"^contract:[a-z][a-z0-9-]{0,127}$")
_PORTABLE_REFERENCE_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "kind", "fingerprint", "digest", "bytes", "locator",
    "transport",
})
_REQUIREMENT_FIELDS: Final[frozenset[str]] = frozenset({
    "id", "revision", "fingerprint",
})
_DESIGN_FIELDS: Final[frozenset[str]] = frozenset({
    "revision", "fingerprint",
})
_AUTHORITY_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "run_id", "repository_id", "repository_key", "worktree_id",
    "target_revision", "worktree_revision", "requirement_id",
    "requirement_revision", "design_revision", "design_fingerprint",
    "actor", "session_id", "authority_revision", "authority_fingerprint",
})
_TERMINAL_FIELDS: Final[frozenset[str]] = frozenset({
    "actor", "terminalized_at", "reason_code", "reason",
    "completed_deliverables", "completion_evidence",
})
_STAGE_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "run_id", "stage_id", "requirement", "design", "stage_kind",
    "parent_stage_ids", "predecessor_stage_ids", "input_manifest_ref",
    "execution_root_id", "deliverables", "selected_artifacts", "budget",
    "dependencies", "contracts", "authority", "state", "outcome",
    "default_consumable", "terminal", "created_at", "aggregate_revision",
    "fingerprint",
})
_SPLIT_SPEC_FIELDS: Final[frozenset[str]] = frozenset({
    "stage_kind", "selected_artifacts", "dependencies", "budget",
    "deliverables", "contracts", "input_manifest_ref",
})
_LINEAGE_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "parent_stage_id", "child_stage_id", "predecessor_stage_ids",
    "handoff_fingerprint", "split_operation_id", "fingerprint",
})


class StageValidationError(ValueError):
    """A stage value violates the closed canonical schema."""


class StageIntegrityError(StageValidationError):
    """A stage or derived projection does not match its fingerprint."""


class StageLifecycleError(StageValidationError):
    """A requested lifecycle transition is not permitted."""


class SplitValidationError(StageLifecycleError):
    """A split cannot produce a complete, isolated child set."""


STATELESS_PHASE_PROJECTION_SCHEMA: Final[str] = \
    "taskplane.stateless-phase-projection/v1"
_STATELESS_PHASE_PROJECTION_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "phase", "mode", "handoff_id", "handoff_fingerprint",
    "full_envelope_reference", "source", "requirement", "design", "plan",
    "subject_fingerprint", "contracts", "acceptance", "obligations",
    "progress", "selected_artifacts", "authority_receipts", "lineage",
    "write_allow", "fingerprint",
})


def _phase_handoff_owner():
    if __package__:
        from . import phase_handoff
    else:
        import phase_handoff
    return phase_handoff


def stateless_phase_startup_projection(
        handoff: Mapping[str, object]) -> JsonObject:
    """Project a v2 handoff into exact Design/Plan startup authority.

    The projection is intentionally pure: it has no workspace argument and
    therefore cannot consult a locator, run, loop, claim, or predecessor
    lease.  Attempt-local execution authority is minted only by the kernel
    after this complete immutable projection validates.
    """
    owner = _phase_handoff_owner()
    try:
        checked = owner.validate_phase_handoff(handoff)
    except (owner.PhaseHandoffError, ValueError) as exc:
        raise StageValidationError(str(exc)) from exc
    phase = str(checked["successor"]["phase"])
    mode = str(checked["successor"]["mode"])
    if phase not in {"design", "plan"}:
        raise StageLifecycleError(
            "stateless startup projection supports only Design or Plan")
    if phase == "design":
        subject = str(checked["requirement"]["fingerprint"])
        write_allow = ["design/**"]
    else:
        design = checked.get("design")
        if not isinstance(design, Mapping):
            raise StageValidationError(
                "Plan startup requires an approved Design identity")
        subject = str(design["fingerprint"])
        write_allow = ["plan/**"]
    work = review_evidence.phase_startup_work(checked)
    material: JsonObject = {
        "schema": STATELESS_PHASE_PROJECTION_SCHEMA,
        "phase": phase,
        "mode": mode,
        "handoff_id": checked["handoff_id"],
        "handoff_fingerprint": checked["fingerprint"],
        "full_envelope_reference":
            review_evidence.create_phase_full_envelope_reference(checked),
        "source": copy.deepcopy(checked["source"]),
        "requirement": copy.deepcopy(checked["requirement"]),
        "design": copy.deepcopy(checked["design"]),
        "plan": copy.deepcopy(checked["plan"]),
        "subject_fingerprint": subject,
        "contracts": copy.deepcopy(checked["contracts"]),
        "acceptance": work["acceptance"],
        "obligations": work["obligations"],
        "progress": work["progress"],
        "selected_artifacts": copy.deepcopy(checked["selected_artifacts"]),
        "authority_receipts": copy.deepcopy(
            checked["authority_receipts"]),
        "lineage": copy.deepcopy(checked["lineage"]),
        "write_allow": write_allow,
    }
    material["fingerprint"] = review_evidence.content_fingerprint(material)
    return validate_stateless_phase_startup_projection(material, checked)


def validate_stateless_phase_startup_projection(
        projection: Mapping[str, object], handoff: Mapping[str, object]) \
        -> JsonObject:
    """Reject stale, foreign, widened, or synthetic startup projections."""
    row = _closed(
        projection, _STATELESS_PHASE_PROJECTION_FIELDS,
        "stateless phase startup projection")
    supplied_fingerprint = _fingerprint(
        row.get("fingerprint"), "stateless phase projection fingerprint")
    material = {str(key): copy.deepcopy(value)
                for key, value in row.items() if key != "fingerprint"}
    expected_fingerprint = review_evidence.content_fingerprint(material)
    if supplied_fingerprint != expected_fingerprint:
        raise StageIntegrityError(
            "stateless phase startup projection fingerprint mismatch")
    # Recreate from the sealed handoff and compare every byte.  This binds the
    # source, requirement, Design, acceptance, contracts and artifact set and
    # leaves no field that a caller can widen between validation and dispatch.
    owner = _phase_handoff_owner()
    try:
        checked = owner.validate_phase_handoff(handoff)
    except owner.PhaseHandoffError as exc:
        raise StageValidationError(str(exc)) from exc
    phase = str(checked["successor"]["phase"])
    if phase not in {"design", "plan"}:
        raise StageLifecycleError(
            "stateless startup projection supports only Design or Plan")
    expected_subject = (checked["requirement"]["fingerprint"]
                        if phase == "design" else
                        checked["design"]["fingerprint"])
    work = review_evidence.phase_startup_work(checked)
    exact = {
        "schema": STATELESS_PHASE_PROJECTION_SCHEMA,
        "phase": phase,
        "mode": checked["successor"]["mode"],
        "handoff_id": checked["handoff_id"],
        "handoff_fingerprint": checked["fingerprint"],
        "full_envelope_reference":
            review_evidence.create_phase_full_envelope_reference(checked),
        "source": checked["source"],
        "requirement": checked["requirement"],
        "design": checked["design"],
        "plan": checked["plan"],
        "subject_fingerprint": expected_subject,
        "contracts": checked["contracts"],
        "acceptance": work["acceptance"],
        "obligations": work["obligations"],
        "progress": work["progress"],
        "selected_artifacts": checked["selected_artifacts"],
        "authority_receipts": checked["authority_receipts"],
        "lineage": checked["lineage"],
        "write_allow": [f"{phase}/**"],
    }
    exact["fingerprint"] = review_evidence.content_fingerprint(exact)
    if dict(row) != exact:
        raise StageIntegrityError(
            "stateless phase startup projection is stale, foreign, or widened")
    return copy.deepcopy(exact)


def create_stateless_phase_scoped_view(
        handoff: Mapping[str, object], *, worker_id: str) -> JsonObject:
    """Create the scoped evidence view for one stage-owned phase worker."""
    return review_evidence.create_phase_scoped_view(
        handoff, worker_id=worker_id)


def stateless_phase_result_schema(*, phase: str) -> JsonObject:
    """Create the sealed result contract for a stage-owned phase worker."""
    return review_evidence.phase_result_schema(phase=phase)


def validate_stateless_phase_full_envelope_reference(
        value: Mapping[str, object], handoff: Mapping[str, object]) \
        -> JsonObject:
    """Validate one phase startup's reference to its complete handoff."""
    return review_evidence.validate_phase_full_envelope_reference(
        value, handoff)


def validate_stateless_phase_scoped_view(
        value: Mapping[str, object], handoff: Mapping[str, object], *,
        expected_worker_id: str) -> JsonObject:
    """Validate one stage-owned phase worker's scoped evidence view."""
    return review_evidence.validate_phase_scoped_view(
        value, handoff, expected_worker_id=expected_worker_id)


def validate_stateless_phase_result_schema(
        value: Mapping[str, object], *, expected_phase: str) -> JsonObject:
    """Validate one stage-owned phase worker's result contract."""
    return review_evidence.validate_phase_result_schema(
        value, expected_phase=expected_phase)


# Contract-oriented aliases used by the successor pickup coordinator.
project_stateless_phase_startup = stateless_phase_startup_projection
validate_stateless_phase_projection = \
    validate_stateless_phase_startup_projection


def _closed(value: object, fields: frozenset[str], label: str, *,
            optional: frozenset[str] = frozenset()) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise StageValidationError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise StageValidationError(f"{label} field names must be strings")
    keys = set(value)
    unknown = keys - fields
    missing = fields - keys - optional
    if unknown:
        raise StageValidationError(
            f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise StageValidationError(
            f"{label} has missing fields: {', '.join(sorted(missing))}")
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


def _strings(values: object, label: str, *,
             pattern: re.Pattern[str] | None = None,
             allow_empty: bool = True) -> list[str]:
    if isinstance(values, (str, bytes, Mapping)):
        raise StageValidationError(f"{label} must be a list")
    try:
        rows = list(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise StageValidationError(f"{label} must be a list") from exc
    if len(rows) > MAX_COLLECTION_ITEMS:
        raise StageValidationError(
            f"{label} contains at most {MAX_COLLECTION_ITEMS} entries")
    result: list[str] = []
    for raw in rows:
        if not isinstance(raw, str):
            raise StageValidationError(f"{label} entries must be strings")
        text = raw.strip()
        if text != raw or not text or len(text.encode("utf-8")) > 256 or \
                (pattern is not None and not pattern.fullmatch(text)):
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


def _plain_mapping(value: object, label: str, *,
                   allow_empty: bool = False,
                   max_bytes: int = 8 * 1024) -> dict[str, object]:
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
        "fingerprint": _fingerprint(
            row.get("fingerprint"), "requirement fingerprint"),
    }


def _design(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    row = _closed(value, _DESIGN_FIELDS, "design")
    return {
        "revision": _revision(row.get("revision"), "design revision"),
        "fingerprint": _fingerprint(
            row.get("fingerprint"), "design fingerprint"),
    }


def _portable_reference(value: object, label: str, *,
                        manifest_bound: bool = False) -> dict[str, object]:
    row = _closed(value, _PORTABLE_REFERENCE_FIELDS, label)
    if row.get("schema") != "taskplane.artifact-reference/v1" or \
            row.get("transport") != "artifact-reference":
        raise StageValidationError(f"{label} schema is invalid")
    kind = _bounded_text(row.get("kind"), f"{label} kind", maximum=64)
    if not _KIND.fullmatch(kind):
        raise StageValidationError(f"{label} kind is invalid")
    fingerprint = _fingerprint(row.get("fingerprint"),
                               f"{label} fingerprint")
    digest = _fingerprint(row.get("digest"), f"{label} digest")
    byte_count = row.get("bytes")
    if isinstance(byte_count, bool) or not isinstance(byte_count, int) or \
            byte_count < 0:
        raise StageValidationError(f"{label} byte count is invalid")
    if manifest_bound and byte_count > MAX_INPUT_MANIFEST_BYTES:
        raise StageValidationError(
            f"input manifest exceeds {MAX_INPUT_MANIFEST_BYTES} bytes")
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


def _references(values: object, label: str, *,
                allow_empty: bool = True) -> list[dict[str, object]]:
    if isinstance(values, (str, bytes, Mapping)):
        raise StageValidationError(f"{label} must be a list")
    try:
        rows = list(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise StageValidationError(f"{label} must be a list") from exc
    if len(rows) > MAX_COLLECTION_ITEMS:
        raise StageValidationError(
            f"{label} contains at most {MAX_COLLECTION_ITEMS} references")
    result = [_portable_reference(row, f"{label} reference") for row in rows]
    identities = [(str(row["kind"]), str(row["fingerprint"]))
                  for row in result]
    if len(set(identities)) != len(identities):
        raise StageValidationError(f"{label} contains duplicate references")
    if not allow_empty and not result:
        raise StageValidationError(f"{label} must not be empty")
    return sorted(result, key=lambda row: (str(row["kind"]),
                                           str(row["fingerprint"])))


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


def _authority(value: object, *, run_id: str,
               requirement: Mapping[str, object],
               design: Mapping[str, object] | None) -> dict[str, object]:
    row = _closed(value, _AUTHORITY_FIELDS, "authority")
    if row.get("schema") != AUTHORITY_SCHEMA:
        raise StageValidationError("authority schema is invalid")
    checked: dict[str, object] = {
        "schema": AUTHORITY_SCHEMA,
        "run_id": _path_id(row.get("run_id"), "authority run id"),
        "repository_id": _bounded_text(
            row.get("repository_id"), "authority repository id"),
        "repository_key": _bounded_text(
            row.get("repository_key"), "authority repository key"),
        "worktree_id": _bounded_text(
            row.get("worktree_id"), "authority worktree id"),
        "target_revision": _revision(
            row.get("target_revision"), "authority target revision"),
        "worktree_revision": _revision(
            row.get("worktree_revision"), "authority worktree revision"),
        "requirement_id": _identifier(
            row.get("requirement_id"), "authority requirement id"),
        "requirement_revision": _revision(
            row.get("requirement_revision"),
            "authority requirement revision"),
        "design_revision": None,
        "design_fingerprint": None,
        "actor": _identifier(row.get("actor"), "authority actor"),
        "session_id": _identifier(
            row.get("session_id"), "authority session id"),
        "authority_revision": row.get("authority_revision"),
        "authority_fingerprint": _fingerprint(
            row.get("authority_fingerprint"), "authority fingerprint"),
    }
    authority_revision = checked["authority_revision"]
    if isinstance(authority_revision, bool) or \
            not isinstance(authority_revision, int) or authority_revision < 0:
        raise StageValidationError("authority revision is invalid")
    if row.get("design_revision") is not None:
        checked["design_revision"] = _revision(
            row.get("design_revision"), "authority design revision")
    if row.get("design_fingerprint") is not None:
        checked["design_fingerprint"] = _fingerprint(
            row.get("design_fingerprint"), "authority design fingerprint")
    if checked["run_id"] != run_id or \
            checked["requirement_id"] != requirement.get("id") or \
            checked["requirement_revision"] != requirement.get("revision"):
        raise StageValidationError("authority identity does not match stage")
    expected_design_revision = design.get("revision") if design else None
    expected_design_fingerprint = design.get("fingerprint") if design else None
    if checked["design_revision"] != expected_design_revision or \
            checked["design_fingerprint"] != expected_design_fingerprint:
        raise StageValidationError("authority design does not match stage")
    return checked


def request_fingerprint(request: Mapping[str, object]) -> str:
    """Return the stable semantic identity for an idempotent stage command."""
    if not isinstance(request, Mapping):
        raise StageValidationError("stage request must be an object")
    try:
        return review_evidence.content_fingerprint(request)
    except (TypeError, ValueError) as exc:
        raise StageValidationError("stage request must be canonical JSON") \
            from exc


def stage_fingerprint(stage: Mapping[str, object]) -> str:
    """Return a stage's semantic fingerprint, excluding its self-reference."""
    if not isinstance(stage, Mapping):
        raise StageValidationError("stage must be an object")
    material = {str(key): value for key, value in stage.items()
                if str(key) != "fingerprint"}
    try:
        return review_evidence.content_fingerprint(material)
    except (TypeError, ValueError) as exc:
        raise StageValidationError("stage must be canonical JSON") from exc


def create_stage(*, run_id: str, stage_id: str,
                 requirement: Mapping[str, object],
                 design: Mapping[str, object] | None, stage_kind: str,
                 parent_stage_ids: Iterable[str],
                 predecessor_stage_ids: Iterable[str],
                 input_manifest_ref: Mapping[str, object],
                 execution_root_id: str, deliverables: Iterable[str],
                 budget: Mapping[str, object], dependencies: Iterable[str],
                 contracts: Iterable[str], authority: Mapping[str, object],
                 created_at: str,
                 selected_artifacts: Iterable[Mapping[str, object]] = (),
                 ) -> JsonObject:
    """Create one canonical active aggregate with immutable lineage inputs."""
    run = _path_id(run_id, "run id")
    stage = _path_id(stage_id, "stage id")
    requirement_row = _requirement(requirement)
    design_row = _design(design)
    parents = _path_ids(parent_stage_ids, "parent stage ids")
    predecessors = _path_ids(
        predecessor_stage_ids, "predecessor stage ids")
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
            input_manifest_ref, "input manifest reference",
            manifest_bound=True),
        "execution_root_id": _path_id(
            execution_root_id, "execution root id"),
        "deliverables": deliverable_rows,
        "selected_artifacts": _references(
            selected_artifacts, "selected artifacts"),
        "budget": _budget(budget),
        "dependencies": dependency_rows,
        "contracts": contract_rows,
        "authority": _authority(
            authority, run_id=run, requirement=requirement_row,
            design=design_row),
        "state": "active",
        "outcome": None,
        "default_consumable": True,
        "terminal": None,
        "created_at": _timestamp(created_at, "stage creation time"),
        "aggregate_revision": 1,
    }
    if body["execution_root_id"] != f"execution-{stage}":
        raise StageValidationError(
            "execution root id must be deterministically bound to stage id")
    body["fingerprint"] = stage_fingerprint(body)
    return validate_stage(body)


def validate_stage(stage: Mapping[str, object]) -> JsonObject:
    """Validate and return a detached canonical ``taskplane.stage/v1`` value."""
    row = _closed(stage, _STAGE_FIELDS, "stage",
                  optional=frozenset({"fingerprint"}))
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
    predecessors = _path_ids(
        row.get("predecessor_stage_ids"), "predecessor stage ids")
    dependencies = _path_ids(row.get("dependencies"), "dependencies")
    if stage_id in set(parents) | set(predecessors) | set(dependencies):
        raise StageValidationError("a stage cannot refer to itself")
    deliverables = _strings(row.get("deliverables"), "deliverables")
    contracts = _strings(row.get("contracts"), "contracts",
                         pattern=_CONTRACT)
    selected_artifacts = _references(
        row.get("selected_artifacts"), "selected artifacts")
    for label, canonical in (
            ("parent stage ids", parents),
            ("predecessor stage ids", predecessors),
            ("deliverables", deliverables),
            ("selected artifacts", selected_artifacts),
            ("dependencies", dependencies),
            ("contracts", contracts)):
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
        row.get("input_manifest_ref"), "input manifest reference",
        manifest_bound=True)
    execution_root_id = _path_id(
        row.get("execution_root_id"), "execution root id")
    if execution_root_id != f"execution-{stage_id}":
        raise StageValidationError(
            "execution root id must be deterministically bound to stage id")
    budget = _budget(row.get("budget"))
    authority = _authority(
        row.get("authority"), run_id=run_id, requirement=requirement,
        design=design)
    created_at = _timestamp(row.get("created_at"), "stage creation time")
    aggregate_revision = row.get("aggregate_revision")
    if isinstance(aggregate_revision, bool) or \
            not isinstance(aggregate_revision, int) or aggregate_revision < 1:
        raise StageValidationError("aggregate revision is invalid")

    state = row.get("state")
    outcome = row.get("outcome")
    default_consumable = row.get("default_consumable")
    terminal = row.get("terminal")
    if state not in STAGE_STATES or not isinstance(default_consumable, bool):
        raise StageValidationError("stage lifecycle is invalid")
    canonical_terminal: dict[str, object] | None = None
    if state == "active":
        if outcome is not None or terminal is not None or \
                default_consumable is not True:
            raise StageValidationError("active stage has terminal state")
    else:
        if outcome not in TERMINAL_OUTCOMES:
            raise StageValidationError("terminal stage outcome is invalid")
        terminal_row = _closed(terminal, _TERMINAL_FIELDS,
                               "terminal attribution")
        actor = _identifier(terminal_row.get("actor"), "terminal actor")
        terminalized_at = _timestamp(
            terminal_row.get("terminalized_at"), "terminal time")
        reason_code = terminal_row.get("reason_code")
        reason = terminal_row.get("reason")
        completed = _strings(
            terminal_row.get("completed_deliverables"),
            "completed deliverables")
        if not set(completed).issubset(deliverables):
            raise StageValidationError(
                "completed deliverables were not declared")
        completion_evidence = _references(
            terminal_row.get("completion_evidence"), "completion evidence")
        if outcome == "done":
            if completed != deliverables or not completion_evidence:
                raise StageLifecycleError(
                    "done requires all deliverables and completion evidence")
            if reason_code is not None or reason is not None or \
                    default_consumable is not True:
                raise StageValidationError("done terminal state is invalid")
        else:
            reason_code = _identifier(reason_code, "terminal reason code")
            reason = _bounded_text(
                reason, "terminal reason", maximum=MAX_REASON_BYTES)
            if default_consumable is not False:
                raise StageValidationError(
                    f"{outcome} stage cannot be consumed by default")
        canonical_terminal = {
            "actor": actor,
            "terminalized_at": terminalized_at,
            "reason_code": reason_code,
            "reason": reason,
            "completed_deliverables": completed,
            "completion_evidence": completion_evidence,
        }
        if terminal != canonical_terminal:
            raise StageValidationError(
                "terminal attribution is not canonical")

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
        stage: Mapping[str, object], *, outcome: str, actor: str,
        terminal_at: str | None = None, terminalized_at: str | None = None,
        reason_code: str | None = None, reason: str | None = None,
        completed_deliverables: Iterable[str] = (),
        completion_evidence: Iterable[Mapping[str, object]] = ()) -> JsonObject:
    """Return the sole legal active-to-terminal revision of a stage."""
    current = validate_stage(stage)
    if current["state"] != "active":
        raise StageLifecycleError("terminal stage cannot transition again")
    terminal_outcome = str(outcome or "")
    if terminal_outcome not in TERMINAL_OUTCOMES:
        raise StageLifecycleError("terminal outcome is invalid")
    if terminal_at is not None and terminalized_at is not None and \
            terminal_at != terminalized_at:
        raise StageValidationError("terminal time is ambiguous")
    at = terminalized_at if terminalized_at is not None else terminal_at
    terminal: JsonObject = {
        "actor": _identifier(actor, "terminal actor"),
        "terminalized_at": _timestamp(at, "terminal time"),
        "reason_code": reason_code,
        "reason": reason,
        "completed_deliverables": _strings(
            completed_deliverables, "completed deliverables"),
        "completion_evidence": _references(
            completion_evidence, "completion evidence"),
    }
    if terminal["actor"] != current["authority"]["actor"]:
        raise StageLifecycleError(
            "terminal actor does not match stage authority")
    updated = copy.deepcopy(current)
    updated.update({
        "state": "terminal",
        "outcome": terminal_outcome,
        "default_consumable": terminal_outcome == "done",
        "terminal": terminal,
        "aggregate_revision": int(current["aggregate_revision"]) + 1,
    })
    updated["fingerprint"] = stage_fingerprint(updated)
    return validate_stage(updated)


def split_child_id(run_id: str, parent_stage_id: str,
                   operation_id: str, ordinal: int) -> str:
    """Derive an independently addressable child id from one split request."""
    run = _path_id(run_id, "run id")
    parent = _path_id(parent_stage_id, "parent stage id")
    operation = _identifier(operation_id, "split operation id")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise SplitValidationError("child ordinal is invalid")
    identity = request_fingerprint({
        "schema": "taskplane.split-child-identity/v1",
        "run_id": run,
        "parent_stage_id": parent,
        "operation_id": operation,
        "ordinal": ordinal,
    })
    return f"stage-{identity[:32]}"


def _lineage_row(*, parent_stage_id: str | None, child_stage_id: str,
                 input_manifest_ref: Mapping[str, object],
                 operation_id: str,
                 predecessor_stage_ids: Iterable[str] = (),
                 ) -> dict[str, object]:
    body: dict[str, object] = {
        "schema": LINEAGE_SCHEMA,
        "parent_stage_id": (
            _path_id(parent_stage_id, "lineage parent stage id")
            if parent_stage_id is not None else None),
        "child_stage_id": child_stage_id,
        "predecessor_stage_ids": _path_ids(
            predecessor_stage_ids, "lineage predecessor stage ids",
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
            _path_id(value.get("parent_stage_id"),
                     "lineage parent stage id")
            if value.get("parent_stage_id") is not None else None),
        "child_stage_id": _path_id(
            value.get("child_stage_id"), "lineage child stage id"),
        "predecessor_stage_ids": _path_ids(
            value.get("predecessor_stage_ids"),
            "lineage predecessor stage ids"),
        "handoff_fingerprint": _fingerprint(
            value.get("handoff_fingerprint"), "lineage handoff fingerprint"),
        "split_operation_id": _identifier(
            value.get("split_operation_id"), "split operation id"),
    }
    if canonical["parent_stage_id"] is not None and \
            canonical["parent_stage_id"] == canonical["child_stage_id"]:
        raise StageValidationError("lineage parent and child must differ")
    expected = request_fingerprint(canonical)
    if value.get("fingerprint") != expected:
        raise StageIntegrityError("stage lineage fingerprint mismatch")
    canonical["fingerprint"] = expected
    return canonical


def _dependency_cycle(dependencies: Mapping[str, list[str]],
                      child_ids: set[str]) -> bool:
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


def create_split(parent: Mapping[str, object], *, operation_id: str,
                 child_specs: Iterable[Mapping[str, object]], actor: str,
                 terminalized_at: str, reason: str) -> JsonObject:
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
        raise SplitValidationError("child specifications must be a list") \
            from exc
    if len(specs) < 2 or len(specs) > MAX_COLLECTION_ITEMS:
        raise SplitValidationError(
            "a split requires between 2 and 64 children (at least two)")

    canonical_specs: list[dict[str, object]] = []
    fingerprints: set[str] = set()
    for ordinal, raw in enumerate(specs):
        row = _closed(raw, _SPLIT_SPEC_FIELDS, f"child {ordinal} spec",
                      optional=frozenset({"deliverables", "contracts"}))
        budget = _budget(row.get("budget"))
        selected = _references(
            row.get("selected_artifacts"),
            f"child {ordinal} selected artifacts")
        dependencies = _strings(
            row.get("dependencies"), f"child {ordinal} dependencies")
        kind = _bounded_text(
            row.get("stage_kind"), f"child {ordinal} stage kind", maximum=64)
        if not _KIND.fullmatch(kind):
            raise SplitValidationError(f"child {ordinal} stage kind is invalid")
        deliverables = _strings(
            row.get("deliverables", current["deliverables"]),
            f"child {ordinal} deliverables")
        contracts = _strings(
            row.get("contracts", current["contracts"]),
            f"child {ordinal} contracts", pattern=_CONTRACT)
        input_ref = _portable_reference(
            row.get("input_manifest_ref"),
            f"child {ordinal} input manifest reference", manifest_bound=True)
        if input_ref["kind"] != "stage-handoff":
            raise SplitValidationError(
                f"child {ordinal} needs an explicit stage handoff")
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
        (str(row["kind"]), str(row["fingerprint"]))
        for row in current["selected_artifacts"]
    }
    for ordinal, spec in enumerate(canonical_specs):
        child_artifacts = {
            (str(row["kind"]), str(row["fingerprint"]))
            for row in spec["selected_artifacts"]
        }
        if not child_artifacts.issubset(parent_artifacts):
            raise SplitValidationError(
                f"child {ordinal} has an undeclared parent artifact subset")

    child_ids = [split_child_id(
        str(current["run_id"]), str(current["stage_id"]), operation, ordinal)
        for ordinal in range(len(canonical_specs))]
    if len(set(child_ids)) != len(child_ids) or \
            str(current["stage_id"]) in child_ids:
        raise SplitValidationError("split child identity collision")
    child_id_set = set(child_ids)
    external_dependencies = set(current["dependencies"]) | \
        set(current["parent_stage_ids"]) | \
        set(current["predecessor_stage_ids"]) | {str(current["stage_id"])}
    resolved_dependencies: dict[str, list[str]] = {}
    for ordinal, spec in enumerate(canonical_specs):
        resolved: list[str] = []
        for dependency in spec["dependencies"]:
            match = re.fullmatch(r"child:(\d+)", str(dependency))
            value = (child_ids[int(match.group(1))]
                     if match and int(match.group(1)) < len(child_ids)
                     else str(dependency))
            if value == child_ids[ordinal]:
                raise SplitValidationError("a split child cannot depend on itself")
            if value not in child_id_set and value not in external_dependencies:
                raise SplitValidationError(
                    f"child {ordinal} has an unresolved dependency")
            resolved.append(value)
        if len(set(resolved)) != len(resolved):
            raise SplitValidationError(
                f"child {ordinal} has duplicate dependencies")
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
            run_id=str(current["run_id"]), stage_id=child_id,
            requirement=current["requirement"], design=current["design"],
            stage_kind=str(spec["stage_kind"]),
            parent_stage_ids=[str(current["stage_id"])],
            predecessor_stage_ids=[],
            input_manifest_ref=spec["input_manifest_ref"],
            execution_root_id=root_id,
            deliverables=spec["deliverables"], budget=spec["budget"],
            dependencies=resolved_dependencies[child_id],
            contracts=spec["contracts"], authority=current["authority"],
            created_at=terminalized_at,
            selected_artifacts=spec["selected_artifacts"])
        children.append(child)

    closed_parent = terminalize_stage(
        current, outcome="closed", actor=actor,
        terminalized_at=terminalized_at, reason_code="split", reason=reason)
    lineage = [validate_lineage(_lineage_row(
        parent_stage_id=str(current["stage_id"]),
        child_stage_id=str(child["stage_id"]),
        input_manifest_ref=child["input_manifest_ref"],
        operation_id=operation)) for child in children]
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
        "predecessor_stage_ids": copy.deepcopy(
            stage["predecessor_stage_ids"]),
        "dependencies": copy.deepcopy(stage["dependencies"]),
        "input_manifest_fingerprint": input_row.get("fingerprint"),
        "execution_root_id": stage["execution_root_id"],
        "deliverables": copy.deepcopy(stage["deliverables"]),
        "completed_deliverables": copy.deepcopy(
            terminal_row.get("completed_deliverables") or []),
        "completion_evidence_fingerprints": sorted(
            str(row.get("fingerprint")) for row in completion
            if isinstance(row, Mapping)),
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
        raise StageValidationError(
            f"stage summary exceeds {MAX_STAGE_SUMMARY_BYTES} bytes")
    return body


def bounded_stage_summary(stage: Mapping[str, object]) -> JsonObject:
    """Return the <=16 KiB read model without opening an execution tree."""
    return _bounded_stage_summary(validate_stage(stage))


def _state_from_head(stage_id: str,
                     head: Mapping[str, object]) -> tuple[str, str]:
    value: Mapping[str, object] = head
    if isinstance(head.get("summary"), Mapping):
        value = head["summary"]  # type: ignore[assignment]
        if value.get("schema") != SUMMARY_SCHEMA:
            raise StageValidationError("stage head summary schema is invalid")
        if len(review_evidence.canonical_bytes(value)) > \
                MAX_STAGE_SUMMARY_BYTES:
            raise StageValidationError(
                f"stage head summary exceeds {MAX_STAGE_SUMMARY_BYTES} bytes")
        expected = request_fingerprint(
            {key: item for key, item in value.items()
             if key != "fingerprint"})
        if value.get("fingerprint") != expected:
            raise StageIntegrityError("stage head summary fingerprint mismatch")
        stage_value = _path_id(value.get("stage_id"),
                               "stage head summary id")
        state = str(value.get("state") or "")
        aggregate_fingerprint = _fingerprint(
            value.get("aggregate_fingerprint"),
            "stage summary aggregate fingerprint")
        if _fingerprint(value.get("stage_fingerprint"),
                        "stage summary fingerprint") != \
                aggregate_fingerprint:
            raise StageIntegrityError(
                "stage summary fingerprint aliases disagree")
        reference = head.get("object")
        if not isinstance(reference, Mapping) or _fingerprint(
                reference.get("fingerprint"),
                "stage object fingerprint") != aggregate_fingerprint:
            raise StageIntegrityError(
                "stage object and summary fingerprints disagree")
    elif value.get("schema") == SCHEMA:
        aggregate = validate_stage(value)
        stage_value = str(aggregate["stage_id"])
        state = str(aggregate["state"])
    else:
        # A compact head may expose summary fields directly.
        stage_value = _path_id(value.get("stage_id", stage_id),
                               "stage head id")
        state = str(value.get("state") or "")
        if not _FINGERPRINT.fullmatch(str(value.get("stage_fingerprint") or
                                          value.get("fingerprint") or "")):
            raise StageValidationError("stage head fingerprint is invalid")
    if stage_value != stage_id or state not in STAGE_STATES:
        raise StageValidationError("stage head identity or state is invalid")
    return stage_value, state


def active_stage_projection(
        stage_heads: Mapping[str, Mapping[str, object]],
        foreground_stage_id: str | None = None) -> JsonObject:
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


def _stage_head(store: object, run_id: str,
                stage: Mapping[str, object]) -> dict[str, object]:
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


def _read_indexed_stage(store: object, run_id: str, stage_id: str,
                        head: object, *,
                        expected_fingerprint: str | None = None) -> JsonObject:
    if not isinstance(head, Mapping) or set(head) != {"object", "summary"}:
        raise StageLifecycleError(f"stage head {stage_id} is invalid")
    reference = head.get("object")
    summary = head.get("summary")
    if not isinstance(reference, Mapping) or not isinstance(summary, Mapping):
        raise StageLifecycleError(f"stage head {stage_id} is incomplete")
    fingerprint = _fingerprint(
        reference.get("fingerprint"), "stage head fingerprint")
    if expected_fingerprint is not None and fingerprint != \
            _fingerprint(expected_fingerprint, "expected stage fingerprint"):
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
    if not isinstance(head, Mapping) or not isinstance(
            head.get("object"), Mapping):
        raise StageLifecycleError(f"stage head {stage_id} is invalid")
    return _fingerprint(
        head["object"].get("fingerprint"), "stage head fingerprint")


class StageLifecycle:
    """Transactional service over immutable stage values and ``RunStore``.

    The service deliberately accepts repository authority revalidation as a
    dependency.  It therefore owns no repository mutation or worktree cleanup
    edge and can be exercised on every host with the same lifecycle semantics.
    """

    def __init__(
            self, store: object, *, workspace: str | None = None,
            artifact_store: review_evidence.ArtifactStore | None = None,
            authority_resolver: Callable[[Mapping[str, object]],
                                         Mapping[str, object]],
            authority_validator: Callable[
                [Mapping[str, object], Mapping[str, object]], object
            ],
            handoff_resolver: Callable[[Mapping[str, object]], object]
            | None = None,
            artifact_validator: Callable[[Mapping[str, object]], object]
            | None = None,
            execution_root_claimer: Callable[[Mapping[str, object]], object]
            | None = None):
        commit = getattr(store, "commit_stage_operation", None)
        if not callable(commit):
            raise StageLifecycleError(
                "StageLifecycle requires a stage-capable RunStore")
        for value, label in (
                (authority_resolver, "authority resolver"),
                (authority_validator, "authority validator")):
            if not callable(value):
                raise StageValidationError(f"{label} must be callable")
        for value, label in (
                (handoff_resolver, "handoff resolver"),
                (artifact_validator, "artifact validator"),
                (execution_root_claimer, "execution root claimer")):
            if value is not None and not callable(value):
                raise StageValidationError(f"{label} must be callable")
        self.store = store
        self.workspace = workspace
        self.artifact_store = artifact_store
        self.authority_resolver = authority_resolver
        self.authority_validator = authority_validator
        self.handoff_resolver = handoff_resolver
        self.artifact_validator = artifact_validator
        self.execution_root_claimer = execution_root_claimer

    def _check_authority(
            self, expected: Mapping[str, object],
            manifest: Mapping[str, object],
            stage: Mapping[str, object]) -> None:
        current = self.authority_resolver(copy.deepcopy(manifest))
        current_checked = _authority(
            current, run_id=str(stage["run_id"]),
            requirement=stage["requirement"], design=stage["design"])
        self.authority_validator(copy.deepcopy(dict(expected)),
                                 copy.deepcopy(current_checked))

    def _artifact_store(self) -> review_evidence.ArtifactStore:
        if self.artifact_store is not None:
            return self.artifact_store
        if not self.workspace:
            raise StageLifecycleError(
                "stage handoff validation requires a canonical workspace")
        return stage_handoff.canonical_artifact_store(self.workspace)

    def _claim_execution_root(
            self, stage: Mapping[str, object], *,
            attempt_id: str | None = None) -> Mapping[str, object]:
        claim = runtime_storage.claim_stage_execution_root_for_run(
            getattr(self.store, "home", None), str(stage["run_id"]),
            str(stage["stage_id"]), str(stage["execution_root_id"]),
            attempt_id=attempt_id)
        if self.execution_root_claimer is not None:
            self.execution_root_claimer(copy.deepcopy(claim))
        return claim

    def _verify_artifact(self, reference: Mapping[str, object]) -> None:
        if self.artifact_validator is not None:
            self.artifact_validator(copy.deepcopy(reference))
            return
        review_evidence.verify_portable_artifact_reference(
            self._artifact_store(), dict(reference))

    def _read_handoff(
            self, reference: Mapping[str, object], *,
            producer: Mapping[str, object],
            consumer: Mapping[str, object]) -> JsonObject:
        authority = producer["authority"]
        assert isinstance(authority, Mapping)
        resolved: object = None
        if self.handoff_resolver is not None:
            try:
                resolved = self.handoff_resolver(copy.deepcopy(reference))
            except Exception as exc:
                raise StageLifecycleError(
                    "handoff reference fingerprint could not be resolved") \
                    from exc
        if resolved is None:
            store, stored_reference = self._artifact_store(), reference
        elif isinstance(resolved, tuple) and len(resolved) == 2:
            store, stored_reference = resolved
        else:
            raise StageLifecycleError(
                "handoff resolver must return (artifact_store, reference)")
        if not isinstance(store, review_evidence.ArtifactStore) or \
                not isinstance(stored_reference, Mapping):
            raise StageLifecycleError("handoff resolver returned invalid data")
        checked = stage_handoff.read_manifest(
            store, stored_reference,
            expected_authority_revision=int(authority["authority_revision"]),
            expected_authority_fingerprint=str(
                authority["authority_fingerprint"]),
            allow_nonconsumable_reuse=producer["outcome"] in {
                "closed", "discarded",
            })
        return self._verify_handoff_value(
            checked, producer=producer, consumer=consumer)

    def _verify_handoff_value(
            self, checked: Mapping[str, object], *,
            producer: Mapping[str, object],
            consumer: Mapping[str, object] | None = None) -> JsonObject:
        authority = producer["authority"]
        assert isinstance(authority, Mapping)
        checked = copy.deepcopy(dict(checked))
        produced = checked.get("producer")
        if not isinstance(produced, Mapping) or \
                produced.get("stage_id") != producer.get("stage_id") or \
                produced.get("outcome") != producer.get("outcome"):
            raise StageLifecycleError(
                "handoff producer does not match predecessor stage")
        if checked.get("requirement") != producer.get("requirement") or \
                checked.get("design") != producer.get("design"):
            raise StageLifecycleError(
                "handoff revision does not match predecessor stage")
        authorization = checked.get("authorization")
        if not isinstance(authorization, Mapping) or \
                authorization.get("actor") != authority.get("actor") or \
                authorization.get("session_id") != authority.get("session_id"):
            raise StageLifecycleError(
                "handoff authorization does not match stage authority")
        if consumer is not None:
            if checked.get("requirement") != consumer.get("requirement") or \
                    checked.get("design") != consumer.get("design"):
                raise StageLifecycleError(
                    "handoff revision does not match successor stage")
            input_ref = consumer.get("input_manifest_ref")
            if not isinstance(input_ref, Mapping) or \
                    input_ref.get("fingerprint") != checked.get("fingerprint"):
                raise StageLifecycleError(
                    "successor input does not reference the verified handoff")
            if checked.get("selected_artifacts") != \
                    consumer.get("selected_artifacts"):
                raise StageLifecycleError(
                    "handoff artifacts do not match successor selection")
        return checked

    def _verify_handoff(
            self, manifest: Mapping[str, object], *,
            producer: Mapping[str, object],
            consumer: Mapping[str, object] | None = None) -> JsonObject:
        authority = producer["authority"]
        assert isinstance(authority, Mapping)
        checked = stage_handoff.validate_manifest(
            self._artifact_store(), manifest,
            expected_authority_revision=int(authority["authority_revision"]),
            expected_authority_fingerprint=str(
                authority["authority_fingerprint"]),
            allow_nonconsumable_reuse=producer["outcome"] in {
                "closed", "discarded",
            })
        return self._verify_handoff_value(
            checked, producer=producer, consumer=consumer)

    def start_stage(
            self, stage: Mapping[str, object], *, expected_revision: int,
            operation_id: str,
            expected_predecessor_fingerprints: Mapping[str, str] | None = None,
            foreground: bool = True) -> dict[str, object]:
        """Atomically index one root stage or verified successor stage."""
        candidate = validate_stage(stage)
        if candidate["state"] != "active":
            raise StageLifecycleError("a new stage must be active")
        operation = _identifier(operation_id, "stage operation id")
        if not isinstance(foreground, bool):
            raise StageValidationError("foreground selection must be boolean")
        expected_predecessors = {
            _path_id(key, "expected predecessor id"):
            _fingerprint(value, "expected predecessor fingerprint")
            for key, value in (expected_predecessor_fingerprints or {}).items()
        }
        if set(expected_predecessors) != set(
                candidate["predecessor_stage_ids"]):
            raise StageLifecycleError(
                "expected predecessor heads do not match stage lineage")
        request = request_fingerprint({
            "operation": "start_stage",
            "operation_id": operation,
            "stage_fingerprint": candidate["fingerprint"],
            "expected_predecessor_fingerprints": expected_predecessors,
            "handoff_fingerprint": (
                candidate["input_manifest_ref"]["fingerprint"]),
            "foreground": foreground,
        })

        def authority_check(current: dict[str, object]) -> None:
            self._check_authority(
                candidate["authority"], current, candidate)

        def mutate(current: dict[str, object]) -> dict[str, object]:
            heads = copy.deepcopy(current.get("stage_heads") or {})
            if not isinstance(heads, dict):
                raise StageLifecycleError("stage heads are invalid")
            stage_id = str(candidate["stage_id"])
            if stage_id in heads:
                raise StageLifecycleError("stage id is already indexed")
            predecessor_objects: dict[str, JsonObject] = {}
            for predecessor_id in candidate["predecessor_stage_ids"]:
                if predecessor_id not in heads:
                    raise StageLifecycleError(
                        "successor predecessor is not indexed")
                predecessor = _read_indexed_stage(
                    self.store, str(candidate["run_id"]), predecessor_id,
                    heads[predecessor_id], expected_fingerprint=
                    expected_predecessors[predecessor_id])
                if predecessor["state"] != "terminal":
                    raise StageLifecycleError(
                        "successor predecessor is not terminal")
                predecessor_objects[predecessor_id] = predecessor
            for parent_id in candidate["parent_stage_ids"]:
                if parent_id not in heads:
                    raise StageLifecycleError(
                        "successor parent is not indexed")
                _read_indexed_stage(
                    self.store, str(candidate["run_id"]), parent_id,
                    heads[parent_id])
            for existing_id, existing_head in heads.items():
                summary = existing_head.get("summary") \
                    if isinstance(existing_head, Mapping) else None
                if isinstance(summary, Mapping) and \
                        summary.get("execution_root_id") == \
                        candidate["execution_root_id"]:
                    raise StageLifecycleError(
                        f"execution root is already owned by {existing_id}")
            if predecessor_objects:
                verified = False
                failures: list[Exception] = []
                for producer in predecessor_objects.values():
                    try:
                        self._read_handoff(
                            candidate["input_manifest_ref"],
                            producer=producer, consumer=candidate)
                    except (StageValidationError,
                            stage_handoff.HandoffValidationError) as exc:
                        failures.append(exc)
                        continue
                    verified = True
                    break
                if not verified:
                    raise StageLifecycleError(
                        "successor handoff does not match any predecessor") \
                        from (failures[-1] if failures else None)
            self._claim_execution_root(candidate)
            heads[stage_id] = _stage_head(
                self.store, str(candidate["run_id"]), candidate)
            old_projection = current.get("active_stage_projection")
            old_foreground = (old_projection.get("foreground_stage_id")
                              if isinstance(old_projection, Mapping) else None)
            projection = active_stage_projection(
                heads, stage_id if foreground else old_foreground)
            lineage = copy.deepcopy(current.get("lineage") or [])
            if not isinstance(lineage, list):
                raise StageLifecycleError("stage lineage is invalid")
            lineage_parents: list[str | None] = list(
                candidate["parent_stage_ids"])
            if not lineage_parents and candidate["predecessor_stage_ids"]:
                # One canonical predecessor-only relationship carries the
                # complete predecessor set; emitting one row per predecessor
                # would produce identical immutable lineage fingerprints.
                lineage_parents = [None]
            for parent_id in lineage_parents:
                lineage.append(validate_lineage(_lineage_row(
                    parent_stage_id=parent_id,
                    child_stage_id=stage_id,
                    input_manifest_ref=candidate["input_manifest_ref"],
                    operation_id=operation,
                    predecessor_stage_ids=
                    candidate["predecessor_stage_ids"])))
            result = {"head": heads[stage_id],
                      "active_stage_projection": projection}
            return {
                "changes": {
                    "stage_heads": heads,
                    "lineage": lineage,
                    "active_stage_projection": projection,
                },
                "receipt": {
                    "operation": "start_stage",
                    "stage_ids": [stage_id],
                    "result": result,
                },
            }

        return self.store.commit_stage_operation(
            str(candidate["run_id"]), expected_revision=expected_revision,
            operation_id=operation, request_fingerprint=request,
            mutate=mutate, validate_authority=authority_check)

    def terminalize(
            self, run_id: str, *, stage_id: str,
            expected_head_fingerprint: str, expected_revision: int,
            operation_id: str,
            outcome: str, actor: str, terminalized_at: str,
            reason_code: str | None = None, reason: str | None = None,
            completed_deliverables: Iterable[str] = (),
            completion_evidence: Iterable[Mapping[str, object]] = (),
            handoff_manifest: Mapping[str, object] | None = None,
            ) -> dict[str, object]:
        """Terminalize exactly one expected head and optionally bind a handoff."""
        run = _path_id(run_id, "run id")
        stage = _path_id(stage_id, "stage id")
        expected = _fingerprint(
            expected_head_fingerprint, "expected stage fingerprint")
        operation = _identifier(operation_id, "stage operation id")
        completed = list(completed_deliverables)
        evidence = list(completion_evidence)
        handoff_copy = (copy.deepcopy(dict(handoff_manifest))
                        if handoff_manifest is not None else None)
        request = request_fingerprint({
            "operation": "terminalize",
            "operation_id": operation,
            "run_id": run,
            "stage_id": stage,
            "expected_head_fingerprint": expected,
            "outcome": outcome,
            "actor": actor,
            "terminalized_at": terminalized_at,
            "reason_code": reason_code,
            "reason": reason,
            "completed_deliverables": completed,
            "completion_evidence": evidence,
            "handoff_fingerprint": (
                handoff_copy.get("fingerprint") if handoff_copy else None),
        })

        def load(current: Mapping[str, object]) -> JsonObject:
            heads = current.get("stage_heads")
            if not isinstance(heads, Mapping) or stage not in heads:
                raise StageLifecycleError("stage is not indexed")
            return _read_indexed_stage(
                self.store, run, stage, heads[stage],
                expected_fingerprint=expected)

        def authority_check(current: dict[str, object]) -> None:
            active = load(current)
            self._check_authority(
                active["authority"], current, active)

        def mutate(current: dict[str, object]) -> dict[str, object]:
            active = load(current)
            terminal = terminalize_stage(
                active, outcome=outcome, actor=actor,
                terminalized_at=terminalized_at, reason_code=reason_code,
                reason=reason, completed_deliverables=completed,
                completion_evidence=evidence)
            if outcome == "done":
                for reference in terminal["terminal"]["completion_evidence"]:
                    self._verify_artifact(reference)
            handoff_ref: dict[str, object] | None = None
            if handoff_copy is not None:
                checked_handoff = self._verify_handoff(
                    handoff_copy, producer=terminal)
                artifact_store = self._artifact_store()
                native = stage_handoff.store_manifest(
                    artifact_store, checked_handoff)
                handoff_ref = review_evidence.portable_artifact_reference(
                    artifact_store, native)
            heads = copy.deepcopy(current["stage_heads"])
            heads[stage] = _stage_head(self.store, run, terminal)
            old_projection = current.get("active_stage_projection")
            foreground = (old_projection.get("foreground_stage_id")
                          if isinstance(old_projection, Mapping) else None)
            projection = active_stage_projection(heads, foreground)
            result = {
                "head": heads[stage],
                "handoff": handoff_ref,
                "active_stage_projection": projection,
            }
            return {
                "changes": {
                    "stage_heads": heads,
                    "lineage": copy.deepcopy(current.get("lineage") or []),
                    "active_stage_projection": projection,
                },
                "receipt": {
                    "operation": "terminalize",
                    "stage_ids": [stage],
                    "result": result,
                },
            }

        return self.store.commit_stage_operation(
            run, expected_revision=expected_revision,
            operation_id=operation, request_fingerprint=request,
            mutate=mutate, validate_authority=authority_check)

    def terminalize_and_start(
            self, predecessor_stage_id: str,
            successor_stage: Mapping[str, object], *,
            expected_head_fingerprint: str, expected_revision: int,
            operation_id: str, outcome: str, actor: str,
            terminalized_at: str, reason_code: str | None = None,
            reason: str | None = None,
            completed_deliverables: Iterable[str] = (),
            completion_evidence: Iterable[Mapping[str, object]] = (),
            foreground: bool = True) -> dict[str, object]:
        """Terminalize one predecessor and index its successor in one commit."""
        successor = validate_stage(successor_stage)
        run = str(successor["run_id"])
        predecessor_id = _path_id(
            predecessor_stage_id, "predecessor stage id")
        successor_id = str(successor["stage_id"])
        if successor["state"] != "active" or \
                successor["predecessor_stage_ids"] != [predecessor_id]:
            raise StageLifecycleError(
                "successor must be active with exactly one predecessor")
        expected = _fingerprint(
            expected_head_fingerprint, "expected stage fingerprint")
        operation = _identifier(operation_id, "stage operation id")
        if not isinstance(foreground, bool):
            raise StageValidationError("foreground selection must be boolean")
        completed = list(completed_deliverables)
        evidence = list(completion_evidence)
        request = request_fingerprint({
            "operation": "terminalize_and_start",
            "operation_id": operation,
            "run_id": run,
            "predecessor_stage_id": predecessor_id,
            "expected_head_fingerprint": expected,
            "successor_fingerprint": successor["fingerprint"],
            "outcome": outcome,
            "actor": actor,
            "terminalized_at": terminalized_at,
            "reason_code": reason_code,
            "reason": reason,
            "completed_deliverables": completed,
            "completion_evidence": evidence,
            "foreground": foreground,
        })

        def load(current: Mapping[str, object]) -> JsonObject:
            heads = current.get("stage_heads")
            if not isinstance(heads, Mapping) or predecessor_id not in heads:
                raise StageLifecycleError("predecessor is not indexed")
            if successor_id in heads:
                raise StageLifecycleError("successor is already indexed")
            return _read_indexed_stage(
                self.store, run, predecessor_id, heads[predecessor_id],
                expected_fingerprint=expected)

        def authority_check(current: dict[str, object]) -> None:
            predecessor = load(current)
            self._check_authority(
                predecessor["authority"], current, predecessor)
            self._check_authority(
                successor["authority"], current, successor)

        def mutate(current: dict[str, object]) -> dict[str, object]:
            predecessor = load(current)
            terminal = terminalize_stage(
                predecessor, outcome=outcome, actor=actor,
                terminalized_at=terminalized_at, reason_code=reason_code,
                reason=reason, completed_deliverables=completed,
                completion_evidence=evidence)
            if outcome == "done":
                for reference in terminal["terminal"]["completion_evidence"]:
                    self._verify_artifact(reference)
            self._read_handoff(
                successor["input_manifest_ref"], producer=terminal,
                consumer=successor)
            heads = copy.deepcopy(current["stage_heads"])
            for existing_id, head in heads.items():
                summary = head.get("summary") \
                    if isinstance(head, Mapping) else None
                if existing_id != predecessor_id and \
                        isinstance(summary, Mapping) and \
                        summary.get("execution_root_id") == \
                        successor["execution_root_id"]:
                    raise StageLifecycleError(
                        "successor execution root is already owned")
            self._claim_execution_root(successor)
            predecessor_head = _stage_head(self.store, run, terminal)
            successor_head = _stage_head(self.store, run, successor)
            heads[predecessor_id] = predecessor_head
            heads[successor_id] = successor_head
            lineage = copy.deepcopy(current.get("lineage") or [])
            if not isinstance(lineage, list):
                raise StageLifecycleError("stage lineage is invalid")
            lineage_row = validate_lineage(_lineage_row(
                parent_stage_id=None,
                child_stage_id=successor_id,
                input_manifest_ref=successor["input_manifest_ref"],
                operation_id=operation,
                predecessor_stage_ids=[predecessor_id]))
            lineage.append(lineage_row)
            old_projection = current.get("active_stage_projection")
            old_foreground = (old_projection.get("foreground_stage_id")
                              if isinstance(old_projection, Mapping) else None)
            projection = active_stage_projection(
                heads, successor_id if foreground else old_foreground)
            result = {
                "predecessor_head": predecessor_head,
                "successor_head": successor_head,
                "lineage": [lineage_row],
                "active_stage_projection": projection,
            }
            return {
                "changes": {
                    "stage_heads": heads,
                    "lineage": lineage,
                    "active_stage_projection": projection,
                },
                "receipt": {
                    "operation": "terminalize_and_start",
                    "stage_ids": sorted([predecessor_id, successor_id]),
                    "result": result,
                },
            }

        return self.store.commit_stage_operation(
            run, expected_revision=expected_revision,
            operation_id=operation, request_fingerprint=request,
            mutate=mutate, validate_authority=authority_check)

    def split_stage(
            self, run_id: str, *, stage_id: str,
            expected_head_fingerprint: str, expected_revision: int,
            operation_id: str,
            child_specs: Iterable[Mapping[str, object]], actor: str,
            terminalized_at: str, reason: str) -> dict[str, object]:
        """Atomically replace one active parent with deterministic children."""
        run = _path_id(run_id, "run id")
        stage = _path_id(stage_id, "stage id")
        expected = _fingerprint(
            expected_head_fingerprint, "expected stage fingerprint")
        operation = _identifier(operation_id, "stage operation id")
        specs = copy.deepcopy(list(child_specs))
        request = request_fingerprint({
            "operation": "split_stage",
            "operation_id": operation,
            "run_id": run,
            "stage_id": stage,
            "expected_head_fingerprint": expected,
            "child_specs": specs,
            "actor": actor,
            "terminalized_at": terminalized_at,
            "reason": reason,
        })

        def load(current: Mapping[str, object]) -> JsonObject:
            heads = current.get("stage_heads")
            if not isinstance(heads, Mapping) or stage not in heads:
                raise StageLifecycleError("split parent is not indexed")
            return _read_indexed_stage(
                self.store, run, stage, heads[stage],
                expected_fingerprint=expected)

        def authority_check(current: dict[str, object]) -> None:
            parent = load(current)
            self._check_authority(
                parent["authority"], current, parent)

        def mutate(current: dict[str, object]) -> dict[str, object]:
            parent = load(current)
            split = create_split(
                parent, operation_id=operation, child_specs=specs,
                actor=actor, terminalized_at=terminalized_at, reason=reason)
            heads = copy.deepcopy(current["stage_heads"])
            existing_roots = {
                str(head.get("summary", {}).get("execution_root_id"))
                for head in heads.values() if isinstance(head, Mapping) and
                isinstance(head.get("summary"), Mapping)
            }
            # Every child has a separately authorized parent-produced handoff;
            # the parent's predecessor input is never inherited implicitly.
            for child in split["children"]:
                self._read_handoff(
                    child["input_manifest_ref"], producer=split["parent"],
                    consumer=child)
            self._claim_execution_root(split["parent"])
            parent_head = _stage_head(self.store, run, split["parent"])
            child_heads: dict[str, dict[str, object]] = {}
            for child in split["children"]:
                child_id = str(child["stage_id"])
                if child_id in heads:
                    raise SplitValidationError("split child id already exists")
                if str(child["execution_root_id"]) in existing_roots:
                    raise SplitValidationError(
                        "split child execution root already exists")
                existing_roots.add(str(child["execution_root_id"]))
                self._claim_execution_root(child)
                child_heads[child_id] = _stage_head(self.store, run, child)
            heads[stage] = parent_head
            heads.update(child_heads)
            lineage = copy.deepcopy(current.get("lineage") or [])
            if not isinstance(lineage, list):
                raise StageLifecycleError("stage lineage is invalid")
            known_lineage = {
                str(row.get("fingerprint")) for row in lineage
                if isinstance(row, Mapping)
            }
            for row in split["lineage"]:
                if str(row["fingerprint"]) in known_lineage:
                    raise SplitValidationError("split lineage already exists")
                lineage.append(row)
            old_projection = current.get("active_stage_projection")
            old_foreground = (old_projection.get("foreground_stage_id")
                              if isinstance(old_projection, Mapping) else None)
            projection = active_stage_projection(
                heads, None if old_foreground == stage else old_foreground)
            result = {
                "parent_head": parent_head,
                "child_heads": child_heads,
                "lineage": copy.deepcopy(split["lineage"]),
                "active_stage_projection": projection,
            }
            return {
                "changes": {
                    "stage_heads": heads,
                    "lineage": lineage,
                    "active_stage_projection": projection,
                },
                "receipt": {
                    "operation": "split_stage",
                    "stage_ids": sorted([stage, *child_heads]),
                    "result": result,
                },
            }

        return self.store.commit_stage_operation(
            run, expected_revision=expected_revision,
            operation_id=operation, request_fingerprint=request,
            mutate=mutate, validate_authority=authority_check)

    def resume_stage(
            self, run_id: str, *, stage_id: str,
            expected_head_fingerprint: str, expected_revision: int,
            operation_id: str,
            attempt_id: str | None = None) -> dict[str, object]:
        """Record a fresh attempt under the same immutable active-stage root."""
        run = _path_id(run_id, "run id")
        stage = _path_id(stage_id, "stage id")
        expected = _fingerprint(
            expected_head_fingerprint, "expected stage fingerprint")
        operation = _identifier(operation_id, "stage operation id")
        attempt_material = {
            "run_id": run,
            "stage_id": stage,
            "operation_id": operation,
        }
        attempt = (_path_id(attempt_id, "stage attempt id")
                   if attempt_id is not None else
                   f"attempt-{request_fingerprint(attempt_material)[:24]}")
        request = request_fingerprint({
            "operation": "resume_stage", "operation_id": operation,
            "run_id": run, "stage_id": stage,
            "expected_head_fingerprint": expected,
            "attempt_id": attempt,
        })

        def load(current: Mapping[str, object]) -> JsonObject:
            heads = current.get("stage_heads")
            if not isinstance(heads, Mapping) or stage not in heads:
                raise StageLifecycleError("stage is not indexed")
            return _read_indexed_stage(
                self.store, run, stage, heads[stage],
                expected_fingerprint=expected)

        def authority_check(current: dict[str, object]) -> None:
            active = load(current)
            self._check_authority(
                active["authority"], current, active)

        def mutate(current: dict[str, object]) -> dict[str, object]:
            active = load(current)
            if active["state"] != "active":
                raise StageLifecycleError("a terminal stage cannot resume")
            claim = dict(self._claim_execution_root(
                active, attempt_id=attempt))
            claim.pop("root", None)
            return {
                "changes": {
                    "stage_heads": copy.deepcopy(current["stage_heads"]),
                    "lineage": copy.deepcopy(current.get("lineage") or []),
                    "active_stage_projection": copy.deepcopy(
                        current["active_stage_projection"]),
                },
                "receipt": {
                    "operation": "resume_stage",
                    "stage_ids": [stage],
                    "result": {
                        "stage_id": stage,
                        "attempt_id": attempt,
                        "execution_root_id": active["execution_root_id"],
                        "claim": claim,
                        "stage_fingerprint": active["fingerprint"],
                    },
                },
            }

        return self.store.commit_stage_operation(
            run, expected_revision=expected_revision,
            operation_id=operation, request_fingerprint=request,
            mutate=mutate, validate_authority=authority_check)

    def rebuild_active_projection(
            self, run_id: str, *, expected_revision: int,
            foreground_stage_id: str | None = None,
            operation_id: str | None = None) -> dict[str, object]:
        """Delegate the locked repair of the replaceable projection cache."""
        repair = getattr(self.store, "rebuild_active_stage_projection", None)
        if not callable(repair):
            raise StageLifecycleError(
                "run store cannot rebuild active stage projection")
        return repair(
            run_id, expected_revision=expected_revision,
            foreground_stage_id=foreground_stage_id,
            operation_id=operation_id)
