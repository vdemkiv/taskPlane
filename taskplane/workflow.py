"""Pure workflow rules. Native authority and artifact I/O belong to adapters."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
import uuid

from .primitives import content_fingerprint

PHASES = ("product", "design", "plan", "build", "evaluate", "engineering", "retro")
ENTRY_PHASES = ("product", "design", "engineering")
OUTPUT_FIELDS = {
    "product": ("scope", "acceptance_criteria", "non_goals", "dependencies", "task_outline", "finding_references"),
    "design": ("approach", "alternatives", "interfaces_and_state", "authority_boundaries", "failure_and_recovery", "graph_impact", "acceptance_test_map"),
    "plan": ("task_dag", "ownership", "write_scope", "acceptance_coverage", "verification_strategy", "integration_order"),
    "build": ("change_inventory", "task_acceptance_map", "build_checks", "known_gaps"),
    "evaluate": ("criterion_results", "source_test_fingerprints", "regression_evidence", "unknowns_and_failures"),
    "engineering": ("findings", "severity", "lens_coverage", "source_locations", "requirements_comparison", "remaining_risk"),
    "retro": ("accepted_outcome", "actual_verification", "deferred_items_and_owners", "lessons"),
}
BINDING_FIELDS = ("workspace", "root", "run", "visit", "checkpoint", "revision",
                  "manifest_digest", "scope_digest")
PLANNING_CONTRACT = "implementation/v1"


class Refusal(ValueError):
    """A mandatory workflow refusal, never a telemetry warning."""

    def __init__(self, reason: str, detail: str):
        self.reason = reason
        self.detail = detail
        super().__init__(detail)

    def result(self) -> dict[str, Any]:
        return {"status": "capability_blocked" if self.reason == "unsupported_authority" else "blocked",
                "reason": self.reason, "detail": self.detail}


def require(condition: object, reason: str, detail: str) -> None:
    if not condition:
        raise Refusal(reason, detail)


def visit(phase: str) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex, "phase": phase, "work": "working",
            "decision": "not_requested", "packet": None, "superseded": False}


def scope_preflight(scope: dict[str, Any]) -> dict[str, Any]:
    """Check declared implementation feasibility without guessing intent from suffixes.

    Older scopes retain their contract; absence is unknown feasibility, not proof
    that a requested source repair can be performed in report-only paths.
    """
    contract = scope.get("planning_contract")
    require(contract in (None, PLANNING_CONTRACT), "invalid_evidence", "Unknown planning contract.")
    intent = scope.get("implementation_intent")
    if contract is None and intent is None:
        return {"status": "legacy_unknown", "detail": "Implementation feasibility was not declared."}
    require(contract == PLANNING_CONTRACT and isinstance(intent, dict), "invalid_evidence",
            "implementation/v1 requires an explicit implementation_intent before the first checkpoint.")
    assert isinstance(intent, dict)
    require(intent.get("kind") in ("source", "documentation"), "invalid_evidence",
            "Implementation intent must explicitly select source or documentation work.")

    def paths(key: str, *, required: bool = True) -> set[str]:
        values = intent.get(key)
        require(isinstance(values, list) and (bool(values) or not required)
                and all(isinstance(value, str) and value.strip() for value in values)
                and len(values) == len(set(values)), "invalid_evidence",
                f"Implementation intent needs exact unique {key}.")
        assert isinstance(values, list)
        return set(values)

    implementation = paths("implementation_paths")
    tests = paths("test_paths", required=intent["kind"] == "source")
    build = set(scope["paths"]["build"])
    require(implementation <= build and tests <= build | set(scope.get("verification_inputs", [])),
            "scope_violation", "Implementation and test paths must be authorized before Product; Plan cannot widen them.")
    outputs = intent.get("build_outputs")
    require(isinstance(outputs, dict) and set(outputs) == {"packet", "report", "verification_history"}
            and all(isinstance(value, str) and value.strip() for value in outputs.values())
            and len(set(outputs.values())) == 3, "invalid_evidence",
            "Implementation scope needs distinct Build packet, report and verification history paths up front.")
    assert isinstance(outputs, dict)
    require(set(outputs.values()) <= build and not set(outputs.values()) & (implementation | tests),
            "scope_violation", "Build evidence outputs cannot substitute for implementation or test paths.")
    coverage = intent.get("criteria")
    require(isinstance(coverage, dict) and set(coverage) == set(scope["criteria"]), "invalid_evidence",
            "Implementation intent must map every stable acceptance criterion before Product.")
    assert isinstance(coverage, dict)
    for criterion, row in coverage.items():
        require(isinstance(row, dict) and isinstance(row.get("paths"), list) and row["paths"]
                and all(isinstance(value, str) for value in row["paths"])
                and set(row["paths"]) <= implementation
                and isinstance(row.get("verification"), str) and row["verification"].strip(),
                "invalid_evidence", f"Implementation criterion {criterion} needs intended paths and verification.")
    return {"status": "declared_feasible", "contract": contract, "kind": intent["kind"],
            "detail": "Declared paths cover implementation, tests and Build evidence; behavior still requires verification."}


def validate_scope(scope: dict[str, Any]) -> None:
    require(isinstance(scope, dict), "invalid_evidence", "Scope must be an object.")
    require(scope.get("execution_contract") in (None, "native-default/v1"),
            "invalid_evidence", "Unknown execution contract.")
    criteria = scope.get("criteria")
    require(isinstance(criteria, list) and criteria and
            all(isinstance(c, str) and c.strip() for c in criteria) and len(set(criteria)) == len(criteria),
            "invalid_evidence", "Scope needs unique acceptance criterion IDs.")
    paths = scope.get("paths")
    require(isinstance(paths, dict) and set(paths) == set(PHASES),
            "invalid_evidence", "Declare exact write paths for all seven phases.")
    assert isinstance(paths, dict)
    for phase in PHASES:
        values = paths[phase]
        require(isinstance(values, list) and all(isinstance(p, str) and p for p in values),
                "invalid_evidence", f"Invalid write scope for {phase}.")
    require(isinstance(scope.get("verification_inputs", []), list)
            and all(isinstance(p, str) and p for p in scope.get("verification_inputs", [])),
            "invalid_evidence", "Verification inputs must be a path list.")
    scope_preflight(scope)


def new_state(workspace: str, root: str, run: str, scope: dict[str, Any], *,
              entry: str = "product", standalone: bool = False) -> dict[str, Any]:
    require(entry in ENTRY_PHASES and (standalone or entry == "product"),
            "approval_required", "Full delivery begins at Product; standalone entry cannot grant Build.")
    validate_scope(scope)
    return {"schema": "taskplane.workflow/v1", "workspace": workspace, "root": root,
            "run": run, "revision": 0, "scope": deepcopy(scope),
            "visits": [visit(p) for p in ((entry,) if standalone else PHASES)],
            "index": 0, "decisions": {}, "history": [], "finished": False}


def current(state: dict[str, Any]) -> dict[str, Any]:
    return dict(state["visits"][state["index"]])


def accepted_plan(state: dict[str, Any]) -> dict[str, Any]:
    plans = [v for v in state["visits"][:state["index"]]
             if v["phase"] == "plan" and not v["superseded"]]
    require(plans and plans[-1]["decision"] == "approved" and plans[-1]["packet"],
            "approval_required", "A current accepted Plan is required for Build or repair.")
    return dict(plans[-1])


def validate_state(state: dict[str, Any]) -> None:
    """Reject structurally corrupt protected records without inventing repairs."""
    from . import workflow_approval as approval
    validate_scope(state["scope"])
    approval.validate_history(state)
    require(type(state["revision"]) is int and state["revision"] >= 0
            and type(state["index"]) is int and isinstance(state["visits"], list)
            and 0 <= state["index"] < len(state["visits"])
            and isinstance(state["decisions"], dict) and isinstance(state["history"], list)
            and type(state["finished"]) is bool,
            "state_unavailable", "Invalid protected state fields.")
    ids = set()
    for stage in state["visits"]:
        require(isinstance(stage, dict) and isinstance(stage.get("id"), str) and stage["id"]
                and stage["id"] not in ids and stage.get("phase") in PHASES
                and stage.get("work") in ("working", "ready", "evidence_blocked")
                and stage.get("decision") in ("not_requested", "awaiting_human_approval", "approved",
                                              "changes_requested", "rejected", "cancelled", "stale")
                and type(stage.get("superseded")) is bool and "packet" in stage,
                "state_unavailable", "Invalid protected visit.")
        ids.add(stage["id"])
        packet = stage["packet"]
        if packet is None:
            require(stage["decision"] == "not_requested", "state_unavailable", "Decision has no packet.")
            continue
        require(isinstance(packet, dict) and packet.get("phase") == stage["phase"]
                and packet.get("visit") == stage["id"] and isinstance(packet.get("checkpoint"), str)
                and all(isinstance(packet.get(k), dict) for k in ("manifest", "source_manifest", "context", "output")),
                "state_unavailable", "Invalid protected packet.")
        if stage["decision"] in ("approved", "changes_requested", "rejected", "cancelled"):
            require(any(isinstance(d, dict) and approval.decision_authorized(state, d)
                        and d.get("choice") == stage["decision"] and isinstance(d.get("binding"), dict)
                        and d["binding"].get("visit") == stage["id"]
                        and d["binding"].get("manifest_digest") == content_fingerprint(packet)
                        for d in state["decisions"].values()),
                    "state_unavailable", "Stored decision has no matching native record.")
    require(not state["finished"] or all(v["superseded"] or v["decision"] == "approved" for v in state["visits"]),
            "state_unavailable", "Finished route contains unaccepted visits.")
    cancellation = state.get("cancellation")
    if cancellation is not None:
        require(isinstance(cancellation, dict) and isinstance(cancellation.get("event_id"), str),
                "state_unavailable", "Invalid cancellation record.")
        event = state["decisions"].get(cancellation.get("event_id"), {})
        require(cancellation.get("schema") == "taskplane.cancellation/v1"
                and event.get("choice") == "cancelled" and approval.decision_authorized(state, event)
                and cancellation.get("binding") == event.get("binding")
                and current(state)["decision"] == "cancelled" and not state["finished"]
                and state["history"].count({"cancellation": cancellation}) == 1,
                "state_unavailable", "Cancellation must retain its bound decision and append-only history.")


def binding(state: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    return {**{key: state[key] for key in ("workspace", "root", "run", "revision")},
            "visit": current(state)["id"], "checkpoint": packet["checkpoint"],
            "manifest_digest": content_fingerprint(packet),
            "scope_digest": content_fingerprint(state["scope"])}


def submit(state: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    require(not state.get("cancellation"), "approval_required", "This run was cancelled; an explicit new run is required.")
    require(not state["finished"], "approval_required", "This route is already accepted.")
    s = deepcopy(state)
    stage = s["visits"][s["index"]]
    require(packet.get("phase") == stage["phase"] and packet.get("visit") == stage["id"],
            "invalid_evidence", "Output must belong to the current stage visit.")
    if stage["packet"]:
        s["history"].append({"visit": stage["id"], "packet": stage["packet"], "decision": stage["decision"]})
    stage.update(packet=deepcopy(packet), work="ready", decision="awaiting_human_approval")
    from .primitives import content_fingerprint
    stage["submitted_at"] = datetime.now(timezone.utc).isoformat()
    stage["checkpoint_scope_digest"] = content_fingerprint(s["scope"])
    s["revision"] += 1
    stage["packet_revision"] = s["revision"]
    return s


def decide(state: dict[str, Any], verified: dict[str, Any]) -> dict[str, Any]:
    """Consume a profile-validated decision; adapters own its stated assurance."""
    event_id = verified.get("event_id")
    require(isinstance(event_id, str) and event_id, "unsupported_authority", "Native event identity is missing.")
    from . import workflow_approval as approval
    require(approval.decision_authorized(state, verified),
            "unsupported_authority", "Decision needs explicit human approval or a valid run-bound policy.")
    if verified.get("kind") == "policy":
        require(approval.automatic_decision(state, verified["assessment"]) == verified,
                "invalid_evidence", "Automatic decision does not match the validated assessment.")
    if state.get("profile") == "native_workflow":
        require(verified.get("assurance") == "observed" and isinstance(verified.get("provenance"), dict),
                "invalid_evidence", "Native workflow decisions must disclose observed provenance.")
    else:
        require("assurance" not in verified or verified["assurance"] != "observed",
                "unsupported_authority", "Observed local provenance cannot authorize a protected host profile.")
    if event_id in state["decisions"]:
        require(state["decisions"][event_id] == verified, "stale_checkpoint", "Conflicting native event replay.")
        return deepcopy(state)
    stage = current(state)
    choice = verified.get("choice")
    require(not state.get("cancellation") and not state["finished"],
            "approval_required", "An ended run cannot receive new checkpoint decisions.")
    require((stage["decision"] == "awaiting_human_approval"
             or stage["decision"] == "approved" and choice == "cancelled") and stage["packet"],
            "approval_required", "Submit the current stage's evidence before requesting a decision.")
    expected = binding(state, stage["packet"])
    require(verified.get("binding") == expected, "stale_checkpoint",
            "Decision does not match the current root, visit, revision and artifact checkpoint.")
    require(choice in ("approved", "changes_requested", "rejected", "cancelled"),
            "invalid_evidence", "Unknown human decision.")
    s = deepcopy(state)
    stage = s["visits"][s["index"]]
    stage["decision"] = choice
    stage["work"] = "ready" if choice == "approved" else "working"
    s["decisions"][event_id] = deepcopy(verified)
    if choice == "cancelled":
        # Preserve the original approval/event unchanged. This new bound event
        # revokes continuation, including resubmission and stale worker grants;
        # it is not evidence that any native process has terminated.
        cancellation = {"schema": "taskplane.cancellation/v1", "event_id": event_id,
                        "binding": deepcopy(expected)}
        s["cancellation"] = cancellation
        s["history"].append({"cancellation": deepcopy(cancellation)})
    if choice != "approved" or stage["packet"].get("route_change"):
        approval.suspend(s, "Human intervention or route change requires renewed automatic authorization.")
    if choice == "approved" and stage["phase"] == "plan":
        # The accepted Plan narrows the outer requested scope to actual Build grants.
        s["scope"]["paths"]["build"] = list(stage["packet"]["output"]["write_scope"])
        from .primitives import content_fingerprint
        stage["approved_scope_digest"] = content_fingerprint(s["scope"])
    change = stage["packet"].get("route_change")
    if choice == "approved" and change:
        require(stage["phase"] in ("design", "evaluate", "engineering"),
                "invalid_evidence", "This stage cannot extend the delivery route.")
        kind = change["kind"]
        stages: tuple[str, ...]
        if kind == "delivery":
            require(all(v["superseded"] for v in s["visits"][s["index"] + 1:]),
                    "invalid_evidence", "Delivery extension applies only at the end of the current scope.")
            validate_scope(change["scope"])
            # Acceptance ends the standalone scope. Its evidence remains historical;
            # later authorized Build edits must not stale the initiating review.
            stage["superseded"] = True
            s["scope"] = deepcopy(change["scope"])
            stages = PHASES
        else:
            require(kind == "repair" and stage["phase"] in ("evaluate", "engineering"),
                    "invalid_evidence", "Only evaluation or review can propose a scoped repair.")
            plan = accepted_plan(s)
            plan_index = next(i for i, v in enumerate(s["visits"]) if v["id"] == plan["id"])
            require(any(v["phase"] == "build" and not v["superseded"]
                        and v["decision"] == "approved" and v["packet"]
                        for v in s["visits"][plan_index + 1:s["index"]]),
                    "approval_required", "Repair requires an accepted Build after the current Plan.")
            s["scope"]["paths"]["build"] = list(plan["packet"]["output"]["write_scope"])
            stages = PHASES[3:]
            # Supersede implementation verification, retaining decisions and old packets as history.
            for old in s["visits"]:
                if old["phase"] in PHASES[3:]:
                    old["superseded"] = True
        s["history"].append({"route_change": deepcopy(change), "event_id": event_id})
        s["visits"].extend(visit(p) for p in stages)
    s["revision"] += 1
    return s


def advance(state: dict[str, Any], phase: str) -> dict[str, Any]:
    require(not state.get("cancellation"), "approval_required", "This run was cancelled; no continuation is authorized.")
    require(not state["finished"], "approval_required", "The accepted route cannot be reopened by progress.")
    stage = current(state)
    require(stage["decision"] == "approved", "approval_required", f"Human or authorized policy approval for {stage['phase']} is required.")
    next_index = next((i for i in range(state["index"] + 1, len(state["visits"]))
                       if not state["visits"][i]["superseded"]), None)
    require(next_index is not None, "approval_required", "No next phase is authorized; finish the accepted scope.")
    assert next_index is not None
    require(state["visits"][next_index]["phase"] == phase,
            "approval_required", "Skipped, backward or undeclared phase transition.")
    s = deepcopy(state)
    s["index"] = next_index
    s["revision"] += 1
    return s


def finish(state: dict[str, Any]) -> dict[str, Any]:
    require(not state.get("cancellation"), "approval_required", "A cancelled run cannot be accepted.")
    require(all(v["superseded"] or v["decision"] == "approved" for v in state["visits"]),
            "approval_required", "Every required stage visit needs current human acceptance or valid policy acceptance.")
    s = deepcopy(state)
    if not s["finished"]:
        s["finished"] = True
        s["finished_at"] = datetime.now(timezone.utc).isoformat()
        s["revision"] += 1
    return s


def invalidate(state: dict[str, Any], visit_id: str, reason: str) -> dict[str, Any]:
    s = deepcopy(state)
    if state.get("cancellation"):
        return s  # Source drift cannot reopen a cancelled run's write scope.
    first = next(i for i, v in enumerate(s["visits"]) if v["id"] == visit_id)
    for v in s["visits"][first:]:
        if not v["superseded"] and v["packet"]:
            v["decision"] = "stale"
            v["work"] = "evidence_blocked"
    s["index"] = first
    s["finished"] = False
    s["revision"] += 1
    from . import workflow_approval as approval
    approval.suspend(s, "Evidence drift requires renewed authorization: " + reason)
    s["history"].append({"invalidated_visit": visit_id, "reason": reason})
    return s
