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
from typing import Any, cast
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


def terminal_transcript(workspace: str, contract: dict[str, Any], event: dict[str, Any]) -> tuple[str, str]:
    """Select only the active lifecycle owner's exact native child metadata.

    The hook must load its authenticated active slot before calling. Parent
    transcript fields never select a child counter or provide missing identity.
    """
    lifecycle = contract.get("worker_lifecycle") or {}
    owner = lifecycle.get("owner")
    if not isinstance(owner, dict) or lifecycle.get("status") != "active":
        raise ValueError("native terminal transcript requires an active child owner")
    identity = {"agent_id":event.get("agent_id"),
        "session_id":event.get("session_id") or event.get("thread_id"),
        "task_name":event.get("task_name") or event.get("agent_type")}
    if (any(not value or owner.get(key) != value for key,value in identity.items())
            or identity["task_name"] != lifecycle.get("expected_task_name")
            or os.path.realpath(str(event.get("cwd") or "")) != os.path.realpath(workspace)):
        raise ValueError("native terminal event differs from the bound child owner")
    matched = _matching_child(event)
    if matched is None or matched[0] != owner["task_name"]:
        raise ValueError("native terminal transcript has no exact child metadata")
    return matched[1], matched[2]


def observed_usage(workspace: str, terminal: dict[str, Any], *,
                   codex_home: str | None = None) -> dict[str, Any]:
    """Read the exact child's counter at an authenticated Start or Stop.

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
    snapshot = native_session_meter.read_snapshot(matched[1], at_or_before=stopped_at)
    counter_at = datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
    if snapshot["session_id"] != owner["agent_id"] or \
            snapshot["root_session_id"] != owner["session_id"] or \
            snapshot["parent_session_id"] != owner["session_id"] or \
            snapshot["source"]["metadata_record_sha256"] != matched[2] or \
            snapshot["resumed"] or counter_at.tzinfo is None or \
            counter_at.timestamp() > stopped_at:
        raise ValueError("terminal native usage is foreign or later than the stopped attempt")
    return snapshot


def completed_child(workspace: str, start: dict[str, Any]) -> dict[str, Any]:
    """Read current provider completion for an authenticated Start's child.

    This is not a recovered SubagentStop. Only bounded lifecycle metadata is
    returned; messages are neither interpreted nor retained.
    """
    from taskplane import review_evidence
    owner = start["owner"]
    matched = _matching_child({**owner, "cwd":workspace},
        now=datetime.fromtimestamp(start["observed_at"], timezone.utc))
    if matched is None or matched[0] != owner["task_name"]:
        raise ValueError("completed worker has no exact provider identity")
    descriptor = os.open(matched[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        offset = max(0, before.st_size - MAX_METADATA_BYTES)
        stream.seek(offset)
        tail = stream.read(MAX_METADATA_BYTES)
        after = os.fstat(stream.fileno())
    if any(getattr(before, key) != getattr(after, key) for key in ("st_ino", "st_size", "st_mtime_ns")):
        raise ValueError("provider lifecycle changed during observation")
    if offset:
        tail = tail.partition(b"\n")[2]
    latest = None
    for raw in tail.splitlines():
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(row, dict) or row.get("type") != "event_msg":
            continue
        payload = row.get("payload")
        if isinstance(payload, dict) and payload.get("type") in {"task_started", "task_complete", "turn_aborted"}:
            latest = {key:payload.get(key) for key in ("type", "turn_id", "started_at", "completed_at", "duration_ms")}
    if not latest or latest["type"] != "task_complete" or latest["turn_id"] != start["turn_id"]:
        raise ValueError("exact provider turn is not currently completed")
    ended = latest["completed_at"]
    began = latest["started_at"]
    if type(ended) not in (int, float) or type(began) not in (int, float) or not (
            0 <= cast(int | float, began) <= start["observed_at"] <= cast(int | float, ended) <= datetime.now(timezone.utc).timestamp()):
        raise ValueError("provider completion time is invalid")
    result = {"source":"codex-task-complete", "owner":dict(owner), "turn_id":latest["turn_id"],
        "observed_at":ended, "outcome":"complete", "tokens":None, "provider_lifecycle":latest}
    return {**result, "claim":"codex-task-complete:" + review_evidence.content_fingerprint(result)}
