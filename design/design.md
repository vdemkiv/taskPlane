# R-0001 Design — Stateless `tp pickup` front door

Status: proposed HOW for human review. This Design is an overlay only. It does not approve itself, authorize a Plan, start Build, or authorize push, tag, publication, or release mutation.

## Decision

Select a **thin stateless pickup adapter over the existing BUILD-C boundaries**.

`tp pickup <design-contract>` resolves one repository-relative signed shelf Design Contract, verifies its existing approval and engine receipt, checks a clean exact authorized Git identity, and selects exactly one contract element. A new `taskplane/pickup.py` module derives an in-memory, one-element micro-plan and asks `taskplane/build_c.py` for the existing direct-scope assignment, acceptance-checkpoint, and green-integration operations. Pickup does not initialize or load the legacy loop, and it never creates a run, track, wave, claim, lease, per-task lens, or equivalent private coordination record.

The only durable handoff is an append-only receipt chain below `exports/pickup/`, rooted by the authorized source SHA and Design evidence fingerprint. Each receipt binds one acceptance criterion, its predecessor receipt, the exact assigned revision, the engine checkpoint receipt, and the repository merge outcome. A new checkout reconstructs the next criterion solely from the Git-tracked Design authority and that chain; an empty or unrelated `TASKPLANE_HOME` changes no result.

This is an adapter, not a second orchestration system. `taskplane/checkpoint.py` remains the checkpoint specification and engine-receipt owner; `taskplane/repository.py` remains the worktree/merge owner; `taskplane/design_contract.py` remains the Design evidence-fingerprint and approval-verification owner. `taskplane/build_c.py` exposes small loop-independent façades around its incumbent direct-assignment and green-integration validation so pickup does not copy them. Existing loop entry points retain their signatures and behavior. The hook security layer is unchanged.

## Grounded current state at `726acd108d3ca431e680183de129918842202eda`

The Taskplane current-state inventory is empty, so this design is grounded in the supplied baseline graph and cited repository sources:

- The supplied graph baseline is `e0d54a84434269c488941a265f865803c90c7e8adaa0796159ea60a3257adc8b`: 50 modules and 165 edges. Design traversal remains local depth 3, contract-only at named boundaries, one contract hop, and one requirement hop.
- `taskplane/tp.py` owns the public parser and user-facing error boundary. It has no `pickup` command at the baseline.
- `taskplane/build_c.py::assign_scopes` already selects dependency-ready graph-disjoint scopes and documents that the direct path creates no legacy wave, claim, build-lease, per-task review, Evaluate, or Fix state. `integrate_on_green` already rejects mixed caller evidence, validates an engine-green checkpoint for the exact registered revision/scope, and delegates the merge to `RepositoryManager`.
- `taskplane/checkpoint.py::validate_checkpoint_spec` fails before execution on unknown fields, an untracked or dirty proof, scope mismatch, or stale HEAD. `validate_and_mint` accepts the incumbent governed-command wait result, derives engine-owned receipt fields, and binds the exact worktree revision, scope, predecessor receipt digests, command output, and active engine contract.
- `taskplane/design_contract.py` already computes Design content and evidence fingerprints and validates current approved Design evidence. Pickup consumes this authority; it does not define a second approval mechanism or accept a digest lookalike as approval.
- `taskplane/repository.py::RepositoryManager.merge_registered_task` is the incumbent merge owner and returns durable repository receipt data. Pickup must reach this owner through BUILD-C authorization, not invoke Git merge itself.
- `taskplane/storage.py` shows the private run/worktree/claim layout that pickup must not use as its handoff. It is a negative witness in AC1 and AC4, not a new pickup store.
- `README.md` and `CHANGELOG.md` describe the existing BUILD-C checkpoint, bounded review, and legacy flow. The three plugin manifests currently name 2.17.19. Version and release-note changes are last, only after AC1–AC5 are green.

No current source provides a stateless public front door, a one-element pickup micro-plan, or a Git-resident pickup receipt chain. Those are the only new capabilities.

## Alternatives considered

### A. Thin stateless adapter over BUILD-C (selected)

Add `taskplane/pickup.py`, a public CLI adapter, and the minimum loop-independent BUILD-C/repository entry seams. Keep the shelf authority, checkpoint engine, direct assignment rules, and merge validation with their incumbent owners.

