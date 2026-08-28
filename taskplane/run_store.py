"""Atomic, revision-checked owner for one taskPlane run."""
from __future__ import annotations

from contextlib import contextmanager
import copy
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
import taskplane_lite as tp


class RunStoreError(RuntimeError):
    pass


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


_RUN_SCHEMAS = frozenset({"taskplane.run/v3", "taskplane.run/v4"})
_STAGE_INDEX_KEYS = frozenset({
    "stage_heads", "lineage", "stage_operations",
    "active_stage_projection", "stage_journal_outbox",
})
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
    "migrate_singleton",
    "rebuild_active_stage_projection",
})
_STAGE_JOURNAL_EVENT_FIELDS = frozenset({
    "event", "operation", "operation_id", "request_fingerprint",
    "revision", "stage_ids", "at",
})


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


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


def _promote_stage_index(current: dict) -> dict:
    promoted = copy.deepcopy(current)
    schema = promoted.get("schema")
    if schema not in _RUN_SCHEMAS:
        raise RunStoreError("run manifest schema is unsupported")
    if schema == "taskplane.run/v3":
        if set(promoted) & _STAGE_INDEX_KEYS:
            raise StageStateError(
                "v3 run manifest cannot preseed stage indexes")
        promoted["schema"] = "taskplane.run/v4"
        promoted["stage_heads"] = {}
        promoted["lineage"] = []
        promoted["stage_operations"] = {}
        promoted["stage_journal_outbox"] = {}
        promoted["active_stage_projection"] = \
            _canonical_active_projection(promoted["stage_heads"])
    elif schema == "taskplane.run/v4":
        # v4 existed before journal delivery became a persisted projection.
        promoted.setdefault("stage_journal_outbox", {})
    return promoted


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
    if manifest.get("schema") != "taskplane.run/v4":
        raise StageStateError("stage operations require taskplane.run/v4")
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


