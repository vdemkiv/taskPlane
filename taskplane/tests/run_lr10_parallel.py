#!/usr/bin/env python3
"""Run LR-10's declared pytest surface in isolated parallel shards."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable, Mapping, Sequence


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

LR10_SELECTORS = (
    "taskplane/tests/test_delivery_policy.py",
    "taskplane/tests/test_lens_route_policy.py",
    "taskplane/tests/test_lens_route_telemetry.py",
    "taskplane/tests/test_expanded_route_authority_provider.py",
    "taskplane/tests/test_expanded_lens_route_authority.py",
    "taskplane/tests/test_review_routing.py",
    "taskplane/tests/test_evaluation_output_contract.py",
    "taskplane/tests/test_evidence_bundle.py",
    "taskplane/tests/test_runtime_eval_guidance.py",
    "taskplane/tests/test_focused_lens_routing.py",
    "taskplane/tests/test_loop.py",
)
LR09_SELECTORS = (
    *LR10_SELECTORS,
    "taskplane/tests/test_agents_skills_focused_routing.py",
    "taskplane/tests/test_lens_routing_product_truth.py",
    "taskplane/tests/test_lens_routing_integration.py",
)
LR09_SHARDS = {
    "policy": (
        *SHARDS["policy"],
        "taskplane/tests/test_agents_skills_focused_routing.py",
    ),
    "authority": SHARDS["authority"],
    "review": (
        *SHARDS["review"],
        "taskplane/tests/test_lens_routing_product_truth.py",
    ),
    "evidence": (
        *SHARDS["evidence"],
        "taskplane/tests/test_lens_routing_integration.py",
    ),
    "loop": SHARDS["loop"],
}


@dataclass(frozen=True)
class RunnerProfile:
    name: str
    shards: Mapping[str, tuple[str, ...]]
    expected_selectors: tuple[str, ...]
    hermetic_pytest: bool


PROFILES = {
    "lr10": RunnerProfile(
        "lr10", SHARDS, LR10_SELECTORS, False,
    ),
    "lr09": RunnerProfile("lr09", LR09_SHARDS, LR09_SELECTORS, True),
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
    try:
        for index, name in enumerate(shards, 1):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
                raise RuntimeError("unsafe LR-10 shard name")
            child = parent / f"{index:02d}-{name}"
            child.mkdir(mode=0o700)
            roots[name] = _validate_child_root(parent, child)
    except BaseException:
        _cleanup_temp_tree(parent, roots)
        raise
    return parent, roots


def _cleanup_temp_tree(parent: Path, roots: Mapping[str, Path]) -> None:
    """Revalidate runner ownership, then remove the complete temporary tree."""
    if not parent.exists() and not parent.is_symlink():
        return
    validation_error: Exception | None = None
    try:
        resolved_parent = _validate_directory(
            parent, label="LR runner temp parent")
        for root in roots.values():
            if root.exists() or root.is_symlink():
                resolved_root = _validate_child_root(parent, root)
                if resolved_root.parent != resolved_parent:
                    raise RuntimeError(
                        "LR shard cleanup root escaped its runner parent")
    except Exception as exc:  # cleanup still removes only the owned parent
        validation_error = exc

    try:
        shutil.rmtree(parent)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError("could not remove LR runner temp tree") from exc
    if parent.exists() or parent.is_symlink():
        raise RuntimeError("LR runner temp tree cleanup was incomplete")
    if validation_error is not None:
        raise RuntimeError(
            "LR runner temp tree failed containment revalidation"
        ) from validation_error


def _validate_profile(profile: RunnerProfile) -> None:
    if not 3 <= len(profile.shards) <= 5:
        raise ValueError("usage: profile must contain three to five shards")
    assigned = [
        selector for selectors in profile.shards.values()
        for selector in selectors
    ]
    if (len(assigned) != len(set(assigned)) or
            len(assigned) != len(profile.expected_selectors) or
            set(assigned) != set(profile.expected_selectors)):
        raise ValueError(
            "usage: profile selectors must match exactly once")


def resolve_profile(argv: Sequence[str]) -> RunnerProfile:
    """Resolve the closed optional profile without hidden discovery."""
    values = list(argv)
    if not values:
        profile = PROFILES["lr10"]
    elif values == ["--profile", "lr09"]:
        profile = PROFILES["lr09"]
    else:
        raise ValueError(
            "usage: run_lr10_parallel.py [--profile lr09]")
    _validate_profile(profile)
    return profile


def _start(
        name: str, selectors: tuple[str, ...], temp_root: Path,
        *, popen_factory: Callable[..., subprocess.Popen[str]],
        hermetic_pytest: bool = False) \
        -> subprocess.Popen[str]:
    env = os.environ.copy()
    # The runner is governed, but its pytest children exercise fresh workspace
    # contract semantics and must not impersonate the runner's worker slot.
    env.pop("TASKPLANE_TASK", None)
    resolved = str(temp_root)
    env.update({"TMPDIR": resolved, "TEMP": resolved, "TMP": resolved})
    argv = [sys.executable, "-m", "pytest", "-q", "-x"]
    if hermetic_pytest:
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("PYTEST_PLUGINS", None)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        argv.extend(["-p", "no:cacheprovider"])
    argv.extend(selectors)
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

    if status != "timeout":
        if process.returncode is None:
            status = "incomplete"
        elif process.returncode != 0:
            status = "failed"
    return ShardResult(
        run.shard_id, run.name, run.selectors, status, process.returncode,
        stdout, stderr, max(0.0, clock() - run.started_at))


def run_shards(
        shards: Mapping[str, tuple[str, ...]] = SHARDS, *,
        popen_factory: Callable[..., subprocess.Popen[str]] | None = None,
        clock: Callable[[], float] | None = None,
        hermetic_pytest: bool = False) \
        -> tuple[Path, list[ShardResult]]:
    """Launch every shard before bounded collection, preserving all results."""
    popen_factory = popen_factory or subprocess.Popen
    clock = clock or time.monotonic
    aggregate_started_at = clock()
    parent: Path | None = None
    roots: dict[str, Path] = {}
    runs: list[ShardRun] = []
    try:
        parent, roots = _create_temp_roots(shards)
        for index, (name, selectors) in enumerate(shards.items(), 1):
            started_at = clock()
            run = ShardRun(
                f"{index:02d}-{name}", name, tuple(selectors), roots[name],
                started_at)
            try:
                run.process = _start(
                    name, run.selectors, run.temp_root,
                    popen_factory=popen_factory,
                    hermetic_pytest=hermetic_pytest)
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
    finally:
        active_exception = sys.exc_info()[0] is not None
        for run in runs:
            _stop_live_process(run.process)
        if parent is not None:
            try:
                _cleanup_temp_tree(parent, roots)
            except RuntimeError:
                if not active_exception:
                    raise


def _stop_live_process(process: subprocess.Popen[str] | None) -> None:
    """Boundedly terminate and collect a child left live by any exit path."""
    if process is None or getattr(process, "returncode", None) is not None:
        return
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.communicate(timeout=TERMINATE_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        return
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.communicate(timeout=TERMINATE_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _print_stream(label: str, value: str) -> None:
    print(f"{label}:", flush=True)
    if value:
        print(value, end="" if value.endswith("\n") else "\n", flush=True)
    else:
        print("<empty>", flush=True)


def _render_shard_map(
        shards: Mapping[str, tuple[str, ...]], *, label: str = "LR-10",
        worker_role: str = "Fix") -> None:
    print(
        f"{label} execution: 1 Taskplane {worker_role} worker/native agent; "
        "5 internal parallel pytest subprocess shards",
        flush=True)
    print(f"{label} parallel shard map:", flush=True)
    for index, (name, selectors) in enumerate(shards.items(), 1):
        print(f"  {index:02d}-{name}: {len(selectors)} file(s)", flush=True)
        for selector in selectors:
            print(f"    {selector}", flush=True)


def _render_results(
        results: list[ShardResult], *, label: str = "LR-10") -> int:
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
        print(f"{label} failed shards: " + ", ".join(failures), flush=True)
        return 1
    print(f"{label} all {len(results)} shards passed", flush=True)
    return 0


def validate_results(
        shards: Mapping[str, tuple[str, ...]],
        results: Sequence[ShardResult]) -> None:
    expected = [
        (f"{index:02d}-{name}", name, tuple(selectors))
        for index, (name, selectors) in enumerate(shards.items(), 1)
    ]
    actual = [
        (result.shard_id, result.name, result.selectors)
        for result in results
    ]
    if actual != expected:
        raise RuntimeError("incomplete or inconsistent shard result collection")
    allowed_statuses = {"passed", "failed", "timeout", "startup-error",
                        "incomplete"}
    for result in results:
        if result.status not in allowed_statuses:
            raise RuntimeError("incomplete or invalid shard status")
        if result.status == "passed" and result.returncode != 0:
            raise RuntimeError("incomplete shard reported a false pass")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        profile = resolve_profile(sys.argv[1:] if argv is None else argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 2

    label = profile.name.upper().replace("LR", "LR-")
    role = "Build" if profile.name == "lr09" else "Fix"
    _render_shard_map(profile.shards, label=label, worker_role=role)
    try:
        _, results = run_shards(
            profile.shards, hermetic_pytest=profile.hermetic_pytest)
        validate_results(profile.shards, results)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"{label} runner failed closed: {exc}", file=sys.stderr,
              flush=True)
        return 1
    return _render_results(results, label=label)


if __name__ == "__main__":
    raise SystemExit(main())
