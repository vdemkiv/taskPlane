"""Dispatch implementation; the caller supplies the composition root ports."""
from __future__ import annotations
from collections.abc import Mapping, Iterable
import hashlib
import json
import re
if __package__:
    from .primitives import canonical_bytes as canonical_json_bytes
else:
    from primitives import canonical_bytes as canonical_json_bytes
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import terminal_truth

if __package__:
    from .stage_handoff import (
        STAGE_DISPATCH_SCHEMA,
        STAGE_STARTUP_SCHEMA,
        STAGE_RECEIPT_SCHEMA,
        STAGE_AUTHORITY_REFERENCE_SCHEMA,
        STAGE_HANDOFF_DISPATCH_SCHEMA,
        STAGE_HANDOFF_V2_DISPATCH_SCHEMA,
        MAX_STAGE_STARTUP_BYTES,
        MAX_STAGE_RECEIPT_BYTES,
        _STAGE_ID_RE,
        _STAGE_FINGERPRINT_RE,
        _STAGE_RECEIPT_FIELDS,
        _STAGE_HANDOFF_FIELDS,
        _STAGE_DISPATCH_RECEIPTS,
        _STAGE_RUNTIME_FORBIDDEN_KEYS,
        StageDispatchError,
        _stage_modules,
        _json_detach,
        _stage_identifier,
        _stage_fingerprint,
        _reject_runtime_context,
        verify_stage_receipt,
        _expected_dispatch_head,
        _verify_dispatch_result,
        _verified_handoff_for_dispatch,
        _dispatch_claim,
        _declared_stage_scope,
        _stage_authority_reference,
        _verify_stage_authority_reference,
        _dispatch_handoff_projection,
        _verify_dispatch_handoff_projection,
        stage_runtime_dispatch,
        stage_dispatch_payload,
        stage_startup_bytes,
        attach_phase_input,
    )
else:
    from stage_handoff import (
        STAGE_DISPATCH_SCHEMA,
        STAGE_STARTUP_SCHEMA,
        STAGE_RECEIPT_SCHEMA,
        STAGE_AUTHORITY_REFERENCE_SCHEMA,
        STAGE_HANDOFF_DISPATCH_SCHEMA,
        STAGE_HANDOFF_V2_DISPATCH_SCHEMA,
        MAX_STAGE_STARTUP_BYTES,
        MAX_STAGE_RECEIPT_BYTES,
        _STAGE_ID_RE,
        _STAGE_FINGERPRINT_RE,
        _STAGE_RECEIPT_FIELDS,
        _STAGE_HANDOFF_FIELDS,
        _STAGE_DISPATCH_RECEIPTS,
        _STAGE_RUNTIME_FORBIDDEN_KEYS,
        StageDispatchError,
        _stage_modules,
        _json_detach,
        _stage_identifier,
        _stage_fingerprint,
        _reject_runtime_context,
        verify_stage_receipt,
        _expected_dispatch_head,
        _verify_dispatch_result,
        _verified_handoff_for_dispatch,
        _dispatch_claim,
        _declared_stage_scope,
        _stage_authority_reference,
        _verify_stage_authority_reference,
        _dispatch_handoff_projection,
        _verify_dispatch_handoff_projection,
        stage_runtime_dispatch,
        stage_dispatch_payload,
        stage_startup_bytes,
        attach_phase_input,
    )

def project_next_action_for_host(_ports, ws: str, action: Mapping[str, object], *,
                                 wave_usage: Mapping[str, object] | None = None) -> dict:
    """One bounded public shape for launch, waiting, refusal and human gates."""
    if not isinstance(action, Mapping):
        raise ValueError("loop next action must be a mapping")
    if action.get("schema") == STAGE_DISPATCH_SCHEMA:
        if set(action) != {"schema", "stage_runtime_dispatch", "obligations"}:
            raise ValueError("phase dispatch contains undeclared context")
        if action["stage_runtime_dispatch"] is not None:
            stage_startup_bytes(action["stage_runtime_dispatch"])
        return _ports._copy_json(action)
    fields = {"step", "error", "code", "awaiting", "paused", "read_only", "wait_policy"}
    obligations = {key: action[key] for key in fields if key in action}
    obligations["dispatch_allowed"] = False
    phase = action.get("phase_runtime")
    if isinstance(phase, Mapping):
        obligations.update(phase_operation=phase["operation_id"], status=phase["status"])
        if phase.get("reason_code"):
            obligations["reason_code"] = phase["reason_code"]
    if action.get("schema") == "taskplane.stage-wave/v1":
        obligations.update(wave=action["wave"], held=action["held"],
                           wait_invocation=action["wait_invocation"])
    return {"schema": STAGE_DISPATCH_SCHEMA, "stage_runtime_dispatch": None,
            "obligations": _ports._copy_json(obligations)}


def build_dispatch_lens_routing(_ports,
        state: Mapping[str, object], task: Mapping[str, object] | None,
        *, workspace: str) -> tuple[dict, dict | None]:
    """Require the approved zero-lens Build mode."""
    receipt = _ports._validated_delivery_mode(state)
    if receipt is None:
        raise _ports.delivery_policy.DeliveryPolicyError("Build requires its approved delivery-mode receipt")
    if receipt["mode"] != "build":
        raise _ports.delivery_policy.DeliveryPolicyError(
            "execute dispatch requires build delivery mode")
    authorization = _ports.build_c.authorize_delivery_dispatch(
        receipt, lens_worker_factory=lambda lens: lens)
    return ({
        "lenses": [],
        "context": {
            "delivery_mode": "build",
            "delivery_mode_receipt": receipt["fingerprint"],
            "automatic_lens_worker_count": 0,
        },
    }, authorization)


