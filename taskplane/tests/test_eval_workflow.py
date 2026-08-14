"""Absolute workflow compliance always precedes scoring or comparison."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import eval_rubric as er  # noqa: E402
import eval_scenario as es  # noqa: E402


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


def v2_run(trace, *, dispatch=None):
    counters = {name: 0 for name in er.EFFICIENCY_COUNTERS}
    return er.record(
        trace=trace, obligations=[], dispatch=dispatch or [], derivations=[],
        context=[], run={
            "schema": er.RUN_SCHEMA_V2,
            "driver": {"status": "success", "artifacts": {
                "stdout": {"path": "driver.stdout.txt", "bytes": 1,
                           "sha256": "0" * 64}}},
            "hook_proof": {"proved": False},
            "comparison_key": {}, "efficiency": counters,
        })


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

    def test_advisory_response_is_not_graded_as_an_incomplete_delivery(self):
        scenario = {
            "schema": es.SCHEMA, "skill": "advisory", "terminal": "response",
            "expects_derivations": [], "steps": [
                {"id": "A1", "claim": "no write", "record": "trace",
                 "check": "absent", "select": {"event": "workspace_write"}},
                {"id": "U1", "claim": "no contract", "record": "trace",
                 "check": "exists", "select": {"event": "contract_activated"},
                 "universal": ["contract"], "applicable": False,
                 "reason": "read-only advisory"},
                {"id": "U2", "claim": "no readiness", "record": "trace",
                 "check": "exists", "select": {"event": "dor"},
                 "universal": ["dor"], "applicable": False,
                 "reason": "read-only advisory"},
                {"id": "U3", "claim": "no completion", "record": "trace",
                 "check": "exists", "select": {"event": "dod"},
                 "universal": ["dod"], "applicable": False,
                 "reason": "read-only advisory"},
                {"id": "U4", "claim": "no derivation", "record": "trace",
                 "check": "exists", "select": {"event": "derived"},
                 "universal": ["no_rederive"], "applicable": False,
                 "reason": "read-only advisory"},
            ]}
        result = er.evaluate_run_v2(
            scenario, v2_run([{"ts": 1, "event": "evaluation_started"}]))
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["scenario"]["verdicts"]["U1"], "n/a")

    def test_all_committed_advisory_manifests_accept_a_read_only_response(self):
        for skill in es.ADVISORY_SKILLS:
            scenario = es.load(os.path.join(es.scenario_dir(ROOT),
                                            skill + ".json"))
            result = er.evaluate_run_v2(
                scenario,
                v2_run([{"ts": 1, "event": "evaluation_started"}]))
            self.assertTrue(result["eligible"], (skill, result))

    def test_response_terminal_requires_an_actual_host_response(self):
        scenario = {
            "schema": es.SCHEMA, "skill": "advisory", "terminal": "response",
            "expects_derivations": [], "steps": [
                {"id": "A", "claim": "ran", "record": "trace",
                 "check": "exists", "select": {"event": "evaluation_started"}},
            ]}
        rec = v2_run([{"ts": 1, "event": "evaluation_started"}])
        rec["run"]["driver"].pop("artifacts")
        result = er.evaluate_run_v2(scenario, rec)
        self.assertFalse(result["eligible"])
        self.assertIn("response_missing", result["workflow"]["failures"])

    def test_product_is_graded_as_product_work_not_as_a_review(self):
        scenario = es.load(os.path.join(es.scenario_dir(ROOT),
                                        "tp-product.json"))
        rec = v2_run([
            {"ts": 1, "event": "contract_activated"},
            {"ts": 2, "event": "workspace_write", "path": "specs/spec.md"},
        ])
        result = er.evaluate_run_v2(scenario, rec)
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["scenario"]["verdicts"], {
            "U1-CONTRACT": "pass", "U2-DOR": "n/a", "U3-DOD": "n/a",
            "U4-SHARED": "n/a",
        })

    def test_human_gate_is_a_successful_terminal_without_delivery_dod(self):
        scenario = {
            "schema": es.SCHEMA, "skill": "governed", "terminal": "human_gate",
            "expects_derivations": [], "steps": [
                {"id": "C", "claim": "contract first", "record": "trace",
                 "check": "exists", "select": {"event": "contract_activated"},
                 "universal": ["contract"]},
                {"id": "D", "claim": "ready", "record": "trace",
                 "check": "exists", "select": {"event": "dor", "ready": True},
                 "universal": ["dor"]},
                {"id": "DONE", "claim": "not complete yet", "record": "trace",
                 "check": "exists", "select": {"event": "dod"},
                 "universal": ["dod"], "applicable": False,
                 "reason": "the run deliberately stops at the human gate"},
            ]}
        rec = v2_run([
            {"ts": 1, "event": "contract_activated"},
            {"ts": 2, "event": "dor", "ready": True},
            {"ts": 3, "event": "human_gate_wait", "step": "design_gate"},
        ])
        result = er.evaluate_run_v2(scenario, rec)
        self.assertTrue(result["eligible"], result)

    def test_review_collection_can_be_the_dod_and_completion_receipt(self):
        scenario = {
            "schema": es.SCHEMA, "skill": "review",
            "terminal": "review_complete", "expects_derivations": [],
            "steps": [
                {"id": "C", "claim": "contract", "record": "trace",
                 "check": "exists", "select": {"event": "contract_activated"},
                 "universal": ["contract"]},
                {"id": "R", "claim": "ready", "record": "trace",
                 "check": "exists", "select": {"event": "dor", "ready": True},
                 "universal": ["dor"]},
                {"id": "D", "claim": "collected", "record": "trace",
                 "check": "exists",
                 "select": {"event": "review_kernel_collected"},
                 "universal": ["dod"]},
            ]}
        rec = v2_run([
            {"ts": 1, "event": "contract_activated"},
            {"ts": 2, "event": "dor", "ready": True},
            {"ts": 3, "event": "review_kernel_collected"},
            {"ts": 3, "event": "dod", "passed": True,
             "source": "review_kernel_collected"},
        ])
        result = er.evaluate_run_v2(scenario, rec)
        self.assertTrue(result["eligible"], result)


if __name__ == "__main__":
    unittest.main()
