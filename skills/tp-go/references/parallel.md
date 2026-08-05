
# /tp-parallel — many agents, one harness each

`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`. Requires a loop
initialized with `--parallel` and an approved plan whose tasks carry
`scope` (+ optional `deps`).

1. `$TP loop wave` — the engine picks the wave (deps passed + pairwise
   scope-disjoint) and explains every held task.
2. Per entry, follow `discipline/worktrees.md`: `git worktree add
   .tp-work/<id> -b tp/<id>`, then `$TP loop claim <id> --agent-workspace
   .tp-work/<id>` — the task's contract activates in THAT worktree.
3. Dispatch ONE subagent per task, all concurrently (single message,
   multiple Task calls). Each builds inside its worktree only, then
   commits and runs `$TP loop submit pass|fail --task <id>`. The orchestrator
   verifies the fingerprint and alone runs the matching `$TP loop gate`.
4. When the wave empties, `$TP loop next` evaluates each built task
   (read-only, routed lenses, impact). On PASS merge `tp/<id>` into the
   main tree and remove the worktree; then the next `$TP loop wave`.

Never widen a worker's scope to dodge a hook denial — overlapping scopes
are the engine's signal to serialize, and merge conflicts are a retro
finding about the plan.

## Show the wave

After `loop wave`, worker submission, and each `loop gate --task`, run
`$TP summary` for the user-facing state; render `$TP dashboard` when
supported. The agent cards
show each worker's task, contract scope, and status (queued → running →
built → passed) live, inline in the reply.
