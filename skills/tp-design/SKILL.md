---
name: tp-design
description: Design how a requested change should work before implementation, with proportionate alternatives, dependencies, trade-offs, and validation.
---

# Design the requested change

Inspect the relevant requirements and current code with native tools. Reuse
existing decisions. Describe the smallest workable approach, the interfaces and
data it changes, meaningful alternatives, risks, and how success will be tested.
Use a diagram or mockup only if it makes a decision easier.

Follow [the shared flow](../tp-go/references/shared-flow.md) for standalone Design
and delivery alike. Start/reuse the relevant run at Design, inspect dependencies
and source decomposition, attach the design task decomposition and evidence, and
render the shared dashboard. Reuse Product criteria or Engineering findings.

Keep detail proportionate to the change. Do not require a lens quota, signed
artifact, or separate designer. The root
orchestrator owns the handoff and assesses readiness from the actual design.
A design-only request ends with the design; it does not authorize implementation.
Present the design and wait for human approval of this checkpoint before continuing
via [delivery](../tp-go/SKILL.md). Reuse an existing valid Design approval.

Record useful milestones and refresh the shared dashboard throughout Design.
Missing observations are advisory and do not invalidate the design.

## Shared delivery state

Standalone and delivery Design use the same run, task decomposition, dependency
graph and dashboard defaults. Return evidence to that run; the root orchestrator
owns stage advancement and respects Design-only scope.

The shared flow requires explicit human approval at each phase checkpoint. Use
the dependency graph with source component decomposition, task DAG and shared
dashboard throughout. Unsupported host authority must be reported, never bypassed.
