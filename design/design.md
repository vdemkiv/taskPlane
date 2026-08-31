# R-0001 Design — canonical settings and dashboard state, CI-first testing, and owned cleanup

## Decision

Use one repository-shipped canonical document, `taskplane/operational-settings.json`, and one dependency-inward standard-library loader, `taskplane/settings.py`. The document is the sole persisted owner of configurable defaults and override classes. The loader owns typing, validation, precedence, normalization, compatibility, and receipts, but contains no duplicate values.

Every Python entry constructs one immutable `SettingsContext` before state or dispatch. Non-Python hooks, JavaScript workflows, and GitHub Actions consume a sealed settings projection and verify its digest; they do not own operational defaults. Existing storage, authority, handoff, worktree, process, package, CI, and release mechanisms remain behavior owners behind injected adapters.

The same settings digest binds the test-strategy contract, frozen CI candidate, worker/handoff envelopes, suite cache, cleanup manifest, dashboard, metrics, package, and release evidence. This creates one control spine without creating another source of truth.

Dashboard delivery gets a second, separate canonical spine for presentation state without becoming workflow authority: `loop_status` selects and validates one legacy-or-v4 source mode, reads the committed state once, and creates exactly one existing `HostSurfaceSnapshot` v1 per event. Required publication data—including committed `generated_at`, settings digest, source provenance, phase-graph digests, and truthful bounded counts—lives in its canonical `values` and therefore in its fingerprint. The same snapshot fans out to `HostNativeRecovery` and native projection on one sibling path and `views.deliver_dashboard` on the other. No renderer, compatibility path, browser, or host adapter may reconstruct semantic state.

This directly corrects the dashboard defect discovered at the Design gate. Successful and failed gates, worker/lens terminals, cancellation, interruption, timeout, handoff, replan/resolve, and recovery all publish through one event adapter. An already-open surface advances only on a higher accepted sequence with exact host acknowledgement; otherwise it visibly declares `STATIC`, `UNVERIFIED`, or `STALE`, shows its identity/sequence/fingerprint/generated-at evidence, and disables approval or mutation delivery.

The work is a contract change, not a configuration cleanup. A partially wired consumer, invalid value, incompatible host, stale digest, or unsafe override stops before any state write, worktree, contract, process, or dispatch.

## Why this is the smallest sound approach

We compared four settings placements and four real dashboard-delivery options. The selected combination is the canonical document plus incumbent adapters and the existing v1 host snapshot plus sibling native/static adapters:

1. **Canonical document + typed loader + injected incumbent adapters — selected.** It removes duplicate value ownership while preserving mature proof mechanisms. Its cost is a broad but mechanical adapter cutover.
2. **Generated Python/JavaScript/YAML/hook copies.** Static hosts consume them easily, but stale derived copies can sever authority. Use a generated projection only when a host proves it needs a literal before Python can run, and stamp/verify the source digest.
3. **Stage-local typed settings.** It is locally cheap but cannot prove one owner, portable bytes, deterministic precedence, or one digest across flows.
4. **Settings daemon plus runtime rewrite.** It offers dynamic policy but adds authentication, availability, deployment, Windows, and recovery domains while discarding proven mechanisms. It is disproportionate to this requirement.

For dashboard delivery, patching only `report_widget`/refresh triggers is smaller but preserves multiple read moments, legacy/v4 disagreement, and presentation-owned semantics. A browser polling store can appear live but creates a forbidden second state authority and duplicates recovery/order rules. A renderer/service rewrite is wider than the defect. Reusing the existing `HostSurfaceSnapshot`, `HostNativeRecovery`, host-capability negotiation, native projection/rendering, `deliver_dashboard`, `plan_topology`, and `depgraph` is the smallest sound option. A new snapshot v2 is unnecessary because v1 `values` already participate in the canonical fingerprint.

Revisit the selected placement only if a non-Python consumer must independently start without a Python-produced envelope, or multiple repositories require continuously revoked remote policy.

## As-built evidence

