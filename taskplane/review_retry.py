"""Bounded ReviewKernel retry selection."""
from __future__ import annotations

import storage as runtime_storage
import taskplane_lite as tp


def binding_key(step: str, task: dict | None) -> str:
    return f"{step}:{str((task or {}).get('id') or '_')}"


def binding(state: dict, step: str,
            task: dict | None = None) -> dict | None:
    """Return the exact ReviewKernel identity minted for one loop action."""
    row = ((state.get("review_kernel_runs") or {}).get(binding_key(step, task)))
    if not isinstance(row, dict):
        return None
    run_id = str(row.get("run_id") or "")
    if len(run_id) != 32 or any(ch not in "0123456789abcdef" for ch in run_id):
        return None
    return dict(row)


def incremental_context(ws: str, diff_ws: str, task: dict | None,
                        binding: dict | None) -> dict | None:
    """Reuse sealed passing lenses and return only prior failed lenses."""
    if not task or int(task.get("fix_cycles") or 0) <= 0 or not binding:
        return None
    try:
        import review
        prior_ws = str(binding.get("workspace") or diff_ws)
        prior = review._load_state(prior_ws, binding["run_id"])
        verdict = tp.load_json(
            runtime_storage.evaluation_path(ws), default=None,
            what="prior evaluator verdict")
    except Exception:
        return None
    if (prior.get("status") != "complete" or not isinstance(verdict, dict) or
            verdict.get("task") != task.get("id") or
            verdict.get("verdict") != "fail"):
        return None
    failed = sorted({
        str(row.get("lens") or "") for row in verdict.get("lenses") or []
        if isinstance(row, dict) and (
            row.get("verdict") == "fail" or int(row.get("blockers") or 0) > 0)
    } - {""})
    if not failed:
        return None
    return {"lenses": failed, "source_run_id": binding["run_id"]}
