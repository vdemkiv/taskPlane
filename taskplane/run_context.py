"""Invocation-local projection of durable run inputs, not another state store.

The loop owns persisted configuration. This module binds its exact values
for existing settings consumers and serializes controller decisions. Host
sessions, environment flags and hooks cannot create or replace those values.
"""
from __future__ import annotations
import hashlib
if __package__:
    from .primitives import canonical_bytes
else:
    from primitives import canonical_bytes  # type: ignore[no-redef]

from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
import json
import os
import sys
from typing import Any, Callable


_CURRENT: ContextVar[tuple[str, str, bytes, str] | None] = ContextVar(
    "taskplane_run_context", default=None)
_CONTROLLER_LOCK: ContextVar[str | None] = ContextVar("taskplane_controller_lock", default=None)


class RunContextError(ValueError):
    pass


def selected(state: Mapping[str, Any] | None) -> bool:
    return bool(state and state.get("_stage_native_root_authority"))


def current_settings() -> tuple[dict[str, Any], str] | None:
    current = _CURRENT.get()
    return None if current is None else (json.loads(current[2]), current[3])


def resource_limits_advisory(workspace: str) -> bool:
    """Read a run-owned decision; environment flags cannot disable limits."""
    from taskplane import storage, run_store, phase_records
    locator = storage.load_workspace_locator(workspace)
    if not locator or not locator.get("run_id"):
        return False
    run_id = locator["run_id"]
    manifest = run_store.RunStore(home=locator["home"]).inspect(run_id)
    return phase_records.resource_policy(manifest, run_id) is not None


@contextmanager
def bind(workspace: str, state: Mapping[str, Any] | None) -> Iterator[None]:
    """Read exact saved settings; no package-default reconstruction or writes."""
    if not selected(state):
        yield
        return
    assert state is not None
    snapshot, digest = state.get("settings_snapshot"), state.get("settings_digest")
    authority = state.get("run_artifact_binding")
    if isinstance(authority, Mapping) and authority.get("settings_digest") != digest:
        raise RunContextError("run settings digest differs from its original artifact binding")
    if not isinstance(snapshot, Mapping):
        raise RunContextError(
            "run settings snapshot is missing; restore the original configuration with "
            "loop restore-settings --from <original-settings.json>; no new session is required")
    encoded = canonical_bytes(dict(snapshot), ensure_ascii=True)
    if snapshot.get("schema") != "taskplane.operational-settings/v2" or hashlib.sha256(encoded).hexdigest() != digest:
        raise RunContextError("run settings snapshot is incomplete or its digest changed")
    run_id = str(state.get("run_id") or "")
    if not run_id:
        raise RunContextError("run context has no durable run identity")
    current = _CURRENT.get()
    if current is not None and (current[1], current[2], current[3]) != (run_id, encoded, digest):
        raise RunContextError("nested invocation changed the bound run or configuration")
    token = _CURRENT.set((os.path.realpath(workspace), run_id, encoded, str(digest)))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def operation(function: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Reconstruct context before an effect and serialize controller decisions.

    The lock is ephemeral coordination, not a new workflow owner. Re-read
    after acquiring it: a second controller must observe the first one's
    prepared/uncertain attempt rather than independently reserve a launch.
    """
    @wraps(function)
    def invoke(workspace: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        runtime = sys.modules[function.__module__]
        try:
            state = runtime._load_raw(workspace)
            if not selected(state):
                return function(workspace, *args, **kwargs)
            if function.__name__ == "next_action" and kwargs.get("_worker_task_id") is None:
                observed = runtime.read_pending_action(workspace)
                if observed is not None:
                    return dict(observed)
            path = os.path.join(runtime.tp.tp_dir(workspace), "controller-operation")
            nested = _CONTROLLER_LOCK.get() == path
            with nullcontext() if nested else runtime.tp.file_lock(path):
                token = _CONTROLLER_LOCK.set(path)
                try:
                    state = runtime._load_raw(workspace)
                    with bind(workspace, state):
                        return function(workspace, *args, **kwargs)
                finally:
                    _CONTROLLER_LOCK.reset(token)
        except (RunContextError, ValueError, OSError) as exc:
            result = {"error": "run context refused: " + str(exc), "dispatch_allowed": False}
            return runtime.project_next_action_for_host(workspace, result) if function.__name__ == "next_action" else result
    return invoke


def restore_settings(runtime: Any, workspace: str, source: str) -> dict[str, Any]:
    """Repair missing saved input with the EXACT original configuration.

    This grants no execution, changes no policy, and cannot rewrite an
    existing snapshot. No session identity or approval renewal is needed.
    """
    if runtime.tp.task_slot() is not None:
        return {"error": "settings restoration is orchestrator-only"}
    state = runtime._load_raw(workspace)
    if not selected(state):
        return {"error": "settings restoration requires an existing stage-native run"}
    try:
        authority = state.get("run_artifact_binding")
        if isinstance(authority, Mapping) and authority.get("settings_digest") != state.get("settings_digest"):
            raise RunContextError("run settings digest differs from its original artifact binding")
        supplied = runtime.operational_settings.load_settings(source, environment={})
        if supplied.digest != state.get("settings_digest"):
            raise RunContextError("supplied settings do not match the run's original digest")
        with runtime.mutate(workspace) as current:
            if current is None or current.get("run_id") != state["run_id"] or \
                    current.get("settings_digest") != supplied.digest:
                raise RunContextError("run changed during settings restoration")
            prior = current.get("settings_snapshot")
            if prior is not None and prior != supplied.to_dict():
                raise RunContextError("existing settings snapshot differs; refusing replacement")
            current["settings_snapshot"] = supplied.to_dict()
        return {"restored": True, "run_id": state["run_id"], "settings_digest": supplied.digest,
                "policy_changed": False, "dispatch_allowed": False, "replay": prior is not None}
    except (ValueError, OSError) as exc:
        return {"error": "settings restoration refused: " + str(exc), "dispatch_allowed": False}
