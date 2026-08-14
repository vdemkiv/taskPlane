"""D3 — the coverage panel crashed on a verdict the router has emitted
since v2.4.0.

A real review of ``aws/karpenter-provider-aws#9464`` produced a routing map
containing ``light`` and ``n/a``. The LEGACY ``render_lens_coverage`` path
builds its tier table with only ``deep`` / ``sweep`` / ``—`` and indexes it
directly, at TWO sites (the chip color and the chip word). ``tp-engineering``'s
own skill tells reviewers to pass exactly that map, so a documented input
raised ``KeyError``, exit 5, and printed NO panel at all — the coverage panel
is the deliverable that says which lenses ran, so losing it loses the whole
coverage claim.

The fix is a total lookup defaulting to the didn't-fire entry at both sites.
The direction matters: an unknown tier must render as DIDN'T FIRE, never as
``deep`` — the panel must fail toward LESS claimed coverage, never toward
claiming a pass that never happened.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# rendering constants the legacy panel is built from — asserted against,
# never imported, so a change to either is visible here
DEEP_COLOR = "var(--text-danger)"
MUTED_COLOR = "var(--text-muted)"
SWEEP_COLOR = "var(--text-secondary)"
DIDNT_FIRE_WORD = "—"

# The verdict vocabulary route v2 has emitted since v2.4.0. `deep` and
# `sweep` are the legacy pair the table already knew.
ROUTER_VERDICTS = ("deep", "light", "n/a", "sweep")


def catalog():
    p = os.path.join(ROOT, "lenses", "catalog.json")
    c = json.load(open(p, encoding="utf-8"))
    return c["lenses"] if isinstance(c, dict) else c


CATALOG = catalog()
IDS = [l["id"] for l in CATALOG]
# names as they appear IN the panel: six catalog names carry '&'
# ("Design & UX", "System design & architecture", ...) and the renderer
# escapes them, so a raw-name search would silently find no chip.
NAMES = {l["id"]: dashboard._esc(l.get("name", l["id"])) for l in CATALOG}


def chip(html, name):
    """The one chip fragment for `name`, or '' — the panel is inline HTML,
    so the chip is located the same way a reader's eye would."""
    for frag in html.split('<span style="display:inline-flex'):
        if f">{name}<" in frag:
            return frag
    return ""


class D3_TheLegacyPanelSurvivesEveryRouterVerdict(unittest.TestCase):
    """The crash itself. Each verdict below is one the router really emits
    and the skill really tells reviewers to pass through."""

    def _render(self, verdict):
        return dashboard.render_lens_coverage({"security": verdict})

    def test_a_light_verdict_renders_instead_of_raising(self):
        """The field map's own value. Before the fix this raised
        KeyError: 'light' and the process exited 5 with no output."""
        html = self._render("light")
        self.assertIn("LENS COVERAGE", html)
        self.assertTrue(chip(html, NAMES["security"]))

    def test_an_na_verdict_renders_instead_of_raising(self):
        html = self._render("n/a")
        self.assertIn("LENS COVERAGE", html)
        self.assertTrue(chip(html, NAMES["security"]))

    def test_every_router_verdict_renders(self):
        for verdict in ROUTER_VERDICTS:
            with self.subTest(verdict=verdict):
                html = self._render(verdict)
                self.assertIn("LENS COVERAGE", html)

    def test_a_wholly_unknown_tier_renders(self):
        """Total means total: a verdict this engine has not invented yet
        must still print a panel, not kill the review."""
        html = self._render("deep (forced)")
        self.assertIn("LENS COVERAGE", html)

    def test_a_full_catalog_na_map_renders(self):
        """The shape a fully-swept review produces: every lens n/a. 26
        chances to hit the missing key."""
        html = dashboard.render_lens_coverage({i: "n/a" for i in IDS})
        for lens_id in IDS:
            with self.subTest(lens=lens_id):
                self.assertTrue(chip(html, NAMES[lens_id]),
                                f"no chip rendered for {lens_id}")

    def test_a_full_catalog_light_map_renders(self):
        html = dashboard.render_lens_coverage({i: "light" for i in IDS})
        self.assertIn("LENS COVERAGE", html)
        self.assertEqual(html.count(f">{DIDNT_FIRE_WORD}</span>"), len(IDS),
                         "every unknown tier should render as didn't-fire")


