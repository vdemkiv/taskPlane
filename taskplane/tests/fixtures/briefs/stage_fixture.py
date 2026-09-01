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
    {"id": "t1", "scope": ["src/alpha/**"], "tests": "true",
     "criteria": ["alpha updated"], "new_modules": ["alpha"]},
    {"id": "t2", "scope": ["src/beta/**"], "tests": "true",
     "criteria": ["beta updated"], "new_modules": ["beta"]},
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


def start_loop(ws: str) -> None:
    """init → plan gate → human plan approval → EXECUTE (parallel)."""
    import loop
    loop.init(ws, GOAL, spec_path="s", checkpoints=["plan"], parallel=True)
    loop.next_action(ws)
    loop.gate(ws, "pass")
    loop.approve(ws)


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
    """Fail the current evaluation → the loop enters FIX for that task."""
    import loop
    assert loop.submit(ws, "fail", note="repro: alpha regression").get(
        "submitted")
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
