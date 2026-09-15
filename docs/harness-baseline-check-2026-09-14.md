# Taskplane recovery baseline check

Historical assessment or plan: see the [repeat Product review](harness-recovery-product-review-2026-09-14.md) for the implemented recovery and actual verification outcomes.

Date: September 14, 2026. All historical times below are America/New_York.

## Recommendation

**Correction after the user's review: keep the refactor's consolidation; the recommendation to restore `1c4b63e` is withdrawn.** The [precise reassessment](harness-refactor-reassessment-2026-09-14.md) checked the original user decisions, retirement map, replacement runtime and test behavior. `1c4b63e7f2f0caecca5e6810c238ee598783f657` is the immediate predecessor of the large refactor, but its stateless machinery is behind rollout controls and its normal path still retains the older loop.

Both older snapshots passed **164 local tests plus 21 subtests each**. These results remain valid executions, but include obsolete path and prose checks and do not establish that the snapshots are preferable products. The prescribed standalone Product contract rejects necessary document work in both.

The revised recovery direction is to retain the active stateless runtime and intended retirements, repair the demonstrated Product capability/completion mismatch, and evaluate later enforcement changes independently. Current main and the installed 2.24.0 plugin were not changed.

The earlier 2.19.0-first and pre-refactor-restoration proposals are both superseded. The evidence below is preserved with its corrected interpretation.

## 1. What changed, and when

