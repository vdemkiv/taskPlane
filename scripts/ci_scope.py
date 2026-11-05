#!/usr/bin/env python3
"""Select focused documentation checks; unknown changes always require full CI."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

MANIFESTS = {".codex-plugin/plugin.json", ".claude-plugin/plugin.json",
             ".claude-plugin/marketplace.json"}
FULL_JOBS = {"portability", "suites", "archive-journeys", "capacity", "dashboard-browser"}
ALWAYS_JOBS = {"scope", "quality-package", "security", "interpreter-import"}


def version_only(path: str, before: bytes, after: bytes) -> bool:
    try:
        old, new = json.loads(before), json.loads(after)
        for value in (old, new):
            version = value.pop("version")
            if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
                return False
            if path == ".claude-plugin/marketplace.json":
                for plugin in value["plugins"]:
                    if plugin.pop("version") != version:
                        return False
        return bool(old) and old == new
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def focused_change(path: str, status: str, before: bytes, after: bytes) -> bool:
    # Type changes, renames and unknown paths require full checks.
    if status not in {"A", "M", "D"}:
        return False
    parts = PurePosixPath(path).parts
    if path in {"README.md", "CHANGELOG.md"}:
        return True
    if parts and parts[0] in {"docs", "skills"} and path.endswith(".md"):
        return True
    return path in MANIFESTS and status == "M" and version_only(path, before, after)


def select_mode(root: Path, base: str, head: str) -> str:
    if not all(re.fullmatch(r"[0-9a-f]{40}", ref) and set(ref) != {"0"} for ref in (base, head)):
        return "full"

    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)

    try:
        fields = git("diff", "--name-status", "--no-renames", "-z", base, head).decode().split("\0")
        fields.pop()  # git's terminating NUL
        if not fields or len(fields) % 2:
            return "full"
        for status, path in zip(fields[::2], fields[1::2]):
            before = git("show", f"{base}:{path}") if path in MANIFESTS else b""
            after = git("show", f"{head}:{path}") if path in MANIFESTS else b""
            if not focused_change(path, status, before, after):
                return "full"
        return "focused"
    except (subprocess.CalledProcessError, UnicodeError):
        return "full"


def verify_results(results: dict[str, Any]) -> None:
    """Only explicitly unneeded jobs may be skipped; errors never become success."""
    if set(results) != FULL_JOBS | ALWAYS_JOBS:
        raise ValueError("Unexpected or missing CI job")
    mode = results["scope"].get("outputs", {}).get("mode")
    if mode not in {"full", "focused"}:
        raise ValueError("Missing or invalid CI scope")
    for name, job in results.items():
        expected = "skipped" if mode == "focused" and name in FULL_JOBS else "success"
        if job["result"] != expected:
            raise ValueError(f"{name}: expected {expected}, got {job['result']}")


if __name__ == "__main__":
    if "RESULTS" in os.environ:
        verify_results(json.loads(os.environ["RESULTS"]))
    else:
        mode = select_mode(Path.cwd(), os.environ.get("BASE_SHA", ""), os.environ.get("HEAD_SHA", ""))
        print(f"CI scope: {mode}")
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"mode={mode}\n")
