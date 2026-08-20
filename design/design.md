# R-0004 Design — Stage-isolated delivery entities and bounded artifact handoffs

Status: proposed HOW, awaiting orchestrator gate and human approval. This document does not mutate the as-built dependency graph.

## Decision

Extend `RunStore` with immutable stage aggregates, immutable handoff manifests, and a small projection/legacy adapter. The run manifest becomes the atomic index for stage heads, lineage, operation receipts, and a replaceable active-stage projection; it is not the stage history itself. Every stage receives its own execution root and starts from one bounded, versioned input manifest plus explicitly selected content-addressed artifacts.

This is an additive `taskplane.run/v4` design. Existing v3 run receipts and the R-0003 enforcement, collision-isolation, worktree-registration, merge, and cleanup behavior remain regression obligations. Stage terminalization never implies worktree deletion. Existing cleanup remains a separate, fail-closed operation governed by its exact registered-worktree and merged-tip proofs.

## Current-state evidence

- `taskplane/loop.py` persists and mutates one `loop.json`; `force` initialization replaces that file, and loop steps, tasks, submissions, gates, and approvals share the same mutable record.
- `taskplane/track.py` represents one active track by moving the live `loop.json` to and from `tracks/<name>/loop.json` under a common lock. This preserves only singleton ownership, not immutable stage lineage or independent split children.
- `taskplane/run_store.py` already provides revision-checked atomic manifest commits, journal entries, and durable R-0003 enforcement/interference/merge/cleanup receipts. It is the smallest existing transaction boundary on which to add stage indexes and idempotency receipts.
- `taskplane/review_evidence.py` already provides confined, immutable, content-addressed canonical JSON artifacts with digest and byte-count verification. Stage handoffs can consume that contract rather than inventing a second artifact authority.
- `taskplane/storage.py` resolves canonical external run roots and managed worktree registration. Execution roots and stage-object roots must use the same locator and must not be placed in the source checkout.
- `taskplane/loop_status.py` and `taskplane/dashboard.py` currently project the singleton loop state. `taskplane/retro.py` currently loads all run trace events. They require a bounded stage-summary read seam so predecessor execution trees are never opened for normal status, review, sign-off, or Retro.
- `docs/state-spec.md` documents external, run-scoped runtime state and the present singleton loop/track model. The stage schema, projection semantics, and migration require an explicit state-spec update.

The captured dependency graph has 64 modules and 224 edges. Its baseline content fingerprint is `6c66052b6ca3b237ce3be38f744e41551ead9f9c118c94a82e6439ac000fe976`. The graph’s current-state inventory is empty, so the cited repository sources above are the current-state authority. R-0004 has exactly 11 acceptance criteria, no open questions, and a resolvable dependency on R-0003.

## Alternatives

### A. Extend the singleton move/restore model

Add stage fields to `loop.json` and teach `track.py` to move more files between active and inactive directories.

This is the smallest superficial change and preserves current CLI shapes. It fails the core aggregate boundary: split children still contend for one mutable history, moving a record changes its address, terminal predecessors can be reclassified by restore, and successor startup remains coupled to a record containing predecessor tasks and submissions. Extra locks cannot turn the moved singleton into independently addressable immutable stages.

Revisit only if R-0004 is narrowed to single-stage, single-child sequential execution without immutable lineage or bounded context.

### B. RunStore-backed immutable stage aggregates with a projection adapter — selected

Store each stage revision, terminal summary, and handoff manifest as immutable content-addressed objects. Commit only references and bounded summaries into a revision-checked run index. Keep `loop.json` and track commands behind a compatibility adapter while migration is active; make new stage writes authoritative in `RunStore`.

This reuses atomic file replacement, run locking, revision checks, canonical runtime roots, artifact verification, and R-0003 receipts. The cost is a deliberate v3-to-v4 schema migration and a temporary dual-reader. It provides explicit aggregate roots without requiring replay of all historical events.

Revisit if a future requirement needs multi-region writers, arbitrary temporal queries, or rebuilding every domain fact from an event log.

### C. Full event-sourced stage graph

