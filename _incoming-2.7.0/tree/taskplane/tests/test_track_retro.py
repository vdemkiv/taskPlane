import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens  # noqa: E402
import loop  # noqa: E402
import track  # noqa: E402

CAT = lens.load_catalog()


_GONE = {"tech-strategy", "cost-roi", "business-alignment"}


class TestAdvisoryTierRemoved(unittest.TestCase):
    """v1.0 removed the exec advisory tier from the code-review catalog;
    strategy now lives in the on-demand north-star review, not a lens tier."""

    def test_strategy_artifact_routes_nothing(self):
        r = lens.route([], artifact_type="strategy", catalog=CAT)
        self.assertEqual({x["id"] for x in r["lenses"]}, set())

    def test_advisory_ids_gone_from_catalog_and_code_routes(self):
        self.assertFalse(_GONE & {l["id"] for l in CAT["lenses"]})
        r2 = lens.route(["src/todo/core.py"], catalog=CAT)
        self.assertFalse(_GONE & {x["id"] for x in r2["lenses"]})

    def test_advisory_not_an_nfr_axis(self):
        import requirements as reqs
        self.assertFalse(_GONE & reqs.NFR_LENSES)

    def test_pm_step_routes_no_advisory_lenses(self):
        ws = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=ws)
        loop.init(ws, "a business goal")          # free-text → pm step
        act = loop.next_action(ws)
        self.assertEqual(act["step"], "pm")
        self.assertFalse(_GONE & {x["id"] for x in (act["lenses"] or [])})


class TestTracks(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_new_list_switch_close(self):
        out = track.new(self.ws, "auth", "build auth")
        self.assertEqual(out["active"], "auth")   # first track auto-activates
        track.new(self.ws, "billing", "build billing")
        self.assertEqual(track.list_(self.ws)["active"], "auth")
        # give auth some loop state, then switch away and back
        loop.save(self.ws, {"goal": "build auth", "step": "plan",
                            "tasks": None, "current_task": 0,
                            "max_fix_cycles": 2, "checkpoints": []})
        track.switch(self.ws, "billing")
        self.assertIsNone(loop.load(self.ws))     # billing has no state yet
        back = track.switch(self.ws, "auth")
        self.assertTrue(back["has_loop_state"])   # auth's state restored
        self.assertEqual(loop.load(self.ws)["goal"], "build auth")
        track.close(self.ws, "auth")
        self.assertIsNone(track.list_(self.ws)["active"])


class TestRetro(unittest.TestCase):
    def test_retro_mines_trace_and_records_lesson(self):
        import kb
        import taskplane_lite as tpl
        ws = tempfile.mkdtemp()
        state = {"goal": "retro goal", "step": "done",
                 "max_fix_cycles": 2, "checkpoints": [],
                 "current_task": 0,
                 "tasks": [{"id": "t1", "scope": ["src/**"], "status":
                            "passed", "fix_cycles": 2}]}
        loop.save(ws, state)
        tpl.trace(ws, "hook_deny", tool="Write", reason="out of scope")
        tpl.trace(ws, "refinement_gate", task="t1", requirement="R-0001",
                  score=0.9, blocking=False, mode="full")
        rep = loop.retro(ws)
        self.assertEqual(rep["hook_denials"], 1)
        # score 0.9 predicted smooth, actual 2 fix cycles → forecast missed
        self.assertFalse(rep["forecast_accuracy"][0]["forecast_held"])
        self.assertTrue(any("forecast missed" in ln or "fix-cycle" in ln
                            for ln in rep["lessons"]))
        titles = [d["title"] for d in kb.list_decisions(ws)]
        self.assertTrue(any(t.startswith("Retrospective") for t in titles))


if __name__ == "__main__":
    unittest.main()
