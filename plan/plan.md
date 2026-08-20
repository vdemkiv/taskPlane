# R-0001 post-EM two-finding repair plan

This human-approved replan preserves every merged t1–t18 commit, worktree,
submission, evaluation, and evidence record. It also preserves the sealed EM
provisional request-changes revision and KB decision `0025`; neither is
rewritten or reinterpreted. The only executable work is two focused,
scope-disjoint repairs for the admitted F-01 and F-02 findings.

Identity boundary: this is the 32-AC Progressive convergent engineering review
R-0001. It does not cover or claim the separate 15-AC governed-run reliability
and exclusive authority R-0001 from another task/store.

## Preserved baseline and coverage

- t1–t18 retain their complete task definitions and are explicitly
  `status: "passed"`. They load as SETTLED coverage records and execution skips
  them; this replan does not rerun, replace, or weaken their green work.
- t19 and t20 are the only `status: "pending"` records. Both have `deps: []`
  because they start from the current governed primary containing all merged
  t1–t18 fixes and evidence.
- The settled records plus the two focused repairs continue to own all 32
  acceptance criteria, every approved Design module and canonical edge, all
  15 requirement contract ids, the acceptance map, and the approved typed
  impact policy. No Product or Design restart and no contract drift is needed.
  Settled records retain earlier verbatim AC15/AC22 text variants as immutable
  task evidence; those variants map to the same criterion identities and do
  not create an AC33 or broaden R-0001.

The sealed EM revision remains a truthful provisional request-changes state:
approval is closed, uncollected review slots remain explicit gaps, and F-01/F-02
are repair inputs rather than permission to alter other evidence or findings.

## Bounded impact

The planner made exactly one graph-impact call over the two new repair
surfaces. At graph fingerprint
`8103e5be4842eb3cb03fb8aaac6fd2fafb2203699b835662b15183400b2db616`,
it touched only `taskplane` and `taskplane/tests`; reached 27 impacted nodes;
affected only `req:R-0001`; and reported no unknown modules, dependent
requirements, policy blocks, contract boundary nodes, truncation, or depth
truncation. The existing policy remains local depth 3, `contract-only`
boundaries, contract depth 1, and requirement depth 1. Neither task introduces
a module, contract, resource, or distributed boundary.

## Independent repair wave

The tasks are scope-disjoint and may execute in parallel. F-01 is listed first
because it is the admitted blocker.

### t19 — evidence-bearing metadata normalization

Owns only the collector, its metadata-repair/retry helpers, and focused
lifecycle/recovery tests. After canonical metadata normalization,
`taskplane/review.py` must revalidate the pass-evidence invariant. A
fail-to-pass normalization with no checked evidence cannot be stored as a
clean pass: mechanical repair is rejected and only that original producer is
scheduled for retry.

The repair must preserve both accepted sides of the contract:

- authority-derivable metadata contradictions still normalize with audited
  before/after values and complete collection in one call without a producer
  rerun; and
- substantive or evidence-bearing defects still retry only the affected
  original producer while conserving unrelated valid results.

Its single focused pytest command covers the existing derivable-normalization
and affected-producer tests plus the new inverse evidence-free-pass regression.

### t20 — evaluator-unavailable retry routing

Owns only `taskplane/loop.py` and the two focused loop/evidence-bundle test
files. A retry from a truthful evaluator-infrastructure-unavailable escalation
must restore `evaluate` with an unsettled task, never enter `fix`; `fix` remains
reserved for a judged product failure. Unavailability stays distinct and
non-judged, approval/readiness stays closed, and the exact outage identity is
preserved across the transition.

The task adds the unavailable → retry → evaluate transition fixture, retains
the existing non-judged/readiness and outage-identity fixtures, and keeps the
judged-failure retry-to-fix assertion. It also updates the known stale legacy
node
`TestUnavailableModelEvaluationDoesNotOpenAProductFix::test_unavailable_advances_with_warning_without_a_fix_cycle`
in place so the focused selector and CI no longer encode an evidence-free
passed state.

## Validation and rollback

Each pending task has exactly one focused `python3 -m pytest -q` command string
containing only named cluster selectors. No task declares a local repository-
wide suite; CI retains ownership of the complete
`python3 -m pytest -q taskplane/tests` validation after both focused repairs
and their independent evaluations close.

No assertion may be removed, skipped, xfailed, loosened, or reclassified.
t19 may change only evidence validation/repair routing and its focused tests;
t20 may change only unavailable retry routing and its focused assertions. If a
task fails to converge, rollback that task alone while preserving all t1–t18
history, the other repair, the sealed provisional EM revision, and KB decision
`0025`, then return the exact non-convergent finding to the governed loop.