Represent every stage transition, split, handoff, and projection change as an event and reconstruct entities through replay plus snapshots.

This offers the richest temporal query surface and natural append-only audit. It also creates a new event schema, replay/upcaster/snapshot machinery, ordering rules, and compaction policy. It makes bounded startup and Retro harder to prove because consumers can accidentally replay predecessor history. Current local, revision-checked single-run writes do not justify that complexity.

Revisit when independently writing coordinators or temporal audit queries become product requirements and snapshot governance is funded.

## Aggregate and storage model

### Immutable objects

`taskplane/stage_entities.py` owns `taskplane.stage/v1` and its state machine. A stage object contains:

- stable `stage_id` and `run_id`;
- `requirement_id` and content fingerprint/revision, optional approved design fingerprint/revision, and `stage_kind` (`product`, `design`, `plan`, `build`, `evaluate`, `engineering`, `retro`, or extension kind);
- sorted `parent_stage_ids` and `predecessor_stage_ids`;
- an immutable `input_manifest_ref` and a unique `execution_root_id` resolved beneath the canonical run root;
- declared deliverables, budget, dependencies, contracts, and authority binding;
- lifecycle state (`active` or `terminal`) and exactly one terminal outcome (`done`, `closed`, or `discarded`) when terminal;
- attribution, reason codes, timestamps, aggregate revision, and content fingerprint.

Only an active aggregate can be terminalized. `done` is accepted only after every declared deliverable and completion-evidence reference verifies. `closed` requires actor, time, reason code, and text explaining why no further work is required. `discarded` requires actor, time, reason code, text explaining why results must not be consumed, and sets `default_consumable=false`. There is no reopen transition. Further work creates a successor stage.

Discarded and closed artifacts remain immutable and addressable for audit. Later reuse is possible only through a new attributable authorization that selects exact artifact fingerprints into a new handoff; this never changes the predecessor outcome and never treats a discarded stage result as default input.

### Handoff manifest

`taskplane/stage_handoff.py` owns `taskplane.stage-handoff/v1`. Its canonical form records:

- producer stage id and terminal outcome;
- requirement id/revision and design revision/fingerprint;
- target identity and commit identity when applicable;
- provided/consumed/changed contracts;
- declared deliverables and verified evidence references;
- each selected artifact kind, fingerprint, digest, byte count, and redacted canonical locator;
- explicit exclusions, including predecessor agents, conversations, event logs, tool transcripts, leases, runtime state, undeclared paths, tools, secrets, and approvals;
- authorization actor, authority record, time, and operation id.

The canonical manifest is at most 64 KiB, contains at most 64 selected artifact references, and each bounded stage summary is at most 16 KiB. Artifact bodies are not inlined in startup; a stage resolves only selected verified references. A larger or incomplete manifest fails closed before a successor is created. Explicit content expansion needs a new attributed authorization and reason and is recorded as another selected artifact, never an implicit transcript import.

### Run index and active projection

`RunStore` v4 adds:

- `stage_heads`: `stage_id -> immutable stage object reference + bounded summary`;
- `lineage`: immutable parent/predecessor/child and handoff-reference tuples;
- `stage_operations`: operation-id receipts for start, resume, terminalize, handoff, split, and migration;
- `active_stage_projection`: a rebuildable object containing sorted active stage ids and an optional foreground stage id.

The projection is a cache, not authority. Its value must equal the active states in `stage_heads`; readers reject ambiguity and rebuild it under the run lock. History is derived from indexed immutable heads and lineage, never from the projection and never from directory location. A status page returns at most 100 summaries plus a cursor.

### Independent execution trees

Every stage obtains `runs/<run-id>/stages/executions/<stage-id>/` through `storage.py`. The path is unique, confined, and never reused. A host dispatcher creates a fresh native agent/thread/tree for the stage and supplies only:

1. the stage id and current authority binding;
2. the verified input handoff manifest;
3. explicitly selected artifact references;
4. the stage’s own budget and declared scope.

