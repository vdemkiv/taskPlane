#!/usr/bin/env python3
"""Closed, isolated local equivalent of Taskplane's blocking CI.

This runner intentionally has no discovery mechanism: INVENTORY is the whole
contract. The canonical CI profile runs the pytest inventory once; explicit
non-authoritative profiles may still use the supported sharding capability.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, NamedTuple, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taskplane.ci_policy import (  # noqa: E402
    BROWSER_INPUTS,
    build_ci_plan,
    freeze_candidate,
)
from taskplane.settings import (  # noqa: E402
    DEFAULT_SETTINGS_PATH,
    OperationalSettings,
    SettingsError,
    load_settings,
)

PYTHON = sys.executable
SCHEMA = "taskplane.local-ci-equivalent/v1"
CI_RUNTIME_SCHEMA = "taskplane.authoritative-ci-runtime/v1"
CI_CELL_SCHEMA = "taskplane.authoritative-ci-cell/v1"
CI_CLEANUP_SCHEMA = "taskplane.ci-cleanup-receipt/v1"
CI_FAILURE_CLASSES = frozenset(("product", "test", "infrastructure", "environment"))
CI_TERMINAL_OUTCOMES = frozenset((
    "success", "failure", "cancellation", "interruption", "timeout", "handoff",
))
CI_CELL_FIELDS = frozenset((
    "schema", "id", "kind", "status", "outcome", "classification",
    "candidate_fingerprint", "source_sha", "plan_fingerprint",
    "settings_receipt_fingerprint", "environment", "browser_fingerprint",
    "browser_observation", "selectors", "duration_ms", "commands",
    "output_digest", "ownership", "cleanup", "receipt",
))
CI_BROWSER_SELECTORS = (
    "taskplane/tests/test_dashboard_browser.py::"
    "test_real_browser_replaces_dom_only_for_newer_snapshot_and_marks_stale",
    "taskplane/tests/test_dashboard_browser.py::"
    "test_real_browser_svg_graphs_and_single_document_are_truthful",
)
CI_MATRICES = ("tests", "quality-package", "browser")
CI_RUNNER_MINUTES_CEILING = 30
CI_RECEIPT_RESERVE_SECONDS = 60
CI_LOGICAL_PYTHON = "taskplane-python"
INVENTORY_VERSION = "REL-2181/2"
RECURSION_GUARD = "TASKPLANE_LOCAL_CI_ACTIVE"
PACKAGE_TEMP_ROOT = "TASKPLANE_PACKAGE_TEMP_ROOT"
TEMP_PREFIX = "taskplane-local-ci-"
DEADLINE_SECONDS = 13_800
CLEANUP_RESERVE_SECONDS = 600


class RunnerError(RuntimeError):
    pass


class Check(NamedTuple):
    id: str
    argv: tuple[str, ...]
    missing_tool: str | None = None


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(canonical_json(value).encode("utf-8"))


def _files_fingerprint(paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(paths):
        path = ROOT / name
        if not path.is_file():
            raise RunnerError(f"CI inventory path is unavailable: {name}")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _ci_pytest_partitions(shard_count: int) -> tuple[tuple[str, ...], ...]:
    files = tuple(
        path for path in pytest_inventory()
        if path != "taskplane/tests/test_dashboard_browser.py"
    )
    weights = {path: max(1, (ROOT / path).stat().st_size) for path in files}
    return partition_pytest_files(files, weights, shard_count)


def _browser_identity(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    fixture_path = (
        ROOT / "taskplane" / "tests" / "fixtures" /
        "dashboard-browser" / "environment.json"
    )
    try:
        config = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RunnerError("browser runtime declaration is unavailable") from exc
    executable = ""
    for name in config.get("executable_environment", []):
        if env.get(str(name)):
            executable = os.path.abspath(env[str(name)])
            break
    if not executable:
        executable = next(
            (
                os.path.abspath(str(path))
                for path in config.get("executable_candidates", [])
                if os.path.isfile(str(path)) and os.access(str(path), os.X_OK)
            ),
            "",
        )
    if not executable or not os.path.isfile(executable) or not os.access(
        executable, os.X_OK
    ):
        raise RunnerError(
            "browser environment failure: no declared Chrome/Chromium "
            "executable is available"
        )
    try:
        result = subprocess.run(
            [executable, "--version"], text=True, encoding="utf-8",
            errors="replace", capture_output=True, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError("browser environment version probe failed") from exc
    version = (result.stdout or result.stderr).strip()
    if result.returncode or not version:
        raise RunnerError("browser environment version is unavailable")
    fixture_server = _sha256_json(config.get("fixture_server"))
    selectors = _sha256_json(config.get("selectors"))
    snapshot = _files_fingerprint((
        "taskplane/tests/fixtures/dashboard-browser/environment.json",
        "taskplane/tests/fixtures/dashboard-browser/topology.json",
    ))
    dashboard_artifact = _files_fingerprint((
        "taskplane/dashboard.py", "taskplane/views.py",
    ))
    identity = {
        "executable": executable,
        "version": version,
        "flags": list(config.get("flags") or []),
        "fixture_server": fixture_server,
        "snapshot": snapshot,
        "dashboard_artifact": dashboard_artifact,
        "selectors": selectors,
    }
    if set(identity) != set(BROWSER_INPUTS):
        raise RunnerError("browser environment declaration is incomplete")
    return identity


def _ci_settings(
    settings_path: str | Path = DEFAULT_SETTINGS_PATH,
) -> OperationalSettings:
    try:
        settings = load_settings(settings_path, environment={})
    except SettingsError as exc:
        raise RunnerError(f"authoritative CI settings were rejected: {exc}") from exc
    if (
        settings.tests.backend != "ci"
        or settings.tests.shards < 1
        or settings.build.concurrency != "native"
        or settings.receipt.get("precedence") != ["defaults", "file"]
    ):
        raise RunnerError("canonical authoritative CI settings are unsupported")
    return settings


def _ci_declaration(
    settings: OperationalSettings,
    *,
    event: str,
    ref: str,
    run_id: str,
) -> dict[str, Any]:
    if event == "pull_request":
        ref_kind = "pull-request"
        group = f"pull-request-{ref}"
        cancel = True
    elif ref == "refs/heads/main":
        ref_kind = "protected-main"
        group = f"protected-main-{run_id}"
        cancel = False
    else:
        ref_kind = "release"
        group = f"release-{run_id}"
        cancel = False
    partitions = _ci_pytest_partitions(settings.tests.shards)
    test_timeout = settings.limits.timeouts["task_seconds"]
    subprocess_timeout = settings.limits.timeouts["subprocess_seconds"]
    cells: list[dict[str, Any]] = []
    for index, selectors in enumerate(partitions, start=1):
        cells.append({
            "id": f"pytest-{index}",
            "kind": "pytest",
            "matrix": "tests",
            "selectors": list(selectors),
            "paths": list(selectors),
            "timeout_seconds": test_timeout,
            "cleanup_resources": [f"generated-state:pytest-{index}"],
        })
    cells.extend((
        {
            "id": "quality-package",
            "kind": "quality-package",
            "matrix": "quality-package",
            "selectors": [
                "command:compile-import", "command:ruff", "command:mypy",
                "command:release-surface", "command:package-openai",
                "command:package-claude",
            ],
            "paths": [
                "requirements-dev.lock", "pyproject.toml",
                "scripts/package_openai.py", "scripts/package_claude.py",
            ],
            "timeout_seconds": subprocess_timeout,
            "cleanup_resources": ["generated-state:quality-package"],
        },
        {
            "id": "dashboard-browser",
            "kind": "browser",
            "matrix": "browser",
            "execution": "ci-only",
            "selectors": list(CI_BROWSER_SELECTORS),
            "paths": [
                "taskplane/tests/test_dashboard_browser.py",
                "taskplane/tests/fixtures/dashboard-browser",
            ],
            "timeout_seconds": subprocess_timeout,
            "cleanup_resources": [
                "process:dashboard-browser", "generated-state:dashboard-browser",
            ],
        },
    ))
    return {
        "settings": {**settings.to_dict(), "digest": settings.digest},
        "run": {
            "event": event,
            "ref_kind": ref_kind,
            "group": group,
            "cancel_in_progress": cancel,
        },
        "matrices": list(CI_MATRICES),
        "serializations": [],
        "cells": cells,
    }


def build_authoritative_ci_runtime(
    *,
    source_sha: str,
    event: str,
    ref: str,
    run_id: str,
    settings_path: str | Path = DEFAULT_SETTINGS_PATH,
    browser: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if source_sha != _git("rev-parse", "HEAD"):
        raise RunnerError("authoritative CI source SHA is not the checked out HEAD")
    settings = _ci_settings(settings_path)
    declaration = _ci_declaration(
        settings, event=event, ref=ref, run_id=run_id,
    )
    tracked_tests = pytest_inventory()
    fingerprints = {
        "source": _sha256_json({"source_sha": source_sha}),
        "tests": _files_fingerprint(tracked_tests),
        "settings": settings.digest,
        "inventory": _sha256_json(tracked_tests),
        "selector": _sha256_json([
            cell["selectors"] for cell in declaration["cells"]
        ]),
        "radius": _sha256_json({
            "selection": settings.tests.selection,
            "paths": [cell["paths"] for cell in declaration["cells"]],
        }),
        "shard-plan": _sha256_json(declaration),
        "runner": _sha256_json({
            "implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "platform": platform.system(),
        }),
        "environment": _sha256_json({"event": event, "ref": ref}),
    }
    candidate = freeze_candidate({
        "source_sha": source_sha,
        "fingerprints": fingerprints,
        "browser": dict(browser) if browser is not None else _browser_identity(),
    })
    plan = build_ci_plan(candidate, declaration)
    if sum(cell["timeout_seconds"] for cell in plan["cells"]) > (
        CI_RUNNER_MINUTES_CEILING * 60
    ):
        raise RunnerError("authoritative CI runner-minute ceiling was exceeded")
    settings_receipt = {
        "schema": "taskplane.authoritative-ci-settings-receipt/v1",
        "source": str(Path(settings_path).resolve()),
        "precedence": list(settings.receipt["precedence"]),
        "candidate_sha": source_sha,
        "settings_digest": settings.digest,
        "effective": settings.to_dict(),
        "loader_receipt": dict(settings.receipt),
    }
    settings_receipt["fingerprint"] = _sha256_json(settings_receipt)
    payload = {
        "schema": CI_RUNTIME_SCHEMA,
        "candidate": candidate,
        "settings_receipt": settings_receipt,
        "plan": plan,
    }
    return {**payload, "fingerprint": _sha256_json(payload)}


PYTEST_SHARD_COUNT = load_settings(
    DEFAULT_SETTINGS_PATH, environment={},
).tests.shards
PYTEST_CHECK_IDS = tuple(
    f"pytest-shard-{index + 1}" for index in range(PYTEST_SHARD_COUNT)
)
def pytest_inventory() -> tuple[str, ...]:
    tracked = subprocess.run(
        ["git", "ls-files", "--", "taskplane/tests/test_*.py"],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.splitlines()
    files = tuple(sorted(row for row in tracked if row))
    present = tuple(sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "taskplane" / "tests").glob("test_*.py")
        if path.is_file()
    ))
    if not files or files != present or len(files) != len(set(files)):
        raise RunnerError("pytest inventory is missing, duplicate, or untracked")
    return files


def pytest_weights(files: Sequence[str]) -> dict[str, int]:
    return {
        path: max(1, (ROOT / path).stat().st_size)
        for path in files
    }


def partition_pytest_files(
    files: Sequence[str], weights: dict[str, int], shard_count: int,
) -> tuple[tuple[str, ...], ...]:
    if len(files) != len(set(files)):
        raise RunnerError("duplicate pytest file inventory row")
    if set(files) != set(weights) or any(
        not isinstance(weight, int) or weight <= 0 for weight in weights.values()
    ):
        raise RunnerError("pytest weight inventory is absent or stale")
    if shard_count < 1 or len(files) < shard_count:
        raise RunnerError("pytest partitions must all be nonempty")
    partitions: list[list[str]] = [[] for _ in range(shard_count)]
    loads = [0] * shard_count
    for path in sorted(files, key=lambda row: (-weights[row], row)):
        target = min(range(shard_count), key=lambda index: (loads[index], index))
        partitions[target].append(path)
        loads[target] += weights[path]
    largest = max(weights.values())
    if max(loads) - min(loads) > largest:
        raise RunnerError("pytest partition estimate is unbalanced")
    result = tuple(tuple(sorted(partition)) for partition in partitions)
    assigned = [path for partition in result for path in partition]
    if sorted(assigned) != sorted(files) or len(assigned) != len(set(assigned)):
        raise RunnerError("pytest file assignment is missing or duplicate")
    return result


PYTEST_FILES = pytest_inventory()
PYTEST_WEIGHTS = pytest_weights(PYTEST_FILES)
PYTEST_PARTITIONS = partition_pytest_files(
    PYTEST_FILES, PYTEST_WEIGHTS, PYTEST_SHARD_COUNT,
)
PYTEST_CHECKS = tuple(
    Check(check_id, (PYTHON, "-m", "pytest", *partition, "-q"))
    for check_id, partition in zip(PYTEST_CHECK_IDS, PYTEST_PARTITIONS)
)


INVENTORY = (
    Check("compile-import", (PYTHON, __file__, "--internal", "compile-import")),
    Check("version-verify", (PYTHON, "taskplane/tp.py", "version", "--verify")),
    Check("zero-token-corpus", (PYTHON, __file__, "--internal", "zero-token-corpus")),
    *PYTEST_CHECKS,
    Check("release-surface", (PYTHON, "scripts/ci_evals.py", "--verify-release-surface", "--json")),
    Check("release-history", (PYTHON, "scripts/ci_release_tags.py", "--json")),
    Check("unittest-canary", (PYTHON, "-m", "unittest", "taskplane.tests.test_runner_isolation.TestUnittestRunnerIsolation", "-v")),
    Check("loop-cost", (PYTHON, "scripts/ci_loop_cost.py")),
    Check("import-cycle-current", (PYTHON, "taskplane/import_cycles.py", "--root", ".", "--policy", "taskplane/tests/fixtures/import-cycles.json", "--check")),
    Check("generated-lens-drift", (PYTHON, __file__, "--internal", "generated-lens-drift")),
    Check("generated-cli-drift", (PYTHON, __file__, "--internal", "generated-cli-drift")),
    Check("package-openai", (PYTHON, __file__, "--internal", "package-openai")),
    Check("package-claude", (PYTHON, __file__, "--internal", "package-claude")),
    Check("ruff", (PYTHON, "-m", "ruff", "check", "taskplane", "hooks", "scripts"), "ruff"),
    Check("mypy", (PYTHON, "-m", "mypy", "--strict", "--config-file", "pyproject.toml"), "mypy"),
    Check("host-platform", (PYTHON, __file__, "--internal", "host-platform")),
)

AUXILIARY_CHECK_IDS = (
    "compile-import", "generated-lens-drift", "ruff", "version-verify",
    "release-surface", "generated-cli-drift", "mypy", "zero-token-corpus",
    "release-history", "package-openai", "host-platform", "unittest-canary",
    "loop-cost", "import-cycle-current", "package-claude",
)


def _local_shards() -> tuple[tuple[str, ...], ...]:
    if PYTEST_SHARD_COUNT < 1:
        raise RunnerError("canonical tests.shards must be positive")
    rows = [[check_id] for check_id in PYTEST_CHECK_IDS]
    for index, check_id in enumerate(AUXILIARY_CHECK_IDS):
        rows[index % PYTEST_SHARD_COUNT].append(check_id)
    return tuple(tuple(row) for row in rows)


SHARDS = _local_shards()
CHECKS = {check.id: check for check in INVENTORY}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(output: str) -> str:
    normalized = output.replace(str(ROOT), "<ROOT>").replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=True,
    )
    return result.stdout.strip()


def _safe_env(shard_root: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(shard_root / "home"),
        "TMPDIR": str(shard_root / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        PACKAGE_TEMP_ROOT: str(shard_root.resolve(strict=True)),
        RECURSION_GUARD: "1",
    }
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
            if name in os.environ:
                env[name] = os.environ[name]
    return env


def _run(check: Check, shard_root: Path) -> dict[str, object]:
    started = time.monotonic()
    if check.missing_tool and importlib.util.find_spec(check.missing_tool) is None:
        output = "locked tool unavailable locally; hosted CI required"
        return _result(check, "remote-required", started, output)
    try:
        proc = subprocess.run(
            list(check.argv), cwd=ROOT, env=_safe_env(shard_root),
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        status = "passed" if proc.returncode == 0 else "failed"
        return _result(check, status, started, proc.stdout)
    except OSError as exc:
        return _result(check, "failed", started, f"child start error: {exc}")


def _result(check: Check, status: str, started: float, output: str) -> dict[str, object]:
    return {
        "check_id": check.id,
        "argv": list(check.argv),
        "status": status,
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
        "output_digest": _digest(output),
        "output_tail": output[-2000:],
    }


def _worker(shard_index: int, shard_root: Path, result_path: Path) -> int:
    results = []
    for check_id in SHARDS[shard_index]:
        results.append(_run(CHECKS[check_id], shard_root))
        temporary = result_path.with_suffix(".tmp")
        temporary.write_text(canonical_json(results), encoding="utf-8")
        temporary.replace(result_path)
    return 0 if all(row["status"] != "failed" for row in results) else 1


def validate_results(expected: Sequence[str], results: Sequence[dict[str, object]]) -> None:
    seen: set[str] = set()
    for row in results:
        check_id = row.get("check_id")
        if not isinstance(check_id, str) or check_id not in expected:
            raise RunnerError("malformed result check id")
        if check_id in seen:
            raise RunnerError(f"duplicate result: {check_id}")
        seen.add(check_id)
        if row.get("status") not in {"passed", "failed", "remote-required"}:
            raise RunnerError(f"malformed result status: {check_id}")
        if not isinstance(row.get("argv"), list):
            raise RunnerError(f"malformed result argv: {check_id}")
        duration = row.get("duration_ms")
        digest = row.get("output_digest")
        if not isinstance(duration, int) or duration < 0:
            raise RunnerError(f"malformed result duration: {check_id}")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise RunnerError(f"malformed result digest: {check_id}")
    missing = sorted(set(expected) - seen)
    if missing:
        raise RunnerError("missing result: " + ", ".join(missing))


def build_report(source_sha: str, results: Sequence[dict[str, object]], *, checkout_mutated: bool) -> dict[str, object]:
    ordered = sorted(results, key=lambda row: list(CHECKS).index(str(row["check_id"])) if row["check_id"] in CHECKS else str(row["check_id"]))
    receipts = []
    for row in ordered:
        receipt = {key: value for key, value in row.items() if key != "output_tail"}
        receipt.update({"source_sha": source_sha, "inventory_version": INVENTORY_VERSION})
        receipt["fingerprint"] = hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
        receipts.append(receipt)
    failed = [row["check_id"] for row in receipts if row["status"] == "failed"]
    remote = [row["check_id"] for row in receipts if row["status"] == "remote-required"]
    if checkout_mutated:
        status = "failed"
    elif failed:
        status = "failed"
    elif remote:
        status = "local-green/remote-required"
    else:
        status = "local-green"
    report = {
        "schema": SCHEMA,
        "inventory_version": INVENTORY_VERSION,
        "source_sha": source_sha,
        "status": status,
        "full_green": False,
        "checkout_mutated": checkout_mutated,
        "failed_checks": failed,
        "remote_required_checks": remote,
        "pytest_partition": [
            {
                "check_id": check_id,
                "estimated_weight": sum(PYTEST_WEIGHTS[path] for path in partition),
                "file_count": len(partition),
                "files": list(partition),
            }
            for check_id, partition in zip(PYTEST_CHECK_IDS, PYTEST_PARTITIONS)
        ],
        "receipts": receipts,
    }
    report["fingerprint"] = hashlib.sha256(canonical_json(report).encode()).hexdigest()
    return report


def cleanup_root(path: Path, owner: Path) -> None:
    owner_resolved = owner.resolve(strict=True)
    if path.is_symlink():
        raise RunnerError("cleanup root is a symlink")
    resolved = path.resolve(strict=True)
    if resolved.parent != owner_resolved or not resolved.name.startswith(TEMP_PREFIX):
        raise RunnerError("cleanup containment validation failed")
    shutil.rmtree(resolved)


def _terminate_all(children: Sequence[subprocess.Popen[bytes]]) -> None:
    for child in children:
        if child.poll() is None:
            if os.name == "posix":
                os.killpg(child.pid, signal.SIGTERM)
            else:
                child.terminate()
    until = time.monotonic() + 5
    for child in children:
        if child.poll() is None:
            try:
                child.wait(max(0, until - time.monotonic()))
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(child.pid, signal.SIGKILL)
            else:
                child.kill()
            child.wait()


def _collect(source_sha: str, before: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    owner = Path(tempfile.gettempdir()).resolve()
    roots: list[Path] = []
    children: list[subprocess.Popen[bytes]] = []
    result_paths: list[Path] = []
    logs = []
    results: list[dict[str, object]] = []
    deadline = time.monotonic() + DEADLINE_SECONDS
    timed_out = False
    try:
        for index in range(len(SHARDS)):
            root = Path(tempfile.mkdtemp(prefix=f"{TEMP_PREFIX}{index}-", dir=owner))
            roots.append(root)
            (root / "home").mkdir()
            (root / "tmp").mkdir()
            result_path = root / "results.json"
            result_paths.append(result_path)
            log = (root / "worker.log").open("wb")
            logs.append(log)
            try:
                child = subprocess.Popen(
                    [PYTHON, __file__, "--worker", str(index), "--shard-root", str(root), "--result", str(result_path)],
                    cwd=ROOT, env=_safe_env(root), stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                children.append(child)
            except OSError:
                for check_id in SHARDS[index]:
                    results.append(_result(CHECKS[check_id], "failed", time.monotonic(), "child start error"))
        for child in children:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
                break
        if timed_out:
            _terminate_all(children)
        for result_path in result_paths:
            if not result_path.is_file():
                continue
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                if not isinstance(payload, list):
                    raise ValueError("not a list")
                results.extend(payload)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        if timed_out:
            seen = {str(row.get("check_id")) for row in results}
            for check in INVENTORY:
                if check.id not in seen:
                    results.append(_result(check, "failed", time.monotonic(), "internal deadline exceeded"))
        try:
            validate_results(tuple(CHECKS), results)
        except RunnerError as exc:
            seen = {str(row.get("check_id")) for row in results}
            for check in INVENTORY:
                if check.id not in seen:
                    results.append(_result(check, "failed", time.monotonic(), str(exc)))
            # duplicates/malformed results remain an aggregate failure.
            if len(results) != len(INVENTORY):
                results = [_result(check, "failed", time.monotonic(), str(exc)) for check in INVENTORY]
        after = _git("status", "--porcelain=v1", "--untracked-files=all")
        report = build_report(source_sha, results, checkout_mutated=after != before)
        return report, results
    finally:
        _terminate_all(children)
        for log in logs:
            log.close()
        for root in roots:
            if root.exists():
                cleanup_root(root, owner)


def _copy_checkout(destination: Path) -> None:
    subprocess.run(
        ["git", "archive", "--format=tar", "HEAD", "-o", str(destination / "tree.tar")],
        cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    shutil.unpack_archive(str(destination / "tree.tar"), destination / "tree")


def _tree_digest(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for index, path in enumerate(paths):
        files = [path] if path.is_file() else sorted(
            file for file in path.rglob("*") if file.is_file()
        )
        for file in files:
            relative = file.name if path.is_file() else str(file.relative_to(path))
            digest.update(f"{index}:{relative}\0".encode())
            digest.update(file.read_bytes())
    return digest.hexdigest()


def _internal(action: str) -> int:
    if action == "compile-import":
        tracked = _git("ls-files", "--", "taskplane/*.py", "hooks/*.py").splitlines()
        shipped = sorted(Path(row) for row in tracked if Path(row).suffix == ".py" and len(Path(row).parts) == 2)
        for relative in shipped:
            compile((ROOT / relative).read_text(encoding="utf-8"), str(relative), "exec")
        sys.path[:0] = [str(ROOT), str(ROOT / "taskplane")]
        for relative in shipped:
            __import__(".".join(relative.with_suffix("").parts))
        print(f"compiled and imported {len(shipped)} entries")
        return 0
    if action == "zero-token-corpus":
        outputs = []
        for _ in range(2):
            result = subprocess.run([PYTHON, "scripts/ci_evals.py", "--corpus"], cwd=ROOT, env=_safe_env(Path(os.environ["TMPDIR"]).parent), capture_output=True, text=True)
            if result.returncode:
                print(result.stdout + result.stderr)
                return result.returncode
            outputs.append(result.stdout)
        if outputs[0] != outputs[1]:
            print("nondeterministic corpus output")
            return 1
        print(_digest(outputs[0]))
        return 0
    if action == "host-platform":
        print(canonical_json({"python": platform.python_version(), "implementation": platform.python_implementation(), "os": os.name, "platform": platform.system(), "disposition": "remote-required"}))
        return 0
    shard_root = Path(os.environ["TMPDIR"]).parent
    if action in {"generated-lens-drift", "generated-cli-drift"}:
        copy_root = shard_root / action
        copy_root.mkdir()
        _copy_checkout(copy_root)
        tree = copy_root / "tree"
        observed = (tree / "lenses", tree / "docs/lens-catalog.md")
        before_digest = _tree_digest(observed)
        commands = (
            ([PYTHON, "lenses/_generate_catalog.py"], [PYTHON, "lenses/_generate_lens_prompts.py"], [PYTHON, "scripts/gen_lens_catalog.py", "--check"])
            if action == "generated-lens-drift" else
            ([PYTHON, "taskplane/tp.py", "help", "--md"],)
        )
        if action == "generated-cli-drift":
            result = subprocess.run(commands[0], cwd=tree, env=_safe_env(shard_root), capture_output=True, text=True)
            expected = (tree / "docs/cli-reference.md").read_text(encoding="utf-8")
            return 0 if result.returncode == 0 and result.stdout == expected else 1
        for command in commands:
            result = subprocess.run(command, cwd=tree, env=_safe_env(shard_root), check=False)
            if result.returncode:
                return result.returncode
        return 0 if _tree_digest(observed) == before_digest else 1
    if action in {"package-openai", "package-claude"}:
        script = "scripts/package_openai.py" if action.endswith("openai") else "scripts/package_claude.py"
        digests = []
        for label in ("a", "b"):
            output = shard_root / f"{action}-{label}"
            result = subprocess.run([PYTHON, script, "--output-dir", str(output)], cwd=ROOT, env=_safe_env(shard_root))
            if result.returncode:
                return result.returncode
            archives = sorted(list(output.glob("*.zip")) + list(output.glob("*.plugin")))
            if len(archives) != 1:
                return 1
            digests.append(hashlib.sha256(archives[0].read_bytes()).hexdigest())
        print(digests[0])
        return 0 if digests[0] == digests[1] else 1
    raise RunnerError(f"unknown internal action: {action}")


def _load_runtime_plan(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RunnerError("authoritative CI runtime plan is unavailable") from exc
    if not isinstance(value, dict) or value.get("schema") != CI_RUNTIME_SCHEMA:
        raise RunnerError("authoritative CI runtime plan schema is invalid")
    expected = _sha256_json({
        key: item for key, item in value.items() if key != "fingerprint"
    })
    if value.get("fingerprint") != expected:
        raise RunnerError("authoritative CI runtime plan is stale or tampered")
    candidate = value.get("candidate")
    plan = value.get("plan")
    receipt = value.get("settings_receipt")
    if not all(isinstance(item, dict) for item in (candidate, plan, receipt)):
        raise RunnerError("authoritative CI runtime plan is incomplete")
    if (
        plan.get("candidate_fingerprint") != candidate.get("fingerprint")
        or plan.get("source_sha") != candidate.get("source_sha")
        or receipt.get("candidate_sha") != candidate.get("source_sha")
        or receipt.get("settings_digest") != plan.get("settings_digest")
    ):
        raise RunnerError("CI plan, candidate, and settings receipt do not match")
    return value


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(canonical_json(value) + "\n", encoding="utf-8")
    temporary.replace(path)


def _ci_cell_commands(cell: Mapping[str, Any], root: Path) -> list[list[str]]:
    """Return the sealed logical command contract, independent of runner paths."""
    PYTHON = CI_LOGICAL_PYTHON  # noqa: N806 - contract token, not executable path
    kind = cell.get("kind")
    selectors = [str(item) for item in cell.get("selectors") or []]
    if kind in {"pytest", "browser"}:
        return [[PYTHON, "-m", "pytest", "-q", *selectors]]
    if kind == "quality-package":
        return [
            [PYTHON, __file__, "--internal", "compile-import"],
            [PYTHON, "-m", "ruff", "check", "--output-format=github",
             "taskplane", "hooks", "scripts"],
            [PYTHON, "-m", "mypy", "--strict", "--config-file", "pyproject.toml"],
            [PYTHON, "scripts/ci_evals.py", "--verify-release-surface", "--json"],
            [PYTHON, "scripts/package_openai.py", "--output-dir", str(root / "openai")],
            [PYTHON, "scripts/package_claude.py", "--output-dir", str(root / "claude")],
        ]
    raise RunnerError(f"unsupported authoritative CI cell kind: {kind}")


def _materialize_ci_cell_command(command: Sequence[str]) -> list[str]:
    if not command or command[0] != CI_LOGICAL_PYTHON:
        raise RunnerError("CI command contract has an unknown interpreter identity")
    return [PYTHON, *command[1:]]


def _classify_ci_failure(status: str, output: str) -> str | None:
    if status == "green":
        return None
    folded = output.casefold()
    if "environment failure" in folded or "no declared chrome" in folded:
        return "environment"
    if any(term in folded for term in (
        "no space left", "runner", "network", "temporary failure",
        "connection reset", "infrastructure failure",
    )):
        return "infrastructure"
    if any(term in folded for term in ("fixture", "assertionerror: test", "collection error")):
        return "test"
    return "product"


def _owned_cell_root(
    runtime: Mapping[str, Any], cell_id: str, owner: Path,
) -> tuple[Path, dict[str, Any]]:
    owner = owner.resolve(strict=True)
    candidate = runtime["candidate"]
    # Chrome creates a process-singleton Unix socket below TMPDIR.  The old
    # human-readable name left too little of AF_UNIX's path budget once pytest
    # added its own temporary-directory segments.  The full candidate and
    # cell identities remain in the signed registration; this short name is a
    # deterministic content address, not a second identity authority.
    name_material = canonical_json({
        "candidate_fingerprint": candidate["fingerprint"],
        "cell_id": cell_id,
    }).encode("utf-8")
    name_token = base64.urlsafe_b64encode(
        hashlib.sha256(name_material).digest()[:3]
    ).decode("ascii")
    name = f"taskplane-ci-{name_token}"
    target = owner / name
    if target.exists() or target.is_symlink():
        raise RunnerError("owned CI cell root already exists")
    registration = {
        "schema": "taskplane.ci-owned-cell/v1",
        "candidate_fingerprint": candidate["fingerprint"],
        "source_sha": candidate["source_sha"],
        "cell_id": cell_id,
        "containment_root": str(owner),
        "relative_name": name,
        "registered_before_run": True,
    }
    registration["fingerprint"] = _sha256_json(registration)
    return target, registration


def _cleanup_ci_cell_root(
    target: Path, registration: Mapping[str, Any], *, outcome: str = "success",
) -> dict[str, Any]:
    if outcome not in CI_TERMINAL_OUTCOMES:
        raise RunnerError("CI cleanup terminal outcome is invalid")
    material = {key: value for key, value in registration.items()
                if key != "fingerprint"}
    if registration.get("fingerprint") != _sha256_json(material):
        raise RunnerError("CI cleanup ownership registration is invalid")
    root = Path(str(registration.get("containment_root") or ""))
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise RunnerError("CI cleanup containment root is unavailable") from exc
    if target.is_symlink():
        raise RunnerError("CI cleanup target is a symlink")
    expected = root / str(registration.get("relative_name") or "")
    if target != expected or target.parent != root or not target.name.startswith(
        "taskplane-ci-"
    ):
        raise RunnerError("CI cleanup target is ambiguous or unowned")
    if target.exists():
        shutil.rmtree(target)
    leaks = [str(target)] if target.exists() or target.is_symlink() else []
    receipt = {
        "schema": CI_CLEANUP_SCHEMA,
        "registration_fingerprint": registration["fingerprint"],
        "outcome": outcome,
        "resources": [str(target)],
        "status": "clean" if not leaks else "attention",
        "leak_count": len(leaks),
        "leaks": leaks,
    }
    return {**receipt, "fingerprint": _sha256_json(receipt)}


def run_authoritative_ci_cell(
    runtime_path: Path,
    cell_id: str,
    receipt_path: Path,
    *,
    environ: Mapping[str, str] | None = None,
    forced_outcome: str | None = None,
) -> int:
    if forced_outcome is not None and forced_outcome not in {
        "cancellation", "interruption", "handoff",
    }:
        raise RunnerError("forced CI terminal outcome is unsupported")
    runtime = _load_runtime_plan(runtime_path)
    candidate = runtime["candidate"]
    if _git("rev-parse", "HEAD") != candidate["source_sha"]:
        raise RunnerError("CI cell checkout is not the frozen candidate")
    matches = [cell for cell in runtime["plan"]["cells"]
               if cell.get("id") == cell_id]
    if len(matches) != 1:
        raise RunnerError("CI cell is absent or ambiguous in the frozen plan")
    cell = matches[0]
    env = dict(os.environ if environ is None else environ)
    runner_temp = Path(env.get("RUNNER_TEMP") or tempfile.gettempdir())
    runner_temp.mkdir(parents=True, exist_ok=True)
    target, registration = _owned_cell_root(runtime, cell_id, runner_temp)
    registration_path = receipt_path.with_suffix(".ownership.json")
    _atomic_write_json(registration_path, registration)
    target.mkdir()
    (target / "home").mkdir()
    (target / "tmp").mkdir()
    safe_env = _safe_env(target)
    if cell.get("kind") == "browser":
        safe_env["TASKPLANE_BROWSER_EXECUTABLE"] = str(
            candidate["browser"]["executable"]
        )
    started = time.monotonic()
    # Leave a settings-bounded minute for cleanup, receipt publication, and
    # the workflow artifact step before GitHub reaches the same job timeout.
    timeout_seconds = int(cell["timeout_seconds"])
    if timeout_seconds <= CI_RECEIPT_RESERVE_SECONDS:
        raise RunnerError("CI cell timeout leaves no receipt publication reserve")
    deadline = started + timeout_seconds - CI_RECEIPT_RESERVE_SECONDS
    output_parts: list[str] = []
    command_receipts: list[dict[str, Any]] = []
    outcome = forced_outcome or "success"
    active: subprocess.Popen[str] | None = None
    interrupted: str | None = forced_outcome
    browser_observation: dict[str, Any] | None = None

    def interrupt(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = "cancellation" if signum == signal.SIGTERM else "interruption"
        if active is not None and active.poll() is None:
            try:
                os.killpg(active.pid, signal.SIGTERM)
            except (OSError, AttributeError):
                active.terminate()

    previous = {
        signum: signal.signal(signum, interrupt)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    cleanup_receipt: dict[str, Any]
    try:
        if cell.get("kind") == "browser" and forced_outcome is None:
            try:
                browser_observation = _browser_identity(safe_env)
            except RunnerError as exc:
                outcome = "failure"
                output_parts.append(f"browser environment failure: {exc}\n")
            else:
                observed_fingerprint = _sha256_json(browser_observation)
                if (
                    browser_observation != candidate["browser"]
                    or observed_fingerprint != candidate["browser_fingerprint"]
                ):
                    outcome = "failure"
                    output_parts.append(
                        "browser environment failure: executing runner identity "
                        "does not match the frozen candidate\n"
                    )
        logical_commands = (
            [] if outcome != "success" else _ci_cell_commands(cell, target)
        )
        for command in logical_commands:
            if interrupted:
                outcome = interrupted
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = "timeout"
                break
            command_started = time.monotonic()
            execution_argv = _materialize_ci_cell_command(command)
            active = subprocess.Popen(
                execution_argv, cwd=ROOT, env=safe_env, text=True, encoding="utf-8",
                errors="replace", stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                output, _ = active.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                outcome = "timeout"
                try:
                    os.killpg(active.pid, signal.SIGTERM)
                except (OSError, AttributeError):
                    active.terminate()
                try:
                    output, _ = active.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(active.pid, signal.SIGKILL)
                    except (OSError, AttributeError):
                        active.kill()
                    output, _ = active.communicate()
            output_parts.append(output or "")
            command_receipts.append({
                "argv": command,
                "returncode": active.returncode,
                "duration_ms": int((time.monotonic() - command_started) * 1000),
                "output_digest": _digest(output or ""),
            })
            if interrupted:
                outcome = interrupted
                break
            if outcome == "timeout" or active.returncode != 0:
                if outcome != "timeout":
                    outcome = "failure"
                break
            active = None
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if active is not None and active.poll() is None:
            try:
                os.killpg(active.pid, signal.SIGKILL)
            except (OSError, AttributeError):
                active.kill()
            active.wait()
        cleanup_receipt = _cleanup_ci_cell_root(
            target, registration, outcome=outcome,
        )

    output = "".join(output_parts)
    log_path = receipt_path.with_suffix(".log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output, encoding="utf-8", errors="replace")
    status = "green" if outcome == "success" and cleanup_receipt["leak_count"] == 0 else "red"
    observed_environment = {
        "implementation": platform.python_implementation(),
        "python": platform.python_version(),
        "os": os.name,
        "platform": platform.system(),
        "machine": platform.machine(),
    }
    classification = (
        None if outcome in {"cancellation", "interruption", "handoff"}
        else _classify_ci_failure(status, output)
    )
    payload = {
        "schema": CI_CELL_SCHEMA,
        "id": cell_id,
        "kind": cell["kind"],
        "status": status,
        "outcome": outcome,
        "classification": classification,
        "candidate_fingerprint": candidate["fingerprint"],
        "source_sha": candidate["source_sha"],
        "plan_fingerprint": runtime["plan"]["fingerprint"],
        "settings_receipt_fingerprint": runtime["settings_receipt"]["fingerprint"],
        "environment": {
            "candidate_fingerprint": candidate["fingerprints"]["environment"],
            "observed": observed_environment,
            "observed_fingerprint": _sha256_json(observed_environment),
        },
        "browser_fingerprint": (
            candidate["browser_fingerprint"] if cell["kind"] == "browser" else None
        ),
        "browser_observation": browser_observation,
        "selectors": list(cell["selectors"]),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "commands": command_receipts,
        "output_digest": _digest(output),
        "ownership": registration,
        "cleanup": cleanup_receipt,
    }
    receipt = {**payload, "receipt": _sha256_json(payload)}
    _atomic_write_json(receipt_path, receipt)
    if status == "green":
        validate_authoritative_ci_cell_receipt(receipt, runtime, cell)
    return 0 if status == "green" else 1


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_authoritative_ci_cell_receipt(
    row: Mapping[str, Any], runtime: Mapping[str, Any], cell: Mapping[str, Any],
) -> dict[str, Any]:
    """Authenticate one closed, exact-candidate cell receipt before aggregation."""
    receipt = dict(row)
    if set(receipt) != CI_CELL_FIELDS or receipt.get("schema") != CI_CELL_SCHEMA:
        raise RunnerError("CI cell receipt schema is not closed")
    material = {key: value for key, value in receipt.items() if key != "receipt"}
    if receipt.get("receipt") != _sha256_json(material):
        raise RunnerError("CI cell receipt is stale")
    candidate = runtime["candidate"]
    plan = runtime["plan"]
    if (
        receipt.get("id") != cell.get("id")
        or receipt.get("kind") != cell.get("kind")
        or receipt.get("selectors") != cell.get("selectors")
        or receipt.get("candidate_fingerprint") != candidate.get("fingerprint")
        or receipt.get("source_sha") != candidate.get("source_sha")
        or receipt.get("plan_fingerprint") != plan.get("fingerprint")
        or receipt.get("settings_receipt_fingerprint")
        != runtime["settings_receipt"].get("fingerprint")
        or not _valid_digest(receipt.get("output_digest"))
    ):
        raise RunnerError("CI cell receipt exact candidate binding failed")
    outcome = receipt.get("outcome")
    status = receipt.get("status")
    classification = receipt.get("classification")
    if outcome not in CI_TERMINAL_OUTCOMES or status not in {"green", "red"}:
        raise RunnerError("CI cell receipt terminal status is invalid")
    if status == "green":
        if outcome != "success" or classification is not None:
            raise RunnerError("green CI cell receipt has non-green terminal evidence")
    elif outcome in {"cancellation", "interruption", "handoff"}:
        if classification is not None:
            raise RunnerError("non-failure terminal outcome cannot invent a failure class")
    elif classification not in CI_FAILURE_CLASSES:
        raise RunnerError("red CI cell receipt requires one failure classification")
    duration = receipt.get("duration_ms")
    if (
        isinstance(duration, bool) or not isinstance(duration, int) or duration < 0
        or duration > (int(cell["timeout_seconds"]) + 10) * 1000
    ):
        raise RunnerError("CI cell timing receipt is invalid")
    environment = receipt.get("environment")
    if not isinstance(environment, dict) or set(environment) != {
        "candidate_fingerprint", "observed", "observed_fingerprint",
    }:
        raise RunnerError("CI cell environment receipt is incomplete")
    observed = environment.get("observed")
    if (
        environment.get("candidate_fingerprint")
        != candidate["fingerprints"]["environment"]
        or not isinstance(observed, dict)
        or set(observed) != {"implementation", "python", "os", "platform", "machine"}
        or any(not isinstance(value, str) or not value for value in observed.values())
        or environment.get("observed_fingerprint") != _sha256_json(observed)
    ):
        raise RunnerError("CI cell observed environment receipt is invalid")
    browser = receipt.get("browser_observation")
    if cell.get("kind") == "browser":
        if (
            not isinstance(browser, dict)
            or browser != candidate.get("browser")
            or _sha256_json(browser) != candidate.get("browser_fingerprint")
            or receipt.get("browser_fingerprint") != candidate.get("browser_fingerprint")
        ):
            raise RunnerError("browser cell executing-runner identity is mismatched")
    elif browser is not None or receipt.get("browser_fingerprint") is not None:
        raise RunnerError("non-browser cell carries browser authority")
    ownership = receipt.get("ownership")
    if not isinstance(ownership, dict) or set(ownership) != {
        "schema", "candidate_fingerprint", "source_sha", "cell_id",
        "containment_root", "relative_name", "registered_before_run", "fingerprint",
    }:
        raise RunnerError("CI cell ownership evidence is incomplete")
    ownership_material = {
        key: value for key, value in ownership.items() if key != "fingerprint"
    }
    if (
        ownership.get("schema") != "taskplane.ci-owned-cell/v1"
        or ownership.get("fingerprint") != _sha256_json(ownership_material)
        or ownership.get("candidate_fingerprint") != candidate.get("fingerprint")
        or ownership.get("source_sha") != candidate.get("source_sha")
        or ownership.get("cell_id") != cell.get("id")
        or ownership.get("registered_before_run") is not True
    ):
        raise RunnerError("CI cell ownership evidence is invalid")
    cleanup = receipt.get("cleanup")
    if not isinstance(cleanup, dict) or set(cleanup) != {
        "schema", "registration_fingerprint", "outcome", "resources", "status",
        "leak_count", "leaks", "fingerprint",
    }:
        raise RunnerError("CI cell cleanup receipt is incomplete")
    cleanup_material = {
        key: value for key, value in cleanup.items() if key != "fingerprint"
    }
    if (
        cleanup.get("schema") != CI_CLEANUP_SCHEMA
        or cleanup.get("fingerprint") != _sha256_json(cleanup_material)
        or cleanup.get("registration_fingerprint") != ownership.get("fingerprint")
        or cleanup.get("outcome") != outcome
        or cleanup.get("status") != "clean"
        or cleanup.get("leak_count") != 0
        or cleanup.get("leaks") != []
        or not isinstance(cleanup.get("resources"), list)
        or len(cleanup["resources"]) != 1
    ):
        raise RunnerError("CI cell cleanup evidence is invalid or leaking")
    expected_resource = str(
        Path(str(ownership["containment_root"])) / str(ownership["relative_name"])
    )
    if cleanup["resources"] != [expected_resource]:
        raise RunnerError("CI cleanup resource does not match exact ownership")
    commands = receipt.get("commands")
    if not isinstance(commands, list):
        raise RunnerError("CI cell command receipts are invalid")
    if status == "green":
        expected = _ci_cell_commands(cell, Path(cleanup["resources"][0]))
        if len(commands) != len(expected):
            raise RunnerError("green CI cell command receipt count is incomplete")
    for index, command in enumerate(commands):
        if not isinstance(command, dict) or set(command) != {
            "argv", "returncode", "duration_ms", "output_digest",
        }:
            raise RunnerError("CI cell command receipt schema is not closed")
        if (
            not isinstance(command.get("argv"), list)
            or not command["argv"]
            or isinstance(command.get("duration_ms"), bool)
            or not isinstance(command.get("duration_ms"), int)
            or command["duration_ms"] < 0
            or not _valid_digest(command.get("output_digest"))
        ):
            raise RunnerError("CI cell command receipt is malformed")
        if status == "green" and (
            command.get("returncode") != 0 or command["argv"] != expected[index]
        ):
            raise RunnerError("green CI cell command evidence is not exact")
    return receipt


def main(argv: Sequence[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    argsv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--worker", type=int)
    parser.add_argument("--shard-root", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--internal")
    parser.add_argument("--emit-ci-plan", type=Path)
    parser.add_argument("--runtime-plan", type=Path)
    parser.add_argument("--ci-cell")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--event")
    parser.add_argument("--ref")
    parser.add_argument("--run-id")
    args = parser.parse_args(argsv)
    if args.internal:
        return _internal(args.internal)
    if args.worker is not None:
        if args.shard_root is None or args.result is None or not 0 <= args.worker < len(SHARDS):
            raise RunnerError("malformed worker invocation")
        return _worker(args.worker, args.shard_root, args.result)
    if args.emit_ci_plan is not None:
        runtime = build_authoritative_ci_runtime(
            source_sha=args.source_sha or _git("rev-parse", "HEAD"),
            event=args.event or "pull_request",
            ref=args.ref or "local",
            run_id=args.run_id or "local",
        )
        _atomic_write_json(args.emit_ci_plan, runtime)
        print(canonical_json({
            "runtime_fingerprint": runtime["fingerprint"],
            "candidate_fingerprint": runtime["candidate"]["fingerprint"],
            "plan_fingerprint": runtime["plan"]["fingerprint"],
            "matrix": {"include": [
                {"id": cell["id"], "kind": cell["kind"]}
                for cell in runtime["plan"]["cells"]
            ]},
            "max_parallel": runtime["plan"]["max_parallel"],
        }))
        return 0
    if args.ci_cell is not None:
        if args.runtime_plan is None or args.receipt is None:
            raise RunnerError("CI cell requires --runtime-plan and --receipt")
        return run_authoritative_ci_cell(
            args.runtime_plan, args.ci_cell, args.receipt, environ=env,
        )
    if env.get(RECURSION_GUARD):
        raise RunnerError("recursive top-level ci_local invocation refused")
    before = _git("status", "--porcelain=v1", "--untracked-files=all")
    source_sha = _git("rev-parse", "HEAD")
    if len(source_sha) != 40 or any(char not in "0123456789abcdef" for char in source_sha):
        raise RunnerError("candidate source SHA is not exact")
    report, raw_results = _collect(source_sha, before)
    if args.json:
        print(canonical_json(report))
    else:
        print(f"local CI: {report['status']} ({len(report['receipts'])} receipts)")
        for row in raw_results:
            if row["status"] != "passed":
                print(f"{row['check_id']}: {row['status']}\n{row.get('output_tail', '')}")
    return 0 if report["status"] != "failed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, RunnerError) as exc:
        print(canonical_json({"schema": SCHEMA, "status": "failed", "error": str(exc)}))
        raise SystemExit(1)
