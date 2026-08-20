# Specification — consolidated governed delivery and convergent review

## Problem

Taskplane currently asks for too many routine approvals and can interrupt
otherwise recoverable work, while engineering review still needs the accepted
risk-first, evidence-complete efficiency controls. Users need one attributable
authorization that carries bounded work end to end, automatic mechanical gates
and recovery, and human attention reserved for genuinely new authority,
selection, escalation, irreversible action, or final sign-off.

## Users and context

Engineers use the Taskplane facade, governed delivery, Product, Design, Build,
engineering review, Status, Help/onboarding, Product's strategic advisory, and
Claude-tag/Slack entry points. This amendment consolidates attributable human
feedback from all ten flows into R-0001 while preserving its progressive-review
requirements: exact evidence/worktree binding, evaluator-outage caching,
documentation-aware routing, early provisional revisions, convergence-based
fixing, per-lens telemetry, complete evidence, and full conservation before
approval.

## In scope

- One consolidated, attributable pre-implementation authorization packet for
  accepted requirements, conditional design, plan, dynamic validation,
  ordinary sandbox work, routine fixes/recovery, evaluation, collection,
  artifact delivery, and bounded end-to-end execution.
- Mechanical Product, Design, and Plan gates that auto-advance when their
  contracts, graph checks, acceptance mapping, and required lenses pass.
- A closed set of events that return to a human: consolidated authorization,
  explicit A/B selection, exhausted/non-convergent recovery or replan,
  material scope/authority change, destructive/irreversible or new external
  authority, changed acceptance criteria or weakened gate, and final sign-off.
- Automatic repository preparation, bounded routine recovery, phase ownership,
  isolated parallel execution, status telemetry, onboarding repair, and
  attributed thread continuation across Codex, Claude, and Slack-capable flows.
- Progressive risk-first engineering review with one attributable deep floor
  for documentation-only/simple low-risk work and four deep floors for
  substantive, risky, mixed, or evidenced ambiguous/corrupt work,
  one light sweep, evidence-triggered deep promotions, early provisional
  request-changes, metadata-only repair, affected-slot retry, exact evidence
  binding, outage caching, documentation-aware routing, convergence policy,
  and per-lens efficiency/quality telemetry.
- Small inline dashboards and complete Markdown delivery for very large
  dashboards, with JSON as machine authority and HTML optional/nonblocking.

## Out of scope

- Silence, inferred intent, or prior unrelated approval granting new authority.
- Auto-approving final sign-off, A/B selection, material scope/authority
  change, destructive/irreversible action, external credentials/publication/
  spend, changed acceptance criteria, or gate-weakening recovery.
- Weakening orchestrator-only gates, evidence-bound submissions, isolated
  worktrees, acceptance evidence, slot conservation, or audit attribution.
- Asking users to choose Product/Design/Engineering personas or exposing
  north-star as a separate required user flow.
- Unbounded recovery loops, global silent failure, fabricated progress/tokens/
  ETA, or treating unavailable telemetry as zero.
- Removing mandatory review floors, allowing a light sweep to substitute for a
  required deep judgment, or requiring exhaustive collection before a truthful
  provisional request-changes revision.
- Dropping valid evidence/findings during early publication, normalization,
  repair, retry, cache reuse, supersession, or large-dashboard delivery.
- Broad product-feature changes, lens-catalog/charter changes, dependency-graph
  redesign, model/billing redesign, or release/marketplace publication.

## Acceptance criteria

1. **One authorization covers routine end-to-end work.** A consolidated packet
   identifies the requirement, conditional design/plan, target and scope,
   acceptance criteria, dynamic-validation intent, ordinary sandbox authority,
   routine fix/recovery policy, evaluation/collection, artifact delivery, and
   execution bounds. One attributable approval authorizes those unchanged
   activities through final-signoff readiness. **Verify:** all ten flow fixtures
   complete routine stages without another approval and retain one receipt plus
   stage-by-stage authority derivation.

2. **Mechanical definition gates auto-advance.** Product, Design, and Plan
   stages automatically complete/score/check and advance when their contracts,
   graph checks, acceptance mapping, required design/review lenses, and evidence
   pass. They do not request separate Product, Design, or Plan approval.
   **Verify:** pass/fail fixtures assert automatic advance only on complete
   evidence and a named non-human blocker when mechanical checks fail.

