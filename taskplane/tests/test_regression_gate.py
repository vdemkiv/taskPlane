"""Tests for the graph-scoped regression gate (v2.3.1).

These exercise the pure decision logic and the radius/coverage selection
against a tiny synthetic package tree, plus the real taskplane tree, so the
gate is verified without spawning nested pytest runs.


Path shape: the radius, the approved test roots and the test-import
index are all '/'-shaped by contract — that is precisely what makes the
containment check ("fallback may only widen inside an approved root")
work on every host. Expectations here are therefore written with "/" and
never with os.path.join, which asserts the HOST's shape and failed on
Windows against a radius that was correct.
"""
import os
import subprocess
import sys
import textwrap

import pytest

from taskplane import regression as rg


# ----------------------------------------------------------------- classify

def test_classify_regression_is_new_failure():
    d = rg.classify(baseline_fail={"a::t1"}, current_fail={"a::t1", "b::t2"})
    assert d["regressions"] == ["b::t2"]
    assert d["pre_existing"] == ["a::t1"]
    assert d["fixed"] == []


def test_classify_preexisting_never_counts_as_regression():
    d = rg.classify(baseline_fail={"x::t"}, current_fail={"x::t"})
    assert d["regressions"] == []
    assert d["pre_existing"] == ["x::t"]


def test_classify_fixed_detected():
    d = rg.classify(baseline_fail={"x::t", "y::t"}, current_fail={"y::t"})
    assert d["fixed"] == ["x::t"]
    assert d["regressions"] == []


# ------------------------------------------------------- radius / synthetic

def _mk_pkg(tmp_path):
    ws = tmp_path
    pkg = ws / "taskplane"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "loop.py").write_text("def gate(): return 1\n")
    (pkg / "dashboard.py").write_text("def widget(): return '<div>'\n")
    (pkg / "tp.py").write_text("def main(): return 0\n")
    (pkg / "tests" / "test_loop_x.py").write_text(
        "from taskplane import loop\ndef test_g(): assert loop.gate()==1\n")
    (pkg / "tests" / "test_dash_x.py").write_text(
        "import dashboard\ndef test_w(): assert dashboard.widget()\n")
    return str(ws)


def test_radius_selects_only_tests_covering_changed_module(tmp_path):
    ws = _mk_pkg(tmp_path)
    radius, degraded = rg.radius_tests(ws, ["taskplane/loop.py"])
    assert radius == {"taskplane/tests/test_loop_x.py"}
    assert degraded is False


def test_radius_degrades_when_changed_module_has_no_test(tmp_path):
    ws = _mk_pkg(tmp_path)
    # tp.py has no importing test → degraded=True (caller runs full suite)
    radius, degraded = rg.radius_tests(ws, ["taskplane/tp.py"])
    assert degraded is True
    assert radius == {
        "taskplane/tests/test_dash_x.py",
        "taskplane/tests/test_loop_x.py",
    }


def test_radius_empty_when_no_source_module_changed(tmp_path):
    ws = _mk_pkg(tmp_path)
    radius, degraded = rg.radius_tests(ws, ["README.md"])
    assert radius == set()
    assert degraded is False


def test_graph_impacted_widens_radius(tmp_path):
    ws = _mk_pkg(tmp_path)
    # changing loop, but graph says dashboard is impacted too → both tests
    radius, _ = rg.radius_tests(ws, ["taskplane/loop.py"],
                                graph_impacted=["taskplane/dashboard.py"])
    assert "taskplane/tests/test_dash_x.py" in radius


def test_depth_keyed_graph_impact_widens_radius(tmp_path):
    ws = _mk_pkg(tmp_path)
    impact = {1: [{"module": "taskplane/dashboard.py", "via": "loop"}]}
    radius, _ = rg.radius_tests(
        ws, ["taskplane/loop.py"], graph_impacted=impact)
    assert "taskplane/tests/test_dash_x.py" in radius


def test_radius_supports_source_and_tests_outside_taskplane_layout(tmp_path):
    pkg = tmp_path / "src" / "acme"
    tests = tmp_path / "tests"
    pkg.mkdir(parents=True)
    tests.mkdir()
    (pkg / "service.py").write_text("def value(): return 1\n")
    (tests / "test_service.py").write_text(
        "from src.acme import service\n"
        "def test_value(): assert service.value() == 1\n")

    radius, degraded = rg.radius_tests(
        str(tmp_path), ["src/acme/service.py"])

    assert radius == {"tests/test_service.py"}
    assert degraded is False


