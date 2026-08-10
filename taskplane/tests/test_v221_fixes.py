"""v2.2.1 — fixes from the full 26-lens self-review of v2.2.0.

Pins the five HIGH findings:
  H1 submit() rejects a mismatched --task outside parallel EXECUTE;
  H2 gate transitions apply under the state lock (no clobbered workers);
  H3 the design graph baseline re-captures after a legitimate rescan;
  H4 the pm gate is fail-closed (an authored requirement must exist);
  H5 post-approval design staleness is enforced at every gate (tested).
Plus submit/gate negative paths (M9) and fingerprint branches (M11).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def _git(ws, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    *args], cwd=ws, check=True)


def _repo():
    ws = tempfile.mkdtemp(prefix="tp-v221-")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w").write("x = 1\n")
    os.makedirs(os.path.join(ws, "specs"))
    open(os.path.join(ws, "specs", "spec.md"), "w").write("# spec\n")
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


class _Env(unittest.TestCase):
    def setUp(self):
        # t9 (R-0011 E2): save-and-restore, not an unconditional pop — the
        # old tearDown deleted an exported TASKPLANE_STORE/HOME for every
        # LATER test module in the process.
        self._env0 = {k: os.environ.get(k)
                      for k in ("TASKPLANE_HOME", "TASKPLANE_STORE")}
        os.environ["TASKPLANE_HOME"] = tempfile.mkdtemp(prefix="tp-h-")
        os.environ.pop("TASKPLANE_STORE", None)

    def tearDown(self):
        for k, v in self._env0.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _to_step(ws, step, tasks=None):
    state = loop.load(ws)
    state.update({"step": step, "current_task": 0,
                  "tasks": tasks if tasks is not None else [
                      {"id": "t1", "scope": ["src/**"], "tests": "true",
                       "criteria": ["works"], "status": "pending",
                       "fix_cycles": 0}]})
    loop.save(ws, state)
    return state


class TestH1SubmitTaskAttribution(_Env):
    def test_mismatched_task_rejected_outside_parallel_execute(self):
        ws = _repo()
        loop.init(ws, "g")
        _to_step(ws, "evaluate")
        out = loop.submit(ws, "pass", task_id="t2")   # not the current task
        self.assertIn("error", out)
        self.assertIn("does not match the current task", out["error"])

    def test_current_task_id_still_accepted(self):
        ws = _repo()
        loop.init(ws, "g")
        _to_step(ws, "evaluate")
        out = loop.submit(ws, "pass", task_id="t1")
        self.assertNotIn("error", out)


class TestH2LockedGateTransition(_Env):
    def test_gate_preserves_concurrent_worker_update(self):
        ws = _repo()
        loop.init(ws, "g", parallel=True)
        _to_step(ws, "execute", tasks=[
            {"id": "t1", "scope": ["src/**"], "tests": "true",
             "criteria": ["c"], "status": "built", "fix_cycles": 0},
            {"id": "t2", "scope": ["src/**"], "tests": "true",
             "criteria": ["c"], "status": "running", "fix_cycles": 0}])
        # Simulate a wave worker landing DURING gate validation: hook the
        # staleness check (which runs mid-gate) to update t2 under the lock.
        orig = loop._evaluation_errors

        def mid_gate(ws_, st_, task_):
            with loop.mutate(ws_) as st:
                t2 = next(t for t in st["tasks"] if t["id"] == "t2")
                t2["status"] = "built"
            return []

        loop._evaluation_errors = mid_gate
        try:
            state = loop.load(ws)
            state["submission_required"] = False
            state["step"] = "evaluate"
            loop.save(ws, state)
            out = loop.gate(ws, "pass")
        finally:
            loop._evaluation_errors = orig
        self.assertNotIn("error", out)
        # H2: the concurrent update to t2 must survive the gate's save
        t2 = next(t for t in loop.load(ws)["tasks"] if t["id"] == "t2")
        self.assertEqual(t2["status"], "built")

    def test_gate_refuses_when_step_moved_underneath(self):
        ws = _repo()
        loop.init(ws, "g")
        _to_step(ws, "fix")
        state = loop.load(ws)
        state["submission_required"] = False
        loop.save(ws, state)
        orig = loop._task_dod_errors

        def race(ws_, st_, task_, snap_):
            with loop.mutate(ws_) as st:
                st["step"] = "evaluate"          # someone else advanced
            return []

        loop._task_dod_errors = race
        try:
            out = loop.gate(ws, "pass")
        finally:
            loop._task_dod_errors = orig
        self.assertIn("error", out)
        self.assertIn("while this gate was validating", out["error"])


class TestH3DesignRebaseline(_Env):
    def test_design_baseline_recaptures_after_rescan(self):
        ws = _repo()
        loop.init(ws, "g", design=True) if "design" in \
            loop.init.__code__.co_varnames else None
        state = loop.load(ws) or {}
        if not state:
            loop.init(ws, "g")
            state = loop.load(ws)
        state.update({"step": "design", "design_required": True,
                      "design_graph_fingerprint": "stale-fp"})
        loop.save(ws, state)
        depgraph.scan(ws)                        # legitimate rescan
        loop.next_action(ws)                     # may block on design DoR
        fp = loop.load(ws).get("design_graph_fingerprint")
        self.assertNotEqual(fp, "stale-fp")      # re-baselined, not stuck
        current = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
        self.assertEqual(fp, current)


class TestH4PmGateFailClosed(_Env):
    def test_pm_gate_blocks_without_spec(self):
        ws = _repo()
        os.remove(os.path.join(ws, "specs", "spec.md"))
        loop.init(ws, "g")
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("no requirement was authored", out["error"])
        self.assertEqual(loop.load(ws)["step"], "pm")

    def test_pm_gate_advances_with_spec(self):
        ws = _repo()
        loop.init(ws, "g")
        out = loop.gate(ws, "pass")
        self.assertNotIn("error", out)
        self.assertEqual(loop.load(ws)["step"], "plan")

    def test_pm_gate_advances_with_requirement_id(self):
        ws = _repo()
        os.remove(os.path.join(ws, "specs", "spec.md"))
        loop.init(ws, "g", requirement_id="R-0001")
        out = loop.gate(ws, "pass")
        self.assertNotIn("error", out)

    def test_pm_gate_fail_stays_governed(self):
        ws = _repo()
        loop.init(ws, "g")
        out = loop.gate(ws, "fail")
        self.assertIn("error", out)
        self.assertEqual(loop.load(ws)["step"], "pm")


class TestH5DesignStalenessGuard(_Env):
    def _approved_design(self):
        ws = _repo()
        loop.init(ws, "g")
        state = loop.load(ws)
        state.update({"design_required": True, "design_approved": True,
                      "design_fingerprint": "sealed"})
        os.makedirs(os.path.join(ws, "design"))
        open(os.path.join(ws, "design", "contract.json"), "w").write(
            json.dumps({"schema": "taskplane.design/v1"}))
        open(os.path.join(ws, "design", "design.md"), "w").write("# d\n")
        loop.save(ws, state)
        return ws

    def test_tampered_contract_reported_stale(self):
        ws = self._approved_design()
        errs = loop._design_current_errors(ws, loop.load(ws))
        # fingerprint 'sealed' cannot match the real artifacts -> stale
        self.assertTrue(errs)
        joined = " ".join(errs).lower()
        self.assertIn("design", joined)


class TestM9SubmitGateNegativePaths(_Env):
    def test_submit_rejected_at_non_worker_step(self):
        ws = _repo()
        loop.init(ws, "g")                        # step pm
        out = loop.submit(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("not a worker submission step", out["error"])

    def test_submit_bad_outcome_rejected(self):
        ws = _repo()
        loop.init(ws, "g")
        _to_step(ws, "evaluate")
        out = loop.submit(ws, "maybe")
        self.assertIn("error", out)

    def test_gate_outcome_mismatch_rejected(self):
        ws = _repo()
        loop.init(ws, "g")
        _to_step(ws, "evaluate")
        loop.submit(ws, "fail", note="broken")
        out = loop.gate(ws, "pass")               # orchestrator disagrees
        self.assertIn("error", out)
        self.assertIn("does not match the worker submission", out["error"])

    def test_parallel_submit_without_task_rejected(self):
        ws = _repo()
        loop.init(ws, "g", parallel=True)
        _to_step(ws, "execute")
        out = loop.submit(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("--task", out["error"])


class TestM11FingerprintBranches(_Env):
    def test_fingerprint_changes_per_branch(self):
        ws = _repo()
        snap = tp.git_head(ws)
        base = tp.workspace_fingerprint(ws, snap)
        # untracked file changes the digest
        open(os.path.join(ws, "src", "new.py"), "w").write("n = 1\n")
        with_untracked = tp.workspace_fingerprint(ws, snap)
        self.assertNotEqual(base, with_untracked)
        # deleting a tracked file changes it again
        os.remove(os.path.join(ws, "src", "a.py"))
        with_deleted = tp.workspace_fingerprint(ws, snap)
        self.assertNotEqual(with_untracked, with_deleted)
        # extra evidence paths fold in
        os.makedirs(os.path.join(ws, ".eval"), exist_ok=True)
        open(os.path.join(ws, ".eval", "verdict.json"), "w").write("{}")
        with_extra = tp.workspace_fingerprint(
            ws, snap, extra_paths=[".eval/verdict.json"])
        self.assertNotEqual(with_deleted, with_extra)

    def test_fingerprint_stable_when_unchanged(self):
        ws = _repo()
        snap = tp.git_head(ws)
        # L13: with NO explicit baseline it falls back to HEAD (never a
        # constant), and refuses outside a committed repo
        self.assertEqual(tp.workspace_fingerprint(ws, None),
                         tp.workspace_fingerprint(ws, snap))
        bare = tempfile.mkdtemp(prefix="tp-bare-")
        with self.assertRaises(ValueError):
            tp.workspace_fingerprint(bare, None)
        self.assertEqual(tp.workspace_fingerprint(ws, snap),
                         tp.workspace_fingerprint(ws, snap))


if __name__ == "__main__":
    unittest.main()


class TestM10UserSummary(_Env):
    def test_not_started(self):
        ws = _repo()
        out = loop.user_summary(ws, host="claude")
        self.assertEqual(out["state"], "not_started")
        self.assertFalse(out["action_required"])
        self.assertTrue(out["headline"])

    def test_mid_loop_reports_progress(self):
        ws = _repo()
        loop.init(ws, "ship exports")
        _to_step(ws, "execute")
        out = loop.user_summary(ws, host="claude")
        self.assertIn("headline", out)
        self.assertIn("goal", out)
        self.assertEqual(out.get("goal"), "ship exports")

    def test_human_gate_requires_action(self):
        ws = _repo()
        loop.init(ws, "g")
        state = loop.load(ws)
        state["step"] = "plan_approval"
        loop.save(ws, state)
        out = loop.user_summary(ws, host="claude")
        self.assertTrue(out["action_required"])

    def test_host_injection_beats_env(self):
        ws = _repo()
        os.environ["CODEX_HOME"] = "/tmp/x"
        try:
            out = loop.user_summary(ws, host="claude")
        finally:
            os.environ.pop("CODEX_HOME", None)
        self.assertNotIn("codex", json.dumps(out).lower())


class TestL4PolicyEdgeValues(_Env):
    def test_normalize_policy_coerces_garbage(self):
        p = depgraph.normalize_policy({"local_depth": "nope",
                                       "contract_depth": -3,
                                       "boundary_mode": "sideways"})
        self.assertEqual(p["local_depth"], 3)       # default restored
        self.assertEqual(p["contract_depth"], 0)    # clamped to minimum
        self.assertEqual(p["boundary_mode"], "contract-only")

    def test_impact_policy_output_always_normalized(self):
        p = depgraph.impact_policy({"type": "distributed",
                                    "impact_policy": {"local_depth": "x"}})
        self.assertIsInstance(p["local_depth"], int)
        self.assertIn(p["boundary_mode"], ("contract-only", "stop", "expand"))
