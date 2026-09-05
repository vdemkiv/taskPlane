# Stateless phases — consolidated architecture review and improvement report

Date: September 5, 2026  
Reviewed source: `795c5ac633ee4288881c5715627915927c9ae114`  
Comparison baseline used by the preceding audit: `b0dcda0a3deff70947e93cf1be5af2f90cec3236`  
Context: PR #17 and the stateless phase / pickup / end-to-end workflow effort  
Status: **Review recommendations only. No implementation, requirement amendment, workflow transition, approval, or formal Engineering sign-off is performed by this report.**

## 1. Executive conclusion

The intended architecture is sound: a workflow phase should be a reusable execution unit that receives explicit inputs, performs its assigned work, validates its result, obtains the applicable authorization, and publishes a complete durable output. Design, Plan, Build, and later phases should share these mechanics.

The present implementation does not consistently realize that model. It reuses some underlying utilities but independently composes lifecycle behavior in several places. This produces duplicated responsibility, inconsistent recovery, incomplete handoffs, and tests that can pass without proving the production connections.

The five identified defects are not five unrelated bugs. They expose three connected weaknesses:

| Weakness | Defects | Architectural consequence |
|---|---|---|
| Incomplete or distorted behavioral contracts | A: missing downstream artifact; D: receipt representation restricts planning | The producer and consumer do not share one complete contract, and storage convenience dictates valid work decomposition. |
| Unreliable substantive judgment | C: collector trusts a reported pass despite unresolved high findings | Valid identity and bytes are mistaken for sufficient evidence that the work is acceptable. |
| Incomplete execution recovery | B: partially persisted setup; E: wrong refusal and recovery advice | Repeated calls neither reliably reconcile the attempt nor identify the action that would allow progress. |

The testing problem cuts across all three: fixtures reconnect paths that production leaves disconnected.

**Recommended disposition:** do not accept the reviewed change as a demonstrated end-to-end workflow fix. Retain useful primitives and corrections, restore trustworthy judgments and truthful recovery, then consolidate execution ownership and prove the complete workflow through representative journeys.

**Fix priority: C → E → A → B → D.** This is a priority order within a future approved improvement effort, not authorization to start coding now.

The objective is fewer independent implementations of the same responsibility—not a smaller arbitrary module count and not another framework over the existing frameworks.

## 2. Evidence and limits

This report merges the preceding architectural assessment, the reusable-phase proposal, and the supplied external feedback into one improvement-oriented assessment. It does not treat the feedback as additional executed test evidence.

The preceding audit inspected the named candidate against its baseline and reported five isolated diagnostics. A was a structural producer/consumer contract gap; B used a mocked interruption during persistence; C used controlled review results; D used two pure task/proof cases; E demonstrated public error projection. These are concrete diagnostics, but they are not a real-host end-to-end completion trace.

The prior audit also recorded green CI, including 4,373 passed tests, one skipped test, one deselected test, and 597 subtests. That is useful regression evidence, not proof of the missing composed journeys. This report does not rerun those tests or independently refresh remote CI status.

Important distinctions are maintained throughout:

- A new-path defect is not automatically a demonstrated regression from a formerly working baseline.
- A pre-existing gap is not a new PR regression, but it can still leave the user's stated end-to-end objective unmet.
- An architectural recommendation is not an approved Design Contract.
- A review document is not a completed formal Engineering gate or a human approval.
- No historical collector result was revalidated as part of preparing this document.

## 3. Root cause: shared mechanics do not have one owner

The system has three overlapping execution surfaces: the legacy delivery loop, the existing stage lifecycle, and repository-phase coordination. They are not three wholly independent engines. They share kernels and helpers, but that reuse has not produced a single coherent execution model.

Successful progress currently depends on agreement among portable handoffs, active contracts, dispatch intents, expected dispatch records, terminal observations, review caches, and admission records. Some separate records are legitimate. The problem is treating several independently updated representations as if they already form one recoverable operation.

