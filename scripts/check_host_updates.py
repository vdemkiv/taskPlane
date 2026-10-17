"""Compare explicit host observations without acquiring authority or scheduling work.

Usage: python scripts/check_host_updates.py --current observation.json
       --previous previous-observation.json

Inputs use taskplane.host-observation/v1. The authorized monitor supplies native
references; this offline comparator neither authenticates those references nor
proves a scheduled wake. Keep the input files with the printed report. Exit 0
means a complete stable/changed comparison, 2 means unknown/unsupported, and 1
means a failed or invalid observation. No input or baseline is ever rewritten.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

SCHEMA = "taskplane.host-observation/v1"
CAPABILITIES = ("identity", "automatic_hooks", "interrupt", "process_exit")
STATES = {"supported", "unsupported", "unknown"}
MAX_OBSERVATION_BYTES = 2 * 1024 * 1024  # Parser safety bound, not a delivery target.


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("observed_at must be a timezone-qualified timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include its timezone")
    return parsed


def validate_observation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("expected taskplane.host-observation/v1")
    if not isinstance(value.get("host"), str) or value["host"] not in {"codex", "claude"}:
        raise ValueError("host must be codex or claude")
    if (not isinstance(value.get("status"), str)
            or value["status"] not in {"observed", "unavailable", "unsupported", "failed"}):
        raise ValueError("unknown observation status")
    _timestamp(value.get("observed_at"))
    source = value.get("source")
    if (not isinstance(source, dict) or not isinstance(source.get("kind"), str)
            or source["kind"] not in {"native", "fixture"}
            or not isinstance(source.get("reference"), str) or not source["reference"].strip()):
        raise ValueError("source requires a native/fixture kind and exact reference")
    if value.get("status") != "observed":
        if not isinstance(value.get("reason"), str) or not value["reason"].strip():
            raise ValueError("unavailable/unsupported/failed observations require a reason")
        return deepcopy(value)
    if value.get("host_version") is not None and (
            not isinstance(value["host_version"], str) or not value["host_version"].strip()):
        raise ValueError("host_version must be a nonempty string or null")
    runtime = value.get("runtime", {})
    if not isinstance(runtime, dict) or type(runtime.get("complete", False)) is not bool:
        raise ValueError("runtime requires a boolean complete inventory indicator")
    members = runtime.get("member_sha256", {})
    if not isinstance(members, dict):
        raise ValueError("runtime.member_sha256 must be an object")
    for name, digest in members.items():
        if not isinstance(name, str):
            raise ValueError("runtime member name must be a string")
        path = PurePosixPath(name)
        if (not name or "\\" in name or path.is_absolute() or ".." in path.parts
                or path.as_posix() != name):
            raise ValueError("runtime member must be a canonical relative path")
        if digest is not None and (not isinstance(digest, str)
                                   or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("runtime member digest must be lowercase SHA-256 or null")
    capabilities = value.get("capabilities", {})
    if not isinstance(capabilities, dict) or any(
            not isinstance(name, str) or not name or not isinstance(state, str) or state not in STATES
            for name, state in capabilities.items()):
        raise ValueError("capabilities must map names to supported/unsupported/unknown")
    return deepcopy(value)


def _gaps(value: dict[str, Any]) -> list[str]:
    if value["status"] != "observed":
        return ["observation: " + value["status"]]
    gaps = []
    if not value.get("host_version"):
        gaps.append("host_version")
    runtime = value.get("runtime", {})
    members = runtime.get("member_sha256", {})
    if not runtime.get("complete"):
        gaps.append("runtime.complete")
    if not members:
        gaps.append("runtime.member_sha256")
    gaps.extend("runtime.member_sha256." + name for name, digest in members.items() if digest is None)
    capabilities = value.get("capabilities", {})
    gaps.extend("capabilities." + name for name in sorted(set(CAPABILITIES) | set(capabilities))
                if capabilities.get(name, "unknown") == "unknown")
    return sorted(gaps)


def compare_observations(current: Any, previous: Any = None) -> dict[str, Any]:
    """Report facts and gaps; a caller-supplied native label is not attestation."""
    current = validate_observation(current)
    previous = validate_observation(previous) if previous is not None else None
    gaps = ["current." + name for name in _gaps(current)]
    changes: list[dict[str, Any]] = []
    if previous is None:
        gaps.append("previous observation unavailable")
    elif previous["host"] != current["host"]:
        raise ValueError("cannot compare observations from different hosts")
    elif previous["source"]["kind"] != current["source"]["kind"]:
        raise ValueError("cannot compare fixture and native evidence as one lineage")
    else:
        gaps.extend("previous." + name for name in _gaps(previous))
        if _timestamp(current["observed_at"]) <= _timestamp(previous["observed_at"]):
            gaps.append("current observation is not newer than previous")
        if current["status"] == previous["status"] == "observed":
            if current.get("host_version") and previous.get("host_version"):
                if current["host_version"] != previous["host_version"]:
                    changes.append({"field": "host_version", "previous": previous["host_version"],
                                    "current": current["host_version"]})
            old_runtime, new_runtime = previous.get("runtime", {}), current.get("runtime", {})
            old_members = old_runtime.get("member_sha256", {})
            new_members = new_runtime.get("member_sha256", {})
            for name in sorted(set(old_members) | set(new_members)):
                old, new = old_members.get(name), new_members.get(name)
                known = old is not None and new is not None
                membership = (old_runtime.get("complete") and new_runtime.get("complete")
                              and (name in old_members) != (name in new_members))
                if old != new and (known or membership):
                    changes.append({"field": "runtime.member_sha256." + name,
                                    "previous": old, "current": new})
            old_caps, new_caps = previous.get("capabilities", {}), current.get("capabilities", {})
            for name in sorted(set(old_caps) | set(new_caps)):
                old, new = old_caps.get(name, "unknown"), new_caps.get(name, "unknown")
                if old != new and "unknown" not in {old, new}:
                    changes.append({"field": "capabilities." + name, "previous": old, "current": new})
    unsupported = sorted(name for name, state in current.get("capabilities", {}).items()
                         if state == "unsupported")
    if current["status"] == "failed":
        status = "failed"
    elif current["status"] == "unsupported":
        status = "unsupported"
    elif changes:
        status = "changed"
    elif gaps:
        status = "unknown"
    elif unsupported:
        status = "unsupported"
    else:
        status = "stable"
    return {"schema": "taskplane.host-watch-report/v1", "status": status,
            "comparison_complete": not gaps, "changes": changes, "unknowns": sorted(gaps),
            "unsupported_capabilities": unsupported, "current": current, "previous": previous,
            "recheck_required": status != "stable", "authority_verified": False,
            "native_execution_verified": False, "scheduled_wake_verified": False,
            "notification_sent": False,
            "detail": "Input comparison only; preserve native references and independently recheck changed boundaries."}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate observation key: " + key)
        result[key] = value
    return result


def read_observation(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read bounded regular files only; retain the exact input digest."""
    path = path.absolute()
    if any(candidate.is_symlink() for candidate in (path, *path.parents)):
        raise ValueError("observation paths cannot follow symlinks")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("observation must be a regular file")
        raw = stream.read(MAX_OBSERVATION_BYTES + 1)
    if len(raw) > MAX_OBSERVATION_BYTES:
        raise ValueError("observation exceeds parser safety bound")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    return validate_observation(value), {"path": str(path), "bytes": len(raw),
                                         "sha256": hashlib.sha256(raw).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args(argv)
    try:
        current, current_ref = read_observation(args.current)
        previous, previous_ref = read_observation(args.previous) if args.previous else (None, None)
        report = compare_observations(current, previous)
        report["inputs"] = {"current": current_ref, "previous": previous_ref}
    except (OSError, ValueError, UnicodeError) as exc:
        report = {"schema": "taskplane.host-watch-report/v1", "status": "failed",
                  "reason": str(exc), "comparison_complete": False, "authority_verified": False,
                  "native_execution_verified": False, "scheduled_wake_verified": False,
                  "notification_sent": False}
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] == "failed":
        return 1
    return 0 if report["status"] in {"stable", "changed"} and report["comparison_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
