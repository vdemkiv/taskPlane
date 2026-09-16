"""Delivery observations, never execution or permission decisions.

The orchestrator owns progress. This journal stores metadata and native token
counters, not prompts, command text, tool responses, or permission receipts.
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
import uuid
from typing import Any, Callable

if __package__:
    from . import native_session_meter as _package_meter
    from . import flow_usage as _package_usage
    from . import claude_flow_usage as _package_claude
    native_session_meter, flow_usage, claude_flow_usage = _package_meter, _package_usage, _package_claude
else:
    import native_session_meter as _flat_meter
    import flow_usage as _flat_usage
    import claude_flow_usage as _flat_claude
    native_session_meter, flow_usage, claude_flow_usage = _flat_meter, _flat_usage, _flat_claude


JOURNAL = ".taskplane/flow-events.jsonl"
DASHBOARD = ".taskplane/dashboard.html"
HOOK_NAMES = {
    "screen": "PreToolUse", "screen-dispatch": "PreToolUse",
    "screen-skill": "PreToolUse", "screen-render": "PreToolUse",
    "context": "SessionStart", "host-native-check": "SessionStart",
    "subagent-start": "SubagentStart", "subagent-stop": "SubagentStop",
    "session-verify": "Stop",
}


def read_events(workspace: Path) -> list[dict[str, Any]]:
    try:
        with (workspace / JOURNAL).open(encoding="utf-8") as stream:
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
    path = workspace / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    payload = json.dumps({"at": datetime.now(timezone.utc).isoformat(), **row}) + "\n"
    # One append write keeps simultaneous worker observations together.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, payload.encode())
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
    if not path and session != "local":
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        candidates = list((home / "sessions").glob(f"**/*{session}*.jsonl"))
        if len(candidates) == 1:
            path = str(candidates[0])
    if isinstance(path, str):
        try:
            snapshot = native_session_meter.read_snapshot(path, allow_unsequenced=True)
            if snapshot["session_id"] == session:
                return {"usage": snapshot["usage"],
                        "parent": snapshot.get("parent_session_id"),
                        "agent": snapshot.get("agent_path"),
                        "usage_status": "observed"}
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
        "phase": milestones[-1].get("phase") if milestones else "product",
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


def report(workspace: Path, run_id: str | None = None) -> dict[str, Any] | None:
    rows = read_events(workspace)
    run = next((r for r in reversed(rows) if r.get("kind") == "start"
                and (not run_id or r.get("run") == run_id)), None)
    if not run:
        return None
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
    return result


def hook(event: dict[str, Any]) -> dict[str, Any]:
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


def run_hook(command: str | None = None) -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            event = {}
        if command:
            event.setdefault("hook_event_name", HOOK_NAMES[command])
        result = hook(event)
    except Exception:
        # A telemetry failure must never become an execution failure.
        result = {}
    print(json.dumps(result))
    return 0


def main(argv: list[str] | None = None, *,
         prepare: Callable[[str], dict[str, Any]] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "progress", "finish", "report", "attach", "hook"])
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--goal", default="")
    parser.add_argument("--phase", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--run", help="Select an existing run for report or evidence attachment")
    parser.add_argument("--tasks", help="Workspace-relative task decomposition JSON")
    parser.add_argument("--reviews", help="Workspace-relative lens review index JSON")
    parser.add_argument("--evidence", action="append", default=[], help="Workspace-relative evidence file")
    parser.add_argument("--changed", action="append", default=[], help="Workspace-relative changed path for impact analysis")
    args = parser.parse_args(argv)
    if args.action == "hook":
        return run_hook()
    try:
        workspace = Path(args.workspace)
        rows = read_events(workspace)
        session = session_id({})
        run = active_run(rows, session, counter({}, session).get("parent"))
        if args.action in {"report", "attach"}:
            run = next((r for r in reversed(rows) if r.get("kind") == "start"
                        and (not args.run or r.get("run") == args.run)), None)
        artifacts: dict[str, Any] = {}
        for key in ("tasks", "reviews"):
            if getattr(args, key):
                artifacts[key] = str(artifact(workspace, getattr(args, key)).relative_to(workspace.resolve()))
        if args.evidence:
            artifacts["evidence"] = [str(artifact(workspace, p).relative_to(workspace.resolve()))
                                     for p in args.evidence]
        if args.changed:
            for path in args.changed:
                (workspace / path).resolve().relative_to(workspace.resolve())
            artifacts["changed"] = args.changed
        if args.action == "start":
            if run is None:
                setup = "not_observed"
                if prepare is not None:
                    try:
                        setup = "ready" if prepare(str(workspace)).get("ok") else "unavailable"
                    except Exception:
                        pass  # Native hook setup is optional observation, never a gate.
                run = {"kind": "start", "run": uuid.uuid4().hex, "session": session,
                       "at": datetime.now(timezone.utc).isoformat(),
                       "goal": args.goal[:2000], "hook_setup": setup,
                       "artifacts": artifacts, **counter({}, session)}
                append(workspace, run)
                rows.append(run)
        elif args.action != "report" and run is not None:
            row = {"kind": args.action, "run": run["run"], "session": session,
                   "phase": args.phase[:80], "note": args.note[:1000],
                   "artifacts": artifacts, **counter({}, session)}
            if args.action == "attach":
                row = {"kind": "attach", "run": run["run"], "session": run["session"],
                       "artifacts": artifacts}
            append(workspace, row)
            rows.append(row)
        if args.action == "report" and run is None:
            run = next((row for row in reversed(rows) if row.get("kind") == "start"), None)
        result = report(workspace, run["run"]) if run else None
        if result and run and args.action in {"progress", "finish", "attach"} and result.get("sessions"):
            append(workspace, {"kind": "usage", "run": run["run"], "session": run["session"],
                               "measurement": {k: result[k] for k in
                                               ("sessions", "tokens", "native_tokens", "token_coverage")}})
        if result and args.action != "report":
            try:
                if __package__:
                    from . import dashboard as _package_dashboard
                    from . import flow_dashboard as _package_flow_dashboard
                    dashboard, flow_dashboard = _package_dashboard, _package_flow_dashboard
                else:
                    import dashboard as _flat_dashboard
                    import flow_dashboard as _flat_flow_dashboard
                    dashboard, flow_dashboard = _flat_dashboard, _flat_flow_dashboard
                target = workspace / DASHBOARD
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(dashboard.standalone_document(
                    [flow_dashboard.render(str(workspace), result)], title="Taskplane — delivery"),
                    encoding="utf-8")
            except (OSError, ValueError, TypeError, KeyError):
                result["evidence_errors"].append("Dashboard refresh unavailable; delivery continues")
        print(json.dumps(result if result else {
            "mode": "advisory", "status": "no_observations", "tokens": None}, indent=2))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"mode": "advisory", "status": "telemetry_unavailable",
                          "error": type(exc).__name__, "tokens": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
