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
    """Report declared intent; exact Build ownership is reviewed in Plan."""
    return {"status": "progressive", "contract": "ownership/v1",
            "detail": "Plan approval authorizes concrete Build paths; future artifacts need not exist at start."}


def validate_scope(scope: dict[str, Any]) -> None:
    require(isinstance(scope, dict), "invalid_evidence", "Scope must be an object.")
    require(scope.get("execution_contract") in (None, "native-default/v1", "ownership/v1"),
            "invalid_evidence", "Unknown execution contract.")
    criteria = scope.get("criteria")
    require(isinstance(criteria, list) and criteria and
            all(isinstance(c, str) and c.strip() for c in criteria) and len(set(criteria)) == len(criteria),
            "invalid_evidence", "Scope needs unique acceptance criterion IDs.")
    paths = scope.get("paths")
    require(isinstance(paths, dict) and set(paths) <= set(PHASES),
            "invalid_evidence", "Declare literal write paths for the current phase; Plan defines Build ownership.")
    assert isinstance(paths, dict)
    for phase in PHASES:
        values = paths.get(phase, [])
        require(isinstance(values, list) and all(isinstance(p, str) and p for p in values),
                "invalid_evidence", f"Invalid write scope for {phase}.")
    require(isinstance(scope.get("verification_inputs", []), list)
            and all(isinstance(p, str) and p for p in scope.get("verification_inputs", [])),
            "invalid_evidence", "Verification inputs must be a path list.")
    from . import native_tool_guard
    native_tool_guard.validate_scope(scope)
    scope_preflight(scope)


def normalized_scope(scope: dict[str, Any]) -> dict[str, Any]:
    """Give a new delivery all phase slots without adding authorized paths."""
    validate_scope(scope)
    scope = deepcopy(scope)
    scope["paths"] = {phase: list(scope["paths"].get(phase, [])) for phase in PHASES}
    scope["execution_contract"] = "ownership/v1"
    return scope


def new_state(workspace: str, root: str, run: str, scope: dict[str, Any], *,
              entry: str = "product", standalone: bool = False) -> dict[str, Any]:
    require(entry in ENTRY_PHASES and (standalone or entry == "product"),
            "approval_required", "Full delivery begins at Product; standalone entry cannot grant Build.")
    scope = normalized_scope(scope)
    return {"schema": "taskplane.workflow/v2", "workspace": workspace, "root": root,
            "run": run, "revision": 0, "scope": deepcopy(scope),
            "visits": [visit(p) for p in ((entry,) if standalone else PHASES)],
            "index": 0, "decisions": {}, "history": [], "finished": False}


def current(state: dict[str, Any]) -> dict[str, Any]:
    return dict(state["visits"][state["index"]])


def phase_write_paths(state: dict[str, Any]) -> list[str]:
    """Production writes follow Plan; current root result metadata is separate."""
    stage = current(state)
    paths = state["scope"]["paths"][stage["phase"]]
    repair = state.get('repair_scope')
    if repair and stage['id'] in repair.get('visits', [repair.get('build_visit')]):
        sealed: set[str] = set()
        for prior in state['visits']:
            packet = prior.get('packet') or {}
            sealed.update(path for path in (packet.get('output_path'), packet.get('context', {}).get('tasks_path')) if path)
            sealed.update(packet.get('verification', {}).get('files', []))
            sealed.update(ref['path'] for ref in packet.get('output', {}).get('artifacts', []))
        paths = [path for path in paths if path not in sealed
                 and (stage['phase'] != 'build' or path in repair['paths'])]
    return list(dict.fromkeys([*paths, *stage.get("artifact_paths", [])]))


def unmet_evidence(packet: dict[str, Any]) -> list[str]:
    """Identify only unsatisfied mandatory evidence, without comparing prose."""
    checks = packet.get('verification', {}).get('effective_checks', [])
    return [row['check_id'] for row in checks if row.get('required', True) and row.get('status') != 'pass']


