"""Reconcile advisory usage with native session metadata, including unhooked lenses."""
from __future__ import annotations

from typing import Any
from datetime import datetime
import json
import os
import stat
import hashlib
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


_READERS: dict[str, meter.IncrementalUsageReader] = {}
_LAST_REFRESH: dict[str, Any] = {}


def _reader(run: dict[str, Any], roots: list[Path], cutoff: float | None,
            host: str, *, purpose: str = 'advisory', expected_root: str | None = None
            ) -> meter.IncrementalUsageReader:
    key = meter.fingerprint({'host': host, 'root': run['session'], 'run': run.get('run'),
        'roots': [str(p) for p in roots], 'start': run.get('at'), 'end': cutoff,
        'known': sorted(run.get('worker_sessions', [])), 'purpose': purpose,
        'expected_root': expected_root})
    reader = _READERS.get(key)
    if reader is None:
        reader = meter.IncrementalUsageReader(host, run['session'], roots,
            start=_time(run.get('at')), end=cutoff, known=run.get('worker_sessions', []),
            checkpoint=run.get('usage_checkpoint') if purpose == 'advisory' else None,
            expected_root=expected_root)
        _READERS[key] = reader
        # This is an advisory process cache, not an unbounded archive.
        if len(_READERS) > 16:
            _READERS.pop(next(iter(_READERS))).close()
    return reader


def _codex_sessions(run: dict[str, Any], cutoff: float | None,
                    diagnostics: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], int]:
    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    reader = _reader(run, [home / 'sessions', home / 'archived_sessions'], cutoff, 'Codex')
    result = reader.refresh()
    if diagnostics is not None:
        diagnostics.extend(result.get('inventory_diagnostics', []))
    _LAST_REFRESH.clear()
    _LAST_REFRESH.update(result)
    _LAST_REFRESH['checkpoint'] = reader.checkpoint()
    baseline = meter.starting_baseline(run)
    sessions = meter.run_sessions(result['sessions'], run['session'], run.get('worker_sessions', []),
                                  start=_time(run.get('at')), end=cutoff)
    if not any(s['session'] == run['session'] for s in sessions):
        sessions.append({'session': run['session'], 'agent': 'orchestrator', 'role': 'orchestrator',
            'usage': None, 'native_usage': None, 'status': 'unavailable',
            'errors': ['native counter unavailable'], 'attribution_unknown': True})
    for session in sessions:
        if session['session'] == run['session']:
            native = session.get('native_usage')
            if baseline is None or native is None or any(native[k] < baseline[k] for k in baseline):
                session.update(usage=None, status='partial', attribution_unknown=True)
                session['errors'].append('run baseline unavailable or reset')
            elif not session.get('errors'):
                session['usage'] = {k: native[k] - baseline[k] for k in baseline}
    errors = len(reader.state['discovery_errors']) + int(not result['fully_discovered'])
    return sessions, errors


def owned_intervals(run: dict[str, Any]) -> dict[str, Any]:
    """Read native interval evidence without advisory counters or saved samples.

    The caller supplies controller-owned identities and start time. Native reader
    boundary/lineage checks determine coverage; absent evidence stays unknown.
    """
    started = _time(run.get('at'))
    if started is None:
        raise ValueError('Owned accounting needs the controller run start time')
    explicit = None
    if run.get('host') == 'claude':
        explicit = run.get('transcript_path') or claude.transcript(run['session'], {})
        home = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
        roots = [Path(explicit).with_suffix('') / 'subagents'] if explicit else [home / 'projects']
        host = 'Claude'
    else:
        home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        roots, host = [home / 'sessions', home / 'archived_sessions'], 'Codex'
    reader = _reader(run, roots, None, host, purpose='required_budget',
                     expected_root=run['session'] if explicit else None)
    if explicit:
        reader.add_file(Path(explicit), session=run['session'])
    refresh = reader.refresh()
    sessions = meter.run_sessions(refresh['sessions'], run['session'], run.get('worker_sessions', []),
                                  start=started, end=None)
    errors = len(reader.state['discovery_errors']) + int(not refresh['fully_discovered'])
    delivery = [row for row in sessions if row.get('role') != 'host_approval_review']
    return {'sessions': sessions, 'token_coverage': {
        'unmeasured_sessions': sum(row.get('usage') is None for row in delivery)
                               + int(not any(row.get('session') == run['session'] for row in delivery)),
        'partial_sessions': sum(row.get('status') != 'measured' or bool(row.get('attribution_unknown'))
                                for row in delivery),
        'discovery_errors': errors, 'inventory_errors': refresh.get('inventory_errors', 0)}}


