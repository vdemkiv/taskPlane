"""The Evaluate-Loop engine — owned by taskplane.

taskplane owns the loop state machine, sequences the DoR/DoD gates, activates
each step's contract (so the PreToolUse hook enforces it), and records every
transition to `.taskplane/trace.jsonl`. The role agents are pluggable step
workers: the engine tells the driver which role to run and under which
contract; the driver runs it and reports the outcome back via `gate`.

State machine (per docs/loop-design.md, answers locked 2026-07-11):
  init → (pm if free-text goal, else optional design or plan)
  pm      → optional design → design_approval (human) → plan
  plan    → plan_approval (human) → execute
  execute → evaluate
  evaluate: pass → next task, or → em when all tasks pass
            fail → fix (if fix_cycles < max) else escalated (human)
  fix     → evaluate
  em      → signoff (human) → done
  escalated → (human) retry | skip | abort

Human gates: design approval (when requested), plan approval, and EM sign-off.
On FAIL: auto-fix up to
max_fix_cycles (default 2), then escalate. Goal input: free-text (→pm) or an
existing spec (→plan).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time

import depgraph
import kb
import lens as lens_router
import requirements as reqs
import taskplane_lite as tp

LOOP_FILE = "loop.json"


def _state_dir(ws: str) -> str:
    """Loop coordination state. v1.5.1: state is PER-USER even in team/repo
    knowledge mode — share knowledge, not the state machine. Two teammates'
    concurrent loops in a committed loop.json are guaranteed unmergeable
    conflicts, and flock on a git-round-tripped file serializes nothing
    across machines. The ONE exception is the explicit TASKPLANE_STORE=repo
    env override (Claude Tag): there the sandbox is ephemeral and
    single-writer, so committed state is exactly what lets the next session
    resume the loop."""
    if tp.store_env() == "repo":
        return os.path.join(tp.kb_root(ws), "state")
    ext = os.path.join(tp.external_store_root(ws), "knowledge", "state")
    if os.path.exists(os.path.join(ext, LOOP_FILE)):
        return ext
    legacy = os.path.join(ws, "knowledge", "state")   # unmigrated project
    if os.path.exists(os.path.join(legacy, LOOP_FILE)):
        return legacy
    return ext

# Per-step contract recipes. Non-build steps are read-only with a write-allow
# so they can only touch their own artifact dir; build steps get a real scope.
# pm and em are two deliberate personas (split in v0.8.0): tp-product owns
# the requirement; tp-engineering owns the final all-lens review.
STEP_ROLE = {
    "pm": "tp-product",
    "design": "tp-designer",
    "plan": "tp-planner",
    "execute": "tp-executor",
    "evaluate": "tp-evaluator",
    "fix": "tp-fixer",
    "em": "tp-engineering",
}
HUMAN_STEPS = {"design_approval", "plan_approval", "selection", "signoff", "escalated",
               "done", "failed"}

# A task is SETTLED when nothing further is owed on it: it passed, or the
# selection gate closed it (not_selected / reference), or a human skipped it.
# Wave readiness and "are we done?" both reason over this set.
SETTLED = {"passed", "not_selected", "reference", "skipped",
           "done", "external"}
# Statuses that SATISFY a dependency: the work exists (passed here,
# `done` seeded from outside the loop, `external` deferred to an
# external gate by an explicit human decision). `skipped` settles a
# task but does NOT satisfy its dependents (they cascade-skip).
DEP_SATISFIED = {"passed", "done", "external"}

# The canonical governance rail — (step, label). This is the SINGLE source a
# view renders its timeline from; the engine owns the machine, so a dashboard
# must derive its pipeline from here (via display_pipeline) rather than
# re-encode it and drift. is-human comes from HUMAN_STEPS, role from STEP_ROLE.
PIPELINE = [
    ("pm", "PM"), ("design", "Design"),
    ("design_approval", "Approve design"),
    ("plan", "Plan"), ("plan_approval", "Approve"),
    ("execute", "Execute"), ("evaluate", "Evaluate"), ("fix", "Fix"),
    ("em", "EM"), ("signoff", "Sign-off"), ("done", "Done"),
]
# The A/B selection gate is spliced in before 'em', but only for an A/B loop
# that hasn't selected yet — one place owns that rule (display_pipeline).
SELECTION_STEP = ("selection", "Select")


def splice_selection(rail: list, state: dict | None) -> list:
    """Insert the A/B 'selection' gate before 'em' when the loop is an A/B
    round that hasn't selected yet. `rail` is any list whose items' [0] is a
    step id (with or without label/flag). Returns a NEW list. This is the ONE
    place the splice rule lives, so render()'s full rail and widget()'s
    collapsed spine can't disagree."""
    if not (state and state.get("ab") and not state.get("selection")):
        return list(rail)
    ids = [r[0] for r in rail]
    i = ids.index("em") if "em" in ids else len(rail)
    sel = (SELECTION_STEP[0], SELECTION_STEP[1], True)
    return list(rail[:i]) + [sel] + list(rail[i:])


def display_pipeline(state: dict | None = None) -> list:
    """The ordered rail a view should render: list of (step, label, is_human).
    Both dashboard.render() and dashboard.widget() derive from the engine
    (this + splice_selection), so the timeline and the human-gate set can't
    drift between the two renderers or from the engine."""
    rows = list(PIPELINE)
    if not (state and state.get("design_required")):
        rows = [row for row in rows
                if row[0] not in ("design", "design_approval")]
    elif state.get("design_only"):
        rows = [row for row in rows
                if row[0] in ("pm", "design", "design_approval", "done")]
    rail = [(s, lbl, s in HUMAN_STEPS) for s, lbl in rows]
    return splice_selection(rail, state)


def _next_unsettled_index(state: dict, after: int):
    """Next task index strictly after `after` whose task is not SETTLED, or
    None when the rest are all settled. Serial advance uses this so a task
    the skip-cascade already settled is never re-executed."""
    tasks = state.get("tasks") or []
    for i in range(after + 1, len(tasks)):
        if tasks[i].get("status") not in SETTLED:
            return i
    return None


def _loop_path(ws: str) -> str:
    return os.path.join(_state_dir(ws), LOOP_FILE)


def _legacy_loop_path(ws: str) -> str:
    return os.path.join(tp.tp_dir(ws), LOOP_FILE)


def load(ws: str) -> dict | None:
    p = _loop_path(ws)
    if not os.path.exists(p):
        p = _legacy_loop_path(ws)          # pre-spec state, read once
        if not os.path.exists(p):
            return None
    with open(p) as f:
        return json.load(f)


def save(ws: str, state: dict) -> None:
    os.makedirs(_state_dir(ws), exist_ok=True)
    # Atomic write: parallel wave workers gate concurrently against the shared
    # loop.json — a torn read of a half-written file is a corrupt loop that
    # stalls everyone. Write a temp file and rename so a reader only ever sees
    # a complete state. (Lost-update races between concurrent read-modify-write
    # are serialized by `mutate()` below, which holds an exclusive lock across
    # the whole load→change→save.)
    p = _loop_path(ws)
    tmp = p + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, p)
    legacy = _legacy_loop_path(ws)         # migrate: single source of truth
    if os.path.exists(legacy):
        tp.safe_remove(legacy)


