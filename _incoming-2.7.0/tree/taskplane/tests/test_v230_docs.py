"""v2.3.0 docs fix wave — doc-drift assertions (fix agent G_docs).

These tests pin shipped docs to code truth so the drift classes fixed in
this wave cannot silently return:

  - every TASKPLANE_* env var the code reads is documented in
    docs/configuration.md;
  - docs/lens-catalog.md regenerates byte-identical from lenses/catalog.json;
  - the ONE severity vocabulary: lens verdict labels all normalize through
    loop.normalize_severity, blockers/major/unknown block (map UP to high),
    and no doc re-teaches the removed "major -> med" downgrade;
  - every mechanically-enforced human gate (loop.HUMAN_STEPS) appears in
    docs/authority-matrix.md, including `selection` and the per-gate
    attribution asymmetry;
  - authority-matrix write-allows match the agent contracts
    (.eval/** for the evaluator, .em-review/lens-<id>/** for lens agents);
  - README "What's new" versions all resolve in CHANGELOG.md and both name
    the CHANGELOG authoritative (v2.1.0 reachable again);
  - the OpenAI submission worksheet carries no hardcoded release version;
  - PRIVACY.md states the honest kb-lint boundary (gate-enforced, not
    write/publish-time) and the gates really do call lint;
  - Design "boundary policy" is documented at DoD, where design_dod checks
    it — design_dor does not;
  - the drift rule reads as the engine enforces it: ANY drift entry blocks;
    the explicit accepted_drift path is the only sanctioned exception;
  - the security-methodology escalation speaks taskplane terms (no
    Conductor/Board/CSO machinery, no foreign agent names).

READ-ONLY toward taskplane/*.py: these tests import and inspect code, they
never modify it.
"""
import importlib.util
import inspect
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import design_contract  # noqa: E402

ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*rel):
    with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
        return f.read()


class TestEnvVarSurfaceDocumented(unittest.TestCase):
    def test_every_env_var_in_code_is_documented(self):
        code = ""
        tp_dir = os.path.join(ROOT, "taskplane")
        for name in sorted(os.listdir(tp_dir)):
            if name.endswith(".py"):
                code += _read("taskplane", name)
        code += _read("hooks", "hooks.json")
        tokens = set(re.findall(r"TASKPLANE_[A-Z_]*[A-Z]", code))
        # dynamic prefixes (e.g. "TASKPLANE_MODEL_" + tier) resolve to the
        # concrete names, which must themselves be present in the code set
        doc = _read("docs", "configuration.md")
        missing = sorted(t for t in tokens if t not in doc)
        self.assertEqual(
            missing, [],
            "env vars read by code but absent from docs/configuration.md "
            f"(document them there): {missing}")

    def test_state_spec_points_at_configuration_reference(self):
        self.assertIn("docs/configuration.md", _read("docs", "state-spec.md"))


