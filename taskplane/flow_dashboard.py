"""One shared view of protected decisions and separately labelled observations."""
from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any

if __package__:
    from . import flow as _package_flow
    from . import depgraph as _package_depgraph
    flow, depgraph = _package_flow, _package_depgraph
else:
    import flow as _flat_flow
    import depgraph as _flat_depgraph
    flow, depgraph = _flat_flow, _flat_depgraph


PHASES = ('product', 'design', 'plan', 'build', 'evaluate', 'engineering', 'retro')
STYLE = '''
<style>
.tp-flow{--good:#286657;--good-bg:#e8f1ec;--warn:#965023;max-width:1180px;margin:auto;color:var(--text-primary,#25241f);font:14px/1.6 var(--font-sans,system-ui)}
.wrap:has(.tp-flow){max-width:1224px}.tp-flow *{box-sizing:border-box}.tp-flow h1{font-size:clamp(25px,4vw,38px);line-height:1.18;letter-spacing:-1.1px;max-width:850px;margin:10px 0 16px}.tp-flow h2{font-size:19px;letter-spacing:-.35px;margin:0}.tp-flow h3{font-size:14px;margin:0 0 6px}.tp-flow p{margin:5px 0 12px}.tp-flow .muted{color:var(--text-secondary,#666158);font-size:12px}.tp-flow .eyebrow{font:11px var(--font-mono,monospace);text-transform:uppercase;letter-spacing:1.6px;color:var(--text-secondary)}
.tp-flow .topline,.tp-flow .section-head{display:flex;justify-content:space-between;gap:14px;align-items:center}.tp-flow .pill{display:inline-block;padding:4px 10px;border:1px solid var(--border,#ddd9d0);border-radius:20px;font:11px var(--font-mono,monospace);white-space:nowrap}.tp-flow .outcome{border-left:3px solid var(--warn);padding:12px 16px;background:var(--surface-1,#f7f6f3);margin:22px 0;overflow-wrap:anywhere}
.tp-flow nav{display:flex;gap:20px;flex-wrap:wrap;padding:15px 0;border-block:1px solid var(--border,#ddd9d0);margin:24px 0}.tp-flow a{color:inherit;text-underline-offset:4px}.tp-flow .metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:20px 0 26px}.tp-flow .metric{padding:15px 18px;border:1px solid var(--border,#ddd9d0);border-radius:9px}.tp-flow .metric b{font-size:26px;font-weight:550;letter-spacing:-.7px;display:block}
.tp-flow section{margin:34px 0}.tp-flow .section-head{margin-bottom:14px}.tp-flow .stages{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px}.tp-flow .stage{border:1px solid var(--border,#ddd9d0);border-radius:8px;padding:12px 10px;background:var(--surface-1,#f7f6f3)}.tp-flow .stage strong{display:block;font-size:12px}.tp-flow .stage small{font-size:10px;color:var(--text-secondary)}.tp-flow .stage.recorded{border-top:3px solid var(--good)}.tp-flow .stage.current{outline:2px solid var(--good)}
.tp-flow details{border-bottom:1px solid var(--border,#ddd9d0);padding:12px 0}.tp-flow summary{cursor:pointer;font-weight:550}.tp-flow .note{margin:10px 0;padding:10px 13px;background:var(--surface-1,#f7f6f3);border-radius:5px;overflow-wrap:anywhere}.tp-flow .table-wrap{overflow:auto;border:1px solid var(--border,#ddd9d0);border-radius:9px}.tp-flow table{border-collapse:collapse;width:100%;font-size:12px}.tp-flow th,.tp-flow td{text-align:left;border-bottom:1px solid var(--border,#ddd9d0);padding:12px 14px;vertical-align:top}.tp-flow th{background:var(--surface-1,#f7f6f3);font-size:10px;letter-spacing:.6px;text-transform:uppercase}.tp-flow td code{font-size:11px;overflow-wrap:anywhere}.tp-flow pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.65 var(--font-mono,monospace);max-height:440px;overflow:auto;background:var(--surface-1,#f7f6f3);padding:14px;border-radius:7px}
.tp-flow .task-chain{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}.tp-flow .task-chain span{border:1px solid var(--border,#ddd9d0);border-radius:6px;padding:7px 12px;font-size:12px}.tp-flow iframe{width:100%;height:690px;border:1px solid var(--border,#ddd9d0);border-radius:10px;background:var(--surface-2,#fff)}.tp-flow .review-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.tp-flow .review{border:1px solid var(--border,#ddd9d0);border-radius:9px;padding:16px;min-width:0}.tp-flow footer{border-top:1px solid var(--border,#ddd9d0);padding:20px 0;color:var(--text-secondary);font-size:11px;overflow-wrap:anywhere}
@media(prefers-color-scheme:dark){.tp-flow{--good:#93cab4;--good-bg:#243d31;--warn:#e7ac81}}
@media(max-width:760px){.tp-flow .metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.tp-flow .stages{grid-template-columns:repeat(2,minmax(0,1fr))}.tp-flow .review-grid{grid-template-columns:1fr}.tp-flow .topline{align-items:flex-start}.tp-flow nav{gap:12px}.tp-flow iframe{height:760px}}
</style>
'''


