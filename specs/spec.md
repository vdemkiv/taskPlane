# Specification — governed-run enforcement, collision isolation, and safe worktree cleanup

## Problem

Taskplane must remain trustworthy when host hooks are absent, another delivery
orchestrator competes for authority, ReviewKernel metadata needs repair, or
parallel task worktrees outlive their merge. Today those cases can silently
degrade enforcement, allow two delivery loops to drive one governed workspace,
or leave managed worktrees behind; an over-broad cleanup would create the
opposite failure by destroying unmerged work or evidence.

The August 19 field evidence also identified ReviewKernel collection and
dashboard failures. Fixes for metadata normalization, non-judged evaluator
outages, shape-safe status projection, and exact claimed-worktree binding are
already on `main` in v2.17.10/v2.17.11. This amendment treats those shipped
behaviors as non-regressible obligations while defining the remaining Claude
enforcement, collision-isolation, and cleanup outcomes.

## Users and context

Engineers run Taskplane through Codex, Claude Code, Chat/Cowork, Slack-capable
entry points, local repositories, managed checkouts, and parallel task
worktrees. They need to know whether governance is structurally enforced or
explicitly advisory, to have one delivery authority inside governed state, and
to recover disk space only when Taskplane can prove a managed branch is already
merged into the repository's primary branch (`main` in this repository).

Evidence reviewed for this change:

- `/Users/vdemkiv/Downloads/taskplane-bug-report.md` (field evidence only)
- `backlog/skill-collision-isolation.md` (identical to the supplied collision
  evidence; product decisions are re-stated here)
- `backlog/claude-enforcement-heartbeat.md` (identical to the supplied heartbeat
  evidence; product decisions are re-stated here)
- current v2.17.11 behavior and regression tests on `main`

## In scope

- Preserve the landed ReviewKernel collection, metadata-repair, evaluator-
  unavailable, dashboard-shape, Python-compatibility, and claimed-worktree
  binding outcomes as regression obligations.
- Expose one structural enforcement status for Claude-family hosts; fail closed
  at governed entry and closing gates when enforcement is unproven, with an
  explicit attributable advisory escape hatch.
- Screen known competing delivery skills and agent dispatches while Taskplane
  governs the exact workspace; allow non-delivery helpers and audit unknown
  foreign skills without claiming ungoverned workspaces.
- Detect competing-orchestrator state by signature, exclude it from compiled
  write authority by default, and report interference in status/onboarding and
  retrospectives.
- Automatically clean only Taskplane-managed linked task worktrees whose exact
  branch tips are verifiably ancestors of the current primary-branch tip after
  a successful orchestrator-owned merge.
- Make cleanup conservative, idempotent, auditable, path-safe, and independent
  of branch deletion or governance-evidence retention.
- Keep hook manifests, skills/docs, status, dashboard, and cross-host behavior
  consistent with the canonical engine decisions.

## Out of scope

- Disabling, uninstalling, renaming, shadowing, or modifying another plugin.
- Governing skill choice or agent dispatch when no Taskplane contract, loop, or
  review is active in the exact workspace.
- Treating model prose, plugin descriptions, directory names alone, or stale
  receipts as proof of enforcement or foreign-state identity.
- Weakening provenance, slot conservation, acceptance evidence, orchestrator-
  only gates, exact worktree binding, or the human final-signoff boundary.
- Automatically deleting Git branches, stashes, untracked files, failed-task
  artifacts, governance records, selected A/B variants, review evidence, or
  any worktree not proven Taskplane-managed.
- Force-removing dirty, locked, linked, symlinked, foreign, unmerged, ambiguous,
  selected-variant, failed, active, or evidence-needed worktrees.
- Fetching, rebasing, merging, resolving conflicts, changing `main`, or asking
  for broader authority merely to make a worktree eligible for cleanup.
- Release/marketplace publication, host-level per-project plugin controls,
  lens-catalog redesign, or unrelated product features.

## Functional requirements

1. The ReviewKernel collector derives summary verdict/count/severity metadata
   from canonical admissible findings before contradiction rejection, records
   an evidence-bearing equivalence audit, and never requires an impossible
   producer rewrite for a metadata-only repair.