def _focused_stage_route(_ports,
        ws: str, *, stage: str, target: str, evidence: Mapping[str, object],
        mandatory_lenses: Iterable[str] | None = None,
        maximum_lenses: int | None = None,
        expanded_route_provider_client:
        terminal_truth.ExpandedRouteProviderClient | None = None,
        expanded_route_provider_receipt:
        terminal_truth.ExpandedRouteProviderReceipt | None = None,
        ) -> tuple[
            dict[str, object], dict[str, object], dict[str, object] | None]:
    """Build one pre-Build focused quick route from stage-owned evidence.

    Product and Design keep only stage-relevant positive signals. Plan uses
    three mandatory delivery floors and at most one highest incumbent risk in
    its normal path. An explicit mandatory set is exact: two refuses, three
    or four dispatches, and five or more produces a closed provider request
    with zero workers.
    """
    if stage not in {"product", "design", "plan"}:
        raise _ports.lens_route_policy.LensRoutePolicyError(
            "focused adapter requires a routed stage: product, design, or plan")
    if not isinstance(evidence, _ports.Mapping):
        raise _ports.lens_route_policy.LensRoutePolicyError(
            "focused stage evidence must be an object")
    material = _ports.json.loads(_ports.json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False))
    files = sorted({str(path) for path in material.get("files") or []
                    if str(path)})
    evidence_text = " " + _ports.json.dumps(
        material, sort_keys=True, ensure_ascii=False).lower() + " "
    incumbent_stage = "design" if stage == "design" else "build"
    incumbent = _ports.lens_router.route(
        files, task_type=("solution-design" if stage == "design" else stage),
        breadth="routed", stage=incumbent_stage, workspace=ws,
        requirement_text=evidence_text[:128 * 1024])
    if (incumbent.get("context") or {}).get("status") == \
            "mapper_unavailable":
        raise _ports.lens_route_policy.LensRoutePolicyError(
            "incumbent applicability mapper is unavailable")

    catalog = _ports.lens_router.load_catalog()
    definitions = list(catalog.get("lenses") or [])
    known = {str(row.get("id") or "") for row in definitions}
    explicit = mandatory_lenses is not None
    if mandatory_lenses is None:
        effective = _ports.operational_settings.load_settings(
            environment=_ports.os.environ)
        policy = effective.lenses.policy_for(
            stage, catalog_ids=known)
        mandatory_lenses = policy.mandatory
        if maximum_lenses is None:
            maximum_lenses = policy.max_count
    mandatory = tuple(str(lens_id) for lens_id in mandatory_lenses)
    if not mandatory or len(set(mandatory)) != len(mandatory) or \
            not set(mandatory) <= known:
        raise _ports.lens_route_policy.LensRoutePolicyError(
            "mandatory focused lenses must be unique catalog ids")
    if maximum_lenses is not None and (
            isinstance(maximum_lenses, bool) or
            not isinstance(maximum_lenses, int) or
            maximum_lenses < len(mandatory)):
        raise _ports.lens_route_policy.LensRoutePolicyError(
            "focused lens maximum must cover every mandatory lens")
    allowed = (known if stage == "design" else _ports._FOCUSED_STAGE_LENSES[stage])
    positive = set(mandatory)
    keyword_hits = {
        lens_id for lens_id, words in _ports._FOCUSED_STAGE_KEYWORDS.items()
        if lens_id in allowed and
        any(word in evidence_text for word in words)
    }
    mapped = {str(row.get("id") or ""): row
              for row in incumbent.get("lenses") or []
              if isinstance(row, dict)}
    incumbent_hits = {
        lens_id for lens_id, row in mapped.items()
        if lens_id in allowed and
        str(row.get("verdict") or row.get("tier") or "n/a") != "n/a"
    }
    component_hits = {
        str(lens_id) for lens_id in
        material.get("component_lens_candidates") or []
        if str(lens_id) in allowed
    }
    if not explicit or maximum_lenses is not None:
        # A decomposed component may strengthen an independently detected
        # risk; it may not admit a lens by itself. Broad/core components often
        # map to most of the catalog and otherwise turn ``max`` into a fill
        # target instead of a safety cap.
        candidates = (keyword_hits | incumbent_hits) - positive
        if stage == "plan":
            # The normal Plan path is bounded before policy dispatch. The
            # deterministic incumbent score chooses one fourth risk; callers
            # with five independent mandatory risks must declare them exactly
            # and receive the protected overflow path below.
            ranked = sorted(candidates, key=lambda lens_id: (
                -float((mapped.get(lens_id) or {}).get("score") or 0), lens_id))
            positive.update(ranked[:1])
        else:
            ranked = sorted(candidates, key=lambda lens_id: (
                -int(lens_id in component_hits),
                -int(lens_id in keyword_hits),
                -float((mapped.get(lens_id) or {}).get("score") or 0),
                lens_id))
            capacity = (len(ranked) if maximum_lenses is None else
                        max(0, maximum_lenses - len(positive)))
            positive.update(ranked[:capacity])

    catalog_fp = _ports.lens_route_policy.catalog_fingerprint(definitions)
    evidence_fp = _ports.lens_route_policy.fingerprint(material)
    context = {
        "schema": _ports.lens_route_policy.CONTEXT_SCHEMA,
        "stage": stage,
        "target": str(target),
        "policy_version": _ports.lens_route_policy.POLICY_VERSION,
        "catalog_fingerprint": catalog_fp,
        "execution_mode": "quick-only",
        "stage_input_fingerprint": evidence_fp,
        "mandatory_lenses": list(mandatory),
        **({"maximum_lenses": maximum_lenses}
           if maximum_lenses is not None else {}),
    }
    rows = []
    for index, definition in enumerate(definitions):
        lens_id = str(definition.get("id") or "")
        source = mapped.get(lens_id) or {}
        hit = lens_id in positive
        source_evidence = [str(value) for value in
                           source.get("evidence") or source.get("reasons") or []
                           if str(value)]
        reasons = []
        if lens_id in mandatory:
            reasons.append(f"{stage} mandatory focused risk: {lens_id}")
        if lens_id in keyword_hits:
            reasons.append(f"{stage} evidence matched focused risk: {lens_id}")
        if lens_id in incumbent_hits:
            reasons.extend(source_evidence[:3])
        if lens_id in component_hits:
            reasons.append(
                f"{stage} decomposition selected component risk: {lens_id}")
        score = float(source.get("score") or 0)
        if lens_id in mandatory:
            score = max(score, 0.95 - index / 10000)
        elif lens_id in keyword_hits:
            score = max(score, 0.75 - index / 10000)
        rows.append({
            "id": lens_id,
            "verdict": "light" if hit else "n/a",
            "score": min(1.0, max(0.0, score)),
            "evidence": reasons or ([f"{stage} applicable signal: {lens_id}"]
                                     if hit else []),
            "negative_evidence": ([] if hit else
                                  [f"no applicable {stage} signal for {lens_id}"]),
            # Each admitted pre-Build concern is independently evidenced;
            # minimum sufficiency happens in the adapter's positive set.
            "risk_group": f"{stage}:{lens_id}",
            "mandatory": lens_id in mandatory,
            "fingerprint_inputs": {
                "stage_input": evidence_fp,
                "lens": lens_id,
                "source_signal": _ports.lens_route_policy.fingerprint({
                    "verdict": source.get("verdict") or source.get("tier"),
                    "score": source.get("score"),
                    "evidence": source_evidence[:3],
                }),
            },
        })
    focused = _ports.lens_route_policy.build_route(context, rows, definitions)
    try:
        _, _, review_module = _ports._review_runtime_modules()
    except RuntimeError as exc:
        raise _ports.lens_route_policy.LensRoutePolicyError(
            "checkout ReviewKernel runtime is unavailable") from exc
    try:
        focused, request = review_module.apply_expanded_route_authority(
            ws, focused, catalog,
            provider_client=expanded_route_provider_client,
            provider_receipt=expanded_route_provider_receipt)
    except review_module.ReviewKernelError as exc:
        raise _ports.lens_route_policy.LensRoutePolicyError(str(exc)) from exc
    projected = review_module.project_focused_route(
        incumbent, focused, catalog)
    projected.setdefault("context", {})["task_to_ac_coverage"] = \
        _ports._copy_json(material.get("task_to_ac_coverage") or {})
    return focused, projected, request


def _focused_plan_inputs(_ports, ws: str, state: Mapping[str, object]) -> tuple[dict, dict]:
    """Read only the immutable inputs selected by the current Plan stage."""
    from taskplane import review_evidence
    paths = {"design/contract.json":"design", "plan/tasks.json":"plan"}
    admitted = {}
    context = _ports._stage_loop_context(ws, state)
    if context is None:
        raise ValueError("focused Plan requires its selected stage inputs")
    stage = context.get("stage") or {}
    if stage.get("stage_kind") != "plan" or stage.get("requirement", {}).get("id") != state.get("requirement_id"):
        raise ValueError("focused Plan input stage or requirement is foreign")
    if stage["requirement"]["fingerprint"] != _ports._dc.requirement_fingerprint(ws, state.get("requirement_id")):
        raise ValueError("focused Plan input requirement is stale")
    handoff = _ports._verified_stage_handoff(context["lifecycle"], context["store"], context["manifest"], stage)
    artifact_store = context["lifecycle"]._artifact_store()
    for reference in handoff["selected_artifacts"]:
        payload = artifact_store.read(reference)
        if payload.get("schema") != "taskplane.loop-stage-output-bundle/v1":
            continue
        for snapshot in payload.get("files") or []:
            relative = snapshot.get("path")
            if relative not in paths:
                continue
            kind = paths[relative]
            content = snapshot.get("content")
            if (kind in admitted or not isinstance(content, str) or snapshot.get("encoding") != "utf-8"
                    or snapshot.get("sha256") != _ports.hashlib.sha256(content.encode()).hexdigest()
                    or snapshot.get("bytes") != len(content.encode())
                    or payload.get("step") not in ({"design"} if kind == "design" else {"plan", "plan_approval"})):
                raise ValueError("focused Plan selected artifact provenance is invalid")
            admitted[kind] = _ports.json.loads(content)
    if (stage.get("design") or state.get("design_fingerprint")) and "design" not in admitted:
        raise ValueError("focused Plan selected Design input is missing")
    if state.get("plan_fingerprint") and "plan" not in admitted:
        raise ValueError("focused Plan selected Plan input is missing")
    for kind, value in admitted.items():
        if not isinstance(value, dict) or value.get("requirement") != state.get("requirement_id"):
            raise ValueError("focused Plan " + kind + " requirement is foreign or missing")
        if kind == "plan" and any(not isinstance(task, dict) or task.get("req", value["requirement"]) != value["requirement"]
                for task in value.get("tasks") or []):
            raise ValueError("focused Plan task requirement is foreign")
    return admitted.get("design", {}), admitted.get("plan", {})


def _focused_stage_evidence(_ports, ws: str, state: Mapping[str, object],
                            stage: str
                            ) -> tuple[dict[str, object], list[str] | None]:
    """Assemble the closed, stage-owned inputs required by the Design."""
    requirement = _ports.reqs.get_requirement(ws, state.get("requirement_id")) \
        if state.get("requirement_id") else None
    req_material = ({
        key: _ports._copy_json(requirement.get(key))
        for key in ("id", "title", "functional", "nfr", "acceptance",
                    "open_questions", "contracts", "depends_on",
                    "context_files", "review_policy")
        if requirement.get(key) is not None
    } if isinstance(requirement, dict) else {})
    acceptance = _ports._copy_json(req_material.get("acceptance") or [])
    files = sorted({str(path) for path in
                    req_material.get("context_files") or [] if str(path)})
    domains = sorted({path.split("/", 1)[0] for path in files if path})

    if stage == "product":
        return ({
            "goal": str(state.get("goal") or ""),
            "requirement": req_material,
            "acceptance": acceptance,
            "domain": domains,
            "constraints": {
                "nfr": _ports._copy_json(req_material.get("nfr") or {}),
                "open_questions": _ports._copy_json(
                    req_material.get("open_questions") or []),
                "contracts": _ports._copy_json(req_material.get("contracts") or []),
            },
            "product_risk": _ports._copy_json(
                req_material.get("review_policy") or {}),
            "files": files,
        }, None)

    design, plan = _ports._focused_plan_inputs(ws, state) if stage == "plan" else ({}, {})
    design_material = ({
        key: _ports._copy_json(design.get(key))
        for key in ("schema", "summary", "selected_approach", "modules",
                    "contracts", "expanded_route_authority", "stage_policy",
                    "validation", "rollback")
        if isinstance(design, dict) and design.get(key) is not None
    })
    if stage == "design":
        return ({
            "approved_requirement": req_material,
            "acceptance": acceptance,
            # The Design team is selected before the current Design artifact
            # exists. Checked-in predecessor artifacts are history, never
            # evidence for this run's dynamic route.
            "proposed_solution": {},
            "interfaces": [],
            "data_boundaries": [],
            "trust_boundaries": {},
            "migration_risk": {},
            "rollback_risk": {},
            "files": files,
        }, None)

    if stage != "plan":
        raise _ports.lens_route_policy.LensRoutePolicyError(
            "focused evidence stage is unsupported")
    tasks = list(plan.get("tasks") or []) if isinstance(plan, dict) else []
    task_scopes = {str(task.get("id")): _ports._copy_json(task.get("scope") or [])
                   for task in tasks if isinstance(task, dict) and task.get("id")}
    ownership = {str(task.get("id")): str(task.get("owner") or "")
                 for task in tasks if isinstance(task, dict) and task.get("id")}
    selectors = {str(task.get("id")): str(task.get("tests") or "")
                 for task in tasks if isinstance(task, dict) and task.get("id")}
    task_to_ac = {
        str(task.get("id")): _ports._copy_json(
            task.get("acceptance_refs") or task.get("criteria") or [])
        for task in tasks if isinstance(task, dict) and task.get("id")
    }
    scope_files = sorted({str(path) for paths in task_scopes.values()
                          for path in paths if str(path)}) or files
    declared_route = plan.get("plan_route") \
        if isinstance(plan, dict) and isinstance(plan.get("plan_route"), dict) \
        else {}
    return ({
        "approved_product": req_material,
        "approved_design": design_material,
        "dependency_graph": _ports._copy_json(_ports.depgraph.summary(ws)),
        "task_scopes": task_scopes,
        "ownership": ownership,
        "selectors": selectors,
        "validation_strategy": _ports._copy_json({
            str(task.get("id")): task.get("tests")
            for task in tasks if isinstance(task, dict) and task.get("id")}),
        "task_to_ac_coverage": task_to_ac,
        "plan_route": _ports._copy_json(declared_route),
        "files": scope_files,
    }, None)


