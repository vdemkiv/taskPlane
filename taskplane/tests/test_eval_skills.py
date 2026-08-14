"""The native skill evaluator always drives one immutable checkout bundle."""
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location(
    "eval_skills", os.path.join(ROOT, "scripts", "eval_skills.py"))
eval_skills = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(eval_skills)


def write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(value)


class TestCheckoutLocalBundle(unittest.TestCase):
    def test_native_matrix_is_the_nine_exposed_codex_skills(self):
        self.assertEqual(set(eval_skills.NATIVE_SKILLS), {
            "taskplane", "tp-go", "tp-build", "tp-design", "tp-engineering",
            "tp-product", "tp-help", "tp-status", "tp-northstar",
        })
        self.assertNotIn("tp-tag", eval_skills.NATIVE_SKILLS)

    def test_stage_is_byte_identical_and_detects_later_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "source")
            workspace = os.path.join(tmp, "workspace")
            os.makedirs(workspace)
            write(os.path.join(source, ".codex-plugin", "plugin.json"),
                  json.dumps({"name": "taskplane", "version": "9.9.9"}))
            write(os.path.join(source, "skills", "taskplane", "SKILL.md"),
                  "# facade\n")
            write(os.path.join(source, "taskplane", "tp.py"), "# cli\n")
            staged = eval_skills.stage_bundle(source, workspace)
            self.assertEqual(staged["version"], "9.9.9")
            self.assertEqual(staged["fingerprint"],
                             eval_skills.bundle_fingerprint(source))
            write(os.path.join(staged["root"], "taskplane", "tp.py"),
                  "# modified\n")
            self.assertNotEqual(eval_skills.bundle_fingerprint(staged["root"]),
                                staged["fingerprint"])

    def test_manifest_pins_exact_skill_and_cli_in_the_staged_bundle(self):
        bundle = {"root": "/drive/.taskplane-eval/plugin",
                  "version": "2.14.1", "fingerprint": "abc"}
        manifest = eval_skills.skill_manifest(
            skill="tp-design", bundle=bundle, ws="/drive/checkout", host="codex")
        body = "\n".join(manifest["instructions"])
        self.assertEqual(manifest["bundle"]["skill_path"],
                         "../.taskplane-eval/plugin/skills/tp-design/SKILL.md")
        self.assertIn("../.taskplane-eval/plugin/taskplane/tp.py", body)
        self.assertIn("do not use an ambient installed plugin copy", body)
        self.assertIn("Never approve a human gate", body)

    def test_setup_resolves_private_storage_before_onboarding_check(self):
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(eval_skills.subprocess, "run",
                               return_value=completed) as run:
            got = eval_skills.prepare_fixture(
                bundle={"root": "/fixture/.taskplane-eval/plugin"},
                ws="/fixture", env={})
        self.assertEqual(got["returncode"], 0)
        self.assertEqual(run.call_count, 2)
        init = run.call_args_list[0].args[0]
        onboard = run.call_args_list[1].args[0]
        self.assertEqual(init[2:6], ["init", "--plan", "personal",
                                     "--workspace"])
        self.assertIn("--install-codex-hooks", onboard)
        self.assertNotIn("--init", onboard)
        self.assertNotIn("--knowledge-plan", onboard)

    def test_codex_adapter_uses_the_noninteractive_eval_boundary(self):
        argv = eval_skills.EvalCodexAdapter(
            executable="codex", model="gpt-test",
            reasoning_effort="high").argv("/fixture")
        self.assertIn("--ephemeral", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--approve-for-me", argv)
        self.assertIn("--dangerously-bypass-hook-trust", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-test")
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertEqual(argv[-1], "-")

    def test_claude_adapter_loads_only_the_staged_plugin_and_project_settings(self):
        argv = eval_skills.EvalClaudeAdapter(
            executable="claude", plugin_root="/drive/.taskplane-eval/plugin",
            model="sonnet", reasoning_effort="high").argv("/fixture")
        self.assertEqual(argv[argv.index("--plugin-dir") + 1],
                         "/drive/.taskplane-eval/plugin")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        self.assertEqual(argv[argv.index("--effort") + 1], "high")


if __name__ == "__main__":
    unittest.main()
