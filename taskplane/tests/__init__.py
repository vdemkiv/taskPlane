"""Opt-in test-runner isolation without package-import side effects.

Importing :mod:`taskplane.tests` is intentionally inert. The two supported
test runners opt into :func:`isolated_test_runtime` instead: unittest through
``load_tests`` and pytest through its existing ``_SESSION_HOME`` bootstrap
import. Every process-global change made by the bootstrap is restored at
runner shutdown.
"""

from __future__ import annotations

import atexit
from contextlib import contextmanager
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest


_MISSING = object()
_RUNTIME_LOCK = threading.RLock()
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAP_CONTEXT = None
_BOOTSTRAP_STATE = None


def _restore_environment(name: str, value: object) -> None:
    if value is _MISSING:
        os.environ.pop(name, None)
    else:
        os.environ[name] = str(value)


def _clear_readonly_and_retry(func, target, _exc) -> None:
    """Make a read-only checkout entry removable, then retry once."""
    for path in (os.path.dirname(target), target):
        try:
            os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
    func(target)


@contextmanager
def isolated_test_runtime():
    """Scope test-store, temp-dir, and runner patches to one runner.

    The lock deliberately serializes these process-global bindings. Nested
    use is supported and restores in LIFO order; concurrent callers cannot
    observe a half-installed or half-restored runtime.
    """
    with _RUNTIME_LOCK:
        saved_tempdir = tempfile.tempdir
        saved_rmtree = shutil.rmtree
        saved_force_marker = getattr(shutil, "_tp_force_rmtree", _MISSING)
        saved_testcase_run = unittest.TestCase.run
        saved_isolated_marker = getattr(
            unittest.TestCase, "_tp_isolated", _MISSING)
        saved_environment = {
            name: os.environ.get(name, _MISSING)
            for name in ("TMPDIR", "PYTHONIOENCODING", "TASKPLANE_HOME")
        }

        # Create the aggregation root before redirecting tempfile itself.
        _TMP_ROOT = os.path.realpath(tempfile.mkdtemp(prefix="tp-tests-"))
        tempfile.tempdir = _TMP_ROOT
        os.environ["TMPDIR"] = _TMP_ROOT
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        _SESSION_HOME = tempfile.mkdtemp(prefix="tp-store-test-")
        os.environ.setdefault("TASKPLANE_HOME", _SESSION_HOME)

        def _force_rmtree(path, ignore_errors=False, **kwargs):
            if ignore_errors or kwargs.get("onerror") or kwargs.get("onexc"):
                return saved_rmtree(
                    path, ignore_errors=ignore_errors, **kwargs)
            if sys.version_info >= (3, 12):
                kwargs["onexc"] = (
                    lambda f, t, e: _clear_readonly_and_retry(f, t, e))
            else:
                kwargs["onerror"] = (
                    lambda f, t, e: _clear_readonly_and_retry(f, t, e))
            return saved_rmtree(path, **kwargs)

        def _isolating_run(self, result=None):
            saved_home = os.environ.get("TASKPLANE_HOME", _MISSING)
            try:
                return saved_testcase_run(self, result)
            finally:
                _restore_environment("TASKPLANE_HOME", saved_home)

        shutil.rmtree = _force_rmtree
        shutil._tp_force_rmtree = True
        unittest.TestCase.run = _isolating_run
        unittest.TestCase._tp_isolated = True
        try:
            yield {"tmp_root": _TMP_ROOT, "session_home": _SESSION_HOME}
        finally:
            # Restore exact identities only after the runner has stopped.
            if unittest.TestCase.run is _isolating_run:
                unittest.TestCase.run = saved_testcase_run
            if saved_isolated_marker is _MISSING:
                try:
                    del unittest.TestCase._tp_isolated
                except AttributeError:
                    pass
            else:
                unittest.TestCase._tp_isolated = saved_isolated_marker
            if shutil.rmtree is _force_rmtree:
                shutil.rmtree = saved_rmtree
            if saved_force_marker is _MISSING:
                try:
                    del shutil._tp_force_rmtree
                except AttributeError:
                    pass
            else:
                shutil._tp_force_rmtree = saved_force_marker
            tempfile.tempdir = saved_tempdir
            for name, value in saved_environment.items():
                _restore_environment(name, value)
            saved_rmtree(_TMP_ROOT, ignore_errors=True)


def _stop_runner_bootstrap() -> None:
    global _BOOTSTRAP_CONTEXT, _BOOTSTRAP_STATE
    context = _BOOTSTRAP_CONTEXT
    _BOOTSTRAP_CONTEXT = None
    _BOOTSTRAP_STATE = None
    globals().pop("_TMP_ROOT", None)
    globals().pop("_SESSION_HOME", None)
    if context is not None:
        context.__exit__(None, None, None)


def _start_runner_bootstrap() -> dict:
    """Enter the compatibility bootstrap only when a runner requests it."""
    global _BOOTSTRAP_CONTEXT, _BOOTSTRAP_STATE
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAP_STATE is None:
            context = isolated_test_runtime()
            state = context.__enter__()
            _BOOTSTRAP_CONTEXT = context
            _BOOTSTRAP_STATE = state
            globals()["_TMP_ROOT"] = state["tmp_root"]
            globals()["_SESSION_HOME"] = state["session_home"]
            atexit.register(_stop_runner_bootstrap)
        return _BOOTSTRAP_STATE


def __getattr__(name: str):
    # Existing pytest bootstrap imports _SESSION_HOME explicitly. Keeping
    # this compatibility attribute lazy makes a plain package import inert.
    if name in {"_SESSION_HOME", "_TMP_ROOT"}:
        state = _start_runner_bootstrap()
        return state["session_home" if name == "_SESSION_HOME" else "tmp_root"]
    raise AttributeError(name)


class _RunnerScopedSuite(unittest.TestSuite):
    """Run discovered unittest tests inside one restoring runtime scope."""

    def run(self, result, debug=False):
        with isolated_test_runtime():
            return super().run(result, debug)


def load_tests(loader, tests, pattern):
    """Provide isolated recursive discovery without import-time mutation."""
    package_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(package_dir))
    with isolated_test_runtime():
        discovered = loader.discover(
            package_dir, pattern=pattern or "test*.py", top_level_dir=repo_root)
    return _RunnerScopedSuite(discovered)