def _native_dispatch_intent(_ports,
    ws: str, state: Mapping[str, object], *, step: str, task_id: str,
    dispatch: Mapping[str, object], wait_policy: Mapping[str, object],
    wave_id: str | None = None,
) -> dict:
    """Persist non-authoritative intent before the host launches an agent."""
    try:
        locator = _ports.runtime_storage.load_workspace_locator(ws)
    except Exception:
        locator = None
    run_id = (str(locator.get("run_id") or "")
              if isinstance(locator, _ports.Mapping)
              else str(state.get("run_id") or ""))
    if _ports.re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", run_id) is None:
        import hashlib
        material = _ports.json.dumps({
            "workspace": _ports.os.path.realpath(ws),
            "goal": state.get("goal"),
            "baseline": state.get("baseline"),
        }, sort_keys=True, separators=(",", ":"))
        run_id = "loop-" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    role = str(dispatch.get("role") or _ports.STEP_ROLE.get(step) or step)
    context_inheritance = str(
        _ports.operational_settings.load_settings(
            environment=_ports.os.environ
        ).workflow.worker_inheritance["context"]
    )
    result = _ports.governed_command(ws, "dispatch", {
        "authorization": f"loop-dispatch:{run_id}",
        "consumer": f"{role}:{task_id}",
        "host": "native-agent",
        "payload": {
            "schema": "taskplane.native-agent-dispatch/v1",
            "step": step,
            "role": role,
            "task_name": dispatch.get("task_name"),
            "wait_policy": dict(wait_policy),
            "fork_turns": context_inheritance,
            "inherited_turns": 0,
        },
        "run_id": run_id,
        "task_id": task_id,
        "wave_id": wave_id,
    })
    result["wait_policy"] = dict(wait_policy)
    result["fork_turns"] = context_inheritance
    result["inherited_turns"] = 0
    return result


def _reserve_worker_dispatch_ref(_ports,
        ws: str, state: dict, *, stage: str, task: str,
        worker_workspace: str) -> tuple[str, int]:
    """Reserve a host-unique identity for one native worker attempt.

    Codex retains completed task names for the life of a thread. Re-emitting
    the same stable name after Fix or an unavailable Evaluate therefore turns
    a new worker slot into an unbindable orphan: no fresh direct native agent
    can own the old name, and nested agent identities do not match it exactly.
    Stage-native reservations include the bound run and durable attempt
    sequence. Already emitted identities stay unchanged.
    The worker contract still binds the exact emitted name.
    """
    stage = str(stage or "").strip()
    task = str(task or "").strip()
    if not stage or not task:
        raise ValueError("worker dispatch sequence identity is incomplete")
    key = f"{stage}:{task}"

    with _ports.mutate(ws) as fresh:
        if fresh is None:
            raise ValueError("worker dispatch sequence requires an active loop")
        sequences = fresh.setdefault("worker_dispatch_sequences", {})
        if not isinstance(sequences, dict):
            raise ValueError("worker dispatch sequence ledger is malformed")
        previous = sequences.get(key, 0)
        if type(previous) is not int or previous < 0:
            raise ValueError("worker dispatch sequence ledger is malformed")
        sequence = previous + 1
        sequences[key] = sequence
        selected = _ports.stage_loop.task_phase_state(_ports, ws, fresh,
            task if fresh.get("parallel") and stage in {"execute", "fix", "evaluate"} else None)
        state.clear()
        state.update(selected)

    ref = f"{task}-run-{state['run_id']}-attempt-{sequence}"
    _ports.tp.trace(ws, "worker_dispatch_identity_reserved", stage=stage, task=task,
             sequence=sequence, dispatch_ref=ref)
    return ref, sequence


def _parallel_evaluate_workspace(_ports,
        ws: str, state: Mapping[str, object],
        task: Mapping[str, object]) -> tuple[str | None, str | None]:
    """Resolve one unambiguous worker tree for parallel Evaluate.

    Falling back to the primary checkout here would pair worker target bytes
    with an unrelated primary graph.  Keep that identity failure explicit so
    missing or ambiguous worker state cannot silently weaken review routing.
    """
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        return None, ("parallel Evaluate task worktree is ambiguous: task "
                      "identity is missing")
    raw = str(task.get("workspace") or "").strip()
    if not raw or not _ports.os.path.isdir(raw):
        return None, ("parallel Evaluate task worktree is missing; restore "
                      "the exact claimed worktree and its graph before retry")
    supplied = _ports.os.path.abspath(_ports.os.path.expanduser(raw))
    worker = _ports.os.path.realpath(supplied)
    tasks = state.get("tasks")
    rows = tasks if isinstance(tasks, list) else []
    owners = sorted(str(row.get("id") or "")
                    for row in rows if isinstance(row, _ports.Mapping) and
                    row.get("workspace") and
                    _ports.os.path.realpath(str(row["workspace"])) == worker)
    if owners != [task_id]:
        return None, ("parallel Evaluate task worktree is ambiguous: "
                      f"workspace is assigned to {', '.join(owners)}")
    try:
        locator = _ports.runtime_storage.load_workspace_locator(ws)
        recovered = False
        if locator is None:
            raise ValueError("parallel Evaluate requires the primary run locator")
        expected_path = _ports.runtime_storage.task_worktree_path(ws, task_id)
    except (OSError, ValueError, _ports.runtime_storage.StorageIdentityError) as exc:
        return None, ("parallel Evaluate task worktree identity is invalid: "
                      f"{exc}")
    expected_path = _ports.os.path.abspath(expected_path)
    managed_root = _ports.os.path.realpath(_ports.os.path.dirname(expected_path))
    expected = _ports.os.path.join(managed_root, _ports.os.path.basename(expected_path))
    try:
        is_contained = _ports.os.path.commonpath((managed_root, worker)) == \
            managed_root
    except ValueError:
        is_contained = False
    if _ports.os.path.normcase(_ports.os.path.normpath(supplied)) != \
            _ports.os.path.normcase(expected) or \
            _ports.os.path.realpath(_ports.os.path.dirname(supplied)) != managed_root or \
            _ports.os.path.basename(supplied) != _ports.os.path.basename(expected) or \
            worker != expected or not is_contained or _ports.os.path.islink(supplied):
        return None, ("parallel Evaluate task worktree is not the exact "
                      "canonical managed task worktree for this task")
    if worker == _ports.os.path.realpath(ws):
        return None, ("parallel Evaluate task worktree is ambiguous: the "
                      "primary checkout cannot serve as task graph evidence")
    try:
        registration = (None if recovered else
                        _ports.runtime_storage.load_task_worktree_registration(
                            ws, task_id))
        primary_identity = _ports.runtime_storage.resolve_repository_identity(ws)
        worker_identity = _ports.runtime_storage.resolve_repository_identity(worker)
    except (OSError, ValueError, _ports.runtime_storage.StorageIdentityError) as exc:
        return None, ("parallel Evaluate task worktree identity is invalid: "
                      f"{exc}")
    registered_repository = ((registration or {}).get("repository") or {})
    if not recovered and (not isinstance(registered_repository, _ports.Mapping) or \
            registration is None or \
            registration.get("linked") is not True or \
            _ports.os.path.realpath(str(
                registration.get("primary_checkout") or "")) != \
            _ports.os.path.realpath(ws) or \
            _ports.os.path.realpath(str(
                registration.get("path") or "")) != worker or \
            registered_repository.get("repo_id") != \
            primary_identity.repo_id or \
            worker_identity.repo_id != primary_identity.repo_id):
        return None, ("parallel Evaluate task worktree identity does not "
                      "match this run and repository")
    if not recovered and locator is not None and registration.get("run_id") != \
            locator.get("run_id"):
        return None, ("parallel Evaluate task worktree identity does not "
                      "match this run and repository")
    return worker, None


