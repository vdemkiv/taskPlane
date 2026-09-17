"""Reconcile advisory usage with native session metadata, including unhooked lenses."""
from __future__ import annotations

from typing import Any
from datetime import datetime
import os
from pathlib import Path

if __package__:
    from . import native_session_meter as _package_meter
    from . import claude_flow_usage as _package_claude
    claude = _package_claude
    meter = _package_meter
else:
    import native_session_meter as _flat_meter
    import claude_flow_usage as _flat_claude
    claude = _flat_claude
    meter = _flat_meter


def _time(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return None


def _codex_sessions(run: dict[str, Any], cutoff: float | None) -> tuple[list[dict[str, Any]], int]:
    root = run['session']
    started = _time(run.get('at'))
    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions'
    metadata = []
    discovery_errors = 0
    for path in home.glob('**/*.jsonl'):
        try:
            with path.open('rb') as stream:
                # Session metadata is the leading record, not the conversation.
                prefix = stream.readline(meter.MAX_METADATA_BYTES)
            item, _ = meter._session_metadata(prefix)
            item['path'] = path
            metadata.append(item)
        except (OSError, ValueError):
            discovery_errors += 1
    ids = {root}
    selected = []
    while True:
        selected = [m for m in metadata if m['session_id'] in ids or (
            m.get('parent_session_id') in ids
            and (m.get('thread_source') == 'guardian_review' or started is None
                 or (_time(m['started_at']) or 0) >= started)
            and (cutoff is None or (_time(m['started_at']) or 0) < cutoff))]
        found = ids | {m['session_id'] for m in selected}
        if found == ids:
            break
        ids = found
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in selected:
        grouped.setdefault(item['session_id'], []).append(item)
    grouped.setdefault(root, [])
    sessions = []
    for sid, segments in grouped.items():
        meta = segments[0] if segments else {}
        role = ('orchestrator' if sid == root else 'host_approval_review'
                if meta.get('thread_source') == 'guardian_review' else 'lens')
        errors = []
        try:
            snapshot = meter.read_logical_snapshot(
                [segment['path'] for segment in segments], sid, at_or_before=cutoff)
            native = snapshot['usage']
            if snapshot['partial']:
                errors.append('native counter unavailable')
        except (OSError, ValueError):
            native = None
            if segments:
                errors.append('segment counters could not be reconciled')
        baseline = run.get('usage') if sid == root else None
        usage = ({k: max(0, v - (baseline or {}).get(k, 0)) for k, v in native.items()}
                 if native and (sid != root or baseline is not None) else None)
        sessions.append({'session': sid, 'agent': meta.get('agent_path') or role,
                         'role': role, 'usage': usage, 'native_usage': native,
                         'status': 'partial' if errors else 'measured' if usage else 'unavailable',
                         'basis': 'start baseline' if sid == root else 'child created during flow'})
    return sessions, discovery_errors


def reconcile(run: dict[str, Any], events: list[dict[str, Any]],
              expected: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Read native identities and counters; never run tools or retain content.

    Final responses remain observable until the next run in the same session.
    Both hosts feed the same coverage, saved-counter and dashboard model.
    """
    started = _time(run.get('at'))
    later = [_time(e.get('at')) for e in events if e.get('kind') == 'start'
             and e.get('session') == run['session'] and e.get('run') != run['run']]
    cutoff = min((t for t in later if t and started and t > started), default=None)
    if run.get('host') == 'claude':
        sessions, discovery_errors = claude.sessions(run, events, cutoff)
    else:
        sessions, discovery_errors = _codex_sessions(run, cutoff)
    saved: dict[str, Any] = next((e.get('measurement', {}) for e in reversed(events)
                  if e.get('run') == run['run'] and e.get('kind') == 'usage'), {})
    by_session = {s['session']: s for s in sessions}
    for prior in saved.get('sessions', []):
        current = by_session.get(prior['session'])
        if prior.get('usage') and (current is None or not current.get('usage')):
            if current is not None:
                sessions.remove(current)
            sessions.append(dict(prior, status='recorded; native counter unavailable'))
    matched = {s['agent'] for s in sessions} | {s['session'] for s in sessions}
    for agent in sorted(set(expected) - matched):
        sessions.append({'session': None, 'agent': agent, 'role': 'lens', 'usage': None,
                         'native_usage': None, 'status': 'unavailable', 'basis': 'declared reviewer; native session not found'})
    def total(key: str, role: bool) -> dict[str, int] | None:
        values = [s[key] for s in sessions if (s['role'] == 'host_approval_review') == role and s[key]]
        return {k: sum(v.get(k, 0) for v in values) for k in values[0]} if values else None
    delivery = [s for s in sessions if s['role'] != 'host_approval_review']
    return {'sessions': sessions, 'tokens': total('usage', False),
            'native_tokens': total('native_usage', False),
            'host_approval_tokens': total('usage', True),
            'token_coverage': {'measured_sessions': sum(s['usage'] is not None for s in delivery),
                               'unmeasured_sessions': sum(s['usage'] is None for s in delivery),
                               'partial_sessions': sum(s['status'] == 'partial' for s in delivery),
                               'discovery_errors': discovery_errors,
                               'basis': 'Root delta from flow start; full usage of children created during flow. Includes final responses and recovery until the next run in the same task. Host approval review shown separately.'}}
