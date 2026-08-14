"""Phase 2 debt burn-down pins (t6, R-0006 — D-0002 + D-0003).

Pins, hermetically (temp workspaces / temp stores only — never the real
external store):

  * D-0002 fixtures-path discount: classifier, positive/negative/
    mixed-support re-weighting, floors survival, evidence honesty;
  * goldens-only-via-regen discipline: every checked-in golden carries the
    regen.py provenance banner (a hand-edited golden loses it -> finding);
  * D-0003 routed-audit hybrid MEASUREMENT: the frozen corpus, the
    `audit_hybrid_measured` event shape, the adoption bar (>=30% token
    reduction AND zero escaped n/a-lens findings; default DECLINE), and
    determinism — measurement remains isolated while normal EM is selective;
  * the debt burn-down MECHANISM: debt records can be linked to a recorded
    decision and marked resolved (the REAL store flip for D-0002/D-0003
    happens at sign-off — this pins the mechanism, not the flip).
"""
import glob as globmod
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens_signals as ls  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BRIEFS = os.path.join(HERE, "fixtures", "briefs")
CORPUS_PATH = os.path.join(BRIEFS, "audit_hybrid_corpus.json")
EMPTY_GRAPH = {"hub_dependents": 0, "boundary_contracts": [], "modules": []}


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return rel


# --------------------------------------------------- D-0002: the discount

class TestDiscountClassifier(unittest.TestCase):
    def test_positive_cases(self):
        for p in ("fixtures/x.json", "tests/fixtures/a/b.py",
                  "pkg/testdata/in.txt", "goldens/out.json",
                  "a/goldens/b/c.txt", "briefs/route.golden"):
            self.assertTrue(ls.is_fixture_path(p), p)

    def test_negative_cases(self):
        for p in ("src/app.py", "locales/en.json", "tests/test_app.py",
                  "fixture_factory.py", "docs/testdata.md"):
            self.assertFalse(ls.is_fixture_path(p), p)

    def test_discount_factor_is_quarter(self):
        self.assertEqual(ls.FIXTURE_DISCOUNT, 0.25)


class TestDiscountReweighting(unittest.TestCase):
    def _score(self, layout, files, lid="i18n"):
        with tempfile.TemporaryDirectory() as ws:
            for rel, text in layout.items():
                write(ws, rel, text)
            return ls.detect(lid, ls.make_ctx(ws, files, graph=EMPTY_GRAPH))

    LOCALE = '{"hello": "Hello there friend"}'
    APP = "import i18n from 'i18n';\n"

    def test_fixture_only_support_discounted(self):
        r = self._score({"tests/fixtures/locales/en.json": self.LOCALE,
                         "tests/fixtures/app.js": self.APP},
                        ["tests/fixtures/app.js",
                         "tests/fixtures/locales/en.json"])
        self.assertLess(r["score"], ls.DEEP)
        self.assertTrue(r["evidence"])   # re-weighted, never suppressed
        self.assertTrue(all("fixture-path discount x0.25" in e
                            for e in r["evidence"]))

    def test_real_support_full_weight(self):
        r = self._score({"locales/en.json": self.LOCALE, "app.js": self.APP},
                        ["app.js", "locales/en.json"])
        self.assertGreaterEqual(r["score"], ls.DEEP)
        self.assertFalse(any("discount" in e for e in r["evidence"]))

    def test_mixed_support_full_weight(self):
        r = self._score({"locales/en.json": self.LOCALE, "app.js": self.APP,
                         "tests/fixtures/locales/en.json": self.LOCALE},
                        ["app.js", "locales/en.json",
                         "tests/fixtures/locales/en.json"])
        self.assertGreaterEqual(r["score"], ls.DEEP)
        self.assertFalse(any("discount" in e for e in r["evidence"]))

    def test_floors_survive_discount(self):
        # a fixture-only auth-ish diff: security stays >= light via the
        # floor — the discount can never drop a lens below the floors
        with tempfile.TemporaryDirectory() as ws:
            write(ws, "tests/fixtures/auth/login.py", "password = input()\n")
            out = ls.route_verdicts(ws, ["tests/fixtures/auth/login.py"])
            self.assertIn(out["security"]["verdict"], ("light", "deep"))
            self.assertIn("floor", out["security"])
            self.assertIn(out["architecture"]["verdict"], ("light", "deep"))


