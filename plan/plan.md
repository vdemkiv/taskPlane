# R-0003 implementation plan

## Outcome and design lock

Implement the approved **Proof-carrying enforcement, collision isolation, and safe post-merge cleanup** design for R-0003 without changing Product or Design authority. The active approved Design fingerprint is `0567c975ac61b3372e01441f3391223264689fda2afffedafcdea177e3cb27d3`; the graph baseline fingerprint is `1c922579cff49bae94329a07532106138583e9de73be0048f6b7dacc70930d6b`.

The single bounded R-0003 replan impact query reached 22 in-policy modules, identified `req:R-0002` and `req:R-0003` as affected requirements, and returned no unknown surfaces. Its current as-built graph content fingerprint is `c6ea692270d32cc5a44108deefe17814623125fb64d37a1767bb6b9da48279e7`; the approved Design fingerprint and frozen Design graph baseline remain unchanged. The query's requirement-depth truncation is the expected boundary when traversal encounters R-0003 through its recorded dependency on R-0002, not missing local or contract coverage. Because the Plan checker resolves scope through coarse as-built modules, every exact approved `design.graph.proposed_modules` identifier is also declared once in the owning task's `new_modules`. This is mechanical coverage metadata only: implementation ownership and design remain unchanged. Every task uses the approved typed policy: local depth 3, contract-only boundaries, contract depth 1, and requirement depth 1.

Product dependencies R-0001 and R-0002 both resolve as requirement nodes with direct recorded high-confidence `R-0003 -> dependency` edges. Neither dependency requires a Plan-side repair.

Exact module ownership is: t01 owns the five ReviewKernel/evaluator modules and `specs/spec.md`; t02 owns `taskplane/enforcement.py`, `taskplane/host_capabilities.py`, and `taskplane/run_store.py`; t03 owns `taskplane/tp.py`, `taskplane/taskplane_lite.py`, `taskplane/loop.py`, `taskplane/dashboard.py`, and `taskplane/runtime_eval.py`; t04 owns `taskplane/collision.py` and `taskplane/collision_registry.json`; t05 owns `hooks/hooks.json` and `.codex/hooks.json`; t06 owns `taskplane/repository.py`, `taskplane/storage.py`, and `taskplane/worktree_cleanup.py`; and t09 owns `agents`, `skills`, `docs`, and `taskplane/tests`.

## Narrow replan delta

Independent evaluation failed t01 after its focused floor passed 17 tests. The failure was proof and executability debt: the declared command included `taskplane/tests/test_review_routing.py` outside writable scope, two routing expectations still demanded a producer rerun forbidden by AC1, the seven-file radius exceeded Taskplane's 600-second evidence bound, and the focused floor did not behaviorally prove supported/unsupported runtime handling, original-run retry lineage, all engine/worktree/acceptance identity negatives, Product/Engineering/CLI/canonical-inline continuation parity, or the full evaluator-outage repository/worktree/readiness/no-fix matrix.

The replan changes only t01 scope and its test command. Scope now includes the exact behavioral owner tests used by the command; approved source owners needed if those tests expose a real failure; the Product and Engineering agent/skill guidance plus CLI reference required by AC7; and `.github/workflows/ci.yml` so the focused runtime smoke actually executes under Python 3.10, 3.11, and 3.12. In particular, `taskplane/tests/test_review_routing.py` is writable, so the two stale expectations can be aligned with normalize-once/no-producer-rerun semantics.

The exact t01 scope additions are:

- Source owners: `taskplane/tp.py`, `taskplane/taskplane_lite.py`, `taskplane/dashboard.py`, `taskplane/run_store.py`, and `taskplane/loop.py`.
- Guidance and CI: `agents/tp-product.md`, `agents/tp-engineering.md`, `skills/tp-product/SKILL.md`, `skills/tp-engineering/SKILL.md`, `docs/cli-reference.md`, and `.github/workflows/ci.yml`.
- Behavioral owner tests: `taskplane/tests/test_review_evidence_lifecycle.py`, `taskplane/tests/test_review_routing.py`, `taskplane/tests/test_status_and_large_delivery.py`, `taskplane/tests/test_review_preflight.py`, `taskplane/tests/test_dor_dod.py`, `taskplane/tests/test_binding_obligations.py`, and `taskplane/tests/test_loop.py`.

The replacement command runs the complete focused floor plus selected behavioral node ids for collection/repair, retry lineage, provenance/conservation, shape projection, active-run addressability, executable continuation, PR/README DoR, dynamic consent, scope denial, early request-changes, evaluator-unavailable recovery, and claimed-worktree binding. It no longer runs six non-floor files wholesale: four are reduced to exact owners, the unrelated host-capabilities/storage files are removed, and three narrowly selected owner files are added. Every test path named in the command is present literally in t01 scope, preventing the prior scope/test mismatch mechanically; CI owns cross-interpreter execution rather than a syntax-only callable-name check.

Tasks t02–t09 retain their identities, scopes, commands, dependencies, criteria, contracts, edges, modules, policies, and model tiers. Automatic worktree cleanup remains disabled through t08 and becomes reachable only in t09 after the regression floor, enforcement, isolation, durable merge receipt, preservation/crash matrix, and post-removal governance-continuity evidence pass.

## Risk-first order