@contextlib.contextmanager
def mutate(ws: str):
    """Serialize a read-modify-write of the shared loop state. Concurrent wave
    workers each do load()→change→save(); without a lock two workers can read
    the same state and the second save clobbers the first's update (a gated
    task silently reverts to running and the loop stalls). An exclusive
    flock held across the whole critical section prevents that. Yields the
    current state dict; persists it on clean exit.

        with loop.mutate(ws) as st:
            task = next(t for t in st['tasks'] if t['id'] == tid)
            task['status'] = 'built'
    """
    os.makedirs(_state_dir(ws), exist_ok=True)
    lock_path = _loop_path(ws) + ".lock"
    lf = open(lock_path, "w")
    try:
        try:
            import fcntl
            fcntl.flock(lf, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass                            # best-effort on platforms w/o flock
        st = load(ws)
        yield st
        if st is not None:
            save(ws, st)
    finally:
        lf.close()


def init(ws: str, goal: str, spec_path: str | None = None,
         max_fix_cycles: int = 2, checkpoints=None,
         requirement_id: str | None = None, parallel: bool = False,
         design: bool = False, design_only: bool = False) -> dict:
    checkpoints = list(checkpoints if checkpoints is not None else
                       ["plan", "em"])
    state = {
        "governance_revision": 2,
        # Workers submit evidence; only the driver asks the engine to evaluate
        # a gate.  Older persisted loops omit this flag and remain resumable.
        "submission_required": True,
        "graph_governance": True,
        "goal": goal,
        "parallel": bool(parallel),
        "design_required": bool(design or design_only),
        "design_only": bool(design_only),
        "requirement_id": requirement_id,
        "spec_path": spec_path,
        "max_fix_cycles": int(max_fix_cycles),
        "checkpoints": checkpoints,
        "step": ("design" if spec_path and (design or design_only)
                 else "plan" if spec_path else "pm"),
        "tasks": None,
        "current_task": 0,
    }
    save(ws, state)
    tp.trace(ws, "loop_init", goal=goal, spec_path=spec_path,
             first_step=state["step"], max_fix_cycles=max_fix_cycles,
             checkpoints=checkpoints, design=bool(design or design_only),
             design_only=bool(design_only))
    # v2.0.0: point the driver at prior gate snapshots (context cache) -
    # read the published state instead of re-deriving it.
    with contextlib.suppress(Exception):
        art = os.path.join(tp.store_root(ws), "artifacts")
        tracks = sorted(os.listdir(art)) if os.path.isdir(art) else []
        if tracks:
            return {**state, "prior_artifacts": {
                "path": art, "tracks": tracks,
                "note": "prior gate snapshots (dashboard, plan, "
                        "findings, graph, HEADLINES) - read these "
                        "before re-deriving context"}}
    return state


# --------------------------------------------------------------- contracts

def _step_contract(step: str, state: dict) -> dict:
    task = _current_task(state)
    if step == "pm":
        return tp.build_contract(
            f"PM: {state['goal']}", read_only=True,
            write_allow=["specs/**", "docs/**"],
            tools=["Read", "Grep", "Glob", "WebSearch", "Write"])
    if step == "design":
        return tp.build_contract(
            f"DESIGN: {state['goal']}", read_only=True,
            write_allow=["design/**"],
            tools=["Read", "Grep", "Glob", "WebSearch", "Write"])
    if step == "plan":
        return tp.build_contract(
            f"PLAN: {state['goal']}", read_only=True, write_allow=["plan/**"],
            tools=["Read", "Grep", "Glob", "Write"])
    if step in ("execute", "fix"):
        verb = "EXECUTE" if step == "execute" else "FIX"
        return tp.build_contract(
            f"{verb}: {task['id']}", scope=task["scope"],
            test_command=task.get("tests"),
            tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit",
                   "MultiEdit"])
    if step == "evaluate":
        return tp.build_contract(
            f"EVALUATE: {task['id']}", read_only=True,
            write_allow=[".eval/**"],
            tools=["Read", "Grep", "Glob", "Bash", "Write"])
    if step == "em":
        return tp.build_contract(
            "EM review", read_only=True, write_allow=[".em-review/**"],
            tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit"])
    raise ValueError(f"no contract for step {step}")


def _current_task(state: dict):
    tasks = state.get("tasks")
    if not tasks:
        return None
    i = state.get("current_task", 0)
    return tasks[i] if 0 <= i < len(tasks) else None


def _edge_nudges(ws: str, changed, base: str) -> list:
    """Spot side-effect channels the import scanner cannot see (v2.0.0):
    SQL/migrations, HTTP calls, queue/topic messaging in the diff. Each
    nudge asks the reviewer to record the runtime edge (`tp graph edge`)
    so the NEXT change to that surface has a true blast radius."""
    import re as _re
    import subprocess as _sp
    nudges = []
    try:
        names = " ".join(changed)
        if _re.search(r"\.sql\b|/migrations?/", names):
            nudges.append(
                "diff touches SQL/migrations - schema changes ripple to "
                "every consumer of those tables; record the edge: "
                "tp graph edge <consumer-module> <db-module> --kind data")
        diff = _sp.run(["git", "diff", "-U0", base, "--", *changed[:50]],
                       cwd=ws, capture_output=True, text=True
                       ).stdout[:60000]
        added = "\n".join(l for l in diff.splitlines()
                           if l.startswith("+"))
        if _re.search(r"https?://|requests\.|urllib|fetch\(|axios"
                      r"|http\.client|HttpClient", added):
            nudges.append(
                "diff adds HTTP calls - cross-service effects are not "
                "import edges; record them: tp graph edge <this-module> "
                "<called-service> --kind runtime")
        if _re.search(r"publish|subscribe|topic|queue|kafka|sqs|rabbit"
                      r"|emit\(", added, _re.I):
            nudges.append(
                "diff touches messaging (topic/queue) - consumers are "
                "invisible to the import graph; record them: tp graph "
                "edge <consumer> <contract:event-name> --kind consumes; "
                "record the producer with --kind provides. Dependency edges "
                "point from the dependent to the contract so contract changes "
                "impact consumers in the correct direction")
    except Exception:
        pass
    return nudges


def _diff_files(ws: str, base: str) -> list:
    import subprocess

    def run(args):
        return subprocess.run(["git", *args], cwd=ws, capture_output=True,
                              text=True).stdout
    return [f for f in (run(["diff", "--name-only", base])
                        + run(["ls-files", "--others",
                               "--exclude-standard"])).splitlines() if f]


# --------------------------------------------------------------- parallel

def _stems(globs) -> set:
    # A glob's fixed prefix, as path SEGMENTS. Drop empty stems (a leading
    # `**/…` has no fixed prefix — it must not be treated as "matches
    # everything" or it would conflict with every other task).
    out = set()
    for g in (globs or []):
        if not g:
            continue
        stem = g.split("*", 1)[0].rstrip("/")
        if stem:
            out.add(stem)
    return out


def _seg_prefix(x: str, y: str) -> bool:
    """True when path `x` is `y` or a descendant of `y` — on SEGMENT
    boundaries, so `src/a` is inside `src` but `src/ab` is NOT inside
    `src/a`."""
    return x == y or x.startswith(y + "/")


def _scopes_overlap(a, b) -> bool:
    """Two scopes conflict when one's fixed prefix contains the other's, on
    path-segment boundaries — conflicting tasks are serialized into later
    waves. Segment-aware so sibling dirs (src/a vs src/ab) do NOT collide,
    and empty-prefix globs don't conflict with everything."""
    sa, sb = _stems(a), _stems(b)
    return any(_seg_prefix(x, y) or _seg_prefix(y, x) for x in sa for y in sb)


def wave(ws: str) -> dict:
    """The next parallel wave: every task whose dependencies have PASSED
    and whose scope is disjoint from the rest of the wave. Each entry ships
    its own contract + primed lenses + requirement — one governed agent per
    task, each in its own worktree. THE HARNESS IS PER AGENT: a worker's
    hook enforces its own task's contract in its own workspace."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    if not state.get("parallel"):
        return {"error": "loop is serial — `loop init --parallel` to enable"}
    if state["step"] != "execute":
        return {"error": f"waves only at execute (current: {state['step']})"}
    tasks = state.get("tasks") or []
    passed = {t["id"] for t in tasks
              if t.get("status") in DEP_SATISFIED}
    ready, held = [], []
    for t in tasks:
        if t.get("status") != "pending":
            continue
        if not set(t.get("deps") or []) <= passed:
            held.append({"task": t["id"],
                         "reason": "waiting on deps: "
                         + ",".join(sorted(set(t.get("deps") or []) - passed))})
            continue
        clash = [c["id"] for c in ready
                 if _scopes_overlap(t.get("scope"), c.get("scope"))
                 # A/B variants are alternatives in separate worktrees —
                 # overlapping scope between DIFFERENT variants is the
                 # point, not a conflict; they never merge.
                 and not (state.get("ab") and t.get("variant")
                          and c.get("variant")
                          and t.get("variant") != c.get("variant"))]
        if clash:
            held.append({"task": t["id"],
                         "reason": f"scope overlaps {clash[0]} — next wave"})
            continue
        ready.append(t)

    entries = []
    for t in ready:
        prime = lens_router.prime_scope(t.get("scope"),
                                        task_type=t.get("type"))
        recalled = kb.retrieve(ws, files=t.get("scope") or [],
                               tags=[t["id"]], limit=3)
        rid = t.get("req") or state.get("requirement_id")
        rec = reqs.get_requirement(ws, rid) if rid else None
        is_variant = bool(state.get("ab") and t.get("variant"))
        entries.append({
            "task": {"id": t["id"], "scope": t.get("scope"),
                     "tests": t.get("tests"), "deps": t.get("deps") or [],
                     "variant": t.get("variant")},
            "worktree": f".tp-work/{t['id']}",
            "merge_on_pass": not is_variant,
            "lenses": prime["lenses"],
            "requirement": rec and {"id": rec["id"], "title": rec["title"],
                                    "acceptance": rec["acceptance"]},
            "design": _design_context(ws, state),
            "knowledge": kb.render_context(recalled),
        })
    tp.trace(ws, "loop_wave", ready=[t["id"] for t in ready],
             held=[h["task"] for h in held])

    # Deadlock guard: nothing ready, nothing built to evaluate, yet tasks
    # are held — and none of them is held merely on a scope clash (which a
    # later wave clears). If every held task waits on a dep that can NEVER
    # pass (skipped/failed/absent) or on a cycle, the loop cannot self-
    # advance — surface it for the human instead of returning a silent
    # empty wave forever.
    built = any(t.get("status") == "built" for t in tasks)
    if not entries and not built and held:
        by_id = {t["id"]: t for t in tasks}
        stuck = []
        for h in held:
            t = by_id[h["task"]]
            unmet = set(t.get("deps") or []) - passed
            dead = [d for d in unmet
                    if d not in by_id
                    or by_id[d].get("status") in ("skipped", "failed")]
            waiting_on_scope = "scope overlaps" in h["reason"]
            if dead or (unmet and not waiting_on_scope
                        and not any(by_id.get(d, {}).get("status")
                                    in (None, "pending", "running", "built")
                                    for d in unmet)):
                stuck.append({"task": h["task"], "blocked_by": sorted(unmet),
                              "dead_deps": dead})
        if stuck:
            tp.trace(ws, "loop_deadlock", stuck=[s["task"] for s in stuck])
            return {
                "step": "execute", "parallel": True, "wave": [], "held": held,
                "deadlock": stuck,
                "error": "wave deadlock — held tasks depend on tasks that "
                         "can never pass (skipped/failed/missing or a "
                         "dependency cycle). Resolve with `loop resolve "
                         "skip|abort`, or fix plan/tasks.json deps.",
            }

    return {
        "step": "execute", "parallel": True,
        "wave": entries, "held": held,
        "instruction": (
            "Dispatch ONE governed subagent per wave entry, concurrently. "
            "Per task: (1) `git worktree add <worktree> -b tp/<task>` from "
            "the approved baseline; (2) `tp.py loop claim <task> "
            "--agent-workspace <worktree>` — activates THAT task's contract "
            "in THAT worktree, so the hook confines the agent mechanically; "
            "(3) the subagent builds inside its worktree (TDD, primed "
            "lenses, acceptance criteria); (4) it COMMITS its work in the "
            "worktree (`git add -A && git commit`) and runs `tp.py loop "
            "submit pass|fail --task <id>`. The orchestrator alone runs "
            "the matching `loop gate`. When the wave empties, `loop next` "
            "evaluates each built task; on evaluate PASS merge its branch "
            "(`git merge tp/<task>`) and remove the worktree. "
            "EXCEPTION — entries with merge_on_pass=false are A/B variants: "
            "do NOT merge them; when all variants pass, the loop pauses at "
            "the SELECTION gate and the human picks what ships."),
    } if entries else {
        "step": "execute", "parallel": True, "wave": [], "held": held,
        "instruction": "no dispatchable tasks — evaluate built tasks via "
                       "`loop next`, or resolve held dependencies.",
    }


def claim(ws: str, task_id: str, agent_ws: str) -> dict:
    """Activate `task_id`'s contract in the worker's own workspace
    (worktree). From here the worker's PreToolUse hook enforces this task's
    scope/tools/commands — the core invariant: every parallel agent runs
    under the harness, individually."""
    # Two workers claiming concurrently must not both win the same task —
    # serialize the claim's read-check-write under the shared lock.
    with mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        t = next((x for x in state.get("tasks") or [] if x["id"] == task_id),
                 None)
        if t is None:
            return {"error": f"no task {task_id}"}
        if t.get("status") not in ("pending", "running"):
            return {"error": f"task {task_id} is {t.get('status')} — "
                             "not claimable"}
        contract = tp.build_contract(
            f"EXECUTE: {t['id']}", scope=t.get("scope"),
            test_command=t.get("tests"),
            tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit",
                   "MultiEdit"])
        agent_ws = os.path.abspath(agent_ws)
        snapshot = tp.git_head(agent_ws)
        dor_ready, blockers, warnings = tp.dor_check(
            contract, agent_ws, snapshot)
        if not dor_ready:
            tp.trace(ws, "loop_claim_blocked", task=task_id,
                     agent_workspace=agent_ws, dor_blockers=blockers)
            return {"error": "Definition of Ready failed — task was not "
                             "claimed", "task": task_id,
                    "dor": {"ready": False, "blockers": blockers,
                            "warnings": warnings}}
        tp.activate(agent_ws, contract, snapshot=snapshot)
        t["status"] = "running"
        t["workspace"] = agent_ws
    tp.trace(ws, "loop_claim", task=task_id, agent_workspace=agent_ws,
             dor_ready=dor_ready)
    return {"claimed": task_id, "workspace": agent_ws,
            "contract": {"scope": contract["coding"]["scope_paths"],
                         "tests": contract["coding"]["dod"]["test_command"]},
            "dor": {"ready": dor_ready, "blockers": blockers,
                    "warnings": warnings}}


# --------------------------------------------------------------- next / gate

def next_action(ws: str) -> dict:
    """Advance to the current step's work: activate its contract and return
    what the driver should run. Human steps pause without activating."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop — run `tp.py loop init` first"}
    step = state["step"]

    if step in HUMAN_STEPS:
        awaiting = {
            "design_approval": "human: review design/design.md and the "
                               "Design Contract, then `loop approve`",
            "plan_approval": "human: review plan/plan.md, then `loop approve`",
            "selection": "human: A/B gate — compare the variants (rendered "
                         "side by side, criteria + lenses + spend), then "
                         "`loop select <variant|task-id|hybrid>`",
            "signoff": "human: EM sign-off, then `loop approve`",
            "escalated": "human: `loop resolve retry|skip|abort` "
                         "(fix cycles exhausted)",
            "done": "loop complete",
            "failed": "loop aborted",
        }[step]
        out = {"step": step, "paused": True, "awaiting": awaiting,
               "status": status(ws)}
        if step == "selection":
            out["variants"] = [
                {"id": t["id"], "variant": t.get("variant"),
                 "status": t.get("status"), "scope": t.get("scope"),
                 "worktree": f".tp-work/{t['id']}"}
                for t in (state.get("tasks") or []) if t.get("variant")]
            out["instruction"] = (
                "Present BOTH variants for the human's pick: re-run each "
                "variant's tests (trust but verify), render both UIs side "
                "by side — live screenshots over mocks — with the criteria "
                "scoreboard, lens findings, and per-variant resource spend. "
                "Then WAIT; `loop select` only on their explicit choice.")
        if step == "signoff":
            # Run the MECHANICAL Definition-of-Done here so the human signs off
            # seeing both the EM's read-out AND the scope-diff/lint verdict.
            out["dod"] = _signoff_dod(ws, state)
        if step == "design_approval":
            design_errors = _design_dod_errors(ws, state)
            out["dod"] = {"passed": not design_errors,
                          "errors": design_errors,
                          "fingerprint": _design_evidence_fingerprint(ws)}
        return out

    # Parallel mode: EXECUTE is a wave (dispatch handled by `wave`/`claim`);
    # once workers report built, evaluate them one by one (read-only).
    if step == "execute" and state.get("parallel"):
        built = [i for i, t in enumerate(state.get("tasks") or [])
                 if t.get("status") == "built"]
        if built:
            state["current_task"] = built[0]
            state["step"] = step = "evaluate"
            save(ws, state)
        else:
            return wave(ws)

    # Defence in depth: a per-task step must have a current task. If the loop
    # ever reaches execute/fix/evaluate with none (e.g. a plan that produced
    # no tasks), return a structured error instead of crashing in
    # _step_contract on task["id"].
    if step in ("execute", "fix", "evaluate") and _current_task(state) is None:
        return {"error": f"loop step '{step}' has no current task — the plan "
                         f"produced no tasks, so the loop should not be here. "
                         f"Re-run the plan step (`loop gate fail`, then "
                         f"re-plan) or start over with `loop init`.",
                "step": step, "status": status(ws)}

    # Per-task steps run in the task's own workspace when one was claimed.
    act_ws = ws
    if step in ("evaluate", "fix") and state.get("parallel"):
        tws = (_current_task(state) or {}).get("workspace")
        act_ws = tws if tws and os.path.isdir(tws) else ws

    if step == "design" and not state.get("design_graph_fingerprint"):
        state["design_graph_fingerprint"] = (
            depgraph.load(ws).get("meta") or {}).get("content_fingerprint")
        save(ws, state)

    contract = _step_contract(step, state)
    snapshot = tp.git_head(act_ws)
    dor_ready, blockers, warnings = tp.dor_check(
        contract, act_ws, snapshot)
    if step == "design":
        design_dor = _design_dor(ws, state)
        blockers.extend(design_dor["blockers"])
        warnings.extend(design_dor["warnings"])
        dor_ready = not blockers
    tp.trace(ws, "loop_step", step=step, role=STEP_ROLE[step],
             task=(_current_task(state) or {}).get("id"),
             dor_ready=dor_ready, dor_blockers=blockers,
             dor_warnings=warnings)
    if not dor_ready:
        return {"error": "Definition of Ready failed — resolve blockers "
                         "before this step can start",
                "step": step, "role": STEP_ROLE[step],
                "dor": {"ready": False, "blockers": blockers,
                        "warnings": warnings},
                "status": status(ws)}
    # The graph is an input to evaluation, not a cache refreshed only after
    # review.  Serial work and the final merged-tree review can safely refresh
    # the shared graph here. Parallel task worktrees are deliberately deferred
    # until their branches merge; publishing one worker's partial graph as the
    # project graph would hide its siblings.
    if step == "em":
        # Make the final graph describe the merged, as-built system BEFORE
        # the engineering reviewer receives it.  Doing this at the EM gate
        # would invalidate the review's graph fingerprint at sign-off.
        try:
            _true_up_graph(ws, state)
        except Exception as exc:
            if state.get("graph_governance"):
                return {"error": f"graph true-up failed before {step}: {exc}",
                        "step": step, "status": status(ws)}
    elif step == "evaluate" and not state.get("parallel"):
        try:
            depgraph.scan(ws)
        except Exception as exc:
            if state.get("graph_governance"):
                return {"error": f"graph refresh failed before {step}: {exc}",
                        "step": step, "status": status(ws)}
    tp.activate(act_ws, contract, snapshot=snapshot)

    # Inject the handful of prior decisions relevant to this step's work, so
    # the role starts with context instead of re-deriving it (token savings).
    task = _current_task(state)
    query_files = (task or {}).get("scope") or []
    query_tags = ([task["id"]] if task else []) + [state["goal"][:24]]
    recalled = kb.retrieve(ws, files=query_files, tags=query_tags, limit=5)
    if recalled:
        tp.trace(ws, "kb_recall", step=step,
                 decisions=[d["id"] for d in recalled])

    # Lens wiring. EXECUTE/FIX: PRIME — the same lenses that will review the
    # change are named before it's built. EVALUATE/EM: ROUTE on the real diff
    # since plan approval, so review effort lands exactly where change did.
    routing = None
    if step in ("pm", "plan"):
        # Advisory tier: C-level lenses run at STRATEGY level, always-on at
        # the pm/plan steps — never on code.
        routing = lens_router.route(
            [], artifact_type="strategy",
            catalog=None)
    elif step == "design":
        # The design lens is mandatory at this phase, independent of diff
        # routing. Keep a fallback brief so an in-place minor update remains
        # resumable while the catalog file itself is being upgraded.
        routed = lens_router.route(
            [], task_type="solution-design", only=["solution-design"])
        routing = routed if routed.get("lenses") else {"lenses": [{
            "id": "solution-design", "name": "Solution design",
            "mode": "inline", "tier": "deep",
            "reasons": ["mandatory Design Contract lens"], "checks": [],
            "looks_for": "approach coherence, dependency boundaries, "
                         "trade-offs, failure modes, and verifiable delivery"
        }]}
    elif step in ("execute", "fix"):
        routing = lens_router.prime_scope((task or {}).get("scope"),
                                          task_type=(task or {}).get("type"))
    elif step in ("evaluate", "em"):
        diff_ws = ws
        if step == "evaluate" and state.get("parallel"):
            tws = (task or {}).get("workspace")
            diff_ws = tws if tws and os.path.isdir(tws) else ws
        # EVALUATE verifies per-task with the routed lenses; EM is the
        # FINAL review under the merged lead persona and runs the FULL
        # catalog — routed lenses deep, the rest as a quick sweep, so a
        # review never misses a category the router didn't predict.
        routing = lens_router.route_git_diff(
            diff_ws, base=state.get("baseline") or "HEAD",
            task_type=(task or {}).get("type"),
            breadth="all" if step == "em" else "routed")
    if routing:
        tp.trace(ws, "lens_route", step=step,
                 lenses=[[x["id"], x["mode"]] for x in routing["lenses"]])

    # Blast radius from the persistent dependency graph — the reviewer sees
    # what the change can break WITHOUT re-deriving dependencies (no tokens).
    imp = None
    if routing and step in ("evaluate", "em"):
        diff_ws = ws
        if step == "evaluate":
            tws = (task or {}).get("workspace")
            if tws and os.path.isdir(tws):
                diff_ws = tws
        changed = [f for f in _diff_files(
            diff_ws, state.get("baseline") or "HEAD")
            if not f.startswith(lens_router.LOOP_OWNED)]
        if changed or step == "em":
            review_policy = (_aggregate_impact_policy(state.get("tasks") or [])
                             if step == "em" else
                             depgraph.impact_policy(task or {}))
            imp = depgraph.impact(ws, changed, policy=review_policy)
            # Product side of the blast radius: which OTHER requirements'
            # surface this diff touches (their criteria may need re-checking)
            # and which requirements depend on the affected ones.
            prod = depgraph.product_impact(ws, changed)
            own = (task or {}).get("req") or state.get("requirement_id")
            own = depgraph._req_node(own) if own else None
            imp["affected_requirements"] = [
                r for r in prod["affected_requirements"] if r != own]
            imp["dependent_requirements"] = prod["dependent_requirements"]
            nudges = _edge_nudges(diff_ws, changed,
                                  state.get("baseline") or "HEAD")
            if nudges:
                imp["edge_suggestions"] = nudges
            tp.trace(ws, "graph_impact", step=step,
                     touched=imp["touched"],
                     impacted=imp["total_impacted"],
                     affected_reqs=imp["affected_requirements"])
    elif step in ("execute", "fix") and task:
        # v2.0.0: the BUILDER sees the blast radius BEFORE changing code
        # (previously only the judges at evaluate/em did) - side effects
        # get prevented, not just detected a loop-step later.
        scope = task.get("scope") or []
        if scope and depgraph.load(ws)["modules"]:
            mods = depgraph.modules_for_scope(scope)
            if mods:
                imp = depgraph.impact(
                    ws, mods, policy=depgraph.impact_policy(task))
                if not imp["touched"]:
                    imp = None
                else:
                    tp.trace(ws, "graph_impact", step=step,
                             touched=imp["touched"],
                             impacted=imp["total_impacted"])
    elif step == "design":
        design_req = reqs.get_requirement(ws, state.get("requirement_id"))
        design_scope = (design_req or {}).get("context_files") or []
        design_modules = depgraph.modules_for_scope(design_scope)
        if design_modules and depgraph.load(ws).get("modules"):
            design_policy = {"local_depth": 3,
                             "boundary_mode": "contract-only",
                             "contract_depth": 1,
                             "requirement_depth": 1}
            imp = depgraph.impact(ws, design_modules, policy=design_policy)
            tp.trace(ws, "graph_impact", step=step,
                     touched=imp["touched"],
                     impacted=imp["total_impacted"])

    # Requirement anchoring: this task's R-id (or the loop's) is the spine —
    # its acceptance criteria are the DoD the evaluator holds the work to.
    req_rec = None
    rid = (task or {}).get("req") or state.get("requirement_id")
    if rid:
        req_rec = reqs.get_requirement(ws, rid)

    # Capability-tier the model for this step's role: a per-task `model` tier
    # (a planner marks a simple task "cheap") wins, else the step default. The
    # DRIVER passes `model` to the Agent tool's `model` param — null = inherit
    # the session model (the portable default). See tp.model_for_tier.
    model_tier = tp.step_tier(step, task)
    model = tp.model_for_tier(model_tier)
    tp.trace(ws, "model_tier", step=step,
             task=(task or {}).get("id"), tier=model_tier, model=model)
    tp.record_expected_dispatch(ws, "step", STEP_ROLE[step], model_tier,
                                model, ref=(task or {}).get("id") or step)

    return {
        "step": step,
        "role": STEP_ROLE[step],
        "role_instructions": os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agents", STEP_ROLE[step] + ".md"),
        "codex_dispatch": ("Dispatch a general Codex subagent with the "
                           "role_instructions file plus this action payload "
                           "when the named role is not registered."),
        "task": task,
        "model_tier": model_tier,
        "model": model,
        "contract": {"read_only": bool(contract.get("read_only")),
                     "scope": contract["coding"]["scope_paths"],
                     "write_allow": contract.get("write_allow"),
                     "tests": contract["coding"]["dod"]["test_command"]},
        "dor": {"ready": dor_ready, "blockers": blockers,
                "warnings": warnings},
        "knowledge": {"decisions": recalled,
                      # R-0002: accepted decisions whose modules overlap this
                      # task's scope are ALWAYS in force — injected
                      # unconditionally, not relevance-ranked.
                      "governing_decisions": kb.governing(
                          ws, contract["coding"]["scope_paths"]),
                      # R-0004: the as-built inventory — ALWAYS in the brief
                      # when filled, so design work is judged as a delta
                      # against what exists, never in a vacuum.
                      "current_state": kb.current_state(ws),
                      "context": kb.render_context(recalled)},
        "lenses": routing["lenses"] if routing else None,
        "impact": imp and {**imp, "context": depgraph.render_context(imp)},
        "design": _design_context(ws, state),
        "design_graph": ({
            "baseline_fingerprint": state.get("design_graph_fingerprint"),
            "summary": depgraph.summary(ws),
            "policy": {"local_depth": 3,
                       "boundary_mode": "contract-only",
                       "contract_depth": 1,
                       "requirement_depth": 1},
            "rule": "propose modules and edges in design/contract.json; "
                    "do not mutate the as-built graph during Design"
        } if step == "design" else None),
        "requirement": req_rec and {
            "id": req_rec["id"], "title": req_rec["title"],
            "acceptance": req_rec["acceptance"],
            "open_questions": req_rec["open_questions"],
            "context": reqs.render_context([req_rec])},
        "instruction": _instruction(step, state),
    }


