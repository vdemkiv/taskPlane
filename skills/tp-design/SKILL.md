---
name: tp-design
description: Design how a requested change should work before implementation, with proportionate alternatives, dependencies, trade-offs, and validation.
---

# Design the requested change

Inspect the relevant requirements and current code with native tools. Reuse
existing decisions. Describe the smallest workable approach, the interfaces and
data it changes, meaningful alternatives, risks, and how success will be tested.
Use a diagram or mockup only if it makes a decision easier.

Keep detail proportionate to the change. Do not require a graph registry, lens
quota, signed artifact, separate designer, or workflow initialization. The root
orchestrator owns the handoff and assesses readiness from the actual design.
A design-only request ends with the design; it does not authorize implementation.
When implementation is already authorized, continue via
[delivery](../tp-go/SKILL.md) without asking for the same approval again.

Record useful milestones through delivery telemetry when a flow is active.
Missing observations are advisory and do not invalidate the design.

## Shared delivery state

For an active delivery, follow [the shared flow](../tp-go/references/shared-flow.md).
Use its existing run, task decomposition, dependency graph and dashboard. Return
evidence to that run; the root orchestrator owns stage advancement.
