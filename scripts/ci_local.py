#!/usr/bin/env python3
"""Closed, isolated, parallel local equivalent of Taskplane's blocking CI.

This runner intentionally has no discovery mechanism: INVENTORY is the whole
contract.  Each check is assigned once to one of four subprocess shards and
every shard is collected even after another fails.
"""
from __future__ import annotations

import argparse
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
from typing import NamedTuple, Sequence


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
SCHEMA = "taskplane.local-ci-equivalent/v1"
INVENTORY_VERSION = "REL-2181/2"
RECURSION_GUARD = "TASKPLANE_LOCAL_CI_ACTIVE"
PACKAGE_TEMP_ROOT = "TASKPLANE_PACKAGE_TEMP_ROOT"
TEMP_PREFIX = "taskplane-local-ci-"
DEADLINE_SECONDS = 13_800
CLEANUP_RESERVE_SECONDS = 600
AGGREGATE_TIMEOUT_SECONDS = 14_400


class RunnerError(RuntimeError):
    pass


class Check(NamedTuple):
    id: str
    argv: tuple[str, ...]
    missing_tool: str | None = None


PYTEST_SHARD_COUNT = 4
PYTEST_CHECK_IDS = tuple(
    f"pytest-shard-{index + 1}" for index in range(PYTEST_SHARD_COUNT)
)
# Content address of every repository-relative `path:estimated-byte-weight` row.
# A file added, removed, renamed, or reweighted must deliberately refresh this
# pin, so the complete suite cannot silently shrink or use stale balancing data.
PYTEST_WEIGHT_SHA256 = "f970086db6fc0352ee478af05ebf8a03ac0c9a61945b2366683456bebdbb9564"


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
    weights = {
        path: max(1, (ROOT / path).stat().st_size)
        for path in files
    }
    rows = "\n".join(f"{path}:{weights[path]}" for path in sorted(weights))
    observed = hashlib.sha256(rows.encode()).hexdigest()
    if observed != PYTEST_WEIGHT_SHA256:
        raise RunnerError(
            "pytest weight inventory is absent or stale: "
            f"expected {PYTEST_WEIGHT_SHA256}, observed {observed}"
        )
    return weights


def partition_pytest_files(
    files: Sequence[str], weights: dict[str, int], shard_count: int,
) -> tuple[tuple[str, ...], ...]:
    if len(files) != len(set(files)):
        raise RunnerError("duplicate pytest file inventory row")
    if set(files) != set(weights) or any(
        not isinstance(weight, int) or weight <= 0 for weight in weights.values()
    ):
        raise RunnerError("pytest weight inventory is absent or stale")
    if shard_count < 2 or len(files) < shard_count:
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
    Check("import-cycle-history", (PYTHON, "taskplane/import_cycles.py", "--root", ".", "--policy", "taskplane/tests/fixtures/import-cycles.json", "--check", "--verify-history")),
    Check("generated-lens-drift", (PYTHON, __file__, "--internal", "generated-lens-drift")),
    Check("generated-cli-drift", (PYTHON, __file__, "--internal", "generated-cli-drift")),
    Check("package-openai", (PYTHON, __file__, "--internal", "package-openai")),
    Check("package-claude", (PYTHON, __file__, "--internal", "package-claude")),
    Check("ruff", (PYTHON, "-m", "ruff", "check", "taskplane", "hooks", "scripts"), "ruff"),
    Check("mypy", (PYTHON, "-m", "mypy", "--strict", "--config-file", "pyproject.toml"), "mypy"),
    Check("host-platform", (PYTHON, __file__, "--internal", "host-platform")),
)

SHARDS = (
    (PYTEST_CHECK_IDS[0], "compile-import", "generated-lens-drift", "ruff"),
    (PYTEST_CHECK_IDS[1], "version-verify", "release-surface", "generated-cli-drift", "mypy"),
    (PYTEST_CHECK_IDS[2], "zero-token-corpus", "release-history", "package-openai", "host-platform"),
    (PYTEST_CHECK_IDS[3], "unittest-canary", "loop-cost", "import-cycle-history", "package-claude"),
)
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


def main(argv: Sequence[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    argsv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--worker", type=int)
    parser.add_argument("--shard-root", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--internal")
    args = parser.parse_args(argsv)
    if args.internal:
        return _internal(args.internal)
    if args.worker is not None:
        if args.shard_root is None or args.result is None or not 0 <= args.worker < len(SHARDS):
            raise RunnerError("malformed worker invocation")
        return _worker(args.worker, args.shard_root, args.result)
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
