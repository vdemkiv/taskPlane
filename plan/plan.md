# R-0004 recovery replan — stage-isolated delivery entities and bounded artifact handoffs

## Approved Design bootstrap binding

The active legacy loop reports `design=null` because it cannot import the approval receipt from the completed design-only loop. This Plan uses the explicitly authorized bootstrap binding to the committed, human-approved R-0004 Design Contract at commit `5fc8dfb6a8bf86bf02318d71cf44f6fb400a664b`:

- machine contract: `design/contract.json`
- human contract: `design/design.md`
- human approval fingerprint: `cd77b0d371716841f83a21f117df834d33601353d6c27ffaed7cb048c2768458`
- Design content fingerprint: `28d8ae50217990462d79d6d43fae1e33bae8523cd31b66b034df4a295c1a51d6`
- approved graph baseline: `6c66052b6ca3b237ce3be38f744e41551ead9f9c118c94a82e6439ac000fe976`

This binding fixes only the legacy approval-handoff bootstrap. It does not self-approve Design or Plan, alter Product or Design artifacts, or authorize implementation. The orchestrator remains the only gate owner.

R-0004 depends on the resolved R-0003 foundation. All 11 acceptance criteria and all six requirement contract ids are present, with no open questions. Autonomous-after-design execution and broader workflow simplification are explicitly deferred and out of scope.

## Replan evidence and committed t01 recovery

Parallel Evaluate currently resolves the governed task worktree for its target, diff, contract, and evidence, but then calls `depgraph.load`, `depgraph.impact`, and product-impact logic against the shared primary checkout. That split identity makes ReviewKernel judge a valid task revision against an unrelated stale primary graph. The observed task graph at commit `44415b6c92976e4ab9f6730c449607109a4aeebf` is complete with 41 modules, 153 edges, and 500 files; the shared primary graph at Design commit `5fc8dfb6a8bf86bf02318d71cf44f6fb400a664b` instead produces `impact_incomplete` / `stale_graph` for the task evaluation.

The new `t00-parallel-evaluate-worktree-graph-binding` prerequisite repairs only that harness seam. ReviewKernel graph head, impact, product impact, routing inputs, and emitted graph evidence must use the exact governed worktree and target revision already selected for parallel Evaluate. The regression creates distinct primary and task graph states, proves the evaluator reads the task graph, proves the primary graph bytes and metadata are neither read as task evidence nor overwritten, and preserves fail-closed results for a missing, stale, revision-mismatched, or ambiguous task graph. No depgraph/storage module is added because the required dependency is a workspace-selection correction inside `taskplane/loop.py`.

The already-built handoff implementation is an immutable reusable artifact, not work to rewrite: branch `tp/t01-bounded-handoff-artifact-boundary` points to commit `44415b6c92976e4ab9f6730c449607109a4aeebf`, whose parent is `0fa1b3e0810fcebe5e83cc4f30be9c6278ab139e` and whose diff is exactly the four preserved t01 scope files: `taskplane/review_evidence.py`, `taskplane/stage_handoff.py`, `taskplane/tests/test_stage_handoff.py`, and `taskplane/tests/test_stage_handoff_security.py`. Its 14 focused tests already pass.

After t00 independently passes and the orchestrator merges that repair, Taskplane claims a fresh governed t01 worktree from the repaired primary baseline. Inside that claimed worktree only, recover the immutable artifact by cherry-picking commit `44415b6c92976e4ab9f6730c449607109a4aeebf`; retain the original branch and commit unchanged, verify the recovered diff still contains exactly those four files, rescan the recovered task worktree graph at its new target commit, and rerun t01's declared command. The original t01 branch is not merged directly or prematurely into primary, and no ungoverned copy/rewrite substitutes for the commit.

## Bounded impact and depth policy

