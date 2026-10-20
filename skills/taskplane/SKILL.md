---
name: taskplane
description: Route requests to review code, design a change, implement work, author or invoke reusable workflows, inspect Taskplane status, or explain Taskplane. Preserve the user's requested scope and existing decisions.
---

# Taskplane

Route the user's actual request and preserve existing decisions and constraints.
Read only the selected route: [Product](../tp-product/SKILL.md),
[Design](../tp-design/SKILL.md), [delivery](../tp-go/SKILL.md),
[Engineering](../tp-engineering/SKILL.md), [workflow](../tp-workflow/SKILL.md),
[status](../tp-status/SKILL.md) or [help](../tp-help/SKILL.md).

Help, status, catalog, validation and preview inspect existing state without
activation. Execution uses [the shared flow](../tp-go/references/shared-flow.md).
Resolve the selected project and actual loaded runtime; continue matching work
rather than create another run. Respect an explicit manual continuation without
reactivating a retired harness. Missing setup is a concrete blocker, not a reason
to redirect a standalone review into full delivery.

Before a new repository run, select a clean dedicated linked worktree at the
intended repository/base through the native host. Verify actual session location;
a shell workdir or worktree attachment is insufficient. Report automatic hook
coverage separately; missing callbacks do not veto root checkpoints or require
a session restart. Native worker readiness remains mandatory. Resume
matching initialized work without repeating fresh-start checks or approvals.

Product, Design, Plan, Build, Evaluate, Engineering and Retro each retain a
separate approval. Loading a skill, producing an artifact or completing a worker
never approves a phase. Source review does not authorize implementation.
Default to manual decisions. Optional automatic mode requires explicit run-bound
policy authorization, scoped conditions, named stop phases and assessments;
ordinary assent approves only the current unchanged result.

Root work, serial work and useful scoped native workers are valid. This skill
authorizes delegation for useful independent tasks, subject to user constraints;
it requires no worker count or lens quota. Report actual independent coverage.
See [native ownership](../tp-go/references/codex-native-dispatch.md).
