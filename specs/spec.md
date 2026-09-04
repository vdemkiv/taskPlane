# Stateless phase pickup from sealed repository handoffs

## Product authority

This specification is the Product authority for end-to-end stateless phase
pickup. It defines the missing product behavior that lets a fresh Taskplane
task continue governed work from repository-resident evidence alone. The
historical specification retained at the end of this file is not part of this
requirement and must not be treated as current scope.

## Problem

Taskplane can carry bounded handoffs inside an active run and can pick up one
approved shelf Design element for Build, but normal phase completion does not
yet yield a public, sealed handoff that a wholly fresh environment can consume
across Design, Plan, and Build. As a result, continuation may depend on
workspace locators, run records, leases, private homes, predecessor sessions,
or other runtime context that is unavailable after a true handoff.

Taskplane needs a repository-resident continuation contract whose authority,
lineage, scope, and evidence are complete and independently verifiable. It
must fail closed when those claims cannot be proved and must preserve the
existing pickup v1/v2 behavior.

## Users and context

- Operators need to export a phase result through the supported Taskplane
  surface and hand it to another task, session, checkout, or machine without
  reconstructing private control-plane state.
- Product, Design, and Plan owners need the accepted requirement, decisions,
  dependencies, contracts, scope, and acceptance obligations to arrive intact
  at the next phase.
- Build workers need code-authoring authority limited to approved task scope,
  followed by the existing BUILD-C validation and integration boundary.
- Reviewers and approvers need receipts that distinguish attributable human
  authority at actual human gates from mechanical phase progress.
- Maintainers need deterministic, bounded artifacts, named refusal reasons,
  backward compatibility, and targeted tests that prove the public seams.

## Product decisions

1. The durable source of continuation is a sealed, repository-resident phase
   handoff and its referenced repository artifacts, not a pointer to hidden
   Taskplane state.
2. Export is part of normal successful phase completion, not a test-only or
   internal lifecycle operation. Interrupted work has a separately identified
   same-phase resume form.
3. Import and resume are available through the public Taskplane surface.
4. A pickup is authorized only by the evidence appropriate to its boundary.
   Human attribution is required only where the governing flow has a true
   human gate; mechanical steps must not invent or impersonate approval.
5. Existing pickup v1/v2 inputs and receipts remain supported. The successor
   contract extends the product rather than silently reinterpreting them.
6. Fresh continuation is fail-closed. Missing, unverifiable, contradictory, or
   scope-widening evidence never degrades to a best-effort run.

## In scope

1. A public producer/export action available from normal successful Design,
   Plan, and Build completion, plus an export suitable for interrupted
   same-phase resume.
2. A public pickup action that consumes one sealed repository-resident handoff
   in a fresh task, session, checkout, or empty private home.
3. Four continuation journeys:
   - Design from an approved requirement/spec handoff.
   - Plan from an approved Design handoff.
   - Build from an approved Plan handoff.
   - Resume of an interrupted Design, Plan, or Build phase without reopening a
     terminal predecessor.
4. Complete phase identity and lineage: exact source commit SHA and repository
   tree identity; requirement identity and fingerprint; applicable Design and
   Plan fingerprints; phase kind and outcome; predecessor and receipt lineage;
   task scopes and ordering; dependencies and named contracts; acceptance
   obligations and proof identities; and attributable authority records.
5. Portable, bounded artifact references whose contents can be independently
   verified from the repository and whose canonical representation has a
   deterministic fingerprint.
6. Build pickup that grants code-authoring only for exact approved scopes and
   carries the work through BUILD-C validation and repository integration.
7. Named pre-execution refusals for malformed, tampered, stale, foreign,
   ambiguous, dirty, incomplete, replay-conflicting, or scope-widened inputs.
8. Compatibility behavior for valid existing pickup v1/v2 contracts, receipts,
   trust-source handling, repository-only resume, and cold-start behavior.
9. Targeted positive and deliberately severed tests, including fresh-clone and
   empty-private-home journeys, with evidence that no forbidden hidden state
   was read or created.
