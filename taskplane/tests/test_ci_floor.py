"""t9 (R-0011 / E1 + E2) — CI test-hygiene guards, self-tested.

Two guards live here because both are about the SUITE itself rather than
about any engine behavior:

  E1  scripts/ci_unittest_floor.py — the unittest-discover CI leg only proves
      "the second runner still works" for the tests that runner can actually
      COLLECT. Two ways that proof silently erodes: tests disappear from
      discovery (a converted/deleted/renamed module), or new pytest-only
      files (module-level `def test_*` with no unittest.TestCase) accumulate
      so the discover leg covers an ever-smaller slice. The script closes
      both directions — a pinned FLOOR on the collected count, and a pinned
      MANIFEST of the files discovery legitimately cannot collect. Recorded
      design decision (R-0011 row 1): floor + manifest, convert NOTHING.

  E2  the conftest env-mutation guard — taskplane/tests/conftest.py snapshots
      os.environ around every test MODULE and fails the module if it is not
      byte-identical afterwards, naming the module and the offending keys.
      The self-test below runs pytest on a scratch tree whose test module
      deliberately leaks an env var and asserts the guard names both.

Both self-tests drive the real artifacts as subprocesses against synthetic
trees, so they cannot pass by re-implementing the thing under test.
"""
import json
import os
import subprocess
import sys
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "scripts", "ci_unittest_floor.py")

# The ratchet pins (t9 landing values). These literals may only move in the
# tightening direction: FLOOR up, MANIFEST down. A future task that raises
# the floor updates BOTH the script and the literal below; a task that
# converts a pytest-only file to unittest style shrinks both manifests.
_RATCHET_FLOOR = 1149
_RATCHET_MANIFEST = frozenset({
    "test_dispatch_parity.py",
    "test_regression_dod.py",
    "test_regression_gate.py",
    "test_review_discipline.py",
    "test_review_wave.py",
    "test_stage_waves.py",
    "test_v231_ci.py",
    "test_v231_cli.py",
    "test_v231_dispatch.py",
    "test_v231_guardrails.py",
})


def _run_script(*args, cwd=None):
    return subprocess.run([sys.executable, SCRIPT, *args],
                          capture_output=True, text=True,
                          cwd=cwd or ROOT, encoding="utf-8")


def _mk_tree(base, files):
    """Write a synthetic repo root: <base>/taskplane/tests/{__init__,*}.py."""
    tdir = os.path.join(base, "taskplane", "tests")
    os.makedirs(tdir, exist_ok=True)
    open(os.path.join(base, "taskplane", "__init__.py"), "w", encoding="utf-8").close()
    open(os.path.join(tdir, "__init__.py"), "w", encoding="utf-8").close()
    for name, body in files.items():
        with open(os.path.join(tdir, name), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(body))
    return tdir


_UNITTEST_MODULE = """\
    import unittest


    class T(unittest.TestCase):
        def test_a(self):
            self.assertTrue(True)

        def test_b(self):
            self.assertTrue(True)
"""

_PYTEST_ONLY_MODULE = """\
    def test_plain_function():
        assert True
"""


