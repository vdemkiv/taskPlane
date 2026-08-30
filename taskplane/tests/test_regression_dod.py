"""End-to-end proof of the regression gate through a real git repo:
baseline-green -> break source -> Tier-1 flags a regression;
change a config with no test -> Tier-2 flags a coverage gap.
"""
import os
import subprocess
import textwrap

import pytest

from taskplane import regression as rg


def _git(ws, *args):
    return subprocess.run(["git", *args], cwd=ws, capture_output=True,
                          text=True, check=True, encoding="utf-8", errors="replace")


def _mk_repo(tmp_path):
    ws = str(tmp_path)
    pkg = tmp_path / "taskplane"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "loop.py").write_text("def gate():\n    return 1\n")
    # a covering test that imports the module via its own directory
    (pkg / "tests" / "test_loop.py").write_text(textwrap.dedent("""
        import os, sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import loop
        def test_gate():
            assert loop.gate() == 1
    """))
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "baseline green")
    base = _git(ws, "rev-parse", "HEAD").stdout.strip()
    return ws, base


def _mk_generic_repo(tmp_path):
    ws = str(tmp_path)
    pkg = tmp_path / "src" / "acme"
    tests = tmp_path / "tests"
    pkg.mkdir(parents=True)
    tests.mkdir()
    (pkg / "service.py").write_text("def value():\n    return 1\n")
    (tests / "test_service.py").write_text(textwrap.dedent("""
        from src.acme import service
        def test_value():
            assert service.value() == 1
    """))
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "baseline green")
    return ws, _git(ws, "rev-parse", "HEAD").stdout.strip()


def test_tier1_flags_a_real_regression(tmp_path):
    ws, base = _mk_repo(tmp_path)
    # break the source so the previously-green test now fails
    (tmp_path / "taskplane" / "loop.py").write_text("def gate():\n    return 2\n")
    errs = rg.dod_errors(ws, base, ["taskplane/loop.py"])
    assert any(e.startswith("regression:") for e in errs), errs


def test_no_regression_when_behavior_preserved(tmp_path):
    ws, base = _mk_repo(tmp_path)
    # a comment-only change: test still passes → no regression
    (tmp_path / "taskplane" / "loop.py").write_text(
        "def gate():\n    return 1  # unchanged behavior\n")
    errs = rg.dod_errors(ws, base, ["taskplane/loop.py"])
    assert not any(e.startswith("regression:") for e in errs), errs


def test_tier2_flags_config_change_with_no_test(tmp_path):
    ws, base = _mk_repo(tmp_path)
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text("{}\n")
    errs = rg.dod_errors(ws, base, ["hooks/hooks.json"])
    assert any(e.startswith("regression_coverage_gap:") for e in errs), errs


def test_tier1_generic_layout_flags_a_real_regression(tmp_path):
    ws, base = _mk_generic_repo(tmp_path)
    (tmp_path / "src" / "acme" / "service.py").write_text(
        "def value():\n    return 2\n")

    errs = rg.dod_errors(ws, base, ["src/acme/service.py"])

    assert any(e.startswith("regression:") for e in errs), errs


def test_hosted_pr_checks_authorizes_repository_static_test_roots(tmp_path):
    ws, _base = _mk_repo(tmp_path)

    assert rg.approved_test_roots(
        ws, "gh pr checks 17 --watch --fail-fast") == {"."}
    assert rg.approved_test_roots(
        ws, "gh pr checks 17 --fail-fast --watch") == {"."}


def test_hosted_pr_checks_skips_tier1_but_keeps_static_coverage(tmp_path):
    ws, base = _mk_repo(tmp_path)
    calls = []

    errors = rg.dod_errors(
        ws, base, ["taskplane/loop.py"],
        test_command="gh pr checks 17 --watch --fail-fast",
        runner=lambda _ws, _files: calls.append("current") or set(),
        baseline_failures=(
            lambda _ws, _base, _files:
            calls.append("baseline") or set()))

    assert errors == []
    assert calls == []
    roots = rg.approved_test_roots(
        ws, "gh pr checks 17 --watch --fail-fast")
    index = rg.test_import_index(ws, roots)
    radius, _degraded = rg.radius_tests(
        ws, ["taskplane/loop.py"], test_roots=roots, import_index=index)
    assert "taskplane/tests/test_loop.py" in radius
    assert rg.coverage_gaps(
        ["taskplane/loop.py"], radius, ws,
        import_index=index, test_roots=roots) == []


def test_hosted_pr_checks_still_blocks_a_real_static_coverage_gap(tmp_path):
    ws, base = _mk_repo(tmp_path)
    (tmp_path / "taskplane" / "tests" / "test_loop.py").write_text(
        "def test_unrelated():\n    assert True\n")
    calls = []

    errors = rg.dod_errors(
        ws, base, ["taskplane/loop.py"],
        test_command="gh pr checks 17 --watch --fail-fast",
        runner=lambda _ws, _files: calls.append("current") or set(),
        baseline_failures=(
            lambda _ws, _base, _files:
            calls.append("baseline") or set()))

    assert any(error.startswith("regression_coverage_gap:")
               for error in errors)
    assert calls == []


@pytest.mark.parametrize("command", [
    "gh pr checks 0 --watch --fail-fast",
    "gh pr checks -1 --watch --fail-fast",
    "gh pr checks one --watch --fail-fast",
    "gh pr checks 1 --watch",
    "gh pr checks 1 --watch --fail-fast --repo owner/repo",
    "gh pr view 1 --watch --fail-fast",
    "gh repo delete owner/repo --yes",
    "gh pr checks 1 --watch --fail-fast && pytest",
    "gh pr checks 1 --watch --fail-fast; gh pr merge 1",
])
def test_malformed_or_unrelated_gh_commands_do_not_widen_roots(
        tmp_path, command):
    ws, _base = _mk_repo(tmp_path)

    assert rg.approved_test_roots(ws, command) == set()