def model(ws: str) -> dict[str, Any] | None:
    return flow.report(Path(ws))


def _e(value: Any) -> str:
    return escape(str(value if value is not None else '—'), quote=True)


def _number(value: Any) -> str:
    return f'{value:,}' if isinstance(value, int) else 'Unknown'


def _evidence(ws: str, path: str) -> str:
    try:
        source = flow.artifact(Path(ws), str(path))
        with source.open(encoding='utf-8') as stream:
            text = stream.read(48001)
        suffix = '\n[Preview limited to 48,000 characters. Full evidence remains in the workspace.]' if len(text) > 48000 else ''
        return f'<pre>{_e(text[:48000] + suffix)}</pre>'
    except (OSError, ValueError, UnicodeError):
        return '<p class="muted">Evidence unavailable in this workspace.</p>'


def headline(m: dict[str, Any]) -> str:
    return f"taskplane: {m['status']} · {m['phase']} · {len(m['tasks'])} tasks · {len(m['reviews'])} lens reviews · {_number((m.get('tokens') or {}).get('total_tokens'))} flow tokens"


def _workflow(m: dict[str, Any]) -> str:
    authority = m.get('workflow') or {}
    visits = authority.get('visits') or []
    html = '<section id="workflow"><div class="section-head"><h2>01 / Workflow</h2><span class="muted">Work, evidence and human decisions</span></div><div class="stages">'
    details = ''
    if visits:
        details = '<p class="muted">Authorized route: ' + _e(' → '.join(v['phase'].title() for v in visits if not v.get('superseded'))) + '</p>'
        if any(v.get('superseded') for v in visits):
            details += '<p class="muted">Visit history: ' + _e(' → '.join(v['phase'].title() for v in visits)) + '</p>'
        for i, visit in enumerate(visits):
            phase = visit['phase']
            anchor = 'phase-' + phase + '-' + visit['id']
            decision = visit['decision']
            packet = visit.get('packet')
            superseded = visit.get('superseded')
            active = i == authority['index'] and not authority.get('finished')
            work = 'Produced' if packet else 'In progress' if active else 'Not started'
            validated = 'Stale' if decision == 'stale' else 'Validated' if packet else 'Not submitted'
            human = decision.replace('_', ' ').capitalize()
            style = 'recorded' if decision == 'approved' and not superseded else 'current' if active else 'pending'
            suffix = ' · Superseded history' if superseded else ''
            html += f'<a class="stage {style}" href="#{_e(anchor)}"><strong>{_e(phase.title())}</strong><small>Work: {_e(work)}<br>Evidence: {_e(validated)}<br>Human decision: {_e(human + suffix)}</small></a>'
            details += f'<details id="{_e(anchor)}"><summary>{_e(phase.title())} · {_e(human + suffix)}</summary><p>Visit {_e(visit["id"])}</p>'
            if packet:
                details += f'<p>Checkpoint {_e(packet.get("checkpoint"))}</p><p class="muted">Evidence validated against the submitted scope. Validation does not establish that every criterion passed.</p>'
                decisions = [d for d in authority.get('decisions', {}).values()
                             if d.get('binding', {}).get('visit') == visit['id']]
                for d in decisions:
                    details += f'<p class="muted">Human event {_e(d.get("event_id"))} · {_e(d.get("choice"))} · reviewed revision {_e(d.get("binding", {}).get("revision"))} · packet {_e(d.get("binding", {}).get("manifest_digest"))}</p>'
                details += '<pre>' + _e(json.dumps(packet.get('output', {}), indent=2)) + '</pre>'
            details += '</details>'
    else:
        history = [m.get('entry_phase') or 'product'] + [n.get('phase') for n in m['milestones']]
        order = list(dict.fromkeys([p for p in history if p in PHASES] + list(PHASES)))
        for phase in order:
            notes = [n for n in m['milestones'] if n.get('phase') == phase]
            html += f'<a class="stage pending" href="#phase-{phase}"><strong>{phase.title()}</strong><small>Work: {"Recorded" if notes else "Not recorded"}<br>Evidence: Unverified<br>Human decision: Unverified</small></a>'
            details += f'<details id="phase-{phase}"><summary>{phase.title()} · {len(notes)} recorded updates</summary>'
            details += ''.join(f'<div class="note"><span class="muted">{_e(n.get("at"))}</span><p>{_e(n.get("note"))}</p></div>' for n in notes) or '<p class="muted">No milestone recorded. Completion is not inferred.</p>'
            details += '</details>'
        details = '<p class="muted">Legacy observations are unverified. Progress, task completion and finish notes do not establish human acceptance.</p>' + details
    return html + '</div>' + details + '</section>'


