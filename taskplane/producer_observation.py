"""Host-observed producer receipts for evaluator and EM submissions."""

from __future__ import annotations

import hashlib
import base64
import json
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from taskplane.delivery_ports import (
    Clock,
    DeliveryPortError,
    EvidenceStore,
    HostActionCapabilitySource,
    ProducerEventSource,
    content_fingerprint,
    LocatorEvidenceStore,
    SystemClock,
)


PRODUCER_OBSERVATION_SCHEMA = "taskplane.producer-observation/v1"
HOST_PRODUCER_EVENT_SCHEMA = "taskplane.host-producer-event/v1"
PRODUCER_CONSUMPTION_SCHEMA = "taskplane.producer-observation-consumption/v1"
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
_DISPATCH_FIELDS = frozenset(
    {"run_id", "task_id", "stage", "producer", "task_name",
     "role_marker", "model", "reasoning_effort", "fingerprint"}
)
_STOP_IDENTITY_FIELDS = frozenset(
    {"session_id", "turn_id", "agent_id", "agent_type", "task_name"}
)
_CONSUMPTION_IDENTITY_FIELDS = frozenset(
    {"host", "host_session_or_turn", "run_id", "task_id", "stage",
     "producer", "output_sha256", "output_contract_fingerprint",
     "source_sha", "observed_at"}
)
_CONSUMPTION_FIELDS = frozenset(
    {"schema", "receipt_fingerprint", "observation_identity",
     "evidence_receipt_fingerprint", "evidence_predecessor_fingerprint",
     "evidence_state_fingerprint", "store_namespace_fingerprint",
     "consumed_at", "fingerprint"}
)
_EVIDENCE_RECEIPT_FIELDS = frozenset(
    {"domain", "operation_id", "predecessor_fingerprint", "payload",
     "payload_fingerprint", "prepare_token", "fingerprint"}
)


class ProducerObservationError(ValueError):
    """A submission lacks one exact, fresh host observation."""


class _NativeEventSource:
    """One host-owned event, constructed inside the lifecycle adapter."""

    def __init__(self, event: Mapping[str, Any]) -> None:
        self._event = dict(event)

    def events(self, *, host_session_id: str,
               host_turn_id: str) -> tuple[dict[str, Any], ...]:
        if self._event.get("host_session_id") != host_session_id or \
                self._event.get("host_turn_id") != host_turn_id:
            return ()
        return (dict(self._event),)


class _OneUseNativeCapability:
    """Process-private exact-bound capability for one claimed hook event."""

    def __init__(self, handle: str, bindings: Mapping[str, Any], *,
                 issued_at: float, expires_at: float) -> None:
        self._handle = handle
        self._bindings = dict(bindings)
        self._issued_at = float(issued_at)
        self._expires_at = float(expires_at)
        self._consumed = False

    def consume(self, handle: str, *, expected_bindings: Mapping[str, Any],
                now: float) -> dict[str, Any]:
        if self._consumed:
            raise DeliveryPortError("host capability replay")
        if handle != self._handle:
            raise DeliveryPortError("missing host-private capability handle")
        if not self._issued_at <= float(now) < self._expires_at:
            raise DeliveryPortError("host capability expired or not yet valid")
        if dict(expected_bindings) != self._bindings:
            raise DeliveryPortError("host capability binding mismatch")
        self._consumed = True
        return {**self._bindings, "cryptographic_authenticity_claimed": False}


def _production_store(evidence_root: str, workspace: str,
                      run_id: str) -> LocatorEvidenceStore:
    root = Path(evidence_root)
    root.mkdir(parents=True, exist_ok=True)
    repository = hashlib.sha256(
        os.path.realpath(workspace).encode("utf-8")).hexdigest()
    namespace = hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()
    return LocatorEvidenceStore(root, repository, namespace)


def exact_output_bundle(paths: list[tuple[str, bytes]]) -> bytes:
    """Frame one or more exact output files without JSON/text re-encoding."""
    framed = bytearray()
    for path, raw in paths:
        if not isinstance(path, str) or not path or not isinstance(raw, bytes):
            raise ProducerObservationError("output bundle entries are invalid")
        name = path.encode("utf-8")
        framed.extend(len(name).to_bytes(8, "big"))
        framed.extend(name)
        framed.extend(len(raw).to_bytes(8, "big"))
        framed.extend(raw)
    return bytes(framed)


