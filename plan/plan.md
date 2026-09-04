# R-0001 implementation plan: stateless phase pickup

## Authority and status

This plan is derived only from `specs/spec.md`, `design/design.md`,
`design/contract.json`, and the single bounded dependency-impact result at the
current repository HEAD. The Design artifacts are a validated candidate
awaiting consolidated human approval. This plan does not approve Design or
Plan, does not authorize Build, and does not create or imply a human gate
receipt. Implementation starts only after the orchestrator presents the
validated Design and Plan for attributable human approval.

## Outcome

Implement the Design candidate without drift: one repository-native,
closed `taskplane.stage-handoff/v2` producer and one synchronous stateless
consumer for requirement-to-Design, Design-to-Plan, Plan-to-Build, Build
terminal evidence, and interrupted same-phase resume. The consumer treats the
sealed repository handoff as its sole predecessor authority and performs every
integrity, lineage, authority, transition, dependency, scope, and proof check
before any attempt minting, dispatch, authoring, checkpoint, publication, or
integration effect.

The public commands are exactly:

- `tp.py phase export --request <repository-relative-json>`
- `tp.py phase pickup <repository-relative-handoff>`
- `tp.py phase submit --request <repository-relative-json>`
- `tp.py phase resume <repository-relative-handoff>`

The legacy `tp.py pickup <approved-design>` path remains schema-disjoint and
retains its v1/v2 input, trust-source, receipt, interruption, collision,
repository-only resume, cold-start, and refusal-order behavior.

## Non-negotiable implementation invariants

1. The v2 manifest is closed and capped at 64 KiB; it contains every field
   listed in the Design Contract. Repository artifact references are closed,
   digest-addressed, path-safe, and capped at 64 entries. Startup values are
   bounded to 128 KiB.
2. Canonicalization is UTF-8 JSON with sorted object keys, compact separators,
   no NaN, and schema-defined list order. SHA-256 excludes only the top-level
   fingerprint. The handoff identity binds repository identity, exact source
   commit/tree, requirement fingerprint, producer phase/outcome, successor
   phase/mode, and predecessor handoff/receipt head.
3. Publication uses the Design paths under `exports/pickup`, same-directory
   temporary files, flush/fsync, no-follow checks, create-if-absent behavior,
   exact-byte idempotency, and `publication-conflict` for different bytes at
   the same identity. No overwrite or permissive recovery is allowed.
4. Initial, Design, and Plan authority is accepted only from attributable
   `human:<identity>` receipts bound to the exact gate subject and predecessor
   authority. Mechanical progress receipts name an engine producer and never
   a human actor. Content hashing is not actor authentication, and
   `cryptographic_authenticity_claimed` stays false without independently
   verifiable public-key evidence.
5. Validation order is fixed: size/JSON/closed schema; fingerprint/order/
   uniqueness/bounds; repository/source/tracked lineage/clean checkout;
   selected artifacts; predecessor and progress receipts; human authority;
   phase transition; then obligation/task/dependency/scope/contract/
   acceptance/proof closure. Stable refusal codes and safe non-widening
   recoveries are public and path-free.
6. Done work advances only to its declared next phase or Build terminal
   evidence. Interrupted work resumes only Design-to-Design, Plan-to-Plan, or
   Build-to-Build from the last durable receipt. A fresh attempt lease is
   minted; predecessor leases and terminal predecessors are never reopened.
7. Build pickup first validates the complete approved Plan and selects one
   dependency-ready exact task. It then mints a fresh scoped authoring
   bootstrap, dispatches authoring, validates the result and exact Git diff,
   and only afterward calls the existing BUILD-C checkpoint and repository
   integration boundary. A green progress receipt requires both verified
   BUILD-C receipts.
8. The consumer must work from a clean fresh clone with an empty unrelated
   private home. It may not read or serialize `loop.json`, run/track/claim
   state, lease stores, workspace locators, prior conversations, predecessor
   runtime context, secrets, tool logs, or absolute host paths.
