# R-0012 Design — Compatibility-first BUILD-C spine from v2.17.16

Status: proposed HOW for human approval. This Design is an overlay only; it does not authorize Plan, Build, integration, PROVE, or any external action.

## Decision

Select a **compatibility-first checkpoint spine**:

1. R-0009 records governed v2.17.16 as the sole baseline and closes as evidence-only, with no product-tree change.
2. R-0010 extends the live v2.17.16 loop through exactly six missing BUILD-C capabilities: AC checkpoints, engine-minted checkpoint receipts, submit-to-checkpoint wiring, DEFINE projection through the incumbent router, checkpoint-authorized integration, and direct graph-disjoint assignment without claim/build-lease/wave state.
3. R-0011 re-delivers donor content as new source changes through that checkpoint spine, then makes one durable seven-component PROVE bundle the only completion authority.

The selected design preserves every baseline-positive production call edge. It adds two narrowly owned R-0010 modules—`taskplane/checkpoint.py` for checkpoint execution/receipt validation and `taskplane/build_c.py` for phase authority, DEFINE projection, direct assignment, delivery mode, and merge authorization—and three R-0011 proof modules: `taskplane/prove.py`, `taskplane/test_tiers.py`, and `taskplane/schema_registry.py`.

No Build begins before one attributed human approval of this complete Design. R-0010 remains blocked until the evidence-only R-0009 decision is accepted. R-0011 remains blocked until R-0010 has exact pushed-SHA proof and a separate human sign-off. Main integration, pushes, tags, publications, destructive history actions, and scope expansion remain independent human gates.

## Current state at governed v2.17.16

The engine supplied no current-state inventory, so the design is grounded in the exact Git identity and cited production sources:

- `HEAD`, branch `codex/r0009-r0011-from-v2.17.16`, and dereferenced tag `v2.17.16` name `bba3354e7fc5eb052beac74af230611ae48bd7db`; the tree is `a7f46dbf6859f7d2122c4ee6ce99006b4862197a`.
- `taskplane/tp.py::cmd_command` calls `governed_command_engine.execute`; `taskplane/loop.py::governed_command` calls `governed_commands.execute`. `taskplane/governed_commands.py` composes `CommandAdapter` and `CommandRuntime`.
- `taskplane/command_runtime.py` persists append-first transitions, immutable identity bindings, 16 KiB redacted output summaries, terminal states, and delivery receipts. `taskplane/command_adapters.py` owns launch, reconnect, wait, cancellation, and host process binding.
- `taskplane/loop.py::event_wait_policy` and `event_wait_invocation` emit one 1800-second event wait, prohibit scheduled polling, and allow reissue only after completion or attention. Existing review and wave flows invoke them.
- `taskplane/review.py::start_review` invokes `lens.automatic_sweep_route`; the selector emits exactly four or five concurrent sweep slots including architecture. `review_depth_policy` and manifest validation refuse automatic/adaptive deep with `direct-human-command-not-shipped`.
- Existing graph, repository, storage, worktree cleanup, merge receipt, findings, review lease, enforcement, pushed-SHA, zero-token, and SCC/cycle primitives are live and reusable.
- The missing R-0010 edges are visible in production code: `loop.submit` records runtime-eval and snapshot evidence but does not execute an AC checkpoint; the Design/Plan boundary does not start a DEFINE projection; default parallel delivery uses `wave`, `claim`, and stage-split state; `_automatic_merge_cleanup` has no checkpoint-specific exact-SHA authorization.
- `taskplane/prove.py`, `taskplane/test_tiers.py`, `taskplane/schema_registry.py`, `taskplane/schema-registry.json`, `taskplane/tests/fixtures/full-suite-inventory.json`, and the 23 named R-0011 test paths do not exist at the baseline.
- The stabilized graph scan at committed governance baseline `17e5a847e423781afb4e64f222d595f407d3594c` is complete and non-degraded: content fingerprint `8845fcb7aeeb56991f6b2ef46fa3bfc600b1bac6bd7a83e290201371c52c3039`, scan-quality fingerprint `96171616b4fc30c8fe1daec657a2144dc0df5920526bbe7f203845a1ea00d4b2`, 92 modules, 296 edges, and 523 files. Review traverses local dependencies to depth 3 and stops at named contracts after one contract and one requirement hop. The engine reports 28 known impacted modules and no unknowns inside that radius.

## Alternatives considered

