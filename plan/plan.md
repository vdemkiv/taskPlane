# R-0001 Plan — canonical settings, truthful dashboard publication, CI-first validation, and exact-owned cleanup

Status: drafted for Taskplane mechanical Plan validation and one consolidated human preimplementation approval. This worker does not gate or approve the Plan.

## Outcome and fixed boundaries

Implement the materially revised approved Design without introducing a second settings authority, dashboard state model, dashboard store, or v2 snapshot. The implementation reuses `HostSurfaceSnapshot`, `HostNativeRecovery`, host-capability negotiation, `native_dashboard_projection`, `render_native_dashboard_surface`, `views.deliver_dashboard`, `plan_topology`, and `depgraph`.

The current approved Design fingerprint is `b77e08eb6953e476a6bf76daf8115410b27b62639d1cf5cab831becbee86252e`. This is an approval-continuity rebind, not a new Design semantic approval: the prior `a76c445f70d493456f98d853e18555575b6c850de8b6e0e7d110305150c9f3a4` evidence was rebound after the governed 2.18.4 release projection updated the canonical compatibility policy and release-green schema while retaining the historical 2.18.2 as-built baseline. The earlier `c774497` correction migrated the two deleted renderer-seam proofs to their current `test_status_and_large_delivery.py` and `test_review_production_integration.py` selectors while preserving the already-authorized behavior. It contains 42 modules, 58 edges, nine requirement contracts/resources, and three Design-only dashboard boundary contracts/resources. Task contract entries are exact IDs without relation prefixes. Every task uses local depth 3, contract-only boundaries, contract depth 1, and requirement depth 1.

The single permitted impact call covered 36 exact paths, touched `.github/workflows`, `hooks`, `scripts`, and `taskplane`, reached 28 existing modules, found zero unknown modules, and was neither result- nor depth-truncated. Its graph fingerprint is `896a36584e338e0e9963be4237bd93dbb2eaf92de1908396fb96c555212c0f33` at revision `eae8572b31f2f3cd0b8a46a2cc80a7e1e9bae388`.

Build and Fix launch zero lens workers. Evaluate and Engineering are direct evidence reviews with zero lens workers. No push, merge, version change, tag, publication, installation, or release is authorized by this Plan.

For Taskplane 2.18.4 compatibility, `plan/tasks.json` also projects that already-approved policy through the required top-level `delivery_mode: "build"`, empty `automatic_lenses`, and source-attributed `plan_authority` fields. These fields add no second policy or semantic change: `delivery_policy` remains the detailed canonical Plan declaration, and the projection exists only so the runtime can bind and seal its delivery-mode receipt before Build dispatch.

## Four recorded QUICK Plan lenses — reused unchanged

Exactly four pairwise-disjoint QUICK workers previously ran concurrently against the 42-module/58-edge Design. This acceptance-allocation correction runs zero new lenses and reuses those four receipts byte-for-byte; no old pre-dashboard result and no other lens is used. All 26 issued dispositions remain machine-readable and unchanged in `plan/tasks.json`.

| Lens | Acceptance ownership | Result | Incorporated dashboard evidence |
| --- | --- | --- | --- |
| Architecture | AC-SET1, AC-SET4, AC-SET5, AC-P0 | Attention, 0 blockers | Replace stale topology; retain snapshot v1; use W0 contract → W1 state/projection/host → W2 delivery; keep Design graph, Plan DAG/waves, and repository impact separate; give `host_capabilities.py` and `depgraph.py` one owner each. |
| Security | AC-SET2, AC-SET3, AC-CLN1, AC-CLN2, AC-REL | Pass, 0 blockers | Select/read one source mode with no invalid-v4 fallback; commit outcome before sequence CAS/freeze; fsync generation before pointer CAS; bind host acknowledgement to the exact head and disable stale actions; preserve cleanup/release authority. |
| Testability | AC-TST1, AC-TST2, AC-TST3, AC-CI2, AC-REG | Attention, 0 blockers | Preserve all 24 protected selectors plus large-delivery floors; replace the contradictory error-skips-dashboard test; register failure batching; require real Chrome/Chromium and executable severed edges. |
| Cost / FinOps | AC-CI1, AC-MET | Attention, 0 blockers | Encode canonical bytes once; bound source/visible/omitted/truncation truth; keep the browser cell inside three matrices and 30 runner-minutes; invalidate only its environment fingerprint; never recount metrics from DOM. |

## Acceptance allocation and completed-W0 preservation

