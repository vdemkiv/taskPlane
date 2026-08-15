"""Exactly-once Codex hook selection and bounded duplicate claims."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import taskplane_lite as tp
import tp as cli


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

    def test_repository_manifest_names_bridge_fallback(self):
        commands = self._commands(".codex/hooks.json")
        self.assertTrue(commands)
        self.assertTrue(all("TASKPLANE_HOOK_PATH=bridge" in command
                            for command in commands))


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