### A. Compatibility-first checkpoint spine (selected)

Keep the proven v2.17.16 runtime/review/repository boundaries, add the six missing R-0010 edges, and re-deliver R-0011 only after BUILD-C proves itself.

- Gains: smallest authorized change; preserves event-driven waits and bounded review; makes each new edge mutation-sensitive; provides a clean rollback to the unchanged baseline mode; prevents donor history from becoming proof.
- Costs: R-0010 must bootstrap under manual checkpoints; R-0011 cannot begin until a pushed exact-SHA receipt and human sign-off exist; the durable proof schemas add explicit evidence maintenance.
- Revisit when: a baseline-positive capability fails its live reuse fixture, or a seventh gap is demonstrated and a human expands scope.

### B. Port donor behavior first, then retrofit BUILD-C

Copy boundary-first modules/tests into the baseline, then add checkpointing and reconciliation around the result.

- Gains: donor functionality appears sooner and offers familiar source material.
- Costs: violates the required phase order, treats ungoverned later history as implementation authority, cannot truthfully prove the first BUILD-C shakedown, and risks integrating red work.
- Revisit when: never under R-0012; it requires a replacement Product decision.

### C. Replace the loop with a new orchestration core

Create a greenfield scheduler, receipt store, router, worktree manager, and proof service.

- Gains: one internally uniform model without compatibility adapters.
- Costs: rebuilds capabilities already proven at v2.17.16, expands beyond six gaps, creates migration/rollback risk, and invalidates the closed reuse matrix.
- Revisit when: multiple incumbent contracts independently fail live compatibility and Product authorizes a platform redesign.

### D. Verification-only status quo

Record v2.17.16 and make no BUILD-C or boundary-first changes.

- Gains: zero product change and immediate reversibility.
- Costs: leaves all six verified R-0010 gaps and every R-0011 acceptance criterion unsatisfied.
- Revisit when: Product cancels R-0010/R-0011 and closes the consolidated program after R-0009.

## Module ownership

### R-0009 evidence-only composition

- `taskplane/preflight.py` verifies branch, commit, tag, tree, Design/Plan authority, and tree equality using existing Git/read-only helpers.
- `scripts/ci_evals.py` remains the exact pushed-SHA and required-check authority; it is invoked, not reimplemented.
- `exports/r0012-v21716-baseline.json` records the exact baseline identity, attributed scope decision, product-tree fingerprint, and closed reuse matrix.
- `exports/r0012-program-ledger.json` is an append-safe logical ledger of consolidated approval, R-0009 acceptance, R-0010/R-0011 eligibility, sign-offs, and separately authorized external actions. R-0009 writes only these evidence surfaces.

### R-0010 checkpoint spine

- `taskplane/checkpoint.py` owns `taskplane.build-c-checkpoint/v1` specifications, ordered checkpoint phases, runtime-result validation, and `taskplane.build-c-checkpoint-receipt/v1`. It trusts only engine-observed command events and repository identity.
- `taskplane/build_c.py` owns `taskplane.program-phase-ledger/v1`, `taskplane.scope-disjoint-assignment/v1`, `taskplane.integration-authorization/v1`, and the direct BUILD-C mode. It validates phase eligibility, asks the existing router for DEFINE, derives non-overlapping assignments from Plan/graph scope, and authorizes merge only for the checkpoint’s exact green revision.
- `taskplane/loop.py` remains the live orchestration root. Its submission path calls the existing governed command engine and checkpoint owner; its Design/Plan transition calls BUILD-C DEFINE projection; its execution dispatch calls direct assignment; its integration path consumes exact checkpoint authorization.
- `taskplane/governed_commands.py`, `command_runtime.py`, and `command_adapters.py` remain the only command lifecycle. No checkpoint runner, polling loop, or receipt store is forked.
- `taskplane/review.py` and `lens.py` remain the only applicability/router path. DEFINE is a new caller of the incumbent selector, not a second selector.
- `taskplane/depgraph.py`, `storage.py`, `repository.py`, and `worktree_cleanup.py` remain the graph/worktree/merge substrate. BUILD-C skips legacy wave/claim/build-lease state but does not fork repository identity or merge receipts.

### R-0011 boundary-first proving delivery

