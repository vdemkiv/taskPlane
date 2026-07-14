# Scalability & performance lens

**Group:** Operations
**Charter:** will it hold under load and data growth
**Does NOT own:** runtime reliability/observability → sre

## Looks for
N+1 / unbounded queries, blocking calls, resource ceilings, hot paths

## Fires when
- files match: **/api/**, **/db/**, **/*.sql, **/services/**, **/queries/**
- task types: api, integration, backend
- runs as **subagent** when: **/*.sql, **/db/**

## Evaluator prompt

You are reviewing this change through the **Scalability & performance** lens only. Your charter: will it hold under load and data growth. Stay inside it — anything under “runtime reliability/observability → sre” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Complexity of new hot paths against realistic data growth, not demo data.
2. Unbounded work: queries without LIMIT, load-everything collections, unpaginated APIs.
3. N+1 and fan-out patterns on request paths.
4. Blocking calls (sync I/O, locks) inside latency-sensitive paths.
5. Cache correctness: invalidation, stampede protection, key cardinality.
6. Resource ceilings: connection pools, memory per request, file handles.

**Blocker** = unbounded work on a hot path that grows with data.
**Major** = an N+1 or blocking call in a latency-sensitive route.
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
