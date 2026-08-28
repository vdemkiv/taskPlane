"""Focused H2-A evidence for reproducible Python quality enforcement."""

from __future__ import annotations

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
POLICY = ROOT / "pyproject.toml"
LOCK = ROOT / "requirements-dev.lock"

EXPECTED_LOCK = {
    "mypy": "1.17.1",
    "mypy-extensions": "1.1.0",
    "pathspec": "1.1.1",
    "ruff": "0.12.9",
    "typing-extensions": "4.16.0",
}
STRICT_BOUNDARIES = {
    "taskplane/enforcement.py",
    "taskplane/review_convergence.py",
}


def _job(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
        source,
    )
    return match.group(1) if match else ""


def _locked_requirements(source: str) -> tuple[dict[str, str], set[str], list[str]]:
    versions: dict[str, str] = {}
    hashed: set[str] = set()
    invalid: list[str] = []
    current = ""
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        requirement = re.fullmatch(r"([A-Za-z0-9_-]+)==([A-Za-z0-9_.+-]+)\s*\\?", line)
        if requirement:
            current = requirement.group(1).lower()
            versions[current] = requirement.group(2)
            continue
        if line.startswith("--hash=sha256:") and current:
            if re.fullmatch(r"--hash=sha256:[0-9a-f]{64}\s*\\?", line):
                hashed.add(current)
            else:
                invalid.append(line)
            continue
        invalid.append(line)
    return versions, hashed, invalid


def _quality_violations(ci: str, policy: str, lock: str) -> list[str]:
    problems: list[str] = []
    job = _job(ci, "python-quality")
    required_job_fragments = (
        "name: Python quality (ruff + strict mypy)",
        "runs-on: ubuntu-latest",
        'python-version: "3.12"',
        "python -m pip install --disable-pip-version-check",
        "--require-hashes -r requirements-dev.lock",
        "python -m ruff check --output-format=github taskplane hooks scripts",
        "python -m mypy --strict --config-file pyproject.toml",
    )
    if not job:
        problems.append("missing blocking python-quality job")
    for fragment in required_job_fragments:
        if fragment not in job:
            problems.append(f"quality job misses {fragment}")
    if "continue-on-error:" in job or "strategy:" in job:
        problems.append("quality job must be one blocking non-matrix execution")

    required_policy_fragments = (
        'target-version = "py310"',
        'select = ["E9", "F63", "F7", "F82"]',
        'python_version = "3.10"',
        "strict = true",
        "warn_unused_configs = true",
        "warn_unused_ignores = true",
        "incremental = false",
    )
    for fragment in required_policy_fragments:
        if fragment not in policy:
            problems.append(f"quality policy misses {fragment}")
    if "ignore_errors = true" in policy or "disable_error_code" in policy:
        problems.append("strict typing cannot be disabled by a blanket escape")
    configured_boundaries = set(re.findall(r'"(taskplane/[^"\n]+\.py)"', policy))
    if not STRICT_BOUNDARIES <= configured_boundaries:
        problems.append("strict typed boundaries were narrowed")
    for boundary in STRICT_BOUNDARIES:
        if not (ROOT / boundary).is_file():
            problems.append(f"strict boundary does not exist: {boundary}")

    versions, hashed, invalid = _locked_requirements(lock)
    if versions != EXPECTED_LOCK:
        problems.append("quality dependency versions are not the reviewed exact pins")
    if hashed != set(EXPECTED_LOCK):
        problems.append("every quality dependency must have a SHA-256 artifact hash")
    if invalid:
        problems.append("lockfile contains an unparseable or unpinned directive")
    return problems


def _windows_sandbox_violations(ci: str) -> list[str]:
    portability = _job(ci, "tests-portability")
    match = re.search(
        r"(?ms)^\s{10}- os: windows-latest\n(.*?)(?=^\s{10}- os: macos-latest\n|\Z)",
        portability,
    )
    windows = match.group(1) if match else ""
    problems = []
    if not windows:
        problems.append("blocking Windows matrix row is missing")
    if "advisory: false" not in windows:
        problems.append("Windows portability must remain blocking")
    if "taskplane/tests/test_em_h1_sandbox.py" not in windows:
        problems.append("Windows does not execute the H1 sandbox deadline regression")
    return problems


def test_h09_ci_enforces_lint_and_strict_types() -> None:
    """H-09: CI executes pinned lint and strict type gates, fail closed."""
    assert _quality_violations(
        CI.read_text(encoding="utf-8"),
        POLICY.read_text(encoding="utf-8"),
        LOCK.read_text(encoding="utf-8"),
    ) == []


@pytest.mark.parametrize(
    ("target", "old", "new"),
    (
        ("ci", "--require-hashes", "--no-deps"),
        ("ci", "python -m ruff check", "echo ruff check"),
        ("ci", "python -m mypy --strict", "python -m mypy"),
        (
            "ci",
            "name: Python quality (ruff + strict mypy)\n    runs-on: ubuntu-latest",
            "name: Python quality (ruff + strict mypy)\n"
            "    continue-on-error: true\n    runs-on: ubuntu-latest",
        ),
        ("policy", "strict = true", "strict = false"),
        ("policy", "warn_unused_ignores = true", "warn_unused_ignores = false"),
        ("lock", "ruff==0.12.9", "ruff>=0.12.9"),
    ),
)
def test_h09_quality_contract_rejects_weakened_configuration(
    target: str, old: str, new: str
) -> None:
    sources = {
        "ci": CI.read_text(encoding="utf-8"),
        "policy": POLICY.read_text(encoding="utf-8"),
        "lock": LOCK.read_text(encoding="utf-8"),
    }
    assert old in sources[target]
    sources[target] = sources[target].replace(old, new, 1)
    assert _quality_violations(sources["ci"], sources["policy"], sources["lock"])


def test_h1e_sandbox_regression_runs_on_blocking_windows_leg() -> None:
    assert _windows_sandbox_violations(CI.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("advisory: false", "advisory: true"),
        ("taskplane/tests/test_em_h1_sandbox.py", "taskplane/tests/test_eval_recorder.py"),
    ),
)
def test_windows_sandbox_contract_rejects_advisory_or_missing_proof(
    old: str, new: str
) -> None:
    ci = CI.read_text(encoding="utf-8")
    assert old in ci
    assert _windows_sandbox_violations(ci.replace(old, new, 1))