def test_unmapped_generic_module_falls_back_to_every_python_test(tmp_path):
    pkg = tmp_path / "src" / "acme"
    tests = tmp_path / "tests"
    pkg.mkdir(parents=True)
    tests.mkdir()
    (pkg / "service.py").write_text("VALUE = 1\n")
    (tests / "test_one.py").write_text("def test_one(): assert True\n")
    (tests / "test_two.py").write_text("def test_two(): assert True\n")

    radius, degraded = rg.radius_tests(
        str(tmp_path), ["src/acme/service.py"])

    assert degraded is True
    assert radius == {
        "tests/test_one.py",
        "tests/test_two.py",
    }


def test_duplicate_basename_cannot_prove_a_narrow_radius(tmp_path):
    for package in ("a", "b"):
        path = tmp_path / "src" / package
        path.mkdir(parents=True)
        (path / "service.py").write_text("VALUE = 1\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_b.py").write_text("import src.b.service\n")
    (tests / "test_other.py").write_text("def test_ok(): assert True\n")

    radius, degraded = rg.radius_tests(
        str(tmp_path), ["src/a/service.py"])

    assert degraded is True
    assert radius == {"tests/test_b.py", "tests/test_other.py"}


def test_git_discovery_excludes_ignored_foreign_tests(tmp_path):
    ws = _mk_pkg(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    (tmp_path / ".gitignore").write_text("vendor/\n")
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "test_foreign.py").write_text("raise RuntimeError('foreign')\n")

    radius, degraded = rg.radius_tests(ws, ["taskplane/tp.py"])

    assert degraded is True
    assert all(not path.startswith("vendor/") for path in radius)


def test_test_fixtures_are_support_code_not_standalone_radius_tests(tmp_path):
    ws = _mk_pkg(tmp_path)
    fixtures = tmp_path / "taskplane" / "tests" / "fixtures"
    fixtures.mkdir()
    fixture = fixtures / "cli.py"
    fixture.write_text("raise RuntimeError('only valid when loaded as data')\n")
    (tmp_path / "conftest.py").write_text("ROOT_FIXTURE = True\n")

    tests = rg._python_files(ws, tests=True)
    sources = rg._python_files(ws, tests=False)

    assert "taskplane/tests/fixtures/cli.py" not in tests
    assert "taskplane/tests/fixtures/cli.py" not in sources
    assert "conftest.py" not in tests
    assert "conftest.py" not in sources
    assert "taskplane/tests/test_loop_x.py" in tests


def test_git_discovery_failure_blocks_instead_of_using_os_walk(
        monkeypatch, tmp_path):
    ws = _mk_pkg(tmp_path)
    (tmp_path / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "true\n", "")
        return subprocess.CompletedProcess(cmd, 128, "", "permission denied")

    monkeypatch.setattr(rg.subprocess, "run", fake_run)
    with pytest.raises(rg.RegressionDiscoveryError, match="permission denied"):
        rg.radius_tests(ws, ["taskplane/loop.py"])


def test_fallback_stays_within_approved_test_roots(tmp_path):
    ws = _mk_pkg(tmp_path)
    foreign = tmp_path / "foreign_tests"
    foreign.mkdir()
    (foreign / "test_foreign.py").write_text("raise RuntimeError('no')\n")
    roots = rg.approved_test_roots(
        ws, "python -m pytest taskplane/tests/test_loop_x.py")

    radius, degraded = rg.radius_tests(
        ws, ["taskplane/tp.py"], test_roots=roots)

    assert degraded is True
    assert all(path.startswith("taskplane/tests/") for path in radius)


def test_non_pytest_command_authorizes_no_python_fallback(tmp_path):
    ws = _mk_pkg(tmp_path)
    roots = rg.approved_test_roots(ws, "npm test")

    radius, degraded = rg.radius_tests(
        ws, ["taskplane/tp.py"], test_roots=roots)

    assert roots == set()
    assert radius == set()
    assert degraded is True


@pytest.mark.parametrize("command", [
    "echo pytest",
    "python checker.py pytest",
    "python checker.py -m pytest",
    "python-helper -m pytest",
    "pytest-helper --all",
])
def test_merely_mentioning_pytest_does_not_authorize_fallback(tmp_path, command):
    ws = _mk_pkg(tmp_path)
    assert rg.approved_test_roots(ws, command) == set()


