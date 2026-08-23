---
name: tp-design
description: "The pre-build solution-design flow of taskplane. Use when the user says 'taskplane design', asks to design a new feature or approach before coding, wants a technical design based on requirements and/or an existing codebase, wants alternatives and trade-offs, or needs dependency/contract/rollout decisions settled before Build. Produces an approved Design Contract with proposed graph, Design DoR/DoD, solution-design evidence, and conditional visualization. It designs the HOW; it does not implement or review current code."
---

# /tp-design — settle the HOW before Build

The user provides the goal and material decisions. Keep the interface simple; internally run the same strict taskplane harness as Build and Review.

`flow.json` is the approved Design graph: **requirement/code context → baseline
graph → alternatives/trade-offs → Design Contract → solution-design evidence
→ conditional technical visual → human Design approval**. Design never exits
through a worker-authored verdict; the human gate is part of the contract.

Prefer the stable workspace launcher on Codex; it resolves the newest valid
installed taskplane engine on every call. Fall back to the loaded plugin root
only during first setup or on another host:

```bash
if [ -f .taskplane/codex-hook.py ]; then
  TP=".taskplane/codex-hook.py"
else
  TP="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"
fi
```

If that path does not exist, locate this skill's plugin root and use its `taskplane/tp.py`. Do not ask the user to run commands.

## Responsibility boundary

- Product owns **WHAT** and why.
- Design owns the proposed **HOW** before code changes.
- Build realizes the approved Design Contract.
- Review judges the realized result and any drift.

Design is read-only toward product code. The `tp-designer` role may write only `design/**`; it never implements, fixes, mutates the as-built dependency graph, or approves its own work.

## Start

When Design is grounded in an existing repository, URL, ref, or PR, first run
`python3 "$TP" repository prepare <target>` and use the verified checkout and
run paths it returns. A recoverable `needs_user` action is a conversation-level
approval: ask it here, then resume the same run. Do not perform an ad-hoc clone,
store source under artifacts, or send the user to a new task/terminal.

Separation of duties starts with requirement refinement, not only with the
later design artifact. The orchestrator never authors Product or Design work.
For every `loop next` Product/Design payload, immediately dispatch the exact
role named by the engine and wait for its result. On Codex use the emitted
`task_name`, role instructions, standalone `role_marker`, model when non-null,
and `reasoning_effort`, following
[`../tp-go/references/codex-native-dispatch.md`](../tp-go/references/codex-native-dispatch.md).
Claude dispatches the same complete brief through its named agent. If the host
cannot dispatch, stop as a host-capability blocker; never refine or design
inline as a fallback.

Run onboarding first:

```bash
python3 "$TP" onboard --json
```

If setup is incomplete, complete the safe setup steps directly. Then ensure
the goal is anchored to a refined requirement with testable acceptance
criteria and no blocking open questions. When the WHAT is not ready,
initialize once without `--req` so the loop's Product step emits the
`tp-product` worker brief; dispatch it, then attach its returned R-id with
`loop gate pass --req R-XXXX`. Do not run standalone Product first, create a
second requirement, or let the orchestrator/Design invent product scope.

Choose one execution form:

- The user asked only for a design: initialize with `loop init --design --design-only [--req R-…] "<goal>"`.
- The user wants Design followed by implementation: initialize with `loop init --design [--req R-…] "<goal>"`.
- An existing spec may be passed with `--spec`; otherwise the Product step runs first.

## When Build should route through Design

Build should add `--design` when any of these is true:

- multiple modules, components, services, or teams are affected;
- an API, event, schema, data, runtime, or deployment contract changes;
- the work is distributed, migratory, security/privacy-sensitive, expensive, hard to reverse, or operationally risky;
- the requirement admits materially different approaches;
- current code or settled decisions constrain the solution and the correct shape is not already explicit.

Small, local, reversible, single-module work with an obvious implementation may go directly to Plan/Build. If uncertain, use Design; the extra human gate is cheaper than building the wrong dependency shape.

