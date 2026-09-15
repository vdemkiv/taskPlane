# Taskplane recovery: independent Engineering review

Date: September 14, 2026. Branch: `codex/restore-2.23.1-harness-recovery`.

**Verdict: the reviewed Product changes restore useful boundaries, but recovery acceptance remains open.** This review found one material Product authority defect with two argument-parsing paths. Both are corrected in the working tree and pass focused regression checks. The last installed candidate still failed its first required Product read; that separate adapter correction and the corrected package need a fresh installed journey.

## Scope and independence

The comparison is restoration commit `b2a8811` through recovery commit `21b59b8cb103f6634f257088ad2267c13b33cbdd`, plus the narrowly authorized control correction described below. The restoration keeps the selected 2.23.1 source, `a76e7de12630a1ed083f50799082bb337fe9c582`, and its stateless architecture.

This reviewer did not author the initially reviewed Product core:

- `requirements.py`: readiness scoring and separate Product DoR, Product DoD, human decision, and implementation-acceptance projections.
- `tp.py`: standalone Product entry, trusted control admission, document/presentation access, exact-owner finish, and budget-screen treatment.
- `taskplane_lite.py`: read-only command delegation and atomic activation/clear ownership.
- `host_capabilities.py`: the fixed native read-only request and pending-call adapter at `21b59b8`.

This was direct source review with bounded reproductions, without activating a contract or a lens workflow. This reviewer previously authored the native source-review simplification, so that code is **not independently certified here**. After reporting the control defect, the recovery owner explicitly authorized this reviewer to make the minimal correction. Its fix and verification are therefore author evidence; the original finding is independent review evidence.

## Finding: P1 — Product command admission disagreed with the CLI parser

**Status: fixed in source; corrected package and installed revalidation pending.** Location: `tp.py` `_product_control_argv` and `_screen_contract_tool`.

The Product adapter admitted a command by inspecting protected option spellings, while the CLI's existing argument parser accepts shortened long options and later values. A command could contain the required `planned` or `proposed` value and still reach a handler with stronger authority:

| Admitted argument sequence at the reviewed candidate | Actual parsed result |
| --- | --- |
| `graph link --req R-0001 --files taskplane/tp.py --kind planned --ki realizes` | `kind=realizes` |
| `decision new policy --status proposed --st accepted` | `status=accepted` |
| `decision new policy --status proposed --su 0001` | `supersedes=0001` |
| `decision new -- --status=proposed` | Title is the literal `--status=proposed`; default status is `accepted` |

The first three also reproduced with `--ki=realizes`, `--st=accepted`, and `--su=0001`. The final case arose because screening counted a title after the `--` option terminator as a status option. These paths defeat the intended planned-only graph and proposed-only decision boundaries. Real parser namespaces were captured before invoking command handlers; the reproductions did not create a realized graph edge, accept a decision, or supersede existing knowledge.

The correction adds one small protected-option spelling check, shared with the existing workspace check. It rejects abbreviated workspace, graph base/kind, and decision status/supersedes options before admission. Command-specific checks now inspect only options before `--`. Canonical planned/proposed values, including `=` forms, remain usable; literal option-like titles remain data. No general command parser, new permissions service, or delivery approval route was added.

Validation:

- All six abbreviation denial cases failed before the correction.
- The option-terminator refusal and legitimate literal-title cases both failed before their correction.
- The final full `taskplane/tests/test_product_recovery.py` run passed: **57 tests in 5.72 seconds**. It covers every protected prefix in split and `=` forms, allowed and forbidden authority values, canonical positive cases, source/mixed-patch refusal, shell/workspace binding, and Product lifecycle boundaries.
- Whitespace validation for the changed source and tests passed. No broader suite was rerun for this correction.

## Other reviewed boundaries

| Area | Assessment and practical limit |
| --- | --- |
| DoR and DoD visibility | Requirement content readiness, recorded human decision, phase completion, and implementation acceptance are separate projections. Blank statements no longer pass readiness. Missing phase-review or implementation evidence stays unverified; a requirement approval is not presented as completed implementation. This projection does not itself prove operational readiness. |
| Document and control scope | Product permits its declared document roots while the existing kernel refuses source writes and mixed source/document patches. Controls are restricted to the current engine and workspace with a fixed outer shell. Planned graph links and proposed decision drafts are enforced after the correction. Existing requirement and knowledge handlers remain their data owners. |
| Activation ownership | Product entry refuses existing owners, worker slots, and active delivery. The additional empty-owner check occurs under the same lifecycle lock as activation, closing the check/write race for this path. No source target acquisition or Build authority is introduced. |
| Normal finish | The command names the exact current standalone Product task. Worker/union ownership and active delivery are refused, and the kernel rechecks identity and the supplied guard under its lifecycle lock before clearing. This review found no further concrete sibling/delivery release regression in the inspected changes. |
| Exhausted or unreadable meter | Product presentation, human input, and exact normal finish remain reachable. This is native permission deferral, not a manufactured host approval. Source writing remains subject to the same contract boundary. |
| Native read delegation | The reviewed adapter requires the host read-only profile, current workspace, bounded payload, and a matching pending native call for projected shell events. It delegates execution to Codex rather than implementing a second command executor. Its actual installed integration failed and is not accepted on the strength of those source checks. |

## Evidence and remaining limits

The recovery owner supplied earlier candidate results: 396 passing focused tests and 274 passing subtests, with five stale assertions subsequently corrected and passed on their exact rerun; 92 passing host tests and 45 passing subtests with one Go skip, followed by one corrected Git fixture test and eight passing subtests; and 16 selected stateless checks. These are reused evidence from separate runs, not newly rerun or additive totals. The host fixture failure reproduced on the selected baseline with an incompatible Git executable; the fixture correction does not certify that every host command environment is usable.

The last installed package was personal `2.23.1+codex.20260915023342`, from `21b59b8`. As recorded in the [repeat Product review](harness-recovery-product-review-2026-09-14.md), its first probe stopped on absent hook trust. After the user enabled trust, strict Product activated, but Taskplane refused the exact read request it returned. Exact normal finish then worked once, and an ordinary read worked afterward. That proves a useful cleanup improvement; it does not prove Product draft/save/finish, source-write refusal on the actual host, or successful delivery.

The adapter owner is correcting that observed read integration defect separately. This report does not certify that later correction, the next package identity, or its installed behavior. Fresh installed evidence must cover the returned read, document creation, requirement score/show, planned link, meaningful source refusal, exact finish, and the next ordinary request. The small stateless delivery journey also remains unverified on the real installed host.

Independent Product specialist dispatch/collection was not demonstrated. The Product report's 26-lens author coverage ledger must not be read as 26 independent workers or accepted phase evidence. No complete recovery, phase acceptance, performance improvement, or implementation signoff is granted by this Engineering review.

**Recommendation:** retain this recovery direction, include the corrected Product controls, finish the native adapter repair, and repeat the installed acceptance journey before claiming the harness is usable again.
