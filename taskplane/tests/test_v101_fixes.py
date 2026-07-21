"""v1.0.1 fixes — regression tests.

3.4  FUSE-safe removal: clear()/legacy-migrate/cmd_clear must survive
     filesystems that forbid unlink (rename-tombstone fallback).
3.2  Dispatch expectation queue + verify-dispatch audit.
3.1  screen-dispatch hook: inert by default; warn/strict on mismatch.
3.5  done/external statuses satisfy deps; resolve defer.
3.7  onboard report exposes resolved model tiers.
"""
import builtins
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402

TPPY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tp.py")


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w").write("x = 1\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def _deny_unlink():
    """Patch os.remove/os.unlink to behave like a no-unlink FUSE mount."""
    def boom(*a, **k):
        raise PermissionError(1, "Operation not permitted")
    return mock.patch.multiple(os, remove=boom, unlink=boom)


class TestSafeRemove(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_clear_survives_no_unlink_mount(self):
        ws = _repo(self.tmp)
        tp.activate(ws, {"task_id": "t", "goal": "g",
                         "coding": {"scope_paths": ["src/**"],
                                    "dod": {"test_command": None}}})
        path = os.path.join(tp.tp_dir(ws), "active_contract.json")
        self.assertTrue(os.path.exists(path))
        with _deny_unlink():
            tp.clear(ws)                      # must not raise
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(tp.load_active(ws))

    def test_gate_advances_on_no_unlink_mount(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")
        with _deny_unlink():
            out = loop.gate(ws, "pass")       # pm -> plan calls tp.clear
        self.assertNotIn("error", out)
        self.assertEqual(loop.load(ws)["step"], "plan")

    def test_safe_remove_plain_delete_still_works(self):
        p = os.path.join(self.tmp, "f")
        open(p, "w").write("x")
        tp.safe_remove(p)
        self.assertFalse(os.path.exists(p))
        tp.safe_remove(p)                     # missing: no raise


class TestDispatchQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_loop_next_records_expectation(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")
        loop.next_action(ws)                  # pm brief
        q = tp._load_queue(tp._dispatch_path(ws, "expected_dispatch.json"))
        self.assertTrue(q)
        self.assertEqual(q[-1]["agent"], "tp-product")
        self.assertIn(q[-1]["model_tier"], tp.MODEL_TIERS)

    def test_consume_matches_oldest_by_agent_and_namespaced(self):
        ws = _repo(self.tmp)
        os.makedirs(tp.tp_dir(ws), exist_ok=True)
        tp.record_expected_dispatch(ws, "lens", "tp-lens", "cheap", "haiku",
                                    ref="sweep")
        tp.record_expected_dispatch(ws, "lens", "tp-lens", "deep", None,
                                    ref="security")
        e = tp.consume_expectation(ws, "taskplane:tp-lens")
        self.assertEqual(e["ref"], "sweep")   # oldest first
        e2 = tp.consume_expectation(ws, "tp-lens")
        self.assertEqual(e2["ref"], "security")
        self.assertIsNone(tp.consume_expectation(ws, "tp-lens"))

    def test_report_flags_hook_inactive(self):
        ws = _repo(self.tmp)
        os.makedirs(tp.tp_dir(ws), exist_ok=True)
        tp.record_expected_dispatch(ws, "step", "tp-executor", "standard",
                                    None, ref="t1")
        rep = tp.dispatch_report(ws)
        self.assertFalse(rep["hook_active"])
        self.assertIn("TASKPLANE_ENFORCE_DISPATCH", rep["note"])

    def test_report_mismatch(self):
        ws = _repo(self.tmp)
        os.makedirs(tp.tp_dir(ws), exist_ok=True)
        exp = {"kind": "lens", "agent": "tp-lens", "ref": "sweep",
               "model_tier": "cheap", "model": "haiku"}
        tp.record_observed_dispatch(ws, "tp-lens", None, exp, ok=False)
        rep = tp.dispatch_report(ws)
        self.assertTrue(rep["hook_active"])
        self.assertEqual(len(rep["mismatches"]), 1)
        self.assertEqual(rep["mismatches"][0]["expected_model"], "haiku")


class TestScreenDispatchHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        os.makedirs(tp.tp_dir(self.ws), exist_ok=True)

    def _run(self, event, env_mode=None):
        env = {**os.environ}
        env.pop("TASKPLANE_ENFORCE_DISPATCH", None)
        if env_mode:
            env["TASKPLANE_ENFORCE_DISPATCH"] = env_mode
        return subprocess.run(
            [sys.executable, TPPY, "screen-dispatch"],
            input=json.dumps(event), text=True, capture_output=True, env=env)

    def _event(self, model=None):
        ti = {"subagent_type": "taskplane:tp-lens", "prompt": "x"}
        if model is not None:
            ti["model"] = model
        return {"tool_name": "Task", "tool_input": ti, "cwd": self.ws}

    def test_inert_without_env(self):
        tp.record_expected_dispatch(self.ws, "lens", "tp-lens", "cheap",
                                    "haiku", ref="sweep")
        r = self._run(self._event(model=None))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_warn_on_missing_model(self):
        tp.record_expected_dispatch(self.ws, "lens", "tp-lens", "cheap",
                                    "haiku", ref="sweep")
        r = self._run(self._event(model=None), env_mode="warn")
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertIn("haiku", out["systemMessage"])
        self.assertIn("sweep", out["systemMessage"])

    def test_strict_denies_mismatch(self):
        tp.record_expected_dispatch(self.ws, "lens", "tp-lens", "cheap",
                                    "haiku", ref="sweep")
        r = self._run(self._event(model="opus"), env_mode="strict")
        out = json.loads(r.stdout)
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_match_and_inherit_expected_are_silent(self):
        tp.record_expected_dispatch(self.ws, "lens", "tp-lens", "cheap",
                                    "haiku", ref="sweep")
        r = self._run(self._event(model="haiku"), env_mode="warn")
        self.assertEqual(r.stdout.strip(), "")
        tp.record_expected_dispatch(self.ws, "step", "tp-executor",
                                    "standard", None, ref="t1")
        ev = self._event(model=None)
        ev["tool_input"]["subagent_type"] = "taskplane:tp-executor"
        r = self._run(ev, env_mode="warn")
        self.assertEqual(r.stdout.strip(), "")   # expected None = inherit ok

    def test_unexpected_agent_is_silent(self):
        r = self._run(self._event(model=None), env_mode="warn")
        self.assertEqual(r.stdout.strip(), "")


class TestStatuses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _seed(self, ws, statuses):
        loop.init(ws, "g", parallel=True)
        st = loop.load(ws)
        st["step"] = "execute"
        st["tasks"] = [
            {"id": "t1", "scope": ["src/a/**"], "status": statuses[0]},
            {"id": "t2", "scope": ["src/b/**"], "deps": ["t1"],
             "status": statuses[1]},
        ]
        loop.save(ws, st)

    def test_done_seed_satisfies_dep(self):
        ws = _repo(self.tmp)
        self._seed(ws, ["done", "pending"])
        w = loop.wave(ws)
        ids = [e["task"]["id"] for e in w.get("wave", [])]
        self.assertIn("t2", ids, w)

    def test_external_satisfies_dep(self):
        ws = _repo(self.tmp)
        self._seed(ws, ["external", "pending"])
        w = loop.wave(ws)
        ids = [e["task"]["id"] for e in w.get("wave", [])]
        self.assertIn("t2", ids, w)

    def test_resolve_defer_sets_external(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")
        st = loop.load(ws)
        st.update({"step": "escalated", "current_task": 0,
                   "tasks": [{"id": "t1", "scope": ["src/**"],
                              "status": "running"}]})
        loop.save(ws, st)
        out = loop.resolve(ws, "defer")
        self.assertNotIn("error", out)
        self.assertEqual(loop.load(ws)["tasks"][0]["status"], "external")

    def test_bad_decision_lists_defer(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")
        st = loop.load(ws)
        st.update({"step": "escalated",
                   "tasks": [{"id": "t1", "status": "running"}],
                   "current_task": 0})
        loop.save(ws, st)
        self.assertIn("defer", loop.resolve(ws, "nope")["error"])


class TestOnboardTiers(unittest.TestCase):
    def test_onboard_reports_resolved_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _repo(tmp)
            env = {**os.environ, "TASKPLANE_MODEL_DEEP": "opus"}
            r = subprocess.run([sys.executable, TPPY, "onboard", "--json",
                                "--workspace", ws], capture_output=True,
                               text=True, env=env)
            rep = json.loads(r.stdout)
            self.assertEqual(rep["model_tiers"]["cheap"], "haiku")
            self.assertEqual(rep["model_tiers"]["standard"], "inherit")
            self.assertEqual(rep["model_tiers"]["deep"], "opus")


if __name__ == "__main__":
    unittest.main()
