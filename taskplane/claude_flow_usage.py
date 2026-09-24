"""Claude's native session counters for the shared advisory delivery view.

Only identities, timestamps and usage leave this reader. Streamed assistant
records repeat message usage; count each API message once, not each JSONL row.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shlex
from typing import Any

if __package__:
    from .spend import normalize_usage as _package_normalize
    normalize_usage = _package_normalize
else:
    from spend import normalize_usage as _flat_normalize
    normalize_usage = _flat_normalize


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
    messages: dict[str, dict[str, int] | None] = {}
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
            if cutoff is not None and (at is None or at >= cutoff):
                continue
            if start is not None and (at is None or at < start):
                continue
            message = row.get('message')
            if row.get('type') != 'assistant' or not isinstance(message, dict):
                continue
            key = message.get('id') or row.get('requestId')
            if not key or message.get('model') == '<synthetic>':
                continue
            raw_usage = message.get('usage')
            usage = normalize_usage(raw_usage if isinstance(raw_usage, dict) else {}, provider='claude')
            if not usage['available']:
                messages.setdefault(key, None)
                continue
            # Cache writes are uncached input in the shared host-neutral view.
            uncached = usage['uncached_input_tokens'] + usage['cache_creation_tokens']
            value = {'input_tokens': uncached + usage['cached_input_tokens'],
                     'cached_input_tokens': usage['cached_input_tokens'],
                     'uncached_input_tokens': uncached,
                     'output_tokens': usage['output_tokens'],
                     'reasoning_tokens': usage['reasoning_tokens'],
                     'total_tokens': usage['raw_total_tokens']}
            previous = messages.get(key)
            if previous is None or value['total_tokens'] >= previous['total_tokens']:
                messages[key] = value
    if not matched:
        raise ValueError('Claude session identity unavailable')
    measured = [m for m in messages.values() if m is not None]
    total = {key: sum(m[key] for m in measured) for key in measured[0]} if measured else None
    return {'usage': total, 'started': started,
            'partial': any(m is None for m in messages.values())}


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
        baseline = run.get('usage') if sid == root else {}
        usage = ({k: max(0, v - baseline.get(k, 0)) for k, v in native.items()}
                 if native is not None and baseline is not None else None)
        if native is not None and baseline is not None and any(v < baseline.get(k, 0) for k, v in native.items()):
            usage = None
            snapshot['partial'] = True
        if sid != root and born is not None and started is not None and born < started and source:
            try:
                interval = read_snapshot(source, root, agent=sid, cutoff=cutoff, start=started)
                usage = interval['usage']
                snapshot['partial'] = snapshot.get('partial') or interval['partial']
                if usage is None and not interval['partial'] and sid not in observed:
                    continue  # An old inactive child is not part of this run.
            except (OSError, ValueError):
                errors += 1
                usage = None
                snapshot['partial'] = True
        result.append({'session': sid, 'agent': 'orchestrator' if sid == root else sid,
                       'role': 'orchestrator' if sid == root else 'lens',
                       'usage': usage, 'native_usage': native,
                       'status': 'partial' if snapshot.get('partial') else 'measured' if usage else 'unavailable',
                       'basis': 'start baseline' if sid == root else 'owned message interval [start, end)',
                       'attribution_schema': 'taskplane.owned-interval/v1' if sid != root else None,
                       'interval': {'start': started, 'end_exclusive': cutoff} if sid != root else None})
    return result, errors
