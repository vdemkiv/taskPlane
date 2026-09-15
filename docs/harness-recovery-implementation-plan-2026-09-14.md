# Harness recovery: implementation and acceptance plan

Date: September 14, 2026. Product/Design authoring for the recovery authorized in this task.

## 1. Decision and intended outcome

The user authorized: “proceed with rollback to suggested version and apply fixes. after rollback run the same product review with all details”. The selected baseline is **2.23.1, `a76e7de12630a1ed083f50799082bb337fe9c582`**, followed by the bounded fixes below. This is a controlled recovery candidate, not a claim that 2.23.1 was the user's last working installation. The earlier pre-refactor `1c4b63e` recommendation remains withdrawn.

Keep the active stateless phases, one v4 aggregate, bounded artifact handoffs, independent evaluation, and sealed completion evidence introduced by the consolidation. Preserve the intentional retirement of the obsolete runtime and tests. Rollback removes later changes as a group; those losses must be visible in the final comparison, and essential user-facing behavior must be checked again.

The original product goals remain:

- **Visibility:** the user can tell what is ready, what is done, what evidence is missing, and who acts next.
- **Focus:** authorized work proceeds within its scope; an unrelated source edit or unsupported completion claim is refused.
- **Continuity:** requirements, decisions, and useful artifacts survive interruption and completion without carrying active restrictions into unrelated work.
- **Lower supervision cost:** the normal journey requires no Taskplane repair command from the user and no repeated attempt under an unchanged blocking condition.

This plan implements the first recovery slice and checks a small stateless delivery journey. It does not promise that every historical problem is resolved. The [original Product assessment](harness-product-recovery-2026-09-14.md) supplies all nine problem spaces and AC1–AC8; the [corrected refactor assessment](harness-refactor-reassessment-2026-09-14.md) supplies attribution and the readiness defect. Their earlier statements that implementation was unauthorized describe the assessment stage; the user's latest instruction authorizes this recovery work.

## 2. Minimum implementation decisions

| Boundary | Required decision and owner | Protection to retain |
| --- | --- | --- |
| Baseline and installation | Restore the named source candidate with a recoverable record of the previous source and local artifacts. Resolve the actual installed launcher/engine, skills, and hooks used for validation. Keep candidate execution state separate from old active runs. The recovery owner records exact revisions and installation evidence. | Do not reinterpret a 2.24 active contract, approval, or sealed result as authority for an older engine. Do not delete unrelated task data. |
| Product authoring | Use the existing read-only-toward-code contract with an explicit allowance for the task's declared document artifacts. Correct Product instructions to request that allowance before authoring. The existing contract/screener remains the owner. | Product cannot edit implementation files, widen its own scope, or acquire Build authority by writing a requirement. |
| Repository inspection and required controls | Permit the native read path available on the host and the exact Product control operations required by its procedure: requirement creation/refinement/scoring/mode and planned graph linking. Reuse existing command identification and operation dispatch; authorize the operation, not arbitrary interpreter or shell content. | Preserve host permissions, source-write restrictions, argument/path checks, and the prohibition on fabricating host-hook events. Required control access is not permission to approve, replace, or release another authority. |
| Readiness for quality checks | Resolve/probe required quality tools in the same interpreter and environment used to execute their evidence commands. Reuse the existing execution-environment owner; preserve isolation. Missing tools produce an actionable capability result before dispatch. | A passing probe must not rely on packages that execution cannot load. Required quality evidence remains required; missing dependencies do not become a product-code defect. |
| Presentation and human input | Allow the phase's supported artifact presentation and native human-input path. Give a concise text result with links, gate verdicts, and the next owner if richer presentation is unavailable. Product instructions must not make optional rendering the only completion carrier. | Presenting a result or receiving no reply cannot imply approval. Artifact access does not permit arbitrary external publication. |
| Ordinary completion and cancellation | Make the existing normal completion/release path reachable for the current standalone Product contract, including after a named limit or refusal. Preserve artifacts and distinguish completed, cancelled, and still-unverified results. Restrict release to the authority owned by this task. | Never clear unrelated contracts, bypass a governed phase's required evidence/decision, or report a cancelled phase as successfully completed. |
| DoR/DoD | Reuse requirement readiness and current phase/completion evidence. Show separate entry and completion verdicts, with each applicable criterion's status, evidence, gap, and next owner. Preserve AC1–AC8 downstream. | A refinement score, authored report, successful command, or passing local test count cannot replace missing review, installed-host evidence, or an applicable human disposition. |