2. Substantive review-result changes retry only affected slots; exact producer,
   lease, worktree, run, and slot provenance plus full conservation remain
   mandatory for approval.
3. Review/status surfaces remain usable for coding, read-only Product/Design,
   ReviewKernel, released, and legacy contract shapes, and supported Python
   interpreters fail actionably rather than at parse time.
4. Review execution choice, run-manifest lookup, dashboard-artifact addressing,
   evaluator-unavailable recovery, and exact claimed-worktree evaluation remain
   internally consistent and reachable.
5. One kernel-owned enforcement status classifies an exact workspace/session as
   `live`, `unproven`, or explicitly `advisory` from structural receipts and
   screen activity; every entry, gate, status, dashboard, artifact, and retro
   consumes that same decision.
6. Claude governed entry and closing gates fail closed without current proof of
   enforcement in strict mode and create no partial governed state; a live hook
   proves itself through the entry call without an extra probe.
7. Advisory operation requires an explicit human identity, is durably recorded,
   and is stamped on all downstream artifacts; mid-run enforcement loss must be
   acknowledged before a closing gate can pass.
8. During exact-workspace governed state, known competing delivery skills and
   agents are denied structurally with an actionable Taskplane equivalent;
   unknown foreign delivery invocations are recorded and advised, and strict
   isolation may deny them.
9. Non-delivery helper skills are silently allowed, and all collision screening
   is a no-op outside governed state; advisory runs report weaker assurance
   without pretending that inactive hooks enforced a denial.
10. Competing-orchestrator state is identified by versioned signatures rather
    than names, excluded from new contract write authority by default, and may
    be included only by an explicit attributable override.
11. Status/onboarding and retro outputs truthfully report foreign interference,
    including denied skills, denied dispatches, advised invocations, and signed
    state roots, while clean runs remain quiet.
12. Cleanup eligibility requires Taskplane-managed identity, an exact registered
    linked-worktree path, a clean worktree, an inactive/released task lifecycle,
    and proof that the candidate branch tip is an ancestor of the current
    resolved primary-branch tip (`main` here).
13. Cleanup revalidates every eligibility fact immediately before removal and
    preserves a candidate on any uncertainty, state change, lock, path anomaly,
    Git failure, or evidence-retention need; it never uses force.
14. Cleanup runs after a successful orchestrator-owned merge and may be retried
    by bounded maintenance, but removes only the linked worktree registration and
    directory. It does not delete branches or canonical Taskplane evidence.
15. Cleanup decisions and outcomes are durable, idempotent, visible in status/
    retro, and preserve EM/final-signoff behavior after eligible worktrees are
    gone.

## Acceptance criteria

1. **Metadata-only ReviewKernel repair reaches collection.** Reproduce a slot
   whose findings contain normalized high/major/blocker rows while its producer
   summary uses the older blocker-only count. Collection normalizes the summary
   from canonical admissible findings exactly once, records before/after,
   derivation authority, and proven equivalence, leaves finding bytes intact,
   and reaches a canonical complete revision when no other gap exists. No
   producer contract reactivation or result rewrite is requested.
2. **Substance still reruns narrowly.** Mutating checked evidence, findings, or
   another substantive result field cannot be normalized. The engine reissues
   only the affected slot within its attempt bound, keeps unchanged sibling
   results sealed, and binds the new attempt to the original run/slot lineage.
3. **Provenance and conservation do not regress.** Wrong producer, copied,
   sibling-worktree, stale lease, wrong run, missing slot, duplicate slot, and
   mismatched engine cases remain non-approvable; only exact once-complete slot
   and acceptance-evidence conservation permits approval.
4. **Review status is shape-safe.** With each supported coding, read-only,
   ReviewKernel, released, and legacy contract shape active, status, dashboard,
   and inline projection render without exception and show the available scope
   or write allowance. A missing `coding` object never raises `KeyError`.
