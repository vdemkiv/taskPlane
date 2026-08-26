from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from taskplane import evaluation_output, loop
from taskplane.delivery_ports import (
    FakeClock,
    RecordedHostActionCapabilitySource,
    RecordedProducerEventSource,
    SandboxEvidenceStore,
)
from taskplane.producer_observation import (
    ProducerObservationError,
    observe_submission,
    validate_producer_observation,
)


FIXTURE = Path(__file__).parent / "fixtures" / "r0001" / "codex-producer-events.jsonl"


def _fixture_rows():
    rows = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        raw = base64.b64decode(event.pop("output_base64"), validate=True)
        rows.append((event, raw))
    return rows


def _observe(tmp_path, event, raw, *, events=None, source=None, store=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = source or RecordedHostActionCapabilitySource()
    handle = source.issue(
        capability_id=f"cap-{event['event_id']}",
        purpose="producer_observation",
        sequence=1,
        host_session_id=event["host_session_id"],
        host_turn_id=event["host_turn_id"],
        run_id=event["run_id"],
        kernel_id=None,
        task_id=event["task_id"],
        stage=event["stage"],
        request_or_output_digest=event["output_sha256"],
        contract_fingerprint=event["output_contract_fingerprint"],
        issued_at=10.0,
        expires_at=20.0,
        nonce=f"nonce-{event['event_id']}",
    )
    store = store or SandboxEvidenceStore(
        tmp_path, "repository-fingerprint", event["run_id"]
    )
    receipt = observe_submission(
        run_id=event["run_id"],
        task_id=event["task_id"],
        stage=event["stage"],
        producer=event["producer"],
        host=event["host"],
        host_session_id=event["host_session_id"],
        host_turn_id=event["host_turn_id"],
        output_path=event["output_path"],
        output_bytes=raw,
        output_schema_id=event["output_schema_id"],
        output_contract_fingerprint=event["output_contract_fingerprint"],
        source_sha=event["source_sha"],
        capability_handle=handle,
        event_source=events or RecordedProducerEventSource([event]),
        capability_source=source,
        evidence_store=store,
        clock=FakeClock(wall_time=11.0, monotonic=1.0),
    )
    return receipt, source, handle, store


def test_codex_evaluator_submission_has_host_observation(tmp_path):
    event, raw = _fixture_rows()[0]
    receipt, _, _, store = _observe(tmp_path, event, raw)

    assert validate_producer_observation(receipt) == receipt
    assert receipt["schema"] == "taskplane.producer-observation/v1"
    assert receipt["stage"] == "evaluate"
    assert receipt["host"] == "codex"
    assert receipt["host_session_or_turn"] == "codex-session-1:evaluate-turn-1"
    assert receipt["output_bytes"] == len(raw)
    assert receipt["output_sha256"] == event["output_sha256"]
    assert list((store.path / "producer_observation" / "receipts").glob("*.json"))


def test_codex_em_submission_has_host_observation(tmp_path):
    event, raw = _fixture_rows()[1]
    receipt, _, _, _ = _observe(tmp_path, event, raw)

    assert validate_producer_observation(receipt) == receipt
    assert receipt["stage"] == "em"
    assert receipt["producer"] == "tp-engineering"
    assert receipt["output_schema_id"] == "taskplane.em-output/v1"


def test_severed_host_observation_blocks_submission_without_outage_resolution(
    tmp_path,
):
    event, raw = _fixture_rows()[0]
    source = RecordedHostActionCapabilitySource()
    store = SandboxEvidenceStore(tmp_path, "repository-fingerprint", event["run_id"])

    with pytest.raises(ProducerObservationError, match="host producer event") as caught:
        _observe(
            tmp_path,
            event,
            raw,
            events=RecordedProducerEventSource([]),
            source=source,
            store=store,
        )
    assert "outage" not in str(caught.value).lower()
    assert not list((store.path / "producer_observation" / "receipts").glob("*.json"))

    receipt, _, _, _ = _observe(
        tmp_path,
        event,
        raw,
        events=RecordedProducerEventSource([event]),
        source=source,
        store=store,
    )
    assert receipt["output_sha256"] == event["output_sha256"]


def test_recorded_event_source_replay_is_hermetic_and_deterministic(tmp_path):
    event, raw = _fixture_rows()[0]
    recorded = RecordedProducerEventSource([event])
    event["output_sha256"] = "0" * 64

    first, _, _, _ = _observe(tmp_path / "one", _fixture_rows()[0][0], raw, events=recorded)
    second_event, second_raw = _fixture_rows()[0]
    second, _, _, _ = _observe(tmp_path / "two", second_event, second_raw)

    assert first == second


@pytest.mark.parametrize("field", ["output_bytes", "output_sha256", "output_schema_id"])
def test_mismatched_or_ambiguous_host_event_fails_before_capability_consumption(
    tmp_path, field
):
    event, raw = _fixture_rows()[0]
    altered = dict(event)
    altered[field] = altered[field] + 1 if field == "output_bytes" else "wrong"
    with pytest.raises(ProducerObservationError, match="host producer event"):
        _observe(
            tmp_path,
            event,
            raw,
            events=RecordedProducerEventSource([altered]),
        )

    with pytest.raises(ProducerObservationError, match="ambiguous"):
        _observe(
            tmp_path,
            event,
            raw,
            events=RecordedProducerEventSource([event, event]),
        )


@pytest.mark.parametrize("row", [0, 1], ids=["evaluator", "em"])
def test_loop_submission_consumes_observable_producer_receipt(tmp_path, row):
    event, raw = _fixture_rows()[row]
    receipt, _, _, _ = _observe(tmp_path, event, raw)
    submission = {
        "step": event["stage"],
        "task": event["task_id"],
        "evidence_paths": [event["output_path"]],
    }

    observed = loop.bind_producer_observation(
        submission,
        receipt,
        output_bytes=raw,
        output_schema_id=event["output_schema_id"],
        output_contract_fingerprint=event["output_contract_fingerprint"],
    )

    assert observed["producer_observation"] == receipt
    assert evaluation_output.validate_submission_observation(
        observed,
        output_bytes=raw,
        output_schema_id=event["output_schema_id"],
        output_contract_fingerprint=event["output_contract_fingerprint"],
    ) == receipt


def test_loop_submission_rejects_missing_observation_without_outage_path():
    with pytest.raises(ProducerObservationError, match="producer observation") as caught:
        loop.bind_producer_observation(
            {"step": "evaluate", "task": "task-eval"},
            None,
            output_bytes=b"{}\n",
            output_schema_id="taskplane.evaluator-output/v1",
            output_contract_fingerprint="d" * 64,
        )

    assert "outage" not in str(caught.value).lower()
