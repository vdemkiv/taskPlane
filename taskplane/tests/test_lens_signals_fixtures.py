"""Per-detector fixture discipline (t1, R-0001 acceptance: every detector has
at least one positive AND one negative fixture).

Layout: taskplane/tests/fixtures/detectors/<lens_id>/positive/** and
.../negative/** — tiny synthetic file trees. The completeness test asserts
EVERY catalog lens id has BOTH dirs; the behavior tests assert the positive
tree fires (score >= LIGHT) and the negative tree yields n/a WITH non-empty
machine-generated negative evidence.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens_signals as ls  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXROOT = os.path.join(HERE, "fixtures", "detectors")
CAT = ls.load_catalog()
CATALOG_IDS = sorted(l["id"] for l in CAT["lenses"])
EMPTY_GRAPH = {"hub_dependents": 0, "boundary_contracts": [], "modules": []}


def tree_files(root):
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            out.append(os.path.relpath(os.path.join(dirpath, n),
                                       root).replace(os.sep, "/"))
    return out


def ctx_for(root):
    return ls.make_ctx(root, tree_files(root), graph=EMPTY_GRAPH)


class TestFixtureCompleteness(unittest.TestCase):
    def test_every_catalog_lens_has_positive_and_negative_fixture(self):
        missing = []
        for lid in CATALOG_IDS:
            for kind in ("positive", "negative"):
                d = os.path.join(FIXROOT, lid, kind)
                if not os.path.isdir(d) or not tree_files(d):
                    missing.append(f"{lid}/{kind}")
        self.assertEqual(missing, [],
                         "missing/empty detector fixture dirs: %s" % missing)

    def test_no_stray_fixture_dirs(self):
        strays = [d for d in sorted(os.listdir(FIXROOT))
                  if os.path.isdir(os.path.join(FIXROOT, d))
                  and d not in CATALOG_IDS]
        self.assertEqual(strays, [])

    def test_fixture_files_not_collectible_by_pytest(self):
        # fixture trees must never leak into test collection
        bad = []
        for lid in CATALOG_IDS:
            for rel in tree_files(os.path.join(FIXROOT, lid)):
                base = os.path.basename(rel)
                if base.endswith(".py") and (base.startswith("test_")
                                             or base.endswith("_test.py")):
                    bad.append(f"{lid}/{rel}")
        self.assertEqual(bad, [])


class TestDetectorFixtures(unittest.TestCase):
    def test_positive_fixture_fires_for_every_detector(self):
        failures = []
        for lid in CATALOG_IDS:
            root = os.path.join(FIXROOT, lid, "positive")
            r = ls.detect(lid, ctx_for(root))
            if r["score"] < ls.LIGHT:
                failures.append(f"{lid}: score {r['score']} "
                                f"(evidence={r['evidence']})")
            elif not r["evidence"]:
                failures.append(f"{lid}: fired without evidence")
        self.assertEqual(failures, [],
                         "positive fixtures that did not fire:\n"
                         + "\n".join(failures))

    def test_negative_fixture_yields_na_with_negative_evidence(self):
        failures = []
        for lid in CATALOG_IDS:
            root = os.path.join(FIXROOT, lid, "negative")
            r = ls.detect(lid, ctx_for(root))
            v = ls.verdict_for_score(r["score"])
            if v != "n/a":
                failures.append(f"{lid}: verdict {v} score {r['score']} "
                                f"(evidence={r['evidence']})")
            elif not r["negative_evidence"]:
                failures.append(f"{lid}: n/a without negative evidence")
        self.assertEqual(failures, [],
                         "negative fixtures that were not clean n/a:\n"
                         + "\n".join(failures))

    def test_negative_evidence_is_machine_generated_and_specific(self):
        for lid in CATALOG_IDS:
            root = os.path.join(FIXROOT, lid, "negative")
            r = ls.detect(lid, ctx_for(root))
            joined = " ".join(r["negative_evidence"])
            self.assertIn(f"0 {lid} signals", joined, lid)
            self.assertIn("no ", joined, lid)

    def test_i18n_positive_and_negative_specifics(self):
        pos = ls.detect("i18n", ctx_for(os.path.join(FIXROOT, "i18n",
                                                     "positive")))
        self.assertGreaterEqual(pos["score"], ls.LIGHT)
        self.assertTrue(any("locale" in e or "i18n" in e
                            for e in pos["evidence"]))
        neg = ls.detect("i18n", ctx_for(os.path.join(FIXROOT, "i18n",
                                                     "negative")))
        self.assertEqual(ls.verdict_for_score(neg["score"]), "n/a")
        self.assertTrue(any("no locale files" in e
                            for e in neg["negative_evidence"]))


class TestD0002InflationCaseClosed(unittest.TestCase):
    """R-0006 / D-0002: the ACTUAL repo fixture paths that inflated
    i18n/mobile to deep (a diff touching only this repo's checked-in
    detector fixtures) must no longer route those lenses deep — while the
    real-surface positive fixture (paths WITHOUT fixture segments, same
    contents) still routes i18n deep, unchanged."""

    REPO = os.path.dirname(os.path.dirname(HERE))
    FIXTURE_ONLY_DIFF = [
        "taskplane/tests/fixtures/detectors/i18n/positive/locales/en.json",
        "taskplane/tests/fixtures/detectors/i18n/positive/src/app.js",
        "taskplane/tests/fixtures/detectors/mobile/positive/android/"
        "AndroidManifest.xml",
    ]

    def setUp(self):
        for rel in self.FIXTURE_ONLY_DIFF:   # the case must stay real
            self.assertTrue(os.path.isfile(os.path.join(self.REPO, rel)),
                            f"repo fixture moved: {rel}")

    def test_fixture_only_diff_no_longer_routes_i18n_mobile_deep(self):
        ctx = ls.make_ctx(self.REPO, self.FIXTURE_ONLY_DIFF,
                          graph=EMPTY_GRAPH)
        for lid in ("i18n", "mobile"):
            r = ls.detect(lid, ctx)
            self.assertLess(r["score"], ls.DEEP,
                            f"{lid} still deep on a fixture-only diff: {r}")
            # re-weighted, never suppressed: hits remain, discount named
            self.assertTrue(r["evidence"], lid)
            self.assertTrue(any("fixture-path discount x0.25" in e
                                for e in r["evidence"]), r["evidence"])

    def test_real_locale_diff_still_routes_i18n_deep(self):
        # positive fixture unchanged: same contents at non-fixture paths
        r = ls.detect("i18n", ctx_for(os.path.join(FIXROOT, "i18n",
                                                   "positive")))
        self.assertGreaterEqual(r["score"], ls.DEEP)
        self.assertEqual(ls.verdict_for_score(r["score"]), "deep")
        self.assertFalse(any("discount" in e for e in r["evidence"]))


if __name__ == "__main__":
    unittest.main()
