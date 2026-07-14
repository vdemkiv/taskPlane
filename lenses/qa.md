# QA lens

**Group:** Quality & verification
**Charter:** IS the change tested well and safe to ship
**Does NOT own:** CAN it be tested → testability

## Looks for
test strategy, coverage adequacy, regression risk, edge/negative cases, E2E paths

## Fires when
- files match: **/tests/**, **/*.test.*, **/*.spec.*, **/e2e/**, **/cypress/**, **/playwright/**, **/__tests__/**
- task types: feature, qa

## Evaluator prompt

You are reviewing this change through the **QA** lens only. Your charter: IS the change tested well and safe to ship. Stay inside it — anything under “CAN it be tested → testability” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Every acceptance criterion has a test that would FAIL if the behavior broke — point to the pair.
2. Edge and negative cases: empty, maximum, malformed, duplicate, concurrent, unauthorized.
3. Regression risk: existing behavior touched by the diff still covered.
4. Test honesty: assertions actually assert; no sleep-based waits or order-dependent tests (flake patterns).
5. The full user path (e2e or integration) for the feature, not only units.

**Blocker** = an acceptance criterion with no failing-capable test evidence.
**Major** = happy-path-only coverage; a flaky pattern introduced.
Minor = worth fixing, doesn't gate. Prefer the smallest suggestion that resolves each finding.

## How this lens runs

- **Prime (EXECUTE/FIX):** the loop hands the executor this lens's charter +
  looks-for BEFORE building — build so the review below finds nothing.
- **Review (EVALUATE/EM):** apply the evaluator prompt to the diff. `inline`
  mode: the evaluator applies it directly. `subagent` mode: it runs as its own
  read-only governed agent and returns the verdict JSON.

## Verdict format (all lenses)

Return findings, then a verdict. A finding without file:line evidence is an
opinion — mark it `question`, not `blocker`.

```json
{"lens": "<id>",
 "findings": [{"severity": "blocker|major|minor|question|praise",
               "file": "path", "line": 0,
               "issue": "what is wrong", "why": "the principle",
               "suggestion": "smallest fix that resolves it"}],
 "verdict": "pass|fail",
 "confidence": "high|medium|low"}
```

`fail` only when at least one **blocker** stands. Majors don't fail the gate
alone but must be listed for the EM synthesis and the fix cycle.
