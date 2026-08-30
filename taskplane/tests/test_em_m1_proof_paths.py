"""Focused M-14/M-15 proof-path closure for R-0002 M1-D."""

from __future__ import annotations

import hashlib
import json

import pytest

from taskplane import taskplane_lite
from taskplane.delivery_ports import FakeClock, content_fingerprint
from taskplane.producer_observation import (
    ProducerObservationError,
    consume_matching_observation,
    record_codex_subagent_stop,
    validate_consumed_matching_observation,
)


_PRODUCERS = {
    "evaluate": ("tp-evaluator", "taskplane.evaluator-output/v1"),
    "em": ("tp-engineering", "taskplane.em-output/v1"),
}


def _codex_production_material(workspace, stage):
    """Construct host-shaped input while exercising only production policy.

    This is the mandatory hermetic CI boundary, not a claim that CI itself is
    a live Codex host.  Genuine host emission remains the explicitly optional
    companion canary in ``test_r0001_live_host_canary.py``.
    """
    producer, schema = _PRODUCERS[stage]
    task_id = f"task-{stage}"
    task_name = f"tp_step_{stage}_{task_id}_deadbeef"
    output = (json.dumps({"schema": schema, "verdict": "pass"}) + "\n").encode()
    dispatch_projection = {
        "run_id": "run-required-codex-production-path",
        "task_id": task_id,
        "stage": stage,
        "producer": producer,
        "task_name": task_name,
        "role_marker": f"taskplane-role:{producer}",
        "model": None,
        "reasoning_effort": "medium",
    }
    dispatch = {
        **dispatch_projection,
        "fingerprint": content_fingerprint(dispatch_projection),
    }
    event = {
        "hook_event_name": "SubagentStop",
        "session_id": "codex-required-production-session",
        "turn_id": f"codex-required-{stage}-turn",
        "agent_id": f"codex-required-{stage}-agent",
        "agent_type": task_name,
        "task_name": task_name,
    }
    common = {
        "workspace": str(workspace),
        "evidence_root": str(workspace / "evidence"),
        "run_id": dispatch_projection["run_id"],
        "task_id": task_id,
        "stage": stage,
        "producer": producer,
        "output_path": f".eval/{stage}-result.json",
        "output_bytes": output,
        "output_schema_id": schema,
        "output_contract_fingerprint": hashlib.sha256(
            f"{stage}-output-contract".encode()
        ).hexdigest(),
        "source_sha": "c" * 40,
        "producer_dispatch": dispatch,
        "clock": FakeClock(wall_time=11.0, monotonic=1.0),
    }
    claim = hashlib.sha256(
        taskplane_lite.hook_event_identity(
            str(workspace), "subagent-stop", event
        ).encode()
    ).hexdigest()
    return common, event, claim


def _record_consume_validate(workspace, stage):
    common, event, claim = _codex_production_material(workspace, stage)
    receipt = record_codex_subagent_stop(
        event=event,
        hook_claim_id=claim,
        **common,
    )
    assert consume_matching_observation(**common) == receipt
    assert validate_consumed_matching_observation(receipt, **common) == receipt
    return receipt, common, event


def test_m15_live_Codex_producer_event_path_is_required(tmp_path):
    for stage in ("evaluate", "em"):
        workspace = tmp_path / stage
        workspace.mkdir()
        receipt, _common, event = _record_consume_validate(workspace, stage)
        stopping_identity = json.loads(receipt["host_session_or_turn"])
        assert receipt["host"] == "codex"
        assert receipt["stage"] == stage
        assert stopping_identity["session_id"] == event["session_id"]
        assert stopping_identity["turn_id"] == event["turn_id"]
        assert stopping_identity["agent_id"] == event["agent_id"]
        assert stopping_identity["task_name"] == event["task_name"]


def test_m15_production_path_rejects_relabel_and_exact_output_tamper(tmp_path):
    relabel_workspace = tmp_path / "relabel"
    relabel_workspace.mkdir()
    common, event, _claim = _codex_production_material(
        relabel_workspace, "evaluate"
    )
    relabelled = {
        **event,
        "agent_type": "general-purpose",
        "task_name": "not-the-dispatched-evaluator",
    }
    relabelled_claim = hashlib.sha256(
        taskplane_lite.hook_event_identity(
            str(relabel_workspace), "subagent-stop", relabelled
        ).encode()
    ).hexdigest()
    with pytest.raises(ProducerObservationError, match="stopping agent"):
        record_codex_subagent_stop(
            event=relabelled,
            hook_claim_id=relabelled_claim,
            **common,
        )

    tamper_workspace = tmp_path / "tamper"
    tamper_workspace.mkdir()
    receipt, common, _event = _record_consume_validate(
        tamper_workspace, "em"
    )
    with pytest.raises(ProducerObservationError, match="mismatched"):
        validate_consumed_matching_observation(
            receipt,
            **{**common, "output_bytes": common["output_bytes"] + b" "},
        )
