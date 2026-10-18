"""Run-bound, observed authorization for optional automatic phase decisions.

This is cooperative workflow policy, not host authentication or tool permission.
No policy executes commands, approves missing evidence, or extends a route.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from . import workflow as w
from .primitives import content_fingerprint

SCHEMA = "taskplane.approval-policy/v1"
ASSESSMENT = "taskplane.policy-assessment/v1"
ASSESSMENT_LIMIT = 64 * 1024


def read_assessment(workspace: Path, filename: str = "", inline: str | None = None) -> dict[str, Any]:
    """Accept a bounded control payload without permitting sealed workspace writes."""
    from . import workflow_evidence as evidence
    import os
    import stat
    w.require(bool(filename) != (inline is not None), "invalid_evidence",
              "Supply exactly one assessment file or inline JSON object.")
    if inline is not None:
        raw = inline.encode("utf-8")
    else:
        target = evidence.path(workspace, filename)
        try:
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                w.require(stat.S_ISREG(info.st_mode) and info.st_size <= ASSESSMENT_LIMIT,
                          "invalid_evidence", "Assessment must be a regular file of at most 64 KiB.")
                raw = stream.read(ASSESSMENT_LIMIT + 1)
        except OSError as exc:
            raise w.Refusal("invalid_evidence", f"Assessment unavailable: {filename}") from exc
    w.require(len(raw) <= ASSESSMENT_LIMIT, "invalid_evidence", "Assessment exceeds 64 KiB.")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise w.Refusal("invalid_evidence", "Assessment must be a JSON object.") from None
    w.require(isinstance(value, dict), "invalid_evidence", "Assessment must be a JSON object.")
    return dict(value)


def policy_binding(state: dict[str, Any]) -> dict[str, Any]:
    return {**{k: state[k] for k in ("workspace", "root", "run", "revision")},
            "scope_digest": content_fingerprint(state["scope"])}


def _text(value: Any, maximum: int = 4096) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


DECISIONS = {"approved", "changes_requested", "rejected", "cancelled"}


def validate_checkpoint_consent(question: str, answer: str | None, *, phase: str,
                                binding: dict[str, Any], candidate: str | None = None,
                                reaction: bool = False) -> str:
    """Validate a semantic candidate AFTER original human transport linkage.

    The orchestrator interprets the complete user response in context. Runtime
    code checks provenance, target, chronology and replay; it does not classify
    natural language or authenticate the orchestrator's interpretation.
    """
    w.require(_text(question, 16384) and phase in w.PHASES and isinstance(binding, dict)
              and _text(binding.get("checkpoint")), "invalid_evidence",
              "Preserve the actual checkpoint presentation and target.")
    w.require(candidate in DECISIONS and (reaction and answer is None or _text(answer)),
              "invalid_evidence", "A complete human observation needs an explicit semantic decision candidate.")
    w.require(not reaction or candidate == "approved", "invalid_evidence",
              "A verified approval reaction cannot request a different decision.")
    assert isinstance(candidate, str)
    return candidate


def contextual_consent(state: dict[str, Any], request: dict[str, Any], observed: datetime,
                       *, relay: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Bind an exact brief choice to the actual previously presented policy.

    This optional envelope is observed conversational evidence, just like the
    decision envelope. It cannot turn a generic approval into a policy without
    retaining the question, proposed instructions and exact policy fields.
    """
    context = request.get("choice_context")
    if context is None:
        return None
    w.require(request.get("mode") == "autonomous" and isinstance(context, dict)
              and context.get("schema") == "taskplane.policy-choice/v1",
              "decision_provenance", "Policy choice requires its complete presented context.")
    proposal = {key: request.get(key) for key in ("binding", "mode", "allowed_phases", "stop_phases", "conditions")}
    w.require(context.get("proposal") == proposal, "decision_binding",
              "Presented policy differs from this run's requested policy.")
    source = context.get("source", {})
    origin = relay["source_thread"] if relay else state["root"]
    w.require(isinstance(source, dict) and source.get("conversation") == origin
              and source.get("actor") == "assistant" and _text(source.get("reference"), 512)
              and _text(context.get("question")) and _text(context.get("instructions")),
              "decision_provenance", "Policy choice needs the actual question, instructions and presentation source.")
    from .workflow_local import timestamp
    w.require(timestamp(state["started_at"]) <= timestamp(source.get("observed_at")) < observed,
              "decision_chronology", "The automatic-policy question must follow run start and precede the response.")
    w.require(context.get("selected_label") == request.get("excerpt")
              and request.get("semantic_choice") == "approved", "decision_provenance",
              "Retain the observed response and explicit policy semantic candidate.")
    return deepcopy(dict(context))


