"""Gates implementation; the caller supplies the composition root ports."""
from __future__ import annotations
from contextlib import contextmanager
from collections.abc import Mapping, Iterable
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import terminal_truth

def _engineering_review_errors(_ports,
        ws: str, state: dict | None = None, *,
        output_snapshot: Mapping[str, object] | None = None) -> list:
    """Validate the current EM review and its declared evidence coverage."""
    path = _ports.runtime_storage.review_public_path(ws, "findings.json")
    report_path = _ports.runtime_storage.review_public_path(ws, "report.md")
    captured_findings = None
    if output_snapshot is None:
        findings, errors = _ports._read_json(path)
        if errors:
            return errors
        try:
            with open(report_path, encoding="utf-8") as report_file:
                report_text = report_file.read()
            if not report_text.strip():
                errors.append("engineering narrative report is empty")
        except OSError:
            errors.append("engineering narrative report is missing: "
                          + report_path)
    else:
        exact = _ports.em_outage.output_snapshot_bytes(output_snapshot)
        errors = []
        try:
            findings = _ports.json.loads(exact["findings"].decode("utf-8"))
            if not isinstance(findings, dict):
                raise ValueError("findings must be an object")
        except (UnicodeDecodeError, ValueError, _ports.json.JSONDecodeError) as exc:
            return ["engineering findings result is invalid: " + str(exc)]
        try:
            report_text = exact["report"].decode("utf-8")
        except UnicodeDecodeError as exc:
            errors.append("engineering narrative report is invalid UTF-8: "
                          + str(exc))
            report_text = ""
        if not report_text.strip():
            errors.append("engineering narrative report is empty")
        # Router-audit can append deterministic rows to the public alias.
        # A candidate derived from a protected snapshot must never silently
        # accept those different bytes.  Fail this attempt and let the next
        # capture bind the now-current complete document.
        captured_findings = _ports.json.loads(_ports.json.dumps(findings))
    meta = findings.get("meta") or {}
    # Real EM/sign-off gates always pass loop state and therefore require the
    # canonical selective kernel. ``state=None`` is the long-standing pure
    # classification seam used by audit/finding unit tests; keeping it free
    # of repository orchestration lets those tests judge only the rule they
    # name without constructing a fake review transaction.
    if state is not None:
        try:
            import review as _review
            import review_evidence as _review_evidence
            binding = _ports.review_kernel_binding(
                state, "em", _ports._current_task(state))
            if not binding:
                raise RuntimeError("loop state has no bound EM review kernel")
            kernel_ws = str(binding.get("workspace") or ws)
            kernel = _review._load_state(kernel_ws, binding["run_id"])
            if kernel.get("status") != "complete" or kernel.get("stage") != "review":
                errors.append("engineering selective review kernel is incomplete")
            else:
                current = _review_evidence._read_current(
                    _review_evidence.ArtifactStore(kernel_ws))
                for key, value in (current or {}).items():
                    if meta.get(key) != value:
                        errors.append("engineering review contradicts canonical "
                                      f"revision identity: {key}")
        except Exception as exc:
            errors.append("engineering canonical revision is missing: "
                          f"{exc.__class__.__name__}: {exc}")
    if state:
        errors.extend(_ports._design_review_errors(ws, state, meta))
    coverage = meta.get("lens_coverage") or {}
    if not isinstance(coverage, dict):
        errors.append("engineering lens coverage must be an object")
        coverage = {}
    delivery = _ports._validated_delivery_mode(state) if state else None
    if state is not None and delivery is None:
        errors.append("loop Engineering requires its approved delivery-mode receipt")
    expected = (set() if state is not None else
        {entry["id"] for entry in _ports.lens_router.load_catalog().get("lenses") or []})
    if state is not None and coverage:
        errors.append("loop Engineering must consume direct evidence with zero lens workers")
    missing = sorted(expected - set(coverage))
    # Standalone classification uses structured verdicts; loop EM is zero-lens.
    valid_tiers = ("deep", "sweep", "light", "n/a", "deep (forced)")
    invalid = sorted(k for k, v in coverage.items()
                     if k in expected
                     and _ports._coverage_disposition(v) not in valid_tiers)
    if missing:
        errors.append("engineering review omitted lenses: " + ", ".join(missing))
    if invalid:
        errors.append("engineering review has invalid lens tiers: "
                      + ", ".join(invalid))
    # EM v3 tightening: a lens skipped as n/a must carry MACHINE-CHECKABLE
    # negative evidence (the v2 dict shape with negative_evidence). A bare
    # string "n/a" asserted the skip without evidence AND slipped past the
    # router-audit backstop (which only diffs dict-shaped decisions) — the
    # one disposition that reduces coverage was the one with no proof.
    bare_na = sorted(
        k for k, v in coverage.items()
        if k in expected and isinstance(v, str) and v.strip().lower() == "n/a")
    if bare_na:
        errors.append(
            "engineering review marks lenses n/a without negative evidence "
            "(use the v2 dict shape {verdict: 'n/a', negative_evidence: "
            "[...]}): " + ", ".join(bare_na))
    else:
        for k, v in sorted(coverage.items()):
            if (k in expected and isinstance(v, dict)
                    and str(v.get("verdict", "")).strip().lower() == "n/a"
                    and not v.get("negative_evidence")):
                errors.append(
                    "engineering review marks lens n/a with EMPTY "
                    "negative_evidence: " + k)
    impact_ev = meta.get("impact")
    if not isinstance(impact_ev, dict):
        errors.append("engineering review is missing dependency impact evidence")
    elif (state or {}).get("graph_governance"):
        required = {"touched", "impacted", "total_impacted", "unknown",
                    "depth_limit", "truncated", "policy", "graph"}
        missing_impact = sorted(required - set(impact_ev))
        if missing_impact:
            errors.append("engineering dependency impact evidence is incomplete: "
                          + ", ".join(missing_impact))
        changed = [f for f in _ports._diff_files(
            ws, _ports._review_baseline(ws, state or {}, "em") or "HEAD")
            if not f.startswith(_ports.lens_router.LOOP_OWNED)]
        if changed:
            review_policy = _ports._aggregate_impact_policy(
                (state or {}).get("tasks") or [])
            expected = _ports.depgraph.impact(ws, changed, policy=review_policy)
            if not impact_ev.get("touched"):
                errors.append("engineering dependency impact names no touched modules")
            elif not set(expected.get("touched") or []) <= \
                    set(impact_ev.get("touched") or []):
                errors.append("engineering dependency impact does not cover the diff")
            expected_fp = (expected.get("graph") or {}).get("content_fingerprint")
            actual_fp = (impact_ev.get("graph") or {}).get("content_fingerprint")
            if expected_fp and actual_fp != expected_fp:
                errors.append("engineering dependency impact uses a stale graph revision")
            if impact_ev.get("policy") != review_policy:
                errors.append("engineering dependency impact uses the wrong review policy")
    if not meta.get("tests"):
        errors.append("engineering review is missing test evidence")
    if state:
        for task in state.get("tasks") or []:
            errors.extend(f"task {task['id']}: {error}" for error in
                _ports._task_graph_evidence_errors(ws, state, task, meta))
    gate = meta.get("gate") or {}
    if gate.get("verdict") not in ("pass", "recommend-pass"):
        errors.append("engineering review does not recommend sign-off — "
                      'set meta.gate.verdict to "pass" or "recommend-pass" '
                      "in " + _ports.runtime_storage.review_public_path(
                          ws, "findings.json"))
    rows = findings.get("findings") or []
    if not isinstance(rows, list):
        errors.append("engineering findings must be a list")
        rows = []
    # v2.3.0 raw unresolved-high sweep (body in audit.py): unknown
    # severities normalize UP to high and BLOCK. Machinery warn rows are
    # exempt ONLY when re-derived as legitimate this run — the A5 shape
    # alone is a costume any findings author can wear.
    errors.extend(_ports._unresolved_high_errors(meta, rows))
    # R-0013: commentary may not block this gate (body in audit.py).
    errors.extend(_ports._blocking_claim_errors(ws, state, rows))
    # Audit sweep (v3 Phase 1): when the review recorded a routing decision,
    # diff the findings against it — n/a-lens findings are auto-filed as
    # router regressions and block sign-off via the frozen finding_blocks
    # rule (no guardrail change).
    errors.extend(_ports._router_audit_gate(ws, path, findings, meta, rows))
    if captured_findings is not None and findings != captured_findings:
        errors.append(
            "engineering review changed during immutable output validation; "
            "retry from a new captured snapshot")
    return errors


def _run_submit_checkpoint(_ports, ws: str, state: Mapping[str, object],
                           task: Mapping[str, object], act_ws: str) -> dict:
    """Run one task-declared AC checkpoint through the incumbent runtime.

    The Plan declares stable checkpoint inputs.  Submit owns the mutable
    repository identity and scope, while ``checkpoint`` remains the sole
    preflight and receipt authority.  This keeps command lifecycle behavior
    on the existing governed launch/wait path and prevents a task-authored
    mapping from masquerading as proof.
    """
    declaration = task.get("checkpoint")
    if not isinstance(declaration, _ports.Mapping):
        raise _ports.checkpoint.CheckpointSpecError(
            f"task {task.get('id') or '?'} checkpoint declaration must be "
            "a mapping")
    reserved = sorted(set(declaration) & {
        "schema", "worktree_revision", "declared_scope", "receipt",
        "producer", "result",
    })
    if reserved:
        raise _ports.checkpoint.CheckpointSpecError(
            "checkpoint declaration contains engine-owned fields: " +
            ", ".join(reserved))
    spec = {
        **dict(declaration),
        "schema": _ports.checkpoint.CHECKPOINT_SCHEMA,
        "worktree_revision": _ports.tp.git_head(act_ws),
        "declared_scope": list(task.get("scope") or []),
    }
    validated = _ports.checkpoint.validate_checkpoint_spec(act_ws, spec)
    checkpoint_id = validated["checkpoint_id"]
    authorization = "loop-submit-checkpoint:" + str(task.get("id") or "task")
    run_id = str(state["run_id"])
    try:
        checkpoint_authority = \
            _ports.governed_commands.mint_semantic_checkpoint_authorization(
                act_ws, lifecycle_authorization=authorization,
                run_id=run_id, task_id=str(task.get("id") or "task"))
    except _ports.governed_commands.GovernedCommandError as exc:
        raise _ports.checkpoint.CheckpointReceiptError(
            f"checkpoint {checkpoint_id} authorization refused: {exc}") \
            from exc
    # This semantic action accepts no argv/cwd/env/executable from the worker.
    # The governed-command engine reloads the current Plan task, derives the
    # exact validated checkpoint, and executes it outside the reviewed source.
    launched = _ports.governed_commands.execute(act_ws, "checkpoint", {
        "authorization": authorization,
        "checkpoint_authority": checkpoint_authority,
        "run_id": run_id,
        "task_id": str(task.get("id") or "task"),
    })
    if launched.get("error"):
        raise _ports.checkpoint.CheckpointReceiptError(
            f"checkpoint {checkpoint_id} runtime launch failed: " +
            str(launched["error"]))
    observed = _ports.governed_commands.execute(act_ws, "wait", {
        "authorization": authorization,
        "handle": launched["handle"],
        "consumer": "checkpoint:" + checkpoint_id,
    })
    if observed.get("error"):
        raise _ports.checkpoint.CheckpointReceiptError(
            f"checkpoint {checkpoint_id} runtime wait failed: " +
            str(observed["error"]))
    if (observed.get("snapshot") or {}).get("state") != "succeeded":
        # Preserve the checkpoint engine's typed red/timed-out/cancelled
        # verdict.  A green boundary receipt is intentionally unavailable for
        # a failed proof, but failure still needs the canonical checkpoint
        # diagnostic rather than a generic sidecar error.
        return _ports.checkpoint.validate_and_mint(act_ws, spec, observed)
    receipt = _ports.checkpoint.validate_and_mint(
        act_ws, spec, observed,
        semantic_authorization=authorization)
    return receipt


