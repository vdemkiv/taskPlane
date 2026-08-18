# Specification — progressive, convergent engineering review

## Problem

Taskplane engineering review currently pays exhaustive-review cost before it
can return an obvious request-changes decision, repeats some non-substantive
work, and treats infrastructure, workspace identity, documentation routing,
and fix-cycle limits too coarsely. The accepted retrospective requires faster
truthful feedback without weakening evidence, slot conservation, lens floors,
or the human approval boundary.

## Users and context

Engineers use Taskplane to judge implementation quality, repair review defects,
and decide whether work is approvable. They need high-risk evidence first,
early actionable request-changes output, deterministic recovery from mechanical
producer defects, and measurable lens value. This requirement follows R-0011's
accepted retrospective and changes review execution policy only; it does not
change product requirements, lens charters, or final approval authority.

## In scope

- Progressive risk-first review: catalog-declared mandatory deep floors, the
  initially applicable deep lenses, and at most one bounded light sweep.
- Evidence-triggered promotion of relevant light-sweep concerns into named deep
  lenses without exhaustive up-front dispatch.
- Early canonical provisional request-changes revisions once admissible
  blocking evidence exists, with every undispatched, running, missing, invalid,
  or infrastructure-blocked slot represented as an explicit gap.
- Full selected/prepared/dispatched/produced/collected slot conservation and
  complete required evidence before approval can become available.
- Deterministic normalization of producer summary/count/verdict metadata and
  audited metadata-only repair that never changes substantive findings.
- Reuse of evaluator-infrastructure failure evidence only for the same engine
  identity and exact worktree/root context, with safe invalidation.
- Exact binding of every review/evaluation artifact to repository identity,
  worktree root, target/revision, engine identity, run, lease, slot, and
  producer.
- Documentation-aware lens routing that recognizes documentation evidence and
  does not widen to a module-level full-catalog review merely because code-
  module mapping is unavailable or inapplicable.
- Convergence-based fix continuation/escalation rather than one global two-
  cycle limit, while preserving bounded, observable, human-governed recovery.
- Per-lens telemetry for quality, unique value, latency, cost, overlap, false-
  positive outcomes, retries, and infrastructure unavailability.

## Out of scope

- Changing the 26-lens catalog, lens ownership boundaries, finding severity
  definitions, or catalog-declared mandatory floors.
- Removing the light sweep, running more than one light sweep per canonical
  review, or promoting a lens without attributable evidence.
- Allowing provisional output, partial collection, a blocker alone, or a
  request-changes recommendation to become approval/pass.
- Dropping valid findings/evidence because an early revision was published,
  silently excluding selected slots, or relaxing lease/producer/schema checks.
- Substantively rewriting a producer's verdict, finding, severity, rationale,
  evidence, or action under the label of normalization or repair.
- Sharing cached evaluator failures across mismatched engines, worktrees,
  targets, capabilities, or expired evidence windows.
- Treating missing module mapping as proof that every lens is applicable, or
  treating documentation-only change as no review needed.
- Unlimited fix loops, silent retry, automatic human decisions, or a fixed
  numeric cycle limit that ignores convergence evidence.
- Using telemetry alone to suppress mandatory review, grade an individual
  worker, or overwrite canonical findings.
- Redesigning Product/Design/Build flows, dashboards, host-native UI, release,
  marketplace, dependency graph, or repository storage generally.

## Acceptance criteria

1. **Mandatory floors always run.** Every engineering review dispatches every
   catalog-declared mandatory deep floor applicable to the stage, regardless of
   diff size, early blockers, documentation-only scope, cached failures, or
   provisional publication. **Verify:** code, docs-only, mixed, empty-map,
   high-risk, low-risk, and early-blocker fixtures assert the exact mandatory
   floor slots are selected and conserved.

2. **Initial review is progressive and bounded.** The first wave contains the
   mandatory floors, other deep lenses selected from current evidence, and at
   most one bounded light sweep; it does not dispatch every catalog lens merely
   to establish that most are not applicable. **Verify:** representative small,
   large, code, configuration, and documentation changes assert initial slot
   membership, one-or-zero light sweeps, and machine-readable n/a evidence.

3. **Deep promotion requires attributable evidence.** A light-sweep concern
   promotes a named lens to deep review only when the sweep supplies a
   normalized risk/severity signal, affected evidence reference, rationale,
   and promotion trigger within that lens's charter. Promotions are
   deterministic and idempotent. **Verify:** high/major, low/minor, duplicate,
   cross-charter, missing-evidence, and replay fixtures assert promoted slots
   and trigger records.

4. **High-risk sweep evidence is followed through.** Every admissible high or
   major light-sweep concern is either promoted to the responsible deep lens or
   explicitly rejected with a canonical reason proving it is duplicate,
   out-of-charter, invalid, or already covered. **Verify:** a mixed sweep leaves
   no unexplained high/major concern and never counts the sweep itself as the
   required deep judgment.

