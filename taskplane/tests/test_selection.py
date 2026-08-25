"""The A/B `selection` step: native human gate between evaluate and em for
variant builds — variants never merge, one gets picked (or hybridized)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import depgraph  # noqa: E402
from taskplane.tests.review_kernel_support import (  # noqa: E402
    complete_evaluate_slots,
)


def _git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, capture_output=True, check=False)


def _repo():
    ws = tempfile.mkdtemp(prefix="tp-sel-")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t"); _git(ws, "config", "user.name", "t")
    open(os.path.join(ws, "a.py"), "w", encoding="utf-8").write("x = 1\n")
    _git(ws, "add", "-A"); _git(ws, "commit", "-qm", "base")
    return ws


AB_PLAN = {"mode": "ab-selection", "tasks": [
    {"id": "feat-variant-a", "variant": "A",
     "scope": ["src/**"], "new_modules": ["src"], "tests": "true",
     "criteria": ["variant A is ready for human selection"]},
    {"id": "feat-variant-b", "variant": "B",
     "scope": ["src/**"], "new_modules": ["src"], "tests": "true",
     "criteria": ["variant B is ready for human selection"]},
]}


def _to_plan_approved(ws, plan=AB_PLAN, parallel=True):
    loop.init(ws, "ab goal", parallel=parallel)
    state = loop.load(ws)
    state["step"] = "plan"
    loop.save(ws, state)
    os.makedirs(os.path.join(ws, "plan"), exist_ok=True)
    json.dump(plan, open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8"))
    loop.gate(ws, "pass")            # plan → plan_approval (+ ab detection)
    loop.approve(ws)                 # → execute
    return loop.load(ws)


def _claim_variant_worktrees(ws):
    """Give simulated variant builds their real isolated task worktrees."""
    for task in loop.load(ws)["tasks"]:
        if task.get("status") != "pending":
            continue
        worker = os.path.join(ws, ".tp-work", task["id"])
        _git(ws, "worktree", "add", "-q", worker, "-b",
             "tp/" + task["id"])
        claimed = loop.claim(ws, task["id"], worker)
        if claimed.get("error"):
            raise AssertionError(claimed["error"])
        depgraph.scan(worker)


def _pass_eval(ws):
    brief = loop.next_action(ws)
    if brief.get("error"):
        raise AssertionError(brief["error"])
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    act_ws = task.get("workspace") if state.get("parallel") else ws
    complete_evaluate_slots(
        act_ws, session_id="selection-" + task["id"])
    routed = [row for row in brief["lenses"] if row.get("mode") != "none"]
    os.makedirs(os.path.join(act_ws, ".eval"), exist_ok=True)
    with open(os.path.join(act_ws, ".eval", "verdict.json"), "w",
              encoding="utf-8") as f:
        json.dump({"schema": "taskplane.evaluator-output/v1",
                   "task": task["id"],
                   "requirement": task.get("req") or
                                  state.get("requirement_id") or "",
                   "verdict": "pass",
                   "criteria": [{"criterion": c, "status": "met",
                                  "evidence": "verified"}
                                for c in loop._criteria_for(ws, state, task)],
                   "lenses": [{"lens": row["id"], "verdict": "pass",
                               "blockers": 0} for row in routed],
                   "graph": {"dispositions": [],
                             "requirements_checked": [],
                             "contracts_checked": []},
                   "failures": []}, f)
    with mock.patch("runtime_eval.guide_loop",
                    return_value={"status": "on_path", "recovered": False}):
        loop.submit(ws, "pass")
    return loop.gate(ws, "pass")


class TestSelectionStep(unittest.TestCase):
    def setUp(self):
        self.ws = _repo()

    def test_ab_detected_from_plan(self):
        state = _to_plan_approved(self.ws)
        self.assertTrue(state["ab"])
        self.assertIn("selection", loop.HUMAN_STEPS)

    def test_wave_does_not_serialize_variants(self):
        _to_plan_approved(self.ws)
        w = loop.wave(self.ws)
        ready = [e["task"]["id"] for e in w["wave"]]
        self.assertEqual(sorted(ready),
                         ["feat-variant-a", "feat-variant-b"])
        self.assertEqual(w["held"], [])
        self.assertTrue(all(e["merge_on_pass"] is False for e in w["wave"]))

    def test_same_scope_non_variants_still_serialize(self):
        plan = {"tasks": [
            {"id": "t1", "scope": ["src/**"], "new_modules": ["src"],
             "tests": "true", "criteria": ["task one passes review"]},
            {"id": "t2", "scope": ["src/**"], "new_modules": ["src"],
             "tests": "true", "criteria": ["task two passes review"]}]}
        _to_plan_approved(self.ws, plan=plan)
        w = loop.wave(self.ws)
        self.assertEqual(len(w["wave"]), 1)
        self.assertEqual(len(w["held"]), 1)

    def test_all_variants_passed_pauses_at_selection(self):
        _to_plan_approved(self.ws)
        _claim_variant_worktrees(self.ws)
        state = loop.load(self.ws)
        # simulate both variants built; evaluate each to pass
        for t in state["tasks"]:
            t["status"] = "built"
        state["step"] = "evaluate"
        state["current_task"] = 0
        loop.save(self.ws, state)
        _pass_eval(self.ws)                              # variant a passes
        state = loop.load(self.ws)
        state["step"] = "evaluate"; state["current_task"] = 1
        loop.save(self.ws, state)
        r = _pass_eval(self.ws)                          # variant b passes
        self.assertEqual(r["step"], "selection")
        nxt = loop.next_action(self.ws)
        self.assertTrue(nxt["paused"])
        self.assertEqual(len(nxt["variants"]), 2)

    def _to_selection(self):
        state = _to_plan_approved(self.ws)
        for t in state["tasks"]:
            t["status"] = "passed"
        state["step"] = "selection"
        loop.save(self.ws, state)

    def test_select_winner(self):
        self._to_selection()
        r = loop.select(self.ws, "A", note="cards fit the manager persona")
        self.assertEqual(r["step"], "em")
        self.assertEqual(r["selection"]["choice"], "feat-variant-a")
        state = loop.load(self.ws)
        a = next(t for t in state["tasks"] if t["id"] == "feat-variant-a")
        b = next(t for t in state["tasks"] if t["id"] == "feat-variant-b")
        self.assertTrue(a.get("selected"))
        self.assertEqual(b["status"], "not_selected")

    def test_select_by_task_id_and_bad_choice(self):
        self._to_selection()
        bad = loop.select(self.ws, "C")
        self.assertIn("error", bad)
        r = loop.select(self.ws, "feat-variant-b")
        self.assertEqual(r["selection"]["choice"], "feat-variant-b")

    def test_select_hybrid_goes_back_to_plan(self):
        self._to_selection()
        r = loop.select(self.ws, "hybrid", note="A engine + B face")
        self.assertEqual(r["step"], "plan")
        state = loop.load(self.ws)
        self.assertTrue(all(t["status"] == "reference"
                            for t in state["tasks"]))

    def test_plain_approve_rejected_at_selection(self):
        self._to_selection()
        r = loop.approve(self.ws)
        self.assertIn("error", r)
        self.assertIn("loop select", r["error"])

    def test_select_only_at_selection_gate(self):
        _to_plan_approved(self.ws)
        r = loop.select(self.ws, "A")
        self.assertIn("error", r)

    def test_post_selection_fix_cycle_returns_to_em(self):
        # human sends the winner back at signoff → fix → evaluate pass must
        # go to em (loser is settled as not_selected; selection is done)
        _to_plan_approved(self.ws)
        _claim_variant_worktrees(self.ws)
        state = loop.load(self.ws)
        for task in state["tasks"]:
            task["status"] = "passed"
        state["step"] = "selection"
        loop.save(self.ws, state)
        loop.select(self.ws, "B")                      # winner: b → em
        state = loop.load(self.ws)
        state["step"] = "fix"
        state["current_task"] = 1                      # the winner task
        loop.save(self.ws, state)
        loop.next_action(self.ws)                       # activate fix contract
        loop.submit(self.ws, "pass")
        loop.gate(self.ws, "pass")                     # fix → evaluate
        r = _pass_eval(self.ws)                          # evaluate pass
        self.assertEqual(r["step"], "em")              # NOT execute/selection


if __name__ == "__main__":
    unittest.main()
