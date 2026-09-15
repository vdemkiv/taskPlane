# Taskplane recovery: repeat Product review

Date: September 14, 2026. Branch: `codex/restore-2.23.1-harness-recovery`.

**Current verdict: the source candidate is materially closer to the original Product goals. Installed recovery acceptance is not yet established.** The repairs address identifiable causes of the earlier Product failures while preserving the active stateless runtime. The most important remaining proof is a real installed draft–save–finish journey, followed by a small governed change. Passing source checks cannot establish either result.

This repeats the [original Product assessment](harness-product-recovery-2026-09-14.md) against the [authorized recovery plan](harness-recovery-implementation-plan-2026-09-14.md). The user authorized rollback, identified fixes, and this repeat review. That instruction authorizes the work; it does not pre-accept its eventual result. No new requirement, delivery run, contract, approval, or receipt was created for this review.

## 1. Candidate and evidence boundaries

| Item | Inspected state / evidence |
| --- | --- |
| Earlier assessed source | `7fb20110aeb51d1a088ec3c8f257b641736996ee`, installed 2.24.0 at the original assessment. |
| Selected recovery source | 2.23.1, `a76e7de12630a1ed083f50799082bb337fe9c582`, preserving the consolidation and stateless default. This was not certified as the user's last working installation. |
| Restoration commit | `b2a8811` plus the current repairs on the recovery branch. |
| Final repaired commit / tree | **Awaiting the recovery owner's final SHA or working-tree fingerprint.** This source review inspected the assembled working tree; new changes require a relevant evidence update. |
| Installed engine / skill / hook identity | **Awaiting actual installation evidence.** A source branch is not an installed plugin identity. |
| Confirmed local validation available to this author | Requirements module: **49 tests and 49 subtests passed**; focused Ruff and whitespace checks passed. This verifies the modified readiness/projection behavior locally, not installed Product entry or complete recovery. |
| Aggregate candidate checks | **Awaiting the recovery owner's results and evidence paths.** Earlier historical snapshot passes are comparison evidence, not results for this candidate. |
| Installed native journeys | **Awaiting actual journey evidence.** No synthetic event, local test, source inspection, or author ledger is counted as a live installed journey. |

The Product reviewer inspected the current source and instructions directly. This author also implemented the requirement gate presentation and its blank-content correction; this report is not an independent Engineering certification of that component. The 26-lens ledger below is a disclosed Product assessment, not proof that 26 specialists ran.

## 2. The same original goals

| Original goal | Earlier experience | Candidate change | Current assessment |
| --- | --- | --- | --- |
| **Visibility** | A readiness score could coexist with unusable operations; DoR/DoD were absent from the review; completion and approval were easy to confuse. | Requirement Markdown and score/show results project separate content DoR, recorded human decision, Product DoD, and implementation acceptance. Blank content fails readiness; requested changes are visible as `changes_required`; missing review remains unknown. Product instructions require operational evidence and next owners. | **Improved in source and focused local checks.** Installed presentation and the full phase handoff remain to be demonstrated. |
| **Focus** | Product's contract blocked its documents, planned graph link, presentation, and ordinary exit. Repairing the harness displaced the requested work. | Product has explicit document authority, required installed-engine controls, planned-only graph linking, proposed-only decision drafts, permitted artifact presentation, and an owner-specific finish. Ordinary source Review, Help, and Status use native tools without automatic activation. | **Improved in source.** Negative checks and installed execution must establish that legitimate work proceeds while actual scope, authority, and evidence violations still stop. |
| **Continuity** | Artifacts survived only after an explicitly approved cleanup; blank session context and private knowledge/session coupling raised reuse concerns. | Normal Product finish retains artifacts and decisions, preserves other owners, and grants no Build authority. Stateless inputs and existing knowledge ownership remain. | **Partially improved.** Cleanup ownership is more explicit. Same-task resume and cross-task knowledge retrieval without inherited restrictions remain unverified; the memory architecture was not repaired here. |

Lower supervision cost is the test of these goals in use. The normal path should require zero Taskplane-specific user repair commands and zero unchanged retries. We have not yet measured elapsed time, real repair interventions, repeated polling, or cost on the installed candidate.

## 3. The same nine problem spaces

