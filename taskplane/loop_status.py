"""Loop loading and human-facing status presentation.

This module owns read-model assembly and dashboard decoration, leaving the
state machine in ``loop.py`` focused on transitions and gate validation.
Imports of ``loop`` are intentionally lazy so the public functions retain
their historical signatures without creating an import cycle.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time
from typing import Any

import progress
import taskplane_lite as tp

try:
    from . import host_native
    from . import plan_topology
    from . import settings as operational_settings
    from . import storage as runtime_storage
    from . import wave_metrics
except (ImportError, ValueError):
    from taskplane import host_native
    from taskplane import plan_topology
    from taskplane import settings as operational_settings
    from taskplane import storage as runtime_storage
    from taskplane import wave_metrics


BOUNDED_STAGE_VIEW_SCHEMA = "taskplane.bounded-stage-view/v1"
BOUNDED_STAGE_VIEW_MAX_ITEMS = 100
DASHBOARD_REPLAY_BLOCK_SCHEMA = "taskplane.dashboard-replay-block/v1"
_PHASE_GRAPH_PROJECTOR = None


def configure_phase_graph_projector(projector) -> None:
    """Inject the pure graph projector from a presentation composition root."""
    global _PHASE_GRAPH_PROJECTOR
    if projector is not None and not callable(projector):
        raise TypeError("phase graph projector must be callable")
    _PHASE_GRAPH_PROJECTOR = projector


def _empty_stage_view(*, limit: int, mode: str, status: str,
                      error: str | None = None,
                      run_id: str | None = None) -> dict:
    """Return the stable, non-authoritative stage read-model envelope."""
    return {
        "schema": BOUNDED_STAGE_VIEW_SCHEMA,
        "mode": mode,
        "status": status,
        "available": False,
        "run_id": run_id,
        "revision": None,
        "current_stage": None,
        "predecessor_stages": [],
        "child_stage_ids": [],
        "handoff_fingerprint": None,
        "history": [],
        "lineage": [],
        "limits": {
            "history": limit,
            "lineage": limit,
            "requested": limit,
            "maximum": BOUNDED_STAGE_VIEW_MAX_ITEMS,
        },
        "error": error,
    }


def _stage_view_error(exc: Exception) -> str:
    """Bound diagnostics without interpreting persisted values as markup."""
    message = f"{exc.__class__.__name__}: {exc}"
    encoded = message.encode("utf-8", errors="replace")
    if len(encoded) <= 512:
        return message
    return encoded[:509].decode("utf-8", errors="ignore") + "..."


def _lineage_sort_key(row: dict) -> tuple:
    return (
        str(row.get("child_stage_id") or ""),
        str(row.get("parent_stage_id") or ""),
        tuple(str(value) for value in
              (row.get("predecessor_stage_ids") or [])),
        str(row.get("fingerprint") or ""),
    )


def bounded_stage_view(ws: str, *, limit: int = 100) -> dict:
    """Return a bounded v4 stage/index projection for default read surfaces.

    Only the locator-bound run manifest is opened.  Stage objects, execution
    roots, trace/transcript data, meters, and paginated history are outside
    this read boundary.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not (
            1 <= limit <= BOUNDED_STAGE_VIEW_MAX_ITEMS):
        raise ValueError(
            f"stage view limit must be 1..{BOUNDED_STAGE_VIEW_MAX_ITEMS}")

    import loop

    try:
        locator = loop.runtime_storage.load_workspace_locator(ws)
    except Exception as exc:
        return _empty_stage_view(
            limit=limit, mode="v4", status="corrupt",
            error=_stage_view_error(exc))
    if not isinstance(locator, dict):
        return _empty_stage_view(
            limit=limit, mode="legacy", status="legacy")

    run_id = locator.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return _empty_stage_view(
            limit=limit, mode="v4", status="corrupt",
            error="workspace stage locator has no valid run id")
    try:
        store = loop._stage_store(ws, run_id)
        manifest = store.load(run_id)
    except Exception as exc:
        return _empty_stage_view(
            limit=limit, mode="v4", status="corrupt", run_id=run_id,
            error=_stage_view_error(exc))

    if manifest.get("schema") == "taskplane.run/v3":
        return _empty_stage_view(
            limit=limit, mode="legacy", status="legacy", run_id=run_id)
    if manifest.get("schema") != "taskplane.run/v4":
        return _empty_stage_view(
            limit=limit, mode="v4", status="corrupt", run_id=run_id,
            error="run manifest schema is not taskplane.run/v4")

    try:
        # RunStore.load owns the exact locator-bound manifest read; its index
        # validator proves summary/lineage/receipt fingerprints and the
        # active-projection binding without following any object reference.
        try:
            if __package__:
                from . import run_store as stage_run_store
            else:
                import run_store as stage_run_store
        except ImportError:  # pragma: no cover - direct script import mode
            import run_store as stage_run_store
        if manifest.get("run_id") != run_id:
            raise stage_run_store.RunStoreError(
                "run manifest identity does not match the workspace locator")
        stage_run_store._validate_stage_index(manifest)

        heads = manifest["stage_heads"]
        projection = manifest["active_stage_projection"]
        active_ids = list(projection["active_stage_ids"])
        foreground_id = projection["foreground_stage_id"]
        if foreground_id is not None:
            current_id = foreground_id
            view_status = "v4"
            available = True
            view_error = None
        elif len(active_ids) == 1:
            current_id = active_ids[0]
            view_status = "v4"
            available = True
            view_error = None
        elif len(active_ids) > 1:
            current_id = None
            view_status = "ambiguous"
            available = False
            view_error = ("active stage projection has several active "
                          "stages and no foreground stage")
        else:
            current_id = None
            view_status = "v4"
            available = True
            view_error = None

        stage_ids = sorted(heads)
        history_ids = stage_ids[:limit]
        history = [copy.deepcopy(heads[stage_id]["summary"])
                   for stage_id in history_ids]
        history_id_set = set(history_ids)
        lineage_rows = sorted(
            (row for row in manifest["lineage"]
             if row["child_stage_id"] in history_id_set),
            key=_lineage_sort_key,
        )[:limit]
        lineage = [copy.deepcopy(row) for row in lineage_rows]

        current = (copy.deepcopy(heads[current_id]["summary"])
                   if current_id is not None else None)
        if current is None:
            predecessor_stages = []
            child_stage_ids = []
            handoff_fingerprint = None
        else:
            predecessor_ids = list(current["predecessor_stage_ids"])
            predecessor_stages = [
                copy.deepcopy(heads[stage_id]["summary"])
                for stage_id in predecessor_ids[:limit]
            ]
            children = {
                row["child_stage_id"] for row in manifest["lineage"]
                if row["parent_stage_id"] == current_id or
                current_id in row["predecessor_stage_ids"]
            }
            child_stage_ids = sorted(children)[:limit]
            handoff_fingerprint = current["input_manifest_fingerprint"]

        return {
            "schema": BOUNDED_STAGE_VIEW_SCHEMA,
            "mode": "v4",
            "status": view_status,
            "available": available,
            "run_id": run_id,
            "revision": manifest.get("revision"),
            "current_stage": current,
            "predecessor_stages": predecessor_stages,
            "child_stage_ids": child_stage_ids,
            "handoff_fingerprint": handoff_fingerprint,
            "history": history,
            "lineage": lineage,
            "limits": {
                "history": limit,
                "lineage": limit,
                "requested": limit,
                "maximum": BOUNDED_STAGE_VIEW_MAX_ITEMS,
            },
            "error": view_error,
        }
    except Exception as exc:
        return _empty_stage_view(
            limit=limit, mode="v4", status="corrupt", run_id=run_id,
            error=_stage_view_error(exc))


