# Authority matrix — who may decide what, and how it's enforced

taskplane's answer to "how much can the agents decide on their own?" Four
authority levels, from most to least autonomous. Unlike a convention-only
matrix, most rows here are **mechanically enforced** — the PreToolUse hook
and the loop's gates make over-reach impossible rather than just discouraged.

| Level | Who holds it | May decide | May NOT decide | Enforced by |
| --- | --- | --- | --- | --- |
| **AUTONOMOUS** | executor / fixer agents | anything *inside* the active contract: which in-scope files to edit, how to implement, when to run the declared tests | anything outside scope/tools/commands; changing its own contract; skipping the DoD | PreToolUse hook blocks out-of-contract actions before they run |
| **TECHNICAL** | lenses & the evaluator | verdicts *within each lens's charter*: pass/fail per acceptance criterion, finding severity, routing a fix cycle (≤ `max_fix_cycles`) | widening its charter (boundary disputes resolve by the catalog's "does NOT own" line); overriding another lens; deciding "done" | read-only contracts (write-allow `.eval/**` only); the loop owns the fail policy in one place |
| **VALIDATION** | the EM agent | what to *surface*: the synthesized multi-lens report, the requirements-vs-implementation comparison, what it recommends | nothing final — it never fixes, never dispatches fixes, never closes DoD; judgment is handed to the human | read-only contract (write-allow `.em-review/**`); sign-off only via human `loop approve` |
| **HUMAN** | you | plan approval (incl. forcing a BLOCKED refinement gate), EM sign-off, escalation resolution (`retry` / `skip` / `abort`), contract scope changes, anything irreversible | — | the loop pauses at `plan_approval`, `signoff`, `escalated`; nothing advances these steps but an explicit human command |

## Escalation paths (when a level runs out of authority)

- **Fix cycles exhausted** (task fails evaluate > `max_fix_cycles` times) →
  the loop *stops* at `escalated`; only a human `loop resolve` continues.
- **Refinement gate BLOCKED** (high-cost task under the refinement
  threshold) → `loop approve` refuses; a human either refines the
  requirement or overrides with `--force` (the override is traced).
- **CRITICAL/HIGH security finding** → the work must not pass its gate; the
  security lens verdict fails EVALUATE, and if fixes can't clear it the
  normal exhaustion path escalates to the human.
- **Two or more `cannot-verify` acceptance criteria** → treated as an
  under-refined requirement: the evaluator says so, and the right response
  is refinement (HUMAN + product lens), not another fix cycle.
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
contract activations, hook denials, gate outcomes, refinement scores, forced
approvals, escalation resolutions, and human sign-offs. The matrix is only
as honest as its audit log — taskplane writes the log mechanically.
