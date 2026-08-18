# R-0010 plan — structured criteria and process-tree no-push closure

R-0010 is intentionally limited to the blockers confirmed by engineering
decision 0046 plus the amended worktree-lifecycle isolation defect: structured
acceptance supplied outside `requirement.text` is lost before canonical DoR, a
validation descendant can push to an explicit URL with `--no-verify`, and a
contract bound to worktree A can intercept lifecycle events belonging to
worktree B. The preceding focused R-0009 correction plan is
preserved unchanged in
`plan/r0009-focused-correction-plan.pre-r0010.md` and
`plan/r0009-focused-correction-tasks.pre-r0010.json`.

## Bounded impact and graph contract

The single required impact projection for the amendment confirmed
`taskplane/tp.py` and `taskplane/taskplane_lite.py` as the exact lifecycle
owners. It returned 30 impacted nodes, no unknown modules, and the current
graph fingerprint
`cadabcfd487b979671df0220062d0bd0fe3c7dbd2b5565f0620e0cd8c2761667`
at HEAD `33d1ee985f4319cc48b6d79700a1caf05c16c585`. Every task retains the typed
policy: local depth 3, `contract-only` boundaries, contract depth 1, and
requirement depth 1.

The plan declares two exact contracts:
`contract:review-dor-evidence` and
`contract:review-validation-sandbox`. Its eight exact edges bind canonical
DoR production/consumption, sandbox production/consumption, and focused test
validation through those boundaries. The amendment introduces no third
cross-boundary contract or invented edge: it tightens lifecycle ownership
inside the existing Taskplane control plane. Explicit graph-node declarations
cover all nine owning modules, `taskplane/tests`, and both contracts.

## Delivery

The three implementation tasks are scope-disjoint and can run concurrently in
isolated worktrees. A small test-only task joins the first two because AC9
requires one proof that those R-0009 corrections compose without changing
R-0009. Taskplane's exact-file scopes keep the lifecycle regression separate
from that compatibility task.

1. **Canonical structured criteria.** Admit the existing structured criteria
   field as a first-class canonical DoR source even when `requirement.text` is
   empty or unrelated. Preserve source order and authoritative source/target/
   revision provenance; assign deterministic identities; deduplicate only true
   semantic duplicates; reject empty or malformed entries fail-closed; and
   carry the same records through ledger, approval evidence, JSON, Markdown,
   HTML, and inline projections. A failed, unproven, or unsupported entry must
   block approval exactly like a text-extracted criterion.
2. **Process-tree no-push isolation.** Establish isolation before launch and
   bind it to validation run, sandbox root, cwd, environment, executable, and
   descendants. Enforce remote-write denial below Git command spelling so an
   explicit URL/refspec, `--no-verify`, alternate configuration, hook override,
   shell/package wrapper, child, or grandchild cannot bypass it. Verify the
   isolated destination and reviewed checkout before/after. Missing, tampered,
   escaped, or unverifiable isolation blocks execution and cannot record pass,
   while ordinary reads, builds, tests, and disposable local writes continue.
3. **R-0009 compatibility verification.** Run one bounded integration selector
   covering the new cross-contract fixture plus the existing production,
   routing, and session regressions. Audit the selected tests for removal,
   skip, xfail, loosened floors, or reclassification. This task changes tests
   only and depends on both implementations.
4. **Exact-worktree lifecycle isolation.** Resolve and bind contract lookup,
   lifecycle interception, processed-event identity, and duplicate-delivery
   keys to the exact canonical workspace/worktree plus task lifecycle id—not a
   repository family, shared Git directory, host event shape, or path prefix.
   With A governed, `create_thread` and unrelated work in sibling worktree B
   must proceed under B's own state while A remains governed. Re-delivering the
   same event for A stays idempotent; an identical-shaped event for B is
   independent. Tests cover sibling worktrees, shared `.git` metadata,
   path-prefix collisions, duplicate delivery in A, and identical host events
   across A/B without clearing or weakening either contract.

## Exact non-functional requirements

- **security**: The no-push policy is enforced at the real validation process-tree boundary and cannot be bypassed by explicit URLs, --no-verify, Git configuration, hooks, wrappers, or descendants; inability to prove isolation blocks execution.
- **architecture**: Structured acceptance is one canonical input to DoR, ledger, projections, and approval; validation isolation is one host-neutral process-tree contract; lifecycle governance is scoped by exact resolved worktree and task identity rather than repository family.
- **data-safety**: Neither the reviewed checkout nor any local or remote Git destination may change during disposable validation, and structured criteria/evidence cannot be silently dropped, duplicated, or reordered.
- **sre**: Isolation establishment, blocked attempts, and criterion-propagation failures produce stable actionable states with bounded execution and no partial or false-success record.
- **integrability**: Existing R-0009 consumers remain compatible, and multiple Codex tasks may work concurrently in separate worktrees of one repository without clearing or weakening each other's contracts.

## Runnable validation

| Task | Criteria | Command |
|---|---|---|
| Canonical structured criteria | AC1–AC4 | `python3 -m pytest -q taskplane/tests/test_review_structured_criteria.py` |
| Process-tree no-push isolation | AC5–AC8 | `python3 -m pytest -q taskplane/tests/test_review_process_tree_isolation.py` |
| R-0009 compatibility | AC9 | `python3 -m pytest -q taskplane/tests/test_r0010_r0009_compatibility.py taskplane/tests/test_review_production_integration.py taskplane/tests/test_review_routing.py taskplane/tests/test_review_session.py` |
| Exact-worktree lifecycle isolation | AC10–AC11 | `python3 -m pytest -q taskplane/tests/test_contract_worktree_isolation.py` |

No additional product feature, broad cleanup, artifact redesign, routing change, or
host-specific review authority is authorized. Rollback disables the corrected
paths for new reviews only and returns to the existing R-0009 implementation;
it never rewrites review evidence, criteria, sandbox records, or test history.
