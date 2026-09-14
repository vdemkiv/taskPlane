---
name: taskplane
description: Route requests to review code, design a change, implement work, inspect Taskplane status, or explain Taskplane. Preserve the user's requested scope and existing decisions.
---

# Taskplane

Route the requested work directly. Review, help, and status do not require onboarding,
hook readiness, a delivery run, or a fresh conversation.

- Source review, architecture/security review, or validation: read
  [engineering](../tp-engineering/SKILL.md) and use native tools in the available checkout.
- Status: read [status](../tp-status/SKILL.md). Inspect existing state without initializing it.
- Help: read [help](../tp-help/SKILL.md). Answer the question without setup.
- Product requirements: read [product](../tp-product/SKILL.md).
- Design before implementation: read [design](../tp-design/SKILL.md).
- Build or fix an approved delivery plan: read [delivery](../tp-go/SKILL.md).
  For a new feature or explicit A/B prototype, use [build](../tp-build/SKILL.md).
- Explicit installation, setup, or configuration diagnostics: read
  [entry diagnostics](references/entry-initialization.md).

Product, Design, and Plan should produce concrete work for review before Build when
requested. Keep native execution, permissions, waiting, and session identity with the
host. Load only the selected workflow's references; don't recite internal setup or
introduce approval steps that the user has already satisfied.
