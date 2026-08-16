"""Stop/SubagentStop preserve governed submission authority."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import taskplane_lite as tp
import tp as cli


class SubmissionStopFixture(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="tp-stop-")
        self.home = tempfile.mkdtemp(prefix="tp-stop-store-")
        self.env = mock.patch.dict(os.environ, {
            "TASKPLANE_HOME": self.home,
            "TASKPLANE_TASK": "t1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        subprocess.run(["git", "init", "-q"], cwd=self.ws, check=True)
        Path(self.ws, "a.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "add", "-A"], cwd=self.ws, check=True)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "base"], cwd=self.ws, check=True)
        self.snapshot = tp.git_head(self.ws)
        base = tp.build_contract("EXECUTE: t1", scope=["a.py"])
        self.contract = tp.bind_submission_contract(
            base, self.ws, task="t1", stage="execute", slot="t1",
            locator={"type": "loop_submission"},
            validation_rule="loop-submission/v1")
        tp.activate(self.ws, self.contract, snapshot=self.snapshot)

    def loop_state(self, submission=None):
        task = {"id": "t1", "status": "running"}
        if submission is not None:
            task["_submission"] = submission
        return {
            "submission_required": True,
            "step": "execute", "parallel": True,
            "current_task": 0, "tasks": [task],
        }

    def submission(self, **updates):
        row = {
            "step": "execute", "task": "t1", "outcome": "pass",
            "workspace": self.ws, "snapshot": self.snapshot,
            "fingerprint": tp.workspace_fingerprint(
                self.ws, self.snapshot),
            "evidence_paths": [],
        }
        row.update(updates)
        return row

    @staticmethod
    def digest(value) -> str:
        if isinstance(value, (dict, list)):
            raw = json.dumps(value, sort_keys=True,
                             separators=(",", ":")).encode()
        else:
            raw = Path(value).read_bytes()
        return hashlib.sha256(raw).hexdigest()


class TestMissingAndInvalidSubmissions(SubmissionStopFixture):
    def test_missing_submission_blocks_with_exact_recovery(self):
        status = tp.submission_status(
            self.ws, self.contract, observed_slot="t1",
            loop_state=self.loop_state())

        self.assertTrue(status["block"])
        self.assertEqual(status["status"], "missing")
        self.assertEqual(status["contract_id"], self.contract["task_id"])
        self.assertEqual(status["task"], "t1")
        self.assertEqual(status["stage"], "execute")
        self.assertEqual(status["slot"], "t1")
        self.assertIn("loop submit", status["recovery"])
        self.assertIn("loop submission", status["artifact"])

    def test_wrong_slot_blocks_without_using_sibling_submission(self):
        state = self.loop_state()
        state["tasks"].append({
            "id": "t2", "status": "running",
            "_submission": self.submission(task="t2"),
        })

        status = tp.submission_status(
            self.ws, self.contract, observed_slot="t2", loop_state=state)

        self.assertTrue(status["block"])
        self.assertEqual(status["status"], "wrong_slot")

    def test_wrong_workspace_and_stage_block(self):
        for submission, expected in (
            (self.submission(workspace=tempfile.mkdtemp()), "wrong_workspace"),
            (self.submission(step="evaluate"), "wrong_stage"),
        ):
            with self.subTest(expected=expected):
                status = tp.submission_status(
                    self.ws, self.contract, observed_slot="t1",
                    loop_state=self.loop_state(submission))
                self.assertTrue(status["block"])
                self.assertEqual(status["status"], expected)

    def test_corrupt_submission_blocks(self):
        state = self.loop_state(submission="not-an-object")

        status = tp.submission_status(
            self.ws, self.contract, observed_slot="t1", loop_state=state)

        self.assertTrue(status["block"])
        self.assertEqual(status["status"], "corrupt")

    def test_stale_fingerprint_blocks(self):
        status = tp.submission_status(
            self.ws, self.contract, observed_slot="t1",
            loop_state=self.loop_state(self.submission(fingerprint="0" * 64)))

        self.assertTrue(status["block"])
        self.assertEqual(status["status"], "stale")

    def test_subagent_stop_command_blocks_without_mutating_state(self):
        state = self.loop_state()
        contract_path = tp.active_contract_path(self.ws, "t1")
        before = (self.digest(contract_path), self.digest(state))
        event = {"cwd": self.ws, "hook_event_name": "SubagentStop",
                 "task_slot": "t1", "agent_id": "a1"}
        status = cli._submission_stop_check(event, loop_state=state)
        out = io.StringIO()
        with redirect_stdout(out):
            cli._emit_submission_stop_block("SubagentStop", status)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["decision"], "block")
        self.assertIn(self.contract["task_id"], payload["reason"])
        self.assertIn("slot=t1", payload["reason"])
        self.assertIn("missing artifact", payload["reason"])
        self.assertEqual((self.digest(contract_path), self.digest(state)), before)


class TestSubmissionPreservation(SubmissionStopFixture):
    def test_valid_submission_allows_and_every_input_stays_byte_identical(self):
        state = self.loop_state(self.submission())
        contract_path = tp.active_contract_path(self.ws, "t1")
        before = (self.digest(contract_path), self.digest(state))

        first = tp.submission_status(
            self.ws, self.contract, observed_slot="t1", loop_state=state)
        second = tp.submission_status(
            self.ws, self.contract, observed_slot="t1", loop_state=state)

        self.assertTrue(first["valid"])
        self.assertFalse(first["block"])
        self.assertEqual(first, second)
        self.assertEqual((self.digest(contract_path), self.digest(state)), before)
        self.assertIsNotNone(tp.load_active(self.ws))

    def test_submission_required_contract_never_orphan_releases(self):
        self.contract["activated_pid"] = 999999999
        orphaned, reason = tp.orphan_status(
            self.ws, self.contract, now=10**12)

        self.assertFalse(orphaned)
        self.assertIn("submission-required", reason)

    def test_standalone_contract_keeps_existing_orphan_semantics(self):
        standalone = tp.build_contract("standalone", scope=["a.py"])
        standalone["activated_pid"] = 999999999

        orphaned, _ = tp.orphan_status(self.ws, standalone, now=10**12)

        self.assertTrue(orphaned)

    def test_explicit_no_submission_required_is_allowed(self):
        contract = tp.bind_submission_contract(
            tp.build_contract("standalone", scope=["a.py"]), self.ws,
            task="standalone", stage="execute", slot="t1",
            locator={"type": "loop_submission"},
            validation_rule="loop-submission/v1", required=False)

        status = tp.submission_status(
            self.ws, contract, observed_slot="t1", loop_state=None)

        self.assertEqual(status["status"], "not_required")
        self.assertFalse(status["block"])


class TestArtifactSubmission(SubmissionStopFixture):
    def test_review_artifact_requires_matching_receipt_and_digest(self):
        result = ".em-review/kernel-v2/results/lease.json"
        receipt = ".em-review/kernel-v2/receipts/lease.json"
        result_path = Path(self.ws, result)
        receipt_path = Path(self.ws, receipt)
        result_path.parent.mkdir(parents=True)
        receipt_path.parent.mkdir(parents=True)
        payload = {
            "schema": "taskplane.lens-slot-output/v2",
            "slot_id": "deep.security", "lease_fingerprint": "lease",
        }
        raw = json.dumps(payload, sort_keys=True).encode()
        result_path.write_bytes(raw)
        receipt_path.write_text(json.dumps({
            "schema": "taskplane.slot-write-observation/v3",
            "contract_task_slot": "review-lease",
            "result_path": result,
            "result_sha256": hashlib.sha256(raw).hexdigest(),
        }), encoding="utf-8")
        contract = tp.bind_submission_contract(
            tp.build_contract("review lens", read_only=True,
                              write_allow=[result]),
            self.ws, task="deep.security", stage="evaluate",
            slot="review-lease",
            locator={"type": "artifact", "path": result,
                     "receipt_path": receipt,
                     "schema": "taskplane.lens-slot-output/v2"},
            validation_rule="leased-review-result/v2")

        status = tp.submission_status(
            self.ws, contract, observed_slot="review-lease")

        self.assertTrue(status["valid"])
        self.assertFalse(status["block"])

    def test_review_artifact_without_receipt_blocks(self):
        result = ".em-review/result.json"
        path = Path(self.ws, result)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "schema": "taskplane.lens-slot-output/v2"}), encoding="utf-8")
        contract = tp.bind_submission_contract(
            tp.build_contract("review lens", read_only=True,
                              write_allow=[result]),
            self.ws, task="deep.security", stage="evaluate",
            slot="review-lease",
            locator={"type": "artifact", "path": result,
                     "receipt_path": ".em-review/missing-receipt.json",
                     "schema": "taskplane.lens-slot-output/v2"},
            validation_rule="leased-review-result/v2")

        status = tp.submission_status(
            self.ws, contract, observed_slot="review-lease")

        self.assertTrue(status["block"])
        self.assertEqual(status["status"], "unobserved")


class TestStableHookLauncherRecognition(unittest.TestCase):
    def test_repo_hook_launcher_preserves_control_plane_verbs(self):
        self.assertEqual(
            tp.taskplane_verb(
                "python3 .taskplane/codex-hook.py clear --approved-by user"),
            "clear")
        self.assertEqual(
            tp.taskplane_verb(
                "python3 .taskplane/codex-hook.py loop gate fail"),
            "loop")
        self.assertIsNone(tp.taskplane_verb(
            "python3 tools/codex-hook.py clear --approved-by user"))


if __name__ == "__main__":
    unittest.main()