Requirement-level acceptance has one evaluation owner per criterion: `SET-CONFORMANCE` owns AC-SET1 and AC-SET4; `SET-SPINE` owns AC-SET2, AC-SET3, and AC-SET5; `TEST-STRATEGY` owns AC-TST1; `TEST-PORTFOLIO` owns AC-TST2; `CI-POLICY` owns AC-TST3, AC-CI1, and the full verbatim AC-CI2; `OWNED-CLEANUP` owns AC-CLN1 and AC-CLN2; `STARTUP-DASHBOARD-STATE` owns AC-P0; `RELEASE-GATE` owns AC-REL; `WAVE-METRICS` owns AC-MET; and `FINAL-CONFORMANCE` owns AC-REG. Every requirement criterion therefore remains covered exactly once.

Adapter, bridge, delivery, browser, and workflow tasks carry explicit task-level DoD statements instead of full criteria whose selectors belong to siblings or successors. Each such DoD is limited to the task's unchanged `tests` command and explicitly disclaims completion of the requirement-level criteria it supports. No red-matrix batching, classified-inventory, one-correction-wave, unchanged-green reuse, settings closure, cleanup, metrics, protected-floor, or release obligation moves earlier or disappears.

The four completed W0 product commits and evidence remain preserved on their existing branches: `PLAN-STABILIZATION` at `fde0a392e6528a6c9fd6b0df33e3dce79d7fd009`, `SET-SPINE` at `566d1653bfd043fac84fb23ca19403a1cbbd6ba0`, `DASHBOARD-CONTRACT` at `0c67aa56703b3b1671bf215c5cafc9ea76c58b83`, and `TEST-STRATEGY` at `eec21e2d7c7d8fae0df8386311a399e98dcad14e`. `SET-SPINE` and `TEST-STRATEGY` retain byte-identical task objects and therefore reanchor unchanged. `PLAN-STABILIZATION` and `DASHBOARD-CONTRACT` retain byte-identical scope, dependencies, test commands, contracts, modules, edges, and implementation commits; only their evaluator-facing criterion allocation changes, so their existing commits/evidence remain the implementation inputs for fresh direct evaluation under the corrected task-local DoD after renewed human Plan approval.

## Build DAG: 17 tasks, 10 waves

All scopes are pairwise-disjoint. Shared generators, fixtures, manifests, ledgers, workflows, package files, and current-pointer authority have one owner. Every pairwise-disjoint task at the same dependency frontier dispatches concurrently.

1. **W0 foundations:** `PLAN-STABILIZATION` ∥ `SET-SPINE` ∥ `DASHBOARD-CONTRACT` ∥ `TEST-STRATEGY`. Each foundation exercises only an existing test file or a not-yet-existing test file inside its own unique scope, so executable test-artifact inference cannot add a contradictory predecessor.
2. **W1 disjoint adapters:** `STARTUP-DASHBOARD-STATE` ∥ `DASHBOARD-PROJECTION` ∥ `DASHBOARD-HOST-SURFACES` ∥ `CI-POLICY`.
3. **W2 dashboard publication:** `DASHBOARD-DELIVERY`.
4. **W3 conformance and cleanup:** `DASHBOARD-CONFORMANCE` ∥ `OWNED-CLEANUP`.
5. **W4 authoritative CI:** `CI-WIRING`.
6. **W5 metrics:** `WAVE-METRICS`.
7. **W6 release gate:** `RELEASE-GATE`.
8. **W7 portfolio:** `TEST-PORTFOLIO`.
9. **W8 settings closure:** `SET-CONFORMANCE`.
10. **W9 final join:** `FINAL-CONFORMANCE`.

The dashboard ownership is explicit:

- `DASHBOARD-CONTRACT` owns existing `HostSurfaceSnapshot` v1 identity, ordering, fingerprint, and canonical-value fixtures.
- `STARTUP-DASHBOARD-STATE` owns one-read source selection, sequence allocation, canonical assembly, and publication callbacks for successful and failed gates, worker/lens member terminal, cancellation, interruption, timeout, handoff, replan, resolve, and recovery.
- `DASHBOARD-PROJECTION` owns native rendering plus separately truthful Design graph, current Plan task DAG/waves/approval, and repository module-impact components.
- `DASHBOARD-HOST-SURFACES` owns `HostNativeRecovery`, host acknowledgement/fallback, hooks, and declarative host flow surfaces; it consumes but does not edit the capability interface or the `DASHBOARD-DELIVERY`-owned `test_status_and_large_delivery.py` consumer proof.
- `DASHBOARD-DELIVERY` owns static delivery, single-document HTML, decoded equality, content-addressed generation, current-pointer CAS, publication receipt, and stale/unverified/static action disabling.
- Replan correction: the missing publication-receipt selector is landed test-first in the predecessor-owned `test_r0001_dashboard_pipeline.py` before fresh approval. `DASHBOARD-DELIVERY` changes only its own producer and test files, then runs that exact predecessor-owned selector; file ownership therefore remains single and its dependency names the required serialization.
- `DASHBOARD-CONFORMANCE` owns the isolated real-browser DOM/SVG proof and its fixtures.
- `PLAN-STABILIZATION` alone edits `depgraph.py`; dashboard projection consumes repository-impact facts without redefining them.