def _task_submission_authority(_ports,
        workspace: str, stage: str, task: Mapping[str, object]) -> dict:
    """Project exact task-owned active contract evidence for one submission."""
    binding = _ports._worker_stage_binding(workspace, stage, task)
    if not isinstance(binding, _ports.Mapping):
        raise ValueError("exact task worker contract is missing")
    contract = binding.get("contract") or {}
    lifecycle = contract.get("worker_lifecycle") or {}
    expected_scope = list(task.get("scope") or [])
    actual_scope = list((contract.get("coding") or {}).get(
        "scope_paths") or [])
    if lifecycle.get("stage") != stage or \
            lifecycle.get("task") != str(task.get("id") or "") or \
            lifecycle.get("slot") != binding.get("slot") or \
            actual_scope != expected_scope or \
            lifecycle.get("status") not in {"active", "terminal"}:
        raise ValueError("task worker contract does not match this submission")
    material = {
        "schema": "taskplane.task-submission-authority/v1",
        "stage": stage,
        "task": str(task.get("id") or ""),
        "task_slot": str(binding.get("slot") or ""),
        "contract_id": str(contract.get("task_id") or ""),
        "expected_task_name": str(
            lifecycle.get("expected_task_name") or ""),
        "scope": actual_scope,
        "workspace_fingerprint": _ports.hashlib.sha256(
            _ports.os.path.realpath(workspace).encode("utf-8")).hexdigest(),
    }
    return {**material, "fingerprint": _ports.hashlib.sha256(_ports.json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")).hexdigest()}


def _task_submission_authority_error(_ports,
        workspace: str, submission: Mapping[str, object], stage: str,
        task: Mapping[str, object]) -> str | None:
    authority = submission.get("task_authority")
    if not isinstance(authority, _ports.Mapping):
        return "submission has no exact task-owned contract authority"
    material = {str(key): value for key, value in authority.items()
                if key != "fingerprint"}
    expected_fingerprint = _ports.hashlib.sha256(_ports.json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")).hexdigest()
    if authority.get("fingerprint") != expected_fingerprint:
        return "submission task-owned contract authority is stale"
    expected = {
        "stage": stage,
        "task": str(task.get("id") or ""),
        "scope": list(task.get("scope") or []),
        "workspace_fingerprint": _ports.hashlib.sha256(
            _ports.os.path.realpath(workspace).encode("utf-8")).hexdigest(),
    }
    if any(authority.get(key) != value for key, value in expected.items()):
        return "submission task-owned contract authority is foreign"
    if authority.get("task_slot") != authority.get("contract_id"):
        return "submission task slot and contract identity are severed"
    return None


def submit(_ports, ws: str, outcome: str, note: str = "",
           task_id: str | None = None) -> dict:
    """Worker submission — evidence request, never a state transition.

    Trust boundary (L12, v2.2.1): "orchestrator-only gating" is a PROTOCOL
    guarantee, not a process-isolation one — any process with workspace
    access can call gate(). What holds mechanically is the EVIDENCE: a gate
    only advances when the fingerprinted submission matches the bytes on
    disk, so a worker gating itself still cannot pass unproven work. Gate
    calls are traced for after-the-fact attribution.

    The engine, not the worker, computes the changed paths and fingerprint.
    The orchestrator subsequently calls ``gate``; if anything changed between
    submission and validation, the gate rejects the stale evidence.  Repeating
    the same submission is idempotent, which makes interrupted/resumed drivers
    safe.

    A4 (decision 0018): the record additionally carries ``engine_fingerprint``
    — the identity of the ENGINE BUILD that produced it (tp.engine_fingerprint
    over the validator surface). Purely additive: older gates ignore the
    unknown key, and the evaluate gate uses it to refuse evidence produced by
    a different build than the one validating it.
    """
    if refusal := _ports._run_schema_refusal(ws):
        return refusal
    state = _ports.load(ws)
    if state is None:
        return {"error": "no active loop"}
    state = _ports.stage_loop.task_phase_state(_ports, ws, state, task_id)
    step = state.get("step")
    if step not in _ports.WORKER_SUBMISSION_STEPS:
        return {"error": f"step '{step}' is not a worker submission step — "
                         "run `loop next` to see the current role and "
                         "instruction; submissions happen at design/execute/fix/"
                         "evaluate/em"}
    if outcome not in ("pass", "fail", "unavailable"):
        return {"error": "submission outcome must be pass, fail, or unavailable"}
    if outcome == "unavailable" and step != "evaluate":
        return {"error": "unavailable is only valid for model evaluation; "
                         "product execution/fix/review still submit pass or fail"}

    task = _ports._current_task(state)
    act_ws = ws
    parallel_execute = step in {"execute", "fix", "evaluate"} and state.get("parallel")
    if parallel_execute and task_id is None:
        task_id = (_ports.runtime_storage.load_workspace_locator(ws) or {}).get("task_id")
    if not parallel_execute and task_id and \
            task_id != (task or {}).get("id"):
        # H1 (v2.2.1): outside a parallel EXECUTE wave the engine evaluates
        # ONE current task — silently dropping a mismatched --task would
        # record this worker's evidence against a different task.
        return {"error": f"--task {task_id} does not match the current "
                         f"task '{(task or {}).get('id')}' at step "
                         f"'{step}' — a wave worker submits only during "
                         "parallel EXECUTE; otherwise omit --task or "
                         "pass the current task's id"}
    if parallel_execute:
        task = next((x for x in state.get("tasks") or []
                     if x.get("id") == task_id), None)
        if task is None:
            return {"error": "parallel submit needs --task <id> of a wave member"}
        act_ws = task.get("workspace") or ws
    elif step in ("evaluate", "fix") and state.get("parallel"):
        tws = (task or {}).get("workspace")
        act_ws = tws if tws and _ports.os.path.isdir(tws) else ws

    runtime_guidance = None
    checkpoint_receipt = None
    # A worker submits bytes before its native Stop can attest completion.
    # Evaluate/EM receipts are consumed by Stop, then checked by the gate.
    if outcome == "pass" and step not in {"evaluate", "em"}:
        runtime_guidance = _ports.runtime_eval.guide_loop(ws, task_id=task_id)
        if runtime_guidance.get("error"):
            return {"error": "runtime eval checkpoint failed: "
                             + runtime_guidance["error"],
                    "submitted": False, "transitioned": False,
                    "runtime_eval": runtime_guidance}
        if runtime_guidance.get("status") != "on_path":
            status = runtime_guidance.get("status")
            return {
                "error": ("runtime eval detected recoverable execution drift; "
                          "apply the supplied correction before pass submission"
                          if status == "correct" else
                          "runtime eval blocked repeated unresolved execution "
                          "drift; submit fail or return to the orchestrator"),
                "submitted": False, "transitioned": False,
                "runtime_eval": runtime_guidance,
            }
        if step in ("execute", "fix") and isinstance(
                (task or {}).get("checkpoint"), _ports.Mapping):
            try:
                checkpoint_receipt = _ports._run_submit_checkpoint(
                    ws, state, task, act_ws)
            except _ports.checkpoint.CheckpointSpecError as exc:
                return {
                    "error": f"AC checkpoint refused: {exc}",
                    "submitted": False, "transitioned": False,
                    "runtime_eval": runtime_guidance,
                }

    snapshot = _ports._worker_stage_snapshot(act_ws, step, task)
    evidence_paths = _ports.runtime_storage.submission_evidence_paths(act_ws, step)
    if step == "design":
        # Design outputs can be ignored by Git. Bind only this stage's named
        # evidence, including a required visual, without reading old lens files.
        design, _ = _ports._dc.design_contract(act_ws)
        design = dict(design) if isinstance(design, dict) else {}
        if not isinstance(design.get("visualization"), dict):
            design["visualization"] = {}
        evidence_paths = _ports._dc.design_evidence_paths(act_ws, design)
        evidence_paths.append("design/test-strategy.json")
    graph_fingerprint = None
    if state.get("graph_governance") and \
            (step in {"design", "em"} or step == "evaluate" and not state.get("parallel")):
        graph_fingerprint = (_ports.depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
    evidence_engine_ws = _ports._submission_evidence_engine_workspace(
        ws, state, task, act_ws)
    task_authority = None
    if step in {"execute", "fix"} and _ports._task_submission_authority_required(task):
        try:
            task_authority = _ports._task_submission_authority(
                act_ws, step, task)
        except Exception as exc:
            return {
                "error": "task-owned submission authority failed closed: "
                         f"{exc.__class__.__name__}: {exc}",
                "submitted": False, "transitioned": False,
            }
    submission = {
        "step": step,
        "task": ((task or {}).get("id") or ("engineering-signoff"
            if step == "em" and _ports._phase_bridge_context(ws, state) is not None else None)),
        "outcome": outcome,
        "note": note,
        "workspace": act_ws,
        "snapshot": snapshot,
        "fingerprint": _ports.tp.workspace_fingerprint(
            act_ws, snapshot, extra_paths=evidence_paths),
        "changed_files": (_ports.tp.changed_files(act_ws, snapshot)
                          if snapshot else []),
        "evidence_paths": evidence_paths,
        "graph_fingerprint": graph_fingerprint,
        "engine_fingerprint": _ports.tp.engine_fingerprint(),
        # A4 REPAIR (EM, v3 phase 3): engine_fingerprint attests the process
        # RUNNING submit — the same installed plugin the gate uses, so it
        # could never fire. Stamp the engine in the workspace the EVIDENCE
        # came from; None where that workspace carries no engine copy.
        "evidence_engine_fingerprint":
            _ports.tp.workspace_engine_fingerprint(evidence_engine_ws),
        "submitted_at": int(_ports.time.time()),
        **({"task_authority": task_authority}
           if task_authority is not None else {}),
    }
    if checkpoint_receipt is not None:
        submission["checkpoint_receipt"] = checkpoint_receipt
    with _ports.mutate(ws) as locked:
        if locked is None:
            return {"error": "no active loop"}
        def _same(existing):
            # Both engine fingerprints are part of the identity: a
            # re-submission under a different running engine OR after the
            # exact task tip moves under the primary evidence producer must
            # replace stale metadata, not be deduplicated into it (A4's
            # merge-and-resubmit remedy).
            return existing and all(
                existing.get(k) == submission.get(k)
                for k in ("step", "task", "outcome", "fingerprint",
                          "engine_fingerprint",
                          "evidence_engine_fingerprint"))
        if parallel_execute:
            target = next((x for x in locked.get("tasks") or []
                           if x.get("id") == task_id), None)
            if target is None:
                return {"error": f"no task {task_id}"}
            if _same(target.get("_submission")):
                submission = target["_submission"]
            else:
                target["_submission"] = submission
        else:
            if _same(locked.get("_submission")):
                submission = locked["_submission"]
            else:
                locked["_submission"] = submission
    telemetry_finalization = None
    if step == "execute" and submission.get("task"):
        telemetry_finalization = {
            "status": "pending-host-lifecycle",
            "reason": "SubagentStop owns exact native usage terminalization",
        }
    _ports.tp.trace(ws, "loop_submit", step=step, task=submission.get("task"),
             outcome=outcome, fingerprint=submission["fingerprint"][:12])
    return {"submitted": True, "transitioned": False,
            **({"runtime_eval": runtime_guidance}
               if runtime_guidance is not None else {}),
            **({"dispatch_telemetry": telemetry_finalization}
               if telemetry_finalization is not None else {}),
            "submission": submission,
            "next": "orchestrator: run loop gate with the submitted outcome"}


def _submission_staleness(_ports, ws: str, submission: dict) -> str | None:
    """Recompute the engine-owned attestations for a pending submission."""
    sub_ws = submission.get("workspace") or ws
    current_fp = _ports.tp.workspace_fingerprint(
        sub_ws, submission.get("snapshot"),
        extra_paths=submission.get("evidence_paths") or [])
    if current_fp != submission.get("fingerprint"):
        return "workspace or evidence changed after worker submission"
    graph_fp = submission.get("graph_fingerprint")
    if graph_fp:
        current_graph_fp = (_ports.depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
        if current_graph_fp != graph_fp:
            return "dependency graph changed after worker submission"
    return None



def collect_submission_observation(_ports, ws: str, *, slot: str) -> dict:
    """Bind this worker's submitted bytes to its authenticated native Stop."""
    contract = _ports.tp.load_json(_ports.tp.active_contract_path(ws, slot),
        default=None, what="stopping producer contract")
    if contract is None:
        contract = _ports.tp.released_worker_contract(ws, slot)
    binding = contract.get("submission_contract") or {}
    task_id = binding.get("task")
    selected_task_id = task_id if binding.get("stage") == "evaluate" else None
    state = _ports.stage_loop.task_phase_state(_ports, ws, _ports.load(ws), selected_task_id)
    task = _ports._current_task(state or {}) or {}
    submission = (state or {}).get("_submission") or {}
    step = (state or {}).get("step")
    expected = str(task.get("id") or "engineering-signoff")
    if (step not in {"evaluate", "em"} or binding.get("stage") != step
            or task_id != expected or submission.get("step") != step
            or submission.get("task") != expected or submission.get("outcome") not in {"pass", "fail", "unavailable"}):
        raise ValueError("native producer observation lacks its exact current submission")
    lifecycle = contract.get("worker_lifecycle") or {}
    if contract.get("task_slot") != slot or not isinstance(lifecycle.get("owner"), dict):
        raise ValueError("submission no longer owns its exact worker")
    if _ports._submission_staleness(ws, submission):
        raise ValueError("submission changed before native observation consumption")
    material = _ports.producer_output_identity(ws, state, task, step, active_contract=contract)
    receipt = submission.get("producer_observation")
    if receipt is None:
        receipt = _ports.producer_observation_policy.consume_matching_observation(reconcile=True, **material)
    _ports.producer_observation_policy.validate_consumed_matching_observation(receipt, **material)
    identity = _ports.producer_observation_policy._decode_stopping_identity(
        receipt["host_session_or_turn"], material["producer_dispatch"])
    if lifecycle["owner"] != {key: identity[key] for key in ("agent_id", "session_id", "task_name")}:
        raise ValueError("native producer observation belongs to another worker owner")
    with _gate_state(_ports, ws, selected_task_id) as current:
        pending = (current or {}).get("_submission") or {}
        if current.get("step") != step or any(pending.get(key) != submission.get(key)
                for key in ("task", "fingerprint", "outcome", "workspace")) or _ports._submission_staleness(ws, pending):
            raise ValueError("submission changed during native observation consumption")
        pending["producer_observation"] = _ports._copy_json(receipt)
        pending["producer_worker_slot"] = slot
    # Review collection may journal telemetry in the run aggregate; do not
    # hold its manifest lock across that independent effect owner.
    if submission["outcome"] == "pass":
        _ports._collect_zero_lens_evaluate_before_guidance(ws, ws, _ports.load(ws), task, step=step)
    return _ports._copy_json(receipt)


def _producer_observation_errors(_ports,
        act_ws: str, state: dict, task: dict | None, step: str,
        submission: Mapping[str, object] | None, *, clock=None) -> list[str]:
    """Re-attest the durable, consumed native receipt at the final gate."""
    if _ports._validated_delivery_mode(state) is None or step not in {"evaluate", "em"}:
        return []
    try:
        contract = _ports._worker_stage_contract(act_ws, step, task)
        slot = (submission or {}).get("producer_worker_slot")
        if slot is not None:
            if contract and contract.get("task_slot") != slot:
                raise ValueError("producer slot was replaced")
            if not contract:
                contract = _ports.tp.released_worker_contract(act_ws, slot)
            lifecycle = contract.get("worker_lifecycle") or {}
            if lifecycle.get("stage") != step or lifecycle.get("task") != str((task or {}).get("id") or step):
                raise ValueError("terminal producer contract belongs to another task")
        material = _ports.producer_output_identity(
            act_ws, state, task, step,
            active_contract=contract)
        _ports.producer_observation_policy.validate_consumed_matching_observation(
            (submission or {}).get("producer_observation"), **material,
            clock=clock)
        if slot is not None:
            identity = _ports.producer_observation_policy._decode_stopping_identity(
                submission["producer_observation"]["host_session_or_turn"], material["producer_dispatch"])
            if lifecycle.get("owner") != {key:identity[key] for key in ("agent_id", "session_id", "task_name")}:
                raise ValueError("terminal producer observation belongs to another worker owner")
    except Exception as exc:
        return ["producer observation validation failed: "
                f"{exc.__class__.__name__}: {exc}"]
    return []


@contextmanager
def _gate_state(_ports, ws, task_id):
    """Commit a task's phase outcome without moving a sibling's cursor."""
    with _ports.mutate(ws) as locked:
        if locked is None or not locked.get("parallel") or task_id is None:
            yield locked
            return
        view = _ports.stage_loop.task_phase_state(_ports, ws, locked, task_id)
        before = _ports._copy_json(view)
        yield view
        if view == before:
            return
        task = next(row for row in view["tasks"] if row["id"] == task_id)
        if view["step"] != before["step"]:
            task["phase_step"] = "done" if task.get("status") == "passed" else view["step"]
        if "_submission" not in view:
            task.pop("_submission", None)
        if "evaluate_child_evidence" in view:
            task["evaluate_child_evidence"] = view.pop("evaluate_child_evidence")
        else:
            task.pop("evaluate_child_evidence", None)
        cursor, coordinator_step = locked.get("current_task", 0), locked["step"]
        if view["step"] in {"em", "selection"}:
            coordinator_step = view["step"]
        locked.clear()
        locked.update(view)
        locked["current_task"], locked["step"] = cursor, coordinator_step
        locked.pop("_submission", None)


def gate(_ports, ws: str, outcome: str, note: str = "", task_id: str | None = None,
         rid: str | None = None) -> dict:
    """Record the current step's outcome, transition, and clear its contract."""
    if refusal := _ports._run_schema_refusal(ws):
        return refusal
    state = _ports.load(ws)
    if state is None:
        return {"error": "no active loop"}
    state = _ports.stage_loop.task_phase_state(_ports, ws, state, task_id)
    phase_workspace = ((_ports._current_task(state) or {}).get("workspace") or ws
                       if state.get("parallel") and task_id else ws)
    try:
        _ports._phase_bridge_gate_check(phase_workspace, state)
        if outcome == "pass":
            lens_refusal = _ports.phase_harness.lens_gate(_ports, phase_workspace, state)
            if lens_refusal:
                return {**lens_refusal, "step": state.get("step")}
    except (ValueError, OSError) as exc:
        return {"error": "phase runtime gate refused: " + str(exc), "step": state.get("step")}
    # v2.3.0 wiring: `--req R-xxxx` attaches a requirement to the in-flight
    # loop through the SANCTIONED validator (design_contract.design_attach_
    # requirement) — it validates exactly what the design DoR demands and
    # refuses to swap an anchored requirement; nothing downstream is skipped.
    if rid:
        attach_errors: list = []
        with _ports.mutate(ws) as st:
            if st is None:
                return {"error": "no active loop"}
            attach_errors = _ports._dc.design_attach_requirement(ws, st, rid)
        if attach_errors:
            return {"error": "requirement attach failed — the gate was not "
                             "evaluated", "blockers": attach_errors}
        state = _ports.load(ws)
    step = state["step"]
    # Preserve the same stage/task identity that `next_action` used before
    # gate validation mutates state (the Plan gate loads tasks, for example).
    # Recomputing after that load changes `plan` into the first task id and
    # strands the planner's exact worker slot.
    gate_worker_task = str((_ports._current_task(state) or {}).get("id") or step)
    submission = None
    reanchor_receipt = None

    # v2.3.0: validate --task FIRST in a parallel wave. An unknown id used to
    # fall through to "worker evidence was not submitted", telling the driver
    # to submit for a task that does not exist (mirrors H1's submit-side
    # validation).
    if step == "execute" and state.get("parallel"):
        members = [str(x.get("id")) for x in state.get("tasks") or []]
        if not task_id:
            return {"error": "parallel gate needs --task <id> of a wave "
                             "member", "step": step}
        if task_id not in members:
            return {"error": f"unknown task id '{task_id}' — wave members: "
                             + ", ".join(members), "step": step}

    if state.get("submission_required") and step in _ports.WORKER_SUBMISSION_STEPS:
        task_for_submission = (_ports._current_task(state) if step != "execute"
                               or not state.get("parallel") else
                               next((x for x in state.get("tasks") or []
                                     if x.get("id") == task_id), None))
        submission = ((task_for_submission or {}).get("_submission")
                      if step in {"execute", "fix", "evaluate"} and state.get("parallel") else
                      state.get("_submission"))
        if not submission:
            return {"error": "worker evidence was not submitted — the worker "
                             "must run `loop submit pass|fail|unavailable`; only the "
                             "orchestrator may evaluate `loop gate`",
                    "step": step}
        if submission.get("step") != step or submission.get("outcome") != outcome:
            return {"error": "gate request does not match the worker submission",
                    "step": step, "submission": submission}
        if step in {"execute", "fix"} and \
                _ports._task_submission_authority_required(task_for_submission):
            authority_error = _ports._task_submission_authority_error(
                str(submission.get("workspace") or ws), submission, step,
                task_for_submission)
            if authority_error:
                return {"error": authority_error, "step": step}
        stale = _ports._submission_staleness(ws, submission)
        if stale:
            return {"error": stale + " — discard stale evidence and submit again",
                    "step": step}

    # Parallel EXECUTE: a wave worker reports its own task's build outcome.
    # Concurrent workers gate against the same aggregate — serialize the whole
    # read-modify-write under an exclusive lock so a second worker's save
    # can't clobber the first's status update (which would revert a gated task
    # to running and stall the wave).
    if step == "execute" and state.get("parallel"):
        return _gate_parallel_build(_ports, note, outcome, state, step, submission, task_id, ws)

    # H4 (v2.2.1): the pm gate was the one fail-open step — it advanced with
    # no spec and no submission. Symmetric minimal DoD: the authored
    # requirement must exist before the loop leaves Define.
    if step == "pm":
        refusal = _validate_product_gate(_ports, note, outcome, state, step, ws)
        if refusal is not None:
            return refusal

    # Validate the proposed HOW while its read-only contract is active. The
    # designer cannot self-certify or mutate the as-built graph; a complete
    # contract advances only to the human approval gate.
    if step == "design":
        refusal = _validate_design_gate(_ports, note, outcome, state, step, ws)
        if refusal is not None:
            return refusal

    # Validate the implementation-ready plan while its read-only contract is
    # still active. A rejected plan remains governed for the planner's retry.
    if step == "plan":
        _ports._load_tasks(ws, state)
        if outcome != "pass":
            _ports.tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "plan rejected — staying at plan")
            return {"error": "plan gate: outcome was not 'pass' — the plan "
                             "was rejected. Revise plan/tasks.json (+ "
                             "plan/plan.md) and gate again; the loop stays at "
                             "the plan step.",
                    "step": "plan", "status": _ports.status(ws)}
        if not state.get("tasks"):
            _ports.tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note="phantom plan: plan/tasks.json missing or empty")
            return {"error": "plan gate: plan/tasks.json is missing or has "
                             "no tasks — the plan exists only as words. "
                             "Write plan/tasks.json (+ plan/plan.md for the "
                             "human), then gate again."}
        dor_errors = _ports._plan_dor_errors(ws, state, apply=True)
        if dor_errors:
            _ports.tp.trace(ws, "loop_gate_blocked", step=step, reason="dor",
                     errors=dor_errors)
            return {"error": "Definition of Ready failed — revise "
                             "plan/tasks.json before approval or execution",
                    "step": "plan",
                    "dor": {"ready": False, "blockers": dor_errors}}
        reanchor_receipt, reanchor_errors = _ports._reanchor_replanned_tasks(
            ws, state)
        if reanchor_errors:
            _ports.tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="replan_reanchor_ambiguous",
                     errors=reanchor_errors)
            return {"error": "replan reanchor failed closed — repair the "
                             "append-only replan evidence before Plan "
                             "acceptance",
                    "step": "plan",
                    "dor": {"ready": False,
                            "blockers": reanchor_errors}}
        # Validate task ids here too: checkpoint-less loops skip approve.
        if (refusal := _ports.tp.plan_task_id_refusal(ws, state.get("tasks"),
                                               "gate")):
            return refusal

    task = _ports._current_task(state)
    gated_task_id = str((task or {}).get("id") or "")
    act_ws = ws
    if step in ("evaluate", "fix") and state.get("parallel"):
        tws = (task or {}).get("workspace")
        act_ws = tws if tws and _ports.os.path.isdir(tws) else ws

    refusal, unavailable_verdict, evaluation_progress, failure_verdict, failure_decision, signoff_evidence, em_request_changes = _collect_gate_evidence(_ports, act_ws, outcome, state, step, submission, task, ws)
    if refusal is not None:
        return refusal

    # H2 (v2.2.1): validation above ran on a snapshot and can take seconds
    # (tests, evidence, graph). Apply the transition under the state LOCK to
    # a FRESH read, so a wave worker's concurrent update to another task is
    # never clobbered by saving this stale snapshot wholesale. Fields the
    # VALIDATION itself computed on the snapshot (loaded plan tasks, graph
    # DoR) are carried over explicitly.
    _validated = state
    stage_transition = None
    refusal, state, stage_transition = _commit_gate_transition(_ports, _validated, act_ws, em_request_changes, evaluation_progress, failure_decision, failure_verdict, note, outcome, signoff_evidence, step, submission, task_id, unavailable_verdict, ws)
    if refusal is not None:
        return refusal
    return _finish_gate(_ports, state, _validated, act_ws, gate_worker_task, gated_task_id, note, outcome, reanchor_receipt, stage_transition, step, task, task_id, unavailable_verdict, ws)


def _compute_signoff_dod(_ports,
        ws: str, state: dict, *,
        output_snapshot: Mapping[str, object] | None = None) -> dict:
    """Mechanical final DoD over aggregate scope, requirements, tests, graph,
    engineering evidence, and committed knowledge. Human sign-off remains."""
    scopes: list = []
    for t in (state.get("tasks") or []):
        scopes.extend(t.get("scope") or [])
    baseline = state.get("baseline")
    errors: list = []
    notices: list = []
    errors.extend("requirement DoD: " + e for e in _ports.tp.requirement_coverage_errors(
        state.get("tasks") or [],
        lambda rid: _ports.reqs.get_requirement(ws, rid),
        state.get("requirement_id"), require_passed=True))
    if scopes:
        # Aggregate diff-scope, EXCLUDING loop-owned artifacts: they are
        # authored by governed steps under their own write-allow contracts
        # and human gates, so requiring them inside the union of TASK
        # scopes was a contradiction. Every other engine path (evaluate
        # routing, em review, impact, and — A2 — the per-task DoD) filters
        # lens_router.LOOP_OWNED the same way. Fail-closed stance
        # unchanged: no snapshot still errors, and NON-loop-owned files
        # outside the union still block.
        if not baseline:
            errors.append("diff_scope: cannot verify — no git snapshot "
                          "(commit the workspace before governing)")
        else:
            # plan_minted: the union IS the human-approved plan's scopes
            # (approved wildcard-free literals keep their provenance-gated
            # override); DEFAULT_OUT_OF_SCOPE here is STRICTER than the
            # old synthetic contract, which had no out_of_scope at all.
            coding = {"scope_paths": scopes,
                      "out_of_scope_paths": list(_ports.tp.DEFAULT_OUT_OF_SCOPE),
                      "plan_minted": True}
            for f in _ports.tp.changed_files(ws, baseline):
                if f.startswith(_ports.lens_router.LOOP_OWNED):
                    continue
                v = _ports.tp.scope_violation(f, coding)
                if v:
                    errors.append("diff_scope: " + v)
            if errors:
                errors.append(
                    "diff_scope recovery: revert the out-of-scope files or "
                    "widen the owning task's scope via the human gate "
                    "(attributable: trace + KB decision), then re-run")
    if state.get("graph_governance"):
        try:
            _ports.depgraph.scan(ws)
        except Exception as exc:
            errors.append(f"graph_dod: final merged-tree scan failed: {exc}")
    for task in state.get("tasks") or []:
        test_command = task.get("tests")
        if not test_command:
            errors.append(f"task {task.get('id', '?')}: test command missing")
            continue
        test_contract = _ports.tp.build_contract(
            f"SIGNOFF TEST: {task.get('id', '?')}",
            scope=task.get("scope"), test_command=test_command,
            plan_minted=True, regression_gate=True,
            test_timeout_seconds=_ports.tp.task_test_timeout_seconds(task))
        # Aggregate scope is already checked; run each task's scoped evidence.
        test_contract["coding"]["dod"]["require_clean_scope_diff"] = False
        regression_files = [f for f in (_ports.tp.changed_files(ws, baseline)
                                        if baseline else [])
                     if _ports.tp.match_any(f, task.get("scope") or [])]
        task_notices: list = []
        errors.extend(f"task {task.get('id', '?')}: {e}" for e in _ports.tp.dod_check(
            test_contract, ws, baseline, regression_files=regression_files,
            notices=task_notices))
        notices.extend(f"task {task.get('id', '?')}: {n}"
                       for n in task_notices)
    errors.extend(_ports._engineering_review_errors(
        ws, state, output_snapshot=output_snapshot))
    for problem in _ports.kb.lint(ws):
        errors.append("kb_lint: " + (problem.get("file", "?")) + " — "
                      + problem.get("problem", ""))
    # D-0008: sign-off is the human's decision point. A `tests_pass` that was
    # CITED rather than executed is a fact about the evidence being signed
    # for, so it travels with the verdict instead of only into the trace.
    return {"passed": not errors, "errors": errors, "notices": notices,
            "scope": scopes, "baseline": baseline}


def _signoff_evidence_binding(_ports,
        ws: str, state: dict, *,
        output_snapshot: Mapping[str, object] | None = None
        ) -> tuple[dict | None, list]:
    """Seal final DoD and review identity at the integrated revision.

    Sign-off is a human decision over the EM-reviewed tree, not a fresh review
    of whichever bytes happen to occupy the shared checkout later.  The EM
    gate therefore computes the terminal mechanical verdict once and carries
    its run-owned review identity into the human gate.
    """
    revision = _ports.tp.git_head(ws)
    errors = []
    if not revision:
        errors.append("sign-off evidence has no integrated git revision")
    elif any(not path.startswith(_ports.lens_router.LOOP_OWNED)
            for path in _ports.tp.changed_files(ws, revision)):
        errors.append("sign-off evidence cannot bind an uncommitted product "
                      "diff; commit the reviewed integration tree first")
    if errors:
        return None, errors
    dod = _ports._compute_signoff_dod(
        ws, state, output_snapshot=output_snapshot)
    if not dod["passed"]:
        return None, list(dod["errors"])
    binding = _ports.review_kernel_binding(state, "em", _ports._current_task(state)) or {}
    if output_snapshot is None:
        findings, _ = _ports._read_json(
            _ports.runtime_storage.review_public_path(ws, "findings.json"))
    else:
        exact = _ports.em_outage.output_snapshot_bytes(output_snapshot)
        try:
            findings = _ports.json.loads(exact["findings"].decode("utf-8"))
        except (UnicodeDecodeError, ValueError, _ports.json.JSONDecodeError):
            return None, ["engineering findings result is invalid"]
    meta = (findings or {}).get("meta") or {}
    evidence = {
        "schema": "taskplane.signoff-evidence/v1",
        "integration_revision": revision,
        "requirement_id": state.get("requirement_id"),
        "baseline": state.get("baseline"),
        "review_kernel": dict(binding),
        "review_revision": {
            key: meta.get(key) for key in (
                "target_fingerprint", "context_fingerprint",
                "findings_fingerprint", "canonical_revision")
            if meta.get(key) is not None
        },
        "dod": dod,
        "notices": _ports._design_review_notices(meta),
    }
    if output_snapshot is not None:
        evidence["em_output_snapshot"] = \
            _ports.em_outage.output_snapshot_evidence(output_snapshot)
    return evidence, []


def _signoff_dod(_ports, ws: str, state: dict) -> dict:
    return signoff_dod(state)


def signoff_dod(state: dict) -> dict:
    """Return the EM-sealed terminal verdict; never re-read shared evidence."""
    evidence = state.get("signoff_evidence")
    if isinstance(evidence, dict) \
            and evidence.get("schema") == "taskplane.signoff-evidence/v1" \
            and evidence.get("integration_revision") \
            and isinstance(evidence.get("dod"), dict):
        return dict(evidence["dod"])
    return {
        "passed": False,
        "errors": ["sign-off requires immutable integration evidence"],
        "notices": [], "scope": [], "baseline": state.get("baseline"),
    }


def _signoff_gate_dod(_ports, ws: str, state: dict) -> dict:
    return _ports._signoff_dod(ws, state)


def approve(_ports, ws: str, force: bool = False, by: str = None) -> dict:
    """Pass a human checkpoint (plan-approval or EM sign-off).

    `by` (v1.4.0, built for Claude Tag threads): WHO approved and where —
    e.g. "Dana R. — 'approved' in #platform-eng thread". Recorded into the
    trace event and the KB decision, so a gate pass is attributable to a
    human even in environments with no hook enforcement. In an unattended
    or Tag session, an approve WITHOUT `by` is exactly the self-approval
    the adherence experiment flags — drivers must pass the human's words."""
    if refusal := _ports._run_schema_refusal(ws):
        return refusal
    state = _ports.load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state["step"]
    refinement = None
    attestation_warning = None
    gate_notices: list = []
    define_projection = None
    root_preparation = None
    if not str(by or "").strip() or _ports.tp.task_slot() is not None:
        return {"error": "approval requires an attributable human outside a worker slot"}
    if step == "signoff":
        errors = evaluation_approval_errors(state)
        if errors:
            return {"error": "evaluation unavailable: explicit human acceptance with reason is required",
                    "blockers": errors}
    if step == "design_approval":
        refusal, define_projection, gate_notices = _approve_design(_ports, by, state, step, ws)
        if refusal is not None:
            return refusal
    elif step == "plan_approval":
        current_errors = _ports._design_current_errors(ws, state)
        if current_errors:
            return {"error": "approved design is stale — plan approval is "
                             "blocked", "step": step,
                    "dor": {"ready": False, "blockers": current_errors}}
        # Refinement gate (advisory; hard only for high-cost tasks).
        refinement = _ports._refinement_report(ws, state)
        blocked = [r for r in refinement if r.get("gate", {}).get("blocking")]
        if blocked and not force:
            return {"error": "refinement gate BLOCKED — a high-cost task's "
                             "requirement is under the threshold. Refine it "
                             "(close the gaps) or `loop approve --force`.",
                    "refinement": refinement}
        if (refusal := _ports.tp.plan_task_id_refusal(
                ws, state.get("tasks"), "approve", by=by)):
            return refusal
        state["authority_target_revision"] = _ports.tp.git_head(ws)
        fields = _ports._authorization_fields(ws, state)
        packet = _ports.authority_engine.create_packet(fields)
        actor = str(by or "").strip()
        receipt = _ports.authority_engine.approve(
            packet, actor=actor,
            thread="loop:" + packet["fingerprint"][:20],
            authenticated=bool(actor))
        state["authority_packet"] = packet
        state["authority_receipt"] = receipt
        state["authority_derivations"] = {
            "execute": _ports.authority_engine.derive(
                packet, receipt, stage="execute", current=fields,
                actor=receipt["actor"], thread=receipt["thread"])
        }
        _ports.tp.trace(ws, "authority_packet", actor=receipt["actor"],
                 packet=packet["fingerprint"],
                 receipt=receipt["fingerprint"])
        # Baseline for later diff-routing at EVALUATE/EM.
        state["baseline"] = _ports.tp.git_head(ws)
        resume_at = _ports._first_unsettled_task_index(state)
        state["step"] = "execute" if resume_at is not None else "em"
        state["current_task"] = resume_at if resume_at is not None else 0
        if state["step"] == "execute":
            try:
                root_preparation = _ports._prepare_approved_plan_root(ws, state)
            except Exception as exc:
                return {
                    "error": "root seed preparation failed before Plan "
                             "approval commit: "
                             f"{exc.__class__.__name__}: {exc}",
                    "step": "plan_approval",
                }
            state["root_hygiene"] = root_preparation["prepared"]
            state["plan_fingerprint"] = root_preparation["plan_fingerprint"]
            state["settings_digest"] = root_preparation["settings_digest"]
        _ports.tp.trace(ws, "loop_approve", gate="plan", by=by)
        # High-signal decision → the knowledge base.
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        _ports.kb.record_decision(
            ws, f"Plan approved: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}"
                    + (f"\nApproved by: {by}" if by else ""),
            decision=f"Approved a {len(state.get('tasks') or [])}-task plan.",
            tags=["plan-approval"], context_files=scope,
            links={"loop": "plan"})
    elif step == "signoff":
        if not state.get("signoff_evidence"):
            return {"error": "sign-off requires retained Engineering evidence", "step": "signoff"}
        dod = _ports._signoff_dod(ws, state)
        if not dod["passed"]:
            _ports.tp.trace(ws, "loop_approve_blocked", gate="em_signoff",
                     reason="dod", errors=dod["errors"], by=by)
            return {"error": "Definition of Done failed — sign-off cannot "
                             "complete until the evidence is repaired",
                    "step": "signoff", "dod": dod}
        terminal_metrics = _ports._seal_terminal_metrics_before_retro(ws, state)
        state["terminal_metrics"] = terminal_metrics
        state["step"] = "retro"
        _ports.tp.trace(ws, "loop_approve", gate="em_signoff", final="retro", by=by)
        # v2.3.0 wiring: the sign-off payload carries the review's design
        # notices (accepted drift, declared edge realizations) when present.
        gate_notices = list(
            (state.get("signoff_evidence") or {}).get("notices") or [])
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        _ports.kb.record_decision(
            ws, f"Accepted: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}"
                    + (f"\nApproved by: {by}" if by else "")
                    + ("".join("\nNotice: " + n for n in gate_notices)),
            decision="EM review passed and the human signed off — shipped.",
            tags=["accepted", "em-signoff"], context_files=scope,
            links={"loop": "signoff"})
    elif step == "selection":
        return {"error": "the selection gate needs a CHOICE, not a plain "
                         "approve — `loop select <variant|task-id|hybrid>`"}
    else:
        return {"error": f"nothing to approve at step '{step}'"}
    # Commit under the lock with a compare-and-swap on the entry step (v2.3.1):
    # approve() runs seconds of unlocked validation (signoff DoD runs every
    # task's tests, refinement, kb writes); an unlocked save could clobber a
    # concurrent gate() transition (the lost-update class the H2 fix closed in
    # gate()). If the on-disk step advanced while we worked, abort instead.
    stage_transition = None
    with _ports.mutate(ws) as locked:
        if locked.get("step") != step:
            return {"error": "the loop advanced concurrently during this "
                             f"approval (was '{step}', now "
                             f"'{locked.get('step')}') — re-run `loop next`",
                    "step": locked.get("step")}
        completion = _ports._stage_loop_gate_completion(
            ws, state, step=step, outcome="approved", note=str(by or ""),
            approval={"actor": str(by or ""), "force": bool(force),
                      "notices": list(gate_notices)})
        try:
            stage_transition = _ports._stage_loop_transition(
                ws, state, from_step=step, to_step=state["step"],
                completion=completion)
        except Exception as exc:
            return {"error": "stage-native loop transition failed closed: "
                    f"{exc.__class__.__name__}: {exc}", "step": step}
        locked.clear()
        locked.update(state)
    out = {"step": state["step"], "status": _ports.status(ws)}
    if stage_transition is not None:
        out["stage_transition"] = stage_transition
    if refinement:
        out["refinement"] = refinement
    if define_projection is not None:
        out["define_projection"] = define_projection
    if attestation_warning:
        out["warning"] = attestation_warning
    if gate_notices:
        out["notices"] = gate_notices
    if state["step"] == "retro":
        out["instruction"] = (
            "Human sign-off is recorded. Run `tp loop retro` once to record "
            "the lessons, true up the dependency graph, and close the loop.")
    return out


def select(_ports, ws: str, choice: str, note: str = "") -> dict:
    """The A/B selection gate — the human's pick of what ships. Accepts a
    variant letter, a task id, or 'hybrid'. This gate REPLACES the merge
    step variants never have: a winner goes to the engineering review; a
    hybrid goes back to plan for the graft (both variants kept as
    reference). Recorded to the KB — the WHY outlives the losing branch."""
    if refusal := _ports._run_schema_refusal(ws):
        return refusal
    _ports.reconcile_authority_effects(ws)
    stage_transition = None
    with _ports.mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        if state["step"] != "selection":
            return {"error": f"selection only at the selection gate "
                             f"(current: {state['step']})"}
        stage_before = _ports.json.loads(_ports.json.dumps(state))
        tasks = state.get("tasks") or []
        variants = [t for t in tasks if t.get("variant")] or tasks
        expected_revision = str(state.get("authority_target_revision") or
                                state.get("baseline") or "")
        # Revision validation and state mutation share one lock. A checkout
        # change can no longer land between validation and persistence.
        current_revision = _ports.tp.git_head(ws)
        if not expected_revision:
            return {"error": "selection has no approved target revision"}
        boundary = _ports.authority_engine.build_selection(
            variants, selected=choice.strip(), revision=current_revision,
            expected_revision=expected_revision)
        if not boundary["authorized"] and \
                "stale_selection" in boundary["reasons"]:
            return {"error": "A/B selection is stale — the checkout revision "
                             "changed after the selection gate opened; refresh "
                             "the variants before choosing",
                    "expected_revision": expected_revision,
                    "actual_revision": current_revision,
                    "variants": [{"id": t["id"],
                                  "variant": t.get("variant")}
                                 for t in variants]}
        if not boundary["authorized"] and \
                "invalid_selection" in boundary["reasons"] and \
                choice.strip().lower() not in {
                    "hybrid", "neither", "none", "reject", "reject-both"}:
            return {"error": f"no variant matches '{choice}' — use a task "
                             "id, a variant letter, or 'hybrid'",
                    "variants": [{"id": t["id"],
                                  "variant": t.get("variant")}
                                 for t in variants]}
        state["_authority_revision_fence"] = current_revision
        if choice.strip().lower() == "hybrid":
            state["selection"] = {"choice": "hybrid", "note": note,
                                  "revision": current_revision}
            for t in variants:
                t["status"] = "reference"
            state["step"] = "plan"
            instruction = (
                "Hybrid selected: write a NEW plan/tasks.json with the graft "
                "task(s) — name the base variant's branch and what to graft "
                "from the other — then `loop gate pass`. Plan approval and "
                "the build/evaluate cycle apply as usual; both variant "
                "branches stay as reference until the retro.")
        elif choice.strip().lower() in (
                "neither", "none", "reject", "reject-both"):
        # Neither variant ships — the A/B round is abandoned. Both variants
        # become not_selected (kept as reference branches) and the loop goes
        # back to PLAN for a fresh approach, so the human who picks "neither"
        # has a real transition instead of parking at the selection gate.
            state["selection"] = {"choice": "neither", "note": note,
                                  "revision": current_revision}
            for t in variants:
                t["status"] = "not_selected"
            state["step"] = "plan"
            instruction = (
                "Neither variant selected: both are set aside (branches kept "
                "as reference). Write a NEW plan/tasks.json taking a "
                "different approach — what did both variants get wrong? — "
                "then `loop gate pass`. Plan approval and the build/evaluate "
                "cycle apply as usual.")
        else:
            c = choice.strip()
            win = next((t for t in variants
                        if t["id"] == c or
                        str(t.get("variant", "")).lower() == c.lower()), None)
            if win is None:
                return {"error": f"no variant matches '{choice}' — use a task "
                                 "id, a variant letter, or 'hybrid'",
                        "variants": [{"id": t["id"],
                                      "variant": t.get("variant")}
                                     for t in variants]}
            state["selection"] = {"choice": win["id"],
                                  "variant": win.get("variant"), "note": note,
                                  "revision": current_revision}
            win["selected"] = True
            win["status"] = "passed"
            for t in variants:
                if t is not win:
                    t["status"] = "not_selected"
            state["step"] = "em"
            instruction = (
                f"Winner: {win['id']}. Merge its branch "
                f"(`git merge tp/{win['id']}`), keep the losing branch as "
                "reference until the retro, clear the variant worktree "
                "contracts, then run the engineering review of the merged "
                "result using the sealed direct evidence with zero lens workers.")
        selection = dict(state["selection"])
        goal = str(state.get("goal") or "")
        context_files = sorted({g for t in variants
                                for g in t.get("scope", [])})
        effect_id = f"selection:{current_revision}:{selection['choice']}"
        _ports._enqueue_authority_effect(
            state, effect_id, trace_event="loop_select",
            trace_data={"choice": selection["choice"], "note": note},
            kb_data={
                "title": f"A/B selection: {selection['choice']} — {goal[:48]}",
                "context": (f"Goal: {goal}; variants: "
                            + ", ".join(t["id"] for t in variants)),
                "decision": (note or
                             f"Human selected {selection['choice']} at the "
                             "selection gate."),
                "tags": ["ab-selection"], "context_files": context_files,
                "links": {"loop": "selection"},
            })
        state["_stage_completion"] = _ports._stage_loop_decision_completion(
            ws, schema="taskplane.loop-selection-result/v1",
            step="selection", outcome="selected",
            result={"choice": selection, "note": str(note or "")[:1024],
                    "selected_revision": current_revision})
        try:
            stage_transition = _ports._stage_loop_transition(
                ws, state, from_step="selection", to_step=state["step"])
        except Exception as exc:
            state.clear()
            state.update(stage_before)
            return {"error": "stage-native selection transition failed "
                    f"closed: {exc.__class__.__name__}: {exc}",
                    "step": "selection"}
        state.pop("_stage_completion", None)
    state.pop("_authority_revision_fence", None)  # fake/test mutate adapters
    fence_failure = state.pop("_revision_fence_failed", None)
    if fence_failure:
        return {
            "error": "A/B selection is stale — the checkout revision changed "
                     "during the locked selection commit; no authority "
                     "transition was persisted",
            "expected_revision": fence_failure["expected"],
            "actual_revision": fence_failure["actual"],
        }
    effects = _ports.reconcile_authority_effects(ws)
    return {"step": state["step"], "selection": selection,
            "instruction": instruction, "status": _ports.status(ws),
            "effect_delivery": effects,
            **({"stage_transition": stage_transition}
               if stage_transition is not None else {})}


def _cascade_skip(_ports, state: dict, root_id: str) -> list:
    """Skip every task that (transitively) depends on root_id — they can
    never reach passed, so leaving them pending would deadlock the wave.
    Returns the ids that were cascaded."""
    tasks = state.get("tasks") or []
    dead = {root_id}
    cascaded = []
    changed = True
    while changed:
        changed = False
        for t in tasks:
            if t.get("status") in _ports.SETTLED:
                continue
            if set(t.get("deps") or []) & dead:
                t["status"] = "skipped"
                dead.add(t["id"])
                cascaded.append(t["id"])
                changed = True
    return cascaded


def resolve(_ports,
        ws: str, decision: str, *, by: str | None = None,
        run_id: str | None = None, task_id: str | None = None, reason: str | None = None,
        accept_producer_receipt_outage: bool = False,
        outage_fingerprint: str | None = None, phase_operation: str | None = None,
        candidate_fingerprint: str | None = None, worker_stopped: bool = False) -> dict:
    """Resolve a task escalation or retry the current failed EM review."""
    if refusal := _ports._run_schema_refusal(ws):
        return refusal
    state = _ports.load(ws)
    if decision == "limits-advisory":
        if state is None or phase_operation or candidate_fingerprint or worker_stopped:
            return {"error": "limits-advisory requires only the existing run and human --by"}
        return _ports.phase_harness.advise_resource_limits(_ports, ws, state, by or "")
    if decision == "reconcile":
        if state is None or candidate_fingerprint or worker_stopped:
            return {"error": "reconcile requires the exact existing phase operation"}
        return _ports.phase_harness.reconcile(_ports, ws, state, phase_operation or "")
    if phase_operation is not None or candidate_fingerprint is not None or worker_stopped:
        try:
            if decision != "retry" or state is None:
                raise ValueError("phase recovery only supports retry on the existing run")
            return _ports._resolve_phase_retry(ws, state, operation=phase_operation or "",
                candidate=candidate_fingerprint or "", by=by or "", worker_stopped=worker_stopped)
        except (ValueError, OSError) as exc:
            return {"error": "phase retry refused: " + str(exc), "dispatch_allowed": False}
    if state is not None and state.get("step") == "em" and \
            isinstance(state.get("engineering_review_outage"), _ports.Mapping) and \
            decision == "pass" and accept_producer_receipt_outage is True:
        return _ports._resolve_em_producer_receipt_outage(
            ws, by=by, accept=accept_producer_receipt_outage,
            supplied_fingerprint=outage_fingerprint)
    if state is None or state["step"] != "escalated":
        return {"error": "nothing escalated to resolve"}
    t = _ports._current_task(state)
    cascaded = []
    em_retry = None
    if decision == "retry" and "engineering_review_request_changes" in state:
        changes = state["engineering_review_request_changes"]
        if not isinstance(changes, dict) or changes.get("schema") != \
                "taskplane.engineering-review-request-changes/v1":
            return {"error": "EM request-changes record is malformed"}
        failed = changes.get("submission")
        if not isinstance(failed, dict) or failed.get("step") != "em" or \
                failed.get("outcome") != "fail" or \
                not _ports.re.fullmatch(r"[0-9a-f]{64}", str(failed.get("fingerprint") or "")):
            return {"error": "EM request-changes submission is invalid"}
        resolved = changes.get("resolution")
        if resolved is not None:
            if not isinstance(resolved, dict) or resolved.get("decision") != "retry" or \
                    resolved.get("submission_fingerprint") != failed["fingerprint"] or \
                    resolved.get("run_id") != state.get("run_id") or \
                    resolved.get("task") != failed.get("task") or \
                    not str(resolved.get("actor") or "").strip():
                return {"error": "EM retry resolution is invalid"}
            if t.get("status") in _ports.SETTLED:
                return {"error": "EM request changes were already resolved"}
        else:
            if failed.get("task") != t.get("id") or \
                    not str(failed.get("workspace") or "").strip() or \
                    _ports.os.path.realpath(str(failed.get("workspace") or "")) != _ports.os.path.realpath(ws) or \
                    not state.get("run_id") or any(
                        row.get("status") not in _ports.SETTLED for row in state.get("tasks") or []):
                return {"error": "EM request changes do not match the completed current Build"}
            if not str(by or "").strip():
                return {"error": "EM retry requires attributable --by"}
            em_retry = _ports.json.loads(_ports.json.dumps(changes))
            changes["resolution"] = {"decision": "retry", "actor": str(by).strip(),
                "run_id": state["run_id"], "task": t["id"],
                "submission_fingerprint": failed["fingerprint"], "resolved_at": int(_ports.time.time())}
            state["step"] = "em"
    if decision == "retry" and em_retry is not None:
        pass  # The completed Build records stay untouched; only EM retries.
    elif decision == "retry":
        classified = t.get("failure_routing") or {}
        retry_product_fix = (
            isinstance(classified, _ports.Mapping)
            and classified.get("next") == "fix"
            and classified.get("product_fix_allowed") is True
        )
        t["fix_cycles"] = 0
        t["status"] = "running"
        # An unavailable evaluator produced no product judgment, so there is
        # no implementation finding to fix. Non-product classifications also
        # cannot enter product Fix; after their owned recovery/correction,
        # retry the judgment. Only an exact product-only route reopens Fix.
        state["step"] = "fix" if retry_product_fix else "evaluate"
    elif decision == "pass":
        refusal = _accept_evaluation_outage(_ports, accept_producer_receipt_outage, by, outage_fingerprint, reason, state, t, ws)
        if refusal is not None:
            return refusal
    elif decision == "skip":
        t["status"] = "skipped"
        # Cascade: a task that depended (transitively) on the skipped one
        # can never satisfy deps⊆passed — skip it too, so it doesn't hold
        # the wave forever (the deadlock). Record which were cascaded.
        cascaded = _ports._cascade_skip(state, t["id"])
        if cascaded:
            _ports.tp.trace(ws, "loop_skip_cascade", root=t["id"], skipped=cascaded)
        if state.get("parallel"):
            # settled-aware: advance only when every task is settled
            if all(x.get("status") in _ports.SETTLED for x in state["tasks"]):
                state["step"] = ("selection" if state.get("ab")
                                 and not state.get("selection") else "em")
            else:
                state["step"] = "execute"
        else:
            # serial: skip past any task the cascade just settled, so the
            # next execute is a task that still has work owed.
            nxt = _ports._next_unsettled_index(state, state["current_task"])
            if nxt is not None:
                state["current_task"] = nxt
                state["step"] = "execute"
            else:
                state["step"] = "em"
    elif decision == "defer":
        # Human parks the task on an external gate: it settles AND satisfies
        # its dependents (the work will exist, just not via this loop) — the
        # clean form of what previously required hand-editing loop.json.
        t["status"] = "external"
        if state.get("parallel"):
            if all(x.get("status") in _ports.SETTLED for x in state["tasks"]):
                state["step"] = ("selection" if state.get("ab")
                                 and not state.get("selection") else "em")
            else:
                state["step"] = "execute"
        else:
            nxt = _ports._next_unsettled_index(state, state["current_task"])
            if nxt is not None:
                state["current_task"] = nxt
                state["step"] = "execute"
            else:
                state["step"] = "em"
    elif decision == "abort":
        state["step"] = "failed"
    else:
        return {"error": "decision must be retry|pass|skip|defer|abort"}
    state["_stage_completion"] = _ports._stage_loop_decision_completion(
        ws, schema="taskplane.loop-resolution-result/v1",
        step="escalated", outcome="resolved",
        result={"decision": decision, "task_id": t.get("id"),
                "resulting_status": t.get("status"),
                "resulting_step": state["step"],
                "cascaded_task_ids": list(cascaded)})
    stage_transition = None
    with _ports.mutate(ws) as locked:                       # v2.3.1: locked commit
        if locked.get("step") != "escalated":
            return {"error": "the loop advanced concurrently during resolve "
                             f"(now '{locked.get('step')}') — re-run",
                    "step": locked.get("step")}
        if em_retry is not None and (
                locked.get("engineering_review_request_changes") != em_retry or
                locked.get("run_id") != state.get("run_id") or
                locked.get("tasks") != state.get("tasks") or
                locked.get("current_task") != state.get("current_task")):
            return {"error": "EM request changes changed during retry"}
        try:
            stage_transition = _ports._stage_loop_transition(
                ws, state, from_step="escalated", to_step=state["step"])
        except Exception as exc:
            return {"error": "stage-native recovery transition failed "
                    f"closed: {exc.__class__.__name__}: {exc}",
                    "step": "escalated"}
        state.pop("_stage_completion", None)
        locked.clear()
        locked.update(state)
    _ports.tp.trace(ws, "loop_resolve", decision=decision, task=t.get("id"))
    return {"step": state["step"], "status": _ports.status(ws),
            **({"stage_transition": stage_transition}
               if stage_transition is not None else {})}


def replan(_ports, ws: str, by: str, reason: str) -> dict:
    if refusal := _ports._run_schema_refusal(ws):
        return refusal

    @_ports.contextlib.contextmanager
    def stage_bound_mutate(workspace: str):
        with _ports.mutate(workspace) as locked:
            before = _ports.json.loads(_ports.json.dumps(locked)) if locked is not None \
                else None
            from_step = str((locked or {}).get("step") or "")
            yield locked
            if locked is None or locked.get("step") == from_step:
                return

            locked["_stage_completion"] = _ports._stage_loop_decision_completion(
                workspace, schema="taskplane.loop-replan-result/v1",
                step=from_step, outcome="replanned",
                result={"from_step": from_step,
                        "to_step": str(locked["step"]),
                        "by": str(by)[:256],
                        "reason": str(reason)[:1024]})
            locked["_stage_force_transition"] = True
            try:
                _ports._stage_loop_transition(
                    workspace, locked, from_step=from_step,
                    to_step=str(locked["step"]))
            except Exception:
                locked.clear()
                locked.update(before or {})
                raise
            finally:
                locked.pop("_stage_completion", None)
                locked.pop("_stage_force_transition", None)

    def release_replanned_contract(workspace: str) -> None:
        _ports.tp.sweep_completed_worker_contracts(
            workspace, loop_state=_ports.load(workspace))

    try:
        return _ports.loop_recovery.replan(
            ws, by=by, reason=reason, load_state=_ports.load,
            mutate_state=stage_bound_mutate,
            clear_contract=release_replanned_contract,
            trace=_ports.tp.trace, record_decision=_ports.kb.record_decision)
    except Exception as exc:
        return {"error": "stage-native replan transition failed closed: "
                f"{exc.__class__.__name__}: {exc}",
                "step": (_ports.load(ws) or {}).get("step")}


def retro(_ports, ws: str) -> dict:
    if refusal := _ports._run_schema_refusal(ws):
        return refusal

    # Abort/failure can enter Retro without passing human sign-off. Seal the
    # same measured-or-attributable-unavailable terminal truth before Retro
    # reads it; missing usage is never converted to zero.
    opening = _ports.load(ws)
    if isinstance(opening, _ports.Mapping):
        try:
            _ports._phase_bridge_retro_completion(ws, opening)
        except (ValueError, OSError) as exc:
            return {"error": "phase Retro prerequisites refused: " + str(exc), "step": opening.get("step")}
    if isinstance(opening, _ports.Mapping) and isinstance(
            opening.get("run_artifact_binding"), _ports.Mapping) and not (
                isinstance(opening.get("wave_metrics_receipt"), _ports.Mapping) or
                isinstance(opening.get("wave_metrics_unavailable"), _ports.Mapping)):
        with _ports.mutate(ws) as locked:
            if locked is None or locked.get("step") not in {
                    "retro", "failed", "done"}:
                return {"error": "terminal metrics cannot be sealed outside "
                                 "Retro", "step": (locked or {}).get("step")}
            locked["terminal_metrics"] = \
                _ports._seal_terminal_metrics_before_retro(ws, locked)

    @_ports.contextlib.contextmanager
    def prepare_only_mutate(workspace: str):
        with _ports.mutate(workspace) as locked:
            if locked is not None:
                _ports._phase_bridge_retro_completion(workspace, locked)
            yield locked
            if locked is None:
                return
            sealed = locked.get("retro") or {}
            if locked.get("step") in {"done", "failed"} and \
                    sealed.get("status") == "complete":
                locked["_retro_terminal_step"] = locked["step"]
                locked["step"] = "retro"

    result = _ports.retro_engine.run(
        ws, load_state=_ports.load, mutate_state=prepare_only_mutate,
        loop_path=_ports._loop_path(ws), normalize_severity=_ports.normalize_severity)
    if not isinstance(result, dict) or result.get("error"):
        return result
    final = _ports.load(ws) or {}
    target_step = str(final.get("_retro_terminal_step") or final.get("step"))
    if target_step not in {"done", "failed"}:
        return result
    terminal_artifacts = final.get("terminal_artifacts")
    if terminal_artifacts is None and isinstance(
            final.get("run_artifact_binding"), _ports.Mapping):
        wave_receipt = final.get("wave_metrics_receipt")
        unavailable_metrics = final.get("wave_metrics_unavailable")
        if not isinstance(wave_receipt, _ports.Mapping) and not isinstance(
                unavailable_metrics, _ports.Mapping):
            return {
                "error": "terminal run artifacts require measured or "
                         "attributable-unavailable live wave metrics; missing "
                         "usage cannot be converted to zero or cleaned up",
                "step": "retro", "retro": result,
            }
        try:
            artifact_root = _ports._run_artifact_root(ws, final)
            terminal_artifacts = _ports.retro_engine.publish_terminal_artifacts(
                artifact_root,
                wave_receipt=(dict(wave_receipt)
                              if isinstance(wave_receipt, _ports.Mapping) else None),
                report={**result, **({"wave_metrics_unavailable":
                                      dict(unavailable_metrics)}
                                     if isinstance(unavailable_metrics, _ports.Mapping)
                                     else {})},
                lifecycle_outcome=("success" if target_step == "done"
                                   else "failure"))
            with _ports.mutate(ws) as locked:
                if locked is None or locked.get("step") != "retro":
                    return {"error": "loop advanced while terminal artifacts "
                                     "were being sealed", "step": (
                                         locked or {}).get("step")}
                locked["terminal_artifacts"] = terminal_artifacts
                locked.setdefault("run_artifact_refs", {}).update({
                    "terminal_telemetry": terminal_artifacts["telemetry"],
                    "terminal_retro": terminal_artifacts["retro"],
                })
            final = _ports.load(ws) or final
        except Exception as exc:
            return {"error": "terminal run-artifact publication failed "
                             "closed before cleanup: "
                             f"{exc.__class__.__name__}: {exc}",
                    "step": "retro", "retro": result}
    terminal_cleanup = final.get("terminal_cleanup")
    if terminal_cleanup is None and isinstance(
            final.get("run_artifact_binding"), _ports.Mapping):
        try:
            terminal_cleanup = _ports._finalize_owned_run_cleanup(
                ws, final,
                outcome=("success" if target_step == "done" else "failure"))
            with _ports.mutate(ws) as locked:
                if locked is None or locked.get("step") != "retro":
                    return {"error": "loop advanced while owned cleanup was "
                                     "being sealed", "step": (
                                         locked or {}).get("step")}
                locked["terminal_cleanup"] = terminal_cleanup
                cleanup_ref = terminal_cleanup.get(
                    "durable_cleanup_artifact")
                if isinstance(cleanup_ref, _ports.Mapping):
                    locked.setdefault("run_artifact_refs", {})[
                        "terminal_cleanup"] = dict(cleanup_ref)
            final = _ports.load(ws) or final
        except Exception as exc:
            return {"error": "owned terminal cleanup failed closed before "
                             "transition: "
                             f"{exc.__class__.__name__}: {exc}",
                    "step": "retro", "retro": result}
    try:
        context = _ports._stage_loop_context(ws, final)
        already_terminal = (
            context is not None and context.get("stage") is None
            and (final.get("retro") or {}).get("prior_step") in {"done", "failed"})
        transition = None
        if not already_terminal:
            transition_kwargs = {"from_step": "retro", "to_step": target_step}
            if final.get("_retro_terminal_step"):
                completion = _ports._stage_loop_gate_completion(
                    ws, final, step="retro", outcome=target_step)
                completion["retro"] = result
                completion["_stage_output"]["values"]["retro"] = result
                transition_kwargs["completion"] = completion
            transition = _ports._stage_loop_transition(ws, final, **transition_kwargs)
    except Exception as exc:
        # Keep the sealed report and its terminal target as a durable replay
        # marker.  ``retro_engine.run`` returns that same sealed report on the
        # next invocation, allowing stage terminalization to resume without
        # re-running or rewriting Retro.  The marker is cleared only after the
        # immutable stage transition succeeds and workflow metadata can commit.
        return {"error": "stage-native Retro terminalization failed closed: "
                f"{exc.__class__.__name__}: {exc}", "step": "retro",
                "retro": result}
    terminal_authority = None
    terminal_delivery = final.get("terminal_delivery")
    if terminal_delivery is not None:
        try:
            if not isinstance(terminal_delivery, _ports.Mapping):
                raise TypeError("terminal delivery composition must be a mapping")
            terminal_delivery = _ports.copy.deepcopy(dict(terminal_delivery))
            root_receipt = final.get("root_hygiene_receipt")
            if isinstance(root_receipt, _ports.Mapping):
                surfaces = terminal_delivery.get("surfaces")
                release_surface = ((surfaces or {}).get("release_evidence")
                                   if isinstance(surfaces, _ports.Mapping) else None)
                release_payload = ((release_surface or {}).get("payload")
                                   if isinstance(release_surface, _ports.Mapping)
                                   else None)
                identity = terminal_delivery.get("identity")
                if not isinstance(release_payload, _ports.Mapping) or \
                        not isinstance(identity, _ports.Mapping):
                    raise TypeError(
                        "terminal release surface cannot consume root hygiene")
                terminal_delivery["surfaces"] = _ports.copy.deepcopy(dict(surfaces))
                terminal_delivery["surfaces"]["release_evidence"] = \
                    _ports.release_evidence.terminal_release_evidence_surface(
                        identity, {**dict(release_payload),
                                   "root_hygiene_receipt": root_receipt})
            terminal_authority = _ports.terminal_truth.finalize_terminal_delivery(
                **terminal_delivery)
        except Exception as exc:
            # The stage transition is replay-safe and the workflow marker
            # remains at Retro until the durable terminal bundle reconciles.
            # Never return a terminal outcome without its live CAS authority.
            return {"error": "terminal delivery failed closed: "
                    f"{exc.__class__.__name__}: {exc}", "step": "retro",
                    "retro": result}
    if final.get("_retro_terminal_step"):
        with _ports.mutate(ws) as locked:
            if locked is None or locked.get("step") != "retro":
                return {"error": "Retro finalization lost its "
                        "prepared state", "step": (locked or {}).get("step")}
            sealed = locked.get("retro") or {}
            if sealed.get("status") != "complete":
                return {"error": "Retro report is not complete",
                        "step": "retro"}
            locked["step"] = target_step
            locked["completed_at"] = _ports.time.time()
            locked.pop("_retro_terminal_step", None)
    if transition is not None:
        result = {**result, "stage_transition": transition}
    if terminal_authority is not None:
        result = {**result, "terminal_authority": terminal_authority}
    if terminal_artifacts is not None:
        result = {**result, "terminal_artifacts": terminal_artifacts}
    if terminal_cleanup is not None:
        result = {**result, "terminal_cleanup": terminal_cleanup}
    return result


def evaluation_approval_errors(state: Mapping[str, object]) -> list[str]:
    """Infrastructure non-results need an exact, attributable exception."""
    errors = []
    for task in state.get("tasks") or []:
        evaluation = task.get("evaluation") or {}
        if evaluation.get("status") not in {"unavailable", "deferred", "unsupported"}:
            continue
        accepted = task.get("human_resolution") or {}
        identity = evaluation.get("outage_identity") or {}
        if (accepted.get("decision") != "pass" or not accepted.get("reason") or
                not accepted.get("actor") or accepted.get("run_id") != state.get("run_id") or
                accepted.get("task_id") != task.get("id") or
                accepted.get("outage_reason_code") != evaluation.get("reason_code") or
                accepted.get("outage_fingerprint") != str(identity.get("fingerprint") or "")):
            errors.append(str(task.get("id")) + ": " + str(evaluation["status"]))
    return errors


def _gate_parallel_build(_ports, note, outcome, state, step, submission, task_id, ws):
    wt_precheck = next((x for x in state.get("tasks") or []
                        if x["id"] == task_id), None)
    if wt_precheck is None:
        return {"error": "parallel gate needs --task <id> of a wave "
                         "member"}
    # Fail closed: an uncommitted worktree means the branch carries
    # NOTHING — the merge would be empty and worktree removal would
    # destroy the work. Commit first, then gate.
    wt = wt_precheck.get("workspace")
    if outcome == "pass":
        with _ports._claimed_execute_suite_binding():
            dod_errors = _ports._task_dod_errors(
                wt or ws, state, wt_precheck,
                submission.get("snapshot") if submission is not None else
                _ports._worker_stage_snapshot(wt or ws, step, wt_precheck))
        if dod_errors:
            _ports.tp.trace(ws, "loop_gate_blocked", step=step, task=task_id,
                     reason="dod", errors=dod_errors)
            return {"error": "Definition of Done failed — task remains "
                             "running", "dod": {"passed": False,
                             "errors": dod_errors}}
    if outcome == "pass" and wt and _ports.os.path.isdir(wt) and _ports.tp.is_dirty(wt):
        return {"error": f"task {task_id}: uncommitted work in {wt} — "
                         "the tp/<task> branch carries nothing yet. "
                         "`git add -A && git commit` in the worktree, "
                         "then gate again."}
    with _ports.mutate(ws) as locked:
        t = next((x for x in (locked.get("tasks") or [])
                  if x["id"] == task_id), None)
        if t is None:
            return {"error": "parallel gate needs --task <id> of a wave "
                             "member"}
        # v2.3.0: the final staleness re-attest runs INSIDE the lock,
        # immediately before the status commits — no TOCTOU window
        # between the attest and the transition.
        if state.get("submission_required"):
            stale = _ports._submission_staleness(ws, submission)
            if stale:
                return {"error": stale + " during gate validation — "
                                 "submit the final state again",
                        "step": step}
            if _ports._task_submission_authority_required(t):
                authority_error = _ports._task_submission_authority_error(
                    str(submission.get("workspace") or ws), submission,
                    step, t)
                if authority_error:
                    return {"error": authority_error, "step": step}
        prepared_registration = None
        if outcome == "pass":
            try:
                prepared_registration = \
                    _ports.runtime_storage.refresh_task_worktree_tip(
                        (_ports.runtime_storage.load_workspace_locator(ws) or {}).get("primary_checkout") or ws,
                        str(task_id))
            except _ports.runtime_storage.StorageIdentityError as exc:
                return {"error": f"task {task_id}: managed worktree "
                                 f"target binding failed: {exc}",
                        "step": step}
        t["status"] = "built"
        if prepared_registration is not None:
            t["target_commit"] = prepared_registration["branch_tip"]
            t["source_tree"] = _ports.tp._run(
                ["git", "rev-parse", "HEAD^{tree}"], cwd=wt).stdout.strip()
        task_state = _ports.stage_loop.task_phase_state(_ports, ws, locked, task_id)
        completion = _ports._stage_loop_gate_completion(wt or ws, task_state,
            step="execute", outcome=outcome, note=note, submission=submission,
            target_commit=t.get("target_commit"))
        _ports._stage_loop_transition(wt or ws, task_state, from_step="execute",
            to_step="evaluate", completion=completion)
        locked["_stage_bindings"] = task_state["_stage_bindings"]
        t["phase_step"] = "evaluate"
        verified_suite = ((state.get("_validated_suite_evidence") or {})
                          .get(t["id"]))
        if verified_suite:
            locked.setdefault("_suite_evidence", {})[t["id"]] = \
                verified_suite
        t.pop("_submission", None)
        if outcome != "pass":
            t["_build_failed"] = True
            t["failure_routing"] = _ports._detected_build_failure_routing(
                wt or ws, t, submission or {}, "execute")
        release_ws = t.get("workspace") or ws
        released_contracts = _ports.tp.release_worker_contracts_for_gate(
            release_ws, stage=step, task=str(task_id),
            outcome="success" if outcome == "pass" else "failure",
            submission_status="gated:" + outcome)
        _ports.tp.trace(ws, "loop_gate", step=step, task=task_id, outcome=outcome,
                 note=note)
        running = [x["id"] for x in locked["tasks"]
                   if x.get("status") == "running"]
    return {"step": "execute", "task": task_id, "built": True,
            "still_running": running, "status": _ports.status(ws)}


def _validate_product_gate(_ports, note, outcome, state, step, ws):
    if outcome != "pass":
        _ports.tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                 note=note or "pm rejected — staying at pm")
        return {"error": "pm gate: outcome was not 'pass' — refine the "
                         "requirement/spec, then gate again",
                "step": "pm", "status": _ports.status(ws)}
    spec_rel = state.get("spec_path") or _ports.os.path.join("specs", "spec.md")
    spec_abs = spec_rel if _ports.os.path.isabs(spec_rel) \
        else _ports.os.path.join(ws, spec_rel)
    has_req = bool(state.get("requirement_id"))
    if not has_req and not (_ports.os.path.isfile(spec_abs)
                            and _ports.os.path.getsize(spec_abs) > 0):
        _ports.tp.trace(ws, "loop_gate_blocked", step=step, reason="dod",
                 errors=["no spec"])
        return {"error": "pm Definition of Done failed — no requirement "
                         "was authored. Write a non-empty specs/spec.md "
                         "(or record a requirement with `tp req new` and "
                         "attach its R-id with `tp loop gate pass --req "
                         "R-XXXX`), then gate again.",
                "step": "pm",
                "dod": {"passed": False,
                        "errors": [f"{spec_rel} missing or empty and no "
                                   "requirement_id is attached — the pm "
                                   "step authors the WHAT before the "
                                   "loop advances"]}}
    if has_req:
        rec = _ports.reqs.get_requirement(ws, state["requirement_id"])
        product_dor = _ports.reqs.product_dor(rec)
        refinement = product_dor["refinement"]
        if not product_dor["passed"]:
            errors = ([f"requirement {state['requirement_id']} is missing"]
                      if not rec else product_dor["errors"])
            _ports.tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="requirement_dor", errors=errors)
            return {"error": "pm Definition of Ready failed — refine the "
                             "same requirement before planning",
                    "step": "pm",
                    "dor": {"passed": False, "errors": errors,
                            "refinement": refinement}}
        # Dependencies and named contracts are recorded by req new. The
        # context-file ownership edge is equally mechanical and belongs
        # at this gate, not in a second model-authored graph command.
        context_files = list(rec.get("context_files") or [])
        if context_files:
            _ports.depgraph.link_requirement(
                ws, rec["id"], context_files,
                kind="planned", replace=True)
        state["requirement_refinement"] = refinement
        # Product owns refinement, including the optional strategic note.
        # The note is recorded as advisory evidence and cannot create a
        # standalone user gate or override the canonical Product DoR.
        product_evidence = dict(rec)
        product_evidence.setdefault("score", refinement.get("score", 1)
                                    if isinstance(refinement, dict) else 1)
        state["product_definition"] = _ports._product_definition_gate(
            product_evidence)