The single required replan impact query used one comma-separated value for the revised paths `taskplane/loop.py,taskplane/tests/test_loop.py`. It returned 29 in-policy impacted modules, no unknown surfaces, affected requirements R-0002/R-0003/R-0004, dependent requirement R-0005, current primary graph fingerprint `edd1bee36c3e1168f9fac33dc9d36acde833e0f91d2ec88f412a6e489d482d6e`, and scanned head `5fc8dfb6a8bf86bf02318d71cf44f6fb400a664b`. The result was policy-truncated but not depth-truncated: requirement traversal stopped at the approved requirement-depth boundary, while local depth and contract coverage remained complete. The approved Design overlay remains pinned separately to baseline `6c66052b6ca3b237ce3be38f744e41551ead9f9c118c94a82e6439ac000fe976`.

Every task carries the approved typed impact policy:

- local depth: 3
- boundary mode: `contract-only`
- contract depth: 1
- requirement depth: 1

The seven-task set preserves exactly 19/19 proposed modules, 31/31 proposed edges, 6/6 contract ids, and 11/11 unique verbatim acceptance criteria. There are 12 criterion assignments because Plan DoR requires every prerequisite to carry a criterion: t00 shares AC9 solely as the harness that makes its Review/sign-off evidence target-correct, while t05 retains the approved product-delivery ownership.

## Design locks

The implementation must preserve these settled boundaries:

- `taskplane.stage/v1` aggregates and `taskplane.stage-handoff/v1` manifests are immutable, content-addressed objects indexed by additive `taskplane.run/v4` state.
- One expected-revision RunStore commit owns stage heads, lineage, the replaceable active-stage projection, and request-fingerprinted operation receipts. Immutable objects are written before the authoritative index commit.
- A canonical handoff is at most 64 KiB and at most 64 selected artifact references; each bounded summary is at most 16 KiB; each history page is at most 100 rows.
- Successor startup reads only the handoff and explicitly selected verified artifacts. It receives a fresh execution root and inherits no predecessor agents, conversations, event logs, tool transcripts, leases, meters, contracts, runtime environment, or mutable worktree.
- Terminal outcomes are exactly `done`, `closed`, or `discarded`; no terminal stage reopens. Projection corruption stops dispatch and is repaired from indexed heads without guessing a foreground stage.
- Migration is additive, idempotent, and non-destructive. Exact legacy source bytes are retained, conservation is proved, and ambiguous state becomes an immutable `taskplane.legacy-unknown/v1` sentinel with an attributable reason.
- Stage terminalization, migration, closure, and discard never invoke worktree cleanup. R-0003 authority, ReviewKernel evidence, interference isolation, merge receipts, and exact-worktree cleanup invariants remain unchanged.

## Task graph

| Order | Task | Dependencies | Type / model | Criteria | Proposed modules | Edges |
| --- | --- | --- | --- | --- | ---: | ---: |
| 0 | `t00-parallel-evaluate-worktree-graph-binding` | none | reliability / deep | AC9 shared, validation-enabling | 0 | 0 |
| 1 | `t01-bounded-handoff-artifact-boundary` | t00 | security / deep | AC4 | 2 | 4 |
| 2 | `t02-atomic-stage-lifecycle-and-lineage` | t01 | reliability / deep | AC1, AC2, AC6, AC7, AC8 | 6 | 14 |
| 3a | `t03-isolated-stage-dispatch-and-cli` | t02 | integration / standard | AC3 | 3 | 3 |
| 3b | `t04-conservative-singleton-migration` | t02 | data / deep | AC11 | 3 | 5 |
| 4 | `t05-bounded-lineage-projections-and-scaling` | t03 | performance / deep | AC9 product, AC10 | 4 | 4 |
| 5 | `t06-cross-host-rollout-and-r0003-preservation` | t04, t05 | integration / deep | AC5 | 1 | 1 |

t03 and t04 retain disjoint writable scopes and may execute in parallel after the atomic domain core is proven. t06 joins migration and bounded-reader evidence before cross-host enablement. The six approved delivery scopes are unchanged. The only overlap introduced by the prerequisite is `taskplane/loop.py` with downstream t03; the transitive chain t00 → t01 → t02 → t03 serializes that overlap honestly.

### t00 — parallel Evaluate worktree graph binding