- `taskplane/preflight.py` additionally seals `taskplane.plan-reconciliation-baseline/v1`: new modules, predicted impact and tolerance, contracts, task scopes, AC identities, graph revision, and compatible graph/diff identity rules.
- `taskplane/test_tiers.py` generates `taskplane.test-tier-manifest/v1` from the import graph and proves every tracked test appears exactly once, Tier-0 cannot reach quarantine, thin PROVE runs zero Tier-2, and governed-build runs all applicable Tier-2.
- `taskplane/schema_registry.py` validates the frozen 279-existing-schema reproduction and `taskplane/schema-registry.json`; new identifiers must be explicitly registered and a schema cannot unregister itself.
- `taskplane/prove.py` owns the public seven-component PROVE orchestration, identity reconciliation, full-suite dispositions, independent AC evaluation, canaries, durable attempt journal, bundle, and one-fix limit.
- `taskplane/tp.py` adds only the public `prove` CLI adapter and forwards closed arguments to `taskplane.prove`.
- `.github/workflows/ci.yml` invokes the public PROVE/ratchet surfaces and continues to invoke incumbent zero-token, compatibility, pushed-SHA, packaging, and cycle checks.
- `taskplane/dashboard.py` renders the phase/checkpoint/PROVE journal in semantic text and machine-equivalent state.
- `taskplane/retro.py` binds the required delivery metrics to the final revision and writes `exports/r0012-retro.json`.
- The exact 23 `taskplane/tests/test_r0008_*.py` files named in the requirement are new focused and live-wiring proofs. `taskplane/tests/fixtures/full-suite-inventory.json` is the frozen baseline disposition input.

## Runtime and data contracts

### Phase authority

`taskplane.program-phase-ledger/v1` is append-safe and content-addressed. Its only forward transitions are:

`awaiting_consolidated_approval → r0009_ready → r0009_accepted → r0010_active → r0010_exact_sha_green → r0010_signed_off → r0011_active → r0011_proved → r0011_signed_off`.

A transition carries actor/authority receipt, prior-record digest, phase target, exact repository revision, and evidence digests. Refusal creates no executable stage. External actions are separate rows with their own prior human approval and action receipt; phase approval never implies one.

### Checkpoint

`taskplane.build-c-checkpoint/v1` contains checkpoint id, phase, AC ids, predecessor checkpoint ids, exact worktree revision, declared scope, focused-proof repository path and argv, and ratchet baseline. The path must be a regular tracked file inside the worktree before any command starts.

The live synchronous call path is:

`tp.py loop submit → loop.submit → governed_commands.execute(launch/wait) → CommandAdapter → CommandRuntime events → checkpoint.validate_and_mint → build_c phase/integration eligibility`.

A green `taskplane.build-c-checkpoint-receipt/v1` binds:

- engine and active-contract fingerprints;
- run/task/checkpoint/AC identities;
- exact command argv/cwd fingerprint and sanitized environment fingerprint;
- bounded redacted output digest/byte count/truncation, terminal result, and exit code;
- exact repository/worktree revision and declared scope;
- ordered phase receipts for compile/import, focused proof, forbidden-state counts, ratchet delta, and one Engineering judgment over only that AC evidence delta;
- predecessor receipt digests and final verdict.

Observed fields come from `CommandRuntime` snapshots/events and Git. Caller-authored producer, result, output, environment, revision, or receipt fields are rejected as unknown. A red, errored, canceled, dead, missing-proof, stale-revision, or truncated-required-output result mints no green receipt and runs no later checkpoint phase. Checkpoints dispatch zero lenses.

Command execution stays synchronous at the orchestration boundary. Cancellation/death propagate as named terminal runtime events and wake the one existing event wait. No asyncio task or `ExceptionGroup` is introduced.

### DEFINE review

After the attributed consolidated Design approval and before Plan execution state, `loop.approve` calls `build_c.project_define`, which calls `review.start_review(stage="define")`. That uses `lens.automatic_sweep_route` once, yielding exactly four or five concurrent quick slots including architecture, one event wait, and no deep/full/26-lens/serial fallback. Any slot-count, architecture, depth, graph-quality, or collection violation returns zero Plan authority.

### Direct assignment and merge

`build_c.assign` consumes the sealed Plan plus `depgraph` scope/identity. It topologically selects ready tasks, assigns pairwise graph-disjoint scopes concurrently to isolated registered worktrees, and serializes overlaps by dependency order. Its durable assignment receipt contains no wave, claim, build-task lease, or per-task lens state. An out-of-scope write invalidates the assignment and creates no integration authorization.

