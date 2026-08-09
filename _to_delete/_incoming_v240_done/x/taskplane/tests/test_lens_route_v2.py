"""route() v2 — stage profiles + signal-driven verdicts (t2, R-0001).

Pins, in order of importance:
- LEGACY BYTE-IDENTITY: route() without a stage (or with use_signals=False,
  or on a catalog without stage_profiles) produces EXACTLY the pre-v2
  output — snapshot-pinned below, captured before route v2 landed.
- STAGE RESTRICTION: the candidate set is the stage profile (design never
  yields code-quality); profile membership is DATA — adding a lens to a
  profile changes routing with zero code change.
- SIGNAL INTEGRATION: verdicts come from lens_signals (i18n fixture diff
  routes i18n deep; a stdlib diff routes i18n n/a WITH negative evidence
  present in the routing output — coverage honesty).
- BUDGET + FLOORS through route(): deep hard-capped at 8 (overflow demoted
  to light, never dropped); architecture >= light on any code change;
  security never n/a on an enforcement-touching diff.
- FORCE / SKIP: --lens/--only forces deep regardless of verdict; --skip
  stays visible as an evidenced n/a.
- ENGINE FAILURE FAILS OPEN: lens_signals raising falls back to legacy
  breadth=all routing (MORE coverage) with a degradation marker.
- breadth="all" stays the legacy full-catalog sweep, byte-identical.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens  # noqa: E402
import lens_signals  # noqa: E402

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


def tree_files(root):
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            out.append(os.path.relpath(os.path.join(dirpath, n),
                                       root).replace(os.sep, "/"))
    return out


def write_ws(spec):
    """Materialize {relpath: content} into a temp workspace."""
    ws = tempfile.mkdtemp(prefix="tp-lens-v2-")
    for rel, content in spec.items():
        p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(p) or ws, exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    return ws


# --------------------------------------------------------------------------
# The EXACT output of the pre-v2 router for this fixture diff, captured on
# the t2 baseline BEFORE route v2 landed. route() without a stage must keep
# producing this byte-for-byte — that is the legacy pin.
# --------------------------------------------------------------------------
LEGACY_FILES = ["src/auth/session.py", "web/components/Card.tsx",
                "docs/guide.md"]
LEGACY_TASK_TYPE = "feature"
LEGACY_SNAPSHOT = json.loads(r"""
{
 "lenses": [
  {"id": "product", "name": "Product", "mode": "inline", "tier": "deep",
   "reasons": ["task type 'feature'"], "checks": [],
   "looks_for": "requirements met, scope creep/gaps, user-journey completeness, success metrics"},
  {"id": "security", "name": "Security", "mode": "subagent", "tier": "deep",
   "reasons": ["touches **/auth/** (src/auth/session.py)", "baseline (any code change)"],
   "checks": ["gitleaks", "semgrep --config auto", "dependency audit"],
   "looks_for": "secrets, authz gaps, injection, unsafe input, vulnerable deps"},
  {"id": "code-quality", "name": "Code quality", "mode": "inline", "tier": "deep",
   "reasons": ["baseline (any code change)"],
   "checks": ["lint", "typecheck", "jscpd (copy-paste/duplication)"],
   "looks_for": "clarity, error handling, dead code, naming, duplication"},
  {"id": "testability", "name": "Testability", "mode": "inline", "tier": "deep",
   "reasons": ["baseline (any code change)"], "checks": ["coverage"],
   "looks_for": "coverage of new paths, seams/mockability, hidden globals, non-determinism"},
  {"id": "design", "name": "Design & UX", "mode": "subagent", "tier": "deep",
   "reasons": ["touches **/*.tsx (web/components/Card.tsx)"], "checks": [],
   "looks_for": "UX flow, loading/empty/error states, visual consistency, hierarchy"},
  {"id": "tech-writer", "name": "Technical writing", "mode": "subagent", "tier": "deep",
   "reasons": ["touches **/*.md (docs/guide.md)"], "checks": [],
   "looks_for": "README/API-doc/changelog accuracy & completeness, ADR clarity, examples that run"},
  {"id": "qa", "name": "QA", "mode": "inline", "tier": "deep",
   "reasons": ["task type 'feature'"], "checks": [],
   "looks_for": "test strategy, coverage adequacy, regression risk, edge/negative cases, E2E paths"},
  {"id": "frontend", "name": "Front-end engineering", "mode": "subagent", "tier": "deep",
   "reasons": ["touches **/*.tsx (web/components/Card.tsx)"], "checks": [],
   "looks_for": "component architecture, state mgmt, render/bundle perf, browser/device compat, FE error/loading handling"},
  {"id": "tradeoffs", "name": "Design trade-offs", "mode": "inline", "tier": "deep",
   "reasons": ["task type 'feature'"], "checks": [],
   "looks_for": "unexamined single-option designs, hidden costs of the chosen path, missing revisit conditions, decisions made in code but never recorded"},
  {"id": "time-to-market", "name": "Time to market", "mode": "inline", "tier": "deep",
   "reasons": ["task type 'feature'"], "checks": [],
   "looks_for": "over-engineering vs the stated goal, deferrable work inside the critical path, scope that could ship in halves, missing debt records for deliberate cuts"},
  {"id": "architecture", "name": "System design & architecture", "mode": "inline", "tier": "deep",
   "reasons": ["baseline (system design is always on)"], "checks": [],
   "looks_for": "component/service decomposition, data flow & coupling, state & consistency, scaling & failure modes, tech-choice fit",
   "effort": "light"},
  {"id": "accessibility", "name": "Accessibility (a11y)", "mode": "inline", "tier": "deep",
   "reasons": ["touches **/*.tsx (web/components/Card.tsx)"],
   "checks": ["axe", "a11y-lint"],
   "looks_for": "keyboard nav, ARIA/screen-reader, contrast, focus management, alt text, WCAG"}
 ],
 "context": {"changed_files": 3, "has_code": true, "large_change": false,
             "task_type": "feature", "artifact_type": null,
             "breadth": "routed", "hub_dependents": 0}
}
""")


class TestLegacyByteIdentity(unittest.TestCase):
    def test_route_without_stage_is_byte_identical_to_snapshot(self):
        r = lens.route(LEGACY_FILES, task_type=LEGACY_TASK_TYPE, catalog=CAT)
        self.assertEqual(r, LEGACY_SNAPSHOT)

    def test_no_stage_profiles_key_means_legacy_even_with_stage(self):
        cat = copy.deepcopy(CAT)
        cat.pop("stage_profiles", None)
        r = lens.route(LEGACY_FILES, task_type=LEGACY_TASK_TYPE, catalog=cat,
                       stage="review")
        self.assertEqual(r, LEGACY_SNAPSHOT)

    def test_use_signals_false_forces_legacy(self):
        r = lens.route(LEGACY_FILES, task_type=LEGACY_TASK_TYPE, catalog=CAT,
                       stage="review", use_signals=False)
        self.assertEqual(r, LEGACY_SNAPSHOT)

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
    def test_i18n_fixture_diff_routes_i18n_deep(self):
        files = tree_files(I18N_POS)
        r = lens.route(files, stage="review", catalog=CAT, workspace=I18N_POS)
        x = entry(r, "i18n")
        self.assertEqual(x["tier"], "deep")
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
        self.assertEqual(sec["tier"], "deep")
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
    def test_deep_capped_at_8_overflow_demoted_never_dropped(self):
        ws = write_ws(BUDGET_WS_SPEC)
        try:
            r = lens.route(sorted(BUDGET_WS_SPEC), stage="review",
                           catalog=CAT, workspace=ws)
        finally:
            shutil.rmtree(ws)
        t = tiers(r)
        deep = [lid for lid, tier in t.items() if tier == "deep"]
        self.assertEqual(len(deep), lens_signals.DEEP_CAP)      # exactly 8
        # every catalog lens still present — overflow was demoted, not dropped
        self.assertEqual(set(t), set(ALL_IDS))
        demoted = [x for x in r["lenses"]
                   if any("budget: demoted" in e for e in x["evidence"])]
        self.assertTrue(demoted)
        for x in demoted:
            self.assertEqual(x["tier"], "light")

    def test_architecture_floor_on_any_code_change(self):
        ws = write_ws({"src/todo/util.py": "def add(a, b):\n    return a+b\n"})
        try:
            r = lens.route(["src/todo/util.py"], stage="review", catalog=CAT,
                           workspace=ws)
        finally:
            shutil.rmtree(ws)
        arch = entry(r, "architecture")
        self.assertIn(arch["tier"], ("light", "deep"))   # never n/a on code
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
        self.assertIn(entry(r, "architecture")["tier"], ("light", "deep"))

    def test_security_never_na_on_enforcement_touching_diff(self):
        ws = write_ws({"hooks/pretool_gate.txt": "plain text, zero code\n"})
        try:
            r = lens.route(["hooks/pretool_gate.txt"], stage="review",
                           catalog=CAT, workspace=ws)
        finally:
            shutil.rmtree(ws)
        self.assertIn(entry(r, "security")["tier"], ("light", "deep"))


class TestForceAndSkip(unittest.TestCase):
    def test_forced_lens_overrides_na(self):
        files = tree_files(I18N_NEG)
        r = lens.route(files, stage="review", catalog=CAT, workspace=I18N_NEG,
                       only=["i18n"])
        x = entry(r, "i18n")
        self.assertEqual(x["tier"], "deep")
        self.assertEqual(x["verdict"], "deep (forced)")
        self.assertEqual(x["mode"], "subagent")
        self.assertTrue(any("forced" in e for e in x["evidence"]))
        # everything else is an evidenced, VISIBLE n/a — not silently gone
        for other in r["lenses"]:
            if other["id"] != "i18n":
                self.assertEqual(other["tier"], "n/a")
                self.assertTrue(other["negative_evidence"])

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


class TestEngineFailureFailsOpen(unittest.TestCase):
    def test_engine_exception_falls_back_to_legacy_breadth_all(self):
        files = ["src/todo/core.py"]
        expected = lens.route(files, catalog=CAT, breadth="all")

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
        self.assertIn("degraded", r["context"])
        # the fallback IS legacy breadth=all — MORE coverage, never less
        self.assertEqual(r["context"]["breadth"], "all")
        self.assertEqual(r["lenses"], expected["lenses"])
        self.assertGreaterEqual(len(r["lenses"]), len(ALL_IDS))


class TestBreadthAllUnchanged(unittest.TestCase):
    def test_breadth_all_is_legacy_even_with_stage(self):
        files = ["src/todo/core.py"]
        legacy = lens.route(files, catalog=CAT, breadth="all")
        with_stage = lens.route(files, catalog=CAT, breadth="all",
                                stage="review", workspace=".")
        self.assertEqual(with_stage, legacy)
        # legacy shape: no v2 verdict/score keys anywhere
        for x in with_stage["lenses"]:
            self.assertNotIn("verdict", x)
            self.assertNotIn("score", x)


class TestDispatchBriefsV2(unittest.TestCase):
    def _routing(self):
        ws = write_ws(BUDGET_WS_SPEC)
        try:
            return lens.route(sorted(BUDGET_WS_SPEC), stage="review",
                              catalog=CAT, workspace=ws)
        finally:
            shutil.rmtree(ws)

    def test_na_lenses_get_no_brief_but_full_decision_is_carried(self):
        r = self._routing()
        d = lens.dispatch_briefs(r, base="HEAD")
        t = tiers(r)
        deep_ids = {b["id"] for b in d["deep"]}
        self.assertEqual(deep_ids,
                         {lid for lid, tier in t.items() if tier == "deep"})
        # light lenses batch into the single sweep-style brief
        self.assertEqual(set(d["sweep"]["ids"]),
                         {lid for lid, tier in t.items() if tier == "light"})
        # n/a lenses appear in NO brief ...
        na_ids = {lid for lid, tier in t.items() if tier == "n/a"}
        self.assertFalse(na_ids & deep_ids)
        self.assertFalse(na_ids & set(d["sweep"]["ids"]))
        # ... but the decision object carries ALL catalog dispositions,
        # each n/a with its negative evidence (coverage honesty)
        self.assertEqual(set(d["routing_decision"]), set(ALL_IDS))
        for lid in na_ids:
            self.assertTrue(d["routing_decision"][lid]["negative_evidence"])

    def test_deep_briefs_carry_additive_verdict_fields(self):
        r = self._routing()
        d = lens.dispatch_briefs(r, base="HEAD")
        for b in d["deep"]:
            self.assertEqual(b["verdict"], "deep")
            self.assertIsInstance(b["score"], float)
            self.assertTrue(b["evidence"])
            # contract:lens-brief shape otherwise unchanged
            self.assertEqual(b["task_slot"], f"lens-{b['id']}")
            self.assertEqual(b["contract"]["task_slot"], f"lens-{b['id']}")
            self.assertTrue(b["contract"]["read_only"])
            self.assertIn(f"export TASKPLANE_TASK=lens-{b['id']}", b["prompt"])

    def test_legacy_dispatch_shape_untouched(self):
        r = lens.route(LEGACY_FILES, task_type=LEGACY_TASK_TYPE, catalog=CAT)
        d = lens.dispatch_briefs(r, base="HEAD")
        self.assertNotIn("routing_decision", d)
        for b in d["deep"]:
            self.assertNotIn("verdict", b)
            self.assertNotIn("score", b)


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


if __name__ == "__main__":
    unittest.main()
