"""Focused H2-A evidence for reproducible Python quality enforcement."""

from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
POLICY = ROOT / "pyproject.toml"
LOCK = ROOT / "requirements-dev.lock"
RUNNER = ROOT / "scripts" / "ci_local.py"

EXPECTED_LOCK = {
    "mypy": "1.17.1",
    "mypy-extensions": "1.1.0",
    "pathspec": "1.1.1",
    "ruff": "0.12.9",
    "typing-extensions": "4.16.0",
}
STRICT_BOUNDARIES = {
    "taskplane.audit_projection",
    "taskplane.checkpoint_boundary",
    "taskplane.ci_failure_batching",
    "taskplane.ci_policy",
    "taskplane.dispatch_telemetry",
    "taskplane.em_outage",
    "taskplane.enforcement",
    "taskplane.expanded_route_authority_provider",
    "taskplane.host_native",
    "taskplane.lens_route_policy",
    "taskplane.owned_cleanup",
    "taskplane.review_convergence",
    "taskplane.settings",
    "taskplane.settings_legacy",
    "taskplane.test_strategy",
    "taskplane.wave_metrics",
}
DYNAMIC_RUFF_NAMES = {
    "Ctx",
    "DEEP_CAP",
    "DEEP_TARGET",
    "_graph_payload",
    "apply_budget",
    "evidence",
    "load_catalog",
    "verdicts",
}


def _workflow_job(source: str, name: str) -> dict:
    workflow = yaml.safe_load(source)
    jobs = workflow.get("jobs", {}) if isinstance(workflow, dict) else {}
    job = jobs.get(name, {}) if isinstance(jobs, dict) else {}
    return job if isinstance(job, dict) else {}


def _step_runs(job: dict) -> str:
    steps = job.get("steps", [])
    if not isinstance(steps, list):
        return ""
    return "\n".join(
        str(step.get("run", ""))
        for step in steps if isinstance(step, dict)
    )


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


def _array_values(source: str, key: str) -> list[str]:
    match = re.search(
        rf'(?ms)^\s*{re.escape(key)}\s*=\s*\[(.*?)^\s*\]', source
    )
    return re.findall(r'"([^"\n]+)"', match.group(1)) if match else []


def _production_modules() -> set[str]:
    return {
        f"taskplane.{path.stem}"
        for path in (ROOT / "taskplane").glob("*.py")
    }


def _strict_policy_violations(policy: str) -> list[str]:
    problems: list[str] = []
    production = _production_modules()
    debt = set(_array_values(policy, "module"))
    admitted = production - debt
    global_policy = policy.split("[[tool.mypy.overrides]]", 1)[0]

    if 'files = ["taskplane/*.py"]' not in global_policy:
        problems.append("mypy must directly target every top-level production module")
    if 'follow_imports = "skip"' in policy:
        problems.append("mypy cannot skip imported types")
    if "ignore_errors = true" in global_policy or "disable_error_code" in policy:
        problems.append("strict typing cannot be disabled by a blanket escape")
    if "[[tool.mypy.overrides]]" not in policy or "ignore_errors = true" not in policy:
        problems.append("the staged debt baseline is missing")
    if not debt or any("*" in module for module in debt):
        problems.append("legacy typing debt must use exact module names")
    if debt - production:
        problems.append("legacy typing debt names a non-production module")
    if not STRICT_BOUNDARIES <= admitted:
        problems.append("the measured strict-module ratchet regressed")
    if len(admitted) <= 2:
        problems.append("strict production coverage was narrowed")

    builtins = set(_array_values(policy, "builtins"))
    if builtins != DYNAMIC_RUFF_NAMES:
        problems.append("dynamic Ruff names differ from the reviewed exact set")
    if "per-file-ignores" in policy or "extend-per-file-ignores" in policy:
        problems.append("undefined-name lint cannot be suppressed for a whole file")
    return problems


def _quality_violations(
    ci: str, policy: str, lock: str, runner: str,
) -> list[str]:
    problems: list[str] = []
    job = _workflow_job(ci, "quality-package")
    if not job:
        problems.append("missing direct quality-package job")
    if job.get("runs-on") != "ubuntu-latest":
        problems.append("quality job must execute directly on Linux")
    if "continue-on-error" in job or "needs" in job or "strategy" in job:
        problems.append("quality job must be a blocking direct non-matrix execution")
    steps = job.get("steps", []) if isinstance(job, dict) else []
    setup = next((step for step in steps if isinstance(step, dict) and
                  str(step.get("uses", "")).startswith("actions/setup-python@")), {})
    if setup.get("with", {}).get("python-version") != "3.12":
        problems.append("quality job must use the authoritative Python 3.12 runtime")
    commands = _step_runs(job)
    for fragment in (
        "python -m pip install --disable-pip-version-check",
        "--require-hashes --no-deps -r requirements-dev.lock",
        "--ci-cell quality-package",
    ):
        if fragment not in commands:
            problems.append(f"quality job misses {fragment}")
    for fragment in (
        '[PYTHON, "-m", "ruff", "check", "--output-format=github",',
        '[PYTHON, "-m", "mypy", "--strict", "--config-file", "pyproject.toml"],',
    ):
        if fragment not in runner:
            problems.append(f"quality runner misses {fragment}")

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
    problems.extend(_strict_policy_violations(policy))

    versions, hashed, invalid = _locked_requirements(lock)
    if versions != EXPECTED_LOCK:
        problems.append("quality dependency versions are not the reviewed exact pins")
    if hashed != set(EXPECTED_LOCK):
        problems.append("every quality dependency must have a SHA-256 artifact hash")
    if invalid:
        problems.append("lockfile contains an unparseable or unpinned directive")
    return problems


