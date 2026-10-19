"""Shared observations plus guarded workflow entry points.

Mandatory transitions use the workflow controller. Live observations stay
advisory; journal records are explicit post-run analysis only.
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
import time
import uuid
from typing import Any, Callable, cast
from taskplane import workflow, workflow_host, workflow_local, depgraph, workspace_binding, runtime_command
from taskplane.context_delivery import observed
from taskplane import live_telemetry, run_journal

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


def _native_metadata(event: dict[str, Any], session: str) -> tuple[list[dict[str, Any]], bool]:
    """Read matching native identities without interpreting history as delegation."""
    if claude_session(event):
        return [], False  # Claude ancestry comes from parent fields and child-start events.
    path = event.get("transcript_path") or event.get("transcript")
    candidates = [Path(path)] if isinstance(path, str) else []
    if session != "local":
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        candidates.extend((home / "sessions").glob(f"**/*{session}*.jsonl"))
    records: list[dict[str, Any]] = []
    complete = True
    for candidate in dict.fromkeys(candidates):
        try:
            fd = os.open(candidate, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    complete = False
                    continue
                # Share the canonical metadata parser, but require no token counter.
                metadata, raw = native_session_meter._session_metadata(
                    stream.read(native_session_meter.MAX_METADATA_BYTES))
            if metadata["session_id"] == session:
                payload = json.loads(raw)["payload"]
                # A desktop/CLI chat fork copies history, not a worker grant.
                # Unknown or conflicting host shapes retain the conservative path.
                independent = (payload.get("thread_source") == "agent_forked_thread"
                               and payload.get("source") in ("vscode", "cli")
                               and bool(payload.get("forked_from_id"))
                               and not payload.get("parent_thread_id"))
                records.append({**metadata, "independent_fork": independent,
                                "cwd": payload.get("cwd"), "source_path": str(candidate)})
        except (OSError, ValueError):
            complete = False
    return records, complete


def observed_parent(event: dict[str, Any], session: str) -> str | None:
    """Native history lineage, which alone does not establish a worker role."""
    records, _ = _native_metadata(event, session)
    parents = {str(row["parent_session_id"]) for row in records if row.get("parent_session_id")}
    if parents or records:
        return next(iter(parents)) if len(parents) == 1 else None
    # Host lifecycle observations are operational identity, not telemetry or journal history.
    key = hashlib.sha256(session.encode()).hexdigest()[:32]
    candidate = Path(os.environ.get('TASKPLANE_WORKSPACE') or event.get('cwd') or Path.cwd())
    if not (candidate / '.taskplane' / ('native-parent-' + key + '.json')).exists():
        return None
    try:
        workspace = workspace_binding.resolve_workspace(None, event=event)
        target = storage.runtime_file(str(workspace), 'native-parent-' + key + '.json')
        binding = json.loads(target.read_text(encoding='utf-8'))
    except (FileNotFoundError, workspace_binding.MissingBinding):
        return None
    workflow.require(isinstance(binding, dict) and binding.get('schema') == 'taskplane.native-parent/v1'
                     and binding.get('child') == session and binding.get('workspace') == str(workspace)
                     and isinstance(binding.get('parent'), str),
                     'scope_violation', 'Native parent observation is invalid.')
    current = _controller(workspace, binding['parent'], event=event).report(diagnostics=False)
    return binding['parent'] if current.get('run') == binding.get('run') else None


def independent_fork(event: dict[str, Any], session: str) -> bool:
    records, complete = _native_metadata(event, session)
    return (complete and bool(records) and all(row["independent_fork"] for row in records)
            and len({row.get("parent_session_id") for row in records}) == 1)


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
    from .worker_runtime import SPAWN
    dispatches = sum(row.get("tool") in SPAWN for row in tools)
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
        "hook_outcomes": {"attempted": sum(r.get("event") == "PreToolUse" for r in unique),
                          **{outcome: sum(r.get("outcome") == outcome for r in unique)
                             for outcome in ("admitted", "denied", "error", "observed")},
                          "coverage": "approximate" if any(r.get("kind") == "hook" and not r.get("call_id") for r in unique) else "identified"},
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
                *, event: dict[str, Any] | None = None, principal: str | None = None) -> workflow_host.Controller:
    return workflow_host.Controller(workspace, root,
        workflow_host.installed_adapter("claude" if claude_session(event or {}) else "codex", profile), principal=principal)


@observed('controller-selection', stage='startup')
def select_controller(workspace: Path, actor: str, parent: str | None, *,
                      profile: str = "native_workflow", event: dict[str, Any] | None = None,
                      governor: workflow_host.Controller | None = None,
                      pending_start: bool = False,
                      diagnostics: bool = True) -> tuple[workflow_host.Controller, dict[str, Any]]:
    """Exact observed worker bindings outrank own runs; ordinary ancestry does not."""
    def selected_report(controller: workflow_host.Controller) -> dict[str, Any]:
        try:
            return controller.report(diagnostics=diagnostics)
        except workflow.Refusal:
            if (pending_start and controller.adapter.profile == 'native_workflow'
                    and controller.principal == controller.root
                    and cast(workflow_local.LocalWorkflow, controller.adapter).pending_initialization()):
                return {**controller.availability(), 'status': 'initialization_pending'}
            raise
    if governor is not None:
        return governor, selected_report(governor)
    own = _controller(workspace, actor, profile, event=event)
    own_state = selected_report(own)
    fork = independent_fork(event or {}, actor)
    inherited = _controller(workspace, parent, profile, event=event, principal=actor) if parent and parent != actor else None
    inherited_state = inherited.report(diagnostics=diagnostics and not fork) if inherited else {}
    from .worker_runtime import find
    if inherited is not None and find(inherited_state, actor) is not None:
        return inherited, inherited_state
    if own_state.get("run") or fork:
        return own, own_state
    if inherited is not None and inherited_state.get("run"):
        return inherited, inherited_state
    if inherited is not None:
        return inherited, inherited_state
    return own, own_state


def requested_report_controller(workspace: Path, actor: str, run: str, profile: str
                                ) -> tuple[workflow_host.Controller, dict[str, Any]] | None:
    """Locate an explicit native report in workflow stores, never the journal.

    This discovery is confined to a user-requested report. Candidate identities
    are hints only; the owning controller validates its complete store before use.
    """
    if profile != 'native_workflow':
        return None
    from . import workflow_retention
    for candidate in sorted((workspace / '.taskplane').glob('workflow-*.json')):
        if len(candidate.stem.removeprefix('workflow-')) != 32:
            continue
        try:
            target = storage.runtime_file(str(workspace), candidate.name)
            fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    continue
                raw = stream.read(workflow_local.MAX_BYTES + 1)
            if len(raw.encode()) > workflow_local.MAX_BYTES:
                continue
            hint = json.loads(raw)
            if (not isinstance(hint, dict) or not isinstance(hint.get('root'), str)
                    or run not in (hint.get('runs') or {}) and run not in (hint.get('archives') or {})):
                continue
            controller = _controller(workspace, hint['root'], profile, principal=actor)
            if controller._path() != target:
                continue
            with primitives.file_lock(str(target)):
                state = workflow_retention.read(workspace, controller._read(target), run)
            return controller, state
        except (OSError, ValueError, TypeError, workflow.Refusal):
            continue
    return None


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
        "graph_digest": primitives.content_fingerprint(graph), "status": "complete",
        "scanned_at": graph.get("meta", {}).get("scanned_at"),
        "source_revision": graph.get("meta", {}).get("scanned_head")})


def refresh_scan(workspace: Path) -> None:
    """Lifecycle-only derived refresh; failure never changes controller authority."""
    try:
        graph = depgraph.load(str(workspace))
        quality = depgraph.scan_quality(graph)
        if (quality.get('mode') == 'components' and not quality.get('degraded')
                and depgraph.source_inputs_current(str(workspace), graph)):
            record_scan(workspace, graph)
            return
        graph = depgraph.scan(str(workspace), decompose=True, strict=True)
        record_scan(workspace, graph)
    except (OSError, ValueError, TypeError, KeyError, primitives.StateError) as exc:
        try:
            primitives.atomic_json(storage.runtime_file(str(workspace), 'graph-receipt.json'), {
                'schema': 'taskplane.graph-receipt/v1', 'workspace': str(workspace),
                'status': 'failed', 'detail': str(exc)[:512],
                'attempted_at': datetime.now(timezone.utc).isoformat()})
        except (OSError, ValueError, primitives.StateError):
            pass


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


def phase_artifacts(workspace: Path, state: dict[str, Any], attachments: dict[str, Any]) -> list[dict[str, Any]]:
    """Explicit phase outputs and evidence, with snapshot-time availability."""
    rows: dict[str, dict[str, Any]] = {}
    for visit in state.get('visits', []):
        if visit.get('superseded'):
            continue
        packet = visit.get('packet') or {}
        paths = [packet.get('output_path'), packet.get('context', {}).get('tasks_path'),
                 *visit.get('artifact_paths', [])]
        paths.extend(row.get('path') for row in packet.get('output', {}).get('artifacts', [])
                     if isinstance(row, dict))
        for path in paths:
            if isinstance(path, str):
                rows[path] = {'path': path, 'phase': visit['phase'], 'visit': visit['id'],
                              'phase_decision': visit['decision']}
    for path in attachments.get('evidence', []):
        rows.setdefault(path, {'path': path, 'phase': 'attached'})
    for path, row in rows.items():
        try:
            from .workflow_evidence import path as evidence_path
            source = evidence_path(workspace, path)
            row['status'] = 'available' if source.is_file() else 'missing'
            if row['status'] == 'available':
                row['href'] = source.as_uri()
        except (OSError, ValueError, workflow.Refusal):
            row['status'] = 'unavailable'
    return list(rows.values())


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
    if not historical and receipt.get('status') == 'failed':
        status = 'failed'
    return {"data": graph, "status": status, "workspace": str(workspace.resolve()),
            "receipt": receipt if bound else None, "source_inputs_current": current,
            "quality": quality, "fingerprint": primitives.content_fingerprint(graph),
            "detail": receipt.get('detail', 'Scan failed; retry the supported scan.') if status == 'failed' else
                      "Historical sealed graph" if historical else "Workspace/input identity verified" if status == "current" else
                      "Missing/foreign scan receipt, changed inputs or degraded scan; run graph scan --decompose --strict."}


def graph_view(workspace: Path, state: dict[str, Any], historical: bool) -> dict[str, Any]:
    try:
        return _graph_view(workspace, state, historical)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"data": {}, "status": "unavailable", "workspace": str(workspace.resolve()),
                "receipt": None, "source_inputs_current": False, "quality": {"degraded": True},
                "fingerprint": None, "detail": "Optional graph observation failed; regenerate after repairing scan access."}


def reconcile_usage(run: dict[str, Any], rows: list[dict[str, Any]], state: dict[str, Any],
                    expected: list[str] | tuple[str, ...] = (), *,
                    closing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keep the same participant coverage in live and committed observations."""
    workers = ({r['worker_id'] for r in state.get('workers', {}).values() if r.get('worker_id')}
               | set(run.get('native_children', {})))
    declared = set(expected) | workers
    for row in rows:
        if row.get('run') != run['run']:
            continue
        if row.get('child'):
            declared.add(str(row['child']))
        reviews = (row.get('captured') or {}).get('reviews', [])
        groups = reviews.values() if isinstance(reviews, dict) else [reviews]
        for group in groups:
            if isinstance(group, list):
                declared.update(str(review['agent']) for review in group
                                if isinstance(review, dict) and review.get('agent'))
    return flow_usage.reconcile({**run, 'worker_sessions': sorted(workers)}, rows,
                                sorted(declared), closing=closing)


