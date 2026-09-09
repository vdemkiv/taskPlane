# R-0001 Design — bounded phase agents on incumbent authorities

Status: provisional A1-merged HOW for the active R-0001 Design stage. This artifact is bound to candidate `80024e6bbe65df8b868bd0c68fbcd8a6d62fcd6bc5c8cee9cf290ed5ec191e60`, Design team `4325e85b896cd576e5d62e18547e28a7bb4d124a8cd8ff5cde76519d58f01ad8`, and baseline graph `ffc0d3b194e51132ea6fccbbe69bbe7b66c451d3a3b89321234b3df46419a5b3`. Nine affected lenses require fresh evidence against this merged content before final synthesis or approval. It is not approval, implementation authority, or a change to the installed Taskplane 2.19.0 control plane.

## Decision

Use the existing Taskplane 2.19.0 lifecycle, graph, wiring, stage, RunStore, host, review, artifact, telemetry, knowledge, Retro, and release owners as one composed path. Product, Design, Plan, Build, Evaluate, Engineering, and Retro each execute as a stateless phase agent: sealed package in, declared artifacts out. A single explicitly thin `taskplane/agent_runtime.py` facade composes the incumbent owners for package delivery, budget enforcement, host observation, trace, output validation, and evaluator dispatch. It owns no policy, persistence, phase ordering, continuation, gate, or progression, so it is not a second coordinator. `taskplane/loop.py` remains the sole continuation owner and loads phase behavior from `phase-definition/v1` data plus each phase skill rather than hardcoding a phase list.

Every phase agent has no lifecycle API. It cannot approve, advance, dispatch, persist, select its evaluator, mutate its definition, or read outside its sealed package. `working_lenses` are internal agent configuration; `evaluation_lenses` are orchestrator-owned, applied by a separate evaluator, and absent from the evaluated agent's authority. Knowledge is a sealed scoped input at an exact fingerprint. Learning is a provenance-bearing `knowledge-update/v1` candidate admitted append-only only after validation and the declared gate; in-run self-modification is forbidden.

The operative pipeline is:

1. Verify source touchpoints once against a bound input and emit bounded coverage.
2. Derive the connected impacted subgraph and a proposed seam overlay.
3. Plan dependency-ordered tasks and many-to-many acceptance contributions.
4. Run one lifecycle-owned attempt path: validate authority, establish/recover identity, reconcile effects, deliver the sealed package through the shared agent runtime, dispatch or wait, collect and validate against the phase definition's `produces`, dispatch the evaluator under the orchestrator-owned lens set, and publish a complete stage result.
5. Apply canonical substantive finding policy before any correction or progression.
6. Re-run the full source-coverage → decomposition → Plan → seam-manifest → Build → realized-conformance chain, compare the integrated candidate to the approved overlay, and consume both positive and independently reported severed-edge proofs.
7. Require independent Engineering judgment and the sealed terminal-wave telemetry receipt before Retro, finalization, or release assembly may become ready.
8. Seal Retro and final evidence, deliver by PR, and require distinct publication authority.

Risk treatment remains C → E → A → B → D, but the slices below put prerequisite contracts and the real-host capability spike first where they are necessary to make later evidence trustworthy.

## Current state and delta

The baseline already has the necessary authority-bearing foundations:

- `graph_decomposition.py`, `depgraph.py`, `plan_topology.py`, and `wiring_closure.py` own graph derivation, topology, exact selector identity, candidate checkout, and seam proof mechanics.
- `stage_entities.py`, `stage_handoff.py`, `stage_migration.py`, and `run_store.py` own immutable stage values, terminalization, handoff verification, historical migration, transaction identity, replay, and durable journaling.
- `delivery_ports.py`, `producer_observation.py`, `design_host_transport.py`, `host_native.py`, and `dispatch_telemetry.py` own host capability, observed identity/events, Design-worker conservation, native projections, and usage evidence.
- `evaluation_output.py`, `failure_routing.py`, `review.py`, and `review_evidence.py` own typed evaluator output, failure routing, canonical finding collection, and portable review evidence.
- `build_quality.py`, `run_artifacts.py`, `retro.py`, `release_evidence.py`, and `terminal_truth.py` already own quality receipts, durable artifact classes, terminal metrics/Retro, protected-main evidence, and final truth.