The current legacy dashboard cannot yet render this Plan DAG and these waves. Until Build lands `DASHBOARD-PROJECTION` and `DASHBOARD-DELIVERY`, the Plan approval surface must report that limitation as static/unverified, display this exact ten-wave DAG from `plan/tasks.json`, and disable approval/mutation actions unless the current Plan fingerprint and exact human approval receipt are independently verified. It must never label the Plan “approved” from prose or stale artifacts.

## Bounded PLAN-STABILIZATION bridge

The known validator defect remains binding: `design_plan_errors()` asks `depgraph.scope_modules()` for coverage, while `modules_for_scope()` collapses exact Python file scopes to the directory module. The revised Design now has 20 file-granular overlay IDs, so honest exact scopes cannot satisfy the current pre-Build gate.

`PLAN-STABILIZATION` exclusively owns `taskplane/depgraph.py`, `taskplane/design_contract.py`, and one exact regression file. Both of its exact selectors live in that same-slice file; the unchanged AC-CI2 failure-batching selectors remain exclusively with downstream owner `CI-POLICY`. Its temporary `new_modules` entries enumerate only those 20 approved overlays. Actual files remain with their semantic task owners. This is a compatibility declaration for graph surfaces, not duplicate edit authority, directory collapse, an unknown-module escape, or a fallback matcher.

Its task-level DoD proves only the approved exact-file overlay matcher and the already-required third-return stabilization bridge through `test_file_granular_overlay_modules_are_covered_by_exact_scopes` and `test_third_plan_return_requires_one_stabilization_successor`. It does not claim the red-matrix inventory, classification, batching, correction-wave, or unchanged-green portions of AC-CI2; those remain wholly owned and evaluated by `CI-POLICY` through `test_ci_failure_batching.py`.

Removal condition: once the corrected exact-file matcher and the revised 58-edge authority floor are the active baseline, every future Plan must remove these compatibility declarations. The correction must preserve directory-module coverage, unknown/broad-scope refusal, scope confinement, and duplicate-owner checks.

## Named serialization and parallel seams

Serialization is limited to exact authority or data dependencies:

- Settings schema and fail-closed validation precede every state read, resource, contract, dispatch, dashboard, cleanup, CI, and release effect.
- Authoritative event/outcome receipt commit and fsync precede dashboard publication.
- One legacy-or-v4 source-mode selection and one read precede snapshot assembly; corrupt, ambiguous, missing, or mismatched v4 never falls back or mixes cards.
- Sequence allocation uses CAS per stable identity before snapshot freeze.
- After freeze, native, JSON, Markdown, HTML, Design, Plan, and repository-impact projections may run concurrently from the same immutable canonical bytes. They must not repeat semantic reads.
- `HostNativeRecovery.apply` serializes only per stable identity.
- Decoded equality, DOM/SVG proof, host acknowledgement or explicit limitation, and freshness join before complete-generation fsync.
- Generation fsync precedes the expected-head current-pointer CAS; unrelated projection work is not globally locked.
- Original outcome/evidence and a durable publication-replay obligation precede cleanup. Different exact resources clean concurrently; the same resource uses its manifest lock/CAS and worktree cleanup is last.
- Producer bytes precede consumer freshness and deliberately severed-edge tests.
- Coverage adjudication and all protected floors precede removal; one portfolio ledger owner prevents concurrent deletion authority.
- Candidate freeze precedes all authoritative cells. Browser, primary test, compatibility, quality, and package cells run concurrently where selectors/files are disjoint; terminal aggregation waits for matching receipts.
- Exact terminal CI, zero-leak cleanup, current dashboard evidence, sealed metrics, and exact protected-main green for any merge-created SHA precede a later human-authorized version/tag/release.

