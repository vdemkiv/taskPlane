"""Thin host and result adapters for repository-only phase pickup.

Portable authority remains in phase_handoff. Native contracts are disposable
current-attempt enforcement; they are never inputs to a successor phase.
"""
from __future__ import annotations

import copy
import json
import os
import re
from typing import Any, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from . import design_contract, loop, phase_admission, phase_build, phase_handoff, phase_pickup, phase_plan, phase_producer, phase_review_host, review_evidence, taskplane_lite as kernel
elif __package__:
    from . import design_contract, loop, phase_admission, phase_build, phase_handoff, phase_pickup, phase_plan, phase_producer, phase_review_host, review_evidence, taskplane_lite as kernel
else:
    import design_contract
    import loop
    import phase_admission
    import phase_build
    import phase_handoff
    import phase_pickup
    import phase_plan
    import phase_producer
    import phase_review_host
    import review_evidence
    import taskplane_lite as kernel


def worker_brief(handoff: dict[str, Any], startup: dict[str, Any]) -> dict[str, Any]:
    """Add host-neutral instructions without exposing the attempt lease."""
    phase = str(handoff["successor"]["phase"])
    build = phase == "build"
    worker = startup if build else startup["workers"][0]
    producer = worker["producer_contract"]
    attempt = str(producer["attempt_id"])
    role = {"design": "tp-designer", "plan": "tp-planner",
            "build": "tp-executor"}[phase]
    worker_id = str(worker["task"]["id"] if build else worker["worker_id"])
    dispatch = kernel.dispatch_fields(
        "step", role, worker_id, "deep" if phase == "design" else "standard",
        namespace=attempt)
    dispatch.pop("role_instructions", None)  # package-relative, digest-bound instead
    result: dict[str, Any] = {
        **dispatch,
        "schema": "taskplane.phase-host-dispatch/v1",
        "protocol": "repository-phase", "phase": phase,
        "role_reference": kernel.portable_role_reference(role),
        "fork_turns": "none", "inherited_turns": 0,
        "task_slot": dispatch["task_name"],
        "environment": {"TASKPLANE_TASK": dispatch["task_name"]},
        "producer_contract": copy.deepcopy(producer),
        "scoped_view": copy.deepcopy(worker["scoped_view"]),
        "full_envelope_reference": copy.deepcopy(worker["full_envelope_reference"]),
        "output_paths": list(producer["write_allow"]),
        "completion": {
            "worker_may_approve": False,
            "commit_before_submit": True,
            "command": ["phase", "submit", "--request", "<request.json>"],
            "submit_request": {
                "handoff": phase_handoff.handoff_path(handoff["handoff_id"]),
                **({"task_id": worker_id} if build else {
                    "result": worker["output"]}),
            },
        },
    }
    if build:
        result["task"] = copy.deepcopy(worker["task"])
        result["completion"]["proof_commands"] = list(worker["task"]["proofs"])
        result["completion"]["request_path"] = f".taskplane/phase-requests/{attempt}.json"
        result["completion"]["command"][-1] = result["completion"]["request_path"]
    else:
        result["result_schema"] = copy.deepcopy(worker["result_schema"])
        result["result_template"] = {
            "schema": "taskplane.phase-worker-result/v1", "phase": phase,
            "worker_id": worker_id, "attempt_id": attempt,
            "handoff_fingerprint": producer["handoff_fingerprint"],
            "subject_fingerprint": producer["subject_fingerprint"],
        }
        result["result_fingerprint"] = {
            "algorithm": "sha256", "encoding": "utf-8", "sort_keys": True,
            "separators": [",", ":"], "ensure_ascii": False,
            "allow_nan": False, "exclude": ["fingerprint"],
        }
        result["completion"]["seal_request"] = {
            "handoff": phase_handoff.handoff_path(handoff["handoff_id"]),
            "attempt_id": attempt, "status": "<observed done or interrupted>",
        }
        result["completion"]["sealing_rule"] = (
            "The orchestrator may seal observed worker status and committed output bytes; "
            "hashing does not invent review evidence or approve a successor.")
        if phase == "design":
            result["completion"]["design_content_fingerprint"] = (
                "You may omit lens_evidence[].content_fingerprint when hashing is unavailable. "
                "After observing your exact output, the engine derives only that content hash "
                "for validation. Supplied wrong hashes, missing judgment and missing reviews still refuse.")
        if phase == "plan":
            result["plan_output"] = loop._plan_output_contract({
                "requirement_id": handoff["requirement"]["id"],
                "design_fingerprint": handoff["design"]["fingerprint"],
            })
            result["plan_output"]["template"]["replan_history"] = []
            result["plan_output"]["history_rule"] = (
                "On resume preserve replan_history from the selected prior Plan exactly.")
    if len(kernel.canonical_json_bytes(result)) > kernel.MAX_STAGE_STARTUP_BYTES:
        raise ValueError("phase native brief exceeds its startup bound")
    return result


