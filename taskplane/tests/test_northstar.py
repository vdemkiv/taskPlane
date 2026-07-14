"""North-star review (v1.0) — the summoned strategic lens.

Covers: the north_star() reader, the render_strategy_note fragment, the
`tp north-star` CLI, the product.md scaffold's Direction line staying
lint-clean, and the catalog cut (the 3 exec advisory lenses are gone)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tl  # noqa: E402
import dashboard  # noqa: E402
import kb  # noqa: E402
import lens as lens_router  # noqa: E402
import tp as tpcli  # noqa: E402

_TP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "tp.py")


def _ctx_product(ws, body):
    ctx = os.path.join(tl.kb_root(ws), "context")
    os.makedirs(ctx, exist_ok=True)
    open(os.path.join(ctx, "product.md"), "w").write(body)


class TestNorthStarReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp(prefix="tp-home-ns-")
        self._prev = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(self.ws)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._prev

    def test_reads_the_direction_line(self):
        _ctx_product(self.ws, "# Product context\n\n"
                     "- **Direction / north star:** a trustworthy governed loop\n"
                     "- **Product:** x\n")
        self.assertEqual(tpcli.north_star(self.ws),
                         "a trustworthy governed loop")

    def test_unfilled_placeholder_reads_as_none(self):
        _ctx_product(self.ws, "- **Direction / north star:** (one sentence — "
                     "the direction every strategic call is judged against)\n")
        self.assertIsNone(tpcli.north_star(self.ws))

    def test_missing_doc_or_line_is_none(self):
        self.assertIsNone(tpcli.north_star(self.ws))          # no doc
        _ctx_product(self.ws, "- **Product:** x\n")           # no line
        self.assertIsNone(tpcli.north_star(self.ws))

    def test_scaffold_direction_line_is_lint_clean(self):
        # the shipped scaffold must not trip kb-lint (pricing/prompt markers)
        _ctx_product(self.ws, tpcli.PRODUCT_MD)
        self.assertEqual(kb.lint(self.ws), [])
        # and the scaffold ships the Direction line
        self.assertIn("Direction / north star", tpcli.PRODUCT_MD)


class TestRenderStrategyNote(unittest.TestCase):
    def _note(self, **over):
        n = {
            "target": "add A/B variants",
            "north_star": "a trustworthy governed loop",
            "alignment": {"verdict": "drift", "note": "power-user branch"},
            "lenses": [
                {"name": "Leverage", "read": "low", "note": "few flows"},
                {"name": "Reversibility", "read": "two-way", "note": "cheap to pull"},
                {"name": "Opportunity cost", "read": "high", "note": "displaces the screen fix"},
                {"name": "Coherence", "read": "med", "note": "second selection concept"},
            ],
            "tension": "breadth vs trust",
            "recommendation": "proceed-with-eyes-open",
            "rationale": "after the screen fix",
        }
        n.update(over)
        return n

    def test_note_contains_all_parts(self):
        f = dashboard.render_strategy_note(self._note())
        for token in ("North-star review", "add A/B variants",
                      "a trustworthy governed loop", "drift", "Leverage",
                      "Reversibility", "Opportunity cost", "Coherence",
                      "breadth vs trust", "proceed-with-eyes-open"):
            self.assertIn(token, f, token)
        self.assertIn('class="sr-only"', f)          # accessible summary
        self.assertIn("advisory", f)                  # never-a-gate note

    def test_note_escapes_html(self):
        f = dashboard.render_strategy_note(
            self._note(target="<script>alert(1)</script>"))
        self.assertNotIn("<script>alert(1)</script>", f)
        self.assertIn("&lt;script&gt;", f)

    def test_missing_north_star_is_flagged_not_crashed(self):
        f = dashboard.render_strategy_note(self._note(north_star=None))
        self.assertIn("no north star set", f)


class TestNorthStarCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp(prefix="tp-home-nscli-")
        self._prev = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(self.ws)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._prev

    def _run(self, *args):
        return subprocess.run([sys.executable, _TP_PY, "north-star", *args],
                              cwd=self.ws, capture_output=True, text=True,
                              env={**os.environ, "TASKPLANE_HOME": self.home})

    def test_prints_north_star_json(self):
        _ctx_product(self.ws, "- **Direction / north star:** ship trust\n")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["north_star"], "ship trust")
        self.assertTrue(out["set"])

    def test_unset_reports_hint(self):
        r = self._run()
        out = json.loads(r.stdout)
        self.assertFalse(out["set"])
        self.assertIsNotNone(out["hint"])

    def test_render_a_note_file(self):
        note = os.path.join(self.tmp, "note.json")
        json.dump({"target": "t", "alignment": {"verdict": "on-course",
                   "note": "n"}, "recommendation": "proceed"},
                  open(note, "w"))
        r = self._run("--render", note)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("North-star review", r.stdout)
        self.assertIn("on course", r.stdout)


class TestCatalogCut(unittest.TestCase):
    def test_advisory_lenses_removed(self):
        cat = lens_router.load_catalog()
        ids = {l["id"] for l in cat["lenses"]}
        for gone in ("tech-strategy", "cost-roi", "business-alignment"):
            self.assertNotIn(gone, ids)
        self.assertEqual(len(cat["lenses"]), 22)
        self.assertFalse(any("Advisory" in (l.get("group") or "")
                             for l in cat["lenses"]))


if __name__ == "__main__":
    unittest.main()
