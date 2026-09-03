"""Test host adapter for opening a prepared delivery root.

Core loop and transport tests are not root-admission tests, but current
delivery dispatch requires the same fresh, metered host evidence as
production.  This helper supplies that precondition through public APIs;
the refusal and tamper cases remain owned by test_native_root_session.py.
"""
from __future__ import annotations

import hashlib
import json
import os


def open_delivery_root(workspace: str) -> bytes:
    import host_capabilities
    import host_native
    import loop
    import native_session_meter
    import root_seed
    import settings
    import tp

    state = loop.load(workspace) or {}
    current = state.get("root_hygiene")
    authority = tp._transcript_projection_authority(workspace)
    if isinstance(current, dict) and current.get("status") == "open":
        return authority
    if not isinstance(current, dict):
        tasks = state.get("tasks") or []
        task_rows = [row for row in tasks if isinstance(row, dict)]
        task_ids = [str(row.get("id") or "") for row in task_rows]
        plan_fingerprint = hashlib.sha256(json.dumps(
            task_ids, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        loop.prepare_delivery_root(
            workspace, seed_ref=".taskplane/test-root/root-seed.json",
            wave_id="test-wave", prepared_at="2026-01-01T00:00:00Z",
            operation_id="test-root-prepare",
            design={"path": "design/contract.json",
                    "fingerprint": str(state.get("design_fingerprint") or
                                       "d" * 64)},
            plan={"path": "plan/tasks.json", "fingerprint": plan_fingerprint},
            pickups=[{
                "id": task_id,
                "write_scopes": list(task_rows[index].get("scope") or
                                     ["test-fixture/**"]),
                "disjointness_receipt_fingerprint": hashlib.sha256(
                    task_id.encode()).hexdigest(),
            } for index, task_id in enumerate(task_ids)],
            outstanding_human_gates=[],
            predecessor_terminal_projection={"status": "none"})
        state = loop.load(workspace) or {}
        current = state.get("root_hygiene")
    if not isinstance(current, dict) or current.get("status") != "prepared":
        raise AssertionError("test journey has no prepared delivery root")

    transcript = os.path.join(
        os.path.dirname(loop._loop_path(workspace)), "test-root.jsonl")
    rows = [
        {"type": "session_meta", "payload": {
            "session_id": "test-fresh-root", "id": "test-fresh-root",
            "timestamp": "2026-01-01T00:00:00Z",
            "thread_source": "agent_created_thread"}},
        {"ordinal": 1, "type": "event_msg", "payload": {
            "type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 99, "cached_input_tokens": 0,
                "cache_write_input_tokens": 0, "output_tokens": 1,
                "reasoning_output_tokens": 0, "total_tokens": 100}}}},
    ]
    with open(transcript, "w", encoding="utf-8") as stream:
        stream.write("\n".join(json.dumps(row) for row in rows) + "\n")
    native = native_session_meter.read_snapshot(transcript)
    effective = settings.load_settings()
    observations = {
        name: host_capabilities.Observation(
            status="supported", source="test-host", confidence="high",
            observed_at="2026-01-01T00:00:00Z")
        for name in (
            "native_plugin_hooks_loaded", "managed_policy_permission",
            "root_fresh_start", "root_cumulative_meter", "root_turn_mapping")
    }
    host = host_capabilities.probe_snapshot(
        workspace, host="codex", install_context="personal",
        native_installed=True, bridge_configured=False,
        observations=observations, session_id="test-fresh-root",
        now="2026-01-01T00:00:00Z")
    capability = host_capabilities.root_session_capability(
        host, settings_digest=effective.digest, native_snapshot=native,
        turn_id="test-root-turn")
    seed = root_seed.load_root_seed(workspace, str(current["seed_ref"]))
    start = host_native.start_root_session(
        capability, seed, run_id=str(seed["run_id"]),
        wave_id=str(seed["wave_id"]), candidate_sha=str(seed["candidate_sha"]),
        settings_digest=effective.digest,
        session_pseudonym=hashlib.sha256(authority).hexdigest(),
        started_at="2026-01-01T00:00:00Z", issuer_sequence=1,
        authority=authority)
    observation = native_session_meter.seal_root_observation(
        native, sequence=1, session_role="root",
        status_receipt_fingerprint=start["fingerprint"], authority=authority)
    loop.open_delivery_wave(
        workspace, host_start_receipt=start, first_observation=observation,
        observation_authority=authority)
    return authority
