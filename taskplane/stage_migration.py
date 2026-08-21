"""Non-destructive migration of legacy singleton loop state to v4 stages.

The source snapshot is retained byte-for-byte before any v4 index points at a
projection.  Classification is deliberately narrow: a legacy value which
cannot be mapped without inference becomes an immutable ``legacy-unknown``
sentinel and never a guessed active or terminal stage.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import base64
import copy
import hashlib
import json
import os
import posixpath
import re
import stat
from typing import Final

if __package__:
    from . import review_evidence
    from . import run_store as run_store_module
    from . import stage_entities
    from . import storage
else:  # pragma: no cover - direct script import mode
    import review_evidence
    import run_store as run_store_module
    import stage_entities
    import storage


SOURCE_SCHEMA: Final[str] = "taskplane.legacy-source-bundle/v1"
CONSERVATION_SCHEMA: Final[str] = "taskplane.legacy-conservation/v1"
UNKNOWN_SCHEMA: Final[str] = "taskplane.legacy-unknown/v1"
MIGRATION_INPUT_SCHEMA: Final[str] = "taskplane.legacy-migration-input/v1"
PROJECTION_SCHEMA: Final[str] = "taskplane.legacy-stage-projection/v1"
MIGRATION_OPERATION: Final[str] = "migrate_singleton"
MAX_LEGACY_SOURCES: Final[int] = 4096
MAX_LEGACY_SOURCE_BYTES: Final[int] = 64 * 1024 * 1024
_FINGERPRINT: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_KNOWN_ACTIVE_STEPS: Final[dict[str, str]] = {
    "pm": "product",
    "design": "design",
    "design_approval": "design",
    "plan": "plan",
    "plan_approval": "plan",
    "execute": "build",
    "fix": "build",
    "evaluate": "evaluate",
    "selection": "evaluate",
    "em": "engineering",
    "signoff": "engineering",
    "retro": "retro",
    "escalated": "engineering",
}
_COLLECTION_KEYS: Final[dict[str, str]] = {
    "requirement": "requirements",
    "requirements": "requirements",
    "task": "tasks",
    "tasks": "tasks",
    "decision": "decisions",
    "decisions": "decisions",
    "evidence": "evidence",
    "evidences": "evidence",
    "commit": "commits",
    "commits": "commits",
    "review": "reviews",
    "reviews": "reviews",
    "audit": "audit_history",
    "audit_history": "audit_history",
}
_COLLECTIONS: Final[tuple[str, ...]] = (
    "requirements", "tasks", "decisions", "evidence", "commits",
    "reviews", "audit_history",
)


class MigrationError(RuntimeError):
    """A legacy singleton cannot be migrated safely."""


class MigrationIntegrityError(MigrationError):
    """Retained source, sentinel, receipt, or projection failed verification."""


def _fingerprint(value: object) -> str:
    return review_evidence.content_fingerprint(value)


def _without_fingerprint(value: Mapping[str, object]) -> dict[str, object]:
    return {str(key): copy.deepcopy(item) for key, item in value.items()
            if key != "fingerprint"}


def _source_name(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or \
            value.startswith("/"):
        raise MigrationIntegrityError("legacy source name is not portable")
    normalized = posixpath.normpath(value)
    if normalized != value or normalized in {".", ".."} or \
            normalized.startswith("../"):
        raise MigrationIntegrityError("legacy source name is not portable")
    return normalized


def _record_values(value: object, rows: dict[str, list[object]]) -> None:
    """Collect named record classes without changing their JSON values."""
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            category = _COLLECTION_KEYS.get(key)
            is_collection = key in _COLLECTIONS or key in {
                "requirements", "tasks", "decisions", "commits", "reviews",
            }
            is_record = isinstance(child, (dict, list)) or key == "commit"
            if category is not None and (is_collection or is_record):
                values = child if isinstance(child, list) else [child]
                rows[category].extend(copy.deepcopy(values))
            # A named collection owns the direct values; still descend so
            # nested reviews/evidence/commits in task records are conserved.
            _record_values(child, rows)
    elif isinstance(value, list):
        for child in value:
            _record_values(child, rows)


def _conservation(sources: Mapping[str, bytes]) -> dict[str, object]:
    rows: dict[str, list[object]] = {name: [] for name in _COLLECTIONS}
    for name in sorted(sources):
        raw = sources[name]
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        _record_values(value, rows)
        # A standalone requirement record is identified by its retained
        # source class even though it has no wrapping `requirements` key.
        if name.startswith("requirements/") and isinstance(value, dict):
            rows["requirements"].append(copy.deepcopy(value))
    body: dict[str, object] = {"schema": CONSERVATION_SCHEMA}
    for name in _COLLECTIONS:
        fingerprints = sorted(_fingerprint(row) for row in rows[name])
        body[name] = {
            "count": len(fingerprints), "fingerprints": fingerprints,
        }
    body["fingerprint"] = _fingerprint(body)
    return body


def retain_legacy_sources(
        sources: Mapping[str, bytes | bytearray | memoryview],
        ) -> dict[str, object]:
    """Return a canonical bundle which embeds every exact source byte."""
    if not isinstance(sources, Mapping) or not sources:
        raise MigrationIntegrityError("legacy sources must not be empty")
    if len(sources) > MAX_LEGACY_SOURCES:
        raise MigrationIntegrityError("legacy source count exceeds its bound")
    checked: dict[str, bytes] = {}
    total = 0
    for raw_name, value in sources.items():
        name = _source_name(raw_name)
        if name in checked:
            raise MigrationIntegrityError("legacy source names are duplicated")
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise MigrationIntegrityError("legacy source value must be bytes")
        raw = bytes(value)
        total += len(raw)
        if total > MAX_LEGACY_SOURCE_BYTES:
            raise MigrationIntegrityError("legacy source bytes exceed their bound")
        checked[name] = raw
    source_rows = [{
        "name": name,
        "bytes": len(checked[name]),
        "sha256": hashlib.sha256(checked[name]).hexdigest(),
        "base64": base64.b64encode(checked[name]).decode("ascii"),
    } for name in sorted(checked)]
    body: dict[str, object] = {
        "schema": SOURCE_SCHEMA,
        "sources": source_rows,
        "source_bytes": total,
        "conservation": _conservation(checked),
    }
    body["fingerprint"] = _fingerprint(body)
    return body


def _sources_from_bundle(bundle: Mapping[str, object]) -> dict[str, bytes]:
    if not isinstance(bundle, Mapping) or bundle.get("schema") != SOURCE_SCHEMA:
        raise MigrationIntegrityError("legacy source bundle schema is invalid")
    if set(bundle) != {
            "schema", "sources", "source_bytes", "conservation",
            "fingerprint"}:
        raise MigrationIntegrityError("legacy source bundle fields are invalid")
    source_rows = bundle.get("sources")
    if not isinstance(source_rows, list) or not source_rows or \
            len(source_rows) > MAX_LEGACY_SOURCES:
        raise MigrationIntegrityError("legacy source rows are invalid")
    sources: dict[str, bytes] = {}
    total = 0
    prior = ""
    for row in source_rows:
        if not isinstance(row, dict) or set(row) != {
                "name", "bytes", "sha256", "base64"}:
            raise MigrationIntegrityError("legacy source row is invalid")
        name = _source_name(row.get("name"))
        if name <= prior:
            raise MigrationIntegrityError("legacy source rows are not canonical")
        prior = name
        try:
            raw = base64.b64decode(str(row.get("base64") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise MigrationIntegrityError(
                "legacy source base64 is invalid") from exc
        if isinstance(row.get("bytes"), bool) or row.get("bytes") != len(raw) or \
                row.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise MigrationIntegrityError("legacy source bytes do not verify")
        total += len(raw)
        sources[name] = raw
    if total != bundle.get("source_bytes") or total > MAX_LEGACY_SOURCE_BYTES:
        raise MigrationIntegrityError("legacy source total does not verify")
    expected_conservation = _conservation(sources)
    if bundle.get("conservation") != expected_conservation:
        raise MigrationIntegrityError("legacy conservation does not verify")
    if bundle.get("fingerprint") != _fingerprint(_without_fingerprint(bundle)):
        raise MigrationIntegrityError("legacy source fingerprint mismatch")
    return sources


def verify_retained_sources(
        bundle: Mapping[str, object],
        sources: Mapping[str, bytes | bytearray | memoryview] | None = None,
        ) -> bool:
    """Verify a retained bundle, optionally against the original byte map."""
    retained = _sources_from_bundle(bundle)
    if sources is not None:
        supplied = {_source_name(name): bytes(raw)
                    for name, raw in sources.items()}
        if retained != supplied:
            raise MigrationIntegrityError(
                "retained legacy bytes differ from their source")
    return True


def legacy_unknown(bundle: Mapping[str, object], *, unknown_reason: str,
                   retained_source_ref: Mapping[str, object] | None = None,
                   ) -> dict[str, object]:
    """Create the immutable non-lifecycle sentinel for an ambiguous record."""
    verify_retained_sources(bundle)
    reason = str(unknown_reason or "").strip()
    if not reason or len(reason.encode("utf-8")) > 4096:
        raise MigrationIntegrityError("legacy unknown reason is invalid")
    body: dict[str, object] = {
        "schema": UNKNOWN_SCHEMA,
        "source_fingerprint": bundle["fingerprint"],
        "unknown_reason": reason,
        "conservation_fingerprint": bundle["conservation"]["fingerprint"],
        "retained_source_ref": (copy.deepcopy(retained_source_ref)
                                if retained_source_ref is not None else None),
    }
    body["fingerprint"] = _fingerprint(body)
    return body


def verify_legacy_unknown(sentinel: Mapping[str, object],
                          bundle: Mapping[str, object]) -> bool:
    verify_retained_sources(bundle)
    if not isinstance(sentinel, Mapping) or set(sentinel) != {
            "schema", "source_fingerprint", "unknown_reason",
            "conservation_fingerprint", "retained_source_ref",
            "fingerprint"} or sentinel.get("schema") != UNKNOWN_SCHEMA:
        raise MigrationIntegrityError("legacy unknown sentinel is invalid")
    reason = str(sentinel.get("unknown_reason") or "").strip()
    if not reason or reason != sentinel.get("unknown_reason") or \
            len(reason.encode("utf-8")) > 4096:
        raise MigrationIntegrityError("legacy unknown reason is invalid")
    if sentinel.get("source_fingerprint") != bundle.get("fingerprint") or \
            sentinel.get("conservation_fingerprint") != \
            bundle["conservation"]["fingerprint"] or \
            sentinel.get("fingerprint") != \
            _fingerprint(_without_fingerprint(sentinel)):
        raise MigrationIntegrityError("legacy unknown sentinel does not verify")
    if "state" in sentinel or "outcome" in sentinel:
        raise MigrationIntegrityError("legacy unknown is not a lifecycle state")
    return True


def _read_regular_file(path: str, *, root: str) -> bytes:
    absolute = os.path.abspath(path)
    authority_root = os.path.abspath(root)
    if os.path.commonpath((authority_root, absolute)) != authority_root:
        raise MigrationIntegrityError("legacy source escapes its state root")
    metadata = os.lstat(absolute)
    if not stat.S_ISREG(metadata.st_mode):
        raise MigrationIntegrityError("legacy source is not a regular file")
    descriptor = os.open(absolute, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or \
                (metadata.st_dev, metadata.st_ino) != \
                (opened.st_dev, opened.st_ino):
            raise MigrationIntegrityError("legacy source changed while opening")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read(MAX_LEGACY_SOURCE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def capture_legacy_sources(workspace: str) -> dict[str, bytes]:
    """Read the singleton loop, registry, and archived loops byte-for-byte."""
    # Import lazily to avoid making migration a second loop writer.
    if __package__:
        from . import loop
    else:  # pragma: no cover
        import loop
    state_root = os.path.abspath(loop.state_dir(workspace))
    live = loop._loop_path(workspace)
    legacy = loop._legacy_loop_path(workspace)
    sources: dict[str, bytes] = {}
    if os.path.exists(live):
        sources["loop.json"] = _read_regular_file(live, root=state_root)
    elif os.path.exists(legacy):
        workspace_root = os.path.abspath(workspace)
        sources["loop.json"] = _read_regular_file(
            legacy, root=workspace_root)
    registry = os.path.join(state_root, "tracks.json")
    if os.path.exists(registry):
        sources["tracks.json"] = _read_regular_file(
            registry, root=state_root)
    tracks_root = os.path.join(state_root, "tracks")
    if os.path.isdir(tracks_root) and not os.path.islink(tracks_root):
        for directory, names, files in os.walk(tracks_root, followlinks=False):
            names[:] = sorted(name for name in names
                              if not os.path.islink(os.path.join(directory, name)))
            for name in sorted(files):
                if name == "loop.json":
                    path = os.path.join(directory, name)
                    relative = os.path.relpath(
                        path, state_root).replace(os.sep, "/")
                    sources[_source_name(relative)] = _read_regular_file(
                        path, root=state_root)
    if not sources:
        raise MigrationIntegrityError("legacy singleton sources are unavailable")
    return sources


def _legacy_loop(bundle: Mapping[str, object]) -> tuple[dict | None, str | None]:
    sources = _sources_from_bundle(bundle)
    candidates = [name for name in sources if name == "loop.json"]
    if len(candidates) != 1:
        return None, "singleton_loop_missing_or_ambiguous"
    try:
        value = json.loads(sources[candidates[0]].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "singleton_loop_json_invalid"
    if not isinstance(value, dict):
        return None, "singleton_loop_not_an_object"
    step = str(value.get("step") or "").strip()
    if step not in _KNOWN_ACTIVE_STEPS and step != "done":
        return None, f"unrecognized_loop_step:{step or 'missing'}"
    tasks = value.get("tasks") or []
    if not isinstance(tasks, list) or len(tasks) > \
            stage_entities.MAX_COLLECTION_ITEMS:
        return None, "singleton_tasks_invalid_or_oversized"
    task_ids = [str(row.get("id") or "").strip()
                for row in tasks if isinstance(row, dict)]
    if len(task_ids) != len(tasks) or any(not item for item in task_ids) or \
            len(set(task_ids)) != len(task_ids):
        return None, "singleton_task_identity_ambiguous"
    return copy.deepcopy(value), None


def _migration_artifact_store(
        workspace: str, store: run_store_module.RunStore, run_id: str,
        ) -> review_evidence.ArtifactStore:
    root = os.path.join(store.home, "runs", run_id, "stages",
                        "migration-artifacts")
    return review_evidence.ArtifactStore(workspace, root=root)


def _portable(store: review_evidence.ArtifactStore,
              reference: dict[str, object]) -> dict[str, object]:
    return review_evidence.portable_artifact_reference(store, reference)


def _resolve_store(workspace: str, *,
                   store: run_store_module.RunStore | None,
                   run_id: str | None,
                   ) -> tuple[run_store_module.RunStore, str]:
    if store is not None and run_id:
        return store, str(run_id)
    locator = storage.load_workspace_locator(workspace)
    if locator is None:
        raise MigrationIntegrityError(
            "stage migration requires a managed run locator")
    resolved_run = str(run_id or locator.get("run_id") or "")
    resolved_store = store or run_store_module.RunStore(
        home=str(locator.get("home") or ""))
    if not resolved_run:
        raise MigrationIntegrityError("managed run id is unavailable")
    return resolved_store, resolved_run


def migrate_singleton(
        workspace: str, *, operation_id: str, expected_revision: int,
        authority: Mapping[str, object], requirement: Mapping[str, object],
        design: Mapping[str, object] | None, contracts: Iterable[str],
        created_at: str, store: run_store_module.RunStore | None = None,
        run_id: str | None = None,
        legacy_sources: Mapping[str, bytes | bytearray | memoryview] | None = None,
        authority_validator: Callable[[Mapping[str, object], Mapping[str, object]], None] | None = None,
        ) -> dict[str, object]:
    """Atomically index one deterministic known legacy projection.

    Ambiguous input is retained as a verified unknown sentinel in a zero-head
    migration receipt, without manufacturing a lifecycle value.
    """
    if authority_validator is None:
        raise MigrationIntegrityError(
            "migration requires an exact authority validator")
    resolved_store, resolved_run = _resolve_store(
        workspace, store=store, run_id=run_id)
    sources = (capture_legacy_sources(workspace)
               if legacy_sources is None else legacy_sources)
    bundle = retain_legacy_sources(sources)
    artifact_store = _migration_artifact_store(
        workspace, resolved_store, resolved_run)
    source_native = artifact_store.put(
        "legacy-source", bundle, fingerprint=str(bundle["fingerprint"]))
    source_ref = _portable(artifact_store, source_native)
    conservation = copy.deepcopy(bundle["conservation"])
    conservation_native = artifact_store.put(
        "legacy-conservation", conservation,
        fingerprint=str(conservation["fingerprint"]))
    conservation_ref = _portable(artifact_store, conservation_native)
    loop_state, unknown_reason = _legacy_loop(bundle)

    if unknown_reason is not None:
        sentinel = legacy_unknown(
            bundle, unknown_reason=unknown_reason,
            retained_source_ref=source_ref)
        sentinel_native = artifact_store.put(
            "legacy-unknown", sentinel,
            fingerprint=str(sentinel["fingerprint"]))
        sentinel_ref = _portable(artifact_store, sentinel_native)
        request = {
            "schema": "taskplane.legacy-migration-request/v1",
            "run_id": resolved_run,
            "operation_id": operation_id,
            "source_fingerprint": bundle["fingerprint"],
            "conservation_fingerprint": conservation["fingerprint"],
            "unknown_fingerprint": sentinel["fingerprint"],
            "authority": copy.deepcopy(authority),
        }

        def validate_unknown(current: Mapping[str, object]) -> None:
            authority_validator(
                copy.deepcopy(authority), copy.deepcopy(current))

        def retain_unknown(current: dict) -> dict:
            heads = copy.deepcopy(current.get("stage_heads") or {})
            if heads:
                raise MigrationIntegrityError(
                    "legacy migration requires an empty v4 stage index")
            lineage = copy.deepcopy(current.get("lineage") or [])
            projection = stage_entities.active_stage_projection(heads)
            return {
                "changes": {
                    "stage_heads": heads,
                    "lineage": lineage,
                    "active_stage_projection": projection,
                },
                "receipt": {
                    "operation": MIGRATION_OPERATION,
                    "stage_ids": [],
                    "result": {
                        "classification": "legacy-unknown",
                        "source_fingerprint": bundle["fingerprint"],
                        "source_ref": source_ref,
                        "conservation": conservation,
                        "conservation_ref": conservation_ref,
                        "unknown_reason": unknown_reason,
                        "unknown": sentinel,
                        "unknown_ref": sentinel_ref,
                        "active_stage_projection": projection,
                    },
                },
            }

        return resolved_store.commit_stage_operation(
            resolved_run,
            expected_revision=expected_revision,
            operation_id=operation_id,
            request_fingerprint=stage_entities.request_fingerprint(request),
            mutate=retain_unknown,
            validate_authority=validate_unknown,
        )

    assert loop_state is not None
    step = str(loop_state["step"])
    task_ids = [str(row["id"]) for row in loop_state.get("tasks") or []]
    deliverables = task_ids or ["legacy-singleton-state"]
    input_value = {
        "schema": MIGRATION_INPUT_SCHEMA,
        "source_fingerprint": bundle["fingerprint"],
        "source_ref": source_ref,
        "conservation_ref": conservation_ref,
    }
    input_native = artifact_store.put("stage-handoff", input_value)
    input_ref = _portable(artifact_store, input_native)
    stage_id = "legacy-" + str(bundle["fingerprint"])[:32]
    stage = stage_entities.create_stage(
        run_id=resolved_run,
        stage_id=stage_id,
        requirement=requirement,
        design=design,
        stage_kind=("retro" if step == "done" else _KNOWN_ACTIVE_STEPS[step]),
        parent_stage_ids=[],
        predecessor_stage_ids=[],
        input_manifest_ref=input_ref,
        execution_root_id=f"execution-{stage_id}",
        deliverables=deliverables,
        selected_artifacts=[source_ref, conservation_ref],
        budget={"migration": True},
        dependencies=[],
        contracts=contracts,
        authority=authority,
        created_at=created_at,
    )
    if step == "done":
        stage = stage_entities.terminalize_stage(
            stage,
            outcome="done",
            actor=str(authority.get("actor") or ""),
            terminalized_at=created_at,
            completed_deliverables=deliverables,
            completion_evidence=[source_ref, conservation_ref],
        )
    request = {
        "schema": "taskplane.legacy-migration-request/v1",
        "run_id": resolved_run,
        "operation_id": operation_id,
        "source_fingerprint": bundle["fingerprint"],
        "conservation_fingerprint": conservation["fingerprint"],
        "stage_fingerprint": stage["fingerprint"],
        "authority": copy.deepcopy(authority),
    }
    request_fingerprint = stage_entities.request_fingerprint(request)

    def validate(current: Mapping[str, object]) -> None:
        authority_validator(copy.deepcopy(authority), copy.deepcopy(current))

    def mutate(current: dict) -> dict:
        heads = copy.deepcopy(current.get("stage_heads") or {})
        if heads:
            raise MigrationIntegrityError(
                "legacy migration requires an empty v4 stage index")
        object_ref = resolved_store.put_stage_object(resolved_run, stage)
        head = {
            "object": object_ref,
            "summary": stage_entities.bounded_stage_summary(stage),
        }
        heads[stage_id] = head
        foreground = stage_id if stage["state"] == "active" else None
        projection = stage_entities.active_stage_projection(
            heads, foreground_stage_id=foreground)
        return {
            "changes": {
                "stage_heads": heads,
                "lineage": copy.deepcopy(current.get("lineage") or []),
                "active_stage_projection": projection,
            },
            "receipt": {
                "operation": MIGRATION_OPERATION,
                "stage_ids": [stage_id],
                "result": {
                    "classification": "stage",
                    "source_fingerprint": bundle["fingerprint"],
                    "source_ref": source_ref,
                    "conservation": conservation,
                    "conservation_ref": conservation_ref,
                    "head": head,
                    "active_stage_projection": projection,
                },
            },
        }

    return resolved_store.commit_stage_operation(
        resolved_run,
        expected_revision=expected_revision,
        operation_id=operation_id,
        request_fingerprint=request_fingerprint,
        mutate=mutate,
        validate_authority=validate,
    )


def _verified_receipt(manifest: Mapping[str, object]) -> dict | None:
    operations = manifest.get("stage_operations")
    if not isinstance(operations, dict):
        return None
    receipts = [copy.deepcopy(row) for row in operations.values()
                if isinstance(row, dict) and
                row.get("operation") == MIGRATION_OPERATION]
    if not receipts:
        return None
    if len(receipts) != 1:
        raise MigrationIntegrityError("multiple singleton migrations are ambiguous")
    receipt = receipts[0]
    if receipt.get("schema") != "taskplane.stage-operation-receipt/v1" or \
            receipt.get("result_fingerprint") != \
            _fingerprint(receipt.get("result")):
        raise MigrationIntegrityError("migration receipt does not verify")
    revision = receipt.get("committed_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or \
            revision > int(manifest.get("revision") or 0):
        raise MigrationIntegrityError("migration receipt revision is invalid")
    return receipt


def migration_projection(
        workspace: str, *, store: run_store_module.RunStore | None = None,
        run_id: str | None = None) -> dict[str, object] | None:
    """Return a read-only v4 projection only after receipt verification.

    This function never acquires the legacy loop lock, so track mutators may
    call it while holding that lock to make cutover and rejection atomic.
    """
    try:
        resolved_store, resolved_run = _resolve_store(
            workspace, store=store, run_id=run_id)
    except MigrationIntegrityError:
        return None
    manifest = resolved_store.load(resolved_run)
    receipt = _verified_receipt(manifest)
    if receipt is None:
        return None
    result = receipt.get("result")
    if not isinstance(result, dict) or result.get("classification") not in {
            "stage", "legacy-unknown"}:
        raise MigrationIntegrityError("migration result is invalid")
    artifact_store = _migration_artifact_store(
        workspace, resolved_store, resolved_run)
    for key in ("source_ref", "conservation_ref"):
        reference = result.get(key)
        if not isinstance(reference, dict):
            raise MigrationIntegrityError("migration artifact reference is missing")
        review_evidence.verify_portable_artifact_reference(
            artifact_store, reference)
    source_bundle = artifact_store.read(dict(result["source_ref"]))
    verify_retained_sources(source_bundle)
    conservation = artifact_store.read(dict(result["conservation_ref"]))
    if conservation != source_bundle["conservation"] or \
            conservation != result.get("conservation") or \
            result.get("source_fingerprint") != source_bundle["fingerprint"]:
        raise MigrationIntegrityError("migration conservation does not verify")
    projection = manifest.get("active_stage_projection")
    heads = manifest.get("stage_heads")
    if not isinstance(projection, dict) or not isinstance(heads, dict):
        raise MigrationIntegrityError("migration stage index is invalid")
    if result.get("active_stage_projection") != projection:
        raise MigrationIntegrityError(
            "migration receipt projection does not match the run manifest")
    try:
        expected_projection = stage_entities.active_stage_projection(
            heads, foreground_stage_id=projection.get("foreground_stage_id"))
    except (ValueError, TypeError, KeyError) as exc:
        raise MigrationIntegrityError(
            "migration stage projection does not verify") from exc
    if projection != expected_projection:
        raise MigrationIntegrityError(
            "migration stage projection is stale")
    stage_ids = receipt.get("stage_ids")
    if result["classification"] == "legacy-unknown":
        unknown_ref = result.get("unknown_ref")
        if stage_ids != [] or heads or not isinstance(unknown_ref, dict):
            raise MigrationIntegrityError(
                "unknown migration created lifecycle authority")
        review_evidence.verify_portable_artifact_reference(
            artifact_store, unknown_ref)
        unknown = artifact_store.read(unknown_ref)
        if unknown != result.get("unknown") or \
                result.get("unknown_reason") != unknown.get("unknown_reason"):
            raise MigrationIntegrityError(
                "legacy unknown migration does not verify")
        verify_legacy_unknown(unknown, source_bundle)
    else:
        if not isinstance(stage_ids, list) or len(stage_ids) != 1 or \
                stage_ids[0] not in heads:
            raise MigrationIntegrityError(
                "migration receipt stage binding is invalid")
        stage_id = str(stage_ids[0])
        head = heads[stage_id]
        if not isinstance(head, dict) or result.get("head") != head:
            raise MigrationIntegrityError("migration head does not verify")
        resolved_store.read_stage_object(resolved_run, head["object"])
    foreground_id = projection.get("foreground_stage_id")
    foreground = (copy.deepcopy(heads[foreground_id]["summary"])
                  if foreground_id is not None else None)
    return {
        "schema": PROJECTION_SCHEMA,
        "run_id": resolved_run,
        "receipt": receipt,
        "active_stage_ids": copy.deepcopy(projection.get("active_stage_ids")),
        "foreground_stage_id": foreground_id,
        "foreground": foreground,
        "stages": {stage: copy.deepcopy(value["summary"])
                   for stage, value in sorted(heads.items())},
    }


def has_verified_migration(
        workspace: str, *, store: run_store_module.RunStore | None = None,
        run_id: str | None = None) -> bool:
    return migration_projection(
        workspace, store=store, run_id=run_id) is not None


def legacy_track_projection(workspace: str) -> dict[str, object] | None:
    """Project verified v4 summaries into the narrow legacy track shape."""
    projection = migration_projection(workspace)
    if projection is None:
        return None
    foreground_id = projection["foreground_stage_id"]
    tracks = {stage_id: {
            "name": stage_id,
            "goal": str((summary.get("requirement") or {}).get("id") or
                        summary.get("stage_kind") or stage_id),
            "requirement_id": (summary.get("requirement") or {}).get("id"),
            "status": ("open" if summary.get("state") == "active" else
                       str(summary.get("outcome") or "closed")),
        }
        for stage_id, summary in projection["stages"].items()
    }
    return {"active": foreground_id, "tracks": tracks}