Bind every ReviewKernel graph-quality, head, impact, product-impact, and evidence read for parallel Evaluate to the exact governed task worktree and target revision. Do not scan, load as task evidence, publish into, or overwrite the unrelated primary graph. Missing, stale, mismatched, or ambiguous task graphs remain blocking; no fallback to the primary graph is allowed. `TestParallelEvaluateWorktreeGraphBinding` is a planned new behavioral class in the scoped existing owner, not an assumed existing selector.

Scope: `taskplane/loop.py`, `taskplane/tests/test_loop.py`.

Verification: `python3 -m pytest -q taskplane/tests/test_loop.py::TestParallelEvaluateWorktreeGraphBinding taskplane/tests/test_eval_graph_compliance.py`

### t01 — bounded handoff and artifact boundary

Implement the closed canonical manifest schema, authority binding, explicit exclusions, 64 KiB/64-reference bounds, conditional target/commit identity, and existing content-addressed evidence verification. Hostile paths, undeclared context, stale authority, tampering, digest/byte mismatch, and discarded-default consumption fail before lifecycle state changes.

Scope: `taskplane/stage_handoff.py`, `taskplane/review_evidence.py`, `taskplane/tests/test_stage_handoff.py`, `taskplane/tests/test_stage_handoff_security.py`.

Verification: `python3 -m pytest -q taskplane/tests/test_stage_handoff.py taskplane/tests/test_stage_handoff_security.py taskplane/tests/test_artifact_references.py`

### t02 — atomic stage lifecycle and lineage

Add stage aggregates, unique confined execution roots, RunStore v4 indexes, request-fingerprinted receipts, terminal guards, projection rebuild, split transactions, crash/reconnect/retry behavior, and exact authorization revalidation. A split commits the closed parent, deterministic children, artifact subsets, dependencies, budgets, roots, lineage, projection, and receipt atomically. This task also establishes the source-level barrier that stage operations cannot call or broaden R-0003 cleanup.

Scope: `taskplane/stage_entities.py`, `taskplane/run_store.py`, `taskplane/storage.py`, `taskplane/repository.py`, `taskplane/worktree_cleanup.py`, and the four focused stage entity/transaction/split/projection test owners declared in `tasks.json`.

Verification: `python3 -m pytest -q taskplane/tests/test_stage_entities.py taskplane/tests/test_stage_transactions.py taskplane/tests/test_stage_split.py taskplane/tests/test_stage_projection.py taskplane/tests/test_storage_kernel.py taskplane/tests/test_worktree_cleanup_eligibility.py taskplane/tests/test_worktree_cleanup.py`

### t03 — isolated stage dispatch and CLI

Route start, resume, terminalize, split, history, and explicit reuse through stage commands. Create a fresh native execution tree per stage/attempt and serialize only the bounded handoff, selected artifact references, stage authority, budget, and declared scope. Reject terminal resume and require a successor instead.

Scope: `taskplane/loop.py`, `taskplane/taskplane_lite.py`, `taskplane/tp.py`, `taskplane/tests/test_stage_dispatch.py`, `taskplane/tests/test_stage_cli.py`.

Verification: `python3 -m pytest -q taskplane/tests/test_stage_dispatch.py taskplane/tests/test_stage_cli.py taskplane/tests/test_v230_loop.py taskplane/tests/test_v231_dispatch.py`

### t04 — conservative singleton migration

Fingerprint and retain singleton loop, track, and governed reference bytes before projection; create deterministic v4 aggregates only when evidence is unambiguous; otherwise retain an explicit unknown sentinel. Commit the conservation manifest, stage indexes, lineage, projection, source fingerprints, and migration receipt atomically. After a verified receipt the compatibility adapter is read-only; source artifacts remain available through rollback.

Scope: `taskplane/stage_migration.py`, `taskplane/track.py`, `docs/state-spec.md`, `docs/loop-design.md`, `taskplane/tests/test_stage_migration.py`, `taskplane/tests/test_stage_legacy_adapter.py`.

Verification: `python3 -m pytest -q taskplane/tests/test_stage_migration.py taskplane/tests/test_stage_legacy_adapter.py taskplane/tests/test_track_retro.py taskplane/tests/test_v230_loop.py`

### t05 — bounded lineage projections and scaling

