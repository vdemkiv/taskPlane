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
