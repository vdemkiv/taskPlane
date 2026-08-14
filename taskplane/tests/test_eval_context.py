import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import eval_rubric as er  # noqa: E402


class TestInstrumentationCounters(unittest.TestCase):
    def test_complete_structural_counters_gate_without_token_telemetry(self):
        run = {"efficiency": {"cli_count": 8, "emitted_bytes": 100,
                              "repeated_derivation_bytes": 0,
                              "dispatched_agent_count": 3,
                              "prompt_view_bytes": 1000,
                              "artifact_render_bytes": 4000,
                              "duplicate_artifact_bytes": 0,
                              "duplicate_html_emissions": 0}}
        got = er.structural_efficiency(run)
        self.assertTrue(got["passed"], got)

    def test_a_thirteenth_cli_call_fails(self):
        values = {name: 0 for name in er.EFFICIENCY_COUNTERS}
        values.update(cli_count=13, dispatched_agent_count=1)
        got = er.structural_efficiency({"efficiency": values})
        self.assertIn("cli_budget_exceeded", got["failures"])

    def test_missing_counter_is_not_assumed_zero(self):
        got = er.structural_efficiency({"efficiency": {}})
        self.assertFalse(got["passed"])
        self.assertTrue(any(x.startswith("counter_missing:") for x in got["failures"]))


def cohort(**changes):
    values = {name: name + "-value" for name in er.COMPARISON_KEYS}
    values.update(changes)
    return values


class TestComparableTokens(unittest.TestCase):
    def test_missing_tokens_are_not_comparable_not_zero(self):
        run = {"comparison_key": cohort(telemetry_method="unavailable"),
               "efficiency": {"effective_tokens": None}}
        self.assertEqual(er.token_efficiency(run)["status"], "not_comparable")

    def test_one_cohort_mismatch_is_not_comparable(self):
        candidate = {"comparison_key": cohort(model="new"),
                     "efficiency": {"effective_tokens": 100}}
        baseline = {"comparison_key": cohort(model="old"),
                    "efficiency": {"effective_tokens": 200}}
        got = er.token_efficiency(candidate, baseline)
        self.assertEqual(got["status"], "not_comparable")
        self.assertIn("model", got["reason"])

    def test_matching_cohort_must_meet_the_pinned_limit(self):
        candidate = {"comparison_key": cohort(),
                     "efficiency": {"effective_tokens": 1_180_001}}
        baseline = {"comparison_key": cohort(),
                    "efficiency": {"effective_tokens": 2_360_000}}
        self.assertEqual(er.token_efficiency(candidate, baseline)["status"],
                         "fail")


if __name__ == "__main__": unittest.main()
