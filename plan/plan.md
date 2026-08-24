# R-0012 Plan — compatibility-first checkpoint spine

## Outcome and fixed authority

Deliver the approved consolidated R-0009 → R-0010 → R-0011 program from the governed v2.17.16 baseline without importing, repairing, reverting, or releasing any v2.17.17-or-later history.

The authoritative branch is codex/r0009-r0011-from-v2.17.16 at bba3354e7fc5eb052beac74af230611ae48bd7db, tagged v2.17.16. The approved Design fingerprint is 0f5214f141c6b6d258813ab22a8f1a19b3f1052abdf773c191d10029606ee794 and its solution-design evidence fingerprint is 6b9191831fd83e12c8e04d8488a663a9a17a53c1f82813aa567e4f3b98d89288. The exact pre-Design baseline graph at 45eed52ff331db28ff229ba8f72761732564fc54 has content fingerprint a377a00651e46b5c4a28154edc043ee2dbaaf77cbfe82d2b29fde01585f9414d, scan-quality fingerprint d128a3e7aeae7fc468e79c186e4407ce52ce8b66f6e21024f75c7bcf6b33e73a, 92 modules, 296 edges, and 523 files.

The one bounded impact query returned 28 impacted modules through the approved radius and zero unknown modules. Every task copies the typed graph policy unchanged: local depth 3, contract-only boundaries, contract depth 1, and requirement depth 1.

The 20 tasks collectively cover all 62 approved modules, all 50 canonical Design edges exactly once, all 19 exact requirement contract IDs, and all 20 unique acceptance-map criteria verbatim. Every task has an explicit non-empty criterion set. T08a–T08e deliberately repeat the closest applicable R-0012 criteria already covered by their original owners so their evaluators receive bounded task-local obligations instead of falling back to all 20 criteria; T08f retains aggregate phase-exit ownership. Every designed module is declared through the engine-recognized new_modules field, including an exact task-local declaration whenever a scoped new surface is owned. No task invents a relation-prefixed contract ID or a seventh R-0010 gap. The T08 repair tasks carry the already-implemented R-0010 source and green focused evidence forward; they repair or recollect only the failed phase-exit surfaces and do not discard or duplicate the six delivered gaps.

## Governing boundaries

R-0009 is evidence-only. It records exact v2.17.16 identity, the attributed consolidated decision, the closed reuse matrix, and the unchanged product-tree fingerprint. It writes no product or test code and performs no reset, revert, merge, donor mutation, tag, push, release, or publication.

R-0010 implements exactly six absent capabilities:

1. ac-bound-checkpoints
2. engine-minted-checkpoint-receipts
3. submit-to-checkpoint-wiring
4. define-stage-projection
5. direct-no-state-graph-disjoint-assignment
6. checkpoint-to-integration-authorization

Each code-bearing R-0010 task has exactly one gap_category field. Existing command runtime/adapters, event waits, 4–5-lens router and architecture floor, automatic-deep refusal, graph/worktree/repository/merge receipts, findings, review leases, enforcement, and T09 are verification/reuse-only. A task that replaces one of those passing capabilities or adds a seventh category is Design drift and requires a new human scope decision.

R-0011 remains closed until R-0010 has an authorized fetched exact-SHA proof and attributed human sign-off. Donor content is source provenance only: every delivered surface is a fresh change with a fresh R-0010 checkpoint. No v2.17.17-or-later commit may be merged, cherry-picked, or treated as proof.

Main integration, push, tag, release/marketplace publication, destructive history actions, and scope-expanding exceptions are not executable Build tasks. Each remains a separate attributed human gate. The verification tasks may validate supplied receipts but never perform those actions.

## Dependency-ordered waves

### Wave 0 — R-0009 no-build closure

1. t01-r0009-v21716-authority writes only the baseline and append-safe program ledger. It binds the branch, full commit, tag target, tree fingerprint, consolidated approval, superseded later-history premise, reuse-only capabilities, and the closed six-gap catalog. Its command proves the five protected product/tool roots remain byte-identical to bba3354e.

No R-0010 task may start until the R-0009 record is accepted. There is no parallel work in this wave because any executable preparation before the no-build authority is accepted would violate phase order.

### Waves 1–6 — manual R-0010 bootstrap

R-0010 must bootstrap under manual checkpoint discipline: one gap at a time, no dependent work on red state, and no use of the incomplete spine to prove itself.

