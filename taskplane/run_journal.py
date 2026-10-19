"""Optional post-run analysis records. No gate or active-run consumer."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from . import primitives, storage, workflow

SCHEMA = 'taskplane.run-record/v1'


def record(workspace: Path, state: dict[str, Any], *, note: str = '') -> dict[str, Any]:
    workflow.require(bool(state.get('finished') or state.get('retired') or state.get('superseded_by')
                          or state.get('cancellation')), 'state_unavailable',
                     'Analysis records are available only after a run has ended.')
    key = primitives.content_fingerprint({'root': state['root'], 'run': state['run']})[:32]
    target = storage.runtime_file(str(workspace), 'run-record-' + key + '.json')
    with primitives.file_lock(str(target)):
        if target.exists():
            prior = json.loads(target.read_text(encoding='utf-8'))
            if (not isinstance(prior, dict) or prior.get('schema') != SCHEMA
                    or any(prior.get(k) != state[k] for k in ('workspace', 'root', 'run'))):
                raise ValueError('Existing analysis record is invalid; preserve it for diagnosis.')
            return prior
        value = {'schema': SCHEMA, **{k: state[k] for k in ('workspace', 'root', 'run')},
                 'goal': state.get('goal'), 'started_at': state.get('started_at'),
                 'finished_at': state.get('finished_at'),
                 'outcome': 'finished' if state.get('finished') else 'cancelled' if state.get('cancellation')
                            else 'retired' if state.get('retired') else 'superseded',
                 'recorded_at': datetime.now(timezone.utc).isoformat(), 'note': note,
                 'phases': [{'phase': v['phase'], 'decision': v['decision'],
                              'output_path': (v.get('packet') or {}).get('output_path')}
                             for v in state['visits']],
                 'authority': 'analysis_only'}
        primitives.atomic_json(target, value)
        return value