class TestFloorScriptCLI(unittest.TestCase):
    """The script is a CI leg: it must be drivable, self-describing, and
    fail NONZERO on every erosion direction."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="tp-floor-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp,
                        ignore_errors=True)

    def test_script_exists_and_is_executable_source(self):
        self.assertTrue(os.path.exists(SCRIPT), f"{SCRIPT} missing")
        src = open(SCRIPT, encoding="utf-8").read()
        self.assertIn("FLOOR", src)
        self.assertIn("PYTEST_ONLY_MANIFEST", src)

    def test_below_floor_tree_exits_nonzero_and_names_both_numbers(self):
        """A tree with 2 collected tests against a floor of 50 fails, and the
        message prints CURRENT and FLOOR so the drop is legible."""
        _mk_tree(self.tmp, {"test_alpha.py": _UNITTEST_MODULE})
        r = _run_script("--root", self.tmp, "--floor", "50",
                        "--manifest", "")
        self.assertNotEqual(r.returncode, 0,
                            f"expected failure; stdout={r.stdout}")
        blob = r.stdout + r.stderr
        self.assertIn("2", blob)
        self.assertIn("50", blob)
        self.assertIn("floor", blob.lower())

    def test_at_or_above_floor_tree_passes(self):
        _mk_tree(self.tmp, {"test_alpha.py": _UNITTEST_MODULE})
        r = _run_script("--root", self.tmp, "--floor", "2", "--manifest", "")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2", r.stdout)

    def test_new_pytest_only_file_absent_from_manifest_fails(self):
        """The widening direction: a NEW module-level-def test file that
        unittest discovery cannot collect must fail the leg until it is
        added to the manifest deliberately."""
        _mk_tree(self.tmp, {"test_alpha.py": _UNITTEST_MODULE,
                            "test_sneaky.py": _PYTEST_ONLY_MODULE})
        r = _run_script("--root", self.tmp, "--floor", "2", "--manifest", "")
        self.assertNotEqual(r.returncode, 0,
                            f"expected manifest failure; stdout={r.stdout}")
        blob = r.stdout + r.stderr
        self.assertIn("test_sneaky.py", blob)
        self.assertIn("manifest", blob.lower())

    def test_pytest_only_file_named_in_manifest_passes(self):
        _mk_tree(self.tmp, {"test_alpha.py": _UNITTEST_MODULE,
                            "test_sneaky.py": _PYTEST_ONLY_MODULE})
        r = _run_script("--root", self.tmp, "--floor", "2",
                        "--manifest", "test_sneaky.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_manifest_entry_that_stopped_being_pytest_only_fails(self):
        """The manifest is an EQUALITY, not a floor: a file that was
        converted to unittest style must be REMOVED from the manifest, so
        the manifest can only shrink."""
        _mk_tree(self.tmp, {"test_alpha.py": _UNITTEST_MODULE})
        r = _run_script("--root", self.tmp, "--floor", "2",
                        "--manifest", "test_alpha.py")
        self.assertNotEqual(r.returncode, 0,
                            f"expected stale-manifest failure; {r.stdout}")
        self.assertIn("test_alpha.py", r.stdout + r.stderr)

    def test_broken_module_in_discovery_fails_the_leg(self):
        """Discovery turns an unimportable module into a _FailedTest
        placeholder that still COUNTS — the floor alone would not notice.
        The script fails on any such placeholder, by name."""
        _mk_tree(self.tmp, {"test_alpha.py": _UNITTEST_MODULE,
                            "test_broken.py": "import no_such_module_xyz\n"})
        r = _run_script("--root", self.tmp, "--floor", "1", "--manifest", "")
        self.assertNotEqual(r.returncode, 0,
                            f"expected import-failure detection; {r.stdout}")
        self.assertIn("test_broken", r.stdout + r.stderr)

    def test_json_output_reports_count_and_floor(self):
        _mk_tree(self.tmp, {"test_alpha.py": _UNITTEST_MODULE})
        r = _run_script("--root", self.tmp, "--floor", "2", "--manifest", "",
                        "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["count"], 2)
        self.assertEqual(doc["floor"], 2)
        self.assertEqual(doc["ok"], True)


class TestFloorScriptAgainstTheRealTree(unittest.TestCase):
    """The pinned values must describe THIS repo — and only ratchet."""

    def test_real_tree_passes_at_the_pinned_values(self):
        r = _run_script("--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertGreaterEqual(doc["count"], doc["floor"])
        self.assertEqual(doc["pytest_only"], doc["manifest"])

    def test_store_isolation_holds_under_the_unittest_runner(self):
        """The store-isolation belt the discover leg exists to prove.

        `python -m unittest` never loads conftest.py, so the per-test
        TASKPLANE_HOME restore lives in taskplane/tests/__init__.py (a
        TestCase.run wrapper). TestHigh5StoreIsolation is the canary: _1 pops
        the var, _2 asserts the store is still not the developer's real
        ~/.taskplane. Run it through the SECOND runner, in a subprocess with
        TASKPLANE_HOME unset — exactly how ci.yml invokes the leg.

        Known residual gap, deliberately NOT papered over here: the belt uses
        `os.environ.setdefault`, so a shell that EXPORTS
        TASKPLANE_HOME=~/.taskplane before the run keeps that value and this
        canary fails — the suite would then write into the real store. The
        fix belongs in taskplane/tests/__init__.py (refuse a TASKPLANE_HOME
        that resolves to the default real store), which is outside t9's
        scope; filed as debt rather than reached for here.
        """
        env = {k: v for k, v in os.environ.items()
               if k not in ("TASKPLANE_HOME",)}
        env["PYTHONPATH"] = ROOT
        r = subprocess.run(
            [sys.executable, "-m", "unittest",
             "taskplane.tests.test_v097_fixes.TestHigh5StoreIsolation", "-v"],
            capture_output=True, text=True, cwd=ROOT, env=env, encoding="utf-8")
        self.assertEqual(
            r.returncode, 0,
            "store isolation regressed under `python -m unittest`:\n"
            + r.stdout + r.stderr)

    def test_floor_only_ratchets_up(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import ci_unittest_floor as mod
        self.assertGreaterEqual(
            mod.FLOOR, _RATCHET_FLOOR,
            "the discover-leg floor may only RISE — lowering it re-opens "
            "the erosion this guard exists to close")

    def test_manifest_only_ratchets_down(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import ci_unittest_floor as mod
        extra = set(mod.PYTEST_ONLY_MANIFEST) - _RATCHET_MANIFEST
        self.assertEqual(
            extra, set(),
            f"pytest-only manifest grew by {sorted(extra)} — the manifest "
            "may only SHRINK (convert the file or justify a plan-approved "
            "ratchet change in BOTH this literal and the script)")

    def test_ci_workflow_invokes_the_script(self):
        wf = open(os.path.join(ROOT, ".github", "workflows", "ci.yml"),
                  encoding="utf-8").read()
        self.assertIn("scripts/ci_unittest_floor.py", wf)
        self.assertIn("python -m unittest discover -s taskplane/tests -t .",
                      wf, "the discover leg itself must stay — the floor "
                          "script counts collection, it does not RUN tests")


# --------------------------------------------------- E2: env-guard self-test

_LEAKY_TEST_MODULE = """\
    import os


    def test_leaks_an_env_var():
        os.environ["TP_SCRATCH_LEAK"] = "1"
        assert True