3. **Human-attention boundaries are closed and explicit.** A new human decision
   occurs only for initial consolidated authorization; explicit A/B selection;
   exhausted/no-progress recovery or replan; material scope creep or major
   authority change; destructive/irreversible action; new external system,
   credential, publication, or spend; changed acceptance criteria; recovery
   that weakens a gate; or final sign-off. **Verify:** an approval trace for
   every flow contains no other prompt and each allowed prompt names the new
   fact, consequence, and authority requested.

4. **Silence never expands authority.** Missing, stale, ambiguous, or
   unauthenticated responses pause only the affected human-owned decision and
   cannot authorize additional scope, external effects, destructive action,
   changed criteria, weakened controls, or final approval. **Verify:** timeout,
   replay, wrong-thread, wrong-revision, and free-form ambiguous fixtures.

5. **Routine recovery is automatic and bounded.** Within authorized scope,
   Taskplane retries or mechanically repairs transient, metadata, evaluator,
   collection, artifact, render, and setup failures automatically two to three
   times or while measurable convergence continues. It asks only after the
   bounded policy is exhausted, progress stops/oscillates/worsens, safety or
   authority changes, or replan is needed. **Verify:** one-, two-, three-,
   converging-longer, no-progress, repeated-fingerprint, unsafe, and authority-
   change cases assert recover/escalate behavior and reasons.

6. **Ownership and parallelism remain governed.** Each phase has one accountable
   owner; parallel agents/subagents run only in isolated worktrees with bounded
   scopes, evidence-bound submissions, and orchestrator-only gates. **Verify:**
   serial, parallel, overlapping-scope, sibling-worktree, crashed-owner, copied-
   evidence, and attempted-worker-gate fixtures preserve ownership and isolation.

7. **Facade performs preparation automatically.** Repository acquisition,
   managed checkout/worktree preparation, target/ref pinning, and verification
   run without a user prompt when existing authority is sufficient. Failures
   classify into automatic recovery or a named genuine authority/external
   boundary. **Verify:** local, remote, cached, stale, moved, auth-required, host-
   policy, and external-unavailable repository fixtures.

8. **Facade preserves intent and hides persona plumbing.** User intent, current
   loop state, and authority deterministically select Product/Design/Build/
   Engineering/Status/Help behavior; the user is never asked which persona to
   invoke. **Verify:** ambiguous-language/state matrix routes or asks one
   substantive clarification without exposing internal role selection.

9. **Dashboard delivery is size-appropriate and complete.** Small dashboards
   render inline. Very large dashboards automatically deliver complete Markdown
   without truncating evidence; canonical JSON remains machine authority and
   HTML failure/absence is optional and nonblocking. **Verify:** below/above
   thresholds and large-finding fixtures compare semantic equality and gate
   state across JSON, inline, Markdown, and optional HTML.

10. **Design and Plan share one authorization packet.** High-impact work may
    retain a separate Design phase, but Design and Plan are presented together
    in the consolidated pre-implementation packet; mechanically passing stages
    do not request another approval. Non-material evolution within accepted
    requirements/contracts proceeds automatically. **Verify:** high/low impact,
    material/non-material drift, contract-preserving, and contract-changing
    scenarios assert packet contents and reauthorization boundaries.

11. **Product completes refinement automatically.** Product records and scores
    the complete requirement and feeds it into the consolidated packet without
    a standalone Product approval. Missing acceptance, contract, dependency,
    or NFR evidence blocks mechanically rather than asking for ceremonial
    approval. **Verify:** complete and gap fixtures assert score/evidence and
    transition behavior.

12. **North-star is conditional internal advice.** Product invokes north-star
    internally only for strategic ambiguity, high opportunity cost,
    irreversible direction, or an explicit request. It is advisory and never a
    gate or separate user-facing flow. Its concise note always contains
    alignment, leverage, reversibility, opportunity cost, coherence, sharpest
    tension, and recommendation. **Verify:** trigger/non-trigger fixtures and
    schema checks; absence or disagreement cannot independently block work.

