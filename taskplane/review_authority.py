"""Human authority and append-only persistence for ReviewKernel rebinding."""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from taskplane.delivery_ports import (
    Clock,
    DeliveryPortError,
    EvidenceStore,
    HostActionCapabilitySource,
    PreparedEvidence,
    canonical_json,
    content_fingerprint,
)


REVIEW_KERNEL_OVERRIDE_SCHEMA = "taskplane.review-kernel-override/v1"
_LIFECYCLE_EVENTS = (
    "slot_starts",
    "producer_assignments",
    "write_observations",
    "collection_reservations",
)


class ReviewAuthorityError(ValueError):
    """A review rebind request violates authority or lifecycle invariants."""


@dataclass
class _Pending:
    prepared: PreparedEvidence
    receipt: dict[str, Any]


@dataclass
class _StoreState:
    receipts: list[dict[str, Any]] = field(default_factory=list)
    envelope_heads: list[str] = field(default_factory=list)
    pending: _Pending | None = None


_STATE_GUARD = threading.Lock()
_STATES: dict[tuple[Any, ...], _StoreState] = {}
_LOCKS: dict[tuple[Any, ...], threading.RLock] = {}


def _store_key(store: EvidenceStore) -> tuple[Any, ...]:
    identity = tuple(
        getattr(store, field_name, None)
        for field_name in ("caller_root", "repository_fingerprint", "run_namespace")
    )
    if all(value is not None for value in identity):
        return ("evidence-store", *(str(value) for value in identity))
    return ("evidence-store-object", id(store))


def _state_and_lock(store: EvidenceStore) -> tuple[_StoreState, threading.RLock]:
    key = _store_key(store)
    with _STATE_GUARD:
        return _STATES.setdefault(key, _StoreState()), _LOCKS.setdefault(
            key, threading.RLock()
        )


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewAuthorityError(f"{field_name} is required")
    return value.strip()


def _fingerprint(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    text = _required_text(value, field_name)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ReviewAuthorityError(
            f"{field_name} must be a lowercase SHA-256 fingerprint"
        )
    return text


def _binding(value: Any, field_name: str) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping) or not value:
        raise ReviewAuthorityError(f"{field_name} must be a non-empty mapping")
    projection = dict(value)
    try:
        fingerprint = content_fingerprint(projection)
    except (TypeError, ValueError) as exc:
        raise ReviewAuthorityError(f"{field_name} must be canonical JSON") from exc
    return projection, fingerprint


