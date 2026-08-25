# R-0001 Design — corrective delivery architecture

Status: proposed HOW for human approval. This document does not approve a Plan, authorize implementation, or relabel any release.

## Decision

Use a **contract-first evidence spine with narrow owner adapters**, delivered in the signed order: A2 mechanisms first; the forward v2.17.21 release-repair lane second; Design test/wiring enforcement third; performance/orchestration fourth; and the measured pickup cold-start gate last. The spine introduces small standard-library owners for injected delivery ports, delivery policy, review rebind authority, host producer observations, Design wiring closure, release evidence, plan topology, delta briefs, and dispatch telemetry. Existing `loop.py`, `review.py`, `build_c.py`, `checkpoint.py`, `repository.py`, and `retro.py` remain execution boundaries but call those owners through explicit typed contracts.

This split is deliberate. Current `loop.py` is a shared hot owner for Plan readiness, stage briefing, lens routing, review startup, dispatch, gates, and release-adjacent evidence. Adding all corrective policy there would serialize otherwise disjoint work and make severed edges difficult to prove. The selected design keeps transition order in `loop.py` while moving validation and receipt construction to testable, side-effect-bounded modules.

The following meanings are fixed:

- `feature-green` is a focused, exact-SHA acceptance receipt that may advance Build. It has no tag, install, publication, or release authority.
- `release-green` is a different final-SHA receipt. It requires the closed wiring ledger, terminal full matrix, package/manifests evidence, and an independently re-queried hosted-platform run/check identity for the exact pushed SHA. A protected consumer then obtains an outside-model human recheck before an irreversible action. Local receipts alone are not platform or actor authenticity proof.
- `released-unverified` is an attributed human override record with a non-empty exact list of skipped proofs. It is evidence of an exception and can never be consumed as `release-green`; it does not claim cryptographic authenticity.
- v2.17.20 stays `released-incomplete`. Forward repair is v2.17.21. Historical graph revision `2757822ede49177fc52de8c173302286364d6206` remains an attributed inherited limitation; no history rewrite, re-release, or verifier weakening is designed.

## Grounded current state

The Design is rebound to clean detached HEAD `ecfc48ec2f5f4c25dd0d9bab4d6751bc2f130845` and authoritative requirement-enriched dependency graph `a6a3c1e72c0c268648e3727cdcec904f60c41442a1f77bf16231bbdb84cd90a6` (50 modules, 156 edges: 150 scanner edges plus six recorded R-0001 contract edges). Graph scan quality is complete/not degraded and `scanned_head` is the exact Design base. The graph policy remains local depth 3, `contract-only`, contract depth 1, requirement depth 1.

Observed current behavior:

1. `loop._plan_dor_errors` validates scopes, commands, criteria, contracts, graph readiness, and Design readiness, but no sealed delivery mode or pairwise topology is required.
2. `loop.next_action` primes lenses for Execute/Fix and creates a ReviewKernel for Evaluate/EM. `loop.wave` also calls `lens_router.prime_scope`; build dispatch therefore still carries automatic lens work.
3. `loop._evaluation_errors` derives `expected_lenses` from a ReviewKernel and understands a normal complete kernel, but there is no explicit successful empty-collection receipt contract. Producer-receipt absence can flow into evaluator-outage handling.
4. `review.start_review` seals a routed run and slots; `loop.next_action` persists a binding. There is no bounded human-only append-only rebind contract that distinguishes an unstarted kernel from any slot with start/write/collection evidence.
5. Leased lens results have strong host observations in `review.py`, while evaluator and final-EM output contracts say observations are required without one closed live-Codex producer-to-gate path.
6. `design_contract.design_dod_errors` maps criterion text to narrative validation, but does not require exact test files/selectors or a closed producer-consumer wiring ledger. `checkpoint.validate_checkpoint_spec` checks its single focused proof file, not the Design-declared per-AC set.
7. Release provenance, freshness, pushed-SHA classification, workflow matrix, version manifests, archives, docs, and runtime checks exist across scripts and tests, but no one release-green receipt closes them. The 16-commit CI repair delta now fixes the current v2.17.20 generated CLI reference and README window; those changes are integrated inventory, not R-0001 work.
8. `RepositoryManager.acquire_repository` fetches a hosted mirror and then dereferences bare `HEAD`. A newly initialized mirror can retain `refs/heads/master` while the fetched default is `origin/main`, producing the observed ambiguous-HEAD startup failure.
9. `loop.next_action` returns a broad rediscovered payload rather than a stable-reference delta. Existing spend/progress/lens telemetry does not enforce all four wave ceilings or persist the required per-dispatch/thread-type fields. Retro does not compute parallelism factor or longest serial chain.
10. The released v2.17.20 security-methodology reference mandates `references/prompt-injection-defense.md`, but that file is absent from the installed package. The source-to-package-to-security-lens edge is broken.

