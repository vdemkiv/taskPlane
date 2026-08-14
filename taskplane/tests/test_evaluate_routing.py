"""R-0006 row 1 — evaluate consumes routed briefs (t7).

The EVALUATE step routes its lens brief with stage="build" so route v2
engages: build-profile candidates, the R-0001 budget (5-7 deep target,
hard cap 8, demote-never-drop) inherited verbatim, floors surviving
profile narrowing, and n/a entries carrying negative evidence. Final EM
uses the same complete decision with the review stage profile.

_evaluation_errors consumes the persisted decision and verifies the SAME
stage, so the validator never performs a second mapping derivation.
"""
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import lens  # noqa: E402
import review  # noqa: E402
import review_evidence  # noqa: E402

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
    """The lenses that owe the evaluator a verdict: deep + light."""
    return [x for x in brief["lenses"] if x["mode"] != "none"]


def _write_verdict(ws, task_id, criteria, lens_rows):
    os.makedirs(os.path.join(ws, ".eval"), exist_ok=True)
    with open(os.path.join(ws, ".eval", "verdict.json"), "w", encoding="utf-8") as f:
        json.dump({"task": task_id, "verdict": "pass",
                   "criteria": [{"criterion": c, "status": "met",
                                 "evidence": "verified by test"}
                                for c in criteria],
                   "lenses": lens_rows, "failures": []}, f)


