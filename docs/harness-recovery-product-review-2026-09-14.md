# Taskplane recovery: repeat Product review

Date: September 14, 2026. Branch: `codex/restore-2.23.1-harness-recovery`.

**Current verdict: the source is materially closer, and normal installed cleanup worked, but the last installed Product journey failed at its first read. Recovery acceptance is not met.** The exact read request returned by activation was refused by Taskplane. The adapter owner is correcting that defect; the next candidate must repeat the journey. Passing source checks and successful cleanup cannot substitute for draft–save–finish or a real small governed change.

This repeats the [original Product assessment](harness-product-recovery-2026-09-14.md) against the [authorized recovery plan](harness-recovery-implementation-plan-2026-09-14.md). The user authorized rollback, identified fixes, and this repeat review. That instruction authorizes the work; it does not pre-accept its eventual result. This report author created no new runtime authority. The recovery owner separately performed an installed Product probe and repaired an inert workspace locator; those actions and their limits are recorded below.

## 1. Candidate and evidence boundaries

| Item | Inspected state / evidence |
| --- | --- |
| Earlier assessed source | `7fb20110aeb51d1a088ec3c8f257b641736996ee`, installed 2.24.0 at the original assessment. |
| Selected recovery source | 2.23.1, `a76e7de12630a1ed083f50799082bb337fe9c582`, preserving the consolidation and stateless default. This was not certified as the user's last working installation. |
| Restoration commit | `b2a8811` plus the current repairs on the recovery branch. |
| Last installed source / next candidate | `21b59b8cb103f6634f257088ad2267c13b33cbdd` was packaged and exercised. Corrections to the actual native-read failure and abbreviated-control guard findings are in progress; **their final SHA and revalidation remain pending**. |
| Installed engine / skill / hook identity | Personal `2.23.1+codex.20260915023342`, from source `21b59b8`. The remote 2.24 installation was removed to avoid duplicate hooks. The user then enabled the new installation's hook trust. This identifies the tested installation, not the next corrected candidate. |
| Confirmed local validation available to this author | Requirements module: **49 tests and 49 subtests passed**; focused Ruff and whitespace checks passed. This verifies the modified readiness/projection behavior locally, not installed Product entry or complete recovery. |
| Aggregate candidate checks | Source selection: **396 passed, 274 subtests passed, five failed**; the five stale assertions were corrected and passed on focused rerun. Host selection: **92 passed, 45 subtests passed, one Go skip**, with six failed subcases in one hook test; these reproduced on the baseline with an incompatible Intel Git executable. The corrected fixture's exact test and eight subtests passed. These are separate runs; do not sum overlapping reruns into a new pass total. |
| Stateless delivery checks | **16 selected checks passed**, including serial/parallel Evaluate–Engineering–Retro and missing-seal/handoff refusal. Native lifecycle events are simulated test inputs. No installed delivery completion is claimed. |
| Static/release checks | Ruff, mypy, source policy, version verification, and release-surface checks passed. Generated CLI validation passed after correcting the temporary-directory environment. Refactor metrics reported **eight debt violations**; this is not a clean full release-CI claim. |
| Installed native journeys | Attempt 1 stopped before Product entry because hook trust was absent. After the user enabled trust, attempt 2 observed `native_effective`, activated strict Product, and **failed on the exact returned native read**. Normal exact-owner finish succeeded once; a subsequent ordinary read worked. No requirement, Product report, graph link, or source-write refusal probe was reached. |

The Product reviewer inspected the current source and instructions directly. This author also implemented the requirement gate presentation and its blank-content correction; this report is not an independent Engineering certification of that component. The 26-lens ledger below is a disclosed Product assessment, not proof that 26 specialists ran.

Evidence is retained in [the recovery diagnostics](../.taskplane/diagnostics/recovery-2026-09-14/), including the [native outcome](../.taskplane/diagnostics/recovery-2026-09-14/native-canary-final.txt), [stateless check metadata](../.taskplane/diagnostics/recovery-2026-09-14/stateless-delivery.json), [static results](../.taskplane/diagnostics/recovery-2026-09-14/static-results.json), and [storage verification](../.taskplane/diagnostics/recovery-2026-09-14/storage-verification.json). Some original run records retain a nonzero result followed by a focused correction/rerun; their original failure must remain visible.