| Priority / problem space | Before | Candidate evidence and improvement | Remaining gap / next owner |
| --- | --- | --- | --- |
| **P0 — DoR/DoD disappear** | The original review lacked explicit gate verdicts; scoring and completion were conflated. | `requirements.product_gate_summary`, readable requirement projection, `req score`/`req show`, and the Product procedure expose definitions, criteria, evidence, gaps, and owners. Shared scoring now rejects empty statements and NFR values. | **Improved.** Requirement records cannot validate stage-review or implementation evidence. Product must consume actual stage evidence when available; installed delivery must show the same distinctions. |
| **P0 — Phase obstructs its own work** | Read-only authority permitted neither the expected documents nor required controls. | `cmd_new --product` grants `docs/**`, `specs/**`, and `knowledge/**`; exact controls admit planned graph linking; the Codex read adapter requests the native read-only sandbox. | **Improved, operational acceptance open.** Entry checks declared tool compatibility/runtime discovery, not successful execution. The installed read, document, graph, and finish path must actually work. Recovery owner. |
| **P0 — Recovery becomes the task** | Ordinary release and its suggested remedy were blocked; the user had to authorize a special repair. | Exact `clear --task-id` is scoped to the current standalone Product owner; replacement/foreign/worker/active-delivery authority is preserved, including guarded cleanup. Presentation and human input remain reachable at limits. | **Improved.** Exercise finish, cancel, refusal–recovery–resume, and a subsequent request under actual hooks. A successful direct function call alone cannot close this problem. |
| **P1 — Enforcement exceeds useful role** | Native work was wrapped in broad prerequisites and repeated proof requirements. | Ordinary source Review, Help, and Status avoid automatic onboarding/contracts. Product uses the host's sandbox and existing domain checks. No replacement scheduler is added. | **Partially improved.** Product still relies on exact command shape and pending native-call matching. Delivery retains lifecycle/receipt/telemetry controls. Their proportionality and installed compatibility remain open; do not declare native duplication eliminated. |
| **P1 — Failures collapse into “blocked”** | Unavailable evaluation or integration capability could resemble a product defect and trigger wasted repair. | Quality probes and evidence execution share `evidence_command_environment`; ambient-only Python tools no longer justify readiness. Product instructions distinguish capability, content, execution, optional output, and acceptance. | **Improved for the demonstrated environment mismatch.** Stale-engine, optional-telemetry, unsupported host, and recovery cases still need the declared scenario evidence. Evaluate must not turn missing dependencies into a source-code FIX. |
| **P1 — Cost controls do not show value** | Repeated setup, large context, checks, and recovery consumed effort without demonstrated benefit. | Reduced ordinary-entry instructions and no automatic Review activation remove work from common requests. Product instructs one score/link sequence and no retry without a changed condition. | **Unmeasured.** Native waiting/polling behavior is not repaired or benchmarked by these changes. The existing emitted wait policy still governs delivery. Record time to artifact, interventions, non-progress retries, and unavailable usage honestly. |
| **P1 — Session isolation threatens knowledge continuity** | New blank context and a new session-qualified requirement raised concern about project-memory reuse. | Source restoration preserves stateless selected inputs; normal finish preserves authored artifacts. No new migration or replacement store is introduced. | **Open beyond cleanup.** The knowledge/session boundary remains unchanged. Run interruption/resume and a second-task retrieval journey; verify provenance and no inherited restrictions. Recovery owner, then Product. |
| **P1 — Readiness/visibility mislead** | Setup reported ready while required work failed; optional presentation blocked delivery. | Content readiness explicitly disclaims operational readiness. Text/file links suffice; real failures become failed/changes-required states, while missing evidence stays pending/not-verified. | **Improved.** Runtime discovery cannot establish supported native execution. The nested sandbox and fixed pending-call adapter need real-host results, including any genuine host approval required. |
| **P1 — Release evidence misses the journey** | Large test counts coexisted with incomplete installed acceptance. | Recovery has a criterion-level matrix, narrow behavioral refusals, retained stateless checks, and explicit source/simulation/installed evidence distinctions. | **Still open until exercised.** Installation and complete native journeys remain the release evidence boundary. No cross-host success or successful governed delivery is inferred here. |

### Specific remaining product risks

