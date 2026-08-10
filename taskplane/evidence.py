"""Evidence bundle — the mechanically-derivable half of an evaluation.

Extracted from loop.py (P2, R-0012) the same way audit.py was, so the
loop-engine line ratchet keeps holding and this concern owns its own file.

THE PERFORMANCE REGRESSION THIS CLOSES. Measured over v3 phase 3: agents
spent 41 percent of shell wall-clock rebuilding, one shell call at a time,
facts the engine already held — which criteria exist, which lenses routed,
what the diff touched, which graph nodes are impacted, whether the suite
passed. At roughly eighteen seconds per shell call (a model turn plus its
execution), assembling a verdict by hand cost about sixty tool calls per
evaluator run. This module hands all of it over in one.

THE SPLIT THAT KEEPS THIS HONEST. The engine fills only what it can
COMPUTE. It never fills a JUDGMENT. Every `status`, `verdict` and
`evidence` slot comes back empty on purpose, and loop._evaluation_errors
already refuses an empty one — so a bundle straight from the engine cannot
pass a gate. What the agent stops doing is transcription; what it still
owes is exactly the reasoning it always owed.

WHY THIS MODULE IS DELIBERATELY NOT IN tp.VALIDATOR_SURFACE. The bundle is
UNTRUSTED INPUT. The evaluate gate re-derives the criteria set, the routed
lens set and the graph obligations independently in loop._evaluation_errors
and validates the submitted verdict against its OWN derivation. A bundle
that under-stated an obligation therefore cannot smuggle a pass — it just
walks its evaluator into a refusal. Adding this module to the fingerprint
surface would imply the gate trusts it; the gate does not, and must not.
"""

from __future__ import annotations

import os

import taskplane_lite as tp
import depgraph
import lens as lens_router


EVIDENCE_JUDGMENT_KEYS = ("status", "verdict", "evidence", "blockers")