def _include_stage_view(view: dict) -> bool:
    return view.get("status") in {"v4", "ambiguous", "corrupt"}


def _canonical_fingerprint(value: object) -> str:
    material = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _dashboard_block_path(ws: str) -> str:
    managed = runtime_storage.managed_path(
        ws, "state", "dashboard-publication-block.json")
    return managed or os.path.join(
        tp.tp_dir(ws), "dashboard-publication-block.json")


def _dashboard_replay_block(ws: str) -> dict | None:
    path = _dashboard_block_path(ws)
    value = tp.load_json(
        path, default=None, what="dashboard publication replay block")
    if value is None:
        return None
    fields = {
        "schema", "event_type", "outcome", "state_fingerprint",
        "source_fingerprint", "error", "recorded_at", "fingerprint",
    }
    if not isinstance(value, dict) or set(value) != fields or \
            value.get("schema") != DASHBOARD_REPLAY_BLOCK_SCHEMA:
        raise ValueError("dashboard publication replay block is invalid")
    payload = {key: value[key] for key in value if key != "fingerprint"}
    if value.get("fingerprint") != _canonical_fingerprint(payload):
        raise ValueError(
            "dashboard publication replay block fingerprint is invalid")
    for field in ("event_type", "outcome", "state_fingerprint", "error",
                  "recorded_at"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError(
                f"dashboard publication replay block {field} is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", value["state_fingerprint"]):
        raise ValueError(
            "dashboard publication replay block state identity is invalid")
    source = value.get("source_fingerprint")
    if source is not None and not (
            isinstance(source, str) and re.fullmatch(r"[0-9a-f]{64}", source)):
        raise ValueError(
            "dashboard publication replay block source identity is invalid")
    return value


def _loop_state_fingerprint(ws: str) -> str | None:
    import loop
    state = loop.load(ws)
    return _canonical_fingerprint(state) if isinstance(state, dict) else None


def _dashboard_source_fingerprint(ws: str) -> str | None:
    try:
        source = _select_dashboard_source(ws)
    except Exception:
        return None
    fingerprint = source.get("source_fingerprint")
    return (fingerprint if isinstance(fingerprint, str) and
            re.fullmatch(r"[0-9a-f]{64}", fingerprint) else None)


def _write_dashboard_replay_block(
        ws: str, *, event_type: str, outcome: str,
        state_fingerprint: str, source_fingerprint: str | None,
        error: str) -> dict:
    payload = {
        "schema": DASHBOARD_REPLAY_BLOCK_SCHEMA,
        "event_type": str(event_type), "outcome": str(outcome),
        "state_fingerprint": state_fingerprint,
        "source_fingerprint": source_fingerprint,
        "error": str(error),
        "recorded_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"),
    }
    value = {**payload, "fingerprint": _canonical_fingerprint(payload)}
    tp.atomic_write_json(_dashboard_block_path(ws), value, sort_keys=True)
    return value


