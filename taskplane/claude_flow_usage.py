"""Claude's native session counters for the shared advisory delivery view.

Only identities, timestamps and usage leave this reader. Streamed assistant
records repeat message usage; count each API message once, not each JSONL row.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
from typing import Any
from copy import deepcopy

if __package__:
    from .spend import normalize_usage as _package_normalize
    from .native_session_meter import starting_baseline as _package_baseline, USAGE_KEYS as _package_keys
    normalize_usage = _package_normalize
    starting_baseline = _package_baseline
    USAGE_KEYS = _package_keys
else:
    from spend import normalize_usage as _flat_normalize
    from native_session_meter import starting_baseline as _flat_baseline, USAGE_KEYS as _flat_keys
    normalize_usage = _flat_normalize
    starting_baseline = _flat_baseline
    USAGE_KEYS = _flat_keys


def timestamp(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return None


def bind_session(event: dict[str, Any]) -> None:
    """Use Claude's supported session-scoped Bash environment, even before a run."""
    target = os.environ.get('CLAUDE_ENV_FILE')
    if event.get('hook_event_name') != 'SessionStart' or not target:
        return
    values = {'TASKPLANE_CLAUDE_SESSION_ID': event.get('session_id'),
              'TASKPLANE_CLAUDE_TRANSCRIPT': event.get('transcript_path')}
    selected = os.environ.get('TASKPLANE_WORKSPACE')
    if selected:
        from taskplane import workspace_binding
        workspace = workspace_binding.resolve_workspace(None, event=event)
        workspace_binding.ensure(workspace)
        values['TASKPLANE_WORKSPACE'] = str(workspace)
    if all(isinstance(v, str) and v for v in values.values()):
        with open(target, 'a', encoding='utf-8') as stream:
            for key, value in values.items():
                stream.write(f'export {key}={shlex.quote(str(value))}\n')


def transcript(session: str, event: dict[str, Any]) -> Path | None:
    explicit = event.get('transcript_path') or event.get('transcript')
    if not explicit and session == os.environ.get('TASKPLANE_CLAUDE_SESSION_ID'):
        explicit = os.environ.get('TASKPLANE_CLAUDE_TRANSCRIPT')
    if explicit:
        return Path(explicit).expanduser()
    if session == 'local' or Path(session).name != session:
        return None
    home = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
    roots = [home / 'projects']
    key = meter.fingerprint(['transcript_lookup', session, [str(p) for p in roots]])
    reader = _READERS.get(key)
    if reader is None:
        reader = meter.IncrementalUsageReader('Claude', session, roots)
        _cache(key, reader)
    result = reader.refresh()
    # Unfinished discovery cannot establish uniqueness. Subsequent calls resume
    # this reader under the enclosing whole-action budget.
    if not result['fully_discovered'] or reader.state['discovery_errors']:
        return None
    paths = [Path(f['path']) for f in reader.state['files'].values()
             if (f.get('metadata') or {}).get('session_id') == session]
    return paths[0] if len(paths) == 1 else None


if __package__:
    from . import native_session_meter as _package_meter
    meter = _package_meter
else:
    import native_session_meter as _flat_meter
    meter = _flat_meter

_READERS: dict[str, meter.IncrementalUsageReader] = {}
_LAST_REFRESH: dict[str, Any] = {}


def _cache(key: str, reader: meter.IncrementalUsageReader) -> None:
    _READERS[key] = reader
    if len(_READERS) > 16:
        _READERS.pop(next(iter(_READERS))).close()


def last_refresh() -> dict[str, Any]:
    return deepcopy(_LAST_REFRESH)


def read_snapshot(path: Path, session: str, *, agent: str | None = None,
                  cutoff: float | None = None, start: float | None = None) -> dict[str, Any]:
    key = meter.fingerprint([str(path.absolute()), session, agent, cutoff, start])
    reader = _READERS.get(key)
    if reader is None:
        reader = meter.IncrementalUsageReader('Claude', session, start=start, end=cutoff,
                                              known=[agent] if agent else [], expected_root=session)
        reader.add_file(path, session=agent or session, agent=agent)
        _cache(key, reader)
    result = reader.refresh()
    row = next((s for s in result['sessions'] if s['session'] == (agent or session)), None)
    if row is None or not any((f.get('metadata') or {}).get('session_id') == (agent or session)
                              for f in reader.state['files'].values()):
        raise ValueError('Claude session identity unavailable')
    return {'usage': row['usage'], 'native_usage': row['native_usage'],
            'started': min((timestamp(f['metadata'].get('started_at')) or 0)
                           for f in reader.state['files'].values()),
            'measured_at': row['measured_at'], 'interval': row['interval'],
            'partial': row['status'] != 'measured', 'errors': row['errors'],
            'active': bool(row['usage'] and row['usage']['total_tokens']),
            'usage_refresh': result}


def sessions(run: dict[str, Any], events: list[dict[str, Any]],
             cutoff: float | None) -> tuple[list[dict[str, Any]], int]:
    # Paths supplied by native hook evidence avoid a global projects inventory.
    # Fallback discovery remains incremental and shares the aggregate budget.
    root = run['session']
    explicit = run.get('transcript_path') or run.get('transcript')
    if not explicit and root == os.environ.get('TASKPLANE_CLAUDE_SESSION_ID'):
        explicit = os.environ.get('TASKPLANE_CLAUDE_TRANSCRIPT')
    home = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
    roots = [Path(explicit).with_suffix('') / 'subagents'] if explicit else [home / 'projects']
    known = set(run.get('worker_sessions', []))
    paths = []
    if explicit:
        paths.append(Path(explicit))
    for child, transcript_path in run.get('native_children', {}).items():
        known.add(child)
        if transcript_path:
            paths.append(Path(transcript_path))
    for event in events:
        if event.get('run') == run['run'] and event.get('child'):
            known.add(event['child'])
            if event.get('agent_transcript_path'):
                paths.append(Path(event['agent_transcript_path']))
    key = meter.fingerprint([root, run.get('run'), run.get('at'), cutoff,
                             [str(p) for p in roots], sorted(known)])
    reader = _READERS.get(key)
    if reader is None:
        reader = meter.IncrementalUsageReader('Claude', root, roots,
            start=timestamp(run.get('at')), end=cutoff, known=known,
            checkpoint=run.get('usage_checkpoint'), expected_root=root if explicit else None)
        _cache(key, reader)
    for path in paths:
        reader.add_file(path)
    result = reader.refresh()
    _LAST_REFRESH.clear(); _LAST_REFRESH.update(result)
    _LAST_REFRESH['checkpoint'] = reader.checkpoint()
    baseline = starting_baseline(run)
    rows = meter.run_sessions(result['sessions'], root, known,
                              start=timestamp(run.get('at')), end=cutoff)
    if not any(row['session'] == root for row in rows):
        rows.append({'session': root, 'agent': 'orchestrator', 'role': 'orchestrator',
                     'usage': None, 'native_usage': None, 'status': 'partial',
                     'errors': ['native counter unavailable'], 'attribution_unknown': True})
    for row in rows:
        if row['session'] == root:
            native = row.get('native_usage')
            if baseline is None or native is None or any(native[k] < baseline[k] for k in baseline):
                row.update(usage=None, status='partial', attribution_unknown=True)
                row['errors'].append('run baseline unavailable or reset')
            elif not row['errors']:
                row['usage'] = {k: native[k] - baseline[k] for k in baseline}
    return rows, len(reader.state['discovery_errors']) + int(not result['fully_discovered'])
