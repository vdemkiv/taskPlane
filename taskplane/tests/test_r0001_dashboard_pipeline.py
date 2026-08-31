"""Canonical dashboard-state pipeline regressions for R-0001."""
from __future__ import annotations

from pathlib import Path

import pytest

from taskplane import host_native
from taskplane import loop_status
from taskplane import storage


def _legacy_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = {
        "goal": "keep the dashboard current",
        "step": "execute",
        "baseline": "revision-7",
        "tasks": [{"id": "dashboard", "status": "running"}],
        "current_task": 0,
    }
    monkeypatch.setattr(loop_status, "_select_dashboard_source", lambda _ws: {
        "mode": "legacy", "status": "ready", "run_id": "legacy-run",
        "revision": "revision-7", "target": "dashboard", "state": state,
        "evidence": ["loop-state:revision-7"],
    })
    return workspace


def test_generated_at_sequence_revision_and_fingerprint_survive_restart(
        tmp_path, monkeypatch):
    workspace = _legacy_source(tmp_path, monkeypatch)
    first = loop_status.refresh_dashboard_snapshot(
        str(workspace), event_type="gate", outcome="pass",
        committed_at="2026-08-18T10:00:00Z")
    restored = loop_status.refresh_dashboard_snapshot(
        str(workspace), event_type="session_recovery", replay=True)
    assert restored["replayed"] is True
    assert restored["snapshot"] == first["snapshot"]
    assert restored["snapshot"]["values"]["generated_at"] == \
        "2026-08-18T10:00:00Z"
    assert restored["snapshot"]["sequence"] == 1
    assert restored["snapshot"]["revision"] == "revision-7"
    assert restored["snapshot"]["fingerprint"] == \
        first["snapshot"]["fingerprint"]


def test_same_sequence_different_fingerprint_is_rejected_as_contradictory(
        tmp_path, monkeypatch):
    workspace = _legacy_source(tmp_path, monkeypatch)
    first = loop_status.refresh_dashboard_snapshot(
        str(workspace), event_type="gate", outcome="pass", committed_at=1)
    original = host_native.HostSurfaceSnapshot.from_dict(first["snapshot"])
    contradictory = host_native.HostSurfaceSnapshot.create(
        workflow_id=original.workflow_id, run_id=original.run_id,
        target=original.target, revision=original.revision,
        sequence=original.sequence, stage=original.stage,
        state="failed", values=dict(original.values),
        evidence=original.evidence, safe_actions=())
    with pytest.raises(host_native.ContradictorySnapshotError):
        storage.commit_dashboard_snapshot(str(workspace), contradictory)


def test_refresh_builds_one_snapshot_and_all_surfaces_share_fingerprint(
        tmp_path, monkeypatch):
    workspace = _legacy_source(tmp_path, monkeypatch)
    reads = 0
    selected = loop_status._select_dashboard_source

    def counted(workspace_path):
        nonlocal reads
        reads += 1
        return selected(workspace_path)

    monkeypatch.setattr(loop_status, "_select_dashboard_source", counted)
    publication = loop_status.refresh_dashboard_snapshot(
        str(workspace), event_type="gate", outcome="failure", committed_at=2)
    assert reads == 1
    fingerprint = publication["snapshot"]["fingerprint"]
    assert set(publication["surfaces"].values()) == {fingerprint}
    assert publication["event"]["snapshot_fingerprint"] == fingerprint


def test_v4_snapshot_never_falls_back_to_unbound_legacy_state(
        tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(loop_status.runtime_storage,
                        "load_workspace_locator", lambda _ws: {
                            "run_id": "run-v4"})
    monkeypatch.setattr(loop_status, "_load_v4_manifest",
                        lambda _ws, _locator: (_ for _ in ()).throw(
                            ValueError("corrupt v4")))
    monkeypatch.setattr(loop_status, "_load_legacy_state",
                        lambda _ws: (_ for _ in ()).throw(
                            AssertionError("legacy fallback used")))
    publication = loop_status.refresh_dashboard_snapshot(
        str(workspace), event_type="recovery", committed_at=3)
    assert publication["source_mode"] == "v4"
    assert publication["snapshot"]["state"] == "corrupt"
    assert publication["snapshot"]["safe_actions"] == []
    assert "corrupt v4" in " ".join(publication["snapshot"]["evidence"])


def test_corrupt_or_ambiguous_v4_disables_actions_and_preserves_evidence(
        tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sources = iter((
        {"mode": "v4", "status": "corrupt", "run_id": "run-v4",
         "revision": "9", "target": "stage", "state": None,
         "evidence": ["manifest fingerprint mismatch"]},
        {"mode": "v4", "status": "ambiguous", "run_id": "run-v4",
         "revision": "10", "target": "stage", "state": None,
         "evidence": ["two foreground stages"]},
    ))
    monkeypatch.setattr(loop_status, "_select_dashboard_source",
                        lambda _ws: next(sources))
    corrupt = loop_status.refresh_dashboard_snapshot(
        str(workspace), event_type="recovery", committed_at=4)
    ambiguous = loop_status.refresh_dashboard_snapshot(
        str(workspace), event_type="recovery", committed_at=5)
    for publication, evidence in (
            (corrupt, "manifest fingerprint mismatch"),
            (ambiguous, "two foreground stages")):
        assert publication["snapshot"]["safe_actions"] == []
        assert evidence in publication["snapshot"]["evidence"]

