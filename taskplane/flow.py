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

if __package__:
    from . import native_session_meter
else:
    import native_session_meter


JOURNAL = ".taskplane/flow-events.jsonl"
HOOK_NAMES = {
    "screen": "PreToolUse", "screen-dispatch": "PreToolUse",
    "screen-skill": "PreToolUse", "screen-render": "PreToolUse",
    "context": "SessionStart", "host-native-check": "SessionStart",
    "subagent-start": "SubagentStart", "subagent-stop": "SubagentStop",
    "session-verify": "Stop",
}


def read_events(workspace: Path) -> list[dict]:
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


def append(workspace: Path, row: dict) -> None:
    path = workspace / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"at": datetime.now(timezone.utc).isoformat(), **row}) + "\n"
    # One append write keeps simultaneous worker observations together.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, payload.encode())
    finally:
        os.close(fd)


def session_id(event: dict) -> str:
    return str(event.get("session_id") or event.get("thread_id")
               or os.environ.get("CODEX_THREAD_ID")
               or os.environ.get("CLAUDE_SESSION_ID") or "local")


def counter(event: dict, session: str) -> dict:
    path = event.get("transcript_path") or event.get("transcript")
    if not path and session != "local":
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        candidates = list((home / "sessions").glob(f"**/*{session}*.jsonl"))
        if len(candidates) == 1:
            path = str(candidates[0])
    if isinstance(path, str):
        try:
            snapshot = native_session_meter.read_snapshot(path)
            if snapshot["session_id"] == session:
                return {"usage": snapshot["usage"],
                        "parent": snapshot.get("parent_session_id"),
                        "usage_status": "observed"}
        except (OSError, ValueError):
            pass
    return {"usage": None, "usage_status": "unavailable"}


def active_run(rows: list[dict], session: str, parent: str | None = None) -> dict | None:
    owners = {session, parent} - {None}
    for row in reversed(rows):
        if row.get("session") in owners:
            owners.add(row.get("root"))
    closed = {row.get("run") for row in rows if row.get("kind") == "finish"}
    return next((row for row in reversed(rows)
                 if row.get("kind") == "start" and row.get("session") in owners
                 and row.get("run") not in closed), None)


def summarize(rows: list[dict], run: dict) -> dict:
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
    sessions: dict[str, list[dict]] = {}
    for row in unique:
        sessions.setdefault(row["session"], []).append(row)
    usage = Counter()
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
        "milestones": [{"phase": r.get("phase"), "note": r.get("note")} for r in milestones],
        "tool_calls": sum(r.get("event") == "PreToolUse" for r in unique),
        "tokens": dict(usage) if measured else None,
        "token_coverage": {"measured_sessions": measured, "unmeasured_sessions": unmeasured,
                           "basis": "delta between first and latest observed native counters; lower bound"},
        "advice": warnings,
    }


def hook(event: dict) -> dict:
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


def main(argv: list[str] | None = None, *, prepare=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "progress", "finish", "report", "hook"])
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--goal", default="")
    parser.add_argument("--phase", default="")
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)
    if args.action == "hook":
        return run_hook()
    try:
        workspace = Path(args.workspace)
        rows = read_events(workspace)
        session = session_id({})
        run = active_run(rows, session)
        if args.action == "start":
            if run is None:
                setup = "unavailable"
                if prepare is not None:
                    try:
                        setup = "ready" if prepare(str(workspace)).get("ok") else "unavailable"
                    except Exception:
                        pass  # Native hook setup is optional observation, never a gate.
                run = {"kind": "start", "run": uuid.uuid4().hex, "session": session,
                       "goal": args.goal[:2000], "hook_setup": setup, **counter({}, session)}
                append(workspace, run)
                rows.append(run)
        elif args.action != "report" and run is not None:
            row = {"kind": args.action, "run": run["run"], "session": session,
                   "phase": args.phase[:80], "note": args.note[:1000], **counter({}, session)}
            append(workspace, row)
            rows.append(row)
        if args.action == "report" and run is None:
            run = next((row for row in reversed(rows) if row.get("kind") == "start"), None)
        print(json.dumps(summarize(rows, run) if run else {
            "mode": "advisory", "status": "no_observations", "tokens": None}, indent=2))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"mode": "advisory", "status": "telemetry_unavailable",
                          "error": type(exc).__name__, "tokens": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
