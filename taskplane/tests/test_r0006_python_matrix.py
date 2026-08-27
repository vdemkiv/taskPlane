"""R-0006 supported-Python matrix and pre-test P2 gate contract."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CONFIGURATION = ROOT / "docs" / "configuration.md"
SUPPORTED_PYTHONS = ("3.10", "3.11", "3.12", "3.13")
GATE_NAME = "Compile and import every shipped Python entry point"
TEST_STEP_NAME = "Run the authoritative suite or compatibility smoke set"


def _primary_test_job(workflow: str) -> str:
    start = workflow.index("  tests:\n")
    end = workflow.index("\n  tests-portability:", start)
    return workflow[start:end]


def _gate_payload(workflow: str) -> str:
    start_marker = "# R-0006-COMPILE-IMPORT-BEGIN"
    end_marker = "# R-0006-COMPILE-IMPORT-END"
    start = workflow.index(start_marker) + len(start_marker)
    end = workflow.index(end_marker, start)
    lines = workflow[start:end].splitlines()
    return textwrap.dedent("\n".join(lines)).strip()


def _copy_shipped_python_surface(destination: Path) -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--", "taskplane/*.py", "hooks/*.py"],
        cwd=ROOT, text=True, encoding="utf-8", capture_output=True,
        check=True).stdout.splitlines()
    assert tracked
    for relative in tracked:
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True)
    subprocess.run(["git", "add", "taskplane", "hooks"], cwd=destination,
                   check=True)


def test_primary_ci_matrix_is_exactly_the_supported_cpython_range() -> None:
    job = _primary_test_job(WORKFLOW.read_text(encoding="utf-8"))
    versions = tuple(re.findall(
        r'^          - python: "(3\.\d+)"$', job, flags=re.MULTILINE))

    assert versions == SUPPORTED_PYTHONS
    assert "python-version: ${{ matrix.python }}" in job
    starts = [job.index(f'          - python: "{version}"')
              for version in SUPPORTED_PYTHONS]
    ends = starts[1:] + [job.index("    steps:", starts[-1])]
    entries = dict(zip(SUPPORTED_PYTHONS,
                       (job[start:end] for start, end in zip(starts, ends))))
    for version in ("3.10", "3.11", "3.13"):
        assert entries[version].count(
            "              taskplane/tests/test_r0006_python_matrix.py") == 1


def test_primary_ci_full_suite_has_complete_release_and_graph_history() -> None:
    job = _primary_test_job(WORKFLOW.read_text(encoding="utf-8"))
    checkout = job.index("      - uses: actions/checkout@")
    setup = job.index("      - uses: actions/setup-python@", checkout)
    checkout_step = job[checkout:setup]

    assert "          fetch-depth: 0" in checkout_step
    assert "          persist-credentials: false" in checkout_step


def test_every_matrix_leg_runs_compatibility_flows_before_tests() -> None:
    job = _primary_test_job(WORKFLOW.read_text(encoding="utf-8"))

    gate = job.index(f"      - name: {GATE_NAME}")
    tests = job.index(f"      - name: {TEST_STEP_NAME}")
    assert gate < tests
    assert "if: matrix.python" not in job[gate:tests]
    for command in (
        "python taskplane/tp.py version --verify",
        "python taskplane/tp.py --help",
        "python taskplane/tp.py graph --workspace",
        "python taskplane/tp.py status",
        "python scripts/ci_evals.py --corpus",
    ):
        assert command in job[gate:tests]


def test_compile_import_gate_uses_the_tracked_shipped_surface() -> None:
    payload = _gate_payload(WORKFLOW.read_text(encoding="utf-8"))

    assert '"git", "ls-files"' in payload
    assert '"taskplane/*.py", "hooks/*.py"' in payload
    assert "compile(source" in payload
    assert "importlib.import_module" in payload
    assert payload.index("compile(source") < payload.index(
        "importlib.import_module")


def test_seeded_syntax_error_stops_before_the_test_step(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _copy_shipped_python_surface(checkout)
    (checkout / "taskplane" / "stage_entities.py").write_text(
        "def seeded_syntax_error(:\n", encoding="utf-8")
    sentinel = checkout / "tests-ran"

    payload = _gate_payload(WORKFLOW.read_text(encoding="utf-8"))
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    gate = subprocess.run(
        [sys.executable, "-c", payload], cwd=checkout, env=env,
        text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False)
    if gate.returncode == 0:
        sentinel.write_text("tests ran\n", encoding="utf-8")

    assert gate.returncode != 0
    assert "stage_entities.py" in gate.stderr
    assert not sentinel.exists(), "the test step ran after a failed P2 gate"


def test_configuration_names_support_and_early_refusal_semantics() -> None:
    documentation = " ".join(
        CONFIGURATION.read_text(encoding="utf-8").split())

    assert "CPython 3.10 through 3.13 inclusive" in documentation
    assert "outside the validated support range" in documentation
    assert "newer than 3.13 may start, but remains unvalidated" in documentation
    assert "before importing shipped modules" in documentation
    assert "before creating or changing Taskplane state" in documentation
