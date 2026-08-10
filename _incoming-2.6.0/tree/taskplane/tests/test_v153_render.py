"""v1.5.3 — the render-reliability contract for inline dashboards.

A dashboard's data is too valuable to depend on one big widget that might be
skipped. Three guarantees, tested here:
  1. a never-skippable plain-text headline on every dashboard command;
  2. paged output — ordered, self-contained fragments each <= PAGE_BUDGET,
     split by meaning, so nothing is too big to render;
  3. small reviews still return a single page (no behavior change).
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TPPY = os.path.join(ROOT, "taskplane", "tp.py")


def _many(n_high=15, n_med=30, n_low=25):
    findings = []
    for i in range(n_high):
        findings.append({"severity": "high", "domain": "security",
                         "file": f"a/b{i}.py", "line": i,
                         "title": f"high finding number {i} with a longish title",
                         "scenario": "x" * 200, "fix": "y" * 180})
    for i in range(n_med):
        findings.append({"severity": "med", "domain": "arch",
                         "title": f"medium finding {i} " + "z" * 40})
    for i in range(n_low):
        findings.append({"severity": "low", "domain": "docs",
                         "title": f"low finding {i} " + "w" * 40})
    return findings


class TestHeadline(unittest.TestCase):
    def test_headline_carries_counts(self):
        h = dashboard.headline_findings(
            _many(3, 4, 5), {"title": "rev", "tests": "10 passed"})
        self.assertIn("3 high", h)
        self.assertIn("4 med", h)
        self.assertIn("5 low", h)
        self.assertIn("10 passed", h)

    def test_headline_includes_recommendation(self):
        h = dashboard.headline_findings(
            [], {"headline": "ship it"})
        self.assertIn("ship it", h)


class TestPaging(unittest.TestCase):
    def test_small_review_is_single_page(self):
        pages = dashboard.render_findings_paged(
            [{"severity": "high", "title": "one"}], {"title": "t"})
        self.assertEqual(len(pages), 1)

    def test_large_review_splits_and_respects_budget(self):
        pages = dashboard.render_findings_paged(_many(), {"title": "big"})
        self.assertGreater(len(pages), 1)
        for p in pages:
            self.assertLessEqual(len(p["html"]), dashboard.PAGE_BUDGET,
                                 f"page '{p['title']}' exceeds budget")
            self.assertIn("title", p)
            self.assertIn("html", p)

    def test_pages_are_ordered_summary_then_severities(self):
        pages = dashboard.render_findings_paged(_many(), {"title": "big"})
        self.assertIn("summary", pages[0]["title"])
        titles = " ".join(p["title"] for p in pages)
        self.assertLess(titles.index("high"), titles.index("medium"))
        self.assertLess(titles.index("medium"), titles.index("low"))
        # every page numbered i/n
        for p in pages:
            self.assertRegex(p["title"], r"\d+/\d+$")

    def test_every_finding_appears_somewhere(self):
        f = _many(15, 30, 25)
        pages = dashboard.render_findings_paged(f, {"title": "big"})
        blob = " ".join(p["html"] for p in pages)
        # a sampling of titles from each tier must be present
        for probe in ("high finding number 14", "medium finding 29",
                      "low finding 24"):
            self.assertIn(probe, blob)


class TestCliContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fj = os.path.join(self.tmp, "findings.json")
        json.dump({"meta": {"title": "rev", "tests": "5 passed"},
                   "findings": _many()}, open(self.fj, "w"))

    def test_findings_paged_prints_headline_and_pages(self):
        r = subprocess.run([sys.executable, TPPY, "findings", "--paged",
                            "--file", self.fj],
                           capture_output=True, text=True,
                           env={**os.environ})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("HEADLINE: "))
        payload = json.loads(r.stdout.split("\n", 1)[1])
        self.assertIn("pages", payload)
        self.assertGreater(len(payload["pages"]), 1)
        self.assertIn("PER PAGE", payload["render"])

    def test_findings_default_still_prints_headline_then_fragment(self):
        r = subprocess.run([sys.executable, TPPY, "findings",
                            "--file", self.fj],
                           capture_output=True, text=True,
                           env={**os.environ})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("HEADLINE: "))
        self.assertIn("<", r.stdout)     # a fragment followed


class TestLoopHeadline(unittest.TestCase):
    def test_headline_loop_no_loop(self):
        tmp = tempfile.mkdtemp()
        ws = os.path.join(tmp, "ws")
        os.makedirs(ws)
        subprocess.run(["git", "init", "-q"], cwd=ws)
        self.assertIn("no active loop", dashboard.headline_loop(ws))


class TestSkillContract(unittest.TestCase):
    def test_skills_document_the_render_contract(self):
        eng = open(os.path.join(ROOT, "skills", "tp-engineering",
                                "SKILL.md")).read()
        self.assertIn("--paged", eng)
        self.assertIn("per page", eng.lower())
        self.assertIn("HEADLINE", eng)



class TestOnboardHeadline(unittest.TestCase):
    def test_onboard_render_prints_headline_first(self):
        import subprocess, tempfile
        ws = tempfile.mkdtemp(prefix="tp-onb-hl-")
        tppy = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "tp.py")
        p = subprocess.run([sys.executable, tppy, "onboard",
                            "--workspace", ws],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        first = p.stdout.splitlines()[0]
        self.assertTrue(first.startswith("HEADLINE: "), first)
        self.assertIn("prerequisites ready", first)
        self.assertIn("next:", first)

    def test_onboard_headline_reports_codex_host(self):
        import dashboard as d
        h = d.headline_onboarding(
            {"checks": [{"ok": True}, {"ok": False}],
             "next_action": "init_git", "host": "codex"})
        self.assertIn("1/2", h)
        self.assertIn("git init", h)
        self.assertIn("host: codex", h)


if __name__ == "__main__":
    unittest.main()
