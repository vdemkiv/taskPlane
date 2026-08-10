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
import json
import os
import time

import depgraph
import kb
import lens as lens_router
import requirements as reqs
import taskplane_lite as tp

LOOP_FILE = "loop.json"

# R-0006 row 1: the EVALUATE step routes lenses with the BUILD stage
# profile (route v2: build-profile candidates, R-0001 budget 5-7/cap-8
# inherited verbatim, component assembly from R-0003). ONE constant feeds
# BOTH the evaluate brief's routing and _evaluation_errors' expected-lens
# derivation, so the validator's expectation can never drift from what
# was dispatched. The em step never uses this: it stays breadth="all"
# (full catalog), where route v2 deliberately does not engage.
EVALUATE_ROUTE_STAGE = "build"


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


def state_dir(ws: str) -> str:
    """THE exported owner of the loop-state location rule (v2.3.0).

    Any module that touches per-user coordination state (loop.json,
    tracks.json — see docs/state-spec.md, 'Loop coordination state is
    per-user') must resolve its directory HERE instead of re-deriving via
    tp.kb_root/store_root: re-derivation is exactly how track state ended up
    in the committed team store on a team plan. TASKPLANE_STORE=repo remains
    the single exception, and this function owns it."""
    return _state_dir(ws)

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
HUMAN_STEPS = {"design_approval", "plan_approval", "selection",
               "signoff", "escalated",
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
    # v2.3.0: a corrupt loop.json fails CLOSED with a typed error naming the
    # file and a remedy (tp.StateError) — never a bare JSONDecodeError
    # traceback, and never a silent default that would mask the corruption.
    return tp.load_json(p, what="loop state file")


def save(ws: str, state: dict) -> None:
    os.makedirs(_state_dir(ws), exist_ok=True)
    # Atomic write (tp.atomic_write_json): parallel wave workers gate
    # concurrently against the shared loop.json — a torn read of a
    # half-written file is a corrupt loop that stalls everyone; a reader only
    # ever sees a complete state. (Lost-update races between concurrent
    # read-modify-write are serialized by `mutate()` below, which holds an
    # exclusive lock across the whole load→change→save.)
    tp.atomic_write_json(_loop_path(ws), state, indent=2)
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

    v2.3.0: the lock is tp.file_lock — where flock is unavailable or refused
    (Windows, FUSE/NFS/SMB mounts, exactly the hosts this plugin targets) it
    falls back to an atomic mkdir spin-lock, and if even that cannot be
    acquired it raises tp.StateError. Wave serialization is therefore never
    SILENTLY lost the way the old `except OSError: pass` fallback lost it.
    """
    os.makedirs(_state_dir(ws), exist_ok=True)
    with tp.file_lock(_loop_path(ws)):
        st = load(ws)
        yield st
        if st is not None:
            save(ws, st)


TERMINAL_STEPS = ("done", "failed")


def init(ws: str, goal: str, spec_path: str | None = None,
         max_fix_cycles: int = 2, checkpoints=None,
         requirement_id: str | None = None, parallel: bool = False,
         design: bool = False, design_only: bool = False,
         force: bool = False) -> dict:
    checkpoints = list(checkpoints if checkpoints is not None else
                       ["plan", "em"])
    # v2.3.0: init over an IN-FLIGHT loop refuses by default — one mistyped
    # init must not silently reset a governed session's step, tasks,
    # approvals and baseline. `force` discards deliberately, and even then
    # the prior state file is archived (visible, recoverable), never erased.
    existing = load(ws)
    archived_to = None
    if existing and existing.get("step") not in TERMINAL_STEPS:
        if not force:
            return {"error": "an active loop already exists at step="
                             f"'{existing.get('step')}' — refusing to discard "
                             "its progress. Finish or abort it first "
                             "(`loop resolve abort`), or re-run init with "
                             "force to archive the current state and restart.",
                    "refused": True, "step": existing.get("step")}
        src = _loop_path(ws) if os.path.exists(_loop_path(ws)) \
            else _legacy_loop_path(ws)
        archived_to = _loop_path(ws) + time.strftime(
            ".replaced-%Y%m%d-%H%M%S") + f".{os.getpid()}"
        os.makedirs(_state_dir(ws), exist_ok=True)
        os.replace(src, archived_to)
        tp.trace(ws, "loop_init_replaced", prior_step=existing.get("step"),
                 archived_to=archived_to)
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
    out = dict(state)
    if archived_to:
        out["previous_loop_archived"] = archived_to
        out["note"] = f"previous in-flight loop archived to {archived_to}"
    # v2.0.0: point the driver at prior gate snapshots (context cache) -
    # read the published state instead of re-deriving it.
    with contextlib.suppress(Exception):
        art = os.path.join(tp.store_root(ws), "artifacts")
        tracks = sorted(os.listdir(art)) if os.path.isdir(art) else []
        if tracks:
            out["prior_artifacts"] = {
                "path": art, "tracks": tracks,
                "note": "prior gate snapshots (dashboard, plan, "
                        "findings, graph, HEADLINES) - read these "
                        "before re-deriving context"}
    return out


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
            test_command=task.get("tests"), plan_minted=True, regression_gate=True,
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
    except (OSError, _sp.SubprocessError, UnicodeDecodeError) as e:
        # Degraded nudging must be VISIBLE, never silent (v2.3.0): the
        # reviewer loses side-effect-channel hints, so say so once.
        import sys as _sys
        print(f"taskplane: edge-nudge scan degraded ({e.__class__.__name__}: "
              f"{e}) — record runtime edges manually via `tp graph edge`",
              file=_sys.stderr)
        try:
            tp.trace(ws, "edge_nudges_failed", error=str(e))
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

def _scopes_overlap(a, b) -> bool:
    """Two scopes conflict when one's fixed prefix contains the other's, on
    path-segment boundaries — conflicting tasks are serialized into later
    waves. Segment-aware so sibling dirs (src/a vs src/ab) do NOT collide,
    and empty-prefix globs don't conflict with everything. (The path math
    itself lives in the kernel — tp.scope_stems / tp.seg_prefix.)"""
    sa, sb = tp.scope_stems(a), tp.scope_stems(b)
    return any(tp.seg_prefix(x, y) or tp.seg_prefix(y, x)
               for x in sa for y in sb)


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
        dispatch = tp.dispatch_fields(
            "step", "tp-executor", t["id"], tp.step_tier("execute", t))
        prime = lens_router.prime_scope(t.get("scope"),
                                        task_type=t.get("type"))
        recalled = kb.retrieve(ws, files=t.get("scope") or [],
                               tags=[t["id"]], limit=3)
        rid = t.get("req") or state.get("requirement_id")
        rec = reqs.get_requirement(ws, rid) if rid else None
        is_variant = bool(state.get("ab") and t.get("variant"))
        entries.append({**dispatch,
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
    # v2.3.0 (scalability): DoR preparation shells out to git in the worker's
    # worktree (slow) — prepare OUTSIDE the global lock, then commit the
    # claim under the lock with a status RE-CHECK.
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    if not state.get("parallel"):
        # A1 (R-0007): a direct claim on a serial loop forms a wave whose
        # submits deadlock (decision 0011) — fail closed BEFORE any
        # contract/DoR work, backstopping wave()'s existing refusal.
        tp.trace(ws, "loop_claim_blocked", task=task_id, reason="serial_mode")
        return {"error": "loop was initialized without --parallel — a wave "
                         "cannot claim; re-init with --parallel or run "
                         "serially via `loop next`"}
    t = next((x for x in state.get("tasks") or [] if x["id"] == task_id),
             None)
    if t is None:
        return {"error": f"no task {task_id}"}
    if t.get("status") not in ("pending", "running"):
        return {"error": f"task {task_id} is {t.get('status')} — "
                         "not claimable"}
    contract = tp.build_contract(
        f"EXECUTE: {t['id']}", scope=t.get("scope"),
        test_command=t.get("tests"), plan_minted=True, regression_gate=True,
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
    # Two concurrent claimers: the claimability check is REPEATED under the
    # shared lock on a fresh read, so both cannot win the same task.
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

def next_action(ws: str, rid: str | None = None) -> dict:
    """Advance to the current step's work: activate its contract and return
    what the driver should run. Human steps pause without activating."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop — run `tp.py loop init` first"}
    # v2.3.0 wiring: attach a requirement BEFORE the design DoR evaluates —
    # the sanctioned mid-loop exit for a loop started without --req. The
    # validator (design_contract.design_attach_requirement) enforces the same
    # completeness the DoR demands; failure blocks, success persists.
    if rid:
        attach_errors: list = []
        with mutate(ws) as st:
            if st is None:
                return {"error": "no active loop — run `tp.py loop init` "
                                 "first"}
            attach_errors = _dc.design_attach_requirement(ws, st, rid)
        if attach_errors:
            return {"error": "requirement attach failed",
                    "blockers": attach_errors}
        state = load(ws)
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
            # v2.3.0 wiring: accepted design drift and hand-declared edge
            # realizations are VISIBLE at sign-off, not dead-on-pass.
            findings, _errs = _read_json(
                os.path.join(ws, ".em-review", "findings.json"))
            notices = _dc.design_review_notices(
                (findings or {}).get("meta") or {})
            if notices:
                out["notices"] = notices
        if step == "design_approval":
            design_errors = _design_dod_errors(ws, state)
            out["dod"] = {"passed": not design_errors,
                          "errors": design_errors,
                          "fingerprint": _design_evidence_fingerprint(ws)}
            # v2.3.0 wiring: self-attested lens evidence is surfaced AT the
            # human gate instead of being silently accepted.
            notices = _dc.design_approval_notices(ws)
            if notices:
                out["notices"] = notices
        return out

    # Parallel mode: EXECUTE is a wave (dispatch handled by `wave`/`claim`);
    # once workers report built, evaluate them one by one (read-only).
    # v2.3.0: the built→evaluate flip is a read-modify-write of the SHARED
    # loop.json while wave workers gate concurrently — apply it under
    # mutate() to a FRESH read (the same lost-update class H2 closed in
    # gate()), so a worker's just-gated status is never clobbered by saving
    # this function's earlier unlocked snapshot.
    if step == "execute" and state.get("parallel"):
        moved = False
        with mutate(ws) as fresh:
            if fresh is None:
                return {"error": "no active loop — run `tp.py loop init` "
                                 "first"}
            if fresh.get("step") != "execute" or not fresh.get("parallel"):
                moved = True                # advanced under us — re-dispatch
            else:
                built = [i for i, t in enumerate(fresh.get("tasks") or [])
                         if t.get("status") == "built"]
                if built:
                    fresh["current_task"] = built[0]
                    fresh["step"] = "evaluate"
            state = fresh
        if moved:
            return next_action(ws)
        step = state["step"]
        if step == "execute":
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

    if step == "design" and not state.get("design_approved"):
        # H3 (v2.2.1): until the design is human-approved, the graph
        # baseline follows the CURRENT scan — capturing once from a stale
        # graph and then blocking on "rescan" left the stored fingerprint
        # permanently mismatched (the engine's own remedy deadlocked the
        # step). A pre-approval rescan re-baselines, with a trace.
        current_fp = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
        if state.get("design_graph_fingerprint") != current_fp:
            # v2.3.0: persist the rebaseline under the state lock on a fresh
            # read — a bare save() here could clobber a concurrent update.
            with mutate(ws) as fresh:
                if fresh is not None and fresh.get("step") == "design" \
                        and fresh.get("design_graph_fingerprint") != current_fp:
                    if fresh.get("design_graph_fingerprint"):
                        tp.trace(
                            ws, "design_rebaseline",
                            old=(fresh["design_graph_fingerprint"] or "")[:12],
                            new=(current_fp or "")[:12])
                    fresh["design_graph_fingerprint"] = current_fp
                if fresh is not None:
                    state = fresh

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
            tp.trace(ws, "graph_refresh_failed", step=step, error=str(exc))
    elif step == "evaluate" and not state.get("parallel"):
        try:
            depgraph.scan(ws)
        except Exception as exc:
            if state.get("graph_governance"):
                return {"error": f"graph refresh failed before {step}: {exc}",
                        "step": step, "status": status(ws)}
            tp.trace(ws, "graph_refresh_failed", step=step, error=str(exc))
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
        # R-0006 row 1: evaluate passes the build stage so route v2
        # engages (build-profile candidates, inherited budget, floors
        # survive narrowing, n/a entries carry negative evidence). The em
        # step passes NO stage — its full-catalog mandate is untouched.
        routing = lens_router.route_git_diff(
            diff_ws, base=state.get("baseline") or "HEAD",
            task_type=(task or {}).get("type"),
            stage=None if step == "em" else EVALUATE_ROUTE_STAGE,
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
            own = depgraph.req_node(own) if own else None
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

    # Audit cadence (v3 Phase 1): the em brief advertises audit mode — due
    # every Nth completed review (default 5) or on a release flag — and,
    # when due, carries the recorded routing decision so the gate can diff
    # the breadth=all findings against it (router-regression auto-filing).
    audit_info = None
    if step == "em":
        audit_info = _audit_brief(ws, state)
        if audit_info.get("due"):
            tp.trace(ws, "audit_due", reason=audit_info.get("reason"),
                     reviews_completed=audit_info.get("reviews_completed"))

    # Requirement anchoring: this task's R-id (or the loop's) is the spine —
    # its acceptance criteria are the DoD the evaluator holds the work to.
    req_rec = None
    rid = (task or {}).get("req") or state.get("requirement_id")
    if rid:
        req_rec = reqs.get_requirement(ws, rid)

    dispatch = tp.dispatch_fields(
        "step", STEP_ROLE[step], (task or {}).get("id") or step,
        tp.step_tier(step, task))
    model_tier, model = dispatch["model_tier"], dispatch["model"]
    reasoning_effort, task_name = (dispatch["reasoning_effort"],
                                   dispatch["task_name"])
    tp.trace(ws, "model_tier", step=step,
             task=(task or {}).get("id"), tier=model_tier, model=model,
             reasoning_effort=reasoning_effort)
    tp.record_expected_dispatch(ws, "step", STEP_ROLE[step], model_tier,
                                model, ref=(task or {}).get("id") or step,
                                task_name=task_name,
                                reasoning_effort=reasoning_effort,
                                role_marker_value=dispatch["role_marker"])
    model_note = None
    if model is None and (model_tier or "standard") != "standard":
        model_note = (f"tier '{model_tier}' resolves to inherit on this "
                      f"host — the planned routing has no effect; set "
                      f"TASKPLANE_MODEL_{str(model_tier).upper()} to "
                      "activate it")
    return {**dispatch,
        **({"model_note": model_note} if model_note else {}),
        "step": step,
        "codex_dispatch": ("Use Codex's native subagent task orchestration with "
                           "this exact task_name, role instructions, standalone "
                           "role_marker, model when non-null, and "
                           "reasoning_effort."),
        "task": task,
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
        "audit": audit_info,
        "impact": imp and {**imp, "context": depgraph.render_context(imp)},
        "design": _design_context(ws, state),
        "design_graph": ({
            "baseline_fingerprint": state.get("design_graph_fingerprint"),
            "summary": depgraph.summary(ws),
            "policy": depgraph.impact_policy({}),
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
                    f"{t and t['id']}: START with `tp loop evidence --write` — "
                    "one call returns the suite result, the diff, and the exact "
                    "criteria, routed-lens and graph obligations this gate "
                    "demands, judgment slots empty; do NOT rebuild those by "
                    "hand. Then do what the engine cannot: prove each criterion "
                    "against real behavior, apply each ROUTED lens (prompt at "
                    "lenses/<id>.md) — inline ones yourself, one governed "
                    "read-only subagent per subagent-mode lens — and disposition "
                    "graph impact + affected requirements; reject stale Design "
                    "evidence. Fill the empty slots in .eval/verdict.json "
                    "(submitted unchanged, it is refused). Then `loop submit "
                    "pass|fail`; only the orchestrator calls `loop gate`.",
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


# Design Contract validation lives in design_contract.py (v2.2.1) — thin
# delegates keep loop's internal API stable for callers and tests.
import design_contract as _dc

_read_json = _dc.read_json
DESIGN_SCHEMA = _dc.DESIGN_SCHEMA
DESIGN_CONTRACT = _dc.DESIGN_CONTRACT
DESIGN_NARRATIVE = _dc.DESIGN_NARRATIVE
_design_path = _dc.design_path
_design_contract = _dc.design_contract
_design_safe_rel = _dc.design_safe_rel
_design_evidence_paths = _dc.design_evidence_paths
_design_evidence_fingerprint = _dc.design_evidence_fingerprint
_design_current_errors = _dc.design_current_errors
_design_dor = _dc.design_dor
_design_dod_errors = _dc.design_dod_errors
_design_plan_errors = _dc.design_plan_errors
_design_review_errors = _dc.design_review_errors


def _design_context(ws: str, state: dict) -> dict | None:
    if not state.get("design_required"):
        return None
    contract, errors = _design_contract(ws)
    approved = bool(state.get("design_fingerprint"))
    stale = _design_current_errors(ws, state) if approved else []
    if stale:
        # M8 (v2.2.1): an approved design whose artifacts changed after
        # approval is NOT served as approved — the same staleness the
        # gates enforce is reported in every brief that carries it.
        approved = False
        errors = list(errors or []) + stale
    return {"approved": approved,
            "stale": bool(stale) or None,
            "fingerprint": state.get("design_fingerprint"),
            "contract": contract, "errors": errors}


def _criteria_for(ws: str, state: dict, task: dict) -> list:
    criteria = list(task.get("criteria") or [])
    rid = task.get("req") or state.get("requirement_id")
    rec = reqs.get_requirement(ws, rid) if rid else None
    if rec and not criteria:
        criteria = list(rec.get("acceptance") or criteria)
    criteria = [str(c).strip() for c in criteria if str(c).strip()]
    if not criteria and str(task.get("tests") or "").strip():
        criteria = [f"test command passes: {task['tests']}"]
    return criteria


def _aggregate_impact_policy(tasks) -> dict:
    return depgraph.aggregate_impact_policy(tasks)


def _plan_dor_errors(ws: str, state: dict, apply: bool = False) -> list:
    """Definition of Ready for implementation, derived from the plan.

    M3 (v2.2.1): a Ready CHECK must not mutate. With apply=False
    (default) this is pure — it inspects and reports. Only the plan
    GATE passes apply=True, which merges requirement contracts into
    tasks, records requirement/contract edges, resolves each task's
    impact policy, and stores the graph DoR verdict on the state."""
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
                cids = depgraph.contract_ids([contract])
                cid = cids[0] if cids else ""
                if cid and cid not in seen_contracts:
                    merged_contracts.append(contract)
                    seen_contracts.add(cid)
            if apply:
                task["contracts"] = merged_contracts
            for dep in rec.get("depends_on") or []:
                if reqs.get_requirement(ws, dep) is None:
                    errors.append(prefix + f"requirement dependency {dep} "
                                  "does not exist")
                elif apply:
                    # Requirements are the source of truth. Reconcile their
                    # product edges before graph Ready instead of depending on
                    # a particular CLI path having populated the derived map.
                    depgraph.link_requirement_dep(ws, rid, dep)
            if apply:
                for contract in rec.get("contracts") or []:
                    cids = depgraph.contract_ids([contract])
                    relation = (contract.get("relation", "changes")
                                if isinstance(contract, dict) else "changes")
                    if cids:
                        depgraph.record_edge(
                            ws, depgraph.req_node(rid), cids[0],
                            kind=relation, confidence="high")
        if apply:
            task["impact_policy"] = depgraph.impact_policy(task)
        if rid and task.get("high_cost"):
            if rec is None:
                errors.append(prefix + f"requirement {rid} does not exist")
            elif rec.get("open_questions"):
                errors.append(prefix + "requirement has unresolved questions: "
                              + "; ".join(rec["open_questions"]))
    graph_dor = depgraph.readiness(ws, state.get("tasks") or [])
    if apply:
        state["graph_dor"] = graph_dor
    errors.extend("graph DoR: " + e for e in graph_dor.get("errors") or [])
    errors.extend(tp.requirement_coverage_errors(state.get("tasks") or [],
        lambda rid: reqs.get_requirement(ws, rid), state.get("requirement_id")))
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
        test_command=task.get("tests"), plan_minted=True, regression_gate=True)
    # Scope regression evidence to this task; loop-owned artifacts self-gate.
    regression_files = [f for f in (tp.changed_files(ws, snapshot) if snapshot else [])
                        if tp.match_any(f, task.get("scope") or [])]
    return (_design_current_errors(ws, state) + tp.dod_check(
        contract, ws, snapshot, ignore_prefixes=lens_router.LOOP_OWNED,
        regression_files=regression_files))


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

    # Derive the expected lens set with the SAME stage the evaluate brief
    # routed with (EVALUATE_ROUTE_STAGE — single-sourced, R-0006 row 1), so
    # expectation matches dispatch. Route v2 returns EVERY catalog lens for
    # coverage honesty; only the ROUTED ones (deep + light, mode != "none")
    # owe the evaluator a verdict row — n/a lenses carry their negative
    # evidence in the routing itself. On the legacy path no entry has mode
    # "none", so the filter is a no-op there.
    routing = lens_router.route_git_diff(
        ws, base=state.get("baseline") or "HEAD",
        task_type=task.get("type"), stage=EVALUATE_ROUTE_STAGE,
        breadth="routed")
    expected_lenses = {entry["id"] for entry in routing.get("lenses") or []
                       if entry.get("mode") != "none"}
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
            own = depgraph.req_node(own) if own else None
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


# One canonical severity vocabulary (v2.3.0). Producers disagree — the lens
# brief says high|med|low, the lens catalog's verdict schema says
# blocker|major|minor|question|praise, free-form reviews say critical —
# so every CONSUMER normalizes through this map. Enforcement rule: unknown
# or foreign severities map UP to 'high'; a finding a gate cannot classify
# must BLOCK, never pass or render as medium (fail closed).
SEVERITY_CANONICAL = ("high", "med", "low", "info")
_SEVERITY_MAP = {
    "high": "high", "critical": "high", "blocker": "high", "major": "high",
    "sev1": "high", "p0": "high", "p1": "high",
    "med": "med", "medium": "med", "moderate": "med",
    "low": "low", "minor": "low", "trivial": "low",
    "info": "info", "question": "info", "praise": "info", "note": "info",
    "nit": "info",
}


def normalize_severity(value) -> str:
    """Map any producer's severity onto the canonical enum — UNKNOWN maps UP
    to 'high' so an unclassifiable finding blocks rather than slips through.
    Shared consumption point for the EM gate and the dashboard renderer."""
    return _SEVERITY_MAP.get(str(value or "").strip().lower(), "high")


# Review discipline (v2.3.1). A finding's CLASS decides whether it gates a
# change, orthogonally to how bad it is. This is what stops a whole-tree
# 26-lens sweep (which always yields ~100 observations) from reading as "100
# blockers": only a regression, or a NEW high defect in the change's own diff,
# blocks — pre-existing debt and taste are surfaced but never block the change.
FINDING_CLASSES = ("regression", "pre-existing", "observation")
_CLASS_MAP = {
    "regression": "regression", "regressed": "regression",
    "pre-existing": "pre-existing", "preexisting": "pre-existing",
    "pre_existing": "pre-existing", "existing": "pre-existing",
    "debt": "pre-existing", "legacy": "pre-existing",
    "observation": "observation", "taste": "observation",
    "style": "observation", "nit": "observation", "opinion": "observation",
    "suggestion": "observation", "enhancement": "observation",
}


def normalize_finding_class(value) -> str:
    """Canonical finding class, or 'unclassified' when absent/foreign.

    Unlike severity, an unknown class maps to 'unclassified' (NOT up to
    'regression') — taste must never be inflated to a blocker. But an absent
    class does NOT let a high slip through either: `finding_blocks` routes an
    unclassified finding through the severity rule, so you cannot hide a real
    high defect merely by omitting the class."""
    v = str(value or "").strip().lower()
    return _CLASS_MAP.get(v, "unclassified")


def _finding_in_diff(finding: dict, changed_files) -> bool:
    if changed_files is None:
        return True                      # no diff context → cannot exclude
    f = str(finding.get("file") or "").replace("\\", "/")
    return f in {str(c).replace("\\", "/") for c in changed_files}


def finding_blocks(finding: dict, changed_files=None) -> bool:
    """Does this finding block THIS change's gate?

      regression                     -> always blocks
      pre-existing / observation     -> never blocks (surfaced, tracked)
      unclassified + high + in-diff  -> blocks (a new high defect in the
                                        change's own surface — fail closed)
      unclassified + high + no diff  -> blocks (cannot prove it's old)
      anything else                  -> does not block
    """
    cls = normalize_finding_class(finding.get("class"))
    if cls == "regression":
        return True
    if cls in ("pre-existing", "observation"):
        return False
    # unclassified: fall back to the severity rule (danger fails closed)
    if normalize_severity(finding.get("severity")) != "high":
        return False
    return _finding_in_diff(finding, changed_files)


def classify_findings(findings, changed_files=None) -> dict:
    """Split a findings list into the blocker set and the triage buckets, so a
    review headline reads '7 block · 93 to triage' instead of '100 issues'."""
    out = {"blockers": [], "regressions": [], "pre_existing": [],
           "observations": [], "unclassified": []}
    for f in findings or []:
        cls = normalize_finding_class(f.get("class"))
        if cls == "regression":
            out["regressions"].append(f)
        elif cls == "pre-existing":
            out["pre_existing"].append(f)
        elif cls == "observation":
            out["observations"].append(f)
        else:
            out["unclassified"].append(f)
        if finding_blocks(f, changed_files):
            out["blockers"].append(f)
    return out


# Evidence bundle → evidence.py (P2 / R-0012); extracted like audit.py, same
# line ratchet. UNTRUSTED INPUT — _evaluation_errors re-derives every
# obligation itself (evidence.py's docstring: why it stays out of VALIDATOR_SURFACE).
from evidence import EVIDENCE_JUDGMENT_KEYS, evidence  # noqa: E402,F401

# ---------------------------------------------------------------------------
# Audit sweep cadence + router-regression auto-filing (v3 Phase 1, R-0001):
# MOVED VERBATIM to audit.py (R-0006 / D-0004, v3 Phase 2), byte-frozen by
# taskplane/tests/test_audit_extraction.py. The names below are CALLER
# aliases bound once at import — NOT patch seams (t9 / R-0011 E6). Patch the
# MACHINERY at audit.<name> (audit.audit_counter, audit.audit_every, …),
# resolved module-locally inside audit.py: rebinding the loop alias is
# invisible to audit_due. Patch the GATE MATH at loop.<name>
# (finding_blocks, normalize_finding_class, load, _state_dir) — audit.py
# late-binds those via _loop() (audit.py:41-51) every call, so a patched
# loop.finding_blocks does govern the gate. TestPatchSeams pins both halves.
from audit import (  # noqa: E402,F401 — re-exports, not dead imports
    AUDIT_EVERY_DEFAULT,
    AUDIT_FILE,
    _audit_brief,
    _audit_path,
    _is_machinery_warn_row,
    _is_router_regression,
    _release_review_flagged,
    _blocking_claim_errors,
    _router_audit_gate,
    _router_regression_key,
    _routing_decision_from_meta,
    _routing_decision_of,
    _unresolved_high_errors,
    audit_counter,
    audit_due,
    audit_every,
    record_audit_review,
    router_audit,
)


def _coverage_disposition(v) -> str:
    """Legacy coverage values are tier strings ('deep'|'sweep'); v2 values
    (contract:findings-v2) are {verdict, ...} objects. One accessor so the
    tier validation accepts both shapes."""
    if isinstance(v, dict):
        v = v.get("verdict")
    return str(v or "")


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
    # Legacy tiers ('deep'|'sweep') and v2 verdicts (contract:findings-v2:
    # {verdict: deep|light|n/a|deep (forced), ...}) are both valid coverage.
    valid_tiers = ("deep", "sweep", "light", "n/a", "deep (forced)")
    invalid = sorted(k for k, v in coverage.items()
                     if k in expected
                     and _coverage_disposition(v) not in valid_tiers)
    if missing:
        errors.append("engineering review omitted lenses: " + ", ".join(missing))
    if invalid:
        errors.append("engineering review has invalid lens tiers: "
                      + ", ".join(invalid))
    # EM v3 tightening: a lens skipped as n/a must carry MACHINE-CHECKABLE
    # negative evidence (the v2 dict shape with negative_evidence). A bare
    # string "n/a" asserted the skip without evidence AND slipped past the
    # router-audit backstop (which only diffs dict-shaped decisions) — the
    # one disposition that reduces coverage was the one with no proof.
    bare_na = sorted(
        k for k, v in coverage.items()
        if k in expected and isinstance(v, str) and v.strip().lower() == "n/a")
    if bare_na:
        errors.append(
            "engineering review marks lenses n/a without negative evidence "
            "(use the v2 dict shape {verdict: 'n/a', negative_evidence: "
            "[...]}): " + ", ".join(bare_na))
    else:
        for k, v in sorted(coverage.items()):
            if (k in expected and isinstance(v, dict)
                    and str(v.get("verdict", "")).strip().lower() == "n/a"
                    and not v.get("negative_evidence")):
                errors.append(
                    "engineering review marks lens n/a with EMPTY "
                    "negative_evidence: " + k)
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
        errors.append("engineering review does not recommend sign-off — "
                      'set meta.gate.verdict to "pass" or "recommend-pass" '
                      "in .em-review/findings.json")
    rows = findings.get("findings") or []
    if not isinstance(rows, list):
        errors.append("engineering findings must be a list")
        rows = []
    # v2.3.0 raw unresolved-high sweep (body in audit.py): unknown
    # severities normalize UP to high and BLOCK. Machinery warn rows are
    # exempt ONLY when re-derived as legitimate this run — the A5 shape
    # alone is a costume any findings author can wear.
    errors.extend(_unresolved_high_errors(meta, rows))
    # R-0013: commentary may not block this gate (body in audit.py).
    errors.extend(_blocking_claim_errors(ws, state, rows))
    # Audit sweep (v3 Phase 1): when the review recorded a routing decision,
    # diff the findings against it — n/a-lens findings are auto-filed as
    # router regressions and block sign-off via the frozen finding_blocks
    # rule (no guardrail change).
    errors.extend(_router_audit_gate(ws, path, findings, meta, rows))
    return errors


def submit(ws: str, outcome: str, note: str = "",
           task_id: str | None = None) -> dict:
    """Worker submission — evidence request, never a state transition.

    Trust boundary (L12, v2.2.1): "orchestrator-only gating" is a PROTOCOL
    guarantee, not a process-isolation one — any process with workspace
    access can call gate(). What holds mechanically is the EVIDENCE: a gate
    only advances when the fingerprinted submission matches the bytes on
    disk, so a worker gating itself still cannot pass unproven work. Gate
    calls are traced for after-the-fact attribution.

    The engine, not the worker, computes the changed paths and fingerprint.
    The orchestrator subsequently calls ``gate``; if anything changed between
    submission and validation, the gate rejects the stale evidence.  Repeating
    the same submission is idempotent, which makes interrupted/resumed drivers
    safe.

    A4 (decision 0018): the record additionally carries ``engine_fingerprint``
    — the identity of the ENGINE BUILD that produced it (tp.engine_fingerprint
    over the validator surface). Purely additive: older gates ignore the
    unknown key, and the evaluate gate uses it to refuse evidence produced by
    a different build than the one validating it.
    """
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state.get("step")
    if step not in ("execute", "fix", "evaluate", "em"):
        return {"error": f"step '{step}' is not a worker submission step — "
                         "run `loop next` to see the current role and "
                         "instruction; submissions happen at execute/fix/"
                         "evaluate/em"}
    if outcome not in ("pass", "fail"):
        return {"error": "submission outcome must be pass or fail"}

    task = _current_task(state)
    act_ws = ws
    parallel_execute = step == "execute" and state.get("parallel")
    if not parallel_execute and task_id and \
            task_id != (task or {}).get("id"):
        # H1 (v2.2.1): outside a parallel EXECUTE wave the engine evaluates
        # ONE current task — silently dropping a mismatched --task would
        # record this worker's evidence against a different task.
        return {"error": f"--task {task_id} does not match the current "
                         f"task '{(task or {}).get('id')}' at step "
                         f"'{step}' — a wave worker submits only during "
                         "parallel EXECUTE; otherwise omit --task or "
                         "pass the current task's id"}
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
        "engine_fingerprint": tp.engine_fingerprint(),
        # A4 REPAIR (EM, v3 phase 3): engine_fingerprint attests the process
        # RUNNING submit — the same installed plugin the gate uses, so it
        # could never fire. Stamp the engine in the workspace the EVIDENCE
        # came from; None where that workspace carries no engine copy.
        "evidence_engine_fingerprint":
            tp.workspace_engine_fingerprint(act_ws),
        "submitted_at": int(time.time()),
    }
    with mutate(ws) as locked:
        if locked is None:
            return {"error": "no active loop"}
        def _same(existing):
            # engine_fingerprint is part of the identity: a re-submission
            # under a DIFFERENT engine must replace the record, not be
            # deduplicated into it (A4's in-flight remedy).
            return existing and all(
                existing.get(k) == submission.get(k)
                for k in ("step", "task", "outcome", "fingerprint",
                          "engine_fingerprint"))
        if parallel_execute:
            target = next((x for x in locked.get("tasks") or []
                           if x.get("id") == task_id), None)
            if target is None:
                return {"error": f"no task {task_id}"}
            if _same(target.get("_submission")):
                submission = target["_submission"]
            else:
                target["_submission"] = submission
        else:
            if _same(locked.get("_submission")):
                submission = locked["_submission"]
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


def gate(ws: str, outcome: str, note: str = "", task_id: str | None = None,
         rid: str | None = None) -> dict:
    """Record the current step's outcome, transition, and clear its contract."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    # v2.3.0 wiring: `--req R-xxxx` attaches a requirement to the in-flight
    # loop through the SANCTIONED validator (design_contract.design_attach_
    # requirement) — it validates exactly what the design DoR demands and
    # refuses to swap an anchored requirement; nothing downstream is skipped.
    if rid:
        attach_errors: list = []
        with mutate(ws) as st:
            if st is None:
                return {"error": "no active loop"}
            attach_errors = _dc.design_attach_requirement(ws, st, rid)
        if attach_errors:
            return {"error": "requirement attach failed — the gate was not "
                             "evaluated", "blockers": attach_errors}
        state = load(ws)
    step = state["step"]

    # v2.3.0: validate --task FIRST in a parallel wave. An unknown id used to
    # fall through to "worker evidence was not submitted", telling the driver
    # to submit for a task that does not exist (mirrors H1's submit-side
    # validation).
    if step == "execute" and state.get("parallel"):
        members = [str(x.get("id")) for x in state.get("tasks") or []]
        if not task_id:
            return {"error": "parallel gate needs --task <id> of a wave "
                             "member", "step": step}
        if task_id not in members:
            return {"error": f"unknown task id '{task_id}' — wave members: "
                             + ", ".join(members), "step": step}

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
        with mutate(ws) as locked:
            t = next((x for x in (locked.get("tasks") or [])
                      if x["id"] == task_id), None)
            if t is None:
                return {"error": "parallel gate needs --task <id> of a wave "
                                 "member"}
            # v2.3.0: the final staleness re-attest runs INSIDE the lock,
            # immediately before the status commits — no TOCTOU window
            # between the attest and the transition.
            if state.get("submission_required"):
                stale = _submission_staleness(ws, submission)
                if stale:
                    return {"error": stale + " during gate validation — "
                                     "submit the final state again",
                            "step": step}
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

    # H4 (v2.2.1): the pm gate was the one fail-open step — it advanced with
    # no spec and no submission. Symmetric minimal DoD: the authored
    # requirement must exist before the loop leaves Define.
    if step == "pm":
        if outcome != "pass":
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "pm rejected — staying at pm")
            return {"error": "pm gate: outcome was not 'pass' — refine the "
                             "requirement/spec, then gate again",
                    "step": "pm", "status": status(ws)}
        spec_rel = state.get("spec_path") or os.path.join("specs", "spec.md")
        spec_abs = spec_rel if os.path.isabs(spec_rel) \
            else os.path.join(ws, spec_rel)
        has_req = bool(state.get("requirement_id"))
        if not has_req and not (os.path.isfile(spec_abs)
                                and os.path.getsize(spec_abs) > 0):
            tp.trace(ws, "loop_gate_blocked", step=step, reason="dod",
                     errors=["no spec"])
            return {"error": "pm Definition of Done failed — no requirement "
                             "was authored. Write a non-empty specs/spec.md "
                             "(or record a requirement with `tp req new` and "
                             "attach its R-id), then gate again.",
                    "step": "pm",
                    "dod": {"passed": False,
                            "errors": [f"{spec_rel} missing or empty and no "
                                       "requirement_id is attached — the pm "
                                       "step authors the WHAT before the "
                                       "loop advances"]}}

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
        dor_errors = _plan_dor_errors(ws, state, apply=True)
        if dor_errors:
            tp.trace(ws, "loop_gate_blocked", step=step, reason="dor",
                     errors=dor_errors)
            return {"error": "Definition of Ready failed — revise "
                             "plan/tasks.json before approval or execution",
                    "step": "plan",
                    "dor": {"ready": False, "blockers": dor_errors}}
        # B2: ordering at the GATE too — checkpoint-less loops skip approve.
        if (refusal := tp.plan_ordering_refusal(ws, state.get("tasks"),
                                                "gate")):
            return refusal

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
        # A4: the engine that PRODUCED this evidence vs the one about to
        # judge it — a pure pre-check (decision 0018), so equal engines
        # leave the walk below byte-unchanged.
        if (skew := tp.engine_skew_refusal(ws, state.get("_submission"))):
            return skew
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

    # H2 (v2.2.1): validation above ran on a snapshot and can take seconds
    # (tests, evidence, graph). Apply the transition under the state LOCK to
    # a FRESH read, so a wave worker's concurrent update to another task is
    # never clobbered by saving this stale snapshot wholesale. Fields the
    # VALIDATION itself computed on the snapshot (loaded plan tasks, graph
    # DoR) are carried over explicitly.
    _validated = state
    with mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        if state.get("step") != step:
            return {"error": f"loop advanced to '{state.get('step')}' while "
                             "this gate was validating — run loop next and "
                             "gate again", "step": state.get("step")}
        # v2.3.0: the final staleness re-attest runs INSIDE the state lock,
        # immediately before the transition commits — the old pre-lock check
        # left a TOCTOU window in which a workspace edit got blessed by a
        # gate whose evidence was attested against different bytes. (The
        # contract is cleared AFTER the locked transition, below, so a
        # refused gate also leaves the workspace governed.)
        if _validated.get("submission_required") and step in \
                ("execute", "fix", "evaluate", "em"):
            stale = _submission_staleness(ws, submission)
            if stale:
                return {"error": stale + " during gate validation — submit "
                                 "the final state again", "step": step}
        if _validated.get("tasks") and not state.get("tasks"):
            state["tasks"] = _validated["tasks"]
        if step == "plan":
            # plan validation recomputed these on the snapshot via
            # _load_tasks: loaded tasks, the ab flag, the round-scoped
            # selection reset, and the graph DoR verdict.
            if _validated.get("tasks"):
                state["tasks"] = _validated["tasks"]
            if "ab" in _validated:
                state["ab"] = _validated["ab"]
            if "parallel" in _validated:
                state["parallel"] = _validated["parallel"]
            if "selection" not in _validated:
                state.pop("selection", None)
            if "graph_dor" in _validated:
                state["graph_dor"] = _validated["graph_dor"]
        elif "design_graph_fingerprint" in _validated and \
                "design_graph_fingerprint" not in state:
            state["design_graph_fingerprint"] = \
                _validated["design_graph_fingerprint"]
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
    if step == "em" and outcome == "pass":
        # One more COMPLETED engineering review: advance the audit cadence
        # (every Nth em review runs as a full audit sweep). A cadence-store
        # failure is traced, never allowed to block a validated sign-off.
        try:
            reviews = record_audit_review(ws)
            tp.trace(ws, "audit_review_recorded", reviews=reviews,
                     next_audit_due=audit_due(ws, state))
        except Exception as exc:      # noqa: BLE001
            tp.trace(ws, "audit_counter_failed", error=str(exc))
    # Release the step's contract only AFTER the locked transition committed
    # (v2.3.0): clearing before the lock left the workspace ungoverned during
    # the commit window; a refused gate above leaves it governed for retry.
    tp.clear(act_ws)
    tp.trace(ws, "loop_gate", step=step, outcome=outcome, note=note)
    return {"step": state["step"], "status": status(ws)}


def _signoff_dod(ws: str, state: dict) -> dict:
    """Mechanical final DoD over aggregate scope, requirements, tests, graph,
    engineering evidence, and committed knowledge. Human sign-off remains."""
    scopes: list = []
    for t in (state.get("tasks") or []):
        scopes.extend(t.get("scope") or [])
    baseline = state.get("baseline")
    errors: list = []
    errors.extend("requirement DoD: " + e for e in tp.requirement_coverage_errors(
        state.get("tasks") or [], lambda rid: reqs.get_requirement(ws, rid),
        state.get("requirement_id"), require_passed=True))
    if scopes:
        # Aggregate diff-scope, EXCLUDING loop-owned artifacts: they are
        # authored by governed steps under their own write-allow contracts
        # and human gates, so requiring them inside the union of TASK
        # scopes was a contradiction. Every other engine path (evaluate
        # routing, em review, impact, and — A2 — the per-task DoD) filters
        # lens_router.LOOP_OWNED the same way. Fail-closed stance
        # unchanged: no snapshot still errors, and NON-loop-owned files
        # outside the union still block.
        if not baseline:
            errors.append("diff_scope: cannot verify — no git snapshot "
                          "(commit the workspace before governing)")
        else:
            # plan_minted: the union IS the human-approved plan's scopes
            # (approved wildcard-free literals keep their provenance-gated
            # override); DEFAULT_OUT_OF_SCOPE here is STRICTER than the
            # old synthetic contract, which had no out_of_scope at all.
            coding = {"scope_paths": scopes,
                      "out_of_scope_paths": list(tp.DEFAULT_OUT_OF_SCOPE),
                      "plan_minted": True}
            for f in tp.changed_files(ws, baseline):
                if f.startswith(lens_router.LOOP_OWNED):
                    continue
                v = tp.scope_violation(f, coding)
                if v:
                    errors.append("diff_scope: " + v)
            if errors:
                errors.append(
                    "diff_scope recovery: revert the out-of-scope files or "
                    "widen the owning task's scope via the human gate "
                    "(attributable: trace + KB decision), then re-run")
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
            scope=task.get("scope"), test_command=test_command,
            plan_minted=True, regression_gate=True)
        # Aggregate scope is already checked; run each task's scoped evidence.
        test_contract["coding"]["dod"]["require_clean_scope_diff"] = False
        regression_files = [f for f in (tp.changed_files(ws, baseline)
                                        if baseline else [])
                     if tp.match_any(f, task.get("scope") or [])]
        errors.extend(f"task {task.get('id', '?')}: {e}" for e in tp.dod_check(
            test_contract, ws, baseline, regression_files=regression_files))
    errors.extend(_engineering_review_errors(ws, state))
    for problem in kb.lint(ws):
        errors.append("kb_lint: " + (problem.get("file", "?")) + " — "
                      + problem.get("problem", ""))
    return {"passed": not errors, "errors": errors,
            "scope": scopes, "baseline": baseline}


def _record_design_contracts(ws: str, state: dict, contract: dict | None) -> list:
    """The sanctioned mechanical path for DESIGN-introduced contracts into
    the dependency graph (v2.3.0).

    A design may legitimately propose a NEW boundary (e.g.
    contract:order-cancelled-v2) that is not declared on the requirement.
    Only requirement contracts were auto-recorded, the designer is forbidden
    to mutate the graph, and the planner's contract has no Bash tool — so
    graph readiness blocked with 'contracts are not recorded in the
    dependency graph' and no in-band remedy. At the human design-approval
    gate-PASS the engine records each approved design contract as a
    req→contract edge (registering the contract node), recorded + traced.
    Plan DoR is NOT weakened: it still independently verifies every declared
    contract is recorded — this only provides the governed path that records
    them. Returns the recorded contract ids."""
    rid = state.get("requirement_id")
    if not rid:
        return []
    applied = []
    for row in (contract or {}).get("contracts") or []:
        cids = depgraph.contract_ids([row])
        if not cids:
            continue
        relation = (row.get("relation", "changes")
                    if isinstance(row, dict) else "changes")
        depgraph.record_edge(ws, depgraph.req_node(rid), cids[0],
                             kind=relation, confidence="high",
                             note="approved design contract")
        applied.append(cids[0])
    if applied:
        tp.trace(ws, "design_contracts_recorded", gate="design_approval",
                 requirement=rid, contracts=applied)
    return applied


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
        own = depgraph.req_node(rid) if rid else None
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
    attestation_warning = None
    gate_notices: list = []
    if not str(by or "").strip() and step in ("plan_approval", "signoff"):
        # L5 (v2.2.1): symmetric attestation. Design approval hard-requires
        # --by; these two gates stay compatible but an anonymous pass is
        # RECORDED as unattributed and warned — not silently equivalent.
        by = "(unattributed)"
        attestation_warning = (f"{step} passed without --by — record WHO "
                               "approved (name + where) for an attributable "
                               "gate trail")
        tp.trace(ws, "loop_approve_unattributed", gate=step)
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
        # v2.3.0 wiring: notices (e.g. self-attested lens evidence) surface
        # in the approval response AND in the recorded approval decision.
        gate_notices = _dc.design_approval_notices(ws, contract)
        # v2.3.0: the sanctioned mechanical path for design-introduced
        # contracts into the graph — recorded + traced at the human
        # gate-pass (see _record_design_contracts). Plan DoR is unchanged.
        _record_design_contracts(ws, state, contract)
        modules = ((contract or {}).get("graph") or {}).get(
            "proposed_modules") or []
        kb.record_decision(
            ws, f"Design approved: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}\nApproved by: {by}\n"
                    f"Fingerprint: {state['design_fingerprint']}"
                    + ("".join("\nNotice: " + n for n in gate_notices)),
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
        # B2 (R-0008): mechanical brief-shape-before-golden-regen ordering.
        if (refusal := tp.plan_ordering_refusal(ws, state.get("tasks"),
                                                "approve", by=by)):
            return refusal
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
        # v2.3.0 wiring: the sign-off payload carries the review's design
        # notices (accepted drift, declared edge realizations) when present.
        findings, _errs = _read_json(
            os.path.join(ws, ".em-review", "findings.json"))
        gate_notices = _dc.design_review_notices(
            (findings or {}).get("meta") or {})
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        kb.record_decision(
            ws, f"Accepted: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}"
                    + (f"\nApproved by: {by}" if by else "")
                    + ("".join("\nNotice: " + n for n in gate_notices)),
            decision="EM review passed and the human signed off — shipped.",
            tags=["accepted", "em-signoff"], context_files=scope,
            links={"loop": "signoff"})
    elif step == "selection":
        return {"error": "the selection gate needs a CHOICE, not a plain "
                         "approve — `loop select <variant|task-id|hybrid>`"}
    else:
        return {"error": f"nothing to approve at step '{step}'"}
    # Commit under the lock with a compare-and-swap on the entry step (v2.3.1):
    # approve() runs seconds of unlocked validation (signoff DoD runs every
    # task's tests, refinement, kb writes); an unlocked save could clobber a
    # concurrent gate() transition (the lost-update class the H2 fix closed in
    # gate()). If the on-disk step advanced while we worked, abort instead.
    with mutate(ws) as locked:
        if locked.get("step") != step:
            return {"error": "the loop advanced concurrently during this "
                             f"approval (was '{step}', now "
                             f"'{locked.get('step')}') — re-run `loop next`",
                    "step": locked.get("step")}
        locked.clear()
        locked.update(state)
    out = {"step": state["step"], "status": status(ws)}
    if refinement:
        out["refinement"] = refinement
    if attestation_warning:
        out["warning"] = attestation_warning
    if gate_notices:
        out["notices"] = gate_notices
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
    with mutate(ws) as locked:                       # v2.3.1: locked commit
        if locked.get("step") != "selection":
            return {"error": "the loop advanced concurrently during selection "
                             f"(now '{locked.get('step')}') — re-run",
                    "step": locked.get("step")}
        locked.clear()
        locked.update(state)
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
    with mutate(ws) as locked:                       # v2.3.1: locked commit
        if locked.get("step") != "escalated":
            return {"error": "the loop advanced concurrently during resolve "
                             f"(now '{locked.get('step')}') — re-run",
                    "step": locked.get("step")}
        locked.clear()
        locked.update(state)
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


def user_summary(ws: str, host: str | None = None) -> dict:
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
    # v2.3.0 (H): budget exhaustion is a HUMAN gate the loop step does not
    # encode — without this, the plain-text surface (primary on Codex/Tag)
    # says "no action required" while the run is blocked waiting on the
    # human to grant more actions. Same detection the rich widget uses:
    # active contract's budget.max_actions vs the live meter.
    budget = None
    budget_blocked = False
    try:
        _contract = tp.load_active(ws)
    except Exception:
        _contract = None
    if _contract and (_contract.get("budget") or {}).get("max_actions"):
        _b_max = int(_contract["budget"]["max_actions"])
        _tid = _contract.get("task_id", "_")
        try:
            with open(os.path.join(tp.tp_dir(ws), "meter.json")) as _f:
                _b_used = int((json.load(_f).get(_tid) or {})
                              .get("actions", 0))
        except (OSError, ValueError, TypeError):
            _b_used = 0
        budget = {"used": _b_used, "max": _b_max,
                  "exhausted": _b_used >= _b_max}
        if budget["exhausted"] and step not in TERMINAL_STEPS and not action:
            action = ("Grant more actions (tp budget --grant N) or clear "
                      "the contract")
            budget_blocked = True
    graph = depgraph.summary(ws)
    # M10 (v2.2.1): host is injectable — ambient env detection is only the
    # default, so the control-plane surface is testable deterministically.
    host = host or ("codex" if os.environ.get("CODEX_HOME")
                    or os.environ.get("CODEX_THREAD_ID") else
                    "claude-tag" if tp.store_env() == "repo" else "claude")
    assurance = ("state-and-evidence enforced; tool interception is cooperative"
                 if host == "claude-tag" else
                 "state, evidence, and tool boundaries mechanically enforced")
    if step == "done":
        headline = f"Complete — {settled}/{len(tasks)} task(s) settled."
    elif budget_blocked:
        headline = ("Blocked — action budget exhausted "
                    f"({budget['used']}/{budget['max']}).")
    elif action:
        headline = f"Decision required — {action}"
    else:
        label = current.get("id") if current else step
        headline = (f"In progress — {settled}/{len(tasks)} task(s) settled; "
                    f"current: {label} ({step}).")
    return {
        **({"budget": budget} if budget else {}),
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


# --- Dashboard v2 (R-0001): rendering is part of the flow — every gate()/
# next_action() refreshes the fragment on disk and points at it.
# ---- shared progress artifacts (v2.0.0) -------------------------------------
# Every gate transition snapshots its decision artifacts into the ACTIVE
# store (team plan: in-repo .taskplane-kb/; personal: the external store).
# Doubles as a context cache. Fail-open: publishing never breaks the loop.

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
                # v2.3.0 (scalability): re-copy the graph snapshot only when
                # its content fingerprint changed — dumping megabytes into a
                # committed store on EVERY transition was pure churn.
                gp = os.path.join(root, "graph.json")
                new_fp = (g.get("meta") or {}).get("content_fingerprint")
                old_fp = None
                if new_fp and os.path.exists(gp):
                    try:
                        with open(gp) as f:
                            old_fp = (json.load(f).get("meta") or {}).get(
                                "content_fingerprint")
                    except (OSError, ValueError):
                        old_fp = None
                if not new_fp or old_fp != new_fp:
                    with open(gp, "w") as f:
                        json.dump(g, f, indent=1)
        with contextlib.suppress(Exception):
            # Late import BY DESIGN: dashboard.py imports loop at module top,
            # so a top-level `import dashboard` here would close an import
            # cycle and break every entry point. DEBT (v2.3.0, noted at the
            # extraction seam): rendering/publishing belongs in the
            # CLI/driver layer (tp.cmd_loop) with evidence validation split
            # into evidence.py — until that extraction, imports of the view
            # from this engine stay local to these two functions.
            import dashboard as _dash
            line = _dash.headline_loop(ws)
            if line:
                p = os.path.join(root, "HEADLINES.md")
                prev = ""
                size = 0
                if os.path.exists(p):
                    # v2.3.0 (scalability): read only the TAIL to find the
                    # last line — HEADLINES.md is append-forever, and a full
                    # read per gate made cumulative reads quadratic.
                    with open(p, "rb") as f:
                        f.seek(0, os.SEEK_END)
                        size = f.tell()
                        f.seek(max(0, size - 8192))
                        tail = f.read().decode("utf-8", "replace")
                    tail_lines = tail.rstrip().splitlines()
                    prev = tail_lines[-1] if tail_lines else ""
                if not prev.endswith(line):        # skip consecutive repeats
                    with open(p, "a") as f:
                        if not prev:
                            f.write(f"# {state.get('goal', 'track')} — "
                                    "progress log\n\n")
                        stamp = _time.strftime("%Y-%m-%d %H:%M UTC",
                                               _time.gmtime())
                        f.write(f"- {stamp} · {line}\n")
                    # Cap the log: keep the header + the last 500 entries.
                    # Amortized — the full-file pass runs only past 256 KiB.
                    if size > 262144:
                        with open(p) as f:
                            all_lines = f.read().splitlines()
                        head = [l for l in all_lines[:2]
                                if l.startswith("#") or not l.strip()]
                        body = [l for l in all_lines[len(head):] if l.strip()]
                        if len(body) > 500:
                            tmp = f"{p}.tmp.{os.getpid()}"
                            with open(tmp, "w") as f:
                                f.write("\n".join(head + body[-500:]) + "\n")
                            os.replace(tmp, p)
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
