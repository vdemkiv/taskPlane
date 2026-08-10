"""Evidence bundle (P2, R-0012) — engine computes facts, agent owns judgment.

Phase 3 agents spent 41 percent of shell wall-clock rebuilding, one shell
call at a time, facts the engine already held: which criteria exist, which
lenses routed, what the diff touched, which graph nodes are impacted,
whether the suite passed. `tp loop evidence` hands all of that over in one
call.

The guardrail these tests exist to pin is the SPLIT. The engine may state
an obligation; it may never discharge one. So the load-bearing test here is
not that the bundle is complete — it is that a bundle taken straight from
the engine and submitted unchanged is REFUSED at the evaluate gate.
"""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402


TASK = {"id": "t1", "scope": ["src/todo/**"], "tests": "true",
        "criteria": ["complete() marks done"]}


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


def submit_gate(ws, outcome="pass", task_id=None):
    submitted = loop.submit(ws, outcome, task_id=task_id)
    if "error" in submitted:
        return submitted
    return loop.gate(ws, outcome, task_id=task_id)


class _AtEvaluate(unittest.TestCase):
    """Drive a loop to the evaluate step with one real task."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.ws = git_ws(self.tmp, [TASK])
        loop.init(self.ws, "g", spec_path="specs/spec.md")
        loop.next_action(self.ws)
        loop.gate(self.ws, "pass")               # plan → plan_approval
        loop.approve(self.ws, "plan")
        loop.next_action(self.ws)                # execute
        open(os.path.join(self.ws, "src", "todo", "a.py"), "a").write("y=2\n")
        submit_gate(self.ws, "pass")             # execute → evaluate
        loop.next_action(self.ws)

    def bundle(self, **kw):
        return loop.evidence(self.ws, **kw)


class TestTheEngineNeverJudges(_AtEvaluate):
    def test_every_criterion_slot_comes_back_empty(self):
        b = self.bundle()
        self.assertTrue(b["criteria"], "the obligation must be stated")
        for row in b["criteria"]:
            self.assertEqual(row["status"], "")
            self.assertEqual(row["evidence"], "")

    def test_every_lens_slot_comes_back_empty(self):
        b = self.bundle()
        for row in b.get("lenses") or []:
            self.assertEqual(row["verdict"], "")
            self.assertIsNone(row["blockers"])

    def test_the_top_level_verdict_comes_back_empty(self):
        self.assertEqual(self.bundle()["verdict"], "")

    def test_graph_dispositions_come_back_empty(self):
        g = self.bundle().get("graph")
        if g:
            for row in g["dispositions"]:
                self.assertEqual(row["status"], "")
                self.assertEqual(row["evidence"], "")
            self.assertEqual(g["requirements_checked"], [])
            self.assertEqual(g["contracts_checked"], [])

    def test_an_unedited_bundle_is_refused_at_the_gate(self):
        """THE load-bearing test. If the engine's own output could pass the
        gate, the engine would be grading itself and the evaluate step would
        be decoration."""
        self.bundle(write=True)
        path = os.path.join(self.ws, ".eval", "verdict.json")
        self.assertTrue(os.path.exists(path))
        result = submit_gate(self.ws, "pass")
        self.assertIn("error", result,
                      "an unjudged bundle must never satisfy the gate")
        self.assertEqual(loop.load(self.ws)["step"], "evaluate",
                         "the loop must not advance on engine output alone")

    def test_the_refusal_names_the_unproven_criteria(self):
        self.bundle(write=True)
        result = submit_gate(self.ws, "pass")
        blob = json.dumps(result)
        self.assertIn("acceptance criterion", blob)


class TestTheBundleMatchesWhatTheGateDemands(_AtEvaluate):
    def test_the_lens_set_is_exactly_the_gate_s_expected_set(self):
        """A bundle that briefed a NARROWER lens set than the gate checks
        would quietly send evaluators into a refusal they cannot see coming
        — and a wider one would invent obligations. Both are drift; the
        bundle and the gate must derive from the same route."""
        state = loop.load(self.ws)
        task = state["tasks"][state["current_task"]]
        routing = loop.lens_router.route_git_diff(
            self.ws, base=state.get("baseline") or "HEAD",
            task_type=task.get("type"), stage=loop.EVALUATE_ROUTE_STAGE,
            breadth="routed")
        expected = {e["id"] for e in routing["lenses"]
                    if e.get("mode") != "none"}
        offered = {r["lens"] for r in self.bundle().get("lenses") or []}
        self.assertEqual(offered, expected)

    def test_the_criteria_are_exactly_the_gate_s_expected_criteria(self):
        state = loop.load(self.ws)
        task = state["tasks"][state["current_task"]]
        expected = loop._criteria_for(self.ws, state, task)
        offered = [r["criterion"] for r in self.bundle()["criteria"]]
        self.assertEqual(offered, expected)

    def test_a_filled_bundle_does_pass_the_gate(self):
        """The complement: once an agent actually discharges the obligation
        the bundle stated, nothing else is in the way."""
        b = self.bundle()
        for row in b["criteria"]:
            row["status"] = "met"
            row["evidence"] = "covered by the task's tests"
        for row in b.get("lenses") or []:
            row["verdict"] = "pass"
            row["blockers"] = 0
        if b.get("graph"):
            for row in b["graph"]["dispositions"]:
                row["status"] = "tested"
                row["evidence"] = "covered by declared task tests"
            b["graph"]["requirements_checked"] = \
                b["graph"].pop("requirements_to_check")
            b["graph"]["contracts_checked"] = \
                b["graph"].pop("contracts_to_verify")
        b["verdict"] = "pass"
        os.makedirs(os.path.join(self.ws, ".eval"), exist_ok=True)
        with open(os.path.join(self.ws, ".eval", "verdict.json"), "w") as f:
            json.dump(b, f)
        result = submit_gate(self.ws, "pass")
        self.assertNotIn("error", result)


class TestWriteIsNonDestructive(_AtEvaluate):
    def test_an_existing_verdict_is_never_overwritten(self):
        path = os.path.join(self.ws, ".eval", "verdict.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"authored": "by the agent"}, f)
        out = self.bundle(write=True)
        self.assertFalse(out["written"])
        with open(path) as f:
            self.assertEqual(json.load(f), {"authored": "by the agent"})

    def test_without_write_nothing_is_written(self):
        self.bundle()
        self.assertFalse(os.path.exists(
            os.path.join(self.ws, ".eval", "verdict.json")))


class TestDegradationIsLoud(_AtEvaluate):
    def test_a_routing_failure_surfaces_instead_of_briefing_no_lenses(self):
        """A bundle that silently dropped the lens obligation would look
        complete while briefing nothing — the dangerous direction."""
        with mock.patch.object(loop.lens_router, "route_git_diff",
                               side_effect=RuntimeError("catalog gone")):
            b = self.bundle()
        self.assertIn("lenses_error", b)
        self.assertIn("do not submit", b["lenses_error"])

    def test_an_unknown_task_id_is_refused(self):
        self.assertIn("error", self.bundle(task_id="nope"))

    def test_no_loop_is_refused(self):
        import tempfile
        bare = tempfile.mkdtemp()
        self.assertIn("error", loop.evidence(bare))


class TestTheSuiteIsCitedNotRerun(_AtEvaluate):
    def test_the_bundle_cites_the_run_the_execute_gate_already_paid_for(self):
        """The wave economics in one assertion: the execute gate ran this
        task's tests, so the evaluator's bundle must cite that run rather
        than buy a second identical one."""
        b = self.bundle()
        self.assertTrue(b["suite"]["cited"])
        self.assertEqual(b["suite"]["returncode"], 0)
        self.assertIsNotNone(b["suite"].get("seconds_saved"))

    def test_the_kill_switch_forces_the_bundle_to_execute(self):
        with mock.patch.dict(os.environ,
                             {"TASKPLANE_NO_SUITE_CACHE": "1"}, clear=False):
            b = self.bundle()
        self.assertFalse(b["suite"]["cited"])
        self.assertIn("seconds", b["suite"])

    def test_the_bundle_is_traced(self):
        self.bundle()
        path = os.path.join(tp.tp_dir(self.ws), "trace.jsonl")
        with open(path) as f:
            events = [json.loads(x) for x in f if x.strip()]
        rows = [e for e in events if e.get("event") == "evidence_bundle"]
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["task"], "t1")


if __name__ == "__main__":
    unittest.main()