No predecessor conversation, agent identity, trace, event log, tool transcript, lease, meter, active contract, or runtime environment is inherited. Resume of an active stage creates a new attempt beneath the same isolated stage root from the same immutable input manifest; resuming a terminal stage is rejected and the caller must create a successor.

## Atomic commands and idempotency

Each command requires `run_id`, expected run revision, actor/authority, and an idempotency `operation_id`. Under the existing `RunStore` lock it:

1. returns the prior receipt when the operation id and request fingerprint match, or rejects reuse with different input;
2. loads and validates the current indexed aggregate heads and authority;
3. verifies all artifact references, declared evidence, lifecycle preconditions, and numeric bounds;
4. writes immutable stage/handoff objects first; unreferenced objects are harmless and garbage collection is out of scope;
5. commits in one atomic run-manifest revision the new heads, lineage, active projection, and operation receipt;
6. appends the diagnostic journal entry after the authoritative commit.

A crash before step 5 leaves the prior lifecycle authoritative. A crash after step 5 is recovered by the stored receipt even if the diagnostic journal append was missed. Duplicate events, reconnects, and retries are therefore semantic no-ops. Revision conflicts reload and re-evaluate; they never merge lifecycle changes speculatively.

### Start and terminalize

Starting a successor requires a terminal predecessor plus a valid handoff authorization, except for a root stage. It writes the new active aggregate and updates lineage/projection in one commit. Terminalization writes exactly one terminal outcome and removes that stage from the projection in one commit. Creating a successor may be combined with terminalization only by the dedicated `terminalize_and_start` command, so there is no state in which a successor is active without a durable predecessor outcome and handoff.

Non-build stages may terminalize `closed` or `discarded` with no successor. No implicit implementation stage is created.

### Split

`split_stage` requires an active parent and at least two child specifications. One transaction:

- terminalizes the parent `closed` with `reason_code=split`, actor, time, and reason;
- creates deterministic child ids from `run_id + parent_stage_id + operation_id + ordinal`;
- binds each child to an explicit selected-artifact subset, dependency list, budget, input manifest, and unique execution root;
- records child lineage and the parent-to-child handoffs;
- replaces the active projection with the child set and stores one receipt.

Children have separate aggregate heads. A child operation can name only that child’s expected head, so it cannot update its parent or siblings. Read-only artifact reference overlap is allowed; undeclared artifact inheritance is not.

## Bounded read models

`loop_status.py`, dashboard, review, sign-off, and Retro consume `stage_summary_page` and `lineage_summary`; they do not open predecessor execution directories. A terminal summary includes stage id/kind/outcome, predecessor outcome, handoff fingerprint, child ids, bounded deliverable/evidence counts, pending human action, and timestamps. Text-first renderings expose the same fields and never rely on color or the visual.

`retro.py` stops scanning all predecessor trace events. Terminalization produces the bounded metrics Retro needs, including outcome, duration, attempts, finding counts, selected artifact bytes, manifest bytes, startup tokens, explicit expansion reason, and graph/evidence fingerprints. Retro aggregates those summaries. Detailed execution data remains addressable for an explicit audit command, outside the default status/startup path.

The scaling invariant is mechanical: successor startup serialization reads the handoff object and selected artifact references only. A fixture constructs equivalent terminal stages with 10 and 100,000 irrelevant predecessor events and asserts byte-identical startup bytes, identical selected-ref reads, zero predecessor execution-tree opens, bounded manifest size, and startup/token counters independent of event count.

## Legacy migration and compatibility

`taskplane/stage_migration.py` performs an idempotent, non-destructive migration under the loop/run locks:

1. fingerprint the exact singleton `loop.json`, `tracks.json`, stored track records, and associated requirements/tasks/decisions/evidence/commits/reviews/audit references;
2. retain those source bytes as content-addressed migration artifacts before projecting anything;
3. deterministically create v4 stage objects only for states whose identity, lifecycle, and evidence are unambiguous;
4. preserve every ambiguous record as `taskplane.legacy-unknown/v1` with source fingerprint, explicit `unknown_reason`, and retained references—never guess `pending`, `done`, `closed`, or `discarded`;
5. atomically commit stage indexes, lineage, projection, migration receipt, and source fingerprints;
6. switch the adapter to stage-authoritative reads only after the receipt verifies.

