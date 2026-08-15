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
from taskplane.tests.review_kernel_support import complete_review  # noqa: E402


def _repo():
    ws = tempfile.mkdtemp(prefix="tp-governance-")
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8") as f:
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
    with open(os.path.join(ws, ".eval", "verdict.json"), "w", encoding="utf-8") as f:
        json.dump({"task": task["id"], "verdict": "pass",
                   "criteria": [{"criterion": "feature works",
                                  "status": "met", "evidence": "true"}],
                   "lenses": [{"lens": x["id"], "verdict": "pass",
                               "blockers": 0} for x in routed["lenses"]],
                   "failures": []}, f)


def _write_em(ws):
    coverage = {x["id"]: "sweep" for x in lens.load_catalog()["lenses"]}
    complete_review(ws, coverage=coverage)


class TestGovernanceInvariants(unittest.TestCase):
    def test_failed_dor_is_governed_but_does_not_start(self):
        ws = _repo()
        _state(ws, "execute", tests=None)
        out = loop.next_action(ws)
        self.assertIn("error", out)
        self.assertFalse(out["dor"]["ready"])
        active = tp.load_active(ws)
        self.assertIsNotNone(active)
        self.assertEqual(active["task"], "EXECUTE: t1")
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
        self.assertEqual(out["step"], "retro")

    def test_unknown_mutation_capability_is_denied(self):
        contract = tp.build_contract("t", scope=["src/**"],
                                     test_command="true",
                                     tools=["Write"])
        ok, reason = tp.screen_tool(contract, "mcp__fs__delete_file",
                                    {"path": "src/a.py"}, _repo())
        self.assertFalse(ok)
        self.assertIn("allowed_tools", reason)


# ---- C2 (R-0009): components.yaml joins DEFAULT_OUT_OF_SCOPE — a strict-
# ---- or-stricter widening that must still honor the plan-minted literal
# ---- scope override (scope_violation), exactly as every other deny-family
# ---- member already does. Both directions live in this one file so
# ---- neither can silently regress. ----

class TestComponentsYamlDenyFamily(unittest.TestCase):
    def test_unscoped_contract_is_blocked_from_writing_components_yaml(self):
        # `tp new --scope components.yaml` style: no plan provenance, a
        # literal scope entry naming the file — still denied, exactly like
        # every other unminted literal against the default deny family.
        contract = tp.build_contract("t", scope=["components.yaml"],
                                     test_command="true")
        self.assertNotIn("plan_minted", contract["coding"])
        v = tp.scope_violation("components.yaml", contract["coding"])
        self.assertIsNotNone(v)
        self.assertIn("out_of_scope_paths", v)

    def test_plan_minted_literal_scope_still_writes_components_yaml(self):
        # the loop engine building a task contract from a human-approved
        # plan carries plan_minted=True — the literal override must still
        # apply here exactly as it does for every other deny-family member
        # (this is what prevents the Phase-2 scope-precedence deadlock).
        contract = tp.build_contract("EXECUTE: t9",
                                     scope=["components.yaml"],
                                     test_command="true", plan_minted=True)
        self.assertTrue(contract["coding"]["plan_minted"])
        v = tp.scope_violation("components.yaml", contract["coding"])
        self.assertIsNone(v)

    def test_deny_family_only_gained_a_member(self):
        # widening-only: every previously-shipped entry is still present,
        # byte-unchanged, plus the new one.
        previous = [".git/**", ".github/**", "deploy/**", "*.lock",
                    "**/.env", "**/secrets/**", ".env", "secrets/**"]
        for entry in previous:
            self.assertIn(entry, tp.DEFAULT_OUT_OF_SCOPE)
        self.assertIn("components.yaml", tp.DEFAULT_OUT_OF_SCOPE)
        self.assertNotIn("components.yaml", tp._SACRED_OUT_OF_SCOPE)
        self.assertEqual(len(tp.DEFAULT_OUT_OF_SCOPE), len(previous) + 1)


if __name__ == "__main__":
    unittest.main()
