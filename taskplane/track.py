"""Tracks — multiple workstreams over one governed engine.

A track is a named unit of work (feature, epic, refactor) with its own loop
state. Only one track is ACTIVE at a time (its loop.json is the live one);
switching archives the current loop state into the state dir's `tracks/<name>/`
and restores the target's. The KB, graph, and requirements are shared across
tracks — that's the point: track 7 recalls what track 2 decided.
"""

from __future__ import annotations

import copy
import os
import shutil

import loop as _loop
import stage_migration as _stage_migration
import taskplane_lite as tp

LOOP_FILE = "loop.json"
_READ_ONLY_ERROR = (
    "legacy track writes are read-only after verified stage migration; "
    "use stage commands")


def _state_dir(ws: str) -> str:
    # v2.3.0 (H): loop.state_dir OWNS the per-user-state rule — track state
    # (tracks.json, archived loop.json files) lives exactly where the engine
    # reads live loop state. Re-deriving via tp.kb_root committed per-user
    # in-flight state to the team store on a team plan, and on partially
    # migrated projects made `track switch` archive a loop.json the engine
    # never reads (two tracks silently sharing one state machine).
    return _loop.state_dir(ws)


def _reg_path(ws: str) -> str:
    return os.path.join(_state_dir(ws), "tracks.json")


def _tracks_dir(ws: str, name: str) -> str:
    return os.path.join(_state_dir(ws), "tracks", name)


def _registry(ws: str) -> dict:
    # v2.3.0: a corrupt registry fails CLOSED with a typed error naming the
    # file and remedy (tp.StateError) — never a bare JSONDecodeError, and
    # never a silent empty registry (which would drop the active-track
    # pointer and orphan every archived loop.json).
    return tp.load_json(_reg_path(ws),
                        default={"active": None, "tracks": {}},
                        what="track registry")


def _save(ws: str, reg: dict) -> None:
    os.makedirs(_state_dir(ws), exist_ok=True)
    # Atomic (v2.3.0): temp + os.replace, same as loop.save — a torn write
    # of tracks.json loses ALL track metadata.
    tp.atomic_write_json(_reg_path(ws), reg, indent=2)


def _live_loop(ws: str) -> str:
    # THE engine's live loop path — imported, never re-derived, so archiving
    # and the engine always agree on where loop.json lives.
    return _loop._loop_path(ws)


def _legacy_write_error(ws: str) -> dict | None:
    """Fail closed once the migration receipt makes stages authoritative."""
    if _stage_migration.legacy_track_projection(ws) is None:
        return None
    return {"error": _READ_ONLY_ERROR}


def new(ws: str, name: str, goal: str, requirement_id: str | None = None) -> dict:
    """Register a track. It becomes active only via switch (or if first)."""
    with tp.file_lock(_live_loop(ws)):
        blocked = _legacy_write_error(ws)
        if blocked is not None:
            return blocked
        reg = _registry(ws)
        if name in reg["tracks"]:
            return {"error": f"track '{name}' already exists"}
        reg["tracks"][name] = {"name": name, "goal": goal,
                               "requirement_id": requirement_id,
                               "status": "open"}
        first = reg["active"] is None
        _save(ws, reg)
    tp.trace(ws, "track_new", track=name, goal=goal)
    out = {"created": name, "goal": goal, "active": reg["active"]}
    if first:
        out.update(switch(ws, name))
    return out


def list_(ws: str) -> dict:
    projected = _stage_migration.legacy_track_projection(ws)
    if projected is not None:
        # The migration module verifies the operation receipt before exposing
        # this seam.  Return a detached, legacy-shaped read model and never
        # fall through to tracks.json once v4 stages are authoritative.
        return {
            "active": projected["active"],
            "tracks": sorted(copy.deepcopy(projected["tracks"]).values(),
                             key=lambda item: item["name"]),
        }
    reg = _registry(ws)
    return {"active": reg["active"],
            "tracks": sorted(reg["tracks"].values(),
                             key=lambda t: t["name"])}


def switch(ws: str, name: str) -> dict:
    """Archive the active track's loop state; restore the target's.

    Runs under the SAME lock the engine's mutate() takes on loop.json
    (v2.3.0), so the archive/restore moves and the registry update cannot
    interleave with a live gate's read-modify-write.
    """
    with tp.file_lock(_live_loop(ws)):
        blocked = _legacy_write_error(ws)
        if blocked is not None:
            return blocked
        reg = _registry(ws)
        if name not in reg["tracks"]:
            return {"error": f"no track '{name}' — `tp track new` first"}
        cur = reg["active"]
        live = _live_loop(ws)
        if cur and cur != name and os.path.exists(live):
            os.makedirs(_tracks_dir(ws, cur), exist_ok=True)
            shutil.move(live, os.path.join(_tracks_dir(ws, cur), LOOP_FILE))
        archived = os.path.join(_tracks_dir(ws, name), LOOP_FILE)
        if os.path.exists(archived) and not os.path.exists(live):
            shutil.move(archived, live)
        reg["active"] = name
        _save(ws, reg)
        has_loop = os.path.exists(live)
    tp.trace(ws, "track_switch", track=name, previous=cur)
    return {"active": name, "previous": cur,
            "has_loop_state": has_loop}


def close(ws: str, name: str, status: str = "done") -> dict:
    with tp.file_lock(_live_loop(ws)):
        blocked = _legacy_write_error(ws)
        if blocked is not None:
            return blocked
        reg = _registry(ws)
        if name not in reg["tracks"]:
            return {"error": f"no track '{name}'"}
        reg["tracks"][name]["status"] = status
        if reg["active"] == name:
            live = _live_loop(ws)
            if os.path.exists(live):
                os.makedirs(_tracks_dir(ws, name), exist_ok=True)
                shutil.move(live,
                            os.path.join(_tracks_dir(ws, name), LOOP_FILE))
            reg["active"] = None
        _save(ws, reg)
    tp.trace(ws, "track_close", track=name, status=status)
    return {"closed": name, "status": status}
