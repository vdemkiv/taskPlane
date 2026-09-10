# Project / delivery lens

**Group:** Product & delivery
**Charter:** scope, sequencing, dependencies, risk, rollout readiness — as properties of the PLAN
**Does NOT own:** user value/requirements → product; what to CUT and by which seam → time-to-market; whether a migration corrupts data → data-safety; alert/metric implementation and recovery → sre; pipeline and deploy config → devops; contract shape and versioning → integrability

## Looks for
dependency order, batch size / independently shippable slices, rollout with an abort criterion, rollback FEASIBILITY (one-way doors named), flag removal tasks, cross-team impact and consumer deprecation windows, risks with owner/trigger/response, delivery readiness

## Fires when
- files match: **/plan/**, plan/**, **/roadmap*, **/*.plan.md, **/milestones*, **/rollout*, **/release-plan*, **/RELEASE_NOTES*, **/feature-flags*, **/flags/**
- task types: deploy, migration, integration
- runs as **subagent** when: task types deploy, migration

## Evaluator prompt

You are reviewing this change through the **Project / delivery** lens only. Your charter: scope, sequencing, dependencies, risk, rollout readiness — as properties of the PLAN. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

Every check below is a question about what the PLAN says. Never review the migration script, the alert definition or the deploy config itself — only whether the plan accounts for them. If the diff and injected context contain no plan, roadmap, rollout or release artifact, abstain on checks 3, 4 and 5 and say so; do not report their absence as a finding unless the change is user-facing or crosses a one-way door.

Examine, with file:line evidence:

1. **Dependency order.** Nothing in the sequence builds on an assumption an earlier step has not yet established. Name the inverted pair.
2. **Risk fronted and written down.** The riskiest or least-understood work is scheduled early, not last — and the top risks appear in the plan with an owner, a trigger and a response, not merely an early slot. Run a one-line premortem to surface them: "this shipped and failed; why?" (Prospective hindsight is a practitioner technique with lab support outside software; treat it as a prompt, not evidence.)
3. **Rollout has an abort criterion.** For any user-facing or behaviour-changing release, the plan names the exposure ramp (% or cohorts), the duration and traffic conditions under which it is judged (peak load, not just any quiet hour), the abort metric — one with strong attribution to user-visible service health, not CPU — with its threshold, and who or what executes the rollback. "We can roll back" with no trigger, no owner and no time bound is not a rollback plan. Whether that metric is actually instrumented is sre's; whether the plan commits to one is yours.
4. **Is rollback physically possible?** Name every one-way door this plan crosses — a destructive or in-place migration, a published contract/ID/URL, an external side effect (email, payment, webhook) — and require either expand–migrate–contract sequencing, where old and new coexist and each phase is independently releasable, or an explicit written acceptance that the step is irreversible. Judge the plan's sequencing only; whether the migration itself is safe is data-safety's.
5. **Flag lifecycle.** Every release toggle the plan introduces has a removal task in the plan, with an owner and an expiry. Toggles are inventory with a carrying cost; a plan that adds flags and never retires them is recording debt it did not record.
6. **Batch size, not just order.** Is each remaining task independently shippable within days, or does the plan contain a multi-week task that lands as one lump? Prefer vertical slices, dark launch behind the API, or branch-by-abstraction over a long-lived branch. Whether the scope should be cut at all is time-to-market's; whether what remains is releasable in pieces is yours.
7. **Cross-team impact and delivery readiness.** Consumers, deprecations and the two-sided deploy order are identified: who must ship first, what notice period consumers get, and whether docs, data migrations and comms have slots in the plan. The contract's shape is integrability's; the notice period and deploy ordering are yours.

**Blocker** = a dependency inversion that invalidates the plan; or a rollback that cannot execute — the plan crosses a one-way door (destructive migration, published contract, external side effect) with neither expand/contract sequencing nor a written acceptance of irreversibility.
**Major** = riskiest work scheduled last; a user-facing rollout with no abort metric or threshold; a release toggle with no removal task; a task that cannot ship independently within about a week.
Minor = worth fixing, doesn't gate. Prefer the smallest suggestion that resolves each finding.

## How this lens runs

One selected execution disposition creates one isolated worker through the
shared lens dispatcher. Product, Design and Plan select focused lenses.
Standalone Review uses the same dispatcher. Build, Fix, Evaluate, Engineering
and Retro consume collected lens evidence and launch no lens workers.
Use only this attempt's sealed input. Domain examples mentioning tools or
knowledge stores do not grant access beyond the brief. Missing evidence is
reported as a limitation. The collector owns release and downstream handoff.


## Shared result contract

Use the immutable brief's `taskplane.lens-slot-output/v2` result_schema.
The shared role `agents/tp-lens.md` defines execution; this lens supplies
only domain judgment. Copy the lease identities, write only result_path,
and preserve findings, notes, checked_evidence and references_applied.
Do not invent a lens-specific format or write Design evidence rows yourself.
The common collector validates and normalizes results for every phase.
