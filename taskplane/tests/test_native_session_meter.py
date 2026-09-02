from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskplane import native_session_meter


def _write_segment(
    path: Path,
    *,
    session_id: str,
    total: int | None,
    cached: int = 0,
    output: int = 1,
    parent: str | None = None,
    resumed: bool = False,
    ordinal: int = 7,
) -> None:
    metadata = {
        "session_id": "root-session",
        "id": session_id,
        "timestamp": "2026-09-01T00:00:00Z",
        "thread_source": "subagent" if parent else "agent_created_thread",
    }
    if parent:
        metadata.update({
            "forked_from_id": parent,
            "parent_thread_id": parent,
            "source": {"subagent": {"thread_spawn": {
                "parent_thread_id": parent,
                "agent_path": f"/root/{session_id}",
            }}},
        })
    if resumed:
        metadata["history_base"] = {
            "thread_id": session_id,
            "end_ordinal_exclusive": 100,
            "end_byte_offset": 200,
        }
    rows: list[dict] = [{
        "timestamp": metadata["timestamp"],
        "type": "session_meta",
        "payload": metadata,
    }]
    rows.append({
        "timestamp": "2026-09-01T00:00:01Z",
        "type": "response_item",
        "payload": {"type": "message", "content": "private conversation"},
    })
    if total is not None:
        input_tokens = total - output
        rows.append({
            "timestamp": f"2026-09-01T00:00:{ordinal:02d}Z",
            "ordinal": ordinal,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached,
                    "cache_write_input_tokens": 0,
                    "output_tokens": output,
                    "reasoning_output_tokens": 0,
                    "total_tokens": total,
                }},
            },
        })
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_reads_current_metadata_and_latest_native_counter_without_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "worker.jsonl"
    _write_segment(
        source, session_id="worker-1", parent="root-session",
        total=125, cached=100, output=5,
    )

    snapshot = native_session_meter.read_snapshot(str(source))

    assert snapshot["session_id"] == "worker-1"
    assert snapshot["parent_session_id"] == "root-session"
    assert snapshot["agent_path"] == "/root/worker-1"
    assert snapshot["usage"] == {
        "input_tokens": 120,
        "cached_input_tokens": 100,
        "uncached_input_tokens": 20,
        "output_tokens": 5,
        "reasoning_tokens": 0,
        "total_tokens": 125,
    }
    assert "private conversation" not in json.dumps(snapshot)


def test_resume_uses_latest_cumulative_counter_and_fork_counts_once(
    tmp_path: Path,
) -> None:
    first = tmp_path / "root-1.jsonl"
    resumed = tmp_path / "root-2.jsonl"
    child = tmp_path / "child.jsonl"
    _write_segment(first, session_id="root", total=90, ordinal=7)
    _write_segment(
        resumed, session_id="root", total=110, resumed=True, ordinal=11,
    )
    _write_segment(
        child, session_id="child", parent="root", total=40, ordinal=5,
    )

    wave = native_session_meter.aggregate([
        native_session_meter.read_snapshot(str(first)),
        native_session_meter.read_snapshot(str(resumed)),
        native_session_meter.read_snapshot(str(child)),
    ])

    assert wave["physical_segments"] == 3
    assert wave["logical_sessions"] == 2
    assert wave["usage"]["total_tokens"] == 150
    assert {row["session_id"]: row["segments"] for row in wave["sessions"]} \
        == {"child": 1, "root": 2}


def test_missing_counter_and_backwards_resume_refuse_instead_of_zero(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.jsonl"
    _write_segment(missing, session_id="missing", total=None)
    with pytest.raises(
        native_session_meter.NativeSessionMeterError,
        match="no complete token counter",
    ):
        native_session_meter.read_snapshot(str(missing))

    newer = tmp_path / "newer.jsonl"
    older = tmp_path / "older.jsonl"
    _write_segment(older, session_id="root", total=100, ordinal=6)
    _write_segment(newer, session_id="root", total=99, ordinal=9)
    with pytest.raises(
        native_session_meter.NativeSessionMeterError,
        match="moved backwards",
    ):
        native_session_meter.aggregate([
            native_session_meter.read_snapshot(str(older)),
            native_session_meter.read_snapshot(str(newer)),
        ])
