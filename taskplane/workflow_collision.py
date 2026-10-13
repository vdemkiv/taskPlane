"""Protect another active native run's sealed paths before write admission."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from . import workflow as w, workflow_evidence as evidence

MAX_DATABASES = 128
MAX_BYTES = 64 * 1024 * 1024


def sealed_paths(workspace: Path, current: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Read bounded exact controller filenames; history is never rewritten.

    This is a cooperative pre-tool guard, not an OS lock across native writes.
    Completed/retired runs are historical; another active root's approvals must
    not be silently invalidated by this run's ordinary source/artifact writes.
    """
    root = evidence.path(workspace, '.taskplane')
    if not root.exists():
        return {}
    paths = sorted(p for p in root.iterdir() if re.fullmatch(r'workflow-[a-f0-9]{32}\.json', p.name))
    w.require(len(paths) <= MAX_DATABASES, 'state_unavailable', 'Active-run collision inventory exceeds its bounded database count.')
    remaining = MAX_BYTES
    protected: dict[str, dict[str, str]] = {}
    for path in paths:
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                w.require(stat.S_ISREG(info.st_mode) and info.st_size <= remaining, 'state_unavailable',
                          'Active-run collision inventory is unsafe or exceeds its byte bound.')
                raw = stream.read(remaining + 1)
                remaining -= len(raw)
                w.require(remaining >= 0, 'state_unavailable', 'Active-run collision inventory changed beyond its byte bound.')
            db = json.loads(raw)
            if db.get('root') == current['root'] or db.get('workspace') != str(workspace) or not db.get('active'):
                continue
            other = db.get('runs', {}).get(db['active'])
            w.require(isinstance(other, dict) and other.get('root') == db['root'], 'state_unavailable',
                      'Another active workflow has an incomplete controller binding.')
            if any(other.get(key) for key in ('finished', 'retired', 'superseded_by')):
                continue
            for visit in other.get('visits', []):
                if visit.get('superseded') or visit.get('decision') not in {'approved', 'awaiting_human_approval'}:
                    continue
                packet = visit.get('packet') or {}
                for key in ('manifest', 'source_manifest'):
                    for relative in packet.get(key, {}):
                        protected[relative] = {'root': other['root'], 'run': other['run'], 'phase': visit['phase']}
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise w.Refusal('state_unavailable', 'Another workflow controller cannot be safely checked before writing.') from exc
    return protected
