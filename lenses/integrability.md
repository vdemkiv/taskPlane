# Integrability lens

**Group:** Interfaces
**Charter:** contracts BETWEEN systems: shapes, versioning, errors
**Does NOT own:** internal service logic → backend

## Looks for
API/data contracts, versioning, error codes, error recovery, schema hygiene

## Fires when
- files match: **/api/**, **/*.proto, **/schema/**, **/contracts/**, **/openapi*
- task types: api, integration

## Evaluator prompt

You are reviewing this change through the **Integrability** lens only. Your charter: contracts BETWEEN systems: shapes, versioning, errors. Stay inside it — anything under “internal service logic → backend” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Contract changes versioned; nothing existing consumers parse is silently changed or removed.
2. Errors structured and documented: codes, retryability, machine-readable shape.
3. Timeout and retry semantics stated for consumers (idempotency keys where retries are expected).
4. Schema evolution additive; openapi/proto/schema files updated with the change.
5. Pagination, filtering, naming consistent with the API's existing conventions.

**Blocker** = a breaking change to a published contract without a version.
**Major** = undocumented error shapes; contract files out of sync with code.
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
