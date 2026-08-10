"""R-0006 row 1 — evaluate consumes routed briefs (t7).

The EVALUATE step routes its lens brief with stage="build" so route v2
engages: build-profile candidates, the R-0001 budget (5-7 deep target,
hard cap 8, demote-never-drop) inherited verbatim, floors surviving
profile narrowing, and n/a entries carrying negative evidence. The em
step is UNTOUCHED: full catalog via the byte-pinned
'"all" if step == "em" else "routed"' literal, no stage.

_evaluation_errors derives its expected lens set with the SAME stage
(single-sourced through loop.EVALUATE_ROUTE_STAGE), so the validator's
expectation can never drift from what the brief dispatched.
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
LOOP_SRC = os.path.join(ROOT, "taskplane", "loop.py")

TASK = {"id": "t1", "scope": ["src/app/**", "hooks/**"], "tests": "true",
        "criteria": ["feature works"], "new_modules": ["app", "hooks"]}


def _loop_src() -> str:
    with open(LOOP_SRC) as f:
        return f.read()


def _repo(tmp) -> str:
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "plan"))
    os.makedirs(os.path.join(ws, "src", "app"))
    with open(os.path.join(ws, "src", "app", "a.py"), "w") as f:
        f.write("x=1\n")
    subprocess.run(["git", "init", "-q"], cwd=ws)
    subprocess.run(["git", "config", "user.email", "e@e"], cwd=ws)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws)
    with open(os.path.join(ws, "plan", "tasks.json"), "w") as f:
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
        with open(path, "w") as f:
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
    with open(os.path.join(ws, ".eval", "verdict.json"), "w") as f:
        json.dump({"task": task_id, "verdict": "pass",
                   "criteria": [{"criterion": c, "status": "met",
                                 "evidence": "verified by test"}
                                for c in criteria],
                   "lenses": lens_rows, "failures": []}, f)


def _pass_eval(ws, brief):
    """Evaluator evidence built from the ROUTED set of the brief itself."""
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    rows = [{"lens": x["id"], "verdict": "pass", "blockers": 0}
            for x in _routed(brief)]
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
        # brief wiring: evaluate gets the stage, em explicitly gets NONE
        self.assertIn('stage=None if step == "em" else EVALUATE_ROUTE_STAGE',
                      src)
        # validator derives its expectation with the SAME constant
        self.assertIn("stage=EVALUATE_ROUTE_STAGE",
                      inspect.getsource(loop._evaluation_errors))

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


class TestEmSurfaceUntouched(unittest.TestCase):
    """(b) + (c) em still routes breadth=all; the literal is byte-present."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_em_literal_byte_present(self):
        self.assertIn('"all" if step == "em" else "routed"', _loop_src())

    def test_em_brief_still_full_catalog_breadth_all(self):
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
        # legacy breadth=all shape: sweep tier present, NO v2 engine keys,
        # and no lens is suppressed (mode "none" never appears at em)
        self.assertTrue(any(x["tier"] == "sweep" for x in em["lenses"]))
        for x in em["lenses"]:
            self.assertNotIn("score", x)
            self.assertNotEqual(x["mode"], "none")

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
        act = _to_evaluate(ws, {"hooks/guard.py": "y=2\n",
                                "src/app/feature.py": "x=3\n"})
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
        act = _to_evaluate(ws, {"hooks/guard.py": "y=2\n",
                                "src/app/feature.py": "x=3\n"})
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

    def test_verdict_from_routed_set_validates(self):
        ws, act, state, task = self._at_evaluate()
        rows = [{"lens": x["id"], "verdict": "pass", "blockers": 0}
                for x in _routed(act)]
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
