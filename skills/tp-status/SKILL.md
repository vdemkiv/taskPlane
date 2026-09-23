---
name: tp-status
description: Show delivery progress, the responsible owner, actual outcomes, and available token usage without initializing a workflow.
---

# Delivery status

Use current task context first. For recorded delivery, invoke the installed
`taskplane/tp.py flow report --workspace <checkout>` once. Show the orchestrator
as owner, the latest meaningful milestone, actual verification, remaining work,
and useful token or waste observations. An advisory is not a blocking gate.
Missing usage means unknown, not zero. A recorded milestone is not proof that
acceptance criteria passed; check the cited evidence when making that claim.

Do not start a flow or mutate evidence to answer status. Distinguish work produced,
evidence validated, human or policy approval, stale state and legacy unverified observations.
Show workflow_available separately from authority_verified. Native workflow gates
can be active with host protections unavailable; capability_blocked applies to an
explicit protected_host request without a verified integration.

## Shared delivery state

For an active delivery, follow [the shared flow](../tp-go/references/shared-flow.md).
Use its existing run, task decomposition, dependency graph and dashboard. Return
evidence to that run; the root orchestrator owns stage advancement.

Show native discovery separately from protected storage, human origin, tool
containment and process revocation. A plugin version or observed hook is not
proof of those capabilities; an unavailable native owner stays unverified.

Select the current task’s run explicitly when available. Report approval mode, policy
version and pause reason, current visit versus run tokens, unknown/unallocated usage,
snapshot timestamp and graph freshness. Never equate old tab contents with current state.

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
