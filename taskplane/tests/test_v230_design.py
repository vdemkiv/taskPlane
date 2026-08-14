"""v2.3.0 design fix-wave regression tests (findings E1–E9).

Every test here asserts the STRICT behavior: the fixes tighten validation
(pin more, self-attest less) or improve messages — nothing makes an
approvable design easier to approve.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import design_contract as dc  # noqa: E402
import loop  # noqa: E402
import requirements as reqs  # noqa: E402


class _DesignEnv(unittest.TestCase):
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
        self._commit("init")
        self.old_home = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home
        # t9 (R-0011 E2): save-and-restore, not a bare pop — an exported
        # TASKPLANE_STORE would otherwise be deleted for every LATER module.
        self.old_store = os.environ.get("TASKPLANE_STORE")
        os.environ.pop("TASKPLANE_STORE", None)
        self.req = reqs.record_requirement(
            self.ws, "add governed design",
            functional=["users can design before build"],
            acceptance=["design is approved before planning",
                        "the proposed graph stays separate"],
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

    def _commit(self, msg):
        subprocess.run(["git", "add", "-A"], cwd=self.ws, check=True)
        subprocess.run(["git", "commit", "-qm", msg, "--allow-empty"],
                       cwd=self.ws, check=True)

    # ---------------------------------------------------------- fixtures

    def _graph_fp(self):
        return (depgraph.load(self.ws).get("meta") or {}).get(
            "content_fingerprint")

    def _base_contract(self, graph_fp=None):
        graph_fp = graph_fp or self._graph_fp()
        return {
            "schema": "taskplane.design/v1",
            "requirement": self.req["id"],
            "title": "Governed design phase",
            "summary": "Add an optional design contract before planning.",
            "current_state": {
                "summary": "The loop moves from product straight to plan.",
                "sources": ["taskplane/loop.py", "src/core/a.py"]},
            "alternatives": [
                {"id": "state", "name": "Loop state",
                 "description": "Add Design to the existing loop.",
                 "tradeoffs": {"gains": ["one governance rail"],
                               "costs": ["state machine grows"],
                               "revisit_when": "Design needs its own "
                                               "lifecycle"}},
                {"id": "sidecar", "name": "Sidecar command",
                 "description": "Keep Design outside the loop.",
                 "tradeoffs": {"gains": ["smaller loop"],
                               "costs": ["approval can drift"],
                               "revisit_when": "Loop compatibility "
                                               "dominates"}}],
            "selected_approach": "state",
            "decision": "Use an optional loop phase with explicit approval.",
            "modules": {"existing": ["taskplane"],
                        "new": ["skills/tp-design"]},
            "contracts": [
                {"relation": "provides", "id": "contract:design-artifact",
                 "description": "Machine-checkable approved design "
                                "evidence"}],
            "graph": {
                "baseline_fingerprint": graph_fp,
                "proposed_modules": ["taskplane", "skills/tp-design"],
                "proposed_edges": [
                    {"from": "skills/tp-design", "to": "taskplane",
                     "kind": "runtime", "reason": "skill drives the loop"},
                    {"from": "taskplane", "to": "contract:design-artifact",
                     "kind": "provides",
                     "reason": "engine emits approved design evidence"}],
                "depth_policy": {"local_depth": 3,
                                 "boundary_mode": "contract-only",
                                 "contract_depth": 1,
                                 "requirement_depth": 1},
                "dor": [{"check": "baseline graph is current",
                         "evidence": graph_fp}],
                "dod": [{"check": "realized graph matches the proposal",
                         "evidence": "final engineering review"}]},
            "acceptance_map": [
                {"criterion": criterion,
                 "design_element": "design approval gate",
                 "validation": "state-machine regression test"}
                for criterion in self.req["acceptance"]],
            "risks": [{"risk": "persisted-state regression",
                       "mitigation": "keep Design opt-in",
                       "owner": "engineering"}],
            "failure_modes": [
                {"mode": "design evidence changes after approval",
                 "detection": "fingerprint mismatch",
                 "recovery": "return to Design and approve again"}],
            "observability": {"signals": ["design gate trace"],
                              "alerts": ["stale design rejection"]},
            "rollout": {"strategy": "opt-in CLI flag",
                        "rollback": "initialize without the flag"},
            "visualization": {"required": False, "kind": "none",
                              "path": None,
                              "reason": "The design document and graph "
                                        "edge are sufficient."},
            "lens_evidence": [
                {"lens": "solution-design", "verdict": "pass",
                 "blockers": 0,
                 "evidence": "alternatives, boundaries, drift checked",
                 "produced_by": "tp-lens solution-design run",
                 "independent": True}],
            "open_questions": [],
        }

    def _write_design(self, contract, *, bind=True):
        os.makedirs(os.path.join(self.ws, "design"), exist_ok=True)
        with open(os.path.join(self.ws, "design", "design.md"), "w", encoding="utf-8") as f:
            f.write("# Governed Design\n\nUse an optional loop state.\n")
        if bind:
            contract["lens_evidence"][0]["content_fingerprint"] = \
                dc.design_content_fingerprint(self.ws, contract)
        with open(os.path.join(self.ws, "design", "contract.json"),
                  "w", encoding="utf-8") as f:
            json.dump(contract, f, indent=2)
        return contract

    def _dod_state(self):
        return {"requirement_id": self.req["id"],
                "design_graph_fingerprint": self._graph_fp()}


# ------------------------------------------------------ HIGH: dead-end exit

class H1AnchorMechanicalExit(_DesignEnv):
    def test_dor_blocker_names_the_mechanical_exit(self):
        dor = dc.design_dor(self.ws, {"requirement_id": None})
        self.assertFalse(dor["ready"])
        joined = " ".join(dor["blockers"])
        self.assertIn("tp req new", joined)
        self.assertIn("--req", joined)
        self.assertIn("preserved", joined)

    def test_attach_sets_requirement_and_is_idempotent(self):
        state = {}
        self.assertEqual(
            dc.design_attach_requirement(self.ws, state, self.req["id"]), [])
        self.assertEqual(state["requirement_id"], self.req["id"])
        self.assertEqual(
            dc.design_attach_requirement(self.ws, state, self.req["id"]), [])
        # ...and the design DoR is now satisfied on the anchor axis.
        dor = dc.design_dor(self.ws, state)
        self.assertNotIn("anchored", " ".join(dor["blockers"]))

    def test_attach_fails_closed_on_bad_requirements(self):
        state = {}
        self.assertTrue(dc.design_attach_requirement(self.ws, state, ""))
        self.assertIn("does not exist", " ".join(
            dc.design_attach_requirement(self.ws, state, "R-9999")))
        no_acc = reqs.record_requirement(self.ws, "vague", functional=["x"])
        self.assertIn("acceptance", " ".join(
            dc.design_attach_requirement(self.ws, state, no_acc["id"])))
        unresolved = reqs.record_requirement(
            self.ws, "ambiguous", functional=["x"], acceptance=["works"],
            open_questions=["which boundary?"])
        self.assertIn("open questions", " ".join(
            dc.design_attach_requirement(self.ws, state, unresolved["id"])))
        # No failed attach ever mutated the loop state.
        self.assertNotIn("requirement_id", state)

    def test_attach_refuses_to_swap_the_anchor(self):
        other = reqs.record_requirement(
            self.ws, "other", functional=["x"], acceptance=["works"])
        state = {"requirement_id": self.req["id"]}
        errors = dc.design_attach_requirement(self.ws, state, other["id"])
        self.assertIn("refusing to swap", " ".join(errors))
        self.assertEqual(state["requirement_id"], self.req["id"])


# ------------------------------------------ MED: requirement pinned (M2)

class M2RequirementPinnedInApproval(_DesignEnv):
    def test_requirement_fingerprint_tracks_record_edits(self):
        before = dc.requirement_fingerprint(self.ws, self.req["id"])
        path = os.path.join(reqs.kb_dir(self.ws), self.req["file"])
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n- edited acceptance criterion\n")
        self.assertNotEqual(
            before, dc.requirement_fingerprint(self.ws, self.req["id"]))
        self.assertNotEqual(
            before, dc.requirement_fingerprint(self.ws, "R-9999"))

    def test_requirement_edit_after_approval_invalidates_it(self):
        loop.init(self.ws, "design this", spec_path="specs/x.md",
                  requirement_id=self.req["id"], design=True)
        loop.next_action(self.ws)
        self._write_design(self._base_contract(
            graph_fp=loop.load(self.ws)["design_graph_fingerprint"]))
        self.assertEqual(loop.gate(self.ws, "pass")["step"],
                         "design_approval")
        self.assertEqual(loop.approve(self.ws, by="human")["step"], "plan")
        state = loop.load(self.ws)
        self.assertEqual(dc.design_current_errors(self.ws, state), [])
        # Hand-edit the anchored requirement record (no engine guard).
        path = os.path.join(reqs.kb_dir(self.ws), self.req["file"])
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n- NEW acceptance criterion sneaked in\n")
        errors = dc.design_current_errors(self.ws, state)
        self.assertTrue(errors)
        self.assertIn("requirement", " ".join(errors))
        # The plan gate inherits the block — no silent traceability break.
        self.assertTrue(dc.design_plan_errors(self.ws, state))

    def test_index_edit_also_invalidates(self):
        contract = self._write_design(self._base_contract())
        before = dc.design_evidence_fingerprint(self.ws, contract)
        reqs.set_status(self.ws, self.req["id"], "changed")
        self.assertNotEqual(
            before, dc.design_evidence_fingerprint(self.ws, contract))


# ------------------------------------- MED: lens evidence binding (M3)

class M3LensEvidenceBound(_DesignEnv):
    def test_valid_bound_independent_evidence_passes(self):
        self._write_design(self._base_contract())
        self.assertEqual(dc.design_dod_errors(self.ws, self._dod_state()),
                         [])

    def test_designer_typed_row_without_binding_fails(self):
        contract = self._base_contract()
        row = contract["lens_evidence"][0]
        del row["produced_by"]
        del row["independent"]
        self._write_design(contract, bind=False)
        joined = " ".join(dc.design_dod_errors(self.ws, self._dod_state()))
        self.assertIn("produced_by", joined)
        self.assertIn("not bound", joined)
        self.assertIn(dc.design_content_fingerprint(self.ws, contract), joined)
        self.assertIn("exactly one of independent: true or "
                      "self_attested: true", joined)

    def test_stale_binding_fails_after_content_change(self):
        self._write_design(self._base_contract())
        with open(os.path.join(self.ws, "design", "design.md"), "a", encoding="utf-8") as f:
            f.write("\nA material change after the lens ran.\n")
        joined = " ".join(dc.design_dod_errors(self.ws, self._dod_state()))
        self.assertIn("not bound to the current design content", joined)

    def test_both_independence_flags_rejected(self):
        contract = self._base_contract()
        contract["lens_evidence"][0]["self_attested"] = True
        self._write_design(contract)
        joined = " ".join(dc.design_dod_errors(self.ws, self._dod_state()))
        self.assertIn("exactly one", joined)

    def test_self_attestation_is_explicit_and_surfaced(self):
        contract = self._base_contract()
        row = contract["lens_evidence"][0]
        del row["independent"]
        row["self_attested"] = True
        row["produced_by"] = "tp-designer (same agent)"
        self._write_design(contract)
        # Explicit self-attestation is mechanically valid...
        self.assertEqual(dc.design_dod_errors(self.ws, self._dod_state()),
                         [])
        # ...but NEVER silent: the approval gate renders it.
        notices = dc.design_approval_notices(self.ws)
        self.assertEqual(len(notices), 1)
        self.assertIn("SELF-ATTESTED", notices[0])
        self.assertIn("tp-designer (same agent)", notices[0])

    def test_independent_evidence_yields_no_notice(self):
        self._write_design(self._base_contract())
        self.assertEqual(dc.design_approval_notices(self.ws), [])


# --------------------------------- MED: unscannable edge realization (M4)

class M4UnscannableEdgeDeclaration(_DesignEnv):
    BOUNDARY_EDGE = "taskplane->contract:design-artifact:provides"

    def _review_state(self):
        contract = self._write_design(self._base_contract())
        depgraph.record_edge(self.ws, "skills/tp-design", "taskplane",
                             kind="runtime")
        depgraph.record_edge(self.ws, "taskplane",
                             "contract:design-artifact", kind="provides")
        fp = dc.design_evidence_fingerprint(self.ws, contract)
        return {"design_required": True, "design_fingerprint": fp,
                "requirement_id": self.req["id"]}

    def _meta(self, **extra):
        evidence = {"fingerprint": None, "verdict": "conformant",
                    "modules_checked": ["taskplane", "skills/tp-design"],
                    "edges_checked": [
                        "skills/tp-design->taskplane:runtime",
                        self.BOUNDARY_EDGE],
                    "contracts_checked": ["contract:design-artifact"],
                    "drift": []}
        evidence.update(extra)
        return {"design": evidence}

    def test_hand_recorded_boundary_edge_no_longer_auto_passes(self):
        state = self._review_state()
        meta = self._meta(fingerprint=state["design_fingerprint"])
        errors = dc.design_review_errors(self.ws, state, meta)
        joined = " ".join(errors)
        self.assertIn("scanner-invisible designed edge", joined)
        self.assertIn(self.BOUNDARY_EDGE, joined)
        # The module→module edge needs no declaration — the scanner can
        # see it; only the boundary edge is named.
        self.assertNotIn("skills/tp-design->taskplane:runtime", joined)

    def test_explicit_declaration_satisfies_and_renders(self):
        state = self._review_state()
        meta = self._meta(
            fingerprint=state["design_fingerprint"],
            edge_evidence=[{"edge": self.BOUNDARY_EDGE,
                            "evidence": "loop.py:1866 emits the record; "
                                        "regression test asserts it",
                            "declared_by": "reviewer"}])
        self.assertEqual(dc.design_review_errors(self.ws, state, meta), [])
        notices = " ".join(dc.design_review_notices(meta))
        self.assertIn("scanner-invisible edge", notices)
        self.assertIn("declared by reviewer", notices)

    def test_incomplete_declaration_still_blocks(self):
        state = self._review_state()
        meta = self._meta(fingerprint=state["design_fingerprint"],
                          edge_evidence=[{"edge": self.BOUNDARY_EDGE,
                                          "evidence": "  "}])
        self.assertIn("scanner-invisible designed edge", " ".join(
            dc.design_review_errors(self.ws, state, meta)))


# --------------------------------------------------------- LOW fixes

class L1ContractPrefixUnified(_DesignEnv):
    def test_one_rule_is_the_stricter_plan_rule(self):
        self.assertEqual(dc.CONTRACT_ID_PREFIXES,
                         ("contract:", "resource:"))

    def test_design_dod_rejects_ids_the_plan_would_reject(self):
        contract = self._base_contract()
        contract["contracts"].append(
            {"relation": "provides", "id": "svc:billing",
             "description": "billing surface"})
        contract["graph"]["proposed_edges"].append(
            {"from": "taskplane", "to": "svc:billing", "kind": "provides",
             "reason": "billing dependency"})
        self._write_design(contract)
        joined = " ".join(dc.design_dod_errors(self.ws, self._dod_state()))
        self.assertIn("contract: or resource: prefixes", joined)
        self.assertIn("svc:billing", joined)


class L2AlertsModeled(_DesignEnv):
    def _errors_with_observability(self, observability):
        contract = self._base_contract()
        contract["observability"] = observability
        self._write_design(contract)
        return " ".join(dc.design_dod_errors(self.ws, self._dod_state()))

    def test_neither_field_fails_with_exact_shape_named(self):
        joined = self._errors_with_observability(
            {"signals": ["design gate trace"]})
        self.assertIn("exactly one of", joined)
        self.assertIn("alerts_none_rationale", joined)

    def test_both_fields_fail(self):
        joined = self._errors_with_observability(
            {"signals": ["s"], "alerts": ["a"],
             "alerts_none_rationale": "r"})
        self.assertIn("exactly one of", joined)

    def test_explicit_none_rationale_is_a_distinct_valid_shape(self):
        joined = self._errors_with_observability(
            {"signals": ["design gate trace"],
             "alerts_none_rationale": "design-only phase; no runtime "
                                      "surface to alert on"})
        self.assertNotIn("observability", joined)

    def test_empty_rationale_and_empty_alerts_fail(self):
        self.assertIn("alerts_none_rationale must explain",
                      self._errors_with_observability(
                          {"signals": ["s"], "alerts_none_rationale": " "}))
        self.assertIn("non-empty list",
                      self._errors_with_observability(
                          {"signals": ["s"], "alerts": []}))


class L3AcceptedDriftRepresentation(M4UnscannableEdgeDeclaration):
    def test_drift_error_names_the_real_rule_and_remedy(self):
        state = self._review_state()
        meta = self._meta(fingerprint=state["design_fingerprint"],
                          drift=["swapped queue for cron"])
        joined = " ".join(dc.design_review_errors(self.ws, state, meta))
        self.assertIn("any drift entry blocks", joined)
        self.assertIn("accepted_drift", joined)

    def test_accepted_drift_is_representable_and_rendered(self):
        state = self._review_state()
        meta = self._meta(
            fingerprint=state["design_fingerprint"],
            edge_evidence=[{"edge": self.BOUNDARY_EDGE,
                            "evidence": "probe passes",
                            "declared_by": "reviewer"}],
            accepted_drift=[{"drift": "helper lives in kb.py not loop.py",
                             "reason": "avoids an import cycle",
                             "accepted_by": "human EM"}])
        self.assertEqual(dc.design_review_errors(self.ws, state, meta), [])
        notices = " ".join(dc.design_review_notices(meta))
        self.assertIn("accepted design drift (by human EM)", notices)
        self.assertIn("import cycle", notices)

    def test_incomplete_accepted_drift_fails_closed(self):
        state = self._review_state()
        meta = self._meta(fingerprint=state["design_fingerprint"],
                          accepted_drift=[{"drift": "x"}])
        self.assertIn("every accepted_drift entry needs drift, reason, "
                      "and accepted_by", " ".join(
                          dc.design_review_errors(self.ws, state, meta)))


class L4L5Messages(_DesignEnv):
    def test_stale_graph_blocker_names_its_remedy(self):
        with open(os.path.join(self.ws, "src", "core", "b.py"), "w", encoding="utf-8") as f:
            f.write("VALUE = 2\n")
        self._commit("new head after scan")
        dor = dc.design_dor(self.ws, {"requirement_id": self.req["id"]})
        stale = [b for b in dor["blockers"] if "stale" in b]
        self.assertTrue(stale)
        self.assertIn("run graph scan", stale[0])

    def test_absent_open_questions_names_the_empty_list_remedy(self):
        contract = self._base_contract()
        del contract["open_questions"]
        self._write_design(contract)
        joined = " ".join(dc.design_dod_errors(self.ws, self._dod_state()))
        self.assertIn("open_questions is required", joined)
        self.assertIn("or [] when none", joined)


if __name__ == "__main__":
    unittest.main()


class WorktreeRequirementResolution(unittest.TestCase):
    """v3 dogfood fix: requirement_fingerprint inside a linked git worktree
    must resolve the PRIMARY workspace's store, so design DoD in a parallel
    wave agent workspace matches the approval fingerprint."""

    def test_worktree_fingerprint_matches_primary(self):
        import subprocess, tempfile, os
        home = tempfile.mkdtemp(prefix="tp-wt-store-")
        old = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = home
        try:
            ws = tempfile.mkdtemp(prefix="tp-wt-ws-")
            subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=ws, check=True)
            open(os.path.join(ws, "f.txt"), "w", encoding="utf-8").write("x\n")
            subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=ws, check=True)
            import requirements as reqs_mod
            reqs_mod.record_requirement(ws, "pin me", acceptance=["a1"])
            fp_primary = dc.requirement_fingerprint(ws, "R-0001")
            wt = os.path.join(ws, ".tp-work", "tx")
            subprocess.run(["git", "worktree", "add", "-q", wt, "-b", "tp/tx"],
                           cwd=ws, check=True)
            fp_worktree = dc.requirement_fingerprint(wt, "R-0001")
            self.assertEqual(fp_primary, fp_worktree,
                             "worktree must resolve the primary store")
            # a truly-missing rid still hashes as missing (fail closed)
            self.assertNotEqual(dc.requirement_fingerprint(wt, "R-9999"),
                                fp_primary)
        finally:
            if old is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = old
