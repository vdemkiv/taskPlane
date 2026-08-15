# Harness rules — submit / gate / human checkpoint (canonical)

This file is the SINGLE canonical statement of the invariants every
taskplane flow runs under. Skills summarize them in one line and point
here; the engine enforces them mechanically. If a skill and this file ever
disagree, this file wins — fix the skill.

1. **Workers submit and stop.** The design, execute, fix, evaluate, and
   engineering workers perform their contracted role, write their required
   evidence, run `tp loop submit pass|fail` (or evaluator-only
   `tp loop submit unavailable` for a structured host/model outage with
   green bound tests and no product/lens defect; with `--task <id>` only in a
   parallel EXECUTE wave — outside one the engine rejects any `--task`
   that is not the current task — after committing in their worktree),
   and STOP. A submission records an engine-computed source and
   evidence-artifact fingerprint; it never advances or clears anything.
   Product and planner roles return their artifacts to the orchestrator,
   whose plan gate is mechanical and still precedes explicit human
   approval.

2. **Only the orchestrator gates.** The orchestrator recomputes the
   submission fingerprint and calls the matching `tp loop gate`. A worker
   never calls `loop gate` on its own work — the builder must never accept
   its own completion claim.

3. **The engine judges evidence, not prose.** Whether DoR/DoD evidence is
   sufficient is decided by the engine's recomputed evidence, never by a
   worker's narrative. A missing, mismatched, or stale submission is
   rejected. Evaluator and engineering submissions bind the exact
   verdict/findings/report bytes as well as the source state.

4. **Human checkpoints are never self-approved.** Design Contract
   approval, plan approval, A/B selection, escalation resolution, and
   final sign-off stop for an explicit human decision. Returning frozen work
   to Plan uses `tp loop replan --by <human> --reason <why>`, archives the
   prior task snapshot, and requires fresh Plan approval; `tp loop approve`
   runs only on that explicit yes (in Claude Tag, with `--by` recording
   who). No phrasing of urgency changes this.

5. **Contracts are never self-cleared after submission.** After
   `loop submit`, a worker never clears or widens its own contract, never
   weakens a test to pass it, never silently widens scope, and never
   treats an incomplete action list as completion.

6. **Runtime evals guide; they do not demand a transcript.** Every
   `tp loop submit pass` mechanically runs the same checkpoint exposed by
   `tp loop guide` (`--task <id>` in a parallel EXECUTE wave). It checks
   deterministic workflow facts from the active contract, graph,
   ReviewKernel, and trace—never model wording. A recoverable drift prevents
   submission and returns one bounded correction; the corrected retry may
   continue. The same unresolved drift a second time blocks. `submit fail`
   remains available and never requires evidence that the worker is honestly
   reporting it could not produce. Historical model baselines are telemetry,
   not runtime or release gates.

These are not process preferences — they are the exact failure modes
taskplane exists to prevent, and the drift check in
`taskplane/tests/test_release_freshness.py` keeps this statement from
forking back into the skills.
