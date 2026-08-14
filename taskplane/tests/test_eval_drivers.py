"""Native model transports share one canonical, bounded contract."""
import json
import os
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import eval_drivers as drivers  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import eval_record  # noqa: E402


class TestCanonicalTransport(unittest.TestCase):
    def test_claude_and_codex_receive_identical_pre_transport_bytes(self):
        value = {"routing": {"security": "light"}, "views": ["b", "a"]}
        seen = []

        def runner(**kw):
            seen.append((kw["host"], kw["input_bytes"]))
            return drivers.ProcessOutcome(status="success", returncode=0,
                                          stdout=b'{}\n', stderr=b'')

        for adapter in (drivers.ClaudeAdapter(runner=runner, executable="claude"),
                        drivers.CodexAdapter(runner=runner, executable="codex")):
            result = adapter.run(value, cwd=ROOT, timeout_s=1)
            self.assertEqual(result["status"], "success")
        self.assertEqual(seen[0][1], seen[1][1])
        self.assertEqual(seen[0][1], drivers.canonical_bytes(value))
        self.assertEqual(seen[0][1], b'{"routing":{"security":"light"},"views":["b","a"]}\n')

    def test_normalized_outcomes_ignore_only_transport_identity(self):
        value = {"context": "abc"}
        results = []
        def runner(**kw):
            return drivers.ProcessOutcome(status="success", returncode=0,
                                          stdout=b'{"ok":true}\n', stderr=b'')
        for adapter in (drivers.ClaudeAdapter(runner=runner, executable="claude"),
                        drivers.CodexAdapter(runner=runner, executable="codex")):
            results.append(adapter.run(value, cwd=ROOT, timeout_s=1))
        self.assertNotEqual(results[0]["host"], results[1]["host"])
        self.assertEqual(drivers.normalized_result(results[0]),
                         drivers.normalized_result(results[1]))

    def test_missing_cli_is_capability_unavailable_not_a_failed_run(self):
        got = drivers.CodexAdapter(executable="definitely-not-a-real-cli").run(
            {"scenario": "x"}, cwd=ROOT, timeout_s=1)
        self.assertEqual(got["status"], "capability_unavailable")
        self.assertFalse(got["attempted"])
        self.assertIsNone(got["returncode"])


class TestBoundedRunner(unittest.TestCase):
    def test_secret_marked_environment_values_never_reach_the_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "env.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write("import os,sys; sys.stdout.write('|'.join(sorted(os.environ)))")
            env = dict(os.environ, OPENAI_API_KEY="canary", ORDINARY_CANARY="visible")
            got = drivers.run_process(
                host="codex", argv=[sys.executable, script], input_bytes=b"",
                cwd=tmp, env=env, timeout_s=2)
        self.assertEqual(got.status, "success")
        self.assertNotIn(b"OPENAI_API_KEY", got.stdout)
        self.assertNotIn(b"canary", got.stdout)
        self.assertNotIn(b"ORDINARY_CANARY", got.stdout)

    def test_timeout_is_named_and_process_is_reaped(self):
        got = drivers.run_process(
            host="claude", argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            input_bytes=b"", cwd=ROOT, env=os.environ, timeout_s=.05)
        self.assertEqual(got.status, "timeout")
        self.assertTrue(got.terminated)
        self.assertIsNotNone(got.pid)

    def test_pre_cancelled_run_never_starts_a_process(self):
        cancel = threading.Event()
        cancel.set()
        got = drivers.run_process(
            host="codex", argv=[sys.executable, "-c", "raise SystemExit(9)"],
            input_bytes=b"", cwd=ROOT, env=os.environ, timeout_s=1,
            cancel=cancel)
        self.assertEqual(got.status, "cancelled")
        self.assertIsNone(got.pid)


class TestHookProof(unittest.TestCase):
    def test_hook_proof_requires_an_observed_enforcement_event(self):
        self.assertFalse(drivers.hook_proof([])["proved"])
        got = drivers.hook_proof([
            {"event": "contract_activated"},
            {"event": "hook_screen", "decision": "approve", "host": "codex"},
        ])
        self.assertTrue(got["proved"])
        self.assertEqual(got["event"], "hook_screen")


class TestRunV2Shape(unittest.TestCase):
    def test_native_attempt_has_named_schema_and_is_not_eligible_on_prose(self):
        class Fake:
            def run(self, body, **kw):
                return {"schema": drivers.SCHEMA, "host": "codex",
                        "status": "success", "attempted": True,
                        "returncode": 0, "stdout": "model says done",
                        "stderr": "", "canonical_input_sha256": drivers.digest(body),
                        "canonical_input_bytes": len(body),
                        "telemetry_method": "unavailable",
                        "efficiency": {"cli_count": 0, "emitted_bytes": 0,
                                       "repeated_derivation_bytes": 0,
                                       "dispatched_agent_count": 0,
                                       "prompt_view_bytes": 0,
                                       "artifact_render_bytes": 0,
                                       "duplicate_artifact_bytes": 0,
                                       "duplicate_html_emissions": 0}}
        with tempfile.TemporaryDirectory() as tmp:
            got = eval_record.record_run_v2(
                host="codex", root=ROOT, dest=os.path.join(tmp, "drive"),
                out_dir=os.path.join(tmp, "record"), run_id="v2-shape",
                manifest={"goal": "review"}, adapter=Fake(), model="m",
                reasoning_effort="high")
            run = got["run"]
            self.assertEqual(run["schema"], eval_record.RUN_SCHEMA_V2)
            self.assertFalse(run["baseline_eligible"])
            self.assertIn("ineligible", run["baseline_reason"])
            self.assertEqual(run["comparison_key"]["host"], "codex")
            self.assertEqual(run["comparison_key"]["telemetry_method"],
                             "unavailable")
            self.assertIn("stdout", run["driver"]["artifacts"])


if __name__ == "__main__":
    unittest.main()
