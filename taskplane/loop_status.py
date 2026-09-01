"""Loop loading and human-facing status presentation.

This module owns read-model assembly and dashboard decoration, leaving the
state machine in ``loop.py`` focused on transitions and gate validation.
Imports of ``loop`` are intentionally lazy so the public functions retain
their historical signatures without creating an import cycle.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time

import progress
import taskplane_lite as tp

try:
    from . import host_native
    from . import settings as operational_settings
    from . import storage as runtime_storage
    from . import wave_metrics
except (ImportError, ValueError):
    from taskplane import host_native
    from taskplane import settings as operational_settings
    from taskplane import storage as runtime_storage
    from taskplane import wave_metrics


BOUNDED_STAGE_VIEW_SCHEMA = "taskplane.bounded-stage-view/v1"
BOUNDED_STAGE_VIEW_MAX_ITEMS = 100
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
        return {
            "loop": "none",
            **({"stage_view": stage_view}
               if _include_stage_view(stage_view) else {}),
        }
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


def refresh_dashboard_snapshot(
        ws: str, *, event_type: str, outcome: str | None = None,
        committed_at: float | str | None = None, replay: bool = False) -> dict:
    settings = operational_settings.load_settings()
    return host_native.refresh_dashboard_snapshot(
        ws, event_type=event_type, outcome=outcome,
        committed_at=committed_at, replay=replay,
        settings_digest=settings.digest, source_loader=_select_dashboard_source,
        graph_projector=_PHASE_GRAPH_PROJECTOR,
        metrics_projector=wave_metrics.consumer_projection,
        publication_loader=runtime_storage.load_dashboard_publication,
        snapshot_committer=runtime_storage.commit_dashboard_snapshot,
        event_committer=runtime_storage.commit_dashboard_event,
        error_formatter=_stage_view_error)

def publish_artifacts(ws: str) -> "str | None":
    import views
    return views._publish_artifacts(ws)


def with_dashboard(fn):
    def wrapped(ws, *args, **kwargs):
        # Load before the wrapped transition so malformed settings cannot
        # follow a state write with a merely stale dashboard warning.
        settings = operational_settings.load_settings()
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
                if publication.get("status") != "no_active":
                    result["dashboard_snapshot"] = publication
                    import views
                    views.refresh_views(ws, result)
            except Exception as exc:
                # The committed lifecycle outcome stays authoritative. A
                # failed publication becomes replayable secondary evidence.
                result["dashboard_refresh"] = {
                    "status": "stale", "replay_required": True,
                    "error": _stage_view_error(exc),
                }
        return result
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    wrapped.__wrapped__ = fn
    return wrapped
