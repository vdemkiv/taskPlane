# Native delegation

Native dispatch is the default for useful independent work under this skill.
Every new run scope must declare `execution_contract: "native-default/v1"`; this
is mandatory in this entry flow. Publish typed `execution: "native_required"`
tasks for ready independent work and one worker per selected Engineering lens,
with a unique `review_lens` on each lens task. This instruction authorizes that
bounded delegation; do not ask again solely because the user did not name agents.

Explicit user serial/no-delegation constraints take priority. Dependencies,
read/write conflicts and trivial scope can justify `execution: "root"` with a
substantive `execution_reason` and `execution_reference`. Root lens coverage is
`serial_scope`, never native independence. Observe host capacity, launch ready
independent tasks together and refill slots as prerequisites complete. Limited
capacity queues distinct workers; reusing one identity for several lenses does
not satisfy independence. An unavailable adapter needs an observed reason/reference;
required native tasks remain incomplete and block sealing.

Use the installed prepare/claim/context/join/result protocol. Execute the exact
returned `next_action` with the installed runtime launcher; it retains workspace,
run and task. Follow `--read-required` actions until `remaining_required` is zero.
Workers return scoped evidence. Only the root verifies and accepts joined fresh
results and advances phases under the existing approval policy. `native_verified`
lens coverage binds `task_id`, `grant` and the actual worker `reviewer`; labels alone
cannot satisfy frozen requirements. Historical untyped evidence stays unverified.


Give the worker the shared root workspace, run ID, attached task ID, graph and
dashboard paths. Require [the shared flow](shared-flow.md), so the worker reads
existing dependencies and returns its evidence to the root. Also give the
worker the goal, relevant paths, scope, existing decisions, and expected
verification. Use native agent tools and the host's supported settings. Keep
context small; do not copy whole histories, catalogs, or unrelated source trees.
Avoid conflicting writes by assigning disjoint files or isolated checkouts.

Use native status observations to join the attempt, inspect the actual output, and integrate it.
`wait_agent` only waits for mailbox activity; neither it nor an interrupt request
proves completion. Native terminal/idle status and root result verification are separate.
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

Check the actually loaded runtime and workflow coverage before dispatch. The scoped
native adapter supports explicit Codex collaboration and Claude Agent/Task contracts
in `native_workflow`. Source changes and fixture tests do not prove the host loaded
this adapter. Do not substitute a shell worker, new user-owned task, cache edit or
disabled hook for a refused native launch.

## Prepare, execute and accept

The root uses `flow worker --operation prepare --workspace PATH --run RUN --task ID
--expected-revision N --worker-json JSON`. JSON supplies `capacity` with observed
`host_slots`, `includes_root` and a real source `reference`; optional
`configured_limit` and `resource_limit` narrow it. No default of two is applied.
Count reservations, live attempts and unknown launches. Fill available capacity
with useful ready tasks, then refill when results satisfy prerequisites. Two
overlapping workers is the minimum live acceptance test, not the normal limit.

Preparation returns a grant and bounded message. Pass the message unchanged as
the prefix of the native task request, with the prepared `task_name` and
`fork_turns: "none"`. Use the actual exposed native tool schema. An observed call
binds the reservation; a returned canonical name is correlated to the real session
ID through native parent/name metadata. Ambiguous correlation remains blocked.
The prepared name includes the reservation ID because Codex desktop can expose
encrypted message text to hooks. Preserve that exact name; custom name prefixes
are limited to 46 characters. This is a correlation token, not worker authority.
Opaque follow-up messages without an observable grant remain unsupported; use a
new prepared native spawn for a fresh attempt instead of weakening grant checks.

