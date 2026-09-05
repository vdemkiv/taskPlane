# Fresh Product specification: reliable reusable phases and source-derived decomposition

Status: **manually produced; pending review**. This is a Product draft, not a registered requirement, Design, Plan, approval receipt, or harness-approved result.
Input requirement: R-0005. Source baseline: `8d02d856a77134026f05e83ef4ddcdd714354814` (2.19.0), reproduced by rollback commit `88759129642c9dbf56c05be5fd0012cb566b5412`.
Rollback PR #18 merged as `1cf41e9673bce86ccb8e77517c20e9a6b1332e74`. Its source tree exactly matches the baseline (`284b5c1cb777a3c1b95f2b59849a3e2a803d3af5`); all eight PR checks passed. 2.19.0 was an unreleased candidate, not a published release. Date: 2026-09-05.
Inputs read completely: the immutable copied [R-0005 requirement](inputs/R-0005.md) and [consolidated improvement report](inputs/consolidated-improvement-report.md).
Companion: `intake.json` is plain draft input for later authorized registration. No runtime state, registry, Git state, or approval has been changed by this Product work.

## Problem and users

Taskplane must turn a requirement into dependency-grounded tasks and carry their verified results through release using phases that a fresh execution can resume. The supplied failed-effort evidence shows incomplete producer outputs, unreliable review decisions, misleading recovery, divergent setup behavior, and invalid task/proof restrictions; it does not prove those later-path defects exist unchanged in 2.19.0.
The primary user is the project owner directing delivery. Other consumers are fresh phase workers, independent reviewers, supported native hosts, and maintainers assessing PRs, compatibility, and release evidence.

## Baseline and product boundaries

The 2.19.0 inventory contains graph/decomposition/topology and wiring-closure facilities, stage lifecycle/handoff, RunStore, delivery ports, quality strategy, child evidence, and review policy. These are reuse candidates, not proof of a working end-to-end path.
The later `phase_*` files and `worker_hook_identity.py` named in the inputs are absent from this baseline. Their names, former layouts, and diagnostic line numbers are historical context, not implementation requirements.
In scope: explicit phase input/result contracts, actual dependency analysis and decomposition, worker/task/phase/runtime seams, findings and recovery policy, attempt reconciliation, native transport compatibility, aggregate acceptance, durable finalization, telemetry, and bounded PR delivery.
The broken native transport is an implementation obligation with capability, identity, observation, and live-proof acceptance below. It does not block this manual Product draft.
Out of scope: repairing or driving the current harness during Product; importing prior Design/Plan/runtime/conversations as authority; creating an approval or canonical requirement here; code changes here; unrelated features; new generic workflow frameworks, coordinators, stores, parallel authorities, or arbitrary module/line-count targets.
Also excluded: direct pushes to main, unapproved publication, invented host observations or usage, secret/prompt/transcript export, whole-repository scans without a bounded need, and closing unrelated P14/P23 evidence obligations by implication.

## Required behavior and priority

- **FR-01 — Source-grounded graph:** verify current source touchpoints and bounded dependency coverage before decomposition; distinguish observed source, verified proposed extension, and realized candidate facts.
- **FR-02 — Decomposition and seams:** derive task scope, ownership, dependencies, and order from connected impacted subgraphs; close every actual cut edge and declared runtime seam, including phase boundaries.
- **FR-03 — Candidate conformance:** compare the integrated candidate with proposed seams, consume exact boundary proofs, and support honestly bounded standalone Review without invented Plan approval.
- **FR-04 — C, admissibility:** apply canonical finding classification and blocking policy after identity/byte validation; retain non-blocking findings and append revalidation when historical passes are relied upon.
- **FR-05 — E, recovery:** preserve the actual refusal, effects, attempt, prerequisites, and authorized continuation; distinguish waiting, artifact correction, setup repair, and requests for new authority; stop unchanged failing retries.
- **FR-06 — A, complete results:** every producer publishes all required produced and inherited artifacts, actual quality strategy bytes, evidence, and applicable authority for a fresh successor.
- **FR-07 — B, execution:** owner and reviewer setup/pickup reconcile all required durable records and uncertain external effects before readiness; identity-bound replay preserves completed work and rejects changed input.
- **FR-08 — D, acceptance:** support many-to-many task/criterion contributions, multiple exact proofs, accountable acceptance ownership, and separately required joint integration proof.
- **FR-09 — Complete delivery:** explicit results and continuations connect Product, Design, Plan, Build, Review, Retro, and Release or another explicitly declared final state; no phase requires predecessor private runtime.
- **FR-10 — Authority and history:** one explicit applicable policy distinguishes produced work, mechanically validated evidence, authorized progression, and human sign-off; scope-valid authorization is reusable only as recorded.
- **FR-11 — Small reusable units:** reuse incumbent lifecycle, graph, storage, seam/evidence, and policy capabilities; each migrated responsibility has one active owner, explicit historical compatibility, retired paths, and rollback.
- **FR-12 — Truthful visibility:** report provenance-bound progress, effects, waits, corrections, replay, timing, evidence gaps, and provider/cache usage without fabricating unavailable data or double-counting.
- **FR-13 — Demonstrated delivery:** future work uses current-source Design, bounded PRs, exact acceptance/proof mapping, six labeled journeys, independent final review, scoped sign-off, Retro, and separately authorized release.

