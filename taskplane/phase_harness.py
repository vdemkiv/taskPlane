"""Phase-attempt decisions through the existing run journal and lifecycle ports.

No session owns an attempt. Reads preserve preparation and uncertainty;
explicit retry uses the same journal and never fabricates host completion.
"""
from collections.abc import Mapping
import os
import re
import time
from typing import Any


def compile_brief(context: dict[str, Any], action: dict[str, Any], requirement: object,
                  workspace: str | None = None) -> None:
    """Compile one role's obligations from the admitted phase definition.

    Domain evidence/submission checks stay with their existing owners. This
    function does not approve, collect, invent a lens route, or author output.
    """
    import copy
    from taskplane import storage
    definition = context["definition"]
    phase = definition["id"]
    evaluator_result, review_root = storage.instruction_artifact_paths(workspace)
    action["execution_mode"] = "stateless-phase"
    action["phase_definition"] = copy.deepcopy(definition)
    action["requirement"] = copy.deepcopy(requirement)
    policy = resource_policy(context.get("manifest", {}), str(context.get("run_id") or ""))
    if policy is not None:
        action["resource_policy"] = policy
    action["phase_outputs"] = [{**row,
        "path": action["phase_runtime"]["outputs"].get(row["artifact_class"]),
        "owner": "worker" if row["artifact_class"] in action["phase_runtime"]["outputs"] else "runtime"}
        for row in definition["produces"]]
    instruction = (
        "Execute the selected stateless phase using only this action's saved inputs, "
        "stage startup and sealed package; do not recover predecessor conversations or workspaces. "
        "The phase_definition owns role, working/evaluation lenses, budget and output schemas. "
        "Write each worker-owned phase_outputs candidate at its exact declared path. "
        "Runtime-owned outputs are produced by the collector; do not fabricate them. "
        "An empty lens set authorizes no lens workers or legacy focused routing. "
        "Use contract_bootstrap.environment for scoped operations; an actual host Start must bind "
        "the slot before authorship. Preserve requirement identity, scope and acceptance criteria. "
        "Do not change run scope or approval, clear contracts or fabricate lifecycle observations. ")
    if phase == "product":
        instruction += (
            "Author a requirement candidate with schema taskplane.requirement/v1, the supplied id "
            "and title, and nonempty acceptance_criteria preserving the supplied acceptance criteria. "
            "Do not call req new or loop submit, or substitute specs/spec.md for the declared JSON. "
            "Return after writing; host Stop collects it and the orchestrator owns the gate. "
            "Do not claim native acceptance in the requirement artifact.")
    else:
        # Explicit domain ports retain substantive acceptance without importing
        # legacy routing, role changes or predecessor-session instructions.
        obligations = {
            "design": (
                "Remain read-only toward product code. Compare alternatives against the sealed "
                "requirement and baseline graph; define modules, contracts, graph DoR/DoD, depth "
                "policy, acceptance selectors, risks and rollout. Apply solution-design judgment. "
                "The design candidate uses taskplane.design/v1 and includes its selected "
                "test_strategy; author the declared test-strategy candidate too. Preserve the "
                "contract-required design/design.md narrative and design/contract.json projection "
                "for the existing substantive Design validator. Do not mutate the as-built graph. "
                "Return without loop submit; the orchestrator validates and presents human approval."),
            "plan": (
                "Plan from the sealed requirement, approved Design and test strategy. At the "
                "declared plan-task path write a raw plan with a nonempty tasks array (or one raw "
                "task), not an already-sealed taskplane.plan-task/v1 envelope. Each task needs "
                "scope, tests as ONE command string, criteria, dependencies, exact contract ids, "
                "design_edges and impact policy. Cover approved modules, edges, depth policy and "
                "acceptance without drift. Derive dependency impact once, not per worker. Preserve "
                "plan/tasks.json and plan/plan.md required by the existing substantive Plan validator. "
                "The collector seals Design quality authority and dependency outputs; never invent "
                "their receipts. Return without loop submit; only the orchestrator requests a gate."),
            "build": (
                "Implement only the supplied task under its contract and approved Design, using "
                "TDD and the declared phase lenses. For a fix, repair the listed failures and add "
                "a regression test. Preserve the bound test command, test-strategy authority and "
                "real evidence. The runtime produces realized-conformance. Report the actual "
                "outcome with loop submit pass or fail; submission alone is not a gate."),
            "evaluate": (
                "Remain read-only. Start with tp loop evidence --write once for the exact diff, "
                "bound tests, acceptance, impact, contracts, Design conformance and provenance. "
                "Do not dispatch lens workers. Prove each criterion against real behavior; record "
                f"findings and an overall judgment in {evaluator_result} as required by the "
                "output contract as well as the declared phase candidate. Then loop submit pass "
                "or fail. Use structured unavailability and loop submit unavailable only when "
                "the existing bounded-attempt, green-test and no-product-defect conditions hold."),
            "engineering": (
                "Remain read-only. Consume Evaluate's sealed direct evidence and judge requirement "
                "conformance; do not launch a legacy review sweep. Missing substantive evidence "
                "must return to Evaluate, not be fabricated. Preserve the existing report.md and "
                f"findings.json output contract under {review_root}, including lens coverage, "
                "impact, approved Design conformance, tests and verdict. Record the verdict through "
                "the existing knowledge owner and loop submit the actual outcome. Only the "
                "orchestrator validates before presenting human sign-off."),
            "retro": (
                "Consume only the sealed terminal package and author the declared retrospective. "
                "Do not seal the loop, admit knowledge, mint publication authority or publish. "
                "Return through the existing host completion hook."),
        }
        instruction += obligations[phase] + (
            " Host Stop collects the exact attempt. Only the orchestrator may request a gate; "
            "collection is not human approval.")
    if policy is not None:
        instruction += (" The human's saved resource_policy makes execution resource limits advisory "
            "for this run. Preserve observed usage and declared limits; exceeding a limit is not "
            "a scope change, a retry grant or permission to bypass evidence or human gates.")
    action["instruction"] = instruction