These decisions change existing owners. They do not introduce a scheduler, parallel permission system, capability registry, telemetry meter, or proof framework. Native tools retain execution, permissions, user interaction, and worker lifecycle. Taskplane retains task scope, selected inputs, attributable decisions, and evidence necessary for its own completion claims.

### Alternatives resolved

- Restoring the pre-refactor runtime would undo the stateless default and intended retirements; reject it.
- Leaving 2.24 unchanged would leave the demonstrated Product/control failures and ignore the authorized recovery; reject it for this task.
- Disabling all enforcement would lose meaningful scope and evidence checks; reject it.
- Restoring 2.23.1 alone would retain known Product and readiness defects; apply the bounded corrections before judging recovery.

No further product preference is required to start these fixes. An unavailable host capability or an inability to align the installed candidate is a factual validation gap to report, not an invitation to invent a replacement execution system.

## 3. Scope and exclusions

**Included:** the named rollback; Product document authority; required inspection/control operations; exact-environment quality readiness; text/artifact/human-input access; safe ordinary cleanup; explicit Product DoR/DoD; focused positive and negative verification; and the requested repeat of the detailed Product assessment against the resulting candidate.

**Excluded:** revival of v3 or retired tests; broad harness redesign; new scheduling/proof/usage systems; project-memory migration; restoration of every post-2.23.1 change; expanded host-support claims; and unrelated feature work. A defect discovered while validating these boundaries may justify a bounded fix in its existing owner. Broader work remains a named gap with its impact and next owner.

## 4. Acceptance and evidence matrix

All results must identify the same candidate revision or working-tree fingerprint, engine, host, and evidence type. Local tests and simulated lifecycle events are useful evidence but must be labeled separately from an installed native journey. Until the resulting candidate is exercised, every implementation outcome below is **not verified**.

| AC / user-visible outcome | Minimum evidence for this recovery | Meaningful negative case | Completion limit / owner |
| --- | --- | --- | --- |
| **AC1 — inspect, draft, and save** | A Product-only request reaches repository inspection, creates/refines one complete requirement, and saves its declared document through the candidate's supported entry. Record any setup intervention. | A missing required inspection/authoring capability is reported before restrictive activation or with a reachable recovery. | Installed-entry evidence is required to call the user journey verified. Recovery owner records it; Product judges the result. |
| **AC2 — correct document and source authority** | Declared document write and required `graph link` succeed through the real contract/control boundary; inspect the resulting artifact and planned association. | An implementation edit, an undeclared artifact target, and an unrecognized command remain refused. | Tests must exercise decisions and resulting state, not merely skill wording or whitelist membership. Engineering verifies. |
| **AC3 — finish, cancel, and remain usable** | Normal Product finish and cancel preserve artifacts and leave a subsequent request usable. Native human input and ordinary recovery remain reachable under the same restriction. | Release cannot remove another task's authority or certify a governed phase with missing required evidence. | State ownership and completion meaning both need verification; recovery owner and Engineering. |
| **AC4 — accurate blockers and bounded retries** | Exercise missing execution-tool, unavailable required capability, stale/mismatched engine, and optional presentation/telemetry cases where supported. Show cause, saved work, next action, and owner. | Ambient-only quality tools cannot produce a ready verdict when the execution environment cannot load them. Optional failure must not initiate a product FIX. | Any unexercised failure class remains explicitly unverified. No retry without a changed condition. |
| **AC5 — continuity without restriction leakage** | Same-task interruption/resume retains the selected requirement, scope, decisions, and saved artifacts. An independent task does not inherit this task's active restriction. | A foreign contract or old run's approval cannot be reused implicitly. | A unit ownership check alone does not prove cross-task project-knowledge reuse; the broader memory goal may remain open. |
| **AC6 — usable delivery and lower supervision** | Record zero Taskplane-specific repair commands requested from the user and zero new implementation-approval prompts in a normal Product-only journey. Exercise text delivery when rich rendering is unavailable. Record required diagnostic size against the existing proposed 2 KiB target. | An unavailable optional display does not suppress the result or turn a pending decision into approval. | Separate genuine host permission decisions from harness repair interventions. Report measured values or unavailable data, never estimates as measurements. |
| **AC7 — supported-host evidence** | Record exact installed candidate and host/version for each real Product positive and refusal–recovery–resume journey. Run one small stateless delivery through evaluation and final closure; retain applicable serial/parallel tests. | A real scope violation and missing sealed completion evidence are refused. | Simulated tests do not certify installed Codex, Claude, or Cowork. Claim only the host/journey actually exercised; remaining hosts stay unverified. |
| **AC8 — visible DoR and DoD** | The final Product result separately lists readiness and completion criteria, actual evidence/status, unresolved gaps, next owners, and downstream repair acceptance. | A complete requirement with missing required review or acceptance is visibly incomplete; a failed criterion cannot be hidden by a passing score. | Product owns clear presentation; existing validators and independent evaluation own the supporting verdicts. |

