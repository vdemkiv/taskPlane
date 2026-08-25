"""BUILD-C phase authority and the bounded DEFINE review projection.

This module adds orchestration edges only.  Review membership, depth, slot
leases, and event waiting remain owned by the incumbent review/lens/loop
surfaces.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import os
import re

import depgraph
import checkpoint
import repository
import storage as runtime_storage
import taskplane_lite as tp


PROGRAM_LEDGER_SCHEMA = "taskplane.program-phase-ledger/v1"
DEFINE_PROJECTION_SCHEMA = "taskplane.define-projection/v1"
SCOPE_ASSIGNMENT_SCHEMA = "taskplane.scope-disjoint-assignment/v1"
INTEGRATION_AUTHORIZATION_SCHEMA = \
    "taskplane.build-c-integration-authorization/v1"


class ProgramAuthorityError(RuntimeError):
    """The requested program phase has no predecessor authority."""


class DefineProjectionError(RuntimeError):
    """DEFINE could not preserve the bounded quick-review contract."""


class ScopeAssignmentError(RuntimeError):
    """Direct BUILD-C assignment could not preserve scope isolation."""


class IntegrationAuthorizationError(RuntimeError):
    """A task cannot cross the BUILD-C integration boundary."""


_LEGACY_BUILD_STATE = frozenset({
    "wave", "waves", "claim", "claims", "build_lease", "build_leases",
    "slot_lease", "slot_leases", "lens_state", "per_task_contract",
    "evaluate_state", "fix_state", "_stage_bindings",
})
_BRANCH_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CHECKPOINT_RECEIPT_FIELDS = frozenset({
    "schema", "producer", "engine_fingerprint",
    "active_contract_fingerprint", "identity", "phase",
    "ordered_phases", "completed_phases", "command",
    "environment_fingerprint", "output", "result", "worktree_revision",
    "declared_scope", "predecessor_receipt_digests", "verdict",
    "receipt_digest",
})
_integration_state_loader: Callable[[str], object] | None = None
_wait_policy_factory: Callable[[str, int], dict] | None = None
_wait_invocation_factory: Callable[[Mapping[str, object],
                                    list[str]], dict] | None = None


def bind_loop_runtime(
        *, state_loader: Callable[[str], object],
        wait_policy_factory: Callable[[str, int], dict],
        wait_invocation_factory: Callable[[Mapping[str, object],
                                           list[str]], dict]) -> None:
    """Bind loop-owned services without creating a BUILD-C -> loop edge."""
    if not all(callable(value) for value in (
            state_loader, wait_policy_factory, wait_invocation_factory)):
        raise TypeError("BUILD-C loop runtime services must be callable")
    global _integration_state_loader, _wait_policy_factory
    global _wait_invocation_factory
    _integration_state_loader = state_loader
    _wait_policy_factory = wait_policy_factory
    _wait_invocation_factory = wait_invocation_factory


def _scope_overlap(left: Mapping[str, object],
                   right: Mapping[str, object]) -> bool:
    left_modules = set(left["modules"])
    right_modules = set(right["modules"])
    if left_modules & right_modules:
        return True
    left_stems = tp.scope_stems(left["scope"])
    right_stems = tp.scope_stems(right["scope"])
    return any(tp.seg_prefix(a, b) or tp.seg_prefix(b, a)
               for a in left_stems for b in right_stems)


def _direct_worktree(ws: str, task_id: str, revision: str) -> str:
    """Create one isolated worktree through the incumbent repository owner."""
    path = runtime_storage.task_worktree_path(ws, task_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    token = _BRANCH_SAFE.sub("-", str(task_id)).strip("-.") or "task"
    manager = repository.RepositoryManager()
    manager._run(  # noqa: SLF001 - the incumbent owner is the live boundary
        ["git", "worktree", "add", "-b", f"tp/{token}", path, revision],
        cwd=ws)
    return path


def _assignment_wait(
        member_ids: list[str], *,
        wait_policy_factory: Callable[[str, int], dict] | None,
        wait_invocation_factory: Callable[[Mapping[str, object],
                                           list[str]], dict] | None) \
        -> tuple[dict, dict]:
    if wait_policy_factory is None or wait_invocation_factory is None:
        wait_policy_factory = wait_policy_factory or _wait_policy_factory
        wait_invocation_factory = (wait_invocation_factory or
                                   _wait_invocation_factory)
    if wait_policy_factory is None or wait_invocation_factory is None:
        raise ScopeAssignmentError(
            "direct assignment loop runtime services are unavailable")
    policy = wait_policy_factory("build-c-direct", len(member_ids))
    if not isinstance(policy, Mapping) or \
            policy.get("schema") != "taskplane.wait-policy/v1" or \
            policy.get("mode") != "event" or \
            policy.get("scheduled_polling") is not False or \
            int(policy.get("timeout_seconds") or 0) < 1800 or \
            policy.get("reissue_after") != ["completion", "attention"] or \
            int(policy.get("outstanding_count") or 0) != len(member_ids):
        raise ScopeAssignmentError(
            "direct assignment requires one non-polling event wait")
    invocation = wait_invocation_factory(policy, member_ids)
    if not isinstance(invocation, Mapping) or \
            invocation.get("schema") != \
            "taskplane.event-wait-invocation/v1" or \
            invocation.get("operation") != "wait_for_events" or \
            invocation.get("scheduled") is not False or \
            invocation.get("reissue") is not False or \
            list(invocation.get("outstanding_members") or []) != member_ids:
        raise ScopeAssignmentError(
            "direct assignment event wait invocation is invalid")
    return dict(policy), dict(invocation)


def assign_scopes(
        ws: str, state: Mapping[str, object], *, graph: dict | None = None,
        revision: str | None = None,
        create_worktree: Callable[[str, str, str], str] = _direct_worktree,
        register_worktree: Callable[[str, str, str], object] =
        runtime_storage.register_task_worktree,
        wait_policy_factory: Callable[[str, int], dict] | None = None,
        wait_invocation_factory: Callable[[Mapping[str, object],
                                           list[str]], dict] | None = None) \
        -> dict:
    """Assign the first deterministic set of ready graph-disjoint tasks.

    This is the thin BUILD-C path: it creates and registers isolated
    worktrees directly.  It deliberately creates no legacy wave, claim,
    build-lease, per-task review, Evaluate, or Fix state.
    """
    if not isinstance(state, Mapping):
        raise ScopeAssignmentError("direct assignment state is invalid")
    present_legacy = sorted(key for key in _LEGACY_BUILD_STATE
                            if state.get(key))
    if present_legacy:
        raise ScopeAssignmentError(
            "direct assignment refuses legacy BUILD state: " +
            ", ".join(present_legacy))
    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        raise ScopeAssignmentError("sealed Plan tasks are missing")
    graph = graph if isinstance(graph, dict) else depgraph.load(ws)
    modules = graph.get("modules")
    if not isinstance(modules, Mapping) or not modules:
        raise ScopeAssignmentError("dependency graph identity is unavailable")
    revision = str(revision or tp.git_head(ws) or "").strip()
    if not revision:
        raise ScopeAssignmentError("assignment revision is unavailable")

    passed = {str(row.get("id")) for row in tasks
              if isinstance(row, Mapping) and
              row.get("status") in {"passed", "accepted"}}
    candidates = []
    for row in tasks:
        if not isinstance(row, Mapping) or row.get("status") != "pending":
            continue
        task_id = str(row.get("id") or "").strip()
        scope = list(row.get("scope") or [])
        deps = {str(value) for value in row.get("deps") or []}
        if not task_id or not scope or not deps <= passed:
            continue
        graph_modules = depgraph.scope_modules(ws, scope)
        if not graph_modules or any(value not in modules
                                    for value in graph_modules):
            raise ScopeAssignmentError(
                f"task {task_id} has ambiguous graph identity")
        candidates.append({"task_id": task_id, "scope": scope,
                           "modules": sorted(graph_modules)})

    selected: list[dict] = []
    serialized = []
    for candidate in candidates:
        blocker = next((row for row in selected
                        if _scope_overlap(candidate, row)), None)
        if blocker is not None:
            serialized.append({"task_id": candidate["task_id"],
                               "blocked_by": blocker["task_id"],
                               "reason": "scope_overlap"})
        else:
            selected.append(candidate)
    if not selected:
        raise ScopeAssignmentError("no ready scope can be assigned")

    member_ids = [row["task_id"] for row in selected]
    wait_policy, wait_invocation = _assignment_wait(
        member_ids, wait_policy_factory=wait_policy_factory,
        wait_invocation_factory=wait_invocation_factory)
    dispatch_material = {"revision": revision, "members": member_ids}
    dispatch_id = "build-c-direct-" + hashlib.sha256(json.dumps(
        dispatch_material, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()[:20]

    assignments = []
    for candidate in selected:
        task_id = candidate["task_id"]
        worker = os.path.realpath(create_worktree(ws, task_id, revision))
        registration = register_worktree(ws, worker, task_id)
        if not isinstance(registration, Mapping) or \
                registration.get("schema") != \
                "taskplane.managed-task-worktree/v1" or \
                registration.get("task_id") != task_id or \
                os.path.realpath(str(registration.get("path") or "")) != \
                worker or not str(registration.get("branch_tip") or ""):
            raise ScopeAssignmentError(
                f"task {task_id} registration identity is invalid")
        assignments.append({
            "task_id": task_id, "scope": candidate["scope"],
            "graph_modules": candidate["modules"], "worktree": worker,
            "registration": dict(registration),
        })

    material = {
        "schema": SCOPE_ASSIGNMENT_SCHEMA, "revision": revision,
        "assignments": assignments, "serialized": serialized,
        "dispatch_set": {
            "schema": "taskplane.dispatch-set/v1", "id": dispatch_id,
            "concurrent": True, "member_count": len(assignments),
            "members": member_ids,
        },
        "wait_policy": wait_policy,
        "wait_invocation": wait_invocation,
    }
    return {**material, "fingerprint": hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()}


def _integration_state(ws: str) -> Mapping[str, object]:
    if _integration_state_loader is None:
        raise IntegrationAuthorizationError(
            "live BUILD-C integration state loader is unavailable")
    state = _integration_state_loader(ws)
    if not isinstance(state, Mapping):
        raise IntegrationAuthorizationError(
            "live BUILD-C integration state is missing")
    return state


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


def _green_predecessor_digests(
        tasks: list[object], task: Mapping[str, object]) -> list[str]:
    by_id = {str(row.get("id")): row for row in tasks
             if isinstance(row, Mapping) and row.get("id")}
    digests = []
    for dependency in task.get("deps") or []:
        dependency_id = str(dependency)
        predecessor = by_id.get(dependency_id)
        if not isinstance(predecessor, Mapping) or \
                predecessor.get("status") not in {
                    "passed", "accepted", "integrated"}:
            raise IntegrationAuthorizationError(
                f"predecessor {dependency_id} is not green")
        authorization = predecessor.get("integration_authorization")
        digest = ((authorization or {}).get("checkpoint_receipt_digest")
                  if isinstance(authorization, Mapping) else None)
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise IntegrationAuthorizationError(
                f"predecessor {dependency_id} has no green integration "
                "authorization")
        digests.append(digest)
    return digests


def integrate_on_green(primary_checkout: str, task_id: str) -> dict:
    """Integrate exactly the engine-checkpointed registered task revision.

    The receipt is deliberately not a parameter: only the checkpoint nested
    in the live engine submission can authorize this boundary.  Branch tips,
    caller-authored receipt lookalikes, and stale or mixed identities therefore
    cannot become merge authority.
    """
    primary = os.path.realpath(primary_checkout)
    state = _integration_state(primary)
    if state.get("step") != "execute":
        raise IntegrationAuthorizationError(
            "BUILD-C integration is available only during execute")
    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        raise IntegrationAuthorizationError("sealed Plan tasks are missing")
    task = next((row for row in tasks if isinstance(row, Mapping) and
                 row.get("id") == task_id), None)
    if task is None:
        raise IntegrationAuthorizationError(
            f"task {task_id} is not in the sealed Plan")
    submission = task.get("_submission")
    if not isinstance(submission, Mapping) or \
            submission.get("task") != task_id or \
            submission.get("outcome") != "pass":
        raise IntegrationAuthorizationError(
            "engine checkpoint receipt is missing from a green submission")
    try:
        registration = runtime_storage.refresh_task_worktree_tip(
            primary, task_id)
    except Exception as exc:
        raise IntegrationAuthorizationError(
            f"registered worktree identity is invalid: {exc}") from exc
    worker = os.path.realpath(str(registration.get("path") or ""))
    if os.path.realpath(str(task.get("workspace") or "")) != worker:
        raise IntegrationAuthorizationError(
            "task workspace and managed registration are mixed")
    active_contract = tp.load_active(worker)
    if not isinstance(active_contract, Mapping):
        raise IntegrationAuthorizationError(
            "checkpoint active engine contract is missing")
    revision = str(registration.get("branch_tip") or "")
    scope = list(task.get("scope") or [])
    receipt = _checkpoint_integration_receipt(
        submission.get("checkpoint_receipt"), task_id=task_id,
        run_id=str(registration.get("run_id") or "legacy"),
        revision=revision, scope=scope, active_contract=active_contract)
    predecessor_digests = _green_predecessor_digests(tasks, task)
    if receipt.get("predecessor_receipt_digests") != predecessor_digests:
        raise IntegrationAuthorizationError(
            "checkpoint predecessor receipts are stale or mixed")
    try:
        merge_receipt = repository.RepositoryManager().merge_registered_task(
            primary, task_id=task_id,
            run_id=str(registration.get("run_id") or "legacy"))
    except Exception as exc:
        raise IntegrationAuthorizationError(
            f"repository integration failed closed: {exc}") from exc
    if not isinstance(merge_receipt, Mapping) or \
            merge_receipt.get("task_id") != task_id or \
            merge_receipt.get("branch_tip") != revision or \
            os.path.realpath(str(merge_receipt.get("managed_path") or "")) != \
            worker or \
            os.path.realpath(str(
                merge_receipt.get("primary_checkout") or "")) != primary:
        raise IntegrationAuthorizationError(
            "repository merge receipt does not match authorization")
    material = {
        "schema": INTEGRATION_AUTHORIZATION_SCHEMA,
        "status": "integrated", "task_id": task_id,
        "authorized_revision": revision,
        "checkpoint_receipt_digest": receipt["receipt_digest"],
        "predecessor_receipt_digests": predecessor_digests,
        "merge_receipt": dict(merge_receipt),
    }
    return {**material, "fingerprint": hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")).hexdigest()}


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


def _program_authority(ledger: Mapping[str, object]) -> Mapping[str, object]:
    if ledger.get("schema") != "taskplane.r0012-program-ledger/v1":
        raise ProgramAuthorityError("program ledger schema is invalid")
    authority = ledger.get("program_authority")
    if not isinstance(authority, Mapping) or \
            authority.get("schema") != PROGRAM_LEDGER_SCHEMA:
        raise ProgramAuthorityError("program phase authority is missing")
    return authority


def require_program_phase(ledger: Mapping[str, object], phase: str) -> dict:
    """Fail closed unless the requested R-0012 phase is currently eligible."""
    authority = _program_authority(ledger)
    approval = authority.get("consolidated_approval")
    approval = approval if isinstance(approval, Mapping) else {}
    if approval.get("approved") is not True or \
            not str(approval.get("actor") or "").strip() or \
            not str(approval.get("authority_receipt") or "").strip():
        raise ProgramAuthorityError(
            "attributed consolidated human approval is required")

    normalized = str(phase or "").strip().lower().replace("-", "")
    if normalized not in {"r0009", "r0010", "r0011"}:
        raise ProgramAuthorityError(f"unknown program phase {phase!r}")
    if normalized in {"r0010", "r0011"}:
        r0009 = authority.get("r0009")
        r0009 = r0009 if isinstance(r0009, Mapping) else {}
        if r0009.get("accepted") is not True or \
                not str(r0009.get("evidence_digest") or "").strip():
            raise ProgramAuthorityError(
                "R-0009 acceptance is required before R-0010")
    if normalized == "r0011":
        r0011 = authority.get("r0011")
        r0011 = r0011 if isinstance(r0011, Mapping) else {}
        if r0011.get("exact_sha_green") is not True or \
                not str(r0011.get("signed_off_by") or "").strip():
            raise ProgramAuthorityError(
                "R-0010 exact-SHA proof and human sign-off are required "
                "before R-0011")
    if normalized == "r0010":
        r0010 = authority.get("r0010")
        r0010 = r0010 if isinstance(r0010, Mapping) else {}
        if r0010.get("status") != "active":
            raise ProgramAuthorityError("R-0010 is not active")
    material = {
        "schema": PROGRAM_LEDGER_SCHEMA,
        "phase": normalized,
        "actor": str(approval["actor"]),
        "authority_receipt": str(approval["authority_receipt"]),
        "r0009_evidence": str(
            ((authority.get("r0009") or {}).get("evidence_digest") or "")),
    }
    return {**material, "status": "authorized", "fingerprint":
            hashlib.sha256(json.dumps(
                material, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()}


def _load_ledger(ws: str) -> dict:
    path = os.path.join(ws, "exports", "r0012-program-ledger.json")
    value = tp.load_json(path, default=None, what="R-0012 program ledger")
    if not isinstance(value, dict):
        raise ProgramAuthorityError("R-0012 program ledger is missing")
    return value


def program_enabled(ws: str) -> bool:
    """Return whether this checkout declares the governed R-0012 program."""
    try:
        _program_authority(_load_ledger(ws))
        return True
    except (OSError, ValueError, ProgramAuthorityError):
        return False


def _design_modules(ws: str) -> list[str]:
    contract = tp.load_json(
        os.path.join(ws, "design", "contract.json"), default=None,
        what="approved Design Contract")
    modules = ((contract or {}).get("graph") or {}).get("proposed_modules")
    if not isinstance(modules, list) or not modules:
        raise DefineProjectionError(
            "approved Design Contract has no proposed modules")
    return sorted({str(path) for path in modules if str(path).strip()})


def _define_impact(graph: Mapping[str, object], files: list[str]) -> dict:
    touched = []
    covered = set()
    modules = graph.get("modules")
    modules = modules if isinstance(modules, Mapping) else {}
    graph_files = graph.get("files")
    graph_files = graph_files if isinstance(graph_files, Mapping) else {}
    selected = set(files)
    for module_id, row in modules.items():
        declared_files = (row or {}).get("files") \
            if isinstance(row, Mapping) else None
        if isinstance(declared_files, Mapping):
            module_files = {str(path) for path in declared_files}
        elif isinstance(declared_files, (list, tuple, set, frozenset)):
            module_files = {str(path) for path in declared_files}
        elif isinstance(declared_files, int) and not isinstance(
                declared_files, bool):
            module = str(module_id).rstrip("/")
            prefix = f"{module}/"
            module_files = {
                str(path) for path in graph_files
                if str(path) == module or str(path).startswith(prefix)
            }
        else:
            module_files = set()
        overlap = selected & module_files
        if overlap:
            touched.append(str(module_id))
            covered.update(overlap)
    return {
        "touched": sorted(touched), "impacted": {},
        "total_impacted": len(touched),
        "unknown": sorted(selected - covered),
    }


def _validated_define_evidence(manifest: Mapping[str, object]) -> dict:
    """Validate and retain the ReviewKernel evidence emitted for DEFINE."""
    if manifest.get("status") != "ready" or manifest.get("stage") != "define":
        raise DefineProjectionError("DEFINE review did not become ready")
    if manifest.get("routing_mode") != "selective":
        raise DefineProjectionError(
            "DEFINE requires selective quick-only routing")
    slots = manifest.get("slots")
    if not isinstance(slots, list) or not 4 <= len(slots) <= 5:
        raise DefineProjectionError(
            "DEFINE must emit exactly four or five quick sweep slots")
    selected = []
    slot_ids = set()
    for slot in slots:
        if not isinstance(slot, Mapping):
            raise DefineProjectionError("DEFINE slot is malformed")
        slot_id = str(slot.get("slot_id") or "")
        lens_ids = slot.get("lens_ids")
        if not slot_id.startswith("sweep.") or \
                not isinstance(lens_ids, list) or len(lens_ids) != 1:
            raise DefineProjectionError(
                "DEFINE may contain quick sweep slots only")
        if slot_id in slot_ids or str(lens_ids[0]) in selected:
            raise DefineProjectionError("DEFINE slots must be unique")
        slot_ids.add(slot_id)
        selected.append(str(lens_ids[0]))
    if "architecture" not in selected:
        raise DefineProjectionError(
            "DEFINE quick sweep requires the architecture floor")

    slot_ids = [str(row["slot_id"]) for row in slots]
    depth = manifest.get("review_depth_policy")
    if not isinstance(depth, Mapping) or \
            depth.get("depth") != "quick-only" or \
            depth.get("deep_slots_allowed") is not False or \
            depth.get("deep_slots") != [] or \
            depth.get("promotion_attempts") != 0 or \
            not isinstance(depth.get("quick_slots"), list) or \
            sorted(depth["quick_slots"]) != sorted(slot_ids):
        raise DefineProjectionError(
            "DEFINE requires observed quick-only depth evidence")
    routing_counts = manifest.get("routing_counts")
    if not isinstance(routing_counts, Mapping) or \
            int(routing_counts.get("sweep") or 0) != len(slots) or \
            set(routing_counts) - {"sweep", "n/a"}:
        raise DefineProjectionError(
            "DEFINE may not dispatch deep, full, or 26-lens review")

    dispatch_sets = [row.get("dispatch_set") for row in slots]
    if not all(isinstance(row, Mapping) for row in dispatch_sets):
        raise DefineProjectionError(
            "DEFINE slots require router-produced dispatch evidence")
    dispatch_set = dict(dispatch_sets[0])
    if any(dict(row) != dispatch_set for row in dispatch_sets[1:]) or \
            dispatch_set.get("schema") != "taskplane.dispatch-set/v1" or \
            not str(dispatch_set.get("id") or "").strip() or \
            dispatch_set.get("concurrent") is not True or \
            int(dispatch_set.get("member_count") or 0) != len(slots):
        raise DefineProjectionError(
            "DEFINE requires one concurrent router-produced dispatch set")
    wait_policies = [row.get("wait_policy") for row in slots]
    if not all(isinstance(row, Mapping) for row in wait_policies) or any(
            row.get("schema") != "taskplane.wait-policy/v1" or
            row.get("outstanding_set") != dispatch_set["id"] or
            int(row.get("outstanding_count") or 0) != len(slots) or
            row.get("mode") != "event" or
            int(row.get("timeout_seconds") or 0) < 1800 or
            row.get("scheduled_polling") is not False or
            set(row.get("reissue_after") or []) != {"completion", "attention"}
            for row in wait_policies):
        raise DefineProjectionError(
            "DEFINE requires router-produced event wait evidence")
    return {
        "selected_lenses": [
            "architecture", *sorted(set(selected) - {"architecture"})],
        "dispatch_set": dispatch_set,
        "automatic_deep": bool(depth.get("deep_slots")),
        "automatic_full": manifest.get("routing_mode") == "all",
        "serial_fallback": dispatch_set.get("concurrent") is not True,
    }


def validate_define_projection(manifest: Mapping[str, object]) -> list[str]:
    """Validate only the invariants added at DEFINE; never select again."""
    return _validated_define_evidence(manifest)["selected_lenses"]


def project_define(
        ws: str, state: Mapping[str, object], *,
        start_review: Callable[..., dict] | None = None,
        selector: Callable[..., dict] | None = None,
        bind_actions: Callable[..., dict] | None = None,
        graph: dict | None = None, revision: str | None = None) -> dict:
    """Invoke ReviewKernel once and expose its quick slots concurrently.

    The emitted inline catalog is contained in ReviewKernel briefs as priming
    evidence.  This function does not route it again or create any other slot.
    """
    authority = require_program_phase(_load_ledger(ws), "r0010")
    actor = str(state.get("design_approved_by") or "").strip()
    fingerprint = str(state.get("design_fingerprint") or "").strip()
    if not actor or actor == "(unattributed)" or not fingerprint:
        raise ProgramAuthorityError(
            "DEFINE requires attributed approved Design authority")
    files = _design_modules(ws)
    graph = graph if isinstance(graph, dict) else depgraph.load(ws)
    revision = str(revision or tp.git_head(ws) or "")
    target_material = {
        "stage": "define", "revision": revision,
        "design_fingerprint": fingerprint,
    }
    target = {"head": revision, **target_material, "fingerprint":
              hashlib.sha256(json.dumps(
                  target_material, sort_keys=True, separators=(",", ":")
              ).encode("utf-8")).hexdigest()}
    requirement = {
        "id": str(state.get("requirement_id") or "R-0012"),
        "text": str(state.get("goal") or "Approved Design definition"),
        "review_policy": {"depth": "quick-only"},
    }
    if selector is None:
        raise DefineProjectionError(
            "DEFINE requires an observable ReviewKernel selector")
    if start_review is None:
        raise DefineProjectionError(
            "DEFINE requires the loop-owned ReviewKernel entry")
    if bind_actions is None:
        raise DefineProjectionError(
            "DEFINE requires the loop-owned review binding entry")
    selector_invocations = 0

    def observed_router() -> dict:
        nonlocal selector_invocations
        selector_invocations += 1
        return selector(
            files, task_type="design", breadth="routed", stage="define",
            workspace=ws, requirement_text=requirement["text"])

    manifest = start_review(
        ws, target=target, graph=graph,
        impact=_define_impact(graph, files),
        diff={"files": files, "changed_symbols": []},
        requirement=requirement,
        acceptance=list(state.get("acceptance") or []),
        contracts=["contract:define.design-review",
                   "contract:review.routing",
                   "contract:review.depth-boundary"],
        stage="define", task_type="design", base="HEAD",
        router=observed_router)
    if selector_invocations != 1:
        raise DefineProjectionError(
            "DEFINE requires exactly one selector invocation")
    evidence = _validated_define_evidence(manifest)
    bound = bind_actions(ws, manifest, task_id="define")
    wait = bound.get("wait_invocation") if isinstance(bound, Mapping) else None
    if not isinstance(wait, Mapping) or wait.get("scheduled") is not False or \
            wait.get("reissue") is not False or \
            wait.get("operation") != "wait_for_events" or \
            int(wait.get("timeout_seconds") or 0) < 1800:
        raise DefineProjectionError(
            "DEFINE requires exactly one unscheduled event wait")
    members = list(wait.get("outstanding_members") or [])
    slots = list(bound.get("slots") or [])
    if members != [str(row.get("slot_id") or "") for row in slots]:
        raise DefineProjectionError(
            "DEFINE wait must cover the emitted quick slots exactly once")
    if any(not isinstance(row, Mapping) or
           dict(row.get("dispatch_set") or {}) != evidence["dispatch_set"]
           for row in slots):
        raise DefineProjectionError(
            "DEFINE binding must preserve router-produced dispatch evidence")
    for row in slots:
        bootstrap = row.get("contract_bootstrap")
        if not isinstance(bootstrap, Mapping) or \
                bootstrap.get("activation_order") != \
                "orchestrator_before_subagent_start" or \
                bootstrap.get("environment") != {
                    "TASKPLANE_TASK": bootstrap.get("task_slot")}:
            raise DefineProjectionError(
                "DEFINE producer activation must precede SubagentStart")
    collection = bound.get("collection")
    if not isinstance(collection, Mapping) or \
            collection.get("schema") != \
            "taskplane.review-collection-bridge/v1" or \
            collection.get("function") != "loop.collect_review_bridge" or \
            collection.get("run_id") != bound.get("run_id") or \
            collection.get("release_incomplete_producers") is not True:
        raise DefineProjectionError(
            "DEFINE requires the producer-releasing collection bridge")
    return {
        "schema": DEFINE_PROJECTION_SCHEMA,
        "status": "ready", "stage": "define",
        "program_authority": authority,
        "run_id": str(bound.get("run_id") or ""),
        "selected_lenses": evidence["selected_lenses"],
        "dispatch_set": evidence["dispatch_set"],
        "slots": slots, "wait_invocation": dict(wait),
        "collection": dict(collection),
        "selector_invocations": selector_invocations,
        "automatic_deep": evidence["automatic_deep"],
        "automatic_full": evidence["automatic_full"],
        "serial_fallback": evidence["serial_fallback"],
    }