def admit_lens_plan(context: dict[str, Any]) -> None:
    """Never substitute the legacy risk router for a declared phase plan."""
    definition = context["definition"]
    if definition["working_lenses"] or definition["evaluation_lenses"]:
        raise ValueError("selected phase requires an explicit phase-owned lens dispatch plan; legacy routing is not authority")


def _initial_operation(context: dict[str, Any]) -> str:
    from taskplane import stage_migration
    prepared = sorted((row for row in stage_migration.phase_records(context["manifest"]).values()
        if row["operation"] == "phase_prepare" and
        row["result"].get("stage_fingerprint") == context["stage"]["fingerprint"]),
        key=lambda row: row["committed_revision"])
    return prepared[0]["operation_id"] if prepared else "phase-attempt-" + context["stage"]["fingerprint"][:32]


def retry_chain(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Follow explicit retry grants in the existing run journal, never time."""
    from taskplane import stage_migration
    operation = _initial_operation(context)
    rows = sorted((row for row in stage_migration.phase_records(context["manifest"]).values()
        if row["operation"] == "phase_retry" and
        row["result"].get("stage_fingerprint") == context["stage"]["fingerprint"]),
        key=lambda row: row["committed_revision"])
    for row in rows:
        result = row["result"]
        request_keys = {"run_id", "stage_fingerprint", "previous_operation",
            "candidate_fingerprint", "actor", "worker_stopped", "additional_attempts"}
        from taskplane import review_evidence
        if set(result) != request_keys | {"schema", "next_operation", "previous_preparation",
                "contract_slot", "contract_reference", "routing", "old_result", "approved_in_session"} or \
                result.get("schema") != "taskplane.phase-retry/v1" or \
                review_evidence.content_fingerprint({key: result[key] for key in request_keys}) != row["request_fingerprint"] or \
                result.get("run_id") != context["run_id"] or \
                result.get("previous_operation") != operation or \
                result.get("additional_attempts") != 1 or \
                result.get("worker_stopped") is not True or \
                result.get("next_operation") != "phase-attempt-" + row["request_fingerprint"][:32]:
            raise ValueError("phase retry chain does not verify")
        operation = result["next_operation"]
    allowed = {_initial_operation(context)} | {row["result"]["next_operation"] for row in rows}
    if any(row["operation"] == "phase_prepare" and
            row["result"].get("stage_fingerprint") == context["stage"]["fingerprint"] and
            row["operation_id"] not in allowed
            for row in stage_migration.phase_records(context["manifest"]).values()):
        raise ValueError("multiple ungranted phase preparations")
    return rows


def operation_id(context: dict[str, Any]) -> str:
    retries = retry_chain(context)
    return (str(retries[-1]["result"]["next_operation"]) if retries else
            _initial_operation(context))


def usage_evidence(workspace: str, material: Mapping[str, Any], terminal: dict[str, Any]) -> dict[str, Any]:
    """An authenticated terminal's inline count or its exact native counter."""
    result = {"tokens": terminal["tokens"],
              "source": "host-stop" if terminal["tokens"] is not None else "unavailable"}
    if result["tokens"] is None and str(material["bindings"]["host_kind_version"]).startswith("codex:"):
        from taskplane import codex_identity
        try:
            snapshot = codex_identity.terminal_usage(workspace, terminal)
            result = {"tokens": snapshot["usage"]["total_tokens"], "source": "native-counter",
                      "snapshot_fingerprint": snapshot["fingerprint"]}
        except (ValueError, OSError):
            pass  # Unknown is never zero or a waiver.
    return result


def resource_policy(manifest: Mapping[str, Any], run_id: str) -> dict[str, Any] | None:
    """Read the human's run-wide resource decision from the existing journal."""
    from taskplane import stage_migration, review_evidence
    row = stage_migration.phase_records(manifest).get("run-resource-limits")
    if row is None:
        return None
    value = row["result"]
    if row["operation"] != "resource_policy" or not isinstance(value, dict) or set(value) != {
            "schema", "run_id", "mode", "actor", "authority_fingerprint", "decided_at"} or \
            value["schema"] != "taskplane.resource-policy/v1" or value["run_id"] != run_id or \
            value["mode"] != "advisory" or not str(value["actor"]).startswith("human:") or \
            not re.fullmatch(r"[a-f0-9]{64}", value["authority_fingerprint"]) or \
            type(value["decided_at"]) is not int or value["decided_at"] < 0 or \
            row["request_fingerprint"] != review_evidence.content_fingerprint(value):
        raise ValueError("run resource policy does not verify")
    return {**value, "fingerprint": row["request_fingerprint"]}


def advise_resource_limits(runtime: Any, ws: str, state: dict[str, Any], by: str) -> dict[str, Any]:
    """Explicit human policy, not a settings rewrite, retry or scope approval."""
    from taskplane import stage_migration, review_evidence
    if runtime.tp.task_slot() is not None:
        raise ValueError("resource policy is orchestrator-only")
    context = runtime._phase_bridge_context(ws, state)
    if context is None or not by or by != (state.get("_stage_native_root_authority") or {}).get("actor"):
        raise ValueError("resource policy requires the existing run's human --by")
    runtime._phase_bridge_authorize(ws, context, context["manifest"])
    prior = resource_policy(context["manifest"], context["run_id"])
    if prior is not None:
        return {"resource_policy": prior, "replay": True, "dispatch_allowed": False}
    value = {"schema": "taskplane.resource-policy/v1", "run_id": context["run_id"],
        "mode": "advisory", "actor": by, "decided_at": int(time.time()),
        "authority_fingerprint": context["stage"]["authority"]["authority_fingerprint"]}
    stage_migration.commit_phase_record(context["store"], context["run_id"],
        expected_revision=context["manifest"]["revision"], operation_id="run-resource-limits",
        operation="resource_policy", request_fingerprint=review_evidence.content_fingerprint(value), result=value,
        validate_authority=lambda current: runtime._phase_bridge_authorize(ws, context, current))
    return {"resource_policy": resource_policy(context["store"].load(context["run_id"]), context["run_id"]),
        "replay": False, "dispatch_allowed": False}


def reconcile(runtime: Any, ws: str, state: dict[str, Any], operation: str) -> dict[str, Any]:
    """Validate current candidates using genuine completion, never a replayed hook."""
    from taskplane import stage_migration, codex_identity
    if runtime.tp.task_slot() is not None:
        raise ValueError("phase reconciliation is orchestrator-only")
    context = runtime._phase_bridge_context(ws, state)
    if context is None or operation != operation_id(context):
        raise ValueError("reconcile must name the exact current phase operation")
    runtime._phase_bridge_authorize(ws, context, context["manifest"])
    row = stage_migration.phase_records(context["manifest"]).get(operation)
    if row is None or row["operation"] != "phase_prepare":
        raise ValueError("reconcile requires an existing prepared attempt")
    material = context["artifacts"].read(row["result"]["reference"])
    slot = material["contract_slot"]
    path = runtime.tp.active_contract_path(ws, slot)
    contract = runtime.tp.load_json(path, default=None, what="phase worker contract")
    if contract is None:
        contract = runtime.tp.released_worker_contract(ws, slot)
    attempt = runtime._phase_bridge_attempt(ws, contract)
    if attempt is None or attempt[0]["operation_id"] != operation:
        raise ValueError("phase contract is foreign")
    source, dispatch = attempt[4].nonce, attempt[5]
    source.validate(dispatch.issued, dispatch.nonce_bindings,
        enforce_deadline=not attempt[4].resource_limits_advisory)
    hooks = source.terminal_hooks(dispatch.issued, dispatch.nonce_bindings)
    if hooks is None:
        if context["stage"]["stage_kind"] not in {"product", "design", "plan"}:
            raise ValueError("provider completion reconciliation is pre-build only")
        start = source._read_phase_hook(dispatch.issued, dispatch.nonce_bindings, "start")
        terminal = codex_identity.completed_child(ws, start)
    else:
        start, terminal = hooks
    owner = (contract.get("worker_lifecycle") or {}).get("owner")
    if not isinstance(owner, dict) or any(owner != {key: observed["owner"][key]
            for key in ("session_id", "agent_id", "task_name")} for observed in (start, terminal)):
        raise ValueError("phase terminal owner differs from the bound worker")
    record_dispatch(runtime, ws, contract, material, start)
    result = runtime._collect_phase_attempt(ws, attempt, completed_worker=terminal if hooks is None else None)
    if result["status"] != "collected":
        return {**result, "dispatch_allowed": False}
    _reconcile_usage(runtime, ws, contract, material, terminal)
    runtime.collect_phase_runtime_telemetry(ws, contract)
    if hooks is None:
        # Current validation is not a historical Stop. Keep the slot intact;
        # the ordinary successful gate owns its retirement.
        return {**result, "worker_released":False, "dispatch_allowed":False,
            "validation":"current-semantic", "completion_source":"codex-task-complete"}
    lifecycle = contract["worker_lifecycle"]
    with runtime.tp.file_lock(path):
        current = runtime.tp.load_json(path, default=None, what="phase worker contract")
        if current is not None:
            terminal_receipt = (current.get("worker_lifecycle") or {}).get("terminal")
            if terminal_receipt is None:
                terminal_receipt = runtime.tp.record_worker_terminal(ws, slot, event=None,
                    outcome=terminal["outcome"], submission_status="phase-collected:" + terminal["claim"],
                    authority="phase-observation")
            runtime.tp.release_worker_contract(ws, slot, action=lifecycle["release_action"],
                terminal_receipt=terminal_receipt)
    return {**result, "worker_released": True, "dispatch_allowed": False}


def record_dispatch(runtime: Any, ws: str, contract: dict[str, Any], material: dict[str, Any],
                    start: dict[str, Any]) -> None:
    """Project an authenticated phase Start, even if the tool hook was absent.

    Called only with the nonce owner's freshly verified or persisted receipt.
    This records an existing host fact, never grants a launch or invents one.
    """
    lifecycle = contract["worker_lifecycle"]
    name = material["envelope"]["task_name"]
    if start["kind"] != "start" or start["owner"]["task_name"] != name or \
            lifecycle["dispatch_intent_id"] != material["bindings"]["attempt_id"] or \
            lifecycle["owner"] != {key: start["owner"][key]
                for key in ("session_id", "agent_id", "task_name")}:
        raise ValueError("phase Start does not match its prepared dispatch")
    runtime.record_native_dispatch_observation(ws,
        expected={"intent_id": material["bindings"]["attempt_id"],
            "intent_run_id": material["bindings"]["run_id"], "ref": lifecycle["task"],
            "kind": "step", "agent": material["envelope"]["role"], "task_name": name},
        native_task_name=name, observed_at=start["observed_at"])


def _reconcile_usage(runtime: Any, ws: str, contract: dict[str, Any], material: dict[str, Any],
                     terminal: dict[str, Any]) -> None:
    """Project the verified terminal and exact counter through existing owners."""
    from taskplane import codex_identity
    lifecycle = contract["worker_lifecycle"]
    task_id, dispatch_id = lifecycle["task"], lifecycle["dispatch_intent_id"]
    name = lifecycle["expected_task_name"]
    unavailable = False
    try:
        snapshot = codex_identity.terminal_usage(ws, terminal)
    except (ValueError, OSError):
        snapshot = None
    if snapshot is not None:
        recorded = runtime.record_native_session_snapshot(ws, task_id=task_id,
            dispatch_id=dispatch_id, snapshot=snapshot)
        usage = recorded["dispatch_usage"]
        runtime.record_observed_dispatch_usage(ws, task_id=task_id, dispatch_id=dispatch_id,
            native_task_name=name, source_fingerprint=snapshot["source"]["path_fingerprint"],
            normalized_usage={**usage, "schema": "taskplane.token-usage/v2",
                "available": True, "raw_total_tokens": usage["total_tokens"]})
    else:
        from taskplane import dispatch_telemetry
        ledger = runtime.load(ws).get("dispatch_telemetry")
        dispatch_telemetry.validate_ledger(ledger)
        binding = next(row for row in ledger["bindings"] if row["dispatch_id"] == dispatch_id)
        unavailable = binding.get("usage") is None
    runtime.finalize_observed_dispatch_usage(ws, task_id=task_id, dispatch_id=dispatch_id,
        native_task_name=name, outcome=terminal["outcome"], ended_at=terminal["observed_at"],
        usage_unavailable=unavailable, phase_runtime=True)


def release_expired(runtime: Any, ws: str, result: dict[str, Any]) -> None:
    """Retire only the exact unbound slot; this is not native completion."""
    from taskplane import review_evidence
    slot = result["contract_slot"]
    path = runtime.tp.active_contract_path(ws, slot)
    with runtime.tp.file_lock(path):
        contract = runtime.tp.load_json(path, default=None, what="expired phase contract")
        if contract is None:
            return  # Authenticated release already quarantined it.
        original = review_evidence.ArtifactStore(ws).read(result["contract_reference"])
        lifecycle = contract.get("worker_lifecycle") or {}
        submission = "human-authorized-phase-retry:" + result["previous_operation"]
        if lifecycle.get("status") == "terminal":
            terminal = lifecycle.get("terminal") or {}
            if terminal.get("authority") != "orphan-recovery" or terminal.get("submission_status") != submission:
                raise ValueError("expired worker has a different terminal result")
        else:
            if contract != original or lifecycle.get("status") != "pending" or lifecycle.get("owner") is not None:
                raise ValueError("expired worker changed; reconcile its actual effects before retry")
            terminal = runtime.tp.record_worker_terminal(ws, slot, event=None, outcome="interruption",
                submission_status=submission, authority="orphan-recovery")
        runtime.tp.release_worker_contract(ws, slot, action=lifecycle["release_action"], terminal_receipt=terminal)




def resolve_retry(runtime: Any, ws: str, state: dict[str, Any], *, operation: str, candidate: str,
                         by: str, worker_stopped: bool) -> dict[str, Any]:
    """One human-authorized successor attempt, retaining the failed evidence.

    Expired unbound workers require an explicit stop attestation. Active or
    effect-owning Build workers need reconciliation, not this recovery path.
    The journal grant precedes cleanup; a crash is replayable and next cannot
    dispatch while cleanup is incomplete. Routing still has its single owner.
    """
    from datetime import datetime
    from taskplane import stage_migration, review_evidence
    if runtime.tp.task_slot() is not None:
        raise ValueError("phase retry is orchestrator-only")
    root = state.get("_stage_native_root_authority") or {}
    if not by or by != root.get("actor") or not worker_stopped:
        raise ValueError("phase retry requires the run's human --by and --worker-stopped attestation")
    session = str(os.environ.get("TASKPLANE_SESSION_ID") or os.environ.get("CODEX_THREAD_ID") or
        os.environ.get("CLAUDE_SESSION_ID") or "").strip()
    if not session or len(session.encode()) > 256 or any(ord(char) < 32 for char in session):
        raise ValueError("phase retry requires an attributable current host session")
    if not re.fullmatch(r"[a-f0-9]{64}", candidate or ""):
        raise ValueError("phase retry requires the exact --candidate-fingerprint")
    context = runtime._phase_bridge_context(ws, state)
    if context is None or context["stage"]["stage_kind"] == "build":
        raise ValueError("phase retry requires a non-Build phase with no active effect owner")
    runtime._phase_bridge_authorize(ws, context, context["manifest"])
    request = {"run_id": context["run_id"], "stage_fingerprint": context["stage"]["fingerprint"],
        "previous_operation": operation, "candidate_fingerprint": candidate, "actor": by,
        "worker_stopped": True, "additional_attempts": 1}
    fingerprint = review_evidence.content_fingerprint(request)
    grant_id = operation + "-retry"
    rows = stage_migration.phase_records(context["manifest"])
    prior = rows.get(grant_id)
    if prior is not None:
        original_request = {key: prior["result"].get(key) for key in request}
        if prior["operation"] != "phase_retry" or any(original_request[key] != value
                for key, value in request.items() if key != "candidate_fingerprint"):
            raise ValueError("phase retry approval replay changed")
        config = context["configuration"]
        if candidate != config["candidate_fingerprint"]:
            if prior["result"]["next_operation"] in rows:
                raise ValueError("phase retry approval replay changed after preparation")
            # The existing routing owner may select a corrected candidate
            # before this ONE granted attempt is prepared. Never mint another
            # grant, nonce, deadline, or stage attempt for that selection.
            stage_migration.change_phase_routing(context["store"], context["run_id"],
                owner="agent-runtime", configuration=dict(config, candidate_fingerprint=candidate),
                expected_previous=context["route"]["result_fingerprint"],
                expected_revision=context["manifest"]["revision"],
                operation_id=grant_id + "-candidate-" + candidate[:32],
                validate_authority=lambda fresh: runtime._phase_bridge_authorize(ws, context, fresh))
        runtime._phase_retry_release(ws, prior["result"])
        return {"resolved": "retry", "run_id": context["run_id"], "phase_retry": prior, "replay": True}
    if operation != operation_id(context):
        raise ValueError("phase retry must name the exact current --phase-operation")
    prepared = rows.get(operation)
    if prepared is None or prepared["operation"] != "phase_prepare" or operation + "-complete" in rows:
        raise ValueError("phase retry requires an uncollected prepared attempt")
    material = context["artifacts"].read(prepared["result"]["reference"])
    if prepared["request_fingerprint"] != review_evidence.content_fingerprint(material):
        raise ValueError("phase preparation changed")
    deadline = datetime.fromisoformat(material["bindings"]["deadline"])
    if deadline.tzinfo is None or time.time() < deadline.timestamp():
        raise ValueError("phase retry requires an expired attempt")
    slot = material["contract_slot"]
    contract = runtime.tp.load_json(runtime.tp.active_contract_path(ws, slot), what="expired phase contract")
    lifecycle = contract.get("worker_lifecycle") or {}
    if lifecycle.get("status") != "pending" or lifecycle.get("owner") is not None or \
            (contract.get("phase_runtime") or {}).get("operation_id") != operation:
        raise ValueError("phase worker is not unbound; reconcile its actual terminal/effects first")
    # Preserve the exact pre-recovery object, not just its terminal projection.
    snapshot = context["artifacts"].put("phase-retry-contract", contract)
    config = dict(context["configuration"], candidate_fingerprint=candidate)
    route = stage_migration.change_phase_routing(context["store"], context["run_id"],
        owner="agent-runtime", configuration=config,
        expected_previous=context["route"]["result"]["previous"]
            if context["route"]["operation_id"] == grant_id + "-routing"
            else context["route"]["result_fingerprint"],
        expected_revision=context["manifest"]["revision"], operation_id=grant_id + "-routing",
        validate_authority=lambda fresh: runtime._phase_bridge_authorize(ws, context, fresh))
    result = {"schema": "taskplane.phase-retry/v1", **request,
        "next_operation": "phase-attempt-" + fingerprint[:32],
        "previous_preparation": prepared["result"]["reference"],
        "contract_slot": slot, "contract_reference": snapshot,
        "routing": route["result_fingerprint"], "old_result": "uncollected-not-passed",
        "approved_in_session": session}
    current = context["store"].load(context["run_id"])
    receipt = stage_migration.commit_phase_record(context["store"], context["run_id"],
        expected_revision=current["revision"], operation_id=grant_id, operation="phase_retry",
        request_fingerprint=fingerprint, result=result,
        validate_authority=lambda fresh: runtime._phase_bridge_authorize(ws, context, fresh))
    runtime._phase_retry_release(ws, result)
    return {"resolved": "retry", "run_id": context["run_id"], "phase_retry": receipt, "replay": False}


def pending(runtime: Any, ws: str, state: Mapping[str, object]) -> dict[str, Any] | None:
    # Even after rollback, a prepared current stage remains pinned to v2.
    context = runtime._stage_loop_context(ws, state)
    if context is None or not isinstance(context.get("stage"), dict):
        return None
    from taskplane import stage_migration
    rows = stage_migration.phase_records(context["manifest"])
    retries = retry_chain(context)
    if retries and os.path.exists(runtime.tp.active_contract_path(ws, retries[-1]["result"]["contract_slot"])):
        raise ValueError("phase retry cleanup incomplete; replay the exact authorized resolve command")
    row = rows.get(operation_id(context))
    if row is None:
        return None
    from taskplane import review_evidence
    material = review_evidence.ArtifactStore(ws).read(row["result"]["reference"])
    if not {"signing_scope", "freshness", "impact_reference"} <= set(material):
        raise ValueError("persisted phase attempt has no compatible runtime-signing admission; retain its original identity")
    if material["stage_fingerprint"] != context["stage"]["fingerprint"] or \
            row["request_fingerprint"] != review_evidence.content_fingerprint(material):
        raise ValueError("phase preparation changed")
    completed = rows.get(operation_id(context) + "-complete")
    if completed is None:
        from taskplane import design_host_transport
        nonce = design_host_transport.phase_nonce_source(runtime.tp, ws, context["run_id"], existing_only=True)
        issued = nonce.recover(material["nonce_bindings"])
        hooks = nonce.terminal_hooks(issued, material["nonce_bindings"])
        if hooks is not None:
            terminal = hooks[1]
            measured = usage_evidence(ws, material, terminal)
            limit = material["bindings"]["budget"]["tokens"]
            advisory = resource_policy(context["manifest"], context["run_id"]) is not None
            reason = ("terminal_not_successful" if terminal["outcome"] != "success" else
                      "phase_collection_requires_reconciliation" if advisory else
                      "phase_usage_unavailable" if measured["tokens"] is None else
                      "phase_token_budget_exhausted" if measured["tokens"] >= limit else
                      "phase_collection_requires_reconciliation")
            return {"step": state.get("step"), "paused": True,
                "error": "host terminal is recorded; " + reason,
                "phase_runtime": {"status": "recovery_required", "reason_code": reason,
                    "operation_id": operation_id(context), "reference": row["result"]["reference"],
                    "completion": None, "terminal_claim": terminal["claim"],
                    "usage": {**measured, "token_limit": limit, "advisory": advisory}},
                "awaiting": "reconcile the existing terminal; no new worker is authorized",
                "dispatch_allowed": False}
        from datetime import datetime
        deadline = datetime.fromisoformat(str(material["bindings"]["deadline"]).replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            raise ValueError("persisted phase deadline lacks a timezone")
        if time.time() >= deadline.timestamp() and resource_policy(context["manifest"], context["run_id"]) is None:
            # Waiting cannot make an expired attempt admissible. Preserve
            # its nonce, scope and evidence; do not silently launch again.
            return {"step": state.get("step"), "paused": True,
                "error": "phase attempt deadline exhausted; explicit recovery authority is required",
                "phase_runtime": {"status": "recovery_required",
                    "reason_code": "phase_deadline_exhausted",
                    "operation_id": operation_id(context),
                    "reference": row["result"]["reference"], "completion": None,
                    "deadline": material["bindings"]["deadline"]},
                "awaiting": "bounded recovery authorization for the existing run",
                "dispatch_allowed": False}
    return {"step": state.get("step"), "paused": True,
        "phase_runtime": {"status": "collected" if completed else "pending",
            "operation_id": operation_id(context), "reference": row["result"]["reference"],
            "completion": None if completed is None else completed["result"]},
        "awaiting": "current gate" if completed else "matching host terminal and output collection",
        "wait_policy": runtime.event_wait_policy(operation_id(context), 1)}