Sources inspected: `taskplane/loop.py`, `taskplane/build_c.py`, `taskplane/checkpoint.py`, `taskplane/design_contract.py`, `taskplane/review.py`, `taskplane/review_retry.py`, `taskplane/evaluation_output.py`, `taskplane/evaluator_health.py`, `taskplane/repository.py`, `taskplane/preflight.py`, `taskplane/retro.py`, `taskplane/progress.py`, `taskplane/spend.py`, `taskplane/lens_telemetry.py`, `taskplane/command_runtime.py`, `taskplane/pickup.py`, `taskplane/tp.py`, `scripts/ci_evals.py`, packaging scripts, `.github/workflows/ci.yml`, release manifests, release/freshness/repository tests, the accepted retro, and its signed authority amendment.

The engine supplied no `knowledge.current_state`, and the repository has no `architecture.md`. This Design therefore makes only a bounded exact-HEAD claim from the cited files, tests, baseline graph, accepted retro, and signed amendment. The component map, lane barriers, and graph DoR/DoD below are R-0001 authority only; final graph scan and independent review must re-attest them.

### Exact-tip rebind verification

The exact `4a0378e7f080..ecfc48ec2f5f` range contains 16 CI compatibility/regression commits across 60 files. It integrates four relevant classes of inventory: (1) `build_c.py` now receives loop-owned state and event-wait services through `bind_loop_runtime`, removing its lazy imports of `loop`/`review`; (2) Review uses the graph and runtime bundle from the same canonical tree, and degraded graphs retain architecture plus security; (3) evidence, stage-startup telemetry, repository identity, cleanup replay, and their fixtures are repaired; and (4) current plugin descriptions, CLI reference, configuration, and README release window are fresh. None introduces delivery-mode receipts, empty-lens success, human kernel rebind, evaluator/EM producer observation, Design wiring closure, release-green authority, the R-0001 scheduler/budgets, hosted-default preparation, or the missing prompt-injection reference.

The clean authoritative graph was scanned separately before this uncommitted Design overlay, then replayed with the same six recorded R-0001→contract edges. Its module and edge topology is identical to the approved 4a0378e baseline; only source hashes, scanned revision, and content fingerprint changed. The separate `73abaaaad36e18663bde23523d28a4dcef9474e57ce4307721eda3ef02e3d309` 44/152 graph is rejected as a binding because it observed Design overlay files and omitted the recorded requirement edges.

Compatibility is unchanged by the delta: manifests remain v2.17.20; no R-0001 receipt schema, host/plugin capability, N/N-1 matrix cell, migration, deprecation, or sunset rule changed. The approved v2.17.21 forward-only design therefore remains intact.

## Alternatives

### A. Contract-first evidence spine with narrow adapters — selected

Gains: policy is single-sourced; producer and consumer identities are executable; new owners give Plan disjoint scopes; release claims become impossible without complete evidence; each seam has a severed-edge test. Costs: several small modules and additive receipt schemas; a compatibility projection is needed for existing loop output and historical focused receipts. Revisit if measured coupling shows two adjacent owners always change together for two releases; merge only after graph and test evidence.

### B. Patch the existing `loop.py`/`review.py` paths in place

Gains: fewer files and short-term call-site edits. Costs: one shared owner serializes A2, Design enforcement, performance, and release work; mode, budgets, observations, and release authority remain mixed with transitions; severed-edge tests become source-introspection rather than behavior; rollback is all-or-nothing. Revisit only for an emergency one-line refusal fix, never for this multi-contract program.

### C. Put correctness in CI/workflows only

Gains: release checks are visible and parallelizable without local state changes. Costs: build lens creation, empty-lens handling, human rebind, live-host observations, `loop next` size, and repository startup all occur before CI; local tags/install could still bypass workflow-only evidence. Revisit only for redundant remote attestation after local/runtime contracts are closed.

### D. Status quo plus operator discipline

Gains: no implementation. Costs: it reproduces the accepted defects and the fourth unverified ship; it cannot satisfy any of the new enforcement criteria. Revisit never under R-0001.

### E. Cryptographically authenticated actor authority — shelved

Gains: signatures and repository-verifiable keys could establish actor authenticity across untrusted producer hosts. Costs: this adds protected keys, signer/verifier integration, trust bootstrap, rotation/revocation, and a materially larger security surface. The accepted human decision explicitly shelves it: R-0001 adds no signature, MAC, key, signer, verifier, or authenticity claim. Revisit only for a second operator, an untrusted producer host, external evidence verification, or a new human authorization. Until then actor strings are attributed but unauthenticated.

