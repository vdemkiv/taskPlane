"""Dashboard component rendering and lifecycle publication hooks.

Renderer tests consume explicit projection fixtures. Phase transitions and
publication replay are tested through the current phase runtime in
 test_stage_loop_integration; this file does not simulate the retired loop.
"""
import json
import shutil
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402
import loop  # noqa: E402
import loop_status  # noqa: E402
import settings as operational_settings  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8").write("x = 1\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def _projection_state(ws, *, note="spec ok"):
    """A component input, not evidence of a Product-to-Plan transition."""
    from taskplane import requirements
    from taskplane.tests.phase_fixture import save_component_workflow
    requirement = requirements.record_requirement(
        ws, "demo feature", functional=["show the complete governed delivery plan"],
        acceptance=["criterion one: gate works", "criterion two: table",
                    "criterion three: escaped", "criterion four: tests"])
    state = {"step": "plan", "goal": "g", "requirement_id": requirement["id"],
             "tasks": [{"id": "t1", "scope": ["src/a/**"], "status": "pending"},
                       {"id": "t2", "scope": ["src/b/**"], "deps": ["t1"], "status": "pending"}]}
    save_component_workflow(ws, state)
    tier = next(iter(tp.MODEL_TIERS))
    tp.trace(ws, "model_tier", step="pm", tier=tier, model=None)
    tp.trace(ws, "loop_gate", step="pm", outcome="pass", note=note)
    tp.trace(ws, "model_tier", step="plan", tier=tier, model=None)
    tp.record_expected_dispatch(ws, "step", "tp-product", tier, None, ref="pm")


class TestAutoRender(unittest.TestCase):          # AC1
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


    def test_error_payloads_skip_dashboard(self):
        ws = _repo(self.tmp)                      # no loop at all
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertNotIn("dashboard", out)

    def test_every_committed_lifecycle_event_refreshes_exact_snapshot_once(self):
        calls = []
        original = loop_status.refresh_dashboard_snapshot
        loop_status.refresh_dashboard_snapshot = lambda ws, **kw: (
            calls.append((ws, kw)) or {"snapshot": {"fingerprint": "f"}})
        try:
            for name in operational_settings.REQUIRED_DASHBOARD_LIFECYCLE_EVENTS:
                def committed(_ws):
                    return {"step": "execute", "outcome": "success"}
                committed.__name__ = name
                wrapped = loop_status.with_dashboard(committed)
                before = len(calls)
                wrapped("/workspace")
                self.assertEqual(len(calls), before + 1)
        finally:
            loop_status.refresh_dashboard_snapshot = original

    def test_replan_resolve_and_committed_secondary_failure_still_refresh(self):
        calls = []
        original = loop_status.refresh_dashboard_snapshot
        loop_status.refresh_dashboard_snapshot = lambda ws, **kw: (
            calls.append((ws, kw)) or {"snapshot": {"fingerprint": "f"}})
        try:
            for name, result in (
                    ("replan", {"step": "plan"}),
                    ("resolve", {"step": "execute"}),
                    ("gate", {"step": "escalated", "worktree_cleanup": {
                        "status": "preserved",
                        "reason": "secondary failure"}})):
                def committed(_ws, _result=result):
                    return _result
                committed.__name__ = name
                loop_status.with_dashboard(committed)("/workspace")
            self.assertEqual(len(calls), 3)
            self.assertEqual([row[1]["event_type"] for row in calls],
                             ["replan", "resolve", "gate"])
        finally:
            loop_status.refresh_dashboard_snapshot = original


class TestJourney(unittest.TestCase):             # AC2
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = _repo(self.tmp)
        _projection_state(self.ws)

    def test_journey_lists_traversed_steps_with_detail(self):
        v = dashboard._journey(self.ws)
        steps = [x["step"] for x in v]
        self.assertIn("pm", steps)
        self.assertIn("plan", steps)
        pm = next(x for x in v if x["step"] == "pm")
        self.assertEqual(pm["agent"], "tp-product")
        self.assertEqual(pm["outcome"], "pass")
        self.assertIn("note minimized for audit", pm["note"])
        self.assertNotIn("spec ok", pm["note"])
        self.assertIn(pm["tier"], tp.MODEL_TIERS)

    def test_widget_renders_navigator_clickable(self):
        frag = dashboard.widget(self.ws)
        self.assertIn("tp-journey-s", frag)
        self.assertIn("tpJ(", frag)               # client-side reveal
        self.assertIn("tp-product", frag)

    def test_human_gate_without_brief_still_appears(self):
        tp.trace(self.ws, "loop_gate", step="plan_approval", outcome="pass",
                 note="approved by human")
        visits = dashboard._journey(self.ws)
        decision = next(row for row in visits if row["step"] == "plan_approval")
        self.assertEqual(decision["agent"], "you")
        self.assertEqual(decision["outcome"], "pass")


class TestStatsAlways(unittest.TestCase):         # AC3
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = _repo(self.tmp)

    def test_stats_band_present_without_any_loop(self):
        frag = dashboard.widget(self.ws)          # graceful: no loop state
        self.assertIn("tp-stats-s", frag)

    def test_model_table_joins_expected_and_observed(self):
        _projection_state(self.ws)
        exp = tp._load_queue(
            tp._dispatch_path(self.ws, "expected_dispatch.json"))
        tp.record_observed_dispatch(self.ws, "tp-product", None,
                                    exp[-1], ok=True)
        frag = dashboard.widget(self.ws)
        self.assertIn("tp-models-s", frag)
        self.assertIn("tp-product", frag)
        self.assertIn("session ✓", frag)

    def test_rows_without_observation_show_dash(self):
        _projection_state(self.ws)
        rows = dashboard._model_rows(self.ws)
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["dispatched"], "—")


