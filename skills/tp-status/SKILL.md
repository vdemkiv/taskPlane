---
name: tp-status
description: Show delivery progress, the responsible owner, actual outcomes, and available token usage without initializing a workflow.
---

# Delivery status

Check the conversation for an earlier execution request that never initialized.
Report it as pending setup, retaining its repository, route and requested lenses.
If `flow report` returns `workspace_binding`, show its `detail` and `next_action`;
history is unknown when inspection failed. Never infer “has never tracked a delivery”
from a refused report or missing state in an incidental directory. A readable selected
folder without state needs initialization for the earlier task, not a new `tp-go`
request. Explain the precise next step; do not repeat folder selection if already done.
A status-only invocation still does not initialize or resume work by itself.

Use current task context first. For recorded delivery, invoke the installed
`taskplane/tp.py flow report --workspace <checkout>` once, adding `--run <run>`
when the current task identifies its run. Show the orchestrator
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

Read the existing run, task decomposition, dependency graph and dashboard as needed
to explain progress. Use the [shared flow](../tp-go/references/shared-flow.md)
to interpret recorded decisions.

Show native discovery separately from protected storage, human origin, tool
containment and process revocation. A plugin version or observed hook is not
proof of those capabilities; an unavailable native owner stays unverified.

Report approval mode, policy
version and pause reason, current visit versus run tokens, unknown/unallocated usage,
snapshot timestamp and graph freshness. Never equate old tab contents with current state.
