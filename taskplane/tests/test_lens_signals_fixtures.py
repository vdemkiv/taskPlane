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


# ==========================================================================
# t5 / B5 (R-0008 design row 5) — fixture-classifier product-dir guard.
#
# D-0002's discount keys off the PATH alone, so a real product directory
# literally named `fixtures/` was discounted x0.25 — the dangerous direction
# (under-routing). The discount application point now consults a
# graph-informed exemption computed at ctx construction: a fixture-classed
# path whose containing graph MODULE has >=1 dependent keeps FULL weight,
# with the exemption named in the evidence line. The exemption can only
# RESTORE weight, never deepen a discount, and is_fixture_path() itself is
# untouched.
# ==========================================================================


def _b5_ws(tmp):
    """A product module literally named fixtures/ WITH a dependent, and a
    genuine test fixture tree with none — identical file contents."""
    body = ('SECRET_KEY = "s3cr3t"\n'
            'API_PASSWORD = "hunter2"\n\n\n'
            'def authorize(token):\n'
            '    """Check the auth token."""\n'
            '    return token == SECRET_KEY\n')

    def w(rel, txt):
        p = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(txt)

    w("app/fixtures/tokens.py", body)
    w("app/web/server.py",
      "from app.fixtures import tokens\n\n\n"
      "def handler(req):\n    return tokens.authorize(req.get('t'))\n")
    w("tests/fixtures/tokens.py", body)
    return tmp


class TestB5FixtureDirProductGuard(unittest.TestCase):
    PRODUCT = "app/fixtures/tokens.py"
    TESTFIX = "tests/fixtures/tokens.py"
    LENS = "security"

    def setUp(self):
        import shutil
        import tempfile
        import depgraph as dg
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = _b5_ws(self.tmp)
        self.graph = dg.scan(self.ws)

    def test_is_fixture_path_itself_is_unchanged(self):
        self.assertTrue(ls.is_fixture_path(self.PRODUCT))
        self.assertTrue(ls.is_fixture_path(self.TESTFIX))
        self.assertFalse(ls.is_fixture_path("app/web/server.py"))

    def test_product_fixtures_module_with_dependents_keeps_full_weight(self):
        ctx = ls.make_ctx(self.ws, [self.PRODUCT])
        self.assertFalse(ctx.is_discounted(self.PRODUCT))
        r = ls.detect(self.LENS, ctx)
        self.assertGreaterEqual(r["score"], ls.W_CONTENT)
        self.assertFalse(any(ls._DISCOUNT_NOTE in e for e in r["evidence"]),
                         r["evidence"])
        self.assertTrue(any("exemption" in e for e in r["evidence"]),
                        r["evidence"])
        self.assertTrue(any("dependent" in e for e in r["evidence"]),
                        r["evidence"])

    def test_genuine_test_fixture_without_dependents_stays_discounted(self):
        ctx = ls.make_ctx(self.ws, [self.TESTFIX])
        self.assertTrue(ctx.is_discounted(self.TESTFIX))
        r = ls.detect(self.LENS, ctx)
        self.assertLess(r["score"], ls.LIGHT)
        self.assertTrue(any(ls._DISCOUNT_NOTE in e for e in r["evidence"]),
                        r["evidence"])

    def test_exemption_restores_exactly_the_discounted_weight(self):
        prod = ls.detect(self.LENS, ls.make_ctx(self.ws, [self.PRODUCT]))
        test = ls.detect(self.LENS, ls.make_ctx(self.ws, [self.TESTFIX]))
        self.assertAlmostEqual(prod["score"], ls.W_CONTENT, places=4)
        self.assertAlmostEqual(test["score"],
                               ls.W_CONTENT * ls.FIXTURE_DISCOUNT, places=4)

    def test_exception_can_only_restore_never_deepen(self):
        """Every discounted path is still a fixture-class path: the
        exemption strictly SHRINKS the discounted set."""
        files = [self.PRODUCT, self.TESTFIX, "app/web/server.py"]
        ctx = ls.make_ctx(self.ws, files)
        for f in files:
            if ctx.is_discounted(f):
                self.assertTrue(ls.is_fixture_path(f), f)

    def test_exemption_is_computed_at_ctx_construction_from_the_graph(self):
        ctx = ls.make_ctx(self.ws, [self.PRODUCT, self.TESTFIX])
        self.assertIn(self.PRODUCT, ctx.fixture_exempt)
        self.assertNotIn(self.TESTFIX, ctx.fixture_exempt)
        # an empty graph payload exempts nothing (no dependents known)
        plain = ls.Ctx(self.ws, [self.PRODUCT], None, dict(EMPTY_GRAPH), None)
        self.assertEqual(plain.fixture_exempt, {})
        self.assertTrue(plain.is_discounted(self.PRODUCT))

    def test_positive_case_needs_a_DEPENDENCY_edge_not_just_any_edge(self):
        """The positive half must earn its exemption on a real `imports`
        edge — swapping that edge's kind for a structural one takes the
        exemption away. Pins WHY app/fixtures/ is exempt."""
        import depgraph as dg
        real = ls._graph_payload(self.ws, [self.PRODUCT])
        self.assertGreaterEqual(real["module_dependents"]["fixtures"], 1)
        kinds = {e["kind"] for e in self.graph["edges"]
                 if e["to"] == "fixtures"}
        self.assertTrue(kinds <= dg.DEPENDENCY_EDGE_KINDS, kinds)
        self.assertIn("imports", kinds)


