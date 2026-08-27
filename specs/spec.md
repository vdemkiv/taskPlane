# R-0013 — first corrective wave: native delivery and terminal truth

## Product authority

This specification is the Product authority for the first corrective wave
derived from the accepted R-0001 two-wave retrospective. The retrospective at
`/private/tmp/taskplane-r0001-two-wave-retro-20260826/RETROSPECTIVE.md` is
evidence inventory only; its P0 register at lines 212–215 is adopted exactly
as described below.

This wave is bound to source revision
`27ab9fecad3cf3b477e02678f6fa4d9ec721f54e`. It contains exactly seven
acceptance criteria. It does not replay completed R-0001 feature or release
work and does not authorize Design, Plan, implementation, gates, merge, push,
tag, publication, or release through Product authoring.

## Problem

R-0001 ultimately produced green code, but its delivery process permitted four
P0 failures: terminal sources disagreed after delivery, native usage telemetry
did not enforce accepted budgets, Taskplane duplicated authority already owned
by Codex, and generated substitutes allowed wiring checks to pass without
proving the real production checkout. The next governed production Build must
not begin until these four controls are explicit, testable delivery
requirements.

## Users and outcomes

- A human authorizing a governed wave receives a small acceptance boundary and
  a hard stop before cost, authority, or scope expands.
- Codex remains the sole owner of native agent spawning, concurrency,
  capacity, lifecycle, completion, attention, and event transport.
- Taskplane describes requirements, contracts, dependencies, disjoint scopes,
  gates, checkpoints, and evidence without acquiring host execution authority.
- Design receives one bounded broad review signal, while Build, Fix, Evaluate,
  and execution-time EM run with zero Taskplane lens workers.
- Operators and automation receive one atomic, exact-SHA terminal truth backed
  by production-reachable wiring rather than generated substitutes.

## Adopted P0 register

1. **P0-1 — atomic exact-SHA finalization** is enforced by AC7. Progress, the
   run journal, reports, verification and release evidence must terminalize on
   one exact SHA or merge/push is refused; no retained run may remain
   `executing` after delivery reaches main.
2. **P0-2 — native budget telemetry and enforcement** is enforced by AC6.
   Native wall time, unique sessions, total and uncached tokens enter the
   governed run, `observed_tokens` is never null for an active production
   wave, and a threshold breach cannot silently continue.
3. **P0-3 — host-native dispatch invariant** is enforced by AC1 and AC4.
   Codex owns concurrency. Taskplane may express dependency and disjoint-scope
   intent but may not add scheduling, reservation, admission, capacity,
   lifecycle, replay, lease-concurrency, or execution-DAG authority; static
   and behavioral seams must detect duplication.
4. **P0-4 — real-checkout wiring closure** is enforced by AC7. Named selectors,
   receipts, and producer edges are executed in the actual pinned and final
   candidate checkout. Generated temporary substitutes are prohibited;
   opaque or foreign wiring fingerprints grant no terminal or release
   authority; severing any named edge makes the exact production test fail.

## Functional requirements

1. Design begins with an evidence-backed inventory of Codex-native dispatch,
   concurrency, capacity, lifecycle, completion, attention, and event-wait
   capabilities. Every proposed delivery responsibility maps to native reuse,
   retained Taskplane governance, or an evidenced gap.
2. Design prohibits duplicate Taskplane authority or machinery. Taskplane may
   express dependency, disjoint-scope, gate, claim, checkpoint, and evidence
   intent but does not own host scheduling, capacity, reservation, admission,
   lease concurrency, worker lifecycle, replay queues, or an execution DAG.
3. Design runs exactly one quick concurrent all-26-lens sweep and dispositions
   every result as Design input. No automatic full, deep, serial-all, or
   repeated all-lens sweep is authorized.
4. Build, Fix, Evaluate, and execution-time EM start zero Taskplane lens
   workers. An empty expected-lens set is an explicit valid success state;
   direct native Codex evaluator and EM assignments are not lenses.
5. Every ready pairwise-disjoint unit across governed stages is offered
   together for native Codex parallel dispatch. Overlap serializes only for a
   named dependency or shared owner, and one event-driven wait follows the
   exact outstanding set without scheduled or repeated model polling.
6. This wave is limited to the seven acceptance criteria below and no governed
   wave may exceed eight. Every criterion pair is classified parallel or
   serialized for an evidenced dependency or shared owner, and independent
   criteria dispatch in parallel.
7. Every stage transition uses a delta handoff below 4,000 tokens containing
   exact SHA, requirement, active contracts, acceptance scope, new evidence,
   unresolved decisions, outstanding set, and observed usage. The wave stops
   before 8 hours, 60 unique sessions, 150M total tokens, or 25M uncached
   input tokens.
8. Completion atomically reconciles Git, governed progress, the run journal,
   task and gate state, public and repository verification reports, release
   evidence, and repository-resident terminal evidence to one exact candidate
   SHA and one terminal disposition.