5. **Supported Python versions start cleanly.** The CLI/dashboard modules parse
   and the ReviewKernel start smoke fixture runs on every Python version in the
   supported CI matrix (currently 3.10, 3.11, and 3.12). An unsupported
   interpreter receives one actionable version error before state creation;
   supported versions never fail through PEP-701-only syntax.
6. **Live review runs remain addressable.** While a ReviewKernel run is active
   and collection is incomplete, repository/run status resolves the same run id
   and canonical manifest. It cannot report `run manifest is unavailable` for a
   run that `review collect --run-id` can still operate on.
7. **Review recovery guidance matches executable actions.** A validation
   `needs_user` payload, Product/Engineering guidance, and CLI reference expose
   the same executable dynamic/render/static continuation; dashboard obligations
   identify the canonical inline artifact. Fixtures execute the emitted command
   rather than relying on a conflicting prose command.
8. **Accepted ReviewKernel behavior remains.** PR-commit/README DoR derivation,
   explicit dynamic-validation consent with an attributable receipt, strict
   out-of-scope write denial, immutable findings/provenance, early truthful
   request-changes, and approval conservation all remain covered by executable
   regression fixtures.
9. **Evaluator unavailability is non-judgmental.** A bound evaluator outage is
   recorded as unavailable rather than pass/fail, keeps readiness closed, and a
   retry returns to evaluation without opening a product-fix cycle. Cache reuse
   remains exact to evaluator, engine, capability, repository, worktree, and
   validity window.
10. **Evaluation stays on the claimed tree.** Parallel task DoD and evaluator
    evidence bind to the exact claimed task worktree whether invoked from the
    primary or worker checkout; a sibling, parent, moved, dirty, or stale tree
    cannot substitute.
11. **One structural enforcement decision serves every surface.** Given the
    same workspace/session receipts and meter state, new/loop/review entry,
    stage dispatch, closing gates, status, dashboard, artifacts, and retro all
    return the same `live|unproven|advisory` classification and evidence id.
12. **Unproven Claude entry creates nothing.** With Claude hook support declared
    but the plugin hook absent, strict `new`, `loop init`, `review start`, stage
    emission, and claim return nonzero machine-shaped refusals with host-specific
    recovery and leave contracts, loop/review state, and receipts uncreated.
13. **A live Claude hook is self-proving.** The same entry commands succeed when
    the hook is live, using the fresh PreToolUse receipt from that entry command
    with no additional probe, model call, or human prompt.
14. **Advisory mode is explicit and lossless.** `--advisory` without `--by`
    refuses. With an attributable identity it records one decision, proceeds,
    and stamps every gate, evidence payload, review manifest/verdict, dashboard,
    recommendation, and retro with who acknowledged advisory status and when.
15. **Mid-run degradation gates closure.** A run that entered live but has no
    valid screen activity in the active contract window cannot pass a loop gate
    or close a review until fresh proof or an explicit advisory acknowledgment;
    the downgrade propagates from that point without discarding evidence.
16. **Stale receipts cannot vouch for another session.** A foreign session
    fingerprint with no known current session and an observation older than the
    bounded freshness window (300 seconds today) classifies `unproven`; matching
    fresh receipts and screen activity classify live.
17. **Known delivery collisions are denied.** With an active governed step,
    invoking a registry-known competing delivery skill or agent is denied even
    when optional dispatch enforcement is unset. The denial names the active
    run/step, foreign identity, and exact Taskplane continuation; no foreign
    state write or self-approval follows.
18. **Helpers and unknowns follow declared tiers.** Document/spreadsheet/
    presentation/visualization helpers on the non-delivery allowlist pass
    silently. An unknown foreign skill is recorded and advised in normal mode
    and denied in strict isolation. Registry and allowlist changes are versioned
    and visible in the audit record.
19. **Foreign state detection is signature-safe.** A competing state layout
    with the registry's required signature is named in entry, onboarding, and
    status; a directory with the same name but no signature is ignored. New
    contract authority excludes the signed root unless an explicit attributable
    override is recorded.
20. **Taskplane claims only governed state.** With no exact-workspace contract,
    live loop, or open review, skill and dispatch screens are no-ops. An
    advisory run never claims a hook denial occurred; it reports advisory
    assurance and observed interference only.
