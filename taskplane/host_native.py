"""Canonical, host-neutral records for native workflow surfaces.

The records in this module are presentation inputs, not a second workflow
authority.  They are immutable, content addressed, and retain the complete
canonical value/evidence/action set when a host has to use a fallback.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .host_capabilities import SurfaceSelection


SNAPSHOT_SCHEMA = "taskplane.host-surface-snapshot/v1"
EVENT_SCHEMA = "taskplane.host-surface-event/v1"


class ContradictorySnapshotError(ValueError):
    """Two snapshots claim different canonical truth at one sequence."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item)
                                 for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class HostSurfaceSnapshot:
    """One immutable semantic view shared by every host presentation."""

    workflow_id: str
    run_id: str
    target: str
    revision: str
    sequence: int
    stage: str
    state: str
    values: Mapping[str, Any]
    evidence: tuple[str, ...]
    safe_actions: tuple[str, ...]
    fingerprint: str
    schema: str = SNAPSHOT_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        workflow_id: str,
        run_id: str,
        target: str,
        revision: str,
        sequence: int,
        stage: str,
        state: str,
        values: Mapping[str, Any],
        evidence: Sequence[str] = (),
        safe_actions: Sequence[str] = (),
    ) -> "HostSurfaceSnapshot":
        if not all(str(item).strip() for item in
                   (workflow_id, run_id, target, revision, stage, state)):
            raise ValueError("canonical snapshot identity fields are required")
        if (isinstance(sequence, bool) or not isinstance(sequence, int)
                or sequence < 0):
            raise ValueError("sequence must be a non-negative integer")
        frozen_values = _freeze(values)
        frozen_evidence = tuple(str(item) for item in evidence)
        frozen_actions = tuple(str(item) for item in safe_actions)
        payload = {
            "schema": SNAPSHOT_SCHEMA,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "target": target,
            "revision": revision,
            "sequence": sequence,
            "stage": stage,
            "state": state,
            "values": _plain(frozen_values),
            "evidence": list(frozen_evidence),
            "safe_actions": list(frozen_actions),
        }
        return cls(
            workflow_id=workflow_id,
            run_id=run_id,
            target=target,
            revision=revision,
            sequence=sequence,
            stage=stage,
            state=state,
            values=frozen_values,
            evidence=frozen_evidence,
            safe_actions=frozen_actions,
            fingerprint=_fingerprint(payload),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HostSurfaceSnapshot":
        """Rehydrate and authenticate persisted v1 canonical bytes."""
        if value.get("schema") != SNAPSHOT_SCHEMA:
            raise ValueError("unsupported host-surface snapshot schema")
        expected_fields = {
            "schema", "workflow_id", "run_id", "target", "revision",
            "sequence", "stage", "state", "values", "evidence",
            "safe_actions", "fingerprint",
        }
        if set(value) != expected_fields:
            raise ValueError(
                "host-surface snapshot fields are incomplete or unknown"
            )
        fingerprint = value.get("fingerprint")
        if not isinstance(fingerprint, str):
            raise ValueError("host-surface snapshot fingerprint is required")
        try:
            snapshot = cls.create(
                workflow_id=value["workflow_id"],
                run_id=value["run_id"],
                target=value["target"],
                revision=value["revision"],
                sequence=value["sequence"],
                stage=value["stage"],
                state=value["state"],
                values=value["values"],
                evidence=value["evidence"],
                safe_actions=value["safe_actions"],
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid host-surface snapshot") from exc
        if not hmac.compare_digest(snapshot.fingerprint, fingerprint):
            raise ValueError("host-surface snapshot fingerprint mismatch")
        return snapshot

    @property
    def generated_at(self) -> str | None:
        """Return the committed event time, absent only on historical v1 data."""
        value = self.values.get("generated_at")
        return value if isinstance(value, str) else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "target": self.target,
            "revision": self.revision,
            "sequence": self.sequence,
            "stage": self.stage,
            "state": self.state,
            "values": _plain(self.values),
            "evidence": list(self.evidence),
            "safe_actions": list(self.safe_actions),
            "fingerprint": self.fingerprint,
        }

    def project(self, selection: SurfaceSelection) -> dict[str, Any]:
        """Pair canonical truth with a negotiated, non-authoritative view."""
        presentation = selection.to_dict()
        presentation["kind"] = (
            selection.selected_surface if selection.selected_surface == "native"
            else selection.fallback
        )
        presentation["reason"] = (
            "available" if selection.selected_surface == "native"
            else "unavailable"
        )
        # Unavailable host functionality must never be reported as a choice.
        presentation["user_declined"] = False
        presentation["safe_actions"] = list(self.safe_actions)
        return {"canonical": self.to_dict(), "presentation": presentation}


@dataclass(frozen=True)
class HostSurfaceEvent:
    """Ordered content-addressed notification referencing a snapshot."""

    workflow_id: str
    run_id: str
    revision: str
    sequence: int
    event_type: str
    snapshot_fingerprint: str
    fingerprint: str
    schema: str = EVENT_SCHEMA

    @classmethod
    def from_snapshot(
        cls, snapshot: HostSurfaceSnapshot, *, event_type: str
    ) -> "HostSurfaceEvent":
        if not str(event_type).strip():
            raise ValueError("event_type is required")
        payload = {
            "schema": EVENT_SCHEMA,
            "workflow_id": snapshot.workflow_id,
            "run_id": snapshot.run_id,
            "revision": snapshot.revision,
            "sequence": snapshot.sequence,
            "event_type": event_type,
            "snapshot_fingerprint": snapshot.fingerprint,
        }
        return cls(
            workflow_id=snapshot.workflow_id,
            run_id=snapshot.run_id,
            revision=snapshot.revision,
            sequence=snapshot.sequence,
            event_type=event_type,
            snapshot_fingerprint=snapshot.fingerprint,
            fingerprint=_fingerprint(payload),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HostSurfaceEvent":
        """Rehydrate and authenticate a persisted v1 event reference."""
        if value.get("schema") != EVENT_SCHEMA:
            raise ValueError("unsupported host-surface event schema")
        expected_fields = {
            "schema", "workflow_id", "run_id", "revision", "sequence",
            "event_type", "snapshot_fingerprint", "fingerprint",
        }
        if set(value) != expected_fields:
            raise ValueError(
                "host-surface event fields are incomplete or unknown"
            )
        fingerprint = value.get("fingerprint")
        if not isinstance(fingerprint, str):
            raise ValueError("host-surface event fingerprint is required")
        payload = {key: value[key] for key in expected_fields - {"fingerprint"}}
        if not hmac.compare_digest(_fingerprint(payload), fingerprint):
            raise ValueError("host-surface event fingerprint mismatch")
        try:
            sequence = value["sequence"]
            if (isinstance(sequence, bool) or not isinstance(sequence, int)
                    or sequence < 0):
                raise ValueError("sequence must be a non-negative integer")
            if not all(str(value[key]).strip() for key in (
                    "workflow_id", "run_id", "revision", "event_type",
                    "snapshot_fingerprint")):
                raise ValueError("canonical event fields are required")
            return cls(
                workflow_id=value["workflow_id"],
                run_id=value["run_id"],
                revision=value["revision"],
                sequence=sequence,
                event_type=value["event_type"],
                snapshot_fingerprint=value["snapshot_fingerprint"],
                fingerprint=fingerprint,
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid host-surface event") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "revision": self.revision,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "fingerprint": self.fingerprint,
        }


def ordered_snapshots(
    snapshots: Iterable[HostSurfaceSnapshot],
) -> tuple[HostSurfaceSnapshot, ...]:
    """Return one deterministic v1 history or reject contradictory order.

    Exact duplicates are idempotent. A sequence may never name two different
    fingerprints for the same stable identity; consumers must reject that
    contradiction before projecting either candidate.
    """
    by_sequence: dict[int, HostSurfaceSnapshot] = {}
    identity: tuple[str, str, str, str] | None = None
    for snapshot in snapshots:
        # Public dataclass construction cannot bypass persisted-byte integrity.
        authenticated = HostSurfaceSnapshot.from_dict(snapshot.to_dict())
        candidate_identity = (
            authenticated.workflow_id,
            authenticated.run_id,
            authenticated.target,
            authenticated.revision,
        )
        if identity is None:
            identity = candidate_identity
        elif candidate_identity != identity:
            raise ValueError("host-surface snapshot identity changed")
        previous = by_sequence.get(authenticated.sequence)
        if (previous is not None
                and previous.fingerprint != authenticated.fingerprint):
            raise ContradictorySnapshotError(
                "contradictory snapshots share one sequence"
            )
        by_sequence.setdefault(authenticated.sequence, authenticated)
    return tuple(by_sequence[key] for key in sorted(by_sequence))