10. Public documentation of producer, consumer, supported journey, authority,
    compatibility, refusal, recovery, and artifact-retention behavior.

## Out of scope

- Replacing the normal stateful Taskplane loop or making every active run
  stateless.
- Implementing a remote artifact service, network transport, registry, queue,
  or cloud synchronization layer.
- Importing predecessor conversations, agents, prompts, event logs, tool
  transcripts, leases, worktrees, process state, private-home state, secrets,
  or undeclared artifacts.
- Changing which lifecycle boundaries are human gates, auto-approving a gate,
  or creating synthetic human identities for mechanical progress.
- Replacing BUILD-C, checkpoint validation, repository ownership, or merge
  policy; the pickup must enter those existing governed boundaries.
- Broadening a task's approved file scope, dependencies, contracts, order, or
  acceptance obligations during pickup.
- Treating a dirty checkout, a different repository, a stale source commit, or
  a malformed legacy artifact as recoverable authority.
- Requiring network access or a populated Taskplane private home for export,
  import, validation, or resume.
- Defining the final schema version, file layout, module decomposition,
  cryptographic mechanism, or migration implementation; Design owns those
  choices after comparing alternatives.
- Removing or weakening pickup v1/v2 behavior during the compatibility window.
- A broad whole-suite pass as the sole acceptance proof; this requirement uses
  bounded seam-focused tests, with broader regression runs left to normal
  release policy.

## Functional requirements

1. Taskplane shall produce a sealed repository-resident continuation handoff
   through its public surface when Design, Plan, or Build completes normally.
2. Taskplane shall produce a separately identifiable resumable handoff when an
   in-scope phase is interrupted before terminal success.
3. Taskplane shall start Design from an approved requirement/spec handoff,
   start Plan from an approved Design handoff, and start Build from an approved
   Plan handoff without consulting predecessor runtime state.
4. Taskplane shall resume interrupted work only in the same phase and shall
   preserve completed work, remaining obligations, and receipt lineage without
   reopening a terminal predecessor.
5. Every successor handoff shall carry or content-address all information
   needed to prove exact source identity, requirement identity, applicable
   Design and Plan identity, phase identity/outcome, task scope/order,
   dependencies, named contracts, acceptance proofs, authority, and lineage.
6. Taskplane shall validate the handoff and every selected artifact before
   dispatch, code authoring, checkpoint execution, or integration.
7. Build pickup shall grant only the approved code-bearing task scopes and
   shall use the existing BUILD-C validation and repository-integration
   contract before reporting completion.
8. Export and import shall be deterministic for identical semantic input;
   exact replays shall be idempotent, while a conflicting artifact or receipt
   at the same identity shall be refused.
9. Valid pickup v1/v2 inputs and receipt chains shall retain their documented
   behavior, including their existing source-trust distinctions.
10. Public results shall report the consumed handoff fingerprint, phase and
    source identity, lineage outcome, and a stable named status/refusal without
    leaking private paths, secrets, or predecessor runtime context.

## Non-functional requirements

- `security`: Authenticate or explicitly attribute every authority boundary,
  verify source/repository/artifact integrity before any execution, reject
  confused-deputy and scope-escalation attempts, and never serialize secrets or
  synthetic approval into a portable handoff.
- `architecture`: Provide one composable phase-handoff contract shared by
  Design, Plan, Build, and same-phase resume; preserve separation between
  portable repository truth, lifecycle authority, and BUILD-C execution; do
  not create a second competing pickup mechanism.
- `integrability`: Preserve documented pickup v1/v2 inputs, receipts, refusal
  ordering, and BUILD-C integration while exposing a clear compatibility path
  for successor handoffs.
- `data-safety`: Use bounded, canonical, content-addressed artifacts and atomic
  publication semantics so partial writes, collisions, tampering, and replay
  conflicts cannot be accepted as current continuation authority.
- `privacy-compliance`: Operate with an empty private home and exclude prior
  conversations, prompts, logs, tool transcripts, host paths, secrets, and
  unrelated artifacts from exported content and public results.