def report(workspace: Path, run_id: str | None = None, *,
           governor: workflow_host.Controller | None = None,
           history: bool = False) -> dict[str, Any] | None:
    if history:
        return _report(workspace, run_id, governor=governor, history=True)
    controller = governor or _controller(workspace, session_id({}))
    # Authority reads must fail normally; only the derived report is optional.
    governed = controller.report(run_id)
    if not governed.get('visits'):
        return None
    active = controller.report() if run_id else governed
    captured_at = datetime.now(timezone.utc).isoformat()
    try:
        return _report(workspace, run_id, governor=controller,
                       governed=governed, active=active, captured_at=captured_at)
    except Exception:
        # Build a standalone view without retrying failed optional helpers or
        # exposing a partially enriched result as a successful measurement.
        stage = workflow.current(governed)
        historical = bool(governed.get('finished') or active.get('run') != governed['run'])
        return {'mode': 'advisory', 'owner': 'orchestrator', 'run': governed['run'],
                'goal': governed.get('goal'), 'phase': governed['phase'], 'status': governed['status'],
                'started_at': governed.get('started_at'), 'finished_at': governed.get('finished_at'),
                'workflow': governed, 'historical': historical, 'dashboard': str(workspace.resolve() / DASHBOARD),
                'milestones': [], 'tasks': [], 'reviews': [], 'artifacts': {}, 'graph': {},
                'sessions': [], 'tokens': None, 'observed_tokens': None,
                'token_coverage': {'measured_sessions': 0, 'unmeasured_sessions': 1,
                                   'basis': 'Advisory report unavailable'},
                'usage_measurement': {'status': 'unavailable', 'attempted_at': captured_at,
                                      'measured_at': None, 'oldest_at': None, 'newest_at': None},
                'evidence_errors': ['Advisory report unavailable; controller state remains authoritative.'],
                'snapshot': {'schema': 'taskplane.dashboard-snapshot/v1', 'workspace': str(workspace),
                    'root': governed['root'], 'run': governed['run'], 'phase': governed['phase'],
                    'visit': stage['id'], 'revision': governed['revision'], 'historical': historical,
                    'captured_at': captured_at, 'generated_at': captured_at,
                    'measurement_status': 'unavailable', 'measurement_at': None}}


