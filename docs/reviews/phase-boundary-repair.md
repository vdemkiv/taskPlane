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