Taskplane 2.18.2 has no configuration file. The repository scan found at least 65 distinct `TASKPLANE_*` names in 37 operational files, while Product measured setting-like candidates across 258 code, documentation, and workflow files. Direct or duplicated values occur in:

- `taskplane/tp.py`: parser and command defaults, graph depth, verification timeouts, fix-cycle limits;
- `taskplane/taskplane_lite.py`: model/reasoning defaults, suite-cache policy, store/home/host selection, orphan TTL;
- `taskplane/loop.py`: stage-native mode, consolidated flow, cleanup, session and dispatch policy;
- `taskplane/storage.py` and hook bridging: duplicated home/store resolution;
- hooks: duplicated launcher fallbacks and host timeouts;
- skill flow graphs and role text: route and lifecycle policy;
- JavaScript workflows: retry and fan-out defaults;
- GitHub Actions and local CI: independently hard-coded matrix, shard, selector, timeout, and concurrency policy;
- packaging and release: overlapping membership, compatibility windows, versions, and freshness limits.

Runtime observations such as session/task identity, `PATH`, locale, host-capability markers, hook events, and credentials are not settings. They enter through typed observation adapters, cannot own defaults, and may only validate host compatibility or receipt evidence.

The test baseline is 266 files, 4,909 cases, and 95,601 Product-baseline LOC. The prior wave used 12 matrices, nine red, with the first hosted CI after 31h37m. Cleanup ended with 132 worktrees and about 17.3 GB of stale state. Those wall-clock and lifecycle facts are the primary design driver.

Dashboard RCA found an independent high-impact delivery problem. `loop_status.with_dashboard` refreshes only successful return payloads; worker/lens terminal and lifecycle callbacks do not publish. `HostSurfaceSnapshot.create`, `HostNativeRecovery`, `native_dashboard_projection`, and `render_native_dashboard_surface` are tested but unwired in production. `views.refresh_views` instead reads workspace state through the legacy renderer and puts rendered HTML into the supposed semantic model. It then passes a complete standalone document into another complete-document wrapper, producing nested doctypes/html/body. The automatic “dependency graph” is only a silently sampled repository module impact view, not the existing 36-module/37-edge Design graph or the 13-task/eight-wave Plan topology. Static `file://` delivery has no live acknowledgement or freshness state, so an open page can silently retain an obsolete gate.

## Canonical settings contract

The closed document includes:

- stage model aliases and reasoning effort;
- Product/Design minimum-sufficient quick routes, Plan exactly three or four quick lenses, and zero lens workers in Build, Fix, direct Evaluate, and Engineering;
- Build shard count and maximum concurrency;
- test backend, selection, sharding, cache, and validation progression;
- timeouts, stage/wave budgets, token ceilings, matrix ceilings, and wall-clock targets;
- workflow transport and the zero-inherited-turn worker contract;
- cleanup outcomes, resource kinds, grace periods, retention, refusal, and leak policy;
- migration window, safe override classes, and observability bounds.

Default route bounds are Product 1–2 quick lenses, Design 1–3 quick lenses with mandatory `solution-design`, Plan 3–4 quick lenses, and zero lens workers elsewhere. Build and CI concurrency defaults live only in the canonical document. Policy invariants that can never be overridden—human authority, protected-main release truth, exact ownership, and fail-closed proof—remain compiled protocol constraints and are inventory-dispositioned as non-settings.

Resolution order is:

1. an exact-authorized CLI overlay for a key that requires authority;
2. an allowlisted safe CLI overlay;
3. an allowlisted safe environment overlay or one-release legacy alias;
4. the canonical value.

Overlays are ephemeral and never persisted as stores. Lower concurrency/budgets and supported model aliases within stage floors may be safe. Backend or transport changes, broader selectors, higher budgets, expanded scope, or weakened lens, proof, cleanup, authority, or release policy require exact authority or are forbidden. Modern and legacy aliases at the same tier must agree.

`settings_legacy.py` owns no defaults. For one released compatibility window it maps documented variables, artifacts, CLI forms, and numeric/cache-busted package versions into the same overlay vocabulary, emits a deprecation/source receipt, and rejects conflicts or expiry. It cannot talk directly to a consumer.

