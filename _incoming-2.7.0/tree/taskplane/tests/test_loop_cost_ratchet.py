"""Per-task cost ratchet (P3, R-0012) — the guard that outlives the fixes.

The month-1 regression was not caused by any one change. Four defensible
local decisions multiplied into roughly thirteen times the per-task cost,
and nothing in the system was watching the product. This pins the product.

These tests do two jobs: prove the harness measures the real loop (not a
mock of it), and prove the ratchet actually FAILS when cost grows — a guard
that cannot fail is decoration.
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(HERE, "taskplane"))
sys.path.insert(0, os.path.join(HERE, "scripts"))

import ci_loop_cost  # noqa: E402


class TestTheHarnessMeasuresTheRealLoop(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        self.got = ci_loop_cost.measure()

    def test_one_task_costs_exactly_one_suite_execution(self):
        """The whole P1 claim in one number. If this ever reads 2, some
        caller started re-running identical content again."""
        self.assertEqual(self.got["suite_executions"], 1)

    def test_the_evaluation_cited_rather_than_re_ran(self):
        self.assertGreaterEqual(self.got["suite_citations"], 1)

    def test_the_loop_really_reached_the_end(self):
        """A harness that measured a loop which never completed would pin a
        flattering number. Gates counted must match the real transition
        count for one task."""
        self.assertEqual(self.got["gates"], 4)

    def test_every_measured_key_has_a_pin(self):
        for key in ("suite_executions", "engine_entrypoints", "gates"):
            self.assertIn(key, ci_loop_cost.PINS)


class TestTheRatchetActuallyBites(unittest.TestCase):
    def test_it_fails_when_a_measured_cost_exceeds_its_pin(self):
        inflated = {"suite_executions": 6, "engine_entrypoints": 64,
                    "gates": 4, "suite_citations": 0}
        with mock.patch.object(ci_loop_cost, "measure", return_value=inflated):
            self.assertEqual(ci_loop_cost.main(), 1)

    def test_it_fails_when_a_measurement_is_missing_entirely(self):
        """A harness that silently stopped measuring must fail closed, not
        report success on an empty reading."""
        with mock.patch.object(ci_loop_cost, "measure", return_value={}):
            self.assertEqual(ci_loop_cost.main(), 1)

    def test_it_passes_at_exactly_the_pin(self):
        at_pin = dict(ci_loop_cost.PINS)
        at_pin["suite_citations"] = 1
        with mock.patch.object(ci_loop_cost, "measure", return_value=at_pin):
            self.assertEqual(ci_loop_cost.main(), 0)

    def test_the_failure_message_names_the_way_out(self):
        """A ratchet that only says NO teaches nothing. This one has to say
        that raising the pin is allowed, and that it must be said out loud."""
        inflated = dict(ci_loop_cost.PINS)
        inflated["engine_entrypoints"] += 1
        import io
        buf = io.StringIO()
        with mock.patch.object(ci_loop_cost, "measure", return_value=inflated), \
                mock.patch("sys.stdout", buf):
            ci_loop_cost.main()
        text = buf.getvalue()
        self.assertIn("raise the pin", text)
        self.assertIn("on the record", text)


class TestTheScriptRunsStandalone(unittest.TestCase):
    def test_the_real_script_exits_zero_on_this_tree(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "scripts", "ci_loop_cost.py")],
            capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("per-task cost holds", proc.stdout)


if __name__ == "__main__":
    unittest.main()
