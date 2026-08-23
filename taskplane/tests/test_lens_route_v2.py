"""route() v2 — stage profiles + signal-driven verdicts (t2, R-0001).

Pins, in order of importance:
- LEGACY BYTE-IDENTITY: route() without a stage (or with use_signals=False,
  or on a catalog without stage_profiles) produces EXACTLY the pre-v2
  output — snapshot-pinned below, captured before route v2 landed.
- STAGE RESTRICTION: the candidate set is the stage profile (design never
  yields code-quality); profile membership is DATA — adding a lens to a
  profile changes routing with zero code change.
- SIGNAL INTEGRATION: verdicts come from lens_signals, then automatic
  Review/Evaluate projects them to one 4–5-lens sweep (an i18n fixture pins
  i18n into that sweep; a stdlib diff keeps i18n n/a WITH its negative
  evidence visible — coverage honesty).
- AUTOMATIC BUDGET + FLOORS through route(): no deep/full dispositions;
  exactly 4–5 sweeps; architecture is always in the sweep; contextual
  security/QA/documentation signals compete inside that fixed budget.
- AUTOMATIC SELECT / SKIP: --only pins membership inside the 4–5-lens
  automatic sweep without granting deep authority; --skip is a hard,
  evidenced exclusion. Architecture remains the mandatory sweep floor.
- ENGINE FAILURE FAILS CLOSED: lens_signals raising emits a named
  mapper_unavailable decision with zero dispatch.
- breadth="all" stays the legacy full-catalog sweep, byte-identical.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
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
        with open(p, "w", encoding="utf-8") as f:
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
# REGENERATED for lenses 2.0 (R-0012). This snapshot pins the LEGACY
# routing surface byte-for-byte so route v2 can never silently change it.
# It necessarily also pins catalog CONTENT (looks_for, checks, reasons),
# so a deliberate catalog change requires regenerating it — which is what
# happened here: 26 lens charters, looks-for lines and routing surfaces
# were rewritten. The guardrail keeps its value: any FUTURE unintended
# routing change still breaks this. Regenerate with:
#   python3 -c "import sys;sys.path.insert(0,'taskplane');import lens,json;print(json.dumps(lens.route(FILES,task_type='feature',catalog=CAT),indent=1))"
LEGACY_SNAPSHOT = json.loads(r"""
{
 "lenses": [
  {
   "id": "product",
   "name": "Product",
   "mode": "inline",
   "tier": "deep",
   "reasons": [
    "task type 'feature'"
   ],
   "checks": [],
   "looks_for": "requirements met, requirement QUALITY (verifiable, singular, unambiguous acceptance criteria), scope gaps/creep, journey completeness incl. non-happy states, existing-user regression, success metrics with baseline + guardrail + decision rule, user-facing naming"
  },
  {
   "id": "security",
   "name": "Security",
   "mode": "subagent",
   "tier": "deep",
   "reasons": [
    "touches **/auth/** (src/auth/session.py)",
    "baseline (any code change)"
   ],
   "checks": [
    "gitleaks",
    "semgrep --config auto",
    "dependency audit",
    "zizmor (GitHub Actions workflows, when `.github/**` is in the diff)"
   ],
   "looks_for": "secrets (exposure = compromise, rotate not delete), authz gaps incl. object-level/IDOR, injection, SSRF, unsafe input, security misconfiguration, supply-chain & build integrity (deps, lockfiles, CI workflows, install scripts, pinning), fail-open error paths, AI/agent surface risk"
  },
  {
   "id": "code-quality",
   "name": "Code quality",
   "mode": "inline",
   "tier": "deep",
   "reasons": [
    "baseline (any code change)"
   ],
   "checks": [
    "lint (repo's configured linter, warnings included)",
    "typecheck",
    "jscpd / `dupl` / pylint R0801 — copy-paste census, diff-scoped",
    "**suppression delta**: NEW escape hatches introduced by this diff only —"
   ],
   "looks_for": "logic correctness at boundaries, error handling that swallows or mislabels, names and comments that lie, duplication that is real coupling, dead and unreachable code, unjustified suppressions, speculative generality"
  },
  {
   "id": "testability",
   "name": "Testability",
   "mode": "inline",
   "tier": "deep",
   "reasons": [
    "baseline (any code change)"
   ],
   "checks": [
    "coverage — use ONLY as evidence for check 4: which new branches NO test can reach at all. Do not report a coverage percentage or judge coverage adequacy; that is qa's. [Inozemtseva & Holmes, ICSE 2014: coverage correlates only weakly-to-moderately with suite effectiveness once suite size is controlled — a number here would be an unsupported quality claim]"
   ],
   "looks_for": "seams and substitutability (clock, network, filesystem, DB, model/LLM client), hidden globals and shared state OUTSIDE the process, non-determinism, parallel-safety, reachability of new branches from a public surface, a pure side-effect-free core that invariants could be stated against"
  },
  {
   "id": "design",
   "name": "Design & UX",
   "mode": "subagent",
   "tier": "deep",
   "reasons": [
    "touches **/*.tsx (web/components/Card.tsx)"
   ],
   "checks": [],
   "looks_for": "UX flow, loading/empty/error/partial/success states, error recoverability, latency-proportional feedback, visual consistency against declared tokens, hierarchy"
  },
  {
   "id": "tech-writer",
   "name": "Technical writing",
   "mode": "subagent",
   "tier": "deep",
   "reasons": [
    "touches **/*.md (docs/guide.md)",
    "task type 'feature'",
    "baseline (any code change)"
   ],
   "checks": [],
   "looks_for": "documented commands/flags/endpoints/paths/defaults/outputs that the diff has made untrue, capabilities removed or renamed with docs left behind, examples that no longer run, the right documentation TYPE for the change (reference / how-to / explanation / tutorial) and one reader-question per document, prerequisites and destructive-step warnings placed after the step they govern, new documentation nobody can reach, one name per concept, decisions made in the diff and recorded nowhere, changelog entries that describe commits rather than user outcomes"
  },
  {
   "id": "qa",
   "name": "QA",
   "mode": "inline",
   "tier": "deep",
   "reasons": [
    "task type 'feature'",
    "untested change (code changed, no test file)"
   ],
   "checks": [],
   "looks_for": "test strategy, behaviour coverage (never a coverage %), assertion strength, regression risk, edge/negative cases, flake patterns, rerun/retry used as suppression, tests that encode the implementation rather than the requirement, E2E paths"
  },
  {
   "id": "frontend",
   "name": "Front-end engineering",
   "mode": "subagent",
   "tier": "deep",
   "reasons": [
    "touches **/*.tsx (web/components/Card.tsx)"
   ],
   "checks": [],
   "looks_for": "component architecture, state mgmt, async race safety, render/bundle perf, Core Web Vitals impact (LCP/INP/CLS) with a named code cause, browser/device compat against a Baseline target, FE error/loading handling"
  },
  {
   "id": "tradeoffs",
   "name": "Design trade-offs",
   "mode": "inline",
   "tier": "deep",
   "reasons": [
    "task type 'feature'"
   ],
   "checks": [],
   "looks_for": "unexamined single-option designs, one-way-door choices taken without deliberation, strawman alternatives, criteria reverse-engineered after the winner was picked, hidden costs of the chosen path, what the rejected option would have bought, missing or unobservable revisit triggers, decisions made in code but never recorded durably, choices that silently contradict or supersede an accepted D-record, trade-off tables that never name the quality attribute being optimised"
  },
  {
   "id": "time-to-market",
   "name": "Time to market",
   "mode": "inline",
   "tier": "deep",
   "reasons": [
    "task type 'feature'"
   ],
   "checks": [],
   "looks_for": "over-engineering vs the stated goal, deferrable work inside the critical path, ONE-WAY DOORS inside a proposed fast path, the PRICE of deferring (backfill / migration / re-teach cost), named slicing seams (vertical slice, dark launch, branch by abstraction), missing debt records for deliberate cuts"
  },
  {
   "id": "architecture",
   "name": "System design & architecture",
   "mode": "inline",
   "tier": "deep",
   "reasons": [
    "baseline (system design is always on)"
   ],
   "checks": [],
   "looks_for": "component/service decomposition, data flow & coupling measured against the dependency graph, state & consistency, scaling & failure modes, structure introduced without a requirement that needs it",
   "effort": "light"
  },
  {
   "id": "accessibility",
   "name": "Accessibility (a11y)",
   "mode": "inline",
   "tier": "deep",
   "reasons": [
    "touches **/*.tsx (web/components/Card.tsx)"
   ],
   "checks": [
    "axe-core (via axe DevTools / jest-axe / cypress-axe / Playwright `@axe-core/playwright`)",
    "a11y-lint (eslint-plugin-jsx-a11y, vue/svelte a11y compiler warnings)",
    "contrast checker"
   ],
   "looks_for": "keyboard operability and order, ARIA role-vs-implementation honesty, accessible-name appropriateness, focus management, announcement timing, non-text contrast, pointer alternatives, accessible authentication, WCAG 2.2 AA"
  },
  {
   "id": "i18n",
   "name": "Localization / i18n (optional)",
   "mode": "inline",
   "tier": "deep",
   "reasons": [
    "touches **/*.tsx (web/components/Card.tsx)",
    "task type 'feature'"
   ],
   "checks": [],
   "looks_for": "externalized strings incl. backend-generated text, plural/gender selection, sentence assembly by concatenation, locale formatting AND parsing, currency minor units, timezone intent, text expansion, bidi isolation and RTL, collation and grapheme-safe text"
  }
 ],
 "context": {
  "changed_files": 3,
  "has_code": true,
  "large_change": false,
  "task_type": "feature",
  "artifact_type": null,
  "breadth": "routed",
  "hub_dependents": 0,
  "deep_cap": 8,
  "deep_dispatched": 4
 }
}
""")


class TestLegacyByteIdentity(unittest.TestCase):
    """The snapshot guards that ROUTE V2 never changed legacy routing. It
    was updated once, for D-0005, and only in `context`: the legacy path had
    no dispatch budget at all, so `--all` fanned out 26 subagents under a cap
    of 8. The two new keys REPORT the budget; the `lenses` list in this
    snapshot is byte-unchanged, which is the part that decides what runs.
    `test_the_lens_selection_itself_is_untouched` below pins that separately
    so a future edit cannot hide a selection change inside a context diff."""

    @staticmethod
    def _routing_only(value):
        """Language references are an intentional additive handoff field."""
        value = copy.deepcopy(value)
        value.get("context", {}).pop("language_references", None)
        for row in value.get("lenses") or []:
            row.pop("language_references", None)
        return value

    def test_the_lens_selection_itself_is_untouched(self):
        """The half of the snapshot that must never move for a disclosure
        change: same lenses, same modes, same tiers, same reasons."""
        r = lens.route(LEGACY_FILES, task_type=LEGACY_TASK_TYPE, catalog=CAT)
        self.assertEqual(self._routing_only(r)["lenses"],
                         LEGACY_SNAPSHOT["lenses"])
        self.assertEqual(
            [(e["id"], e["mode"], e["tier"]) for e in r["lenses"]],
            [(e["id"], e["mode"], e["tier"])
             for e in LEGACY_SNAPSHOT["lenses"]])

    def test_route_without_stage_is_byte_identical_to_snapshot(self):
        r = lens.route(LEGACY_FILES, task_type=LEGACY_TASK_TYPE, catalog=CAT)
        self.assertEqual(self._routing_only(r), LEGACY_SNAPSHOT)

    def test_no_stage_profiles_key_means_legacy_even_with_stage(self):
        cat = copy.deepcopy(CAT)
        cat.pop("stage_profiles", None)
        r = lens.route(LEGACY_FILES, task_type=LEGACY_TASK_TYPE, catalog=cat,
                       stage="review")
        self.assertEqual(self._routing_only(r), LEGACY_SNAPSHOT)

    def test_use_signals_false_forces_legacy(self):
        r = lens.route(LEGACY_FILES, task_type=LEGACY_TASK_TYPE, catalog=CAT,
                       stage="review", use_signals=False)
        self.assertEqual(self._routing_only(r), LEGACY_SNAPSHOT)

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
        # Automatic sweep dispositions batch into the single sweep brief.
        self.assertEqual(set(d["sweep"]["ids"]),
                         {lid for lid, tier in t.items() if tier == "sweep"})
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
