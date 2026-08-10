"""Dashboard v2 (R-0001) — regression tests against the acceptance criteria.

AC1: gate()/next_action() refresh .taskplane/dashboard.html every transition
     and include a `dashboard` field in the payload.
AC2: the widget carries a step journey — one entry per traversed step with
     agent/model/outcome detail, revealed client-side.
AC3: the stats band + agent→model table render on every widget, fed by the
     model_tier / expected_dispatch / observed_dispatch records.
NFR: trace-derived text is HTML-escaped; empty/old runs degrade gracefully.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402
import loop  # noqa: E402
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


class TestAutoRender(unittest.TestCase):          # AC1
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_gate_refreshes_fragment_and_payload(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")
        os.makedirs(os.path.join(ws, 'specs'), exist_ok=True); open(os.path.join(ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        out = loop.gate(ws, "pass")               # pm -> plan
        self.assertIn("dashboard", out)
        p = os.path.join(tp.tp_dir(ws), "dashboard.html")
        self.assertTrue(os.path.exists(p))
        self.assertIn("mission control", open(p, encoding="utf-8").read())

    def test_next_action_refreshes_fragment(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")
        os.makedirs(os.path.join(ws, 'specs'), exist_ok=True); open(os.path.join(ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        loop.gate(ws, "pass")
        p = os.path.join(tp.tp_dir(ws), "dashboard.html")
        before = open(p, encoding="utf-8").read()
        out = loop.next_action(ws)                # plan brief
        self.assertIn("dashboard", out)
        after = open(p, encoding="utf-8").read()
        self.assertNotEqual(before, after)        # refreshed, not stale

    def test_error_payloads_skip_dashboard(self):
        ws = _repo(self.tmp)                      # no loop at all
        out = loop.gate(ws, "pass")
        self.assertIn("error", out)
        self.assertNotIn("dashboard", out)


class TestJourney(unittest.TestCase):             # AC2
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        loop.init(self.ws, "g")
        os.makedirs(os.path.join(self.ws, 'specs'), exist_ok=True); open(os.path.join(self.ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        loop.next_action(self.ws)                 # pm visit (model_tier)
        loop.gate(self.ws, "pass", note="spec ok")  # closes pm, -> plan
        loop.next_action(self.ws)                 # plan visit

    def test_journey_lists_traversed_steps_with_detail(self):
        v = dashboard._journey(self.ws)
        steps = [x["step"] for x in v]
        self.assertIn("pm", steps)
        self.assertIn("plan", steps)
        pm = next(x for x in v if x["step"] == "pm")
        self.assertEqual(pm["agent"], "tp-product")
        self.assertEqual(pm["outcome"], "pass")
        self.assertEqual(pm["note"], "spec ok")
        self.assertIn(pm["tier"], tp.MODEL_TIERS)

    def test_widget_renders_navigator_clickable(self):
        frag = dashboard.widget(self.ws)
        self.assertIn("tp-journey-s", frag)
        self.assertIn("tpJ(", frag)               # client-side reveal
        self.assertIn("tp-product", frag)

    def test_human_gate_without_brief_still_appears(self):
        st = loop.load(self.ws)
        st["step"] = "plan_approval"
        loop.save(self.ws, st)
        loop.gate(self.ws, "pass", note="approved by human")
        v = dashboard._journey(self.ws)
        pa = [x for x in v if x["step"] == "plan_approval"]
        self.assertTrue(pa)
        self.assertEqual(pa[-1]["agent"], "you")


class TestStatsAlways(unittest.TestCase):         # AC3
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)

    def test_stats_band_present_without_any_loop(self):
        frag = dashboard.widget(self.ws)          # graceful: no loop state
        self.assertIn("tp-stats-s", frag)

    def test_model_table_joins_expected_and_observed(self):
        loop.init(self.ws, "g")
        os.makedirs(os.path.join(self.ws, 'specs'), exist_ok=True); open(os.path.join(self.ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        loop.next_action(self.ws)                 # records expectation
        exp = tp._load_queue(
            tp._dispatch_path(self.ws, "expected_dispatch.json"))
        tp.record_observed_dispatch(self.ws, "tp-product", None,
                                    exp[-1], ok=True)
        frag = dashboard.widget(self.ws)
        self.assertIn("tp-models-s", frag)
        self.assertIn("tp-product", frag)
        self.assertIn("session ✓", frag)

    def test_rows_without_observation_show_dash(self):
        loop.init(self.ws, "g")
        os.makedirs(os.path.join(self.ws, 'specs'), exist_ok=True); open(os.path.join(self.ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        loop.next_action(self.ws)
        rows = dashboard._model_rows(self.ws)
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["dispatched"], "—")


class TestSpineNavigation(unittest.TestCase):     # AC2 addendum (sign-off feedback)
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        loop.init(self.ws, "g")
        os.makedirs(os.path.join(self.ws, 'specs'), exist_ok=True); open(os.path.join(self.ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        loop.next_action(self.ws)
        loop.gate(self.ws, "pass")                # pm done -> plan
        loop.next_action(self.ws)

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
        self.ws = _repo(self.tmp)
        import requirements as reqs
        rec = reqs.record_requirement(
            self.ws, "demo feature",
            acceptance=["criterion one: gate works", "criterion two: table",
                        "criterion three: escaped", "criterion four: tests"])
        loop.init(self.ws, "g", requirement_id=rec["id"])
        os.makedirs(os.path.join(self.ws, 'specs'), exist_ok=True); open(os.path.join(self.ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        loop.next_action(self.ws)                 # pm visit
        loop.gate(self.ws, "pass")                # -> plan
        st = loop.load(self.ws)
        st["tasks"] = [
            {"id": "t1", "scope": ["src/a/**"], "status": "pending"},
            {"id": "t2", "scope": ["src/b/**"], "deps": ["t1"],
             "status": "pending"}]
        loop.save(self.ws, st)
        loop.next_action(self.ws)                 # plan visit

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

    def test_grey_selection_on_journey_and_spine(self):
        frag = dashboard.widget(self.ws)
        self.assertIn('b.style.background=on?"var(--surface-0)"', frag)
        # v2.3.0: spine ids are per-view (s/d) — the old shared id was a
        # duplicate-DOM-id bug that highlighted the hidden copy.
        self.assertIn("tp-spine-s-pm", frag)
        self.assertIn("tp-spine-d-pm", frag)
        self.assertIn('me.style.background="var(--surface-0)"', frag)


class TestEscaping(unittest.TestCase):            # security NFR
    def test_trace_text_is_escaped(self):
        tmp = tempfile.mkdtemp()
        ws = _repo(tmp)
        loop.init(ws, "g")
        os.makedirs(os.path.join(ws, 'specs'), exist_ok=True); open(os.path.join(ws, 'specs', 'spec.md'), 'w', encoding="utf-8").write('# spec\n')
        loop.next_action(ws)
        loop.gate(ws, "pass", note="<script>alert(1)</script>")
        frag = dashboard.widget(ws)
        self.assertNotIn("<script>alert(1)</script>", frag)
        self.assertIn("&lt;script&gt;", frag)


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

    def test_component_nodes_dismiss_the_tooltip_on_escape(self):
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

    def test_no_fixed_ring_radius_remains_in_the_renderer(self):
        html, _data = self._render({"m/a": 2})
        self.assertNotIn("r(m)+24", html)

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
