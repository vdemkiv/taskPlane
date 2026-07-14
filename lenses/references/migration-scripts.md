# Relational migration scripts — the review standard

Applied by **data-safety** (safety of the change) and **dba** (quality of
the schema it produces) whenever a migration file is in the diff. Framework-
agnostic: the same bar for raw SQL, Flyway/Liquibase, EF Migrations,
ActiveRecord, Alembic, Prisma, or knex.

## 1. Expand / contract — the only safe shape

Never break the running application. A change that renames/retypes/moves
data ships as **two or more migrations across deploys**:

1. **Expand**: add the new column/table (nullable or defaulted), dual-write
   from the app, backfill in batches.
2. **Verify**: counts/checksums prove old and new agree.
3. **Contract** (a later deploy): switch reads, stop dual-writes, drop the
   old column.

A single migration doing rename-and-drop is a **blocker** on any live
system.

## 2. Locks — the production killer

- Know your engine's DDL locking: adding a NOT NULL column with a volatile
  default, retyping a column, or adding an index rewrites/locks the table.
- Large tables: create indexes **concurrently** (or engine equivalent);
  batch backfills (bounded UPDATE loops with sleep), never one giant UPDATE.
- State the expected lock scope in the migration's comment; "don't know" on
  a hot table = **blocker**.

## 3. Reversibility

- Every migration has a working `down` (or a documented, tested recovery
  procedure when down is impossible — e.g., after a destructive contract).
- The down was actually run once (up → down → up green) — untested downs
  are wishes, not rollbacks.
- Destructive steps (DROP, DELETE, TRUNCATE) live in their own migration,
  clearly named, never mixed with additive steps.

## 4. Data correctness

- New NOT NULL columns: default or backfill covers **existing** rows.
- FK/unique/check constraints added only after data is proven clean
  (constraint-with-existing-violations aborts mid-migration on some
  engines, half-applies on others).
- Cascades reviewed against real relationships — an ON DELETE CASCADE is a
  mass-delete waiting for its trigger.
- Idempotency where the runner may retry (IF NOT EXISTS guards or
  framework-managed state).

## 5. Script hygiene (the code-quality of migrations)

- One concern per migration; sequential, collision-free versioning
  (framework-managed or timestamped).
- Deterministic: no application code imports, no environment branching, no
  `NOW()`-dependent data seeds that differ per run.
- Transactional where the engine allows (and explicit `-- non-transactional`
  marker where DDL can't be, so the reviewer sees it).
- Never edit a migration that has run anywhere beyond your machine — fix
  forward with a new one.
- Seeds/reference data separated from schema changes.

## Severity anchors

**Blocker** = destructive change without expand/contract + verified
rollback; an unbounded table-locking operation on a hot table; a
constraint added over unverified data.
**Major** = untested down; mixed destructive+additive migration;
non-idempotent script a retry can corrupt.
**Minor** = naming/ordering hygiene, missing comments on lock expectations.