9. The known Design-lens transport defect is an implementation obligation in
   scope: after handoff validation, each dispatch receives a fresh
   attempt-local `contract_bootstrap`, `producer_contract`, lease,
   `scoped_view`, closed `result_schema`, and `full_envelope_reference` before
   worker start. No mutable predecessor attempt value is exported or reused.
10. New code retains Python 3.10 compatibility and must be exercised in the
    approved Python 3.14 lane, strict type checking, clean package installation,
    and import/package-edge verification in addition to the focused commands
    recorded in `plan/tasks.json`.

## Dependency impact and Design coverage

The one bounded graph query covered all 24 intended implementation and test/
documentation paths. It reported graph fingerprint
`1c63d34f0d191e21c84151405d48f43bf4553d1e5863f81078b4a83e1db9afe3`
at scanned HEAD `9562e1b8d6d6e5c8ccdb43cecf76a68c49d8d510`, matching the Design baseline.
The graph is non-degraded, depth was not truncated, 29 modules were impacted,
no paths were unknown, and no policy boundary was blocked.

Every task copies the Design depth policy unchanged:
`local_depth=3`, `boundary_mode=contract-only`, `contract_depth=1`, and
`requirement_depth=1`. Cross-entity traversal occurs only through named
contracts. `plan/tasks.json` collectively covers all 24 Design modules, all
seven exact contract IDs, and all 22 canonical `FROM->TO:KIND` edges. The
three Design-declared new files are identified in their owning tasks even
though the graph query returned `unknown=[]` at its component granularity.

## Tasks and order

| Task | Deliverable | Depends on | Acceptance ownership |
|---|---|---|---|
| T-001 | Closed v2 schema, repository artifact verification, canonical identity, atomic publication, and path-free repository receipt projection | — | AC1, AC6 |
| T-002 | Exact gate-authority adapters and bounded Design/Plan startup material, including the dispatch-bootstrap correction | T-001 | AC2, AC3, AC7 |
| T-003 | Stateless pickup coordinator and the strict authoring-before-BUILD-C submission path | T-001, T-002 | AC4, AC9 |
| T-004 | Normal completion exporters and interrupted same-phase durable receipt production, while keeping private stores producer-side | T-001, T-002, T-003 | AC5 |
| T-005 | Public phase CLI and documentation for journeys, authority, compatibility, refusals, recovery, retention, determinism, and privacy | T-001–T-004 | AC11, AC12 |
| T-006 | New isolated fresh-clone, mutation, authority, effect-counter, replay, resume, and semantic severed-edge suite | T-001–T-005 | AC8 |
| T-007 | Legacy route isolation and unchanged v1/v2/cold-start regression floor | T-001–T-005 | AC10 |

T-001 through T-005 are ordered by contract dependency. T-006 and T-007 have
disjoint scopes and may run in parallel after T-005. No two task scopes
overlap. Tests may exercise code owned by predecessor tasks but do not widen
their edit authority.

### Task execution notes

- T-001 owns pure construction/validation and storage mechanics. It must keep
  private `stage-handoff/v1` valid while adding the repository-native v2 and
  must make exact replay produce no new publication.
- T-002 projects real gate decisions without authenticating by hash or
  inventing actors. It produces only fresh successor attempt material after
  validation and keeps product code read-only for Design and Plan workers.
- T-003 owns readiness selection and all effect ports. It compares any Build
  request byte-for-byte to the sealed ordinal task, validates the authored diff
  before checkpoint calls, then uses the incumbent BUILD-C and repository
  owners. Removing that edge or failing focused proof cannot complete a task.
- T-004 wires the same exporter to approved Design, approved Plan, integrated
  Build, and interrupted boundaries. RunStore and loop state may inform the
  producer adapter but never enter the exported value or the consumer graph.
- T-005 exposes stable machine-readable success/refusal fields without private
  paths and documents safe recovery that restores exact evidence or returns to
  the real gate; it never recommends trust overrides or scope expansion.
