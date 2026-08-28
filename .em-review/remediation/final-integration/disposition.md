# R-0002 final integration disposition

## Outcome

**Ready for the independent `FINAL-EVAL`; not yet a strict AC8 pass.**

FINAL-I joins the exact high-gate, M1-I, and M2-I histories and retains a
machine-verifiable 72-row inventory. It does not turn the two approved bypasses
into green evidence, and it does not claim that the later independent evaluator
or one-time full suite has run.

| State | Findings | Count |
|---|---|---:|
| Independently green at the retained high gate | High findings outside the two exceptions | 19 |
| Attributed, non-independent H1-I exception | H-03–H-08, H-14, H-15, H-19, H-22, H-26, H-30, H-34 | 13 |
| Attributed, non-independent H3-C exception | H-23, H-25 | 2 |
| Focused integration green, awaiting independent final evaluation | All 28 medium and all 10 low findings | 38 |
| Missing, suppressed, downgraded, or silently deleted | none | 0 |

The strict high criterion remains **AC5 not satisfied** because 15 high rows
are accepted exceptions rather than independently green results. The final
criterion remains **AC8 pending independent final evaluation** because
`FINAL-EVAL` owns the exact-candidate evaluator and the single complete test
suite run.

## Exact inventory

The retained review snapshot contains exactly 72 ID-joined rows:

- 34 high: H-01 through H-34
- 28 medium: M-01 through M-28
- 10 low: L-01 through L-10

Every row is checked against `design/contract.json` and `plan/tasks.json` for
its owner, contract boundary, wave, task, dependency class, prerequisites, and
focused evidence selector. Every low row also retains its companion wave and
shared-owner or pairwise-disjoint execution mode. A missing, duplicate,
relabelled, out-of-scope, or low-only-tail row fails closed.

The original ignored review file is identified by SHA-256
`74745ab55c2d0313c9c4271697f2ee024a3e3966ea46f4323a18c9b26f5f6041`.
Its exact ID-joined content is retained at
`.em-review/remediation/final-integration/findings-snapshot.json` and pinned by
SHA-256
`7f68603d889fc932a7f022c4df4b53e48317ce71fbc3608f4d27704d5a2f30ab`.

## Preserved exception authority

1. `H1-I-selector-receipt-authority` — Volodymyr Demkiv, “bypass and proceed
   with the plan”; 13 affected findings; independently green: **false**.
2. `H3-C-retention-gaps` — Volodymyr Demkiv, “bypass and proceed”; 2 affected
   findings; independently green: **false**.

Both repository documents and their SHA-256 identities are validated against
the high-gate result. Any relabelling to independent green is rejected.

## Candidate and ancestry binding

The production join resolves clean `HEAD` at validation time, records its
exact commit and tree, hashes every retained input, and verifies that every
finding-owning task plus H1-I, H2-I, H3-I, HG-EVAL, M1-I, and M2-I is an exact
ancestor. It also verifies each declared Plan dependency between those task
commits and refuses post-integration mutation of the M1-I or M2-I focused
proofs.

## Next governed step

`FINAL-EVAL` must independently evaluate this clean exact candidate, run the
focused final integration evidence, and then run
`python3 -m pytest taskplane/tests -q` once. Until that evidence exists, this
artifact remains a truthful readiness disposition, not final sign-off.
