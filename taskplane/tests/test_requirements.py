import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requirements as req  # noqa: E402


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