- Gains: satisfies zero orchestration state; reuses the live checkpoint and green-integration code; makes the pickup-to-BUILD-C edge mutation-testable; preserves the legacy loop and hook layer; enables Git-only resume.
- Costs: BUILD-C needs a small explicit-input façade because its current integration wrapper reads loop state; receipt lineage and collision checks become a new repository artifact contract.
- Revisit when: more than one contract element must be scheduled concurrently, a required existing BUILD-C boundary cannot accept explicit verified inputs without semantic change, or Product authorizes a broader orchestration mode.

### B. Create an ephemeral loop and delete it after pickup

Initialize a normal Taskplane loop, synthesize a one-task Plan, run the existing loop path, then remove its private state.

- Gains: reuses the current top-level loop wrappers with little refactoring.
- Costs: creates exactly the run/track/claim/lease/wave-equivalent state R-0001 forbids; deletion destroys interruption evidence; a second checkout depends on private-store handoff; cleanup failure leaves hidden authority.
- Revisit when: never for R-0001; it requires a replacement Product requirement that permits private orchestration state.

### C. Independent pickup executor and merge engine

Implement worktree creation, command execution, checkpoint receipts, and Git integration entirely inside `pickup.py`.

- Gains: the new path could be locally self-contained.
- Costs: duplicates four trust boundaries, creates a second checkpoint and merge implementation, invites receipt drift, weakens severed-edge confidence, and violates explicit out-of-scope constraints.
- Revisit when: incumbent BUILD-C and repository contracts are formally retired under a separate approved migration.

### D. Status quo

Leave signed shelf contracts executable only through private loop state.

- Gains: no source or release change.
- Costs: satisfies none of AC1–AC4 and leaves no production path for the intended R-0013 design-intake pickup.
- Revisit when: Product cancels the pickup front door.

## Module ownership and APIs

### `taskplane/tp.py` — public adapter

- Add exactly one command shape: `tp pickup <design-contract>` plus the existing common workspace option.
- Resolve the positional path relative to the selected checkout; absolute paths, `..`, symlinks, non-regular files, and paths outside the repository fail before authority parsing.
- Delegate once to `pickup.run(...)`. The CLI does not read loop state or perform assignment, checkpoint, merge, or receipt validation itself.
- Render deterministic named-boundary failures and nonzero exit through the incumbent public error boundary.

### `taskplane/pickup.py` — new bounded coordinator

Own these pure or explicit-input operations:

1. `load_authority(checkout, design_path)` asks `design_contract.py` to validate the existing signed approved-contract and engine-receipt contract. The returned immutable authority contains the authorized source SHA, Design evidence fingerprint, approval identity/digest, engine receipt digest, declared element ids, scopes, and acceptance criteria. Caller-authored substitutes and unknown authority fields fail.
2. `verify_checkout(checkout, authority)` proves regular-file/no-symlink authority bytes, a clean tracked and untracked product tree, and either (a) initial `HEAD == authority.source_sha`, or (b) an exact valid pickup receipt lineage rooted at that SHA/fingerprint. Resume accepts no unrelated history or dirty evidence.
3. `micro_plan(authority, element_id, receipts)` produces exactly one pending element and exactly its ordered acceptance criteria. Selection is deterministic: the contract's sole element is implicit; multiple elements require the contract's existing selected-element field and otherwise refuse. No requirement, Plan, backlog, or neighboring Design content is read.
4. `next_checkpoint(...)` advances at most one acceptance criterion. Its predecessor is the prior immutable pickup receipt. It constructs the existing checkpoint specification from the selected element's declared scope and tracked focused proof, then calls the BUILD-C façade.
5. `write_receipt(...)` writes only after a green exact-revision checkpoint and accepted repository merge. Receipt discovery, validation, and collision handling are append-only and deterministic.

The module imports no loop, run store, track store, claim, lease, wave, ReviewKernel, or hook module. It does not call storage mutation APIs. A test-only trace sink records function-boundary events without becoming authority.

### `taskplane/design_contract.py` — existing authority owner

Expose a narrow `load_approved_contract_for_pickup(checkout, path)` verifier over the existing `contract:design.approved-contract`. It must:

- recompute the Design evidence fingerprint from repository bytes;
- validate the existing signed approval and engine receipt using incumbent verification, never a caller-provided boolean or bare digest;
- require the authorized source SHA, approval, engine receipt, and Design fingerprint to agree;
- return a closed normalized mapping for pickup; and
- perform no approval write, approval migration, or hook/security action.

If the incumbent approved-contract cannot supply this proof from repository facts, the implementation stops for a human scope decision; it must not invent a second signer or approval schema.

