"""R-0006/D-0014 — Evaluate is one zero-lens producer judgment.

Evaluate still owns a ReviewKernel run at the build stage, but the accepted
delivery contract gives it no lens slots.  Final EM remains the selective
lens-routing stage.  These tests pin that boundary and the genuine external
producer observation required for accepting evaluator bytes.
"""
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import lens  # noqa: E402
import review  # noqa: E402
import producer_observation  # noqa: E402
import taskplane_lite as tp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
LOOP_SRC = os.path.join(ROOT, "taskplane", "loop.py")

TASK = {"id": "t1", "scope": ["src/app/**", "hooks/**"], "tests": "true",
        "criteria": ["feature works"], "new_modules": ["app", "hooks"]}


def _loop_src() -> str:
    with open(LOOP_SRC, encoding="utf-8") as f:
        return f.read()


def _repo(tmp) -> str:
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "plan"))
    os.makedirs(os.path.join(ws, "src", "app"))
    with open(os.path.join(ws, "src", "app", "a.py"), "w", encoding="utf-8") as f:
        f.write("x=1\n")
    subprocess.run(["git", "init", "-q"], cwd=ws)
    subprocess.run(["git", "config", "user.email", "e@e"], cwd=ws)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws)
    with open(os.path.join(ws, "plan", "tasks.json"), "w", encoding="utf-8") as f:
        json.dump({"tasks": [dict(TASK)]}, f)
    return ws


def _to_evaluate(ws, build_files) -> dict:
    """Real loop walk: init -> plan -> execute (write the diff) -> evaluate.
    Returns the EVALUATE brief from next_action."""
    loop.init(ws, "g", spec_path="s", checkpoints=["em"])
    loop.next_action(ws)
    loop.gate(ws, "pass")                       # plan -> execute
    loop.next_action(ws)                        # execute brief
    for rel, content in build_files.items():
        path = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    loop.submit(ws, "pass")
    loop.gate(ws, "pass")                       # execute -> evaluate
    act = loop.next_action(ws)
    assert act["step"] == "evaluate", act
    return act


def _routed(brief) -> list:
    """Evaluate deliberately owes no lens verdicts after D-0014."""
    assert "lenses" not in brief
    return []


def _write_verdict(ws, task_id, criteria, lens_rows):
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    os.makedirs(os.path.join(ws, ".eval"), exist_ok=True)
    with open(os.path.join(ws, ".eval", "verdict.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "taskplane.evaluator-output/v2",
                   "task": task_id,
                   "requirement": task.get("req") or
                                  state.get("requirement_id") or "",
                   "verdict": "pass",
                   "evaluation": {"status": "complete",
                                  "reason_code": "none", "detail": ""},
                   "criteria": [{"criterion": c, "status": "met",
                                 "evidence": "verified by test"}
                                for c in criteria],
                   "graph": {"dispositions": [],
                             "requirements_checked": [],
                             "contracts_checked": []},
                   "failures": []}, f)


def _write_kernel_results(ws, *, dropped=None):
    state = review._load_state(ws)
    assert dropped is None
    assert state["expected_lenses"] == []
    assert state["slots"] == []
    assert state["zero_lens_evaluation"] is True


def _pass_eval(ws, brief):
    """Record the genuine host stop that observed the evaluator bytes."""
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    _routed(brief)
    _write_kernel_results(ws)
    _write_verdict(ws, task["id"], loop._criteria_for(ws, state, task), [])
    material = loop.producer_output_identity(
        ws, state, task, "evaluate", active_contract=tp.load_active(ws) or {})
    event = {
        "hook_event_name": "SubagentStop",
        "session_id": "evaluate-routing-session",
        "turn_id": "evaluate-routing-turn",
        "agent_id": "evaluate-routing-producer",
        "agent_type": material["producer_dispatch"]["task_name"],
        "task_name": material["producer_dispatch"]["task_name"],
    }
    claim = hashlib.sha256(tp.hook_event_identity(
        ws, "subagent-stop", event).encode("utf-8")).hexdigest()
    producer_observation.record_codex_subagent_stop(
        event=event, hook_claim_id=claim, **material)
    with mock.patch("runtime_eval.guide_loop",
                    return_value={"status": "on_path", "recovered": False}):
        loop.submit(ws, "pass")
    return loop.gate(ws, "pass")