def _validate_design_gate(_ports, note, outcome, state, step, ws):
    if outcome != "pass":
        _ports.tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                 note=note or "design rejected — staying at design")
        return {"error": "design gate: outcome was not 'pass' — revise "
                         "design/design.md and design/contract.json, then "
                         "gate again", "step": "design",
                "status": _ports.status(ws)}
    design_errors = _ports._design_dod_errors(ws, state)
    if design_errors:
        _ports.tp.trace(ws, "loop_gate_blocked", step=step,
                 reason="design_dod", errors=design_errors)
        return {"error": "Design Definition of Done failed — revise the "
                         "Design Contract before approval",
                "step": "design",
                "dod": {"passed": False, "errors": design_errors}}


def _advance_evaluated_task(_ports, evaluation_progress, failure_decision, failure_verdict, outcome, state, unavailable_verdict, ws):
    t = _ports._current_task(state)
    # One Evaluate route is bound to one task/candidate/attempt.  It
    # must never survive a terminal gate and be reused by the next
    # task or retry.
    state.pop("evaluate_child_evidence", None)
    build_failed = state.pop("_build_failed", False) or \
        t.pop("_build_failed", False)
    if outcome == "fail" and isinstance(failure_decision, dict):
        t["failure_routing"] = _ports._copy_json(failure_decision)
        t["evaluation"] = {
            "task": t.get("id"),
            "status": "complete",
            "verdict": "fail",
            "reason_code": "classified_failure",
            "detail": ("classified before correction: "
                       + str(failure_decision.get("next") or "hold")),
            "failures": _ports._copy_json(
                (failure_verdict or {}).get("failures") or []),
            "routing": _ports._copy_json(failure_decision),
        }
        _ports.tp.trace(
            ws, "failure_classified", task=t.get("id"),
            route=failure_decision.get("next"),
            classes=sorted({
                str(row.get("class")) for row in
                failure_decision.get("records") or []
                if isinstance(row, _ports.Mapping)}),
            product_fix_allowed=failure_decision.get(
                "product_fix_allowed") is True,
            fingerprint=failure_decision.get("fingerprint"))
    if outcome == "unavailable" and not build_failed:
        availability = dict(
            (unavailable_verdict or {}).get("evaluation") or {})
        identity = _ports.evaluator_health.outage_identity(
            task=str(t.get("id") or ""),
            requirement=str((unavailable_verdict or {}).get(
                "requirement") or ""),
            evaluation=availability,
            failures=list((unavailable_verdict or {}).get(
                "failures") or []))
        warning = {
            "task": t.get("id"),
            "status": "unavailable",
            "verdict": "non-judged",
            "reason_code": availability.get("reason_code"),
            "detail": str(availability.get("detail") or "")[:500],
            "outage_identity": identity,
        }
        t["status"] = "unavailable"
        t["evaluation"] = warning
        warnings = state.setdefault("evaluation_warnings", [])
        warnings[:] = [row for row in warnings
                       if row.get("task") != t.get("id")]
        warnings.append(warning)
        # Infrastructure could not provide the independent judgment
        # required for readiness.  Keep the task unsettled and pause
        # at the existing governed recovery boundary; only an
        # attributed human retry/skip/defer/abort can move it.
        state["step"] = "escalated"
    elif outcome == "pass" and not build_failed:
        t["status"] = "passed"
        # After the LAST task: A/B loops pause at the human SELECTION
        # gate (variants never merge — one gets picked) — but only
        # ONCE; a post-selection fix cycle goes back to the review.
        after_last = ("selection" if state.get("ab")
                      and not state.get("selection") else "em")
        if state.get("parallel"):
            # merge is the driver's job (instruction), state just moves on
            if all(x.get("status") in _ports.SETTLED
                   for x in state["tasks"]):
                state["step"] = after_last
            else:
                state["step"] = "execute"   # next wave / next built task
        else:
            # serial: advance to the next UNSETTLED task, skipping any the
            # skip-cascade already closed (else a dependency-failed task
            # gets silently built and shipped).
            nxt = _ports._next_unsettled_index(state, state["current_task"])
            if nxt is not None:
                state["current_task"] = nxt
                state["step"] = "execute"
            else:
                state["step"] = after_last
    elif outcome == "fail" and (
            not isinstance(failure_decision, dict) or
            failure_decision.get("next") != "fix" or
            failure_decision.get("product_fix_allowed") is not True):
        t["status"] = "failed"
        state["step"] = "escalated"
    else:
        t["fix_cycles"] = t.get("fix_cycles", 0) + 1
        if isinstance(evaluation_progress, dict):
            import review_convergence

            previous = t.get("convergence_revision")
            history = list(t.get("convergence_history") or [])
            if isinstance(previous, dict):
                closed = {
                    finding for row in history if isinstance(row, dict)
                    for finding in ((row.get("findings") or {}).get(
                        "closed") or [])}
                boundaries = t.get("convergence_boundaries") or {}
                decision = review_convergence.evaluate_fix_cycle(
                    previous, evaluation_progress,
                    cycle=t["fix_cycles"], previously_closed=closed,
                    history=history,
                    max_cycles=t.get("max_fix_cycles"),
                    human_stop=boundaries.get("human_stop") is True,
                    unsafe_recovery=(
                        boundaries.get("unsafe_recovery") is True),
                    scope_changed=(
                        previous.get("scope_fingerprint") !=
                        evaluation_progress.get("scope_fingerprint") or
                        boundaries.get("scope_changed") is True),
                    authority_changed=(
                        previous.get("authority_fingerprint") !=
                        evaluation_progress.get("authority_fingerprint") or
                        boundaries.get("authority_changed") is True))
            else:
                # The first failed canonical evaluation establishes the
                # comparison baseline and opens one bounded fix.
                baseline = review_convergence.evaluate_fix_cycle(
                    evaluation_progress, evaluation_progress,
                    cycle=t["fix_cycles"])
                decision = dict(
                    baseline, decision="continue",
                    reason="canonical_baseline_established")
            history.append(decision)
            t["convergence_history"] = history
            t["convergence_revision"] = evaluation_progress
            _ports.tp.trace(ws, "review_convergence_decision",
                     task=t.get("id"), cycle=t["fix_cycles"],
                     decision=decision["decision"],
                     reason=decision["reason"])
            if decision["decision"] == "continue":
                state["step"] = "fix"
            else:
                t["status"] = "failed"
                state["step"] = "escalated"
        else:
            t["status"] = "failed"
            state["step"] = "escalated"