def _clear_dashboard_replay_block(ws: str) -> None:
    path = _dashboard_block_path(ws)
    tp.safe_remove(path)
    if os.path.lexists(path):
        raise ValueError("dashboard publication replay block was not cleared")


def _dashboard_block_status(ws: str) -> dict | None:
    try:
        block = _dashboard_replay_block(ws)
    except Exception as exc:
        return {"status": "blocked", "replay_required": True,
                "error": _stage_view_error(exc)}
    if block is None:
        return None
    return {
        "status": "blocked", "replay_required": True,
        "event_type": block["event_type"], "outcome": block["outcome"],
        "error": block["error"], "fingerprint": block["fingerprint"],
    }


def load_tasks(ws: str, state: dict) -> None:
    path = os.path.join(ws, "plan", "tasks.json")
    if not os.path.exists(path):
        state["tasks"] = []
        return
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    tasks = data.get("tasks", data) if isinstance(data, dict) else data
    for task in tasks:
        task.setdefault("status", "pending")
        task.setdefault("fix_cycles", 0)
    state["tasks"] = tasks
    ab = bool(
        (isinstance(data, dict) and data.get("mode") == "ab-selection")
        or any(task.get("variant") for task in tasks))
    state["ab"] = ab
    if ab:
        state.pop("selection", None)
    if ab and not state.get("parallel"):
        state["parallel"] = True
        tp.trace(ws, "ab_forced_parallel",
                 note="A/B variants require isolated worktrees")


