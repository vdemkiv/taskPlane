"""v1.5.4 — lens coverage + dependency graph surfaced in the review.

New lenses drifted out of the visualization and the dependency graph was
skipped in reviews. Both now derive from their source of truth and appear in
the dashboard automatically:
  * render_lens_coverage() reads catalog.json — add a lens, it shows up;
  * the findings dashboard renders a blast-radius panel + coverage panel;
  * the headline carries coverage + impact counts (never-skippable);
  * an empty graph explains the polyglot cross-service gap, not silence;
  * docs/marketing counts must match the catalog (no hand-drift).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG = os.path.join(ROOT, "lenses", "catalog.json")


def _catalog_count():
    c = json.load(open(CATALOG, encoding="utf-8"))
    return len(c["lenses"] if isinstance(c, dict) else c)


class TestLensCoverage(unittest.TestCase):
    def test_coverage_counts_from_catalog(self):
        n = _catalog_count()
        cov = dashboard.lens_coverage({"security": "deep", "qa": "sweep"})
        self.assertEqual(cov["total"], n)
        self.assertEqual(cov["deep"], 1)
        self.assertEqual(cov["sweep"], 1)
        self.assertEqual(cov["skipped"], n - 2)

    def test_new_lens_appears_without_touching_dashboard(self):
        # the panel is generated from the catalog, so all catalog names render
        html = dashboard.render_lens_coverage({"security": "deep"})
        cat = json.load(open(CATALOG, encoding="utf-8"))
        lenses = cat["lenses"] if isinstance(cat, dict) else cat
        for lz in lenses:
            self.assertIn(dashboard._esc(lz["name"]), html, lz["id"])

    def test_catalog_mode_when_no_routing(self):
        self.assertIn("LENS CATALOG", dashboard.render_lens_coverage(None))
        self.assertIn("LENS COVERAGE",
                      dashboard.render_lens_coverage({"security": "deep"}))


class TestReviewGraph(unittest.TestCase):
    def test_empty_graph_explains_polyglot(self):
        tmp = tempfile.mkdtemp()
        ws = os.path.join(tmp, "ws")
        os.makedirs(ws)
        subprocess.run(["git", "init", "-q"], cwd=ws)
        h = dashboard.render_review_graph(ws)
        self.assertIn("polyglot", h)
        self.assertIn("tp graph edge", h)

    def test_graph_with_impact_shows_blast_radius(self):
        tmp = tempfile.mkdtemp()
        ws = os.path.join(tmp, "ws")
        os.makedirs(ws)
        subprocess.run(["git", "init", "-q"], cwd=ws)
        orig = dashboard._dg.load
        dashboard._dg.load = lambda _w: {"modules": {"a": {}, "b": {}},
                                         "edges": [{"from": "a", "to": "b"}]}
        try:
            h = dashboard.render_review_graph(
                ws, {"total_impacted": 3, "touched": ["a"]})
        finally:
            dashboard._dg.load = orig
        self.assertIn("3 modules impacted", h)


class TestFindingsIntegration(unittest.TestCase):
    def test_findings_fragment_carries_coverage_and_graph(self):
        tmp = tempfile.mkdtemp()
        ws = os.path.join(tmp, "ws")
        os.makedirs(ws)
        subprocess.run(["git", "init", "-q"], cwd=ws)
        meta = {"title": "rev", "lens_coverage": {"security": "deep"},
                "ws": ws}
        frag = dashboard.render_findings(
            [{"severity": "high", "title": "x"}], meta)
        self.assertIn("tp-lens-coverage", frag)
        self.assertIn("tp-review-graph", frag)

    def test_headline_carries_coverage_and_impact(self):
        h = dashboard.headline_findings(
            [{"severity": "high"}],
            {"title": "rev", "lens_coverage": {"security": "deep"},
             "impact": {"total_impacted": 5}})
        self.assertIn("deep", h)
        self.assertIn("of " + str(_catalog_count()), h)
        self.assertIn("touches 5 modules", h)


class TestNoCountDrift(unittest.TestCase):
    def test_readme_lens_count_matches_catalog(self):
        n = _catalog_count()
        readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        # the README must not advertise a stale lens count anywhere
        self.assertNotIn("22 lens", readme)
        self.assertNotIn("22 review lenses", readme)
        self.assertIn(f"{n}", readme)          # the real count appears
        self.assertIn(f"{n}-lens", readme)     # e.g. "full 25-lens catalog"

    def test_plugin_manifest_count_matches_catalog(self):
        n = _catalog_count()
        man = open(os.path.join(ROOT, ".claude-plugin",
                                "plugin.json"), encoding="utf-8").read()
        self.assertIn(f"{n} review lenses", man)


class TestRenderingTierDoc(unittest.TestCase):
    def test_model_tiers_doc_pins_rendering_to_cheap(self):
        doc = open(os.path.join(ROOT, "discipline", "model-tiers.md"), encoding="utf-8").read()
        self.assertIn("Rendering runs on the cheap tier", doc)


if __name__ == "__main__":
    unittest.main()
