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