def status(ws: str) -> dict:
    import loop

    stage_view = bounded_stage_view(ws)
    state = loop.load(ws)
    if state is None:
        out = {
            "loop": "none",
            **({"stage_view": stage_view}
               if _include_stage_view(stage_view) else {}),
        }
        blocked = _dashboard_block_status(ws)
        if blocked is not None:
            out["dashboard_publication"] = blocked
        return out
    tasks = state.get("tasks") or []
    out = {
        "step": state["step"], "goal": state["goal"],
        "tasks": [
            {"id": task["id"], "status": task.get("status"),
             "fix_cycles": task.get("fix_cycles", 0),
             **({"evaluation": task["evaluation"]}
                if task.get("evaluation") else {}),
             **({"variant": task["variant"]} if task.get("variant") else {})}
            for task in tasks],
        "current_task": state.get("current_task"),
        "max_fix_cycles": state["max_fix_cycles"],
        "checkpoints": state["checkpoints"],
    }
    if state.get("enforcement"):
        out["enforcement"] = state["enforcement"]
    if state.get("ab"):
        out["ab"] = True
    if state.get("design_required"):
        out["design"] = {
            "only": bool(state.get("design_only")),
            "approved": bool(state.get("design_fingerprint")),
            "fingerprint": state.get("design_fingerprint"),
        }
    if state.get("selection"):
        out["selection"] = state["selection"]
    out["live_progress"] = progress.read_workspace_status(
        ws, now=time.time(), state_dir=tp.tp_dir(ws))
    if _include_stage_view(stage_view):
        out["stage_view"] = stage_view
    blocked = _dashboard_block_status(ws)
    if blocked is not None:
        out["dashboard_publication"] = blocked
    return out


def user_summary(ws: str, host: str | None = None,
                 now: float | None = None) -> dict:
    """Human control-plane read model over existing durable artifacts."""
    import loop

    stage_view = bounded_stage_view(ws)
    state = loop.load(ws)
    if state is None:
        return {"state": "not_started", "action_required": False,
                "headline": "No active taskplane run.",
                "next": "Tell taskplane what to build or review.",
                **({"stage_view": stage_view}
                   if _include_stage_view(stage_view) else {})}
    tasks = state.get("tasks") or []
    settled = sum(1 for task in tasks
                  if task.get("status") in loop.SETTLED)
    step = state.get("step")
    action = {
        "design_approval": "Review and approve the proposed Design Contract.",
        "plan_approval": "Review and approve the implementation plan.",
        "selection": "Choose the A/B variant or request a hybrid.",
        "signoff": "Review the engineering evidence and sign off.",
        "escalated": "Choose retry, skip/defer, or abort.",
    }.get(step)
    current = loop._current_task(state)
    budget = None
    budget_blocked = False
    try:
        contract = tp.load_active(ws)
    except Exception:
        contract = None
    if contract and (contract.get("budget") or {}).get("max_actions"):
        maximum = int(contract["budget"]["max_actions"])
        task_id = contract.get("task_id", "_")
        try:
            with open(os.path.join(tp.tp_dir(ws), "meter.json"),
                      encoding="utf-8") as stream:
                used = int((json.load(stream).get(task_id) or {})
                           .get("actions", 0))
        except (OSError, ValueError, TypeError):
            used = 0
        budget = {"used": used, "max": maximum,
                  "exhausted": used >= maximum}
        if budget["exhausted"] and step not in loop.TERMINAL_STEPS \
                and not action:
            action = "Grant more actions (tp budget --grant N) or clear the contract"
            budget_blocked = True
    live_progress = progress.read_workspace_status(
        ws, now=float(now if now is not None else time.time()),
        state_dir=tp.tp_dir(ws))
    host = host or ("codex" if os.environ.get("CODEX_HOME")
                    or os.environ.get("CODEX_THREAD_ID") else
                    "claude-tag" if tp.store_env() == "repo" else "claude")
    assurance = ("state-and-evidence enforced; tool interception is cooperative"
                 if host == "claude-tag" else
                 "state, evidence, and tool boundaries mechanically enforced")
    enforcement = ((state.get("enforcement") or {}).get("current"))
    if isinstance(enforcement, dict):
        assurance = "screen enforcement " + str(
            enforcement.get("status") or "unproven")
        advisory = enforcement.get("advisory") or {}
        if advisory.get("actor"):
            assurance += "; acknowledged by " + str(advisory["actor"])
    if step == "done":
        headline = f"Complete — {settled}/{len(tasks)} task(s) settled."
    elif budget_blocked:
        headline = ("Blocked — action budget exhausted "
                    f"({budget['used']}/{budget['max']}).")
    elif action:
        headline = f"Decision required — {action}"
    else:
        label = current.get("id") if current else step
        headline = (f"In progress — {settled}/{len(tasks)} task(s) settled; "
                    f"current: {label} ({step}).")
    dashboard_block = _dashboard_block_status(ws)
    return {
        **({"budget": budget} if budget else {}),
        "state": step, "goal": state.get("goal"),
        "progress": {"settled": settled, "total": len(tasks)},
        "current_task": current and {"id": current.get("id"),
                                     "status": current.get("status")},
        "action_required": bool(action), "decision": action,
        "headline": headline, "host": host, "assurance": assurance,
        **({"enforcement": enforcement} if enforcement else {}),
        "live_progress": live_progress,
        "submission_pending_validation": bool(
            state.get("_submission")
            or any(task.get("_submission") for task in tasks)),
        **({"stage_view": stage_view}
           if _include_stage_view(stage_view) else {}),
        **({"dashboard_publication": dashboard_block}
           if dashboard_block is not None else {}),
    }


