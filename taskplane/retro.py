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
import taskplane_lite as tp
import storage as runtime_storage


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


def _trace_seen(ws: str, retro_id: str) -> bool:
    for path in tp.trace_paths(ws):
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


def _write_report(ws: str, state: dict, report: dict, routing: list) -> None:
    graph = report["graph_true_up"]
    lines = [f"# Retro — {state.get('goal', 'track')}", "",
             f"- tasks: {len(report['tasks'])}",
             f"- hook denials: {report['hook_denials']}",
             f"- parallel waves: {report['parallel_waves']}",
             f"- findings: {report['findings']['total']}", "",
             "## Graph true-up", "",
             f"- fingerprint: {graph['content_fingerprint']}",
             f"- scanned head: {graph['scanned_head']}",
             "- modules / edges / components: "
             f"{graph['modules']} / {graph['edges']} / {graph['components']}",
             "", "## Lens routing", ""]
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
        lessons = []
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
            "graph_true_up": graph_true_up,
            "trace_scope": {"from_ts": trace_from, "events": len(events)},
            "lessons": lessons,
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

        if not _trace_seen(ws, retro_id):
            tp.trace(ws, "loop_retro", retro_id=retro_id,
                     lessons=len(lessons), denials=len(denies),
                     routes=len(routing), findings=len(finding_rows),
                     graph_fingerprint=graph_true_up["content_fingerprint"])
        if not _trace_seen(ws, retro_id):
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