The unknown record is an audit/migration sentinel, not a stage terminal outcome. It cannot be selected as default successor input. Resolution creates an attributable new stage or handoff while retaining the sentinel unchanged.

During rollout, old CLI/status callers use `track.py` as a narrow projection adapter. They may read a v4 foreground projection and render legacy fields, but they may not move, restore, overwrite, or reclassify stage aggregates. New writes go only through stage commands. The original legacy artifacts remain retained and readable throughout rollback.

## Authorization, evidence, and R-0003 preservation

The changed consolidated-authorization contract binds every lifecycle command and handoff authorization to the exact run, repository/worktree, requirement/design revision, actor/session, and current authority revision. Authority is re-resolved immediately before the atomic commit; stale or advisory-only authority cannot be silently upgraded.

The consumed review-evidence-binding contract verifies review artifact identity and keeps evidence usable after an execution worktree is removed. Stage handoff code consumes the existing artifact reference verifier rather than interpreting arbitrary paths.

R-0003 automatic recovery remains bounded and does not gain stage authority. Recovery may replay an already authorized operation id or rebuild the active projection from immutable heads. It cannot choose a terminal outcome, add artifacts, reopen a stage, manufacture a child, approve a gate, or broaden worktree cleanup.

Stage terminalization, migration, and retention never call cleanup. Existing post-merge cleanup may remove only an exact registered Taskplane-managed linked worktree after the recorded branch tip is proven an ancestor of the re-resolved primary `main` tip and every R-0003 last-moment eligibility check passes. Dirty, untracked, staged, unmerged, foreign, unregistered, selected-variant, failed, active, locked, symlinked/reparse-point, path-mismatched, missing-ref, ambiguous-main, primary/main, merge-in-progress, evidence-needed, or last-moment-uncertain worktrees remain retained. Cleanup is no-force, never broadens scope, deletes no branch/commit/requirement/design/plan/submission/test/review-evidence/audit/sign-off input, and records its outcome idempotently; a pre-merge-receipt crash retains, a post-receipt crash permits only one identical maintenance replay, and canonical EM/graph/evidence/Retro/status/sign-off records remain usable after an eligible removal.

## Failure and negative-case policy

- Invalid schema, missing revision, oversized manifest, too many references, digest mismatch, undeclared path/tool/secret/approval, noncanonical execution root, or stale authority: reject before changing the run index.
- Terminal transition requested twice: identical operation returns its receipt; a different request rejects because the head is terminal.
- Start races terminalize: expected revision permits one commit; the loser reloads and can only return the matching receipt or fail.
- Split child collision, fewer than two children, duplicate child spec, unresolved dependency, missing budget, or artifact outside its declared subset: reject the whole split.
- Projection absent/corrupt/ambiguous: stop stage dispatch, rebuild from indexed heads under lock, and record the repair; never choose a foreground stage heuristically.
- Crash around immutable writes: unindexed objects cannot affect lifecycle; a later maintenance tool may report them but does not delete them in this requirement.
- Legacy ambiguity: preserve an explicit unknown sentinel and require attributed resolution.
- Predecessor trace growth: startup and default read models never open it; a regression is a release blocker.
- R-0003 worktree cleanup negatives: retain the worktree and record the exact failed predicate; never retry with force or broader path authority.

## Observability

Structured signals include stage id/kind/state/outcome, operation/receipt fingerprint, run revision, authority revision, predecessor/parent/child ids, handoff fingerprint, manifest bytes, selected-ref count and bytes, startup bytes/tokens, explicit expansion reason, projection repairs, lifecycle conflicts, migration source/result fingerprints, unknown reasons, and cleanup-preservation reason. Metadata is bounded and redacts host paths and secret-bearing values.

Alerts fire for terminal-reopen attempts, ambiguous active projections, repeated operation-id mismatch, stage/handoff digest failure, successor startup opening a predecessor execution root, manifest bound violation, migration loss/count mismatch, and any destructive cleanup attempt lacking the complete R-0003 proof.

