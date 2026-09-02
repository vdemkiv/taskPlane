"""Native lifecycle telemetry preserves exact attempts and honest usage."""

from __future__ import annotations

import hashlib
import io
import json
import os

import pytest

from taskplane import dispatch_telemetry
from taskplane import native_session_meter
from taskplane import settings as operational_settings
from taskplane import host_native
from taskplane import loop
from taskplane import retro
from taskplane import taskplane_lite as tp
from taskplane import tp as cli
from taskplane import wave_metrics


RUN_ID = "run-native-terminal-telemetry"
SOURCE_SHA = "a" * 40
DESIGN_FINGERPRINT = "b" * 64
PLAN_FINGERPRINT = "c" * 64
CANDIDATE_FINGERPRINT = "d" * 64


def _state(*, step: str = "execute") -> dict:
    return {
        "governance_revision": 2,
        "run_id": RUN_ID,
        "baseline": SOURCE_SHA,
        "design_fingerprint": DESIGN_FINGERPRINT,
        "plan_fingerprint": PLAN_FINGERPRINT,
        "step": step,
        "tasks": [{
            "id": "task-a", "deps": [], "status": "running",
            "fix_cycles": 0,
        }],
        "current_task": 0,
        "authority_effect_outbox": {},
    }


def _intent(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _task_name(label: str) -> str:
    return f"tp_step_executor_task_a_{label}"


def _role_marker() -> str:
    return tp.role_marker("tp-executor")


def _record_expectation(
        workspace: str, *, label: str, run_id: str = RUN_ID,
        intent_id: str | None = None) -> dict:
    expected = {
        "intent_id": intent_id or _intent(label),
        "intent_run_id": run_id,
        "task_name": _task_name(label),
    }
    tp.record_expected_dispatch(
        workspace, "step", "tp-executor", "standard", None,
        ref="task-a", task_name=expected["task_name"],
        reasoning_effort="medium", role_marker_value=_role_marker(),
        intent_id=expected["intent_id"], intent_run_id=run_id)
    return expected


def _screen_dispatch(
        workspace: str, expected: dict, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str], *,
        fork_turns: str | None = "none",
        preflight_tokens: int | None = 2) -> list[dict]:
    event = {
        "cwd": workspace,
        "tool_input": {
            "task_name": expected["task_name"],
            "model": None,
            "reasoning_effort": "medium",
            "message": _role_marker(),
        },
    }
    if preflight_tokens is not None:
        root_transcript = os.path.join(
            workspace, ".taskplane", "test-root-session.jsonl")
        os.makedirs(os.path.dirname(root_transcript), exist_ok=True)
        _write_codex_transcript(
            root_transcript, label="root", input_tokens=preflight_tokens,
            cached_tokens=min(1, preflight_tokens), output_tokens=0)
        event["transcript_path"] = root_transcript
    if fork_turns is not None:
        event["tool_input"]["fork_turns"] = fork_turns
    monkeypatch.setenv("TASKPLANE_ENFORCE_DISPATCH", "strict")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    assert cli.cmd_screen_dispatch(None) == 0
    return [
        json.loads(line) for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]


def _activate_worker(
        workspace: str, expected: dict, *, slot: str,
        max_tokens: int | None = None) -> dict:
    contract = tp.build_contract(
        "EXECUTE: task-a", scope=["taskplane/**"], tools=["Read"])
    contract["task_id"] = slot
    contract = tp.prepare_worker_contract(
        workspace, contract, stage="execute", task="task-a",
        task_name=expected["task_name"], role_marker=_role_marker(), now=10)
    contract["worker_lifecycle"]["dispatch_intent_id"] = \
        expected["intent_id"]
    contract["worker_lifecycle"]["dispatch_intent_run_id"] = \
        expected["intent_run_id"]
    if max_tokens is not None:
        contract["budget"]["max_tokens"] = max_tokens
    tp.activate(
        workspace, contract, snapshot=SOURCE_SHA,
        task_slot_override=contract["task_slot"])
    return contract


def _worker_event(workspace: str, expected: dict, *, label: str) -> dict:
    return {
        "hook_event_name": "SubagentStart",
        "cwd": workspace,
        "session_id": f"session-{label}",
        "agent_id": f"agent-{label}",
        "agent_type": expected["task_name"],
        "task_name": expected["task_name"],
        "turn_id": f"turn-{label}",
    }


def _start_worker(
        event: dict, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    assert cli.cmd_subagent_start(None) == 0
    assert "permissionDecision\": \"deny" not in capsys.readouterr().out


def _write_codex_transcript(path: os.PathLike[str], *, label: str,
                            input_tokens: int, cached_tokens: int,
                            output_tokens: int,
                            session_id: str | None = None,
                            resumed: bool = False) -> None:
    total = input_tokens + output_tokens
    native_session_id = session_id or f"session-{label}"
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "timestamp": "2026-09-01T00:00:00Z",
            "type": "session_meta",
            "payload": {
                "session_id": native_session_id,
                "id": native_session_id,
                "timestamp": "2026-09-01T00:00:00Z",
                "thread_source": "subagent",
                **({"history_base": {"kind": "resume"}}
                   if resumed else {}),
            },
        }) + "\n")
        stream.write(json.dumps({
            "timestamp": "2026-09-01T00:00:01Z",
            "ordinal": 1,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_tokens,
                    "cache_write_input_tokens": 0,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": 0,
                    "total_tokens": total,
                }},
            },
        }) + "\n")


def _stop_worker(
        event: dict, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str], *, outcome: str,
        transcript: os.PathLike[str] | None = None,
        expected_rc: int = 0) -> list[dict]:
    stop = {**event, "hook_event_name": "SubagentStop", "outcome": outcome}
    if transcript is not None:
        stop["agent_transcript_path"] = os.fspath(transcript)
    monkeypatch.setattr(cli, "_submission_stop_check", lambda _event: None)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(stop)))
    assert cli.cmd_subagent_stop(None) == expected_rc
    return [
        json.loads(line) for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]