def wave(_ports, ws: str, *, root_observation_authority: bytes | None = None) -> dict:
    """The next parallel wave: every task whose dependencies have PASSED
    and whose scope is disjoint from the rest of the wave. Each entry ships
    one verified phase envelope for a task in its own worktree. THE HARNESS IS PER AGENT: a worker's
    hook enforces its own task's contract in its own workspace."""
    if refusal := _ports._run_schema_refusal(ws):
        return refusal
    state = _ports.load(ws)
    if state is None:
        return {"error": "no active loop"}
    if error := _ports.stage_loop.dispatch_authority_error(_ports, state):
        return {"error": error}
    if not state.get("parallel"):
        return {"error": "loop is serial — `loop init --parallel` to enable"}
    if state["step"] != "execute":
        return {"error": f"waves only at execute (current: {state['step']})"}
    try:
        delivery_receipt = _ports._validated_delivery_mode(state)
    except _ports.delivery_policy.DeliveryPolicyError as exc:
        return {"error": "build delivery mode refused before dispatch: "
                + str(exc), "step": "execute", "parallel": True}
    if delivery_receipt is not None and delivery_receipt["mode"] != "build":
        return {"error": "build delivery mode refused before dispatch: "
                "execute dispatch requires build delivery mode",
                "step": "execute", "parallel": True}
    try:
        ledger = _ports._ensure_dispatch_telemetry(ws)
        budget_projection = _ports.dispatch_telemetry.budget_projection(
            ledger, _ports.SystemClock())
    except _ports.dispatch_telemetry.DispatchTelemetryError as exc:
        return {"error": "dispatch telemetry refused before wave: " + str(exc),
                "step": "execute", "parallel": True}
    if not budget_projection["dispatch_allowed"]:
        _ports.tp.trace(ws, "loop_wave_budget_stop",
                 triggered=budget_projection["triggered"])
        return {
            "step": "human_scope_review", "parallel": True,
            "paused": True, "wave": [], "held": [],
            "budget": budget_projection,
            "instruction": "Binding delivery budget reached; obtain human "
                           "scope review before any new dispatch.",
        }
    state = _ports.load(ws) or state
    tasks = state.get("tasks") or []
    enforcement = ((state.get("enforcement") or {}).get("current"))
    passed = {t["id"] for t in tasks
              if t.get("status") in _ports.DEP_SATISFIED}
    try:
        ready, held, executable_topology = _ports.select_ready_tasks(
            tasks, passed=passed,
            repository_files=_ports._declared_repository_test_files(ws, tasks),
            allow_isolated_variants=bool(state.get("ab")))
    except _ports.plan_topology.PlanTopologyError as exc:
        return {"error": "executable Plan topology refused dispatch: " + str(exc),
                "step": "execute", "parallel": True}
    # A/B variants deliberately overlap in isolated worktrees.  The approved
    # Plan topology remains the default; variant isolation is the one existing
    # runtime exception and never makes non-variant work parallel.
    if state.get("ab"):
        selected: list[dict] = []
        remaining: list[dict] = []
        for task in ready:
            clash = next((member for member in selected
                          if _ports._scopes_overlap(task.get("scope"), member.get("scope"))
                          and not (task.get("variant") and member.get("variant")
                                   and task.get("variant") != member.get("variant"))),
                         None)
            if clash is None:
                selected.append(task)
            else:
                remaining.append({"task": task["id"],
                                  "reason": f"scope overlaps {clash['id']} — next wave",
                                  "shared_owner": "scope"})
        ready, held = selected, [*held, *remaining]

    effective_settings = _ports.operational_settings.load_settings(
        environment=_ports.os.environ)
    configured_concurrency = effective_settings.build.concurrency
    active = {task["id"] for task in tasks if task.get("workspace") and
              _ports.tp._active_worker_contracts(task["workspace"])}
    continuing = [task for task in tasks if task["id"] not in active and
                  (task.get("phase_step") in {"evaluate", "fix"} or task.get("status") == "built")]
    ready = list({task["id"]: task for task in [*continuing, *ready] if task["id"] not in active}.values())
    held.extend({"task": task_id, "reason": "phase attempt is already in flight"} for task_id in sorted(active))
    if isinstance(configured_concurrency, int):
        capacity = max(0, configured_concurrency - len(active))
        held.extend({"task": task["id"], "reason": "configured phase concurrency cap"}
                    for task in ready[capacity:])
        ready = ready[:capacity]
    wave_wait_policy = (
        _ports.event_wait_policy("execute-wave", len(ready)) if ready else None)
    wave_wait_invocation = (
        _ports.event_wait_invocation(
            wave_wait_policy, [str(task["id"]) for task in ready])
        if ready else None)
    for task in ready:
        try:
            if task.get("phase_step", "execute") in {"execute", "fix"}:
                _ports.build_dispatch_lens_routing(state, task, workspace=ws)
        except _ports.delivery_policy.DeliveryPolicyError as exc:
            return {"error": "Build delivery mode refused: " + str(exc), "wave": [], "held": held}
    try:
        if not state.get("_stage_bindings"):
            _ports._stage_loop_wave_dispatches(ws, state, tasks)
    except (ValueError, OSError) as exc:
        return {"error": "wave stage binding refused: " + str(exc), "wave": [], "held": held}
    entries = []
    for task in ready:
        try:
            if task in continuing:
                entry = _ports.next_action(ws, root_observation_authority=root_observation_authority,
                                           _worker_task_id=task["id"])
            else:
                worker = _wave_worktree(_ports, ws, state, task)
                entry = claim(_ports, ws, str(task["id"]), worker,
                              root_observation_authority=root_observation_authority)
        except (ValueError, OSError) as exc:
            entry = {"error": str(exc)}
        if not (entry.get("obligations") or {}).get("dispatch_allowed"):
            detail = entry.get("obligations", entry)
            held.append({"task": task["id"], "reason": detail.get("error") or detail.get("awaiting") or "existing attempt pending"})
        else:
            entries.append(entry)
    _ports.tp.trace(ws, "loop_wave", ready=[task["id"] for task in ready],
        held=[item["task"] for item in held], topology_fingerprint=executable_topology["fingerprint"])
    return {"schema": "taskplane.stage-wave/v1", "step": "execute", "parallel": True,
            "wave": entries, "held": held, "wait_invocation": wave_wait_invocation}


def claim(_ports, ws: str, task_id: str, agent_ws: str, *, root_observation_authority=None) -> dict:
    """Bind one approved wave task, then use the ordinary phase dispatcher."""
    from taskplane.preflight import PreflightError, atomic_governed_startup
    state = _ports.load(ws)
    if not state or not state.get("parallel") or state.get("step") != "execute":
        return {"error": "claim requires a current parallel Build wave"}
    task = next((row for row in state.get("tasks") or [] if row["id"] == task_id), None)
    if task is None or task.get("status") not in {"pending", "running"}:
        return {"error": "task is not claimable", "task": task_id}
    try:
        atomic_governed_startup(workspace=ws, worker_workspace=agent_ws, task_id=task_id)
        _ports.runtime_storage.bind_worker_locator(ws, agent_ws, task_id)
        binding = (state.get("_stage_bindings") or {}).get(task_id)
        if not binding or not binding.get("build"):
            raise ValueError("claim requires the task's committed wave stage")
        with _ports.mutate(ws) as current:
            row = next(item for item in current["tasks"] if item["id"] == task_id)
            if row.get("status") not in {"pending", "running"} or row.get("workspace") not in {None, agent_ws}:
                raise ValueError("task claim changed")
            row["workspace"] = agent_ws
            row["status"] = "running"
        return _ports.next_action(ws, root_observation_authority=root_observation_authority,
                                  _worker_task_id=task_id)
    except (PreflightError, ValueError, OSError) as exc:
        return {"error": "wave task refused: " + str(exc), "task": task_id}


def _wave_worktree(_ports, ws, state, task):
    """Create or verify only the selected task's registered checkout."""
    path = _ports.runtime_storage.task_worktree_path(ws, task["id"])
    if not _ports.os.path.exists(path):
        branch = "codex/taskplane-" + state["run_id"] + "-" + _ports.runtime_storage._worktree_token(task["id"])
        baseline = state.get("baseline")
        if not baseline:
            raise ValueError("wave has no approved baseline")
        result = _ports.tp._run(["git", "worktree", "add", "-b", branch, path, baseline], cwd=ws)
        if result.returncode:
            raise ValueError("task worktree creation failed: " + result.stderr.strip())
    _ports.runtime_storage.register_task_worktree(ws, path, task["id"])
    return path


def read_pending_action(_ports, ws: str) -> dict | None:
    """Observe an existing attempt without settings, host admission or effects.

    This cannot authorize dispatch or a gate. A later mutation must reload
    current authority. Never flush an outbox or reconstruct a preparation
    merely to tell a replacement controller what is already in flight.
    """
    try:
        if _ports.loop_status._dashboard_replay_block(ws) is not None:
            return None  # Publication recovery must run before pending pickup.
        state = _ports._load_raw(ws)
        if not _ports.run_context.selected(state):
            return None
        if refusal := _ports._run_schema_refusal(ws):
            return {**refusal, "read_only": True, "dispatch_allowed": False}
        locator = _ports.runtime_storage.load_workspace_locator(ws) or {}
        if state.get("parallel") and state.get("step") == "execute" and not locator.get("task_id"):
            return None  # One active task cannot hide ready sibling phases.
        observed = _ports._phase_bridge_pending(ws, state)
        return None if observed is None else project_next_action_for_host(_ports, ws, {
            **observed, "read_only": True, "dispatch_allowed": False})
    except (ValueError, OSError) as exc:
        return project_next_action_for_host(_ports, ws, {"error": "phase runtime pickup refused: " + str(exc),
                "code": "unsupported_run_schema" if "unsupported_run_schema" in str(exc) else "invalid_run",
                "read_only": True, "dispatch_allowed": False})


