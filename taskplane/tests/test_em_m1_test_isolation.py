"""Focused M-24/L-10 test-runner and runtime-binding evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pytest

import taskplane.tests as test_package
from taskplane.tests import isolated_test_runtime


ROOT = Path(__file__).resolve().parents[2]






def test_m24_test_package_import_has_no_process_global_side_effect():
    script = r'''
import json
import os
import shutil
import tempfile
import unittest

before = {
    "env": {key: os.environ.get(key) for key in
            ("TMPDIR", "PYTHONIOENCODING", "TASKPLANE_HOME")},
    "tempdir": tempfile.tempdir,
    "rmtree": id(shutil.rmtree),
    "run": id(unittest.TestCase.run),
    "force": getattr(shutil, "_tp_force_rmtree", None),
    "isolated": getattr(unittest.TestCase, "_tp_isolated", None),
}
original_mkdtemp = tempfile.mkdtemp
def forbidden(*args, **kwargs):
    raise AssertionError("package import created a temp directory")
tempfile.mkdtemp = forbidden
import taskplane.tests
after = {
    "env": {key: os.environ.get(key) for key in
            ("TMPDIR", "PYTHONIOENCODING", "TASKPLANE_HOME")},
    "tempdir": tempfile.tempdir,
    "rmtree": id(shutil.rmtree),
    "run": id(unittest.TestCase.run),
    "force": getattr(shutil, "_tp_force_rmtree", None),
    "isolated": getattr(unittest.TestCase, "_tp_isolated", None),
}
assert tempfile.mkdtemp is forbidden
assert after == before, (before, after)
print(json.dumps(after, sort_keys=True))
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["tempdir"] is None


def test_m24_runner_scope_restores_every_mutated_process_binding(monkeypatch):
    import shutil
    import unittest

    inherited = {"CLAUDE_SESSION_ID": "host-session", "CODEX_THREAD_ID": "host-thread",
                 "TASKPLANE_TASK": "host-task"}
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    names = ("TMPDIR", "PYTHONIOENCODING", "TASKPLANE_HOME", *inherited)
    environment = {name: os.environ.get(name) for name in names}
    before = (tempfile.tempdir, shutil.rmtree, unittest.TestCase.run)
    with isolated_test_runtime() as runtime:
        root = runtime["tmp_root"]
        assert tempfile.tempdir == root
        assert tempfile.mkdtemp().startswith(root)
        assert os.environ["TMPDIR"] == root
        assert not any(name in os.environ for name in inherited)
        with isolated_test_runtime(host_environment={"CODEX_THREAD_ID": "explicit-test"}):
            assert os.environ["CODEX_THREAD_ID"] == "explicit-test"
            assert "CLAUDE_SESSION_ID" not in os.environ
        assert "CODEX_THREAD_ID" not in os.environ
        os.environ["TASKPLANE_HOME"] = "mutated-by-test"
        assert shutil.rmtree is not before[1]
        assert unittest.TestCase.run is not before[2]
    assert (tempfile.tempdir, shutil.rmtree, unittest.TestCase.run) == before
    assert {name: os.environ.get(name) for name in names} == environment
    assert not os.path.exists(root)


def test_m24_partial_runner_entry_restores_prior_bindings(monkeypatch):
    import shutil

    names = ("TMPDIR", "PYTHONIOENCODING", "TASKPLANE_HOME")
    environment = {name: os.environ.get(name) for name in names}
    before = (tempfile.tempdir, shutil.rmtree, unittest.TestCase.run)
    original_mkdtemp = tempfile.mkdtemp
    created = []

    def fail_session_home(*args, **kwargs):
        if created:
            raise OSError("injected session-home allocation failure")
        path = original_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", fail_session_home)
    with pytest.raises(OSError, match="injected session-home"):
        with isolated_test_runtime():
            raise AssertionError("unreachable")

    assert (tempfile.tempdir, shutil.rmtree, unittest.TestCase.run) == before
    assert {name: os.environ.get(name) for name in names} == environment
    assert len(created) == 1 and not os.path.exists(created[0])


def test_m24_unittest_bootstrap_scopes_discovery_and_each_case():
    before_home = os.environ.get("TASKPLANE_HOME")

    class MutatingCase(unittest.TestCase):
        def runTest(self):
            assert os.environ.get("TASKPLANE_HOME")
            os.environ.pop("TASKPLANE_HOME", None)

    class RestoredCase(unittest.TestCase):
        def runTest(self):
            assert os.environ.get("TASKPLANE_HOME")

    class Loader:
        def discover(self, start_dir, *, pattern, top_level_dir):
            assert tempfile.tempdir and "tp-tests-" in tempfile.tempdir
            assert start_dir.endswith(os.path.join("taskplane", "tests"))
            assert pattern == "test*.py"
            assert top_level_dir == str(ROOT)
            return unittest.TestSuite([MutatingCase(), RestoredCase()])

    suite = test_package.load_tests(Loader(), unittest.TestSuite(), None)
    assert os.environ.get("TASKPLANE_HOME") == before_home
    result = unittest.TestResult()
    suite.run(result)
    assert result.wasSuccessful(), (result.failures, result.errors)
    assert os.environ.get("TASKPLANE_HOME") == before_home


def test_m24_named_unittest_path_scopes_success_and_failure():
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    for name in ("TASKPLANE_HOME", "TMPDIR", "PYTHONIOENCODING"):
        environment.pop(name, None)

    command = [
        sys.executable,
        "-m",
        "unittest",
        "taskplane.tests.test_runner_isolation.TestUnittestRunnerIsolation",
        "-v",
    ]
    success = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert success.returncode == 0, success.stdout + success.stderr
    assert "test_store_is_not_the_real_user_store" in (
        success.stdout + success.stderr)

    failure_script = r'''
import os
import shutil
import tempfile
import unittest

before = {
    "environment": {key: os.environ.get(key) for key in
                    ("TMPDIR", "PYTHONIOENCODING", "TASKPLANE_HOME")},
    "tempdir": tempfile.tempdir,
    "rmtree": shutil.rmtree,
    "run": unittest.TestCase.run,
    "force": getattr(shutil, "_tp_force_rmtree", None),
    "isolated": getattr(unittest.TestCase, "_tp_isolated", None),
}
import taskplane.tests.test_runner_isolation as runner_module

after_import = {
    "environment": {key: os.environ.get(key) for key in
                    ("TMPDIR", "PYTHONIOENCODING", "TASKPLANE_HOME")},
    "tempdir": tempfile.tempdir,
    "rmtree": shutil.rmtree,
    "run": unittest.TestCase.run,
    "force": getattr(shutil, "_tp_force_rmtree", None),
    "isolated": getattr(unittest.TestCase, "_tp_isolated", None),
}
assert after_import == before

class FailingCase(unittest.TestCase):
    def runTest(self):
        assert os.environ.get("TASKPLANE_HOME")
        assert tempfile.tempdir and "tp-tests-" in tempfile.tempdir
        os.environ["TASKPLANE_HOME"] = "must-not-leak"
        self.fail("expected adversarial failure")

FailingCase.__module__ = runner_module.__name__
result = unittest.TestResult()
unittest.TestSuite([FailingCase()]).run(result)
assert len(result.failures) == 1 and not result.errors

after_failure = {
    "environment": {key: os.environ.get(key) for key in
                    ("TMPDIR", "PYTHONIOENCODING", "TASKPLANE_HOME")},
    "tempdir": tempfile.tempdir,
    "rmtree": shutil.rmtree,
    "run": unittest.TestCase.run,
    "force": getattr(shutil, "_tp_force_rmtree", None),
    "isolated": getattr(unittest.TestCase, "_tp_isolated", None),
}
assert after_failure == before, (before, after_failure)
'''
    failure = subprocess.run(
        [sys.executable, "-c", failure_script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert failure.returncode == 0, failure.stdout + failure.stderr