class D3_AnUnknownTierFailsTowardLessCoverage(unittest.TestCase):
    """Direction of the default. Rendering an unrecognized verdict as `deep`
    would turn a rendering bug into a false coverage CLAIM — the panel would
    say a lens got a full pass that never ran."""

    def test_a_light_verdict_never_renders_as_deep(self):
        html = dashboard.render_lens_coverage({"security": "light"})
        sec = chip(html, NAMES["security"])
        self.assertNotIn(DEEP_COLOR, sec,
                         "an unknown tier was painted with the deep colour")
        self.assertNotIn(">deep</span>", sec,
                         "an unknown tier was labelled deep")

    def test_a_light_verdict_renders_as_didnt_fire(self):
        sec = chip(dashboard.render_lens_coverage({"security": "light"}),
                   NAMES["security"])
        self.assertIn(MUTED_COLOR, sec)
        self.assertIn(f">{DIDNT_FIRE_WORD}</span>", sec)

    def test_no_lens_is_ever_painted_deep_by_an_unknown_map(self):
        html = dashboard.render_lens_coverage({i: "light" for i in IDS})
        self.assertNotIn(DEEP_COLOR, html)
        self.assertNotIn(">deep</span>", html)

    def test_the_summary_counts_an_unknown_tier_as_did_not_fire(self):
        """The headline numbers must agree with the chips: nothing deep,
        nothing swept, everything unaccounted for."""
        html = dashboard.render_lens_coverage({i: "light" for i in IDS})
        self.assertIn("0 deep", html)
        self.assertIn("0 sweep", html)
        self.assertIn(f"{len(IDS)} did not fire", html)

    def test_lens_coverage_itself_reports_the_unknown_tier_as_skipped(self):
        cov = dashboard.lens_coverage({i: "light" for i in IDS})
        self.assertEqual(cov["deep"], 0)
        self.assertEqual(cov["sweep"], 0)
        self.assertEqual(cov["skipped"], len(IDS))


class D3_TheKnownTiersAreUnchanged(unittest.TestCase):
    """The default must not swallow the two tiers the table always knew — a
    lookup that returned didn't-fire for everything would also stop
    crashing, and would be a worse bug."""

    MAP = {"security": "deep", "qa": "sweep"}

    def test_a_deep_lens_still_renders_deep(self):
        sec = chip(dashboard.render_lens_coverage(self.MAP),
                   NAMES["security"])
        self.assertIn(DEEP_COLOR, sec)
        self.assertIn(">deep</span>", sec)

    def test_a_sweep_lens_still_renders_sweep(self):
        qa = chip(dashboard.render_lens_coverage(self.MAP), NAMES["qa"])
        self.assertIn(SWEEP_COLOR, qa)
        self.assertIn(">sweep</span>", qa)

    def test_an_absent_lens_still_renders_didnt_fire(self):
        other = chip(dashboard.render_lens_coverage(self.MAP),
                     NAMES["devops"])
        self.assertIn(MUTED_COLOR, other)
        self.assertIn(f">{DIDNT_FIRE_WORD}</span>", other)

    def test_the_catalog_only_panel_still_renders_without_tier_words(self):
        """routed=None is the LENS CATALOG panel: no run, so no tier word on
        any chip."""
        html = dashboard.render_lens_coverage(None)
        self.assertIn("LENS CATALOG", html)
        self.assertNotIn(">deep</span>", html)
        self.assertNotIn(f">{DIDNT_FIRE_WORD}</span>", html)

    def test_the_v2_dict_map_still_takes_the_v2_path(self):
        """The dual-shape contract: a v2 entry map is not the legacy path
        and must keep its own verdict words."""
        html = dashboard.render_lens_coverage(
            {"security": {"verdict": "light", "score": 0.3,
                          "evidence": ["path: hooks/x.py"]}})
        self.assertIn(">light</span>", html)


if __name__ == "__main__":
    unittest.main()
