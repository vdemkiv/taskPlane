from __future__ import annotations
from taskplane.tests.phase_fixture import save_component_workflow

import io
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from taskplane import (
    dispatch_telemetry, host_capabilities, host_native, loop, native_session_meter, root_seed,
)
from taskplane.settings import load_settings
from taskplane import tp as tp_cli


AUTHORITY = b"private-host-root-authority"


def _write_root(path: Path, *, total: int, sequence: int,
                resumed: bool = False,
                session_id: str = "root-session") -> dict:
    metadata = {
        "session_id": session_id, "id": session_id,
        "timestamp": "2026-09-02T04:00:00Z",
        "thread_source": "agent_created_thread",
    }
    if resumed:
        metadata["history_base"] = {
            "thread_id": session_id, "end_ordinal_exclusive": 1,
            "end_byte_offset": 1,
        }
    rows = [
        {"type": "session_meta", "payload": metadata},
        {"ordinal": sequence, "type": "event_msg", "payload": {
            "type": "token_count", "info": {"total_token_usage": {
                "input_tokens": max(0, total - 1), "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 1 if total else 0,
                "reasoning_output_tokens": 0, "total_tokens": total,
            }},
        }},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                    encoding="utf-8")
    return native_session_meter.read_snapshot(str(path))


def _capability(tmp_path: Path, digest: str) -> dict:
    observations = {
        name: host_capabilities.Observation(
            status="supported", source="host-runtime", confidence="high",
            observed_at="2026-09-02T04:00:00Z")
        for name in (
            "native_plugin_hooks_loaded", "managed_policy_permission",
            "root_fresh_start", "root_cumulative_meter", "root_turn_mapping",
        )
    }
    snapshot = host_capabilities.probe_snapshot(
        str(tmp_path), host="codex", install_context="personal",
        native_installed=True, bridge_configured=False,
        observations=observations, session_id="root-session",
        now="2026-09-02T04:00:00Z")
    native = _write_root(
        tmp_path / "root-capability.jsonl", total=1, sequence=1)
    return host_capabilities.root_session_capability(
        snapshot, settings_digest=digest, native_snapshot=native,
        turn_id="turn-capability")


def _prepared(tmp_path: Path) -> tuple[dict, dict, dict]:
    settings = load_settings()
    state = {
        "run_id": "run-root-public", "baseline": "a" * 40,
        "design_fingerprint": "b" * 64, "plan_fingerprint": "c" * 64,
        "settings_digest": settings.digest, "step": "execute",
        "tasks": [{"id": "P13", "status": "pending"}],
        "current_task": 0, "goal": "root public journey",
    }
    save_component_workflow(str(tmp_path), state)
    prepared = loop.prepare_delivery_root(
        str(tmp_path), seed_ref="waves/W1/root-seed.json", wave_id="W1",
        prepared_at="2026-09-02T04:00:00Z",
        operation_id="prepare-run-root-public-W1",
        design={"path": "design/contract.json", "fingerprint": "b" * 64},
        plan={"path": "plan/tasks.json", "fingerprint": "c" * 64},
        pickups=[{"id": "P13", "write_scopes": ["taskplane/loop.py"],
                  "disjointness_receipt_fingerprint": "d" * 64}],
        outstanding_human_gates=[],
        predecessor_terminal_projection={
            "path": "runs/prior/terminal.json", "fingerprint": "e" * 64},
    )
    return state, prepared, settings.workflow.root_session.consumer_projection(
        "root-seed.prepare")


def _next_seed(tmp_path: Path) -> dict:
    state = loop.load(str(tmp_path))
    receipt, prepared, _ = loop._build_delivery_root_preparation(
        str(tmp_path), state, seed_ref=".taskplane/root-seeds/second.json", wave_id="W1",
        prepared_at="2026-09-02T04:05:00Z", operation_id="prepare-corrected-plan",
        design={"path": "design/contract.json", "fingerprint": "b" * 64},
        plan={"path": "plan/tasks.json", "fingerprint": "d" * 64},
        pickups=[{"id": "P13", "write_scopes": ["taskplane/loop.py"],
                  "disjointness_receipt_fingerprint": "d" * 64}],
        outstanding_human_gates=[], predecessor_terminal_projection={"status": "none"})
    state["root_hygiene"] = prepared
    save_component_workflow(str(tmp_path), state)
    return root_seed.load_root_seed(str(tmp_path), receipt["seed_ref"])


