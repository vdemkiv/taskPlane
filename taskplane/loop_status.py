"""Loop loading and human-facing status presentation.

This module owns read-model assembly and dashboard decoration, leaving the
state machine in ``loop.py`` focused on transitions and gate validation.
Imports of ``loop`` are intentionally lazy so the public functions retain
their historical signatures without creating an import cycle.
"""
from __future__ import annotations

import json
import os
import time

import depgraph
import progress
import taskplane_lite as tp


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

    state = loop.load(ws)
    if state is None:
        return {"loop": "none"}
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
    return out


def user_summary(ws: str, host: str | None = None,
                 now: float | None = None) -> dict:
    """Human control-plane read model over existing durable artifacts."""
    import loop

    state = loop.load(ws)
    if state is None:
        return {"state": "not_started", "action_required": False,
                "headline": "No active taskplane run.",
                "next": "Tell taskplane what to build or review."}
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
    graph = depgraph.summary(ws)
    live_progress = progress.read_workspace_status(
        ws, now=float(now if now is not None else time.time()),
        state_dir=tp.tp_dir(ws))
    host = host or ("codex" if os.environ.get("CODEX_HOME")
                    or os.environ.get("CODEX_THREAD_ID") else
                    "claude-tag" if tp.store_env() == "repo" else "claude")
    assurance = ("state-and-evidence enforced; tool interception is cooperative"
                 if host == "claude-tag" else
                 "state, evidence, and tool boundaries mechanically enforced")
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
        "graph": graph, "live_progress": live_progress,
        "submission_pending_validation": bool(
            state.get("_submission")
            or any(task.get("_submission") for task in tasks)),
    }


def publish_artifacts(ws: str) -> "str | None":
    import views
    return views._publish_artifacts(ws)


def with_dashboard(fn):
    def wrapped(ws, *args, **kwargs):
        result = fn(ws, *args, **kwargs)
        if isinstance(result, dict) and "error" not in result:
            import views
            views.refresh_views(ws, result)
        return result
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    wrapped.__wrapped__ = fn
    return wrapped