def authorize(state: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    w.require(state.get("profile") == "native_workflow", "unsupported_authority",
              "Observed automatic authorization is available only in native_workflow.")
    w.require(not state["finished"], "approval_required", "An ended run cannot change its policy.")
    from . import delegated_approval
    w.require(request.get("schema") in {SCHEMA, delegated_approval.POLICY_SCHEMA},
              "invalid_evidence", "Approval policy schema is missing.")
    event = request.get("event_id")
    w.require(_text(event, 512), "invalid_evidence", "Policy needs an actual user event reference.")
    events = state.get("policy_events", {})
    digest = content_fingerprint(request)
    if event in events:
        w.require(events[event] == digest, "stale_checkpoint", "Conflicting policy event replay.")
        return deepcopy(state)
    w.require(content_fingerprint(request.get("binding")) == content_fingerprint(policy_binding(state)), "stale_checkpoint",
              "Policy must bind the current workspace, task, run, scope and revision.")
    relay = (delegated_approval.verify(state, request)
             if request.get("schema") == delegated_approval.POLICY_SCHEMA else None)
    w.require(relay is not None or "relay" not in request, "invalid_evidence",
              "Delegated observations require the explicit v2 policy schema.")
    source = request.get("source")
    w.require(isinstance(source, dict) and (relay is not None or (
              source.get("kind") in ("conversation", "native_prompt")
              and source.get("conversation") == state["root"])) and source.get("actor") == "user"
              and source.get("automatic") is False and _text(source.get("reference"), 512)
              and request.get("recorder") in ("root_orchestrator", "native_prompt_hook"),
              "invalid_evidence", "Policy requires observed user provenance, not an agent/tool event.")
    assert isinstance(source, dict)
    try:
        observed = datetime.fromisoformat(str(source.get("observed_at", "")).replace("Z", "+00:00"))
        started = datetime.fromisoformat(state["started_at"].replace("Z", "+00:00"))
        w.require(observed.tzinfo is not None and started.tzinfo is not None
                  and observed <= datetime.now(observed.tzinfo), "invalid_evidence", "Invalid authorization time.")
        # Instructions can precede start, but cannot come from an older run.
        w.require(request.get("request_reference") == state.get("request_provenance", {}).get("reference")
                  or observed >= started, "invalid_evidence", "Pre-start authorization must reference this run's request.")
    except (ValueError, TypeError):
        raise w.Refusal("invalid_evidence", "Policy needs a valid observed timestamp.") from None
    excerpt = request.get("excerpt")
    w.require(_text(excerpt), "invalid_evidence", "Preserve the actual additional instructions.")
    assert isinstance(excerpt, str)
    mode = request.get("mode")
    w.require(mode in ("manual", "autonomous"), "invalid_evidence", "Choose manual or autonomous approval.")
    choice_context = contextual_consent(state, request, observed, relay=relay)
    w.require(request.get("semantic_choice") == "approved", "approval_required",
              "Explicit policy authorization needs the orchestrator's semantic candidate and actual human source.")
    allowed, stops = request.get("allowed_phases", []), request.get("stop_phases", [])
    w.require(isinstance(allowed, list) and isinstance(stops, list)
              and all(isinstance(p, str) and p in w.PHASES for p in allowed + stops)
              and len(set(allowed)) == len(allowed) and len(set(stops)) == len(stops)
              and (bool(allowed) if mode == "autonomous" else not allowed),
              "invalid_evidence", "Declare exact allowed phases and mandatory stop phases.")
    conditions = request.get("conditions", [])
    w.require(isinstance(conditions, list) and len(conditions) <= 32, "invalid_evidence", "Invalid policy conditions.")
    ids = {"user_instructions"}
    for condition in conditions:
        w.require(isinstance(condition, dict) and _text(condition.get("id"), 80)
                  and condition["id"] not in ids and condition.get("kind") in ("observed", "required_check")
                  and _text(condition.get("instruction"))
                  and (condition["kind"] != "required_check" or _text(condition.get("check"), 200)),
                  "invalid_evidence", "Conditions need unique IDs, instructions and a supported kind.")
        ids.add(condition["id"])
    s = deepcopy(state)
    history = s.setdefault("policy_history", [])
    w.require(len(history) < 256, "state_unavailable", "Policy history limit reached; do not reset active state.")
    policy = {"schema": SCHEMA, "id": event, "revision": len(history) + 1, "mode": mode,
              "binding": policy_binding(state), "authorized_scope": deepcopy(state["scope"]),
              "allowed_phases": allowed, "stop_phases": stops,
              "conditions": [{"id": "user_instructions", "kind": "observed",
                              "instruction": choice_context["instructions"] if choice_context else excerpt}] + conditions,
              "provenance": {"source": deepcopy(source), "recorder": request["recorder"], "excerpt": excerpt},
              "assurance": "observed", "request_digest": digest}
    if choice_context is not None:
        policy["provenance"]["choice_context"] = choice_context
    if relay is not None:
        policy["provenance"].update(schema=delegated_approval.POLICY_SCHEMA, relay=relay)
        policy["source_assurance"] = "relayed_observation"
        policy["host_attested"] = False
    policy["digest"] = content_fingerprint(policy)
    history.append(policy)
    s["approval_policy"] = deepcopy(policy)
    s["policy_suspension"] = None
    s.setdefault("policy_events", {})[event] = digest
    s["revision"] += 1
    return s


def suspend(state: dict[str, Any], reason: str) -> None:
    if state.get("approval_policy", {}).get("mode") == "autonomous":
        state["policy_suspension"] = reason


def validate_history(state: dict[str, Any]) -> None:
    history = state.get("policy_history", [])
    w.require(isinstance(history, list) and len(history) <= 256, "state_unavailable", "Invalid policy history.")
    for index, policy in enumerate(history):
        w.require(isinstance(policy, dict) and policy.get("schema") == SCHEMA
                  and policy.get("revision") == index + 1 and policy.get("assurance") == "observed"
                  and policy.get("digest") == content_fingerprint({k:v for k,v in policy.items() if k != "digest"})
                  and all(policy.get("binding", {}).get(k) == state[k] for k in ("workspace", "root", "run")),
                  "state_unavailable", "Corrupt or foreign policy history.")
    if history:
        w.require(state.get("approval_policy") == history[-1], "state_unavailable", "Policy version mismatch.")
    else:
        w.require(not state.get("approval_policy"), "state_unavailable", "Policy has no history.")


def decision_authorized(state: dict[str, Any], decision: dict[str, Any]) -> bool:
    if decision.get("human") is True and decision.get("automatic") is False:
        return bool(decision.get("kind", "human") == "human")
    if state.get("profile") != "native_workflow" or decision.get("kind") != "policy":
        return False
    policy = next((p for p in state.get("policy_history", []) if p["digest"] == decision.get("policy_digest")), None)
    assessment = decision.get("assessment", {})
    stage = next((v for v in state["visits"] if v["id"] == decision.get("binding", {}).get("visit")), None)
    return bool(policy and stage and policy["mode"] == "autonomous"
                and stage["phase"] in policy["allowed_phases"] and stage["phase"] not in policy["stop_phases"]
                and decision.get("human") is False and decision.get("automatic") is True
                and decision.get("choice") == "approved" and assessment.get("binding") == decision.get("binding")
                and assessment.get("policy_digest") == policy["digest"]
                and {c["id"] for c in assessment.get("conditions", [])} == {c["id"] for c in policy["conditions"]}
                and all(c.get("status") == "pass" for c in assessment.get("conditions", [])))


def automatic_decision(state: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    w.require(state.get("profile") == "native_workflow", "unsupported_authority", "Automatic approval is unavailable in protected_host.")
    w.require(assessment.get("schema") == ASSESSMENT and isinstance(assessment.get("binding"), dict),
              "invalid_evidence", "Supply a checkpoint-bound policy assessment.")
    event = "policy:" + str(assessment.get("policy_digest")) + ":" + str(assessment["binding"].get("checkpoint"))
    if event in state["decisions"]:
        old = state["decisions"][event]
        w.require(old.get("assessment") == assessment, "stale_checkpoint", "Conflicting automatic decision replay.")
        return deepcopy(old)
    policy = state.get("approval_policy") or {}
    w.require(policy.get("mode") == "autonomous" and not state.get("policy_suspension"),
              "approval_required", state.get("policy_suspension") or "Manual approval is active; explicit authorization is required.")
    stage = w.current(state)
    w.require(stage["decision"] == "awaiting_human_approval" and stage["packet"], "approval_required", "Submit evidence before automatic approval.")
    w.require(stage["phase"] in policy["allowed_phases"] and stage["phase"] not in policy["stop_phases"],
              "approval_required", "This phase requires a human checkpoint under the current policy.")
    packet = stage["packet"]
    w.require(content_fingerprint(assessment["binding"]) == content_fingerprint(w.binding(state, packet)) and assessment.get("policy_digest") == policy["digest"],
              "stale_checkpoint", "Assessment belongs to an old policy or checkpoint.")
    outer = policy["authorized_scope"]
    w.require(state["scope"]["criteria"] == outer["criteria"]
              and state["scope"].get("verification_inputs", []) == outer.get("verification_inputs", [])
              and all(set(paths) <= set(outer["paths"][phase]) for phase,paths in state["scope"]["paths"].items())
              and not packet.get("route_change"), "approval_required", "Scope or route changes require renewed human authorization.")
    output = packet["output"]
    from .workflow_evidence import effective_checks
    checks = effective_checks(state, output)
    if stage["phase"] == "build":
        w.require(checks and all(c.get("status") == "pass" for c in checks if c.get("required", True))
                  and not output.get("known_gaps"),
                  "approval_required", "Build checks or unresolved gaps require review.")
    if stage["phase"] == "evaluate":
        w.require(all(c.get("status") == "pass" for c in output["criterion_results"].values())
                  and not output.get("unknowns_and_failures"), "approval_required", "Evaluation is failed or unknown.")
    if stage["phase"] == "engineering":
        w.require(not any(f.get("blocking") or str(f.get("severity", "")).casefold() in
                          ("p0", "p1", "critical", "high", "blocker") for f in output.get("findings", [])),
                  "approval_required", "Unresolved Engineering blockers require review.")
    items = assessment.get("conditions")
    w.require(isinstance(items, list) and len(items) == len(policy["conditions"])
              and all(isinstance(c, dict) for c in items)
              and {c.get("id") for c in items} == {c["id"] for c in policy["conditions"]},
              "invalid_evidence", "Assess every user condition, including the original instructions.")
    assert isinstance(items, list)
    for c in items:
        w.require(c.get("status") == "pass", "approval_required", "A required condition is failed or unknown: " + str(c.get("id")))
        refs = c.get("evidence")
        w.require(_text(c.get("explanation")) and isinstance(refs, list) and refs
                  and all(isinstance(p, str) and p in packet["manifest"] for p in refs),
                  "invalid_evidence", "Condition assessment needs an explanation and sealed evidence files.")
        rule = next(rule for rule in policy["conditions"] if rule["id"] == c["id"])
        if rule["kind"] == "required_check":
            w.require(any(check.get("name") == rule["check"] and check.get("status") == "pass" for check in checks),
                      "approval_required", "Named required check is absent or not passing.")
    return {"event_id": event, "kind": "policy", "human": False, "automatic": True,
            "choice": "approved", "binding": w.binding(state, packet), "policy_digest": policy["digest"],
            "policy_id": policy["id"], "policy_revision": policy["revision"], "assurance": "observed",
            "provenance": deepcopy(policy["provenance"]), "assessment": deepcopy(assessment)}
