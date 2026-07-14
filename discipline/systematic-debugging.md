# Systematic debugging (for FIX steps)

Never fix what you haven't reproduced. The evaluator's failure entry gives
you the repro command — run it, watch it fail, THEN hypothesize.

1. Reproduce (exact command, exact failure).
2. Localize — bisect the path: which layer, which function, which input.
   The dependency graph tells you what's upstream of the symptom.
3. One hypothesis at a time; the experiment that can disprove it fastest
   goes first.
4. Fix the cause, not the symptom; if the symptom hides a design problem,
   say so in the gate note — that's requirement/architecture feedback.
5. Add the regression test BEFORE declaring done (the fixer's DoD).
6. If two fix cycles haven't cracked it, stop — escalation exists because
   thrashing burns cycles; write up what you ruled out.
