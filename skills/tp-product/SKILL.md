---
name: tp-product
description: Define the requested product outcome, scope, acceptance criteria, and material dependencies without changing implementation.
---

# Define what should be built

Before substantive work, resolve this installed plugin's runtime and run
`flow activate --workspace <checkout> --phase tp-product --request-reference <actual-user-request>`.
Inspect `flow report`: reuse the matching active visit or initialize the appropriate
scoped run before continuing. This requirement includes standalone work and resumed
stages; loading a skill alone grants no phase approval. See the shared flow for
bootstrap, native dashboard handoff and legitimate user waits.

Use the user's request and relevant existing product context. State the problem,
intended user behavior, scope, acceptance criteria, material dependencies, and
unresolved decisions. Reuse settled answers and keep the artifact proportionate
to the task. Write a document when requested or useful for downstream work.

Follow [the shared flow](../tp-go/references/shared-flow.md) even for Product-only
work. Start/reuse the relevant run at Product, inspect the dependency graph and
source decomposition, record a proportionate task decomposition, attach criteria,
and render the shared dashboard. If Engineering initiated the work, use its findings
and evidence to define the problem and acceptance criteria; preserve finding IDs
and prior decisions rather than restarting discovery.

Distinguish product readiness from delivered implementation. A completed spec
means the outcome is defined; it does not mean the product has been built or
validated. Surface only questions that materially affect the result.

Use native tools for inspection and document authoring. Product-only work does
not authorize implementation. For an already authorized delivery, return the
criteria and dashboard for Product checkpoint acceptance under the shared policy before
continuing through [delivery](../tp-go/SKILL.md). Reuse a valid prior acceptance.
A completed document or telemetry receipt cannot authorize advancement.

## Shared delivery state

Standalone and delivery Product use the same run, task decomposition, dependency
graph and dashboard defaults. Return evidence to that run; the root orchestrator
owns stage advancement and respects Product-only scope.

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
