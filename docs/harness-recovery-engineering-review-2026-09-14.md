# Taskplane recovery: independent Engineering review

Started September 14, 2026; completed September 15, 2026. Branch: `codex/restore-2.23.1-harness-recovery`.

**Verdict: the bounded installed Product journey and fresh-task continuity check passed.** The reviewed fixes materially improve Product usability while preserving the tested source and ownership boundaries. All four findings below are corrected. The tested package supports reading, document authoring, requirement controls, planned graph links, source-write refusal, normal finish, and retrieval of saved work. Full native stateless delivery and specialist phase collection remain unverified; no Product human decision or implementation acceptance was self-approved.

## Scope and independence

The initial comparison was restoration commit `b2a8811` through recovery commit `21b59b8cb103f6634f257088ad2267c13b33cbdd`. Follow-up review covers control correction `75329fa`, native-session/parser repair `578e1f5`, and quoted-prose/explicit-denial repair in current candidate `6ada3f44a772d98641bb341c3d199f84bedb4c55`. The restoration keeps the selected 2.23.1 source, `a76e7de12630a1ed083f50799082bb337fe9c582`, and its stateless architecture.

| Current candidate identity | Evidence |
| --- | --- |
| Source | `6ada3f44a772d98641bb341c3d199f84bedb4c55` |
| Installed personal package | `2.23.1+codex.20260915033421` |
| Package/source comparison | Recovery owner reports **261 files checked, zero mismatches**. This establishes package identity, not runtime acceptance. |
| Clean installed Product journey | **Passed** on Codex CLI `0.154.0-alpha.6.2`; detailed outcomes below. |
| Fresh-task continuity | **Passed** for existing requirement/report/source retrieval with zero active contracts and no reactivation or repair. |

This reviewer did not author the initially reviewed Product core:

- `requirements.py`: readiness scoring and separate Product DoR, Product DoD, human decision, and implementation-acceptance projections.
- `tp.py`: standalone Product entry, trusted control admission, document/presentation access, exact-owner finish, and budget-screen treatment.
- `taskplane_lite.py`: read-only command delegation and atomic activation/clear ownership.
- `host_capabilities.py`: the fixed native read-only request and pending-call adapter, including the subsequent literal parser.

This was direct source review with bounded reproductions, without activating a contract or a lens workflow. This reviewer previously authored the native source-review simplification, so that code is **not independently certified here**. The later native-session changes in `review.py` were reviewed only as adapter dependencies. The recovery owner explicitly authorized this reviewer to implement the control-argument and quoted-prose corrections. Their tests are author evidence; the recovery owner independently reviewed the literal-word reader. The adapter owner implemented the native-session scope and explicit-denial corrections, which this reviewer inspected independently.

## Finding: P1 — Product command admission disagreed with the CLI parser

**Status: fixed in `75329fa`, retained in the current installed package.** The installed positive controls passed; the specific negative argument variants are covered by source regression tests. Location: `tp.py` `_product_control_argv` and `_screen_contract_tool`.

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
- The then-current `taskplane/tests/test_product_recovery.py` run passed: **57 tests in 5.72 seconds**. It covers every protected prefix in split and `=` forms, allowed and forbidden authority values, canonical positive cases, source/mixed-patch refusal, shell/workspace binding, and Product lifecycle boundaries.
- Whitespace validation for the changed source and tests passed. No broader suite was rerun for this correction.

## Finding: P1 — Native-session repair added a prerequisite to unrelated tools

**Status: corrected by the adapter owner in `578e1f5`; independently reviewed with focused regression evidence.** Location: `tp.py` `_run_hook_command`.

The first session-binding patch resolved and validated the native session index/header for every claimed native `screen` event, before the existing no-contract screening exit. A missing, changed, or mismatched transcript header could therefore disrupt ordinary source review or Build commands even when they did not need the read-only adapter. This would recreate the kind of broad harness dependency the recovery is intended to remove.

A bounded reproduction with no active contract and a mismatched header showed the difference: the existing `cmd_screen` returned zero and abstained; the new native wrapper raised `HostTranscriptUnavailable` before reaching that screen. No contract or workflow was activated for this reproduction.

The corrected code binds a session only for a claimed native `PreToolUse` command tool with an already active read-only contract. Ungoverned commands, Build commands, and document writes skip the new transcript lookup. The adapter owner added four regression cases covering no-contract and Build commands with missing or foreign transcript metadata; these assert that the new selector is never called and preserve the existing Build screening behavior.

