"""Claude's native session counters for the shared advisory delivery view.

Only identities, timestamps and usage leave this reader. Streamed assistant
records repeat message usage; count each API message once, not each JSONL row.
"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shlex
from typing import Any, Iterator

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
    with action_budget() as budget:
        explicit = event.get('transcript_path') or event.get('transcript')
        if not explicit and session == os.environ.get('TASKPLANE_CLAUDE_SESSION_ID'):
            explicit = os.environ.get('TASKPLANE_CLAUDE_TRANSCRIPT')
        if explicit:
            return Path(explicit).expanduser()
        if session == 'local' or Path(session).name != session:
            return None
        home = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
        found = None
        for entry in _entries(home / 'projects', budget):
            if entry.is_dir(follow_symlinks=False):
                candidate = Path(entry.path) / (session + '.jsonl')
                if candidate.is_file():
                    if found is not None:
                        return None
                    found = candidate
        # A partial inventory cannot establish unique native identity.
        return None if budget.discovery_incomplete else found


# Shared across nested counter/report/session readers for one public action.
from taskplane.claude_usage_cache import action_budget as action_budget, read_snapshot as read_snapshot
from taskplane import claude_usage_cache as _cache


def _entries(directory: Path, budget: _cache.Budget) -> Iterator[os.DirEntry[str]]:
    """Bound directory enumeration itself, including nonmatching entries."""
    try:
        with os.scandir(directory) as entries:
            while budget.entries < budget.max_entries:
                try:
                    entry = next(entries)
                except StopIteration:
                    return
                budget.take('entries')
                yield entry
            # No extra entry is consumed to prove exhaustion. Conservatively
            # report incomplete when the last allowed slot is occupied.
            budget.discovery_incomplete = True
    except FileNotFoundError:
        return
    except OSError:
        budget.discovery_incomplete = True
        return


def _sessions(run: dict[str, Any], events: list[dict[str, Any]],
             cutoff: float | None) -> tuple[list[dict[str, Any]], int]:
    root = run['session']
    path = transcript(root, run)
    candidates: dict[str, Path | None] = {root: path}
    budget = _cache._CURRENT.get()
    assert budget is not None
    observed = set()
    page = _cache.rotation(path, root + '/participants', budget) if path else 0
    workers = run.get('worker_sessions', [])
    # Supplied inventories are already in memory: indexed slices can advance
    # across actions without walking an unbounded prefix to reach the next page.
    available = budget.max_entries - budget.entries
    worker_limit = min(len(workers), available // 2 if events else available)
    if len(workers) > worker_limit:
        budget.discovery_incomplete = True
    worker_start = (page * max(1, worker_limit)) % len(workers) if workers else 0
    for index in range(worker_limit):
        budget.take('entries')
        sid = workers[(worker_start + index) % len(workers)]
        observed.add(sid)
        if path:
            candidates[sid] = path.with_suffix('') / 'subagents' / ('agent-' + sid + '.jsonl')
    # SubagentStop supplies the authoritative path, including host variations.
    event_limit = min(len(events), budget.max_entries - budget.entries)
    if len(events) > event_limit:
        budget.discovery_incomplete = True
    event_start = (page * max(1, event_limit)) % len(events) if events else 0
    # Preserve chronological last-path-wins within this bounded page.
    event_indexes = sorted((event_start + i) % len(events) for i in range(event_limit))
    for index in event_indexes:
        budget.take('entries')
        event = events[index]
        if event.get('run') == run['run'] and event.get('child'):
            child = event['child']
            observed.add(child)
            candidates.setdefault(child, None)
            if event.get('agent_transcript_path'):
                candidates[child] = Path(event['agent_transcript_path']).expanduser()
    if path:
        for entry in _entries(path.with_suffix('') / 'subagents', budget):
            if entry.name.startswith('agent-') and entry.name.endswith('.jsonl') and entry.is_file(follow_symlinks=False):
                child = Path(entry.path)
                candidates.setdefault(child.stem.removeprefix('agent-'), child)
    result = []
    errors = int(budget.discovery_incomplete)
    started = timestamp(run.get('at'))
    ordered = list(candidates.items())
    if path and len(ordered) > 1:
        shift = _cache.rotation(path, root, budget) % len(ordered)
        ordered = ordered[shift:] + ordered[:shift]
    for sid, source in ordered:
        snapshot: dict[str, Any] = {}
        try:
            if source:
                snapshot = read_snapshot(source, root, agent=None if sid == root else sid, cutoff=cutoff)
        except (OSError, ValueError):
            errors += 1
        born = snapshot.get('started')
        if sid != root and born is not None and cutoff is not None and born >= cutoff and not snapshot.get('partial'):
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
    result.sort(key=lambda item: item['session'] != root)
    return result, errors


def sessions(run: dict[str, Any], events: list[dict[str, Any]],
             cutoff: float | None) -> tuple[list[dict[str, Any]], int]:
    with action_budget(run.get('workspace')):
        return _sessions(run, events, cutoff)