The missing delta is production composition and complete contracts: source coverage must gate decomposition; all cut edges and phase boundaries need one manifest; stage output must carry every required produced/inherited artifact plus the governing phase-definition and knowledge fingerprints; host outcomes need one closed portable envelope; recovery must preserve effect truth; acceptance must represent contributors, multiple exact proofs, and joint proof separately; arbitrary test fixture substitution must be detectable at review time; and finalization must consume real-host, sealed telemetry, and protected-main evidence without rebuilding or importing stale authority. `phase-definition/v1`, `agent-runtime/v1`, and `knowledge-update/v1` make the existing FP-AC19 extension promise executable. J0 proves that a fresh clone and empty home can carry one small real requirement to completion through the actual shared mechanism without operator repair and stays green at every slice exit. J7 preserves FP-AC01–FP-AC05 as one actual producer chain rather than isolated fixtures.

The checked-in R-0002 Design, compatibility file, test strategy, selectors, candidate, and team fingerprints remain historical. They do not authorize or define this proposal.

## Alternatives

### A — Evolve incumbent contracts and compose one lifecycle (selected)

Gain: smallest authority surface, current modules remain accountable, phase behavior becomes data-plus-skill, historical readers can be retained, and each public edge can be severed and proven. Cost: one new thin facade and three contracts need carefully ordered compatibility work, strict no-policy enforcement, and a broad but bounded journey suite. Revisit when a second independently deployed repository needs live cross-installation coordination.

### B — Add a new universal phase engine and migrate everything

Gain: a superficially uniform API with centralized policy. Cost: unlike the selected thin facade, it duplicates the current lifecycle, store, host, graph, review, and continuation owners; makes historical compatibility and rollback riskier; delays the highest-risk C/E fixes. Revisit only if incumbent owners cannot satisfy the closed agent-runtime contract without migrating their authority.

### C — Patch individual failing callers

Gain: smaller initial diffs. Cost: retains divergent progression decisions, incomplete packages, and inconsistent refusal semantics, so passing unit tests still cannot establish a complete production journey. Revisit only for a temporary adapter with a named retirement condition.

## Resolved Design questions

### DQ-01 — progression and approval policy

One policy applies everywhere:

- produced bytes are not completion;
- mechanical validity is not substantive acceptance;
- a stage becomes consumable only after its complete candidate-bound result is terminalized by the current scoped stage authority;
- the Design gate is the one consolidated pre-implementation human authorization, binds the exact current Design/requirement/candidate/graph/test-strategy bytes and scope, and cannot be issued by the Design agent;
- independent Engineering review must be current and conformant before final sign-off;
- interrupted, closed, discarded, retained, or historical results are non-consumable unless an explicit scope-valid reuse authorization selects their exact fingerprints, and revalidation is append-only;
- correction authority is bounded to its named candidate/attempt and does not imply stage progression;
- PR merge/release readiness and publication are separate: protected-main evidence may authorize preparation, while tagging or publishing requires a distinct current human publication authorization.

Recovery rechecks authority immediately before effects. A digest or copied receipt proves identity/integrity only; it never becomes transferable authentication.

### DQ-02 — native host and observation contract

The selected target is the local Codex native path running the installed Taskplane 2.19.0 engine through the existing delivery-port/producer-observation adapters. S0 runs a fresh supported-host canary before lifecycle work. If that canary shows stable host-owned start, progress/attention, wait, cancellation, terminal, and collection identities, the incumbent adapters remain unchanged.

If stable host identity is unavailable but Taskplane hooks execute, the smallest fallback is Taskplane-owned identity: the runtime generates a cryptographic per-attempt nonce, binds it into the sealed package or environment, and Taskplane's SessionStart, PreToolUse, and Stop hooks emit sealed receipts carrying the nonce, phase-definition fingerprint, monotonic hook sequence, and output reference. The production adapter observes those receipts as `phase-host-dispatch/v1`; it does not parse an unstable host transcript, depend on a role marker in a tool message, synthesize a terminal, modify the host, add a store, or take lifecycle/progression ownership.

If neither stable events nor Taskplane hook receipts are available, S0 emits a rigorously labeled `degraded_observation` receipt with the missing capabilities, host/version, canary operation, observed raw identity class, evidence gaps, and permitted next action. Degraded mode is admissible only for deterministic diagnostics and simulated fault work. It cannot satisfy J0, J1, J6, native-success acceptance, final sign-off, or publication under the current requirement. The run stops at the exact unsupported gap and presents capability repair or an attributable human Product rescope that creates new requirement/Design bindings. The non-native C/E foundation may still land after its named gate, but native-dependent expansion stays closed. This preserves Product's real-supported-host evidence requirement.

