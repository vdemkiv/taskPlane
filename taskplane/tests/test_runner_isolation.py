"""Small behavioral checks for the two supported Python test runners.

The suite belongs to pytest.  CI keeps one unittest canary because unittest
does not load ``conftest.py`` and therefore exercises the independent store
isolation installed by ``taskplane.tests``.  The rest of the former
unittest-discovery floor/manifest was bookkeeping about test shape, not
product behavior.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from taskplane.tests import isolated_test_runtime


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
_MODULE_RUNTIME = None


def setUpModule():
    """Enter isolation when unittest loads this module by its exact name.

    Named unittest loading bypasses the package ``load_tests`` protocol.  A
    module fixture is the narrow supported hook that starts at execution time
    (not import time) and is paired with ``tearDownModule`` even when a test
    fails.
    """
    global _MODULE_RUNTIME
    if _MODULE_RUNTIME is not None:
        raise RuntimeError("unittest module isolation is already active")
    context = isolated_test_runtime()
    context.__enter__()
    _MODULE_RUNTIME = context


def tearDownModule():
    """Restore every process binding installed by ``setUpModule``."""
    global _MODULE_RUNTIME
    context = _MODULE_RUNTIME
    _MODULE_RUNTIME = None
    if context is not None:
        context.__exit__(None, None, None)


class TestUnittestRunnerIsolation(unittest.TestCase):
    def test_store_is_not_the_real_user_store(self):
        configured = os.environ.get("TASKPLANE_HOME", "")
        real_store = os.path.abspath(os.path.expanduser("~/.taskplane"))
        self.assertTrue(configured, "the test package must isolate TASKPLANE_HOME")
        self.assertNotEqual(os.path.abspath(configured), real_store)


class TestEnvironmentMutationGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tp-envguard-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tests = os.path.join(self.tmp, "tests")
        os.makedirs(self.tests)
        shutil.copy(os.path.join(HERE, "conftest.py"),
                    os.path.join(self.tests, "conftest.py"))

    def _run(self, name: str, body: str) -> subprocess.CompletedProcess[str]:
        with open(os.path.join(self.tests, name), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(body))
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", os.path.join("tests", name)],
            capture_output=True,
            text=True,
            cwd=self.tmp,
            env={**os.environ, "PYTHONPATH": ROOT},
            encoding="utf-8",
            errors="replace",
        )

    def test_leak_is_named_and_rejected(self):
        result = self._run(
            "test_scratch_leak.py",
            """
            import os

            def test_leaks_an_env_var():
                os.environ["TP_SCRATCH_LEAK"] = "1"
            """,
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("TP_SCRATCH_LEAK", output)
        self.assertIn("test_scratch_leak", output)

    def test_restored_environment_passes(self):
        result = self._run(
            "test_scratch_clean.py",
            """
            import os

            def test_restores_what_it_sets():
                os.environ["TP_SCRATCH_CLEAN"] = "1"
                del os.environ["TP_SCRATCH_CLEAN"]
            """,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