Priority remains **C → E → A → B → D**: first restore trust in acceptance decisions; then make continuation truthful; then close missing producer contracts; then reconcile execution; then remove invalid contribution/proof restrictions.
This is a risk priority for Design and bounded implementation slices, not a command to bypass dependencies. Source-derived decomposition, one owner, and honest evidence constrain every slice; Design may order prerequisites differently only with an explicit dependency and acceptance rationale.

## Reusable phase behavior

A phase receives an explicit versioned input package, performs its own expert work, applies integrity and domain validation plus the applicable authorization policy, and persists a complete result usable by a fresh successor. Durable state may exist; predecessor conversation or private runtime is not an input contract.
The result identifies artifacts, full accessible evidence, unresolved issues, attempt/candidate provenance, completion status, and the precise continuation. Publishing some files or saving an interrupted draft cannot mean phase completion or approval.

| Phase | Required expert work and result |
|---|---|
| Product | Define the problem, scope, requirements, dependencies, exclusions, and fail-able acceptance criteria. |
| Design | Resolve alternatives, boundaries, dependencies, complete outputs, quality strategy, compatibility, and policy decisions against current source. |
| Plan | Produce graph-grounded tasks, scope/order, contribution ownership, and exact proof obligations. |
| Build | Produce scoped changes and candidate-bound task, quality, seam, and integration evidence. |
| Review | Evaluate evidence sufficiency and conformance, obtain independent Engineering judgment, and expose any required human sign-off. |
| Retro | Record actual outcomes, telemetry limits, lessons, corrective recommendations, and follow-up obligations. |
| Release | Reach the declared PR-based delivery/final outcome using accessible review evidence and applicable publication authority. |

These are behavior boundaries, not a required number of modules, services, workers, classes, or stores. Adding a phase using existing capabilities must require its definition, processing, and domain validation without changing shared dispatch, pickup, storage, hooks, or lifecycle behavior.

## Acceptance criteria and verification

Every criterion below is a future implementation obligation unless explicitly marked as the Product handoff. Tests must bind the current source/candidate and public production boundary. Design must assign owned exact positive/negative selectors before implementation; no test or journey is claimed executed by this draft.

