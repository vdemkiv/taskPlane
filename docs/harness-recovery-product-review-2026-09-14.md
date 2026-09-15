# Taskplane recovery: final Product review

Started September 14, 2026; final review September 15, 2026. Branch: `codex/restore-2.23.1-harness-recovery`.

**Verdict: materially closer to the original goals. The core installed Codex Product journey now works, including a real source-write refusal and normal cleanup. Broader recovery acceptance remains partial.** A fresh native session also recovered the saved requirement, report, constraints, and gate states without inheriting an active restriction. Full native governed delivery, selected specialist review collection, interruption recovery, and the other supported hosts remain unverified.

This repeats the [original Product assessment](harness-product-recovery-2026-09-14.md) against the [authorized recovery plan](harness-recovery-implementation-plan-2026-09-14.md): the same three goals, nine problem spaces, AC1–AC8, and 26 lens dispositions. The user's instruction authorized rollback, fixes, and this review. It does not imply acceptance of the resulting implementation or approval of the probe's draft requirement. This report created no runtime authority.

## 1. Candidate and evidence

| Item | Verified or recorded state |
| --- | --- |
| Original assessment | Source `7fb20110aeb51d1a088ec3c8f257b641736996ee`; installed 2.24.0. |
| Selected recovery baseline | 2.23.1, `a76e7de12630a1ed083f50799082bb337fe9c582`, preserving the active stateless runtime and intended retirements. This is not claimed to be the user's last working installation. |
| Restoration | Commit `b2a8811`, followed by the bounded recovery fixes. |
| Final code | `6ada3f44a772d98641bb341c3d199f84bedb4c55`; tree `da27a23f854e479cf5b3068ec01d1bcccf547019`. |
| Installed plugin | Personal `2.23.1+codex.20260915033421`; 261 installed files checked with zero mismatches apart from the official manifest cachebuster. The older remote installation was removed to avoid duplicate hooks. The user enabled the new hook trust before the final journeys. |
| Export | [Recovery package](../exports/taskplane-recovery-6ada3f4/taskplane-2.23.1-openai.zip), SHA-256 `034b944e98e9e40bb50387395191edf93986e183b49ce2a41f1a70f71497cd0c`. [Provenance](../exports/taskplane-recovery-6ada3f4/taskplane-2.23.1-openai.zip.provenance.json) verifies source identity, not complete CI. |
| Native host | Codex CLI `0.154.0-alpha.6.2`; final Product session `01a0a321-7c26-7852-abea-1433839b1838`. |
| Final Product probe | **Passed the bounded installed journey:** strict/live activation with native receipt; native reads, scoped document writes, requirement creation, score/show, planned graph link; one source-write denial; exact finish; retained artifacts and subsequent ordinary reading. |
| Fresh-session retrieval | **Passed the bounded read-only check** in 79.65s: zero contracts, same requirement/report/source recovered, useful constraints and gate states understood, no activation, file changes, or repair. |

Primary evidence: [final native outcome](../.taskplane/diagnostics/recovery-2026-09-14/native-canary-final.txt), [native measurements](../.taskplane/diagnostics/recovery-2026-09-14/native-canary-measurements.json), [independent post-finish verification](../.taskplane/diagnostics/recovery-2026-09-14/native-independent-verification.json), and [fresh-session retrieval](../.taskplane/diagnostics/recovery-2026-09-14/continuity-final.txt). Retained copies of the [native Product report](../.taskplane/diagnostics/recovery-2026-09-14/native-saved-product-review.md) and [requirement R-0001](../.taskplane/diagnostics/recovery-2026-09-14/native-saved-requirement-R-0001.md) preserve the delivered content.

The Product reviewer inspected candidate source and instructions directly, and also implemented the requirement presentation/blank-content correction. This report therefore does not independently certify that component's code. Independent Engineering review and local checks have their own evidence; the 26-lens ledger below is the Product author's assessment, not 26 executed specialists.

### What the final native journey established