The worker claims its observed identity using `flow worker --operation claim
--workspace PATH --run RUN --grant GRANT`, then obtains `flow context --task ID`
with workspace/run and consumes every required page. Its receipt is unique to the
worker, grant, attempt, task and generation. Only assigned writes are admitted.
Pinned inputs survive own output edits; changed read inputs require revalidation.
Grants pin exact prerequisite results and deliver their output evidence in task
context. Join affected readers before replacing prerequisites. Accepted results
retain read-input and prerequisite fingerprints, so later source edits invalidate
dependent admission until fresh verification. Workers may report progress only to
their bound parent; this does not permit nested delegation or root controls.
Preparation and launch reject both write/write and read/write conflicts, including
replacement workers that would change a live reader's prerequisite evidence.
Workers cannot operate root controls, publish tasks, start a run or delegate.

The root uses `--operation accept-result --task ID --grant GRANT` with the current
revision and `--worker-json` containing `outputs` and `checks`. Each check has a
name, `status: "pass"` and an existing evidence path. Native attempts must be
joined, context consumed and child commands stopped. Explicit root-owned prerequisites
use the same result operation without a grant or fictional worker ID. Root results
pin the same declared source inputs as native attempts. Legacy root results without
`input_contract: "declared-source/v1"` require reacceptance before reuse. Read and
verify actual outputs first; assertions in check records are not independent proof.

`--operation status` shows attempts and ready/waiting reasons. `capacity` updates
observed limits; a reduction stops admission without cancelling live work.
`abandon --grant GRANT` releases only an unlaunched reservation. An idle worker's
follow-up needs a new preparation with its actual `worker_id`, a fresh grant and
fresh task context. Unknown attempts remain visible and block sealing.
For a reused worker, join with a native status call admitted during the new
attempt, or a terminal event correlated to that attempt's launch. An old poll
or an uncorrelated stop cannot complete a newer attempt.
Missing or conflicting stop identities preserve the join barrier. Desktop tool
hooks may report the parent session alongside a child `agent_id`; select that child
only with matching native lineage before checking its scope. An unresolved or
conflicting child actor refuses admission. Start/stop lifecycle hooks name an
observed child, not a replacement actor. A different executing environment identity
is also used only with matching native child lineage when the actor field is absent.

Native final status is an observation, not a measured execution interval. Live
overlap evidence must use actual task-start/end records; delayed polling alone
cannot prove overlap. Keep Claude contract tests distinct from live Claude coverage.


## Efficient native startup and context

For every new native task, declare exact `read_inputs` from the run verification
inputs or accepted Build paths, a concise `purpose`, and `context_budget_bytes`
(default 128 KiB of unique required bodies). Include source dependencies and tests
needed for the conclusion; narrow inputs must not hide a relevant dependency.
Missing read inputs retain conservative legacy coverage. Inspect preparation's
`context_preflight` before launch. Declare source/log/test detail artifacts as
`source`, `raw-log`, `verification` or `supporting`; keep concise requirements and
reports normative. Use `required_for` when supporting bodies are mandatory.

Always supply `fork_turns: "none"` explicitly. Start the first useful scoped task,
then observe its successful claim, complete context and matching automatic pre/post
hook pair before preparing the rest of the cohort. Scoped preparation enforces
this automatically; `readiness_after` can name an additional same-phase startup
prerequisite. Release remaining ready work together while the first task works.
Do not use throwaway probes or relaunch unchanged failures. A repeated scoped
attempt needs `retry_reason` naming the observed defect or changed input.

Execute the exact returned context action. New scoped workers use `--drain`, which
returns at most 32 KiB including bodies and receipt. Return every response to the
consumer, then follow `next_action` only while `remaining_required` is nonzero.
Terminal responses have `done: true` and no action. Accumulate all subprocess/tool
chunks until exit before parsing; never parse a running handle's partial output.
Keep the combined response budget large enough and use bounded long waits (up to
60 seconds between user updates), not repeated short model-facing polls.

After a narrow repair, repeat affected checks and reviewers only. Unchanged
scoped results remain fresh within their original binding; across visits use
independently fingerprinted check evidence and a fresh scoped delta review.
Never transfer approval, worker identity or a context receipt to another binding.
Inspect attempt purposes, retry causes and delivered bytes in worker status and
the dashboard. Delivered bytes, native tokens and Codex allowance are different
measurements; no allowance saving can be inferred from byte counts alone.
