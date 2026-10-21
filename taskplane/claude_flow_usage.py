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
    paths = list((home / 'projects').glob(f'*/{session}.jsonl'))
    return paths[0] if len(paths) == 1 else None


def read_snapshot(path: Path, session: str, *, agent: str | None = None,
                  cutoff: float | None = None, start: float | None = None) -> dict[str, Any]:
    messages: dict[str, list[tuple[float | None, dict[str, int] | None]]] = {}
    errors: set[str] = set()
    started = None
    matched = False
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue  # A final line can still be in flight.
            if not isinstance(row, dict) or row.get('sessionId') != session:
                continue
            if agent and row.get('agentId') != agent:
                continue
            if not agent and (row.get('agentId') or row.get('isSidechain')):
                continue
            at = timestamp(row.get('timestamp'))
            matched = True
            if at is not None:
                started = at if started is None else min(started, at)
            message = row.get('message')
            if row.get('type') != 'assistant' or not isinstance(message, dict):
                continue
            key = message.get('id') or row.get('requestId')
            if not key or message.get('model') == '<synthetic>':
                continue
            if at is None and (cutoff is not None or start is not None):
                errors.add('missing_message_timestamp')
                continue
            if cutoff is not None and at is not None and at >= cutoff:
                continue
            raw_usage = message.get('usage')
            usage = normalize_usage(raw_usage if isinstance(raw_usage, dict) else {}, provider='claude')
            if not usage['available']:
                messages.setdefault(key, []).append((at, None))
                continue
            # Cache writes are uncached input in the shared host-neutral view.
            uncached = usage['uncached_input_tokens'] + usage['cache_creation_tokens']
            value = {'input_tokens': uncached + usage['cached_input_tokens'],
                     'cached_input_tokens': usage['cached_input_tokens'],
                     'uncached_input_tokens': uncached,
                     'output_tokens': usage['output_tokens'],
                     'reasoning_tokens': usage['reasoning_tokens'],
                     'total_tokens': usage['raw_total_tokens']}
            messages.setdefault(key, []).append((at, value))
    if not matched:
        raise ValueError('Claude session identity unavailable')
    measured = []
    active = False
    for records in messages.values():
        # Stable order preserves streamed rows sharing a native timestamp.
        records.sort(key=lambda row: row[0] if row[0] is not None else float('-inf'))
        present = [value for _, value in records if value is not None]
        if any(any(b[k] < a[k] for k in USAGE_KEYS) for a, b in zip(present, present[1:])):
            errors.add('counter_decreased')
            continue
        end_value = records[-1][1]
        if start is None:
            active = True
            if end_value is None:
                errors.add('missing_message_usage')
            else:
                measured.append(end_value)
            continue
        owned = [(at, value) for at, value in records if at is not None and at >= start]
        active = active or bool(owned)
        if not owned:
            continue
        prior = [value for at, value in records if at is not None and at < start]
        baseline = prior[-1] if prior else dict.fromkeys(USAGE_KEYS, 0)
        if baseline is None or end_value is None:
            errors.add('missing_message_boundary')
            continue
        measured.append({k: end_value[k] - baseline[k] for k in USAGE_KEYS})
    total = ({key: sum(m[key] for m in measured) for key in USAGE_KEYS}
             if measured or (start is not None and not errors) else None)
    sample_times = [at for records in messages.values() for at, value in records if at is not None and value is not None]
    return {'usage': total, 'started': started,
            'measured_at': datetime.fromtimestamp(max(sample_times), timezone.utc).isoformat() if sample_times else None,
            'interval': {'start': start, 'end_exclusive': cutoff},
            'partial': bool(errors), 'errors': sorted(errors), 'active': active}


def sessions(run: dict[str, Any], events: list[dict[str, Any]],
             cutoff: float | None) -> tuple[list[dict[str, Any]], int]:
    root = run['session']
    path = transcript(root, run)
    candidates: dict[str, Path | None] = {root: path}
    observed = set(run.get('worker_sessions', []))
    if path:
        for child in path.with_suffix('').glob('subagents/agent-*.jsonl'):
            candidates[child.stem.removeprefix('agent-')] = child
    # SubagentStop supplies the authoritative path, including host variations.
    for event in events:
        if event.get('run') == run['run'] and event.get('child'):
            child = event['child']
            observed.add(child)
            candidates.setdefault(child, None)
            if event.get('agent_transcript_path'):
                candidates[child] = Path(event['agent_transcript_path']).expanduser()
    result = []
    errors = 0
    started = timestamp(run.get('at'))
    for sid, source in candidates.items():
        snapshot: dict[str, Any] = {}
        try:
            if source:
                snapshot = read_snapshot(source, root, agent=None if sid == root else sid, cutoff=cutoff)
        except (OSError, ValueError):
            errors += 1
        born = snapshot.get('started')
        if sid != root and born is not None and cutoff is not None and born >= cutoff:
            continue
        native = snapshot.get('usage')
        baseline = starting_baseline(run) if sid == root else {}
        session_errors = list(snapshot.get('errors', []))
        if sid == root and baseline is None:
            session_errors.append('run baseline is partial or unavailable; attribution is unknown')
            snapshot['partial'] = True
        usage = ({k: max(0, v - baseline.get(k, 0)) for k, v in native.items()}
                 if native is not None and baseline is not None else None)
        if native is not None and baseline is not None and any(v < baseline.get(k, 0) for k, v in native.items()):
            usage = None
            snapshot['partial'] = True
            session_errors.append('native counter moved below run baseline; reset attribution is unknown')
        if sid != root and born is not None and started is not None and born < started and source:
            try:
                interval = read_snapshot(source, root, agent=sid, cutoff=cutoff, start=started)
                usage = interval['usage']
                snapshot['partial'] = snapshot.get('partial') or interval['partial']
                session_errors.extend(interval.get('errors', []))
                if not interval['active'] and not interval['partial'] and sid not in observed:
                    continue  # An old inactive child is not part of this run.
            except (OSError, ValueError):
                errors += 1
                usage = None
                snapshot['partial'] = True
        result.append({'session': sid, 'agent': 'orchestrator' if sid == root else sid,
                       'role': 'orchestrator' if sid == root else 'lens',
                       'usage': usage, 'native_usage': native,
                       'status': 'partial' if snapshot.get('partial') else 'measured' if usage else 'unavailable',
                       'errors': sorted(set(session_errors)),
                       'measured_at': snapshot.get('measured_at'), 'measurement_source': 'native_sample',
                       'attribution_unknown': bool(session_errors),
                       'basis': 'start baseline' if sid == root else 'owned message interval [start, end)',
                       'attribution_schema': 'taskplane.owned-interval/v1' if sid != root else None,
                       'interval': {'start': started, 'end_exclusive': cutoff} if sid != root else None})
    return result, errors
