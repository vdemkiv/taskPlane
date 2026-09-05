# Phase boundary repair: Design, Plan and Build

Date: 2026-09-05. Status: source repair under validation in PR #17; not an installed-release or native-host sign-off.

## Root cause

Producers, role instructions and consumers described different contracts. Tests exercised internal validators with manually assembled results, so successful unit checks did not prove that a fresh worker could act on the public handoff. The same class of mismatch extends past Design.

| Boundary | Reproduced defect | Repair in this change |
| --- | --- | --- |
| New run / worker attempt | Host task names were reused across runs; Design lens names did not include the owning attempt. | Deterministic run namespaces and an owner-attempt namespace for Design lenses. Legacy unnamespaced name calculation remains compatible. |
| Design → native lens | Child briefs omitted task-specific input, their own startup and result protocol. The generic lens role expected leased v2 output; Design collected native v1 output. | Seal the existing stage evidence in the team plan and project it into every brief with the native child contract, exact result identity, schema, fingerprint recipe and supported file transport. Producer and collector share one result validator. No second lease system or default verdict. |
| Fresh Design input | An unapproved contract from another requirement appeared as current Design input. | Exclude foreign-requirement contracts; do not promote them to current authority. |
| Design → Plan | Role instructions omitted top-level metadata required by the Plan consumer. The planner was told to discover dependency impact using a shell its read-only contract disallows. | Emit a Plan output template and bounded dependency snapshot, preserving approved Design depth policy. The template does not grant Build approval. |
| Plan → parallel Build | Wave dispatch omitted assigned criteria, contract and test-strategy details; claim did not restore them. Build-quality admission was required but absent from completion instructions. | Carry the approved task fields through wave and claim and expose the existing completion/admission commands. Evidence must still be genuinely produced and admitted. |
| Build → Build pickup | One execution of a task with multiple acceptance obligations recorded only the first obligation, then excluded the entire task from further work. | Emit one chained receipt per completed obligation and preserve the complete chain in export. Partial acceptance overlap refuses before execution. Historical partial chains require explicit remaining-work recovery, never fabricated receipts or automatic reruns. |

## Regression boundary

- A fresh isolated process receives only the emitted native Design brief, checks that requirement and acceptance input arrived, and produces a result accepted by the real collector contract. Missing required fields and the wrong output version fail. This is an input/output serialization contract test, not substantive Design judgment or a native LLM/host-receipt test.
- Real Design/Plan actions expose the output metadata and graph snapshot; the metadata round-trips through the actual delivery-mode consumer without granting approval.
- Wave and claim retain task-specific criteria; the task projection preserves acceptance, contracts and test-strategy fields without copying mutable predecessor runtime state.
- Committed Build execution and repository export cover one, two and three obligations, including same-phase resume selecting the remaining dependent task. Negative cases preserve the supplied historical evidence unchanged.

## Separate adapter and retry paths

The public `phase pickup` / `phase resume` path is distinct from native `loop next`. This repair must not be presented as making those paths interchangeable.

1. Stateless non-Build startup emits a `phase-worker-result/v1` descriptor and a default `plan/result.json` write target. The native planner instead produces `plan/tasks.json` and `plan/plan.md`. No connected consumer for those stateless worker results was found in the audit.
2. The public pickup receipt deliberately omits private bootstrap/lease data and does not supply a complete native role/continuation adapter. Stateless Build also uses `proofs`/`acceptance` and `phase submit`, unlike the native executor's `tests`/`criteria` and `loop submit` protocol.
3. Native parallel Build retry had a separate attempt-identity mismatch: `wave()` named from the task ID, while `claim()` reserved an attempt-suffixed identity. Commit `61d2d2b` now makes wave, intent and claim share one reserved attempt. Pending claims replay; active/submitted workers cannot be replaced; retry requires the existing authenticated terminal/release proof and fresh wave/root admission. The 13 focused regression cases passed.

The repository-phase adapter now connects the first two paths through actual role contracts, current-attempt native intents, observed owner outputs, focused reviews, attributed approval and repository export. Its current-attempt caches are disposable enforcement, not successor authority. The facade and orchestrator instructions explicitly select this route instead of falling back to `loop next`.

## Continuation findings and verification boundary

