import inspect
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requirements as req  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "calibration")


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _req_from_inputs(fi):
    """Rebuild a requirement dict from recorded corpus forecast inputs."""
    return {
        "functional": ["<recorded>"] * fi["functional_count"],
        "acceptance": ["<recorded>"] * fi["acceptance_count"],
        "open_questions": ["<recorded>"] * fi["open_questions_count"],
        "nfr": {lz: "<recorded>" for lz in fi["nfr_stated"]},
    }


def _old_cycles(n_gaps):
    """Pre-recalibration forecast: every gap ~0.5 cycles, (n+1)//2."""
    return 0 if n_gaps == 0 else (n_gaps + 1) // 2


def _functional_complete(fi):
    """Does a corpus entry's recorded inputs have a COMPLETE functional
    axis (the precondition for any NFR discount to apply at all)?"""
    return bool(fi["functional_count"] and fi["acceptance_count"]
                and not fi["open_questions_count"])


class TestRequirementRecords(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_record_writes_file_and_index(self):
        e = req.record_requirement(
            self.ws, "export user data",
            functional=["user can request a data export"],
            acceptance=["export contains all user rows"],
            context_files=["src/export/**"])
        self.assertEqual(e["id"], "R-0001")
        self.assertTrue(os.path.exists(
            os.path.join(req.kb_dir(self.ws), e["file"])))
        self.assertEqual(len(req.list_requirements(self.ws)), 1)

    def test_ids_increment(self):
        req.record_requirement(self.ws, "a")
        self.assertEqual(req.record_requirement(self.ws, "b")["id"], "R-0002")

    def test_change_request_links_and_status(self):
        base = req.record_requirement(self.ws, "orig")
        chg = req.record_requirement(self.ws, "orig v2",
                                     changed_from=base["id"])
        self.assertEqual(chg["status"], "changed")
        self.assertEqual(chg["links"]["changed_from"], base["id"])

    def test_product_signoff_is_human_and_dor_gated(self):
        thin = req.record_requirement(
            self.ws, "thin", functional=["works"], acceptance=["verified"],
            nfr={"security": "no new trust boundary"})
        with self.assertRaises(req.ProductSignoffError):
            req.product_signoff(
                self.ws, thin["id"], decision="approve", by="approved")

        ready = req.record_requirement(
            self.ws, "ready", functional=["works"], acceptance=["verified"],
            nfr={"security": "no new trust boundary",
                 "architecture": "local and reversible"})
        result = req.product_signoff(
            self.ws, ready["id"], decision="approve", by="approved by user")
        self.assertTrue(result["dor"]["passed"])
        self.assertEqual(req.get_requirement(
            self.ws, ready["id"])["status"], "product-approved")
        self.assertTrue(req.product_signoff(
            self.ws, ready["id"], decision="approve",
            by="approved by user")["idempotent"])

    def test_product_changes_return_to_same_requirement(self):
        row = req.record_requirement(self.ws, "revise me")
        result = req.product_signoff(
            self.ws, row["id"], decision="changes", by="needs clearer AC")
        self.assertEqual(result["requirement"], row["id"])
        self.assertEqual(req.get_requirement(
            self.ws, row["id"])["status"], "changes-requested")
        amended = req.amend_requirement(
            self.ws, row["id"], functional=["clear behavior"],
            acceptance=["observable outcome"], clear_open=True,
            nfr={"security": "no new trust boundary",
                 "architecture": "local and reversible"})
        self.assertEqual(amended["id"], row["id"])
        self.assertEqual(amended["status"], "draft")
        self.assertNotIn("product_signoff", amended)
        approved = req.product_signoff(
            self.ws, row["id"], decision="approve", by="approved revision")
        self.assertEqual(approved["requirement"], row["id"])
        with open(os.path.join(req.kb_dir(self.ws), amended["file"]),
                  encoding="utf-8") as stream:
            body = stream.read()
        self.assertIn("- status: product-approved", body)
        self.assertIn("observable outcome", body)


class TestRefinementScorer(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_fully_refined_scores_high_no_gaps(self):
        # ordinary code path → no NFR lenses apply → nfr axis is a free 1.0
        r = req.record_requirement(
            self.ws, "add complete()",
            functional=["mark a todo complete"],
            acceptance=["completing sets done=true and is idempotent"],
            context_files=["src/todo/**"])
        s = req.score_refinement(r)
        self.assertEqual(s["gaps"], [])
        self.assertEqual(s["score"], 1.0)
        self.assertIn("straight-through", s["forecast"])

    def test_open_questions_and_missing_acceptance_are_gaps(self):
        r = req.record_requirement(
            self.ws, "fuzzy feature",
            functional=["do the thing"],
            open_questions=["which auth model?"],
            context_files=["src/todo/**"])
        s = req.score_refinement(r)
        details = [g["detail"] for g in s["gaps"]]
        self.assertTrue(any("acceptance" in d for d in details))
        self.assertTrue(any("open question" in d for d in details))
        self.assertLess(s["score"], 1.0)

    def test_nfr_gap_detected_via_router(self):
        # auth files → security lens applies → an unstated security NFR = gap
        r = req.record_requirement(
            self.ws, "login",
            functional=["user can log in"],
            acceptance=["valid creds return a session"],
            context_files=["src/auth/**"])
        s = req.score_refinement(r)
        self.assertIn("security", s["applicable_nfr"])
        self.assertTrue(any(g.get("lens") == "security" for g in s["gaps"]))

    def test_stated_nfr_covers_the_axis(self):
        r = req.record_requirement(
            self.ws, "login",
            functional=["user can log in"],
            acceptance=["valid creds return a session"],
            nfr={"security": "passwords hashed with argon2; no creds in logs"},
            context_files=["src/auth/**"])
        s = req.score_refinement(r)
        self.assertIn("security", s["covered_nfr"])
        self.assertFalse(any(g.get("lens") == "security" for g in s["gaps"]))

    def test_gate_is_advisory_unless_high_cost(self):
        # touches auth → an unstated security NFR + missing functional/accept
        # → well below threshold
        r = req.record_requirement(self.ws, "thin", context_files=["src/auth/**"])
        low = req.gate(r, high_cost=False)
        self.assertTrue(low["below_threshold"])
        self.assertFalse(low["blocking"])          # advisory
        hi = req.gate(r, high_cost=True)
        self.assertTrue(hi["blocking"])            # hard block for risky work


class TestTaskModeAndDebt(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_quick_when_low_refinement_small_change(self):
        m = req.suggest_mode(0.3, change_size=2)
        self.assertEqual(m["mode"], "quick")

    def test_full_when_refined_or_large(self):
        self.assertEqual(req.suggest_mode(0.9, 2)["mode"], "full")
        self.assertEqual(req.suggest_mode(0.3, 20)["mode"], "full")

    def test_debt_recorded_and_listed(self):
        r = req.record_requirement(self.ws, "feature")
        d = req.record_debt(self.ws, "harden export path",
                            requirement_id=r["id"],
                            reason="shipped quick stub",
                            follow_up="stream + paginate large exports")
        self.assertEqual(d["id"], "D-0001")
        self.assertTrue(os.path.exists(
            os.path.join(req.kb_dir(self.ws), d["file"])))
        self.assertEqual(len(req.list_debt(self.ws)), 1)
        req.resolve_debt(self.ws, d["id"])
        self.assertEqual(len(req.list_debt(self.ws)), 0)

    def test_cost_estimate_bands(self):
        self.assertEqual(req.estimate_cost(1, [])["band"], "small")
        self.assertEqual(
            req.estimate_cost(8, ["security", "scalability"])["band"], "large")


class TestCalibrationCorpus(unittest.TestCase):
    """B1 (R-0008): the two-phase calibration corpus, replayed through the
    recalibrated scorer. Well-scoped rows that ran clean must carry no false
    friction; under-specified rows must warn at least as loudly as the old
    weights (no-under-warn)."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = _load("phase1-2-corpus.json")
        cls.entries = cls.corpus["entries"]

    # ---- corpus schema/versioning (so future retros append + re-score)

    def test_corpus_is_versioned_with_provenance(self):
        meta = self.corpus["_meta"]
        self.assertEqual(meta["schema"], "taskplane.calibration-corpus/v1")
        self.assertTrue(meta["captured"])
        self.assertIn("0007", meta["source_decisions"])
        self.assertIn("0015", meta["source_decisions"])

    def test_corpus_captures_both_phases_and_outcomes(self):
        # store truth: 6 tasks (retro 0007) + 9 tasks (retro 0015) = 15,
        # with exactly 1 fix cycle total (phase 2 t7). The design text's
        # "17 tasks" is not reproducible from any store record — the
        # discrepancy is documented in _meta.count_note.
        by_phase = {}
        for e in self.entries:
            by_phase.setdefault(e["phase"], []).append(e)
        self.assertEqual(len(by_phase[1]), 6)
        self.assertEqual(len(by_phase[2]), 9)
        self.assertEqual(len(self.entries), self.corpus["_meta"]["task_count"])
        total_fix = sum(e["outcome"]["fix_cycles"] for e in self.entries)
        self.assertEqual(total_fix, 1)
        self.assertEqual(total_fix, self.corpus["_meta"]["fix_cycles_total"])
        self.assertIn("15", self.corpus["_meta"]["count_note"])

    def test_corpus_entry_schema(self):
        for e in self.entries:
            for k in ("phase", "task", "requirement", "title", "scope",
                      "forecast_inputs", "as_scored", "outcome"):
                self.assertIn(k, e, f"{e.get('task')}: missing {k}")
            fi = e["forecast_inputs"]
            for k in ("functional_count", "acceptance_count",
                      "open_questions_count", "nfr_stated",
                      "applicable_nfr", "covered_nfr"):
                self.assertIn(k, fi)
            self.assertIn("score", e["as_scored"])
            self.assertIn("fix_cycles", e["outcome"])

    # ---- replay: recorded inputs reproduce the recorded plan-gate scores
    #      wherever behavior is pinned unchanged (functional-incomplete rows)

    def test_replay_reproduces_recorded_scores_for_warned_rows(self):
        for e in self.entries:
            if e["phase"] != 1:
                continue
            s = req.score_axes(_req_from_inputs(e["forecast_inputs"]),
                               e["forecast_inputs"]["applicable_nfr"])
            self.assertEqual(s["score"], e["as_scored"]["score"],
                             f"phase1 {e['task']}: warned row must re-score "
                             "identically (no loosening)")

    # ---- calibration direction 1: no false friction on well-scoped rows

    def test_well_scoped_clean_rows_score_at_or_above_threshold(self):
        # phase 2 rows: functionally complete requirements, ran (nearly)
        # clean — the recalibrated scorer must not manufacture friction.
        rows = [e for e in self.entries if e["phase"] == 2]
        self.assertEqual(len(rows), 9)
        for e in rows:
            s = req.score_axes(_req_from_inputs(e["forecast_inputs"]),
                               e["forecast_inputs"]["applicable_nfr"])
            self.assertGreaterEqual(
                s["score"], 0.6,
                f"phase2 {e['task']}: well-scoped row below threshold")
            d = req.forecast_detail(s["gaps"])
            self.assertLess(d["friction"], 0.33,
                            f"phase2 {e['task']}: friction >= old 0.33")

    # ---- calibration direction 2: real under-specified rows never quieter

    def test_phase1_underspecified_rows_warn_at_least_as_loudly(self):
        for e in self.entries:
            if e["phase"] != 1:
                continue
            s = req.score_axes(_req_from_inputs(e["forecast_inputs"]),
                               e["forecast_inputs"]["applicable_nfr"])
            self.assertLess(s["score"], 0.6)
            d = req.forecast_detail(s["gaps"])
            self.assertGreaterEqual(
                d["cycles"], _old_cycles(len(s["gaps"])),
                f"phase1 {e['task']}: recalibrated forecast quieter than "
                "the old weights")


class TestNoUnderWarnNegativeCorpus(unittest.TestCase):
    """The guardrail corpus: recalibration may not silence real warnings.
    Each synthetic under-specified requirement stays below threshold and
    forecasts at least as many cycles as the old weights."""

    @classmethod
    def setUpClass(cls):
        cls.negatives = _load("negative-corpus.json")["entries"]

    def test_three_synthetics_present(self):
        self.assertEqual(len(self.negatives), 3)
        ids = {e["id"] for e in self.negatives}
        self.assertEqual(ids, {"neg-vague-acceptance", "neg-no-test-hooks",
                               "neg-auth-missing-security"})

    def test_every_negative_stays_flagged_below_threshold(self):
        for e in self.negatives:
            s = req.score_axes(e["requirement"], e["applicable_nfr"])
            self.assertTrue(s["gaps"], f"{e['id']}: no gaps detected")
            self.assertLess(s["score"], 0.6,
                            f"{e['id']}: under-specified requirement scored "
                            "at/above the non-blocking threshold")

    def test_every_negative_forecasts_at_least_old_cycles(self):
        for e in self.negatives:
            s = req.score_axes(e["requirement"], e["applicable_nfr"])
            d = req.forecast_detail(s["gaps"])
            self.assertGreaterEqual(
                d["cycles"], _old_cycles(len(s["gaps"])),
                f"{e['id']}: recalibration quieted a real warning")

    def test_auth_negative_routes_security_live(self):
        # the recorded axes are not a fiction: the live router still says
        # security applies to the auth-touching scope.
        e = next(x for x in self.negatives
                 if x["id"] == "neg-auth-missing-security")
        s = req.score_refinement({**e["requirement"], "context_files":
                                  e["context_files"]})
        self.assertIn("security", s["applicable_nfr"])
        self.assertLess(s["score"], 0.6)


class TestRecalibratedScheme(unittest.TestCase):
    """The class-weighted scheme itself (design B1, approach A): functional
    gaps keep 0.5 cycles; non-critical NFR gaps drop to 0.1 ONLY when the
    functional axis is complete; security/data-safety are NEVER discounted."""

    COMPLETE = {"functional": ["f"], "acceptance": ["a"],
                "open_questions": [], "nfr": {}}
    INCOMPLETE = {"functional": [], "acceptance": ["a"],
                  "open_questions": [], "nfr": {}}

    def test_noncritical_nfr_discounted_when_functional_complete(self):
        s = req.score_axes(dict(self.COMPLETE),
                           ["scalability", "architecture", "i18n"])
        self.assertEqual(len(s["gaps"]), 3)
        self.assertGreaterEqual(s["score"], 0.6)   # no false friction
        d = req.forecast_detail(s["gaps"])
        self.assertLess(d["friction"], 0.33)       # 3 x 0.1 < old 0.33
        self.assertEqual(d["cycles"], 0)

    def test_no_discount_when_functional_incomplete(self):
        # functional incomplete → byte-identical to the old scorer:
        # score 0.5*f + 0.5*nfr, every gap ~0.5 cycles.
        s = req.score_axes(dict(self.INCOMPLETE),
                           ["scalability", "architecture"])
        self.assertEqual(s["score"],
                         round(0.5 * (2 / 3) + 0.5 * 0.0, 2))
        d = req.forecast_detail(s["gaps"])
        self.assertEqual(d["friction"], 0.5 * len(s["gaps"]))
        self.assertEqual(d["cycles"], _old_cycles(len(s["gaps"])))

    def test_security_gap_never_scores_clean_regardless_of_completeness(self):
        # sweep: functionally complete, every non-critical axis covered,
        # security unstated — the score must stay below threshold no matter
        # how many covered axes pad the average.
        noncrit = sorted(req.NFR_LENSES - req.CRITICAL_NFR_LENSES)
        for k in range(len(noncrit) + 1):
            covered = noncrit[:k]
            r = {"functional": ["f"], "acceptance": ["a"],
                 "open_questions": [],
                 "nfr": {lz: "stated" for lz in covered}}
            s = req.score_axes(r, ["security"] + covered)
            self.assertTrue(any(g.get("lens") == "security"
                                for g in s["gaps"]))
            self.assertLess(s["score"], 0.6,
                            f"security gap scored clean with {k} covered "
                            "non-critical axes")
            d = req.forecast_detail(s["gaps"])
            self.assertGreaterEqual(d["friction"], 0.5,
                                    "security gap discounted")

    def test_data_safety_gap_never_discounted_either(self):
        s = req.score_axes(dict(self.COMPLETE), ["data-safety", "i18n"])
        self.assertLess(s["score"], 0.6)
        d = req.forecast_detail(s["gaps"])
        self.assertGreaterEqual(d["friction"], 0.5)

    def test_covered_security_does_not_trigger_the_cap(self):
        r = {"functional": ["f"], "acceptance": ["a"], "open_questions": [],
             "nfr": {"security": "threat model stated"}}
        s = req.score_axes(r, ["security", "i18n"])
        self.assertNotIn("security", [g.get("lens") for g in s["gaps"]])
        self.assertGreaterEqual(s["score"], 0.9)

    def test_fully_covered_still_scores_one(self):
        r = {"functional": ["f"], "acceptance": ["a"], "open_questions": [],
             "nfr": {"security": "s", "architecture": "a"}}
        s = req.score_axes(r, ["security", "architecture"])
        self.assertEqual(s["score"], 1.0)
        self.assertEqual(s["gaps"], [])
        self.assertIn("straight-through", s["forecast"])

    def test_forecast_string_derives_from_forecast_detail(self):
        gaps_inc = req.score_axes(dict(self.INCOMPLETE),
                                  ["scalability"])["gaps"]
        d = req.forecast_detail(gaps_inc)
        text = req.forecast(gaps_inc)
        self.assertIn(f"~{d['cycles']} fix cycle", text)
        # old string form preserved on the undiscounted path
        self.assertIn("refining now is cheaper", text)
        # discounted path says ~0 cycles but still names the gaps
        gaps_ok = req.score_axes(dict(self.COMPLETE), ["i18n"])["gaps"]
        self.assertIn("~0 fix cycle", req.forecast(gaps_ok))
        self.assertIn("1 gap(s)", req.forecast(gaps_ok))

    def test_score_refinement_shape_unchanged(self):
        # callers (loop._refinement_report / gate) rely on this exact shape.
        r = {"functional": ["f"], "acceptance": ["a"], "open_questions": [],
             "nfr": {}, "context_files": ["src/todo/**"]}
        s = req.score_refinement(r)
        self.assertEqual(
            set(s), {"score", "functional", "nfr", "applicable_nfr",
                     "covered_nfr", "gaps", "forecast"})
        self.assertIsInstance(s["score"], float)
        self.assertIsInstance(s["gaps"], list)
        self.assertIsInstance(s["forecast"], str)


class TestRiskBearingNfrAxesAreNeverDiscounted(unittest.TestCase):
    """Phase 3 EM review, deep3 finding #3 (MED regression): B1's critical
    set was narrower than its own no-under-warn principle. NFR_LENSES also
    carries privacy-compliance (PII/GDPR), dba (schema/data migrations) and
    sre (availability) — all risk-bearing — yet each was discounted to 0.1.

    Measured before the fix: a functionally-complete requirement with the
    axis unstated scored 0.91 for each of the three (0.5, below threshold,
    at 43253c22), so `gate(high_cost=True)` returned "proceed —
    sufficiently refined" where it previously hard-BLOCKED."""

    RISK_BEARING = ("security", "data-safety", "privacy-compliance",
                    "dba", "sre")
    COMPLETE = {"functional": ["migrate the orders table"],
                "acceptance": ["orders migrated"], "open_questions": [],
                "nfr": {}}

    def test_the_critical_set_is_the_risk_bearing_family(self):
        self.assertEqual(req.CRITICAL_NFR_LENSES, set(self.RISK_BEARING))
        self.assertLess(req.CRITICAL_NFR_LENSES, req.NFR_LENSES)

    def test_each_risk_bearing_axis_caps_below_threshold_when_unstated(self):
        for axis in self.RISK_BEARING:
            with self.subTest(axis):
                s = req.score_axes(dict(self.COMPLETE), [axis])
                self.assertLessEqual(s["score"], req.CRITICAL_GAP_SCORE_CAP)
                self.assertLess(s["score"], 0.6)
                d = req.forecast_detail(s["gaps"])
                self.assertGreaterEqual(d["friction"],
                                        req.GAP_WEIGHT_CRITICAL_NFR)
                self.assertGreaterEqual(d["cycles"], 1)

    def test_high_cost_work_with_an_unstated_risk_axis_hard_blocks(self):
        # the reviewer's scenario: high-cost/irreversible work touching a
        # data migration with no dba statement must BLOCK, not proceed
        for axis in self.RISK_BEARING:
            with self.subTest(axis), mock.patch.object(
                    req, "applicable_nfr_lenses", return_value=[axis]):
                g = req.gate(dict(self.COMPLETE), high_cost=True)
                self.assertTrue(g["below_threshold"], g)
                self.assertTrue(g["blocking"], g)
                self.assertIn("BLOCK", g["recommendation"])

    def test_covering_the_axis_clears_the_cap(self):
        for axis in self.RISK_BEARING:
            with self.subTest(axis):
                r = {**self.COMPLETE, "nfr": {axis: "stated"}}
                s = req.score_axes(r, [axis])
                self.assertEqual(s["score"], 1.0)
                self.assertEqual(s["gaps"], [])

    def test_fit_and_finish_axes_stay_discountable(self):
        # the widening is scoped: rework-costing axes keep the discount, so
        # B1's "no false friction" direction is preserved.
        rest = sorted(req.NFR_LENSES - req.CRITICAL_NFR_LENSES)
        self.assertEqual(rest, ["accessibility", "architecture",
                                "cost-finops", "i18n", "integrability",
                                "scalability"])
        s = req.score_axes(dict(self.COMPLETE), rest)
        self.assertEqual(len(s["gaps"]), len(rest))
        self.assertGreaterEqual(s["score"], 0.6)     # no false friction
        d = req.forecast_detail(s["gaps"])
        self.assertEqual(d["friction"],
                         round(len(rest) * req.GAP_WEIGHT_NFR_DISCOUNTED, 2))
        self.assertLess(d["cycles"], _old_cycles(len(rest)))
        # and one at a time, each still forecasts zero cycles
        for axis in rest:
            with self.subTest(axis):
                one = req.score_axes(dict(self.COMPLETE), [axis])
                self.assertGreaterEqual(one["score"], 0.6)
                self.assertEqual(
                    req.forecast_detail(one["gaps"])["cycles"], 0)

    def test_the_discount_comment_does_not_claim_corpus_evidence(self):
        """The corpus has ZERO entries in the population the discount
        governs (every phase-1 row is functionally INCOMPLETE, every
        phase-2 row has no NFR gap), so the comment may not claim it shows
        anything about that population."""
        src = inspect.getsource(req)
        head = src[:src.index("GAP_WEIGHT_FUNCTIONAL")]
        self.assertNotIn("rarely cost a cycle", head)
        self.assertIn("ZERO entries", head)
        governed = [e["task"] for e in _load("phase1-2-corpus.json")["entries"]
                    if _functional_complete(e["forecast_inputs"])
                    and set(e["forecast_inputs"]["applicable_nfr"])
                    - set(e["forecast_inputs"]["covered_nfr"])]
        self.assertEqual(governed, [], "the corpus now HAS entries in the "
                         "discounted population — re-word the comment and "
                         "calibrate the weight against them")


class TestKBCoexistence(unittest.TestCase):
    def test_requirements_and_decisions_share_index(self):
        import kb
        ws = tempfile.mkdtemp()
        kb.record_decision(ws, "a decision", context_files=["src/**"])
        req.record_requirement(ws, "a requirement", context_files=["src/**"])
        # both live in the same index without clobbering each other
        self.assertEqual(len(kb.list_decisions(ws)), 1)
        self.assertEqual(len(req.list_requirements(ws)), 1)


if __name__ == "__main__":
    unittest.main()
