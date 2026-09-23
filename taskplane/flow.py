"""Shared observations plus guarded workflow entry points.

The workspace journal is never approval authority. Mandatory transitions use
the protected workflow controller; optional usage observation stays advisory.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import stat
import uuid
from typing import Any, Callable
from taskplane import workflow, workflow_host, workflow_local, depgraph

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
    if event.get("host") in {"codex", "claude"}:
        return bool(event["host"] == "claude")
    if event.get("thread_id"):
        return False
    transcript = event.get("transcript_path") or event.get("transcript")
    if isinstance(transcript, str):
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()
        path = Path(transcript).resolve()
        if any(path.is_relative_to(home / folder) for folder in ("sessions", "archived_sessions")):
            return False  # Native hooks can omit CODEX_THREAD_ID from their environment.
    if (os.environ.get("CLAUDECODE") or os.environ.get("TASKPLANE_CLAUDE_SESSION_ID")
            or os.environ.get("CLAUDE_SESSION_ID")):
        return True
    # Codex exports CLAUDE_PLUGIN_ROOT for hook compatibility. It is not a
    # transcript-format signal when an actual Codex session is available.
    if os.environ.get("CODEX_THREAD_ID"):
        return False
    return bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))


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


def observed_parent(event: dict[str, Any], session: str) -> str | None:
    """Read native lineage independently of whether usage has been emitted yet."""
    if claude_session(event):
        return None  # Claude ancestry comes from parent fields and child-start events.
    path = event.get("transcript_path") or event.get("transcript")
    candidates = [Path(path)] if isinstance(path, str) else []
    if session != "local":
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        candidates.extend((home / "sessions").glob(f"**/*{session}*.jsonl"))
    parents: set[str] = set()
    for candidate in candidates:
        try:
            fd = os.open(candidate, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    continue
                # Share the canonical metadata parser, but require no token counter.
                metadata, _ = native_session_meter._session_metadata(
                    stream.read(native_session_meter.MAX_METADATA_BYTES))
            if metadata["session_id"] == session and metadata.get("parent_session_id"):
                parents.add(str(metadata["parent_session_id"]))
        except (OSError, ValueError):
            continue
    return next(iter(parents)) if len(parents) == 1 else None


def active_run(rows: list[dict[str, Any]], session: str, parent: str | None = None) -> dict[str, Any] | None:
    owners = {session, parent} - {None}
    for row in reversed(rows):
        root = row.get("root")
        if isinstance(root, str) and root and (row.get("session") in owners or (
                row.get("event") == "SubagentStart" and row.get("child") in owners)):
            owners.add(root)
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
        if row.get("kind") in {"attach", "usage", "usage_boundary"}:
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


def _controller(workspace: Path, root: str, profile: str = "native_workflow",
                *, event: dict[str, Any] | None = None) -> workflow_host.Controller:
    return workflow_host.Controller(workspace, root,
        workflow_host.installed_adapter("claude" if claude_session(event or {}) else "codex", profile))


def usage_point(state: dict[str, Any], measurement: dict[str, Any], *,
                observed_at: str | None = None, previous_revision: int | None = None) -> dict[str, Any]:
    stage = workflow.current(state)
    return {"kind": "usage_boundary", "schema": "taskplane.phase-usage/v1", "run": state["run"],
            "session": state["root"], "revision": state["revision"], "previous_revision": previous_revision,
            "phase": stage["phase"], "visit": stage["id"],
            "bucket": "follow_up" if state["finished"] else "review" if stage["decision"] in
                      ("awaiting_human_approval", "approved") else "work",
            "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
            "sessions": deepcopy(measurement.get("sessions", [])),
            "coverage": deepcopy(measurement.get("token_coverage", {}))}



def measurement_time(value: Any) -> str | None:
    """A missing saved timestamp cannot be reconstructed from a later read."""
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
        return stamp.astimezone(timezone.utc).isoformat() if stamp and stamp.tzinfo else None
    except ValueError:
        return None


def usage_measurement(measurement: dict[str, Any], attempted_at: str) -> dict[str, Any]:
    delivery = [s for s in measurement.get("sessions", []) if s.get("role") != "host_approval_review"]
    usable = [s for s in delivery if isinstance(s.get("usage"), dict) and s["usage"]]
    times = [measurement_time(s.get("measured_at")) for s in usable]
    known = sorted({t for t in times if t is not None})
    complete = (bool(usable) and len(usable) == len(delivery) and all(times)
                and not measurement.get("token_coverage", {}).get("discovery_errors"))
    statuses = {"recorded" if str(s.get("status", "")).startswith("recorded") else s.get("status") for s in usable}
    status = ("unavailable" if not usable else "partial" if not complete else
              "fresh" if statuses == {"measured"} else "recorded" if statuses == {"recorded"} else
              "mixed" if statuses == {"measured", "recorded"} else "partial")
    return {"status": status, "attempted_at": attempted_at,
            "measured_at": known[0] if status in ("fresh", "recorded") and len(known) == 1 else None,
            "oldest_at": known[0] if known else None, "newest_at": known[-1] if known else None}

def phase_usage(rows: list[dict[str, Any]], state: dict[str, Any],
                measurement: dict[str, Any]) -> dict[str, Any]:
    """Derived, reconciled attribution; native evidence and approval stay unchanged."""
    points = [r for r in rows if r.get("run") == state["run"] and r.get("kind") == "usage_boundary"]
    endpoint = usage_point(state, measurement, previous_revision=state["revision"])
    return flow_usage.phase_accounting(points, endpoint, state, measurement)


def record_scan(workspace: Path, graph: dict[str, Any]) -> None:
    primitives.atomic_json(storage.runtime_file(str(workspace), "graph-receipt.json"), {
        "schema": "taskplane.graph-receipt/v1", "workspace": str(workspace.resolve()),
        "graph_digest": primitives.content_fingerprint(graph),
        "scanned_at": graph.get("meta", {}).get("scanned_at"),
        "source_revision": graph.get("meta", {}).get("scanned_head")})


def capture_attachments(workspace: Path, artifacts: dict[str, Any]) -> dict[str, Any]:
    """Capture explicitly attached view inputs, never a whole conversation."""
    captured: dict[str, Any] = {}
    for key in ("tasks", "reviews"):
        if artifacts.get(key):
            try:
                captured[key] = json.loads(artifact(workspace, artifacts[key]).read_text())
            except (OSError, ValueError):
                captured[key] = None
    for relative in artifacts.get("evidence", []):
        try:
            raw = artifact(workspace, relative).read_bytes()
            captured.setdefault("evidence", {})[relative] = {
                "digest": primitives.content_fingerprint(raw), "text": raw[:48000].decode("utf-8", errors="replace"),
                "limited": len(raw) > 48000}
        except (OSError, ValueError):
            captured.setdefault("evidence", {})[relative] = None
    return captured


def _graph_view(workspace: Path, state: dict[str, Any], historical: bool) -> dict[str, Any]:
    packets = [v["packet"] for v in state.get("visits", []) if v.get("packet")]
    graph = deepcopy(packets[-1]["context"]["graph"]) if historical and packets else depgraph.load(str(workspace))
    quality = depgraph.scan_quality(graph)
    receipt: dict[str, Any] = {}
    try:
        receipt = json.loads(storage.runtime_file(str(workspace), "graph-receipt.json").read_text())
    except (OSError, ValueError):
        pass
    if historical and packets:
        receipt = packets[-1]["context"].get("graph_receipt") or {}
    bound = (receipt.get("workspace") == str(workspace.resolve())
             and receipt.get("graph_digest") == primitives.content_fingerprint(graph))
    current = bool(graph.get("modules")) and depgraph.source_inputs_current(str(workspace), graph)
    status = "at checkpoint" if historical and packets else "current" if bound and current and not quality.get("degraded") else "unverified or stale"
    return {"data": graph, "status": status, "workspace": str(workspace.resolve()),
            "receipt": receipt if bound else None, "source_inputs_current": current,
            "quality": quality, "fingerprint": primitives.content_fingerprint(graph),
            "detail": "Historical sealed graph" if historical else "Workspace/input identity verified" if status == "current" else
                      "Missing/foreign scan receipt, changed inputs or degraded scan; run graph scan --decompose --strict."}


def graph_view(workspace: Path, state: dict[str, Any], historical: bool) -> dict[str, Any]:
    try:
        return _graph_view(workspace, state, historical)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"data": {}, "status": "unavailable", "workspace": str(workspace.resolve()),
                "receipt": None, "source_inputs_current": False, "quality": {"degraded": True},
                "fingerprint": None, "detail": "Optional graph observation failed; regenerate after repairing scan access."}


def report(workspace: Path, run_id: str | None = None, *,
           governor: workflow_host.Controller | None = None) -> dict[str, Any] | None:
    captured_at = datetime.now(timezone.utc).isoformat()
    rows = read_events(workspace)
    workspace = workspace.resolve()
    owner = governor.root if governor else session_id({})
    run = next((r for r in reversed(rows) if r.get("kind") == "start"
                and (r.get("run") == run_id if run_id else r.get("session") == owner)), None)
    controller = governor or _controller(workspace, str(run["session"]) if run else session_id({}))
    try:
        governed = controller.report(run_id or (str(run["run"]) if run else None))
    except workflow.Refusal as exc:
        governed = {**exc.result(), "authority_verified": False}
    # Preserve the old journal-only advisory view, explicitly unverified. Native
    # workflow journals always select by task or --run and never use this fallback.
    legacy_only = (not any(r.get("kind") == "start" and r.get("hook_setup") == "host_adapter" for r in rows)
                   and not any(Path(storage.tp_dir(str(workspace))).glob("workflow-*.json")))
    if run_id is None and governor is None and legacy_only and not governed.get("visits"):
        run = next((r for r in reversed(rows) if r.get("kind") == "start"), None)
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
    captured: dict[str, Any] = {}
    for row in rows:
        if row.get("run") == run["run"]:
            incoming = row.get("artifacts") or {}
            evidence = list(dict.fromkeys(attachments.get("evidence", []) + incoming.get("evidence", [])))
            attachments.update(incoming)
            if evidence:
                attachments["evidence"] = evidence
            incoming_view = row.get("captured", {})
            previews = {**captured.get("evidence", {}), **incoming_view.get("evidence", {})}
            captured.update(incoming_view)
            captured["evidence"] = previews
    result["artifacts"] = attachments
    result["tasks"] = []
    result["reviews"] = []
    result["evidence_errors"] = []
    for key in ("tasks", "reviews"):
        if not attachments.get(key):
            continue
        try:
            data = captured[key] if key in captured else json.loads(artifact(workspace, attachments[key]).read_text(encoding="utf-8"))
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
    measured_at = datetime.now(timezone.utc).isoformat()
    for measured in result.get("sessions", []):
        if (str(measured.get("status", "")).startswith("recorded")
                or measured.get("measurement_source") == "run_boundary"):
            measured["measured_at"] = measurement_time(measured.get("measured_at"))
        else:
            measured["measured_at"] = measured_at if measured.get("usage") and measured.get("native_usage") else None
        if measured.get("session") == run["session"] and run.get("usage") and measured.get("native_usage"):
            if any(measured["native_usage"].get(k, -1) < v for k,v in run["usage"].items()):
                measured.update(usage=None, measured_at=None, status="partial; counters reset below run baseline")
    if result.get("sessions"):
        delivery = [s for s in result["sessions"] if s.get("role") != "host_approval_review"]
        values = [s["usage"] for s in delivery if s.get("usage") is not None]
        result["tokens"] = {k:sum(v.get(k, 0) for v in values) for k in values[0]} if values else None
        result["token_coverage"].update(measured_sessions=sum(s.get("usage") is not None for s in delivery),
            unmeasured_sessions=sum(s.get("usage") is None for s in delivery),
            partial_sessions=sum(s.get("status") != "measured" for s in delivery))
    result["usage_measurement"] = usage_measurement(result, measured_at)
    result["evidence_previews"] = captured.get("evidence", {})
    result["workflow"] = governed
    result["observation_status"] = result["status"]
    if governed.get("visits"):
        result["phase"] = governed["phase"]
        result["status"] = governed["status"]
        active = controller.report()
        historical = bool(governed.get("finished") or active.get("run") != governed["run"])
        if historical and "reviews" not in captured:
            result["reviews"] = []
            if attachments.get("reviews"):
                result["evidence_errors"].append("Historical reviewer context was not captured; mutable file not reused.")
        stage = workflow.current(governed)
        if stage.get("packet"):
            result["tasks"] = deepcopy(stage["packet"]["context"]["tasks"])
        for task in result["tasks"]:
            candidates = [v for v in governed["visits"] if v["phase"] == task.get("phase") and not v.get("superseded")]
            if candidates:
                visit = candidates[-1]
                task["phase_decision"] = visit["decision"]
                if visit["decision"] == "approved":
                    task["status"] = "complete"
                elif visit["packet"]:
                    task["status"] = "ready_for_review" if visit["decision"] == "awaiting_human_approval" else visit["decision"]
        result["phase_usage"] = phase_usage(rows, governed, result)
        result["historical"] = historical
        result["graph"] = graph_view(workspace, governed, historical)
        result["snapshot"] = {"schema": "taskplane.dashboard-snapshot/v1", "workspace": str(workspace),
            "root": governed["root"], "run": governed["run"], "phase": governed["phase"], "visit": stage["id"],
            "revision": governed["revision"], "policy_revision": governed.get("approval_policy", {}).get("revision"),
            "source_revision": depgraph._git_head(str(workspace)),
            "graph_fingerprint": result["graph"]["fingerprint"], "historical": historical,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "captured_at": captured_at,
            "observation_count": sum(r.get("run") == governed["run"] for r in rows),
            "measurement_at": result["usage_measurement"]["measured_at"],
            "measurement_status": result["usage_measurement"]["status"],
            "measurement_oldest_at": result["usage_measurement"]["oldest_at"],
            "measurement_newest_at": result["usage_measurement"]["newest_at"],
            "measurement_attempted_at": result["usage_measurement"]["attempted_at"],
            "last_observation_at": next((r.get("at") for r in reversed(rows) if r.get("run") == governed["run"]), None)}
    else:
        result["status"] = "legacy_unverified"
        result["graph"] = graph_view(workspace, {}, False)
        result["graph"]["status"] = "legacy unverified"
        result["graph"]["detail"] = "Advisory journal only; no captured workflow provenance."
    return result


def older_snapshot(previous: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    """Compare observations as well as gates; progress need not change revision."""
    old_revision, revision = previous.get("revision", -1), snapshot.get("revision", -1)
    if old_revision != revision:
        return bool(old_revision > revision)
    old_count, count = previous.get("observation_count"), snapshot.get("observation_count")
    if type(old_count) is int and type(count) is int and old_count != count:
        return bool(old_count > count)
    # A slow reader can finish after a newer report: use capture start, not
    # render completion. Older selection files have only a generation timestamp.
    old_time = measurement_time(previous.get("captured_at")) or measurement_time(previous.get("generated_at"))
    captured = measurement_time(snapshot.get("captured_at")) or measurement_time(snapshot.get("generated_at"))
    return bool(old_time and (not captured or captured <= old_time))


def publish_dashboard(workspace: Path, run_id: str | None = None, *, output: Path | None = None,
                      select: bool = False, governor: workflow_host.Controller | None = None,
                      supplied: dict[str, Any] | None = None) -> Path:
    """One renderer and immutable generation per explicit/task-bound run."""
    from taskplane import dashboard, flow_dashboard
    workspace = workspace.resolve()
    for attempt in range(3):
        model = supplied if attempt == 0 and supplied else report(workspace, run_id, governor=governor)
        workflow.require(model, "state_unavailable", "No run belongs to this task; select an existing --run explicitly.")
        assert model is not None
        governed = model.get("workflow", {})
        controller = governor or _controller(workspace, str(governed.get("root") or session_id({})))
        target = output or storage.runtime_file(str(workspace), "dashboard.html")
        target = target.absolute()
        workflow.require(target.suffix == ".html" and not target.is_symlink(), "scope_violation", "Dashboard target must be a regular HTML path.")
        snapshot = model.setdefault("snapshot", {"schema": "taskplane.dashboard-snapshot/v1", "run": model["run"],
            "workspace": str(workspace), "generated_at": datetime.now(timezone.utc).isoformat(), "historical": True})
        snapshot["presentation_target"] = str(target)
        snapshot["digest"] = primitives.content_fingerprint({k:v for k,v in model.items() if k != "snapshot"} | {
            "snapshot": {k:v for k,v in snapshot.items() if k != "digest"}})
        document = dashboard.standalone_document([flow_dashboard.render(str(workspace), model)], title="Taskplane — delivery")
        if governed.get("visits"):
            after = controller.report(model["run"])
            if (after.get("revision"), after.get("status"), after.get("invalidation_pending")) != (
                    governed.get("revision"), governed.get("status"), governed.get("invalidation_pending")):
                continue
            if model.get("graph", {}).get("status") == "current" and not depgraph.source_inputs_current(str(workspace), model["graph"]["data"]):
                continue
        prefix = "snapshot-" + hashlib.sha256(str(model["run"]).encode()).hexdigest()[:16] + "-" + snapshot["digest"]
        immutable = storage.runtime_file(str(workspace), prefix + ".html")
        primitives.atomic_write_bytes(str(immutable), document.encode())
        primitives.atomic_json(storage.runtime_file(str(workspace), prefix + ".json"), model)
        selection = target.with_suffix(".selection.json")
        workflow.require(not selection.is_symlink(), "scope_violation", "Dashboard selection cannot follow a link.")
        with primitives.file_lock(str(target)):
            previous = json.loads(selection.read_text()) if selection.exists() else {}
            if previous and previous.get("run") != model["run"] and not select:
                # Concurrent background publishers retain their independent snapshot.
                return immutable
            if previous.get("run") == model["run"] and older_snapshot(previous, snapshot):
                return immutable
            primitives.atomic_write_bytes(str(target), document.encode())
            primitives.atomic_json(selection, {"workspace": str(workspace), "root": snapshot.get("root"),
                "run": model["run"], "revision": snapshot.get("revision", -1), "digest": snapshot["digest"],
                "captured_at": snapshot.get("captured_at"), "observation_count": snapshot.get("observation_count"),
                "generated_at": snapshot["generated_at"], "snapshot": str(immutable),
                "presentation": "generated; opening and visible verification are host observations"})
        return target.resolve()
    raise workflow.Refusal("stale_checkpoint", "Dashboard inputs changed during publication; regenerate the selected run.")


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
    workspace = Path(event.get("cwd") or os.getcwd()).resolve()
    rows, session = read_events(workspace), session_id(event)
    parent = event.get("parent_session_id")
    legacy = active_run(rows, session, parent)
    controller = governor or _controller(workspace, session, event=event)
    guarded = controller.report()
    # A task's own active binding survives a missing journal and takes precedence
    # over ancestry. Only an otherwise unbound child inherits its parent's guard.
    if not guarded.get("run") and governor is None:
        if legacy is None:
            try:
                parent = observed_parent(event, session) or parent
            except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
                pass
            legacy = active_run(rows, session, parent)
        if legacy or parent:
            controller = _controller(workspace, str(legacy["session"] if legacy else parent), event=event)
            guarded = controller.report()
    name = event.get("hook_event_name")
    harness = workflow_local.Harness(workspace, controller.root) if controller.adapter.profile == "native_workflow" else None
    selected = workflow_local.execution_entry(event)
    if harness:
        harness.update(hook_observed=True)
        if name == "PostToolUse":
            harness.observe_recovery(event, guarded)
        if selected:
            selection_reference = str(event.get("tool_use_id") or event.get("call_id") or event.get("turn_id") or
                            "observed/" + hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest())[:512]
            harness.select(selected, selection_reference, guarded)
        elif name == "UserPromptSubmit" or name == "PreToolUse":
            if harness.read().get("waiting"):
                harness.update(waiting=None)
        if guarded.get("visits") and not guarded.get("finished"):
            harness.bind(guarded)
        elif harness.read().get("run") and not guarded.get("run"):
            prior = controller.report(harness.read()["run"])
            if prior.get("finished"):
                harness.update(selected=False, waiting=None)
        if name == "PreToolUse" and harness.read().get("selected"):
            if not guarded.get("run"):
                harness.guard_bootstrap(event, guarded)
            if (event.get("tool_name") or event.get("tool")) in workflow_local.QUESTION_TOOLS:
                harness.wait(guarded, "Native question requested; awaiting the user's response.")
    # Protected state is checked independently of the deletable workspace journal.
    run = guarded.get("run") or (legacy or {}).get("run")
    if guarded.get("workflow_available") and guarded.get("run"):
        controller.observe(event, str(run))
        if name == "Stop" and not controller.adapter.can_seal(guarded):
            return {"systemMessage": "Taskplane is waiting for process quiescence before sealing. No phase has advanced; unknown host coverage remains explicit."}
    guarded_run = guarded.get("run") or (run if controller.adapter.profile == "protected_host" else None)
    if guarded_run and name == "PreToolUse":
        controller.guard(event, str(run))
    if name == "Stop" and harness:
        stopped = harness.stop(event, guarded)
        if stopped:
            return stopped
    if run and name == "UserPromptSubmit":
        if not guarded.get("workflow_available") or not guarded.get("run"):
            return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                    "Taskplane approval remains unverified; this prompt does not authorize a workflow transition."}}
        policy_event = event.get("taskplane_policy")
        if isinstance(policy_event, dict):
            controller.apply("policy", str(run), expected_revision=guarded["revision"],
                             native_reference=json.dumps(policy_event))
            guarded = controller.report(str(run))
        if guarded.get("status") == "awaiting_human_approval" and not policy_event:
            reference = controller.adapter.prompt_reference(event, guarded)
            if reference:
                controller.apply("decide", str(run), expected_revision=guarded["revision"], native_reference=reference)
                guarded = controller.report(str(run))
            else:
                return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                        "Taskplane: record the actual human response with its presented checkpoint and conversation provenance. This prompt alone has not advanced the workflow."}}
    try:
        result = _observe_hook(event)
    except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
        result = {}  # Only optional observations abstain on failure.
    if harness and harness.read().get("selected") and (selected or name in {"SessionStart", "UserPromptSubmit"}):
        output = result.setdefault("hookSpecificOutput", {"hookEventName": name})
        output["additionalContext"] = harness.guidance(guarded) + " " + output.get("additionalContext", "")
    return result


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


def emit(payload: dict[str, Any], workspace: Path, action: str, *, full: bool = False) -> None:
    """Bound shipped CLI transport while preserving full Python reports and hook envelopes."""
    from .context import Store, encode
    from .context_handoff import Session
    from .context_views import summary
    if full:
        print(json.dumps(payload, indent=2))
        return
    state = payload.get("workflow", payload)
    context: dict[str, Any] = {"status": "not_applicable"}
    try:
        if state.get("visits") and not state.get("finished") and not payload.get("historical"):
            context = Session(workspace, state).descriptor()
    except (workflow.Refusal, OSError, ValueError, TypeError, KeyError, primitives.StateError) as exc:
        context = {"status": "unavailable", "reason": str(exc)[:512],
                   "next_action": "Repair context storage and retry flow context; do not replay a committed action."}
    try:
        result = summary(Store(workspace), payload, action, context)
    except (workflow.Refusal, OSError, ValueError, TypeError, KeyError, primitives.StateError):
        # Serialization is after the authoritative commit. Preserve its result
        # even when derived storage is unavailable, without dumping full history.
        stage = workflow.current(state) if state.get("visits") else {}
        result = {"schema": "taskplane.command-summary/v1", "action": action,
                  "status": state.get("status", "unknown"),
                  "binding": {k: state.get(k) for k in ("workspace", "root", "run", "revision", "pending_checkpoint")},
                  "phase": stage.get("phase"), "approval": {"status": stage.get("decision")},
                  "next_action": "Read the native dashboard or use --full. Do not replay a committed action.",
                  "context": context, "artifacts": {"dashboard": payload.get("dashboard")},
                  "coverage": {"details": "unavailable"}, "details": None,
                  "errors": {"reason": payload.get("reason"), "detail": str(payload.get("detail", ""))[:512],
                             "transport": "Derived context storage unavailable."}}
    print(encode(result).decode("utf-8"))


def main(argv: list[str] | None = None, *, compact: bool = False,
         prepare: Callable[[str], dict[str, Any]] | None = None,
         governor: workflow_host.Controller | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "progress", "finish", "report", "attach",
                                           "submit", "decide", "advance", "policy", "auto-decide", "hook",
                                           "activate", "present", "wait", "diagnose", "context"])
    parser.add_argument("--full", action="store_true", help="Explicit complete output; unbounded")
    parser.add_argument("--task", help="Current phase task ID for context selection")
    context_read = parser.add_mutually_exclusive_group()
    context_read.add_argument("--consume", help="Consume the current handoff SHA-256")
    context_read.add_argument("--read", help="Read a current verified reference SHA-256")
    context_read.add_argument("--read-required", help="Return a bounded batch of missing required pages for this handoff SHA-256")
    parser.add_argument("--section")
    parser.add_argument("--page", type=int)
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
    parser.add_argument("--policy-json", default="", help="Observed user policy envelope; never host authentication")
    parser.add_argument("--presentation", choices=["linked", "verified", "blocked"])
    assessments = parser.add_mutually_exclusive_group()
    assessments.add_argument("--assessment", help="Assessment JSON file for auto-decide (at most 64 KiB)")
    assessments.add_argument("--assessment-json", help="Inline assessment JSON for auto-decide (at most 64 KiB)")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--replace-run", help="Explicitly replace this active native run, preserving its evidence; start only")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--changed", action="append", default=[])
    args = parser.parse_args(argv)
    if any(value is not None for value in (args.task, args.consume, args.read, args.read_required, args.section, args.page)) and args.action != "context":
        parser.error("context selection options apply only to flow context")
    if (args.section is not None or args.page is not None) and args.read is None:
        parser.error("--section and --page require --read")
    workspace = Path(args.workspace).resolve()
    protected: dict[str, Any] = {}
    def show(payload: dict[str, Any]) -> None:
        if payload.get("reason") and protected.get("run"):
            payload = {**payload, "workflow": {**protected, "status": payload["status"]}}
        emit(payload, workspace, args.action, full=args.full or not compact)
    if args.replace_run and args.action != "start":
        parser.error("--replace-run applies only to flow start")
    if (args.assessment is not None or args.assessment_json is not None) and args.action != "auto-decide":
        parser.error("assessment options apply only to flow auto-decide")
    if args.action == "hook":
        return run_hook(governor=governor)
    observation_errors: list[str] = []
    try:
        workspace = Path(args.workspace).resolve()
        if args.action == "diagnose":
            diagnostics = workflow_local.diagnose(workspace)
            try:
                state = (governor or _controller(workspace, session_id({}), args.profile)).report()
                diagnostics["workflow"] = {k: state.get(k) for k in ("run", "revision", "phase", "status")}
            except (workflow.Refusal, OSError, ValueError, TypeError, KeyError) as exc:
                diagnostics["workflow_error"] = str(exc)
            show(diagnostics)
            return 0
        rows = read_events(workspace)
        session = session_id({})
        run = active_run(rows, session, counter({}, session).get("parent"))
        if args.action in {"report", "attach"}:
            run = next((r for r in reversed(rows) if r.get("kind") == "start"
                        and (r.get("run") == args.run if args.run else r.get("session") == session)), None)
        controller = governor or _controller(workspace, str(run["session"]) if run else session, args.profile)
        # A protected binding can outlive every workspace projection.
        protected = controller.report()
        if args.action == "context":
            from .context import encode
            context_result = controller.context(args.run, task=args.task, consume=args.consume,
                                        read=args.read, page=args.page if args.page is not None else 0, section=args.section,
                                        read_required=args.read_required)
            print(encode(context_result).decode("utf-8"))
            return 0
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
        harness = workflow_local.Harness(workspace, controller.root) if controller.adapter.profile == "native_workflow" else None
        if args.action in {"activate", "present", "wait"}:
            workflow.require(harness is not None, "unsupported_authority", "Harness observations do not admit a protected host owner.")
            assert harness is not None
            if args.action == "activate":
                harness.select(args.phase or "taskplane", args.request_reference, protected)
            elif args.action == "wait":
                harness.wait(protected, args.note)
            else:
                workflow.require(len(args.evidence) == 1, "invalid_evidence", "Supply exactly one native dashboard --evidence path.")
                harness.present(protected, args.evidence[0], args.presentation or "", args.note)
            show({"workflow": protected, "harness": harness.readiness(protected),
                  "guidance": harness.guidance(protected)})
            return 0
        before_measurement: dict[str, Any] | None = None
        measured_at = datetime.now(timezone.utc).isoformat()
        if run and args.action not in ("report", "start"):
            try:
                before_measurement = flow_usage.reconcile(run, rows)
            except (OSError, ValueError, TypeError, KeyError):
                observation_errors.append("Transition counters unavailable; phase allocation remains partial.")
        start_counter = counter({}, session) if args.action == "start" else {}
        if args.action == "start":
            from . import workflow_evidence
            scope = workflow_evidence.object_file(workspace, args.scope) if args.scope else None
            if harness and not protected.get("run"):
                selected_entry = harness.read().get("entry") if harness.read().get("selected") else None
                standalone_phase = {"tp-engineering": "engineering", "tp-northstar": "engineering",
                                    "tp-design": "design", "tp-product": "product",
                                    "engineering": "engineering", "design": "design", "product": "product"}.get(str(selected_entry))
                workflow.require(not standalone_phase or args.standalone and args.phase == standalone_phase,
                                 "scope_violation", "The selected standalone task requires --standalone --phase " + str(standalone_phase))
            state = controller.start({"entry": args.phase or "product", "standalone": args.standalone,
                                      "goal": args.goal, "native_reference": args.native_event,
                                      "scope": scope, "request_reference": args.request_reference, "tasks": args.tasks,
                                      "replace_run": args.replace_run, "expected_revision": args.expected_revision})
            if harness:
                if args.replace_run:
                    harness.select("tp-" + (args.phase or "product") if args.standalone else "tp-go",
                                   args.request_reference, state)
                harness.bind(state)
            if not any(r.get("kind") == "start" and r.get("run") == state["run"] for r in rows):
                graph = depgraph.scan(str(workspace), decompose=True)
                record_scan(workspace, graph)
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
                       "captured": capture_attachments(workspace, artifacts), "hook_setup": "host_adapter", **start_counter}
                append(workspace, run)
                initial = {"sessions": [{"session": state["root"], "role": "orchestrator", "native_usage": start_counter.get("usage"),
                    "status": "measured" if start_counter.get("usage_status") == "observed" else "unavailable"}]}
                try:
                    append(workspace, usage_point(state, initial, observed_at=measured_at))
                except (OSError, ValueError, primitives.StateError):
                    observation_errors.append("Run started; initial phase observation unavailable.")
        elif args.action in {"submit", "decide", "advance", "finish", "policy", "auto-decide"}:
            workflow.require(run, "state_unavailable", "No active workflow binding.")
            assert run is not None
            state = controller.apply(args.action, str(run["run"]),
                expected_revision=args.expected_revision, output=(args.assessment if args.action == "auto-decide" else args.output) or "",
                tasks=args.tasks or "", phase=args.phase, assessment_json=args.assessment_json,
                native_reference=args.policy_json if args.action == "policy" else args.decision_json if controller.adapter.profile == "native_workflow" else args.native_event)
            if harness and state.get("finished"):
                harness.update(selected=False, waiting=None)
            try:
                append(workspace, {"kind": args.action, "run": run["run"], "session": run["session"],
                                   "phase": workflow.current(state)["phase"], "note": args.note[:1000],
                                   "artifacts": artifacts, "captured": capture_attachments(workspace, artifacts)})
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
                               "artifacts": artifacts, "captured": capture_attachments(workspace, artifacts), **counter({}, session)})
        if before_measurement and run:
            try:
                after_state = controller.report(str(run["run"]))
                if after_state.get("visits"):
                    # Idempotent control retries must not add duplicate boundaries.
                    mutated = after_state["revision"] != protected.get("revision")
                    if mutated or args.action in ("progress", "attach"):
                        append(workspace, usage_point(after_state, before_measurement, observed_at=measured_at,
                               previous_revision=protected.get("revision")))
            except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
                observation_errors.append("Action committed; phase boundary observation unavailable.")
        result = report(workspace, str(run["run"]) if run else args.run, governor=controller)
        if result:
            result["evidence_errors"].extend(observation_errors)
        if result and run and args.action != "report":
            try:
                if result.get("sessions"):
                    append(workspace, {"kind": "usage", "run": run["run"], "session": run["session"],
                        "measurement": {k: result[k] for k in ("sessions", "tokens", "native_tokens", "token_coverage")}})
                result["dashboard"] = str(publish_dashboard(workspace, str(run["run"]), governor=controller,
                    supplied=result, select=args.action == "start"))
            except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
                result["evidence_errors"].append("Optional observations or dashboard refresh unavailable.")
        if result and harness and not result.get("historical"):
            result["harness"] = harness.readiness(controller.report())
        empty = {**controller.report(), "harness": harness.readiness(protected)} if harness else controller.availability()
        show(result if result else empty)
        return 0
    except workflow.Refusal as exc:
        show(exc.result())
        return 2
    except (OSError, ValueError, TypeError, KeyError, primitives.StateError) as exc:
        show({"status": "blocked", "reason": "state_unavailable",
              "detail": str(exc), "tokens": None})
        return 2


if __name__ == "__main__":
    raise SystemExit(main(compact=True))
