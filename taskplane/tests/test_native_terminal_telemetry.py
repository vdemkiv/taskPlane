"""Native lifecycle telemetry preserves exact attempts and honest usage."""

from __future__ import annotations

import hashlib
import io
import json
import os

import pytest

from taskplane import dispatch_telemetry
from taskplane import loop
from taskplane import retro
from taskplane import taskplane_lite as tp
from taskplane import tp as cli


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
        workspace: str, *, label: str, run_id: str = RUN_ID) -> dict:
    expected = {
        "intent_id": _intent(label),
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
        capsys: pytest.CaptureFixture[str]) -> list[dict]:
    event = {
        "cwd": workspace,
        "tool_input": {
            "task_name": expected["task_name"],
            "model": None,
            "reasoning_effort": "medium",
            "message": _role_marker(),
        },
    }
    monkeypatch.setenv("TASKPLANE_ENFORCE_DISPATCH", "strict")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    assert cli.cmd_screen_dispatch(None) == 0
    return [
        json.loads(line) for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]


def _activate_worker(workspace: str, expected: dict, *, slot: str) -> dict:
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
                            output_tokens: int) -> None:
    total = input_tokens + output_tokens
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "message": {
                "id": f"message-{label}",
                "usage": {
                    "input_tokens": input_tokens,
                    "input_tokens_details": {
                        "cached_tokens": cached_tokens,
                    },
                    "output_tokens": output_tokens,
                    "total_tokens": total,
                },
            },
        }) + "\n")


def _stop_worker(
        event: dict, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str], *, outcome: str,
        transcript: os.PathLike[str] | None = None) -> list[dict]:
    stop = {**event, "hook_event_name": "SubagentStop", "outcome": outcome}
    if transcript is not None:
        stop["agent_transcript_path"] = os.fspath(transcript)
    monkeypatch.setattr(cli, "_submission_stop_check", lambda _event: None)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(stop)))
    assert cli.cmd_subagent_stop(None) == 0
    return [
        json.loads(line) for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]


def _bound_attempt(workspace: str, expected: dict) -> dict:
    return next(
        row for row in loop.load(workspace)["dispatch_telemetry"]["bindings"]
        if row["dispatch_id"] == expected["intent_id"])


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
    assert not os.path.exists(tp.active_contract_path(
        workspace, contract["task_slot"]))


def test_missing_transcript_closes_attempt_without_inventing_zero_tokens(
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

    assert _stop_worker(
        event, monkeypatch, capsys, outcome="failure") == [{}]

    state = loop.load(workspace)
    binding = _bound_attempt(workspace, expected)
    assert binding["usage"] is None
    assert binding["usage_source_fingerprint"] is None
    assert binding["finalized_receipt_fingerprint"] is None
    assert binding["events"][-1]["kind"] == "failed"
    assert binding["events"][-1]["payload"]["usage_status"] == "unavailable"
    attribution = dispatch_telemetry.terminal_attempt_attribution(
        state["dispatch_telemetry"])
    assert len(attribution) == 1
    assert attribution[0]["outcome"] == "failed"
    assert attribution[0]["usage_status"] == "unavailable"
    assert attribution[0]["total_tokens"] is None
    assert attribution[0]["uncached_input_tokens"] is None
    assert attribution[0]["effective_tokens"] is None
    assert not os.path.exists(tp.active_contract_path(
        workspace, contract["task_slot"]))


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
    assert loop.load(workspace)["dispatch_telemetry"]["bindings"] == []


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
