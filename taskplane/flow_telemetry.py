"""Immutable observations of committed lifecycle transitions, never authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable, TypeGuard

from . import flow_usage, primitives
from .snapshot_retention import _directory, _read, _write, _locked, _root

SCHEMA = "taskplane.usage-closure/v1"


def _valid(value: Any, workspace: str) -> TypeGuard[dict[str, Any]]:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("workspace") != workspace:
        return False
    identity = {key: value.get(key) for key in ("workspace", "root", "run", "transition", "revision")}
    if value.get("transition") == "interval_closed":
        identity["next_run"] = value.get("next_run")
    return bool(value.get("committed") and value.get("id") == primitives.content_fingerprint(identity)
                and value.get("fingerprint") == primitives.content_fingerprint(
                    {key: body for key, body in value.items() if key != "fingerprint"}))


def _point(state: dict[str, Any], measurement: dict[str, Any], observed_at: str,
           previous_revision: int | None) -> dict[str, Any]:
    visit = state["visits"][state["index"]]
    return {"kind": "usage_boundary", "schema": "taskplane.phase-usage/v1", "run": state["run"],
            "session": state["root"], "revision": state["revision"], "previous_revision": previous_revision,
            "phase": visit["phase"], "visit": visit["id"],
            "bucket": "follow_up" if state.get("finished") else "review" if visit.get("decision") in
                      {"approved", "awaiting_human_approval"} else "work",
            "observed_at": observed_at, "sessions": deepcopy(measurement.get("sessions", [])),
            "coverage": deepcopy(measurement.get("token_coverage", {}))}


def commit_boundary(workspace: str | Path, before_state: dict[str, Any], after_state: dict[str, Any],
                    transition: str, measurement: dict[str, Any], observed_at: str, *,
                    cutoff: str | None = None, closing_counter: dict[str, Any] | None = None,
                    host: str = "codex", next_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Save an original sample only after a matching controller transition.

    A repeated publication returns the first committed bytes, never a later
    measurement relabelled as finish. Callers catch I/O errors after committing
    control, report the gap, and do not reverse the successful control action.
    """
    root = _root(workspace)
    binding = ("workspace", "root", "run")
    interval_closed = (transition == "interval_closed" and isinstance(next_state, dict)
                       and next_state.get("workspace") == after_state.get("workspace")
                       and next_state.get("root") == after_state.get("root")
                       and next_state.get("run") != after_state.get("run")
                       and next_state.get("started_at") == cutoff and bool(cutoff)
                       and bool(after_state.get("finished") or after_state.get("retired")))
    if (transition not in {"finish", "replacement", "interval_closed"}
            or any(before_state.get(k) != after_state.get(k) for k in binding)
            or after_state.get("workspace") != str(root.parent)
            or type(before_state.get("revision")) is not int or type(after_state.get("revision")) is not int
            or (after_state["revision"] <= before_state["revision"] and not interval_closed)
            or transition == "interval_closed" and not interval_closed
            or transition == "finish" and not after_state.get("finished")
            or transition == "replacement" and not after_state.get("superseded_by")):
        raise ValueError("Usage closure requires a matching committed lifecycle transition")
    stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Usage observation needs an actual timezone-aware timestamp")
    if cutoff is not None and datetime.fromisoformat(cutoff.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("Usage cutoff needs a timezone-aware timestamp")
    identity = {key: after_state[key] for key in binding}
    identity.update(transition=transition, revision=after_state["revision"])
    if interval_closed:
        identity["next_run"] = next_state["run"]  # type: ignore[index]
    key = primitives.content_fingerprint(identity)
    observed = deepcopy(measurement)
    for session in observed.get("sessions", []):
        if (session.get("usage") is not None and session.get("native_usage") is not None
                and session.get("status") == "measured" and not session.get("measured_at")):
            session["measured_at"] = observed_at
    value = {"schema": SCHEMA, "id": key, **identity, "host": host,
             "previous_revision": before_state["revision"], "observed_at": observed_at,
             "cutoff": cutoff or observed_at, "recorded_at": datetime.now(timezone.utc).isoformat(),
             "label": "At finish" if transition == "finish" else "Interval closed",
             "measurement": observed, "closing_counter": deepcopy(closing_counter),
             "coverage": deepcopy(observed.get("token_coverage", {})), "state": deepcopy(after_state),
             "point": _point(after_state, observed, observed_at, before_state["revision"]),
             "authority": "observation_only", "committed": True}
    value["point"]["closure_id"] = key
    value["fingerprint"] = primitives.content_fingerprint(value)
    root.mkdir(exist_ok=True, mode=0o700)
    with _locked(root) as fd:
        name = "usage-closure-" + key + ".json"
        try:
            prior = json.loads(_read(fd, name))
        except FileNotFoundError:
            _write(fd, name, primitives.canonical_bytes(value, trailing_newline=True), exclusive=True)
            return value
        if (not _valid(prior, str(root.parent)) or prior.get("id") != key
                or any(prior.get(k) != v for k, v in identity.items())):
            raise ValueError("Usage closure record is corrupt; original evidence must be preserved")
        return prior


def read_boundaries(workspace: str | Path, run: str, root: str | None = None) -> list[dict[str, Any]]:
    """Read bounded immutable closure records; absence is not a zero counter."""
    store = _root(workspace)
    values: list[dict[str, Any]] = []
    if not store.exists():
        return values
    with _directory(store) as fd:
        with os.scandir(fd) as entries:
            for count, entry in enumerate(entries):
                if count >= 20000:
                    raise ValueError("Usage closure inventory exceeds its bound")
                if not entry.name.startswith("usage-closure-") or not entry.name.endswith(".json"):
                    continue
                value = json.loads(_read(fd, entry.name))
                if not _valid(value, str(store.parent)):
                    raise ValueError("Usage closure inventory contains invalid evidence")
                if value.get("run") == run and (root is None or value.get("root") == root):
                    values.append(value)
    return sorted(values, key=lambda value: (value["revision"], value["cutoff"], value["id"]))


def frozen_model(model: dict[str, Any], boundary: dict[str, Any],
                 points: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Build a final view from the original sample and post-commit state."""
    if model.get("run") != boundary.get("run") or not _valid(boundary, model.get("snapshot", {}).get("workspace")):
        raise ValueError("Final view requires the same committed run boundary")
    result = deepcopy(model)
    measurement = deepcopy(boundary["measurement"])
    for key in ("sessions", "tokens", "native_tokens", "host_approval_tokens", "token_coverage"):
        result[key] = measurement.get(key)
    result["workflow"] = deepcopy(boundary["state"])
    delivery = [s for s in measurement.get("sessions", []) if s.get("role") != "host_approval_review"]
    known = [s for s in delivery if s.get("usage") is not None]
    times = sorted({s["measured_at"] for s in known if s.get("measured_at")})
    complete = (bool(known) and len(known) == len(delivery) and all(s.get("measured_at")
                and s.get("status") == "measured" for s in known)
                and not measurement.get("token_coverage", {}).get("discovery_errors"))
    result["usage_measurement"] = {"status": "fresh" if complete else "partial" if known else "unavailable",
        "measured_at": times[0] if complete and len(times) == 1 else None, "attempted_at": boundary["observed_at"],
        "oldest_at": times[0] if times else None, "newest_at": times[-1] if times else None}
    history = [deepcopy(p) for p in points if p.get("run") == boundary["run"]
               and p.get("kind") == "usage_boundary" and p.get("revision", -1) <= boundary["revision"]]
    if not any(p.get("closure_id") == boundary["id"] for p in history):
        history.append(deepcopy(boundary["point"]))
    measurement["usage_measurement"] = result["usage_measurement"]
    result["phase_usage"] = flow_usage.phase_accounting(history, boundary["point"], boundary["state"], measurement)
    result["final_observation"] = {key: deepcopy(boundary[key]) for key in
        ("id", "transition", "label", "revision", "previous_revision", "observed_at", "cutoff", "coverage", "authority")}
    result["historical"] = True
    result["snapshot"] = {**result.get("snapshot", {}), "revision": boundary["revision"],
                          "root": boundary["root"], "workspace": boundary["workspace"], "run": boundary["run"],
                          "captured_at": boundary["observed_at"], "measurement_at": result["usage_measurement"]["measured_at"],
                          "measurement_status": result["usage_measurement"]["status"],
                          "measurement_oldest_at": result["usage_measurement"]["oldest_at"],
                          "measurement_newest_at": result["usage_measurement"]["newest_at"],
                          "measurement_attempted_at": boundary["observed_at"], "historical": True,
                          "final_observation": boundary["id"]}
    result["snapshot"].pop("digest", None)
    return result