def _report(workspace: Path, run_id: str | None = None, *,
            governor: workflow_host.Controller | None = None, history: bool = False,
            governed: dict[str, Any] | None = None, active: dict[str, Any] | None = None,
            captured_at: str | None = None) -> dict[str, Any] | None:
    """Project optional observations from already-read normal workflow state."""
    captured_at = captured_at or datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    aggregate = None
    telemetry_error = None
    if not history:
        assert governor is not None and governed is not None and active is not None
        controller = governor
        try:
            aggregate = live_telemetry.read(workspace, governed)
        except Exception:
            aggregate = live_telemetry.empty(governed)
            telemetry_error = 'Advisory telemetry unavailable; controller state remains authoritative.'
        run = aggregate['metadata']
        if aggregate.get('measurement'):
            rows = [{'kind': 'usage', 'run': governed['run'], 'session': governed['root'],
                     'measurement': aggregate['measurement']}]
    else:
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
    result = live_telemetry.summary(aggregate, governed) if aggregate is not None else summarize(rows, run)
    result["dashboard"] = str(workspace.resolve() / DASHBOARD)
    attachments: dict[str, Any] = deepcopy(aggregate["artifacts"]) if aggregate is not None else {}
    captured: dict[str, Any] = deepcopy(aggregate["captured"]) if aggregate is not None else {}
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
    result["evidence_errors"] = [telemetry_error] if telemetry_error else []
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
    closing = run.get('closing_counter') if aggregate is not None else None
    if aggregate is not None and not closing:
        closed_at = live_telemetry.closing_time(governed)
        if closed_at:
            closing = {'session': governed['root'], 'host': run.get('host', 'codex'),
                       'at': closed_at, 'usage': None, 'usage_status': 'unavailable'}
    try:
        result.update(reconcile_usage(run, rows, governed, expected, closing=closing))
    except Exception:
        result["evidence_errors"].append("Native session reconciliation unavailable; showing hook observations")
    measured_at = datetime.now(timezone.utc).isoformat()
    for measured in result.get("sessions", []):
        if (str(measured.get("status", "")).startswith("recorded")
                or measured.get("measurement_source") in {"run_boundary", "native_sample"}):
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
    if aggregate is not None:
        try:
            live_telemetry.remember_measurement(workspace, governed, result, captured_at)
        except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
            result['evidence_errors'].append('Advisory measurement could not be retained.')
    result["evidence_previews"] = captured.get("evidence", {})
    result["review_coverage"] = governed.get("worker_status", {}).get("lens_coverage", [])
    for review in result["reviews"]:
        review["coverage_status"] = "legacy_unverified"
    result["workflow"] = governed
    result["post_completion_observations"] = [r for r in rows if r.get("run") == run["run"]
        and r.get("kind") == "hook" and (r.get("admission") or {}).get("authority") == "observation_only"][-32:]
    result["final_snapshots"] = [r for r in rows if r.get("run") == run["run"] and r.get("kind") == "final_snapshot"]
    result["observation_status"] = result["status"]
    if governed.get("visits"):
        result["phase"] = governed["phase"]
        result["status"] = governed["status"]
        active = active if active is not None else controller.report()
        historical = bool(governed.get("finished") or active.get("run") != governed["run"])
        if historical and "reviews" not in captured:
            result["reviews"] = []
            if attachments.get("reviews"):
                result["evidence_errors"].append("Historical reviewer context was not captured; mutable file not reused.")
        stage = workflow.current(governed)
        from . import workflow_evidence
        observations = {t['id']: t for t in result['tasks'] if isinstance(t, dict) and 'id' in t}
        result['tasks'] = deepcopy(workflow_evidence.context_tasks(governed))
        if stage.get("packet"):
            result["tasks"] = deepcopy(stage["packet"]["context"]["tasks"])
        for task in result["tasks"]:
            observation = observations.get(task['id'])
            if (observation and workflow_evidence.task_definitions([observation])
                    == workflow_evidence.task_definitions([task])):
                task.update({key: deepcopy(value) for key, value in observation.items()
                             if key in workflow_evidence.TASK_OBSERVATIONS})
            candidates = [v for v in governed["visits"] if v["phase"] == task.get("phase") and not v.get("superseded")]
            if candidates:
                visit = candidates[-1]
                task["phase_decision"] = visit["decision"]
                task.setdefault('status', 'unverified')
        result["phase_usage"] = phase_usage(rows, governed, result)
        result["historical"] = historical
        result['artifact_links'] = phase_artifacts(workspace, governed, attachments)
        result["graph"] = graph_view(workspace, governed, historical)
        result["snapshot"] = {"schema": "taskplane.dashboard-snapshot/v1", "workspace": str(workspace),
            "root": governed["root"], "run": governed["run"], "phase": governed["phase"], "visit": stage["id"],
            "revision": governed["revision"], "policy_revision": governed.get("approval_policy", {}).get("revision"),
            "source_revision": depgraph._git_head(str(workspace)),
            "graph_fingerprint": result["graph"]["fingerprint"], "historical": historical,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "captured_at": captured_at,
            "observation_count": aggregate["observation_count"] if aggregate is not None else sum(r.get("run") == governed["run"] for r in rows),
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
                      supplied: dict[str, Any] | None = None, detached: bool = False,
                      pin_reason: str | None = None, checkpoint_required: bool = False) -> Path:
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
        if governed.get("visits") and not model.get("final_observation"):
            after = controller.report(model["run"])
            if (after.get("revision"), after.get("status"), after.get("invalidation_pending")) != (
                    governed.get("revision"), governed.get("status"), governed.get("invalidation_pending")):
                continue
            if model.get("graph", {}).get("status") == "current" and not depgraph.source_inputs_current(str(workspace), model["graph"]["data"]):
                continue
        from .snapshot_retention import publish_pair
        publication_pin = pin_reason
        # Explicit checkpoint delivery is required evidence. Derive the pin
        # after each retry's freshness checks, never from a caller's checkpoint.
        if (checkpoint_required and not publication_pin and governed.get("visits")
                and not model.get("historical") and not model.get("final_observation")
                and not any(governed.get(key) for key in
                            ("finished", "retired", "superseded_by", "cancellation"))):
            stage = workflow.current(governed)
            checkpoint = (stage.get("packet") or {}).get("checkpoint")
            if checkpoint and stage.get("decision") in {"awaiting_human_approval", "approved"}:
                publication_pin = "checkpoint/" + checkpoint
        return publish_pair(workspace, model, document, target=target, select=select,
                            detached=detached, older=older_snapshot, pin_reason=publication_pin)
    raise workflow.Refusal("stale_checkpoint", "Dashboard inputs changed during publication; regenerate the selected run.")


def _observe_hook(event: dict[str, Any], *, outcome: str = "observed", reason: str | None = None) -> dict[str, Any]:
    try:
        workspace = workspace_binding.resolve_workspace(event.get('taskplane_execution_workspace'), event=event)
        workspace_binding.ensure(workspace)
    except workflow.Refusal:
        return {}  # A denied selection must not create a competing journal or session export.
    claude_flow_usage.bind_session(event)
    state = event.get('_telemetry_state')
    if not isinstance(state, dict) or not state.get('run'):
        return {}
    session = session_id(event)
    try:
        observation = counter(event, session)
    except (OSError, ValueError, TypeError, KeyError):
        observation = {'usage': None, 'usage_status': 'unavailable'}
    name = str(event.get("hook_event_name") or "unknown")
    tool = str(event.get("tool_name") or event.get("tool") or "")[:120]
    action = hashlib.sha256(json.dumps(
        [tool, event.get("tool_input")], sort_keys=True).encode()).hexdigest()
    row = {'session': session, 'event': name, 'tool': tool, 'action': action,
           'call_id': str(event.get('tool_use_id') or event.get('call_id') or '')[:512] or None,
           'outcome': outcome, **observation}
    if name in {'SubagentStart', 'SubagentStop'}:
        row.update(child=event.get('agent_id') or event.get('subagent_id'),
                   agent_transcript_path=event.get('agent_transcript_path'))
    advice = live_telemetry.observe(workspace, state, row)
    if advice and name in {"PreToolUse", "SessionStart"}:
        return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                "Taskplane advisory (continue delivery): " + " ".join(advice)}}
    # Abstain: no permissionDecision=allow and no decision=block. The host
    # still applies its own permissions, approvals and sandbox.
    return {}


def _workspace_admin(event: dict[str, Any]) -> bool:
    """Only the installed bounded administration CLI can precede binding checks."""
    if (event.get("tool_name") or event.get("tool")) not in {"Bash", "exec_command"}:
        return False
    if event.get("parent_session_id") or event.get("agent_id") or event.get("subagent_id"):
        return False
    words = workflow_local.runtime_words(event)
    cwd = runtime_command.execution_directory(event, Path.cwd())
    if (len(words) < 4 or not runtime_command.installed(words, cwd, absolute=True)
            or words[2] != 'workspace' or words[3] not in {'inspect', 'bind', 'recover'}):
        return False
    options = words[4:]
    source = '--request-json' if '--request-json' in options[::2] else '--request'
    required = {'--workspace'} | ({source} if words[3] != 'inspect' else set())
    return (len(options) == 2 * len(required) and set(options[::2]) == required
            and all(value and not value.startswith('--') for value in options[1::2]))


def _onboarding_read(event: dict[str, Any]) -> bool:
    """Root setup discovery only; no workflow or general shell mutation exception."""
    if event.get('parent_session_id') or event.get('agent_id') or event.get('subagent_id'):
        return False
    tool = event.get('tool_name') or event.get('tool')
    if tool in workflow_local.READ_TOOLS | workflow_local.QUESTION_TOOLS:
        return True
    if tool == 'Skill':
        args = event.get('tool_input', {})
        return isinstance(args, dict) and args.get('skill') in {
            'taskplane', *('taskplane:' + name for name in (*workflow_local.EXECUTION_ENTRIES, 'tp-status', 'tp-help'))}
    if tool not in {'Bash', 'exec_command'}:
        return False
    if workflow_local.readonly_command(event):
        return True
    words = workflow_local.runtime_words(event)
    cwd = runtime_command.execution_directory(event, Path.cwd())
    if (runtime_command.installed(words, cwd, absolute=True)
            and words[2:] in (['version', '--verify'], ['help', '--md'])):
        return True
    if (runtime_command.installed(words, cwd, absolute=True)
            and words[2:4] == ['flow', 'activate']
            and len(words) == 6 and words[4] == '--phase'
            and words[5] in workflow_local.EXECUTION_ENTRIES):
        return True  # Selection can explain missing binding without creating workflow state.
    # Compute the execution-side probe without admitting Python or shell scripts.
    # Host-side evidence must still be obtained independently by the caller.
    if ((words[:4] == ['shasum', '-a', '256', '--'] and len(words) == 5)
            or (words[:2] == ['sha256sum', '--'] and len(words) == 3)):
        return bool(words[-1] and words[-1] != '-')
    if (len(words) < 4 or not runtime_command.installed(words, cwd, absolute=True)
            or words[2:4] != ['flow', 'report']):
        return False
    options = words[4:]
    if options.count('--full') > 1:
        return False
    options = [value for value in options if value != '--full']
    return (len(options) % 2 == 0 and len(set(options[::2])) == len(options[::2])
            and set(options[::2]) <= {'--workspace', '--run'}
            and all(value and not value.startswith('--') for value in options[1::2]))


