"""Fail-closed DoR/DoD invariants shared by Claude and Codex hosts."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def _repo():
    ws = tempfile.mkdtemp(prefix="tp-governance-")
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "a.py"), "w") as f:
        f.write("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                    "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                    "commit", "-qm", "base"], cwd=ws, check=True)
    return ws


def _state(ws, step, tests="true"):
    task = {"id": "t1", "scope": ["src/**"], "tests": tests,
            "criteria": ["feature works"], "status": "pending",
            "fix_cycles": 0}
    state = {"goal": "g", "step": step, "tasks": [task],
             "current_task": 0, "parallel": False, "max_fix_cycles": 2,
             "checkpoints": ["em"], "baseline": tp.git_head(ws)}
    loop.save(ws, state)
    return state


def _write_eval(ws, state):
    task = state["tasks"][0]
    routed = lens.route_git_diff(ws, base=state["baseline"], breadth="routed")
    os.makedirs(os.path.join(ws, ".eval"), exist_ok=True)
    with open(os.path.join(ws, ".eval", "verdict.json"), "w") as f:
        json.dump({"task": task["id"], "verdict": "pass",
                   "criteria": [{"criterion": "feature works",
                                  "status": "met", "evidence": "true"}],
                   "lenses": [{"lens": x["id"], "verdict": "pass",
                               "blockers": 0} for x in routed["lenses"]],
                   "failures": []}, f)


def _write_em(ws):
    coverage = {x["id"]: "sweep" for x in lens.load_catalog()["lenses"]}
    os.makedirs(os.path.join(ws, ".em-review"), exist_ok=True)
    with open(os.path.join(ws, ".em-review", "findings.json"), "w") as f:
        json.dump({"meta": {"lens_coverage": coverage, "impact": {},
                            "tests": ["true"],
                            "gate": {"verdict": "recommend-pass"}},
                   "findings": []}, f)


class TestGovernanceInvariants(unittest.TestCase):
    def test_failed_dor_does_not_activate_or_start(self):
        ws = _repo()
        _state(ws, "execute", tests=None)
        out = loop.next_action(ws)
        self.assertIn("error", out)
        self.assertFalse(out["dor"]["ready"])
        self.assertIsNone(tp.load_active(ws))
        self.assertEqual(loop.load(ws)["step"], "execute")

    def test_execute_pass_is_rejected_when_tests_fail(self):
        ws = _repo()
        _state(ws, "execute", tests="false")
        loop.next_action(ws)
        out = loop.gate(ws, "pass")
        self.assertIn("Definition of Done failed", out["error"])
        self.assertEqual(loop.load(ws)["step"], "execute")
        self.assertIsNotNone(tp.load_active(ws))

    def test_evaluate_requires_complete_evidence(self):
        ws = _repo()
        state = _state(ws, "evaluate")
        loop.next_action(ws)
        out = loop.gate(ws, "pass")
        self.assertIn("evaluation evidence failed", out["error"])
        self.assertEqual(loop.load(ws)["step"], "evaluate")
        _write_eval(ws, state)
        out = loop.gate(ws, "pass")
        self.assertEqual(out["step"], "em")

    def test_em_and_signoff_require_full_review_evidence(self):
        ws = _repo()
        state = _state(ws, "em")
        state["tasks"][0]["status"] = "passed"
        loop.save(ws, state)
        loop.next_action(ws)
        out = loop.gate(ws, "pass")
        self.assertIn("engineering review is incomplete", out["error"])
        _write_em(ws)
        out = loop.gate(ws, "pass")
        self.assertEqual(out["step"], "signoff")
        out = loop.approve(ws, by="human")
        self.assertEqual(out["step"], "done")

    def test_unknown_mutation_capability_is_denied(self):
        contract = tp.build_contract("t", scope=["src/**"],
                                     test_command="true",
                                     tools=["Write"])
        ok, reason = tp.screen_tool(contract, "mcp__fs__delete_file",
                                    {"path": "src/a.py"}, _repo())
        self.assertFalse(ok)
        self.assertIn("allowed_tools", reason)


if __name__ == "__main__":
    unittest.main()
