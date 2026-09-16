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