Portable effective bytes contain host-neutral values and abstract model aliases. Host capability facts are validated separately and contribute only a digest to the receipt. Equal inputs produce byte-identical effective settings on every supported host; an incompatible host stops rather than falling back.

The redacted `taskplane.effective-settings-receipt/v1` binds canonical source and effective digests, applied source classes, engine/package identity, normalized version, host-capability digest, exact authority when used, and redaction proof. It excludes secrets, raw environment values, absolute paths, workstation identity, prompts, and diffs.

## Modules and interfaces

New modules:

- `taskplane/operational-settings.json` — sole persisted settings values and override classes;
- `taskplane/settings.py` — frozen types, validation, canonical bytes, digest, projections, and receipt;
- `taskplane/settings_legacy.py` — one-release alias adapter with zero defaults;
- `taskplane/settings_inventory.json` — machine-checkable disposition ledger, not a settings source;
- `taskplane/test_strategy.py` and `taskplane/test_portfolio.json` — exact selectors, producer/consumer and pruning authority;
- `taskplane/ci_policy.py` and `taskplane/ci_failure_batching.py` — candidate/shard/reuse/classification contracts;
- `taskplane/owned_cleanup.py` — orchestrator-owned resource manifest, typed handlers, replay, and leak proof;
- `taskplane/wave_metrics.py` — one non-cumulative metrics receipt.

The loader API is:

`load_effective_settings(source_root, cli_overlay, env_overlay, host_observations, authority_receipt=None) -> SettingsContext`

The loader imports no CLI, loop, storage, hook, workflow, or authority implementation. It validates JSON, CLI/environment inputs, persisted receipts, and host observations at their trust boundaries. Static typing is not treated as runtime validation. There is no global mutable settings singleton or import-time configuration.

`tp.py` loads before parser side effects. `preflight.py` proves settings with repository/workspace, hook, launcher, session, and store. `loop.py`, `stage_handoff.py`, worker contracts, cache, cleanup, dashboard, telemetry, CI requests, and release evidence receive the immutable context or digest. JavaScript and host manifests validate closed projections.

Both deterministic marketplace packagers explicitly include and verify the new Python/JSON files and the canonical source digest in clean extracted archives. This project does not build a wheel; adding one is not justified by R-0001.

## Canonical dashboard publication and phase topology

No new dashboard state module or store is created. Existing components are reused with missing adapters added inside their current boundaries:

- `taskplane/loop_status.py` owns the pure snapshot assembler and transition/lifecycle interception;
- `taskplane/host_native.py` retains the existing immutable v1 snapshot/event contract;
- `hooks/host_native_runtime.py` retains ordered recovery and host reprojection;
- `taskplane/host_capabilities.py` selects presentation only from source-attributed capability evidence;
- `taskplane/dashboard.py` renders the supplied native/static projection and performs no workspace semantic reads;
- `taskplane/views.py` publishes content-addressed generations and one atomic current pointer;
- `taskplane/plan_topology.py` validates Plan topology without owning it, while `taskplane/depgraph.py` provides repository-impact facts;
- `design/contract.json` and `plan/tasks.json` remain the sole phase-graph artifacts.

Snapshot identity is `(workflow_id, run_id, target, revision)`. Its `sequence` is durable and strictly monotonic per identity, allocated from the committed event journal rather than a render clock. The committed event timestamp becomes canonical `values.generated_at`; settings, source-mode, loop/stage, artifact, Design graph, Plan topology, and impact digests are also canonical values. A duplicate `(sequence, fingerprint)` is an idempotent no-op. A lower sequence is stale; equal sequence with different bytes is contradictory; identity change and nonterminal update after whole-run terminal are rejected before projection. A worker or lens terminal updates a component but does not close the workflow; only whole-run completed, failed, cancelled, or closed seals recovery.

