"""Evaluation lifecycle records stay complete, bounded, and secret-free."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import runtime_eval  # noqa: E402


class TestEvaluationLifecycle(unittest.TestCase):
    def test_success_record_validates_and_separates_planned_from_observed(self):
        row = runtime_eval.build_evaluation_lifecycle(
            run_id="run-1", host="codex", host_version="1.2.3",
            capability_source="host-receipt:session",
            transport="codex-native", schema_transport="native",
            schema_fallback_reason=None, task="t1", slot="lens-security",
            lease="lease-1", planned_model="gpt-5-codex",
            planned_effort="high", observed_model="gpt-5-codex",
            observed_effort="high", attempts=[{
                "attempt": 1, "status": "success", "duration_ms": 12}],
            duration_ms=12, terminal_status="success",
            validation_status="valid", telemetry={
                "available": True, "reason": None}, diagnostics=[])
        self.assertEqual(runtime_eval.validate_evaluation_lifecycle(row), [])
        self.assertEqual(row["routing"]["planned"]["model"], "gpt-5-codex")
        self.assertEqual(row["routing"]["observed"]["model"], "gpt-5-codex")

    def test_diagnostics_are_redacted_and_bounded(self):
        row = runtime_eval.build_evaluation_lifecycle(
            run_id="run-2", host="claude", host_version=None,
            capability_source="fallback", transport="workflow",
            schema_transport="governed-file",
            schema_fallback_reason="native schema unsupported", task="t1",
            slot=None, lease=None, planned_model=None, planned_effort=None,
            observed_model=None, observed_effort=None,
            attempts=[{"attempt": 1, "status": "failed", "duration_ms": 1}],
            duration_ms=1, terminal_status="failed",
            validation_status="invalid", telemetry={
                "available": False, "reason": "no usage"},
            diagnostics=["OPENAI_API_KEY=secret " + "/Users/alice/private/" +
                         "x" * 1000])
        message = row["diagnostics"][0]["message"]
        self.assertLessEqual(len(message.encode("utf-8")), 512)
        self.assertNotIn("secret", message)
        self.assertNotIn("/Users/alice", message)
        self.assertEqual(runtime_eval.validate_evaluation_lifecycle(row), [])

    def test_missing_required_field_is_rejected(self):
        row = runtime_eval.build_evaluation_lifecycle(
            run_id="run-3", host="codex", host_version=None,
            capability_source="unknown", transport="native",
            schema_transport="governed-file", schema_fallback_reason="unknown",
            task=None, slot=None, lease=None, planned_model=None,
            planned_effort=None, observed_model=None, observed_effort=None,
            attempts=[], duration_ms=0, terminal_status="unavailable",
            validation_status="unavailable", telemetry={
                "available": False, "reason": "no transcript"}, diagnostics=[])
        del row["capability_source"]
        self.assertIn("missing capability_source",
                      runtime_eval.validate_evaluation_lifecycle(row))


if __name__ == "__main__":
    unittest.main()
