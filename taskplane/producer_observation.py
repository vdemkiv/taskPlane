"""Host-observed producer receipts for evaluator and EM submissions."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

from taskplane.delivery_ports import (
    Clock,
    DeliveryPortError,
    EvidenceStore,
    HostActionCapabilitySource,
    ProducerEventSource,
    content_fingerprint,
)


PRODUCER_OBSERVATION_SCHEMA = "taskplane.producer-observation/v1"
HOST_PRODUCER_EVENT_SCHEMA = "taskplane.host-producer-event/v1"
MAX_EVENT_AGE_SECONDS = 300.0

_OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "run_id",
        "task_id",
        "stage",
        "producer",
        "host",
        "host_session_or_turn",
        "output_path",
        "output_bytes",
        "output_sha256",
        "output_schema_id",
        "output_contract_fingerprint",
        "source_sha",
        "observed_at",
        "fingerprint",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "schema",
        "event_id",
        "host",
        "host_session_id",
        "host_turn_id",
        "run_id",
        "task_id",
        "stage",
        "producer",
        "output_path",
        "output_bytes",
        "output_sha256",
        "output_schema_id",
        "output_contract_fingerprint",
        "source_sha",
        "observed_at",
    }
)


class ProducerObservationError(ValueError):
    """A submission lacks one exact, fresh host observation."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProducerObservationError(f"{field} is required")
    return value