1. **Standalone specialist review is not yet demonstrated.** The Product skill calls for a focused route and selected specialist work. That instruction and this author ledger do not prove dispatch, admissible collection, candidate binding, or Product phase completion. Reuse the existing stage mechanisms where applicable; do not invent a receipt to close the gap.
2. **Native command matching remains a narrow integration.** Codex read/control admission depends on the expected outer shell, workspace, fixed invocation, and—in projected hooks—one recognizable pending native call. Ambiguous or differently shaped host records remain unsupported. A real journey must show that this restriction does not recreate the prior fight with the harness.
3. **A sandbox launch can need genuine host permission.** The native adapter explicitly handles non-nestable macOS sandboxing through a native escalation request. That is distinct from a Taskplane-specific reset, but its actual burden must be counted and explained. Tool discovery alone is insufficient.
4. **The rollback is broader than the repairs.** It removes later enforcement and other changes as a group. Ordinary review/help/status simplification has been reapplied in the candidate; the complete impact of every post-2.23.1 behavior has not been certified. Aggregate checks and the representative journey must expose regressions, not just improvements.

## 4. AC1–AC8: same acceptance bar

“Improved” below describes source behavior, not a completed installed acceptance criterion.

| AC | Original baseline | Candidate result so far | Missing evidence / next owner |
| --- | --- | --- | --- |
| **AC1 — usable Product entry** | Required manual harness recovery before useful completion. | **Improved, not verified end to end:** direct Product entry, readable source reuse, one requirement sequence, explicit document scope. | Installed request → inspect → requirement/document save without new task or implicit Build. Recovery owner. |
| **AC2 — correct authority** | Documents and planned graph link were refused; source-write negative case was not exercised. | **Improved in source:** document allowlist, exact controls, planned graph links, proposed decisions, native read boundary. | Candidate-bound positive/negative results and a real source-write refusal. Required-operation execution must be checked, not inferred from advertised tools. Engineering/recovery owner. |
| **AC3 — finish and recover** | Ordinary release failed; exceptional user approval cleared the temporary restriction. | **Improved in source:** exact owner finish, retained artifacts, no new Build authority, guarded protection against a replacement owner. | Installed finish and cancel at normal/limit conditions; subsequent request works; other owners remain protected. |
| **AC4 — accurate refusal and bounded retry** | Full failure matrix was not exercised. | **Specific mismatch repaired:** quality readiness uses the execution environment; Product instructions separate integration gaps and actual defects. | Installed missing-tool, unsupported capability, stale-engine, optional-output/telemetry cases; zero retry under unchanged prerequisites. |
| **AC5 — continuity** | Same-task and second-task scenarios were not established. | **Partly improved:** cleanup preserves stored work and protects other authority. Storage/knowledge architecture is unchanged. | Real interruption/resume and second-task knowledge reuse without active restrictions. Remains open. |
| **AC6 — presentation and proportional overhead** | File-panel delivery and normal completion needed recovery. | **Improved in source:** native input, indexed artifact access, text fallback, simplified ordinary entry. | Measure repair commands, approval requests, diagnostic size against the proposed 2 KiB target, and non-progress retries on the installed journey. No measurements available yet. |
| **AC7 — supported-host evidence** | Incident evidence existed for Codex; repaired cross-host acceptance did not. | **Not satisfied yet:** source and local checks are explicitly separated from live acceptance. | Exact installed Codex journey plus small stateless Build/Evaluate/Engineering/Retro journey; Claude/Cowork remain unverified unless actually exercised. |
| **AC8 — visible DoR/DoD** | Initial assessment omitted gate status; requirement score did not prove review completion. | **Improved and locally tested:** requirement projection separates content, human disposition, phase review, and implementation. Blank content fails; requested changes remain actionable. | Installed output and real stage handoff must carry these distinctions. Missing review/acceptance must remain visible after a 1.0 score. |

## 5. Explicit readiness, decisions, and completion

### Product DoR for this repeat review

| Criterion | Status | Evidence / gap | Next owner |
| --- | --- | --- | --- |
| Original problem, three goals, and authorized scope are clear | **Met** | User instruction, original assessment, implementation plan. | Product preserves scope. |
| Exact historical baseline and inspected candidate are identified | **Partially met** | Pinned 2.23.1 and restoration commit; final repaired SHA/installation identity pending. | Recovery owner supplies final identity. |
| Criteria and applicable risks are explicit | **Met — authored** | Same nine spaces and AC1–AC8; current source inspected. | Product maintains the same bar. |
| Required Product operations are demonstrably executable | **Not verified** | Source compatibility checks and local tests do not establish installed operations. | Recovery owner performs the journey. |

