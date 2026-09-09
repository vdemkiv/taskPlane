"""Shared live stage-wave fixture journey (t4, R-0004 stage parity).

The EXECUTE, EVALUATE, and FIX dispatches are captured from one loop journey
in a throwaway git workspace. Tests compare the active Task and workflow
producers directly and assert current contract fields; no frozen payload
snapshot is used as a correctness oracle.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TASKPLANE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if TASKPLANE not in sys.path:
    sys.path.insert(0, TASKPLANE)

# Every env var that may vary the dispatch path, tier->model resolution, or
# contract slot is cleared so both live producers receive the same inputs.
SCRUB_VARS = ("CODEX_HOME", "CODEX_THREAD_ID", "TASKPLANE_MODEL_CHEAP",
              "TASKPLANE_MODEL_STANDARD", "TASKPLANE_MODEL_DEEP",
              "TASKPLANE_REASONING_CHEAP", "TASKPLANE_REASONING_STANDARD",
              "TASKPLANE_REASONING_DEEP",
              "TASKPLANE_WORKFLOWS", "CLAUDE_CODE_WORKFLOWS",
              "TASKPLANE_TASK", "TASKPLANE_SESSION_ID")

STAGES = ("execute", "evaluate", "fix")

# journey constants — part of the frozen fixture (stable ids)
GOAL = "stage wave fixture"
TASKS = [
    {"id": "t1", "scope": ["src/alpha/**", "tests/test_alpha.py"],
     "tests": "python3 -m pytest -q tests/test_alpha.py::test_current_contract",
     "criteria": ["alpha updated"], "new_modules": ["alpha"],
     "evaluation_evidence_edges": [{
         "producer": "src/alpha/m.py", "consumer": "tests/test_alpha.py",
         "selector": "tests/test_alpha.py::test_current_contract",
         "freshness_inputs": ["candidate_sha", "source_tree"],
         "severed_edge": {
             "mutation": "remove the alpha implementation",
             "selector": "tests/test_alpha.py::test_current_contract"}}],
     "changed_interfaces": [], "classified_failures": []},
    {"id": "t2", "scope": ["src/beta/**", "tests/test_beta.py"],
     "tests": "python3 -m pytest -q tests/test_beta.py::test_current_contract",
     "criteria": ["beta updated"], "new_modules": ["beta"],
     "evaluation_evidence_edges": [{
         "producer": "src/beta/m.py", "consumer": "tests/test_beta.py",
         "selector": "tests/test_beta.py::test_current_contract",
         "freshness_inputs": ["candidate_sha", "source_tree"],
         "severed_edge": {
             "mutation": "remove the beta implementation",
             "selector": "tests/test_beta.py::test_current_contract"}}],
     "changed_interfaces": [], "classified_failures": []},
]

def _git(ws, *args):
    subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                    *args], cwd=ws, check=True, capture_output=True)


def build_repo(tmp: str) -> str:
    """The frozen fixture workspace: two disjoint one-file modules and the
    two-task plan, committed as the baseline."""
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "plan"))
    for d in ("src/alpha", "src/beta"):
        os.makedirs(os.path.join(ws, d))
        with open(os.path.join(ws, d, "m.py"), "w") as f:
            f.write("x = 1\n")
    os.makedirs(os.path.join(ws, "tests"))
    for module in ("alpha", "beta"):
        with open(os.path.join(ws, "tests", f"test_{module}.py"), "w") as f:
            f.write(f"from src.{module}.m import x\n\n"
                    "def test_current_contract():\n"
                    "    assert x in {1, 2}\n")
    os.makedirs(os.path.join(ws, ".taskplane"))
    os.environ["TASKPLANE_SESSION_ID"] = "stage-fixture"
    with open(os.path.join(ws, "plan", "tasks.json"), "w") as f:
        json.dump({"tasks": TASKS}, f, indent=2)
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    with open(os.path.join(ws, ".taskplane", "codex-hook.py"), "w") as f:
        f.write("#!/usr/bin/env python3\n")
    return ws


def cli(*argv) -> "tuple[int, str]":
    """Run the tp CLI in-process, capturing stdout — the byte surface the
    goldens pin."""
    import tp as _cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = _cli.main(list(argv))
    return rc, out.getvalue()


def finish_plan_lenses(ws, workspace, action, *, usage="unavailable", runtime=None):
    """Simulate only native dispatch/start/Stop; use real lifecycle owners."""
    if runtime is None:
        import loop as runtime
    loop = runtime
    from taskplane.tests.test_r0002_cross_host_journey import _digest
    plan = action["plan_team_plan"]
    events = []
    for index, worker in enumerate(plan["workers"]):
        expected = loop.tp.peek_expectation(ws, worker["task_name"], strict=True)
        loop.tp.record_design_dispatch_assignment_activity(ws, expected)
        loop.record_native_dispatch_observation(ws, expected=expected,
            native_task_name=worker["task_name"], observed_at=100 + index)
        assert loop.tp.commit_dispatch_verification(ws, worker["task_name"], worker["model"],
            expected, True, worker["reasoning_effort"], strict=True)
        event = {"cwd":ws, "session_id":os.environ.get("TASKPLANE_SESSION_ID", "plan-fixture-session"), "agent_id":f"plan-child-{index}",
            "agent_type":worker["task_name"], "task_name":worker["task_name"], "turn_id":f"turn-{index}"}
        bound = loop.tp.bind_worker_contract_event(ws, event)
        loop.tp.record_design_worker_start_activity(ws, bound, event)
        material = {"schema":"taskplane.plan-lens-result/v1", "lens":worker["lens"],
            "worker_identity":worker["task_name"], "team_plan_fingerprint":plan["fingerprint"],
            "candidate_fingerprint":plan["candidate_fingerprint"], "outcome":"pass", "findings":[]}
        path = workspace / worker["output"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**material, "fingerprint":_digest(material)}))
        if usage == "measured":
            loop.record_observed_dispatch_usage(ws, task_id=worker["lens"],
                native_task_name=worker["task_name"], source_fingerprint="a" * 64,
                normalized_usage={"schema":loop.spend.USAGE_SCHEMA, "available":True,
                    "cached_input_tokens":60, "uncached_input_tokens":40, "output_tokens":10,
                    "raw_total_tokens":110, "reasoning_tokens":5})
            sealed = loop.finalize_observed_dispatch_usage(ws, task_id=worker["lens"],
                native_task_name=worker["task_name"], ended_at=110 + index, outcome="success")
            event["usage_reference"] = {"schema":"taskplane.native-dispatch-usage-reference/v1",
                "dispatch_receipt":sealed["receipt"]}
        elif usage != "missing":
            sealed = loop.finalize_observed_dispatch_usage(ws, task_id=worker["lens"],
                native_task_name=worker["task_name"], ended_at=110 + index, outcome="success",
                usage_unavailable=True, unavailable_reason="simulated host has no counter provider")
            assert sealed["status"] == "unavailable"
            assert sealed["binding"]["usage"] is None
        terminal = loop.tp.terminalize_worker_contract(ws, {**event, "outcome":"success"},
            outcome="success", submission_status="not_required")
        assert terminal
        events.append(event)
    return events


def prepare_plan(ws, *, runtime=None, usage="unavailable"):
    """Complete the simulated Plan lens prerequisite, not the Plan gate."""
    if runtime is None:
        import loop as runtime
    assert runtime.load(ws)["step"] == "plan"
    action = runtime.next_action(ws)
    assert not action.get("error"), {key: action[key] for key in ("error", "dor") if key in action}
    finish_plan_lenses(ws, Path(ws), action, runtime=runtime, usage=usage)
    return action


def start_loop(ws: str) -> None:
    """init → plan gate → human plan approval → EXECUTE (parallel)."""
    import loop
    from tests.root_session_fixture import open_delivery_root
    loop.init(ws, GOAL, spec_path="s", checkpoints=["plan"], parallel=True)
    prepare_plan(ws, runtime=loop, usage="measured")
    loop.gate(ws, "pass")
    loop.approve(ws)
    open_delivery_root(ws)


def build_task(ws: str, tid: str, module: str) -> None:
    """One wave worker's task-rail journey: worktree → claim → edit →
    commit → submit → orchestrator gate."""
    import loop
    aws = os.path.join(ws, ".tp-work", tid)
    _git(ws, "worktree", "add", "-q", aws, "-b", f"tp/{tid}")
    claimed = loop.claim(ws, tid, aws)
    assert claimed.get("claimed") == tid, claimed
    with open(os.path.join(aws, "src", module, "m.py"), "w") as f:
        f.write("x = 2\n")
    _git(aws, "add", "-A")
    _git(aws, "commit", "-qm", tid)
    assert loop.submit(ws, "pass", task_id=tid).get("submitted")
    assert loop.gate(ws, "pass", task_id=tid).get("built")


def to_fix_step(ws: str) -> None:
    """Classify one product failure, then enter FIX for that task."""
    import loop
    import evaluation_output
    import failure_routing

    assert loop.submit(ws, "fail", note="repro: alpha regression").get(
        "submitted")
    state = loop.load(ws)
    task = state["tasks"][state["current_task"]]
    act_ws = task.get("workspace") or ws
    evidence = {
        "selector": "stage_fixture::alpha_regression",
        "returncode": 1,
    }
    candidate = loop._failure_candidate_identity(act_ws, task)
    verdict = {
        "schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": task["id"],
        "requirement": str(task.get("req") or ""),
        "verdict": "fail",
        "evaluation": {"status": "complete", "reason_code": "none",
                       "detail": "typed product failure"},
        "criteria": [{"criterion": task["criteria"][0],
                      "status": "not-met", "evidence": "failure-alpha"}],
        "graph": {"dispositions": [], "requirements_checked": [],
                  "contracts_checked": []},
        "failures": [{
            "schema": failure_routing.FAILURE_RECORD_SCHEMA_ID,
            "id": "failure-alpha", "source": "pytest",
            "stage": "evaluate",
            "repro": "run stage_fixture::alpha_regression",
            "evidence": evidence,
            "evidence_digest": failure_routing.evidence_digest(evidence),
            "class": "product", "reason": "alpha behavior regressed",
            "owner": "product-code", "cluster": "alpha-regression",
            "route": "fix", "candidate": candidate,
        }],
    }
    verdict_path = loop.runtime_storage.evaluation_path(act_ws)
    os.makedirs(os.path.dirname(verdict_path), exist_ok=True)
    with open(verdict_path, "w", encoding="utf-8") as handle:
        json.dump(verdict, handle, sort_keys=True)
        handle.write("\n")
    out = loop.gate(ws, "fail")
    assert out.get("step") == "fix", out


def capture_stage(ws: str, stage: str, *extra) -> str:
    """The stage's Task-path stdout via the REAL CLI surface."""
    sub = "wave" if stage == "execute" else "next"
    rc, out = cli("loop", "--workspace", ws, sub, *extra)
    assert rc == 0, out
    return out


def journey(ws: str) -> "dict[str, str]":
    """Drive the frozen journey and return each stage's bare Task-path
    stdout: execute (the two-task wave), evaluate (t1 built → evaluated),
    fix (t1's evaluation failed)."""
    captures = {}
    start_loop(ws)
    captures["execute"] = capture_stage(ws, "execute")
    build_task(ws, "t1", "alpha")
    build_task(ws, "t2", "beta")
    captures["evaluate"] = capture_stage(ws, "evaluate")
    to_fix_step(ws)
    captures["fix"] = capture_stage(ws, "fix")
    return captures


def store_root(ws: str) -> str:
    """The external store root for the capture — resolve it WHILE the
    capture's TASKPLANE_HOME is in effect (env-dependent)."""
    import taskplane_lite as tp
    return tp.external_store_root(ws)
