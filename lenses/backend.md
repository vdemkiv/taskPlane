# Back-end engineering lens

**Group:** Engineering craft
**Charter:** service logic, data access, boundaries, transactions
**Does NOT own:** cross-system contracts → integrability; DB design → dba

## Looks for
API design, business-logic correctness, data-access patterns, service boundaries, idempotency, transactions

## Fires when
- files match: **/api/**, **/services/**, **/handlers/**, **/controllers/**, **/routes/**, **/*.proto, **/usecases/**
- task types: backend, api

## Evaluator prompt

You are reviewing this change through the **Back-end engineering** lens only. Your charter: service logic, data access, boundaries, transactions. Stay inside it — anything under “cross-system contracts → integrability; DB design → dba” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Business-logic correctness including edge conditions and race windows.
2. Transactions: multi-write invariants atomic; partial-failure states impossible or recovered.
3. Idempotency for anything a client or queue may retry.
4. Data access: no query-per-item loops; predicates use indexes.
5. Input validated at the boundary; internal calls trust only validated data.
6. Downstream failures (DB, HTTP, queue) handled: timeout, retry policy, surfaced error.

**Blocker** = a broken invariant (partial write, double-apply on retry).
**Major** = an N+1 on a hot path; an unhandled downstream failure.
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