## Per-task test contract

Every task has one executable command string of exact `file.py::selector` node IDs. Each task names changed producers, all consumers, fingerprint freshness, a deliberately severed-edge behavior, same-slice fixtures, and the four closed failure classes: product, test, infrastructure, and environment. No correction starts until every direct failure has one class, reason, owner, and cluster.

Dashboard selectors include:

- `test_host_native_capabilities.py` for the foundation's immutable snapshot contract, including restart identity/fingerprint and contradiction rules; owning producer `STARTUP-DASHBOARD-STATE` then realizes those same approved assertions in `test_r0001_dashboard_pipeline.py` together with one-read source-mode validation and publication receipts. Every later command that cites that new pipeline file already depends on its owner.
- `test_dashboard_v2.py` and `test_worker_contract_lifecycle.py` for every successful/failed/member-terminal/cancellation/interruption/timeout/handoff/replan/resolve/recovery publication edge.
- `test_dashboard_phase_graphs.py` for Design graph, Plan DAG/waves/approval state, and separately truthful bounded module impact.
- `test_dashboard_delivery_html.py::test_delivery_contains_exactly_one_doctype_html_head_and_body` and `test_status_and_large_delivery.py::test_runtime_output_adapter_uses_fresh_receipt_or_accessible_fallback` replace the removed view seam with exact canonical-delivery and failure-fallback proofs; `test_host_native_dashboard.py`, `test_status_and_large_delivery.py`, `test_stage_bounded_views.py`, and `test_stage_cross_host.py` retain accessibility, large delivery, legacy/v4, and cross-host behavior.
- `test_host_native_compatibility.py` for monotonic recovery, same-snapshot replay, capability fallback, and host acknowledgement.
- `test_dashboard_browser.py` for real Chrome/Chromium DOM replacement, stale action disabling, SVG edges, and one HTML document.

The browser harness is dependency-free and CI-only. It uses an existing declared Chrome/Chromium binary, a standard-library loopback server plus fail-closed `file://` behavior, and a closed browser-environment receipt. A missing declared browser is an environment failure, never a skip. Harness or loopback failure is infrastructure. No browser package/download enters runtime or package manifests.

## Validation progression and unchanged-green reuse

Local validation is static plus each task’s exact fast selector command only. Broad local runs refuse by default.

Progression is exactly:

1. Static schema, inventory, prohibited-read, compile, Ruff, strict mypy, graph, workflow, and package membership checks.
2. The changed producer’s exact selector.
3. Its complete changed-radius consumers, freshness checks, and deliberately severed-edge mutations.
4. One settings-derived proportional shard set.
5. One frozen-SHA authoritative GitHub Actions matrix.

An unchanged green layer is cited, never rerun. Reuse requires equality of source, tests, settings, inventory, selector, radius, shard plan, runner, and environment fingerprints. The browser cell additionally binds executable path, browser version, flags, fixture-server identity, snapshot, dashboard artifact, and selector fingerprints; browser drift invalidates only that cell.

Every red matrix is inventoried once, classified once, clustered once, and corrected in one wave. No rerun occurs without a changed product byte, evidence byte, or named environment condition. The already-required third Plan return is represented only by `PLAN-STABILIZATION`.

The CI planner emits one exact-SHA pytest suite plus disjoint quality/package and browser jobs, with settings-derived timeouts, PR-only same-group cancellation, and durable all-outcome receipts. Release validation consumes those real job receipts directly; there is no terminal join job or compatibility alias. Protected-main and release runs never cancel in progress. The isolated browser selectors are allocated exactly once, not repeated inside the pytest suite.

Targets remain: first matrix within two hours of `integration_ready_at`; at most three matrices; p50 at most 10 minutes; p95 at most 15 minutes; no more than 30 raw runner-minutes; and at least 4.0x parallelism when four shards exist.

## Evidence-based portfolio reduction

Baseline: 266 files, 4,909 cases, and 95,601 test LOC. Target: at most 230 files and 4,200 cases, at least six evidenced redundant families removed, and zero protected loss.

Each removal row binds frozen SHA, current contract or approved obsolete-contract authority, retained exact selector, mutation/severed-edge detection, fixture/generator/manifest consumers and digest, owner, and before/after files/cases/LOC. Names and counts are candidate discovery only, never deletion authority.