Publication order is authoritative event/terminal receipt commit and fsync, one source-mode selection/read, sequence CAS, snapshot freeze, pairwise-disjoint projections, decoded-equality/DOM checks, generation fsync, then atomic current-pointer CAS. A crash after workflow commit but before publication leaves the old page explicitly stale and creates a replay obligation; SessionStart or the next entry republishes the same sequence/fingerprint. Renderer failure may make HTML unavailable but must still publish current canonical JSON and complete Markdown. Partial bundles are unreachable because the pointer advances last.

`taskplane.dashboard-publication-receipt/v1` binds event outcome, snapshot identity/sequence/revision/generated-at/fingerprint, settings and phase-graph digests, source mode, artifact hashes, prior/current head, host capability and acknowledgement or static limitation, freshness state, decoded equality, DOM/SVG validation, rejection/recovery evidence, and its own fingerprint. Portable bytes contain logical artifact ids, never absolute host paths, prompts, secrets, raw environment, or workstation identity.

Design topology appears from Design onward and renders the exact `proposed_modules` and `proposed_edges` or truthfully paginates them with complete semantic details. Plan topology appears from Plan onward with every task, dependency, and declared wave. It says `PROPOSED/AWAITING APPROVAL` until an exact Plan approval receipt permits `APPROVED`. Repository module impact is a separate component labelled exactly “Repository module impact (local depth 3)” and exposes `source_total`, `visible_count`, `omitted_count`, `source_truncated`, `depth_truncated`, `policy_blocked`, `unknown`, depth/boundary policy, and pagination. A sampled SVG never reports visible nodes as the total or substitutes for a phase graph.

Exactly one layer owns the HTML document shell. Renderers return fragments only; delivery emits one doctype, html, head, and body and embeds one base64 canonical snapshot. JSON, Markdown, HTML, Codex, and Claude decode to the same snapshot fingerprint. A fresh host bridge may replace an open DOM only after acknowledging that fingerprint. Without it, `file://` immediately presents a read-only static limitation and uses bounded same-URL reload; expiry, invalid identity/hash, impossible clock, or failed reload raises a semantic alert and disables every decision/mutation action. Terminal pages retain evidence and do not age into a false actionable state.

## Test-strategy and portfolio contract

Validation progresses once per unchanged evidence layer:

1. static schema, inventory, prohibited-read scan, compile, Ruff, strict mypy, graph, and package membership;
2. exact acceptance selector for the changed producer;
3. changed-radius consumers plus freshness and deliberately severed-edge checks, with interface fixtures in the same slice;
4. one settings-derived proportional shard set for impacted current contracts;
5. one exact frozen-SHA authoritative GitHub Actions matrix.

Each layer seals source, settings, selector, dependency radius, runner, and environment fingerprints. An equal green fingerprint is cited, not executed again. Source, test, settings, inventory, selector, or shard-plan bytes invalidate terminal authority. Broad local execution is refused unless canonical configuration and exact authority explicitly select it.

Every failure is classified before correction as `product`, `test`, `infrastructure`, or `environment`. Each has one reason, owner, and cluster. A red matrix is inventoried once and produces one correction wave. A third Plan return becomes one bounded stabilization successor for coupled generators, goldens, checksums, fixtures, manifests, and ledgers.

Every removed test or family records the current contract it claimed, an exact retained selector with mutation/severed-edge detection or an approved obsolete-contract id, fixture/generator consumers and digest, before/after cases/LOC, owner, and candidate SHA. Counts are consequences, never deletion authority.

Candidate families include version/fix history replay, requirement-wave replay, Engineering tranche decomposition, duplicated lens routing, dashboard versions, host-parity duplication, field-report reproductions, and stale fixture/golden/corpus ceremony. At least six families must be adjudicated and removed while reaching no more than 230 files and 4,200 cases.

Protected floors retain security and consolidated authority; host/session/store/worker identity; cross-host encoding/path portability; receipt tamper/replay; cache and cleanup races; CI immutable pins, permissions, and hash locks; and release version, exact-head, first-parent, protected-main, tag, and provenance truth. Every protected entry in `design/contract.json` is an exact `file.py::Class::test` or `file.py::test` selector; no whole-file reference is used as acceptance authority.

