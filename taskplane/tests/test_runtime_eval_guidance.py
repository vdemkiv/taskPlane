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


class TestRuntimeEvalLoopWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(self.ws, "plan"))
        os.makedirs(os.path.join(self.ws, "src"))
        with open(os.path.join(self.ws, "src", "a.py"), "w",
                  encoding="utf-8") as stream:
            stream.write("def a():\n    return 1\n")
        with open(os.path.join(self.ws, "plan", "tasks.json"), "w",
                  encoding="utf-8") as stream:
            json.dump({"tasks": [{"id": "t1", "scope": ["src/**"],
                                   "tests": "true",
                                   "criteria": ["a remains callable"]}]},
                      stream)
        subprocess.run(["git", "init", "-q"], cwd=self.ws, check=True)
        subprocess.run(["git", "config", "user.email", "e@e"],
                       cwd=self.ws, check=True)
        subprocess.run(["git", "config", "user.name", "t"],
                       cwd=self.ws, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.ws, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.ws,
                       check=True)
        loop.init(self.ws, "runtime guidance", spec_path="specs/spec.md",
                  checkpoints=[])

    def test_real_stage_brief_carries_runtime_guidance(self):
        brief = loop.next_action(self.ws)

        self.assertEqual(brief["runtime_evals"]["schema"],
                         "taskplane.runtime-guidance/v1")
        self.assertEqual(brief["runtime_evals"]["baseline_policy"],
                         "telemetry-only")
        self.assertIn("loop guide", brief["runtime_evals"]["checkpoint"])

    def test_loop_guide_persists_one_correction_then_recovers(self):
        state = loop.load(self.ws)
        state["step"] = "evaluate"
        loop.save(self.ws, state)
        missing = {
            "graph_before_route": False,
            "shared_review_context": False,
            "selective_lens_mapping": False,
            "lens_results_collected": False,
        }
        complete = {key: True for key in runtime_eval.REVIEW_FACTS}

        binding = {"run_id": "a" * 32, "workspace": self.ws}
        with mock.patch("loop.review_kernel_binding", return_value=binding), \
                mock.patch("runtime_eval.collect_review_if_ready"), \
                mock.patch("runtime_eval.review_facts", return_value=missing):
            first = loop.guide(self.ws)
            second = loop.guide(self.ws)
        with mock.patch("loop.review_kernel_binding", return_value=binding), \
                mock.patch("runtime_eval.collect_review_if_ready"), \
                mock.patch("runtime_eval.review_facts", return_value=complete):
            recovered = loop.guide(self.ws)

        self.assertEqual(first["status"], "correct")
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(recovered["status"], "on_path")
        self.assertTrue(recovered["recovered"])

    def test_pass_submission_automatically_corrects_then_recovers(self):
        state = loop.load(self.ws)
        state["step"] = "evaluate"
        loop.save(self.ws, state)
        missing = {
            "graph_before_route": False,
            "shared_review_context": False,
            "selective_lens_mapping": False,
            "lens_results_collected": False,
        }
        complete = {key: True for key in runtime_eval.REVIEW_FACTS}

        binding = {"run_id": "a" * 32, "workspace": self.ws}
        with mock.patch("loop.review_kernel_binding", return_value=binding), \
                mock.patch(
                    "loop._collect_zero_lens_evaluate_before_guidance"), \
                mock.patch("runtime_eval.collect_review_if_ready"), \
                mock.patch("runtime_eval.review_facts", return_value=missing):
            corrected = loop.submit(self.ws, "pass")
            blocked = loop.submit(self.ws, "pass")
        self.assertFalse(corrected["submitted"])
        self.assertEqual(corrected["runtime_eval"]["status"], "correct")
        self.assertEqual(blocked["runtime_eval"]["status"], "blocked")
        self.assertNotIn("_submission", loop.load(self.ws))

        with mock.patch("loop.review_kernel_binding", return_value=binding), \
                mock.patch(
                    "loop._collect_zero_lens_evaluate_before_guidance"), \
                mock.patch("runtime_eval.collect_review_if_ready"), \
                mock.patch("runtime_eval.review_facts", return_value=complete):
            accepted = loop.submit(self.ws, "pass")
        self.assertTrue(accepted["submitted"])
        self.assertTrue(accepted["runtime_eval"]["recovered"])

    def test_honest_fail_submission_is_never_blocked_by_runtime_guide(self):
        state = loop.load(self.ws)
        state["step"] = "evaluate"
        loop.save(self.ws, state)

        with mock.patch("runtime_eval.review_facts",
                        side_effect=AssertionError("guide must not run")):
            submitted = loop.submit(self.ws, "fail")

        self.assertTrue(submitted["submitted"])
        self.assertEqual(submitted["submission"]["outcome"], "fail")


if __name__ == "__main__":
    unittest.main()
