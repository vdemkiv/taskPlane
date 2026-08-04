"""Codex host compatibility for taskplane's shared enforcement boundary."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tp  # noqa: E402

TPPY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tp.py")


def _repo():
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "a.py"), "w") as f:
        f.write("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                    "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                    "commit", "-qm", "base"], cwd=ws, check=True)
    return ws


def _patch(*paths):
    chunks = ["*** Begin Patch"]
    for path in paths:
        chunks.extend([f"*** Update File: {path}", "@@", "-x = 1", "+x = 2"])
    chunks.append("*** End Patch")
    return "\n".join(chunks)


class TestCodexApplyPatch(unittest.TestCase):
    def setUp(self):
        self.ws = _repo()

    def test_edit_alias_allows_in_scope_patch(self):
        contract = tp.build_contract("t", scope=["src/**"],
                                     tools=["Edit"])
        ok, _ = tp.screen_tool(contract, "apply_patch",
                               {"command": _patch("src/a.py")}, self.ws)
        self.assertTrue(ok)

    def test_every_patch_target_is_screened(self):
        contract = tp.build_contract("t", scope=["src/**"],
                                     tools=["Write"])
        ok, reason = tp.screen_tool(
            contract, "apply_patch",
            {"command": _patch("src/a.py", "docs/outside.md")}, self.ws)
        self.assertFalse(ok)
        self.assertIn("docs/outside.md", reason)

    def test_move_destination_is_screened(self):
        contract = tp.build_contract("t", scope=["src/**"])
        body = ("*** Begin Patch\n*** Update File: src/a.py\n"
                "*** Move to: docs/a.py\n@@\n-x = 1\n+x = 2\n"
                "*** End Patch")
        ok, reason = tp.screen_tool(contract, "apply_patch",
                                    {"command": body}, self.ws)
        self.assertFalse(ok)
        self.assertIn("docs/a.py", reason)

    def test_opaque_patch_fails_closed_when_governed(self):
        contract = tp.build_contract("t", scope=["src/**"])
        ok, reason = tp.screen_tool(contract, "apply_patch",
                                    {"command": "not a patch"}, self.ws)
        self.assertFalse(ok)
        self.assertIn("screenable write target", reason)

    def test_read_only_patch_honors_artifact_allowlist(self):
        contract = tp.build_contract("t", scope=["**"], read_only=True,
                                     write_allow=[".eval/**"])
        ok, _ = tp.screen_tool(contract, "apply_patch",
                               {"command": _patch(".eval/verdict.json")},
                               self.ws)
        self.assertTrue(ok)
        ok, reason = tp.screen_tool(contract, "apply_patch",
                                    {"command": _patch("src/a.py")}, self.ws)
        self.assertFalse(ok)
        self.assertIn("read-only review contract", reason)


class TestCodexHookProtocol(unittest.TestCase):
    def setUp(self):
        self.ws = _repo()
        contract = tp.build_contract("t", scope=["src/**"],
                                     tools=["Edit"])
        tp.activate(self.ws, contract, snapshot=tp.git_head(self.ws))

    def _run(self, event):
        return subprocess.run([sys.executable, TPPY, "screen"],
                              cwd=self.ws, input=json.dumps(event), text=True,
                              capture_output=True)

    def test_codex_allow_is_silent(self):
        event = {"turn_id": "turn-1", "cwd": self.ws,
                 "tool_name": "apply_patch",
                 "tool_input": {"command": _patch("src/a.py")}}
        result = self._run(event)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_claude_allow_keeps_legacy_approve(self):
        event = {"cwd": self.ws, "tool_name": "Edit",
                 "tool_input": {"file_path": "src/a.py"}}
        result = self._run(event)
        self.assertEqual(json.loads(result.stdout), {"decision": "approve"})

    def test_codex_denial_uses_supported_legacy_block_shape(self):
        event = {"turn_id": "turn-1", "cwd": self.ws,
                 "tool_name": "apply_patch",
                 "tool_input": {"command": _patch("outside.py")}}
        result = self._run(event)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("outside.py", payload["reason"])


class TestSkillPortability(unittest.TestCase):
    def test_no_bare_claude_plugin_root_in_skills(self):
        # Codex does not set CLAUDE_PLUGIN_ROOT; every skill command must use
        # the ${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}} fallback so the very first
        # $TP invocation works on both hosts.
        import glob
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        offenders = []
        for f in glob.glob(os.path.join(root, "skills", "**", "*.md"),
                           recursive=True):
            body = open(f).read()
            bare = body.replace(
                "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}", "")
            if "${CLAUDE_PLUGIN_ROOT}" in bare:
                offenders.append(os.path.relpath(f, root))
        self.assertEqual(offenders, [])

    def test_no_bare_claude_plugin_root_in_agent_roles(self):
        # Codex dispatches these files as general-subagent role instructions.
        # Their contract/cleanup commands must work before any host-specific
        # environment variable is assumed.
        import glob
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        offenders = []
        for f in glob.glob(os.path.join(root, "agents", "*.md")):
            body = open(f).read()
            bare = body.replace(
                "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}", "")
            if "${CLAUDE_PLUGIN_ROOT}" in bare:
                offenders.append(os.path.relpath(f, root))
        self.assertEqual(offenders, [])

    def test_generated_lens_cleanup_is_host_portable(self):
        import lens
        self.assertIn("${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}",
                      lens.CLEAR_ALWAYS)


class TestCodexOnboarding(unittest.TestCase):
    def test_reports_codex_workspace_instructions(self):
        ws = tempfile.mkdtemp()
        env = {**os.environ, "CODEX_HOME": "/tmp/codex-test",
               "TASKPLANE_HOME": tempfile.mkdtemp()}
        result = subprocess.run(
            [sys.executable, TPPY, "onboard", "--json", "--workspace", ws],
            capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["host"], "codex")
        self.assertEqual(report["next_action"], "attach_folder")
        workspace = next(c for c in report["checks"]
                         if c["id"] == "workspace")
        self.assertIn("starting `codex`", workspace["hint"])
        self.assertIn("new task", workspace["hint"])


if __name__ == "__main__":
    unittest.main()
