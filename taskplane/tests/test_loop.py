import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def git_ws(tmp, tasks):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "plan"))
    os.makedirs(os.path.join(ws, "src", "todo"))
    open(os.path.join(ws, "src", "todo", "a.py"), "w").write("x=1\n")
    subprocess.run(["git", "init", "-q"], cwd=ws)
    subprocess.run(["git", "config", "user.email", "e@e"], cwd=ws)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws)
    json.dump({"tasks": tasks}, open(os.path.join(ws, "plan", "tasks.json"), "w"))
    return ws


TASK = {"id": "t1", "scope": ["src/todo/**"], "tests": "true",
        "criteria": ["complete() marks done"]}


class TestLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_free_text_goal_starts_at_pm(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "add complete()")
        self.assertEqual(loop.load(ws)["step"], "pm")

    def test_existing_spec_skips_pm(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")
        self.assertEqual(loop.load(ws)["step"], "plan")

    def test_next_activates_contract_gate_clears(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")
        act = loop.next_action(ws)
        self.assertEqual(act["role"], "tp-planner")
        self.assertTrue(act["contract"]["read_only"])
        self.assertIsNotNone(tp.load_active(ws))          # activated
        loop.gate(ws, "pass")
        self.assertIsNone(tp.load_active(ws))             # cleared

    def test_plan_gate_fails_closed_on_phantom_plan(self):
        """A planner CLAIMING a plan is nothing: if plan/tasks.json is
        missing or empty, the plan gate must refuse to advance — the exact
        hallucinated-completion failure the ungoverned control run showed."""
        ws = git_ws(self.tmp, [TASK])
        os.remove(os.path.join(ws, "plan", "tasks.json"))   # phantom plan
        loop.init(ws, "g", spec_path="specs/spec.md")       # → plan
        loop.next_action(ws)
        r = loop.gate(ws, "pass")
        self.assertIn("error", r)
        self.assertIn("plan/tasks.json", r["error"])
        self.assertEqual(loop.load(ws)["step"], "plan")     # did NOT advance
        # writing a real plan unblocks the same gate
        json.dump({"tasks": [TASK]},
                  open(os.path.join(ws, "plan", "tasks.json"), "w"))
        loop.next_action(ws)
        r = loop.gate(ws, "pass")
        self.assertNotIn("error", r)
        self.assertEqual(loop.load(ws)["step"], "plan_approval")

    def test_plan_checkpoint_then_execute(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="specs/spec.md")   # → plan
        loop.next_action(ws); loop.gate(ws, "pass")     # plan → plan_approval
        self.assertEqual(loop.load(ws)["step"], "plan_approval")
        act = loop.next_action(ws)
        self.assertTrue(act["paused"])                   # human gate
        loop.approve(ws)
        self.assertEqual(loop.load(ws)["step"], "execute")
        self.assertEqual(loop.load(ws)["tasks"][0]["id"], "t1")

    def test_happy_path_to_signoff(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])  # no plan gate
        loop.next_action(ws); loop.gate(ws, "pass")            # plan → execute
        self.assertEqual(loop.load(ws)["step"], "execute")
        loop.next_action(ws); loop.gate(ws, "pass")            # execute → evaluate
        loop.next_action(ws); loop.gate(ws, "pass")            # evaluate → em
        self.assertEqual(loop.load(ws)["step"], "em")
        loop.next_action(ws); loop.gate(ws, "pass")            # em → signoff
        self.assertEqual(loop.load(ws)["step"], "signoff")
        loop.approve(ws)                                       # → done
        self.assertEqual(loop.load(ws)["step"], "done")

    def test_fail_autofix_then_escalate(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"], max_fix_cycles=2)
        loop.next_action(ws); loop.gate(ws, "pass")   # plan → execute
        loop.next_action(ws); loop.gate(ws, "pass")   # execute → evaluate
        loop.next_action(ws); loop.gate(ws, "fail")   # evaluate FAIL → fix (1)
        self.assertEqual(loop.load(ws)["step"], "fix")
        loop.next_action(ws); loop.gate(ws, "pass")   # fix → evaluate
        loop.next_action(ws); loop.gate(ws, "fail")   # FAIL → fix (2)
        loop.next_action(ws); loop.gate(ws, "pass")   # fix → evaluate
        loop.next_action(ws); loop.gate(ws, "fail")   # cycle 3 > max → escalated
        self.assertEqual(loop.load(ws)["step"], "escalated")
        loop.resolve(ws, "skip")                       # last task → em
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_multi_task_progression(self):
        t2 = dict(TASK, id="t2")
        ws = git_ws(self.tmp, [TASK, t2])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])
        loop.next_action(ws); loop.gate(ws, "pass")   # plan → execute t1
        loop.next_action(ws); loop.gate(ws, "pass")   # execute → evaluate
        loop.next_action(ws); loop.gate(ws, "pass")   # evaluate t1 pass → execute t2
        self.assertEqual(loop.load(ws)["step"], "execute")
        self.assertEqual(loop.load(ws)["current_task"], 1)
        loop.next_action(ws); loop.gate(ws, "pass")
        loop.next_action(ws); loop.gate(ws, "pass")   # evaluate t2 pass → em
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_escalate_retry_resets_cycles(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"], max_fix_cycles=1)
        loop.next_action(ws); loop.gate(ws, "pass")   # → execute
        loop.next_action(ws); loop.gate(ws, "pass")   # → evaluate
        loop.next_action(ws); loop.gate(ws, "fail")   # → fix (1)
        loop.next_action(ws); loop.gate(ws, "pass")   # → evaluate
        loop.next_action(ws); loop.gate(ws, "fail")   # cycle2 > max1 → escalated
        loop.resolve(ws, "retry")
        self.assertEqual(loop.load(ws)["step"], "fix")
        self.assertEqual(loop.load(ws)["tasks"][0]["fix_cycles"], 0)


if __name__ == "__main__":
    unittest.main()


class TestLoopLensAndRequirementWiring(unittest.TestCase):
    """Step 1 wiring: prime at EXECUTE, route at EVALUATE/EM, refinement
    gate at plan approval, tasks anchored to R-ids."""

    def _ws(self, scope="src/auth/**", high_cost=False, with_req=True):
        import requirements as reqs
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "plan"))
        os.makedirs(os.path.join(ws, "src", "auth"))
        with open(os.path.join(ws, "src", "auth", "a.py"), "w") as f:
            f.write("x=1\n")
        for c in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", *c], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        task = {"id": "t1", "scope": [scope], "tests": "true"}
        if high_cost:
            task["high_cost"] = True
        if with_req:
            r = reqs.record_requirement(
                ws, "login", functional=["user logs in"],
                acceptance=["valid creds -> session"],
                context_files=[scope])
            task["req"] = r["id"]
        with open(os.path.join(ws, "plan", "tasks.json"), "w") as f:
            json.dump({"tasks": [task]}, f)
        loop.init(ws, "auth work", spec_path="s", checkpoints=["plan"])
        loop.next_action(ws)
        loop.gate(ws, "pass")          # plan -> plan_approval
        return ws

    def test_execute_is_primed_and_anchored(self):
        ws = self._ws()
        out = loop.approve(ws)
        self.assertIn("refinement", out)          # forecast shown at the gate
        act = loop.next_action(ws)                # execute step
        self.assertEqual(act["step"], "execute")
        primed = {x["id"] for x in act["lenses"]}
        self.assertIn("security", primed)         # auth scope -> security
        self.assertEqual(act["requirement"]["id"], "R-0001")
        self.assertTrue(act["requirement"]["acceptance"])

    def test_evaluate_routes_on_real_diff(self):
        ws = self._ws()
        loop.approve(ws)
        loop.next_action(ws)
        # the "build": touch an auth file, uncommitted
        with open(os.path.join(ws, "src", "auth", "b.py"), "w") as f:
            f.write("y=2\n")
        loop.gate(ws, "pass")                     # execute -> evaluate
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "evaluate")
        sec = next(x for x in act["lenses"] if x["id"] == "security")
        self.assertEqual(sec["mode"], "subagent")  # deep on auth diff

    def test_high_cost_unrefined_blocks_until_force(self):
        import requirements as reqs
        ws = self._ws(with_req=False, high_cost=True)
        # anchor to a thin (unrefined) requirement
        r = reqs.record_requirement(ws, "vague", context_files=["src/auth/**"])
        st = loop.load(ws)
        st["tasks"][0]["req"] = r["id"]
        st["tasks"][0]["high_cost"] = True
        loop.save(ws, st)
        out = loop.approve(ws)
        self.assertIn("error", out)                # hard-blocked
        out2 = loop.approve(ws, force=True)
        self.assertEqual(out2["step"], "execute")  # human override

    def test_no_requirement_still_flows(self):
        ws = self._ws(with_req=False)
        out = loop.approve(ws)
        self.assertEqual(out["step"], "execute")
        act = loop.next_action(ws)
        self.assertIsNone(act["requirement"])      # unanchored is allowed