13. **Build preserves the intended selection boundary.** A single variant uses
    consolidated authorization end to end. When A/B variants were explicitly
    chosen, variant selection is the only ordinary mid-build human gate and
    each variant retains isolated comparable evidence. **Verify:** single, A/B,
    invalid variant, stale selection, and resumed selection fixtures.

14. **Preview feedback becomes an attributable scoped change.** Human feedback
    from a design/build preview is recorded as an attributable scoped change
    request against the current requirement/target. Non-material in-contract
    feedback proceeds automatically; changed acceptance, material scope, or
    authority returns to consolidated authorization. **Verify:** cosmetic,
    behavioral, acceptance-changing, scope-expanding, and unauthenticated input.

15. **Deep-review floors scale with attributable risk.** Documentation-only
    and simple low-risk changes run exactly one risk-selected deep review lens.
    Missing/inapplicable code-module mapping alone does not widen that review.
    Substantive or risky changes retain architecture, code-quality, security,
    and QA as four mandatory deep floors; genuinely ambiguous or corrupt
    evidence widens to those floors with an explicit evidence-backed reason.
    Cache, light sweep, early provisional publication, or another lens cannot
    replace the deep slot or slots required for the change's risk class.
    **Verify:** documentation-only, simple low-risk, substantive, risky, mixed,
    early-blocker, mapping-gap, ambiguous, and corrupt fixtures assert exact
    risk classification, lens selection, reason, and slot conservation.

16. **Other review is progressive.** Non-floor lenses begin through at most one
    bounded light sweep and promote to named deep review only from attributable
    normalized evidence within the lens charter. Every high/major concern is
    promoted or explicitly rejected as duplicate, invalid, out-of-charter, or
    already covered. **Verify:** severity, duplicate, replay, missing-evidence,
    cross-charter, and mixed-sweep fixtures.

17. **Severe harm publishes request-changes immediately.** An admissible
    Blocker, High, any security vulnerability, or harmful/destructive bug
    publishes an immutable provisional request-changes revision immediately,
    preserving complete known evidence and explicit lifecycle gaps. Exhaustive
    collection is not required for this recommendation. **Verify:** each trigger,
    non-trigger severities, invalidated finding, duplicate, and simultaneous
    finding fixtures assert timing and lineage.

18. **Approval still requires complete conservation.** Approval/pass remains
    unavailable until every mandatory/selected/promoted slot is prepared,
    dispatched, produced, validated, and collected exactly once, all acceptance
    evidence is complete, and no unresolved gap remains. Request-changes may be
    provisional; approval may not. **Verify:** remove/duplicate every lifecycle
    record and assert only the complete conserved case is approvable.

19. **Normalization and repair never redo substance.** Producer verdict/count/
    severity/summary metadata normalizes deterministically from canonical
    findings. Authoritatively derivable metadata-only defects repair
    automatically with before/after, derivation authority, and fingerprint
    equivalence; substantive change reruns only affected slots. **Verify:** field
    permutation, summary contradiction, identity/count defect, and every
    substantive mutation.

20. **Exact execution-root evidence binding remains.** Every review/evaluation
    artifact verifies repository identity, exact Git worktree root, target/base/
    head, engine fingerprint, run, lens, slot, lease, and producer. Parent,
    sibling, moved, symlinked, cloned, engine-skewed, stale, or copied evidence
    cannot substitute. **Verify:** negative topology matrix and valid resume.

21. **Evaluator-outage cache is exact and truthful.** A verified evaluator
    infrastructure failure reuses only for the same evaluator, engine/version,
    capability, repository, exact worktree, and validity window. It records
    infrastructure unavailable—not lens pass/fail—and invalidates on any key,
    expiry, or recovery change. **Verify:** cache hit/miss matrix and launch count.

22. **Documentation routing avoids module fail-open widening.** Documentation-
    only and mixed changes route from document content, directives, contracts,
    audiences, and graph evidence. Missing/inapplicable code-module mapping
    alone retains exactly one attributable risk-selected deep lens for
    documentation-only/simple low-risk work. Only genuinely ambiguous, corrupt,
    mixed, substantively risky, or otherwise materially risky evidence widens
    to architecture, code-quality, security, and QA, with an explicit reason;
    routing is never unconditional all-lens or no-review. **Verify:** API/
    security/user docs, runbook, changelog, typo, malformed, ambiguous, mixed,
    and absent-map cases.

