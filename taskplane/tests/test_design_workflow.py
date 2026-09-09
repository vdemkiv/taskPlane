import json
import hashlib
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
        with open(os.path.join(self.ws, "src", "core", "a.py"), "w", encoding="utf-8") as f:
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
            nfr={"security": "no new trust boundary",
                 "architecture": "keep design in the governed loop"},
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
        acceptance_tests = {
            "design is approved before planning": [
                "taskplane/tests/test_stage_loop_integration.py::"
                "test_real_stage_completion_seals_design_outputs",
            ],
            "the proposed graph stays separate": [
                "taskplane/tests/test_dashboard_phase_graphs.py::"
                "test_design_graph_plan_dag_waves_and_module_impact_are_distinct",
            ],
        }
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
                 "validation": "state-machine regression test",
                 "tests": acceptance_tests[criterion]}
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
            "lens_evidence": [],
            "open_questions": []
        }
        state = loop.load(self.ws)
        plan = state["design_team_plan"]
        authority = plan["host_authority"]
        artifact_root = loop._run_artifact_root(self.ws, state)
        artifact_binding = state["run_artifact_binding"]
        for index, worker in enumerate(plan["workers"], start=1):
            expectation = tp.peek_expectation(
                self.ws, worker["task_name"], strict=True)
            self.assertIsNotNone(expectation)
            tp.record_design_dispatch_assignment_activity(
                self.ws, expectation)
            self.assertTrue(tp.commit_dispatch_verification(
                self.ws, worker["task_name"], worker["model"], expectation,
                True, worker["reasoning_effort"], strict=True))
            worker_contract = tp.build_contract(
                f"DESIGN LENS: {worker['lens']}", read_only=True,
                write_allow=[worker["output"]], tools=["Read", "Write"])
            worker_contract["task_id"] = worker["task_slot"]
            worker_contract = tp.prepare_worker_contract(
                self.ws, worker_contract, stage="design-lens",
                task=worker["lens"], task_name=worker["task_name"],
                role_marker=worker["role_marker"])
            worker_contract = tp.attach_design_lens_host_authority(
                worker_contract, authority["workers"][worker["lens"]],
                artifact_root=artifact_root,
                artifact_binding=artifact_binding)
            tp.activate(
                self.ws, worker_contract, snapshot=tp.git_head(self.ws),
                task_slot_override=worker["task_slot"])
            event = {
                "cwd": self.ws, "session_id": "design-workflow-session",
                "agent_id": f"design-workflow-agent-{index}",
                "agent_type": worker["task_name"],
                "task_name": worker["task_name"],
                "turn_id": f"design-workflow-turn-{index}",
            }
            host_binding = tp.bind_worker_contract_event(self.ws, event)
            tp.record_design_worker_start_activity(
                self.ws, host_binding, event)
            result_material = {
                "schema": "taskplane.design-lens-result/v1",
                "lens": worker["lens"],
                "worker_identity": worker["task_name"],
                "team_plan_fingerprint": plan["fingerprint"],
                "candidate_fingerprint": plan["candidate_fingerprint"],
                "outcome": "pass", "findings": [],
            }
            result_material["fingerprint"] = hashlib.sha256(json.dumps(
                result_material, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False).encode(
                    "utf-8")).hexdigest()
            result_path = os.path.join(self.ws, worker["output"])
            os.makedirs(os.path.dirname(result_path), exist_ok=True)
            with open(result_path, "w", encoding="utf-8") as stream:
                json.dump(result_material, stream)
            tp.terminalize_worker_contract(
                self.ws, {**event, "outcome": "success"},
                outcome="success", submission_status="not_required")
            contract["lens_evidence"].append({
                "lens": worker["lens"], "verdict": "pass", "blockers": 0,
                "evidence": "the assigned Design concern was checked",
                "produced_by": worker["task_name"], "independent": True,
            })
        os.makedirs(os.path.join(self.ws, "design"), exist_ok=True)
        with open(os.path.join(self.ws, "design", "design.md"), "w", encoding="utf-8") as f:
            f.write("# Governed Design\n\nUse an optional loop state.\n")
        if visualization_required:
            with open(os.path.join(self.ws, "design", "visual.html"), "w", encoding="utf-8") as f:
                f.write("<div>Product → Design → Approve → Plan</div>\n")
        # v2.3.0: lens evidence is bound to the exact design content judged.
        content_fingerprint = dc.design_content_fingerprint(self.ws, contract)
        for evidence in contract["lens_evidence"]:
            evidence["content_fingerprint"] = content_fingerprint
        with open(os.path.join(self.ws, "design", "contract.json"), "w", encoding="utf-8") as f:
            json.dump(contract, f, indent=2)
        return contract

    def _gate(self, outcome, **kwargs):
        if loop.load(self.ws)["step"] == "design":
            submitted = loop.submit(self.ws, outcome)
            self.assertTrue(submitted.get("submitted"), submitted)
        return loop.gate(self.ws, outcome, **kwargs)

    def test_design_submission_binds_ignored_outputs_before_gate(self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        action = loop.next_action(self.ws)
        slot = action["contract_bootstrap"]["task_slot"]
        bound = tp.load_json(tp.active_contract_path(self.ws, slot))
        self.assertEqual(bound["submission_contract"]["stage"], "design")
        with open(os.path.join(self.ws, ".git", "info", "exclude"), "a") as handle:
            handle.write("\ndesign/\n")
        self._write_design(visualization_required=True)
        self.assertIn("not submitted", loop.gate(self.ws, "pass")["error"])
        submitted = loop.submit(self.ws, "pass")
        self.assertTrue(submitted.get("submitted"), submitted)
        self.assertFalse(submitted["transitioned"])
        self.assertEqual(loop.load(self.ws)["step"], "design")
        self.assertIn("design/visual.html", submitted["submission"]["evidence_paths"])
        self.assertIn("does not match", loop.gate(self.ws, "fail")["error"])
        for relative in ("design/contract.json", "design/design.md", "design/visual.html",
                         "design/test-strategy.json"):
            with self.subTest(relative=relative):
                submitted = loop.submit(self.ws, "pass")
                self.assertTrue(submitted.get("submitted"), submitted)
                path = os.path.join(self.ws, relative)
                if os.path.exists(path):
                    with open(path, "rb") as handle:
                        before = handle.read()
                else:
                    before = None
                with open(path, "ab") as handle:
                    handle.write(b"\nchanged after submission\n")
                self.assertIn("changed after worker submission",
                              loop.gate(self.ws, "pass")["error"])
                if before is None:
                    os.unlink(path)
                else:
                    with open(path, "wb") as handle:
                        handle.write(before)
        self.assertTrue(loop.submit(self.ws, "pass")["submitted"])
        self.assertEqual(loop.gate(self.ws, "pass")["step"], "design_approval")

    def test_design_can_submit_failure_with_incomplete_artifacts(self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        loop.next_action(self.ws)
        os.makedirs(os.path.join(self.ws, "design"), exist_ok=True)
        with open(os.path.join(self.ws, "design", "contract.json"), "w") as handle:
            json.dump({"visualization": "invalid incomplete field"}, handle)
        self.assertIn("only valid for model evaluation",
                      loop.submit(self.ws, "unavailable")["error"])
        self.assertIn("does not match the current", loop.submit(
            self.ws, "fail", task_id="foreign-task")["error"])
        submitted = loop.submit(self.ws, "fail", "scope correction required")
        self.assertTrue(submitted.get("submitted"), submitted)
        self.assertEqual(loop.load(self.ws)["step"], "design")
        self.assertEqual(loop.gate(self.ws, "fail")["step"], "design")

    def test_default_loop_remains_product_to_plan(self):
        loop.init(self.ws, "ordinary build", requirement_id=self.req["id"])
        self.assertEqual(loop.load(self.ws)["step"], "pm")
        self.assertNotIn("design", [x[0] for x in loop.display_pipeline(
            loop.load(self.ws))])
        self._gate("pass")
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
        self._gate("pass")
        self.assertEqual(loop.load(self.ws)["step"], "design")

    def test_fresh_product_does_not_read_ambient_design_or_knowledge(self):
        from pathlib import Path
        from unittest import mock

        old_design = Path(self.ws) / "design" / "contract.json"
        old_design.parent.mkdir()
        old_design.write_text(json.dumps({
            "schema": "taskplane.design/v1", "requirement": "R-9999",
            "title": "UNRELATED_DESIGN_SENTINEL",
        }))
        old_context = Path(tp.kb_root(self.ws)) / "context" / "current-state.md"
        old_context.parent.mkdir(parents=True, exist_ok=True)
        old_context.write_text("# Old context\nSTALE_CONTEXT_SENTINEL\n")
        before = (old_design.read_bytes(), old_context.read_bytes())
        initialized = loop.init(self.ws, "fresh Product input", design=True)
        self.assertNotIn("error", initialized)
        with mock.patch.object(loop.kb, "retrieve", side_effect=AssertionError(
                "fresh Product must not discover old decisions")):
            action = loop.next_action(self.ws)
        self.assertEqual(action.get("role"), "tp-product", action.get("error"))
        self.assertIsNone(action["design"])
        self.assertEqual(action["knowledge"], {
            "decisions": [], "governing_decisions": [],
            "current_state": None, "context": ""})
        self.assertNotIn("UNRELATED_DESIGN_SENTINEL", json.dumps(action))
        self.assertNotIn("STALE_CONTEXT_SENTINEL", json.dumps(action))
        self.assertEqual(before, (old_design.read_bytes(), old_context.read_bytes()))
        self._gate("pass", rid=self.req["id"])
        with mock.patch.object(loop.kb, "retrieve", side_effect=AssertionError(
                "Design must not discover old decisions")), \
                mock.patch.object(loop.kb, "governing", side_effect=AssertionError(
                    "Design must not discover old governing decisions")), \
                mock.patch.object(loop.kb, "current_state", side_effect=AssertionError(
                    "Design must not read historical state")):
            successor = loop.next_action(self.ws)
        self.assertEqual(successor.get("role"), "tp-designer", successor.get("error"))
        self.assertIsNone(successor["design"])
        self.assertNotIn("UNRELATED_DESIGN_SENTINEL", json.dumps(successor))
        self.assertNotIn("STALE_CONTEXT_SENTINEL", json.dumps(successor))

    def test_product_requirement_attachment_preserves_artifact_owner(self):
        from pathlib import Path

        initialized = loop.init(self.ws, "Product creates the requirement", design=True)
        self.assertNotIn("error", initialized)
        original_binding = initialized["run_artifact_binding"]
        root = loop._run_artifact_root(self.ws, initialized)
        manifest = Path(root) / "run-artifacts.json"
        original_manifest = manifest.read_bytes()
        self.assertEqual(original_binding["candidate"]["requirement"], "")
        gated = self._gate("pass", rid=self.req["id"])
        self.assertEqual(gated.get("step"), "design", gated.get("error"))
        receipt, _policy = loop._prepare_design_control_plane(self.ws, loop.load(self.ws))
        self.assertEqual(receipt["status"], "ready")
        current = loop.load(self.ws)
        self.assertEqual(current["run_artifact_binding"], original_binding)
        self.assertEqual(current["design_control_plane_binding"]["requirement"],
                         self.req["id"])
        # Publication may append entries; ownership is immutable, and the
        # pre-Design manifest remains an authentic prefix of its classes.
        before = json.loads(original_manifest)
        after = json.loads(manifest.read_bytes())
        self.assertEqual(before["binding"], after["binding"])
        for name, rows in before["classes"].items():
            self.assertEqual(rows["entries"],
                             after["classes"][name]["entries"][:len(rows["entries"])])

    def test_design_dor_blocks_unresolved_requirement(self):
        unresolved = reqs.record_requirement(
            self.ws, "ambiguous", functional=["do it"],
            acceptance=["it works"], open_questions=["which boundary?"])
        loop.init(self.ws, "design ambiguous", spec_path="specs/x.md",
                  requirement_id=unresolved["id"], design=True)
        action = loop.next_action(self.ws)
        self.assertEqual(action["step"], "design")
        self.assertIn("unresolved", " ".join(action["dor"]["blockers"]))

    def test_changed_design_input_gets_fresh_native_workers_and_output_paths(self):
        from pathlib import Path

        loop.init(self.ws, "design isolated attempts", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        first = loop.next_action(self.ws)
        self.assertIsNone(first.get("error"))
        workers = first["design_lens_dispatches"]
        expected = tp.peek_expectation(self.ws, workers[0]["task_name"], strict=True)
        self.assertTrue(tp.native_worker_role_matches(
            self.ws, expected, workers[0]["task_name"]))
        self.assertFalse(tp.native_worker_role_matches(
            self.ws, {**expected, "intent_id": "0" * 64}, workers[0]["task_name"]))
        old_result = Path(self.ws) / workers[0]["output"]
        old_result.parent.mkdir(parents=True, exist_ok=True)
        old_result.write_text("retained first attempt")
        Path(self.ws, "src/core/a.py").write_text("VALUE = 2\n")
        second = loop.next_action(self.ws)
        later = second["design_lens_dispatches"]
        self.assertNotEqual(first["design_team_plan"]["fingerprint"],
                            second["design_team_plan"]["fingerprint"])
        for field in ("task_name", "task_slot", "output"):
            self.assertFalse({row[field] for row in workers} &
                             {row[field] for row in later}, field)
        self.assertEqual(old_result.read_text(), "retained first attempt")

    def test_design_gate_approves_and_fingerprints_evidence(self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        action = loop.next_action(self.ws)
        self.assertEqual(action["role"], "tp-designer")
        selected = {x["lens"] for x in action["design_lens_dispatches"]}
        state = loop.load(self.ws)
        self.assertEqual(selected, set(state["design_team_plan"]["selected"]))
        self.assertLessEqual(len(selected), 16)
        self.assertEqual(state["design_decomposition_receipt"]["status"],
                         "ready")
        solution = next(x for x in action["lenses"]
                        if x["id"] == "solution-design")
        self.assertNotEqual(solution["mode"], "none")
        self._write_design()
        gated = self._gate("pass")
        self.assertEqual(gated["step"], "design_approval")
        approved = loop.approve(self.ws, by="human — approved")
        self.assertEqual(approved["step"], "plan")
        state = loop.load(self.ws)
        self.assertEqual(len(state["design_fingerprint"]), 64)
        self.assertEqual(state["design_approved_by"], "human — approved")

    def test_design_gate_without_exact_acceptance_tests_records_no_authority(
            self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        loop.next_action(self.ws)
        contract = self._write_design()
        del contract["acceptance_map"][0]["tests"]
        contract["lens_evidence"][0]["content_fingerprint"] = \
            dc.design_content_fingerprint(self.ws, contract)
        with open(os.path.join(self.ws, "design", "contract.json"), "w",
                  encoding="utf-8") as f:
            json.dump(contract, f, indent=2)

        gated = self._gate("pass")

        self.assertEqual(gated["step"], "design")
        self.assertIn("acceptance criterion has no exact tests", " ".join(
            gated["dod"]["errors"]))
        state = loop.load(self.ws)
        self.assertNotIn("design_fingerprint", state)
        self.assertNotIn("design_approved_by", state)

    def test_design_only_finishes_after_approval(self):
        loop.init(self.ws, "design only", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True,
                  design_only=True)
        loop.next_action(self.ws)
        self._write_design(visualization_required=True)
        self.assertEqual(self._gate("pass")["step"],
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
        rejected = self._gate("pass")
        self.assertEqual(rejected["step"], "design")
        self.assertIn("as-built graph changed", " ".join(
            rejected["dod"]["errors"]))

    def test_plan_dor_covers_approved_design(self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        loop.next_action(self.ws)
        self._write_design()
        self._gate("pass")
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
        with open(os.path.join(self.ws, "design", "contract.json"), "w", encoding="utf-8") as f:
            json.dump(contract, f)
        gated = self._gate("pass")
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
        self._gate("pass")
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

def test_evaluate_evidence_children_do_not_count_as_catalog_lenses(
        tmp_path, monkeypatch):
    from taskplane import loop as public_loop, run_artifacts
    from taskplane.tests.test_evaluate_child_evidence import (
        ROOT, _binding, _impact, _run,
    )

    root, _ = _run(tmp_path, monkeypatch)
    assignments = public_loop.start_evaluate_evidence_children(
        workspace=str(ROOT), artifact_root=str(root), binding=_binding(),
        impact_manifest=_impact())
    entries = run_artifacts.load_manifest(root)["classes"][
        "agent-activity"]["entries"]
    assert len(assignments) == 2
    assert {row["metadata"]["lens"] for row in entries} == {
        "non-lens-language-code-quality", "non-lens-test-design"}
    assert all(assignment["capabilities"]["verdict"] is False
               for assignment in assignments)


if __name__ == "__main__":
    unittest.main()
