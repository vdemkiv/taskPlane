# First-class Design workflow

## Problem

taskPlane exposes Build and Review as the two main execution surfaces. Build already performs product refinement and visual-first work, but there is no explicit place to design a new solution before committing to an implementation plan. Users need a simple Design entry point that turns a feature idea, technical problem, or existing-code constraint into an approvable engineering approach.

## Responsibility model

- Product owns **what** should be built and why.
- Design owns the proposed **how** before code changes.
- Build realizes the approved approach.
- Review judges the realized result and any drift from the approved approach.

Design is optional. It is explicit when the user says `taskplane design`, and Build may route complex, cross-boundary, high-cost, or ambiguous work through it. Small, reversible changes may continue directly to planning and implementation.

## Design Contract

Design must produce a machine-checkable Design Contract and a concise human-readable design. The contract records:

- requirement and current-state evidence;
- at least one viable approach and an explicit selected approach;
- modules and named API, event, data, and runtime contracts;
- a proposed dependency-graph overlay that is distinct from the as-built graph;
- local dependency depth and contract-level boundary policy;
- risks, failure modes, observability, rollout, and rollback;
- mapping from acceptance criteria to design elements and validation;
- the `solution-design` lens verdict;
- whether a visualization is useful, its type and path, or why it is unnecessary.

## Design Definition of Ready

Design may start only when the goal is anchored to a sufficiently refined requirement, open blocking questions are closed, brownfield work has a current-state and baseline graph, governing decisions are available, and the intended system boundary is named. For distributed systems, traversal across entity or service boundaries stops at named contracts unless a human explicitly expands it.

## Design Definition of Done

Design is done only when the contract is complete and internally consistent, alternatives and trade-offs are explicit, every acceptance criterion maps to a design element and validation method, proposed modules and edges resolve under the declared depth policy, contract boundaries are named, risks and operational consequences have owners or mitigations, conditional visualization has been handled, and a human approves the design.

Approval fingerprints the Design Contract. Planning must remain consistent with that fingerprint and cover the approved modules, contracts, graph policy, and acceptance mapping. Build and final Review compare the realized graph and behavior with the approved design; unexplained drift blocks completion or requires an explicit re-design/re-plan decision.

## User surface

The simple entry point exposes four intents:

- `taskplane design …`
- `taskplane build …`
- `taskplane review …`
- `taskplane status`

Codex and Claude must route these intents consistently. A dedicated `tp-design` skill and `tp-designer` role own the design artifact. The existing `design` lens remains the UX/product-interface lens; a new `solution-design` lens judges engineering approaches.

## Acceptance criteria

1. `taskplane design` routes consistently in Codex and Claude guidance and skills.
2. The loop supports an optional Design step plus an explicit human design-approval gate before Plan.
3. A mechanically validated Design Contract contains the required solution, graph, contract, risk, rollout, acceptance, lens, and visualization evidence.
4. The proposed graph never mutates the as-built dependency graph before implementation.
5. Approved design evidence is fingerprinted; stale or changed evidence is rejected at later gates.
6. Plan DoR covers the approved design, and evaluation/final Review report conformance or explicit drift.
7. The existing UX `design` lens remains unchanged; the new `solution-design` lens is first-class.
8. Existing behavior remains compatible when Design is not enabled.
9. The complete test suite and OpenAI package validation pass.
10. Plugin version remains 2.1.0 for this implementation.