Dashboard producers and every consumer edge are separately machine-mapped with exact selectors. Same-slice fixtures cover the final Design graph, Plan 13-task/eight-wave topology, complete/truncated impact, success/failure/terminal event streams, stale/duplicate/contradictory/post-terminal updates, nested-document rejection, and host acknowledgements. Deliberately severing a gate or worker callback, snapshot consumer, graph source, count field, DOM fingerprint, SVG node/edge, or current-pointer edge must fail before correction. Dashboard failures follow the same product-versus-test-versus-infrastructure-versus-environment classification gate as every other failure.

## CI and release contract

A read-only settings-plan job invokes the loader and emits one closed disjoint shard projection for a frozen source/settings/test/shard fingerprint. At least four independent shards execute concurrently when four exist. Compatibility cells, quality, package smokes, graph verification, zero-egress proof, and release prechecks run in parallel where their inputs do not overlap.

One disjoint authoritative browser shard uses a no-dependency harness with the GitHub-hosted Chrome/Chromium executable against a loopback-only fixture server and the `file://` fallback. It records executable, version, flags, source/settings/snapshot fingerprints, and outcome in `taskplane.browser-environment-receipt/v1`; no declared browser is a red environment failure, never a skip. It proves real DOM replacement only for a newer accepted sequence, stale action disabling, exact SVG nodes/edges/count semantics, and one valid document. Local browser execution is optional targeted feedback; no browser package becomes a runtime or marketplace dependency.

Superseded pull-request heads may cancel only inside their exact PR group. Protected-main and release groups never cancel in progress. Every job has a settings-derived timeout and writes a candidate-bound receipt. Missing, duplicate, overlapping, stale, mismatched, or improperly cancelled shards block aggregation.

Targets are first CI within two hours of integration readiness, at most three authoritative matrices, p50 at most ten minutes, p95 at most fifteen minutes, at most 30 runner-minutes, and at least 4x effective parallelism with four shards.

Least privilege, immutable actions, credential-empty untrusted jobs, hash-locked dependencies, exact-head proof, first-parent compatibility, and exact protected-main green remain mandatory. A merge-created SHA is new release authority; it cannot inherit protected-main truth from a branch SHA.

## Exact-owned all-outcome cleanup

The root/orchestrator owns an append-before-use `taskplane.owned-resource-manifest/v1`, terminal compare-and-swap, replay, and cleanup invocation. Workers may reserve and attest only resources in their exact run/task/attempt. Hooks report terminal facts; settings select bounded policy; neither creates destructive authority.

Every worktree, worker contract, process/process group, cache, generated state, and test artifact is reserved before creation and then activated with repository, workspace, settings, run/task/attempt, containment root and relative name, creator nonce, type-specific stable identity, evidence references, dependency edges, and receipt digest. Prefix, age, branch, PID/name, or path alone never proves ownership.

On success, failure, cancellation, interruption, timeout, handoff, or startup recovery:

1. seal the original outcome durably; later callbacks replay;
2. copy, hash, fsync, and independently resolve required evidence outside deletable roots;
3. clean reverse dependencies—exact process trees, exact contract, owned artifacts/caches/generated state, then a clean registered worktree last;
4. revalidate live identity before each action, journal/postcheck after it, and resume from durable state after crashes;
5. aggregate `taskplane.cleanup-receipt/v1` and prove exactly zero registered/live leaks.

Cleanup refuses foreign or wrong-generation ownership; missing/tampered/ambiguous registration; relocation, symlink, hard-link, reparse-point, or containment failure; dirty/staged/untracked/unmerged/locked/ref-mismatched worktrees; active contracts; PID/group/start/token/binding mismatch; cache producer/version/input mismatch; artifact type/inode/parent mismatch; unsealed evidence; manifest races; handoff authority transfer; and unsupported no-follow hosts.