- **FP-AC01 — Verified bounded source coverage.** Verify files, modules, symbols, configuration, contracts, and runtime touchpoints once per bound input; unverified proposals cannot enter decomposition. Default local depth is 3; report exact depth, fan-out, time, parser/language, ambiguity, policy, missing, unsupported, and rejected stopping conditions. Identical inputs produce deterministic semantic results and partial coverage never reads as complete. **Verify:** bounded source/graph fixtures with changed, missing, ambiguous, unsupported, and truncated inputs.
- **FP-AC02 — Dependency-derived tasks and complete seams.** Connected impacted subgraphs determine ownership/order; verify every proposed extension before expansion. Every cut edge and declared runtime seam carries exact producer/consumer nodes and symbols, schema/version, explicit cardinality, both owners, direction/order, distinct positive/severed selectors, and source/graph/requirement/Design/Plan/candidate provenance. **Verify:** public readiness rejects omitted nodes/edges, orphan/duplicate producers or consumers, wrong direction/order, unowned rows, and stale/incomplete bindings.
- **FP-AC03 — Integrated realized conformance.** Proposed overlays remain distinct from as-built truth. Detect added, removed/missing, changed, reversed, stale, and unexpected seams in the combined candidate, including individually passing tasks whose shared contract disagrees. **Verify:** Engineering cannot approve before consuming current conformance and every required seam proof; swapping overlay identity or removing a binding changes the public outcome to blocked.
- **FP-AC04 — Faithful production-boundary proof.** Each required worker/task/phase/runtime seam passes with actual upstream production output and fails observably under a distinct, targeted one-edge severance. **Verify:** reject missing, stale, foreign, multi-edge, ceremonial, or non-failing severance and consumer-only fabricated boundary inputs; an unavailable required runtime probe remains a blocking evidence gap.
- **FP-AC05 — Standalone review and compatibility.** Review without Plan derives current source/diff seams without inventing Plan authority, labels declared/derived coverage and partial/time-out/unsupported/truncated limits, and reads eligible historical graph/evaluation-evidence/wiring records through deterministic migration or revalidation. **Verify:** historical-generation and standalone fixtures preserve original records and reject a second graph, seam, or evidence authority.
- **FP-AC06 — Entry, native capability, and identity.** Supported ordinary CLI and native launchers preserve selected valid engine, arguments, contract, and enforcement; absent, invalid, or ambiguous installed candidates fail before effects. Capability is established before dispatch readiness. Runs/replacement attempts have unique native identities, review children bind their owner attempt, and exact replay retains identity. **Verify:** entry/capability tests and a fresh real native spawn; broken binding or foreign results cannot widen authority, while duplicate events converge through one effective observation owner.
- **FP-AC07 — C: substantive review admissibility.** Valid observed bytes and a reported pass cannot override a canonical policy-blocking unresolved finding or missing required evidence. Equal classifications yield equal applicable decisions; non-blocking debt/observations remain visible and resolution needs current linked evidence. **Verify:** contradictory public collection cases and append-only revalidation of relied-on historical passes; missing retained evidence yields unverified, with originals unchanged.
- **FP-AC08 — E: truthful authorized recovery.** Refusals name safe reason, phase/attempt, committed/observed/pending/uncertain effects, continuation, prerequisites, and whether replacement is permitted. Missing terminal evidence names observation/wait/reconciliation, not generic artifact repair. **Verify:** public failures distinguish wait/correction/setup/authority, recheck authority on execution, preserve identity/completed work on unchanged retry, and stop repeated identical failure without progress while legitimate waits use the named event mechanism.
- **FP-AC09 — A: complete producer result.** Normal quality-enabled Design seals actual strategy bytes and every selected produced/inherited artifact, accessible evidence, and applicable quality authority; fresh Plan and Build consume only the package through public interfaces. **Verify:** remove, alter, or stale each required artifact and observe producer-completion or successor-admission refusal with its specific reason; no test inserts missing artifacts, receipts, or permission between phases.
- **FP-AC10 — B: recoverable attempts.** Owner/reviewer setup and pickup never advertise readiness with absent/inconsistent required records. Same-operation replay reconciles setup or returns its valid prior result; changed input under the identity is refused. **Verify:** relevant-phase fault injection at each local persistence/external-effect boundary and host reconciliation before any uncertain re-launch; unavailable reconciliation is a named blocker, never an exactly-once remote claim.
- **FP-AC11 — D: contributions and acceptance.** One task may contribute to multiple criteria, multiple tasks to one criterion, and each task/criterion may require several exact proofs plus joint integration evidence. Accountability cannot exclude contributors. **Verify:** public Plan/Build/acceptance fixtures block missing contribution/proof, stale candidate, or absent joint proof; completed tasks alone cannot establish acceptance, and distinct shell commands are never guessed equivalent.
- **FP-AC12 — J1: real native start through collection.** On an authorized supported real host, dispatch observes genuine start/identity and terminal events and collects actual output through production interfaces. **Verify:** preserve exact host/evidence provenance and show missing identity/terminal refusal without fabricated completion or duplicate launch; unsupported capability is refused before readiness and remains unverified. Simulation or skip cannot satisfy J1.
- **FP-AC13 — J2: quality Design → Plan → Build.** A fresh execution trace carries the actual Design-selected strategy, complete outputs, and quality authority into fresh Plan/Build. **Verify:** run the corresponding removed/altered-artifact failure through the same production connection; label host mode and evidence origin, with simulated host evidence unable to substitute for J1/J6.
- **FP-AC14 — J3: interruption and pickup.** Production-entry traces parameterize owner/reviewer roles and applicable phases over fresh pickup, interruption, cancellation, and duplicate events while preserving logical operation identity. **Verify:** fault injection at relevant persistence/effect boundaries asserts readiness records and launch counts; each trace explicitly labels real or simulated host evidence and uncertain effects.
- **FP-AC15 — J4: rejected review and bounded correction.** Production collection rejects a pass carrying a genuine blocker, takes only a bounded authorized correction route, and admits only a corrected candidate with sufficient current evidence. **Verify:** unchanged failure preserves identity/completed work and stops blind retries; host mode, unresolved evidence, and the correction authority are explicit.
- **FP-AC16 — J5: multiple tasks and proofs.** An actual Plan-produced many-to-many contribution and multiple-proof package reaches Build and aggregate acceptance with joint behavior evidence. **Verify:** removing each required contribution/proof or changing candidate identity blocks the same journey; no fixture rewrites the producer package to bypass contribution checks, and host mode is labeled.
- **FP-AC17 — J6: real finalization.** On a supported real host, production Build output provides full accessible evidence to fresh Evaluate, independent Engineering review, applicable scoped human sign-off, Retro, and the declared Release/Publish/final outcome. **Verify:** missing downstream evidence or authority blocks at its boundary; PR-based delivery and separately authorized publication are evidenced. Build-only completion, documents, synthetic terminal events, historical approval, or skips do not satisfy J6.
- **FP-AC18 — Authority and retained obligations.** Each supported path distinguishes draft/interrupted output, mechanical validity, and attributed progression authority under the explicit applicable policy; recovery rechecks scope-valid authorization. **Verify:** reject predecessor conversations/runtime, old Design/Plan/approvals/reviews/completion claims, and stale fingerprints as authority; retain R-0004 and R-0001/R-0003 dependencies, leaving P14 native canary and P23 whole-suite adjudication separately open.
- **FP-AC19 — Modest reuse and bounded migration.** Design maps every proposed unit to one responsibility, explicit inputs/outputs, incumbent capabilities, and current acceptance checks. Each migration PR names removed/inactive superseded paths, compatibility, and rollback. **Verify:** an extension fixture adds a phase through definition/processing/domain validation using existing capabilities without shared dispatch/pickup/storage/hook/lifecycle changes; review rejects competing progression owners, unexplained infrastructure, or silently reinterpreted historical authority.
- **FP-AC20 — Honest telemetry and privacy.** Preserve run/phase/attempt/operation/candidate identity, continuation, timing, actual terminal outcome, last progress/wait, correction/replay counts, uncertain effects, missing evidence, and next permitted action. **Verify:** replay/duplicate events count attempt and provider usage once; usage has provenance/cache semantics, absent values remain unavailable, and exported diagnostics contain no secrets, full prompts, transcripts, or predecessor private runtime.
- **FP-AC21 — Fresh Product and Design handoff.** This manual spec and intake draft are the only current Product deliverables; they do not constitute registration or approval. Future authorized registration must retain the complete draft scope. **Verify:** artifact review confirms all original criteria/journeys map below, no code/runtime/Git/authority mutation is claimed, and fresh Design must bind current source, map owned exact positive/negative selectors, and settle policy/compatibility/rollout before applicable gates; former Design/Plan are historical only.