The probe created **its own project-local draft R-0001**, titled “Explain app.py VALUE in one short README paragraph.” Its score was 1.0 and content DoR passed. This identifier is qualified by the probe's knowledge store; it does not replace a historical R-0001 or authorize that proposed README change.

Exactly one native `apply_patch` attempt against source was refused by Taskplane and was not retried. `app.py` and README remained unchanged. Exact normal finish of `task_d81cce9a` succeeded, no active contract remained, and the report, requirement, and graph survived. Ordinary reading then worked. Independent filesystem/Git verification confirmed the source and retained artifacts.

There were **zero unexpected Taskplane denials, zero Taskplane repair interventions, and zero unchanged Taskplane-denial retries** in the final probe. Native sandbox escalation requests succeeded; there were no new trust prompts or approval-review rejections. This does not mean that no native permission requests occurred.

The fresh retrieval session began **after contract clearance while the producer was still finishing its final summary**. It recovered the existing R-0001, report, source, functional constraints, and honest pending gates. It proves useful saved-context retrieval without restriction leakage in that scenario. It does not prove producer-exit handling, interrupted-task recovery, automatic knowledge reconciliation, or full project-memory correctness.

### Earlier failures remain evidence

| Attempt | Actual result | Attribution and disposition |
| --- | --- | --- |
| 1, before hook trust | Stopped before Product entry. | Installation trust was absent; the user enabled it. This is not a completed Product journey or a new code-regression finding. |
| 2, `21b59b8` | Strict/native-effective activation; returned read refused; exact finish and ordinary read worked. Requirement/report not reached. | Fresh authentic rollout had an exact session UUID/header but no session-index row. The adapter's lookup did not handle that case. Source comparison was unavailable because `/usr/local/bin/git` had the wrong CPU architecture. |
| 3, `75329fa` | Read again refused; exact finish, ordinary read, and source-integrity comparison passed. | Hook execution lacked `CODEX_THREAD_ID`; native binding was corrected. An initial overly broad `/private` filename search returned no paths and was not repeated. |
| Development corrections | Native read and finish worked after intervention. Engineering found unconditional native binding affected ordinary work; it was narrowed to active read-only commands. | Useful repair evidence, not clean installed acceptance. Guard and explicit-refusal/replay corrections received focused regression checks. |
| 4, `578e1f5` | Reads, scoped report save, exact finish, source/README integrity and ordinary reading worked. Real `req new` was refused before execution; no R-id or source probe. | Canonical quoting comparison rejected ordinary double-quoted prose containing semicolons. Fixed by bounded literal parsing. Probe total recorded as 351.9s. The earlier “301.38s to first artifact” used a saved-file modification time, **not proof of the first write**. |
| 5, `6ada3f4` | Full bounded Product journey passed, including real refusal and normal finish. | Current installed acceptance evidence for this one Codex journey. Earlier failures are preserved and not retroactively counted as passes. |

[Attempt 2](../.taskplane/diagnostics/recovery-2026-09-14/native-attempt-2-read-blocked/native-canary-final.txt), [attempt 3](../.taskplane/diagnostics/recovery-2026-09-14/native-attempt-3-read-blocked/native-canary-final.txt), and [attempt 4](../.taskplane/diagnostics/recovery-2026-09-14/native-attempt-4-quoted-control-blocked/native-canary-final.txt) remain available. The Git architecture issue reproduced on baseline `a76e7de`; it is an environment limitation. Applicable native/developer policy constraints and hook trust are distinguished from the actual Taskplane defects.

### Local checks and their limits

