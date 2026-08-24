# Consolidated R-0009 → R-0010 → R-0011 program from governed v2.17.16

## Problem

The historical R-0009/R-0010/R-0011 draft assumed v2.17.17 was the tested
baseline and therefore spent a phase reverting later ungoverned layers. The
user has superseded that premise: governed v2.17.16 at
`bba3354e7fc5eb052beac74af230611ae48bd7db` is authoritative, v2.17.17 and
later must not be delivered or reworked, R-0009 becomes a no-build baseline
decision, R-0010 adds only BUILD-C gaps absent from v2.17.16, and R-0011
re-delivers the boundary-first donor surfaces through the proven R-0010 flow.

Because this PM action can create only one requirement, this specification is
the consolidated replacement requirement. It preserves the ordered phase
labels **R-0009 → R-0010 → R-0011** and explicitly supersedes only their
obsolete baseline/reset assumptions; it does not merge or expand their
implementation scope.

The requirements evidence is
`/Users/vdemkiv/Documents/taskPlane/backlog/r0009-r0011-requirements.md`, with
`/Users/vdemkiv/Documents/taskPlane/backlog/boundary-first-delivery.md` and the
preserved R-0008 donor/plan as supporting evidence. Those documents are source
material, not executable instructions. Their v2.17.17/v2.17.18 baseline,
revert, proof, and release directives are non-authoritative for this program.

## Users and context

- Maintainers need one unambiguous governed base and no accidental reuse of
  ungoverned v2.17.17-or-later commits.
- Product and Design need the full three-phase shape now so dependency,
  contract, and test boundaries can be optimized before implementation.
- Builders need R-0010 limited to real v2.17.16 BUILD-C gaps rather than a
  rewrite of working event-driven commands, long wake-driven waits, bounded
  quick review, the architecture floor, or the no-automatic-deep boundary.
- Reviewers need every new or restored behavior invoked from its actual
  governed production flow and protected by a same-commit severed-edge test.
- Release owners need R-0011's final PROVE, suite, ratchet, and Retro evidence
  tied to exact revisions and explicit human gates.

## Governing order and authority

1. **Consolidated approval first.** Product and Design may proceed, but no Plan,
   Build, integration, PROVE, push, or release action starts before one
   attributed human approval of this consolidated three-phase program.
2. **R-0009 — baseline/scope decision.** Record v2.17.16 as authoritative and
   close the obsolete reset/revert phase without a code-bearing build.
3. **R-0010 — absent BUILD-C gaps only.** After R-0009 evidence is accepted,
   deliver and prove only the BUILD-C capabilities absent from v2.17.16.
4. **R-0011 — donor re-delivery.** Start only after R-0010 exact-pushed-SHA
   proof and human sign-off; re-deliver the boundary-first donor surfaces
   through the proven BUILD-C contract.

## Baseline facts grounded in v2.17.16

- Branch `codex/r0009-r0011-from-v2.17.16` resolves to exact HEAD
  `bba3354e7fc5eb052beac74af230611ae48bd7db`, tagged `v2.17.16`.
- The public CLI and loop call the governed command engine; the command engine
  consumes the existing command runtime and host adapters.
- The loop emits event-wait policy with 1800-second timeout, 300-second floor,
  wake-only reissue, and `scheduled_polling=false`.
- Automatic review already projects exactly 4 or 5 sweep lenses total and
  requires architecture; the live baseline applies it to review/build stages.
- Automatic deep/adaptive promotion is already refused fail-closed. v2.17.16
  reports the explicit human entry as not shipped; this program preserves the
  human-only authority boundary and does not invent an automatic substitute.
- Existing graph-disjoint wave selection, worktree merge receipts, stage/
  enforcement receipts, findings, and review-lease machinery are reusable
  primitives, but they do not by themselves satisfy the absent BUILD-C flow.

## In scope

### R-0009 — no-build authoritative baseline decision

1. Record exact v2.17.16 commit/tag identity and the supersession of all
   v2.17.17-or-later baseline, reset, revert, and release assumptions.
2. Produce a baseline capability matrix that proves reusable production wiring
   and names the bounded R-0010 gap list; a positive capability cannot enter
   implementation scope.
