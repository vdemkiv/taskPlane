#!/usr/bin/env python3
"""Run LR-10's declared pytest surface in isolated parallel shards."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Callable, Mapping


ROOT = Path(__file__).resolve().parents[2]
SHARD_TIMEOUT_SECONDS = 1500
AGGREGATE_TIMEOUT_SECONDS = 1800
CLEANUP_MARGIN_SECONDS = 300
TERMINATE_GRACE_SECONDS = 10
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


@dataclass
class ShardRun:
    shard_id: str
    name: str
    selectors: tuple[str, ...]
    temp_root: Path
    started_at: float
    process: subprocess.Popen[str] | None = None
    startup_error: str | None = None


@dataclass
class ShardResult:
    shard_id: str
    name: str
    selectors: tuple[str, ...]
    status: str
    returncode: int | None
    stdout: str
    stderr: str
    duration_seconds: float


def _validate_directory(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"{label} must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"{label} is unavailable") from exc
    if not resolved.is_dir():
        raise RuntimeError(f"{label} must be a directory")
    return resolved


def _validate_child_root(parent: Path, child: Path) -> Path:
    resolved_parent = _validate_directory(
        parent, label="LR-10 runner temp parent")
    resolved_child = _validate_directory(
        child, label="LR-10 shard temp root")
    if child.parent != parent or resolved_child.parent != resolved_parent:
        raise RuntimeError(
            "LR-10 shard temp root must be a direct child of its runner parent")
    return resolved_child


def _create_temp_roots(
        shards: Mapping[str, tuple[str, ...]]) -> tuple[Path, dict[str, Path]]:
    parent = Path(tempfile.mkdtemp(prefix="lr10-runner-"))
    parent = _validate_directory(parent, label="LR-10 runner temp parent")
    roots: dict[str, Path] = {}
    for index, name in enumerate(shards, 1):
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
            raise RuntimeError("unsafe LR-10 shard name")
        child = parent / f"{index:02d}-{name}"
        child.mkdir(mode=0o700)
        roots[name] = _validate_child_root(parent, child)
    return parent, roots


def _start(
        name: str, selectors: tuple[str, ...], temp_root: Path,
        *, popen_factory: Callable[..., subprocess.Popen[str]]) \
        -> subprocess.Popen[str]:
    env = os.environ.copy()
    # The runner is governed, but its pytest children exercise fresh workspace
    # contract semantics and must not impersonate the runner's worker slot.
    env.pop("TASKPLANE_TASK", None)
    resolved = str(temp_root)
    env.update({"TMPDIR": resolved, "TEMP": resolved, "TMP": resolved})
    argv = [sys.executable, "-m", "pytest", "-q", "-x", *selectors]
    return popen_factory(
        argv, cwd=ROOT, env=env, text=True, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _latest_output(previous: str, current: str | bytes | None) -> str:
    update = _as_text(current)
    if not update:
        return previous
    if update.startswith(previous):
        return update
    return previous + update


def _timed_communicate(
        process: subprocess.Popen[str], timeout: float) -> tuple[str, str]:
    stdout, stderr = process.communicate(timeout=max(0.0, timeout))
    return _as_text(stdout), _as_text(stderr)


def _collect_run(
        run: ShardRun, *, aggregate_wait_deadline: float,
        aggregate_hard_deadline: float, clock: Callable[[], float]) \
        -> ShardResult:
    if run.process is None:
        return ShardResult(
            run.shard_id, run.name, run.selectors, "startup-error", None,
            "", run.startup_error or "process startup failed",
            max(0.0, clock() - run.started_at))

    process = run.process
    stdout = ""
    stderr = ""
    status = "passed"
    shard_deadline = run.started_at + SHARD_TIMEOUT_SECONDS
    wait_deadline = min(shard_deadline, aggregate_wait_deadline)
    try:
        stdout, stderr = _timed_communicate(
            process, wait_deadline - clock())
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        stdout = _latest_output(stdout, exc.output)
        stderr = _latest_output(stderr, exc.stderr)
        try:
            process.terminate()
        except OSError as terminate_error:
            stderr += f"\nterminate failed: {terminate_error}"

        terminate_deadline = min(
            aggregate_hard_deadline, clock() + TERMINATE_GRACE_SECONDS)
        try:
            later_stdout, later_stderr = _timed_communicate(
                process, terminate_deadline - clock())
            stdout = _latest_output(stdout, later_stdout)
            stderr = _latest_output(stderr, later_stderr)
        except subprocess.TimeoutExpired as terminate_timeout:
            stdout = _latest_output(stdout, terminate_timeout.output)
            stderr = _latest_output(stderr, terminate_timeout.stderr)
            try:
                process.kill()
            except OSError as kill_error:
                stderr += f"\nkill failed: {kill_error}"
            try:
                later_stdout, later_stderr = _timed_communicate(
                    process, aggregate_hard_deadline - clock())
                stdout = _latest_output(stdout, later_stdout)
                stderr = _latest_output(stderr, later_stderr)
            except subprocess.TimeoutExpired as kill_timeout:
                stdout = _latest_output(stdout, kill_timeout.output)
                stderr = _latest_output(stderr, kill_timeout.stderr)
                stderr += "\nprocess did not exit before aggregate deadline"

    if status != "timeout" and process.returncode:
        status = "failed"
    return ShardResult(
        run.shard_id, run.name, run.selectors, status, process.returncode,
        stdout, stderr, max(0.0, clock() - run.started_at))


def run_shards(
        shards: Mapping[str, tuple[str, ...]] = SHARDS, *,
        popen_factory: Callable[..., subprocess.Popen[str]] | None = None,
        clock: Callable[[], float] | None = None) \
        -> tuple[Path, list[ShardResult]]:
    """Launch every shard before bounded collection, preserving all results."""
    popen_factory = popen_factory or subprocess.Popen
    clock = clock or time.monotonic
    aggregate_started_at = clock()
    parent, roots = _create_temp_roots(shards)
    runs: list[ShardRun] = []

    for index, (name, selectors) in enumerate(shards.items(), 1):
        started_at = clock()
        run = ShardRun(
            f"{index:02d}-{name}", name, tuple(selectors), roots[name],
            started_at)
        try:
            run.process = _start(
                name, run.selectors, run.temp_root,
                popen_factory=popen_factory)
        except (OSError, subprocess.SubprocessError) as exc:
            run.startup_error = f"{type(exc).__name__}: {exc}"
        runs.append(run)

    aggregate_hard_deadline = (
        aggregate_started_at + AGGREGATE_TIMEOUT_SECONDS)
    aggregate_wait_deadline = (
        aggregate_hard_deadline - CLEANUP_MARGIN_SECONDS)
    results = [
        _collect_run(
            run, aggregate_wait_deadline=aggregate_wait_deadline,
            aggregate_hard_deadline=aggregate_hard_deadline, clock=clock)
        for run in runs
    ]
    return parent, results


def _print_stream(label: str, value: str) -> None:
    print(f"{label}:", flush=True)
    if value:
        print(value, end="" if value.endswith("\n") else "\n", flush=True)
    else:
        print("<empty>", flush=True)


def _render_shard_map(shards: Mapping[str, tuple[str, ...]]) -> None:
    print(
        "LR-10 execution: 1 Taskplane Fix worker/native agent; "
        "5 internal parallel pytest subprocess shards",
        flush=True)
    print("LR-10 parallel shard map:", flush=True)
    for index, (name, selectors) in enumerate(shards.items(), 1):
        print(f"  {index:02d}-{name}: {len(selectors)} file(s)", flush=True)
        for selector in selectors:
            print(f"    {selector}", flush=True)


def _render_results(results: list[ShardResult]) -> int:
    failures: list[str] = []
    for result in results:
        exit_value = (
            "not-started" if result.returncode is None else result.returncode)
        print(
            f"\n[{result.shard_id}] status={result.status} "
            f"exit={exit_value} duration={result.duration_seconds:.3f}s",
            flush=True)
        _print_stream("stdout", result.stdout)
        _print_stream("stderr", result.stderr)
        if result.status != "passed":
            failures.append(result.shard_id)

    if failures:
        print("LR-10 failed shards: " + ", ".join(failures), flush=True)
        return 1
    print(f"LR-10 all {len(results)} shards passed", flush=True)
    return 0


def main() -> int:
    _render_shard_map(SHARDS)
    _, results = run_shards()
    return _render_results(results)


if __name__ == "__main__":
    raise SystemExit(main())
