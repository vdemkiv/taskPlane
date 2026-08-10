# Native Codex subagent dispatch

Use this procedure whenever a taskplane action or lens brief is executed on
Codex. The CLI has already decided the role, contract, model tier, reasoning
effort, and evidence obligations. Codex supplies the transport; it does not
reinterpret those decisions.

## One brief, one exact task

For every brief:

1. Open the brief's exact `role_instructions` path, read that file completely,
   and include it with the full action payload in the delegated
   message. Never replace it with a summary. Include the payload's exact
   `role_marker` as a standalone line so strict dispatch can bind this native
   Codex task to the taskplane role that owns the contract.
2. Call Codex's native `spawn_agent` with the brief's exact `task_name` and
   `reasoning_effort`. Pass `model` only when it is non-null; null means let
   the subagent inherit Codex's model choice. The human-facing taskplane role
   remains the payload's `role`/`agent` and must not be renamed.
3. Independent, scope-disjoint briefs may be spawned concurrently. Never give
   two write-capable agents the same checkout: use the worktree and contract
   slot emitted for a parallel build wave.
4. Use native `wait_agent` with a bounded timeout, repeat while agents are
   making valid progress, and collect every final result before synthesis or
   an orchestrator gate. A timeout is a status checkpoint, not success; a fast
   result does not cancel a slower obligation.
5. If an agent is stalled, working the wrong scope, or violating its role,
   send a bounded correction. If that cannot restore the contract, use
   `interrupt_agent`, preserve the partial evidence, and escalate through the
   loop's human gate. Do not silently replace, waive, or mark the task done.

`SubagentStart` and `SubagentStop` hooks add bounded contract context and trace
lifecycle metadata. They are observability, not completion evidence. The
`PreToolUse` screen, worker submission, evaluator evidence, orchestrator-only
gate, and human checkpoints remain authoritative.

## Long-running loops

For a run likely to span many steps, recommend that the user start Codex Goal
mode with `/goal` and place the outcome, constraints, and verification criteria
in the goal. Goal mode does not expand permissions or replace taskplane gates.
Only the user starts a goal; do not claim that a skill or subagent started it.

## Claude parity

Do not modify the Claude path while applying this procedure. Claude Dynamic
Workflows remain an optional journaled transport. The portable task payload is
still the mandatory fallback and carries the same contracts, briefs, and gates.