DASHBOARD_PUBLICATION_SCHEMA = host_native.DASHBOARD_PUBLICATION_SCHEMA


def _load_legacy_state(ws: str) -> dict | None:
    import loop
    return loop.load(ws)


def _load_v4_manifest(ws: str, locator: dict) -> dict:
    import loop
    store = loop._stage_store(ws, str(locator["run_id"]))
    return store.load(str(locator["run_id"]))


def _validate_v4_manifest(manifest: dict) -> None:
    try:
        from . import run_store as stage_run_store
    except (ImportError, ValueError):
        from taskplane import run_store as stage_run_store
    stage_run_store._validate_stage_index(manifest)


def _select_dashboard_source(ws: str) -> dict:
    return host_native.select_dashboard_source(
        ws, locator_loader=runtime_storage.load_workspace_locator,
        legacy_loader=_load_legacy_state, manifest_loader=_load_v4_manifest,
        manifest_validator=_validate_v4_manifest,
        error_formatter=_stage_view_error)


def _phase_graph_impact(
        ws: str, tasks: list[dict[str, Any]], supplied: object = None) \
        -> Mapping[str, Any]:
    """Compose the graph owner's current impact without a renderer import."""
    if isinstance(supplied, Mapping) and supplied.get("touched"):
        return supplied
    scope = sorted({str(path).rstrip("*").rstrip("/")
                    for task in tasks
                    for path in task.get("scope") or () if path})
    if not scope:
        return supplied if isinstance(supplied, Mapping) else {}
    try:
        # ``loop`` is already this read model's state/engine boundary.  Reuse
        # its canonical depgraph owner instead of coupling presentation back
        # to dashboard.py or creating another impact implementation.
        import loop
        return loop.depgraph.impact(
            ws, scope, policy=loop.depgraph.aggregate_impact_policy(tasks))
    except Exception:
        return supplied if isinstance(supplied, Mapping) else {}


def phase_graph_projection(
        workspace: str, state: Mapping[str, Any] | None = None, *,
        snapshot_values: Mapping[str, Any] | None = None,
        impact: Mapping[str, Any] | None = None,
        module_impact_limit: int = 8,
        require_bound: bool = False) -> dict[str, Any]:
    """Compose the pure phase projector with current approval evidence.

    Design remains the owner of approval truth and Plan topology remains the
    graph projector.  This read boundary supplies their already-established
    loop adapters so neither presentation module needs to import the other.
    """
    design_artifact_fingerprint = None
    if require_bound and isinstance(state, Mapping) and isinstance(
            state.get("design_fingerprint"), str):
        try:
            import loop
            contract, errors = loop._design_contract(workspace)  # noqa: SLF001
            if not errors and isinstance(contract, dict):
                design_artifact_fingerprint = \
                    loop._design_evidence_fingerprint(  # noqa: SLF001
                        workspace, contract)
        except Exception:
            # Strict publication treats unavailable proof as unbound.  Never
            # reuse mutable Design files merely because rendering must proceed.
            design_artifact_fingerprint = None
    return plan_topology.phase_graph_projection(
        workspace, state, snapshot_values=snapshot_values, impact=impact,
        module_impact_limit=module_impact_limit, loop_loader=_load_legacy_state,
        impact_loader=lambda ws, tasks: _phase_graph_impact(ws, tasks),
        require_bound=require_bound,
        design_artifact_fingerprint=design_artifact_fingerprint)