## Selected modules and boundaries

New narrow owners under `taskplane/`:

- `delivery_ports.py`: public Protocols for `Clock`, `EventWaiter`, `ProducerEventSource`, `HostActionCapabilitySource`, `TaskDispatchCapabilityFactory`, `EvidenceStore`, `PlatformCiQuery`, `GitRunner`, and `FaultInjector`. Production and deterministic test implementations share these boundaries.
- `delivery_policy.py`: frozen delivery-mode and wave-budget values; parse/validate factories; `taskplane.delivery-mode-receipt/v1`; budget-stop decision. Modes are closed (`build`, `review`, `design`), with automatic lenses allowed only for Design. Raw Plan mappings never cross this boundary.
- `review_authority.py`: kernel lifecycle projection and append-only `taskplane.review-kernel-override/v1`; accepts only attributed human authority, exact prior/new binding fingerprints, reason, timestamp, and zero-start proof. Any slot start, producer assignment, write observation, collection reservation, or revision makes the kernel immutable.
- `producer_observation.py`: host-neutral `taskplane.producer-observation/v1` for evaluator and EM outputs, bound to run/task/stage/producer/host session or turn/output path/bytes/SHA/schema/contract/revision. Codex and Claude adapters supply host facts; gates consume only validated observations.
- `wiring_closure.py`: closed Design AC test map and producer-consumer edge ledger; exact file/selector resolution; `taskplane.wiring-closure/v1` fingerprint. It is pure validation and imports no loop/review runtime.
- `release_evidence.py`: mutually exclusive feature-green, release-green, and release-override constructors/validators; only release-green exports release authority.
- `plan_topology.py`: exhaustive unordered task-pair classification (`parallel` or `serialized-because-<shared-owner>`), ready-set validation, verification fan-out topology, and critical-path inputs.
- `brief_projection.py`: delta-shaped `loop next` projection containing current action, new evidence, and content-addressed references to unchanged data; canonical token/byte measurement and refusal over 4,000 tokens.
- `dispatch_telemetry.py`: append-only per-dispatch facts, binding budget aggregation, thread type, duration/wait/corrections, progress/completion/attention events, parallelism factor and critical-path calculation.

Existing owners receive bounded adapters only:

- `loop.py`: invokes policy/topology/brief/observation/release validators at existing Plan, dispatch, submission, gate, and next-action seams. It does not implement their schemas.
- `build_c.py`: consumes an approved build-mode receipt and topology-ready set; refuses any lens-worker factory; dispatches all pairwise-disjoint ready work and preserves the existing event wait through the incumbent ecfc48e `bind_loop_runtime` dependency-inversion seam. R-0001 extends that seam and does not restore lazy `build_c → loop/review` imports.
- `review.py` and `evaluation_output.py`: use producer observations and empty-collection receipt; no host-specific authority is inferred from authored JSON.
- `design_contract.py` and `checkpoint.py`: consume the AC test map and wiring ledger; checkpoint reports the exact missing file and selector before command start.
- `repository.py` and `preflight.py`: resolve remote default ref after fetch, prove it exists, set/read the mirror default binding explicitly, then resolve the commit; never dereference an unverified bare `HEAD`.
- `progress.py`, `command_runtime.py`, `spend.py`, and `retro.py`: emit/aggregate the telemetry contract. Telemetry unavailability cannot fabricate green; missing binding budget data blocks continuation for human scope review.
- `pickup.py`: emits first-executing-checkpoint timing markers used by the final cold-start measurement. No pickup behavior is redesigned.
- `tp.py`: exposes only bounded CLI projections for human kernel rebind and evidence/status; generated CLI docs remain a required freshness consumer.

## Delivery sequence and parallel lanes

The program order is binding, while tasks inside a numbered phase use maximum disjoint fan-out.