### `taskplane/build_c.py` — existing BUILD-C owner

Expose an explicit-input pickup façade that reuses, rather than forks, current validation:

- `assign_direct_scopes(...)` receives the one-element micro-plan and exact source revision, selects exactly one scope, and delegates worktree creation to the repository owner. Pickup mode disables private registration persistence and returns an in-memory assignment receipt bound to source SHA, Design fingerprint, element id, scope, and worktree identity.
- `run_acceptance_checkpoint(...)` uses the incumbent governed command lifecycle and `checkpoint.validate_checkpoint_spec`/`validate_and_mint`. The pickup identity is deterministic from source SHA + Design fingerprint + element id, not a created run record. Exactly one AC id is permitted per invocation.
- `integrate_pickup_on_green(...)` calls the same closed checkpoint validation used by `integrate_on_green`, requires the assignment's exact revision/scope and predecessor digest, and then calls the repository merge owner. It accepts no caller-authored green status.

Existing `assign_scopes` and `integrate_on_green` remain behaviorally and signature compatible. Shared pure validation may be extracted, but the legacy loop caller and its state lifecycle are not changed.

### `taskplane/checkpoint.py` — existing checkpoint owner

Remain the sole checkpoint spec and engine-green receipt authority. Pickup supplies one AC, exact HEAD, scope, proof argv, and predecessor receipt. The existing engine derives producer, output, result, revision, environment, and receipt digest. Any required stateless engine-receipt adapter must verify the existing signed engine receipt and produce the same active-contract fingerprint semantics without persisting a run or weakening the current path. No second checkpoint schema or producer is introduced.

### `taskplane/repository.py` — existing worktree and merge owner

Add or expose explicit-input operations for a short-lived pickup worktree and exact-revision merge, while preserving the existing `RepositoryManager` as sole Git mutation owner. The merge operation receives BUILD-C authorization, verifies primary/worker identity and exact branch tip, performs ordinary merge-on-green, and returns the incumbent merge receipt shape. Pickup never shells out to `git merge` itself. Worktree cleanup remains receipt-scoped and recoverable.

### Negative witnesses and release surfaces

