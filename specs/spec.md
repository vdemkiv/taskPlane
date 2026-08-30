# Focused dynamic lens routing across Taskplane stages

## Product authority

This specification promotes the approved requirement amendment in
`docs/token-efficiency-lens-routing-amendment.md` and its machine-readable
companion to canonical Product authority. It supersedes behavior or wording
that limits dynamic lens routing to Evaluate/Engineering Review or treats a
complete 26-lens disposition ledger as an instruction to execute all lenses.

The authorized delivery is local only. It does not authorize pushing,
merging to main, tagging, packaging, publishing, or releasing.

## Problem

Taskplane's routing policy is inconsistent across decision stages: focused
routing exists around review, while Product, Design, and Plan can still use
fixed or full-catalog execution patterns. That spends review work where it
adds no independent coverage and prevents safe evidence reuse after a Fix.

## Users and context

- Taskplane users need governed delivery to remain transparent without paying
  for unnecessary lens executions.
- Product and Design workers need stage-specific evidence, not a route copied
  from an earlier stage.
- Planners and evaluators need a small, deterministic route that covers the
  highest-ranked independent risks and fails visibly when four lenses are
  insufficient.
- Build and Fix workers need uninterrupted implementation scope; review lens
  execution belongs at decision and evaluation boundaries.
- Maintainers need machine-readable routing evidence, selective invalidation,
  and stable observability for cost and quality analysis.

## In scope

1. Focused dynamic routing at Product, Design, Plan, and Evaluate.
2. A stage-specific canonical review context and deterministic ordered route.
3. Minimum-sufficient focused execution at Product and Design, with mandatory
   solution-design coverage at Design.
4. Exactly three or four executed lenses for every non-trivial Plan or
   Evaluate target.
5. Machine-readable negative evidence when a genuinely trivial Plan or
   Evaluate target executes fewer than three lenses.
6. Complete evidenced disposition of the 26-lens catalog using
   `execute_deep`, `execute_light`, `covered_by`, or `not_applicable`.
7. Scope splitting or explicit authenticated `expanded-lens-route` approval
   when more than four independent mandatory risks apply.
8. Fingerprinted route and evidence reuse, with a Fix invalidating only the
   evidence whose canonical inputs changed.
9. Route telemetry for stage, selection, cost, runtime, reuse, and
   invalidation.
10. Enforcement that Build and Fix launch no lens workers.

## Out of scope

- Removing, reducing, or renaming the 26-lens catalog.
- Automatically executing the full catalog at any normal stage.
- Running lens workers from Build or Fix agents while they edit code.
- Blindly reusing Product or Design routes at Plan or Evaluate.
- Weakening mandatory security, architecture, solution-design, or other risk
  floors to satisfy a numerical cap.
- Treating an unauthenticated request as an expanded-route approval.
- Changing the explicit all-deep calibration/audit surface into the normal
  focused workflow.
- Unrelated Taskplane refactors or correction of historical review findings.
- Push, merge to main, tag, package, publish, release, or CI waiting.

## Functional requirements

1. Build one canonical, versioned routing context from the evidence available
   at the current stage; never carry an earlier stage's selected route forward
   unchanged.
2. Score all 26 catalog lenses against acceptance/specification concepts,
   affected components and dependency edges, applicable diff signals, boundary
   and rollback risk, unresolved findings, and evidence invalidation.
3. Apply mandatory risk floors before final selection; a cap cannot silently
   remove a mandatory lens.
4. Remove materially duplicated contribution by recording `covered_by` with
   the covering lens and evidence.
5. Product and Design select the smallest sufficient focused set. Design must
   retain solution-design coverage.
6. Non-trivial Plan and Evaluate targets execute exactly three or four lenses
   covering the highest-ranked independent risks.
7. A trivial Plan/Evaluate route with fewer than three executions must emit a
   machine-readable exception and negative evidence for each omitted slot.
8. More than four independent mandatory risks must split the target into
   independently evaluable scopes or stop for an explicit authenticated
   `expanded-lens-route` approval listing the additional lenses and cost.
9. Every route emits a complete 26-lens disposition ledger with evidence and
   reason, plus an ordered route fingerprint derived from canonical inputs and
   policy version.
10. A Fix recalculates the Evaluate route, retains fingerprint-matching lens
    evidence, and reruns only the invalidated subset.
11. Build and Fix dispatch records must assert and prove zero lens-worker
    starts.
12. Explicit all-deep calibration remains available only as a separate,
    human-selected audit path.

## Acceptance criteria

1. **AC-LR1 — Product routing.** Given Product goal, requirement, acceptance,
   domain, constraint, and product-risk evidence, Product executes the
   deterministic minimum-sufficient focused route and emits all 26
   dispositions. Verify with identical-input determinism and product-risk
   mutation tests; normal Product routing must not launch a full-catalog run.
