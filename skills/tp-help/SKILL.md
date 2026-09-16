---
name: tp-help
description: Explain Taskplane capabilities, commands, and existing workflow state when the user asks for help. Does not initialize or activate a workflow.
---

# Taskplane help

Answer the user's question directly. Use only the relevant installed documentation
or a command's `--help` output. Do not run onboarding, inventory tools, activate a
contract, or render a setup dashboard just to explain the product.

Ordinary code review uses native tools and [engineering](../tp-engineering/SKILL.md).
The orchestrator owns delivery. Hooks record advisory telemetry and never block
normal flow; native host permissions remain authoritative. Use `flow report` for
recorded milestones and available token usage.
For actual setup or a concrete installation failure, consult
[CLI reference](../../docs/cli-reference.md).
Explain only the requested concept; load no full manuals or hook manifests as a tour.