- T-006 uses real fresh clones, empty unrelated homes, forbidden-access
  sentinels, recursive content scans, and five downstream effect counters. Its
  BUILD-C severed-edge assertion is semantic, not a source-shape check.
- T-007 preserves legacy files and assertions unless a focused compatibility
  fixture is required. The full requirement DoD command is its final test
  command; existing legacy selectors must pass unchanged beside v2.

## Acceptance coverage

Each R-0001 criterion is copied verbatim into exactly one owning task in
`plan/tasks.json`: T-001 owns AC1/AC6; T-002 owns AC2/AC3/AC7; T-003 owns
AC4/AC9; T-004 owns AC5; T-005 owns AC11/AC12; T-006 owns AC8; and T-007 owns
AC10. The new integrated test file supplies cross-cutting proof for all twelve
criteria, while ownership remains unambiguous.

The final acceptance run is:

```text
python3 -m pytest -q taskplane/tests/test_stateless_phase_pickup.py taskplane/tests/test_pickup.py taskplane/tests/test_r0001_pickup_cold_start.py taskplane/tests/test_stage_handoff.py taskplane/tests/test_stage_handoff_security.py taskplane/tests/test_stage_non_build_handoffs.py taskplane/tests/test_stage_loop_integration.py taskplane/tests/test_build_quality.py
```

The final evidence bundle must also show the repository-standard strict typing,
Python 3.10 import floor, Python 3.14 validation lane, clean-wheel install, and
package-edge checks. Broad suite success cannot replace the focused selectors
or the deliberate severed tests.

## Risks, riskiest first

1. **False or widened authority before effects.** A stale/reused human receipt,
   widened Build request, or imported predecessor lease could become execution
   authority. Mitigation: fixed pre-effect validation order, exact subject and
   sealed-task comparisons, fresh attempt contracts, and zero-effect counters.
2. **Build completion bypasses authoring or BUILD-C.** Mitigation: make
   authoring-result validation a prerequisite to the sole coordinator-to-
   BUILD-C edge, require both checkpoint and integration receipts, and test a
   semantically severed edge plus failed focused proof.
3. **Tamper, collision, or partial publication is accepted.** Mitigation:
   closed bounded schemas, canonical bytes, digest verification, no-follow
   containment, fsynced create-if-absent publication, exact replay semantics,
   and conflict/partial-write fault injection.
4. **Private state or host data leaks into a portable handoff.** Mitigation:
   repository-only artifact references, mandatory exclusion fields,
   import/read sentinels, recursive scans, empty-home journeys, and path-free
   public receipt projection.
5. **Successor work regresses legacy pickup v1/v2.** Mitigation: disjoint schema
   and command routing, unchanged legacy trust and refusal order, and the full
   legacy suite beside route-isolation fixtures.
6. **New files are omitted from distributions or fail a supported runtime.**
   Mitigation: explicit new-module ownership plus strict typing, Python 3.10/
   3.14 lanes, clean-wheel installation, and import/package-edge verification.

## Focused Plan lenses

Exactly four quick lenses were selected deterministically and evaluated by the
planner from the Product/Design artifacts and bounded graph result. No lens
workers were launched.

| Selected lens | Why mandatory here | Result applied to tasks |
|---|---|---|
| security | Human-gate attribution, confused-deputy resistance, exact scope, secrets, and pre-effect refusal are explicit requirement signals. | T-002 owns truthful subject-bound authority; T-003 validates sealed scope before effects; T-006 proves zero-effect refusals. |
| architecture | The change spans lifecycle, artifact, CLI, coordinator, BUILD-C, repository, and seven named contracts with 22 designed edges. | Tasks preserve the contract-only depth policy, assign every designed module/edge once, and keep legacy pickup separate. |
| data-safety | Canonical fingerprints, content-addressed artifacts, collision handling, partial writes, and receipt chains are central one-way data decisions. | T-001 owns bounded atomic publication and identity; T-004 owns durable lineage; T-006 injects tamper/collision/partial-write failures. |
| testability | Twelve criteria require fresh-clone, empty-home, severed-edge, mutation, and downstream-effect observations at public seams. | Every task has a runnable focused command; T-006 owns the new deterministic seam suite and T-007 the unchanged compatibility floor. |