The clearest example is duplicated owner and reviewer setup. Both prepare contracts and dispatch expectations, but their replay behavior differs. The reviewer repairs a pending expectation; the owner can report readiness without doing so.

The broader pattern is:

1. A failure is encountered at a boundary.
2. A local coordinator, cache, adapter, or validation is added.
3. Existing coordination remains active.
4. More records must agree before progress is possible.
5. The recovery path is not designed and tested as a complete operation.

This is why individual safety checks can become stronger while the workflow remains unable to progress.

Being the only human user does not eliminate interruption, multiple processes, duplicate hook delivery, or uncertain external execution. Those require modest, correct execution semantics—not enterprise-scale infrastructure and not removal of verification.

## 4. Detailed findings and required improvements

### C — Review collection can report pass while carrying unresolved high findings

**Priority:** first; high trust impact.  
**Evidence class:** reproduced defect in the new collection path.

The controlled reproduction supplied four valid-shaped review results that each reported `outcome: pass` while containing an open high-severity finding. Collection returned `status: pass` and retained the findings.

The collector verifies identity, observed bytes, fingerprints, and coverage of the selected workers. Its final status nevertheless depends on the workers' outcome strings. The component responsible for evaluating evidence is relying on a self-reported conclusion at the decisive point.

Evidence: [collection decision](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/phase_review.py:243).

**Required improvement**

Apply the canonical finding normalization, classification, and blocking policy before reporting pass. Keep transport/provenance validation and substantive admissibility as separate checks. Reuse the existing policy rather than writing a phase-specific interpretation of severity.

A high-severity finding must be classified correctly; pre-existing debt and observations should not be silently promoted into new regressions. Equally, missing or misleading classification must not hide a genuine blocking finding in the changed surface. Missing required evidence must remain an explicit verification gap, not disappear behind a pass label.

**Acceptance conditions**

- A reported pass accompanied by a policy-blocking unresolved finding cannot yield an admissible pass.
- Missing required evidence cannot be converted to pass by a worker's outcome field.
- Legitimate non-blocking findings remain visible without being incorrectly escalated.
- The same finding classification produces the same admissibility decision across applicable consumers.
- Corrected or resolved findings remain linked to their evidence and candidate; resolution is not inferred from a changed outcome string alone.

**Historical result handling**

The defect does not establish that every prior pass was wrong. It establishes that the affected collector's pass alone is insufficient assurance. Revalidate affected passes that are being relied on using retained evidence and corrected policy. Preserve the original record and append the revalidation result. Unavailable evidence means unverified, not retrospectively approved and not automatically proven defective.

### E — A specific execution failure is converted into the wrong recovery advice

**Priority:** second; high progress and diagnosability impact.  
**Evidence class:** reproduced error-projection defect.

A failure such as “phase review child has no observed terminal receipt” can become `handoff-malformed` with generic advice to restore the handoff. The actual missing object is an execution observation, not necessarily the handoff.

Following that advice leaves the real cause unchanged. Retrying then produces the same failure and the same advice. This explains a concrete mechanism for the observed loop; it is not proof that every overnight retry had this cause.

Evidence: [public failure projection](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/tp.py:8137).

**Required improvement**

Adopt one rule across phase boundaries: **a refusal identifies the actual problem and an authorized recovery action.**

The common refusal/result contract should preserve:

- Stable reason and concise explanation.
- Affected phase and attempt identity.
- What is already committed, observed, still pending, or uncertain.
- The permitted next action and its prerequisites.
- Whether the action continues the same attempt or explicitly permits a replacement.

This belongs in the existing error/result boundary, not a new error-handling subsystem. Avoid exposing sensitive internal data in user-facing diagnostics.

**Acceptance conditions**

