"""Phase admission wiring with explicit synthetic host-provider fixtures."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from taskplane import loop, phase_admission, phase_dispatch, phase_handoff, phase_pickup, settings
from taskplane import taskplane_lite as kernel, tp as cli
from taskplane.tests.test_native_root_session import AUTHORITY, _capability, _write_root
from taskplane.tests.test_stateless_phase_pickup import _published_checkout


def _forbidden(*_args, **_kwargs):
    raise AssertionError("phase admission must not read predecessor loop state")


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    checkout, handoff = _published_checkout(tmp_path, "build")
    startup = phase_pickup.prepare_build_pickup(str(checkout), handoff)
    brief = phase_dispatch._hydrated_brief(str(checkout), handoff, startup)
    wait = loop.event_wait_policy("fixture-phase-build", 1)
    monkeypatch.setattr(loop, "load", _forbidden)
    monkeypatch.setattr(loop, "mutate", _forbidden)
    preparation = phase_admission.prepare(str(checkout), handoff, startup, brief, wait_policy=wait)
    contract = phase_dispatch._native_contract(str(checkout), brief, handoff)
    contract.update(task_id=brief["task_slot"], phase_dispatch=brief, phase_startup=startup,
                    phase_handoff_fingerprint=handoff["fingerprint"],
                    phase_admission_reference=preparation["reference"],
                    phase_dispatch_intent=preparation["intent"])
    contract = kernel.prepare_worker_contract(
        str(checkout), contract, stage="phase-build", task=handoff["handoff_id"],
        task_name=brief["task_name"], role_marker=brief["role_marker"])
    contract["worker_lifecycle"].update(
        dispatch_intent_id=preparation["intent"]["intent_id"],
        dispatch_intent_run_id=preparation["intent"]["identity"]["run_id"])
    kernel.activate(str(checkout), contract, task_slot_override=brief["task_slot"])
    return checkout, handoff, startup, brief, preparation, contract


def _admit(prepared, authority=AUTHORITY):
    checkout, handoff, startup, brief, preparation, _contract = prepared
    return phase_admission.admit(str(checkout), handoff, startup, brief,
                                 reference=preparation["reference"], observation_authority=authority)


def _observe(prepared, tmp_path, *, total=100, resumed=False, session="root-session"):
    checkout, _handoff, _startup, _brief, _preparation, contract = prepared
    native = _write_root(tmp_path / "root.jsonl", total=total, sequence=1,
                         resumed=resumed, session_id=session)
    capability = _capability(tmp_path, settings.load_settings().digest)
    phase_admission.observe_pending(str(checkout), contract, snapshot=native,
                                    capability=capability, observation_authority=AUTHORITY)


def test_prepared_phase_intent_is_not_root_admission_and_replay_is_exact(prepared):
    checkout, handoff, startup, brief, preparation, contract = prepared
    assert preparation["status"] == "prepared"
    assert preparation["dispatch_allowed"] is False
    assert preparation["intent"]["evidence"]["authoritative"] is False
    assert preparation["intent"]["evidence"]["execution_observed"] is False
    assert phase_admission.pending_contract(str(checkout))["phase_admission_reference"] == preparation["reference"]
    repeated = phase_admission.prepare(str(checkout), handoff, startup, brief,
                                      wait_policy=loop.event_wait_policy("fixture-phase-build", 1))
    assert repeated == preparation
    before = phase_admission._load(str(checkout), preparation["reference"])
    decision = _admit(prepared)
    assert decision["status"] == "waiting-for-root-observation"
    assert decision["dispatch_allowed"] is False
    assert phase_admission._load(str(checkout), preparation["reference"]) == before
    assert contract["worker_lifecycle"]["status"] == "pending"


def test_authenticated_phase_root_uses_existing_atomic_admission_and_replay(prepared, tmp_path):
    _observe(prepared, tmp_path)
    decision = _admit(prepared)
    assert decision["dispatch_allowed"] is True
    assert decision["operation_status"] == "admitted"
    assert decision["root_usage"]["total_tokens"] == 100
    assert decision["binding"]["dispatch_id"] == prepared[4]["intent"]["intent_id"]
    assert decision["binding"]["thread_id"] == prepared[3]["task_name"]
    repeated = _admit(prepared)
    assert repeated["operation_status"] == "duplicate"
    assert repeated["binding"] == decision["binding"]
    with pytest.raises(ValueError, match="unauthentic"):
        _admit(prepared, authority=b"foreign-provider-authority")


def test_started_phase_worker_remains_the_current_meter_owner(prepared):
    checkout, _handoff, _startup, brief, preparation, _contract = prepared
    bound = kernel.bind_worker_contract_event(str(checkout), {
        "agent_id": "phase-child", "session_id": "phase-session", "task_name": brief["task_name"]})
    assert bound["contract"]["worker_lifecycle"]["status"] == "active"
    assert phase_admission.pending_contract(str(checkout))["phase_admission_reference"] == preparation["reference"]


@pytest.mark.parametrize("case, reason", [
    ("resumed", "root_resume_forbidden"),
    ("seed-budget", "root_seed_budget_exceeded"),
    ("source-replaced", "root_usage_unavailable"),
])
def test_phase_root_keeps_shared_refusal_and_sticky_policy(prepared, tmp_path, case, reason):
    total = (settings.load_settings().workflow.root_session.seed_budget_tokens + 2
             if case == "seed-budget" else 100)
    _observe(prepared, tmp_path, total=total, resumed=case == "resumed")
    if case == "source-replaced":
        _observe(prepared, tmp_path, total=200, session="foreign-root")
    decision = _admit(prepared)
    assert decision["dispatch_allowed"] is False
    assert decision["root_admission"]["reason_code"] == reason
    assert decision["root_admission"]["sticky"] is True
    assert decision["binding"] is None
    assert _admit(prepared)["dispatch_allowed"] is False


@pytest.mark.parametrize("case", ["attempt", "seed-reference", "ledger", "symlink"])
def test_phase_admission_rejects_foreign_cache_and_unsafe_reference(prepared, case, tmp_path):
    checkout, handoff, startup, brief, preparation, _contract = prepared
    reference = preparation["reference"]
    path = checkout / reference
    if case == "attempt":
        altered = copy.deepcopy(brief)
        altered["producer_contract"]["attempt_id"] = "foreign-attempt"
        with pytest.raises(ValueError, match="foreign"):
            phase_admission.admit(str(checkout), handoff, startup, altered,
                                  reference=reference, observation_authority=AUTHORITY)
        return
    if case == "symlink":
        source = tmp_path / "foreign.json"
        path.rename(source)
        path.symlink_to(source)
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        if case == "seed-reference":
            value["seed_ref"] = "predecessor/root-seed.json"
        else:
            value["ledger"]["source_sha"] = "d" * 40
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="foreign|symlinks"):
        _admit(prepared)


def test_phase_hook_routes_observation_without_loading_a_loop(prepared, tmp_path, monkeypatch):
    checkout, _handoff, _startup, _brief, _preparation, contract = prepared
    native = _write_root(tmp_path / "hook-root.jsonl", total=100, sequence=1)
    capability = _capability(tmp_path, settings.load_settings().digest)
    # tp's direct-executable hook imports use the top-level module aliases.
    import phase_admission as hook_admission
    import loop as hook_loop
    import spend as hook_spend
    monkeypatch.setattr(hook_loop, "load", _forbidden)
    monkeypatch.setattr(hook_admission, "pending_contract", lambda _: contract)
    calls = []
    monkeypatch.setattr(hook_admission, "observe_pending", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(hook_spend, "event_transcript", lambda _: "fixture-provider.jsonl")
    monkeypatch.setattr(cli, "_bounded_transcript_projection", lambda *_args: {
        "status": "available", "native_session": native, "usage": {"total_tokens": 100}})
    monkeypatch.setattr(cli, "_host_capability_snapshot", lambda _: "observed-host-capability")
    monkeypatch.setattr(cli.host_caps, "root_session_capability", lambda *_args, **_kwargs: capability)
    monkeypatch.setattr(cli, "_transcript_projection_authority", lambda _: AUTHORITY)
    cli._observe_active_loop_orchestrator(str(checkout), {"turn_id": "phase-root-turn"})
    assert len(calls) == 1
    assert calls[0][0] == (str(checkout), contract)
    assert calls[0][1] == {"snapshot": native, "capability": capability,
                          "observation_authority": AUTHORITY}


def _expect(checkout, handoff, brief, intent):
    kernel.record_expected_dispatch(
        str(checkout), "step", brief["role"], brief["model_tier"], brief["model"],
        ref=handoff["handoff_id"], task_name=brief["task_name"],
        reasoning_effort=brief["reasoning_effort"], role_marker_value=brief["role_marker"],
        dispatch_route=brief.get("dispatch_route"), intent_id=intent["intent_id"],
        intent_run_id=intent["identity"]["run_id"])


def _screen(checkout, brief, tmp_path, monkeypatch, capsys):
    import loop as hook_loop
    import review as hook_review
    import spend as hook_spend
    predecessor_reads = []

    def forbidden_read(*args, **kwargs):
        predecessor_reads.append((args, kwargs))
        _forbidden(*args, **kwargs)

    for name in ("load", "_load_raw", "record_native_orchestrator_snapshot", "record_native_dispatch_observation"):
        monkeypatch.setattr(hook_loop, name, forbidden_read)
    monkeypatch.setattr(hook_review, "_load_state", forbidden_read)
    native = _write_root(tmp_path / "screen-root.jsonl", total=100, sequence=1)
    capability = _capability(tmp_path, settings.load_settings().digest)
    monkeypatch.setattr(hook_spend, "event_transcript", lambda _: "fixture-provider.jsonl")
    monkeypatch.setattr(cli, "_bounded_transcript_projection", lambda *_args: {
        "status": "available", "native_session": native, "usage": {"total_tokens": 100}})
    monkeypatch.setattr(cli, "_host_capability_snapshot", lambda _: "observed-host-capability")
    monkeypatch.setattr(cli.host_caps, "root_session_capability", lambda *_args, **_kwargs: capability)
    monkeypatch.setattr(cli, "_transcript_projection_authority", lambda _: AUTHORITY)
    monkeypatch.setenv("TASKPLANE_ENFORCE_DISPATCH", "strict")
    event = {"cwd": str(checkout), "turn_id": "phase-root-turn", "tool_input": {
        "task_name": brief["task_name"], "model": brief["model"],
        "reasoning_effort": brief["reasoning_effort"], "fork_turns": "none",
        "message": brief["role_marker"]}}
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    assert cli.cmd_screen_dispatch(None) == 0
    assert predecessor_reads == []
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_build_dispatch_hook_admits_phase_without_any_loop_observation(
        prepared, tmp_path, monkeypatch, capsys):
    checkout, handoff, _startup, brief, preparation, _contract = prepared
    _expect(checkout, handoff, brief, preparation["intent"])
    output = _screen(checkout, brief, tmp_path, monkeypatch, capsys)
    assert not any(row.get("hookSpecificOutput", {}).get("permissionDecision") == "deny" for row in output), output
    assert kernel.peek_expectation(str(checkout), brief["task_name"]) is None
    value = phase_admission._load(str(checkout), preparation["reference"])
    assert value["host_start"] is not None
    assert value["ledger"]["bindings"][0]["dispatch_id"] == preparation["intent"]["intent_id"]


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_non_build_phase_dispatch_intent_uses_common_audit_without_loop(
        tmp_path, monkeypatch, capsys, phase):
    checkout, handoff = _published_checkout(tmp_path, phase)
    startup = kernel.create_stateless_phase_startup(handoff)
    brief = phase_dispatch._hydrated_brief(str(checkout), handoff, startup)
    intent = phase_admission.create_intent(str(checkout), handoff, brief,
                                           wait_policy=loop.event_wait_policy("fixture-phase", 1))
    contract = phase_dispatch._native_contract(str(checkout), brief, handoff)
    contract.update(task_id=brief["task_slot"], phase_dispatch=brief, phase_startup=startup,
                    phase_handoff_fingerprint=handoff["fingerprint"], phase_dispatch_intent=intent)
    contract = kernel.prepare_worker_contract(
        str(checkout), contract, stage="phase-" + phase, task=handoff["handoff_id"],
        task_name=brief["task_name"], role_marker=brief["role_marker"])
    contract["worker_lifecycle"].update(dispatch_intent_id=intent["intent_id"],
                                         dispatch_intent_run_id=intent["identity"]["run_id"])
    kernel.activate(str(checkout), contract, task_slot_override=brief["task_slot"])
    _expect(checkout, handoff, brief, intent)
    output = _screen(checkout, brief, tmp_path, monkeypatch, capsys)
    assert not any(row.get("hookSpecificOutput", {}).get("permissionDecision") == "deny" for row in output), output
    assert kernel.peek_expectation(str(checkout), brief["task_name"]) is None


@pytest.mark.parametrize("field", ["intent-run", "brief", "policy"])
def test_phase_intent_mismatch_denies_before_consuming_expectation(prepared, tmp_path, monkeypatch, capsys, field):
    checkout, handoff, _startup, brief, preparation, contract = prepared
    _expect(checkout, handoff, brief, preparation["intent"])
    if field == "intent-run":
        contract["worker_lifecycle"]["dispatch_intent_run_id"] = "foreign"
    elif field == "brief":
        contract["phase_dispatch"]["output_paths"] = ["**"]
    else:
        contract["write_allow"] = ["**"]
    kernel.atomic_write_json(kernel.active_contract_path(str(checkout), brief["task_slot"]), contract)
    output = _screen(checkout, brief, tmp_path, monkeypatch, capsys)
    assert any(row.get("hookSpecificOutput", {}).get("permissionDecision") == "deny" for row in output)
    assert kernel.peek_expectation(str(checkout), brief["task_name"]) is not None


def test_public_phase_start_exposes_waiting_until_dispatch_is_allowed(prepared, monkeypatch, capsys):
    checkout, handoff, startup, brief, _preparation, _contract = prepared
    import phase_dispatch as hook_dispatch
    calls = []

    def bind(_workspace, _handoff, _startup, **kwargs):
        calls.append(kwargs)
        return startup, {**brief, "dispatch_allowed": False, "activation": "bound"}

    monkeypatch.setattr(hook_dispatch, "bind_native_worker", bind)
    monkeypatch.setattr(cli, "_transcript_projection_authority", lambda _: AUTHORITY)
    assert cli._phase_start(SimpleNamespace(workspace=str(checkout), handoff=phase_handoff.handoff_path(
        handoff["handoff_id"])), "next-phase") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "waiting"
    assert result["code"] == "phase-waiting"
    assert calls == [{"observation_authority": AUTHORITY}]


def test_fresh_cli_build_pickup_uses_canonical_typed_settings_and_waits(tmp_path):
    checkout, handoff = _published_checkout(tmp_path, "build")
    cli_path = Path(__file__).resolve().parents[1] / "tp.py"
    command = subprocess.run(
        [sys.executable, str(cli_path), "phase", "pickup",
         phase_handoff.handoff_path(handoff["handoff_id"]), "--workspace", str(checkout)],
        cwd=checkout, env={**os.environ, "TASKPLANE_HOME": str(tmp_path / "empty-home")},
        text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    assert command.returncode == 0, command.stdout + command.stderr
    result = json.loads(command.stdout)
    assert result["status"] == "waiting"
    assert result["code"] == "phase-waiting"
    assert result["dispatch"]["dispatch_allowed"] is False
    admission = result["dispatch"]["admission"]
    assert admission["status"] == "waiting-for-root-observation"
    cache = phase_admission._load(str(checkout), admission["reference"])
    assert cache["host_start"] is None
    assert cache["ledger"]["bindings"] == []