- The first adapter round-trip exposed an evidence bypass: a three-field Design header plus `status: done` could advance. The existing substantive Design DoD body is now shared through `design_artifact_errors`, with explicit sealed requirement/graph inputs. Its nine new parity/refusal tests and four existing gate selectors passed. The new collector calls it; it is not a replacement for focused-route/host evidence.
- The native adapter initially omitted dispatch registration and instructed Build to write its submission request outside the permitted task scope. It now registers the expected worker and materializes that exact request before dispatch. Root admission uses the existing seed, usage observation, authenticated host start and dispatch verifier without reading a predecessor loop. A fresh-process test additionally exposed a package/flat import identity mismatch in typed operational settings; the canonical settings import fixes it. Missing genuine host evidence still returns waiting, never an executable Build dispatch.
- Cached startup, input content, coding scope, allowed tools and native intent are revalidated. Collection requires a signed terminal/released current-owner contract and exact output bytes observed at native stop. The same mechanism covers focused-review children. These checks attest identity and observed bytes, not model authorship or substantive correctness by themselves.
- The native Plan artifact is retained and passes the shared Plan readiness and Design-conformance checks over explicit sealed requirement and graph snapshots before projection. Task-local criteria, test contracts and strategy authority survive into Build. Ambiguous acceptance/proof ownership refuses; one task cannot terminalize an obligation owned by another task.
- Native quality admission is connected before BUILD-C. The new `phase quality` command creates only an empty candidate-bound receipt, preserves a valid current receipt and retains exact older bytes before beginning new checks. Successful BUILD-C carries admitted quality evidence into repository export. No validator fixture is claimed as real test-layer execution.
- Focused Design/Plan reviews use the existing routing and result validator. All 26 dispositions are retained; only selected children run; Build remains zero-lens. New phase workers may omit only a mechanically derived content hash when their read-only tool set cannot compute it. The engine derives that field only after authenticating observed bytes, without changing output files, inventing judgments or repairing bad supplied hashes. Legacy result schemas remain strict.
- A first interrupted Plan can now retain a draft with no executable tasks and resume Plan. Completed Plan and all Build handoffs still require executable tasks. Refusals report potentially partial effects as unknown rather than falsely claiming complete rollback.
- The composed Design → focused review → fresh Plan → focused review → fresh Build subprocess fixture passes, with actual BUILD-C execution and clean successor clones. Native lifecycle events and model judgments are explicitly synthetic. It proves command and artifact wiring, not live host/model end-to-end sign-off. Done/interrupted phase branches and exact negative byte/authority/projection tests cover the adjacent boundaries.
- PR #17's initial full CI run had 4 failed tests, 4,056 passed tests and 597 passed subtests. Failures were missing run IDs in synthetic fixtures and omitted subprocess encoding. All eight CI checks subsequently passed on both `61d2d2b` and `fe7bcdf`; the latter full test job took 13m48s. This adapter's final-candidate CI is still pending. No version bump or release has been made.

## Release and live-run limits

No installed plugin cache, historical receipt, approval, old phase artifact or live worker contract is rewritten by this source repair. Live verification needs a matching engine and role digest through supported source-development onboarding or plugin installation. It must prove effective native hooks, bounded evidence delivery, permitted artifact writes and genuine fresh-root admission. Engine-derived hashes remove the need to grant a shell to read-only authors. R-0004 remains the existing fresh Design run, with older Design material used only as reference. This document is an RCA and verification boundary, not an Engineering sign-off or retrospective claiming completed delivery.

## Outer-boundary audit: remaining work

The middle chain passing is not proof of a complete delivery workflow. The
read-only audit of `5948bee` found two missing connections:

- At that candidate, no public producer emitted the schema-supported Requirement/done → Design
  handoff. The test constructed that initial artifact explicitly;
  `loop.project_phase_export` accepted only Design, Plan and Build.
- Build/done exports `terminal/terminal-evidence`, but no consumer carries it
  into stateless Evaluate, Engineering, sign-off or Retro. Generic
  `StageLifecycle` already supplies transactional isolated roots and
  transitions; the semantic Evaluate/EM/Retro checks still read loop state.
  They need explicit sealed-input adapters, not a fabricated loop positioned
  at Evaluate and not a second state machine.