def _advance_accepted_plan(_ports, stage_state_before, state, ws):
    _ports._annotate_plan_graph(ws, state)
    derivation = _ports._derive_consolidated_authority(ws, state, "execute")
    if derivation and derivation.get("authorized"):
        state["authority_derivations"] = {
            **(state.get("authority_derivations") or {}),
            "execute": derivation,
        }
        state["step"] = "execute"
        _ports.tp.trace(ws, "mechanical_gate", gate="plan",
                 outcome="pass", human_required=False,
                 authority=derivation.get("fingerprint"))
    else:
        state["step"] = ("plan_approval"
                         if "plan" in state["checkpoints"]
                         else "execute")
    resume_at = _ports._first_unsettled_task_index(state)
    state["current_task"] = resume_at if resume_at is not None else 0
    if state["step"] == "execute" and resume_at is None:
        state["step"] = "em"
    if state["step"] in ("execute", "em"):
        state["baseline"] = _ports.tp.git_head(ws)
    if state["step"] == "execute":
        try:
            root_preparation = _ports._prepare_approved_plan_root(ws, state)
        except Exception as exc:
            state.clear()
            state.update(stage_state_before)
            return {
                "error": "root seed preparation failed before Plan "
                         "gate commit: "
                         f"{exc.__class__.__name__}: {exc}",
                "step": "plan",
            }
        state["root_hygiene"] = root_preparation["prepared"]
        state["plan_fingerprint"] = root_preparation["plan_fingerprint"]
        state["settings_digest"] = \
            root_preparation["settings_digest"]


