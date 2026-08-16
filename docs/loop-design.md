# Loop design — the shipped design

> **Status: SHIPPED.** This is the design taskplane's Evaluate-Loop was built
> to; the open decisions listed at the bottom were resolved as recommended and
> **locked on 2026-07-11** (see `taskplane/loop.py`). The document is kept as
> the design record — the "Open decisions" section now reads as the settled
> answers, not a request for input.

## Why the loop first

Product and Engineering Review are the loop's bookends: Product defines WHAT
must be true and Review judges whether the result is sound. For complex work,
Design now sits before Plan and proposes HOW it should be realized; Build
implements it. Keeping these responsibilities separate prevents a designer or
builder from approving its own assumptions.

## The core principle: the loop runs *through* taskplane

taskplane is not just a per-role guard bolted onto an orchestrator. **taskplane
owns the loop itself** — the state machine, the gate sequencing (DoR before a
step, DoD after), and the single audit trace. The role agents are pluggable
*step workers*; taskplane is the *engine* that drives them, checks them, and
records them. Concretely:

- Every loop step = **activate a contract → run the role → orchestrator runs
  the engine gate → clear → transition.** Risk-bearing execute, fix, evaluate,
  and engineering workers first submit evidence bound to the source state and
  their exact evidence artifacts, so they cannot accept their own completion
  claim or alter it after submission. Product, Design, and Plan return
  artifacts directly to the orchestrator. Design and Plan each have a
  mechanical gate followed by explicit human approval.