### Full 26-lens disposition ledger

| Lens | Disposition | Evidence |
|---|---|---|
| product | not_applicable | Product authority is settled in R-0001 with 12 criteria, named contracts, and no open questions; Plan does not redefine WHAT. |
| security | execute_light | Authority, scope escalation, secrets, artifact integrity, and pre-effect refusal are mandatory and map to T-002, T-003, and T-006. |
| code-quality | not_applicable | No independent code-quality routing signal is needed at Plan; strict typing/runtime/package evidence is already a Design DoD obligation. |
| testability | execute_light | Fresh clones, empty homes, mutation matrices, semantic severing, and effect counters require explicit runnable seams in T-006/T-007. |
| design | not_applicable | There is no interactive visual or UI surface; the machine-readable CLI contract is fully specified by Design. |
| scalability | not_applicable | Work is bounded local filesystem/Git activity with fixed 64-KiB, 64-reference, and 128-KiB ceilings, not a throughput service. |
| integrability | not_applicable | Integration risk is already concretely owned by architecture coverage of BUILD-C, repository, CLI, and legacy-route edges. |
| data-safety | execute_light | Atomic create-if-absent publication, canonical bytes, digest chains, collisions, and partial-write recovery are owned by T-001/T-004/T-006. |
| tech-writer | not_applicable | Documentation is a direct scoped deliverable in T-005 with content enumerated by the requirement; no separate writing judgment is needed. |
| qa | not_applicable | The selected testability lens already covers positive, negative, regression, and severed acceptance proof without a distinct QA risk. |
| devops | not_applicable | No deployment service or workflow change is designed; clean-clone and package/runtime validation are explicit task evidence. |
| dba | not_applicable | No database, migration, query, or index is introduced. |
| sre | not_applicable | The feature is synchronous local CLI work; stable refusal observability and recovery are already covered by security/data-safety tasks. |
| project-management | not_applicable | The seven-task DAG, approval prerequisite, and three-slice-compatible order settle delivery coordination. |
| frontend | not_applicable | No frontend surface exists. |
| backend | not_applicable | No network service or database backend is introduced; Python module boundaries are handled by architecture. |
| tradeoffs | not_applicable | Design already compared three approaches and fixed the selected v2 overlay; Plan introduces no new alternative. |
| solution-design | not_applicable | The validated Design candidate already specifies HOW; Plan copies it without reinterpretation or drift. |
| services-selection | not_applicable | No external service or runtime dependency is added. |
| time-to-market | not_applicable | Reversible rollout slices are already settled and do not change task correctness or dependencies. |
| architecture | execute_light | All 24 modules, seven contracts, 22 edges, and the exact depth policy are assigned across T-001–T-007. |
| mobile | not_applicable | No mobile target exists. |
| accessibility | not_applicable | There is no new human-facing UI; outputs are stable machine-readable CLI envelopes. |
| privacy-compliance | not_applicable | Privacy signals are inseparable from the selected security/data-safety checks and are concretely tested by AC11 sentinels and scans. |
| cost-finops | not_applicable | Fixed bounds and no network/service dependency make resource cost finite and locally testable. |
| i18n | not_applicable | Public identifiers are stable ASCII machine codes; human prose is documentation-only and introduces no localized UI contract. |

## Human decision boundary

The next action belongs to the orchestrator: validate these artifacts and show
the consolidated Design/Plan approval decision to a real human. A rejection or
requested scope change returns to Design/Plan; it must not be hidden inside a
Build task. No task in this plan may mint, reuse, or simulate that approval.