9. Design and terminal gates validate every named selector, receipt, and
   producer edge in the actual pinned and final candidate checkout. Generated
   temporary substitutes are prohibited, opaque or foreign wiring
   fingerprints grant no authority, and severing a named edge makes the exact
   production test fail.
10. The accepted retrospective remains evidence inventory only. Completed
    R-0001 feature/release work, W31 and cold-start work, history/tag repair,
    P1/P2 follow-ups, R-0011, unrelated R-0013 backlog, and external release
    mutation are excluded.

## Acceptance criteria

1. **AC1 — Design proves native capability reuse and refuses duplicate
   authority.** Before Plan authorization, Design contains the complete
   evidenced Codex-native capability inventory and responsibility map. The
   Design and architecture gate refuse Taskplane-owned scheduling, capacity,
   reservation, admission, replay, lease-concurrency, worker-lifecycle, or
   execution-DAG authority. Removing a native mapping or introducing duplicate
   authority makes both the static and behavioral seam proofs fail and blocks
   Design or Plan before Build.
2. **AC2 — the only all-lens sweep is quick, concurrent, and Design-only.**
   Design trace contains exactly one quick result for each of all 26 registered
   lenses, independent lens work is concurrent, and every result is
   dispositioned. A missing result, serial or repeated all-26 pass, automatic
   full/deep sweep, or any all-lens worker outside Design blocks completion.
3. **AC3 — execution starts zero Taskplane lens workers.** Real traces and the
   native session ledger show zero Taskplane lens-worker starts in Build, Fix,
   Evaluate, and execution-time EM, and a valid empty expected-lens collection
   succeeds without outage handling. Any attempted start, non-empty
   expectation, malformed empty result, or outage fallback fails before
   dispatch or gate success.
4. **AC4 — independent work is native-parallel and waits are event-driven.**
   At every governed stage, each ready pairwise-disjoint task or criterion
   appears exactly once in one deterministic native Codex dispatch set,
   overlaps are held only for recorded dependency or ownership, and one
   event-driven wait wakes on completion or attention. Severed readiness,
   dispatch, completion, or wait wiring fails closed without replacement
   Taskplane scheduling state.
5. **AC5 — the wave ceiling and parallel-criterion rule are enforced.** The
   approved Plan contains exactly these seven acceptance outcomes and never
   more than eight, classifies every pair as parallel or serialized with a
   named dependency or shared owner, and places every independent pair in the
   same available native wave. A ninth outcome, missing classification,
   unexplained serial edge, or false-disjoint overlap blocks Plan or dispatch.
6. **AC6 — handoffs and operating budgets are bounded by observed native
   truth.** Each stage receives a delta handoff strictly below 4,000 tokens
   with all required identities and observed usage and no inherited full
   transcript. Throughout an active production wave, native wall-time and
   unique-session telemetry and `observed_tokens` are present, finite, and
   never null. Before every dispatch the wave is below 8 hours, 60 unique
   sessions, 150M total tokens, and 25M uncached input; equality, a breach, or
   missing data stops for human scope review and cannot silently continue. A
   second failed fix/evaluate cycle requires a human architecture or scope
   decision.
7. **AC7 — atomic exact-SHA terminal truth includes real-checkout wiring
   closure.** On the final clean candidate, atomic reconciliation proves Git
   HEAD, governed progress, run journal, tasks and gates, public report,
   repository verification report, release evidence, and `exports/` terminal
   evidence all name the identical full SHA, terminal status, requirement, and
   evidence fingerprints. Every named selector, receipt, and producer edge is
   validated and executed in the actual pinned and final candidate checkout;
   generated temporary substitutes are prohibited, and opaque or foreign
   wiring fingerprints cannot authorize terminal or release truth. Severing
   any named edge makes the exact production test fail. Finalization publishes
   all terminal projections for the exact SHA or none; stale, `executing`,
   missing, mixed, contradictory, severed-edge, or partial truth blocks Done,
   merge, push, and release claims. Retry converges without overwriting prior
   immutable evidence, and no retained run remains `executing` after delivery
   reaches main.

## Non-functional requirements

- **security:** Codex retains exclusive host execution authority. Workers
  receive no scheduler, capacity, reservation, lease, replay, credential,
  push, tag, publish, or irreversible authority. Duplicate or undeclared
  capability fails closed before dispatch.
- **architecture:** Design starts from verified Codex-native capabilities and
  Taskplane remains an intent, contract, gate, and evidence layer. No renamed
  or direct Taskplane scheduler, admission system, capacity model, lifecycle
  manager, replay queue, lease-concurrency layer, or execution DAG is allowed.
- **data-safety:** Terminal truth and usage evidence is exact-SHA bound,
  immutable where historical, retry-safe, and rejects stale, mixed, partial,
  forked, foreign, opaque, or contradictory updates while preserving prior
  committed evidence bytes.
