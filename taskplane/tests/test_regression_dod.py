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
                          text=True, check=True, encoding="utf-8")


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
