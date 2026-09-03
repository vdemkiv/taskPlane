"""Post-run learning and graph true-up for the delivery loop.

The loop owns the state transition; this module owns the comparatively large
retrospective calculation and its resumable side effects.  A stable retro id
is reserved in loop state before any external write.  Retries use that id to
reuse the KB decision and trace receipt instead of duplicating either one.
"""

from __future__ import annotations

import contextlib
import hashlib
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
    from . import plan_topology, run_artifacts, wave_metrics
except ImportError:  # pragma: no cover - direct module loading
    import plan_topology
    import run_artifacts
    import wave_metrics


_STAGE_VIEW_LIMIT = 100
TERMINAL_ARTIFACT_BUNDLE_SCHEMA = \
    "taskplane.terminal-artifact-bundle/v1"
_TERMINAL_OUTCOMES = frozenset({
    "success", "failure", "cancellation", "interruption", "timeout",
    "handoff", "recovery",
})


def _content_fingerprint(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def performance_projection(state: dict) -> dict:
    """Compute delivery metrics from native dispatch and loop trace facts."""
    return plan_topology.execution_metrics(state)


def evaluator_summary(tasks: list) -> dict:
    """Project supplied evaluator identities and outcomes without raw prose."""
    rows = []
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(
                task.get("evaluation"), dict):
            continue
        evaluation = task["evaluation"]
        status = str(evaluation.get("status") or "unknown")
        reason = str(evaluation.get("reason_code") or "unspecified")
        outage = evaluation.get("outage_identity")
        identity = (str(outage.get("fingerprint") or "")
                    if isinstance(outage, dict) else "")
        if len(identity) != 64 or any(
                character not in "0123456789abcdef" for character in identity):
            identity = ""
        rows.append({
            "task": str(task.get("id") or evaluation.get("task") or
                        "unknown"),
            "status": status, "verdict": str(
                evaluation.get("verdict") or "unknown"),
            "reason_code": reason,
            "identity_fingerprint": identity or None,
        })
        counts[status] = counts.get(status, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "total": len(rows), "by_status": dict(sorted(counts.items())),
        "by_reason": dict(sorted(reasons.items())), "evaluators": rows,
    }


def sealed_wave_metrics_projection(state: dict) -> dict:
    """Consume a sealed wave receipt; never reconstruct metrics in Retro."""
    receipt = state.get("wave_metrics_receipt")
    if receipt is None:
        unavailable = state.get("wave_metrics_unavailable")
        reason = (str(unavailable.get("reason"))
                  if isinstance(unavailable, dict) and
                  unavailable.get("reason") else
                  "sealed terminal wave metrics receipt is unavailable")
        return wave_metrics.unavailable_consumer_projection(
            consumer="retro", reason=reason,
            attempts=(unavailable.get("attempts")
                      if isinstance(unavailable, dict) else None))
    projection = wave_metrics.consumer_projection(receipt, consumer="retro")
    if projection["signoff"]["ready"] is not True:
        raise wave_metrics.WaveMetricsError(
            "wave metrics have blocking cleanup or ceiling evidence")
    return projection


def sealed_root_hygiene_projection(state: dict) -> dict:
    """Consume the same canonical root seal used by every terminal view."""
    receipt = state.get("root_hygiene_receipt")
    if not isinstance(receipt, dict):
        raise wave_metrics.WaveMetricsError(
            "canonical root hygiene receipt is unavailable")
    return wave_metrics.root_hygiene_projection(receipt, consumer="retro")


def publish_terminal_artifacts(
        artifact_root: str, *, wave_receipt: dict | None, report: dict,
        lifecycle_outcome: str, publication_attempt: int = 1) -> dict:
    """Publish telemetry and Retro beside the run before cleanup begins.

    The two class entries share one deterministic bundle fingerprint.  A
    retry reuses already-published entries and fills only a missing peer,
    while any cleanup entry refuses a late or reordered terminal publication.
    The run-artifact manifest supplies the authoritative candidate binding.
    """
    if lifecycle_outcome not in _TERMINAL_OUTCOMES:
        raise wave_metrics.WaveMetricsError(
            "terminal artifact lifecycle outcome is invalid")
    if isinstance(publication_attempt, bool) or not isinstance(
            publication_attempt, int) or publication_attempt < 1:
        raise wave_metrics.WaveMetricsError(
            "terminal artifact publication attempt is invalid")
    unavailable = report.get("wave_metrics_unavailable")
    if wave_receipt is None:
        if not isinstance(unavailable, dict) or unavailable.get("schema") != \
                "taskplane.wave-metrics-unavailable/v1":
            raise wave_metrics.WaveMetricsError(
                "terminal metrics need a measured receipt or attributable "
                "unavailable record")
        material = {key: value for key, value in unavailable.items()
                    if key != "fingerprint"}
        if unavailable.get("fingerprint") != _content_fingerprint(material):
            raise wave_metrics.WaveMetricsError(
                "terminal unavailable metrics fingerprint is invalid")
        projection = wave_metrics.unavailable_consumer_projection(
            consumer="retro", reason=str(unavailable.get("reason") or ""),
            attempts=list(unavailable.get("attempts") or []))
        sealed = None
    else:
        sealed = wave_metrics.validate_wave_receipt(wave_receipt)
        projection = wave_metrics.consumer_projection(
            sealed, consumer="retro")
    supplied = report.get("wave_metrics")
    if supplied is not None and supplied != projection:
        raise wave_metrics.WaveMetricsError(
            "Retro report metrics do not match the sealed terminal receipt")
    terminal_report = {**report, "wave_metrics": projection}
    evaluators = terminal_report.get("evaluator_summary")
    if not isinstance(evaluators, dict) or not isinstance(
            evaluators.get("evaluators"), list):
        raise wave_metrics.WaveMetricsError(
            "terminal Retro requires evaluator identity and outcome summary")
    if sealed is not None and sealed.get("evaluator_summary") is not None \
            and sealed.get("evaluator_summary") != evaluators:
        raise wave_metrics.WaveMetricsError(
            "terminal Retro evaluator summary does not match metrics evidence")

    manifest = run_artifacts.load_manifest(artifact_root)
    binding = manifest["binding"]
    metrics_candidate = (sealed["run"]["candidate_fingerprint"]
                         if sealed is not None else
                         unavailable.get("candidate_fingerprint"))
    if metrics_candidate is not None and \
            binding["candidate"].get("fingerprint") != metrics_candidate:
        raise wave_metrics.WaveMetricsError(
            "terminal artifacts belong to another working candidate")
    if manifest["classes"]["cleanup"]["entries"]:
        raise wave_metrics.WaveMetricsError(
            "terminal metrics and Retro must be sealed before cleanup")

    telemetry_payload = {
        "schema": TERMINAL_ARTIFACT_BUNDLE_SCHEMA,
        "role": "terminal-telemetry",
        "lifecycle_outcome": lifecycle_outcome,
        "wave_metrics_receipt": sealed,
        "wave_metrics_unavailable": (None if sealed is not None else
                                     dict(unavailable)),
        "token_usage": projection["token_usage"],
        "evaluator_summary": evaluators,
    }
    retro_payload = {
        "schema": TERMINAL_ARTIFACT_BUNDLE_SCHEMA,
        "role": "terminal-retro",
        "lifecycle_outcome": lifecycle_outcome,
        "report": terminal_report,
    }
    bundle_fingerprint = _content_fingerprint({
        "schema": TERMINAL_ARTIFACT_BUNDLE_SCHEMA,
        "binding_fingerprint": binding["fingerprint"],
        "lifecycle_outcome": lifecycle_outcome,
        "telemetry_fingerprint": _content_fingerprint(telemetry_payload),
        "retro_fingerprint": _content_fingerprint(retro_payload),
    })
    metadata_base = {
        "terminal_bundle_fingerprint": bundle_fingerprint,
        "lifecycle_outcome": lifecycle_outcome,
        "publication_attempt": publication_attempt,
    }

    def existing(artifact_class: str) -> dict | None:
        current = run_artifacts.load_manifest(artifact_root)
        matches = [entry for entry in current["classes"][artifact_class][
            "entries"] if entry.get("metadata", {}).get(
                "terminal_bundle_fingerprint") == bundle_fingerprint]
        if len(matches) > 1:
            raise wave_metrics.WaveMetricsError(
                "terminal artifact retry found duplicate durable entries")
        return matches[0] if matches else None

    telemetry_entry = existing("telemetry")
    if telemetry_entry is None:
        telemetry_entry = run_artifacts.publish_artifact(
            artifact_root, "telemetry", telemetry_payload,
            metadata={**metadata_base, "artifact_role": "terminal-telemetry"})
    current = run_artifacts.load_manifest(artifact_root)
    if current["classes"]["cleanup"]["entries"]:
        raise wave_metrics.WaveMetricsError(
            "cleanup started before terminal Retro was sealed")
    retro_entry = existing("retro")
    if retro_entry is None:
        retro_entry = run_artifacts.publish_artifact(
            artifact_root, "retro", retro_payload,
            metadata={**metadata_base, "artifact_role": "terminal-retro"})
    verification = run_artifacts.verify_manifest(
        artifact_root, expected_binding=binding)
    return {
        "schema": TERMINAL_ARTIFACT_BUNDLE_SCHEMA,
        "bundle_fingerprint": bundle_fingerprint,
        "lifecycle_outcome": lifecycle_outcome,
        "publication_attempt": publication_attempt,
        "telemetry": telemetry_entry, "retro": retro_entry,
        "artifact_verification": verification,
    }


def _execution_state(tasks: list, events: list) -> dict:
    """Project dependency timing from native wave/claim/gate trace events."""
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
    }


