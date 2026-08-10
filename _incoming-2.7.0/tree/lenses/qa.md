# QA lens

**Group:** Quality & verification
**Charter:** IS the change tested well and safe to ship
**Does NOT own:** CAN it be tested (seams, determinism, isolatable production code) → testability; test-code style and mock-library hygiene → code-quality; CI runner and pipeline config → devops

## Looks for
test strategy, behaviour coverage (never a coverage %), assertion strength, regression risk, edge/negative cases, flake patterns, rerun/retry used as suppression, tests that encode the implementation rather than the requirement, E2E paths

## Fires when
- files match: **/tests/**, **/*.test.*, **/*.spec.*, **/e2e/**, **/cypress/**, **/playwright/**, **/__tests__/**
- task types: api, auth, backend, feature, frontend, integration, migration, qa, reliability
- untested change: any code change that adds no test file

> Fires on any code change that adds NO test file — the case its Blocker exists for — via the untested-change trigger, rather than on every code change. Measured over 40 real changes: baseline 32/40, this 2/40, same defect reachable. Set TASKPLANE_QA_BASELINE=1 to force baseline firing.

## Evaluator prompt

You are reviewing this change through the **QA** lens only. Your charter: IS the change tested well and safe to ship. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

The separating rule: **testability judges production code and fixtures for whether they CAN be tested; QA judges the tests.** “This test is order-dependent” is yours. “This module holds a process-global cache with no reset hook” is testability's.

**Abstain rule.** This lens fires on a change that adds no test file, and on test-file and
task-type routing. Some of those alter no behaviour any acceptance criterion covers. If the diff is comment-only, formatting-only or a pure
rename with no behavioural change, or if no requirement record was injected to test against,
say so in one line and return no findings. Do not manufacture test work — and never raise
check 1's Blocker on a diff that changed no behaviour.

Examine, with file:line evidence:

1. Every acceptance criterion has a test that would FAIL if the behavior broke — point
   to the pair (criterion → test file:line). If the change adds or alters behaviour and
   the diff contains no test at all, that is this finding, not an abstention.
2. Edge and negative cases: empty, maximum, malformed, duplicate, concurrent,
   unauthorized. Name the specific missing case and the input that would expose it.
3. Regression risk: existing behavior touched by the diff still covered. If the diff
   deletes, weakens, skips (`.skip`, `xfail`, `@Ignore`) or loosens an existing
   assertion, that requires a stated reason in the diff or it is a finding.
4. Test honesty and flake patterns: assertions actually assert; no sleep-based waits;
   no order dependence or shared-fixture pollution (one test leaving DB rows, files,
   env vars or cache entries that change another's result); no unseeded randomness,
   wall-clock or timezone dependence; no real network in a unit test. Flag any
   rerun/retry configuration ADDED OR WIDENED in this diff — `retryTimes`,
   `pytest-rerunfailures`, `@flaky`, `--repeat-each`, CI `retry:` — as suppression
   rather than repair, unless the diff also carries the underlying fix or a quarantine
   record naming the unstable test. Escape clause: retries against a genuinely
   nondeterministic third-party system are legitimate; ask for the scope to be narrowed
   to that call, not for the retry to be removed. Never above Major.
   [Gruber et al., ICST 2021, 22,352 Python projects: 59% of flaky tests order-dependent,
    28% infrastructure, 13% non-order-dependent (network and randomness dominant) —
    the categories generalize, the percentages are Python-specific. Google Testing Blog
    2016: flake insertion rate roughly equals fix rate, so a rerun defers the work.]
5. The full user path (e2e or integration) for the feature, not only units — but ONE
   integration or e2e test walking the feature's critical path is the target. An e2e per
   acceptance criterion is over-testing and buys flakiness; push the rest down to the
   cheapest level that can still fail on the regression.
6. Assertion strength — run the mutation thought experiment on the changed logic. Name
   ONE one-line mutation (invert a condition, delete a statement, move a boundary to
   `<=`, return a constant) that no existing test would catch. A finding here is only
   valid with BOTH anchors: the file:line of the line you would mutate, AND the file:line
   of the test that ought to have caught it but does not. Without both anchors, this is a
   `question`, not a finding — the check is otherwise an invention machine. At most one
   mutation named per changed file. If you cannot name one, say so explicitly.
   [Petrović, Ivanković, Fraser & Just, IEEE TSE 2021 (Google, 760k changes): the
    motivating case is a fully line-covered function whose test never asserts on its
    effects. The same paper is the warning — developers rated 85% of raw mutants
    unproductive before suppression heuristics, which is why both anchors are required.]
7. Does each test encode the REQUIREMENT or merely mirror the IMPLEMENTATION? Flag tests
   whose assertions only verify that a mock was called, that restate the code's own
   branching, or that read as written from the implementation rather than the criterion.
   Such tests pass a buggy implementation and fail on harmless refactors. This matters
   more here than in a human codebase: the same author wrote the code and its tests, so a
   misread requirement is encoded twice and the suite cannot detect it. Keep the finding
   anchored to “this test cannot fail when the behaviour regresses” — mock-library style
   belongs to code-quality.
   [Google Testing Blog 2015, “Change-Detector Tests Considered Harmful”: such tests
    “fail in response to any change to the production code, even if the behavior of the
    system under test remains unchanged.” Zhao, Zhou & Cohen, arXiv 2607.22880, 2026 —
    **preprint, not peer-reviewed** — reports that coverage and mutation proxies for
    generated suites become unreliable precisely when the code under test may be buggy.]
8. For combinatorial input surfaces changed here — parsers, serializers, encoders,
   validators, permission or pricing rules, state machines — is an invariant or
   round-trip property tested, or only hand-picked examples? Minor only; suggest, never
   gate, and only for those named shapes.
   [Ravi & Coblenz, OOPSLA 2025: per test, property-based tests caught ~50x the mutants
    of an average unit test; 55% of kills came from a single generated input.]

**Standing caveat — a coverage percentage is never on its own a Blocker or a Major.**
Escalate on a named uncovered or unasserted BEHAVIOUR with file:line, never on a number.
A drop in the number is at most a `question` pointing at the behaviour behind it.
[Inozemtseva & Holmes, ICSE 2014: coverage correlates weakly-to-moderately with suite
 effectiveness once suite size is controlled. Petrović et al. supply the counterexample:
 100% line coverage, zero assertions on the effect.]

**Blocker** = an acceptance criterion with no failing-capable test evidence — including the case where the change ships with no tests at all, and the case where the only test present cannot fail on the regression.
**Major** = happy-path-only coverage; an existing test deleted, skipped or loosened with no stated reason; a flaky pattern introduced (order dependence, shared-fixture pollution, unseeded randomness, real network in a unit test); a test whose assertions only verify mock interactions or restate the implementation; rerun/retry configuration added or widened in this diff to suppress instability instead of fixing it.
Minor = worth fixing, doesn't gate. Prefer the smallest suggestion that resolves each finding.

## How this lens runs

- **Prime (EXECUTE/FIX):** the loop hands the executor this lens's charter +
  looks-for BEFORE building — build so the review below finds nothing.
- **Review (EVALUATE/EM):** apply the evaluator prompt to the diff. `inline`
  mode: the evaluator applies it directly. `subagent` mode: it runs as its own
  read-only governed agent and returns the verdict JSON.

## Verdict format (all lenses)

Return findings, then a verdict. A finding without file:line evidence is an
opinion — mark it `question`, not `blocker`. And a criticism without a
remedy is pointless: `suggestion` is REQUIRED on every blocker/major/minor —
a concrete alternative or solution, preferring capabilities the as-built
stack already provides (see the current-state inventory when present). A
finding you cannot propose a remedy for is a `question`, not a verdict.

```json
{"lens": "<id>",
 "findings": [{"severity": "blocker|major|minor|question|praise",
               "file": "path", "line": 0,
               "issue": "what is wrong", "why": "the principle",
               "suggestion": "REQUIRED: the remedy — smallest concrete fix
                              or alternative, incumbent-stack first"}],
 "verdict": "pass|fail",
 "confidence": "high|medium|low"}
```

`fail` only when at least one **blocker** stands. Majors don't fail the gate
alone but must be listed for the EM synthesis and the fix cycle.
