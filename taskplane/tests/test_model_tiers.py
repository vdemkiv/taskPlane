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
import lens as lens_router  # noqa: E402
import loop  # noqa: E402


class TestTierResolver(unittest.TestCase):
    def setUp(self):
        # isolate the TASKPLANE_MODEL_* env across tests
        self._saved = {k: os.environ.get(k) for k in
                       ("TASKPLANE_MODEL_CHEAP", "TASKPLANE_MODEL_STANDARD",
                        "TASKPLANE_MODEL_DEEP")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_defaults_only_cheap_is_pinned(self):
        # portable default: only 'cheap' maps to a concrete model; the rest
        # inherit the session model (None) so nothing is forced.
        self.assertEqual(tp.model_for_tier("cheap"), "haiku")
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


class TestStepTier(unittest.TestCase):
    def test_reasoning_steps_default_deep(self):
        for s in ("pm", "plan", "em"):
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

    def test_dispatch_briefs_surface_resolved_model(self):
        routing = {"lenses": [
            {"id": "security", "name": "Security", "tier": "deep"},
            {"id": "code-quality", "name": "Code quality", "tier": "deep"},
            {"id": "product", "name": "Product", "tier": "sweep"},
        ], "context": {"changed_files": 3}}
        d = lens_router.dispatch_briefs(routing, base="HEAD")
        by_id = {b["id"]: b for b in d["deep"]}
        # every deep brief carries a tier + resolved model for the driver
        for b in d["deep"]:
            self.assertIn("model_tier", b)
            self.assertIn("model", b)
        self.assertEqual(by_id["security"]["model_tier"], "deep")
        self.assertEqual(by_id["code-quality"]["model_tier"], "standard")
        # the quick sweep runs cheap -> the one pinned model by default
        self.assertEqual(d["sweep"]["model_tier"], "cheap")
        self.assertEqual(d["sweep"]["model"], "haiku")


class TestLoopPayloadCarriesModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _git_ws(self):
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(ws, "plan"))
        os.makedirs(os.path.join(ws, "src"))
        open(os.path.join(ws, "src", "a.py"), "w").write("x=1\n")
        for a in (["init", "-q"], ["config", "user.email", "e@e"],
                  ["config", "user.name", "t"], ["add", "-A"],
                  ["commit", "-qm", "init"]):
            subprocess.run(["git", *a], cwd=ws, capture_output=True)
        return ws

    def test_plan_step_payload_has_model_fields(self):
        ws = self._git_ws()
        loop.init(ws, "add a feature")     # free-text -> pm
        loop.gate(ws, "pass")              # pm -> plan
        out = loop.next_action(ws)
        self.assertEqual(out["step"], "plan")
        self.assertIn("model_tier", out)
        self.assertIn("model", out)
        self.assertEqual(out["model_tier"], "deep")   # reasoning step
        self.assertIsNone(out["model"])               # inherit by default

    def test_execute_step_honors_a_cheap_task(self):
        ws = self._git_ws()
        json.dump({"tasks": [{"id": "t1", "scope": ["src/**"], "tests": "true",
                              "criteria": ["done"], "model": "cheap"}]},
                  open(os.path.join(ws, "plan", "tasks.json"), "w"))
        loop.init(ws, "simple mechanical change")
        loop.gate(ws, "pass")              # pm -> plan
        loop.gate(ws, "pass")              # plan -> plan_approval
        loop.approve(ws)                   # -> execute
        out = loop.next_action(ws)
        self.assertEqual(out["step"], "execute")
        self.assertEqual(out["model_tier"], "cheap")
        self.assertEqual(out["model"], "haiku")


if __name__ == "__main__":
    unittest.main()