class TestSpineNavigation(unittest.TestCase):     # AC2 addendum (sign-off feedback)
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = _repo(self.tmp)
        _projection_state(self.ws)

    def test_visited_spine_stages_are_clickable(self):
        frag = dashboard.widget(self.ws)
        self.assertIn("tpSpine('pm')", frag)      # Define — executed
        self.assertIn("tpSpine('plan')", frag)    # Plan — current
        self.assertNotIn("tpSpine('em')", frag)   # Review — not reached yet
        self.assertIn("function tpSpine", frag)

    def test_tier_label_carries_model_in_brackets(self):
        frag = dashboard.widget(self.ws)
        # pm/plan resolve to inherit by default -> "(session)"; a pinned
        # tier shows the concrete model, e.g. "cheap (haiku)"
        self.assertIn("(session)", frag)

    def test_journey_entries_carry_step_addressing(self):
        frag = dashboard.widget(self.ws)
        self.assertIn('data-step="pm"', frag)
        self.assertIn('data-step="plan"', frag)


class TestArtifactsInDetail(unittest.TestCase):   # sign-off feedback r2
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = _repo(self.tmp)
        _projection_state(self.ws)

    def test_pm_detail_lists_all_acceptance_criteria(self):
        frag = dashboard.widget(self.ws)
        self.assertIn("acceptance", frag)
        for c in ("criterion one: gate works", "criterion two: table",
                  "criterion three: escaped", "criterion four: tests"):
            self.assertIn(c, frag)

    def test_plan_detail_lists_full_execution_plan(self):
        frag = dashboard.widget(self.ws)
        self.assertIn("execution plan", frag)
        self.assertIn("t1", frag)
        self.assertIn("src/b/**", frag)


class TestEscaping(unittest.TestCase):            # security NFR
    def test_trace_text_is_escaped(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ws = _repo(tmp)
        _projection_state(ws, note="<script>alert(1)</script>")
        frag = dashboard.widget(ws)
        self.assertNotIn("<script>alert(1)</script>", frag)
        self.assertNotIn("&lt;script&gt;", frag)
        self.assertIn("note minimized for audit", frag)
        self.assertIn("raw text intentionally omitted", frag)


# ==========================================================================
# t5 / E3 (R-0011 design row 3) — depgraph component-layer a11y + layout.
#
# Component nodes lacked the keydown/Escape tooltip dismissal module nodes
# already have (keyboard users could open a tooltip and never dismiss it),
# and the ring radius was a FIXED r(m)+24 — component labels overlapped on
# many-component modules. The gap is now a monotonically increasing
# function of the module's component count, computed host-portably in
# Python and carried per component in the embedded data.
# ==========================================================================


class TestDepgraphComponentLayer(unittest.TestCase):
    def setUp(self):
        import depgraph as dg
        self.dg = dg
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = _repo(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render(self, per_module):
        """Render an HTML page for a graph carrying `per_module` =
        {module: n} synthetic components; return (html, embedded data)."""
        dg = self.dg
        dg.scan(self.ws)
        p = dg._path(self.ws)
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        comps = []
        for mod, n in sorted(per_module.items()):
            raw["modules"].setdefault(mod, {"files": 1, "kind": "module"})
            for i in range(n):
                comps.append({"id": f"{mod}::c{i}", "module": mod,
                              "files": [f"{mod}/f{i}.py"], "symbols": [],
                              "deps": []})
        raw["components"] = comps
        with open(p, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        out = dg.to_html(self.ws, out=os.path.join(self.tmp, "g.html"))
        html = open(out, encoding="utf-8").read()
        blob = html.split("const G=", 1)[1].split(";\n", 1)[0]
        return html, json.loads(blob.replace("\\u003c", "<"))

    def test_component_details_dismiss_on_escape(self):
        html, _data = self._render({"m/a": 2})
        block = html.split("const comps=G.components", 1)[1]
        self.assertIn("cc.addEventListener('keydown'", block)
        self.assertIn("ev.key==='Escape'", block)

    def test_ring_radius_grows_with_component_count(self):
        _h2, d2 = self._render({"m/a": 2})
        _h12, d12 = self._render({"m/a": 12})
        r2 = {c["ring"] for c in d2["components"]}
        r12 = {c["ring"] for c in d12["components"]}
        self.assertEqual(len(r2), 1)
        self.assertEqual(len(r12), 1)
        self.assertGreater(r12.pop(), r2.pop())

    def test_ring_gap_function_is_monotonically_increasing(self):
        f = self.dg.component_ring_gap
        vals = [f(n) for n in range(1, 40)]
        self.assertEqual(vals, sorted(vals))
        self.assertGreater(vals[-1], vals[0])
        self.assertGreaterEqual(vals[0], self.dg.COMPONENT_RING_BASE)


    def test_renderer_stays_host_portable_and_self_contained(self):
        html, data = self._render({"m/a": 3, "m/b": 2})
        self.assertNotIn("<script src=", html)
        self.assertNotIn("<link ", html)
        # the only absolute URL a self-contained page may carry is the SVG
        # namespace literal — nothing is fetched at view time
        for chunk in html.split("http")[1:]:
            self.assertTrue(chunk.startswith("://www.w3.org/2000/svg"),
                            chunk[:60])
        rings = {c["module"]: c["ring"] for c in data["components"]}
        self.assertGreater(rings["m/a"], rings["m/b"])


if __name__ == "__main__":
    unittest.main()
