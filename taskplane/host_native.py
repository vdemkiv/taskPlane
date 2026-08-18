"""Canonical, host-neutral records for native workflow surfaces.

The records in this module are presentation inputs, not a second workflow
authority.  They are immutable, content addressed, and retain the complete
canonical value/evidence/action set when a host has to use a fallback.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .host_capabilities import SurfaceSelection


SNAPSHOT_SCHEMA = "taskplane.host-surface-snapshot/v1"
EVENT_SCHEMA = "taskplane.host-surface-event/v1"


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