23. **Fix policy measures convergence.** Each fix evaluation records closed,
    persistent, regressed, and new admissible findings plus test/evidence
    progress. Safe measurable convergence can continue beyond three cycles;
    no-progress, repetition, oscillation, worsening, task-specific bounds,
    unsafe recovery, scope/authority change, or human stop escalates. **Verify:**
    convergence matrix and human-ownership assertions.

24. **Per-lens telemetry is complete and independent.** Each lens records
    eligible/selected/promoted/collected state, admissible/confirmed/unique/
    overlap/duplicate/invalidated/false-positive findings, retry/repair,
    latency, provider-correct tokens/cost when available, and infrastructure
    unavailability with versioned definitions/denominators. Telemetry derives
    from sealed revisions, does not expose drafts across lenses, alter current
    verdicts, or suppress floors. **Verify:** golden arithmetic, unavailable-vs-
    zero, information-isolation, and enabled/disabled equivalence fixtures.

25. **Status is cheap, live, and non-gating.** Status reads durable snapshots
    only, performs no expensive recomputation, and never blocks or gates work.
    Picture-in-Picture continuously identifies the active agent/phase and
    updates from durable events. **Verify:** instrumentation asserts bounded
    reads/no graph/review recomputation and identical workflow outcome with
    status open, closed, interrupted, or unavailable.

26. **Progress telemetry is truthful.** Status shows observed tokens used,
    elapsed time in the current focus/stage, and execution/wait/human-wait state.
    ETA appears only from observed comparable or bounded work and includes
    source, confidence, and update time; otherwise it says unavailable.
    **Verify:** execution, tool wait, agent wait, human wait, resume, unknown
    tokens, sparse history, comparable history, and stale ETA fixtures.

27. **Onboarding self-repairs when authorized.** Every setup check is classified
    self-repairable, authority-required, host-policy, or external-unavailable.
    Self-repair executes automatically; authority-required asks once for the
    exact authority; host-policy/external-unavailable explains and retries only
    when state can change, without repeated prompts. **Verify:** full setup matrix
    and prompt counts.

28. **Onboarding survives sibling worktrees.** A stable repository-family hook/
    launcher resolves the exact current worktree and latest valid engine
    dynamically, so supported sibling worktrees operate without restart or a
    new Taskplane task. **Verify:** root, nested, sibling, newly created, moved,
    stale-engine, unavailable-engine, and policy-restricted worktree fixtures.

29. **Thread approval is attributable and sufficient.** In Claude-tag/Slack
    contexts, one attributed approval in the bound thread authorizes the same
    routine continuation as the consolidated packet; wrong thread, actor,
    revision, replay, or ambiguous reply cannot authorize. **Verify:** receipt
    matrix and one-routine-approval trace.

30. **Thread delivery preserves complete evidence.** A concise summary leads,
    but the complete evidence set is attached or linked and remains canonical.
    Missing native controls degrade to an accessible thread summary, complete
    Markdown/artifacts, and canonical actions through attributed replies;
    unavailable controls are never interpreted as decline. Automatic retry/
    recovery precedes any user prompt. **Verify:** native, missing-control,
    attachment/link failure, large artifact, retry success/exhaustion, and
    accessibility fixtures.

31. **Evidence remains lossless across all recovery and delivery.** Provisional
    publication, promotion, normalization, repair, retry, cache reuse,
    supersession, dashboard-size fallback, and thread delivery never drop or
    duplicate valid findings, criteria, gaps, provenance, telemetry, or artifact
    identity. **Verify:** an end-to-end multi-revision large-review round trip
    reconciles canonical JSON, complete Markdown, optional HTML, and thread links.

32. **Cross-host and legacy semantics remain compatible.** Codex, Claude,
    Slack-capable entry, managed worktrees, and supported fallbacks preserve the
    same authorization, recovery, gate, review, evidence, convergence, status,
    and telemetry semantics. **Verify:** golden cross-host scenarios and existing
    suites pass without removed, skipped, xfailed, loosened, or reclassified
    governance assertions.

## Non-functional requirements

- `security`: Authorization is attributable, target/revision/scope bound and
  least-privilege; silence, stale receipts, UI absence, cache, repair, recovery,
  or telemetry cannot expand authority, cross worktrees, forge evidence, weaken
  a gate, expose secrets, or authorize destructive/external action.