The owner's initial corrected-scope selection passed **144 tests and eight subtests in 11.31 seconds**. The later explicit-denial selection below supersedes that component result. These are separate, overlapping runs, not additive totals.

## Finding: P1 — Quoted Product prose was blocked while shell comments could hide authority options

**Status: corrected in `6ada3f4`; author verification and independent recovery-owner review complete.** Location: `tp.py` `_product_literal_words` and `_product_control_argv`.

Installed attempt 4 failed a normal `req new` command whose double-quoted functional and acceptance text contained semicolons. The old check searched raw text for metacharacters and then required one canonical quoting style. It treated safely quoted prose as a shell effect, so Product could save its document but could not create the requirement needed for its next steps.

The same check omitted shell comments. `decision new policy # --status proposed` was admitted because screening counted the apparent proposed option, but the shell discards it and the CLI defaults to accepted. A graph link with `# --kind planned` had the analogous realized-default problem. Bounded denial tests reproduced both admission errors without executing those registry mutations.

The correction reads only literal shell words for the already restricted engine control. It accepts single/double quotes and escaped data, while rejecting unquoted command composition, comments, expansion, redirection, globs, braces, and tildes. Only space and tab separate words; Unicode whitespace remains argument data. Existing interpreter, engine, workspace, graph-kind, and decision-status checks still apply. No general shell execution path was enabled.

Before correction, the new focused cases produced **13 failures and 20 passes**. The final Product control file passed **92 tests in 8.51 seconds**, covering ordinary prose, quote escapes, literal dollar/backtick data, comments, expansions, Unicode/NBSP, and the previous scope/ownership checks. Nine literal-word samples also matched the arguments received by actual `/bin/sh`; those comparisons executed no engine command. Ruff and whitespace checks passed. The recovery owner independently reviewed the reader and reported no additional confirmed issue. Observed results are retained in the recovery diagnostics as `product-control-literal-focused.log` and `product-control-literal-focused-result.json`.

## Finding: P1 — Native identity errors lacked an explicit blocking hook response

**Status: corrected by the adapter owner in `6ada3f4`; independently reviewed.** Location: `tp.py` `_run_hook_command`.

Expected identity/header failures originally raised outside `cmd_screen`'s blocking-response boundary. A public-entry reproduction showed both `HostTranscriptUnavailable` and `ValueError` becoming exit 70 with stderr and empty stdout. That supplied no explicit denial to the host. No actual host-level bypass was demonstrated; the confirmed defect was reliance on generic process-error behavior for a required refusal.

The corrected dependent path catches its expected identity-resolution failures, emits `decision: block` and native `permissionDecision: deny`, returns zero for the handled hook response, and uses the existing completion journal. Diagnostics contain only a bounded stage and exception class. Public-entry tests cover environment conflict, mismatched indexed header, and missing transcript. They verify the first denial, restored environment, completed blocking journal entry, and a duplicate denial without re-resolution or journal mutation.

The adapter owner's four-file selection—`test_product_native_read_access.py`, `test_product_recovery.py`, `test_codex_hook_selection.py`, and `test_em_h2_bounds.py`—passed **145 tests and eight subtests in 12.15 seconds**. This is separate from the later 92-test Product control run and must not be added to it. Durable records are `native-identity-block-focused.*` and `native-identity-block-static.log` in the recovery diagnostics. This reviewer inspected the final response and replay path and found no further confirmed issue in that correction.

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
| Native read delegation | The reviewed adapter requires the host read-only profile, current workspace, bounded payload, and a matching pending native call for projected shell events. It delegates execution to Codex rather than implementing a second command executor. The final installed journey passed its required read and normal finish on the tested host. |

## Evidence and remaining limits

The recovery owner supplied earlier candidate results: 396 passing focused tests and 274 passing subtests, with five stale assertions subsequently corrected and passed on their exact rerun; 92 passing host tests and 45 passing subtests with one Go skip, followed by one corrected Git fixture test and eight passing subtests; and 16 selected stateless checks. Current-candidate static evidence supplied by the owner includes passing mypy across **114 files** and source-policy checks across **129 Python files with zero violations**. These are reused evidence from separate runs, not newly rerun or additive totals. The host fixture failure reproduced on the selected baseline with an incompatible Git executable; the fixture correction does not certify that every host command environment is usable.