Build's exported checkpoint/integration fingerprints are not complete portable
execution evidence. Checkpoint creation currently discards proof-output bodies
after retaining their digest/count. The native terminal receipt is authenticated
with a workspace-local HMAC and cannot be cryptographically revalidated by a
fresh checkout just by copying the JSON. Never export its secret. A successor
must use an explicit verified-evidence intake or execute fresh independent
proofs through the existing evaluator. These are unresolved release-claim
limits; no finalization, Retro or Engineering PASS is inferred.

### Initial entry and dependency-direction repair

The existing public export command now accepts Requirement/done and emits the
initial Design handoff. It requires clean committed selected Product inputs,
the existing Product readiness check, exact requirement/graph/dependency
artifacts and attributable initial human authorization. It creates no
predecessor tasks, phase receipts or approval. All 31 entry tests passed,
including separate-process pickup and refusal of private predecessor reads.

CI on `5107884` caught real dependency cycles added by the middle-phase adapter.
The repair extracts the catalog loader and output observation below their
callers, moves protocol dispatch above admission, and gives Plan/Build/pickup
one shared input/error layer. Compatibility facades preserve existing entry
points. No import-cycle baseline, scanner, evidence rule or approval gate was
relaxed. The exact ratchet returns `pass` with no violations; the prior SCCs
remain 17 members/49 edges and 7 members/13 edges, with no new phase SCC.
Targeted observer/catalog checks passed 39 tests; Build input/quality checks
passed 37; native dispatch/review checks passed 12. These batches overlap
earlier checks and are not summed into a unique-suite or native-run claim.

Full strict typing passes for all 120 shipped source files. A local package
test on this dirty workspace refuses the pre-existing modified hook config
(`installed hook lacks the bounded plugin fallback`); that user-owned file is
not part of this repair and remains unchanged. Release/package verification
must use the clean PR candidate. Terminal Build → Evaluate/EM/Retro and live
hook trust remain unresolved; this repair does not claim final delivery.

The `5107884` full test job finished with 4,296 passed tests, six failures and
596 passed subtests in 15m17s. The failures comprise two legacy Markdown-only
Plan pickup fixtures, an empty-home Build test still expecting immediate native
readiness, missing UTF-8 declarations, a static native-authority name collision,
and stale facade scenario source metadata. Scenario
metadata now identifies the existing plain-goal approval scenario explicitly;
its only extracted source-flow addition is the prohibition on routing sealed
handoffs through `loop next`. No historical run fingerprint, score or waiver
is refreshed, and no new live evaluation is claimed.

The three failing pickup selectors now pass using producer/consumer clone
executables and full native Design inputs for positive Plan coverage. Empty-home
Build explicitly requires `waiting` with dispatch disallowed. The authority
checker matched any function named `admit`; this adapter actually screens an
already declared dispatch through the existing authenticated root meter. Its
real API is now `screen_root_dispatch`. The checker/forbidden set is unchanged.
The exact authority test and four meter tests pass, including traps for task
scheduling, reservations, new intents and worker lifecycle changes. Screening
preserves contract bytes, task slots and root identity and changes only its
telemetry binding. These tests use synthetic provider evidence.

## Measured host probe

On 2026-09-05, supported source onboarding in an isolated temporary checkout
resolved the engine at candidate `5948bee`. Fresh CLI session
`01a0703b-ce47-7743-b13d-4b9711d035f5` ran version verification and read-only
onboarding successfully. Its native completion reported 52,132 input tokens
(35,584 cached), 655 output tokens, 267 reasoning-output tokens and zero
cache-write input tokens. These are that probe's raw counters, not a total for
this repair and not an admitted phase meter.

The fresh session still had no trusted/effective hook receipts. The subsequent
native hook browser confirmed nine installed hooks, zero active, all nine
requiring review. Trust has not been changed and no bypass flag was used.
Therefore this probe demonstrates launcher resolution, not native phase
execution or end-to-end sign-off.

## Next patch candidate preparation

The routine version-bump authorization is being applied as candidate 2.19.2,
not a release. Both plugin manifests, the Claude marketplace package metadata,
the runtime version and the existing compatibility matrix name 2.19.2.
The verified last-released 2.17.20 baseline and 2.18.0 compatibility generation
are unchanged. The untagged 2.19.1 source is recorded as a superseded candidate;
no tag, installation or historical receipt is changed. On 2026-09-05, the
remote main remained `b0dcda0a3deff70947e93cf1be5af2f90cec3236`, with no
`v2.19.1` or `v2.19.2` tag.

