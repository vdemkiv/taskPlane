"""Mission-control dashboard — a live visual of the governed loop.

Renders the current loop state to a self-contained HTML page so the human
SEES what taskplane is doing: which step we're on, which agents are running
under which contracts, what the hook just blocked, and where a human gate
is waiting. Regenerated at every transition (the driver re-renders after
each `loop next`/`gate`/`approve`), so delivering it repeatedly gives a
live-updating control tower. Pure stdlib; reads only committed/runtime
state — no tokens.
"""

from __future__ import annotations

import json
import os

import taskplane_lite as tp
import loop as _loop        # engine owns the state machine; the view derives
import kb as _kb            # from its public read models (display_pipeline,
import depgraph as _dg      # STEP_ROLE, kb.counts, depgraph.summary) instead
                            # of re-encoding schemas that then drift.

# trace event → (icon, label, css class)
EVENT_STYLE = {
    "loop_init": ("🚀", "loop started", "info"),
    "project_init": ("📁", "project set up", "info"),
    "contract_activated": ("🔒", "contract active", "lock"),
    "loop_step": ("▶", "step", "info"),
    "hook_deny": ("⛔", "BLOCKED", "deny"),
    "budget_deny": ("⛔", "BUDGET STOP", "deny"),
    "loop_gate": ("•", "gate", "info"),
    "loop_approve": ("✅", "human approved", "human"),
    "loop_wave": ("🌊", "wave dispatched", "wave"),
    "loop_claim": ("🤝", "agent claimed task", "wave"),
    "kb_recall": ("🧠", "recalled decisions", "kb"),
    "decision_recorded": ("🧠", "decision recorded", "kb"),
    "requirement_recorded": ("📋", "requirement", "kb"),
    "lens_route": ("🔍", "lenses routed", "lens"),
    "graph_impact": ("💥", "impact computed", "lens"),
    "graph_scan": ("🕸", "graph scanned", "lens"),
    "refinement_gate": ("📊", "refinement scored", "kb"),
    "loop_retro": ("🔁", "retrospective", "human"),
    "loop_resolve": ("⚖", "human resolved", "human"),
    "debt_recorded": ("📌", "debt tracked", "kb"),
    "trace_rotated": ("🗂", "trace rotated", "info"),
}

# Role label per step — sourced from the engine (loop.STEP_ROLE), not a
# second hand-maintained copy.
STEP_ROLE_LABEL = _loop.STEP_ROLE

# trace.jsonl is the AUDIT RECORD — the dashboard never rotates, truncates
# or deletes it. Past this size the renderer TAIL-READS (parses only the
# last TRACE_TAIL_BYTES) and says so with a visible "showing recent events"
# notice; the full history stays on disk, untouched.
TRACE_TAIL_BYTES = 2 * 1024 * 1024


def _read_trace_all(ws: str, stats: dict | None = None) -> list:
    """Parse the main trace + every parallel worker trace ONCE, returning the
    full CHRONOLOGICAL event list. Render entry points call this a single
    time per invocation and slice/reuse the parsed list — the trace is not
    re-parsed 4-5x per transition.

    Robustness is centralized HERE so no consumer can crash the dashboard:
      - a line that is not valid JSON (truncated worker write) is skipped
        and COUNTED (stats["unparseable"]) — surfaced, never silent;
      - a valid-JSON dict missing the "event" key (malformed/legacy record)
        renders as a DEGRADED-BUT-VISIBLE row (event "(unrecorded)"),
        counted in stats["degraded"];
      - a file larger than TRACE_TAIL_BYTES is tail-read (stats["tail"]),
        with the skipped byte count in stats["tail_skipped_bytes"] — the
        trace itself is the audit record and is never rotated or deleted.
    `stats` (optional dict) is filled in place for the caller's notice."""
    if stats is None:
        stats = {}
    stats.setdefault("unparseable", 0)
    stats.setdefault("degraded", 0)
    stats.setdefault("tail", False)
    stats.setdefault("tail_skipped_bytes", 0)
    paths = [os.path.join(tp.tp_dir(ws), "trace.jsonl")]
    workroot = os.path.join(ws, ".tp-work")
    if os.path.isdir(workroot):
        for d in sorted(os.listdir(workroot)):
            wp = os.path.join(workroot, d, ".taskplane", "trace.jsonl")
            if os.path.exists(wp):
                paths.append(wp)
    evts = []
    for p in paths:
        if not os.path.exists(p):
            continue
        tag = os.path.basename(os.path.dirname(os.path.dirname(p)))
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        with open(p, "rb") as f:
            if size > TRACE_TAIL_BYTES:
                f.seek(size - TRACE_TAIL_BYTES)
                dropped = f.readline()       # partial first line after seek
                stats["tail"] = True
                stats["tail_skipped_bytes"] += (size - TRACE_TAIL_BYTES
                                                + len(dropped))
            for raw in f:
                ln = raw.decode("utf-8", "replace")
                if not ln.strip():
                    continue
                try:
                    e = json.loads(ln)
                except ValueError:
                    stats["unparseable"] += 1
                    continue   # a truncated/partial record — skip it, don't
                               # crash the whole render; COUNTED for the
                               # visible notice, never silently dropped
                if not isinstance(e, dict):
                    stats["unparseable"] += 1
                    continue
                if "event" not in e:
                    # valid JSON minus the event field — render it as a
                    # degraded-but-visible row instead of hiding it
                    e = dict(e)
                    e["event"] = "(unrecorded)"
                    stats["degraded"] += 1
                if tag != ".taskplane":
                    e["_agent"] = tag
                evts.append(e)
    evts.sort(key=lambda e: e.get("ts", 0))
    return evts


def _trace_notice(stats: dict | None) -> str:
    """Visible disclosure when the trace view is partial: N unparseable
    lines skipped, M malformed records shown degraded, and/or tail-read of
    an oversized audit log. Empty string when the view is complete."""
    stats = stats or {}
    bits = []
    if stats.get("unparseable"):
        bits.append(_msg("trace_unparseable", n=stats["unparseable"]))
    if stats.get("degraded"):
        bits.append(_msg("trace_degraded", n=stats["degraded"]))
    if stats.get("tail"):
        mb = stats.get("tail_skipped_bytes", 0) / (1024.0 * 1024.0)
        bits.append(_msg("trace_tail", mb=f"{mb:.1f}"))
    if not bits:
        return ""
    return (f'<div id="tp-trace-notice" role="note" style="border:1px solid '
            f'var(--border-strong);border-radius:6px;padding:7px 12px;'
            f'margin-bottom:12px;font-family:var(--font-mono);'
            f'font-size:11px;color:var(--text-secondary)">⚠ '
            + _esc(" · ".join(bits)) + '</div>')


def _read_trace(ws: str, limit: int = 24) -> list:
    """Newest-first tail of the merged trace (main + workers). Kept for
    callers that need a one-off slice; render paths use _read_trace_all once
    and slice the parsed list themselves."""
    return _read_trace_all(ws)[-limit:][::-1]


# ------------------------------------------------------- message catalog
# One assembly point for every counted / composed phrase (i18n readiness):
# each entry is a FULL template with named placeholders and ICU-shaped
# plural selection, so extraction hands a translator whole sentences whose
# segment order lives in the template — never in code flow. English-only
# for now; the call sites survive extraction unchanged.

import re as _re

_PLURAL_RE = _re.compile(
    r"\{(\w+),\s*plural,\s*one\s*\{(.*?)\}\s*other\s*\{(.*?)\}\}")

_MESSAGES = {
    "n_lenses": "{n} {n, plural, one {lens} other {lenses}}",
    "n_findings": "{n} {n, plural, one {finding} other {findings}}",
    "n_agents": "{verb} {n} {n, plural, one {agent} other {agents}}",
    "n_tasks_planned": "{n} {n, plural, one {task} other {tasks}} planned",
    "n_warnings": " · {n} {n, plural, one {warning} other {warnings}}",
    "no_ceiling": "{n} {n, plural, one {action} other {actions}} · "
                  "no ceiling set",
    "dependent_modules": "{n} dependent {n, plural, one {module} other "
                         "{modules}} within 3 hops",
    "plan_all_tasks": "execution plan — all {n} {n, plural, one {task} "
                      "other {tasks}}, for review",
    "tasks_progress": "{done}/{total} {total, plural, one {task} other "
                      "{tasks}} passed · {actions} actions metered · "
                      "{blocked} blocked",
    "design_dod_fail": "Design DoD ❌ {n} {n, plural, one {issue} other "
                       "{issues}}: {details}",
    "signoff_dod_fail": "all tasks reviewed · DoD ❌ {n} {n, plural, "
                        "one {issue} other {issues}}: {details}",
    # never-skippable headlines — one full template each; optional segments
    # are named placeholders rendered from their own catalog entries (or "")
    "headline_findings": "{title}: {high} high · {med} med · {low} low "
                         "({total} {total, plural, one {finding} other "
                         "{findings}}){unrated}{notes}{tests}{coverage}"
                         "{impact}{rec}",
    "headline_findings_unrated": " · {n} unrated → counted high",
    "headline_findings_notes": " · {n} {n, plural, one {note} other "
                               "{notes}} (question/praise, not defects)",
    "headline_findings_tests": " · {tests}",
    "headline_findings_coverage": " · lenses {deep} deep/{sweep} sweep "
                                  "of {total}",
    # v2 coverage honesty (route v2): every catalog lens has an evidenced
    # disposition — deep, light, or n/a-with-negative-evidence. Format
    # PINNED by test_dashboard_coverage_v2.
    "headline_findings_coverage_v2": " · lenses {deep} deep · {light} "
                                     "light · {na} n/a (evidenced) "
                                     "of {total}",
    "headline_findings_impact": " · touches {n} {n, plural, one {module} "
                                "other {modules}}",
    "headline_findings_rec": " · {rec}",
    "headline_loop": "taskplane loop: step={step} · tasks {done}/{total} · "
                     "\"{goal}\"{gate}{budget}",
    "headline_loop_gate": " — YOUR GATE: approve/sign-off",
    "headline_loop_budget": " — ACTION BUDGET EXHAUSTED ({used}/{max}): "
                            "the agent is blocked; grant more actions",
    "dor_warnings": "{n} {n, plural, one {warning} other {warnings}}: "
                    "{details}",
    # trace-view disclosure (the trace is the audit record — a partial view
    # must SAY it is partial, and why)
    "trace_unparseable": "{n} unparseable trace {n, plural, one {line} "
                         "other {lines}} skipped",
    "trace_degraded": "{n} malformed trace {n, plural, one {record} other "
                      "{records}} shown degraded",
    "trace_tail": "showing recent events only — {mb} MB of older trace "
                  "not shown (full history preserved in trace.jsonl)",
}


def _msg(key: str, **kw) -> str:
    t = _MESSAGES[key]

    def _pl(m):
        return m.group(2) if kw.get(m.group(1)) == 1 else m.group(3)

    return _PLURAL_RE.sub(_pl, t).format(**kw)


# Directional glyphs go through ONE helper so an RTL locale can flip flow
# arrows in a single place instead of a whole-file rewrite.
def _arrow(back: bool = False) -> str:
    return "←" if back else "→"


def _fmt_ts(ts) -> str:
    """Timestamps render as ISO-8601 UTC with the offset explicit ('Z') —
    never the server's local wall clock in a bare 24h pattern, which reads
    as the VIEWER's time with no marker. One helper so a future client-side
    Intl formatting swap is one change."""
    import time as _t
    try:
        return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(float(ts)))
    except (TypeError, ValueError, OSError, OverflowError):
        return str(ts)


def _load_loop(ws: str) -> dict | None:
    # Delegate to the engine's own loader (handles current + legacy state
    # paths) rather than re-deriving them here.
    return _loop.load(ws)


def _counts(ws: str) -> dict:
    # Consume each owner's public read model — no raw index.json / graph.json
    # key access in the view.
    c = _kb.counts(ws)
    g = _dg.summary(ws)
    return {"decisions": c["decisions"], "requirements": c["requirements"],
            "debt": c["debt_open"], "modules": g["modules"],
            "edges": g["edges"]}


def _render_pipeline(state, step) -> str:
    """The full-page pipeline strip. Each node carries a visually-hidden
    state word (done/current/pending, + human gate), so step state is not
    conveyed by dot color alone."""
    main = [(s, lbl, h) for s, lbl, h in _loop.display_pipeline(state)
            if s != "fix"]
    order = [s[0] for s in main]
    cur_i = order.index(step) if step in order else -1
    pipe_html = []
    for i, (sid, label, gate) in enumerate(main):
        cls = "done" if (cur_i >= 0 and i < cur_i) else \
              ("cur" if i == cur_i else "todo")
        if gate:
            cls += " gate"
        wait = " · waiting on you" if (i == cur_i and gate) else ""
        st_word = ("done" if cls.startswith("done")
                   else "current step" if i == cur_i else "pending")
        if gate:
            st_word += " · human gate"
        sr = f'<span class="sr"> — {st_word}</span>'
        pipe_html.append(
            f'<div class="node {cls}"><span class="dot"></span>'
            f'<span class="nl">{label}{wait}{sr}</span></div>')
        if i < len(main) - 1:
            pipe_html.append('<div class="conn"></div>')
    if step == "fix":
        pipe_html.append('<div class="fixflag">↻ FIX cycle in progress</div>')
    return "".join(pipe_html)