# ==========================================================================
# t5 FIX — the case the first cut of B5 got wrong.
#
# The exemption's premise is "nothing depends on a test-fixture tree". Two
# things made that premise FALSE on this very repository, and both are
# pinned here in isolation:
#
#   guard 1 (edge KIND)        `module_dependents` counted every incoming
#                              edge, including the STRUCTURAL `defined_in`
#                              edge that a docker-compose.yml emits about
#                              its OWN module. A fixture tree containing a
#                              compose file therefore vouched for itself.
#
#   guard 2 (GRANULARITY)      depgraph.module_of() yields an id of a few
#                              segments (`taskplane/tests`) while
#                              is_fixture_path classifies the FULL path, so
#                              an ancestor module's dependents spoke for an
#                              arbitrarily deep fixture subtree below it.
#
# Regression symptom on the real repo: the D-0002 scenario (a fixture-only
# i18n diff) went i18n n/a (0.1625) -> light (0.425), the evidence line
# claimed a checked-in fixture was "real product code, not a test fixture",
# and the extra weight displaced architecture/testability out of the cap-8
# deep band on a real commit.
# ==========================================================================


def _fixture_tree_with_incoming_edges(tmp):
    """A workspace whose fixture trees DO have incoming graph edges.

      harness/tests/fixtures/**   module `harness/tests` — NOT fixture-
                                  classed, and it has BOTH structural
                                  (defined_in, from a compose file living
                                  INSIDE the fixture tree) and GENUINE
                                  (`imports`, from consumer/) dependents.
                                  Guard 2 must keep it discounted.
      app/fixtures/**             module `fixtures` — IS fixture-classed,
                                  and its only incoming edge is the
                                  structural defined_in its own compose
                                  file emits. Guard 1 must keep it
                                  discounted.
    """
    def w(rel, txt):
        p = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(txt)

    compose = ("services:\n  api:\n    image: nginx\n"
               "    depends_on:\n      - queue\n  queue:\n    image: redis\n")
    # fixture tree nested BELOW a 2-segment module id (the repo's own shape)
    w("harness/tests/fixtures/detectors/arch/positive/docker-compose.yml",
      compose)
    w("harness/tests/fixtures/detectors/arch/positive/locales/en.json",
      '{"greeting": "Hello there my friend", '
      '"bye": "See you later my friend"}\n')
    w("harness/__init__.py", "")
    w("harness/tests/__init__.py", "")
    # a GENUINE dependent of harness/tests, so guard 1 alone would not save us
    w("consumer/use.py",
      "import harness.tests\n\n\ndef go():\n    return harness.tests\n")
    # a fixture-classed MODULE whose only incoming edge is structural
    w("app/fixtures/compose_only.py", 'SECRET_KEY = "s3cr3t"\n')
    w("app/fixtures/docker-compose.yml",
      "services:\n  worker:\n    image: busybox\n")
    return tmp


