# Native Codex subagent dispatch

Use this procedure whenever a taskplane action or lens brief is executed on
Codex. The CLI has already decided the role, contract, model tier, reasoning
effort, and evidence obligations. Codex supplies the transport; it does not
reinterpret those decisions.

## One bounded startup, one exact task

`loop next` emits exactly `schema`, `stage_runtime_dispatch`, and `obligations`.
The driver uses `obligations` to launch the worker. The delegated message contains
only the unchanged `stage_runtime_dispatch`, the standalone `role_marker`,
the exact `contract_bootstrap.environment`, and this fixed operational instruction:
"Read the supplied JSON once using `python3 .taskplane/codex-hook.py stage read-input
--request - --workspace .`, with that JSON as stdin. Batch independent input reads.
Do not ask the parent how to read artifacts or send progress messages."
Host roots belong only in that
environment. Never forward the full action, a prior role brief, a conversation,
ambient knowledge, or an unrelated Design.

1. Use the exact `task_name`, model and `reasoning_effort` in `obligations`.
   Set `fork_turns="none"` explicitly. Omit a null model so the host inherits it.
2. The worker verifies the startup and reads its pinned phase input with
   `$TP stage read-input --request -`, supplying the envelope as JSON on stdin.
   The engine verifies its size, digest, authority and committed input reference.
   The input declares the phase skill and typed artifact references. Read only
   those inputs and files permitted by the scoped contract.
3. Independent wave entries use the same envelope and verification protocol.
   Each write-capable worker uses its own registered checkout and contract slot.
4. Follow the emitted wait policy for the outstanding set. Collect every result
   before asking for an orchestrator gate. A faster worker does not cancel another.
   Do not poll status/list agents or send progress requests. A host timeout is
   not progress and does not justify another review or a new phase attempt.
   `stage collect-lenses` returns the full collection in `report`; consume it
   directly without another artifact read. Reuse completed, unchanged leases.
5. A bounded correction preserves the current scope and attempt identity. If a
   worker cannot continue, retain its evidence and use an attributable stage
   close/discard operation. Do not infer completion from interruption.

`SubagentStart` binds the pending slot to the worker. `SubagentStop` records its
actual terminal outcome and releases the slot. These observations do not grant
human approval. The driver alone requests the declared gate.

Standalone Review has its own scoped brief protocol; it does not replace phase
startup or inject a lens route into Evaluate or Engineering.

## Sealed phase continuation

For the active phase runtime, `taskplane/loop.py` owns
`phase_evaluator_request` and `continue_phase_result`. Evaluation lenses come
from the admitted registry; the agent's working lenses are omitted. The loop
requires the current signed runtime result and canonical review, applies the
declared gate, requests knowledge compare-and-swap through the incumbent owner,
commits the result, and checks telemetry readiness before selecting declared
edges. Trusted authority, gate, knowledge, and artifact ports are host
capabilities; never put them in a worker package or reconstruct them from a
role label. `taskplane/tp.py:phase_continuation_output` and the loop's
`require_phase_continuation` revalidate committed evidence and current
authority at consumption. Missing evidence or authority holds progression;
knowledge conflicts and rejections remain visible while the accepted runtime
result is preserved. The run aggregate owns phase selection. Simulated host identity and unavailable usage keep their
original provenance in the output.

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