- **sre:** Normal progress is event-driven with no scheduled polling. Missing
  or non-finite telemetry, unobserved outstanding work, budget equality, or
  terminal-source disagreement stops safely and identifies the unsatisfied
  boundary.
- **integrability:** Native Codex dispatch and existing Taskplane governance
  entry points remain the only active production path. Named selectors,
  receipts, producer edges, severed-edge proofs, and the final suite run
  against the real candidate checkout, never synthesized substitutes; legacy
  behavior outside this wave remains green.
- **cost-finops:** Every stage handoff is below 4,000 tokens and observed root,
  worker, and internal-helper usage stops new dispatch before 8 hours, 60
  unique sessions, 150M total tokens, or 25M uncached input.
- **privacy-compliance:** Telemetry is limited to delivery-control metadata
  required by AC6 and AC7: exact SHA and non-content fingerprints,
  requirement/contract/acceptance/task/stage identifiers, dispatch/wait and
  terminal state, timestamps and durations, session/thread type, and aggregate
  token counts. It must not capture source or diff bytes, prompts,
  transcripts, model-output bodies, secrets or credentials, or personal-
  content fields. Unexpected or free-text content is redacted or refused
  before persistence or export. Detailed per-session telemetry remains only
  in the private runtime until AC7 terminal reconciliation completes and is
  then deleted or irreversibly minimized; repository `exports/` retains only
  the redacted aggregate exact-SHA terminal projection required by AC7, never
  a raw-session archive.

## In scope

- Product definition and subsequent Design/Plan boundaries for the seven
  acceptance criteria above and only the four adopted P0 controls.
- Existing delivery-policy, loop transition, direct-scope assignment, review,
  evaluation, EM, progress, usage, event-wait, reporting, and terminal evidence
  surfaces only as Design-inspected candidates.
- Real-checkout selector, receipt, producer-edge, focused, mutation, and
  severed-edge proof plus the final Taskplane test suite at the exact candidate
  SHA.
- A redacted repository-resident exact-SHA terminal projection under
  `exports/`.

## Out of scope

- W31 live-host/cold-start work, historical tag or release repair, pushed-SHA
  release closure, and all other P1/P2 retrospective follow-ups.
- Replay or expansion of completed R-0001 feature and release work, R-0011,
  unrelated R-0013 backlog, or another product delivery.
- A Taskplane scheduler, reservation/admission service, host capacity model,
  worker manager, execution DAG, replay queue, lease-concurrency layer, or
  equivalent renamed authority.
- Execution-time Taskplane lens workers, automatic full/deep/all-lens sweeps,
  repeated Design sweeps, or serial all-26 review.
- Push, tag, publication, marketplace upload, release, credential use, or
  `origin/main` mutation.
- Implementation during Product, Design, or Plan authoring and unrelated CI,
  import-cycle, release-tag, package, manifest, README, or CHANGELOG changes.

## Contract handoff

### Active canonical boundary ids and relations

```yaml
contracts:
  - id: contract:design.codex-native-capability-inventory
    relation: provides
  - id: contract:design.quick-concurrent-all-lens-sweep
    relation: changes
  - id: contract:delivery.execution-zero-lens
    relation: changes
  - id: contract:delivery.codex-native-dispatch
    relation: changes
  - id: contract:delivery.event-driven-wait
    relation: changes
  - id: contract:delivery.acceptance-wave-ceiling
    relation: changes
  - id: contract:delivery.bounded-stage-handoff
    relation: changes
  - id: contract:delivery.exact-sha-terminal-truth
    relation: changes
  - id: resource:exports.exact-sha-terminal-truth
    relation: provides
```

### Context files

```yaml
context_files:
  - specs/spec.md
  - .taskplane/codex-hook.py
  - skills/tp-go/**
  - lenses/**
  - taskplane/loop.py
  - taskplane/build_c.py
  - taskplane/review.py
  - taskplane/evaluation_output.py
  - taskplane/progress.py
  - taskplane/spend.py
  - taskplane/command_runtime.py
  - taskplane/retro.py
  - taskplane/tp.py
  - taskplane/tests/test_loop.py
  - taskplane/tests/test_command_adapters.py
  - taskplane/tests/test_command_runtime.py
  - taskplane/tests/test_v101_fixes.py
  - docs/**
  - exports/**
```

### Definition of Done

```yaml
dod:
  acceptance_count: 7
  adopted_p0_controls: 4
  design_only_lens_sweep: quick-concurrent-all-26
  execution_taskplane_lens_workers: 0
  native_dispatch_owner: Codex
  wait_mode: event
  real_checkout_wiring: required
  terminal_identity: atomic-exact-candidate-SHA
  test_command: python3 -m pytest taskplane/tests -q
```

## Open questions

None. Absence of a native capability or a real-checkout wiring proof is a
fail-closed Design finding, not permission to invent duplicate Taskplane
authority or accept a generated substitute.
