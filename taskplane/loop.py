"""The Evaluate-Loop engine — owned by taskplane.

taskplane owns the loop state machine, sequences the DoR/DoD gates, activates
each step's contract (so the PreToolUse hook enforces it), and records every
transition to `.taskplane/trace.jsonl`. The role agents are pluggable step
workers: the engine tells the driver which role to run and under which
contract; the driver runs it and reports the outcome back via `gate`.

State machine (per docs/loop-design.md, answers locked 2026-07-11):
  init → (pm if free-text goal, else plan)
  pm      → plan
  plan    → plan_approval (human) → execute
  execute → evaluate
  evaluate: pass → next task, or → em when all tasks pass
            fail → fix (if fix_cycles < max) else escalated (human)
  fix     → evaluate
  em      → signoff (human) → done
  escalated → (human) retry | skip | abort

Human gates: plan-approval and EM sign-off. On FAIL: auto-fix up to
max_fix_cycles (default 2), then escalate. Goal input: free-text (→pm) or an
existing spec (→plan).
"""

from __future__ import annotations

import contextlib
import json
import os

import depgraph
import kb
import lens as lens_router
import requirements as reqs
import taskplane_lite as tp

LOOP_FILE = "loop.json"


def _state_dir(ws: str) -> str:
    """Loop coordination state — lives in the external per-project store
    (taskplane_lite.kb_root), NOT the repo, so it never gets committed with
    the code. A teammate continues the loop via the shared store, not git."""
    return os.path.join(tp.kb_root(ws), "state")

# Per-step contract recipes. Non-build steps are read-only with a write-allow
# so they can only touch their own artifact dir; build steps get a real scope.
# pm and em are two deliberate personas (split in v0.8.0): tp-product owns
# the requirement; tp-engineering owns the final all-lens review.
STEP_ROLE = {
    "pm": "tp-product",
    "plan": "tp-planner",
    "execute": "tp-executor",
    "evaluate": "tp-evaluator",
    "fix": "tp-fixer",
    "em": "tp-engineering",
}
HUMAN_STEPS = {"plan_approval", "selection", "signoff", "escalated",
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
    ("pm", "PM"), ("plan", "Plan"), ("plan_approval", "Approve"),
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
    rail = [(s, lbl, s in HUMAN_STEPS) for s, lbl in PIPELINE]
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
         requirement_id: str | None = None, parallel: bool = False) -> dict:
    checkpoints = list(checkpoints if checkpoints is not None else
                       ["plan", "em"])
    state = {
        "goal": goal,
        "parallel": bool(parallel),
        "requirement_id": requirement_id,
        "spec_path": spec_path,
        "max_fix_cycles": int(max_fix_cycles),
        "checkpoints": checkpoints,
        "step": "plan" if spec_path else "pm",
        "tasks": None,
        "current_task": 0,
    }
    save(ws, state)
    tp.trace(ws, "loop_init", goal=goal, spec_path=spec_path,
             first_step=state["step"], max_fix_cycles=max_fix_cycles,
             checkpoints=checkpoints)
    return state


# --------------------------------------------------------------- contracts

def _step_contract(step: str, state: dict) -> dict:
    task = _current_task(state)
    if step == "pm":
        return tp.build_contract(
            f"PM: {state['goal']}", read_only=True,
            write_allow=["specs/**", "docs/**"],
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
            "lenses, acceptance criteria); (4) it COMMITS its work in the worktree (`git add -A && git commit`) and reports `tp.py loop gate "
            "pass|fail --task <id>`. When the wave empties, `loop next` "
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
        tp.activate(agent_ws, contract)
        dor_ready, blockers, warnings = tp.dor_check(
            contract, agent_ws, tp.snapshot_ref(agent_ws))
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

    contract = _step_contract(step, state)
    tp.activate(act_ws, contract)
    dor_ready, blockers, warnings = tp.dor_check(
        contract, act_ws, tp.snapshot_ref(act_ws))
    tp.trace(ws, "loop_step", step=step, role=STEP_ROLE[step],
             task=(_current_task(state) or {}).get("id"),
             dor_ready=dor_ready, dor_blockers=blockers,
             dor_warnings=warnings)

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
        if changed and depgraph.load(ws)["modules"]:
            imp = depgraph.impact(ws, changed)
            # Product side of the blast radius: which OTHER requirements'
            # surface this diff touches (their criteria may need re-checking)
            # and which requirements depend on the affected ones.
            prod = depgraph.product_impact(ws, changed)
            own = (task or {}).get("req") or state.get("requirement_id")
            own = depgraph._req_node(own) if own else None
            imp["affected_requirements"] = [
                r for r in prod["affected_requirements"] if r != own]
            imp["dependent_requirements"] = prod["dependent_requirements"]
            tp.trace(ws, "graph_impact", step=step,
                     touched=imp["touched"],
                     impacted=imp["total_impacted"],
                     affected_reqs=imp["affected_requirements"])

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
                      "context": kb.render_context(recalled)},
        "lenses": routing["lenses"] if routing else None,
        "impact": imp and {**imp, "context": depgraph.render_context(imp)},
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
              "testable acceptance criteria + a contract handoff. Then "
              "`loop gate pass`.",
        "plan": "Run the tp-planner role: write plan/tasks.json (machine) "
                "and plan/plan.md (human) — tasks with scope, tests, "
                "criteria. Then `loop gate pass`.",
        "execute": f"Run the tp-executor on task {t and t['id']}: build "
                   "under this contract (TDD), honoring the PRIMED lenses "
                   "(see `lenses`) and the requirement's acceptance criteria "
                   "(see `requirement`). Then `loop gate pass` (or `fail` "
                   "if you couldn't build it).",
        "evaluate": f"Run the tp-evaluator (read-only) on task "
                    f"{t and t['id']}: run its tests + acceptance criteria, "
                    "then apply each ROUTED lens (see `lenses`; prompt at "
                    "lenses/<id>.md) — inline ones yourself, one governed "
                    "read-only subagent per subagent-mode lens. Write "
                    ".eval/verdict.json. Then `loop gate pass|fail`.",
        "fix": f"Run the tp-fixer on task {t and t['id']}: repair the "
               "listed failures + add a regression test. Then `loop gate "
               "pass`.",
        "em": "Run tp-engineering (read-only): the `lenses` list is "
              "the FULL catalog — run tier=deep lenses at full depth (their "
              "mode says inline vs subagent) and every tier=sweep lens as a "
              "quick pass (its top checks against the diff; flag or clear). "
              "Synthesize all verdicts + requirement-vs-implementation into "
              ".em-review/report.md, record the verdict to the knowledge "
              "base, then `loop approve` for human sign-off.",
    }[step]