### What the installed attempt actually proved

Strict Product activation and effective native hooks were observed. Taskplane then rejected its own returned read request and the probe stopped without an unchanged retry or an enforcement workaround. Exact `clear --task-id` succeeded once and ordinary source reading worked afterward. This is a real improvement over the earlier stranded contract, but **usable Product entry failed**. The probe process's exit code of zero indicates that its blocker report completed, not that the Product journey passed.

No requirement ID or saved Product report was created. Scoring, planned graph linking, the source-write refusal probe, and full Product DoR/DoD were not reached. Source comparison against HEAD was also unverified because `/usr/local/bin/git` failed with “Bad CPU type in executable”. The same architecture issue reproduced on `a76e7de`; it is an environment limitation, not evidence of a new harness regression. Hook trust enablement and applicable native/developer policy constraints are also separate from the confirmed Taskplane read-admission defect.

The missing workspace locator was repaired through a fresh inert preflight. All five prior manifests and Git selection metadata were preserved; all 40 current stage-object digests and sizes verified. The old Product context remains archived and was not automatically selected. This establishes preservation of the checked authority/artifact references, not a byte-for-byte audit of every historical output or successful cross-task knowledge reuse.

## 2. The same original goals

| Original goal | Earlier experience | Candidate change | Current assessment |
| --- | --- | --- | --- |
| **Visibility** | A readiness score could coexist with unusable operations; DoR/DoD were absent from the review; completion and approval were easy to confuse. | Requirement Markdown and score/show results project separate content DoR, recorded human decision, Product DoD, and implementation acceptance. Blank content fails readiness; requested changes are visible as `changes_required`; missing review remains unknown. Product instructions require operational evidence and next owners. | **Improved in source and focused local checks.** Installed presentation and the full phase handoff remain to be demonstrated. |
| **Focus** | Product's contract blocked its documents, planned graph link, presentation, and ordinary exit. Repairing the harness displaced the requested work. | Product has explicit document authority, required installed-engine controls, planned-only graph linking, proposed-only decision drafts, permitted artifact presentation, and an owner-specific finish. Ordinary source Review, Help, and Status use native tools without automatic activation. | **Mixed installed result:** exact normal finish worked, but required native reading failed before Product authoring. Corrected admission and control-guard fixes require revalidation. |
| **Continuity** | Artifacts survived only after an explicitly approved cleanup; blank session context and private knowledge/session coupling raised reuse concerns. | Normal Product finish retains artifacts and decisions, preserves other owners, and grants no Build authority. Stateless inputs and existing knowledge ownership remain. | **Partially demonstrated:** normal finish and the subsequent ordinary read worked; five prior manifests and 40 current stage objects verified after locator recovery. Same-task resume and cross-task knowledge retrieval remain unverified. |

Lower supervision cost is the test of these goals in use. The normal path should require zero Taskplane-specific user repair commands and zero unchanged retries. The second installed attempt made no unchanged retry or harness workaround, and normal finish worked. The first attempt required the user to enable the new hook trust; separate this installation action from normal Product work. No time-to-artifact or cost improvement is established because authoring was never reached.

## 3. The same nine problem spaces