Refusal performs no destructive action, preserves the target and original outcome, emits an exact leak/manual-recovery receipt, and blocks sign-off/release. Cleanup failure is secondary evidence; it never rewrites pass, fail, cancel, interruption, timeout, or handoff.

## Producer-consumer freshness

The complete machine-readable map is in `design/contract.json`. The central edges are:

- canonical JSON → typed loader → every flow/operational consumer. A key change invalidates every affected digest; any direct governed read fails inventory/wiring.
- host observations → compatibility/receipt only. Host facts cannot select a default or fallback.
- legacy input → adapter → loader overlay only. No direct legacy consumer exists.
- source/settings/test/inventory → frozen shard plan → CI shards → one classified correction wave → protected-main release. Any byte change invalidates candidate authority.
- resource creator → ownership manifest → typed cleanup handler → cleanup receipt → metrics/sign-off. Identity mutation severs deletion and preserves the target.
- producer/interface → named consumers and same-slice fixtures. Producer changes without fresh consumers fail; deliberate edge removal requires a severed-edge ledger and absence assertion.
- committed loop/stage/worker/lens/lifecycle event → one `HostSurfaceSnapshot` → sibling `HostNativeRecovery` and static delivery → one atomic current head. A changed event/settings/source/graph digest must change every consumer; a severed publisher leaves a stale actionable page and fails.
- Design graph → Design component; Plan tasks/deps/waves → Plan component; depgraph impact → separately labelled module-impact component. Missing or invalid sources show unavailable/unverified rather than substituting another graph.
- package/release manifests → both plugin archives → provenance and tag gate. Omission, dirty source, wrong SHA, stale green, or topology mismatch severs release.

## Parallelism and named serialization

After the settings and snapshot assembler interfaces are fixed, pairwise-disjoint CLI/flow, hook/workflow, dashboard projection, host runtime, CI/release/package, cleanup, portfolio, and metrics adapters may build concurrently when they do not share a generated file. After one snapshot freezes, Codex, Claude, JSON, Markdown, HTML, Design graph, Plan topology, and module-impact projections run concurrently and join before current-pointer publication. CI shards and independent validation jobs, including the browser shard, run concurrently against one candidate. Independent cleanup handlers run concurrently after terminalization and join at a zero-leak barrier.

Serialization is allowed only for:

- settings load before side effects — fail-closed trust dependency;
- canonical interface before adapters — schema dependency;
- resource reservation before creation — deletion/recovery authority;
- outcome/evidence seal before cleanup — evidence and terminal authority;
- same-resource cleanup under lock — CAS/replay safety;
- coverage adjudication before deletion — current-contract evidence;
- producer change before consumer freshness tests — interface dependency;
- authoritative event/terminal receipt commit before projection — workflow and terminal authority;
- one legacy-or-v4 source-mode selection/read before snapshot freeze — split-brain prevention;
- dashboard sequence CAS per stable identity — monotonic order and replay safety;
- snapshot freeze before parallel projections — one semantic input;
- generation fsync before current-pointer CAS — atomic publication and freshness;
- `HostNativeRecovery.apply` per identity — stale/conflict/post-terminal rejection;
- candidate freeze before terminal CI — exact-SHA authority;
- terminal CI and exact protected-main green before tag/release — release authority.

Coupled generators, goldens, checksums, fixtures, manifests, and ledgers use one stabilization owner rather than racing concurrent edits.

## Observability and measurable targets

Settings receipts expose source/effective digests, safe source classes, validation and compatibility results. Dashboard receipts expose sequence/revision/generated-at/fingerprint/source mode, commit-to-head and head-to-host lag, freshness/stale age, retry/rejection/recovery, capability/acknowledgement or static limitation, phase-graph totals/truncation, HTML document count, and DOM/SVG proof. Validation exposes layer fingerprints and unchanged-green reuse. CI exposes candidate/shard/browser-environment wall time, runner-minutes, parallelism, cancellation, and failure class. Cleanup exposes outcome, latency, replay, refusal reason, TERM→KILL escalation, evidence verification, and exact leak count. Metrics expose non-cumulative suite, feedback, tokens, duration, workers/worktrees, matrices, returns, and every serialization reason.

