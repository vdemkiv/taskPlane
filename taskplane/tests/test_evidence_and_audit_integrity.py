"""Two ways the record lied about itself (D-0008, D-0014).

D-0008 — A GATE NAMED `tests_pass`, SATISFIED WITH NO TEST RUN.
The suite cache lets a completed run stand in for execution over
byte-identical content. That is a real and valuable optimization — a
parallel wave pays for one suite, not one per task. Two things were wrong
with how it discharged the gate:

  * The record carried a `ts` that NOTHING read. The key binds everything
    taskplane controls — tree content, command, engine fingerprint, some
    env — but it cannot bind the interpreter minor version, the installed
    package set, or the OS libraries, and those drift. A green result from
    months ago still passed today's gate.
  * The citation existed only as a trace line. The human signing off read
    "tests pass"; nothing they looked at said nobody ran anything.

D-0014 — AN AUDIT TRACE THAT DESTROYED ITS OWN HISTORY AND DENIED IT.
Rotation moved `trace.jsonl` to a FIXED `trace.jsonl.1`, so the second
rotation silently overwrote the first archive — while the record it wrote
in the new file said "earlier events moved aside, not lost". The false
claim is the dangerous part: the only reader who would notice the gap is
the one auditing it, and they were being told there wasn't one.

Every assertion here was observed FAILING before it was kept.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tp  # noqa: E402


def _git_ws(root):
    open(os.path.join(root, "a.py"), "w", encoding="utf-8").write("x = 1\n")
    for args in (["init", "-q"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=root, capture_output=True)
    return root


class _Ws(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in
                     ("TASKPLANE_HOME", "TASKPLANE_SUITE_CACHE_MAX_AGE",
                      "TASKPLANE_NO_SUITE_CACHE")}
        self.home = tempfile.mkdtemp(prefix="tp-int-home-")
        os.environ["TASKPLANE_HOME"] = self.home
        os.environ.pop("TASKPLANE_SUITE_CACHE_MAX_AGE", None)
        os.environ.pop("TASKPLANE_NO_SUITE_CACHE", None)
        self.ws = _git_ws(tempfile.mkdtemp(prefix="tp-int-ws-"))

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)


class TestD0008ACitationIsBoundedInTime(_Ws):
    CMD = "true"

    def _store(self, ts_offset=0.0):
        tp.suite_cache_store(self.ws, self.CMD, {}, returncode=0,
                             tail="", duration_s=1.0)
        if ts_offset:
            key = tp._suite_cache_key(self.ws, self.CMD, {})
            path = tp._suite_cache_path(key)
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            rec["ts"] = time.time() - ts_offset
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f)

    def test_a_fresh_citation_is_still_used(self):
        """The optimization has to survive the fix, or a parallel wave pays
        for one suite run per task again."""
        self._store()
        self.assertIsNotNone(tp.suite_cache_lookup(self.ws, self.CMD, {}))

    def test_a_stale_citation_is_refused(self):
        self._store(ts_offset=tp.SUITE_CACHE_MAX_AGE_S + 60)
        self.assertIsNone(tp.suite_cache_lookup(self.ws, self.CMD, {}))

    def test_the_refusal_is_traced(self):
        """'Why did my suite run again?' must be answerable."""
        self._store(ts_offset=tp.SUITE_CACHE_MAX_AGE_S + 60)
        tp.suite_cache_lookup(self.ws, self.CMD, {})
        events = []
        for p in tp.trace_paths(self.ws):
            with open(p, encoding="utf-8") as f:
                events += [json.loads(l) for l in f if l.strip()]
        self.assertTrue(any(e["event"] == "suite_cache_stale" for e in events))

    def test_an_undatable_record_is_not_evidence(self):
        self._store()
        key = tp._suite_cache_key(self.ws, self.CMD, {})
        path = tp._suite_cache_path(key)
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        rec.pop("ts")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        self.assertIsNone(tp.suite_cache_lookup(self.ws, self.CMD, {}))

    def test_the_window_is_configurable_and_zero_means_never_cite(self):
        self._store()
        os.environ["TASKPLANE_SUITE_CACHE_MAX_AGE"] = "0"
        self.assertIsNone(tp.suite_cache_lookup(self.ws, self.CMD, {}))
        os.environ["TASKPLANE_SUITE_CACHE_MAX_AGE"] = "not-a-number"
        self.assertEqual(tp.suite_cache_max_age(),
                         float(tp.SUITE_CACHE_MAX_AGE_S),
                         "a garbage value must fall back to the default, "
                         "never to 'unbounded'")


class TestD0008ACitationIsDisclosed(_Ws):
    def _contract(self):
        return tp.build_contract("t", scope=["**"], test_command="true",
                                 plan_minted=True)

    def test_a_cited_pass_says_so(self):
        c = self._contract()
        c["coding"]["dod"]["require_clean_scope_diff"] = False
        tp.dod_check(c, self.ws, None)              # executes, populates cache
        notices: list = []
        errors = tp.dod_check(c, self.ws, None, notices=notices)
        self.assertEqual(errors, [])
        self.assertTrue(notices, "the second check cited and said nothing")
        self.assertIn("CITED, not executed", notices[0])
        self.assertIn("TASKPLANE_NO_SUITE_CACHE", notices[0])

    def test_an_executed_pass_produces_no_notice(self):
        """The complement — a notice on every pass would be noise nobody
        reads, which is how the trace line got ignored in the first place."""
        os.environ["TASKPLANE_NO_SUITE_CACHE"] = "1"
        c = self._contract()
        c["coding"]["dod"]["require_clean_scope_diff"] = False
        notices: list = []
        tp.dod_check(c, self.ws, None, notices=notices)
        self.assertEqual(notices, [])

    def test_the_parameter_is_optional_so_old_callers_are_unchanged(self):
        c = self._contract()
        c["coding"]["dod"]["require_clean_scope_diff"] = False
        self.assertEqual(tp.dod_check(c, self.ws, None), [])


class TestD0014RotationKeepsEveryGeneration(_Ws):
    def _fill(self, n=3):
        """Force `n` rotations by writing past the bound each time."""
        path = os.path.join(tp.tp_dir(self.ws), "trace.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        for i in range(n):
            tp.trace(self.ws, "marker", generation=i)
            with open(path, "a", encoding="utf-8") as f:
                f.write("x" * (tp._TRACE_MAX_BYTES + 1) + "\n")
        tp.trace(self.ws, "marker", generation=n)
        return path

    def test_every_generation_survives(self):
        path = self._fill(3)
        for n in (1, 2, 3):
            self.assertTrue(os.path.exists(f"{path}.{n}"),
                            f"archive {n} was destroyed by a later rotation")

    def test_no_marker_is_lost(self):
        """The claim the old record made and could not keep."""
        self._fill(3)
        seen = set()
        for p in tp.trace_paths(self.ws):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("event") == "marker":
                        seen.add(e["generation"])
        self.assertEqual(seen, {0, 1, 2, 3})

    def test_the_rotation_record_names_the_archive_it_made(self):
        self._fill(2)
        rotations = []
        for p in tp.trace_paths(self.ws):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("event") == "trace_rotated":
                        rotations.append(e["archived_to"])
        self.assertEqual(len(set(rotations)), len(rotations),
                         "two rotations naming the same archive means one "
                         "of them overwrote the other")
        for r in rotations:
            self.assertNotIn("\\\\", r, "archive pointers are '/'-shaped")

    def test_trace_paths_is_chronological_with_the_active_file_last(self):
        path = self._fill(2)
        paths = tp.trace_paths(self.ws)
        self.assertEqual(paths[-1], path)
        self.assertEqual(paths[:-1], [f"{path}.1", f"{path}.2"])

    def test_an_unrotated_workspace_returns_just_the_active_file(self):
        tp.trace(self.ws, "hello")
        self.assertEqual(
            tp.trace_paths(self.ws),
            [os.path.join(tp.tp_dir(self.ws), "trace.jsonl")])

    def test_reserving_an_archive_never_reuses_a_name(self):
        """The concurrency property: two rotations racing must not both
        win the same n. O_CREAT|O_EXCL is what makes that true."""
        path = os.path.join(tp.tp_dir(self.ws), "trace.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        claimed = {tp._reserve_trace_archive(path) for _ in range(5)}
        self.assertEqual(len(claimed), 5)
        self.assertNotIn(None, claimed)


if __name__ == "__main__":
    unittest.main()
