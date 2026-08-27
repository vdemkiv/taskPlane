# R-0013 bootstrap/runtime isolation glitch

Status: recorded before the fresh R-0013 Build  
Recorded: 2026-08-27  
Classification: process/bootstrap/runtime defect; no R-0013 product defect established

## Decision and disposition

The earlier R-0013 execution is abandoned as delivery authority and retained only as audit evidence. It is not resumed, merged, or used as the source baseline. R-0013 restarts from exact `origin/main` SHA `c9ec81a021ac74b048bfa58abfbfec870e49711a`, which includes the latest CI repairs, in a new worktree and the dedicated `TASKPLANE_HOME=/Users/vdemkiv/.taskplane-r0013` runtime.

The archived R-0001 delivery remains archived. No R-0001 requirement, Design, Plan, runtime, or approval state is authority for this R-0013 Build.

## What failed

The prior R-0013 loop was created in a Taskplane runtime home named for the already archived R-0001 work (`/Users/vdemkiv/.taskplane-r0001-retro-design`). The controller's secure workspace locator then retained absolute paths into that runtime. Changing the `TASKPLANE_HOME` environment variable afterward did not re-anchor the controller: `tp kb where` continued to resolve the R-0001-named home.

A separate, older project-scoped R-0013 loop also remains in the dedicated R-0013 home for `/Users/vdemkiv/.codex/worktrees/6dcd/taskPlane`. It stopped at PM on SHA `726acd108d3ca431e680183de129918842202eda` and has no requirement binding. This explains why a home-wide search appears to show an active loop even though the fresh worktree resolves a distinct, empty project store. Both old loops are retained as evidence and neither is inherited.

Seven independent Wave-1 tasks reached the built/evaluation boundary. At the first leaf evaluation, Taskplane correctly required a genuine external host-producer receipt. The already-running Codex session had no loaded host receipt. Installing the repository hook bridge during that same session made the configuration present on disk but could not retroactively load it into the active host session. A separate normal-trust host probe also produced no valid receipt.

This created a bootstrap deadlock: leaf evaluation required live host integration evidence before downstream shared runtime/integration work could run. No defect in the T01 implementation was established; its declared tests and direct fail-closed evidence were green. The failure was the run bootstrap, runtime isolation, and plan/execution boundary.

## Preserved audit evidence

- Old controller: `/private/tmp/taskplane-r0013-design-main.BIvGYf/repo`
- Old run ID: `24d8d6dbda884081853e98e7f7439cd3`
- Old controller baseline: `d6b2cd9ca98d2a782748ef82afd831690cdd6a78`
- Old runtime home: `/Users/vdemkiv/.taskplane-r0001-retro-design`
- Blocking observation: genuine external host-producer receipt absent; W31 remained open
- Earlier dedicated-home loop: project key `-Users-vdemkiv-codex-worktrees-6dcd-taskPlane-1cf801a9`, step `pm`, revision `726acd108d3ca431e680183de129918842202eda`
- Fresh project store: `/Users/vdemkiv/.taskplane-r0013/projects/-private-tmp-taskplane-r0013-fresh-main-XJGRl1-da103a2d`

The old controller, run records, task branches, and worktrees are preserved for audit. They are not copied into the fresh Build and must not be treated as approved implementation evidence for the new baseline.

## Required prevention controls

Before any fresh Build task is dispatched:

1. Verify the installed marketplace manifest is exactly Taskplane 2.17.22.
2. Verify the source checkout is clean and at the intended exact `origin/main` SHA.
3. Set the dedicated R-0013 home for every Taskplane command.
4. Verify `tp kb where` resolves the intended R-0013 home before loop creation.
5. Verify no R-0001 requirement or runtime state is selected.
6. Prove the host integration/receipt prerequisite before implementation dispatch, or stop before Build.
7. Ensure no leaf Evaluate checkpoint depends on runtime integration that remains downstream in the same Plan.
8. Keep Build and evaluation at zero lenses; use Codex-native parallel execution for disjoint tasks.

## Fresh baseline

- Source: `origin/main`
- Exact SHA: `c9ec81a021ac74b048bfa58abfbfec870e49711a`
- Fresh branch: `codex/r0013-fresh-build-c9ec`
- Fresh worktree: `/private/tmp/taskplane-r0013-fresh-main.XJGRl1`
- Required runtime home: `/Users/vdemkiv/.taskplane-r0013`

This record documents the glitch and the restart decision. It does not authorize a Design change, implementation scope expansion, release, merge, or push.