| Evidence | Result and limit |
| --- | --- |
| Requirements checks | 49 tests and 49 subtests passed, including blank content, sign-off refusal, amendment invalidation, and honest readiness/completion presentation. |
| Earlier source selection | 396 tests and 274 subtests passed, with five failures. Five stale assertions were corrected and those five passed on focused rerun. |
| Earlier host selection | 92 tests and 45 subtests passed, one Go skip; six failed subcases within one hook test reproduced on the baseline with incompatible Intel Git. The corrected fixture's exact test and eight subtests passed. |
| Final identity/control checks | Explicit native-identity block/public-boundary/replay: 145 tests and eight subtests passed before the final control fix. Final literal-control/recovery selection: 92 tests passed; nine actual-shell samples matched intended parsing. These overlapping runs are **not summed**. |
| Stateless runtime | 16 selected serial/parallel Evaluate–Engineering–Retro and missing-seal/handoff checks passed. Native events are simulated; no real installed delivery completion is inferred. |
| Static/release checks | Final mypy passed across 114 source files; source policy checked 129 modules with zero violations. Earlier Ruff, version, release-surface, and generated-CLI checks passed, the latter after correcting its temporary-directory environment. No complete release-CI result is claimed. |
| Structural debt | Eight metric-debt categories also exist on `a76e7de`. Baseline production/test lines and test files were 138,185 / 75,457 / 235; an earlier repaired candidate reported 138,474 / 76,458 / 240. Existing debt and some growth remain visible; the categories were not introduced by these fixes. |

See [component results](../.taskplane/diagnostics/recovery-2026-09-14/literal-candidate-checks.json), [stateless results](../.taskplane/diagnostics/recovery-2026-09-14/stateless-delivery.json), and [static/debt results](../.taskplane/diagnostics/recovery-2026-09-14/static-results.json). Original nonzero results remain alongside their corrected reruns.

The workspace locator was recovered through a fresh inert preflight. [Storage verification](../.taskplane/diagnostics/recovery-2026-09-14/storage-verification.json) preserves all five earlier manifests and selection metadata and verifies all 40 current stage-object digests/sizes. The old Product context stays archived and was not selected as new authority. This is not a byte comparison of every historical output file.

## 2. The same three original goals

| Goal | Original assessment | Final evidence and verdict |
| --- | --- | --- |
| **Visibility** | DoR/DoD were absent from the review; scoring, readiness, approval, and completion were confused. | **Materially improved and demonstrated:** saved report and requirement expose content DoR, human disposition, Product review completion, and implementation acceptance separately. Score 1.0 coexists honestly with pending human decision and unverified review/implementation. The fresh session recovered those distinctions. |
| **Focus** | Required Product operations and normal exit were blocked; operating the harness displaced the task. | **Core journey demonstrated:** reads, documents, requirement controls, planned graph link, and normal finish work under strict live enforcement. A real source-write probe is refused and source stays unchanged. Ordinary Review/Help/Status also avoid unnecessary automatic activation by design. |
| **Continuity** | Saved work required exceptional cleanup; isolation and blank context raised knowledge-reuse concerns. | **Narrow continuity demonstrated:** artifacts survive normal finish, the next native session sees no active restriction and retrieves useful prior constraints. Historical selected artifacts are preserved. Interruption recovery and broader automatic project-memory reuse remain open. |

The final journey required no Taskplane-specific repair and no unchanged refusal retry. **No speedup or cost saving is claimed:** timing sources disagree, and full native delivery has not been measured.

## 3. The same nine problem spaces