class TestParallelExecution(unittest.TestCase):
    """Waves: deps + scope-disjointness pick the wave; every worker gets its
    OWN contract in its OWN worktree — the harness is per agent."""

    def _ws(self):
        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "plan"))
        for d in ("src/a", "src/b", "src/c"):
            os.makedirs(os.path.join(ws, d))
            with open(os.path.join(ws, d, "m.py"), "w") as f:
                f.write("x=1\n")
        subprocess.run(["git", "init", "-q"], cwd=ws)
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        tasks = [
            {"id": "t1", "scope": ["src/a/**"], "tests": "true"},
            {"id": "t2", "scope": ["src/b/**"], "tests": "true"},
            {"id": "t3", "scope": ["src/a/**", "src/c/**"], "tests": "true"},
            {"id": "t4", "scope": ["src/c/**"], "tests": "true",
             "deps": ["t1"]},
        ]
        with open(os.path.join(ws, "plan", "tasks.json"), "w") as f:
            json.dump({"tasks": tasks}, f)
        loop.init(ws, "parallel goal", spec_path="s", checkpoints=["plan"],
                  parallel=True)
        loop.next_action(ws); loop.gate(ws, "pass")   # plan → approval
        loop.approve(ws)                               # → execute
        return ws

    def test_wave_respects_deps_and_scope_disjointness(self):
        ws = self._ws()
        w = loop.wave(ws)
        ids = [e["task"]["id"] for e in w["wave"]]
        held = {h["task"]: h["reason"] for h in w["held"]}
        self.assertEqual(ids, ["t1", "t2"])       # disjoint, dep-free
        self.assertIn("t3", held)                 # overlaps t1 (src/a)
        self.assertIn("t4", held)                 # dep t1 not passed yet
        self.assertTrue(all(e["lenses"] is not None for e in w["wave"]))

    def test_claim_activates_contract_in_worker_worktree(self):
        import taskplane_lite as tpl
        ws = self._ws()
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws)
        out = loop.claim(ws, "t1", agent_ws)
        self.assertEqual(out["claimed"], "t1")
        # the WORKER's workspace is governed…
        c = tpl.load_active(agent_ws)
        self.assertEqual(c["coding"]["scope_paths"], ["src/a/**"])
        # …and the hook blocks it outside its own task scope:
        allow, _ = tpl.screen_tool(
            c, "Write", {"file_path": os.path.join(agent_ws, "src/b/x.py")},
            agent_ws)
        self.assertFalse(allow)
        allow2, _ = tpl.screen_tool(
            c, "Write", {"file_path": os.path.join(agent_ws, "src/a/x.py")},
            agent_ws)
        self.assertTrue(allow2)
        # the MAIN workspace is not governed by this worker's contract
        self.assertIsNone(tpl.load_active(ws))

    def test_parallel_gates_flow_to_evaluate_then_next_wave(self):
        ws = self._ws()
        for tid in ("t1", "t2"):
            agent_ws = os.path.join(ws, ".tp-work", tid)
            subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                            f"tp/{tid}"], cwd=ws)
            loop.claim(ws, tid, agent_ws)
        out = loop.gate(ws, "pass", task_id="t1")
        self.assertEqual(out["still_running"], ["t2"])
        loop.gate(ws, "pass", task_id="t2")
        # both built → next surfaces evaluate for the first built task
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "evaluate")
        self.assertEqual(act["task"]["id"], "t1")
        loop.gate(ws, "pass")                         # t1 passed
        act2 = loop.next_action(ws)                   # evaluate t2
        self.assertEqual(act2["task"]["id"], "t2")
        loop.gate(ws, "pass")                         # t2 passed
        # t1 passed unlocks t4, but t3/t4 overlap on src/c → serialized:
        # t3 (first in plan order) dispatches, t4 holds for the next wave.
        w = loop.wave(ws)
        self.assertEqual({e["task"]["id"] for e in w["wave"]}, {"t3"})
        held = {h["task"]: h["reason"] for h in w["held"]}
        self.assertIn("t4", held)
        self.assertIn("overlaps", held["t4"])

    def test_all_passed_reaches_em(self):
        ws = self._ws()
        st = loop.load(ws)
        for t in st["tasks"]:
            t["status"] = "passed"
        st["tasks"][0]["status"] = "built"     # last one still to evaluate
        loop.save(ws, st)
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "evaluate")
        out = loop.gate(ws, "pass")
        self.assertEqual(out["step"], "em")

    def test_gate_requires_task_id_in_parallel_execute(self):
        ws = self._ws()
        self.assertIn("error", loop.gate(ws, "pass"))


class TestParallelCommitDiscipline(unittest.TestCase):
    def test_gate_refuses_uncommitted_worktree_then_accepts(self):
        ws = TestParallelExecution._ws(TestParallelExecution())
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws)
        loop.claim(ws, "t1", agent_ws)
        with open(os.path.join(agent_ws, "src", "a", "new.py"), "w") as f:
            f.write("y=2\n")
        out = loop.gate(ws, "pass", task_id="t1")
        self.assertIn("error", out)                    # fail closed
        self.assertIn("uncommitted", out["error"])
        subprocess.run(["git", "add", "-A"], cwd=agent_ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "t1"], cwd=agent_ws)
        out2 = loop.gate(ws, "pass", task_id="t1")
        self.assertTrue(out2.get("built"))             # now accepted

    def test_em_survives_removed_worktrees(self):
        ws = TestParallelExecution._ws(TestParallelExecution())
        st = loop.load(ws)
        for t in st["tasks"]:
            t["status"] = "passed"
            t["workspace"] = os.path.join(ws, ".tp-work", "gone")  # removed
        st["step"] = "em"
        loop.save(ws, st)
        act = loop.next_action(ws)                     # must not crash
        self.assertEqual(act["step"], "em")
