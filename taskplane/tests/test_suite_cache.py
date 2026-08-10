"""Suite-result cache (P1, R-0012) — the performance regression fix.

Phase 3 executed the DoD test command 161 times for ten tasks because it
ran once per CALLER instead of once per TREE STATE. These tests pin the
only property that makes citing a prior run legitimate: a hit requires the
same command to have already run to completion over byte-identical governed
content, under the same engine, under the same governing env — and every
other case must actually execute.

The sentinel technique below is the proof. The test command appends a line
to a file OUTSIDE the workspace, so the number of lines is exactly the
number of times the command really ran. No narration, no mocking of the
thing under test.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tp  # noqa: E402


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def _head(ws):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


class _CacheCase(unittest.TestCase):
    """Every case gets its own store, so cache state never leaks between
    tests and never touches the developer's real store."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        self.store = os.path.join(self.tmp, "store")
        self.sentinel = os.path.join(self.tmp, "ran.log")
        self._env = mock.patch.dict(
            os.environ, {"TASKPLANE_HOME": self.store}, clear=False)
        self._env.start()
        os.environ.pop("TASKPLANE_NO_SUITE_CACHE", None)
        self.addCleanup(self._env.stop)

    def cmd(self, exit_code=0):
        return f"echo ran >> {self.sentinel}; exit {exit_code}"

    def runs(self):
        try:
            with open(self.sentinel, encoding="utf-8") as f:
                return len([x for x in f if x.strip()])
        except OSError:
            return 0

    def contract(self, exit_code=0):
        return tp.build_contract("t1", scope=["src/**"],
                                 test_command=self.cmd(exit_code))

    def dod(self, exit_code=0):
        return tp.dod_check(self.contract(exit_code), self.ws, _head(self.ws))


class TestCacheHitsOnlyOnIdenticalContent(_CacheCase):
    def test_second_check_over_identical_content_cites_the_first(self):
        self.assertEqual(self.dod(), [])
        self.assertEqual(self.runs(), 1)
        self.assertEqual(self.dod(), [])
        self.assertEqual(self.runs(), 1, "identical tree must not re-execute")

    def test_a_tracked_edit_invalidates_and_re_executes(self):
        self.dod()
        self.assertEqual(self.runs(), 1)
        open(os.path.join(self.ws, "src", "a.py"), "a", encoding="utf-8").write("y = 2\n")
        self.dod()
        self.assertEqual(self.runs(), 2, "changed content must re-run")

    def test_an_untracked_file_invalidates_and_re_executes(self):
        self.dod()
        open(os.path.join(self.ws, "src", "new.py"), "w", encoding="utf-8").write("z = 3\n")
        self.dod()
        self.assertEqual(self.runs(), 2,
                         "an untracked file is part of the tree identity")

    def test_untracked_file_content_change_invalidates(self):
        open(os.path.join(self.ws, "src", "new.py"), "w", encoding="utf-8").write("z = 3\n")
        self.dod()
        open(os.path.join(self.ws, "src", "new.py"), "w", encoding="utf-8").write("z = 4\n")
        self.dod()
        self.assertEqual(self.runs(), 2,
                         "same path, different bytes, is a different tree")

    def test_a_new_commit_invalidates(self):
        self.dod()
        open(os.path.join(self.ws, "src", "b.py"), "w", encoding="utf-8").write("b = 1\n")
        _git(self.ws, "add", "-A")
        _git(self.ws, "commit", "-qm", "second")
        tp.dod_check(self.contract(), self.ws, _head(self.ws))
        self.assertEqual(self.runs(), 2)

    def test_a_different_command_does_not_share_a_result(self):
        self.dod()
        other = tp.build_contract("t1", scope=["src/**"],
                                  test_command=f"echo ran >> {self.sentinel}; "
                                               "exit 0  # different")
        tp.dod_check(other, self.ws, _head(self.ws))
        self.assertEqual(self.runs(), 2, "the command is part of the key")


class TestFailuresAreCachedHonestly(_CacheCase):
    def test_a_failure_is_reported_on_the_cited_run_too(self):
        first = self.dod(exit_code=1)
        self.assertTrue(any(e.startswith("tests_pass:") for e in first))
        second = self.dod(exit_code=1)
        self.assertTrue(any(e.startswith("tests_pass:") for e in second),
                        "a cited failure must still block the gate")
        self.assertEqual(self.runs(), 1)

    def test_the_cited_failure_names_how_to_force_a_real_run(self):
        self.dod(exit_code=1)
        second = self.dod(exit_code=1)
        joined = " ".join(second)
        self.assertIn("TASKPLANE_NO_SUITE_CACHE", joined)

    def test_a_failing_tree_that_is_fixed_re_executes(self):
        self.assertTrue(self.dod(exit_code=1))
        open(os.path.join(self.ws, "src", "a.py"), "a", encoding="utf-8").write("fixed = 1\n")
        self.assertEqual(self.dod(exit_code=0), [])
        self.assertEqual(self.runs(), 2)