class TestEvaluateBriefRoutesBuildStage(unittest.TestCase):
    """Evaluate keeps build-stage identity without lens dispatch."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_constant_is_build_and_single_sourced(self):
        self.assertEqual(loop.EVALUATE_ROUTE_STAGE, "build")
        src = _loop_src()
        # The shared kernel retains stage identity; Evaluate's adapter removes
        # lens authority while final EM still consumes review routing.
        self.assertIn('stage = "review" if step == "em" else EVALUATE_ROUTE_STAGE',
                      src)
        # Validator consumes the persisted decision and checks its stage;
        # it must not invoke a second mapper derivation.
        validator = inspect.getsource(loop._evaluation_errors)
        self.assertIn('kernel.get("stage") != EVALUATE_ROUTE_STAGE', validator)
        self.assertNotIn("route_git_diff", validator)

    def test_evaluate_brief_has_zero_lens_delivery_contract(self):
        ws = _repo(self.tmp)
        act = _to_evaluate(ws, {"src/app/feature.py":
                                "def f():\n    return 1\n"})
        self.assertNotIn("lenses", act)
        self.assertEqual(act["output_schema"],
                         "taskplane.evaluator-output/v2")
        kernel = review._load_state(ws)
        self.assertEqual(kernel["stage"], loop.EVALUATE_ROUTE_STAGE)
        self.assertEqual(kernel["expected_lenses"], [])
        self.assertEqual(kernel["slots"], [])
        self.assertTrue(kernel["zero_lens_evaluation"])
        self.assertIsNotNone(kernel["delivery_mode_receipt"])

    def test_evaluate_does_not_reexport_internal_route_decision(self):
        ws = _repo(self.tmp)
        act = _to_evaluate(ws, {"src/app/feature.py": "def f():\n"
                                "    return 1\n"})
        self.assertNotIn("lenses", act)
        kernel = review._load_state(ws)
        self.assertEqual(kernel["routing"]["lenses"], [])


class TestEmUsesSelectiveKernel(unittest.TestCase):
    """Final EM uses the same complete selective decision as Evaluate."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_em_has_no_normal_breadth_all_fallback(self):
        self.assertNotIn('"all" if step == "em" else "routed"', _loop_src())

    def test_evaluate_and_em_use_the_same_bounded_sparse_graph_adapter(self):
        graph = {
            "symbol_edges": [
                {"caller": "api", "callee": "changed",
                 "contract": "http"},
                {"caller": "job", "callee": "api"},
            ]}
        bounds = {"max_symbols": 8, "max_hops": 4, "max_edges": 16,
                  "timeout_seconds": 2, "max_callers": 16}
        direct = review.bounded_caller_expander(graph)(
            snapshot={"ignored": "ambient"},
            changed_symbols=["changed"], bounds=bounds)
        self.assertEqual(direct["callers"], ["api", "job"])
        source = inspect.getsource(loop._review_kernel)
        self.assertIn("caller_expander = review.bounded_caller_expander(graph)",
                      source)
        self.assertIn("caller_expander=caller_expander", source)


class TestCanonicalFindingEnforcement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_evaluate_cannot_receive_caller_authored_lens_findings(self):
        ws = _repo(self.tmp)
        brief = _to_evaluate(ws, {"src/app/feature.py":
                                  "def f():\n    return 1\n"})
        kernel = review._load_state(ws)
        self.assertEqual(kernel["slots"], [])
        self.assertEqual(kernel["expected_lenses"], [])
        self.assertNotIn("lenses", brief)

    def test_em_brief_maps_full_catalog_but_dispatches_selectively(self):
        ws = _repo(self.tmp)
        act = _to_evaluate(ws, {"src/app/feature.py": "def f():\n"
                                "    return 1\n"})
        out = _pass_eval(ws, act)
        self.assertNotIn("error", out)
        self.assertEqual(loop.load(ws)["step"], "em")
        em = loop.next_action(ws)
        self.assertEqual(em["step"], "em")
        catalog_ids = {l["id"] for l in lens.load_catalog()["lenses"]}
        self.assertEqual({x["id"] for x in em["lenses"]}, catalog_ids)
        self.assertTrue(any(x["tier"] == "sweep"
                            for x in em["lenses"]))
        self.assertTrue(any(x["tier"] == "n/a" for x in em["lenses"]))
        for x in em["lenses"]:
            self.assertIn("score", x)
            if x["tier"] == "n/a":
                self.assertEqual(x["mode"], "none")
                self.assertTrue(x.get("negative_evidence"))

    def test_audit_gate_surfaces_still_reexported(self):
        """Cadence + router-regression blocking live in audit.py now;
        loop keeps the public names (zero caller churn, t5 differential
        re-verified by the audit batteries)."""
        import audit
        self.assertIs(loop.audit_due, audit.audit_due)
        self.assertIs(loop._router_audit_gate, audit._router_audit_gate)
        self.assertIs(loop.router_audit, audit.router_audit)


class TestFloorsRemainAnEngineeringConcern(unittest.TestCase):
    """Evaluate does not reacquire final-EM floor authority."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_enforcement_and_code_diff_still_produce_zero_lens_evaluate(self):
        ws = _repo(self.tmp)
        act = _to_evaluate(ws, {
            "hooks/guard.py": "def guard():\n    return True\n",
            "src/app/feature.py": "def feature():\n    return 3\n"})
        self.assertNotIn("lenses", act)
        self.assertEqual(review._load_state(ws)["expected_lenses"], [])


class TestEvaluationErrorsZeroLensSet(unittest.TestCase):
    """The validator accepts exactly the evaluator output contract."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _at_evaluate(self):
        ws = _repo(self.tmp)
        act = _to_evaluate(ws, {
            "hooks/guard.py": "def guard():\n    return True\n",
            "src/app/feature.py": "def feature():\n    return 3\n"})
        state = loop.load(ws)
        task = state["tasks"][state["current_task"]]
        return ws, act, state, task

    def test_no_lens_entries_are_exposed(self):
        _, act, _, _ = self._at_evaluate()
        self.assertNotIn("lenses", act)

    def test_genuinely_observed_zero_lens_verdict_validates(self):
        ws, act, _, _ = self._at_evaluate()
        result = _pass_eval(ws, act)
        self.assertNotIn("error", result)
        self.assertEqual(loop.load(ws)["step"], "em")

    def test_unobserved_verdict_is_rejected(self):
        ws, act, state, task = self._at_evaluate()
        _write_verdict(ws, task["id"],
                       loop._criteria_for(ws, state, task), [])
        errors = loop._evaluation_errors(ws, state, task)
        self.assertTrue(any("producer observation" in err or
                            "leased slot collection" in err
                            for err in errors), errors)


class TestWorkflowAgnostic(unittest.TestCase):
    """(g) loop.py stays workflow-agnostic after the wiring change."""

    def test_zero_workflow_substrings(self):
        src = _loop_src()
        for marker in ("workflows/", "TASKPLANE_WORKFLOWS",
                       "CLAUDE_CODE_WORKFLOWS", "workflow_available(",
                       "review-wave"):
            self.assertNotIn(marker, src)


if __name__ == "__main__":
    unittest.main()
