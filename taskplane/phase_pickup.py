"""Synchronous repository-only pickup for sealed phase handoffs.

The coordinator deliberately has no locator, run, loop, claim, or predecessor
lease dependency.  A v2 handoff is validated in full before a fresh attempt is
minted, and Build enters BUILD-C only after the authored Git diff verifies.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import copy
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
from typing import Any, Final, TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from . import build_c, checkpoint, design_contract, phase_handoff
    from . import review_evidence
else:
    if __package__:
        from . import build_c, checkpoint, design_contract, phase_handoff
        from . import review_evidence
    else:  # pragma: no cover - direct module import compatibility
        import build_c
        import checkpoint
        import design_contract
        import phase_handoff
        import review_evidence


BUILD_ASSIGNMENT_SCHEMA: Final[str] = \
    "taskplane.phase-build-assignment/v1"
BUILD_PRODUCER_CONTRACT_SCHEMA: Final[str] = \
    "taskplane.phase-build-producer-contract/v1"
BUILD_ATTEMPT_LEASE_SCHEMA: Final[str] = \
    "taskplane.phase-build-attempt-lease/v1"
BUILD_SCOPED_VIEW_SCHEMA: Final[str] = \
    "taskplane.phase-build-scoped-view/v1"
BUILD_RESULT_SCHEMA: Final[str] = \
    "taskplane.phase-build-result-schema/v1"
BUILD_BOOTSTRAP_SCHEMA: Final[str] = \
    "taskplane.phase-build-contract-bootstrap/v1"
PUBLIC_RESULT_SCHEMA: Final[str] = "taskplane.phase-pickup-result/v1"

JsonObject: TypeAlias = dict[str, Any]
HandoffInput: TypeAlias = str | os.PathLike[str] | Mapping[str, object]

_GIT_OBJECT: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40,64}$")
_ASSIGNMENT_FIELDS: Final[frozenset[str]] = frozenset({
    "schema", "attempt_id", "handoff_id", "handoff_fingerprint",
    "base_revision", "task", "contract_bootstrap", "producer_contract",
    "lease", "scoped_view", "result_schema", "full_envelope_reference",
    "fingerprint",
})

_RECOVERY: Final[dict[str, str]] = {
    "handoff-malformed": "restore the exact sealed handoff",
    "handoff-integrity": "restore the exact canonical handoff bytes",
    "repository-foreign": "use the recorded repository checkout",
    "source-stale": "use the recorded source and export lineage",
    "checkout-dirty": "use a clean checkout of the sealed handoff",
    "artifact-integrity": "restore the exact digest-addressed artifact",
    "receipt-lineage": "resume from the sole verified receipt head",
    "authority-missing": "return to the real human gate",
    "authority-stale": "obtain approval for the exact current subject",
    "transition-invalid": "use only the declared successor transition",
    "scope-widened": "use the exact sealed task or return to Plan",
    "dependency-unmet": "complete the declared predecessor tasks",
    "proof-invalid": "restore the exact sealed proof command",
    "authoring-invalid": "submit the exact committed scoped authoring result",
    "build-c-unavailable": "restore the required BUILD-C integration edge",
    "build-c-failed": "correct the scoped implementation or focused proof",
}


class PhasePickupError(RuntimeError):
    """Stable path-free successor pickup refusal."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        self.recovery = _RECOVERY.get(
            code, "restore exact repository evidence and retry")
        super().__init__(f"{code}: {detail}")

    def public_result(self) -> JsonObject:
        return {
            "schema": PUBLIC_RESULT_SCHEMA,
            "status": "refused", "code": self.code,
            "detail": self.detail, "recovery": self.recovery,
        }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")


def _fingerprinted(material: Mapping[str, object]) -> JsonObject:
    value = copy.deepcopy(dict(material))
    return {**value, "fingerprint": hashlib.sha256(_canonical(value)).hexdigest()}


