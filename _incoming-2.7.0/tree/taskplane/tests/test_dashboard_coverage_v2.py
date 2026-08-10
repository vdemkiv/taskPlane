"""t3 — coverage honesty: dashboard coverage map v2 + HEADLINE.

The dashboard's lens-coverage panel and the never-skippable findings
headline learn the route-v2 coverage shape while keeping the legacy shape
byte-identical (dual-shape, like the severity bridge precedent):

  * legacy  meta.lens_coverage = {id: "deep"|"sweep"}          — unchanged
  * v2      meta.lens_coverage = {id: {"verdict": "deep"|"light"|"n/a",
                                       "score": float,
                                       "evidence"|"negative_evidence": [..]}}

v2 renders verdict chips (the verdict WORD on every chip — never
color-only), every n/a lens shows its negative-evidence reason (title
attr + inline text), the HEADLINE reads
`lenses N deep · M light · K n/a (evidenced) of 26` (pinned), and
meta.routing_decision (lens.dispatch_briefs' full per-lens disposition
object) feeds the same panel. Pages stay under the enforced byte budget
even with a large v2 coverage map, and evidence strings are escaped.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CATALOG = os.path.join(ROOT, "lenses", "catalog.json")


def _lenses():
    c = json.load(open(CATALOG))
    return c["lenses"] if isinstance(c, dict) else c


def _v2_map(n_deep=2, n_light=3, evidence_len=1):
    """A FULL-catalog v2 coverage map: first n_deep deep, next n_light
    light, the rest n/a with negative evidence — the shape route v2 /
    dispatch's routing_decision emits ({verdict, score, evidence|
    negative_evidence})."""
    m = {}
    for i, lz in enumerate(_lenses()):
        if i < n_deep:
            m[lz["id"]] = {"verdict": "deep", "score": 0.9,
                           "evidence": [f"glob hit {j} in changed files"
                                        for j in range(evidence_len)]}
        elif i < n_deep + n_light:
            m[lz["id"]] = {"verdict": "light", "score": 0.4,
                           "evidence": [f"weak signal {j}"
                                        for j in range(evidence_len)]}
        else:
            m[lz["id"]] = {"verdict": "n/a", "score": 0.0,
                           "negative_evidence": [
                               f"no {lz['id']} signal: 0 matching files "
                               f"in the diff"][:max(1, evidence_len)]}
    return m


N = len(_lenses())          # 26 — the honest denominator


# ------------------------------------------------ legacy shape: unchanged

class TestLegacyShapeBytePinned(unittest.TestCase):
    """The legacy {id: 'deep'|'sweep'} shape must render byte-identically
    to today — summary line and chip markup pinned as byte regions."""

    def test_legacy_counts_unchanged(self):
        cov = dashboard.lens_coverage({"security": "deep", "qa": "sweep"})
        self.assertEqual(cov["total"], N)
        self.assertEqual(cov["deep"], 1)
        self.assertEqual(cov["sweep"], 1)
        self.assertEqual(cov["skipped"], N - 2)
        self.assertFalse(cov.get("v2"))

    def test_legacy_summary_byte_region_pinned(self):
        html = dashboard.render_lens_coverage({"security": "deep"})
        self.assertIn(
            f'LENS COVERAGE — {N} lenses · 1 deep · 0 sweep · '
            f'{N - 1} did not fire', html)

    def test_legacy_chip_markup_byte_region_pinned(self):
        # the exact chip byte region emitted today for a routed deep lens
        html = dashboard.render_lens_coverage({"security": "deep"})
        name = next(x for x in _lenses() if x["id"] == "security")["name"]
        chip = (
            '<span style="display:inline-flex;align-items:center;gap:5px;'
            'font-size:11.5px;padding:2px 9px;border:1px solid '
            'var(--border);border-radius:12px;margin:0 5px 5px 0;'
            'color:var(--text-danger)">' + dashboard._esc(name) +
            '<span style="font-family:var(--font-mono);font-size:9px">'
            'deep</span></span>')
        self.assertIn(chip, html)

    def test_catalog_mode_unchanged(self):
        html = dashboard.render_lens_coverage(None)
        self.assertIn("LENS CATALOG", html)
        self.assertIn(f"{N} lenses across", html)


# ------------------------------------------------- v2 shape: verdict chips

class TestCoverageMapV2(unittest.TestCase):
    def test_v2_counts(self):
        cov = dashboard.lens_coverage(_v2_map(2, 3))
        self.assertTrue(cov["v2"])
        self.assertEqual(cov["total"], N)
        self.assertEqual(cov["deep"], 2)
        self.assertEqual(cov["light"], 3)
        self.assertEqual(cov["na"], N - 5)
        self.assertEqual(cov["skipped"], 0)

    def test_v2_chips_carry_verdict_words_not_color_only(self):
        html = dashboard.render_lens_coverage(_v2_map(2, 3))
        # every verdict WORD appears in chip text (mono span), so tiers are
        # distinguishable without color
        for word in ("deep", "light", "n/a"):
            self.assertIn(
                '<span style="font-family:var(--font-mono);font-size:9px">'
                f'{word}</span>', html)
        # all catalog lens names render (panel still catalog-derived)
        for lz in _lenses():
            self.assertIn(dashboard._esc(lz["name"]), html, lz["id"])

    def test_v2_summary_line(self):
        html = dashboard.render_lens_coverage(_v2_map(2, 3))
        self.assertIn("LENS COVERAGE", html)
        self.assertIn(
            f'{N} lenses · 2 deep · 3 light · {N - 5} n/a (evidenced)',
            html)

    def test_every_na_lens_shows_its_reason(self):
        m = _v2_map(2, 3)
        html = dashboard.render_lens_coverage(m)
        for lid, d in m.items():
            if d["verdict"] != "n/a":
                continue
            reason = d["negative_evidence"][0]
            # reason visible as inline text AND as a title attribute
            self.assertIn(dashboard._esc(reason), html, lid)
        self.assertIn('title="', html)

    def test_forced_deep_counts_as_deep(self):
        m = _v2_map(2, 3)
        first = next(iter(m))
        m[first] = {"verdict": "deep (forced)", "score": 1.0,
                    "evidence": ["--lens force"]}
        cov = dashboard.lens_coverage(m)
        self.assertEqual(cov["deep"], 2)


# --------------------------------------------------- HEADLINE: both pinned

class TestHeadlinePinned(unittest.TestCase):
    def test_legacy_headline_byte_identical(self):
        h = dashboard.headline_findings(
            [{"severity": "high", "title": "x"}],
            {"title": "rev",
             "lens_coverage": {"security": "deep", "qa": "sweep"}})
        self.assertIn(f" · lenses 1 deep/1 sweep of {N}", h)
        self.assertNotIn("light", h)
        self.assertNotIn("evidenced", h)

    def test_v2_headline_format_pinned(self):
        h = dashboard.headline_findings(
            [{"severity": "high", "title": "x"}],
            {"title": "rev", "lens_coverage": _v2_map(2, 3)})
        self.assertIn(
            f" · lenses 2 deep · 3 light · {N - 5} n/a (evidenced) of {N}",
            h)
        self.assertNotIn("sweep", h)

    def test_v2_headline_via_routing_decision(self):
        # meta.routing_decision alone also drives the honest headline
        h = dashboard.headline_findings(
            [{"severity": "high", "title": "x"}],
            {"title": "rev", "routing_decision": _v2_map(1, 1)})
        self.assertIn(
            f" · lenses 1 deep · 1 light · {N - 2} n/a (evidenced) of {N}",
            h)

    def test_no_coverage_no_segment(self):
        h = dashboard.headline_findings(
            [{"severity": "high", "title": "x"}], {"title": "rev"})
        self.assertNotIn("lenses", h)


# ------------------------------------- meta.routing_decision in the panel

class TestRoutingDecisionMeta(unittest.TestCase):
    def test_render_findings_accepts_routing_decision(self):
        frag = dashboard.render_findings(
            [{"severity": "high", "title": "x"}],
            {"title": "rev", "routing_decision": _v2_map(2, 3)})
        self.assertIn("tp-lens-coverage", frag)
        self.assertIn("n/a (evidenced)", frag)
        # an n/a reason from the decision object reaches the HTML
        self.assertIn("signal: 0 matching files", frag)

    def test_render_findings_absent_means_todays_behavior(self):
        frag = dashboard.render_findings(
            [{"severity": "high", "title": "x"}], {"title": "rev"})
        self.assertNotIn("tp-lens-coverage", frag)

    def test_routing_decision_wins_over_legacy_coverage_in_panel(self):
        frag = dashboard.render_findings(
            [{"severity": "high", "title": "x"}],
            {"title": "rev",
             "lens_coverage": {"security": "deep"},
             "routing_decision": _v2_map(2, 3)})
        self.assertIn("n/a (evidenced)", frag)

    def test_paged_accepts_routing_decision(self):
        pages = dashboard.render_findings_paged(
            [{"severity": "high", "title": "x"}],
            {"title": "rev", "routing_decision": _v2_map(2, 3)})
        blob = " ".join(p["html"] for p in pages)
        self.assertIn("LENS COVERAGE", blob)


# ------------------------------------------- byte budget with a big v2 map

class TestPagedByteBudgetWithLargeV2Map(unittest.TestCase):
    def _big_findings(self, n=60):
        return [{"severity": "high", "domain": "security",
                 "title": f"finding {i} with a longish descriptive title",
                 "scenario": "x" * 200, "fix": "y" * 180}
                for i in range(n)]

    def test_every_page_fits_budget_with_large_v2_coverage(self):
        meta = {"title": "big review",
                "gate": True,
                "gate_buttons": [{"label": "approve", "prompt": "approve",
                                  "primary": True}],
                "lens_coverage": _v2_map(2, 3, evidence_len=40)}
        pages = dashboard.render_findings_paged(self._big_findings(), meta)
        self.assertGreater(len(pages), 1)
        for p in pages:
            self.assertLessEqual(len(p["html"].encode("utf-8")),
                                 dashboard.PAGE_BUDGET, p["title"])
        blob = " ".join(p["html"] for p in pages)
        self.assertIn("LENS COVERAGE", blob)

    def test_single_page_review_with_v2_map_fits_too(self):
        meta = {"title": "small review",
                "routing_decision": _v2_map(2, 3, evidence_len=40)}
        pages = dashboard.render_findings_paged(
            [{"severity": "low", "title": "one"}], meta)
        for p in pages:
            self.assertLessEqual(len(p["html"].encode("utf-8")),
                                 dashboard.PAGE_BUDGET, p["title"])


# --------------------------------------------------------- XSS: evidence

class TestEvidenceEscaped(unittest.TestCase):
    def test_script_in_evidence_is_inert(self):
        m = _v2_map(1, 1)
        payload = "<script>alert(1)</script>"
        lid_deep = next(k for k, v in m.items() if v["verdict"] == "deep")
        lid_na = next(k for k, v in m.items() if v["verdict"] == "n/a")
        m[lid_deep]["evidence"] = [payload]
        m[lid_na]["negative_evidence"] = [payload]
        html = dashboard.render_lens_coverage(m)
        self.assertNotIn("<script", html)
        self.assertIn("&lt;script&gt;", html)

    def test_attribute_breakout_in_evidence_is_inert(self):
        m = _v2_map(1, 0)
        lid_na = next(k for k, v in m.items() if v["verdict"] == "n/a")
        m[lid_na]["negative_evidence"] = [
            '" onmouseover="alert(1)']
        html = dashboard.render_lens_coverage(m)
        self.assertNotIn('" onmouseover=', html)

    def test_script_in_evidence_inert_through_render_findings(self):
        m = _v2_map(1, 1)
        lid_na = next(k for k, v in m.items() if v["verdict"] == "n/a")
        m[lid_na]["negative_evidence"] = ["<script>alert(2)</script>"]
        frag = dashboard.render_findings(
            [{"severity": "low", "title": "x"}],
            {"title": "rev", "routing_decision": m})
        self.assertNotIn("<script>alert(2)</script>", frag)
        self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", frag)


if __name__ == "__main__":
    unittest.main()