- Missing terminal evidence names the relevant attempt and the applicable observation/wait/reconciliation action.
- An unchanged retry does not allocate a new attempt or discard completed work.
- Waiting, correcting an artifact, repairing incomplete setup, and requesting new authority remain distinguishable actions.
- Recovery actions are checked against current state and authority when executed.
- Repeated non-progress stops automatic retries and presents the precise blocker; expected waiting uses the named event mechanism rather than repeated unchanged calls.

The agent also needs a behavioral safeguard: repeating the same refusal without a state change is not progress. Correct recovery instructions do not remove the responsibility to recognize this.

### A — Design does not export an artifact that the downstream quality path requires

**Priority:** third; high integration impact.  
**Evidence class:** confirmed structural gap in the fresh production path.

Design can reference a test strategy, but its normal allowed output set and export path do not produce/select that strategy artifact. Plan requires it among the sealed inputs when referenced. Native tasks carrying the quality test contract depend on that authority.

The standalone quality test supplies the strategy directly, so it cannot prove the Design producer supplies it.

Evidence: [Design output paths](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/phase_handoff.py:257), [Plan's selected-input check](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/phase_plan.py:143).

**Required improvement**

Treat phase handoffs as producer/consumer seams, just like worker or task boundaries. Define the complete artifact set for the selected downstream path, including required inherited artifacts and artifacts produced by the current phase. Validate that set before declaring the producer complete.

Use the existing graph, wiring-closure, artifact and evidence mechanisms. Do not add a separate phase-seam ledger or duplicate dependency validator.

**Acceptance conditions**

- The normal quality-enabled Design path publishes the actual strategy bytes and references required by Plan and Build.
- A fresh successor can verify its complete input package without reading the predecessor's private runtime.
- Missing, stale, or mismatched required artifacts prevent completion at the relevant boundary with a specific reason.
- Removing the strategy from an otherwise valid production-produced handoff breaks the composed test observably.
- Tests do not hand-create the missing artifact between producer and consumer.

### B — Owner setup is not a recoverable operation across its durable writes

**Priority:** fourth; high execution-correctness impact.  
**Evidence class:** isolated interruption reproduction with mocked persistence.

Owner setup activates a contract and then records the expected dispatch. An interruption between those writes leaves the contract present but the expectation missing. Replay finds the contract, does not restore the expectation, and can return `dispatch_allowed: true`.

The reviewer path already re-establishes expectations for pending children. This is duplicated responsibility with divergent recovery.

Evidence: [owner setup and replay](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/phase_dispatch.py:263), [reviewer setup and replay](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/phase_review_host.py:186).

**Required improvement**

One shared attempt-setup operation must complete or reconcile all required local records before reporting readiness. Reuse transactional and operation-replay facilities where they already exist.

Atomic writes of separate files are not an atomic operation. Also, an atomic local operation does not guarantee exactly-once remote worker execution. External dispatch requires idempotency or reconciliation against observed host work before retrying an uncertain action.

**Acceptance conditions**

- Fault injection at each relevant persistence boundary cannot yield readiness while a required record is absent or inconsistent.
- Replaying the same logical operation repairs incomplete setup or returns the prior valid result.
- Reusing an operation identity with different input is rejected.
- Owner and reviewer setup pass the same interruption/replay contract tests.
- An uncertain external dispatch is reconciled before another launch; inability to reconcile remains a specific blocker.

### D — Receipt representation imposes invalid planning restrictions

**Priority:** fifth; high generality and domain-model impact.  
**Evidence class:** two reproduced task/proof restrictions.

The portable projection compares the set of required proof commands with a single-element list containing the native test command. Separately, obligation validation requires exactly one task owner based on acceptance overlap.

Consequences include rejecting a task with two distinct required proofs and rejecting frontend/backend tasks that both contribute to one acceptance criterion.

Evidence: [proof projection](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/phase_plan.py:89), [obligation ownership](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/phase_inputs.py:206).

**Required improvement**

Separate task completion from requirement acceptance. Tasks and acceptance criteria have a many-to-many contribution relationship. Criteria may require multiple explicit proofs. A single accountable acceptance owner may still be designated without forbidding multiple contributing tasks.

Task completion records what the task finished. Acceptance evaluation aggregates the required contributions and proof evidence. Receipt representation must follow this model rather than dictate the shape of the plan.

**Acceptance conditions**

- One task can contribute to multiple criteria and carry multiple exact proof requirements.
- Multiple tasks can contribute to one criterion without a false scope-widening refusal.
- Missing any required contribution or proof prevents acceptance.
- Joint behavior can require its own integration proof; individual task completion is not automatically joint acceptance.
- Proof identity remains explicit. The system does not guess equivalence between different shell commands to make a receipt fit.

## 5. Additional architectural gaps that must remain visible

### 5.1 Build completion is not workflow finalization

The portable Build endpoint exports terminal evidence without a complete stateless continuation into Evaluate, Engineering/sign-off, and Retro. The prior audit verified that this endpoint existed in the baseline: it is unfinished scope, not a newly introduced regression.

It nevertheless prevents the complete end-to-end claim. Later phases must consume explicit durable results through the same phase mechanism, rather than requiring an agent to reconstruct legacy state.

Evidence: [portable successor selection](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/loop.py:2741).

Portable references must provide access to the evidence required by each consumer. Fingerprints and proof counts alone do not preserve full proof output. A copied locally authenticated receipt also does not transfer its authentication authority. Do not export secrets or claim cross-checkout authenticity from an ordinary hash.

### 5.2 Approval semantics differ across paths

The facade describes mechanical phase completion followed by consolidated pre-implementation authorization, while the repository protocol lists separate initial, Design and Plan approval requirements. Interrupted output can also be presented as a phase-approval event. The underlying gate list predates this PR.

The improvement is one explicit approval policy consumed by every execution path. Preserve three different facts: work was produced, evidence was validated, and progression was authorized.

Valid authorization should be reusable within its recorded scope. Saving or resuming an unfinished draft is not approval of the completed result. This report neither chooses new authority nor waives existing gates; the policy discrepancy must be resolved in the future Design.

### 5.3 Host capability must be established before dispatch readiness

The identity adapter documents reliance on an observed transcript format rather than a stable public API. Admission also relies on observing a literal role marker. The prior live failure occurred at this boundary; the final candidate added diagnostics, not demonstrated end-to-end host compatibility.

Evidence: [host identity boundary](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/worker_hook_identity.py:1), [marker-based admission](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/tp.py:1601).

Keep verification, but verify the host's ability to supply it before advertising dispatchable work. Unsupported capability needs an exact compatibility refusal. Phase processors should not inspect transcripts or guess identity. Duplicate event delivery should converge through one effective observation/processing owner with replay protection.

### 5.4 More files do not necessarily mean more modularity

The prior audit counted 12 new runtime modules with roughly 2,856 lines, alongside 841 additions across loop.py, taskplane_lite.py, and tp.py. Some code was extracted, but the central coordination surface also grew.

The final import graph introduced no new strongly connected components according to that audit. That is a positive result, but it does not prove clear responsibility boundaries. Calls into private loop helpers and duplicated orchestration still create coupling without introducing a new import cycle.

Do not delete useful modules to hit a target count. Measure independent lifecycle implementations, duplicated decisions, private cross-boundary dependencies, and the work required to add another phase.

## 6. Target architecture: one lifecycle, small phase processors

### 6.1 Reuse the existing foundation

- [StageLifecycle](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/stage_entities.py:1262) already supplies shared stage operations over immutable stage values.
- [RunStore](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/run_store.py:816) already supports transactional lifecycle updates, operation identity, request fingerprint checks, replay receipts, and a persisted journal outbox.
- [Delivery ports](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/delivery_ports.py:235) already separate host capabilities and evidence operations.
- [Design validation](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/design_contract.py:929) and [graph readiness](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/depgraph.py:2961) already contain reusable checks over explicit inputs.

These are foundations to reconcile and reuse, not proof of a drop-in migration. Existing stage and repository handoff formats have different semantics and must be handled explicitly.

### 6.2 Responsibility boundaries

| Unit | Responsibility | Forbidden responsibility |
|---|---|---|
| Workflow definition | Phase order, dependencies, applicable reviews, approval policy, allowed correction routes | Dispatch implementation or private retry logic |
| Shared phase runner | Start/pickup, attempt reconciliation, dispatch, collection, validation sequencing, completion and continuation | Phase-specific reasoning or independently invented approval policy |
| Phase processor and validator | Transform explicit inputs into declared outputs; judge phase-specific correctness | Its own dispatch engine, lifecycle store, transcript parser, or retry loop |
| Storage and host adapters | Durable records, artifact access, host capability, execution observation and permitted side effects | Deciding the workflow's substantive progression policy |

These are conceptual responsibilities, not instructions to add four new packages. Prefer small functions, ordinary data structures, and the existing interfaces. No generic workflow language, dynamic plugin framework, large inheritance hierarchy, or extra coordination service is justified by the current need.

### 6.3 Common lifecycle

The same entry point handles both first execution and pickup:

1. Validate the explicit input package and applicable authority.
2. Load or establish the attempt identity.
3. Reconcile persisted state and observed external work.
4. Run the selected processor when permitted, or return the specific pending action.
5. Collect actual output and evidence for that attempt.
6. Apply common integrity checks and phase-specific semantic validation.
7. Satisfy applicable independent review and approval policy.
8. Commit the complete result and publish the verified successor package.
9. Return the declared continuation without inheriting predecessor runtime.

If waiting or correction is required, the runner returns an explicit continuation rather than spinning inside a generic retry loop. Review workers can use the same execution primitive without gaining self-approval authority or permission to recursively create reviews.

### 6.4 Phase-specific behavior

| Phase | Specific work that should remain distinct |
|---|---|
| Design | Alternatives, trade-offs, contracts, dependency decisions, output definitions and test strategy |
| Plan | Task decomposition, ordering, scope, acceptance contribution and proof mapping |
| Build | Scoped code changes, declared tests, candidate-bound quality and integration effects |
| Evaluate / Engineering | Evidence sufficiency, requirement coverage, findings and conformance judgment |
| Retro | Outcomes, telemetry, lessons, corrective recommendations and follow-up records |

These differences justify processors, permissions and validators. They do not justify separate versions of start, wait, resume, collect and finish.

### 6.5 Stateless does not mean stateless storage

The processor must be replaceable by a fresh execution with no predecessor conversation or private runtime. Its input package contains versioned artifacts, applicable scope/authority, output requirements, and any necessary durable checkpoint. Its result contains produced artifacts, evidence, unresolved issues, and completion/continuation status.

Execution ownership and uncertain external effects still require durable records. One owner decides authoritative phase progress; caches and status projections are derived or reconciled.

Immutable output artifacts may be written before the authoritative completion commit, but the phase is not complete until the full required set is committed and usable. Incomplete publication must not appear as an approved successor input.

## 7. Disposition of current modules

| Existing surface | Proposed disposition | Intended simplification |
|---|---|---|
| phase_inputs | Retain explicit input selection and validation; correct ownership/contribution semantics | One input boundary without invalid planning restrictions |
| phase_output | Retain shared output-byte observation and validation | One output observation boundary |
| lens_catalog | Retain the shared catalog | No duplicate catalog or per-phase copies |
| phase_entry | Retain only entry/input adaptation that remains necessary | No separate lifecycle owner at entry |
| phase_plan | Retain Plan-specific projection and validation | No private Plan dispatch/recovery engine |
| phase_build | Retain Build-specific scope, quality and integration behavior | Shared lifecycle with explicit Build capabilities |
| phase_review | Retain selected-result validation and apply canonical substantive findings policy | Collection cannot confuse valid bytes with acceptable judgment |
| phase_dispatch and phase_review_host | Consolidate attempt preparation, reconciliation, dispatch and collection behavior | One continuation owner instead of divergent owner/reviewer implementations |
| phase_admission | Reconcile common admission responsibilities with lifecycle and host capabilities | No competing readiness authority |
| phase_producer | Keep only a temporary compatibility facade if callers require it | Remove an unnecessary layer when migration is complete |
| worker_hook_identity | Keep host-specific identity logic behind a version-aware adapter | Phase processors do not depend on transcript internals |
| loop / stage handoff / repository handoff surfaces | Select one live owner for each responsibility; preserve historical formats through explicit adapters | No parallel active lifecycle authority for the same attempt |

Retain receipt coverage, exact-byte provenance, shared validators, and compatibility tests that execute historical behavior against its actual generation. Correct their integration rather than removing the protections.

Every migration slice must identify the code path it removes or disables. A new shared runner left alongside all old active coordinators would repeat the current failure.

## 8. Why the current tests miss the failures

The composed chain test starts from prepared inputs, supplies synthetic completions and reviews, uses a Plan without the strategy-dependent quality path, does not require dispatch permission before constructing its Build behavior, and stops at Build. The quality test separately constructs its own strategy input.

Evidence: [composed chain fixture](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/tests/test_phase_dispatch.py:179), [quality wiring tests](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/taskplane/tests/test_phase_quality_wiring.py).

Both test families can pass while their production connection is broken. The fixtures supply the missing connection.

### Fixture-bypass rule

Hand-built predecessor outputs are legitimate for isolated consumer unit tests. Such tests prove the consumer's behavior under supplied inputs; they do not prove the producer emits those inputs.

For a boundary or end-to-end claim, downstream input must come from the actual upstream production path. Do not insert a strategy, terminal receipt, review judgment, or dispatch permission that the real producer failed to create.

Apply this rule to all predecessor-output construction, not only worker seams. Include positive and deliberately severed boundary tests so removing the actual connection changes the public result. Do not sever unrelated paths or use ceremonial mutations that cannot affect the asserted behavior.

Simulated hosts are valuable for deterministic fault injection, but they must be labeled as simulated evidence. A missing real-host run is unverified, not a successful fallback.

## 9. Required acceptance suite

These six journeys should be specified and represented by tests before further coordination implementation. Establish the known failing cases, then make those same journeys pass through the production boundaries.

| Journey | Positive evidence | Required failure/recovery evidence |
|---|---|---|
| J1 — Real native start and terminal event | Authorized dispatch, genuine observed start/identity, actual terminal evidence and collected output | Missing identity or terminal evidence yields an exact refusal; no fabricated completion or duplicate dispatch |
| J2 — Quality-enabled Design → Plan → Build | Design produces the complete strategy/output package; fresh downstream phases consume it through public interfaces | Remove or alter a required artifact: the boundary fails visibly before unsupported progression |
| J3 — Interrupted setup and replay | Repeating the same logical operation completes or reconciles setup | Inject interruption at each relevant persistence/effect boundary; never report readiness with missing records or launch duplicate work |
| J4 — Rejected review and bounded correction | A corrected candidate with sufficient evidence advances through the allowed route | A pass label with a genuine blocker does not pass; unchanged retries preserve identity and completed work |
| J5 — Multiple tasks and proofs | Multiple contributors per criterion and multiple exact proofs per task are supported | Missing contribution, missing proof, stale candidate or incomplete integration coverage prevents acceptance |
| J6 — Build through finalization | Complete portable results reach Evaluate, Engineering/sign-off, Retro and the declared final state | Missing downstream evidence or authorization prevents completion; Build alone cannot stand in for the whole journey |

Parameterize shared lifecycle tests over owner/reviewer roles and applicable phases. Include fresh-execution pickup, duplicate events, cancellation and interruption where they affect these journeys. Keep the suite focused on meaningful boundaries rather than producing another large collection of disconnected fixtures.

If the host cannot support a required observation, report the journey as unverified with the capability reason. Synthetic success or a skip cannot satisfy the real-host acceptance condition.

## 10. Requirement changes proposed for a later design phase

The feedback recommends extending the seam specification, broadening the fixture-bypass rule, and separating atomicity/recovery into its own requirement. The conceptual recommendation is accepted, with an identifier caveat.

| Proposed requirement area | Content | Reuse constraint |
|---|---|---|
| All-boundary seam closure | Cover worker, task and phase producer/consumer contracts; require complete downstream artifact sets | Extend canonical graph, wiring closure and evidence mechanisms |
| Evidence fidelity and admissibility | Keep observed bytes distinct from substantive judgment; prohibit fixture-repaired boundary claims | Reuse finding classification and evidence validation |
| Attempt setup and recovery | Transactional local setup, replay reconciliation, truthful refusals, authorized continuation and non-progress handling | Use one lifecycle/continuation owner and existing persistence |
| Task contribution and acceptance | Separate completion from acceptance; support many-to-many contributions and multiple proofs | Extend domain data without a second requirement authority |

The inspected [R-0004 specification](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/specs/spec.md:62) already requires closed seams, current provenance and canonical-mechanism reuse. Phase boundaries should extend that principle, not become a parallel design.

The supplied feedback names R-0005 and R-0005.5. Those exact definitions were not located in the inspected workspace specification files. Their amendment target must be resolved against the relevant requirement record rather than assumed. This checkout also already contains [R-0006 for host-capability parity](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/specs/r0006-host-capability-parity-spec.md). Do not reuse that ID for a different recovery requirement.

Separating recovery from seam closure is sensible because their acceptance evidence differs. That separation does not imply separate engines, extra coordination layers, or necessarily different people. No requirement identifiers are allocated or specifications edited by this report.

## 11. Proposed improvement sequence — not initiated

### Step 0: Settle the Design before changing coordination

Use this report as input to a future current-scope Design Contract. Settle ownership, named boundaries, complete artifact sets, findings/refusal semantics, acceptance mapping, compatibility, rollout and rollback.

An older Design file does not automatically cover the new repair scope. Conversely, absence of a referenced current contract is not proof that no Design artifact exists anywhere. The required evidence is an explicit binding between the proposed work, its current Design, and the eventual diff.

Define the six journey tests and intended removals before adding more coordination code. This moves the architectural judgment earlier without asking for another framework or a larger worker fan-out.

### Step 1: Restore trust — C

Correct collector admissibility using the canonical finding policy. Add contradictory-result regressions. Identify the affected historical passes that need revalidation without rewriting them.

### Step 2: Restore truthful continuation — E

Preserve specific refusal reasons and allowed next actions. Preserve attempt identity on unchanged retries. Prove missing terminal evidence does not become a handoff-repair instruction.

### Step 3: Close phase contracts — A

Produce and publish the complete quality-enabled artifact bundle. Prove the actual Design output reaches Plan and Build through fresh executions. Remove test scaffolding that concealed the missing connection in boundary claims.

### Step 4: Consolidate execution recovery — B

Bring owner and reviewer setup onto one operation/reconciliation path. Reuse the existing lifecycle and transaction facilities. Remove the superseded coordination behavior in the same bounded migration slice.

### Step 5: Correct the domain model — D

Support task contributions and exact proof lists independently from requirement acceptance. Add joint-contribution and incomplete-coverage cases. Preserve explicit compatibility for historical records where valid.

### Step 6: Complete the workflow and retire the legacy duplication

Wire all remaining consumers through explicit phase results. Prove J6 on a real supported host with required authorization and evidence. Reduce obsolete loop/adaptor responsibilities once the replacement path is demonstrated.

Use small PRs with bounded scope. Each needs changed-boundary tests, evidence of the production connection, and a list of retired paths. Existing historical records remain immutable; migration does not silently reinterpret authority. Pin each run to one supported execution path so two lifecycle owners cannot become active for the same attempt.

Do not claim completion based on component CI, document generation, synthetic terminal events, or the disappearance of a refusal. Completion requires the declared final workflow outcome and its evidence.

## 12. Observability and non-progress controls

Record execution telemetry in the shared path, not separately in every phase:

- Run, phase, attempt and operation identity.
- Input/candidate identity and current continuation state.
- Start/end time, elapsed time and actual observed terminal outcome.
- Last meaningful progress event and waiting reason.
- Retry/replay count, correction reason and whether an external effect is known or uncertain.
- Required evidence present/missing and the actual next permitted action.
- Available provider token usage, including cache semantics, with provenance and availability.

Replay must not count an attempt or its token usage twice. Provider counters must be interpreted according to their semantics; missing usage remains unavailable rather than zero. Do not persist full prompts, transcripts or secrets merely to obtain telemetry.

Non-progress is not simply elapsed time. A legitimate long-running worker may be progressing or waiting for an expected event. The relevant warning is repeated identical failure or recovery without a new observation, changed input, or permitted state transition. That should stop blind retries and expose the reason.

## 13. Definition of an improved system

The modularity test is:

> Adding another phase that uses existing execution capabilities requires its definition, processor and domain validation—not edits to dispatch, pickup, storage, hooks, or the shared lifecycle.

This is necessary but not sufficient. Improvement is demonstrated only when:

1. One execution mechanism owns lifecycle behavior for the migrated phases and reviewers.
2. Complete output contracts connect every required producer and consumer.
3. Pickup reconciles the same attempt and preserves completed work.
4. Collected pass means the required evidence is substantively admissible under the applicable policy.
5. Refusals identify the actual problem and an authorized next action.
6. Task decomposition supports legitimate many-to-many acceptance contributions without weakening proof coverage.
7. The six journeys pass with honestly labeled real versus simulated evidence.
8. Superseded execution paths are removed or explicitly inactive, with historical compatibility preserved.
9. The full workflow reaches its declared final state without manual runtime repair.
10. Review, approval, telemetry and finalization claims match retained evidence.

No target module count, line-count reduction, arbitrary timeout, new schema count, or estimated delivery duration is asserted as proof of success.

## 14. Final review recommendation

Keep the sound primitives, shared validation, receipt coverage and evidence-provenance improvements. Correct the trust and recovery defects first. Consolidate owner/reviewer coordination into the existing lifecycle, make phase contracts complete, and separate task completion from acceptance.

The repair must reduce the number of places that decide what happens next. It must not add another continuation owner above the old ones.

The willingness to reproduce defects and reject an unsupported success claim is valuable. Move that scrutiny earlier—into Design, seam tests, and pre-merge evidence—while retaining the agent's responsibility to notice non-progress.

**Review outcome: improvements required; implementation remains out of scope for the current request.** This is the consolidated review report for planning those improvements, not their execution or formal approval.

## Sources

- [Original architecture verdict and eleven diagrams](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/docs/reviews/pr-17-architecture-verdict-and-diagrams.md) — Parts I and II retain the original audit and visual explanation.
- [Supplied additional feedback](/Users/vdemkiv/.codex/attachments/f7dd826e-9530-4bbc-aace-4beb9e7b3b4b/pasted-text.txt).
- [Accumulated phase-boundary RCA](/Users/vdemkiv/.codex/worktrees/a522/taskPlane/docs/reviews/phase-boundary-repair.md) — historical progress entries are not treated as final sign-off.
- Candidate source locations are linked beside the findings and reusable components they support.
