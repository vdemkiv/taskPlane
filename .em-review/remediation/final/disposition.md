# R-0002 final Engineering disposition

## Outcome

**Remediation delivery is complete under the attributed exception and
selective-rerun policy.** The final clean candidate is
`4dacd46faba12b2662f265c7665bf13fd5bc2cf8` with tree
`434eafc2c1fa7804c35d397a0bbca79bb59692da`.

The 72-row inventory is intact: 57 findings are independently green and 15
high findings remain explicitly accepted, non-independent exceptions. No row
was removed, hidden, downgraded, or relabelled green.

| Disposition | Count |
|---|---:|
| Independently green high findings | 19 |
| Independently green medium findings | 28 |
| Independently green low findings | 10 |
| Accepted H1 selector-receipt exceptions | 13 |
| Accepted H3 retention exceptions | 2 |
| Missing or suppressed | 0 |

Strict AC5 remains **not satisfied** because its 15 exceptions are not
independent PASS results. Strict AC8 also remains **not satisfied** because the
user explicitly rejected another aggregate rerun after the final four local
corrections. Completion therefore rests on the preserved green shard receipts,
the exact final-candidate affected-selector checks, and the attributed human
exceptions—not on a false claim of an all-green exact-SHA monolithic suite.

## Test evidence

The 265 test files were partitioned into ten pairwise-disjoint shards and run
in parallel at `b9adea3e61781431feb9af9b1fd3330b18591507`. The manifest digest
is `4ece8a2091cf0ca400bfecc213653795e46d4a8cd52826a1ffc2f4ec951b3f91`.
That run produced 4,822 passes, four localized failures, five skips, and 738
passing subtests.

The four failures were classified and corrected without repeating the full
suite:

1. Two strict quality-inventory omissions: fixed and independently validated
   with Ruff, strict mypy, graph-boundary checks, and the exact H-09 selectors.
2. One duplicate test import identity: fixed test-only; both file orders and
   the exact live graph-loader selector are green.
3. One architecture-authority scoping bug: fixed in production; the Plan gate,
   R-0002/R-0013 tamper cases, release history, packaging, cycle ratchet, and
   exact verifier seal are green.

At the final candidate, four focused groups executed 183 passing pytest
checks, plus Ruff and strict mypy. The effective disposition is 4,826 passed,
zero unresolved failures, five skips, and 738 passing subtests. This is
selective retained evidence, not a claim that every test reran at the final
SHA.

## Architecture and release integrity

The real `checkpoint ↔ governed_commands` cycle is removed. The remaining
protected SCC has 7 members, 13 edges, and 15,701 physical LOC against the
15,822 ceiling. Its current inventory digest is
`6343dd0697926107b22121f725be4dab58d2f8a4c0987e42bf48fc8f8353297d`;
the approved policy digest remains
`55ab2022bdcde4c6a1c363e2b46064ac1e4d583d0c9a900a495c7a83867c5735`.

Volodymyr Demkiv approved the exact verifier transition from depgraph blob
`285fdba9a1207cdf729079aa6d111283572ff8d8` to
`13aca2b71e907dc8fafa7351786cfefd39075e30`. The historical depgraph blob and
CI verifier blob remain unchanged. Ordinary Design contracts no longer inherit
the special R-0002/R-0013 architecture map; the two pinned authorities remain
fail-closed for missing or mutated evidence.

## Authority and external state

The retained exception authorities remain exactly those documented by the
high gate. This completion does not infer or exercise push, tag, publication,
origin/main mutation, PR merge, or release authority.

Machine-readable evidence is in `results.json`; the delivery lessons and
follow-up constraints are in `retro.md`.
