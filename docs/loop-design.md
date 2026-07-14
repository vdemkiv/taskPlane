# Loop design — outline for review

> Draft for you to mark up. The open decisions are at the bottom; answer those
> and I'll build to them. Nothing here is implemented yet.

## Why the loop first

PM and EM only feel real once there's a loop between them: **PM feeds the
loop** (it authors the contract the work runs under) and **EM closes it** (it
reviews the loop's output). So the loop is the connective tissue — building
its skeleton makes PM's output have a consumer and EM's input have a producer.

## The core principle: the loop runs *through* taskplane

taskplane is not just a per-role guard bolted onto an orchestrator. **taskplane
owns the loop itself** — the state machine, the gate sequencing (DoR before a
step, DoD after), and the single audit trace. The role agents are pluggable
*step workers*; taskplane is the *engine* that drives them, checks them, and
records them. Concretely:

- Every loop step = **activate a contract → run the role → run its gate →
  record the verdict to `.taskplane/trace.jsonl` → transition.**
- The loop's state lives in one place taskplane manages (`plan/state.json`),
  and every transition is a taskplane event. There is one audit log for the
  whole run, not per-role scraps.
- A step cannot advance unless its DoR passed; a step cannot be called done
  unless its DoD passed. The loop can't "skip a gate to keep moving."

This is the difference from a prompt-driven orchestrator: the loop is
**mechanically gated**, not honor-system.

## The state machine

```
 goal / spec
     │
     ▼
 ┌─ PM ─────────────┐   contract: planning (write specs/** only)
 │  → spec + handoff │   DoR: goal is stated   DoD: testable acceptance criteria
 └─────────┬─────────┘
           ▼
 ┌─ PLAN ───────────┐   contract: read-only + write plan/**
 │  → plan.md        │   DoR: spec+criteria     DoD: every criterion → ≥1 task,
 │    (tasks w/       │                          each task names its contract
 │     contracts)     │
 └─────────┬─────────┘
           ▼                          ┌───────────── per task (sequential v0.1) ──────────────┐
     for each task ───────────────────▶  EXECUTE  → EVALUATE ──PASS──▶ (next task)             │
                                       │  (build)    (read-only)                                │
                                       │     ▲          │FAIL & cycles<max                      │
                                       │     └── FIX ◀──┘                                       │
                                       │  (same build contract; +regression test)              │
                                       └───────────────────────────────────────────────────────┘
           ▼  (all tasks PASS)
 ┌─ EM REVIEW ──────┐   contract: read-only review (write .em-review/** only)
 │  → matrix + read-  │   DoR: tasks done       DoD: (human) sign-off
 │    out (yours)     │
 └─────────┬─────────┘
           ▼
        HUMAN sign-off ──▶ done
```

Each arrow is a taskplane contract activate → gate → clear, logged.

## Per-step contracts (the enforced boundaries)

| Step | Role | Contract | May write | DoR (enter) | DoD (exit) |
| --- | --- | --- | --- | --- | --- |
| PM | product-manager | planning | `specs/**`,`docs/**` | goal stated | testable acceptance criteria + handoff |
| PLAN | loop-planner | read-only + allow `plan/**` | `plan/**` | spec + criteria exist | every criterion → task; each task has a contract |
| EXECUTE | loop-executor | build (per-task scope) | the task's `scope_paths` | deps done; scope+tests set | task test passes; diff in scope |
| EVALUATE | loop-evaluator | read-only + allow `.eval/**` | `.eval/**` | impl commits exist | PASS/FAIL + evidence per criterion |
| FIX | loop-fixer | build (same task scope) | the task's `scope_paths` | a reproducible FAIL | failure fixed + regression + re-verified |
| EM | engineering-manager | read-only review | `.em-review/**` | all tasks PASS | human sign-off (no auto-verdict) |

## Artifacts / handoff chain (what each step hands the next)

```
goal ─▶ specs/spec.md + handoff block
      ─▶ plan/plan.md         (tasks: id, scope_paths, test_command, deps, criteria refs)
      ─▶ <task code changes>  (in scope, DoD-verified)
      ─▶ .eval/verdict.json   (per task: PASS/FAIL + evidence)
      ─▶ .em-review/          (DoD matrix + engineering-quality read-out)
      ─▶ .taskplane/trace.jsonl   (every gate decision, the whole run)
```

## The loop engine (proposed: taskplane owns it)

Add a small state machine to taskplane so the loop *is* a taskplane feature,
not prose in an agent. Proposed CLI (stdlib, same file family as `tp.py`):

```
tp.py loop init  <spec-or-handoff>   # create plan/state.json, seed the run
tp.py loop next                       # advance ONE step: activate the right
                                      # contract, return which role to run +
                                      # its DoR; the agent does the work; then
tp.py loop gate  [pass|fail]          # run the step's DoD, record it, transition
tp.py loop status                     # where are we, per-task status, cycles
```

The **orchestrator agent** becomes a thin driver: call `loop next` → run the
named role → call `loop gate` → repeat, until the EM/human step. All state and
all gates are taskplane's; the agent only supplies the per-step reasoning.
(Alternative: the orchestrator agent holds the state in prose and calls the
existing `tp.py new/ready/dod/clear` per step. Simpler to build, weaker
guarantee. This is decision #1 below.)

## The inputs *you* provide

Per run, the loop needs (some from you, some the PM can derive):

- **The goal / spec** — one or more sentences, or a `specs/spec.md`.
- **Acceptance criteria** — testable statements (PM drafts if you don't).
- **Per-task scope + test command** — the planner proposes; you can override.
- **`max_fix_cycles`** — how many FIX→EVALUATE rounds before escalating.
- **Human checkpoints** — where the loop pauses for you (see decision #2).
- **Autonomy on FAIL** — auto-fix then escalate, vs stop on first FAIL.

## v0.1 scope vs later

- **v0.1:** sequential tasks, one fix-loop per task, single audit trace, EM
  human gate at the end.
- **Later (v0.2+):** parallel task dispatch, a plan-approval human gate,
  per-role budget rollups, board escalation on repeated FAIL.

## Open decisions — your input, please

1. **Loop engine location.** taskplane owns the state machine (`tp.py loop`
   engine, strongest "runs through taskplane") *or* the orchestrator agent
   drives via prose calling `tp.py new/ready/dod`. **Recommendation: taskplane
   owns it.**
2. **Human checkpoints.** Where does the loop pause for you? (a) only EM at the
   end; (b) also approve the plan before EXECUTE; (c) after every task; (d)
   configurable per run. **Recommendation: (d), default = plan-approval + EM.**
3. **On FAIL.** Auto-fix up to `max_fix_cycles` then escalate to human, or stop
   on the first FAIL and ask? And what default `max_fix_cycles`? **Rec: auto-fix,
   default 2, then escalate.**
4. **Input format.** Do you want to hand the loop a free-text goal (PM turns it
   into the spec), or always author `specs/spec.md` yourself first? **Rec:
   accept both — free-text triggers PM; an existing spec skips PM.**
5. **Task granularity owner.** Planner proposes task scopes/tests and you can
   edit `plan/plan.md` before EXECUTE, yes? **Rec: yes — plan is editable.**

## Parallel execution (waves) — added with the port pass

`loop init --parallel` switches EXECUTE from one-task-at-a-time to **waves**:

- A wave = every pending task whose `deps` have PASSED and whose scope is
  pairwise-disjoint from the rest of the wave (overlapping scopes serialize
  into later waves — two agents never share writable files).
- The driver dispatches ONE governed subagent per wave entry: worktree per
  task (`git worktree add .tp-work/<id> -b tp/<id>`), then
  `loop claim <id> --agent-workspace <worktree>` activates *that task's
  contract in that worktree* — the PreToolUse hook enforces each agent
  individually. The harness is per agent, not per fleet.
- Workers report `loop gate pass|fail --task <id>`; built tasks are then
  evaluated (read-only, in their worktree, routed lenses) one by one; on
  evaluate PASS the driver merges `tp/<id>` and removes the worktree.
- All tasks passed → EM synthesis on the merged tree → human sign-off.

Authority note: wave membership is a loop decision; each worker holds
AUTONOMOUS authority only inside its own contract (docs/authority-matrix.md).
