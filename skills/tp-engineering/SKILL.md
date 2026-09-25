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
   host's permissions and agent lifecycle. Use supporting tools or agents only when
   the task warrants them and the user or applicable instructions authorize them.
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

When authorized, use the installed native dispatch protocol: root prepares a run-bound
task grant, the observed worker claims its identity and consumes its own task context,
and root verifies the joined result before satisfying dependencies. Fill observed
capacity with useful independent work; two is only the minimum live acceptance test.
Workers return evidence and cannot operate root phase controls or publish task definitions.
Require actual loaded-runtime and native execution evidence for live claims.