def _copy_validated_plan(_ports, _validated, state, step):
    if _validated.get("tasks"):
        state["tasks"] = _validated["tasks"]
    if "ab" in _validated:
        state["ab"] = _validated["ab"]
    if "parallel" in _validated:
        state["parallel"] = _validated["parallel"]
    if "selection" not in _validated:
        state.pop("selection", None)
    if "graph_dor" in _validated:
        state["graph_dor"] = _validated["graph_dor"]
    # Plan DoR validates and seals the delivery declaration on the
    # unlocked snapshot.  The locked transition must carry those
    # exact validated bytes forward just like tasks and graph_dor;
    # otherwise the fresh read silently severs Plan authority from
    # the first Build dispatch.  Revalidate before copying so this
    # bridge cannot become a fallback or a receipt-forging seam.
    if "delivery_mode_receipt" in _validated:
        validated_delivery_receipt = \
            _ports.delivery_policy.validate_delivery_mode_receipt(
                _validated["delivery_mode_receipt"])
        if validated_delivery_receipt != \
                _validated["delivery_mode_receipt"]:
            return {
                "error": "Plan delivery-mode receipt normalization "
                         "changed during locked transition",
                "step": step,
            }
        state["delivery_mode_receipt"] = _ports.json.loads(_ports.json.dumps(
            _validated["delivery_mode_receipt"]))
    for field in ("replan_reanchor", "replan_reanchor_history"):
        if field in _validated:
            state[field] = _ports.json.loads(_ports.json.dumps(_validated[field]))


