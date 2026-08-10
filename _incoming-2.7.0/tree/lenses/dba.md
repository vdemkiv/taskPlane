# DBA lens

**Group:** Data
**Charter:** schema design, indexing, query efficiency, data modeling
**Does NOT own:** migration EXECUTION safety — locks, lock/statement timeouts, rollback, backfill, expand/contract sequencing → data-safety; N+1 and runtime behaviour under load → scalability; business logic inside repositories/services → backend; personal-data classification and retention → privacy-compliance

## Looks for
deliberate vs accidental denormalization, key & clustering design, type choice and precision, nullability and defaults, DB-enforced constraints, index column order & leftmost-prefix usability, sargable predicates, dead/redundant indexes and missing ones for queries the same diff introduces, plan evidence at realistic volume, partitioning

## Fires when
- files match: **/*.sql, **/*.ddl, **/models/**, **/entities/**, **/*.prisma, **/schema/**, **/schema.rb, **/repositories/**, **/queries/**, **/db/**, **/migrations/**, **/db/migrate/**, **/db/migration/**, **/Migrations/**, **/alembic/**, **/drizzle/**, **/*.dbml
- task types: migration, backend, data, solution-design
- runs as **subagent** when: **/schema/**, **/*.prisma

## Evaluator prompt

You are reviewing this change through the **DBA** lens only. Your charter: schema design, indexing, query efficiency, data modeling. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

In particular: on a migration file you judge **the schema it produces**, never how it is applied — locks, timeouts, rewrite paths, backfill and rollback are data-safety's, and double-grading them wastes the gate.

**First, establish the engine and version** from the diff, the as-built inventory or the migration framework (PostgreSQL 18; MySQL InnoDB — 26.7 Innovation, 9.7 LTS, 8.4 LTS; SQL Server, SQLite, …). Checks 3 and 7 name engine-specific behaviour; if you cannot establish the engine, say so and skip those rather than guessing.

Examine, with file:line evidence:

1. **Engine fit** (requirement/plan time only, not after the build): is the store right for this workload? Apply `references/database-selection.md` — the four workload questions, relational-by-default, the scenario table, the polyglot red flags. A second engine must earn its place against a workload the incumbent demonstrably cannot serve. Abstain on diffs that don't introduce or change a data store.
2. **Model, keys and clustering.** Do entities and relationships model the domain, and is the normalization level a decision rather than an accident? Denormalization is legitimate when it is **declared** — the duplicated column names its source of truth and the mechanism keeping them in sync (generated column, trigger, application write path, scheduled reconcile); a copied column with no stated owner is drift waiting to happen, and repeated column groups (`addr_line1_2`, `phone1/phone2/phone3`) or an entity-attribute-value table are usually the accidental kind. Primary key design is a **create-time** decision: on InnoDB the primary key *is* the clustered index, every secondary index record carries the PK columns, and restructuring the clustered index copies the whole table — so a wide or late-added PK inflates every index permanently *[MySQL 26.7 Reference Manual, Clustered and Secondary Indexes]*. Random `uuidv4` primary keys scatter inserts across the B-tree; time-ordered UUIDv7 keeps them local, a difference RFC 9562 puts at "one order of magnitude or more" *[RFC 9562, Standards Track, May 2024, obsoletes RFC 4122; PostgreSQL 18 ships `uuidv7()`]*.
3. **Types and precision.** Money in `float`/`double` is a defect in any engine — use exact decimal (`numeric(p,s)`) with the scale stated, and store the currency alongside it. Time needs a stated instant-vs-wall-clock intent and an explicit fractional-second precision, because engine defaults truncate silently (MySQL `DATETIME`/`TIMESTAMP` default to 0 fractional digits and drop microseconds unless declared `(6)`). Enums, dates and IDs held as free text push validation into every caller. PostgreSQL-specific defect list: `timestamp` without time zone where `timestamptz` is meant; `char(n)` (silently space-pads, breaking comparison); the `money` type (no fractional cents, single-currency, `lc_monetary`-dependent); `serial` where an identity column belongs; `json` where `jsonb` is meant; arbitrary `varchar(n)` caps. Generated columns: in PostgreSQL 18 `GENERATED ALWAYS AS` is **VIRTUAL by default** — a column intended to be indexed or read hot must say `STORED` explicitly *[PostgreSQL 18 docs, Generated Columns; PostgreSQL Wiki "Don't Do This" — community wiki, weaker governance than the manual: use as a defect checklist, and treat `varchar(n)` as a preference, not a rule]*.
4. **Nullability and defaults.** For each new nullable column, is NULL a *meaningful* state of the domain, or an unmodelled one — a column nullable only because some rows belong to a different entity is a missing table. Conversely, a column the application always reads as present should be `NOT NULL` at the store. Defaults declared only in the ORM/model class do not apply to rows written by SQL, another service or a backfill; if the default is part of the contract it belongs in the DDL. Check the three-valued-logic consequences the diff creates: NULLs in a column carrying a `UNIQUE` constraint are not deduplicated by it, and `NOT IN` over a nullable subquery returns zero rows because `NOT (NULL)` is `NULL` *[PostgreSQL Wiki, "Don't Do This"]*. Whether existing rows get a value is data-safety's; whether the column should have allowed NULL at all is yours.
5. **Constraints guard invariants at the store, not only in app code.** For every invariant this change introduces, ask what stops a second writer — a script, a job, another service — from violating it: foreign keys for referential integrity, `UNIQUE` (partial/filtered where soft-delete or tenancy scopes it) for identity, `CHECK` for value domains, exclusion constraints for non-overlap. Referential *design* is yours: is the relationship optional or mandatory, and is `ON DELETE CASCADE` / `RESTRICT` / `SET NULL` the deliberate answer for this relationship rather than the framework default. (Whether the constraint can be added over the data already there, and what it locks, is data-safety's.)
6. **Indexes — the missing ones and the useless ones together.** For each new or changed query predicate, join and sort introduced *by this same diff*, name the index that serves it. A composite index serves only predicates that include its **leftmost prefix**: `(a, b)` does nothing for a query filtering on `b` alone. Order columns by how the application actually queries — equality predicates first, then the range/sort column — not by selectivity *[Winand, Use The Index, Luke! — concatenated keys]*. Then hunt indexes that can never be used: one whose column list is a strict prefix of another (redundant, not merely duplicate); one whose predicate is wrapped in a function or cast (`lower(email) =`, `date(created_at) =`, `id::text =`) or a leading-wildcard `LIKE '%x'`, unless a matching expression index lands in the same change. Every index taxes every `INSERT`/`UPDATE`/`DELETE`, so an index nothing can use is pure cost. Scope this to the queries and tables the diff touches; do not audit the pre-existing index set.
7. **Plan evidence, and growth.** For a new heavy query or a new read path on a table expected to grow, plan evidence must be real: `EXPLAIN (ANALYZE, BUFFERS)` captured **after** `ANALYZE`, at production-comparable volume, with **estimated vs actual row counts compared** — a plan without prior statistics is, in the manual's words, "a lost cause", and "it is especially fatal to use very small test data sets", where 1 row out of 100 fits in one page and no index can win. `enable_seqscan=off` is a diagnostic for understanding the planner, never the fix *[PostgreSQL 18 docs, Examining Index Usage]*. If no plan was captured, say what to capture rather than asserting the query is slow. Partitioning and archival for tables that will grow: the partition key must be part of every primary key and unique constraint on the table, and as of PostgreSQL 18 `CREATE INDEX CONCURRENTLY` is still **not supported on partitioned tables** (build per-partition concurrently, then attach the parent index) — so choosing to partition changes what index maintenance is possible for the life of the table *[PostgreSQL 18 docs, CREATE INDEX]*.

## Deep references

- **Engine choice** (requirement/plan time): follow
  `lenses/references/database-selection.md` — four workload questions,
  relational-by-default, scenario table, polyglot red flags. Record the
  choice to the KB.
- **Migration scripts**: the schema QUALITY side of
  `lenses/references/migration-scripts.md` §5 (hygiene) and §4 (data
  correctness) — safety belongs to data-safety; don't double-grade.

**Blocker** = an invariant enforceable by the DB left to app code on critical data (money, identity, ownership, referential integrity); money stored in a binary float.
**Major** = a new query with no supporting index, **or an index whose column order cannot serve it**; a non-sargable predicate against a column indexed for it; a timestamp with no time-zone intent or silently truncated precision; a nullable column that is actually a missing entity; denormalized data with no named source of truth or sync mechanism.
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