- `sre`: Produce stable machine-readable success/refusal outcomes and enough
  lineage observability to diagnose a failed boundary without hidden state;
  repeated valid invocations must be deterministic and recoverable.

## Acceptance criteria

1. **AC1 — normal completion exports.** Completing each of Design, Plan, and
   Build through the supported public flow produces one bounded, sealed,
   repository-resident handoff whose fingerprint verifies after a fresh clone.
   **Verify with:** parameterized public-surface integration tests for all three
   producer phases, including canonical byte/fingerprint and repository-only
   artifact checks.
2. **AC2 — fresh Design continuation.** Given only a clean fresh checkout and
   an approved requirement/spec handoff, a new task with an empty private home
   starts Design with the exact requirement identity, scope, contracts, and
   acceptance obligations, without a locator or predecessor state.
   **Verify with:** an isolated subprocess journey that forbids loop/run/track,
   lease, workspace-locator, conversation, and predecessor-runtime reads.
3. **AC3 — fresh Plan continuation.** Given only a clean fresh checkout and an
   approved Design handoff, a new task with an empty private home starts Plan
   with matching source, requirement, Design fingerprint, contracts, and
   acceptance map; stale or absent Design approval is refused before dispatch.
   **Verify with:** positive isolated continuation plus approval-removed and
   approval-stale severed variants.
4. **AC4 — fresh Build continuation.** Given only a clean fresh checkout and an
   approved Plan handoff, a new task with an empty private home starts the
   approved Build work in declared dependency order and exposes only exact task
   scopes, contracts, and proof commands.
   **Verify with:** a multi-task Plan fixture that asserts ordered assignments
   and a scope-widening variant that observes zero authoring/checkpoint calls.
5. **AC5 — interrupted same-phase resume.** For Design, Plan, and Build, an
   interrupted handoff resumes the same phase from its last durable receipt,
   preserves completed obligations, schedules only remaining work, and never
   reopens a terminal predecessor.
   **Verify with:** crash-after-first-durable-receipt tests in a second checkout
   and empty private home for each phase, including exact predecessor digest
   chains and no duplicate completed work.
6. **AC6 — complete closed lineage.** Every accepted successor artifact proves
   exact source commit and tree identity, repository identity, requirement
   fingerprint, applicable Design and Plan fingerprints, producer phase and
   outcome, ordered task scopes/dependencies/contracts/acceptance proofs,
   authority record, selected artifact digests, predecessor receipts, and its
   own canonical fingerprint; unknown or missing fields are rejected.
   **Verify with:** schema closure tests plus one-field-at-a-time omission,
   mutation, unknown-field, reorder, duplicate, and size-bound fixtures.
7. **AC7 — authority remains truthful.** Human identity and approval are
   required and attributable at real requirement/Design/Plan gates; mechanical
   progress carries mechanical receipts and cannot manufacture a human actor.
   **Verify with:** positive gate-lineage fixtures and severed tests for missing,
   foreign, stale, mismatched, reused, or synthetic human authority.
8. **AC8 — fail closed before effects.** Malformed, tampered, stale, foreign,
   ambiguous, dirty, incomplete, replay-conflicting, and scope-widened inputs
   each return a stable named refusal before phase dispatch, code authoring,
   checkpoint execution, receipt publication, or integration.
   **Verify with:** parameterized negative tests that install effect counters
   at every downstream boundary and assert all counters remain zero.
9. **AC9 — BUILD-C is not bypassed.** A valid Build pickup obtains an exact
   scoped authoring assignment, reaches the existing BUILD-C checkpoint and
   repository integration boundaries, reports their verified receipts, and
   cannot report completion when the pickup-to-BUILD-C edge is deliberately
   severed or when focused proof fails.
   **Verify with:** positive public CLI integration and severed-edge/failing-
   proof tests using real BUILD-C entry points.
