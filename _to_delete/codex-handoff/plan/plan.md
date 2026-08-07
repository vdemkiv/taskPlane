# Implementation plan — first-class Design

The approved product direction adds an optional design phase between requirement refinement and implementation planning. It keeps the user surface simple while adding a strict internal Design Contract, a separate proposed-graph overlay, and explicit approval before Build commits to a plan.

## Execution scope

One governed task covers the engine, shared Claude/Codex surfaces, generated lens catalog, documentation, marketplace metadata, and validation. The scope is intentionally consolidated: serial task DoD compares the full uncommitted diff with the approved baseline, and this run is not authorized to create intermediate commits merely to separate internal work packages.

Within that contract the implementation order remains:

1. Extend the loop and CLI with optional `design` and `design_approval` states, Design Contract validation, graph isolation, fingerprints, conformance checks, dashboard state, and tests.
2. Add the `tp-design` skill, `tp-designer` role, shared host routing, and generated-catalog-backed `solution-design` lens without changing the UX `design` lens.
3. Update README, Claude/Codex manifests, and technical docs; run the full suite; validate the OpenAI package; and verify every manifest still says 2.1.0.

## Dependency-graph policy

- Baseline: the existing deterministic graph is read-only input to Design.
- Proposal: Design writes `design/contract.json`; its `graph` object is an overlay and never calls graph mutation APIs.
- Local traversal: depth 3 for engine work, depth 2 for surfaces and docs.
- Distributed/entity boundary: `contract-only`, one level across named API/event/data/runtime contracts.
- Approval: a SHA-256 fingerprint binds the validated design evidence to the loop.
- Plan DoR: task scopes, contracts, policies, and acceptance coverage must cover the approved design.
- Build/Review DoD: realized modules/contracts and final engineering review must report conformance or evidenced, explicitly approved drift.

## Design visualization

Visualization is conditional, not ritual. The Design Contract records `required`, `kind`, `path`, and `reason`. Useful kinds include dependency graph, sequence, state transition, data flow, and UI mock. If no visual would materially clarify a decision, the contract records why it is unnecessary.

## Principal risks and controls

- **State-machine regression:** default `loop init` behavior remains unchanged; Design is opt-in through `--design` or the dedicated skill. Existing persisted states continue loading.
- **Self-approved design:** `design_approval` is a human step; approval records the approver and only then fingerprints the evidence.
- **Graph pollution:** proposed edges are validated structurally but never written through `depgraph.record_edge` or `depgraph.scan`.
- **Stale design:** later plan, evaluation, engineering, and sign-off gates recompute the approved contract fingerprint.
- **Overconstraining implementation:** conformance distinguishes explained drift from silent drift; changes require re-design/re-plan rather than pretending the original design still governs.
- **Claude/Codex drift:** shared skill content, explicit role guidance, compatibility tests, and package validation cover both hosts.
- **Premature release signal:** no version field changes; CHANGELOG uses an unreleased entry only.

## Validation

- Focused loop/design/dashboard tests after T1.
- Skill, lens, and Codex compatibility tests after T2.
- Full pytest suite and `python3 scripts/package_openai.py` validation after T3.
- Final full-catalog engineering review plus requirement-to-implementation and design-to-realization walks before human sign-off.
