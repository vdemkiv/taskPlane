---
name: tp-build
description: Build a requested feature through to verified output. Reuse existing product decisions and use optional alternatives only when requested or needed.
---

# Build a feature

Before substantive work, resolve this installed plugin's runtime and run
`flow activate --workspace <checkout> --phase tp-build --request-reference <actual-user-request>`.
Inspect `flow report`: reuse the matching active visit or initialize the appropriate
scoped run before continuing. This requirement includes standalone work and resumed
stages; loading a skill alone grants no phase approval. See the shared flow for
bootstrap, native dashboard handoff and legitimate user waits.

Read [delivery](../tp-go/SKILL.md) and execute its flow. The root orchestrator
owns completion. Clarify the outcome, inspect the existing implementation, make
the smallest sufficient design, build, verify, and deliver.

Use a visual when it helps settle a UI decision; do not require one for backend
work. Discuss alternatives when a consequential trade-off needs a decision.
Build A/B variants only when requested, in separate checkouts, and let the user
select before integrating mutually exclusive variants. Do not create multiple
implementations or review waves by default.

Reuse authorization already given. Missing telemetry or a
review quota must not block authorized implementation. Native host permissions
remain in force.

## Shared delivery state

Follow [the shared flow](../tp-go/references/shared-flow.md) for every execution.
Use the shared run, task decomposition, dependency graph and dashboard, including
work originating in a standalone phase. Return evidence to that run; the root
orchestrator owns stage advancement.

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
