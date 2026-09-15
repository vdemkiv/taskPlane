# Delivery Engineering review

Read this only for an explicitly supplied Taskplane stage dispatch or delivery run.
Ordinary repository review uses SKILL.md directly.

## Stage-native review boundary

Engineering consumes the sealed direct Evaluate evidence; it never launches
lens workers or a promotion wave. Standalone Review routing, when explicitly
requested, is a separate Review-owned surface and is never attributed to
Evaluate or loop EM.

When Taskplane supplies a `taskplane.stage-dispatch/v1` envelope, treat its
verified `taskplane.stage-startup/v1` value as the complete execution context.
It contains the current stage authority, budget, and scope, one
`taskplane.stage-handoff/v1` versioned bounded manifest, explicitly selected
content-addressed artifact references, execution claim, and attempt id.
Use only those inputs. Do not import a predecessor conversation, event log,
tool transcript, lease, meter, active contract, runtime environment, mutable
worktree, or execution tree. Do not open a predecessor execution root to start
Review, render findings, decide sign-off, or prepare Retro.

The bounded read model for Review and sign-off must expose the current stage,
predecessor outcome, handoff fingerprint, and child lineage. A missing,
ambiguous, corrupt, oversized, or fingerprint-mismatched manifest or summary is
a refusal, never permission to inspect predecessor runtime state or choose a
stage heuristically. The dispatch's authority and declared scope still govern
every review action; selected artifacts do not grant broader repository,
approval, or cleanup authority.

A Review, Evaluation, Engineering, or other non-build stage may finish
`closed` or `discarded` with the required attributable reason and without an
implementation child. Terminalization retains its content-addressed artifacts
for audit, does not reopen or rewrite its predecessor, and never invokes
worktree cleanup. Later reuse requires a new explicitly authorized handoff;
`discarded` results are not consumable by default and require exact explicit
nonconsumable-reuse authority. These lifecycle operations do not change R-0003
enforcement, ReviewKernel evidence/provenance, collision
isolation, delivery gates, final human sign-off, or exact-worktree
cleanup eligibility.

Engineering reads the current run aggregate and its selected sealed evidence.
An unsupported manifest is a refusal; no alternate runtime supplies review inputs.

Engineering consumes the sealed Evaluate evidence and launches no lens workers. Use the exact stage input and output commands supplied by the run. A missing candidate remains a failure; use the existing exact-operation recovery. Read [harness rules](../../taskplane/references/harness-rules.md) only for this delivery workflow.

For an existing legacy review, `tp review collect` validates the retained results,
`tp findings` reads them, and `tp review signoff` records an authorized decision.
Use the exact run identifier and returned continuation commands. These operations
are not prerequisites for ordinary source review.