2. t02-r0010-ac-checkpoint creates the tracked-proof preflight and ordered AC checkpoint phases. It also makes the approved Plan reject missing, duplicate, or non-closed gap_category values.
3. t03-r0010-engine-receipts binds checkpoint success to engine-observed runtime events, bounded output, sanitized environment, result, exact worktree revision, predecessor receipts, and repository scope. Caller-authored producer/result/receipt fields fail.
4. t04-r0010-submit-checkpoint-wiring connects the actual governed submit path to the incumbent command engine and then to checkpoint validation. The existing event lifecycle remains the only launch/wait path; completion, attention, error, cancellation, and death wake once with no scheduled polling.
5. t05-r0010-define-projection adds program-phase authority and calls the incumbent review router at the real DEFINE approval boundary. It emits one concurrent set of exactly four or five quick lenses including architecture, uses one event wait, and creates no automatic full/deep/26-lens/serial-fallback slot.
6. t06-r0010-direct-assignment derives ready, pairwise graph-disjoint scopes from the sealed Plan and existing depgraph identities. Disjoint tasks receive isolated registered worktrees concurrently; overlapping or ambiguous scopes serialize. The direct receipt contains zero wave, claim, build-task lease, or per-task lens state.
7. t07-r0010-merge-on-green authorizes integration only when a green checkpoint receipt names the registered worktree tip, matching scope, and green predecessors. Red or stale work and dependents remain isolated and never become a rebase base.

These six tasks are deliberately serialized. checkpoint.py overlaps between the first two; loop.py and governed_commands.py overlap at submit wiring; build_c.py and loop.py are revisited for DEFINE, assignment, and integration. Parallel execution across those shared owners would create unverifiable bootstrap order.

Every task contains the positive production invocation and its severed-edge mutation in the same bounded scope. Checkpoints dispatch zero lenses. Existing quick review remains exactly four or five concurrent quick lenses including architecture; automatic full, deep, 26-lens, light-all, or serial fallback is forbidden.

### Wave 7 — bounded R-0010 recovery and human predecessor gate

The failed t08 receipt at exact SHA 803cf6c49b074c731bffa9f4ae85ce9b011f7039 is immutable input to this replan: 106 tests failed while 4048 passed, CPython 3.10 proof was absent, the `pytest && corpus` command skipped the corpus leg, graph evidence was stale/incomplete, and the selected architecture, DevOps, frontend, integrability, and security quick-lens producers were not active. The zero-file fix attempt is not implementation evidence. Recovery is split so each owner can change only the confirmed surface and the terminal check never hides one leg behind another.

Criteria are intentionally task-local within that split. T08a carries the exact baseline-live, DEFINE, and production-reachability criteria for producer provenance, bounded quick routing, one safe event wait, automatic-depth refusal, and severed-edge behavior. T08b carries the exact compatibility criterion. T08c, T08d, and T08e each carry the exact R-0010 phase-exit criterion but are judged only on their scoped corpus/sequencing, graph/cycle, and compile/import-receipt clauses respectively; T08f owns the same criterion as the terminal aggregate and cannot go green until every clause and separate human boundary is satisfied.

8. t08a-r0010-review-producer-bootstrap runs first and alone. It carries the existing DEFINE implementation, repairs only its live orchestration/activation wiring, and establishes an exact-engine receipt before the first automatic evaluation in the recovery wave. That receipt must activate the same five selected quick producers—architecture, DevOps, frontend, integrability, and security—concurrently as one set, collect them through one event-driven wait, and show no automatic full, deep, 26-lens, light-all, or serial fallback. Its focused positive flow and severed-edge mutations cover loop→BUILD-C→incumbent review→incumbent lens routing in the same bounded commit. `review.py` and `lens.py` remain reuse-only targets; the task may not replace their selector, architecture floor, or depth policy.

After that live bootstrap is proven, t08b and t08c may run concurrently because their write scopes are disjoint:

- t08b-r0010-changed-surface-regression-repair owns only the R-0010 changed production/test surfaces: `build_c.py`, `checkpoint.py`, `loop.py`, and the three focused R-0007 regression files. It consumes a complete exact-v2.17.16 suite receipt plus the immutable 106-failure receipt, classifies every failure by actual dependency, repairs only regressions caused by those changed surfaces, and preserves baseline-positive command, wait, review, graph/worktree, receipt, findings, lease, and enforcement behavior. Every repaired live edge retains a positive invocation and same-commit severed-edge mutation. A failure requiring any other product surface is not silently dispositioned; it stops for a new approved scope decision.
- t08c-r0010-corpus-sequencing owns the CI→zero-token-corpus path and its wiring proof. The corpus is its own executable leg, so it always receives a receipt independent of full-suite status. Severing CI invocation or substituting a non-empty credential environment makes the focused mutation proof fail.

When their respective predecessors finish, t08d and t08e may also run concurrently:

- t08d-r0010-graph-ratchet-refresh follows the changed-surface repair because both own `test_loop.py`. It performs a fresh strict graph scan and bounded impact over the R-0010 changed surfaces, records complete cycle/graph-ratchet disposition at the exact revision, and rejects stale, degraded, unknown-root, or impact-incomplete evidence. Any production call-edge correction must land with its positive flow and severed-edge mutation in this same task.
- t08e-r0010-python-matrix-receipts follows corpus wiring because both own CI compatibility surfaces. It handles and validates engine/CI-minted CPython 3.10, 3.11, 3.12, and 3.13 compile/import receipts for one revision. The absent local 3.10 interpreter cannot be waived, simulated as green, or replaced by a caller assertion; without a real 3.10 receipt the task stays red. It adds no runtime dependency and performs no push.

Finally, t08f-r0010-phase-exit depends on all repair/evidence branches. Its single command runs the full suite, corpus, graph scan/impact, and receipt checks as independent status-accumulating legs, so a red suite cannot prevent corpus or graph evidence from being collected. It must disposition all 106 prior failures against the exact v2.17.16 baseline, validate the complete Python matrix and five-producer receipt, and leave zero new undispositioned suite/cycle/graph failure on one exact final SHA. It performs no push. After local green, the orchestrator must stop for separate push authority; only an externally supplied fetched-SHA receipt for the identical revision plus attributed R-0010 sign-off opens R-0011.

A red or receipt-incomplete R-0010 keeps direct mode disabled and R-0011 closed. Existing governed-build remains the intact compatibility path.

### Wave 8 — R-0011 Plan and registry

9. t09-r0011-plan-and-schema seals Plan modules, predicted impact, tolerance, contracts, task scopes, AC identities, graph revision, and compatible graph/diff identity rules. The registry check classifies the frozen 279 baseline IDs as existing, rejects falsely new, duplicate, malformed, stale, or self-unregistered rows, and requires explicit registration only for genuinely new IDs.

### Waves 9–10 — live DEFINE and thin BUILD-C

10. t11-r0011-delivery-modes-and-provenance combines catalog groups 2 and 3 because both revisit build_c.py and loop.py. It re-delivers the real DEFINE and post-change review behavior through the proven R-0010 boundary, with exactly four or five concurrent quick lenses including architecture and no automatic deep/full fallback. It then re-delivers default thin BUILD-C plus retained governed-build compatibility: event waits, graph-disjoint assignments, exact checkpoints, scope refusal, merge-on-green, and zero legacy per-task governance or lens state. Every donor-derived row records path/content provenance and a fresh checkpoint. One command runs all five exact catalog paths from both groups.

The combined task serializes the shared owners internally; splitting those groups into parallel tasks would create overlapping write scopes.

### Wave 11 — safe parallel R-0011 proof owners

After t11, two scope-disjoint tasks may run concurrently:

- t12-r0011-test-tier-manifest owns test_tiers.py and its two focused tests. It classifies every tracked test exactly once, rejects Tier-0 quarantine reachability, requires thin Tier-2 count zero, and requires all applicable Tier-2 tests in governed-build.
- t13-r0011-reconciliation-and-batch-ac owns prove.py reconciliation, batched acceptance, and four-canary composition with its four focused tests while consuming the conformant depgraph and evidence owners unchanged. It rejects both impact-difference directions in the same canonical namespace, accepts exactly one non-builder evidence row per Plan criterion, and proves each syntax, cycle, corpus, and scope canary goes red for the intended cause and green only after reversion.

Their production and focused-test scopes do not overlap. No other R-0011 tasks are claimed parallel.

### Wave 12 — durable seven-component PROVE

14. t14-r0011-durable-prove joins the completed owners behind the public tp prove command. Each append-only attempt produces exactly seven terminal receipts:

1. supported CPython compile/import
2. generated Tier 0
3. credential-empty zero-token corpus
4. cycle/SCC/LOC ratchet
5. four-canary red/revert/green component
6. graph/diff impact reconciliation
7. independent non-builder batched AC walk

The bundle binds one Plan, diff, graph revision, full-suite receipt, clean repository revision, required-check set, exact pushed SHA, and seven component receipts. Missing, duplicate, partial, stale, mixed, or caller-authored evidence fails. The first red attempt permits one checkpointed batch fix and one complete re-PROVE. A second red blocks sign-off and returns an attributed human action. Restart resumes immutable journal state and never overwrites or reuses partial green evidence.

### Wave 13 — catalog, CI, and ratchets