- `taskplane/storage.py` and `taskplane/taskplane_lite.py` remain unchanged; AC1/AC4 instrument their mutation entry points and private home to prove pickup created no orchestration state.
- `taskplane/tests/test_pickup.py` owns the signed shelf fixture, trace, cold-start, severed-edge, second-checkout, collision/tamper, interruption, and release-metadata-focused coverage.
- `README.md`, `CHANGELOG.md`, `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, and `.claude-plugin/marketplace.json` change only after the functional path is green, consistently naming 2.17.20 and the bounded authority boundary.
- `hooks/**`, `.taskplane/codex-hook.py`, `taskplane/loop.py`, `plan/**`, `backlog/**`, `requirements/**`, `components.yaml`, `.github/**`, and deploy surfaces are byte-unchanged.

## Runtime sequence and contracts

### Initial start

1. CLI resolves the repository-local regular Design Contract path.
2. The approved-contract owner verifies signed approval, engine receipt, source SHA, and recomputed Design fingerprint.
3. Pickup proves the checkout clean and exactly at the authorized source SHA; it audits that no pickup lineage already conflicts.
4. Pickup derives one in-memory element and its ordered AC list, without reading any private Taskplane state.
5. BUILD-C assigns the one declared scope through the repository worktree owner.
6. The host executes the bounded element work. Production behavior and the focused proof for the current AC are committed together.
7. BUILD-C runs exactly that AC through the existing checkpoint engine. A red or interrupted checkpoint is durable negative evidence and cannot reach integration.
8. A green checkpoint for the exact assigned revision enters the existing merge-on-green owner.
9. Pickup writes one immutable export receipt after the merge outcome. The next invocation repeats from repository facts for the next AC.

### Resume

Receipt files live at:

`exports/pickup/<authorized-source-sha>/<design-evidence-fingerprint>/<element-id>/<ordinal>-<ac-id>-<receipt-digest>.json`

Each `taskplane.pickup-receipt/v1` contains a closed field set:

- authorized source SHA, Design evidence fingerprint, signed approval digest, engine receipt digest, element id, and micro-plan fingerprint;
- criterion ordinal/id and predecessor pickup-receipt digest (null only for the first criterion);
- exact assigned worktree revision and declared scope;
- the complete validated checkpoint receipt plus its canonical digest;
- the complete incumbent merge receipt, merge outcome, and repository tree fingerprint after integration;
- producer id `taskplane.pickup/v1`, terminal status, and canonical receipt digest.

The file name digest must equal the canonical digest of its bytes excluding its own digest field. Existing paths are never overwritten. Duplicate ordinals, forks, digest/path mismatch, a gap, a receipt for another SHA/fingerprint/element, an incompatible terminal outcome, or history not explained by the validated merge lineage fails by naming `receipt-lineage` before execution.

The receipt key remains the original authorized source SHA plus the unchanged Design fingerprint. Resume verifies the new checkout's Git tree and ancestry against the latest accepted merge receipt; it never treats an evidence commit's self-referential Git SHA as receipt content. Thus the chain is content-addressed without an impossible receipt-commit hash cycle. A second checkout may use a completely empty private Taskplane home because every required authority and predecessor byte is tracked in Git.

### Failure boundaries

- `checkout-clean`: dirty tracked bytes, untracked product files, symlink authority, or authority outside the repository.
- `source-sha`: initial HEAD mismatch or resumed history/tree not explained by the receipt lineage.
- `approved-design`: stale Design bytes, missing/invalid approval, or approval/Design fingerprint mismatch.
- `engine-receipt`: missing, forged, unknown-field, wrong-SHA, wrong-fingerprint, or wrong-producer engine evidence.
- `micro-plan`: zero/multiple ambiguous elements, scope escape, unrelated content, unknown AC, or more than one current AC.
- `pickup-build-c`: missing or severed direct-assignment/checkpoint entry.
- `acceptance-checkpoint`: red, canceled, errored, stale, truncated, mixed, or caller-authored checkpoint evidence.
- `green-integration`: revision/scope/predecessor mismatch or rejected repository merge.
- `receipt-lineage`: collision, overwrite attempt, fork, gap, digest mismatch, foreign identity, or incompatible consumed outcome.

Every refusal is synchronous, names one boundary, exits nonzero, and authorizes neither the next AC nor merge.

## Acceptance-to-validation map

| Criterion | Design element | Executable validation |
|---|---|---|
| AC1 | `contract:pickup.stateless-front-door` plus the BUILD-C/checkpoint/merge chain | `test_pickup.py::test_signed_shelf_pickup_reaches_checkpoint_and_green_merge_without_orchestration_state` runs a signed fixture through the real CLI seam, asserts the ordered trace, and snapshots an empty private home plus instrumented storage mutation calls. |
| AC2 | `pickup.verify_checkout` → `pickup.micro_plan` → `build_c.assign_direct_scopes` | `test_pickup.py::test_fresh_checkout_reaches_first_executing_checkpoint_under_120_seconds` uses a fresh Git checkout, empty home, no warm process, monotonic start/checkpoint timestamps, and requires `< 120.0` seconds. |
| AC3 | the proposed runtime edge `taskplane/pickup.py → taskplane/build_c.py` | `test_pickup.py::test_severed_pickup_to_build_c_edge_fails` removes/replaces the call and requires the end-to-end fixture to fail; `python -m pytest taskplane/tests/test_loop.py taskplane/tests/test_r0007_governed_commands.py -q` proves the unchanged legacy path. |
| AC4 | `resource:exports.pickup-receipts` lineage | `test_pickup.py::test_second_checkout_resumes_from_git_receipts_without_private_home` completes one AC, commits its export, destroys access to the first home, and resumes in a second checkout from tracked Design/receipt bytes only. |
| AC5 | final exact-SHA regression evidence | Run `python -m pytest taskplane/tests -q` on the final clean commit, record the command/SHA/counts, and compare with the exact baseline so any new failure blocks review. |

The delivery itself uses five ordered manual checkpoints, AC1 through AC5. Each functional change and the focused test proving that AC are in the same implementation commit. A checkpoint receipt may follow as an evidence commit because it can only exist after execution and merge; it never substitutes for the same-commit behavior/test rule.

## Quality targets and observability

- Initial pickup with a fresh checkout and empty private home reaches the first executing checkpoint in strictly less than 120.0 wall-clock seconds, measured with a monotonic clock at CLI entry and checkpoint-start trace emission.
- Pickup creates exactly zero run, track, claim, lease, wave, stage, per-task-lens, or equivalent coordination records and performs zero private-store handoff reads during resume.
- Every successful criterion has exactly one unambiguous receipt-chain successor; every receipt and predecessor digest is 64 lowercase hexadecimal characters and recomputes exactly.
- Integration count is zero unless the exact assigned revision has one engine-green checkpoint for the current single AC and matching predecessor lineage.
- The final full suite has zero failures not present at the exact baseline and no skipped pickup acceptance proof.

Machine-visible signals are `pickup.preflight.refusal`, `pickup.checkpoint.started`, `pickup.checkpoint.terminal`, `pickup.integration.outcome`, `pickup.receipt.lineage`, `pickup.storage.audit`, and `pickup.cold_start.seconds`. They are deterministic trace/export fields, not remote telemetry. There is no always-on service, so no automated alert channel is introduced; nonzero CLI refusal is immediate operator action and the final quick security/QA plus EM reviews inspect the exact-SHA evidence.

## Risks and controls

- **A digest lookalike becomes approval.** Mitigation: the approved-contract owner verifies the incumbent signed approval and engine receipt; pickup accepts neither booleans nor caller-authored producer/result fields. Owner: `taskplane/design_contract.py`.
- **Stateless becomes hidden state under another name.** Mitigation: no loop/private-store imports in pickup, instrumentation of storage mutation APIs, empty-home before/after snapshot, and second-checkout proof. Owner: `taskplane/pickup.py` and QA.
- **A second orchestration or merge path drifts.** Mitigation: BUILD-C/repository façades reuse shared validation and the incumbent owner; severed-edge coverage fails if pickup no longer reaches BUILD-C. Owner: `taskplane/build_c.py` and `taskplane/repository.py`.
- **Receipt commits create SHA self-reference.** Mitigation: lineage is rooted at the authorized source SHA and binds merged tree/receipt digests, never its own future evidence-commit SHA. Owner: `taskplane/pickup.py`.
- **Resume follows a forked or foreign chain.** Mitigation: require one contiguous ordinal/predecessor chain for one source-SHA/fingerprint/element tuple; forks, gaps, and unrelated history fail before assignment. Owner: `taskplane/pickup.py`.
- **Release metadata lands before behavior.** Mitigation: version/docs/manifests are a final checkpoint after AC1–AC5; final review targets one clean SHA. Owner: release task and EM.

No silent technical debt is accepted. The additive pickup receipt schema is intentionally local and Git-resident; there is no migration or compatibility burden on existing loop state. Supporting multi-element or concurrent pickup is explicitly deferred to a new requirement, not designed as an unused extension point here.

## Rollout, review, and rollback

1. After human Design and consolidated Plan authorization, implement the manual AC1–AC5 checkpoint sequence only.
2. Keep hooks and the legacy loop byte-unchanged. If the existing approved-contract, checkpoint, or repository boundary cannot be consumed without redesign, stop for a human scope decision.
3. After functional AC1–AC5 are green, update README, CHANGELOG, and the three manifests consistently to 2.17.20.
4. On one final clean SHA, run security and QA as two concurrent quick passes, followed by engineering-manager review on the same SHA and evidence set.
5. Report the release candidate and stop for explicit push authority. Do not push, tag, publish, or mutate `origin/main`.

Rollback before any shared mutation is branch abandonment or a normal revert of the additive pickup commits. After a shared commit, rollback is a forward revert that removes the CLI/module/façades and restores prior version metadata while retaining already-committed export evidence. There is no data migration, hook change, loop-state migration, force-push, receipt deletion, or tag movement. Existing Taskplane releases ignore the additive `exports/pickup/` records.

## Visualization decision

A sequence visual is required because the distinction among initial exact-SHA authority, one-AC BUILD-C execution, post-merge receipt persistence, and Git-only second-checkout resume is easy to misread as an ephemeral loop. The visual makes the one-way boundaries and the absent private-store handoff explicit.

## Solution-design lens result

The selected adapter is grounded in the cited incumbent owners, not a greenfield replacement. The rejected alternatives are materially different and explain why ephemeral-loop reuse and duplicated execution are unacceptable. Every AC maps to a named module/contract and a test that fails when that element is severed. Quality targets are numeric, failure detections name emitted signals, rollback is additive and migration-free, and the work can be decomposed without deciding new Product scope. The solution-design attestation is self-attested by this designer and must be surfaced at the human gate.

## Open questions

None. Any need to change hook security, the legacy loop, introduce a new approval/signature scheme, redesign an incumbent BUILD-C/checkpoint/merge contract, widen beyond one selected element, or perform a release mutation is a new human scope decision.