3. Preserve later commits and donor material only as historical/source
   evidence. Do not reset, revert, cherry-pick, merge, tag, publish, or modify
   product code in R-0009.

### R-0010 — only BUILD-C capabilities absent from v2.17.16

1. Add AC-bound checkpoints whose focused proof file must exist and pass before
   dependent work proceeds.
2. Bind checkpoint evidence to engine-observed command, bounded output, result,
   environment, and exact revision using existing receipt primitives; reject
   caller-authored receipts or producer/result claims.
3. Wire agent submission to checkpoint execution through the already-live
   command-event runtime and wake-driven wait path.
4. Extend the existing 4–5-lens applicability projection to the real DEFINE
   boundary, reusing the existing router and architecture floor.
5. Gate integration on a green checkpoint by consuming existing worktree/
   merge-receipt primitives; red work cannot become a dependent base.
6. Reuse graph/worktree scope analysis for direct graph-disjoint assignment
   with zero default-path claim, build-task lease, or wave state; overlapping
   scopes serialize in dependency order.
7. Deliver R-0010 itself under manual checkpoint discipline because BUILD-C
   cannot bootstrap itself: one AC or approved AC group at a time, no work on
   top of red state.

### R-0011 — boundary-first donor surfaces through R-0010

1. Re-deliver heavy DEFINE, direct thin BUILD through BUILD-C, and durable
   batch PROVE from source evidence rather than donor commit history.
2. Seal Plan new modules, impact prediction, tolerance, contracts, task scopes,
   ACs, graph revision, and compatible identity policy as the reconciliation
   baseline.
3. Re-deliver test-tier generation, the full-suite inventory, schema-registry
   correctness, both-direction impact reconciliation, independent batched AC
   evaluation, the seven-component PROVE battery, four failure-direction
   canaries, bounded fix/re-PROVE, and exact-pushed-SHA proof.
4. Deliver the sealed donor Plan's exact 23-test catalog below, or an
   individually attributed human re-scope recorded before dependent work.
5. Treat donor T09 and any other surface as complete only where actual
   production reachability and current contract conformance are proven; revise
   only missing wiring/conformance and do not rewrite passing behavior.
6. Record the first full BUILD-C Retro with checkpoint, wait, defect, wall-
   clock, token, proof-attempt, and baseline-comparison metrics.

## Out of scope

- Any reset, revert, forward-revert, proof, repair, release, or delivery of
  v2.17.17, v2.17.18, or any later ungoverned layer.
- Product-code changes in R-0009; that phase is a baseline/scope decision only.
- Reimplementation of v2.17.16 governed command execution, command runtime/
  adapters, event-wait policy, review/build 4–5 quick routing, architecture
  floor, automatic-deep refusal, review leases/findings, stage receipts,
  enforcement receipts, graph scanner, or merge-receipt primitives when live
  baseline evidence passes.
- A newly shipped deep-review feature. Deep can only ever be entered by a
  direct attributed human command; v2.17.16's fail-closed unavailable result is
  valid and must not be bypassed by automatic behavior.
- Automatic full, deep, 26-lens, light-all, or serial-fallback review; lens
  dispatch inside AC checkpoints.
- Mutating, rebasing, amending, or cherry-picking donor commits; content may be
  re-delivered only through fresh R-0010 checkpoints after R-0011 begins.
- Deleting the retained governed-build compatibility machinery or its tests;
  quarantine/deletion work remains separately earned.
- Unrelated package/component/schema-platform/MCP/attestation/persistence/
  physical-isolation/orchestration-core redesign.
- Main integration, push, tag, release/marketplace publication, history rewrite,
  tag deletion, force-push, or scope-expanding exception without separate
  attributed human approval.

## Functional requirements

1. **One consolidated authority.** This replacement preserves ordered labels
   R-0009 → R-0010 → R-0011 and supersedes their obsolete v2.17.17+ baseline
   assumptions. No executable stage starts before consolidated human approval,
   and each later phase remains blocked on predecessor proof/sign-off.
2. **R-0009 exact baseline.** The baseline record binds branch, full commit
   `bba3354e7fc5eb052beac74af230611ae48bd7db`, tag `v2.17.16`, tree
   fingerprint, and decision attribution, and explicitly excludes later layers.
