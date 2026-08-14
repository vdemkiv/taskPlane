import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import eval_rubric as er  # noqa: E402
from .test_eval_workflow import GOOD, run  # noqa: E402


class TestGraphCompliance(unittest.TestCase):
    def test_scanned_head_must_equal_evaluated_head(self):
        rows = [dict(r) for r in GOOD]
        next(r for r in rows if r["event"] == "graph_impact")["scanned_head"] = "other"
        self.assertIn("graph_head_mismatch", er.absolute_compliance(run(rows))["failures"])

    def test_incomplete_impact_dispositions_block(self):
        rows = [dict(r) for r in GOOD]
        next(r for r in rows if r["event"] == "graph_impact")["dispositions_complete"] = False
        self.assertIn("impact_dispositions_incomplete", er.absolute_compliance(run(rows))["failures"])

    def test_impact_must_precede_dispatch(self):
        rows = [dict(r) for r in GOOD]
        next(r for r in rows if r["event"] == "graph_impact")["ts"] = 6.5
        self.assertIn("impact_after_dispatch", er.absolute_compliance(run(rows))["failures"])


if __name__ == "__main__": unittest.main()
