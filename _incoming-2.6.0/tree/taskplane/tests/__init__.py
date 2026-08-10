"""Session-level store isolation for BOTH test runners (v2.3.0).

pytest reads conftest.py; `python -m unittest discover` does NOT — it imports
this package instead. The v2.2.1 review demonstrated the gap live: a unittest
run wrote into (and deleted governance state from) the developer's real
~/.taskplane. Putting the belt here means importing ANY test module —
under either runner — pins TASKPLANE_HOME to a throwaway temp dir first.

conftest.py keeps the per-test autouse fixture (suspenders) on top.
"""
import atexit
import os
import shutil
import tempfile
import unittest

# TEMP-DIR LEAK (R-0013, found the hard way). Test modules call
# `tempfile.mkdtemp()` in setUp and mostly never remove the result, so every
# suite run left workspaces behind. On this project's own container that
# reached 185,541 directories and about 30 GB, filling the disk until every
# command failed on write — and no review caught it, because nothing about a
# single run looks wrong.
#
# The fix is one root, not sixty cleanups: point `tempfile` at a
# session-scoped directory so EVERY mkdtemp/mkstemp in the suite (and in the
# engine code under test) lands inside it, then remove that one root at
# exit. Runs under both runners because this module is imported by both.
#
# Deliberately still under the system temp dir, so a test asserting a
# path prefix keeps passing, and ignore_errors so a locked file at exit
# costs disk, never a red suite.
_TMP_ROOT = tempfile.mkdtemp(prefix="tp-tests-")
tempfile.tempdir = _TMP_ROOT
os.environ["TMPDIR"] = _TMP_ROOT
atexit.register(shutil.rmtree, _TMP_ROOT, ignore_errors=True)

_SESSION_HOME = tempfile.mkdtemp(prefix="tp-store-test-")
# setdefault, not overwrite: an outer harness that already isolated the store
# (e.g. CI exporting TASKPLANE_HOME) keeps its choice.
os.environ.setdefault("TASKPLANE_HOME", _SESSION_HOME)


# Per-test restore for the `python -m unittest` runner (v2.3.1). conftest.py's
# autouse fixture gives pytest a fresh TASKPLANE_HOME per test and restores it;
# the unittest runner never sees conftest, so a test that pops/overwrites the
# var (test_external_store, test_northstar, …) used to leave EVERY later test
# running against the developer's real ~/.taskplane — a real, reproduced leak.
# Wrapping TestCase.run snapshots the var around each test under BOTH runners,
# so no single test can leak the store into the next. Idempotent under pytest
# (double-restore is harmless).
if not getattr(unittest.TestCase, "_tp_isolated", False):
    _orig_run = unittest.TestCase.run

    def _isolating_run(self, result=None):
        saved = os.environ.get("TASKPLANE_HOME")
        try:
            return _orig_run(self, result)
        finally:
            if saved is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = saved

    unittest.TestCase.run = _isolating_run
    unittest.TestCase._tp_isolated = True