def _commit_gate_transition(_ports, _validated, act_ws, em_request_changes, evaluation_progress, failure_decision, failure_verdict, note, outcome, signoff_evidence, step, submission, task_id, unavailable_verdict, ws):
    with _gate_state(_ports, ws, task_id) as state:
        if state is None:
            return ({"error": "no active loop"}, None, None)
        if state.get("step") != step:
            return ({"error": f"loop advanced to '{state.get('step')}' while "
                             "this gate was validating — run loop next and "
                             "gate again", "step": state.get("step")}, None, None)
        stage_state_before = _ports.json.loads(_ports.json.dumps(state))
        product_successor = None
        if step == "pm":
            try:
                phase = _ports._phase_bridge_context(ws, state)
                if phase is not None:
                    successors = [edge["successor"] for edge in phase["definition"]["edge_conditions"]
                                  if edge["condition"] == "accepted"]
                    if len(successors) != 1 or successors[0] not in {"design", "plan"}:
                        raise ValueError("Product requires one supported accepted successor")
                    product_successor = successors[0]
            except (ValueError, OSError) as exc:
                return ({"error": "phase runtime successor refused: " + str(exc), "step": step}, None, None)
        # v2.3.0: the final staleness re-attest runs INSIDE the state lock,
        # immediately before the transition commits — the old pre-lock check
        # left a TOCTOU window in which a workspace edit got blessed by a
        # gate whose evidence was attested against different bytes. (The
        # contract is cleared AFTER the locked transition, below, so a
        # refused gate also leaves the workspace governed.)
        if _validated.get("submission_required") and step in _ports.WORKER_SUBMISSION_STEPS:
            stale = _ports._submission_staleness(ws, submission)
            if stale:
                return ({"error": stale + " during gate validation — submit "
                                 "the final state again", "step": step}, None, None)
            if step in {"execute", "fix"}:
                locked_task = _ports._current_task(state)
                if _ports._task_submission_authority_required(locked_task):
                    authority_error = _ports._task_submission_authority_error(
                        str(submission.get("workspace") or ws), submission,
                        step, locked_task)
                    if authority_error:
                        return ({"error": authority_error, "step": step}, None, None)
        if outcome == "pass" and step == "evaluate":
            current = _ports._current_task(state)
            current["evaluation_phase"] = _ports._phase_bridge_pending(
                act_ws, state)["phase_runtime"]["completion"]
            try:
                authority_ref, source_revision = \
                    _ports._persist_reanchor_authority(
                        act_ws, current, "independent-pass")
            except Exception as exc:
                return ({"error": "evaluation pass authority could not be "
                                 "persisted fail-closed: "
                                 f"{exc.__class__.__name__}: {exc}",
                        "step": step}, None, None)
            current["workspace"] = _ports.os.path.realpath(act_ws)
            current["target_commit"] = source_revision
            current["reanchor_authority"] = authority_ref
        refreshed_fix_registration = None
        if outcome == "pass" and step == "fix" and state.get("parallel"):
            current = _ports._current_task(state)
            try:
                refreshed_fix_registration = \
                    _ports.runtime_storage.refresh_task_worktree_tip(
                        ws, str((current or {}).get("id") or ""))
            except _ports.runtime_storage.StorageIdentityError as exc:
                return ({"error": "fix gate could not bind the repaired "
                                 f"managed-worktree target: {exc}",
                        "step": step}, None, None)
            current["target_commit"] = \
                refreshed_fix_registration["branch_tip"]
        if _validated.get("tasks") and not state.get("tasks"):
            state["tasks"] = _validated["tasks"]
        if step == "plan":
            # plan validation recomputed these on the snapshot via
            # _load_tasks: loaded tasks, the ab flag, the round-scoped
            # selection reset, and the graph DoR verdict.
            refusal = _copy_validated_plan(_ports, _validated, state, step)
            if refusal is not None:
                return (refusal, None, None)
        elif "design_graph_fingerprint" in _validated and \
                "design_graph_fingerprint" not in state:
            state["design_graph_fingerprint"] = \
                _validated["design_graph_fingerprint"]
        completion_state = _ports.json.loads(_ports.json.dumps(state))
        if signoff_evidence is not None:
            completion_state["signoff_evidence"] = signoff_evidence
        completion = _ports._stage_loop_gate_completion(
            ws, completion_state, step=step, outcome=outcome, note=note,
            submission=submission,
            target_commit=((refreshed_fix_registration or {}).get(
                "branch_tip")))
        state.pop("_submission", None)
        if step == "pm":
            if "requirement_refinement" in _validated:
                state["requirement_refinement"] = \
                    _validated["requirement_refinement"]
            if product_successor is not None:
                state["design_required"] = product_successor == "design"
            state["step"] = product_successor or ("design" if state.get("design_required") else "plan")
        elif step == "design":
            state["step"] = "design_approval"
        elif step == "plan":
            # Product↔engineering graph, PLANNED side: link each task's
            # requirement to the modules its scope intends to touch, then
            # annotate the task with its blast radius (engineering) and any
            # OTHER requirements whose surface it overlaps (product). The
            # human approves the plan seeing both; the executor's contract
            # briefing carries them; evaluation compares against them later.
            refusal = _advance_accepted_plan(_ports, stage_state_before, state, ws)
            if refusal is not None:
                return (refusal, None, None)
        elif step == "execute":
            # a build always goes to evaluate; a FAILED build is flagged so
            # evaluate FAILs and routes to fix/escalate — one place owns the fail
            # policy (so the step transition itself is unconditional).
            state["step"] = "evaluate"
            current = _ports._current_task(state)
            current["target_commit"] = _ports.tp.git_head(act_ws)
            current["source_tree"] = _ports.tp._run(
                ["git", "rev-parse", "HEAD^{tree}"], cwd=act_ws).stdout.strip()
            current["status"] = "built"
            verified_suite = ((_validated.get(
                "_validated_suite_evidence") or {}).get(
                    _ports._current_task(state)["id"]))
            if verified_suite:
                current = _ports._current_task(state)
                state.setdefault("_suite_evidence", {})[current["id"]] = \
                    verified_suite
            if outcome != "pass":
                state["_build_failed"] = True
                current = _ports._current_task(state)
                current["failure_routing"] = \
                    _ports._detected_build_failure_routing(
                        act_ws, current, submission or {}, "execute")
        elif step == "evaluate":
            _advance_evaluated_task(_ports, evaluation_progress, failure_decision, failure_verdict, outcome, state, unavailable_verdict, ws)
        elif step == "fix":
            current = _ports._current_task(state)
            current["target_commit"] = _ports.tp.git_head(act_ws)
            current["source_tree"] = _ports.tp._run(["git", "rev-parse", "HEAD^{tree}"], cwd=act_ws).stdout.strip()
            verified_suite = ((_validated.get(
                "_validated_suite_evidence") or {}).get(
                    _ports._current_task(state)["id"]))
            if verified_suite:
                current = _ports._current_task(state)
                state.setdefault("_suite_evidence", {})[current["id"]] = \
                    verified_suite
            if outcome != "pass":
                current = _ports._current_task(state)
                current["failure_routing"] = \
                    _ports._detected_build_failure_routing(
                        act_ws, current, submission or {}, "fix")
                current["_build_failed"] = True
            state["step"] = "evaluate"
        elif step == "em":
            if outcome == "pass":
                # The graph was true-d up before the EM brief, so its
                # fingerprint is part of the evidence being gated rather
                # than a post-review mutation.
                state["signoff_evidence"] = signoff_evidence
                state["signoff_dod"] = dict(signoff_evidence["dod"])
                state["step"] = "signoff"
            else:
                # Request-changes evidence stays bound to the reviewed
                # snapshot while the existing escalation/replan machinery
                # owns recovery.  Pass-only sign-off evidence is untouched.
                state["engineering_review_request_changes"] = \
                    em_request_changes
                state["step"] = "escalated"
        try:
            stage_transition = _ports._stage_loop_transition(
                act_ws if state.get("parallel") and task_id else ws,
                state, from_step=step, to_step=state["step"], completion=completion,
                terminal_only=bool(state.get("parallel") and step == "evaluate"
                    and outcome == "pass" and state["step"] == "execute"))
        except Exception as exc:
            state.clear()
            state.update(stage_state_before)
            return ({"error": "stage-native loop transition failed closed: "
                    f"{exc.__class__.__name__}: {exc}", "step": step}, None, None)
    return (None, state, stage_transition)


