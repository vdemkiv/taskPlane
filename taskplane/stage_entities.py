"""Stage lifecycle operations over the canonical values and RunStore."""
from __future__ import annotations
from collections.abc import Callable, Iterable, Mapping
import copy
from typing import Final, TypeAlias
from taskplane import review_evidence, stage_handoff, storage as runtime_storage
from taskplane.stage_values import (
    SCHEMA as SCHEMA,
    SUMMARY_SCHEMA as SUMMARY_SCHEMA,
    PROJECTION_SCHEMA as PROJECTION_SCHEMA,
    LINEAGE_SCHEMA as LINEAGE_SCHEMA,
    AUTHORITY_SCHEMA as AUTHORITY_SCHEMA,
    MAX_INPUT_MANIFEST_BYTES as MAX_INPUT_MANIFEST_BYTES,
    MAX_STAGE_SUMMARY_BYTES as MAX_STAGE_SUMMARY_BYTES,
    MAX_COLLECTION_ITEMS as MAX_COLLECTION_ITEMS,
    MAX_REASON_BYTES as MAX_REASON_BYTES,
    TERMINAL_OUTCOMES as TERMINAL_OUTCOMES,
    STAGE_STATES as STAGE_STATES,
    JsonObject as JsonObject,
    _IDENTIFIER as _IDENTIFIER,
    _KIND as _KIND,
    _FINGERPRINT as _FINGERPRINT,
    _CONTRACT as _CONTRACT,
    _PORTABLE_REFERENCE_FIELDS as _PORTABLE_REFERENCE_FIELDS,
    _REQUIREMENT_FIELDS as _REQUIREMENT_FIELDS,
    _DESIGN_FIELDS as _DESIGN_FIELDS,
    _AUTHORITY_FIELDS as _AUTHORITY_FIELDS,
    _TERMINAL_FIELDS as _TERMINAL_FIELDS,
    _STAGE_FIELDS as _STAGE_FIELDS,
    _SPLIT_SPEC_FIELDS as _SPLIT_SPEC_FIELDS,
    _LINEAGE_FIELDS as _LINEAGE_FIELDS,
    PHASE_DEFINITION_SCHEMA as PHASE_DEFINITION_SCHEMA,
    AGENT_RUNTIME_SCHEMA as AGENT_RUNTIME_SCHEMA,
    KNOWLEDGE_UPDATE_SCHEMA as KNOWLEDGE_UPDATE_SCHEMA,
    KNOWLEDGE_APPLY_SCHEMA as KNOWLEDGE_APPLY_SCHEMA,
    HANDOFF_V2_SCHEMA as HANDOFF_V2_SCHEMA,
    _PHASE_FIELDS as _PHASE_FIELDS,
    _RUNTIME_FIELDS as _RUNTIME_FIELDS,
    _KNOWLEDGE_UPDATE_FIELDS as _KNOWLEDGE_UPDATE_FIELDS,
    _KNOWLEDGE_APPLY_FIELDS as _KNOWLEDGE_APPLY_FIELDS,
    _HANDOFF_V2_ADDITIONS as _HANDOFF_V2_ADDITIONS,
    _CONTRACT_FIELDS as _CONTRACT_FIELDS,
    _CONTRACT_SELF_FIELDS as _CONTRACT_SELF_FIELDS,
    _strict_json as _strict_json,
    _contract_json as _contract_json,
    read_contract_json as read_contract_json,
    _contract_budget as _contract_budget,
    _contract_list as _contract_list,
    _contract_strings as _contract_strings,
    _continuation as _continuation,
    _schema_artifacts as _schema_artifacts,
    _validate_phase_definition as _validate_phase_definition,
    _validate_runtime_result as _validate_runtime_result,
    validate_contract as validate_contract,
    create_contract as create_contract,
    canonical_contract_bytes as canonical_contract_bytes,
    StageValidationError as StageValidationError,
    StageIntegrityError as StageIntegrityError,
    StageLifecycleError as StageLifecycleError,
    SplitValidationError as SplitValidationError,
    _closed as _closed,
    _identifier as _identifier,
    _path_id as _path_id,
    _bounded_text as _bounded_text,
    _revision as _revision,
    _fingerprint as _fingerprint,
    _timestamp as _timestamp,
    _strings as _strings,
    _path_ids as _path_ids,
    _plain_mapping as _plain_mapping,
    _requirement as _requirement,
    _design as _design,
    _portable_reference as _portable_reference,
    _references as _references,
    _budget as _budget,
    _authority as _authority,
    request_fingerprint as request_fingerprint,
    stage_fingerprint as stage_fingerprint,
    create_stage as create_stage,
    validate_stage as validate_stage,
    terminalize_stage as terminalize_stage,
    split_child_id as split_child_id,
    _lineage_row as _lineage_row,
    validate_lineage as validate_lineage,
    _dependency_cycle as _dependency_cycle,
    create_split as create_split,
    _bounded_stage_summary as _bounded_stage_summary,
    bounded_stage_summary as bounded_stage_summary,
    _state_from_head as _state_from_head,
    active_stage_projection as active_stage_projection,
    rebuild_active_stage_projection as rebuild_active_stage_projection,
    _stage_head as _stage_head,
    _read_indexed_stage as _read_indexed_stage,
    _head_fingerprint as _head_fingerprint,
)