Earlier personal package `2.23.1+codex.20260915023342`, from `21b59b8`, stopped first on absent hook trust. After the user enabled trust, strict Product activated, but Taskplane refused the exact read request it returned. Exact normal finish then worked once, and an ordinary read worked afterward. Attempt 3 also failed its Product read; the recovery owner reports successful exact finish, preserved source integrity, and a subsequent ordinary read. Those failures remain part of the [repeat Product review](harness-recovery-product-review-2026-09-14.md); later source checks do not erase them.

With the native-session and literal-parser repairs, the adapter owner then observed a successful source-engine native read and exact finish. That was a repair exercise using the source engine, distinct from clean installed acceptance.

Installed attempt 4 against personal `2.23.1+codex.20260915032233` completed in **351.9 seconds**. It read source, saved a Product draft, finished its exact contract, preserved source integrity, and completed an ordinary read afterward. Its quoted `req new` command was denied, no requirement ID was created, and the source-write refusal probe was not reached. Evidence is preserved under `native-attempt-4-quoted-control-blocked` in the recovery diagnostics. The readable draft and successful cleanup are partial progress, not a successful full Product phase.

The final installed journey below subsequently covered the returned read, document creation, requirement score/show, planned link, meaningful source refusal, exact finish, and the next ordinary request. The small stateless delivery journey remains unverified on the real installed host.

### Final installed live appendix

**Attempt 5 passed the bounded installed Product journey** against personal `2.23.1+codex.20260915033421`, from source `6ada3f44a772d98641bb341c3d199f84bedb4c55`.

| Required operation | Observed result |
| --- | --- |
| Effective enforcement | Real native receipt observed; Product activation reported **strict / live**. |
| Native read and document authoring | Exact native read and document writes succeeded; a readable Product report was saved. |
| Requirement controls | `req new` created **R-0001**; score/show succeeded. Requirement remains **draft**, with score 1.0 and content DoR passed. |
| Planned dependency link | Planned graph link succeeded and survived finish. |
| Meaningful source refusal | Exactly one native `apply_patch` source probe received a **Taskplane denial**; it was not retried. |
| Source integrity | The probe was absent. Git comparisons confirmed `app.py` and README unchanged from the fixture baseline. |
| Normal finish | Exact task-owner finish succeeded; no active contract remained. |
| Retention and next request | Report, requirement, and graph survived; subsequent ordinary reads succeeded. |
| Harness friction | Zero unexpected Taskplane denials, repair interventions, or unchanged-denial retries. Native sandbox escalation requests succeeded; no hook-trust prompts or approval-review rejections occurred in this final journey. |

The recovery owner independently checked the original source content, empty source diff, absent active contract, and retained report/requirement files. Those checks corroborate the native result; they are not a fabricated phase receipt. Evidence: [native result](../.taskplane/diagnostics/recovery-2026-09-14/native-canary-final.txt), [run metadata](../.taskplane/diagnostics/recovery-2026-09-14/native-canary-result.json), [independent verification](../.taskplane/diagnostics/recovery-2026-09-14/native-independent-verification.json), and [measurements](../.taskplane/diagnostics/recovery-2026-09-14/native-canary-measurements.json).

**Fresh-task continuity also passed.** A separate native task found zero contracts, retrieved the existing R-0001 through list/show, read the retained Product report and source, and made no file changes, workflow activation, or harness repair. Its recorded duration was **79.65 seconds**. This demonstrates retrieval of saved project context, not full memory continuity or same-task interruption recovery. Evidence: [continuity outcome](../.taskplane/diagnostics/recovery-2026-09-14/continuity-final.txt) and [run metadata](../.taskplane/diagnostics/recovery-2026-09-14/continuity-result.json).

**No speedup or cost claim is supported.** The Product wrapper's elapsed timer reported **801.44 seconds**, while its UTC timestamps span **1,869.15 seconds**. Two host transport retry warnings occurred. The mismatch prevents interpreting this run as a reliable harness-duration measurement; report modification time is the final save rather than time to first artifact. The transport warnings are separate from the zero Taskplane repair/retry counts above.

Independent Product specialist dispatch/collection and real native stateless delivery were not demonstrated. The Product report's 26-lens author coverage ledger must not be read as 26 independent workers or accepted phase evidence. Product phase review remains unverified, human disposition remains pending, and implementation acceptance remains unverified. The bounded result does not certify all CI, other hosts/versions, same-task interruption recovery, or this reviewer's own native source-review implementation. It grants no phase approval or implementation signoff.

**Recommendation:** retain the rollback and reviewed corrections. The tested standalone Product path is materially closer to the original goal and now works through its bounded acceptance journey. Treat full delivery and independent phase collection as the next evidence gaps before claiming complete governed-development recovery.
