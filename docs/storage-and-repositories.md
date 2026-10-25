# Repository preconditions and hybrid storage

Repository acquisition is a taskplane precondition. A local path, repository
URL, ref, or pull request is resolved before a contract, dependency graph, or
review lens can run. `tp repository prepare <target>` is the shared entry point;
`tp review start <target>` invokes the same kernel automatically.

## Hybrid layout

With the default `TASKPLANE_HOME=~/.taskplane`, private machine state is kept
outside the source checkout:

```text
~/.taskplane/
  repositories/<repository-key>.json
  checkouts/<repository-key>/mirror.git
  checkouts/<repository-key>/worktrees/<checkout-id>/
  projects/<repository-key>/knowledge/
  runs/<run-id>/
    manifest.json
    journal.jsonl
    state/
    graph/
    evidence/
    lenses/
    artifacts/
  cache/graphs/<repository-key>/<head>/<scanner>.json
```

The checkout contains source only. Private control state, graph evidence,
leases, and reports belong to the run. Shared team knowledge alone is stored
in the repository under `.taskplane-kb/knowledge/` when team/enterprise mode
is selected. A secure locator in Git metadata binds the checkout to its
repository key and run; untrusted PR content cannot commit or spoof it.

## Prepare, ask, resume

```bash
tp repository prepare https://github.com/OWNER/REPO/pull/123
```

`ready` means the manifest contains a verified repository identity, checkout,
head/base/merge-base, and changed-file set. `needs_user` is recoverable, not a
failed review. It carries one bounded action and prompt for missing GitHub
authentication, a required tool, storage authorization, retry, or cancellation.
To change the target, cancel this run and prepare the new
target; the engine never silently retargets an existing manifest. The driver
presents the exact prompt in the current chat;
after the human responds it resumes the same run:

```bash
tp repository resume --run-id RUN --action-id ACTION \
  --response approve --by "human identity"
```

Only engine-stored argv may be executed after approval, without a shell. A
failed approved action returns a new prompt; it does not strand the run or ask
the human to open a terminal/task. `tp repository status --run-id RUN` exposes
the durable state.

## Review ordering

The standalone review order is absolute:

1. acquire and verify repository/PR;
2. create the run manifest;
3. build or load graph evidence bound to repository + head + scanner;
4. compute graph quality and the bounded blast radius; if enrichment remains
   incomplete, preserve the warning and use the pinned diff for standalone
   PR Review (Evaluate/EM remain fail-closed);
5. route the complete 26-lens disposition;
6. only when routing is ready, activate the read-only contract and dispatch
   the exact deep slots plus at most one light sweep.

Sparse or stale graph evidence is never presented as complete. Standalone PR
Review continues from the pinned diff with an explicit degraded-graph warning
and architecture/security floors. Governed Evaluate/EM still stop with zero
lens dispatch because those are delivery gates. A repository/checkout failure
is reported as a precondition—not mislabelled as graph sparsity.

## Legacy migration

`tp repository migrate` audits old `.em-review/scratch` checkouts. A clean,
identifiable checkout is registered as a non-destructive alias. Dirty,
ambiguous, or local-only checkouts are reported for human review. Migration
never moves or deletes source, and new source acquisition never writes there.

## Native snapshot retention

New native dashboard pairs use a separate `snapshot-managed-v1.sqlite3`
generation with a workspace-bound marker. The old `snapshot-catalog.json`, old
pairs, controller history and approvals are preserved unchanged. They are never
adopted into deletion ownership. This change bounds future transient growth; it
does not reclaim the historical backlog.

A producer must register references before publishing them:

```python
from taskplane.snapshot_retention import register_references
register_references(workspace, complete_value, "external-report")
# Only now publish the report containing the snapshot reference.
```

Use `format="json"` for complete JSON bytes/text, `format="text"` for complete
UTF-8 text, and the default for a decoded structured value. Never register a
truncated preview. Registration recognizes complete native snapshot names,
local paths and file URLs, including complete tokens in report links or prose.
Ordinary names such as `snapshot-v1.json` are ignored. Invalid declared native
paths, unavailable pairs, incomplete traversal and exhausted verification budgets
raise before publication. A successful registration permanently pins its pairs;
a failed later publication may safely retain an extra pin.

Reference traversal uses one iterator per ancestor (maximum depth 32), rather than
allocating a stack entry for every sibling. Its 64 MiB work budget counts every
value and key, including UTF-8 text, scalar and container units. This conservative
lower bound on serialized JSON size admits supported large payloads without an
independent 100,000-node ceiling. Complete text/JSON input also retains 64 MiB byte
admission. Cycles, unsupported values and exhausted budgets refuse registration;
no partial reference set is published.

Context storage, evidence sealing, lifecycle closures, journal/attachment writes
and snapshot publication register their complete inputs. External writers that
write raw files must use the same registration API first. A raw file on disk no
longer implicitly protects a new managed pair. Legacy files remain preserved.

Collection uses a persisted finite high-water mark and descending indexed cursor.
Pins, selected views, latest active views and leases are checked again for each
candidate. New publication does not restart a sweep. Each call examines at most
64 candidates and reserves at most 128 MiB for complete pair verification, enough
for the existing two 64 MiB member limits. A smaller configured allowance defers
the candidate explicitly. Deletion intent is committed before either unlink;
recovery verifies any surviving member before completing that intent. Missing or
corrupt ownership is never rebuilt into deletion permission. These are logical
row, SQLite VM and source-byte bounds, not hard wall-clock or internal I/O limits.

The lock order is controller/context/journal, then snapshot. Registration releases
the snapshot lock before the producer writes its reference. Closure and snapshot
producers already holding that lock use its internal registration helper. The
collector never calls controller or context APIs. Directory inventory remains a
separate optional observation and may report a lower bound; legacy totals remain
unknown when not independently measured.