| Priority / problem space | Before | What changed and what was proved | Remaining outcome / next owner |
| --- | --- | --- | --- |
| **P0 — DoR/DoD disappear** | Initial review lacked explicit gates; a score suggested more completion than it established. | Shared requirement projection and Product instructions expose definitions, criterion evidence/status/gaps/owners. Blank content fails. Real saved output and fresh retrieval retain pending review, human, and implementation states despite score 1.0. | **Core visibility repaired.** Carry real selected review and completion evidence through the full governed phase handoff. Product and stage-review owner. |
| **P0 — Phase obstructs its own work** | Documents, native reads, planned graph link, and presentation were refused. | Explicit document authority, native read admission, exact quoted controls and planned graph linking all worked in the final installed journey. | **Demonstrated for this Codex path.** Unsupported-capability and other host versions remain unverified. Integration owner. |
| **P0 — Recovery becomes the task** | Ordinary release and its remedy failed; special user intervention was needed. | Exact owner finish succeeded after earlier failures and after successful final authoring/refusal. Artifacts remain; subsequent reading works; no active contract leaked into fresh retrieval. | **Normal finish repaired.** Cancellation, configured-limit and interrupted-phase recovery need their own native cases. Existing lifecycle owner. |
| **P1 — Enforcement exceeds useful role** | Native work accumulated broad setup/proof prerequisites. | Ordinary source Review/Help/Status use native tools without automatic contracts. Product retains scoped documents, exact domain controls, real host sandboxing and evidence boundaries. A genuine source violation is refused. | **Partially improved.** Native-call matching and delivery receipt/telemetry machinery remain. Judge their value in real delivery; do not claim all duplication removed. Architecture/integration owner. |
| **P1 — Failures collapse into “blocked”** | Capability gaps could resemble source defects and trigger wasted fixes. | Quality probes use the evidence-execution environment. Native identity failures return explicit blocks. The final scope refusal is distinguished from unexpected failures, and host/environment issues are attributed separately. | **Specific defects repaired.** Complete the stale-engine, missing-tool, optional-telemetry and unsupported-host scenario matrix. Evaluate/integration owner. |
| **P1 — Cost controls do not show value** | Setup, checks and recovery consumed effort without measured benefit. | Final probe has zero Taskplane repair interventions, unexpected denials, and unchanged denial retries. Common entry instructions are shorter and avoid needless activation. | **Supervision improved in this sample; speed/cost unknown.** Resolve timing instrumentation and measure native waiting/polling and a complete delivery. Recovery owner. |
| **P1 — Session isolation threatens continuity** | New session context and requirement numbering obscured prior knowledge. | A fresh native session retrieved the same requirement/report and useful constraints after clearance, with zero active contracts and no repair. Historical selection/artifact references verified. | **Narrow reuse proved.** Same-task interruption, producer exit, broader knowledge discovery/reconciliation and multi-project isolation remain open. Knowledge/lifecycle owner. |
| **P1 — Readiness/visibility mislead** | Setup could say ready while the next operation failed; optional display blocked delivery. | Final required operations actually succeeded; readable text/files carry the result. Content readiness remains distinct from review completion and implementation. Earlier false operational confidence remains recorded as failure. | **Repaired for the exercised path.** Tool discovery alone still cannot certify every supported host or required operation. Product/integration owner. |
| **P1 — Release evidence misses the journey** | Large pass counts coexisted with incomplete native acceptance. | A source-identified installed journey now includes positive work, a real refusal, integrity checks, cleanup and fresh retrieval. Local, simulated, and native evidence are explicitly separated. | **Materially closer, acceptance partial.** Real stateless delivery and the complete supported-host matrix remain missing. Release/review owner. |

## 4. AC1–AC8: the original acceptance bar

A demonstrated subjourney does not close a broader criterion whose remaining cases were not exercised.

