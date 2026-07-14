
# Definition of Ready & Definition of Done

## Overview

Every step of the Evaluate-Loop is a gate. Work flows through it only when two questions are answered with evidence:

- **Definition of Ready (DoR)** — the **entry gate**. *Is the input good enough to start this step?* Judged through engineering lenses from the catalog: code-quality, security, integrability, scalability, testability, sre (reliability & observability), and data-safety.
- **Definition of Done (DoD)** — the **exit gate**. *Is the output actually the feature that was asked for?* Judged through the feature/requirements lens: acceptance criteria, functional correctness, user-journey completeness, scope fidelity, and verified evidence.

**Core principle:** DoR protects *how* the work is built. DoD protects *what* was delivered. A step is not finished until its DoD passes; a step may not begin until its DoR passes.

**Relationship to existing skills:** This skill is the contract; the `eval-*` skills are the test harness that proves the contract. DoR/DoD say *what* must hold at each gate; `eval-code-quality`, `eval-business-logic`, `eval-integration`, and `eval-ui-ux` say *how* to verify it. Completion claims at any gate are subject to `verification-before-completion` — evidence before assertion, always.

## The Two Lenses

### Definition of Ready — engineering perspectives (entry gate)

| Perspective | The question it answers | Ready when… |
|---|---|---|
| **Code quality** | Is the input clean enough to build on — and can the next person change it safely? | Builds/type-checks green at the boundary; no `any`, dead code, or unresolved TODOs being inherited; naming and module boundaries follow codebase conventions; scope is single-responsibility and bite-sized; no speculative generality (YAGNI) |
| **Security** | Can this proceed without opening a hole? | No secrets in source or config; authn/authz path is defined for any new surface; inputs that cross a trust boundary have a validation owner; threat surface is named, not assumed |
| **Integrability** | Will this fit the systems around it? | API/data contracts are explicit (request/response shapes, error codes); upstream/downstream dependencies are available or stubbed; side effects on other tracks/components are identified |
| **Scalability & performance** | Will it hold under real load and data growth? | Expected volume/throughput stated; N+1 / unbounded-query / unindexed-FK risks named; no synchronous work that should be async; resource ceilings considered |
| **Testability** | Can we prove it works? | Acceptance criteria are observable/measurable; test strategy and target coverage are agreed (per `qa-lead`); fixtures/seed data available |
| **SRE (reliability & observability)** | Will we know when it breaks? | Failure modes have a logging/error-surfacing plan; no silent catch-and-swallow planned; user-facing errors have an owner |
| **Data & migration safety** | Can this ship without corrupting state? | Schema changes are additive or have a migration + rollback; new columns nullable or defaulted; cascade rules explicit; backfill strategy defined |

### Definition of Done — feature perspective (exit gate)

| Perspective | Done when… |
|---|---|
| **Requirements coverage** | Every acceptance criterion in `spec.md` maps to delivered, verified behavior — checked line-by-line, not inferred from "tests pass" |
| **Functional correctness** | Happy path, declared edge cases, and error/empty/loading states all behave per spec |
| **User-journey completeness** | The end-to-end journey the feature serves completes without dead ends or manual workarounds |
| **Scope fidelity** | Nothing in scope is missing; nothing out of scope was added (no creep). Deviations are logged and approved per the authority matrix |
| **Evidence of verification** | Build, type-check, and full test suite were run *in this cycle* and their output confirms the claim (`verification-before-completion`) |
| **Documentation & traceability** | Decision log, business docs (`business-docs-sync`), and any ADRs are updated; breaking changes documented |

## Gates Per Loop Step

The loop is `1. PLAN → 2. EVALUATE PLAN → 3. EXECUTE → 4. EVALUATE EXECUTION → 5. FIX`. Each step below lists what must be *ready* to enter (DoR) and what must be *done* to exit (DoD), the level it operates at, and the skill that enforces it.

### Step 1 — PLAN  ·  level: track → plan

**DoR (enter planning when):**
- `spec.md` exists and states acceptance criteria, not just a goal.
- The goal is unambiguous, or its interpretations are listed for `product-lead`.
- No conflicting/overlapping active track in `tracks.md`.
- Non-functional constraints are known and written down: target stack, security posture, expected scale, budget ceiling (see authority-matrix cost rows).

**DoD (planning is done when):**
- Every `spec.md` acceptance criterion maps to at least one task (traceability).
- Tasks are bite-sized and each names its own acceptance check *and* which evaluator will judge it (`eval-code-quality` / `-business-logic` / `-integration` / `-ui-ux`).
- The dependency DAG is acyclic and parallelizable.
- Non-functional work is represented as explicit tasks or constraints, not assumed — at minimum security, scalability, and data-migration items where relevant.
- Risks are flagged for attention.

**Enforced by:** `writing-plans` → checkpoint `PLAN: PASSED`.

### Step 2 — EVALUATE PLAN  ·  level: plan

**DoR (enter when):** `plan.md` exists; `tracks.md` is readable for overlap analysis.

**DoD (done when):**
- Technical feasibility confirmed and scope is disciplined (no creep introduced during planning).
- Task granularity meets the bite-sized standard.
- Cross-track conflicts are cleared.
- The DoR engineering perspectives are *represented in the plan*: a reviewer can point to where security, integrability, scalability, and data-safety are handled. Architecture-heavy plans also clear `cto-plan-reviewer`.
- Verdict is `PASS` (else return to Step 1 with feedback).