| Date | Source point | Significance |
| --- | --- | --- |
| September 5, 17:27 | [PR #18](https://github.com/vdemkiv/taskPlane/pull/18), merge `1cf41e9` | Last major revert. Restored the 2.19.0 source tree and backed out the then-current stateless rewrite. Too early for the user's present preference. |
| September 9, 06:10 | [PR #19](https://github.com/vdemkiv/taskPlane/pull/19), `9a6a87e` | Stateless restart merged, labeled 2.20.0. |
| September 9, 09:54 | [PR #20](https://github.com/vdemkiv/taskPlane/pull/20), `9871489` | Further restart/continuity work, still labeled 2.20.0. |
| September 9, 12:04 | [PR #21](https://github.com/vdemkiv/taskPlane/pull/21), `da3cd4d` | Last main snapshot before PR #22. First tested historical snapshot. |
| September 9, 18:23 | [`1c4b63e`](https://github.com/vdemkiv/taskPlane/commit/1c4b63e7f2f0caecca5e6810c238ee598783f657) | Five subsequent commits include startup/protocol fixes; phase-runtime cutover and full retirement are still unfinished. |
| September 10, 12:43 | [`146104c`](https://github.com/vdemkiv/taskPlane/commit/146104cc089872394bfa9db648635d06efd07577) | “Refactor stateless phase harness and retire obsolete runtime and tests”: **347 files, +20,620 / −62,105 lines**. This is the explicitly requested consolidation and cleanup. Size does not establish the cause of the user's regressions; the reassessment identifies a specific readiness/execution mismatch on its new evidence path. |
| September 10, 13:36 | [PR #22](https://github.com/vdemkiv/taskPlane/pull/22), `7a67072` | Merges that refactor and the preceding startup work: **360 files, +21,307 / −62,256 lines** against the previous main snapshot. |
| September 11, 13:37 | [`e8a8db4`, 2.23.2](https://github.com/vdemkiv/taskPlane/commit/e8a8db4a4f03fb93dbae4bd226ead5f7530d5cd8) | Further enforcement tightening: removes advisory enforcement recovery, binds phase token ceilings into live contracts, extends budget enforcement, and restricts review/lens budgets. |

The September 9 candidates and the September 10 PR #22 merge all report **2.20.0**. Version number alone cannot select the intended baseline. Use the exact commit and matching skills, hooks, and engine.

The size and timing locate a source boundary but do not justify reversing it. The original task records serious usability failures before this refactor. Native token requirements also already existed; September 11 strengthened their application rather than introducing all metering from scratch.

## 2. What was checked

Six exact source revisions were copied into isolated temporary repositories. Tests used separate temporary state, the installed Python 3.13.9, and the existing test runner. No candidate was activated as the user's plugin, and no current run state was imported into an older engine.

| Snapshot | Version | Core checks | Focused phase integration |
| --- | --- | --- | --- |
| `da3cd4d9090b644c195b5bb02cc804f000149294` — last main before PR #22 | 2.20.0 | **135 passed + 21 subtests** | **29 passed** |
| `1c4b63e7f2f0caecca5e6810c238ee598783f657` — immediately before large refactor | 2.20.0 | **135 passed + 21 subtests** | **29 passed** |
| `7a6707233643a26c966cb8b7fe0c75bd7e8cf04d` — after PR #22 | 2.20.0 | **114 passed + 21 subtests** | Not run |
| `a76e7de12630a1ed083f50799082bb337fe9c582` — before 2.23.2 | 2.23.1 | Policy probe only | Not run |
| `e8a8db4a4f03fb93dbae4bd226ead5f7530d5cd8` — enforcement tightening | 2.23.2 | Policy probe only | Not run |
| `7fb20110aeb51d1a088ec3c8f257b641736996ee` — current checkout | 2.24.0 | **115 passed + 21 subtests** | Not run |

These are each revision's selected existing tests. The counts are not a like-for-like benchmark: tests were removed and added between revisions. Later runs emitted test-report-format warnings; all selected tests completed without failures. No timing comparison is used as product performance evidence.

The table records the initial comparison. The subsequent [precise reassessment](harness-refactor-reassessment-2026-09-14.md) also ran current stateless integration checks: **15 passed after PR #22 and 16 passed at 2.23.1**, after making the required tools available in the isolated execution environment. Initial failures exposed the readiness/environment mismatch documented there. No installed-host usability claim follows from these checks.

The core selection covers requirements and human sign-off, DoR/DoD, deterministic handoffs, dispatch, CLI boundaries, phase retry, and product graph relationships. The pre-refactor selection also includes non-Build handoff tests removed later.

The additional 29 checks cover Product readiness/refusal, Product/Design/Plan transitions, immutable output capture, rejection of changed or unverified handoffs, transition replay, Build output propagation, and Retro completion/retry. They use synthetic fixtures and some mocked host/validation dependencies. Even tests named “real” here exercise runtime functions in a test workspace; they are **not live native-host acceptance evidence**.

## 3. Stateless machinery versus the normal execution path

Both pre-refactor candidates contain stateless components, and selected tests exercise them. However, new/unbound runs default to the disabled stage mode, and the old loop coexists with the stage path. Those tests did not establish stateless execution by default. The refactor activates the current phase runtime and makes the aggregate authoritative.

`1c4b63e` adds five commits over `da3cd4d`—31 files, +871 / −382 lines—including startup/protocol fixes and shared review/telemetry cleanup. Both passed the selected checks. The original task nevertheless identifies `1c4b63e` as an unfinished cutover, after substantial harness failures. The passing count does not support preferring it over the completed consolidation.

Keep the active stateless model as a recovery requirement. Preserve selected handoffs and evidence without reintroducing the old parallel runtime or another task's restrictions.

## 4. DoR and DoD findings

- Before the refactor, `test_dor_dod.py` contains **17 tests**, including explicit dashboard DoR/DoD visibility, readiness trace data, and sign-off completion checks. They passed on both candidates.
- Commit `146104c` removes **11 of those tests**, leaving six. The dedicated dashboard visibility and sign-off tests are among the removals.
- The refactor leaves the dashboard renderer in place and moves readiness/sign-off behavior into other runtime modules. **It does not remove all DoR/DoD implementation.** Test deletion alone cannot prove the visible feature stopped working.
- Detailed inspection found old mutable singleton/sign-off fixtures, old review-lens expectations, and a dashboard test that checked only the presence of two words. The replacement runtime still computes readiness and seals DoD at Engineering. Do not restore obsolete tests to recreate retired behavior; verify the intended outcome on the current path.
- This task's actual current Product review omitted an explicit gate verdict until the user corrected it. That presentation failure remains valid regardless of the test-retirement decision.

The recovery acceptance criterion remains visible, criterion-level DoR and DoD at phase entry and completion, with evidence, unresolved gaps, and the next owner. A passing requirement score must not substitute for a phase-completion verdict.

## 5. A rollback alone would retain a Product blocker

A common **synthetic policy probe** used each revision's actual contract builder and screener with the Product skill's prescribed standalone contract: read-only, scope `docs/**,specs/**,knowledge/**`, and no document write allowance. The probe did not activate contracts, execute the candidate tool requests, supply approval, or simulate a successful host journey.

| Requested operation | Both September 9 candidates | After PR #22 / 2.23.1 / 2.23.2 | Current 2.24.0 |
| --- | --- | --- | --- |
| Native file read | Allowed | Allowed | Allowed |
| Save Product document | **Denied** | **Denied** | **Denied** |
| Edit implementation | Denied, as required | Denied, as required | Denied, as required |
| Shell-based file read used by Codex | **Denied** | **Denied** | **Denied** |
| Present Product document in Codex | **Denied** | **Denied** | **Denied** |
| Create requirement through the engine CLI | **Denied** | **Denied** | Allowed at this policy layer |
| Link requirement to graph through CLI | **Denied** | **Denied** | **Denied** |
| Ordinary contract clear | **Denied** | **Denied** | **Denied** |
| Read contract status | Allowed | Allowed | Allowed |

Adding an explicit documentation write allowance to a hypothetical contract allowed the document write and still refused the source edit on **all six snapshots**. This identifies a bounded correction using existing capability. It does not fix the other control, presentation, or completion paths and was not applied to production.

The probe concerns the standalone Product example. Stage workers can receive different contracts. Runtime telemetry, hook liveness, obligations, and downstream command validation are additional conditions not exercised by this probe. These results therefore establish a permission mismatch, not a complete verdict on every governed phase journey.

## 6. Recovery decision and acceptance

**Revised path:** retain the consolidation and intended retirements in `146104c`; repair the current Product capability/completion mismatch and inspect later enforcement changes separately. If a historical comparison is needed, post-refactor 2.23.1 `a76e7de` is a more relevant comparison point. None of these snapshots is certified as an installed restoration target. See the [reassessment](harness-refactor-reassessment-2026-09-14.md).

| Gate for this baseline decision | Result | Evidence / outstanding work |
| --- | --- | --- |
| Exact pre-refactor source identified | **PASS** | Parent of `146104c`, exact commit recorded above. |
| Stateless handoff components and selected transitions retained | **PASS — selected local checks only** | Does not establish the default execution path; pre-refactor new runs retain the old loop by default. |
| DoR/DoD words present in old dashboard fixture | **PASS — weak local assertion** | Did not verify correct criterion-level presentation through the installed stateless journey. |
| Standalone Product can perform its required work | **FAIL — policy mismatch** | Document, control, presentation, and ordinary release refusals above. |
| Installed Product draft–save–finish and small governed Build work | **NOT VERIFIED** | No candidate plugin installed or live phase journey run in this check. |
| Restoration DoD | **NOT SATISFIED** | Required usability fixes and installed acceptance are outstanding. |

Before accepting a restored release, demonstrate:

1. Product can inspect, author, link, present, and finish without user-operated harness repairs; a source edit remains refused.
2. A small approved change completes through stateless phases with visible DoR and DoD, evidence-backed completion, and ordinary cleanup.
3. Interrupting and resuming preserves selected inputs and evidence; a second task starts without inheriting active restrictions.
4. The exact packaged hooks/skills/engine work with the current host. Keep current run artifacts separate until compatibility is verified.

This is the completion condition for a restoration, not another runtime gate system to build. No implementation or restoration approval is recorded by this document.

## Evidence and related assessment

Local test commands, exact source manifest, results, policy observations, and XML reports are saved under [the baseline-check evidence directory](../.taskplane/diagnostics/baseline-check-2026-09-14/). The original isolated repositories remain under `/private/tmp/tp-harness-check-lxl9dypk` for follow-up during this session.

See [the Product recovery assessment](harness-product-recovery-2026-09-14.md) for the full problem spaces, proposed requirement, and phase-level DoR/DoD. This check narrows its recovery recommendation while preserving the user's requested Product scope.
