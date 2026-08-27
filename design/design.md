# R-0013 Design — native delivery and terminal truth

Status: **final Design candidate awaiting the human Design gate**. The one
authorized quick, concurrent, Design-only all-26-lens sweep is complete:
10 lenses passed and 16 requested changes. Every finding is dispositioned in
`design/contract.json`; the four original blockers are resolved in this Design.
This is not approval, Plan, Build authorization, merge authority, or a release
claim.

## Outcome

Select a **native-authority adapter quarantine with an atomic evidence
coordinator**:

- Codex remains the sole owner of agent creation, canonical task identity,
  capacity, admission to native slots, parent/child lifecycle, messages,
  follow-ups, interruption, completion/attention transport, and event waits.
- Taskplane remains a planner, contract/gate enforcer, checkout preparer, and
  evidence observer. It emits one deterministic intent set for all ready
  pairwise-disjoint work; it never schedules, reserves, leases concurrency,
  owns a worker lifecycle, persists an execution DAG, or replays work.
- The live duplicate-authority path is removed from native dispatch:
  `loop.wave` no longer calls `_stage_loop_wave_dispatches`, creates per-agent
  `StageLifecycle` children, resumes agent attempts, claims per-agent execution
  roots, persists `_stage_bindings`, or emits `stage_runtime_dispatch`.
  `StageLifecycle` remains only for governed phase/gate/evidence history.
- A Design-only collector validates one quick native result for each of the 26
  catalog lenses. Build, Fix, Evaluate, and execution-time EM use direct Codex
  assignments with `expected_lenses=[]` and create zero Taskplane lens workers.
- Existing Plan topology, direct BUILD-C assignment, event-wait, brief, and
  dispatch telemetry owners are tightened rather than replaced.
- One new exact-SHA finalizer consumes native-usage evidence and a receipt from
  the real pinned/final Git checkout, then logically publishes every terminal
  projection through one immutable bundle and one CAS head. No individual
  projection is terminal authority on its own.
- Normal reconciliation is bounded to the head, its predecessor, and indexed
  missing/dirty projections. It exposes explicit preparing, committing,
  reconciling, complete, and failed operator states; a separate offline audit
  performs full-history verification.

No W31/cold-start, history/tag repair, P1/P2, completed R-0001 replay, R-0011,
push, tag, publication, or external release mutation enters this Design.

## First Design check: Codex-native responsibility inventory

This check precedes the alternatives and selected approach. It is grounded in
the installed Taskplane 2.17.22 native-dispatch reference and committed bytes
at `27ab9fecad3cf3b477e02678f6fa4d9ec721f54e`; the current host advertises 13
total collaboration slots including the root, but that observation is not a
constant or Taskplane policy.