## Explicit preservation map

| R-0005 original criterion | Fresh criterion | Preserved obligation |
|---|---|---|
| AC1 | FP-AC01 | Verified touchpoints, bounded coverage, deterministic source truth. |
| AC2 | FP-AC02 | Subgraph decomposition, every seam, exact ownership and provenance. |
| AC3 | FP-AC03 | Integrated realization and Engineering consumption of conformance. |
| AC4 | FP-AC04 | Actual producer boundary proof and meaningful one-edge severance. |
| AC5 | FP-AC05 | Standalone Review, bounded coverage, historical compatibility. |
| AC6 | FP-AC06 | Launchers, engine/contract preservation, native identity and real spawn. |
| AC7 | FP-AC07 | C policy admissibility and append-only historical revalidation. |
| AC8 | FP-AC08 | E precise refusal, preserved attempt, authorized recovery, non-progress stop. |
| AC9 | FP-AC09 | A complete quality-enabled producer bundle and fresh consumption. |
| AC10 | FP-AC10 | B all-record reconciliation and uncertain host-effect handling. |
| AC11 | FP-AC11 | D contributions, exact multiple proofs, joint acceptance. |
| AC12 | FP-AC12 | J1 actual native start, identity, terminal event, and collection. |
| AC13 | FP-AC13 | J2 normal quality producer path and artifact-failure trace. |
| AC14 | FP-AC14 | J3 interruption, replay, cancellation, duplicate events, roles/phases. |
| AC15 | FP-AC15 | J4 blocked collection, authorized correction, no blind retry. |
| AC16 | FP-AC16 | J5 actual Plan package, multiple contributions/proofs, aggregate failure. |
| AC17 | FP-AC17 | J6 real complete downstream workflow, sign-off, Retro, publication policy. |
| AC18 | FP-AC18 | Authority separation, immutable history, retained R/P obligations. |
| AC19 | FP-AC19 | Small reuse, one active owner, extension, migration, rollback. |
| AC20 | FP-AC20 | Truthful provenance/usage/cache telemetry and safe export. |
| AC21 | FP-AC21 | Product-only boundary and fresh Design; registration deferred explicitly. |