def sections(ws: str, m: dict[str, Any]) -> list[tuple[str, str]]:
    tasks, reviews = m['tasks'], m['reviews']
    usage = m.get('tokens') or {}
    coverage = m.get('token_coverage') or {}
    completed = sum(t.get('status') in {'complete', 'completed', 'passed', 'done'} for t in tasks)
    header = f'''<header><div class="topline"><span class="eyebrow">Taskplane / Delivery overview</span><span class="pill">{_e(m['status'])} · {_e(m['phase'])}</span></div>
<h1>{_e(m.get('goal'))}</h1><p class="muted">Owner: orchestrator · Started {_e(m.get('started_at'))} · Finished {_e(m.get('finished_at'))}</p></header>'''
    if m.get('outcome'):
        header += f'<div class="outcome"><strong>Recorded outcome</strong><p>{_e(m["outcome"])}</p></div>'
    authority = m.get('workflow') or {}
    if authority.get('profile') == 'native_workflow' and authority.get('workflow_available'):
        header += '<div class="outcome" role="status"><strong>Workflow gates active; host-wide protection unavailable</strong><p>Decisions use observed conversation provenance and local state. Native permissions govern tools; complete containment and process census are unavailable.</p></div>'
    elif not authority.get('authority_verified'):
        header += f'<div class="outcome" role="status"><strong>Host governance unverified</strong><p>{_e(authority.get("detail") or "No protected decision record is available. Historical observations remain visible.")}</p></div>'
    else:
        header += '<p class="muted">Human decisions come from protected host records. This dashboard is a read-only snapshot and cannot grant approval.</p>'
    native = authority.get('native_observations') or {}
    capabilities = authority.get('capabilities') or {}
    header += '<details id="native-support"><summary>Native host support</summary>'
    header += f'<p class="muted">Host: {_e(capabilities.get("host") or native.get("host"))} · Plugin: {_e(native.get("plugin_version"))}. Discovery and hook activity do not grant approval.</p>'
    header += '<div class="table-wrap"><table><thead><tr><th>Protection</th><th>Current adapter evidence</th></tr></thead><tbody>'
    for key, label in [('protected_store', 'Protected approval storage'), ('human_origin', 'Human approval source'),
                       ('tool_containment', 'Tool scope enforcement'), ('process_tracking', 'Process revocation')]:
        value = capabilities.get(key)
        status = 'Confirmed by adapter' if value is True else 'Unverified' if value is False else 'Unknown'
        header += f'<tr><td>{label}</td><td>{status}</td></tr>'
    header += '</tbody></table></div></details>'
    header += '<nav aria-label="Dashboard sections">'+''.join(f'<a href="#{key}">{name}</a>' for key, name in [('workflow','Workflow'),('decomposition','Tasks'),('dependencies','Dependency graph'),('lenses','Lenses'),('telemetry','Tokens'),('evidence','Evidence')])+'</nav>'
    header += '<div class="metrics">'+''.join(f'<div class="metric"><span class="muted">{label}</span><b>{value}</b></div>' for label,value in [('Tasks complete',f'{completed} / {len(tasks)}'),('Lens reviews',str(len(reviews))),('Flow tokens',_number(usage.get('total_tokens'))),('Measured sessions',str(coverage.get('measured_sessions',0)))])+'</div>'
    for error in m.get('evidence_errors',[]):
        header += f'<p role="status">{_e(error)}</p>'
    stages = _workflow(m)
    plan = '<section id="decomposition"><div class="section-head"><h2>02 / Task decomposition</h2><span class="muted">Dependencies and execution evidence</span></div>'
    if tasks:
        plan += '<div class="task-chain">'+''.join(f'<span>{_e(t.get("id"))} ← {_e(", ".join(t.get("dependencies",t.get("deps",[]))) or "No prerequisites")}</span>' for t in tasks)+'</div>'
        plan += '<div class="table-wrap"><table><thead><tr><th>Task</th><th>Depends on</th><th>State</th><th>Execution / verification</th></tr></thead><tbody>'
        for t in tasks:
            timing=f'{t.get("started_at") or "Not started"} → {t.get("completed_at") or "Not completed"}'
            plan += f'<tr><td><strong>{_e(t.get("id"))}</strong><br>{_e(t.get("title"))}</td><td>{_e(", ".join(t.get("dependencies",t.get("deps",[]))) or "—")}</td><td>{_e(t.get("status","unknown"))}</td><td><span class="muted">{_e(timing)}</span><br><code>{_e(t.get("verification","Not recorded"))}</code>'
            if t.get('premature_completed_at'):
                plan += '<br><span class="muted">Earlier completion corrected; history retained in task evidence.</span>'
            plan += '</td></tr>'
        plan += '</tbody></table></div>'
    else:
        plan += '<p class="muted">No task decomposition attached to this run.</p>'
    plan += '</section>'
    graph = '<section id="dependencies"><div class="section-head"><h2>03 / Dependency graph</h2><span class="muted">Source dependencies and change impact</span></div>'
    paths = m['artifacts'].get('changed') or list(dict.fromkeys(p for t in tasks for p in t.get('paths',t.get('scope',[]))))
    try:
        doc = depgraph.html_document(ws, changed_files=paths)
        graph += '<p class="muted">Impact is computed from '+('the attached change set' if m['artifacts'].get('changed') else 'the attached task scopes')+'. Dependencies point from consumer to dependency.</p>'
        graph += f'<iframe title="Interactive dependency graph" sandbox="allow-scripts" srcdoc="{_e(doc)}"></iframe>'
    except (OSError, ValueError, KeyError, TypeError):
        graph += '<p class="muted">Dependency graph unavailable. Run graph scan for this workspace.</p>'
    graph += '</section>'
    session_map = {identity:s for s in m.get('sessions',[])
                   for identity in (s['agent'], s.get('session')) if identity}
    lenses = '<section id="lenses"><div class="section-head"><h2>04 / Lens reviews</h2><span class="muted">Native reviewers and their evidence</span></div><div class="review-grid">'
    for r in reviews:
        session = session_map.get(r.get('agent'),{})
        lenses += f'<article class="review"><div class="topline"><h3>{_e(r.get("lens","Review"))}</h3><span class="pill">{_e(r.get("phase","review"))}</span></div><p class="muted">{_e(r.get("agent"))}</p><p>{_number((session.get("usage") or {}).get("total_tokens"))} tokens · {_e(session.get("status","unavailable"))}</p>'
        if r.get('evidence'):
            lenses += f'<details><summary>Review findings and disposition</summary>{_evidence(ws,r["evidence"])}</details>'
        lenses += '</article>'
    lenses += '</div>' + ('<p class="muted">No review index attached to this run.</p>' if not reviews else '') + '</section>'
    tokens = '<section id="telemetry"><div class="section-head"><h2>05 / Token usage</h2><span class="muted">Native counters · no token gate</span></div>'
    tokens += f'<p class="muted">{_e(coverage.get("basis"))}</p><p>{coverage.get("measured_sessions",0)} measured · {coverage.get("unmeasured_sessions",0)} unmeasured · {coverage.get("partial_sessions",0)} partial sessions</p>'
    tokens += '<div class="table-wrap"><table><thead><tr><th>Session</th><th>Cached input</th><th>Uncached input</th><th>Output</th><th>Total</th></tr></thead><tbody>'
    for session in m.get('sessions',[]):
        u=session.get('usage') or {}
        tokens += '<tr><td>'+_e(session['agent'])+'<br><span class="muted">'+_e(session['role'])+' · '+_e(session['status'])+'</span></td>'+''.join('<td>'+_number(u.get(k))+'</td>' for k in ['cached_input_tokens','uncached_input_tokens','output_tokens','total_tokens'])+'</tr>'
    tokens += '</tbody></table></div>'
    tokens += f'<p class="muted">Native lifetime totals for delivery sessions: {_number((m.get("native_tokens") or {}).get("total_tokens"))}. Flow totals subtract the root start baseline. Host approval review is separate. Cached input is included in input; reasoning is included in output. Token counts are not a billing estimate.</p></section>'
    evidence = '<section id="evidence"><h2>06 / Evidence and Retro</h2>'
    for path in m['artifacts'].get('evidence',[]):
        evidence += f'<details><summary>{_e(path)}</summary>{_evidence(ws,path)}</details>'
    evidence += '</section>'
    footer = f'<footer>Run {_e(m["run"])} · Snapshot of recorded evidence and current native counters. Regenerate the dashboard to refresh. Historical findings remain visible after fixes.</footer>'
    return [('Overview', header),('Workflow',stages),('Task decomposition',plan),('Dependency graph',graph),('Lens reviews',lenses),('Token usage',tokens),('Evidence and Retro',evidence+footer)]


def render(ws: str, m: dict[str, Any] | None = None) -> str:
    m = m or model(ws)
    if m is None:
        return ""
    return STYLE+'<main class="tp-flow">'+''.join(body for _,body in sections(ws,m))+'</main>'