def _finish_gate(_ports, state, _validated, act_ws, gate_worker_task, gated_task_id, note, outcome, reanchor_receipt, stage_transition, step, task, task_id, unavailable_verdict, ws):
    if step == "em" and outcome in {"pass", "fail"}:
        binding = _ports.review_kernel_binding(_validated, step, task)
        if binding:
            try:
                _ports._purge_review_generation_diff(
                    str(binding.get("workspace") or ws), binding["run_id"])
            except Exception as exc:
                _ports.tp.trace(ws, "review_diff_retention_failed", run_id=binding["run_id"],
                    failure_code=exc.__class__.__name__)
    if step == "em" and outcome == "pass":
        # One more COMPLETED engineering review: advance the audit cadence
        # (every Nth em review runs as a full audit sweep). A cadence-store
        # failure is traced, never allowed to block a validated sign-off.
        try:
            reviews = _ports.record_audit_review(ws)
            _ports.tp.trace(ws, "audit_review_recorded", reviews=reviews,
                     next_audit_due=_ports.audit_due(ws, state))
        except Exception as exc:      # noqa: BLE001
            _ports.tp.trace(ws, "audit_counter_failed", error=str(exc))
    # Release the step's contract only AFTER the locked transition committed
    # (v2.3.0): clearing before the lock left the workspace ungoverned during
    # the commit window; a refused gate above leaves it governed for retry.
    release_task = gate_worker_task
    released_contracts = _ports.tp.release_worker_contracts_for_gate(
        act_ws, stage=step, task=release_task,
        outcome="success" if outcome == "pass" else "failure",
        submission_status="gated:" + outcome)
    cleanup_result = None
    cleanup_task = (next((row for row in state.get("tasks", []) if row["id"] == task_id), None)
                    if isinstance(state, dict) and state.get("parallel") and task_id
                    else _ports._current_task(state) if isinstance(state, dict) else None)
    if step == "evaluate" and outcome == "pass" and state.get("parallel") \
            and cleanup_task is not None and \
            cleanup_task.get("status") == "passed":
        cleanup_result = _ports._automatic_merge_cleanup(ws, cleanup_task)
        # The helper may have moved the loop to escalation when the merge
        # could not produce a durable receipt. Return the committed truth.
        state = _ports.load(ws) or state
    _ports.yield_meter.gate_snapshot(ws, step, outcome)   # records, never gates
    _ports.tp.trace(ws, "loop_gate", step=step, task=gated_task_id,
             outcome=outcome, note=note,
             **({"reason": ((unavailable_verdict or {}).get("evaluation")
                             or {}).get("reason_code")}
                if outcome == "unavailable" else {}))
    if reanchor_receipt is not None:
        _ports.tp.trace(ws, "loop_replan_reanchored",
                 restored=reanchor_receipt["restored_count"],
                 pending=reanchor_receipt["pending_count"],
                 tasks=[row["task_id"]
                        for row in reanchor_receipt["restored"]],
                 fingerprint=reanchor_receipt["fingerprint"])
    return {"step": state["step"], "status": _ports.status(ws),
            **({"stage_transition": stage_transition}
               if stage_transition is not None else {}),
            **({"worktree_cleanup": cleanup_result}
               if cleanup_result is not None else {}),
            **({"warning": (state.get("evaluation_warnings") or [])[-1]}
               if outcome == "unavailable" else {}),
            **({"reanchor": reanchor_receipt}
               if reanchor_receipt is not None else {}),
            }


