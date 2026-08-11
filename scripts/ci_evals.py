#!/usr/bin/env python3
"""Evals — was the machinery USED? (WS-F)

    python3 scripts/ci_evals.py                    # score this workspace
    python3 scripts/ci_evals.py --corpus           # score the eval corpus
    python3 scripts/ci_evals.py --json

WHY THIS EXISTS. 1,736 tests, a cost meter, a yield meter and a graph-accuracy
meter all ask whether the machinery is CORRECT. None of them asks whether it
was USED, and that is the gap the product actually fell into. Every one of
these was a green engine and a broken product:

    "here we go again no inline dashboard visualisation. no report nothing?"
    "this is not the graph and dependency visualisation we designed"
    "again ignored graph design"
    "Skills agents and lenses are the most important part of this plugin"

In each case the engine rendered the artifact, wrote it to disk, pointed at
it in the payload, and told the assistant to show it. The unit suite could
not have caught any of them, because nothing was wrong with the unit under
test. Only an instrument that watches a REAL session can.

WHAT IS SCORED, AND FROM WHAT. The six areas of WS-F, split by one rule:
anything the engine can observe is scored as a FACT from its own records;
only what it cannot observe is scored from a CLAIM. The two never share a
column.

  1 artifact surfacing   CLAIM   obligations ledger: render_dashboard issued
                                 vs acknowledged. An unacknowledged
                                 obligation IS the "no dashboard" complaint,
                                 recorded.
  2 the product's graph  CLAIM   obligations ledger: render_graph, plus
                                 MISMATCHED acks — acknowledged while citing
                                 a fingerprint that is not the artifact the
                                 engine built. That is the "not the graph we
                                 designed" complaint, and it is a different
                                 failure from skipping.
  3 agent fan-out        FACT    tp.dispatch_report: expected briefs vs
                                 dispatches actually observed at the
                                 PreToolUse Task hook.
  4 skill-flow order     FACT    trace `loop_step`: the steps that ran, in
                                 the order they ran, against the engine's own
                                 state machine.
  5 gate discipline      FACT    trace: an approval that carried no human
                                 attribution (`loop_approve_unattributed`) is
                                 the assistant approving its own gate.
  6 cross-host parity    FACT    every ledger and trace row carries `host`.
                                 The same scenario on two hosts should
                                 produce the same governance decisions.

HONEST UNKNOWNS. An area with no evidence reports `no evidence` — never 0%,
which would slander a session that simply did not reach that step, and never
100%, which would flatter one. This is the same discipline the yield meter
uses for undispositioned findings, and it is the difference between an
instrument and a scoreboard.

AN ACKNOWLEDGEMENT IS A CLAIM. An assistant could acknowledge without
rendering. Claims and facts are reported separately for exactly that reason,
and the fingerprint check is what makes the claim hard to fake accidentally.
If deliberate false acks ever appear, the answer is host-transcript scoring;
this instrument is what would show that it is needed.

THIS GATES NOTHING. It prints numbers and exits 0 unless a corpus fixture is
malformed. Pin it later, on purpose, when there is a number worth defending.
"""
import io
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "evals")
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

AREAS = ("artifact_surfacing", "product_graph", "agent_fanout",
         "skill_flow", "gate_discipline", "cross_host")

# The engine's own step machine. Imported rather than copied: a second list
# of steps would be free to disagree with the loop, which is the drift shape
# this codebase already carries elsewhere.
def _known_steps():
    import loop
    return set(loop.STEP_ROLE) | set(loop.HUMAN_STEPS)


def _rows(path):
    out = []
    try:
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    out.append({"event": "unparseable"})
    except OSError:
        pass
    return out


def _pct(n, d):
    return None if not d else n / d


