---
name: tp-northstar
description: Give an on-demand strategic review of a task, design or change against the project's direction.
---

# Strategic review

Before substantive work, resolve this installed plugin's runtime and run
`flow activate --workspace <checkout> --phase tp-northstar --request-reference <actual-user-request>`.
Inspect `flow report`: reuse the matching active visit or initialize the appropriate
scoped run before continuing. This requirement includes standalone work and resumed
stages; loading a skill alone grants no phase approval. See the shared flow for
bootstrap, native dashboard handoff and legitimate user waits.

Read the requested target and existing product direction. If the direction is not
available, state that limitation. Assess alignment, leverage, reversibility,
opportunity cost, and coherence. Identify the sharpest tension and recommend a
concrete next action. Keep the note proportionate to the decision.

This is advisory and does not authorize code changes. Without a matching active run, use a standalone Engineering visit. For an active delivery,
follow [the shared flow](../tp-go/references/shared-flow.md), inspect its dependencies
and task decomposition, and attach the note to the same run and dashboard.

The shared flow defaults to explicit human approval at each phase checkpoint.
A recorded, explicit run policy may authorize automatic approval as described there. Use
the dependency graph with source component decomposition, task DAG and shared
dashboard throughout. Unsupported host authority must be reported, never bypassed.
