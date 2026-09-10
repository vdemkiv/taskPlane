"""Model capability-tier routing (v0.9.8 feature).

taskplane pins no model in agent frontmatter (agents stay model:inherit for
portability); a step/task/lens carries an abstract tier and the driver resolves
it to a concrete model at dispatch. These tests cover the resolver, the
step/task tier selection, and that the loop payload + lens briefs surface a
resolved `model` for the driver to pass to the Agent tool."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tp  # noqa: E402
from tests.root_session_fixture import open_delivery_root  # noqa: E402
import lens as lens_router  # noqa: E402
import loop  # noqa: E402


class TestTierResolver(unittest.TestCase):
    def setUp(self):
        # isolate the TASKPLANE_MODEL_* env across tests
        self._saved = {k: os.environ.get(k) for k in
                       ("TASKPLANE_MODEL_CHEAP", "TASKPLANE_MODEL_STANDARD",
                        "TASKPLANE_MODEL_DEEP", "CODEX_HOME",
                        "CODEX_THREAD_ID")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_codex_defaults_all_tiers_to_inherit(self):
        os.environ["CODEX_HOME"] = "/tmp/codex-test"
        self.assertIsNone(tp.model_for_tier("cheap"))
        self.assertIsNone(tp.model_for_tier("standard"))
        self.assertIsNone(tp.model_for_tier("deep"))

    def test_unknown_and_none_degrade_to_inherit(self):
        self.assertIsNone(tp.model_for_tier("turbo"))   # unknown, no raise
        self.assertIsNone(tp.model_for_tier(None))       # -> standard -> None

    def test_env_override_per_tier(self):
        os.environ["TASKPLANE_MODEL_CHEAP"] = "fast-9"
        os.environ["TASKPLANE_MODEL_DEEP"] = "opus"
        self.assertEqual(tp.model_for_tier("cheap"), "fast-9")
        self.assertEqual(tp.model_for_tier("deep"), "opus")

    def test_env_inherit_sentinel_and_empty_mean_inherit(self):
        os.environ["TASKPLANE_MODEL_CHEAP"] = "inherit"
        self.assertIsNone(tp.model_for_tier("cheap"))
        os.environ["TASKPLANE_MODEL_CHEAP"] = "  "
        self.assertIsNone(tp.model_for_tier("cheap"))


class TestAgentFrontmatterPortability(unittest.TestCase):
    def test_agents_never_pin_provider_specific_models(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        agents = os.path.join(root, "agents")
        for name in os.listdir(agents):
            if not (name.startswith("tp-") and name.endswith(".md")):
                continue
            with open(os.path.join(agents, name), encoding="utf-8") as f:
                text = f.read()
            self.assertTrue(text.startswith("---\n"), name)
            self.assertEqual(len(text.split("---", 2)), 3, name)
            frontmatter = text.split("---", 2)[1]
            self.assertIn("model: inherit", frontmatter, name)


class TestStepTier(unittest.TestCase):
    def test_reasoning_steps_default_deep(self):
        for s in ("pm", "design", "plan", "em"):
            self.assertEqual(tp.step_tier(s), "deep")

    def test_build_steps_default_standard(self):
        for s in ("execute", "fix", "evaluate"):
            self.assertEqual(tp.step_tier(s), "standard")

    def test_unknown_step_is_standard(self):
        self.assertEqual(tp.step_tier("whatever"), "standard")

    def test_task_model_overrides_step_default(self):
        self.assertEqual(tp.step_tier("execute", {"model": "cheap"}), "cheap")
        self.assertEqual(tp.step_tier("plan", {"model": "cheap"}), "cheap")

    def test_invalid_task_tier_ignored(self):
        self.assertEqual(tp.step_tier("execute", {"model": "turbo"}),
                         "standard")


class TestLensBriefsCarryModel(unittest.TestCase):
    def test_lens_tier_mapping(self):
        self.assertEqual(lens_router._lens_tier("security", "deep"), "deep")
        self.assertEqual(lens_router._lens_tier("architecture", "deep"), "deep")
        self.assertEqual(lens_router._lens_tier("code-quality", "deep"),
                         "standard")
        self.assertEqual(lens_router._lens_tier("anything", "sweep"), "cheap")





if __name__ == "__main__":
    unittest.main()