def _authoritative_execution_state(
        state: dict, tasks: list, events: list) -> tuple[dict, str]:
    """Use repository/run-native trace facts; no private concurrency authority."""
    return (_execution_state(tasks, events),
            "native-dispatch-and-loop-trace")


def _events_for_run(
        ws: str, state: dict, *, active_only: bool = False) \
        -> tuple[list, float | None]:
    events = []
    paths = ([os.path.join(tp.tp_dir(ws), "trace.jsonl")]
             if active_only else tp.trace_paths(ws))
    for trace_path in paths:
        try:
            trace_file = open(trace_path, encoding="utf-8")
        except FileNotFoundError:
            continue
        with trace_file as f:
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
        f"- execution metric source: "
        f"{report.get('execution_metric_source', 'unknown')}",
    ])
    wave = report.get("wave_metrics") or {}
    if wave:
        wave_signoff = wave.get("signoff") or {}
        token_usage = wave.get("token_usage") or {}
        token_status = str(token_usage.get("status") or "unavailable")
        lines.extend([
            "", "## Sealed wave metrics", "",
            f"- receipt: {wave.get('receipt_fingerprint')}",
            f"- candidate: {wave.get('candidate_fingerprint')}",
            f"- integration ready: {wave.get('integration_ready_at')}",
            f"- sign-off ready: {str(bool(wave_signoff.get('ready'))).lower()}",
            "- source digests: " + json.dumps(
                wave.get("source_digests") or {}, sort_keys=True),
            f"- token usage status: {token_status}",
            "- observed total tokens: " + (
                str(token_usage.get("total_tokens"))
                if token_usage.get("total_tokens") is not None else
                "unavailable"),
            "- observed uncached input tokens: " + (
                str(token_usage.get("uncached_input_tokens"))
                if token_usage.get("uncached_input_tokens") is not None else
                "unavailable"),
            "- observed effective tokens: " + (
                str(token_usage.get("effective_tokens"))
                if token_usage.get("effective_tokens") is not None else
                "unavailable"),
        ])
        if token_usage.get("reason"):
            lines.append("- token usage reason: " + str(
                token_usage["reason"])[:512])
    evaluators = report.get("evaluator_summary") or {}
    lines.extend([
        "", "## Evaluator outcomes", "",
        f"- total: {evaluators.get('total', 0)}",
        "- by status: " + json.dumps(
            evaluators.get("by_status") or {}, sort_keys=True),
        "- by reason: " + json.dumps(
            evaluators.get("by_reason") or {}, sort_keys=True),
    ])
    lines.extend(
        "- " + json.dumps(row, sort_keys=True)
        for row in evaluators.get("evaluators") or [])
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
            events, trace_from = _events_for_run(
                ws, state, active_only=True)
        else:
            stage_view, stage_metrics = None, None
            events, trace_from = _events_for_run(ws, state)
        denies = [row for row in events if row.get("event") == "hook_deny"]
        gates = [row for row in events
                 if row.get("event") == "refinement_gate"]
        waves = [row for row in events if row.get("event") == "loop_wave"]
        tasks = state.get("tasks") or []
        try:
            metrics_projection = sealed_wave_metrics_projection(state)
        except wave_metrics.WaveMetricsError as exc:
            return {"error": "retro wave metrics evidence is unavailable — "
                    "loop remains open",
                    "detail": f"{exc.__class__.__name__}: {exc}",
                    "step": "retro", "retro_id": retro_id}

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
        metrics_projection = sealed_wave_metrics_projection(state)
        if metrics_projection.get("token_usage", {}).get("status") != \
                "available":
            lessons.append(
                "terminal token usage is unavailable — the run cannot claim "
                "complete measurement or a clean telemetry outcome.")
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
                _authoritative_execution_state(state, tasks, events)
            execution_metrics = performance_projection(execution_state)
        except (ValueError, TypeError,
                plan_topology.PlanTopologyError) as exc:
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
            "evaluator_summary": evaluator_summary(tasks),
        }
        if metrics_projection is not None:
            report["wave_metrics"] = metrics_projection
        if stage_native:
            report["stage_view"] = stage_view
            report["stage_metrics"] = stage_metrics
            report["trace_scope"] = {
                "source": "active-run-trace",
                "from_ts": trace_from,
                "events": len(events),
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
