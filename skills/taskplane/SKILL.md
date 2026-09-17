---
name: taskplane
description: Route requests to review code, design a change, implement work, inspect Taskplane status, or explain Taskplane. Preserve the user's requested scope and existing decisions.
---

# Taskplane

Route the requested work directly. Product, Design, and Engineering always use the
shared dependency graph, task decomposition, and dashboard, including standalone
requests. Read [the shared flow](../tp-go/references/shared-flow.md) for these defaults.
Help and status inspect existing state without starting a run. The default native_workflow
profile enforces Taskplane gates using observed human responses and local state. Host-wide
protection is unavailable; explicitly requested protected_host execution still refuses
without a verified owner. Never substitute one profile for the other.

- Source review, architecture/security review, or validation: read
  [engineering](../tp-engineering/SKILL.md) and use native tools in the available checkout.
- Status: read [status](../tp-status/SKILL.md). Inspect existing state without initializing it.
- Help: read [help](../tp-help/SKILL.md). Answer the question without setup.
- Product requirements: read [product](../tp-product/SKILL.md).
- Design before implementation: read [design](../tp-design/SKILL.md).
- Build or fix an approved delivery plan: read [delivery](../tp-go/SKILL.md).
  For a new feature or explicit A/B prototype, use [build](../tp-build/SKILL.md).
- Explicit installation, setup, or configuration diagnostics: read
  [CLI reference](../../docs/cli-reference.md).

Product, Design, Plan, Build, Evaluate, Engineering and Retro each require explicit
human approval of their concrete output before advancement. Engineering can be the entry point: its findings can define subsequent
Product scope, Design, and implementation when authorized. Keep native execution,
permissions, waiting, and session identity with the
host. Load only the selected workflow's references; don't recite internal setup or
introduce approval steps that the user has already satisfied.

Native discovery is observational. Use the installed named hook entry points and
report their actual capability status; restoring native routines does not admit
a host owner or change any human checkpoint.
