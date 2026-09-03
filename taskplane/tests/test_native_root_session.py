from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from taskplane import host_capabilities, host_native, loop, native_session_meter
from taskplane.settings import load_settings
from taskplane import tp as tp_cli


AUTHORITY = b"private-host-root-authority"


def _write_root(path: Path, *, total: int, sequence: int,
                resumed: bool = False) -> dict:
    metadata = {
        "session_id": "root-session", "id": "root-session",
        "timestamp": "2026-09-02T04:00:00Z",
        "thread_source": "agent_created_thread",
    }
    if resumed:
        metadata["history_base"] = {
            "thread_id": "prior", "end_ordinal_exclusive": 1,
            "end_byte_offset": 1,
        }
    rows = [
        {"type": "session_meta", "payload": metadata},
        {"ordinal": sequence, "type": "event_msg", "payload": {
            "type": "token_count", "info": {"total_token_usage": {
                "input_tokens": total - 1, "cached_input_tokens": 0,
                "cache_write_input_tokens": 0, "output_tokens": 1,
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
    return host_capabilities.root_session_capability(
        snapshot, settings_digest=digest)


def _prepared(tmp_path: Path) -> tuple[dict, dict, dict]:
    settings = load_settings()
    state = {
        "run_id": "run-root-public", "baseline": "a" * 40,
        "design_fingerprint": "b" * 64, "plan_fingerprint": "c" * 64,
        "settings_digest": settings.digest, "step": "execute",
        "tasks": [{"id": "P13", "status": "pending"}],
        "current_task": 0, "goal": "root public journey",
    }
    loop.save(str(tmp_path), state)
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


def test_wave_open_requires_prepared_seed_fresh_capable_host_and_first_observation_before_dispatch(
        tmp_path: Path) -> None:
    _, prepared, _ = _prepared(tmp_path)
    severed = loop.load(str(tmp_path))
    severed["parallel"] = True
    severed["tasks"][0].update({
        "scope": ["taskplane/loop.py"], "deps": [],
        "tests": "true",
        "contracts": ["contract:host.root-session-start/v1"],
    })
    loop.save(str(tmp_path), severed)
    refused = loop.wave(str(tmp_path))
    assert refused["wave"] == []
    assert "native root admission refused before wave" in refused["error"]

    settings = load_settings()
    seed = json.loads((tmp_path / prepared["seed_ref"]).read_text(
        encoding="utf-8"))
    capability = _capability(tmp_path, settings.digest)
    start = host_native.start_root_session(
        capability, seed, run_id="run-root-public", wave_id="W1",
        candidate_sha="a" * 40, settings_digest=settings.digest,
        session_pseudonym="f" * 64, started_at="2026-09-02T04:00:01Z",
        issuer_sequence=1, authority=AUTHORITY)
    observation = native_session_meter.seal_root_observation(
        _write_root(tmp_path / "root.jsonl", total=40_000, sequence=1),
        sequence=1, session_role="root",
        status_receipt_fingerprint=start["fingerprint"], authority=AUTHORITY)

    opened = loop.open_delivery_wave(
        str(tmp_path), host_start_receipt=start,
        first_observation=observation, observation_authority=AUTHORITY)
    assert opened["status"] == "open"
    assert opened["meter"]["first_observed_input_tokens"] == 39_999

    admitted = loop.admit_native_dispatch(
        str(tmp_path), observation_authority=AUTHORITY,
        dispatch={"dispatch_id": "dispatch-P13", "thread_id": "worker-P13",
                  "thread_type": "worker", "task_id": "P13",
                  "dependencies": [], "shared_owner": None,
                  "started_at": 1, "ended_at": 1,
                  "wait_duration_seconds": 0, "correction_count": 0,
                  "events": []},
        current_stage="execute", outstanding_set_fingerprint="1" * 64,
        preserved_context_fingerprint="2" * 64)
    assert admitted["operation_status"] == "admitted"
    assert admitted["root_usage"]["total_tokens"] == 40_000
    assert loop.load(str(tmp_path))["root_hygiene"]["meter"][
        "first_observed_input_tokens"] == 39_999


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
    loop.save(str(tmp_path), state)
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


def test_bootstrap_seed_precedes_implementation_root_and_is_not_claimed_as_runtime_enforcement(
        tmp_path: Path) -> None:
    _, prepared, _ = _prepared(tmp_path)
    state = loop.load(str(tmp_path))
    state["parallel"] = True
    state["tasks"][0].update({
        "scope": ["taskplane/loop.py"], "deps": [],
        "tests": "python3 -m pytest -q taskplane/tests/test_native_root_session.py",
    })
    state["bootstrap_root_evidence"] = dict(state.pop("root_hygiene"))
    assert state["bootstrap_root_evidence"]["prepare_receipt"] == prepared
    loop.save(str(tmp_path), state)

    refused = loop.wave(str(tmp_path), root_observation_authority=AUTHORITY)
    assert refused["wave"] == []
    assert "prepared and opened fresh root evidence" in refused["error"]


def test_public_command_passes_existing_private_authority_and_refuses_corruption(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    import loop as cli_loop

    _, prepared, _ = _prepared(tmp_path)
    state = loop.load(str(tmp_path))
    state["parallel"] = True
    state["tasks"][0].update({
        "scope": ["taskplane/loop.py"], "deps": [],
        "tests": "true",
        "contracts": [],
    })
    loop.save(str(tmp_path), state)
    monkeypatch.setattr(tp_cli, "_workspace", lambda _value: str(tmp_path))
    monkeypatch.setattr(
        tp_cli, "_enforcement_check",
        lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(cli_loop, "record_enforcement", lambda *_args: None)
    monkeypatch.setattr(cli_loop, "_validated_delivery_mode", lambda _state: None)
    monkeypatch.setattr(
        cli_loop, "build_dispatch_lens_routing",
        lambda *_args, **_kwargs: ({"lenses": [], "context": {}}, None))
    authority = tp_cli._transcript_projection_authority(str(tmp_path))
    settings = load_settings()
    seed = json.loads((tmp_path / prepared["seed_ref"]).read_text(
        encoding="utf-8"))
    start = host_native.start_root_session(
        _capability(tmp_path, settings.digest), seed,
        run_id="run-root-public", wave_id="W1", candidate_sha="a" * 40,
        settings_digest=settings.digest, session_pseudonym="f" * 64,
        started_at="2026-09-02T04:00:01Z", issuer_sequence=1,
        authority=authority)
    observation = native_session_meter.seal_root_observation(
        _write_root(tmp_path / "root.jsonl", total=40_000, sequence=1),
        sequence=1, session_role="root",
        status_receipt_fingerprint=start["fingerprint"], authority=authority)
    loop.open_delivery_wave(
        str(tmp_path), host_start_receipt=start,
        first_observation=observation, observation_authority=authority)
    args = SimpleNamespace(
        workspace=str(tmp_path), loop_action="wave", req=None,
        advisory=False, by=None)
    assert tp_cli.cmd_loop(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["task"]["id"] for row in payload["wave"]] == ["P13"], payload
    assert payload["root_admission"]["dispatch_allowed"] is True

    authority_path = tmp_path / ".taskplane" / "transcript-usage" / \
        "authority-v1.json"
    authority_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(Exception, match="authority is invalid"):
        tp_cli.cmd_loop(args)