def repair(state: dict[str, Any], tasks: list[str], verified: dict[str, Any]) -> dict[str, Any]:
    """Re-enter the existing Build route for an explicitly authorized task closure."""
    require(not state.get('cancellation') and not state.get('retired'), 'approval_required',
            'Cancelled or retired runs cannot regain write grants.')
    require(not state.get('superseded_by'), 'state_unavailable',
            'This run was replaced; its evidence is historical and its grants are revoked.')
    require(verified.get('human') is True and not verified.get('automatic')
            and verified.get('choice') in {'approved', 'changes_requested'},
            'approval_required', 'Repair requires the actual human request.')
    plan = accepted_plan(state)['packet']['output']
    strategy = plan.get('verification_strategy')
    require(isinstance(strategy, dict) and strategy.get('schema') == 'taskplane.verification-strategy/v1',
            'invalid_evidence', 'Scoped repair requires the existing typed Plan and evidence contract.')
    definitions = {row['id']: row for row in plan['task_dag'] if row.get('phase') == 'build'}
    require(isinstance(tasks, list) and tasks and all(isinstance(key, str) and key in definitions for key in tasks),
            'scope_violation', 'Repair must name existing approved Build tasks.')
    affected = set(tasks)
    while True:
        paths = {path for key in affected for path in definitions[key]['paths']}
        expanded = affected | {key for key, row in definitions.items()
            if set(row.get('dependencies', [])) & affected or set(row.get('read_inputs', [])) & paths}
        if expanded == affected:
            break
        affected = expanded
    s = deepcopy(state)
    # Keep accepted packets, decisions and unaffected results. Existing check
    # keys determine which historical evidence can still be reused.
    s['history'].append({'repair_request': deepcopy(verified), 'tasks': sorted(affected)})
    for previous in s['visits']:
        if previous['phase'] in PHASES[3:]:
            previous['superseded'] = True
    fresh = [visit(phase) for phase in PHASES[3:]]
    s['visits'].extend(fresh)
    s['index'] = len(s['visits']) - len(PHASES[3:])
    stage = s['visits'][s['index']]
    checks = strategy.get('checks', [])
    for renewed in fresh:
        prefix = f".taskplane/runs/{s['run']}/{renewed['id']}/{renewed['phase']}"
        renewed['artifact_paths'] = [prefix + '/output.json', prefix + '/tasks.json',
                                    prefix + '/verification.json', prefix + '/report.md']
        for check in checks:
            label = content_fingerprint(check['id'])[:16]
            renewed['artifact_paths'].append(prefix + '/check-' + label + '.log')
    s['repair_scope'] = {'tasks': sorted(affected), 'paths': sorted(paths),
        'checks': [row['id'] for row in checks
                   if set(row.get('source_inputs', []) + row.get('test_inputs', [])) & paths],
        'build_visit': stage['id'], 'visits': [v['id'] for v in fresh],
        'prior_visits': [v['id'] for v in state['visits']],
        'retained_results': sorted(set(s.get('task_results', {})) - affected)}
    for key in affected:
        s.get('task_results', {}).pop(key, None)
        s.get('retained_task_authority', {}).pop(key, None)
        s.setdefault('task_epochs', {})[key] = s.get('task_epochs', {}).get(key, 0) + 1
    s['finished'] = False
    s.pop('finished_at', None)
    s.pop('parent_hook_readiness', None)
    s['revision'] += 1
    return s


def accepted_plan(state: dict[str, Any]) -> dict[str, Any]:
    plans = [v for v in state["visits"][:state["index"]]
             if v["phase"] == "plan" and not v["superseded"]]
    require(plans and plans[-1]["decision"] == "approved" and plans[-1]["packet"],
            "approval_required", "A current accepted Plan is required for Build or repair.")
    return dict(plans[-1])


