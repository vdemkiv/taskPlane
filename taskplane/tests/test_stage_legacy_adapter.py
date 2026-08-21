"""R-0004 compatibility tests for the singleton track read adapter."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import track  # noqa: E402


def _projection() -> dict:
    return {
        "active": "stage-build",
        "tracks": {
            "stage-product": {
                "name": "stage-product", "goal": "define it",
                "requirement_id": "R-0004", "status": "closed"},
            "stage-build": {
                "name": "stage-build", "goal": "build it",
                "requirement_id": "R-0004", "status": "open"},
        },
    }


def _sorted_projection() -> dict:
    value = _projection()
    value["tracks"] = sorted(
        value["tracks"].values(), key=lambda item: item["name"])
    return value


def test_unverified_workspace_keeps_the_existing_singleton_behavior(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = str(tmp_path)
    monkeypatch.setattr(
        track._stage_migration, "legacy_track_projection",
        lambda _ws: None)

    created = track.new(ws, "auth", "build auth", "R-0001")
    assert created == {
        "created": "auth", "goal": "build auth",
        "active": "auth", "previous": None, "has_loop_state": False,
    }
    track.new(ws, "billing", "build billing")
    loop.save(ws, {"goal": "build auth", "step": "plan", "tasks": None,
                   "current_task": 0, "max_fix_cycles": 2,
                   "checkpoints": []})
    assert track.switch(ws, "billing") == {
        "active": "billing", "previous": "auth",
        "has_loop_state": False,
    }
    assert track.close(ws, "billing") == {
        "closed": "billing", "status": "done",
    }


def test_verified_receipt_uses_only_the_detached_stage_projection(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = str(tmp_path)
    expected = _sorted_projection()
    observed: list[str] = []

    def projected(workspace: str) -> dict:
        observed.append(workspace)
        return copy.deepcopy(_projection())

    monkeypatch.setattr(
        track._stage_migration, "legacy_track_projection", projected)
    monkeypatch.setattr(
        track, "_registry",
        lambda _ws: pytest.fail("verified adapter read legacy tracks.json"))

    first = track.list_(ws)
    assert first == expected
    first["tracks"][0]["goal"] = "caller mutation"
    assert track.list_(ws) == expected
    assert observed == [ws, ws]


def test_verified_receipt_makes_legacy_track_writes_read_only(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = str(tmp_path)
    monkeypatch.setattr(
        track._stage_migration, "legacy_track_projection",
        lambda _ws: None)
    track.new(ws, "auth", "build auth", "R-0004")
    track.new(ws, "billing", "build billing", "R-0004")
    loop.save(ws, {"goal": "build auth", "step": "plan", "tasks": None,
                   "current_task": 0, "max_fix_cycles": 2,
                   "checkpoints": []})

    state_root = Path(track._state_dir(ws))
    before = {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in state_root.rglob("*") if path.is_file()
    }
    monkeypatch.setattr(
        track._stage_migration, "legacy_track_projection",
        lambda _ws: copy.deepcopy(_projection()))
    monkeypatch.setattr(
        track, "_registry",
        lambda _ws: pytest.fail("verified adapter opened legacy registry"))

    for result in (
            track.new(ws, "after", "must not be created"),
            track.switch(ws, "billing"),
            track.close(ws, "auth")):
        assert "error" in result
        assert "read-only" in result["error"]

    after = {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in state_root.rglob("*") if path.is_file()
    }
    assert after == before