# ------------------------------------- goldens: only via regen.py, banner

class TestGoldenRegenProvenance(unittest.TestCase):
    """Hand-edited goldens remain a review finding: every golden carries
    the regen provenance banner naming the ONLY documented regen path."""

    def test_every_golden_carries_regen_banner(self):
        goldens = sorted(globmod.glob(os.path.join(BRIEFS, "golden_*.json")))
        self.assertGreaterEqual(len(goldens), 5, goldens)
        for g in goldens:
            with open(g, encoding="utf-8") as f:
                head = f.read(1200)
            self.assertTrue(head.startswith("# GOLDEN"),
                            f"{os.path.basename(g)}: missing GOLDEN banner")
            self.assertIn("taskplane/tests/fixtures/briefs/regen.py", head,
                          f"{os.path.basename(g)}: regen path not named")
            self.assertIn("Regenerate ONLY", head, os.path.basename(g))

    def test_regen_script_is_the_documented_path(self):
        self.assertTrue(os.path.isfile(os.path.join(BRIEFS, "regen.py")))


# ------------------------------- D-0003: hybrid measurement (not adoption)

class TestHybridMeasurement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(CORPUS_PATH, encoding="utf-8") as f:
            cls.corpus = json.load(f)

    def test_frozen_corpus_shape(self):
        self.assertIn("_provenance", self.corpus)
        entries = self.corpus["entries"]
        self.assertGreaterEqual(len(entries), 6)   # small but real corpus
        for e in entries:
            self.assertTrue(e["files"], e["label"])
            for fnd in e.get("findings", []):
                self.assertIn("lens", fnd)
        # replay leg: at least one entry carries real review findings
        self.assertTrue(any(e.get("findings") for e in entries))

    def test_measured_event_shape(self):
        m = ls.measure_audit_hybrid(self.corpus["entries"], workspace=REPO)
        self.assertEqual(m["event"], "audit_hybrid_measured")
        for key in ("tokens_full", "tokens_hybrid", "token_reduction_pct",
                    "escaped_findings", "verdict", "bar", "corpus_size",
                    "rows"):
            self.assertIn(key, m)
        self.assertEqual(m["corpus_size"], len(self.corpus["entries"]))
        self.assertGreater(m["tokens_full"], 0)
        self.assertGreater(m["tokens_hybrid"], 0)
        self.assertIn(m["verdict"], ("adopt", "decline"))
        # the verdict is exactly the bar applied to the measured numbers
        self.assertEqual(m["verdict"],
                         ls.hybrid_verdict(m["token_reduction_pct"],
                                           m["escaped_findings"]))

    def test_measurement_is_deterministic(self):
        a = ls.measure_audit_hybrid(self.corpus["entries"], workspace=REPO)
        b = ls.measure_audit_hybrid(self.corpus["entries"], workspace=REPO)
        self.assertEqual(a, b)

    def test_adoption_bar_default_decline(self):
        # >=30% AND zero escapes -> adopt; anything else -> DECLINE
        self.assertEqual(ls.hybrid_verdict(30.0, 0), "adopt")
        self.assertEqual(ls.hybrid_verdict(45.2, 0), "adopt")
        self.assertEqual(ls.hybrid_verdict(29.99, 0), "decline")
        self.assertEqual(ls.hybrid_verdict(45.2, 1), "decline")
        self.assertEqual(ls.hybrid_verdict(-0.52, 3), "decline")
        self.assertEqual(ls.hybrid_verdict(0.0, 0), "decline")
        self.assertEqual(ls.HYBRID_BAR,
                         {"min_token_reduction_pct": 30.0,
                          "max_escaped_findings": 0})

    def test_verification_brief_covers_every_na_lens(self):
        decision = {"i18n": {"verdict": "n/a",
                             "negative_evidence": ["0 i18n signals"]},
                    "mobile": {"verdict": "n/a",
                               "negative_evidence": ["0 mobile signals"]},
                    "security": {"verdict": "deep", "evidence": ["x"]}}
        prompt = ls.verification_brief_prompt(decision)
        self.assertIn("- i18n: 0 i18n signals", prompt)
        self.assertIn("- mobile: 0 mobile signals", prompt)
        self.assertNotIn("- security:", prompt)
        self.assertIn("READ-ONLY", prompt)

    def test_normal_em_adopts_selective_execution(self):
        """R-0005 removes full-catalog fan-out from normal final EM."""
        with open(os.path.join(REPO, "taskplane", "loop.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('"all" if step == "em" else "routed"', src)
        self.assertIn('routing, breadth = None, "routed"', src)


# ------------------------------ debt burn-down mechanism (flip at sign-off)

class TestDebtBurndownMechanism(unittest.TestCase):
    """The store flip for D-0002/D-0003 happens at sign-off; here we pin
    that the MECHANISM exists: a debt record can be linked to a recorded
    decision and marked resolved — in a throwaway workspace/store."""

    def _mk_ws(self, tmp):
        ws = os.path.join(tmp, "ws")
        os.makedirs(ws)
        return ws

    def _set_store(self, home):
        """Point TASKPLANE_HOME at a throwaway store and RESTORE the previous
        value when the test ends (t9 / R-0011 E2). A bare assignment here
        outlived the test: every later test module in the same process
        inherited a store under a deleted temp dir. conftest.py's
        _env_mutation_guard now fails the module if this is skipped."""
        prior = os.environ.get("TASKPLANE_HOME")
        self.addCleanup(
            lambda: (os.environ.__setitem__("TASKPLANE_HOME", prior)
                     if prior is not None
                     else os.environ.pop("TASKPLANE_HOME", None)))
        os.environ["TASKPLANE_HOME"] = home

    def test_debt_can_be_linked_and_marked_resolved(self):
        import kb
        import requirements as reqs
        with tempfile.TemporaryDirectory() as tmp:
            self._set_store(os.path.join(tmp, "store"))
            ws = self._mk_ws(tmp)
            # mint up to D-0003 (the ids under burn-down are D-0002/D-0003)
            reqs.record_debt(ws, "seed one")
            d2 = reqs.record_debt(ws, "router precision: fixtures inflate",
                                  reason="fixture-only diffs route deep",
                                  follow_up="fixtures-path discount")
            d3 = reqs.record_debt(ws, "audit sweep cost",
                                  reason="cadence bounds it",
                                  follow_up="evaluate routed-audit hybrid")
            self.assertEqual((d2["id"], d3["id"]), ("D-0002", "D-0003"))
            # a decision is recorded CARRYING the link to the debt id
            entry = kb.record_decision(
                ws, "DECLINE routed-audit hybrid (D-0003)",
                decision="decline: bar not met",
                tags=["debt", "D-0003"],
                links={"debt": "D-0003"})
            self.assertEqual(entry["links"]["debt"], "D-0003")
            got = kb.get_decision(ws, entry["id"])
            self.assertIn("D-0003", got.get("tags", []))
            # ... and the debt can be marked resolved (sign-off does this)
            reqs.resolve_debt(ws, "D-0003")
            open_ids = [d["id"] for d in reqs.list_debt(ws, open_only=True)]
            self.assertNotIn("D-0003", open_ids)
            all_ids = {d["id"]: d["status"]
                       for d in reqs.list_debt(ws, open_only=False)}
            self.assertEqual(all_ids["D-0003"], "resolved")
            self.assertEqual(all_ids["D-0002"], "open")   # flips at sign-off
            reqs.resolve_debt(ws, "D-0002")               # mechanism works
            all_ids = {d["id"]: d["status"]
                       for d in reqs.list_debt(ws, open_only=False)}
            self.assertEqual(all_ids["D-0002"], "resolved")


if __name__ == "__main__":
    unittest.main()