def test_interpreter_token_is_never_reconsidered_as_a_test_root(tmp_path):
    ws = _mk_pkg(tmp_path)
    (tmp_path / "python").mkdir()

    roots = rg.approved_test_roots(
        ws, "python -m pytest taskplane/tests/test_loop_x.py")

    assert roots == {"taskplane/tests"}


@pytest.mark.parametrize("command", [
    "env -u CODEX_HOME -u CODEX_THREAD_ID python -m pytest -q",
    "env -uCODEX_HOME python -m pytest -q",
    "env --unset=CODEX_HOME CI=1 python -m pytest -q",
    "env -- CI=1 python -m pytest taskplane/tests/test_loop_x.py",
    "py -3 -m pytest taskplane/tests/test_loop_x.py",
    "py -3.13 -m pytest taskplane/tests/test_loop_x.py",
])
def test_supported_pytest_launchers_authorize_their_test_roots(
        tmp_path, command):
    ws = _mk_pkg(tmp_path)

    roots = rg.approved_test_roots(ws, command)

    expected = {"taskplane/tests"} if "test_loop_x.py" in command else {"."}
    assert roots == expected


@pytest.mark.parametrize("command", [
    "env -u python -m pytest",
    "env --unset= python -m pytest",
    "env FOO=1 -u BAR python -m pytest",
])
def test_malformed_env_prefix_authorizes_no_fallback(tmp_path, command):
    ws = _mk_pkg(tmp_path)
    assert rg.approved_test_roots(ws, command) == set()


@pytest.mark.parametrize("option", sorted(rg._PYTEST_VALUE_OPTIONS))
def test_pytest_option_missing_value_authorizes_no_fallback(tmp_path, option):
    ws = _mk_pkg(tmp_path)
    assert rg.approved_test_roots(ws, f"python -m pytest {option}") == set()


@pytest.mark.parametrize("option", sorted(rg._PYTEST_VALUE_OPTIONS))
def test_pytest_option_cannot_consume_following_option(tmp_path, option):
    ws = _mk_pkg(tmp_path)
    command = f"python -m pytest {option} --collect-only"
    assert rg.approved_test_roots(ws, command) == set()


@pytest.mark.parametrize("prefix", [
    "CI-FLAG=1", "--fake=x", "tools/run=x",
])
def test_invalid_assignment_prefix_authorizes_no_fallback(tmp_path, prefix):
    ws = _mk_pkg(tmp_path)
    assert rg.approved_test_roots(
        ws, f"{prefix} python -m pytest") == set()


@pytest.mark.parametrize("command", [
    "pytest taskplane/tests < other_tests/input.py",
    "pytest taskplane/tests>runner.log",
    "pytest taskplane/tests && pytest other_tests",
    "pytest taskplane/tests\npytest .",
    "pytest taskplane/tests\rpytest .",
    "pytest taskplane/tests # approved\npytest .",
])
def test_shell_control_syntax_authorizes_no_fallback(tmp_path, command):
    ws = _mk_pkg(tmp_path)
    assert rg.approved_test_roots(ws, command) == set()


def test_symlink_spelled_workspace_returns_canonical_relative_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    _mk_pkg(real)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")

    roots = rg.approved_test_roots(
        str(alias), "python -m pytest taskplane/tests")
    radius, degraded = rg.radius_tests(
        str(alias), ["taskplane/loop.py"], test_roots=roots)

    assert roots == {"taskplane/tests"}
    assert radius == {"taskplane/tests/test_loop_x.py"}
    assert degraded is False


def test_command_tokenizer_preserves_windows_path_separators():
    command = r"py -3 -m pytest tests\test_service.py"
    lexer = rg.shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    assert rg._pytest_argv(list(lexer)) == [r"tests\test_service.py"]


# ----------------------------------------------------- coverage-gap (Tier 2)

def test_config_change_with_no_test_is_a_coverage_gap(tmp_path):
    ws = _mk_pkg(tmp_path)
    index = rg.test_import_index(ws)
    # ws points at the synthetic pkg (no test references ci.yml there)
    gaps = rg.coverage_gaps(
        [".github/workflows/ci.yml"], radius=set(), ws=ws,
        import_index=index)
    assert ".github/workflows/ci.yml" in gaps