Move status, dashboard, Review, sign-off, and Retro to bounded stage and lineage summaries. Instrument predecessor-tree opens and fail if default projections use them. Prove identical successor startup SHA-256 and bytes, identical selected-reference reads, zero predecessor-root opens, and stable work/token estimates for equivalent predecessors with 10 and 100,000 irrelevant events.

Scope: `taskplane/loop_status.py`, `taskplane/dashboard.py`, `taskplane/retro.py`, `taskplane/runtime_eval.py`, `taskplane/tests/test_stage_bounded_views.py`, `taskplane/tests/test_stage_startup_scaling.py`.

Verification: `python3 -m pytest -q taskplane/tests/test_stage_bounded_views.py taskplane/tests/test_stage_startup_scaling.py taskplane/tests/test_status_and_large_delivery.py taskplane/tests/test_v230_dashboard.py taskplane/tests/test_track_retro.py`

### t06 — cross-host rollout and R-0003 preservation

Make Product, Design, Build, Review, Evaluation, Engineering, status, and Retro host flows consume the bounded manifest without importing predecessor runtime context. Prove Product/Design/Review/Evaluation and extension stages may close or discard without an implementation child, retained artifacts remain auditable, and later explicit reuse requires new authority. Finish Codex/Claude/Slack-capable/managed/legacy parity, text-first and machine parity, Python 3.10–3.12 checks, clean plugin packaging, disabled-flag/shadow-migration/new-run canary/reader-cutover/rollback fixtures, and the complete R-0003 preservation suite.

Scope: the exact skill, flow, packaging, CI, and focused test paths declared by t06 in `tasks.json`.

Verification: `python3 -m pytest -q taskplane/tests/test_stage_non_build_handoffs.py taskplane/tests/test_stage_cross_host.py taskplane/tests/test_stage_rollout.py taskplane/tests/test_stage_r0003_preservation.py taskplane/tests/test_stage_release_matrix.py taskplane/tests/test_host_native_compatibility.py taskplane/tests/test_dispatch_parity.py taskplane/tests/test_skill_flows.py taskplane/tests/test_r0002_reviewkernel_regression_floor.py taskplane/tests/test_enforcement_core.py taskplane/tests/test_enforcement_integration.py taskplane/tests/test_collision_core.py taskplane/tests/test_collision_integration.py taskplane/tests/test_worktree_cleanup_eligibility.py taskplane/tests/test_worktree_cleanup.py taskplane/tests/test_artifact_references.py taskplane/tests/test_review_evidence_lifecycle.py taskplane/tests/test_consolidated_authority.py taskplane/tests/test_release_freshness.py taskplane/tests/test_release_provenance.py`

## Verbatim acceptance ownership

1. **t02:** Every stage entity has a stable stage id, requirement revision, stage kind, parent or predecessor links, bounded input manifest, independent execution tree, and exactly one terminal outcome of done, closed, or discarded.
2. **t02:** Done requires the declared deliverables and completion evidence; closed requires an attributable reason explaining why no further work is required; discarded requires an attributable reason explaining why its results must not be consumed. No terminal entity can silently return to active.
3. **t03:** Starting a next stage creates a new execution tree and consumes only a versioned manifest plus explicitly selected content-addressed artifacts from predecessor stages; prior agents, conversations, event logs, tool transcripts, leases, and runtime state are not inherited as context.
4. **t01:** A handoff manifest records producer stage id and outcome, requirement and design revisions, target and commit identity where applicable, contracts, deliverables, evidence references, artifact fingerprints, exclusions, and the actor and time authorizing continuation.
5. **t06:** Product, Design, Review, Evaluation, and other non-build work can terminate closed or discarded without creating an implementation stage, while their retained artifacts remain addressable for audit or later explicit reuse.
6. **t02:** Splitting a deliverable closes the parent with a split reason and creates two or more independently addressable child stage entities with explicit artifact subsets, dependencies, budgets, and lifecycles; one child outcome cannot mutate sibling or parent history.
7. **t02:** The active-stage pointer is a replaceable projection only. Starting, resuming, splitting, or terminalizing a stage never overwrites or reclassifies a predecessor entity, and history lists every terminal and active entity with lineage.
8. **t02:** Crash, duplicate event, reconnect, and retry fixtures prove stage terminalization, handoff creation, and split creation are atomic and idempotent, with no duplicate child, lost artifact, reopened terminal stage, or ambiguous active pointer.
9. **t00 (validation-enabling) and t05 (product delivery):** Dashboard, status, review, sign-off, and Retro show the current stage, predecessor outcome, artifact handoff, and child lineage from bounded summaries without loading predecessor execution trees.
10. **t05:** As irrelevant predecessor history grows from ten to one hundred thousand events, the default successor startup payload remains byte-identical and bounded to the manifest plus explicitly selected artifacts; startup work and token use do not scale with predecessor runtime history.
11. **t04:** A migration converts singleton loop records into stage entities without losing requirements, tasks, decisions, evidence, commits, reviews, or audit history; ambiguous legacy state is preserved with an explicit unknown reason rather than guessed as pending, done, closed, or discarded.