def validate_state(state: dict[str, Any]) -> None:
    """Reject structurally corrupt protected records without inventing repairs."""
    from . import workflow_approval as approval
    from .workflow_write_guard import validate_control_history
    validate_control_history(state)
    validate_scope(state["scope"])
    approval.validate_history(state)
    require(type(state["revision"]) is int and state["revision"] >= 0
            and type(state["index"]) is int and isinstance(state["visits"], list)
            and 0 <= state["index"] < len(state["visits"])
            and isinstance(state["decisions"], dict) and isinstance(state["history"], list)
            and type(state["finished"]) is bool,
            "state_unavailable", "Invalid protected state fields.")
    ids = set()
    plan_checks: list[dict[str, Any]] = []
    for stage in state["visits"]:
        require(isinstance(stage, dict) and isinstance(stage.get("id"), str) and stage["id"]
                and stage["id"] not in ids and stage.get("phase") in PHASES
                and stage.get("work") in ("working", "ready", "evidence_blocked")
                and stage.get("decision") in ("not_requested", "awaiting_human_approval", "approved",
                                              "changes_requested", "rejected", "cancelled", "stale")
                and type(stage.get("superseded")) is bool and "packet" in stage,
                "state_unavailable", "Invalid protected visit.")
        if "artifact_paths" in stage:
            prefix = f".taskplane/runs/{state['run']}/{stage['id']}/{stage['phase']}"
            standard = [prefix + '/output.json', prefix + '/tasks.json']
            ordinary = stage['phase'] == 'build' and stage['artifact_paths'] == standard
            repair_artifacts: list[str] = []
            if not ordinary:
                require(isinstance(plan_checks, list) and all(isinstance(check, dict)
                        and isinstance(check.get('id'), str) and check['id'] for check in plan_checks),
                        'state_unavailable', 'Repair metadata requires identified Plan checks.')
                repair_artifacts = [*standard, prefix + '/verification.json', prefix + '/report.md',
                    *(prefix + '/check-' + content_fingerprint(check['id'])[:16] + '.log' for check in plan_checks)]
            require(state.get("schema") == "taskplane.workflow/v2" and stage["phase"] in PHASES[3:]
                    and (ordinary or stage["artifact_paths"] == repair_artifacts),
                    "state_unavailable", "Invalid current Build result metadata paths.")
        ids.add(stage["id"])
        packet = stage["packet"]
        if packet is None:
            require(stage["decision"] == "not_requested", "state_unavailable", "Decision has no packet.")
            continue
        require(isinstance(packet, dict) and packet.get("phase") == stage["phase"]
                and packet.get("visit") == stage["id"] and isinstance(packet.get("checkpoint"), str)
                and all(isinstance(packet.get(k), dict) for k in ("manifest", "source_manifest", "context", "output")),
                "state_unavailable", "Invalid protected packet.")
        if stage['phase'] == 'plan':
            strategy = packet['output'].get('verification_strategy')
            plan_checks = strategy.get('checks', []) if isinstance(strategy, dict) else []
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


def route_eligibility(state: dict[str, Any], change: Any) -> None:
    """One pure validator before sealing and again before recording consent."""
    if not change:
        return
    stage = current(state)
    require(isinstance(change, dict) and change.get("kind") in {"delivery", "repair"},
            "invalid_evidence", "Invalid route amendment.")
    require(stage["phase"] in {"design", "evaluate", "engineering"},
            "invalid_evidence", "This stage cannot extend the delivery route.")
    if change["kind"] == "delivery":
        require(all(v["superseded"] for v in state["visits"][state["index"] + 1:]),
                "invalid_evidence", "Delivery extension applies only at the end of the current scope.")
        validate_scope(change.get("scope", {}))
    else:
        require(stage["phase"] in {"evaluate", "engineering"}, "invalid_evidence",
                "Only evaluation or review can propose a scoped repair.")
        plan = accepted_plan(state)
        position = next(i for i, v in enumerate(state["visits"]) if v["id"] == plan["id"])
        require(any(v["phase"] == "build" and not v["superseded"] and v["decision"] == "approved"
                    and v["packet"] for v in state["visits"][position + 1:state["index"]]),
                "approval_required", "Repair requires an accepted Build after the current Plan.")


