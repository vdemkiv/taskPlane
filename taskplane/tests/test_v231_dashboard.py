"""v2.3.1 dashboard fix wave — regression tests for 4 HIGH findings, all in
taskplane/dashboard.py.

H1  widget_paged's page 1 could tail-truncate straight through the trailing
    <script> (_WIDGET_JS), leaving emitted gate buttons (tpFire/tpSend/
    tpView/tpTab) wired to undefined functions. Fix: the <style>/<script>
    chrome is now FIXED (outside the truncatable region) on page 1 — a
    byte-fit trim can only ever remove panel content.

H2  dashboard.py bucketed severities with its OWN local map (e.g.
    'major' -> med) that diverged from the engine's loop.normalize_severity
    ('major' -> high) — a finding that blocks the gate could render in a
    lesser bucket. Fix: _sev_info's bucket now comes from
    loop.normalize_severity directly.

H3/H4  render_findings_paged never rendered the sign-off gate, lens
    coverage, the blast-radius graph, or the note on ANY page in a paged
    (large) review — the human's primary action was unreachable. Fix: the
    gate box (shared with render_findings via _gate_box), lens coverage
    and the graph now land on the summary page (or a dedicated follow-up
    page when they don't fit alongside the gate).
"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402
import depgraph  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def _bytes(html):
    return len(html.encode("utf-8"))


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


def _loop_ws_at_action_ceiling(tmp, n_tasks=40):
    """A parallel-wave loop state, deep into execute, with 40 tasks (lots of
    harness cards) AND its contract at the action ceiling (budget_exhausted)
    — enough to blow page 1 (header+notice+hero+gatebar+dor+stats+pipe+
    harness) well past PAGE_BUDGET on its own."""
    ws = _repo(tmp)
    import requirements as reqs
    rec = reqs.record_requirement(
        ws, "demo feature",
        acceptance=["criterion one", "criterion two", "criterion three"])
    loop.init(ws, "fixture goal", requirement_id=rec["id"])
    os.makedirs(os.path.join(ws, "specs"), exist_ok=True)
    open(os.path.join(ws, "specs", "spec.md"), "w", encoding="utf-8").write("# spec\n")
    loop.next_action(ws)
    loop.gate(ws, "pass", note="spec ok")
    st = loop.load(ws)
    st["tasks"] = [{"id": f"t{i+1}", "scope": [f"src/{i}/**"],
                    "status": "pending"} for i in range(n_tasks)]
    st["tasks"][0]["status"] = "passed"
    st["step"] = "execute"
    st["parallel"] = True
    loop.save(ws, st)
    for i in range(25):
        tp.trace(ws, "loop_step", step="execute", role="tp-executor")
        tp.trace(ws, "hook_deny", tool="Bash", reason=f"deny {i}")
    import json
    c = {"task_id": "t2", "read_only": False,
         "coding": {"scope_paths": ["src/**"],
                    "command_policy": {"deny": []}},
         "budget": {"max_actions": 5}}
    with open(os.path.join(tp.tp_dir(ws), "active_contract.json"), "w", encoding="utf-8") as f:
        json.dump(c, f)
    with open(os.path.join(tp.tp_dir(ws), "meter.json"), "w", encoding="utf-8") as f:
        json.dump({"t2": {"actions": 5}}, f)
    return ws


# ----------------------------------------- H1: page-1 chrome never cut off

class TestDashboardRenderingIsObservational(unittest.TestCase):
    def test_signoff_dashboard_does_not_execute_dod(self):
        state = {"step": "signoff", "tasks": [{"status": "passed"}]}
        with mock.patch.object(
                loop, "_signoff_dod",
                side_effect=AssertionError("render attempted to execute DoD")):
            html = dashboard._widget_gatebar(
                ".", state, "signoff", state["tasks"], False, 0, 40)

        self.assertIn("sign off", html)
        self.assertIn("evaluated only when you act", html)


class TestWidgetPagedChromeSurvivesTruncation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _loop_ws_at_action_ceiling(self.tmp)

    def test_page1_head_body_actually_exceeds_budget(self):
        # sanity: this fixture must reproduce the over-budget condition,
        # otherwise the test below proves nothing.
        p = dashboard._widget_parts(self.ws)
        head_body = (p["header"] + p["notice"] + p["hero"] + p["gatebar"]
                     + p["dor"] + p["stats"] + p["pipe_s"]
                     + p["harness_panel"])
        self.assertGreater(_bytes(head_body), dashboard.PAGE_BUDGET)

    def test_page1_keeps_full_widget_js_and_fits_budget(self):
        pages = dashboard.widget_paged(self.ws)
        self.assertGreater(len(pages), 1)
        p1 = pages[0]["html"]
        self.assertLessEqual(_bytes(p1), dashboard.PAGE_BUDGET)
        # the JS function DEFINITIONS must be intact, not just a stray
        # reference inside an onclick= attribute
        self.assertIn("function tpFire", p1)
        self.assertIn("function tpView", p1)
        self.assertIn("function tpSend", p1)
        self.assertIn("function tpTab", p1)
        self.assertIn("</script>", p1)

    def test_every_other_page_still_fits_budget(self):
        pages = dashboard.widget_paged(self.ws)
        for p in pages:
            self.assertLessEqual(_bytes(p["html"]), dashboard.PAGE_BUDGET,
                                 p["title"])


class TestGraphContextIsDiscoverable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _loop_ws_at_action_ceiling(self.tmp)

    def test_graph_tab_is_visible_before_the_simple_view(self):
        html = dashboard.widget(self.ws)
        self.assertLess(html.index('id="tp-tab-map"'),
                        html.index('id="tp-simple"'))

    def test_graph_tab_opens_existing_detailed_panel(self):
        html = dashboard.widget(self.ws)
        self.assertIn('aria-controls="tp-panel-map"', html)
        self.assertIn('onclick="tpTab(\'map\',true)"', html)
        self.assertIn(
            'function tpTab(w,moveFocus){if(moveFocus)tpView("detail")',
            html)
        self.assertIn(
            'id="tp-panel-map" role="tabpanel" '
            'aria-labelledby="tp-tab-map"', html)

    def test_reference_style_graph_is_a_visible_change_path_svg(self):
        depgraph.save(self.ws, {
            "modules": {"caller": {}, "changed": {}, "contract:api": {}},
            "files": {}, "recorded": [], "meta": {},
            "edges": [
                {"from": "caller", "to": "changed", "kind": "calls"},
                {"from": "changed", "to": "contract:api",
                 "kind": "consumes"}],
        })
        html = dashboard.render_dependency_flow(self.ws, impact={
            "touched": ["changed"],
            "impacted": {1: [{"module": "caller", "via": "changed",
                               "kind": "calls"}]},
            "total_impacted": 1, "depth_limit": 3, "truncated": False,
            "boundary_nodes": ["contract:api"], "policy_blocked": [],
        })
        self.assertIn('<svg viewBox="0 0 880 ', html)
        self.assertIn("caller", html)
        self.assertIn("changed — CHANGED", html)
        self.assertIn("contract boundary — traversal stops here", html)
        self.assertIn("changed by this scope", html)

    def test_workflow_and_graph_are_primary_sections_not_hidden_content(self):
        depgraph.save(self.ws, {"modules": {"taskplane": {}}, "edges": [],
                                "files": {}, "recorded": [], "meta": {}})
        html = dashboard.widget(self.ws)
        self.assertIn('id="tp-workflow-flow"', html)
        self.assertIn('id="tp-dependency-flow"', html)
        self.assertLess(html.index('id="tp-workflow-flow"'),
                        html.index('id="tp-detail-tabs"'))
        self.assertLess(html.index('id="tp-tab-map"'),
                        html.index('id="tp-panel-map"'))
        self.assertLess(html.index('id="tp-panel-map"'),
                        html.index('id="tp-dependency-flow"'))

    def test_durable_report_uses_reference_order_and_ends_at_the_gate(self):
        depgraph.save(self.ws, {"modules": {"taskplane": {}}, "edges": [],
                                "files": {}, "recorded": [], "meta": {}})
        html = dashboard.report_widget(self.ws)
        order = [html.index(probe) for probe in (
            'id="tp-workflow-flow"', 'id="tp-dependency-flow"',
            "execution — governed agents", "requirements, decisions",
            "action budget exhausted")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn('id="tp-simple"', html)


# ------------------------------------ H2: canonical severity vocabulary

class TestSeverityBucketMatchesGate(unittest.TestCase):
    def test_blocker_major_critical_all_bucket_as_high_via_loop(self):
        for label in ("blocker", "major", "critical"):
            bucket, *_ = dashboard._sev_info(label)
            self.assertEqual(bucket, loop.normalize_severity(label))
            self.assertEqual(bucket, "high", label)

    def test_bucket_never_diverges_from_loop_normalize_severity_for_any_label(self):
        # sweep a wide vocabulary — dashboard's bucket must equal the
        # engine's, not a second hand-maintained map that can drift.
        for label in ("blocker", "high", "critical", "major", "sev1", "p0",
                      "p1", "med", "medium", "moderate", "low", "minor",
                      "trivial", "info", "question", "praise", "note",
                      "nit", "totally-unknown-label", ""):
            bucket, *_ = dashboard._sev_info(label)
            self.assertEqual(bucket, loop.normalize_severity(label), label)

    def test_headline_counts_major_as_high_not_medium(self):
        findings = [{"severity": "major", "title": "m"}]
        h = dashboard.headline_findings(findings, {"title": "r"})
        self.assertIn("1 high", h)
        self.assertIn("0 med", h)

    def test_a_major_finding_lands_in_the_high_bucket_card_list(self):
        # render_findings_paged buckets findings using the same _sev_info —
        # a 'major' finding must land in the 'high — fix first' page, the
        # one the gate actually blocks on.
        findings = ([{"severity": "major", "domain": "d", "title": "MFIND",
                     "scenario": "x" * 50, "fix": "y" * 50}]
                    + [{"severity": "low", "domain": "d", "title": f"l{i}"}
                       for i in range(2)])
        norm = []
        for f in findings:
            f = dashboard._alias(f)
            k, *_ = dashboard._sev_info(f.get("severity", "med"))
            norm.append((f["title"], k))
        self.assertIn(("MFIND", "high"), norm)


# -------------------------- H3/H4: gate + coverage + graph survive paging

class TestPagedFindingsCarryTheGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)

    def _big_findings(self, n=60):
        return [{"severity": "high", "domain": "security",
                 "title": f"finding {i} with a longish descriptive title",
                 "scenario": "x" * 200, "fix": "y" * 180}
                for i in range(n)]

    def _meta(self):
        return {
            "title": "big review",
            "gate": True,
            "gate_title": "sign off on this review",
            "gate_buttons": [
                {"label": "approve", "prompt": "approve the review",
                 "primary": True},
                {"label": "send back", "prompt": "send it back"}],
            "lens_coverage": {"security": "deep", "perf": "sweep"},
            "note": "reviewed by 26 lenses",
            "ws": self.ws,
        }

    def test_full_page_would_exceed_budget(self):
        # sanity: this fixture must actually force the paged path
        full = dashboard.render_findings(self._big_findings(), self._meta())
        self.assertGreater(_bytes(full), dashboard.PAGE_BUDGET)

    def test_gate_buttons_reachable_on_some_page(self):
        pages = dashboard.render_findings_paged(
            self._big_findings(), self._meta())
        self.assertGreater(len(pages), 1)
        blob = " ".join(p["html"] for p in pages)
        self.assertIn("tpSend", blob)
        self.assertIn("approve the review", blob)
        self.assertIn("sign off on this review", blob)

    def test_gate_lands_on_the_summary_page_specifically(self):
        pages = dashboard.render_findings_paged(
            self._big_findings(), self._meta())
        self.assertIn("summary", pages[0]["title"])
        self.assertIn("tpSend", pages[0]["html"])
        self.assertIn("approve the review", pages[0]["html"])

    def test_lens_coverage_and_graph_panel_appear_somewhere(self):
        pages = dashboard.render_findings_paged(
            self._big_findings(), self._meta())
        blob = " ".join(p["html"] for p in pages)
        self.assertTrue("LENS COVERAGE" in blob or "LENS CATALOG" in blob)
        self.assertIn("DEPENDENCY GRAPH", blob)

    def test_note_appears_somewhere(self):
        pages = dashboard.render_findings_paged(
            self._big_findings(), self._meta())
        blob = " ".join(p["html"] for p in pages)
        self.assertIn("reviewed by 26 lenses", blob)

    def test_every_page_still_fits_budget(self):
        pages = dashboard.render_findings_paged(
            self._big_findings(), self._meta())
        for p in pages:
            self.assertLessEqual(_bytes(p["html"]), dashboard.PAGE_BUDGET,
                                 p["title"])

    def test_no_gate_no_gate_markup_emitted(self):
        # negative check: a non-gated paged review must not fabricate one
        meta = self._meta()
        meta["gate"] = False
        pages = dashboard.render_findings_paged(self._big_findings(), meta)
        blob = " ".join(p["html"] for p in pages)
        self.assertNotIn("tpSend", blob)


if __name__ == "__main__":
    unittest.main()