class TestB5FixtureTreeWithIncomingEdgesStaysDiscounted(unittest.TestCase):
    """The negative half of R-0008 B5 for the case that actually breaks:
    a genuine fixture tree whose graph module HAS incoming edges."""

    DEEP_FIX = ("harness/tests/fixtures/detectors/arch/positive/"
                "locales/en.json")
    STRUCT_FIX = "app/fixtures/compose_only.py"

    def setUp(self):
        import shutil
        import tempfile
        import depgraph as dg
        self.dg = dg
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = _fixture_tree_with_incoming_edges(self.tmp)
        self.graph = dg.scan(self.ws)

    # ---- the premise the test rests on: these modules DO have edges
    def test_setup_really_has_incoming_edges(self):
        incoming = {}
        for e in self.graph["edges"]:
            incoming.setdefault(e["to"], set()).add(e["kind"])
        self.assertEqual(incoming.get("harness/tests"),
                         {"defined_in", "imports"}, incoming)
        self.assertEqual(incoming.get("fixtures"), {"defined_in"}, incoming)

    # ---- guard 1: only DEPENDENCY kinds count
    def test_structural_edges_do_not_count_as_dependents(self):
        p = ls._graph_payload(self.ws, [self.STRUCT_FIX, self.DEEP_FIX])
        self.assertEqual(p["module_dependents"]["fixtures"], 0,
                         "a defined_in edge must not count as a dependent")
        # harness/tests keeps its ONE genuine imports dependent
        self.assertEqual(p["module_dependents"]["harness/tests"], 1)
        # hub_dependents is the pre-existing signal and still counts all
        # incoming edges — narrowing it would drop graph weight lenses earn
        self.assertGreaterEqual(p["hub_dependents"], 3)

    def test_fixture_classed_module_with_only_structural_edges_discounted(
            self):
        ctx = ls.make_ctx(self.ws, [self.STRUCT_FIX])
        self.assertTrue(ls.is_fixture_path(self.STRUCT_FIX))
        self.assertTrue(ls._module_is_fixture_classed("fixtures"))
        self.assertEqual(ctx.fixture_exempt, {})
        self.assertTrue(ctx.is_discounted(self.STRUCT_FIX))
        r = ls.detect("security", ctx)
        self.assertAlmostEqual(r["score"],
                               ls.W_CONTENT * ls.FIXTURE_DISCOUNT, places=4)
        self.assertTrue(any(ls._DISCOUNT_NOTE in e for e in r["evidence"]),
                        r["evidence"])

    # ---- guard 2: an ancestor module id may not speak for a nested tree
    def test_deep_fixture_tree_under_a_depended_on_module_stays_discounted(
            self):
        self.assertEqual(self.dg.module_of(self.DEEP_FIX), "harness/tests")
        self.assertFalse(ls._module_is_fixture_classed("harness/tests"))
        p = ls._graph_payload(self.ws, [self.DEEP_FIX])
        self.assertGreaterEqual(p["module_dependents"]["harness/tests"], 1,
                                "guard 2 must carry this case alone")
        ctx = ls.make_ctx(self.ws, [self.DEEP_FIX])
        self.assertEqual(ctx.fixture_exempt, {})
        self.assertTrue(ctx.is_discounted(self.DEEP_FIX))
        r = ls.detect("i18n", ctx)
        self.assertTrue(r["evidence"])
        self.assertTrue(any(ls._DISCOUNT_NOTE in e for e in r["evidence"]),
                        r["evidence"])
        self.assertFalse(any("exemption" in e for e in r["evidence"]),
                         r["evidence"])

    def test_discount_is_exactly_x025_not_merely_absent(self):
        """×0.25, pinned as a ratio against the same content at a
        non-fixture path — 'still discounted' must mean the full discount."""
        real = os.path.join(self.tmp, "prod", "locales")
        os.makedirs(real, exist_ok=True)
        src = os.path.join(self.ws, self.DEEP_FIX)
        with open(src) as f:
            body = f.read()
        with open(os.path.join(real, "en.json"), "w") as f:
            f.write(body)
        plain = ls.detect("i18n", ls.make_ctx(self.ws,
                                              ["prod/locales/en.json"]))
        disc = ls.detect("i18n", ls.make_ctx(self.ws, [self.DEEP_FIX]))
        self.assertGreater(plain["score"], 0)
        self.assertAlmostEqual(disc["score"],
                               plain["score"] * ls.FIXTURE_DISCOUNT,
                               places=4)

    # ---- cached component maps must agree with live routing
    def test_decompose_graph_payload_mirrors_the_kind_filter(self):
        import decompose
        for mod in ("fixtures", "harness/tests"):
            live = ls._graph_payload(self.ws, [self.STRUCT_FIX
                                               if mod == "fixtures"
                                               else self.DEEP_FIX])
            cached = decompose._graph_payload(self.graph, mod)
            self.assertEqual(cached["module_dependents"][mod],
                             live["module_dependents"][mod],
                             f"cached vs live dependent count for {mod}")
        self.assertEqual(
            decompose._graph_payload(self.graph, "fixtures"
                                     )["module_dependents"]["fixtures"], 0)