def _instruction(step: str, state: dict) -> str:
    t = _current_task(state)
    return {
        "pm": "Run tp-product: author specs/spec.md with "
              "testable acceptance criteria + a contract handoff. Return to "
              "the orchestrator; it validates with `loop gate pass`.",
        "design": "Run tp-designer (read-only toward product code): inspect "
                  "the requirement, current state, decisions, and baseline "
                  "graph; author design/design.md and design/contract.json "
                  "using schema taskplane.design/v1. Compare alternatives, "
                  "select the HOW, define modules/contracts plus graph DoR/DoD "
                  "and depth policy, map acceptance, handle risks/rollout, "
                  "apply solution-design, and create a visualization only "
                  "when it materially clarifies the choice. Never mutate the "
                  "as-built graph. Return to the orchestrator; it validates "
                  "with `loop gate pass` and then pauses for human approval.",
        "plan": "Run the tp-planner role: write plan/tasks.json (machine) "
                "and plan/plan.md (human) — tasks with scope, tests, "
                "criteria, dependencies, contracts, design_edges, and impact policy. When "
                "`design.approved` is true, cover its modules, edges, contracts, "
                "depth policy, and acceptance mapping without drift. Return "
                "to the orchestrator; it validates with `loop gate pass`.",
        "execute": f"Run the tp-executor on task {t and t['id']}: build "
                   "under this contract (TDD), honoring the PRIMED lenses "
                   "(see `lenses`) and the requirement's acceptance criteria "
                   "(see `requirement`) plus the approved Design Contract "
                   "when `design.approved` is true. Then `loop submit pass` (or `fail` "
                   "if you couldn't build it); only the orchestrator calls "
                   "`loop gate`.",
        "evaluate": f"Run the tp-evaluator (read-only) on task "
                    f"{t and t['id']}: run its tests + acceptance criteria, "
                    "then apply each ROUTED lens (see `lenses`; prompt at "
                    "lenses/<id>.md) — inline ones yourself, one governed "
                    "read-only subagent per subagent-mode lens. Write "
                    ".eval/verdict.json, including graph dispositions and "
                    "affected requirements; reject stale Design evidence. "
                    "Then `loop submit pass|fail`; "
                    "only the orchestrator calls `loop gate`.",
        "fix": f"Run the tp-fixer on task {t and t['id']}: repair the "
               "listed failures + add a regression test. Then `loop submit "
               "pass`; only the orchestrator calls `loop gate`.",
        "em": "Run tp-engineering (read-only): the `lenses` list is "
              "the FULL catalog — run tier=deep lenses at full depth (their "
              "mode says inline vs subagent) and every tier=sweep lens as a "
              "quick pass (its top checks against the diff; flag or clear). "
              "Synthesize all verdicts + requirement-vs-implementation into "
              ".em-review/report.md AND .em-review/findings.json (including "
              "complete meta.lens_coverage, meta.impact, meta.design "
              "conformance when an approved design exists, tests, and gate "
              "verdict), record the verdict to the knowledge "
              "base, then `loop submit pass`. The orchestrator validates "
              "with `loop gate pass` before presenting human sign-off.",
    }[step]


def _read_json(path: str) -> tuple[dict | None, list]:
    try:
        with open(path) as f:
            value = json.load(f)
    except FileNotFoundError:
        return None, [f"required evidence missing: {path}"]
    except (OSError, ValueError) as exc:
        return None, [f"invalid evidence {path}: {exc}"]
    if not isinstance(value, dict):
        return None, [f"invalid evidence {path}: root must be an object"]
    return value, []


# --------------------------------------------------------------- design

DESIGN_SCHEMA = "taskplane.design/v1"
DESIGN_CONTRACT = os.path.join("design", "contract.json")
DESIGN_NARRATIVE = os.path.join("design", "design.md")


def _design_path(ws: str, rel: str) -> str:
    return os.path.join(ws, rel)


def _design_contract(ws: str) -> tuple[dict | None, list]:
    return _read_json(_design_path(ws, DESIGN_CONTRACT))


def _design_safe_rel(rel) -> str | None:
    rel = str(rel or "").replace("\\", "/").strip()
    if (not rel or os.path.isabs(rel) or rel == ".."
            or rel.startswith("../") or "/../" in rel
            or not rel.startswith("design/")):
        return None
    return rel


def _design_evidence_paths(ws: str, contract: dict | None = None) -> list:
    paths = [DESIGN_CONTRACT, DESIGN_NARRATIVE]
    contract = contract or (_design_contract(ws)[0] or {})
    visual = contract.get("visualization") or {}
    if visual.get("required"):
        rel = _design_safe_rel(visual.get("path"))
        if rel:
            paths.append(rel)
    return paths


def _design_evidence_fingerprint(ws: str,
                                 contract: dict | None = None) -> str:
    """Fingerprint exactly the approved design evidence, not source code."""
    h = hashlib.sha256()
    for rel in sorted(set(_design_evidence_paths(ws, contract))):
        h.update(b"\0path\0")
        h.update(rel.encode("utf-8", errors="surrogateescape"))
        try:
            with open(_design_path(ws, rel), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"\0MISSING\0")
    return h.hexdigest()


def _design_current_errors(ws: str, state: dict) -> list:
    if not state.get("design_required") or not state.get("design_fingerprint"):
        return []
    contract, errors = _design_contract(ws)
    if errors:
        return ["approved design is unavailable: " + e for e in errors]
    current = _design_evidence_fingerprint(ws, contract)
    if current != state.get("design_fingerprint"):
        return ["approved design evidence changed after approval — return to "
                "Design and obtain a new human approval"]
    return []


def _design_dor(ws: str, state: dict) -> dict:
    """Entry gate for the proposed-HOW phase."""
    blockers, warnings = [], []
    rid = state.get("requirement_id")
    rec = reqs.get_requirement(ws, rid) if rid else None
    if not rid:
        blockers.append("Design must be anchored to a requirement R-id")
    elif rec is None:
        blockers.append(f"Design requirement {rid} does not exist")
    else:
        if not rec.get("acceptance"):
            blockers.append("Design requirement has no acceptance criteria")
        if rec.get("open_questions"):
            blockers.append("Design requirement has unresolved questions: "
                            + "; ".join(rec["open_questions"]))
    graph = depgraph.load(ws)
    meta = graph.get("meta") or {}
    if not meta.get("content_fingerprint"):
        blockers.append("baseline dependency graph is missing — run graph scan")
    elif meta.get("scanned_head") and meta.get("scanned_head") != tp.git_head(ws):
        blockers.append("baseline dependency graph is stale for the current HEAD")
    if not graph.get("modules"):
        warnings.append("baseline graph has no source modules; treat this as "
                        "greenfield and declare every proposed module")
    if not kb.current_state(ws):
        warnings.append("current-state inventory is empty; ground the design "
                        "in cited repository sources and the baseline graph")
    return {"ready": not blockers, "blockers": blockers,
            "warnings": warnings}