1. **A2 feature package:** delivery mode + zero-lens collection; review rebind; evaluator/EM producer observations. These are separate write owners and run concurrently after the shared receipt schemas are frozen. A focused exact-SHA receipt makes the package feature-green only.
2. **Forward v2.17.21 release-repair lane:** consume the ecfc48e CI fixes as integrated inventory (do not reimplement them), advance final docs/manifests freshness for v2.17.21, repair the still-missing security reference edge, fix hosted default-branch startup, add release override/evidence, preserve the A5 historical disposition, and converge on one candidate SHA. Cheap docs/manifests/package/default-branch jobs fan out; one full matrix follows; pushed-SHA proof is terminal. v2.17.20 remains unchanged in history.
3. **Design enforcement:** AC test map and wiring ledger validators plus Build DoR/checkpoint adapters. This phase dogfoods the exact R-0001 map in this Design Contract.
4. **Performance/orchestration:** topology, delta brief, telemetry/budget stop, progress wakeups, verification fan-out, Retro metrics. Disjoint module owners dispatch together; adapters into `loop.py` serialize under the named `loop-transition-owner` only.
5. **Cold-start gate:** from a fresh checkout at the same final pushed SHA and empty private Taskplane home, measure command start to the first executing pickup checkpoint. A value below 120 seconds yields the resume receipt. Failure blocks R-0013 and opens only a bounded cold-start repair.

No tag/install/publication occurs merely because a phase is feature-green. The final release-green constructor consumes the final exact SHA, complete wiring fingerprint, terminal full matrix, package/manifests proofs, and successful fetched pushed-SHA proof.

## Receipt and data contracts

All receipts use canonical JSON (UTF-8, sorted keys, compact separators, newline), closed fields, SHA-256 content fingerprints, exact source SHA, and predecessor digests.

`taskplane.delivery-mode-receipt/v1` records requirement, Plan fingerprint, mode, automatic-lens policy, attributed Plan authority, and exact SHA. Build dispatch accepts only `mode=build` and `automatic_lenses=[]`.

`taskplane.empty-lens-collection/v1` records run/task/stage, `expected_lenses: []`, `collected_lenses: []`, schema-valid evaluator/EM output fingerprint, producer-observation fingerprint, and `status: complete`. It is a normal success, never an outage identity.

`taskplane.review-kernel-override/v1` records prior binding, replacement binding, zero-start evidence, human authority receipt, reason, and predecessor digest. Receipts are append-only. The lifecycle predicate checks durable slot and collection evidence, not merely a mutable status string.

`taskplane.producer-observation/v1` binds the real host event to exact output bytes and the expected output contract. Missing, stale, mismatched, ambiguous, or caller-authored observations fail before evaluator/EM gate consumption; they are not translated into a human outage decision.

`taskplane.wiring-closure/v1` contains the exact AC tests and every edge below. Each row names producer, artifact/contract, consumer, required status, and exact severed/freshness selector. A closed fingerprint is carried into Plan, Build, feature evidence, and release evidence.

`taskplane.dispatch-telemetry/v1` records dispatch identity, thread type (`main`, `worker`, `lens`, `evaluator`, `guardian`), input, cached input, uncached input, output, reasoning tokens, start/end/duration, wait duration, correction count, task/dependency/owner, and events. Aggregation stops before another dispatch when elapsed >= 8h, sessions >= 60, total tokens >= 150M, or uncached input >= 25M.

`taskplane.host-action-capability/v1`, `taskplane.task-dispatch-capability/v1`, and `taskplane.platform-ci-proof/v1` are closed authority-boundary schemas described below. All set `cryptographic_authenticity_claimed: false` where applicable. They add continuity, least authority, and independent platform facts without claiming actor authenticity.

## Wiring closure ledger

Each edge is mandatory and has an exact implementation-time test:

| ID | Producer → artifact/contract → consumer | Test |
|---|---|---|
| W01 | Plan gate → delivery-mode receipt → loop/build dispatch | `taskplane/tests/test_r0001_delivery_mode.py::test_sever_delivery_mode_receipt_to_dispatch_fails_closed` |
| W02 | build dispatch → zero lens-worker intent → host dispatcher | `taskplane/tests/test_r0001_delivery_mode.py::test_build_mode_dispatch_creates_zero_automatic_lens_workers` |
| W03 | empty expected set + valid result → empty-collection receipt → Evaluate/EM gate | `taskplane/tests/test_r0001_delivery_mode.py::test_empty_expected_lenses_emits_successful_collection_receipt` |
| W04 | human authority → append-only override → ReviewKernel binding | `taskplane/tests/test_r0001_review_authority.py::test_human_override_rebinds_only_unstarted_kernel` |
| W05 | slot lifecycle events → immutability predicate → override refusal | `taskplane/tests/test_r0001_review_authority.py::test_started_slot_rebind_is_immutable` |
| W06 | Codex/Claude host event → producer observation → evaluator gate | `taskplane/tests/test_r0001_producer_observation.py::test_severed_host_observation_blocks_submission_without_outage_resolution` |
| W07 | Design AC map → Plan task tests → checkpoint path/selector validation | `taskplane/tests/test_r0001_design_wiring.py::test_checkpoint_refuses_named_missing_test_file` |
| W08 | Design wiring ledger → Build DoR → release evidence | `taskplane/tests/test_r0001_design_wiring.py::test_every_changed_producer_has_closed_consumer_edges_and_edge_tests` |
| W09 | `tp.py` parser → generator → `docs/cli-reference.md` | `taskplane/tests/test_release_freshness.py::TestGeneratedCliReference::test_committed_reference_matches_live_parser` |
| W10 | version manifests + changelog → README three-row release window | `taskplane/tests/test_release_freshness.py::TestReleaseWindow::test_readme_keeps_exactly_three_current_changelog_rows` |
| W11 | runtime/skills/hooks/docs sources → both packagers → archives/install consumers | `taskplane/tests/test_r0001_release_green.py::test_runtime_and_public_surfaces_are_in_both_installable_archives` |
| W12 | security methodology → prompt-injection reference → package → security lens loader | `taskplane/tests/test_r0001_design_wiring.py::test_security_methodology_reference_exists_is_packaged_and_loads` |
| W13 | manifests → marketplace/package validators → installed version 2.17.21 | `taskplane/tests/test_r0001_forward_release.py::test_forward_candidate_is_exactly_v21721` |
| W14 | CI workflow → cheap jobs/shards/full matrix → check receipts | `taskplane/tests/test_r0001_release_green.py::test_ci_matrix_and_terminal_full_matrix_are_closed` |
| W15 | feature receipt → Build gate only | `taskplane/tests/test_r0001_release_green.py::test_feature_green_cannot_authorize_release` |
| W16 | wiring + full matrix + package + pushed proof → release-green → tag/install/publication | `taskplane/tests/test_r0001_release_green.py::test_release_green_requires_wiring_matrix_full_matrix_and_pushed_sha` |
| W17 | attributed skipped-proof human authority → released-unverified receipt → audit/history only | `taskplane/tests/test_r0001_release_green.py::test_release_override_records_released_unverified_and_every_skipped_proof` |
| W18 | loop state/new evidence → delta brief → CLI/host consumers | `taskplane/tests/test_r0001_wave_budgets.py::test_loop_next_delta_projection_is_under_4000_tokens` |
| W19 | Plan pair map → direct ready-set → all disjoint worker dispatches | `taskplane/tests/test_r0001_parallel_delivery.py::test_direct_assignment_dispatches_all_ready_disjoint_tasks_simultaneously` |
| W20 | worker runtime → progress/completion/attention events → event wait/orchestrator wake | `taskplane/tests/test_r0001_parallel_delivery.py::test_long_workers_emit_events_and_ready_work_never_idles` |
| W21 | host usage/session receipts → dispatch telemetry → budget stop | `taskplane/tests/test_r0001_wave_budgets.py::test_any_binding_budget_ceiling_stops_for_human_scope_review` |
| W22 | task dependencies/durations → Retro → parallelism factor/longest serial chain | `taskplane/tests/test_r0001_parallel_delivery.py::test_retro_reports_parallelism_factor_and_longest_serial_chain` |
| W23 | remote advertised default → fetched remote ref → mirror binding → checkout SHA | `taskplane/tests/test_r0001_repository_default_branch.py::test_non_master_default_branch_survives_severed_bare_head` |
| W24 | pickup command/timing events → cold-start receipt → R-0013 resume guard | `taskplane/tests/test_r0001_pickup_cold_start.py::test_r0013_resume_refuses_without_passing_cold_start_receipt` |
| W25 | v2.17.20/historical graph disposition → release history/evidence → verifier | `taskplane/tests/test_r0001_forward_release.py::test_historical_graph_revision_is_attributed_without_history_rewrite` |
| W26 | host/plugin adapters → capability handshake → Plan/dispatch/release cutover | `taskplane/tests/test_r0001_compatibility.py::test_mixed_plugin_host_n_n_minus_1_matrix` |
| W27 | checked-in schemas + compatibility policy → diff/N/N-1 receipts → release-green | `taskplane/tests/test_r0001_compatibility.py::test_release_green_requires_compatibility_matrix_receipt` |
| W28 | recorded/live producer event sources → replay/canary receipts → observation/release-green | `taskplane/tests/test_r0001_producer_observation.py::test_recorded_event_source_replay_is_hermetic_and_deterministic` |
| W29 | EvidenceStore prepare/commit/reconcile → atomic receipt lineage → five evidence domains | `taskplane/tests/test_r0001_test_harness.py::test_all_domains_expose_prepare_commit_and_idempotent_recovery_fault_seams` |
| W30 | scheduler admission → reservation + execution DAG → dispatcher/Retro/budget stop | `taskplane/tests/test_r0001_parallel_delivery.py::test_atomic_batch_admission_caps_and_preserves_overflow_ready` |
| W31 | host-private channel → single-use exact-bound action capability → rebind/observation protected entry | `taskplane/tests/test_r0001_host_capability.py::test_rebind_capability_is_single_use_and_exact_bound` |
| W32 | Plan admission + protected release consumer → default-deny dispatch capability/platform proof → worker/release barrier | `taskplane/tests/test_r0001_dispatch_capability.py::test_task_capability_defaults_deny_every_undeclared_surface` |

