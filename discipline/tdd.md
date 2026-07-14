# Test-driven development (the executor's default)

Write the failing test FIRST — it's the acceptance criterion made
executable. Then the smallest code that passes. Then refactor with the
test green. Under taskplane this isn't ceremony: the requirement's
acceptance criteria map 1:1 to tests, the DoD runs them, and the evaluator
demands the test↔criterion pair. Red → green → refactor, per criterion.

Rules of thumb: a test that never failed proves nothing — watch it fail
once. One behavior per test; name it after the behavior. No sleeps; no
order dependence; fakes at the boundaries you own, real code inside them.
If the code is hard to test, that's a testability finding — surface it,
don't force the test.