1. **t01 — ReviewKernel regression floor.** Repair and behaviorally prove all ten already-landed ReviewKernel, evaluator, projection, Python, continuation, and claimed-worktree guarantees before touching new authority or lifecycle code. Align the two stale producer-rerun expectations, close the evaluator's proof deficits, and keep the focused owner radius within 600 seconds; no approved invariant may be weakened.
2. **t02 — Canonical enforcement decision and receipts.** Add the sole live/unproven/advisory classifier, exact workspace/session/run/revision evidence identity, 300-second trust boundary, attributable advisory record, and atomic storage.
3. **t03 — Enforcement gates and projections.** Make entry, dispatch, stage emission, claim, loop/review closure, status, dashboard, artifacts, and retro consume the same decision. Strict refusal must occur before state creation; live entry consumes its own PreToolUse receipt with zero extra probes.
4. **t04 — Collision registry and classification.** Add the versioned registry, exact-governed-state screen, known-driver denial, helper allowlist, unknown normal/strict tiers, signed-root discovery, and durable interference events.
5. **t05 — Collision adapters and audit.** Wire Claude/Codex hooks and canonical UI/retro projections. Status reads durable summaries and performs no rediscovery. This completes isolation before cleanup work begins.
6. **t06 — Durable merge receipt and read-only eligibility.** Extend managed registration and add the orchestrator-owned merge receipt plus read-only eligibility proof. No automatic removal is reachable in this task.
7. **t07 — Preservation, crash, and replay matrix.** Implement the locked exact-path no-force cleanup primitive behind an uncalled boundary, then prove every preservation case, failure point, and already-clean replay. Automatic cleanup remains disabled.
8. **t08 — Post-removal governance continuity.** Prove EM, graph/evidence reconciliation, retro, status, and final-signoff inputs remain canonical after a directly exercised eligible removal; evidence-held trees stay retained.
9. **t09 — Enable automatic cleanup and cross-host conformance.** Only after t06–t08 pass, connect the orchestrator merge boundary to automatic cleanup. This is the final and only enablement task; it also runs strict/warn/off, collision normal/strict/off, Codex/Claude/Slack-capable, and managed/legacy rollback goldens.

Dependencies serialize every overlapping surface. The only independent work that would otherwise be safe is intentionally held behind the selected rollout: ReviewKernel floor, enforcement, collision isolation, receipt/eligibility, destructive-matrix proof, governance continuity, then enablement.

## Acceptance ownership

- t01 owns AC1–AC10 exactly as copied in `plan/tasks.json`.
- t02 owns AC11, AC13, and AC16.
- t03 owns AC12, AC14, and AC15.
- t04 owns AC17–AC20.
- t05 owns AC21.
- t06 owns AC22.
- t07 owns AC23–AC25.
- t08 owns AC27.
- t09 owns AC26 and AC28.

There are exactly 28 verbatim criteria across the tasks, each owned once. The task set also contains all 11 approved contract ids and every exact proposed design edge once.

## Fail-closed retention and negative matrices

The ReviewKernel floor retains wrong-producer, copied, sibling-worktree, stale-lease, wrong-run, missing-slot, duplicate-slot, engine-mismatch, substantive-mutation, evaluator-outage, moved/dirty/stale-tree, unsupported-Python, missing-contract-shape, and conflicting-continuation negatives. None may become approvable, judged, or silently omitted.

Enforcement retains strict absent-hook zero-state refusals for new, loop init, review start, stage emission, and claim; advisory-without-actor refusal; mid-run liveness loss; mismatched/foreign/stale session receipts at the 299/300/301-second boundary; and identical classification/evidence identity across all consumers. Recovery may obtain fresh proof or an attributable advisory acknowledgment only; it cannot fabricate live status.

Collision isolation retains no-op behavior outside exact governed state; silent allow for document, spreadsheet, presentation, and visualization helpers; unconditional governed-state denial for registry-known competing skills and agents; advised unknowns in normal mode; denied unknowns only in strict isolation; advisory observations that never claim an inactive hook denied; signed multi-file roots only; same-named unsigned directories ignored; unreadable/ambiguous candidates fail closed without inventing identity; and attributable exact-root override only.

Cleanup preserves every dirty, untracked, staged, unmerged, foreign, unregistered, selected-variant, failed, active, locked, symlinked, path-mismatched, missing-ref, ambiguous-main, merge-in-progress, and evidence-needed worktree. The same matrix runs against both the initial read and the locked last-moment read. It also preserves on repository/path/type/branch/tip/primary-tip/cleanliness/lifecycle/variant/retention changes, pre-receipt crashes, Git failure, partial absence, or inconsistent registration. No path prefix, name, status, message, fetch, force flag, broader target, branch deletion, evidence deletion, or stronger retry may establish eligibility.

Crash points cover before merge, after Git merge but before durable receipt, after receipt but before removal, and after exact removal but before outcome persistence. Only a durable receipt permits cleanup; one same-receipt maintenance replay may record already-clean when both exact path and exact registration are absent. Any inconsistent postcondition becomes permanent manual attention for that outcome fingerprint.

## Verification and rollout controls

Each task has one runnable pytest command string. New modules remain synchronous, stdlib-only, Python 3.10-compatible, runtime-validate JSON/env/Git trust boundaries, and package the versioned registry for clean-wheel imports. The final conformance task must verify Python 3.10/3.11/3.12 smoke coverage and the approved cross-host rollback constraints.

Rollback may set enforcement to warn, disable collision screening, and return cleanup to manual. It must retain truthful unproven/advisory status, interference and merge/cleanup records, branches, commits, governance evidence, ReviewKernel floors, and human final-signoff authority.

Any change to an acceptance criterion, approved module, contract id, proposed edge, depth policy, cleanup eligibility invariant, or enablement order is Design drift and must return to Design for a new human approval.