W12 specifically closes the newly observed released-tip defect. Its positive case proves the source file exists, both package builders include it, an installed archive can resolve it from `security-methodology.md`, and bytes match source. It also requires an independently reviewed document whose semantic contract is explicitly `detect → obstruct → flag`, binds that reviewed digest into wiring closure and release-green, and tests missing/stale/semantically incomplete cases. Design does not invent the document's contents.

## Review-bound architecture and runtime details

### Owners, graph, lanes, and barriers

The nine new owner modules form an acyclic graph. No owner imports a transition adapter. `delivery_ports.py` is dependency-free; `review_authority.py`, `producer_observation.py`, `release_evidence.py`, `plan_topology.py`, and `dispatch_telemetry.py` consume its injected protocols. `delivery_policy.py` feeds only `brief_projection.py` and `dispatch_telemetry.py`; `wiring_closure.py` feeds only `release_evidence.py`; `plan_topology.py` feeds only `dispatch_telemetry.py`. Existing adapters consume owners in one direction. Any undeclared import, reverse edge, cycle, or fan-in/fan-out drift blocks release-green.

Plan must assign exclusive new-owner files to disjoint lanes. Shared adapters serialize behind named barriers: `loop.py` under `loop-transition-owner`; `review.py` and `evaluation_output.py` under `review-integration-owner`; `tp.py`/generated CLI under `cli-owner`; `retro.py` under `retro-owner`; packaging/manifests/workflow under `release-surface-owner`. Owners finish and publish focused evidence before the associated adapter owner begins. This retains fan-out while preventing concurrent edits to shared integration files.

### Atomic evidence, events, and recovery

Authoritative evidence is kept in the managed run store as immutable canonical JSON files plus expected-head CAS pointers, namespaced by caller root, repository fingerprint, and run id. Review rebinds live under `review-authority/<kernel-id>/overrides/`; release evidence under `release-evidence/<target-sha>/<kind>/`; telemetry, observations, reservations, and execution-DAG revisions use the same primitive. Repository `exports/retro/...` files are content-bound projections, not authority, avoiding a Git-SHA self-reference. Prepare writes and fsyncs intent; commit writes and fsyncs immutable bytes then CASes the head; reconciliation is idempotent and rejects forks, gaps, collisions, mixed lineages, and contradictory heads. Public fault seams cover before/after bytes, CAS, domain state, and recovery.

The incumbent `taskplane.wait-policy/v1` remains event-driven with one 1,800-second wait. Dispatch events bind dispatch id, producer/thread id, monotonic sequence, kind, payload digest, and fingerprint. Identical duplicates are idempotent; contradictory duplicates fail; out-of-order events wait in a 256-entry per-dispatch map; each event is capped at 64 KiB. Completion/attention wakes immediately. Timeout or adapter disconnect records `partial-host` terminal attention with missing members; it never becomes success or outage resolution.

### Compatibility and repository preparation

Host/plugin cutover is emit-before-require, not atomic. N=2.17.21 and N-1=2.17.20 readers use schema discrimination and closed objects. New plugin/new host, new/old, old/new, and old/old are all tested. Missing N capability may retain declared feature compatibility but never release-green. Authority schema changes require a new schema id and a machine-readable compatibility diff; legacy focused or unobserved evidence is historical/feature-only and cannot be upgraded into release authority. The checked-in authorities are `design/compatibility.json` and `design/schemas/r0001-evidence-schemas.json`.

`taskplane.repository-preparation-request/v1` fully binds locator, remote, requested/default ref policy, caller root, repository fingerprint, run namespace, retry predecessor, and request fingerprint. `taskplane.repository-preparation/v1` returns a stable status/refusal id, exact retryability, repository/default-ref/fetch/resolved-SHA/checkout facts, predecessor, and fingerprint. Unknown fields fail; idempotent retry requires the same request and predecessor; ambiguous/missing defaults never dereference bare `HEAD`.

### Hermetic seams and atomic admission

