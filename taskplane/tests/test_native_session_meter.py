from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskplane import host_capabilities
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


def test_resume_sums_reset_physical_segment_and_fork_counters_once(
    tmp_path: Path,
) -> None:
    first = tmp_path / "root-1.jsonl"
    resumed = tmp_path / "root-2.jsonl"
    child = tmp_path / "child.jsonl"
    _write_segment(first, session_id="root", total=90, ordinal=7)
    _write_segment(
        resumed, session_id="root", total=25, resumed=True, ordinal=11,
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
    assert wave["usage"]["total_tokens"] == 155
    assert {row["session_id"]: row["segments"] for row in wave["sessions"]} \
        == {"child": 1, "root": 2}
    assert {row["session_id"]: row["total_tokens"]
            for row in wave["sessions"]} == {"child": 40, "root": 115}


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


def test_root_meter_counts_complete_turn_events_and_derives_first_peak_rent_without_payload_content(
        tmp_path: Path) -> None:
    source = tmp_path / "root.jsonl"
    authority = b"host-observation-authority"
    capability_snapshot = host_capabilities.probe_snapshot(
        str(tmp_path), host="codex", install_context="personal",
        native_installed=True, bridge_configured=False,
        observations={
            name: host_capabilities.Observation(
                status="supported", source="host-runtime",
                confidence="high", observed_at="2026-09-02T00:00:00Z")
            for name in (
                "native_plugin_hooks_loaded", "managed_policy_permission",
                "root_fresh_start", "root_cumulative_meter",
                "root_turn_mapping",
            )
        },
        session_id="root-session", now="2026-09-02T00:00:00Z")
    capability = host_capabilities.root_session_capability(
        capability_snapshot, settings_digest="a" * 64)
    assert capability["status"] == "supported"
    observations = []
    for sequence, (total, cached, output) in enumerate(
            ((12, 4, 2), (30, 12, 4), (51, 24, 6)), start=1):
        _write_segment(
            source, session_id="root", total=total, cached=cached,
            output=output, ordinal=sequence * 3)
        observations.append(native_session_meter.seal_root_observation(
            native_session_meter.read_snapshot(str(source)),
            sequence=sequence, session_role="root",
            status_receipt_fingerprint=capability["fingerprint"],
            terminal_reason="complete" if sequence == 3 else None,
            authority=authority))

    meter = native_session_meter.fold_root_observations(
        observations, authority=authority)

    assert meter["status"] == "available"
    assert meter["session_role"] == "root"
    assert meter["turns"] == 3
    assert meter["first_observed_input_tokens"] == 10
    assert meter["peak_context_tokens"] == 19
    assert meter["usage"]["total_tokens"] == 51
    assert meter["context_rent_tokens"] == 8.0
    assert meter["resumed"] is False
    assert meter["terminal_reason"] == "complete"
    assert meter["watermark"]["last_sequence"] == 3
    assert "history_base" not in json.dumps(meter)
    assert "private conversation" not in json.dumps(meter)

    replay = native_session_meter.fold_root_observations(
        [observations[-1]], authority=authority,
        prior=meter["watermark"])
    assert replay == meter


def test_root_meter_refuses_nonmonotonic_ambiguous_turn_or_unreconciled_counter_evidence(
        tmp_path: Path) -> None:
    authority = b"host-observation-authority"

    def observation(path: Path, *, sequence: int, total: int,
                    cached: int = 0, output: int = 1) -> dict:
        _write_segment(
            path, session_id="root", total=total, cached=cached,
            output=output, ordinal=sequence * 3)
        return native_session_meter.seal_root_observation(
            native_session_meter.read_snapshot(str(path)),
            sequence=sequence, session_role="root",
            status_receipt_fingerprint="b" * 64,
            authority=authority)

    source = tmp_path / "root.jsonl"
    first = observation(source, sequence=1, total=10)
    assert native_session_meter.fold_root_observations(
        [], authority=authority)["reason_code"] == "observation_truncated"
    backwards = observation(source, sequence=2, total=9)
    assert native_session_meter.fold_root_observations(
        [first, backwards], authority=authority)["reason_code"] == \
        "counter_backwards"

    gap = observation(source, sequence=3, total=20)
    assert native_session_meter.fold_root_observations(
        [first, gap], authority=authority)["reason_code"] == \
        "observation_gap"

    ambiguous_a = observation(source, sequence=2, total=20)
    ambiguous_b = observation(source, sequence=2, total=21)
    assert native_session_meter.fold_root_observations(
        [first, ambiguous_a, ambiguous_b], authority=authority
    )["reason_code"] == "observation_ambiguous"

    replacement = observation(
        tmp_path / "replacement.jsonl", sequence=2, total=20)
    assert native_session_meter.fold_root_observations(
        [first, replacement], authority=authority
    )["reason_code"] == "source_replaced"

    tampered = dict(first)
    tampered["sequence"] = 2
    assert native_session_meter.fold_root_observations(
        [tampered], authority=authority)["reason_code"] == \
        "authentication_failed"

    assert native_session_meter.fold_root_observations(
        [first, ambiguous_a], authority=authority, max_observations=1
    )["reason_code"] == "observation_overflow"