def reconcile(run: dict[str, Any], events: list[dict[str, Any]],
              expected: list[str] | tuple[str, ...] = (), *,
              closing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read native identities and counters; never run tools or retain content.

    Final responses remain observable until the next run in the same session.
    Both hosts feed the same coverage, saved-counter and dashboard model.
    """
    started = _time(run.get('at'))
    later = [e for e in events if e.get('kind') == 'start'
             and e.get('session') == run['session'] and e.get('run') != run['run']
             and started is not None and (_time(e.get('at')) or 0) > started]
    boundary = min(later, key=lambda e: _time(e.get('at')) or 0) if later else None
    if closing is not None:
        instant = _time(closing.get('at'))
        if (closing.get('session') != run['session'] or closing.get('host', 'codex') != run.get('host', 'codex')
                or instant is None or started is None or instant < started
                or boundary and instant > (_time(boundary.get('at')) or 0)):
            raise ValueError('Closing counter must match this root, host and ownership interval')
        boundary = closing
    cutoff = _time(boundary.get('at')) if boundary else None
    diagnostics: list[dict[str, Any]] = []
    _LAST_REFRESH.clear()
    if run.get('host') == 'claude':
        sessions, discovery_errors = claude.sessions(run, events, cutoff)
        _LAST_REFRESH.update(claude.last_refresh())
    else:
        known_workers = set(run.get('worker_sessions', []))
        known_workers.update(str(e.get('session')) for e in events if e.get('run') == run['run']
                             and e.get('kind') == 'hook' and e.get('session') != run['session'])
        sessions, discovery_errors = _codex_sessions({**run, 'worker_sessions': sorted(known_workers)}, cutoff, diagnostics)
    start = meter.starting_baseline(run)
    if start is None:
        for session in sessions:
            if session.get('session') == run['session']:
                session.update(usage=None, status='partial', attribution_unknown=True)
    closing_end = meter.starting_baseline(closing) if closing is not None else None
    if closing is not None and (closing_end is None or start is not None and any(
            closing_end[k] < start[k] for k in start)):
        for session in sessions:
            if session.get('session') == run['session']:
                session.update(usage=None, status='partial', attribution_unknown=True,
                               errors=['committed closing counter is partial, unavailable or reset'])
    saved: dict[str, Any] = next((e.get('measurement', {}) for e in reversed(events)
                  if e.get('run') == run['run'] and e.get('kind') == 'usage'), {})
    by_session = {s['session']: s for s in sessions}
    for prior in saved.get('sessions', []):
        current = by_session.get(prior['session'])
        # Legacy saved child totals can contain pre-run lifetime usage. Never
        # restore them over an explicit reset/conflict or a revised interval.
        owned = (prior.get('attribution_schema') == 'taskplane.owned-interval/v1'
                 and prior.get('interval') == {'start': started, 'end_exclusive': cutoff})
        current_errors = set((current or {}).get('errors', []))
        absence = {'native counter unavailable', 'native_source_unavailable', 'native_metadata_unavailable',
                   'run baseline unavailable or reset'}
        unavailable = (current is None or not current.get('usage') or 'native_source_unavailable' in current_errors)
        # Missing native bytes do not invalidate an earlier observed subtotal.
        # Reset/identity/conflict evidence does: never restore over those errors.
        prior_errors = set(prior.get('errors', []))
        recoverable = (current_errors <= absence | prior_errors
                       and not current_errors & {'native_metadata_invalid', 'native_parent_mismatch'}
                       and not any('reset' in error or 'conflict' in error or 'mutation' in error
                                   for error in current_errors - {'run baseline unavailable or reset'}))
        native = (current or {}).get('native_usage')
        if prior.get('role') == 'orchestrator' and native is not None and start is not None:
            recoverable = recoverable and all(native.get(k, -1) >= start[k] for k in start)
        if (prior.get('usage') and unavailable and recoverable
                and (prior.get('role') != 'orchestrator' or start is not None)
                and (prior.get('role') == 'orchestrator' or owned)):
            if current is not None:
                sessions.remove(current)
            sessions.append(dict(prior,
                status='partial' if prior.get('status') == 'partial' else 'recorded; native counter unavailable',
                recovery_status='recorded; native counter unavailable'))
        elif current is None:
            sessions.append({**prior, 'usage': None, 'native_usage': None, 'status': 'unavailable',
                             'basis': 'saved attribution has no verified matching run interval'})
    # The next run's observed root baseline closes this run exactly. It remains
    # available when growing transcripts push that counter outside the bounded
    # native reader, so historical totals cannot fall back to an older sample.
    end = meter.starting_baseline(boundary) if boundary else None
    if (boundary and boundary.get('usage_status') == 'observed'
            and run.get('usage_status', 'observed') == 'observed'
            and boundary.get('host', 'codex') == run.get('host', 'codex')
            and start is not None and end is not None
            and _counts(start) and _counts(end) and set(start) == set(end)
            and all(end[k] >= start[k] for k in start)):
        root_session = next((s for s in sessions if s.get('session') == run['session']), None)
        if root_session is not None and not root_session.get('attribution_unknown'):
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
    refresh = {k:v for k,v in _LAST_REFRESH.items() if k not in ('sessions', 'checkpoint')}
    if refresh:
        known = [s['usage'] for s in sessions if s.get('usage') is not None]
        refresh.update(totals={k: sum(v.get(k, 0) for v in known) for k in known[0]} if known else None,
                       attribution={s['session']: s['usage'] for s in sessions if s.get('session')})
        refresh['complete'] = bool(refresh.get('complete') and all(
            s.get('status') == 'measured' and not s.get('attribution_unknown') for s in sessions))
        refresh['coverage'] = 'complete' if refresh['complete'] else 'partial'
    return {'sessions': sessions, 'tokens': total('usage', False),
            'usage_refresh': refresh,
            'usage_checkpoint': _LAST_REFRESH.get('checkpoint'),
            'ownership_interval': {'start': started, 'end_exclusive': cutoff},
            'native_tokens': total('native_usage', False),
            'host_approval_tokens': total('usage', True),
            'token_coverage': {'measured_sessions': sum(s['usage'] is not None for s in delivery),
                               'unmeasured_sessions': sum(s['usage'] is None for s in delivery),
                               'partial_sessions': sum(s['status'] == 'partial' for s in delivery),
                               'discovery_errors': discovery_errors,
                                'inventory_errors': _LAST_REFRESH.get('inventory_errors', len(diagnostics)),
                                'discovery_diagnostics': diagnostics[:32],
                                'diagnostics_omitted': max(0, _LAST_REFRESH.get('inventory_errors', len(diagnostics)) - 32),
                               'basis': 'Root start delta; new child usage and reused-session intervals [run start, next run start). Host approval review is separate. Missing/reset boundaries remain unknown.'}}


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
    if any(s.get('usage') is None or s.get('status') != 'measured' for s in delivery):
        gaps.append('Measured subtotal has incomplete session coverage; missing usage is unknown, not zero.')
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