| Priority / problem space | Before | Candidate evidence and improvement | Remaining gap / next owner |
| --- | --- | --- | --- |
| **P0 — DoR/DoD disappear** | The original review lacked explicit gate verdicts; scoring and completion were conflated. | `requirements.product_gate_summary`, readable requirement projection, `req score`/`req show`, and the Product procedure expose definitions, criteria, evidence, gaps, and owners. Shared scoring now rejects empty statements and NFR values. | **Improved.** Requirement records cannot validate stage-review or implementation evidence. Product must consume actual stage evidence when available; installed delivery must show the same distinctions. |
| **P0 — Phase obstructs its own work** | Read-only authority permitted neither the expected documents nor required controls. | `cmd_new --product` grants document scope and the adapter returns a native read request. | **Failed on the last installed candidate:** that exact read was blocked after strict activation. Document/graph operations were not reached. Adapter correction and fresh installed evidence are required. Recovery owner. |
| **P0 — Recovery becomes the task** | Ordinary release and its suggested remedy were blocked; the user had to authorize a special repair. | Exact `clear --task-id` is scoped to the current standalone Product owner; replacement/foreign/worker/active-delivery authority is protected. | **Partly demonstrated:** exact normal finish succeeded once after the unexpected read denial, and a subsequent ordinary read worked. Full finish/cancel/limit and refusal–resume scenarios remain open. |
| **P1 — Enforcement exceeds useful role** | Native work was wrapped in broad prerequisites and repeated proof requirements. | Ordinary source Review, Help, and Status avoid automatic onboarding/contracts. Product uses the host's sandbox and existing domain checks. No replacement scheduler is added. | **Partially improved.** Product still relies on exact command shape and pending native-call matching. Delivery retains lifecycle/receipt/telemetry controls. Their proportionality and installed compatibility remain open; do not declare native duplication eliminated. |
| **P1 — Failures collapse into “blocked”** | Unavailable evaluation or integration capability could resemble a product defect and trigger wasted repair. | Quality probes and evidence execution share `evidence_command_environment`; ambient-only Python tools no longer justify readiness. Product instructions distinguish capability, content, execution, optional output, and acceptance. | **Improved for the demonstrated environment mismatch.** Stale-engine, optional-telemetry, unsupported host, and recovery cases still need the declared scenario evidence. Evaluate must not turn missing dependencies into a source-code FIX. |
| **P1 — Cost controls do not show value** | Repeated setup, large context, checks, and recovery consumed effort without demonstrated benefit. | Reduced ordinary-entry instructions and no automatic Review activation remove work from common requests. Product instructs one score/link sequence and no retry without a changed condition. | **Unmeasured.** Native waiting/polling behavior is not repaired or benchmarked by these changes. The existing emitted wait policy still governs delivery. Record time to artifact, interventions, non-progress retries, and unavailable usage honestly. |
| **P1 — Session isolation threatens knowledge continuity** | New blank context and a new session-qualified requirement raised concern about project-memory reuse. | Five prior manifests/metadata and 40 stage-object digests verified after an inert locator recovery; archived Product context was not selected as new authority. | **Preservation improved; reuse remains open.** The knowledge/session boundary remains unchanged. Run interruption/resume and second-task retrieval without inherited restrictions. Recovery owner, then Product. |
| **P1 — Readiness/visibility mislead** | Setup reported ready while required work failed; optional presentation blocked delivery. | Content readiness is separate, and actual probe reporting correctly records a blocked journey despite a completed reporting process. | **Still demonstrated operational gap:** native-effective hooks and successful activation did not establish an executable read. The exact adapter request failed. Correct the defect and repeat the journey before a ready claim. |
| **P1 — Release evidence misses the journey** | Large test counts coexisted with incomplete installed acceptance. | Recovery has a criterion-level matrix, narrow behavioral refusals, retained stateless checks, and explicit source/simulation/installed evidence distinctions. | **Still open until exercised.** Installation and complete native journeys remain the release evidence boundary. No cross-host success or successful governed delivery is inferred here. |

### Specific remaining product risks

1. **Standalone specialist review is not yet demonstrated.** The Product skill calls for a focused route and selected specialist work. That instruction and this author ledger do not prove dispatch, admissible collection, candidate binding, or Product phase completion. Reuse the existing stage mechanisms where applicable; do not invent a receipt to close the gap.
2. **Native command matching produced a real failure.** Codex read/control admission depends on the expected outer shell, workspace, fixed invocation, and—in projected hooks—one recognizable pending native call. The last installation refused its own returned request. Separately, reviewers identified abbreviated kind/status/supersedes guard cases being corrected. Source fixes must be followed by direct installed positive and refusal checks.
3. **A sandbox launch can need genuine host permission.** The native adapter explicitly handles non-nestable macOS sandboxing through a native escalation request. That is distinct from a Taskplane-specific reset, but its actual burden must be counted and explained. Tool discovery alone is insufficient.
4. **The rollback is broader than the repairs.** It removes later enforcement and other changes as a group. Ordinary review/help/status simplification has been reapplied in the candidate; the complete impact of every post-2.23.1 behavior has not been certified. Aggregate checks and the representative journey must expose regressions, not just improvements.