def _design_dod_errors(ws: str, state: dict) -> list:
    """Mechanical Design Contract completion and graph-isolation proof."""
    contract, errors = _design_contract(ws)
    if errors:
        return errors
    assert contract is not None

    def text(value) -> bool:
        return bool(str(value or "").strip())

    def object_field(name: str) -> dict:
        value = contract.get(name)
        if not isinstance(value, dict):
            errors.append(f"design {name} must be an object")
            return {}
        return value

    def text_list(value) -> bool:
        return (isinstance(value, list) and bool(value)
                and all(text(item) for item in value))

    if contract.get("schema") != DESIGN_SCHEMA:
        errors.append(f"design schema must be {DESIGN_SCHEMA}")
    if contract.get("requirement") != state.get("requirement_id"):
        errors.append("design requirement does not match the loop requirement")
    for field in ("title", "summary", "decision"):
        if not text(contract.get(field)):
            errors.append(f"design {field} is missing")

    current = object_field("current_state")
    if not text(current.get("summary")) or not text_list(current.get("sources")):
        errors.append("design current_state needs a summary and cited sources")

    alternatives = contract.get("alternatives") or []
    if not isinstance(alternatives, list) or len(alternatives) < 2:
        errors.append("design must compare at least two approaches")
        alternatives = []
    alt_ids = set()
    for alt in alternatives:
        if not isinstance(alt, dict):
            errors.append("every design alternative must be an object")
            continue
        aid = str(alt.get("id") or "").strip()
        if not aid or aid in alt_ids:
            errors.append("design alternatives need unique non-empty ids")
        alt_ids.add(aid)
        trade = alt.get("tradeoffs")
        if not isinstance(trade, dict):
            trade = {}
        if (not text(alt.get("name")) or not text(alt.get("description"))
                or not text_list(trade.get("gains"))
                or not text_list(trade.get("costs"))
                or not text(trade.get("revisit_when"))):
            errors.append(f"alternative {aid or '?'} needs description, "
                          "gains, costs, and revisit_when")
    if contract.get("selected_approach") not in alt_ids:
        errors.append("selected_approach does not name a declared alternative")

    modules = object_field("modules")
    declared_modules = {str(x).strip() for x in
                        list(modules.get("existing") or [])
                        + list(modules.get("new") or []) if str(x).strip()}
    if not declared_modules:
        errors.append("design modules must name existing or new modules")

    contracts = contract.get("contracts") or []
    if not isinstance(contracts, list):
        errors.append("design contracts must be a list")
        contracts = []
    contract_ids = set()
    for row in contracts:
        if not isinstance(row, dict) or not text(row.get("id")) \
                or not text(row.get("relation")) \
                or not text(row.get("description")):
            errors.append("every design contract needs relation, id, and description")
            continue
        if row.get("relation") not in ("provides", "consumes", "changes"):
            errors.append("design contract relation must be provides, consumes, or changes")
        contract_ids.add(str(row["id"]))
    rec = reqs.get_requirement(ws, state.get("requirement_id"))
    required_contracts = {
        str(row.get("id") if isinstance(row, dict) else row)
        for row in ((rec or {}).get("contracts") or [])
        if str(row.get("id") if isinstance(row, dict) else row).strip()
    }
    missing_contracts = sorted(required_contracts - contract_ids)
    if missing_contracts:
        errors.append("design omits requirement contracts: "
                      + ", ".join(missing_contracts))

    graph = object_field("graph")
    current_fp = (depgraph.load(ws).get("meta") or {}).get(
        "content_fingerprint")
    baseline_fp = state.get("design_graph_fingerprint")
    if not baseline_fp:
        errors.append("design graph baseline was not captured by the engine")
    if current_fp != baseline_fp:
        errors.append("as-built graph changed during Design; proposed edges "
                      "must remain an overlay")
    if graph.get("baseline_fingerprint") != baseline_fp:
        errors.append("design graph does not cite the captured baseline fingerprint")
    proposed_modules = {str(x).strip() for x in
                        (graph.get("proposed_modules") or []) if str(x).strip()}
    if not proposed_modules:
        errors.append("design graph has no proposed_modules")
    if not declared_modules <= proposed_modules:
        errors.append("design graph does not include every declared module")
    edges = graph.get("proposed_edges")
    if not isinstance(edges, list):
        errors.append("design graph proposed_edges must be a list")
        edges = []
    known = set((depgraph.load(ws).get("modules") or {})) | proposed_modules
    edge_nodes = set()
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("every proposed graph edge must be an object")
            continue
        for end in ("from", "to"):
            node = str(edge.get(end) or "").strip()
            if not node:
                errors.append(f"proposed graph edge is missing {end}")
            elif node not in known and not node.startswith(
                    ("contract:", "resource:", "svc:", "ext:")):
                errors.append(f"proposed graph edge has undeclared node: {node}")
            else:
                edge_nodes.add(node)
        if not text(edge.get("kind")) or not text(edge.get("reason")):
            errors.append("proposed graph edges need kind and reason")
    if contract_ids - edge_nodes:
        errors.append("design contracts are missing from the proposed graph: "
                      + ", ".join(sorted(contract_ids - edge_nodes)))
    policy = graph.get("depth_policy")
    if not isinstance(policy, dict):
        errors.append("design graph depth_policy must be an object")
        policy = {}
    try:
        local_depth = int(policy.get("local_depth"))
        contract_depth = int(policy.get("contract_depth"))
        requirement_depth = int(policy.get("requirement_depth"))
    except (TypeError, ValueError):
        local_depth = contract_depth = requirement_depth = -1
    if not 1 <= local_depth <= 10:
        errors.append("design graph local_depth must be between 1 and 10")
    if policy.get("boundary_mode") not in ("stop", "contract-only", "expand"):
        errors.append("design graph boundary_mode is invalid")
    if contract_depth < 0 or requirement_depth < 0:
        errors.append("design graph contract/requirement depth must be non-negative")
    if policy.get("boundary_mode") == "contract-only" and contract_depth > 1:
        errors.append("contract-only design may traverse only one contract level")
    for field in ("dor", "dod"):
        rows = graph.get(field)
        if not isinstance(rows, list) or not rows:
            errors.append(f"design graph {field} checks are missing")
            continue
        for row in rows:
            if not isinstance(row, dict) or not text(row.get("check")) \
                    or not text(row.get("evidence")):
                errors.append(f"every graph {field} check needs check and evidence")

    criteria = list((rec or {}).get("acceptance") or [])
    mapping = contract.get("acceptance_map") or []
    if not isinstance(mapping, list):
        errors.append("design acceptance_map must be a list")
        mapping = []
    mapped = [row.get("criterion") for row in mapping
              if isinstance(row, dict)]
    for criterion in criteria:
        rows = [row for row in mapping if isinstance(row, dict)
                and row.get("criterion") == criterion]
        if len(rows) != 1 or not text(rows[0].get("design_element")) \
                or not text(rows[0].get("validation")):
            errors.append("acceptance criterion lacks one complete design mapping: "
                          + criterion)
    extras = sorted({str(x) for x in mapped if x not in criteria})
    if extras:
        errors.append("design maps unknown acceptance criteria: "
                      + ", ".join(extras))

    for field, required in (("risks", ("risk", "mitigation", "owner")),
                            ("failure_modes", ("mode", "detection", "recovery"))):
        rows = contract.get(field)
        if not isinstance(rows, list) or not rows:
            errors.append(f"design {field} evidence is missing")
            continue
        for row in rows:
            if not isinstance(row, dict) or any(not text(row.get(k))
                                                for k in required):
                errors.append(f"every {field} row needs " + ", ".join(required))
    observability = object_field("observability")
    if not text_list(observability.get("signals")):
        errors.append("design observability signals are missing")
    if not text_list(observability.get("alerts")):
        errors.append("design observability alerts or an explicit none rationale are missing")
    rollout = object_field("rollout")
    if not text(rollout.get("strategy")) or not text(rollout.get("rollback")):
        errors.append("design rollout needs strategy and rollback")

    visual = object_field("visualization")
    if not isinstance(visual.get("required"), bool):
        errors.append("design visualization.required must be boolean")
    elif visual.get("required"):
        rel = _design_safe_rel(visual.get("path"))
        if visual.get("kind") not in (
                "dependency-graph", "sequence", "state-transition",
                "data-flow", "ui-flow") or not rel:
            errors.append("required design visualization needs kind and safe design/ path")
        elif not os.path.isfile(_design_path(ws, rel)) \
                or os.path.getsize(_design_path(ws, rel)) == 0:
            errors.append("required design visualization is missing or empty")
    elif not text(visual.get("reason")):
        errors.append("skipped design visualization needs a reason")

    lens_evidence = contract.get("lens_evidence") or []
    if not isinstance(lens_evidence, list):
        errors.append("design lens_evidence must be a list")
        lens_evidence = []
    evidence = [row for row in lens_evidence
                if isinstance(row, dict)
                and row.get("lens") == "solution-design"]
    try:
        solution_blockers = int(evidence[0].get("blockers") or 0) \
            if len(evidence) == 1 else -1
    except (TypeError, ValueError):
        solution_blockers = -1
    if (len(evidence) != 1 or evidence[0].get("verdict") != "pass"
            or solution_blockers != 0
            or not text(evidence[0].get("evidence"))):
        errors.append("solution-design lens must pass with evidence and no blockers")
    if not isinstance(contract.get("open_questions"), list):
        errors.append("design open_questions must be a list")
    elif contract.get("open_questions"):
        errors.append("design has unresolved open_questions")
    try:
        with open(_design_path(ws, DESIGN_NARRATIVE), encoding="utf-8") as f:
            if not f.read().strip():
                errors.append("design narrative is empty")
    except OSError:
        errors.append("design narrative is missing: " + DESIGN_NARRATIVE)
    return errors


def _design_plan_errors(ws: str, state: dict) -> list:
    """Approved Design Contract → implementation plan conformance."""
    errors = _design_current_errors(ws, state)
    if errors or not state.get("design_required"):
        return errors
    contract, read_errors = _design_contract(ws)
    if read_errors:
        return read_errors
    assert contract is not None
    tasks = state.get("tasks") or []
    planned_modules = set()
    planned_contracts = set()
    planned_edges = set()
    for task in tasks:
        planned_modules.update(depgraph.modules_for_scope(task.get("scope") or []))
        planned_modules.update(str(x) for x in (task.get("new_modules") or []))
        for row in task.get("contracts") or []:
            cid = row.get("id") if isinstance(row, dict) else row
            if str(cid or "").strip():
                planned_contracts.add(str(cid))
        for row in task.get("design_edges") or []:
            if isinstance(row, dict):
                planned_edges.add(
                    f"{row.get('from')}->{row.get('to')}:{row.get('kind')}")
            elif str(row or "").strip():
                planned_edges.add(str(row))
    graph = contract.get("graph") or {}
    expected_modules = {str(x) for x in graph.get("proposed_modules") or []}
    missing_modules = sorted(expected_modules - planned_modules)
    if missing_modules:
        errors.append("approved design modules are not covered by the plan: "
                      + ", ".join(missing_modules))
    expected_contracts = {
        str(row.get("id") if isinstance(row, dict) else row)
        for row in contract.get("contracts") or []
    }
    missing_contracts = sorted(expected_contracts - planned_contracts)
    if missing_contracts:
        errors.append("approved design contracts are not covered by the plan: "
                      + ", ".join(missing_contracts))
    expected_edges = {
        f"{row.get('from')}->{row.get('to')}:{row.get('kind')}"
        for row in graph.get("proposed_edges") or [] if isinstance(row, dict)
    }
    missing_edges = sorted(expected_edges - planned_edges)
    if missing_edges:
        errors.append("approved design edges are not covered by the plan: "
                      + ", ".join(missing_edges))
    planned_policy = _aggregate_impact_policy(tasks)
    expected_policy = graph.get("depth_policy") or {}
    ranks = {"stop": 0, "contract-only": 1, "expand": 2}
    if (planned_policy.get("local_depth", 0)
            < int(expected_policy.get("local_depth", 0) or 0)
            or planned_policy.get("contract_depth", 0)
            < int(expected_policy.get("contract_depth", 0) or 0)
            or planned_policy.get("requirement_depth", 0)
            < int(expected_policy.get("requirement_depth", 0) or 0)
            or ranks.get(planned_policy.get("boundary_mode"), 1)
            < ranks.get(expected_policy.get("boundary_mode"), 1)):
        errors.append("plan dependency depth policy is narrower than the "
                      "approved design depth policy")
    return errors


def _design_review_errors(ws: str, state: dict, meta: dict) -> list:
    """Approved design → final as-built review evidence."""
    errors = _design_current_errors(ws, state)
    if errors or not state.get("design_required") or state.get("design_only"):
        return errors
    evidence = meta.get("design")
    if not isinstance(evidence, dict):
        return ["engineering review is missing approved-design conformance evidence"]
    if evidence.get("fingerprint") != state.get("design_fingerprint"):
        errors.append("engineering review uses the wrong design fingerprint")
    if evidence.get("verdict") != "conformant":
        errors.append("engineering review reports design drift; return to Design "
                      "and re-plan before sign-off")
    for field in ("modules_checked", "edges_checked", "contracts_checked",
                  "drift"):
        if not isinstance(evidence.get(field), list):
            errors.append(f"engineering design evidence {field} must be a list")
    contract, _ = _design_contract(ws)
    graph = (contract or {}).get("graph") or {}
    expected_modules = {str(x) for x in graph.get("proposed_modules") or []}
    as_built = depgraph.load(ws)
    actual_modules = set(as_built.get("modules") or {})
    unrealized_modules = expected_modules - actual_modules
    if unrealized_modules:
        errors.append("as-built graph is missing designed modules: "
                      + ", ".join(sorted(unrealized_modules)))
    checked_modules = {str(x) for x in evidence.get("modules_checked") or []} \
        if isinstance(evidence.get("modules_checked"), list) else set()
    if expected_modules - checked_modules:
        errors.append("engineering review did not check every designed module: "
                      + ", ".join(sorted(expected_modules - checked_modules)))
    expected_edges = {
        f"{row.get('from')}->{row.get('to')}:{row.get('kind')}"
        for row in graph.get("proposed_edges") or [] if isinstance(row, dict)
    }
    actual_edges = {
        f"{row.get('from')}->{row.get('to')}:{row.get('kind')}"
        for row in as_built.get("edges") or [] if isinstance(row, dict)
    }
    unrealized_edges = expected_edges - actual_edges
    if unrealized_edges:
        errors.append("as-built graph is missing designed edges: "
                      + ", ".join(sorted(unrealized_edges)))
    checked_edges = {str(x) for x in evidence.get("edges_checked") or []} \
        if isinstance(evidence.get("edges_checked"), list) else set()
    if expected_edges - checked_edges:
        errors.append("engineering review did not check every designed edge: "
                      + ", ".join(sorted(expected_edges - checked_edges)))
    expected_contracts = {
        str(row.get("id") if isinstance(row, dict) else row)
        for row in (contract or {}).get("contracts") or []
    }
    unrealized_contracts = expected_contracts - actual_modules
    if unrealized_contracts:
        errors.append("as-built graph is missing designed contracts: "
                      + ", ".join(sorted(unrealized_contracts)))
    checked_contracts = {str(x) for x in evidence.get("contracts_checked") or []} \
        if isinstance(evidence.get("contracts_checked"), list) else set()
    if expected_contracts - checked_contracts:
        errors.append("engineering review did not check every designed contract: "
                      + ", ".join(sorted(expected_contracts - checked_contracts)))
    if isinstance(evidence.get("drift"), list) and evidence.get("drift"):
        errors.append("engineering review contains unexplained design drift")
    return errors