def gate(ws: str, outcome: str, note: str = "", task_id: str | None = None) -> dict:
    """Record the current step's outcome, transition, and clear its contract."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state["step"]

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
            tp.clear(t.get("workspace") or ws)
            t["status"] = "built"
            if outcome != "pass":
                t["_build_failed"] = True
            tp.trace(ws, "loop_gate", step=step, task=task_id, outcome=outcome,
                     note=note)
            running = [x["id"] for x in locked["tasks"]
                       if x.get("status") == "running"]
        return {"step": "execute", "task": task_id, "built": True,
                "still_running": running, "status": status(ws)}

    tp.clear(ws)
    tp.trace(ws, "loop_gate", step=step, outcome=outcome, note=note)

    if step == "pm":
        state["step"] = "plan"
    elif step == "plan":
        _load_tasks(ws, state)
        # Fail closed: the plan step ADVANCES only on an explicit `pass` with
        # a real plan on disk. A `fail`/`reject` outcome (human or evaluator
        # rejected the plan) keeps the loop AT `plan` for a retry — advancing
        # a failed plan would strand the loop at `execute` with no current
        # task, and the next `loop next` would crash dereferencing it.
        if outcome != "pass":
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "plan rejected — staying at plan")
            return {"error": "plan gate: outcome was not 'pass' — the plan "
                             "was rejected. Revise plan/tasks.json (+ "
                             "plan/plan.md) and gate again; the loop stays at "
                             "the plan step.",
                    "step": "plan", "status": status(ws)}
        # Fail closed on a phantom plan: a planner REPORTING a plan is
        # nothing — the gate believes only plan/tasks.json on disk. This
        # is exactly the failure the ungoverned control run produced
        # (planner claimed files it never wrote; downstream trusted it).
        if not state["tasks"]:
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note="phantom plan: plan/tasks.json missing or empty")
            return {"error": "plan gate: plan/tasks.json is missing or has "
                             "no tasks — the plan exists only as words. "
                             "Write plan/tasks.json (+ plan/plan.md for the "
                             "human), then gate again."}
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
        # TRUE-UP the product graph before sign-off: replace each
        # requirement's planned links with edges to what the build ACTUALLY
        # changed, then rescan so new modules/edges from the new code are
        # in the graph. The next contract and review start from reality.
        _true_up_graph(ws, state)
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
        imp = depgraph.impact(ws, mods) if \
            depgraph.load(ws)["modules"] else None
        prod = depgraph.product_impact(ws, mods)
        own = depgraph._req_node(rid) if rid else None
        shared = [r for r in prod["affected_requirements"] if r != own]
        t["blast"] = {
            "modules": mods,
            "impacted": imp["total_impacted"] if imp else 0,
            "shared_with": shared,
            "dependent_requirements": prod["dependent_requirements"],
        }
        if shared:
            tp.trace(ws, "graph_shared_surface", task=t["id"],
                     requirement=rid, shared_with=shared)


def _true_up_graph(ws: str, state: dict) -> None:
    """EM-gate graph work: realizes edges from the actual diff + rescan."""
    changed = [f for f in _diff_files(ws, state.get("baseline") or "HEAD")
               if not f.startswith(lens_router.LOOP_OWNED)]
    if not changed:
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
    try:
        depgraph.scan(ws)
    except Exception:
        pass   # a scan failure must never block the gate
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


def approve(ws: str, force: bool = False) -> dict:
    """Pass a human checkpoint (plan-approval or EM sign-off)."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state["step"]
    refinement = None
    if step == "plan_approval":
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
        tp.trace(ws, "loop_approve", gate="plan")
        # High-signal decision → the knowledge base.
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        kb.record_decision(
            ws, f"Plan approved: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}",
            decision=f"Approved a {len(state.get('tasks') or [])}-task plan.",
            tags=["plan-approval"], context_files=scope,
            links={"loop": "plan"})
    elif step == "signoff":
        state["step"] = "done"
        tp.trace(ws, "loop_approve", gate="em_signoff", final="done")
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        kb.record_decision(
            ws, f"Accepted: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}",
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
    if state.get("selection"):
        out["selection"] = state["selection"]
    return out


# --- Dashboard v2 (R-0001): rendering is part of the flow, not a separate
# call. Every successful gate()/next_action() refreshes the fragment on disk
# and points at it in the payload — the driver renders what's already there.
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
        except Exception:
            pass
        return out
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    wrapped.__wrapped__ = fn
    return wrapped


gate = _with_dashboard(gate)
next_action = _with_dashboard(next_action)
