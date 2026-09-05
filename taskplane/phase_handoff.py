"""Closed repository-native phase handoffs.

This module owns only portable repository truth.  It deliberately has no
dependency on the Taskplane home, run store, workspace locators, attempts, or
leases.  A handoff can therefore be validated from a clean checkout alone.
"""
from __future__ import annotations

from collections.abc import Mapping
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
from typing import cast, Final, TypeAlias


SCHEMA: Final[str] = "taskplane.stage-handoff/v2"
ARTIFACT_REFERENCE_SCHEMA: Final[str] = \
    "taskplane.repository-artifact-reference/v1"
HUMAN_GATE_RECEIPT_SCHEMA: Final[str] = "taskplane.human-gate-receipt/v1"
PROGRESS_RECEIPT_SCHEMA: Final[str] = "taskplane.phase-progress-receipt/v1"
PUBLICATION_RECEIPT_SCHEMA: Final[str] = \
    "taskplane.phase-handoff-publication/v1"
MAX_MANIFEST_BYTES: Final[int] = 64 * 1024
MAX_ARTIFACT_REFERENCES: Final[int] = 64
ARTIFACT_DIRECTORY: Final[str] = "exports/pickup/artifacts/sha256"
PHASE_DIRECTORY: Final[str] = "exports/pickup/phases"
RECEIPT_DIRECTORY: Final[str] = "exports/pickup/phase-receipts"
REQUIRED_EXCLUSIONS: Final[frozenset[str]] = frozenset({
    "claims",
    "host-paths",
    "predecessor-conversations",
    "predecessor-event-logs",
    "predecessor-leases",
    "predecessor-runtime-state",
    "predecessor-tool-transcripts",
    "run-store",
    "secrets",
    "undeclared-artifacts",
    "workspace-locator",
})

JsonObject: TypeAlias = dict[str, object]
_DIGEST: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_ID: Final[re.Pattern[str]] = re.compile(
    r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_IDENTIFIER: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_REPOSITORY_ID: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,511}$")
_CONTRACT_ID: Final[re.Pattern[str]] = re.compile(
    r"^contract:[a-z][a-z0-9-]{0,127}$")
_MEDIA_TYPE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+/-]{0,126}$")

_MANIFEST_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "handoff_id", "repository", "source", "requirement",
    "design", "plan", "producer", "successor", "obligations",
    "progress", "tasks", "contracts", "acceptance",
    "selected_artifacts", "authority_receipts", "progress_receipts",
    "lineage", "exclusions", "fingerprint",
})
_ARTIFACT_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "kind", "digest", "bytes", "media_type", "destination",
    "locator",
})
_AUTHORITY_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "gate", "actor", "context", "subject_fingerprint",
    "repository_id", "source_commit", "source_tree", "decision",
    "predecessor_authority_fingerprint",
    "cryptographic_authenticity_claimed", "fingerprint",
})
_PROGRESS_RECEIPT_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "producer", "sequence", "phase", "obligation_id",
    "task_id", "status", "predecessor_receipt_fingerprint",
    "checkpoint_receipt_digest", "integration_receipt_fingerprint",
    "fingerprint",
})
_REPOSITORY_FIELDS: Final[frozenset[str]] = frozenset({"id"})
_SOURCE_FIELDS: Final[frozenset[str]] = frozenset({"commit", "tree"})
_SUBJECT_FIELDS: Final[frozenset[str]] = frozenset({
    "id", "fingerprint", "artifact",
})
_PHASE_FINGERPRINT_FIELDS: Final[frozenset[str]] = frozenset({
    "fingerprint", "artifact",
})
_PRODUCER_FIELDS: Final[frozenset[str]] = frozenset({"phase", "outcome"})
_SUCCESSOR_FIELDS: Final[frozenset[str]] = frozenset({"phase", "mode"})
_OBLIGATION_FIELDS: Final[frozenset[str]] = frozenset({
    "id", "ordinal", "contracts", "acceptance", "proofs",
})
_PROGRESS_FIELDS: Final[frozenset[str]] = frozenset({
    "completed", "remaining",
})
_TASK_FIELDS: Final[frozenset[str]] = frozenset({
    "id", "ordinal", "scope", "dependencies", "contracts", "acceptance",
    "proofs",
})
_CONTRACT_FIELDS: Final[frozenset[str]] = frozenset({"id", "relation"})
_ACCEPTANCE_FIELDS: Final[frozenset[str]] = frozenset({
    "id", "ordinal", "criterion", "proofs",
})
_LINEAGE_FIELDS: Final[frozenset[str]] = frozenset({
    "predecessor_handoff_fingerprint", "predecessor_receipt_head",
})
_PHASES: Final[frozenset[str]] = frozenset({
    "requirement", "design", "plan", "build", "terminal",
})
_CONTRACT_RELATIONS: Final[tuple[str, ...]] = \
    ("provides", "consumes", "changes")
_GATE_ORDER: Final[tuple[str, ...]] = \
    ("initial-authorization", "design-approval", "plan-approval")


class PhaseHandoffError(ValueError):
    """A stable fail-closed phase-handoff refusal."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class HandoffMalformedError(PhaseHandoffError):
    def __init__(self, detail: str):
        super().__init__("handoff-malformed", detail)


class HandoffIntegrityError(PhaseHandoffError):
    def __init__(self, detail: str):
        super().__init__("handoff-integrity", detail)


class ArtifactIntegrityError(PhaseHandoffError):
    def __init__(self, detail: str):
        super().__init__("artifact-integrity", detail)


class PublicationConflictError(PhaseHandoffError):
    def __init__(self, detail: str):
        super().__init__("publication-conflict", detail)


def canonical_bytes(value: object) -> bytes:
    """Return the one canonical byte representation used by every identity."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HandoffMalformedError("value is not canonical JSON") from exc


def canonical_fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _without_fingerprint(value: Mapping[str, object]) -> JsonObject:
    return {str(key): item for key, item in value.items()
            if str(key) != "fingerprint"}


def manifest_fingerprint(manifest: Mapping[str, object]) -> str:
    return canonical_fingerprint(_without_fingerprint(manifest))


def receipt_fingerprint(receipt: Mapping[str, object]) -> str:
    return canonical_fingerprint(_without_fingerprint(receipt))


