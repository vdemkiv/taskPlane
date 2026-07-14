"""The A/B `selection` step: native human gate between evaluate and em for
variant builds — variants never merge, one gets picked (or hybridized)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402


def _git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, capture_output=True, check=False)


def _repo():
    ws = tempfile.mkdtemp(prefix="tp-sel-")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t"); _git(ws, "config", "user.name", "t")
    open(os.path.join(ws, "a.py"), "w").write("x = 1\n")
    _git(ws, "add", "-A"); _git(ws, "commit", "-qm", "base")
    return ws


AB_PLAN = {"mode": "ab-selection", "tasks": [
    {"id": "feat-variant-a", "variant": "A", "req": "R-0001",
     "scope": ["src/**"], "tests": "true"},
    {"id": "feat-variant-b", "variant": "B", "req": "R-0001",
     "scope": ["src/**"], "tests": "true"},
]}


def _to_plan_approved(ws, plan=AB_PLAN, parallel=True):
    loop.init(ws, "ab goal", parallel=parallel)
    state = loop.load(ws)
    state["step"] = "plan"
    loop.save(ws, state)
    os.makedirs(os.path.join(ws, "plan"), exist_ok=True)
    json.dump(plan, open(os.path.join(ws, "plan", "tasks.json"), "w"))
    loop.gate(ws, "pass")            # plan → plan_approval (+ ab detection)
    loop.approve(ws)                 # → execute
    return loop.load(ws)


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
            {"id": "t1", "scope": ["src/**"], "tests": "true"},
            {"id": "t2", "scope": ["src/**"], "tests": "true"}]}
        _to_plan_approved(self.ws, plan=plan)
        w = loop.wave(self.ws)
        self.assertEqual(len(w["wave"]), 1)
        self.assertEqual(len(w["held"]), 1)

    def test_all_variants_passed_pauses_at_selection(self):
        state = _to_plan_approved(self.ws)
        # simulate both variants built; evaluate each to pass
        for t in state["tasks"]:
            t["status"] = "built"
        state["step"] = "evaluate"
        state["current_task"] = 0
        loop.save(self.ws, state)
        loop.gate(self.ws, "pass")                     # variant a passes
        state = loop.load(self.ws)
        state["step"] = "evaluate"; state["current_task"] = 1
        loop.save(self.ws, state)
        r = loop.gate(self.ws, "pass")                 # variant b passes
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
        self._to_selection()
        loop.select(self.ws, "B")                      # winner: b → em
        state = loop.load(self.ws)
        state["step"] = "fix"
        state["current_task"] = 1                      # the winner task
        loop.save(self.ws, state)
        loop.gate(self.ws, "pass")                     # fix → evaluate
        r = loop.gate(self.ws, "pass")                 # evaluate pass
        self.assertEqual(r["step"], "em")              # NOT execute/selection


if __name__ == "__main__":
    unittest.main()
