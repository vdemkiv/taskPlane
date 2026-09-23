"""Reconcile advisory usage with native session metadata, including unhooked lenses."""
from __future__ import annotations

from typing import Any
from datetime import datetime
import json
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
    later = [e for e in events if e.get('kind') == 'start'
             and e.get('session') == run['session'] and e.get('run') != run['run']
             and started is not None and (_time(e.get('at')) or 0) > started]
    boundary = min(later, key=lambda e: _time(e.get('at')) or 0) if later else None
    cutoff = _time(boundary.get('at')) if boundary else None
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
    # The next run's observed root baseline closes this run exactly. It remains
    # available when growing transcripts push that counter outside the bounded
    # native reader, so historical totals cannot fall back to an older sample.
    end: Any = boundary.get('usage') if boundary else None
    start: Any = run.get('usage')
    if (boundary and boundary.get('usage_status') == 'observed'
            and run.get('usage_status', 'observed') == 'observed'
            and boundary.get('host', 'codex') == run.get('host', 'codex')
            and _counts(start) and _counts(end) and set(start) == set(end)
            and all(end[k] >= start[k] for k in start)):
        root_session = next((s for s in sessions if s.get('session') == run['session']), None)
        if root_session is not None:
            root_session.update(native_usage=dict(end), usage={k: end[k] - start[k] for k in start},
                                status='measured', basis='observed next-run start baseline',
                                measurement_source='run_boundary', measured_at=boundary['at'])
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


