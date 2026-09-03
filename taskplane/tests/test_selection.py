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
import requirements  # noqa: E402
from tests.root_session_fixture import open_delivery_root  # noqa: E402


def _git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, capture_output=True, check=False)


def _repo():
    ws = tempfile.mkdtemp(prefix="tp-sel-")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t"); _git(ws, "config", "user.name", "t")
    open(os.path.join(ws, "a.py"), "w", encoding="utf-8").write("x = 1\n")
    _git(ws, "add", "-A"); _git(ws, "commit", "-qm", "base")
    os.makedirs(os.path.join(ws, ".taskplane"), exist_ok=True)
    open(os.path.join(ws, ".taskplane", "codex-hook.py"), "w",
         encoding="utf-8").write("# stable test launcher\n")
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
    acceptance = [
        str(criterion)
        for task in plan["tasks"]
        for criterion in task.get("criteria") or []
    ]
    requirement = requirements.record_requirement(
        ws, "select one validated implementation variant",
        acceptance=acceptance)
    loop.init(
        ws, "ab goal", requirement_id=requirement["id"], parallel=parallel)
    state = loop.load(ws)
    state["step"] = "plan"
    loop.save(ws, state)
    os.makedirs(os.path.join(ws, "plan"), exist_ok=True)
    plan = {"requirement": requirement["id"],
            "delivery_mode": "build", "automatic_lenses": [],
            "plan_authority": "human:test-fixture", **plan}
    json.dump(plan, open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8"))
    loop.gate(ws, "pass")            # plan → plan_approval (+ ab detection)
    loop.approve(ws)                 # → execute
    return loop.load(ws)


class TestSelectionStep(unittest.TestCase):
    def setUp(self):
        self.ws = _repo()
        previous = os.environ.get("TASKPLANE_SESSION_ID")
        os.environ["TASKPLANE_SESSION_ID"] = "selection-test-session"
        self.addCleanup(
            lambda: os.environ.pop("TASKPLANE_SESSION_ID", None)
            if previous is None else os.environ.__setitem__(
                "TASKPLANE_SESSION_ID", previous))

    def test_ab_detected_from_plan(self):
        state = _to_plan_approved(self.ws)
        self.assertTrue(state["ab"])
        self.assertIn("selection", loop.HUMAN_STEPS)

    def test_wave_does_not_serialize_variants(self):
        _to_plan_approved(self.ws)
        authority = open_delivery_root(self.ws)
        w = loop.wave(
            self.ws, root_observation_authority=authority)
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
        authority = open_delivery_root(self.ws)
        w = loop.wave(
            self.ws, root_observation_authority=authority)
        self.assertEqual(len(w["wave"]), 1)
        self.assertEqual(len(w["held"]), 1)

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

if __name__ == "__main__":
    unittest.main()
