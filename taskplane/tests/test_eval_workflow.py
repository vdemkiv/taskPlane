"""Absolute workflow compliance always precedes scoring or comparison."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import eval_rubric as er  # noqa: E402


def run(trace):
    return er.record(
        trace=trace, obligations=[{"event": "issued"}],
        dispatch=[{"lens": "architecture", "context_path": "ctx"}],
        derivations=[
            {"event": "derived", "key": "impact", "input_key": "h", "probe": True},
            {"event": "derived", "key": "diff", "input_key": "b..h"},
            {"event": "derived", "key": "impact", "input_key": "h"},
        ],
        context=[{"kind": "context_file", "fingerprint": "c", "path": "ctx"}],
        run={"schema": er.RUN_SCHEMA_V2, "target_head": "h", "hook_proof": {"proved": True},
             "driver": {"status": "success"}})


GOOD = [
    {"ts": 1, "event": "contract_activated"},
    {"ts": 2, "event": "dor", "ready": True},
    {"ts": 3, "event": "graph_impact", "scanned_head": "h", "dispositions_complete": True},
    {"ts": 4, "event": "review_context_written", "fingerprint": "c"},
    {"ts": 5, "event": "lens_route", "requested_breadth": "routed", "complete": True},
    {"ts": 6, "event": "subagent_start"},
    {"ts": 7, "event": "dod", "passed": True},
    {"ts": 8, "event": "loop_submit"},
]


class TestAbsoluteWorkflow(unittest.TestCase):
    def test_complete_ordered_evidence_is_eligible(self):
        got = er.absolute_compliance(run(GOOD))
        self.assertTrue(got["passed"], got)

    def test_missing_dor_is_ineligible_even_when_model_claims_completion(self):
        got = er.absolute_compliance(run([r for r in GOOD if r["event"] != "dor"]))
        self.assertFalse(got["passed"])
        self.assertIn("dor_missing", got["failures"])

    def test_out_of_order_context_and_dispatch_is_ineligible(self):
        rows = [dict(r) for r in GOOD]
        rows[3]["ts"], rows[5]["ts"] = 6.5, 4
        got = er.absolute_compliance(run(rows))
        self.assertIn("context_after_dispatch", got["failures"])

    def test_self_approval_is_never_eligible(self):
        rows = GOOD + [{"ts": 7.5, "event": "loop_approve_unattributed"}]
        self.assertIn("self_or_unattributed_approval", er.absolute_compliance(run(rows))["failures"])


if __name__ == "__main__":
    unittest.main()
