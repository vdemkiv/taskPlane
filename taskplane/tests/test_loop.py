import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import lens  # noqa: E402
import depgraph  # noqa: E402


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


def submit_gate(ws, outcome="pass", task_id=None):
    submitted = loop.submit(ws, outcome, task_id=task_id)
    if "error" in submitted:
        return submitted
    return loop.gate(ws, outcome, task_id=task_id)


def write_verdict(ws):
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    act_ws = task.get("workspace") or ws
    routed = lens.route_git_diff(
        act_ws, base=state.get("baseline") or "HEAD",
        task_type=task.get("type"), breadth="routed")
    criteria = loop._criteria_for(ws, state, task)
    os.makedirs(os.path.join(act_ws, ".eval"), exist_ok=True)
    graph_dod = loop._task_graph_dod(ws, state, task)
    impact = graph_dod.get("impact") or {}
    direct = sorted({e.get("module")
                     for e in (impact.get("impacted") or {}).get(1, [])
                     if e.get("module") and
                     not str(e.get("module")).startswith("req:")})
    prod = depgraph.product_impact(
        ws, graph_dod.get("realized_modules") or [])
    own = task.get("req") or state.get("requirement_id")
    own = depgraph._req_node(own) if own else None
    affected = [r for r in prod.get("affected_requirements") or []
                if r != own]
    contracts = [c.get("id") if isinstance(c, dict) else c
                 for c in (task.get("contracts") or [])]
    with open(os.path.join(act_ws, ".eval", "verdict.json"), "w") as f:
        json.dump({"task": task["id"], "verdict": "pass",
                   "criteria": [{"criterion": c, "status": "met",
                                  "evidence": "verified by test"}
                                for c in criteria],
                   "lenses": [{"lens": x["id"], "verdict": "pass",
                               "blockers": 0} for x in routed["lenses"]],
                   "graph": {
                       "dispositions": [
                           {"node": node, "status": "tested",
                            "evidence": "covered by declared task tests"}
                           for node in direct],
                       "requirements_checked": affected,
                       "contracts_checked": contracts,
                   },
                   "failures": []}, f)


def pass_eval(ws):
    write_verdict(ws)
    return submit_gate(ws, "pass")