## 4. AC1–AC8: same acceptance bar

“Improved” below describes source behavior, not a completed installed acceptance criterion.

| AC | Original baseline | Candidate result so far | Missing evidence / next owner |
| --- | --- | --- | --- |
| **AC1 — usable Product entry** | Required manual harness recovery before useful completion. | **Failed on installed `21b59b8`:** activation succeeded, exact required read failed, and authoring was not reached. | Corrected candidate must complete inspect → requirement/document save without a new task or implicit Build. Recovery owner. |
| **AC2 — correct authority** | Documents and planned graph link were refused; source-write negative case was not exercised. | **Improved in source:** document allowlist, exact controls, planned graph links, proposed decisions, native read boundary. | Candidate-bound positive/negative results and a real source-write refusal. Required-operation execution must be checked, not inferred from advertised tools. Engineering/recovery owner. |
| **AC3 — finish and recover** | Ordinary release failed; exceptional user approval cleared the temporary restriction. | **Partially demonstrated live:** exact owner finish succeeded once after the read refusal; subsequent ordinary reading worked. | Complete cancel/limit and refusal–resume cases; preserve artifacts and other owners. One cleanup does not complete the entire criterion. |
| **AC4 — accurate refusal and bounded retry** | Full failure matrix was not exercised. | **Specific mismatch repaired:** quality readiness uses the execution environment; Product instructions separate integration gaps and actual defects. | Installed missing-tool, unsupported capability, stale-engine, optional-output/telemetry cases; zero retry under unchanged prerequisites. |
| **AC5 — continuity** | Same-task and second-task scenarios were not established. | **Preservation verified within stated bounds:** five prior manifests/selection metadata unchanged, 40 stage-object digests/sizes verified, old Product context archived and not selected. | Real interruption/resume and second-task knowledge reuse without active restrictions. This is not proof of all historical output-file bytes or useful knowledge retrieval. |
| **AC6 — presentation and proportional overhead** | File-panel delivery and normal completion needed recovery. | **Improved in source:** native input, indexed artifact access, text fallback, simplified ordinary entry. | Measure repair commands, approval requests, diagnostic size against the proposed 2 KiB target, and non-progress retries on the installed journey. No measurements available yet. |
| **AC7 — supported-host evidence** | Incident evidence existed for Codex; repaired cross-host acceptance did not. | **Not satisfied:** live Codex attempt failed at reading; 16 serial/parallel delivery checks passed using simulated native events. | Successful corrected installed Product and small stateless delivery journeys; Claude/Cowork remain unverified unless exercised. |
| **AC8 — visible DoR/DoD** | Initial assessment omitted gate status; requirement score did not prove review completion. | **Improved and locally tested:** requirement projection separates content, human disposition, phase review, and implementation. Blank content fails; requested changes remain actionable. | Installed output and real stage handoff must carry these distinctions. Missing review/acceptance must remain visible after a 1.0 score. |

## 5. Explicit readiness, decisions, and completion

### Product DoR for this repeat review

| Criterion | Status | Evidence / gap | Next owner |
| --- | --- | --- | --- |
| Original problem, three goals, and authorized scope are clear | **Met** | User instruction, original assessment, implementation plan. | Product preserves scope. |
| Exact historical baseline and inspected candidate are identified | **Met for last tested candidate** | Installed `21b59b8` package is identified; next corrective candidate is pending. | Recovery owner supplies the next identity and evidence. |
| Criteria and applicable risks are explicit | **Met — authored** | Same nine spaces and AC1–AC8; current source inspected. | Product maintains the same bar. |
| Required Product operations are demonstrably executable | **Failed on last installed candidate** | Exact activation-returned read was refused; authoring was not reached. Exact normal finish worked. | Recovery owner corrects and repeats the journey. |

**DoR verdict:** sufficient evidence exists for this comparative assessment. Operational Product readiness failed on the last installed candidate. The corrected candidate is pending validation. This is not a mechanical requirement score or stage-gate pass.

### Human decision