2. **AC-LR2 — Design routing.** Given the approved requirement and a proposed
   solution context, Design executes a minimum-sufficient focused route that
   includes solution-design coverage and emits findings, design changes, and
   all 26 dispositions. Verify by changing an interface, trust boundary, and
   rollback risk independently and observing attributable route changes.
3. **AC-LR3 — Plan cap.** Every non-trivial Plan validation executes exactly
   three or four dynamically selected lenses, records why each lens was
   chosen, and maps the selected coverage to tasks and acceptance criteria.
   Verify positive cases at three and four plus refusal of two or five.
4. **AC-LR4 — Evaluate cap.** Every non-trivial task or wave Evaluate executes
   exactly three or four lenses selected from actual diff, changed files,
   dependency impact, test evidence, and unresolved findings. Verify the
   route changes when material implementation evidence changes.
5. **AC-LR5 — No silent dropping.** When Plan or Evaluate identifies more
   than four independent mandatory risks, the normal route either splits the
   scope or refuses pending an authenticated expanded-route approval that
   lists additional lenses and expected cost. Mutation tests must prove no
   mandatory lens disappears silently and tampered approval is rejected.
6. **AC-LR6 — Full transparency.** Every Product, Design, Plan, and Evaluate
   route contains exactly one internally consistent, evidenced disposition
   for every catalog lens, while only `execute_deep` and `execute_light`
   selections launch workers. Verify missing, duplicate, unsupported, and
   unevidenced dispositions fail closed.
7. **AC-LR7 — Selective rerun.** After a material Fix, only lens evidence
   whose fingerprint inputs changed is invalidated and rerun; independently
   valid evidence is retained without launching a new worker. Verify with
   single-input, multi-input, and unchanged-input cases.
8. **AC-LR8 — Determinism.** Equal canonical inputs and routing-policy version
   produce the same ordered route, dispositions, and fingerprint across
   Product, Design, Plan, and Evaluate. Verify repeated and key-order-varied
   contexts; changing a relevant input or policy version must change the
   fingerprint.
9. **AC-LR9 — Observability.** Each route records stage, selected count,
   per-lens reason, estimated and actual tokens, runtime, cache reuse, and any
   invalidation cause without persisting secrets or raw private content.
   Verify schema completeness, bounded values, and redaction behavior.
10. **AC-LR10 — No editing-time fan-out.** Build and Fix workers launch no
    lens workers, and lens execution occurs only at Product, Design, Plan, and
    Evaluate. Verify native-dispatch traces for successful, failed,
    cancelled, interrupted, and handed-off Build/Fix attempts all report zero
    lens starts.

## Non-functional requirements

- **security:** Expanded-route authority is narrowly scoped, authenticated,
  tamper-evident, target-bound, and cannot weaken general contract or mandatory
  lens enforcement.
- **architecture:** One dependency-neutral routing policy consumes canonical
  stage contexts and exposes versioned decisions; Product, Design, Plan, and
  Evaluate adapt through explicit boundaries, while Build/Fix cannot depend on
  lens-worker dispatch.
- **data-safety:** Route, disposition, approval, reuse, and invalidation records
  are atomic and fail closed; no mandatory risk or evidence is silently lost.
- **sre:** Routing and worker fan-out are bounded; overflow, cancellation,
  interruption, handoff, and partial evidence produce truthful terminal state.
- **cost-finops:** Normal execution is minimum sufficient, Plan/Evaluate are
  capped at three or four absent explicit override, and valid cached evidence
  does not launch duplicate workers.
- **integrability:** Existing explicit all-deep calibration remains separate
  and supported; versioned routing artifacts have deterministic compatibility
  and migration behavior.
- **privacy-compliance:** Telemetry stores bounded structured metrics and
  redacted reasons, not secrets, raw prompts/diffs, workstation identity, or
  absolute private paths.

## Contract handoff

```yaml
scope_paths:
  - taskplane/lens.py
  - taskplane/lens_signals.py
  - taskplane/review.py
  - taskplane/loop.py
  - taskplane/dispatch_telemetry.py
  - taskplane/taskplane_lite.py
  - taskplane/tp.py
  - taskplane/tests/**
  - agents/**
  - skills/taskplane/**
  - skills/tp-*/**
  - docs/routing-and-flows.md
  - docs/lenses-and-knowledge.md
  - docs/lens-catalog.md
  - README.md
out_of_scope:
  - release and publication surfaces
  - unrelated review-remediation work
  - lens-catalog removal or reduction
  - implementation inside Build/Fix lens workers
contracts:
  - contract:lens.focused-stage-routing
  - contract:review.catalog-disposition
  - contract:delivery.stage-lens-execution
  - contract:authority.expanded-lens-route
  - resource:review.route-fingerprint
  - resource:telemetry.lens-route
dod:
  test_command: >-
    python3 -m pytest taskplane/tests/test_lens.py
    taskplane/tests/test_review_routing.py
    taskplane/tests/test_loop.py
    taskplane/tests/test_dispatch_telemetry.py -q
```

## Dependencies and open questions

- Requirement dependencies: none.
- Open questions: none.
