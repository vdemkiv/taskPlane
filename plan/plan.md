# R-0001 final t15 scope-correction plan

This human-approved replan preserves the complete archived t1–t18 delivery
history, commits, submissions, evaluations, and evidence. It does not restart
Product or Design, create a feature, broaden acceptance, or repeat green work.
The only executable work is the remaining t15 reconciliation, now authorized
to edit the two stale artifacts omitted from its prior scope:
`plan/tasks.json` and `design/visual.html`.

Identity boundary: this is the 32-AC Progressive convergent engineering review
R-0001. It does not cover or claim the separate 15-AC governed-run reliability
and exclusive authority R-0001 from another task/store.

## Preserved governed baseline

- t1–t14 retain their exact archived definitions and `status: "passed"`.
  They provide Plan DoR coverage and load as SETTLED, so execution skips them.
- t15 retains the already-green implementation/fixer commit `2602b5b`, its
  focused evidence, criteria, contracts, graph edges, impact policy, and prior
  scope. It remains `status: "pending"` only for the bounded stale-artifact
  reconciliation described below.
- t16 commit `2061139`, t17 commit `fcee90c`, and t18 commit `cdceb475` retain
  their accepted evidence and are now `status: "passed"`. They are
  coverage-only SETTLED records and will not be rerun.
- The current governed primary, including the accepted t14 exact-worktree and
  harness fixes, is t15's baseline. t15 has `deps: []`; no settled task is
  rescheduled as an executable prerequisite.

The 17 settled records plus pending t15 collectively retain ownership of all
32 acceptance criteria, all approved Design modules, all 15 requirement
contract ids, every canonical Design edge, the approved acceptance mapping,
and the typed depth policy. The approved Design checkpoint remains the source
of HOW; this replan changes only repair reachability.

## Bounded impact

The planner made exactly one required graph-impact call over t15's corrected
scope, including `plan/tasks.json` and `design/visual.html`. At graph
fingerprint
`58c3742bf33ae859ec63aef3727b779c232ea901192673ff70368af5a5795ea2`,
the result touched `design`, `plan`, `specs`, `taskplane`, and
`taskplane/tests`; reached 24 impacted nodes; affected only `req:R-0001`; and
reported no unknown modules, dependent requirements, policy blocks, contract
boundary nodes, truncation, or depth truncation. The existing policy remains
local depth 3, `contract-only` boundaries, contract depth 1, and requirement
depth 1. No new module, contract, resource, or distributed boundary is added.

## Only pending work: t15

t15 keeps its existing risk-routing implementation and focused selector. Its
scope adds exactly:

- `plan/tasks.json`, so the persisted AC22 ownership text can be reconciled
  with the already authorized risk-scaled AC15/AC22 rule; and
- `design/visual.html`, so the visual no longer states an unconditional
  four-deep-floor rule.

The repair must make the requirement, Design Contract, Design narrative,
visual, active plan criterion, runtime routing, and focused fixtures agree:
documentation-only and simple low-risk changes select exactly one attributable
risk-selected deep lens; a missing code-module mapping alone does not widen;
only genuinely ambiguous, corrupt, substantively risky, or otherwise
evidence-backed material risk widens to the architecture, code-quality,
security, and QA floors with an explicit reason. The selected slot or slots
and the eight-lens cap remain conserved.

The task retains one focused command string naming only the existing t15
selectors. There is no local repository-wide suite in this plan. CI retains
ownership of the single complete `python3 -m pytest -q taskplane/tests`
confirmation after focused closure and independent evaluation.

## Risks and rollback

The narrow risk is semantic drift between the corrected runtime behavior and
the two stale artifacts. `plan/tasks.json` is in scope only to update t15's
AC22 ownership text after the requirement wording is reconciled; it is not
authority to change task history, statuses, other criteria, or any other task
definition. `design/visual.html` may express the approved risk-scaled rule but
may not introduce a new Design approach.

No assertion may be removed, skipped, xfailed, loosened, or reclassified. No
private keys, host authentication boundary, i18n, retention/compaction,
scalability machinery, or wishlist documentation enters the repair. If the
focused t15 selector does not converge, rollback only the new stale-artifact
repair while preserving commit `2602b5b` and all settled task evidence, then
return the exact blocker to the governed loop.