Version consistency, plugin validation and release-history validation pass;
19 focused release tests pass. Package checks require a clean committed
candidate because the unrelated local hook edit still fails the OpenAI
package precondition. Candidate `e903733` has already passed its clean-checkout
20-test package/dependency/authority check and ten scenario subtests. Those
receipts are not transferred to 2.19.2, and do not substitute for the pending
native canary, Engineering, Retro or protected release evidence.

## Approved native probe and prepared-run entry repair

On 2026-09-05 the human explicitly approved the nine hooks in the isolated
test checkout. The native hook browser then showed all nine active. No trust
bypass, installed-cache edit or change to the user's development hook config
was used. The first fresh probe resolved source 2.19.2 but had no hook receipt:
the isolated checkout had not yet received its required governed storage
binding. Supported `repository prepare` and private `init` completed that setup.
Fresh session `01a07131-8919-7a10-9a03-7e5b8133ab67` subsequently reported
`ready: true`, with matching native/bridge execution and `native_effective`.
This is real host-readiness evidence, not phase completion.

The next native Product-entry canary failed before dispatch. Repository
preparation owned run `ab8d619460984f649936ab661addde53`, while ordinary
`loop init` generated an unrelated loop UUID. Artifact ownership correctly
refused the mismatch. No requirement, Product worker, gate or successor was
created. A storage/command regression test reproduced the same error.

First ordinary loop entry now consumes the current ready preflight run ID
after checking its store, repository, checkout and exact target revision.
The artifact ownership check remains unchanged. This does not rebind a
locator, transfer predecessor progress, enable stage-native rollout, or
reopen a terminal run. Six focused tests cover Product, Design and Plan first
entry and refusal of a stale target, cancelled preparation and wrong store.
The native canary still needs rerunning with this repair; no Product PASS is
inferred from those tests. The affected loop and stage-integration suites also
passed: 162 tests and 57 subtests in 469.92 seconds. Thirteen additional
selected integration checks passed, strict typing passed for all 120 source
files, and the changed-code lint and diff checks passed.

Candidate `d342d534e79c3af9b73f7bce96c0767153f32faf` passed all eight CI checks:
4,341 tests and 597 subtests passed, with six skipped and one deselected, in
945.22 seconds. That receipt predates this additional entry repair and is not
relabeled as evidence for the next commit.

Raw native completion counters for the new probes are below. They are
per-session transport counters, not admitted phase budgets or billing totals.

| Native session | Scope | Input | Cached input | Output | Reasoning output |
| --- | --- | ---: | ---: | ---: | ---: |
| `01a0712c-9245-7c00-a4c9-93564dbc1603` | Trusted, unbound readiness probe | 35,083 | 28,032 | 531 | 227 |
| `01a07131-8919-7a10-9a03-7e5b8133ab67` | Bound readiness probe | 34,620 | 28,032 | 633 | 247 |
| `01a07137-1461-74e1-be22-54bb3c2eefc4` | Product-entry refusal before dispatch | 325,209 | 281,472 | 2,470 | 834 |

All three reported zero cache-write input tokens. None completed a governed
phase or supplied final Engineering, Retro or release evidence.

## Native retry and package-generation boundary

Startup repair `82f4889293f50b25f2e3cb0455a835a1c5573dbe` passed all eight CI
checks in run `33962890213`. The full suite passed 4,347 tests and 597 subtests,
with six skipped and one deselected, in 915.32 seconds. Both clean-candidate
archives built successfully. These receipts cover that commit, not later
packaging repairs.

Fresh native root `01a07149-0e93-73f0-8e0f-03795ff24bb8` verified readiness,
initialized the ordinary loop with its preparation owner, and dispatched
`tp_step_product_pm_98859eea197f9f28` with zero inherited turns. Child
`01a07151-d9ee-7740-8e27-87423acdbe84` authored the canary spec and R-0001;
the mechanical PM gate advanced to Design. No Design worker was dispatched.
However, the audit records a mismatched/unobserved dispatch, and SubagentStart
could not bind the pending slot. Its later terminal receipt was gate cleanup,
not a successful native owner binding. Therefore this proves the startup
repair and actual Product artifact creation, **not** a clean native phase or
end-to-end pass. Host connection retries and one malformed worker patch also
occurred and are not reclassified as taskPlane production defects.