def _design_context(ws: str, state: dict) -> dict | None:
    if not state.get("design_required"):
        return None
    contract, errors = _design_contract(ws)
    return {"approved": bool(state.get("design_fingerprint")),
            "fingerprint": state.get("design_fingerprint"),
            "contract": contract, "errors": errors}


def _criteria_for(ws: str, state: dict, task: dict) -> list:
    criteria = list(task.get("criteria") or [])
    rid = task.get("req") or state.get("requirement_id")
    rec = reqs.get_requirement(ws, rid) if rid else None
    if rec:
        criteria = list(rec.get("acceptance") or criteria)
    criteria = [str(c).strip() for c in criteria if str(c).strip()]
    # Minor-version compatibility for pre-1.6 plans: their runnable test
    # command was the only acceptance check. New planners emit explicit
    # criteria, but an existing plan remains executable and its test still
    # has to pass at every DoD gate.
    if not criteria and str(task.get("tests") or "").strip():
        criteria = [f"test command passes: {task['tests']}"]
    return criteria


def _aggregate_impact_policy(tasks) -> dict:
    """One fail-closed review radius for a multi-task final review."""
    policies = [depgraph.impact_policy(t) for t in (tasks or [])]
    if not policies:
        return depgraph.impact_policy({})
    boundary_rank = {"stop": 0, "contract-only": 1, "expand": 2}
    boundary = max(
        (p.get("boundary_mode", "contract-only") for p in policies),
        key=lambda value: boundary_rank.get(value, 1))
    def number(policy, key, default, minimum):
        try:
            return max(minimum, int(policy.get(key, default)))
        except (TypeError, ValueError):
            return default

    return {
        "local_depth": max(number(p, "local_depth", 3, 1)
                           for p in policies),
        "boundary_mode": boundary,
        "contract_depth": max(number(p, "contract_depth", 1, 0)
                              for p in policies),
        "requirement_depth": max(number(p, "requirement_depth", 1, 0)
                                 for p in policies),
    }


def _plan_dor_errors(ws: str, state: dict) -> list:
    """Definition of Ready for implementation, derived from the plan."""
    errors = []
    for task in state.get("tasks") or []:
        prefix = f"task {task.get('id', '?')}: "
        if not task.get("scope"):
            errors.append(prefix + "scope is missing")
        if not str(task.get("tests") or "").strip():
            errors.append(prefix + "test command is missing")
        if not _criteria_for(ws, state, task):
            errors.append(prefix + "acceptance criteria are missing")
        rid = task.get("req") or state.get("requirement_id")
        rec = reqs.get_requirement(ws, rid) if rid else None
        if rec:
            # Requirements own stable product/contract dependencies; the plan
            # may add contracts but cannot silently erase the requirement's
            # boundaries with an empty or narrower task-level list.
            merged_contracts, seen_contracts = [], set()
            for contract in list(rec.get("contracts") or []) + \
                    list(task.get("contracts") or []):
                contract_id = (contract.get("id")
                               if isinstance(contract, dict) else contract)
                contract_id = str(contract_id or "").strip()
                if contract_id and contract_id not in seen_contracts:
                    merged_contracts.append(contract)
                    seen_contracts.add(contract_id)
            task["contracts"] = merged_contracts
            for dep in rec.get("depends_on") or []:
                if reqs.get_requirement(ws, dep) is None:
                    errors.append(prefix + f"requirement dependency {dep} "
                                  "does not exist")
                else:
                    # Requirements are the source of truth. Reconcile their
                    # product edges before graph Ready instead of depending on
                    # a particular CLI path having populated the derived map.
                    depgraph.link_requirement_dep(ws, rid, dep)
            for contract in rec.get("contracts") or []:
                contract_id = (contract.get("id")
                               if isinstance(contract, dict) else contract)
                relation = (contract.get("relation", "changes")
                            if isinstance(contract, dict) else "changes")
                if str(contract_id or "").strip():
                    depgraph.record_edge(
                        ws, depgraph._req_node(rid), str(contract_id),
                        kind=relation, confidence="high")
        task["impact_policy"] = depgraph.impact_policy(task)
        if rid and task.get("high_cost"):
            if rec is None:
                errors.append(prefix + f"requirement {rid} does not exist")
            elif rec.get("open_questions"):
                errors.append(prefix + "requirement has unresolved questions: "
                              + "; ".join(rec["open_questions"]))
    graph_dor = depgraph.readiness(ws, state.get("tasks") or [])
    state["graph_dor"] = graph_dor
    errors.extend("graph DoR: " + e for e in graph_dor.get("errors") or [])
    errors.extend("design DoR: " + e for e in _design_plan_errors(ws, state))
    return errors


def _task_graph_dod(ws: str, state: dict, task: dict) -> dict:
    """As-built dependency proof for one task.

    Parallel worktrees cannot replace the shared graph before merge, so their
    final graph proof is explicitly deferred to the merged EM review.
    """
    if state.get("parallel"):
        return {"passed": True, "deferred_to_post_merge": True,
                "errors": [], "impact": {}}
    baseline = state.get("baseline") or tp.snapshot_ref(ws)
    changed = [f for f in _diff_files(ws, baseline or "HEAD")
               if not f.startswith(lens_router.LOOP_OWNED)]
    stems = [g.split("*", 1)[0] for g in (task.get("scope") or [])]
    mine = [f for f in changed
            if not stems or any(f.startswith(s) for s in stems if s)]
    planned = ((task.get("blast") or {}).get("modules")
               or depgraph.modules_for_scope(task.get("scope") or []))
    return depgraph.completion(
        ws, mine, planned_modules=planned,
        policy=task.get("impact_policy") or depgraph.impact_policy(task))


def _task_dod_errors(ws: str, state: dict, task: dict,
                     snapshot: str | None) -> list:
    contract = tp.build_contract(
        f"EXECUTE: {task['id']}", scope=task.get("scope"),
        test_command=task.get("tests"))
    return (_design_current_errors(ws, state)
            + tp.dod_check(contract, ws, snapshot))


def _evaluation_errors(ws: str, state: dict, task: dict) -> list:
    """Validate evaluator evidence instead of trusting `gate pass`."""
    path = os.path.join(ws, ".eval", "verdict.json")
    verdict, errors = _read_json(path)
    if errors:
        return errors
    errors.extend(_design_current_errors(ws, state))
    if verdict.get("task") != task.get("id"):
        errors.append("evaluation evidence is for task "
                      f"{verdict.get('task')!r}, expected {task.get('id')!r}")
    if verdict.get("verdict") != "pass":
        errors.append("evaluation verdict is not pass")

    expected_criteria = _criteria_for(ws, state, task)
    rows = verdict.get("criteria") or []
    if not isinstance(rows, list):
        errors.append("evaluation criteria must be a list")
        rows = []
    by_criterion = {str(r.get("criterion", "")).strip(): r
                    for r in rows if isinstance(r, dict)}
    for criterion in expected_criteria:
        row = by_criterion.get(criterion)
        if not row:
            errors.append(f"acceptance criterion has no evidence: {criterion}")
        elif row.get("status") != "met" or not str(row.get("evidence") or "").strip():
            errors.append(f"acceptance criterion is not proven met: {criterion}")

    routing = lens_router.route_git_diff(
        ws, base=state.get("baseline") or "HEAD",
        task_type=task.get("type"), breadth="routed")
    expected_lenses = {entry["id"] for entry in routing.get("lenses") or []}
    raw_lenses = verdict.get("lenses") or []
    if not isinstance(raw_lenses, list):
        errors.append("evaluation lenses must be a list")
        raw_lenses = []
    lens_rows = {str(r.get("lens", "")): r for r in raw_lenses
                 if isinstance(r, dict)}
    for lens_id in sorted(expected_lenses):
        row = lens_rows.get(lens_id)
        if not row:
            errors.append(f"routed lens has no verdict: {lens_id}")
        else:
            try:
                blocker_count = int(row.get("blockers") or 0)
            except (TypeError, ValueError):
                blocker_count = 1
            if row.get("verdict") != "pass" or blocker_count > 0:
                errors.append(f"routed lens did not pass cleanly: {lens_id}")
    if verdict.get("failures"):
        errors.append("evaluation contains unresolved failures")
    if state.get("graph_governance"):
        graph_dod = _task_graph_dod(ws, state, task)
        errors.extend("graph DoD: " + e for e in graph_dod.get("errors") or [])
        if not graph_dod.get("deferred_to_post_merge"):
            impact = graph_dod.get("impact") or {}
            direct = sorted({e.get("module")
                             for e in (impact.get("impacted") or {}).get(1, [])
                             if e.get("module")
                             and not str(e.get("module")).startswith("req:")})
            prod = depgraph.product_impact(ws,
                                           graph_dod.get("realized_modules") or [])
            own = task.get("req") or state.get("requirement_id")
            own = depgraph._req_node(own) if own else None
            affected = sorted(r for r in prod.get("affected_requirements") or []
                              if r != own)
            needs_graph_evidence = bool(
                direct or affected or graph_dod.get("contract_files")
                or impact.get("unknown") or impact.get("truncated"))
            graph_ev = verdict.get("graph") or {}
            if needs_graph_evidence and not isinstance(verdict.get("graph"), dict):
                errors.append("evaluation is missing graph impact evidence")
                graph_ev = {}
            dispositions = {str(x.get("node")): x for x in
                            (graph_ev.get("dispositions") or [])
                            if isinstance(x, dict)}
            allowed = {"tested", "contract-verified", "unaffected",
                       "follow-up", "requires-replan"}
            for node in direct:
                row = dispositions.get(node)
                if (not row or row.get("status") not in allowed
                        or not str(row.get("evidence") or "").strip()):
                    errors.append(f"graph impact has no evidenced disposition: {node}")
                elif row.get("status") == "requires-replan":
                    errors.append(f"graph impact requires replanning: {node}")
            checked = set(graph_ev.get("requirements_checked") or [])
            for rid in affected:
                if rid not in checked:
                    errors.append("affected requirement was not re-checked: " + rid)
            expected_contracts = set()
            for contract_row in task.get("contracts") or []:
                contract_id = (contract_row.get("id")
                               if isinstance(contract_row, dict)
                               else contract_row)
                if str(contract_id or "").strip():
                    expected_contracts.add(str(contract_id))
            checked_contracts = set(graph_ev.get("contracts_checked") or [])
            for contract in sorted(expected_contracts - checked_contracts):
                errors.append("declared contract was not verified: " + contract)
    return errors


def _engineering_review_errors(ws: str, state: dict | None = None) -> list:
    """Require full-catalog lens evidence before the EM gate can pass."""
    path = os.path.join(ws, ".em-review", "findings.json")
    findings, errors = _read_json(path)
    if errors:
        return errors
    report_path = os.path.join(ws, ".em-review", "report.md")
    try:
        with open(report_path, encoding="utf-8") as report_file:
            report_text = report_file.read()
        if not report_text.strip():
            errors.append("engineering narrative report is empty")
    except OSError:
        errors.append("engineering narrative report is missing: "
                      + report_path)
    meta = findings.get("meta") or {}
    if state:
        errors.extend(_design_review_errors(ws, state, meta))
    coverage = meta.get("lens_coverage") or {}
    if not isinstance(coverage, dict):
        errors.append("engineering lens coverage must be an object")
        coverage = {}
    catalog = lens_router.load_catalog()
    expected = {entry["id"] for entry in catalog.get("lenses") or []}
    missing = sorted(expected - set(coverage))
    invalid = sorted(k for k, v in coverage.items()
                     if k in expected and v not in ("deep", "sweep"))
    if missing:
        errors.append("engineering review omitted lenses: " + ", ".join(missing))
    if invalid:
        errors.append("engineering review has invalid lens tiers: "
                      + ", ".join(invalid))
    impact_ev = meta.get("impact")
    if not isinstance(impact_ev, dict):
        errors.append("engineering review is missing dependency impact evidence")
    elif (state or {}).get("graph_governance"):
        required = {"touched", "impacted", "total_impacted", "unknown",
                    "depth_limit", "truncated", "policy", "graph"}
        missing_impact = sorted(required - set(impact_ev))
        if missing_impact:
            errors.append("engineering dependency impact evidence is incomplete: "
                          + ", ".join(missing_impact))
        changed = [f for f in _diff_files(
            ws, (state or {}).get("baseline") or "HEAD")
            if not f.startswith(lens_router.LOOP_OWNED)]
        if changed:
            review_policy = _aggregate_impact_policy(
                (state or {}).get("tasks") or [])
            expected = depgraph.impact(ws, changed, policy=review_policy)
            if not impact_ev.get("touched"):
                errors.append("engineering dependency impact names no touched modules")
            elif not set(expected.get("touched") or []) <= \
                    set(impact_ev.get("touched") or []):
                errors.append("engineering dependency impact does not cover the diff")
            expected_fp = (expected.get("graph") or {}).get("content_fingerprint")
            actual_fp = (impact_ev.get("graph") or {}).get("content_fingerprint")
            if expected_fp and actual_fp != expected_fp:
                errors.append("engineering dependency impact uses a stale graph revision")
            if impact_ev.get("policy") != review_policy:
                errors.append("engineering dependency impact uses the wrong review policy")
    if not meta.get("tests"):
        errors.append("engineering review is missing test evidence")
    gate = meta.get("gate") or {}
    if gate.get("verdict") not in ("pass", "recommend-pass"):
        errors.append("engineering review does not recommend sign-off")
    rows = findings.get("findings") or []
    if not isinstance(rows, list):
        errors.append("engineering findings must be a list")
        rows = []
    for finding in rows:
        if (isinstance(finding, dict)
                and str(finding.get("severity", "")).lower() in ("critical", "high")
                and str(finding.get("status", "open")).lower()
                not in ("resolved", "accepted", "closed")):
            errors.append("engineering review has an unresolved "
                          f"{finding.get('severity')} finding: "
                          f"{finding.get('title', 'untitled')}")
    return errors


