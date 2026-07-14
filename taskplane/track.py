"""Tracks — multiple workstreams over one governed engine.

A track is a named unit of work (feature, epic, refactor) with its own loop
state. Only one track is ACTIVE at a time (its loop.json is the live one);
switching archives the current loop state into `.taskplane/tracks/<name>/`
and restores the target's. The KB, graph, and requirements are shared across
tracks — that's the point: track 7 recalls what track 2 decided.
"""

from __future__ import annotations

import json
import os
import shutil

import taskplane_lite as tp

LOOP_FILE = "loop.json"


def _state_dir(ws: str) -> str:
    # External per-project store (taskplane_lite.kb_root), not the repo.
    return os.path.join(tp.kb_root(ws), "state")


def _reg_path(ws: str) -> str:
    return os.path.join(_state_dir(ws), "tracks.json")


def _tracks_dir(ws: str, name: str) -> str:
    return os.path.join(_state_dir(ws), "tracks", name)


def _registry(ws: str) -> dict:
    p = _reg_path(ws)
    if not os.path.exists(p):
        return {"active": None, "tracks": {}}
    with open(p) as f:
        return json.load(f)


def _save(ws: str, reg: dict) -> None:
    os.makedirs(_state_dir(ws), exist_ok=True)
    with open(_reg_path(ws), "w") as f:
        json.dump(reg, f, indent=2)


def _live_loop(ws: str) -> str:
    return os.path.join(_state_dir(ws), LOOP_FILE)


def new(ws: str, name: str, goal: str, requirement_id: str | None = None) -> dict:
    """Register a track. It becomes active only via switch (or if first)."""
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
    reg = _registry(ws)
    return {"active": reg["active"],
            "tracks": sorted(reg["tracks"].values(),
                             key=lambda t: t["name"])}


def switch(ws: str, name: str) -> dict:
    """Archive the active track's loop state; restore the target's."""
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
    tp.trace(ws, "track_switch", track=name, previous=cur)
    return {"active": name, "previous": cur,
            "has_loop_state": os.path.exists(live)}


def close(ws: str, name: str, status: str = "done") -> dict:
    reg = _registry(ws)
    if name not in reg["tracks"]:
        return {"error": f"no track '{name}'"}
    reg["tracks"][name]["status"] = status
    if reg["active"] == name:
        live = _live_loop(ws)
        if os.path.exists(live):
            os.makedirs(_tracks_dir(ws, name), exist_ok=True)
            shutil.move(live, os.path.join(_tracks_dir(ws, name), LOOP_FILE))
        reg["active"] = None
    _save(ws, reg)
    tp.trace(ws, "track_close", track=name, status=status)
    return {"closed": name, "status": status}
