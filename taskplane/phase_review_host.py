"""Current-attempt native orchestration for repository-phase focused reviews.

Portable routing and result policy stay in phase_review. This adapter retains
only a disposable current-attempt startup, reuses the existing native intent,
contract and terminal receipt, and refuses to restart an observed child.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, TYPE_CHECKING, cast

if TYPE_CHECKING or __package__:
    from . import phase_admission, phase_handoff, phase_plan, phase_producer, phase_review
    from . import taskplane_lite as kernel
else:
    import phase_admission
    import phase_handoff
    import phase_plan
    import phase_producer
    import phase_review
    import taskplane_lite as kernel

Json = dict[str, Any]
SCHEMA = "taskplane.phase-review-host/v1"
_POLICY_FIELDS = ("coding", "read_only", "write_allow", "allowed_tools", "budget", "submission_contract")


def _identity(handoff: Json, owner_brief: Json) -> tuple[str, str]:
    phase = phase_review._phase(handoff)
    producer = owner_brief.get("producer_contract") or {}
    attempt = producer.get("attempt_id")
    if (owner_brief.get("protocol") != "repository-phase" or owner_brief.get("phase") != phase or
            producer.get("handoff_fingerprint") != handoff["fingerprint"] or
            not isinstance(attempt, str) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", attempt) or
            not isinstance(owner_brief.get("task_name"), str) or not owner_brief["task_name"]):
        raise ValueError("phase review host requires the exact current owner identity")
    return phase, attempt


def _cache_path(workspace: str, attempt: str, *, create: bool = False) -> Path:
    root = Path(workspace)
    if root.absolute() != root.resolve():
        raise ValueError("phase review workspace must be canonical")
    parent = root
    for part in (".taskplane", "phase-reviews"):
        parent /= part
        if parent.is_symlink() or parent.exists() and not parent.is_dir():
            raise ValueError("phase review cache parent is unsafe")
        if create:
            parent.mkdir(exist_ok=True)
    path = parent / (attempt + ".json")
    if path.is_symlink():
        raise ValueError("phase review cache cannot follow a symlink")
    return path


def _inputs(workspace: str, handoff: Json, owner_brief: Json, artifact: Json,
            candidate_references: list[Json], candidate_content: dict[str, bytes], *,
            startup: Json | None = None) -> Json:
    phase, attempt = _identity(handoff, owner_brief)
    return phase_review.prepare(handoff, attempt_id=attempt,
        requirement=phase_plan.selected_json(workspace, handoff, "requirement"),
        graph=phase_plan.selected_json(workspace, handoff, "graph"),
        design=artifact if phase == "design" else phase_plan.selected_json(workspace, handoff, "design"),
        plan=artifact if phase == "plan" else None,
        candidate_artifacts=candidate_references, candidate_content=candidate_content,
        startup=startup)


def _saved(workspace: str, handoff: Json, owner_brief: Json, artifact: Json,
           candidate_references: list[Json], candidate_content: dict[str, bytes], *,
           create: bool) -> Json:
    _, attempt = _identity(handoff, owner_brief)
    path = _cache_path(workspace, attempt)
    # Validate immutable inputs and all startup bounds before creating anything.
    if not path.exists():
        if not create:
            raise ValueError("phase review has no prepared current attempt")
        prepared = _inputs(workspace, handoff, owner_brief, artifact,
                           candidate_references, candidate_content)
        path = _cache_path(workspace, attempt, create=True)
    else:
        prepared = None
    with kernel.file_lock(str(path)):
        if path.exists():
            _, raw = phase_handoff._safe_regular_file(
                workspace, str(path.relative_to(workspace)), code="artifact-integrity")
            if len(raw) > kernel.MAX_STAGE_STARTUP_BYTES * 17:
                raise ValueError("phase review cache exceeds its closed worker bound")
            cached = json.loads(raw.decode("utf-8"))
            if (not isinstance(cached, dict) or set(cached) != {
                    "schema", "owner_task_name", "handoff_fingerprint", "startup", "plan"} or
                    cached.get("schema") != SCHEMA or
                    cached.get("owner_task_name") != owner_brief["task_name"] or
                    cached.get("handoff_fingerprint") != handoff["fingerprint"]):
                raise ValueError("phase review cache belongs to another owner or handoff")
            prepared = _inputs(workspace, handoff, owner_brief, artifact,
                candidate_references, candidate_content, startup=cached["startup"])
            if cached["plan"] != prepared["plan"]:
                raise ValueError("phase review cached candidate or route has changed")
        else:
            if prepared is None:
                raise ValueError("phase review attempt disappeared during preparation")
            cached = {"schema": SCHEMA, "owner_task_name": owner_brief["task_name"],
                      "handoff_fingerprint": handoff["fingerprint"],
                      "startup": prepared["startup"], "plan": prepared["plan"]}
            if len(kernel.canonical_json_bytes(cached)) > kernel.MAX_STAGE_STARTUP_BYTES * 17:
                raise ValueError("phase review cache exceeds its closed worker bound")
            kernel.atomic_write_json(str(path), cached)
    return prepared


def _task(handoff: Json, brief: Json) -> str:
    return f"{handoff['handoff_id']}:{brief['producer_contract']['attempt_id']}:{brief['lens']}"


def _contract(workspace: str, brief: Json) -> Json:
    value = kernel.build_contract(
        f"PHASE {brief['phase']} LENS: {brief['lens']}", read_only=True,
        write_allow=[brief["result_path"]], tools=["Read", "Grep", "Glob", "Write", "Edit"])
    return phase_producer.bind_output_submission(workspace, value, brief)


def _check_contract(workspace: str, handoff: Json, prepared: Json,
                    brief: Json, contract: Json) -> None:
    lifecycle = contract.get("worker_lifecycle") or {}
    wanted = _contract(workspace, brief)
    if (contract.get("task_id") != brief["task_slot"] or
            contract.get("phase_handoff_fingerprint") != handoff["fingerprint"] or
            contract.get("phase_startup") != prepared["startup"] or
            contract.get("phase_dispatch") != brief or
            any(contract.get(key) != wanted.get(key) for key in _POLICY_FIELDS) or
            lifecycle.get("stage") != "phase-" + brief["phase"] + "-review" or
            lifecycle.get("task") != _task(handoff, brief) or
            lifecycle.get("expected_task_name") != brief["task_name"] or
            lifecycle.get("expected_role_marker") != brief["role_marker"] or
            lifecycle.get("slot") != brief["task_slot"]):
        raise ValueError("phase review child contract is stale, widened or foreign")
    intent = contract.get("phase_dispatch_intent")
    if (not isinstance(intent, dict) or
            lifecycle.get("dispatch_intent_id") != intent.get("intent_id") or
            lifecycle.get("dispatch_intent_run_id") != (intent.get("identity") or {}).get("run_id")):
        raise ValueError("phase review child native intent is missing or foreign")


def _terminal(workspace: str, handoff: Json, prepared: Json,
              brief: Json, *, required: bool = True) -> Json | None:
    slot = brief["task_slot"]
    path = kernel._worker_terminal_path(workspace, slot)
    receipt = kernel.load_json(path, default=None)
    if receipt is None and not required:
        return None
    receipt_id = (receipt or {}).get("receipt_id")
    if not isinstance(receipt_id, str) or not re.fullmatch(r"worker-terminal-[a-f0-9]{24}", receipt_id):
        raise ValueError("phase review child has no observed terminal receipt")
    contract = kernel.load_json(os.path.join(kernel.tp_dir(workspace), "quarantine", "contracts",
        f"{slot}-{receipt_id.split('-')[-1]}.json"), default=None)
    if not isinstance(contract, dict):
        raise ValueError("phase review child has no retained released contract")
    _check_contract(workspace, handoff, prepared, brief, contract)
    lifecycle = contract["worker_lifecycle"]
    action = lifecycle.get("release_action")
    if lifecycle.get("status") != "released" or not isinstance(action, dict):
        raise ValueError("phase review child was not released by native lifecycle")
    kernel._verify_worker_release_action(workspace, slot, action, contract)
    kernel._verify_worker_terminal_receipt(workspace, slot, receipt, contract, action)
    if receipt.get("authority") != "host-lifecycle" or receipt.get("outcome") != "success":
        raise ValueError("phase review child did not complete; a new owner attempt is required")
    return cast(Json, receipt)


def _expect(workspace: str, handoff: Json, brief: Json, intent: Json) -> None:
    kernel.record_expected_dispatch(workspace, "lens", brief["role"], brief["model_tier"], brief["model"],
        ref=handoff["handoff_id"], task_name=brief["task_name"],
        reasoning_effort=brief["reasoning_effort"], role_marker_value=brief["role_marker"],
        dispatch_route=brief.get("dispatch_route"), intent_id=intent["intent_id"],
        intent_run_id=intent["identity"]["run_id"])


def prepare(workspace: str, handoff: Json, owner_brief: Json, artifact: Json,
            candidate_references: list[Json], candidate_content: dict[str, bytes]) -> Json:
    """Prepare or replay the exact selected children; never replace an owner."""
    prepared = _saved(workspace, handoff, owner_brief, artifact,
                      candidate_references, candidate_content, create=True)
    _, attempt = _identity(handoff, owner_brief)
    with kernel.file_lock(str(_cache_path(workspace, attempt)) + ".dispatch"):
        return _prepare_children(workspace, handoff, prepared)


def _prepare_children(workspace: str, handoff: Json, prepared: Json) -> Json:
    dispatches = []
    statuses = []
    for brief in prepared["dispatches"]:
        existing = kernel.worker_contract_for_stage(workspace,
            stage="phase-" + brief["phase"] + "-review", task=_task(handoff, brief))
        if existing is not None:
            contract = existing["contract"]
            _check_contract(workspace, handoff, prepared, brief, contract)
            activation = contract["worker_lifecycle"]["status"]
            if activation not in {"pending", "active"}:
                raise ValueError("phase review child lifecycle cannot be replayed")
            intent = phase_admission.create_intent(workspace, handoff, brief,
                                                    wait_policy=prepared["wait_policy"])
            if contract["phase_dispatch_intent"] != intent:
                raise ValueError("phase review child native intent has changed")
        elif _terminal(workspace, handoff, prepared, brief, required=False) is not None:
            statuses.append("completed")
            continue
        else:
            intent = phase_admission.create_intent(workspace, handoff, brief,
                                                    wait_policy=prepared["wait_policy"])
            contract = _contract(workspace, brief)
            contract.update({"task_id": brief["task_slot"],
                "phase_handoff_fingerprint": handoff["fingerprint"],
                "phase_dispatch": copy.deepcopy(brief), "phase_startup": copy.deepcopy(prepared["startup"]),
                "phase_dispatch_intent": intent})
            contract = kernel.prepare_worker_contract(workspace, contract,
                stage="phase-" + brief["phase"] + "-review", task=_task(handoff, brief),
                task_name=brief["task_name"], role_marker=brief["role_marker"])
            contract["worker_lifecycle"].update(dispatch_intent_id=intent["intent_id"],
                dispatch_intent_run_id=intent["identity"]["run_id"])
            kernel.activate(workspace, contract, task_slot_override=brief["task_slot"])
            activation = "pending"
        if activation == "pending":
            _expect(workspace, handoff, brief, intent)
        statuses.append(activation)
        dispatches.append({**copy.deepcopy(brief), "activation": activation,
            "dispatch_allowed": activation == "pending", "dispatch_intent": copy.deepcopy(intent)})
    return {"status": "ready" if "pending" in statuses else
            "working" if "active" in statuses else "completed",
            "dispatches": dispatches, "wait_policy": prepared["wait_policy"],
            "plan_fingerprint": prepared["plan"]["fingerprint"]}


def validate_dispatch(workspace: str, handoff: Json, contract: Json) -> Json:
    """Rebuild one child for the native dispatch hook without trusting its brief.

    The current cache is transport, not authority: canonical candidate bytes and
    every sealed input are reread and the entire plan/startup must agree before
    any cached task name, role, scope or intent can be consumed.
    """
    brief = contract.get("phase_dispatch") or {}
    producer = brief.get("producer_contract") or {}
    attempt = producer.get("attempt_id")
    if (brief.get("protocol") != "repository-phase-review" or
            not isinstance(attempt, str) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", attempt)):
        raise ValueError("phase review dispatch has no current child identity")
    path = _cache_path(workspace, attempt)
    _, raw = phase_handoff._safe_regular_file(
        workspace, str(path.relative_to(workspace)), code="artifact-integrity")
    if len(raw) > kernel.MAX_STAGE_STARTUP_BYTES * 17:
        raise ValueError("phase review cache exceeds its closed worker bound")
    cached = json.loads(raw.decode("utf-8"))
    if not isinstance(cached, dict):
        raise ValueError("phase review current cache is malformed")
    phase = phase_review._phase(handoff)
    # Output selection is lower-owned; validating a review child does not
    # depend on the higher owner dispatch coordinator.
    references = phase_handoff.phase_output_references(workspace, phase)
    paths = phase_handoff.phase_output_paths(phase)[:2]
    if len(references) == 3:
        paths.append("design/visual.html")
    contents = {str(reference["kind"]): phase_handoff._safe_regular_file(
        workspace, path, code="artifact-integrity")[1] for path, reference in zip(paths, references)}
    artifact = json.loads(contents[phase].decode("utf-8"))
    owner = {"protocol": "repository-phase", "phase": phase,
        "task_name": cached.get("owner_task_name"),
        "producer_contract": {"attempt_id": attempt, "handoff_fingerprint": handoff["fingerprint"]}}
    prepared = _saved(workspace, handoff, owner, artifact, references, contents, create=False)
    expected = [row for row in prepared["dispatches"] if row["task_name"] == brief.get("task_name")]
    if len(expected) != 1:
        raise ValueError("phase review dispatch does not identify one selected child")
    _check_contract(workspace, handoff, prepared, expected[0], contract)
    return cast(Json, expected[0])


def collect(workspace: str, handoff: Json, owner_brief: Json, artifact: Json,
            candidate_references: list[Json], candidate_content: dict[str, bytes]) -> Json:
    """Collect all selected judgments only after authentic exact-byte terminals."""
    prepared = _saved(workspace, handoff, owner_brief, artifact,
                      candidate_references, candidate_content, create=False)
    by_lens = {brief["lens"]: brief for brief in prepared["dispatches"]}
    results = {}
    observations = {}
    for lens_id, brief in by_lens.items():
        if kernel.worker_contract_for_stage(workspace,
                stage="phase-" + brief["phase"] + "-review", task=_task(handoff, brief)) is not None:
            raise ValueError("phase review is waiting for every selected native child")
        receipt = _terminal(workspace, handoff, prepared, brief)
        if receipt is None:
            raise ValueError("phase review child has no authenticated terminal")
        _, raw = phase_handoff._safe_regular_file(workspace, brief["result_path"], code="artifact-integrity")
        results[lens_id] = raw
        observations[lens_id] = receipt

    def observed(worker: Json, raw: bytes) -> None:
        brief = by_lens[worker["lens"]]
        digest = hashlib.sha256(raw).hexdigest()
        reference = {"schema": phase_handoff.ARTIFACT_REFERENCE_SCHEMA,
            "kind": brief["phase"] + "-lens-" + brief["lens"], "media_type": "application/json",
            "digest": digest, "bytes": len(raw), "destination": phase_handoff.artifact_destination(digest),
            "locator": "repo-artifact://sha256/" + digest}
        phase_producer.verify_output_observation(observations[worker["lens"]], brief, [reference])

    return phase_review.collect(prepared["plan"], results, verify_observation=observed)
