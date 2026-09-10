"""DoR/DoD visibility wiring (v1.0.0):
- DoR is computed every loop step and now traced with blockers/warnings, and the
  dashboard renders a DoR strip from it.
- The mechanical DoD (scope-diff + KB-lint) now runs at the sign-off gate
  (loop._signoff_dod) and is surfaced in the next_action payload + dashboard.
"""
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
import dashboard  # noqa: E402
import lens  # noqa: E402
from taskplane.tests.review_kernel_support import complete_review  # noqa: E402


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    open(os.path.join(ws, "README.md"), "w", encoding="utf-8").write("# readme\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def _head(ws):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws,
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()








if __name__ == "__main__":
    unittest.main()


class TestTaskDoDLoopOwnedExclusion(unittest.TestCase):
    """A2 (R-0007): the per-task DoD scope-diff excludes lens.LOOP_OWNED —
    parity with the sign-off aggregate's exclusion — while every NON-loop-
    owned out-of-scope file still errors (no loosening)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    TASK = {"id": "t1", "scope": ["src/**"], "tests": "true",
            "criteria": ["works"]}

    def _state(self):
        return {"goal": "g", "step": "execute", "current_task": 0,
                "tasks": [dict(self.TASK)]}

    def test_loop_owned_artifact_no_longer_trips_the_task_dod(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        # orchestrator-synced loop artifacts land in the task's diff
        os.makedirs(os.path.join(ws, "design"), exist_ok=True)
        open(os.path.join(ws, "design", "contract.json"), "w", encoding="utf-8").write("{}\n")
        os.makedirs(os.path.join(ws, "specs"), exist_ok=True)
        open(os.path.join(ws, "specs", "spec.md"), "w", encoding="utf-8").write("# s\n")
        errs = loop._task_dod_errors(ws, self._state(), dict(self.TASK), base)
        self.assertFalse(any(e.startswith("diff_scope") for e in errs), errs)

    def test_non_loop_owned_out_of_scope_still_errors(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        open(os.path.join(ws, "README.md"), "w", encoding="utf-8").write("# changed\n")
        errs = loop._task_dod_errors(ws, self._state(), dict(self.TASK), base)
        self.assertTrue(any(e.startswith("diff_scope") for e in errs), errs)

    def test_exclusion_source_is_lens_loop_owned_exactly(self):
        # parity pin: the excluded set IS lens.LOOP_OWNED — the same tuple
        # the sign-off aggregate filters — passed via dod_check's
        # ignore_prefixes; without it the identical diff fails.
        ws = _repo(self.tmp)
        base = _head(ws)
        os.makedirs(os.path.join(ws, "design"), exist_ok=True)
        open(os.path.join(ws, "design", "contract.json"), "w", encoding="utf-8").write("{}\n")
        c = tp.build_contract("T", scope=["src/**"], plan_minted=True)
        with_excl = tp.dod_check(c, ws, base,
                                 ignore_prefixes=lens.LOOP_OWNED)
        without = tp.dod_check(c, ws, base)
        self.assertFalse(any(e.startswith("diff_scope") for e in with_excl),
                         with_excl)
        self.assertTrue(any(e.startswith("diff_scope") for e in without))

    def test_parallel_gate_path_uses_the_exclusion(self):
        # the wave gate's wt_precheck path goes through _task_dod_errors:
        # a loop-owned file inside the WORKTREE diff must not block the gate
        ws = _repo(self.tmp)
        base = _head(ws)
        os.makedirs(os.path.join(ws, "plan"), exist_ok=True)
        open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8").write("[]\n")
        state = self._state()
        state["parallel"] = True
        errs = loop._task_dod_errors(ws, state, dict(self.TASK), base)
        self.assertFalse(any(e.startswith("diff_scope") for e in errs), errs)


class TestDodCheckSlotEnvSanitization(unittest.TestCase):
    """A3 (R-0007): dod_check's test subprocess must not inherit
    TASKPLANE_TASK — a gate run under a wave slot leaked the slot into
    slot-sensitive tests. The parent env stays untouched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _canary_cmd(self, out):
        return (sys.executable + " -c \"import os;"
                "open(r'" + out + "','w').write("
                "os.environ.get('TASKPLANE_TASK','<absent>')"
                "+'|'+os.environ.get('TP_CANARY','<absent>'))\"")

    def test_slot_is_stripped_from_the_child_env_only(self):
        ws = _repo(self.tmp)
        out = os.path.join(ws, "src", "envprobe.txt")
        c = tp.build_contract("T", scope=["src/**"],
                              test_command=self._canary_cmd(out))
        env = {"TASKPLANE_TASK": "t-leak", "TP_CANARY": "kept"}
        with mock.patch.dict(os.environ, env):
            errs = tp.dod_check(c, ws, _head(ws))
            # parent env untouched
            self.assertEqual(os.environ["TASKPLANE_TASK"], "t-leak")
        self.assertFalse(any(e.startswith("tests_pass") for e in errs), errs)
        probed = open(out, encoding="utf-8").read().split("|")
        self.assertEqual(probed[0], "<absent>")        # slot stripped
        self.assertEqual(probed[1], "kept")            # env NOT wiped

    def test_without_slot_the_child_env_is_unchanged(self):
        # the working path: no slot in the parent → nothing to strip
        ws = _repo(self.tmp)
        out = os.path.join(ws, "src", "envprobe.txt")
        c = tp.build_contract("T", scope=["src/**"],
                              test_command=self._canary_cmd(out))
        with mock.patch.dict(os.environ, {"TP_CANARY": "kept"}):
            os.environ.pop("TASKPLANE_TASK", None)
            errs = tp.dod_check(c, ws, _head(ws))
        self.assertFalse(any(e.startswith("tests_pass") for e in errs), errs)
        self.assertEqual(open(out, encoding="utf-8").read(), "<absent>|kept")
