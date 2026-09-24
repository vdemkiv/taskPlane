# Native delegation

Delegate only when authorized and a bounded task can usefully run independently.
The orchestrator retains responsibility for integrating the result and completing
the user's flow. A separate worker for every stage is not required.

Give the worker the shared root workspace, run ID, attached task ID, graph and
dashboard paths. Require [the shared flow](shared-flow.md), so the worker reads
existing dependencies and attaches its evidence to the same run. Also give the
worker the goal, relevant paths, scope, existing decisions, and expected
verification. Use native agent tools and the host's supported settings. Keep
context small; do not copy whole histories, catalogs, or unrelated source trees.
Avoid conflicting writes by assigning disjoint files or isolated checkouts.

Wait using the native wait tool, inspect the actual output, and integrate it.
Fix concrete defects without repeating all reviews. Interruptions and missing
results are not successful completion. Telemetry records available activity and
usage; missing observations never prevent collection or progress.

Native host permissions and the user’s scope remain authoritative.

Workers cannot accept checkpoints or broaden writes. Use the accepted phase scope
and join known live work before sealing. Native-workflow scope checks apply to
covered structured tools and auditable source effects; they do not certify opaque
commands or host-wide process containment. Return requested changes as evidence;
route amendments require human acceptance. Normal phase acceptance follows the
root’s recorded manual/autonomous policy; workers never grant it independently.

Observed handles retain their visit and revision. Known terminal or stale handles
cannot receive input through a covered hook. Unknown coverage stays visible.
Protected-host handles additionally require native process identity and complete
revocation proof. No Stop/cancel observation manufactures approval or process exit.

## Consume bounded context

After activation, start, advancement or resumption, use the installed runtime's
`flow context --workspace <checkout> --run <run>` and consume the returned
handoff with `--consume <handoff-ref-sha256>`. Use `--task <current-task-id>` for
focused work. Read every omitted required input using `--read <ref-sha256>`;
follow index children and page cursors until all required bodies are returned.
A fresh consumer receives these bodies, scoped source and relevant findings;
do not copy the predecessor conversation or tool history into its instructions.
No context operation authorizes dispatch or a phase transition.

For phase submission, obtain a current whole-phase receipt (without `--task`)
and include the returned `context_receipt` object in the phase output. Renew it
after source, scope, revision or accepted input changes. New bounded/v1 runs
reject missing, partial, foreign or stale receipts. Legacy runs keep their
original evidence contract. A receipt proves returned data, not model attention.

Routine command summaries preserve the current gate and reference full details.
Expand specific references when needed; request `--full` only for a consumer that
requires the complete legacy report. Native dashboards retain complete evidence.
Reuse a prior passing check only when its verification record matches current
source/dependency, tests, runtime, environment, command and criteria fingerprints.
Retain failed/unknown results and findings; reuse never transfers approval.

## Adapter availability

Check workflow coverage before dispatch. The current cooperative adapter reports native delegation unsupported because it cannot bind worker writes to a verified task scope. Do not work around a rejected agent tool with a shell subprocess or new user-owned task. Root review may produce attributed findings, but it is not independent review; any required independent gate remains a disclosed coverage limitation.
