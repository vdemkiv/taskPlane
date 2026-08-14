# Bounded repair plan — R-0005 graph-governance closure

This is a send-back repair for the final engineering gate. It preserves the
completed 2.7 implementation and its existing evidence; it does not reopen the
four historical delivery tasks or rerun their broad suites.

## One implementation task

1. Separate an intentional `contract-only` or requirement-depth policy stop
   from genuine unexplored traversal. Keep the stop visible in
   `policy_blocked`, while graph quality fails closed only for stale data,
   unknown nodes, unresolved edges, incomplete scanners, or real depth
   truncation.
2. Make dashboard/report rendering observational. A render may display a
   cached sign-off verdict, but it must never invoke `_signoff_dod`, discover a
   regression radius, or execute tests.
3. Keep selector-scoped validation selector-scoped. The regression gate may
   still run its static coverage-gap guard, but it must not widen an approved
   `file.py::test_name` contract into whole current/baseline test files.
4. Store requirement acceptance and graph impact once in the immutable review
   envelope. Scoped views preserve the facts and their envelope reference,
   but do not duplicate those large lists under nested records.
5. Verify and record the approved design's exact as-built edges in the graph.
   Contract edges receive explicit file/test/probe evidence in the final EM
   record; no edge is declared merely to satisfy the gate.
6. Run one canonical selective Review from one shared diff and impact envelope,
   collect the routed results, and generate the final EM report from that
   revision. The report dispositions all 26 lenses and uses the same graph,
   findings, and revision identity as the dashboard and gate.

## Validation budget

The implementation task has exactly five focused regression selectors: one
for graph-policy semantics, one for bounded contract traversal, and one proving
dashboard rendering cannot execute DoD, plus one proving selector-scoped
validation cannot widen into whole-file Tier-1 execution, and one proving the
review envelope stores repeated requirement/impact facts once. Graph
realization and documentation are static checks. The final review reuses
already-passed task evidence and does not rerun T1–T4, the full repository
suite, the corpus, or detached baselines.

## Graph policy and risks

Traversal remains six local hops, one contract hop, one requirement hop, with
`contract-only` distributed boundaries. The change does not weaken uncertainty
handling: genuine incomplete traversal still dispatches zero lenses. The main
risk is incorrectly classifying a real depth cutoff as a policy stop; the
focused tests require separate `depth_truncated` and `policy_blocked` evidence
so the two states cannot collapse again.

No version, release, marketplace, packaging, archive cleanup, or unrelated
refactoring is in scope.