def test_config_gap_clears_when_a_test_names_the_path(tmp_path):
    ws = _mk_pkg(tmp_path)
    # a lint-test that references the config path by name COVERS it
    (tmp_path / "taskplane" / "tests" / "test_ci_lint.py").write_text(
        "def test_ci(): assert '.github/workflows/ci.yml'\n")
    index = rg.test_import_index(ws)
    gaps = rg.coverage_gaps(
        [".github/workflows/ci.yml"], radius=set(), ws=ws,
        import_index=index)
    assert gaps == []


def test_enforcement_module_covered_by_radius_is_not_a_gap(tmp_path):
    ws = _mk_pkg(tmp_path)
    # rename loop test to look like it imports taskplane_lite? simulate:
    (tmp_path / "taskplane" / "taskplane_lite.py").write_text("def screen_command(): pass\n")
    (tmp_path / "taskplane" / "tests" / "test_lite_x.py").write_text(
        "from taskplane import taskplane_lite\ndef test_s(): pass\n")
    index = rg.test_import_index(ws)
    radius = {"taskplane/tests/test_lite_x.py"}
    gaps = rg.coverage_gaps(
        ["taskplane/taskplane_lite.py"], radius=radius, ws=ws,
        import_index=index)
    assert gaps == []


def test_scratch_mirror_cannot_impersonate_real_enforcement_path(tmp_path):
    ws = _mk_pkg(tmp_path)
    changed = [
        "_incoming-2.7.0/taskplane/tp.py",
        ".fixwave/hooks/hooks.json",
        "_to_delete/.github/workflows/ci.yml",
        "taskplane/taskplane_lite.py",
    ]

    gaps = rg.coverage_gaps(
        changed, radius=set(), ws=ws, import_index={})

    assert gaps == ["taskplane/taskplane_lite.py"]
    assert rg._changed_modules(changed) == {
        "taskplane.taskplane_lite", "taskplane_lite"
    }


# ------------------------------------------------------------- scan (impure)

def test_regression_scan_flags_regression_with_injected_runners(tmp_path):
    ws = _mk_pkg(tmp_path)
    tfile = "taskplane/tests/test_loop_x.py"

    def now(_ws, files):
        return {f"{tfile}::test_g"} if files else set()

    def base(files):
        return set()  # was green at baseline

    out = rg.regression_scan(ws, "BASE", ["taskplane/loop.py"],
                             runner=now, baseline_runner=base)
    assert out["regressions"] == [f"{tfile}::test_g"]
    assert out["blocks"] is True


def test_regression_scan_preexisting_does_not_block(tmp_path):
    ws = _mk_pkg(tmp_path)
    tfile = "taskplane/tests/test_loop_x.py"

    def now(_ws, files):
        return {f"{tfile}::test_g"}

    def base(files):
        return {f"{tfile}::test_g"}  # already failing at baseline

    out = rg.regression_scan(ws, "BASE", ["taskplane/loop.py"],
                             runner=now, baseline_runner=base)
    assert out["regressions"] == []
    assert out["pre_existing"] == [f"{tfile}::test_g"]
    # no regression and no coverage gap (loop has a covering test) → no block
    assert out["blocks"] is False


def test_regression_scan_baseline_runner_error_blocks_structurally(tmp_path):
    ws = _mk_pkg(tmp_path)

    def broken(_files):
        raise rg.RegressionRunnerError("baseline collection failed")

    out = rg.regression_scan(
        ws, "BASE", ["taskplane/loop.py"], runner=lambda _ws, _files: set(),
        baseline_runner=broken)

    assert out["blocks"] is True
    assert out["runner_error"].startswith("baseline:")


# ----------------------------------------------------- real-tree smoke

def test_real_tree_index_maps_loop_tests():
    ws = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    idx = rg.test_import_index(ws)
    # at least one test file imports 'loop' and one imports 'taskplane_lite'
    imports_loop = any("loop" in mods for mods in idx.values())
    imports_lite = any("taskplane_lite" in mods for mods in idx.values())
    assert imports_loop and imports_lite


# ------------------------------------------------------- runner fail-closed