3. **R-0009 performs no build.** Closing R-0009 creates only decision and
   evidence artifacts; product/code tree fingerprint remains unchanged, and no
   reset, revert, merge, tag, push, release, or donor mutation is performed.
4. **Reuse is evidence-bound.** The baseline matrix verifies actual CLI/loop
   command-engine reachability, runtime/adapters, wake-driven waits, automatic
   4–5 quick review with architecture, no-automatic-deep enforcement, and
   existing graph/worktree/receipt/review primitives. Passing capabilities are
   reuse-only and cannot be listed as R-0010 implementation tasks.
5. **R-0010 gap catalog is closed.** R-0010 implementation scope contains only:
   AC-bound checkpoint behavior; engine-owned checkpoint receipt binding;
   submit-to-checkpoint production wiring; DEFINE-stage projection using the
   existing router; checkpoint-to-integration authorization; and direct no-
   state graph-disjoint assignment. Any additional gap requires a new human
   scope decision.
6. **Checkpoint completeness.** Each AC checkpoint reports compile/import, the
   declared focused-proof file/command, exact-revision engine receipt,
   forbidden-state counts, ratchet delta, and one bounded Engineering judgment
   over that AC evidence delta. Missing proof refuses by name; no lens is
   dispatched.
7. **Merge and dependency safety.** Only a green exact-revision checkpoint may
   enter the integration branch. Red work and its dependents cannot merge or
   rebase onto it; overlapping scopes serialize and disjoint scopes may run
   concurrently in isolated worktrees.
8. **Baseline behavior remains live.** R-0010 consumes, rather than forks, the
   existing command runtime, event waits, quick router, architecture floor,
   no-automatic-deep boundary, graph identity, worktree, receipt, findings, and
   enforcement contracts. Observable baseline behavior remains unchanged.
9. **Every R-0010 edge is live.** Each new edge has positive invocation from the
   actual governed flow and a same-commit severed-edge mutation test. Static
   source presence, direct unit-only calls, or evaluation-only callers cannot
   satisfy the phase.
10. **R-0011 uses proven BUILD-C.** Every donor-derived piece lands through an
    R-0010 checkpoint with fresh exact-revision receipts; donor commits and
    prior narrative/test claims never become completion evidence.
11. **Boundary-first lifecycle.** DEFINE invokes the real-stage 4–5 quick
    projection; default BUILD is direct, plan-scoped, event-driven, checkpointed,
    graph-disjoint, and merge-on-green with zero wave/per-task-contract/claim/
    build-lease/evaluate-fix/mid-build-lens state; final PROVE is mandatory.
12. **Plan and impact identities agree.** The sealed Plan and PROVE use
    compatible canonical graph/diff identities. Actual-but-unpredicted changes
    outside tolerance fail as blast-radius drift; predicted-but-untouched
    modules fail as structural scope cuts; prose cannot waive either.
13. **Test and schema truth is closed.** Every tracked test is mechanically
    tiered once, thin PROVE runs zero Tier-2, governed-build runs all applicable
    Tier-2, all 23 catalog paths are present/pass or explicitly re-scoped, and
    baseline-existing schemas remain existing while genuinely new ids require a
    registry entry.
14. **Durable PROVE.** One public `tp prove` executes compile/import, generated
    Tier 0, zero-token corpus, cycle/SCC/LOC ratchet, four-canary component,
    impact reconciliation, and the independent batched AC walk, binding one
    durable journal/bundle to Plan, diff, graph, full-suite receipt, repository
    revision, required checks, and exact pushed SHA.
15. **Bounded proof recovery.** A first red PROVE permits one checkpointed batch
    fix and complete re-PROVE. A second red blocks sign-off and returns an
    attributed human action; failed/superseded attempts remain durable.
16. **Every R-0011 behavior is live.** DEFINE projection, mode selection, scope
    screen, tiering, registry, reconciliation, PROVE components/collection,
    CI/ratchets, dashboard, and Retro each require positive production-flow
    evidence and a same-commit severed-edge/mutation test.
