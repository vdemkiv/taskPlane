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
5. record the catalog disposition and select exactly 4–5 relevant quick lenses;
6. only when routing is ready, activate the read-only contract and dispatch
   those selected lenses concurrently.

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