def next_action(_ports,
        ws: str, rid: str | None = None, *,
        expanded_route_provider_client:
        terminal_truth.ExpandedRouteProviderClient | None = None,
        expanded_route_provider_receipt:
        terminal_truth.ExpandedRouteProviderReceipt | None = None,
        root_observation_authority: bytes | None = None,
        _worker_task_id: str | None = None) -> dict:
    """Advance to the current step's work: activate its contract and return
    what the driver should run. Human steps pause without activating."""
    if refusal := _ports._run_schema_refusal(ws):
        return refusal
    state = _ports.load(ws)
    if state is None:
        return {"error": "no active loop — run `tp.py loop init` first"}
    if error := _ports.stage_loop.dispatch_authority_error(_ports, state):
        return {"error": error}
    phase_ws = ws
    if state.get("parallel") and state.get("step") == "execute" and _worker_task_id is None:
        return _ports.wave(ws, root_observation_authority=root_observation_authority)
    if _worker_task_id is not None:
        state = _ports.stage_loop.task_phase_state(_ports, ws, state, _worker_task_id)
        task = _ports._current_task(state)
        if state.get("step") not in {"execute", "fix", "evaluate"}:
            return {"error": "task has no dispatchable phase"}
        phase_ws = task.get("workspace")
        if not phase_ws or not _ports.os.path.isdir(phase_ws):
            return {"error": "task phase requires its bound worktree"}
    try:
        pending_phase = _ports._phase_bridge_pending(phase_ws, state)
        if pending_phase is not None:
            return pending_phase
        phase_context = _ports._phase_bridge_context(phase_ws, state)
        if phase_context is None and state.get("step") not in _ports.HUMAN_STEPS:
            raise ValueError("current phase runtime is missing")
        if phase_context is not None:
            _ports.phase_harness.admit_lens_plan(phase_context)
    except (ValueError, OSError) as exc:
        return {"error": "phase runtime pickup refused: " + str(exc), "step": state.get("step")}
    if rid and rid != state.get("requirement_id"):
        return {"error": "a phase dispatch cannot replace its bound requirement"}
    step = state["step"]
    expanded_authority_supplied = (
        expanded_route_provider_client is not None or
        expanded_route_provider_receipt is not None)
    if (expanded_route_provider_client is None) != \
            (expanded_route_provider_receipt is None):
        return {"error": "expanded-route authority requires the live provider "
                         "client and receipt together",
                "step": step, "status": _ports.status(ws)}
    if expanded_authority_supplied and step != "plan":
        return {"error": "expanded-route authority is limited to Plan",
                "step": step, "status": _ports.status(ws)}

    retro_requested = step == "retro" or (
            step == "failed" and
            isinstance(state.get("run_artifact_binding"), _ports.Mapping) and
            not isinstance(state.get("terminal_cleanup"), _ports.Mapping))
    if retro_requested:
        try:
            context = _ports._phase_bridge_context(ws, state)
            if context is not None:
                if step != "retro":
                    raise ValueError("failed run has no current Retro stage")
                _ports._phase_bridge_retro_inputs(ws, context)
        except (ValueError, OSError) as exc:
            return {"step": "retro", "error": "phase Retro prerequisites refused: " + str(exc)}
    if step in _ports.HUMAN_STEPS:
        return _human_step_action(_ports, state, step, ws)

    # Defence in depth: a per-task step must have a current task. If the loop
    # ever reaches execute/fix/evaluate with none (e.g. a plan that produced
    # no tasks), return a structured error instead of crashing in
    # _step_contract on task["id"].
    if step in ("execute", "fix", "evaluate") and _ports._current_task(state) is None:
        return {"error": f"loop step '{step}' has no current task — the plan "
                         f"produced no tasks, so the loop should not be here. "
                         f"Re-run the plan step (`loop gate fail`, then "
                         f"re-plan) or start over with `loop init`.",
                "step": step, "status": _ports.status(ws)}

    if step == "fix":
        current_dispatch_task = _ports._current_task(state) or {}
        correction = current_dispatch_task.get("failure_routing")
        if not isinstance(correction, _ports.Mapping) or \
                correction.get("next") != "fix" or \
                correction.get("product_fix_allowed") is not True:
            return {
                "error": "product Fix refused: the current candidate has no "
                         "exclusive classified product-failure authority",
                "step": step,
                "failure_routing": correction,
                "status": _ports.status(ws),
            }

    # Per-task steps run in the task's own workspace when one was claimed.
    act_ws = phase_ws
    is_parallel_evaluate = step == "evaluate" and bool(state.get("parallel"))
    if step in ("evaluate", "fix") and state.get("parallel"):
        current = _ports._current_task(state) or {}
        if step == "evaluate":
            resolved_ws, workspace_error = _ports._parallel_evaluate_workspace(
                ws, state, current)
            if workspace_error or resolved_ws is None:
                return {"error": workspace_error or
                        "parallel Evaluate task worktree is unresolved",
                        "step": step,
                        "status": _ports.status(ws)}
            act_ws = resolved_ws
        else:
            tws = current.get("workspace")
            act_ws = tws if tws and _ports.os.path.isdir(tws) else ws

    refusal, state, design_decomposition, design_lens_policy = _prepare_design_input(_ports, state, step, ws)
    if refusal is not None:
        return refusal

    refusal, dispatch, contract, snapshot, worker_ref = _prepare_phase_contract(_ports, act_ws, design_decomposition, design_lens_policy, phase_context, state, step, ws)
    if refusal is not None:
        return refusal

    # The graph is an input to evaluation, not a cache refreshed only after
    # review. A parallel evaluator scans its isolated task worktree rather
    # than publishing a partial worker graph into the shared checkout. This
    # must finish before impact, quality, routing, leases, or activation.
    if step == "em":
        # Make the final graph describe the merged, as-built system BEFORE
        # the engineering reviewer receives it.  Doing this at the EM gate
        # would invalidate the review's graph fingerprint at sign-off.
        try:
            _ports._true_up_graph(ws, state)
        except Exception as exc:
            if state.get("graph_governance"):
                return {"error": f"graph true-up failed before {step}: {exc}",
                        "step": step, "status": _ports.status(ws)}
            _ports.tp.trace(ws, "graph_refresh_failed", step=step, error=str(exc))
    elif step == "evaluate":
        graph_refresh_ws = act_ws if is_parallel_evaluate else ws
        try:
            _ports.depgraph.scan(graph_refresh_ws)
        except Exception as exc:
            return {"error": f"graph refresh failed before {step}: {exc}",
                    "step": step, "status": _ports.status(ws),
                    "review_kernel": {"status": "impact_incomplete",
                                      "slots": []}}
    task = _ports._current_task(state)
    # Evaluate uses only the canonical workspace returned by the resolver.
    # Re-reading task["workspace"] here would create a second path authority
    # after validation and reopen alias/retarget races for graph evidence.
    if is_parallel_evaluate or (
            step == "fix" and state.get("parallel")):
        wtree = act_ws
    else:
        candidate_ws = (task or {}).get("workspace") or ""
        wtree = candidate_ws if _ports.os.path.isdir(candidate_ws) else ws
    graph_ws = act_ws if is_parallel_evaluate else ws
    if step in {"execute", "fix"}:
        try:
            _ports.build_dispatch_lens_routing(state, task, workspace=wtree)
        except _ports.delivery_policy.DeliveryPolicyError as exc:
            return {"error": "Build delivery mode refused: " + str(exc), "step": step}
    def heads():                    # lazy: only an emitting branch pays
        # The row must name the same canonical tree that supplied the graph
        # and impact.  A serial loop can retain an old task workspace after
        # a claim or resume, but Evaluate deliberately reviews the project
        # checkout in that mode.  Naming that stale worker tree would make
        # the trace describe bytes that were never scanned.
        return {"head": _ports.tp.git_head(graph_ws),
                "scanned_head": (_ports.depgraph.load(graph_ws).get("meta")
                                 or {}).get("scanned_head")}
    # Blast radius from the persistent dependency graph — the reviewer sees
    # what the change can break WITHOUT re-deriving dependencies (no tokens).
    refusal, imp, review_base = _prepare_review_impact(_ports, act_ws, graph_ws, heads, state, step, task, ws)
    if refusal is not None:
        return refusal

    refusal, review_kernel, evaluate_children = _prepare_review_workers(_ports, act_ws, contract, dispatch, expanded_route_provider_client, expanded_route_provider_receipt, graph_ws, imp, is_parallel_evaluate, review_base, root_observation_authority, state, step, task, ws)
    if refusal is not None:
        return refusal
    return _publish_phase_dispatch(_ports, act_ws, contract, dispatch, evaluate_children, phase_context, review_kernel, root_observation_authority, snapshot, state, step, task, worker_ref, ws)


def event_wait_policy(_ports, outstanding_set: str, outstanding_count: int) -> dict:
    """Return the single long-lived event wait for a dispatched set."""
    if not str(outstanding_set or "").strip():
        raise ValueError("outstanding_set is required")
    if int(outstanding_count) < 1:
        raise ValueError("outstanding_count must be positive")
    return {
        "schema": "taskplane.wait-policy/v1",
        "outstanding_set": str(outstanding_set),
        "outstanding_count": int(outstanding_count),
        "mode": "event",
        "timeout_seconds": 1800,
        "minimum_timeout_seconds": 300,
        "reissue_after": ["completion", "attention"],
        "scheduled_polling": False,
    }


