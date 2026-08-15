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
UNTRUSTED INPUT. Criteria and graph dispositions are checked by the gate,
while routed lens obligations are cited from the immutable decision already
owned by the ReviewKernel and checked against that same decision. The bundle
never maps again and therefore cannot drift from a fail-closed kernel.
"""

from __future__ import annotations

import os

import taskplane_lite as tp
import depgraph
import evaluation_output
import host_capabilities


EVIDENCE_JUDGMENT_KEYS = ("status", "verdict", "evidence", "blockers")


def _verdict_template(out: dict) -> dict:
    """Project rich engine context into the strict evaluator output shape."""
    graph = out.get("graph") if isinstance(out.get("graph"), dict) else {}
    return {
        "schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": str(out.get("task") or ""),
        "requirement": str(out.get("req") or ""),
        "verdict": "",
        "criteria": list(out.get("criteria") or []),
        "lenses": list(out.get("lenses") or []),
        "graph": {
            "dispositions": list(graph.get("dispositions") or []),
            "requirements_checked": [],
            "contracts_checked": [],
        },
        "failures": [],
    }


def _canonical_kernel_obligations(ws: str, expected_stage: str) -> dict:
    """Read the live kernel's terminal graph/routing decision by reference."""
    import review
    import review_evidence

    kernel = review._load_state(ws)
    status = str(kernel.get("status") or "")
    stage = str(kernel.get("stage") or "")
    manifest = kernel.get("manifest") or {}
    citation = {
        "schema": "taskplane.review-kernel-citation/v1",
        "run_id": kernel.get("run_id"), "status": status, "stage": stage,
        "target_fingerprint": (kernel.get("target") or {}).get(
            "fingerprint") or manifest.get("target_fingerprint"),
        "graph_quality": manifest.get("graph_quality"),
        "routing_decision": manifest.get("routing_decision"),
    }
    if stage != expected_stage:
        raise RuntimeError(
            f"live review kernel stage is {stage!r}, expected {expected_stage!r}")
    if status not in {"ready", "prepared", "committed", "complete"}:
        return {"citation": citation, "lenses": [], "not_applicable": [],
                "error": (f"live review kernel is {status}; graph quality "
                          "must be repaired before lens evaluation")}
    decision_ref = kernel.get("routing_decision")
    if not isinstance(decision_ref, dict):
        raise RuntimeError("live review kernel has no routing decision")
    payload = review_evidence.ArtifactStore(ws).read(decision_ref)
    dispositions = payload.get("dispositions")
    if not isinstance(dispositions, dict) or len(dispositions) != 26:
        raise RuntimeError("live review kernel routing decision is incomplete")
    lenses, not_applicable = [], []
    for lens_id, row in sorted(dispositions.items()):
        verdict = (row or {}).get("verdict")
        if verdict == "n/a":
            not_applicable.append(lens_id)
        elif verdict in {"deep", "light"}:
            lenses.append({"lens": lens_id,
                           "mode": "subagent" if verdict == "deep" else "inline",
                           "verdict": "", "blockers": None})
        else:
            raise RuntimeError(
                f"live review kernel has invalid disposition for {lens_id}")
    return {"citation": citation, "lenses": lenses,
            "not_applicable": not_applicable, "error": None}


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
    slot = tp.task_slot()
    active_path = tp.active_contract_path(ws, slot)
    # A native child may inherit a task slot before that slot is projected
    # into its checkout. Derive the byte-identical output contract in that
    # one missing-file case; a present but corrupt contract still raises.
    active = (tp.load_active(ws) or {}) if os.path.exists(active_path) else {}
    output_contract = active.get("output_contract")
    if not isinstance(output_contract, dict) or \
            output_contract.get("task") != str(task.get("id")):
        output_contract = evaluation_output.create_evaluator_contract(
            workspace=ws, task=str(task.get("id")), slot=slot,
            capability_snapshot=
            host_capabilities.dispatch_snapshot_from_environment(
                ws, host=tp.host(), environment=os.environ))
    out: dict = {"output_schema": output_contract["output_schema"],
                 "output_schema_id": output_contract["output_schema_id"],
                 "output_contract": output_contract,
                 "resume_identity": evaluation_output.resume_identity(
                     output_contract),
                 "max_attempts": output_contract["max_attempts"],
                 "task": task.get("id"),
                 "req": task.get("req") or state.get("requirement_id"),
                 "baseline": base,
                 "generated_by": "tp loop evidence",
                 "judgment_owed": list(EVIDENCE_JUDGMENT_KEYS)}

    # --- the suite. The execute/fix gate already paid for and bound this
    # result to the exact command/tree/engine/env key. Consume that record
    # directly: asking the cache again is needless work and, before T3, a
    # kernel-authored runtime file could even turn it into a hidden rerun.
    # An absent/stale record retains the old fail-safe cache/run path.
    tests = str(task.get("tests") or "").strip()
    if tests:
        env = {k: v for k, v in os.environ.items() if k != "TASKPLANE_TASK"}
        force_run = not tp.suite_cache_enabled()
        direct = ((state.get("_suite_evidence") or {}).get(
            str(task.get("id"))) or {})
        direct_key = tp._suite_cache_key(ws, tests, env)
        direct_valid = (
            direct.get("schema") == "taskplane.suite-evidence/v1"
            and direct.get("command") == tests
            and direct_key is not None
            and direct.get("key") == direct_key
            and isinstance(direct.get("returncode"), int)
        )
        if direct_valid and not force_run:
            out["suite"] = {
                "command": tests, "returncode": direct["returncode"],
                "cited": True, "tail": direct.get("tail"),
                "seconds_saved": direct.get("duration_s"),
                "source": "execute-gate",
            }
            tp.trace(ws, "suite_evidence_direct", command=tests,
                     key=direct_key, returncode=direct["returncode"],
                     produced_by=direct.get("source"))
        elif not force_run:
            hit = tp.suite_cache_lookup(ws, tests, env)
            if direct:
                tp.trace(ws, "suite_evidence_stale", command=tests,
                         recorded_key=direct.get("key"), current_key=direct_key)
        else:
            hit = None
        if not force_run and not direct_valid and hit is not None:
            out["suite"] = {"command": tests, "returncode": hit.get("returncode"),
                            "cited": True, "tail": hit.get("tail"),
                            "seconds_saved": hit.get("duration_s")}
            # Trace the citation exactly as dod_check does — a cited run is
            # evidence, and evidence absent from the audit log is not evidence.
            tp.trace(ws, "suite_cache_hit", command=str(tests),
                     key=hit.get("key"), returncode=hit.get("returncode"),
                     seconds_saved=hit.get("duration_s"),
                     produced_in=hit.get("produced_in"), via="evidence_bundle")
        elif force_run or not direct_valid:
            import time as _t
            t0 = _t.time()
            proc = tp.run_suite_command(ws, tests, env=env)
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

    # --- which lenses owe a verdict. Consume the one live ReviewKernel
    #     decision. Mapping here would create a second truth and can turn a
    #     terminal impact_incomplete/zero-slot decision into fresh work.
    try:
        obligations = _canonical_kernel_obligations(
            ws, loop.EVALUATE_ROUTE_STAGE)
        out["review_kernel"] = obligations["citation"]
        out["lenses"] = obligations["lenses"]
        out["lenses_not_applicable"] = obligations["not_applicable"]
        if obligations["error"]:
            out["lenses_error"] = obligations["error"]
    except Exception as e:
        out["lenses"] = []
        out["lenses_not_applicable"] = []
        out["lenses_error"] = (f"{e.__class__.__name__}: {e} — route the "
                               "ReviewKernel first; do not independently "
                               "remap or submit without its decision")

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

    out["verdict_template"] = _verdict_template(out)
    if write:
        path = os.path.join(ws, ".eval", "verdict.json")
        if os.path.exists(path):
            out["written"] = False
            out["write_note"] = (".eval/verdict.json already exists — left "
                                 "untouched; the engine never overwrites an "
                                 "authored judgment")
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tp.atomic_write_json(path, out["verdict_template"])
            out["written"] = True
    tp.trace(ws, "evidence_bundle", task=task.get("id"),
             suite_cited=bool((out.get("suite") or {}).get("cited")),
             criteria=len(out.get("criteria") or []),
             lenses=len(out.get("lenses") or []))
    return out