`build_c.authorize_integration` accepts only a green checkpoint receipt whose target revision equals the registered worktree tip, whose predecessors are green, and whose scope matches the assignment. Only then may `RepositoryManager.merge_registered_task` run and mint the incumbent merge receipt. Red worktrees and their dependents stay isolated; no rebase onto a red revision is permitted.

### Plan identity, schema, tiers, and PROVE

The sealed Plan uses canonical repository-relative module identities and records graph fingerprint/revision, predicted impact, explicit tolerance, contracts, scopes, AC ids, and new-module identities. The diff uses the same namespace. Ambiguous identities fail before reconciliation.

`taskplane.schema-registry/v1` records id, owner module, introduced revision, shape fingerprint, and status. The frozen fixture contains exactly 279 baseline-existing ids. Unknown new ids block until registered; duplicate/malformed/stale/self-unregistered rows fail.

`taskplane.test-tier-manifest/v1` records every tracked test once with import-graph fingerprint, tier, reason, and owning component. Tier-0-to-quarantine reachability and omissions fail. Thin PROVE refuses any Tier-2 execution; governed-build completeness requires every applicable Tier-2 test.

`tp prove` creates one append-only `taskplane.prove-journal/v1` attempt and exactly seven terminal component receipts:

1. compile/import across the supported CPython 3.10–3.13 matrix;
2. generated Tier-0 manifest execution;
3. credential-empty zero-token corpus;
4. cycle/SCC/LOC ratchet;
5. four-canary red/revert/green component;
6. graph/diff impact reconciliation;
7. independent non-builder batched AC walk.

The final `taskplane.prove-bundle/v1` binds one Plan fingerprint, diff fingerprint, graph revision, full-suite inventory receipt, clean repository revision, required-check receipt set, exact pushed SHA, and the seven component receipts. Stale, mixed, duplicate, caller-authored, missing, or partial evidence fails nonzero. First red permits exactly one new checkpointed batch fix and a complete second attempt; second red records an attributable human action and blocks sign-off. Restart resumes the immutable attempt journal and never overwrites partial or failed rows.

### Python and packaging

All new runtime code stays in the existing `taskplane` namespace, uses the standard library only, and adds no runtime dependency, import-time client, or global mutable authority. Public trust-boundary records are runtime-validated even when typed. Shared state is file-locked and append-safe; concurrency tests use separate processes and do not infer safety from the GIL or claim untested free-threaded extension support.

The supported runtime floor remains the shipped CPython 3.10–3.13 matrix. The Python 3.14 solution-design guidance is applied by keeping syntax portable and adding a 3.14 compile/import leg when available. New modules receive strict static type checking in CI with a pinned development-only checker. Taskplane ships plugin archives rather than a wheel, so clean-wheel installation is not applicable; deterministic clean Codex/Claude package installation, archive-content checks for the new modules/JSON registry, and import smoke tests are the packaging DoD. No lockfile or runtime package surface changes.

## Live-edge and mutation verification gate

A behavior is delivered only when the same commit contains both positive production invocation and a severed-edge test:

| Edge or invariant | Positive production evidence | Required severed-edge/mutation evidence |
|---|---|---|
| CLI/loop → governed engine → runtime/adapters | Existing public CLI and loop fixtures launch, wait, reconnect/cancel, and bind results | Remove each call/import; `test_r0007_governed_commands.py` fails |
| Event wait | Completion, attention, error, cancellation, and death wake once; scheduled polling count is zero | Replace event invocation with polling or allow timeout reissue; sweep/command tests fail |
| Existing review/deep boundary | Existing review/build stages emit 4–5 concurrent quick slots with architecture; nonhuman deep refuses | Remove router/architecture/depth validation; sweep bootstrap fails |
| submit → runtime → checkpoint → receipt | A real governed submit runs the declared focused proof and returns the engine receipt for its exact revision | Cut any of the three edges or inject caller receipt fields; focused production-flow tests fail |
| Design approval → DEFINE router | The real approval transition emits one DEFINE quick set | Cut `build_c.project_define` or bypass `review.start_review`; wiring test fails |
| Plan → direct assignment → worktree | Disjoint scopes dispatch concurrently, overlap serializes, no legacy state exists | Cut graph call, seed overlap/out-of-scope write, or add claim/wave/lease state; direct-build tests fail |
| checkpoint → integration → merge receipt | Only the checkpoint target SHA merges | Cut authorization or substitute another/red SHA; integration fixture fails closed |
| public CLI → seven PROVE components → journal/bundle | One public command produces seven exact terminal receipts | Cut any component/collector edge, mix identities, or inject caller evidence; PROVE wiring tests fail |
| CI/dashboard/Retro live consumers | CI blocks on PROVE and ratchets; dashboard and Retro render the same journal/revision | Remove each invocation; wiring/operations tests fail |
| T09 and donor surfaces | Actual governed entry points exercise reachable conformant behavior | Sever every claimed production call edge; dormant/test-only implementations fail |

