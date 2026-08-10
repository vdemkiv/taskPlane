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
                          text=True, check=False, encoding="utf-8")


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
            json.dump({"tasks": [task]}, f)
        result = loop.gate(self.ws, "pass")
        self.assertNotIn("error", result)
        self.assertEqual(loop.load(self.ws)["step"], "execute")

    def test_worker_submission_never_self_transitions_and_stale_work_blocks(self):
        self._plan_to_execute()
        loop.next_action(self.ws)
        path = os.path.join(self.ws, "src", "core", "a.py")
        with open(path, "a", encoding="utf-8") as f:
            f.write("VALUE_2 = 2\n")

        missing = loop.gate(self.ws, "pass")
        self.assertIn("not submitted", missing["error"])
        submitted = loop.submit(self.ws, "pass")
        self.assertTrue(submitted["submitted"])
        self.assertFalse(submitted["transitioned"])
        self.assertEqual(loop.load(self.ws)["step"], "execute")
        self.assertIsNotNone(tp.load_active(self.ws))

        with open(path, "a", encoding="utf-8") as f:
            f.write("VALUE_3 = 3\n")
        stale = loop.gate(self.ws, "pass")
        self.assertIn("changed after worker submission", stale["error"])
        loop.submit(self.ws, "pass")
        accepted = loop.gate(self.ws, "pass")
        self.assertEqual(accepted["step"], "evaluate")

    def test_evaluator_submission_is_bound_to_graph_revision(self):
        self._plan_to_execute()
        loop.next_action(self.ws)
        loop.submit(self.ws, "pass")
        loop.gate(self.ws, "pass")
        loop.next_action(self.ws)
        evidence_dir = os.path.join(self.ws, ".eval")
        os.makedirs(evidence_dir, exist_ok=True)
        with open(os.path.join(evidence_dir, "verdict.json"), "w", encoding="utf-8") as f:
            json.dump({"task": "t1", "verdict": "pass"}, f)
        loop.submit(self.ws, "pass")
        depgraph.record_edge(self.ws, "core", "contract:late-change",
                             kind="provides", confidence="high")
        stale = loop.gate(self.ws, "pass")
        self.assertIn("dependency graph changed after worker submission",
                      stale["error"])

    def test_rejected_plan_keeps_its_contract_active(self):
        loop.init(self.ws, "g", spec_path="specs/spec.md")
        loop.next_action(self.ws)
        self.assertIsNotNone(tp.load_active(self.ws))
        rejected = loop.gate(self.ws, "fail")
        self.assertIn("rejected", rejected["error"])
        self.assertEqual(loop.load(self.ws)["step"], "plan")
        self.assertIsNotNone(tp.load_active(self.ws))

    def test_evaluation_artifact_is_bound_to_submission_fingerprint(self):
        self._plan_to_execute()
        loop.next_action(self.ws)
        loop.submit(self.ws, "pass")
        loop.gate(self.ws, "pass")
        loop.next_action(self.ws)
        evidence_dir = os.path.join(self.ws, ".eval")
        os.makedirs(evidence_dir, exist_ok=True)
        verdict = os.path.join(evidence_dir, "verdict.json")
        with open(verdict, "w", encoding="utf-8") as f:
            json.dump({"task": "t1", "verdict": "pass"}, f)
        loop.submit(self.ws, "pass")
        with open(verdict, "w", encoding="utf-8") as f:
            json.dump({"task": "t1", "verdict": "fail"}, f)
        stale = loop.gate(self.ws, "pass")
        self.assertIn("changed after worker submission", stale["error"])

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
                "tests": "true", "contracts": []}
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

    def test_legacy_loop_without_submission_flag_remains_resumable(self):
        loop.save(self.ws, {
            "goal": "legacy", "step": "execute", "current_task": 0,
            "tasks": [{"id": "t1", "scope": ["src/core/**"],
                       "tests": "true", "criteria": ["works"],
                       "status": "pending", "fix_cycles": 0}],
            "baseline": "HEAD", "parallel": False,
            "max_fix_cycles": 1, "checkpoints": [],
        })
        loop.next_action(self.ws)
        result = loop.gate(self.ws, "pass")
        self.assertEqual(result["step"], "evaluate")


if __name__ == "__main__":
    unittest.main()
