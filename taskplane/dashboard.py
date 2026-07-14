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
}

# Role label per step — sourced from the engine (loop.STEP_ROLE), not a
# second hand-maintained copy.
STEP_ROLE_LABEL = _loop.STEP_ROLE


def _read_trace(ws: str, limit: int = 24) -> list:
    """Main trace + any parallel worker traces (.tp-work/*/.taskplane),
    so mission control shows EVERY agent's events, blocks included."""
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
        with open(p) as f:
            for ln in f:
                if not ln.strip():
                    continue
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue   # a truncated/partial worker record — skip it,
                               # don't crash the whole render on one bad line
                if tag != ".taskplane":
                    e["_agent"] = tag
                evts.append(e)
    evts.sort(key=lambda e: e.get("ts", 0))
    return evts[-limit:][::-1]


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


def render(ws: str, out: str | None = None) -> str:
    state = _load_loop(ws)
    trace = _read_trace(ws)
    counts = _counts(ws)
    contract = tp.load_active(ws)

    step = (state or {}).get("step", "—")
    goal = (state or {}).get("goal", "no active loop")
    tasks = (state or {}).get("tasks") or []
    parallel = bool((state or {}).get("parallel"))
    denials = sum(1 for e in _read_trace(ws, 9999) if e["event"] == "hook_deny")

    # pipeline: mark done / current / gate-waiting. Derived from the engine's
    # single source (loop.display_pipeline) — fix is a side-loop, hidden here.
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
        pipe_html.append(
            f'<div class="node {cls}"><span class="dot"></span>'
            f'<span class="nl">{label}{wait}</span></div>')
        if i < len(main) - 1:
            pipe_html.append('<div class="conn"></div>')
    if step == "fix":
        pipe_html.append('<div class="fixflag">↻ FIX cycle in progress</div>')

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
        awaiting = {"plan_approval": "Review the plan, then approve.",
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
            extra = f' → {e.get("step","")} ({e.get("role","")})'
        elif e["event"] == "hook_deny":
            who = f'[{e["_agent"]}] ' if e.get("_agent") else ""
            extra = f' {who}{e.get("tool","")}: {str(e.get("reason",""))[:50]}'
        elif e["event"] == "loop_gate":
            extra = f' {e.get("step","")} = {e.get("outcome","")}'
        elif e["event"] == "lens_route":
            ls = e.get("lenses", [])
            extra = f' {len(ls)} lens(es)'
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
        .replace("__PIPE__", "".join(pipe_html)) \
        .replace("__AGENTS__", "".join(agent_cards)) \
        .replace("__ROSTER__", roster) \
        .replace("__FEED__", feed_html) \
        .replace("__STATS__", stats)
    out = out or os.path.join(tp.tp_dir(ws), "dashboard.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(html)
    return out


_TEMPLATE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>taskplane — mission control</title><style>
*{box-sizing:border-box;margin:0}
body{font:13.5px/1.5 -apple-system,'Segoe UI',Inter,sans-serif;
background:#14140f;color:#e8e8e2;padding:18px 22px}
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
.node .nl{font-size:12.5px;white-space:nowrap;color:#8a8a80}
.node.done .dot{background:#1baf7a}.node.done .nl{color:#c9c9c2}
.node.cur{background:#26261c}.node.cur .dot{background:#eda100;
animation:pulse 1.2s infinite;box-shadow:0 0 0 4px rgba(237,161,0,.15)}
.node.cur .nl{color:#fff;font-weight:600}
.node.gate .dot{border-radius:3px}
.node.gate.cur{background:#2a1f14}.node.gate.cur .dot{background:#e34948;
box-shadow:0 0 0 4px rgba(227,73,72,.2)}
.node.gate.cur .nl{color:#ff9d6e}
.conn{flex:1;min-width:10px;height:2px;background:#33332a}
.fixflag{color:#eb6834;font-size:12px;font-weight:600;margin-left:10px;
padding:5px 10px;background:#2a1a12;border-radius:7px}
/* grid */
.grid{display:grid;grid-template-columns:1.1fr 1fr;gap:14px}
.card{background:#1c1c15;border:1px solid #33332a;border-radius:6px;padding:14px}
.card h2{font-size:11px;text-transform:uppercase;letter-spacing:.8px;
color:#77776c;margin-bottom:11px}
/* agents */
.agent{border:1px solid #33332a;border-radius:9px;padding:10px 12px;
margin-bottom:9px;border-left:4px solid #44443a}
.agent.running{border-left-color:#1baf7a}.agent.built{border-left-color:#eda100}
.agent.passed{border-left-color:#1baf7a}.agent.failed{border-left-color:#e34948}
.agent.idle{border-left-color:#44443a}
.ah{display:flex;justify-content:space-between;align-items:center;
margin-bottom:5px}.ah b{color:#fff;font-size:13.5px}
.ameta{color:#9a9a90;font-size:11.5px;margin-top:2px}
.ameta code{background:#26261c;color:#c9c9a2;padding:1px 5px;border-radius:4px;
font-size:11px}
.badge{font-size:10.5px;padding:2px 8px;border-radius:99px;font-weight:600;
text-transform:uppercase;letter-spacing:.4px}
.badge.running{background:#123a2b;color:#3fd99a}
.badge.built,.badge.pending,.badge.queued{background:#3a2f12;color:#f0c04a}
.badge.passed{background:#123a2b;color:#3fd99a}
.badge.failed{background:#3a1616;color:#ff7a78}
.badge.idle{background:#26261c;color:#9a9a90}
.roster{width:100%;border-collapse:collapse;margin-top:10px;font-size:12px}
.roster th{text-align:left;color:#77776c;font-weight:600;padding:4px 8px;
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
.legend{color:#66665c;font-size:11px;margin-top:12px}
</style></head><body>
<h1><span class="live"></span> taskplane — mission control
<span style="color:#77776c;font-weight:400;font-size:12px">· step
<b style="color:#eda100">__STEP__</b> · __MODE__</span></h1>
<div class="goal">goal: <b>__GOAL__</b></div>
<div class="pipe">__PIPE__</div>
<div class="grid">
 <div class="card"><h2>Agents &amp; contracts</h2>__AGENTS____ROSTER__</div>
 <div class="card"><h2>Live feed (newest first)</h2>
  <ul class="feed">__FEED__</ul></div>
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
        f'<div style="{_CARD};margin-top:8px;border-left:3px solid {rcol};'
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
    "med": (2, "medium", "var(--text-primary)", "var(--border-strong)"),
    "medium": (2, "medium", "var(--text-primary)", "var(--border-strong)"),
    "low": (3, "low", "var(--text-muted)", "var(--border)"),
}
_SEV_KEY = {"blocker": "high", "high": "high", "med": "med",
            "medium": "med", "low": "low"}


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
        sev = str(f.get("severity", "med")).lower()
        key = _SEV_KEY.get(sev, "med")
        norm.append({**f, "_key": key, "_rank": _SEV.get(sev, _SEV["med"])[0]})
    norm.sort(key=lambda x: (x["_rank"], str(x.get("domain", "")),
                             str(x.get("file", ""))))
    counts = {k: sum(1 for f in norm if f["_key"] == k)
              for k in ("high", "med", "low")}
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
             + chip("low", "low", counts["low"]))

    # one card per finding
    cards = []
    for i, f in enumerate(norm):
        sev = str(f.get("severity", "med")).lower()
        _, slabel, dot, accent = _SEV.get(sev, _SEV["med"])
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
            f'border-left:3px solid {accent};border-radius:0 6px 6px 0;'
            f'margin-bottom:8px">'
            f'<div style="display:flex;align-items:baseline;gap:9px;'
            f'flex-wrap:wrap"><span style="width:8px;height:8px;border-radius:'
            f'50%;background:{dot};flex:none;align-self:center"></span>'
            f'<span style="font-family:var(--font-mono);font-size:10px;'
            f'letter-spacing:1px;color:{dot}">{_esc(slabel).upper()}</span>'
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
        f'font-family:var(--font-mono);font-size:11.5px;white-space:nowrap">'
        f'{_esc(tests)}</span>' if tests else "")

    gate_html = ""
    if meta.get("gate"):
        gate_html = (
            f'<div style="background:var(--text-primary);border-radius:6px;'
            f'padding:14px 16px;margin-top:14px;display:flex;'
            f'justify-content:space-between;align-items:center;gap:12px;'
            f'flex-wrap:wrap"><div style="font-weight:500;color:'
            f'var(--surface-2)"><i class="ti ti-writing-sign" '
            f'aria-hidden="true"></i> {_esc(meta.get("gate_title", "your call — the review is the deliverable"))}'
            f'</div><div style="display:flex;gap:8px;flex-wrap:wrap">'
            + "".join(
                f'<button onclick="if(window.sendPrompt)sendPrompt('
                f'&#39;{_jsattr(b["prompt"])}&#39;)" style="border:'
                f'{"none" if b.get("primary") else "1px solid var(--surface-2)"};'
                f'border-radius:6px;padding:9px 15px;font-size:13px;'
                f'font-weight:500;cursor:pointer;font-family:var(--font-sans);'
                f'background:{"var(--surface-2)" if b.get("primary") else "none"};'
                f'color:{"var(--text-primary)" if b.get("primary") else "var(--surface-2)"}">'
                f'{_esc(b["label"])}</button>'
                for b in meta.get("gate_buttons", []))
            + '</div></div>')

    title = _esc(meta.get("title", "review findings"))
    subtitle = _esc(meta.get("subtitle", ""))
    note = (f'<div style="{_MICRO};margin-top:10px">{_esc(meta["note"])}</div>'
            if meta.get("note") else "")

    frag = (
        f'<h2 class="sr-only">Review findings: {counts["high"]} high, '
        f'{counts["med"]} medium, {counts["low"]} low. Filter by severity '
        f'and expand each for the failure scenario and fix.</h2>'
        f'<div style="padding:0.5rem 0;font-family:var(--font-sans);color:'
        f'var(--text-primary)">'
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:flex-start;gap:12px;margin-bottom:12px"><div>'
        f'<div style="font-size:16px;font-weight:500">{title}</div>'
        f'<div style="font-size:13px;color:var(--text-secondary)">{subtitle}'
        f'</div></div>{tests_pill}</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px" '
        f'id="tpf-chips">{chips}</div>'
        f'<div id="tpf-list">{cards_html}</div>'
        f'{clean_html}{gate_html}{note}'
        f'<script>'
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
                f'{n} finding{"s" if n != 1 else ""}' if n
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
        f'</div><span style="font-family:var(--font-mono);font-size:11px;'
        f'color:var(--text-muted);white-space:nowrap">{_esc(phase)}</span>'
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
            f'font-weight:400;margin-left:8px">{_esc(c.get("detail",""))}'
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
        return (f'<button style="{style}" onclick="if(window.sendPrompt)'
                f'sendPrompt(&#39;{_jsattr(prompt)}&#39;)">{_esc(label)}</button>')

    if nxt == "attach_folder":
        headline = "Let's give taskplane a place to work"
        sub = ("Connect the folder you want to work in — then I'll set up "
               "the rest. Nothing's attached yet.")
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
        f'your machine.</div></div>')

    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w") as fh:
            fh.write(frag)
    return frag


def _run_metrics(ws, tasks, contract):
    """Real run metrics from the trace (agents, steps, waves, fixes, blocks)
    + the advisory budget. Model tokens are COOPERATIVE in the plugin — the
    paid proxy runtime measures real spend; here we surface the ceiling."""
    full = _read_trace(ws, 99999)
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
        return (f'<div style="{_MICRO};margin-top:6px">{used} action(s) · '
                f'no ceiling set</div>')
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
    ("pm", "Define", False), ("plan", "Plan", False),
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
        f'font-size:10.5px">{lbl}</span></div><div style="font-size:12px;'
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
                '</code>.</div>')
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
        f'padding:3px 0"><span style="flex:none;width:150px;overflow:hidden;'
        f'text-overflow:ellipsis;white-space:nowrap;color:var(--text-'
        f'secondary)">{_esc(m)}</span><span style="flex:1;height:8px;'
        f'background:var(--surface-0);border-radius:4px;overflow:hidden">'
        f'<span style="display:block;height:100%;width:{int(100 * d / mx)}%;'
        f'background:var(--text-primary);border-radius:2px"></span></span>'
        f'<span style="flex:none;width:26px;text-align:right;color:'
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
                f'color:var(--text-muted)">({_esc(e.get("kind", ""))} ← '
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
                f'margin-bottom:6px">{im.get("total_impacted", 0)} dependent '
                f'module(s) within 3 hops</div>{rows}{more}</div>')
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
                f'font-size:10.5px;margin-left:4px">→ {_esc(r)}</span>'
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
            f'var(--text-muted);font-size:12px;white-space:nowrap">'
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
            f'{_MICRO};margin-top:4px;padding-left:15px">{_esc(act)}</div>'
            f'<div style="font-size:11px;color:var(--text-secondary);margin-'
            f'top:2px;padding-left:15px"><code style="font-family:var(--font-'
            f'mono);font-size:10.5px">{scope}</code></div>{budget}</div>')
    dot_h = ('<span style="width:7px;height:7px;border-radius:50%;background:'
             'var(--text-primary);flex:none"></span>' if n_run else
             '<i class="ti ti-check" aria-hidden="true"></i>')
    return (
        f'<div style="border:1px solid var(--border-strong);border-radius:6px;'
        f'padding:12px 14px;margin-bottom:14px"><div style="display:flex;'
        f'align-items:center;gap:8px;margin-bottom:10px"><i class="ti ti-'
        f'arrows-split" aria-hidden="true" style="color:var(--text-primary)">'
        f'</i><span style="font-weight:500;font-size:14px">{verb} {head_n} '
        f'agent{"s" if head_n != 1 else ""}</span>'
        f'{"" if not parallel else "<span style=" + chr(34) + _MICRO + chr(34) + ">· parallel wave</span>"}'
        f'<span style="flex:1"></span>'
        f'<span style="{_MICRO}">{n_done}/{head_n} done</span></div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap">{"".join(chips)}'
        f'</div></div>')


def widget(ws: str) -> str:
    """Return an inline HTML fragment for mcp__visualize__show_widget. Opens
    with a live parallel-agents hero band on top (when agents are active),
    then simple/detailed views. Gate buttons grey out on click."""
    state = _load_loop(ws)
    trace = _read_trace(ws, 8)
    c = _counts(ws)
    contract = tp.load_active(ws)
    step = (state or {}).get("step", "—")
    goal = _esc((state or {}).get("goal", "no active loop"))[:80]
    tasks = (state or {}).get("tasks") or []
    parallel = bool((state or {}).get("parallel"))
    full_trace = _read_trace(ws, 9999)
    denials = sum(1 for e in full_trace
                  if e["event"] in ("hook_deny", "budget_deny"))
    metrics = _run_metrics(ws, tasks, contract)
    harness = _harness_agents(ws)
    hmap = {h["tag"]: h for h in harness if h["tag"]}
    hmain = next((h for h in harness if not h["tag"]), None)
    totals = _meter_totals(ws)

    # Budget exhaustion is a HUMAN gate, but the loop step is unchanged — so
    # without this the banner reads "no action needed" while the run is
    # actually blocked waiting on the human to grant more actions. Detect it
    # from the active contract's meter and surface it (see the gatebar below).
    budget_exhausted = False
    budget_used = budget_max = 0
    if contract and (contract.get("budget") or {}).get("max_actions"):
        budget_max = int(contract["budget"]["max_actions"])
        _tid = contract.get("task_id", "_")
        try:
            _mp = os.path.join(tp.tp_dir(ws), "meter.json")
            with open(_mp) as _f:
                budget_used = int((json.load(_f).get(_tid) or {})
                                  .get("actions", 0))
        except (OSError, ValueError, TypeError):
            budget_used = 0
        budget_exhausted = budget_used >= budget_max

    spine_step = "build" if step in _BUILD_STEPS else step
    # A/B loop: the Select gate is spliced in before Review — variants never
    # merge, one gets picked. Same splice rule as render(), shared from the
    # engine so the two rails can't drift.
    spine = _loop.splice_selection(_SPINE, state)
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
        nodes.append(
            f'<span style="display:flex;align-items:center;gap:6px;padding:'
            f'5px 11px;border-radius:20px;font-family:var(--font-mono);'
            f'font-size:12px;white-space:nowrap;{bg}color:{col};font-weight:'
            f'{"500" if i == cur_i else "400"}"><span style="width:7px;'
            f'height:7px;border-radius:{sq};flex:none;box-sizing:border-box;'
            f'{dot}"></span>{label}{wt}</span>')
        if i < len(spine) - 1:
            nodes.append('<span style="flex:1;min-width:6px;height:1px;'
                         'background:var(--border)"></span>')
    caption = ""
    if tasks:
        caption = (
            f'<div style="{_MICRO};margin:-8px 0 14px;padding:0 4px">inside '
            f'build each task runs build → evaluate ⟲ fix (≤2) — lanes run '
            f'in parallel when scope-disjoint and deps are clear</div>')
    pipe = ('<div style="display:flex;align-items:center;gap:2px;border:'
            '1px solid var(--border);border-radius:6px;'
            'padding:12px 14px;margin-bottom:14px;flex-wrap:wrap">'
            + "".join(nodes) + "</div>" + caption)

    # gate action bar — buttons grey out on click via tpFire()
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

    if step == "plan_approval":
        n = len(tasks)
        b = (f'<button style="{prim}" onclick="tpFire(this,\'approve the plan\','
             f'\'approved\')"><i class="ti ti-check" aria-hidden="true"></i> '
             f'approve plan</button><button style="{sec}" onclick="tpFire(this,'
             f'\'send the plan back, I want changes\')">request changes</button>')
        gatebar = gate_box("ti-hand-stop", "your gate — nothing builds until "
                           "you approve", f"{n} task(s) planned", b)
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
            _dsub = (f"all tasks reviewed · DoD ❌ {len(_dod['errors'])} "
                     f"issue(s): {_esc('; '.join(_dod['errors'])[:150])}")
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

    # build lanes — one per task, each its own mini-pipeline + live meter
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
    cards_html = "".join(cards) or ('<div style="font-size:13px;color:var('
                                    '--text-muted)">no active tasks</div>')

    # live feed
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
            detail = f'{len(e.get("lenses",[]))} lens(es)'
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
    feed_html = "".join(feed) or ('<div style="font-size:13px;color:var(--'
                                  'text-muted)">no events yet</div>')

    lanes_title = ("build lanes · parallel" if parallel and len(tasks) > 1
                   else "build lanes" if len(tasks) > 1 else
                   "build lane" if tasks else "tasks &amp; contracts")

    # run stats as a compact side strip inside the loop tab (no own tab) —
    # oversized mono numerals, the monochrome way
    def cell(v, l, hot=False):
        col = "var(--text-danger)" if hot else "var(--text-primary)"
        return (f'<div style="padding:8px 6px;text-align:center"><div style='
                f'"font-size:17px;font-weight:500;font-family:var(--font-'
                f'mono);color:{col}">{v}</div><div style="{_MICRO}">{l}'
                f'</div></div>')
    ministats = (
        f'<div style="{_CARD};padding:8px;margin-bottom:12px"><div style="'
        f'display:grid;grid-template-columns:repeat(3,1fr)">'
        + cell(metrics["agents"], "agents") + cell(metrics["waves"], "waves")
        + cell(metrics["fixes"], "fixes")
        + cell(totals["actions"], "actions")
        + cell(metrics["blocks"], "blocks", hot=bool(metrics["blocks"]))
        + cell(metrics["steps"], "steps") + '</div></div>')

    loop_panel = (
        f'<div style="display:grid;grid-template-columns:1.25fr 1fr;'
        f'gap:12px"><div style="{_CARD}"><div style="{_MICRO};margin-'
        f'bottom:10px">{lanes_title}</div><div style="display:flex;flex-'
        f'direction:column;gap:8px">{cards_html}</div></div><div>'
        f'{ministats}<div style="{_CARD}"><div style="{_MICRO};'
        f'margin-bottom:10px">live feed</div>'
        f'{feed_html}</div></div></div>')

    # graph + context merged into one "map" tab — the codebase context
    # (hubs, blast radius) above the work context (requirement, lenses, KB)
    map_panel = (
        _graph_panel(ws, tasks)
        + '<div style="height:14px"></div>'
        + _context_panel(ws, state, full_trace)
        + f'<div style="{_MICRO};margin-top:10px">action budgets are '
        'hook-enforced; dollar spend stays cooperative in the plugin.</div>')

    # ---- simple view: the focus points only — where we are, your gate,
    # and each agent's harness (on topic + within budget)
    hcards = "".join(_harness_card(h) for h in harness)
    if not hcards:
        why = ("waiting at a human gate — no agent is running"
               if step in ("plan_approval", "signoff", "escalated", "done")
               else "no contract active — workspace ungoverned")
        hcards = (f'<div style="font-size:13px;color:var(--text-muted)">'
                  f'{why}</div>')
    n_pass = sum(1 for t in tasks if t.get("status") == "passed")
    prog = (f'<div style="font-size:12px;color:var(--text-muted);margin-top:'
            f'10px">{n_pass}/{len(tasks)} task(s) passed · {totals["actions"]}'
            f' actions metered · {denials} blocked</div>' if tasks else "")
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
        f'<button id="tp-tab-{k}" style="{tabbtn}'
        + (';background:var(--text-primary);color:var(--surface-2)'
           if k == "loop" else "")
        + f'" onclick="tpTab(\'{k}\')">{lbl}</button>'
        for k, lbl in (("loop", "loop"), ("map", "graph &amp; context")))
    vbtn = ('border:none;background:none;font-family:var(--font-mono);'
            'font-size:11.5px;letter-spacing:.8px;font-weight:500;'
            'padding:4px 11px;cursor:pointer;border-radius:20px;'
            'color:var(--text-secondary)')
    toggle = (
        f'<div style="display:flex;gap:2px;border:1px solid var(--border);'
        f'border-radius:20px;padding:2px"><button id="tp-vb-simple" '
        f'style="{vbtn}" onclick="tpView(\'simple\')">simple</button>'
        f'<button id="tp-vb-detail" style="{vbtn}" '
        f'onclick="tpView(\'detail\')">detailed</button></div>')
    step_badge = _esc(step.replace("_", " "))
    script = (
        '<script>function tpFire(b,m,l){b.disabled=true;'
        'b.style.background="var(--surface-0)";'
        'b.style.color="var(--text-muted)";b.style.border="none";'
        'b.style.cursor="default";'
        'if(l)b.innerHTML="<i class=\'ti ti-check\'></i> "+l;'
        'Array.from(b.parentNode.querySelectorAll("button")).forEach('
        'function(x){if(x!==b){x.disabled=true;x.style.opacity="0.45";'
        'x.style.cursor="default";}});if(window.sendPrompt)sendPrompt(m);}'
        'function tpTab(w){["loop","map"].forEach('
        'function(k){var p=document.getElementById("tp-panel-"+k),'
        'b=document.getElementById("tp-tab-"+k);if(!p||!b)return;'
        'var on=k===w;p.style.display=on?"block":"none";'
        'b.style.background=on?"var(--text-primary)":"none";'
        'b.style.color=on?"var(--surface-2)":"var(--text-secondary)";});}'
        'function tpView(v){var s=document.getElementById("tp-simple"),'
        'd=document.getElementById("tp-detail"),'
        'bs=document.getElementById("tp-vb-simple"),'
        'bd=document.getElementById("tp-vb-detail");var on=v==="detail";'
        's.style.display=on?"none":"block";d.style.display=on?"block":"none";'
        'function st(b,a){b.style.background=a?"var(--text-primary)":"none";'
        'b.style.color=a?"var(--surface-2)":"var(--text-secondary)";}'
        'st(bs,!on);st(bd,on);}'
        'tpView("simple");tpTab("loop");</script>')
    # DoR strip — the entry-gate verdict for the CURRENT step, surfaced from
    # the latest loop_step trace (was computed every `loop next` but never
    # shown). Ready/blocked/warnings, so the human sees readiness at a glance.
    dor_html = ""
    dor_ev = next((e for e in full_trace
                   if e.get("event") == "loop_step"), None)
    if dor_ev is not None and step not in ("done", "failed") \
            and dor_ev.get("dor_ready") is not None:
        _rdy = dor_ev.get("dor_ready")
        _blk = dor_ev.get("dor_blockers") or []
        _wrn = dor_ev.get("dor_warnings") or []
        if not _rdy:
            _dc, _dl, _dd = ("var(--text-danger)", "NOT READY",
                             _esc("; ".join(_blk)))
        elif _wrn:
            _dc, _dl, _dd = ("var(--text-warning,var(--text-primary))",
                             "ready", f"{len(_wrn)} warning(s): "
                             + _esc("; ".join(_wrn)))
        else:
            _dc, _dl, _dd = ("var(--text-success,var(--text-primary))",
                             "ready", "")
        dor_html = (
            f'<div style="border:1px solid var(--border);border-radius:6px;'
            f'padding:8px 13px;margin-bottom:14px;display:flex;align-items:'
            f'center;gap:9px;flex-wrap:wrap"><span style="{_MICRO}">DoR</span>'
            f'<span style="font-size:12.5px;font-weight:500;color:{_dc}">'
            f'{_dl}</span>'
            + (f'<span style="font-size:12px;color:var(--text-secondary)">'
               f'{_dd}</span>' if _dd else "")
            + f'<span style="{_MICRO};margin-left:auto">entry gate · '
              f'{_esc(step)}</span></div>')

    return (
        f'<h2 class="sr-only">taskplane mission control: the governed loop is '
        f'at step {step_badge} for goal {goal}.</h2>'
        f'<div style="padding:0.5rem 0;font-family:var(--font-sans);color:'
        f'var(--text-primary)"><div style="display:flex;justify-content:'
        f'space-between;align-items:flex-start;margin-bottom:12px;gap:12px">'
        f'<div><div style="font-size:16px;font-weight:500">taskplane mission '
        f'control</div><div style="font-size:13px;color:var(--text-'
        f'secondary)">goal: {goal}{" · parallel" if parallel else ""}</div>'
        f'</div><div style="display:flex;gap:10px;align-items:center">'
        f'<span style="border:1px solid var(--border-strong);color:'
        f'var(--text-primary);border-radius:20px;padding:4px 12px;'
        f'font-family:var(--font-mono);font-size:11.5px;letter-spacing:.8px;'
        f'font-weight:500;white-space:nowrap">step: {step_badge}</span>'
        f'{toggle}</div></div>'
        f'{hero}'
        f'{gatebar}'
        f'{dor_html}'
        f'<div id="tp-simple">{pipe}{harness_panel}</div>'
        f'<div id="tp-detail">'
        f'<div style="display:flex;gap:6px;margin-bottom:14px;border-bottom:'
        f'1px solid var(--border);padding-bottom:10px">{tabs}</div>'
        f'<div id="tp-panel-loop">{pipe}{loop_panel}</div>'
        f'<div id="tp-panel-map">{map_panel}</div></div></div>'
        + script)