class StageLifecycle:
    """Transactional service over immutable stage values and ``RunStore``.

    The service deliberately accepts repository authority revalidation as a
    dependency.  It therefore owns no repository mutation or worktree cleanup
    edge and can be exercised on every host with the same lifecycle semantics.
    """

    def __init__(
        self,
        store: object,
        *,
        workspace: str | None = None,
        artifact_store: review_evidence.ArtifactStore | None = None,
        authority_resolver: Callable[[Mapping[str, object]], Mapping[str, object]],
        authority_validator: Callable[[Mapping[str, object], Mapping[str, object]], object],
        handoff_resolver: Callable[[Mapping[str, object]], object] | None = None,
        artifact_validator: Callable[[Mapping[str, object]], object] | None = None,
        execution_root_claimer: Callable[[Mapping[str, object]], object] | None = None,
    ):
        commit = getattr(store, "commit_stage_operation", None)
        if not callable(commit):
            raise StageLifecycleError("StageLifecycle requires a stage-capable RunStore")
        for value, label in (
            (authority_resolver, "authority resolver"),
            (authority_validator, "authority validator"),
        ):
            if not callable(value):
                raise StageValidationError(f"{label} must be callable")
        for value, label in (
            (handoff_resolver, "handoff resolver"),
            (artifact_validator, "artifact validator"),
            (execution_root_claimer, "execution root claimer"),
        ):
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
        self,
        expected: Mapping[str, object],
        manifest: Mapping[str, object],
        stage: Mapping[str, object],
    ) -> None:
        current = self.authority_resolver(copy.deepcopy(manifest))
        current_checked = _authority(
            current,
            run_id=str(stage["run_id"]),
            requirement=stage["requirement"],
            design=stage["design"],
        )
        self.authority_validator(copy.deepcopy(dict(expected)), copy.deepcopy(current_checked))

    def _artifact_store(self) -> review_evidence.ArtifactStore:
        if self.artifact_store is not None:
            return self.artifact_store
        if not self.workspace:
            raise StageLifecycleError("stage handoff validation requires a canonical workspace")
        return stage_handoff.canonical_artifact_store(self.workspace)

    def _claim_execution_root(
        self, stage: Mapping[str, object], *, attempt_id: str | None = None
    ) -> Mapping[str, object]:
        claim = runtime_storage.claim_stage_execution_root_for_run(
            getattr(self.store, "home", None),
            str(stage["run_id"]),
            str(stage["stage_id"]),
            str(stage["execution_root_id"]),
            attempt_id=attempt_id,
        )
        if self.execution_root_claimer is not None:
            self.execution_root_claimer(copy.deepcopy(claim))
        return claim

    def _verify_artifact(self, reference: Mapping[str, object]) -> None:
        if self.artifact_validator is not None:
            self.artifact_validator(copy.deepcopy(reference))
            return
        review_evidence.verify_portable_artifact_reference(self._artifact_store(), dict(reference))

    def _read_handoff(
        self,
        reference: Mapping[str, object],
        *,
        producer: Mapping[str, object],
        consumer: Mapping[str, object],
    ) -> JsonObject:
        authority = producer["authority"]
        assert isinstance(authority, Mapping)
        resolved: object = None
        if self.handoff_resolver is not None:
            try:
                resolved = self.handoff_resolver(copy.deepcopy(reference))
            except Exception as exc:
                raise StageLifecycleError(
                    "handoff reference fingerprint could not be resolved"
                ) from exc
        if resolved is None:
            store, stored_reference = self._artifact_store(), reference
        elif isinstance(resolved, tuple) and len(resolved) == 2:
            store, stored_reference = resolved
        else:
            raise StageLifecycleError("handoff resolver must return (artifact_store, reference)")
        if not isinstance(store, review_evidence.ArtifactStore) or not isinstance(
            stored_reference, Mapping
        ):
            raise StageLifecycleError("handoff resolver returned invalid data")
        checked = stage_handoff.read_manifest(
            store,
            stored_reference,
            expected_authority_revision=int(authority["authority_revision"]),
            expected_authority_fingerprint=str(authority["authority_fingerprint"]),
            allow_nonconsumable_reuse=producer["outcome"]
            in {
                "closed",
                "discarded",
            },
        )
        return self._verify_handoff_value(checked, producer=producer, consumer=consumer)

    def _verify_handoff_value(
        self,
        checked: Mapping[str, object],
        *,
        producer: Mapping[str, object],
        consumer: Mapping[str, object] | None = None,
    ) -> JsonObject:
        authority = producer["authority"]
        assert isinstance(authority, Mapping)
        checked = copy.deepcopy(dict(checked))
        produced = checked.get("producer")
        if (
            not isinstance(produced, Mapping)
            or produced.get("stage_id") != producer.get("stage_id")
            or produced.get("outcome") != producer.get("outcome")
        ):
            raise StageLifecycleError("handoff producer does not match predecessor stage")
        if checked.get("requirement") != producer.get("requirement") or checked.get(
            "design"
        ) != producer.get("design"):
            raise StageLifecycleError("handoff revision does not match predecessor stage")
        authorization = checked.get("authorization")
        if (
            not isinstance(authorization, Mapping)
            or authorization.get("actor") != authority.get("actor")
            or authorization.get("session_id") != authority.get("session_id")
        ):
            raise StageLifecycleError("handoff authorization does not match stage authority")
        if consumer is not None:
            if checked.get("requirement") != consumer.get("requirement") or checked.get(
                "design"
            ) != consumer.get("design"):
                raise StageLifecycleError("handoff revision does not match successor stage")
            input_ref = consumer.get("input_manifest_ref")
            if not isinstance(input_ref, Mapping) or input_ref.get("fingerprint") != checked.get(
                "fingerprint"
            ):
                raise StageLifecycleError("successor input does not reference the verified handoff")
            if checked.get("selected_artifacts") != consumer.get("selected_artifacts"):
                raise StageLifecycleError("handoff artifacts do not match successor selection")
        return checked

    def _verify_handoff(
        self,
        manifest: Mapping[str, object],
        *,
        producer: Mapping[str, object],
        consumer: Mapping[str, object] | None = None,
    ) -> JsonObject:
        authority = producer["authority"]
        assert isinstance(authority, Mapping)
        checked = stage_handoff.validate_manifest(
            self._artifact_store(),
            manifest,
            expected_authority_revision=int(authority["authority_revision"]),
            expected_authority_fingerprint=str(authority["authority_fingerprint"]),
            allow_nonconsumable_reuse=producer["outcome"]
            in {
                "closed",
                "discarded",
            },
        )
        return self._verify_handoff_value(checked, producer=producer, consumer=consumer)

    def start_stage(
        self,
        stage: Mapping[str, object],
        *,
        expected_revision: int,
        operation_id: str,
        expected_predecessor_fingerprints: Mapping[str, str] | None = None,
        foreground: bool = True,
    ) -> dict[str, object]:
        """Atomically index one root stage or verified successor stage."""
        candidate = validate_stage(stage)
        if candidate["state"] != "active":
            raise StageLifecycleError("a new stage must be active")
        operation = _identifier(operation_id, "stage operation id")
        if not isinstance(foreground, bool):
            raise StageValidationError("foreground selection must be boolean")
        expected_predecessors = {
            _path_id(key, "expected predecessor id"): _fingerprint(
                value, "expected predecessor fingerprint"
            )
            for key, value in (expected_predecessor_fingerprints or {}).items()
        }
        if set(expected_predecessors) != set(candidate["predecessor_stage_ids"]):
            raise StageLifecycleError("expected predecessor heads do not match stage lineage")
        request = request_fingerprint(
            {
                "operation": "start_stage",
                "operation_id": operation,
                "stage_fingerprint": candidate["fingerprint"],
                "expected_predecessor_fingerprints": expected_predecessors,
                "handoff_fingerprint": (candidate["input_manifest_ref"]["fingerprint"]),
                "foreground": foreground,
            }
        )

        def authority_check(current: dict[str, object]) -> None:
            self._check_authority(candidate["authority"], current, candidate)

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
                    raise StageLifecycleError("successor predecessor is not indexed")
                predecessor = _read_indexed_stage(
                    self.store,
                    str(candidate["run_id"]),
                    predecessor_id,
                    heads[predecessor_id],
                    expected_fingerprint=expected_predecessors[predecessor_id],
                )
                if predecessor["state"] != "terminal":
                    raise StageLifecycleError("successor predecessor is not terminal")
                predecessor_objects[predecessor_id] = predecessor
            for parent_id in candidate["parent_stage_ids"]:
                if parent_id not in heads:
                    raise StageLifecycleError("successor parent is not indexed")
                _read_indexed_stage(
                    self.store, str(candidate["run_id"]), parent_id, heads[parent_id]
                )
            for existing_id, existing_head in heads.items():
                summary = (
                    existing_head.get("summary") if isinstance(existing_head, Mapping) else None
                )
                if (
                    isinstance(summary, Mapping)
                    and summary.get("execution_root_id") == candidate["execution_root_id"]
                ):
                    raise StageLifecycleError(f"execution root is already owned by {existing_id}")
            if predecessor_objects:
                verified = False
                failures: list[Exception] = []
                for producer in predecessor_objects.values():
                    try:
                        self._read_handoff(
                            candidate["input_manifest_ref"], producer=producer, consumer=candidate
                        )
                    except (StageValidationError, stage_handoff.HandoffValidationError) as exc:
                        failures.append(exc)
                        continue
                    verified = True
                    break
                if not verified:
                    raise StageLifecycleError(
                        "successor handoff does not match any predecessor"
                    ) from (failures[-1] if failures else None)
            self._claim_execution_root(candidate)
            heads[stage_id] = _stage_head(self.store, str(candidate["run_id"]), candidate)
            old_projection = current.get("active_stage_projection")
            old_foreground = (
                old_projection.get("foreground_stage_id")
                if isinstance(old_projection, Mapping)
                else None
            )
            projection = active_stage_projection(heads, stage_id if foreground else old_foreground)
            lineage = copy.deepcopy(current.get("lineage") or [])
            if not isinstance(lineage, list):
                raise StageLifecycleError("stage lineage is invalid")
            lineage_parents: list[str | None] = list(candidate["parent_stage_ids"])
            if not lineage_parents and candidate["predecessor_stage_ids"]:
                # One canonical predecessor-only relationship carries the
                # complete predecessor set; emitting one row per predecessor
                # would produce identical immutable lineage fingerprints.
                lineage_parents = [None]
            for parent_id in lineage_parents:
                lineage.append(
                    validate_lineage(
                        _lineage_row(
                            parent_stage_id=parent_id,
                            child_stage_id=stage_id,
                            input_manifest_ref=candidate["input_manifest_ref"],
                            operation_id=operation,
                            predecessor_stage_ids=candidate["predecessor_stage_ids"],
                        )
                    )
                )
            result = {"head": heads[stage_id], "active_stage_projection": projection}
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
            str(candidate["run_id"]),
            expected_revision=expected_revision,
            operation_id=operation,
            request_fingerprint=request,
            mutate=mutate,
            validate_authority=authority_check,
        )

    def terminalize(
        self,
        run_id: str,
        *,
        stage_id: str,
        expected_head_fingerprint: str,
        expected_revision: int,
        operation_id: str,
        outcome: str,
        actor: str,
        terminalized_at: str,
        reason_code: str | None = None,
        reason: str | None = None,
        completed_deliverables: Iterable[str] = (),
        completion_evidence: Iterable[Mapping[str, object]] = (),
        handoff_manifest: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Terminalize exactly one expected head and optionally bind a handoff."""
        run = _path_id(run_id, "run id")
        stage = _path_id(stage_id, "stage id")
        expected = _fingerprint(expected_head_fingerprint, "expected stage fingerprint")
        operation = _identifier(operation_id, "stage operation id")
        completed = list(completed_deliverables)
        evidence = list(completion_evidence)
        handoff_copy = (
            copy.deepcopy(dict(handoff_manifest)) if handoff_manifest is not None else None
        )
        request = request_fingerprint(
            {
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
                "handoff_fingerprint": (handoff_copy.get("fingerprint") if handoff_copy else None),
            }
        )

        def load(current: Mapping[str, object]) -> JsonObject:
            heads = current.get("stage_heads")
            if not isinstance(heads, Mapping) or stage not in heads:
                raise StageLifecycleError("stage is not indexed")
            return _read_indexed_stage(
                self.store, run, stage, heads[stage], expected_fingerprint=expected
            )

        def authority_check(current: dict[str, object]) -> None:
            active = load(current)
            self._check_authority(active["authority"], current, active)

        def mutate(current: dict[str, object]) -> dict[str, object]:
            active = load(current)
            terminal = terminalize_stage(
                active,
                outcome=outcome,
                actor=actor,
                terminalized_at=terminalized_at,
                reason_code=reason_code,
                reason=reason,
                completed_deliverables=completed,
                completion_evidence=evidence,
            )
            if outcome == "done":
                for reference in terminal["terminal"]["completion_evidence"]:
                    self._verify_artifact(reference)
            handoff_ref: dict[str, object] | None = None
            if handoff_copy is not None:
                checked_handoff = self._verify_handoff(handoff_copy, producer=terminal)
                artifact_store = self._artifact_store()
                native = stage_handoff.store_manifest(artifact_store, checked_handoff)
                handoff_ref = review_evidence.portable_artifact_reference(artifact_store, native)
            heads = copy.deepcopy(current["stage_heads"])
            heads[stage] = _stage_head(self.store, run, terminal)
            old_projection = current.get("active_stage_projection")
            foreground = (
                old_projection.get("foreground_stage_id")
                if isinstance(old_projection, Mapping)
                else None
            )
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
            run,
            expected_revision=expected_revision,
            operation_id=operation,
            request_fingerprint=request,
            mutate=mutate,
            validate_authority=authority_check,
        )

    def terminalize_and_start(
        self,
        predecessor_stage_id: str,
        successor_stage: Mapping[str, object],
        *,
        expected_head_fingerprint: str,
        expected_revision: int,
        operation_id: str,
        outcome: str,
        actor: str,
        terminalized_at: str,
        reason_code: str | None = None,
        reason: str | None = None,
        completed_deliverables: Iterable[str] = (),
        completion_evidence: Iterable[Mapping[str, object]] = (),
        foreground: bool = True,
    ) -> dict[str, object]:
        """Terminalize one predecessor and index its successor in one commit."""
        successor = validate_stage(successor_stage)
        run = str(successor["run_id"])
        predecessor_id = _path_id(predecessor_stage_id, "predecessor stage id")
        successor_id = str(successor["stage_id"])
        if successor["state"] != "active" or successor["predecessor_stage_ids"] != [predecessor_id]:
            raise StageLifecycleError("successor must be active with exactly one predecessor")
        expected = _fingerprint(expected_head_fingerprint, "expected stage fingerprint")
        operation = _identifier(operation_id, "stage operation id")
        if not isinstance(foreground, bool):
            raise StageValidationError("foreground selection must be boolean")
        completed = list(completed_deliverables)
        evidence = list(completion_evidence)
        request = request_fingerprint(
            {
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
            }
        )

        def load(current: Mapping[str, object]) -> JsonObject:
            heads = current.get("stage_heads")
            if not isinstance(heads, Mapping) or predecessor_id not in heads:
                raise StageLifecycleError("predecessor is not indexed")
            if successor_id in heads:
                raise StageLifecycleError("successor is already indexed")
            return _read_indexed_stage(
                self.store,
                run,
                predecessor_id,
                heads[predecessor_id],
                expected_fingerprint=expected,
            )

        def authority_check(current: dict[str, object]) -> None:
            predecessor = load(current)
            self._check_authority(predecessor["authority"], current, predecessor)
            self._check_authority(successor["authority"], current, successor)

        def mutate(current: dict[str, object]) -> dict[str, object]:
            predecessor = load(current)
            terminal = terminalize_stage(
                predecessor,
                outcome=outcome,
                actor=actor,
                terminalized_at=terminalized_at,
                reason_code=reason_code,
                reason=reason,
                completed_deliverables=completed,
                completion_evidence=evidence,
            )
            if outcome == "done":
                for reference in terminal["terminal"]["completion_evidence"]:
                    self._verify_artifact(reference)
            self._read_handoff(
                successor["input_manifest_ref"], producer=terminal, consumer=successor
            )
            heads = copy.deepcopy(current["stage_heads"])
            for existing_id, head in heads.items():
                summary = head.get("summary") if isinstance(head, Mapping) else None
                if (
                    existing_id != predecessor_id
                    and isinstance(summary, Mapping)
                    and summary.get("execution_root_id") == successor["execution_root_id"]
                ):
                    raise StageLifecycleError("successor execution root is already owned")
            self._claim_execution_root(successor)
            predecessor_head = _stage_head(self.store, run, terminal)
            successor_head = _stage_head(self.store, run, successor)
            heads[predecessor_id] = predecessor_head
            heads[successor_id] = successor_head
            lineage = copy.deepcopy(current.get("lineage") or [])
            if not isinstance(lineage, list):
                raise StageLifecycleError("stage lineage is invalid")
            lineage_row = validate_lineage(
                _lineage_row(
                    parent_stage_id=None,
                    child_stage_id=successor_id,
                    input_manifest_ref=successor["input_manifest_ref"],
                    operation_id=operation,
                    predecessor_stage_ids=[predecessor_id],
                )
            )
            lineage.append(lineage_row)
            old_projection = current.get("active_stage_projection")
            old_foreground = (
                old_projection.get("foreground_stage_id")
                if isinstance(old_projection, Mapping)
                else None
            )
            projection = active_stage_projection(
                heads, successor_id if foreground else old_foreground
            )
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
            run,
            expected_revision=expected_revision,
            operation_id=operation,
            request_fingerprint=request,
            mutate=mutate,
            validate_authority=authority_check,
        )

    def split_stage(
        self,
        run_id: str,
        *,
        stage_id: str,
        expected_head_fingerprint: str,
        expected_revision: int,
        operation_id: str,
        child_specs: Iterable[Mapping[str, object]],
        actor: str,
        terminalized_at: str,
        reason: str,
    ) -> dict[str, object]:
        """Atomically replace one active parent with deterministic children."""
        run = _path_id(run_id, "run id")
        stage = _path_id(stage_id, "stage id")
        expected = _fingerprint(expected_head_fingerprint, "expected stage fingerprint")
        operation = _identifier(operation_id, "stage operation id")
        specs = copy.deepcopy(list(child_specs))
        request = request_fingerprint(
            {
                "operation": "split_stage",
                "operation_id": operation,
                "run_id": run,
                "stage_id": stage,
                "expected_head_fingerprint": expected,
                "child_specs": specs,
                "actor": actor,
                "terminalized_at": terminalized_at,
                "reason": reason,
            }
        )

        def load(current: Mapping[str, object]) -> JsonObject:
            heads = current.get("stage_heads")
            if not isinstance(heads, Mapping) or stage not in heads:
                raise StageLifecycleError("split parent is not indexed")
            return _read_indexed_stage(
                self.store, run, stage, heads[stage], expected_fingerprint=expected
            )

        def authority_check(current: dict[str, object]) -> None:
            parent = load(current)
            self._check_authority(parent["authority"], current, parent)

        def mutate(current: dict[str, object]) -> dict[str, object]:
            parent = load(current)
            split = create_split(
                parent,
                operation_id=operation,
                child_specs=specs,
                actor=actor,
                terminalized_at=terminalized_at,
                reason=reason,
            )
            heads = copy.deepcopy(current["stage_heads"])
            existing_roots = {
                str(head.get("summary", {}).get("execution_root_id"))
                for head in heads.values()
                if isinstance(head, Mapping) and isinstance(head.get("summary"), Mapping)
            }
            # Every child has a separately authorized parent-produced handoff;
            # the parent's predecessor input is never inherited implicitly.
            for child in split["children"]:
                self._read_handoff(
                    child["input_manifest_ref"], producer=split["parent"], consumer=child
                )
            self._claim_execution_root(split["parent"])
            parent_head = _stage_head(self.store, run, split["parent"])
            child_heads: dict[str, dict[str, object]] = {}
            for child in split["children"]:
                child_id = str(child["stage_id"])
                if child_id in heads:
                    raise SplitValidationError("split child id already exists")
                if str(child["execution_root_id"]) in existing_roots:
                    raise SplitValidationError("split child execution root already exists")
                existing_roots.add(str(child["execution_root_id"]))
                self._claim_execution_root(child)
                child_heads[child_id] = _stage_head(self.store, run, child)
            heads[stage] = parent_head
            heads.update(child_heads)
            lineage = copy.deepcopy(current.get("lineage") or [])
            if not isinstance(lineage, list):
                raise StageLifecycleError("stage lineage is invalid")
            known_lineage = {
                str(row.get("fingerprint")) for row in lineage if isinstance(row, Mapping)
            }
            for row in split["lineage"]:
                if str(row["fingerprint"]) in known_lineage:
                    raise SplitValidationError("split lineage already exists")
                lineage.append(row)
            old_projection = current.get("active_stage_projection")
            old_foreground = (
                old_projection.get("foreground_stage_id")
                if isinstance(old_projection, Mapping)
                else None
            )
            projection = active_stage_projection(
                heads, None if old_foreground == stage else old_foreground
            )
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
            run,
            expected_revision=expected_revision,
            operation_id=operation,
            request_fingerprint=request,
            mutate=mutate,
            validate_authority=authority_check,
        )

    def resume_stage(
        self,
        run_id: str,
        *,
        stage_id: str,
        expected_head_fingerprint: str,
        expected_revision: int,
        operation_id: str,
        attempt_id: str | None = None,
    ) -> dict[str, object]:
        """Record a fresh attempt under the same immutable active-stage root."""
        run = _path_id(run_id, "run id")
        stage = _path_id(stage_id, "stage id")
        expected = _fingerprint(expected_head_fingerprint, "expected stage fingerprint")
        operation = _identifier(operation_id, "stage operation id")
        attempt_material = {
            "run_id": run,
            "stage_id": stage,
            "operation_id": operation,
        }
        attempt = (
            _path_id(attempt_id, "stage attempt id")
            if attempt_id is not None
            else f"attempt-{request_fingerprint(attempt_material)[:24]}"
        )
        request = request_fingerprint(
            {
                "operation": "resume_stage",
                "operation_id": operation,
                "run_id": run,
                "stage_id": stage,
                "expected_head_fingerprint": expected,
                "attempt_id": attempt,
            }
        )

        def load(current: Mapping[str, object]) -> JsonObject:
            heads = current.get("stage_heads")
            if not isinstance(heads, Mapping) or stage not in heads:
                raise StageLifecycleError("stage is not indexed")
            return _read_indexed_stage(
                self.store, run, stage, heads[stage], expected_fingerprint=expected
            )

        def authority_check(current: dict[str, object]) -> None:
            active = load(current)
            self._check_authority(active["authority"], current, active)

        def mutate(current: dict[str, object]) -> dict[str, object]:
            active = load(current)
            if active["state"] != "active":
                raise StageLifecycleError("a terminal stage cannot resume")
            claim = dict(self._claim_execution_root(active, attempt_id=attempt))
            claim.pop("root", None)
            return {
                "changes": {
                    "stage_heads": copy.deepcopy(current["stage_heads"]),
                    "lineage": copy.deepcopy(current.get("lineage") or []),
                    "active_stage_projection": copy.deepcopy(current["active_stage_projection"]),
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
            run,
            expected_revision=expected_revision,
            operation_id=operation,
            request_fingerprint=request,
            mutate=mutate,
            validate_authority=authority_check,
        )

    def rebuild_active_projection(
        self,
        run_id: str,
        *,
        expected_revision: int,
        foreground_stage_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        """Delegate the locked repair of the replaceable projection cache."""
        repair = getattr(self.store, "rebuild_active_stage_projection", None)
        if not callable(repair):
            raise StageLifecycleError("run store cannot rebuild active stage projection")
        return repair(
            run_id,
            expected_revision=expected_revision,
            foreground_stage_id=foreground_stage_id,
            operation_id=operation_id,
        )