def _bound_attempt(workspace: str, expected: dict) -> dict:
    return next(
        row for row in loop.load(workspace)["dispatch_telemetry"]["bindings"]
        if row["dispatch_id"] == expected["intent_id"])


class _Clock:
    def __init__(self, now: float = 20.0) -> None:
        self.now = now

    def wall_time(self) -> float:
        return self.now


def _usage(total: int, *, cached: int = 0, output: int = 0) -> dict:
    input_tokens = total - output
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "uncached_input_tokens": input_tokens - cached,
        "output_tokens": output,
        "reasoning_tokens": 0,
        "total_tokens": total,
    }


def _dispatch(dispatch_id: str, *, thread_type: str = "worker") -> dict:
    return {
        "dispatch_id": dispatch_id,
        "thread_id": f"thread-{dispatch_id}",
        "thread_type": thread_type,
        "task_id": dispatch_id,
        "dependencies": [],
        "shared_owner": None,
        "started_at": 10,
        "ended_at": 10,
        "wait_duration_seconds": 0,
        "correction_count": 0,
        "events": [],
    }


def _ledger() -> dict:
    return dispatch_telemetry.new_ledger(
        run_id=RUN_ID, source_sha=SOURCE_SHA,
        design_fingerprint=DESIGN_FINGERPRINT,
        plan_fingerprint=PLAN_FINGERPRINT, started_at=10)


def _root_meter(total: int, *, authority: bytes,
                terminal_reason: str | None = None,
                prior: dict | None = None, sequence: int = 1) -> dict:
    usage = _usage(total, cached=min(total, 4), output=0)
    snapshot = {
        "schema": native_session_meter.SNAPSHOT_SCHEMA,
        "session_id": "root-session",
        "root_session_id": "root-session",
        "parent_session_id": None,
        "thread_source": "agent_created_thread",
        "agent_path": None,
        "started_at": "2026-09-02T00:00:00Z",
        "resumed": False,
        "ordinal": sequence,
        "observed_at": "2026-09-02T00:00:01Z",
        "usage": usage,
        "source": {
            "path_fingerprint": "e" * 64, "device": 1, "inode": 2,
            "size": 3, "metadata_record_sha256": "f" * 64,
            "counter_record_sha256": "0" * 64,
        },
        "source_identity_fingerprint": "1" * 64,
    }
    snapshot["fingerprint"] = native_session_meter.fingerprint(snapshot)
    sealed = native_session_meter.seal_root_observation(
        snapshot, sequence=sequence, session_role="root",
        status_receipt_fingerprint="2" * 64,
        terminal_reason=terminal_reason, authority=authority)
    return native_session_meter.fold_root_observations(
        [sealed], authority=authority,
        prior=prior["watermark"] if prior is not None else None)


def _configure_root(ledger: dict):
    snapshot = operational_settings.load_settings()
    dispatch_telemetry.configure_root_admission(
        ledger,
        root_session_settings=snapshot.workflow.root_session.to_dict(),
        settings_digest=snapshot.digest)
    return snapshot


