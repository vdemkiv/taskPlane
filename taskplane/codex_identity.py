"""Normalize Codex v2 child identity at the host boundary.

`agent_type=default` is a host role, not the native task name. Read only the
exact child's bounded session metadata, never its messages or its parent's
conversation. This is a read-only adapter, not a new owner/receipt registry.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from uuid import UUID


MAX_METADATA_BYTES = 128 * 1024
_AGENT_PATH = re.compile(r"/root/(?:[a-z0-9_]+/)*([a-z0-9_]+)\Z")


def normalize_lifecycle(event: dict[str, Any], *, codex_home: str | None = None,
                        now: datetime | None = None) -> dict[str, Any]:
    """Resolve a generic child only when exact host metadata agrees.

    A missing or ambiguous record leaves identity unresolved. Never select a
    child by the number of pending slots, a role label or an agent prompt.
    The caller can report the refusal, but must not bind that event.
    """
    if (event.get("hook_event_name") not in {"SubagentStart", "SubagentStop"}
            or event.get("agent_type") != "default"
            or event.get("task_name")):
        return event
    matched = _matching_child(event, codex_home=codex_home, now=now)
    return event if matched is None else {**event, "task_name": matched[0]}


def _matching_child(event: dict[str, Any], *, codex_home: str | None = None,
                    now: datetime | None = None) -> tuple[str, str, str] | None:
    """One exact host-owned metadata source; no conversation reconstruction."""
    child = str(event.get("agent_id") or "")
    session = str(event.get("session_id") or event.get("thread_id") or "")
    try:
        if str(UUID(child)) != child or not session or not event.get("cwd"):
            return None
    except ValueError:
        return None
    home = codex_home or os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    root = (Path(home) / "sessions").resolve()
    supplied = event.get("agent_transcript_path")
    if supplied:
        candidates = [Path(supplied)] if isinstance(supplied, str) else []
    else:
        # Start events may precede the transcript-path field. Bound discovery
        # to three date directories and this exact native UUID; never scan
        # predecessor sessions or read conversation contents for context.
        instant = now or datetime.now(timezone.utc)
        candidates = []
        for offset in (-1, 0, 1):
            day = instant + timedelta(days=offset)
            directory = root / day.strftime("%Y/%m/%d")
            candidates.extend(directory.glob(f"rollout-*-{child}.jsonl"))
    matches = []
    for path in candidates:
        try:
            resolved = path.resolve()
            if (not resolved.is_relative_to(root) or path.is_symlink()
                    or not path.name.endswith(f"-{child}.jsonl")):
                continue
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    continue
                raw = stream.readline(MAX_METADATA_BYTES + 1)
            if len(raw) > MAX_METADATA_BYTES or not raw.endswith(b"\n"):
                continue
            record = json.loads(raw)
            meta = record.get("payload")
            if record.get("type") != "session_meta" or not isinstance(meta, dict):
                continue
            spawn = ((meta.get("source") or {}).get("subagent") or {}).get("thread_spawn")
            if not isinstance(spawn, dict):
                continue
            parent = str(event.get("parent_thread_id") or session)
            agent_path = str(meta.get("agent_path") or "")
            name = _AGENT_PATH.fullmatch(agent_path)
            if (meta.get("id") != child or meta.get("session_id") != session
                    or meta.get("parent_thread_id") != parent
                    or spawn.get("parent_thread_id") != parent
                    or spawn.get("agent_path") != agent_path or not name
                    or (event.get("agent_path") and event["agent_path"] != agent_path)
                    or os.path.realpath(str(meta.get("cwd") or "")) !=
                    os.path.realpath(str(event["cwd"]))):
                continue
            matches.append((name.group(1), str(resolved),
                hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return matches[0] if len(matches) == 1 else None


def terminal_usage(workspace: str, terminal: dict[str, Any], *,
                   codex_home: str | None = None) -> dict[str, Any]:
    """Read the exact stopped child's host counter when Stop has no usage.

    The caller must authenticate the terminal receipt first. This does not
    rewrite that receipt, replay a hook, read messages or invent a counter.
    A later resumed segment/counter cannot be charged to this terminal.
    """
    from taskplane import native_session_meter
    owner = terminal["owner"]
    stopped_at = float(terminal["observed_at"])
    matched = _matching_child({**owner, "cwd": workspace}, codex_home=codex_home,
        now=datetime.fromtimestamp(stopped_at, timezone.utc))
    if matched is None or matched[0] != owner["task_name"]:
        raise ValueError("terminal native usage has no exact child source")
    snapshot = native_session_meter.read_snapshot(matched[1])
    counter_at = datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
    if snapshot["session_id"] != owner["agent_id"] or \
            snapshot["root_session_id"] != owner["session_id"] or \
            snapshot["parent_session_id"] != owner["session_id"] or \
            snapshot["source"]["metadata_record_sha256"] != matched[2] or \
            snapshot["resumed"] or counter_at.tzinfo is None or \
            counter_at.timestamp() > stopped_at:
        raise ValueError("terminal native usage is foreign or later than the stopped attempt")
    return snapshot
