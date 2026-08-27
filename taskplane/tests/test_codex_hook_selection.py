"""Exactly-once Codex hook selection and bounded duplicate claims."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import taskplane_lite as tp
import tp as cli
import storage


ROOT = Path(__file__).resolve().parents[2]


class TestHookPathManifests(unittest.TestCase):
    def _commands(self, relative: str) -> list[str]:
        data = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        return [
            hook.get("command", "")
            for rows in data["hooks"].values()
            for row in rows
            for hook in row.get("hooks") or []
        ]

    def test_native_manifest_names_native_path(self):
        commands = self._commands("hooks/hooks.json")
        self.assertTrue(commands)
        self.assertTrue(all("TASKPLANE_HOOK_PATH=native" in command
                            for command in commands))

    def test_native_manifest_prefers_version_independent_workspace_runner(self):
        commands = [command for command in self._commands("hooks/hooks.json")
                    if "host_native_runtime.py" not in command]
        self.assertTrue(all('.taskplane/codex-hook.py' in command
                            for command in commands))
        self.assertTrue(all('[ -f ".taskplane/codex-hook.py" ]' in command
                            for command in commands))

    def test_cached_native_hook_runs_bridge_when_old_plugin_root_is_gone(self):
        manifest = json.loads((ROOT / "hooks" / "hooks.json").read_text(
            encoding="utf-8"))
        command = manifest["hooks"]["Stop"][0]["hooks"][0]["command"]
        with tempfile.TemporaryDirectory(prefix="tp-native-bridge-") as ws:
            Path(ws, ".taskplane").mkdir()
            marker = Path(ws, "called.json")
            Path(ws, ".taskplane", "codex-hook.py").write_text(
                "import json, os, sys\n"
                "with open(os.environ['TP_MARKER'], 'w', encoding='utf-8') as f:\n"
                "    json.dump({'argv': sys.argv[1:], "
                "'path': os.environ.get('TASKPLANE_HOOK_PATH')}, f)\n",
                encoding="utf-8")
            result = subprocess.run(
                command, cwd=ws, shell=True, text=True, capture_output=True,
                env={**os.environ, "PLUGIN_ROOT": os.path.join(ws, "removed"),
                     "TP_MARKER": str(marker)}, encoding="utf-8",
                errors="replace")
            self.assertEqual(result.returncode, 0, result.stderr)
            called = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(called, {
                "argv": ["session-verify"], "path": "native"})

    def test_repository_manifest_names_bridge_fallback(self):
        commands = self._commands(".codex/hooks.json")
        self.assertTrue(commands)
        self.assertTrue(all("TASKPLANE_HOOK_PATH=bridge" in command
                            for command in commands))
        governed = [command for command in commands
                    if "host_native_runtime.py" not in command]
        native_checks = [command for command in commands
                         if "host_native_runtime.py" in command]
        self.assertTrue(governed)
        self.assertTrue(all('[ -f ".taskplane/codex-hook.py" ]' in command
                            for command in governed))
        self.assertEqual(len(native_checks), 1)
        self.assertIn("check --host codex", native_checks[0])
        self.assertTrue(all("PLUGIN_ROOT" in command for command in commands))

    def test_repository_hook_uses_plugin_when_worktree_runner_is_missing(self):
        manifest = json.loads((ROOT / ".codex" / "hooks.json").read_text(
            encoding="utf-8"))
        command = manifest["hooks"]["Stop"][0]["hooks"][0]["command"]
        with tempfile.TemporaryDirectory(prefix="tp-worktree-hook-") as ws:
            plugin = Path(ws, "plugin")
            engine = plugin / "taskplane" / "tp.py"
            engine.parent.mkdir(parents=True)
            marker = Path(ws, "called.json")
            engine.write_text(
                "import json, os, sys\n"
                "with open(os.environ['TP_MARKER'], 'w', encoding='utf-8') as f:\n"
                "    json.dump({'argv': sys.argv[1:], "
                "'path': os.environ.get('TASKPLANE_HOOK_PATH')}, f)\n",
                encoding="utf-8")
            result = subprocess.run(
                command, cwd=ws, shell=True, text=True, capture_output=True,
                env={**os.environ, "PLUGIN_ROOT": str(plugin),
                     "TP_MARKER": str(marker)}, encoding="utf-8",
                errors="replace")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(marker.read_text(encoding="utf-8")), {
                "argv": ["session-verify"], "path": "bridge"})

    def test_primary_skills_prefer_workspace_launcher_for_version_refreshes(self):
        skills = (
            "taskplane", "tp-go", "tp-build", "tp-design", "tp-engineering",
            "tp-product", "tp-status", "tp-northstar", "tp-help",
        )
        for skill in skills:
            body = (ROOT / "skills" / skill / "SKILL.md").read_text(
                encoding="utf-8")
            self.assertIn(".taskplane/codex-hook.py", body, skill)
            self.assertIn("newest valid installed", " ".join(body.split()),
                          skill)

    def test_onboarding_preserves_bridge_identity_on_both_shells(self):
        with tempfile.TemporaryDirectory(prefix="tp-codex-hooks-") as ws:
            Path(ws, ".codex").mkdir()
            Path(ws, ".codex", "hooks.json").write_text(
                '{"hooks": {}}\n', encoding="utf-8")
            with mock.patch.dict(
                    os.environ,
                    {"CODEX_HOME": "/tmp/codex",
                     "TASKPLANE_MANAGED_HOOK_POLICY": "supported"},
                    clear=True), \
                    mock.patch.object(cli, "_install_context",
                                      return_value="personal"):
                cli._install_codex_hooks(ws)
            data = json.loads(Path(ws, ".codex", "hooks.json").read_text(
                encoding="utf-8"))
        hooks = [
            hook
            for rows in data["hooks"].values()
            for row in rows
            for hook in row.get("hooks") or []
        ]
        self.assertTrue(hooks)
        self.assertTrue(all("TASKPLANE_HOOK_PATH=bridge" in
                            hook.get("command", "") for hook in hooks))
        self.assertTrue(all('TASKPLANE_HOOK_PATH=bridge' in
                            hook.get("commandWindows", "")
                            for hook in hooks))
        governed_hooks = [
            hook for hook in hooks
            if "host_native_runtime.py" not in hook.get("command", "")
        ]
        self.assertTrue(all('if exist ".taskplane\\codex-hook.py"' in
                            hook.get("commandWindows", "")
                            for hook in governed_hooks))
        native = json.loads((ROOT / "hooks" / "hooks.json").read_text(
            encoding="utf-8"))
        native_hooks = [
            hook
            for rows in native["hooks"].values()
            for row in rows
            for hook in row.get("hooks") or []
        ]
        governed_hooks = [
            hook for hook in native_hooks
            if "host_native_runtime.py" not in hook.get("command", "")
        ]
        self.assertTrue(all('if exist ".taskplane\\codex-hook.py"' in
                            hook.get("commandWindows", "")
                            for hook in governed_hooks))

    def test_generated_workspace_hook_config_is_git_local_only(self):
        with tempfile.TemporaryDirectory(prefix="tp-codex-hooks-git-") as ws:
            subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
            with mock.patch.dict(
                    os.environ,
                    {"CODEX_HOME": "/tmp/codex",
                     "TASKPLANE_MANAGED_HOOK_POLICY": "supported"},
                    clear=True), \
                    mock.patch.object(cli, "_install_context",
                                      return_value="personal"):
                cli._install_codex_hooks(ws)

            ignored = subprocess.run(
                ["git", "check-ignore", "-q", ".codex/hooks.json"],
                cwd=ws)
            self.assertEqual(ignored.returncode, 0)


class TestHookEventClaims(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="tp-hook-claim-")
        self.home = tempfile.mkdtemp(prefix="tp-hook-store-")
        self.env = mock.patch.dict(
            os.environ,
            {"TASKPLANE_HOME": self.home, "CODEX_THREAD_ID": "thread-1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    @staticmethod
    def _event(**extra):
        return {
            "hook_event_name": "SubagentStop",
            "turn_id": "turn-1",
            "agent_id": "agent-1",
            **extra,
        }

    def test_native_then_bridge_executes_once_and_replays_class(self):
        first = tp.claim_hook_event(
            self.ws, "subagent-stop", self._event(), hook_path="native")
        self.assertTrue(first["execute"])
        tp.complete_hook_event(
            self.ws, first, response_class="block")

        duplicate = tp.claim_hook_event(
            self.ws, "subagent-stop", self._event(), hook_path="bridge")

        self.assertFalse(duplicate["execute"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["response_class"], "block")
        self.assertEqual(first["claim_id"], duplicate["claim_id"])

    def test_different_child_identity_is_not_a_duplicate(self):
        first = tp.claim_hook_event(
            self.ws, "subagent-stop", self._event(), hook_path="native")
        second = tp.claim_hook_event(
            self.ws, "subagent-stop", self._event(agent_id="agent-2"),
            hook_path="bridge")

        self.assertTrue(first["execute"])
        self.assertTrue(second["execute"])
        self.assertNotEqual(first["claim_id"], second["claim_id"])

    def test_unstable_identity_fails_closed_without_journal_entry(self):
        decision = tp.claim_hook_event(
            self.ws, "pre-tool-use", {"hook_event_name": "PreToolUse"},
            hook_path="native")

        self.assertFalse(decision["execute"])
        self.assertEqual(decision["status"], "identity_unavailable")
        self.assertEqual(decision["response_class"], "block")
        self.assertFalse(os.path.exists(tp.hook_claim_journal_path(self.ws)))

    def test_journal_contains_only_bounded_digest_and_response_metadata(self):
        secret = "secret-command-argument"
        event = self._event(tool_input={"command": secret}, prompt=secret)
        claim = tp.claim_hook_event(
            self.ws, "subagent-stop", event, hook_path="native")
        tp.complete_hook_event(self.ws, claim, response_class="allow")

        raw = Path(tp.hook_claim_journal_path(self.ws)).read_text(
            encoding="utf-8")
        journal = json.loads(raw)
        self.assertNotIn(secret, raw)
        self.assertLessEqual(len(journal["claims"]), tp.HOOK_CLAIM_CAP)
        row = journal["claims"][0]
        self.assertEqual(set(row), {
            "claim_id", "created_at", "updated_at", "status",
            "response_class", "hook_path",
        })
        self.assertRegex(row["claim_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            row["claim_id"], hashlib.sha256(
                tp.hook_event_identity(
                    self.ws, "subagent-stop", event).encode("utf-8")
            ).hexdigest())

    def test_completed_duplicate_is_idempotent_bytes(self):
        claim = tp.claim_hook_event(
            self.ws, "subagent-stop", self._event(), hook_path="native")
        tp.complete_hook_event(self.ws, claim, response_class="allow")
        path = Path(tp.hook_claim_journal_path(self.ws))
        before = path.read_bytes()

        duplicate = tp.claim_hook_event(
            self.ws, "subagent-stop", self._event(), hook_path="bridge")

        self.assertFalse(duplicate["execute"])
        self.assertEqual(path.read_bytes(), before)

    def test_cli_hook_wrapper_executes_native_and_bridge_event_once(self):
        subprocess.run(["git", "init", "-q"], cwd=self.ws, check=True)
        identity = storage.resolve_repository_identity(self.ws)
        layout = storage.resolve_layout(
            identity, run_id="run-hook-claim", home=self.home)
        storage.write_workspace_locator(
            self.ws, identity=identity, layout=layout,
            run_id="run-hook-claim")
        calls = []
        event = {
            "hook_event_name": "PreToolUse", "session_id": "s1",
            "tool_use_id": "call-1", "cwd": self.ws,
        }

        def handler(_args):
            calls.append("executed")
            print(json.dumps({"decision": "approve"}))
            return 0

        args = Namespace(cmd="screen", fn=handler, workspace=None)
        outputs = []
        for path in ("native", "bridge"):
            old = cli.sys.stdin
            cli.sys.stdin = io.StringIO(json.dumps(event))
            out = io.StringIO()
            try:
                with mock.patch.dict(os.environ,
                                     {"TASKPLANE_HOOK_PATH": path}), \
                        redirect_stdout(out):
                    self.assertEqual(cli._run_hook_command(args), 0)
            finally:
                cli.sys.stdin = old
            outputs.append(json.loads(out.getvalue()))
        self.assertEqual(calls, ["executed"])
        self.assertEqual(outputs[0]["decision"], "approve")
        self.assertEqual(outputs[1]["decision"], "approve")


if __name__ == "__main__":
    unittest.main()