17. **Review depth stays bounded.** Automatic DEFINE and post-change review
    dispatch exactly 4 or 5 selected quick lenses total, concurrently, including
    architecture. No automatic full/deep/26-lens/fallback path exists; any deep
    request without a shipped direct-human entry refuses fail-closed.
18. **Measured outcome.** R-0011 Retro binds wall clock, tokens, checkpoints,
    mean checkpoint cost, empty-wait share, defects per checkpoint, PROVE-caught
    defects, attempts, and stop/re-scope events to its exact revision. Empty-
    wait share is below 10%; a run over 24 hours without prior human re-scope
    fails the process criterion.
19. **External actions remain human.** Main integration, push, tagging,
    release/marketplace publication, destructive history action, and any scope-
    expanding exception remain distinct attributed human gates.

## Exact R-0011 23-test catalog

1. `t01-c1-sealed-plan-and-schema-registry`
   - `taskplane/tests/test_r0008_plan_baseline.py`
   - `taskplane/tests/test_r0008_schema_registry.py`
   - `taskplane/tests/test_r0008_wiring_define_baseline.py`
2. `t02-c1-define-review-routing`
   - `taskplane/tests/test_r0008_define_review.py`
   - `taskplane/tests/test_r0008_wiring_define_review.py`
3. `t03-c2-dual-mode-direct-build`
   - `taskplane/tests/test_r0008_delivery_modes.py`
   - `taskplane/tests/test_r0008_direct_build.py`
   - `taskplane/tests/test_r0008_wiring_delivery_modes.py`
4. `t04-c3-test-tier-manifest`
   - `taskplane/tests/test_r0008_test_tiers.py`
   - `taskplane/tests/test_r0008_wiring_test_tiers.py`
5. `t05-c3-impact-and-batch-acceptance`
   - `taskplane/tests/test_r0008_impact_reconciliation.py`
   - `taskplane/tests/test_r0008_batch_acceptance.py`
   - `taskplane/tests/test_r0008_wiring_reconciliation.py`
6. `t06-c3-durable-batch-prove`
   - `taskplane/tests/test_r0008_prove.py`
   - `taskplane/tests/test_r0008_prove_canaries.py`
   - `taskplane/tests/test_r0008_fix_reprove.py`
   - `taskplane/tests/test_r0008_wiring_prove.py`
7. `t07-c4-ci-inventory-and-ratchets`
   - `taskplane/tests/test_r0008_ci_ratchets.py`
   - `taskplane/tests/test_r0008_full_suite_inventory.py`
   - `taskplane/tests/test_r0008_wiring_ci.py`
8. `t08-c4-operations-guidance-and-dogfood`
   - `taskplane/tests/test_r0008_operations.py`
   - `taskplane/tests/test_r0008_dogfood_bundle.py`
   - `taskplane/tests/test_r0008_production_wiring.py`

## Acceptance criteria

1. **Consolidated approval and order.** Before one attributed human approval of
   this consolidated program, live attempts to create Plan/Build/integration/
   PROVE/release execution state refuse. After approval, R-0010 remains blocked
   until R-0009 is accepted, and R-0011 remains blocked until R-0010 exact-
   pushed-SHA proof and human sign-off.
2. **R-0009 records exactly v2.17.16.** A machine check resolves the declared
   branch to `bba3354e7fc5eb052beac74af230611ae48bd7db`, verifies tag `v2.17.16`,
   records the tree fingerprint and attributed scope decision, and refuses any
   v2.17.17-or-later revision as baseline or implementation input.
3. **R-0009 is demonstrably no-build.** Before/after tree fingerprints are
   identical; audit evidence contains only baseline/scope artifacts and shows
   zero code write, reset, revert, merge, donor mutation, tag, push, or release.
   The old reset/revert acceptance set is explicitly superseded, not executed.
4. **Baseline reuse matrix is production-proven.** Live v2.17.16 fixtures prove
   CLI→governed-command-engine and loop→governed-command-engine calls, runtime/
   adapter consumption, wake-driven non-polling waits, exactly 4–5 automatic
   quick lenses including architecture at existing boundaries, and fail-closed
   automatic deep refusal. Existing mutation/flow-edge tests fail when each
   edge is severed; all passing rows are marked reuse-only.
