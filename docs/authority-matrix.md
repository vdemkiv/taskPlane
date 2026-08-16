# Authority matrix — who may decide what, and how it's enforced

taskplane's answer to "how much can the agents decide on their own?" Five
authority levels, from most to least autonomous. Unlike a convention-only
matrix, most rows here are **mechanically enforced** — the PreToolUse hook
and the loop's gates make over-reach impossible rather than just discouraged.

| Level | Who holds it | May decide | May NOT decide | Enforced by |
| --- | --- | --- | --- | --- |
| **DESIGN** | tp-designer | propose the technical HOW inside the refined requirement: compare approaches, select a recommendation, declare modules/edges/contracts/depth, Design DoR/DoD, validation, failure handling and rollout | changing product scope; editing product code; mutating the as-built graph; approving its own Design Contract | read-only contract with write-allow `design/**`; mechanical Design DoR/DoD; graph fingerprint; human `design_approval` |
| **AUTONOMOUS** | executor / fixer agents | anything *inside* the active contract: which in-scope files to edit, how to implement, when to run the declared tests | anything outside scope/tools/commands; changing its own contract; skipping the DoD | PreToolUse hook blocks out-of-contract actions before they run |
| **TECHNICAL** | lenses & the evaluator | verdicts *within each lens's charter*: pass/fail per acceptance criterion, finding severity, routing a fix cycle (≤ `max_fix_cycles`) | widening its charter (boundary disputes resolve by the catalog's "does NOT own" line); overriding another lens; deciding "done" | read-only contracts with a per-role write-allow: managed evaluators write only their exact external run-evidence path (legacy unmanaged workspaces use `.eval/**`); each leased lens slot receives one exact `<result_path>` in its `producer_contract.write_allow`; collection validates that sealed path, the lease identities, schema, lens coverage, and finding/verdict consistency. Host lifecycle receipts add attribution when available and any contradictory receipt fails, but missing host telemetry does not discard a valid leased artifact; the loop derives blockers from canonical findings and owns the fail policy in one place |
| **VALIDATION** | the EM agent | what to *surface*: the synthesized multi-lens report, the requirements-vs-implementation comparison, what it recommends | nothing final — it never fixes, never dispatches fixes, never closes DoD; judgment is handed to the human | read-only contract with exact leased external run/artifact paths; sign-off only via human `loop approve` |
| **CONTROL** | orchestrator | request an engine gate that matches a worker's submission; sequence steps and waves | doing role work; fabricating worker evidence; changing the submitted outcome; passing an engine-rejected gate; approving a human checkpoint | source + evaluator/EM artifact fingerprint, engine-owned DoR/DoD checks, and state machine |
| **HUMAN** | you | Design approval, plan approval (incl. forcing a BLOCKED refinement gate), A/B **selection** (which variant ships — `loop select`, never a plain approve), EM sign-off, escalation resolution (`retry` / `skip` / `abort`), contract scope changes, anything irreversible | — | the loop pauses at `design_approval`, `plan_approval`, `selection`, `signoff`, `escalated`; the terminal states `done` and `failed` are human-owned too (only a human starts new work from them); nothing advances any of these steps but an explicit human command. Attribution is asymmetric: `design_approval` hard-requires `--by`; `plan_approval` and `signoff` accept an anonymous approve but record it as `(unattributed)` with a warning and a `loop_approve_unattributed` trace event |

## Escalation paths (when a level runs out of authority)

- **Fix cycles exhausted** (task fails evaluate > `max_fix_cycles` times) →
  the loop *stops* at `escalated`; only a human `loop resolve` continues.
- **Refinement gate BLOCKED** (high-cost task under the refinement
  threshold) → `loop approve` refuses; a human either refines the
  requirement or overrides with `--force` (the override is traced).
- **CRITICAL/HIGH security finding** → the work must not pass its gate; the
  security lens verdict fails EVALUATE, and if fixes can't clear it the
  normal exhaustion path escalates to the human.
- **Approved Design drift** (evidence changes, Plan omits a designed
  module/contract/boundary, or implementation diverges) → the next gate fails;
  return to Design, update the contract, obtain a new human approval, and
  re-plan. Review cannot waive drift.
- **Two or more `cannot-verify` acceptance criteria** → treated as an
  under-refined requirement: the evaluator says so, and the right response
  is refinement (HUMAN + product lens), not another fix cycle.
- **A/B selection gate** (parallel variant builds reach `selection`) → the
  loop parks; `loop approve` is refused there — only an explicit human
  `loop select <variant|task-id|hybrid>` decides what ships. Unselected
  variants settle as `not_selected`/`reference`; they never merge on their
  own.
- **Boundary dispute between lenses** → resolved by the catalog charter, not
  negotiation; if the catalog is genuinely ambiguous, that's a human
  decision and a catalog edit.

## Parallel execution note

Authority is **per agent, per contract**. In a parallel wave every worker
holds AUTONOMOUS authority only inside its own task's contract, in its own
worktree — one worker cannot write another's scope even though they run at
the same time. Wave membership itself (which tasks may run concurrently) is
a loop decision: dependencies satisfied + pairwise-disjoint scopes.

## Audit

Every authority exercise leaves a trace event (`.taskplane/trace.jsonl`):
contract activations, hook denials, worker submissions and their workspace
fingerprints, gate outcomes, refinement scores, forced
approvals, escalation resolutions, and human sign-offs. The matrix is only
as honest as its audit log — taskplane writes the log mechanically.

Attribution in that log is per-gate, not uniform: `design_approval` cannot
be recorded without `--by` (the approve errors), while `plan_approval` and
`signoff` tolerate an omitted `--by` by recording the approver as
`(unattributed)`, emitting a warning, and tracing
`loop_approve_unattributed`. An auditor should treat `(unattributed)`
entries as detectable self-approval risk (this is exactly the signal the
Claude Tag protocol uses), not as a uniform attributed trail.