def _closed(value: object, fields: frozenset[str], label: str) \
        -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str)
                                             for key in value):
        raise HandoffMalformedError(f"{label} must be an object")
    keys = set(value)
    unknown = keys - fields
    missing = fields - keys
    if unknown:
        raise HandoffMalformedError(
            f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise HandoffMalformedError(
            f"{label} has missing fields: {', '.join(sorted(missing))}")
    return value


def _text(value: object, label: str, *, maximum: int = 4096,
          pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or \
            len(value.encode("utf-8")) > maximum or any(
                ord(character) < 32 or ord(character) == 127
                for character in value):
        raise HandoffMalformedError(f"{label} is invalid")
    if pattern is not None and not pattern.fullmatch(value):
        raise HandoffMalformedError(f"{label} is invalid")
    return value


def _optional_digest(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum=64, pattern=_DIGEST)


def _positive_int(value: object, label: str, *, allow_zero: bool = False) -> int:
    floor = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < floor:
        raise HandoffMalformedError(f"{label} is invalid")
    return value


def _string_list(value: object, label: str, *, allow_empty: bool = True,
                 pattern: re.Pattern[str] | None = None,
                 ordered_by: Mapping[str, int] | None = None) -> list[str]:
    if not isinstance(value, list):
        raise HandoffMalformedError(f"{label} must be a list")
    result = [_text(item, f"{label} entry", maximum=4096, pattern=pattern)
              for item in value]
    if not allow_empty and not result:
        raise HandoffMalformedError(f"{label} must not be empty")
    if len(set(result)) != len(result):
        raise HandoffMalformedError(f"{label} contains duplicate entries")
    if ordered_by is None:
        canonical = sorted(result)
    else:
        if any(item not in ordered_by for item in result):
            raise HandoffMalformedError(f"{label} contains an unknown entry")
        canonical = sorted(result, key=ordered_by.__getitem__)
    if result != canonical:
        raise HandoffMalformedError(f"{label} is not in canonical order")
    return result


def _repository_relative(value: object, label: str) -> str:
    text = _text(value, label, maximum=1024)
    if "\\" in text:
        raise ArtifactIntegrityError(f"{label} is not a safe repository path")
    path = PurePosixPath(text)
    if path.is_absolute() or text != path.as_posix() or \
            any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactIntegrityError(f"{label} is not a safe repository path")
    return text


def artifact_destination(digest: str) -> str:
    checked = _text(digest, "artifact digest", maximum=64, pattern=_DIGEST)
    return f"{ARTIFACT_DIRECTORY}/{checked}"


def handoff_path(handoff_id: str) -> str:
    checked = _text(handoff_id, "handoff id", maximum=64, pattern=_DIGEST)
    return f"{PHASE_DIRECTORY}/{checked}/handoff.json"


def progress_receipt_path(handoff_id: str, receipt: Mapping[str, object]) -> str:
    checked_id = _text(handoff_id, "handoff id", maximum=64, pattern=_DIGEST)
    checked_receipt = _validate_progress_receipt(receipt)
    return (
        f"{RECEIPT_DIRECTORY}/{checked_id}/"
        f"{checked_receipt['sequence']}-{checked_receipt['fingerprint']}.json"
    )


def _git(root: str, *args: str, code: str = "source-stale") -> str:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PhaseHandoffError(code, "repository identity is unavailable") \
            from exc
    if completed.returncode:
        raise PhaseHandoffError(code, "repository identity is unavailable")
    return (completed.stdout or "").strip()


def _repository_root(repository_root: str | os.PathLike[str]) -> str:
    root = os.path.realpath(os.path.abspath(os.fspath(repository_root)))
    if not os.path.isdir(root):
        raise ArtifactIntegrityError("repository root is unavailable")
    observed = _git(root, "rev-parse", "--show-toplevel",
                    code="repository-foreign")
    if os.path.realpath(observed) != root:
        raise PhaseHandoffError(
            "repository-foreign", "repository root identity does not match")
    return root


def repository_identity(repository_root: str | os.PathLike[str]) -> str:
    """Resolve the same hosted repository identity used by fresh clones.

    Hosted remotes are normalized to ``host/owner/name``.  A repository with
    no hosted origin receives an explicit Git-local identity; callers that
    move such a repository must preserve or supply that identity themselves.
    """
    root = _repository_root(repository_root)
    remote = _git(root, "remote", "get-url", "origin",
                  code="repository-foreign") if _git(
                      root, "remote", code="repository-foreign") else ""
    if remote:
        # Accept the ordinary HTTPS/SSH/SCP remote forms without retaining
        # credentials, schemes, or host-local checkout paths.
        value = remote.strip().removesuffix(".git").rstrip("/")
        if "://" in value:
            from urllib.parse import urlsplit
            parsed = urlsplit(value)
            host = (parsed.hostname or "").lower()
            parts = [part.lower() for part in parsed.path.split("/") if part]
        elif ":" in value and "@" in value.split(":", 1)[0]:
            host_part, path_part = value.split(":", 1)
            host = host_part.rsplit("@", 1)[-1].lower()
            parts = [part.lower() for part in path_part.split("/") if part]
        else:
            host, parts = "", []
        if host and len(parts) == 2:
            return f"{host}/{parts[0]}/{parts[1]}"
    roots = sorted(filter(None, _git(
        root, "rev-list", "--max-parents=0", "HEAD",
        code="repository-foreign").splitlines()))
    if not roots or any(not _OBJECT_ID.fullmatch(item) for item in roots):
        raise PhaseHandoffError(
            "repository-foreign", "local repository identity is unavailable")
    return "local-git:" + canonical_fingerprint(roots)


def _safe_regular_file(root: str, relative: str, *, code: str) -> tuple[str, bytes]:
    rel = _repository_relative(relative, "artifact destination")
    candidate = os.path.join(root, *PurePosixPath(rel).parts)
    try:
        mode = os.lstat(candidate).st_mode
        resolved = os.path.realpath(candidate)
    except OSError as exc:
        raise PhaseHandoffError(code, "repository artifact is missing") from exc
    if not stat.S_ISREG(mode) or resolved != os.path.abspath(candidate) or \
            os.path.commonpath((root, resolved)) != root:
        raise PhaseHandoffError(code, "repository artifact path is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
        with os.fdopen(descriptor, "rb") as source:
            data = source.read()
    except OSError as exc:
        raise PhaseHandoffError(code, "repository artifact cannot be read") \
            from exc
    return rel, data


def _validate_artifact_shape(reference: object) -> JsonObject:
    row = _closed(reference, _ARTIFACT_FIELDS, "repository artifact reference")
    if row.get("schema") != ARTIFACT_REFERENCE_SCHEMA:
        raise HandoffMalformedError("repository artifact schema is unsupported")
    kind = _text(row.get("kind"), "artifact kind", maximum=128,
                 pattern=_IDENTIFIER)
    digest = _text(row.get("digest"), "artifact digest", maximum=64,
                   pattern=_DIGEST)
    byte_count = _positive_int(row.get("bytes"), "artifact byte count",
                               allow_zero=True)
    media_type = _text(row.get("media_type"), "artifact media type",
                       maximum=127, pattern=_MEDIA_TYPE)
    destination = _repository_relative(
        row.get("destination"), "artifact destination")
    if destination != artifact_destination(digest):
        raise ArtifactIntegrityError("artifact destination is not digest-addressed")
    if row.get("locator") != f"repo-artifact://sha256/{digest}":
        raise ArtifactIntegrityError("artifact locator does not match digest")
    return {
        "schema": ARTIFACT_REFERENCE_SCHEMA,
        "kind": kind,
        "digest": digest,
        "bytes": byte_count,
        "media_type": media_type,
        "destination": destination,
        "locator": row["locator"],
    }


def validate_repository_artifact_reference(
        repository_root: str | os.PathLike[str], reference: object, *,
        require_tracked: bool = True) -> JsonObject:
    """Verify a closed digest reference without following links or locators."""
    root = _repository_root(repository_root)
    row = _validate_artifact_shape(reference)
    relative, data = _safe_regular_file(
        root, str(row["destination"]), code="artifact-integrity")
    if require_tracked:
        _git(root, "ls-files", "--error-unmatch", "--", relative,
             code="artifact-integrity")
    if len(data) != row["bytes"]:
        raise ArtifactIntegrityError("artifact byte count does not match")
    if hashlib.sha256(data).hexdigest() != row["digest"]:
        raise ArtifactIntegrityError("artifact digest does not match")
    return row


def _ensure_safe_parents(root: str, destination: str) -> Path:
    path = Path(root).joinpath(*PurePosixPath(destination).parts)
    current = Path(root)
    for part in PurePosixPath(destination).parts[:-1]:
        current = current / part
        if current.exists() and (current.is_symlink() or not current.is_dir()):
            raise PublicationConflictError("publication parent is unsafe")
        current.mkdir(mode=0o755, exist_ok=True)
        if current.resolve() != current.absolute():
            raise PublicationConflictError("publication parent is unsafe")
    return path


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_if_absent(root: str, destination: str, data: bytes) -> bool:
    """Publish bytes once; return True for an exact existing replay."""
    path = _ensure_safe_parents(root, destination)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        os.chmod(temporary, 0o644)
        with os.fdopen(descriptor, "wb") as target:
            descriptor = -1
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            _, existing = _safe_regular_file(
                root, destination, code="publication-conflict")
            if existing != data:
                raise PublicationConflictError(
                    "different bytes already exist at this identity")
            return True
        _fsync_directory(path.parent)
        return False
    except PhaseHandoffError:
        raise
    except OSError as exc:
        raise PublicationConflictError("atomic publication failed closed") \
            from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def create_repository_artifact_reference(
        repository_root: str | os.PathLike[str], source: str, *, kind: str,
        media_type: str = "application/json", publish: bool = True) \
        -> JsonObject:
    """Content-address one tracked repository file and optionally publish it."""
    root = _repository_root(repository_root)
    source_rel = _repository_relative(source, "artifact source")
    source_rel, data = _safe_regular_file(
        root, source_rel, code="artifact-integrity")
    _git(root, "ls-files", "--error-unmatch", "--", source_rel,
         code="artifact-integrity")
    digest = hashlib.sha256(data).hexdigest()
    reference: JsonObject = {
        "schema": ARTIFACT_REFERENCE_SCHEMA,
        "kind": _text(kind, "artifact kind", maximum=128,
                      pattern=_IDENTIFIER),
        "digest": digest,
        "bytes": len(data),
        "media_type": _text(media_type, "artifact media type", maximum=127,
                            pattern=_MEDIA_TYPE),
        "destination": artifact_destination(digest),
        "locator": f"repo-artifact://sha256/{digest}",
    }
    checked = _validate_artifact_shape(reference)
    if publish:
        _create_if_absent(root, str(checked["destination"]), data)
        validate_repository_artifact_reference(
            root, checked, require_tracked=False)
    return checked


# Short, explicit aliases used by producer adapters.
create_artifact_reference = create_repository_artifact_reference
validate_artifact_reference = validate_repository_artifact_reference


def create_human_gate_receipt(*, gate: str, actor: str, context: str,
                              subject_fingerprint: str, repository_id: str,
                              source_commit: str, source_tree: str,
                              decision: str = "approved",
                              predecessor_authority_fingerprint: str | None = None,
                              cryptographic_authenticity_claimed: bool = False) \
        -> JsonObject:
    material: JsonObject = {
        "schema": HUMAN_GATE_RECEIPT_SCHEMA,
        "gate": gate,
        "actor": actor,
        "context": context,
        "subject_fingerprint": subject_fingerprint,
        "repository_id": repository_id,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "decision": decision,
        "predecessor_authority_fingerprint":
            predecessor_authority_fingerprint,
        "cryptographic_authenticity_claimed":
            cryptographic_authenticity_claimed,
    }
    material["fingerprint"] = receipt_fingerprint(material)
    return _validate_authority_receipt(material)


def validate_human_gate_actor(value: object) -> str:
    """Require a named human, never a mechanical or synthetic identity."""
    actor = _text(value, "authority actor", maximum=256)
    normalized = actor.lower().replace("_", "-")
    if (not actor.startswith("human:") or actor == "human:"
            or normalized in {
                "human:(unattributed)",
                "human:mechanical-definition-gate",
                "human:mechanical", "human:engine", "human:synthetic",
            }
            or normalized.startswith("human:synthetic-")
            or normalized.startswith("human:mechanical-")):
        raise PhaseHandoffError(
            "authority-missing", "gate actor is not an attributable human")
    return actor


def _validate_authority_receipt(value: object) -> JsonObject:
    row = _closed(value, _AUTHORITY_FIELDS, "human authority receipt")
    if row.get("schema") != HUMAN_GATE_RECEIPT_SCHEMA:
        raise HandoffMalformedError("human authority receipt schema is unsupported")
    gate = _text(row.get("gate"), "authority gate", maximum=64)
    if gate not in _GATE_ORDER:
        raise HandoffMalformedError("authority gate is invalid")
    actor = validate_human_gate_actor(row.get("actor"))
    context = _text(row.get("context"), "authority context", maximum=1024)
    subject = _text(row.get("subject_fingerprint"), "authority subject",
                    maximum=64, pattern=_DIGEST)
    repository_id = _text(row.get("repository_id"), "authority repository",
                          maximum=512, pattern=_REPOSITORY_ID)
    source_commit = _text(row.get("source_commit"), "authority source commit",
                          maximum=64, pattern=_OBJECT_ID)
    source_tree = _text(row.get("source_tree"), "authority source tree",
                        maximum=64, pattern=_OBJECT_ID)
    if row.get("decision") != "approved":
        raise PhaseHandoffError("authority-missing", "gate decision is not approved")
    predecessor = _optional_digest(
        row.get("predecessor_authority_fingerprint"),
        "predecessor authority fingerprint")
    if row.get("cryptographic_authenticity_claimed") is not False:
        raise PhaseHandoffError(
            "authority-stale", "unverifiable cryptographic authority was claimed")
    expected = receipt_fingerprint(row)
    if row.get("fingerprint") != expected:
        raise HandoffIntegrityError("human authority fingerprint mismatch")
    return {
        "schema": HUMAN_GATE_RECEIPT_SCHEMA, "gate": gate, "actor": actor,
        "context": context, "subject_fingerprint": subject,
        "repository_id": repository_id, "source_commit": source_commit,
        "source_tree": source_tree, "decision": "approved",
        "predecessor_authority_fingerprint": predecessor,
        "cryptographic_authenticity_claimed": False,
        "fingerprint": expected,
    }


def create_progress_receipt(*, producer: str, sequence: int, phase: str,
                            obligation_id: str, task_id: str | None,
                            status: str,
                            predecessor_receipt_fingerprint: str | None,
                            checkpoint_receipt_digest: str | None = None,
                            integration_receipt_fingerprint: str | None = None) \
        -> JsonObject:
    material: JsonObject = {
        "schema": PROGRESS_RECEIPT_SCHEMA,
        "producer": producer,
        "sequence": sequence,
        "phase": phase,
        "obligation_id": obligation_id,
        "task_id": task_id,
        "status": status,
        "predecessor_receipt_fingerprint":
            predecessor_receipt_fingerprint,
        "checkpoint_receipt_digest": checkpoint_receipt_digest,
        "integration_receipt_fingerprint": integration_receipt_fingerprint,
    }
    material["fingerprint"] = receipt_fingerprint(material)
    return _validate_progress_receipt(material)


def publish_progress_receipt(
        repository_root: str | os.PathLike[str], handoff_id: str,
        receipt: Mapping[str, object]) -> JsonObject:
    """Publish one immutable predecessor-linked receipt at its designed path."""
    root = _repository_root(repository_root)
    checked = _validate_progress_receipt(receipt)
    destination = progress_receipt_path(handoff_id, checked)
    replayed = _create_if_absent(root, destination, canonical_bytes(checked))
    result: JsonObject = {
        "schema": "taskplane.phase-progress-publication/v1",
        "status": "replayed" if replayed else "published",
        "handoff_id": handoff_id,
        "receipt_fingerprint": checked["fingerprint"],
        "sequence": checked["sequence"],
    }
    result["fingerprint"] = receipt_fingerprint(result)
    return result


def _validate_progress_receipt(value: object) -> JsonObject:
    row = _closed(value, _PROGRESS_RECEIPT_FIELDS, "phase progress receipt")
    if row.get("schema") != PROGRESS_RECEIPT_SCHEMA:
        raise HandoffMalformedError("phase progress receipt schema is unsupported")
    producer = _text(row.get("producer"), "progress producer", maximum=256)
    if not producer.startswith("engine:"):
        raise HandoffMalformedError("progress producer must be an engine")
    sequence = _positive_int(row.get("sequence"), "progress sequence")
    phase = _text(row.get("phase"), "progress phase", maximum=32)
    if phase not in {"design", "plan", "build"}:
        raise HandoffMalformedError("progress phase is invalid")
    obligation = _text(row.get("obligation_id"), "progress obligation",
                       maximum=256, pattern=_IDENTIFIER)
    task = row.get("task_id")
    if task is not None:
        task = _text(task, "progress task", maximum=256, pattern=_IDENTIFIER)
    status = _text(row.get("status"), "progress status", maximum=32)
    if status not in {"green", "interrupted"}:
        raise HandoffMalformedError("progress status is invalid")
    predecessor = _optional_digest(
        row.get("predecessor_receipt_fingerprint"),
        "predecessor receipt fingerprint")
    checkpoint = _optional_digest(
        row.get("checkpoint_receipt_digest"), "checkpoint receipt digest")
    integration = _optional_digest(
        row.get("integration_receipt_fingerprint"),
        "integration receipt fingerprint")
    if phase == "build" and status == "green" and \
            (checkpoint is None or integration is None):
        raise HandoffMalformedError(
            "green Build progress requires checkpoint and integration receipts")
    expected = receipt_fingerprint(row)
    if row.get("fingerprint") != expected:
        raise HandoffIntegrityError("phase progress receipt fingerprint mismatch")
    return {
        "schema": PROGRESS_RECEIPT_SCHEMA, "producer": producer,
        "sequence": sequence, "phase": phase, "obligation_id": obligation,
        "task_id": task, "status": status,
        "predecessor_receipt_fingerprint": predecessor,
        "checkpoint_receipt_digest": checkpoint,
        "integration_receipt_fingerprint": integration,
        "fingerprint": expected,
    }


def _validate_subject(value: object, label: str, *, requirement: bool) \
        -> JsonObject:
    fields = _SUBJECT_FIELDS if requirement else _PHASE_FINGERPRINT_FIELDS
    row = _closed(value, fields, label)
    result: JsonObject = {}
    if requirement:
        result["id"] = _text(row.get("id"), f"{label} id", maximum=256,
                             pattern=_IDENTIFIER)
    result["fingerprint"] = _text(
        row.get("fingerprint"), f"{label} fingerprint", maximum=64,
        pattern=_DIGEST)
    result["artifact"] = _validate_artifact_shape(row.get("artifact"))
    return result


def _validate_obligations(value: object) -> list[JsonObject]:
    if not isinstance(value, list) or not value:
        raise HandoffMalformedError("obligations must be a nonempty list")
    result: list[JsonObject] = []
    for expected_ordinal, item in enumerate(value, 1):
        row = _closed(item, _OBLIGATION_FIELDS, "obligation")
        if row.get("ordinal") != expected_ordinal:
            raise HandoffMalformedError("obligations are not in canonical order")
        result.append({
            "id": _text(row.get("id"), "obligation id", maximum=256,
                        pattern=_IDENTIFIER),
            "ordinal": expected_ordinal,
            "contracts": _string_list(
                row.get("contracts"), "obligation contracts",
                pattern=_CONTRACT_ID),
            "acceptance": _string_list(
                row.get("acceptance"), "obligation acceptance"),
            "proofs": _string_list(row.get("proofs"), "obligation proofs"),
        })
    ids = [str(row["id"]) for row in result]
    if len(set(ids)) != len(ids):
        raise HandoffMalformedError("obligations contain duplicate ids")
    return result


def _validate_acceptance(value: object) -> list[JsonObject]:
    if not isinstance(value, list) or not value:
        raise HandoffMalformedError("acceptance must be a nonempty list")
    result: list[JsonObject] = []
    for expected_ordinal, item in enumerate(value, 1):
        row = _closed(item, _ACCEPTANCE_FIELDS, "acceptance entry")
        if row.get("ordinal") != expected_ordinal:
            raise HandoffMalformedError("acceptance is not in canonical order")
        result.append({
            "id": _text(row.get("id"), "acceptance id", maximum=256,
                        pattern=_IDENTIFIER),
            "ordinal": expected_ordinal,
            "criterion": _text(row.get("criterion"), "acceptance criterion",
                               maximum=16384),
            "proofs": _string_list(row.get("proofs"), "acceptance proofs",
                                   allow_empty=False),
        })
    ids = [str(row["id"]) for row in result]
    if len(set(ids)) != len(ids):
        raise HandoffMalformedError("acceptance contains duplicate ids")
    return result


def _validate_contracts(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        raise HandoffMalformedError("contracts must be a list")
    result: list[JsonObject] = []
    for item in value:
        row = _closed(item, _CONTRACT_FIELDS, "contract relation")
        relation = _text(row.get("relation"), "contract relation", maximum=16)
        if relation not in _CONTRACT_RELATIONS:
            raise HandoffMalformedError("contract relation is invalid")
        result.append({
            "id": _text(row.get("id"), "contract id", maximum=137,
                        pattern=_CONTRACT_ID),
            "relation": relation,
        })
    identities = [(str(row["relation"]), str(row["id"])) for row in result]
    if len(set(identities)) != len(identities):
        raise HandoffMalformedError("contracts contain duplicate relations")
    if len({str(row["id"]) for row in result}) != len(result):
        raise HandoffMalformedError("contract ids must have one exact relation")
    order = {name: index for index, name in enumerate(_CONTRACT_RELATIONS)}
    if result != sorted(result, key=lambda row: (
            order[str(row["relation"])], str(row["id"]))):
        raise HandoffMalformedError("contracts are not in canonical order")
    return result


def _validate_tasks(value: object, *, contract_ids: set[str],
                    acceptance_order: Mapping[str, int]) -> list[JsonObject]:
    if not isinstance(value, list):
        raise HandoffMalformedError("tasks must be a list")
    result: list[JsonObject] = []
    ordinal_by_id: dict[str, int] = {}
    for expected_ordinal, item in enumerate(value, 1):
        row = _closed(item, _TASK_FIELDS, "task")
        if row.get("ordinal") != expected_ordinal:
            raise HandoffMalformedError("tasks are not in canonical order")
        task_id = _text(row.get("id"), "task id", maximum=256,
                        pattern=_IDENTIFIER)
        if task_id in ordinal_by_id:
            raise HandoffMalformedError("tasks contain duplicate ids")
        dependencies = _string_list(
            row.get("dependencies"), "task dependencies",
            ordered_by=ordinal_by_id)
        contracts = _string_list(
            row.get("contracts"), "task contracts", pattern=_CONTRACT_ID)
        if any(contract not in contract_ids for contract in contracts):
            raise HandoffMalformedError("task references an unknown contract")
        acceptance = _string_list(
            row.get("acceptance"), "task acceptance",
            ordered_by=acceptance_order)
        scope = row.get("scope")
        if isinstance(scope, list):
            scope = [_repository_relative(path, "task scope")
                     for path in scope]
        result.append({
            "id": task_id, "ordinal": expected_ordinal,
            "scope": _string_list(scope, "task scope", allow_empty=False),
            "dependencies": dependencies,
            "contracts": contracts,
            "acceptance": acceptance,
            "proofs": _string_list(row.get("proofs"), "task proofs",
                                   allow_empty=False),
        })
        ordinal_by_id[task_id] = expected_ordinal
    return result


def _validate_transition(producer: Mapping[str, object],
                         successor: Mapping[str, object]) -> None:
    phase = cast(str, producer["phase"])
    outcome = cast(str, producer["outcome"])
    target = cast(str, successor["phase"])
    mode = cast(str, successor["mode"])
    next_phase = {"requirement": "design", "design": "plan", "plan": "build"}
    if outcome == "done" and phase in next_phase and \
            mode == "next-phase" and target == next_phase[phase]:
        return
    if outcome == "done" and phase == "build" and \
            mode == "terminal-evidence" and target == "terminal":
        return
    if outcome == "interrupted" and phase in {"design", "plan", "build"} and \
            mode == "same-phase-resume" and target == phase:
        return
    raise PhaseHandoffError("transition-invalid", "phase transition is invalid")


def handoff_identity(manifest: Mapping[str, object]) -> str:
    repository = manifest.get("repository")
    source = manifest.get("source")
    requirement = manifest.get("requirement")
    producer = manifest.get("producer")
    successor = manifest.get("successor")
    lineage = manifest.get("lineage")
    if not all(isinstance(value, Mapping) for value in (
            repository, source, requirement, producer, successor, lineage)):
        raise HandoffMalformedError("handoff identity fields are incomplete")
    repository = cast(Mapping[str, object], repository)
    source = cast(Mapping[str, object], source)
    requirement = cast(Mapping[str, object], requirement)
    producer = cast(Mapping[str, object], producer)
    successor = cast(Mapping[str, object], successor)
    lineage = cast(Mapping[str, object], lineage)
    return canonical_fingerprint({
        "repository_id": repository.get("id"),
        "source_commit": source.get("commit"),
        "source_tree": source.get("tree"),
        "requirement_fingerprint": requirement.get("fingerprint"),
        "producer_phase": producer.get("phase"),
        "producer_outcome": producer.get("outcome"),
        "successor_phase": successor.get("phase"),
        "successor_mode": successor.get("mode"),
        "predecessor_handoff_fingerprint":
            lineage.get("predecessor_handoff_fingerprint"),
        "predecessor_receipt_head": lineage.get("predecessor_receipt_head"),
    })


def _required_gates(producer_phase: str) -> list[str]:
    return {
        "requirement": ["initial-authorization"],
        "design": ["initial-authorization", "design-approval"],
        "plan": ["initial-authorization", "design-approval", "plan-approval"],
        "build": ["initial-authorization", "design-approval", "plan-approval"],
    }[producer_phase]


def validate_manifest(manifest: object) -> JsonObject:
    """Validate the complete closed v2 value without reading a repository."""
    row = _closed(manifest, _MANIFEST_FIELDS, "phase handoff")
    if row.get("schema") != SCHEMA:
        raise HandoffMalformedError("unsupported phase handoff schema")
    repository = _closed(row.get("repository"), _REPOSITORY_FIELDS,
                         "repository")
    repository_id = _text(repository.get("id"), "repository id", maximum=512,
                          pattern=_REPOSITORY_ID)
    source = _closed(row.get("source"), _SOURCE_FIELDS, "source")
    source_commit = _text(source.get("commit"), "source commit", maximum=64,
                          pattern=_OBJECT_ID)
    source_tree = _text(source.get("tree"), "source tree", maximum=64,
                        pattern=_OBJECT_ID)
    requirement = _validate_subject(row.get("requirement"), "requirement",
                                    requirement=True)
    design = (None if row.get("design") is None else
              _validate_subject(row.get("design"), "design", requirement=False))
    plan = (None if row.get("plan") is None else
            _validate_subject(row.get("plan"), "plan", requirement=False))
    producer_row = _closed(row.get("producer"), _PRODUCER_FIELDS, "producer")
    producer = {
        "phase": _text(producer_row.get("phase"), "producer phase", maximum=32),
        "outcome": _text(producer_row.get("outcome"), "producer outcome",
                         maximum=32),
    }
    if producer["phase"] not in _PHASES - {"terminal"} or \
            producer["outcome"] not in {"done", "interrupted"}:
        raise HandoffMalformedError("producer phase or outcome is invalid")
    successor_row = _closed(row.get("successor"), _SUCCESSOR_FIELDS, "successor")
    successor = {
        "phase": _text(successor_row.get("phase"), "successor phase", maximum=32),
        "mode": _text(successor_row.get("mode"), "successor mode", maximum=32),
    }
    if successor["phase"] not in _PHASES:
        raise HandoffMalformedError("successor phase is invalid")
    _validate_transition(producer, successor)
    if producer["phase"] in {"design", "plan", "build"} and design is None:
        raise HandoffMalformedError("applicable Design identity is missing")
    if producer["phase"] in {"plan", "build"} and plan is None:
        raise HandoffMalformedError("applicable Plan identity is missing")
    if producer["phase"] == "requirement" and (design is not None or plan is not None):
        raise HandoffMalformedError("inapplicable phase identity is present")
    if producer["phase"] == "design" and plan is not None:
        raise HandoffMalformedError("inapplicable Plan identity is present")

    obligations = _validate_obligations(row.get("obligations"))
    obligation_order = {str(item["id"]): cast(int, item["ordinal"])
                        for item in obligations}
    progress_row = _closed(row.get("progress"), _PROGRESS_FIELDS, "progress")
    completed = _string_list(
        progress_row.get("completed"), "completed obligations",
        ordered_by=obligation_order)
    remaining = _string_list(
        progress_row.get("remaining"), "remaining obligations",
        ordered_by=obligation_order)
    if set(completed).intersection(remaining) or \
            completed + remaining != list(obligation_order):
        raise HandoffMalformedError(
            "completed and remaining obligations are not an exact partition")

    contracts = _validate_contracts(row.get("contracts"))
    contract_ids = {str(item["id"]) for item in contracts}
    acceptance = _validate_acceptance(row.get("acceptance"))
    acceptance_order = {str(item["id"]): cast(int, item["ordinal"])
                        for item in acceptance}
    acceptance_proofs = {
        str(item["id"]): list(cast(list[str], item["proofs"]))
        for item in acceptance
    }
    for obligation in obligations:
        obligation_contracts = cast(list[str], obligation["contracts"])
        obligation_acceptance = cast(list[str], obligation["acceptance"])
        if any(item not in contract_ids for item in obligation_contracts):
            raise HandoffMalformedError("obligation references an unknown contract")
        if any(item not in acceptance_order for item in obligation_acceptance):
            raise HandoffMalformedError("obligation references unknown acceptance")
        expected_proofs = sorted({
            proof
            for acceptance_id in obligation_acceptance
            for proof in acceptance_proofs[str(acceptance_id)]
        })
        if obligation["proofs"] != expected_proofs:
            raise HandoffMalformedError(
                "obligation proofs do not close over acceptance")
    tasks = _validate_tasks(row.get("tasks"), contract_ids=contract_ids,
                            acceptance_order=acceptance_order)
    for task in tasks:
        task_acceptance = cast(list[str], task["acceptance"])
        expected_proofs = sorted({
            proof
            for acceptance_id in task_acceptance
            for proof in acceptance_proofs[str(acceptance_id)]
        })
        if task["proofs"] != expected_proofs:
            raise HandoffMalformedError("task proofs do not close over acceptance")
    if (plan is None) != (not tasks):
        raise HandoffMalformedError("tasks must be empty exactly before Plan exists")

    artifacts_raw = row.get("selected_artifacts")
    if not isinstance(artifacts_raw, list):
        raise HandoffMalformedError("selected artifacts must be a list")
    if len(artifacts_raw) > MAX_ARTIFACT_REFERENCES:
        raise HandoffMalformedError(
            f"at most {MAX_ARTIFACT_REFERENCES} artifacts may be selected")
    artifacts = [_validate_artifact_shape(item) for item in artifacts_raw]
    identities = [(str(item["kind"]), str(item["digest"])) for item in artifacts]
    if len(set(identities)) != len(identities):
        raise HandoffMalformedError("selected artifacts contain duplicates")
    if artifacts != sorted(artifacts, key=lambda item: (
            str(item["kind"]), str(item["digest"]))):
        raise HandoffMalformedError("selected artifacts are not in canonical order")
    required_artifacts = [requirement["artifact"]]
    if design is not None:
        required_artifacts.append(design["artifact"])
    if plan is not None:
        required_artifacts.append(plan["artifact"])
    if any(artifact not in artifacts for artifact in required_artifacts):
        raise HandoffMalformedError("selected artifacts omit a phase identity artifact")

    authorities_raw = row.get("authority_receipts")
    if not isinstance(authorities_raw, list):
        raise HandoffMalformedError("authority receipts must be a list")
    authorities = [_validate_authority_receipt(item) for item in authorities_raw]
    expected_gates = _required_gates(str(producer["phase"]))
    if [item["gate"] for item in authorities] != expected_gates:
        raise PhaseHandoffError("authority-missing", "required gate chain is incomplete")
    subject_by_gate = {
        "initial-authorization": requirement["fingerprint"],
        "design-approval": None if design is None else design["fingerprint"],
        "plan-approval": None if plan is None else plan["fingerprint"],
    }
    predecessor_authority: str | None = None
    for authority in authorities:
        if authority["repository_id"] != repository_id or \
                authority["subject_fingerprint"] != subject_by_gate[
                    str(authority["gate"])] or \
                authority["predecessor_authority_fingerprint"] != \
                predecessor_authority:
            raise PhaseHandoffError("authority-stale", "authority chain is stale")
        predecessor_authority = str(authority["fingerprint"])

    receipts_raw = row.get("progress_receipts")
    if not isinstance(receipts_raw, list):
        raise HandoffMalformedError("progress receipts must be a list")
    receipts = [_validate_progress_receipt(item) for item in receipts_raw]
    predecessor_receipt: str | None = None
    completed_ids: list[str] = []
    task_ids = {str(item["id"]) for item in tasks}
    for expected_sequence, receipt in enumerate(receipts, 1):
        if receipt["sequence"] != expected_sequence or \
                receipt["predecessor_receipt_fingerprint"] != predecessor_receipt:
            raise PhaseHandoffError("receipt-lineage", "progress chain is not contiguous")
        if receipt["obligation_id"] not in obligation_order or \
                (receipt["task_id"] is not None and
                 receipt["task_id"] not in task_ids):
            raise PhaseHandoffError(
                "receipt-lineage", "progress receipt subject is unknown")
        if receipt["status"] == "green" and \
                receipt["phase"] == producer["phase"]:
            completed_ids.append(str(receipt["obligation_id"]))
        predecessor_receipt = str(receipt["fingerprint"])
    if completed_ids != completed:
        raise PhaseHandoffError(
            "receipt-lineage", "progress receipts do not prove completed obligations")

    lineage = _closed(row.get("lineage"), _LINEAGE_FIELDS, "lineage")
    predecessor_handoff = _optional_digest(
        lineage.get("predecessor_handoff_fingerprint"),
        "predecessor handoff fingerprint")
    receipt_head = _optional_digest(
        lineage.get("predecessor_receipt_head"), "predecessor receipt head")
    if receipt_head != predecessor_receipt:
        raise PhaseHandoffError("receipt-lineage", "receipt head does not match chain")
    exclusions = _string_list(row.get("exclusions"), "exclusions",
                              allow_empty=False)
    if set(exclusions) != REQUIRED_EXCLUSIONS:
        raise HandoffMalformedError("required hidden-state exclusions are incomplete")

    normalized: JsonObject = {
        "schema": SCHEMA,
        "handoff_id": row.get("handoff_id"),
        "repository": {"id": repository_id},
        "source": {"commit": source_commit, "tree": source_tree},
        "requirement": requirement, "design": design, "plan": plan,
        "producer": producer, "successor": successor,
        "obligations": obligations,
        "progress": {"completed": completed, "remaining": remaining},
        "tasks": tasks, "contracts": contracts, "acceptance": acceptance,
        "selected_artifacts": artifacts,
        "authority_receipts": authorities,
        "progress_receipts": receipts,
        "lineage": {
            "predecessor_handoff_fingerprint": predecessor_handoff,
            "predecessor_receipt_head": receipt_head,
        },
        "exclusions": exclusions,
        "fingerprint": row.get("fingerprint"),
    }
    expected_identity = handoff_identity(normalized)
    if row.get("handoff_id") != expected_identity:
        raise HandoffIntegrityError("handoff identity mismatch")
    expected_fingerprint = manifest_fingerprint(normalized)
    if row.get("fingerprint") != expected_fingerprint:
        raise HandoffIntegrityError("handoff fingerprint mismatch")
    if len(canonical_bytes(normalized)) > MAX_MANIFEST_BYTES:
        raise HandoffMalformedError(
            f"canonical handoff exceeds {MAX_MANIFEST_BYTES} bytes")
    return normalized


def create_manifest(**values: object) -> JsonObject:
    """Seal already assembled v2 material with canonical identity and digest."""
    material = dict(values)
    material["schema"] = SCHEMA
    material.pop("fingerprint", None)
    material.pop("handoff_id", None)
    material["handoff_id"] = handoff_identity(material)
    material["fingerprint"] = manifest_fingerprint(material)
    return validate_manifest(material)


def publish_manifest(repository_root: str | os.PathLike[str],
                     manifest: object) -> JsonObject:
    """Atomically publish one sealed manifest with exact replay semantics."""
    root = _repository_root(repository_root)
    checked = validate_manifest(manifest)
    selected_artifacts = cast(
        list[JsonObject], checked["selected_artifacts"])
    progress_receipts = cast(list[JsonObject], checked["progress_receipts"])
    repository = cast(JsonObject, checked["repository"])
    source = cast(JsonObject, checked["source"])
    for reference in selected_artifacts:
        validate_repository_artifact_reference(
            root, reference, require_tracked=False)
    for progress_receipt in progress_receipts:
        publish_progress_receipt(
            root, str(checked["handoff_id"]), progress_receipt)
    payload = canonical_bytes(checked)
    destination = handoff_path(str(checked["handoff_id"]))
    replayed = _create_if_absent(root, destination, payload)
    publication_receipt: JsonObject = {
        "schema": PUBLICATION_RECEIPT_SCHEMA,
        "status": "replayed" if replayed else "published",
        "handoff_id": checked["handoff_id"],
        "handoff_fingerprint": checked["fingerprint"],
        "repository_id": repository["id"],
        "source_commit": source["commit"],
        "source_tree": source["tree"],
        "artifact_count": len(selected_artifacts),
        "artifact_bytes": sum(cast(int, item["bytes"])
                              for item in selected_artifacts),
    }
    publication_receipt["fingerprint"] = receipt_fingerprint(
        publication_receipt)
    return publication_receipt


def validate_repository_manifest(
        repository_root: str | os.PathLike[str], manifest: object, *,
        require_clean: bool = True,
        allowed_task_id: str | None = None) -> JsonObject:
    """Verify source, repository, tracked export lineage, and selected blobs."""
    root = _repository_root(repository_root)
    checked = validate_manifest(manifest)
    repository = cast(JsonObject, checked["repository"])
    source = cast(JsonObject, checked["source"])
    authority_receipts = cast(
        list[JsonObject], checked["authority_receipts"])
    selected_artifacts = cast(
        list[JsonObject], checked["selected_artifacts"])
    progress_receipts = cast(list[JsonObject], checked["progress_receipts"])
    tasks = cast(list[JsonObject], checked["tasks"])
    producer = cast(JsonObject, checked["producer"])
    successor = cast(JsonObject, checked["successor"])
    if repository_identity(root) != repository["id"]:
        raise PhaseHandoffError("repository-foreign", "repository identity differs")
    source_commit = str(source["commit"])
    source_tree = str(source["tree"])
    observed_tree = _git(root, "rev-parse", f"{source_commit}^{{tree}}")
    if observed_tree != source_tree:
        raise PhaseHandoffError("source-stale", "source tree differs from commit")
    prior_authority_commit: str | None = None
    for authority in authority_receipts:
        authority_commit = str(authority["source_commit"])
        authority_tree = str(authority["source_tree"])
        observed_authority_tree = _git(
            root, "rev-parse", f"{authority_commit}^{{tree}}",
            code="authority-stale")
        if observed_authority_tree != authority_tree:
            raise PhaseHandoffError(
                "authority-stale", "authority source tree differs from commit")
        if prior_authority_commit is not None:
            _git(root, "merge-base", "--is-ancestor",
                 prior_authority_commit, authority_commit,
                 code="authority-stale")
        _git(root, "merge-base", "--is-ancestor",
             authority_commit, source_commit, code="authority-stale")
        prior_authority_commit = authority_commit
    head = _git(root, "rev-parse", "HEAD")
    _git(root, "merge-base", "--is-ancestor", source_commit, head)
    if require_clean and _git(
            root, "status", "--porcelain=v1", "--untracked-files=all",
            code="checkout-dirty"):
        raise PhaseHandoffError("checkout-dirty", "checkout contains local changes")
    for reference in selected_artifacts:
        validate_repository_artifact_reference(root, reference)
    for receipt in progress_receipts:
        receipt_relative = progress_receipt_path(
            str(checked["handoff_id"]), receipt)
        receipt_relative, receipt_data = _safe_regular_file(
            root, receipt_relative, code="receipt-lineage")
        _git(root, "ls-files", "--error-unmatch", "--", receipt_relative,
             code="receipt-lineage")
        if receipt_data != canonical_bytes(receipt):
            raise PhaseHandoffError(
                "receipt-lineage", "repository receipt bytes differ")
    manifest_relative = handoff_path(str(checked["handoff_id"]))
    relative, data = _safe_regular_file(
        root, manifest_relative, code="handoff-integrity")
    _git(root, "ls-files", "--error-unmatch", "--", relative,
         code="handoff-integrity")
    if data != canonical_bytes(checked):
        raise HandoffIntegrityError("repository manifest bytes are not canonical")
    changed = set(filter(None, _git(
        root, "diff", "--name-only", source_commit, head, "--",
        code="source-stale").splitlines()))
    allowed_scope: list[str] = []
    if allowed_task_id is not None:
        matched_tasks = [task for task in tasks
                         if task["id"] == allowed_task_id]
        build_authorized = (
            producer == {"phase": "plan", "outcome": "done"} and
            successor == {"phase": "build", "mode": "next-phase"}
        ) or (
            producer == {"phase": "build", "outcome": "interrupted"} and
            successor == {"phase": "build", "mode": "same-phase-resume"}
        )
        if len(matched_tasks) != 1 or not build_authorized:
            raise PhaseHandoffError(
                "scope-widened", "task scope is not authorized by this handoff")
        allowed_scope = [str(path) for path in cast(
            list[str], matched_tasks[0]["scope"])]
    if not changed or any(
            not path.startswith("exports/pickup/") and not any(
                fnmatchcase(path, pattern) for pattern in allowed_scope)
            for path in changed):
        raise PhaseHandoffError(
            "source-stale", "source-to-export lineage contains unrelated changes")
    required = {manifest_relative} | {
        progress_receipt_path(str(checked["handoff_id"]), receipt)
        for receipt in progress_receipts
    }
    if not required <= changed:
        raise PhaseHandoffError("source-stale", "export lineage is incomplete")
    return checked


def load_manifest(repository_root: str | os.PathLike[str], relative_path: str,
                  *, require_clean: bool = True,
                  allowed_task_id: str | None = None) -> JsonObject:
    """Read bounded canonical JSON and then perform full repository validation."""
    root = _repository_root(repository_root)
    relative = _repository_relative(relative_path, "handoff path")
    if not relative.startswith(f"{PHASE_DIRECTORY}/") or \
            not relative.endswith("/handoff.json"):
        raise HandoffMalformedError("handoff path is outside the phase export area")
    relative, data = _safe_regular_file(root, relative, code="handoff-malformed")
    if len(data) > MAX_MANIFEST_BYTES:
        raise HandoffMalformedError(
            f"canonical handoff exceeds {MAX_MANIFEST_BYTES} bytes")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffMalformedError("handoff is not UTF-8 JSON") from exc
    if canonical_bytes(value) != data:
        raise HandoffMalformedError("handoff bytes are not canonical")
    checked = validate_repository_manifest(
        root, value, require_clean=require_clean,
        allowed_task_id=allowed_task_id)
    if relative != handoff_path(str(checked["handoff_id"])):
        raise HandoffIntegrityError("handoff path does not match identity")
    return checked


# Producer/consumer-facing names kept explicit for readability.
create_phase_handoff = create_manifest
validate_phase_handoff = validate_manifest
publish_phase_handoff = publish_manifest
load_phase_handoff = load_manifest


__all__ = [
    "ARTIFACT_DIRECTORY", "ARTIFACT_REFERENCE_SCHEMA",
    "ArtifactIntegrityError", "HUMAN_GATE_RECEIPT_SCHEMA",
    "HandoffIntegrityError", "HandoffMalformedError", "MAX_MANIFEST_BYTES",
    "MAX_ARTIFACT_REFERENCES", "PHASE_DIRECTORY", "PROGRESS_RECEIPT_SCHEMA",
    "PUBLICATION_RECEIPT_SCHEMA", "PhaseHandoffError",
    "PublicationConflictError", "RECEIPT_DIRECTORY", "REQUIRED_EXCLUSIONS",
    "SCHEMA", "artifact_destination", "canonical_bytes",
    "canonical_fingerprint", "create_artifact_reference",
    "create_human_gate_receipt", "create_manifest",
    "create_phase_handoff", "create_progress_receipt",
    "create_repository_artifact_reference", "handoff_identity", "handoff_path",
    "load_manifest", "load_phase_handoff", "manifest_fingerprint",
    "progress_receipt_path", "publish_manifest", "publish_phase_handoff",
    "publish_progress_receipt", "receipt_fingerprint",
    "repository_identity", "validate_artifact_reference", "validate_manifest",
    "validate_human_gate_actor",
    "validate_phase_handoff", "validate_repository_artifact_reference",
    "validate_repository_manifest",
]