5. **R-0010 cannot grow past the six gaps.** Its approved Plan maps every task
   to exactly one closed-gap category from Functional Requirement 5 and maps
   all baseline-positive capabilities to verification/reuse only. A task that
   reimplements a passing baseline capability or adds a seventh category makes
   readiness fail pending a new human scope decision.
6. **R-0010 checkpoint and receipts run live.** The actual governed submit path
   invokes the existing command runtime and then the AC checkpoint. A missing
   declared proof file refuses by name; a real focused test mints an exact-
   revision engine receipt; caller-authored receipts fail; later phases do not
   run after red. Cutting submit→runtime, submit→checkpoint, or checkpoint→
   receipt validation fails its same-commit mutation test.
7. **R-0010 DEFINE, assignment, and merge gaps close without forks.** A live
   DEFINE transition reuses the current router to emit exactly 4 or 5 concurrent
   quick slots including architecture and no deep slot; live assignment runs
   disjoint scopes concurrently with zero claim/build-lease/wave state and
   serializes overlap; live integration accepts only the green checkpoint SHA.
   Cutting each new edge fails its same-commit mutation test.
8. **R-0010 preserves baseline behavior.** Compatibility fixtures show no
   observable change to existing CLI/loop command events, wait policy, existing
   review/build routing, architecture inclusion, nonhuman deep refusal, graph/
   worktree identities, receipts, findings, leases, or enforcement semantics.
   No baseline-positive implementation is replaced.
9. **R-0010 phase exit is exact.** The full suite, compile/import matrix,
   zero-token corpus, cycle and graph ratchets have no new undispositioned
   failure on R-0010's final SHA; after separately authorized push, fetched-SHA
   proof is green for that exact revision and attributed human sign-off opens
   R-0011.
10. **R-0011 never imports later history.** Every reused donor surface records
    source/content provenance and a fresh R-0010 checkpoint, while ancestry and
    diff evidence show no v2.17.17-or-later commit was merged, cherry-picked,
    or treated as implementation proof.
11. **R-0011 boundary flow is live.** A fresh requirement completes DEFINE →
    thin BUILD-C → PROVE; DEFINE and post-change review each emit exactly 4 or 5
    concurrent quick lenses including architecture, BUILD trace contains only
    permitted R-0010 checkpoint state and zero legacy per-task governance/lens
    state, and final sign-off receives the durable PROVE bundle.
12. **All 23 files are accounted for.** A machine comparison finds exactly the
    catalog above; every path exists and passes on the final SHA or links to an
    individual attributed human re-scope recorded before dependent work.
    Missing, skipped, renamed, duplicate, or silently cut files fail their
    checkpoint and final completeness check.
13. **Registry bootstrap is correct.** Against the frozen 279-existing-schema
    reproduction, all 279 classify as existing and zero as newly unregistered;
    one genuinely new id fails by name until validly registered. Duplicate,
    malformed, stale-fingerprint, or self-unregistered registry data fails.
14. **Tier and full-suite truth is exact.** Import-graph generation classifies
    every tracked test once, rejects Tier-0 reachability to quarantine, shows
    thin PROVE executes zero Tier-2 and governed-build executes all Tier-2, and
    dispositions every full-suite failure relative to the v2.17.16 baseline
    with zero undispositioned new failure.
15. **PROVE binds one identity set.** A public `tp prove` run emits one terminal
    receipt for each seven-component battery part and binds the same sealed
    Plan, diff, graph revision, full-suite receipt, clean repository revision,
    required-check receipt set, and exact pushed SHA. Stale, mixed, missing,
    duplicate, partial, or caller-authored identities fail nonzero.
16. **Impact, ACs, and failure directions fail honestly.** Compatible-identity
    fixtures fail predicted-but-untouched and actual-but-unpredicted modules by
    name; one non-builder pass proves every Plan AC exactly once; syntax-error,
    cycle-edge, corrupted-corpus, and out-of-scope-write canaries each go red
    for the intended cause and green only after reversion.
