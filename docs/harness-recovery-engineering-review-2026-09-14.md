# Taskplane recovery: independent Engineering review

Date: September 14, 2026. Branch: `codex/restore-2.23.1-harness-recovery`.

**Verdict: the reviewed Product changes restore useful boundaries, but recovery acceptance remains open.** This review found a Product authority defect with two argument-parsing paths and a later native-session change that would have imposed a new prerequisite on ordinary work. Both findings are corrected in the installed source candidate. Focused checks pass, and repaired source-engine native reads and finish have worked. A clean journey through the corrected installed package is still running; its outcome is not inferred from those earlier results.

## Scope and independence

The initial comparison was restoration commit `b2a8811` through recovery commit `21b59b8cb103f6634f257088ad2267c13b33cbdd`. Follow-up review covers the control correction committed in `75329fa` and the native-session/parser repair committed in current candidate `578e1f5d861e665a71071a5d9a9603dcb7207a94`. The restoration keeps the selected 2.23.1 source, `a76e7de12630a1ed083f50799082bb337fe9c582`, and its stateless architecture.

| Current candidate identity | Evidence |
| --- | --- |
| Source | `578e1f5d861e665a71071a5d9a9603dcb7207a94` |
| Installed personal package | `2.23.1+codex.20260915032233` |
| Package/source comparison | Recovery owner reports **261 files checked, zero mismatches**. This establishes package identity, not runtime acceptance. |
| Clean installed Product journey | Running; final live outcome pending. |

This reviewer did not author the initially reviewed Product core:

- `requirements.py`: readiness scoring and separate Product DoR, Product DoD, human decision, and implementation-acceptance projections.
- `tp.py`: standalone Product entry, trusted control admission, document/presentation access, exact-owner finish, and budget-screen treatment.
- `taskplane_lite.py`: read-only command delegation and atomic activation/clear ownership.
- `host_capabilities.py`: the fixed native read-only request and pending-call adapter, including the subsequent literal parser.

This was direct source review with bounded reproductions, without activating a contract or a lens workflow. This reviewer previously authored the native source-review simplification, so that code is **not independently certified here**. The later native-session changes in `review.py` were reviewed only as adapter dependencies. After reporting the control defect, the recovery owner explicitly authorized this reviewer to make the minimal correction. Its fix and verification are therefore author evidence; the original finding is independent review evidence. The adapter owner implemented the second finding's correction, which this reviewer inspected independently.

## Finding: P1 — Product command admission disagreed with the CLI parser

**Status: fixed in `75329fa`, retained in the current installed package; clean installed revalidation pending.** Location: `tp.py` `_product_control_argv` and `_screen_contract_tool`.

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

## Finding: P1 — Native-session repair added a prerequisite to unrelated tools

**Status: corrected by the adapter owner in `578e1f5`; independently reviewed with focused regression evidence.** Location: `tp.py` `_run_hook_command`.

The first session-binding patch resolved and validated the native session index/header for every claimed native `screen` event, before the existing no-contract screening exit. A missing, changed, or mismatched transcript header could therefore disrupt ordinary source review or Build commands even when they did not need the read-only adapter. This would recreate the kind of broad harness dependency the recovery is intended to remove.

A bounded reproduction with no active contract and a mismatched header showed the difference: the existing `cmd_screen` returned zero and abstained; the new native wrapper raised `HostTranscriptUnavailable` before reaching that screen. No contract or workflow was activated for this reproduction.

The corrected code binds a session only for a claimed native `PreToolUse` command tool with an already active read-only contract. Ungoverned commands, Build commands, and document writes skip the new transcript lookup. The adapter owner added four regression cases covering no-contract and Build commands with missing or foreign transcript metadata; these assert that the new selector is never called and preserve the existing Build screening behavior.

The owner’s final focused selection—`test_product_native_read_access.py`, `test_product_recovery.py`, `test_codex_hook_selection.py`, and `test_em_h2_bounds.py`—passed **144 tests and eight subtests in 11.31 seconds**. This includes the corrected scope tests, native binding/conflict/restoration checks, and literal parser cases. The recovery owner also reports passing Ruff/whitespace checks and mypy across **114 source files**. These results are reused owner evidence; this reviewer did not repeat the full selection.

## Native-session and literal-parser follow-up

