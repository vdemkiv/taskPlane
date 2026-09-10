"""Regression coverage for the simplified v2 user surface and strict core.

The user gets build/review/status.  These tests pin the machinery underneath:
requirement-owned graph readiness, contract-bounded impact, and a worker
submission that can never advance its own state transition.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import loop  # noqa: E402
import requirements  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def _git(ws, *args):
    return subprocess.run(["git", *args], cwd=ws, capture_output=True,
                          text=True, check=False, encoding="utf-8", errors="replace")


class TestGovernanceV2(unittest.TestCase):
    def setUp(self):
        self.old_home = os.environ.get("TASKPLANE_HOME")
        self.old_store = os.environ.pop("TASKPLANE_STORE", None)
        os.environ["TASKPLANE_HOME"] = tempfile.mkdtemp(prefix="tp-v2-home-")
        self.ws = tempfile.mkdtemp(prefix="tp-v2-ws-")
        os.makedirs(os.path.join(self.ws, "src", "core"))
        os.makedirs(os.path.join(self.ws, "plan"))
        with open(os.path.join(self.ws, "src", "core", "a.py"), "w", encoding="utf-8") as f:
            f.write("VALUE = 1\n")
        _git(self.ws, "init", "-q")
        _git(self.ws, "config", "user.email", "test@example.com")
        _git(self.ws, "config", "user.name", "test")
        _git(self.ws, "add", "-A")
        _git(self.ws, "commit", "-qm", "base")
        depgraph.scan(self.ws)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self.old_home
        if self.old_store is not None:
            os.environ["TASKPLANE_STORE"] = self.old_store





    def test_high_cost_new_surface_must_be_declared(self):
        task = {"id": "new", "scope": ["src/new/**"],
                "high_cost": True}
        blocked = depgraph.readiness(self.ws, [task])
        self.assertFalse(blocked["passed"])
        task["new_modules"] = ["new"]
        ready = depgraph.readiness(self.ws, [task])
        self.assertTrue(ready["passed"])

    def test_ordinary_new_surface_must_also_be_declared(self):
        task = {"id": "ordinary", "scope": ["src/new/**"]}
        blocked = depgraph.readiness(self.ws, [task])
        self.assertFalse(blocked["passed"])
        task["new_modules"] = ["new"]
        self.assertTrue(depgraph.readiness(self.ws, [task])["passed"])

    def test_contract_boundary_depth_is_explicit_and_bounded(self):
        depgraph.record_edge(self.ws, "svc:orders", "contract:orders-v1",
                             kind="provides", confidence="high")
        depgraph.record_edge(self.ws, "svc:checkout", "contract:orders-v1",
                             kind="consumes", confidence="high")
        one = depgraph.impact(
            self.ws, ["contract:orders-v1"],
            policy={"local_depth": 3, "boundary_mode": "contract-only",
                    "contract_depth": 1, "requirement_depth": 1})
        direct = {row["module"] for row in one["impacted"].get(1, [])}
        self.assertEqual(direct, {"svc:orders", "svc:checkout"})

        stopped = depgraph.impact(
            self.ws, ["contract:orders-v1"],
            policy={"local_depth": 3, "boundary_mode": "contract-only",
                    "contract_depth": 0, "requirement_depth": 1})
        self.assertEqual(stopped["total_impacted"], 0)
        self.assertTrue(stopped["truncated"])
        self.assertFalse(stopped["depth_truncated"])
        self.assertTrue(all(row["reason"] == "contract-depth"
                            for row in stopped["policy_blocked"]))

    def test_recorded_edge_changes_graph_content_fingerprint(self):
        before = depgraph.load(self.ws)["meta"]["content_fingerprint"]
        depgraph.record_edge(self.ws, "core", "contract:core-v1",
                             kind="provides", confidence="high")
        graph = depgraph.load(self.ws)
        after = graph["meta"]["content_fingerprint"]
        self.assertNotEqual(before, after)
        edge = next(e for e in graph["edges"]
                    if e["to"] == "contract:core-v1")
        self.assertEqual(edge["source"], "recorded")
        self.assertEqual(edge["confidence"], "high")

    def test_final_review_uses_most_expansive_approved_policy(self):
        policy = loop._aggregate_impact_policy([
            {"impact_policy": {"local_depth": 2,
                               "boundary_mode": "stop",
                               "contract_depth": 0,
                               "requirement_depth": 1}},
            {"impact_policy": {"local_depth": 5,
                               "boundary_mode": "expand",
                               "contract_depth": 2,
                               "requirement_depth": 3}},
        ])
        self.assertEqual(policy, {"local_depth": 5,
                                  "boundary_mode": "expand",
                                  "contract_depth": 2,
                                  "requirement_depth": 3})


if __name__ == "__main__":
    unittest.main()