class TestB5RealRepoFixtureCorpusStaysDiscounted(unittest.TestCase):
    """The regression on real data: this repository's own fixture corpus.
    Every fixture-classed tracked path collapses to a module id that is NOT
    fixture-classed, so no dependent count — real or fabricated — can
    exempt it."""

    REPO = os.path.dirname(os.path.dirname(HERE))
    D0002_DIFF = [
        "taskplane/tests/fixtures/detectors/i18n/positive/locales/en.json",
        "taskplane/tests/fixtures/detectors/i18n/positive/src/app.js",
    ]

    def test_no_fixture_classed_repo_path_has_a_fixture_classed_module(self):
        import depgraph as dg
        offenders = []
        for dirpath, dirs, names in os.walk(FIXROOT):
            dirs.sort()
            for n in sorted(names):
                rel = os.path.relpath(os.path.join(dirpath, n),
                                      self.REPO).replace(os.sep, "/")
                if (ls.is_fixture_path(rel)
                        and ls._module_is_fixture_classed(dg.module_of(rel))):
                    offenders.append(rel)
        self.assertEqual(offenders, [],
                         "fixture paths whose MODULE is fixture-classed "
                         "would be exemptible: %s" % offenders[:5])

    def test_d0002_diff_stays_na_even_with_dependents_on_its_module(self):
        """The exact scenario that regressed. `taskplane/tests` is handed a
        generous dependent count and the discount must still stand."""
        graph = {"hub_dependents": 9, "boundary_contracts": [],
                 "modules": ["taskplane/tests", "src"],
                 "module_dependents": {"taskplane/tests": 9, "src": 9}}
        ctx = ls.make_ctx(self.REPO, self.D0002_DIFF, graph=graph)
        self.assertEqual(ctx.fixture_exempt, {})
        r = ls.detect("i18n", ctx)
        self.assertEqual(ls.verdict_for_score(r["score"]), "n/a",
                         f"D-0002 repealed: {r}")
        self.assertTrue(any(ls._DISCOUNT_NOTE in e for e in r["evidence"]),
                        r["evidence"])
        self.assertFalse(any("real product code" in e
                             for e in r["evidence"]), r["evidence"])


class TestB5Guard3AFixtureCannotVouchForAFixture(unittest.TestCase):
    """Guard 3 (EM, v3 phase 3). Guards 1 and 2 asked what KIND of edge and
    at what GRANULARITY; neither asked WHO is vouching. So one test tree
    importing another exempted the second, and a fixture-only diff kept full
    weight — the exact D-0002 routing inflation the discount prevents.

    The reviewer's verified repro is the fixture: fixtures/core/locale.py
    imported by testdata/core/use.py, both fixture-classed."""

    FIX = "fixtures/core/locale.py"

    def _ws(self, consumer_dir):
        import shutil
        import tempfile
        import depgraph as dg
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        def w(rel, txt):
            path = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(txt)

        w("fixtures/__init__.py", "")
        w("fixtures/core/__init__.py", "")
        w("fixtures/core/locale.py", 'MSG = "Hello there my friend"\n')
        w(consumer_dir + "/__init__.py", "")
        w(consumer_dir + "/use.py",
          "from fixtures.core.locale import MSG\n\n\ndef go():\n"
          "    return MSG\n")
        dg.scan(tmp)
        return tmp

    def test_a_fixture_classed_dependent_does_not_grant_the_exemption(self):
        ws = self._ws("testdata/core")
        payload = ls._graph_payload(ws, [self.FIX])
        self.assertEqual(payload["module_dependents"].get("fixtures/core", 0), 0,
                         "a fixture cannot be its own witness")
        self.assertEqual(ls._fixture_exemptions([self.FIX], payload), {})

    def test_a_real_product_dependent_still_grants_the_exemption(self):
        """The complement — guard 3 must not swallow the case B5 exists for.
        Real product code depending on a fixture-NAMED directory is still
        evidence that the directory is real product code."""
        ws = self._ws("src/app")
        payload = ls._graph_payload(ws, [self.FIX])
        self.assertGreaterEqual(
            payload["module_dependents"].get("fixtures/core", 0), 1,
            "a genuine product dependent must still count")
        self.assertIn(self.FIX, ls._fixture_exemptions([self.FIX], payload))

    def test_the_cached_component_path_mirrors_guard_3(self):
        """decompose._graph_payload must apply guard 3 too — a cached map
        granting an exemption the live route refuses is exactly the drift
        the mirror exists to prevent."""
        import decompose
        import depgraph as dg
        ws = self._ws("testdata/core")
        cached = decompose._graph_payload(dg.load(ws), "fixtures/core")
        self.assertEqual(cached["module_dependents"].get("fixtures/core", 0), 0)

    def test_the_cached_path_still_counts_a_real_dependent(self):
        import decompose
        import depgraph as dg
        ws = self._ws("src/app")
        cached = decompose._graph_payload(dg.load(ws), "fixtures/core")
        self.assertGreaterEqual(
            cached["module_dependents"].get("fixtures/core", 0), 1)


if __name__ == "__main__":
    unittest.main()