The root's raw completion counters were 800,754 input (707,200 cached), 9,769
output and 4,507 reasoning-output tokens. The Product child's counters were
1,394,320 input (1,332,992 cached), 9,853 output and 3,182 reasoning-output
tokens. Both reported zero cache-write tokens. These per-session transport
counters are not admitted phase budgets, billing totals or release authority.

The additional real package-compatibility producer failed on
`2.17.20-on-2.19.2`: current archive validation compared the historical
package's hooks with current-checkout hooks. Its other version-specific inputs
already came from the pinned historical source. The bounded repair supplies
that generation's exact hook authority through the same matrix call; ordinary
current validation still derives current authority, and schema/equality checks
remain strict. No historical source, tag, hook trust or runtime hook is edited.
The regression reproduced the exact failure before the fix. Four new matrix
and altered-hook refusal checks plus eleven existing release checks passed;
17 broader package/release checks passed in 63.38 seconds. These integration
tests are not a clean-candidate release-compatibility receipt; that production
receipt must still be generated after committing this repair.

The committed `d25d84c36a0d24349f227142a160c383425884ac` production matrix
subsequently passed all four cells in a clean candidate checkout, and both
2.19.2 test archives rebuilt. CI run `33965146663` passed seven jobs, but its
aggregate reported four failures, 4,347 passes, six skips, one deselection and
597 passing subtests in 1,065.51 seconds. Every failure was a missing historical
Git object/tag required by the four new compatibility tests: the test job
fetched only 30 commits, unlike the full-history package job. The CI setup
now fetches the release history for that same test job, with credentials still
not persisted. A regression checks that explicit prerequisite. The pinned
historical producer, exact archive assertions and production validators are
unchanged. An in-place package test also refused the user's unrelated dirty
hook configuration as expected; clean-checkout verification is required and
that configuration is not repaired or included in this change.

## Native child identity adapter

