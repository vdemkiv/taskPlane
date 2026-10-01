"""Bounded Claude launch observations, never host authority or prompt inference.

Only native tool-use/result relationships and a matching sidechain header bind a
child. A missing record is unknown, including a result not yet flushed to disk.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

from .context import digest

MAX_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 256 * 1024
MAX_RECORDS = 16384
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


def _stamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp has no timezone")
    return parsed


def _answer(status: str, reason: str, **values: Any) -> dict[str, Any]:
    return {"schema": "taskplane.worker-identity-observation/v1", "host": "claude",
            "status": status, "reason": reason, "assurance": "observed", **values}


def _unique(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    # Repeated identical delivery is idempotent; competing records are not.
    return list({digest(row): row for row in records}.values())


def correlate_records(parent: str, attempt: Mapping[str, Any],
                      records: Sequence[Mapping[str, Any]],
                      child_headers: Sequence[Mapping[str, Any]], *,
                      observed_at: str | None = None) -> dict[str, Any]:
    """Pure parser for host-selected records; fixture inputs do not prove origin."""
    call_id, workspace = attempt.get("call_id"), attempt.get("workspace")
    if (not isinstance(parent, str) or not _ID.fullmatch(parent)
            or not isinstance(call_id, str) or not call_id or len(call_id) > 512
            or not isinstance(workspace, str) or not workspace
            or not isinstance(attempt.get("dispatch_digest"), str)):
        return _answer("conflict", "Incomplete admitted launch binding")
    if len(records) > MAX_RECORDS or len(child_headers) > MAX_RECORDS:
        return _answer("unsupported", "Native record bound exceeded")
    if not all(isinstance(row, Mapping) for row in [*records, *child_headers]):
        return _answer("conflict", "Malformed native record")
    uses: list[Mapping[str, Any]] = []
    results: list[Mapping[str, Any]] = []
    for row in records:
        message = row.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, Mapping):
                continue
            if item.get("type") == "tool_use" and item.get("id") == call_id:
                uses.append({"record": row, "item": item})
            if item.get("type") == "tool_result" and item.get("tool_use_id") == call_id:
                results.append({"record": row, "item": item})
    uses, results = _unique(uses), _unique(results)
    if len(uses) > 1 or len(results) > 1:
        return _answer("conflict", "Conflicting native launch call or result")
    if not uses or not results:
        return _answer("not_yet_available", "Exact native launch call/result is not readable")
    use, result = uses[0], results[0]
    call, returned = use["record"], result["record"]
    structured = returned.get("toolUseResult")
    if (use["item"].get("name") not in {"Agent", "Task"}
            or not isinstance(use["item"].get("input"), Mapping)
            or digest(use["item"]["input"]) != attempt["dispatch_digest"]
            or result["item"].get("is_error") is True
            or any(row.get("sessionId") != parent or row.get("cwd") != workspace
                   or row.get("isSidechain") is True for row in (call, returned))):
        return _answer("conflict", "Native launch disagrees with admitted parent, workspace or arguments")
    if not isinstance(structured, Mapping):
        return _answer("unsupported", "Native result has no supported structured identity")
    child = structured.get("agentId")
    if (structured.get("isAsync") is not True or structured.get("status") != "async_launched"):
        return _answer("unsupported", "Native launch status is not supported")
    if not isinstance(child, str) or not _ID.fullmatch(child) or child == parent:
        return _answer("conflict", "Native result has an invalid child identity")
    if attempt.get("worker_id") not in (None, child):
        return _answer("conflict", "Native result changed the prepared worker identity")
    headers = _unique([row for row in child_headers if row.get("agentId") == child])
    if not headers:
        return _answer("not_yet_available", "Matching child header is not readable")
    if len(headers) != 1:
        return _answer("conflict", "Conflicting child headers")
    header = headers[0]
    if (header.get("sessionId") != parent or header.get("isSidechain") is not True
            or header.get("cwd") != workspace):
        return _answer("conflict", "Child header has foreign parent or workspace")
    try:
        prepared = _stamp(attempt.get("prepared_at"))
        called, returned_at, started = (_stamp(row.get("timestamp")) for row in (call, returned, header))
        observed = _stamp(observed_at) if observed_at else datetime.now(timezone.utc)
        if not prepared <= called <= returned_at <= observed or not called <= started <= observed:
            raise ValueError("Out-of-order native timestamps")
    except (ValueError, TypeError, OverflowError):
        return _answer("conflict", "Native timestamps are missing, future or out of order")
    evidence = {"call": digest(call), "result": digest(returned), "header": digest(header)}
    return _answer("matched", "Exact native launch call/result and child header",
                   parent=parent, workspace=workspace, call_id=call_id, worker_id=child,
                   grant_id=attempt.get("grant_id"), attempt=attempt.get("attempt"),
                   dispatch_digest=attempt["dispatch_digest"], record_sha256=evidence,
                   evidence_sha256=digest(evidence), called_at=call["timestamp"],
                   started_at=header["timestamp"], observed_at=returned["timestamp"])


def _open_regular(path: Path) -> int:
    # Hold each directory descriptor while opening its child. A check followed
    # by a normal open would permit a swapped parent symlink to escape the root.
    if not path.is_absolute() or os.open not in os.supports_dir_fd or not hasattr(os, 'O_NOFOLLOW'):
        raise ValueError('Native no-follow reads are unavailable')
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory)
    finally:
        os.close(directory)


def _read(path: Path, *, first_only: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fd = _open_regular(path)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Native transcript is not regular")
        offset = 0 if first_only else max(0, info.st_size - MAX_BYTES)
        stream.seek(offset)
        if offset:
            stream.readline(MAX_LINE_BYTES + 1)
        offset = stream.tell()
        raw = stream.readline(MAX_LINE_BYTES + 1) if first_only else stream.read(MAX_BYTES)
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break  # A concurrently written tail is not a complete record.
        if len(line) > MAX_LINE_BYTES:
            raise ValueError("Native record exceeds byte bound")
        if len(rows) >= MAX_RECORDS:
            raise ValueError("Native transcript exceeds record bound")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("Malformed native record")
        rows.append(value)
    return rows, {"source": str(path), "offset": offset, "bytes": len(raw),
                  "sha256": hashlib.sha256(raw).hexdigest()}


def observe(parent: str, attempt: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    """Read only the selected parent's native project and the exact child file.

    outputFile and arbitrary event transcript paths never expand the read scope.
    Unsupported Claude layouts remain unknown instead of scanning other projects.
    """
    workspace = attempt.get("workspace")
    if (not isinstance(parent, str) or not _ID.fullmatch(parent)
            or not isinstance(workspace, str) or not Path(workspace).is_absolute()):
        return _answer("conflict", "Invalid native parent or workspace")
    project = re.sub(r"[^A-Za-z0-9]", "-", workspace)
    path = Path.home() / ".claude" / "projects" / project / (parent + ".jsonl")
    supplied = event.get("transcript_path") or event.get("transcript")
    if supplied is not None and str(path) != supplied:
        return _answer("conflict", "Hook transcript differs from selected native parent")
    try:
        rows, source = _read(path)
        # Discover the child only from the exact structured result, never text.
        children = set()
        for row in rows:
            message = row.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list) and any(isinstance(item, dict)
                    and item.get("type") == "tool_result" and item.get("tool_use_id") == attempt.get("call_id")
                    for item in content):
                result = row.get("toolUseResult")
                if isinstance(result, dict) and isinstance(result.get("agentId"), str):
                    children.add(result["agentId"])
        headers, sources = [], [source]
        for child in sorted(children):
            if not _ID.fullmatch(child) or child == parent:
                return _answer("conflict", "Invalid native child filename")
            try:
                head, reference = _read(path.with_suffix("") / "subagents" / ("agent-" + child + ".jsonl"), first_only=True)
                headers.extend(head)
                sources.append(reference)
            except FileNotFoundError:
                pass
        answer = correlate_records(parent, attempt, rows, headers)
        answer["references"] = sources
        return answer
    except FileNotFoundError:
        return _answer("not_yet_available", "Native transcript is not readable yet")
    except (OSError, ValueError, TypeError, UnicodeError):
        return _answer("conflict", "Native transcript is unsafe, malformed or exceeds read bounds")
