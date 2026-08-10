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