10. **AC10 — v1/v2 compatibility.** The existing valid pickup v1/v2 shelf,
    trust-source, repository-only resume, collision, interrupted-write, and
    cold-start cases retain their documented results and refusal ordering.
    **Verify with:** the existing pickup and cold-start suites run unchanged,
    plus compatibility fixtures exercised alongside the successor contract.
11. **AC11 — no hidden-state dependency or leakage.** Each positive journey
    succeeds in a fresh clone with an empty unrelated private home and no
    loop.json, track, claim, lease, run store, workspace locator, prior
    conversation, or predecessor runtime context; exported artifacts and
    public output contain none of those values or any secret/absolute host path.
    **Verify with:** isolated environment audits, forbidden-access sentinels,
    recursive serialized-content scans, and an exact clean-checkout assertion.
12. **AC12 — deterministic public contract and recovery.** Identical semantic
    exports have identical fingerprints and exact replay is idempotent;
    conflicting bytes at the same identity fail closed; every refusal names the
    boundary and a safe recovery that does not weaken authority or widen scope.
    **Verify with:** repeat/export/import tests across two clean clones,
    collision and partial-publication fault injection, and public CLI output
    assertions.

## Contract handoff

```yaml
requirement_dependencies: []
contracts:
  provides:
    - contract:stateless-phase-pickup
  consumes:
    - contract:build-c-admission
    - contract:human-gate-authority
  changes:
    - contract:stage-artifact-handoff
    - contract:pickup-receipt-lineage
scope_paths:
  - taskplane/pickup.py
  - taskplane/design_contract.py
  - taskplane/stage_handoff.py
  - taskplane/stage_entities.py
  - taskplane/run_store.py
  - taskplane/loop.py
  - taskplane/build_c.py
  - taskplane/checkpoint.py
  - taskplane/repository.py
  - taskplane/review_evidence.py
  - taskplane/tp.py
  - taskplane/tests/test_*pickup*.py
  - taskplane/tests/test_pickup.py
  - taskplane/tests/test_r0001_pickup_cold_start.py
  - taskplane/tests/test_stage_handoff.py
  - taskplane/tests/test_stage_handoff_security.py
  - taskplane/tests/test_stage_non_build_handoffs.py
  - taskplane/tests/test_stage_loop_integration.py
  - taskplane/tests/test_build_quality.py
  - docs/cli-reference.md
  - docs/loop-design.md
  - specs/spec.md
out_of_scope:
  - remote artifact transport or registry
  - replacement of the normal stateful loop
  - changes to human-gate policy or synthetic approval
  - replacement of BUILD-C, checkpoint, repository, or merge policy
  - importing private runtime state, conversations, logs, leases, or secrets
  - scope widening during pickup
  - removal or weakening of pickup v1/v2 compatibility
dod:
  test_command: >-
    python3 -m pytest -q
    taskplane/tests/test_stateless_phase_pickup.py
    taskplane/tests/test_pickup.py
    taskplane/tests/test_r0001_pickup_cold_start.py
    taskplane/tests/test_stage_handoff.py
    taskplane/tests/test_stage_handoff_security.py
    taskplane/tests/test_stage_non_build_handoffs.py
    taskplane/tests/test_stage_loop_integration.py
    taskplane/tests/test_build_quality.py
```

## Design handoff

Design is required because this requirement changes a public continuation
contract across lifecycle, artifact, authority, CLI, and execution boundaries.
Design must inspect the current source tree; compare at least two viable
producer/import models; define the exact schema and compatibility policy;
identify module ownership and every cross-module edge; preserve the incumbent
stage-handoff, pickup receipt, artifact-reference, checkpoint, repository, and
BUILD-C contracts; map every acceptance criterion to deterministic fixtures
and probes; and specify rollout, rollback, observability, partial-publication
recovery, and v1/v2 migration. The approved Design must make clear which
authority is portable, which remains local, and why no hidden predecessor state
is needed.

## Open questions

None block Design. Schema version, canonical repository path, command spelling,
and internal module boundaries are Design decisions, provided the acceptance
criteria and compatibility contract above remain unchanged.