Final targets are:

- 100% governed settings with one key/owner and zero duplicate defaults;
- no more than 230 test files and 4,200 cases, at least six redundant families removed, zero protected loss;
- exact-selector p95 ≤60 seconds, changed-radius/proportional p95 ≤5 minutes, broad local refused;
- CI p50 ≤10 minutes, p95 ≤15 minutes, ≤30 runner-minutes, ≥4x parallelism with four shards;
- exactly zero owned leaks and active worktrees ≤active shards + 1;
- tokens target ≤100M total/15M uncached, hard ceiling 150M/25M, with billing truth separate;
- Design ≤60 minutes, post-authorization delivery ≤8 hours, phase through Retro ≤12 hours;
- no more than two Plan returns before stabilization, planned sessions ≤24, fail-closed ceiling 60.
- 100% of named committed dashboard events publish or idempotently replay, zero silently stale actionable surfaces, complete/truthfully paginated Design and Plan topology, source = visible + omitted for module impact, one HTML document, and one fingerprint across JSON/Markdown/HTML/Codex/Claude with real-browser DOM/SVG proof.

## Failure, rollout, and rollback

Settings validation, stale binding, migration conflict, or host incompatibility stops at preflight. A dashboard publication failure never rewrites the committed product outcome: the old surface becomes explicit stale/read-only evidence and recovery republishes the same snapshot. Stale, contradictory, identity-changing, and post-terminal host updates reject before projection. Invalid graph sources remain unavailable/unverified; nested HTML or diverging format fingerprints prevent pointer advance. Test pruning without current-contract authority retains the test. Invalid CI shards preserve matching greens and rerun only invalidated cells. Unsafe cleanup preserves the target and blocks sign-off. Incomplete metrics or package/release authority blocks sign-off/tag/install.

Rollout order is baseline/inventory; loader/canonical source/legacy receipt and snapshot assembler; shadow snapshot-backed dashboard generations plus exact decoded/graph/DOM comparison; all event callbacks, host recovery/native projection, static stale-aware fallback, and one atomic dashboard current-pointer cutover; atomic preflight/storage/handoff/CLI/dispatch cutover; parallel disjoint adapters; shadow owned-resource registration then authoritative cleanup; dry-run shards including the browser receipt, then CI replacement; evidenced portfolio pruning; one terminal frozen-SHA matrix; protected-main validation if SHA changes; clean package, graph, dashboard publication, cleanup, and metric proof.

Before dashboard cutover, discard the shadow generation. After cutover, select the prior complete writer at startup but mark any legacy page without a current snapshot receipt unverified/stale and action-disabled; never dual-own state, reuse a sequence, or fall back from invalid v4. Before release, full rollback is a complete candidate revert, never dual default ownership. After release, rollback installs the prior signed plugin while retaining read-only settings, dashboard, migration, cleanup, CI, portfolio, metrics, and release receipts. The new loader never falls back to stage-local defaults, and no environment kill switch disables proof, freshness, or cleanup.

## Design route and approval

The deterministic minimum-sufficient quick Design route executed exactly three independent lenses in parallel: architecture/integrability for canonical state and adapters, SRE for failed/terminal/cancellation/interruption/handoff/recovery freshness, and mandatory solution-design/testability for graphs, valid HTML, exact selectors, and real host/browser proof. Each found blockers in the stale prior draft; every blocker is now resolved in this Design, so all three final dispositions pass with zero unresolved blockers. The other 23 catalog lenses have one evidenced `covered_by` or `not_applicable` disposition in the contract. Frontend and accessibility are explicitly covered by solution-design and real-browser/retained native accessibility selectors; security is covered by the architecture authority boundary; privacy/data safety are covered by the SRE receipt/lifecycle contract.

Build, Fix, direct Evaluate, and Engineering must execute zero lens workers. No Plan or Build work is authorized by this artifact. The root orchestrator alone performs the Design gate and presents Product, Design, and Plan together at the consolidated pre-implementation human authorization gate.
