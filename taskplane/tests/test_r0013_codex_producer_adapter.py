from __future__ import annotations

import hashlib
import shutil
import io
import json
import types

import pytest

from taskplane import taskplane_lite
from taskplane.delivery_ports import FakeClock, content_fingerprint
from taskplane.producer_observation import (
    ProducerObservationError,
    consume_matching_observation,
    record_codex_subagent_stop,
    validate_consumed_matching_observation,
)


def _args(tmp_path, *, clock=FakeClock(wall_time=10.0, monotonic=1.0),
          claim=None):
    raw = b'{"schema":"taskplane.evaluator-output/v1"}\n'
    dispatch_projection = {
        "run_id": "run-r0013",
        "task_id": "task-a",
        "stage": "evaluate",
        "producer": "tp-evaluator",
        "task_name": "tp_step_evaluator_task_a_deadbeef",
        "role_marker": "taskplane-role:tp-evaluator",
        "model": None,
        "reasoning_effort": "medium",
    }
    common = {
        "workspace": str(tmp_path),
        "evidence_root": str(tmp_path / "evidence"),
        "run_id": "run-r0013",
        "task_id": "task-a",
        "stage": "evaluate",
        "producer": "tp-evaluator",
        "output_path": ".eval/verdict.json",
        "output_bytes": raw,
        "output_schema_id": "taskplane.evaluator-output/v1",
        "output_contract_fingerprint": "b" * 64,
        "source_sha": "c" * 40,
        "producer_dispatch": {
            **dispatch_projection,
            "fingerprint": content_fingerprint(dispatch_projection),
        },
        "clock": clock,
    }
    event = {
        "hook_event_name": "SubagentStop",
        "session_id": "codex-session",
        "turn_id": "turn-1",
        "agent_id": "agent-1",
        "agent_type": dispatch_projection["task_name"],
        "task_name": dispatch_projection["task_name"],
    }
    if claim is None:
        claim = hashlib.sha256(
            taskplane_lite.hook_event_identity(
                str(tmp_path), "subagent-stop", event).encode("utf-8")
        ).hexdigest()
    return common, event, claim


def test_native_stop_records_and_submit_consumes_once(tmp_path):
    common, event, claim = _args(tmp_path)
    receipt = record_codex_subagent_stop(
        event=event, hook_claim_id=claim, **common)

    consumed = consume_matching_observation(**common)

    assert consumed == receipt
    assert validate_consumed_matching_observation(receipt, **{
        **common, "clock": FakeClock(wall_time=10.0, monotonic=1.0)
    }) == receipt
    with pytest.raises(ProducerObservationError, match="replay"):
        consume_matching_observation(**common)


def test_native_bridge_duplicate_is_idempotent_not_a_second_mint(tmp_path):
    common, event, claim = _args(tmp_path)
    first = record_codex_subagent_stop(
        event=event, hook_claim_id=claim, **common)
    second = record_codex_subagent_stop(
        event=event, hook_claim_id=claim, **common)

    assert second == first
    receipts = list((tmp_path / "evidence").glob(
        ".taskplane-evidence/*/*/producer_observation/receipts/*.json"))
    assert len(receipts) == 1