| Journey | Fresh criteria | Required evidence mode and failure |
|---|---|---|
| J1 — Native start/terminal | FP-AC06, FP-AC12 | Actual supported host required; missing identity/terminal refuses, without duplicate launch. |
| J2 — Quality Design/Plan/Build | FP-AC09, FP-AC13 | Host mode and origin labeled; actual producer artifacts, then removed/altered-artifact failure. |
| J3 — Interrupted setup/replay | FP-AC08, FP-AC10, FP-AC14 | Real/simulated labeled; boundary fault injection, readiness/launch counts, same-operation pickup. |
| J4 — Review/correction | FP-AC07, FP-AC08, FP-AC15 | Real/simulated labeled; genuine blocker, authorized bounded correction, unchanged-failure stop. |
| J5 — Tasks/proofs | FP-AC11, FP-AC16 | Real/simulated labeled; production Plan package and missing/stale/joint-evidence failures. |
| J6 — Finalization | FP-AC17, FP-AC18, FP-AC20 | Actual supported host required; missing evidence/authority blocks each downstream boundary. |

## Non-functional requirements

- **security:** treat paths, symlinks, selectors, manifests, host events, and portable evidence as untrusted; enforce containment, exact identity/provenance, and current authority before effects/recovery. A hash or copied locally authenticated receipt is not transferable authentication.
- **architecture:** one active owner per migrated lifecycle/graph/seam/finding/acceptance responsibility; reuse incumbent capabilities in small units with proposed and as-built truth distinct. Reject another framework, coordinator, store, parallel authority, or arbitrary module-count target.
- **integrability:** exact versioned producer/consumer contracts, full portable artifacts/evidence, multiple contributions/proofs, typed refusals, explicit additive/breaking classification, and deterministic eligible historical migration/revalidation; reject silent drift.
- **data-safety:** immutable historical requirements, approvals, passes, and artifacts; append revalidation, reconcile interrupted operations, reject changed-input replay, preserve completed work, and provide bounded rollback without dual owners.
- **sre:** bounded traversal/correction, visible degraded capability/evidence, precise continuation, persistence/effect fault injection and duplicate-event reconciliation; legitimate waits remain waits and identical failing retries stop.
- **privacy-compliance:** export only necessary diagnostic/telemetry data and safe reason codes, with provenance; exclude secrets, full prompts/transcripts, predecessor private runtime, and copied authentication authority.
- **cost-finops:** default graph depth 3 plus explicit fan-out/time/traversal limits; avoid unnecessary whole-repository scans and worker fan-out; deduplicate replay and record available provider/cache counters with their actual semantics.
- **qa:** require six production-boundary journeys, exact positive/targeted-severed evidence, current candidate binding, and real/simulated labels; consumer fixtures, suite counts, skips, and synthetic observations cannot establish real host completion.

## Focused Product lens disposition

This is draft consideration, not a review wave or executed lens evidence. No lens workers were launched; later review selection must follow actual changed scope and risk.

