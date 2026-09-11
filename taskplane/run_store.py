"""Atomic, revision-checked owner for one taskPlane run."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable, Mapping
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
import re
import stat
import sys
import time
import uuid

import storage
if __package__:
    from .primitives import atomic_json, canonical_bytes as _canonical_json_bytes
else:
    from primitives import atomic_json, canonical_bytes as _canonical_json_bytes
if __package__:
    from .primitives import file_lock, StateError
else:
    from primitives import file_lock, StateError
try:
    from . import run_artifacts, stage_handoff
except ImportError:  # pragma: no cover - direct-module compatibility
    import run_artifacts
    import stage_handoff


class RunStoreError(RuntimeError):
    pass


class UnsupportedRunSchemaError(RunStoreError):
    """The manifest is outside the current run format."""


class RunStoreBusy(RunStoreError):
    pass


class RevisionConflict(RunStoreError):
    pass


class OperationConflict(RunStoreError):
    """An idempotency operation id was reused for different input."""


class StageStateError(RunStoreError):
    """The v4 stage index or requested stage mutation is invalid."""


class TaskplaneCompatibilityError(RunStoreError):
    """A required engine dependency cannot run on this interpreter."""


_STAGE_ENTITIES_MODULE = None


def _stage_entities_module():
    """Load the stage value contract through one named failure boundary."""
    global _STAGE_ENTITIES_MODULE
    if _STAGE_ENTITIES_MODULE is not None:
        return _STAGE_ENTITIES_MODULE
    try:
        module = importlib.import_module(
            ".stage_entities", package=__package__) if __package__ else \
            importlib.import_module("stage_entities")
    except (ImportError, SyntaxError) as exc:
        raise TaskplaneCompatibilityError(
            "required stage dependency 'stage_entities' cannot load on "
            f"Python {sys.version_info.major}.{sys.version_info.minor}") \
            from exc
    _STAGE_ENTITIES_MODULE = module
    return module


def ensure_stage_compatibility() -> None:
    """Eagerly verify the stage dependency before any run state is opened."""
    _stage_entities_module()


RUN_SCHEMA = "taskplane.run/v4"
_STAGE_INDEX_KEYS = frozenset({
    "stage_heads", "lineage", "stage_operations",
    "active_stage_projection", "stage_journal_outbox",
})
_RUN_OWNED_FIELDS = frozenset({"schema", "run_artifacts"})
_STAGE_MUTATION_KEYS = frozenset({
    "stage_heads", "lineage", "active_stage_projection",
})
_STAGE_RECEIPT_SCHEMA = "taskplane.stage-operation-receipt/v1"
_ACTIVE_PROJECTION_SCHEMA = "taskplane.active-stage-projection/v1"
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_MAX_STAGE_SUMMARY_BYTES = 16 * 1024
_MAX_STAGE_INDEX_ENTRIES = 10_000
_MAX_STAGE_RECEIPT_BYTES = 2 * 1024 * 1024
_STAGE_SUMMARY_FIELDS = frozenset({
    "schema", "stage_id", "run_id", "stage_kind", "requirement", "design",
    "state", "outcome", "default_consumable", "parent_stage_ids",
    "predecessor_stage_ids", "dependencies", "input_manifest_fingerprint",
    "execution_root_id", "deliverables", "completed_deliverables",
    "completion_evidence_fingerprints", "actor", "terminalized_at",
    "reason_code", "reason", "aggregate_revision",
    "aggregate_fingerprint", "stage_fingerprint", "fingerprint",
})
_STAGE_RECEIPT_FIELDS = frozenset({
    "schema", "operation_id", "request_fingerprint", "operation",
    "stage_ids", "committed_revision", "result", "result_fingerprint",
})
_EMPTY_STAGE_ID_OPERATIONS = frozenset({
    "rebuild_active_stage_projection",
})
_STAGE_JOURNAL_EVENT_FIELDS = frozenset({
    "event", "operation", "operation_id", "request_fingerprint",
    "revision", "stage_ids", "at",
})


def _nonempty_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise StageStateError(f"{label} must not be empty")
    return text


def _run_id(value: object) -> str:
    try:
        return storage.validate_stage_path_id(value, "run id")
    except storage.StorageIdentityError as exc:
        raise RunStoreError("run id is invalid") from exc


def _stage_id(value: object, label: str = "stage id") -> str:
    try:
        return storage.validate_stage_path_id(value, label)
    except storage.StorageIdentityError as exc:
        raise StageStateError(f"{label} is invalid") from exc


def _operation_id(value: object, label: str = "operation id") -> str:
    operation_id = str(value or "").strip()
    if not _SAFE_OPERATION_ID.fullmatch(operation_id):
        raise StageStateError(f"{label} is invalid")
    return operation_id


def _fingerprint(value: object, label: str) -> str:
    fingerprint = str(value or "")
    if not _FINGERPRINT.fullmatch(fingerprint):
        raise StageStateError(f"{label} is invalid")
    return fingerprint


@contextmanager
def _confined_directory_fd(home: str, directory: str):
    """Pin a directory beneath canonical home without following any link."""
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW") or \
            os.open not in os.supports_dir_fd or \
            os.link not in os.supports_dir_fd or \
            os.unlink not in os.supports_dir_fd:
        raise StageStateError(
            "stage object storage needs no-follow dir-fd support")
    root = os.path.abspath(home)
    target = os.path.abspath(directory)
    if os.path.commonpath((root, target)) != root:
        raise StageStateError("stage object directory escapes taskPlane home")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        relative = os.path.relpath(target, root)
        components = [] if relative == "." else relative.split(os.sep)
        for component in components:
            if component in {"", ".", ".."}:
                raise StageStateError(
                    "stage object directory component is invalid")
            next_descriptor = os.open(
                component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise StageStateError("stage object parent is not a directory")
        yield descriptor
    except OSError as exc:
        raise StageStateError(
            "stage object directory is unavailable") from exc
    finally:
        os.close(descriptor)


def _active_stage_ids(stage_heads: object) -> list[str]:
    if not isinstance(stage_heads, dict):
        raise StageStateError("stage_heads must be an object")
    if len(stage_heads) > _MAX_STAGE_INDEX_ENTRIES:
        raise StageStateError("stage_heads exceeds its persisted bound")
    active: list[str] = []
    for raw_stage_id, head in stage_heads.items():
        stage_id = _stage_id(raw_stage_id, "stage head id")
        if not isinstance(head, dict) or set(head) != {"object", "summary"}:
            raise StageStateError(f"stage head {stage_id} must be an object")
        reference = head.get("object")
        if not isinstance(reference, dict) or set(reference) != {
                "schema", "stage_id", "fingerprint", "digest", "bytes",
                "locator"}:
            raise StageStateError(
                f"stage head {stage_id} object reference is invalid")
        if reference.get("schema") != "taskplane.stage-object-ref/v1" or \
                reference.get("stage_id") != stage_id:
            raise StageStateError(
                f"stage head {stage_id} object reference identity mismatch")
        object_fingerprint = _fingerprint(
            reference.get("fingerprint"), "stage object fingerprint")
        _fingerprint(reference.get("digest"), "stage object digest")
        byte_count = reference.get("bytes")
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or \
                byte_count <= 0:
            raise StageStateError("stage object byte count is invalid")
        if reference.get("locator") != (
                f"stages/objects/{stage_id}/{object_fingerprint}.json"):
            raise StageStateError("stage object locator mismatch")
        summary = head.get("summary")
        if not isinstance(summary, dict) or \
                set(summary) != _STAGE_SUMMARY_FIELDS:
            raise StageStateError(
                f"stage head {stage_id} needs a bounded summary")
        if len(_canonical_json_bytes(summary)) > _MAX_STAGE_SUMMARY_BYTES:
            raise StageStateError(
                f"stage head {stage_id} summary exceeds its byte bound")
        if summary.get("schema") != "taskplane.stage-summary/v1" or \
                summary.get("stage_id") != stage_id:
            raise StageStateError(
                f"stage head {stage_id} summary identity mismatch")
        aggregate_fingerprint = _fingerprint(
            summary.get("aggregate_fingerprint"),
            "stage aggregate fingerprint")
        if summary.get("stage_fingerprint") != aggregate_fingerprint or \
                object_fingerprint != aggregate_fingerprint:
            raise StageStateError(
                f"stage head {stage_id} fingerprint binding mismatch")
        expected_summary_fingerprint = hashlib.sha256(
            _canonical_json_bytes({
                key: value for key, value in summary.items()
                if key != "fingerprint"
            })).hexdigest()
        if summary.get("fingerprint") != expected_summary_fingerprint:
            raise StageStateError(
                f"stage head {stage_id} summary fingerprint mismatch")
        aggregate_revision = summary.get("aggregate_revision")
        if isinstance(aggregate_revision, bool) or \
                not isinstance(aggregate_revision, int) or \
                aggregate_revision < 1:
            raise StageStateError("stage aggregate revision is invalid")
        if not isinstance(summary.get("default_consumable"), bool):
            raise StageStateError("stage consumability is invalid")
        state = str(summary.get("state") or "")
        if state == "active":
            if summary.get("outcome") is not None:
                raise StageStateError("active stage summary has an outcome")
            active.append(stage_id)
        elif state == "terminal":
            if summary.get("outcome") not in {"done", "closed", "discarded"}:
                raise StageStateError(
                    "terminal stage summary outcome is invalid")
        else:
            raise StageStateError(
                f"stage head {stage_id} has invalid lifecycle state")
    return sorted(active)


def _canonical_active_projection(
        stage_heads: object, foreground_stage_id: object = None) -> dict:
    active = _active_stage_ids(stage_heads)
    foreground = (None if foreground_stage_id is None else
                  _nonempty_text(foreground_stage_id,
                                 "foreground stage id"))
    # A requested stale foreground is cleared; repair never guesses a
    # different foreground stage from the active set.
    if foreground is not None and foreground not in active:
        foreground = None
    projection = {
        "schema": _ACTIVE_PROJECTION_SCHEMA,
        "active_stage_ids": active,
        "foreground_stage_id": foreground,
    }
    projection["fingerprint"] = hashlib.sha256(
        _canonical_json_bytes(projection)).hexdigest()
    return projection


def _validate_active_projection(stage_heads: object,
                                projection: object) -> dict:
    if not isinstance(projection, dict):
        raise StageStateError("active_stage_projection must be an object")
    if set(projection) != {
            "schema", "active_stage_ids", "foreground_stage_id",
            "fingerprint"}:
        raise StageStateError("active stage projection fields are invalid")
    if projection.get("schema") != _ACTIVE_PROJECTION_SCHEMA:
        raise StageStateError("active stage projection schema is invalid")
    active = projection.get("active_stage_ids")
    if not isinstance(active, list) or any(
            not isinstance(value, str) or not value for value in active):
        raise StageStateError("active stage ids must be a list of ids")
    if active != sorted(set(active)):
        raise StageStateError("active stage ids must be sorted and unique")
    expected = _canonical_active_projection(
        stage_heads, projection.get("foreground_stage_id"))
    if projection != expected:
        raise StageStateError("active stage projection is stale")
    return copy.deepcopy(expected)


def _validate_lineage(lineage: object) -> list[dict]:
    if not isinstance(lineage, list):
        raise StageStateError("lineage must be a list")
    if len(lineage) > _MAX_STAGE_INDEX_ENTRIES:
        raise StageStateError("lineage exceeds its persisted bound")
    stage_entities = _stage_entities_module()
    rows: list[dict] = []
    for row in lineage:
        if not isinstance(row, dict):
            raise StageStateError("lineage entries must be objects")
        if len(_canonical_json_bytes(row)) > _MAX_STAGE_SUMMARY_BYTES:
            raise StageStateError("lineage entry exceeds its byte bound")
        if row.get("schema") != "taskplane.stage-lineage/v1":
            raise StageStateError("stage lineage schema is invalid")
        try:
            checked = stage_entities.validate_lineage(row)
        except (TypeError, ValueError) as exc:
            raise StageStateError("stage lineage entry is invalid") from exc
        if checked != row:
            raise StageStateError("stage lineage entry is not canonical")
        rows.append(copy.deepcopy(checked))
    return rows


def _validate_lineage_bindings(lineage: list[dict],
                               stage_heads: dict) -> None:
    fingerprints: set[str] = set()
    for row in lineage:
        parent_id = row["parent_stage_id"]
        child_id = row["child_stage_id"]
        predecessor_ids = row["predecessor_stage_ids"]
        if child_id not in stage_heads or any(
                predecessor_id not in stage_heads
                for predecessor_id in predecessor_ids):
            raise StageStateError("stage lineage names an unindexed stage")
        child_summary = stage_heads[child_id]["summary"]
        if parent_id is not None and (
                parent_id not in stage_heads or
                parent_id not in child_summary["parent_stage_ids"]):
            raise StageStateError(
                "stage lineage parent does not match the child head")
        if (row["handoff_fingerprint"] !=
                child_summary["input_manifest_fingerprint"] or
                predecessor_ids !=
                child_summary["predecessor_stage_ids"]):
            raise StageStateError(
                "stage lineage does not match the child head")
        fingerprint = row["fingerprint"]
        if fingerprint in fingerprints:
            raise StageStateError("stage lineage contains duplicates")
        fingerprints.add(fingerprint)


def _current_stage_index(current: dict) -> dict:
    _validate_stage_index(current)
    return copy.deepcopy(current)


def _validate_stage_journal_outbox(value: object, *,
                                   operations: dict) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise StageStateError("stage_journal_outbox must be an object")
    if len(value) > _MAX_STAGE_INDEX_ENTRIES:
        raise StageStateError("stage_journal_outbox exceeds its persisted bound")
    for operation_id, entry in value.items():
        checked_id = _operation_id(operation_id, "stage journal operation id")
        if not isinstance(entry, dict) or set(entry) != {"event", "delivered"}:
            raise StageStateError("stage journal outbox entry is invalid")
        if not isinstance(entry["delivered"], bool):
            raise StageStateError("stage journal delivery state is invalid")
        event = entry["event"]
        if not isinstance(event, dict) or set(event) != \
                _STAGE_JOURNAL_EVENT_FIELDS:
            raise StageStateError("stage journal event is invalid")
        receipt = operations.get(checked_id)
        if receipt is None or event != {
            "event": "stage_operation_committed",
            "operation": receipt["operation"],
            "operation_id": checked_id,
            "request_fingerprint": receipt["request_fingerprint"],
            "revision": receipt["committed_revision"],
            "stage_ids": receipt["stage_ids"],
            "at": event.get("at"),
        }:
            raise StageStateError("stage journal event is not receipt-bound")
        timestamp = event.get("at")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or \
                timestamp < 0:
            raise StageStateError("stage journal event time is invalid")


def _validate_stage_index(manifest: dict) -> None:
    if manifest.get("schema") != RUN_SCHEMA:
        raise UnsupportedRunSchemaError(f"unsupported_run_schema: expected {RUN_SCHEMA}")
    manifest_revision = manifest.get("revision")
    if isinstance(manifest_revision, bool) or \
            not isinstance(manifest_revision, int) or manifest_revision < 1:
        raise StageStateError("run manifest revision is invalid")
    heads = manifest.get("stage_heads")
    _active_stage_ids(heads)
    run_id = manifest.get("run_id")
    for head in heads.values():
        if head["summary"].get("run_id") != run_id:
            raise StageStateError("stage head belongs to another run")
    lineage = _validate_lineage(manifest.get("lineage"))
    _validate_lineage_bindings(lineage, heads)
    operations = manifest.get("stage_operations")
    if not isinstance(operations, dict):
        raise StageStateError("stage_operations must be an object")
    if len(operations) > _MAX_STAGE_INDEX_ENTRIES:
        raise StageStateError("stage_operations exceeds its persisted bound")
    revision = manifest_revision
    for operation_id, receipt in operations.items():
        _validate_operation_receipt(
            receipt, operation_id=_operation_id(
                operation_id, "stage operation id"),
            manifest_revision=revision)
    _validate_stage_journal_outbox(
        manifest.get("stage_journal_outbox"), operations=operations)
    _validate_active_projection(
        manifest.get("stage_heads"), manifest.get("active_stage_projection"))


def _operation_receipt(value: object, *, operation_id: str,
                       request_fingerprint: str,
                       committed_revision: int) -> dict:
    if not isinstance(value, dict):
        raise StageStateError("stage operation receipt must be an object")
    allowed_input = _STAGE_RECEIPT_FIELDS - {"committed_revision"}
    if set(value) - allowed_input:
        raise StageStateError("stage operation receipt fields are invalid")
    receipt = copy.deepcopy(value)
    supplied_schema = receipt.get("schema")
    if supplied_schema not in (None, _STAGE_RECEIPT_SCHEMA):
        raise StageStateError("stage operation receipt schema is invalid")
    supplied_id = receipt.get("operation_id")
    if supplied_id not in (None, operation_id):
        raise StageStateError("stage operation receipt id mismatch")
    supplied_request = receipt.get("request_fingerprint")
    if supplied_request not in (None, request_fingerprint):
        raise StageStateError("stage operation receipt request mismatch")
    operation = _operation_id(receipt.get("operation"), "stage operation")
    raw_stage_ids = receipt.get("stage_ids")
    if not isinstance(raw_stage_ids, list) or any(
            not isinstance(value, str) or not value
            for value in raw_stage_ids):
        raise StageStateError("stage operation stage_ids must be a list")
    if not raw_stage_ids and operation not in _EMPTY_STAGE_ID_OPERATIONS:
        raise StageStateError("stage operation stage_ids must not be empty")
    stage_ids = sorted(set(raw_stage_ids))
    if len(stage_ids) != len(raw_stage_ids):
        raise StageStateError("stage operation stage_ids must be unique")
    receipt.update({
        "schema": _STAGE_RECEIPT_SCHEMA,
        "operation_id": operation_id,
        "request_fingerprint": request_fingerprint,
        "operation": operation,
        "stage_ids": stage_ids,
        "committed_revision": committed_revision,
    })
    if "result" in receipt:
        result_bytes = _canonical_json_bytes(receipt["result"])
        if len(result_bytes) > _MAX_STAGE_RECEIPT_BYTES:
            raise StageStateError("stage operation result exceeds its bound")
        expected_result = hashlib.sha256(result_bytes).hexdigest()
        supplied_result = receipt.get("result_fingerprint")
        if supplied_result not in (None, expected_result):
            raise StageStateError(
                "stage operation result fingerprint mismatch")
        receipt["result_fingerprint"] = expected_result
    elif "result_fingerprint" in receipt:
        raise StageStateError(
            "stage operation result fingerprint has no result")
    _validate_operation_receipt(
        receipt, operation_id=operation_id,
        manifest_revision=committed_revision)
    return receipt


def _validate_operation_receipt(value: object, *, operation_id: str,
                                manifest_revision: int) -> dict:
    if not isinstance(value, dict):
        raise StageStateError("stored stage operation receipt is invalid")
    required = _STAGE_RECEIPT_FIELDS - {"result", "result_fingerprint"}
    optional = {"result", "result_fingerprint"}
    if not required.issubset(value) or set(value) - (required | optional):
        raise StageStateError("stored stage operation receipt fields are invalid")
    if ("result" in value) != ("result_fingerprint" in value):
        raise StageStateError("stored stage operation result fields mismatch")
    if value.get("schema") != _STAGE_RECEIPT_SCHEMA or \
            value.get("operation_id") != operation_id:
        raise StageStateError("stored stage operation receipt identity mismatch")
    _fingerprint(value.get("request_fingerprint"),
                 "stage operation request fingerprint")
    _operation_id(value.get("operation"), "stage operation")
    stage_ids = value.get("stage_ids")
    if not isinstance(stage_ids, list) or any(
            not isinstance(stage_id, str) for stage_id in stage_ids):
        raise StageStateError("stored stage operation stage ids are invalid")
    try:
        for stage_id in stage_ids:
            _stage_id(stage_id, "stored stage operation stage id")
    except StageStateError as exc:
        raise StageStateError(
            "stored stage operation stage ids are invalid") from exc
    if stage_ids != sorted(set(stage_ids)):
        raise StageStateError(
            "stored stage operation stage ids must be sorted and unique")
    if not stage_ids and value.get("operation") not in \
            _EMPTY_STAGE_ID_OPERATIONS:
        raise StageStateError("stored stage operation stage ids are empty")
    revision = value.get("committed_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or \
            revision < 1 or revision > int(manifest_revision):
        raise StageStateError(
            "stored stage operation committed revision is invalid")
    if "result" in value:
        result_bytes = _canonical_json_bytes(value["result"])
        if len(result_bytes) > _MAX_STAGE_RECEIPT_BYTES:
            raise StageStateError("stored stage operation result exceeds its bound")
        expected_result = hashlib.sha256(result_bytes).hexdigest()
        if value.get("result_fingerprint") != expected_result:
            raise StageStateError(
                "stored stage operation result fingerprint mismatch")
    if len(_canonical_json_bytes(value)) > _MAX_STAGE_RECEIPT_BYTES:
        raise StageStateError("stored stage operation receipt exceeds its bound")
    return copy.deepcopy(value)


_TRANSACTIONS: ContextVar[dict] = ContextVar("taskplane_run_transactions", default={})


def _atomic_write_json(path: str, value: dict) -> None:
    pending = _TRANSACTIONS.get().get(os.path.abspath(path))
    if pending is not None:
        pending["manifest"] = copy.deepcopy(value)
    else:
        atomic_json(path, value)


@contextmanager
def _lock(path: str):
    if os.path.abspath(path) in _TRANSACTIONS.get():
        yield
        return
    for candidate in (path, path + ".lock"):
        if os.path.realpath(candidate) != os.path.abspath(candidate):
            raise RunStoreError("run storage lock uses an unsafe symlink")
    try:
        with file_lock(path, timeout=10.0):
            yield
    except StateError as exc:
        raise RunStoreBusy(f"run manifest lock is unavailable: {exc}") \
            from None


def _merge(current: dict, changes: dict) -> dict:
    merged = copy.deepcopy(current)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class RunStore:
    """Persist canonical run identity, state, and artifact ownership."""

    def __init__(self, *, home: str | None = None,
                 workspace: str | None = None):
        ensure_stage_compatibility()
        self.home = storage.taskplane_home(home, workspace=workspace)

    def _knowledge_path(self, workspace: str) -> str:
        # Use the incumbent knowledge-root resolution, including migration.
        identity = storage.resolve_repository_identity(workspace)
        layout = storage.resolve_layout(identity, home=self.home, run_id="knowledge")
        return storage._confined_stage_path(
            self.home, "projects", identity.key, "knowledge",
            "governed-updates.json", leaf_kind="file")

    def _load_knowledge(self, path: str) -> dict:
        try:
            with open(path, encoding="utf-8") as handle:
                state = json.load(handle)
        except FileNotFoundError:
            return {"schema": "taskplane.governed-knowledge/v1", "scopes": {}, "operations": {}}
        except (OSError, ValueError) as exc:
            raise RunStoreError("knowledge state is unavailable") from exc
        if (not isinstance(state, dict)
                or set(state) != {"schema", "scopes", "operations"}
                or state["schema"] != "taskplane.governed-knowledge/v1"
                or not isinstance(state["scopes"], dict)
                or not isinstance(state["operations"], dict)):
            raise RunStoreError("knowledge state is invalid")
        return state

    @staticmethod
    def _knowledge_scope(state: dict, scope: list[str]) -> dict:
        identity = hashlib.sha256(_canonical_json_bytes(scope)).hexdigest()
        initial = {"scope": scope, "lineage": [], "content": {}}
        initial["fingerprint"] = hashlib.sha256(_canonical_json_bytes(initial)).hexdigest()
        snapshot = copy.deepcopy(state["scopes"].get(identity, initial))
        if (not isinstance(snapshot, dict)
                or set(snapshot) != {"scope", "lineage", "content", "fingerprint"}
                or snapshot["scope"] != scope
                or not isinstance(snapshot["lineage"], list)
                or not isinstance(snapshot["content"], dict)):
            raise RunStoreError("knowledge scope is invalid")
        material = {name: value for name, value in snapshot.items() if name != "fingerprint"}
        if snapshot["fingerprint"] != hashlib.sha256(_canonical_json_bytes(material)).hexdigest():
            raise RunStoreError("knowledge scope fingerprint mismatch")
        return snapshot

    def knowledge_snapshot(self, workspace: str, *, scope: list[str]) -> dict:
        """Read only the caller's selected scope, without receipt/private bodies."""
        path = self._knowledge_path(workspace)
        with _lock(path):
            return self._knowledge_scope(self._load_knowledge(path), scope)

    def apply_knowledge(
        self, workspace: str, proposal: Mapping[str, object], *, scope: list[str],
        writer_id: str, fencing_token: int, current_writer: Callable[[], tuple[str, int]],
        authorize: Callable[[dict[str, object], str], str | None] | None,
        retention_class: str, key: stage_handoff.SigningKey,
        now: int, expires_at: int, freshness: Mapping[str, object],
        action: str = "apply",
    ) -> dict[str, object]:
        """Apply through the incumbent KB root and shared serialization owner.

        Only the orchestrator supplies the trusted current writer and admission
        port. The port rechecks evaluator admission, canonical gate, exact
        proposal/candidate/provenance, scope and retention authority under the
        lock, returning its gate fingerprint or None. Agents receive neither
        this port nor the signing key. No lifecycle authority comes from data.

        One atomic record contains both append-only lineage and the original
        signed replay receipt. A crash before replacement applies nothing; a
        crash after replacement returns that receipt without another effect.
        Fingerprints-only retention never persists a proposal body, including
        to journals or staging files. Rejections/conflicts are returned sealed
        to the orchestrator as findings and leave the knowledge store unchanged.
        """
        value = stage_handoff.prepare_knowledge_proposal(proposal, expected_scope=scope)
        # Unlike a producer, a consumer must never mint a missing identity.
        if proposal.get("proposal_fingerprint") != value["proposal_fingerprint"]:
            raise stage_handoff.HandoffIntegrityError("knowledge producer identity is missing")
        if action not in {"apply", "delete", "minimize"}:
            raise RunStoreError("unsupported knowledge action")
        entities = _stage_entities_module()
        writer_id = _operation_id(writer_id, "knowledge writer")
        if type(fencing_token) is not int or fencing_token < 1:
            raise RunStoreError("knowledge writer fence is invalid")
        path = self._knowledge_path(workspace)
        scope_id = hashlib.sha256(_canonical_json_bytes(scope)).hexdigest()
        operation = str(value["operation_id"])
        operation_key = str(value["run_id"]) + ":" + operation
        request = hashlib.sha256(_canonical_json_bytes({
            "proposal": value["proposal_fingerprint"], "freshness": dict(freshness),
            "scope": scope, "retention_class": retention_class,
        })).hexdigest()
        with _lock(path):
            state = self._load_knowledge(path)
            snapshot = self._knowledge_scope(state, scope)
            previous = state["operations"].get(operation_key)
            if previous is not None:
                if previous["request_fingerprint"] != request:
                    raise OperationConflict("knowledge operation reused with changed input")
                existing = previous["receipts"].get(action)
                if existing is not None:
                    return copy.deepcopy(existing)
            gate = authorize(copy.deepcopy(value), action) if callable(authorize) else None
            gate_valid = isinstance(gate, str) and bool(_FINGERPRINT.fullmatch(gate))
            fence_valid = callable(current_writer) and current_writer() == (writer_id, fencing_token)
            fence_valid = fence_valid and fencing_token >= max(
                (entry["fencing_token"] for entry in snapshot["lineage"]), default=1)
            if previous is not None:
                fence_valid = fence_valid and fencing_token >= previous["fencing_token"]
            retention_valid = retention_class in {"fingerprints-only", "scoped-facts"}
            outcome = "applied" if action == "apply" else "tombstoned"
            reason = "continue"
            if not gate_valid or not fence_valid or not retention_valid:
                outcome, reason = "rejected", "hold"
            elif action == "apply" and value["base_knowledge_fingerprint"] != snapshot["fingerprint"]:
                outcome, reason = "conflict", "fresh-package"
            elif action != "apply" and previous is None:
                outcome, reason = "rejected", "hold"
            elif action == "apply" and any(
                superseded not in {entry["proposal_fingerprint"] for entry in snapshot["lineage"]}
                for superseded in value["supersedes"]
            ):
                outcome, reason = "rejected", "hold"
            new_fingerprint = None
            next_snapshot = copy.deepcopy(snapshot)
            if outcome in {"applied", "tombstoned"}:
                entry = {"proposal_fingerprint": value["proposal_fingerprint"],
                    "content_fingerprint": hashlib.sha256(str(value["content"]).encode()).hexdigest(),
                    "run_id": value["run_id"], "phase_id": value["phase_id"],
                    "attempt_id": value["attempt_id"], "operation_id": operation,
                    "candidate_fingerprint": value["candidate_fingerprint"],
                    "observation_fingerprint": hashlib.sha256(str(value["finding_or_observation_reference"]).encode()).hexdigest(),
                    "supersedes": value["supersedes"], "action": action,
                    "writer_id": writer_id, "fencing_token": fencing_token,
                    "retention_class": retention_class, "prior_fingerprint": snapshot["fingerprint"]}
                next_snapshot["lineage"].append(entry)
                if action == "apply" and retention_class == "scoped-facts":
                    next_snapshot["content"][value["proposal_fingerprint"]] = value["content"]
                elif action != "apply":
                    next_snapshot["content"].pop(value["proposal_fingerprint"], None)
                new_fingerprint = hashlib.sha256(_canonical_json_bytes({
                    "scope": scope, "lineage": next_snapshot["lineage"],
                    "content": next_snapshot["content"]})).hexdigest()
                next_snapshot["fingerprint"] = new_fingerprint
            result = entities.create_contract({
                "schema": entities.KNOWLEDGE_APPLY_SCHEMA,
                "base_fingerprint": value["base_knowledge_fingerprint"],
                "current_fingerprint": snapshot["fingerprint"],
                "new_fingerprint": new_fingerprint,
                "proposal_fingerprint": value["proposal_fingerprint"],
                "gate_receipt_fingerprint": gate if gate_valid else "0" * 64,
                "operation_id": operation, "writer_id": writer_id,
                "fencing_token": fencing_token,
                "retention_class": retention_class if retention_valid else "unassigned",
                "applied_at": datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z"),
                "outcome": outcome, "continuation": {"kind": reason, "phase_id": value["phase_id"]},
            })
            receipt = stage_handoff.sign_contract(result, key=key, issued_at=now,
                expires_at=expires_at, freshness=freshness)
            if outcome in {"applied", "tombstoned"}:
                state["scopes"][scope_id] = next_snapshot
                record = copy.deepcopy(previous) if previous else {
                    "request_fingerprint": request, "receipts": {}, "fencing_token": fencing_token}
                record["receipts"][action] = receipt
                record["fencing_token"] = fencing_token
                state["operations"][operation_key] = record
                _atomic_write_json(path, state)
            return receipt

    def _manifest_path(self, run_id: str) -> str:
        return storage._confined_stage_path(
            self.home, "runs", _run_id(run_id), "manifest.json", leaf_kind="file")

    def _journal_path(self, run_id: str) -> str:
        return storage._confined_stage_path(
            self.home, "runs", _run_id(run_id), "journal.jsonl", leaf_kind="file")

    def _append_journal(self, run_id: str, event: dict) -> None:
        pending = _TRANSACTIONS.get().get(self._manifest_path(run_id))
        if pending is not None:
            pending["events"].append(copy.deepcopy(event))
            return
        path = self._journal_path(run_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(event, sort_keys=True,
                                    separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _read_journal_rows_locked(self, run_id: str) -> list[dict]:
        """Read complete JSONL rows and discard only a crash-torn tail."""
        path = self._journal_path(run_id)
        try:
            with open(path, "rb") as handle:
                payload = handle.read()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise RunStoreError("run journal is unavailable or corrupt") from exc

        rows: list[dict] = []
        offset = 0
        lines = payload.splitlines(keepends=True)
        for index, encoded in enumerate(lines):
            final_unterminated = (
                index == len(lines) - 1 and
                not encoded.endswith((b"\n", b"\r"))
            )
            try:
                row = json.loads(encoded.decode("utf-8"))
                if not isinstance(row, dict):
                    raise ValueError("journal row is not an object")
            except (UnicodeDecodeError, ValueError) as exc:
                if not final_unterminated:
                    raise RunStoreError(
                        "run journal is unavailable or corrupt") from exc
                try:
                    with open(path, "r+b") as handle:
                        handle.truncate(offset)
                        handle.flush()
                        os.fsync(handle.fileno())
                except OSError as repair_exc:
                    raise RunStoreError(
                        "run journal crash tail could not be repaired") \
                        from repair_exc
                break
            rows.append(row)
            offset += len(encoded)
            if final_unterminated:
                try:
                    with open(path, "ab") as handle:
                        handle.write(b"\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                except OSError as repair_exc:
                    raise RunStoreError(
                        "run journal crash tail could not be completed") \
                        from repair_exc
        return rows

    def _journal_contains_stage_event(self, run_id: str,
                                      event: dict, rows: list[dict] | None = None) -> bool:
        rows = self._read_journal_rows_locked(run_id) if rows is None else list(rows)
        pending = _TRANSACTIONS.get().get(self._manifest_path(run_id))
        if pending is not None:
            rows += pending["events"]
        identity = (event["operation_id"], event["request_fingerprint"])
        matches = [row for row in rows if (
            row.get("event") == "stage_operation_committed" and
            (row.get("operation_id"), row.get("request_fingerprint")) ==
            identity
        )]
        if len(matches) > 1 or (matches and matches[0] != event):
            raise StageStateError("stage journal event collision")
        return bool(matches)

    def _relay_stage_journal_outbox_locked(
            self, run_id: str, manifest: dict,
            operation_id: str, journal_rows: list[dict] | None = None) -> dict:
        """Deliver and acknowledge one receipt-bound journal event."""
        outbox = manifest.get("stage_journal_outbox") or {}
        entry = outbox.get(operation_id)
        if entry is None or entry.get("delivered") is True:
            return manifest
        event = copy.deepcopy(entry["event"])
        if not self._journal_contains_stage_event(run_id, event, journal_rows):
            self._append_journal(run_id, event)
            if journal_rows is not None:
                journal_rows.append(event)
        # The journal is derived. Its event identity is the delivery marker;
        # acknowledging delivery must not publish a second aggregate head.
        entry["delivered"] = True
        return manifest

    def _relay_all_stage_journal_outbox_locked(
            self, run_id: str, manifest: dict) -> dict:
        """Sweep every committed undelivered event under the run lock."""
        _validate_stage_index(manifest)
        relayed = copy.deepcopy(manifest)
        journal_rows = self._read_journal_rows_locked(run_id)
        for operation_id in sorted(
                (manifest.get("stage_journal_outbox") or {}).keys()):
            relayed = self._relay_stage_journal_outbox_locked(
                run_id, relayed, operation_id, journal_rows)
        return relayed

    def _load_manifest(self, run_id: str) -> dict:
        path = self._manifest_path(run_id)
        pending = _TRANSACTIONS.get().get(path)
        if pending is not None:
            return copy.deepcopy(pending["manifest"])
        try:
            with open(path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RunStoreError(f"run manifest is unavailable: {run_id}") \
                from exc
        if not isinstance(value, dict):
            raise RunStoreError(f"run manifest is invalid: {run_id}")
        if value.get("schema") != RUN_SCHEMA:
            raise UnsupportedRunSchemaError(
                f"unsupported_run_schema: run {run_id} requires {RUN_SCHEMA}")
        if "run_artifacts" in value:
            try:
                run_artifacts.validate_manifest_locator_reference(
                    value["run_artifacts"])
            except run_artifacts.RunArtifactError as exc:
                raise RunStoreError(
                    f"run artifact locator is invalid: {run_id}") from exc
        return value

    def create(self, identity: storage.RepositoryIdentity, *, run_id: str,
               checkout: str, host: dict, target: dict) -> dict:
        layout = storage.resolve_layout(identity, home=self.home,
                                        run_id=run_id)
        path = self._manifest_path(run_id)
        repository_record = storage._confined_stage_path(
            self.home, "repositories", f"{identity.key}.json", leaf_kind="file")
        os.makedirs(self.home, mode=0o700, exist_ok=True)
        storage._ensure_confined_directories(self.home, layout.run_root)
        storage._ensure_confined_directories(self.home, os.path.dirname(repository_record))
        with _lock(path):
            if os.path.exists(path):
                raise RunStoreError(f"run already exists: {run_id}")
            repository = identity.to_dict()
            repository["checkout"] = os.path.realpath(checkout)
            manifest = {
                "schema": RUN_SCHEMA,
                "run_id": str(run_id),
                "revision": 1,
                "status": "preflight",
                "repository": repository,
                "target": copy.deepcopy(target),
                "host": copy.deepcopy(host),
                "preflight": {"status": "pending", "completed_steps": [],
                              "pending_action": None},
                "contract": {"status": "inactive", "task_id": None},
                "paths": {
                    "state": layout.state_root,
                    "graph": layout.graph_root,
                    "evidence": layout.evidence_root,
                    "lenses": layout.lens_root,
                    "artifacts": layout.artifact_root,
                },
                "run_artifacts": run_artifacts.manifest_locator_reference(),
                "stage_heads": {}, "lineage": [], "stage_operations": {},
                "stage_journal_outbox": {},
                "active_stage_projection": _canonical_active_projection({}),
            }
            _atomic_write_json(path, manifest)
            _atomic_write_json(repository_record, {
                "schema": "taskplane.repository/v1",
                "repository": identity.to_dict(),
                "repository_key": identity.key,
            })
            self._append_journal(run_id, {
                "event": "run_created", "revision": 1,
                "status": "preflight", "at": int(time.time())})
            return manifest

    def inspect(self, run_id: str) -> dict:
        """Read manifest identity without replaying journals or writing locks."""
        run_id = _run_id(run_id)
        value = self._load_manifest(run_id)
        if value.get("run_id") != run_id:
            raise RunStoreError(f"run manifest identity is invalid: {run_id}")
        _validate_stage_index(value)
        return value

    def load(self, run_id: str) -> dict:
        run_id = _run_id(run_id)
        path = self._manifest_path(run_id)
        with _lock(path):
            value = self._load_manifest(run_id)
            return self._relay_all_stage_journal_outbox_locked(
                run_id, value)

    @contextmanager
    def transaction(self, run_id: str):
        """Publish one head for an entire workflow transition, or none on error.

        Existing lifecycle operations retain their CAS and receipt checks.
        Nested operations share the private draft under the same run lock;
        immutable objects remain unreferenced if the transition aborts.
        """
        path = self._manifest_path(run_id)
        if path in _TRANSACTIONS.get():
            yield
            return
        with _lock(path):
            original = self._load_manifest(run_id)
            pending = {"manifest": copy.deepcopy(original), "events": []}
            token = _TRANSACTIONS.set({**_TRANSACTIONS.get(), path: pending})
            try:
                yield
            except BaseException:
                raise
            else:
                _validate_stage_index(pending["manifest"])
            finally:
                _TRANSACTIONS.reset(token)
            if pending["manifest"] != original:
                # Keep journal events in the durable outbox until delivery.
                # A process crash after this write is replayed by load().
                for entry in pending["manifest"].get("stage_journal_outbox", {}).values():
                    if entry["event"] in pending["events"]:
                        entry["delivered"] = False
                _atomic_write_json(path, pending["manifest"])
            for event in pending["events"]:
                self._append_journal(run_id, event)

    def save_workflow(self, run_id: str, workflow: dict) -> dict:
        """Replace workflow metadata in the aggregate; removed keys stay removed."""
        with self.transaction(run_id):
            current = self._load_manifest(run_id)
            if current.get("workflow") == workflow:
                return current
            updated = self.commit(run_id, expected_revision=current["revision"],
                                  changes={"workflow": None})
            updated["workflow"] = copy.deepcopy(workflow)
            _atomic_write_json(self._manifest_path(run_id), updated)
            return updated

    def commit(self, run_id: str, *, expected_revision: int,
               changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise RunStoreError("run changes must be an object")
        forbidden = set(changes) & (_RUN_OWNED_FIELDS | _STAGE_INDEX_KEYS)
        if forbidden:
            raise RunStoreError(
                "generic run commit cannot change owned fields: " +
                ", ".join(sorted(forbidden)))
        path = self._manifest_path(run_id)
        with _lock(path):
            current = self._load_manifest(run_id)
            current = self._relay_all_stage_journal_outbox_locked(
                run_id, current)
            actual = int(current.get("revision") or 0)
            if actual != int(expected_revision):
                raise RevisionConflict(
                    f"run {run_id} revision is {actual}, expected "
                    f"{expected_revision}")
            _validate_stage_index(current)
            updated = _merge(current, changes)
            updated["revision"] = actual + 1
            _validate_stage_index(updated)
            _atomic_write_json(path, updated)
            self._append_journal(run_id, {
                "event": "run_committed", "revision": updated["revision"],
                "status": updated.get("status"), "at": int(time.time())})
            return updated

    def commit_stage_operation(
            self, run_id: str, *, expected_revision: int,
            operation_id: str, request_fingerprint: str, mutate,
            validate_authority=None) -> dict:
        """Commit one exactly-once v4 lifecycle mutation under the run lock.

        `mutate` receives a private current-manifest copy while the lock is
        held and returns ``{"changes": ..., "receipt": ...}``.  It may write
        immutable objects before returning; those objects are harmless unless
        this single manifest commit indexes them.
        """
        run_id = _run_id(run_id)
        operation_id = _operation_id(operation_id, "operation id")
        request_fingerprint = _fingerprint(
            request_fingerprint, "request fingerprint")
        if not callable(mutate):
            raise StageStateError("stage operation mutate must be callable")
        if validate_authority is not None and not callable(validate_authority):
            raise StageStateError("authority validator must be callable")
        path = self._manifest_path(run_id)
        with _lock(path):
            current = self._load_manifest(run_id)
            current = self._relay_all_stage_journal_outbox_locked(
                run_id, current)
            if current.get("run_id") != run_id:
                raise StageStateError("run manifest identity mismatch")
            operations = current.get("stage_operations")
            if operations is not None and not isinstance(operations, dict):
                raise StageStateError("stage_operations must be an object")
            previous = (operations or {}).get(operation_id)
            if previous is not None:
                actual = int(current.get("revision") or 0)
                checked_previous = _validate_operation_receipt(
                    previous, operation_id=operation_id,
                    manifest_revision=actual)
                if checked_previous.get("request_fingerprint") != \
                        request_fingerprint:
                    raise OperationConflict(
                        f"operation id {operation_id} was reused with "
                        "a different request fingerprint")
                checked = _current_stage_index(current)
                self._relay_stage_journal_outbox_locked(
                    run_id, checked, operation_id)
                return checked_previous

            actual = int(current.get("revision") or 0)
            if actual != int(expected_revision):
                raise RevisionConflict(
                    f"run {run_id} revision is {actual}, expected "
                    f"{expected_revision}")
            if validate_authority is None:
                raise StageStateError(
                    "stage operation requires authority revalidation")

            checked = _current_stage_index(current)
            if validate_authority is not None:
                validate_authority(copy.deepcopy(checked))
            result = mutate(copy.deepcopy(checked))
            if not isinstance(result, dict) or set(result) != {
                    "changes", "receipt"}:
                raise StageStateError(
                    "stage mutate must return changes and receipt")
            changes = result.get("changes")
            if not isinstance(changes, dict):
                raise StageStateError("stage operation changes must be an object")
            invalid_changes = set(changes) - _STAGE_MUTATION_KEYS
            if invalid_changes:
                raise StageStateError(
                    "stage operation cannot change fields: " +
                    ", ".join(sorted(invalid_changes)))
            missing_changes = _STAGE_MUTATION_KEYS - set(changes)
            if missing_changes:
                raise StageStateError(
                    "stage operation must replace fields: " +
                    ", ".join(sorted(missing_changes)))

            updated = copy.deepcopy(checked)
            for key, value in changes.items():
                if key in {"stage_heads", "lineage",
                           "active_stage_projection"}:
                    updated[key] = copy.deepcopy(value)
                elif isinstance(value, dict) and \
                        isinstance(updated.get(key), dict):
                    updated[key] = _merge(updated[key], value)
                else:
                    updated[key] = copy.deepcopy(value)

            old_lineage = _validate_lineage(checked.get("lineage"))
            new_lineage = _validate_lineage(updated.get("lineage"))
            if new_lineage[:len(old_lineage)] != old_lineage:
                raise StageStateError("stage lineage is immutable and append-only")
            updated["revision"] = actual + 1
            _validate_stage_index(updated)
            receipt = _operation_receipt(
                result.get("receipt"), operation_id=operation_id,
                request_fingerprint=request_fingerprint,
                committed_revision=updated["revision"])
            old_heads = checked["stage_heads"]
            new_heads = updated["stage_heads"]
            removed_heads = set(old_heads) - set(new_heads)
            if removed_heads:
                raise StageStateError("stage heads are immutable and cannot be removed")
            changed_heads = sorted(
                stage_id for stage_id, head in new_heads.items()
                if old_heads.get(stage_id) != head)
            stage_entities = _stage_entities_module()
            for changed_stage_id in changed_heads:
                changed_head = new_heads[changed_stage_id]
                indexed_stage = self.read_stage_object(
                    run_id, changed_head["object"])
                expected_summary = stage_entities.bounded_stage_summary(
                    indexed_stage)
                if expected_summary != changed_head["summary"]:
                    raise StageStateError(
                        "stage head summary does not match immutable object")
            if receipt["operation"] == "resume_stage":
                if changed_heads or len(receipt["stage_ids"]) != 1:
                    raise StageStateError(
                        "resume receipt must bind one unchanged stage head")
                resumed_id = receipt["stage_ids"][0]
                resumed = new_heads.get(resumed_id)
                if resumed is None or \
                        resumed["summary"]["state"] != "active" or \
                        new_lineage != old_lineage or \
                        updated["active_stage_projection"] != \
                        checked["active_stage_projection"]:
                    raise StageStateError(
                        "resume may only claim an active stage attempt")
            elif receipt["stage_ids"] != changed_heads:
                raise StageStateError(
                    "stage operation receipt does not bind changed heads")
            for stage_id in set(old_heads) & set(changed_heads):
                old_summary = old_heads[stage_id]["summary"]
                new_summary = new_heads[stage_id]["summary"]
                if old_summary["state"] == "terminal":
                    raise StageStateError("terminal stage head is immutable")
                if new_summary["aggregate_revision"] != \
                        old_summary["aggregate_revision"] + 1:
                    raise StageStateError(
                        "stage aggregate revision must advance exactly once")
            updated["stage_operations"] = copy.deepcopy(
                checked.get("stage_operations") or {})
            updated["stage_operations"][operation_id] = receipt
            updated["stage_journal_outbox"] = copy.deepcopy(
                checked.get("stage_journal_outbox") or {})
            updated["stage_journal_outbox"][operation_id] = {
                "event": {
                    "event": "stage_operation_committed",
                    "operation": receipt["operation"],
                    "operation_id": operation_id,
                    "request_fingerprint": request_fingerprint,
                    "revision": updated["revision"],
                    "stage_ids": receipt["stage_ids"],
                    "at": int(time.time()),
                },
                "delivered": False,
            }
            _validate_stage_index(updated)
            _atomic_write_json(path, updated)
            self._relay_stage_journal_outbox_locked(
                run_id, updated, operation_id)
            return copy.deepcopy(receipt)

    def rebuild_active_stage_projection(
            self, run_id: str, *, expected_revision: int,
            foreground_stage_id: str | None = None,
            operation_id: str | None = None) -> dict:
        """Repair the non-authoritative active projection under one lock."""
        run_id = _run_id(run_id)
        path = self._manifest_path(run_id)
        with _lock(path):
            current = self._load_manifest(run_id)
            if current.get("run_id") != run_id:
                raise StageStateError("run manifest identity mismatch")
            checked = copy.deepcopy(current)
            heads = checked.get("stage_heads")
            expected = _canonical_active_projection(
                heads, foreground_stage_id)
            # This operation exists specifically to recover a missing,
            # corrupt, or stale non-authoritative projection. Validate every
            # other v4 authority against the canonical replacement before
            # revision/replay decisions; validating ``current`` here makes
            # the repair path impossible to enter. Do not persist this
            # candidate until the revision check and receipt are complete.
            repairable = copy.deepcopy(checked)
            repairable["active_stage_projection"] = expected
            _validate_stage_index(repairable)
            repair_id = (_operation_id(operation_id, "operation id")
                         if operation_id is not None else
                         f"projection-repair:{int(expected_revision)}")
            request = hashlib.sha256(_canonical_json_bytes({
                "operation": "rebuild_active_stage_projection",
                "run_id": run_id,
                "projection": expected,
            })).hexdigest()
            operations = checked.get("stage_operations")
            if not isinstance(operations, dict):
                raise StageStateError("stage_operations must be an object")
            previous = operations.get(repair_id)
            if previous is not None:
                checked_previous = _validate_operation_receipt(
                    previous, operation_id=repair_id,
                    manifest_revision=int(current.get("revision") or 0))
                if checked_previous.get("request_fingerprint") != request:
                    raise OperationConflict(
                        f"operation id {repair_id} was reused with different input")
                if checked.get("active_stage_projection") != expected:
                    raise StageStateError(
                        "committed projection repair result is not present")
                relayed = self._relay_all_stage_journal_outbox_locked(
                    run_id, checked)
                return copy.deepcopy(relayed)

            actual = int(current.get("revision") or 0)
            if actual != int(expected_revision):
                raise RevisionConflict(
                    f"run {run_id} revision is {actual}, expected "
                    f"{expected_revision}")
            if current.get("active_stage_projection") == expected:
                _validate_stage_index(current)
                relayed = self._relay_all_stage_journal_outbox_locked(
                    run_id, current)
                return copy.deepcopy(relayed)

            updated = repairable
            updated["revision"] = actual + 1
            receipt = _operation_receipt({
                "operation": "rebuild_active_stage_projection",
                "stage_ids": expected["active_stage_ids"],
                "result": {"projection": expected},
            }, operation_id=repair_id, request_fingerprint=request,
                committed_revision=updated["revision"])
            updated["stage_operations"] = copy.deepcopy(operations)
            updated["stage_operations"][repair_id] = receipt
            updated["stage_journal_outbox"] = copy.deepcopy(
                checked.get("stage_journal_outbox") or {})
            updated["stage_journal_outbox"][repair_id] = {
                "event": {
                    "event": "stage_operation_committed",
                    "operation": receipt["operation"],
                    "operation_id": repair_id,
                    "request_fingerprint": request,
                    "revision": updated["revision"],
                    "stage_ids": receipt["stage_ids"],
                    "at": int(time.time()),
                },
                "delivered": False,
            }
            _validate_stage_index(updated)
            _atomic_write_json(path, updated)
            relayed = self._relay_stage_journal_outbox_locked(
                run_id, updated, repair_id)
            return copy.deepcopy(relayed)

    def put_stage_object(self, run_id: str, stage: dict) -> dict:
        """Write one validated content-addressed stage object create-once."""
        run_id = _run_id(run_id)
        stage_entities = _stage_entities_module()

        checked = stage_entities.validate_stage(copy.deepcopy(stage))
        if not isinstance(checked, dict):
            raise StageStateError("validated stage must be an object")
        if checked.get("run_id") != run_id:
            raise StageStateError("stage object belongs to another run")
        stage_id = _nonempty_text(checked.get("stage_id"), "stage id")
        fingerprint = stage_entities.stage_fingerprint(checked)
        if checked.get("fingerprint") != fingerprint:
            raise StageStateError("stage object fingerprint mismatch")
        path = storage.stage_object_path_for_run(
            self.home, run_id, stage_id, fingerprint)
        parent = storage.ensure_stage_object_parent_for_run(
            self.home, run_id, stage_id, fingerprint)
        # Re-resolve after safe component creation so a raced link/type change
        # is rejected by the canonical storage boundary before any leaf write.
        path = storage.stage_object_path_for_run(
            self.home, run_id, stage_id, fingerprint)
        payload = _canonical_json_bytes(checked) + b"\n"
        if len(payload) > _MAX_STAGE_RECEIPT_BYTES:
            raise StageStateError("stage object exceeds its persisted bound")
        digest = hashlib.sha256(payload).hexdigest()
        leaf = os.path.basename(path)
        temporary = f".{fingerprint}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with _confined_directory_fd(self.home, parent) as parent_fd:
            try:
                try:
                    descriptor = os.open(
                        temporary, write_flags, 0o600, dir_fd=parent_fd)
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    try:
                        os.link(
                            temporary, leaf, src_dir_fd=parent_fd,
                            dst_dir_fd=parent_fd, follow_symlinks=False)
                    except FileExistsError:
                        # Existing immutable objects are idempotent only when
                        # their exact stored bytes match the canonical value.
                        try:
                            read_descriptor = os.open(
                                leaf, os.O_RDONLY | os.O_NOFOLLOW,
                                dir_fd=parent_fd)
                            with os.fdopen(read_descriptor, "rb") as handle:
                                if not stat.S_ISREG(
                                        os.fstat(handle.fileno()).st_mode):
                                    raise StageStateError(
                                        "stage object path is not a regular file")
                                existing = handle.read(len(payload) + 1)
                        except OSError as exc:
                            raise StageStateError(
                                "stage object is unavailable") from exc
                        if existing != payload:
                            raise StageStateError(
                                "stage object path contains different bytes")
                    os.fsync(parent_fd)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass
            except OSError as exc:
                raise StageStateError(
                    "stage object could not be stored") from exc
        # Confirm that the pinned directory is still the canonical locator.
        checked_path = storage.stage_object_path_for_run(
            self.home, run_id, stage_id, fingerprint)
        if checked_path != path:
            raise StageStateError("stage object path changed after write")
        return {
            "schema": "taskplane.stage-object-ref/v1",
            "stage_id": stage_id,
            "fingerprint": fingerprint,
            "digest": digest,
            "bytes": len(payload),
            "locator": (
                f"stages/objects/{stage_id}/{fingerprint}.json"),
        }

    def read_stage_object(self, run_id: str, reference: dict) -> dict:
        """Read and fully verify one immutable stage-object reference."""
        run_id = _run_id(run_id)
        if not isinstance(reference, dict) or set(reference) != {
                "schema", "stage_id", "fingerprint", "digest", "bytes",
                "locator"}:
            raise StageStateError("stage object reference fields are invalid")
        if reference.get("schema") != "taskplane.stage-object-ref/v1":
            raise StageStateError("stage object reference schema is invalid")
        stage_id = _stage_id(reference.get("stage_id"), "stage id")
        fingerprint = _fingerprint(
            reference.get("fingerprint"), "stage fingerprint")
        digest = _fingerprint(reference.get("digest"), "stage digest")
        byte_count = reference.get("bytes")
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or \
                byte_count <= 0 or byte_count > _MAX_STAGE_RECEIPT_BYTES:
            raise StageStateError("stage object byte count is invalid")
        expected_locator = (
            f"stages/objects/{stage_id}/{fingerprint}.json")
        if reference.get("locator") != expected_locator:
            raise StageStateError("stage object locator mismatch")
        path = storage.stage_object_path_for_run(
            self.home, run_id, stage_id, fingerprint)
        parent = os.path.dirname(path)
        leaf = os.path.basename(path)
        with _confined_directory_fd(self.home, parent) as parent_fd:
            try:
                with os.fdopen(os.open(
                        leaf, os.O_RDONLY | os.O_NOFOLLOW,
                        dir_fd=parent_fd), "rb") as handle:
                    metadata = os.fstat(handle.fileno())
                    if not stat.S_ISREG(metadata.st_mode):
                        raise StageStateError(
                            "stage object path is not a regular file")
                    payload = handle.read(byte_count + 1)
            except OSError as exc:
                raise StageStateError("stage object is unavailable") from exc
        if len(payload) != byte_count or metadata.st_size != byte_count:
            raise StageStateError("stage object byte count mismatch")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise StageStateError("stage object digest mismatch")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise StageStateError("stage object JSON is invalid") from exc
        if _canonical_json_bytes(value) + b"\n" != payload:
            raise StageStateError("stage object bytes are not canonical")
        stage_entities = _stage_entities_module()
        checked = stage_entities.validate_stage(value)
        if checked.get("run_id") != run_id or \
                checked.get("stage_id") != stage_id or \
                checked.get("fingerprint") != fingerprint:
            raise StageStateError("stage object identity mismatch")
        return copy.deepcopy(checked)

    def claim_stage_execution_root(
            self, run_id: str, *, stage_id: str, execution_root_id: str,
            attempt_id: str | None = None) -> dict:
        """Create-once claim an isolated stage execution root."""
        return storage.claim_stage_execution_root_for_run(
            self.home, _run_id(run_id), _stage_id(stage_id),
            _stage_id(execution_root_id, "execution root id"),
            attempt_id=(_stage_id(attempt_id, "attempt id")
                        if attempt_id is not None else None))





    def register_checkout(self, identity: storage.RepositoryIdentity, *,
                          checkout: str, source: str) -> dict:
        """Register a non-authoritative checkout alias without moving it."""
        layout = storage.resolve_layout(
            identity, home=self.home, run_id="checkout-registration")
        path = layout.repository_record
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _lock(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    record = json.load(handle)
            except FileNotFoundError:
                record = {
                    "schema": "taskplane.repository/v1",
                    "repository": identity.to_dict(),
                    "repository_key": identity.key, "checkouts": []}
            except (OSError, ValueError) as exc:
                raise RunStoreError(
                    f"repository record is unavailable: {path}") from exc
            if record.get("schema") != "taskplane.repository/v1" or \
                    (record.get("repository") or {}).get("repo_id") != \
                    identity.repo_id:
                raise RunStoreError("repository record identity mismatch")
            rows = list(record.get("checkouts") or [])
            row = {"path": os.path.realpath(checkout),
                   "source": str(source)}
            if row not in rows:
                rows.append(row)
            record["checkouts"] = sorted(rows, key=lambda item: (
                str(item.get("path")), str(item.get("source"))))
            _atomic_write_json(path, record)
            return record

    def reference_command(self, run_id: str, *, expected_revision: int,
                          handle: str,
                          wave_id: str | None = None) -> dict:
        """Revision-check and retain opaque command/wave references.

        The run manifest never stores argv, environment, output, host process
        identifiers, or authorization material. Those remain owned by the
        command runtime's bound record.
        """
        current = self.load(run_id)
        commands = copy.deepcopy(current.get("commands") or {
            "handles": [], "waves": [],
        })
        handles = list(commands.get("handles") or [])
        if str(handle) not in handles:
            handles.append(str(handle))
        waves = list(commands.get("waves") or [])
        if wave_id is not None and str(wave_id) not in waves:
            waves.append(str(wave_id))
        return self.commit(run_id, expected_revision=expected_revision,
                           changes={"commands": {
                               "handles": handles, "waves": waves,
                           }})