def pass_em(ws):
    coverage = {x["id"]: "sweep" for x in lens.load_catalog()["lenses"]}
    os.makedirs(os.path.join(ws, ".em-review"), exist_ok=True)
    with open(os.path.join(ws, ".em-review", "report.md"), "w") as f:
        f.write("# Engineering review\n\nAll required evidence passed.\n")
    state = loop.load(ws)
    changed = [f for f in loop._diff_files(
        ws, state.get("baseline") or "HEAD")
        if not f.startswith(lens.LOOP_OWNED)]
    impact = depgraph.impact(ws, changed)
    with open(os.path.join(ws, ".em-review", "findings.json"), "w") as f:
        json.dump({"meta": {"lens_coverage": coverage, "impact": impact,
                            "tests": ["true"],
                            "gate": {"verdict": "recommend-pass"}},
                   "findings": []}, f)
    return submit_gate(ws, "pass")


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
        loop.next_action(ws); submit_gate(ws, "pass")          # execute → evaluate
        loop.next_action(ws); pass_eval(ws)                     # evaluate → em
        self.assertEqual(loop.load(ws)["step"], "em")
        loop.next_action(ws); pass_em(ws)                       # em → signoff
        self.assertEqual(loop.load(ws)["step"], "signoff")
        loop.approve(ws)                                       # → done
        self.assertEqual(loop.load(ws)["step"], "done")

    def test_fail_autofix_then_escalate(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"], max_fix_cycles=2)
        loop.next_action(ws); loop.gate(ws, "pass")   # plan → execute
        loop.next_action(ws); submit_gate(ws, "pass") # execute → evaluate
        loop.next_action(ws); submit_gate(ws, "fail") # evaluate FAIL → fix (1)
        self.assertEqual(loop.load(ws)["step"], "fix")
        loop.next_action(ws); submit_gate(ws, "pass") # fix → evaluate
        loop.next_action(ws); submit_gate(ws, "fail") # FAIL → fix (2)
        loop.next_action(ws); submit_gate(ws, "pass") # fix → evaluate
        loop.next_action(ws); submit_gate(ws, "fail") # cycle 3 > max → escalated
        self.assertEqual(loop.load(ws)["step"], "escalated")
        loop.resolve(ws, "skip")                       # last task → em
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_multi_task_progression(self):
        t2 = dict(TASK, id="t2")
        ws = git_ws(self.tmp, [TASK, t2])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])
        loop.next_action(ws); loop.gate(ws, "pass")   # plan → execute t1
        loop.next_action(ws); submit_gate(ws, "pass") # execute → evaluate
        loop.next_action(ws); pass_eval(ws)            # evaluate t1 pass → execute t2
        self.assertEqual(loop.load(ws)["step"], "execute")
        self.assertEqual(loop.load(ws)["current_task"], 1)
        loop.next_action(ws); submit_gate(ws, "pass")
        loop.next_action(ws); pass_eval(ws)            # evaluate t2 pass → em
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_escalate_retry_resets_cycles(self):
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"], max_fix_cycles=1)
        loop.next_action(ws); loop.gate(ws, "pass")   # → execute
        loop.next_action(ws); submit_gate(ws, "pass") # → evaluate
        loop.next_action(ws); submit_gate(ws, "fail") # → fix (1)
        loop.next_action(ws); submit_gate(ws, "pass") # → evaluate
        loop.next_action(ws); submit_gate(ws, "fail") # cycle2 > max1 → escalated
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
        """R-0006 row 1: EVALUATE routes the real diff with stage='build'
        (route v2) — not the legacy stage-less route it pinned pre-design."""
        ws = self._ws()
        loop.approve(ws)
        loop.next_action(ws)
        # the "build": touch an auth file, uncommitted
        with open(os.path.join(ws, "src", "auth", "b.py"), "w") as f:
            f.write("y=2\n")
        submit_gate(ws, "pass")                   # execute -> evaluate
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "evaluate")
        # v2 build-stage signature: full-catalog coverage honesty (every
        # lens appears, the narrowed-away ones as mode "none") — the
        # legacy routed path returned only the summoned subset.
        catalog_ids = {l["id"] for l in lens.load_catalog()["lenses"]}
        self.assertEqual({x["id"] for x in act["lenses"]}, catalog_ids)
        self.assertTrue([x for x in act["lenses"] if x["mode"] == "none"])
        # security is floored on an auth diff: routed, never n/a...
        sec = next(x for x in act["lenses"] if x["id"] == "security")
        self.assertNotEqual(sec["mode"], "none")
        self.assertIn(sec["tier"], ("light", "deep"))
        # ...and carries the v2 engine keys the legacy path never emitted
        self.assertIn("verdict", sec)
        self.assertIn("score", sec)
        # the brief IS route v2 on this diff: same mode as the direct
        # build-stage derivation the validator single-sources
        state = loop.load(ws)
        direct = lens.route_git_diff(
            ws, base=state.get("baseline") or "HEAD",
            task_type=None, stage=loop.EVALUATE_ROUTE_STAGE,
            breadth="routed")
        dsec = next(x for x in direct["lenses"] if x["id"] == "security")
        self.assertEqual(sec["mode"], dsec["mode"])
        self.assertEqual(
            {x["id"] for x in act["lenses"] if x["mode"] != "none"},
            {x["id"] for x in direct["lenses"] if x["mode"] != "none"})

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
        out = submit_gate(ws, "pass", task_id="t1")
        self.assertEqual(out["still_running"], ["t2"])
        submit_gate(ws, "pass", task_id="t2")
        # both built → next surfaces evaluate for the first built task
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "evaluate")
        self.assertEqual(act["task"]["id"], "t1")
        pass_eval(ws)                                  # t1 passed
        act2 = loop.next_action(ws)                   # evaluate t2
        self.assertEqual(act2["task"]["id"], "t2")
        pass_eval(ws)                                  # t2 passed
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
        out = pass_eval(ws)
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
        loop.submit(ws, "pass", task_id="t1")
        out = loop.gate(ws, "pass", task_id="t1")
        self.assertIn("error", out)                    # fail closed
        self.assertIn("uncommitted", out["error"])
        subprocess.run(["git", "add", "-A"], cwd=agent_ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "t1"], cwd=agent_ws)
        loop.submit(ws, "pass", task_id="t1")
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


