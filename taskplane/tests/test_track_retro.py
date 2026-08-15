import os
import json
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
    def _repo(self):
        ws = tempfile.mkdtemp(prefix="tp-retro-")
        os.makedirs(os.path.join(ws, "src"))
        with open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8") as f:
            f.write("def answer():\n    return 42\n")
        for args in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", "-c", "user.email=t@t",
                            "-c", "user.name=t"] + args,
                           cwd=ws, check=True)
        subprocess.run(["git", "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-qm", "base"],
                       cwd=ws, check=True)
        return ws

    def test_retro_mines_trace_and_records_lesson(self):
        import kb
        import taskplane_lite as tpl
        ws = self._repo()
        state = {"goal": "retro goal", "step": "retro",
                 "max_fix_cycles": 2, "checkpoints": [],
                 "current_task": 0,
                 "tasks": [{"id": "t1", "scope": ["src/**"], "status":
                            "passed", "fix_cycles": 2}]}
        loop.save(ws, state)
        tpl.trace(ws, "hook_deny", tool="Write", reason="out of scope")
        tpl.trace(ws, "refinement_gate", task="t1", requirement="R-0001",
                  score=0.9, blocking=False, mode="full")
        tpl.trace(ws, "lens_route", step="evaluate", requested_breadth="routed",
                  lenses=[["architecture", "deep"], ["backend", "light"],
                          ["frontend", "n/a"]])
        pending = loop.next_action(ws)
        self.assertEqual(pending["action"], "loop_retro")
        with open(os.path.join(ws, ".taskplane", "dashboard.html"),
                  encoding="utf-8") as f:
            self.assertIn("finalizing — retro + graph true-up", f.read())
        rep = loop.retro(ws)
        self.assertEqual(rep["hook_denials"], 1)
        # score 0.9 predicted smooth, actual 2 fix cycles → forecast missed
        self.assertFalse(rep["forecast_accuracy"][0]["forecast_held"])
        self.assertTrue(any("forecast missed" in ln or "fix-cycle" in ln
                            for ln in rep["lessons"]))
        self.assertEqual(rep["lens_routing"][0]["counts"],
                         {"deep": 1, "light": 1, "n/a": 1})
        self.assertEqual(rep["graph_true_up"]["scanned_head"],
                         subprocess.check_output(
                             ["git", "rev-parse", "HEAD"], cwd=ws,
                             text=True, encoding="utf-8",
                             errors="replace").strip())
        self.assertTrue(rep["graph_true_up"]["content_fingerprint"])
        self.assertEqual(loop.load(ws)["step"], "done")
        with open(os.path.join(ws, ".taskplane", "dashboard.html"),
                  encoding="utf-8") as f:
            dashboard = f.read()
        self.assertIn("retro and graph true-up recorded", dashboard)
        self.assertNotIn("run the retro</button>", dashboard)
        titles = [d["title"] for d in kb.list_decisions(ws)]
        self.assertTrue(any(t.startswith("Retrospective") for t in titles))

        # A host retry or a second button click returns the sealed report;
        # it must not create a second KB decision or rescan the graph.
        before = len(kb.list_decisions(ws))
        replay = loop.retro(ws)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["graph_true_up"], rep["graph_true_up"])
        self.assertEqual(len(kb.list_decisions(ws)), before)

    def test_retro_refuses_to_run_before_its_stage(self):
        import kb
        ws = self._repo()
        loop.save(ws, {"goal": "too early", "step": "execute",
                       "max_fix_cycles": 2, "checkpoints": [],
                       "current_task": 0,
                       "tasks": [{"id": "t1", "scope": ["src/**"],
                                  "status": "running", "fix_cycles": 0}]})
        out = loop.retro(ws)
        self.assertIn("error", out)
        self.assertIn("only runs after sign-off", out["error"])
        self.assertEqual(kb.list_decisions(ws), [])

    def test_retro_report_carries_canonical_finding_summary(self):
        ws = self._repo()
        loop.save(ws, {"goal": "finding summary", "step": "retro",
                       "max_fix_cycles": 2, "checkpoints": [],
                       "current_task": 0, "tasks": []})
        os.makedirs(os.path.join(ws, ".em-review"))
        with open(os.path.join(ws, ".em-review", "findings.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"findings": [
                {"lens": "security", "severity": "blocker"},
                {"lens": "backend", "severity": "minor"},
            ]}, f)
        rep = loop.retro(ws)
        self.assertEqual(rep["findings"], {
            "total": 2, "by_severity": {"high": 1, "low": 1},
            "by_lens": {"backend": 1, "security": 1}})

    def test_graph_true_up_failure_keeps_the_loop_open(self):
        import depgraph
        ws = self._repo()
        loop.save(ws, {"goal": "graph failure", "step": "retro",
                       "max_fix_cycles": 2, "checkpoints": [],
                       "current_task": 0, "tasks": []})
        original = depgraph.scan
        depgraph.scan = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("scanner unavailable"))
        try:
            out = loop.retro(ws)
        finally:
            depgraph.scan = original
        self.assertIn("graph true-up failed", out["error"])
        self.assertEqual(loop.load(ws)["step"], "retro")
        self.assertEqual(loop.load(ws)["retro"]["status"], "prepared")

    def test_trace_failure_keeps_open_and_retry_reuses_decision(self):
        import kb
        import retro as retro_engine
        ws = self._repo()
        loop.save(ws, {"goal": "trace retry", "step": "retro",
                       "max_fix_cycles": 2, "checkpoints": [],
                       "current_task": 0, "tasks": []})
        original = retro_engine.tp.trace
        retro_engine.tp.trace = lambda *args, **kwargs: None
        try:
            failed = loop.retro(ws)
        finally:
            retro_engine.tp.trace = original
        self.assertIn("trace receipt was not recorded", failed["error"])
        self.assertEqual(loop.load(ws)["step"], "retro")
        decisions = kb.list_decisions(ws)
        self.assertEqual(len(decisions), 1)

        completed = loop.retro(ws)
        self.assertNotIn("error", completed)
        self.assertEqual(loop.load(ws)["step"], "done")
        self.assertEqual(len(kb.list_decisions(ws)), len(decisions))


if __name__ == "__main__":
    unittest.main()