15. t15-r0011-ci-inventory-and-ratchets lands the frozen full-suite disposition input and test, schema/tier/cycle/SCC/LOC policies, and the CI-to-public-PROVE edge. It also installs the external-action authority cases in the shared operations test and records only attributed authorization state in the program ledger; it performs no external action. Its bounded command runs the CI ratchet, authority, and wiring owners. The full 23-path assertion is deliberately deferred until t16 has created the final operations paths.

### Wave 14 — operations, T09, final wiring, and human completion

16. t16-r0011-operations-and-final-wiring creates the last catalog paths, runs the six group-7/group-8 files together, proves all 23 tracked paths are present and dispositioned, executes the terminal production-edge matrix, preserves reachable conformant T09 behavior, renders one journal/revision across operations and dashboard surfaces, and writes exact-revision Retro evidence. Retro records wall clock, tokens, checkpoint count, mean checkpoint cost, empty-wait share, defects per checkpoint, PROVE-caught defects, attempts, and stop/re-scope events. Empty waits must remain below 10 percent; elapsed time beyond 24 hours without a prior attributed re-scope fails.

This task validates external-action authority fixtures but performs no integration, push, tag, publication, destructive history action, or scope expansion. Final exact-SHA proof and each desired external transition require their own human approval outside Build.

## Test and production-edge policy

Each machine task contains exactly one runnable command string. Focused commands are used while building each bounded owner. The R-0010 terminal command is intentionally broad but accumulates each leg's status instead of short-circuiting; the R-0011 terminal floor remains bounded to its approved catalog and PROVE surfaces.

A behavior is complete only when the same task/commit contains:

- a positive invocation from the actual CLI, loop, DEFINE, assignment, integration, PROVE, CI, dashboard, or Retro root;
- a severed-edge mutation that makes that live flow fail;
- exact revision and contract evidence from the owning engine;
- no alternate helper-only, direct-unit-only, or evaluation-only route.

The baseline command engine, runtime/adapters, event wait, router, architecture floor, deep refusal, graph/worktree repository substrate, receipts, findings, leases, enforcement, and T09 remain unchanged when their compatibility fixtures pass. Mutation evidence verifies reuse; it does not authorize replacement.

## Primary risks and controls

- Scope creep disguised as compatibility work. Six exact gap_category values are the complete R-0010 implementation inventory. Any seventh or any task replacing a green baseline capability returns to human scope decision.
- Self-attested checkpoint success. Green receipts are minted only from runtime events, engine fingerprints, Git revision, bounded output, exact scope, and ordered phase evidence; caller fields fail as unknown.
- Merge at the wrong revision. Integration authorization binds checkpoint digest, registered worktree tip, scope, predecessors, and incumbent merge receipt. Red/stale work remains isolated.
- Automatic review expands depth. DEFINE and post-change routing use the incumbent selector once, dispatch exactly four or five quick lenses concurrently including architecture, and refuse automatic full/deep/26/fallback paths.
- Thin BUILD leaks legacy state. Assignment receipts and production traces require zero wave, claim, build-task lease, per-task contract, evaluate/fix, or per-task lens state.
- Donor history is smuggled as proof. R-0011 accepts content provenance plus new R-0010 receipts only; ancestry and diff checks reject later commits, cherry-picks, old tests, and narrative completion claims.
- Seven proof components disagree on identity. The bundle accepts one canonical identity set and refuses collection before all seven exact terminal receipts agree.
- Retry erases a red result. Journal rows are append-only with predecessor digests; one complete second attempt is the limit.
- Parallelism crosses a hidden shared owner. The recovery fan-outs are only t08b/t08c and then t08d/t08e; t12/t13 remain the only R-0011 fan-out. All checkpoint/loop/build_c, CI, graph-test, PROVE, and operations overlaps are dependency-serialized.
- Python or plugin packaging drifts. New modules remain stdlib-only, portable across CPython 3.10–3.13 and available 3.14 compile/import, strictly typed in CI, and included in deterministic clean Codex/Claude plugin archives.

## Rollout and rollback

Roll out R-0009 evidence, then the six R-0010 gaps under manual checkpoints, then the live producer bootstrap, the two bounded recovery fan-outs, and the separate exact-SHA/sign-off gate. Only afterward run the eight R-0011 catalog groups, with t12/t13 as the sole R-0011 parallel fan-out, followed by PROVE, CI, operations, final exact-SHA proof, and human sign-off.

Before sharing, an authorized human may abandon a code-bearing branch and return to unchanged bba3354e while retaining or append-safely superseding evidence. While R-0010 is unproven, direct BUILD-C stays disabled. After sharing, correction is a new forward commit or a disabled delivery-mode transition—never reset, force-push, tag movement, receipt deletion, or donor mutation. A failed R-0011 PROVE preserves attempts and blocks sign-off; it never rolls back into unproven integration.