def _atomic_write_json(path: str, value: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


@contextmanager
def _lock(path: str):
    try:
        with tp.file_lock(path, timeout=10.0):
            yield
    except tp.StateError as exc:
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

    def __init__(self, *, home: str | None = None):
        ensure_stage_compatibility()
        self.home = storage.taskplane_home(home)

    def _manifest_path(self, run_id: str) -> str:
        return os.path.join(self.home, "runs", _run_id(run_id),
                            "manifest.json")

    def _journal_path(self, run_id: str) -> str:
        return os.path.join(self.home, "runs", _run_id(run_id),
                            "journal.jsonl")

    def _append_journal(self, run_id: str, event: dict) -> None:
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
                                      event: dict) -> bool:
        rows = self._read_journal_rows_locked(run_id)
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
            operation_id: str) -> dict:
        """Deliver and acknowledge one receipt-bound journal event."""
        outbox = manifest.get("stage_journal_outbox") or {}
        entry = outbox.get(operation_id)
        if entry is None or entry.get("delivered") is True:
            return manifest
        event = copy.deepcopy(entry["event"])
        if not self._journal_contains_stage_event(run_id, event):
            self._append_journal(run_id, event)
        delivered = copy.deepcopy(manifest)
        delivered["stage_journal_outbox"][operation_id]["delivered"] = True
        _validate_stage_index(delivered)
        _atomic_write_json(self._manifest_path(run_id), delivered)
        return delivered

    def _relay_all_stage_journal_outbox_locked(
            self, run_id: str, manifest: dict) -> dict:
        """Sweep every committed undelivered event under the run lock."""
        if manifest.get("schema") != "taskplane.run/v4":
            return manifest
        _validate_stage_index(manifest)
        relayed = manifest
        for operation_id in sorted(
                (manifest.get("stage_journal_outbox") or {}).keys()):
            relayed = self._relay_stage_journal_outbox_locked(
                run_id, relayed, operation_id)
        return relayed

    def _load_manifest(self, run_id: str) -> dict:
        path = self._manifest_path(run_id)
        try:
            with open(path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RunStoreError(f"run manifest is unavailable: {run_id}") \
                from exc
        if not isinstance(value, dict) or value.get("schema") not in \
                _RUN_SCHEMAS:
            raise RunStoreError(f"run manifest is invalid: {run_id}")
        return value

    def create(self, identity: storage.RepositoryIdentity, *, run_id: str,
               checkout: str, host: dict, target: dict) -> dict:
        layout = storage.resolve_layout(identity, home=self.home,
                                        run_id=run_id)
        path = self._manifest_path(run_id)
        os.makedirs(layout.run_root, exist_ok=True)
        with _lock(path):
            if os.path.exists(path):
                raise RunStoreError(f"run already exists: {run_id}")
            repository = identity.to_dict()
            repository["checkout"] = os.path.realpath(checkout)
            manifest = {
                "schema": "taskplane.run/v3",
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
            }
            _atomic_write_json(path, manifest)
            _atomic_write_json(layout.repository_record, {
                "schema": "taskplane.repository/v1",
                "repository": identity.to_dict(),
                "repository_key": identity.key,
            })
            self._append_journal(run_id, {
                "event": "run_created", "revision": 1,
                "status": "preflight", "at": int(time.time())})
            return manifest

    def load(self, run_id: str) -> dict:
        run_id = _run_id(run_id)
        path = self._manifest_path(run_id)
        with _lock(path):
            value = self._load_manifest(run_id)
            return self._relay_all_stage_journal_outbox_locked(
                run_id, value)

    def commit(self, run_id: str, *, expected_revision: int,
               changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise RunStoreError("run changes must be an object")
        forbidden = set(changes) & ({"schema"} | _STAGE_INDEX_KEYS)
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
            if current.get("schema") == "taskplane.run/v4":
                _validate_stage_index(current)
            updated = _merge(current, changes)
            updated["revision"] = actual + 1
            if updated.get("schema") == "taskplane.run/v4":
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
                promoted = _promote_stage_index(current)
                self._relay_stage_journal_outbox_locked(
                    run_id, promoted, operation_id)
                return checked_previous

            actual = int(current.get("revision") or 0)
            if actual != int(expected_revision):
                raise RevisionConflict(
                    f"run {run_id} revision is {actual}, expected "
                    f"{expected_revision}")
            if validate_authority is None:
                raise StageStateError(
                    "stage operation requires authority revalidation")

            promoted = _promote_stage_index(current)
            # A corrupt v4 cache blocks normal lifecycle dispatch.  The
            # dedicated repair method is the sole recovery path.
            if current.get("schema") == "taskplane.run/v4":
                _validate_stage_index(promoted)
            if validate_authority is not None:
                validate_authority(copy.deepcopy(promoted))
            result = mutate(copy.deepcopy(promoted))
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

            updated = copy.deepcopy(promoted)
            for key, value in changes.items():
                if key in {"stage_heads", "lineage",
                           "active_stage_projection"}:
                    updated[key] = copy.deepcopy(value)
                elif isinstance(value, dict) and \
                        isinstance(updated.get(key), dict):
                    updated[key] = _merge(updated[key], value)
                else:
                    updated[key] = copy.deepcopy(value)

            old_lineage = _validate_lineage(promoted.get("lineage"))
            new_lineage = _validate_lineage(updated.get("lineage"))
            if new_lineage[:len(old_lineage)] != old_lineage:
                raise StageStateError("stage lineage is immutable and append-only")
            updated["revision"] = actual + 1
            _validate_stage_index(updated)
            receipt = _operation_receipt(
                result.get("receipt"), operation_id=operation_id,
                request_fingerprint=request_fingerprint,
                committed_revision=updated["revision"])
            old_heads = promoted["stage_heads"]
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
                        promoted["active_stage_projection"]:
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
                promoted.get("stage_operations") or {})
            updated["stage_operations"][operation_id] = receipt
            updated["stage_journal_outbox"] = copy.deepcopy(
                promoted.get("stage_journal_outbox") or {})
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
            promoted = _promote_stage_index(current)
            heads = promoted.get("stage_heads")
            expected = _canonical_active_projection(
                heads, foreground_stage_id)
            # This operation exists specifically to recover a missing,
            # corrupt, or stale non-authoritative projection. Validate every
            # other v4 authority against the canonical replacement before
            # revision/replay decisions; validating ``current`` here makes
            # the repair path impossible to enter. Do not persist this
            # candidate until the revision check and receipt are complete.
            repairable = copy.deepcopy(promoted)
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
            operations = promoted.get("stage_operations")
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
                if promoted.get("active_stage_projection") != expected:
                    raise StageStateError(
                        "committed projection repair result is not present")
                relayed = self._relay_all_stage_journal_outbox_locked(
                    run_id, promoted)
                return copy.deepcopy(relayed)

            actual = int(current.get("revision") or 0)
            if actual != int(expected_revision):
                raise RevisionConflict(
                    f"run {run_id} revision is {actual}, expected "
                    f"{expected_revision}")
            if current.get("schema") == "taskplane.run/v4" and \
                    current.get("active_stage_projection") == expected:
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
                promoted.get("stage_journal_outbox") or {})
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

    def record_enforcement_decision(
            self, run_id: str, *, expected_revision: int,
            decision: dict) -> dict:
        """Atomically retain the canonical enforcement decision for a run."""
        import enforcement

        checked = enforcement.validate_decision(decision)
        if checked.get("run_id") not in (None, str(run_id)):
            raise RunStoreError("enforcement decision belongs to another run")
        current = self.load(run_id)
        if int(current.get("revision") or 0) != int(expected_revision):
            raise RevisionConflict(
                f"run {run_id} revision is {current.get('revision')}, "
                f"expected {expected_revision}")
        history = list((current.get("enforcement") or {}).get("history") or [])
        if not history or history[-1].get("evidence_id") != \
                checked.get("evidence_id"):
            history.append(copy.deepcopy(checked))
        # Bound the projection while preserving the complete journaled writes.
        history = history[-64:]
        return self.commit(
            run_id, expected_revision=expected_revision,
            changes={"enforcement": {
                "schema": "taskplane.run-enforcement/v1",
                "current": checked,
                "history": history,
            }})

    def record_foreign_interference(
            self, run_id: str, *, expected_revision: int,
            interference: dict) -> dict:
        """Atomically persist the bounded foreign-interference authority."""
        import collision

        checked = collision.validate_ledger(interference)
        if checked.get("run_id") not in (None, str(run_id)):
            raise RunStoreError("foreign interference belongs to another run")
        return self.commit(
            run_id, expected_revision=expected_revision,
            changes={"foreign_interference": checked})

    def record_task_merge(self, run_id: str, *, expected_revision: int,
                          receipt: dict) -> dict:
        import worktree_cleanup
        checked = worktree_cleanup.validate_merge_receipt(receipt)
        if checked.get("run_id") != str(run_id):
            raise RunStoreError("task merge receipt belongs to another run")
        return self.commit(
            run_id, expected_revision=expected_revision,
            changes={"task_merges": {
                str(checked["task_id"]): checked}})

    def record_worktree_cleanup(self, run_id: str, *, expected_revision: int,
                                cleanup: dict) -> dict:
        import worktree_cleanup
        checked = worktree_cleanup.validate_cleanup_record(cleanup)
        if checked.get("run_id") != str(run_id):
            raise RunStoreError("worktree cleanup belongs to another run")
        return self.commit(
            run_id, expected_revision=expected_revision,
            changes={"worktree_cleanups": {
                str(checked["task_id"]): checked}})

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