| Lens | Draft disposition | Scope evidence |
|---|---|---|
| security | Applied | Untrusted portable inputs, host identity, and effects. |
| architecture | Applied | Reuse, dependency decomposition, and one active owner. |
| integrability | Applied | Versioned seams, historical formats, and native contracts. |
| data-safety | Applied | Immutable history and interrupted durable operations. |
| sre | Applied | Recovery, waits, duplicate events, and live capability. |
| privacy-compliance | Applied | Telemetry and portable evidence export. |
| cost-finops | Applied | Bounded graph work, correction, and honest usage. |
| qa | Applied | Six journeys and production-boundary proof fidelity. |
| product | Applied | User outcome, exclusions, priority, and fail-able acceptance. |
| testability | Applied | Observable public failures and exact proof identities. |
| solution-design | Deferred to Design | Implementation ownership and policy decisions need current-source alternatives. |
| backend | Deferred to Design/review | Runtime behavior is in scope; no implementation exists for this effort. |
| code-quality | Deferred to implementation review | This draft changes no code. |
| devops | Applied | Bounded PR delivery, CI evidence limits, rollback, release authority. |
| project-management | Applied | Explicit dependencies, acceptance ownership, and handoff readiness. |
| tech-writer | Applied | Precise refusal and evidence terminology. |
| scalability | Bounded within cost-finops/sre | Traversal/fan-out limits; no distributed scale expansion requested. |
| tradeoffs | Deferred to Design | Alternative designs must explain reuse and compatibility costs. |
| time-to-market | Applied | Small dependency-justified PR slices; no ritual full-catalog wave. |
| services-selection | Not material | No new external service selection is requested. |
| dba | Not material | No database product or schema selection is prescribed. |
| frontend | Not material | No new frontend surface is requested. |
| design | Not material | No visual product redesign is requested. |
| accessibility | Not material to this draft | No new interaction surface; revisit if Design adds one. |
| i18n | Not material | No localization behavior is requested. |
| mobile | Not material | No mobile client or capability is requested. |

## Dependencies, contracts, and handoff

Retain unresolved input dependencies **R-0004, R-0001, R-0003** (deduplicated from R-0005). Their history is preserved; this draft does not verify satisfaction or import completion. R-0005 is the requirements input, not evidence of an approved current Design.
Design must revalidate these obligations against current source: touchpoint coverage, bounded graph analysis, graph-derived ownership/order, proposed/realized seams, integrated conformance, runtime boundaries, and positive/severed production proofs. P14 native canary and P23 whole-suite adjudication remain separately open.
Canonical IDs are separate from their relation; no identifier is allocated here:

| Canonical contract ID | Relation |
|---|---|
| contract:graph-decomposition | changes |
| contract:slice-validation | changes |
| contract:taskplane-source-touchpoint-coverage-v1 | changes |
| contract:taskplane-cross-task-seam-manifest-v1 | changes |
| contract:taskplane-realized-seam-conformance-v1 | changes |
| contract:taskplane-standalone-review-seams-v1 | changes |
| contract:taskplane.stage-handoff/v2 | changes |
| contract:taskplane.phase-progress-receipt/v1 | changes |
| contract:taskplane.phase-pickup-result/v1 | changes |
| contract:taskplane.phase-review-collection/v1 | changes |
| contract:taskplane.phase-host-dispatch/v1 | changes |
| contract:taskplane.stage-authority-binding/v1 | consumes |

These preserve input boundary identities, not a claim that every named version exists in 2.19.0. Design must classify baseline availability and additive/breaking compatibility before changing a boundary; successor versioning must retain explicit traceability.
Product write scope is only this `spec.md` and companion `intake.json`. Prospective implementation areas are existing runtime/tests, launcher/hook adapters, role/skill guidance, and delivery documentation; Design must resolve exact paths from the current checkout rather than carry removed implementation paths forward.
The Product handoff has no implementation test command: `dod.test_command` and exact owned selectors remain to be defined by fresh Design/Plan before implementation. Document review is not a substitute for those future checks.
Future readiness requires reviewed Product scope, current-source Design with all criteria/journeys mapped, explicit dependencies/contracts, chosen approval policy, and bounded migration/rollback before the applicable implementation authorization. Review must be independent of the author of the work it judges.

## Open questions for Design; no Product intake blocker

- **DQ-01:** Which explicit approval policy applies consistently to each supported path, including scope-valid reuse, interrupted drafts, independent review, human sign-off, and separate publishing authority? Do not invent or waive authorization.
- **DQ-02:** Which native host/version and production observation contract provide real identity/start/terminal evidence, and what scoped transport changes are required? Unsupported capability remains an implementation/evidence gap.
- **DQ-03:** Which input boundary versions exist at 2.19.0, which historical generations remain readable, and which bounded migrations are additive or breaking? Identify one active owner and rollback for each slice.
- **DQ-04:** What exact owned public positive/negative selectors and dependency-ordered PR slices prove all criteria and six journeys against the selected candidate? Establish the known failing boundary cases before coordination changes.

These are decisions the new Design must make, not missing Product intent and not permission to reuse former Design/Plan. Status remains manually produced and pending review.