def _start(tmp_path: Path, seed: dict) -> dict:
    settings = load_settings()
    return host_native.start_root_session(
        _capability(tmp_path, settings.digest), seed, run_id="run-root-public", wave_id="W1",
        candidate_sha="a" * 40, settings_digest=settings.digest,
        session_pseudonym="f" * 64, started_at="2026-09-02T04:05:01Z",
        issuer_sequence=1, authority=AUTHORITY)


@pytest.mark.parametrize("case", ["valid", "foreign", "stale", "rollback"])
def test_new_seed_opens_without_resetting_native_counter(tmp_path: Path, case: str) -> None:
    _, prepared, _ = _prepared(tmp_path)
    original_bytes = (tmp_path / prepared["seed_ref"]).read_bytes()
    seed = root_seed.load_root_seed(str(tmp_path), prepared["seed_ref"])
    start = _start(tmp_path, seed)
    transcript = tmp_path / "root.jsonl"
    first = native_session_meter.seal_root_observation(
        _write_root(transcript, total=12, sequence=1), sequence=1, session_role="root",
        status_receipt_fingerprint=start["fingerprint"], authority=AUTHORITY)
    loop.open_delivery_wave(str(tmp_path), host_start_receipt=start,
        first_observation=first, observation_authority=AUTHORITY)
    advance = native_session_meter.seal_root_observation(
        _write_root(transcript, total=25, sequence=2), sequence=2, session_role="root",
        status_receipt_fingerprint=start["fingerprint"], authority=AUTHORITY)
    loop.record_delivery_root_observation(str(tmp_path), observation=advance, observation_authority=AUTHORITY)
    prior = copy.deepcopy(loop.load(str(tmp_path))["dispatch_telemetry"])
    next_seed = _next_seed(tmp_path)
    next_start = _start(tmp_path, next_seed)
    assert next_seed["seed_fingerprint"] != seed["seed_fingerprint"]
    next_observation = native_session_meter.seal_root_observation(
        _write_root(transcript, total=20 if case == "rollback" else 50, sequence=3,
                    session_id="foreign-session" if case == "foreign" else "root-session"),
        sequence=2 if case == "stale" else 3, session_role="root",
        status_receipt_fingerprint=next_start["fingerprint"], authority=AUTHORITY)
    before = copy.deepcopy(loop.load(str(tmp_path)))
    if case != "valid":
        with pytest.raises(ValueError, match="root generation observation refused"):
            loop.open_delivery_wave(str(tmp_path), host_start_receipt=next_start,
                first_observation=next_observation, observation_authority=AUTHORITY)
        assert loop.load(str(tmp_path)) == before
        return
    # Ordinary same-generation folding and recording still reject the new binding.
    refused = native_session_meter.fold_root_observations([next_observation], authority=AUTHORITY,
        prior=prior["root_admission"]["meter"]["watermark"])
    assert refused["status"] == "unavailable"
    opened = loop.open_delivery_wave(str(tmp_path), host_start_receipt=next_start,
        first_observation=next_observation, observation_authority=AUTHORITY)
    meter = opened["meter"]
    assert meter["turns"] == 3
    assert meter["watermark"]["last_sequence"] == 3
    assert meter["first_observed_input_tokens"] == prior["root_admission"]["meter"]["first_observed_input_tokens"]
    assert meter["peak_context_tokens"] >= prior["root_admission"]["meter"]["peak_context_tokens"]
    assert meter["usage"]["total_tokens"] == 50
    assert meter["status_receipt_fingerprint"] == next_start["fingerprint"]
    with pytest.raises(ValueError, match="source or observation authority was replaced"):
        dispatch_telemetry.record_root_meter(copy.deepcopy(prior), meter,
            observation_authority=AUTHORITY)
    after = loop.load(str(tmp_path))
    ledger = after["dispatch_telemetry"]
    assert ledger["root_admission_history"] == [prior["root_admission"]]
    assert ledger["root_openings"] == prior["root_openings"] + [{
        "seed_ref": ".taskplane/root-seeds/second.json", "host_start_receipt": next_start,
        "first_observation": next_observation}]
    assert (tmp_path / prepared["seed_ref"]).read_bytes() == original_bytes
    assert loop.open_delivery_wave(str(tmp_path), host_start_receipt=next_start,
        first_observation=next_observation, observation_authority=AUTHORITY) == opened
    assert loop.load(str(tmp_path)) == after
    with pytest.raises(ValueError, match="stale|different evidence"):
        loop.open_delivery_wave(str(tmp_path), host_start_receipt=start,
            first_observation=first, observation_authority=AUTHORITY)


