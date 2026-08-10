# Design — graph-scoped regression gate + review discipline

Root cause of the v2.3.0 irony: the fix wave shipped with a **green full suite
(744 passing)** yet introduced ~7 regressions, and the next review reported
~100 "issues" of which ~90 were pre-existing debt or taste. Two defects in
taskplane's own process:

1. **The DoD gate has no regression check.** `dod_check` runs the test command
   and blocks on non-zero exit. It never asks *"is anything that was
   verifiably working before this change broken now?"* A green suite is not a
   regression gate when the suite doesn't cover the changed behavior.
2. **Reviews conflate regression, pre-existing debt, and taste.** Every lens
   opinion becomes a "finding" of equal standing, so a whole-tree 26-lens
   sweep always yields ~100 and buries the ~7 that matter.

This design fixes both. Principle from the user: *verify each change for
regressions right away, not postponed; scope it to a reasonable radius using
the dependency graph; flag actual regressions only.*

## Part A — regression gate (at evaluate / DoD, per change)

Runs at the DoD/evaluate step of every change, before it can be called done.

**Radius (graph-scoped).** `changed_files` → `depgraph.impact` gives the
source modules in the change's blast radius (the changed modules plus their
reverse-dependents, bounded by the impact policy depth). Map that module set
to the **test files that import any module in it** (a test-import index built
by scanning `from taskplane.X import` / `import taskplane.X`). That test
subset is the radius. If the graph is empty/sparse or a changed module has no
importing test, fall back to the full suite **and say so** (`degraded: true`)
— never silently narrow.

**Two tiers, because coverage is uneven.**

- **Tier 1 — covered regressions (objective, blocks).** Run the radius tests
  at the change's base ref (baseline) and on the current tree. A test that was
  **green at baseline and fails now** is a regression → block, naming the exact
  node id and both states. A test failing in *both* is pre-existing → reported,
  never blocks this change. A newly-added test is the change's own DoD, handled
  as today.

- **Tier 2 — coverage-gap guard (blocks "verified", not the work).** Tier 1
  only catches regressions in behaviors that *have* tests. The v2.3.0
  regressions that hurt most (the broken CI invocation, `tp.py` self-blocked
  under a read-only contract, the contract-slot fix unwired into dispatch) had
  **no covering test**, so no test-diff could have caught them. So: if the
  change touches an **enforcement path or a documented public entry point**
  (screen_*/dod/dor/hooks/CLI subcommands/CI invocation) and the radius
  contains **no test that exercises that entry point**, the gate returns
  `unverifiable: [<entry point>]` and refuses a clean DoD until a covering
  behavioral test exists. This turns "green suite = safe" into "green suite +
  every changed public behavior is actually exercised = safe."

Baseline source: the change's contract snapshot ref (already recorded), else
`git merge-base` with the tracking branch. Run the baseline in a throwaway
`git worktree` at that ref so no state is mutated.

## Part B — review discipline (finding classification)

Every finding carries a **`class`**: `regression | pre-existing | observation`.

- `regression` — a behavior verifiably worse than a named baseline. Blocks.
- `pre-existing` — real defect/debt that predates this change. Surfaced +
  tracked as debt; does **not** block *this* change's gate.
- `observation` — taste, style, or a design opinion about code just read.
  Informational; never a blocker, never inflates the count.

Blocker set at the gate = `class == regression` **or** (`severity == high`
**and** the finding is anchored in the change's diff). Unknown `class` →
`observation` (fail toward not-blocking taste), while unknown `severity` stays
→ `high` (fail closed on danger). The headline splits the count:
`R regressions · H new-high-in-diff · P pre-existing · O observations` — so
"~100 findings" reads honestly as "7 that block, 93 to triage."

Lens agents are instructed to classify and to anchor regressions to a
baseline; a review's gate counts only the blocker set.

## Scope of this build

- `taskplane/regression.py`: radius selection (graph + test-import index),
  baseline-diff runner, coverage-gap guard. Pure/injectable where possible.
- Wire Tier 1 + Tier 2 into `dod_check` behind the existing DoD (opt-in via a
  `regression_gate` DoD flag, default on when a base ref is resolvable).
- Findings schema `class` field; gate/headline blocker-set split in
  loop + dashboard; lens brief + tp-engineering skill updated.
- Tests for every path, run **in-radius immediately** (dogfooding the rule).
- Demonstrated against v2.3.0's real changed files.
