---
name: tp-help
description: Explain Taskplane capabilities, commands, and existing workflow state when the user asks for help. Does not initialize or activate a workflow.
---

# Taskplane help

Answer the user's question directly. Use only the relevant installed documentation
or a command's `--help` output. Do not run onboarding, inventory tools, activate a
contract, or render a setup dashboard just to explain the product.

Ordinary code review uses native tools and [engineering](../tp-engineering/SKILL.md).
Taskplane's explicit delivery workflows retain their requirements and evidence checks.
For actual setup or a concrete installation failure, consult
[entry diagnostics](../tp-go/references/setup.md).
Explain only the requested concept; load no full manuals or hook manifests as a tour.
