import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import design_contract as dc  # noqa: E402
import lens  # noqa: E402
import loop  # noqa: E402
import requirements as reqs  # noqa: E402
import taskplane_lite as tp  # noqa: E402


class DesignWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = os.path.join(self.tmp, "ws")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.ws, "src", "core"))
        with open(os.path.join(self.ws, "src", "core", "a.py"), "w") as f:
            f.write("VALUE = 1\n")
        subprocess.run(["git", "init", "-q"], cwd=self.ws, check=True)
        subprocess.run(["git", "config", "user.email", "e@example.com"],
                       cwd=self.ws, check=True)
        subprocess.run(["git", "config", "user.name", "test"],
                       cwd=self.ws, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.ws, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.ws,
                       check=True)
        self.old_home = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home
        # t9 (R-0011 E2): the pop below is a real mutation — a developer or
        # CI exporting TASKPLANE_STORE would have it silently deleted for
        # every LATER test module. Save it and restore in tearDown.
        self.old_store = os.environ.get("TASKPLANE_STORE")
        os.environ.pop("TASKPLANE_STORE", None)
        self.req = reqs.record_requirement(
            self.ws, "add design flow",
            functional=["users can design before build"],
            acceptance=["design is approved before planning",
                        "the proposed graph stays separate"],
            contracts=[{"relation": "provides",
                        "id": "contract:design-artifact"}],
            context_files=["src/core/**"])
        depgraph.scan(self.ws)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self.old_home
        if self.old_store is None:
            os.environ.pop("TASKPLANE_STORE", None)
        else:
            os.environ["TASKPLANE_STORE"] = self.old_store

    def _write_design(self, *, graph_fingerprint=None,
                      visualization_required=False):
        graph_fingerprint = graph_fingerprint or (
            depgraph.load(self.ws).get("meta") or {}).get(
                "content_fingerprint")
        contract = {
            "schema": "taskplane.design/v1",
            "requirement": self.req["id"],
            "title": "Governed design phase",
            "summary": "Add an optional design contract before planning.",
            "current_state": {
                "summary": "The loop currently moves from product to plan.",
                "sources": ["taskplane/loop.py", "src/core/a.py"]
            },
            "alternatives": [
                {"id": "state", "name": "Loop state",
                 "description": "Add Design to the existing loop.",
                 "tradeoffs": {"gains": ["one governance rail"],
                               "costs": ["state machine grows"],
                               "revisit_when": "Design needs its own lifecycle"}},
                {"id": "sidecar", "name": "Sidecar command",
                 "description": "Keep Design outside the loop.",
                 "tradeoffs": {"gains": ["smaller loop"],
                               "costs": ["approval can drift"],
                               "revisit_when": "Loop compatibility dominates"}}
            ],
            "selected_approach": "state",
            "decision": "Use an optional loop phase with explicit approval.",
            "modules": {"existing": ["taskplane"],
                        "new": ["skills/tp-design"]},
            "contracts": [
                {"relation": "provides", "id": "contract:design-artifact",
                 "description": "Machine-checkable approved design evidence"}
            ],
            "graph": {
                "baseline_fingerprint": graph_fingerprint,
                "proposed_modules": ["taskplane", "skills/tp-design"],
                "proposed_edges": [
                    {"from": "skills/tp-design", "to": "taskplane",
                     "kind": "runtime", "reason": "skill drives the loop"},
                    {"from": "taskplane", "to": "contract:design-artifact",
                     "kind": "provides",
                     "reason": "engine emits approved design evidence"}
                ],
                "depth_policy": {"local_depth": 3,
                                 "boundary_mode": "contract-only",
                                 "contract_depth": 1,
                                 "requirement_depth": 1},
                "dor": [{"check": "baseline graph is current",
                         "evidence": graph_fingerprint}],
                "dod": [{"check": "realized graph matches the proposal",
                         "evidence": "final engineering review"}]
            },
            "acceptance_map": [
                {"criterion": criterion,
                 "design_element": "design approval gate",
                 "validation": "state-machine regression test"}
                for criterion in self.req["acceptance"]
            ],
            "risks": [{"risk": "persisted-state regression",
                       "mitigation": "keep Design opt-in",
                       "owner": "engineering"}],
            "failure_modes": [
                {"mode": "design evidence changes after approval",
                 "detection": "fingerprint mismatch",
                 "recovery": "return to Design and approve again"}
            ],
            "observability": {"signals": ["design gate trace"],
                              "alerts": ["stale design rejection"]},
            "rollout": {"strategy": "opt-in CLI flag",
                        "rollback": "initialize without the flag"},
            "visualization": {
                "required": visualization_required,
                "kind": "state-transition" if visualization_required else "none",
                "path": "design/visual.html" if visualization_required else None,
                "reason": "The state rail materially clarifies the choice."
                if visualization_required else
                "The design document and graph edge are sufficient."
            },
            "lens_evidence": [
                {"lens": "solution-design", "verdict": "pass",
                 "blockers": 0,
                 "evidence": "alternatives, boundaries, and drift policy checked",
                 "produced_by": "tp-lens solution-design run",
                 "independent": True}
            ],
            "open_questions": []
        }
        os.makedirs(os.path.join(self.ws, "design"), exist_ok=True)
        with open(os.path.join(self.ws, "design", "design.md"), "w") as f:
            f.write("# Governed Design\n\nUse an optional loop state.\n")
        if visualization_required:
            with open(os.path.join(self.ws, "design", "visual.html"), "w") as f:
                f.write("<div>Product → Design → Approve → Plan</div>\n")
        # v2.3.0: lens evidence is bound to the exact design content judged.
        contract["lens_evidence"][0]["content_fingerprint"] = \
            dc.design_content_fingerprint(self.ws, contract)
        with open(os.path.join(self.ws, "design", "contract.json"), "w") as f:
            json.dump(contract, f, indent=2)
        return contract

    def test_default_loop_remains_product_to_plan(self):
        loop.init(self.ws, "ordinary build", requirement_id=self.req["id"])
        self.assertEqual(loop.load(self.ws)["step"], "pm")
        self.assertNotIn("design", [x[0] for x in loop.display_pipeline(
            loop.load(self.ws))])
        loop.gate(self.ws, "pass")
        self.assertEqual(loop.load(self.ws)["step"], "plan")

    def test_design_loop_routes_product_to_design(self):
        loop.init(self.ws, "design this", requirement_id=self.req["id"],
                  design=True)
        state = loop.load(self.ws)
        self.assertTrue(state["design_required"])
        self.assertEqual(state["step"], "pm")
        self.assertEqual([x[0] for x in loop.display_pipeline(state)][:5],
                         ["pm", "design", "design_approval", "plan",
                          "plan_approval"])
        loop.gate(self.ws, "pass")
        self.assertEqual(loop.load(self.ws)["step"], "design")

    def test_design_dor_blocks_unresolved_requirement(self):
        unresolved = reqs.record_requirement(
            self.ws, "ambiguous", functional=["do it"],
            acceptance=["it works"], open_questions=["which boundary?"])
        loop.init(self.ws, "design ambiguous", spec_path="specs/x.md",
                  requirement_id=unresolved["id"], design=True)
        action = loop.next_action(self.ws)
        self.assertEqual(action["step"], "design")
        self.assertIn("unresolved", " ".join(action["dor"]["blockers"]))

    def test_design_gate_approves_and_fingerprints_evidence(self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        action = loop.next_action(self.ws)
        self.assertEqual(action["role"], "tp-designer")
        self.assertEqual([x["id"] for x in action["lenses"]],
                         ["solution-design"])
        self._write_design()
        gated = loop.gate(self.ws, "pass")
        self.assertEqual(gated["step"], "design_approval")
        approved = loop.approve(self.ws, by="human — approved")
        self.assertEqual(approved["step"], "plan")
        state = loop.load(self.ws)
        self.assertEqual(len(state["design_fingerprint"]), 64)
        self.assertEqual(state["design_approved_by"], "human — approved")

    def test_design_only_finishes_after_approval(self):
        loop.init(self.ws, "design only", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True,
                  design_only=True)
        loop.next_action(self.ws)
        self._write_design(visualization_required=True)
        self.assertEqual(loop.gate(self.ws, "pass")["step"],
                         "design_approval")
        self.assertEqual(loop.approve(self.ws, by="human")["step"], "done")

    def test_design_gate_rejects_graph_mutation(self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        loop.next_action(self.ws)
        baseline = loop.load(self.ws)["design_graph_fingerprint"]
        self._write_design(graph_fingerprint=baseline)
        depgraph.record_edge(self.ws, "core", "contract:surprise",
                             kind="runtime")
        rejected = loop.gate(self.ws, "pass")
        self.assertEqual(rejected["step"], "design")
        self.assertIn("as-built graph changed", " ".join(
            rejected["dod"]["errors"]))

    def test_plan_dor_covers_approved_design(self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        loop.next_action(self.ws)
        self._write_design()
        loop.gate(self.ws, "pass")
        loop.approve(self.ws, by="human")
        state = loop.load(self.ws)
        state["tasks"] = [{"id": "t1", "scope": ["taskplane/**"],
                           "tests": "true", "req": self.req["id"],
                           "criteria": list(self.req["acceptance"]),
                           "contracts": [], "new_modules": [],
                           "impact_policy": {"local_depth": 1,
                                             "boundary_mode": "stop",
                                             "contract_depth": 0,
                                             "requirement_depth": 0}}]
        errors = loop._plan_dor_errors(self.ws, state)
        joined = " ".join(errors)
        self.assertIn("skills/tp-design", joined)
        self.assertIn("design edges", joined)
        self.assertIn("depth policy", joined)

    def test_malformed_design_fails_closed(self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        loop.next_action(self.ws)
        contract = self._write_design()
        contract["graph"] = []
        contract["observability"] = "trust me"
        contract["lens_evidence"][0]["blockers"] = "not-a-number"
        with open(os.path.join(self.ws, "design", "contract.json"), "w") as f:
            json.dump(contract, f)
        gated = loop.gate(self.ws, "pass")
        self.assertEqual(gated["step"], "design")
        joined = " ".join(gated["dod"]["errors"])
        self.assertIn("graph must be an object", joined)
        self.assertIn("observability must be an object", joined)
        self.assertIn("solution-design lens", joined)

    def test_review_requires_complete_design_conformance(self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        loop.next_action(self.ws)
        self._write_design()
        loop.gate(self.ws, "pass")
        loop.approve(self.ws, by="human")
        state = loop.load(self.ws)
        incomplete = loop._design_review_errors(
            self.ws, state,
            {"design": {"fingerprint": state["design_fingerprint"],
                        "verdict": "conformant"}})
        self.assertIn("modules_checked must be a list", " ".join(incomplete))
        depgraph.record_edge(self.ws, "skills/tp-design", "taskplane",
                             kind="runtime")
        depgraph.record_edge(self.ws, "taskplane",
                             "contract:design-artifact", kind="provides")
        complete = loop._design_review_errors(
            self.ws, state,
            {"design": {"fingerprint": state["design_fingerprint"],
                        "verdict": "conformant",
                        "modules_checked": ["taskplane", "skills/tp-design"],
                        "edges_checked": [
                            "skills/tp-design->taskplane:runtime",
                            "taskplane->contract:design-artifact:provides"],
                        "contracts_checked": ["contract:design-artifact"],
                        # v2.3.0: the contract: edge is scanner-invisible —
                        # it needs an explicit realization declaration.
                        "edge_evidence": [
                            {"edge": "taskplane->contract:design-artifact"
                                     ":provides",
                             "evidence": "loop.py design approval emits the "
                                         "artifact; regression test passes",
                             "declared_by": "reviewer — hand-recorded edge"}],
                        "drift": []}})
        self.assertEqual(complete, [])

    def test_design_evidence_is_not_silently_scope_exempt(self):
        self.assertIn("design/", lens.LOOP_OWNED)
        self.assertNotIn("design/", tp.RUNTIME_OWNED)
        # v2.3.0: specs/ became runtime-owned (the pm step authors it), but
        # design evidence stays governed — the guardrail this test pins.


if __name__ == "__main__":
    unittest.main()