`contract:taskplane.phase-host-dispatch/v1` is the portable boundary. It covers assignment, observed start/identity, progress or attention, wait, cancellation/interruption, terminal observation, collection, and refusal. Every request binds run, requirement, stage, phase, attempt, operation, candidate, phase definition, knowledge, contract digest, host kind/version, and authority reference. Every observation names its identity source and adds either a host-owned event identity or Taskplane nonce-receipt identity, monotonic sequence, observed time, effect state, output references, and a terminal outcome or stable error with retry class and permitted continuation. Exact replay is idempotent; nonce mismatch, a receipt gap, or changed input under one operation is permanent refusal; uncertain effects require reconciliation before replacement. Consumers ignore unknown optional fields only within v1 and reject unknown required/authority fields, schema majors, enums, and ambiguous duplicates.

Simulation remains useful for deterministic fault injection but carries `evidence_mode=simulated` and cannot satisfy J0, J1, or J6.

### DQ-03 — compatibility and migration

Baseline versions are explicit in the contract migration ledger. Important facts are: RunStore reads `taskplane.run/v3` and `/v4` and promotes v3 append-only; current stage values and authority use `/v1`; the current handoff implementation is `taskplane.stage-handoff/v1`; Review uses current v3 collection manifests with bounded legacy handling; the requirement's phase progress/pickup/review/host and seam contracts do not yet have one complete public producer/consumer realization.

For every boundary: first add a closed reader/validator and deterministic historical adapter; then emit the successor alongside the incumbent projection; prove restart, duplicate-event convergence, changed-input refusal, completed-work preservation, and immutable historical reads; switch one writer/progression owner; finally mark the superseded writer inactive. Breaking authority semantics—especially stage-handoff v2—never silently reinterpret v1. Rollback restores the prior writer/reader selection but retains all newly written immutable evidence and never relaunches observed work.

### DQ-04 — proof ownership and delivery slices

The contract maps all 21 exact requirement criteria to named prospective public selectors. Every row has an accountable owner, a positive selector, a distinct negative or one-edge-severed selector, controlled dependencies, candidate/provenance requirements, and validation. Eight journeys compose those criterion proofs through actual producer outputs. J0 is one user-visible invariant: from a fresh clone and empty home, one small real requirement reaches its declared terminal outcome without operator repair, fabricated producer output, skipped shared boundaries, degraded evidence, or a missing telemetry seal. Its implementation mode is explicit: S0 proves the incumbent Taskplane 2.19.0 public path; S1–S2 rerun and preserve that incumbent mode; S3–S6 prove the same invariant through `agent-runtime/v1`. Agent-runtime removal/severance is a valid J0 negative only in S3+, while S0–S2 sever the corresponding incumbent shared lifecycle/dispatch/collection boundary. The two modes are equivalent only when their receipts bind the same user input, declared outcome, producer lineage, real-host evidence class, gate set, and no-intervention rule. J7 remains the FP-AC01–FP-AC05 decomposition path: actual bounded source coverage feeds decomposition, Plan, the seam manifest, Build, and realized conformance; its positive run and each named severed seam must consume producer outputs rather than fabricated fixtures. J0, J1, and J6 require a real supported host or valid Taskplane-owned nonce hook receipts on that host.

Compound negative selectors are parameterized by named case IDs and emit one result/receipt per case. No aggregate `or`, first-failure shortcut, or shared setup failure may let one refusal, staleness, replay, authority, seam, or provenance branch mask another. The contract's 24-entry negative-case inventory is the review source of truth. Review also runs an arbitrary-test-code fixture-bypass detector: it scans test collection and producer lineage for synthesized missing artifacts, monkeypatched producer success, copied receipts, or a consumer invoked without its public producer seam. Legitimate same-slice compatibility fixtures must be declared, fingerprinted, and tied to a real producer contract; all other substitution is a substantive review failure.

Dependency-ordered PR slices and their delivery branches are:

1. **Capability/proof scaffold:** freeze contract schemas, exact selectors, fault seams, and privacy allowlist; record J0 green on the installed Taskplane 2.19.0 baseline; and run host-event plus Taskplane-owned nonce/hook capability spikes. No product lifecycle behavior changes. Stop if J1 identity/start/terminal/collection cannot be observed by either real path.
2. **C — admissibility:** make `phase-review-collection/v1` the public typed collection result and apply canonical finding policy after provenance validation. Append historical revalidation; retire any caller-local pass override.
3. **E — recovery:** emit progress/pickup receipts with effect truth and one lifecycle-owned continuation policy; `budget_exhausted` is a terminal refusal with recoverable pickup state and `lifecycle_api_attempt` is refused before effect. Retire divergent caller recovery projections.
4. **A — complete results, seams, and shared runtime:** introduce `phase-definition/v1`, the thin `agent-runtime/v1` facade, `knowledge-update/v1`, stage-handoff v2 with definition/knowledge fingerprints, source coverage, cross-task seam manifest, and complete artifact packages. Migrate Design as the first phase agent, establish J7, and prove FP-AC19's data-plus-skill extension. Retain v1 read compatibility, then disable its writer after cutover.
5. **B — recoverable attempts/native transport:** converge owner and reviewer pickup/dispatch on RunStore, delivery ports, and `agent-runtime/v1`; retire duplicate active setup/observation/caller-local dispatch paths after host-event/nonce, simulated parity, and interruption proofs.
6. **D — contributions, agent migration, and conformance:** extend Plan/acceptance data for many-to-many contributions, exact proof lists, joint evidence, and realized seam conformance; migrate Product, Plan, Build, Evaluate, and Engineering agents; keep evaluator lenses orchestrator-owned and task completion separate from requirement acceptance.
7. **J6 finalization:** migrate Retro, connect current Build output through fresh Evaluate, independent Engineering, scoped sign-off, sealed terminal-wave telemetry, Retro, protected-main release assembly, and separate publication authorization. Preserve exact tested package bytes; no rebuild or direct push.

Every slice S1–S6 has a candidate-bound J0 regression gate. A skip, xfail, degraded host, fixture substitution, operator repair, removed shared component, or failing J0 blocks that slice regardless of its narrower criterion suite.

S0–S2 form the **Foundation branch** and may land independently after the attributable **Foundation Acceptance gate** binds their exact candidate, contract, evidence, real-host/degraded capability outcome, and active-owner inventory. Its rollback restores only the prior review/recovery routing, preserves immutable receipts and observed effects, and does not require or authorize S3+. S3–S6 form the **Expansion branch**. Before S3 starts, the attributable **Expansion Revalidation gate** must bind the accepted foundation head plus the still-current requirement, Design contract, graph overlay, host capability, and compatibility evidence. Any change to those inputs returns S3+ to revalidation. Expansion rollback leaves the accepted foundation intact and rolls back only the affected successor writer/reader; a foundation rollback invalidates expansion authority and blocks finalization.

Each slice must be independently revertible, name any inactive superseded path, include its positive/severed evidence, and leave the repository at one active owner per responsibility.

### DQ-05 — agent/mechanism split and knowledge governance

`contract:taskplane.phase-definition/v1` declares each phase's role; `skill_ref` and skill content fingerprint; versioned consumed/produced artifact schemas; knowledge scope/fingerprint requirement; domain-validator identities and registered-validator inventory fingerprint; capability requirements; budgets; model tier; two lens sets; gate; telemetry scope; and exactly one DAG shape using `predecessors`, `successors`, `edge_conditions`, `entry`, and `terminal`. Singular `successor` is invalid. The orchestrator owns `evaluation_lenses`, `gate`, and all DAG fields; agent-produced copies are ignored and recorded as authority violations. The definition-set, phase-definition, skill, validator-inventory, artifact-schema, and capability-set fingerprints bind dispatch, result, evaluation, gate, handoff, and continuation. A new phase changes only `settings/agents/<name>.*`, `skills/<name>/SKILL.md`, and its tests, except for a separately registered domain validator. Any per-phase edit to the shared runtime, dispatch, pickup, storage, hooks, or lifecycle fails FP-AC19 review.

`contract:taskplane.agent-runtime/v1` is one shared package-in/result-out mechanism. The disclosed `taskplane/agent_runtime.py` module is deliberately thin: it composes incumbent RunStore, delivery, observation, telemetry, and review owners but contains no phase policy or persistence. Stable refusals include `package_mismatch`, `produces_nonconforming`, `budget_exhausted`, `knowledge_fingerprint_mismatch`, and `lifecycle_api_attempt`; all are terminal observations with explicit recovery, never locks.

`contract:taskplane.knowledge-update/v1` makes learning an artifact rather than hidden mutable state. Knowledge enters a run only inside the sealed package at the declared scoped fingerprint. Updates contain run/attempt/finding provenance, phase/repository scope, a fact or evidence reference, and optional superseded fingerprint. Gate-conditioned strategy, unreferenced content, free-form strategy, cross-scope writes, and forbidden portable fields are rejected. The incumbent knowledge owner appends admitted updates and re-fingerprints; rejected updates remain visible findings and are never silently applied or dropped.

## Human gate inventory

