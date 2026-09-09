# Legacy retirement and retained history

Retirement follows the existing run's recorded migration boundary. An
unmigrated singleton still uses its working legacy loop and track writer.
Installing this change does not migrate that run or change its dispatch mode.

After `migrate_singleton` commits through RunStore, legacy track creation,
switching and closing remain read-only. The guard consumes the verified
migration projection before touching the singleton. Disabling stage-native
mutation for rollback does not reactivate those retired track writers.

The migration receipt records the original immutable head and active-stage
projection. Subsequent authorized lifecycle commits may close that stage or
select a successor. The reader verifies the retained snapshot against its
original stage object and verifies the current projection against the current
RunStore index. It does not require historical and current states to be equal.
Unreadable or changed retained evidence fails closed; it cannot select the
legacy writer as a fallback.

Source bundles, stage objects, operation receipts and original review evidence
remain readable without rewriting their bytes. Readability and mechanical
integrity confer no current progression, recovery, reuse or publication
authority. Those decisions remain with their existing scoped authority owners.

Phase-routing rollback is a different existing operation: it changes admission
for new attempts through the same RunStore journal, rechecks authority, and
preserves prepared attempts and prior receipts. It introduces no second phase
registry, lifecycle, progression owner, store or cleanup mechanism. R-0003
maintenance isolation is unchanged.

T20's four selectors in `taskplane/tests/test_r0001_legacy_retirement.py` exercise
writer-before-reader retirement, a persistent fence with both enabled and
disabled stage mutation, severed actual retained source output, readable active
and terminal history, and sole-owner routing with revoked-authority refusal.
These are local production-boundary regressions with simulated authority inputs,
not native journey or final acceptance evidence. R-0001/R-0003 dependencies and
the separately open P14 native canary and P23 whole-suite adjudication remain.
Historical R-0004 records stay historical; their incorporated obligations remain
in R-0001. Publication still requires separate current post-merge authority.
