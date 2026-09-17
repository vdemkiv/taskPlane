"""Shared observations plus guarded workflow entry points.

The workspace journal is never approval authority. Mandatory transitions use
the protected workflow controller; optional usage observation stays advisory.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import stat
import uuid
from typing import Any, Callable
from taskplane import workflow, workflow_host, depgraph

if __package__:
    from . import native_session_meter as _package_meter
    from . import flow_usage as _package_usage
    from . import claude_flow_usage as _package_claude
    from . import storage as _package_storage, primitives as _package_primitives
    native_session_meter, flow_usage, claude_flow_usage = _package_meter, _package_usage, _package_claude
    storage, primitives = _package_storage, _package_primitives
else:
    import native_session_meter as _flat_meter
    import flow_usage as _flat_usage
    import claude_flow_usage as _flat_claude
    import storage as _flat_storage
    import primitives as _flat_primitives
    native_session_meter, flow_usage, claude_flow_usage = _flat_meter, _flat_usage, _flat_claude
    storage, primitives = _flat_storage, _flat_primitives


JOURNAL = ".taskplane/flow-events.jsonl"
DASHBOARD = ".taskplane/dashboard.html"
HOOK_NAMES = {
    "screen": "PreToolUse", "screen-dispatch": "PreToolUse",
    "screen-skill": "PreToolUse", "screen-render": "PreToolUse",
    "context": "SessionStart", "host-native-check": "SessionStart",
    "subagent-start": "SubagentStart", "subagent-stop": "SubagentStop",
    "tool-observe": "PostToolUse", "human-input": "UserPromptSubmit",
    "session-verify": "Stop",
}


def read_events(workspace: Path) -> list[dict[str, Any]]:
    try:
        with storage.runtime_file(str(workspace), "flow-events.jsonl").open(encoding="utf-8") as stream:
            rows = []
            for line in stream:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except ValueError:
                    continue
            return rows
    except FileNotFoundError:
        return []


def append(workspace: Path, row: dict[str, Any]) -> None:
    path = storage.runtime_file(str(workspace), "flow-events.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    ignore = storage.runtime_file(str(workspace), ".gitignore")
    if not ignore.exists():
        primitives.atomic_write_bytes(str(ignore), b"*\n")
    payload = (json.dumps({"at": datetime.now(timezone.utc).isoformat(), **row}) + "\n").encode()
    storage.runtime_file(str(workspace), "flow-events.jsonl.lock")
    with primitives.file_lock(str(path)):
        storage.runtime_file(str(workspace), "flow-events.jsonl")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Flow journal must be a regular file")
            if info.st_size:
                os.lseek(fd, -1, os.SEEK_END)
                if os.read(fd, 1) != b"\n":
                    payload = b"\n" + payload
            remaining = memoryview(payload)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("Flow journal write made no progress")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)


def session_id(event: dict[str, Any]) -> str:
    return str(event.get("thread_id") or event.get("session_id")
               or os.environ.get("TASKPLANE_CLAUDE_SESSION_ID")
               or os.environ.get("CLAUDE_SESSION_ID")
               or os.environ.get("CODEX_THREAD_ID")
               or "local")


def claude_session(event: dict[str, Any]) -> bool:
    return bool(os.environ.get("CLAUDE_PLUGIN_ROOT") or os.environ.get("CLAUDECODE")
                or os.environ.get("TASKPLANE_CLAUDE_SESSION_ID")
                or os.environ.get("CLAUDE_SESSION_ID") or event.get("host") == "claude")


def counter(event: dict[str, Any], session: str) -> dict[str, Any]:
    if claude_session(event):
        root = str(event.get("session_id") or session)
        claude_path = claude_flow_usage.transcript(root, event)
        identity = {"host": "claude", "transcript_path": str(claude_path) if claude_path else None}
        try:
            if claude_path:
                snapshot = claude_flow_usage.read_snapshot(claude_path, root)
                return {**identity, "usage": snapshot["usage"],
                        "usage_status": "partial" if snapshot["partial"] else "observed"}
        except (OSError, ValueError):
            pass
        return {**identity, "usage": None, "usage_status": "unavailable"}
    path = event.get("transcript_path") or event.get("transcript")
    candidates: list[str | Path] = [path] if isinstance(path, str) else []
    if session != "local":
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        candidates.extend((home / "sessions").glob(f"**/*{session}*.jsonl"))
    if candidates:
        try:
            snapshot = native_session_meter.read_logical_snapshot(candidates, session)
            return {"usage": snapshot["usage"],
                    "parent": snapshot.get("parent_session_id"),
                    "agent": snapshot.get("agent_path"),
                    "usage_status": "partial" if snapshot["partial"] else "observed"}
        except (OSError, ValueError):
            pass
    return {"usage": None, "usage_status": "unavailable"}


def active_run(rows: list[dict[str, Any]], session: str, parent: str | None = None) -> dict[str, Any] | None:
    owners = {session, parent} - {None}
    for row in reversed(rows):
        if row.get("session") in owners:
            owners.add(row.get("root"))
    closed = {row.get("run") for row in rows if row.get("kind") == "finish"}
    return next((row for row in reversed(rows)
                 if row.get("kind") == "start" and row.get("session") in owners
                 and row.get("run") not in closed), None)


def summarize(rows: list[dict[str, Any]], run: dict[str, Any]) -> dict[str, Any]:
    events = [row for row in rows if row.get("run") == run["run"]]
    seen = set()
    unique = []
    for row in events:
        key = (row.get("session"), row.get("event"), row.get("call_id"))
        if row.get("call_id"):
            if key in seen:
                continue
            seen.add(key)
        unique.append(row)
    sessions: dict[str, list[dict[str, Any]]] = {}
    for row in unique:
        if row.get("kind") in {"attach", "usage"}:
            continue
        sessions.setdefault(row["session"], []).append(row)
    usage: Counter[str] = Counter()
    measured = 0
    unmeasured = 0
    for observations in sessions.values():
        counters = [row["usage"] for row in observations if row.get("usage")]
        if not counters:
            unmeasured += 1
            continue
        measured += 1
        # Cumulative native counters are never added to each other. A late
        # first observation is a baseline, so the delta is a lower bound.
        first = counters[0]
        for field, value in first.items():
            usage[field] += max(0, max(c[field] for c in counters) - value)
    milestones = [row for row in unique if row.get("kind") == "progress"]
    entry_phase = run.get("phase") or (milestones[0].get("phase") if milestones else None) or "product"
    last_progress = max((i for i, row in enumerate(unique)
                         if row.get("kind") in {"start", "progress"}), default=0)
    recent = unique[last_progress + 1:]
    tools = [row for row in recent if row.get("event") == "PreToolUse"]
    repetitions = Counter(row.get("action") for row in tools if row.get("action"))
    warnings = []
    if len(tools) >= 20:
        warnings.append("20+ tool calls since reported progress: check the next deliverable and simplify.")
    if repetitions and max(repetitions.values()) >= 4:
        warnings.append("The same action was attempted 4+ times since progress: change approach or report the real blocker.")
    dispatches = sum(row.get("tool", "").split("__")[-1] in
                     {"spawn_agent", "Task", "Agent"} for row in tools)
    if dispatches >= 6:
        warnings.append("6+ agent dispatches since progress: check whether the reviews justify their cost.")
    if usage.get("total_tokens", 0) >= 100_000 and not milestones:
        warnings.append("100k+ observed tokens without a milestone: reduce context and focus on a testable result.")
    return {
        "mode": "advisory", "owner": "orchestrator", "run": run["run"],
        "goal": run.get("goal"),
        "status": "finished" if any(r.get("kind") == "finish" for r in unique) else "active",
        "outcome": next((r.get("note") for r in reversed(unique)
                         if r.get("kind") == "finish"), None),
        "entry_phase": entry_phase,
        "phase": milestones[-1].get("phase") if milestones else entry_phase,
        "milestones": [{"phase": r.get("phase"), "note": r.get("note"),
                        "at": r.get("at")} for r in milestones],
        "started_at": run.get("at"),
        "finished_at": next((r.get("at") for r in reversed(unique)
                             if r.get("kind") == "finish"), None),
        "tool_calls": sum(r.get("event") == "PreToolUse" for r in unique),
        "tokens": dict(usage) if measured else None,
        "token_coverage": {"measured_sessions": measured, "unmeasured_sessions": unmeasured,
                           "basis": "delta between first and latest observed native counters; lower bound"},
        "advice": warnings,
    }


def artifact(workspace: Path, value: str) -> Path:
    """Evidence is explicitly attached to a run and confined to its workspace."""
    path = (workspace / value).resolve()
    path.relative_to(workspace.resolve())
    if not path.is_file():
        raise ValueError("evidence file does not exist")
    return path


def _controller(workspace: Path, root: str, profile: str = "native_workflow") -> workflow_host.Controller:
    return workflow_host.Controller(workspace, root,
        workflow_host.installed_adapter("claude" if claude_session({}) else "codex", profile))


def report(workspace: Path, run_id: str | None = None, *,
           governor: workflow_host.Controller | None = None) -> dict[str, Any] | None:
    rows = read_events(workspace)
    run = next((r for r in reversed(rows) if r.get("kind") == "start"
                and (not run_id or r.get("run") == run_id)), None)
    controller = governor or _controller(workspace, str(run["session"]) if run else session_id({}))
    try:
        governed = controller.report(run_id or (str(run["run"]) if run else None))
    except workflow.Refusal as exc:
        governed = {**exc.result(), "authority_verified": False}
    if not run:
        if not governed.get("visits"):
            return None
        # Rebuild a derived view after a durable decision/start but lost rendering.
        # This does not fabricate a historical observation or human decision.
        run = {"kind": "start", "run": governed["run"], "session": governed["root"],
               "phase": governed["visits"][0]["phase"], "goal": governed.get("goal", ""),
               "at": governed.get("started_at")}
    result = summarize(rows, run)
    result["dashboard"] = str(workspace.resolve() / DASHBOARD)
    attachments: dict[str, Any] = {}
    for row in rows:
        if row.get("run") == run["run"]:
            incoming = row.get("artifacts") or {}
            evidence = list(dict.fromkeys(attachments.get("evidence", []) + incoming.get("evidence", [])))
            attachments.update(incoming)
            if evidence:
                attachments["evidence"] = evidence
    result["artifacts"] = attachments
    result["tasks"] = []
    result["reviews"] = []
    result["evidence_errors"] = []
    for key in ("tasks", "reviews"):
        if not attachments.get(key):
            continue
        try:
            data = json.loads(artifact(workspace, attachments[key]).read_text(encoding="utf-8"))
            if key == "tasks":
                result[key] = data.get("tasks", []) if isinstance(data, dict) else data
            else:
                if isinstance(data, list):
                    result[key] = data
                else:
                    result[key] = [dict(r, phase=phase) for phase, group in data.items()
                                   if isinstance(group, list) for r in group
                                   if isinstance(r, dict) and r.get("agent")]
            if not isinstance(result[key], list) or any(not isinstance(r, dict) for r in result[key]):
                raise ValueError("invalid evidence rows")
        except (OSError, ValueError, TypeError, AttributeError):
            result[key] = []
            result["evidence_errors"].append(f"{key}: attached evidence unavailable or invalid")
    expected = [str(r["agent"]) for r in result["reviews"] if r.get("agent")]
    expected += [str(r["child"]) for r in rows if r.get("run") == run["run"] and r.get("child")]
    result["observed_tokens"] = result["tokens"]
    try:
        result.update(flow_usage.reconcile(run, rows, expected))
    except (OSError, ValueError, TypeError, KeyError):
        result["evidence_errors"].append("Native session reconciliation unavailable; showing hook observations")
    result["workflow"] = governed
    result["observation_status"] = result["status"]
    if governed.get("visits"):
        result["phase"] = governed["phase"]
        result["status"] = governed["status"]
    else:
        result["status"] = "legacy_unverified"
    return result


def _observe_hook(event: dict[str, Any]) -> dict[str, Any]:
    claude_flow_usage.bind_session(event)
    workspace = Path(event.get("cwd") or os.getcwd())
    rows = read_events(workspace)
    if not rows:
        return {}
    session = session_id(event)
    observation = counter(event, session)
    run = active_run(rows, session, observation.get("parent") or event.get("parent_session_id"))
    if run is None:
        return {}
    name = str(event.get("hook_event_name") or "unknown")
    tool = str(event.get("tool_name") or event.get("tool") or "")[:120]
    action = hashlib.sha256(json.dumps(
        [tool, event.get("tool_input")], sort_keys=True).encode()).hexdigest()
    row = {"kind": "hook", "run": run["run"], "root": run["session"],
           "session": session, "event": name, "tool": tool, "action": action,
           "call_id": event.get("tool_use_id") or event.get("call_id"), **observation}
    if name in {"SubagentStart", "SubagentStop"} and event.get("agent_id"):
        row["child"] = str(event["agent_id"])[:200]
        if claude_session(event) and event.get("agent_transcript_path"):
            row["agent_transcript_path"] = str(event["agent_transcript_path"])
    before = summarize(rows, run)["advice"]
    append(workspace, row)
    advice = [item for item in summarize([*rows, row], run)["advice"] if item not in before]
    if advice and name in {"PreToolUse", "SessionStart"}:
        return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                "Taskplane advisory (continue delivery): " + " ".join(advice)}}
    # Abstain: no permissionDecision=allow and no decision=block. The host
    # still applies its own permissions, approvals and sandbox.
    return {}


def hook(event: dict[str, Any], *,
         governor: workflow_host.Controller | None = None) -> dict[str, Any]:
    workspace = Path(event.get("cwd") or os.getcwd())
    legacy = active_run(read_events(workspace), session_id(event), event.get("parent_session_id"))
    controller = governor or _controller(workspace, str(legacy["session"]) if legacy else session_id(event))
    name = event.get("hook_event_name")
    guarded = controller.report()
    # Protected state is checked independently of the deletable workspace journal.
    run = guarded.get("run") or (legacy or {}).get("run")
    if guarded.get("workflow_available") and guarded.get("run"):
        controller.observe(event, str(run))
        if name == "Stop" and not controller.adapter.can_seal(guarded):
            return {"systemMessage": "Taskplane is waiting for process quiescence before sealing. No phase has advanced; unknown host coverage remains explicit."}
    guarded_run = guarded.get("run") or (run if controller.adapter.profile == "protected_host" else None)
    if guarded_run and name == "PreToolUse":
        controller.guard(event, str(run))
    if run and name == "UserPromptSubmit":
        if not guarded.get("workflow_available") or not guarded.get("run"):
            return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                    "Taskplane approval remains unverified; this prompt does not authorize a workflow transition."}}
        if guarded.get("status") == "awaiting_human_approval":
            reference = controller.adapter.prompt_reference(event, guarded)
            if reference:
                controller.apply("decide", str(run), expected_revision=guarded["revision"], native_reference=reference)
            else:
                return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                        "Taskplane: record the actual human response with its presented checkpoint and conversation provenance. This prompt alone has not advanced the workflow."}}
    try:
        return _observe_hook(event)
    except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
        return {}  # Only optional observations abstain on failure.


def run_hook(command: str | None = None, *,
             governor: workflow_host.Controller | None = None) -> int:
    event: dict[str, Any] = {}
    try:
        # A named host command identifies the error contract even if JSON parsing
        # fails. Keep event object-shaped until validation succeeds.
        if command:
            event["hook_event_name"] = HOOK_NAMES[command]
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("Hook event must be an object")
        if command:
            payload["hook_event_name"] = event["hook_event_name"]
        event = payload
        result = hook(event, governor=governor)
    except (workflow.Refusal, OSError, ValueError, TypeError, KeyError, primitives.StateError) as exc:
        reason = exc.detail if isinstance(exc, workflow.Refusal) else "Taskplane hook input/state is invalid."
        if event.get("hook_event_name") == "PreToolUse":
            result = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                      "permissionDecision": "deny", "permissionDecisionReason": reason}}
            print(json.dumps(result))
            return 0  # Native deny JSON, not approval.
        if event.get("hook_event_name") == "Stop":
            print(json.dumps({"systemMessage": reason}))
            return 0  # Waiting/errors must not create forced-continuation loops.
        print(json.dumps({"decision": "block", "reason": reason}))
        return 2
    print(json.dumps(result))
    return 0


def main(argv: list[str] | None = None, *,
         prepare: Callable[[str], dict[str, Any]] | None = None,
         governor: workflow_host.Controller | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "progress", "finish", "report", "attach",
                                           "submit", "decide", "advance", "hook"])
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--goal", default="")
    parser.add_argument("--phase", default="", type=str.lower)
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument("--note", default="")
    parser.add_argument("--run")
    parser.add_argument("--tasks")
    parser.add_argument("--reviews")
    parser.add_argument("--output", help="Phase output JSON for submit")
    parser.add_argument("--native-event", default="", help="Opaque native event reference; never approval text")
    parser.add_argument("--profile", choices=["native_workflow", "protected_host"], default="native_workflow")
    parser.add_argument("--scope", help="Exact phase scope JSON for native workflow start")
    parser.add_argument("--request-reference", default="", help="Reference to the actual user request")
    parser.add_argument("--decision-json", default="", help="Observed decision envelope; never host authentication")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--changed", action="append", default=[])
    args = parser.parse_args(argv)
    if args.action == "hook":
        return run_hook(governor=governor)
    observation_errors: list[str] = []
    try:
        workspace = Path(args.workspace).resolve()
        rows = read_events(workspace)
        session = session_id({})
        run = active_run(rows, session, counter({}, session).get("parent"))
        if args.action in {"report", "attach"}:
            run = next((r for r in reversed(rows) if r.get("kind") == "start"
                        and (not args.run or r.get("run") == args.run)), None)
        controller = governor or _controller(workspace, str(run["session"]) if run else session, args.profile)
        # A protected binding can outlive every workspace projection.
        protected = controller.report()
        if protected.get("run") and (run is None or args.action not in {"report", "attach"}):
            run = next((r for r in reversed(rows) if r.get("kind") == "start"
                        and r.get("run") == protected["run"]), None) or {
                            "kind": "start", "run": protected["run"], "session": protected["root"],
                            "phase": protected["visits"][0]["phase"], "goal": protected.get("goal", ""),
                            "at": protected.get("started_at")}
        if args.run and args.action not in {"report", "attach"}:
            workflow.require(run and args.run == run["run"], "state_unavailable", "Run does not match the active binding.")
        artifacts: dict[str, Any] = {}
        for key in ("tasks", "reviews"):
            if getattr(args, key):
                artifacts[key] = str(artifact(workspace, getattr(args, key)).relative_to(workspace))
        if args.evidence:
            artifacts["evidence"] = [str(artifact(workspace, p).relative_to(workspace)) for p in args.evidence]
        if args.changed:
            for path in args.changed:
                (workspace / path).resolve().relative_to(workspace)
            artifacts["changed"] = args.changed
        if args.action == "start":
            from . import workflow_evidence
            scope = workflow_evidence.object_file(workspace, args.scope) if args.scope else None
            state = controller.start({"entry": args.phase or "product", "standalone": args.standalone,
                                      "goal": args.goal, "native_reference": args.native_event,
                                      "scope": scope, "request_reference": args.request_reference})
            if not any(r.get("kind") == "start" and r.get("run") == state["run"] for r in rows):
                graph = depgraph.scan(str(workspace), decompose=True)
                workflow.require(not depgraph.scan_quality(graph).get("degraded"),
                                 "invalid_evidence", "Repair the source graph before preparing a checkpoint.")
                if "tasks" not in artifacts:
                    task_path = storage.runtime_file(str(workspace), "tasks.json")
                    primitives.atomic_json(task_path, {"tasks": [{
                        "id": "SCOPE", "title": args.goal or "Prepare the requested outcome",
                        "owner": state["root"], "dependencies": [], "status": "working",
                        "paths": state["scope"]["paths"][workflow.current(state)["phase"]],
                        "criteria": state["scope"]["criteria"],
                        "verification": "Produce criterion evidence and request human stage acceptance."}]})
                    artifacts["tasks"] = str(task_path.relative_to(workspace))
                run = {"kind": "start", "run": state["run"], "session": state["root"],
                       "at": state.get("started_at"), "goal": state.get("goal", args.goal),
                       "phase": workflow.current(state)["phase"], "artifacts": artifacts,
                       "hook_setup": "host_adapter", **counter({}, session)}
                append(workspace, run)
        elif args.action in {"submit", "decide", "advance", "finish"}:
            workflow.require(run, "state_unavailable", "No active workflow binding.")
            assert run is not None
            state = controller.apply(args.action, str(run["run"]),
                expected_revision=args.expected_revision, output=args.output or "",
                tasks=args.tasks or "", phase=args.phase,
                native_reference=args.decision_json if controller.adapter.profile == "native_workflow" else args.native_event)
            try:
                append(workspace, {"kind": args.action, "run": run["run"], "session": run["session"],
                                   "phase": workflow.current(state)["phase"], "note": args.note[:1000],
                                   "artifacts": artifacts})
            except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
                # The authoritative action has already committed. Observation
                # failure cannot turn its actual result into a rejected command.
                observation_errors.append("Workflow action committed; optional journal recording unavailable.")
        elif args.action in {"progress", "attach"}:
            workflow.require(run, "state_unavailable", "No run to attach observations to.")
            assert run is not None
            if args.action == "progress" and args.phase:
                current_phase = protected.get("phase") or summarize(rows, run)["phase"]
                if args.phase != current_phase:
                    raise workflow.Refusal("approval_required", "Progress cannot advance a phase. Use the explicit advance action after human approval.")
            append(workspace, {"kind": args.action, "run": run["run"], "session": run["session"],
                               "phase": args.phase[:80], "note": args.note[:1000],
                               "artifacts": artifacts, **counter({}, session)})
        result = report(workspace, str(run["run"]) if run else args.run, governor=controller)
        if result:
            result["evidence_errors"].extend(observation_errors)
        if result and run and args.action != "report":
            try:
                if result.get("sessions"):
                    append(workspace, {"kind": "usage", "run": run["run"], "session": run["session"],
                        "measurement": {k: result[k] for k in ("sessions", "tokens", "native_tokens", "token_coverage")}})
                from taskplane import dashboard, flow_dashboard
                document = dashboard.standalone_document([flow_dashboard.render(str(workspace), result)],
                                                         title="Taskplane — delivery")
                primitives.atomic_write_bytes(str(storage.runtime_file(str(workspace), "dashboard.html")), document.encode())
            except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
                result["evidence_errors"].append("Optional observations or dashboard refresh unavailable.")
        print(json.dumps(result if result else controller.availability(), indent=2))
        return 0
    except workflow.Refusal as exc:
        print(json.dumps(exc.result(), indent=2))
        return 2
    except (OSError, ValueError, TypeError, KeyError, primitives.StateError) as exc:
        print(json.dumps({"status": "blocked", "reason": "state_unavailable",
                          "detail": str(exc), "tokens": None}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