class TestLensCatalogDocFresh(unittest.TestCase):
    def test_generator_output_matches_committed_doc(self):
        spec = importlib.util.spec_from_file_location(
            "gen_lens_catalog",
            os.path.join(ROOT, "scripts", "gen_lens_catalog.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "lens-catalog.md")
            mod.OUT = out  # generate to a temp path; never touch the repo
            mod.main()
            with open(out, encoding="utf-8") as f:
                fresh = f.read()
        self.assertEqual(
            fresh, _read("docs", "lens-catalog.md"),
            "docs/lens-catalog.md is stale — run "
            "python3 scripts/gen_lens_catalog.py")


class TestOneSeverityVocabulary(unittest.TestCase):
    LENS_LABELS = ("blocker", "major", "minor", "question", "praise")

    def test_lens_vocabulary_fully_normalizes(self):
        for label in self.LENS_LABELS:
            self.assertIn(label, loop._SEVERITY_MAP)
            self.assertIn(loop.normalize_severity(label),
                          loop.SEVERITY_CANONICAL)

    def test_blockers_and_unknowns_block(self):
        # fail closed: blocker/major/critical AND anything unclassifiable
        # land as 'high' — the class the sign-off gate refuses unresolved
        for label in ("blocker", "major", "critical", "sev1",
                      "definitely-not-a-severity", "", None):
            self.assertEqual(loop.normalize_severity(label), "high", label)
        self.assertEqual(loop.normalize_severity("minor"), "low")
        self.assertEqual(loop.normalize_severity("question"), "info")
        self.assertEqual(loop.normalize_severity("praise"), "info")

    def test_docs_do_not_teach_the_downgrade(self):
        for rel in (("agents", "tp-lens.md"),
                    ("skills", "tp-engineering", "SKILL.md")):
            text = _read(*rel)
            self.assertNotIn(
                "major → med", text,
                f"{'/'.join(rel)} re-teaches the removed major->med "
                "downgrade; the engine maps major -> high")
            self.assertIn("normalize_severity", text)


class TestAuthorityMatrixMatchesEngine(unittest.TestCase):
    def test_every_human_step_is_documented(self):
        matrix = _read("docs", "authority-matrix.md")
        for step in loop.HUMAN_STEPS:
            self.assertIn(
                f"`{step}`", matrix,
                f"human-owned step '{step}' (loop.HUMAN_STEPS) missing from "
                "docs/authority-matrix.md")

    def test_attribution_asymmetry_documented(self):
        matrix = _read("docs", "authority-matrix.md")
        self.assertIn("(unattributed)", matrix)
        self.assertIn("loop_approve_unattributed", matrix)
        # and the engine really behaves that way
        src = inspect.getsource(loop.approve)
        self.assertIn("(unattributed)", src)
        self.assertIn("design approval needs --by", src)

    def test_write_allows_match_agent_contracts(self):
        matrix = _read("docs", "authority-matrix.md")
        self.assertIn(".eval/**", matrix)
        self.assertIn(".em-review/lens-<id>/**", matrix)
        self.assertIn(".em-review/lens-<id>/**", _read("agents", "tp-lens.md"))
        self.assertIn(".eval/**", _read("agents", "tp-evaluator.md"))


class TestReadmeChangelogCrossRefs(unittest.TestCase):
    VER = re.compile(r"\| \*\*(v\d+\.\d+\.\d+)\*\* \|")

    def test_every_readme_release_row_resolves_in_changelog(self):
        readme_versions = self.VER.findall(_read("README.md"))
        changelog_versions = self.VER.findall(_read("CHANGELOG.md"))
        self.assertTrue(readme_versions)
        for v in readme_versions:
            self.assertIn(v, changelog_versions)
        # the release the old pointer made unreachable
        self.assertIn("v2.1.0", changelog_versions)

    def test_changelog_named_authoritative_in_both(self):
        self.assertIn("authoritative", _read("README.md"))
        self.assertIn("authoritative", _read("CHANGELOG.md"))


class TestOpenaiWorksheetVersionAgnostic(unittest.TestCase):
    def test_no_hardcoded_artifact_version(self):
        doc = _read("docs", "openai-submission.md")
        pinned = re.findall(r"\d+\.\d+\.\d+-openai\.zip", doc)
        self.assertEqual(pinned, [],
                         f"worksheet pins artifact versions again: {pinned}")
        self.assertNotIn("taskplane 2.2.0", doc)
        self.assertIn("<version>", doc)


class TestPrivacyLintHonesty(unittest.TestCase):
    def test_privacy_states_gate_enforcement_not_write_time_block(self):
        doc = _read("PRIVACY.md")
        self.assertNotIn("mechanically\n  blocks", doc)
        self.assertNotIn("mechanically blocks", doc)
        for phrase in ("Definition-of-Done exit gate",
                       "engineering-review gate"):
            self.assertIn(phrase, doc)

    def test_the_gates_really_call_lint(self):
        # DoD exit gate (tp.py) and the EM/signoff path (loop.py) lint the
        # store; write/publish time does not — the doc must match this
        self.assertRegex(_read("taskplane", "tp.py"), r"kbmod\.lint\(ws\)")
        self.assertRegex(_read("taskplane", "loop.py"), r"kb\.lint\(ws\)")


class TestDesignBoundaryPolicyAtDod(unittest.TestCase):
    def test_dor_does_not_check_boundary_policy(self):
        src = inspect.getsource(design_contract.design_dor)
        self.assertNotIn("boundary_mode", src)
        self.assertNotIn("depth_policy", src)

    def test_skill_states_policy_under_dod(self):
        text = _read("skills", "taskplane", "SKILL.md")
        dor = re.search(r"Design DoR[^.]*\.", text, re.S).group(0)
        dod = re.search(r"Design DoD[^.]*\.", text, re.S).group(0)
        self.assertNotIn("boundary\npolicy",
                         dor.replace("boundary policy", "boundary\npolicy"))
        self.assertNotIn("boundary policy", dor)
        self.assertIn("boundary", dod)


class TestDriftRuleDocsMatchEngine(unittest.TestCase):
    def test_engine_blocks_any_drift_with_accepted_path(self):
        src = inspect.getsource(design_contract.design_review_errors)
        self.assertIn("accepted_drift", src)
        self.assertIn("any drift entry", src.lower())

    def test_live_docs_dropped_the_unexplained_qualifier(self):
        for rel in (("README.md",),
                    ("docs", "loop-design.md"),
                    ("skills", "tp-design", "SKILL.md"),
                    ("skills", "tp-engineering", "SKILL.md"),
                    ("skills", "tp-build", "SKILL.md"),
                    ("agents", "tp-engineering.md")):
            self.assertNotIn(
                "unexplained drift", _read(*rel),
                f"{'/'.join(rel)}: the gate blocks ANY recorded drift — "
                "'unexplained' overstates the tolerance (CHANGELOG history "
                "rows are the only place the old wording may remain)")
        self.assertIn("accepted_drift",
                      _read("skills", "tp-design", "SKILL.md"))


class TestSecurityMethodologySpeaksTaskplane(unittest.TestCase):
    def test_no_foreign_escalation_machinery(self):
        doc = _read("lenses", "references", "security-methodology.md")
        for phrase in ("Conductor schedules", "convene the Board",
                       "loop-execution-evaluator", "loop-fixer",
                       "CSO leading"):
            self.assertNotIn(phrase, doc)
        self.assertIn("tp-fixer", doc)


class TestReadmeGateHonesty(unittest.TestCase):
    def test_protocol_vs_mechanical_guarantee_stated(self):
        readme = _read("README.md")
        self.assertIn("protocol + audit", readme)
        # and the engine documents the same boundary at the submit seam
        # (normalize the wrapped docstring before matching)
        src = " ".join(inspect.getsource(loop.submit).split())
        self.assertIn("PROTOCOL guarantee", src)

    def test_compose_section_matches_skill_persistence_step(self):
        # README promises durable review output via the KB/debt protocol;
        # the tp-engineering skill must actually mandate it
        self.assertIn("tp req debt", _read("README.md"))
        skill = _read("skills", "tp-engineering", "SKILL.md")
        self.assertIn("Persist before you part", skill)
        self.assertIn("req debt", skill)


if __name__ == "__main__":
    unittest.main()
