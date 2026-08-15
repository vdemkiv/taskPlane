"""Audited, human-attributed recovery transitions for the delivery loop."""

from __future__ import annotations

import copy
import time


REPLANNABLE_STEPS = frozenset({
    "plan_approval", "execute", "evaluate", "fix", "escalated",
})


def replan(ws: str, *, by: str, reason: str, load_state, mutate_state,
           clear_contract, trace, record_decision) -> dict:
    """Return frozen delivery configuration to a fresh Plan approval.

    This is the governed escape hatch for configuration defects discovered
    after approval. It preserves the frozen tasks in append-only loop history,
    requires human attribution, and forces the replacement plan through the
    human Plan checkpoint even when the original loop omitted that checkpoint.
    """
    by = str(by or "").strip()
    reason = str(reason or "").strip()
    if not by:
        return {"error": "replan requires --by with the human approver"}
    if not reason:
        return {"error": "replan requires --reason describing the defect"}

    state = load_state(ws)
    if state is None:
        return {"error": "no active loop"}
    entry_step = state.get("step")
    if entry_step not in REPLANNABLE_STEPS:
        return {
            "error": "replan is available only after a plan was frozen "
                     "(plan_approval/execute/evaluate/fix/escalated); current "
                     f"step is '{entry_step}'",
            "step": entry_step,
        }

    prior_tasks = []
    with mutate_state(ws) as locked:
        if locked is None:
            return {"error": "no active loop"}
        if locked.get("step") != entry_step:
            return {
                "error": "the loop advanced concurrently during replan "
                         f"(was '{entry_step}', now '{locked.get('step')}')",
                "step": locked.get("step"),
            }
        # Snapshot from the locked state, not the optimistic read above: a
        # parallel task can settle while this human transition is waiting for
        # the lock without changing the top-level execute step.
        prior_tasks = copy.deepcopy(locked.get("tasks") or [])
        record = {
            "from_step": entry_step,
            "by": by,
            "reason": reason,
            "ts": time.time(),
            "baseline": locked.get("baseline"),
            "tasks": prior_tasks,
        }
        locked.setdefault("replan_history", []).append(record)
        locked["step"] = "plan"
        locked["tasks"] = None
        locked["current_task"] = 0
        checkpoints = list(locked.get("checkpoints") or [])
        if "plan" not in checkpoints:
            checkpoints.append("plan")
        locked["checkpoints"] = checkpoints
        for key in ("baseline", "ab", "selection", "_submission",
                    "_suite_evidence", "_validated_suite_evidence",
                    "_build_failed"):
            locked.pop(key, None)

    # The old worker contract governed the frozen task. Release it only after
    # the new state is durable so an interruption never leaves an unrecorded
    # transition. Parallel contracts remain isolated to retired worktrees.
    clear_error = None
    try:
        clear_contract(ws)
    except Exception as exc:
        clear_error = f"{exc.__class__.__name__}: {exc}"
        trace(ws, "loop_replan_contract_release_failed", error=clear_error)
    trace(ws, "loop_replan", from_step=entry_step, by=by, reason=reason,
          archived_tasks=len(prior_tasks))
    try:
        record_decision(
            ws, "Delivery returned to Plan",
            context=f"From: {entry_step}\nBy: {by}\nReason: {reason}",
            decision="The frozen task configuration was archived; a new plan "
                     "and fresh human approval are required.",
            tags=["replan", "human-gate"],
            links={"loop": "replan", "from_step": entry_step})
    except Exception:
        # Loop-state history is authoritative. A KB projection failure must
        # not re-strand delivery after the transition committed.
        trace(ws, "loop_replan_kb_projection_failed", from_step=entry_step,
              by=by)
    out = {
        "step": "plan",
        "replanned": True,
        "from_step": entry_step,
        "archived_tasks": len(prior_tasks),
        "instruction": "Revise plan/tasks.json, run loop next and the Plan "
                       "gate, then obtain fresh human plan approval.",
    }
    if clear_error:
        out["error"] = (
            "replan state committed, but the old contract could not be "
            f"released ({clear_error}); run `tp clear --workspace <repo>` "
            "from the ungoverned orchestrator before `loop next`")
    return out
