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
