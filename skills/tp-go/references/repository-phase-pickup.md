# Repository-phase continuation

Use this route when the user supplies a sealed repository phase handoff or asks
to continue work already exported through `phase export` / `phase submit`.
It is distinct from `loop next`: do not recreate or consult predecessor loop
state to fill gaps. Older Design/Plan documents without a valid handoff are
reference input for a newly authorized phase, not pickup authority.

For a new Design with no predecessor handoff, use the same `phase export
--request <repository-relative-json>` command. Its request has `phase:
"requirement"`, `outcome: "done"`, `durable_progress: {"phase": "requirement",
"state": "terminal", "outcome": "done"}`, and the existing handoff `material`.
Select and commit the full requirement, baseline graph, and exactly its declared
dependency requirement JSON artifacts first. Bind the material to that clean
source and its explicit attributable initial authorization; the command does
not infer or mint approval. Design/Plan must be null, tasks and progress receipts
empty, and both predecessor lineage values null. Product readiness and exact
acceptance/contract coverage are checked before publication. Commit the export,
then pass the returned `handoff_path` to a fresh Design pickup. Missing legacy
inputs need refinement, not synthetic predecessor state.

Run `phase pickup <handoff>` for its declared next phase, or `phase resume
<handoff>` for an interrupted same-phase handoff. The response contains the
current owner dispatch. Resolve its package-relative role reference and verify
the digest; pass the full role instructions and complete dispatch with its
exact name, role marker, model when non-null, reasoning effort and environment.
Use `fork_turns: none`. Never expose predecessor runtime or replace a running
owner. Dispatch only when `dispatch_allowed` is true.

A waiting Build is not a runnable worker. Follow its admission reason: it needs
a genuinely fresh native root and authenticated host-start usage, not a child
masquerading as a root. Use the supported host launcher when available; report
an unavailable capability instead of granting budget or fabricating a receipt.

For Design/Plan, the owner writes only its declared artifacts and stops. After
native completion, commit those outputs and submit the emitted `seal_request`
with the observed `done` or `interrupted` status. The engine verifies terminal
identity and the exact observed bytes; it derives hashes, never judgments.
When `phase-review-required` is returned, dispatch exactly its allowed focused
reviewers, wait for every result, commit their declared outputs and resubmit
the returned `resume_request`. Already-running or completed reviewers are not
redispatched. Findings requiring changes do not authorize a successor.

When collection succeeds, commit its exact `commit_paths`, present the gate
and notices, and use its `export_request` only with attributable human approval
for the exact subject. Existing explicit approval may be recorded only within
its authorized scope; worker status or a derived fingerprint is not approval.
Commit the published export and give only that sealed handoff and its selected
repository artifacts to the fresh successor.

Build uses its prewritten `completion.request_path`. Preserve `native_task`
criteria and quality obligations alongside the portable task projection. When
required, `completion.quality_admission.command` begins an empty receipt after
the scoped candidate commit. Populate it only from observed checks with the
existing quality helper. `phase submit` admits that evidence before BUILD-C;
only successful BUILD-C publishes quality evidence and progress. Commit the
export, then resume only the remaining declared task. Build launches no lenses.

Phase `done` describes that phase, not final delivery approval. Evaluate,
Engineering, sign-off and Retro still need their actual governed evidence;
never manufacture their completion from a successful transport test.