def _event_values(value: Any, field_name: str) -> list[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ReviewAuthorityError(f"{field_name} must be a collection")
    projection = list(value)
    try:
        canonical_json(projection)
    except (TypeError, ValueError) as exc:
        raise ReviewAuthorityError(
            f"{field_name} must contain canonical JSON values"
        ) from exc
    return projection


def project_kernel_lifecycle(
    *,
    slot_starts: Sequence[Any] = (),
    producer_assignments: Sequence[Any] = (),
    write_observations: Sequence[Any] = (),
    collection_reservations: Sequence[Any] = (),
    revision: Any = None,
) -> dict[str, Any]:
    """Create the durable zero-start projection used by the authority gate."""
    projection: dict[str, Any] = {
        name: _event_values(value, name)
        for name, value in (
            ("slot_starts", slot_starts),
            ("producer_assignments", producer_assignments),
            ("write_observations", write_observations),
            ("collection_reservations", collection_reservations),
        )
    }
    try:
        canonical_json(revision)
    except (TypeError, ValueError) as exc:
        raise ReviewAuthorityError("revision must be a canonical JSON value") from exc
    projection["revision"] = revision
    projection["unstarted"] = (
        not any(projection[name] for name in _LIFECYCLE_EVENTS)
        and revision is None
    )
    projection["fingerprint"] = content_fingerprint(projection)
    return projection


def _lifecycle(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewAuthorityError("lifecycle must be a mapping")
    raw_fields = set(_LIFECYCLE_EVENTS) | {"revision"}
    projected_fields = raw_fields | {"unstarted", "fingerprint"}
    if set(value) not in {frozenset(raw_fields), frozenset(projected_fields)}:
        raise ReviewAuthorityError("lifecycle fields are not closed")
    normalized = project_kernel_lifecycle(
        **{name: value[name] for name in _LIFECYCLE_EVENTS},
        revision=value["revision"],
    )
    if set(value) == projected_fields and (
        value.get("unstarted") != normalized["unstarted"]
        or value.get("fingerprint") != normalized["fingerprint"]
    ):
        raise ReviewAuthorityError("lifecycle projection fingerprint mismatch")
    return normalized


def _human_actor(value: Any) -> str:
    actor = _required_text(value, "human actor")
    if not actor.startswith("human:") or not actor.removeprefix("human:").strip():
        raise ReviewAuthorityError("human actor must be attributed as human:<identity>")
    return actor


def _sequence(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewAuthorityError("host_sequence must be a non-negative integer")
    return value


def _request_projection(
    *,
    run_id: Any,
    kernel_id: Any,
    stage: Any,
    prior_binding: Any,
    replacement_binding: Any,
    lifecycle: Any,
    human_actor: Any,
    reason: Any,
    host_session_id: Any,
    host_turn_id: Any,
    host_sequence: Any,
    contract_fingerprint: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _, prior_fingerprint = _binding(prior_binding, "prior_binding")
    _, replacement_fingerprint = _binding(replacement_binding, "replacement_binding")
    if prior_fingerprint == replacement_fingerprint:
        raise ReviewAuthorityError("replacement binding must differ from prior binding")
    lifecycle_projection = _lifecycle(lifecycle)
    normalized = {
        "purpose": "review_rebind",
        "run_id": _required_text(run_id, "run_id"),
        "kernel_id": _required_text(kernel_id, "kernel_id"),
        "task_id": None,
        "stage": _required_text(stage, "stage"),
        "prior_binding_fingerprint": prior_fingerprint,
        "replacement_binding_fingerprint": replacement_fingerprint,
        "zero_start_fingerprint": lifecycle_projection["fingerprint"],
        "human_actor": _human_actor(human_actor),
        "reason": _required_text(reason, "reason"),
        "host_session_id": _required_text(host_session_id, "host_session_id"),
        "host_turn_id": _required_text(host_turn_id, "host_turn_id"),
        "sequence": _sequence(host_sequence),
        "contract_fingerprint": _fingerprint(
            contract_fingerprint, "contract_fingerprint"
        ),
    }
    return normalized, lifecycle_projection


def rebind_request_digest(**request: Any) -> str:
    """Fingerprint the exact request that a host capability must bind."""
    normalized, _ = _request_projection(**request)
    return content_fingerprint(normalized)


def validate_override_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the closed v1 override receipt and its content fingerprint."""
    fields = {
        "schema",
        "prior_binding_fingerprint",
        "replacement_binding_fingerprint",
        "zero_start_evidence",
        "human_authority_receipt",
        "reason",
        "recorded_at",
        "predecessor_digest",
        "fingerprint",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != fields:
        raise ReviewAuthorityError("review-kernel override receipt fields are not closed")
    if receipt.get("schema") != REVIEW_KERNEL_OVERRIDE_SCHEMA:
        raise ReviewAuthorityError("review-kernel override receipt schema is invalid")
    _fingerprint(receipt.get("prior_binding_fingerprint"), "prior_binding_fingerprint")
    _fingerprint(
        receipt.get("replacement_binding_fingerprint"),
        "replacement_binding_fingerprint",
    )
    zero_start = _lifecycle(receipt.get("zero_start_evidence"))
    if not zero_start["unstarted"]:
        raise ReviewAuthorityError("review kernel is immutable after any durable start signal")
    authority = receipt.get("human_authority_receipt")
    authority_fields = {
        "actor", "authority_kind", "capability_id", "capability_nonce",
        "host_session_id", "host_turn_id", "sequence",
        "channel_continuity_observed", "cryptographic_authenticity_claimed",
    }
    if not isinstance(authority, Mapping) or set(authority) != authority_fields:
        raise ReviewAuthorityError("human authority receipt fields are not closed")
    _human_actor(authority.get("actor"))
    if authority.get("authority_kind") != "attributed-human":
        raise ReviewAuthorityError("human authority kind is invalid")
    _required_text(authority.get("capability_id"), "capability_id")
    _required_text(authority.get("capability_nonce"), "capability_nonce")
    _required_text(authority.get("host_session_id"), "host_session_id")
    _required_text(authority.get("host_turn_id"), "host_turn_id")
    _sequence(authority.get("sequence"))
    if authority.get("channel_continuity_observed") is not True:
        raise ReviewAuthorityError("host channel continuity was not observed")
    if authority.get("cryptographic_authenticity_claimed") is not False:
        raise ReviewAuthorityError("actor authenticity must not be claimed")
    _required_text(receipt.get("reason"), "reason")
    recorded_at = receipt.get("recorded_at")
    if isinstance(recorded_at, bool) or not isinstance(recorded_at, (int, float)):
        raise ReviewAuthorityError("recorded_at must be a number")
    _fingerprint(receipt.get("predecessor_digest"), "predecessor_digest", optional=True)
    projection = {key: receipt[key] for key in fields - {"fingerprint"}}
    if receipt.get("fingerprint") != content_fingerprint(projection):
        raise ReviewAuthorityError("review-kernel override receipt fingerprint mismatch")
    return dict(receipt)


def _decode_envelope(raw: bytes) -> tuple[dict[str, Any], str]:
    try:
        envelope = json.loads(raw)
        payload = base64.b64decode(envelope["payload"], validate=True)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReviewAuthorityError("review rebind evidence envelope is invalid") from exc
    if content_fingerprint(payload) != envelope.get("payload_fingerprint"):
        raise ReviewAuthorityError("review rebind evidence payload fingerprint mismatch")
    envelope_projection = {
        key: value for key, value in envelope.items() if key != "fingerprint"
    }
    if content_fingerprint(envelope_projection) != envelope.get("fingerprint"):
        raise ReviewAuthorityError("review rebind evidence envelope fingerprint mismatch")
    try:
        receipt = validate_override_receipt(json.loads(payload))
    except json.JSONDecodeError as exc:
        raise ReviewAuthorityError("review rebind receipt JSON is invalid") from exc
    return receipt, envelope["fingerprint"]


def _append(state: _StoreState, receipt: dict[str, Any], envelope_head: str) -> None:
    expected = state.receipts[-1]["fingerprint"] if state.receipts else None
    if receipt["predecessor_digest"] != expected:
        raise ReviewAuthorityError("review rebind receipt chain has a fork or gap")
    if state.receipts and receipt["prior_binding_fingerprint"] != (
        state.receipts[-1]["replacement_binding_fingerprint"]
    ):
        raise ReviewAuthorityError("review rebind receipt changes the wrong prior binding")
    if any(row["fingerprint"] == receipt["fingerprint"] for row in state.receipts):
        return
    state.receipts.append(receipt)
    state.envelope_heads.append(envelope_head)


def _recover_locked(store: EvidenceStore, state: _StoreState) -> None:
    if state.pending is not None:
        raw = store.commit(state.pending.prepared)
        receipt, envelope_head = _decode_envelope(raw)
        _append(state, receipt, envelope_head)
        state.pending = None
        return
    if state.receipts:
        return
    for raw in store.reconcile("review_rebind"):
        receipt, envelope_head = _decode_envelope(raw)
        _append(state, receipt, envelope_head)


def reconcile(evidence_store: EvidenceStore) -> tuple[dict[str, Any], ...]:
    """Finish an interrupted publication and return the one validated chain."""
    state, lock = _state_and_lock(evidence_store)
    with lock:
        _recover_locked(evidence_store, state)
        return tuple(dict(receipt) for receipt in state.receipts)


def rebind(
    *,
    run_id: str,
    kernel_id: str,
    stage: str,
    prior_binding: Mapping[str, Any],
    replacement_binding: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    human_actor: str,
    reason: str,
    host_session_id: str,
    host_turn_id: str,
    host_sequence: int,
    contract_fingerprint: str,
    capability_handle: str,
    capability_source: HostActionCapabilitySource,
    evidence_store: EvidenceStore,
    clock: Clock,
    predecessor_digest: str | None = None,
) -> dict[str, Any]:
    """Publish one exact-bound human override for a provably unstarted kernel."""
    normalized, lifecycle_projection = _request_projection(
        run_id=run_id,
        kernel_id=kernel_id,
        stage=stage,
        prior_binding=prior_binding,
        replacement_binding=replacement_binding,
        lifecycle=lifecycle,
        human_actor=human_actor,
        reason=reason,
        host_session_id=host_session_id,
        host_turn_id=host_turn_id,
        host_sequence=host_sequence,
        contract_fingerprint=contract_fingerprint,
    )
    if not lifecycle_projection["unstarted"]:
        raise ReviewAuthorityError("review kernel is immutable after any durable start signal")
    expected_predecessor = _fingerprint(
        predecessor_digest, "predecessor_digest", optional=True
    )
    request_digest = content_fingerprint(normalized)
    state, lock = _state_and_lock(evidence_store)
    with lock:
        _recover_locked(evidence_store, state)
        actual_predecessor = state.receipts[-1]["fingerprint"] if state.receipts else None
        if expected_predecessor != actual_predecessor:
            raise ReviewAuthorityError("review rebind predecessor CAS mismatch")
        if state.receipts and normalized["prior_binding_fingerprint"] != (
            state.receipts[-1]["replacement_binding_fingerprint"]
        ):
            raise ReviewAuthorityError("review rebind prior binding is stale")
        expected_capability = {
            "purpose": "review_rebind",
            "sequence": normalized["sequence"],
            "host_session_id": normalized["host_session_id"],
            "host_turn_id": normalized["host_turn_id"],
            "run_id": normalized["run_id"],
            "kernel_id": normalized["kernel_id"],
            "task_id": None,
            "stage": normalized["stage"],
            "request_or_output_digest": request_digest,
            "contract_fingerprint": normalized["contract_fingerprint"],
        }
        try:
            capability = capability_source.consume(
                _required_text(capability_handle, "capability_handle"),
                expected_bindings=expected_capability,
                now=clock.wall_time(),
            )
        except DeliveryPortError as exc:
            raise ReviewAuthorityError(f"host capability refused: {exc}") from exc
        if capability.get("cryptographic_authenticity_claimed") is not False:
            raise ReviewAuthorityError("host capability must not claim actor authenticity")
        receipt: dict[str, Any] = {
            "schema": REVIEW_KERNEL_OVERRIDE_SCHEMA,
            "prior_binding_fingerprint": normalized["prior_binding_fingerprint"],
            "replacement_binding_fingerprint": normalized["replacement_binding_fingerprint"],
            "zero_start_evidence": lifecycle_projection,
            "human_authority_receipt": {
                "actor": normalized["human_actor"],
                "authority_kind": "attributed-human",
                "capability_id": capability["capability_id"],
                "capability_nonce": capability["nonce"],
                "host_session_id": capability["host_session_id"],
                "host_turn_id": capability["host_turn_id"],
                "sequence": capability["sequence"],
                "channel_continuity_observed": True,
                "cryptographic_authenticity_claimed": False,
            },
            "reason": normalized["reason"],
            "recorded_at": clock.wall_time(),
            "predecessor_digest": actual_predecessor,
        }
        receipt["fingerprint"] = content_fingerprint(receipt)
        receipt = validate_override_receipt(receipt)
        prepared = evidence_store.prepare(
            "review_rebind",
            f"{normalized['run_id']}:{normalized['kernel_id']}:{request_digest}",
            receipt,
            expected_head=state.envelope_heads[-1] if state.envelope_heads else None,
        )
        state.pending = _Pending(prepared, receipt)
        raw = evidence_store.commit(prepared)
        committed_receipt, envelope_head = _decode_envelope(raw)
        if committed_receipt != receipt:
            raise ReviewAuthorityError("committed review rebind receipt bytes changed")
        _append(state, committed_receipt, envelope_head)
        state.pending = None
        return dict(committed_receipt)