def submit(state: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    require(not state.get("cancellation"), "approval_required", "This run was cancelled; an explicit new run is required.")
    require(not state["finished"], "approval_required", "This route is already accepted.")
    route_eligibility(state, packet.get("route_change"))
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
             or stage["decision"] == "approved" and (choice == 'cancelled'
                 or stage['phase'] in PHASES[:3] and choice in {'changes_requested', 'rejected'})) and stage["packet"],
            "approval_required", "Submit the current stage's evidence before requesting a decision.")
    expected = binding(state, stage["packet"])
    require(verified.get("binding") == expected, "stale_checkpoint",
            "Decision does not match the current root, visit, revision and artifact checkpoint.")
    require(choice in ("approved", "changes_requested", "rejected", "cancelled"),
            "invalid_evidence", "Unknown human decision.")
    if choice == "approved":
        route_eligibility(state, stage["packet"].get("route_change"))
        missing = unmet_evidence(stage['packet'])
        require(not missing, 'invalid_evidence', 'Required evidence remains unmet: ' + ', '.join(missing))
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
            next_scope = normalized_scope(change["scope"])
            # Acceptance ends the standalone scope. Its evidence remains historical;
            # later authorized Build edits must not stale the initiating review.
            stage["superseded"] = True
            s["scope"] = next_scope
            stages = PHASES
        else:
            require(kind == "repair" and stage["phase"] in ("evaluate", "engineering"),
                    "invalid_evidence", "Only evaluation or review can propose a scoped repair.")
            plan = accepted_plan(s)
            strategy = plan['packet']['output'].get('verification_strategy')
            if isinstance(strategy, dict) and strategy.get('schema') == 'taskplane.verification-strategy/v1':
                definitions = plan['packet']['output']['task_dag']
                requested = change.get('tasks')
                if requested is None:
                    unmet_ids = set(unmet_evidence(stage['packet']))
                    requested = sorted({row['task'] for row in strategy['checks'] if row['id'] in unmet_ids})
                    if not requested:
                        requested = [row['id'] for row in definitions if row.get('phase') == 'build']
                s['history'].append({'route_change': deepcopy(change), 'event_id': event_id})
                repaired = repair(s, requested, verified)
                repaired['index'] = s['index']
                return repaired
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


def advance(state: dict[str, Any], phase: str, *, output_path: str | None = None) -> dict[str, Any]:
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
    if phase == "build" and s.get("schema") == "taskplane.workflow/v2":
        prefix = f".taskplane/runs/{s['run']}/{s['visits'][next_index]['id']}/build"
        artifacts = s['visits'][next_index].get('artifact_paths') or [prefix + "/output.json", prefix + "/tasks.json"]
        if output_path:
            require(output_path in s["scope"]["paths"][phase] or output_path in artifacts,
                    "scope_violation", "Build output must be planned or use the current result namespace.")
        s["visits"][next_index]["artifact_paths"] = artifacts
    elif phase != "build" and not s["scope"]["paths"].get(phase):
        prefix = f".taskplane/runs/{s['run']}/{s['visits'][next_index]['id']}/{phase}"
        result = output_path or prefix + "/output.json"
        s["scope"]["paths"][phase] = list(dict.fromkeys([
            result, prefix + "/tasks.json", prefix + "/report.md", prefix + "/checks.json"]))
    elif output_path:
        require(output_path in s["scope"]["paths"][phase], "scope_violation",
                "An explicit existing phase scope cannot be widened by advance.")
    s["revision"] += 1
    revision_id = s.get('active_requirements_revision')
    if revision_id and phase == 'build':
        event = s['requirements_revisions'][revision_id]
        require(event['status'] == 'review' and stage.get('requirements_revision') == revision_id,
                'approval_required', 'Implementation needs the accepted revised Plan.')
        event['status'] = 'completed'
        event['result'] = {**event['result'], 'status': 'completed', 'phase': phase,
                           'revision': s['revision'], 'dispatch_paused': False,
                           'next_action': 'Revalidate retained results and prepare fresh affected grants.'}
        s['dispatch_paused'] = False
        s.pop('active_requirements_revision', None)
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
