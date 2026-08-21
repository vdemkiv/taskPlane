# Remediation waves 1–5: compatibility, observability, dependency ratchet, and evaluation confidence

## Problem

Taskplane 2.17.14 can ship code that does not parse on declared Python
versions, can hide degraded dependency analysis and known CLI refusals, and
does not run its zero-token evaluation corpus in CI. Its largest import cycle
has continued to grow, while live-model evaluation has neither repeat sampling
nor fixtures and reporting that measure whether seeded incomplete work is
caught. These gaps allow a locally successful release to overstate both runtime
compatibility and governance confidence.

## Users and context

- Taskplane maintainers need a safe sequence that restores a trustworthy
  baseline before structural changes begin.
- Contributors need fast, deterministic CI failures for parse errors, graph
  degradation, cycle growth, and evaluation-corpus regressions.
- Operators need clean, actionable CLI refusals rather than raw tracebacks or
  silently degraded graph results.
- Release owners need the exact pushed commit to pass the supported Python and
  zero-token evaluation gates before it can be treated as releasable.
- People interpreting model-evaluation results need repeated samples,
  incomplete-work fixtures, graph impact, and a clearly labelled
  seeded-failure catch-rate rather than a generalized reliability claim.

The primary review input is `taskplane improvement register — rev 2 — 2.17.14
@ 1464432`, specifically B1–B4, P1–P2, S1–S2, S4, S7, and V1–V5. The human
has required quick-sweep lens evidence only for this work: no deep lens worker
may be dispatched or promoted, and any substantive quick finding still blocks
or returns the affected work for correction.

## In scope

The work is delivered in this order:

1. Establish a fresh governed baseline from current `main`, remove stale Plan
   projections from active authority, refresh the graph, and prove that prior
   committed Design artifacts and external knowledge evidence were not altered.
2. Correct B1 and B2, declare and enforce the supported CPython range, and add
   P2 fail-fast compilation/import checks before filesystem mutation or tests.
3. Surface B3 graph degradation and B4 known CLI errors cleanly, add the V1
   zero-token evaluation-corpus CI gate, and enforce P1: push only after these
   gates and the supported-version checks are green.
4. Add the S4/S7 import-cycle ratchet before removing the S1 and S2 dependency
   edges; report the resulting strongly connected components and prevent new
   modules from silently joining them.
5. Add V2 repeated model-evaluation sampling, V3 incomplete-work fixtures, V4
   graph edges for evaluation corpora, and V5 seeded-failure catch-rate
   reporting with sample size and model identity.

Across all five waves, governed review uses quick sweeps only. There is no
quick-to-deep promotion path for this requirement.

## Out of scope

- Package moves or renames, including S3 and the proposed foundation/kernel/
  graph/knowledge/review/orchestration/render/CLI package layout.
- Broad component partitioning or impact-walk redesign (S5), ownership redesign
  of the orchestration core (S6), or arbitrary module-size targets.
- Schema consolidation or a schema registry (M1).
- Workspace/repository relocation identity (B5) and plugin install identity or
  stable bundle naming (B6).
- Model-tier observation or routing policy changes (M2).
- Attestation or merge-boundary trust roots (E1), MCP enforcement surfaces
  (E2), filesystem/OS isolation (E3), or coordination-cost taxonomy (E4).
- Any other sixth-wave architecture or security bet, including broad contract
  or persistence-schema redesign.
- Deep lens dispatch, deep-lens promotion, or treating absence of a deep pass as
  permission to ignore a substantive quick-sweep finding.
- Live-model spending in ordinary push/pull-request CI; scheduled or explicitly
  invoked live evaluation remains separate from the zero-token corpus gate.
- Claiming seeded-failure catch-rate as production defect rate, universal model
  reliability, or proof that unseeded failures cannot occur.
- Publishing, release tagging, marketplace installation, or destructive remote
  history changes beyond the explicit green-CI push boundary.

## Functional requirements

1. The governed baseline identifies one fresh run, current repository revision,
   current graph fingerprint, and preserved prior Design/knowledge evidence.
2. Every shipped Python and hook entry point either parses and runs on every
   documented supported CPython minor or refuses before mutation with one named,
   actionable compatibility error.
3. Graph scanning and downstream gates expose degradation structurally and make
   strict degradation a nonzero failure.
4. User-facing CLI commands translate known engine refusals into a clean
   headline and recovery action, retaining tracebacks only for an explicit debug
   path.
5. CI runs the zero-token evaluation corpus and fails on corpus regression before
   release or push completion is accepted.
6. A versioned import-cycle policy measures the real file-level graph, blocks
   bound growth, and lands before the declared dependency-edge cuts.
7. Live-model evaluation supports repeated trials, threshold comparison, and
   per-trial evidence keyed by scenario and model version.
8. The evaluation corpus includes seeded incomplete-work failures and participates
   in the dependency graph so relevant governance changes route impact to it.
