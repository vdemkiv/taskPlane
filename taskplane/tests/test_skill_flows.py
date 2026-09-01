"""Static contract for the ten human-approved taskPlane skill flows."""
import json
import os
import unittest
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    def test_build_flow_has_no_ceremonial_definition_gates(self):
        path = os.path.join(ROOT, "skills", "tp-build", "flow.json")
        with open(path, encoding="utf-8") as stream:
            flow = json.load(stream)
        gates = {row["id"] for row in flow["nodes"]
                 if row["kind"] == "gate"}
        self.assertEqual(gates, {"authorization", "selection", "signoff"})

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
            nodes = flow["nodes"]
            ids = [row["id"] for row in nodes]
            self.assertEqual(len(ids), len(set(ids)), skill)
            self.assertTrue(ids, skill)
            gates = {row["id"] for row in nodes if row["kind"] == "gate"}
            self.assertEqual(gates, EXPECTED_GATES[skill], skill)
            for src, dst in flow["edges"]:
                self.assertIn(src, ids, skill)
                self.assertIn(dst, ids, skill)
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