| AC | Earlier baseline | Final candidate result | Remaining gap / owner |
| --- | --- | --- | --- |
| **AC1 — usable Product entry** | Required manual harness repair before completion. | **Demonstrated in the bounded Codex probe:** inspect, save requirement/report, score/show, planned link, and finish; no new conversation needed to repair it, no implicit Build. | Full specialist Product phase acceptance is separate from this core operation check. Product review owner. |
| **AC2 — correct authority** | Required documents/link failed; source-negative case absent. | **Core authority demonstrated:** declared native document write succeeds; exactly one source patch refused; source/README unchanged; planned graph link succeeds. | Missing-capability detection before activation and broader guard/host cases have local evidence but not a full native scenario matrix. Integration/Engineering. |
| **AC3 — finish and recover** | Ordinary release failed; exceptional approval was required. | **Normal finish demonstrated:** exact owner cleared once, artifacts retained, no active contract, subsequent ordinary read and fresh-session retrieval work. Expected refusal did not strand the session. | Explicit cancel, configured-limit and interruption/recovery cases remain unverified natively. Lifecycle owner. |
| **AC4 — accurate refusal and bounded retry** | Failure classes and retries not established. | **Partial:** expected scope refusal clearly observed; zero unexpected denials/unchanged retries in final probe. Readiness/execution environment and identity refusals have focused fixes/checks. | Stale engine, missing execution dependency, optional telemetry and unsupported-capability native cases remain open. Integration/Evaluate. |
| **AC5 — continuity** | Resume and next-task reuse unproved. | **Narrow fresh-session reuse demonstrated:** same draft R-0001, report, source and functional/gate context recovered without activation or file changes. Historical manifests/artifact references preserved. | Session began after clear while producer summary continued; no producer-exit, interruption-recovery, or full memory claim. Knowledge/lifecycle owner. |
| **AC6 — usable presentation and proportional overhead** | Presentation and completion required recovery. | **Core presentation/supervision demonstrated:** readable files/text, zero Taskplane repairs, zero unchanged refusal retries, no new trust prompt or approval rejection. Full observed denial block 588 bytes, below 2 KiB for this sample. Native sandbox permission requests succeeded. | Timing disagrees; no speed/cost improvement or global diagnostic bound. Optional rich-panel failure and wider measurements remain open. Product/recovery owner. |
| **AC7 — supported-host evidence** | Repaired cross-host journey absent. | **Partial:** one real installed Codex Product positive/refusal/finish journey and fresh retrieval passed. Sixteen stateless local checks passed with simulated native events. | Real installed small Build–Evaluate–Engineering–Retro and Claude/Cowork journeys remain unverified. Release owner. |
| **AC8 — visible DoR/DoD** | Initial review omitted gates; score did not prove completion. | **Demonstrated in saved native output/retrieval:** score 1.0 and content DoR pass coexist with pending human decision/Product DoD and unverified phase review/implementation. Criteria, evidence, gaps and owners are retained. | Required specialist collection and real downstream gates must retain the same distinctions. Product/stage owner. |

## 5. Explicit readiness, decisions, and completion

### Content DoR

| Criterion | Status | Evidence / next owner |
| --- | --- | --- |
| Recovery problem, original goals and authority are explicit | **Met** | Original assessment, authorized plan and this repeat review. Product preserves scope. |
| Final source, installation and evidence provenance are identified | **Met** | `6ada3f4`, verified package/installation, named native sessions and diagnostic artifacts. |
| Functional criteria and applicable risks are reviewable | **Met for this assessment** | Same nine spaces and AC1–AC8; remaining gaps named. |
| Probe requirement's mechanical content readiness | **Passed** | Probe-local R-0001 score 1.0, no reported content gaps. This does not approve the requirement or certify implementation. |

### Operational DoR

| Required operation | Status | Actual evidence / limit |
| --- | --- | --- |
| Native source inspection | **Passed in final installed probe** | Exact native read and bounded source reads succeeded under strict/live authority. |
| Declared document authoring | **Passed** | Native scoped writes saved a readable report; retained copy and digest verified. |
| Requirement/graph controls | **Passed** | R-0001 creation, score/show and planned graph link succeeded. |
| Meaningful source protection | **Passed for the one designated probe** | Exactly one native source patch refused; source/README unchanged. |
| Normal finish and subsequent access | **Passed** | Exact owner clear, no active contract, retained artifacts and subsequent reads. |
| Every supported host and recovery condition | **Not verified** | One Codex host/version and bounded scenario do not establish the wider matrix. |

**DoR verdict:** content and required operational entry are demonstrated for the final bounded Codex journey. This does not generalize to unexecuted host/recovery cases.

### Human decision

| Decision | Observed state | Meaning |
| --- | --- | --- |
| Rollback, fixes and repeat Product review | **Authorized by the user** | Work proceeded under that existing instruction. |
| Accept the finished recovery and its limitations | **Not recorded** | No future acceptance was inferred from implementation authorization. |
| Approve the probe's draft R-0001 | **Pending** | No attributed Product approval, Build authority or self-signoff was created. |

### Product DoD