def render(ws: str, out: str | None = None) -> str:
    tstats = {}
    all_ev = _read_trace_all(ws, stats=tstats)   # trace parsed ONCE/render
    state = _load_loop(ws)
    trace = all_ev[-24:][::-1]
    counts = _counts(ws)
    contract = tp.load_active(ws)

    step = (state or {}).get("step", "—")
    goal = (state or {}).get("goal", "no active loop")
    tasks = (state or {}).get("tasks") or []
    parallel = bool((state or {}).get("parallel"))
    denials = sum(1 for e in all_ev if e["event"] == "hook_deny")

    # pipeline: mark done / current / gate-waiting. Derived from the engine's
    # single source (loop.display_pipeline) — fix is a side-loop, hidden here.
    pipe = _render_pipeline(state, step)

    # agents/contract panel
    agent_cards = []
    if parallel and step in ("execute",):
        for t in tasks:
            stt = t.get("status", "pending")
            badge = {"running": "running", "built": "built",
                     "passed": "passed", "pending": "queued",
                     "failed": "failed"}.get(stt, stt)
            wt = t.get("workspace", "")
            wt = ".tp-work/" + wt.split(".tp-work/")[-1] if ".tp-work/" in wt \
                else ("—" if not wt else wt)
            scope = _esc(", ".join(t.get("scope", [])))
            agent_cards.append(
                f'<div class="agent {stt}"><div class="ah">'
                f'<b>{_esc(t.get("id","?"))}</b><span class="badge {stt}">'
                f'{_esc(badge)}</span></div><div class="ameta">tp-executor · '
                f'scope <code>{scope}</code></div>'
                f'<div class="ameta">worktree <code>{_esc(wt)}</code></div></div>')
    elif contract:
        ro = contract.get("read_only")
        sc = contract["coding"]["scope_paths"] or (
            contract.get("write_allow") if ro else ["(any — set scope!)"])
        agent_cards.append(
            f'<div class="agent running"><div class="ah">'
            f'<b>{STEP_ROLE_LABEL.get(step, step)}</b>'
            f'<span class="badge running">active</span></div>'
            f'<div class="ameta">{"read-only review" if ro else "build"} '
            f'contract {_esc(contract.get("task_id",""))}</div>'
            f'<div class="ameta">scope <code>{_esc(", ".join(sc))}</code></div>'
            f'<div class="ameta">deny <code>'
            f'{_esc(", ".join(contract["coding"]["command_policy"]["deny"][:3]))}…'
            f'</code></div></div>')
    else:
        awaiting = {"design_approval": "Review the design, then approve.",
                    "plan_approval": "Review the plan, then approve.",
                    "signoff": "Review the EM report, then sign off.",
                    "done": "Loop complete.", "escalated": "Resolve to continue."}
        agent_cards.append(
            f'<div class="agent idle"><div class="ah"><b>no active contract'
            f'</b><span class="badge idle">'
            f'{"human gate" if step in awaiting else "idle"}</span></div>'
            f'<div class="ameta">{awaiting.get(step, "workspace ungoverned")}'
            f'</div></div>')

    # task roster (always, compact)
    roster = ""
    if tasks:
        rows = "".join(
            f'<tr><td>{_esc(t.get("id"))}</td>'
            f'<td><span class="badge {_esc(t.get("status","pending"))}">'
            f'{_esc(t.get("status","pending"))}</span></td>'
            f'<td>{int(t.get("fix_cycles",0) or 0)}</td></tr>' for t in tasks)
        roster = (f'<table class="roster"><tr><th>task</th><th>status</th>'
                  f'<th>fix</th></tr>{rows}</table>')

    # live feed
    feed = []
    for e in trace:
        icon, label, cls = EVENT_STYLE.get(
            e["event"], ("·", e["event"], "info"))
        extra = ""
        if e["event"] == "loop_step":
            extra = f' {_arrow()} {e.get("step","")} ({e.get("role","")})'
        elif e["event"] == "hook_deny":
            who = f'[{e["_agent"]}] ' if e.get("_agent") else ""
            extra = f' {who}{e.get("tool","")}: {str(e.get("reason",""))[:50]}'
        elif e["event"] == "loop_gate":
            extra = f' {e.get("step","")} = {e.get("outcome","")}'
        elif e["event"] == "lens_route":
            extra = " " + _msg("n_lenses", n=len(e.get("lenses", [])))
        elif e["event"] == "loop_wave":
            extra = f' ready: {", ".join(e.get("ready", []))}'
        elif e["event"] == "refinement_gate":
            extra = f' {e.get("task","")} score {e.get("score","")}'
        elif e["event"] == "graph_impact":
            extra = f' {e.get("impacted",0)} modules'
        feed.append(f'<li class="ev {cls}"><span class="ei">{icon}</span>'
                    f'<span class="et">{_esc(label)}</span>'
                    f'<span class="ex">{_esc(extra)}</span></li>')
    feed_html = "".join(feed) or '<li class="ev info">no events yet</li>'

    stat = lambda v, l: (f'<div class="stat"><b>{v}</b><span>{l}</span></div>')
    stats = (stat(counts["modules"], "graph modules")
             + stat(counts["edges"], "edges")
             + stat(counts["requirements"], "requirements")
             + stat(counts["decisions"], "KB decisions")
             + stat(counts["debt"], "open debt")
             + stat(f'<span class="{"hot" if denials else ""}">{denials}</span>',
                    "hook blocks"))

    html = _TEMPLATE.replace("__GOAL__", _esc(goal[:80])) \
        .replace("__STEP__", _esc(step)) \
        .replace("__MODE__", "parallel waves" if parallel else "serial") \
        .replace("__NOTICE__", _trace_notice(tstats)) \
        .replace("__PIPE__", pipe) \
        .replace("__AGENTS__", "".join(agent_cards)) \
        .replace("__ROSTER__", roster) \
        .replace("__FEED__", feed_html) \
        .replace("__STATS__", stats)
    out = out or os.path.join(tp.tp_dir(ws), "dashboard.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(html)
    return out


_TEMPLATE = """<!DOCTYPE html><html lang="en" dir="auto"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>taskplane — mission control</title><style>
*{box-sizing:border-box;margin:0}
body{font:13.5px/1.5 -apple-system,'Segoe UI',Inter,sans-serif;
background:#14140f;color:#e8e8e2;padding:18px 22px}
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;
overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
h1{font-size:15px;letter-spacing:.3px;color:#fff;display:flex;
align-items:center;gap:10px}
h1 .live{width:8px;height:8px;border-radius:50%;background:#1baf7a;
animation:pulse 1.4s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.goal{color:#a9a9a2;font-size:13px;margin:3px 0 14px}
.goal b{color:#eda100}
/* pipeline */
.pipe{display:flex;align-items:center;flex-wrap:wrap;gap:2px;
background:#1c1c15;border:1px solid #33332a;border-radius:12px;
padding:14px 16px;margin-bottom:14px}
.node{display:flex;align-items:center;gap:7px;padding:6px 10px;
border-radius:8px}
.node .dot{width:11px;height:11px;border-radius:50%;background:#44443a;flex:none}
.node .nl{font-size:12.5px;color:#8a8a80}
.node.done .dot{background:#1baf7a}.node.done .nl{color:#c9c9c2}
.node.cur{background:#26261c}.node.cur .dot{background:#eda100;
animation:pulse 1.2s infinite;box-shadow:0 0 0 4px rgba(237,161,0,.15)}
.node.cur .nl{color:#fff;font-weight:600}
.node.gate .dot{border-radius:3px}
.node.gate.cur{background:#2a1f14}.node.gate.cur .dot{background:#e34948;
box-shadow:0 0 0 4px rgba(227,73,72,.2)}
.node.gate.cur .nl{color:#ff9d6e}
.conn{flex:1;min-width:10px;height:2px;background:#33332a}
.fixflag{color:#eb6834;font-size:12px;font-weight:600;
margin-inline-start:10px;padding:5px 10px;background:#2a1a12;border-radius:7px}
/* grid */
.grid{display:grid;grid-template-columns:1.1fr 1fr;gap:14px}
@media (max-width:560px){.grid{grid-template-columns:1fr}}
.card{background:#1c1c15;border:1px solid #33332a;border-radius:6px;padding:14px}
.card h2{font-size:11px;letter-spacing:.8px;
color:#94948a;margin-bottom:11px}
/* agents */
.agent{border:1px solid #33332a;border-radius:9px;padding:10px 12px;
margin-bottom:9px;border-inline-start:4px solid #44443a}
.agent.running{border-inline-start-color:#1baf7a}
.agent.built{border-inline-start-color:#eda100}
.agent.passed{border-inline-start-color:#1baf7a}
.agent.failed{border-inline-start-color:#e34948}
.agent.idle{border-inline-start-color:#44443a}
.ah{display:flex;justify-content:space-between;align-items:center;
margin-bottom:5px}.ah b{color:#fff;font-size:13.5px}
.ameta{color:#9a9a90;font-size:11.5px;margin-top:2px}
.ameta code{background:#26261c;color:#c9c9a2;padding:1px 5px;border-radius:4px;
font-size:11px}
.badge{font-size:10.5px;padding:2px 8px;border-radius:99px;font-weight:600;
letter-spacing:.4px}
.badge.running{background:#123a2b;color:#3fd99a}
.badge.built,.badge.pending,.badge.queued{background:#3a2f12;color:#f0c04a}
.badge.passed{background:#123a2b;color:#3fd99a}
.badge.failed{background:#3a1616;color:#ff7a78}
.badge.idle{background:#26261c;color:#9a9a90}
.roster{width:100%;border-collapse:collapse;margin-top:10px;font-size:12px}
.roster th{text-align:start;color:#94948a;font-weight:600;padding:4px 8px;
border-bottom:1px solid #33332a}
.roster td{padding:5px 8px;border-bottom:1px solid #26261c}
/* feed */
.feed{list-style:none;max-height:340px;overflow:auto}
.ev{display:flex;align-items:baseline;gap:8px;padding:5px 4px;
border-bottom:1px solid #22221b;font-size:12.5px}
.ev .ei{flex:none;width:18px;text-align:center}
.ev .et{color:#c9c9c2;font-weight:500}.ev .ex{color:#8a8a80;font-size:11.5px}
.ev.deny{background:#2a1414;border-radius:6px}.ev.deny .et{color:#ff7a78}
.ev.human .et{color:#7fd0ff}.ev.wave .et{color:#6fd9a8}
.ev.kb .et{color:#e0b84a}.ev.lens .et{color:#b79ce9}.ev.lock .et{color:#f0c04a}
/* stats */
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.stat{background:#1c1c15;border:1px solid #33332a;border-radius:10px;
padding:9px 16px;text-align:center;min-width:96px}
.stat b{display:block;font-size:20px;color:#fff}
.stat span{font-size:11px;color:#8a8a80}
.stat .hot{color:#ff7a78}
.legend{color:#94948a;font-size:11px;margin-top:12px}
</style></head><body>
<h1><span class="live"></span> taskplane — mission control
<span style="color:#77776c;font-weight:400;font-size:12px">· step
<b style="color:#eda100">__STEP__</b> · __MODE__</span></h1>
<div class="goal">goal: <b>__GOAL__</b></div>
__NOTICE__
<div class="pipe">__PIPE__</div>
<div class="grid">
 <div class="card"><h2>Agents &amp; contracts</h2>__AGENTS____ROSTER__</div>
 <div class="card"><h2>Live feed (newest first)</h2>
  <ul class="feed" aria-live="polite" aria-atomic="false">__FEED__</ul></div>
</div>
<div class="stats">__STATS__</div>
<div class="legend">green = passed/running · amber = current step / built ·
red square = human gate waiting · ⛔ = the hook blocked an out-of-contract
action (the product working). Regenerated at every loop transition.</div>
</body></html>"""


# ---------------------------------------------------------------- widget
# Native inline visualization fragment for mcp__visualize__show_widget.
# Cowork design system: CSS variables (auto light/dark), Tabler outline
# icons, sendPrompt() gate buttons. No outer background, no titles inside.

_ICON = {
    "loop_init": ("ti-rocket", "s"), "project_init": ("ti-folder", "s"),
    "contract_activated": ("ti-lock", "w"), "loop_step": ("ti-player-play", "a"),
    "hook_deny": ("ti-ban", "d"), "budget_deny": ("ti-gauge", "d"),
    "loop_gate": ("ti-point", "s"),
    "loop_approve": ("ti-check", "g"), "loop_wave": ("ti-arrows-split", "g"),
    "loop_claim": ("ti-hand-grab", "g"), "kb_recall": ("ti-brain", "w"),
    "decision_recorded": ("ti-brain", "w"),
    "requirement_recorded": ("ti-clipboard-text", "w"),
    "lens_route": ("ti-search", "a"), "graph_impact": ("ti-affiliate", "a"),
    "graph_scan": ("ti-topology-star", "a"),
    "refinement_gate": ("ti-chart-dots", "a"),
    "loop_retro": ("ti-refresh", "g"), "loop_resolve": ("ti-scale", "g"),
    "debt_recorded": ("ti-bookmark", "w"),
}
# MONOCHROME design language: grayscale foundation, typography-led
# hierarchy (mono-font micro labels, oversized numerals, hairlines over
# fills), inverted blocks for the human gate + current stage, and exactly
# ONE signal color — danger red, reserved for blocked/failed. Everything
# uses CSS variables, so it inverts cleanly in dark mode.
_ICOLOR = {"a": "var(--text-secondary)", "d": "var(--text-danger)",
           "g": "var(--text-secondary)", "w": "var(--text-secondary)",
           "s": "var(--text-muted)"}
# badge: (bg, fg, label) — outlined mono pills; red only for failed
_BADGE = {
    "running": ("var(--surface-0)", "var(--text-primary)", "running"),
    "passed": ("none", "var(--text-secondary)", "✓ passed"),
    "built": ("var(--surface-0)", "var(--text-secondary)", "built"),
    "pending": ("none", "var(--text-muted)", "queued"),
    "failed": ("var(--bg-danger)", "var(--text-danger)", "failed"),
    "skipped": ("none", "var(--text-muted)", "skipped"),
}
# micro label: the mono lowercase letterspaced card header
_MICRO = ('font-family:var(--font-mono);font-size:10.5px;letter-spacing:'
          '1.2px;color:var(--text-muted)')
_CARD = ('background:none;border:1px solid var(--border);'
         'border-radius:6px;padding:14px')
# Keyboard equivalent for role=button divs/spans: Enter or Space fires the
# element's own onclick, so journey steps and spine nodes are reachable
# without a mouse (WCAG 2.1 keyboard-operable).
_KEYCLICK = ("if(event.key==='Enter'||event.key===' '){"
             "event.preventDefault();this.click()}")


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _attr(s: str) -> str:
    # escape for a quoted HTML attribute (NOT a JS-string context — see
    # _jsattr). HTML-entity encoding is correct for plain attribute values.
    return _esc(s).replace('"', "&quot;").replace("'", "&#39;")


def _jsattr(s: str) -> str:
    """Escape a value destined for a SINGLE-QUOTED JS string that sits inside
    a DOUBLE-QUOTED HTML on* attribute (e.g. onclick="tpFire(this,'<here>')").

    HTML-entity escaping alone is NOT safe here: the HTML parser decodes
    entities in an attribute value BEFORE the inline-handler JS is compiled,
    so `&#39;` becomes a real `'` and breaks out of the JS string (the v0.9.5
    XSS regression). We must BACKSLASH-escape the JS metacharacters first (a
    backslash survives HTML decoding and reaches the JS engine), THEN
    HTML-escape the attribute/markup delimiters. Order matters."""
    s = (str(s).replace("\\", "\\\\").replace("'", "\\'")
         .replace("\n", "\\n").replace("\r", "\\r"))
    return (s.replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------- north-star review note

_ALIGN = {  # alignment verdict -> (label, dot color, accent border)
    "on-course": ("on course", "var(--text-success,var(--text-primary))",
                  "var(--border-strong)"),
    "drift": ("drift", "var(--text-warning,var(--text-primary))",
              "var(--border-strong)"),
    "off-course": ("off course", "var(--text-danger)", "var(--border-danger)"),
}
_REC = {  # recommendation -> accent
    "proceed": "var(--text-success,var(--text-primary))",
    "proceed-with-eyes-open": "var(--text-warning,var(--text-primary))",
    "reconsider": "var(--text-danger)",
}


def render_strategy_note(note, out=None):
    """Render the on-demand NORTH-STAR REVIEW — an alignment verdict vs the
    project's north star, then the strategic lenses (Leverage, Reversibility,
    Opportunity cost, Coherence), the single sharpest tension, and a
    recommendation. ADVISORY: it informs the human; it is never a gate.
    Returns the HTML fragment (also writes it if `out` is set).

    note = {target, north_star, alignment:{verdict, note},
            lenses:[{name, read, note}], tension, recommendation, rationale}
    """
    n = note or {}
    target = _esc(n.get("target", "(unspecified)"))
    ns = n.get("north_star")
    al = n.get("alignment") or {}
    verdict = str(al.get("verdict", "")).lower().replace(" ", "-")
    alabel, adot, aborder = _ALIGN.get(
        verdict, ("unrated", "var(--text-muted)", "var(--border)"))
    rec = str(n.get("recommendation", "")).lower().replace(" ", "-")
    rcol = _REC.get(rec, "var(--text-muted)")

    ns_line = (f'<span style="{_MICRO}">vs north star</span> '
               f'<span style="font-size:12.5px;color:var(--text-secondary)">'
               f'{_esc(ns)}</span>' if ns else
               f'<span style="{_MICRO}">no north star set — add a '
               f'"Direction / north star:" line to context/product.md</span>')

    rows = []
    for ln in (n.get("lenses") or []):
        rows.append(
            f'<div style="display:flex;gap:10px;padding:7px 0;border-top:1px '
            f'solid var(--border)"><span style="font-family:var(--font-mono);'
            f'font-size:12px;min-width:104px;color:var(--text-primary)">'
            f'{_esc(ln.get("name",""))}</span>'
            f'<span style="font-family:var(--font-mono);font-size:11px;'
            f'min-width:52px;color:var(--text-secondary)">'
            f'{_esc(ln.get("read",""))}</span>'
            f'<span style="font-size:12.5px;color:var(--text-secondary);'
            f'line-height:1.5;flex:1">{_esc(ln.get("note",""))}</span></div>')

    tension = n.get("tension")
    tension_html = (
        f'<div style="margin-top:10px;font-size:12.5px;color:var(--text-'
        f'secondary)"><span style="{_MICRO}">sharpest tension</span><br>'
        f'{_esc(tension)}</div>' if tension else "")
    rationale = (f'<div style="font-size:12.5px;color:var(--text-secondary);'
                 f'line-height:1.55;margin-top:3px">{_esc(n.get("rationale"))}'
                 f'</div>' if n.get("rationale") else "")

    frag = (
        f'<h2 class="sr-only">North-star review of {target}: alignment '
        f'{_esc(alabel)}; recommendation {_esc(rec or "none")}.</h2>'
        f'<div style="padding:0.5rem 0;font-family:var(--font-sans);'
        f'color:var(--text-primary)">'
        f'<div style="display:flex;justify-content:space-between;align-items:'
        f'flex-start;gap:12px;margin-bottom:4px"><div>'
        f'<div style="font-size:16px;font-weight:500">North-star review</div>'
        f'<div style="font-size:13px;color:var(--text-secondary)">{target}</div>'
        f'</div><span style="border:1px solid {aborder};border-radius:20px;'
        f'padding:3px 12px;font-family:var(--font-mono);font-size:11px;'
        f'white-space:nowrap;color:{adot}">● {_esc(alabel)}</span></div>'
        f'<div style="margin:8px 0 4px">{ns_line}</div>'
        f'<div style="{_CARD};margin-top:10px">'
        f'<div style="font-size:13px;color:var(--text-secondary);'
        f'line-height:1.6">{_esc(al.get("note",""))}</div>'
        f'{"".join(rows)}{tension_html}</div>'
        f'<div style="{_CARD};margin-top:8px;border-inline-start:3px solid {rcol};'
        f'border-radius:0 6px 6px 0"><span style="{_MICRO}">recommendation'
        f'</span> <span style="font-weight:500;font-size:13.5px;color:{rcol}">'
        f'{_esc(rec or "—")}</span>{rationale}</div>'
        f'<div style="{_MICRO};margin-top:10px">advisory — the north-star '
        f'review informs your call; it never gates the loop</div></div>')

    if out:
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(frag)
        except OSError:
            pass
    return frag


# ------------------------------------------------------- findings dashboard

_SEV = {  # order high→low; each: (rank, label, dot-color, accent-border)
    "blocker": (0, "blocker", "var(--text-danger)", "var(--border-danger)"),
    "high": (1, "high", "var(--text-danger)", "var(--border-danger)"),
    "major": (2, "major", "var(--text-primary)", "var(--border-strong)"),
    "med": (2, "medium", "var(--text-primary)", "var(--border-strong)"),
    "medium": (2, "medium", "var(--text-primary)", "var(--border-strong)"),
    "minor": (3, "minor", "var(--text-muted)", "var(--border)"),
    "low": (3, "low", "var(--text-muted)", "var(--border)"),
    "question": (4, "question", "var(--text-muted)", "var(--border)"),
    "praise": (4, "praise", "var(--text-muted)", "var(--border)"),
}
# Both findings schemas are first-class: the dispatched-agent schema
# (high|med|low) AND the lens-charter verdict schema (blocker|major|minor|
# question|praise). question/praise are NOT defects — they bucket to "info"
# and are excluded from defect counts (but still rendered and counted in
# the headline as notes, so nothing is silently dropped).
#
# v2.3.1: bucket classification no longer has its own map here (a former
# _SEV_KEY duplicated — and drifted from — loop.normalize_severity, e.g.
# mapping 'major' to 'med' while the gate blocks it as 'high'). _sev_info()
# now sources the bucket from loop.normalize_severity directly; _SEV above
# only supplies rank/label/dot/accent for known lens-charter words.


def _sev_info(sev):
    """(bucket, rank, label, dot, accent, flagged) for a severity value.
    GUARDRAIL: severity rendering never downgrades — an UNKNOWN severity
    renders in the HIGH bucket, flagged as unrated, never as medium.

    v2.3.1: the BUCKET (high/med/low/info) is sourced from
    loop.normalize_severity — the same function the sign-off gate uses to
    decide what blocks — never from a second, dashboard-local mapping. A
    local map can drift from the engine (it had: 'major' -> med here vs.
    'major' -> high at the gate), which would let a gate-blocking finding
    render in a lesser bucket than the one that actually blocks. Only the
    richer display LABEL (and rank/dot/accent for known lens-charter
    words) still come from the local table."""
    s = str(sev).lower()
    bucket = _loop.normalize_severity(sev)
    if s in _SEV:
        rank, label, dot, accent = _SEV[s]
        return bucket, rank, label, dot, accent, False
    return (bucket, 1, f"{s or 'unrated'} ⚠ unrated → {bucket}",
            "var(--text-danger)", "var(--border-danger)", True)


def _alias(f):
    """Bridge the lens-charter verdict fields (issue/why/suggestion) to the
    renderer's fields (title/scenario/fix) so charter-schema findings render
    with their content instead of empty cards."""
    if not isinstance(f, dict):
        return f
    out = dict(f)
    if not out.get("title") and out.get("issue"):
        out["title"] = out["issue"]
    if not out.get("scenario") and out.get("why"):
        out["scenario"] = out["why"]
    if not out.get("fix") and out.get("suggestion"):
        out["fix"] = out["suggestion"]
    return out


# Shared gate-button JS: feature-detect the chat bridge FIRST. In the
# standalone HTML artifact (tp dashboard --out / Codex fallback) there is no
# window.sendPrompt — a click must never pretend success; instead the exact
# reply to type in chat is revealed next to the button.
_SEND_JS = (
    'function tpHint(b,m){if(b._tph)return;b._tph=1;'
    'var d=document.createElement("div");'
    'd.style.cssText="margin-top:8px;padding:6px 10px;border-radius:6px;'
    'background:var(--surface-0);color:var(--text-primary);'
    'font-family:var(--font-mono);font-size:11.5px;flex-basis:100%";'
    'd.setAttribute("role","note");'
    'd.textContent="no chat bridge in this static view — reply in chat: "+m;'
    'b.parentNode.appendChild(d);}'
    'function tpSend(b,m){if(window.sendPrompt){sendPrompt(m);}'
    'else{tpHint(b,m);}}')

# Render-reliability contract (v1.5.3): a dashboard's data is too valuable to
# depend on a single big widget that might get skipped. Three guarantees:
#  1. headline_findings() — a plain-text one-liner of the key numbers that is
#     ALWAYS printed to chat, so decision data survives even a skipped render.
#  2. render_findings_paged() — splits a large fragment into ordered,
#     self-contained pages each under PAGE_BUDGET, rendered one after another.
#  3. the skills instruct the driver to render EVERY page and never summarize.
PAGE_BUDGET = 14000     # max UTF-8 BYTES per inline fragment (ENFORCED in
                        # v2.3.0: a page over budget is a bug — split
                        # further; content only ever leaves a page via an
                        # explicit '+N more' marker, never silently)


def headline_findings(findings, meta=None) -> str:
    """The never-skippable text line. Key counts + tests + recommendation, so
    the numbers reach the human even if every widget render is skipped.
    Composed from ONE full template ("headline_findings") whose optional
    segments are named placeholders — no code-flow concatenation. Additive
    only: question/praise entries move out of the defect counts into an
    explicit notes segment; unrated severities are counted HIGH and named."""
    meta = meta or {}
    c = {"high": 0, "med": 0, "low": 0, "info": 0}
    unrated = 0
    for f in findings or []:
        bucket, _, _, _, _, flagged = _sev_info(f.get("severity", "med"))
        c[bucket] += 1
        if flagged:
            unrated += 1
    cov_seg = ""
    cov_map = _effective_coverage(meta)
    if cov_map is not None:
        cov = lens_coverage(cov_map)
        if cov.get("v2"):
            # v2 coverage honesty: every lens dispositioned with evidence —
            # the headline says so (format pinned by test).
            cov_seg = _msg("headline_findings_coverage_v2",
                           deep=cov["deep"], light=cov["light"],
                           na=cov["na"], total=cov["total"])
        else:
            cov_seg = _msg("headline_findings_coverage", deep=cov["deep"],
                           sweep=cov["sweep"], total=cov["total"])
    rec = meta.get("headline") or meta.get("recommendation")
    return _msg(
        "headline_findings",
        title=meta.get("title") or "review findings",
        high=c["high"], med=c["med"], low=c["low"],
        total=c["high"] + c["med"] + c["low"],
        unrated=_msg("headline_findings_unrated", n=unrated)
        if unrated else "",
        notes=_msg("headline_findings_notes", n=c["info"])
        if c["info"] else "",
        tests=_msg("headline_findings_tests", tests=meta["tests"])
        if meta.get("tests") else "",
        coverage=cov_seg,
        impact=_msg("headline_findings_impact",
                    n=meta["impact"].get("total_impacted", 0))
        if meta.get("impact") else "",
        rec=_msg("headline_findings_rec", rec=rec) if rec else "")


def headline_northstar(note) -> str:
    """Never-skippable text line for the north-star review (v1.5.4): the
    alignment verdict + recommendation + sharpest tension, so the strategic
    call lands even if the widget render is skipped."""
    note = note or {}
    al = (note.get("alignment") or {}).get("verdict", "—")
    rec = note.get("recommendation", "—")
    t = note.get("target", "target")
    tail = f" · tension: {note['tension']}" if note.get("tension") else ""
    return f"north-star ({t}): {al} → {rec}{tail}"


def _ptitle(t):
    return (f'<div style="{_MICRO};margin-bottom:8px">{_esc(t)}</div>')


def _compact_card(f, open_=True):
    f = _alias(f)
    _, _, slabel, dot, accent, _ = _sev_info(f.get("severity", "med"))
    loc = ""
    if f.get("file"):
        ln = f":{f['line']}" if f.get("line") not in (None, "") else ""
        loc = (f'<span style="font-family:var(--font-mono);font-size:10px;'
               f'color:var(--text-muted)"> · {_esc(f["file"])}{_esc(ln)}</span>')
    dom = (f'<span style="font-family:var(--font-mono);font-size:9.5px;'
           f'color:var(--text-muted);min-width:80px">{_esc(f.get("domain",""))}'
           f'</span>')
    det = ""
    if open_ and (f.get("scenario") or f.get("fix")):
        parts = []
        # truncate the RAW text, then escape — a slice after escaping can
        # bisect an HTML entity (a dangling '&am') and eat far more visible
        # characters than intended on entity-heavy text.
        if f.get("scenario"):
            parts.append(f'<b style="color:var(--text-primary);'
                         f'font-weight:500">fail</b> '
                         f'{_esc(str(f["scenario"])[:260])}')
        if f.get("fix"):
            parts.append(f'<b style="color:var(--text-primary);'
                         f'font-weight:500">fix</b> '
                         f'{_esc(str(f["fix"])[:220])}')
        det = (f'<div style="padding:3px 0 2px;padding-inline-start:88px;'
               f'font-size:11.5px;'
               f'color:var(--text-secondary);line-height:1.5">'
               + "<br>".join(parts) + "</div>")
    return (f'<div style="border-top:.5px solid var(--border)">'
            f'<div style="padding:5px 0;font-size:12.5px;display:flex;gap:8px">'
            f'{dom}<span style="flex:1">{_esc(f.get("title",""))}'
            f'<span style="font-family:var(--font-mono);font-size:9.5px;'
            f'color:{dot}"> {_esc(slabel)}</span>{loc}</span></div>{det}</div>')


# Reserved headroom per page for the wrapper (outer div + sr heading +
# part title) so the greedy packer's guarantee covers the ASSEMBLED page,
# not just the sum of its rows.
_PAGE_RESERVE = 500


def _truncate_marked(html, budget):
    """Last-resort fit: cut at the last complete block boundary and append
    an EXPLICIT '+N more' marker. Over-budget content is never dropped
    silently — the marker names exactly how much was omitted and where the
    complete view lives."""
    if len(html) <= budget:
        return html

    def _marker(omitted):
        return (f'<div style="font-family:var(--font-mono);font-size:11px;'
                f'color:var(--text-danger);padding:6px 0">… +{omitted} more '
                f'characters truncated to honor the page budget — open '
                f'.taskplane/dashboard.html for the complete view</div>')

    kept = html
    for _ in range(64):
        m = _marker(len(html) - len(kept))
        if len(kept) + len(m) <= budget:
            return kept + m
        cut = kept.rfind("</div>", 0, max(0, budget - len(m)))
        kept = kept[:cut + 6] if cut > 0 else kept[:max(0, budget - len(m))]
    m = _marker(len(html) - len(kept))
    return kept[:max(0, budget - len(m))] + m


def render_findings_paged(findings, meta=None, budget=PAGE_BUDGET):
    """Ordered, self-contained fragments each <= budget INCLUDING the page
    wrapper. If the full rich fragment already fits, returns it as a single
    page (no behavior change for small reviews). Otherwise splits by
    MEANING: summary → high cards → medium → low → notes (question/praise).
    Each page carries a 'part i/n' title. Returns [{"title","html"}]."""
    meta = meta or {}
    full = render_findings(findings, meta)
    if len(full) <= budget:
        return [{"title": meta.get("title", "review findings"), "html": full}]

    norm = []
    for f in findings or []:
        f = _alias(f)
        k, _, _, _, _, _ = _sev_info(f.get("severity", "med"))
        norm.append({**f, "_key": k})
    buckets = {k: [f for f in norm if f["_key"] == k]
               for k in ("high", "med", "low", "info")}
    c = {k: len(v) for k, v in buckets.items()}
    pages = []

    # page 1 — summary: title, counts, tests, clean, gate
    chip_defs = [
        ("high", "high", "var(--bg-danger)", "var(--text-danger)"),
        ("med", "med", "var(--bg-warning,var(--surface-1))",
         "var(--text-warning,var(--text-primary))"),
        ("low", "low", "var(--surface-1)", "var(--text-secondary)")]
    if c["info"]:
        chip_defs.append(("info", "notes", "var(--surface-1)",
                          "var(--text-muted)"))
    chips = "".join(
        f'<span style="font-family:var(--font-mono);font-size:11px;'
        f'padding:2px 9px;border-radius:12px;margin-inline-end:6px;'
        f'background:{bg};color:{fg}">{lbl} {c[k]}</span>'
        for k, lbl, bg, fg in chip_defs)
    clean = meta.get("clean") or []
    clean_html = ""
    if clean:
        clean_html = ('<div style="margin-top:10px;font-size:12px;'
                      'color:var(--text-secondary)"><b style="font-weight:500">'
                      f'clean — {len(clean)} areas:</b> '
                      + "; ".join(_esc(x) for x in clean[:12]) + "</div>")
    rec = meta.get("headline") or meta.get("recommendation") or ""
    rec_html = (f'<div style="border-inline-start:3px solid '
                f'var(--border-danger);'
                f'padding:8px 12px;margin-top:12px;background:var(--surface-1);'
                f'border-radius:0 8px 8px 0;font-size:12.5px">{_esc(rec)}</div>'
                if rec else "")
    sub = _esc(meta.get("subtitle", ""))
    tests = (f' · {_esc(meta["tests"])}' if meta.get("tests") else "")
    summary_h2 = (
        f'<h2 class="sr-only">Review findings summary: {c["high"]} high, '
        f'{c["med"]} medium, {c["low"]} low'
        + (f', {c["info"]} notes' if c["info"] else "") + '.</h2>')
    summary_core = (
        f'<div style="font-size:16px;'
        f'font-weight:500">{_esc(meta.get("title","review findings"))}</div>'
        f'<div style="font-size:12px;color:var(--text-secondary);'
        f'margin-bottom:10px">{sub}{tests}</div>{chips}{clean_html}{rec_html}')

    def _wrap_summary(body):
        return (summary_h2 + '<div style="padding:.5rem 0;font-family:'
                'var(--font-sans);color:var(--text-primary)">' + body
                + '</div>')

    # v2.3.1 (H3&H4): a PAGED review must never make the human's primary
    # action (the sign-off gate) unreachable, nor drop lens coverage / the
    # blast-radius graph / the trailing note — render_findings_paged used
    # to render NONE of these on any page. gate_html is the sign-off
    # buttons; it (plus the note) is a FIXED tail — never truncated away —
    # exactly as widget_paged protects its trailing <script> (finding #1):
    # the truncatable part is only the summary's title/chips/clean/rec.
    gate_html = _gate_box(meta)
    _cov_map = _effective_coverage(meta)  # routing_decision wins over legacy
    coverage_html = (render_lens_coverage(_cov_map)
                      if _cov_map is not None else "")
    graph_html = (render_review_graph(meta["ws"], meta.get("impact"))
                  if meta.get("ws") else "")
    note_html = (f'<div style="{_MICRO};margin-top:10px">'
                 f'{_esc(meta["note"])}</div>' if meta.get("note") else "")
    # the gate buttons call tpSend(...) — wire it on this page (paged
    # fragments are otherwise self-contained and never include it).
    gate_js = f'<script>{_SEND_JS}</script>' if gate_html else ""

    fixed_tail = gate_html + note_html + gate_js
    fixed_bytes = (_page_bytes(_wrap_summary("")) + _page_bytes(fixed_tail))
    fitted_core = _fit_page(summary_core, max(256, budget - fixed_bytes))

    extras = coverage_html + graph_html
    extras_fit_on_summary = False
    if extras:
        with_extras = _wrap_summary(fitted_core + extras + fixed_tail)
        extras_fit_on_summary = _page_bytes(with_extras) <= budget
    summary_page = (with_extras if extras_fit_on_summary
                     else _wrap_summary(fitted_core + fixed_tail))
    pages.append({"title": "summary", "html": summary_page})
    if extras and not extras_fit_on_summary:
        # coverage + graph didn't fit alongside the gate on page 1 — they
        # still land on their OWN page rather than being dropped silently.
        pages.append({
            "title": "lens coverage & graph",
            # a LARGE v2 coverage map (26 evidenced dispositions) can
            # exceed the page budget on its own — the extras page honors
            # the SAME enforced byte budget as every other page (_fit_page:
            # over-budget content leaves only via an explicit marker).
            "html": _fit_page(
                '<h3 class="sr-only">Lens coverage and dependency '
                'graph.</h3><div style="padding:.5rem 0;'
                'font-family:var(--font-sans);'
                f'color:var(--text-primary)">{extras}</div>', budget)})

    def chunk(bucket, label, open_):
        effective = max(1000, budget - _PAGE_RESERVE)
        rows = []
        for f in bucket:
            r = _compact_card(f, open_)
            if len(r) > effective:   # one pathological card — marked, never
                r = _truncate_marked(r, effective)          # silently over
            rows.append(r)
        # greedily pack rows against budget MINUS the wrapper reserve, so
        # the assembled page (wrapper included) honors the guarantee
        cur, cur_len, packed = [], 0, []
        for r in rows:
            if cur and cur_len + len(r) > effective:
                packed.append(cur)
                cur, cur_len = [], 0
            cur.append(r)
            cur_len += len(r)
        if cur:
            packed.append(cur)
        for i, grp in enumerate(packed, 1):
            suffix = f" (part {i}/{len(packed)})" if len(packed) > 1 else ""
            html = (f'<h3 class="sr-only">{_esc(label)} findings'
                    f'{_esc(suffix)} — {len(bucket)} total.</h3>'
                    f'<div style="padding:.5rem 0;font-family:'
                    f'var(--font-sans);color:var(--text-primary)">'
                    + _ptitle(f"{label} · {len(bucket)}{suffix}")
                    + "".join(grp) + "</div>")
            pages.append({"title": f"{label}{suffix}",
                          "html": _truncate_marked(html, budget)})

    if buckets["high"]:
        chunk(buckets["high"], "high — fix first", True)
    if buckets["med"]:
        chunk(buckets["med"], "medium", False)
    if buckets["low"]:
        chunk(buckets["low"], "low", False)
    if buckets["info"]:
        chunk(buckets["info"], "notes", False)

    n = len(pages)
    for i, p in enumerate(pages, 1):
        p["title"] = f"{p['title']} — {i}/{n}"
        # ENFORCED byte budget: the guarantee covers the emitted UTF-8
        # size of the assembled page, wrapper included.
        p["html"] = _fit_page(p["html"], budget)
    return pages


def _catalog():
    """The lens catalog — the single source of truth for what lenses exist.
    Rendered dynamically so adding a lens to catalog.json appears in every
    dashboard with zero hand-maintenance (v1.5.4)."""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "lenses", "catalog.json")
    try:
        c = json.load(open(p))
        return c["lenses"] if isinstance(c, dict) else c
    except (OSError, ValueError):
        return []


# --------------------------------------------- coverage map (dual-shape)
# Two coverage shapes reach the dashboard (dual-shape, like the severity
# bridge precedent — the renderer accepts BOTH, never guesses):
#   legacy: {lens_id: "deep"|"sweep"}                       — unchanged
#   v2:     {lens_id: {"verdict": "deep"|"light"|"n/a", "score": float,
#                      "evidence": [..] | "negative_evidence": [..]}}
# The v2 shape is what lens.route v2 / dispatch_briefs' routing_decision
# emit — every catalog lens dispositioned, n/a WITH its negative evidence
# (coverage honesty: a skip must say why it was safe to skip).


def _cov_is_v2(routed) -> bool:
    """True when the coverage map carries v2 dict entries."""
    return bool(routed) and isinstance(routed, dict) and any(
        isinstance(v, dict) for v in routed.values())


def _effective_coverage(meta):
    """The coverage map a findings meta actually carries: the full
    routing_decision (richer, v2) wins over lens_coverage; None when the
    meta has neither (→ today's no-panel behavior)."""
    if meta.get("routing_decision") is not None:
        return meta["routing_decision"]
    return meta.get("lens_coverage")


def _v2_tier(entry) -> str:
    """Normalize one v2 map value to a tier: 'deep' | 'light' | 'n/a'.
    'deep (forced)' counts as deep; a stray legacy string in a v2 map maps
    'sweep' → light (fail toward MORE claimed coverage never happens here —
    unknown verdicts fall to n/a, the honest floor)."""
    v = entry.get("verdict") if isinstance(entry, dict) else entry
    v = str(v or "n/a")
    if v.startswith("deep"):
        return "deep"
    if v in ("light", "sweep"):
        return "light"
    return "n/a"


def _v2_reason(entry) -> str:
    """The human-readable evidence line for a v2 entry (joined)."""
    if not isinstance(entry, dict):
        return ""
    ev = entry.get("negative_evidence") or entry.get("evidence") or []
    if isinstance(ev, str):
        ev = [ev]
    return "; ".join(str(x) for x in ev)


def _lens_coverage_v2(routed):
    """v2 counterpart of lens_coverage: verdict counts + per-lens tier,
    score and evidence. Lenses ABSENT from the map count as skipped ('—'):
    absence carries no evidence, so it is never dressed up as an
    evidenced n/a."""
    cat = _catalog()
    groups = {}
    deep = light = na = skipped = 0
    for lz in cat:
        entry = routed.get(lz["id"])
        if entry is None:
            tier, reason, score = "—", "", None
            skipped += 1
        else:
            tier = _v2_tier(entry)
            reason = _v2_reason(entry)
            score = entry.get("score") if isinstance(entry, dict) else None
            if tier == "deep":
                deep += 1
            elif tier == "light":
                light += 1
            else:
                na += 1
        groups.setdefault(lz.get("group", "other"), []).append(
            {"id": lz["id"], "name": lz.get("name", lz["id"]), "tier": tier,
             "score": score, "reason": reason})
    return {"total": len(cat), "deep": deep, "light": light, "na": na,
            "sweep": 0, "skipped": skipped, "v2": True,
            "groups": [{"group": g, "lenses": v} for g, v in groups.items()]}


def lens_coverage(routed=None):
    """Coverage summary from the catalog + this run's routing. Dual-shape:
    legacy routed {lens_id: 'deep'|'sweep'} returns {total, deep, sweep,
    skipped, groups:[{group, lenses:[{id,name,tier}]}]} with tier
    'deep'|'sweep'|'—' (UNCHANGED); a v2 map returns the v2 summary
    (deep/light/na counts + per-lens score and evidence, v2: True)."""
    if _cov_is_v2(routed):
        return _lens_coverage_v2(routed)
    cat = _catalog()
    routed = routed or {}
    groups = {}
    deep = sweep = 0
    for lz in cat:
        tier = routed.get(lz["id"], "—")
        if tier == "deep":
            deep += 1
        elif tier == "sweep":
            sweep += 1
        groups.setdefault(lz.get("group", "other"), []).append(
            {"id": lz["id"], "name": lz.get("name", lz["id"]), "tier": tier})
    return {"total": len(cat), "deep": deep, "sweep": sweep,
            "skipped": len(cat) - deep - sweep,
            "groups": [{"group": g, "lenses": v} for g, v in groups.items()]}


def _render_lens_coverage_v2(routed):
    """v2 coverage panel: verdict chips (deep / light / n/a — the verdict
    WORD on every chip, never color-only) with the routing evidence on the
    title attr, and every n/a lens's negative-evidence reason ALSO inline
    (coverage honesty: the reason a lens did not run is part of the
    deliverable, not a tooltip-only nicety). Same style patterns as the
    legacy panel: inline HTML, existing CSS vars, collapsible details."""
    cov = _lens_coverage_v2(routed)
    _tier = {"deep": ("var(--text-danger)", "deep", "solid"),
             "light": ("var(--text-secondary)", "light", "solid"),
             "n/a": ("var(--text-muted)", "n/a", "dashed"),
             "—": ("var(--text-muted)", "—", "dashed")}
    rows = []
    for grp in cov["groups"]:
        chips = []
        reasons = []
        for l in grp["lenses"]:
            col, word, border = _tier[l["tier"]]
            tip = l["reason"]
            if l["score"] is not None:
                tip = f'score {l["score"]}' + (f' · {tip}' if tip else "")
            chips.append(
                f'<span title="{_attr(tip)}" style="display:inline-flex;'
                f'align-items:center;gap:5px;font-size:11.5px;'
                f'padding:2px 9px;border:1px {border} var(--border);'
                f'border-radius:12px;margin:0 5px 5px 0;color:{col}">'
                f'{_esc(l["name"])}'
                f'<span style="font-family:var(--font-mono);font-size:9px">'
                f'{word}</span></span>')
            if l["tier"] == "n/a":
                # _attr (not just _esc): quotes entity-encoded too, so an
                # attribute-breakout payload in evidence can't even APPEAR
                # as a raw `" on...=` byte sequence anywhere in the page
                # (display-equivalent — entities decode to the same text).
                reasons.append(
                    f'<div style="font-size:11px;'
                    f'color:var(--text-muted);padding:1px 0">○ '
                    f'{_esc(l["name"])} — n/a: '
                    f'{_attr(l["reason"] or "no evidence recorded")}</div>')
        rows.append(f'<div style="margin-top:8px"><div style="{_MICRO};'
                    f'margin-bottom:3px">{_esc(grp["group"])}</div>'
                    + "".join(chips) + "".join(reasons) + "</div>")
    summary = (f'{cov["total"]} lenses · {cov["deep"]} deep · '
               f'{cov["light"]} light · {cov["na"]} n/a (evidenced)')
    if cov["skipped"]:
        summary += f' · {cov["skipped"]} did not fire'
    return (f'<details style="margin-top:14px" id="tp-lens-coverage">'
            f'<summary style="cursor:pointer;{_MICRO}">LENS COVERAGE — '
            f'{summary}</summary><div style="margin-top:6px">'
            + "".join(rows) + '</div></details>')


def render_lens_coverage(routed=None):
    """Persistent lens-catalog panel: all N lenses grouped, each marked deep /
    sweep / didn't-fire for this run. Sourced from catalog.json so new lenses
    appear automatically (v1.5.4). Collapsible — coverage line always visible.
    Dual-shape (v3): a v2 coverage map ({id: {verdict, score, evidence |
    negative_evidence}}) renders verdict chips with per-n/a reasons; the
    legacy {id: 'deep'|'sweep'} shape renders byte-identically to before."""
    if _cov_is_v2(routed):
        return _render_lens_coverage_v2(routed)
    cov = lens_coverage(routed)
    _tier = {"deep": ("var(--text-danger)", "deep"),
             "sweep": ("var(--text-secondary)", "sweep"),
             "—": ("var(--text-muted)", "—")}
    rows = []
    for grp in cov["groups"]:
        chips = "".join(
            f'<span style="display:inline-flex;align-items:center;gap:5px;'
            f'font-size:11.5px;padding:2px 9px;border:1px solid var(--border);'
            f'border-radius:12px;margin:0 5px 5px 0;color:{_tier[l["tier"]][0]}">'
            f'{_esc(l["name"])}'
            + (f'<span style="font-family:var(--font-mono);font-size:9px">'
               f'{_tier[l["tier"]][1]}</span>' if routed else "")
            + '</span>'
            for l in grp["lenses"])
        rows.append(f'<div style="margin-top:8px"><div style="{_MICRO};'
                    f'margin-bottom:3px">{_esc(grp["group"])}</div>{chips}</div>')
    if routed:
        summary = (f'{cov["total"]} lenses · {cov["deep"]} deep · '
                   f'{cov["sweep"]} sweep · {cov["skipped"]} did not fire')
        label = "LENS COVERAGE"
    else:
        summary = f'{cov["total"]} lenses across {len(cov["groups"])} groups'
        label = "LENS CATALOG"
    return (f'<details style="margin-top:14px" id="tp-lens-coverage">'
            f'<summary style="cursor:pointer;{_MICRO}">{label} — '
            f'{summary}</summary><div style="margin-top:6px">'
            + "".join(rows) + '</div></details>')


def render_review_graph(ws, impact=None, tasks=None):
    """Blast-radius panel for the REVIEW findings dashboard (v1.5.4) — the
    review is where 'what does this change touch' matters most, yet the graph
    only lived on the loop dashboard before. Explains an empty graph instead
    of omitting it silently."""
    g = _dg.load(ws)
    have = bool(g.get("modules") or g.get("edges"))
    if not have:
        return ('<details style="margin-top:10px" id="tp-review-graph">'
                f'<summary style="cursor:'
                f'pointer;{_MICRO}">DEPENDENCY GRAPH — not scanned</summary>'
                '<div style="font-size:12px;color:var(--text-muted);'
                'margin-top:6px">No graph yet — run <code style="font-family:'
                'var(--font-mono)">tp graph scan</code>. Note: the scanner '
                'follows in-language imports; cross-service calls in a '
                'polyglot repo (e.g. a Node gateway calling Python services '
                'over HTTP) are not import edges, so the graph can look sparse '
                '— record those links with <code style="font-family:'
                'var(--font-mono)">tp graph edge</code> or in an ADR.</div>'
                '</details>')
    n_mod = len(g.get("modules", {}))
    n_edge = len(g.get("edges", []))
    line = f"{n_mod} modules · {n_edge} edges"
    body = ""
    if impact:
        tot = impact.get("total_impacted", 0)
        touched = ", ".join(_esc(m) for m in (impact.get("touched") or [])[:8])
        body = (f'<div style="font-size:12.5px;color:var(--text-secondary);'
                f'margin-top:6px"><b style="font-weight:500;color:'
                f'var(--text-primary)">{tot} modules impacted</b> by the '
                f'changed set{" — " + touched if touched else ""}</div>')
    if not body:
        body = ('<div style="font-size:12px;color:var(--text-muted);'
                'margin-top:6px">no change set to compute blast radius</div>')
    return (f'<details style="margin-top:10px" id="tp-review-graph"><summary '
            f'style="cursor:pointer;{_MICRO}">DEPENDENCY GRAPH — {line}'
            f'</summary>{body}</details>')


def _gate_box(meta):
    """The sign-off gate box: banner + buttons (tpSend), the human's PRIMARY
    ACTION at a review gate. Factored out (v2.3.1) so render_findings_paged
    can fold the SAME markup onto the summary page instead of dropping it —
    a paged review must never make the gate unreachable. Returns "" when
    meta['gate'] is falsy."""
    if not meta.get("gate"):
        return ""
    return (
        f'<div style="background:var(--text-primary);border-radius:6px;'
        f'padding:14px 16px;margin-top:14px;display:flex;'
        f'justify-content:space-between;align-items:center;gap:12px;'
        f'flex-wrap:wrap"><div style="font-weight:500;color:'
        f'var(--surface-2)"><i class="ti ti-writing-sign" '
        f'aria-hidden="true"></i> {_esc(meta.get("gate_title", "your call — the review is the deliverable"))}'
        f'</div><div style="display:flex;gap:8px;flex-wrap:wrap">'
        + "".join(
            f'<button onclick="tpSend(this,'
            f'&#39;{_jsattr(b["prompt"])}&#39;)" style="border:'
            f'{"none" if b.get("primary") else "1px solid var(--surface-2)"};'
            f'border-radius:6px;padding:9px 15px;font-size:13px;'
            f'font-weight:500;cursor:pointer;font-family:var(--font-sans);'
            f'background:{"var(--surface-2)" if b.get("primary") else "none"};'
            f'color:{"var(--text-primary)" if b.get("primary") else "var(--surface-2)"}">'
            f'{_esc(b["label"])}</button>'
            for b in meta.get("gate_buttons", []))
        + '</div></div>')


def render_findings(findings, meta=None, out=None):
    """Render a REVIEW findings dashboard — every severity, each finding an
    expandable card, filterable by severity. Independent of the loop (a pure
    review has no loop state), so tp-engineering can show ALL findings at the
    review gate. Returns the HTML fragment; also writes it if `out` is set.

    findings: [{severity, domain, file, line, title, scenario, fix, status,
                verdict}]  — only severity+title are required.
    meta: {title, subtitle, tests, clean:[...], note, gate:bool}
    """
    meta = meta or {}
    norm = []
    for f in findings or []:
        f = _alias(f)
        key, rank, _, _, _, _ = _sev_info(f.get("severity", "med"))
        norm.append({**f, "_key": key, "_rank": rank})
    norm.sort(key=lambda x: (x["_rank"], str(x.get("domain", "")),
                             str(x.get("file", ""))))
    counts = {k: sum(1 for f in norm if f["_key"] == k)
              for k in ("high", "med", "low", "info")}
    total = len(norm)

    # severity filter chips (all / high / med / low) — click filters via JS
    _chip_style = ('border:1px solid var(--border-strong);background:none;'
                   'border-radius:20px;padding:6px 14px;cursor:pointer;'
                   'font-family:var(--font-mono);font-size:12px;'
                   'display:inline-flex;align-items:center;gap:7px;'
                   'color:var(--text-secondary)')

    def chip(key, label, n, danger=False):
        col = "var(--text-danger)" if danger and n else "var(--text-primary)"
        return (
            f'<button type="button" class="tpf-chip" data-sev="{key}" '
            f'aria-pressed="false" aria-label="filter: {label} ({n})" '
            f"onclick=\"tpFilter('{key}')\" "
            f'style="{_chip_style}">'
            f'<span style="font-size:15px;font-weight:500;color:{col}">{n}'
            f'</span> {label}</button>')

    chips = (chip("all", "all", total)
             + chip("high", "high", counts["high"], danger=True)
             + chip("med", "medium", counts["med"])
             + chip("low", "low", counts["low"])
             + (chip("info", "notes", counts["info"])
                if counts["info"] else ""))

    # one card per finding
    cards = []
    for i, f in enumerate(norm):
        _, _, slabel, dot, accent, _ = _sev_info(f.get("severity", "med"))
        loc = ""
        if f.get("file"):
            ln = f":{f['line']}" if f.get("line") not in (None, "") else ""
            loc = (f'<code style="font-family:var(--font-mono);font-size:11px;'
                   f'color:var(--text-secondary)">{_esc(f["file"])}'
                   f'{_esc(ln)}</code>')
        dom = (f'<span style="{_MICRO}">{_esc(f["domain"])}</span>'
               if f.get("domain") else "")
        status = f.get("status", "")
        sbadge = ""
        if status:
            fixed = str(status).lower() in ("fixed", "resolved", "done")
            sbadge = (
                f'<span style="border:1px solid '
                f'{"var(--border)" if fixed else accent};border-radius:20px;'
                f'padding:1px 9px;font-family:var(--font-mono);font-size:10px;'
                f'color:{"var(--text-secondary)" if fixed else dot}">'
                f'{"✓ " if fixed else ""}{_esc(status)}</span>')
        scenario = (f'<div style="font-size:13px;color:var(--text-secondary);'
                    f'line-height:1.65;margin-top:8px"><span style="{_MICRO}">'
                    f'FAILURE</span><br>{_esc(f["scenario"])}</div>'
                    if f.get("scenario") else "")
        fix = (f'<div style="font-size:13px;color:var(--text-secondary);'
               f'line-height:1.65;margin-top:8px"><span style="{_MICRO}">FIX'
               f'</span><br>{_esc(f["fix"])}</div>' if f.get("fix") else "")
        body = scenario + fix
        # collapsed by default beyond the summary line; details toggle
        details = (
            f'<div id="tpf-d{i}" style="display:none;border-top:1px solid '
            f'var(--border);margin-top:10px;padding-top:4px">{body}</div>'
            if body else "")
        toggle = (
            f' · <button type="button" onclick="tpToggle({i})" '
            f'aria-expanded="false" aria-label="toggle failure and fix detail" '
            f'style="border:none;background:none;color:var(--text-muted);'
            f'font-family:var(--font-mono);font-size:11px;cursor:pointer;'
            f'padding:0"><span id="tpf-t{i}">details ▾</span></button>'
            if body else "")
        cards.append(
            f'<div class="tpf-card" data-sev="{f["_key"]}" style="{_CARD};'
            f'border-inline-start:3px solid {accent};'
            f'border-radius:0 6px 6px 0;'
            f'margin-bottom:8px">'
            f'<div style="display:flex;align-items:baseline;gap:9px;'
            f'flex-wrap:wrap"><span style="width:8px;height:8px;border-radius:'
            f'50%;background:{dot};flex:none;align-self:center"></span>'
            f'<span style="font-family:var(--font-mono);font-size:10px;'
            f'letter-spacing:1px;color:{dot}">{_esc(slabel)}</span>'
            f'{dom}<span style="font-weight:500;font-size:14px;flex:1;'
            f'min-width:180px">{_esc(f.get("title",""))}</span>{sbadge}</div>'
            f'<div style="margin-top:5px">{loc}{toggle}</div>{details}</div>')
    cards_html = "".join(cards) or ('<div style="font-size:13px;color:'
                                    'var(--text-muted)">no findings</div>')

    # clean checks (what passed) — collapsed list
    clean = meta.get("clean") or []
    clean_html = ""
    if clean:
        items = "".join(
            f'<div style="font-size:12.5px;color:var(--text-secondary);'
            f'padding:3px 0;display:flex;gap:7px"><span style="color:'
            f'var(--text-primary)">✓</span>{_esc(c)}</div>' for c in clean)
        clean_html = (
            f'<details style="margin-top:14px"><summary style="cursor:pointer;'
            f'{_MICRO}">CLEAN — {len(clean)} checks passed</summary>'
            f'<div style="margin-top:8px">{items}</div></details>')

    tests = meta.get("tests")
    tests_pill = (
        f'<span style="border:1px solid var(--border-strong);color:'
        f'var(--text-primary);border-radius:20px;padding:4px 12px;'
        f'font-family:var(--font-mono);font-size:11.5px">'
        f'{_esc(tests)}</span>' if tests else "")

    gate_html = _gate_box(meta)

    title = _esc(meta.get("title", "review findings"))
    subtitle = _esc(meta.get("subtitle", ""))
    note = (f'<div style="{_MICRO};margin-top:10px">{_esc(meta["note"])}</div>'
            if meta.get("note") else "")
    # v1.5.4: coverage + blast-radius surfaced IN the review — both derive from
    # their source of truth (catalog.json / graph.json), so new lenses and the
    # graph can't be silently dropped.
    coverage_html = ""
    cov_map = _effective_coverage(meta)   # routing_decision wins over
    if cov_map is not None:               # lens_coverage; absent → no panel
        coverage_html = render_lens_coverage(cov_map)
    graph_html = ""
    if meta.get("ws"):
        graph_html = render_review_graph(meta["ws"], meta.get("impact"))

    frag = (
        f'<h2 class="sr-only">Review findings: {counts["high"]} high, '
        f'{counts["med"]} medium, {counts["low"]} low'
        + (f', {counts["info"]} notes' if counts["info"] else "")
        + '. Filter by severity '
        f'and expand each for the failure scenario and fix.</h2>'
        f'<div dir="auto" style="padding:0.5rem 0;'
        f'font-family:var(--font-sans);color:'
        f'var(--text-primary)">'
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:flex-start;gap:12px;margin-bottom:12px"><div>'
        f'<div style="font-size:16px;font-weight:500">{title}</div>'
        f'<div style="font-size:13px;color:var(--text-secondary)">{subtitle}'
        f'</div></div>{tests_pill}</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px" '
        f'id="tpf-chips">{chips}</div>'
        f'<div id="tpf-list">{cards_html}</div>'
        f'{coverage_html}{graph_html}{clean_html}{gate_html}{note}'
        f'<script>{_SEND_JS}'
        f'function tpFilter(s){{'
        f'document.querySelectorAll(".tpf-card").forEach(function(c){{'
        f'c.style.display=(s==="all"||c.dataset.sev===s)?"block":"none";}});'
        f'document.querySelectorAll(".tpf-chip").forEach(function(b){{'
        f'var on=b.dataset.sev===s;b.style.background=on?"var(--text-primary)":"none";'
        f'b.style.color=on?"var(--surface-2)":"var(--text-secondary)";'
        # non-color cues so the active filter is legible without color:
        # aria-pressed for screen readers, weight + underline for low-vision.
        f'b.setAttribute("aria-pressed",on?"true":"false");'
        f'b.style.fontWeight=on?"500":"400";'
        f'b.style.textDecoration=on?"underline":"none";}});}}'
        f'function tpToggle(i){{var d=document.getElementById("tpf-d"+i),'
        f't=document.getElementById("tpf-t"+i),b=t.parentNode;'
        f'var open=d.style.display==="block";'
        f'd.style.display=open?"none":"block";t.textContent=open?"details ▾":"details ▴";'
        f'if(b&&b.setAttribute)b.setAttribute("aria-expanded",open?"false":"true");}}'
        f'tpFilter("all");</script></div>')

    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w") as fh:
            fh.write(frag)
    return frag


# ------------------------------------------------------- lens-wave progress

def render_lens_wave(lenses, meta=None, out=None):
    """A live PROGRESS board for a lens fan-out — rendered BEFORE the agents
    are dispatched (all queued/running) and again as they land, so a review
    shows work HAPPENING instead of only a result at the end. Each lens is a
    lane with a status dot and, once reported, its finding count.

    lenses: [{id, name, status:'queued|running|done|blocked',
              findings: int|None}]
    meta: {title, subtitle, base}
    """
    meta = meta or {}
    order = {"running": 0, "queued": 1, "done": 2, "blocked": 3}
    items = sorted(lenses or [],
                   key=lambda x: (order.get(x.get("status", "queued"), 9),
                                  str(x.get("name", ""))))
    total = len(items)
    done = sum(1 for x in items if x.get("status") == "done")
    running = sum(1 for x in items if x.get("status") == "running")

    def lane(x):
        st = x.get("status", "queued")
        if st == "done":
            n = x.get("findings")
            dot, lab = "var(--text-primary)", (
                _msg("n_findings", n=n) if n
                else "clean") if n is not None else "done"
            badge = (f'<span style="font-family:var(--font-mono);font-size:'
                     f'10.5px;color:{"var(--text-danger)" if n else "var(--text-muted)"}">'
                     f'{_esc(lab)}</span>')
            ring = "background:var(--text-primary)"
        elif st == "running":
            dot, badge = "var(--text-primary)", (
                '<span style="font-family:var(--font-mono);font-size:10.5px;'
                'color:var(--text-secondary)">running…</span>')
            ring = "background:var(--text-primary)"
        elif st == "blocked":
            dot, badge = "var(--text-danger)", (
                '<span style="font-family:var(--font-mono);font-size:10.5px;'
                'color:var(--text-danger)">blocked</span>')
            ring = "background:var(--text-danger)"
        else:
            dot, badge = "var(--border-strong)", (
                '<span style="font-family:var(--font-mono);font-size:10.5px;'
                'color:var(--text-muted)">queued</span>')
            ring = "background:none;border:1.5px solid var(--border-strong)"
        return (
            f'<div style="display:flex;align-items:center;gap:9px;'
            f'padding:8px 11px;border:1px solid var(--border);border-radius:6px">'
            f'<span style="width:8px;height:8px;border-radius:50%;flex:none;'
            f'box-sizing:border-box;{ring}"></span>'
            f'<span style="font-family:var(--font-mono);font-size:12.5px;'
            f'flex:1;color:{dot}">{_esc(x.get("id",""))}</span>{badge}</div>')

    lanes = "".join(lane(x) for x in items)
    pct = int(100 * done / total) if total else 0
    phase = ("all lenses reported" if done == total and total else
             f"{running} running · {done}/{total} reported" if total else
             "no lenses")
    title = _esc(meta.get("title", "review — lenses running"))
    sub = _esc(meta.get("subtitle",
               "each lens is a read-only governed agent, running in parallel"))

    frag = (
        f'<h2 class="sr-only">Lens review in progress: {done} of {total} '
        f'lenses reported, {running} running.</h2>'
        f'<div style="padding:0.5rem 0;font-family:var(--font-sans);'
        f'color:var(--text-primary)">'
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:flex-start;gap:12px;margin-bottom:4px"><div>'
        f'<div style="font-size:16px;font-weight:500">{title}</div>'
        f'<div style="font-size:13px;color:var(--text-secondary)">{sub}</div>'
        f'</div><span aria-live="polite" style="font-family:var(--font-mono);'
        f'font-size:11px;'
        f'color:var(--text-muted)">{_esc(phase)}</span>'
        f'</div>'
        f'<div style="height:5px;background:var(--surface-0);border-radius:3px;'
        f'overflow:hidden;margin:12px 0 14px"><span style="display:block;'
        f'height:100%;width:{pct}%;background:var(--text-primary)"></span></div>'
        f'<div style="display:grid;grid-template-columns:repeat(auto-fill,'
        f'minmax(200px,1fr));gap:8px">{lanes}</div>'
        f'<div style="{_MICRO};margin-top:12px">read-only harness on every '
        f'lens-agent — reads the diff, writes only its findings, touches no '
        f'code. Results merge into the findings dashboard at the gate.</div>'
        f'</div>')
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w") as fh:
            fh.write(frag)
    return frag


# ------------------------------------------------------ onboarding dashboard

def headline_onboarding(report):
    """Never-skippable one-liner for the setup state (render contract)."""
    checks = report.get("checks") or []
    ok = sum(1 for c in checks if c.get("ok"))
    nxt = {"attach_folder": "connect a project folder",
           "init_git": "create a git snapshot (git init + commit)",
           "tp_init": "initialize taskplane (tp init)",
           "ready": "ready for governed work"}.get(
        report.get("next_action"), "setup incomplete")
    host = report.get("host")
    tail = f" · host: {host}" if host else ""
    return f"setup {ok}/{len(checks)} prerequisites ready · next: {nxt}{tail}"


def render_onboarding(report, out=None):
    """The cold-start dashboard — walks a brand-new user in from a zero state
    (no folder attached, no repo). Shows the three prerequisites as a
    checklist and offers the single next action as a button (sendPrompt).
    report: the output of tp._onboard_report()."""
    checks = report.get("checks", [])
    nxt = report.get("next_action", "ready")
    done = sum(1 for c in checks if c.get("ok"))
    rows = []
    for c in checks:
        ok = c.get("ok")
        dot = ("var(--text-primary)" if ok else "var(--border-strong)")
        mark = ("✓" if ok else "○")
        rows.append(
            f'<div style="display:flex;gap:11px;align-items:flex-start;'
            f'padding:11px 0;border-bottom:1px solid var(--border)">'
            f'<span style="font-family:var(--font-mono);font-size:15px;'
            f'color:{dot};flex:none;width:16px;text-align:center">{mark}</span>'
            f'<div style="flex:1"><div style="font-size:14px;font-weight:500;'
            f'color:{"var(--text-primary)" if ok else "var(--text-primary)"}">'
            f'{_esc(c.get("label",""))}<span style="font-family:'
            f'var(--font-mono);font-size:11px;color:var(--text-muted);'
            f'font-weight:400;margin-inline-start:8px">{_esc(c.get("detail",""))}'
            f'</span></div>'
            + ('' if ok else
               f'<div style="font-size:12.5px;color:var(--text-secondary);'
               f'line-height:1.55;margin-top:3px">{_esc(c.get("hint",""))}'
               f'</div>')
            + '</div></div>')

    # the single next action, as buttons
    btn = ('border:none;border-radius:6px;padding:9px 15px;font-size:13px;'
           'font-weight:500;cursor:pointer;font-family:var(--font-sans);'
           'background:var(--text-primary);color:var(--surface-2)')
    sec = ('border-radius:6px;padding:9px 15px;font-size:13px;font-weight:500;'
           'cursor:pointer;font-family:var(--font-sans);background:none;'
           'color:var(--text-primary);border:1px solid var(--border-strong)')

    def b(style, label, prompt):
        # tpSend feature-detects the chat bridge: with sendPrompt it fires
        # the prompt; in the static artifact it reveals the exact reply to
        # type in chat instead of dead-clicking with zero feedback.
        return (f'<button style="{style}" onclick="tpSend(this,'
                f'&#39;{_jsattr(prompt)}&#39;)">{_esc(label)}</button>')

    if nxt == "attach_folder":
        headline = "Let's give taskplane a place to work"
        if report.get("host") == "codex":
            sub = ("Open the repository as this Codex task's working folder "
                   "— then start a new task and I'll set up the rest.")
        else:
            sub = ("Connect the folder you want to work in — then I'll set "
                   "up the rest. Nothing's attached yet.")
        actions = (
            b(btn, "How do I connect a folder?",
              "How do I connect a folder or repo so taskplane can work in it?")
            + b(sec, "I have a git repo URL",
                "I want to point taskplane at a git repo — here's the URL: ")
            + b(sec, "Use the current folder",
                "Use the current folder as my taskplane workspace and set it up"))
    elif nxt == "init_git":
        headline = "One step: put this folder under git"
        sub = ("taskplane's gates diff against a commit, so the folder needs "
               "a git snapshot. I can initialize it for you.")
        actions = (
            b(btn, "Initialize git here",
              "Run git init and make the first commit in this folder for taskplane")
            + b(sec, "Clone a repo instead",
                "I'd rather clone a git repo — here's the URL: "))
    elif nxt == "tp_init":
        headline = "Almost there — initialize taskplane"
        sub = ("Folder and repo are ready. `tp init` scaffolds the context "
               "docs, knowledge base, and dependency graph.")
        actions = b(btn, "Initialize taskplane",
                    "Run tp init here and help me fill the context docs")
    else:
        headline = "Ready to go"
        sub = ("Folder, repo, and taskplane are all set. State a goal and "
               "I'll drive the governed loop.")
        actions = b(btn, "Start — what should we build?",
                    "taskplane is set up — help me state my first goal")

    frag = (
        f'<h2 class="sr-only">taskplane setup: {done} of {len(checks)} '
        f'prerequisites ready. {_esc(headline)}.</h2>'
        f'<div style="padding:0.5rem 0;font-family:var(--font-sans);'
        f'color:var(--text-primary)">'
        f'<div style="font-size:12px;font-family:var(--font-mono);'
        f'letter-spacing:1.5px;color:var(--text-muted);margin-bottom:6px">'
        f'TASKPLANE · SETUP</div>'
        f'<div style="font-size:18px;font-weight:500;margin-bottom:3px">'
        f'{_esc(headline)}</div>'
        f'<div style="font-size:13.5px;color:var(--text-secondary);'
        f'line-height:1.55;margin-bottom:16px">{_esc(sub)}</div>'
        f'<div style="border:1px solid var(--border);border-radius:8px;'
        f'padding:4px 16px 8px;margin-bottom:16px">{"".join(rows)}</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap">{actions}</div>'
        f'<div style="{_MICRO};margin-top:14px">taskplane runs locally — it '
        f'reads and writes only inside the folder you connect. Nothing leaves '
        f'your machine.</div><script>{_SEND_JS}</script></div>')

    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w") as fh:
            fh.write(frag)
    return frag


def _run_metrics(ws, tasks, contract, events=None):
    """Real run metrics from the trace (agents, steps, waves, fixes, blocks)
    + the advisory budget. Model tokens are COOPERATIVE in the plugin — the
    paid proxy runtime measures real spend; here we surface the ceiling.
    `events`: a pre-parsed trace list (any order) so the render path parses
    the trace once and reuses it."""
    full = events if events is not None else _read_trace(ws, 99999)
    ev = lambda k: sum(1 for e in full if e["event"] == k)
    budget = "—"
    if contract:
        cap = contract.get("budget", {}).get("max_cost_usd")
        if cap:
            budget = f"${cap:g} cap"
    return {
        "agents": ev("contract_activated") + ev("loop_claim"),
        "steps": ev("loop_step"),
        "waves": ev("loop_wave"),
        "fixes": sum(t.get("fix_cycles", 0) for t in tasks),
        "blocks": ev("hook_deny"),
        "budget": budget,
    }


# ------------------------------------------------------------- harness
# The harness is the point of taskplane: each agent runs inside a contract
# that keeps it ON TOPIC (scope/tools/deny — hook-blocked) and WITHIN
# BUDGET (max_actions — every governed tool call metered, ceiling blocks
# before the action runs). These helpers read the live meters.

def _harness_agents(ws):
    """Every active harness: the main workspace contract plus each parallel
    worker's (.tp-work/<task>/), with its live meter."""
    out = []

    def one(w, tag):
        c = tp.load_active(w)
        if not c:
            return
        tid = c.get("task_id", "_")
        m = {}
        p = os.path.join(tp.tp_dir(w), "meter.json")
        if os.path.exists(p):
            try:
                m = json.load(open(p)).get(tid, {})
            except (ValueError, OSError):
                m = {}
        sc = (c.get("coding") or {}).get("scope_paths") or \
            (c.get("write_allow") if c.get("read_only") else []) or []
        out.append({
            "label": tag or (c.get("task") or tid),
            "tag": tag,
            "read_only": bool(c.get("read_only")),
            "scope": sc,
            "used": m.get("actions", 0),
            "denies": m.get("denies", 0),
            "max": (c.get("budget") or {}).get("max_actions"),
        })

    one(ws, None)
    workroot = os.path.join(ws, ".tp-work")
    if os.path.isdir(workroot):
        for d in sorted(os.listdir(workroot)):
            one(os.path.join(workroot, d), d)
    return out


def _meter_totals(ws):
    """Sum of all metered actions/denies across main + worker meters —
    survives contract clears, so stats stay honest after the loop ends."""
    tot = {"actions": 0, "denies": 0}
    paths = [os.path.join(tp.tp_dir(ws), "meter.json")]
    workroot = os.path.join(ws, ".tp-work")
    if os.path.isdir(workroot):
        for d in sorted(os.listdir(workroot)):
            paths.append(os.path.join(workroot, d, ".taskplane",
                                      "meter.json"))
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            for e in json.load(open(p)).values():
                tot["actions"] += e.get("actions", 0)
                tot["denies"] += e.get("denies", 0)
        except (ValueError, OSError):
            pass
    return tot


def _meter_bar(used, mx):
    """Budget meter, monochrome: primary fill on a hairline track; the ONE
    signal color (danger) appears only at the ceiling."""
    if not mx:
        return (f'<div style="{_MICRO};margin-top:6px">'
                f'{_msg("no_ceiling", n=used)}</div>')
    pct = min(100, int(100 * used / mx))
    at_cap = used >= mx
    col = "var(--text-danger)" if at_cap else "var(--text-primary)"
    cnt = ("var(--text-danger)" if at_cap else
           "var(--text-primary)" if pct >= 70 else "var(--text-secondary)")
    return (
        f'<div style="display:flex;align-items:center;gap:8px;margin-top:7px">'
        f'<span style="{_MICRO}">budget</span><span style="flex:1;height:4px;'
        f'background:var(--surface-0);border-radius:2px;overflow:hidden">'
        f'<span style="display:block;height:100%;width:{pct}%;background:'
        f'{col}"></span></span><span style="flex:none;font-size:11.5px;'
        f'color:{cnt};font-family:var(--font-mono);font-weight:'
        f'{"500" if pct >= 70 else "400"}">{used}/{mx}</span></div>')


def _harness_card(h):
    ro = "read-only review" if h["read_only"] else "build"
    blocked = h["denies"]
    shield = ('<span style="font-family:var(--font-mono);font-size:11.5px;'
              'color:var(--text-secondary)"><i class="ti ti-shield-check" '
              'aria-hidden="true"></i> on topic</span>' if not blocked else
              f'<span style="font-family:var(--font-mono);font-size:11.5px;'
              f'color:var(--text-danger);font-weight:500"><i class="ti '
              f'ti-shield-x" aria-hidden="true"></i> {blocked} blocked'
              f'</span>')
    scope = _esc(", ".join(h["scope"])[:70] or "(any — set scope!)")
    return (
        f'<div style="border:1px solid var(--border);border-radius:6px;'
        f'padding:11px 13px"><div style="display:flex;justify-content:'
        f'space-between;align-items:center;gap:10px;flex-wrap:wrap">'
        f'<span style="font-weight:500">{_esc(str(h["label"])[:34])}'
        f'<span style="{_MICRO};font-weight:400"> · {ro}</span></span>'
        f'{shield}</div>'
        f'<div style="font-size:12px;color:var(--text-secondary);margin-top:'
        f'3px"><code style="font-family:var(--font-mono);font-size:11px">'
        f'{scope}</code></div>{_meter_bar(h["used"], h["max"])}</div>')


# Governance spine for the widget rail. execute/evaluate/fix collapse into
# one "Build" phase — the per-task LANES below the rail show the non-linear
# inner loop (build → evaluate ⟲ fix) and what runs in parallel.
_SPINE = [
    ("pm", "Define", False), ("design", "Design", False),
    ("design_approval", "Approve design", True),
    ("plan", "Plan", False),
    ("plan_approval", "Approve", True), ("build", "Build", False),
    ("em", "Review", False), ("signoff", "Sign-off", True),
    ("done", "Done", False),
]
_BUILD_STEPS = {"execute", "evaluate", "fix"}

_CHIP = {  # lane stage → (dot css, text color, bg)
    "done": ("background:var(--text-primary)", "var(--text-secondary)", ""),
    "cur": ("background:var(--surface-2)", "var(--surface-2)",
            "background:var(--text-primary);"),
    "fail": ("background:var(--surface-2)", "var(--surface-2)",
             "background:var(--text-danger);"),
    "todo": ("background:none;border:1.5px solid var(--border-strong)",
             "var(--text-muted)", ""),
}


def _chip(label, st):
    dot, col, bg = _CHIP[st]
    w = "500" if st in ("cur", "fail") else "400"
    return (f'<span style="display:flex;align-items:center;gap:5px;padding:'
            f'3px 9px;border-radius:20px;font-family:var(--font-mono);'
            f'font-size:11.5px;white-space:nowrap;{bg}color:{col};'
            f'font-weight:{w}"><span style="width:6px;height:6px;'
            f'border-radius:50%;flex:none;box-sizing:border-box;{dot}">'
            f'</span>{label}</span>')


_CONN = ('<span style="flex:none;width:12px;height:2px;'
         'background:var(--border)"></span>')


def _lane(t, loop_step, meter=None):
    """One task's own build→evaluate⟲fix mini-pipeline — the per-task,
    non-linear view the governance rail can't show. `meter` (a harness
    dict) adds the live action-budget bar."""
    stt = t.get("status", "pending")
    fx = t.get("fix_cycles", 0)
    if stt == "passed":
        stages = ("done", "done", "done" if fx else "todo")
    elif stt == "failed":
        stages = ("done", "done", "fail")
    elif stt == "built":
        stages = ("done", "cur", "todo")
    elif stt == "running":
        if loop_step == "evaluate":
            stages = ("done", "cur", "todo")
        elif loop_step == "fix":
            stages = ("done", "done", "cur")
        else:
            stages = ("cur", "todo", "todo")
    else:
        stages = ("todo", "todo", "todo")
    bg, fg, lbl = _BADGE.get(stt, ("var(--surface-0)",
                                   "var(--text-muted)", stt))
    fixlbl = "fix" + (f' <i class="ti ti-refresh" aria-hidden="true"></i>{fx}'
                      if fx else "")
    deps = t.get("deps") or []
    wait = (f' · waits on {_esc(", ".join(deps))}'
            if deps and stt == "pending" else "")
    rail = (_chip("build", stages[0]) + _CONN + _chip("evaluate", stages[1])
            + _CONN + _chip(fixlbl, stages[2]))
    scope = _esc(", ".join(t.get("scope", [])))
    bar = _meter_bar(meter["used"], meter["max"]) if meter else ""
    return (
        f'<div style="border:1px solid var(--border);border-radius:6px;'
        f'padding:10px 12px"><div style="display:flex;justify-content:space-'
        f'between;align-items:center;gap:10px;flex-wrap:wrap"><span style="'
        f'font-weight:500">{_esc(t.get("id", "?"))}</span><span style="'
        f'display:flex;align-items:center">{rail}</span><span style="'
        f'background:{bg};color:{fg};border:1px solid var(--border);'
        f'border-radius:20px;padding:2px 9px;font-family:var(--font-mono);'
        f'font-size:10.5px">{_esc(lbl)}</span></div><div style="font-size:12px;'
        f'color:var(--text-secondary);margin-top:4px"><code style="'
        f'font-family:var(--font-mono);font-size:11px">{scope}</code>'
        f'{wait}</div>{bar}</div>')


def _graph_panel(ws, tasks):
    """Graph tab: module/edge summary, most-connected hubs, and the blast
    radius of the current tasks' scope — all from the committed graph."""
    g = _dg.load(ws)          # external store, via the graph owner's loader
    if not (g.get("modules") or g.get("edges")):
        return ('<div style="font-size:13px;color:var(--text-muted)">no '
                'dependency graph yet — scanned at loop start, or run '
                '<code style="font-family:var(--font-mono)">tp graph scan'
                '</code>. In a polyglot repo the scanner follows in-language '
                'imports, so cross-service calls (a Node gateway → Python '
                'services over HTTP) are not import edges and the graph can '
                'look sparse — record those with <code style="font-family:'
                'var(--font-mono)">tp graph edge</code>.</div>')
    mods, edges = g.get("modules", {}), g.get("edges", [])
    internal = [e for e in edges
                if not str(e.get("to", "")).startswith("ext:")]
    deg = {}
    for e in internal:
        for k in ("from", "to"):
            m = e.get(k)
            if m and m != "(root)" and not str(m).startswith("req:"):
                deg[m] = deg.get(m, 0) + 1   # product nodes get their own panel
    hubs = sorted(deg.items(), key=lambda kv: -kv[1])[:7]
    mx = hubs[0][1] if hubs else 1
    bars = "".join(
        f'<div style="display:flex;align-items:center;gap:8px;font-size:12px;'
        f'padding:3px 0"><span style="flex:1 1 96px;min-width:0;'
        f'max-width:150px;overflow:hidden;'
        f'text-overflow:ellipsis;white-space:nowrap;color:var(--text-'
        f'secondary)">{_esc(m)}</span><span style="flex:1;height:8px;'
        f'background:var(--surface-0);border-radius:4px;overflow:hidden">'
        f'<span style="display:block;height:100%;width:{int(100 * d / mx)}%;'
        f'background:var(--text-primary);border-radius:2px"></span></span>'
        f'<span style="flex:none;width:26px;text-align:end;color:'
        f'var(--text-muted)">{d}</span></div>' for m, d in hubs)
    tile3 = (
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">'
        f'<div style="background:none;border:1px solid '
        f'var(--border);border-radius:12px;padding:10px 16px;text-align:'
        f'center;min-width:92px"><div style="font-size:22px;font-weight:500;font-family:var(--font-mono);'
        f'color:var(--text-primary)">{len(mods)}</div><div style="font-size:'
        f'11px;color:var(--text-muted)">modules</div></div>'
        f'<div style="background:none;border:1px solid '
        f'var(--border);border-radius:12px;padding:10px 16px;text-align:'
        f'center;min-width:92px"><div style="font-size:22px;font-weight:500;font-family:var(--font-mono);'
        f'color:var(--text-primary)">{len(internal)}</div><div style="'
        f'font-size:11px;color:var(--text-muted)">internal edges</div></div>'
        f'<div style="background:none;border:1px solid '
        f'var(--border);border-radius:12px;padding:10px 16px;text-align:'
        f'center;min-width:92px"><div style="font-size:22px;font-weight:500;font-family:var(--font-mono);'
        f'color:var(--text-primary)">{len(edges) - len(internal)}</div>'
        f'<div style="font-size:11px;color:var(--text-muted)">external deps'
        f'</div></div></div>')
    imp_html = ""
    scope = sorted({s.rstrip("*").rstrip("/") for t in tasks
                    for s in t.get("scope", []) if s})
    if scope:
        try:
            import depgraph
            im = depgraph.impact(ws, scope)
            touched = im.get("touched", [])
            chips = "".join(
                f'<span style="background:none;border:1px solid var(--border-strong);'
                f'color:var(--text-secondary);border-radius:20px;padding:2px 10px;'
                f'font-family:var(--font-mono);font-size:11.5px">'
                f'{_esc(m)}</span>' for m in touched[:8])
            d1 = (im.get("impacted") or {}).get(1, [])
            rows = "".join(
                f'<div style="font-size:12px;color:var(--text-secondary);'
                f'padding:2px 0">{_esc(e.get("module", ""))} <span style="'
                f'color:var(--text-muted)">({_esc(e.get("kind", ""))} '
                f'{_arrow(back=True)} '
                f'{_esc(e.get("via", ""))})</span></div>' for e in d1[:6])
            more = (f'<div style="font-size:12px;color:var(--text-muted);'
                    f'padding:2px 0">…+{len(d1) - 6} more at depth 1</div>'
                    if len(d1) > 6 else "")
            imp_html = (
                f'<div style="background:none;border:1px solid '
                f'var(--border);border-radius:6px;padding:14px;margin-top:'
                f'12px"><div style="font-size:12px;color:var(--text-muted);'
                f'letter-spacing:.5px;margin-bottom:8px">blast radius of the '
                f'current scope</div><div style="display:flex;gap:6px;'
                f'flex-wrap:wrap;margin-bottom:8px">{chips}</div>'
                f'<div style="font-size:13px;color:var(--text-secondary);'
                f'margin-bottom:6px">'
                + _msg("dependent_modules", n=im.get("total_impacted", 0))
                + f'</div>{rows}{more}</div>')
        except Exception:
            imp_html = ""

    # Product layer: requirements living IN the graph — what each one
    # plans/realizes, product depends-edges, and any shared surface the
    # plan gate flagged on the current tasks.
    prod_html = ""
    req_edges = [e for e in edges
                 if str(e.get("from", "")).startswith("req:")]
    if req_edges:
        by_req = {}
        for e in req_edges:
            by_req.setdefault(e["from"], {"planned": [], "realizes": [],
                                          "depends": []})
            k = e.get("kind")
            if k in ("planned", "realizes", "depends"):
                by_req[e["from"]][k].append(str(e.get("to", "")))
        rows = []
        for rid_n in sorted(by_req):
            d = by_req[rid_n]
            mods_txt = ", ".join(_esc(m) for m in
                                 (d["realizes"] or d["planned"])[:6])
            kind_lbl = "realizes" if d["realizes"] else "planned"
            dep_txt = ("".join(
                f'<span style="border:1px solid var(--border-strong);'
                f'color:var(--text-secondary);border-radius:20px;'
                f'padding:1px 8px;font-family:var(--font-mono);'
                f'font-size:10.5px;margin-inline-start:4px">{_arrow()} {_esc(r)}</span>'
                for r in d["depends"][:4]))
            rows.append(
                f'<div style="display:flex;align-items:baseline;gap:8px;'
                f'font-size:12px;padding:3px 0;flex-wrap:wrap">'
                f'<span style="font-family:var(--font-mono);'
                f'color:var(--text-primary)">{_esc(rid_n)}</span>'
                f'<span style="font-family:var(--font-mono);font-size:10px;'
                f'letter-spacing:1px;color:var(--text-muted)">'
                f'{kind_lbl}</span>'
                f'<span style="color:var(--text-secondary)">{mods_txt}'
                f'</span>{dep_txt}</div>')
        shared = sorted({r for t in tasks
                         for r in (t.get("blast") or {}).get(
                             "shared_with", [])})
        shared_html = (
            f'<div style="font-size:12px;color:var(--text-danger);'
            f'margin-top:6px">⚠ shared surface — current scope overlaps: '
            f'{", ".join(_esc(r) for r in shared)} (their criteria need '
            f're-checking at review)</div>' if shared else "")
        prod_html = (
            f'<div style="background:none;border:1px solid var(--border);'
            f'border-radius:6px;padding:14px;margin-top:12px">'
            f'<div style="font-family:var(--font-mono);font-size:10.5px;'
            f'letter-spacing:1.2px;color:var(--text-muted);margin-bottom:'
            f'8px">product layer — requirements ↔ modules</div>'
            f'{"".join(rows)}{shared_html}</div>')
    return (tile3
            + f'<div style="background:none;border:1px solid '
              f'var(--border);border-radius:6px;padding:14px"><div style="'
              f'font-family:var(--font-mono);font-size:10.5px;letter-spacing:1.2px;color:var(--text-muted);'
              f'margin-bottom:8px">most connected modules</div>{bars}</div>'
            + imp_html + prod_html
            + '<div style="font-size:12px;color:var(--text-muted);margin-top:'
              '10px">from the committed dependency graph — engineering AND '
              'product edges (deterministic, zero tokens).</div>')


def _context_panel(ws, state, trace_all):
    """Context tab: the requirement, its acceptance criteria, routed lenses,
    scope, recent KB decisions and open debt — what the loop is holding."""
    idx = _kb.load_index(ws)      # external store, via the KB owner's loader
    rid = (state or {}).get("requirement_id")
    req = next((r for r in idx.get("requirements", [])
                if r.get("id") == rid), None)
    parts = []
    # R-0002: governing decisions — accepted ADRs whose modules overlap the
    # current task's scope; always shown, they are in force for this work.
    _t = _loop._current_task(state) if state else None
    _scope = (_t or {}).get("scope") or []
    gov = _kb.governing(ws, _scope) if _scope else []
    if gov:
        rows = "".join(
            f'<div style="display:flex;gap:8px;align-items:baseline;'
            f'font-size:13px;padding:3px 0"><span style="font-family:'
            f'var(--font-mono);font-size:11px;color:var(--text-muted)">'
            f'{_esc(d["id"])}</span><span>{_esc(d["title"])}</span>'
            f'<span style="font-family:var(--font-mono);font-size:10px;'
            f'color:var(--text-success,var(--text-primary))">in force'
            f'</span></div>' for d in gov)
        parts.append(
            f'<div id="tp-governing" style="border:1px solid var(--border);'
            f'border-radius:6px;padding:12px 14px;margin-bottom:12px">'
            f'<div style="{_MICRO};margin-bottom:6px">governing decisions '
            f'\u2014 accepted, scope-linked</div>{rows}</div>')
    # R-0004: current-state grounding \u2014 the as-built inventory every design
    # lens is grounded in; shown whenever it's filled.
    _cs = _kb.current_state(ws)
    if _cs:
        _n_lines = sum(1 for ln in _cs["text"].splitlines()
                       if ln.strip() and not ln.strip().startswith("#"))
        parts.append(
            f'<div id="tp-current-state" style="border:1px solid '
            f'var(--border);border-radius:6px;padding:12px 14px;'
            f'margin-bottom:12px"><div style="{_MICRO};margin-bottom:6px">'
            f'current state \u2014 as-built inventory (grounding)</div>'
            f'<div style="font-size:13px">{_esc(_cs["path"])} '
            f'<span style="font-family:var(--font-mono);font-size:10px;'
            f'color:var(--text-success,var(--text-primary))">grounds design '
            f'lenses \u00b7 {_n_lines} lines</span></div></div>')
    if req:
        acc = "".join(
            f'<div style="display:flex;gap:8px;align-items:baseline;'
            f'font-size:13px;padding:3px 0"><i class="ti ti-target" '
            f'style="color:var(--text-secondary)" aria-hidden="true"></i>'
            f'<span>{_esc(a)}</span></div>' for a in req.get("acceptance", []))
        fun = "".join(
            f'<div style="font-size:13px;color:var(--text-secondary);'
            f'padding:2px 0">· {_esc(f)}</div>'
            for f in req.get("functional", []))
        nfr = "".join(
            f'<div style="font-size:12px;color:var(--text-muted);padding:'
            f'2px 0">{_esc(k)}: {_esc(v)}</div>'
            for k, v in (req.get("nfr") or {}).items())
        parts.append(
            f'<div style="background:none;border:1px solid '
            f'var(--border);border-radius:6px;padding:14px;margin-bottom:'
            f'12px"><div style="display:flex;justify-content:space-between;'
            f'align-items:center;margin-bottom:8px"><span style="font-family:var(--font-mono);font-size:10.5px;'
            f'letter-spacing:1.2px;color:var(--text-muted)">requirement '
            f'{_esc(rid or "")}</span><span style="background:var(--surface-'
            f'0);color:var(--text-muted);border-radius:20px;padding:2px 9px;'
            f'font-size:11px">{_esc(req.get("status", ""))}</span></div>'
            f'<div style="font-weight:500;margin-bottom:6px">'
            f'{_esc(req.get("title", ""))}</div>{fun}'
            f'<div style="font-size:12px;color:var(--text-muted);'
            f'letter-spacing:.5px;margin:10px 0 4px">acceptance criteria '
            f'(→ DoD)</div>{acc}{nfr}</div>')
    lenses = next((e.get("lenses", []) for e in trace_all
                   if e.get("event") == "lens_route"), [])
    if lenses:
        pairs = []
        for x in lenses:
            if isinstance(x, (list, tuple)):
                pairs.append((_esc(x[0]), _esc(x[1]) if len(x) > 1 else ""))
            else:
                pairs.append((_esc(x), ""))
        lchips = "".join(
            f'<span style="background:none;color:var(--text-'
            f'secondary);border:1px solid var(--border-strong);border-radius:20px;'
            f'padding:3px 11px;font-family:var(--font-mono);font-size:11.5px">{name}<span style="color:'
            f'var(--text-muted)"> · {mode}</span></span>'
            for name, mode in pairs)
        parts.append(
            f'<div style="background:none;border:1px solid '
            f'var(--border);border-radius:6px;padding:14px;margin-bottom:'
            f'12px"><div style="font-size:12px;color:var(--text-muted);'
            f'letter-spacing:.5px;margin-bottom:8px">routed lenses (picked '
            f'by the diff, not by role)</div><div style="display:flex;gap:'
            f'6px;flex-wrap:wrap">{lchips}</div></div>')
    decs = (idx.get("decisions") or [])[-3:][::-1]
    if decs:
        drows = "".join(
            f'<div style="display:flex;justify-content:space-between;gap:10px'
            f';font-size:13px;padding:4px 0;border-bottom:1px solid '
            f'var(--border)"><span>{_esc(d.get("id", ""))} '
            f'{_esc(d.get("title", ""))[:44]}</span><span style="color:'
            f'var(--text-muted);font-size:12px">'
            f'{_esc(", ".join((d.get("tags") or [])[:2]))}</span></div>'
            for d in decs)
        parts.append(
            f'<div style="background:none;border:1px solid '
            f'var(--border);border-radius:6px;padding:14px;margin-bottom:'
            f'12px"><div style="font-size:12px;color:var(--text-muted);'
            f'letter-spacing:.5px;margin-bottom:8px">recent KB decisions '
            f'(committed, injected at review steps)</div>{drows}</div>')
    debt = [d for d in (idx.get("debt") or []) if d.get("status") == "open"]
    if debt:
        drows = "".join(
            f'<div style="display:flex;gap:8px;align-items:baseline;'
            f'font-size:13px;padding:3px 0"><i class="ti ti-bookmark" '
            f'style="color:var(--text-secondary)" aria-hidden="true"></i>'
            f'<span>{_esc(d.get("title", d.get("id", "")))}</span></div>'
            for d in debt[:5])
        parts.append(
            f'<div style="background:none;border:1px solid '
            f'var(--border);border-radius:6px;padding:14px;margin-bottom:'
            f'12px"><div style="font-size:12px;color:var(--text-muted);'
            f'letter-spacing:.5px;margin-bottom:8px">open debt</div>{drows}'
            f'</div>')
    # v1.5.4: the lens catalog is part of the governed context — show it here
    # (sourced from catalog.json), so adding a lens is reflected in the loop
    # dashboard without touching this file.
    parts.append(render_lens_coverage(None))
    if not parts:
        return ('<div style="font-size:13px;color:var(--text-muted)">no '
                'context recorded yet — the PM step records the requirement '
                'first.</div>')
    return "".join(parts)


def _agents_hero(harness, tasks, step, parallel):
    """The live parallel-agents fan-out — a hero band ON TOP of mission
    control (Cowork-style): 'running N agents' + one card per governed
    agent showing its status, what it's doing, scope, and live budget.
    Rendered only while agents are actually active (a build wave, or a
    single active contract). Monochrome: filled=active, hollow=queued,
    inverted=running-now, danger only for blocked."""
    # Prefer task lanes during a build wave (they carry per-task state),
    # else the harness (serial contract). Map to a common card shape.
    cards = []
    hmap = {h["tag"]: h for h in harness if h.get("tag")}
    hmain = next((h for h in harness if not h.get("tag")), None)
    building = step in _BUILD_STEPS
    if tasks and building:
        for t in tasks:
            stt = t.get("status", "pending")
            h = hmap.get(t.get("id")) or (hmain if stt == "running" else None)
            cards.append({
                "id": t.get("id", "?"),
                "status": stt,
                "act": {"running": "editing files", "built": "built · gating",
                        "passed": "done", "failed": "fix cycle",
                        "pending": "queued"}.get(stt, stt),
                "scope": ", ".join(t.get("scope", [])),
                "used": (h or {}).get("used"), "max": (h or {}).get("max"),
                "denies": (h or {}).get("denies", 0),
            })
    else:
        for h in harness:
            cards.append({
                "id": h["label"], "status": "running",
                "act": "read-only review" if h["read_only"] else "building",
                "scope": ", ".join(h["scope"]),
                "used": h["used"], "max": h["max"], "denies": h["denies"],
            })
    if not cards:
        return ""

    n_run = sum(1 for c in cards if c["status"] in ("running",))
    n_done = sum(1 for c in cards if c["status"] == "passed")
    head_n = len([c for c in cards])
    verb = "running" if n_run else ("done" if n_done == head_n else "governing")
    chips = []
    for c in cards:
        stt = c["status"]
        if stt == "passed":
            dot = 'background:var(--text-primary)'
            act = ('<i class="ti ti-check" aria-hidden="true"></i> ' + c["act"])
        elif stt == "failed":
            dot = 'background:var(--text-danger)'
            act = c["act"]
        elif stt == "running":
            dot = ('background:var(--text-primary);box-shadow:0 0 0 3px '
                   'var(--surface-0)')
            act = c["act"]
        else:  # pending
            dot = ('background:none;border:1.5px solid var(--border-strong)')
            act = c["act"]
        budget = ""
        if c.get("max"):
            pct = min(100, int(100 * (c["used"] or 0) / c["max"]))
            bc = ("var(--text-danger)" if (c["used"] or 0) >= c["max"]
                  else "var(--text-primary)")
            budget = (
                f'<div style="display:flex;align-items:center;gap:6px;margin-'
                f'top:7px"><span style="flex:1;height:3px;background:var(--'
                f'surface-0);border-radius:2px;overflow:hidden"><span style='
                f'"display:block;height:100%;width:{pct}%;background:{bc}">'
                f'</span></span><span style="font-family:var(--font-mono);'
                f'font-size:10px;color:var(--text-muted)">{c["used"]}/'
                f'{c["max"]}</span></div>')
        elif c.get("used") is not None:
            budget = (f'<div style="{_MICRO};margin-top:7px">{c["used"]} '
                      f'actions</div>')
        flag = (f'<span style="color:var(--text-danger);font-family:var(--'
                f'font-mono);font-size:10px"><i class="ti ti-ban" aria-hidden='
                f'"true"></i> {c["denies"]}</span>' if c.get("denies") else "")
        scope = _esc(c["scope"][:38] or "—")
        chips.append(
            f'<div style="flex:1;min-width:150px;border:1px solid var(--'
            f'border);border-radius:6px;padding:10px 12px"><div style="display'
            f':flex;align-items:center;gap:7px"><span style="width:8px;height:'
            f'8px;border-radius:50%;flex:none;box-sizing:border-box;{dot}">'
            f'</span><span style="font-weight:500;font-size:13px;overflow:'
            f'hidden;text-overflow:ellipsis;white-space:nowrap">{_esc(c["id"])}'
            f'</span><span style="flex:1"></span>{flag}</div><div style="'
            f'{_MICRO};margin-top:4px;padding-inline-start:15px">{_esc(act)}</div>'
            f'<div style="font-size:11px;color:var(--text-secondary);margin-'
            f'top:2px;padding-inline-start:15px"><code style="font-family:var(--font-'
            f'mono);font-size:10.5px">{scope}</code></div>{budget}</div>')
    return (
        f'<div style="border:1px solid var(--border-strong);border-radius:6px;'
        f'padding:12px 14px;margin-bottom:14px"><div style="display:flex;'
        f'align-items:center;gap:8px;margin-bottom:10px"><i class="ti ti-'
        f'arrows-split" aria-hidden="true" style="color:var(--text-primary)">'
        f'</i><span style="font-weight:500;font-size:14px">'
        f'{_msg("n_agents", verb=verb, n=head_n)}</span>'
        f'{"" if not parallel else "<span style=" + chr(34) + _MICRO + chr(34) + ">· parallel wave</span>"}'
        f'<span style="flex:1"></span>'
        f'<span style="{_MICRO}">{n_done}/{head_n} done</span></div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap">{"".join(chips)}'
        f'</div></div>')


# --- Dashboard v2 (R-0001): step journey, model table, always-on stats ---

def _journey(ws, events=None, state=None):
    """Ordered step visits reconstructed from the trace — the single source
    of truth (no new state files). A model_tier event opens a visit; the
    matching loop_gate closes it; loop_step DoR detail enriches it.
    `events` (chronological) and `state` are passed by the render path so
    the trace is parsed once per render, not once per panel."""
    full = events if events is not None else _read_trace_all(ws)
    # Artifacts for review: the requirement's FULL acceptance criteria and
    # the FULL execution plan — attached to the steps that produced them,
    # from the same sources the gates use (loop state + requirements store).
    state = (state if state is not None else _load_loop(ws)) or {}
    req = None
    if state.get("requirement_id"):
        try:
            import requirements as _reqs
            req = _reqs.get_requirement(ws, state["requirement_id"])
        except Exception:
            req = None
    criteria = (req or {}).get("acceptance") or []
    plan_tasks = state.get("tasks") or []
    visits = []
    for e in full:
        ev = e.get("event")
        if ev == "model_tier":
            visits.append({
                "step": e.get("step"), "task": e.get("task"),
                "tier": e.get("tier"), "model": e.get("model"),
                "agent": _loop.STEP_ROLE.get(e.get("step"), "\u2014"),
                "ts": e.get("ts"), "ts_end": None,
                "outcome": None, "note": "", "dor": None,
                "criteria": criteria if e.get("step") in ("pm", "design") else None,
                "design": _loop._design_context(ws, state)
                if e.get("step") in ("design", "design_approval") else None,
                "plan": plan_tasks if e.get("step") in
                ("plan", "plan_approval") else None})
        elif ev == "loop_step" and visits and e.get("dor_ready") is not None:
            visits[-1]["dor"] = {
                "ready": e.get("dor_ready"),
                "blockers": e.get("dor_blockers") or [],
                "warnings": e.get("dor_warnings") or []}
        elif ev == "loop_gate":
            step = e.get("step")
            for v in reversed(visits):
                if v["step"] == step and v["outcome"] is None:
                    v["outcome"] = e.get("outcome")
                    v["note"] = e.get("note") or ""
                    v["ts_end"] = e.get("ts")
                    break
            else:
                # human gates (plan_approval, signoff, selection) have no
                # model_tier brief — the gate event IS the visit
                visits.append({
                    "step": step, "task": e.get("task"), "tier": None,
                    "model": None, "agent": "you",
                    "ts": e.get("ts"), "ts_end": e.get("ts"),
                    "outcome": e.get("outcome"),
                    "note": e.get("note") or "", "dor": None,
                    "criteria": criteria if step in
                    ("design_approval", "signoff") else None,
                    "design": _loop._design_context(ws, state)
                    if step == "design_approval" else None,
                    "plan": plan_tasks if step == "plan_approval"
                    else None})
        elif ev == "loop_resolve":
            visits.append({
                "step": "resolve", "task": e.get("task"), "tier": None,
                "model": None, "agent": "you",
                "ts": e.get("ts"), "ts_end": e.get("ts"),
                "outcome": e.get("decision"), "note": "", "dor": None})
    return visits


def _model_rows(ws):
    """Who ran on what: expected dispatches (brief queue) joined best-effort,
    in order, with the observed dispatches the v1.0.1 hook recorded."""
    exp = tp._load_queue(tp._dispatch_path(ws, "expected_dispatch.json"))
    obs = tp._load_queue(tp._dispatch_path(ws, "observed_dispatch.json"))
    by_agent = {}
    for o in obs:
        by_agent.setdefault(o.get("agent"), []).append(o)
    rows = []
    for e in exp:
        pool = by_agent.get(e.get("agent"))
        o = pool.pop(0) if pool else None
        if o is None:
            disp = "\u2014"
        else:
            disp = (o.get("model") or "session") +                 (" \u2713" if o.get("ok") else " \u2717")
        rows.append({"agent": e.get("agent") or "\u2014",
                     "what": e.get("ref") or e.get("kind") or "\u2014",
                     "tier": e.get("model_tier") or "\u2014",
                     "resolved": e.get("model") or "inherit",
                     "dispatched": disp})
    return rows


def render_journey(visits, suffix="s"):
    """Client-side step navigator: one entry per traversed step; clicking
    reveals that step's execution + decision detail. Pure inline JS."""
    if not visits:
        return ""
    items, details = [], []
    for i, v in enumerate(visits):
        oid = f"tpj{suffix}{i}"
        oc = v["outcome"]
        dot = ("var(--text-success,var(--text-primary))" if oc == "pass"
               or oc in ("approved", "retry", "skip", "defer")
               else "var(--text-danger)" if oc in ("fail", "rejected",
                                                   "abort")
               else "var(--border-strong)")
        label = _esc(v["step"] or "\u2014")
        if v.get("task"):
            label += f' \u00b7 {_esc(str(v["task"]))}'
        meta = _esc(v["agent"] or "\u2014")
        if v.get("tier"):
            meta += (f' \u00b7 {_esc(v["tier"])} '
                     f'({_esc(v["model"] or "session")})')
        items.append(
            f'<div onclick="tpJ(\'{suffix}\',{i})" id="{oid}-b" '
            f'role="button" tabindex="0" aria-expanded="false" '
            f'aria-controls="{oid}" '
            f'aria-label="step {_attr(v["step"] or "")} — show execution '
            f'detail" onkeydown="{_KEYCLICK}" '
            f'data-step="{_attr(v["step"] or "")}" '
            f'style="display:flex;align-items:center;gap:8px;padding:6px '
            f'9px;border-radius:6px;cursor:pointer;font-size:12.5px;'
            f'border:1px solid transparent">'
            f'<span style="width:8px;height:8px;border-radius:50%;flex:none;'
            f'background:{dot}"></span><span>{label}</span>'
            f'<span style="margin-inline-start:auto;'
            f'font-family:var(--font-mono);'
            f'font-size:10px;color:var(--text-muted)">{meta}</span></div>')
        kv = []
        def _row(k, val):
            kv.append(f'<div style="display:flex;justify-content:'
                      f'space-between;gap:12px;font-size:12px;padding:4px 0;'
                      f'border-bottom:1px solid var(--border)">'
                      f'<span style="color:var(--text-muted)">{k}</span>'
                      f'<span style="text-align:end">{val}</span></div>')
        _row("agent", _esc(v["agent"] or "\u2014"))
        if v.get("tier"):
            _row("model", f'tier {_esc(v["tier"])} {_arrow()} '
                          f'{_esc(v["model"] or "inherit (session)")}')
        _row("outcome", _esc(oc or "in progress"))
        if v.get("note"):
            _row("decision / note", _esc(v["note"]))
        if v.get("dor"):
            d = v["dor"]
            _row("DoR", ("ready \u2713" if d["ready"] else "NOT READY: "
                         + _esc("; ".join(d["blockers"])))
                 + (_msg("n_warnings", n=len(d["warnings"]))
                    if d["warnings"] else ""))
        if v.get("ts"):
            when = _fmt_ts(v["ts"])
            dur = ""
            if v.get("ts_end"):
                dur = f' \u00b7 {max(0, int(v["ts_end"] - v["ts"]))}s'
            _row("when", _esc(when) + " UTC" + dur)
        artifacts = ""
        if v.get("criteria"):
            lis = "".join(f'<li style="margin:3px 0">{_esc(str(a))}</li>'
                          for a in v["criteria"])
            artifacts += (
                f'<div style="{_MICRO};margin:10px 0 4px">acceptance '
                f'criteria \u2014 all {len(v["criteria"])}, for review'
                f'</div><ol style="margin:0;padding-inline-start:18px;'
                f'font-size:12px;line-height:1.45">{lis}</ol>')
        if v.get("plan"):
            trs = ""
            for t in v["plan"]:
                _tid = _esc(str(t.get("id")))
                _tsc = _esc(", ".join(t.get("scope") or []))
                _tdp = _esc(", ".join(t.get("deps") or []) or "\u2014")
                _tst = _esc(t.get("status") or "pending")
                trs += (
                    f'<tr><td style="padding:3px 6px;font-family:'
                    f'var(--font-mono);font-size:11px">{_tid}</td>'
                    f'<td style="padding:3px 6px;font-family:'
                    f'var(--font-mono);font-size:11px">{_tsc}</td>'
                    f'<td style="padding:3px 6px">{_tdp}</td>'
                    f'<td style="padding:3px 6px">{_tst}</td></tr>')
            th = ("font-size:9.5px;"
                  "letter-spacing:.6px;color:var(--text-muted);text-align:"
                  "start;padding:3px 6px;border-bottom:1px solid "
                  "var(--border)")
            artifacts += (
                f'<div style="{_MICRO};margin:10px 0 4px">'
                + _esc(_msg("plan_all_tasks", n=len(v["plan"]))) + '</div>'
                f'<table style="width:100%;border-collapse:collapse;'
                f'font-size:12px"><tr><th style="{th}">task</th>'
                f'<th style="{th}">scope</th><th style="{th}">deps</th>'
                f'<th style="{th}">status</th></tr>{trs}</table>')
        details.append(
            f'<div id="{oid}" style="display:none;padding:2px 4px">'
            + "".join(kv) + artifacts + '</div>')
    # tpJ marks the active step with aria-current + aria-expanded (not
    # color alone): SR users hear which step's details are showing.
    js = ('<script>function tpJ(sfx,i){var n=0;'
          'while(document.getElementById("tpj"+sfx+n)){'
          'var d=document.getElementById("tpj"+sfx+n),'
          'b=document.getElementById("tpj"+sfx+n+"-b");'
          'var on=n===i;'
          'd.style.display=on?"block":"none";'
          'b.style.borderColor=on?"var(--border-strong)":"transparent";'
          'b.style.background=on?"var(--surface-0)":"none";'
          'b.setAttribute("aria-expanded",on?"true":"false");'
          'if(on){b.setAttribute("aria-current","true");}'
          'else{b.removeAttribute("aria-current");}'
          'n++;}}</script>')
    return (
        f'<div id="tp-journey-{suffix}" style="border:1px solid '
        f'var(--border);border-radius:6px;padding:12px 14px;margin-bottom:'
        f'14px"><div style="{_MICRO};margin-bottom:8px">step journey \u2014 '
        f'click a step for its execution &amp; decisions</div>'
        f'<div class="tp-jgrid" style="display:grid;'
        f'grid-template-columns:minmax(220px,38%) '
        f'1fr;gap:12px"><div>{"".join(items)}</div>'
        f'<div>{"".join(details)}'
        f'<div style="font-size:11px;color:var(--text-muted);padding:4px">'
        f'select a step on the left</div></div></div></div>' + js)


def render_stats(ws, metrics, denials, suffix="s"):
    """The always-on stats band (was retro-only) + the agent\u2192model
    table \u2014 who ran which step/lens on which model, live."""
    rows = _model_rows(ws)
    cells = [("agents", metrics["agents"]), ("steps", metrics["steps"]),
             ("waves", metrics["waves"]), ("fix cycles", metrics["fixes"]),
             ("blocks", denials)]
    band = "".join(
        f'<div style="flex:1;text-align:center;padding:7px 6px">'
        f'<div style="font-size:16px;font-weight:600;'
        f'{"color:var(--text-danger)" if k == "blocks" and v else ""}">{v}'
        f'</div><div style="{_MICRO}">{k}</div></div>'
        for k, v in cells)
    tbl = ""
    if rows:
        tr = "".join(
            f'<tr><td style="padding:4px 6px">{_esc(r["agent"])}</td>'
            f'<td style="padding:4px 6px">{_esc(str(r["what"]))}</td>'
            f'<td style="padding:4px 6px">{_esc(r["tier"])}</td>'
            f'<td style="padding:4px 6px;font-family:var(--font-mono);'
            f'font-size:11px">{_esc(r["resolved"])}</td>'
            f'<td style="padding:4px 6px;font-family:var(--font-mono);'
            f'font-size:11px">{_esc(r["dispatched"])}</td></tr>'
            for r in rows[-14:])
        th = ('font-size:9.5px;letter-spacing:.6px;'
              'color:var(--text-muted);text-align:start;padding:3px 6px;'
              'border-bottom:1px solid var(--border)')
        tbl = (
            f'<table id="tp-models-{suffix}" style="width:100%;'
            f'border-collapse:collapse;font-size:12px;margin-top:8px">'
            f'<tr><th style="{th}">agent</th><th style="{th}">step / lens'
            f'</th><th style="{th}">tier</th><th style="{th}">resolved</th>'
            f'<th style="{th}">dispatched</th></tr>{tr}</table>')
    return (
        f'<div id="tp-stats-{suffix}" style="border:1px solid var(--border);'
        f'border-radius:6px;padding:8px 10px;margin-bottom:14px">'
        f'<div style="display:flex;gap:4px">{band}</div>{tbl}</div>')


def headline_loop(ws: str) -> str:
    """Never-skippable text line for the loop dashboard: step, task progress,
    open gate. Printed to chat so status survives a skipped render (v1.5.3).
    GOVERNANCE CARRIER: it also discloses a blocked-on-budget run — when the
    contract meter hits its ceiling the headline SAYS so instead of reading
    as an idle loop. Composed from one full template ("headline_loop")."""
    state = _load_loop(ws)
    if not state:
        return "taskplane: no active loop"
    step = state.get("step", "—")
    tasks = state.get("tasks") or []
    done = sum(1 for t in tasks if t.get("status") in
               ("passed", "done", "external", "skipped", "not_selected",
                "reference"))
    goal = (state.get("goal") or "")[:60]
    gate = _msg("headline_loop_gate") if step in (
        "design_approval", "plan_approval", "signoff", "selection") else ""
    exhausted, used, mx = _budget_state(ws, tp.load_active(ws))
    budget = (_msg("headline_loop_budget", used=used, max=mx)
              if exhausted else "")
    return _msg("headline_loop", step=step, done=done, total=len(tasks),
                goal=goal, gate=gate, budget=budget)


def _budget_state(ws, contract):
    """(exhausted, used, max) for the active contract's action budget.
    Budget exhaustion is a HUMAN gate, but the loop step is unchanged — so
    without this the banner reads "no action needed" while the run is
    actually blocked waiting on the human to grant more actions. Shared by
    the widget gatebar AND headline_loop, so the never-skippable line
    discloses the blocked state too."""
    if not (contract and (contract.get("budget") or {}).get("max_actions")):
        return False, 0, 0
    budget_max = int(contract["budget"]["max_actions"])
    _tid = contract.get("task_id", "_")
    try:
        _mp = os.path.join(tp.tp_dir(ws), "meter.json")
        with open(_mp) as _f:
            budget_used = int((json.load(_f).get(_tid) or {})
                              .get("actions", 0))
    except (OSError, ValueError, TypeError):
        budget_used = 0
    return budget_used >= budget_max, budget_used, budget_max


def _widget_spine(state, step, tasks, sfx="s"):
    """The governance spine (rail) — pipe html + caption. `sfx` ("s"/"d")
    keeps DOM ids unique across the simple/detailed copies of the rail."""
    spine_step = "build" if step in _BUILD_STEPS else step
    # A/B loop: the Select gate is spliced in before Review — variants never
    # merge, one gets picked. Same splice rule as render(), shared from the
    # engine so the two rails can't drift.
    spine_rows = list(_SPINE)
    if not (state or {}).get("design_required"):
        spine_rows = [row for row in spine_rows
                      if row[0] not in ("design", "design_approval")]
    elif (state or {}).get("design_only"):
        spine_rows = [row for row in spine_rows
                      if row[0] in ("pm", "design", "design_approval", "done")]
    spine = _loop.splice_selection(spine_rows, state)
    order = [s[0] for s in spine]
    cur_i = order.index(spine_step) if spine_step in order else -1

    nodes = []
    for i, (sid, label, gate) in enumerate(spine):
        if sid == "build" and len(tasks) > 1:
            label = (f'Build <i class="ti ti-arrows-split" aria-hidden='
                     f'"true"></i> {len(tasks)} lanes')
        sq = "2px" if gate else "50%"
        if cur_i >= 0 and i < cur_i:
            dot = "background:var(--text-primary)"
            col, wt, bg = "var(--text-secondary)", "", ""
        elif i == cur_i:
            dot = "background:var(--surface-2)"
            col = "var(--surface-2)"
            bg = "background:var(--text-primary);"
            wt = " · you" if gate else ""
            if sid == "build" and step in _BUILD_STEPS:
                wt = f' · {step}'
        else:
            dot = "background:none;border:1.5px solid var(--border-strong)"
            col, wt, bg = "var(--text-muted)", "", ""
        visited = cur_i >= 0 and i <= cur_i
        # ids are per-view (s/d suffix): the rail renders once in the simple
        # view and once in the detailed view, and duplicate DOM ids made
        # tpSpine highlight the HIDDEN copy.
        click = (f' onclick="tpSpine(\'{sid}\')" id="tp-spine-{sfx}-{sid}" '
                 f'role="button" tabindex="0" onkeydown="{_KEYCLICK}" '
                 f'aria-label="stage {sid} — see how it was executed" '
                 f'class="tp-spine-n" title="see how this '
                 f'stage was executed"' if visited else "")
        nodes.append(
            f'<span{click} style="display:flex;align-items:center;gap:6px;'
            f'padding:5px 11px;border-radius:20px;font-family:'
            f'var(--font-mono);'
            f'font-size:12px;white-space:nowrap;{bg}color:{col};font-weight:'
            f'{"500" if i == cur_i else "400"}'
            f'{";cursor:pointer" if visited else ""}"><span style="width:7px;'
            f'height:7px;border-radius:{sq};flex:none;box-sizing:border-box;'
            f'{dot}"></span>{label}{wt}</span>')
        if i < len(spine) - 1:
            nodes.append('<span style="flex:1;min-width:6px;height:1px;'
                         'background:var(--border)"></span>')
    caption = ""
    if tasks:
        caption = (
            f'<div style="{_MICRO};margin:-8px 0 14px;padding:0 4px">inside '
            f'build each task runs build {_arrow()} evaluate ⟲ fix (≤2) — '
            f'lanes run '
            f'in parallel when scope-disjoint and deps are clear</div>')
    return ('<div style="display:flex;align-items:center;gap:2px;border:'
            '1px solid var(--border);border-radius:6px;'
            'padding:12px 14px;margin-bottom:14px;flex-wrap:wrap">'
            + "".join(nodes) + "</div>" + caption)


def _widget_gatebar(ws, state, step, tasks, budget_exhausted, budget_used,
                    budget_max):
    """The gate action bar — buttons grey out on click via tpFire()."""
    gatebar = ""
    btn = ('border:none;border-radius:6px;padding:9px 16px;font-size:'
           '13px;font-weight:500;cursor:pointer;font-family:var(--font-sans)')
    # the human gate is THE moment — an inverted block, white-on-black
    prim = f'{btn};background:var(--surface-2);color:var(--text-primary)'
    sec = (f'{btn};background:none;color:var(--surface-2);'
           f'border:1px solid var(--surface-2)')

    def gate_box(icon, title, sub, buttons, danger=False):
        bg = "var(--text-danger)" if danger else "var(--text-primary)"
        return (f'<div style="background:{bg};border-radius:6px;padding:'
                f'15px 16px;margin-bottom:14px;display:flex;justify-content:'
                f'space-between;align-items:center;gap:14px;flex-wrap:wrap">'
                f'<div><div style="font-weight:500;color:var(--surface-2)">'
                f'<i class="ti {icon}" aria-hidden="true"></i> {title}</div>'
                f'<div style="font-family:var(--font-mono);font-size:11.5px;'
                f'letter-spacing:.6px;color:var(--surface-2);opacity:.72;'
                f'margin-top:3px">{sub}</div>'
                f'</div><div style="display:flex;gap:8px">{buttons}</div></div>')

    if step == "design_approval":
        _derr = _loop._design_dod_errors(ws, state) if state else [
            "no design state"]
        if _derr:
            _dsub = _msg("design_dod_fail", n=len(_derr),
                         details=_esc("; ".join(_derr)[:150]))
        else:
            _dsub = "Design DoD ✅ alternatives, graph, contracts, risks, and acceptance mapped"
        b = (f'<button style="{prim}" onclick="tpFire(this,\'approve the '
             f'Design Contract\',\'approved\')"><i class="ti ti-check" '
             f'aria-hidden="true"></i> approve design</button><button '
             f'style="{sec}" onclick="tpFire(this,\'send the design back, I '
             f'want changes\')">request changes</button>')
        gatebar = gate_box("ti-drafting", "your gate — approve the HOW before "
                           "planning", _dsub, b, danger=bool(_derr))
    elif step == "plan_approval":
        n = len(tasks)
        b = (f'<button style="{prim}" onclick="tpFire(this,\'approve the plan\','
             f'\'approved\')"><i class="ti ti-check" aria-hidden="true"></i> '
             f'approve plan</button><button style="{sec}" onclick="tpFire(this,'
             f'\'send the plan back, I want changes\')">request changes</button>')
        gatebar = gate_box("ti-hand-stop", "your gate — nothing builds until "
                           "you approve", _msg("n_tasks_planned", n=n), b)
    elif step == "signoff":
        b = (f'<button style="{prim}" onclick="tpFire(this,\'sign off on this\','
             f'\'signed off\')"><i class="ti ti-check" aria-hidden="true"></i> '
             f'sign off</button><button style="{sec}" onclick="tpFire(this,'
             f'\'send it back, not ready to ship\')">send back</button>')
        # Mechanical DoD, shown right at the gate so the human signs off seeing
        # the scope-diff + lint verdict, not just the EM read-out.
        _dod = _loop._signoff_dod(ws, state) if state else {"passed": True,
                                                            "errors": []}
        if _dod["passed"]:
            _dsub = "all tasks reviewed · DoD ✅ diff in scope, KB lint clean"
        else:
            _dsub = _msg("signoff_dod_fail", n=len(_dod["errors"]),
                         details=_esc("; ".join(_dod["errors"])[:150]))
        gatebar = gate_box("ti-writing-sign", "your gate — EM review done, "
                           "final sign-off", _dsub, b,
                           danger=not _dod["passed"])
    elif step == "escalated":
        b = (f'<button style="{sec}" onclick="tpFire(this,\'retry the task\','
             f'\'retrying\')">retry</button><button style="{sec}" onclick='
             f'"tpFire(this,\'skip this task\',\'skipped\')">skip</button>'
             f'<button style="{btn};background:var(--surface-2);color:var(--'
             f'text-danger);border:1px solid var(--border-danger)" onclick='
             f'"tpFire(this,\'abort the loop\',\'aborted\')">abort</button>')
        gatebar = gate_box("ti-alert-triangle", "escalated — fix cycles "
                           "exhausted, your call", "choose how to proceed", b,
                           danger=True)
    elif step == "selection":
        variants = [t for t in tasks if t.get("variant")] or tasks
        # The variant/id come from agent-authored task data and are
        # interpolated into an onclick JS-STRING — use _jsattr (backslash-
        # escapes the JS quote so it survives HTML-entity decoding), NOT _attr
        # (entity-only, which the parser decodes back into a breakout quote —
        # the v0.9.5 XSS regression). _esc stays correct for the visible label.
        vb = "".join(
            f'<button style="{prim}" onclick="tpFire(this,'
            f'\'select variant {_jsattr(str(t.get("variant") or t["id"]))} '
            f'({_jsattr(str(t["id"]))}) as the winner\',\'selected\')">'
            f'<i class="ti ti-check" aria-hidden="true"></i> '
            f'{_esc(str(t.get("variant") or t["id"]))}</button>'
            for t in variants)
        vb += (f'<button style="{sec}" onclick="tpFire(this,\'select hybrid '
               f'— merge the best of both variants\',\'hybrid\')">⚡ hybrid'
               f'</button><button style="{sec}" onclick="tpFire(this,'
               f'\'send both variants back, neither ships\')">neither'
               f'</button>')
        gatebar = gate_box(
            "ti-arrows-split", "your gate — A/B selection: pick what ships",
            f"{len(variants)} variants built &amp; evaluated · they never "
            "merge, you choose", vb)
    elif step == "done":
        b = (f'<button style="{prim}" onclick="tpFire(this,\'run the retro\','
             f'\'retro queued\')"><i class="ti ti-flag" aria-hidden="true">'
             f'</i> run the retro</button>')
        gatebar = gate_box("ti-circle-check", "loop complete — nothing "
                           "pending", "retro closes it out", b)
    elif step == "failed":
        b = (f'<button style="{sec}" onclick="tpFire(this,\'start a new loop '
             f'for this goal\')">start over</button>')
        gatebar = gate_box("ti-alert-triangle", "loop failed", "review the "
                           "trace, then decide", b, danger=True)
    elif step and budget_exhausted:
        # The run is blocked on the action budget — a REAL human gate even
        # though the loop step didn't change. Make it loud and name the exact
        # (out-of-workspace) recovery, so the banner never says "no action
        # needed" while the agent is stuck against the wall.
        gb = (f'<button style="{prim}" onclick="tpFire(this,\'the action '
              f'budget is exhausted — grant 25 more actions from outside the '
              f'workspace\',\'granting\')"><i class="ti ti-plus" '
              f'aria-hidden="true"></i> approve 25 more</button>')
        gatebar = gate_box(
            "ti-hand-stop",
            f"action budget exhausted ({budget_used}/{budget_max}) — your call",
            "the agent is blocked at the wall; grant more actions (run from a "
            "directory OUTSIDE this workspace) or clear the contract to stop",
            gb, danger=True)
    elif step:
        # No human gate open — say so EXPLICITLY, so a status check answers
        # "is anything waiting on me?" at a glance, and name the next gate.
        role = STEP_ROLE_LABEL.get(step, step)
        cps = (state or {}).get("checkpoints") or []
        if (state or {}).get("design_required") and step in ("pm", "design"):
            nxt = "design approval"
        else:
            nxt = ("plan approval" if step in ("pm", "plan")
                   and "plan" in cps else "sign-off")
        gatebar = (
            f'<div style="border:1px solid var(--border);border-radius:6px;'
            f'padding:11px 16px;margin-bottom:14px;display:flex;'
            f'align-items:center;gap:10px;flex-wrap:wrap">'
            f'<span style="width:8px;height:8px;border-radius:50%;'
            f'background:var(--text-primary);flex:none" aria-hidden="true">'
            f'</span><span style="font-size:13px;color:var(--text-primary)">'
            f'no action needed from you</span>'
            f'<span style="font-family:var(--font-mono);font-size:11.5px;'
            f'color:var(--text-muted)">{_esc(role)} is on {_esc(step)} · '
            f'next human gate: {nxt}</span></div>')
    return gatebar


def _widget_lanes(state, step, tasks, contract, hmap, hmain):
    """Build lanes — one per task, each its own mini-pipeline + live meter."""
    cur_id = (tasks[(state or {}).get("current_task", 0)].get("id")
              if tasks and (state or {}).get("current_task", 0) < len(tasks)
              else None)
    def lane_meter(t):
        if t.get("id") in hmap:                      # parallel worker
            return hmap[t.get("id")]
        if (hmain and step in _BUILD_STEPS
                and t.get("id") == cur_id):          # serial current task
            return hmain
        return None
    cards = [_lane(t, step, lane_meter(t)) for t in tasks]
    if not cards and contract:
        ro = contract.get("read_only")
        sc = _esc(", ".join(contract["coding"]["scope_paths"]
                  or contract.get("write_allow") or ["(any)"]))
        cards.append(
            f'<div style="border:1px solid var(--border);border-radius:8px;'
            f'padding:9px 11px"><div style="font-weight:500">'
            f'{_esc(STEP_ROLE_LABEL.get(step, step))}</div><div style="font-'
            f'size:12px;color:var(--text-secondary);margin-top:3px">'
            f'{"read-only" if ro else "build"} · <code style="font-family:'
            f'var(--font-mono);font-size:11px">{sc}</code></div></div>')
    return "".join(cards) or ('<div style="font-size:13px;color:var('
                              '--text-muted)">no active tasks</div>')


def _widget_feed(trace):
    """The live feed — newest events first, one degraded-but-visible row per
    event (unknown events render with a fallback icon, never crash)."""
    feed = []
    for e in trace:
        ic, cc = _ICON.get(e["event"], ("ti-point", "s"))
        detail = ""
        if e["event"] == "loop_step":
            detail = f'{e.get("step","")} ({e.get("role","")})'
        elif e["event"] == "hook_deny":
            who = f'[{e["_agent"]}] ' if e.get("_agent") else ""
            detail = f'{who}{e.get("tool","")} out of scope'
        elif e["event"] == "budget_deny":
            detail = (f'{e.get("used","")}/{e.get("max","")} actions — '
                      f'harness stopped')
        elif e["event"] == "loop_gate":
            detail = f'{e.get("step","")} = {e.get("outcome","")}'
        elif e["event"] == "lens_route":
            detail = _msg("n_lenses", n=len(e.get("lenses", [])))
        elif e["event"] == "loop_wave":
            detail = f'ready: {", ".join(e.get("ready",[]))}'
        elif e["event"] == "refinement_gate":
            detail = f'{e.get("task","")} · {e.get("score","")}'
        elif e["event"] == "graph_impact":
            detail = f'{e.get("impacted",0)} modules'
        label = e["event"].replace("_", " ")
        feed.append(
            f'<div style="display:flex;gap:8px;align-items:baseline;padding:'
            f'6px 2px;border-bottom:1px solid var(--border);font-size:13px">'
            f'<i class="ti {ic}" style="color:{_ICOLOR[cc]}" aria-hidden='
            f'"true"></i><span>{_esc(label)} <span style="color:var(--text-'
            f'secondary)">{_esc(detail)}</span></span></div>')
    return "".join(feed) or ('<div style="font-size:13px;color:var(--'
                             'text-muted)">no events yet</div>')


def _widget_ministats(metrics, totals):
    """Run stats as a compact strip — oversized mono numerals."""
    def cell(v, l, hot=False):
        col = "var(--text-danger)" if hot else "var(--text-primary)"
        return (f'<div style="padding:8px 6px;text-align:center"><div style='
                f'"font-size:17px;font-weight:500;font-family:var(--font-'
                f'mono);color:{col}">{v}</div><div style="{_MICRO}">{l}'
                f'</div></div>')
    return (
        f'<div style="{_CARD};padding:8px;margin-bottom:12px"><div style="'
        f'display:grid;grid-template-columns:repeat(3,1fr)">'
        + cell(metrics["agents"], "agents") + cell(metrics["waves"], "waves")
        + cell(metrics["fixes"], "fixes")
        + cell(totals["actions"], "actions")
        + cell(metrics["blocks"], "blocks", hot=bool(metrics["blocks"]))
        + cell(metrics["steps"], "steps") + '</div></div>')


def _widget_dor(full_trace, step):
    """DoR strip — the entry-gate verdict for the CURRENT step, surfaced
    from the latest loop_step trace. `full_trace` is newest-first."""
    dor_ev = next((e for e in full_trace
                   if e.get("event") == "loop_step"), None)
    if dor_ev is None or step in ("done", "failed") \
            or dor_ev.get("dor_ready") is None:
        return ""
    _rdy = dor_ev.get("dor_ready")
    _blk = dor_ev.get("dor_blockers") or []
    _wrn = dor_ev.get("dor_warnings") or []
    if not _rdy:
        _dc, _dl, _dd = ("var(--text-danger)", "NOT READY",
                         _esc("; ".join(_blk)))
    elif _wrn:
        _dc, _dl, _dd = ("var(--text-warning,var(--text-primary))",
                         "ready", _msg("dor_warnings", n=len(_wrn),
                                       details=_esc("; ".join(_wrn))))
    else:
        _dc, _dl, _dd = ("var(--text-success,var(--text-primary))",
                         "ready", "")
    return (
        f'<div style="border:1px solid var(--border);border-radius:6px;'
        f'padding:8px 13px;margin-bottom:14px;display:flex;align-items:'
        f'center;gap:9px;flex-wrap:wrap"><span style="{_MICRO}">DoR</span>'
        f'<span style="font-size:12.5px;font-weight:500;color:{_dc}">'
        f'{_dl}</span>'
        + (f'<span style="font-size:12px;color:var(--text-secondary)">'
           f'{_dd}</span>' if _dd else "")
        + f'<span style="{_MICRO};margin-inline-start:auto">entry gate · '
          f'{_esc(step)}</span></div>')


# The widget's client-side controller — a static block (moved out of the
# 494-line widget() megafunction). tpFire feature-detects the chat bridge
# FIRST: in the standalone artifact (no window.sendPrompt) a click reveals
# the exact reply to type in chat (via tpHint) instead of falsely rendering
# "✓ approved" for a message that never went anywhere.
_WIDGET_JS = (
    '<script>' + _SEND_JS +
    'function tpFire(b,m,l){'
    'if(!window.sendPrompt){tpHint(b,m);return;}'
    'b.disabled=true;'
    'b.style.background="var(--surface-0)";'
    'b.style.color="var(--text-muted)";b.style.border="none";'
    'b.style.cursor="default";'
    'if(l)b.innerHTML="<i class=\'ti ti-check\'></i> "+l;'
    'Array.from(b.parentNode.querySelectorAll("button")).forEach('
    'function(x){if(x!==b){x.disabled=true;x.style.opacity="0.45";'
    'x.style.cursor="default";}});sendPrompt(m);}'
    'function tpTab(w){["loop","map"].forEach('
    'function(k){var p=document.getElementById("tp-panel-"+k),'
    'b=document.getElementById("tp-tab-"+k);if(!p||!b)return;'
    'var on=k===w;p.style.display=on?"block":"none";'
    'b.style.background=on?"var(--text-primary)":"none";'
    'b.style.color=on?"var(--surface-2)":"var(--text-secondary)";'
    # non-color active cues: aria-selected for SRs, weight+underline for
    # low vision — parity with the findings filter chips (v2.2.1).
    'b.setAttribute("aria-selected",on?"true":"false");'
    'b.style.textDecoration=on?"underline":"none";});}'
    'function tpView(v){var s=document.getElementById("tp-simple"),'
    'd=document.getElementById("tp-detail"),'
    'bs=document.getElementById("tp-vb-simple"),'
    'bd=document.getElementById("tp-vb-detail");'
    'if(!s||!d||!bs||!bd)return;var on=v==="detail";'
    's.style.display=on?"none":"block";d.style.display=on?"block":"none";'
    'function st(b,a){b.style.background=a?"var(--text-primary)":"none";'
    'b.style.color=a?"var(--surface-2)":"var(--text-secondary)";'
    'b.setAttribute("aria-pressed",a?"true":"false");'
    'b.style.textDecoration=a?"underline":"none";}'
    'st(bs,!on);st(bd,on);}'
    'var tpMap={pm:["pm"],design:["design"],'
    'design_approval:["design_approval"],plan:["plan"],'
    'plan_approval:["plan_approval"],'
    'build:["execute","evaluate","fix","escalated","resolve"],'
    'selection:["selection"],em:["em"],signoff:["signoff"],'
    'done:["done"]};'
    'function tpSpine(sid){var steps=tpMap[sid]||[sid];'
    'var td=document.getElementById("tp-detail");'
    'var sfx=td&&td.style.display==='
    '"block"?"d":"s";var j=document.getElementById("tp-journey-"+sfx);'
    'if(!j)return;var best=-1,n=0;'
    'while(true){var b=document.getElementById("tpj"+sfx+n+"-b");'
    'if(!b)break;if(steps.indexOf(b.getAttribute("data-step"))>=0)'
    'best=n;n++;}'
    'if(best>=0){tpJ(sfx,best);'
    # spine node ids are per-view (tp-spine-<sfx>-<sid>) — the same rail is
    # rendered in both the simple and detailed views, and highlighting must
    # land on the VISIBLE copy, not a hidden duplicate id.
    'var ns=document.getElementsByClassName("tp-spine-n");'
    'for(var q=0;q<ns.length;q++){if(ns[q].style.background.indexOf('
    '"text-primary")<0){ns[q].style.background="none";'
    'ns[q].removeAttribute("aria-current");}}'
    'var me=document.getElementById("tp-spine-"+sfx+"-"+sid);'
    'if(me){me.setAttribute("aria-current","true");'
    'if(me.style.background.indexOf("text-primary")<0)'
    'me.style.background="var(--surface-0)";}'
    'j.scrollIntoView({behavior:"smooth",block:"nearest"});}}'
    'tpView("simple");tpTab("loop");</script>')

# Responsive fallback for the widget's fixed two-column grids: inline styles
# win over stylesheet rules, so the collapse uses !important — below ~640px
# the loop panel and journey grids degrade to one column instead of forcing
# horizontal overflow on phones / narrow sidebars.
_WIDGET_CSS = (
    '<style>@media (max-width:640px){.tp-grid2,.tp-jgrid{'
    'grid-template-columns:1fr!important}}</style>')


def _widget_parts(ws: str) -> dict:
    """Load state ONCE and build every named part of the loop dashboard.
    widget() assembles them into one fragment; widget_paged() assembles the
    same parts into ordered pages under the byte budget — one source of
    truth for both render paths."""
    state = _load_loop(ws)
    contract = tp.load_active(ws)
    step = (state or {}).get("step", "—")
    goal = _esc((state or {}).get("goal", "no active loop"))[:80]
    tasks = (state or {}).get("tasks") or []
    parallel = bool((state or {}).get("parallel"))
    tstats = {}
    all_ev = _read_trace_all(ws, stats=tstats)   # the trace: parsed ONCE
    trace = all_ev[-8:][::-1]
    full_trace = all_ev[::-1]
    denials = sum(1 for e in full_trace
                  if e["event"] in ("hook_deny", "budget_deny"))
    metrics = _run_metrics(ws, tasks, contract, events=all_ev)
    harness = _harness_agents(ws)
    hmap = {h["tag"]: h for h in harness if h["tag"]}
    hmain = next((h for h in harness if not h["tag"]), None)
    totals = _meter_totals(ws)
    budget_exhausted, budget_used, budget_max = _budget_state(ws, contract)

    pipe_s = _widget_spine(state, step, tasks, "s")
    pipe_d = _widget_spine(state, step, tasks, "d")
    gatebar = _widget_gatebar(ws, state, step, tasks, budget_exhausted,
                              budget_used, budget_max)
    cards_html = _widget_lanes(state, step, tasks, contract, hmap, hmain)
    feed_html = _widget_feed(trace)

    lanes_title = ("build lanes · parallel" if parallel and len(tasks) > 1
                   else "build lanes" if len(tasks) > 1 else
                   "build lane" if tasks else "tasks &amp; contracts")
    ministats = _widget_ministats(metrics, totals)
    feed_panel = (f'<div style="{_CARD}"><div style="{_MICRO};'
                  f'margin-bottom:10px">live feed</div>'
                  f'<div aria-live="polite" aria-atomic="false">'
                  f'{feed_html}</div></div>')
    lanes_panel = (f'<div style="{_CARD}"><div style="{_MICRO};margin-'
                   f'bottom:10px">{lanes_title}</div><div style="display:'
                   f'flex;flex-direction:column;gap:8px">{cards_html}'
                   f'</div></div>')
    loop_panel = (
        f'<div class="tp-grid2" style="display:grid;'
        f'grid-template-columns:1.25fr 1fr;'
        f'gap:12px">{lanes_panel}<div>'
        f'{ministats}{feed_panel}</div></div>')

    # graph + context merged into one "map" tab — the codebase context
    # (hubs, blast radius) above the work context (requirement, lenses, KB)
    graph_html = _graph_panel(ws, tasks)
    context_html = (
        _context_panel(ws, state, full_trace)
        + f'<div style="{_MICRO};margin-top:10px">action budgets are '
        'hook-enforced; dollar spend stays cooperative in the plugin.</div>')
    map_panel = (graph_html + '<div style="height:14px"></div>'
                 + context_html)

    # ---- simple view: the focus points only — where we are, your gate,
    # and each agent's harness (on topic + within budget)
    hcards = "".join(_harness_card(h) for h in harness)
    if not hcards:
        why = ("waiting at a human gate — no agent is running"
               if step in ("design_approval", "plan_approval", "signoff",
                           "escalated", "done")
               else "no contract active — workspace ungoverned")
        hcards = (f'<div style="font-size:13px;color:var(--text-muted)">'
                  f'{why}</div>')
    n_pass = sum(1 for t in tasks if t.get("status") == "passed")
    prog = (f'<div style="font-size:12px;color:var(--text-muted);margin-top:'
            f'10px">'
            + _msg("tasks_progress", done=n_pass, total=len(tasks),
                   actions=totals["actions"], blocked=denials)
            + '</div>' if tasks else "")
    hero = _agents_hero(harness, tasks, step, parallel)
    harness_panel = (
        f'<div style="background:none;border:1px solid '
        f'var(--border);border-radius:6px;padding:14px"><div style="'
        f'font-family:var(--font-mono);font-size:10.5px;letter-spacing:1.2px;color:var(--text-muted);'
        f'margin-bottom:10px">agent harnesses — on topic · within budget'
        f'</div><div style="display:flex;flex-direction:column;gap:8px">'
        f'{hcards}</div>{prog}</div>')

    tabbtn = ('border:none;background:none;font-family:var(--font-mono);'
              'font-size:12px;letter-spacing:.8px;font-weight:500;'
              'padding:6px 14px;cursor:pointer;border-radius:20px;'
              'color:var(--text-secondary)')
    tabs = "".join(
        f'<button id="tp-tab-{k}" role="tab" '
        f'aria-selected="{"true" if k == "loop" else "false"}" '
        f'style="{tabbtn}'
        + (';background:var(--text-primary);color:var(--surface-2);'
           'text-decoration:underline' if k == "loop" else "")
        + f'" onclick="tpTab(\'{k}\')">{lbl}</button>'
        for k, lbl in (("loop", "loop"), ("map", "graph &amp; context")))
    tabs = f'<div role="tablist" style="display:flex;gap:6px">{tabs}</div>'
    vbtn = ('border:none;background:none;font-family:var(--font-mono);'
            'font-size:11.5px;letter-spacing:.8px;font-weight:500;'
            'padding:4px 11px;cursor:pointer;border-radius:20px;'
            'color:var(--text-secondary)')
    toggle = (
        f'<div style="display:flex;gap:2px;border:1px solid var(--border);'
        f'border-radius:20px;padding:2px"><button id="tp-vb-simple" '
        f'aria-pressed="true" '
        f'style="{vbtn}" onclick="tpView(\'simple\')">simple</button>'
        f'<button id="tp-vb-detail" aria-pressed="false" '
        f'style="{vbtn}" '
        f'onclick="tpView(\'detail\')">detailed</button></div>')
    step_badge = _esc(step.replace("_", " "))
    dor_html = _widget_dor(full_trace, step)

    # Dashboard v2 (R-0001): journey navigator + always-on stats band.
    visits = _journey(ws, events=all_ev, state=state)
    journey_s = render_journey(visits, "s")
    journey_d = render_journey(visits, "d")
    stats_html = render_stats(ws, metrics, denials, "s")

    header = (
        f'<div style="display:flex;justify-content:'
        f'space-between;align-items:flex-start;margin-bottom:12px;gap:12px">'
        f'<div><div style="font-size:16px;font-weight:500">taskplane mission '
        f'control</div><div style="font-size:13px;color:var(--text-'
        f'secondary)">goal: {goal}{" · parallel" if parallel else ""}</div>'
        f'</div><div style="display:flex;gap:10px;align-items:center">'
        f'<span style="border:1px solid var(--border-strong);color:'
        f'var(--text-primary);border-radius:20px;padding:4px 12px;'
        f'font-family:var(--font-mono);font-size:11.5px;letter-spacing:.8px;'
        f'font-weight:500;white-space:nowrap">step: {step_badge}</span>'
        f'{toggle}</div></div>')
    sr = (f'<h2 class="sr-only">taskplane mission control: the governed loop '
          f'is at step {step_badge} for goal {goal}.'
          + (' The action budget is exhausted — a human must grant more '
             'actions.' if budget_exhausted else '') + '</h2>')
    notice = _trace_notice(tstats)
    return {
        "sr": sr, "header": header, "notice": notice, "hero": hero,
        "gatebar": gatebar, "dor": dor_html, "stats": stats_html,
        "pipe_s": pipe_s, "pipe_d": pipe_d,
        "journey_s": journey_s, "journey_d": journey_d,
        "harness_panel": harness_panel, "loop_panel": loop_panel,
        "lanes_panel": lanes_panel, "ministats": ministats,
        "feed_panel": feed_panel, "graph": graph_html,
        "context": context_html, "map_panel": map_panel, "tabs": tabs,
        "step_badge": step_badge, "goal": goal, "visits": visits,
    }


def widget(ws: str) -> str:
    """Return an inline HTML fragment for mcp__visualize__show_widget. Opens
    with a live parallel-agents hero band on top (when agents are active),
    then simple/detailed views. Gate buttons grey out on click (only when the
    chat bridge exists — in the static artifact they reveal the reply to type
    instead). Composition of the named parts from _widget_parts()."""
    p = _widget_parts(ws)
    return (
        p["sr"] + _WIDGET_CSS
        + f'<div dir="auto" style="padding:0.5rem 0;'
          f'font-family:var(--font-sans);color:'
          f'var(--text-primary)">' + p["header"]
        + p["notice"]
        + p["hero"]
        + p["gatebar"]
        + p["dor"]
        + p["stats"]
        + f'<div id="tp-simple">{p["pipe_s"]}{p["journey_s"]}'
          f'{p["harness_panel"]}</div>'
        + f'<div id="tp-detail">'
          f'<div style="margin-bottom:14px;border-bottom:'
          f'1px solid var(--border);padding-bottom:10px">{p["tabs"]}</div>'
          f'<div id="tp-panel-loop">{p["pipe_d"]}{p["journey_d"]}'
          f'{p["loop_panel"]}</div>'
          f'<div id="tp-panel-map">{p["map_panel"]}</div></div></div>'
        + _WIDGET_JS)


def _page_bytes(html: str) -> int:
    """The ENFORCED budget unit: UTF-8 bytes of the emitted fragment."""
    return len(html.encode("utf-8"))


def _fit_page(html: str, budget: int) -> str:
    """Guarantee a page fits the BYTE budget. Content is only ever removed
    via _truncate_marked (an explicit '+N more' marker naming the omission
    and pointing at the full dashboard.html) — never silently."""
    if _page_bytes(html) <= budget:
        return html
    char_budget = budget
    for _ in range(32):
        out = _truncate_marked(html, char_budget)
        over = _page_bytes(out) - budget
        if over <= 0:
            return out
        char_budget -= max(over, 16)
    return _truncate_marked(html, max(256, budget // 2))


def _wrap_page(sr: str, body: str) -> str:
    """A self-contained paged fragment: sr heading + widget chrome."""
    return (sr + _WIDGET_CSS
            + '<div dir="auto" style="padding:0.5rem 0;'
              'font-family:var(--font-sans);color:var(--text-primary)">'
            + body + '</div>')


def widget_paged(ws: str, budget: int = PAGE_BUDGET) -> list:
    """The loop dashboard as ordered pages (v1.5.3 contract, ENFORCED in
    v2.3.0): every page is a self-contained fragment whose emitted UTF-8
    size — wrapper included — is <= budget. When the full widget fits it is
    returned as ONE page (no behavior change for small states); otherwise it
    is split by MEANING: status+gate+spine → journey → lanes+feed →
    graph → context, splitting further (journey by visits) when a page is
    still too big. Content leaves a page only via an explicit '+N more'
    marker — never silently. Returns [{"title","html"}]."""
    full = widget(ws)
    if _page_bytes(full) <= budget:
        return [{"title": "mission control", "html": full}]

    p = _widget_parts(ws)
    pages = []

    def add(title, body, sr_text=None):
        sr = (f'<h2 class="sr-only">{_esc(sr_text or title)}</h2>'
              if sr_text is not False else "")
        pages.append({"title": title, "html": _wrap_page(sr, body)})

    # page 1 — status & gate: header, notices, hero, gate banner, DoR,
    # stats, spine, harnesses. The governance carriers (gate banner, budget
    # exhaustion, DoR) all live on the FIRST page.
    #
    # v2.3.1: the <style>/<script> CHROME is fixed — kept OUTSIDE the
    # truncatable region — so a byte-fit trim can only ever remove panel
    # content, never cut off _WIDGET_JS (which wires the gate buttons:
    # tpFire/tpSend/tpView/tpTab). Without this, a page 1 over budget got
    # tail-truncated by _fit_page straight through the trailing <script>,
    # leaving the emitted gate buttons calling undefined functions.
    p1_prefix = (p["sr"] + _WIDGET_CSS
                 + '<div dir="auto" style="padding:0.5rem 0;'
                   'font-family:var(--font-sans);'
                   'color:var(--text-primary)">')
    p1_suffix = '</div>' + _WIDGET_JS
    p1_fixed_bytes = _page_bytes(p1_prefix) + _page_bytes(p1_suffix)
    p1_body = (p["header"] + p["notice"] + p["hero"] + p["gatebar"]
               + p["dor"] + p["stats"] + p["pipe_s"] + p["harness_panel"])
    p1_body = _fit_page(p1_body, max(256, budget - p1_fixed_bytes))
    pages.append({"title": "mission control — status & gate",
                  "html": p1_prefix + p1_body + p1_suffix})

    # page 2+ — the step journey (split by visits when oversized)
    visits = p["visits"]
    if visits:
        j = _wrap_page("", render_journey(visits, "s"))
        if _page_bytes(j) <= budget:
            add("step journey", render_journey(visits, "s"),
                "step journey — every traversed step with its decisions.")
        else:
            n_chunks = 2
            while n_chunks <= max(2, len(visits)):
                size = max(1, (len(visits) + n_chunks - 1) // n_chunks)
                chunks = [visits[i:i + size]
                          for i in range(0, len(visits), size)]
                rendered = [_wrap_page("", render_journey(c, f"s{ci}"))
                            for ci, c in enumerate(chunks)]
                if all(_page_bytes(r) <= budget for r in rendered) \
                        or size == 1:
                    for ci, c in enumerate(chunks):
                        add(f"step journey (part {ci + 1}/{len(chunks)})",
                            render_journey(c, f"s{ci}"),
                            f"step journey part {ci + 1} of {len(chunks)}.")
                    break
                n_chunks += 1

    # build lanes + live feed (split apart if together they exceed budget)
    lanes_feed = p["lanes_panel"] + '<div style="height:12px"></div>' \
        + p["ministats"] + p["feed_panel"]
    if _page_bytes(_wrap_page("", lanes_feed)) <= budget:
        add("build lanes & live feed", lanes_feed,
            "build lanes and the live event feed.")
    else:
        add("build lanes", p["lanes_panel"], "build lanes.")
        add("live feed", p["ministats"] + p["feed_panel"],
            "run stats and the live event feed.")

    # graph, then context (already the two natural halves of the map tab)
    add("dependency graph", p["graph"], "dependency graph.")
    add("context", p["context"],
        "requirement, lenses, decisions and debt context.")

    n = len(pages)
    for i, page in enumerate(pages, 1):
        page["title"] = f"{page['title']} — {i}/{n}"
        # ENFORCED: no page ships over the byte budget; last-resort trims
        # are explicit '+N more' markers, never silent drops.
        page["html"] = _fit_page(page["html"], budget)
    return pages