def test_run_pytest_uses_the_active_interpreter(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(rg.subprocess, "run", fake_run)
    assert rg.run_pytest(str(tmp_path), ["tests/test_x.py"]) == set()
    assert seen["cmd"][0] == sys.executable


def test_run_pytest_sanitizes_credentials_and_isolates_home(
        monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setenv("EXAMPLE_API_TOKEN", "secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("KUBECONFIG", "/tmp/kubeconfig")
    monkeypatch.setenv("DOCKER_CONFIG", "/tmp/docker")
    monkeypatch.setattr(rg.subprocess, "run", fake_run)
    rg.run_pytest(str(tmp_path), ["tests/test_x.py"])

    assert "EXAMPLE_API_TOKEN" not in seen["env"]
    assert "SSH_AUTH_SOCK" not in seen["env"]
    assert "KUBECONFIG" not in seen["env"]
    assert "DOCKER_CONFIG" not in seen["env"]
    assert seen["env"]["HOME"] != os.environ.get("HOME")


def test_current_and_baseline_runners_share_one_configured_timeout(
        monkeypatch, tmp_path):
    seen = []

    def fake_run(workspace, command, *, env, timeout):
        seen.append((workspace, timeout))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setenv("TASKPLANE_REGRESSION_TIMEOUT_SECONDS", "750")
    monkeypatch.setattr(rg.tp, "run_suite_command", fake_run)
    rg.run_pytest(str(tmp_path), ["tests/test_x.py"])
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    rg.run_pytest(str(baseline), ["tests/test_x.py"])

    assert seen == [(str(tmp_path), 750), (str(baseline), 750)]


def test_run_pytest_timeout_is_bounded_and_fails_closed(
        monkeypatch, tmp_path):
    def timeout(_workspace, command, *, env, timeout):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setenv("TASKPLANE_REGRESSION_TIMEOUT_SECONDS", "37")
    monkeypatch.setattr(rg.tp, "run_suite_command", timeout)
    with pytest.raises(rg.RegressionRunnerError,
                       match="timed out after 37 seconds"):
        rg.run_pytest(str(tmp_path), ["tests/test_x.py"])


@pytest.mark.parametrize("value", ["not-a-number", "29", "1801"])
def test_regression_timeout_configuration_cannot_remove_the_bound(
        monkeypatch, tmp_path, value):
    monkeypatch.setenv("TASKPLANE_REGRESSION_TIMEOUT_SECONDS", value)
    with pytest.raises(rg.RegressionRunnerError,
                       match="TASKPLANE_REGRESSION_TIMEOUT_SECONDS"):
        rg.run_pytest(str(tmp_path), ["tests/test_x.py"])


def test_symlinked_test_is_refused(tmp_path):
    outside = tmp_path.parent / "test_outside.py"
    outside.write_text("def test_outside(): assert True\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_link.py").symlink_to(outside)

    with pytest.raises(rg.RegressionDiscoveryError):
        rg.test_import_index(str(tmp_path))


@pytest.mark.parametrize("returncode, output", [
    (1, "python: No module named pytest"),
    (2, "ERROR collecting tests/test_x.py"),
    (3, "INTERNALERROR> plugin crashed"),
    (4, "ERROR: usage error"),
    (5, "no tests ran"),
])
def test_run_pytest_refuses_infrastructure_and_collection_errors(
        monkeypatch, tmp_path, returncode, output):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=output, stderr="")

    monkeypatch.setattr(rg.subprocess, "run", fake_run)
    with pytest.raises(rg.RegressionRunnerError):
        rg.run_pytest(str(tmp_path), ["tests/test_x.py"])


def test_dod_errors_turns_runner_failure_into_a_named_blocker(tmp_path):
    ws = _mk_pkg(tmp_path)

    def broken_runner(_ws, _files):
        raise rg.RegressionRunnerError("pytest collection failed")

    errors = rg.dod_errors(
        ws, "abcdef123456", ["taskplane/loop.py"],
        runner=broken_runner,
        baseline_failures=lambda _ws, _base, _radius: set())

    assert any(e.startswith("regression_gate: current runner failed")
               for e in errors)


def test_selector_scoped_contract_does_not_widen_tier1(tmp_path):
    ws = _mk_pkg(tmp_path)
    calls = []

    errors = rg.dod_errors(
        ws, "abcdef123456", ["taskplane/loop.py"],
        test_command=(
            "python -m pytest -q "
            "taskplane/tests/test_loop_x.py::test_g"),
        runner=lambda _ws, _files: calls.append("current") or set(),
        baseline_failures=(
            lambda _ws, _base, _files:
            calls.append("baseline") or set()))

    assert errors == []
    assert calls == []
