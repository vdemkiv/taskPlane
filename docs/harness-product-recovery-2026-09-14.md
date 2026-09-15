# Taskplane harness recovery: Product assessment

Historical assessment or plan: see the [repeat Product review](harness-recovery-product-review-2026-09-14.md) for the implemented recovery and actual verification outcomes.

Date: 2026-09-14 (America/New_York). Source baseline: `7fb20110aeb51d1a088ec3c8f257b641736996ee`. Installed engine: 2.24.0.

**Recommendation, corrected after precise refactor review:** retain the active stateless runtime and intended retirements in `146104c`. The recommendation to restore pre-refactor `1c4b63e` is withdrawn: it retained the old loop by default, and the original task records serious harness failures before it. Repair the demonstrated Product capability/completion mismatch and assess later enforcement changes independently. See [the corrected reassessment](harness-refactor-reassessment-2026-09-14.md). Passing old tests and deletion counts do not establish the right product behavior or a safe recovery baseline.

Status: Product assessment and proposed recovery scope. No implementation, Design approval, Build authorization, release, or live cross-host acceptance is claimed. The temporary Product contract created during this assessment was released with the user's explicit approval. The first-slice requirement is `R-0001` in this task's session store; that identifier does not supersede historical requirements with the same number.

**DoR / DoD correction following user review:** the initial report omitted an explicit gate assessment. Its statement that the assessment was complete referred to authored material; it did not establish governed Product completion. The user requested changes. This revision adds the missing definitions, evidence, owners, and actual statuses, and makes their disappearance from the user experience a P0 problem.

| Gate | Current result | Meaning |
| --- | --- | --- |
| Product requirement DoR (content readiness) | **PASS — mechanical check** | R-0001 has the fields checked by the current engine; its refinement score is 1.0. This does not validate execution capability, review completion, or implementation. |
| Product execution DoR (ability to perform the phase) | **FAILED on the exercised governed path** | Required document authority, graph linking, and ordinary cleanup were unavailable under the prescribed contract. Authorized recovery let the assessment continue; it did not fix that path. |
| Product phase DoD (reviewed, accepted handoff) | **NOT SATISFIED** | Revised artifacts exist. Governed Product lens-evidence collection and human acceptance of the revised result have not been recorded. |
| Repair DoD (implemented result) | **NOT VERIFIED** | No repair candidate has been built or evaluated against AC1–AC8. |

These labels distinguish the current engine result from this report's broader product assessment. They do not claim new runtime checks were implemented.

## 1. The original idea

The v1.0 README at commit `1416908` describes three outcomes:

1. **Visibility:** know what agents are doing, what is complete, and what needs a decision.
2. **Focus:** agents stay within the requested task and avoid accidental destructive actions.
3. **Continuity:** preserve decisions, requirements, debt, and progress so subsequent work starts informed.

It explicitly describes scope guardrails and gates as the means of delivering those outcomes. It also acknowledges that command screening is a guard against ordinary drift and that a plugin cannot fully contain arbitrary execution or intercept the model's own calls.

[Requirements at the core](requirements-core.md) adds the economic premise: better functional and non-functional requirements should reduce implementation iterations. Its recorded refinement policy is advisory by default, with hard blocking for high-cost or irreversible work.

These outcomes provide the recovery criteria. Improvements worth retaining include stateless phases with explicit artifact handoffs, dependency-aware planning, independent validation, reusable evidence, session isolation, and the 2.24.0 simplification of ordinary source review. The user explicitly prefers to keep stateless phases.

**Proposed product statement:** Taskplane helps a person turn an intent into scoped, verifiable work while keeping progress and material decisions clear, preserving knowledge, and reducing the effort spent supervising agents.

## 2. What this assessment established

### Observed in this task

The installed Product procedure was exercised in the current checkout:

| Observation | Product consequence |
| --- | --- |
| Onboarding reported `ready: true`, while product, current-state, stack, and workflow context contained empty templates. | Setup readiness does not establish useful product context. |
| Repository preparation initially failed because its run binding required writes beneath `.git/taskplane/sessions/…`. Native permission approval allowed it to proceed. | Product evidence gathering required an additional storage permission before producing its result. |
| The Product skill's prescribed activation reported `writable: (nothing — reads only)`. | The phase could record requirements through a permitted engine operation, but its contract did not permit the specification files it expected the agent to author. |
| The required nested native read-only sandbox failed to start on macOS. It worked after a native permission escalation. | Compatibility reported at entry did not establish that the declared read path would execute. This is an integration observation, not proof that all sandboxing should be removed. |
| The hook refused a documentation-only replacement contract and ordinary contract release. | The remedy suggested by the refusal was itself inaccessible through the normal path. |
| `req new` succeeded and `req score` returned 1.0, no gaps, and `product_dor_passed: true`. The next prescribed `graph link` was blocked. | Requirement completeness and workflow executability are different facts. A green score must not imply that the next operation works. |
| Opening the generated requirement in a Codex file panel was also blocked. | Artifact production and artifact delivery are not consistently supported by the active phase contract. |
| Explicit user approval permitted release of this task's temporary contract. | Recovery existed, but it consumed an extra human intervention to finish an otherwise authorized Product request. |

The graph-link refusal has a matching source explanation: [the Product skill](../skills/tp-product/SKILL.md) requires that operation, while `_READONLY_CONTROL_ACTIONS` in [tp.py](../taskplane/tp.py) includes other graph operations but omits `link`. Its Product activation example omits a documentation write allowance.

These observations concern this installed Codex Product path. They do not establish a failure rate across all users or prove current Claude/Cowork behavior. Several diagnostic attempts, including repeated ordinary release attempts, added effort without advancing the product result. They are investigation overhead, not successful recovery.

### Historical evidence and its limits