## Contract and proposed-module ownership

The machine plan uses only the six exact contract ids, never relation-prefixed pseudo-ids: `contract:stage-entity-lifecycle`, `contract:stage-artifact-handoff`, `contract:delivery-lineage`, `contract:consolidated-authorization`, `contract:automatic-recovery`, and `contract:review-evidence-binding`.

- t00 adds no Design module; it repairs the existing `taskplane/loop.py` evaluation harness under `contract:consolidated-authorization` and `contract:review-evidence-binding`.
- t01 owns `taskplane/stage_handoff.py` and `taskplane/review_evidence.py`.
- t02 owns `taskplane/stage_entities.py`, `taskplane/run_store.py`, `taskplane/storage.py`, `taskplane/repository.py`, `taskplane/worktree_cleanup.py`, and `taskplane/tests`.
- t03 owns `taskplane/loop.py`, `taskplane/taskplane_lite.py`, and `taskplane/tp.py`.
- t04 owns `taskplane/stage_migration.py`, `taskplane/track.py`, and `docs`.
- t05 owns `taskplane/loop_status.py`, `taskplane/dashboard.py`, `taskplane/retro.py`, and `taskplane/runtime_eval.py`.
- t06 owns `skills`.

## Exact proposed-edge ownership

### t01 — 4

- `taskplane/stage_handoff.py->contract:stage-artifact-handoff:provides`
- `taskplane/stage_handoff.py->contract:review-evidence-binding:consumes`
- `taskplane/stage_handoff.py->taskplane/review_evidence.py:runtime`
- `taskplane/stage_handoff.py->taskplane/storage.py:runtime`

### t02 — 14

- `taskplane/stage_entities.py->contract:stage-entity-lifecycle:provides`
- `taskplane/stage_entities.py->contract:delivery-lineage:provides`
- `taskplane/stage_entities.py->contract:consolidated-authorization:changes`
- `taskplane/stage_entities.py->contract:automatic-recovery:changes`
- `taskplane/stage_entities.py->taskplane/run_store.py:runtime`
- `taskplane/stage_entities.py->taskplane/stage_handoff.py:runtime`
- `taskplane/stage_entities.py->taskplane/storage.py:runtime`
- `taskplane/run_store.py->contract:stage-entity-lifecycle:stores`
- `taskplane/run_store.py->contract:delivery-lineage:stores`
- `taskplane/repository.py->contract:consolidated-authorization:consumes`
- `taskplane/worktree_cleanup.py->contract:automatic-recovery:consumes`
- `taskplane/tests->contract:stage-entity-lifecycle:validates`
- `taskplane/tests->contract:stage-artifact-handoff:validates`
- `taskplane/tests->contract:delivery-lineage:validates`

### t03 — 3

- `taskplane/loop.py->taskplane/stage_entities.py:runtime`
- `taskplane/tp.py->taskplane/stage_entities.py:runtime`
- `taskplane/taskplane_lite.py->taskplane/stage_entities.py:runtime`

### t04 — 5

- `taskplane/stage_migration.py->taskplane/stage_entities.py:runtime`
- `taskplane/stage_migration.py->taskplane/run_store.py:runtime`
- `taskplane/stage_migration.py->taskplane/review_evidence.py:runtime`
- `taskplane/track.py->taskplane/stage_migration.py:runtime`
- `docs->contract:stage-entity-lifecycle:defines`

