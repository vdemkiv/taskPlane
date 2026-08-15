# R-0006 plan — integrated host-capability repair

This plan realizes approved Design Contract fingerprint
`3e7ceb20fe28d9194c7c2ca1bfca334a34236de6b337d24a5d0c596f9872b7d2`
without changing its authority boundaries. Host transports may differ, but hook
execution, ReviewKernel collection, evaluator output, submission validation,
dispatch receipts, and telemetry converge on the five approved contracts.
Workers never gain gate, approval, state-advance, or contract-clear authority.

## Why the repair is one batch

The original five-task sequence was mechanically valid but operationally
self-blocking: the first task had to pass independent ReviewKernel evaluation
before later tasks could implement the producer receipt and output contracts
that collection required. That ordering produced real leased result files but
correctly failed collection because Codex emitted no host-observed producer
receipt. Splitting capability discovery, provenance, workflow adoption,
routing, telemetry, and their pinned CI repairs therefore creates artificial
gates across one runtime protocol.

The recovery preserves every approved module, edge, contract, impact bound,
and acceptance criterion. It changes only execution order: completed runtime
work, the six failure clusters from GitHub Actions run `31891142855`, truthful
documentation, and final validation are closed as one coherent repair batch.

## Bounded impact and graph policy

The approved impact derivation covers all 24 design modules with 21 impacted
nodes, no unknown modules, and affected requirements R-0001, R-0005, and
R-0006, with R-0002 as a dependent requirement. Its reported truncation is the
approved one-hop requirement boundary, not permission to expand history.

The task retains the same graph policy: three local hops, `contract-only`
boundaries, one contract hop, and one requirement hop. Collectively they own
all 24 proposed modules, all 38 design edges, all five exact contract ids, and
all 14 verbatim acceptance criteria.

## Delivery

1. **t1 — host-capability CI repair.** Preserve the committed truthful
   capability snapshot and bounded reference-first requirement envelope, then
   close the CI failures without weakening the new capability contracts:
   extract loop status/evaluation presentation instead of raising the LOC
   ceiling; make legacy fixtures author canonical evaluator evidence; align
   workflow tests with leased receipt-only transport; keep host-specific route
   receipts outside canonical cross-host artifact comparisons; register the
   three intentional pytest-only files; and document the 16 capability
   variables. The cost ratchet must require exactly three gates—fewer gates
   are incomplete execution, not an efficiency win. Run the exact failed
   selectors once after the batch, then
   `python3 -m pytest taskplane/tests -q` once. Cross-version/platform,
   packaging, manifest, release-history, docs, hook, and dispatch-parity floors
   remain intact and may not be skipped, xfailed, loosened, or de-gated.

## Validation budget

The existing CI baseline is not repeated locally before repair: 28 failures,
2831 passes, and 9 skips. The six failure clusters are repaired together, then
the exact failed-selector set runs once. The repository-wide suite runs once
after source, fixture, workflow, manifest, and documentation changes are
complete; GitHub Actions is the cross-version/platform confirmation.

## Risks, rollout, and rollback

Principal risks are false capability authority, duplicate hook races, stale
workflow resume, foreign or unsupported dispatch arguments, destructive leak
recovery, unobserved producer evidence, provider token double counting, and
regression-floor erosion. Rollout remains additive and fail-closed: readers and
validators first, exact hook/producer claims second, workflow and evaluator
adoption third, strict routing and telemetry last.

Rollback may change only transport selection. It may not accept prose or
invalid JSON, duplicate side effects, unsupported explicit routing, unproved
submissions, translated in-flight leases/revisions, fabricated token zeros, or
worker-owned clear/gate/approval.