21. **Interference is measurable.** A retro with denials or advised foreign
    invocations headlines counts and identities for denied skills, denied
    agents, advised invocations, and detected signed roots; a clean run has no
    interference headline. Status reads this durable record without rerunning
    discovery.
22. **Merged managed worktrees become eligible, not assumed.** After the
    orchestrator successfully merges a Taskplane task branch into the resolved
    primary branch, eligibility proves the candidate is registered at the exact
    Taskplane-managed path, its branch/tip matches the recorded task, the tree is
    clean and inactive, and `branch_tip` is an ancestor of the current primary-
    branch tip. A branch name, task status, merge message, or path prefix alone
    is insufficient.
23. **Every preservation case fails closed.** Matrix fixtures preserve dirty,
    untracked, staged, unmerged, foreign/unregistered, selected A/B variant,
    failed, active, locked, symlinked, path-mismatched, missing-ref, ambiguous-
    main, merge-in-progress, and evidence-needed worktrees. They also preserve
    any candidate whose eligibility cannot be re-read immediately before
    removal.
24. **Cleanup is path-safe and never forced.** Immediately before removal the
    engine re-resolves repository identity, registered path, worktree type,
    branch/tip, primary tip, clean state, lifecycle, variant, and retention
    flags. Any check or Git removal failure leaves the worktree and registration
    intact, emits one actionable diagnostic, and never retries with `--force`, a
    broader path, or branch deletion.
25. **Cleanup is idempotent and evidence-preserving.** Replaying cleanup after a
    successful removal reports already-clean with no error. Only the eligible
    linked worktree registration and directory disappear; the branch, commits,
    requirement/design/plan, submissions, tests, review evidence, audit trail,
    and final-signoff inputs remain addressable.
26. **Cleanup timing follows the merge transaction.** Automatic cleanup is
    attempted only after the orchestrator has observed a successful merge into
    the resolved primary branch and durably recorded the merge result. A crash
    before that record preserves the tree; a crash after it may be recovered by
    one idempotent maintenance pass using the same eligibility rules.
27. **Removed eligible trees do not break governance.** EM synthesis, graph/
    evidence reconciliation, retro, status, and final sign-off complete from
    retained canonical records after eligible worker trees are removed. A task
    marked evidence-needed remains present until that retention flag is
    explicitly released by the owning lifecycle.
28. **Cross-host and rollback semantics stay conservative.** Codex, Claude,
    Slack-capable flows, managed/legacy worktrees, strict/warn/off enforcement,
    and enabled/disabled collision screening retain equivalent authority and
    evidence semantics. Rollback may return to warning/manual cleanup but cannot
    auto-approve, forge live enforcement, weaken ReviewKernel regression tests,
    or force-delete a worktree.

## Non-functional requirements

- `security`: Enforcement and advisory acknowledgments are actor/session/
  workspace/revision bound; collision decisions are registry-evidenced; cleanup
  resolves exact Git identity and paths and cannot expand authority, traverse a
  link, remove a foreign tree, weaken a gate, expose credentials, or use force.
- `architecture`: One canonical enforcement decision, collision registry,
  interference record, ReviewKernel repair authority, and worktree-cleanup
  eligibility record serve all commands/hosts; adapters, hooks, docs, and UI do
  not create parallel truth.
- `data-safety`: Findings, producer evidence, branches, commits, dirty/unmerged
  bytes, variants, failed-task state, requirements, designs, plans, submissions,
  audit records, and final-signoff evidence survive repair, retry, cleanup,
  crash recovery, and rollback without loss or duplication.
- `sre`: Entry/gate checks and cleanup are deterministic, bounded, idempotent,
  crash-recoverable, and observable; failures preserve state and produce one
  actionable reason rather than looping, hanging collection, or escalating to a
  stronger removal primitive.
- `integrability`: Versioned enforcement, collision, interference, repair, and
  cleanup contracts preserve semantic parity across Codex, Claude, Slack,
  managed/legacy repositories, current manifests, and supported hook adapters.