- [August evaluation retrospective](retrospective-2026-08-15-evaluation-loop.md): unavailable model/host evidence became product blockers and unnecessary FIX cycles. The document records a correction; it establishes a recurring failure class, not proof that this exact defect remains today.
- [September initialization incident](https://github.com/vdemkiv/taskPlane/blob/7fb20110aeb51d1a088ec3c8f257b641736996ee/docs/incidents/2026-09-13-entry-initialization-overengineering.md): a startup repair expanded into a duplicate transport, permissions, and receipt system before the draft was removed.
- [September review-entry incident](https://github.com/vdemkiv/taskPlane/blob/7fb20110aeb51d1a088ec3c8f257b641736996ee/docs/incidents/2026-09-14-review-entry-recovery.md): read-only enforcement blocked engine controls, human input, and presentation. Local tests and simulated host checks were explicitly distinguished from live-host evidence.
- [2.24.0 review simplification](https://github.com/vdemkiv/taskPlane/blob/7fb20110aeb51d1a088ec3c8f257b641736996ee/docs/remediation/2026-09-14-claude-review-simplification.md): ordinary source review now avoids automatic delivery onboarding and contracts. This is a useful implemented improvement. Product and explicit delivery retain the stricter path.
- [Previous harness refactor specification](../specs/harness-refactor/pre-pickup-spec.md): already calls for bounded retries, reusable phases, accurate recovery, single ownership, and genuine native journeys. These are unfinished or unevenly realized product promises; adding another larger specification alone will not resolve them.

## 3. Problem spaces and required outcomes

| Priority / space | Diagnosis | Required outcome |
| --- | --- | --- |
| **P0 — DoR and DoD disappear from the user experience** | My original report exposed a score and acceptance list without an explicit entry/completion verdict. Existing definitions are distributed across requirements, phase validation, and role instructions. A user cannot reliably tell what is ready, what is done, what evidence is missing, or who must act. | Every phase presents its applicable Definition of Ready and Definition of Done, criterion-level evidence and status, unresolved gaps, and next owner. Restore visible, meaningful gates through the existing validators and reports. A score, completed tool call, or authored file cannot stand in for a completion verdict. |
| **P0 — The phase obstructs its own work** | Product's required operations and its admitted operations disagree. Broad activation precedes proof that authoring, delivery, and exit work. | Validate the phase's required capabilities together. Permit declared Product artifacts and necessary controls while preserving implementation restrictions. No success-ready claim when a required operation is unusable. |
| **P0 — Recovery becomes the task** | A refusal can block its own remedy or completion. Repeated errors require the user to understand internal contracts and recovery commands. | One bounded explanation: affected operation, actual reason, saved work, next action, and next owner. Resume, inspection, cancellation, human input, and lawful completion remain reachable. Retry only after a named condition changes. |
| **P1 — Enforcement exceeds its useful role** | Hook readiness, host proof, resource telemetry, and evidence machinery can become broad prerequisites for otherwise legitimate work. The original limits of a local plugin become harder to see. | Keep real scope and authorization safeguards. Use the host's existing permissions and execution capabilities. Apply Taskplane gates at the operations whose integrity they protect. Document the guarantee each check actually supplies. |
| **P1 — Different failures collapse into “blocked”** | Historical incidents show unavailable evaluation represented as defective product code. Current refusal text similarly obscures whether the issue is product, integration, policy, or presentation. | Distinguish a product defect, missing required evidence, unavailable host capability, optional diagnostic, and human decision. Each has an appropriate continuation. Missing mandatory proof still prevents the claim that requires it; optional failure does not trigger a code repair. |
| **P1 — Cost controls do not demonstrate value** | The original economic goal is fewer iterations. Repeated setup, oversized context, checks, and recovery spend effort before useful work. A refinement forecast of zero fix cycles is a heuristic, not a delivery prediction validated by this assessment. | Measure time to first artifact, user repair interventions, repeated operations, and completed work. Reuse applicable evidence. Report native usage categories and unavailable measurements honestly. Avoid introducing another meter to enforce these product targets. |
| **P1 — Session isolation threatens knowledge continuity** | This session received blank context and a new `R-0001`. Its private knowledge path sits below a session-specific home. The storage code explicitly shares the private knowledge and execution home. | Preserve runtime isolation and stable project knowledge as separate needs. Explicitly select or reconcile relevant knowledge, preserve provenance, and make missing context visible. A second-task journey must prove reuse without inheriting active restrictions. Existing knowledge loss was not established here. |
| **P1 — Readiness and visibility are misleading** | The system can say ready while its next required action fails. Optional display mechanisms can block delivery. | Report separate facts: requirement drafted, evidence available, operation executable, approval pending, and result verified. Always provide a concise text result and next owner. Rich presentation supports the work without becoming its completion condition. |
| **P1 — Release evidence misses the user's journey** | Repeated incident reports contain substantial passing test counts alongside incomplete native journeys. Individual owners can pass while entry, hooks, tools, storage, and exit disagree. | Validate the installed artifact through real supported user journeys, including a permitted operation and a meaningful denial. Record simulated, package, and live-host evidence separately. A missing live journey remains explicitly unverified. |

### The causal pattern

An integration failure creates a new control or proof requirement. That control introduces more states and compatibility assumptions. Another failure adds a recovery exception. Tests validate the new rule locally, while the installed end-to-end journey remains incomplete. The user becomes the operator of the harness.

The corrective product rule is simple: **each retained gate must name the user harm it prevents, the operation it protects, the evidence it uses, and a recovery path that works under that same gate.** Apply this as a review discipline in the existing workflow. It does not require a new registry or policy engine.

## 4. What should remain strict

| Situation | Required behavior |
| --- | --- |
| An agent attempts a source change outside its approved scope | Refuse that change and explain the scope decision needed. |
| A worker attempts to approve its own work or change the meaning of a human decision | Refuse the transition. |
| Completion relies on missing, stale, contradictory, or failed required evidence | Preserve the evidence gap and block the corresponding completion claim. |
| A host cannot safely perform a required operation | Stop that operation, preserve work, and expose the supported recovery or limitation. |
| Optional visualization or optional usage data is unavailable | Continue applicable work and label the unavailable information. |
| A user asks for Product analysis only | Deliver Product artifacts and finish without initiating Build. |

Taskplane retains requirements, dependency intent, saved evidence, and meaningful workflow decisions. The host retains native execution, permissions, and worker lifecycle, consistent with [the existing ownership decision](loop-design.md) and [harness guidance](https://github.com/vdemkiv/taskPlane/blob/7fb20110aeb51d1a088ec3c8f257b641736996ee/docs/harness-build.md). Any proposed change to a previously accepted strictness or ownership policy requires an explicit superseding decision during Design; this assessment creates no such approval.

## 5. Recovery sequence

### First decision — retain the consolidation and repair demonstrated failures

The user recalled a working governed flow roughly two weeks before September 14 and asked when the last major revert occurred. Git history establishes:

- **September 5, 2026, 17:27:40 Eastern:** [PR #18](https://github.com/vdemkiv/taskPlane/pull/18) merged as `1cf41e9673bce86ccb8e77517c20e9a6b1332e74`.
- The rollback commit was `88759129642c9dbf56c05be5fd0012cb566b5412`, authored at 17:17:14 Eastern. It restored **2.19.0** from `8d02d856a77134026f05e83ef4ddcdd714354814` and backed out the later stateless phase-pickup rewrite.
- Compared with the pre-rollback main tree, it changed **47 files: 1,167 insertions and 9,030 deletions**.
- The merge's tree and the named baseline's tree both resolve to `284b5c1cb777a3c1b95f2b59849a3e2a803d3af5`. This verifies an exact source restoration.
- [The restart specification](../specs/r0005-restart/spec.md) records that this was an unreleased candidate. Successful source restoration and passing PR checks do not establish a working installed journey.
- Later development resumed through the September 9 restart merges and September 10 harness refactor. The September 5 restoration therefore predates substantial changes in the current 2.24.0 source.

The user's approximate date does not identify their installed version conclusively. The [historical check](harness-baseline-check-2026-09-14.md) initially recommended September 9 `1c4b63e`, relying too heavily on passing tests and the presence of stateless components. The user's correction prompted examination of the original decisions, retirement map and normal execution path. The [reassessment](harness-refactor-reassessment-2026-09-14.md) withdraws that recommendation: `146104c` activates the stateless runtime and removes the competing old loop as requested. Separate September 11 changes further tightened enforcement.

The older candidates retain the standalone Product document-authority mismatch and reintroduce retired runtime machinery. They are historical comparison points, not recommended recovery baselines. If a historical enforcement comparison is useful, post-refactor 2.23.1 `a76e7de` is more relevant; it remains unverified as an installed restoration target.

| Option | When it is the quicker recovery | Condition before choosing it |
| --- | --- | --- |
| Retain the refactor and repair the current version in bounded slices | The demonstrated Product mismatches have specific current owners, while wholesale rollback restores unwanted duplication | Use slice 1 below; then demonstrate installed Product and a small stateless Build journey, visible DoR/DoD, correct scope refusal and clean completion. |
| Compare a post-refactor snapshot before later enforcement tightening | A particular later policy change is suspected and a controlled comparison can isolate its effect | Compare exact revisions and required user journeys. Do not infer usability from fewer tests or controls, and do not restore retired v3 behavior. |

**DoR for a restoration decision:** pin exact source and matching plugin/hooks, preserve current source and run artifacts, isolate candidate runtime state, state the required workflow and accepted losses, and identify host compatibility. Do not feed current run state into an older engine without verified compatibility.

**DoD for baseline validation:** one representative governed change completes through visible readiness, scoped execution, evidence-backed completion, and normal cleanup. A genuine scope violation is refused. Any missing host evidence or failed criterion stays visible. If those checks pass, present a concrete restoration and selective-fix proposal; their success does not itself authorize changing main or replacing the installed plugin.

No checkout, installation, or history was reverted during this assessment. R-0001 remains the proposed forward-repair requirement; the user has not selected a rollback or authorized either implementation path.

### Slice 1 — Product can inspect, draft, save, and finish

**Scope:** Product entry, document authority, required control operations, explicit DoR/DoD reporting, presentation fallback, and normal phase cleanup. Reuse current components. Resolve the observed mismatch without widening Product into implementation.

The saved first-slice requirement has eight acceptance criteria:

1. Reach evidence inspection and save a complete requirement through the installed entry without a delivery loop, new conversation, or manual harness repair.
2. Allow declared Product artifacts and refuse an implementation edit. Entry detects an unusable required authoring or finish operation before restrictive activation.
3. Finish or cancel Product without implicit Build or a leftover restriction on the next request. Human input and appropriate recovery remain reachable.
4. Classify capability, authoring, stale-engine, and optional-telemetry failures accurately; do not retry unchanged conditions or initiate a product FIX without a product defect.
5. Preserve this request's requirement, repository, scope, and decisions across interruption; do not inherit another session's active restrictions.
6. Require zero Taskplane-specific repair commands from the user and zero implementation-approval prompts in a normal Product-only journey. Provide text delivery when rich presentation is unavailable. Keep required diagnostic output within the proposed 2 KiB acceptance target.
7. Verify real installed draft–save–finish and refusal–recovery–resume journeys on every host version claimed supported, separately labeling simulated evidence.
8. Present Product DoR and Product DoD separately, with each criterion's evidence, status, unresolved gap, and next owner. Preserve the repair's AC1–AC8 as its downstream DoD. Missing required review evidence or acceptance must remain visible even when refinement scores 1.0; it must not be presented as a completed, approved Product phase.

The zero-intervention and output-size values are proposed release acceptance targets, not measured current performance or new runtime quotas. Broader project-memory restructuring, budget redesign, and Build changes are excluded from this slice.

### Slice 2 — One small governed change completes

Use an existing small, reversible requirement through Plan, scoped implementation, independent evaluation, and final sign-off. Preserve applicable approval already granted by the user. Demonstrate that a real scope violation and a real failing criterion stop the appropriate action while legitimate work reaches completion.

Carry explicit phase DoR and DoD through that journey. Product acceptance criteria must remain traceable into Design, Plan, evaluation, and final sign-off, with evidence for the current candidate. A phase cannot silently substitute its own narrower completion checklist for the user's acceptance criteria.

Measure repair interventions, duplicated evidence work, and unnecessary approval requests. This slice defines and reviews any necessary changes to existing strictness policy. Product slice success alone does not prove Build works.

### Slice 3 — Interruption and the next task preserve value

Resume a partially completed change without recreating its work. Start a second task that retrieves the first task's requirement and decisions while inheriting none of its active restrictions. Resolve the private project-memory/session boundary with explicit compatibility and rollback. Do not silently copy old approvals into new authority.

### Slice 4 — Expand supported journeys and retire redundant controls

Extend verified behavior across declared hosts and more complex workflows. Remove redundant checks and obsolete instructions in the existing owners. Record per-host acceptance evidence. Expansion follows demonstrated utility; parallel phases, new adapters, and additional proof infrastructure are not prerequisites for the first slice.

## 6. Measurement and validation

Use existing traces and native observations where available; use a bounded manual journey record where they are unavailable.

| Measure | Definition / initial target |
| --- | --- |
| Time to first useful artifact | Time from user request to saved draft, plan, or evidence-backed finding. Establish a baseline on a fixed journey before setting a time target. |
| User repair interventions | Taskplane-specific permission, reset, reload, manual command, or fresh-task requests needed to complete the journey. Target zero on the normal supported path; distinguish genuine host policy decisions. |
| Non-progress retries | Repeated failed operations with unchanged relevant inputs and prerequisites. Target zero. |
| Meaningful protection | The designated scope violation and unsupported completion claim are refused. Both must remain effective. |
| Continuity | Same-run resume preserves completed work; a new task recalls accepted project knowledge without inherited restrictions. |
| Completion | All declared artifacts are accessible and the phase has no leftover restriction. Requirement scoring alone does not satisfy this measure. |

Run focused checks for each changed boundary and one installed journey per declared host. Reuse unchanged applicable CI evidence. Broaden testing when new failures, changes, or unresolved risks justify it. Do not use suite counts as a substitute for the journey.

## 7. Definition of Ready and Definition of Done

**Definition of Ready (DoR)** states what must be true before a specific phase or operation can begin. **Definition of Done (DoD)** states what evidence is required before its result can be accepted as complete. Every definition must name its phase and boundary. Product authors the repair's acceptance criteria; Engineering later verifies the implemented repair against them.

### What exists in the current implementation

- `product_dor()` in [requirements.py](../taskplane/requirements.py) checks requirement completeness, unresolved functional questions, and required risk statements. Its returned dependency and contract lists do not prove all dependency or contract questions have been resolved.
- `_validate_product_gate()` in [gates.py](../taskplane/gates.py) contains explicit PM DoR/DoD behavior: require a specification or attached requirement, check an attached requirement's DoR, and link its context into the graph.
- [The Product phase definition](../agents/spec-phase-definitions.json) declares a requirement and lens evidence as required outputs, domain validation, and a human gate.
- [The Product skill](../skills/tp-product/SKILL.md) explicitly says a scored requirement is ready for review and requires a human decision for the handoff. It also says not to run delivery `dod` for standalone Product refinement. That command distinction does not remove the need to define and report Product completion.

Therefore, this assessment establishes a missing and fragmented user-visible gate assessment. It does not establish that all DoR/DoD code has been deleted or that every current phase bypasses its checks.

### Product DoR: entry and requirement readiness

| Criterion | Evidence in this assessment | Status | Gap owner / next action |
| --- | --- | --- | --- |
| User problem, intended outcome, and Product-only authority are stated | Original user request; original-intent reconstruction in section 1; explicit first-slice exclusions | **PASS** | Product preserves this scope. |
| Relevant source, baseline, incidents, and limits are identified | Commit, engine, current observations, and attributed historical evidence in section 2 | **PASS** | Product must label new evidence against its own baseline. |
| Functional behavior, applicable NFRs, and falsifiable acceptance criteria are recorded | R-0001 and AC1–AC8; current mechanical content check | **PASS — content** | Product maintains the same requirement as it is refined. |
| Dependency and contract intent is recorded | Planned requirement/module edges and three named boundaries | **PASS — declared intent** | Design must verify concrete owners, versions, current graph coverage, and compatibility; those checks remain outstanding. |
| Required phase operations are available within declared authority | Document writes, graph link, presentation, and cleanup were refused under the prescribed contract | **FAIL — governed entry** | Harness recovery slice 1 must prove these operations and a meaningful out-of-scope refusal through the installed entry. |

**Verdict:** the requirement passes the existing content DoR. The exercised governed Product entry fails operational readiness. Subsequent authorized cleanup permitted document work but does not retroactively turn that entry into a pass.

### Product DoD: completion of this Product phase

| Criterion | Evidence / current state | Status | Gap owner / next action |
| --- | --- | --- | --- |
| Explain original value, observed drift, and prioritized problem spaces | Sections 1–4, including the DoR/DoD visibility failure | **PASS — authored** | Product responds to substantive review changes. |
| Save a bounded requirement with explicit exclusions and verification criteria | Same R-0001, first-slice scope, AC1–AC8, and repair DoD table below | **PASS — authored** | Product keeps roadmap context separate from first-slice implementation scope. |
| Record dependencies, boundaries, and next-phase questions | Planned graph link and handoff section | **PASS — Product declaration** | Design owns concrete architecture, compatibility, and validation decisions. |
| Complete the required Product review and retain admissible evidence | The coverage ledger is the author's assessment; no governed Product lens-evidence collection is claimed | **NOT VERIFIED** | The Product review owner must supply and validate the selected review evidence before governed completion is asserted. |
| Present DoR, DoD, gaps, and actual next owner to the user | This corrected section and the accompanying response | **CORRECTED IN THIS REVISION** | User may review the revised content; absence of a reply is not acceptance. |
| Record the applicable human disposition and preserve scope | User requested changes; no approval of the revised requirement exists | **PENDING REVIEW / ACCEPTANCE** | The user decides whether this revision satisfies the Product request. The earlier contract-release approval covers cleanup only. |
| Retain artifacts and leave no phase restriction blocking later work | Saved report and requirement; explicitly approved release of the temporary contract | **PASS — recovery performed** | Slice 1 must make ordinary completion work without this recovery intervention. |

**Verdict: Product DoD is not satisfied.** Authored deliverables are available for review; governed review evidence and applicable acceptance are still outstanding. Neither an overall 1.0 score nor successful cleanup supplies them. No Product stage terminalization is claimed.

### Repair DoD: how the eventual first-slice implementation is accepted

All eight criteria apply to the same repair candidate. Product defines the observable outcome; Design/Plan must select exact executable checks and host versions; independent evaluation consumes their results. A broad suite result alone cannot close a row.

| Criterion | Required evidence | Current baseline / repair status |
| --- | --- | --- |
| AC1: usable Product entry | Installed request reaches evidence inspection and saved requirement without a new task, delivery loop, or manual harness repair | Baseline required repair. **Repair not verified.** |
| AC2: correct phase authority | Supported documentation write succeeds; implementation write is refused; missing authoring/finish capability is detected before activation | Baseline document authority failed. Negative source-write case was not exercised. **Repair not verified.** |
| AC3: finish and recover | Normal finish/cancel, human input, bounded recovery, and configured-limit case preserve artifacts and leave the next request usable | Baseline ordinary release was refused; explicit recovery succeeded. **Repair not verified.** |
| AC4: meaningful refusal and bounded retries | Each named capability/authoring/stale-engine/optional-telemetry case reports its distinct reason and reachable action; unchanged retry and spurious FIX do not occur | Full scenario matrix was not exercised. **Repair not verified.** |
| AC5: continuity | Interrupt/resume preserves requirement, selected scope, and applicable decisions; another session does not inherit restrictions | Full scenario matrix was not exercised. **Repair not verified.** |
| AC6: usable presentation and proportional overhead | Normal Product path has zero Taskplane repair commands and zero implementation-approval prompts; text fallback works; required diagnostics satisfy the proposed size target | Baseline required user recovery and blocked file-panel delivery. **Repair not verified.** |
| AC7: supported-host evidence | Real installed positive and recovery journeys for every claimed host/version, with simulation distinctly labeled | This task supplies incident evidence on Codex, not a repaired cross-host acceptance run. **Repair not verified.** |
| AC8: visible DoR and DoD | Product output includes definitions, criterion evidence/status, unresolved owners, and separate approval/implementation states; a missing review result remains incomplete despite a 1.0 content score | This document corrects the report. The installed workflow behavior is unchanged. **Repair not verified.** |

**Verdict: repair DoD is not met.** No implementation has started. These are acceptance obligations, not completed checks.

### Gate continuity across governed development

This is the product-level behavior the recovery roadmap must restore. It maps existing responsibilities; it does not add another transition engine or a mandatory human approval at every row. Slice 1 covers Product. Slice 2 verifies the remaining applicable phase boundaries on a small governed change.

| Phase | DoR: ready to begin when | DoD: complete when | Completion owner |
| --- | --- | --- | --- |
| Product | Problem, authority, relevant inputs, and required authoring operations are available | Bounded requirement, acceptance criteria, dependencies/contracts, required review evidence, and applicable human disposition are retained | Product authors; existing validator checks; user owns the decision. |
| Design, when required | Refined requirement, declared dependencies/contracts, current source/graph baseline, and design authority are available | Approach, alternatives, proposed graph/contracts, AC-to-validation map, risks, rollout, and required design evidence form a reviewable handoff | Design authors; existing gate checks; approval follows the applicable consolidated policy. |
| Plan | Applicable Product/Design outputs and unresolved constraints are understood | Every acceptance criterion maps to owned tasks and evidence, graph/dependency scope is checked, and implementation authority is established | Planner and existing gate; user owns applicable implementation authorization. |
| Build / Fix | Task dependencies, approved scope, allowed operations, and validation obligations are ready | In-scope changes and declared checks are complete, with candidate-bound evidence submitted | Builder submits; existing gate validates submission. |
| Evaluate | Current candidate and required tests, acceptance criteria, graph impact, and contracts are available | Each criterion and affected boundary has an evidence-backed result; real defects, unavailable capability, and missing proof are classified separately | Independent evaluator and existing gate. |
| Engineering / sign-off | Integrated candidate, evaluation results, and required graph/contract conformance are available | Review and unresolved findings are presented against the full requirement, with applicable human disposition | Engineering recommends; user signs off. |
| Retro / close | Applicable sign-off or explicit stopped/aborted disposition is recorded | Actual outcome, outstanding work, useful lessons, retained evidence, and owned cleanup are recorded | Existing closure/retro owner. |

Every user-visible phase transition should answer: **what was required, what evidence exists, what passed or failed, what remains, and who acts next.** Reuse the existing gate results and acceptance criteria to provide these answers.

## 8. Product handoff

- **Mode:** full for the bounded first slice. The mode helper used refinement 1.0 and a provisional 12-file estimate. Twelve is a sizing input, not an inspected implementation plan or a file limit.
- **DoR:** the requirement's content check passed. Its original zero-fix forecast is heuristic. Operational readiness failed on the governed path. The graph link was completed after the user-approved release of the temporary contract; that recovery does not establish a repaired workflow.
- **DoD:** authored Product deliverables are available, but required governed review evidence and human acceptance of this revision remain outstanding. The repair's AC1–AC8 remain unverified. See section 7 for criterion-level evidence and owners.
- **Dependencies:** no prior R-record is imported as an approved prerequisite. The new session's R-0001 is qualified by its store path. Existing ownership decisions, the native-review improvement, and relevant history remain constraints and evidence. Each later slice depends on demonstrated outcomes of its predecessor.
- **Boundaries:** proposed change to `contract:taskplane-product-authoring-and-completion` (a product boundary name, not a claim of an existing runtime schema); consume `contract:taskplane.host-capabilities/v1` and `contract:taskplane.stage-handoff/v1`. Design must verify the concrete existing owners and compatibility.
- **Next decision:** review the recovery priorities and first-slice scope, then authorize a bounded Design if desired. Product scoring and the contract-release approval do not authorize implementation.
- **Design must settle:** the minimum valid Product operation set, which redundant checks can be removed, how normal finish releases only its own authority, and the exact host/version acceptance matrix. It should name any accepted decisions requiring supersession before code changes.

### Focused Product coverage

This is the author's product-risk coverage ledger. It is not an independent specialist review, a native lens dispatch receipt, or a claim that 26 workers executed. Relevant concerns were incorporated into the requirement; implementation judgment is deferred to Design and Engineering.

| Lens | Disposition and basis |
| --- | --- |
| product | Applied: original outcomes, user journey, priorities, eight acceptance criteria, and explicit phase DoR/DoD assessment. |
| security | Applied: preserve source scope, host permissions, and real authority boundaries. |
| code-quality | Deferred: no implementation changes to assess. |
| testability | Applied: observable positive and refusal journeys. |
| design | Applied at product level: clear state, accessible output, recovery, and next owner. |
| scalability | Deferred: no measured throughput or scale claim; investigate only if journey timing identifies it. |
| integrability | Applied: observed installed entry, hook, tool, and storage mismatches. |
| data-safety | Applied: preserve artifacts, decisions, and unrelated sessions during recovery. |
| tech-writer | Applied: concise diagnostics and explanation of actual next action. |
| qa | Applied: installed journey evidence separated from simulation and unit checks. |
| devops | Deferred to Design: package and host/version acceptance matrix required before delivery claims. |
| dba | Not applicable to first slice: no database change proposed. |
| sre | Applied: reachable cancellation, bounded recovery, and honest unavailability. |
| project-management | Applied: bounded slices, dependencies, and no implicit Build. |
| frontend | Not applicable to first slice: no frontend implementation proposed. |
| backend | Deferred to Design: existing runtime owners must be inspected before changes. |
| tradeoffs | Applied: preserve useful gates while reducing operational burden. |
| solution-design | Deferred: select concrete technical changes only in an authorized Design. |
| services-selection | Not applicable: no new service is required. |
| time-to-market | Applied: prove the smallest useful journey before wider recovery. |
| architecture | Applied at product level: one owner per capability, reuse host execution and existing domain gates. |
| mobile | Not applicable: no mobile surface proposed. |
| accessibility | Applied: text result and next action remain available without rich rendering. |
| privacy-compliance | Applied: bounded diagnostic data and no raw transcript or credential export. |
| cost-finops | Applied: avoid duplicate work and distinguish measured usage from forecasts. |
| i18n | Deferred: no new locale scope; preserve existing supported text behavior. |
