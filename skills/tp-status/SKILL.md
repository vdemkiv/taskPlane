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

Do not start a flow to answer status. Use legacy `summary` or the
[legacy status reference](references/delivery-status.md) only when the user asks
about an older governed run. Distinguish historical gate state from current work.
