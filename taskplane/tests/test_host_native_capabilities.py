from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from taskplane.host_capabilities import (
    Observation,
    SurfaceSelection,
    dispatch_snapshot_from_environment,
    negotiate_host_surfaces,
    negotiate_snapshot_surfaces,
)
from taskplane.host_native import (
    ContradictorySnapshotError,
    HostSurfaceEvent,
    HostSurfaceSnapshot,
    ordered_snapshots,
)


SURFACES = (
    "pip",
    "visualization",
    "carousel",
    "approval",
    "sandbox",
    "hosting",
    "browser",
    "side_panel",
)


def _dashboard_contract_fixture() -> dict:
    path = Path(__file__).parent / "fixtures/dashboard-contract/snapshot-v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _observed(status: str, *, reason: str = "fixture") -> Observation:
    return Observation(
        status=status,
        source="host-receipt:test",
        confidence="high",
        reason=reason,
        observed_at="2026-08-18T10:00:00Z",
    )


@pytest.mark.parametrize(
    ("status", "selected", "limitation"),
    [
        ("supported", "native", None),
        ("unsupported", "fallback", "unsupported"),
        ("partial", "fallback", "partial"),
        ("unknown", "fallback", "unknown"),
        ("stale", "fallback", "stale"),
        ("contradictory", "fallback", "contradictory"),
        ("changed", "fallback", "changed"),
    ],
)
def test_negotiation_is_fail_closed_and_source_attributed(
    status: str, selected: str, limitation: str | None
) -> None:
    selections = negotiate_host_surfaces(
        host="codex",
        host_version="26.813",
        observations={name: _observed(status) for name in SURFACES},
        observed_at="2026-08-18T10:00:00Z",
    )

    selection = selections["pip"]
    assert isinstance(selection, SurfaceSelection)
    assert selection.selected_surface == selected
    assert selection.source == "host-receipt:test"
    assert selection.confidence == "high"
    assert selection.freshness == ("fresh" if status in {"supported", "unsupported"} else status)
    assert selection.limitation == limitation
    assert selection.fallback == (None if selected == "native" else "accessible_bounded")


def test_independent_disablements_preserve_canonical_snapshot() -> None:
    canonical = HostSurfaceSnapshot.create(
        workflow_id="wf-1",
        run_id="run-1",
        target="repo@abc",
        revision="rev-7",
        sequence=3,
        stage="review",
        state="awaiting_approval",
        values={"findings": ("F-1", "F-2"), "gate": "blocked"},
        evidence=("sha256:e1",),
        safe_actions=("inspect", "approve"),
    )
    before = canonical.to_dict()

    for disabled in SURFACES:
        observations = {name: _observed("supported") for name in SURFACES}
        observations[disabled] = _observed("unsupported")
        selection = negotiate_host_surfaces(
            host="claude",
            host_version="1.2",
            observations=observations,
            observed_at="2026-08-18T10:00:00Z",
        )[disabled]
        fallback = canonical.project(selection)

        assert fallback["canonical"] == before
        assert fallback["presentation"]["kind"] == "accessible_bounded"
        assert fallback["presentation"]["reason"] == "unavailable"
        assert fallback["presentation"]["user_declined"] is False
        assert fallback["presentation"]["safe_actions"] == ["inspect", "approve"]
        assert canonical.to_dict() == before


def test_snapshot_and_events_are_immutable_ordered_and_deterministic() -> None:
    snapshot = HostSurfaceSnapshot.create(
        workflow_id="wf-1",
        run_id="run-1",
        target="repo@abc",
        revision="rev-7",
        sequence=1,
        stage="execute",
        state="active",
        values={
            "generated_at": "2026-08-18T10:00:00Z",
            "agents": [{"id": "a-1", "state": "working"}],
        },
        evidence=("sha256:e1",),
        safe_actions=("cancel",),
    )
    again = HostSurfaceSnapshot.create(**{
        "workflow_id": "wf-1", "run_id": "run-1", "target": "repo@abc",
        "revision": "rev-7", "sequence": 1, "stage": "execute",
        "state": "active", "values": {
            "generated_at": "2026-08-18T10:00:00Z",
            "agents": [{"id": "a-1", "state": "working"}],
        },
        "evidence": ("sha256:e1",), "safe_actions": ("cancel",),
    })
    event = HostSurfaceEvent.from_snapshot(snapshot, event_type="updated")

    assert snapshot.fingerprint == again.fingerprint
    assert event.sequence == snapshot.sequence
    assert event.snapshot_fingerprint == snapshot.fingerprint
    assert ordered_snapshots((again, snapshot)) == (snapshot,)
    with pytest.raises(FrozenInstanceError):
        snapshot.stage = "done"  # type: ignore[misc]
    with pytest.raises(TypeError):
        snapshot.values["agents"] = ()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        event.event_type = "done"  # type: ignore[misc]


def test_generated_at_sequence_revision_and_fingerprint_survive_restart() -> None:
    persisted = _dashboard_contract_fixture()
    restored_snapshot = HostSurfaceSnapshot.from_dict(persisted["snapshot"])
    restored_event = HostSurfaceEvent.from_dict(persisted["event"])

    assert restored_snapshot.generated_at == "2026-08-18T10:00:00Z"
    assert restored_snapshot.sequence == 41
    assert restored_snapshot.revision == "rev-7"
    assert restored_snapshot.fingerprint == persisted["snapshot"]["fingerprint"]
    assert restored_snapshot.to_dict() == persisted["snapshot"]
    assert restored_event.to_dict() == persisted["event"]


def test_same_sequence_different_fingerprint_is_rejected_as_contradictory() -> None:
    persisted = _dashboard_contract_fixture()
    accepted = HostSurfaceSnapshot.from_dict(persisted["snapshot"])
    contradictory = HostSurfaceSnapshot.from_dict(
        persisted["same_sequence_contradiction"]
    )

    with pytest.raises(ContradictorySnapshotError, match="contradictory"):
        ordered_snapshots((accepted, contradictory))


def test_environment_receipts_round_trip_through_sealed_snapshot() -> None:
    snapshot = dispatch_snapshot_from_environment(
        "/repo",
        host="codex",
        environment={
            "TASKPLANE_NATIVE_PIP": "supported",
            "TASKPLANE_NATIVE_APPROVAL": "partial",
            "TASKPLANE_HOST_VERSION": "26.813",
            "TASKPLANE_HOST_RECEIPT_AT": "2026-08-18T10:00:00Z",
        },
    )
    selections = negotiate_snapshot_surfaces(snapshot)

    assert selections["pip"].selected_surface == "native"
    assert selections["approval"].selected_surface == "fallback"
    assert selections["approval"].limitation == "partial"
    assert selections["browser"].status == "unknown"
