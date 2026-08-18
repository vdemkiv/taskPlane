# R-0009 correction plan — production composition and safety closure

This replaces the broad implementation plan with the smallest correction wave
supported by accepted engineering decision 0042 and the sealed evidence in
`.em-review/report.md` and `.em-review/findings.json`. The prior full R-0009
plan is preserved unchanged in `plan/r0009-full-plan.pre-em-correction.md` and
`plan/r0009-full-tasks.pre-em-correction.json`.

The EM verdict is authoritative: the 69 focused tests passed, but production
did not compose the canonical DoR, session, recovery, artifact, or paged-inline
modules; dynamic validation allowed push-protection bypasses; artifact roots
could escape; and four otherwise valid producer results were stranded by an
add-only summary-repair contract. This plan fixes only those defects and their
regressions. It does not add retention, cost attribution, broad scalability
refactors, documentation rewrites, or unrelated cleanup.

## Bounded impact and retained Design authority

The required single impact call covered the correction surfaces with local
depth 3, `contract-only` boundaries, contract depth 1, and requirement depth
1. It returned 31 impacted nodes and no unknown modules at reviewed HEAD
`4336ec2e44a896f75994c02edb7598b566c788cb`. The graph fingerprint changed
with the implementation under review, but decision 0042 explicitly retains
the approved R-0009 Design fingerprint
`74c41bf3d111c14a496bfb642aa1d6f9e3ba47c223b924a4bc0a1f0c1d4b05e7`
and records no accepted drift.

For mechanical Design conformance, `new_modules` remains explicit graph
coverage metadata: the four tasks collectively retain all approved module and
contract nodes and all 19 exact Design edges, even though executable scope is
restricted to correction files. All eight contracts, AC1–AC16 verbatim, and
the approved depth policy remain owned.

## Risk-first correction wave

The first three tasks have disjoint scopes and can run in parallel isolated
worktrees. The production-composition task runs only after their contracts are
sealed.

1. **Close sandbox push bypasses.** Bind the command `cwd`, transport, and
   observed repository identity to one verified disposable root. Enforce
   push-disabled behavior below command spelling: reject explicit destinations,
   `--no-verify`, aliases/wrappers, environment/config overrides, and any
   transport capable of remote writes. Missing pre/post remote observations
   are `unavailable`, never “unchanged.” Prove zero push attempts and unchanged
   pinned checkout/ref and remote while preserving sandbox delta evidence.
2. **Confine artifact publication.** Reject empty roots before normalization;
   require a designated governed root; validate every ancestor and final
   target against traversal and symlink escape; use safe atomic creation; and
   keep all preflight/semantic/write failures inside the stable retryable
   `unavailable` boundary. Tests cover empty, relative, traversal, symlinked
   ancestor/final component, race replacement, and valid atomic publication.
3. **Repair or safely rerun one slot.** Let the collector accept an audited
   lease-derived producer-summary correction without mutating findings,
   evidence, target, producer, slot, or canonical provenance. If equivalence
   cannot be proven or the add-only file cannot be safely superseded, emit a
   durable affected-only retry manifest and rerun exactly that producer. The
   captured four summary-consistency gaps must become collectable or bounded
   retries while valid slot digests remain unchanged and findings never
   duplicate.
4. **Wire the canonical production transaction.** Make the real review/loop
   path invoke DoR discovery and criterion ledger before routing, establish one
   ReviewSession authority, progressively freeze partial revisions, invoke
   repair/retry during collection, atomically publish JSON/Markdown/HTML, and
   render the ordered bounded inline model. Gate only the canonical revision.
   Exercise real DOM behavior so root roving navigation ignores native
   interactive controls; assert pages/facets stay within 14,000 bytes. A single
   production-path regression proves host semantic parity, criterion gating,
   partial honesty, automatic artifacts, actionable failures, accessibility,
   scale bounds, and legacy small-review compatibility without fabricated
   models.

## Runnable validation

| Task | Command |
|---|---|
| Sandbox transport closure | `python3 -m pytest -q taskplane/tests/test_review_sandbox_security.py` |
| Artifact path confinement | `python3 -m pytest -q taskplane/tests/test_review_artifact_confinement.py` |
| Summary repair / affected retry | `python3 -m pytest -q taskplane/tests/test_review_summary_recovery.py` |
| Production composition | `python3 -m pytest -q taskplane/tests/test_review_production_integration.py` |

The final test must call the actual production entry points rather than build
fabricated canonical models. It recreates the EM provisional state, observes
valid slot retention and recovery, publishes the artifact transaction, and
drives keyboard/filter/pagination/action-receipt behavior. Existing tests may
not be removed, skipped, xfailed, weakened, or have floors lowered.

## Failure and rollback controls

- Any sandbox identity or remote observation gap is a named non-success; it
  cannot attest immutability.
- Any root/path/symlink ambiguity fails before publication and returns one
  stable retryable artifact status without advertising a partial set.
- Any substantive or unverifiable producer change bypasses mechanical repair
  and schedules only the affected slot, bounded by the approved retry limit.
- Any unwired stage, renderer failure, unrepaired gap, or artifact failure
  preserves findings and consent while keeping completion/pass false.
- Rollback disables the canonical production flag for new sessions only;
  issued leases and immutable provisional/canonical revisions continue through
  retained readers. No artifact, finding, or audit record is rewritten or
  deleted.