AC4's deterministic proof uses an immutable recorded `ProducerEventSource`; a separately classified live Codex canary is a release input. Injected wall/monotonic `Clock` and `EventWaiter` eliminate sleeps. `EvidenceStore` gives deterministic parallel namespaces and scoped teardown. `GitRunner` uses local bare remotes for public-entry W23 coverage. `FaultInjector` enumerates prepare/commit/recovery seams for review rebind, producer observation, telemetry, release evidence, and remote-default preparation.

Admission atomically reserves one tranche with `min(pairwise-disjoint ready count, host free concurrency, remaining 60-session budget, max-in-flight free capacity)`. Overflow remains durably ready. A terminal event releases capacity and admits the next tranche in the same wake transaction. A missing reservation forbids dispatch; exhausted session or missing binding data stops for human scope review. CAS races yield one winner. An immutable, edge-complete execution DAG persists readiness/admission/start/complete/attention/human timestamps across replans. A controlled wide-DAG proof requires elapsed time no greater than critical path plus two fake-clock seconds per tranche and one per terminal event, with zero scheduler-caused idle whenever ready capacity exists.

### Bounded security authority

`taskplane.host-action-capability/v1` is a host-issued, single-use, **non-cryptographic** continuity token delivered through an agent-inaccessible host channel. It binds purpose, opaque nonce, monotonic sequence, host session/turn, run, kernel, task, stage, request/output digest, and contract fingerprint. The protected consumer consumes it atomically before publishing evidence. Replay, cross-run/kernel/task/stage/session/output use, expiry, digest mismatch, duplicate sequence, missing handle, or direct-filesystem injection fails before rebind, observation, receipt, or gate mutation. Its schema fixes `cryptographic_authenticity_claimed: false`: it does not prove the actor identity, and unauthenticated actor strings remain an accepted inherited limitation.

`taskplane.task-dispatch-capability/v1` is closed and per task. It binds run/SHA/Design/Plan/task/stage/reservation/predecessor and explicit allowed tools, read/write paths, Git refs, network endpoints, and credential handles. Unlisted surfaces are denied; no wildcard or inherited authority exists; workers get no release credential and no push/tag/install/publish permission. An outside-model human recheck is required at the protected consumer for every irreversible action.

`taskplane.platform-ci-proof/v1` is created only from an independent hosted-platform query by the protected release consumer immediately before release. It binds repository, protected default branch, exact pushed SHA, workflow run id, check-run ids/names/conclusions, query freshness, and platform response digest. Local receipts cannot populate this port or establish platform/actor authenticity. Any signature/MAC/key/verifier edge or authenticity language is Design drift; the cryptographic option stays shelved.

## Acceptance tests

The canonical map is machine-readable in `design/contract.json`. Summary:

1. AC1: `test_r0001_delivery_mode.py` — Plan receipt, build zero-lens dispatch, severed receipt.
2. AC2: `test_r0001_delivery_mode.py` — valid empty collection, malformed result, no outage path.
3. AC3: `test_r0001_review_authority.py` — human unstarted rebind, started immutability, append-only attribution.
4. AC4: `test_r0001_producer_observation.py` — live Codex evaluator/EM observations and severed observation.
5. AC5: `test_r0001_design_wiring.py` — exact selector resolution, missing-file refusal, Build DoR.
6. AC6: `test_r0001_design_wiring.py`, `test_release_freshness.py`, and release packaging tests — full edge closure and freshness.
7. AC7: `test_r0001_release_green.py` — distinct authority, complete prerequisites, override semantics, consumer refusal.
8. AC8: `test_r0001_wave_budgets.py` — <4,000-token brief, every binding ceiling, complete telemetry.
9. AC9: `test_r0001_parallel_delivery.py` — every pair classified, maximum ready fan-out, named serialization, wakeups, fan-out matrix, Retro metrics.
10. AC10: `test_r0001_repository_default_branch.py` — fetched default before HEAD, non-master sever, ambiguous/missing refusal.
11. AC11: `test_r0001_pickup_cold_start.py` — same-SHA empty-home <120 seconds and resume refusal.
12. AC12: `test_r0001_forward_release.py` — immutable v2.17.20 disposition, exact v2.17.21 forward candidate, historical graph attribution, unchanged verifier strength.

## Failure behavior

All authority failures occur before their consumer action:

- Missing/malformed/mismatched delivery mode: no build dispatch and no lens construction.
- Non-empty build automatic-lens set: no host dispatch.
- Empty expected set with invalid result or missing observation: no success receipt; no outage reinterpretation.
- Rebind without attributed human authority or after any slot start evidence: append-only refusal, prior binding unchanged.
- Missing/stale producer observation: evaluator/EM gate remains closed.
- Missing test file/selector or wiring edge/test: Design/Build DoR remains closed and names the exact identity.
- Missing release evidence: tag/install/publication remains closed. An override records skipped facts but cannot become green.
- Brief >4,000 tokens: emit a bounded refusal plus stable artifact reference; do not truncate authority fields.
- Any binding budget ceiling: stop before new dispatch and request human scope review.
- Missing/ambiguous remote default ref: no bare-HEAD dereference or checkout.
- Cold start >=120 seconds or identity mismatch: no R-0013 resume.
- Missing/replayed/cross-bound/file-injected host capability: no rebind or producer observation and no partial evidence.
- Any undeclared worker tool/path/ref/network/credential or irreversible request: refuse before invocation; workers never receive release credentials.
- Missing/stale/wrong-SHA hosted-platform proof or missing outside-model human recheck: no tag/install/publication; local receipts do not substitute.
- Missing, stale, or semantically incomplete prompt-injection reference: W12 and release-green remain open.

## Observability and budgets

Repository-resident Retro evidence records every dispatch and thread type, including guardian sessions. Counters use host-observed usage when available and fail closed for binding totals when required fields are absent. Signals include delivery mode, expected/created lens counts, kernel binding/override digest, producer-observation status, wiring fingerprint, feature/release disposition, brief token count, elapsed/session/token thresholds, ready/held/running tasks, progress event age, default-branch identity, cold-start duration, parallelism factor, and longest serial chain.

Alerts are loop actions rather than background services: mode/lens contradiction; attempted started-kernel rebind; missing producer observation; broken wiring; release consumer without release-green; brief token overflow; budget ceiling; idle orchestrator with ready work; stale long worker; ambiguous default branch; cold-start failure.

## Rollout and rollback

Roll forward through the five ordered phases. Each new schema is additive and versioned; old focused receipts remain readable but cannot grant new release authority. Host/plugin behavior uses capability handshake emit, observe, then human-authorized require with N/N-1 dual-read rather than an atomic cutover. The first phase runs in refusal-observe mode only in focused fixtures, then enforcement is enabled before another production wave. Protected release consumers switch only after the compatibility matrix passes. Current-only release fixes and inherited CI agent work are integrated, not reimplemented.

Rollback is by phase before release authority: remove the thin adapter and its new module together, restore the prior consumer behavior, and retain all append-only receipts. Never rewrite or delete an override, producer observation, release disposition, or historical graph record. After release consumers require `release-green/v1`, rollback may disable publication but must not accept legacy focused evidence as release authority. v2.17.20 is never retagged or reclassified.

## Graph readiness and done proof

DoR: exact clean HEAD `ecfc48ec2f5f4c25dd0d9bab4d6751bc2f130845` and graph `a6a3c1e72c0c268648e3727cdcec904f60c41442a1f77bf16231bbdb84cd90a6` (50/156, six recorded, complete quality) match; all modules/contract ids exist; the signed order is reflected in dependencies; every AC has exact test file/selectors and controlled dependencies; all 32 wiring edges have a named severed/freshness selector; component fan-in/fan-out and lane barriers are declared; checked-in schemas/compatibility policy parse; the 16-commit CI repairs are mapped as integrated non-replay inventory; no R-0013 or product implementation enters Design.

DoD: rescan the final SHA; compare realized modules/edges to the overlay; require exact selector collection; sever each W01–W32 edge; verify owner cycles/fan-in/fan-out and graph drift; prove lane barriers, CAS recovery, event idempotency/bounds, N/N-1 compatibility, hermetic replay, atomic admission and critical-path bounds; inspect adapters for schema duplication; prove no automatic build lens worker; prove host-capability replay/direct-injection refusal and default-deny worker authority; prove no cryptographic authenticity claim or worker release credential; produce feature-green and release-green as distinct receipts; re-query the protected platform for exact pushed-SHA run/check identities and obtain an outside-model human recheck; run the terminal full matrix; bind the reviewed prompt-injection reference digest through both packages; and retain v2.17.20 plus graph revision 2757822e unchanged.

## Solution-design lens

Self-attested Design evidence is recorded once in the contract and will be rebound to the final content fingerprint after the visual/fingerprint pass. The human approval gate must treat it as self-attested and wait for the independent Design-boundary lens set. There are no open product questions in the accepted requirement and signed amendment.

## Visualization

A sequence/dependency visual is materially useful because five ordered phases contain parallel lanes and two different green authorities. `design/visual.html` will show that order, the disjoint owners, the release barrier, and the cold-start resume gate; it is explanatory only and not authority.
