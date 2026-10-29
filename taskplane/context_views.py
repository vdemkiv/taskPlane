"""Deterministic bounded transport views; full evidence stays behind verified refs."""
from __future__ import annotations

from typing import Any
from pathlib import Path
from copy import deepcopy

from .context import Store, digest, encode, signed
from . import workflow as w

PHASE_BYTES = dict(zip(w.PHASES, (16384, 32768, 16384, 65536, 32768, 32768, 16384)))
COMMAND_BYTES = 16384


def review_projection(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Bounded, read-only review metadata; immutable refs retain the full records."""
    from . import workflow_evidence as evidence
    from .workflow_host import phase_readiness
    stage = w.current(state)
    result: dict[str, Any] = {"contract": state.get("phase_contract", "legacy"),
        "run": state["run"], "visit": stage["id"], "phase": stage["phase"],
        "approval": stage.get("decision"), "ready": None,
        "hold": deepcopy(stage.get("hold")), "history_ref": (stage.get("phase_review") or {}).get("history_ref")}
    if not w.phase_contract(state):
        return {**result, "status": "legacy", "note": "Legacy contract: DoR/DoD and collaboration records were not required; no outcomes inferred."}
    store = Store(workspace)
    packet = stage.get("packet") or {}
    head = stage.get("phase_review") or {}
    output = packet.get("output", {})
    artifacts = packet.get("review_artifacts", [])
    unsubmitted = bool(head and head.get("revision", 0) > output.get("phase_review", {}).get("revision", 0))
    if unsubmitted:
        draft = store.resolve(head["ref"])
        output, artifacts = draft["output"], draft["artifacts"]
    result.update(status="draft" if unsubmitted else "sealed" if packet else "unpublished",
                  draft_ref=head.get("ref"), revision=output.get("phase_review", {}).get("revision"))
    try:
        gates = phase_readiness(workspace, state, output if unsubmitted else None)
    except (w.Refusal, OSError, ValueError) as exc:
        gates = {"ready": False, "blockers": [str(exc)], "causes": [], "repeated_attempts": []}
    review = output.get("phase_review", {})
    feedback = {row["id"]: row for row in review.get("feedback", [])}
    for identity, ref in stage.get("review_feedback", {}).items():
        if identity not in feedback:
            feedback[identity] = store.resolve(ref)
    result["ready"] = gates["ready"]
    result["history"] = (evidence.review_history_page(workspace, head["history_ref"], limit=4)
                         if head.get("history_ref") else {"entries": [], "next": None})
    groups = {key: review.get(key, []) for key in ("dor", "dod", "run_dod", "uncertainties")}
    groups.update(feedback=list(feedback.values()), artifacts=artifacts,
                  causes=gates.get("causes", review.get("causes", [])),
                  repeated_attempts=gates.get("repeated_attempts", []), blockers=gates.get("blockers", []))
    for key, values in groups.items():
        selected: list[Any] = []
        for row in values[:6]:
            if len(encode([*selected, row])) > 3072:
                break
            selected.append(deepcopy(row))
        result[key] = {"total": len(values), "items": selected, "omitted": len(values) - len(selected)}
    # Holds carry actual source text, but the full immutable event stays in state.
    if result["hold"] and len(encode(result["hold"])) > 2048:
        hold = result["hold"]
        result["hold"] = {"active": hold.get("active"), "event_id": hold.get("event_id"),
                          "reason": str(hold.get("reason", ""))[:512], "source_excerpt_omitted": True}
    return result


def collection(store: Store, values: list[Any], kind: str, limit: int = 4) -> dict[str, Any]:
    return {"total": len(values), "items": values[:limit],
            "details": store.put(kind, values) if len(values) > limit else None}


def view(store: Store, binding: dict[str, Any], phase: str, task_ids: list[str],
         criteria: list[str], authority: dict[str, Any], inputs: list[dict[str, Any]],
         coverage: dict[str, Any], *,
         _prepared_refs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    w.require(phase in PHASE_BYTES, "invalid_context", "Unknown context phase.")
    refs: dict[str, dict[str, Any]] = {}
    bodies: dict[str, Any] = {}
    for item in sorted(inputs, key=lambda x: (not x.get("required", True), x.get("priority", 1), x["id"])):
        ref = (_prepared_refs[item["id"]] if _prepared_refs is not None
               else store.put(item.get("kind", "input"), item["body"]))
        refs[item["id"]] = ref
        bodies[item["id"]] = item["body"]
    required = [{"id": item["id"], "ref": refs[item["id"]]}
                for item in inputs if item.get("required", True)]
    required.sort(key=lambda x: x["id"])
    source_key = digest({"binding": binding, "refs": refs, "authority": authority,
                         "delivery": [(item["id"], item.get("required", True), item.get("priority", 1)) for item in inputs]})
    result: dict[str, Any] = {"schema": "taskplane.context-view/v1", "binding": binding,
              "source_key": source_key, "phase": phase,
              "task_ids": collection(store, task_ids, "task-ids", 16),
              "criteria": collection(store, criteria, "criteria", 16),
              "authority_ref": store.put("authority", authority),
              "required_inputs": collection(store, required, "required-inputs", 0),
              "inline": {}, "references": collection(store, [{"id": k, "ref": v} for k,v in refs.items()], "input-index", 0),
              "omissions": [], "coverage": coverage,
              "budget": {"limit_bytes": PHASE_BYTES[phase], "overflow": False}}
    inline: dict[str, Any] = {}
    for item in sorted(inputs, key=lambda x: (not x.get("required", True), x.get("priority", 1), x["id"])):
        key = item["id"]
        # Repeated bodies are referenced once, not repeatedly supplied inline.
        if any(refs[other]["sha256"] == refs[key]["sha256"] for other in inline):
            continue
        candidate = {**result, "inline": {**inline, key: bodies[key]}}
        returned = {refs[row['id']]['sha256'] for row in inputs
                    if row.get('required', True) and row['id'] in candidate['inline']}
        # Leave room both for this consume receipt and for a later maximum-size
        # page. Hundreds of tiny inline roots otherwise strand omitted bodies.
        reserve = max(4096, 1024 + 67 * len(returned))
        if len(returned) <= 100 and len(encode(signed(candidate))) <= PHASE_BYTES[phase] - reserve:
            inline[key] = bodies[key]
    result["inline"] = inline
    result["omissions"] = [{"reason": "Read verified input references for bodies outside the inline budget.",
                            "count": len(refs) - len(inline)}] if len(inline) != len(refs) else []
    result["budget"]["overflow"] = bool(result["omissions"])
    w.require(len(encode(signed(result))) <= PHASE_BYTES[phase], "context_overflow",
              "Required context metadata cannot fit the phase budget.")
    return signed(result)


def summary(store: Store, payload: dict[str, Any], action: str,
            context: dict[str, Any] | None = None) -> dict[str, Any]:
    state = payload.get("workflow", payload)
    visits = state.get("visits", [])
    stage = visits[state.get("index", 0)] if visits else {}
    binding = {k: state.get(k) for k in ("workspace", "root", "run", "revision")}
    binding.update(visit=stage.get("id"), pending_checkpoint=state.get("pending_checkpoint"))
    status = payload.get("status", state.get("status", "unknown"))
    errors = [str(x) for x in payload.get("evidence_errors", [])]
    if payload.get("reason"):
        errors.insert(0, str(payload.get("detail", payload["reason"])))
    blocking = status in {"blocked", "capability_blocked", "stale", "cancelled", "rejected"}
    phase = state.get("phase") or stage.get("phase") or payload.get("phase")
    next_action = ("Resolve the reported refusal before continuing." if blocking else
                   "Initialize the requested scoped workflow." if status in {"no_workflow", "available", "legacy_unverified"} else
                   "Request the human decision for this checkpoint." if status == "awaiting_human_approval" else
                   "Advance to the next authorized phase or finish the accepted route." if status == "approved" else
                   "Finished; no phase work remains." if state.get("finished") else
                   "Consume current context, complete phase evidence, then submit for approval.")
    details = store.put("command-result", payload)
    from .workflow_evidence import outcome_summary
    outcomes = outcome_summary(state) if visits else {}
    result: dict[str, Any] = {"schema": "taskplane.command-summary/v1", "action": action, "status": status,
              "reason": payload.get("reason"), "detail": str(payload.get("detail", ""))[:512],
               "storage": state.get("storage"), "archived": state.get("archived", False),
              "binding": binding, "phase": phase, "run": state.get("run"),
              "revision": state.get("revision"),
              "outcomes": {"current_checks": collection(store, outcomes.get("current_checks", []), "current-checks", 0),
                           "unresolved_original_findings": len(outcomes.get("unresolved_findings", [])),
                           "details": store.put("original-findings", outcomes) if outcomes else None},
              "tokens": payload.get("tokens"), "native_tokens": payload.get("native_tokens"),
              "token_coverage": payload.get("token_coverage"),
              "approval": {"status": stage.get("decision", status), "decisions": len(state.get("decisions", {})),
                           "policy_mode": state.get("approval_policy", {}).get("mode", "manual"),
                           "policy_digest": state.get("approval_policy", {}).get("digest"),
                           "conditions": state.get("approval_policy", {}).get("conditions", []),
                           "allowed_phases": state.get("approval_policy", {}).get("allowed_phases", []),
                           "stop_phases": state.get("approval_policy", {}).get("stop_phases", [])},
              "next_action": next_action, "context": context or {"status": "not_prepared"},
              "artifacts": {"dashboard": payload.get("dashboard"), "details": details},
              "coverage": {"workflow": state.get("coverage", {}),
                           "graph": (payload.get("graph") or payload.get("source_graph") or {}).get("status", "unknown"),
                           "tokens": payload.get("token_coverage", "unknown")},
              "errors": {"blocking": blocking, "reason": payload.get("reason"),
                         **collection(store, errors, "errors", 0)}, "details": details}
    if visits and state.get("phase_contract"):
        review = review_projection(store.workspace, state)
        result["phase_review"] = {key: review.get(key) for key in
            ("contract", "status", "revision", "ready", "draft_ref", "history_ref")}
        result["phase_review"]["hold_active"] = bool((review.get("hold") or {}).get("active"))
        result["phase_review"]["blockers"] = review["blockers"]
        result["phase_review"]["details"] = store.put("phase-review-summary", review)
        if not review["ready"]:
            result["next_action"] = "Resolve the phase review blockers; an active user hold requires explicit release. Readiness is separate from approval."
    accounting = payload.get("phase_usage", {}).get("accounting")
    if accounting:
        result["usage_accounting"] = {**accounting,
            "intervals": len(payload["phase_usage"].get("intervals", [])),
            "details": store.put("usage-accounting", payload["phase_usage"])}
    if len(encode(result)) > COMMAND_BYTES:
        for field in ("tokens", "native_tokens", "token_coverage"):
            if result[field] is not None:
                result[field] = {"status": "referenced", "details": store.put(field, result[field])}
        result["coverage"] = {"status": "See verified details", "details": store.put("coverage", result["coverage"])}
        result["context"] = {"status": "referenced", "details": store.put("context-status", result["context"])}
    w.require(len(encode(result)) < COMMAND_BYTES, "context_overflow",
              "Required command identity exceeds the response budget; use explicit full detail.")
    if state.get("visits"):
        from .context_handoff import binding as current_binding
        references = [details]
        if result["outcomes"].get("details"):
            references.append(result["outcomes"]["details"])
        for section in (result["errors"], result["coverage"], result["context"], result.get("usage_accounting"),
                      result["tokens"], result["native_tokens"], result["token_coverage"], result.get("phase_review")):
            if not isinstance(section, dict):
                continue
            if section.get("details"):
                references.append(section["details"])
        read_binding = current_binding(state)
        if context and context.get("source_key"):
            read_binding["source_key"] = context["source_key"]
        store.register(read_binding, references)
    return result