@pytest.mark.parametrize(
    "event",
    [
        {"hook_event_name": "SubagentStop", "turn_id": "turn-1"},
        {"hook_event_name": "SubagentStop", "session_id": "session-1"},
        {"hook_event_name": "Stop", "session_id": "session-1",
         "turn_id": "turn-1"},
        {"hook_event_name": "SubagentStop", "session_id": "session-1",
         "turn_id": "turn-1", "agent_id": "agent-1"},
    ],
)
def test_synthetic_or_unbound_event_is_refused(tmp_path, event, monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    common, _, claim = _args(tmp_path)
    with pytest.raises(ProducerObservationError):
        record_codex_subagent_stop(
            event=event, hook_claim_id=claim, **common)


def test_stale_and_mismatched_exact_bytes_fail_closed(tmp_path):
    common, event, claim = _args(tmp_path)
    record_codex_subagent_stop(event=event, hook_claim_id=claim, **common)

    with pytest.raises(ProducerObservationError, match="stale"):
        consume_matching_observation(
            **{**common, "clock": FakeClock(wall_time=311.0,
                                             monotonic=2.0)})

    other = tmp_path / "other"
    other.mkdir()
    common, event, claim = _args(other)
    record_codex_subagent_stop(event=event, hook_claim_id=claim, **common)
    with pytest.raises(ProducerObservationError, match="mismatched"):
        consume_matching_observation(
            **{**common, "output_bytes": common["output_bytes"] + b" "})


def test_unrelated_stopping_agent_cannot_be_relabeled_as_evaluator(tmp_path):
    common, event, claim = _args(tmp_path)
    unrelated = {**event, "agent_id": "unrelated-agent",
                 "agent_type": "general-purpose",
                 "task_name": "unrelated-task"}

    with pytest.raises(ProducerObservationError, match="stopping agent"):
        record_codex_subagent_stop(
            event=unrelated, hook_claim_id=claim, **common)

    assert not list((tmp_path / "evidence").glob(
        ".taskplane-evidence/*/*/producer_observation/receipts/*.json"))


def test_stopping_agent_id_is_bound_to_the_native_hook_claim(tmp_path):
    common, event, claim = _args(tmp_path)
    sibling = {**event, "agent_id": "sibling-agent"}

    with pytest.raises(ProducerObservationError, match="hook claim"):
        record_codex_subagent_stop(
            event=sibling, hook_claim_id=claim, **common)


def test_gate_rejects_arbitrary_consumption_marker_bytes(tmp_path):
    common, event, claim = _args(tmp_path)
    receipt = record_codex_subagent_stop(
        event=event, hook_claim_id=claim, **common)
    namespace = next((tmp_path / "evidence" / ".taskplane-evidence").glob(
        "*/*"))
    consumed = namespace / "producer_observation" / "consumed"
    consumed.mkdir(parents=True)
    (consumed / f"{receipt['fingerprint']}.json").write_text(
        "synthetic\n", encoding="utf-8")

    with pytest.raises(ProducerObservationError, match="marker is corrupt"):
        validate_consumed_matching_observation(
            receipt, **{**common, "clock": FakeClock(
                wall_time=10.0, monotonic=1.0)})


def test_gate_rejects_consumption_marker_copied_from_another_store(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    common_a, event_a, claim_a = _args(first)
    receipt_a = record_codex_subagent_stop(
        event=event_a, hook_claim_id=claim_a, **common_a)
    consume_matching_observation(**common_a)
    marker_a = next((first / "evidence").glob(
        ".taskplane-evidence/*/*/producer_observation/consumed/*.json"))

    common_b, event_b, claim_b = _args(second)
    receipt_b = record_codex_subagent_stop(
        event=event_b, hook_claim_id=claim_b, **common_b)
    namespace_b = next((second / "evidence" / ".taskplane-evidence").glob(
        "*/*"))
    consumed_b = namespace_b / "producer_observation" / "consumed"
    consumed_b.mkdir(parents=True)
    shutil.copyfile(marker_a, consumed_b / f"{receipt_b['fingerprint']}.json")

    with pytest.raises(ProducerObservationError, match="marker mismatched"):
        validate_consumed_matching_observation(
            receipt_b, **{**common_b, "clock": FakeClock(
                wall_time=10.0, monotonic=1.0)})


@pytest.mark.parametrize("gate_time", [9.0, 311.0])
def test_gate_rejects_future_or_stale_consumed_observation(
        tmp_path, gate_time):
    common, event, claim = _args(tmp_path)
    receipt = record_codex_subagent_stop(
        event=event, hook_claim_id=claim, **common)
    consume_matching_observation(**common)

    with pytest.raises(ProducerObservationError, match="stale"):
        validate_consumed_matching_observation(
            receipt, **{**common, "clock": FakeClock(
                wall_time=gate_time, monotonic=2.0)})


def test_copied_or_ambiguous_receipt_fails_closed(tmp_path):
    common, event, claim = _args(tmp_path)
    record_codex_subagent_stop(event=event, hook_claim_id=claim, **common)
    receipts = list((tmp_path / "evidence").glob(
        ".taskplane-evidence/*/*/producer_observation/receipts/*.json"))
    copied = receipts[0].with_name("d" * 64 + ".json")
    shutil.copyfile(receipts[0], copied)

    with pytest.raises(ProducerObservationError, match="corrupt|ambiguous"):
        consume_matching_observation(**common)


def test_subagent_stop_wires_native_observation_before_submission_check(
    tmp_path, monkeypatch, capsys,
):
    import loop as loop_runtime
    import producer_observation as native_policy
    import tp as cli

    state = {"step": "evaluate", "delivery_mode_receipt": {"sealed": True}}
    task = {"id": "task-a"}
    material, _, _ = _args(tmp_path)
    observed = []
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    monkeypatch.setattr(cli, "_subagent_workspace", lambda _event: str(tmp_path))
    monkeypatch.setattr(cli.tp, "trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.tp, "load_active", lambda _ws: {
        "submission_contract": {"stage": "evaluate", "task": "task-a"}})
    monkeypatch.setattr(loop_runtime, "load", lambda _ws: state)
    monkeypatch.setattr(loop_runtime, "_current_task", lambda _state: task)
    monkeypatch.setattr(loop_runtime, "_validated_delivery_mode",
                        lambda _state: {"fingerprint": "f" * 64})
    monkeypatch.setattr(loop_runtime, "producer_output_identity",
                        lambda *_args, **_kwargs: material)
    monkeypatch.setattr(
        native_policy, "record_codex_subagent_stop",
        lambda **kwargs: observed.append(kwargs) or {"fingerprint": "e" * 64})
    monkeypatch.setattr(cli, "_submission_stop_check", lambda _event: {
        "block": True, "contract_id": "contract-a", "task": "task-a",
        "stage": "evaluate", "slot": "task-a", "status": "missing",
        "artifact": "loop submission", "recovery": "retry submit"})
    event = {"hook_event_name": "SubagentStop", "session_id": "session-1",
             "turn_id": "turn-1", "_taskplane_hook_claim_id": "a" * 64,
             "agent_id": "agent-1",
             "agent_type": "tp_step_evaluator_task_a_deadbeef",
             "task_name": "tp_step_evaluator_task_a_deadbeef",
             "cwd": str(tmp_path)}
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))

    rc = cli.cmd_subagent_stop(types.SimpleNamespace())

    assert rc == 2
    assert observed and observed[0]["output_bytes"] == material["output_bytes"]
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
