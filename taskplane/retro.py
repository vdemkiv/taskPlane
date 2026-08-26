"""Post-run learning and graph true-up for the delivery loop.

The loop owns the state transition; this module owns the comparatively large
retrospective calculation and its resumable side effects.  A stable retro id
is reserved in loop state before any external write.  Retries use that id to
reuse the KB decision and trace receipt instead of duplicating either one.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid

import depgraph
import kb
import loop_status
import taskplane_lite as tp
import storage as runtime_storage

try:
    from . import dispatch_telemetry, plan_topology
except ImportError:  # pragma: no cover - direct module loading
    import dispatch_telemetry
    import plan_topology


_STAGE_VIEW_LIMIT = 100


def performance_projection(state: dict) -> dict:
    """Consume the shared execution-DAG metrics for the Retro surface."""
    return dispatch_telemetry.retro_projection(state)


def _execution_state(tasks: list, events: list) -> dict:
    """Legacy-only projection for runs without the R-0001 scheduler."""
    dependencies = {
        str(task.get("id")): [str(value) for value in task.get("deps") or []]
        for task in tasks if isinstance(task, dict) and task.get("id")
    }
    starts: dict[str, float] = {}
    terminals: dict[str, float] = {}
    for row in events:
        if not isinstance(row, dict) or not isinstance(row.get("ts"), (int, float)):
            continue
        if row.get("event") == "loop_wave":
            for task_id in row.get("ready") or []:
                starts.setdefault(str(task_id), float(row["ts"]))
        elif row.get("event") == "loop_claim" and row.get("task"):
            starts.setdefault(str(row["task"]), float(row["ts"]))
        elif row.get("event") == "loop_gate" and row.get("task") and \
                row.get("step") == "evaluate":
            terminals[str(row["task"])] = float(row["ts"])
    return {
        "topology": {"effective_dependencies": dependencies},
        "task_times": {
            task_id: {"start": started, "terminal": terminals[task_id]}
            for task_id, started in starts.items() if task_id in terminals
        },
        "scheduler_caused_idle_seconds": sum(
            float(row.get("scheduler_caused_idle_seconds") or 0)
            for row in events if isinstance(row, dict)
            and row.get("event") == "scheduler_metrics"),
    }


def _authoritative_execution_state(
        state: dict, tasks: list, events: list, *,
        execution_dag_store=None) -> tuple[dict, str]:
    scheduler = state.get("performance_scheduler")
    if isinstance(scheduler, dict):
        # This validates statuses, events, and metric computability before
        # Retro signs the report.  The persisted execution DAG remains the
        # authority; trace rows are not used to reconstruct it.
        dispatch_telemetry.scheduler_projection(scheduler)
        if execution_dag_store is None:
            raise ValueError(
                "authoritative execution DAG store is unavailable")
        head = execution_dag_store.read_head()
        if head != scheduler.get("execution_dag_head"):
            raise ValueError(
                "scheduler execution DAG head contradicts stored authority")
        dag = execution_dag_store.read_dag()
        if dag.get("fingerprint") != head.get("fingerprint"):
            raise ValueError("authoritative execution DAG is unavailable")
        authoritative = json.loads(json.dumps(scheduler))
        authoritative["execution_dag"] = dag
        return authoritative, "managed-run-execution-dag-head"
    if isinstance(state.get("dispatch_telemetry"), dict):
        raise ValueError(
            "dispatch telemetry exists without its authoritative scheduler")
    return _execution_state(tasks, events), "legacy-trace-compatibility"


def _managed_execution_dag_store(ws: str, state: dict):
    locator = runtime_storage.load_workspace_locator(ws)
    scheduler = state.get("performance_scheduler")
    run_id = str((scheduler or {}).get("run_id") or "")
    if not isinstance(locator, dict) or not run_id or \
            str(locator.get("run_id") or "") != run_id:
        raise ValueError("authoritative managed-run locator is unavailable")
    home = os.path.realpath(str(locator.get("home") or ""))
    managed_run = os.path.realpath(os.path.join(home, "runs", run_id))
    if not os.path.isdir(managed_run) or \
            os.path.commonpath((home, managed_run)) != home:
        raise ValueError("authoritative managed-run root is unavailable")
    return plan_topology.ExecutionDagRevisionStore(managed_run)


def _events_for_run(ws: str, state: dict) -> tuple[list, float | None]:
    events = []
    for trace_path in tp.trace_paths(ws):
        with open(trace_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    events.append(row)
    starts = [row.get("ts") for row in events
              if row.get("event") == "loop_init"
              and row.get("goal") == state.get("goal")
              and isinstance(row.get("ts"), (int, float))]
    trace_from = max(starts) if starts else None
    if trace_from is not None:
        events = [row for row in events
                  if isinstance(row.get("ts"), (int, float))
                  and row.get("ts") >= trace_from]
    return events, trace_from


def _findings(ws: str, normalize_severity) -> tuple[list, dict, dict]:
    rows = []
    with contextlib.suppress(OSError, ValueError, TypeError):
        with open(runtime_storage.review_public_path(ws, "findings.json"),
                  encoding="utf-8") as f:
            payload = json.load(f)
        rows = [row for row in payload.get("findings") or []
                if isinstance(row, dict)]
    by_severity = {}
    by_lens = {}
    for row in rows:
        severity = normalize_severity(row.get("severity"))
        by_severity[severity] = by_severity.get(severity, 0) + 1
        owner = str(row.get("lens") or row.get("domain") or "unattributed")
        by_lens[owner] = by_lens.get(owner, 0) + 1
    return rows, by_severity, by_lens


def _existing_decision(ws: str, retro_id: str) -> dict | None:
    for row in kb.load_index(ws).get("decisions") or []:
        links = row.get("links") if isinstance(row, dict) else None
        if isinstance(links, dict) and links.get("retro_id") == retro_id:
            return row
    return None


def _trace_seen(ws: str, retro_id: str, *, include_archives: bool = True) \
        -> bool:
    if include_archives:
        paths = tp.trace_paths(ws)
    else:
        # Stage-native Retro never mines predecessor trace history.  The
        # active control-plane tail is sufficient to deduplicate and verify
        # the receipt written by this resumable operation.
        active = os.path.join(tp.tp_dir(ws), "trace.jsonl")
        paths = [active] if os.path.exists(active) else []
    for path in paths:
        with contextlib.suppress(OSError):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    with contextlib.suppress(ValueError, TypeError):
                        row = json.loads(line)
                        if (isinstance(row, dict)
                                and row.get("event") == "loop_retro"
                                and row.get("retro_id") == retro_id):
                            return True
    return False


def _bounded_stage_projection(ws: str) -> tuple[dict, dict] | None:
    """Return the v4 report projection, or ``None`` for an exact v3 run.

    ``bounded_stage_view`` owns discovery and validation.  In particular,
    ambiguous and corrupt v4 state are data, not permission to fall back to
    the singleton trace archive.  The defensive slices keep this report
    bounded even if a future producer accidentally relaxes its own limit.
    """
    resolver = getattr(loop_status, "bounded_stage_view", None)
    if not callable(resolver):
        # Additive rollout compatibility: an older loop_status module means
        # this process has no stage-native reader and remains legacy-only.
        return None
    try:
        raw = resolver(ws, limit=_STAGE_VIEW_LIMIT)
    except Exception as exc:
        raw = {
            "schema": "taskplane.bounded-stage-view/v1",
            "status": "corrupt", "available": False,
            "run_id": None, "revision": None, "current_stage": None,
            "predecessor_stages": [], "child_stage_ids": [],
            "handoff_fingerprint": None, "history": [], "lineage": [],
            "limits": {"history": _STAGE_VIEW_LIMIT,
                       "lineage": _STAGE_VIEW_LIMIT},
            "error": (f"{exc.__class__.__name__}: {exc}")[:512],
        }
    if raw.get("status") == "legacy":
        return None

    view = dict(raw)
    for key in ("predecessor_stages", "child_stage_ids", "history",
                "lineage"):
        rows = view.get(key)
        view[key] = list(rows[:_STAGE_VIEW_LIMIT]) \
            if isinstance(rows, list) else []
    history = view["history"]
    terminal = [row for row in history
                if isinstance(row, dict) and row.get("state") == "terminal"]
    outcomes = {}
    for row in terminal:
        outcome = str(row.get("outcome") or "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    metrics = {
        "status": str(view.get("status") or "corrupt"),
        "available": bool(view.get("available")),
        "history": len(history),
        "lineage": len(view["lineage"]),
        "predecessors": len(view["predecessor_stages"]),
        "children": len(view["child_stage_ids"]),
        "terminal": len(terminal),
        "outcomes": dict(sorted(outcomes.items())),
    }
    return view, metrics


def _write_report(ws: str, state: dict, report: dict, routing: list) -> None:
    graph = report["graph_true_up"]
    lines = [f"# Retro — {state.get('goal', 'track')}", "",
             f"- tasks: {len(report['tasks'])}",
             f"- hook denials: {report['hook_denials']}",
             f"- parallel waves: {report['parallel_waves']}",
             f"- findings: {report['findings']['total']}"]
    performance = report.get("execution_metrics") or {}
    chain = performance.get("longest_serial_chain") or {}
    lines.extend([
        f"- parallelism factor: {performance.get('parallelism_factor', 0)}",
        "- longest serial chain: " +
        " -> ".join(chain.get("tasks") or []) +
        f" ({chain.get('seconds', 0)}s)",
        f"- scheduler-caused idle: "
        f"{performance.get('scheduler_caused_idle_seconds', 0)}s",
        f"- execution metric source: "
        f"{report.get('execution_metric_source', 'unknown')}",
    ])
    foreign = report.get("foreign_interference") or {}
    if foreign.get("headline"):
        lines.extend(["", "## FOREIGN INTERFERENCE", "",
                      "- counts: " + json.dumps(
                          foreign.get("counts") or {}, sort_keys=True),
                      "- identities: " + ", ".join(
                          foreign.get("identities") or []),
                      "- signed roots: " + ", ".join(
                          foreign.get("state_roots") or [])])
    stage = report.get("stage_view")
    stage_metrics = report.get("stage_metrics") or {}
    if isinstance(stage, dict):
        current = stage.get("current_stage")
        current_id = (current.get("stage_id")
                      if isinstance(current, dict) else None)
        lines.extend([
            "", "## Stage lineage", "",
            f"- status: {stage_metrics.get('status')}",
            f"- available: {str(bool(stage_metrics.get('available'))).lower()}",
            f"- current stage: {current_id or 'none'}",
            "- predecessors / children / history / lineage: "
            f"{stage_metrics.get('predecessors', 0)} / "
            f"{stage_metrics.get('children', 0)} / "
            f"{stage_metrics.get('history', 0)} / "
            f"{stage_metrics.get('lineage', 0)}",
            f"- terminal: {stage_metrics.get('terminal', 0)}",
            "- terminal outcomes: " + json.dumps(
                stage_metrics.get("outcomes") or {}, sort_keys=True),
        ])
        if stage.get("error"):
            lines.append("- error: " + str(stage["error"])[:512])
    lines.extend(["",
             "## Graph true-up", "",
             f"- fingerprint: {graph['content_fingerprint']}",
             f"- scanned head: {graph['scanned_head']}",
             "- modules / edges / components: "
             f"{graph['modules']} / {graph['edges']} / {graph['components']}",
             "", "## Lens routing", ""])
    lines.extend("- " + json.dumps(row, sort_keys=True) for row in routing)
    lines.extend(["", "## Lessons", ""])
    lines.extend("- " + str(lesson) for lesson in report["lessons"])
    os.makedirs(tp.tp_dir(ws), exist_ok=True)
    path = os.path.join(tp.tp_dir(ws), "retro.md")
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp):
                os.unlink(tmp)


def run(ws: str, *, load_state, mutate_state, loop_path: str,
        normalize_severity) -> dict:
    """Seal post-run learning and graph state, then close the loop.

    ``prepared`` is durable and switches the loop to the in-flight ``retro``
    step before graph/KB/report writes.  If the process stops, the next call
    resumes under the same id and deduplicates every observable artifact.
    """
    with tp.file_lock(loop_path + ".retro"):
        state = load_state(ws)
        if state is None:
            return {"error": "no active loop"}
        sealed = state.get("retro") or {}
        if (sealed.get("status") == "complete"
                and isinstance(sealed.get("report"), dict)):
            return {**sealed["report"], "replayed": True}
        prior_step = sealed.get("prior_step") or state.get("step")
        if state.get("step") not in ("retro", "failed", "done"):
            return {"error": "retro only runs after sign-off (or after an "
                    f"abort); current step is '{state.get('step')}'"}

        retro_id = sealed.get("id")
        if not retro_id:
            retro_id = uuid.uuid4().hex
            with mutate_state(ws) as locked:
                if locked is None:
                    return {"error": "loop disappeared while retro was starting"}
                current = locked.get("retro") or {}
                if current.get("status") == "complete":
                    return {**current.get("report", {}), "replayed": True}
                if locked.get("step") not in ("retro", "failed", "done"):
                    return {"error": "loop advanced while retro was starting",
                            "step": locked.get("step")}
                locked["step"] = "retro"
                locked["retro"] = {
                    "id": retro_id, "status": "prepared",
                    "prior_step": prior_step, "started_at": time.time(),
                }
            state = load_state(ws)

        stage_projection = _bounded_stage_projection(ws)
        stage_native = stage_projection is not None
        if stage_native:
            stage_view, stage_metrics = stage_projection
            stage_status = stage_metrics["status"]
            if (not stage_metrics["available"]
                    or stage_status in {"corrupt", "ambiguous"}):
                detail = str(
                    stage_view.get("error")
                    or "bounded stage lineage is unavailable"
                )[:512]
                return {
                    "error": ("retro requires an available bounded stage "
                              "projection — loop remains open"),
                    "detail": detail,
                    "stage_projection": {
                        "schema": "taskplane.retro-stage-projection-error/v1",
                        "status": stage_status,
                        "available": False,
                        "run_id": stage_view.get("run_id"),
                        "revision": stage_view.get("revision"),
                        "error": detail,
                    },
                    "step": "retro",
                    "retro_id": retro_id,
                }
            events, trace_from = [], None
        else:
            stage_view, stage_metrics = None, None
            events, trace_from = _events_for_run(ws, state)
        denies = [row for row in events if row.get("event") == "hook_deny"]
        gates = [row for row in events
                 if row.get("event") == "refinement_gate"]
        waves = [row for row in events if row.get("event") == "loop_wave"]
        tasks = state.get("tasks") or []

        accuracy = []
        for gate_row in gates:
            task = next((row for row in tasks
                         if row.get("id") == gate_row.get("task")), None)
            if task is None:
                continue
            actual = task.get("fix_cycles", 0)
            accuracy.append({
                "task": gate_row.get("task"),
                "refinement_score": gate_row.get("score"),
                "actual_fix_cycles": actual,
                "forecast_held": ((actual == 0)
                                  == (gate_row.get("score", 1) >= 0.6)),
            })

        routing = []
        for row in (item for item in events
                    if item.get("event") == "lens_route"):
            counts = {}
            for lens_row in row.get("lenses") or []:
                if isinstance(lens_row, (list, tuple)) and len(lens_row) >= 2:
                    mode = str(lens_row[1])
                    counts[mode] = counts.get(mode, 0) + 1
            routing.append({
                "step": row.get("step"), "counts": counts,
                "kernel_status": row.get("kernel_status"),
                "requested_breadth": row.get("requested_breadth"),
            })

        finding_rows, severity_counts, lens_counts = _findings(
            ws, normalize_severity)
        try:
            import collision
            import runtime_eval
            foreign_interference = runtime_eval.foreign_interference_projection(
                collision.load_ledger(ws))
        except Exception:
            foreign_interference = {
                "schema": "taskplane.foreign-interference-projection/v1",
                "headline": False, "total": 0, "counts": {},
                "identities": [], "state_roots": []}
        lessons = []
        if foreign_interference.get("headline"):
            lessons.append(
                f"foreign interference observed {foreign_interference['total']} "
                "time(s): " + ", ".join(
                    foreign_interference.get("identities") or
                    foreign_interference.get("state_roots") or []))
        if denies:
            lessons.append(
                f"{len(denies)} hook denial(s) — scopes were tighter than "
                "the work wanted; check whether task scopes were too narrow "
                "or the work drifted: "
                + "; ".join(sorted({str(row.get('reason') or '')[:60]
                                     for row in denies}))[:300])
        weak = [row for row in accuracy if not row["forecast_held"]]
        if weak:
            lessons.append("refinement forecast missed on: "
                           + ", ".join(row["task"] for row in weak)
                           + " — revisit the NFR axes routed for those scopes.")
        hi_fix = [row.get("id") for row in tasks
                  if row.get("fix_cycles", 0) >= 2]
        if hi_fix:
            lessons.append("high fix-cycle tasks (requirements were the "
                           "cheap place to catch this): "
                           + ", ".join(str(item) for item in hi_fix))
        if finding_rows and severity_counts.get("high"):
            lessons.append(
                f"{severity_counts['high']} high finding(s) reached final "
                "review — use their lens ownership to move detection earlier.")
        if not lessons:
            lessons.append("clean run — no scope friction, forecasts held.")

        try:
            before = depgraph.load(ws)
            graph = depgraph.scan(ws, decompose="components" in before)
        except Exception as exc:
            return {"error": "retro graph true-up failed — loop remains open",
                    "detail": f"{exc.__class__.__name__}: {exc}",
                    "step": "retro"}
        before_meta = before.get("meta") or {}
        graph_meta = graph.get("meta") or {}
        graph_true_up = {
            "content_fingerprint": graph_meta.get("content_fingerprint"),
            "previous_fingerprint": before_meta.get("content_fingerprint"),
            "changed": (before_meta.get("content_fingerprint")
                        != graph_meta.get("content_fingerprint")),
            "scanned_head": graph_meta.get("scanned_head"),
            "modules": len(graph.get("modules") or {}),
            "edges": len(graph.get("edges") or []),
            "components": len(graph.get("components") or {}),
        }
        try:
            execution_state, execution_metric_source = \
                _authoritative_execution_state(
                    state, tasks, events,
                    execution_dag_store=(
                        _managed_execution_dag_store(ws, state)
                        if isinstance(state.get("performance_scheduler"), dict)
                        else None),
                )
            execution_metrics = performance_projection(execution_state)
        except (ValueError, TypeError,
                dispatch_telemetry.DispatchTelemetryError) as exc:
            return {"error": "retro performance evidence is unavailable — "
                    "loop remains open",
                    "detail": f"{exc.__class__.__name__}: {exc}",
                    "step": "retro", "retro_id": retro_id}
        report = {
            "goal": state.get("goal"),
            "tasks": [{"id": row.get("id"), "status": row.get("status"),
                       "fix_cycles": row.get("fix_cycles", 0)}
                      for row in tasks],
            "hook_denials": len(denies), "parallel_waves": len(waves),
            "forecast_accuracy": accuracy, "lens_routing": routing,
            "findings": {"total": len(finding_rows),
                         "by_severity": dict(sorted(severity_counts.items())),
                         "by_lens": dict(sorted(lens_counts.items()))},
            "foreign_interference": foreign_interference,
            "graph_true_up": graph_true_up,
            "trace_scope": {"from_ts": trace_from, "events": len(events)},
            "lessons": lessons,
            "execution_metrics": execution_metrics,
            "execution_metric_source": execution_metric_source,
            "execution_dag": ({
                "schema": execution_state["execution_dag"]["schema"],
                "fingerprint": execution_state["execution_dag"][
                    "fingerprint"],
                "generations": len(
                    execution_state["execution_dag"].get("generations") or []),
                "nodes": len(
                    execution_state["execution_dag"].get("nodes") or []),
                "edges": len(
                    execution_state["execution_dag"].get("edges") or []),
            } if execution_metric_source ==
            "managed-run-execution-dag-head"
               else None),
        }
        if stage_native:
            report["stage_view"] = stage_view
            report["stage_metrics"] = stage_metrics
            report["trace_scope"] = {
                "source": "bounded-stage-view", "from_ts": None,
                "events": 0,
            }
        scope = sorted({glob for task in tasks for glob in task.get("scope", [])})
        decision = _existing_decision(ws, retro_id)
        if decision is None:
            decision = kb.record_decision(
                ws, f"Retrospective: {state.get('goal', 'track')[:56]}",
                context=(f"{len(tasks)} task(s), {len(denies)} hook denial(s), "
                         f"{len(waves)} wave(s), {len(finding_rows)} finding(s), "
                         "graph " + str(graph_true_up["content_fingerprint"])),
                decision=" | ".join(lessons)[:400], tags=["retrospective"],
                context_files=scope,
                links={"loop": "retro", "retro_id": retro_id,
                       "graph": graph_true_up["content_fingerprint"]})
        report["decision_id"] = decision.get("id")

        if not _trace_seen(ws, retro_id, include_archives=not stage_native):
            tp.trace(ws, "loop_retro", retro_id=retro_id,
                     lessons=len(lessons), denials=len(denies),
                     routes=len(routing), findings=len(finding_rows),
                     graph_fingerprint=graph_true_up["content_fingerprint"])
        if not _trace_seen(ws, retro_id, include_archives=not stage_native):
            return {"error": "retro trace receipt was not recorded — loop "
                    "remains open", "step": "retro", "retro_id": retro_id}

        try:
            _write_report(ws, state, report, routing)
        except OSError as exc:
            return {"error": "retro report write failed — loop remains open",
                    "detail": f"{exc.__class__.__name__}: {exc}",
                    "step": "retro", "retro_id": retro_id}
        with mutate_state(ws) as locked:
            if locked is None:
                return {"error": "loop disappeared while retro was running"}
            current = locked.get("retro") or {}
            if current.get("status") == "complete":
                return {**current.get("report", {}), "replayed": True}
            if current.get("id") != retro_id or locked.get("step") != "retro":
                return {"error": "loop advanced while retro was running",
                        "step": locked.get("step")}
            locked["retro"] = {
                **current, "status": "complete", "completed_at": time.time(),
                "report": report,
            }
            locked["step"] = "failed" if prior_step == "failed" else "done"
        return report