def refresh_dashboard_snapshot(
        ws: str, *, event_type: str, outcome: str | None = None,
        committed_at: float | str | None = None, replay: bool = False) -> dict:
    settings = operational_settings.load_settings()
    projector = _PHASE_GRAPH_PROJECTOR
    if projector is None:
        # ``loop`` is also a supported public API, not only the CLI
        # composition root in tp.py. Resolve the same canonical projector at
        # call time so direct governed transitions cannot silently publish a
        # graph-less snapshot.
        projector = phase_graph_projection
    return host_native.refresh_dashboard_snapshot(
        ws, event_type=event_type, outcome=outcome,
        committed_at=committed_at, replay=replay,
        settings_digest=settings.digest, source_loader=_select_dashboard_source,
        graph_projector=projector,
        metrics_projector=wave_metrics.consumer_projection,
        publication_loader=runtime_storage.load_dashboard_publication,
        snapshot_committer=runtime_storage.commit_dashboard_snapshot,
        event_committer=runtime_storage.commit_dashboard_event,
        error_formatter=_stage_view_error)

def publish_artifacts(ws: str) -> "str | None":
    import views
    return views._publish_artifacts(ws)


def _publication_problem(ws: str, publication: dict, result: dict) -> str | None:
    """Return why this public transition is not sealed/current."""
    import views
    if publication.get("status") != "ready":
        return ("dashboard snapshot source is not ready: "
                f"{publication.get('status') or 'unknown'}")
    snapshot = publication.get("snapshot")
    if not isinstance(snapshot, dict) or not re.fullmatch(
            r"[0-9a-f]{64}", str(snapshot.get("fingerprint") or "")):
        return "dashboard snapshot receipt is missing or invalid"
    if any(value != snapshot["fingerprint"] for value in
           (publication.get("surfaces") or {}).values()):
        return "dashboard snapshot surface bindings are severed"
    values = snapshot.get("values")
    if not isinstance(values, dict):
        return "dashboard snapshot values are missing"
    if values.get("phase_graph_error"):
        return "dashboard dependency graph is unavailable: " + str(
            values["phase_graph_error"])
    graph_keys = {
        key for key in (
            "design_graph", "plan_task_dag", "plan_waves", "module_impact")
        if isinstance(values.get(key), dict)
    }

    dashboard = result.get("dashboard")
    if not isinstance(dashboard, dict):
        return "dashboard delivery result is missing"
    if dashboard.get("error"):
        return "dashboard delivery is degraded: " + str(dashboard["error"])
    delivery = dashboard.get("delivery")
    if not isinstance(delivery, dict) or delivery.get("status") != "published":
        return "dashboard delivery is not published"
    receipt = delivery.get("publication_receipt")
    head = delivery.get("current_head")
    if not isinstance(receipt, dict) or \
            receipt.get("fingerprint") != \
            views.dashboard_publication_receipt_fingerprint(receipt):
        return "dashboard publication receipt is invalid"
    receipt_snapshot = receipt.get("snapshot")
    if not isinstance(receipt_snapshot, dict) or \
            receipt_snapshot.get("fingerprint") != snapshot["fingerprint"]:
        return "dashboard rendered snapshot is stale"
    if not isinstance(head, dict) or \
            head.get("snapshot_fingerprint") != snapshot["fingerprint"] or \
            head.get("receipt_fingerprint") != receipt.get("fingerprint"):
        return "dashboard durable head is stale or contradictory"
    graph_bindings = receipt.get("graphs")
    if not isinstance(graph_bindings, dict) or set(graph_bindings) != graph_keys:
        return "dashboard graph publication bindings are incomplete"
    for key in graph_keys:
        if graph_bindings.get(key) != _canonical_fingerprint(values[key]):
            return f"dashboard graph publication binding is stale: {key}"
    html = (delivery.get("artifacts") or {}).get("html")
    if not isinstance(html, dict) or html.get("status") != "available":
        return "dashboard HTML artifact is unavailable"
    preservation = dashboard.get("run_artifacts")
    preservation_required = \
        runtime_storage.load_workspace_locator(ws) is not None or \
        (isinstance(preservation, dict) and
         preservation.get("status") != "unavailable")
    if preservation_required and (
            not isinstance(preservation, dict) or
            preservation.get("status") != "preserved"):
        return "dashboard/graph run-artifact preservation is degraded"
    return None


