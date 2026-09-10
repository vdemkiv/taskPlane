"""Stage profiles, applicability signals, routing floors and refusals.

These verify the configured routing contract; historical catalog prose and
pre-refactor byte snapshots are not behavior authority.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from taskplane.tests.lens_fixture import tree_files
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens  # noqa: E402
import lens_signals  # noqa: E402
import path_roles  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXROOT = os.path.join(HERE, "fixtures", "detectors")
I18N_POS = os.path.join(FIXROOT, "i18n", "positive")
I18N_NEG = os.path.join(FIXROOT, "i18n", "negative")

CAT = lens.load_catalog()
ALL_IDS = [l["id"] for l in CAT["lenses"]]


def entry(routing, lid):
    return next(x for x in routing["lenses"] if x["id"] == lid)


def tiers(routing):
    return {x["id"]: x["tier"] for x in routing["lenses"]}


def write_ws(spec):
    """Materialize {relpath: content} into a temp workspace."""
    ws = tempfile.mkdtemp(prefix="tp-lens-v2-")
    for rel, content in spec.items():
        p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(p) or ws, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    return ws


class TestStageProfiles(unittest.TestCase):
    def test_catalog_carries_stage_profiles_data(self):
        # contract:stage-profiles — the key exists, every profile id is a
        # real lens id, and the review profile is the FULL catalog (a final
        # review can never be profile-narrowed).
        sp = CAT["stage_profiles"]
        self.assertEqual(sorted(sp), ["build", "design", "review"])
        for stage_name, ids in sp.items():
            self.assertEqual(sorted(set(ids) - set(ALL_IDS)), [],
                             f"unknown ids in profile {stage_name}")
            self.assertEqual(len(ids), len(set(ids)), stage_name)
        self.assertEqual(sp["review"], ALL_IDS)
        self.assertNotIn("code-quality", sp["design"])
        self.assertIn("security", sp["design"])
        self.assertIn("security", sp["build"])


class TestStageRestriction(unittest.TestCase):
    def test_design_stage_never_yields_code_quality(self):
        ws = write_ws({"src/todo/core.py": "def add(a, b):\n    return a+b\n"})
        try:
            r = lens.route(["src/todo/core.py"], stage="design", catalog=CAT,
                           workspace=ws)
        finally:
            shutil.rmtree(ws)
        cq = entry(r, "code-quality")
        self.assertEqual(cq["tier"], "n/a")
        self.assertTrue(any("stage profile 'design'" in e
                            for e in cq["negative_evidence"]))
        # ALL catalog lenses appear in the output — n/a included.
        self.assertEqual({x["id"] for x in r["lenses"]}, set(ALL_IDS))
        self.assertEqual(r["context"]["stage"], "design")

    def test_profile_membership_is_data_no_code_change(self):
        ws = write_ws({"src/todo/core.py": "def add(a, b):\n    return a+b\n"})
        try:
            cat2 = copy.deepcopy(CAT)
            cat2["stage_profiles"]["design"] = (
                cat2["stage_profiles"]["design"] + ["code-quality"])
            r = lens.route(["src/todo/core.py"], stage="design", catalog=cat2,
                           workspace=ws)
        finally:
            shutil.rmtree(ws)
        # same diff, same code — one data line changed the routing
        self.assertNotEqual(entry(r, "code-quality")["tier"], "n/a")

    def test_unknown_stage_fails_open_to_full_catalog(self):
        ws = write_ws({"src/todo/core.py": "def add(a, b):\n    return a+b\n"})
        try:
            r = lens.route(["src/todo/core.py"], stage="no-such-stage",
                           catalog=CAT, workspace=ws)
        finally:
            shutil.rmtree(ws)
        self.assertEqual(sorted(r["context"]["stage_profile"]),
                         sorted(ALL_IDS))
        self.assertNotEqual(entry(r, "code-quality")["tier"], "n/a")


class TestSignalIntegration(unittest.TestCase):
    def test_i18n_fixture_diff_routes_i18n_sweep(self):
        files = tree_files(I18N_POS)
        r = lens.route(files, stage="review", catalog=CAT, workspace=I18N_POS)
        x = entry(r, "i18n")
        self.assertEqual(x["tier"], "sweep")
        self.assertEqual(x["mode"], "subagent")
        self.assertGreaterEqual(x["score"], lens_signals.DEEP)
        self.assertTrue(any("locale" in e or "i18n" in e
                            for e in x["evidence"]))

    def test_stdlib_diff_routes_i18n_na_with_evidence_in_output(self):
        files = tree_files(I18N_NEG)     # a plain stdlib cli.py
        r = lens.route(files, stage="review", catalog=CAT, workspace=I18N_NEG)
        x = entry(r, "i18n")             # the n/a entry IS in the output
        self.assertEqual(x["tier"], "n/a")
        self.assertEqual(x["mode"], "none")
        joined = " ".join(x["negative_evidence"])
        self.assertIn("0 i18n signals", joined)
        self.assertIn("no locale files", joined)
        # coverage honesty: the reasons of an n/a entry ARE its negative
        # evidence, so any renderer shows why the lens did not run
        self.assertEqual(x["reasons"], x["negative_evidence"])

    def test_v2_reasons_merge_legacy_glob_reasons_with_signal_evidence(self):
        ws = write_ws({"src/auth/login.py":
                       "password = 'x'\nimport subprocess\n"})
        try:
            r = lens.route(["src/auth/login.py"], stage="review", catalog=CAT,
                           workspace=ws)
        finally:
            shutil.rmtree(ws)
        sec = entry(r, "security")
        self.assertEqual(sec["tier"], "sweep")
        # legacy vocabulary (glob/baseline) ...
        self.assertTrue(any(rr.startswith("touches ")
                            for rr in sec["reasons"]), sec["reasons"])
        self.assertIn("baseline (any code change)", sec["reasons"])
        # ... merged with engine evidence in the same reasons list
        self.assertTrue(any(rr.startswith(("path:", "content:", "graph:",
                                           "requirement:"))
                            for rr in sec["reasons"]), sec["reasons"])


BUDGET_WS_SPEC = {
    "src/auth/login.py": "password = 'x'\nimport subprocess\n",
    "src/api/handlers.py": ("@app.get('/x')\ndef handle_x():\n"
                            "    requests.get('http://x')\n"
                            "# transaction rollback\n"
                            "async def f():\n    pass\n"),
    "db/schema.sql": ("CREATE TABLE t (id INT PRIMARY KEY);\n"
                      "ALTER TABLE t ADD COLUMN b INT;\n"
                      "SELECT a FROM t JOIN u ON 1=1 GROUP BY a;\n"),
    "Dockerfile": "FROM python:3\nRUN pip install x\n",
    "web/components/App.tsx": ("<Button className='x' aria-label='y' "
                               "tabIndex={0} />\n"
                               "const [a, setA] = useState(0)\n"),
    "tests/helper_check.py": ("import pytest\n@pytest.fixture\n"
                              "def use(monkeypatch):\n    assert True\n"),
    "locales/en.json": '{"locale": "en"}\n',
}


class TestBudgetAndFloorsThroughRoute(unittest.TestCase):
    def test_automatic_route_projects_signals_to_four_or_five_sweeps(self):
        ws = write_ws(BUDGET_WS_SPEC)
        try:
            r = lens.route(sorted(BUDGET_WS_SPEC), stage="review",
                           catalog=CAT, workspace=ws)
        finally:
            shutil.rmtree(ws)
        t = tiers(r)
        self.assertFalse([lid for lid, tier in t.items() if tier == "deep"])
        sweep = {lid for lid, tier in t.items() if tier == "sweep"}
        self.assertIn(len(sweep), (4, 5))
        self.assertIn("architecture", sweep)
        # Every catalog lens remains dispositioned; the cap never hides rows.
        self.assertEqual(set(t), set(ALL_IDS))
        self.assertEqual(set(t.values()), {"sweep", "n/a"})

    def test_architecture_floor_on_any_code_change(self):
        ws = write_ws({"src/todo/util.py": "def add(a, b):\n    return a+b\n"})
        try:
            r = lens.route(["src/todo/util.py"], stage="review", catalog=CAT,
                           workspace=ws)
        finally:
            shutil.rmtree(ws)
        arch = entry(r, "architecture")
        self.assertEqual(arch["tier"], "sweep")   # never n/a on code
        self.assertIn("floor", arch)
        self.assertIn("floor: architecture promoted", " ".join(arch["evidence"]))

    def test_architecture_floor_survives_stage_profile_exclusion(self):
        # architecture is NOT in the build profile, but the governance floor
        # (>= light on any code change) may never be profile-narrowed away.
        ws = write_ws({"src/todo/util.py": "def add(a, b):\n    return a+b\n"})
        try:
            r = lens.route(["src/todo/util.py"], stage="build", catalog=CAT,
                           workspace=ws)
        finally:
            shutil.rmtree(ws)
        self.assertNotIn("architecture", CAT["stage_profiles"]["build"])
        self.assertEqual(entry(r, "architecture")["tier"], "sweep")

    def test_security_never_na_on_enforcement_touching_diff(self):
        ws = write_ws({"hooks/pretool_gate.txt": "plain text, zero code\n"})
        try:
            r = lens.route(["hooks/pretool_gate.txt"], stage="review",
                           catalog=CAT, workspace=ws)
        finally:
            shutil.rmtree(ws)
        self.assertEqual(entry(r, "security")["tier"], "sweep")

    # ---- the untested trigger, on the STAGE-AWARE path -------------------
    #
    # The regression these pin: the trigger shipped in 2.7.0 verified only
    # through `lens.route(files)` with NO stage — the legacy router, where it
    # appends reason TEXT after applicability has already been decided. On
    # the stage-aware path it moved no verdict at all, so qa stayed n/a on
    # exactly the change it exists for. Every assertion below passes `stage=`
    # explicitly, which is what the original verification omitted.

    def test_qa_routes_on_untested_code_in_stage_aware_review(self):
        ws = write_ws({"src/app.py": "def value():\n    return 1\n"})
        try:
            r = lens.route(["src/app.py"], stage="review", catalog=CAT,
                           workspace=ws)
        finally:
            shutil.rmtree(ws)
        qa = entry(r, "qa")
        self.assertEqual(qa["tier"], "sweep")
        self.assertIn("untested change (code changed, no test file)",
                      qa["reasons"])
        self.assertIn("change shape: code changed with no test file",
                      qa["evidence"])

    def test_the_same_change_with_a_test_does_not_fire_the_trigger(self):
        ws = write_ws({"src/app.py": "def value():\n    return 1\n",
                       "tests/test_app.py": "def test_value():\n    assert 1\n"})
        try:
            r = lens.route(["src/app.py", "tests/test_app.py"],
                           stage="review", catalog=CAT, workspace=ws)
        finally:
            shutil.rmtree(ws)
        self.assertNotIn("change shape: code changed with no test file",
                         entry(r, "qa")["evidence"])

    def test_docs_only_change_keeps_attributable_lens_in_sweep(self):
        ws = write_ws({"docs/guide.md": "# guide\n"})
        try:
            r = lens.route(["docs/guide.md"], stage="review", catalog=CAT,
                           workspace=ws)
        finally:
            shutil.rmtree(ws)
        self.assertFalse([x for x in r["lenses"] if x["tier"] == "deep"])
        writer = entry(r, "tech-writer")
        self.assertEqual(writer["tier"], "sweep")
        self.assertEqual(writer["review_risk_class"], "documentation-only")
        self.assertIn("documentation evidence selected tech-writer",
                      writer["review_risk_reason"])
        self.assertIn("risk-selected review floor", writer["floor"])
        qa = entry(r, "qa")
        self.assertNotIn("change shape: code changed with no test file",
                         qa["evidence"])

    def test_simple_low_risk_signal_is_preserved_without_creating_depth(self):
        ws = write_ws({"src/value.py": "value = 1\n"})
        try:
            r = lens.route(["src/value.py"], stage="review", catalog=CAT,
                           workspace=ws)
        finally:
            shutil.rmtree(ws)
        self.assertFalse([x for x in r["lenses"] if x["tier"] == "deep"])
        risk = [x for x in r["lenses"]
                if x.get("review_risk_class") == "simple-low-risk"]
        self.assertEqual(len(risk), 1)
        self.assertEqual(risk[0]["tier"], "sweep")
        self.assertIn(risk[0]["id"],
                      {"architecture", "code-quality", "security", "qa"})
        self.assertIn("single mapped low-risk code file",
                      risk[0]["review_risk_reason"])
        self.assertIn("risk-selected review floor", risk[0]["floor"])

    def test_substantive_and_risky_changes_never_escape_sweep_depth(self):
        fixtures = (
            {"src/one.py": "value = 1\n", "src/two.py": "value = 2\n"},
            {"src/auth/login.py": "password = request.value\n"},
        )
        for files in fixtures:
            with self.subTest(files=sorted(files)):
                ws = write_ws(files)
                try:
                    r = lens.route(sorted(files), stage="review", catalog=CAT,
                                   workspace=ws)
                finally:
                    shutil.rmtree(ws)
                sweep = [x for x in r["lenses"] if x["tier"] == "sweep"]
                self.assertIn(len(sweep), (4, 5))
                self.assertFalse([x for x in r["lenses"]
                                  if x["tier"] == "deep"])
                self.assertEqual(entry(r, "architecture")["tier"], "sweep")
                risk = [x for x in r["lenses"]
                        if x.get("review_risk_class") == "substantive-risky"]
                self.assertTrue(risk)
                self.assertTrue(all("mandatory review floor" in x["floor"]
                                    for x in risk))

    def test_automatic_selector_uses_complete_catalog_after_stage_signals(self):
        # QA is absent from the build profile, but the approved automatic
        # selector works over the complete catalog and may select its live
        # untested-change signal inside the fixed sweep budget.
        self.assertNotIn("qa", CAT["stage_profiles"]["build"])
        ws = write_ws({"src/app.py": "def value():\n    return 1\n"})
        try:
            r = lens.route(["src/app.py"], stage="build", catalog=CAT,
                           workspace=ws)
        finally:
            shutil.rmtree(ws)
        self.assertEqual(entry(r, "qa")["tier"], "sweep")

    def test_substring_lookalike_filenames_still_route_qa(self):
        # contest.py / latest.py / specification.py / protest/ all contain
        # "test" or "spec" as a SUBSTRING. The old marker list read every one
        # of them as a test file and suppressed the trigger.
        for name in ("src/contest.py", "src/latest.py",
                     "src/specification.py", "src/protest/handler.py"):
            with self.subTest(name=name):
                ws = write_ws({name: "def value():\n    return 1\n"})
                try:
                    r = lens.route([name], stage="review", catalog=CAT,
                                   workspace=ws)
                finally:
                    shutil.rmtree(ws)
                self.assertEqual(entry(r, "qa")["tier"], "sweep",
                              f"{name} was mistaken for a test file")

    def test_test_path_detection_is_segment_and_filename_aware(self):
        code_ext = CAT["code_extensions"]
        for product_path in ("src/contest.py", "src/latest.py",
                             "src/specification.py"):
            with self.subTest(product_path=product_path):
                self.assertTrue(lens._adds_no_test([product_path], code_ext))

        for test_path in ("tests/test_app.py", "src/app.test.ts",
                          "spec/app_spec.rb", "e2e/login.ts",
                          "src/conftest.py"):
            with self.subTest(test_path=test_path):
                self.assertFalse(lens._adds_no_test(
                    ["src/app.py", test_path], code_ext))

    def test_qa_baseline_override_still_forces_tested_code_changes(self):
        code_ext = CAT["code_extensions"]
        files = ["src/app.py", "tests/test_app.py"]
        self.assertFalse(lens._adds_no_test(files, code_ext))
        with mock.patch.dict(os.environ, {"TASKPLANE_QA_BASELINE": "1"}):
            self.assertTrue(lens._adds_no_test(files, code_ext))


class TestTestPathRoles(unittest.TestCase):
    """One shared definition of "this path is a test", in path_roles, so the
    legacy router and the signal engine cannot drift apart again."""

    TESTS = ["tests/app.py", "src/__tests__/a.js", "e2e/flow.spec.ts",
             "cypress/e2e/x.js", "playwright/login.js", "test_loop.py",
             "foo_test.go", "web/Checkout.test.tsx", "FooTest.java",
             "OrderTests.cs", "conftest.py", "spec/models/user_spec.rb",
             "src/testing/helpers.py", "integration-tests/api.py"]
    NOT_TESTS = ["src/contest.py", "src/latest.py", "src/specification.py",
                 "protest/app.py", "src/manifest.json", "locales/it/en.json",
                 "src/app.py", "greatest_hits.py", "src/attest.rb"]

    def test_test_paths(self):
        for p in self.TESTS:
            self.assertTrue(path_roles.is_test_path(p), p)

    def test_non_test_paths(self):
        for p in self.NOT_TESTS:
            self.assertFalse(path_roles.is_test_path(p), p)

    def test_windows_separators_classify_identically(self):
        self.assertTrue(path_roles.is_test_path(r"tests\app.py"))
        self.assertFalse(path_roles.is_test_path(r"src\contest.py"))

    def test_baseline_override_does_not_fire_without_code(self):
        with mock.patch.dict(os.environ, {"TASKPLANE_QA_BASELINE": "1"}):
            self.assertFalse(path_roles.change_adds_no_test(
                ["README.md"], CAT["code_extensions"]))


class TestAutomaticSelectAndSkip(unittest.TestCase):
    def test_only_pins_na_lens_inside_bounded_sweep_without_depth(self):
        files = tree_files(I18N_NEG)
        r = lens.route(files, stage="review", catalog=CAT, workspace=I18N_NEG,
                       only=["i18n"])
        x = entry(r, "i18n")
        self.assertEqual(x["tier"], "sweep")
        self.assertEqual(x["verdict"], "sweep")
        self.assertEqual(x["mode"], "subagent")
        selected = {row["id"] for row in r["lenses"]
                    if row["tier"] == "sweep"}
        self.assertIn("i18n", selected)
        self.assertIn("architecture", selected)
        self.assertIn(len(selected), (4, 5))
        self.assertEqual({row["tier"] for row in r["lenses"]},
                         {"sweep", "n/a"})
        self.assertFalse(any(row["tier"] == "deep" for row in r["lenses"]))

    def test_skip_is_a_visible_evidenced_na(self):
        ws = write_ws({"src/auth/login.py": "password = 'x'\n"})
        try:
            r = lens.route(["src/auth/login.py"], stage="review", catalog=CAT,
                           workspace=ws, skip=["security"])
        finally:
            shutil.rmtree(ws)
        sec = entry(r, "security")
        self.assertEqual(sec["tier"], "n/a")
        self.assertTrue(any("--skip" in e for e in sec["negative_evidence"]))
        selected = {row["id"] for row in r["lenses"]
                    if row["tier"] == "sweep"}
        self.assertNotIn("security", selected)
        self.assertIn("architecture", selected)
        self.assertIn(len(selected), (4, 5))

    def test_skip_architecture_conflict_fails_closed(self):
        r = lens.route(["src/app.py"], stage="review", catalog=CAT,
                       skip=["architecture"])
        self.assertEqual(r["lenses"], [])
        self.assertEqual(r["context"]["status"], "mapper_unavailable")
        self.assertIn("mandatory architecture floor",
                      r["context"]["lens_engine_failed"])

    def test_same_lens_in_only_and_skip_fails_closed(self):
        r = lens.route(["src/app.py"], stage="review", catalog=CAT,
                       only=["security"], skip=["security"])
        self.assertEqual(r["lenses"], [])
        self.assertIn("both select and skip",
                      r["context"]["lens_engine_failed"])

    def test_unknown_and_over_budget_explicit_selection_fail_closed(self):
        cases = (
            (["not-a-lens"], "unknown lenses"),
            (["security", "qa", "testability", "i18n", "dba"],
             "exceeds the 5-lens budget"),
        )
        for only, error in cases:
            with self.subTest(only=only):
                r = lens.route(["src/app.py"], stage="review", catalog=CAT,
                               only=only)
                self.assertEqual(r["lenses"], [])
                self.assertIn(error, r["context"]["lens_engine_failed"])


class TestEngineFailureStopsDispatch(unittest.TestCase):
    def test_engine_exception_emits_mapper_unavailable_and_zero_dispatch(self):
        files = ["src/todo/core.py"]

        def boom(*a, **k):
            raise RuntimeError("engine exploded")

        orig = lens_signals.route_verdicts
        lens_signals.route_verdicts = boom
        ws = tempfile.mkdtemp(prefix="tp-lens-v2-fail-")
        try:
            r = lens.route(files, stage="review", catalog=CAT, workspace=ws)
        finally:
            lens_signals.route_verdicts = orig
            shutil.rmtree(ws)
        # degradation marker present and honest
        self.assertIn("engine exploded", r["context"]["lens_engine_failed"])
        self.assertEqual(r["context"]["status"], "mapper_unavailable")
        self.assertEqual(r["context"]["breadth"], "routed")
        self.assertEqual(r["lenses"], [])


class TestDeterminism(unittest.TestCase):
    def test_two_identical_v2_routes_are_byte_identical(self):
        ws = write_ws(BUDGET_WS_SPEC)
        try:
            a = lens.route(sorted(BUDGET_WS_SPEC), stage="review",
                           catalog=CAT, workspace=ws)
            b = lens.route(sorted(BUDGET_WS_SPEC), stage="review",
                           catalog=CAT, workspace=ws)
        finally:
            shutil.rmtree(ws)
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))


# ==========================================================================
# t5 / B4 (R-0008 design row 4) — component lens maps include requirement
# keywords at ASSEMBLY.
#
# A component's cached lens_map is derived WITHOUT requirement_text, so a
# lens the requirement's own keywords earn is absent from the cached
# proposals and the component path narrowed it away — a NARROWING the
# fail-open ladder forbids. `_assemble_components` now re-runs the
# requirement-keyword detector LIVE on the ctx it already builds and UNIONS
# the keyword-supported lenses into `proposed` (attributed
# 'requirement-keywords') BEFORE the narrowing; the union only ever widens,
# and floors/budget still run after on the live ctx.
#
# The fixture: module svc/api decomposes into ::handlers, ::testdata and
# ::core. The ::testdata component's own signals score `scalability` at
# 0.0875 (fixture-path discount) — below LIGHT, so the cached map does NOT
# propose it. A requirement naming latency/throughput/load adds W_KEYWORD
# (0.15) on the LIVE ctx -> 0.2375 >= LIGHT. svc/api has no dependents, so
# the B5 product-dir exemption deliberately does not apply here.
# ==========================================================================

KEYWORD_REQ = ("Reduce request latency on the hot path and hold throughput "
               "under peak load.")


def _b4_ws(tmp):
    ws = os.path.join(tmp, "b4ws")

    def w(rel, txt):
        p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(txt)

    w("svc/api/handlers/h1.py",
      "import json\n\n\ndef handle_one(req):\n"
      "    return json.dumps({'ok': True})\n")
    w("svc/api/handlers/h2.py", "def handle_two(req):\n"
                                "    return {'ok': False}\n")
    w("svc/api/testdata/seed_a.py",
      "SEED = [{'user_email': 'a@example.com', 'password': 'x'}]\n\n\n"
      "def load_seed():\n    return SEED\n")
    w("svc/api/testdata/seed_b.py",
      "ROWS = [{'amount': 10, 'currency': 'USD'}]\n\n\n"
      "def load_rows():\n    return ROWS\n")
    for i in range(1, 5):
        w("svc/api/u%d.py" % i, "def util_%d(x):\n    return x + %d\n"
          % (i, i))
    return ws


class TestB4RequirementKeywordUnionAtAssembly(unittest.TestCase):
    DIFF = ["svc/api/testdata/seed_a.py", "svc/api/testdata/seed_b.py"]
    LENS = "scalability"

    def setUp(self):
        import depgraph as dg
        self.tmp = tempfile.mkdtemp()
        self.ws = _b4_ws(self.tmp)
        self.graph = dg.scan(self.ws, decompose=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _component(self):
        return next(c for c in self.graph["components"]
                    if c["id"] == "svc/api::testdata")

    def test_fixture_precondition_cached_map_does_not_propose_the_lens(self):
        lm = self._component()["lens_map"]
        self.assertIn(self.LENS, lm)
        self.assertEqual(lm[self.LENS]["verdict"], "n/a")
        proposals = {lid for lid, e in lm.items()
                     if e["verdict"] in ("deep", "light")}
        self.assertTrue(proposals, "component must still propose something")
        self.assertNotIn(self.LENS, proposals)

    def test_keyword_earned_lens_is_routed_on_the_component_path(self):
        r = lens.route(self.DIFF, stage="review", workspace=self.ws,
                       requirement_text=KEYWORD_REQ)
        self.assertTrue(r["context"]["component_route"])
        x = entry(r, self.LENS)
        self.assertNotEqual(x["tier"], "n/a",
                            "requirement keywords were narrowed away by the "
                            "cached component map")
        self.assertEqual(x["component_attribution"], ["requirement-keywords"])
        self.assertEqual(r["context"]["component_attribution"][self.LENS],
                         ["requirement-keywords"])

    def test_union_only_widens_versus_the_module_paths_keywords(self):
        """Superset: every lens the MODULE path routes on the strength of a
        requirement keyword is routed on the component path too."""
        r = lens.route(self.DIFF, stage="review", workspace=self.ws,
                       requirement_text=KEYWORD_REQ)
        routed = {x["id"] for x in r["lenses"] if x["tier"] != "n/a"}
        ctx = lens_signals.make_ctx(self.ws, self.DIFF,
                                    requirement_text=KEYWORD_REQ,
                                    stage="review")
        module_v = lens_signals.route_verdicts(
            self.ws, self.DIFF, stage="review",
            requirement_text=KEYWORD_REQ)
        keyworded = {
            lid for lid in lens_signals.requirement_keyword_lenses(ctx)
            if module_v[lid]["verdict"] != "n/a"}
        self.assertIn(self.LENS, keyworded)
        self.assertTrue(keyworded.issubset(routed),
                        "component route dropped keyword-earned lenses: %s"
                        % sorted(keyworded - routed))

    def test_without_requirement_text_routing_is_unchanged(self):
        r = lens.route(self.DIFF, stage="review", workspace=self.ws)
        self.assertTrue(r["context"]["component_route"])
        self.assertEqual(entry(r, self.LENS)["tier"], "n/a")
        for x in r["lenses"]:
            self.assertNotIn("requirement-keywords",
                             x.get("component_attribution") or [])
        # the routed set is exactly the cached proposals disposed live (plus
        # floors) — with no requirement text the union is empty
        proposals = {lid for lid, e in self._component()["lens_map"].items()
                     if e["verdict"] in ("deep", "light")}
        proposals.update(x["id"] for x in r["lenses"] if "floor" in x)
        for x in r["lenses"]:
            if x["tier"] != "n/a" and "component_attribution" in x:
                self.assertIn(x["id"], proposals)


class TestCanonicalDiffContentSignals(unittest.TestCase):
    def test_untouched_markers_in_a_changed_file_do_not_route_lenses(self):
        ws = tempfile.mkdtemp(prefix="tp-diff-signals-")
        self.addCleanup(shutil.rmtree, ws, True)
        os.makedirs(os.path.join(ws, "src"))
        with open(os.path.join(ws, "src", "app.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("password = 'old'\naria-label = 'old'\nvalue = 2\n")

        whole_file = lens.route(
            ["src/app.py"], stage="review", workspace=ws)
        canonical_diff = lens.route(
            ["src/app.py"], stage="review", workspace=ws,
            content_by_file={"src/app.py": "value = 2\n"})

        whole_security = entry(whole_file, "security")
        canonical_security = entry(canonical_diff, "security")
        self.assertGreater(whole_security["score"],
                           canonical_security["score"])
        self.assertIn("content: auth/secret markers",
                      " ".join(whole_security["evidence"]))
        self.assertNotEqual(entry(whole_file, "accessibility")["tier"], "n/a")
        security = canonical_security
        self.assertEqual(security["tier"], "n/a")
        self.assertFalse(any("credential" in evidence.lower()
                             or "password" in evidence.lower()
                             for evidence in security["evidence"]))
        self.assertEqual(entry(canonical_diff, "accessibility")["tier"], "n/a")
        self.assertEqual(canonical_diff["context"]["content_source"],
                         "canonical-diff")


if __name__ == "__main__":
    unittest.main()
