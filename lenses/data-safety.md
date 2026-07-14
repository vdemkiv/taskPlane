# Data & migration safety lens

**Group:** Data
**Charter:** changing stored data without corrupting it
**Does NOT own:** schema DESIGN/perf → dba

## Looks for
additive/rollback-safe migrations, nullable/defaulted columns, backfill, cascades

## Fires when
- files match: **/migrations/**, **/*.sql, **/schema/**
- task types: migration
- runs as **subagent** when: **/migrations/**

## Evaluator prompt

You are reviewing this change through the **Data & migration safety** lens only. Your charter: changing stored data without corrupting it. Stay inside it — anything under “schema DESIGN/perf → dba” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Migrations additive and reversible (expand/contract); destructive steps separated and gated.
2. Existing rows: NULL/default handling for new columns; backfill plan with verification.
3. Cascades and ON DELETE behavior reviewed against real relationships.
4. Lock budget: no long table locks on hot tables (online migration strategy where needed).
5. Rollback actually tested — down-migration or documented recovery.

## Deep reference — migration scripts

Follow `lenses/references/migration-scripts.md` in full: expand/contract
as the only safe shape, lock analysis on hot tables, tested reversibility,
data correctness for existing rows, idempotency. Its severity anchors
override the generic ones below for migration files.

**Blocker** = a destructive migration without backfill + verified rollback.
**Major** = a long lock on a hot table; unhandled NULLs for existing rows.
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
