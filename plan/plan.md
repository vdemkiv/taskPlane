# R-0003 recovery implementation plan

## Recovery boundary and design lock

Continue the approved **Proof-carrying enforcement, collision isolation, and safe post-merge cleanup** design for R-0003 after the ReviewKernel regression floor shipped as Taskplane v2.17.12 at commit `470b5b2`. The approved Design fingerprint remains `0567c975ac61b3372e01441f3391223264689fda2afffedafcdea177e3cb27d3`; its frozen graph baseline remains `1c922579cff49bae94329a07532106138583e9de73be0048f6b7dacc70930d6b`.

The single bounded recovery impact query reached 23 in-policy modules, identified `req:R-0002` and `req:R-0003` as affected requirements, returned no unknown surfaces, and recorded current graph fingerprint `1e8ad7f9e6798cef3e5237af38ac00f5fe2e8cc9245e11404cf8b93140478f4d` at shipped head `470b5b22e2602b882396a46a57787811d9bc44c0`. The requirement-depth truncation is the expected boundary when traversal encounters R-0003 through its recorded dependency on R-0002; it is not missing local or contract coverage. R-0001 and R-0002 remain resolvable Product dependencies.

This is a recovery replan after `loop resolve skip` unintentionally propagated through the serial dependents. It does not reopen Product, Design, or released Task 1 work.

## Settled released prerequisite

`t01-reviewkernel-regression-floor` is represented with the engine-supported task schema and `status: done`. Its exact prior scope, test command, ten verbatim criteria, four ReviewKernel contracts, six exact designed modules, six exact proposed edges, model tier, and approved depth policy remain intact as the v2.17.12 / `470b5b2` evidence anchor. The settled row is placed after t09, so index 0 and the first executable task are t02. The engine must not execute, repeat, weaken, or claim t01 as new work.

t02 retains its exact approved `deps: ["t01-reviewkernel-regression-floor"]`. That dependency is satisfied mechanically by the settled `done` row.

## Executable order

1. **t02 — Canonical enforcement decision and receipts.** Add the sole live/unproven/advisory classifier, exact workspace/session/run/revision evidence identity, 300-second trust boundary, attributable advisory record, and atomic storage.
2. **t03 — Enforcement gates and projections.** Make entry, dispatch, stage emission, claim, loop/review closure, status, dashboard, artifacts, and retro consume the same decision. Strict refusal occurs before state creation; live entry consumes its own PreToolUse receipt without an extra probe.
3. **t04 — Collision registry and classification.** Add the versioned registry, exact-governed-state screen, known-driver denial, helper allowlist, unknown normal/strict tiers, signed-root discovery, and durable interference events.
4. **t05 — Collision adapters and audit.** Wire Claude/Codex hooks and canonical UI/retro projections. Status reads durable summaries and performs no rediscovery.
5. **t06 — Durable merge receipt and read-only eligibility.** Extend managed registration and add the orchestrator-owned merge receipt plus read-only eligibility proof. No automatic removal is reachable.
6. **t07 — Preservation, crash, and replay matrix.** Implement the locked exact-path no-force cleanup primitive behind an uncalled boundary and prove every preservation case, failure point, and already-clean replay. Automatic cleanup remains disabled.
7. **t08 — Post-removal governance continuity.** Prove EM, graph/evidence reconciliation, retro, status, and final-signoff inputs remain canonical after a directly exercised eligible removal; evidence-held trees stay retained.
8. **t09 — Enable automatic cleanup and cross-host conformance.** Only after t02–t08 pass, connect the orchestrator merge boundary to automatic cleanup. This is the final and only enablement task.

The prior t02–t09 ids, scopes, tests, criteria, dependencies, types, contracts, new-module declarations, design edges, impact policies, and model tiers are preserved byte-semantically. Their array order is unchanged; only the settled t01 row follows them.

## Acceptance and Design coverage

- Released prerequisite t01 satisfies AC1–AC10.
- t02 owns AC11, AC13, and AC16.
- t03 owns AC12, AC14, and AC15.
- t04 owns AC17–AC20.
- t05 owns AC21.
- t06 owns AC22.
- t07 owns AC23–AC25.
- t08 owns AC27.
- t09 owns AC26 and AC28.

Coverage is exact across the nine supported task rows: 28/28 verbatim criteria, 11/11 contract ids, 25/25 exact designed module identifiers, 37/37 exact proposed edges, and one approved typed policy of local depth 3, contract-only boundaries, contract depth 1, and requirement depth 1. Pending executable tasks own the remaining 18 criteria; the settled t01 row owns the first 10 and is excluded from execution by `status: done`.

## Fail-closed retention matrix

The released floor remains binding for wrong-producer, copied, sibling-worktree, stale-lease, wrong-run, missing-slot, duplicate-slot, engine-mismatch, substantive-mutation, evaluator-outage, moved/dirty/stale-tree, unsupported-Python, missing-contract-shape, and conflicting-continuation negatives. Recovery cannot weaken or rerun that floor as new scope.

Enforcement retains strict absent-hook zero-state refusals for new, loop init, review start, stage emission, and claim; advisory-without-actor refusal; mid-run liveness loss; mismatched, foreign, and stale session receipts at the 299/300/301-second boundary; and identical classification/evidence identity across every consumer. Recovery may obtain fresh proof or an attributable advisory acknowledgment only; it cannot fabricate live status.

Collision isolation remains a no-op outside exact governed state; allows document, spreadsheet, presentation, and visualization helpers silently; denies registry-known competing delivery skills and agents in governed state; advises unknowns normally and denies them only under strict isolation; never claims an inactive hook denied; recognizes signed multi-file roots only; ignores same-named unsigned directories; fails closed on unreadable or ambiguous candidates without inventing identity; and requires an attributable exact-root override.

Cleanup preserves every dirty, untracked, staged, unmerged, foreign, unregistered, selected-variant, failed, active, locked, symlinked, path-mismatched, missing-ref, ambiguous-main, merge-in-progress, and evidence-needed worktree. The same matrix applies at the initial and locked last-moment reads. Repository/path/type/branch/tip/primary-tip/cleanliness/lifecycle/variant/retention changes, pre-receipt crashes, Git failure, partial absence, or inconsistent registration all preserve state. Names, prefixes, statuses, messages, fetches, force flags, broader targets, branch deletion, evidence deletion, and stronger retries never establish eligibility.

Crash coverage remains before merge, after Git merge but before durable receipt, after receipt but before removal, and after exact removal but before outcome persistence. Only the durable receipt permits cleanup; one same-receipt maintenance replay may record already-clean when both exact path and exact registration are absent. An inconsistent postcondition becomes permanent manual attention for that outcome fingerprint.

## Rollout and rollback

Automatic cleanup is disabled through t08. It becomes reachable only in t09 after enforcement, collision isolation, durable receipt/eligibility, the full preservation/crash matrix, and post-removal governance continuity have passed independently.

Rollback may set enforcement to warn, disable collision screening, and return cleanup to manual. It retains truthful unproven/advisory status, interference and merge/cleanup records, branches, commits, governance evidence, the released ReviewKernel floor, and human final-signoff authority.

Any change to a remaining task definition, acceptance criterion, released evidence anchor, approved contract/module/edge, depth policy, cleanup invariant, or enablement order is Design drift and requires a new approved Design.