def submit(ws: str, outcome: str, note: str = "",
           task_id: str | None = None) -> dict:
    """Worker submission — evidence request, never a state transition.

    The engine, not the worker, computes the changed paths and fingerprint.
    The orchestrator subsequently calls ``gate``; if anything changed between
    submission and validation, the gate rejects the stale evidence.  Repeating
    the same submission is idempotent, which makes interrupted/resumed drivers
    safe.
    """
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state.get("step")
    if step not in ("execute", "fix", "evaluate", "em"):
        return {"error": f"step '{step}' is not a worker submission step"}
    if outcome not in ("pass", "fail"):
        return {"error": "submission outcome must be pass or fail"}

    task = _current_task(state)
    act_ws = ws
    parallel_execute = step == "execute" and state.get("parallel")
    if parallel_execute:
        task = next((x for x in state.get("tasks") or []
                     if x.get("id") == task_id), None)
        if task is None:
            return {"error": "parallel submit needs --task <id> of a wave member"}
        act_ws = task.get("workspace") or ws
    elif step in ("evaluate", "fix") and state.get("parallel"):
        tws = (task or {}).get("workspace")
        act_ws = tws if tws and os.path.isdir(tws) else ws

    snapshot = tp.snapshot_ref(act_ws)
    evidence_paths = ({"evaluate": [".eval/verdict.json"],
                       "em": [".em-review/findings.json",
                              ".em-review/report.md"]}.get(step, []))
    graph_fingerprint = None
    if state.get("graph_governance") and \
            (step == "em" or step == "evaluate" and not state.get("parallel")):
        graph_fingerprint = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
    submission = {
        "step": step,
        "task": (task or {}).get("id"),
        "outcome": outcome,
        "note": note,
        "workspace": act_ws,
        "snapshot": snapshot,
        "fingerprint": tp.workspace_fingerprint(
            act_ws, snapshot, extra_paths=evidence_paths),
        "changed_files": (tp.changed_files(act_ws, snapshot)
                          if snapshot else []),
        "evidence_paths": evidence_paths,
        "graph_fingerprint": graph_fingerprint,
        "submitted_at": int(time.time()),
    }
    with mutate(ws) as locked:
        if locked is None:
            return {"error": "no active loop"}
        if parallel_execute:
            target = next((x for x in locked.get("tasks") or []
                           if x.get("id") == task_id), None)
            if target is None:
                return {"error": f"no task {task_id}"}
            existing = target.get("_submission")
            if existing and all(existing.get(k) == submission.get(k)
                                for k in ("step", "task", "outcome",
                                          "fingerprint")):
                submission = existing
            else:
                target["_submission"] = submission
        else:
            existing = locked.get("_submission")
            if existing and all(existing.get(k) == submission.get(k)
                                for k in ("step", "task", "outcome",
                                          "fingerprint")):
                submission = existing
            else:
                locked["_submission"] = submission
    tp.trace(ws, "loop_submit", step=step, task=submission.get("task"),
             outcome=outcome, fingerprint=submission["fingerprint"][:12])
    return {"submitted": True, "transitioned": False,
            "submission": submission,
            "next": "orchestrator: run loop gate with the submitted outcome"}


def _submission_staleness(ws: str, submission: dict) -> str | None:
    """Recompute the engine-owned attestations for a pending submission."""
    sub_ws = submission.get("workspace") or ws
    current_fp = tp.workspace_fingerprint(
        sub_ws, submission.get("snapshot"),
        extra_paths=submission.get("evidence_paths") or [])
    if current_fp != submission.get("fingerprint"):
        return "workspace or evidence changed after worker submission"
    graph_fp = submission.get("graph_fingerprint")
    if graph_fp:
        current_graph_fp = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
        if current_graph_fp != graph_fp:
            return "dependency graph changed after worker submission"
    return None