- `architecture`: One canonical authorization packet, authority ledger,
  mechanical-gate model, recovery/convergence record, slot ledger, evidence
  binding, status snapshot, and audit stream serve every host/flow; adapters and
  UI never create parallel truth.
- `data-safety`: Valid requirements, designs, plans, evidence, findings,
  criteria, gaps, telemetry, artifacts, receipts, and revisions survive
  automation, repair, retry, caching, supersession, and fallback losslessly.
- `sre`: Preparation, gates, recovery, review waves, promotions, caches, status,
  onboarding, rendering, attachments, and thread actions have deterministic
  bounded states, 2–3 routine retries or convergence logic, expiry, idempotency,
  actionable diagnostics, and no infinite/prompt loop.
- `integrability`: Authorization, recovery, review, status, onboarding,
  north-star-note, Markdown delivery, and thread-continuation contracts are
  versioned and semantically portable across Codex, Claude, Slack, worktrees,
  and supported legacy consumers.
- `scalability`: Progressive review scales with attributable risk rather than
  catalog size; durable status reads stay cheap; large dashboards/artifacts,
  agent waves, revisions, and telemetry remain complete and bounded/indexable.
- `cost-finops`: Automatic recovery avoids redundant user/model turns;
  evaluator cache, metadata repair, progressive dispatch, affected-slot retry,
  durable status, and provider-correct per-lens telemetry bound wasted spend;
  new external spend always requires human authority.
- `privacy-compliance`: Receipts, thread records, evidence paths, worktree
  identity, status, caches, telemetry, and artifacts retain minimum audit data
  while redacting credentials, secrets, personal paths, prompts, and unrelated
  conversation/repository content.
- `accessibility`: Inline/status/thread fallbacks and approval packets are
  keyboard operable, semantically labeled, readable without color, responsive,
  and provide complete Markdown when native visualization is unavailable.

## Contract handoff

- `scope_paths`:
  - `taskplane/tp.py`
  - `taskplane/loop.py`
  - `taskplane/review.py`
  - `taskplane/review_evidence.py`
  - `taskplane/review_dor.py`
  - `taskplane/lens.py`
  - `taskplane/lens_signals.py`
  - `taskplane/views.py`
  - `taskplane/runtime_eval.py`
  - `taskplane/taskplane_lite.py`
  - `taskplane/spend.py`
  - `taskplane/dashboard.py`
  - `taskplane/command_runtime.py`
  - `taskplane/command_adapters.py`
  - `taskplane/host_capabilities.py`
  - `taskplane/preflight.py`
  - `taskplane/repository.py`
  - `taskplane/storage.py`
  - `agents/**`
  - `skills/**`
  - `workflows/**`
  - `hooks/**`
  - `.codex-plugin/**`
  - `.claude-plugin/**`
  - `docs/**`
  - `taskplane/tests/**`
  - `specs/spec.md`
- `out_of_scope`: feature/lens-catalog/graph/model/billing redesign, release or
  marketplace publication, automatic final/A-B/destructive/external approval,
  evidence weakening, unlimited recovery, and unrelated product behavior.
- `dod.test_command`: `python3 -m pytest -q taskplane/tests/test_requirements.py`
- dependency: `R-0011`.
- existing changed contracts preserved:
  - `contract:review-kernel-slot`
  - `contract:review-kernel-partial-revision`
  - `contract:review-kernel-mechanical-repair`
- existing provided contracts preserved:
  - `contract:review-risk-progression`
  - `contract:review-evidence-binding`
  - `contract:evaluator-infrastructure-health`
  - `contract:review-fix-convergence`
  - `contract:lens-quality-telemetry`
- added/changed contract intent:
  - `contract:consolidated-authorization`
  - `contract:automatic-recovery`
  - `contract:status-progress-telemetry`
  - `contract:onboarding-worktree-continuity`
  - `contract:product-internal-north-star`
  - `contract:large-markdown-delivery`
  - `contract:attributed-thread-continuation`

This remains bounded to Taskplane control-plane/workflow UX and engineering-
review efficiency. It is a material cross-flow authority-contract amendment
and requires Design before Build. There are no blocking Product questions.