def score(trace_rows, ledger_rows, dispatch) -> dict:
    """Score one session. Pure: takes records, returns numbers.

    Pure on purpose — it is what lets the corpus below prove the scorer
    without a host, a workspace, or a running loop.
    """
    res = {}

    # --- 1 & 2: CLAIMS. issued vs acknowledged, per render kind.
    issued = {r["id"]: r for r in ledger_rows
              if r.get("event") == "issued" and r.get("id")}
    acks = {}
    for r in ledger_rows:
        if r.get("event") == "acknowledged" and r.get("id"):
            acks.setdefault(r["id"], r)
    for area, kind in (("artifact_surfacing", "render_dashboard"),
                       ("product_graph", "render_graph")):
        mine = [o for o in issued.values() if o.get("kind") == kind]
        met = shown_other = 0
        for o in mine:
            ack = acks.get(o["id"])
            if ack is None:
                continue
            want, got = o.get("fingerprint"), ack.get("fingerprint")
            if want and got and want != got:
                shown_other += 1      # a SUBSTITUTE, not a skip
            else:
                met += 1
        res[area] = {
            "source": "claim", "issued": len(mine), "acknowledged": met,
            "substituted": shown_other,
            "skipped": len(mine) - met - shown_other,
            "rate": _pct(met, len(mine)),
        }

    # --- 3: FACT. briefs the engine emitted vs dispatches the hook saw.
    exp = int((dispatch or {}).get("expected") or 0)
    unobserved = int((dispatch or {}).get("unobserved") or 0)
    hook_active = bool((dispatch or {}).get("hook_active"))
    res["agent_fanout"] = {
        "source": "fact", "expected": exp, "unobserved": unobserved,
        "dispatched": max(0, exp - unobserved),
        "hook_active": hook_active,
        # Without the hook the engine sees expectations and no dispatches at
        # all, which is indistinguishable from a run that dispatched nothing.
        # Report it as unknown rather than as total failure.
        "rate": _pct(max(0, exp - unobserved), exp) if hook_active else None,
        "note": None if hook_active else
        "no dispatches observed — the PreToolUse Task hook was not active, "
        "so fan-out is UNKNOWN for this session, not zero",
    }

    # --- 4: FACT. the steps that ran, against the engine's own machine.
    steps = [r.get("step") for r in trace_rows
             if r.get("event") == "loop_step" and r.get("step")]
    known = _known_steps()
    unknown_steps = sorted({s for s in steps if s not in known})
    res["skill_flow"] = {
        "source": "fact", "steps_run": len(steps),
        "distinct": sorted(set(steps)), "unrecognised": unknown_steps,
        "rate": _pct(len(steps) - len(unknown_steps), len(steps)),
    }

    # --- 5: FACT. an approval with no human behind it.
    approvals = sum(1 for r in trace_rows if r.get("event") == "loop_approve")
    unattributed = sum(1 for r in trace_rows
                       if r.get("event") == "loop_approve_unattributed")
    res["gate_discipline"] = {
        "source": "fact", "approvals": approvals,
        "unattributed": unattributed,
        "rate": _pct(approvals - unattributed, approvals),
    }

    # --- 6: FACT. same scenario, two hosts, same decisions?
    hosts = sorted({r.get("host") for r in ledger_rows + trace_rows
                    if r.get("host")})
    by_host = {}
    for h in hosts:
        by_host[h] = sorted({r.get("kind") for r in ledger_rows
                             if r.get("host") == h and r.get("kind")})
    agree = len(hosts) > 1 and len({tuple(v) for v in by_host.values()}) == 1
    res["cross_host"] = {
        "source": "fact", "hosts": hosts, "obligations_by_host": by_host,
        "rate": (1.0 if agree else 0.0) if len(hosts) > 1 else None,
        "note": None if len(hosts) > 1 else
        "one host in this record — parity is UNKNOWN until the same "
        "scenario runs on another",
    }
    return res


def _fmt(v):
    return "no evidence" if v is None else f"{v:>4.0%}"


def report(name, res) -> None:
    print(f"  {name}")
    for area in AREAS:
        r = res[area]
        line = f"    {area:<20} {r['source']:<5} {_fmt(r['rate'])}"
        if area in ("artifact_surfacing", "product_graph"):
            line += (f"   ({r['acknowledged']}/{r['issued']} shown"
                     f", {r['skipped']} skipped"
                     f", {r['substituted']} substituted)")
        elif area == "agent_fanout":
            line += f"   ({r['dispatched']}/{r['expected']} dispatched)"
        elif area == "skill_flow":
            line += f"   ({r['steps_run']} steps, {len(r['distinct'])} distinct)"
        elif area == "gate_discipline":
            line += (f"   ({r['approvals']} approvals, "
                     f"{r['unattributed']} unattributed)")
        elif area == "cross_host":
            line += f"   ({', '.join(r['hosts']) or 'none'})"
        print(line)
        if r.get("note"):
            print(f"      note: {r['note']}")
    print()


def _score_corpus() -> int:
    if not os.path.isdir(CORPUS):
        print(f"evals: no corpus at {CORPUS}", file=sys.stderr)
        return 1
    profiles = sorted(d for d in os.listdir(CORPUS)
                      if os.path.isdir(os.path.join(CORPUS, d)))
    if not profiles:
        print("evals: corpus is empty", file=sys.stderr)
        return 1
    print("evals — the scorer against sessions whose answer is known\n")
    bad = 0
    for name in profiles:
        d = os.path.join(CORPUS, name)
        exp_path = os.path.join(d, "expected.json")
        with io.open(exp_path, encoding="utf-8") as f:
            expected = json.load(f)
        res = score(_rows(os.path.join(d, "trace.jsonl")),
                    _rows(os.path.join(d, "obligations.jsonl")),
                    json.load(io.open(os.path.join(d, "dispatch.json"),
                                      encoding="utf-8")))
        report(name, res)
        print(f"    why: {expected['why']}\n")
        for area, want in (expected.get("rates") or {}).items():
            got = res[area]["rate"]
            if got != want:
                bad += 1
                print(f"    MISMATCH {area}: scorer says {got!r}, "
                      f"fixture expects {want!r}", file=sys.stderr)
    if bad:
        print(f"evals: {bad} corpus expectation(s) not met", file=sys.stderr)
        return 1
    print("  The corpus proves the SCORER. Real sessions are what it is for.")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--corpus" in argv:
        return _score_corpus()
    import taskplane_lite as tp
    import obligations
    ws = os.path.abspath(os.environ.get("TASKPLANE_WORKSPACE") or ".")
    trace_rows = []
    for p in tp.trace_paths(ws):
        trace_rows += _rows(p)
    res = score(trace_rows, obligations.read(ws), tp.dispatch_report(ws))
    if "--json" in argv:
        print(json.dumps(res, indent=2, default=str))
        return 0
    print("evals — was the machinery USED?\n")
    report(os.path.basename(ws) or ws, res)
    print("  Claims and facts are separate columns on purpose: an "
          "acknowledgement\n  says the artifact was shown, it does not "
          "prove it. This gates nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