Focused regression checks should cover the changed behavior and its refusal counterpart. Retain valid stateless startup, serial/parallel handoff, Engineering seal, and closure checks. Add no source-text pins and restore no obsolete tests solely to recover a historical pass count. Reuse applicable unchanged checks; broaden only for an actual change, failure, or unresolved risk.

## 5. Explicit Product DoR and DoD

### Product/Design readiness for this authorized recovery

| Criterion | Status at plan authoring | Evidence / next owner |
| --- | --- | --- |
| Problem, original goals, and user authority are clear | **Met** | User's rollback/fix/review instruction; section 1. This is implementation authorization, not advance acceptance of the finished result. |
| Baseline and preserved architecture are explicit | **Met — decision** | Exact `a76e7de` baseline, active stateless runtime, sealed completion, and intended retirements. Recovery owner must still record actual restored/installed state. |
| Bounded changes, exclusions, and acceptance are reviewable | **Met — authored** | Sections 2–4 and inherited AC1–AC8. |
| Required operational paths can execute | **Not yet verified** | Authoring/control/cleanup and exact-environment readiness are the defects under repair. Do not present the installed Product path as ready until checked. |
| Validation can distinguish source, simulation, and installed behavior | **Met — plan** | Evidence matrix defines these distinctions; actual collection remains outstanding. |

**DoR verdict:** the recovery is sufficiently specified and authorized for implementation. Installed Product operational readiness remains unverified. This plan does not claim a new governed Product/Design run, contract, lens collection, or stage approval.

### Completion of the implementation and repeated Product review

| Criterion | Status at plan authoring | Evidence / next owner |
| --- | --- | --- |
| Source restoration, candidate fixes, and installed version are identified and recoverable | **Pending** | Recovery owner records previous/new state and any installation limitation. |
| Changed operations succeed and meaningful scope/authority/evidence denials remain effective | **Pending** | Engineering results tied to the actual candidate. |
| AC1–AC8 have criterion-level verdicts, evidence, and honest limitations | **Pending** | Product repeats the matrix after implementation; partial verification stays partial. |
| The same nine problem spaces are reassessed against the original goals | **Pending** | Product report shows before/after, resolved versus improved versus open, practical impact, and next owner. |
| DoR/DoD, required review gaps, and outstanding acceptance are visible | **Pending** | Final artifact and concise user summary. A successful rollback alone is not recovery DoD. |
| Useful artifacts survive and this task leaves no unintended restriction | **Pending** | Normal completion/cancellation and ownership evidence. |
| Applicable final user disposition is recorded | **Pending review of the result** | User owns acceptance of the finished recovery. The existing implementation instruction requires no repeat approval to do the authorized work. |

**DoD verdict:** not satisfied at plan authoring. The repeat Product review must say whether the goals are met or measurably closer, including any regressions introduced by returning to 2.23.1. If installed acceptance cannot be completed, deliver the implemented candidate, available evidence, and the exact remaining limitation without certifying recovery.
