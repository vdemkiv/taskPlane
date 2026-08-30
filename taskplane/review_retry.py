"""Bounded ReviewKernel retry selection."""
from __future__ import annotations

import storage as runtime_storage
import taskplane_lite as tp


def fingerprinted_reuse_plan(prior_route: dict, current_route: dict,
                             prior_results: list[dict]) -> dict:
    """Conservatively split current selected lenses into reuse and dispatch.

    A route fingerprint is deliberately too broad for selective retry: any
    Fix changes the route as a whole.  Reuse is therefore authorized only by
    an equal canonical *per-lens* input fingerprint plus one sealed,
    host-verified passing result.  Every malformed, missing, failed, stale, or
    newly selected row fails toward a fresh worker.
    """
    current_selected = current_route.get("dispatchable_selected")
    if not isinstance(current_selected, list) or any(
            not isinstance(lens_id, str) or not lens_id
            for lens_id in current_selected):
        raise ValueError("current focused route has no dispatchable selection")
    if len(set(current_selected)) != len(current_selected):
        raise ValueError("current focused route repeats a selected lens")
    prior_selected = set(prior_route.get("selected") or [])
    prior_fingerprints = prior_route.get("lens_input_fingerprints") or {}
    current_fingerprints = current_route.get("lens_input_fingerprints") or {}
    if not isinstance(prior_fingerprints, dict) or not isinstance(
            current_fingerprints, dict):
        raise ValueError("focused route lens fingerprints are unavailable")

    results = {}
    duplicate_results = set()
    for raw in prior_results if isinstance(prior_results, list) else []:
        row = raw if isinstance(raw, dict) else {}
        lens_id = str(row.get("lens") or "")
        if not lens_id:
            continue
        if lens_id in results:
            duplicate_results.add(lens_id)
        results[lens_id] = row

    reused, dispatch, invalidation, reuse_evidence = [], [], {}, {}
    same_policy = prior_route.get("policy_version") == current_route.get(
        "policy_version")
    same_catalog = prior_route.get("catalog_fingerprint") == current_route.get(
        "catalog_fingerprint")
    for lens_id in current_selected:
        result = results.get(lens_id) or {}
        reason = None
        if lens_id not in prior_selected:
            reason = "new_selection"
        elif not same_policy:
            reason = "policy_version_changed"
        elif not same_catalog:
            reason = "catalog_version_changed"
        elif prior_fingerprints.get(lens_id) != current_fingerprints.get(lens_id):
            reason = "input_fingerprint_changed"
        elif lens_id in duplicate_results:
            reason = "prior_result_ambiguous"
        elif result.get("verdict") != "pass" or int(
                result.get("blockers") or 0) != 0:
            reason = "prior_result_not_passing"
        elif result.get("sealed") is not True or \
                result.get("host_provenance") != "verified" or not str(
                    result.get("result_fingerprint") or ""):
            reason = "prior_result_provenance_invalid"
        if reason:
            dispatch.append(lens_id)
            invalidation[lens_id] = reason
        else:
            reused.append(lens_id)
            reuse_evidence[lens_id] = {
                "lens_input_fingerprint": current_fingerprints[lens_id],
                "result_fingerprint": result["result_fingerprint"],
            }
    return {
        "schema": "taskplane.lens-evidence-reuse-plan/v1",
        "policy_version": current_route.get("policy_version"),
        "catalog_fingerprint": current_route.get("catalog_fingerprint"),
        "reused": reused,
        "dispatch": dispatch,
        "invalidation": invalidation,
        "reuse_evidence": reuse_evidence,
    }


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