"""

_CLEAN_TEST_MODULE = """\
    import os


    def test_restores_what_it_sets():
        os.environ["TP_SCRATCH_CLEAN"] = "1"
        del os.environ["TP_SCRATCH_CLEAN"]
        assert True
"""


class TestEnvMutationGuardSelfTest(unittest.TestCase):
    """E2: the conftest guard must NAME the module and the key it caught.

    Driven by copying the real conftest.py into a scratch tree and running
    pytest there, so the self-test exercises the shipped guard rather than a
    copy of it.
    """

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="tp-envguard-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # A scratch package that mirrors the real layout closely enough for
        # conftest.py's repo-root/sys.path dance and its `taskplane.tests`
        # import to resolve: reuse the REAL repo root, put the scratch tests
        # dir inside it under a temp name.
        self.tdir = os.path.join(self.tmp, "tests")
        os.makedirs(self.tdir)
        shutil.copy(os.path.join(HERE, "conftest.py"),
                    os.path.join(self.tdir, "conftest.py"))

    def _pytest(self, *files):
        # cwd = the scratch root (NOT the repo root): pytest's rootdir
        # inference degrades badly when cwd and the arg paths share only
        # "/", and PYTHONPATH is all the copied conftest needs to resolve
        # `taskplane.tests`.
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q",
             *[os.path.join("tests", f) for f in files]],
            capture_output=True, text=True, cwd=self.tmp,
            env={**os.environ, "PYTHONPATH": ROOT}, encoding="utf-8")

    def _write(self, name, body):
        with open(os.path.join(self.tdir, name), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(body))

    def test_guard_names_the_module_and_the_leaked_key(self):
        self._write("test_scratch_leak.py", _LEAKY_TEST_MODULE)
        r = self._pytest("test_scratch_leak.py")
        blob = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0,
                            f"the guard did not fail the leak:\n{blob}")
        self.assertIn("TP_SCRATCH_LEAK", blob,
                      "the guard must NAME the offending variable")
        self.assertIn("test_scratch_leak", blob,
                      "the guard must NAME the offending module")

    def test_guard_is_silent_when_the_module_restores_everything(self):
        self._write("test_scratch_clean.py", _CLEAN_TEST_MODULE)
        r = self._pytest("test_scratch_clean.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_conftest_declares_the_guard(self):
        src = open(os.path.join(HERE, "conftest.py"), encoding="utf-8").read()
        self.assertIn("_env_mutation_guard", src)
        self.assertIn('scope="module"', src)


if __name__ == "__main__":
    unittest.main()
