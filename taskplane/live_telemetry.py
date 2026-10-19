"""Run-bound advisory aggregates. Never workflow authority or an event journal."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from . import primitives, storage

SCHEMA = 'taskplane.live-telemetry/v1'


def path(workspace: Path, run: str) -> Path:
    key = hashlib.sha256(run.encode()).hexdigest()[:32]
    return storage.runtime_file(str(workspace), 'live-telemetry-' + key + '.json')


def metadata(state: dict[str, Any]) -> dict[str, Any]:
    return {'run': state['run'], 'session': state['root'], 'goal': state.get('goal'),
            'at': state.get('started_at'), 'phase': state['visits'][0]['phase'],
            'host': state.get('host', 'codex')}


def binding(state: dict[str, Any]) -> dict[str, Any]:
    value = {k: state[k] for k in ('workspace', 'root', 'run', 'goal', 'index', 'started_at', 'finished', 'retired', 'cancellation', 'superseded_by')
             if k in state}
    value['visits'] = [{'phase': visit['phase']} for visit in state['visits']]
    return value


def empty(state: dict[str, Any]) -> dict[str, Any]:
    return {'schema': SCHEMA, 'workspace': state['workspace'], 'run': state['run'],
            'root': state['root'], 'metadata': metadata(state), 'seen': [],
            'sessions': {state['root']: {'baseline': None, 'maximum': None}},
            'tool_calls': 0, 'outcomes': {}, 'approximate': False, 'recent_tools': 0,
            'actions': {}, 'dispatches': 0, 'milestones': [], 'artifacts': {},
            'captured': {}, 'observation_count': 0, 'last_observation_at': None}


def read(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path(workspace, state['run']).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return empty(state)
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA
            or any(value.get(k) != state[k] for k in ('workspace', 'root', 'run'))):
        raise ValueError('Live telemetry binding is invalid')
    if (any(not isinstance(value.get(k), dict) for k in
            ('metadata', 'sessions', 'actions', 'outcomes', 'artifacts', 'captured'))
            or any(type(value.get(k)) is not int or value[k] < 0 for k in
                   ('tool_calls', 'recent_tools', 'dispatches', 'observation_count'))
            or type(value.get('approximate')) is not bool
            or not isinstance(value.get('seen'), list) or not isinstance(value.get('milestones'), list)):
        raise ValueError('Live telemetry aggregate is malformed')
    if (value['metadata'].get('run') != state['run'] or value['metadata'].get('session') != state['root']
            or value['metadata'].get('at') != state.get('started_at')
            or not isinstance(value['metadata'].get('native_children', {}), dict)):
        raise ValueError('Live telemetry metadata is foreign or malformed')
    for sample in value['sessions'].values():
        if not isinstance(sample, dict):
            raise ValueError('Live telemetry session is malformed')
        baseline, maximum = sample.get('baseline'), sample.get('maximum')
        if baseline is None and maximum is None:
            continue
        if (not isinstance(baseline, dict) or not isinstance(maximum, dict) or set(baseline) != set(maximum)
                or any(type(n) is not int or n < 0 for n in [*baseline.values(), *maximum.values()])):
            raise ValueError('Live telemetry counters are malformed')
    if any(type(n) is not int or n < 0 for k in ('actions', 'outcomes') for n in value[k].values()):
        raise ValueError('Live telemetry counts are malformed')
    if 'measurement' in value:
        measurement = value['measurement']
        if not isinstance(measurement, dict) or not isinstance(measurement.get('sessions', []), list):
            raise ValueError('Saved telemetry measurement is malformed')
        for row in measurement.get('sessions', []):
            if (not isinstance(row, dict)
                    or any(k not in row for k in ('session', 'agent', 'role', 'usage', 'native_usage'))
                    or any(row[k] is not None and not isinstance(row[k], str) for k in ('session', 'agent', 'role'))
                    or not isinstance(row.get('errors', []), list)
                    or any(not isinstance(error, str) for error in row.get('errors', []))):
                raise ValueError('Saved telemetry session is malformed')
            for key in ('usage', 'native_usage'):
                counts = row[key]
                if counts is not None and (not isinstance(counts, dict)
                        or any(type(n) is not int or n < 0 for n in counts.values())):
                    raise ValueError('Saved telemetry usage is malformed')
    return value


def _save(workspace: Path, value: dict[str, Any]) -> None:
    primitives.atomic_json(path(workspace, value['run']), value)


def _sample(value: dict[str, Any], session: str, usage: Any) -> None:
    counters = value['sessions'].setdefault(session, {'baseline': None, 'maximum': None})
    if not isinstance(usage, dict) or not usage:
        return
    if not all(type(n) is int and n >= 0 for n in usage.values()):
        return
    if counters['baseline'] is None:
        counters.update(baseline=deepcopy(usage), maximum=deepcopy(usage))
    elif set(usage) == set(counters['baseline']):
        counters['maximum'] = {k: max(n, counters['maximum'][k]) for k, n in usage.items()}


def initialize(workspace: Path, state: dict[str, Any], observation: dict[str, Any]) -> None:
    target = path(workspace, state['run'])
    with primitives.file_lock(str(target)):
        value = read(workspace, state)
        value['metadata'].update(observation)
        _sample(value, state['root'], observation.get('usage'))
        _save(workspace, value)


def summary(value: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    usage: Counter[str] = Counter()
    measured = 0
    for sample in value['sessions'].values():
        if sample['baseline'] is not None:
            measured += 1
            usage.update({k: max(0, sample['maximum'][k] - n)
                          for k, n in sample['baseline'].items()})
    warnings = []
    if value['recent_tools'] >= 20:
        warnings.append('20+ tool calls since reported progress: check the next deliverable and simplify.')
    if value['actions'] and max(value['actions'].values()) >= 4:
        warnings.append('The same action was attempted 4+ times since progress: change approach or report the real blocker.')
    if value['dispatches'] >= 6:
        warnings.append('6+ agent dispatches since progress: check whether the reviews justify their cost.')
    if usage.get('total_tokens', 0) >= 100_000 and not value['milestones']:
        warnings.append('100k+ observed tokens without a milestone: reduce context and focus on a testable result.')
    return {'mode': 'advisory', 'owner': 'orchestrator', 'run': state['run'],
            'goal': state.get('goal'), 'status': 'finished' if state.get('finished') else 'active',
            'outcome': None, 'entry_phase': state['visits'][0]['phase'],
            'phase': state['visits'][state['index']]['phase'], 'milestones': value['milestones'],
            'started_at': state.get('started_at'), 'finished_at': state.get('finished_at'),
            'tool_calls': value['tool_calls'],
            'hook_outcomes': {'attempted': value['tool_calls'],
                              **{key: value['outcomes'].get(key, 0) for key in ('admitted', 'denied', 'error', 'observed')},
                              'coverage': 'approximate' if value['approximate'] else 'identified'},
            'tokens': dict(usage) if measured else None,
            'token_coverage': {'measured_sessions': measured,
                               'unmeasured_sessions': len(value['sessions']) - measured,
                               'basis': 'first-to-maximum observed native counters; lower bound'},
            'advice': warnings}


def observe(workspace: Path, state: dict[str, Any], row: dict[str, Any]) -> list[str]:
    target = path(workspace, state['run'])
    with primitives.file_lock(str(target)):
        value = read(workspace, state)
        before = summary(value, state)['advice']
        call = row.get('call_id')
        if call:
            key = hashlib.sha256(json.dumps([row['session'], row['event'], call]).encode()).hexdigest()
            if key in value['seen']:
                return []
            value['seen'].append(key)
        else:
            value['approximate'] = True
        if isinstance(row.get('child'), str) and row['child']:
            child = row['child']
            children = value['metadata'].setdefault('native_children', {})
            children.setdefault(child, None)
            if isinstance(row.get('agent_transcript_path'), str):
                children[child] = row['agent_transcript_path']
            value['sessions'].setdefault(child, {'baseline': None, 'maximum': None})
        if not any(state.get(k) for k in ('finished', 'retired', 'cancellation', 'superseded_by')):
            _sample(value, row['session'], row.get('usage'))
        if row['event'] == 'PreToolUse':
            from .worker_runtime import SPAWN
            value['tool_calls'] += 1
            value['recent_tools'] += 1
            action = row.get('action')
            if action:
                value['actions'][action] = value['actions'].get(action, 0) + 1
            value['dispatches'] += int(row.get('tool') in SPAWN)
        outcome = row.get('outcome', 'observed')
        value['outcomes'][outcome] = value['outcomes'].get(outcome, 0) + 1
        value['observation_count'] += 1
        value['last_observation_at'] = datetime.now(timezone.utc).isoformat()
        advice = [item for item in summary(value, state)['advice'] if item not in before]
        _save(workspace, value)
        return advice


def progress(workspace: Path, state: dict[str, Any], kind: str, note: str,
             artifacts: dict[str, Any], captured: dict[str, Any]) -> None:
    target = path(workspace, state['run'])
    with primitives.file_lock(str(target)):
        value = read(workspace, state)
        value['observation_count'] += 1
        value['last_observation_at'] = datetime.now(timezone.utc).isoformat()
        if kind == 'progress':
            value.update(recent_tools=0, actions={}, dispatches=0)
            value['milestones'].append({'phase': state['visits'][state['index']]['phase'],
                                        'note': note, 'at': datetime.now(timezone.utc).isoformat()})
        evidence = list(dict.fromkeys(value['artifacts'].get('evidence', []) + artifacts.get('evidence', [])))
        value['artifacts'].update(artifacts)
        if evidence:
            value['artifacts']['evidence'] = evidence
        previews = {**value['captured'].get('evidence', {}), **captured.get('evidence', {})}
        value['captured'].update(captured)
        value['captured']['evidence'] = previews
        _save(workspace, value)


def remember_measurement(workspace: Path, state: dict[str, Any], measurement: dict[str, Any],
                         captured_at: str) -> None:
    target = path(workspace, state['run'])
    with primitives.file_lock(str(target)):
        value = read(workspace, state)
        if value.get('measurement_captured_at', '') > captured_at:
            return
        value['measurement'] = {k: deepcopy(measurement[k]) for k in
            ('sessions', 'tokens', 'native_tokens', 'token_coverage') if k in measurement}
        value['measurement_captured_at'] = captured_at
        _save(workspace, value)


def closing_time(state: dict[str, Any]) -> str | None:
    stamp = state.get('finished_at')
    if not stamp and state.get('retired'):
        stamp = state['retired'].get('at')
    if not stamp and state.get('superseded_by'):
        stamp = next((row.get('at') for row in reversed(state.get('history', []))
                      if row.get('replaced_by') == state['superseded_by']), None)
    return stamp if isinstance(stamp, str) else None


def close(workspace: Path, state: dict[str, Any], observation: dict[str, Any]) -> None:
    at = closing_time(state)
    if not at:
        return
    target = path(workspace, state['run'])
    with primitives.file_lock(str(target)):
        value = read(workspace, state)
        if value['metadata'].get('closing_counter'):
            return
        value['metadata']['closing_counter'] = {**observation, 'session': state['root'],
            'host': value['metadata'].get('host', 'codex'), 'at': at}
        _sample(value, state['root'], observation.get('usage'))
        _save(workspace, value)
