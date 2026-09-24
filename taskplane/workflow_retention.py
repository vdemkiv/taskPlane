"""Bounded native history retention; archived evidence never grants authority."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from . import workflow as w
from .context import Store, encode, REFERENCE_SCHEMA

WATERMARK = 4 * 1024 * 1024


def terminal(state: dict[str, Any]) -> bool:
    return bool(state.get("finished") or state.get("superseded_by") or state.get("retired"))


def compact(workspace: Path, db: dict[str, Any]) -> dict[str, Any]:
    """Archive first, then let the caller atomically commit the reduced index.

    A failed index write leaves the original authoritative state intact. Orphan
    immutable objects are harmless; only references in the index select history.
    Protected-host adapters must continue to use their owner's protected store.
    """
    if db.get("profile") != "native_workflow" or len(encode(db)) < WATERMARK:
        return db
    result = deepcopy(db)
    store = Store(workspace)
    for key, state in list(result["runs"].items()):
        if key == result["active"] or not terminal(state):
            continue
        ref = store.put("workflow-history", state)
        w.require(store.resolve(ref) == state, "state_unavailable", "History archive verification failed.")
        result.setdefault("archives", {})[key] = ref
        del result["runs"][key]
        if len(encode(result)) < WATERMARK:
            break
    return result


def validate_index(db: dict[str, Any]) -> None:
    archives = db.get("archives", {})
    w.require(isinstance(archives, dict) and not set(archives) & set(db["runs"]),
              "state_unavailable", "Invalid workflow history index.")
    w.require(not archives or db.get("profile") == "native_workflow", "state_unavailable",
              "Protected history requires its trusted owner.")
    for key, ref in archives.items():
        w.require(isinstance(key, str) and isinstance(ref, dict)
                  and ref.get("schema") == REFERENCE_SCHEMA and ref.get("kind") == "workflow-history",
                  "state_unavailable", "Invalid workflow history reference.")


def read(workspace: Path, db: dict[str, Any], key: str) -> dict[str, Any]:
    if key in db["runs"]:
        return deepcopy(db["runs"][key])
    w.require(key in db.get("archives", {}), "state_unavailable", "Requested workflow does not exist.")
    state = Store(workspace).resolve(db["archives"][key])
    w.require(isinstance(state, dict) and state.get("run") == key and terminal(state)
              and state.get("workspace") == db["workspace"] and state.get("root") == db["root"]
              and state.get("profile") == db.get("profile"),
              "state_unavailable", "Archived workflow identity is invalid.")
    w.validate_state(state)
    return dict(state)


def capacity(db: dict[str, Any], limit: int) -> dict[str, Any]:
    used = len(encode(db)) + 1
    return {"bytes": used, "limit_bytes": limit, "remaining_bytes": max(0, limit - used),
            "watermark_bytes": WATERMARK, "resident_runs": len(db["runs"]),
            "archived_runs": len(db.get("archives", {})),
            "status": "near_limit" if used >= limit * 0.8 else "available",
            "retention": "inactive terminal runs only; historical decisions preserved"}
