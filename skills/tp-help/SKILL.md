---
name: tp-help
description: Explain Taskplane capabilities, commands, and existing workflow state when the user asks for help. Does not initialize or activate a workflow.
---

# Taskplane help

Answer the user's question directly. Use only the relevant installed documentation
or a command's `--help` output. Do not run onboarding, inventory tools, activate a
contract, or render a setup dashboard just to explain the product.

Ordinary code review uses native tools and [engineering](../tp-engineering/SKILL.md).
The orchestrator prepares delivery; humans accept each phase checkpoint. Hooks
separate mandatory workflow refusals from advisory usage. The default native_workflow
profile uses local state and observed human provenance. Host-wide protections are
unavailable; protected_host requires a verified owner. Native permissions remain separate. Use `flow report`
for human decisions, recorded milestones and available token usage.
For actual setup or a concrete installation failure, consult
[CLI reference](../../docs/cli-reference.md).
Explain only the requested concept; load no full manuals or hook manifests as a tour.
For a question about phase policy, consult [the shared flow](../tp-go/references/shared-flow.md).