def _state_admin(event: dict[str, Any], workspace: Path) -> bool:
    """Keep exact diagnosis/recovery reachable when the database itself is unavailable."""
    if ((event.get("tool_name") or event.get("tool")) not in {"Bash", "exec_command"}
            or event.get("parent_session_id") or event.get("agent_id") or event.get("subagent_id")):
        return False
    words = workflow_local.runtime_words(event)
    cwd = runtime_command.execution_directory(event, workspace)
    if (len(words) < 4 or not runtime_command.installed(words, cwd, absolute=True)
            or words[2] != 'flow' or words[3] not in {'diagnose', 'recover'}):
        return False
    options = words[4:]
    if options.count('--full') > 1:
        return False
    options = [value for value in options if value != '--full']
    if words[3] == 'diagnose' and not options:
        try:
            return runtime_command.resolve_selection(str(cwd), event, workspace) == workspace
        except (workflow.Refusal, OSError, ValueError):
            return False
    required = {'--workspace'}
    if words[3] == 'recover':
        required |= ({'--recovery-request', '--expected-sha256', '--recovery-mode'}
                     if '--recovery-request' in options else
                     {'--recover-from', '--expected-sha256', '--run', '--expected-revision', '--request-reference'})
    if len(options) != len(required) * 2 or set(options[::2]) != required:
        return False
    values = dict(zip(options[::2], options[1::2]))
    if any(not value or value.startswith('--') for value in values.values()):
        return False
    try:
        return runtime_command.resolve_selection(values['--workspace'], event, workspace) == workspace
    except (workflow.Refusal, OSError, ValueError):
        return False