class TestFailClosedPaths(_CacheCase):
    def test_the_kill_switch_forces_execution(self):
        self.dod()
        with mock.patch.dict(os.environ,
                             {"TASKPLANE_NO_SUITE_CACHE": "1"}, clear=False):
            self.dod()
        self.assertEqual(self.runs(), 2)

    def test_the_kill_switch_also_stops_recording(self):
        with mock.patch.dict(os.environ,
                             {"TASKPLANE_NO_SUITE_CACHE": "1"}, clear=False):
            self.dod()
            self.dod()
        self.assertEqual(self.runs(), 2)

    def test_a_non_git_workspace_is_uncacheable_and_always_runs(self):
        bare = os.path.join(self.tmp, "bare")
        os.makedirs(os.path.join(bare, "src"))
        open(os.path.join(bare, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
        self.assertIsNone(tp.tree_fingerprint(bare),
                          "no git means no honest content identity")
        c = tp.build_contract("t1", test_command=self.cmd())
        tp.dod_check(c, bare, None)
        tp.dod_check(c, bare, None)
        self.assertEqual(self.runs(), 2)

    def test_an_oversized_untracked_payload_is_uncacheable(self):
        big = os.path.join(self.ws, "big.bin")
        with open(big, "wb") as f:
            f.write(b"\0" * 4096)
        with mock.patch.object(tp, "SUITE_CACHE_MAX_UNTRACKED_BYTES", 1024):
            self.assertIsNone(tp.tree_fingerprint(self.ws))

    def test_a_corrupt_cache_entry_falls_back_to_running(self):
        self.dod()
        d = os.path.join(self.store, "suite-cache")
        for name in os.listdir(d):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write("{not json")
        self.dod()
        self.assertEqual(self.runs(), 2)

    def test_an_unreadable_store_costs_a_rerun_not_a_false_pass(self):
        with mock.patch.object(tp, "atomic_write_json",
                               side_effect=OSError("disk full")):
            self.assertEqual(self.dod(), [])
            self.assertEqual(self.dod(), [])
        self.assertEqual(self.runs(), 2,
                         "an unwritable cache must degrade to real runs")


class TestEvidenceIsBoundToEngineAndEnv(_CacheCase):
    def test_a_different_engine_fingerprint_does_not_share_a_result(self):
        self.dod()
        with mock.patch.object(tp, "engine_fingerprint",
                               return_value="deadbeef" * 8):
            self.dod()
        self.assertEqual(self.runs(), 2,
                         "evidence is bound to the engine that produced it")

    def test_an_unavailable_engine_fingerprint_is_uncacheable(self):
        with mock.patch.object(tp, "engine_fingerprint",
                               side_effect=RuntimeError("no surface")):
            self.dod()
            self.dod()
        self.assertEqual(self.runs(), 2)

    def test_a_different_governing_env_does_not_share_a_result(self):
        self.dod()
        with mock.patch.dict(os.environ,
                             {"TASKPLANE_AUDIT_EVERY": "3"}, clear=False):
            self.dod()
        self.assertEqual(self.runs(), 2,
                         "governing env is part of what the suite proves")

    def test_unrelated_env_does_not_invalidate(self):
        self.dod()
        with mock.patch.dict(os.environ,
                             {"SOME_UNRELATED_VAR": "x"}, clear=False):
            self.dod()
        self.assertEqual(self.runs(), 1)


class TestSharedAcrossWorktrees(_CacheCase):
    def test_two_checkouts_of_identical_content_share_one_run(self):
        """The wave case: worktrees with identical content must not each
        pay for the same suite. Same store, same content, one execution."""
        clone = os.path.join(self.tmp, "clone")
        subprocess.run(["git", "clone", "-q", self.ws, clone],
                       capture_output=True)
        _git(clone, "config", "user.email", "e@e")
        _git(clone, "config", "user.name", "t")
        self.assertEqual(tp.tree_fingerprint(self.ws),
                         tp.tree_fingerprint(clone),
                         "identical content is one identity")
        self.dod()
        tp.dod_check(self.contract(), clone, _head(clone))
        self.assertEqual(self.runs(), 1)


class TestTheHitIsAuditable(_CacheCase):
    def test_a_hit_is_traced_with_the_key_and_the_saving(self):
        self.dod()
        self.dod()
        path = os.path.join(tp.tp_dir(self.ws), "trace.jsonl")
        with open(path, encoding="utf-8") as f:
            events = [__import__("json").loads(x) for x in f if x.strip()]
        hits = [e for e in events if e.get("event") == "suite_cache_hit"]
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].get("key"))
        self.assertEqual(hits[0].get("returncode"), 0)
        self.assertIsNotNone(hits[0].get("seconds_saved"))

    def test_a_real_run_is_traced_with_its_cost(self):
        self.dod()
        path = os.path.join(tp.tp_dir(self.ws), "trace.jsonl")
        with open(path, encoding="utf-8") as f:
            events = [__import__("json").loads(x) for x in f if x.strip()]
        runs = [e for e in events if e.get("event") == "suite_run"]
        self.assertEqual(len(runs), 1)
        self.assertIn("seconds", runs[0])


if __name__ == "__main__":
    unittest.main()