17. **Proof recovery is bounded and durable.** A first red batch permits one
    checkpointed fix and complete re-PROVE; a second red blocks sign-off and
    emits an attributed human action. Restart/resume preserves all attempts and
    cannot overwrite or reuse a partial green.
18. **T09 and every production behavior are reachability-proven.** Each passes
    through its actual governed entry point with positive evidence, and
    severing the call edge fails a same-commit mutation test. Reachable,
    conformant baseline/donor behavior remains unchanged; dormant or test-only
    code cannot pass.
19. **Final floors and economics are evidence-backed.** On R-0011's final SHA,
    full suite, compile matrix, corpus, cycle/SCC/LOC, graph/impact, registry,
    tier, quick-finding, and collection gates are green; Retro binds all metrics
    from Functional Requirement 18, reports empty waits below 10%, and fails a
    >24-hour run without prior human re-scope. Authorized pushed-SHA proof is
    green for that same revision.
20. **External authority is separate.** Every main integration, push, tag,
    release/marketplace publication, destructive history action, or scope-
    expanding exception actually performed has its own attributed human
    approval; missing approval prevents only that action.

## Non-functional requirements

- `security`: Baseline identity, phase approval, checkpoint receipts, producer
  identity, scope, schema/tier status, review depth, PROVE components, pushed-
  SHA proof, and human sign-off cannot be forged through prose or caller fields;
  refusals leave governed/integration state unchanged and expose no secrets.
- `architecture`: v2.17.16 is the sole baseline truth. The consolidated phase
  ledger has one-way R-0009→R-0010→R-0011 authority; R-0010 consumes existing
  runtime/router/graph/worktree/receipt primitives and adds only six gaps;
  R-0011 consumes R-0010 and PROVE alone emits completion evidence.
- `data-safety`: Baseline fingerprints, decisions, donor evidence, checkpoints,
  receipts, inventories, failed/superseded proof attempts, findings, and human
  actions remain immutable or append-safe; no phase rewrites history, mutates
  donor evidence, erases failures, or partially integrates red work.
- `sre`: Event waits wake on completion, attention, death, or cancellation;
  checkpoints and PROVE are deterministic, bounded, resumable, fail-closed, and
  independently diagnosable by phase, AC, file, edge, component, and revision.
- `integrability`: Baseline, phase, graph, Plan, diff, scope, checkpoint,
  receipt, tier, schema, PROVE, review, CI, Retro, and release records use
  versioned compatible identities and stable machine fields across supported
  hosts without forking existing v2.17.16 contracts.
- `cost-finops`: Passing baseline features are not rebuilt; waits remain event-
  driven; BUILD dispatches no lenses; automatic review uses exactly 4–5
  concurrent quick lenses; each PROVE component runs once per attempt and one
  fix round is allowed; wall-clock/token/wait/checkpoint/defect metrics make
  marginal cost attributable.
- `privacy-compliance`: Evidence retains only repository-relative paths,
  canonical ids, declared actors, bounded outputs, revisions, and necessary
  metrics; credentials, unrelated transcripts, private host configuration,
  secrets, and personal data are excluded or redacted.
- `accessibility`: Baseline and phase decisions, dependency refusals, checkpoint
  state, lens selection, deep unavailability, scope drift, tier/schema status,
  PROVE/AC results, approvals, and Retro metrics render as complete semantic
  text independent of color with equivalent machine-readable values.

Supporting quality statements:

- **Reliability:** passing v2.17.16 behavior cannot be replaced accidentally,
  red or stale checkpoints cannot become a base, and no later-history, missing-
  test, dormant-code, or partial-proof state can appear complete.
- **Verification:** every reuse decision and new behavior requires live governed
  execution plus mutation/flow-wiring evidence; the closed gap and 23-test
  catalogs, canaries, full suite, ratchets, and exact-SHA proof make narrative
  completion insufficient.
- **Diagnosability:** every refusal names the phase, predecessor, baseline,
  scope category, AC/test, canonical identity, edge, receipt, PROVE component,
  revision, or human authority that failed and exposes a bounded recovery action.

## Contract handoff

- `scope_paths`:
  - `.github/workflows/ci.yml`
  - `agents/**`
  - `components.yaml`
  - `docs/**`
  - `exports/**`
  - `plan/**`
  - `scripts/**`
  - `skills/tp-design/**`
  - `skills/tp-go/**`
  - `taskplane/**`