9. Retro or dashboard output reports seeded-failure catch-rate, sample size,
   threshold, scenario set, and model identity without overstating the metric.
10. All applicable lens evidence for this requirement is produced by quick
    sweeps only; a substantive quick finding blocks or returns the work for
    correction without dispatching a deep worker.

## Acceptance criteria

1. **Fresh baseline and preservation.** A baseline verification records the
   current `main` revision, fresh governed run id, and refreshed graph
   fingerprint; confirms no stale `plan/**` payload or obsolete run pointer is
   active; and byte/fingerprint comparisons prove previously committed
   `design/**` and external knowledge evidence are unchanged.
2. **Supported Python execution.** CI proves all shipped Python modules and hook
   entry points compile, import, and execute representative version, entry,
   graph, status, and evaluation-corpus flows on CPython 3.10, 3.11, 3.12, and
   3.13; if any minor is intentionally unsupported, the same matrix proves it
   refuses nonzero before filesystem mutation and the documented support range
   matches.
3. **B1 regression closed.** A focused compatibility fixture compiles
   `taskplane/stage_entities.py` and imports every direct consumer on each
   supported minor, and the fixture fails against the 2.17.14 multiline
   f-string defect.
4. **B2 fails early and cleanly.** A deliberately unparseable stage dependency
   is detected at entry/startup rather than first lineage validation; an
   executable fixture asserts one named Taskplane compatibility error, no raw
   traceback in normal mode, and no created or changed run, graph, contract,
   review, or requirement state.
5. **P2 is ordered before tests.** Workflow inspection asserts that every
   supported Python matrix leg runs a repository-wide compile/import gate before
   its test step, and a seeded syntax error makes that leg fail without running
   the tests.
6. **B3 degradation is visible and gateable.** With one unparseable graph module,
   graph-scan JSON and text name the module and reason and mark `degraded=true`;
   the normal documented fail-open mode remains honest, while strict scan,
   Design/Plan readiness, and applicable Review/DoD fixtures refuse nonzero.
7. **B4 CLI errors are actionable.** A parameterized CLI fixture covers every
   known public engine-error class and asserts normal output contains a concise
   headline plus executable recovery action and no Python traceback; an
   explicit debug invocation retains diagnostic traceback access.
8. **V1 zero-token corpus gates CI.** The push/pull-request workflow invokes
   `scripts/ci_evals.py --corpus` without model credentials or network/model
   calls; an intentionally failing corpus fixture fails the job and a valid
   corpus passes deterministically.
9. **P1 green-CI push boundary.** The exact commit accepted as pushed has no
   local commits ahead of `origin/main`, and required checks for criteria 2–8
   are green for that same SHA; automation or a release record cannot describe
   an earlier local-only result as the pushed green result.
10. **S4/S7 ratchet precedes cuts.** Repository history and CI configuration
    prove the import-cycle measurement/bound is active before any S1/S2 edge cut;
    a fixture that adds a module to an existing cycle or exceeds a declared SCC
    bound fails and reports the affected modules, edges, and measured size.
11. **S1 graph edges are absent.** File-level import-graph assertions prove
    `taskplane/depgraph.py` and `taskplane/decompose.py` no longer import one
    another in either direction or scope, and shared graph data has one
    non-circular contract consumed by `depgraph`, `decompose`, and
    `lens_signals` without changing their externally observed payload meaning.
12. **S2 single-call edges are absent.** Static import assertions prove
    `taskplane/lens.py` has no import edge to `taskplane/review.py` and
    `taskplane/taskplane_lite.py` has no import edge to
    `taskplane/depgraph.py`; focused behavior fixtures prove the former call
    results remain available through explicit inputs or registered boundaries.
13. **Post-cut cycle evidence is honest.** The ratchet emits the complete SCC
    inventory before and after S1/S2, including the orchestration, lens, and
    collision/regression/review-evidence/stage-handoff/taskplane-lite cycles;
    checked-in expectations use measured current values rather than the stale
    2.17.14 estimates, and CI blocks any unapproved growth.
14. **V2 repeated sampling works.** An executable model-eval fixture runs ten
    deterministic fake trials with seven passes, persists ten distinct
    per-scenario/per-model-version results, reports pass rate `0.7`, and fails a
    `0.9` threshold; invalid repeat counts, thresholds, and missing trials fail
    with clean errors.
15. **V3 catches incomplete work.** The negative corpus contains at least three
    executable seeded cases: only three of five acceptance criteria completed,
    a silently ignored build error, and a test deleted instead of fixed; the
    harness refuses each case for the intended incomplete-work reason rather
    than a generic workflow-order failure.
16. **V4 corpus impact is connected.** Graph fixtures prove that a change to at
    least one governance/evaluation engine file lists the relevant `evals/*`
    corpus modules in impact and routes their validation, while unrelated
    product changes do not acquire a catch-all corpus blast radius.