def _write_kernel_results(ws, *, dropped=None):
    state = review._load_state(ws)
    store = review_evidence.ArtifactStore(ws)
    for index, slot in enumerate(state["slots"]):
        lease = store.read(slot["lease"])
        brief = store.read(slot["brief"])
        lens_ids = [lid for lid in lease["lens_ids"] if lid != dropped]
        if not lens_ids:
            continue
        row = {**lease, "schema": "taskplane.lens-slot-output/v2",
               "authored_by": "lens-slot", "findings": [],
               "lens_results": [{"lens": lid, "verdict": "pass",
                                  "blockers": 0} for lid in lens_ids]}
        if brief.get("language_references"):
            row["references_applied"] = list(brief["language_references"])
        content = json.dumps(row, sort_keys=True, separators=(",", ":"))
        event = {"session_id": "eval-lens-session",
                 "agent_id": f"eval-lens-child-{index}",
                 "tool_name": "Write",
                 "tool_input": {"file_path": slot["result_path"],
                                "content": content}}
        contract = {"task": brief["producer_contract"]["task"],
                    "task_id": "eval-lens-contract", "read_only": True,
                    "write_allow": [slot["result_path"]]}
        review.register_slot_producer(
            ws, event=event, contract=contract,
            task_slot=brief["producer_contract"]["task_slot"])
        review.record_slot_write_observation(
            ws, event=event, contract=contract,
            task_slot=brief["producer_contract"]["task_slot"])
        path = os.path.join(ws, slot["result_path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(content)


def _pass_eval(ws, brief):
    """Evaluator evidence built from the ROUTED set of the brief itself."""
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    rows = [{"lens": x["id"], "verdict": "pass", "blockers": 0}
            for x in _routed(brief)]
    _write_kernel_results(ws)
    _write_verdict(ws, task["id"], loop._criteria_for(ws, state, task), rows)
    loop.submit(ws, "pass")
    return loop.gate(ws, "pass")


class TestEvaluateBriefRoutesBuildStage(unittest.TestCase):
    """(a) the evaluate brief carries build-stage (route v2) routing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_constant_is_build_and_single_sourced(self):
        self.assertEqual(loop.EVALUATE_ROUTE_STAGE, "build")
        src = _loop_src()
        # Both are mapped: Evaluate uses build signals, final EM review signals.
        self.assertIn('stage = "review" if step == "em" else EVALUATE_ROUTE_STAGE',
                      src)
        # Validator consumes the persisted decision and checks its stage;
        # it must not invoke a second mapper derivation.
        validator = inspect.getsource(loop._evaluation_errors)
        self.assertIn('kernel.get("stage") != EVALUATE_ROUTE_STAGE', validator)
        self.assertNotIn("route_git_diff", validator)

    def test_evaluate_brief_is_route_v2_with_inherited_budget(self):
        ws = _repo(self.tmp)
        act = _to_evaluate(ws, {"src/app/feature.py":
                                "def f():\n    return 1\n"})
        lenses = act["lenses"]
        # v2 signature: EVERY catalog lens appears (coverage honesty) —
        # the legacy routed path returns only the summoned subset.
        catalog_ids = {l["id"] for l in lens.load_catalog()["lenses"]}
        self.assertEqual({x["id"] for x in lenses}, catalog_ids)
        # v2 entries carry the engine's verdict + score
        for x in _routed(act):
            self.assertIn("verdict", x)
            self.assertIn("score", x)
        # R-0001 budget inherited verbatim: hard cap 8 deep, no new knobs
        deep = [x for x in lenses if x["mode"] == "subagent"]
        self.assertLessEqual(len(deep), 8)
        # something routed and something narrowed away (build profile)
        self.assertTrue(_routed(act))
        self.assertTrue([x for x in lenses if x["mode"] == "none"])

    def test_brief_matches_direct_build_stage_routing(self):
        """The brief's routed set IS route_git_diff(stage='build') — same
        derivation the validator uses (no second implementation)."""
        ws = _repo(self.tmp)
        act = _to_evaluate(ws, {"src/app/feature.py": "def f():\n"
                                "    return 1\n"})
        state = loop.load(ws)
        direct = lens.route_git_diff(
            ws, base=state.get("baseline") or "HEAD",
            task_type=None, stage=loop.EVALUATE_ROUTE_STAGE,
            breadth="routed")
        direct_ids = {x["id"] for x in direct["lenses"]
                      if x["mode"] != "none"}
        self.assertEqual({x["id"] for x in _routed(act)}, direct_ids)


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
        self.assertIn("caller_expander=review.bounded_caller_expander(graph)",
                      source)


class TestCanonicalFindingEnforcement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_evaluate_derives_blocking_from_revision_findings(self):
        ws = _repo(self.tmp)
        brief = _to_evaluate(ws, {"src/app/feature.py":
                                  "def f():\n    return 1\n"})
        state = loop.load(ws)
        task = state["tasks"][state["current_task"]]
        kernel = review._load_state(ws)
        store = review_evidence.ArtifactStore(ws)
        blocking_lens = kernel["slots"][0]["lens_ids"][0]
        for index, slot in enumerate(kernel["slots"]):
            lease = store.read(slot["lease"])
            brief_row = store.read(slot["brief"])
            is_blocking = blocking_lens in lease["lens_ids"]
            findings = ([{
                "lens": blocking_lens, "severity": "high",
                "class": "regression", "file": "src/app/feature.py",
                "line": 1, "title": "broken behavior",
                "scenario": "production", "fix": "repair it",
            }] if is_blocking else [])
            rows = [{"lens": lid,
                     "verdict": "fail" if lid == blocking_lens else "pass",
                     "blockers": 1 if lid == blocking_lens else 0}
                    for lid in lease["lens_ids"]]
            payload = {**lease,
                       "schema": "taskplane.lens-slot-output/v2",
                       "authored_by": "lens-slot",
                       "lens_results": rows, "findings": findings}
            if brief_row.get("language_references"):
                payload["references_applied"] = list(
                    brief_row["language_references"])
            content = json.dumps(
                payload, sort_keys=True, separators=(",", ":"))
            event = {"session_id": "eval-child",
                     "agent_id": f"eval-agent-{index}",
                     "tool_name": "Write",
                     "tool_input": {"file_path": slot["result_path"],
                                    "content": content}}
            contract = {"task": brief_row["producer_contract"]["task"],
                        "read_only": True,
                        "write_allow": [slot["result_path"]]}
            review.register_slot_producer(
                ws, event=event, contract=contract,
                task_slot=brief_row["producer_contract"]["task_slot"])
            review.record_slot_write_observation(
                ws, event=event, contract=contract,
                task_slot=brief_row["producer_contract"]["task_slot"])
            path = os.path.join(ws, slot["result_path"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(content)
        review.collect_review(ws, publish=False)
        # Model a corrupted/synthesized summary: the immutable canonical
        # revision still contains the blocker and must control the gate.
        collected = review._load_state(ws)
        collected["lens_results"] = [
            {"lens": row["lens"], "verdict": "pass", "blockers": 0}
            for row in collected["lens_results"]]
        review._save_state(ws, collected)
        free_rows = [{"lens": row["id"], "verdict": "pass", "blockers": 0}
                     for row in _routed(brief)]
        _write_verdict(ws, task["id"], loop._criteria_for(ws, state, task),
                       free_rows)
        errors = loop._evaluation_errors(ws, state, task)
        self.assertTrue(any("canonical blocking finding" in item
                            for item in errors), errors)

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
        self.assertTrue(any(x["tier"] in ("deep", "light")
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


class TestFloorsSurviveBuildProfileNarrowing(unittest.TestCase):
    """(d) shipped route v2 rule, pinned from the EVALUATE path."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_floors_on_enforcement_and_code_diff(self):
        ws = _repo(self.tmp)
        act = _to_evaluate(ws, {
            "hooks/guard.py": "def guard():\n    return True\n",
            "src/app/feature.py": "def feature():\n    return 3\n"})
        by_id = {x["id"]: x for x in act["lenses"]}
        # security: never n/a on an enforcement-touching diff
        sec = by_id["security"]
        self.assertIn(sec["tier"], ("light", "deep"))
        self.assertNotEqual(sec["mode"], "none")
        # architecture: >= light on any code change, and it survives the
        # build-profile narrowing precisely BECAUSE it is floored
        profile = lens.load_catalog()["stage_profiles"]["build"]
        self.assertNotIn("architecture", profile)   # narrowing is real
        arch = by_id["architecture"]
        self.assertIn(arch["tier"], ("light", "deep"))
        self.assertNotEqual(arch["mode"], "none")
        self.assertIn("floor", arch)


class TestEvaluationErrorsRoutedSet(unittest.TestCase):
    """(e) n/a-with-evidence unchanged; (f) validator accepts the routed
    set and rejects a missing routed lens."""

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

    def test_na_entries_carry_negative_evidence(self):
        ws, act, _, _ = self._at_evaluate()
        na = [x for x in act["lenses"] if x["mode"] == "none"]
        self.assertTrue(na)                     # narrowing really happened
        for x in na:
            self.assertTrue(x.get("negative_evidence"),
                            f"lens {x['id']} is n/a without evidence")

    def test_verdict_rows_without_leased_slot_results_are_rejected(self):
        ws, act, state, task = self._at_evaluate()
        rows = [{"lens": x["id"], "verdict": "pass", "blockers": 0}
                for x in _routed(act)]
        _write_verdict(ws, task["id"],
                       loop._criteria_for(ws, state, task), rows)
        errors = loop._evaluation_errors(ws, state, task)
        self.assertTrue(any("leased slot" in err for err in errors), errors)

    def test_verdict_from_leased_routed_set_validates(self):
        ws, act, state, task = self._at_evaluate()
        rows = [{"lens": x["id"], "verdict": "pass", "blockers": 0}
                for x in _routed(act)]
        _write_kernel_results(ws)
        _write_verdict(ws, task["id"],
                       loop._criteria_for(ws, state, task), rows)
        self.assertEqual(loop._evaluation_errors(ws, state, task), [])

    def test_verdict_missing_a_routed_lens_is_rejected(self):
        ws, act, state, task = self._at_evaluate()
        routed = _routed(act)
        self.assertGreater(len(routed), 1)
        dropped = routed[0]["id"]
        rows = [{"lens": x["id"], "verdict": "pass", "blockers": 0}
                for x in routed[1:]]
        _write_verdict(ws, task["id"],
                       loop._criteria_for(ws, state, task), rows)
        _write_kernel_results(ws)
        errors = loop._evaluation_errors(ws, state, task)
        self.assertIn(f"routed lens has no verdict: {dropped}", errors)

    def test_na_lens_owes_no_verdict_row(self):
        """n/a lenses are covered by the routing's negative evidence, not
        by evaluator rows — the validator must not demand them."""
        ws, act, state, task = self._at_evaluate()
        na_ids = {x["id"] for x in act["lenses"] if x["mode"] == "none"}
        self.assertTrue(na_ids)
        rows = [{"lens": x["id"], "verdict": "pass", "blockers": 0}
                for x in _routed(act)]
        _write_verdict(ws, task["id"],
                       loop._criteria_for(ws, state, task), rows)
        _write_kernel_results(ws)
        errors = loop._evaluation_errors(ws, state, task)
        for err in errors:
            for lens_id in na_ids:
                self.assertNotIn(lens_id, err)


class TestWorkflowAgnostic(unittest.TestCase):
    """(g) loop.py stays workflow-agnostic after the wiring change."""

    def test_zero_workflow_substrings(self):
        src = _loop_src()
        self.assertNotIn("workflow", src.lower())
        self.assertNotIn("review-wave", src)


if __name__ == "__main__":
    unittest.main()
