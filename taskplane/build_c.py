"""Build phase lease boundaries, explicit scope assignment and pickup."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
import hashlib
import json
import re

import checkpoint
import repository
import taskplane_lite as tp

if __package__:
    from . import delivery_policy
else:  # pragma: no cover - direct CLI module loading
    import delivery_policy


INTEGRATION_AUTHORIZATION_SCHEMA = "taskplane.build-c-integration-authorization/v1"
SCOPE_ASSIGNMENT_SCHEMA = "taskplane.scope-disjoint-assignment/v1"






class ScopeAssignmentError(RuntimeError):
    """Direct BUILD-C assignment could not preserve scope isolation."""


class IntegrationAuthorizationError(RuntimeError):
    """A task cannot cross the BUILD-C integration boundary."""


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CHECKPOINT_RECEIPT_FIELDS = frozenset({
    "schema", "producer", "engine_fingerprint",
    "active_contract_fingerprint", "identity", "phase",
    "ordered_phases", "completed_phases", "command",
    "environment_fingerprint", "output", "result", "worktree_revision",
    "declared_scope", "predecessor_receipt_digests", "verdict",
    "receipt_digest",
})



def authorize_delivery_dispatch(
        delivery_mode_receipt: Mapping[str, object], *,
        lens_worker_factory: Callable[[str], object]) -> dict:
    """Validate delivery authority before any automatic worker is made."""
    workers = delivery_policy.automatic_lens_workers_for_dispatch(
        delivery_mode_receipt, lens_worker_factory)
    receipt = delivery_policy.validate_delivery_mode_receipt(
        delivery_mode_receipt)
    return {
        "schema": "taskplane.delivery-dispatch-authorization/v1",
        "delivery_mode_receipt": receipt,
        "automatic_lens_workers": workers,
        "automatic_lens_worker_count": len(workers),
    }


def _external_build_binding(dispatch, lease):
    from taskplane import delivery_ports
    if not isinstance(lease, delivery_ports.AttemptLease) or dispatch.bindings["phase_id"] != "build" or any(
            getattr(lease, key) != dispatch.bindings[key] for key in
            ("lease_id", "run_id", "phase_id", "attempt_id", "operation_id", "fencing_token")):
        raise ValueError("Build phase lease binding mismatch")


def prepare_build_phase(runtime, dispatch, *, lease_owner, lease, paths=()):
    """Reserve incumbent lease effects before emitting an external dispatch.

    This is not a launch callback and returns no invented worker identity.
    Any crash leaves the lease uncertain until a matching released observation.
    """
    _external_build_binding(dispatch, lease)
    prepared = runtime.prepare(dispatch)
    if lease_owner.pickup(lease)["continuation"] != "retry_same_attempt":
        raise ValueError("Build effects require reconciliation")
    def reserve(bound):
        runtime.prepare(dispatch)
        for path in paths:
            path.revalidate()
        lease_owner.revalidate(bound)
        return prepared
    return lease_owner.execute(lease, nonce_action=lambda action: action(), action=reserve, paths=paths)


def complete_build_phase(runtime, dispatch, observation, *, lease_owner, lease, terminal):
    from taskplane import agent_runtime, delivery_ports
    _external_build_binding(dispatch, lease)
    if not isinstance(terminal, delivery_ports.LeaseTerminalObservation) or \
            not observation.start_identity or not observation.terminal_identity or \
            terminal.terminal_identity != observation.terminal_identity:
        raise ValueError("Build requires matching released terminal evidence")
    lease_owner.reconcile(lease, terminal)
    if lease_owner.authorize(lease) is not True:
        raise ValueError("Build completion authority revoked")
    return runtime.complete(agent_runtime.PreparedDispatch(dispatch), replace(observation, effect_state="reconciled"))






















def _checkpoint_integration_receipt(
        receipt: object, *, task_id: str, run_id: str, revision: str,
        scope: list[str], active_contract: Mapping[str, object]) -> dict:
    if not isinstance(receipt, Mapping):
        raise IntegrationAuthorizationError(
            "engine checkpoint receipt is missing")
    unknown = sorted(set(receipt) - _CHECKPOINT_RECEIPT_FIELDS)
    missing = sorted(_CHECKPOINT_RECEIPT_FIELDS - set(receipt))
    if unknown:
        raise IntegrationAuthorizationError(
            "checkpoint receipt has caller-authored fields: " +
            ", ".join(unknown))
    if missing:
        raise IntegrationAuthorizationError(
            "engine checkpoint receipt is missing fields: " +
            ", ".join(missing))
    if receipt.get("schema") != checkpoint.CHECKPOINT_RECEIPT_SCHEMA or \
            receipt.get("producer") != "taskplane.checkpoint-engine/v1" or \
            receipt.get("verdict") != "green" or \
            (receipt.get("result") or {}).get("state") != "succeeded" or \
            (receipt.get("result") or {}).get("exit_code") != 0:
        raise IntegrationAuthorizationError(
            "integration requires an engine green checkpoint")
    digest = receipt.get("receipt_digest")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or \
            digest != checkpoint.receipt_digest(receipt):
        raise IntegrationAuthorizationError(
            "checkpoint receipt digest is invalid or mixed")
    identity = receipt.get("identity")
    if not isinstance(identity, Mapping) or set(identity) != {
            "run_id", "task_id", "checkpoint_id", "ac_ids"} or \
            identity.get("task_id") != task_id or \
            identity.get("run_id") != run_id:
        raise IntegrationAuthorizationError(
            "checkpoint task identity is mixed")
    if receipt.get("worktree_revision") != revision:
        raise IntegrationAuthorizationError(
            "checkpoint does not name the registered worktree tip")
    if receipt.get("declared_scope") != scope:
        raise IntegrationAuthorizationError(
            "checkpoint declared scope does not match the sealed task")
    if receipt.get("ordered_phases") != \
            list(checkpoint.ORDERED_CHECKPOINT_PHASES) or \
            receipt.get("completed_phases") != ["focused_proof"]:
        raise IntegrationAuthorizationError(
            "checkpoint phase evidence is missing or mixed")
    expected_contract = hashlib.sha256(json.dumps(
        active_contract, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")).hexdigest()
    if receipt.get("active_contract_fingerprint") != expected_contract or \
            not isinstance(receipt.get("engine_fingerprint"), str) or \
            not _DIGEST.fullmatch(receipt["engine_fingerprint"]):
        raise IntegrationAuthorizationError(
            "checkpoint is not bound to the active engine contract")
    return dict(receipt)






def assign_pickup_scope(checkout: str, micro_plan: Mapping[str, object]) -> dict:
    """Bind one explicit pickup element without registering private state."""
    if not isinstance(micro_plan, Mapping) or set(micro_plan) != {
            "element_id", "scope", "criterion", "fingerprint"}:
        raise ScopeAssignmentError("pickup micro-plan identity is invalid")
    scope = micro_plan.get("scope")
    criterion = micro_plan.get("criterion")
    if not isinstance(scope, list) or not scope or not isinstance(
            criterion, Mapping):
        raise ScopeAssignmentError("pickup micro-plan has no bounded scope")
    revision = tp.git_head(checkout)
    if not revision:
        raise ScopeAssignmentError("pickup assignment revision is unavailable")
    material = {
        "schema": SCOPE_ASSIGNMENT_SCHEMA, "mode": "pickup-stateless",
        "task_id": str(micro_plan["element_id"]), "scope": list(scope),
        "criterion_id": str(criterion.get("id") or ""),
        "revision": revision, "micro_plan_fingerprint": micro_plan["fingerprint"],
    }
    return {**material, "fingerprint": hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()}


def validate_pickup_evidence(checkpoint_receipt: object,
                             merge_receipt: object, *,
                             micro_plan: Mapping[str, object],
                             revision: str) -> tuple[dict, dict]:
    """Revalidate repository-resident pickup evidence for resume."""
    task_id = str(micro_plan.get("element_id") or "")
    scope = micro_plan.get("scope")
    if not task_id or not isinstance(scope, list):
        raise IntegrationAuthorizationError(
            "pickup evidence micro-plan is invalid")
    run_id = "pickup-" + str(micro_plan.get("fingerprint") or "")[:24]
    active_contract = {
        "schema": "taskplane.pickup-active-contract/v1",
        "task_id": task_id, "scope": list(scope), "revision": revision,
        "micro_plan_fingerprint": micro_plan.get("fingerprint"),
    }
    checked_checkpoint = _checkpoint_integration_receipt(
        checkpoint_receipt, task_id=task_id, run_id=run_id,
        revision=revision, scope=list(scope),
        active_contract=active_contract,
    )
    try:
        checked_merge = repository.validate_pickup_merge_receipt(
            merge_receipt, task_id=task_id, revision=revision
        )
    except repository.RepositoryAcquisitionError as exc:
        raise IntegrationAuthorizationError(str(exc)) from exc
    return checked_checkpoint, checked_merge


def run_pickup(checkout: str, micro_plan: Mapping[str, object], *,
               emit: Callable[[str], None]) -> dict:
    """Run one explicit AC through checkpoint and repository ownership."""
    assignment = assign_pickup_scope(checkout, micro_plan)
    emit("pickup.build_c.assigned")
    criterion = micro_plan["criterion"]
    proof = criterion["proof"]
    checkpoint_id = "pickup-" + hashlib.sha256(json.dumps({
        "assignment": assignment["fingerprint"],
        "criterion": criterion["id"],
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
    spec = {
        "schema": checkpoint.CHECKPOINT_SCHEMA,
        "checkpoint_id": checkpoint_id, "phase": "build",
        "ac_ids": [criterion["id"]], "predecessor_checkpoint_ids": [],
        "worktree_revision": assignment["revision"],
        "declared_scope": list(assignment["scope"]),
        "focused_proof": {"path": proof["path"], "argv": list(proof["argv"])},
        "ratchet_baseline": {"cycle_count": 0},
    }
    run_id = "pickup-" + str(micro_plan["fingerprint"])[:24]
    identity = {
        "schema": "taskplane.governed-command-identity/v1",
        "run_id": run_id, "task_id": assignment["task_id"],
    }
    active_contract = {
        "schema": "taskplane.pickup-active-contract/v1",
        "task_id": assignment["task_id"], "scope": assignment["scope"],
        "revision": assignment["revision"],
        "micro_plan_fingerprint": micro_plan["fingerprint"],
    }
    emit("pickup.checkpoint.started")
    try:
        receipt = checkpoint.run_and_mint_stateless(
            checkout, spec, identity=identity, active_contract=active_contract
        )
    except checkpoint.CheckpointSpecError as exc:
        raise IntegrationAuthorizationError(str(exc)) from exc
    emit("pickup.checkpoint.terminal")
    checked = _checkpoint_integration_receipt(
        receipt, task_id=assignment["task_id"], run_id=run_id,
        revision=assignment["revision"], scope=assignment["scope"],
        active_contract=active_contract,
    )
    merge_receipt = repository.RepositoryManager().accept_pickup_revision(
        checkout, task_id=assignment["task_id"],
        revision=assignment["revision"],
    )
    emit("pickup.integration.outcome")
    return {
        "checkpoint": checked,
        "integration": {
            "schema": INTEGRATION_AUTHORIZATION_SCHEMA,
            "status": "integrated", "task_id": assignment["task_id"],
            "authorized_revision": assignment["revision"],
            "checkpoint_receipt_digest": checked["receipt_digest"],
            "merge_receipt": merge_receipt,
        },
    }