5. **Blocking evidence publishes early request-changes.** Once a valid blocker
   or request-changes-level finding is canonically admissible, Taskplane may
   publish an immutable provisional canonical revision immediately without
   waiting for exhaustive collection. It contains the complete known evidence,
   recommendation rationale, and current gate state. **Verify:** blocker-first,
   blocker-late, simultaneous, duplicate, repaired, and withdrawn-invalid
   finding fixtures assert publication timing and revision lineage.

6. **Early revisions expose every gap.** A provisional request-changes revision
   enumerates every selected slot as collected, running, undispatched,
   promoted-pending, missing, invalid, retrying, or infrastructure-unavailable,
   with reason and recovery status. It never reports an incomplete lens set as
   exhaustive. **Verify:** mixed lifecycle fixtures reconcile the gap manifest
   to the selected-slot ledger exactly.

7. **Approval requires full conservation.** Approval/pass is unavailable until
   every required selected slot and promotion is prepared, dispatched,
   produced, validated, and canonically collected exactly once, all acceptance
   evidence is complete, and no unresolved gap remains. **Verify:** mutation
   tests remove or duplicate one record at each lifecycle stage and assert the
   gate remains closed; the conserved complete case alone can proceed.

8. **Early publication never loses later evidence.** Subsequent results,
   promotions, repairs, retries, and dynamic evidence supersede the provisional
   revision through immutable lineage while retaining every previously valid
   finding and provenance record unless a canonical adjudication explicitly
   invalidates it. **Verify:** multi-revision fixtures round-trip all findings,
   gaps, adjudications, and artifact identities without duplication or loss.

9. **Producer summaries normalize deterministically.** For the same valid slot
   payload, summary verdict, finding counts, severity counts, and completion
   metadata normalize to one byte-stable representation derived from the
   canonical findings/schema invariants, independent of producer prose or
   field order. **Verify:** permuted, omitted-summary, contradictory-summary,
   empty, fail-with-findings, and pass-with-zero fixtures produce the expected
   normalized metadata or a named substantive inconsistency.

10. **Mechanical repair is metadata-only.** An unresolved but authoritatively
    derivable identity, count, summary, or schema metadata field may be repaired
    without rerunning substantive review only when target, view, evidence,
    findings, severities, rationale, actions, verdict substance, lease, slot,
    and producer remain byte-equivalent. The repair records before/after,
    derivation authority, and fingerprint proof. **Verify:** each permitted
    metadata defect repairs once; every substantive mutation is rejected and
    routes only the affected slot for rerun.

11. **Infrastructure failures are cached at the correct boundary.** A verified
    evaluator-infrastructure failure is reused within its validity window only
    for matching evaluator, engine fingerprint/version, host capability,
    repository identity, and exact worktree root. Reuse produces an explicit
    cached-unavailable record rather than another evaluator launch or a lens
    pass/fail. **Verify:** repeated identical attempts invoke infrastructure
    once, while changed engine, evaluator, capability, repository, worktree,
    expiry, or repaired infrastructure invalidates the cache.

12. **Evidence is bound to the exact execution root.** Every producer and
    evaluator artifact records and verifies canonical repository identity,
    exact Git worktree root, target/base/head or equivalent revision, engine
    fingerprint, run, lens, slot, lease, and producer. Sibling worktrees and a
    parent/root checkout cannot substitute for each other. **Verify:** root,
    linked-worktree, sibling, moved-root, symlink, clone, engine-skew, stale-
    head, copied-result, and valid-resume fixtures fail or pass as expected.

13. **Documentation routing is evidence-aware.** Documentation-only and mixed
    changes route from document content, requirement/directive signals,
    declared contracts, affected audiences, and known graph evidence. Missing
    or inapplicable code-module mapping is recorded explicitly and does not by
    itself widen routing to every module-level lens. **Verify:** API docs,
    security guidance, runbook, user docs, changelog, typo-only, mixed code/docs,
    malformed-doc, and absent-map fixtures assert relevant deep/light/n/a
    dispositions without full-catalog fail-open widening.

14. **Routing uncertainty remains safe.** When documentation evidence is
    genuinely ambiguous or corrupt, mandatory floors remain and the review
    records the uncertainty plus the smallest evidence-backed widened set; it
    never equates uncertainty with no review or unconditional all-lens review.
    **Verify:** controlled ambiguity fixtures assert monotonic, explained,
    bounded widening and stable routing fingerprints.

15. **Fix cycles follow convergence, not a global count.** After each fix
    evaluation, Taskplane records which admissible blockers/findings closed,
    persisted, regressed, or newly appeared, plus evidence/test progress. It
    may continue while measurable progress exists and recovery remains safe,
    even beyond two cycles; it escalates when progress stalls, repeats, worsens,
    exceeds a task-specific bound, crosses authority/scope, or becomes unsafe.
    **Verify:** fast convergence, three-plus-cycle convergence, no-progress,
    oscillation, regression, repeated-fingerprint, unsafe, scope-change, and
    human-stop fixtures assert continue/escalate decisions and audit reasons.

