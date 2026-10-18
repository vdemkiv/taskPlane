---
name: tp-status
description: Show delivery progress, the responsible owner, actual outcomes, and available token usage without initializing a workflow.
---

# Delivery status

Use current task context and the actual runtime's `flow report --workspace PATH`.
Select the run explicitly when known. Report current phase/owner, produced work,
actual checks, pending approval, blockers and next useful action. Distinguish
validated evidence, accepted results and historical observations.

Status alone does not initialize, activate or resume delivery. If inspection
fails, report its precise reason; missing state or a refused report does not prove
there was no work. Preserve an earlier requested route while identifying its
setup blocker. Do not repeat project selection when already known.

Usage, graphs and dashboards are optional diagnostics. Missing usage is unknown,
not zero. Do not refresh state or consume receipts just to answer status.