The native Product retry exposed a fixture/host protocol mismatch.
`_worker_event_owner` interpreted `agent_type` as the exact emitted task name;
the tests supplied that name in both `agent_type` and `task_name`. Current
Codex supplies a profile in `agent_type` and does not document a task-name
field in SubagentStart. SubagentStop also supplies a child transcript alongside
the common parent transcript. See the [official hook reference](https://learn.chatgpt.com/docs/hooks).

One bounded, read-only adapter now correlates the actual hook's parent/child
IDs with the parent's exact native SubAgentActivity start record, or with the
current child's session metadata for actions and terminal hooks. These record
shapes were observed in CLI 0.153.1; transcript format is not a stable public
API, so missing, foreign, conflicting, oversized or stale identity remains
unavailable. No session-directory search, conversation reconstruction, agent
registry, inferred completion, synthetic lifecycle, owner selection by queue
position, or predecessor execution import is introduced. A child's metadata
alone cannot activate a pending contract. Only the actual SubagentStart path
binds it, and existing lifecycle/gate authority remains separate.

The native-shape regression reproduced the unbound start before the repair.
Thirty identity/lifecycle tests and 85 broader native telemetry, Design,
phase-output and authority tests passed; strict typing passed 121 source files.
A read-only diagnostic against the retained child metadata resolves the exact
observed parent, child and task path without mutating runtime or minting a
receipt. That is correlation evidence, not a rerun of the live phase. A second
red regression showed child terminal usage selecting the parent transcript;
SubagentStop now prefers its supplied child path while ordinary tool events
retain their original path selection.

The usage/identity integration selection passed 84 tests and eight subtests in
79.05 seconds. The final identity/lifecycle selection passed 33 tests in 15.83
seconds, including the real CLI hook shape, read-only child screening, exact
replay idempotency and refusal to activate a pending contract from a child
tool event. Strict typing and lint passed after that guard was added. The
five package-generation tests also passed against committed CI setup repair
`d0741eef9639f5b1ad7a4a1df185a1d5e48769d4` in a clean checkout.

The dispatch marker mismatch in the previous live retry is not waived by this
identity repair. The next live driver must pass the literal standalone role
marker and complete role/action payload, without hiding the marker in encoded
content, and run strict dispatch verification. No clean native phase pass,
Evaluate, Engineering, final sign-off, Retro or marketplace release is claimed.

## Fresh repository-phase native canary and remaining transport block

Candidate `80215ce8d163ba96890a756fa7fa7be340a1c971` was acquired through
`repository prepare` for PR #17 into a new isolated runtime. Only the actual
canary Product requirement and selected baseline graph were sealed as initial
inputs; no previous loop, worker, events or conversation became phase input.
The graph retained all 53 modules and 174 edges while excluding the scanner's
file cache. The real Requirement export emitted handoff
`f68e1053dcc3ec881bc69015adafe6b9636ba6393678c3838b9c1a495404069d`,
fingerprint `9b016ac0813d96d3bacebd35dcbe9d248a47dd4d3e9b3182ce0665da6361f2f2`.
This is an isolated documentation canary, not R-0004 feature delivery or release
authorization. Its local commits were not pushed.

Fresh native root `01a071a5-8cc5-7120-a620-68fe7ff14dbb` observed
`ready: true`, `native_effective`, then used the public `phase pickup` path.
It resolved the exact Design role digest and received a dispatchable owner.
After two host connection retries, strict PreToolUse denied the native spawn:
`role_marker=missing`. Task name, inherited model, high effort and zero inherited
turns matched. Owner `tp_step_designer_design_8df0e89fdd30f36a` never started;
its contract remains pending with no owner. No outputs, seal submission,
focused review, Plan export, or subsequent phase occurred.

The retained host function-call record has an opaque single-string message
(18,020 characters) with no readable role marker. That record is not proof of
the exact hook stdin, nor proof that the model omitted the marker: the new
driver explicitly required full literal role/action text. No ciphertext was
decoded or decrypted. Official hook documentation describes tool arguments but
does not establish this opaque-message behavior. The immediate proven failure
is role observation at native dispatch; the precise host transport cause still
needs a host-visible input diagnostic or a supported host capability.

The denial now reports only input type, character count and line count and
directs inspection before retry. It never logs prompt content, guesses a role
from task name, decrypts input, accepts an absent marker or encourages blind
redispatch. Synthetic opaque, structured and non-standalone message cases
remain denied with the expectation unmatched and no native telemetry binding.
This is a diagnostics repair, **not** a native dispatch fix or canary pass.
The installed alternative Claude CLI reports no authenticated session; no
account connection or alternate-host run was attempted.

The native root's final raw counters were 749,057 input tokens (681,984
cached), 10,053 output, 3,533 reasoning-output and zero cache-write input;
total input plus output was 759,110. These are per-session transport counters,
not admitted phase budgets, billing totals or whole-repair usage. No child
usage exists for this refused spawn.

## Historical CI fixture isolation

CI run `33966775498` on `80215ce` passed seven checks; its aggregate reported
4,365 passed, one skipped, one deselected, five setup errors and 597 passing
subtests in 938.01 seconds. Full history correctly exposed an old differential
test that had been skipped when its pinned Git object was absent. It loaded
the frozen loop against today's changed audit API and failed importing
`AUDIT_EVERY_DEFAULT`. Pinning only the audit dependency was insufficient:
other current dependencies prevented the old control from reaching Evaluate.

The fixture now extracts the exact historical runtime at
`a2139b1ed93cad6e550712506d780b543d7c4b1b`, verifies that it contains the same
pinned loop blob, and executes the unchanged scripted actions in an isolated
process. No historical source bytes or production compatibility API are edited.
Missing history fails rather than skipping the test. Both controls repeat
exactly, the historical Evaluate route is exercised, and the scorer still
refuses to invent breadth from a full-catalog route with no recorded request.

The current trace is not required to be byte-identical to that old runtime:
modern evidence gates reject its old submissions, producing two Execute
attempts instead of the historical Execute/two-Fix sequence. The test now
checks both exact sequences, current zero-lens Execute entries, the v2 audit
schema and explicit breadth/engine fields. It does not claim current end-to-end
execution. All five historical checks passed in 63.75 seconds; the diagnostic
and context-boundary selection passed five tests in 2.05 seconds. The two
changed test files passed 99 tests and eight subtests in 202.98 seconds; strict
typing passed 121 source files, and lint/diff checks passed. Explicit UTF-8 was
then added to the historical Git identity read for Windows portability; the
candidate CI receipt must cover that final fixture form and the committed tree.
