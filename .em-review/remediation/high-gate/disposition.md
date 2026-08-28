# R-0002 high-gate disposition

## Outcome

**Proceed to the medium/low waves only under Volodymyr Demkiv’s two explicit
human bypasses.** This is an exception-aware delivery disposition, not an
independent AC5 high-gate pass.

The evidence is bound to clean candidate
`ecc72d4cdf0d90995e3968e2a57f89f61814cf39` (tree
`9dc0b0f1b3d529f35e275a608c5a0b03fcdd10ae`). The combined H2/H3 integration
command passed all 18 checks in 64.46 seconds. Those checks include their
fail-closed mutation paths and the H3 integration’s exact accepted-retention
record binding.

| Disposition | Findings | Count |
|---|---|---:|
| Independently green on the candidate | H-01, H-02, H-09–H-13, H-16–H-18, H-20, H-21, H-24, H-27–H-29, H-31–H-33 | 19 |
| Accepted H1-I selector-receipt-authority exception | H-03–H-08, H-14, H-15, H-19, H-22, H-26, H-30, H-34 | 13 |
| Accepted H3-C retention exception | H-23, H-25 | 2 |
| Missing, suppressed, downgraded, or self-attested green | none | 0 |

All 34 high IDs are present exactly once in the machine-readable result.

## Focused evidence

```text
python3 -m pytest -q -p no:cacheprovider \
  taskplane/tests/test_em_h2_integration.py \
  taskplane/tests/test_em_h3_integration.py

18 passed in 64.46s (0:01:04)
```

The test inputs are content-bound in `results.json`. No full suite or lens was
run for this task.

## Accepted exceptions

### H1-I selector receipt authority

- Authority: Volodymyr Demkiv, “bypass and proceed with the plan”.
- Record: `design/backlog/r0002-h1i-selector-receipt-authority.md`.
- SHA-256: `f705432f38a95f663bdaa3678ed42e2e1ed7c7e2bc5e03d5b439cb20dcdfe890`.
- Effect: all thirteen H1 rows depend on the affected selector-receipt join,
  so none of them is represented here as independently green.

### H3-C retention gaps

- Authority: Volodymyr Demkiv, “bypass and proceed”.
- Record: `design/backlog/r0002-h3c-retention-exceptions.md`.
- SHA-256: `bd40d659569919abe09b797cf7df66c81f7e1e73ad425efc48dc417f52d550a5`.
- Effect: H-23 and H-25 remain accepted exceptions. H-24 is independently
  green. The residual trace and abandoned-handle retention gaps remain open.

## Planned verifier gap

The Plan names `python3 -m taskplane.remediation_trace verify-high`, but the
module implements no `verify-high` command or CLI entry point. A canary call
using a deliberately missing findings file exited 0, produced no output, and
performed no validation. The Plan’s named
`taskplane/tests/test_em_remediation_integration.py` AC5 selector is also
absent.

Therefore this artifact does not treat that exit code as evidence and does not
claim an engine high-gate PASS. The strict AC5 statement (“no high issue is
merely waived”) remains unsatisfied. Proceeding to medium/low is authorized
only by the two attributed user bypasses above.
