# Retrospective: host-capability implementation and evaluation loop

Date: 2026-08-15
Scope: the overnight R-0006 implementation and the following model-evaluation/recovery session.

## Outcome

The product work was substantially complete before the final evaluation loop
started. The bound mechanical evidence was green (`280 passed`, `215
subtests`), the evaluator marked 10 acceptance criteria met, zero not-met, and
three cannot-verify. The remaining failure was a Codex child/model completion
failure: five review slots were requested, zero were canonically collected,
and one fresh backend attempt timed out.

taskPlane encoded that host failure as five product lens blockers and the loop
had only `pass` or `fail`. A `fail` therefore opened another product FIX cycle
even though no product defect had been identified. That failure-taxonomy bug,
not the feature implementation, created the endless loop.

The repair adds a third EVALUATE result: `unavailable`. It is accepted only
when bound mechanical tests are green, no acceptance criterion is `not-met`,
no completed lens reports a blocker, and the evaluator records a structured
host/transport reason. It advances with a visible warning, never increments
`fix_cycles`, and a mistaken `gate fail` is refused with the instruction to
use `gate unavailable`. T1 has now advanced to T2 with its warning preserved.

## What changed overnight

The observable implementation window ran from commit `57f599d` at 23:18 to
`4da11fc` at 09:27: 11 commits over roughly 10 hours. The accumulated delta
was 42 files, 5,354 insertions, and 1,072 deletions:

| Surface | Insertions | Deletions | Files |
| --- | ---: | ---: | ---: |
| Runtime/workflow/hook source | 2,645 | 215 | 17 |
| Tests and eval fixtures | 1,530 | 11 | 13 |
| Specifications, plans, design, docs, agents | 1,038 | 835 | 9 |
| Other | 141 | 11 | 3 |

The useful product outcomes were real: truthful host-capability state,
capability-aware model/effort routing, exactly-once hook selection, stop-time
submission enforcement, evaluator output schemas, token/telemetry
normalization, and Claude/Codex workflow parity. The evaluation layer also
caught real defects in bounded ReviewKernel views, duplicate suite execution,
reused Codex session binding, and schema propagation.

The cost was disproportionate because one task combined six host capabilities
and 13 acceptance criteria, then used taskPlane to evaluate taskPlane. Every
harness weakness became indistinguishable from a feature regression. Plan
scope was amended repeatedly, model receipts became prerequisites for judging
model receipts, and validation was rerun at multiple boundaries.

## What happened in the current session

1. The implementation commit was mechanically valid and its suite result was
   cited rather than rerun.
2. EVALUATE routed five lenses because the diff touched broad workflow,
   review, hook, and runtime surfaces.
3. Agent lifetime/concurrency and host provenance prevented collection. This
   was a harness-availability failure, not evidence of incorrect product
   behavior.
4. The strict verdict represented every uncollected lens as `fail/blockers=1`.
   That invented product blockers from missing evidence.
5. `loop submit fail` succeeded, and the old state machine was ready to spend
   another FIX cycle on unchanged product code.
6. The new `unavailable` boundary was implemented and checked once with 27
   focused tests. No broad suite or repeated model review was run.
7. The stale failure was reclassified honestly; T1 advanced with
   `evaluation.reason_code=agent_timeout`, while its fix-cycle count remained
   unchanged.

## What improved and what degraded

### Improved

- Mechanical evidence is reusable: the evaluator cited the existing suite
  instead of paying for it again.
- The dependency graph, output schema, host capability source, and producer
  identity are explicit rather than prompt-only conventions.
- Cross-host inconsistencies now have concrete fixtures and observable
  diagnostics.
- Real product findings remain capable of blocking the loop.

### Degraded

- Model evaluation drifted from a guide into a deterministic release oracle.
- Missing model evidence was converted into a product failure instead of an
  availability signal.
- Self-hosting created recursive authority: the ReviewKernel needed working
  ReviewKernel provenance in order to evaluate that provenance.
- Scope governance amplified small repairs into plan/contract amendments.
- Test execution became event-driven (after almost every correction) instead
  of risk-driven (after a coherent repair batch).
- Completion optimized for satisfying the harness, not for adding user value.

The evaluation layer is therefore net-positive only when it guides the model,
detects drift, and contributes independent findings. It is net-negative when
host nondeterminism is treated as proof that product code is wrong.

## Gates for future work

### Implemented now

1. **Separate outcome classes.** EVALUATE has `pass`, product `fail`, and
   host/model `unavailable`.
2. **No product fix without product evidence.** `unavailable` is refused if a
   criterion is not-met, a completed lens blocks, the build failed, or bound
   mechanical evidence is not green.
3. **No accidental loop.** A pure unavailable verdict submitted as `fail` is
   refused; it cannot consume a FIX cycle.
4. **Visible degradation.** The task and loop status retain the reason and a
   bounded detail for EM/human sign-off.
5. **One bounded validation batch.** This repair used one focused source/schema
   batch (27 tests), then the existing suite evidence was cited.

### Policy to enforce in the next harness slice

1. **Value-delta prerequisite.** Before any retry, record which product byte,
   evidence byte, or host condition changed. If none changed, stop; do not
   rerun.
2. **Proportional validation matrix.** Documentation/metadata gets static
   validation; one component change gets its focused selectors; an
   integration batch gets one radius run; the full suite runs once at release
   or CI, not after each edit.
3. **Model-eval budget.** One normal model evaluation and at most one retry
   only after a named condition changes. Exhaustion becomes `unavailable`,
   never an automatic product FIX.
4. **Evidence semantics.** An uncollected lens is `unavailable`, not
   `fail/blocker`. Only a canonical completed result can create a lens defect.
5. **Self-hosting circuit breaker.** A taskPlane transport/provenance failure
   discovered while evaluating an unrelated task is filed as harness debt and
   cannot expand that task's implementation scope.
6. **One canonical run.** EVALUATE must refuse to create a second active
   ReviewKernel run for the same task/fingerprint and must reuse or explicitly
   supersede the first.
7. **Efficiency telemetry.** Every task retro should record product files
   changed, test/eval runs, suite cache hits, model slots requested/completed,
   fix cycles, wall time, and the reason for every rerun.

## Decision

Model evals are navigational evidence, not unit tests. A completed model
finding with a reproducible product consequence may block. Model, transport,
agent-lifetime, or receipt unavailability must remain visible but cannot be
translated into a product defect. Correctness still comes from DoR/DoD,
dependency impact, scoped mechanical evidence, canonical completed findings,
and human sign-off; efficiency comes from paying for each evidence layer once.
