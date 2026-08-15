# R-0006 plan — integrated host-capability repair

This plan realizes approved Design Contract fingerprint
`3e7ceb20fe28d9194c7c2ca1bfca334a34236de6b337d24a5d0c596f9872b7d2`
without changing its authority boundaries. Host transports may differ, but hook
execution, ReviewKernel collection, evaluator output, submission validation,
dispatch receipts, and telemetry converge on the five approved contracts.
Workers never gain gate, approval, state-advance, or contract-clear authority.

## Why the implementation is one batch

The original five-task sequence was mechanically valid but operationally
self-blocking: the first task had to pass independent ReviewKernel evaluation
before later tasks could implement the producer receipt and output contracts
that collection required. That ordering produced real leased result files but
correctly failed collection because Codex emitted no host-observed producer
receipt. Splitting capability discovery, provenance, workflow adoption,
routing, telemetry, and their pinned CI repairs therefore creates artificial
gates across one runtime protocol.

The recovery preserves every approved module, edge, contract, impact bound,
and acceptance criterion. It changes only execution order: all runtime protocol
work is completed and evaluated as one coherent repair batch; documentation
and one final unchanged full-suite run follow once.

## Bounded impact and graph policy

The approved impact derivation covers all 24 design modules with 21 impacted
nodes, no unknown modules, and affected requirements R-0001, R-0005, and
R-0006, with R-0002 as a dependent requirement. Its reported truncation is the
approved one-hop requirement boundary, not permission to expand history.

Both tasks retain the same graph policy: three local hops, `contract-only`
boundaries, one contract hop, and one requirement hop. Collectively they own
all 24 proposed modules, all 38 design edges, all five exact contract ids, and
all 14 verbatim acceptance criteria.

## Two-stage delivery

1. **t1 — integrated host-capability repair.** Preserve the committed truthful
   capability snapshot and bounded reference-first requirement envelope, then
   complete exactly-once Codex hook selection, Claude workflow ReviewKernel
   parity, schema-validated evaluator output, fail-closed Stop enforcement,
   effective portable model/effort routing, evaluation observability, provider
   token reconciliation, and the three pinned CI clusters. The implementation
   must produce real host-observed producer receipts; it may not fabricate or
   bypass provenance. The declared targeted command also owns the two existing
   onboarding assertion files named by the recorded baseline; receipt-based
   readiness remains authoritative and stale tests are corrected rather than
   weakening it. The tp-engineering scenario fingerprint is refreshed because
   its declared agent-role source gains the required output-schema boundary.
   One command covers the complete runtime protocol after focused red/green
   work.
2. **t2 — truthful guidance and final validation.** Update only the five
   approved host-facing references after behavior is fixed, then run
   `python3 -m pytest taskplane/tests -q` exactly once. Cross-version/platform,
   packaging, manifest, release-history, docs, hook, and dispatch-parity floors
   remain intact and may not be skipped, xfailed, loosened, or de-gated.

## Validation budget

The existing baseline is not repeated: 7 failures, 2774 passes, 2 skips, and
861 subtests. During t1, each failure cluster receives targeted red/green
checks, followed by its one declared combined targeted command. No repository-
wide test loop is allowed. T2 performs static documentation checks and the one
final full-suite run after all runtime and documentation changes are complete.

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