| Capability | Native authority | Taskplane may do | Taskplane must never do | Evidence |
|---|---|---|---|---|
| Spawn and task identity | `collaboration.spawn_agent` creates the native agent id and canonical task path | Emit the exact task name, role marker, role payload, model/effort and bind the observed start to its intent | Create a spawn runner, agent registry, alias/rename layer, or synthetic worker id | installed `skills/tp-go/references/codex-native-dispatch.md:12-24`; pinned `.codex/hooks.json:73-99`; `taskplane/loop.py:4390-4426` |
| Capacity and admission | Codex dynamically owns available native slots | Classify dependencies and disjoint scopes; offer every ready disjoint intent | Encode the observed 13 slots, reserve capacity, tranche by host capacity, admit workers, or queue overflow | native-dispatch reference:25-28; `taskplane/plan_topology.py:1-7,141-221`; `taskplane/tests/test_r0001_parallel_delivery.py:29-56` |
| Parent/child delegation | Codex owns the native agent tree; a child may natively delegate | Preserve workflow parent/predecessor metadata and one brief-to-one-task identity | Create a second agent tree, per-agent stage child, attempt hierarchy, or execution root | native-dispatch reference:12-24; pinned `taskplane/stage_entities.py:1085-1175,1619-1718` shows the conflicting incumbent path |
| Messaging and correction | `send_message`, `followup_task`, and `interrupt_agent` own transport and lifecycle action | State bounded correction/escalation policy and preserve partial evidence | Implement a message queue, auto-replacement, waiver, cancellation scheduler, or replay | native-dispatch reference:29-42 |
| Completion and attention | Codex lifecycle/final-result events are the completion truth | Bind expected intent to observed completion/attention and validate exact membership | Fabricate completion, infer it from Taskplane state, or maintain a worker lifecycle | native-dispatch reference:25-42; pinned `taskplane/command_runtime.py`; `.codex/hooks.json:73-99` |
| Event-driven wait | `collaboration.wait_agent` wakes on native completion or attention | Declare one outstanding set and one long-lived event wait; reissue only after a wake | Poll, schedule time-based model wakes, run an event queue, or replay outstanding work | native-dispatch reference:25-33; pinned `taskplane/build_c.py:124-170`; command-runtime regression selectors |
| Lifecycle and usage telemetry | Codex `SubagentStart`/`SubagentStop` plus provider observations own host facts | Observe, bind, aggregate, redact and enforce the human budgets before a later spawn | Claim lifecycle authority, invent usage, accept null active usage, or reconstruct an execution DAG | pinned `.codex/hooks.json:73-99`; `taskplane/dispatch_telemetry.py:1-54,92-199`; `taskplane/progress.py:209-353` |

### Discovered contradiction on the pinned tip

The existing `plan_topology.classify_plan`, `loop.select_ready_tasks`, native
dispatch intent, BUILD-C worktree preparation, hook screening, contract
activation, checkpoints and gates are valid governance seams. However,
`loop.wave` currently calls `_stage_loop_wave_dispatches`
(`taskplane/loop.py:4943-4948`). For a multi-task ready set that helper calls
`StageLifecycle.split_stage`, creates one child and execution root per agent,
persists `_stage_bindings`, and emits `stage_runtime_dispatch`.
`StageLifecycle._claim_execution_root` calls
`storage.claim_stage_execution_root_for_run`. This is Taskplane-owned
execution-DAG/attempt authority parallel to Codex. Default-off packaging does
not make it harmless; the mode is packaged and has required tests. AC1 cannot
pass while this edge remains live.

The correction is narrow: remove the native-dispatch call edge and its
per-agent stage projections, preserve the existing stage journal for phase,
gate, handoff and evidence history, and refuse any renamed active-root path
from Codex dispatch. Historical stage records remain readable; they are never
used to resume, replay, admit, or identify a native worker.

## Current-state delta

The engine's KB current state is empty, so this Design is bounded to the
amended spec, graph and cited exact-tip sources. The as-built graph baseline is
`a44e2b1c6d3bb737858c1d17e520615fec5a3c0534ec3668c0fb8eb18d88a067`
(45 modules, 155 edges, 562 files; complete, not degraded) scanned at the
pinned SHA. The amended Product spec is SHA-256
`e8d984c54e0900643f68d13d88d87f6f2fe6659ef84956519627a27afa12b3ed`.

Existing useful mechanisms:

- `plan_topology.classify_plan` produces an exhaustive pair map without host
  capacity.
- `loop.select_ready_tasks` projects dependency- and owner-safe readiness.
- `build_c.assign_scopes` prepares registered worktrees and an event wait, but
  currently recomputes topology; it will consume the one sealed ready set.
- `delivery_policy` and `review.collect_expected_set` already define the
  zero-lens and explicit empty-collection primitives.
- `dispatch_telemetry` has exact identities and hard ceilings, but active
  bindings begin with null usage, aggregate tokens omit active bindings, and
  hook ingestion is best-effort/swallowed.
- `wiring_closure` validates selector syntax and AST presence, but is
  hard-coded to R-0001 counts and does not execute production reachability in
  the candidate Git checkout.