The exact 23-path catalog is a closed set checked by the PROVE completeness component. Missing, renamed, duplicated, skipped, or silently cut paths fail. T09 is preserved without rewrite when its production reachability and current contract conformance pass; only a proven missing edge or conformance delta permits a minimum correction.

## Observability and quality targets

Machine signals are versioned, deterministic, and render with equivalent semantic text:

- `signal:r0012.phase-authority`: phase, predecessor, exact revision, actor/receipt, decision.
- `signal:r0012.baseline-identity`: branch, commit, tag object/target, tree, reuse-matrix digest.
- `signal:r0012.checkpoint`: AC, proof path/command, phase, revision, terminal condition, verdict.
- `signal:r0012.checkpoint-receipt`: runtime/engine/command/environment/output/result/revision digests.
- `signal:r0012.assignment`: ready/held tasks, scope identities, overlap reason, worktree revisions, forbidden-state counts.
- `signal:r0012.integration`: checkpoint digest, authorized SHA, merge receipt, refusal reason.
- `signal:r0012.review-projection`: boundary, selected quick lenses, architecture inclusion, concurrency, wait invocation, deep refusals.
- `signal:r0012.prove-component`: attempt, component, identity set, terminal status, receipt digest.
- `signal:r0012.prove-attempt`: attempt count, fix authorization, durable predecessor, bundle status.
- `signal:r0012.reconciliation`: predicted/actual/tolerance sets and named drift/cuts.
- `signal:r0012.tier-registry`: tier coverage/quarantine reachability and schema existing/new/error counts.
- `signal:r0012.retro`: exact revision, wall clock, tokens, checkpoints, mean cost, empty-wait share, defects/checkpoint, PROVE defects, attempts, stop/re-scope events.
- `signal:r0012.external-action`: action class, approval actor/receipt, exact target, performed/refused.

Exact targets: zero R-0009 product-tree changes; exactly six R-0010 implementation categories; exactly 4–5 automatic quick lenses including architecture; zero automatic deep/full/26/serial fallback slots; zero scheduled polling; checkpoint output summaries bounded to 16 KiB; exactly 23 catalog paths; 279 baseline-existing schemas and zero falsely new; exactly seven terminal PROVE component receipts per attempt; at most two full PROVE attempts; zero new undispositioned final failures; empty-wait share below 10%; and failure for a run exceeding 24 hours without prior human re-scope. Runtime availability/RPO/RTO are not applicable to this local governed CLI; interruption safety is instead measured by durable resume and zero partial-green reuse.

## Failure modes and recovery

- Baseline or later-history mismatch: `signal:r0012.baseline-identity` refuses R-0009 in the same invocation. The program owner corrects only the evidence/branch selection or returns to Product; no executable state exists.
- Missing consolidated/predecessor authority or seventh gap: `signal:r0012.phase-authority` refuses before Plan/task/worktree creation. The human supplies the missing approval or records a new scope decision; retry is bounded to that transition.
- Missing proof, command failure, cancellation, death, or stale checkpoint revision: `signal:r0012.checkpoint` is red and later phases do not run. The task owner repairs the declared AC scope and reruns that checkpoint once on a new exact revision.
- Forged or mixed checkpoint evidence: `signal:r0012.checkpoint-receipt` refuses before a green receipt or integration state. The checkpoint owner re-derives evidence from the durable runtime journal; caller data is discarded.
- Invalid quick projection or deep request: `signal:r0012.review-projection` creates no downstream authority. The orchestration owner corrects the incumbent-router invocation; automatic fallback is forbidden.
- Overlap, out-of-scope write, or ambiguous graph identity: `signal:r0012.assignment` serializes overlap or refuses the affected assignment before integration. The planner corrects scope/identity under a new approved Plan if needed.
- Red/stale merge attempt: `signal:r0012.integration` keeps the worktree and dependents isolated. The orchestrator accepts only a new green checkpoint for that exact tip; it never rebases dependents onto red.
- Tier/schema/reconciliation/canary failure: `signal:r0012.tier-registry` or `signal:r0012.reconciliation` makes the current PROVE attempt red. The non-builder evaluator authorizes the single checkpointed batch fix, followed by a complete new attempt.
- Missing, mixed, duplicate, or partial PROVE receipt: `signal:r0012.prove-component` and `signal:r0012.prove-attempt` block the bundle. The proof owner resumes the current durable attempt or starts the one allowed re-PROVE; a second red returns human action.
- Missing external approval: `signal:r0012.external-action` blocks only that action. The release owner obtains a distinct attributed approval or leaves the action unperformed.

