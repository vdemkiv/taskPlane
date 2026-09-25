"""Scoped native attempts. Callers serialize every mutation with the controller lock.

Native observations are cooperative evidence, not host authentication. A prompt
grant identifies a reservation; only observed lineage/result IDs bind a worker.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
import uuid

from . import workflow as w, workflow_evidence as e
from .context import Store, digest
from .context_handoff import Session, binding

# Codex desktop also flattens the namespace in actual hook payloads (observed
# collaborationlist_agents). Keep these exact aliases; never strip arbitrary prefixes.
SPAWN = {"spawn_agent", "collaboration.spawn_agent", "functions.collaboration.spawn_agent", "collaborationspawn_agent", "Agent", "Task"}
FOLLOW = {"followup_task", "collaboration.followup_task", "functions.collaboration.followup_task", "collaborationfollowup_task"}
MESSAGE = {"send_message", "collaboration.send_message", "functions.collaboration.send_message", "collaborationsend_message"}
INTERRUPT = {"interrupt_agent", "collaboration.interrupt_agent", "functions.collaboration.interrupt_agent", "collaborationinterrupt_agent"}
STATUS = {"list_agents", "collaboration.list_agents", "functions.collaboration.list_agents", "collaborationlist_agents"}
WAIT = {"wait_agent", "collaboration.wait_agent", "functions.collaboration.wait_agent", "collaborationwait_agent"}
TOOLS = SPAWN | FOLLOW | MESSAGE | INTERRUPT | STATUS | WAIT
LIVE = {"prepared", "launch_pending", "bootstrapping", "running", "cancel_requested", "unknown"}
STATES = LIVE | {"result_pending", "accepted", "failed", "interrupted"}
MARKER = re.compile(r"(?m)^Taskplane grant: ([0-9a-f]{32})$")
NAME_GRANT = re.compile(r"[a-z0-9_]{1,46}__([0-9a-f]{32})")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def records(state: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = state.setdefault("workers", {})
    return value


def validate(state: dict[str, Any]) -> None:
    rows = state.get("workers", {})
    w.require(isinstance(rows, dict) and len(rows) <= 1024, "state_unavailable", "Invalid worker inventory.")
    for key, row in rows.items():
        w.require(isinstance(row, dict) and row.get("grant_id") == key and row.get("state") in STATES
                  and row.get("run") == state["run"] and row.get("root") == state["root"]
                  and type(row.get("attempt")) is int and isinstance(row.get("paths"), list),
                  "state_unavailable", "Invalid stored worker binding.")


def joined(state: dict[str, Any]) -> bool:
    return not any(r["state"] in LIVE for r in state.get("workers", {}).values())


def task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    rows = e.context_tasks(state)
    found = next((r for r in rows if r["id"] == task_id), None)
    w.require(found is not None, "invalid_evidence", "Unknown run-bound task.")
    assert found is not None
    return dict(e.task_definitions([found])[task_id])


def read_input_manifest(workspace: Path, session: Session, owned_paths: list[str]) -> dict[str, Any]:
    """Share the exact context source selection for root and native verification."""
    paths = {item["body"]["path"] for item in session.items if item["kind"] == "source"}
    for item in session.items:
        if item["kind"] == "requirements":
            paths.update(p for row in item["body"].get("tasks", []) for p in row.get("read_inputs", []))
    # Explicit missing inputs must refuse, not vanish from freshness coverage.
    return e.manifest(workspace, sorted(paths - set(owned_paths)))


def native_result_valid(workspace: Path, state: dict[str, Any], task_id: str,
                        result: dict[str, Any]) -> bool:
    """Validate actual joined native identity, never an asserted reviewer label."""
    row = state.get("workers", {}).get(result.get("grant"))
    if not row or row.get("task_id") != task_id or row.get("state") != "accepted":
        return False
    if (not row.get("worker_id") or row["worker_id"] == state["root"]
            or row["worker_id"] != result.get("worker_id")
            or row.get("terminal_status") not in {"completed", "idle"}
            or row.get("task_digest") != result.get("task_digest")
            or row.get("input_manifest") != result.get("input_manifest")
            or row.get("dependency_results") != result.get("dependency_results")):
        return False
    try:
        worker_session(workspace, state, row).validate(row.get("context_receipt"))
        return True
    except w.Refusal:
        return False


def result_valid(workspace: Path, state: dict[str, Any], task_id: str, seen: set[str] | None = None) -> bool:
    seen = set(seen or ())
    if task_id in seen:
        return False
    seen.add(task_id)
    definition = task(state, task_id)
    for stage in state["visits"][:state["index"]]:
        if (stage["decision"] == "approved" and not stage.get("superseded") and stage.get("packet")
                and definition.get("phase") != w.current(state)["phase"]):
            if any(t["id"] == task_id for t in stage["packet"].get("context", {}).get("tasks", [])
                   if t.get("phase") == stage["phase"]):
                return e.changed(workspace, state) is None
    result = state.get("task_results", {}).get(task_id)
    if not result or result.get("task_digest") != digest(definition):
        return False
    if definition.get("execution") == "native_required" and not native_result_valid(workspace, state, task_id, result):
        return False
    if not result.get("grant") and result.get("input_contract") != "declared-source/v1":
        return False
    try:
        return (result["manifest"] == e.manifest(workspace, list(result["manifest"]))
                and (not result.get("grant") or "input_manifest" in result)
                and result.get("input_manifest", {}) == e.manifest(workspace, list(result.get("input_manifest", {})))
                and result.get("dependency_results", {}) == dependency_results(state, definition["dependencies"])
                and all(result_valid(workspace, state, d, seen) for d in definition["dependencies"]))
    except w.Refusal:
        return False


def dependency_results(state: dict[str, Any], dependencies: list[str]) -> dict[str, str]:
    """Pin accepted result identities, including accepted earlier-phase packets."""
    result = {}
    for key in dependencies:
        prior = next((stage["packet"] for stage in state["visits"][:state["index"]]
                      if stage["decision"] == "approved" and not stage.get("superseded") and stage.get("packet")
                      and any(t["id"] == key and t.get("phase") == stage["phase"]
                              for t in stage["packet"].get("context", {}).get("tasks", []))), None)
        result[key] = digest(prior if prior is not None else state.get("task_results", {}).get(key))
    return result


def dependency_manifest(state: dict[str, Any], dependencies: list[str], seen: set[str] | None = None) -> dict[str, str]:
    seen = set(seen or ())
    files: dict[str, str] = {}
    for key in dependencies:
        if key in seen:
            continue
        seen.add(key)
        result = state.get("task_results", {}).get(key, {})
        files.update(result.get("manifest", {}))
        files.update(result.get("input_manifest", {}))
        files.update(dependency_manifest(state, task(state, key)["dependencies"], seen))
    return files


def current(state: dict[str, Any], row: dict[str, Any]) -> None:
    w.require(row["binding"] == binding(state) and row["task_digest"] == digest(task(state, row["task_id"])),
              "stale_checkpoint", "Worker grant belongs to an old authority or task generation.")


def capacity(value: Any) -> int:
    w.require(isinstance(value, dict) and type(value.get("host_slots")) is int
              and value["host_slots"] >= 0 and type(value.get("includes_root")) is bool
              and isinstance(value.get("reference"), str) and 0 < len(value["reference"]) <= 512,
              "invalid_evidence", "Supply observed host capacity and its source; no default worker cap is assumed.")
    limits = [max(0, value["host_slots"] - int(value["includes_root"]))]
    for key in ("configured_limit", "resource_limit"):
        if value.get(key) is not None:
            w.require(type(value[key]) is int and value[key] >= 0, "invalid_evidence", "Invalid worker limit.")
            limits.append(value[key])
    w.require(value.get("status") in (None, "available", "unavailable"),
              "invalid_evidence", "Unknown native capacity status.")
    if value.get("status") == "unavailable":
        w.require(min(limits) == 0 and isinstance(value.get("reason"), str) and value["reason"].strip(),
                  "invalid_evidence", "Unavailable native capacity needs zero slots and an observed reason.")
    return int(min(limits))


def path_conflict(candidate: dict[str, Any], other: dict[str, Any]) -> bool:
    writes, other_writes = set(candidate['paths']), set(other['paths'])
    reads = set(candidate.get('input_manifest', {})) | set(candidate.get('dependency_manifest', {}))
    other_reads = set(other.get('input_manifest', {})) | set(other.get('dependency_manifest', {}))
    return bool(writes & (other_writes | other_reads) or reads & other_writes)


def readiness(row: dict[str, Any]) -> dict[str, Any]:
    """Observed startup evidence, not a protected-host attestation."""
    proof = row.get("hook_readiness", {})
    missing = [label for label, passed in (
        ("native identity", bool(row.get("worker_id"))),
        ("claim", bool(row.get("claimed_at"))),
        ("complete context", bool(row.get("context_receipt"))),
        ("matching automatic hook pair", bool(proof.get("matched_call"))
         and proof.get("root") == row.get("expected_runtime", {}).get("root")
         and proof.get("member_sha256") == row.get("expected_runtime", {}).get("member_sha256"))) if not passed]
    return {"status": "ready" if not missing else "pending", "missing": missing,
            "runtime_root": proof.get("root"), "basis": "Observed child claim, delivered context and automatic pre/post hook pair."}


def startup_blockers(state: dict[str, Any], definition: dict[str, Any]) -> list[str]:
    blocked = []
    gates = list(definition.get("readiness_after", []))
    if "read_inputs" in definition:
        # The first useful task doubles as the startup probe. Once ready, every
        # independent task can fill capacity without spawning a throwaway probe.
        cohort = [r for r in records(state).values() if r["binding"] == binding(state)
                  and "read_inputs" in task(state, r["task_id"])]
        first = min(cohort, key=lambda row: row["prepared_at"]) if cohort else None
        if first and first["task_id"] != definition["id"] and first["task_id"] not in gates:
            gates.append(first["task_id"])
    from .host_capabilities import runtime_identity
    expected = runtime_identity() if gates else None
    for key in gates:
        attempts = [row for row in records(state).values() if row["task_id"] == key]
        latest = max(attempts, key=lambda row: row["attempt"]) if attempts else None
        if (not latest or latest["binding"] != binding(state)
                or latest["state"] not in {"running", "result_pending", "accepted"}
                or latest.get("expected_runtime") != expected
                or readiness(latest)["status"] != "ready"):
            blocked.append(key)
    return blocked


def prepare(workspace: Path, state: dict[str, Any], task_id: str, request: dict[str, Any]) -> dict[str, Any]:
    definition = task(state, task_id)
    stage = w.current(state)
    w.require(definition.get("execution") != "root", "invalid_evidence",
              "Root execution tasks cannot receive a native grant.")
    w.require(stage["decision"] in {"not_requested", "changes_requested", "rejected"}
              and definition.get("phase", stage["phase"]) == stage["phase"],
              "approval_required", "Worker needs an unsealed current phase task.")
    w.require(all(result_valid(workspace, state, d) for d in definition["dependencies"]),
              "scope_violation", "Task prerequisites need verified accepted results.")
    paths = definition["paths"]
    w.require(paths and set(paths) <= set(state["scope"]["paths"][stage["phase"]]),
              "scope_violation", "Worker task exceeds accepted phase paths.")
    for p in paths:
        e.path(workspace, p)
    rows = records(state)
    w.require(not startup_blockers(state, definition), "worker_readiness",
              "Cohort startup needs the declared child's current claim, complete context and matching automatic hooks.")
    previous = [r for r in rows.values() if r["task_id"] == task_id]
    retry_reason = request.get("retry_reason")
    if previous and "read_inputs" in definition:
        w.require(isinstance(retry_reason, str) and 8 <= len(retry_reason.strip()) <= 512,
                  "invalid_evidence", "A repeated scoped task needs a concrete retry_reason before another attempt.")
    live = [r for r in rows.values() if r["state"] in LIVE]
    limit = capacity(request.get("capacity"))
    w.require(len(live) < limit, "worker_capacity", "No available native worker slot; pending and unknown attempts count.")
    w.require(not any(set(paths) & set(r["paths"]) or r["task_id"] == task_id for r in live),
              "scope_violation", "Task has an active attempt or conflicting write ownership.")
    w.require(len(rows) < 1024, "state_unavailable", "Worker attempt limit reached.")
    name = request.get("task_name", task_id.lower().replace("-", "_"))
    w.require(isinstance(name, str) and re.fullmatch(r"[a-z0-9_]{1,46}", name),
              "invalid_evidence", "Native task name prefix must use 1-46 lowercase letters, digits or underscores.")
    key = uuid.uuid4().hex
    # The desktop host can encrypt message text before hooks see it. Carry the
    # same correlation token in the native name, without decrypting the message.
    name = f"{name}__{key}"
    w.require(not any(r["task_name"] == name for r in live), "scope_violation", "Active native task names must be distinct.")
    session = Session(workspace, state, task_id)
    preflight = session.preflight()
    budget = definition.get("context_budget_bytes", 131072 if "read_inputs" in definition else None)
    w.require(budget is None or preflight["required_body_bytes"] <= budget, "context_overflow",
              "Required context exceeds the task budget before launch; inspect normative/supporting declarations.")
    from .host_capabilities import runtime_identity
    # Preserve immutable content in the bounded object store, never the control DB.
    frozen = session.store.put("worker-input-snapshot", session.frozen)
    row = {"grant_id": key, "root": state["root"], "run": state["run"], "binding": binding(state),
           "task_id": task_id, "task_digest": digest(definition), "task_generation": state.get("task_generation", 0),
           "paths": paths, "criteria": e.task_criteria(definition), "task_name": name,
           "attempt": 1 + max((r["attempt"] for r in rows.values() if r["task_id"] == task_id), default=0),
           "state": "prepared", "worker_id": request.get("worker_id"), "call_id": None,
           "snapshot": frozen, "input_manifest": read_input_manifest(workspace, session, paths),
           "dependency_results": dependency_results(state, definition["dependencies"]),
           "dependency_manifest": dependency_manifest(state, definition["dependencies"]),
           "prepared_at": now(), "events": {}, "context_receipt": None,
           "purpose": definition.get("purpose") or definition.get("review_lens") or task_id,
           "retry_reason": retry_reason, "context_preflight": preflight,
           "expected_runtime": runtime_identity()}
    w.require(not any(path_conflict(row, other) for other in live), "scope_violation",
              "Join workers before changing their read inputs or reading active writer outputs.")
    if row["worker_id"]:
        prior = [r for r in rows.values() if r.get("worker_id") == row["worker_id"]]
        w.require(prior and all(r["state"] not in LIVE for r in prior),
                  "scope_violation", "Follow-up needs a known joined native worker.")
        row['canonical_name'] = max(prior, key=lambda r: r['prepared_at']).get('canonical_name')
    rows[key] = row
    state["worker_capacity"] = {**request["capacity"], "effective_limit": limit, "observed_at": now()}
    state["worker_sequence"] = state.get("worker_sequence", 0) + 1
    return row


def dispatch_message(state: dict[str, Any], row: dict[str, Any]) -> str:
    return (f"Taskplane grant: {row['grant_id']}\n"
            f"Workspace: {state['workspace']}\nRun: {state['run']}\nTask: {row['task_id']}\n"
            "Use the installed Taskplane runtime. Claim this grant with flow worker --operation claim "
            "--run RUN --grant GRANT --workspace WORKSPACE, then consume every required input from "
            "flow context --task TASK. Stay within the assigned paths and return evidence. "
            "The grant is a correlation value, not approval or root authority.")


def find(state: dict[str, Any], worker: str) -> dict[str, Any] | None:
    found = [r for r in state.get("workers", {}).values()
             if worker and worker in {r.get("worker_id"), r.get("canonical_name")}]
    return max(found, key=lambda r: r["prepared_at"]) if found else None


def worker_session(workspace: Path, state: dict[str, Any], row: dict[str, Any]) -> Session:
    current(state, row)
    w.require(row.get("worker_id"), "scope_violation", "Native worker identity is not bound.")
    consumer = {k: row[k] for k in ("worker_id", "grant_id", "attempt", "task_id", "task_generation")}
    return Session(workspace, state, row["task_id"], consumer=consumer,
                   snapshot=Store(workspace).resolve(row["snapshot"]))


def admit(state: dict[str, Any], event: dict[str, Any]) -> bool:
    """Recognize only explicit native schemas. Called in the same locked commit as guard."""
    tool, args = event.get("tool_name") or event.get("tool"), event.get("tool_input", {})
    if tool not in TOOLS:
        return False
    w.require(isinstance(args, dict), "scope_violation", "Invalid native worker arguments.")
    if tool in STATUS | WAIT:
        w.require(set(args) <= ({"path_prefix"} if tool in STATUS else {"timeout_ms"}),
                  "scope_violation", "Unsupported worker status schema.")
        if tool in STATUS:
            call = event.get("tool_use_id") or event.get("call_id")
            if isinstance(call, str) and 0 < len(call) <= 512:
                polls = state.setdefault("worker_polls", {})
                w.require(call in polls or len(polls) < 4096, "state_unavailable", "Worker status observation limit reached.")
                # A delayed result must still address the attempts that existed
                # when this native status call was admitted.
                polls.setdefault(call, [r["grant_id"] for r in records(state).values() if r.get("call_id")])
        return True
    if tool not in INTERRUPT:
        w.require(not state.get('invalidation_pending') and w.current(state)['decision'] in
                  {'not_requested', 'changes_requested', 'rejected'},
                  'stale_checkpoint', 'Native input requires a current unsealed phase.')
    if tool in SPAWN | FOLLOW:
        claude = tool in {"Agent", "Task"}
        fields = ({"prompt", "description", "subagent_type", "model", "run_in_background", "resume"} if claude else
                  {"target", "message"} if tool in FOLLOW else
                  {"task_name", "message", "fork_turns", "model", "reasoning_effort"})
        message = args.get("prompt" if claude else "message")
        w.require(not set(args) - fields and isinstance(message, str) and 0 < len(message) <= 32768,
                  "scope_violation", "Unsupported native worker dispatch schema.")
        matches = MARKER.findall(message)
        if tool in SPAWN and not claude:
            named = NAME_GRANT.fullmatch(str(args.get("task_name", "")))
            if named:
                w.require(not matches or matches == [named[1]], "scope_violation", "Dispatch grant name and message conflict.")
                matches = [named[1]]
        w.require(len(matches) == 1 and matches[0] in records(state), "scope_violation", "Dispatch requires one prepared grant.")
        row = records(state)[matches[0]]
        current(state, row)
        workspace = Path(state['workspace'])
        definition = task(state, row['task_id'])
        w.require(not startup_blockers(state, definition), "worker_readiness",
                  "Declared cohort startup proof is missing or stale before launch.")
        w.require(all(result_valid(workspace, state, d) for d in definition['dependencies'])
                  and row.get('dependency_results') == dependency_results(state, definition['dependencies'])
                  and row['input_manifest'] == e.manifest(workspace, list(row['input_manifest'])),
                  'stale_checkpoint', 'Prepared worker inputs or prerequisites changed before launch.')
        w.require(not any(path_conflict(row, other) for other in records(state).values()
                          if other['grant_id'] != row['grant_id'] and other['state'] in LIVE),
                  'scope_violation', 'Native launch conflicts with active worker read/write ownership.')
        call = event.get("tool_use_id") or event.get("call_id")
        w.require(isinstance(call, str) and 0 < len(call) <= 512, "scope_violation", "Native dispatch call identity is missing.")
        if row["call_id"] == call:
            w.require(row.get("dispatch_digest") == digest(args), "scope_violation", "Conflicting native call replay.")
            return True
        w.require(row["state"] == "prepared" and row["call_id"] is None
                  and not any(r.get("call_id") == call for r in records(state).values()),
                  "scope_violation", "Conflicting or stale native launch.")
        if tool in FOLLOW:
            w.require(row.get("worker_id") and args.get("target") in {row["worker_id"], row.get("canonical_name")},
                      "scope_violation", "Follow-up target does not match its new attempt.")
        elif not claude:
            w.require(args.get("task_name") == row["task_name"] and args.get("fork_turns") == "none"
                      and not row.get("worker_id"), "scope_violation", "Spawn needs the prepared name and a task-focused handoff.")
        w.require(sum(r["state"] in LIVE for r in records(state).values()) <=
                  state["worker_capacity"]["effective_limit"], "worker_capacity", "Observed capacity reduced before launch.")
        row.update(call_id=call, dispatch_digest=digest(args), state="launch_pending", launch_requested_at=now(), host="claude" if claude else "codex")
        return True
    w.require(set(args) <= ({"target", "message"} if tool in MESSAGE else {"target"})
              and isinstance(args.get("target"), str), "scope_violation", "Unsupported native worker control schema.")
    row = find(state, args["target"])
    w.require(row is not None, "scope_violation", "Unknown native worker target.")
    assert row is not None
    if tool in INTERRUPT:
        if row["state"] in LIVE:
            row["state"] = "cancel_requested"
        return True  # Cancellation is not a terminal observation.
    current(state, row)
    w.require(row["state"] in {"bootstrapping", "running"}, "scope_violation", "New input requires a live current attempt.")
    return True


def bind_worker(state: dict[str, Any], row: dict[str, Any], identity: str, name: str | None = None) -> bool:
    w.require(identity and identity != state["root"] and len(identity) <= 200,
              "scope_violation", "Invalid native worker identity.")
    if row.get("worker_id") not in (None, identity):
        row["state"] = "unknown"
        return False
    if any(r is not row and r.get("worker_id") == identity and r["state"] in LIVE for r in records(state).values()):
        row["state"] = "unknown"
        return False
    row["worker_id"] = identity
    if name:
        row["canonical_name"] = name[:200]
    if row["state"] == "launch_pending":
        row.update(state="bootstrapping", identity_bound_at=now())
    return True


def terminal(row: dict[str, Any], status: str, event_id: str) -> None:
    status = "completed" if status == "idle" else status
    previous = row["events"].get(event_id)
    if previous == status:
        return
    if previous is not None or row.get("terminal_status") not in (None, status):
        row["state"] = "unknown"
        return
    if row.get("terminal_status") == status:
        return
    row["events"][event_id] = status
    row.update(terminal_status=status, ended_at=now(), state=(
        "result_pending" if status in {"completed", "idle"} else "interrupted" if status == "interrupted" else "failed"))


def observe(state: dict[str, Any], event: dict[str, Any]) -> None:
    name, tool = event.get("hook_event_name"), event.get("tool_name") or event.get("tool")
    call = event.get("tool_use_id") or event.get("call_id")
    response = event.get("tool_response")
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            response = None
    if name == "PostToolUse" and tool in SPAWN | FOLLOW:
        row = next((r for r in records(state).values() if call and r.get("call_id") == call), None)
        w.require(row is not None, "scope_violation", "Native result has no admitted launch.")
        assert row is not None
        if isinstance(response, dict) and isinstance(response.get("agent_id"), str):
            bind_worker(state, row, response["agent_id"], response.get("task_name"))
        elif isinstance(response, dict) and isinstance(response.get("task_name"), str):
            row['canonical_name'] = response['task_name'][:200]
            correlate(state, row)
        elif tool in FOLLOW and row.get('worker_id'):
            bind_worker(state, row, row['worker_id'])
        elif row["state"] == "launch_pending":
            row["state"] = "unknown"  # Ambiguous failure never proves non-start.
    if name in {"SubagentStart", "SubagentStop"}:
        identity = event.get("agent_id")
        row = (next((r for r in records(state).values() if r.get("call_id") == call), None)
               if call else find(state, str(identity or "")))
        if row and (not isinstance(identity, str) or not identity):
            row['state'] = 'unknown'
            row = None
        if (row and call and isinstance(identity, str) and (row.get('worker_id') != identity or row['state'] == 'launch_pending')
                and not bind_worker(state, row, identity)):
            row = None
        if row and not call and sum(r.get("worker_id") == identity for r in records(state).values()) > 1:
            row = None  # Reused identities need attempt-correlated stop/status evidence.
        if row and name == "SubagentStop":
            terminal(row, "completed", str(event.get("event_id") or f"stop/{row['grant_id']}"))
        elif row and name == 'SubagentStart':
            row.setdefault('started_at', now())
    # Explicit native parent-call metadata can resolve a child-before-return race.
    # Parent identity or an echoed grant alone never selects the latest child.
    if event.get("parent_session_id") == state["root"] and event.get("parent_tool_call_id"):
        row = next((r for r in records(state).values()
                    if r.get("call_id") == event["parent_tool_call_id"]), None)
        identity = event.get("thread_id") or event.get("session_id")
        if row and isinstance(identity, str):
            bind_worker(state, row, identity)
    elif event.get('parent_session_id') == state['root']:
        identity = event.get('thread_id') or event.get('session_id')
        known = find(state, str(identity or ''))
        if known and known['state'] == 'launch_pending':
            bind_worker(state, known, str(identity))
        for row in records(state).values():
            if row['state'] == 'launch_pending' and row.get('host') == 'codex':
                correlate(state, row, expected_identity=identity)
    principal = event.get('thread_id') or event.get('session_id')
    executing = find(state, str(principal or ''))
    if executing and name == 'PreToolUse' and executing['state'] in {'bootstrapping', 'running'}:
        executing.setdefault('started_at', now())
    if executing and executing["state"] in {"bootstrapping", "running"} and name in {"PreToolUse", "PostToolUse"}:
        observed = event.get("taskplane_observed_binding", {})
        runtime = event.get("taskplane_runtime_identity", {})
        expected = executing.get("expected_runtime", {}).get("member_sha256")
        if (observed.get("root") == state["root"] and observed.get("principal") == executing["worker_id"]
                and expected and all(expected.values()) and runtime.get("member_sha256") == expected
                and runtime.get("root") == executing.get("expected_runtime", {}).get("root")
                and isinstance(call, str) and 0 < len(call) <= 512):
            proof = executing.setdefault("hook_readiness", {})
            if name == "PreToolUse":
                proof["pending_call"] = call
            elif proof.get("pending_call") == call:
                proof.update(matched_call=call, root=runtime.get("root"), member_sha256=expected, observed_at=now())
        elif runtime and (runtime.get("member_sha256") != expected
                          or runtime.get("root") != executing.get("expected_runtime", {}).get("root")):
            executing["hook_readiness"] = {"mismatch": True, "root": runtime.get("root")}
    if name == "PostToolUse" and tool in STATUS and isinstance(response, dict):
        agents = response.get("agents")
        if isinstance(agents, list):
            for agent in agents:
                if not isinstance(agent, dict):
                    continue
                target = str(agent.get('agent_id') or agent.get('agent_name') or '')
                candidates = [r for r in records(state).values()
                              if target and target in {r.get('worker_id'), r.get('canonical_name')}]
                polls = state.get('worker_polls', {})
                if call in polls:
                    candidates = [r for r in candidates if r['grant_id'] in polls[call]]
                elif len(candidates) > 1:
                    candidates = []  # An uncorrelated old poll cannot join a new attempt.
                row = max(candidates, key=lambda r: r['prepared_at']) if candidates else None
                if row and not row.get('worker_id'):
                    correlate(state, row)
                status = agent.get('status', agent.get('agent_status'))
                if isinstance(status, dict):
                    status = next(iter(status)) if len(status) == 1 else None
                if row and status in {"completed", "idle", "failed", "interrupted"}:
                    if row.get('worker_id'):
                        terminal(row, status, str(call or "status") + "/" + row["grant_id"])
    # wait_agent is a mailbox wait; interrupt returns previous status. Neither joins work.


def correlate(state: dict[str, Any], row: dict[str, Any], expected_identity: str | None = None) -> None:
    from .host_capabilities import worker_identities
    identities = worker_identities(state['root'], canonical_name=row.get('canonical_name'),
                                   task_name=row['task_name'], since=row['prepared_at'])
    if len(identities) == 1 and (expected_identity is None or identities[0]['worker_id'] == expected_identity):
        identity = identities[0]
        bind_worker(state, row, identity['worker_id'], identity['canonical_name'])
        row['identity_basis'] = 'observed native session metadata and admitted task/call'
        row['native_session_started_at'] = identity['started_at']


def accept_result(workspace: Path, state: dict[str, Any], task_id: str, request: dict[str, Any]) -> dict[str, Any]:
    definition = task(state, task_id)
    w.require(all(result_valid(workspace, state, d) for d in definition["dependencies"]),
              "invalid_evidence", "Prerequisite results are missing or changed.")
    grant = request.get("grant")
    row = records(state).get(grant) if grant else None
    if grant:
        w.require(definition.get("execution") != "root", "invalid_evidence",
                  "Root execution conflicts with a native result grant.")
        w.require(row and row["task_id"] == task_id and row["state"] == "result_pending",
                  "invalid_evidence", "Native attempt is not joined with a pending result.")
        assert row is not None
        current(state, row)
        w.require(row["context_receipt"] is not None, "invalid_context", "Worker did not consume its task inputs.")
        w.require(row["input_manifest"] == e.manifest(workspace, list(row["input_manifest"])),
                  "stale_checkpoint", "Worker read inputs changed; revalidate with a fresh attempt.")
        w.require(row.get("dependency_results") == dependency_results(state, definition["dependencies"]),
                  "stale_checkpoint", "Worker prerequisite results changed; consume a fresh attempt.")
        w.require(not any(h["state"] == "running" and h.get("worker_id") == row["worker_id"]
                          for h in state.get("observed_handles", {}).values()),
                  "scope_violation", "Worker commands remain live.")
    else:
        w.require(definition.get("execution") != "native_required", "invalid_evidence",
                  "Required native task needs its real joined worker result.")
        w.require(definition["owner"] in {"root", state["root"]}, "scope_violation", "Native task requires its real joined worker.")
    w.require(not any(r["state"] in LIVE and task_id in r.get("dependency_results", {})
                      for r in records(state).values()), "scope_violation",
              "Join dependent workers before replacing their prerequisite result.")
    outputs, checks = request.get("outputs"), request.get("checks")
    w.require(isinstance(outputs, list) and outputs and all(isinstance(p, str) for p in outputs)
              and set(outputs) <= set(definition["paths"]), "invalid_evidence", "Result outputs must belong to the task.")
    w.require(isinstance(checks, list) and checks and all(isinstance(c, dict) and c.get("status") == "pass"
              and isinstance(c.get("name"), str) and c["name"] and isinstance(c.get("evidence"), str) for c in checks),
              "invalid_evidence", "Accept results only with passing named verification evidence.")
    assert isinstance(outputs, list) and isinstance(checks, list)
    files = outputs + [c["evidence"] for c in checks]
    result = {"task_digest": digest(definition), "manifest": e.manifest(workspace, files),
              "outputs": list(outputs), "input_manifest": deepcopy(row["input_manifest"]) if row else
                  read_input_manifest(workspace, Session(workspace, state, task_id), definition["paths"]),
              "input_contract": "declared-source/v1",
              "dependency_results": dependency_results(state, definition["dependencies"]),
              "checks": deepcopy(checks), "accepted_at": now(), "grant": grant,
              "reviewer": state["root"], "worker_id": row["worker_id"] if row else None}
    state.setdefault("task_results", {})[task_id] = result
    if row:
        row["state"] = "accepted"
    return result


def lens_summary(state: dict[str, Any], workspace: Path) -> list[dict[str, Any]]:
    result = []
    phase = w.current(state)["phase"]
    capacity = state.get("worker_capacity", {})
    stage = w.current(state)
    if phase != "engineering":
        stage = next((visit for visit in reversed(state["visits"][:state["index"] + 1])
                      if visit["phase"] == "engineering" and not visit.get("superseded")
                      and visit.get("packet")), {})
    if (stage.get("packet") and stage["decision"] in {"awaiting_human_approval", "approved"}
            and not state.get("invalidation_pending") and e.changed(workspace, state) is None):
        claims = stage["packet"]["output"].get("lens_coverage", [])
        return [{**claim, "status": claim.get("status") if stage["packet"].get("execution_evidence") == "native-results/v1"
                 and claim.get("task_id") else "legacy_unverified"}
                for claim in claims]
    definitions = [row for row in e.context_tasks(state)
                   if row.get("phase", phase) == phase and row.get("review_lens")]
    valid_results = {row["id"]: state["task_results"][row["id"]] for row in definitions
                     if row.get("execution") == "native_required" and result_valid(workspace, state, row["id"])}
    conflicts = e.native_lens_conflicts(definitions, valid_results)
    for definition in definitions:
        accepted = state.get("task_results", {}).get(definition["id"], {})
        native = definition.get("execution") == "native_required"
        conflict = definition["id"] in conflicts
        verified = definition["id"] in valid_results and not conflict
        result.append({"lens": definition["review_lens"], "task_id": definition["id"],
            "status": "native_verified" if verified else "serial_scope" if not native else
                "unavailable" if not conflict and capacity.get("status") == "unavailable" else "native_pending",
            "reviewer": accepted.get("worker_id") if verified else state["root"] if not native else None,
            "grant": accepted.get("grant") if verified else None,
            "outputs": accepted.get("outputs", []) if verified else [],
            "rationale": ("A distinct actual worker is required for each selected lens; reviewer "
                          + str(accepted["worker_id"]) + " completed multiple lenses.") if conflict else
                definition.get("execution_reason") if not native else capacity.get("reason"),
            "reference": definition.get("execution_reference") if not native else capacity.get("reference")})
    return result


def summary(state: dict[str, Any], workspace: Path | None = None) -> dict[str, Any]:
    rows = list(state.get("workers", {}).values())
    scheduling = []
    limit = state.get('worker_capacity', {}).get('effective_limit')
    active = [r for r in rows if r['state'] in LIVE]
    if workspace is not None:
        phase = w.current(state)['phase']
        for definition in e.context_tasks(state):
            if definition.get('phase', phase) != phase:
                continue
            key = definition['id']
            missing = [d for d in definition['dependencies'] if not result_valid(workspace, state, d)]
            candidate = {'paths': definition['paths'],
                         'input_manifest': set(e.read_inputs(state, definition)) - set(definition['paths']),
                         'dependency_manifest': dependency_manifest(state, definition['dependencies'])}
            reason = ('accepted' if result_valid(workspace, state, key) else
                      'active attempt' if any(r['task_id'] == key for r in active) else
                      'prerequisites: ' + ', '.join(missing) if missing else
                      'root execution: ' + definition['execution_reason'] if definition.get('execution') == 'root' else
                      'startup readiness: ' + ', '.join(startup_blockers(state, definition)) if startup_blockers(state, definition) else
                      'read/write conflict' if any(path_conflict(candidate, r) for r in active) else
                      'capacity unknown' if limit is None else
                      'native unavailable: ' + str(state['worker_capacity'].get('reason') or 'observed zero capacity') if limit == 0 else
                      'capacity full' if len(active) >= limit else 'ready')
            scheduling.append({'task': key, 'reason': reason, 'execution': definition.get('execution', 'legacy'),
                               'review_lens': definition.get('review_lens'),
                               'reference': state.get('worker_capacity', {}).get('reference')})
    return {"capacity": state.get("worker_capacity"), "default_limit": None,
            "scheduling": scheduling,
            "lens_coverage": lens_summary(state, workspace) if workspace is not None else [],
            "pending": sum(r["state"] in {"prepared", "launch_pending", "unknown"} for r in rows),
            "live": sum(r["state"] in {"bootstrapping", "running", "cancel_requested"} for r in rows),
            "accepted": sum(r["state"] == "accepted" for r in rows),
            "counts": {"tasks": len({r["task_id"] for r in rows}), "reserved": len(rows),
                       "launched": sum(bool(r.get("call_id")) for r in rows),
                       "retries": sum(r["attempt"] > 1 for r in rows),
                       "failed": sum(r["state"] in {"failed", "interrupted"} for r in rows)},
            "context_cost": {"returned_bytes": sum(r.get("context_delivery", {}).get("returned_bytes", 0) for r in rows),
                             "responses": sum(r.get("context_delivery", {}).get("responses", 0) for r in rows),
                             "unknown_attempts": sum("context_delivery" not in r for r in rows),
                             "basis": "Delivered context bytes and responses, not native tokens or Codex allowance."},
            "attempts": [{**{k: r.get(k) for k in ("grant_id", "task_id", "worker_id", "attempt", "state",
                "prepared_at", "started_at", "ended_at", "purpose", "retry_reason", "context_preflight", "context_delivery")},
                "readiness": readiness(r)} for r in rows]}
