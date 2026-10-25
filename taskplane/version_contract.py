"""The shared version invariant for available plugin manifest bytes."""
from __future__ import annotations

from collections.abc import Mapping
import json
import re

MANIFEST_PATHS = (".codex-plugin/plugin.json", ".claude-plugin/plugin.json",
                  ".claude-plugin/marketplace.json")


def inspect_versions(manifest_bytes: Mapping[str, bytes]) -> dict[str, str | bool]:
    """Inspect exact input bytes, supporting either host's extracted package."""
    def version(value: object, path: str) -> str:
        candidate = value.get("version") if isinstance(value, dict) else None
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError(f"Missing or invalid version in {path}")
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", candidate):
            raise ValueError(f"Package version must be a safe semantic version in {path}")
        return candidate

    versions = []
    for path in MANIFEST_PATHS[:2]:
        if path in manifest_bytes:
            versions.append(version(json.loads(manifest_bytes[path]), path))
    if not versions:
        raise ValueError("At least one plugin manifest is required")
    market = MANIFEST_PATHS[2]
    if market in manifest_bytes:
        data = json.loads(manifest_bytes[market])
        versions.append(version(data, market))
        plugins = data.get("plugins")
        if not isinstance(plugins, list) or not all(isinstance(p, dict) for p in plugins):
            raise ValueError("Marketplace plugins must be a list of objects")
        entries = [p for p in plugins if p.get("name") == "taskplane"]
        if len(entries) != 1:
            raise ValueError("Marketplace must contain exactly one Taskplane entry")
        versions.append(version(entries[0], market + " Taskplane entry"))
    return {"version": versions[0], "ok": len(set(versions)) == 1}
