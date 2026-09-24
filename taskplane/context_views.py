"""Deterministic bounded transport views; full evidence stays behind verified refs."""
from __future__ import annotations

from typing import Any

from .context import Store, digest, encode, signed
from . import workflow as w

PHASE_BYTES = dict(zip(w.PHASES, (16384, 32768, 16384, 65536, 32768, 32768, 16384)))
COMMAND_BYTES = 16384


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
    for item in sorted(inputs, key=lambda x: (not x.get("required", True), x["id"])):
        ref = (_prepared_refs[item["id"]] if _prepared_refs is not None
               else store.put(item.get("kind", "input"), item["body"]))
        refs[item["id"]] = ref
        bodies[item["id"]] = item["body"]
    required = [{"id": item["id"], "ref": refs[item["id"]]}
                for item in inputs if item.get("required", True)]
    required.sort(key=lambda x: x["id"])
    source_key = digest({"binding": binding, "refs": refs, "authority": authority})
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
    for item in sorted(inputs, key=lambda x: (not x.get("required", True), x["id"])):
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
    result: dict[str, Any] = {"schema": "taskplane.command-summary/v1", "action": action, "status": status,
              "reason": payload.get("reason"), "detail": str(payload.get("detail", ""))[:512],
               "storage": state.get("storage"), "archived": state.get("archived", False),
              "binding": binding, "phase": phase, "run": state.get("run"),
              "revision": state.get("revision"),
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