For the dependent read-only path, the adapter corroborates the claimed event's exact transcript against the canonical session path and native header. It refuses conflicting session identity and restores the temporary environment binding on both success and error. Projected shell commands must still match one pending native request and pass the fixed outer-shell, workspace, payload, and read-only-profile checks. The repair does not supply a host approval.

The code-mode fallback parses a flat object with identifier or quoted keys and JSON scalar values. It uses no expression evaluation; calls, spreads, computed keys, getters, templates, comments, nested values, and duplicate fallback keys are rejected. The fallback caps keys at 32, preserves string escapes as data, and is used only inside the existing fixed single-tool expression shape. Exact Product finish still passes through the existing task-owner and lifecycle guards. This follow-up found no further confirmed authority defect in the reviewed correction.

## Other reviewed boundaries

| Area | Assessment and practical limit |
| --- | --- |
| DoR and DoD visibility | Requirement content readiness, recorded human decision, phase completion, and implementation acceptance are separate projections. Blank statements no longer pass readiness. Missing phase-review or implementation evidence stays unverified; a requirement approval is not presented as completed implementation. This projection does not itself prove operational readiness. |
| Document and control scope | Product permits its declared document roots while the existing kernel refuses source writes and mixed source/document patches. Controls are restricted to the current engine and workspace with a fixed outer shell. Planned graph links and proposed decision drafts are enforced after the correction. Existing requirement and knowledge handlers remain their data owners. |
| Activation ownership | Product entry refuses existing owners, worker slots, and active delivery. The additional empty-owner check occurs under the same lifecycle lock as activation, closing the check/write race for this path. No source target acquisition or Build authority is introduced. |
| Normal finish | The command names the exact current standalone Product task. Worker/union ownership and active delivery are refused, and the kernel rechecks identity and the supplied guard under its lifecycle lock before clearing. This review found no further concrete sibling/delivery release regression in the inspected changes. |
| Exhausted or unreadable meter | Product presentation, human input, and exact normal finish remain reachable. This is native permission deferral, not a manufactured host approval. Source writing remains subject to the same contract boundary. |
| Native read delegation | The reviewed adapter requires the host read-only profile, current workspace, bounded payload, and a matching pending native call for projected shell events. It delegates execution to Codex rather than implementing a second command executor. Earlier installed integration failed; repaired source-engine read/finish succeeded. Clean installed acceptance remains pending. |

## Evidence and remaining limits

The recovery owner supplied earlier candidate results: 396 passing focused tests and 274 passing subtests, with five stale assertions subsequently corrected and passed on their exact rerun; 92 passing host tests and 45 passing subtests with one Go skip, followed by one corrected Git fixture test and eight passing subtests; and 16 selected stateless checks. These are reused evidence from separate runs, not newly rerun or additive totals. The host fixture failure reproduced on the selected baseline with an incompatible Git executable; the fixture correction does not certify that every host command environment is usable.

Earlier personal package `2.23.1+codex.20260915023342`, from `21b59b8`, stopped first on absent hook trust. After the user enabled trust, strict Product activated, but Taskplane refused the exact read request it returned. Exact normal finish then worked once, and an ordinary read worked afterward. Attempt 3 also failed its Product read; the recovery owner reports successful exact finish, preserved source integrity, and a subsequent ordinary read. Those failures remain part of the [repeat Product review](harness-recovery-product-review-2026-09-14.md); later source checks do not erase them.

With the native-session and literal-parser repairs, the adapter owner then observed a successful source-engine native read and exact finish. That was a repair exercise using the source engine, distinct from a clean journey through the corrected installed package. The current package identity is recorded above; fresh installed evidence must cover the returned read, document creation, requirement score/show, planned link, meaningful source refusal, exact finish, and the next ordinary request. The small stateless delivery journey also remains unverified on the real installed host.

### Final installed live appendix

**Pending.** The clean `native-canary-*` journey is running against personal `2.23.1+codex.20260915032233`. This appendix will record its actual result and reached/unreached steps when the recovery owner supplies the completed evidence.

Independent Product specialist dispatch/collection was not demonstrated. The Product report's 26-lens author coverage ledger must not be read as 26 independent workers or accepted phase evidence. No complete recovery, phase acceptance, performance improvement, or implementation signoff is granted by this Engineering review.

**Recommendation:** retain this recovery direction and the reviewed corrections; complete the clean installed acceptance journey before claiming the harness is usable again.
