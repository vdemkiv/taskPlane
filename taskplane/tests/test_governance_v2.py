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

    def _plan_to_execute(self, task=None):
        task = task or {"id": "t1", "scope": ["src/core/**"],
                        "tests": "true", "criteria": ["works"]}
        loop.init(self.ws, "governed change", spec_path="specs/spec.md",
                  checkpoints=["em"])
        loop.next_action(self.ws)
        with open(os.path.join(self.ws, "plan", "tasks.json"), "w", encoding="utf-8") as f:
            json.dump({"requirement": "governance-v2-fixture",
                       "delivery_mode": "build", "automatic_lenses": [],
                       "plan_authority": "human:test-fixture",
                       "tasks": [task]}, f)
        result = loop.gate(self.ws, "pass")
        self.assertNotIn("error", result)
        self.assertEqual(loop.load(self.ws)["step"], "execute")

    def test_rejected_plan_keeps_its_contract_active(self):
        loop.init(self.ws, "g", spec_path="specs/spec.md")
        loop.next_action(self.ws)
        self.assertIsNotNone(tp.worker_contract_for_stage(
            self.ws, stage="plan", task="plan"))
        rejected = loop.gate(self.ws, "fail")
        self.assertIn("rejected", rejected["error"])
        self.assertEqual(loop.load(self.ws)["step"], "plan")
        self.assertIsNotNone(tp.worker_contract_for_stage(
            self.ws, stage="plan", task="plan"))

    def test_requirement_contracts_cannot_be_erased_by_plan(self):
        base = requirements.record_requirement(
            self.ws, "base capability", acceptance=["base stays valid"])
        child = requirements.record_requirement(
            self.ws, "distributed capability", acceptance=["works"],
            depends_on=[base["id"]],
            contracts=[{"relation": "changes",
                        "id": "contract:orders-v1"}])
        task = {"id": "t1", "req": child["id"],
                "type": "distributed", "scope": ["src/core/**"],
                "tests": "true", "criteria": ["works"],
                "contracts": []}
        self._plan_to_execute(task)
        inherited = loop.load(self.ws)["tasks"][0]["contracts"]
        self.assertEqual(inherited[0]["id"], "contract:orders-v1")

    def test_missing_requirement_dependency_blocks_graph_dor(self):
        child = requirements.record_requirement(
            self.ws, "unsafe dependency", acceptance=["works"],
            depends_on=["R-9999"],
            contracts=[{"relation": "changes", "id": "contract:x"}])
        task = {"id": "t1", "req": child["id"],
                "type": "distributed", "scope": ["src/core/**"],
                "tests": "true"}
        loop.init(self.ws, "g", spec_path="specs/spec.md")
        loop.next_action(self.ws)
        with open(os.path.join(self.ws, "plan", "tasks.json"), "w", encoding="utf-8") as f:
            json.dump({"tasks": [task]}, f)
        blocked = loop.gate(self.ws, "pass")
        self.assertIn("requirement dependency R-9999", " ".join(
            blocked["dor"]["blockers"]))

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