## Rollout and rollback

1. Ship v4 readers, schemas, immutable object storage, validation, and projection rebuild behind a disabled feature flag; retain v3 writes.
2. Run shadow migrations and compare bounded legacy/v4 summaries, retained reference counts, lineage, and authority bindings without switching readers.
3. Enable stage-native roots for new runs, then migrate existing runs only after the source-retention and conservation report passes. Keep legacy CLI reads through the adapter.
4. Move dashboard/status/review/sign-off/Retro to bounded summaries and enforce zero predecessor-tree reads. Stop singleton move/restore writes only after coverage and migration telemetry are clean.
5. Remove the write side of the adapter in a later requirement; retain legacy readers and source artifacts for audit.

Rollback disables creation of new v4 stages and returns unmigrated callers to legacy reads. It does not delete immutable objects, erase receipts, collapse stage histories into one mutable loop, reopen terminal entities, guess unknown legacy state, or weaken R-0003 cleanup/evidence proofs. Migrated runs remain readable through v4 and may be resumed only when the stage feature is re-enabled or via an explicit forward migration; no lossy reverse migration is allowed.

## Python solution-design application

The Python solution-design reference was read in full and SHA-256 verified as `9ad8935fadef92c06bfbd4338750debdd612a8391a54ba0ba026424edf7db4b7`.

| Lens concern | Design disposition |
| --- | --- |
| Supported runtime | Preserve the project’s Python 3.10–3.12 floor; parse, import, and smoke-test each version. No 3.14-only syntax or behavior. |
| Sync/async/cancellation | Storage and CLI paths remain synchronous. No event loop or background task is introduced. Interruption is handled at the atomic commit boundary and by idempotent receipts. |
| Boundary typing | Validate JSON dictionaries, enums, identifiers, byte/count bounds, environment-derived roots, Git identities, and artifact references at runtime. Use narrow typed records internally; do not rely on annotations as validation. |
| Concurrency | Use explicit cross-process file locks and expected manifest revisions. Make no correctness claim based on the GIL. |
| Resources/packaging | Schemas are represented in Python validators unless a data schema is added; any added resource must be included in the wheel/plugin and verified by clean-install import tests. Runtime state stays in canonical external roots. |
| Exceptions/cleanup | Convert boundary errors to stable domain failures, preserve causal diagnostics without secrets, close descriptors through context managers, and never compensate with destructive deletion. |
| Performance | Canonical JSON hashing and selected-reference reads are linear in the bounded manifest, not predecessor history. Benchmark 10 vs 100,000 predecessor events and assert byte identity and zero tree opens. |
| Verification | Add lifecycle/property matrices, crash injection at every commit boundary, duplicate/retry/reconnect fixtures, migration conservation tests, malicious-path/reference tests, import-cycle checks, and clean-wheel tests. |

## Graph readiness and completion

The proposed graph overlay uses the captured depth policy: local depth 3, contract-only boundary traversal, contract depth 1, and requirement depth 1. Design entry is ready because the graph is pinned, R-0004’s dependency R-0003 resolves, all 11 criteria and six contract relations are present, current seams are cited, atomic ownership is assigned, and numeric/context boundaries are fixed.

Completion requires the implementation plan and engineering review to account for every proposed module, edge, and contract; a post-merge graph scan; exact lifecycle/handoff/split/migration and 10-vs-100,000-event fixtures; cross-host bounded-summary parity; Python/package checks; and explicit realization evidence for every scanner-invisible contract edge. Any drift returns to Design before sign-off.

## Deferred debt

The compatibility read adapter and retained legacy singleton formats are intentional rollout debt. Once all supported workspaces have a verified v4 migration receipt, record removal as requirement-linked debt rather than deleting support inside this change:

`tp req debt "Remove the R-0004 singleton loop/track compatibility adapter after migration coverage reaches 100%" --req R-0004 --owner engineering --trigger "all supported persisted workspaces report verified v4 migration receipts"`

No other design question remains open.