def test_real_codex_and_claude_usage_is_positive_delta_from_authenticated_baseline_and_is_consumed(
        tmp_path) -> None:
    ledger = _ledger()
    codex_path = tmp_path / "codex.jsonl"
    _write_codex_transcript(
        codex_path, label="root", input_tokens=90,
        cached_tokens=20, output_tokens=10)
    codex_baseline, _ = dispatch_telemetry.project_transcript_usage(
        str(codex_path), provider="codex")
    _write_codex_transcript(
        codex_path, label="root", input_tokens=130,
        cached_tokens=30, output_tokens=15)
    codex_current, _ = dispatch_telemetry.project_transcript_usage(
        str(codex_path), provider="codex")

    claude_path = tmp_path / "claude.jsonl"
    claude_rows = [{"id": "baseline", "usage": {
        "input_tokens": 100, "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 0, "output_tokens": 10}}]
    claude_path.write_text(
        json.dumps(claude_rows[0]) + "\n", encoding="utf-8")
    claude_baseline, checkpoint = \
        dispatch_telemetry.project_transcript_usage(
            str(claude_path), provider="claude")
    claude_rows.append({"id": "delta", "usage": {
        "input_tokens": 40, "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 0, "output_tokens": 10}})
    claude_path.write_text(
        "".join(json.dumps(row) + "\n" for row in claude_rows),
        encoding="utf-8")
    claude_current, _ = dispatch_telemetry.project_transcript_usage(
        str(claude_path), provider="claude", checkpoint=checkpoint)

    observed_total = 0
    for provider, baseline_projection, current_projection in (
            ("codex", codex_baseline, codex_current),
            ("claude", claude_baseline, claude_current)):
        assert baseline_projection["status"] == "available"
        assert current_projection["status"] == "available"
        source = (baseline_projection.get("source_identity_fingerprint") or
                  baseline_projection["path_fingerprint"])
        dispatch_id = f"attempt-{provider}"
        dispatch_telemetry.bind_dispatch(ledger, _dispatch(dispatch_id))
        dispatch_telemetry.capture_usage_baseline(
            ledger, dispatch_id=dispatch_id, provider=provider,
            usage=baseline_projection["usage"], source_fingerprint=source)
        observed = dispatch_telemetry.observe_terminal_usage_delta(
            ledger, dispatch_id=dispatch_id, provider=provider,
            usage=current_projection["usage"], source_fingerprint=source)
        expected = (current_projection["usage"]["total_tokens"] -
                    baseline_projection["usage"]["total_tokens"])
        assert observed["usage"]["total_tokens"] == expected > 0
        observed_total += expected
        finalized = dispatch_telemetry.finalize_usage(
            ledger, dispatch_id=dispatch_id, ended_at=20,
            clock=_Clock())
        assert finalized["receipt"]["total_tokens"] > 0

    budget = dispatch_telemetry.budget_projection(ledger, _Clock())
    assert budget["usage"]["total_tokens"] == observed_total == 105
    assert budget["usage_capability"]["status"] == "available"


def test_required_missing_or_terminal_unavailable_usage_blocks_release_and_new_dispatch() -> None:
    ledger = _ledger()
    dispatch_telemetry.bind_dispatch(ledger, _dispatch("attempt-missing"))
    dispatch_telemetry.capture_usage_baseline(
        ledger, dispatch_id="attempt-missing", provider="codex",
        usage=_usage(100), source_fingerprint="6" * 64)
    dispatch_telemetry.terminalize_unavailable(
        ledger, dispatch_id="attempt-missing", ended_at=20,
        outcome="failed", reason="terminal native counter unavailable")

    screen = dispatch_telemetry.screen_dispatch(
        ledger, _Clock(), current_stage="execute",
        outstanding_set_fingerprint="7" * 64,
        preserved_context_fingerprint="8" * 64)
    assert screen["dispatch_allowed"] is False
    assert screen["usage_capability"]["status"] == "unavailable"
    with pytest.raises(dispatch_telemetry.DispatchTelemetryError):
        dispatch_telemetry.closed_wave_metrics_source(
            ledger, _Clock(), candidate_fingerprint="9" * 64)


def test_root_budget_refuses_new_dispatch_while_active_workers_terminalize_once_without_redispatch() -> None:
    ledger = _ledger()
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("active-worker"), usage=_usage(5),
        source_fingerprint="b" * 64)
    settings_snapshot = _configure_root(ledger)
    authority = b"host-observation-authority"
    dispatch_telemetry.record_root_meter(
        ledger, _root_meter(40_000_000, authority=authority),
        observation_authority=authority)

    screen = dispatch_telemetry.screen_dispatch(
        ledger, _Clock(), current_stage="execute",
        outstanding_set_fingerprint="c" * 64,
        preserved_context_fingerprint="d" * 64,
        observation_authority=authority)
    assert screen["dispatch_allowed"] is False
    assert screen["root_usage"]["total_tokens"] == 40_000_000
    assert screen["worker_usage"]["total_tokens"] == 5
    assert screen["wave_usage"]["total_tokens"] == 40_000_005
    assert screen["root_admission"]["settings_consumed"] == \
        settings_snapshot.workflow.root_session.to_dict()

    first = dispatch_telemetry.finalize_usage(
        ledger, dispatch_id="active-worker", ended_at=20,
        clock=_Clock())
    replay = dispatch_telemetry.finalize_usage(
        ledger, dispatch_id="active-worker", ended_at=20,
        clock=_Clock())
    assert first["status"] == "admitted"
    assert replay["status"] == "duplicate"
    assert len(ledger["dispatches"]) == 1
    assert ledger["dispatches"][0]["events"] == []
    assert len(ledger["bindings"]) == 1


def test_root_usage_missing_or_at_ceiling_cannot_silently_resume_dispatch() -> None:
    ledger = _ledger()
    _configure_root(ledger)

    missing = dispatch_telemetry.screen_dispatch(
        ledger, _Clock(), current_stage="execute",
        outstanding_set_fingerprint="e" * 64,
        preserved_context_fingerprint="f" * 64)
    assert missing["dispatch_allowed"] is False
    assert missing["root_admission"]["reason_code"] == \
        "root_usage_unavailable"

    authority = b"host-observation-authority"
    dispatch_telemetry.record_root_meter(
        ledger, _root_meter(10, authority=authority),
        observation_authority=authority)
    replay = dispatch_telemetry.screen_dispatch(
        ledger, _Clock(), current_stage="execute",
        outstanding_set_fingerprint="e" * 64,
        preserved_context_fingerprint="f" * 64,
        observation_authority=authority)
    assert replay["dispatch_allowed"] is False
    assert replay["root_admission"]["sticky"] is True

    fresh = _ledger()
    _configure_root(fresh)
    dispatch_telemetry.record_root_meter(
        fresh, _root_meter(40_000_000, authority=authority),
        observation_authority=authority)
    ceiling = dispatch_telemetry.screen_dispatch(
        fresh, _Clock(), current_stage="execute",
        outstanding_set_fingerprint="1" * 64,
        preserved_context_fingerprint="2" * 64,
        observation_authority=authority)
    assert ceiling["dispatch_allowed"] is False
    assert ceiling["root_admission"]["reason_code"] == \
        "root_budget_reached"


def test_root_meter_ledger_rejects_projection_tamper_and_cross_call_rollback() -> None:
    authority = b"host-observation-authority"
    ledger = _ledger()
    _configure_root(ledger)
    meter = _root_meter(40_000_000, authority=authority)
    dispatch_telemetry.record_root_meter(
        ledger, meter, observation_authority=authority)

    stored = ledger["root_admission"]["meter"]
    stored["usage"] = _usage(1, cached=1)
    stored["first_observed_input_tokens"] = 1
    stored["peak_context_tokens"] = 1
    stored["context_rent_tokens"] = 0.0
    material = dict(stored)
    material.pop("fingerprint")
    stored["fingerprint"] = native_session_meter.fingerprint(material)
    with pytest.raises(
        dispatch_telemetry.DispatchTelemetryError,
        match="authenticated watermark",
    ):
        dispatch_telemetry.validate_ledger(ledger)

    fresh = _ledger()
    _configure_root(fresh)
    dispatch_telemetry.record_root_meter(
        fresh, _root_meter(40_000_000, authority=authority),
        observation_authority=authority)
    with pytest.raises(
        dispatch_telemetry.DispatchTelemetryError,
        match="observation authority changed",
    ):
        dispatch_telemetry.record_root_meter(
            fresh,
            _root_meter(
                3, authority=b"replacement-observation-authority"),
            observation_authority=b"replacement-observation-authority")

    with pytest.raises(
        dispatch_telemetry.DispatchTelemetryError,
        match="conflicting evidence",
    ):
        dispatch_telemetry.record_root_meter(
            fresh, _root_meter(40_000_001, authority=authority),
            observation_authority=authority)

    rollback = _ledger()
    _configure_root(rollback)
    first = _root_meter(10, authority=authority)
    high = _root_meter(
        40_000_000, authority=authority, prior=first, sequence=2)
    low = _root_meter(11, authority=authority, prior=first, sequence=2)
    rolled = _root_meter(
        12, authority=authority, prior=low, sequence=3)
    dispatch_telemetry.record_root_meter(
        rollback, high, observation_authority=authority)
    with pytest.raises(
        dispatch_telemetry.DispatchTelemetryError,
        match="cumulative watermark moved backwards",
    ):
        dispatch_telemetry.record_root_meter(
            rollback, rolled, observation_authority=authority)


def test_persisted_root_meter_stale_authenticator_cannot_reopen_admission() -> None:
    authority = b"host-observation-authority"
    ledger = _ledger()
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("active-worker"), usage=_usage(1),
        source_fingerprint="3" * 64)
    _configure_root(ledger)
    dispatch_telemetry.record_root_meter(
        ledger, _root_meter(40_000_000, authority=authority),
        observation_authority=authority)

    persisted = json.loads(json.dumps(ledger))
    meter = persisted["root_admission"]["meter"]
    watermark = meter["watermark"]
    reduced_usage = _usage(1, cached=1)
    for projection in (watermark, meter):
        projection["usage"] = reduced_usage
        projection["first_observed_input_tokens"] = 1
        projection["peak_context_tokens"] = 1
        projection["context_rent_tokens"] = 0.0
    watermark_material = dict(watermark)
    watermark_material.pop("fingerprint")
    watermark_material.pop("authenticator")
    watermark["fingerprint"] = native_session_meter.fingerprint(
        watermark_material)
    meter_material = dict(meter)
    meter_material.pop("fingerprint")
    meter["fingerprint"] = native_session_meter.fingerprint(meter_material)

    with pytest.raises(
        dispatch_telemetry.DispatchTelemetryError,
        match="watermark is not authentic",
    ):
        dispatch_telemetry.screen_dispatch(
            persisted, _Clock(), current_stage="execute",
            outstanding_set_fingerprint="4" * 64,
            preserved_context_fingerprint="5" * 64,
            observation_authority=authority,
            admission_operation_id="reopened-worker",
            dispatch=_dispatch("reopened-worker"), usage=_usage(1),
            source_fingerprint="6" * 64)
    assert all(row["dispatch_id"] != "reopened-worker"
               for row in persisted["bindings"])


def test_root_screen_and_worker_binding_are_one_idempotent_admission_operation() -> None:
    authority = b"host-observation-authority"
    ledger = _ledger()
    # Pre-existing measured usage makes the general worker-budget projection
    # available before root admission is configured.
    dispatch_telemetry.bind_dispatch(
        ledger, _dispatch("active-worker"), usage=_usage(1),
        source_fingerprint="3" * 64)
    _configure_root(ledger)
    first = _root_meter(10, authority=authority)
    below = _root_meter(
        39_999_999, authority=authority, prior=first, sequence=2)
    dispatch_telemetry.record_root_meter(
        ledger, below, observation_authority=authority)

    stale = dispatch_telemetry.screen_dispatch(
        ledger, _Clock(), current_stage="execute",
        outstanding_set_fingerprint="4" * 64,
        preserved_context_fingerprint="5" * 64,
        observation_authority=authority)
    assert stale["dispatch_allowed"] is True

    ceiling = _root_meter(
        40_000_000, authority=authority, prior=below, sequence=3)
    dispatch_telemetry.record_root_meter(
        ledger, ceiling, observation_authority=authority)
    with pytest.raises(
        dispatch_telemetry.DispatchTelemetryError,
        match="atomic screen_dispatch admission",
    ):
        dispatch_telemetry.bind_dispatch(
            ledger, _dispatch("stale-worker"), usage=_usage(1),
            source_fingerprint="6" * 64)

    refused = dispatch_telemetry.screen_dispatch(
        ledger, _Clock(), current_stage="execute",
        outstanding_set_fingerprint="4" * 64,
        preserved_context_fingerprint="5" * 64,
        observation_authority=authority,
        admission_operation_id="new-worker",
        dispatch=_dispatch("new-worker"), usage=_usage(1),
        source_fingerprint="6" * 64)
    assert refused["dispatch_allowed"] is False
    assert all(row["dispatch_id"] != "new-worker"
               for row in ledger["bindings"])

    open_ledger = _ledger()
    dispatch_telemetry.bind_dispatch(
        open_ledger, _dispatch("prior-worker"), usage=_usage(1),
        source_fingerprint="7" * 64)
    _configure_root(open_ledger)
    dispatch_telemetry.record_root_meter(
        open_ledger, below, observation_authority=authority)
    before = open_ledger["revision"]
    admitted = dispatch_telemetry.screen_dispatch(
        open_ledger, _Clock(), current_stage="execute",
        outstanding_set_fingerprint="8" * 64,
        preserved_context_fingerprint="9" * 64,
        observation_authority=authority,
        admission_operation_id="atomic-worker",
        dispatch=_dispatch("atomic-worker"), usage=_usage(1),
        source_fingerprint="a" * 64)
    revision = open_ledger["revision"]
    replay = dispatch_telemetry.screen_dispatch(
        open_ledger, _Clock(), current_stage="execute",
        outstanding_set_fingerprint="8" * 64,
        preserved_context_fingerprint="9" * 64,
        observation_authority=authority,
        admission_operation_id="atomic-worker",
        dispatch=_dispatch("atomic-worker"), usage=_usage(1),
        source_fingerprint="a" * 64)
    assert admitted["operation_status"] == "admitted"
    assert replay["operation_status"] == "duplicate"
    assert open_ledger["revision"] == revision == before + 1
    assert len([row for row in open_ledger["bindings"]
                if row["dispatch_id"] == "atomic-worker"]) == 1


@pytest.mark.parametrize(
    ("outcome", "terminal_kind"),
    [
        ("success", "complete"),
        ("failure", "failed"),
        ("cancellation", "cancelled"),
        ("interruption", "interrupted"),
        ("handoff", "handoff"),
    ],
)
def test_subagent_stop_seals_content_bound_usage_and_actual_outcome(
        tmp_path, monkeypatch, capsys, outcome, terminal_kind):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    expected = _record_expectation(workspace, label=terminal_kind)

    assert not any(
        (row.get("hookSpecificOutput") or {}).get("permissionDecision") ==
        "deny"
        for row in _screen_dispatch(workspace, expected, monkeypatch, capsys)
    )
    binding = _bound_attempt(workspace, expected)
    assert binding["task_id"] == "task-a"
    assert binding["thread_id"] == expected["task_name"]

    contract = _activate_worker(
        workspace, expected, slot=f"task_attempt_{terminal_kind}")
    event = _worker_event(workspace, expected, label=terminal_kind)
    _start_worker(event, monkeypatch, capsys)
    transcript = tmp_path / f"{terminal_kind}.jsonl"
    _write_codex_transcript(
        transcript, label=terminal_kind, input_tokens=13,
        cached_tokens=5, output_tokens=3)
    projection, _ = dispatch_telemetry.project_transcript_usage(
        str(transcript), provider="codex")

    output = _stop_worker(
        event, monkeypatch, capsys, outcome=outcome, transcript=transcript)

    assert output == [{}]
    state = loop.load(workspace)
    binding = _bound_attempt(workspace, expected)
    receipt = next(
        row for row in state["dispatch_telemetry"]["dispatches"]
        if row["dispatch_id"] == expected["intent_id"])
    assert binding["usage_source_fingerprint"] == \
        projection["source_fingerprint"]
    assert receipt["events"][-1]["kind"] == terminal_kind
    assert receipt["total_tokens"] == 16
    assert receipt["cached_input_tokens"] == 5
    native = state["native_session_telemetry"]
    assert native["aggregate"]["usage"]["total_tokens"] == 18
    worker_native = next(row for row in native["records"]
                         if row["task_id"] == "task-a")
    assert worker_native["attributed_usage"]["total_tokens"] == 16
    assert worker_native["session_id"] == \
        f"session-{terminal_kind}"
    assert not os.path.exists(tp.active_contract_path(
        workspace, contract["task_slot"]))


def test_native_counter_reaches_nonzero_retro_and_dashboard_consumers(
        tmp_path, monkeypatch, capsys):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    expected = _record_expectation(workspace, label="consumer-wire")
    _screen_dispatch(workspace, expected, monkeypatch, capsys)
    _activate_worker(workspace, expected, slot="task_consumer_wire")
    event = _worker_event(workspace, expected, label="consumer-wire")
    _start_worker(event, monkeypatch, capsys)
    transcript = tmp_path / "consumer-wire.jsonl"
    _write_codex_transcript(
        transcript, label="consumer-wire", input_tokens=13,
        cached_tokens=5, output_tokens=3)
    _stop_worker(
        event, monkeypatch, capsys, outcome="success",
        transcript=transcript)

    state = loop.load(workspace)
    state["settings_digest"] = "f" * 64
    state["run_artifact_binding"] = {
        "candidate": {"fingerprint": CANDIDATE_FINGERPRINT},
        "settings_digest": "f" * 64,
    }
    sealed = loop._seal_terminal_metrics_before_retro(workspace, state)
    assert sealed["status"] == "measured"
    assert state["wave_metrics_receipt"]

    retro_projection = retro.sealed_wave_metrics_projection(state)
    assert retro_projection["token_usage"]["status"] == "available"
    assert retro_projection["token_usage"]["total_tokens"] == 18
    assert retro_projection["metrics"]["token_total_observed"][
        "actual"] == 18

    report = {
        "tasks": [], "hook_denials": 0, "parallel_waves": 1,
        "findings": {"total": 0}, "execution_metrics": {},
        "execution_metric_source": "native dispatch ledger",
        "wave_metrics": retro_projection,
        "evaluator_summary": {"total": 0, "by_status": {},
                              "by_reason": {}, "evaluators": []},
        "graph_true_up": {"content_fingerprint": "a" * 64,
                          "scanned_head": SOURCE_SHA, "modules": 1,
                          "edges": 0, "components": 1},
        "lessons": [],
    }
    retro._write_report(workspace, state, report, [])
    retro_text = (tmp_path / ".taskplane" / "retro.md").read_text(
        encoding="utf-8")
    assert "token usage status: available" in retro_text
    assert "observed total tokens: 18" in retro_text

    dashboard_projection = wave_metrics.consumer_projection(
        state["wave_metrics_receipt"], consumer="dashboard")
    dashboard_html = host_native.render_wave_metrics_projection(
        dashboard_projection)
    assert 'data-wave-metric="token_total_observed"' in dashboard_html
    assert "actual 18 tokens" in dashboard_html


def test_resumed_native_session_reset_is_attributed_as_a_new_segment(
        tmp_path, monkeypatch, capsys):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    shared_session = "session-resumed-worker"
    expected_totals = [12, 20]
    for index, total in enumerate(expected_totals, start=1):
        expected = _record_expectation(
            workspace, label=f"resume-attempt-{index}")
        _screen_dispatch(workspace, expected, monkeypatch, capsys)
        _activate_worker(
            workspace, expected, slot=f"task_resume_attempt_{index}")
        event = _worker_event(
            workspace, expected, label=f"resume-attempt-{index}")
        _start_worker(event, monkeypatch, capsys)
        transcript = tmp_path / f"resume-attempt-{index}.jsonl"
        _write_codex_transcript(
            transcript, label=f"resume-attempt-{index}",
            input_tokens=total - 2, cached_tokens=4 + index - 1,
            output_tokens=2, session_id=shared_session,
            resumed=index > 1)
        _stop_worker(
            event, monkeypatch, capsys, outcome="success",
            transcript=transcript)

    state = loop.load(workspace)
    records = [row for row in state["native_session_telemetry"]["records"]
               if row["session_id"] == shared_session]
    assert [row["attributed_usage"]["total_tokens"] for row in records] == [
        12, 20]
    assert len({row["snapshot"]["source_identity_fingerprint"]
                for row in records}) == 2
    assert state["native_session_telemetry"]["aggregate"]["usage"][
        "total_tokens"] == 34  # 2 root + 12 first + 20 resumed segment
    receipts = [row for row in state["dispatch_telemetry"]["dispatches"]
                if row["thread_type"] == "worker"]
    assert [row["total_tokens"] for row in receipts] == [12, 20]
    assert sum(row["total_tokens"] for row in receipts) == 32


def test_missing_native_counter_blocks_terminal_release_without_zero_fallback(
        tmp_path, monkeypatch, capsys):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    expected = _record_expectation(workspace, label="missing")
    assert not any(
        (row.get("hookSpecificOutput") or {}).get("permissionDecision") ==
        "deny"
        for row in _screen_dispatch(workspace, expected, monkeypatch, capsys)
    )
    contract = _activate_worker(workspace, expected, slot="task_missing")
    event = _worker_event(workspace, expected, label="missing")
    _start_worker(event, monkeypatch, capsys)

    output = _stop_worker(
        event, monkeypatch, capsys, outcome="failure", expected_rc=2)

    state = loop.load(workspace)
    binding = _bound_attempt(workspace, expected)
    denial = next(row for row in output if row.get("decision") == "block")
    assert "native terminal counter" in denial["reason"]
    assert binding["usage"] is None
    assert binding["usage_source_fingerprint"] is None
    assert binding["finalized_receipt_fingerprint"] is None
    assert state["dispatch_telemetry"]["dispatches"] == []
    assert os.path.exists(tp.active_contract_path(
        workspace, contract["task_slot"]))


def test_terminal_native_counter_at_pickup_ceiling_blocks_release(
        tmp_path, monkeypatch, capsys):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    expected = _record_expectation(workspace, label="terminal-ceiling")
    _screen_dispatch(workspace, expected, monkeypatch, capsys)
    contract = _activate_worker(
        workspace, expected, slot="task_terminal_ceiling", max_tokens=10)
    event = _worker_event(workspace, expected, label="terminal-ceiling")
    _start_worker(event, monkeypatch, capsys)
    transcript = tmp_path / "terminal-ceiling.jsonl"
    _write_codex_transcript(
        transcript, label="terminal-ceiling", input_tokens=8,
        cached_tokens=2, output_tokens=2)

    output = _stop_worker(
        event, monkeypatch, capsys, outcome="success",
        transcript=transcript, expected_rc=2)

    denial = next(row for row in output if row.get("decision") == "block")
    assert "TOKEN BUDGET exhausted (10/10 native tokens)" in \
        denial["reason"]
    state = loop.load(workspace)
    assert all(row["thread_type"] == "main"
               for row in state["dispatch_telemetry"]["dispatches"])
    assert os.path.exists(tp.active_contract_path(
        workspace, contract["task_slot"]))


@pytest.mark.parametrize("observed", [None, "all"])
def test_dispatch_refuses_severed_context_boundary_before_binding(
        tmp_path, monkeypatch, capsys, observed):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    expected = _record_expectation(workspace, label=f"context-{observed}")

    output = _screen_dispatch(
        workspace, expected, monkeypatch, capsys, fork_turns=observed)

    denial = next(row["hookSpecificOutput"] for row in output
                  if "hookSpecificOutput" in row)
    assert denial["permissionDecision"] == "deny"
    assert "fork_turns" in denial["permissionDecisionReason"]
    state = loop.load(workspace)
    assert (state.get("dispatch_telemetry") or {}).get("bindings", []) == []
    assert tp.peek_expectation(
        workspace, expected["task_name"], strict=True)["matched"] is False


@pytest.mark.parametrize("preflight_tokens", [None, 0])
def test_null_or_zero_native_meter_refuses_before_worker_dispatch_binding(
        tmp_path, monkeypatch, capsys, preflight_tokens):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    expected = _record_expectation(workspace, label="null-preflight")

    output = _screen_dispatch(
        workspace, expected, monkeypatch, capsys,
        preflight_tokens=preflight_tokens)

    denial = next(row["hookSpecificOutput"] for row in output
                  if "hookSpecificOutput" in row)
    assert denial["permissionDecision"] == "deny"
    assert "non-zero native orchestrator counter" in \
        denial["permissionDecisionReason"]
    state = loop.load(workspace)
    assert "dispatch_telemetry" not in state
    assert "native_session_telemetry" not in state
    assert tp.peek_expectation(
        workspace, expected["task_name"], strict=True)["matched"] is False


def test_canonical_context_flows_from_intent_into_observed_spawn_boundary(
        tmp_path, monkeypatch, capsys):
    workspace = str(tmp_path)
    state = _state()
    loop.save(workspace, state)
    task_name = _task_name("context-live")
    native_intent = loop._native_dispatch_intent(
        workspace, state, step="execute", task_id="task-a",
        dispatch={"role": "tp-executor", "task_name": task_name},
        wait_policy=loop.event_wait_policy("execute:task-a", 1))
    assert native_intent["fork_turns"] == "none"
    assert native_intent["inherited_turns"] == 0
    expected = _record_expectation(
        workspace, label="context-live",
        intent_id=native_intent["intent_id"])

    output = _screen_dispatch(
        workspace, expected, monkeypatch, capsys,
        fork_turns=native_intent["fork_turns"])

    assert not any(
        (row.get("hookSpecificOutput") or {}).get("permissionDecision") ==
        "deny" for row in output)
    binding = _bound_attempt(workspace, expected)
    assert binding["dispatch_id"] == native_intent["intent_id"]
    assert binding["task_id"] == "task-a"
    assert tp.peek_expectation(
        workspace, expected["task_name"], strict=True) is None


@pytest.mark.parametrize("explicit_ceiling", [None, 10])
def test_canonical_pickup_budget_is_nonzero_and_controls_real_screen(
        tmp_path, monkeypatch, capsys, explicit_ceiling):
    workspace = str(tmp_path)
    contract = tp.build_contract(
        "budget wiring", read_only=True, tools=["Read"])
    if explicit_ceiling is not None:
        cli._apply_contract_token_ceiling(contract, explicit_ceiling)
    budget = contract["budget"]
    assert 0 < budget["target_tokens"] < budget["max_tokens"]
    assert budget["token_meter"] == "native_total_tokens"
    assert budget["token_usage_required"] is True
    tp.activate(workspace, contract, snapshot=SOURCE_SHA)

    def screen(total_tokens: int) -> list[dict]:
        transcript = tmp_path / f"screen-{total_tokens}.jsonl"
        _write_codex_transcript(
            transcript, label=str(total_tokens), input_tokens=total_tokens,
            cached_tokens=0, output_tokens=0)
        event = {
            "cwd": workspace,
            "turn_id": "turn-budget",
            "transcript_path": str(transcript),
            "tool_name": "Read",
            "tool_input": {"path": str(tmp_path / "input.txt")},
        }
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
        assert cli.cmd_screen(None) == 0
        return [json.loads(line) for line in
                capsys.readouterr().out.splitlines() if line.strip()]

    assert not any(row.get("decision") == "block" for row in screen(
        budget["max_tokens"] - 1))
    denial = next(row for row in screen(budget["max_tokens"])
                  if row.get("decision") == "block")
    assert "TOKEN BUDGET exhausted" in denial["reason"]
    assert f"{budget['max_tokens']:,}/{budget['max_tokens']:,}" in \
        denial["reason"]


def test_ungoverned_main_hook_keeps_active_loop_native_total_current(
        tmp_path, monkeypatch, capsys):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    transcript = tmp_path / "native-main.jsonl"

    def screen(total_tokens: int) -> None:
        _write_codex_transcript(
            transcript, label="native-main", input_tokens=total_tokens,
            cached_tokens=0, output_tokens=0,
            session_id="native-main-session")
        event = {
            "cwd": workspace,
            "turn_id": "turn-main",
            "transcript_path": str(transcript),
            "tool_name": "Read",
            "tool_input": {"path": str(tmp_path / "input.txt")},
        }
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
        assert cli.cmd_screen(None) == 0
        assert capsys.readouterr().out == ""

    screen(7)
    screen(11)

    state = loop.load(workspace)
    bindings = (state["dispatch_telemetry"] or {})["bindings"]
    assert len(bindings) == 1
    assert bindings[0]["thread_type"] == "main"
    assert bindings[0]["usage"]["total_tokens"] == 11
    ledger = state["native_session_telemetry"]
    assert [row["attributed_usage"]["total_tokens"]
            for row in ledger["records"]] == [7, 4]
    assert ledger["aggregate"]["usage"]["total_tokens"] == 11
    assert ledger["aggregate"]["physical_segments"] == 1


def test_main_meter_observability_failure_never_claims_ungoverned_authority(
        tmp_path, monkeypatch, capsys):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    monkeypatch.setattr(
        cli.tp, "trace", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("trace unavailable")))
    event = {
        "cwd": workspace,
        "turn_id": "turn-main",
        "tool_name": "Read",
        "tool_input": {"path": str(tmp_path / "input.txt")},
    }
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))

    assert cli.cmd_screen(None) == 0
    assert capsys.readouterr().out == ""


def test_usage_and_terminal_replay_are_idempotent_for_one_exact_attempt(
        tmp_path, monkeypatch, capsys):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    expected = _record_expectation(workspace, label="replay")
    _screen_dispatch(workspace, expected, monkeypatch, capsys)
    usage = {
        "schema": "taskplane.token-usage/v2",
        "available": True,
        "provider": "codex",
        "reason": None,
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "uncached_input_tokens": 6,
        "cache_creation_tokens": 0,
        "output_tokens": 2,
        "reasoning_tokens": 0,
        "total_tokens": 12,
        "raw_total_tokens": 12,
        "effective_tokens": 8,
    }
    source = "e" * 64

    first_observation = loop.record_observed_dispatch_usage(
        workspace, task_id="task-a", normalized_usage=usage,
        source_fingerprint=source, native_task_name=expected["task_name"],
        dispatch_id=expected["intent_id"])
    replayed_observation = loop.record_observed_dispatch_usage(
        workspace, task_id="task-a", normalized_usage=usage,
        source_fingerprint=source, native_task_name=expected["task_name"],
        dispatch_id=expected["intent_id"])
    ended_at = float(_bound_attempt(workspace, expected)["started_at"]) + 1
    first_terminal = loop.finalize_observed_dispatch_usage(
        workspace, task_id="task-a", ended_at=ended_at, outcome="success",
        native_task_name=expected["task_name"],
        dispatch_id=expected["intent_id"])
    replayed_terminal = loop.finalize_observed_dispatch_usage(
        workspace, task_id="task-a", ended_at=ended_at, outcome="success",
        native_task_name=expected["task_name"],
        dispatch_id=expected["intent_id"])

    assert replayed_observation == first_observation
    assert first_terminal["status"] == "admitted"
    assert replayed_terminal["status"] == "duplicate"
    assert replayed_terminal["receipt"] == first_terminal["receipt"]
    assert len(loop.load(workspace)["dispatch_telemetry"]["dispatches"]) == 1


def test_retry_attempts_with_same_plan_task_cannot_cross_bind_usage(
        tmp_path, monkeypatch, capsys):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    attempts = [
        _record_expectation(workspace, label="retry-one"),
        _record_expectation(workspace, label="retry-two"),
    ]
    for expected in attempts:
        _screen_dispatch(workspace, expected, monkeypatch, capsys)

    with pytest.raises(
            dispatch_telemetry.DispatchTelemetryError,
            match="ambiguous"):
        loop.record_observed_dispatch_usage(
            workspace, task_id="task-a", normalized_usage={
                "schema": "taskplane.token-usage/v2",
                "available": True,
                "provider": "codex",
                "reason": None,
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "uncached_input_tokens": 1,
                "cache_creation_tokens": 0,
                "output_tokens": 1,
                "reasoning_tokens": 0,
                "total_tokens": 2,
                "raw_total_tokens": 2,
                "effective_tokens": 2,
            }, source_fingerprint="f" * 64)

    for index, expected in enumerate(attempts, start=1):
        usage = {
            "schema": "taskplane.token-usage/v2",
            "available": True,
            "provider": "codex",
            "reason": None,
            "input_tokens": index,
            "cached_input_tokens": 0,
            "uncached_input_tokens": index,
            "cache_creation_tokens": 0,
            "output_tokens": index,
            "reasoning_tokens": 0,
            "total_tokens": 2 * index,
            "raw_total_tokens": 2 * index,
            "effective_tokens": 2 * index,
        }
        loop.record_observed_dispatch_usage(
            workspace, task_id="task-a", normalized_usage=usage,
            source_fingerprint=str(index) * 64,
            native_task_name=expected["task_name"],
            dispatch_id=expected["intent_id"])

    for index, expected in enumerate(attempts, start=1):
        ended_at = float(_bound_attempt(
            workspace, expected)["started_at"]) + index
        loop.finalize_observed_dispatch_usage(
            workspace, task_id="task-a", ended_at=ended_at,
            outcome="success", native_task_name=expected["task_name"],
            dispatch_id=expected["intent_id"])

    receipts = {
        row["dispatch_id"]: row
        for row in loop.load(workspace)["dispatch_telemetry"]["dispatches"]
    }
    assert receipts[attempts[0]["intent_id"]]["total_tokens"] == 2
    assert receipts[attempts[1]["intent_id"]]["total_tokens"] == 4


def test_cross_run_intent_is_denied_and_remains_retryable(
        tmp_path, monkeypatch, capsys):
    workspace = str(tmp_path)
    loop.save(workspace, _state())
    expected = _record_expectation(
        workspace, label="wrong-run", run_id="another-run")

    output = _screen_dispatch(workspace, expected, monkeypatch, capsys)

    denial = next(row["hookSpecificOutput"] for row in output
                  if "hookSpecificOutput" in row)
    assert denial["permissionDecision"] == "deny"
    assert "another governed run" in denial["permissionDecisionReason"]
    assert tp.peek_expectation(
        workspace, expected["task_name"], strict=True)["matched"] is False
    bindings = loop.load(workspace)["dispatch_telemetry"]["bindings"]
    assert all(row["thread_type"] == "main" for row in bindings)
    assert bindings[0]["usage"]["total_tokens"] == 2


def test_terminal_intent_census_mismatch_reports_usage_unavailable(
        tmp_path):
    workspace = str(tmp_path)
    expected = _record_expectation(workspace, label="never-started")
    state = _state()
    state.update({
        "dispatch_telemetry": dispatch_telemetry.new_ledger(
            run_id=RUN_ID, source_sha=SOURCE_SHA,
            design_fingerprint=DESIGN_FINGERPRINT,
            plan_fingerprint=PLAN_FINGERPRINT, started_at=0),
        "settings_digest": "f" * 64,
        "run_artifact_binding": {
            "candidate": {"fingerprint": CANDIDATE_FINGERPRINT},
            "settings_digest": "f" * 64,
        },
    })

    result = loop._seal_terminal_metrics_before_retro(workspace, state)
    projection = retro.sealed_wave_metrics_projection(state)

    assert result["status"] == "unavailable"
    assert expected["intent_id"] in result["reason"] or \
        "unstarted intents" in result["reason"]
    assert "wave_metrics_receipt" not in state
    assert projection["token_usage"]["status"] == "unavailable"
    assert projection["token_usage"]["total_tokens"] is None
    assert projection["token_usage"]["effective_tokens"] is None


def test_active_run_refuses_standalone_lens_dispatch_before_untracked_state(
        tmp_path, capsys):
    workspace = str(tmp_path)
    loop.save(workspace, _state(step="em"))

    rc = cli.main([
        "lens", "dispatch", "--workspace", workspace, "--emit", "task",
    ])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "governed delivery run is active" in captured.err
    assert "tp loop next" in captured.err
    assert tp.dispatch_report(workspace)["expected"] == 0
    assert tp.dispatch_intent_census(workspace, RUN_ID)["intent_ids"] == []
    assert "dispatch_telemetry" not in loop.load(workspace)
    assert not (tmp_path / ".em-review").exists()
