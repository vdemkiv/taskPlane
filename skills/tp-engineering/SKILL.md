---
name: tp-engineering
description: Review source code, changes, architecture, or security and report actionable findings. Also consumes Engineering evidence when explicitly invoked by a Taskplane delivery stage. Review does not implement fixes.
---

# Engineering review

Before substantive work, resolve this installed plugin's runtime and run
`flow activate --workspace <checkout> --phase tp-engineering --request-reference <actual-user-request>`.
Inspect `flow report`: reuse the matching active visit or initialize the appropriate
scoped run before continuing. This requirement includes standalone work and resumed
stages; loading a skill alone grants no phase approval. See the shared flow for
bootstrap, native dashboard handoff and legitimate user waits.

Use the current conversation and native tools to inspect the requested repository.
Preserve the user's scope and existing decisions. Follow
[the shared flow](../tp-go/references/shared-flow.md) for every review, standalone or
within delivery: reuse a relevant run or start at Engineering, inspect dependency
impact and source decomposition, attach the review task decomposition and evidence,
and maintain the shared dashboard. Standalone code review starts with `--standalone --phase engineering`; it does not require a full delivery route. The ordinary native workflow uses cooperative local guards; protected host authority remains separately unavailable.

1. Select the readable checkout and the requested revision or comparison. A whole
   repository request includes its tracked source and needs no artificial diff.
   Reuse an available checkout; acquire or transfer source only when it is unavailable.
2. Inspect relevant source with native file, search, and command tools. Follow the
   host's permissions and agent lifecycle. Select useful independent lenses and apply the native dispatch default below,
   respecting explicit serial constraints and observed capacity.
3. Report concrete findings with severity, triggering conditions, file locations,
   and consequences. Distinguish confirmed defects from questions and missing evidence.
   Reuse applicable CI results. Run additional checks only for a specific evidence gap
   or changed behavior, respecting a static-review request.
4. Present the findings and shared dashboard, including an honest coverage summary
   and remaining uncertainty. Update the task and review evidence before rendering;
   acceptance under the shared approval policy is required before advancement. A
   dashboard edit cannot grant it. Apply fixes only through an approved Build scope.
5. When findings lead to Product work, retain their IDs, severity, source locations
   and evidence as Product inputs. Continue the same active run through Product,
   Design and the remaining authorized phases. Engineering is a valid starting
   point, not only a final review stage.

If a pinned inventory is useful, resolve the installed plugin's `taskplane/tp.py`
from its supplied root or this skill's location and invoke it with Python:

- `review start --scope repository --workspace <checkout>` for tracked source.
- `review start --base <ref> --workspace <checkout>` for a comparison.

The response references the selected source artifact; read it through native tools.
The inventory captures source only. Reviewers use native tools and the host's permissions.
Attach findings and verification to the shared run. The review follows the shared approval policy. A human must accept any explicit repair or delivery extension; the
root orchestrator prepares and carries out that approved transition.

## Shared delivery state

These defaults apply equally to standalone Engineering and delivery. Use one run,
task decomposition, dependency graph and dashboard for the requested scope. The
root orchestrator owns stage advancement; a review-only request still ends at review.

The shared flow defaults to explicit human approval at each phase checkpoint.
A recorded, explicit run policy may authorize automatic approval as described there. Use
the dependency graph with source component decomposition, task DAG and shared
dashboard throughout. Unsupported host authority must be reported, never bypassed.

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

## Scoped native workers

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