- `out_of_scope`: any v2.17.17+ reset/revert/proof/repair/delivery; code changes
  in R-0009; reimplementation of baseline-positive runtime/wait/router/deep-
  boundary/graph/worktree/receipt/review/enforcement behavior; new deep feature;
  automatic full/deep/26-lens review; checkpoint lens waves; donor commit reuse;
  governed-build deletion; unrelated architecture/security work; and external/
  destructive actions without separate attributed human approval.
- `dod.test_command`: `python3 .taskplane/codex-hook.py prove`
- dependencies: none; this single replacement requirement owns its internal
  ordered phase gates and does not depend on obsolete historical requirement
  records.
- contracts:
  - `contract:program.r0009-r0011-order`
  - `contract:baseline.v2.17.16-authority`
  - `contract:runtime.command-events`
  - `contract:review.routing`
  - `contract:review.depth-boundary`
  - `contract:governance.build-c-checkpoint`
  - `contract:governance.checkpoint-receipt`
  - `contract:orchestration.scope-disjoint-assignment`
  - `contract:orchestration.merge-on-green`
  - `contract:define.design-review`
  - `contract:orchestration.delivery-mode`
  - `contract:plan.reconciliation-baseline`
  - `contract:prove.batch-evidence`
  - `contract:graph.impact-reconciliation`
  - `contract:test.tier-manifest`
  - `contract:schema.registry`
  - `contract:import-cycle-ratchet`
  - `contract:retro.delivery-efficiency`
  - `contract:governance.delivery-authority`
- `contract_relations`:
  - provides `contract:program.r0009-r0011-order`
  - provides `contract:baseline.v2.17.16-authority`
  - consumes `contract:runtime.command-events`
  - consumes `contract:review.routing`
  - consumes `contract:review.depth-boundary`
  - provides `contract:governance.build-c-checkpoint`
  - provides `contract:governance.checkpoint-receipt`
  - provides `contract:orchestration.scope-disjoint-assignment`
  - provides `contract:orchestration.merge-on-green`
  - provides `contract:define.design-review`
  - provides `contract:orchestration.delivery-mode`
  - provides `contract:plan.reconciliation-baseline`
  - provides `contract:prove.batch-evidence`
  - provides `contract:graph.impact-reconciliation`
  - provides `contract:test.tier-manifest`
  - provides `contract:schema.registry`
  - changes `contract:import-cycle-ratchet`
  - provides `contract:retro.delivery-efficiency`
  - changes `contract:governance.delivery-authority`
- context files:
  - `specs/spec.md`
  - `.github/workflows/ci.yml`
  - `components.yaml`
  - `plan/tasks.json`
  - `taskplane/command_runtime.py`
  - `taskplane/command_adapters.py`
  - `taskplane/governed_commands.py`
  - `taskplane/loop.py`
  - `taskplane/tp.py`
  - `taskplane/lens.py`
  - `taskplane/review.py`
  - `taskplane/depgraph.py`
  - `taskplane/repository.py`
  - `taskplane/worktree_cleanup.py`
  - `taskplane/evidence.py`
  - `taskplane/import_cycles.py`
  - `taskplane/dashboard.py`
  - `taskplane/retro.py`
  - `taskplane/tests/test_r0007_governed_commands.py`
  - `taskplane/tests/test_r0007_sweep_bootstrap.py`
  - `taskplane/tests/test_loop.py`
  - `taskplane/tests/test_r0006_pushed_sha.py`
  - `taskplane/prove.py`
  - `taskplane/test_tiers.py`
  - `taskplane/schema_registry.py`
  - `taskplane/schema-registry.json`
  - `taskplane/tests/fixtures/full-suite-inventory.json`
  - `taskplane/tests/test_r0008_*.py`
  - `scripts/ci_evals.py`
  - `scripts/ci_loop_cost.py`
  - `exports/**`

This is a cross-module, contract-changing, sequencing-sensitive replacement
requirement. It requires Design before Plan or Build and one consolidated human
approval before any executable stage. It has no blocking Product questions.