- The loop's state lives in one place taskplane manages (the active store's
  `loop.json`),
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
 │  → spec + handoff │   DoR: goal is stated   DoD (mechanical): a non-empty
 └─────────┬─────────┘   spec or attached R-id exists (see PM row note below)
           ▼
 ┌─ DESIGN? ────────┐   contract: read-only toward code; write design/** only
 │ → alternatives,  │   DoR: refined WHAT + current graph
 │   selected HOW,   │   DoD: approvable Design Contract + graph overlay,
 │   graph/contracts │        solution-design evidence, risk/rollout/validation
 └─────────┬─────────┘
           ▼
   HUMAN Design approval       (omitted for simple direct Build)
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
 ┌─ EM REVIEW ──────┐   contract: read-only review (exact run artifact writes)
 │  → matrix + read-  │   DoR: tasks done       DoD: (human) sign-off
 │    out (yours)     │
 └─────────┬─────────┘
           ▼
        HUMAN sign-off ──▶ RETRO + graph true-up ──▶ done
```

Each arrow is contract activate → role return → orchestrator gate → clear,
logged. Execute/fix/evaluate/engineering insert a worker submission bound to a
workspace fingerprint before the orchestrator gate.

## Per-step contracts (the enforced boundaries)

| Step | Role | Contract | May write | DoR (enter) | DoD (exit) |
| --- | --- | --- | --- | --- | --- |
| PM | product-manager | planning | `specs/**`,`docs/**` | goal stated | non-empty spec or attached R-id; when an R-id is attached the PM gate mechanically requires complete functional/acceptance fields plus `security` and `architecture` NFRs, stores the refinement result, and links its context files into the planned graph before Plan |
| DESIGN *(optional/explicit)* | tp-designer | read-only toward code | `design/**` | refined requirement, no blocking questions, current graph baseline | `taskplane.design/v1`: alternatives, selected approach, modules/edges/contracts, bounded depth, graph DoR/DoD, acceptance map, risks/failures/observability/rollout, solution-design PASS, conditional visual; then human approval |
| RETRO | engine-owned, no worker | derived state only | `.taskplane/retro.md`, graph + KB | human sign-off recorded (or aborted run) | one idempotent report records forecast/scope/routing/finding lessons, refreshes and fingerprints the graph, then transitions to `done` |
| PLAN | loop-planner | read-only + allow `plan/**` | `plan/**` | spec + criteria exist | every criterion → task; scope/tests/deps/contracts/new modules/impact policy pass graph DoR |
| EXECUTE | loop-executor | build (per-task scope) | the task's `scope_paths` | deps done; scope+tests+graph policy set | task test passes; diff in scope; fingerprinted submission. Realized graph truth is checked at EVALUATE and finalized before EM. |
| EVALUATE | loop-evaluator | read-only + allow `.eval/**` | `.eval/**` | impl commits exist | PASS/FAIL + evidence per criterion, impacted node, affected requirement, and contract. One bounded host/model `unavailable` result advances with a visible warning and never consumes a product FIX cycle; any actual product/lens failure still enters FIX. |
| FIX | loop-fixer | build (same task scope) | the task's `scope_paths` | a reproducible FAIL | failure fixed + regression + re-verified |
| EM | engineering-manager | read-only review | exact leased paths under the external run root | all tasks PASS + final graph true-up | full lens/graph evidence on the current fingerprint; approved Design module/edge/contract conformance with a zero-drift list when applicable (any recorded drift blocks; human-accepted deviations require explicit `accepted_drift` entries, surfaced at the gate); then human sign-off |

## Artifacts / handoff chain (what each step hands the next)

```
goal ─▶ specs/spec.md + handoff block (requirement deps + named contracts)
      ─▶ design/design.md + design/contract.json  (optional proposed HOW;
                                                    human-approved fingerprint)
      ─▶ plan/plan.md         (tasks: id, scope, tests, deps, criteria,
                               contracts, new_modules, impact_policy)
      ─▶ <task code changes>  (in scope, DoD-verified)
      ─▶ .eval/verdict.json   (per task: PASS/FAIL + evidence)
      ─▶ run/artifacts/       (DoD matrix + engineering-quality read-out)
      ─▶ .taskplane/trace.jsonl   (every gate decision, the whole run)
```

## The loop engine (proposed: taskplane owns it)

Add a small state machine to taskplane so the loop *is* a taskplane feature,
not prose in an agent. Proposed CLI (stdlib, same file family as `tp.py`):

```
tp.py loop init [--design] [--design-only] <spec-or-handoff>
                                      # opt into Design before Plan, or end at
                                      # an approved Design Contract
tp.py loop next                       # advance ONE step: activate the right
                                      # contract, return which role to run +
                                      # its DoR; the agent does the work; then
tp.py loop submit [pass|fail]          # worker requests validation; no transition
tp.py loop gate  [pass|fail]           # orchestrator recomputes evidence and transitions
tp.py loop status                     # where are we, per-task status, cycles
```

The **orchestrator agent** becomes a thin driver: call `loop next` → run the
named role → receive its fingerprinted `loop submit` → call `loop gate` →
repeat, until the EM/human step. All state and
all gates are taskplane's; the agent only supplies the per-step reasoning.
(Alternative: the orchestrator agent holds the state in prose and calls the
existing `tp.py new/ready/dod/clear` per step. Simpler to build, weaker
guarantee. This is decision #1 below.)

## The inputs *you* provide

Per run, the loop needs (some from you, some the PM can derive):

- **The goal / spec** — one or more sentences, or a `specs/spec.md`.
- **Acceptance criteria** — testable statements (PM drafts if you don't).
- **Per-task scope + test command** — `tests` is one command string (for
  example `"python3 -m pytest tests/ -q"`), never a list of files or command
  strings. The Plan DoR rejects an ambiguous shape before approval.
- **`max_fix_cycles`** — how many FIX→EVALUATE rounds before escalating.
- **Human checkpoints** — Design approval when used, plan approval, and final
  sign-off (plus selection/escalation when applicable).
- **Autonomy on FAIL** — auto-fix then escalate, vs stop on first FAIL.

If an approved task configuration is later found invalid, use `tp loop
replan --by "<human>" --reason "<defect>"`. taskPlane archives the frozen
tasks in loop history, returns to Plan, and requires the replacement plan to
pass DoR and receive fresh human approval. Never edit loop state directly.

## v0.1 scope vs later

- **v0.1:** sequential tasks, one fix-loop per task, single audit trace, EM
  human gate at the end.
- **Later (v0.2+):** parallel task dispatch, a plan-approval human gate,
  per-role budget rollups, board escalation on repeated FAIL.

## Design decisions (resolved & locked 2026-07-11)

Each was settled as the **Recommendation** noted below and is what shipped.

1. **Loop engine location.** taskplane owns the state machine (`tp.py loop`
   engine, strongest "runs through taskplane") *or* the orchestrator agent
   drives via prose calling `tp.py new/ready/dod`. **Recommendation: taskplane
   owns it.**
2. **Human checkpoints.** Where does the loop pause for you? (a) only EM at the
   end; (b) also approve the plan before EXECUTE; (c) after every task; (d)
   configurable per run. **Recommendation: configurable; direct Build defaults
   to plan approval + EM, while Design adds its own approval.**
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
- The driver dispatches ONE governed subagent per wave entry using the exact
  `worktree` path emitted by the engine (external managed checkout; legacy
  workspaces use `.tp-work/<id>`), then
  `loop claim <id> --agent-workspace <worktree>` activates *that task's
  contract in that worktree* — the PreToolUse hook enforces each agent
  individually. The harness is per agent, not per fleet.
- Workers commit and report `loop submit pass|fail --task <id>`; EVALUATE may
  instead submit `unavailable` for a proven host/model outage; the
  orchestrator alone runs the matching gate. Built tasks are then
  evaluated (read-only, in their worktree, routed lenses) one by one; on
  evaluate PASS the driver merges `tp/<id>` and removes the worktree.
- All tasks passed → EM synthesis on the merged tree → human sign-off.

Authority note: wave membership is a loop decision; each worker holds
AUTONOMOUS authority only inside its own contract (docs/authority-matrix.md).
