# DBA lens

**Group:** Data
**Charter:** schema design, indexing, query efficiency, data modeling
**Does NOT own:** migration SAFETY → data-safety

## Looks for
normalization, indexes, query plans, constraints/keys, data types, partitioning

## Fires when
- files match: **/*.sql, **/models/**, **/entities/**, **/*.prisma, **/schema/**, **/repositories/**
- task types: migration, backend
- runs as **subagent** when: **/schema/**, **/*.prisma

## Evaluator prompt

You are reviewing this change through the **DBA** lens only. Your charter: schema design, indexing, query efficiency, data modeling. Stay inside it — anything under “migration SAFETY → data-safety” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Engine fit: is the chosen database RIGHT for this workload? Apply references/database-selection.md (relational by default; a second engine must earn its place) — at plan time, not after the build.
2. Schema design: normalization level deliberate; entities and relationships model the domain.
3. Indexes match the actual query predicates introduced; no dead or duplicate indexes.
4. Constraints (FK, unique, check) guard invariants at the database, not only in app code.
5. Data types right-sized; no stringly-typed dates/enums/money.
6. Query plans for new heavy queries; partitioning/archival for tables that will grow.

## Deep references

- **Engine choice** (requirement/plan time): follow
  `lenses/references/database-selection.md` — four workload questions,
  relational-by-default, scenario table, polyglot red flags. Record the
  choice to the KB.
- **Migration scripts**: the schema QUALITY side of
  `lenses/references/migration-scripts.md` §5 (hygiene) and §4 (data
  correctness) — safety belongs to data-safety; don't double-grade.

**Blocker** = an invariant enforceable by the DB left to app code on critical data.
**Major** = a new query with no supporting index; wrong type for money/time.
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