def _publish_and_verify_dashboard(
        ws: str, *, event_type: str, outcome: str, replay: bool) -> tuple[dict, dict]:
    publication = refresh_dashboard_snapshot(
        ws, event_type=event_type, outcome=outcome, replay=replay)
    if publication.get("status") == "no_active":
        raise ValueError("dashboard publication found no active governed run")
    replay_result = {
        "step": ((publication.get("snapshot") or {}).get("stage")),
        "outcome": outcome, "dashboard_snapshot": publication,
    }
    import views
    views.refresh_views(ws, replay_result)
    problem = _publication_problem(ws, publication, replay_result)
    if problem is not None:
        raise ValueError(problem)
    return publication, replay_result


def _replay_dashboard_block(ws: str, block: dict) -> dict:
    state_fingerprint = _loop_state_fingerprint(ws)
    if state_fingerprint != block["state_fingerprint"]:
        raise ValueError(
            "dashboard replay block names another governed state")
    source_fingerprint = _dashboard_source_fingerprint(ws)
    if block["source_fingerprint"] is not None and \
            source_fingerprint != block["source_fingerprint"]:
        raise ValueError(
            "dashboard replay block names another dashboard source")
    publication, replay_result = _publish_and_verify_dashboard(
        ws, event_type=block["event_type"], outcome=block["outcome"],
        replay=True)
    _clear_dashboard_replay_block(ws)
    return {"status": "replayed", "snapshot": publication,
            "dashboard": replay_result["dashboard"],
            "block_fingerprint": block["fingerprint"]}


def with_dashboard(fn):
    def wrapped(ws, *args, **kwargs):
        # Load before the wrapped transition so malformed settings cannot
        # follow a state write with a merely stale dashboard warning.
        settings = operational_settings.load_settings()
        try:
            block = _dashboard_replay_block(ws)
        except runtime_storage.StorageIdentityError as exc:
            return {
                "error": "stage-native transition refused because workspace "
                         "storage identity is unreadable: "
                         f"{_stage_view_error(exc)}",
                "stage_native": "read-only",
            }
        replay_result = None
        if block is not None:
            try:
                replay_result = _replay_dashboard_block(ws, block)
            except Exception as exc:
                return {
                    "error": "dashboard publication replay is required before "
                             f"the next governed transition: {_stage_view_error(exc)}",
                    "dashboard_refresh": {
                        "status": "blocked", "replay_required": True,
                        "error": block["error"],
                        "fingerprint": block["fingerprint"],
                    },
                    "status": status(ws),
                }
        before_fingerprint = _loop_state_fingerprint(ws)
        result = fn(ws, *args, **kwargs)
        if isinstance(result, dict):
            # A stage-native refusal is a proven read-only boundary.  Do not
            # turn that refusal into dashboard/event/artifact writes against
            # the mismatched or disabled store it explicitly rejected.
            if result.get("stage_native") == "read-only" and result.get("error"):
                return result
            outcome = result.get("outcome")
            if outcome is None:
                outcome = "failure" if result.get("error") else "success"
            try:
                if fn.__name__ not in settings.dashboard.refresh.lifecycle_events:
                    raise ValueError(
                        "dashboard lifecycle event is absent from canonical "
                        f"settings: {fn.__name__}")
                publication = refresh_dashboard_snapshot(
                    ws, event_type=fn.__name__, outcome=str(outcome))
                if publication.get("status") == "no_active":
                    if _loop_state_fingerprint(ws) is not None:
                        raise ValueError(
                            "dashboard publication found no active governed run")
                else:
                    result["dashboard_snapshot"] = publication
                    import views
                    views.refresh_views(ws, result)
                    problem = _publication_problem(ws, publication, result)
                    if problem is not None:
                        raise ValueError(problem)
                if replay_result is not None:
                    result["dashboard_replay"] = replay_result
            except Exception as exc:
                after_fingerprint = _loop_state_fingerprint(ws)
                detail = _stage_view_error(exc)
                if after_fingerprint is not None and \
                        after_fingerprint != before_fingerprint:
                    block = _write_dashboard_replay_block(
                        ws, event_type=fn.__name__, outcome=str(outcome),
                        state_fingerprint=after_fingerprint,
                        source_fingerprint=_dashboard_source_fingerprint(ws),
                        error=detail)
                    result["dashboard_refresh"] = {
                        "status": "blocked", "replay_required": True,
                        "error": detail, "fingerprint": block["fingerprint"],
                    }
                else:
                    result["dashboard_refresh"] = {
                        "status": "stale", "replay_required": False,
                        "error": detail,
                    }
        return result
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    wrapped.__wrapped__ = fn
    return wrapped