This table is the single gate source. The flow has initial human scope authorization, one consolidated pre-implementation human gate at Design, attributable final sign-off at Engineering, distinct publication authority, and two delivery-branch authorizations. Plan, Build, Evaluate, and Retro are mechanical. Historical duplicate Design/Plan approval steps are ceremonial compatibility projections and cannot add or replace authority. Preservation authorization for interrupted/draft output is not stage approval.

| Stage | Gate type | Attributable human required | Exact binding |
|---|---|---:|---|
| Product / initial authorization | Human-attributable scope gate | Yes | human identity, exact requirement bytes/fingerprint, repository/run scope, authorized effects, policy version |
| Design | Consolidated pre-implementation gate | Yes | approver ≠ Design agent; exact Design/requirement/candidate/graph/test-strategy bytes and fingerprints; modules/contracts/edges; implementation and branch scope |
| Plan | Mechanical conformance | No | Plan package, Design fingerprint, exact modules/contracts/edges, task DAG and selectors; any deviation returns to Design; legacy Plan approval is ceremonial |
| Build | Mechanical producer validation | No | candidate, phase-definition fingerprint, knowledge fingerprint, seam manifest, quality receipts, actual producer outputs |
| Evaluate | Mechanical evaluator verdict | No | orchestrator-owned evaluation-lens-set fingerprint, candidate evidence, phase-review-collection result; evaluated-agent working lenses absent |
| Engineering | Human-attributable final sign-off | Yes | signer identity, independent verdict, zero unaccepted drift, exact tested package, J0/J1/J6/J7, sealed telemetry, rollback |
| Retro | Mechanical telemetry seal | No | run/candidate, terminal attempt/evaluator identities, terminal-wave metrics receipt, Retro artifact fingerprint |
| Release / publication | Human-attributable publication gate | Yes | publisher identity, protected-main commit, exact tested bytes, version/tag/channel, sign-off fingerprint, publication scope/time |
| S0–S2 foundation landing | Human-attributable branch acceptance | Yes | foundation SHA, host-event/nonce/degraded receipt, C/E proofs, J0 regression, compatibility/rollback, active-owner inventory |
| S3 expansion entry | Human-attributable expansion revalidation | Yes | landed foundation, fresh source/graph/Design/Plan, host/nonce capability, J0 regression, S3–S6 scope/selectors/rollback |

Every human record is append-only and attributable; it binds exact bytes or cryptographic identities, not a mutable label. A ceremonial legacy gate cannot satisfy Design implementation authorization, Engineering sign-off, or publication.

## Module and contract ownership

The proposed graph uses repository modules `taskplane`, the new thin `taskplane/agent_runtime.py` facade, `taskplane/tests`, `scripts`, `.github/workflows`, `agents`, the relevant `skills/*`, and `docs`. Fine-grained ownership remains with incumbents:

| Responsibility | Incumbent owner | Changed contract/output |
|---|---|---|
| Bounded source coverage and decomposition | `graph_decomposition.py`, `depgraph.py` | source-touchpoint coverage, graph-decomposition |
| Task ordering and contribution shape | `plan_topology.py`, `test_strategy.py` | slice-validation, acceptance-evidence |
| Cut-edge, selector, and realized conformance | `wiring_closure.py`, Review conformance adapter | cross-task seam manifest, realized-seam conformance, standalone-review seams |
| Stage values, authority, and complete successor package | `stage_entities.py`, `stage_handoff.py`, `stage_migration.py` | stage-authority-binding v1, stage-handoff v2 |
| Attempt identity, pickup, effects, and replay | `run_store.py`, `pickup.py`, `recovery.py` | phase-pickup-result v1, phase-progress-receipt v1 |
| Host capability and observed lifecycle | `delivery_ports.py`, `producer_observation.py`, `design_host_transport.py`, `host_native.py` | phase-host-dispatch v1 |
| Substantive review and correction admission | `evaluation_output.py`, `failure_routing.py`, `review.py`, `review_evidence.py` | phase-review-collection v1 |
| Quality, artifacts, telemetry, Retro, and release | `build_quality.py`, `run_artifacts.py`, `dispatch_telemetry.py`, `retro.py`, `release_evidence.py`, `terminal_truth.py` | stage-handoff v2 plus final evidence references |
| Shared phase-agent facade | new thin `agent_runtime.py`, composing RunStore/delivery/observation/telemetry/review | agent-runtime v1; no policy, persistence, ordering, or progression |
| Phase definition loading and ordering | canonical settings owner consumed by `loop.py` | phase-definition v1; no per-agent Python or agent-writable authority |
| Scoped knowledge read and gated append | incumbent knowledge-store owner | knowledge-update v1 plus consumed/new knowledge fingerprints |