class TestSerialClaimRefusal(unittest.TestCase):
    """A1 (R-0007): `claim` fails closed on a serial loop — a direct claim
    used to form a wave whose submits deadlock (decision 0011)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _trace(self, ws):
        path = os.path.join(ws, ".taskplane", "trace.jsonl")
        with open(path) as f:
            return [json.loads(line) for line in f]

    def test_two_serial_claims_both_refused_with_remedy_and_trace(self):
        # decision 0011 replayed as the criterion pins it: TWO claims (a
        # would-be wave) under serial mode, BOTH refused fail-closed
        task2 = dict(TASK, id="t2")
        ws = git_ws(self.tmp, [TASK, task2])
        loop.init(ws, "g", spec_path="s", checkpoints=["em"])  # serial
        loop.next_action(ws); loop.gate(ws, "pass")            # plan → execute
        for tid in ("t1", "t2"):
            out = loop.claim(ws, tid, os.path.join(ws, ".tp-work", tid))
            self.assertIn("error", out)
            # the remedy is NAMED: re-init parallel, or the serial path
            self.assertIn("--parallel", out["error"])
            self.assertIn("loop next", out["error"])
        # traced with the named reason, once per refused claim
        blocked = [e for e in self._trace(ws)
                   if e.get("event") == "loop_claim_blocked"]
        self.assertEqual([e.get("task") for e in blocked], ["t1", "t2"])
        self.assertEqual({e.get("reason") for e in blocked}, {"serial_mode"})
        # fail closed BEFORE any claim side effect: statuses still pending,
        # no contract slot activated, no worktree created
        for t in loop.load(ws)["tasks"]:
            self.assertEqual(t.get("status", "pending"), "pending")
            self.assertIsNone(t.get("workspace"))
        self.assertFalse(os.path.isdir(os.path.join(ws, ".tp-work")))

    def test_claim_still_works_on_parallel_loop(self):
        # the non-refusal path: the same claim call on a --parallel loop
        ws = TestParallelExecution._ws(TestParallelExecution())
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws)
        out = loop.claim(ws, "t1", agent_ws)
        self.assertEqual(out.get("claimed"), "t1")
        self.assertEqual(loop.load(ws)["tasks"][0]["status"], "running")


class TestPlanOrderingGate(unittest.TestCase):
    """B2 (R-0008): brief-shape tasks (taskplane/lens.py, lens_signals.py,
    tp.py) must be transitive dependency ancestors of every golden-brief
    regen task (taskplane/tests/fixtures/briefs/**) — enforced mechanically
    at BOTH plan transitions (the plan GATE and plan_approval approve), not
    by planner memory: a loop initialized without the 'plan' checkpoint
    goes plan→execute at the gate and must be refused THERE."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    SHAPE = {"id": "s1", "scope": ["taskplane/lens.py"], "tests": "true",
             "criteria": ["shape"]}
    GOLD = {"id": "g1", "scope": ["taskplane/tests/fixtures/briefs/**"],
            "tests": "true", "criteria": ["golden"],
            "new_modules": ["taskplane/tests"]}

    def _plan_ws(self, tasks, checkpoints=("plan",)):
        ws = git_ws(self.tmp, tasks)
        # the surfaces must exist so the plan's scope maps to real files
        os.makedirs(os.path.join(ws, "taskplane", "tests", "fixtures",
                                 "briefs"), exist_ok=True)
        for f in ("lens.py",):
            open(os.path.join(ws, "taskplane", f), "w").write("x=1\n")
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "surfaces"], cwd=ws)
        loop.init(ws, "g", spec_path="s", checkpoints=list(checkpoints))
        loop.next_action(ws)
        return ws

    def _trace_events(self, ws, event):
        with open(os.path.join(ws, ".taskplane", "trace.jsonl")) as f:
            return [json.loads(line) for line in f
                    if f'"{event}"' in line]

    def test_violating_plan_is_refused_at_the_gate_naming_both_tasks(self):
        ws = self._plan_ws([self.SHAPE, dict(self.GOLD)])      # no dep
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("plan ordering", out["error"])
        self.assertIn("s1", out["error"])                # offender named
        self.assertIn("g1", out["error"])                # offender named
        self.assertEqual(loop.load(ws)["step"], "plan")  # held at plan
        blocked = self._trace_events(ws, "loop_gate_blocked")
        self.assertTrue(blocked)
        self.assertEqual(blocked[-1].get("reason"), "ordering")

    def test_no_plan_checkpoint_loop_cannot_bypass_the_rule(self):
        # the reproduced bypass: `loop init --checkpoints em` has no
        # plan_approval step — the gate transitions plan→execute directly
        # and used to skip the ordering rule entirely
        ws = self._plan_ws([self.SHAPE, dict(self.GOLD)],
                           checkpoints=("em",))
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("plan ordering", out["error"])
        self.assertIn("s1", out["error"])
        self.assertIn("g1", out["error"])
        st = loop.load(ws)
        self.assertEqual(st["step"], "plan")             # NOT execute
        blocked = self._trace_events(ws, "loop_gate_blocked")
        self.assertEqual(blocked[-1].get("reason"), "ordering")

    def test_violating_plan_is_refused_at_approve_too(self):
        # belt and suspenders: the plan_approval transition keeps its own
        # enforcement (a hand-edited state cannot sneak past approve)
        ws = self._plan_ws([self.SHAPE, dict(self.GOLD, deps=["s1"])])
        loop.gate(ws, "pass")                            # ordered → approval
        self.assertEqual(loop.load(ws)["step"], "plan_approval")
        st = loop.load(ws)
        st["tasks"][1]["deps"] = []                      # de-order in state
        loop.save(ws, st)
        out = loop.approve(ws, by="human")
        self.assertIn("error", out)
        self.assertIn("plan ordering", out["error"])
        self.assertIn("s1", out["error"])
        self.assertIn("g1", out["error"])
        self.assertEqual(loop.load(ws)["step"], "plan_approval")  # held
        blocked = self._trace_events(ws, "loop_approve_blocked")
        self.assertEqual(blocked[-1].get("reason"), "ordering")

    def test_declared_dependency_passes_the_gate(self):
        ws = self._plan_ws([self.SHAPE, dict(self.GOLD, deps=["s1"])])
        loop.gate(ws, "pass")                            # plan → approval
        self.assertEqual(loop.load(ws)["step"], "plan_approval")
        out = loop.approve(ws, by="human")
        self.assertNotIn("error", out)
        self.assertEqual(loop.load(ws)["step"], "execute")

    def test_transitive_dependency_satisfies_the_rule(self):
        mid = {"id": "m1", "scope": ["src/todo/**"], "tests": "true",
               "criteria": ["mid"], "deps": ["s1"]}
        errs = tp.plan_ordering_errors(
            [self.SHAPE, mid, dict(self.GOLD, deps=["m1"])])
        self.assertEqual(errs, [])

    def test_same_task_touching_both_surfaces_is_ordered(self):
        both = {"id": "b1", "tests": "true", "criteria": ["b"],
                "scope": ["taskplane/tp.py",
                          "taskplane/tests/fixtures/briefs/**"]}
        self.assertEqual(tp.plan_ordering_errors([both]), [])

    def test_catch_all_scopes_do_not_synthesize_an_unsatisfiable_cycle(self):
        """EM (v3 phase 3): _scope_touches matches stems in BOTH directions,
        so a catch-all scope landed in the shape set AND the golden set at
        once. Two such tasks then demanded that each depend on the other —
        a cycle no plan can satisfy, dead-ending an already-planned loop at
        the human approval gate with no --force path.

        A task in both sets carries both halves itself and is self-ordered,
        which is what the single-task case already recognised."""
        for scope in (["**"], ["taskplane/**"], ["*"]):
            plan = [{"id": "t1", "scope": scope, "tests": "true",
                     "criteria": ["a"], "deps": []},
                    {"id": "t2", "scope": scope, "tests": "true",
                     "criteria": ["b"], "deps": []}]
            self.assertEqual(tp.plan_ordering_errors(plan), [], scope)

    def test_a_both_task_alongside_a_narrow_golden_task_is_not_paired(self):
        both = {"id": "b1", "scope": ["taskplane/**"], "tests": "true",
                "criteria": ["b"], "deps": []}
        self.assertEqual(tp.plan_ordering_errors([both, self.GOLD]), [])

    def test_the_real_phase2_gap_is_still_caught_after_the_fix(self):
        """The regression this gate exists for — t6 (brief shape) parallel
        to t7 (golden regen), two DISJOINT scopes — must still refuse."""
        errs = tp.plan_ordering_errors([self.SHAPE, self.GOLD])
        self.assertEqual(len(errs), 1)
        self.assertIn("s1", errs[0])
        self.assertIn("g1", errs[0])

    def test_the_refusal_names_scope_narrowing_as_a_remedy(self):
        """The old text named only 'add the dep or re-plan' — which for a
        catch-all pair was the one remedy that could not work."""
        errs = tp.plan_ordering_errors([self.SHAPE, self.GOLD])
        self.assertIn("narrow the scopes", errs[0])
        self.assertIn("deps", errs[0])

    def test_the_refusal_says_why_there_is_no_force(self):
        errs = tp.plan_ordering_errors([self.SHAPE, self.GOLD])
        self.assertIn("no --force", errs[0])
        self.assertIn("OLD brief shape", errs[0])

    def test_violation_detected_transitively_not_just_directly(self):
        gold = dict(self.GOLD, deps=["u1"])              # dep, but not on s1
        unrelated = {"id": "u1", "scope": ["src/todo/**"], "tests": "true",
                     "criteria": ["u"]}
        errs = tp.plan_ordering_errors([self.SHAPE, unrelated, gold])
        self.assertEqual(len(errs), 1)
        self.assertIn("g1", errs[0]); self.assertIn("s1", errs[0])

    def test_phase3_plan_shape_passes(self):
        # the shipped Phase 3 plan (ids/deps/scopes) — the rule governs the
        # phase's own plan and must accept it
        plan = [
            {"id": "t1", "deps": [], "scope": [
                "taskplane/loop.py", "taskplane/taskplane_lite.py",
                "taskplane/audit.py", "taskplane/tests/test_loop.py",
                "taskplane/tests/test_dor_dod.py",
                "taskplane/tests/test_audit_sweep.py"]},
            {"id": "t2", "deps": ["t1", "t3"], "scope": [
                "taskplane/loop.py", "taskplane/tp.py",
                "taskplane/taskplane_lite.py",
                "taskplane/tests/test_loop.py"]},
            {"id": "t3", "deps": [], "scope": [
                "taskplane/tp.py", "taskplane/decompose.py",
                "taskplane/tests/test_stage_waves.py",
                "taskplane/tests/test_codex_compat.py",
                "taskplane/tests/test_decompose.py"]},
            {"id": "t4", "deps": [], "scope": [
                "taskplane/requirements.py",
                "taskplane/tests/test_requirements.py",
                "taskplane/tests/fixtures/calibration/**"]},
            {"id": "t5", "deps": ["t3"], "scope": [
                "taskplane/decompose.py", "taskplane/lens.py",
                "taskplane/lens_signals.py", "taskplane/depgraph.py",
                "taskplane/tests/test_decompose.py",
                "taskplane/tests/test_lens_route_v2.py",
                "taskplane/tests/test_lens_signals_fixtures.py",
                "taskplane/tests/test_dashboard_v2.py",
                "taskplane/tests/fixtures/decompose/**",
                "taskplane/tests/fixtures/detectors/**"]},
            {"id": "t6", "deps": ["t2"], "scope": [
                "taskplane/taskplane_lite.py",
                "taskplane/tests/test_governance_invariants.py"]},
            {"id": "t7", "deps": [], "scope": [
                "skills/taskplane/SKILL.md", "skills/tp-go/SKILL.md",
                "references/harness-rules.md",
                "taskplane/tests/test_release_freshness.py"]},
            {"id": "t8", "deps": ["t2", "t7"], "scope": [
                "taskplane/tp.py", "docs/cli-reference.md",
                ".github/workflows/ci.yml",
                "taskplane/tests/test_release_freshness.py"]},
            {"id": "t9", "deps": ["t4", "t6", "t8"], "scope": [
                "taskplane/loop.py", "scripts/ci_unittest_floor.py",
                ".github/workflows/ci.yml", "taskplane/tests/conftest.py",
                "taskplane/tests/test_ci_floor.py",
                "taskplane/tests/test_*.py"]},
            {"id": "t10", "deps": ["t2", "t5", "t8", "t9"], "scope": [
                "taskplane/tp.py", "taskplane/tests/test_stage_waves.py",
                "taskplane/tests/test_codex_compat.py",
                "taskplane/tests/fixtures/briefs/**"]},
        ]
        self.assertEqual(tp.plan_ordering_errors(plan), [])


def _trace_events(ws, event=None):
    with open(os.path.join(ws, ".taskplane", "trace.jsonl")) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [r for r in rows if event is None or r.get("event") == event]


_VOLATILE = {"ts", "time", "now", "duration", "elapsed", "seconds"}


def _scrub(obj):
    """Wall-clock stamps are the only legitimate run-to-run difference when
    the same workspace bytes are gated twice; everything else must match."""
    if isinstance(obj, dict):
        return {k: ("<t>" if k.endswith("_at") or k in _VOLATILE
                    else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    return obj


class TestEngineSkewRefusal(unittest.TestCase):
    """A4 (R-0007, decision 0018): the evaluate gate refuses evidence that a
    DIFFERENT engine build produced.

    Recorded scenario replayed here (Phase 2 retro lesson 2 — the t7
    topology): a parallel-wave worker builds and evaluates inside its own
    worktree whose checkout of taskplane/ is AHEAD of the primary's; the
    orchestrator then gates in the primary. `_evaluation_errors` therefore
    ran under one build and judged evidence produced under another, so the
    verdict depended on WHICH process ran rather than on the evidence.

    STATED DESIGN LIMIT: the comparison is between the PRODUCING process and
    the VALIDATING process — producer-vs-validator skew, the t7 topology.
    It is not an authenticity check on the evidence file itself: a
    hand-authored .eval/verdict.json claiming some third engine is NOT what
    this detects. That case stays covered by the existing guards — the
    submission staleness re-attest binds verdict.json's bytes to the
    submission fingerprint, and the DoD/_evaluation_errors walk validates
    its content. Escalation path if it ever must be closed: stamp the
    engine fingerprint into .eval/verdict.json itself.
    """

    SURFACE = {"loop", "taskplane_lite", "audit", "lens", "lens_signals",
               "design_contract", "depgraph", "decompose", "requirements"}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _wave_ws(self):
        """A one-task parallel wave parked at EVALUATE with the worker's
        evidence written in its worktree — the t7 topology, minus the second
        engine (which the stamp stands in for)."""
        ws = git_ws(self.tmp, [TASK])
        loop.init(ws, "g", spec_path="s", checkpoints=["plan"], parallel=True)
        loop.next_action(ws)
        loop.gate(ws, "pass")                       # plan → plan_approval
        loop.approve(ws)                            # → execute (wave)
        agent_ws = os.path.join(ws, ".tp-work", "t1")
        subprocess.run(["git", "worktree", "add", "-q", agent_ws, "-b",
                        "tp/t1"], cwd=ws)
        loop.claim(ws, "t1", agent_ws)
        submit_gate(ws, "pass", task_id="t1")       # built
        loop.next_action(ws)                        # → evaluate
        write_verdict(ws)
        return ws

    def _restamp(self, ws, fingerprint):
        """Stand in for the second engine: rewrite ONLY the submission's
        engine stamp, leaving the evidence and its fingerprint untouched."""
        st = loop.load(ws)
        if fingerprint is None:
            st["_submission"].pop("engine_fingerprint", None)
        else:
            st["_submission"]["engine_fingerprint"] = fingerprint
        loop.save(ws, st)

    def test_fingerprint_is_the_validator_surface_bytes_not_its_paths(self):
        fp = tp.engine_fingerprint()
        self.assertEqual(fp, tp.engine_fingerprint())        # deterministic
        self.assertRegex(fp, r"^[0-9a-f]{64}$")
        self.assertEqual(set(tp.VALIDATOR_SURFACE), self.SURFACE)
        here = os.path.dirname(os.path.dirname(os.path.abspath(loop.__file__)))
        here = os.path.join(here, "taskplane")
        copy = os.path.join(self.tmp, "engine-copy")
        os.makedirs(copy)
        for name in tp.VALIDATOR_SURFACE:
            shutil.copy(os.path.join(here, name + ".py"), copy)
        fake = {n: types.SimpleNamespace(
            __file__=os.path.join(copy, n + ".py"))
            for n in tp.VALIDATOR_SURFACE}
        with unittest.mock.patch.dict(sys.modules, fake):
            # same bytes at a different path (worktree vs primary checkout)
            self.assertEqual(tp.engine_fingerprint(), fp)
            for name in sorted(tp.VALIDATOR_SURFACE):
                path = os.path.join(copy, name + ".py")
                original = open(path, "rb").read()
                with open(path, "ab") as f:
                    f.write(b"\n# newer engine\n")
                self.assertNotEqual(tp.engine_fingerprint(), fp,
                                    f"{name} is not in the fingerprint")
                open(path, "wb").write(original)
            self.assertEqual(tp.engine_fingerprint(), fp)

    def test_recorded_t7_skew_is_refused_then_gates_through_after_merge(self):
        ws = self._wave_ws()
        loop.submit(ws, "pass")
        evidence = json.loads(json.dumps(loop.load(ws)["_submission"]))
        self.assertEqual(evidence["engine_fingerprint"],
                         tp.engine_fingerprint())
        # the worktree engine is ahead of the primary validator
        self._restamp(ws, "f" * 64)
        before = json.dumps(loop.load(ws), sort_keys=True)
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("different engine build", out["error"])
        self.assertIn("git merge tp/t1", out["error"])
        self.assertIn("loop submit", out["error"])          # named remedy
        # `reason` distinguishes the running-engine mismatch from the
        # workspace-engine one the A4 repair added (R-0013): the payload has
        # to say WHICH pair diverged, or the message quotes two hashes with
        # no way to tell what was compared.
        self.assertEqual(out["engine_skew"],
                         {"submitted": "f" * 64,
                          "validator": tp.engine_fingerprint(),
                          "reason": "engine_skew"})
        # no transition, no state change at all — the task stays evaluable
        self.assertEqual(json.dumps(loop.load(ws), sort_keys=True), before)
        self.assertEqual(loop.load(ws)["step"], "evaluate")
        blocked = _trace_events(ws, "loop_gate_blocked")[-1]
        self.assertEqual(blocked["reason"], "engine_skew")
        self.assertEqual(blocked["submitted"], "f" * 64)
        self.assertEqual(blocked["validator"], tp.engine_fingerprint())
        # "merge tp/t1 into the primary": one engine now owns production and
        # validation. The evidence is IDENTICAL — a re-evaluation is never
        # stranded by the refusal.
        self._restamp(ws, tp.engine_fingerprint())
        self.assertEqual(loop.load(ws)["_submission"], evidence)
        out2 = loop.gate(ws, "pass")
        self.assertNotIn("error", out2)
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_absent_stamp_is_refused_and_a_resubmit_restamps(self):
        """ABSENT = REFUSE (fail-closed), with the in-flight case handled:
        a submission recorded by a pre-A4 engine carries no stamp, and the
        remedy that clears it is the same `loop submit` — so submit's
        idempotence key includes engine_fingerprint, otherwise the unstamped
        record would be kept and the loop stranded."""
        ws = self._wave_ws()
        loop.submit(ws, "pass")
        self._restamp(ws, None)                 # pre-A4 in-flight submission
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertIn("different engine build", out["error"])
        self.assertIn("no engine fingerprint", out["error"])
        self.assertIn("git merge tp/t1", out["error"])
        self.assertIsNone(out["engine_skew"]["submitted"])
        self.assertEqual(loop.load(ws)["step"], "evaluate")
        self.assertEqual(_trace_events(ws, "loop_gate_blocked")[-1]["reason"],
                         "engine_skew")
        again = loop.submit(ws, "pass")
        self.assertEqual(again["submission"]["engine_fingerprint"],
                         tp.engine_fingerprint())
        self.assertNotIn("error", loop.gate(ws, "pass"))
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_no_submission_record_is_not_this_guard_s_business(self):
        """The stamp governs a submission RECORD. A loop with no submission
        at all is the submission_required gate's refusal (already enforced
        above this pre-check) — this guard must not invent a second, weaker
        one, and legacy loops without the flag stay resumable."""
        self.assertIsNone(tp.engine_skew_refusal(self.tmp, None))
        self.assertIsNone(tp.engine_skew_refusal(self.tmp, {}))

    def test_the_comparison_runs_before_the_evaluation_walk(self):
        """Pure PRE-check: it can only refuse more, never validate less."""
        import inspect
        src = inspect.getsource(loop.gate)
        self.assertLess(src.index("engine_skew_refusal"),
                        src.index("_evaluation_errors("))

    def test_equal_fingerprint_gate_is_byte_identical_to_the_pre_a4_flow(self):
        """NON-SKEW DIFFERENTIAL: gate the SAME workspace bytes twice — once
        with the pre-check removed entirely (the pre-A4 flow), once with it
        live — and require identical results, identical post-state and an
        identical trace. Wall-clock stamps are the only scrubbed difference.
        """
        ws = self._wave_ws()
        loop.submit(ws, "pass")
        # the gate reads/writes the workspace AND the per-user state dir
        backup = os.path.join(self.tmp, "backup")
        state_backup = os.path.join(self.tmp, "backup-state")
        shutil.copytree(ws, backup, symlinks=True)
        shutil.copytree(loop.state_dir(ws), state_backup, symlinks=True)
        real = tp.engine_skew_refusal
        tp.engine_skew_refusal = lambda *a, **kw: None       # today's engine
        try:
            today_out = loop.gate(ws, "pass")
            today_state = loop.load(ws)
            today_trace = _trace_events(ws)
        finally:
            tp.engine_skew_refusal = real
        state = loop.state_dir(ws)
        shutil.rmtree(ws)
        shutil.rmtree(state)
        shutil.copytree(backup, ws, symlinks=True)
        shutil.copytree(state_backup, state, symlinks=True)
        a4_out = loop.gate(ws, "pass")
        a4_state = loop.load(ws)
        a4_trace = _trace_events(ws)
        self.assertNotIn("error", a4_out)
        self.assertEqual(a4_state["step"], "em")             # not vacuous:
        self.assertGreater(len(a4_trace), 2)                 # a real gate ran
        self.assertGreater(len(_scrub(a4_out)), 1)
        self.assertEqual(_scrub(today_out), _scrub(a4_out))
        self.assertEqual(_scrub(today_state), _scrub(a4_state))
        self.assertEqual(_scrub(today_trace), _scrub(a4_trace))
        self.assertEqual([r for r in a4_trace
                          if r.get("reason") == "engine_skew"], [])