## Risks and controls

- **Scope creep disguised as compatibility work:** the Plan schema permits only six R-0010 category ids and fails on any task without exactly one. Owner: Plan approver.
- **Checkpoint self-attestation:** runtime-observed fields and engine fingerprints are minted after execution; unknown caller fields fail. Owner: checkpoint module.
- **A green task merging at another SHA:** authorization binds checkpoint digest, registered worktree tip, and merge receipt. Owner: BUILD-C integration.
- **Legacy state leaking into thin BUILD:** direct mode asserts zero wave/claim/build-lease/per-task-lens records; governed-build remains a separate retained compatibility mode. Owner: loop.
- **Donor history smuggling:** content provenance is path/hash only; ancestry/diff proof rejects v2.17.17+ commits as parents, cherry-picks, or evidence. Owner: R-0011 provenance record.
- **Seven-part proof becoming seven inconsistent identities:** the final bundle accepts only one canonical identity set and rejects mixing before collection. Owner: PROVE.
- **Retry overwriting a failure:** append-only attempt ids and predecessor digests preserve red/superseded evidence. Owner: PROVE journal.
- **Python/package drift:** new modules remain stdlib runtime code, are strictly type-checked, run across the support matrix, and are proven in clean deterministic plugin archives. Owner: CI.

No silent technical debt is accepted. Retained governed-build machinery is an explicit compatibility surface, not a deletion candidate in this requirement. Its future removal requires a separate requirement and signed evidence; R-0012 neither expands nor silently accepts that debt.

## Rollout and rollback

1. Obtain one attributed consolidated Design approval; before it, every executable stage refuses.
2. Record R-0009 baseline/reuse evidence only and prove the product tree unchanged.
3. Deliver R-0010 under manual AC/group checkpoints: phase authority and checkpoint/receipt first, submit wiring second, DEFINE projection third, direct assignment fourth, checkpoint-authorized integration fifth, then the compatibility and live-edge matrix.
4. After local R-0010 floors are green, require a separate push approval, fetched exact-SHA proof, and human R-0010 sign-off.
5. Re-deliver R-0011 in graph-disjoint groups matching the eight catalog groups, each through R-0010 checkpoints; seal Plan identity, tier/registry, PROVE/reconciliation, CI/operations, then run final review/PROVE/Retro.
6. Cross each integration, push, tag, publication, destructive-history, or scope-expansion boundary only with its own attributed approval.

Before any shared mutation, rollback is branch abandonment back to unchanged `bba3354e...`; R-0009 evidence is superseded by an append-only record, not rewritten. While R-0010 is unproven, direct BUILD-C remains disabled and the existing governed-build path remains intact. A red R-0010 leaves R-0011 closed. After a commit is shared, rollback is a new forward correction or disabling delivery-mode transition—never reset, force-push, tag movement, receipt deletion, or donor mutation. A failed R-0011 PROVE leaves all checkpoint/journal evidence readable and blocks sign-off; it does not roll back into unproven integration.

## Solution-design lens result

The design is grounded in exact v2.17.16 sources and does not rebuild the proven command, wait, review, graph, worktree, receipt, findings, or enforcement machinery. The alternatives are materially different, and the selected approach is the only one consistent with the six-gap scope and predecessor gates. Every one of the 20 acceptance criteria maps to a named module/contract and executable positive/negative validation. All changed contracts have reversible rollout, every failure mode names an emitted signal plus an actor and bounded recovery, and Plan can decompose the phase/call-edge order without inventing authority or identity rules.

The state-transition visual is required because the non-code R-0009 closure, manual R-0010 bootstrap, exact-SHA/human gate, R-0011 one-fix PROVE loop, and separately controlled external actions are materially easier to audit as one state machine.

## Open questions

None.