def gate(ws: str, outcome: str, note: str = "", task_id: str | None = None) -> dict:
    """Record the current step's outcome, transition, and clear its contract."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state["step"]

    if state.get("submission_required") and step in \
            ("execute", "fix", "evaluate", "em"):
        task_for_submission = (_current_task(state) if step != "execute"
                               or not state.get("parallel") else
                               next((x for x in state.get("tasks") or []
                                     if x.get("id") == task_id), None))
        submission = ((task_for_submission or {}).get("_submission")
                      if step == "execute" and state.get("parallel") else
                      state.get("_submission"))
        if not submission:
            return {"error": "worker evidence was not submitted — the worker "
                             "must run `loop submit pass|fail`; only the "
                             "orchestrator may evaluate `loop gate`",
                    "step": step}
        if submission.get("step") != step or submission.get("outcome") != outcome:
            return {"error": "gate request does not match the worker submission",
                    "step": step, "submission": submission}
        stale = _submission_staleness(ws, submission)
        if stale:
            return {"error": stale + " — discard stale evidence and submit again",
                    "step": step}

    # Parallel EXECUTE: a wave worker reports its own task's build outcome.
    # Concurrent workers gate against the SAME loop.json — serialize the whole
    # read-modify-write under an exclusive lock so a second worker's save
    # can't clobber the first's status update (which would revert a gated task
    # to running and stall the wave).
    if step == "execute" and state.get("parallel"):
        wt_precheck = next((x for x in state.get("tasks") or []
                            if x["id"] == task_id), None)
        if wt_precheck is None:
            return {"error": "parallel gate needs --task <id> of a wave "
                             "member"}
        # Fail closed: an uncommitted worktree means the branch carries
        # NOTHING — the merge would be empty and worktree removal would
        # destroy the work. Commit first, then gate.
        wt = wt_precheck.get("workspace")
        if outcome == "pass":
            dod_errors = _task_dod_errors(
                wt or ws, state, wt_precheck, tp.snapshot_ref(wt or ws))
            if dod_errors:
                tp.trace(ws, "loop_gate_blocked", step=step, task=task_id,
                         reason="dod", errors=dod_errors)
                return {"error": "Definition of Done failed — task remains "
                                 "running", "dod": {"passed": False,
                                 "errors": dod_errors}}
        if outcome == "pass" and wt and os.path.isdir(wt) and tp.is_dirty(wt):
            return {"error": f"task {task_id}: uncommitted work in {wt} — "
                             "the tp/<task> branch carries nothing yet. "
                             "`git add -A && git commit` in the worktree, "
                             "then gate again."}
        if state.get("submission_required"):
            stale = _submission_staleness(ws, submission)
            if stale:
                return {"error": stale + " during gate validation — submit "
                                 "the final state again", "step": step}
        with mutate(ws) as locked:
            t = next((x for x in (locked.get("tasks") or [])
                      if x["id"] == task_id), None)
            if t is None:
                return {"error": "parallel gate needs --task <id> of a wave "
                                 "member"}
            tp.clear(t.get("workspace") or ws)
            t["status"] = "built"
            t.pop("_submission", None)
            if outcome != "pass":
                t["_build_failed"] = True
            tp.trace(ws, "loop_gate", step=step, task=task_id, outcome=outcome,
                     note=note)
            running = [x["id"] for x in locked["tasks"]
                       if x.get("status") == "running"]
        return {"step": "execute", "task": task_id, "built": True,
                "still_running": running, "status": status(ws)}

    # Validate the proposed HOW while its read-only contract is active. The
    # designer cannot self-certify or mutate the as-built graph; a complete
    # contract advances only to the human approval gate.
    if step == "design":
        if outcome != "pass":
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "design rejected — staying at design")
            return {"error": "design gate: outcome was not 'pass' — revise "
                             "design/design.md and design/contract.json, then "
                             "gate again", "step": "design",
                    "status": status(ws)}
        design_errors = _design_dod_errors(ws, state)
        if design_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="design_dod", errors=design_errors)
            return {"error": "Design Definition of Done failed — revise the "
                             "Design Contract before approval",
                    "step": "design",
                    "dod": {"passed": False, "errors": design_errors}}

    # Validate the implementation-ready plan while its read-only contract is
    # still active. A rejected plan remains governed for the planner's retry.
    if step == "plan":
        _load_tasks(ws, state)
        if outcome != "pass":
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "plan rejected — staying at plan")
            return {"error": "plan gate: outcome was not 'pass' — the plan "
                             "was rejected. Revise plan/tasks.json (+ "
                             "plan/plan.md) and gate again; the loop stays at "
                             "the plan step.",
                    "step": "plan", "status": status(ws)}
        if not state.get("tasks"):
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note="phantom plan: plan/tasks.json missing or empty")
            return {"error": "plan gate: plan/tasks.json is missing or has "
                             "no tasks — the plan exists only as words. "
                             "Write plan/tasks.json (+ plan/plan.md for the "
                             "human), then gate again."}
        dor_errors = _plan_dor_errors(ws, state)
        if dor_errors:
            tp.trace(ws, "loop_gate_blocked", step=step, reason="dor",
                     errors=dor_errors)
            return {"error": "Definition of Ready failed — revise "
                             "plan/tasks.json before approval or execution",
                    "step": "plan",
                    "dor": {"ready": False, "blockers": dor_errors}}

    task = _current_task(state)
    act_ws = ws
    if step in ("evaluate", "fix") and state.get("parallel"):
        tws = (task or {}).get("workspace")
        act_ws = tws if tws and os.path.isdir(tws) else ws

    # A reported PASS is a request to evaluate the gate. Evidence, not the
    # agent's assertion, determines whether the state machine advances.
    if outcome == "pass" and step in ("execute", "fix"):
        dod_errors = _task_dod_errors(
            act_ws, state, task, tp.snapshot_ref(act_ws))
        if dod_errors:
            tp.trace(ws, "loop_gate_blocked", step=step, reason="dod",
                     errors=dod_errors)
            return {"error": "Definition of Done failed — step did not "
                             "advance", "step": step,
                    "dod": {"passed": False, "errors": dod_errors}}
    if outcome == "pass" and step == "evaluate":
        evidence_errors = _evaluation_errors(act_ws, state, task)
        if evidence_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="evaluation_evidence", errors=evidence_errors)
            return {"error": "evaluation evidence failed — step did not "
                             "advance", "step": step,
                    "dod": {"passed": False, "errors": evidence_errors}}
    if outcome == "pass" and step == "em":
        review_errors = _engineering_review_errors(ws, state)
        if review_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="engineering_review", errors=review_errors)
            return {"error": "engineering review is incomplete — sign-off "
                             "is not available", "step": step,
                    "dod": {"passed": False, "errors": review_errors}}

    # Tests and evidence validation may take long enough for another process
    # to modify the workspace or graph. Re-attest immediately before the
    # transition; a worker's pre-validation fingerprint is not enough.
    if state.get("submission_required") and step in \
            ("execute", "fix", "evaluate", "em"):
        stale = _submission_staleness(ws, submission)
        if stale:
            return {"error": stale + " during gate validation — submit the "
                             "final state again", "step": step}

    tp.clear(act_ws)
    tp.trace(ws, "loop_gate", step=step, outcome=outcome, note=note)
    state.pop("_submission", None)

    if step == "pm":
        state["step"] = ("design" if state.get("design_required") else "plan")
    elif step == "design":
        state["step"] = "design_approval"
    elif step == "plan":
        # Product↔engineering graph, PLANNED side: link each task's
        # requirement to the modules its scope intends to touch, then
        # annotate the task with its blast radius (engineering) and any
        # OTHER requirements whose surface it overlaps (product). The
        # human approves the plan seeing both; the executor's contract
        # briefing carries them; evaluation compares against them later.
        _annotate_plan_graph(ws, state)
        state["step"] = ("plan_approval" if "plan" in state["checkpoints"]
                         else "execute")
        state["current_task"] = 0
        if state["step"] == "execute":
            state["baseline"] = tp.git_head(ws)
    elif step == "execute":
        # a build always goes to evaluate; a FAILED build is flagged so
        # evaluate FAILs and routes to fix/escalate — one place owns the fail
        # policy (so the step transition itself is unconditional).
        state["step"] = "evaluate"
        if outcome != "pass":
            state["_build_failed"] = True
    elif step == "evaluate":
        t = _current_task(state)
        build_failed = state.pop("_build_failed", False) or \
            t.pop("_build_failed", False)
        if outcome == "pass" and not build_failed:
            t["status"] = "passed"
            # After the LAST task: A/B loops pause at the human SELECTION
            # gate (variants never merge — one gets picked) — but only
            # ONCE; a post-selection fix cycle goes back to the review.
            after_last = ("selection" if state.get("ab")
                          and not state.get("selection") else "em")
            if state.get("parallel"):
                # merge is the driver's job (instruction), state just moves on
                if all(x.get("status") in SETTLED
                       for x in state["tasks"]):
                    state["step"] = after_last
                else:
                    state["step"] = "execute"   # next wave / next built task
            else:
                # serial: advance to the next UNSETTLED task, skipping any the
                # skip-cascade already closed (else a dependency-failed task
                # gets silently built and shipped).
                nxt = _next_unsettled_index(state, state["current_task"])
                if nxt is not None:
                    state["current_task"] = nxt
                    state["step"] = "execute"
                else:
                    state["step"] = after_last
        else:
            t["fix_cycles"] = t.get("fix_cycles", 0) + 1
            if t["fix_cycles"] <= state["max_fix_cycles"]:
                state["step"] = "fix"
            else:
                t["status"] = "failed"
                state["step"] = "escalated"
    elif step == "fix":
        state["step"] = "evaluate"
    elif step == "em":
        # The graph was true-d up before the EM brief, so its fingerprint is
        # part of the evidence being gated rather than a post-review mutation.
        state["step"] = "signoff"
    save(ws, state)
    return {"step": state["step"], "status": status(ws)}


def _signoff_dod(ws: str, state: dict) -> dict:
    """Mechanical Definition-of-Done, run at the sign-off gate: the whole diff
    since the loop's baseline must fall within the UNION of the tasks' declared
    scopes, and the committed knowledge store must be lint-clean. Surfaced to the
    human next to the EM read-out — the sign-off decision is still theirs. Returns
    {passed, errors, scope, baseline}."""
    scopes: list = []
    for t in (state.get("tasks") or []):
        scopes.extend(t.get("scope") or [])
    baseline = state.get("baseline")
    contract = {"coding": {"scope_paths": scopes,
                           "dod": {"require_clean_scope_diff": bool(scopes)}}}
    errors = list(tp.dod_check(contract, ws, baseline)) if scopes else []
    if state.get("graph_governance"):
        try:
            depgraph.scan(ws)
        except Exception as exc:
            errors.append(f"graph_dod: final merged-tree scan failed: {exc}")
    for task in state.get("tasks") or []:
        test_command = task.get("tests")
        if not test_command:
            errors.append(f"task {task.get('id', '?')}: test command missing")
            continue
        test_contract = tp.build_contract(
            f"SIGNOFF TEST: {task.get('id', '?')}",
            scope=task.get("scope"), test_command=test_command)
        # Aggregate scope was checked above; run each task's behavioral test
        # without incorrectly treating another task's files as scope creep.
        test_contract["coding"]["dod"]["require_clean_scope_diff"] = False
        errors.extend(f"task {task.get('id', '?')}: {e}"
                      for e in tp.dod_check(test_contract, ws, baseline))
    errors.extend(_engineering_review_errors(ws, state))
    for problem in kb.lint(ws):
        errors.append("kb_lint: " + (problem.get("file", "?")) + " — "
                      + problem.get("problem", ""))
    return {"passed": not errors, "errors": errors,
            "scope": scopes, "baseline": baseline}


def _annotate_plan_graph(ws: str, state: dict) -> None:
    """Plan-gate graph work: planned req→module links + per-task blast."""
    # Batch by requirement first: link_requirement(replace=True) refreshes a
    # requirement's whole edge set of one kind, so calling it once per task
    # would let a second task sharing the requirement WIPE the first's edges.
    planned = {}
    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        scope = t.get("scope") or []
        if rid and scope:
            planned.setdefault(rid, []).extend(scope)
    for rid, scopes in planned.items():
        depgraph.link_requirement(ws, rid, scopes, kind="planned")

    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        scope = t.get("scope") or []
        if not scope:
            continue
        mods = depgraph.modules_for_scope(scope)
        imp = depgraph.impact(
            ws, mods, policy=t.get("impact_policy")
            or depgraph.impact_policy(t)) if \
            depgraph.load(ws)["modules"] else None
        prod = depgraph.product_impact(ws, mods)
        own = depgraph._req_node(rid) if rid else None
        shared = [r for r in prod["affected_requirements"] if r != own]
        t["blast"] = {
            "modules": mods,
            "impacted": imp["total_impacted"] if imp else 0,
            "unknown": imp["unknown"] if imp else mods,
            "truncated": bool(imp and imp.get("truncated")),
            "policy": t.get("impact_policy") or depgraph.impact_policy(t),
            "shared_with": shared,
            "dependent_requirements": prod["dependent_requirements"],
        }
        if shared:
            tp.trace(ws, "graph_shared_surface", task=t["id"],
                     requirement=rid, shared_with=shared)


def _true_up_graph(ws: str, state: dict) -> None:
    """Pre-EM graph work: realize requirements, then scan the final tree."""
    changed = [f for f in _diff_files(ws, state.get("baseline") or "HEAD")
               if not f.startswith(lens_router.LOOP_OWNED)]
    if not changed:
        depgraph.scan(ws)
        tp.trace(ws, "graph_true_up", files=0)
        return
    # Batch by requirement (see _annotate_plan_graph) so multiple tasks
    # sharing one requirement accumulate their realized surface instead of
    # the last task's replace=True wiping the earlier ones'.
    realized = {}
    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        if not rid:
            continue
        stems = [g.split("*", 1)[0] for g in (t.get("scope") or [])]
        mine = [f for f in changed
                if any(f.startswith(s) for s in stems if s)]
        realized.setdefault(rid, []).extend(mine)
    for rid, files in realized.items():
        depgraph.link_requirement(ws, rid, files or changed, kind="realizes")
    # Scan after recording the realized edges so the graph fingerprint covers
    # both the final code tree and requirement-to-implementation truth.
    depgraph.scan(ws)
    tp.trace(ws, "graph_true_up", files=len(changed))


def _refinement_report(ws: str, state: dict) -> list:
    """Score each task's anchored requirement at the plan gate — the
    forecast shows BEFORE a build starts (requirements-at-the-core)."""
    out = []
    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        if not rid:
            continue
        rec = reqs.get_requirement(ws, rid)
        if rec is None:
            out.append({"task": t["id"], "requirement": rid,
                        "error": "requirement not found in the KB"})
            continue
        g = reqs.gate(rec, high_cost=bool(t.get("high_cost")),
                      changed_files=t.get("scope"), task_type=t.get("type"))
        mode = reqs.suggest_mode(g["score"], len(t.get("scope") or []))
        out.append({"task": t["id"], "requirement": rid, "gate": g,
                    "mode_suggestion": mode})
        tp.trace(ws, "refinement_gate", task=t["id"], requirement=rid,
                 score=g["score"], blocking=g["blocking"],
                 mode=mode["mode"])
    return out


def approve(ws: str, force: bool = False, by: str = None) -> dict:
    """Pass a human checkpoint (plan-approval or EM sign-off).

    `by` (v1.4.0, built for Claude Tag threads): WHO approved and where —
    e.g. "Dana R. — 'approved' in #platform-eng thread". Recorded into the
    trace event and the KB decision, so a gate pass is attributable to a
    human even in environments with no hook enforcement. In an unattended
    or Tag session, an approve WITHOUT `by` is exactly the self-approval
    the adherence experiment flags — drivers must pass the human's words."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state["step"]
    refinement = None
    if step == "design_approval":
        if not str(by or "").strip():
            return {"error": "design approval needs --by with the human's "
                             "identity/context; the designer cannot self-approve"}
        design_errors = _design_dod_errors(ws, state)
        if design_errors:
            tp.trace(ws, "loop_approve_blocked", gate="design",
                     reason="dod", errors=design_errors, by=by)
            return {"error": "Design Definition of Done failed — approval "
                             "cannot be recorded", "step": step,
                    "dod": {"passed": False, "errors": design_errors}}
        contract, _ = _design_contract(ws)
        state["design_fingerprint"] = _design_evidence_fingerprint(ws, contract)
        state["design_approved_by"] = by
        state["step"] = "done" if state.get("design_only") else "plan"
        tp.trace(ws, "loop_approve", gate="design", by=by,
                 fingerprint=state["design_fingerprint"][:12])
        modules = ((contract or {}).get("graph") or {}).get(
            "proposed_modules") or []
        kb.record_decision(
            ws, f"Design approved: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}\nApproved by: {by}\n"
                    f"Fingerprint: {state['design_fingerprint']}",
            decision=(contract or {}).get("decision", "Design approved."),
            tags=["design-approval", "solution-design"],
            context_files=list(modules),
            links={"loop": "design", "modules": list(modules)})
    elif step == "plan_approval":
        current_errors = _design_current_errors(ws, state)
        if current_errors:
            return {"error": "approved design is stale — plan approval is "
                             "blocked", "step": step,
                    "dor": {"ready": False, "blockers": current_errors}}
        # Refinement gate (advisory; hard only for high-cost tasks).
        refinement = _refinement_report(ws, state)
        blocked = [r for r in refinement if r.get("gate", {}).get("blocking")]
        if blocked and not force:
            return {"error": "refinement gate BLOCKED — a high-cost task's "
                             "requirement is under the threshold. Refine it "
                             "(close the gaps) or `loop approve --force`.",
                    "refinement": refinement}
        # Baseline for later diff-routing at EVALUATE/EM.
        state["baseline"] = tp.git_head(ws)
        state["step"] = "execute"
        state["current_task"] = 0
        tp.trace(ws, "loop_approve", gate="plan", by=by)
        # High-signal decision → the knowledge base.
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        kb.record_decision(
            ws, f"Plan approved: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}"
                    + (f"\nApproved by: {by}" if by else ""),
            decision=f"Approved a {len(state.get('tasks') or [])}-task plan.",
            tags=["plan-approval"], context_files=scope,
            links={"loop": "plan"})
    elif step == "signoff":
        dod = _signoff_dod(ws, state)
        if not dod["passed"]:
            tp.trace(ws, "loop_approve_blocked", gate="em_signoff",
                     reason="dod", errors=dod["errors"], by=by)
            return {"error": "Definition of Done failed — sign-off cannot "
                             "complete until the evidence is repaired",
                    "step": "signoff", "dod": dod}
        state["step"] = "done"
        tp.trace(ws, "loop_approve", gate="em_signoff", final="done", by=by)
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        kb.record_decision(
            ws, f"Accepted: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}"
                    + (f"\nApproved by: {by}" if by else ""),
            decision="EM review passed and the human signed off — shipped.",
            tags=["accepted", "em-signoff"], context_files=scope,
            links={"loop": "signoff"})
    elif step == "selection":
        return {"error": "the selection gate needs a CHOICE, not a plain "
                         "approve — `loop select <variant|task-id|hybrid>`"}
    else:
        return {"error": f"nothing to approve at step '{step}'"}
    save(ws, state)
    out = {"step": state["step"], "status": status(ws)}
    if refinement:
        out["refinement"] = refinement
    return out


