"""Lint-test the CI workflow (v2.3.1) — the v2.3.0 CI break shipped because a
workflow file has no unit test. This closes that coverage gap and pins the
invariants: run from the repo root, exercise BOTH runners, and gate versioning.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CI = os.path.join(ROOT, ".github", "workflows", "ci.yml")


def _ci():
    with open(CI, encoding="utf-8") as f:
        return f.read()


def test_pytest_runs_from_repo_root_not_inside_taskplane():
    src = _ci()
    # the broken form was `working-directory: taskplane` + `pytest tests/`
    assert "python -m pytest taskplane/tests" in src
    # no test step should cd into taskplane and run bare `pytest tests/`.
    # Line-anchored so an explanatory comment mentioning the old form doesn't
    # trip it — only a real YAML `working-directory: taskplane` key counts.
    assert not re.search(r"(?m)^\s*working-directory:\s*taskplane\b", src), \
        "CI must run from the repo root so conftest can import the package"


def test_ci_runs_the_unittest_discover_runner():
    assert "python -m unittest discover -s taskplane/tests" in _ci()


def test_ci_gates_single_source_versioning():
    # makes the CHANGELOG's 'CI-gated' version claim literally true
    assert "tp.py version --verify" in _ci()