def _windows_sandbox_violations(ci: str, runner: str) -> list[str]:
    windows = _workflow_job(ci, "native-portability")
    problems = []
    if not windows:
        problems.append("direct native Windows job is missing")
    if windows.get("runs-on") != "windows-latest":
        problems.append("native portability must run on Windows")
    if "continue-on-error" in windows or "needs" in windows:
        problems.append("native portability must remain a direct blocking check")
    if "--ci-cell os-portability-windows" not in _step_runs(windows):
        problems.append("Windows job does not execute its canonical CI cell")
    selector = (
        '"taskplane/tests/test_em_h1_sandbox.py::"\n'
        '    "test_h34_windows_timeout_kills_child_and_grandchild",'
    )
    if selector not in runner:
        problems.append("Windows cell omits the H1 sandbox deadline regression")
    return problems


def test_h09_ci_enforces_lint_and_strict_types() -> None:
    """H-09: CI executes pinned lint and strict type gates, fail closed."""
    assert _quality_violations(
        CI.read_text(encoding="utf-8"),
        POLICY.read_text(encoding="utf-8"),
        LOCK.read_text(encoding="utf-8"),
        RUNNER.read_text(encoding="utf-8"),
    ) == []


@pytest.mark.parametrize(
    ("target", "old", "new"),
    (
        (
            "ci",
            "Install sealed quality environment\n"
            "        shell: bash\n"
            "        run: |\n"
            "          python -m pip install --disable-pip-version-check --require-hashes --no-deps -r requirements-dev.lock",
            "Install sealed quality environment\n"
            "        shell: bash\n"
            "        run: |\n"
            "          python -m pip install --disable-pip-version-check --require-hashes -r requirements-dev.lock",
        ),
        ("runner", '[PYTHON, "-m", "ruff", "check", "--output-format=github",',
         '[PYTHON, "-m", "ruff", "check",'),
        ("runner", '[PYTHON, "-m", "mypy", "--strict",',
         '[PYTHON, "-m", "mypy",'),
        (
            "ci",
            "quality-package:\n"
            "    name: quality + package + release provenance\n"
            "    runs-on: ubuntu-latest",
            "quality-package:\n"
            "    name: quality + package + release provenance\n"
            "    continue-on-error: true\n"
            "    runs-on: ubuntu-latest",
        ),
        ("policy", "strict = true", "strict = false"),
        ("policy", "warn_unused_ignores = true", "warn_unused_ignores = false"),
        (
            "policy",
            'files = ["taskplane/*.py"]',
            'files = ["taskplane/enforcement.py"]',
        ),
        (
            "policy",
            "incremental = false",
            'incremental = false\nfollow_imports = "skip"',
        ),
        (
            "policy",
            '  "taskplane.audit",',
            '  "taskplane.*",',
        ),
        (
            "policy",
            'module = [',
            'module = [\n  "taskplane.enforcement",',
        ),
        (
            "policy",
            '  "verdicts",',
            '  "verdicts",\n  "future_typo",',
        ),
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
        "runner": RUNNER.read_text(encoding="utf-8"),
    }
    assert old in sources[target]
    sources[target] = sources[target].replace(old, new, 1)
    assert _quality_violations(
        sources["ci"], sources["policy"], sources["lock"], sources["runner"],
    )


def test_h1e_sandbox_regression_runs_on_blocking_windows_leg() -> None:
    assert _windows_sandbox_violations(
        CI.read_text(encoding="utf-8"), RUNNER.read_text(encoding="utf-8"),
    ) == []


@pytest.mark.parametrize(
    ("target", "old", "new"),
    (
        ("ci", "runs-on: windows-latest", "runs-on: ubuntu-latest"),
        (
            "runner",
            '"taskplane/tests/test_em_h1_sandbox.py::"\n'
            '    "test_h34_windows_timeout_kills_child_and_grandchild",',
            '"taskplane/tests/test_eval_recorder.py::"\n'
            '    "test_eval_failure_is_recorded",',
        ),
    ),
)
def test_windows_sandbox_contract_rejects_non_native_or_missing_proof(
    target: str, old: str, new: str
) -> None:
    ci = CI.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    sources = {"ci": ci, "runner": runner}
    assert old in sources[target]
    sources[target] = sources[target].replace(old, new, 1)
    assert _windows_sandbox_violations(sources["ci"], sources["runner"])
