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
        self.assertIn("explicitly requests and authorizes every native", body)

    def test_advisory_manifest_does_not_invent_delegation(self):
        bundle = {"root": "/drive/.taskplane-eval/plugin",
                  "version": "2.14.1", "fingerprint": "f" * 64}
        manifest = eval_skills.skill_manifest(
            skill="tp-help", bundle=bundle, ws="/drive", host="codex")
        self.assertNotIn("subagent delegation", json.dumps(manifest))

    def test_engineering_manifest_pins_the_exact_fixture_comparison(self):
        bundle = {"root": "/drive/.taskplane-eval/plugin",
                  "version": "2.14.1", "fingerprint": "abc"}
        manifest = eval_skills.skill_manifest(
            skill="tp-engineering", bundle=bundle, ws="/drive/checkout",
            host="codex", base="base-sha", head="head-sha")
        body = "\n".join(manifest["instructions"])
        self.assertIn("head-sha", body)
        self.assertIn("base-sha", body)
        self.assertIn("do not substitute a branch name", body)

    def test_delivery_skills_assign_product_to_one_loop_phase(self):
        def text(rel):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as stream:
                return stream.read()

        go = " ".join(text("skills/tp-go/SKILL.md").split())
        build = " ".join(text("skills/tp-build/SKILL.md").split())
        design = " ".join(text("skills/tp-design/SKILL.md").split())
        self.assertIn("A goal with no existing R-id starts the loop without",
                      go)
        self.assertIn("Never run a standalone `req new` before this loop", go)
        self.assertIn("Do not run standalone `/tp-product`", build)
        self.assertIn("Continue the loop already initialized", build)
        self.assertIn("initialize once without `--req`", design)

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
        self.assertNotIn("--ephemeral", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--approve-for-me", argv)
        self.assertIn("--dangerously-bypass-hook-trust", argv)
        enabled = [argv[index + 1] for index, value in enumerate(argv)
                   if value == "--enable"]
        self.assertEqual(enabled, ["multi_agent", "multi_agent_v2"])
        disabled = [argv[index + 1] for index, value in enumerate(argv)
                    if value == "--disable"]
        self.assertEqual(disabled, ["plugins", "remote_plugin", "apps"])
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-test")
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertEqual(argv[-1], "-")

    def test_codex_eval_home_is_disposable_but_reuses_only_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "source")
            destination = os.path.join(tmp, "run")
            os.makedirs(source)
            with open(os.path.join(source, "auth.json"), "w") as stream:
                stream.write("{}")
            with mock.patch.object(eval_skills, "DEFAULT_CODEX_HOME", source):
                got = eval_skills.prepare_codex_home(destination)
            self.assertTrue(os.path.islink(os.path.join(got, "auth.json")))
            self.assertFalse(os.path.exists(os.path.join(got, "config.toml")))

    def test_codex_session_store_proves_native_parent_child_lifecycle(self):
        with tempfile.TemporaryDirectory() as home:
            path = os.path.join(home, "sessions", "2026", "08", "14",
                                "rollout-child.jsonl")
            write(path, "\n".join(json.dumps(row) for row in [
                {"timestamp": "2026-08-14T16:43:32Z",
                 "type": "session_meta", "payload": {
                     "id": "child-1", "source": {"subagent": {
                         "thread_spawn": {"parent_thread_id": "parent-1",
                                          "depth": 1,
                                          "agent_path": "/root/tp_step_product"}}}}},
                {"timestamp": "2026-08-14T16:43:33Z",
                 "type": "turn_context", "payload": {
                     "model": "gpt-test", "effort": "high"}},
                {"timestamp": "2026-08-14T16:44:00Z",
                 "type": "event_msg", "payload": {
                     "type": "task_complete", "completed_at": 1234}},
            ]) + "\n")
            got = eval_skills.codex_session_trace(home)
        self.assertEqual([row["event"] for row in got],
                         ["subagent_start", "subagent_stop"])
        self.assertEqual(got[0]["task_name"], "tp_step_product")
        self.assertEqual(got[0]["parent_thread_id"], "parent-1")
        self.assertEqual(got[0]["model"], "gpt-test")
        self.assertEqual(got[0]["reasoning_effort"], "high")
        self.assertTrue(got[0]["host_observed"])

    def test_codex_session_store_records_safe_derivations_from_exec_calls(self):
        with tempfile.TemporaryDirectory() as home, \
                tempfile.TemporaryDirectory() as workspace:
            path = os.path.join(home, "sessions", "2026", "08", "14",
                                "rollout-parent.jsonl")
            tool_input = (
                'const r = await tools.exec_command('
                '{"cmd":"python3 taskplane/tp.py review start HEAD --base '
                'HEAD^","workdir":"/checkout"});\ntext(r.output);')
            write(path, json.dumps({
                "timestamp": "2026-08-14T16:43:32Z",
                "type": "response_item", "payload": {
                    "type": "custom_tool_call", "name": "exec",
                    "input": tool_input}}) + "\n")
            got = eval_skills.codex_session_derivations(home, workspace)
        self.assertEqual(
            [(row["event"], row.get("verb"), row.get("key")) for row in got],
            [("command", "tp review start", None),
             ("derived", None, "diff"),
             ("derived", None, "impact")])
        self.assertTrue(all(row["host_observed"] for row in got))
        self.assertNotIn("HEAD", json.dumps(got))

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