`loop.py` sequences these owners from the ordered definition set and validates their receipts. It may not duplicate their policy or persistence. A later phase extension changes only its definition, skill, tests, and—only when explicitly registered—domain validator. Shared runtime, dispatch, pickup, storage, hook, or lifecycle changes make that extension nonconforming.

## Accepted A1.1 consolidation

The following six proposed decisions are the load-bearing A1.1 refinement. They remain Design decisions, not implementation or approval.

### D-A1-01 — phase definition is the sole registry and DAG

`phase-definition/v1` is the only registry for phase identity and its one closed DAG shape. Every definition requires `predecessors`, `successors`, `edge_conditions`, `entry`, and `terminal`; there is no singular `successor` field. It also requires versioned consumed/produced artifact schemas, `skill_ref` plus the skill content fingerprint, domain-validator identities plus the registered-validator inventory fingerprint, capability requirements, model tier, budget, gate, evaluator lenses, knowledge scope, and telemetry scope. `loop.py` loads one candidate-bound definition set and rejects duplicate phase IDs, cycles, missing/ambiguous DAG fields or endpoints, conflicting entry/terminal declarations, unreachable phases, undeclared artifact schema versions, unavailable capabilities, stale skill bytes, stale validator inventory, and any other phase list or edge source. Dispatch, closed result, evaluation, gate, handoff, and continuation all bind the definition-set and selected phase-definition fingerprints together with the skill, validator-inventory, artifact-schema, and capability-set fingerprints. Agent output cannot modify `evaluation_lenses`, `gate`, or any DAG field.

Revisit only if a real supported phase cannot be expressed in the schema, two accepted extensions require the same shared-runtime edit, the registry cannot represent a required conditional transition without ambiguity, or production receipts cannot reproduce their exact definition/DAG binding.

### D-A1-02 — strict import direction and a non-authoritative runtime

Imports point inward: `loop.py` and phase adapters may import `agent_runtime.py`; `agent_runtime.py` may import only the incumbent RunStore, delivery-port, producer-observation, telemetry, review-evidence, and pure contract validators. Those incumbents never import `agent_runtime.py`, and phase skills/definitions never import runtime internals. The facade accepts a sealed dispatch value and returns a closed result value. It cannot read global phase policy, choose a successor or evaluator, mutate RunStore outside an injected port, create a second journal, or decide readiness.

Revisit if an import-cycle scan finds a reverse edge, the facade gains a phase switch or progression branch, a required capability cannot be injected through a declared incumbent port, or Plan proves the same closed boundary fits an incumbent owner without widening that owner's responsibility.

### D-A1-03 — knowledge proposal/apply CAS and sealed replay

A phase agent can only propose `knowledge-update/v1`; it cannot apply it. The proposal binds base knowledge fingerprint, proposal fingerprint, run/phase/attempt/operation/candidate, finding or observation provenance, scope, content class, and optional superseded fingerprint. After evaluation and the declared gate, the orchestrator asks the incumbent knowledge owner to apply the proposal with compare-and-swap against the exact base fingerprint. Success returns a sealed `knowledge-apply-receipt/v1` containing prior/new fingerprint, proposal, gate, writer/fencing identity, retention class, and applied time. Exact replay returns the same receipt; a changed proposal under the same operation is refused; CAS conflict returns current fingerprint and a fresh-package continuation. Rejected or conflicting proposals never partially write, never block otherwise valid phase artifacts, and remain findings.

Retention is scope/class driven. Deletion or minimization is idempotent, preserves the receipt and content fingerprints needed for audit, and seals tombstone/minimization evidence. Revisit if CAS cannot prevent lost updates, replay produces a different receipt, retention cannot preserve audit identity without retaining forbidden content, or a required fact/evidence reference cannot fit the closed schema.

### D-A1-04 — nonce identity, degraded mode, and evaluator independence

The per-attempt nonce is generated before dispatch and binds run, phase, attempt, operation, candidate, phase-definition, definition-set, sealed-package, knowledge, authority, host kind/version, and deadline. Taskplane hook receipts bind the nonce digest, hook kind/sequence, observed time, effect state, and output reference; they never carry reusable authority. Stable host events remain preferred. Degraded observation records the exact absent capability and may support diagnostics only; it cannot satisfy J0, J1, J6, sign-off, publication, or evaluator independence under the current requirement.

