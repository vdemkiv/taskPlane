from taskplane.tests.phase_fixture import save_component_workflow
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loop  # noqa: E402
import runtime_eval  # noqa: E402


class TestRuntimeEvalControls(unittest.TestCase):
    def test_controls_are_deterministic_entry_data_not_model_baselines(self):
        controls = runtime_eval.load_controls()

        self.assertEqual(controls["schema"], "taskplane.runtime-evals/v1")
        self.assertEqual(controls["baseline_policy"], "telemetry-only")
        self.assertRegex(runtime_eval.controls_fingerprint(), r"^[0-9a-f]{64}$")
        for row in controls["controls"]:
            self.assertNotIn("expected_output", row)
            self.assertNotIn("transcript", row)

    def test_first_review_drift_corrects_and_repetition_blocks(self):
        facts = {
            "graph_before_route": False,
            "shared_review_context": False,
            "selective_lens_mapping": False,
            "lens_results_collected": False,
        }

        first = runtime_eval.assess("evaluate", facts, correction_attempts=0)
        repeated = runtime_eval.assess("evaluate", facts,
                                       correction_attempts=1)

        self.assertEqual(first["status"], "correct")
        self.assertEqual(first["max_corrections"], 1)
        self.assertEqual(repeated["status"], "blocked")
        self.assertEqual(first["missing"], repeated["missing"])

    def test_dynamic_model_wording_is_not_an_eval_input(self):
        facts = {
            "graph_before_route": True,
            "shared_review_context": True,
            "selective_lens_mapping": True,
            "lens_results_collected": True,
            "output_schema_declared": True,
            "output_schema_validated": True,
            "output_producer_observed": True,
        }

        result = runtime_eval.assess("evaluate", facts,
                                     correction_attempts=99)

        self.assertEqual(result["status"], "on_path")
        self.assertNotIn("model_output", result)




if __name__ == "__main__":
    unittest.main()
