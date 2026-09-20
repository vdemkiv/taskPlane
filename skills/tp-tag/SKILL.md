---
name: tp-tag
description: Use the shared Taskplane delivery flow in a Claude channel task.
---

# Delivery in a channel task

Before substantive work, resolve this installed plugin's runtime and run
`flow activate --workspace <checkout> --phase tp-tag --request-reference <actual-user-request>`.
Inspect `flow report`: reuse the matching active visit or initialize the appropriate
scoped run before continuing. This requirement includes standalone work and resumed
stages; loading a skill alone grants no phase approval. See the shared flow for
bootstrap, native dashboard handoff and legitimate user waits.

Follow [delivery](../tp-go/SKILL.md) and [the shared flow](../tp-go/references/shared-flow.md).
Keep the requested scope and native permissions. Each phase requires checkpoint acceptance; human approval is the default and
explicit run-bound policies may authorize automatic decisions. The orchestrator
advances only after that valid decision.
Claude and Codex share native_workflow gates and explicit host limits. Requests for
protected_host refuse without a verified native owner; there is no silent downgrade.
Use the same task decomposition, dependency graph, evidence and dashboard.
Where native counters are unavailable, report unknown usage and continue.
Share messages or artifacts with other people only when explicitly authorized.