def validate_producer_dispatch(
        dispatch: Mapping[str, Any], *, run_id: str, task_id: str,
        stage: str, producer: str) -> dict[str, Any]:
    """Validate the exact engine-emitted native producer dispatch."""
    if not isinstance(dispatch, Mapping) or set(dispatch) != _DISPATCH_FIELDS:
        raise ProducerObservationError(
            "external host producer receipt cannot be matched: producer "
            "dispatch identity is missing or not closed")
    expected = {"run_id": run_id, "task_id": task_id, "stage": stage,
                "producer": producer}
    if any(dispatch.get(key) != value for key, value in expected.items()):
        raise ProducerObservationError("producer dispatch identity mismatched")
    for field in ("run_id", "task_id", "producer", "task_name",
                  "role_marker"):
        _text(dispatch.get(field), field)
    for field in ("model", "reasoning_effort"):
        if dispatch.get(field) is not None:
            _text(dispatch.get(field), field)
    projection = {key: dispatch[key]
                  for key in _DISPATCH_FIELDS - {"fingerprint"}}
    if dispatch.get("fingerprint") != content_fingerprint(projection):
        raise ProducerObservationError("producer dispatch fingerprint mismatch")
    return dict(dispatch)


def _stopping_identity(
        event: Mapping[str, Any], dispatch: Mapping[str, Any], *,
        session_id: str, turn_id: str) -> dict[str, str]:
    """Bind host-owned child identity to the exact emitted task name."""
    identity = {
        "session_id": session_id,
        "turn_id": turn_id,
        "agent_id": str(event.get("agent_id") or "").strip(),
        "agent_type": str(event.get("agent_type") or "").strip(),
        "task_name": str(event.get("task_name") or "").strip(),
    }
    if any(not value for value in identity.values()):
        raise ProducerObservationError(
            "Codex SubagentStop stopping-agent identity is required")
    expected_name = str(dispatch["task_name"])
    if identity["agent_type"] != expected_name or \
            identity["task_name"] != expected_name:
        raise ProducerObservationError(
            "Codex SubagentStop stopping agent does not match emitted "
            "producer dispatch")
    return identity


