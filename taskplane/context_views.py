"""Deterministic bounded transport views; full evidence stays behind verified refs."""
from __future__ import annotations

from typing import Any

from .context import Store, digest, encode, signed, count
from . import workflow as w

PHASE_BYTES = dict(zip(w.PHASES, (16384, 32768, 16384, 65536, 32768, 32768, 16384)))
COMMAND_BYTES = 16384


def collection(store: Store, values: list[Any], kind: str, limit: int = 4) -> dict[str, Any]:
    return {"total": len(values), "items": values[:limit],
            "details": store.put(kind, values) if len(values) > limit else None}


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
    harness = payload.get("harness")
    initialization = ({key: harness.get(key) for key in
                       ("status", "observed_at", "run_created", "hook_observed", "binding", "parent_hook")}
                      if isinstance(harness, dict) else None)
    if isinstance(initialization, dict) and (
            initialization.get("status") in {"initialization_pending", "initialization_stale", "unavailable"}
            or initialization.get('run_created') and
            (initialization.get('parent_hook') or {}).get('status') != 'ready'):
        initialization['detail'] = ('Missing automatic callback coverage is advisory for root work. '
                                    'Controller checks remain active; native workers still require readiness.')
    phase = state.get("phase") or stage.get("phase") or payload.get("phase")
    next_action = ("Resolve the reported refusal before continuing." if blocking else
                   "Initialize the requested scoped workflow." if status in {"no_workflow", "available", "legacy_unverified"} else
                   "Request the human decision for this checkpoint." if status == "awaiting_human_approval" else
                   "Advance to the next authorized phase or finish the accepted route." if status == "approved" else
                   "Finished; no phase work remains." if state.get("finished") else
                   "Read the current brief, complete phase evidence, then submit for approval.")
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
              "initialization": initialization,
              "artifacts": {"dashboard": payload.get("dashboard"), "details": details},
              "coverage": {"workflow": state.get("coverage", {}),
                           "graph": (payload.get("graph") or payload.get("source_graph") or {}).get("status", "unknown"),
                           "tokens": payload.get("token_coverage", "unknown")},
              "errors": {"blocking": blocking,
                         "reason": payload.get("reason"),
                         **collection(store, errors, "errors", 0)}, "details": details}
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
                      result["tokens"], result["native_tokens"], result["token_coverage"]):
            if not isinstance(section, dict):
                continue
            if section.get("details"):
                references.append(section["details"])
        read_binding = current_binding(state)
        if context and context.get("source_key"):
            read_binding["source_key"] = context["source_key"]
        store.register(read_binding, references)
    return result