## Drive the Design phase

Call `loop next` once for the Design step and apply the same mandatory native
dispatch rule from Start; wait for and collect the worker before the
orchestrator gates the handoff.

The Design brief includes the requirement, accepted decisions, current-state inventory, baseline graph fingerprint, bounded impact, and the mandatory `solution-design` lens.

Have `tp-designer` inspect the cited code and write:

- `design/design.md` — concise human design and decision rationale;
- `design/contract.json` — schema `taskplane.design/v1`;
- `design/visual.html` only when a visual materially clarifies the choice.

Read [references/design-contract.md](references/design-contract.md) for the exact contract and graph rules.

Then call `loop gate pass`. The orchestrator, not the designer, validates Design DoD. Missing alternatives, acceptance mappings, named contracts, graph policy, graph DoR/DoD, risk/rollout evidence, lens evidence, safe visualization, or graph isolation keeps the loop at Design.

## Human approval

At `design_approval`, show the user:

- selected approach and alternatives rejected;
- proposed modules, dependency edges, and named contracts;
- local depth plus cross-entity boundary policy;
- important failure modes, risks, rollout, rollback, and observability;
- acceptance-to-validation mapping;
- the visualization when one was useful;
- the mechanical Design DoD result.

Wait for an explicit human decision. Record approval with `loop approve --by "<human identity/context and quoted approval>"`. Never infer or self-issue approval.

Approval fingerprints the complete evidence. For design-only work the loop
ends. For design-before-build it advances to Plan, whose DoR must cover the
approved modules, contracts, graph depth, acceptance mapping, and every
proposed edge via canonical task `design_edges` entries (`FROM->TO:KIND`).

### Non-build terminal handoff

Design receives one bounded stage dispatch. `taskplane.stage-dispatch/v1`
contains `taskplane.stage-startup/v1`; its `input_handoff` is the versioned
bounded `taskplane.stage-handoff/v1` manifest. The startup also carries
explicitly selected content-addressed artifacts and the current stage
authority, budget, and scope. Never inherit predecessor agents, conversations,
event logs, tool transcripts, leases, runtime roots, or other mutable execution
context.

For design-only work, or an attributed decision not to continue into
implementation, terminalize the Design stage as `done`, `closed`, or
`discarded` and create no implicit Build. `done` requires the declared Design
deliverables and completion evidence; `closed` requires the attributable
reason no further work is needed; `discarded` requires the attributable reason
its result must not be consumed. Retain its immutable artifacts and handoff
for audit. Later use of retained `closed` or `discarded` artifacts requires an
explicit `stage reuse` operation, explicit new authority, and exact selected
fingerprints; it never reopens or rewrites Design history.

## Downstream enforcement

When an approved design exists:

- Plan cannot silently narrow its module/contract/dependency coverage.
- Execute and Evaluate receive the approved contract and reject stale evidence.
- Engineering Review must include `meta.design` with the approved fingerprint, every designed module/edge/contract checked, verdict `conformant`, and an empty `drift` list — ANY recorded drift entry blocks sign-off, explained or not.
- Drift is not papered over. Either return to Design, obtain a new approval, and re-plan, or have the human accept the specific deviation on the record: move it to `accepted_drift` (each entry requires `drift`, `reason`, and `accepted_by`), which the sign-off gate renders visibly rather than burying.

For distributed systems, traverse local implementation dependencies to the declared depth, but cross service/entity boundaries at named `contract:` or `resource:` nodes only. Do not expand into another entity's internals unless the human explicitly changes the boundary policy.

## Visualization is conditional

Choose the smallest useful visual:

- dependency graph for coupling and blast radius;
- sequence diagram for cross-component interaction;
- state machine for lifecycle or failure recovery;
- data-flow diagram for storage, privacy, or migration;
- UI flow/mock only when interaction is a design decision.

If prose and the graph declaration are clearer, set `visualization.required=false` and record the reason. Never create a decorative visual merely to satisfy a ritual.