- `RepositoryManager.merge_registered_task`, loop/Retro, progress snapshots,
  `views.publish_report`, repository verification and release evidence are
  separate transitions. `publish_report` can fail open and no coordinator
  proves one terminal SHA across them.

## Alternatives

### A. Native-authority adapter quarantine plus exact-SHA evidence coordinator

Keep the existing pure policy owners and direct BUILD-C path. Add three small
owners: `native_authority.py`, `design_sweep.py`, and `terminal_truth.py`;
generalize `wiring_closure.py` to execute real-checkout proofs. Remove the
stage-native agent-dispatch edge and make existing transition modules thin
adapters.

Gains: smallest removal of duplicate authority; no second scheduler; every P0
control has a public entry point and severed-edge test; disjoint leaf work can
fan out before a narrow shared-adapter integration barrier. Costs: new closed
receipts and strict failure modes make previously best-effort telemetry/report
paths blocking for a governed production wave. Revisit when Codex exposes a
new stable native capability requiring one new inventory row, or two new
owners repeatedly co-change and should be collapsed.

The explicit trade is **authority integrity and terminal reliability gained**
for **operability and short-term modifiability spent**. This selected choice is
the proposed decision record `D-R0013-native-adapter-quarantine`; the Design
gate accepts or rejects it, and no worker may accept it.

### B. Keep stage-native mode as a Taskplane scheduler facade

Rename stage splits, execution-root claims and dispatch rows as host adapters,
then teach Taskplane to reserve/admit against Codex capacity.

Gains: reuses the current stage-native tests and per-agent stage history.
Costs: directly violates AC1/P0-3; duplicates native capacity, lifecycle and
execution-DAG authority; makes event replay and recovery ambiguous. Revisit
only if Codex explicitly delegates those authorities through a new human-
approved product contract. It is rejected now.

This alternative spends authority integrity and lifecycle consistency to gain
short-term operability and modifiability, the inverse of the selected trade.

### C. Documentation and CI assertions only

Document the native boundary, retain runtime behavior, and add static
blacklists plus a final CI job.

Gains: few runtime edits and easy rollback. Costs: current name blacklists
already miss the live stage-native path; cannot close null active telemetry,
runtime lens starts, real-checkout reachability, or atomic terminal truth.
Revisit as redundant CI defense after the runtime contracts are implemented.

### D. Status quo

Continue relying on old R-0001 Design claims and operator discipline.

Gains: no implementation. Costs: all four accepted P0 failures remain, and
the Design would be grounded in a false native-authority premise. Revisit:
never while R-0013 is active.

## Selected boundaries and contracts

The selected approach is A. All nine Product contract ids stay canonical.
Taskplane adds no service, dependency, daemon, queue, database, crypto,
credential, or host scheduler. Python remains synchronous and standard-library
only; host event waiting remains outside the engine process.

New owners:

- `taskplane/native_authority.py`: immutable capability/responsibility rows,
  the delivery-root call-edge validator, forbidden-authority semantic rules,
  and exact native-observation binding. It has no spawn/wait implementation.
- `taskplane/design_sweep.py`: validates one Design generation containing one
  quick result for every catalog id, native concurrency observations and one
  disposition per result. It never launches a worker.
- `taskplane/terminal_truth.py`: prepares immutable exact-SHA projections,
  validates all prerequisite receipts, and commits one logical terminal bundle
  by an orchestrator-only, run/SHA/Design/Plan/predecessor-bound finalization
  capability. Workers and candidate-test processes cannot write the authority
  store. Surface adapters resolve terminal status through this bundle.

The graph scanner consumes the accepted decomposition map in
`design/contract.json#/architecture_decomposition`. It realizes separate nodes
for Codex native orchestration, Taskplane governance adapters, native-authority
validation, Design-sweep validation, terminal coordination, the eight terminal
surface producers (including `exports/`), and tests. The strict final scan
compares those nodes and declared edges rather than claiming that the baseline
aggregate `taskplane` node can prove file-owner isolation.

Changed owners:

