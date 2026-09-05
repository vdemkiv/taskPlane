"""Synchronous repository-only authoring and BUILD-C coordination.

Shared stateless inputs and assignments live in phase_inputs; this module
preserves their historical imports while owning only the execution sequence.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import copy
from typing import Any, Final, TYPE_CHECKING, TypeAlias

if TYPE_CHECKING or __package__:
    from . import build_c, checkpoint, phase_handoff, phase_build, phase_inputs
else:  # pragma: no cover - direct module import compatibility
    import build_c
    import checkpoint
    import phase_handoff
    import phase_build
    import phase_inputs


# Compatibility aliases keep existing callers on the same class/functions.
BUILD_ASSIGNMENT_SCHEMA: Final[str] = phase_inputs.BUILD_ASSIGNMENT_SCHEMA
BUILD_PRODUCER_CONTRACT_SCHEMA: Final[str] = phase_inputs.BUILD_PRODUCER_CONTRACT_SCHEMA
BUILD_ATTEMPT_LEASE_SCHEMA: Final[str] = phase_inputs.BUILD_ATTEMPT_LEASE_SCHEMA
BUILD_SCOPED_VIEW_SCHEMA: Final[str] = phase_inputs.BUILD_SCOPED_VIEW_SCHEMA
BUILD_RESULT_SCHEMA: Final[str] = phase_inputs.BUILD_RESULT_SCHEMA
BUILD_BOOTSTRAP_SCHEMA: Final[str] = phase_inputs.BUILD_BOOTSTRAP_SCHEMA
PUBLIC_RESULT_SCHEMA: Final[str] = phase_inputs.PUBLIC_RESULT_SCHEMA
_GIT_OBJECT = phase_inputs._GIT_OBJECT
_ASSIGNMENT_FIELDS = phase_inputs._ASSIGNMENT_FIELDS
_RECOVERY = phase_inputs._RECOVERY
PhasePickupError = phase_inputs.PhasePickupError
_canonical = phase_inputs._canonical
_fingerprinted = phase_inputs._fingerprinted
_git = phase_inputs._git
_validated_repository_handoff = phase_inputs._validated_repository_handoff
_validated_handoff_value = phase_inputs._validated_handoff_value
_proof_paths = phase_inputs._proof_paths
_validate_plan_proofs = phase_inputs._validate_plan_proofs
_completed_task_ids = phase_inputs._completed_task_ids
_remaining_obligation_ids = phase_inputs._remaining_obligation_ids
select_ready_build_task = phase_inputs.select_ready_build_task
_build_assignment = phase_inputs._build_assignment
validate_build_assignment = phase_inputs.validate_build_assignment
JsonObject: TypeAlias = phase_inputs.JsonObject
HandoffInput: TypeAlias = phase_inputs.HandoffInput


def prepare_build_pickup(
        checkout: str, handoff: HandoffInput, *,
        requested_task: str | Mapping[str, object] | None = None,
        attempt_id: str | None = None) -> JsonObject:
    """Validate all portable authority, then mint one exact Build assignment."""
    checked = _validated_repository_handoff(checkout, handoff)
    _validate_plan_proofs(checkout, checked)
    task = select_ready_build_task(checked, requested_task=requested_task)
    phase_build.resolve_native_task(checkout, checked, task)
    base_revision = _git(checkout, "rev-parse", "HEAD")
    return _build_assignment(
        checked, task=task, base_revision=base_revision,
        attempt_id=attempt_id)


def _task_obligations(handoff: Mapping[str, Any], task: Mapping[str, Any]) \
        -> list[str]:
    obligation_ids = _remaining_obligation_ids(handoff, task)
    if obligation_ids:
        return obligation_ids
    raise PhasePickupError(
        "dependency-unmet", "ready task has no remaining sealed obligation")


def submit_build_pickup(
        checkout: str, handoff: Mapping[str, Any], *,
        assignment: Mapping[str, Any], authoring_result: Mapping[str, Any],
        emit: Callable[[str], None] | None = None) -> JsonObject:
    """Validate authored bytes, cross BUILD-C, then mint green lineage."""
    checked = _validated_handoff_value(handoff)
    task = select_ready_build_task(checked)
    admitted = validate_build_assignment(
        assignment, checked, expected_task=task, checkout=checkout)
    try:
        authored = checkpoint.validate_phase_authoring_result(
            checkout, authoring_result, task=task, assignment=admitted)
    except checkpoint.PhaseAuthoringError as exc:
        code = "scope-widened" if str(exc).startswith("scope-widened:") \
            else "authoring-invalid"
        raise PhasePickupError(code, str(exc).split(": ", 1)[-1]) from exc
    quality = phase_build.admit_quality(checkout, checked, admitted, authored)
    entry = getattr(build_c, "run_phase_pickup", None)
    if not callable(entry):
        raise PhasePickupError(
            "build-c-unavailable", "required post-authoring edge is unavailable")
    events = emit or (lambda _event: None)
    try:
        evidence = entry(
            checkout, task, admitted, authored,
            repository_id=str(checked["repository"]["id"]), emit=events)
        checked_checkpoint, checked_integration = \
            build_c.validate_phase_pickup_evidence(
                evidence, task=task, assignment=admitted,
                authoring_result=authored,
                repository_id=str(checked["repository"]["id"]))
    except build_c.IntegrationAuthorizationError as exc:
        detail = str(exc)
        code = "proof-invalid" if detail.startswith("proof-invalid:") \
            else "build-c-failed"
        raise PhasePickupError(code, detail.split(": ", 1)[-1]) from exc
    progress_receipts = []
    predecessor = checked["lineage"]["predecessor_receipt_head"]
    for sequence, obligation_id in enumerate(
            _task_obligations(checked, task),
            len(checked["progress_receipts"]) + 1):
        progress = phase_handoff.create_progress_receipt(
            producer="engine:taskplane.phase-pickup/v1",
            sequence=sequence, phase="build", obligation_id=obligation_id,
            task_id=str(task["id"]), status="green",
            predecessor_receipt_fingerprint=predecessor,
            checkpoint_receipt_digest=checked_checkpoint["receipt_digest"],
            integration_receipt_fingerprint=checked_integration["fingerprint"],
        )
        progress_receipts.append(progress)
        predecessor = progress["fingerprint"]
    # Preserve the v1 singular field as the terminal receipt/head. A task
    # completing multiple obligations additionally carries the entire chain;
    # single-obligation results keep their existing public shape unchanged.
    progress = progress_receipts[-1]
    material = {
        "schema": PUBLIC_RESULT_SCHEMA,
        "status": "complete", "code": "build-integrated",
        "phase": "build", "mode": checked["successor"]["mode"],
        "handoff_id": checked["handoff_id"],
        "handoff_fingerprint": checked["fingerprint"],
        "source": copy.deepcopy(checked["source"]),
        "task_id": task["id"],
        "authoring_receipt_fingerprint": authored["fingerprint"],
        "checkpoint_receipt_digest": checked_checkpoint["receipt_digest"],
        "integration_receipt_fingerprint": checked_integration["fingerprint"],
        "progress_receipt": progress,
        **({"build_quality": quality} if quality is not None else {}),
        **({"progress_receipts": progress_receipts}
           if len(progress_receipts) > 1 else {}),
        "lineage": {
            "predecessor_receipt_head": checked["lineage"][
                "predecessor_receipt_head"],
            "receipt_head": progress["fingerprint"],
        },
    }
    return _fingerprinted(material)


def run_build_pickup(
        checkout: str, handoff: HandoffInput, *,
        author: Callable[[JsonObject], Mapping[str, Any] | None],
        requested_task: str | Mapping[str, object] | None = None,
        emit: Callable[[str], None] | None = None) -> JsonObject:
    """Run the synchronous validate→author→BUILD-C successor sequence."""
    checked = _validated_repository_handoff(checkout, handoff)
    _validate_plan_proofs(checkout, checked)
    task = select_ready_build_task(checked, requested_task=requested_task)
    phase_build.resolve_native_task(checkout, checked, task)
    assignment = _build_assignment(
        checked, task=task, base_revision=_git(checkout, "rev-parse", "HEAD"))
    supplied = author(copy.deepcopy(assignment))
    if supplied is None:
        try:
            supplied = checkpoint.mint_phase_authoring_result(
                checkout, task=task, assignment=assignment)
        except checkpoint.PhaseAuthoringError as exc:
            raise PhasePickupError("authoring-invalid", str(exc)) from exc
    return submit_build_pickup(
        checkout, checked, assignment=assignment,
        authoring_result=supplied, emit=emit)


def submit_committed(
        checkout: str, handoff: HandoffInput, *, task_id: str,
        emit: Callable[[str], None] | None = None) -> JsonObject:
    """Submit one clean committed Build diff without exposing its assignment."""
    checked = _validated_repository_handoff(
        checkout, handoff, allowed_task_id=task_id)
    _validate_plan_proofs(checkout, checked)
    task = select_ready_build_task(checked, requested_task=task_id)
    manifest_path = phase_handoff.handoff_path(str(checked["handoff_id"]))
    assignment = _build_assignment(
        checked, task=task,
        base_revision=_git(
            checkout, "log", "-1", "--format=%H", "--", manifest_path))
    try:
        authored = checkpoint.mint_phase_authoring_result(
            checkout, task=task, assignment=assignment)
    except checkpoint.PhaseAuthoringError as exc:
        code = "scope-widened" if str(exc).startswith("scope-widened:") \
            else "authoring-invalid"
        raise PhasePickupError(code, str(exc).split(": ", 1)[-1]) from exc
    return submit_build_pickup(
        checkout, checked, assignment=assignment,
        authoring_result=authored, emit=emit)


# Thin public vocabulary for the forthcoming CLI adapter.
prepare = prepare_build_pickup
submit = submit_build_pickup
run = run_build_pickup


__all__ = [
    "BUILD_ASSIGNMENT_SCHEMA", "PUBLIC_RESULT_SCHEMA", "PhasePickupError",
    "prepare", "run", "submit", "submit_committed",
]
