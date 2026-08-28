"""Focused M-24/L-10 test-runner and runtime-binding evidence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import pytest

from taskplane import build_c
import taskplane.tests as test_package
from taskplane.tests import isolated_test_runtime


ROOT = Path(__file__).resolve().parents[2]


def _runtime(label: str):
    def state_loader(workspace: str):
        return {"label": label, "workspace": workspace}

    def wait_policy_factory(_phase: str, count: int):
        return {
            "schema": "taskplane.wait-policy/v1",
            "mode": "event",
            "scheduled_polling": False,
            "timeout_seconds": 1800,
            "reissue_after": ["completion", "attention"],
            "outstanding_count": count,
            "outstanding_set": f"set-{label}",
        }

    def wait_invocation_factory(policy, members):
        return {
            "schema": "taskplane.event-wait-invocation/v1",
            "operation": "wait_for_events",
            "scheduled": False,
            "reissue": False,
            "outstanding_members": list(members),
            "label": label,
            "policy": policy["outstanding_set"],
        }

    return {
        "state_loader": state_loader,
        "wait_policy_factory": wait_policy_factory,
        "wait_invocation_factory": wait_invocation_factory,
    }


def _observed_runtime(label: str) -> tuple[str, str]:
    state = build_c._integration_state(f"workspace-{label}")
    _policy, invocation = build_c._assignment_wait(
        [f"member-{label}"],
        wait_policy_factory=None,
        wait_invocation_factory=None,
    )
    return state["label"], invocation["label"]


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


def test_m24_runner_scope_restores_every_mutated_process_binding():
    import shutil
    import unittest

    names = ("TMPDIR", "PYTHONIOENCODING", "TASKPLANE_HOME")
    environment = {name: os.environ.get(name) for name in names}
    before = (tempfile.tempdir, shutil.rmtree, unittest.TestCase.run)
    with isolated_test_runtime() as runtime:
        root = runtime["tmp_root"]
        assert tempfile.tempdir == root
        assert tempfile.mkdtemp().startswith(root)
        assert os.environ["TMPDIR"] == root
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


def test_l10_nested_and_parallel_runtime_bindings_restore_safely():
    outer = _runtime("outer")
    inner = _runtime("inner")
    with build_c.scoped_loop_runtime(**outer):
        assert _observed_runtime("outer") == ("outer", "outer")
        with pytest.raises(RuntimeError, match="adversarial unwind"):
            with build_c.scoped_loop_runtime(**inner):
                assert _observed_runtime("inner") == ("inner", "inner")
                raise RuntimeError("adversarial unwind")
        assert _observed_runtime("outer") == ("outer", "outer")

        barrier = threading.Barrier(3)

        def use(label: str):
            with build_c.scoped_loop_runtime(**_runtime(label)):
                barrier.wait(timeout=5)
                first = _observed_runtime(label)
                barrier.wait(timeout=5)
                second = _observed_runtime(label)
                return first, second

        with ThreadPoolExecutor(max_workers=2) as pool:
            left = pool.submit(use, "left")
            right = pool.submit(use, "right")
            barrier.wait(timeout=5)
            assert _observed_runtime("outer") == ("outer", "outer")
            barrier.wait(timeout=5)
        assert left.result() == (("left", "left"), ("left", "left"))
        assert right.result() == (("right", "right"), ("right", "right"))
        assert _observed_runtime("outer") == ("outer", "outer")


def test_l10_rejects_invalid_scoped_dependencies_before_mutation():
    outer = _runtime("outer")
    with build_c.scoped_loop_runtime(**outer):
        with pytest.raises(TypeError, match="must be callable"):
            with build_c.scoped_loop_runtime(
                    state_loader=None,
                    wait_policy_factory=outer["wait_policy_factory"],
                    wait_invocation_factory=outer["wait_invocation_factory"]):
                raise AssertionError("unreachable")
        assert _observed_runtime("outer") == ("outer", "outer")


@pytest.mark.parametrize("first_module,second_module", [
    ("loop", "taskplane.loop"),
    ("taskplane.loop", "loop"),
])
def test_l10_loop_import_alias_binding_is_exact_and_idempotent(
        first_module, second_module):
    script = r'''
import importlib
import os
import sys

root, first_name, second_name = sys.argv[1:]
sys.path.insert(0, os.path.join(root, "taskplane"))
sys.path.insert(0, root)
first = importlib.import_module(first_name)
import build_c
before = build_c._default_loop_runtime_services
assert before is not None
second = importlib.import_module(second_name)
after = build_c._default_loop_runtime_services
assert after is before
assert before.state_loader is first.load
assert before.wait_policy_factory is first.event_wait_policy
assert before.wait_invocation_factory is first.event_wait_invocation
assert second.load is not first.load
assert second.load.__module__ != first.load.__module__
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(ROOT), first_module, second_module],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_l10_loop_runtime_hostile_rebinds_remain_rejected():
    script = r'''
import os
import sys
import types

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "taskplane"))
sys.path.insert(0, root)
import loop
import build_c

current = build_c._default_loop_runtime_services
assert current is not None

def hostile_state_loader(workspace):
    return {"hostile": workspace}

# Spoofing the same raw module name cannot turn different code into an alias.
hostile_state_loader.__module__ = loop.load.__module__
try:
    build_c.bind_loop_runtime(
        state_loader=hostile_state_loader,
        wait_policy_factory=loop.event_wait_policy,
        wait_invocation_factory=loop.event_wait_invocation,
    )
except RuntimeError:
    pass
else:
    raise AssertionError("different code in the same module was accepted")

# The supported opposite alias name is still insufficient when code differs.
hostile_state_loader.__module__ = "taskplane.loop"
try:
    build_c.bind_loop_runtime(
        state_loader=hostile_state_loader,
        wait_policy_factory=loop.event_wait_policy,
        wait_invocation_factory=loop.event_wait_invocation,
    )
except RuntimeError:
    pass
else:
    raise AssertionError("different code under the alias name was accepted")

# Even byte-identical code is not an alias when it comes from another module.
lookalike = types.FunctionType(
    loop.load.__code__, loop.load.__globals__, loop.load.__name__,
    loop.load.__defaults__, loop.load.__closure__)
lookalike.__kwdefaults__ = loop.load.__kwdefaults__
lookalike.__annotations__ = dict(loop.load.__annotations__)
lookalike.__module__ = "hostile.loop"
lookalike.__qualname__ = loop.load.__qualname__
try:
    build_c.bind_loop_runtime(
        state_loader=lookalike,
        wait_policy_factory=loop.event_wait_policy,
        wait_invocation_factory=loop.event_wait_invocation,
    )
except RuntimeError:
    pass
else:
    raise AssertionError("same code from a non-alias module was accepted")

assert build_c._default_loop_runtime_services is current
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(ROOT)],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