| Decision | Observed state | Meaning |
| --- | --- | --- |
| Roll back to the selected post-refactor baseline, apply fixes, and repeat Product review | **Authorized by the user** | Continue the requested work without asking again for the same permission. |
| Accept the resulting recovery and any remaining limitations | **Not yet recorded** | Approval of work to be done is not advance acceptance of its result. |
| Disposition of any earlier requirement record | **Not inferred** | This review does not overwrite, fabricate, or import a requirement sign-off. |

### Product DoD for this review and handoff

| Criterion | Status | Evidence / gap | Next owner |
| --- | --- | --- | --- |
| Repeat original goals, problem spaces, criteria, remedies, and trade-offs | **Met — authored** | Sections 2–4. | Product incorporates actual validation results. |
| State truthful before/after and remaining uncertainty | **Met — authored** | Source evidence distinguished from local and installed execution. | Product refreshes after any material fix. |
| Retain selected independent review and candidate-bound evidence | **Partially evidenced** | Local aggregate evidence and reviewer findings are recorded; corrections and selected governed Product review collection are not yet accepted. | Recovery/review owner supplies the final results. |
| Deliver accessible report with explicit gates and next owners | **Authored; delivery pending** | This readable Markdown artifact contains them. | Orchestrator delivers the final revision. |
| Record applicable human disposition | **Pending result review** | Implementation authorization exists; final result acceptance does not. | User. |
| Demonstrate normal Product completion without a stranded restriction | **Partial: cleanup demonstrated** | Last native attempt released its exact contract once and ordinary reading resumed. Full successful authoring/completion remains blocked by the read defect. | Installed journey owner. |

**Product DoD: not satisfied as a governed accepted handoff.** The comparative report is available, but final candidate evidence, applicable review completion, installed cleanup proof, and result acceptance remain distinct outstanding facts.

### Implementation acceptance

**Recovery DoD: not satisfied at this evidence checkpoint. AC1 failed on the last installed candidate.** AC1–AC8 remain the acceptance bar. Normal cleanup and local checks demonstrate progress; they cannot replace successful authoring or installed delivery. Corrections in progress need a fresh candidate-specific result. No percentage-complete estimate is supported.

## 6. Measurement and host matrix

| Measure / journey | Current result |
| --- | --- |
| Time to first useful artifact on fixed installed Product journey | Not measured. |
| Taskplane-specific user repair commands | No workaround or repair intervention in native attempt 2; first-attempt installation required user hook trust enablement. No successful normal Product path exists yet for the zero-intervention target. |
| New implementation-approval prompts during Product-only work | None reported in attempt 2; authoring was not reached. Hook trust enablement is an installation action, not implementation approval. |
| Unchanged failed retries / unnecessary polling | No unchanged retry after the read denial in attempt 2. Broader native waiting/polling efficiency is unverified. |
| Required diagnostic size | Not measured against the proposed 2 KiB target. |
| Codex installed draft–save–finish and refusal–recovery–resume | Last attempt failed at the initial read; exact normal finish and subsequent ordinary read succeeded. Full journey awaits correction and rerun. |
| Codex small stateless delivery through Evaluate, Engineering, Retro | 16 selected local checks passed with simulated native events; real installed delivery remains unverified. |
| Claude / Cowork same supported journeys | Not exercised in this review; no new support certification. |
| Same-task interruption and next-task knowledge reuse | Historical selection/artifact preservation verified within section 1's bounds. Resume and useful cross-task reuse not exercised. |

## 7. All 26 lens dispositions

This is the Product author's focused coverage ledger. “Applied” means the concern informed this report. It does not mean an independent specialist, native lens worker, or existing phase collector produced an accepted result.

