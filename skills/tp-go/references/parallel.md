
# /tp-parallel — many agents, one harness each

On Codex prefer `TP='python3 .taskplane/codex-hook.py'` when the stable
workspace launcher exists; otherwise use
`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`. Requires a
loop initialized with `--parallel` and an approved plan whose tasks carry
`scope` (+ optional `deps`).

1. `$TP loop wave` — the engine picks the wave (deps passed + pairwise
   scope-disjoint) and explains every held task.
2. Per entry, use its exact emitted `worktree`: `git worktree add
   <worktree> -b tp/<id>`, then `$TP loop claim <id> --agent-workspace
   <worktree>` — managed runs keep it in the external checkout registry;
   legacy unmanaged runs may emit `.tp-work/<id>`.
3. Dispatch ONE subagent per task, all concurrently (single message,
   multiple Task calls). Each task is an isolated stage with its own execution
   root. Pass its engine-emitted `taskplane.stage-dispatch/v1` unchanged; its
   bounded `taskplane.stage-startup/v1` contains the stage authority, budget,
   declared scope, execution claim, bounded versioned
   `taskplane.stage-handoff/v1` input handoff, and explicitly selected
   content-addressed artifacts are its only startup context. Never share
   predecessor agents, conversations, event logs, tool transcripts, leases,
   runtime state, or execution roots between workers. Each builds inside its
   worktree only, then commits and runs `$TP loop submit pass|fail --task
   <id>`. The orchestrator verifies the fingerprint and alone runs the
   matching `$TP loop gate`.
4. When the wave empties, `$TP loop next` evaluates each built task
   as a fresh bounded stage (read-only, direct evidence, zero lenses). On PASS merge
   `tp/<id>` into the main tree, then run the separate fail-closed R-0003
   cleanup action; only an exact registered managed worktree with merged-tip,
   re-resolved-primary-main, and last-moment eligibility proofs may be removed.
   Stage terminalization itself never removes a worktree. Then run the next
   `$TP loop wave`.

Never widen a worker's scope to dodge a hook denial — overlapping scopes
are the engine's signal to serialize, and merge conflicts are a retro
finding about the plan.

A non-build worker may finish `closed` or `discarded` without spawning Build.
Its artifacts stay addressable for audit, but no later worker consumes them
without a new explicit handoff and current authority. During rollback,
migrated v4 stage history remains readable and immutable; never reverse-
collapse it, reopen a terminal stage, or weaken the cleanup proof.

## Show the wave

After `loop wave`, worker submission, and each `loop gate --task`, run
`$TP summary` for the user-facing state; render `$TP dashboard` when
supported. The agent cards
show each worker's task, contract scope, and status (queued → running →
built → passed) live, inline in the reply.
