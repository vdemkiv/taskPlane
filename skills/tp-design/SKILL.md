---
name: tp-design
description: Design how a requested change should work before implementation, with proportionate alternatives, dependencies, trade-offs, and validation.
---

# Design the requested change

Before substantive work, resolve this installed plugin's runtime and run
`flow activate --workspace <checkout> --phase tp-design --request-reference <actual-user-request>`.
Inspect `flow report`: reuse the matching active visit or initialize the appropriate
scoped run before continuing. This requirement includes standalone work and resumed
stages; loading a skill alone grants no phase approval. See the shared flow for
bootstrap, native dashboard handoff and legitimate user waits.

Inspect the relevant requirements and current code with native tools. Reuse
existing decisions. Describe the smallest workable approach, the interfaces and
data it changes, meaningful alternatives, risks, and how success will be tested.
Use a diagram or mockup only if it makes a decision easier.

Follow [the shared flow](../tp-go/references/shared-flow.md) for standalone Design
and delivery alike. Start/reuse the relevant run at Design, inspect dependencies
and source decomposition, attach the design task decomposition and evidence, and
render the shared dashboard. Reuse Product criteria or Engineering findings.

Keep detail proportionate to the change. Do not require a lens quota, signed
artifact, or separate designer. The root
orchestrator owns the handoff and assesses readiness from the actual design.
A design-only request ends with the design; it does not authorize implementation.
Present the design and resolve this checkpoint through the shared approval policy before continuing
via [delivery](../tp-go/SKILL.md). Reuse an existing valid Design approval.

Record useful milestones and refresh the shared dashboard throughout Design.
Missing observations are advisory and do not invalidate the design.

## Shared delivery state

Standalone and delivery Design use the same run, task decomposition, dependency
graph and dashboard defaults. Return evidence to that run; the root orchestrator
owns stage advancement and respects Design-only scope.

The shared flow defaults to explicit human approval at each phase checkpoint.
A recorded, explicit run policy may authorize automatic approval as described there. Use
the dependency graph with source component decomposition, task DAG and shared
dashboard throughout. Unsupported host authority must be reported, never bypassed.

## Consume bounded context

After activation, start, advancement or resumption, use the installed runtime's
`flow context --workspace <checkout> --run <run>` and consume the returned
handoff with `--consume <handoff-ref-sha256>`. Use `--task <current-task-id>` for
focused work. Read every omitted required input using `--read <ref-sha256>`;
follow index children and page cursors until all required bodies are returned.
A fresh consumer receives these bodies, scoped source and relevant findings;
do not copy the predecessor conversation or tool history into its instructions.
No context operation authorizes dispatch or a phase transition.

For phase submission, obtain a current whole-phase receipt (without `--task`)
and include the returned `context_receipt` object in the phase output. Renew it
after source, scope, revision or accepted input changes. New bounded/v1 runs
reject missing, partial, foreign or stale receipts. Legacy runs keep their
original evidence contract. A receipt proves returned data, not model attention.

Routine command summaries preserve the current gate and reference full details.
Expand specific references when needed; request `--full` only for a consumer that
requires the complete legacy report. Native dashboards retain complete evidence.
Reuse a prior passing check only when its verification record matches current
source/dependency, tests, runtime, environment, command and criteria fingerprints.
Retain failed/unknown results and findings; reuse never transfers approval.