def seal_result(brief: dict[str, Any], *, status: str,
                evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Seal producer output; hashing is integrity, never approval or judgment."""
    value = {
        **brief["result_template"], "status": status,
        "artifact_fingerprint": evidence[0]["digest"],
        "evidence": copy.deepcopy(evidence),
    }
    value["fingerprint"] = phase_handoff.canonical_fingerprint(value)
    return validate_result(brief, value, evidence)


def validate_result(brief: dict[str, Any], result: object,
                    evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Bind the closed existing v1 result to exact current artifact bytes."""
    template = brief["result_template"]
    schema = review_evidence.phase_result_schema(phase=template["phase"])
    if (not isinstance(result, dict) or set(result) != set(schema["required"]) or
            any(result.get(key) != value for key, value in template.items()) or
            result.get("status") not in {"done", "interrupted"} or
            not evidence or result.get("evidence") != evidence or
            result.get("artifact_fingerprint") != evidence[0]["digest"] or
            result.get("fingerprint") != phase_handoff.manifest_fingerprint(result)):
        raise ValueError("phase worker result is incomplete, stale or foreign")
    return copy.deepcopy(result)


def _hydrated_brief(workspace: str, handoff: dict[str, Any],
                    startup: dict[str, Any]) -> dict[str, Any]:
    brief = worker_brief(handoff, startup)
    if brief["phase"] == "build":
        completion = phase_build.completion_brief(workspace, handoff, brief["task"])
        brief["native_task"] = completion["native_task"]
        brief["legacy_plan"] = completion["legacy_plan"]
        if "quality_admission" in completion:
            quality = completion["quality_admission"]
            quality["command"][-1] = brief["completion"]["request_path"]
            brief["completion"]["quality_admission"] = quality
    # Prior phase review receipts remain verified, selected repository evidence,
    # but are not the successor's authoring input. Keep them by reference instead
    # of rehydrating every prior review into each fresh worker's context.
    selected = [row for row in handoff["selected_artifacts"]
                if row["kind"] not in {"design-review", "plan-review"}]
    if sum(reference["bytes"] for reference in selected) > kernel.MAX_STAGE_STARTUP_BYTES:
        raise ValueError("phase selected artifacts exceed the native input bound")
    contents = []
    for reference in selected:
        phase_handoff.validate_repository_artifact_reference(workspace, reference)
        _, data = phase_handoff._safe_regular_file(
            workspace, reference["destination"], code="artifact-integrity")
        contents.append({"reference": copy.deepcopy(reference), "text": data.decode("utf-8")})
    brief["selected_artifact_content"] = contents
    if len(kernel.canonical_json_bytes(brief)) > kernel.MAX_STAGE_STARTUP_BYTES:
        raise ValueError("phase native input exceeds its startup bound")
    return brief


def _native_contract(workspace: str, brief: dict[str, Any], handoff: dict[str, Any]) -> dict[str, Any]:
    build = brief["phase"] == "build"
    scope = list(brief["output_paths"])
    if build and "quality_admission" in brief["completion"]:
        scope.append(brief["completion"]["quality_admission"]["path"])
    contract = kernel.build_contract(
        f"PHASE {brief['phase']}: {handoff['requirement']['id']}",
        scope=scope if build else None,
        read_only=not build, plan_minted=build,
        write_allow=None if build else brief["output_paths"],
        tools=["Read", "Grep", "Glob", "Write", "Edit", *(["Bash"] if build else [])])
    return contract if build else phase_producer.bind_output_submission(workspace, contract, brief)


def resolve_expected(workspace: str, expected: dict[str, Any], *,
                     native_task_name: str) -> dict[str, Any] | None:
    """Resolve hook dispatch through owner/review validation above admission.

    This coordinator owns protocol routing. The lower admission adapter owns
    intents and meters without importing either coordinator back.
    """
    matches = []
    for slot in kernel.list_task_slots(workspace):
        contract = kernel.load_json(kernel.active_contract_path(workspace, slot))
        lifecycle = (contract or {}).get("worker_lifecycle") or {}
        if str(lifecycle.get("stage") or "").startswith("phase-") and \
                lifecycle.get("expected_task_name") == native_task_name:
            matches.append(contract)
    if not matches and not str(expected.get("intent_run_id") or "").startswith("phase-"):
        return None
    if len(matches) != 1:
        raise ValueError("phase native intent has no unique pending contract")
    contract = matches[0]
    lifecycle = contract["worker_lifecycle"]
    if lifecycle["status"] != "pending" or expected.get("task_name") != native_task_name:
        raise ValueError("phase native intent is not the exact pending worker")
    startup, brief = contract["phase_startup"], contract["phase_dispatch"]
    if brief.get("task_name") != native_task_name or contract.get("task_id") != brief.get("task_slot"):
        raise ValueError("phase native slot differs from its emitted brief")
    review = brief.get("protocol") == "repository-phase-review"
    if brief.get("protocol") not in {"repository-phase", "repository-phase-review"} or \
            lifecycle.get("stage") != "phase-" + brief["phase"] + ("-review" if review else ""):
        raise ValueError("phase native worker protocol differs from its lifecycle")
    handoff_id = startup["full_envelope_reference"]["handoff_id"]
    handoff = cast(dict[str, Any], phase_handoff.load_manifest(
        workspace, phase_handoff.handoff_path(handoff_id), require_clean=True,
        allow_phase_output=review))
    if brief["phase"] == "build":
        phase_pickup.validate_build_assignment(startup, handoff, checkout=workspace)
    else:
        kernel.validate_stateless_phase_startup(startup, handoff)
    if review:
        canonical = phase_review_host.validate_dispatch(workspace, handoff, contract)
    else:
        canonical = _hydrated_brief(workspace, handoff, startup)
        policy = _native_contract(workspace, canonical, handoff)
        if any(contract.get(key) != policy.get(key) for key in (
                "coding", "read_only", "write_allow", "allowed_tools", "budget", "submission_contract")):
            raise ValueError("phase native worker contract is stale or widened")
    if brief != canonical:
        raise ValueError("phase native worker brief is stale or widened")
    if contract.get("phase_handoff_fingerprint") != handoff["fingerprint"] or \
            expected.get("agent") != brief["role"] or expected.get("ref") != handoff_id or \
            lifecycle.get("dispatch_intent_id") != expected.get("intent_id") or \
            lifecycle.get("dispatch_intent_run_id") != expected.get("intent_run_id"):
        raise ValueError("phase native intent authority is foreign")
    intent = contract.get("phase_dispatch_intent")
    if not isinstance(intent, dict):
        raise ValueError("phase native intent is missing from its contract")
    rebuilt = phase_admission.create_intent(workspace, handoff, brief, wait_policy=intent["wait_policy"])
    if intent != rebuilt or expected.get("intent_id") != intent["intent_id"] or \
            expected.get("intent_run_id") != intent["identity"]["run_id"]:
        raise ValueError("phase native intent is stale or tampered")
    return {"contract": contract, "handoff": handoff, "startup": startup, "brief": brief}


def _dispatch_state(workspace: str, handoff: dict[str, Any], startup: dict[str, Any],
                    brief: dict[str, Any], contract: dict[str, Any], *,
                    observation_authority: bytes | None) -> dict[str, Any]:
    activation = contract["worker_lifecycle"]["status"]
    if activation != "pending":
        return {**brief, "activation": activation, "dispatch_allowed": False,
                "instruction": "The current phase worker is already running; wait for its result."}
    admission: dict[str, Any] = {"dispatch_allowed": True, "status": "ready"}
    if brief["phase"] == "build":
        admission = phase_admission.screen_root_dispatch(
            workspace, handoff, startup, brief, reference=contract["phase_admission_reference"],
            observation_authority=observation_authority or b"")
    return {**brief, "activation": activation,
            "dispatch_intent": copy.deepcopy(contract["phase_dispatch_intent"]),
            "dispatch_allowed": admission.get("dispatch_allowed", admission.get("allowed", False)),
            "admission": admission}


def bind_native_worker(workspace: str, handoff: dict[str, Any],
                       startup: dict[str, Any], *,
                       observation_authority: bytes | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prepare the existing native lifecycle from freshly validated input."""
    phase = str(handoff["successor"]["phase"])
    if phase == "plan":
        phase_plan.selected_inputs(workspace, handoff)
    existing = kernel.worker_contract_for_stage(
        workspace, stage="phase-" + phase, task=handoff["handoff_id"])
    if existing is not None:
        contract = existing["contract"]
        saved = contract.get("phase_startup")
        if contract.get("phase_handoff_fingerprint") != handoff["fingerprint"] or \
                not isinstance(saved, dict):
            raise ValueError("current phase worker has a foreign handoff")
        # Revalidate the private current-attempt cache just as strictly as
        # a newly prepared startup. It is not portable phase authority.
        if phase == "build":
            phase_pickup.validate_build_assignment(saved, handoff, checkout=workspace)
        else:
            kernel.validate_stateless_phase_startup(saved, handoff)
        # Replay only the CURRENT attempt, never a predecessor phase/runtime.
        expected = _hydrated_brief(workspace, handoff, saved)
        cached = contract.get("phase_dispatch") or {}
        wanted_contract = _native_contract(workspace, expected, handoff)
        if cached != expected or any(contract.get(key) != wanted_contract.get(key)
                for key in ("coding", "read_only", "write_allow", "allowed_tools", "budget", "submission_contract")):
            raise ValueError("current phase worker brief is stale")
        intent = phase_admission.create_intent(workspace, handoff, expected,
            wait_policy=loop.event_wait_policy("phase:" + saved["attempt_id"], 1))
        if contract.get("phase_dispatch_intent") != intent:
            raise ValueError("current phase native intent is stale")
        if phase == "build":
            request_path = os.path.join(workspace, expected["completion"]["request_path"])
            if os.path.realpath(request_path) != request_path or kernel.load_json(
                    request_path, default=None) != expected["completion"]["submit_request"]:
                raise ValueError("current Build submission request is missing or altered")
        return copy.deepcopy(saved), _dispatch_state(
            workspace, handoff, saved, cached, contract, observation_authority=observation_authority)
    brief = _hydrated_brief(workspace, handoff, startup)
    contract = _native_contract(workspace, brief, handoff)
    contract["task_id"] = brief["task_slot"]
    contract["phase_handoff_fingerprint"] = handoff["fingerprint"]
    contract["phase_dispatch"] = copy.deepcopy(brief)
    contract["phase_startup"] = copy.deepcopy(startup)
    wait_policy = loop.event_wait_policy("phase:" + startup["attempt_id"], 1)
    if phase == "build":
        preparation = phase_admission.prepare(workspace, handoff, startup, brief, wait_policy=wait_policy)
        contract["phase_admission_reference"] = preparation["reference"]
        intent = preparation["intent"]
    else:
        intent = phase_admission.create_intent(workspace, handoff, brief, wait_policy=wait_policy)
    contract["phase_dispatch_intent"] = intent
    contract = kernel.prepare_worker_contract(
        workspace, contract, stage="phase-" + brief["phase"],
        task=handoff["handoff_id"], task_name=brief["task_name"],
        role_marker=brief["role_marker"])
    contract["worker_lifecycle"]["dispatch_intent_id"] = intent["intent_id"]
    contract["worker_lifecycle"]["dispatch_intent_run_id"] = intent["identity"]["run_id"]
    if brief["phase"] == "build":
        request_path = os.path.join(workspace, brief["completion"]["request_path"])
        if os.path.realpath(request_path) != request_path:
            raise ValueError("phase submission request destination is unsafe")
        kernel.atomic_write_json(request_path, brief["completion"]["submit_request"])
    kernel.activate(workspace, contract, task_slot_override=brief["task_slot"])
    kernel.record_expected_dispatch(
        workspace, "step", brief["role"], brief["model_tier"], brief["model"],
        ref=handoff["handoff_id"], task_name=brief["task_name"],
        reasoning_effort=brief["reasoning_effort"],
        role_marker_value=brief["role_marker"], dispatch_route=brief.get("dispatch_route"),
        intent_id=intent["intent_id"], intent_run_id=intent["identity"]["run_id"])
    return startup, _dispatch_state(
        workspace, handoff, startup, brief, contract, observation_authority=observation_authority)


def output_references(workspace: str, phase: str, *,
                      publish: bool = False) -> list[dict[str, Any]]:
    return phase_handoff.phase_output_references(workspace, phase, publish=publish)


def completed_worker_brief(workspace: str, handoff: dict[str, Any], *,
                           attempt_id: str, status: str,
                           references: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Bind collection to this phase's observed producer, not its own claim.

    Only current-attempt native evidence is inspected. The successor receives
    repository artifacts/authority, never this disposable execution cache.
    """
    if status not in {"done", "interrupted"}:
        raise phase_pickup.PhasePickupError("authoring-invalid", "phase worker status is invalid")
    startup = kernel.create_stateless_phase_startup(handoff, attempt_id=attempt_id)
    expected = worker_brief(handoff, startup)
    slot = expected["task_slot"]
    receipt = kernel.load_json(kernel._worker_terminal_path(workspace, slot), default=None)
    receipt_id = str((receipt or {}).get("receipt_id") or "")
    if not re.fullmatch(r"worker-terminal-[a-f0-9]{24}", receipt_id):
        raise phase_pickup.PhasePickupError(
            "authoring-invalid", "phase output has no observed current worker completion")
    contract = kernel.load_json(os.path.join(
        kernel.tp_dir(workspace), "quarantine", "contracts",
        f"{slot}-{receipt_id.split('-')[-1]}.json"), default=None)
    lifecycle = (contract or {}).get("worker_lifecycle") or {}
    if (lifecycle.get("status") != "released" or
            lifecycle.get("stage") != "phase-" + expected["phase"] or
            lifecycle.get("task") != handoff["handoff_id"] or
            lifecycle.get("expected_task_name") != expected["task_name"] or
            receipt.get("authority") != "host-lifecycle" or not receipt.get("owner") or
            (status == "done" and receipt.get("outcome") != "success")):
        raise phase_pickup.PhasePickupError(
            "authoring-invalid", "phase output does not match a completed native producer")
    action = lifecycle.get("release_action")
    if not isinstance(action, dict):
        raise phase_pickup.PhasePickupError("authoring-invalid", "phase worker release action is missing")
    kernel._verify_worker_release_action(workspace, slot, action, contract)
    kernel._verify_worker_terminal_receipt(workspace, slot, receipt, contract, action)
    saved = contract.get("phase_startup")
    kernel.validate_stateless_phase_startup(saved, handoff)
    if saved["attempt_id"] != attempt_id:
        raise phase_pickup.PhasePickupError("authoring-invalid", "phase result attempt is foreign")
    brief = _hydrated_brief(workspace, handoff, saved)
    wanted = _native_contract(workspace, brief, handoff)
    if contract.get("phase_dispatch") != brief or any(
            contract.get(key) != wanted.get(key) for key in
            ("coding", "read_only", "write_allow", "allowed_tools", "budget", "submission_contract")):
        raise phase_pickup.PhasePickupError(
            "authoring-invalid", "observed phase producer contract or inputs are stale")
    try:
        phase_producer.verify_output_observation(
            receipt, brief, references if references is not None else
            output_references(workspace, str(brief["phase"])))
    except ValueError as exc:
        raise phase_pickup.PhasePickupError("authoring-invalid", str(exc)) from exc
    return brief


def _review_arguments(workspace: str, handoff: dict[str, Any], collected: dict[str, Any]) -> tuple[Any, ...]:
    phase = collected["phase"]
    paths = {phase: phase_handoff.phase_output_paths(phase)[0],
             phase + "-narrative": phase_handoff.phase_output_paths(phase)[1],
             "design-visual": "design/visual.html"}
    contents = {row["kind"]: phase_handoff._safe_regular_file(
        workspace, paths[row["kind"]], code="artifact-integrity")[1]
        for row in collected["references"]}
    return (workspace, handoff, collected["producer_brief"], collected["artifact"],
            collected["references"], contents)


def prepare_reviews(workspace: str, handoff: dict[str, Any], result: object) -> dict[str, Any] | None:
    """Prepare independent judgment only after the owner's exact substantive output."""
    collected = collect_output(workspace, handoff, result, require_reviews=False)
    if collected["result"]["status"] != "done":
        return None
    return phase_review_host.prepare(*_review_arguments(workspace, handoff, collected))


def collect_output(workspace: str, handoff: dict[str, Any],
                   result: object, *, require_reviews: bool = True) -> dict[str, Any]:
    """Collect committed Design/Plan output without consulting old runtime."""
    phase = handoff["successor"]["phase"]
    if not isinstance(result, dict) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", str(result.get("attempt_id") or "")):
        raise ValueError("phase result attempt is missing")
    references = output_references(workspace, phase)
    brief = completed_worker_brief(
        workspace, handoff, attempt_id=result["attempt_id"], status=str(result.get("status") or ""),
        references=references)
    checked = validate_result(brief, result, references)
    primary = phase_handoff.phase_output_paths(phase)[0]
    _, data = phase_handoff._safe_regular_file(workspace, primary, code="artifact-integrity")
    artifact = json.loads(data.decode("utf-8"))
    if not isinstance(artifact, dict):
        raise ValueError("phase artifact must be a JSON object")
    tasks = copy.deepcopy(handoff["tasks"])
    if phase == "plan":
        phase_plan.validate_identity(handoff, artifact)
        if checked["status"] == "done":
            tasks = phase_plan.validate_output(workspace, handoff, artifact)
    else:
        if artifact.get("schema") != "taskplane.design/v1" or artifact.get(
                "requirement") != handoff["requirement"]["id"]:
            raise ValueError("Design artifact schema or requirement is foreign")
        if checked["status"] == "done":
            graph_refs = [row for row in handoff["selected_artifacts"] if row["kind"] == "graph"]
            if len(graph_refs) != 1:
                raise ValueError("completed Design requires one sealed baseline graph input")
            _, graph_bytes = phase_handoff._safe_regular_file(
                workspace, graph_refs[0]["destination"], code="artifact-integrity")
            graph = json.loads(graph_bytes.decode("utf-8"))
            if not isinstance(graph, dict):
                raise ValueError("sealed Design baseline graph must be an object")
            # The repository validator already proves every non-Design path
            # unchanged from the sealed source. No predecessor graph store is read.
            # Read-only hosts need not grant a shell just to calculate a hash.
            # Derive absent hash metadata in memory after native byte validation;
            # never alter the artifact, repair a supplied wrong hash, or create
            # a judgment/producer/independence claim for the designer.
            validation_artifact = copy.deepcopy(artifact)
            for row in validation_artifact.get("lens_evidence") or []:
                if isinstance(row, dict) and "content_fingerprint" not in row:
                    row["content_fingerprint"] = design_contract.design_content_fingerprint(workspace, artifact)
            errors = design_contract.design_artifact_errors(
                workspace, validation_artifact, requirement={
                    "id": handoff["requirement"]["id"],
                    "acceptance": [row["criterion"] for row in handoff["acceptance"]],
                    "contracts": handoff["contracts"],
                }, baseline_graph=graph, current_graph=graph)
            if errors:
                raise phase_pickup.PhasePickupError(
                    "authoring-invalid", "Design completion refused: " + "; ".join(errors))
    collected = {"result": checked, "references": references, "artifact": artifact, "tasks": tasks,
                 "phase": phase, "subject_fingerprint": references[0]["digest"], "producer_brief": brief}
    if checked["status"] == "done" and require_reviews:
        try:
            review = phase_review_host.collect(*_review_arguments(workspace, handoff, collected))
        except ValueError as exc:
            raise phase_pickup.PhasePickupError("authoring-invalid", str(exc)) from exc
        if review["status"] != "pass":
            raise phase_pickup.PhasePickupError(
                "authoring-invalid", "focused phase review requires changes; no successor is approved")
        collected["review"] = review
    return collected


def export_output(workspace: str, handoff: dict[str, Any], result: object,
                  decision: dict[str, Any]) -> dict[str, Any]:
    """Connect collected output and explicit human authority to the exporter."""
    if TYPE_CHECKING:
        from . import loop
    elif __package__:
        from . import loop
    else:
        import loop
    collected = collect_output(workspace, handoff, result)
    phase = collected["phase"]
    subject = collected["subject_fingerprint"]
    output_refs = list(collected["references"])
    if "review" in collected:
        review_path = phase + "/review.json"
        _, review_bytes = phase_handoff._safe_regular_file(workspace, review_path, code="artifact-integrity")
        if json.loads(review_bytes.decode("utf-8")) != collected["review"]:
            raise ValueError("committed phase review differs from collected judgments")
        output_refs.append(phase_handoff.create_repository_artifact_reference(
            workspace, review_path, kind=phase + "-review", publish=False))
    if decision.get("gate") != phase + "-approval" or decision.get(
            "subject_fingerprint") != subject:
        raise ValueError("phase approval does not name the collected artifact")
    source = {"commit": phase_handoff._git(workspace, "rev-parse", "HEAD"),
              "tree": phase_handoff._git(workspace, "rev-parse", "HEAD^{tree}")}
    # A resumed partial artifact needs new authority if its bytes changed.
    # The previous chain and artifact remain immutable in the old handoff.
    gates_before = {"initial-authorization"}
    if phase == "plan":
        gates_before.add("design-approval")
    authorities = [copy.deepcopy(row) for row in handoff["authority_receipts"]
                   if row["gate"] in gates_before]
    authority = design_contract.create_phase_human_gate_receipt(
        decision, repository_id=handoff["repository"]["id"],
        source_commit=source["commit"], source_tree=source["tree"],
        predecessor_authority_fingerprint=authorities[-1]["fingerprint"])
    material = {key: copy.deepcopy(handoff[key]) for key in (
        "repository", "requirement", "design", "plan", "obligations",
        "tasks", "contracts", "acceptance", "progress_receipts", "exclusions")}
    material.update({
        "source": source,
        phase: {"fingerprint": subject, "artifact": collected["references"][0]},
        "authority_receipts": [*authorities, authority],
        "selected_artifacts": sorted([
            *(row for row in handoff["selected_artifacts"]
              if row["kind"] not in {phase, phase + "-narrative", phase + "-visual", phase + "-review"}),
            *output_refs], key=lambda row: (row["kind"], row["digest"])),
        "lineage": {"predecessor_handoff_fingerprint": handoff["fingerprint"],
                    "predecessor_receipt_head": handoff["lineage"][
                        "predecessor_receipt_head"]},
    })
    if phase == "plan":
        material["tasks"] = collected["tasks"]
    outcome = collected["result"]["status"]
    progress = {"phase": phase, "state": "terminal" if outcome == "done" else "active",
                "outcome": outcome}
    # Validate topology, authority and lineage before creating export bytes.
    loop.project_phase_export(material, phase=phase, outcome=outcome,
                              durable_progress=progress)
    output_references(workspace, phase, publish=True)
    if "review" in collected:
        phase_handoff.create_repository_artifact_reference(
            workspace, phase + "/review.json", kind=phase + "-review")
    return loop.publish_phase_export(workspace, material, phase=phase,
                                     outcome=outcome, durable_progress=progress)