16. **Convergence does not weaken human authority.** No convergence score,
    elapsed cycle count, early request-changes revision, or cached failure may
    auto-approve, silently abandon a required slot, widen implementation scope,
    or bypass a human decision required by the governed loop. **Verify:** gate,
    scope, destructive-action, and final-signoff fixtures remain human-owned.

17. **Per-lens telemetry is complete and comparable.** Each lens/run records
    eligible/selected/promoted/collected status, admissible and confirmed
    finding counts, unique findings, overlaps/duplicates, later-invalidated or
    false-positive outcomes, retries/repairs, latency, token usage/cost when
    available, and infrastructure unavailability, with versioned definitions
    and denominators. **Verify:** golden arithmetic reconciles raw events to
    aggregate metrics and distinguishes unavailable telemetry from zero.

18. **Telemetry preserves review independence.** Quality/efficiency telemetry
    is derived from sealed canonical revisions and later adjudication, does not
    reveal one lens's draft to another, alter the current review verdict, or
    suppress mandatory floors. **Verify:** ordering and information-isolation
    fixtures show identical substantive results with telemetry enabled or
    disabled.

19. **Host and legacy behavior remain compatible.** Claude, Codex, managed
    worktrees, and supported fallback paths preserve the same canonical review
    semantics, evidence, early-revision rules, conservation gate, repair rules,
    convergence decisions, and telemetry definitions. **Verify:** cross-host
    golden scenarios and the existing review/loop suites pass without removed,
    skipped, xfailed, loosened, or reclassified governance assertions.

## Non-functional requirements

- `security`: Evidence, normalization, repair, caching, routing, and telemetry
  remain target/run/lease/producer bound and fail closed; cached failures or
  metadata repair cannot forge substantive review, cross worktree boundaries,
  expose secrets, or enable approval.
- `architecture`: One canonical slot ledger, immutable revision lineage,
  evidence-binding model, normalization contract, routing record, convergence
  record, and telemetry schema serve all hosts and worktrees without parallel
  review authorities.
- `data-safety`: Valid evidence and findings survive provisional publication,
  retry, repair, and supersession losslessly; exact-root validation prevents
  copied, stale, sibling-worktree, or engine-skew evidence from being accepted.
- `sre`: Progressive waves, promotions, retries, cache validity, and convergence
  have bounded deterministic states, actionable diagnostics, explicit expiry,
  and no infinite review/fix loop or repeated known infrastructure launch.
- `integrability`: Changed review, revision, repair, routing, evidence-binding,
  convergence, and telemetry contracts are versioned and remain compatible
  across Claude, Codex, existing artifacts, and supported legacy consumers.
- `scalability`: Initial dispatch scales with risk and evidence rather than the
  full lens catalog; revisions and telemetry remain bounded/indexable across
  large reviews, many slots, promotions, and fix cycles without losing detail.
- `cost-finops`: Per-lens latency/token/cost and avoided launches are measured
  with provider-correct availability; cached infrastructure failures,
  progressive dispatch, and affected-slot repair avoid redundant model spend.
- `privacy-compliance`: Evidence paths, worktree identities, diagnostics,
  caches, and telemetry retain minimum audit identifiers while redacting
  personal absolute paths, credentials, secrets, prompts, and unrelated data.

## Contract handoff

- `scope_paths`:
  - `taskplane/review.py`
  - `taskplane/review_evidence.py`
  - `taskplane/review_dor.py`
  - `taskplane/lens.py`
  - `taskplane/lens_signals.py`
  - `taskplane/views.py`
  - `taskplane/runtime_eval.py`
  - `taskplane/taskplane_lite.py`
  - `taskplane/loop.py`
  - `taskplane/spend.py`
  - `taskplane/dashboard.py`
  - `agents/tp-engineering.md`
  - `agents/tp-lens.md`
  - `skills/tp-engineering/**`
  - `skills/tp-go/**`
  - `docs/routing-and-flows.md`
  - `docs/authority-matrix.md`
  - `docs/storage-and-repositories.md`
  - `docs/retrospective-2026-08-15-evaluation-loop.md`
  - `taskplane/tests/**`
  - `specs/spec.md`
- `out_of_scope`: lens-catalog/charter changes, product/design/build workflow
  redesign, graph redesign, host-native UI, release/marketplace work, automatic
  approval, unlimited fixing, and any evidence/control weakening.
- `dod.test_command`: `python3 -m pytest taskplane/tests -q`
- dependency: `R-0011`.
- contracts:
  - `contract:review-kernel-slot`
  - `contract:review-kernel-partial-revision`
  - `contract:review-kernel-mechanical-repair`
  - `contract:review-risk-progression`
  - `contract:review-evidence-binding`
  - `contract:evaluator-infrastructure-health`
  - `contract:review-fix-convergence`
  - `contract:lens-quality-telemetry`

This is a cross-module review-policy and evidence-contract change. It requires
Design before Build. There are no blocking Product questions.