def event_wait_invocation(_ports, policy: Mapping[str, object],
                          outstanding_members: list[str], *,
                          wake: str | None = None) -> dict:
    """Emit one live event wait, or its wake-authorized reissue.

    A host may issue the first invocation immediately. A later invocation is
    a reissue and must carry the completion/attention event that woke the
    prior wait; timeouts and scheduled polling never authorize one.
    """
    value = dict(policy) if isinstance(policy, _ports.Mapping) else {}
    members = list(outstanding_members)
    if (value.get("schema") != "taskplane.wait-policy/v1" or
            value.get("mode") != "event" or
            value.get("scheduled_polling") is not False or
            int(value.get("timeout_seconds") or 0) < 1800 or
            value.get("reissue_after") != ["completion", "attention"]):
        raise ValueError("event wait policy is invalid")
    if (not members or any(not isinstance(member, str) or not member.strip()
                           for member in members) or
            len(set(members)) != len(members) or
            int(value.get("outstanding_count") or 0) != len(members)):
        raise ValueError("event wait outstanding set is invalid")
    if wake is not None and wake not in value["reissue_after"]:
        raise ValueError(
            "event wait reissue requires a completion or attention wake")
    return {
        "schema": "taskplane.event-wait-invocation/v1",
        "operation": "wait_for_events",
        "outstanding_set": value["outstanding_set"],
        "outstanding_members": members,
        "timeout_seconds": int(value["timeout_seconds"]),
        "scheduled": False,
        "reissue": wake is not None,
        "wake": wake,
    }




def _human_step_action(_ports, state, step, ws):
    awaiting = {
        "design_approval": "human: review design/design.md and the "
                           "Design Contract, then `loop approve`",
        "plan_approval": "human: review the requirement, design, plan, scope, "
                         "validation and recovery, then `loop approve`",
        "selection": "human: A/B gate — compare the variants (rendered "
                     "side by side, criteria + lenses + spend), then "
                     "`loop select <variant|task-id|hybrid>`",
        "signoff": "human: EM sign-off, then `loop approve`",
        "escalated": "human: `loop resolve retry|skip|abort` "
                     "(fix cycles exhausted)",
        "done": "loop complete",
        "failed": "loop aborted",
    }[step]
    out = {"step": step, "paused": True, "awaiting": awaiting,
           "status": _ports.status(ws)}
    if step == "selection":
        out["variants"] = [
            {"id": t["id"], "variant": t.get("variant"),
             "status": t.get("status"), "scope": t.get("scope"),
             "worktree": _ports.runtime_storage.task_worktree_reference(ws, t["id"])}
            for t in (state.get("tasks") or []) if t.get("variant")]
        out["instruction"] = (
            "Present BOTH variants for the human's pick: re-run each "
            "variant's tests (trust but verify), render both UIs side "
            "by side — live screenshots over mocks — with the criteria "
            "scoreboard, lens findings, and per-variant resource spend. "
            "Then WAIT; `loop select` only on their explicit choice.")
    if step == "signoff":
        # Run the MECHANICAL Definition-of-Done here so the human signs off
        # seeing both the EM's read-out AND the scope-diff/lint verdict.
        out["dod"] = _ports._signoff_gate_dod(ws, state)
        # v2.3.0 wiring: accepted design drift and hand-declared edge
        # realizations are VISIBLE at sign-off, not dead-on-pass.
        notices = list(
            (state.get("signoff_evidence") or {}).get("notices") or [])
        if notices:
            out["notices"] = notices
    if step == "design_approval":
        design_errors = _ports._design_dod_errors(ws, state)
        out["dod"] = {"passed": not design_errors,
                      "errors": design_errors,
                      "fingerprint": _ports._design_evidence_fingerprint(ws)}
        # v2.3.0 wiring: self-attested lens evidence is surfaced AT the
        # human gate instead of being silently accepted.
        notices = _ports._dc.design_approval_notices(ws)
        if notices:
            out["notices"] = notices
    out["runtime_evals"] = _ports.runtime_eval.guidance(step)
    return out


def _prepare_phase_contract(_ports, act_ws, design_decomposition, design_lens_policy, phase_context, state, step, ws):
    capability_vars = {
        "TASKPLANE_MODEL_SELECTION", "TASKPLANE_EFFORT_SELECTION",
        "TASKPLANE_SUPPORTED_MODEL_ALIASES",
        "TASKPLANE_SUPPORTED_EFFORT_VALUES",
        "TASKPLANE_NATIVE_STRUCTURED_OUTPUT",
    }
    capability_snapshot = (
        _ports.host_capabilities.dispatch_snapshot_from_environment(
            ws, host=_ports.tp.host(), environment=_ports.os.environ)
        if step == "evaluate" or any(name in _ports.os.environ
                                     for name in capability_vars) else None)
    try:
        effective_settings = _ports.operational_settings.load_settings(
            environment=_ports.os.environ)
    except _ports.operational_settings.SettingsError as exc:
        return ({"error": "operational settings failed closed: " + str(exc),
                "step": step, "status": _ports.status(ws)}, None, None, None, None)
    if state.get("settings_digest") not in (None, effective_settings.digest):
        return ({"error": "operational settings changed during the active "
                         "governed transition",
                "step": step, "status": _ports.status(ws)}, None, None, None, None)

    worker_task = _ports._current_task(state)
    worker_ref = str((worker_task or {}).get("id") or step)
    dispatch_ref, _ = _ports._reserve_worker_dispatch_ref(
        ws, state, stage=step, task=worker_ref, worker_workspace=act_ws)
    dispatch = _ports.tp.dispatch_fields(
        "step", _ports.STEP_ROLE[step], dispatch_ref,
        _ports.tp.step_tier(step, worker_task),
        capability_snapshot=capability_snapshot,
        enforcement_mode=_ports.os.environ.get("TASKPLANE_ENFORCE_DISPATCH"),
        settings_context=effective_settings)
    if dispatch.get("dispatch_blocked"):
        _ports.tp.trace(ws, "dispatch_route_resolved", step=step,
                 task=(worker_task or {}).get("id"), resolution="blocked",
                 reason=dispatch["dispatch_route"].get("reason"))
        return ({"error": "strict host dispatch route cannot be honored — "
                         + dispatch["dispatch_route"].get("reason", ""),
                "step": step, **dispatch}, None, None, None, None)

    contract = _ports._step_contract(step, state, act_ws)
    try:
        if phase_context is not None:
            definition = phase_context["definition"]
            dispatch = _ports.tp.dispatch_fields("step", definition["role"], dispatch_ref,
                definition["model_tier"], capability_snapshot=capability_snapshot,
                enforcement_mode=_ports.os.environ.get("TASKPLANE_ENFORCE_DISPATCH"),
                settings_context=effective_settings)
            # The admitted definition pins its real skill bytes. A phase role
            # need not have a separately invented agents/<role>.md file.
            dispatch["role_instructions"] = _ports.os.path.join(
                _ports.os.path.dirname(_ports.os.path.dirname(_ports.os.path.abspath(_ports.__file__))), definition["skill_ref"])
    except (ValueError, OSError) as exc:
        return ({"error": "phase definition admission refused: " + str(exc), "step": step}, None, None, None, None)
    if step in {"evaluate", "fix"} and worker_task is not None:
        contract["failure_candidate"] = _ports._failure_candidate_identity(
            act_ws, worker_task)
    if step == "fix" and worker_task is not None:
        contract["failure_routing"] = _ports._copy_json(
            worker_task.get("failure_routing") or {})
    if step == "design" and design_decomposition is not None and \
            design_lens_policy is not None:
        contract["design_decomposition"] = _ports._copy_json(design_decomposition)
        contract["design_lens_policy"] = design_lens_policy.to_dict()
    artifact_binding = state.get("run_artifact_binding")
    if isinstance(artifact_binding, _ports.Mapping):
        # Lifecycle hooks preserve every governed stage worker beside the
        # same run evidence.  This is a locator/binding only; it grants no
        # transition, write, or correction authority.
        contract["run_artifact_root"] = _ports._run_artifact_root(ws, state)
        contract["run_artifact_binding"] = _ports._copy_json(artifact_binding)
    enforcement = ((state.get("enforcement") or {}).get("current"))
    if enforcement:
        contract["enforcement"] = enforcement
    contract = _ports.tp.prepare_worker_contract(
        act_ws, contract, stage=step, task=worker_ref,
        task_name=dispatch["task_name"], role_marker=dispatch["role_marker"])
    evaluator_contract = None
    if step == "evaluate":
        evaluator_contract = _ports.evaluation_output.create_evaluator_contract(
            workspace=act_ws, task=str(_ports._current_task(state)["id"]),
            slot=contract["task_slot"],
            capability_snapshot=capability_snapshot)
        contract = dict(contract)
        contract["output_contract"] = evaluator_contract
    contract = _ports._bind_worker_submission(
        act_ws, state, step, contract, _ports._current_task(state))
    snapshot = _ports.tp.git_head(act_ws)
    # Readiness is evaluated against the complete child contract in memory.
    # The slot is activated only after every control-plane precondition has
    # succeeded, immediately before the native dispatch is returned.  This
    # keeps a failed preflight from binding worker authority to the root
    # checkout while still ensuring the child cannot start before its slot.
    dor_ready, blockers, warnings = _ports.tp.dor_check(
        contract, act_ws, snapshot)
    if step == "design":
        design_dor = _ports._design_dor(ws, state)
        blockers.extend(design_dor["blockers"])
        warnings.extend(design_dor["warnings"])
        dor_ready = not blockers
    _ports.tp.trace(ws, "loop_step", step=step, role=_ports.STEP_ROLE[step],
             task=(_ports._current_task(state) or {}).get("id"),
             dor_ready=dor_ready, dor_blockers=blockers,
             dor_warnings=warnings)
    if not dor_ready:
        return ({"error": "Definition of Ready failed — resolve blockers "
                         "before this step can start",
                "step": step, "role": _ports.STEP_ROLE[step],
                "dor": {"ready": False, "blockers": blockers,
                        "warnings": warnings},
                "status": _ports.status(ws)}, None, None, None, None)
    return (None, dispatch, contract, snapshot, worker_ref)