def _decode_stopping_identity(
        value: Any, dispatch: Mapping[str, Any]) -> dict[str, str]:
    try:
        identity = json.loads(_text(value, "host_session_or_turn"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProducerObservationError(
            "producer observation stopping-agent identity is invalid") from exc
    if not isinstance(identity, dict) or set(identity) != _STOP_IDENTITY_FIELDS:
        raise ProducerObservationError(
            "producer observation stopping-agent identity is not closed")
    for field in _STOP_IDENTITY_FIELDS:
        _text(identity.get(field), field)
    expected_name = str(dispatch["task_name"])
    if identity["agent_type"] != expected_name or \
            identity["task_name"] != expected_name:
        raise ProducerObservationError(
            "producer observation stopping agent mismatched")
    return identity


def _freshness(observed_at: Any, now: float) -> None:
    observed = _number(observed_at, "observed_at")
    if observed > now or now - observed > MAX_EVENT_AGE_SECONDS:
        raise ProducerObservationError("stale host producer observation")


def _load_stored_observation(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read one immutable receipt envelope and its schema-valid payload."""
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or \
                set(envelope) != _EVIDENCE_RECEIPT_FIELDS:
            raise ValueError("receipt envelope is not closed")
        projection = {key: envelope[key]
                      for key in _EVIDENCE_RECEIPT_FIELDS - {"fingerprint"}}
        if envelope.get("domain") != "producer_observation" or \
                envelope.get("fingerprint") != content_fingerprint(projection) or \
                path.name != f"{envelope['fingerprint']}.json":
            raise ValueError("receipt envelope fingerprint mismatch")
        raw = base64.b64decode(envelope["payload"], validate=True)
        if envelope.get("payload_fingerprint") != content_fingerprint(raw):
            raise ValueError("receipt payload fingerprint mismatch")
        payload = json.loads(raw.decode("utf-8"))
        receipt = validate_producer_observation(payload)
    except Exception as exc:
        raise ProducerObservationError(
            "producer observation store is corrupt") from exc
    return receipt, envelope


def _observation_identity(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {key: receipt[key] for key in _CONSUMPTION_IDENTITY_FIELDS}


def _validate_consumption_marker(
        marker: Mapping[str, Any], *, receipt: Mapping[str, Any],
        evidence_receipt: Mapping[str, Any], state_fingerprint: str,
        store_namespace_fingerprint: str, now: float) -> dict[str, Any]:
    if not isinstance(marker, Mapping) or set(marker) != _CONSUMPTION_FIELDS:
        raise ProducerObservationError(
            "producer observation consumption marker is not closed")
    if marker.get("schema") != PRODUCER_CONSUMPTION_SCHEMA:
        raise ProducerObservationError(
            "producer observation consumption marker schema is invalid")
    identity = marker.get("observation_identity")
    if not isinstance(identity, Mapping) or \
            set(identity) != _CONSUMPTION_IDENTITY_FIELDS or \
            dict(identity) != _observation_identity(receipt):
        raise ProducerObservationError(
            "producer observation consumption identity mismatched")
    expected = {
        "receipt_fingerprint": receipt["fingerprint"],
        "evidence_receipt_fingerprint": evidence_receipt["fingerprint"],
        "evidence_predecessor_fingerprint":
            evidence_receipt.get("predecessor_fingerprint"),
        "evidence_state_fingerprint": state_fingerprint,
        "store_namespace_fingerprint": store_namespace_fingerprint,
    }
    if any(marker.get(key) != value for key, value in expected.items()):
        raise ProducerObservationError(
            "producer observation consumption marker mismatched")
    consumed_at = _number(marker.get("consumed_at"), "consumed_at")
    if consumed_at < float(receipt["observed_at"]) or consumed_at > now:
        raise ProducerObservationError(
            "producer observation consumption time is invalid")
    projection = {key: marker[key]
                  for key in _CONSUMPTION_FIELDS - {"fingerprint"}}
    if marker.get("fingerprint") != content_fingerprint(projection):
        raise ProducerObservationError(
            "producer observation consumption marker fingerprint mismatch")
    return dict(marker)


def record_codex_subagent_stop(
    *, workspace: str, evidence_root: str, event: Mapping[str, Any],
    hook_claim_id: str, run_id: str, task_id: str, stage: str,
    producer: str, output_path: str, output_bytes: bytes,
    output_schema_id: str, output_contract_fingerprint: str,
    source_sha: str, producer_dispatch: Mapping[str, Any],
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Mint one production receipt from an already-claimed Codex stop.

    Caller text and transcripts are deliberately absent.  The adapter binds
    only native lifecycle identity and bytes read from the engine-selected
    result path.  Native/bridge duplicates share ``hook_claim_id`` and the
    outer hook claim journal executes this function only once.
    """
    if not isinstance(event, Mapping) or \
            event.get("hook_event_name") != "SubagentStop":
        raise ProducerObservationError("Codex SubagentStop event is required")
    session_id = str(event.get("session_id") or event.get("thread_id") or
                     os.environ.get("CODEX_THREAD_ID") or "").strip()
    turn_id = str(event.get("turn_id") or "").strip()
    if not session_id or not turn_id:
        raise ProducerObservationError(
            "Codex SubagentStop session and turn identity are required")
    dispatch = validate_producer_dispatch(
        producer_dispatch, run_id=run_id, task_id=task_id, stage=stage,
        producer=producer)
    stopping_identity = _stopping_identity(
        event, dispatch, session_id=session_id, turn_id=turn_id)
    if not isinstance(hook_claim_id, str) or len(hook_claim_id) != 64 or \
            any(ch not in "0123456789abcdef" for ch in hook_claim_id):
        raise ProducerObservationError("stable hook claim identity is required")
    try:
        from taskplane import taskplane_lite as host_policy
        hook_identity = host_policy.hook_event_identity(
            workspace, "subagent-stop", dict(event))
    except Exception as exc:
        raise ProducerObservationError(
            "Codex SubagentStop hook identity cannot be verified") from exc
    expected_claim_id = hashlib.sha256(hook_identity.encode("utf-8")).hexdigest() \
        if hook_identity else ""
    if hook_claim_id != expected_claim_id:
        raise ProducerObservationError(
            "Codex SubagentStop hook claim does not match stopping agent")
    active_clock = clock or SystemClock()
    now = float(active_clock.wall_time())
    output_digest = hashlib.sha256(output_bytes).hexdigest()
    host_event = {
        "schema": HOST_PRODUCER_EVENT_SCHEMA,
        "event_id": hook_claim_id,
        "host": "codex",
        "host_session_id": session_id,
        "host_turn_id": turn_id,
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
        "observed_at": now,
    }
    bindings = {
        "purpose": "producer_observation",
        "host_session_id": session_id,
        "host_turn_id": turn_id,
        "run_id": run_id,
        "kernel_id": None,
        "task_id": task_id,
        "stage": stage,
        "request_or_output_digest": output_digest,
        "contract_fingerprint": output_contract_fingerprint,
    }
    handle = "host-private:" + content_fingerprint({
        "claim": hook_claim_id, "bindings": bindings})
    capability = _OneUseNativeCapability(
        handle, bindings, issued_at=now - 1.0, expires_at=now + 60.0)
    return observe_submission(
        run_id=run_id, task_id=task_id, stage=stage, producer=producer,
        host="codex", host_session_id=session_id, host_turn_id=turn_id,
        output_path=output_path, output_bytes=output_bytes,
        output_schema_id=output_schema_id,
        output_contract_fingerprint=output_contract_fingerprint,
        source_sha=source_sha, capability_handle=handle,
        event_source=_NativeEventSource(host_event),
        capability_source=capability,
        evidence_store=_production_store(
            evidence_root, workspace, run_id), clock=active_clock,
        host_producer_identity=json.dumps(
            stopping_identity, sort_keys=True, separators=(",", ":")))


def consume_matching_observation(
    *, workspace: str, evidence_root: str, run_id: str, task_id: str,
    stage: str, producer: str, output_path: str, output_bytes: bytes,
    output_schema_id: str, output_contract_fingerprint: str,
    source_sha: str, producer_dispatch: Mapping[str, Any],
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Locate, validate, and durably consume exactly one fresh receipt."""
    store = _production_store(evidence_root, workspace, run_id)
    receipt_dir = store.path / "producer_observation" / "receipts"
    dispatch = validate_producer_dispatch(
        producer_dispatch, run_id=run_id, task_id=task_id, stage=stage,
        producer=producer)
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for path in sorted(receipt_dir.glob("*.json")) if receipt_dir.exists() else ():
        receipt, envelope = _load_stored_observation(path)
        if receipt["run_id"] == run_id and receipt["task_id"] == task_id \
                and receipt["stage"] == stage:
            rows.append((receipt, envelope))
    if not rows:
        raise ProducerObservationError("missing host producer observation")
    if len(rows) != 1:
        raise ProducerObservationError("ambiguous host producer observations")
    receipt, evidence_receipt = rows[0]
    now = float((clock or SystemClock()).wall_time())
    _freshness(receipt["observed_at"], now)
    _decode_stopping_identity(receipt["host_session_or_turn"], dispatch)
    expected = {
        "producer": producer, "host": "codex", "output_path": output_path,
        "output_bytes": len(output_bytes),
        "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "output_schema_id": output_schema_id,
        "output_contract_fingerprint": output_contract_fingerprint,
        "source_sha": source_sha,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ProducerObservationError("mismatched host producer observation")
    consumed = store.path / "producer_observation" / "consumed"
    consumed.mkdir(parents=True, exist_ok=True)
    marker = consumed / f"{receipt['fingerprint']}.json"
    state_path = store.path / "producer_observation" / "STATE"
    try:
        state_fingerprint = state_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProducerObservationError(
            "producer observation durable state is missing") from exc
    if state_fingerprint != evidence_receipt["fingerprint"]:
        raise ProducerObservationError(
            "producer observation durable state mismatched")
    marker_projection = {
        "schema": PRODUCER_CONSUMPTION_SCHEMA,
        "receipt_fingerprint": receipt["fingerprint"],
        "observation_identity": _observation_identity(receipt),
        "evidence_receipt_fingerprint": evidence_receipt["fingerprint"],
        "evidence_predecessor_fingerprint":
            evidence_receipt.get("predecessor_fingerprint"),
        "evidence_state_fingerprint": state_fingerprint,
        "store_namespace_fingerprint": store.namespace_token,
        "consumed_at": now,
    }
    marker_value = {**marker_projection,
                    "fingerprint": content_fingerprint(marker_projection)}
    marker_bytes = (json.dumps(marker_value, sort_keys=True,
                               separators=(",", ":")) + "\n").encode("utf-8")
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ProducerObservationError("producer observation replay") from exc
    try:
        view = memoryview(marker_bytes)
        while view:
            view = view[os.write(descriptor, view):]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt


def validate_consumed_matching_observation(
    receipt: Mapping[str, Any], *, workspace: str, evidence_root: str,
    run_id: str, task_id: str, stage: str, producer: str,
    output_path: str, output_bytes: bytes, output_schema_id: str,
    output_contract_fingerprint: str, source_sha: str,
    producer_dispatch: Mapping[str, Any], clock: Clock | None = None,
) -> dict[str, Any]:
    """Re-attest that a submission names the exact consumed native receipt."""
    checked = validate_producer_observation(receipt)
    dispatch = validate_producer_dispatch(
        producer_dispatch, run_id=run_id, task_id=task_id, stage=stage,
        producer=producer)
    _decode_stopping_identity(checked["host_session_or_turn"], dispatch)
    now = float((clock or SystemClock()).wall_time())
    _freshness(checked["observed_at"], now)
    expected = {
        "run_id": run_id, "task_id": task_id, "stage": stage,
        "producer": producer, "host": "codex", "output_path": output_path,
        "output_bytes": len(output_bytes),
        "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "output_schema_id": output_schema_id,
        "output_contract_fingerprint": output_contract_fingerprint,
        "source_sha": source_sha,
    }
    if any(checked.get(key) != value for key, value in expected.items()):
        raise ProducerObservationError("mismatched host producer observation")
    store = _production_store(evidence_root, workspace, run_id)
    receipt_dir = store.path / "producer_observation" / "receipts"
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(receipt_dir.glob("*.json")) if receipt_dir.exists() else ():
        payload, envelope = _load_stored_observation(path)
        if payload == checked:
            matches.append((path, envelope))
    if len(matches) != 1:
        raise ProducerObservationError(
            "consumed producer observation is missing or ambiguous")
    evidence_receipt = matches[0][1]
    state_path = store.path / "producer_observation" / "STATE"
    head_path = store.path / "producer_observation" / "HEAD"
    try:
        state_fingerprint = state_path.read_text(encoding="utf-8").strip()
        head_fingerprint = head_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProducerObservationError(
            "producer observation durable state is missing") from exc
    if state_fingerprint != evidence_receipt["fingerprint"] or \
            head_fingerprint != state_fingerprint:
        raise ProducerObservationError(
            "producer observation durable state mismatched")
    marker = (store.path / "producer_observation" / "consumed" /
              f"{checked['fingerprint']}.json")
    if not marker.is_file():
        raise ProducerObservationError(
            "producer observation was not consumed")
    try:
        marker_value = json.loads(marker.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProducerObservationError(
            "producer observation consumption marker is corrupt") from exc
    _validate_consumption_marker(
        marker_value, receipt=checked, evidence_receipt=evidence_receipt,
        state_fingerprint=state_fingerprint,
        store_namespace_fingerprint=store.namespace_token, now=now)
    return checked


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
    host_producer_identity: str | None = None,
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
    projection = {
        "schema": PRODUCER_OBSERVATION_SCHEMA,
        "run_id": values["run_id"],
        "task_id": values["task_id"],
        "stage": stage,
        "producer": values["producer"],
        "host": host,
        "host_session_or_turn": (host_producer_identity or
                                 f"{host_session_id}:{host_turn_id}"),
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
    # The exact, claim-bound reconciliation input must be durable before the
    # process-private capability is consumed.  A failed prepare therefore
    # leaves authority reusable; a later commit failure leaves an immutable
    # intent that EvidenceStore.reconcile can finish after restart.
    try:
        capability_source.consume(
            capability_handle,
            expected_bindings=expected_capability,
            now=now,
        )
    except DeliveryPortError as exc:
        raise ProducerObservationError(str(exc)) from exc
    evidence_store.commit(prepared)
    return receipt