- `taskplane/wiring_closure.py` becomes requirement-count agnostic and produces
  `taskplane.candidate-checkout-wiring/v1` only after tracked selectors and
  named producer edges execute from a registered Git checkout whose repository
  identity, HEAD and tree match the candidate. A sibling registered Git
  worktree at the same commit may host a one-edge mutation; copied/generated
  test sources and arbitrary temporary directories never qualify.
- `delivery_policy.py`, `review.py`, `evaluation_output.py` and `loop.py`
  remove execution-time lens routing. Direct evaluator/EM output remains
  schema-validated acceptance evidence with an explicit empty-lens receipt.
- `plan_topology.py`, `build_c.py`, `loop.py` and `command_runtime.py` pass one
  sealed ready set to worktree/contract preparation, native intent output and
  one native event wait. `build_c` no longer recomputes readiness.
- `brief_projection.py`, `dispatch_telemetry.py`, `progress.py`, `spend.py` and
  the dispatch screen require non-null finite active observations and enforce
  equality as a human stop. Telemetry failure is no longer swallowed for an
  active production wave.
- `repository.py`, `progress.py`, loop journal/task/gate state,
  `views.publish_report`, repository verification, release evidence and
  `exports/` receive content-addressed projections from the one final bundle.

## Production sequence

1. **Design-only broad signal.** Root Codex dispatches one quick native task
   per catalog lens in available concurrent batches. `design_sweep` validates
   exactly 26 unique results for one Design content fingerprint and records
   dispositions. It rejects serial-all, repeats, full/deep mode and use at any
   non-Design stage.
2. **Disjoint leaf build.** Pure native-authority, Design-sweep, zero-lens,
   topology, budget/handoff, wiring and terminal owners build in disjoint
   scopes. Each leaf publishes a typed readiness receipt. A single integration
   owner starts only after all seven readiness receipts—including AC2—exist,
   then exclusively edits shared `loop.py`/`tp.py` adapters. Its integration
   receipt precedes AC7 end-to-end checkout/finalization acceptance, so AC7 is
   never a prerequisite for its own integration. No two workers write one
   shared adapter.
3. **Native execution.** Taskplane seals the exhaustive pair map and ready set,
   prepares worktrees/contracts, and emits every ready disjoint intent once.
   Codex performs native spawn/admission. The dispatch screen atomically binds
   a non-null usage observation or refuses that native start. One native wait
   covers the accepted outstanding set. It receives an absolute child deadline
   strictly before the eight-hour wave ceiling. Completion or native attention
   is the normal wake; silent transport expiry becomes exactly one attributed
   attention outcome with the outstanding-set fingerprint and usage, stops for
   human scope review, and never polls, reissues, or replaces work.
4. **Feature validation.** Direct evaluator and EM assignments carry zero lens
   expectations. Every criterion runs its exact selectors. The real-checkout
   runner then executes every named selector and edge proof in the pinned and
   final Git checkout; its mutation worktree proves each edge-sensitive
   selector fails when its one production edge is severed.
5. **Atomic finalization.** On a clean candidate SHA, the finalizer prepares
   exactly eight named terminal projections, including the redacted `exports/`
   projection,
   as immutable content-addressed bytes. It validates native usage, real-
   checkout wiring and full-suite receipts, fsyncs the bundle, then CASes one
   terminal head. Readers accept no projection without that head and the full
   digest set. A crash before CAS publishes none; a crash after CAS is repaired
   idempotently from immutable bytes. Final local integration must preserve the
   SHA (fast-forward); any SHA-changing merge requires fresh finalization.

The production state shown to the operator is `preparing`, `committing`,
`reconciling`, `complete`, or `failed`. A failed state preserves the last
trustworthy candidate, immutable bundle bytes, private telemetry, exact failed
boundary, and an idempotent retry action. Only a successful reconciliation
receipt proving all eight digests and the native aggregate permits the
terminal-truth owner to delete or irreversibly minimize private session detail;
cleanup failure preserves detail for retry and emits no cleanup receipt.