The evaluator receives the closed candidate result plus an orchestrator-issued evaluation-lens-set fingerprint, but never the evaluated agent's working-lens configuration, nonce secret, lifecycle capability, or mutable knowledge. Revisit if the supported host provides a stronger stable identity, nonce collisions/gaps occur, hooks cannot seal the required binding, or evaluator receipts cannot be reproduced without agent-private working-lens state.

### D-A1-05 — closed results, leases, deadlines, retry, and fencing

`agent-runtime/v1` returns exactly one closed envelope: accepted candidate outputs or a typed terminal refusal. It binds dispatch/definition/package/knowledge/authority/host fingerprints, attempt and operation, lease/fencing token, deadline and budget, start/progress/terminal observation identities, effect state, collected output references, validation results, evaluator-dispatch eligibility, retry class, and one permitted continuation. Unknown required or authority fields, incomplete terminal evidence, and ambiguous duplicate envelopes are refused.

The orchestrator owns leases and deadlines. A lease has attempt-bound identity, monotonically increasing fencing token, owner, issued/expiry times, heartbeat deadline, and effect scope. Expiry never proves cancellation. Retry in the same attempt is permitted only before any external effect or when the operation is proven effect-free; otherwise a replacement attempt needs a higher fence and completed reconciliation of committed/observed/pending/uncertain effects. Every external write carries the attempt/fence where the boundary supports it; a stale fence is refused. Where a boundary cannot fence, replacement remains blocked until reconciliation proves the old operation terminal and non-overlapping. Budget exhaustion is a terminal refusal with recoverable state, never a lock.

Revisit if a supported effect boundary cannot accept fencing and cannot offer authoritative reconciliation, clock/deadline skew exceeds the canonical tolerance, heartbeat load breaches the bounded telemetry budget, or a legitimate retry cannot be classified as effect-free or attempt-bound.

### D-A1-06 — compatibility, rollout, and proof

Readers for the definition set, closed runtime result, knowledge proposal/apply receipt, and nonce/lease fields land before writers. During migration there is one active writer and one continuation owner; old and new readers must agree on immutable shared fields. New fields are additive only when they cannot alter authority; phase-definition/DAG authority, result completion, knowledge CAS, and fencing changes require explicit versioning and revalidation. Historical records remain immutable and never gain current authority through adaptation.

S0 records the J0 invariant in `incumbent_2_19_0` mode through the installed public lifecycle/dispatch/collection boundary and real host-event/nonce capability. S1–S2 remain independently landable and must preserve that same incumbent-mode proof. S3 adds the registry/runtime/knowledge/result readers and migrates Design first, switching J0's implementation binding to `agent_runtime_v1`; S4 converges pickup/dispatch and fencing; S5 migrates Product, Plan, Build, Evaluate, and Engineering; S6 migrates Retro. Every S1–S6 exit reruns the same J0 invariant in its declared mode. Agent-runtime severance applies only to S3–S6; S0–S2 sever the incumbent shared lifecycle/dispatch/collection boundary and must demonstrate equivalent user-visible failure. J7 continues to prove actual source coverage → decomposition → Plan → seam manifest → Build → realized conformance with independently parameterized severances and fixture-bypass review.

Revisit if dual-read projections diverge, two writers or continuation owners become active, rollback cannot preserve attempt/fence/knowledge identity, S0–S2 cannot revert independently of S3+, J0 regresses at any slice, or J7 loses actual producer lineage.

## Failure, observability, and privacy

Public recovery distinguishes unsupported capability, invalid input, package mismatch, produces nonconformance, knowledge-fingerprint mismatch, lifecycle API attempt, budget exhaustion, missing/stale/foreign evidence, wait-in-progress, uncertain external effect, degraded observation, setup repair, artifact correction, fixture/producer bypass, new authority, bounded correction, replacement permitted, and permanent non-progress. Budget exhaustion is a terminal refusal with recoverable effect state and no surviving lock. No path reports ready while a required package, producer result, terminal, reconciliation, conformance, real-host/nonce, sealed-telemetry, or authorization fact is missing.

Low-cardinality telemetry records run/phase/attempt/operation/candidate, host/evidence mode, continuation, timing, last progress/wait, terminal outcome, correction/replay counts, effect state, evidence gaps, and next permitted action. It additionally records the phase-definition fingerprint, consumed knowledge fingerprint, knowledge updates proposed/admitted/rejected, lifecycle API attempt count, orchestrator evaluation-lens-set fingerprint, and Taskplane nonce-hook receipt sequence/gaps. Attempts and provider usage are counted once by logical observation identity. Usage retains provider/source/cache semantics; unavailable remains null/unavailable. Dispatch blocks on package or knowledge mismatch; progression blocks on any lifecycle API attempt or evaluator-lens authority mismatch. The terminal-wave producer emits `contract:taskplane.terminal-wave-metrics-receipt/v1`, sealed to the candidate, terminal attempt set, event identities, aggregation inputs, null/unavailable semantics, and content digest. Plan readiness names this required output; Build/Engineering surface it as a blocker when absent or stale; J6 consumes it; Retro must refuse to seal without it; finalization and publication consume the same sealed receipt and may not reconstruct it from logs.

