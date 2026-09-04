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
  artifacts directly to the orchestrator. Product, Design, and Plan each have
  a mechanical gate; their complete evidence is presented together for one
  consolidated pre-implementation authorization.
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
 ┌─ PLAN ───────────┐   contract: read-only + write plan/**
 │  → plan.md        │   DoR: spec+criteria     DoD: every criterion → ≥1 task,
 │    (tasks w/       │                          each task names its contract
 │     contracts)     │
 └─────────┬─────────┘
           ▼
  HUMAN consolidated authorization
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
| DESIGN *(optional/explicit)* | tp-designer | read-only toward code | `design/**` | refined requirement, no blocking questions, current graph baseline | `taskplane.design/v1`: alternatives, selected approach, modules/edges/contracts, bounded depth, graph DoR/DoD, acceptance map, risks/failures/observability/rollout, solution-design PASS, conditional visual; included in the consolidated packet |
| RETRO | engine-owned, no worker | sealed receipts + derived graph state | `.taskplane/retro.md`, graph + KB | human sign-off recorded (or aborted run) | one idempotent report consumes sealed non-cumulative metrics without recounting, records forecast/scope/routing/finding lessons, refreshes and fingerprints the graph, then transitions to `done` |
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

## Repository-native phase continuation

The stateful loop remains the normal lifecycle authority, but a completed or
interrupted Design, Plan, or Build phase can now be continued from repository
evidence alone. The portable contract is the sealed
`taskplane.stage-handoff/v2`; it names the exact repository, source commit and
tree, requirement, applicable Design and Plan fingerprints, ordered
obligations and tasks, contracts, acceptance proofs, human authority, selected
artifact digests, progress receipts, and lineage.

The public surface is intentionally small:

```text
tp.py phase export --request <repository-relative-json>
tp.py phase pickup <repository-relative-handoff>
tp.py phase submit --request <repository-relative-json>
tp.py phase resume <repository-relative-handoff>
```

`phase export` accepts Design or Plan `material`, `phase`, `outcome`,
`durable_progress`, and optional `receipt_evidence`, then calls the same
exporter used by normal loop completion. Build export is refused on this
public surface: only `phase submit` can carry BUILD-C evidence into a Build
handoff. `phase pickup` admits done requirement-to-Design,
Design-to-Plan, and Plan-to-Build transitions. `phase resume` admits only an
interrupted Design, Plan, or Build handoff whose successor is the same phase.
Both create fresh attempt-local authority after validation; they never reopen
a predecessor attempt. Their public `startup` field projects the validated
startup rather than discarding it. For Design and Plan it contains the phase
projection and each worker's identity, output, producer contract, scoped view,
closed result schema, and full-envelope reference. For Build it contains the
exact task, producer contract, scoped view, closed result schema, and
full-envelope reference. Attempt leases and contract bootstraps stay private.
The projected startup retains the existing 128-KiB startup ceiling.
`phase submit` accepts exactly a repository-relative `handoff` and the exact
`task_id` returned in the safe public startup. It derives the assignment and
authoring evidence from the clean committed Git diff, then uses the existing
BUILD-C checkpoint and repository-integration boundary. A canonical green
progress receipt is automatically carried into the next Build handoff. When
all obligations are green that handoff is terminal; otherwise it is an
interrupted same-phase resume exposing only the next eligible task. The result
returns the next handoff's identity and repository-relative path, never an
assignment, lease, bootstrap, or caller-authored evidence.
Keep the request JSON in repository-relative ignored metadata (for example,
`.git/phase-submit.json`) so it does not dirty the committed Build checkout.

Publication creates repository export files but does not commit them. Commit
`exports/pickup` before sharing or cloning so a fresh clone can validate and
continue the returned handoff path.

Initial, Design, and Plan authority comes only from attributable
`human:<identity>` decisions bound to the exact gate subject and source.
Mechanical progress identifies an engine producer and never manufactures a
human actor. Content fingerprints provide integrity, not actor
authentication. Build receives only the first dependency-ready sealed task,
its exact write scope, contracts, acceptance references, and proof commands.

Validation fails before effects in this order: bounded JSON and closed schema;
canonical identity, ordering, uniqueness, and limits; repository/source and
clean-checkout lineage; selected artifacts; progress receipts; human
authority; phase transition; then obligation, task, dependency, scope,
contract, acceptance, and proof closure. Public JSON contains stable status
and refusal codes, repository-safe identities, lineage and receipt
fingerprints, counts, and safe recovery. It does not print private roots,
artifact locators, loop/run/track/claim state, leases, conversations, secrets,
or absolute host paths.

Recovery is non-widening: restore the exact canonical handoff or
digest-addressed artifact, use the recorded clean source, resume from the sole
verified receipt head, return to the real human gate for the exact subject,
use the sealed task, or restore the sealed proof. Never overwrite a
same-identity conflict, select an ambiguous receipt fork, bypass BUILD-C,
apply a trust override, synthesize approval, or broaden scope. Refusal codes
are `handoff-malformed`, `handoff-integrity`, `repository-foreign`,
`source-stale`, `checkout-dirty`, `artifact-integrity`, `receipt-lineage`,
`authority-missing`, `authority-stale`, `transition-invalid`,
`scope-widened`, `dependency-unmet`, `proof-invalid`, and
`publication-conflict`; Build submission may also report `authoring-invalid`,
`build-c-unavailable`, or `build-c-failed`.

Canonical UTF-8 JSON and create-if-absent publication make identical semantic
exports byte-stable and exact replay idempotent. A conflicting artifact at the
same identity is refused rather than replaced. Retain published handoffs,
digest-addressed artifacts, and progress receipts while successors or audits
cite them. Rollback stops new v2 production but does not rewrite or downgrade
retained evidence.

The legacy `tp.py pickup <approved-design>` route remains schema-disjoint and
unchanged, including its v1/v2 receipts, `--trust-source` behavior,
repository-only resume, cold start, collisions, interrupted-write recovery,
and refusal ordering. The `phase` route never auto-upgrades or downgrades a
legacy artifact and accepts no trust override.

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
tp.py loop submit [pass|fail|unavailable]
                                      # worker requests validation; no transition;
                                      # `unavailable` is EVALUATE-only (see below)
tp.py loop gate  [pass|fail]           # orchestrator recomputes evidence and transitions
tp.py loop status                     # where are we, per-task status, cycles
```

`unavailable` is not a third product verdict and does not mean that evidence
passed. Only an EVALUATE worker may submit it, and only for a bounded host/model
outage that prevents that evaluator from running. The orchestrator records one
visible warning and advances without consuming a product FIX cycle. A product,
contract, test, or lens failure must be submitted as `fail` and follows the
normal FIX path; `unavailable` cannot turn such a failure into progress.

The **orchestrator agent** becomes a thin driver: call `loop next` → run the
named role → receive its fingerprinted `loop submit` → call `loop gate` →
repeat, until the EM/human step. All state and
all gates are taskplane's; the agent only supplies the per-step reasoning.
(Alternative: the orchestrator agent holds the state in prose and calls the
existing `tp.py new/ready/dod/clear` per step. Simpler to build, weaker
guarantee. This is decision #1 below.)

## Stage-native lifecycle and legacy-track compatibility

R-0004 makes the singleton loop record a compatibility input rather than the
authority for migrated runs. Each Product, Design, Plan, Build, Evaluate,
Engineering, Retro, or extension stage has a stable `taskplane.stage/v1`
aggregate, its own execution root, immutable indexed revisions, explicit
lineage, and at most one terminal outcome. The run manifest commits stage
heads, lineage, operation receipts, and a rebuildable active-stage projection
as one revision. The foreground stage is an explicit projection choice; an
adapter must never guess it from the first active stage or from a track name.

The handoff between stages is the versioned bounded manifest plus explicitly
selected verified artifact references. Starting or resuming a stage creates a
fresh execution tree from those inputs. Prior conversations, agents, events,
tool transcripts, leases, process state, worktrees, and unselected artifacts
do not become successor context. Terminal history is not reopened to continue
work; continuation creates a successor stage.

### Conservative migration transaction

Migration of an existing `loop.json`/track collection is deliberately
one-way, non-destructive, and idempotent:

#### Accepted decision `D-LOOP-STAGE-MIGRATION`

- **Status / owner:** ACCEPTED; the taskplane loop engine owns the authority
  conversion and its receipt.
- **Selected authority model:** retain the byte-exact legacy source, then make
  one atomic, receipt-verified, one-way switch to the stage manifest. Before
  that commit the legacy singleton is authoritative; after it the verified
  stage-manifest revision is authoritative. There is no dual-authority window.

| Alternative | What it buys | What it spends / disposition |
| --- | --- | --- |
| **A. Retained-source, receipt-verified one-way conversion (selected)** | One authority at every instant, deterministic replay, and complete audit/rollback evidence | Gives up automatic reverse migration; recovery restores retained evidence or creates a successor revision rather than rewriting history |
| **B. Bidirectional dual-write with reverse migration** | Older and newer clients can both mutate their native representations | Rejected: partial writes create split-brain authority and a reverse projection cannot faithfully classify ambiguous legacy state |
| **C. In-place singleton schema upgrade** | Smallest apparent storage change | Rejected: destroys byte-exact source evidence and makes crash recovery depend on heuristically completing a partial conversion |

**Revisit trigger:** reconsider reverse export or dual-write only when at least
two supported production consumers require reverse export *and* a conformance
suite proves a lossless round trip for every stable stage/manifest revision,
including every `taskplane.legacy-unknown/v1` sentinel, under an authority-epoch
protocol that prevents split brain. Both conditions are required. Until then,
the one-way receipt boundary remains authoritative; convenience or a single
legacy client is not a revisit trigger.

- First retain the exact bytes of the live singleton, registry, archived track
  records, and their governed requirement/task/decision/evidence/commit/review
  and audit references as content-addressed migration evidence. Parsed JSON is
  not a substitute for byte-exact retention.
- Create deterministic stage objects only where legacy identity, lifecycle,
  and evidence are unambiguous. Ambiguity is represented by an immutable
  `taskplane.legacy-unknown/v1` sentinel with a source fingerprint, retained
  references, and explicit `unknown_reason`; it is never guessed as `pending`,
  `done`, `closed`, or `discarded` and is not default successor input.
- Commit the stage index, lineage, projection, source fingerprints,
  conservation report, and migration receipt in one run-manifest revision.
  The conservation report must account for every discovered source and record
  exactly once. A partial or mismatched projection fails without switching
  authority.
- Replaying the same operation and source returns the prior receipt. Reusing
  its operation id for different bytes or parameters is rejected. Before the
  atomic commit, the old singleton remains authoritative; after it, recovery
  verifies the receipt rather than repeating or heuristically completing the
  conversion.

The legacy `track.py` path has an explicit authority boundary. With no
verified migration receipt, existing behavior does not change: switching a
track moves the live `loop.json`, closing an active track archives it, and the
common loop lock prevents interleaved engine mutations. Only after the receipt
verifies its source and result fingerprints and its conservation report does
the adapter read the v4 foreground projection. In that mode it may render
legacy-shaped status, but it is read-only: it cannot move/restore singleton
files as stage authority, overwrite a head, reopen or reclassify a terminal
stage, or choose an ambiguous foreground. New writes use stage lifecycle
commands. The retained singleton bytes and unknown sentinels remain immutable
and available for audit and rollback; no lossy reverse migration exists.

## The inputs *you* provide

Per run, the loop needs (some from you, some the PM can derive):

- **The goal / spec** — one or more sentences, or a `specs/spec.md`.
- **Acceptance criteria** — testable statements (PM drafts if you don't).
- **Per-task scope + test command** — `tests` is one command string (for
  example `"python3 -m pytest tests/ -q"`), never a list of files or command
  strings. The Plan DoR rejects an ambiguous shape before approval.
- **`max_fix_cycles`** — how many FIX→EVALUATE rounds before escalating.
- **Human checkpoints** — consolidated pre-implementation authorization and
  final sign-off (plus selection, material authority change, exhausted
  recovery, or destructive/external action when applicable).
- **Autonomy on FAIL** — auto-fix then escalate, vs stop on first FAIL.

If an approved task configuration is later found invalid, use `tp loop
replan --by "<human>" --reason "<defect>"`. taskPlane archives the frozen
tasks in loop history, returns to Plan, and requires the replacement plan to
pass DoR and receive fresh consolidated authorization. Never edit loop state
directly.

## v0.1 scope vs later

- **v0.1:** sequential tasks, one fix-loop per task, single audit trace, EM
  human gate at the end.
- **Later (v0.2+):** parallel task dispatch,
  per-role budget rollups, board escalation on repeated FAIL.

## Decision registry (resolved & locked 2026-07-11)

Each was settled as the **Recommendation** noted below and is what shipped.

| Decision ID | Status | Accepted authority / decision | Implementation authority |
| --- | --- | --- | --- |
| `D-LOOP-ENGINE-OWNERSHIP/v1` | ACTIVE | taskplane owns governed state and gates; the host orchestrator owns native worker lifecycle within emitted authority | Complete versioned record below |
| `D-LOOP-HUMAN-CHECKPOINTS` | SUPERSEDED by R-0001 | one consolidated pre-implementation authorization plus EM sign-off | loop gate policy |
| `D-LOOP-FAIL-POLICY` | ACCEPTED | auto-fix at most two cycles by default, then escalate | loop cycle policy |
| `D-LOOP-INPUT-FORMAT` | ACCEPTED | accept free text through PM or an existing specification | loop initialization |
| `D-LOOP-TASK-GRANULARITY` | ACCEPTED | the planner proposes task boundaries and the human may edit the plan before authorization | Plan gate |
| `D-LOOP-STAGE-MIGRATION` | ACCEPTED | byte-retaining, receipt-verified, one-way authority conversion | stage manifest migration transaction above |

### Decision record: `D-LOOP-ENGINE-OWNERSHIP/v1`

The fenced record is the retrievable authority for this decision; the table
above is only its human index.

```json
{
  "schema": "taskplane.decision/v1",
  "id": "D-LOOP-ENGINE-OWNERSHIP",
  "version": 1,
  "status": "ACTIVE",
  "owner": "taskplane-loop-engine",
  "affected_module_globs": [
    "taskplane/loop*.py",
    "taskplane/tp.py",
    "taskplane/native_authority.py"
  ],
  "provenance": {
    "requirement_ids": ["R-0002"],
    "finding_ids": ["M-28"],
    "sources": [
      "docs/loop-design.md",
      "design/contract.json#/finding_map/M-28"
    ]
  },
  "selected_alternative": "A-host-orchestrator-lifecycle",
  "authority_owners": {
    "governed_state_transitions_gates_and_audit": "taskplane-loop-engine",
    "native_worker_dispatch_start_stop_and_wait": "host-orchestrator"
  },
  "alternatives": [
    {
      "id": "A-host-orchestrator-lifecycle",
      "disposition": "SELECTED",
      "decision": "Taskplane owns governed state, transitions, DoR/DoD gates, and the audit trace; the host orchestrator owns native worker dispatch, SubagentStart/SubagentStop lifecycle, and event-driven waits within Taskplane-emitted authority.",
      "qualities_gained": [
        "one fail-closed governance authority",
        "host-native identity and parallel lifecycle",
        "no duplicate embedded scheduler"
      ],
      "qualities_spent": [
        "availability depends on versioned host lifecycle receipts",
        "the engine cannot itself create, cancel, or wait for native workers"
      ]
    },
    {
      "id": "B-engine-owned-worker-scheduler",
      "disposition": "REJECTED",
      "decision": "Taskplane owns both governed state and an embedded cross-host worker scheduler.",
      "qualities_gained": [
        "one component controls governance and worker lifecycle",
        "engine-local cancellation and retry"
      ],
      "qualities_spent": [
        "duplicates host-native scheduling and identity",
        "adds cross-host process and credential authority to the engine"
      ]
    },
    {
      "id": "C-prose-orchestrator-owns-loop",
      "disposition": "REJECTED",
      "decision": "The host orchestrator keeps loop state in prose and calls isolated ready, DoD, and clear primitives.",
      "qualities_gained": [
        "fewest engine commands",
        "maximum host-specific flexibility"
      ],
      "qualities_spent": [
        "no single mechanical transition authority",
        "self-approval and audit divergence become possible"
      ]
    }
  ],
  "revisit_trigger": {
    "subject": "stable host-native lifecycle contract",
    "minimum_consecutive_minor_releases": 2,
    "minimum_governed_dispatches": 100,
    "required_start_stop_receipt_pairing_percent": 100,
    "required_exact_checkout_run_binding_percent": 100,
    "maximum_orphaned_worker_identities": 0,
    "maximum_poll_based_waits": 0,
    "action": "A superseding decision may reconsider the ownership split only after every supported host meets every threshold."
  },
  "lineage": {
    "supersedes": ["D-LOOP-ENGINE-OWNERSHIP/prose-2026-07-11"],
    "superseded_by": null,
    "narrows": "engine authority excludes host-native worker lifecycle"
  }
}
```

The ownership record is operational, not honorary: changing state, deciding a
transition, evaluating a DoR/DoD gate, or appending the authoritative audit
event belongs to the engine. An orchestrator may request those operations but
cannot replace them with prose state or self-approve their result. Moving any
of those responsibilities out of `taskplane/loop.py` requires a superseding
accepted decision; a wrapper or new host adapter alone is not a revisit
trigger.

1. **Loop engine location.** `D-LOOP-ENGINE-OWNERSHIP/v1` selects taskplane for
   governed state and gate authority while the host orchestrator owns native
   worker lifecycle within emitted authority. The rejected prose-orchestrator
   and embedded-scheduler alternatives, costs, and revisit trigger are in the
   ACTIVE record above.
2. **Human checkpoints.** Where does the loop pause for you? (a) only EM at the
   end; (b) also approve the plan before EXECUTE; (c) after every task; (d)
   configurable per run. **Superseded by R-0001: one consolidated
   pre-implementation authorization + EM, with optional Design evidence folded
   into that packet.**
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
  evaluated (read-only, in their worktree, routed lenses) one by one. On
  evaluate PASS the orchestrator resolves and merges the exact registered
  task branch, durably records `taskplane.task-merge/v1`, and only then asks
  the cleanup kernel to revalidate and remove the linked worktree. Cleanup
  never uses force or deletes the branch. Dirty, active, failed, variant,
  locked, identity-mismatched, or evidence-needed trees remain registered and
  visible. `tp.py worktree-cleanup replay` performs one receipt-scoped crash
  recovery pass; it never scans arbitrary worktrees.
- All tasks passed → EM synthesis on the merged tree → human sign-off.

Authority note: wave membership is a loop decision; each worker holds
AUTONOMOUS authority only inside its own contract (docs/authority-matrix.md).