### t05 — 4

- `taskplane/loop_status.py->contract:delivery-lineage:consumes`
- `taskplane/dashboard.py->contract:delivery-lineage:consumes`
- `taskplane/retro.py->contract:delivery-lineage:consumes`
- `taskplane/runtime_eval.py->contract:stage-artifact-handoff:consumes`

### t06 — 1

- `skills->contract:stage-artifact-handoff:consumes`

## R-0003 preservation and cleanup matrix

R-0004 changes neither consolidated gate authority nor the conditions under which post-merge cleanup may run. Every lifecycle command and handoff remains bound to the exact run, repository/worktree, requirement/design revision, actor/session, and authority revision; authority is re-resolved immediately before commit. Review artifact identity, digest, byte count, confinement, and canonical-run storage remain verifiable after an eligible worker-tree removal. Recovery may replay an identical authorized receipt or rebuild a projection; it cannot select an outcome, add artifacts, reopen a stage, manufacture children, approve gates, or broaden cleanup.

Stage terminalization, migration, retention, closure, and discard contain no cleanup call. Existing cleanup remains separately eligible only for the exact registered Taskplane-managed linked worktree whose recorded branch tip is proven an ancestor of the re-resolved primary `main` tip after every last-moment check. Dirty, untracked, staged, unmerged, foreign, unregistered, selected-variant, failed, active, locked, symlinked/reparse-point, path-mismatched, missing-ref, ambiguous-main, primary/main, merge-in-progress, evidence-needed, last-moment-uncertain, branch/tip-mismatched, or recorded-tip-not-ancestor state retains the tree and registration.

Cleanup stays no-force and exact-path only. It never broadens scope, deletes a branch or commit, or removes requirement, design, plan, submission, test, review-evidence, audit, Retro, status, graph, EM, or sign-off inputs. A crash before a durable merge receipt retains. A crash after the receipt permits only the same idempotent maintenance replay; inconsistent absence requires manual attention. t02 enforces the source boundary and focused cleanup regressions; t06 runs the complete ReviewKernel, enforcement, collision, evidence, authorization, merge, cleanup, release, and cross-host preservation floor before rollout completes.

## Rollout, rollback, and deferred work

1. t00 binds parallel evaluation to the exact governed task worktree graph and proves fail-closed isolation from primary graph state.
2. t01 is recovered from immutable commit `44415b6c92976e4ab9f6730c449607109a4aeebf` into a fresh post-t00 governed worktree, rescanned, rerun, and independently reevaluated before any merge.
3. t01/t02 ship additive v4 schemas, readers, immutable storage, validation, receipts, and projection repair behind a disabled feature flag while v3 writes remain available.
4. t04 runs non-mutating shadow migration and compares legacy/v4 summaries, lineage, retained references, source fingerprints, and conservation sets without switching readers.
5. t03 enables stage-native roots only for new-run canaries. Existing runs migrate only after t04 conservation evidence passes; the legacy adapter remains readable.
6. t05 cuts status/dashboard/review/sign-off/Retro to bounded summaries only after zero-predecessor-tree-open and 10-vs-100000 invariants pass.
7. t06 enables cross-host stage handoffs only after migration, bounded readers, package/runtime parity, non-build closure, and every R-0003 preservation test pass.

Rollback disables new v4 stage creation and leaves immutable stage, handoff, source, evidence, and receipt objects intact. Unmigrated callers may use legacy reads; migrated runs remain v4-readable and pause mutation until re-enabled. Rollback never reverse-collapses stage history, reopens a terminal outcome, guesses an unknown state, deletes artifacts or branches, weakens authority/evidence, or broadens/forces cleanup.

Removing the singleton write adapter is deferred until verified migration coverage reaches 100 percent and must be tracked as later requirement-linked debt. Live predecessor-context transfer, autonomous continuation after Design, terminal reopening, implicit Build creation, hidden artifact selection, scheduler/artifact-service/source-control redesign, graph/lens redesign, and release publication remain out of scope.
