#!/usr/bin/env python3
"""Per-task cost ratchet (P3, R-0012) — the guard the month-1 loop lacked.

WHY THIS EXISTS. Between mid-July and v3 phase 3 the loop's per-task cost
grew roughly thirteenfold, and no single change caused it. Four independent
growths multiplied: the suite grew about five times, the time to run it once
grew about five times, suite executions per agent run grew about fifteen
times, and agent runs per task grew about three and a half times. Every one
of those was a defensible local decision. Nothing measured the product.

Governance was the only part of this system with no budget. So the fix that
outlives today's optimizations is a NUMBER: run a complete miniature loop
end to end, count what the engine MANDATES per task, and fail when it grows.

WHAT IS COUNTED, AND WHY THESE. Each is a mandated obligation the engine
imposes, not a property of any agent's cleverness — so the pins cannot be
gamed by a chattier or quieter agent:

  suite_executions   how many times the engine actually runs the DoD test
                     command for one task. The cache makes identical content
                     cost one execution; this pin is what keeps it that way.
  engine_entrypoints how many separate CLI/engine calls a worker must make
                     to satisfy one task's gates. This is the number that
                     turns into tool calls, and tool calls are the wall
                     clock (about eighteen seconds each, measured).
  gates              how many gate transitions one task passes through.

REVIEW COST (lenses 2.0, R-0012). Per-task cost was only half the picture:
the review fan-out is now the largest variable cost in a gate, and it grew
when 26 lens routing surfaces were rewritten. The pins below measure the
ROUTING SURFACE over a frozen corpus of change shapes, which is what widens
when someone edits globs or task types.

  review_mean_fired  mean lenses firing (deep + light) per change shape
  review_max_fired   the worst single shape
  review_deep_cap_ok every shape stays within the router's own deep cap

HONEST LIMIT, stated so nobody over-reads these numbers. The corpus paths are
representative, not real files in this repo, so content and graph signals
cannot score against them — only path and task-type routing does. That makes
the deep counts here much lower than on real diffs (about 1 versus about 5
measured over 25 real changes at the time of writing). It is deliberately the
right trade: the pin is hermetic and stable, and it moves precisely when the
routing surface widens, which is the drift it exists to catch. It is NOT a
prediction of what a real review costs.

RAISING A PIN IS ALLOWED — DELIBERATELY. Add a real obligation and this
script fails; edit the number and say why in the commit. That is the whole
point: the cost becomes a decision someone makes on the record, instead of
a thing that happens to everybody.
"""
import json
import os
import subprocess
import sys
import tempfile

# Console codepages are not always UTF-8 (Windows defaults to cp1252, a C
# locale gives ASCII), and this script's own output carries arrows and em
# dashes. The text is ours and it is UTF-8; say so rather than dying in the
# middle of a report.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "taskplane"))