def _hook(event: dict[str, Any], *,
         governor: workflow_host.Controller | None = None) -> dict[str, Any]:
    event.pop("taskplane_admission", None)
    event.pop('taskplane_execution_workspace', None)
    # Legacy measurement is reconstructed only for existing legacy accounts.
    event.pop('taskplane_startup_entry', None)
    session = session_id(event)
    parent = event.get("parent_session_id")
    fork = governor is None and independent_fork(event, session)
    if governor is None:
        parent = observed_parent(event, session) or parent
    if parent and not fork:
        event['parent_session_id'] = parent
    elif fork:
        event.pop('parent_session_id', None)
    words = workflow_local.runtime_words(event)
    cwd = runtime_command.execution_directory(event, Path.cwd())
    if event.get('hook_event_name') == 'PreToolUse':
        try:
            runtime_command.validate_workdir(words, event, Path.cwd())
        except ValueError as exc:
            raise workflow.Refusal('scope_violation', str(exc)) from None
        mismatch = runtime_command.collision(words, cwd)
        workflow.require(not mismatch, 'scope_violation', mismatch or '')
        if len(words) >= 3 and Path(words[1]).name == 'tp.py' and words[2] == 'workflow':
            workflow.require(runtime_command.installed(words, cwd), 'scope_violation',
                             'Use the exact loaded workflow runtime and interpreter.')
        if runtime_command.installed(words, cwd):
            try:
                runtime_command.workspace_selector(words[2:])
            except ValueError as exc:
                raise workflow.Refusal('scope_violation', str(exc)) from None
    if governor is None and _workspace_admin(event):
        return {}  # The administration command still validates proof and active-state recovery.
    if (governor is None and event.get('hook_event_name') in {'PreToolUse', 'PostToolUse'}
            and not any(event.get(k) for k in ('parent_session_id', 'agent_id', 'subagent_id'))
            and runtime_command.installed(words, cwd, absolute=True)
            and words[2:] in (['version', '--verify'], ['help', '--md'])):
        return {'hookSpecificOutput': {'hookEventName': event['hook_event_name'],
                'additionalContext': 'Read-only runtime diagnostics do not create or validate workspace binding.'}}
    escape = runtime_command.reserved_directory_escape(words, event)
    try:
        workspace = (workspace_binding.resolve_workspace(escape, event=event) if escape is not None
                     else runtime_command.hook_workspace(event, Path.cwd()))
        contract = workspace_binding.ensure(workspace)
    except workflow.Refusal as exc:
        incidental = Path(event.get("cwd") or os.getcwd())
        # Installing the plugin must not capture unrelated, unselected Cowork tasks.
        words = workflow_local.runtime_words(event)
        invokes_runtime = len(words) > 2 and Path(words[1]).name == 'tp.py'
        if (not os.environ.get('TASKPLANE_WORKSPACE') and not workflow_local.execution_entry(event)
                and not invokes_runtime and '.taskplane' not in incidental.parts
                and '.taskplane' not in incidental.resolve().parts and not (incidental / '.taskplane').exists()
                and not runtime_command.mutation_targets(event)
                and runtime_command.execution_directory(event, Path.cwd()) == incidental.resolve()):
            return {}
        if isinstance(exc, workspace_binding.MissingBinding) and governor is None:
            name = event.get('hook_event_name')
            root = not any(event.get(k) for k in ('parent_session_id', 'agent_id', 'subagent_id'))
            if root and (name == 'UserPromptSubmit' or name == 'SessionStart'
                         or name in {'PreToolUse', 'PostToolUse'} and _onboarding_read(event)):
                return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': exc.guidance()}}
        raise
    try:
        controller, guarded = select_controller(workspace, session, parent, event=event,
                                                governor=governor, diagnostics=False, pending_start=(
                                                    (runtime_command.installed(words, cwd) and words[2:4] == ["flow", "start"])
                                                    or (event.get("tool_name") or event.get("tool")) == "write_stdin"))
    except (workflow.Refusal, OSError, ValueError, TypeError, KeyError, primitives.StateError) as exc:
        if ((not isinstance(exc, workflow.Refusal) or exc.reason == 'state_unavailable') and _state_admin(event, workspace)
                and (governor is None or governor.adapter.profile == 'native_workflow'
                     and governor.principal == governor.root)):
            return {}  # The exact CLI still validates recovery identity/hash; no phase action is admitted.
        raise
    name = event.get("hook_event_name")
    if not guarded.get('run') and name == 'PreToolUse':
        from .workflow_collision import sealed_paths
        protected = sealed_paths(workspace, {'root': session})
        for value in runtime_command.mutation_targets(event):
            absolute = Path(value) if Path(value).is_absolute() else cwd / value
            relative = workspace_binding.relative_path(workspace, str(absolute))
            workflow.require(relative not in protected, 'scope_violation',
                             'Another active run owns this working or sealed path; preserve its work.')
    event['taskplane_execution_workspace'] = str(workspace)
    if escape is not None:
        workflow.require(controller.adapter.profile == 'native_workflow'
                         and controller.principal == controller.root and not parent
                         and workspace == escape,
                         'scope_violation', 'Only the root may return from its store to the containing project.')
        return {'hookSpecificOutput': {'hookEventName': name,
                'additionalContext': 'Return to the containing project; workspace and workflow state are unchanged.'}}
    # Host hooks need not carry a host field. Use the adapter actually selected
    # for this event, including root Bash hooks, before observing provenance.
    event["host"] = controller.adapter.name
    event["taskplane_observed_binding"] = {"root": controller.root, "principal": controller.principal,
                                           "profile": controller.adapter.profile}
    if (name == 'SubagentStart' and guarded.get('run')
            and controller.principal == controller.root and isinstance(event.get('agent_id'), str)
            and 0 < len(event['agent_id']) <= 200 and event['agent_id'] != controller.root):
        child = event['agent_id']
        key = hashlib.sha256(child.encode()).hexdigest()[:32]
        primitives.atomic_json(storage.runtime_file(str(workspace), 'native-parent-' + key + '.json'),
            {'schema': 'taskplane.native-parent/v1', 'workspace': str(workspace),
             'parent': controller.root, 'child': child, 'run': guarded['run'],
             'source': 'observed SubagentStart', 'observed_at': datetime.now(timezone.utc).isoformat()})
    if guarded.get('run'):
        event['_telemetry_state'] = live_telemetry.binding(guarded)
    event["taskplane_workspace_contract"] = ({k: contract[k] for k in ("project_id", "digest")}
                                             if contract else None)
    # Overwrite caller data with the package actually executing this automatic hook.
    from .host_capabilities import runtime_identity
    event["taskplane_runtime_identity"] = runtime_identity()
    event["taskplane_automatic_hook"] = True
    if name == "PostToolUse":
        pair = controller.complete_admission(event)
        if pair:
            event["taskplane_admission"] = pair
            prior_state = controller.report(pair['run'], diagnostics=False)
            event['_telemetry_state'] = live_telemetry.binding(prior_state)
    harness = workflow_local.Harness(workspace, controller.root) if controller.adapter.profile == "native_workflow" and controller.principal == controller.root else None
    selected = workflow_local.execution_entry(event)
    startup_observed = False
    if harness:
        harness.update(hook_observed=True)
        if name == "PostToolUse":
            harness.observe_recovery(event, guarded)
            if not guarded.get("run"):
                observed_run = controller.observe(event, None)
                startup_observed = True
                if observed_run:
                    guarded = controller.report(observed_run, diagnostics=False)
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
            prior = controller.report(harness.read()["run"], diagnostics=False)
            if prior.get("finished"):
                harness.update(selected=False, waiting=None)
        if (guarded.get('status') == 'initialization_pending' and name == 'PreToolUse'
                and (event.get('tool_name') or event.get('tool')) == 'write_stdin'):
            harness.guard_setup_input(event)
        if name == "PreToolUse" and harness.read().get("selected"):
            if not guarded.get("run"):
                harness.guard_bootstrap(event, guarded)
            if (event.get("tool_name") or event.get("tool")) in workflow_local.QUESTION_TOOLS:
                harness.wait(guarded, "Native question requested; awaiting the user's response.")
    # Protected state is checked independently of the deletable workspace journal.
    run = guarded.get("run")
    if guarded.get("workflow_available") and guarded.get("run"):
        if not startup_observed:
            controller.observe(event, str(run))
        guarded = controller.report(str(run), diagnostics=False)
        if name == "Stop" and not controller.can_seal(str(run)):
            return {"systemMessage": "Taskplane is waiting for process quiescence before sealing. No phase has advanced; unknown host coverage remains explicit."}
    guarded_run = guarded.get("run") or (run if controller.adapter.profile == "protected_host" else None)
    if guarded_run and name == "PreToolUse":
        if "workflow_binding" in guarded.get("scope", {}):
            if (controller.principal != controller.root or not
                    workflow_local.workflow_diagnostic_event(workspace, event, guarded)):
                workflow_local.verify_workflow(workspace, guarded)
        controller.guard(event, str(run))
    elif name == "PreToolUse" and harness and harness.read().get("run"):
        previous = controller.report(harness.read()["run"], diagnostics=False)
        if previous.get("finished") or previous.get("retired"):
            controller.followup_admission(event, previous["run"])
    if name == "Stop" and harness:
        stopped = harness.stop(event, guarded)
        if stopped:
            return stopped
    if run and name == "UserPromptSubmit" and controller.principal == controller.root:
        if not guarded.get("workflow_available") or not guarded.get("run"):
            return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                    "Taskplane approval remains unverified; this prompt does not authorize a workflow transition."}}
        policy_event = event.get("taskplane_policy")
        if isinstance(policy_event, dict):
            controller.apply("policy", str(run), expected_revision=guarded["revision"],
                             native_reference=json.dumps(policy_event))
            guarded = controller.report(str(run), diagnostics=False)
        if (guarded.get("status") == "awaiting_human_approval"
                or guarded.get("status") in {"approved", "cancelled"}
                and isinstance(event.get("taskplane_decision"), dict)) and not policy_event:
            reference = controller.adapter.prompt_reference(event, guarded)
            if reference:
                controller.apply("decide", str(run), expected_revision=guarded["revision"], native_reference=reference)
                guarded = controller.report(str(run), diagnostics=False)
            else:
                return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                        ('Taskplane native approval capture: ' + guarded.get('native_approval_capture', {}).get('reason',
                         'Waiting for the actual human event and original checkpoint presentation.')
                         + ' The next automatic root hook will recheck native delivery. Do not ask the user to extract message IDs or repeat an exact phrase.'
                         if controller.adapter.name == 'claude' else
                         "Taskplane: record the actual human response with its presented checkpoint and conversation provenance. This prompt alone has not advanced the workflow.")}}
    result: dict[str, Any] = {}
    if name == "PreToolUse" and event.get("taskplane_updated_input") is not None:
        result["hookSpecificOutput"] = {"hookEventName": name, "updatedInput": event["taskplane_updated_input"]}
    if harness and harness.read().get("selected") and (selected or name in {"SessionStart", "UserPromptSubmit"}):
        output = result.setdefault("hookSpecificOutput", {"hookEventName": name})
        output["additionalContext"] = harness.guidance(guarded) + " " + output.get("additionalContext", "")
    return result


def hook(event: dict[str, Any], *, governor: workflow_host.Controller | None = None,
         completion: dict[str, Any] | None = None) -> dict[str, Any]:
    """Observe all exits, including denials. Advisory telemetry never grants access."""
    event.pop('_telemetry_state', None)
    event.pop('taskplane_observed_binding', None)
    event.pop('taskplane_admission', None)
    event.pop('taskplane_updated_input', None)
    event.pop('taskplane_claude_root_source', None)
    event.pop('_startup_completion_controller', None)
    outcome, reason = "observed", None
    result: dict[str, Any] = {}
    try:
        # Desktop child tool hooks use session_id for the parent and agent_id
        # for the actor. Lifecycle hooks instead name the child being observed.
        # Resolve the actor before root exemptions; never guess by recent work.
        native_session = os.environ.get("CODEX_THREAD_ID")
        reported = session_id(event)
        if (governor is None and claude_session(event)
                and event.get("hook_event_name") not in {"SubagentStart", "SubagentStop"}
                and event.get("agent_id")):
            actor = event["agent_id"]
            workflow.require(isinstance(actor, str) and 0 < len(actor) <= 200 and actor.strip() == actor,
                             "scope_violation", "Invalid Claude child actor identity.")
            parent = event.get("parent_session_id") or event.get("session_id")
            workflow.require(isinstance(parent, str) and parent and parent != actor,
                             "scope_violation", "Claude child actor lacks its exact parent session.")
            assert isinstance(parent, str)
            workspace = workspace_binding.resolve_workspace(None, event=event)
            parent_controller = _controller(workspace, parent, event=event)
            # A globally installed plugin must not capture ordinary Claude
            # children before this parent has selected or initialized Taskplane.
            # Existing or corrupt state still goes through exact actor checks.
            if (parent_controller.adapter.profile == 'native_workflow'
                    and not cast(workflow_local.LocalWorkflow, parent_controller.adapter).state_exists()
                    and not workflow_local.Harness(workspace, parent).read().get('selected')):
                return {}
            parent_controller.claude_actor(actor, event)
            event = {**event, "host": "claude", "host_reported_session": reported, "thread_id": actor,
                     "parent_session_id": parent, "identity_basis": "structured Claude launch proof"}
        if (governor is None and not claude_session(event)
                and event.get("hook_event_name") not in {"SubagentStart", "SubagentStop"}):
            actor = event.get("agent_id")
            if actor is not None and actor != "":
                workflow.require(isinstance(actor, str) and 0 < len(actor) <= 200 and actor.strip() == actor,
                                 "scope_violation", "Invalid native child actor identity.")
                assert isinstance(actor, str)
                parent = observed_parent(event, actor)
                workflow.require(parent and reported in {actor, parent}
                                 and event.get("parent_session_id") in {None, parent}
                                 and native_session in {None, reported, actor},
                                 "scope_violation", "Native child actor needs unambiguous matching lineage.")
                event = {**event, "host_reported_session": reported, "thread_id": actor,
                         "parent_session_id": parent, "identity_basis": "host agent_id and matching native lineage"}
            elif (native_session and native_session != reported
                    and observed_parent({}, native_session) == reported):
                event = {**event, "host_reported_session": reported, "thread_id": native_session,
                         "parent_session_id": reported, "identity_basis": "native environment and matching session lineage"}
        result = _hook(event, governor=governor)
        if event.get("hook_event_name") == "PreToolUse":
            outcome = "admitted"
    except workflow.Refusal as exc:
        outcome, reason = "denied", exc.reason
        raise
    except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
        outcome, reason = "error", "invalid_input_or_state"
        raise
    finally:
        try:
            advice = _observe_hook(event, outcome=outcome, reason=reason)
            if advice and outcome != "denied":
                output = result.setdefault("hookSpecificOutput", {"hookEventName": event.get("hook_event_name")})
                output["additionalContext"] = (output.get("additionalContext", "") + " " +
                    advice.get("hookSpecificOutput", {}).get("additionalContext", "")).strip()
        except Exception:
            # Only optional observation is inside this boundary. Gate exceptions
            # above still propagate with their original decision and reason.
            pass
    return result


