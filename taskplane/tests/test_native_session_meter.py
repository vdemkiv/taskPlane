from __future__ import annotations

import json
from pathlib import Path

import pytest
from datetime import datetime

from taskplane import native_session_meter


def test_interval_reset_and_truncated_history_never_become_measured_zero(tmp_path, monkeypatch):
    from taskplane.tests.test_harness_review_regressions import native
    path = tmp_path/'reset.jsonl'
    native(path,'child',[(5,100),(15,30)],'root')
    start=datetime.fromisoformat('2026-09-01T00:00:10+00:00').timestamp()
    result=native_session_meter.read_owned_interval([path],'child',start=start)
    assert result['usage'] is None and result['status']=='partial'
    assert 'counter_reset' in result['errors']
    monkeypatch.setattr(native_session_meter,'MAX_REPLAY_BYTES',100)
    result=native_session_meter.read_owned_interval([path],'child',start=start)
    assert result['status']=='partial' and result['usage'] is None


def test_owned_usage_categories_and_foreign_response_exclusion(tmp_path):
    from taskplane.tests.test_harness_review_regressions import native
    path=tmp_path/'owned.jsonl'
    rows=native(path,'child',[(5,100),(15,150)],'root',owned=True)
    rows[-1]['payload']['usage'].update(cached_input_tokens=20,output_tokens=10,input_tokens=40,reasoning_output_tokens=5)
    rows[-1]['payload']['thread_token_usage'].update(cached_input_tokens=20,output_tokens=10,input_tokens=140,reasoning_output_tokens=5)
    # Foreign copied history may coexist but is never charged to this worker.
    foreign=json.loads(json.dumps(rows[-1]));foreign['payload']['thread_id']='parent'
    rows.append(foreign)
    path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    start=datetime.fromisoformat('2026-09-01T00:00:10+00:00').timestamp()
    result=native_session_meter.read_owned_interval([path],'child',start=start)
    assert result['status']=='measured'
    assert result['usage']==dict(input_tokens=40,cached_input_tokens=20,uncached_input_tokens=20,
                                output_tokens=10,reasoning_tokens=5,total_tokens=50)


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


def test_resume_sums_reset_physical_segment_and_fork_counters_once(
    tmp_path: Path,
) -> None:
    first = tmp_path / "root-1.jsonl"
    resumed = tmp_path / "root-2.jsonl"
    child = tmp_path / "child.jsonl"
    _write_segment(first, session_id="root", total=90, ordinal=7)
    _write_segment(
        resumed, session_id="root", total=90, resumed=True, ordinal=11,
    )
    _write_segment(
        child, session_id="child", parent="root", total=40, ordinal=5,
    )

    wave = native_session_meter.aggregate([
        native_session_meter.read_snapshot(str(first)),
        native_session_meter.read_snapshot(str(resumed)),
        native_session_meter.read_snapshot(str(child)),
        native_session_meter.read_snapshot(str(resumed)),
    ])

    assert wave["physical_segments"] == 3
    assert wave["logical_sessions"] == 2
    assert wave["usage"]["total_tokens"] == 220
    assert {row["session_id"]: row["segments"] for row in wave["sessions"]} \
        == {"child": 1, "root": 2}
    assert {row["session_id"]: row["total_tokens"]
            for row in wave["sessions"]} == {"child": 40, "root": 180}
    _write_segment(resumed, session_id="root", total=90, ordinal=11)
    with pytest.raises(native_session_meter.NativeSessionMeterError, match="restart evidence"):
        native_session_meter.aggregate([native_session_meter.read_snapshot(str(first)),
                                        native_session_meter.read_snapshot(str(resumed))])


def test_missing_counter_and_backwards_same_segment_refuse_instead_of_zero(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.jsonl"
    _write_segment(missing, session_id="missing", total=None)
    with pytest.raises(
        native_session_meter.NativeSessionMeterError,
        match="no complete token counter",
    ):
        native_session_meter.read_snapshot(str(missing))

    source = tmp_path / "same-segment.jsonl"
    _write_segment(source, session_id="root", total=100, ordinal=6)
    older = native_session_meter.read_snapshot(str(source))
    _write_segment(source, session_id="root", total=99, ordinal=9)
    newer = native_session_meter.read_snapshot(str(source))
    with pytest.raises(
        native_session_meter.NativeSessionMeterError,
        match="physical-segment counter moved backwards",
    ):
        native_session_meter.aggregate([older, newer])
def test_verified_fork_history_requires_own_thread_counter(tmp_path):
    p = tmp_path/'fork.jsonl'
    _write_segment(p, session_id='child', total=1000, parent='parent')
    rows = [json.loads(line) for line in p.read_text().splitlines()]
    rows[0]['payload']['history_base'] = {'thread_id':'parent','end_ordinal_exclusive':99,'end_byte_offset':100}
    p.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    with pytest.raises(ValueError, match='current-thread counter'):
        native_session_meter.read_snapshot(p)
    own = {'input_tokens':7,'cached_input_tokens':2,'output_tokens':3,'reasoning_output_tokens':1,'total_tokens':10}
    rows.append({'type':'token_usage_record','timestamp':'2026-09-01T00:00:10Z','ordinal':10,
                 'payload':{'thread_id':'child','thread_token_usage':own}})
    p.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    assert native_session_meter.read_snapshot(p)['usage']['total_tokens'] == 10
    rows[0]['payload']['history_base']['thread_id'] = 'foreign'
    p.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    with pytest.raises(ValueError, match='identity'):
        native_session_meter.read_snapshot(p)