def _fingerprint(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ProducerObservationError(f"{field} must be a lowercase SHA-256 fingerprint")
    return text


def _source_sha(value: Any) -> str:
    text = _text(value, "source_sha")
    if len(text) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ProducerObservationError("source_sha must be an exact lowercase Git SHA")
    return text


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProducerObservationError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ProducerObservationError(f"{field} must be a finite number")
    return result


def validate_producer_observation(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the strict checked-in v1 producer-observation schema."""
    if not isinstance(receipt, Mapping):
        raise ProducerObservationError("producer observation must be a mapping")
    if set(receipt) != _OBSERVATION_FIELDS:
        raise ProducerObservationError("producer observation fields are not closed")
    if receipt.get("schema") != PRODUCER_OBSERVATION_SCHEMA:
        raise ProducerObservationError("producer observation schema is invalid")
    for field in (
        "run_id",
        "task_id",
        "producer",
        "host_session_or_turn",
        "output_path",
        "output_schema_id",
    ):
        _text(receipt.get(field), field)
    if receipt.get("stage") not in {"evaluate", "em"}:
        raise ProducerObservationError("stage must be evaluate or em")
    if receipt.get("host") not in {"codex", "claude"}:
        raise ProducerObservationError("host must be codex or claude")
    output_size = receipt.get("output_bytes")
    if isinstance(output_size, bool) or not isinstance(output_size, int) or output_size < 0:
        raise ProducerObservationError("output_bytes must be a non-negative integer")
    _fingerprint(receipt.get("output_sha256"), "output_sha256")
    _fingerprint(
        receipt.get("output_contract_fingerprint"),
        "output_contract_fingerprint",
    )
    _source_sha(receipt.get("source_sha"))
    _number(receipt.get("observed_at"), "observed_at")
    projection = {key: receipt[key] for key in _OBSERVATION_FIELDS - {"fingerprint"}}
    if receipt.get("fingerprint") != content_fingerprint(projection):
        raise ProducerObservationError("producer observation fingerprint mismatch")
    return dict(receipt)


def observe_submission(
    *,
    run_id: str,
    task_id: str,
    stage: str,
    producer: str,
    host: str,
    host_session_id: str,
    host_turn_id: str,
    output_path: str,
    output_bytes: bytes,
    output_schema_id: str,
    output_contract_fingerprint: str,
    source_sha: str,
    capability_handle: str,
    event_source: ProducerEventSource,
    capability_source: HostActionCapabilitySource,
    evidence_store: EvidenceStore,
    clock: Clock,
    predecessor_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Bind one exact host event and capability to immutable output identity."""
    values = {
        "run_id": _text(run_id, "run_id"),
        "task_id": _text(task_id, "task_id"),
        "stage": stage,
        "producer": _text(producer, "producer"),
        "host": host,
        "host_session_id": _text(host_session_id, "host_session_id"),
        "host_turn_id": _text(host_turn_id, "host_turn_id"),
        "output_path": _text(output_path, "output_path"),
        "output_schema_id": _text(output_schema_id, "output_schema_id"),
        "output_contract_fingerprint": _fingerprint(
            output_contract_fingerprint, "output_contract_fingerprint"
        ),
        "source_sha": _source_sha(source_sha),
    }
    if stage not in {"evaluate", "em"}:
        raise ProducerObservationError("stage must be evaluate or em")
    if host not in {"codex", "claude"}:
        raise ProducerObservationError("host must be codex or claude")
    if not isinstance(output_bytes, bytes):
        raise ProducerObservationError("output_bytes must be exact bytes")
    output_digest = hashlib.sha256(output_bytes).hexdigest()
    expected_event = {
        "schema": HOST_PRODUCER_EVENT_SCHEMA,
        "host": host,
        "host_session_id": host_session_id,
        "host_turn_id": host_turn_id,
        "run_id": run_id,
        "task_id": task_id,
        "stage": stage,
        "producer": producer,
        "output_path": output_path,
        "output_bytes": len(output_bytes),
        "output_sha256": output_digest,
        "output_schema_id": output_schema_id,
        "output_contract_fingerprint": output_contract_fingerprint,
        "source_sha": source_sha,
    }
    try:
        events = tuple(
            event_source.events(
                host_session_id=host_session_id, host_turn_id=host_turn_id
            )
        )
    except Exception as exc:
        raise ProducerObservationError("host producer event source failed") from exc
    if len(events) > 1:
        raise ProducerObservationError("ambiguous host producer events")
    if len(events) != 1 or not isinstance(events[0], Mapping):
        raise ProducerObservationError("missing host producer event")
    event = dict(events[0])
    if set(event) != _EVENT_FIELDS or any(
        event.get(field) != expected for field, expected in expected_event.items()
    ):
        raise ProducerObservationError("mismatched host producer event")
    _text(event.get("event_id"), "event_id")
    observed_at = _number(event.get("observed_at"), "observed_at")
    now = _number(clock.wall_time(), "clock.wall_time")
    if observed_at > now or now - observed_at > MAX_EVENT_AGE_SECONDS:
        raise ProducerObservationError("stale host producer event")

    expected_capability = {
        "purpose": "producer_observation",
        "host_session_id": host_session_id,
        "host_turn_id": host_turn_id,
        "run_id": run_id,
        "kernel_id": None,
        "task_id": task_id,
        "stage": stage,
        "request_or_output_digest": output_digest,
        "contract_fingerprint": output_contract_fingerprint,
    }
    try:
        capability_source.consume(
            capability_handle,
            expected_bindings=expected_capability,
            now=now,
        )
    except DeliveryPortError as exc:
        raise ProducerObservationError(str(exc)) from exc

    projection = {
        "schema": PRODUCER_OBSERVATION_SCHEMA,
        "run_id": values["run_id"],
        "task_id": values["task_id"],
        "stage": stage,
        "producer": values["producer"],
        "host": host,
        "host_session_or_turn": f"{host_session_id}:{host_turn_id}",
        "output_path": values["output_path"],
        "output_bytes": len(output_bytes),
        "output_sha256": output_digest,
        "output_schema_id": values["output_schema_id"],
        "output_contract_fingerprint": values["output_contract_fingerprint"],
        "source_sha": values["source_sha"],
        "observed_at": observed_at,
    }
    receipt = {**projection, "fingerprint": content_fingerprint(projection)}
    validate_producer_observation(receipt)
    prepared = evidence_store.prepare(
        "producer_observation",
        f"{run_id}:{task_id}:{stage}:{event['event_id']}",
        receipt,
        expected_head=predecessor_fingerprint,
    )
    evidence_store.commit(prepared)
    return receipt
