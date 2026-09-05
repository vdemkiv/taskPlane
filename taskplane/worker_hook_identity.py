"""Correlate a current Codex hook with bounded host-owned identity records.

Codex's ``agent_type`` is a profile, not ``spawn_agent.task_name``. A live
SubagentStart can be correlated with the parent's exact SubAgentActivity;
child actions and SubagentStop use that child's session metadata. This module
does not activate contracts, infer completion, import conversation content,
search other sessions, or create lifecycle/usage authority.

The transcript format is a versioned host adapter, not a stable public API.
Unknown or conflicting records are unavailable, never a profile-based guess.
"""
from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from typing import Any


MAX_PREFIX = 256 * 1024
MAX_TAIL = 4 * 1024 * 1024
MAX_RECORD = 2 * 1024 * 1024
_PATH = re.compile(r"/root(?:/[a-z0-9_]+)+")


def _read(path: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
                         getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("native worker identity source is not a regular file")
        prefix = stream.read(MAX_PREFIX)
        offset = max(0, before.st_size - MAX_TAIL)
        stream.seek(offset)
        tail = stream.read(MAX_TAIL)
        after = os.fstat(stream.fileno())
    if any(getattr(before, key) != getattr(after, key)
           for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns")):
        raise ValueError("native worker identity changed while reading")
    if offset:
        _, _, tail = tail.partition(b"\n")
    first = prefix.split(b"\n", 1)[0]
    try:
        metadata = json.loads(first)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("native worker session metadata is unavailable") from exc
    if (not isinstance(metadata, dict) or metadata.get("type") != "session_meta"
            or not isinstance(metadata.get("payload"), dict)):
        raise ValueError("native worker session metadata is unavailable")
    records = []
    for raw in tail.splitlines():
        if len(raw) > MAX_RECORD:
            continue
        try:
            row = json.loads(raw)
        except (UnicodeError, ValueError):
            continue
        if isinstance(row, dict) and row.get("type") == "event_msg":
            payload = row.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "item_completed":
                item = payload.get("item")
                if isinstance(item, dict) and item.get("type") == "SubAgentActivity":
                    records.append(payload)
    return metadata["payload"], records


def _path(value: object) -> str:
    if not isinstance(value, str) or len(value) > 1024 or not _PATH.fullmatch(value):
        raise ValueError("native worker task path is unavailable or malformed")
    return value


def resolve(event: Mapping[str, Any]) -> tuple[dict[str, str], int | None] | None:
    """Return canonical owner and observed start time, only for profile hooks.

Explicit task-name events retain their incumbent adapter. ``None`` means a
root or a non-profile event, not permission to guess a governed child.
"""
    if (not event.get("turn_id") or event.get("task_name") or
            str(event.get("agent_type") or "").startswith("tp_")):
        return None
    lifecycle = event.get("hook_event_name") in {"SubagentStart", "SubagentStop"}
    source = (event.get("agent_transcript_path") if
              event.get("hook_event_name") == "SubagentStop" else None) or event.get(
                  "transcript_path")
    if not source:
        if event.get("agent_id"):
            raise ValueError("native worker identity needs the current hook transcript")
        return None
    if not isinstance(source, str):
        raise ValueError("native worker identity source is malformed")
    try:
        metadata, activity = _read(source)
    except OSError as exc:
        raise ValueError("native worker identity source is unavailable") from exc
    session = event.get("session_id")
    if not isinstance(session, str) or not session:
        raise ValueError("native worker hook session is missing")
    if not metadata.get("cwd") or os.path.realpath(str(metadata["cwd"])) != os.path.realpath(
            str(event.get("cwd") or "")):
        raise ValueError("native worker metadata belongs to another checkout")
    native_source = metadata.get("source")
    subagent = native_source.get("subagent") if isinstance(native_source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    parent = metadata.get("parent_thread_id")
    if isinstance(spawn, dict) and (not lifecycle or (
            event.get("hook_event_name") == "SubagentStop" and event.get("agent_transcript_path"))):
        child = metadata.get("id")
        if (not isinstance(child, str) or not child or not isinstance(parent, str) or
                not parent or spawn.get("parent_thread_id") != parent or
                session != (parent if lifecycle else child) or
                (event.get("agent_id") and event["agent_id"] != child)):
            raise ValueError("native worker hook and child lineage disagree")
        path = _path(spawn.get("agent_path"))
        if metadata.get("agent_path", path) != path:
            raise ValueError("native worker task paths disagree")
        return {"session_id": parent, "agent_id": child,
                "task_name": path.rsplit("/", 1)[1]}, None
    if metadata.get("id") != session:
        raise ValueError("native worker hook and parent session disagree")
    child = event.get("agent_id")
    if not child and not lifecycle:
        if parent or isinstance(subagent, dict):
            raise ValueError("native child metadata has no exact spawn lineage")
        return None
    if not isinstance(child, str) or not child:
        raise ValueError("native lifecycle child ID is missing")
    starts = [row for row in activity if row.get("thread_id") == session and
              row["item"].get("agent_thread_id") == child and
              row["item"].get("kind") == "started"]
    if len(starts) != 1:
        raise ValueError("native hook has no unique matching child start identity")
    start = starts[0]
    path = _path(start["item"].get("agent_path"))
    parent_path = _path(spawn.get("agent_path")) if isinstance(spawn, dict) else "/root"
    if path.rsplit("/", 1)[0] != parent_path:
        raise ValueError("native worker task is not a direct child of this parent")
    stamp = start.get("started_at_ms")
    if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0:
        raise ValueError("native worker start time is unavailable")
    if event.get("hook_event_name") == "SubagentStart" and any(
            row.get("thread_id") == session and
            row["item"].get("agent_thread_id") == child and
            row["item"].get("kind") == "completed" for row in activity):
        raise ValueError("native child start identity is already terminal")
    return {"session_id": session, "agent_id": child,
            "task_name": path.rsplit("/", 1)[1]}, stamp