def _git(checkout: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=checkout, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", check=False)
    if completed.returncode:
        raise PhasePickupError(
            "source-stale", "repository revision is unavailable")
    return completed.stdout.strip()


def _validated_repository_handoff(
        checkout: str, handoff: HandoffInput, *,
        allowed_task_id: str | None = None) -> JsonObject:
    try:
        if isinstance(handoff, Mapping):
            checked = phase_handoff.validate_repository_manifest(
                checkout, handoff, require_clean=True,
                allowed_task_id=allowed_task_id)
        else:
            checked = phase_handoff.load_phase_handoff(
                checkout, os.fspath(handoff), require_clean=True,
                allowed_task_id=allowed_task_id)
        design_contract.validate_phase_authority_chain(checked)
        return checked
    except phase_handoff.PhaseHandoffError as exc:
        raise PhasePickupError(exc.code, exc.detail) from exc
    except design_contract.PhaseGateAuthorityError as exc:
        code, _, detail = str(exc).partition(":")
        raise PhasePickupError(
            code if code in _RECOVERY else "authority-stale",
            detail.strip() or "human authority could not be verified") from exc


def _validated_handoff_value(handoff: Mapping[str, object]) -> JsonObject:
    try:
        checked = phase_handoff.validate_phase_handoff(handoff)
        design_contract.validate_phase_authority_chain(checked)
        return checked
    except phase_handoff.PhaseHandoffError as exc:
        raise PhasePickupError(exc.code, exc.detail) from exc
    except design_contract.PhaseGateAuthorityError as exc:
        code, _, detail = str(exc).partition(":")
        raise PhasePickupError(
            code if code in _RECOVERY else "authority-stale",
            detail.strip() or "human authority could not be verified") from exc


def _proof_paths(proof: object) -> list[str]:
    if not isinstance(proof, str) or not proof.strip() or proof != proof.strip():
        raise PhasePickupError("proof-invalid", "proof command is invalid")
    try:
        argv = shlex.split(proof)
    except ValueError as exc:
        raise PhasePickupError(
            "proof-invalid", "proof command is invalid") from exc
    if len(argv) == 1:
        targets = argv
    else:
        executable = os.path.basename(argv[0]) if argv else ""
        if executable.startswith("python") and argv[1:3] == ["-m", "pytest"]:
            arguments = argv[3:]
        elif executable in {"pytest", "py.test"}:
            arguments = argv[1:]
        else:
            raise PhasePickupError(
                "proof-invalid", "proof command must invoke pytest")
        allowed_flags = {
            "-q", "--quiet", "-x", "-s", "--disable-warnings",
            "--strict-markers", "--strict-config",
        }
        if any(item.startswith("-") and item not in allowed_flags and
               not item.startswith(("--maxfail=", "-v"))
               for item in arguments):
            raise PhasePickupError(
                "proof-invalid", "proof command has unsupported pytest options")
        targets = [item for item in arguments if not item.startswith("-")]
    if not targets:
        raise PhasePickupError(
            "proof-invalid", "proof command has no pytest target")
    paths = [target.split("::", 1)[0] for target in targets]
    if any(not path or os.path.isabs(path) or "\\" in path or path == ".." or
           path.startswith("../") or "/../" in path for path in paths):
        raise PhasePickupError("proof-invalid", "proof path is unsafe")
    return paths


def _validate_plan_proofs(checkout: str, handoff: Mapping[str, Any]) -> None:
    for task in handoff["tasks"]:
        for proof in task["proofs"]:
            for path in _proof_paths(proof):
                if not os.path.isfile(os.path.join(checkout, path)):
                    raise PhasePickupError(
                        "proof-invalid", "sealed proof file is missing")
                tracked = subprocess.run(
                    ["git", "ls-files", "--error-unmatch", "--", path],
                    cwd=checkout, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, check=False)
                if tracked.returncode:
                    raise PhasePickupError(
                        "proof-invalid", "sealed proof file is not tracked")


def _completed_task_ids(handoff: Mapping[str, Any]) -> set[str]:
    completed = {
        str(receipt["task_id"])
        for receipt in handoff["progress_receipts"]
        if receipt["phase"] == "build" and receipt["status"] == "green"
        and receipt["task_id"] is not None
    }
    if handoff["successor"]["mode"] == "same-phase-resume":
        for task in handoff["tasks"]:
            if task["id"] in completed and \
                    _remaining_obligation_ids(handoff, task):
                # Older writers emitted one receipt for an entire task.
                # Neither rerunning it nor inventing the missing historical
                # receipts is a valid continuation of that sealed evidence.
                raise PhasePickupError(
                    "receipt-lineage",
                    "prior Build receipt leaves task obligations unresolved; "
                    "return to Plan for explicit remaining-work recovery")
    return completed


def _remaining_obligation_ids(
        handoff: Mapping[str, Any], task: Mapping[str, Any]) -> list[str]:
    """Keep every matched obligation in its sealed canonical order."""
    remaining = set(handoff["progress"]["remaining"])
    if handoff["producer"] == {"phase": "plan", "outcome": "done"} and \
            handoff["successor"] == {
                "phase": "build", "mode": "next-phase"}:
        remaining = {obligation["id"] for obligation in handoff["obligations"]}
    task_acceptance = set(task["acceptance"])
    matched = [
        obligation for obligation in handoff["obligations"]
        if obligation["id"] in remaining and (
            obligation["id"] == task["id"] or
            set(obligation["acceptance"]) & task_acceptance)
    ]
    if any(not set(obligation["acceptance"]) <= task_acceptance
           for obligation in matched):
        raise PhasePickupError(
            "scope-widened",
            "sealed task does not cover every acceptance criterion in a "
            "matched obligation; return to Plan")
    return [str(obligation["id"]) for obligation in matched]


def select_ready_build_task(
        handoff: Mapping[str, Any], *,
        requested_task: str | Mapping[str, object] | None = None) -> JsonObject:
    """Select exactly the first unfinished dependency-ready ordinal task."""
    checked = _validated_handoff_value(handoff)
    producer = checked["producer"]
    successor = checked["successor"]
    fresh = producer == {"phase": "plan", "outcome": "done"} and \
        successor == {"phase": "build", "mode": "next-phase"}
    resume = producer == {"phase": "build", "outcome": "interrupted"} and \
        successor == {"phase": "build", "mode": "same-phase-resume"}
    if not (fresh or resume):
        raise PhasePickupError(
            "transition-invalid", "handoff does not authorize Build pickup")
    completed = _completed_task_ids(checked)
    ready = [task for task in checked["tasks"]
             if task["id"] not in completed and
             set(task["dependencies"]) <= completed and
             _remaining_obligation_ids(checked, task)]
    if not ready:
        raise PhasePickupError(
            "dependency-unmet", "no unfinished task is dependency-ready")
    selected: JsonObject = copy.deepcopy(ready[0])
    if requested_task is None:
        return selected
    if isinstance(requested_task, str):
        if requested_task != selected["id"]:
            raise PhasePickupError(
                "dependency-unmet", "requested task is not the next ready task")
    elif not isinstance(requested_task, Mapping) or \
            dict(requested_task) != selected:
        raise PhasePickupError(
            "scope-widened",
            "requested task differs from sealed scope, order, contracts, or proof")
    return selected


def _build_assignment(handoff: Mapping[str, Any], *, task: JsonObject,
                      base_revision: str,
                      attempt_id: str | None = None,
                      nonce: str | None = None) -> JsonObject:
    attempt = str(attempt_id or f"attempt-{secrets.token_hex(16)}").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", attempt):
        raise PhasePickupError("authoring-invalid", "attempt identity is invalid")
    full_reference = review_evidence.create_phase_full_envelope_reference(handoff)
    relevant_acceptance = [
        copy.deepcopy(row) for row in handoff["acceptance"]
        if row["id"] in task["acceptance"]
    ]
    scoped_view = _fingerprinted({
        "schema": BUILD_SCOPED_VIEW_SCHEMA,
        "handoff_fingerprint": handoff["fingerprint"],
        "task": copy.deepcopy(task),
        "acceptance": relevant_acceptance,
        "full_envelope_reference": full_reference,
    })
    result_schema = _fingerprinted({
        "schema": BUILD_RESULT_SCHEMA,
        "result_schema": checkpoint.PHASE_AUTHORING_RESULT_SCHEMA,
        "required": sorted(checkpoint.PHASE_AUTHORING_RESULT_FIELDS),
        "additional_properties": False,
    })
    producer = _fingerprinted({
        "schema": BUILD_PRODUCER_CONTRACT_SCHEMA,
        "phase": "build", "task_id": task["id"],
        "attempt_id": attempt, "read_only": False,
        "write_allow": list(task["scope"]),
        "contracts": list(task["contracts"]),
        "acceptance": list(task["acceptance"]),
        "proof_commands": list(task["proofs"]),
        "handoff_fingerprint": handoff["fingerprint"],
        "base_revision": base_revision,
    })
    lease = _fingerprinted({
        "schema": BUILD_ATTEMPT_LEASE_SCHEMA,
        "attempt_id": attempt, "task_id": task["id"],
        "phase": "build", "handoff_fingerprint": handoff["fingerprint"],
        "producer_contract_fingerprint": producer["fingerprint"],
        "nonce": nonce or secrets.token_hex(16),
    })
    bootstrap = _fingerprinted({
        "schema": BUILD_BOOTSTRAP_SCHEMA,
        "attempt_id": attempt, "task_id": task["id"],
        "producer_contract_fingerprint": producer["fingerprint"],
        "lease_fingerprint": lease["fingerprint"],
        "scoped_view_fingerprint": scoped_view["fingerprint"],
        "result_schema_fingerprint": result_schema["fingerprint"],
        "full_envelope_reference_fingerprint": full_reference["fingerprint"],
        "environment": {"TASKPLANE_TASK": task["id"]},
    })
    return _fingerprinted({
        "schema": BUILD_ASSIGNMENT_SCHEMA,
        "attempt_id": attempt,
        "handoff_id": handoff["handoff_id"],
        "handoff_fingerprint": handoff["fingerprint"],
        "base_revision": base_revision,
        "task": copy.deepcopy(task),
        "contract_bootstrap": bootstrap,
        "producer_contract": producer,
        "lease": lease,
        "scoped_view": scoped_view,
        "result_schema": result_schema,
        "full_envelope_reference": full_reference,
    })


def validate_build_assignment(
        assignment: object, handoff: Mapping[str, Any], *,
        expected_task: Mapping[str, object] | None = None,
        checkout: str | None = None) -> JsonObject:
    if not isinstance(assignment, Mapping) or set(assignment) != \
            _ASSIGNMENT_FIELDS:
        raise PhasePickupError("authoring-invalid", "assignment fields are invalid")
    checked = _validated_handoff_value(handoff)
    task = select_ready_build_task(checked)
    if expected_task is not None and dict(expected_task) != task:
        raise PhasePickupError("scope-widened", "assignment task is not exact")
    if assignment.get("task") != task or \
            assignment.get("handoff_id") != checked["handoff_id"] or \
            assignment.get("handoff_fingerprint") != checked["fingerprint"]:
        raise PhasePickupError(
            "scope-widened", "assignment differs from the sealed ready task")
    base_revision = assignment.get("base_revision")
    if not isinstance(base_revision, str) or not _GIT_OBJECT.fullmatch(
            base_revision):
        raise PhasePickupError("authoring-invalid", "assignment base is invalid")
    lease_value = assignment.get("lease")
    nonce = lease_value.get("nonce") if isinstance(lease_value, Mapping) else None
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise PhasePickupError("authoring-invalid", "assignment lease is invalid")
    if checkout is not None:
        manifest_path = phase_handoff.handoff_path(str(checked["handoff_id"]))
        export_revision = _git(
            checkout, "log", "-1", "--format=%H", "--", manifest_path)
        if base_revision != export_revision:
            raise PhasePickupError(
                "authoring-invalid",
                "assignment base is not the exact handoff export revision")
    material = {key: assignment[key] for key in assignment
                if key != "fingerprint"}
    if assignment.get("fingerprint") != hashlib.sha256(
            _canonical(material)).hexdigest():
        raise PhasePickupError("authoring-invalid", "assignment fingerprint is invalid")
    # Recompute every nested binding while retaining only the intentionally
    # fresh attempt id and nonce from this assignment.
    expected = _build_assignment(
        checked, task=task, base_revision=base_revision,
        attempt_id=str(assignment.get("attempt_id") or ""), nonce=nonce)
    if dict(assignment) != expected:
        raise PhasePickupError(
            "scope-widened", "assignment envelope is stale, foreign, or widened")
    return copy.deepcopy(expected)


def prepare_build_pickup(
        checkout: str, handoff: HandoffInput, *,
        requested_task: str | Mapping[str, object] | None = None,
        attempt_id: str | None = None) -> JsonObject:
    """Validate all portable authority, then mint one exact Build assignment."""
    checked = _validated_repository_handoff(checkout, handoff)
    _validate_plan_proofs(checkout, checked)
    task = select_ready_build_task(checked, requested_task=requested_task)
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