17. **V5 reports the bounded claim.** Given a known mixture of caught and missed
    seeded failures, Retro or dashboard machine, Markdown, and text projections
    agree on the label `seeded-failure catch-rate`, numerator, denominator,
    sample size, threshold, scenario set, model id/version, and evaluation
    revision; zero-sample input reports unavailable rather than 0% or 100%.
18. **Quick-only review policy is enforced.** Every governed lens dispatch
    manifest for these waves contains only quick-sweep slots and no `deep.*`
    slot or promotion; a seeded substantive quick finding prevents the affected
    gate from passing and returns it for correction without creating a deep
    worker.

## Non-functional requirements

- `security`: Compatibility, graph, evaluation, and CLI failures cannot bypass
  existing authority checks, mutate governed state before refusal, weaken
  strict gates, fabricate evidence, or turn the quick-only policy into silent
  acceptance of findings.
- `architecture`: The import graph has one versioned measurement and ratchet;
  shared graph concepts have an acyclic ownership boundary; CI, CLI, graph,
  evaluation, Retro, and dashboard projections do not become competing sources
  of truth.
- `data-safety`: Baseline cleanup and refresh preserve prior committed Design
  artifacts, external knowledge, requirements, decisions, and audit evidence;
  failed compatibility, graph, or evaluation gates leave governed state
  unchanged and retain per-trial evidence.
- `sre`: Deterministic compilation, degradation, cycle, corpus, and fake-trial
  fixtures fail fast with actionable diagnostics; live sampling is bounded,
  resumable, and never required for ordinary zero-token CI.
- `integrability`: The supported-version contract, CLI error shape, graph
  degradation fields, evaluation trial records, and seeded-failure metrics are
  versioned and consistent across supported hosts, CI, status, Retro, and
  dashboard consumers.
- `cost-finops`: Push/pull-request evaluation is zero-token; live repeat count
  is explicit and bounded; retries do not duplicate accepted trials; reports
  expose sample count so added model spend is attributable.
- `privacy-compliance`: Persisted trial and catch-rate evidence contains only
  declared scenario/model metadata and bounded results; it excludes credentials,
  unrelated transcripts, hidden host configuration, and personal/private
  knowledge content.
- `accessibility`: CLI, status, Retro, and dashboard projections expose errors,
  degradation, thresholds, and catch-rate in semantic text independent of
  color, with machine-readable equivalents and complete labels.

## Contract handoff

- `scope_paths`:
  - `.github/workflows/ci.yml`
  - `taskplane/stage_entities.py`
  - `taskplane/run_store.py`
  - `taskplane/depgraph.py`
  - `taskplane/decompose.py`
  - `taskplane/lens.py`
  - `taskplane/lens_signals.py`
  - `taskplane/review.py`
  - `taskplane/taskplane_lite.py`
  - `taskplane/tp.py`
  - `taskplane/eval_*.py`
  - `taskplane/runtime_eval.py`
  - `taskplane/retro.py`
  - `taskplane/dashboard.py`
  - `taskplane/tests/**`
  - `scripts/ci_evals.py`
  - `scripts/eval_skills.py`
  - `scripts/eval_record.py`
  - `evals/**`
  - `components.yaml`
  - `docs/model-evaluation.md`
  - `docs/EVAL-DoR.md`
  - `docs/configuration.md`
- `out_of_scope`: package moves/renames; broad component, schema, orchestration,
  workspace identity, plugin identity, model-tier, attestation, MCP, persistence,
  or physical-isolation redesign; deep lens dispatch/promotion; ordinary-CI
  live model calls; release/tag/marketplace work; and all other sixth-wave bets.
- `dod.test_command`: `python3 -m pytest -q taskplane/tests && python3 scripts/ci_evals.py --corpus`
- dependencies: none
- contracts:
  - `contract:runtime.python-compatibility`
  - `contract:review.collection`
  - `contract:status.run-observability`
  - `contract:governance.enforcement-status`
  - `contract:governance.delivery-authority`
- `contract_relations`:
  - changes `contract:runtime.python-compatibility`
  - changes `contract:review.collection`
  - changes `contract:status.run-observability`
  - changes `contract:governance.enforcement-status`
  - changes `contract:governance.delivery-authority`
- context files:
  - `.github/workflows/ci.yml`
  - `taskplane/stage_entities.py`
  - `taskplane/run_store.py`
  - `taskplane/depgraph.py`
  - `taskplane/decompose.py`
  - `taskplane/lens.py`
  - `taskplane/lens_signals.py`
  - `taskplane/taskplane_lite.py`
  - `taskplane/tp.py`
  - `scripts/ci_evals.py`
  - `scripts/eval_skills.py`
  - `scripts/eval_record.py`
  - `evals/**`
  - `components.yaml`
  - `docs/model-evaluation.md`
  - `docs/EVAL-DoR.md`

This is a cross-module, contract-changing, sequencing-sensitive requirement.
It requires Design before Plan or Build. It has no blocking Product questions.
