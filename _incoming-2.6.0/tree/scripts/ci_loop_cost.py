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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "taskplane"))

# Pinned against the P1+P2 loop. Every raise is a recorded decision.
PINS = {
    "suite_executions": 1,
    "engine_entrypoints": 10,
    "gates": 4,
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


def main() -> int:
    got = measure()
    failures = []
    for key, pin in sorted(PINS.items()):
        value = got.get(key)
        flag = "ok" if value is not None and value <= pin else "OVER"
        print(f"{key:<20} {value}  (pin {pin})  {flag}")
        if value is None or value > pin:
            failures.append(f"{key}: {value} exceeds the pinned {pin}")
    print(f"{'suite_citations':<20} {got.get('suite_citations')}  "
          "(informational — executions avoided by citing identical content)")
    if failures:
        print()
        print("per-task cost ratchet FAILED:")
        for f in failures:
            print("  - " + f)
        print()
        print("This is not automatically a bug. It means this change added a "
              "mandated per-task obligation. Either remove one, or raise the "
              "pin in scripts/ci_loop_cost.py and say why in the commit — so "
              "the cost is a decision on the record rather than drift.")
        return 1
    print("ok: per-task cost holds at or under every pin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
