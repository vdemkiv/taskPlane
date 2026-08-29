# Focused Dynamic Lens Routing — Design Amendment

Status: approved requirement amendment

This amendment supersedes any part of the token-efficiency design that limits dynamic lens routing to Evaluate or Engineering Review.

## Decision

Taskplane routes lenses dynamically at Product, Design, Plan, and Evaluate. Routing is stage-aware and is recalculated from the evidence available at that stage; a route from an earlier stage must not be copied forward unchanged.

```text
request
  │
  ├─ Product ── focused dynamic route ── requirement and AC risks
  │
  ├─ Design  ── focused dynamic route ── solution and contract risks
  │
  ├─ Plan    ── focused dynamic route ── 3–4 executed lenses
  │
  └─ Build ── Evaluate ── focused dynamic route ── 3–4 executed lenses
```

Build and Fix workers do not execute review lenses while editing code. The Evaluate route is computed after the implementation evidence and changed-file set are available.

## Stage policy

| Stage | Routing inputs | Execution policy | Required output |
|---|---|---|---|
| Product | user goal, requirement text, acceptance criteria, domain, declared constraints, product risk | Execute only focused applicable lenses. No fixed 3–4 cap is imposed at this stage; the router must still choose the smallest sufficient set. | Route rationale, selected lenses, and disposition of the full catalog. |
| Design | approved requirements and ACs, proposed components, interfaces, data and trust boundaries, migration and rollback risk | Execute only focused applicable lenses. Solution-design evidence remains mandatory, but it may be produced by the focused route rather than by an unconditional full-catalog sweep. | Route rationale, selected lenses, findings, design changes, and full-catalog disposition. |
| Plan | approved Product and Design artifacts, dependency graph, task scopes, ownership, selectors, validation strategy | Execute exactly 3 or 4 focused lenses for a non-trivial plan. The selected set must cover the highest-ranked independent plan risks. | Per-lens rationale, task/AC coverage map, findings, and full-catalog disposition. |
| Evaluate | task ACs, specification, design contract, actual diff, changed files, dependency-graph impact, test evidence, unresolved findings | Execute exactly 3 or 4 focused lenses per evaluated task or wave. Recalculate after every material Fix; do not rerun unchanged lenses without invalidated evidence. | Per-lens rationale, exact evidence references, findings, and full-catalog disposition. |

For a trivial Plan or Evaluate target with fewer than three genuinely applicable lenses, the router may execute fewer only when it records negative evidence for every omitted slot. This exception must be visible and machine-readable; it is not a silent optimization.

## Routing algorithm

1. Build one canonical review context for the current stage.
2. Score all catalog lenses using:
   - explicit AC and specification concepts;
   - affected components and graph edges;
   - changed-file and semantic diff signals when available;
   - data, security, compatibility, migration, operational, and rollback risk;
   - unresolved upstream findings and evidence invalidation.
3. Apply mandatory risk floors. A mandatory lens cannot be removed merely to satisfy a numerical cap.
4. Remove lenses whose contribution is materially duplicated by a higher-ranked lens for this target.
5. Select the smallest sufficient focused route:
   - Product: dynamic minimum sufficient set;
   - Design: dynamic minimum sufficient set, including solution-design coverage;
   - Plan: 3–4 lenses;
   - Evaluate: 3–4 lenses.
6. Emit a disposition for every catalog lens: `execute_deep`, `execute_light`, `covered_by`, or `not_applicable`, with evidence and reason.
7. Persist the route fingerprint so unchanged evidence can be reused safely.

## Cap overflow

If Plan or Evaluate identifies more than four independent mandatory risks, Taskplane must not silently omit a lens. It must do one of the following:

1. split the target into smaller independently evaluable scopes, each with a 3–4-lens route; or
2. request an explicit human `expanded-lens-route` override that lists the additional lenses and expected cost.

The normal path is scope splitting. An expanded route is exceptional and must not become an automatic all-lens sweep.

## Evidence reuse

A lens result may be reused only when its fingerprint still matches the relevant ACs, specification sections, design edges, changed files, dependency impact, and test evidence. A Fix invalidates only the lenses whose inputs changed. The evaluator reruns the invalidated subset while retaining independently valid evidence.

## Acceptance criteria

- **AC-LR1 — Product routing:** Product computes and executes a focused dynamic lens route from the current requirement and AC evidence; it does not automatically run the full catalog.
- **AC-LR2 — Design routing:** Design computes and executes a focused dynamic route from the approved requirement plus proposed solution, while preserving mandatory solution-design evidence.
- **AC-LR3 — Plan cap:** Every non-trivial Plan validation executes 3 or 4 dynamically selected lenses and records why each was chosen.
- **AC-LR4 — Evaluate cap:** Every non-trivial task or wave Evaluate executes 3 or 4 dynamically selected lenses based on actual implementation evidence.
- **AC-LR5 — No silent dropping:** More than four mandatory Plan/Evaluate risks cause scope splitting or an explicit expanded-route approval.
- **AC-LR6 — Full transparency:** All catalog lenses receive a machine-readable disposition even though only the focused set executes.
- **AC-LR7 — Selective rerun:** A Fix reruns only lenses whose route inputs or evidence fingerprints were invalidated.
- **AC-LR8 — Determinism:** Equal canonical inputs and policy version produce the same ordered route and fingerprint.
- **AC-LR9 — Observability:** Stage, selected count, selection reasons, estimated/actual tokens, runtime, cache reuse, and invalidation cause are recorded for each route.
- **AC-LR10 — No editing-time fan-out:** Build and Fix agents do not launch lens workers; lens execution occurs at the four decision/review points defined above.

## Required tests

- deterministic routing for identical Product, Design, Plan, and Evaluate contexts;
- stage-specific inputs change the selected route where expected;
- Plan and Evaluate select 3–4 lenses for non-trivial targets;
- fewer-than-three exception requires negative evidence;
- cap overflow cannot silently discard a mandatory lens;
- a material Fix invalidates only affected lens evidence;
- unchanged evidence is reused without launching a new worker;
- full-catalog disposition is complete and internally consistent;
- explicit all-deep calibration remains separate from normal focused routing.

## Non-goals

- Removing the 26-lens catalog.
- Running review lenses inside implementation workers.
- Treating a complete disposition ledger as an instruction to execute all lenses.
- Reusing a Product or Design route blindly at Plan or Evaluate.