def _prepare_review_impact(_ports, act_ws, graph_ws, heads, state, step, task, ws):
    imp = None
    try:
        review_base = _ports._review_baseline(ws, state, step)
    except (ValueError, OSError) as exc:
        return ({"error": "review baseline refused: " + str(exc), "step": step}, None, None)
    if step in ("evaluate", "em"):
        diff_ws = act_ws if step == "evaluate" else ws
        changed = [f for f in _ports._diff_files(
            diff_ws, review_base or "HEAD")
            if not f.startswith(_ports.lens_router.LOOP_OWNED) and
            (step == "em" or not task or not task.get("scope") or
             _ports.tp.match_any(f, task["scope"]))]
        if changed or step == "em":
            review_policy = (_ports._aggregate_impact_policy(state.get("tasks") or [])
                             if step == "em" else
                             _ports.depgraph.impact_policy(task or {}))
            imp = _ports.depgraph.impact(graph_ws, changed, policy=review_policy)
            # Product side of the blast radius: which OTHER requirements'
            # surface this diff touches (their criteria may need re-checking)
            # and which requirements depend on the affected ones.
            prod = _ports.depgraph.product_impact(graph_ws, changed)
            own = (task or {}).get("req") or state.get("requirement_id")
            own = _ports.depgraph.req_node(own) if own else None
            imp["affected_requirements"] = [
                r for r in prod["affected_requirements"] if r != own]
            imp["dependent_requirements"] = prod["dependent_requirements"]
            nudges = _ports._edge_nudges(diff_ws, changed,
                                  review_base or "HEAD")
            if nudges:
                imp["edge_suggestions"] = nudges
            _ports.tp.trace(ws, "graph_impact", step=step,
                     touched=imp["touched"],
                     impacted=imp["total_impacted"],
                     affected_reqs=imp["affected_requirements"], **heads())
    elif step in ("execute", "fix") and task:
        # v2.0.0: the BUILDER sees the blast radius BEFORE changing code
        # (previously only the judges at evaluate/em did) - side effects
        # get prevented, not just detected a loop-step later.
        scope = task.get("scope") or []
        if scope and _ports.depgraph.load(ws)["modules"]:
            mods = _ports.depgraph.scope_modules(ws, scope)
            if mods:
                imp = _ports.depgraph.impact(
                    ws, mods, policy=_ports.depgraph.impact_policy(task))
                if not imp["touched"]:
                    imp = None
                else:
                    _ports.tp.trace(ws, "graph_impact", step=step,
                             touched=imp["touched"],
                             impacted=imp["total_impacted"], **heads())
    elif step == "design":
        design_req = _ports.reqs.get_requirement(ws, state.get("requirement_id"))
        design_scope = (design_req or {}).get("context_files") or []
        design_modules = _ports.depgraph.scope_modules(ws, design_scope)
        if design_modules and _ports.depgraph.load(ws).get("modules"):
            design_policy = {"local_depth": 3,
                             "boundary_mode": "contract-only",
                             "contract_depth": 1, "requirement_depth": 1}
            imp = _ports.depgraph.impact(ws, design_modules, policy=design_policy)
            _ports.tp.trace(ws, "graph_impact", step=step,
                     touched=imp["touched"],
                     impacted=imp["total_impacted"], **heads())
    return (None, imp, review_base)


def _prepare_review_workers(_ports, act_ws, contract, dispatch, expanded_route_provider_client, expanded_route_provider_receipt, graph_ws, imp, is_parallel_evaluate, review_base, root_observation_authority, state, step, task, ws):
    audit_info = None
    if step == "em":
        audit_info = _ports._audit_brief(ws, state)
        if audit_info.get("due"):
            _ports.tp.trace(ws, "audit_due", reason=audit_info.get("reason"),
                     reviews_completed=audit_info.get("reviews_completed"))

    # Requirement anchoring: this task's R-id (or the loop's) is the spine —
    # its acceptance criteria are the DoD the evaluator holds the work to.
    req_rec = None
    rid = (task or {}).get("req") or state.get("requirement_id")
    if rid:
        req_rec = _ports.reqs.get_requirement(ws, rid)

    review_kernel = None
    review_workspace = None
    if step in ("evaluate", "em"):
        diff_ws = act_ws if is_parallel_evaluate else ws
        review_workspace = diff_ws
        base_ref = review_base or "HEAD"
        try:
            review_delivery_authority = _ports._DELIVERY_MODE_AUTHORITY_UNSET
            if step == "em":
                delivery_receipt = _ports._validated_delivery_mode(state)
                if delivery_receipt is not None:
                    review_delivery_authority = delivery_receipt
            retry_context = None
            review_task = task
            review_kernel, routing = _ports._review_kernel(
                ws, diff_ws, base=base_ref, step=step, task=review_task,
                graph=_ports.depgraph.load(graph_ws), impact=imp or {},
                requirement=req_rec,
                test_evidence=((state.get("_suite_evidence") or {}).get(
                    str((task or {}).get("id") or "")) or {}),
                retry_context=retry_context,
                expanded_route_provider_client=
                    expanded_route_provider_client,
                expanded_route_provider_receipt=
                    expanded_route_provider_receipt,
                delivery_mode_receipt=review_delivery_authority)
            if step == "em" and review_delivery_authority is not \
                    _ports._DELIVERY_MODE_AUTHORITY_UNSET and \
                    review_kernel.get("status") == "ready" and \
                    review_kernel.get("slots") == []:
                collected = _ports.collect_review_bridge(
                    diff_ws, publish=False, run_id=review_kernel["run_id"],
                    evaluator_result={
                        "delivery_mode_receipt":
                            review_delivery_authority["fingerprint"]},
                    producer_observation_fingerprint=
                        review_delivery_authority["fingerprint"],
                    collection_stage="EM", result_validator=lambda value: value)
                review_kernel = {**review_kernel,
                                 "status": collected.get("status"),
                                 "empty_lens_collection": collected.get(
                                     "empty_lens_collection")}
            review_kernel = _ports._bind_stateless_review_contract_actions(
                diff_ws, review_kernel,
                task_id=str((task or {}).get("id") or
                            "engineering-signoff"))
            zero_lens_delivery = review_kernel.get("expected_lenses") == [] and \
                review_kernel.get("slots") == [] and (review_kernel.get(
                    "zero_lens_evaluation") is True or review_kernel.get(
                    "delivery_mode_receipt") is not None)
        except _ports._ReviewGraphQualityError as exc:
            _ports.tp.trace(ws, "review_graph_quality_blocked", step=step,
                     task=(task or {}).get("id"),
                     reasons=exc.quality.get("reasons") or [], slots=[])
            return ({
                "error": "graph quality failed before selective review: "
                         + str(exc),
                "step": step, "status": _ports.status(ws),
                "graph_quality": {
                    "status": exc.quality.get("status"),
                    "reasons": list(exc.quality.get("reasons") or []),
                    "artifact": exc.reference,
                },
                "review_kernel": {"status": "impact_incomplete",
                                  "slots": []},
            }, None, None)
        except Exception as exc:
            review_kernel = {"status": "kernel_unavailable", "slots": [],
                             "reason": f"{exc.__class__.__name__}: {exc}"}
            # Keep the first causal refusal. An unavailable kernel has no
            # evaluator attempt identity and cannot authorize child evidence.
            return ({"error": "Review kernel preparation failed closed: " + review_kernel["reason"],
                "step": step, "status": _ports.status(ws), "review_kernel": review_kernel,
                "dispatch_allowed": False}, None, None)
        binding = {
            "schema": "taskplane.review-kernel-binding/v1",
            "run_id": review_kernel.get("run_id"),
            "workspace": review_workspace,
            "stage": (_ports.EVALUATE_ROUTE_STAGE if step == "evaluate" else
                      "review"),
            "status": review_kernel.get("status"),
        }
        with _ports.mutate(ws) as fresh:
            if fresh is not None:
                fresh.setdefault("review_kernel_runs", {})[
                    _ports._review_kernel_binding_key(step, task)] = binding
    evaluate_children = None
    failure_classification = None
    if step == "evaluate":
        try:
            attempt_id = str((review_kernel or {}).get("run_id") or "").strip()
            if not attempt_id:
                raise ValueError("Evaluate evidence lacks evaluator attempt identity")
            failure_classification = _ports._failed_build_classification(
                act_ws, state, task or {}, evaluator_attempt_id=attempt_id)
            if failure_classification is not None:
                contract["failure_classification"] = _ports._copy_json(failure_classification)
            else:
                evaluate_children = _ports._prepare_public_evaluate_evidence(
                    ws, act_ws, state, task or {},
                    evaluator_attempt_id=attempt_id)
                evaluate_children = _ports._dispatch_public_evaluate_evidence_children(
                    ws, state, task or {}, evaluate_children,
                    observation_authority=root_observation_authority,
                    model_tier=str(dispatch["model_tier"]))
                contract["evaluate_child_evidence"] = _ports._copy_json(
                    evaluate_children)
        except Exception as exc:
            return ({
                "error": "Evaluate child evidence preparation failed closed: "
                         f"{exc.__class__.__name__}: {exc}",
                "step": step, "status": _ports.status(ws),
            }, None, None)
    return (None, review_kernel, evaluate_children)