def _collect_gate_evidence(_ports, act_ws, outcome, state, step, submission, task, ws):
    unavailable_verdict = None
    evaluation_progress = None
    failure_verdict = None
    failure_decision = None

    # A reported PASS is a request to evaluate the gate. Evidence, not the
    # agent's assertion, determines whether the state machine advances.
    if outcome == "pass" and step in ("execute", "fix"):
        with _ports._claimed_execute_suite_binding():
            dod_errors = _ports._task_dod_errors(
                act_ws, state, task,
                submission.get("snapshot") if submission is not None else
                _ports._worker_stage_snapshot(act_ws, step, task))
        if dod_errors:
            _ports.tp.trace(ws, "loop_gate_blocked", step=step, reason="dod",
                     errors=dod_errors)
            return ({"error": "Definition of Done failed — step did not "
                             "advance", "step": step,
                    "dod": {"passed": False, "errors": dod_errors}}, None, None, None, None, None, None)
    if outcome == "pass" and step == "evaluate":
        if state.get("_build_failed") or (task or {}).get("_build_failed"):
            return ({
                "error": "a detected Build failure must be classified before "
                         "correction; an evaluator pass cannot erase it",
                "step": step,
            }, None, None, None, None, None, None)
        # A4: the engine that PRODUCED this evidence vs the one about to
        # judge it — a pure pre-check (decision 0018), so equal engines
        # leave the walk below byte-unchanged.
        if (skew := _ports.tp.engine_skew_refusal(ws, state.get("_submission"))):
            return (skew, None, None, None, None, None, None)
        evidence_errors = _ports._producer_observation_errors(
            act_ws, state, task, step, state.get("_submission"))
        evidence_errors.extend(_ports._evaluation_errors(
            act_ws, state, task, artifact_ws=ws))
        if evidence_errors:
            _ports.tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="evaluation_evidence", errors=evidence_errors)
            return ({"error": "evaluation evidence failed — step did not "
                             "advance", "step": step,
                    "dod": {"passed": False, "errors": evidence_errors}}, None, None, None, None, None, None)
    if outcome == "unavailable" and step == "evaluate":
        if (skew := _ports.tp.engine_skew_refusal(ws, state.get("_submission"))):
            return (skew, None, None, None, None, None, None)
        unavailable_errors, unavailable_verdict = \
            _ports._evaluation_unavailable_errors(act_ws, state, task)
        if unavailable_errors:
            _ports.tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="invalid_evaluation_unavailability",
                     errors=unavailable_errors)
            return ({"error": "evaluation unavailability was not proven — "
                             "step did not advance", "step": step,
                    "dod": {"passed": False,
                            "errors": unavailable_errors}}, None, None, None, None, None, None)
    if outcome == "fail" and step == "evaluate":
        unavailable_errors, _ = _ports._evaluation_unavailable_errors(
            act_ws, state, task)
        if not unavailable_errors:
            return ({"error": "evaluation infrastructure is unavailable, not "
                             "a product defect — gate unavailable; no FIX "
                             "cycle was opened", "step": step}, None, None, None, None, None, None)
        routing_errors = _ports._producer_observation_errors(
            act_ws, state, task, step, state.get("_submission"))
        classified_errors, failure_verdict, failure_decision = \
            _ports._evaluation_failure_routing(act_ws, state, task)
        routing_errors.extend(classified_errors)
        if routing_errors:
            _ports.tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="unclassified_evaluation_failure",
                     errors=routing_errors)
            return ({
                "error": "failure classification failed — no correction "
                         "path was opened",
                "step": step,
                "dod": {"passed": False, "errors": routing_errors},
            }, None, None, None, None, None, None)
        if failure_decision.get("next") == "fix":
            try:
                evaluation_progress = _ports._canonical_evaluation_progress(
                    act_ws, state, task)
            except Exception as exc:
                _ports.tp.trace(ws, "review_convergence_unavailable",
                         task=(task or {}).get("id"),
                         error=f"{exc.__class__.__name__}: {exc}")
    signoff_evidence = None
    if outcome == "pass" and step == "em":
        signoff_errors = _ports._producer_observation_errors(
            ws, state, task, step, state.get("_submission"))
        signoff_evidence, binding_errors = _ports._signoff_evidence_binding(ws, state)
        signoff_errors.extend(binding_errors)
        if signoff_errors:
            _ports.tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="terminal_signoff_evidence",
                     errors=signoff_errors)
            return ({"error": "engineering review is incomplete or terminal "
                             "sign-off evidence failed — the loop remains "
                             "at engineering review",
                    "step": step,
                    "dod": {"passed": False, "errors": signoff_errors}}, None, None, None, None, None, None)
    em_request_changes = None
    if outcome == "fail" and step == "em":
        review_submission = state.get("_submission") or {}
        em_request_changes = {
            "schema": "taskplane.engineering-review-request-changes/v1",
            "submission": dict(review_submission),
        }
    return (None, unavailable_verdict, evaluation_progress, failure_verdict, failure_decision, signoff_evidence, em_request_changes)


def _approve_design(_ports, by, state, step, ws):
    define_projection = None
    if not str(by or "").strip():
        return ({"error": "design approval needs --by with the human's "
                         "identity/context; the designer cannot self-approve"}, None, None)
    design_errors = _ports._design_dod_errors(ws, state)
    if design_errors:
        _ports.tp.trace(ws, "loop_approve_blocked", gate="design",
                 reason="dod", errors=design_errors, by=by)
        return ({"error": "Design Definition of Done failed — approval "
                         "cannot be recorded", "step": step,
                "dod": {"passed": False, "errors": design_errors}}, None, None)
    contract, _ = _ports._design_contract(ws)
    state["design_fingerprint"] = _ports._design_evidence_fingerprint(ws, contract)
    state["design_approved_by"] = by
    state["step"] = "done" if state.get("design_only") else "plan"
    _ports.tp.trace(ws, "loop_approve", gate="design", by=by,
             fingerprint=state["design_fingerprint"][:12])
    # v2.3.0 wiring: notices (e.g. self-attested lens evidence) surface
    # in the approval response AND in the recorded approval decision.
    gate_notices = _ports._dc.design_approval_notices(ws, contract)
    # v2.3.0: the sanctioned mechanical path for design-introduced
    # contracts into the graph — recorded + traced at the human
    # gate-pass (see _record_design_contracts). Plan DoR is unchanged.
    _ports._record_design_contracts(ws, state, contract)
    modules = ((contract or {}).get("graph") or {}).get(
        "proposed_modules") or []
    _ports.kb.record_decision(
        ws, f"Design approved: {state['goal'][:60]}",
        context=f"Goal: {state['goal']}\nApproved by: {by}\n"
                f"Fingerprint: {state['design_fingerprint']}"
                + ("".join("\nNotice: " + n for n in gate_notices)),
        decision=(contract or {}).get("decision", "Design approved."),
        tags=["design-approval", "solution-design"],
        context_files=list(modules),
        links={"loop": "design", "modules": list(modules)})
    return (None, define_projection, gate_notices)


def _accept_evaluation_outage(_ports, accept_producer_receipt_outage, by, outage_fingerprint, reason, state, t, ws):
    if not str(by or "").strip() or not str(reason or "").strip() or _ports.tp.task_slot() is not None:
        return {"error": "evaluation acceptance requires human --by and explicit --reason"}
    evaluation = t.get("evaluation") or {}
    accept_errors = []
    reason_code = evaluation.get("reason_code")
    outage_identity = evaluation.get("outage_identity") or {}
    expected_outage_fingerprint = str(
        outage_identity.get("fingerprint") or "").strip()
    supplied_outage_fingerprint = str(
        outage_fingerprint or "").strip()
    producer_receipt_exception = (
        reason_code == "producer_receipt_unavailable"
        and accept_producer_receipt_outage is True
        and bool(str(by or "").strip())
        and bool(expected_outage_fingerprint)
        and supplied_outage_fingerprint == expected_outage_fingerprint
    )
    if not (
            t.get("status") == "unavailable"
            and evaluation.get("status") == "unavailable"
            and evaluation.get("verdict") == "non-judged"
            and (reason_code == "orchestration_unavailable"
                 or producer_receipt_exception)):
        accept_errors.append(
            "pass is only available for a non-judged orchestration "
            "outage or an explicitly accepted exact producer-receipt "
            "outage")
    if reason_code == "producer_receipt_unavailable" and \
            not producer_receipt_exception:
        accept_errors.append(
            "producer-receipt acceptance requires --by, "
            "--accept-producer-receipt-outage, and the exact current "
            "--outage-fingerprint")
    act_ws = str(t.get("workspace") or ws)
    unavailable_errors, verdict = _ports._evaluation_unavailable_errors(
        act_ws, state, t)
    accept_errors.extend(unavailable_errors)
    if producer_receipt_exception:
        try:
            durable_identity = _ports.evaluator_health.outage_identity(
                task=verdict.get("task"),
                requirement=verdict.get("requirement"),
                evaluation=verdict.get("evaluation"),
                failures=verdict.get("failures"))
        except _ports.evaluator_health.EvaluatorHealthError as exc:
            accept_errors.append(
                f"producer-receipt outage identity is invalid: {exc}")
        else:
            if durable_identity != outage_identity:
                accept_errors.append(
                    "producer-receipt outage fingerprint does not bind "
                    "the current durable evaluator verdict")
    criteria = (verdict or {}).get("criteria") or []
    if not criteria or any(
            not isinstance(row, dict) or row.get("status") != "met"
            for row in criteria):
        accept_errors.append(
            "human pass requires every task criterion to be evidenced "
            "as met")
    if accept_errors:
        return {"error": "unavailable evaluation cannot be accepted as "
                "passed", "blockers": accept_errors}
    # This is a HUMAN recovery decision over a mechanically valid outage
    # envelope, not an evaluator self-pass.  Preserve the outage evidence
    # and make the exceptional acceptance explicit in task state.
    t["human_resolution"] = {
        "decision": "pass",
        "reason": str(reason).strip(),
        "actor": str(by).strip(),
        "run_id": state["run_id"],
        "task_id": t["id"],
        "outage_reason_code": reason_code,
        "outage_fingerprint": expected_outage_fingerprint,
    }
    authority_reason = (
        "human-resolved-producer-receipt-outage"
        if producer_receipt_exception
        else "human-resolved-orchestration-outage")
    try:
        authority_ref, source_revision = _ports._persist_reanchor_authority(
            act_ws, t, authority_reason)
    except Exception as exc:
        t.pop("human_resolution", None)
        return {"error": "human pass authority could not be persisted "
                "fail-closed: "
                f"{exc.__class__.__name__}: {exc}"}
    t["workspace"] = _ports.os.path.realpath(act_ws)
    t["target_commit"] = source_revision
    t["reanchor_authority"] = authority_ref
    t["status"] = "passed"
    after_last = ("selection" if state.get("ab")
                  and not state.get("selection") else "em")
    if state.get("parallel"):
        state["step"] = (after_last if all(
            row.get("status") in _ports.SETTLED for row in state["tasks"])
            else "execute")
    else:
        nxt = _ports._next_unsettled_index(state, state["current_task"])
        if nxt is not None:
            state["current_task"] = nxt
            state["step"] = "execute"
        else:
            state["step"] = after_last
