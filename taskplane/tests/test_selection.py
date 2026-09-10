from taskplane.tests.phase_fixture import save_component_workflow
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
    # Component state for selection policy. Current phase transitions are
    # exercised by test_stage_loop_integration, not replayed in every case.
    state = {"goal": "ab selection policy", "step": "execute",
             "baseline": loop.tp.git_head(ws), "parallel": parallel,
             "current_task": 0, "checkpoints": ["plan", "em"],
             "tasks": json.loads(json.dumps(plan["tasks"])),
             "ab": plan.get("mode") == "ab-selection"}
    save_component_workflow(ws, state)
    return state


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
        state = _to_plan_approved(self.ws)
        ready, held, _ = loop.select_ready_tasks(state["tasks"], passed=set(),
            repository_files=set(), allow_isolated_variants=True)
        self.assertEqual([row["id"] for row in ready],
                         ["feat-variant-a", "feat-variant-b"])
        self.assertEqual(held, [])

    def test_same_scope_non_variants_still_serialize(self):
        tasks = [{"id": "t1", "scope": ["src/**"]},
                 {"id": "t2", "scope": ["src/**"]}]
        ready, held, _ = loop.select_ready_tasks(tasks, passed=set(),
            repository_files=set(), allow_isolated_variants=True)
        self.assertEqual([row["id"] for row in ready], ["t1"])
        self.assertEqual([row["task"] for row in held], ["t2"])

    def _to_selection(self):
        state = _to_plan_approved(self.ws)
        for t in state["tasks"]:
            t["status"] = "passed"
        state["step"] = "selection"
        save_component_workflow(self.ws, state)
        transition = mock.patch.object(loop, "_stage_loop_transition", return_value=None)
        transition.start()
        self.addCleanup(transition.stop)

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
        r = loop.approve(self.ws, by="human:simulated")
        self.assertIn("error", r)
        self.assertIn("loop select", r["error"])

    def test_select_only_at_selection_gate(self):
        _to_plan_approved(self.ws)
        r = loop.select(self.ws, "A")
        self.assertIn("error", r)

if __name__ == "__main__":
    unittest.main()