def _publish_phase_dispatch(_ports, act_ws, contract, dispatch, evaluate_children, phase_context, review_kernel, root_observation_authority, snapshot, state, step, task, worker_ref, ws):
    model_tier, model = dispatch["model_tier"], dispatch["model"]
    reasoning_effort, task_name = (dispatch["reasoning_effort"],
                                   dispatch["task_name"])
    if step in {"evaluate", "em"} and \
            _ports._validated_delivery_mode(state) is not None:
        run_id = str((review_kernel or {}).get("run_id") or "").strip()
        task_id = str((task or {}).get("id") or "engineering-signoff")
        if not run_id:
            raise _ports.producer_observation_policy.ProducerObservationError(
                f"{step} producer dispatch lacks ReviewKernel identity")
        dispatch_projection = {
            "run_id": run_id,
            "task_id": task_id,
            "stage": step,
            "producer": _ports.STEP_ROLE[step],
            "task_name": task_name,
            "role_marker": dispatch["role_marker"],
            "model": model,
            "reasoning_effort": reasoning_effort,
        }
        contract["producer_dispatch"] = {
            **dispatch_projection,
            "fingerprint": _ports.producer_observation_policy.content_fingerprint(
                dispatch_projection),
        }
    _ports.tp.trace(ws, "model_tier", step=step,
             task=(task or {}).get("id"), tier=model_tier, model=model,
             reasoning_effort=reasoning_effort)
    if dispatch.get("dispatch_route"):
        _ports.tp.trace(ws, "dispatch_route_resolved", step=step,
                 task=(task or {}).get("id"),
                 resolution=dispatch["dispatch_route"].get("resolution"),
                 capability_source=dispatch["dispatch_route"].get(
                     "capability_source"),
                 exact_route_verified=False)
    dispatch_wait_policy = _ports.event_wait_policy(
        f"{step}:{(task or {}).get('id') or step}", 1)
    result = {**dispatch, "step": step, "wait_policy": dispatch_wait_policy}
    if evaluate_children is not None:
        result["evidence_children"] = evaluate_children["child_dispatches"]
    try:
        stage_dispatch = _ports._stage_loop_dispatch(
            ws, state, slot=str((task or {}).get("id") or step),
            declared_scope=_ports._stage_loop_scope(
                contract["coding"]["scope_paths"],
                contract["coding"].get("out_of_scope_paths") or []))
        if stage_dispatch is None:
            raise ValueError("a phase worker requires an active stage")
        result["stage_runtime_dispatch"] = stage_dispatch
    except (ValueError, OSError) as exc:
        return {"error": "phase dispatch refused: " + str(exc), "step": step}
    dispatch_member = str((task or {}).get("id") or step)
    dispatch_intent = _ports._native_dispatch_intent(
        ws, state, step=step, task_id=dispatch_member,
        dispatch=dispatch, wait_policy=dispatch_wait_policy)
    intent_id = str(dispatch_intent.get("intent_id") or "")
    intent_identity = dispatch_intent.get("identity") or {}
    intent_run_id = str(intent_identity.get("run_id") or "") \
        if isinstance(intent_identity, _ports.Mapping) else ""
    if not intent_id or not intent_run_id:
        return {"error": "native dispatch intent has no exact run identity",
                "step": step, "status": _ports.status(ws)}
    try:
        root_admission = _ports._screen_public_native_route(
            ws, state, stage=step, tasks=[task or {"id": dispatch_member}],
            observation_authority=root_observation_authority,
            dispatch=_ports._native_delivery_dispatch_binding(
                state, stage=step, task=task or {"id": dispatch_member},
                intent_id=intent_id, native_task_name=str(task_name)))
    except (ValueError, _ports.dispatch_telemetry.DispatchTelemetryError) as exc:
        return {"error": "native root admission refused before dispatch: " +
                str(exc), "step": step, "status": _ports.status(ws)}
    contract["worker_lifecycle"]["dispatch_intent_id"] = intent_id
    contract["worker_lifecycle"]["dispatch_intent_run_id"] = intent_run_id
    _ports.tp.record_expected_dispatch(
        ws, "step", _ports.STEP_ROLE[step], model_tier, model,
        ref=dispatch_member, task_name=task_name,
        reasoning_effort=reasoning_effort,
        role_marker_value=dispatch["role_marker"],
        dispatch_route=dispatch.get("dispatch_route"),
        intent_id=intent_id, intent_run_id=intent_run_id)
    result["dispatch_intent"] = dispatch_intent
    if root_admission is not None:
        result["root_admission"] = root_admission
    if step in {"execute", "evaluate", "fix"}:
        result["wait_invocation"] = _ports.event_wait_invocation(
            dispatch_wait_policy, [dispatch_member])
    lifecycle = contract["worker_lifecycle"]
    release_action = _ports.tp.encode_worker_release_action(
        lifecycle["release_action"])
    result["contract_bootstrap"] = {
        "schema": "taskplane.worker-contract-bootstrap/v1",
        "task_slot": contract["task_slot"],
        "worker_identity": task_name,
        "environment": {"TASKPLANE_TASK": contract["task_slot"], "PWD": act_ws},
        "activation": "pending_subagent_start_binding",
        "control_plane_release": {
            "command": "worker-release",
            "signed_action": release_action,
            "terminal_receipt_required": True,
        },
    }
    try:
        phase_request = _ports._phase_bridge_prepare(act_ws, state, contract, dispatch)
        if phase_request is not None:
            contract["phase_runtime"] = phase_request
            result["phase_runtime"] = phase_request
        _ports.tp.activate(
            act_ws, contract, snapshot=snapshot,
            task_slot_override=contract["task_slot"])
        _ports.tp.release_superseded_pending_worker_contracts(
            act_ws, stage=step, task=worker_ref,
            keep_slot=contract["task_slot"])
    except Exception as exc:
        recovery_errors = []
        try:
            _ports.tp.cancel_expected_dispatch(
                ws, intent_id, reason="worker-contract-activation-failed")
        except Exception as recovery_exc:
            recovery_errors.append(
                "intent cancellation failed: "
                f"{recovery_exc.__class__.__name__}: {recovery_exc}")
        try:
            active = _ports.tp.worker_contract_for_stage(
                act_ws, stage=step, task=worker_ref)
            if isinstance(active, _ports.Mapping) and active.get("contract", {}).get(
                    "task_id") == contract.get("task_id"):
                active_contract = active["contract"]
                receipt = _ports.tp.record_worker_terminal(
                    act_ws, str(active["slot"]), event=None,
                    outcome="interruption",
                    submission_status="activation_failed",
                    authority="loop-gate")
                _ports.tp.release_worker_contract(
                    act_ws, str(active["slot"]),
                    action=active_contract["worker_lifecycle"][
                        "release_action"], terminal_receipt=receipt)
        except Exception as recovery_exc:
            recovery_errors.append(
                "slot quarantine failed: "
                f"{recovery_exc.__class__.__name__}: {recovery_exc}")
        return {"error": "worker contract activation failed closed: "
                         f"{exc.__class__.__name__}: {exc}",
                **({"recovery_errors": recovery_errors}
                   if recovery_errors else {}),
                "step": step, "status": _ports.status(ws)}
    return _ports.phase_harness.dispatch_action(_ports, ws, phase_context, result)


def _prepare_design_input(_ports, state, step, ws):
    design_decomposition = None
    design_lens_policy = None
    if step == "design" and not state.get("design_approved"):
        try:
            design_decomposition, design_lens_policy = \
                _ports._prepare_design_control_plane(ws, state)
        except Exception as exc:
            return ({
                "error": "mandatory Design decomposition failed closed: "
                         f"{exc.__class__.__name__}: {exc}",
                "step": step, "status": _ports.status(ws),
            }, None, None, None)
        if design_decomposition.get("status") != "ready":
            return ({
                "error": "mandatory Design decomposition is degraded",
                "step": step,
                "decomposition": design_decomposition,
                "status": _ports.status(ws),
            }, None, None, None)
        state = _ports.load(ws) or state
        # H3 (v2.2.1): until the design is human-approved, the graph
        # baseline follows the CURRENT scan — capturing once from a stale
        # graph and then blocking on "rescan" left the stored fingerprint
        # permanently mismatched (the engine's own remedy deadlocked the
        # step). A pre-approval rescan re-baselines, with a trace.
        current_fp = (_ports.depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
        if state.get("design_graph_fingerprint") != current_fp:
            # v2.3.0: persist the rebaseline under the state lock on a fresh
            # read — a bare save() here could clobber a concurrent update.
            with _ports.mutate(ws) as fresh:
                if fresh is not None and fresh.get("step") == "design" \
                        and fresh.get("design_graph_fingerprint") != current_fp:
                    if fresh.get("design_graph_fingerprint"):
                        _ports.tp.trace(
                            ws, "design_rebaseline",
                            old=(fresh["design_graph_fingerprint"] or "")[:12],
                            new=(current_fp or "")[:12])
                    fresh["design_graph_fingerprint"] = current_fp
                if fresh is not None:
                    state = fresh
    return (None, state, design_decomposition, design_lens_policy)
