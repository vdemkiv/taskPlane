"""Behavioral contract for the lens applicability engine (t1, R-0001).

Covers: detector interface shape, registry completeness vs the catalog,
determinism, content-scan bounds, verdict thresholds, fail-closed n/a
(ValueError without negative evidence), budget cap with demotion-never-drop,
security/architecture floors, and the perf budget of route_verdicts.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens_signals as ls  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CAT = ls.load_catalog()
CATALOG_IDS = sorted(l["id"] for l in CAT["lenses"])
EMPTY_GRAPH = {"hub_dependents": 0, "boundary_contracts": [], "modules": []}


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return rel


class TestDetectorInterface(unittest.TestCase):
    def test_registry_covers_every_catalog_lens(self):
        self.assertEqual(sorted(ls.DETECTORS), CATALOG_IDS)

    def test_detect_shape_for_all_lenses(self):
        with tempfile.TemporaryDirectory() as ws:
            rel = write(ws, "src/app.py", "def main():\n    return 1\n")
            ctx = ls.make_ctx(ws, [rel], graph=EMPTY_GRAPH)
            for lid in CATALOG_IDS:
                r = ls.detect(lid, ctx)
                self.assertIsInstance(r, dict, lid)
                self.assertIsInstance(r["score"], float, lid)
                self.assertGreaterEqual(r["score"], 0.0, lid)
                self.assertLessEqual(r["score"], 1.0, lid)
                self.assertIsInstance(r["evidence"], list, lid)
                self.assertIsInstance(r["negative_evidence"], list, lid)
                for e in r["evidence"] + r["negative_evidence"]:
                    self.assertIsInstance(e, str, lid)
                if r["score"] < ls.LIGHT:
                    self.assertTrue(r["negative_evidence"],
                                    f"{lid}: n/a-range score must carry "
                                    "negative evidence")

    def test_unknown_lens_rejected(self):
        with tempfile.TemporaryDirectory() as ws:
            ctx = ls.make_ctx(ws, [], graph=EMPTY_GRAPH)
            with self.assertRaises(ValueError):
                ls.detect("no-such-lens", ctx)


class TestDeterminism(unittest.TestCase):
    def test_two_runs_identical(self):
        with tempfile.TemporaryDirectory() as ws:
            files = [
                write(ws, "src/auth/login.py",
                      "def authenticate(user, password):\n"
                      "    return sign(password, SECRET_KEY)\n"),
                write(ws, "locales/en.json", '{"hi": "Hello there friend"}\n'),
                write(ws, "migrations/001.sql",
                      "ALTER TABLE users ADD COLUMN email TEXT;\n"),
                write(ws, "components/App.tsx",
                      'export const App = () => <div className="a" '
                      'aria-label="x"/>;\n'),
            ]
            a = ls.route_verdicts(ws, files, requirement_text="add auth",
                                  graph=EMPTY_GRAPH)
            b = ls.route_verdicts(ws, files, requirement_text="add auth",
                                  graph=EMPTY_GRAPH)
            self.assertEqual(json.dumps(a, sort_keys=True),
                             json.dumps(b, sort_keys=True))

    def test_file_order_does_not_matter(self):
        with tempfile.TemporaryDirectory() as ws:
            f1 = write(ws, "api/routes.py", '@app.get("/x")\n'
                       "async def x():\n    return 1\n")
            f2 = write(ws, "schema/users.sql", "CREATE TABLE users (id INT);\n")
            a = ls.route_verdicts(ws, [f1, f2], graph=EMPTY_GRAPH)
            b = ls.route_verdicts(ws, [f2, f1], graph=EMPTY_GRAPH)
            self.assertEqual(a, b)


class TestBounds(unittest.TestCase):
    def test_per_file_read_capped_at_64kb(self):
        with tempfile.TemporaryDirectory() as ws:
            rel = write(ws, "big.py", "x = 1\n" * 30000)   # ~180KB
            ctx = ls.make_ctx(ws, [rel], graph=EMPTY_GRAPH)
            texts = dict(ctx.contents())
            self.assertLessEqual(len(texts[rel].encode()), ls.MAX_FILE_BYTES)

    def test_file_count_capped_at_200(self):
        with tempfile.TemporaryDirectory() as ws:
            files = [write(ws, f"src/m{i:03d}.py", "pass\n")
                     for i in range(230)]
            ctx = ls.make_ctx(ws, files, graph=EMPTY_GRAPH)
            self.assertLessEqual(len(ctx.contents()), ls.MAX_FILES)

    def test_missing_files_skipped(self):
        with tempfile.TemporaryDirectory() as ws:
            ctx = ls.make_ctx(ws, ["gone/away.py"], graph=EMPTY_GRAPH)
            self.assertEqual(ctx.contents(), [])


class TestVerdictThresholds(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(ls.verdict_for_score(1.0), "deep")
        self.assertEqual(ls.verdict_for_score(0.6), "deep")
        self.assertEqual(ls.verdict_for_score(0.59), "light")
        self.assertEqual(ls.verdict_for_score(0.2), "light")
        self.assertEqual(ls.verdict_for_score(0.19), "n/a")
        self.assertEqual(ls.verdict_for_score(0.0), "n/a")

    def test_verdicts_map_shape(self):
        with tempfile.TemporaryDirectory() as ws:
            rel = write(ws, "src/auth/login.py",
                        "def authenticate(p):\n    return password_hash(p)\n")
            ctx = ls.make_ctx(ws, [rel], graph=EMPTY_GRAPH)
            v = ls.verdicts(["security", "i18n"], ctx, floors=False)
            self.assertEqual(sorted(v), ["i18n", "security"])
            self.assertIn(v["security"]["verdict"], ("deep", "light"))
            self.assertTrue(v["security"]["evidence"])
            self.assertEqual(v["i18n"]["verdict"], "n/a")
            self.assertTrue(v["i18n"]["negative_evidence"])


class TestFailClosed(unittest.TestCase):
    def test_na_without_negative_evidence_raises(self):
        with tempfile.TemporaryDirectory() as ws:
            ctx = ls.make_ctx(ws, [], graph=EMPTY_GRAPH)
            original = ls.DETECTORS["i18n"]
            ls.DETECTORS["i18n"] = lambda c: {
                "score": 0.0, "evidence": [], "negative_evidence": []}
            try:
                with self.assertRaises(ValueError):
                    ls.verdicts(["i18n"], ctx, floors=False)
            finally:
                ls.DETECTORS["i18n"] = original

    def test_bad_score_rejected(self):
        with tempfile.TemporaryDirectory() as ws:
            ctx = ls.make_ctx(ws, [], graph=EMPTY_GRAPH)
            original = ls.DETECTORS["qa"]
            ls.DETECTORS["qa"] = lambda c: {
                "score": 3.5, "evidence": ["x"], "negative_evidence": []}
            try:
                with self.assertRaises(ValueError):
                    ls.detect("qa", ctx)
            finally:
                ls.DETECTORS["qa"] = original


def fake_map(deep_ids_scores, light_ids=(), na_ids=()):
    m = {}
    for lid, s in deep_ids_scores:
        m[lid] = {"verdict": "deep", "score": s, "evidence": [f"e:{lid}"],
                  "negative_evidence": []}
    for lid in light_ids:
        m[lid] = {"verdict": "light", "score": 0.3, "evidence": [f"e:{lid}"],
                  "negative_evidence": []}
    for lid in na_ids:
        m[lid] = {"verdict": "n/a", "score": 0.0, "evidence": [],
                  "negative_evidence": [f"0 {lid} signals: nothing in scope"]}
    return m


class TestBudget(unittest.TestCase):
    def test_cap_demotes_overflow_never_drops(self):
        deep = [(f"lens{i:02d}", 1.0 - i * 0.02) for i in range(12)]
        m = fake_map(deep, light_ids=["extra"], na_ids=["absent"])
        out = ls.apply_budget(m, cap=8, target=(5, 7))
        self.assertEqual(sorted(out), sorted(m))          # nothing dropped
        deep_out = [l for l in out if out[l]["verdict"] == "deep"]
        self.assertEqual(len(deep_out), 8)
        demoted = [l for l in out if out[l]["verdict"] == "light"
                   and any(e.startswith("budget:") for e in out[l]["evidence"])]
        self.assertEqual(len(demoted), 4)
        # highest scores kept deep
        self.assertIn("lens00", deep_out)
        self.assertNotIn("lens11", deep_out)
        self.assertEqual(out["lens11"]["verdict"], "light")

    def test_under_cap_untouched(self):
        m = fake_map([("a", 0.9), ("b", 0.8)])
        out = ls.apply_budget(m, cap=8)
        self.assertEqual(out["a"]["verdict"], "deep")
        self.assertEqual(out["b"]["verdict"], "deep")

    def test_input_map_not_mutated(self):
        m = fake_map([(f"l{i}", 0.9) for i in range(10)])
        snapshot = json.dumps(m, sort_keys=True)
        ls.apply_budget(m, cap=8)
        self.assertEqual(json.dumps(m, sort_keys=True), snapshot)

    def test_deterministic_tie_break(self):
        m = fake_map([(f"l{i:02d}", 0.7) for i in range(10)])
        a = ls.apply_budget(m, cap=8)
        b = ls.apply_budget(m, cap=8)
        self.assertEqual(a, b)


class TestFloors(unittest.TestCase):
    def _ctx(self, ws, rels):
        return ls.make_ctx(ws, rels, graph=EMPTY_GRAPH)

    def test_security_floor_on_enforcement_diff(self):
        with tempfile.TemporaryDirectory() as ws:
            rel = write(ws, "hooks/pre_tool.py", "def check():\n    pass\n")
            ctx = self._ctx(ws, [rel])
            m = fake_map([], na_ids=["security"])
            out = ls.apply_budget(m, cap=8, ctx=ctx)
            self.assertIn(out["security"]["verdict"], ("light", "deep"))
            self.assertTrue(any("floor" in e
                                for e in out["security"]["evidence"]))

    def test_security_floor_via_taskplane_lite(self):
        with tempfile.TemporaryDirectory() as ws:
            rel = write(ws, "taskplane/taskplane_lite.py", "pass\n")
            ctx = self._ctx(ws, [rel])
            m = fake_map([], na_ids=["security"])
            out = ls.apply_budget(m, cap=8, ctx=ctx)
            self.assertNotEqual(out["security"]["verdict"], "n/a")

    def test_security_floor_via_boundary_contracts(self):
        with tempfile.TemporaryDirectory() as ws:
            ctx = ls.make_ctx(ws, ["notes.txt"], graph={
                "hub_dependents": 0, "modules": [],
                "boundary_contracts": ["contract:lens-brief"]})
            m = fake_map([], na_ids=["security"])
            out = ls.apply_budget(m, cap=8, ctx=ctx)
            self.assertNotEqual(out["security"]["verdict"], "n/a")

    def test_architecture_floor_on_any_code_change(self):
        with tempfile.TemporaryDirectory() as ws:
            rel = write(ws, "src/util.py", "def f():\n    return 2\n")
            ctx = self._ctx(ws, [rel])
            m = fake_map([], na_ids=["architecture"])
            out = ls.apply_budget(m, cap=8, ctx=ctx)
            self.assertIn(out["architecture"]["verdict"], ("light", "deep"))
            self.assertTrue(any("floor" in e
                                for e in out["architecture"]["evidence"]))

    def test_no_floor_on_docs_only_change(self):
        with tempfile.TemporaryDirectory() as ws:
            rel = write(ws, "notes/notes.txt", "grocery list\n")
            ctx = self._ctx(ws, [rel])
            m = fake_map([], na_ids=["architecture", "security"])
            out = ls.apply_budget(m, cap=8, ctx=ctx)
            self.assertEqual(out["architecture"]["verdict"], "n/a")
            self.assertEqual(out["security"]["verdict"], "n/a")

    def test_route_verdicts_applies_floors_after_budget(self):
        with tempfile.TemporaryDirectory() as ws:
            rel = write(ws, "src/tool.py", "def run():\n    return 0\n")
            out = ls.route_verdicts(ws, [rel], graph=EMPTY_GRAPH)
            self.assertEqual(sorted(out), CATALOG_IDS)   # all 26 present
            self.assertIn(out["architecture"]["verdict"], ("light", "deep"))
            deep = [l for l in out if out[l]["verdict"] == "deep"]
            self.assertLessEqual(len(deep), 8)
            for lid, v in out.items():
                if v["verdict"] == "n/a":
                    self.assertTrue(v["negative_evidence"], lid)


class TestRouteVerdictsEntry(unittest.TestCase):
    def test_security_never_na_on_enforcement_diff_end_to_end(self):
        with tempfile.TemporaryDirectory() as ws:
            rel = write(ws, "hooks/gate.py", "def gate():\n    pass\n")
            out = ls.route_verdicts(ws, [rel], graph=EMPTY_GRAPH)
            self.assertNotEqual(out["security"]["verdict"], "n/a")

    def test_perf_budget(self):
        files = []
        for dirpath, dirs, names in os.walk(os.path.join(REPO, "taskplane")):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__",
                                                    "fixtures")]
            for n in sorted(names):
                if n.endswith(".py"):
                    files.append(os.path.relpath(
                        os.path.join(dirpath, n), REPO))
        files = sorted(files)[:60]
        self.assertGreater(len(files), 10)
        t0 = time.perf_counter()
        out = ls.route_verdicts(REPO, files, requirement_text="lens routing",
                                graph=EMPTY_GRAPH)
        elapsed = time.perf_counter() - t0
        self.assertEqual(sorted(out), CATALOG_IDS)
        # design budget <1s; generous CI margin
        self.assertLess(elapsed, 3.0, f"route_verdicts took {elapsed:.2f}s")


class TestCtxReadContainment(unittest.TestCase):
    """EM v3 hardening: a crafted relpath or an out-pointing symlink must
    not let a detector read files outside the workspace."""

    def test_dotdot_traversal_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            open(os.path.join(tmp, "outside.txt"), "w", encoding="utf-8").write("SECRET")
            ctx = ls.Ctx(ws, ["../outside.txt"], "", EMPTY_GRAPH, "review")
            self.assertIsNone(ctx.read("../outside.txt"))

    def test_symlink_escape_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            open(os.path.join(tmp, "outside.txt"), "w", encoding="utf-8").write("SECRET")
            os.symlink(os.path.join(tmp, "outside.txt"),
                       os.path.join(ws, "link.txt"))
            ctx = ls.Ctx(ws, ["link.txt"], "", EMPTY_GRAPH, "review")
            self.assertIsNone(ctx.read("link.txt"))

    def test_normal_read_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(ws)
            open(os.path.join(ws, "a.py"), "w", encoding="utf-8").write("x = 1\n")
            ctx = ls.Ctx(ws, ["a.py"], "", EMPTY_GRAPH, "review")
            self.assertEqual(ctx.read("a.py"), "x = 1\n")


class TestFixturePathClassifier(unittest.TestCase):
    """R-0006 / D-0002: fixture-class = path segment fixtures/testdata/
    goldens at any depth, or a .golden extension."""

    def test_fixture_segments_any_depth(self):
        for p in ("fixtures/a.json", "tests/fixtures/locales/en.json",
                  "a/b/c/testdata/x.py", "goldens/route.json",
                  "deep/goldens/nested/more/x.txt"):
            self.assertTrue(ls.is_fixture_path(p), p)

    def test_golden_extension(self):
        self.assertTrue(ls.is_fixture_path("briefs/dispatch.golden"))
        self.assertTrue(ls.is_fixture_path("a/b/OUT.GOLDEN"))

    def test_real_product_paths_not_fixture_class(self):
        for p in ("src/api/auth.py", "locales/en.json", "tests/app.test.js",
                  "src/fixture_loader.py", "golden_gate/bridge.py",
                  "docs/goldens.md"):
            self.assertFalse(ls.is_fixture_path(p), p)


class TestFixturePathDiscount(unittest.TestCase):
    """R-0006 / D-0002: path/content/density hits whose ONLY support is
    fixture-class files score x0.25 — a re-weight, never a suppression."""

    def _detect(self, root, files, lid="i18n"):
        return ls.detect(lid, ls.make_ctx(root, files, graph=EMPTY_GRAPH))

    def _real_equivalent(self):
        """The same diff with the SAME contents at non-fixture paths."""
        with tempfile.TemporaryDirectory() as ws:
            write(ws, "locales/en.json", '{"hello": "Hello there friend"}')
            write(ws, "app.js", "import i18n from 'i18n';\n")
            return self._detect(ws, ["locales/en.json", "app.js"])

    def test_fixture_only_support_discounted_with_named_evidence(self):
        with tempfile.TemporaryDirectory() as ws:
            write(ws, "tests/fixtures/locales/en.json",
                  '{"hello": "Hello there friend"}')
            write(ws, "tests/fixtures/app.js",
                  "import i18n from 'i18n';\n")
            r = self._detect(ws, ["tests/fixtures/locales/en.json",
                                  "tests/fixtures/app.js"])
        full = self._real_equivalent()
        self.assertLess(r["score"], ls.DEEP)
        self.assertLess(r["score"], full["score"])
        # evidence NEVER suppressed; discount named in the evidence string
        self.assertTrue(r["evidence"])
        self.assertTrue(all("fixture-path discount x0.25" in e
                            for e in r["evidence"]), r["evidence"])

    def test_real_support_keeps_full_weight(self):
        r = self._real_equivalent()
        self.assertGreaterEqual(r["score"], ls.DEEP)
        self.assertFalse(any("discount" in e for e in r["evidence"]))

    def test_mixed_support_keeps_full_weight(self):
        # the same signals supported by BOTH fixture and real files ->
        # full weight (only fixture-ONLY support is discounted)
        with tempfile.TemporaryDirectory() as ws:
            write(ws, "locales/en.json", '{"hello": "Hello there friend"}')
            write(ws, "app.js", "import i18n from 'i18n';\n")
            write(ws, "tests/fixtures/locales/en.json",
                  '{"hello": "Hello there friend"}')
            r = self._detect(ws, ["app.js", "locales/en.json",
                                  "tests/fixtures/locales/en.json"])
            self.assertGreaterEqual(r["score"], ls.DEEP)
            self.assertFalse(any("discount" in e for e in r["evidence"]),
                             r["evidence"])

    def test_discount_never_breaks_na_semantics(self):
        # a discounted-to-n/a lens still carries machine-generated negative
        # evidence (the verdicts() fail-closed guard keeps holding)
        with tempfile.TemporaryDirectory() as ws:
            write(ws, "tests/fixtures/locales/en.json",
                  '{"hello": "Hello there friend"}')
            out = ls.verdicts(
                ["i18n"], ls.make_ctx(ws, ["tests/fixtures/locales/en.json"],
                                      graph=EMPTY_GRAPH))
            e = out["i18n"]
            if e["verdict"] == "n/a":
                self.assertTrue(e["negative_evidence"])
            self.assertTrue(e["evidence"])   # discounted, not suppressed

    def test_floors_survive_discount(self):
        # fixture-only AUTH-ish diff: the security score is discounted but
        # the security floor still promotes security to at least light
        with tempfile.TemporaryDirectory() as ws:
            write(ws, "tests/fixtures/auth/login.py", "password = input()\n")
            out = ls.route_verdicts(ws, ["tests/fixtures/auth/login.py"])
            sec = out["security"]
            self.assertIn(sec["verdict"], ("light", "deep"))
            self.assertIn("floor", sec)


if __name__ == "__main__":
    unittest.main()
