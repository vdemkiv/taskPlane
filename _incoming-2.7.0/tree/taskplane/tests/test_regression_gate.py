"""Tests for the graph-scoped regression gate (v2.3.1).

These exercise the pure decision logic and the radius/coverage selection
against a tiny synthetic package tree, plus the real taskplane tree, so the
gate is verified without spawning nested pytest runs.
"""
import os
import textwrap

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
    assert radius == {os.path.join("taskplane", "tests", "test_loop_x.py")}
    assert degraded is False


def test_radius_degrades_when_changed_module_has_no_test(tmp_path):
    ws = _mk_pkg(tmp_path)
    # tp.py has no importing test → degraded=True (caller runs full suite)
    radius, degraded = rg.radius_tests(ws, ["taskplane/tp.py"])
    assert degraded is True


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
    assert os.path.join("taskplane", "tests", "test_dash_x.py") in radius


# ----------------------------------------------------- coverage-gap (Tier 2)

def test_config_change_with_no_test_is_a_coverage_gap(tmp_path):
    ws = _mk_pkg(tmp_path)
    rg.test_import_index_cache["idx"] = rg.test_import_index(ws)
    # ws points at the synthetic pkg (no test references ci.yml there)
    gaps = rg.coverage_gaps([".github/workflows/ci.yml"], radius=set(), ws=ws)
    assert ".github/workflows/ci.yml" in gaps


def test_config_gap_clears_when_a_test_names_the_path(tmp_path):
    ws = _mk_pkg(tmp_path)
    # a lint-test that references the config path by name COVERS it
    (tmp_path / "taskplane" / "tests" / "test_ci_lint.py").write_text(
        "def test_ci(): assert '.github/workflows/ci.yml'\n")
    rg.test_import_index_cache["idx"] = rg.test_import_index(ws)
    gaps = rg.coverage_gaps([".github/workflows/ci.yml"], radius=set(), ws=ws)
    assert gaps == []


def test_enforcement_module_covered_by_radius_is_not_a_gap(tmp_path):
    ws = _mk_pkg(tmp_path)
    # rename loop test to look like it imports taskplane_lite? simulate:
    (tmp_path / "taskplane" / "taskplane_lite.py").write_text("def screen_command(): pass\n")
    (tmp_path / "taskplane" / "tests" / "test_lite_x.py").write_text(
        "from taskplane import taskplane_lite\ndef test_s(): pass\n")
    rg.test_import_index_cache["idx"] = rg.test_import_index(ws)
    radius = {os.path.join("taskplane", "tests", "test_lite_x.py")}
    gaps = rg.coverage_gaps(["taskplane/taskplane_lite.py"], radius=radius)
    assert gaps == []


# ------------------------------------------------------------- scan (impure)

def test_regression_scan_flags_regression_with_injected_runners(tmp_path):
    ws = _mk_pkg(tmp_path)
    tfile = os.path.join("taskplane", "tests", "test_loop_x.py")

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
    tfile = os.path.join("taskplane", "tests", "test_loop_x.py")

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


# ----------------------------------------------------- real-tree smoke

def test_real_tree_index_maps_loop_tests():
    ws = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    idx = rg.test_import_index(ws)
    # at least one test file imports 'loop' and one imports 'taskplane_lite'
    imports_loop = any("loop" in mods for mods in idx.values())
    imports_lite = any("taskplane_lite" in mods for mods in idx.values())
    assert imports_loop and imports_lite
