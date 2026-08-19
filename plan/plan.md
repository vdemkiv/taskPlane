# R-0001 post-EM focused repair plan

This authorized replan starts at the completed 14-task Engineering Manager
request-changes checkpoint. It preserves the archived t1–t14 task history,
worktrees, commits, submissions, and evidence, including t14's accepted
exact-worktree and fixture corrections through commit `ac1f960`. Product and
Design are not restarted. The only new work is four scope-disjoint tasks for
the four reproduced EM blockers. The approved Design checkpoint fingerprint is
`87d23fd659956c3b821790a2ecfe6fe0007ce13c5eb6e161a986102612ffe44e`;
t15's in-place wording reconciliation is directly authorized by this EM
replan and may not introduce a new HOW.

Identity boundary: this is the 32-AC Progressive convergent engineering review
R-0001. It does not cover or claim the separate 15-AC governed-run reliability
and exclusive authority R-0001 from another task/store.

The plan packet contains 18 coverage records: t1–t14 are explicitly
`status: "passed"` and load as SETTLED, while only t15–t18 are
`status: "pending"` and executable. The four repair tasks run focused declared
tests only. There is no local full suite in this plan: after focused closure
and independent evaluation, CI owns the single final
`python3 -m pytest -q taskplane/tests` confirmation.

## Preserved checkpoint

- t1–t13 remain completed exactly as archived; this replan does not reopen,
  broaden, replace, or reschedule any of their accepted work. Their full
  definitions are copied from the durable Taskplane snapshot and marked
  passed solely to preserve requirement, contract, module, edge, policy, and
  criterion coverage for Plan DoR. Execution skips them as SETTLED.
- t14 remains completed with its exact-worktree/fixture fixes and focused
  evidence. Its full snapshot definition is likewise marked passed for
  coverage only. Its valid commit and evidence are part of the governed
  primary baseline for every repair task, not work or a dependency to repeat.
- The EM request-changes report remains the authority for the four bounded
  defects. No unrelated cleanup, wishlist documentation, i18n,
  retention/compaction, scalability machinery, private-key scheme, or
  same-UID authenticated-host boundary enters this plan.

## Bounded graph and contract impact

This correction reuses the single bounded graph-impact result obtained for the
same four unchanged repair surfaces in the rejected packet; making a second
identical call would add no planning evidence. At graph fingerprint
`9dd9e555fb5e583431dcc1d51d203edac707682a2b4609d2d298bfa954b05e14`,
it touched `design`, `specs`, `taskplane`, and `taskplane/tests`; reached 25
impacted nodes; affected only `req:R-0001`; and reported no unknown modules,
dependent requirements, policy blocks, contract-boundary violations,
truncation, or depth truncation. The existing typed policy remains local depth
3, `contract-only` boundaries, contract depth 1, and requirement depth 1.
No repair task introduces a module, contract, resource, or distributed
boundary. Across the 14 settled coverage records and four pending repairs, all
32 acceptance criteria and every approved Design module, named contract,
canonical edge, and impact-policy surface remain owned.

## Repair wave

All four tasks start from the current governed primary baseline containing the
accepted t14 fixes. Their `deps` arrays are empty because the passed t14 record
is coverage-only rather than an executable prerequisite. Their write scopes
are disjoint, so they may run in one parallel wave. The ordering below is risk
priority, not a new dependency.

1. **t15 — risk-routing Product/Design reconciliation.** Reconcile AC15 and
   AC22 in-place to the already authorized risk-scaled rule and make the
   Product wording, approved Design artifacts, runtime routing, and focused
   fixtures agree. Documentation-only and simple low-risk changes select
   exactly one attributable risk-selected deep lens. A missing code-module
   mapping alone does not widen. Only evidence of genuine ambiguity,
   corruption, mixed/substantive impact, or other material risk widens to the
   four architecture, code-quality, security, and QA floors, and the widening
   records its reason. The focused matrix also conserves the selected slot(s)
   and the eight-lens cap. This is reconciliation to the approved criterion,
   not a new product requirement or design approach. For mechanical ownership,
   t15's `criteria` array copies the currently persisted AC15 and contradictory
   AC22 text verbatim; AC22 is the defect input, not the intended output. The
   task must reconcile the persisted AC22/acceptance map and Design wording to
   the authorized rule before changing runtime behavior.
2. **t16 — collector normalization order.** Move audited, metadata-only
   normalization of derivable verdict/count/severity/summary contradictions
   ahead of rejection. Record before/after values, derivation authority, and
   fingerprint equivalence, then complete collection in that same call without
   rerunning the producer. A substantive mutation remains a repair failure and
   retries only the affected original producer; unrelated valid results are
   conserved.
3. **t17 — evaluator-unavailable readiness state.** Preserve evaluator
   infrastructure unavailability as a distinct non-judged state with exact
   outage identity. It cannot become a pass, mark the task passed, or open
   readiness without independent evidence or an explicit governing policy.
   Cache behavior stays exact and truthful across evaluator, engine/version,
   capability, repository, worktree, validity-window, expiry, and recovery
   changes.
4. **t18 — shape-safe status contract projection.** Centralize the supported
   contract projection used by dashboard and CLI/status rendering so coding,
   read-only, review, and partially released/released contracts are handled
   without a direct `contract['coding']` assumption. Status stays snapshot-only,
   non-gating, and semantically consistent across JSON, inline, Markdown, and
   optional HTML delivery.

Each task owns one focused `python3 -m pytest -q` command naming only its
cluster's tests. Any new regression test named in a command is created within
that task's declared test scope. No repair task may weaken, skip, xfail,
remove, or reclassify an existing governance assertion.

## Risks and rollback

The principal risk is encoding the AC15 rule in runtime while leaving AC22 or
the approved Design prose contradictory. t15 therefore owns those artifacts
together and must preserve one attributable deep slot for low-risk work rather
than using a missing mapping as a proxy for substantive risk. Any four-floor
widening needs an explicit evidence-backed reason.

Collector repair is limited to authority-derivable metadata. It must not
rewrite findings, identities, evidence, or other substance under the label of
normalization. Evaluator outage handling must not convert operational
availability into an engineering judgment. Contract projection must preserve
the original status semantics rather than fabricate absent fields.

Rollback is task-local: revert only the non-convergent repair while preserving
t1–t14 and the other accepted repairs. A focused failure stays inside that
task's bounded fix cycle. Broad compatibility is assessed once by CI after the
four focused tasks and independent evaluations close; a CI failure is new
integration evidence, not authority to silently widen a leaf task.
