import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import eval_rubric as er  # noqa: E402
from .test_eval_workflow import GOOD, run  # noqa: E402


class TestRoutingCompliance(unittest.TestCase):
    def test_breadth_all_is_absolute_failure(self):
        rows = [dict(r) for r in GOOD]
        next(r for r in rows if r["event"] == "lens_route")["requested_breadth"] = "all"
        self.assertIn("breadth_all", er.absolute_compliance(run(rows))["failures"])

    def test_incomplete_routing_decision_is_absolute_failure(self):
        rows = [dict(r) for r in GOOD]
        next(r for r in rows if r["event"] == "lens_route")["complete"] = False
        self.assertIn("routing_incomplete", er.absolute_compliance(run(rows))["failures"])


if __name__ == "__main__": unittest.main()