def select(ws: str, choice: str, note: str = "") -> dict:
    """The A/B selection gate — the human's pick of what ships. Accepts a
    variant letter, a task id, or 'hybrid'. This gate REPLACES the merge
    step variants never have: a winner goes to the engineering review; a
    hybrid goes back to plan for the graft (both variants kept as
    reference). Recorded to the KB — the WHY outlives the losing branch."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    if state["step"] != "selection":
        return {"error": f"selection only at the selection gate "
                         f"(current: {state['step']})"}
    tasks = state.get("tasks") or []
    variants = [t for t in tasks if t.get("variant")] or tasks
    if choice.strip().lower() == "hybrid":
        state["selection"] = {"choice": "hybrid", "note": note}
        for t in variants:
            t["status"] = "reference"
        state["step"] = "plan"
        instruction = (
            "Hybrid selected: write a NEW plan/tasks.json with the graft "
            "task(s) — name the base variant's branch and what to graft "
            "from the other — then `loop gate pass`. Plan approval and the "
            "build/evaluate cycle apply as usual; both variant branches "
            "stay as reference until the retro.")
    elif choice.strip().lower() in ("neither", "none", "reject", "reject-both"):
        # Neither variant ships — the A/B round is abandoned. Both variants
        # become not_selected (kept as reference branches) and the loop goes
        # back to PLAN for a fresh approach, so the human who picks "neither"
        # has a real transition instead of parking at the selection gate.
        state["selection"] = {"choice": "neither", "note": note}
        for t in variants:
            t["status"] = "not_selected"
        state["step"] = "plan"
        instruction = (
            "Neither variant selected: both are set aside (branches kept as "
            "reference). Write a NEW plan/tasks.json taking a different "
            "approach — what did both variants get wrong? — then "
            "`loop gate pass`. Plan approval and the build/evaluate cycle "
            "apply as usual.")
    else:
        c = choice.strip()
        win = next((t for t in variants
                    if t["id"] == c
                    or str(t.get("variant", "")).lower() == c.lower()), None)
        if win is None:
            return {"error": f"no variant matches '{choice}' — use a task "
                             "id, a variant letter, or 'hybrid'",
                    "variants": [{"id": t["id"],
                                  "variant": t.get("variant")}
                                 for t in variants]}
        state["selection"] = {"choice": win["id"],
                              "variant": win.get("variant"), "note": note}
        win["selected"] = True
        win["status"] = "passed"
        for t in variants:
            if t is not win:
                t["status"] = "not_selected"
        state["step"] = "em"
        instruction = (
            f"Winner: {win['id']}. Merge its branch "
            f"(`git merge tp/{win['id']}`), keep the losing branch as "
            "reference until the retro, clear the variant worktree "
            "contracts, then run the engineering review of the merged "
            "result (full catalog).")
    tp.trace(ws, "loop_select", choice=state["selection"]["choice"],
             note=note)
    kb.record_decision(
        ws, f"A/B selection: {state['selection']['choice']} — "
            f"{state['goal'][:48]}",
        context=(f"Goal: {state['goal']}; variants: "
                 + ", ".join(t["id"] for t in variants)),
        decision=(note or f"Human selected {state['selection']['choice']} "
                          "at the selection gate."),
        tags=["ab-selection"],
        context_files=sorted({g for t in variants
                              for g in t.get("scope", [])}),
        links={"loop": "selection"})
    save(ws, state)
    return {"step": state["step"], "selection": state["selection"],
            "instruction": instruction, "status": status(ws)}


def _cascade_skip(state: dict, root_id: str) -> list:
    """Skip every task that (transitively) depends on root_id — they can
    never reach passed, so leaving them pending would deadlock the wave.
    Returns the ids that were cascaded."""
    tasks = state.get("tasks") or []
    dead = {root_id}
    cascaded = []
    changed = True
    while changed:
        changed = False
        for t in tasks:
            if t.get("status") in SETTLED:
                continue
            if set(t.get("deps") or []) & dead:
                t["status"] = "skipped"
                dead.add(t["id"])
                cascaded.append(t["id"])
                changed = True
    return cascaded


def resolve(ws: str, decision: str) -> dict:
    """Human decision when a task escalated (fix cycles exhausted)."""
    state = load(ws)
    if state is None or state["step"] != "escalated":
        return {"error": "nothing escalated to resolve"}
    t = _current_task(state)
    if decision == "retry":
        t["fix_cycles"] = 0
        t["status"] = "running"
        state["step"] = "fix"
    elif decision == "skip":
        t["status"] = "skipped"
        # Cascade: a task that depended (transitively) on the skipped one
        # can never satisfy deps⊆passed — skip it too, so it doesn't hold
        # the wave forever (the deadlock). Record which were cascaded.
        cascaded = _cascade_skip(state, t["id"])
        if cascaded:
            tp.trace(ws, "loop_skip_cascade", root=t["id"], skipped=cascaded)
        if state.get("parallel"):
            # settled-aware: advance only when every task is settled
            if all(x.get("status") in SETTLED for x in state["tasks"]):
                state["step"] = ("selection" if state.get("ab")
                                 and not state.get("selection") else "em")
            else:
                state["step"] = "execute"
        else:
            # serial: skip past any task the cascade just settled, so the
            # next execute is a task that still has work owed.
            nxt = _next_unsettled_index(state, state["current_task"])
            if nxt is not None:
                state["current_task"] = nxt
                state["step"] = "execute"
            else:
                state["step"] = "em"
    elif decision == "defer":
        # Human parks the task on an external gate: it settles AND satisfies
        # its dependents (the work will exist, just not via this loop) — the
        # clean form of what previously required hand-editing loop.json.
        t["status"] = "external"
        if state.get("parallel"):
            if all(x.get("status") in SETTLED for x in state["tasks"]):
                state["step"] = ("selection" if state.get("ab")
                                 and not state.get("selection") else "em")
            else:
                state["step"] = "execute"
        else:
            nxt = _next_unsettled_index(state, state["current_task"])
            if nxt is not None:
                state["current_task"] = nxt
                state["step"] = "execute"
            else:
                state["step"] = "em"
    elif decision == "abort":
        state["step"] = "failed"
    else:
        return {"error": "decision must be retry|skip|defer|abort"}
    tp.trace(ws, "loop_resolve", decision=decision, task=t.get("id"))
    save(ws, state)
    return {"step": state["step"], "status": status(ws)}


def retro(ws: str) -> dict:
    """Post-track learning: mine the trace + state for what the NEXT track
    should know — forecast accuracy (refinement score vs actual fix cycles),
    hook denials (scope friction), lens routing stats — and record the
    lessons to the knowledge base so they're retrieved, not re-learned."""
    state = load(ws) or {}
    trace_path = os.path.join(tp.tp_dir(ws), "trace.jsonl")
    events = []
    if os.path.exists(trace_path):
        with open(trace_path) as f:
            for ln in f:
                if not ln.strip():
                    continue
                try:
                    events.append(json.loads(ln))
                except ValueError:
                    continue   # a truncated/partial worker line — skip, don't
                               # crash the whole retro on one bad record

    denies = [e for e in events if e["event"] == "hook_deny"]
    gates = [e for e in events if e["event"] == "refinement_gate"]
    waves = [e for e in events if e["event"] == "loop_wave"]
    tasks = state.get("tasks") or []

    # forecast accuracy: refinement predicted ~gaps/2 fix cycles per task
    accuracy = []
    for g in gates:
        t = next((x for x in tasks if x["id"] == g.get("task")), None)
        if t is None:
            continue
        actual = t.get("fix_cycles", 0)
        accuracy.append({
            "task": g["task"], "refinement_score": g.get("score"),
            "actual_fix_cycles": actual,
            "forecast_held": (actual == 0) == (g.get("score", 1) >= 0.6),
        })

    lessons = []
    if denies:
        lessons.append(
            f"{len(denies)} hook denial(s) — scopes were tighter than the "
            "work wanted; check whether task scopes were too narrow or the "
            "work drifted: "
            + "; ".join(sorted({d.get('reason', '')[:60] for d in denies}))[:300])
    weak = [a for a in accuracy if not a["forecast_held"]]
    if weak:
        lessons.append(
            "refinement forecast missed on: "
            + ", ".join(a["task"] for a in weak)
            + " — revisit the NFR axes routed for those scopes.")
    hi_fix = [t["id"] for t in tasks if t.get("fix_cycles", 0) >= 2]
    if hi_fix:
        lessons.append("high fix-cycle tasks (requirements were the cheap "
                       "place to catch this): " + ", ".join(hi_fix))
    if not lessons:
        lessons.append("clean run — no scope friction, forecasts held.")

    report = {
        "goal": state.get("goal"),
        "tasks": [{"id": t["id"], "status": t.get("status"),
                   "fix_cycles": t.get("fix_cycles", 0)} for t in tasks],
        "hook_denials": len(denies),
        "parallel_waves": len(waves),
        "forecast_accuracy": accuracy,
        "lessons": lessons,
    }
    scope = sorted({g for t in tasks for g in t.get("scope", [])})
    kb.record_decision(
        ws, f"Retrospective: {state.get('goal', 'track')[:56]}",
        context=f"{len(tasks)} task(s), {len(denies)} hook denial(s), "
                f"{len(waves)} wave(s)",
        decision=" | ".join(lessons)[:400],
        tags=["retrospective"], context_files=scope,
        links={"loop": "retro"})
    tp.trace(ws, "loop_retro", lessons=len(lessons),
             denials=len(denies))
    # human-readable summary for the shared artifacts snapshot (v2.0.0)
    with contextlib.suppress(Exception):
        lines = [f"# Retro — {state.get('goal', 'track')}", ""]
        for k, v in report.items():
            if isinstance(v, (str, int, float)):
                lines.append(f"- **{k}**: {v}")
        for l in (report.get("lessons") or []):
            lines.append(f"- lesson: {l if isinstance(l, str) else json.dumps(l)}")
        with open(os.path.join(tp.tp_dir(ws), "retro.md"), "w") as f:
            f.write("\n".join(lines) + "\n")
    return report


def _load_tasks(ws: str, state: dict) -> None:
    path = os.path.join(ws, "plan", "tasks.json")
    if not os.path.exists(path):
        state["tasks"] = []
        return
    with open(path) as f:
        data = json.load(f)
    tasks = data.get("tasks", data) if isinstance(data, dict) else data
    for t in tasks:
        t.setdefault("status", "pending")
        t.setdefault("fix_cycles", 0)
    state["tasks"] = tasks
    # A/B mode: the plan says so, or tasks carry variant markers. Variants
    # are scope-identical ALTERNATIVES — they never merge; the merge step
    # is replaced by a human SELECTION gate after all variants evaluate.
    ab = bool(
        (isinstance(data, dict) and data.get("mode") == "ab-selection")
        or any(t.get("variant") for t in tasks))
    state["ab"] = ab
    # A fresh set of variant tasks begins a NEW selection round — drop any
    # stale selection flag so a hybrid re-entry (graft plan that is itself
    # A/B) pauses at the selection gate again instead of skipping it. The
    # prior choice is already recorded in the KB; the flag is round-scoped.
    if ab:
        state.pop("selection", None)
    # A/B without --parallel would build both variants over ONE workspace,
    # each clobbering the other — the selection gate would then choose
    # between code that no longer coexists. Force parallel so variants land
    # in isolated worktrees.
    if ab and not state.get("parallel"):
        state["parallel"] = True
        tp.trace(ws, "ab_forced_parallel",
                 note="A/B variants require isolated worktrees")


def status(ws: str) -> dict:
    state = load(ws)
    if state is None:
        return {"loop": "none"}
    tasks = state.get("tasks") or []
    out = {
        "step": state["step"],
        "goal": state["goal"],
        "tasks": [{"id": t["id"], "status": t.get("status"),
                   "fix_cycles": t.get("fix_cycles", 0),
                   **({"variant": t["variant"]} if t.get("variant") else {})}
                  for t in tasks],
        "current_task": state.get("current_task"),
        "max_fix_cycles": state["max_fix_cycles"],
        "checkpoints": state["checkpoints"],
    }
    if state.get("ab"):
        out["ab"] = True
    if state.get("design_required"):
        out["design"] = {
            "only": bool(state.get("design_only")),
            "approved": bool(state.get("design_fingerprint")),
            "fingerprint": state.get("design_fingerprint")
        }
    if state.get("selection"):
        out["selection"] = state["selection"]
    return out


def user_summary(ws: str) -> dict:
    """Human control-plane read model over the existing durable artifacts.

    It intentionally does not replace loop.json, findings, graph, or trace.
    Skills use this compact view so users see progress and decisions while the
    full harness remains available to agents.
    """
    state = load(ws)
    if state is None:
        return {"state": "not_started", "action_required": False,
                "headline": "No active taskplane run.",
                "next": "Tell taskplane what to build or review."}
    tasks = state.get("tasks") or []
    settled = sum(1 for t in tasks if t.get("status") in SETTLED)
    step = state.get("step")
    decisions = {
        "design_approval": "Review and approve the proposed Design Contract.",
        "plan_approval": "Review and approve the implementation plan.",
        "selection": "Choose the A/B variant or request a hybrid.",
        "signoff": "Review the engineering evidence and sign off.",
        "escalated": "Choose retry, skip/defer, or abort.",
    }
    action = decisions.get(step)
    current = _current_task(state)
    graph = depgraph.summary(ws)
    host = ("codex" if os.environ.get("CODEX_HOME")
            or os.environ.get("CODEX_THREAD_ID") else
            "claude-tag" if tp.store_env() == "repo" else "claude")
    assurance = ("state-and-evidence enforced; tool interception is cooperative"
                 if host == "claude-tag" else
                 "state, evidence, and tool boundaries mechanically enforced")
    if step == "done":
        headline = f"Complete — {settled}/{len(tasks)} task(s) settled."
    elif action:
        headline = f"Decision required — {action}"
    else:
        label = current.get("id") if current else step
        headline = (f"In progress — {settled}/{len(tasks)} task(s) settled; "
                    f"current: {label} ({step}).")
    return {
        "state": step,
        "goal": state.get("goal"),
        "progress": {"settled": settled, "total": len(tasks)},
        "current_task": current and {"id": current.get("id"),
                                     "status": current.get("status")},
        "action_required": bool(action),
        "decision": action,
        "headline": headline,
        "host": host,
        "assurance": assurance,
        "graph": graph,
        "submission_pending_validation": bool(
            state.get("_submission") or any(t.get("_submission") for t in tasks)),
    }


# --- Dashboard v2 (R-0001): rendering is part of the flow, not a separate
# call. Every successful gate()/next_action() refreshes the fragment on disk
# and points at it in the payload — the driver renders what's already there.
# ---- shared progress artifacts (v2.0.0) -------------------------------------
# Every gate transition snapshots its decision artifacts into the ACTIVE store
# (team/enterprise plan: in-repo .taskplane-kb/ — commit it and the whole org
# sees progress from a fresh clone; personal/private: the external store, so
# nothing leaks). Doubles as a context cache: a future session reads the
# snapshot instead of re-deriving it — shared progress AND cheaper tokens.
# Fail-open like the dashboard: publishing must never break the loop.

def _publish_artifacts(ws: str) -> str | None:
    import re as _re
    import shutil as _sh
    import time as _time
    try:
        state = load(ws) or {}
        slug = _re.sub(r"[^a-z0-9]+", "-",
                       str(state.get("goal") or "track").lower()
                       ).strip("-")[:60] or "track"
        root = os.path.join(tp.store_root(ws), "artifacts", slug)
        os.makedirs(root, exist_ok=True)

        def _cp(src):
            if os.path.isfile(src):
                _sh.copyfile(src, os.path.join(root, os.path.basename(src)))

        _cp(os.path.join(tp.tp_dir(ws), "dashboard.html"))
        _cp(os.path.join(tp.tp_dir(ws), "retro.md"))
        _cp(os.path.join(ws, "plan", "plan.md"))
        _cp(os.path.join(ws, "plan", "tasks.json"))
        _cp(os.path.join(ws, ".em-review", "findings.json"))
        _cp(os.path.join(ws, ".em-review", "report.md"))
        with contextlib.suppress(Exception):
            g = depgraph.load(ws)
            if g and g.get("modules"):
                with open(os.path.join(root, "graph.json"), "w") as f:
                    json.dump(g, f, indent=1)
        with contextlib.suppress(Exception):
            import dashboard as _dash
            line = _dash.headline_loop(ws)
            if line:
                p = os.path.join(root, "HEADLINES.md")
                prev = ""
                if os.path.exists(p):
                    lines = open(p).read().rstrip().splitlines()
                    prev = lines[-1] if lines else ""
                if not prev.endswith(line):        # skip consecutive repeats
                    with open(p, "a") as f:
                        if not prev:
                            f.write(f"# {state.get('goal', 'track')} — "
                                    "progress log\n\n")
                        stamp = _time.strftime("%Y-%m-%d %H:%M UTC",
                                               _time.gmtime())
                        f.write(f"- {stamp} · {line}\n")
        return root
    except Exception:
        return None


# Fail-open: a dashboard problem must never break the loop itself.

def _with_dashboard(fn):
    def wrapped(ws, *a, **k):
        out = fn(ws, *a, **k)
        try:
            if isinstance(out, dict) and "error" not in out:
                import dashboard as _dash
                frag = _dash.widget(ws)
                p = os.path.join(tp.tp_dir(ws), "dashboard.html")
                tmp = f"{p}.tmp.{os.getpid()}"
                with open(tmp, "w") as f:
                    f.write(frag)
                os.replace(tmp, p)
                out["dashboard"] = {
                    "path": os.path.join(".taskplane", "dashboard.html"),
                    "render": "refreshed for this transition — show it "
                              "(mcp__visualize__show_widget) before "
                              "proceeding; the dashboard is the interface "
                              "the human governs through"}
                root = _publish_artifacts(ws)
                if root:
                    out["artifacts"] = {
                        "path": root,
                        "note": "gate-state snapshot (dashboard, plan, "
                                "findings, graph, HEADLINES.md) — on a team "
                                "store commit it with the work so the org "
                                "sees progress; future sessions read it "
                                "instead of re-deriving (token cache)"}
        except Exception:
            pass
        return out
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    wrapped.__wrapped__ = fn
    return wrapped


gate = _with_dashboard(gate)
submit = _with_dashboard(submit)
next_action = _with_dashboard(next_action)
approve = _with_dashboard(approve)
retro = _with_dashboard(retro)