| Criterion | Status | Evidence / gap / next owner |
| --- | --- | --- |
| Complete this comparative Product assessment | **Met — authored** | Original goals, all problem spaces, same ACs, 26 dispositions, before/after, evidence limits and priorities are present. |
| Make report/requirement accessible with explicit gates | **Demonstrated for the native probe** | Saved report/requirement and fresh-session retrieval preserve the distinctions; this final report is also saved. Orchestrator delivers it. |
| Preserve artifacts and finish normally | **Demonstrated** | Exact clearance, no active contract, retained report/requirement/graph and ordinary access verified. |
| Required selected specialist review collected by the real phase owner | **Not verified** | Author ledger and local checks do not supply that evidence. Product/stage-review owner. |
| Applicable human acceptance of Product handoff | **Pending** | User disposition has not been recorded. |

**Product DoD verdict:** the requested comparative report is complete as an authored assessment. A fully governed, specialist-reviewed and human-accepted Product phase is **not established**. The native draft remains correctly pending, even though the operation probe passed.

### Implementation acceptance

**Core Product repair is demonstrated; broader recovery DoD is partial.** The unchanged AC1–AC8 bar includes cases still open in section 4. Full native delivery, specialist collection, all supported hosts and broader continuity are not certified. The probe requirement's proposed README implementation is also unverified and was not performed. Passing the canary is neither release-wide acceptance nor approval to implement that draft.

## 6. Measurement and supported journeys

| Measure / journey | Observed result and limit |
| --- | --- |
| First saved report write | Successful native AddFile result at `2026-09-15T03:38:41.633Z`, **245.91 wall seconds** after start `03:34:35.721478Z`. This is a write observation, not a measured speedup. |
| Total Product probe duration | Wrapper elapsed **801.44s**; UTC wall interval **1,869.15s**. These disagree. Two host websocket/I/O retry warnings were observed, but the whole discrepancy is not attributed to them or Taskplane. No speed claim. |
| Unexpected Taskplane denials | **0** in the final probe. The one deliberate source-write refusal is expected. |
| Taskplane repair interventions / unchanged denial retries | **0 / 0** in the final probe. Earlier failed attempts and development intervention remain documented. |
| Trust/permission behavior | No new trust prompt or approval-review rejection during the final journey. Native sandbox escalation requests succeeded; permission requests were not absent. |
| Diagnostic size | Actual complete source-denial error block, including echoed patch: **588 bytes**. Meets the proposed 2 KiB target for this sample only; no global quota or universal bound is established. |
| Native waiting/polling and usage cost | Not evaluated as a complete delivery workload. No cost-saving or polling-efficiency claim. |
| Installed Codex Product journey | **Passed** for the identified candidate/session/host version. |
| Fresh native saved-context retrieval | **Passed**, 79.65s, after clearance while producer summary continued. Narrow continuity only. |
| Real installed stateless delivery | **Not verified.** Sixteen local checks use simulated native events. |
| Claude / Cowork equivalent journeys | **Not verified.** No new cross-host certification. |
| Same-task interruption / producer-exit recovery | **Not verified.** Fresh retrieval does not substitute for either. |

Measurements and their limitations are retained in [native measurements](../.taskplane/diagnostics/recovery-2026-09-14/native-canary-measurements.json) and [continuity results](../.taskplane/diagnostics/recovery-2026-09-14/continuity-result.json).

## 7. All 26 lens dispositions

This is a disclosed Product author ledger. “Applied” means the concern informed this assessment; it does not imply an independent native specialist ran or an existing phase collector accepted its output.