All 24 exact AC-REG selectors run before and after pruning. Additional protected dashboard floors remain until equivalent current-contract replacements are proven: small/large dashboard equality and delivery, canonical UTF-8 size boundary, production refresh delivery, and fresh-receipt/accessibility fallback. Dashboard-version, host-parity, stage-view, view-seam, worker lifecycle, native recovery, security/human authority, portability, cleanup, CI supply-chain, and release floors cannot be count-pruned.

## Exact-owned cleanup on every outcome

Root owns the resource manifest, terminal CAS, destructive authority, replay, and cleanup receipt. Workers reserve and attest exact run/task/attempt resources; hooks report facts; settings select bounded policy but cannot mint deletion authority.

Before creation, register exact worktree, worker contract, process/group, browser, cache, generated state, and test-artifact identities plus containment, nonce/generation, evidence references, dependency edges, and receipt digest. For success, failure, cancellation, interruption, timeout, handoff, or recovery:

1. Seal the original outcome and authoritative dashboard event.
2. Copy, hash, fsync, and independently resolve evidence plus the publication-replay obligation outside deletable roots.
3. Clean reverse dependencies: exact process/browser trees, contracts, artifacts/caches/generated state, then the registered clean worktree.
4. Revalidate live identity before each action, journal/postcheck after it, and replay from durable state.
5. Join results and prove exactly zero owned leaks.

Foreign, dirty, staged, untracked, unmerged, locked, symlinked, hard-linked, relocated, containment-invalid, PID-reused, wrong-generation, active, ambiguous, or unsupported no-follow targets are refused and preserved. Refusal records recovery/leak evidence and blocks green sign-off/release. Cleanup or dashboard publication failure is secondary evidence and never overwrites the original outcome.

## Metrics, release, and terminal gates

One redacted, non-cumulative wave receipt binds exact run/candidate/settings/source interval, counting method, target, ceiling, result, sample size, named serialization, and digests of CI, dashboard publication, cleanup, portfolio, token/session/worktree, and dispatch evidence. It preserves the original baselines: 258 settings-spread files; 266 tests/4,909 cases/95,601 LOC; first CI at 31h37m; 12 matrices/9 red; about 15 wall minutes, 38 runner-minutes, 2.59x parallelism; 132 worktrees/17.3 GB stale state; 540.3M observed tokens versus a separate 1.292B archive upper bound; 40h35m session.

Final targets include 100% settings ownership and zero duplicate defaults; exact p95 ≤60 seconds and radius/proportional p95 ≤5 minutes; zero leaks and active worktrees ≤ active shards + 1; token target 100M total/15M uncached and ceiling 150M/25M; active delivery ≤8 hours and phase through Retro ≤12 hours; ≤24 planned sessions and fail-closed ceiling 60. Billing, observed usage, archive upper bounds, and DOM-visible counts remain separate truth classes.

After exact and proportional evidence is green, freeze one SHA and run one authoritative GitHub Actions workflow. Any source/test byte change creates a new candidate. If merge creates a protected-main SHA, run the exact workflow for that SHA. Only direct exact-candidate job receipts, least privilege, immutable pins/locks, credential-empty untrusted jobs, exact head/first-parent topology, dashboard/browser receipts, package provenance, metrics, cleanup receipt, and zero leaks may support later human-authorized version/tag/release.

Evaluate then Engineering inspect the exact diff and bound evidence directly with zero lenses. Engineering sign-off is required before Retro. Retro consumes sealed receipts without recalculation.

## Gate risks and stop conditions

Stop and return for correction on stale Design or Plan fingerprint/topology; a second settings/dashboard/cleanup/CI authority; any scope overlap; unknown/truncated graph surface; legacy/v4 fallback or mixed cards; render-time sequence; stale/conflicting/post-terminal projection; early pointer advance; unbound host acknowledgement; stale/static approval actions; Plan approval without an exact receipt; sampled impact presented as total or as Design/Plan topology; graph-excluded fixtures escaping inventory; repeated semantic reads; browser skip/simulation/download/leak; browser selectors duplicated in ordinary shards; count-only pruning or protected loss; unclassified failure; unchanged-green rerun; unsafe cleanup or nonzero leak; billing/provenance conflation; protected-run cancellation; branch green used as protected-main truth; or compatibility stabilization becoming a permanent broad fallback.

There are no current Plan-content blockers. Because evaluator-facing task contracts changed after the prior approval, the corrected Plan requires a fresh human Plan approval before any Build continuation. The next action is Taskplane’s targeted mechanical Plan gate by the root orchestrator, followed by repair of exact returns if any, then root presentation for that renewed approval.