**Enforced by:** `loop-plan-evaluator` (+ `cto-plan-reviewer` for architecture/integration/infra plans).

### Step 3 — EXECUTE  ·  level: task → code

DoR/DoD here apply **per task**, evaluated at the commit boundary.

**Task DoR (a task is ready to pick up when):**
- All upstream DAG dependencies are `PASSED`.
- The task's acceptance criteria and interface/contract are explicit.
- Test strategy for the task is known (TDD where applicable, per `test-driven-development`).
- Required fixtures, env vars, or stubs are available.

**Task DoD (a task/commit is done when):**
- Build and type-check exit 0; no new `any`, dead code, or stray `console.log`.
- Tests were written and pass (red→green verified, not assumed).
- Naming, file layout, and module boundaries match codebase conventions.
- No secrets committed; inputs crossing trust boundaries are validated; errors are surfaced, not swallowed.
- State mutations have matching persistence + rollback where the codebase requires it.
- Checkpoint written with commit SHA per `checkpoint-protocol`.

**Enforced by:** `executing-plans` / `task-worker`, with `requesting-code-review` after each task and `verification-before-completion` before any "task complete" claim.

### Step 4 — EVALUATE EXECUTION  ·  level: feature → track  (the quality gate)

**DoR (enter when):** implementation commits exist; the build runs and the test suite is executable.

**DoD (done when):** all dispatched evaluators return PASS and the consolidated `evaluation-report.md` is PASS —
- `eval-code-quality` — quality, style, naming, types, error handling, dead code, coverage.
- `eval-business-logic` — **every acceptance criterion from `spec.md` verified** (the feature DoD core).
- `eval-integration` — contracts, auth, persistence/schema hygiene, secrets, error recovery (the security + integrability + data DoR core, proven).
- `eval-ui-ux` — accessibility and UX, where there is a UI.

If any evaluator fails → Step 5.

**Enforced by:** `loop-execution-evaluator`.

### Step 5 — FIX  ·  level: code

**DoR (enter when):** `evaluation-report.md` lists concrete, reproducible failures with enough detail to root-cause.

**DoD (done when):**
- Each listed failure is fixed *and re-verified* with fresh evidence.
- A regression guard exists for any bug that escaped to evaluation.
- `fix_cycle_count` incremented; control returns to Step 4 for re-evaluation.

**Enforced by:** `systematic-debugging`. After `max_fix_cycles`, unresolved items are escalated (Board in agentic mode) or surfaced to the user (human-in-the-loop mode) — they are never silently dropped.

## Level Roll-Up (quick reference)

| Level | DoR — ready to start (engineering lens) | DoD — done (feature lens) |
|---|---|---|
| **Goal / track** | Spec with acceptance criteria; constraints (stack, security, scale, budget) written; no track overlap | All steps PASSED or `completed-with-warnings` logged; evaluation report PASS; decision log + business docs synced; merged |
| **Plan** | Spec analyzed; NFRs identified | Full requirement→task traceability; NFR tasks present; DAG valid; reviewer PASS |
| **Task** | Dependencies done; contract + acceptance + test strategy clear | Builds, tests pass, conventions met, no secrets, checkpoint written |
| **Code / commit** | Interfaces defined; trust boundaries identified | Diff reviewed (`requesting-code-review`); verified with command output, not claims |

## Escalation Hooks (tie to the Authority Matrix)

A failed DoR/DoD is not always a local fix — some route to a higher authority. Do not wave these through to keep the loop moving:

- **Security DoR fails** (exposed secret, missing authz, unvalidated trust boundary) → HIGH_IMPACT; CSO leads Board review. Never auto-skip a security gate.
- **Prompt-injection guard missing on a model-feeding input** (any field/API whose data can reach an LLM lacks the detect→obstruct→flag guard or structural separation) → security DoR fails; see the `security` lens (`../../tp-engineering/references/security.md`). Plan it before EXECUTE, don't retrofit at evaluation.
- **Coverage below the minimum threshold** or **skipping business-logic/security tests** → HIGH_IMPACT (Board), not a `qa-lead` discretionary call.
- **Breaking schema change or breaking public API** surfaced at a DoR check → HIGH_IMPACT (Board); additive schema is `architecture-lead` (LEAD_CONSULT).
- **Scope gap or creep** found at DoD → adding/removing spec items is HIGH_IMPACT; pure interpretation is `product-lead`.
- **New runtime dependency >50KB or major upgrade** needed to satisfy a DoR → HIGH_IMPACT; under threshold is `tech-lead`.

Every gate decision — pass, fail, or escalation — is logged to the track's `metadata.json` for audit.

## Anti-Patterns

| Anti-pattern | Why it fails the gate |
|---|---|
| "Tests pass, so it's done." | Tests passing ≠ requirements met. DoD requires line-by-line acceptance-criteria verification. |
| Treating DoD as the only gate | Skipping DoR ships insecure/unintegrable/unscalable work that *technically* meets the feature spec. Both gates are mandatory. |
| Counting non-functional work as "extra" | Security, scale, and migration safety are DoR criteria, not nice-to-haves. If they're absent from the plan, Step 2 fails. |
| Claiming a gate passed without running the check | Violates `verification-before-completion`. No fresh command output = no claim. |
| Auto-skipping a failed security or coverage gate to keep the loop moving | These are HIGH_IMPACT escalations, not orchestrator discretion. |