| Lens | Disposition, evidence, and limitation |
| --- | --- |
| product | **Applied:** same three goals, nine spaces, AC1–AC8 and honest partial acceptance. |
| security | **Applied:** real source-write refusal, unchanged source, bounded document/control authority, native sandbox permissions; wider attack/host matrix remains separate. |
| code-quality | **Reviewed at Product boundary:** existing owners reused; component and independent review evidence retained. This author's own code is not independently certified by this ledger. |
| testability | **Applied:** observable positive/refusal/finish checks and explicit simulated/native distinction. |
| design | **Applied:** visible gate meaning, readable saved artifacts, normal exit and actionable unresolved states. |
| scalability | **Deferred:** no throughput/scale claim; native delivery polling/performance unmeasured. |
| integrability | **Applied:** real rollout/hook/quoting defects were found and corrected; final installed path passed. Other hosts and shapes remain unverified. |
| data-safety | **Applied:** source/README unchanged, five prior manifests/metadata and 40 stage objects verified, old authority archived, artifacts survive normal finish. |
| tech-writer | **Applied:** definitions, criterion evidence/gaps/owners, readable reports and a measured 588-byte refusal sample. |
| qa | **Applied:** native canary plus independent integrity verification; failures and reruns retained; broader acceptance gaps explicit. |
| devops | **Applied:** final source/package/installed identity verified; prior duplicate installation removed; eight pre-existing structural debt categories remain visible. |
| dba | **Not applicable to implementation scope:** no database migration/schema change. Knowledge continuity is assessed separately. |
| sre | **Applied:** normal finish and post-refusal continuation work; no unchanged denial retry. Limit/cancel/interruption cases remain unverified. |
| project-management | **Applied:** authorized bounded recovery completed through repeat review; same ACs retained; no implicit Build or invented acceptance. |
| frontend | **Not applicable to UI implementation:** no frontend feature; report presentation covered by design/accessibility. |
| backend | **Applied at behavior boundary:** requirements, controls, readiness, native admission and cleanup exercised. Engineering evidence owns implementation soundness. |
| tradeoffs | **Applied:** preserve stateless consolidation and real guards while simplifying ordinary work. Broad rollback losses and remaining wrappers are acknowledged. |
| solution-design | **Applied:** bounded repairs in existing owners; no restoration of v3, blanket disablement, new scheduler, or proof framework. |
| services-selection | **Not applicable:** no new external service introduced. |
| time-to-market | **Applied:** core usable journey now demonstrated. Timing disagreement prevents a speedup claim; next work should close named gaps. |
| architecture | **Applied:** active stateless v4 and sealed evidence retained; native execution/lifecycle ownership preserved; full native delivery still unverified. |
| mobile | **Not applicable:** no mobile surface or compatibility claim. |
| accessibility | **Applied:** real text/file delivery and fresh retrieval carry gate meaning without requiring rich display. Optional panel failure matrix remains open. |
| privacy-compliance | **Applied:** report uses bounded evidence, not published raw transcripts/credentials; no new regulatory compliance claim. |
| cost-finops | **Applied:** zero repair/retry in final sample; elapsed-clock discrepancy and unmeasured native delivery/usage prevent savings claims. |
| i18n | **Deferred:** no locale expansion; valid requirement text preserved. No localization certification. |

## 8. Recommendation and remaining priorities

**Keep the installed post-refactor recovery candidate.** The original user-facing failure class is substantially reduced: Product can inspect, author, record requirements, show honest gates, refuse a real scope violation, finish, and leave useful context for a fresh session. Restoring the retired pre-refactor runtime would undo the stateless architecture without evidence of a better result.

The next priorities are bounded and evidence-driven:

1. **Complete one real stateless governed change** through Evaluate, Engineering, sign-off and Retro, with current evidence and the existing applicable human decisions. Owner: delivery/review. Local simulated checks are insufficient.
2. **Demonstrate selected Product specialist dispatch/collection and accepted handoff.** Owner: Product/stage review. Do not turn this author ledger or score 1.0 into a receipt.
3. **Exercise cancellation, configured limits, interruption and producer exit.** Owner: lifecycle/integration. Preserve the observed normal-cleanup behavior and foreign-owner protection.
4. **Validate remaining supported hosts and failure classes.** Owner: release/integration. Start with the actually supported host/version matrix, stale installation, unavailable quality tools and optional output/telemetry behavior.
5. **Measure complete work and broader knowledge reuse.** Owner: Product/knowledge. Resolve timing instrumentation, inspect native waiting/polling, and verify useful project-context retrieval across tasks and interruption before claiming speed, cost or full memory recovery.

No new scheduler, permission system, telemetry meter or proof framework is required by this recommendation. This review's final conclusion is **core Product recovery demonstrated, original goals materially closer, broader acceptance partial**.
