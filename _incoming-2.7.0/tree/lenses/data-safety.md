# Data & migration safety lens

**Group:** Data
**Charter:** changing stored data without corrupting it, and shipping that change without an outage
**Does NOT own:** schema DESIGN, normalization, index choice, data types, query plans, partitioning strategy → dba; migration script naming/versioning/one-concern hygiene (`migration-scripts.md` §5) → dba; application read/write logic itself → backend; runner credentials and secrets → security

## Looks for
expand/contract sequenced across deploys, additive/rollback-safe migrations, nullable/defaulted columns, batched restartable backfill, verified rollback, explicit lock_timeout/statement_timeout, engine- and version-specific rewrite paths, constraints validated over proven-clean data, cascades, replica lag, idempotency on retry

## Fires when
- files match: **/migrations/**, **/Migrations/**, **/db/migrate/**, **/db/migration/**, **/migrate/**, **/alembic/**, **/versions/*.py, **/db/changelog/**, **/*migration*.py, **/*migration*.rb, **/*.sql, **/*.ddl, **/schema/**, **/seeds/**
- task types: migration, data
- runs as **subagent** when: **/migrations/**, **/Migrations/**, **/db/migrate/**, **/db/migration/**, **/alembic/**

## Evaluator prompt

You are reviewing this change through the **Data & migration safety** lens only. Your charter: changing stored data without corrupting it, and shipping that change without an outage. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

You are reviewing this change through the **Data & migration safety** lens only. Your charter: changing stored data without corrupting it, and shipping that change without an outage. Stay inside it — schema design, index choice, data types, query plans and script-naming hygiene belong to **dba**; application read/write logic belongs to **backend**. Note either in one line and move on.

**Establish the engine and version first.** Read it from the diff, the migration config, the as-built inventory or the dependency graph. Lock and rewrite behaviour is engine- *and version-*specific: a rule that is true on PostgreSQL 11+ is false on 10, and true on MySQL 9.7 LTS is false on 8.4 LTS. If you cannot establish engine and major version, say so and raise checks 2 and 3 as `question`, not `blocker` — never assert a lock rule for an engine you have not identified.

Examine, with file:line evidence:

1. **Expand/contract is a deploy sequence, not a file property.** A rename, retype or move ships as expand → dual-write → backfill → verify → switch reads → contract, across separate deploys. Check that the *reads* are not switched in the same deploy as the expand, that the old column is not dropped in the same migration that adds the new one, and that the contract step exists as a tracked follow-up rather than an assumption — an expand that never contracts leaves the system worse than it started (Fowler/Sato, ParallelChange). The companion dual-write in application code is **backend**'s to judge for correctness; yours is to confirm it exists. If the diff contains migration files only, raise the missing companion as a `question` and do not pass the sequencing silently.
2. **Name the rewrite/lock path for the stated engine and version.** Do not accept "this is a fast ALTER" without one.
   - *PostgreSQL 11+*: `ADD COLUMN` with a **constant, non-volatile** `DEFAULT` is metadata-only (PG 11 release notes); on PG 10 and earlier it rewrites. Still rewriting on PG 18: a **volatile** default (`clock_timestamp()`), a **stored** generated column, an identity column, or a domain-typed column with constraints. A type change rewrites unless the old type is binary-coercible to the new one.
   - *PostgreSQL*: `ADD CONSTRAINT ... NOT VALID` then a separate `VALIDATE CONSTRAINT` takes only `SHARE UPDATE EXCLUSIVE`, versus `ACCESS EXCLUSIVE` for a validating `ADD CONSTRAINT`; `ADD FOREIGN KEY` needs only `SHARE ROW EXCLUSIVE`. On **PG 12+**, `SET NOT NULL` skips the full scan when a valid `CHECK` already proves no NULL exists.
   - *MySQL / InnoDB — establish which line: 8.4 LTS, 9.7 LTS, or 26.7 Innovation (the first calendar-versioned release, GA July 2026)*: pin `ALGORITHM=` explicitly so a silent fallback to `COPY` fails loudly instead of rebuilding the table. Still `COPY`-only on every current line: changing a column data type, shrinking a `VARCHAR`, dropping a primary key alone, adding a `STORED` generated column. `INSTANT` add/drop column is capped at **64 row versions per table on 8.4 LTS**, raised to **255 as of 9.1.0** and therefore 255 on 9.7 LTS and 26.7, before a rebuild is forced — if this table has taken repeated instant DDL, that cap is the hazard.
   - Other engines (SQLite, SQL Server, Oracle, Vitess, a managed migration service, a document or warehouse store): if you do not know its DDL semantics, say so and abstain on this check rather than transferring a PostgreSQL rule to it.
3. **Lock *acquisition* budget, not just lock duration.** On PostgreSQL every supported version, `lock_timeout` and `statement_timeout` both default to `0` — wait forever. A metadata-only DDL that queues behind one long-running transaction holds its place in the lock queue and blocks every subsequent query on that table behind it, so an "instant" migration still takes the site down. Require a short `lock_timeout` with a longer `statement_timeout`, set in the migration or demonstrably inherited from the runner's connection config, plus a retry posture on timeout. Accept runner-level config as satisfying this — do not flag a repo that sets it centrally. For index builds on a hot table: `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block, so the migration must be marked non-transactional; a failed build leaves an **invalid** index behind that still costs update overhead, so require a stated cleanup (drop and retry, or `REINDEX INDEX CONCURRENTLY`, PG 12+). Concurrent builds on **partitioned** tables are still unsupported on PG 18 — if the target is partitioned, the plan must be per-partition builds then a non-concurrent attach. Whether the index is the *right* index is **dba**'s call.
4. **Backfill: batched, restartable, throttled, verified.** A single unbounded `UPDATE` over a large table is a finding on its own. Require: bounded batches by primary/key range, each batch in its own transaction, run outside the schema-migration transaction, throttled between batches, and **restartable from a recorded cursor** — a backfill killed at 80% that restarts from zero is a second outage. Require a stated verification that old and new agree (counts, checksums, or a sampled comparison) before anything reads the new column, and a stated expectation for **replica lag**: backfill WAL/binlog volume is a read-replica availability problem, not only a primary-side cost.
5. **Existing rows and constraints.** New `NOT NULL` columns must cover rows that already exist — a default, or a backfill completed before the constraint is applied. Constraints (FK, unique, check) are added only over data proven clean, and validated as a separate step; an unvalidated assumption here half-applies or aborts mid-migration depending on engine. Review `ON DELETE`/`ON UPDATE` cascades against the real relationships in the diff — a cascade added to a table with high-fanout children is a mass delete waiting for its trigger. That constraints *should* exist at all is **dba**'s call; that this one is safe to apply to today's data is yours.
6. **Rollback that was actually exercised.** A `down` that has never been run is a wish. Require evidence of up → down → up green, or, where reversal is genuinely impossible (any completed contract step), a documented and tested recovery procedure naming the restore source and the acceptable data loss window — "restore from backup" with no named backup, RPO or rehearsal does not count. Destructive steps (`DROP`, `DELETE`, `TRUNCATE`) live in their own separately named migration, never mixed with additive steps. Where the runner may retry a partially applied migration, the script must be idempotent (guards or framework-managed state) so the retry cannot corrupt.

## Deep reference — migration scripts

Follow `lenses/references/migration-scripts.md` in full: expand/contract
as the only safe shape, lock analysis on hot tables, tested reversibility,
data correctness for existing rows, idempotency. Its severity anchors
override the generic ones below for migration files.

**Blocker** = a destructive or irreversible migration without backfill and verified rollback; a rewrite-triggering or table-locking DDL on a hot table with no `lock_timeout` and no stated lock scope; a constraint added over unverified data; reads switched to a new column in the same deploy that creates it.
**Major** = a long lock on a hot table; unhandled NULLs for existing rows; a rewrite-path `ALTER` on a large table (volatile default, stored generated column, non-coercible type change, MySQL `ALGORITHM=COPY`); an unbatched or non-restartable backfill; a `CREATE INDEX CONCURRENTLY` with no non-transactional marker or no invalid-index cleanup; an untested `down`; destructive and additive steps mixed in one migration.
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
