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
   For review/evaluation briefs, the payload references a canonical review
   context and a scoped view. Pass those references and fingerprints unchanged;
   never paste the full diff/impact into the message or ask the child to run
   `git diff`, graph impact, routing, or runnability discovery again.
2. Call Codex's native `spawn_agent` with the brief's exact `task_name` and
   `reasoning_effort`. Pass `model` only when it is non-null; null means let
   the subagent inherit Codex's model choice. The human-facing taskplane role
   remains the payload's `role`/`agent` and must not be renamed.
3. Independent, scope-disjoint briefs may be spawned concurrently. Never give
   two write-capable agents the same checkout: use the worktree and contract
   slot emitted for a parallel build wave.
4. Use one event-driven wait per outstanding set. Prefer an unbounded native
   wait; when the host requires a timeout, use at least 1800 seconds and never
   less than 300 seconds for a spawned set. Reissue only after a completion or
   attention wake while obligations remain—never on a timer or scheduled
   polling cadence. Collect every final result before synthesis or an
   orchestrator gate; a fast result does not cancel a slower obligation.
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

Claude Dynamic Workflows remain an optional journaled transport. The portable
task payload is the mandatory reference and carries the same canonical context
and view fingerprints, contracts, routing decision, leases, provenance rules,
DoR/DoD gates, and artifact references. Claude and Codex may deliver or dispatch
those bytes differently; they may not derive different semantics.