- `scalability`: Collision checks remain constant or registry-bounded per tool
  event; status reads durable summaries; cleanup enumerates only registered
  Taskplane-managed candidates and does not scan or mutate arbitrary worktrees.
- `cost-finops`: A live hook requires no extra probe; metadata repair and
  evaluator retry avoid redundant agents; collision/status checks are cheap;
  cleanup retries only after state can change and never fetches or launches
  model work merely to prove merge eligibility.
- `privacy-compliance`: Receipts, identities, interference events, paths, and
  cleanup audits retain the minimum attributable data and redact credentials,
  secrets, prompts, personal paths in portable artifacts, and unrelated plugin
  or repository content.
- `accessibility`: Refusals, advisory stamps, collision explanations, cleanup
  exclusions, status, dashboard, and retro are machine-shaped plus readable
  without color, expose keyboard-accessible actions where controls exist, and
  retain complete Markdown fallbacks.

## Contract handoff

- `scope_paths`:
  - `taskplane/tp.py`
  - `taskplane/taskplane_lite.py`
  - `taskplane/host_capabilities.py`
  - `taskplane/loop.py`
  - `taskplane/review.py`
  - `taskplane/review_evidence.py`
  - `taskplane/review_repair.py`
  - `taskplane/review_recovery.py`
  - `taskplane/evaluator_health.py`
  - `taskplane/dashboard.py`
  - `taskplane/repository.py`
  - `taskplane/storage.py`
  - `taskplane/run_store.py`
  - `taskplane/runtime_eval.py`
  - `hooks/**`
  - `.codex/**`
  - `.codex-plugin/**`
  - `.claude-plugin/**`
  - `agents/**`
  - `skills/**`
  - `docs/**`
  - `backlog/skill-collision-isolation.md`
  - `backlog/claude-enforcement-heartbeat.md`
  - `taskplane/tests/**`
  - `specs/spec.md`
- `out_of_scope`: foreign plugin mutation/removal, ungoverned routing control,
  branch/stash/evidence deletion, fetch/rebase/merge-to-enable-cleanup,
  force-removal, final-signoff automation, governance weakening, host-native
  per-project plugin management, publication, and unrelated product behavior.
- `dod.test_command`: `python3 -m pytest -q taskplane/tests/test_review_evidence_lifecycle.py taskplane/tests/test_review_routing.py taskplane/tests/test_status_and_large_delivery.py taskplane/tests/test_loop.py taskplane/tests/test_host_capabilities.py taskplane/tests/test_storage_kernel.py`
- dependencies:
  - `R-0002` (changed-from governed-run enforcement, collision isolation, and
    safe worktree cleanup)
  - `R-0001` (sole explicit dependency; consolidated governed delivery and
    convergent review)
- contracts:
  - `contract:review-kernel-slot`
  - `contract:review-kernel-mechanical-repair`
  - `contract:review-evidence-binding`
  - `contract:evaluator-infrastructure-health`
  - `contract:consolidated-authorization`
  - `contract:automatic-recovery`
  - `contract:onboarding-worktree-continuity`
  - `contract:claude-enforcement-status`
  - `contract:exclusive-delivery-authority`
  - `contract:foreign-interference-audit`
  - `contract:managed-worktree-cleanup`
- `contract_relations`:
  - consumes `contract:review-kernel-slot`
  - consumes `contract:review-kernel-mechanical-repair`
  - consumes `contract:review-evidence-binding`
  - consumes `contract:evaluator-infrastructure-health`
  - changes `contract:consolidated-authorization`
  - changes `contract:automatic-recovery`
  - changes `contract:onboarding-worktree-continuity`
  - provides `contract:claude-enforcement-status`
  - provides `contract:exclusive-delivery-authority`
  - provides `contract:foreign-interference-audit`
  - provides `contract:managed-worktree-cleanup`

This is a material cross-host authority and destructive-lifecycle boundary. It
requires Design before Plan/Build, with an acceptance-to-design map for every
criterion and explicit state machines for enforcement, collision disposition,
and cleanup eligibility. There are no blocking Product questions.