def evidence(ws: str, task_id: "str | None" = None,
             write: bool = False) -> dict:
    """Assemble every mechanically-derivable fact an evaluator needs, plus a
    verdict skeleton whose judgment slots are deliberately empty.

    `write` drops the skeleton at .eval/verdict.json ONLY when no verdict is
    already there — the engine never overwrites an agent's authored
    judgment, and never authors one."""
    import loop          # late — loop re-exports evidence()
    state = loop.load(ws)
    if not state:
        return {"error": "no active loop"}
    task = None
    if task_id:
        for t in state.get("tasks") or []:
            if str(t.get("id")) == str(task_id):
                task = t
                break
        if task is None:
            return {"error": f"no task {task_id!r} in this loop"}
    else:
        task = loop._current_task(state)
    if not task:
        return {"error": "no current task"}

    base = state.get("baseline") or "HEAD"
    out: dict = {"task": task.get("id"),
                 "req": task.get("req") or state.get("requirement_id"),
                 "baseline": base,
                 "generated_by": "tp loop evidence",
                 "judgment_owed": list(EVIDENCE_JUDGMENT_KEYS)}

    # --- the suite. Goes through dod_check's cache, so a wave's tasks over
    # identical content pay for one execution between them, not one each.
    tests = str(task.get("tests") or "").strip()
    if tests:
        env = {k: v for k, v in os.environ.items() if k != "TASKPLANE_TASK"}
        hit = tp.suite_cache_lookup(ws, tests, env)
        if hit is not None:
            out["suite"] = {"command": tests, "returncode": hit.get("returncode"),
                            "cited": True, "tail": hit.get("tail"),
                            "seconds_saved": hit.get("duration_s")}
            # Trace the citation exactly as dod_check does — a cited run is
            # evidence, and evidence absent from the audit log is not evidence.
            tp.trace(ws, "suite_cache_hit", command=str(tests),
                     key=hit.get("key"), returncode=hit.get("returncode"),
                     seconds_saved=hit.get("duration_s"),
                     produced_in=hit.get("produced_in"), via="evidence_bundle")
        else:
            import time as _t
            t0 = _t.time()
            proc = tp._run(tests, cwd=ws, shell=True, env=env)
            elapsed = _t.time() - t0
            tail = " | ".join(
                (proc.stdout + proc.stderr).strip().splitlines()[-5:])
            tp.suite_cache_store(ws, tests, env, returncode=proc.returncode,
                                 tail=tail, duration_s=elapsed)
            out["suite"] = {"command": tests, "returncode": proc.returncode,
                            "cited": False, "tail": tail,
                            "seconds": round(elapsed, 2)}

    # --- the diff the judgment is about
    try:
        changed = tp.changed_files(ws, base)
        stat = tp._run(["git", "diff", "--shortstat", base], cwd=ws)
        out["diff"] = {"base": base, "files": sorted(changed),
                       "shortstat": stat.stdout.strip()}
    except Exception as e:
        out["diff"] = {"base": base, "error": f"{e.__class__.__name__}: {e}"}
        changed = []

    # --- what must be proven (slots empty — the engine states the
    #     obligation, the agent discharges it)
    out["criteria"] = [{"criterion": c, "status": "", "evidence": ""}
                       for c in loop._criteria_for(ws, state, task)]

    # --- which lenses owe a verdict. Derived with EVALUATE_ROUTE_STAGE, the
    #     same single source the gate validates against, so the bundle can
    #     never brief a narrower set than the gate will demand.
    try:
        routing = lens_router.route_git_diff(
            ws, base=base, task_type=task.get("type"),
            stage=loop.EVALUATE_ROUTE_STAGE, breadth="routed")
        out["lenses"] = [{"lens": e["id"], "mode": e.get("mode"),
                          "verdict": "", "blockers": None}
                         for e in routing.get("lenses") or []
                         if e.get("mode") != "none"]
        out["lenses_not_applicable"] = [
            e["id"] for e in routing.get("lenses") or []
            if e.get("mode") == "none"]
    except Exception as e:
        # Fail LOUD, never quiet: a bundle that silently dropped the lens
        # obligation would look complete while briefing nothing.
        out["lenses_error"] = (f"{e.__class__.__name__}: {e} — route the "
                               "lenses manually (`tp lens route`); do not "
                               "submit without lens verdicts")

    # --- graph obligations, when the loop governs the graph
    if state.get("graph_governance"):
        try:
            graph_dod = loop._task_graph_dod(ws, state, task)
            impact = graph_dod.get("impact") or {}
            direct = sorted({e.get("module")
                             for e in (impact.get("impacted") or {}).get(1, [])
                             if e.get("module")
                             and not str(e.get("module")).startswith("req:")})
            prod = depgraph.product_impact(
                ws, graph_dod.get("realized_modules") or [])
            own = task.get("req") or state.get("requirement_id")
            own = depgraph.req_node(own) if own else None
            affected = sorted(r for r in
                              prod.get("affected_requirements") or []
                              if r != own)
            contracts = sorted({
                str(c.get("id") if isinstance(c, dict) else c)
                for c in task.get("contracts") or []
                if str((c.get("id") if isinstance(c, dict) else c) or "").strip()})
            out["graph"] = {
                "errors": graph_dod.get("errors") or [],
                "dispositions": [{"node": n, "status": "", "evidence": ""}
                                 for n in direct],
                "requirements_checked": [],
                "requirements_to_check": affected,
                "contracts_checked": [],
                "contracts_to_verify": contracts,
                "disposition_vocabulary": sorted(
                    ["tested", "contract-verified", "unaffected",
                     "follow-up", "requires-replan"])}
        except Exception as e:
            out["graph_error"] = (f"{e.__class__.__name__}: {e} — compute the "
                                  "graph evidence manually (`tp graph "
                                  "impact`); the gate still requires it")

    out["verdict"] = ""
    out["failures"] = []
    out["note"] = ("Judgment slots are empty by design — the engine states "
                   "what must be proven, it never proves it. Fill status/"
                   "evidence per criterion and verdict/blockers per lens, "
                   "set verdict to 'pass' only if every one holds, then "
                   "submit. An unfilled slot is refused at the gate.")

    if write:
        path = os.path.join(ws, ".eval", "verdict.json")
        if os.path.exists(path):
            out["written"] = False
            out["write_note"] = (".eval/verdict.json already exists — left "
                                 "untouched; the engine never overwrites an "
                                 "authored judgment")
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tp.atomic_write_json(path, out)
            out["written"] = True
    tp.trace(ws, "evidence_bundle", task=task.get("id"),
             suite_cited=bool((out.get("suite") or {}).get("cited")),
             criteria=len(out.get("criteria") or []),
             lenses=len(out.get("lenses") or []))
    return out