| Lens | Disposition, evidence, and limitation |
| --- | --- |
| product | **Applied:** same goals, nine spaces, AC1–AC8, and explicit acceptance gaps. |
| security | **Applied:** scoped document writes, exact controls, host read sandbox, protected authority/evidence; independent negative verification still required. |
| code-quality | **Reviewed at Product boundary:** changes reuse existing owners; this author does not certify code quality, especially its own requirements changes. Engineering owns that result. |
| testability | **Applied:** observable positive/refusal pairs and distinction between simulated and installed evidence. |
| design | **Applied:** explicit readiness/completion, text fallback, retained work, and named recovery owner. |
| scalability | **Deferred:** no throughput or scale claim; native polling/performance remains unmeasured. |
| integrability | **Applied:** actual native read denial confirms the pending-call integration gap; installed package/native hook identity and quality-environment checks are recorded. |
| data-safety | **Applied:** five prior manifests/metadata and 40 current stage objects verified; old Product authority stays archived. Full historical-output byte comparison is not claimed. |
| tech-writer | **Applied:** readable criterion tables, distinct failed/unknown states, bounded next action, and plain-language limitations. |
| qa | **Applied:** same AC matrix; source/local/live evidence must stay separate. |
| devops | **Applied:** source `21b59b8` and personal installation aligned for the last probe; duplicate remote hooks removed and user trust enabled. Next corrective installation/revalidation pending. Eight structural debt violations remain visible. |
| dba | **Not applicable to implementation scope:** no database schema or migration change; project-knowledge continuity is still assessed separately. |
| sre | **Applied:** exact finish and subsequent ordinary read worked after the live read failure; no unchanged retry. Full refusal–resume/limit recovery remains unverified. |
| project-management | **Applied:** bounded rollback/fixes, preserved authorization, named evidence owners, and no implicit Build. |
| frontend | **Not applicable to UI implementation:** no frontend feature; Markdown/status presentation is covered by design/accessibility. |
| backend | **Applied at behavioral boundary:** existing CLI, scope, cleanup, readiness, and requirement owners changed. Engineering validates implementation. |
| tradeoffs | **Applied:** keep stateless consolidation and meaningful safeguards while reducing unnecessary ordinary entry work. Broad rollback losses remain a risk. |
| solution-design | **Applied:** compare bounded existing-owner repairs with pre-refactor restoration, blanket disabling, and new frameworks; rejected alternatives remain rejected. |
| services-selection | **Not applicable:** no new external service is introduced. |
| time-to-market | **Applied:** complete a representative installed journey before expanding recovery; no measured speedup claimed. |
| architecture | **Applied:** active stateless v4 preserved; native lifecycle ownership retained; no new scheduler/proof framework. Remaining wrappers are acknowledged. |
| mobile | **Not applicable:** no mobile surface or compatibility claim. |
| accessibility | **Applied:** text/file delivery and criterion-level meaning do not depend on an optional rich display. Installed presentation remains to be checked. |
| privacy-compliance | **Applied:** bounded evidence and no publication of raw host transcripts or credentials; native-call inspection remains local. No new regulatory compliance claim. |
| cost-finops | **Applied:** distinguish removed entry work from unmeasured cost/usage, polling, and supervision outcomes. |
| i18n | **Deferred:** no locale expansion; preserve valid requirement text, including surrounding whitespace. No localization certification. |

## 8. Product recommendation and evidence update

**Keep this recovery direction, correct the observed read/control defects, and repeat installed acceptance.** Restoring pre-refactor v3 behavior would undo the desired stateless architecture. The changes are closer to the original idea because cleanup now works on the tested native failure path, requirement completion no longer hides missing evidence, and ordinary inspection avoids unnecessary setup. Product still could not perform its first required read, so it is not ready to be called recovered.

The immediate next step is to finish the bounded adapter/control fixes, pin and install that candidate, and repeat Product draft–save–finish with a meaningful refusal. Then complete one small stateless governed change. Record actual user intervention and failures. Preserve the failed attempt as evidence; do not broaden its remedy into an unrestricted harness redesign.

### Evidence update slot

The recovery owner should supply the following facts for a final revision of this report:

1. Final source SHA/tree and installed engine/skill/hook identities.
2. Aggregate check commands/results and retained evidence paths, including failures.
3. Exact native host/version, Product positive and refusal/recovery outcomes, normal finish, and subsequent request result.
4. Small stateless delivery outcome, required evidence, closure, and any unavailable host capability.
5. Measured interventions, retries, diagnostic sizes, and explicit unresolved AC rows.

Until the corrective candidate's evidence arrives, the source-improvement and successful-cleanup findings stand, the last installed entry remains a recorded failure, and acceptance remains unmet.