def _counts(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and all(type(v) is int and v >= 0 for v in value.values())


def _add(target: dict[str, int], value: dict[str, int]) -> None:
    for key, count in value.items():
        target[key] = target.get(key, 0) + count


def phase_accounting(points: list[dict[str, Any]], endpoint: dict[str, Any],
                     state: dict[str, Any], measurement: dict[str, Any]) -> dict[str, Any]:
    """Account for known counters without inventing missing phase boundaries.

    A stable visit ID can recover phase ownership across a missing revision, but
    not the work/review split. Recorded counters are never fresh boundaries.
    Unknown amounts and known amounts with unknown attribution remain distinct.
    """
    clock = measurement.get('usage_measurement', {})
    result: dict[str, Any] = {
        'schema': 'taskplane.usage-accounting/v1', 'visits': {}, 'phases': {},
        'non_phase': {}, 'intervals': [], 'gaps': [], 'status': 'unknown',
        'basis': 'Observed boundary intervals; same-visit gaps retain an unknown work/review split. '
                 'Follow-up is outside phases; unresolved amounts are not estimated phase usage.',
        'measurement_at': clock.get('measured_at'),
        'measurement_attempted_at': clock.get('attempted_at'),
        'measurement_status': clock.get('status', 'unavailable'),
        'baseline_at': points[0].get('observed_at') if points else None,
    }
    gaps, intervals = result['gaps'], result['intervals']
    visits = {v['id']: v['phase'] for v in state['visits']}
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for point in [*points, endpoint]:
        # Journal timestamps can differ when the same boundary is retried.
        identity = json.dumps({k: point.get(k) for k in
                               ('revision', 'previous_revision', 'visit', 'bucket', 'sessions')}, sort_keys=True)
        if identity not in seen:
            unique.append(point)
            seen.add(identity)
    bad_sessions: set[str] = set()
    session_reasons: dict[str, set[str]] = {}
    for before, after in zip(unique, unique[1:]):
        old = {s['session']: s for s in before.get('sessions', []) if s.get('session')}
        new = {s['session']: s for s in after.get('sessions', []) if s.get('session')}
        transition = after.get('previous_revision') == before.get('revision')
        same_visit = before.get('visit') == after.get('visit')
        ordered = (type(before.get('revision')) is int and type(after.get('revision')) is int
                   and after['revision'] >= before['revision'])
        for sid in sorted(old.keys() | new.keys()):
            a, b = old.get(sid, {}), new.get(sid, {})
            if 'host_approval_review' in (a.get('role'), b.get('role')):
                continue
            start, end = a.get('native_usage'), b.get('native_usage')
            comparable = _counts(start) and _counts(end) and set(start) == set(end)
            reset = comparable and any(end[k] < start[k] for k in start)
            if reset:
                bad_sessions.add(sid)
            delta = {k: end[k] - start[k] for k in start} if comparable and not reset else None
            fresh = a.get('status') == b.get('status') == 'measured'
            role_known = a.get('role') == b.get('role') and bool(a.get('role'))
            visit = before.get('visit')
            phase = visits.get(visit)
            bucket = before.get('bucket')
            reason = None
            if not comparable:
                reason = 'late_session' if not a else 'missing_counter_boundary'
            elif reset:
                reason = 'counter_reset'
            elif not fresh:
                reason = 'recorded_or_partial_boundary'
            elif not role_known:
                reason = 'session_role_changed'
            elif not ordered:
                reason = 'out_of_order_boundary'
            elif not transition and not same_visit:
                reason = 'missing_transition'
            elif phase != before.get('phase') or bucket not in ('work', 'review', 'follow_up'):
                reason = 'unknown_visit_or_bucket'
            elif not transition and (before.get('bucket') == 'follow_up') != (after.get('bucket') == 'follow_up'):
                reason = 'missing_finish_boundary'
            category = 'unresolved' if reason else 'non_phase' if bucket == 'follow_up' else 'phase'
            if not reason and not transition:
                reason = 'missing_revision_within_visit'
                bucket = 'unsegmented' if category == 'phase' else bucket
                gaps.append(f'Missing transition observation within {visit}; phase is known, work/review split is not.')
            elif reason:
                gaps.append(('Missing transition observation: ' if reason == 'missing_transition' else f'{sid}: missing, reset or partial boundary: ') + reason)
                session_reasons.setdefault(sid, set()).add(reason)
            intervals.append({'session': sid, 'role': b.get('role') or a.get('role'),
                              'from': before.get('observed_at'), 'to': after.get('observed_at'),
                              'counter_from': a.get('measured_at'), 'counter_to': b.get('measured_at'),
                              'from_revision': before.get('revision'), 'to_revision': after.get('revision'),
                              'visit': visit if category == 'phase' else None,
                              'phase': phase if category == 'phase' else None,
                              'candidate_visits': list(dict.fromkeys([before.get('visit'), after.get('visit')])),
                              'category': category, 'bucket': bucket if category != 'unresolved' else None,
                              'reason': reason, 'tokens': delta,
                              'coverage': 'measured' if not reason else 'phase_known' if category == 'phase' else 'partial'})
    if not points:
        gaps.append('No phase boundaries were recorded for this run; phase attribution is unavailable.')
    delivery = [s for s in measurement.get('sessions', []) if s.get('role') != 'host_approval_review']
    # Reject a whole session's allocation if its counters reset or its intervals
    # exceed the separately measured run delta. Never silently cap a token count.
    budgets = {s['session']: s['usage'] for s in delivery if s.get('session') and _counts(s.get('usage'))}
    for sid, budget in budgets.items():
        used: dict[str, int] = {}
        for item in intervals:
            if item['session'] == sid and item['tokens'] is not None:
                _add(used, item['tokens'])
        if any(used.get(k, 0) > budget.get(k, -1) for k in used):
            bad_sessions.add(sid)
            session_reasons.setdefault(sid, set()).add('session_total_conflict')
    for item in intervals:
        if item['session'] in bad_sessions:
            item.update(category='unresolved', phase=None, visit=None, bucket=None,
                        tokens=None, reason='counter_reset_or_total_conflict', coverage='unknown')
    for sid, budget in budgets.items():
        used = {}
        for item in intervals:
            if item['session'] == sid and item['tokens'] is not None:
                _add(used, item['tokens'])
        residual = {k: v - used.get(k, 0) for k, v in budget.items()}
        if any(residual.values()):
            reasons = sorted(session_reasons.get(sid, {'missing_session_baseline'}))
            intervals.append({'session': sid, 'category': 'unresolved', 'phase': None, 'visit': None,
                              'bucket': None, 'from': None, 'to': endpoint.get('observed_at'),
                              'reason': ', '.join(reasons), 'tokens': residual, 'coverage': 'amount_known'})
    total: Any = measurement.get('tokens')
    used = {}
    for item in intervals:
        if item['tokens'] is not None:
            _add(used, item['tokens'])
    comparable_total = _counts(total) and all(total.get(k, -1) >= v for k, v in used.items())
    if comparable_total:
        residual = {k: v - used.get(k, 0) for k, v in total.items()}
        if any(residual.values()):
            intervals.append({'session': None, 'category': 'unresolved', 'phase': None, 'visit': None,
                              'bucket': None, 'from': None, 'to': endpoint.get('observed_at'),
                              'reason': 'no_phase_boundaries' if not points else 'missing_session_baseline',
                              'tokens': residual, 'coverage': 'amount_known'})
    else:
        gaps.append('Run counters cannot reconcile attributed usage; possible counter reset or missing run baseline.')
    phase_total: dict[str, int] = {}
    other_total: dict[str, int] = {}
    unresolved: dict[str, int] = {k: 0 for k in total} if comparable_total else {}
    sessions: dict[str, Any] = {}
    for item in intervals:
        value = item['tokens']
        if value is None:
            continue
        category = item['category']
        session = sessions.setdefault(item['session'] or 'unknown', {'phase': {}, 'non_phase': {}, 'unresolved': {}})
        _add(session[category], value)
        if category == 'phase':
            visit = result['visits'].setdefault(item['visit'], {'phase': item['phase'], 'tokens': {}, 'buckets': {}, 'status': 'measured'})
            _add(visit['tokens'], value)
            _add(visit['buckets'].setdefault(item['bucket'], {}), value)
            if item['reason']:
                visit['status'] = 'partial'
            _add(phase_total, value)
        elif category == 'non_phase':
            group = result['non_phase'].setdefault(item['bucket'], {'tokens': {}, 'status': 'measured'})
            _add(group['tokens'], value)
            _add(other_total, value)
        else:
            _add(unresolved, value)
    for visit in result['visits'].values():
        phase = result['phases'].setdefault(visit['phase'], {'tokens': {}, 'status': 'measured'})
        _add(phase['tokens'], visit['tokens'])
        if visit['status'] != 'measured':
            phase['status'] = 'partial'
    if any(unresolved.values()):
        gaps.append('Known run usage has explained unresolved attribution; see interval and session accounting.')
    if measurement.get('token_coverage', {}).get('discovery_errors'):
        gaps.append('Native session discovery has errors; additional coverage is unknown.')
    root: dict[str, Any] = next((s for s in delivery if s.get('session') == state['root']), {})
    native: Any = root.get('native_usage')
    run_usage: Any = root.get('usage')
    pre_run = ({k: native[k] - run_usage[k] for k in native}
               if _counts(native) and _counts(run_usage) and set(native) == set(run_usage)
               and all(native[k] >= run_usage[k] for k in native) else None)
    result.update(attributed=phase_total or None, unallocated=unresolved if comparable_total else None,
                  sessions=sessions, pre_run={'tokens': pre_run, 'included_in_run': False,
                  'basis': 'Root native lifetime baseline before this run; includes earlier runs and setup, not inferred activity.'},
                  accounting={'run': total, 'phase': phase_total, 'non_phase': other_total,
                              'unresolved': unresolved if comparable_total else None, 'reconciled': bool(comparable_total)},
                  status='unknown' if not points else 'partial' if gaps else 'measured')
    result['gaps'] = list(dict.fromkeys(gaps))
    return result
