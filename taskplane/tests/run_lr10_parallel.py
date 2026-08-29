#!/usr/bin/env python3
"""Run LR-10's declared pytest surface in isolated parallel shards."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SHARDS = {
    "policy": (
        "taskplane/tests/test_delivery_policy.py",
        "taskplane/tests/test_lens_route_policy.py",
        "taskplane/tests/test_lens_route_telemetry.py",
    ),
    "authority": (
        "taskplane/tests/test_expanded_route_authority_provider.py",
        "taskplane/tests/test_expanded_lens_route_authority.py",
    ),
    "review": (
        "taskplane/tests/test_review_routing.py",
    ),
    "evidence": (
        "taskplane/tests/test_evaluation_output_contract.py",
        "taskplane/tests/test_evidence_bundle.py",
        "taskplane/tests/test_runtime_eval_guidance.py",
        "taskplane/tests/test_focused_lens_routing.py",
    ),
    "loop": (
        "taskplane/tests/test_loop.py",
    ),
}


def _start(name: str, selectors: tuple[str, ...]) -> tuple[subprocess.Popen, str]:
    temp_root = tempfile.mkdtemp(prefix=f"lr10-{name}-")
    resolved = os.path.realpath(temp_root)
    expected_parent = os.path.realpath(tempfile.gettempdir())
    if os.path.commonpath((expected_parent, resolved)) != expected_parent:
        raise RuntimeError(f"unsafe shard temp root: {resolved}")
    env = os.environ.copy()
    env.update({"TMPDIR": resolved, "TEMP": resolved, "TMP": resolved})
    argv = [sys.executable, "-m", "pytest", "-q", "-x", *selectors]
    process = subprocess.Popen(
        argv, cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return process, resolved


def main() -> int:
    running = {
        name: (*_start(name, selectors), selectors)
        for name, selectors in SHARDS.items()
    }
    print("LR-10 parallel shard map:", flush=True)
    for name, (_, temp_root, selectors) in running.items():
        print(f"  {name}: {len(selectors)} file(s), temp={temp_root}",
              flush=True)

    failures = []
    for name, (process, temp_root, selectors) in running.items():
        output, _ = process.communicate()
        print(f"\n[{name}] exit={process.returncode}", flush=True)
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
        if process.returncode:
            failures.append(name)

    if failures:
        print("LR-10 failed shards: " + ", ".join(failures), flush=True)
        return 1
    print(f"LR-10 all {len(running)} shards passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