def run_hook(command: str | None = None, *,
             governor: workflow_host.Controller | None = None) -> int:
    event: dict[str, Any] = {}
    dispatched = False
    completion: dict[str, Any] = {}
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
        dispatched = True
        result = hook(event, governor=governor, completion=completion)
    except (workflow.Refusal, OSError, ValueError, TypeError, KeyError, primitives.StateError) as exc:
        if not dispatched:
            try:
                _observe_hook(event, outcome="error", reason="malformed_hook_input")
            except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
                pass  # Recording failure never replaces the original denial.
        reason = (exc.guidance() if isinstance(exc, workspace_binding.MissingBinding) else
                  exc.detail if isinstance(exc, workflow.Refusal) else "Taskplane hook input/state is invalid.")
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
    print(json.dumps(result), flush=True)
    return 0


def emit(payload: dict[str, Any], workspace: Path, action: str, *, full: bool = False) -> None:
    """Bound shipped CLI transport while preserving full Python reports and hook envelopes."""
    from .context import Store, encode
    from .context_handoff import Brief
    from .context_views import summary
    if full or action in {'diagnose', 'recover', 'resume'}:
        print(json.dumps(payload, indent=2))
        return
    state = payload.get("workflow", payload)
    context: dict[str, Any] = {"status": "not_applicable"}
    try:
        if action == "context" and payload.get("reason"):
            context = {"status": "blocked", "next_action": payload.get("next_action")}
        elif state.get("visits") and not state.get("finished") and not payload.get("historical"):
            context = Brief(workspace, state).descriptor()
    except (workflow.Refusal, OSError, ValueError, TypeError, KeyError, primitives.StateError) as exc:
        context = {"status": "unavailable", "reason": str(exc)[:512],
                   "next_action": "Repair context storage and retry flow context; do not replay a committed action."}
    try:
        result = summary(Store(workspace), payload, action, context)
        if payload.get("next_action"):
            result["next_action"] = payload["next_action"]
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
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=["start", "progress", "finish", "report", "attach",
                                           "submit", "decide", "advance", "policy", "auto-decide", "hook",
                                           "activate", "deactivate", "present", "wait", "diagnose", "recover", "repair", "resume", "inspect", "prevalidate", "context", "revise", "retire", "worker"])
    parser.add_argument('--revision-mode', choices=['preview', 'begin', 'resume', 'abort'])
    parser.add_argument('--revision-request', help='Exact source-pinned requirements revision request')
    parser.add_argument('--revision-sha256')
    parser.add_argument('--revision-note', help='Actual abort reason')
    parser.add_argument('--preview-digest')
    parser.add_argument("--resume-mode", choices=["inspect", "verify"], help="Read-only native same-session continuation step")
    parser.add_argument("--resume-json", help="Observed human resume request with exact source run binding")
    parser.add_argument("--kind", choices=["contract", "result"])
    parser.add_argument("--reference")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=32768)
    parser.add_argument("--update-context", action="store_true", help="Publish run-bound task definitions; attach only")
    parser.add_argument("--operation", choices=["prepare", "claim", "accept-result", "reject-result", "status", "abandon", "capacity", "recover-unavailable", "recover-unbound", "recover-launch-denied", "reconcile-handback"])
    parser.add_argument("--invocation-ref", help=argparse.SUPPRESS)
    parser.add_argument("--grant", default="")
    parser.add_argument("--worker-json", help="Bounded native capacity or result evidence JSON")
    parser.add_argument("--full", action="store_true", help="Explicit complete output; unbounded")
    analysis = parser.add_mutually_exclusive_group()
    analysis.add_argument('--history', action='store_true', help='Explicit legacy journal analysis; report only')
    analysis.add_argument('--record', action='store_true', help='Optionally record an ended run for analysis; report only')
    parser.add_argument("--task", help="Current phase task ID for context selection")
    context_read = parser.add_mutually_exclusive_group()
    context_read.add_argument("--read", help="Read a current verified reference SHA-256")
    parser.add_argument("--section")
    parser.add_argument("--page", type=int)
    parser.add_argument("--workspace")
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
    parser.add_argument("--recover-from", help="Exact adjacent numbered workflow copy; recover only")
    parser.add_argument("--expected-sha256", help="Expected recovery copy or import request SHA-256")
    parser.add_argument("--recovery-request", help="Absolute hash-pinned state import request file")
    parser.add_argument("--recovery-mode", choices=["inspect", "apply"], help="Preview or apply a same-root state import")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--changed", action="append", default=[])
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        runtime_command.workspace_selector(arguments)
    except ValueError as exc:
        parser.error(str(exc))
    args = parser.parse_args(arguments)
    if args.action != 'revise' and any(value is not None for value in
            (args.revision_mode, args.revision_request, args.revision_sha256, args.revision_note)):
        parser.error('revision options apply only to flow revise')
    if args.preview_digest is not None and args.action != 'revise':
        parser.error('preview digest applies only to a supported revision or upgrade')
    if args.action != 'resume' and (args.resume_mode is not None or args.resume_json is not None):
        parser.error('resume options apply only to flow resume')
    if args.action != 'recover' and args.recover_from is not None:
        parser.error('--recover-from applies only to flow recover')
    if args.action != 'recover' and (args.expected_sha256 is not None or args.recovery_request is not None or args.recovery_mode is not None):
        parser.error('recovery options apply only to flow recover')
    if args.recovery_request is not None and (args.recover_from is not None or args.run is not None or args.expected_revision is not None):
        parser.error('select a pinned import request or an adjacent numbered copy, not both')
    if args.recovery_mode is not None and args.recovery_request is None:
        parser.error('--recovery-mode requires --recovery-request')
    if args.task is not None and args.action not in {"context", "worker"}:
        parser.error("--task applies to context or worker")
    if any(value is not None for value in (args.read, args.section, args.page)) and args.action != "context":
        parser.error("context selection options apply only to flow context")
    if args.update_context and (args.action != "attach" or not args.tasks or not args.run):
        parser.error("--update-context requires attach --tasks and --run")
    if (args.operation or args.grant or args.worker_json) and args.action != "worker":
        parser.error("worker options apply only to flow worker")
    if (args.section is not None or args.page is not None) and args.read is None:
        parser.error("--section and --page require --read")
    workspace = Path(args.workspace or os.getcwd()).resolve()
    workspace_validated = False
    protected: dict[str, Any] = {}
    def show(payload: dict[str, Any]) -> None:
        if payload.get("reason") and protected.get("run"):
            payload = {**payload, "workflow": {**protected, "status": payload["status"]}}
        if not workspace_validated or args.action in {"inspect", "prevalidate"}:
            print(json.dumps(payload))  # Error summaries must not initialize a scratch context store.
        else:
            emit(payload, workspace, args.action, full=args.full or not compact)
    if (args.history or args.record) and args.action != 'report':
        parser.error('--history and --record apply only to flow report')
    if args.replace_run and args.action != "start":
        parser.error("--replace-run applies only to flow start")
    if (args.assessment is not None or args.assessment_json is not None) and args.action != "auto-decide":
        parser.error("assessment options apply only to flow auto-decide")
    if args.action == "hook":
        return run_hook(governor=governor)
    observation_errors: list[str] = []
    try:
        workspace = workspace_binding.resolve_workspace(args.workspace)
        workspace_binding.ensure(workspace)
        workspace_validated = True
        if args.invocation_ref is not None:
            workflow.require(governor is None and args.profile == "native_workflow", "unsupported_authority",
                             "Cooperative invocation cannot replace a protected or supplied controller.")
            from .context import encode
            invoked_result = workflow_host.Controller.invoke_claude(list(sys.argv))
            print(encode(invoked_result).decode("utf-8"))
            return 0
        if governor is None and claude_session({}) and (args.action == "context"
                or args.action == "worker" and args.operation in {'claim'}):
            raise workflow.Refusal("unsupported_authority",
                "Claude invocation identity is unavailable; startup and context operations require an automatic invocation reference.")
        if args.action == 'context' or (args.action == 'worker' and args.operation in
                                       {'prepare', 'claim'}):
            # The current controller and observed actor are sufficient for
            # startup. Usage/history reconciliation is unrelated to delivery.
            # Keep all actual authority checks in the selected controller.
            from .context import encode
            actor = governor.principal if governor else session_id({})
            parent = governor.root if governor else observed_parent({}, actor)
            controller, protected = select_controller(workspace, actor, parent, profile=args.profile,
                                                       governor=governor, diagnostics=False)
            if args.action == 'context':
                response = controller.context(args.run, task=args.task,
                    read=args.read, page=args.page if args.page is not None else 0,
                    section=args.section)
            else:
                workflow.require(args.run and len((args.worker_json or '{}').encode()) <= 65536,
                                 'invalid_evidence', 'Startup operation needs an exact run and bounded request.')
                request = json.loads(args.worker_json or '{}')
                workflow.require(isinstance(request, dict), 'invalid_evidence', 'Worker request must be an object.')
                response = controller.worker(args.run, args.operation, revision=args.expected_revision,
                                             task=args.task or '', grant=args.grant, request=request)
            print(encode(response).decode('utf-8'))
            return 0
        if args.action == 'revise':
            from .context import encode
            from . import workflow_evidence
            workflow.require(args.run and args.revision_request and args.revision_mode and args.revision_sha256,
                             'invalid_evidence', 'Select the exact run, operation and pinned revision request.')
            raw = workflow_evidence.read(workspace, args.revision_request)
            workflow.require(len(raw) <= 262144 and hashlib.sha256(raw).hexdigest() == args.revision_sha256,
                             'stale_checkpoint', 'Requirements revision request changed or exceeds its bound.')
            actor = governor.principal if governor else session_id({})
            parent = governor.root if governor else observed_parent({}, actor)
            selected, _ = select_controller(workspace, actor, parent, profile=args.profile,
                                             governor=governor, diagnostics=False)
            response = selected.revise(args.run, args.revision_mode, json.loads(raw),
                                       preview_digest=args.preview_digest, note=args.revision_note)
            print(encode(response).decode('utf-8'))
            return 0
        if args.action == 'resume':
            from . import workflow_continuation as continuation
            observed_request = continuation.read_request(args.resume_json or '')
            requested_binding = observed_request.get('binding')
            workflow.require(isinstance(requested_binding, dict)
                             and isinstance(requested_binding.get('root'), str)
                             and bool(requested_binding['root']) and args.run,
                             'resume_binding', 'Resume requires the exact original root and run.')
            assert isinstance(requested_binding, dict) and args.run is not None
            actor = governor.principal if governor else session_id({})
            workflow.require(governor is None or governor.root == requested_binding['root'],
                             'resume_binding', 'Supplied controller differs from the requested original root.')
            selected = governor or _controller(workspace, requested_binding['root'], args.profile, principal=actor)
            resume_result = continuation.resume(selected, actor=actor, run=args.run,
                    revision=args.expected_revision, mode=args.resume_mode or 'inspect', request=observed_request,
                    parent=observed_parent({}, actor))
            # This operation does not register context references, publish a
            # dashboard, append a start event, or mutate either owner's store.
            print(json.dumps(resume_result))
            return 0
        if args.action == 'recover':
            session = session_id({})
            workflow.require(not observed_parent({}, session) or independent_fork({}, session),
                             'scope_violation', 'Native children cannot restore workflow state.')
            controller = governor or _controller(workspace, session, args.profile)
            if args.recovery_request is not None:
                from . import state_import
                imported = state_import.recover(controller, args.recovery_request, args.expected_sha256 or '',
                                                mode=args.recovery_mode or 'inspect')
                print(json.dumps(imported))  # Preview creates no derived state or historical events.
            else:
                recovery_result = controller.recover_initialization(args.recover_from or '', args.expected_sha256 or '',
                            args.run or '', args.expected_revision, args.request_reference)
                show(recovery_result)
            return 0
        if args.action == "diagnose":
            diagnostics = workflow_local.diagnose(workspace)
            diagnostic_controller = governor or _controller(workspace, session_id({}), args.profile)
            try:
                state = diagnostic_controller.report()
                diagnostics["workflow"] = {k: state.get(k) for k in ("run", "revision", "phase", "status", "storage")}
            except (workflow.Refusal, OSError, ValueError, TypeError, KeyError) as exc:
                diagnostics["workflow_error"] = str(exc)
            if diagnostic_controller.adapter.profile == 'native_workflow':
                try:
                    target = diagnostic_controller.adapter.control_path(workspace, diagnostic_controller.root)
                    marker = storage.runtime_file(str(workspace), target.stem + '.initialized.json')
                    diagnostics['initialization'] = {'database': target.name, 'database_exists': target.exists(),
                        'marker': marker.name, 'marker_exists': marker.exists(), 'root': diagnostic_controller.root}
                except (workflow.Refusal, OSError, ValueError) as exc:
                    diagnostics['initialization'] = {'error': str(exc), 'root': diagnostic_controller.root}
            diagnostics['status'] = 'blocked' if diagnostics.get('workflow_error') else 'available'
            show(diagnostics)
            return 0
        session = session_id({})
        parent = observed_parent({}, session)
        if args.history:
            show(report(workspace, args.run, governor=governor, history=True) or {})
            return 0
        controller, protected = select_controller(workspace, session, parent, profile=args.profile,
                                                  governor=governor, pending_start=args.action == 'start')
        if (args.action == 'report' and args.run and not args.record and not parent
                and governor is None and args.run != protected.get('run')):
            requested = requested_report_controller(workspace, session, args.run, args.profile)
            if requested is not None:
                controller, protected = requested
        if args.record:
            workflow.require(controller.principal == controller.root and args.run,
                             'scope_violation', 'Select an ended run owned by this root for analysis recording.')
            from . import workflow_retention
            target = controller._path()
            with primitives.file_lock(str(target)):
                ended = workflow_retention.read(workspace, controller._read(target), args.run)
            show(run_journal.record(workspace, ended, note=args.note))
            return 0
        run = live_telemetry.metadata(protected) if protected.get('run') else None
        if controller.principal != controller.root:
            if args.action == 'report' and args.run and not parent:
                from . import workflow_continuation as continuation
                from .worker_runtime import find
                workflow.require(find(protected, controller.principal) is None,
                                 'scope_violation', 'Workers cannot inspect root continuation.')
                print(json.dumps(continuation.guidance(protected)))
                return 0
            workflow.require(args.action == "context" or args.action == "worker" and args.operation in {"claim"},
                             "scope_violation", "Worker CLI is limited to claim and scoped context.")
        if protected.get("visits") and "workflow_binding" in protected.get("scope", {}):
            diagnostic = args.action in {"report", "diagnose", "inspect", "recover",
                                         "retire", "wait", "present", "decide"}
            replacement = args.action == "start" and args.replace_run
            worker_recovery = args.action == "worker" and args.operation in {"status", "recover-unbound", "recover-launch-denied", "abandon"}
            if not (diagnostic or replacement or worker_recovery):
                workflow_local.verify_workflow(workspace, protected)
        if args.action in {"inspect", "prevalidate"}:
            workflow.require(args.run, "invalid_evidence", "Select the exact run for recovery inspection.")
            recovery_read = (controller.inspect(args.run, args.kind or "", args.reference or "",
                                         offset=args.offset, limit=args.limit)
                      if args.action == "inspect" else controller.prevalidate(args.run,
                          revision=args.expected_revision, output=args.output or "", tasks=args.tasks or ""))
            show(recovery_read)
            return 0
        if args.action == "worker":
            from .context import encode
            workflow.require(args.run and args.operation and len((args.worker_json or "{}").encode()) <= 65536,
                             "invalid_evidence", "Worker operation needs run and operation with bounded JSON.")
            request = json.loads(args.worker_json or "{}")
            workflow.require(isinstance(request, dict), "invalid_evidence", "Worker JSON must be an object.")
            print(encode(controller.worker(args.run, args.operation, revision=args.expected_revision,
                task=args.task or "", grant=args.grant, request=request)).decode("utf-8"))
            return 0
        if args.action == "context":
            from .context import encode
            context_result = controller.context(args.run, task=args.task,
                                        read=args.read, page=args.page if args.page is not None else 0, section=args.section)
            print(encode(context_result).decode("utf-8"))
            return 0
        if protected.get('run'):
            run = live_telemetry.metadata(protected)
        if args.run and args.action not in {"report", "attach", "repair"}:
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
        if args.action in {"activate", "deactivate", "present", "wait"}:
            workflow.require(harness is not None, "unsupported_authority", "Harness observations do not admit a protected host owner.")
            assert harness is not None
            if args.action == "activate":
                harness.select(args.phase or "taskplane", args.request_reference, protected)
            elif args.action == "deactivate":
                harness.deactivate(protected, args.request_reference, args.note)
            elif args.action == "wait":
                harness.wait(protected, args.note)
            else:
                workflow.require(len(args.evidence) == 1, "invalid_evidence", "Supply exactly one native dashboard --evidence path.")
                harness.present(protected, args.evidence[0], args.presentation or "", args.note)
            show({"workflow": protected, "harness": harness.readiness(protected),
                  "guidance": harness.guidance(protected)})
            return 0
        if args.action == "start":
            from . import workflow_evidence
            scope = workflow_evidence.object_file(workspace, args.scope) if args.scope else None
            try:
                baseline = counter({}, session)
            except Exception:
                baseline = {'usage': None, 'usage_status': 'unavailable'}
            state = controller.start({"entry": args.phase or "product", "standalone": args.standalone,
                "goal": args.goal, "native_reference": args.native_event, "scope": scope,
                "request_reference": args.request_reference, "tasks": args.tasks,
                "replace_run": args.replace_run, "expected_revision": args.expected_revision})
            try:
                live_telemetry.initialize(workspace, state, baseline)
                if args.replace_run:
                    live_telemetry.close(workspace, controller.report(args.replace_run, diagnostics=False), baseline)
            except Exception:
                observation_errors.append('Run started; advisory telemetry unavailable.')
        elif args.action in {"submit", "decide", "advance", "finish", "repair", "policy", "auto-decide", "retire"}:
            target_run = args.run or protected.get("run")
            workflow.require(target_run, "state_unavailable", "No active workflow binding.")
            assert isinstance(target_run, str)
            if args.action == 'submit':
                refresh_scan(workspace)
            closing_observation = {'usage': None, 'usage_status': 'unavailable'}
            if args.action in {'finish', 'retire'}:
                try:
                    closing_observation = counter({}, session)
                except Exception:
                    pass
            state = controller.apply(args.action, target_run, expected_revision=args.expected_revision,
                output=(args.assessment if args.action == "auto-decide" else args.output) or "",
                tasks=args.tasks or "", phase=args.phase, assessment_json=args.assessment_json,
                native_reference=json.dumps({"request_reference": args.request_reference, "reason": args.note})
                    if args.action == "retire" else args.policy_json if args.action == "policy"
                    else args.decision_json if controller.adapter.profile == "native_workflow" else args.native_event)
            if args.action in {'finish', 'retire'}:
                try:
                    live_telemetry.close(workspace, state, closing_observation)
                except Exception:
                    observation_errors.append('Run ended; advisory closing counter unavailable.')
            if harness and (state.get("finished") or state.get("retired")):
                try:
                    harness.update(selected=False, waiting=None)
                except (OSError, ValueError, workflow.Refusal) as exc:
                    state = {**state, "observation_errors": [f"Optional harness update unavailable: {exc}"]}
        elif args.action in {"progress", "attach"}:
            target_run = args.run or protected.get("run")
            workflow.require(target_run, "state_unavailable", "No active workflow binding.")
            assert isinstance(target_run, str)
            if args.update_context:
                state = controller.update_tasks(target_run, args.expected_revision, args.tasks)
            else:
                state = controller.report(target_run, diagnostics=False)
                workflow.require(not args.phase or args.phase == workflow.current(state)["phase"],
                                 "approval_required", "Progress does not advance a phase.")
                try:
                    live_telemetry.progress(workspace, state, args.action, args.note, artifacts,
                                            capture_attachments(workspace, artifacts))
                except Exception:
                    observation_errors.append('Advisory progress could not be saved; workflow state is unchanged.')
        else:
            # Detailed history/graph/usage projections are explicit report work.
            result = report(workspace, args.run, governor=controller)
            show(result or controller.report(args.run))
            return 0
        # Publish at lifecycle milestones, never on ordinary hook/tool events.
        # A failed view cannot replace the already committed enforcement result.
        if (args.action in {'start', 'submit', 'decide', 'advance', 'finish', 'repair'}
                or args.action in {'progress', 'attach'} and (artifacts or args.update_context)):
            try:
                if args.action != 'submit':
                    refresh_scan(workspace)
                publish_dashboard(workspace, state['run'], governor=controller,
                                  checkpoint_required=args.action in {'submit', 'decide'})
            except Exception:
                observation_errors.append('Workflow action committed; dashboard publication needs retry.')
        state = {**state, "phase": workflow.current(state)["phase"],
                 "status": "retired" if state.get("retired") else "superseded" if state.get("superseded_by")
                 else "accepted" if state.get("finished") else workflow.current(state)["decision"]}
        if observation_errors:
            state['observation_errors'] = observation_errors
        print(json.dumps({"workflow": state, "status": state["status"],
                          "run": state["run"], "revision": state["revision"]}))
        return 0
    except workflow.Refusal as exc:
        failure = exc.result()
        if args.action == "context" and not workspace_validated:
            # Context next_action is an executable continuation bound to a validated
            # root. First-run explanation remains in onboarding.guidance instead.
            failure.pop("next_action", None)
        if args.action == "context" and workspace_validated:
            from .context_handoff import context_action
            task_id, context_run = args.task, args.run or protected.get("run", "")
            if "controller" in locals() and controller.principal != controller.root:
                from .worker_runtime import find
                worker = find(protected, controller.principal)
                task_id = worker["task_id"] if worker else task_id
                context_run = protected.get("run", context_run)
            failure["next_action"] = (context_action(workspace, context_run, task_id)
                if task_id is not None or "controller" not in locals() or controller.principal == controller.root
                else "Report the missing current worker binding to the bound parent.")
        show(failure)
        return 2
    except (OSError, ValueError, TypeError, KeyError, primitives.StateError) as exc:
        show({"status": "blocked", "reason": "state_unavailable",
              "detail": str(exc), "tokens": None})
        return 2


if __name__ == "__main__":
    raise SystemExit(main(compact=True))
