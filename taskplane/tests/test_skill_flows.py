"""Static contract for the ten human-approved taskPlane skill flows."""
import json
import os
import shutil
import tempfile
import unittest
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import eval_scenario  # noqa: E402

SKILLS = ("taskplane", "tp-go", "tp-build", "tp-design", "tp-engineering",
          "tp-product", "tp-status", "tp-northstar", "tp-help", "tp-tag")
EXPECTED_GATES = {
    "taskplane": {"signoff"},
    "tp-go": {"authorization", "signoff"},
    "tp-build": {"authorization", "selection", "signoff"},
    "tp-design": {"approval"},
    "tp-engineering": {"signoff"},
    "tp-product": {"approval"},
    "tp-status": set(),
    "tp-northstar": set(),
    "tp-help": set(),
    "tp-tag": {"authorization", "signoff"},
}


class TestApprovedSkillFlows(unittest.TestCase):
    def test_canonical_harness_uses_one_consolidated_authorization(self):
        path = os.path.join(
            ROOT, "skills", "taskplane", "references", "harness-rules.md")
        with open(path, encoding="utf-8") as stream:
            rules = stream.read()
        self.assertIn("One consolidated explicit\n   human authorization", rules)
        self.assertIn("mechanical fail-closed checks", rules)
        self.assertIn("do not create separate ceremonial approval stops", rules)
        self.assertNotIn("Design Contract\n   approval, plan approval", rules)
        self.assertNotIn("requires fresh Plan approval", rules)

    def test_build_flow_has_no_ceremonial_definition_gates(self):
        path = os.path.join(ROOT, "skills", "tp-build", "flow.json")
        with open(path, encoding="utf-8") as stream:
            flow = json.load(stream)
        gates = {row["id"] for row in flow["nodes"]
                 if row["kind"] == "gate"}
        self.assertEqual(gates, {"authorization", "selection", "signoff"})
        self.assertNotIn("design_approval", {row["id"] for row in flow["nodes"]})
        self.assertNotIn("plan_approval", {row["id"] for row in flow["nodes"]})

    def test_delivery_flows_finish_with_retro_after_signoff(self):
        for skill in ("tp-go", "tp-build"):
            with open(os.path.join(ROOT, "skills", skill, "flow.json"),
                      encoding="utf-8") as stream:
                flow = json.load(stream)
            ids = {row["id"] for row in flow["nodes"]}
            edges = {tuple(row) for row in flow["edges"]}
            self.assertIn("retro", ids, skill)
            self.assertIn(("signoff", "retro"), edges, skill)

    def test_all_ten_graphs_are_valid_and_documented(self):
        found = []
        for skill in SKILLS:
            path = os.path.join(ROOT, "skills", skill, "flow.json")
            self.assertTrue(os.path.isfile(path), skill)
            with open(path, encoding="utf-8") as stream:
                flow = json.load(stream)
            found.append(flow["skill"])
            self.assertEqual(flow["schema"], "taskplane.skill-flow/v1")
            self.assertEqual(flow["skill"], skill)
            self.assertEqual(flow["approval"], {
                "status": "human-approved", "scope": "all-10",
                "date": "2026-08-14"})
            nodes = flow["nodes"]
            ids = [row["id"] for row in nodes]
            self.assertEqual(len(ids), len(set(ids)), skill)
            self.assertTrue(ids, skill)
            gates = {row["id"] for row in nodes if row["kind"] == "gate"}
            self.assertEqual(gates, EXPECTED_GATES[skill], skill)
            for src, dst in flow["edges"]:
                self.assertIn(src, ids, skill)
                self.assertIn(dst, ids, skill)
            with open(os.path.join(ROOT, "skills", skill, "SKILL.md"),
                      encoding="utf-8") as stream:
                self.assertIn("flow.json", stream.read(), skill)
        self.assertEqual(found, list(SKILLS))

    def test_approved_graphs_are_acyclic(self):
        for skill in SKILLS:
            with open(os.path.join(ROOT, "skills", skill, "flow.json"),
                      encoding="utf-8") as stream:
                flow = json.load(stream)
            remaining = {row["id"] for row in flow["nodes"]}
            edges = {tuple(edge) for edge in flow["edges"]}
            while remaining:
                roots = {node for node in remaining
                         if not any(dst == node and src in remaining
                                    for src, dst in edges)}
                self.assertTrue(roots, f"cycle in {skill}")
                remaining -= roots

    def test_any_approved_edge_change_moves_the_eval_fingerprint(self):
        root = tempfile.mkdtemp(prefix="tp-approved-flow-")
        self.addCleanup(shutil.rmtree, root, True)
        rel = "skills/taskplane/flow.json"
        target = os.path.join(root, rel)
        os.makedirs(os.path.dirname(target))
        shutil.copyfile(os.path.join(ROOT, rel), target)
        before = eval_scenario.fingerprint(root, [rel])
        with open(target, encoding="utf-8") as stream:
            flow = json.load(stream)
        flow["edges"] = flow["edges"][:-1]
        with open(target, "w", encoding="utf-8") as stream:
            json.dump(flow, stream)
        self.assertNotEqual(before, eval_scenario.fingerprint(root, [rel]))
