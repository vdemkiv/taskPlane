---
name: tp-help
description: Explain Taskplane capabilities, commands, and existing workflow state when the user asks for help. Does not initialize or activate a workflow.
---

# Taskplane help

Answer the user's question directly. Use only the relevant installed documentation
or a command's `--help` output. Do not run onboarding, inventory tools, activate a
contract, or render a setup dashboard just to explain the product.

Ordinary code review uses native tools and [engineering](../tp-engineering/SKILL.md).
The orchestrator prepares delivery; humans accept checkpoints by default.
Explicit user instructions can authorize run-bound automatic phase approvals. Hooks
separate mandatory workflow refusals from advisory usage. The default native_workflow
profile uses local state and observed human provenance. Host-wide protections are
unavailable; protected_host requires a verified owner. Native permissions remain separate. Use `flow report`
for human decisions, recorded milestones and available token usage.
For actual setup or a concrete installation failure, consult
[onboarding](../../docs/onboarding.md) and [CLI reference](../../docs/cli-reference.md).
Explain only the requested concept; load no full manuals or hook manifests as a tour.
For a question about phase policy, consult [the shared flow](../tp-go/references/shared-flow.md).

For reusable workflow questions, consult only the relevant section of
[Workflow Builder](../../docs/workflow-builder.md). Explain conversational
create/edit/save/preview/run through `tp-workflow`, the change-risk review,
Design brief and feature-delivery seeds, and the distinction between saving a
definition and root execution after its Design checkpoint. `workflow catalog`,
`validate`, `preview` and run-bound `check` are read-only; there is no
`workflow run` command. Compilation produces inputs for existing `flow` controls.
Check actual loaded support before claiming availability. Fixture coverage is
not live host evidence, and unsupported runtime or native capacity stays explicit.

Explain that plugin installation, hook trust, phase acceptance and host tool permissions
are separate. Automatic mode requires explicit additional instructions; show a concise
example and how to return to manual mode when requested.