Portable artifacts use positive field allowlists and provenance references. They reject credentials, secrets, full prompts/transcripts, copied authority, predecessor private runtime, arbitrary host paths, exported skill bodies, unscoped knowledge, gate-passing strategy knowledge, and free-form payloads that could carry them. Knowledge updates obey the same allowlist, scope, retention, and forbidden-field rules. Private state and portable outputs have explicit retention owners; deletion/minimization propagates to derived portable copies, while immutable audit evidence that must remain is minimized at creation and retained under the governing policy.

## Rollout and rollback

Roll out behind candidate-bound readers and feature selection, one slice at a time. S0 first records J0 green on the installed Taskplane 2.19.0 baseline and proves host-event or Taskplane nonce observation. Every S1–S6 exit reruns J0 against its exact candidate. Establish every independently parameterized negative boundary case before changing its producer. Use additive dual-read/single-write transitions wherever authority semantics allow; for breaking handoff semantics, version and revalidate explicitly. S0–S2 may merge as the Foundation branch after Foundation Acceptance even if S3+ is not yet authorized. Expansion begins only after Expansion Revalidation against the exact accepted foundation. S3 adds definition/knowledge readers, the thin runtime facade, Design-agent migration, J7, and FP-AC19; S4 converges pickup/dispatch; S5 migrates Product, Plan, Build, Evaluate, and Engineering; S6 migrates Retro. Gate each cutover on J0, current positive/severed selectors, fixture-bypass review, compatibility fixtures, no competing owner, and readable retained evidence.

Rollback changes active routing, not history. It stops new dispatch, reconciles active operations, restores only the prior sole caller route for new attempts, leaves phase-definition/knowledge/nonce/runtime fingerprints and successor artifacts immutable/readable, preserves completed work and operation identity, and never erases admitted knowledge or converts historical bytes into current authority. Expansion rollback preserves the independently accepted foundation; foundation rollback invalidates expansion revalidation and blocks J6/finalization. Any J0 regression blocks landing and triggers rollback before the next slice. The whole candidate can be reverted by PR if a slice cannot maintain one owner or its public journey fails.

## Design readiness and completion

Design DoR remains grounded in the current R-0001 acceptance set, empty Product blockers, source baseline, graph fingerprint, selected component decomposition, exact 16-lens team, and no language-specific override. This provisional merge is not ready for approval: architecture, security, testability, data-safety, integrability, cost-finops, sre, tradeoffs, and solution-design require fresh evidence against the merged content before final synthesis. All other dispositions stand unless those reruns raise a new positive signal.

After fresh lens synthesis, Plan readiness additionally requires the exact validated Design/test-strategy/phase-definition fingerprints, the thin-runtime boundary, governed proposal/apply knowledge CAS, closed runtime results, lease/deadline/retry/fencing rules, J0 baseline and per-slice regression obligations, J7 producer chain, the 24-entry independently parameterized negative inventory, the gate table, Taskplane nonce contingency, and the terminal-wave telemetry prerequisite. Design DoD covers all 21 criteria, 8 journeys, 18 contracts, 25 proposed edges, 1 disclosed new facade module, 6 proposed A1.1 decision records, 7 slices in 2 delivery branches, 10 gate rows, 24 criterion/journey negative-case entries plus the parameterized A1.1 selector set, 26 risks, 24 failure modes, 26 lens dispositions, and zero open questions. Runtime readiness and finalization remain mechanically blocked when J0 regresses, a phase owns lifecycle/evaluator authority, a result envelope is open, knowledge CAS/provenance/sealing is invalid, retry effects are unclassified or unfenced, or the sealed terminal-wave receipt is missing/stale/foreign. Retro cannot seal around that blocker. The Design content fingerprint and test-strategy seal are explicitly pending supported recomputation after these apply-patch-only changes; their prior values cannot authorize Plan.

No visualization is generated: the dependency graph is intentionally module-coarse and the lifecycle, migration, and slice ordering are clearer in the contract tables and ordered prose than in a large decorative graph.