def test_actual_root_hook_opens_next_seed_with_continuous_sequence(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    _prepared(tmp_path)
    monkeypatch.setattr(tp_cli, "_workspace", lambda _value: str(tmp_path))
    monkeypatch.setenv("CODEX_THREAD_ID", "root-session")
    monkeypatch.setenv("TASKPLANE_NATIVE_HOOKS_LOADED", "supported")
    monkeypatch.setenv("TASKPLANE_MANAGED_HOOK_POLICY", "supported")
    transcript = tmp_path / "root-generation-hook.jsonl"
    event = {"cwd": str(tmp_path), "session_id": "root-session",
             "turn_id": "turn-root", "transcript_path": str(transcript),
             "tool_name": "Read", "tool_input": {}}
    def screen(total: int, sequence: int) -> dict:
        _write_root(transcript, total=total, sequence=sequence)
        monkeypatch.setattr(tp_cli.sys, "stdin", io.StringIO(json.dumps(event)))
        assert tp_cli.cmd_screen(None) == 0
        capsys.readouterr()
        return loop.load(str(tmp_path))
    before = screen(120, 1)
    seed = _next_seed(tmp_path)
    after = screen(150, 2)
    assert after["root_hygiene"]["status"] == "open"
    assert after["root_hygiene"]["seed_fingerprint"] == seed["seed_fingerprint"]
    assert after["dispatch_telemetry"]["root_admission_history"] == [before["dispatch_telemetry"]["root_admission"]]
    meter = after["root_hygiene"]["meter"]
    assert meter["turns"] == 2
    assert meter["watermark"]["last_sequence"] == 2
    assert meter["first_observed_input_tokens"] == 119
    assert meter["usage"]["total_tokens"] == 150
    assert screen(175, 3)["root_hygiene"]["meter"]["turns"] == 3




def test_resumed_unknown_over_seed_or_binding_mismatch_refuses_and_override_is_attributed_nonconformance(
        tmp_path: Path) -> None:
    _, prepared, _ = _prepared(tmp_path)
    settings = load_settings()
    seed = json.loads((tmp_path / prepared["seed_ref"]).read_text(
        encoding="utf-8"))
    capability = _capability(tmp_path, settings.digest)
    start = host_native.start_root_session(
        capability, seed, run_id="run-root-public", wave_id="W1",
        candidate_sha="a" * 40, settings_digest=settings.digest,
        session_pseudonym="f" * 64, started_at="2026-09-02T04:00:01Z",
        issuer_sequence=1, authority=AUTHORITY)
    resumed = native_session_meter.seal_root_observation(
        _write_root(tmp_path / "root.jsonl", total=12, sequence=1,
                    resumed=True),
        sequence=1, session_role="root",
        status_receipt_fingerprint=start["fingerprint"], authority=AUTHORITY)
    prepared_state = loop.load(str(tmp_path))["root_hygiene"]
    with pytest.raises(ValueError, match="resumed"):
        loop.open_delivery_wave(
            str(tmp_path), host_start_receipt=start,
            first_observation=resumed, observation_authority=AUTHORITY)

    overridden = loop.open_delivery_wave(
        str(tmp_path), host_start_receipt=start,
        first_observation=resumed, observation_authority=AUTHORITY,
        override={"by": "human:operator", "reason": "diagnostic only"})
    assert overridden["conformance"] == "overridden"
    assert overridden["canary_eligible"] is False
    assert overridden["override"]["by"] == "human:operator"

    state = loop.load(str(tmp_path))
    state["root_hygiene"] = prepared_state
    save_component_workflow(str(tmp_path), state)
    foreign = dict(start, wave_id="W2")
    with pytest.raises(ValueError, match="unauthentic|binding"):
        loop.open_delivery_wave(
            str(tmp_path), host_start_receipt=foreign,
            first_observation=resumed, observation_authority=AUTHORITY)


def test_codex_history_base_is_a_resume_marker_not_a_retained_or_sized_payload(
        tmp_path: Path) -> None:
    snapshot = _write_root(
        tmp_path / "root.jsonl", total=12, sequence=1, resumed=True)
    assert snapshot["resumed"] is True
    assert "history_base" not in json.dumps(snapshot)






def test_repeated_pretooluse_events_for_one_native_counter_count_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    _prepared(tmp_path)
    monkeypatch.setattr(tp_cli, "_workspace", lambda _value: str(tmp_path))
    monkeypatch.setenv("CODEX_THREAD_ID", "root-session")
    monkeypatch.setenv("TASKPLANE_NATIVE_HOOKS_LOADED", "supported")
    monkeypatch.setenv("TASKPLANE_MANAGED_HOOK_POLICY", "supported")
    transcript = tmp_path / "root-replay.jsonl"
    _write_root(transcript, total=40_000, sequence=1)
    event = {"cwd": str(tmp_path), "session_id": "root-session",
             "turn_id": "turn-root", "transcript_path": str(transcript),
             "tool_name": "Read", "tool_input": {}}
    for _ in range(2):
        monkeypatch.setattr(tp_cli.sys, "stdin", io.StringIO(json.dumps(event)))
        assert tp_cli.cmd_screen(None) == 0
        capsys.readouterr()
    meter = loop.load(str(tmp_path))["root_hygiene"]["meter"]
    assert meter["turns"] == 1
    assert meter["usage"]["total_tokens"] == 40_000


@pytest.mark.parametrize("case", [
    "missing", "zero", "malformed", "foreign", "resumed",
])
def test_missing_zero_malformed_foreign_or_resumed_native_evidence_refuses_before_dispatch(
        case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    _prepared(tmp_path)
    monkeypatch.setattr(tp_cli, "_workspace", lambda _value: str(tmp_path))
    monkeypatch.setenv("CODEX_THREAD_ID", "root-session")
    monkeypatch.setenv("TASKPLANE_NATIVE_HOOKS_LOADED", "supported")
    monkeypatch.setenv("TASKPLANE_MANAGED_HOOK_POLICY", "supported")
    transcript = tmp_path / f"root-{case}.jsonl"
    if case == "missing":
        transcript.write_text(json.dumps({"type": "session_meta", "payload": {
            "session_id": "root-session", "id": "root-session",
            "timestamp": "2026-09-02T04:00:00Z",
            "thread_source": "agent_created_thread"}}) + "\n")
    elif case == "malformed":
        transcript.write_text("not-json\n", encoding="utf-8")
    else:
        _write_root(transcript, total=0 if case == "zero" else 40_000,
                    sequence=1, resumed=case == "resumed",
                    session_id="foreign-session" if case == "foreign"
                    else "root-session")
    event = {"cwd": str(tmp_path), "session_id": "root-session",
             "turn_id": "turn-root", "transcript_path": str(transcript),
             "tool_name": "Read", "tool_input": {}}
    monkeypatch.setattr(tp_cli.sys, "stdin", io.StringIO(json.dumps(event)))
    assert tp_cli.cmd_screen(None) == 0
    capsys.readouterr()
    root = loop.load(str(tmp_path))["root_hygiene"]
    assert root["status"] == "prepared"
    assert "meter" not in root
