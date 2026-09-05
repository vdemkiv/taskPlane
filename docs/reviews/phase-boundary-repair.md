# Phase boundary repair: Design, Plan and Build

Date: 2026-09-04. Status: source repair under validation; not an installed-release or native-host sign-off.

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

## Still uncovered: separate adapter and retry paths

The public `phase pickup` / `phase resume` path is distinct from native `loop next`. This repair must not be presented as making those paths interchangeable.

1. Stateless non-Build startup emits a `phase-worker-result/v1` descriptor and a default `plan/result.json` write target. The native planner instead produces `plan/tasks.json` and `plan/plan.md`. No connected consumer for those stateless worker results was found in the audit.
2. The public pickup receipt deliberately omits private bootstrap/lease data and does not supply a complete native role/continuation adapter. Stateless Build also uses `proofs`/`acceptance` and `phase submit`, unlike the native executor's `tests`/`criteria` and `loop submit` protocol.
3. Native parallel Build retry has a separate attempt-identity mismatch: `wave()` names from the task ID, while `claim()` reserves an attempt-suffixed identity. A repeated claim or legacy-pending recovery can therefore disagree with the wave's native intent. Run namespaces alone do not fix this. Wave, intent and claim must share one authoritative reserved attempt, tested on retry and legacy recovery as well as first dispatch.

These are open P0 design inputs for R-0004. Complete the matching public adapter and result-consumption path with fresh-worker round-trip tests; do not expose private leases, reuse predecessor runtime, or bypass approval to paper over the gap.

## Release and live-run limits

No installed plugin cache, historical receipt, approval, old phase artifact or live worker contract is rewritten by this source repair. Changed role files require a matching supported plugin release before live native verification. That verification must also prove the native worker can read its bounded evidence, compute its result digest and write the permitted artifact under the real host contract; the subprocess test does not prove those capabilities. R-0004 remains the existing fresh Design run, with older Design material used only as reference. This document is an RCA and verification boundary, not an Engineering sign-off or retrospective claiming completed delivery.
