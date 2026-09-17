---
name: tp-tag
description: Use the shared Taskplane delivery flow in a Claude channel task.
---

# Delivery in a channel task

Follow [delivery](../tp-go/SKILL.md) and [the shared flow](../tp-go/references/shared-flow.md).
Keep the requested scope and native permissions. Each phase requires explicit
human checkpoint approval; the orchestrator advances only after that valid decision.
Claude and Codex share native_workflow gates and explicit host limits. Requests for
protected_host refuse without a verified native owner; there is no silent downgrade.
Use the same task decomposition, dependency graph, evidence and dashboard.
Where native counters are unavailable, report unknown usage and continue.
Share messages or artifacts with other people only when explicitly authorized.
