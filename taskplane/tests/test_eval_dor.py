import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import eval_rubric as er  # noqa: E402
from .test_eval_workflow import GOOD, run  # noqa: E402


class TestDoRDoD(unittest.TestCase):
    def test_failed_dor_blocks(self):
        rows = [dict(r) for r in GOOD]
        next(r for r in rows if r["event"] == "dor")["ready"] = False
        self.assertIn("dor_failed", er.absolute_compliance(run(rows))["failures"])

    def test_failed_dod_blocks(self):
        rows = [dict(r) for r in GOOD]
        next(r for r in rows if r["event"] == "dod")["passed"] = False
        self.assertIn("dod_failed", er.absolute_compliance(run(rows))["failures"])


if __name__ == "__main__": unittest.main()