## Seven-outcome ownership and parallelism

The Plan must name exactly seven acceptance outcomes (and no more than eight).
Leaf implementation may use a narrow eighth integration task, but it introduces
no acceptance outcome and owns only the shared adapters after the seven leaf
readiness receipts are green. The closed 21-pair matrix in
`design/contract.json` is Plan
authority: pairs are parallel unless it records an evidence dependency or
shared file owner. AC7 validation waits for all prerequisite receipts, while
its pure finalizer/wiring code can be built in the first native wave.

Each of AC1–AC7 retains its single Product outcome and has a closed singular
subassertion list in the contract. An outcome passes only when every listed
subassertion and its exact selector pass; partial success cannot be aggregated
as acceptance. Outcome effectiveness is then observed for the next three
production waves against the R-0001 baseline: zero recurrence of the four P0
failure classes is the target, successful-wave completion must not regress,
and any P0 recurrence forces iteration while completion regression forces a
human keep/iterate/remove decision.

## Failure, observability and rollback

The engine emits only bounded identities and aggregates: source SHA,
requirement/contract/criterion/task/stage ids, content fingerprints, native
dispatch/wait states, finite timestamps/durations and token totals. It stores
no prompts, transcript, diff/source bytes, model-output bodies, secrets,
credentials or personal content. Detailed per-session telemetry stays private
through successful eight-surface reconciliation and is deleted or irreversibly
minimized only after an idempotent cleanup receipt; cleanup or reconciliation
failure preserves it for retry. `exports/` contains only the aggregate terminal
projection.

Budget stops show the measured value beside its ceiling, the preserved
outstanding-set identity, and the available attributed choices: reduce scope
and create a successor wave, end the wave, or return to architecture review.
No choice silently resumes the stopped dispatch set.

Receipt evolution and the Codex boundary are explicit. The contract includes a
producer/consumer compatibility table, predecessor support, deploy order and
retirement condition. The versioned native adapter envelope uses stable error
codes and classifies every conflict or observation failure as retryable and
idempotent, or permanent and human-attention-required.

Missing inventory rows, a forbidden call edge, lens starts, a non-empty lens
expectation, missing native observation, budget equality, false-ready work,
polling, a ninth outcome, a >=4000-token handoff, a foreign checkout, an opaque
wiring fingerprint, a severed producer edge, mixed SHA, nonterminal progress,
partial projections or a CAS fork all fail closed at their named public entry
point. No fallback scheduler, outage resolution or synthetic evidence exists.

Rollback before final integration removes the thin adapter and its new owner
while retaining immutable evidence. Stage journal readers remain compatible;
only the per-agent dispatch projection is retired. After a terminal CAS,
rollback cannot rewrite evidence: a new candidate SHA receives a new bundle
and predecessor link. Legacy reports remain readable but never satisfy R-0013
terminal authority.

## Final evidence state

The Python solution-design reference at SHA-256
`9ad8935fadef92c06bfbd4338750debdd612a8391a54ba0ba026424edf7db4b7`
was applied: synchronous ownership is explicit, validation sits at host/JSON/
Git/persistence boundaries, domain owners are separated from transition
adapters, packaging adds only Python files to both incumbent archives, and no
new dependency or global service locator is introduced. Strict type/import,
package-content, focused, mutation, severed-edge and full-suite checks are
Design DoD inputs.

The evidence directory contains exactly 26 unique lens results bound to
provisional content fingerprint
`40db587023b4f0494800b3494114dd68264a69131a87f53e2ad9dc9aaa26a236`.
Ten source verdicts are pass and sixteen are changes. The four source blockers
(data safety, project management, solution design, and SRE) are resolved by the
safe cleanup boundary, acyclic receipt graph, and bounded native wait described
above. `lens_evidence` records every source path, digest, finding disposition,
and the final Design content fingerprint. No second lens sweep occurred. This
Design does not self-approve; the human Design gate remains mandatory.
