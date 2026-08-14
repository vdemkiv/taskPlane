# Verification before completion

Evidence before assertions — never claim "done", "fixed", or "passing"
without having run the thing that proves it, in this session, and read its
output. taskplane enforces the big ones mechanically (DoD runs the tests,
the scope diff, the gates), but the habit applies to every claim:

- "tests pass" → you ran the declared test command and saw 0 failures.
- "it builds" → you built it.
- "the bug is fixed" → the old repro now passes AND the new regression
  test exists.
- "docs updated" → you opened the doc and the changed behavior is there.

If you can't verify (no env, no data), say "cannot verify" — that's a real
status, and two of them on acceptance criteria means the requirement was
under-refined.

## Proportional verification

Fresh evidence does not mean repeating every available test after every edit.
For a repair cycle, batch related fixes first, then run each distinct failure
cluster once and one combined affected-radius check before submission. The
remote CI matrix is the single full-suite authority unless the requirement,
contract, or human explicitly asks for a local full run.

Documentation-only and generated-artifact changes use static generation,
schema, link, or byte-diff checks. They do not trigger runtime suites,
detached-baseline execution, or repeated validation of unchanged components.
If a targeted check exposes a new failure cluster, repair that cluster and
rerun only its failed selector plus the final affected-radius check.