**DoR verdict:** sufficient evidence exists to make this bounded comparative Product assessment. Operational readiness of the installed candidate is not yet established. This is not a mechanical Product requirement score or stage-gate pass.

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
| Retain selected independent review and candidate-bound evidence | **Not verified in this report** | Author ledger is disclosed; aggregate Engineering evidence awaits linkage. | Recovery/review owner supplies actual results. |
| Deliver accessible report with explicit gates and next owners | **Authored; delivery pending** | This readable Markdown artifact contains them. | Orchestrator delivers the final revision. |
| Record applicable human disposition | **Pending result review** | Implementation authorization exists; final result acceptance does not. | User. |
| Demonstrate normal Product completion without a stranded restriction | **Not verified** | No new contract was created for this review; that is not a test of repaired normal cleanup. | Installed journey owner. |

**Product DoD: not satisfied as a governed accepted handoff.** The comparative report is available, but final candidate evidence, applicable review completion, installed cleanup proof, and result acceptance remain distinct outstanding facts.

### Implementation acceptance

**Recovery DoD: not satisfied at this evidence checkpoint.** AC1–AC8 remain the acceptance bar. Local repairs and source improvement are real progress; they cannot replace installed-host evidence. A real failed criterion must be marked failed. A missing measurement or unexecuted host journey stays not verified. No percentage-complete estimate is supported.

## 6. Measurement and host matrix

| Measure / journey | Current result |
| --- | --- |
| Time to first useful artifact on fixed installed Product journey | Not measured. |
| Taskplane-specific user repair commands | Not measured; normal-path target remains zero. |
| New implementation-approval prompts during Product-only work | Not measured; target remains zero. Genuine host permission decisions are recorded separately. |
| Unchanged failed retries / unnecessary polling | Not measured; zero unchanged retries remains the target. Native waiting efficiency is unverified. |
| Required diagnostic size | Not measured against the proposed 2 KiB target. |
| Codex installed draft–save–finish and refusal–recovery–resume | Awaiting actual evidence. |
| Codex small stateless delivery through Evaluate, Engineering, Retro | Awaiting actual evidence. |
| Claude / Cowork same supported journeys | Not exercised in this review; no new support certification. |
| Same-task interruption and next-task knowledge reuse | Not exercised; no continuity certification. |

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
| integrability | **Applied:** exact native-call projection, nested sandbox, engine/skill/hook identity, and environment consistency are material acceptance boundaries. |
| data-safety | **Applied:** preserve prior artifacts and other task authority; no old-run authority migration assumed. |
| tech-writer | **Applied:** readable criterion tables, distinct failed/unknown states, bounded next action, and plain-language limitations. |
| qa | **Applied:** same AC matrix; source/local/live evidence must stay separate. |
| devops | **Applied:** final candidate identity and installation alignment are pending acceptance evidence. |
| dba | **Not applicable to implementation scope:** no database schema or migration change; project-knowledge continuity is still assessed separately. |
| sre | **Applied:** reachable finish, retained artifacts, accurate capability failure, and no unchanged retry. Installed failure recovery remains unverified. |
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

**Keep this recovery direction and complete the installed acceptance checks.** Restoring pre-refactor v3 behavior would undo the desired stateless architecture. The candidate's changes are closer to the original idea because Product can request the operations it actually needs, completion no longer hides missing evidence, and ordinary inspection avoids unnecessary setup.

The immediate next step is evidence collection, not another harness framework: pin and install the candidate, execute Product draft–save–finish with a meaningful refusal, then complete one small stateless governed change. Record actual user intervention and failures. Correct a demonstrated blocking defect in its current owner; do not expand into an unrestricted harness redesign.

### Evidence update slot

The recovery owner should supply the following facts for a final revision of this report:

1. Final source SHA/tree and installed engine/skill/hook identities.
2. Aggregate check commands/results and retained evidence paths, including failures.
3. Exact native host/version, Product positive and refusal/recovery outcomes, normal finish, and subsequent request result.
4. Small stateless delivery outcome, required evidence, closure, and any unavailable host capability.
5. Measured interventions, retries, diagnostic sizes, and explicit unresolved AC rows.

Until that evidence arrives, this report's source-improvement verdict stands, and its installed acceptance and completion gaps remain open.