# Pinned against the P1+P2 loop. Every raise is a recorded decision.
PINS = {
    "suite_executions": 1,
    "engine_entrypoints": 10,
    "gates": 4,
    # Review routing surface. Measured 7.55 / 19 at the lenses 2.0 landing;
    # pinned just above so ordinary noise does not fail CI but a real
    # widening does.
    "review_mean_fired": 8.0,
    "review_max_fired": 20,
}


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _fixture(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "plan"))
    os.makedirs(os.path.join(ws, "src", "todo"))
    os.makedirs(os.path.join(ws, "specs"))
    open(os.path.join(ws, "src", "todo", "a.py"), "w").write("x = 1\n")
    open(os.path.join(ws, "specs", "spec.md"), "w").write("# spec\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "init")
    json.dump({"tasks": [{"id": "t1", "scope": ["src/todo/**"],
                          "tests": "true",
                          "criteria": ["complete() marks done"]}]},
              open(os.path.join(ws, "plan", "tasks.json"), "w"))
    return ws


def measure() -> dict:
    """Drive one task through a full loop and count what the engine made it do."""
    import loop
    import taskplane_lite as tp

    tmp = tempfile.mkdtemp()
    os.environ["TASKPLANE_HOME"] = os.path.join(tmp, "store")
    os.environ.pop("TASKPLANE_NO_SUITE_CACHE", None)
    ws = _fixture(tmp)

    calls = {"n": 0}

    def counted(fn):
        def wrapper(*a, **kw):
            calls["n"] += 1
            return fn(*a, **kw)
        return wrapper

    gates = 0
    loop.init(ws, "g", spec_path="specs/spec.md")
    for step in ("plan",):
        loop.next_action(ws); calls["n"] += 1
        loop.gate(ws, "pass"); calls["n"] += 1; gates += 1
    loop.approve(ws, "plan"); calls["n"] += 1
    loop.next_action(ws); calls["n"] += 1                       # execute brief
    open(os.path.join(ws, "src", "todo", "a.py"), "a").write("y = 2\n")
    loop.submit(ws, "pass"); calls["n"] += 1
    loop.gate(ws, "pass"); calls["n"] += 1; gates += 1
    loop.next_action(ws); calls["n"] += 1                       # evaluate brief

    bundle = loop.evidence(ws); calls["n"] += 1
    for row in bundle.get("criteria") or []:
        row["status"] = "met"
        row["evidence"] = "covered by the task's tests"
    for row in bundle.get("lenses") or []:
        row["verdict"] = "pass"
        row["blockers"] = 0
    if bundle.get("graph"):
        for row in bundle["graph"]["dispositions"]:
            row["status"] = "tested"
            row["evidence"] = "covered by declared task tests"
        bundle["graph"]["requirements_checked"] = \
            bundle["graph"].pop("requirements_to_check")
        bundle["graph"]["contracts_checked"] = \
            bundle["graph"].pop("contracts_to_verify")
    bundle["verdict"] = "pass"
    os.makedirs(os.path.join(ws, ".eval"), exist_ok=True)
    with open(os.path.join(ws, ".eval", "verdict.json"), "w") as f:
        json.dump(bundle, f)
    loop.submit(ws, "pass"); calls["n"] += 1
    loop.gate(ws, "pass"); calls["n"] += 1; gates += 1

    runs = hits = 0
    trace_path = os.path.join(tp.tp_dir(ws), "trace.jsonl")
    try:
        with open(trace_path) as f:
            for line in f:
                if not line.strip():
                    continue
                e = json.loads(line)
                if e.get("event") == "suite_run":
                    runs += 1
                elif e.get("event") == "suite_cache_hit":
                    hits += 1
    except OSError:
        pass

    return {"suite_executions": runs, "suite_citations": hits,
            "engine_entrypoints": calls["n"], "gates": gates + 1}



def measure_review():
    """Route a frozen corpus of change shapes and count the lenses that fire.

    Uses the real router (lens_signals.route_verdicts) rather than
    re-implementing glob matching, so the pin tracks whatever the router
    actually does — including any future change to scoring or budgeting.
    """
    import statistics
    import lens_signals as LS

    corpus_path = os.path.join(HERE, "scripts", "review_corpus.json")
    corpus = json.load(open(corpus_path))
    stage = corpus.get("_stage") or "build"

    fired, deep_counts, over_cap = [], [], []
    for change in corpus["changes"]:
        vmap = LS.route_verdicts(HERE, change["files"], stage=stage)
        verdicts = [(v.get("verdict") if isinstance(v, dict) else v)
                    for v in vmap.values()]
        d = verdicts.count("deep")
        n_fired = d + verdicts.count("light")
        fired.append(n_fired)
        deep_counts.append(d)
        if d > LS.DEEP_CAP:
            over_cap.append((change["name"], d))

    return {
        "review_mean_fired": round(statistics.mean(fired), 2),
        "review_max_fired": max(fired),
        "review_mean_deep": round(statistics.mean(deep_counts), 2),
        "review_over_cap": over_cap,
        "review_shapes": len(fired),
    }

def main() -> int:
    got = measure()
    got.update(measure_review())
    failures = []
    for key, pin in sorted(PINS.items()):
        value = got.get(key)
        flag = "ok" if value is not None and value <= pin else "OVER"
        print(f"{key:<20} {value}  (pin {pin})  {flag}")
        if value is None or value > pin:
            failures.append(f"{key}: {value} exceeds the pinned {pin}")
    print(f"{'suite_citations':<20} {got.get('suite_citations')}  "
          "(informational — executions avoided by citing identical content)")
    print(f"{'review_mean_deep':<20} {got.get('review_mean_deep')}  "
          f"(informational — routing surface only, over "
          f"{got.get('review_shapes')} frozen change shapes)")
    # An invariant, not a ratchet: the router's own budget must hold. If a
    # shape ever exceeds the deep cap the budget has stopped being applied,
    # which is a bug in the router rather than a cost decision.
    if got.get("review_over_cap"):
        for name, d in got["review_over_cap"]:
            failures.append(
                f"review deep cap breached on '{name}': {d} deep lenses — "
                "the router's budget is not being applied")
    if failures:
        print()
        print("per-task cost ratchet FAILED:")
        for f in failures:
            print("  - " + f)
        print()
        print("This is not automatically a bug. It means this change added a "
              "mandated per-task obligation, or widened the review routing "
              "surface. Either remove one, or raise the pin in "
              "scripts/ci_loop_cost.py and say why in the commit — so the "
              "cost is a decision on the record rather than drift.")
        return 1
    print("ok: per-task cost holds at or under every pin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
