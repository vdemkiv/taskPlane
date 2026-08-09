# Plan — v3 Phase 2: graph decomposition + governed flows + onboarding overhaul + routed-review wiring

Anchored requirement: **R-0003** (primary). **R-0004**, **R-0005**, **R-0006**
ship in the same release (human directive: implement all four streams).
Approved Design Contract: `design/contract.json` (evidence fingerprint
`d058ce55…`, verified current against the loop state), selected approach
**A** — hybrid decomposition layer in `graph.json` (new
`taskplane/decompose.py`), one workflow file per stage
(`workflows/{execute,evaluate,fix}-wave.js`) behind the existing
`workflow_available()` gate with the MANDATORY byte-identical Task-dispatch
fallback, README install decision tree by account type, and the R-0006
wire/discount/measure/extract debt burn-down (D-0002/D-0003/D-0004).

## Task table

| id | title (short) | req | deps | scope owner files |
|----|---------------|-----|------|-------------------|
| t1 | Decomposition engine + components layer + `--decompose` CLI + fingerprint cache | R-0003 | — | `taskplane/decompose.py` (new), `taskplane/depgraph.py`, `taskplane/tp.py`, `taskplane/tests/test_decompose.py`, `taskplane/tests/fixtures/decompose/**` |
| t2 | Route v2 component assembly + fail-open superset + `component_attribution` + dashboard component layer | R-0003 | t1 | `taskplane/lens.py`, `taskplane/depgraph.py`, `taskplane/dashboard.py`, `taskplane/tests/test_decompose.py`, `taskplane/tests/fixtures/decompose/**` |
| t3 | Three stage workflow files + static determinism/gate-verb pins | R-0004 | — | `workflows/execute-wave.js`, `workflows/evaluate-wave.js`, `workflows/fix-wave.js`, `taskplane/tests/test_stage_waves.py` |
| t4 | tp.py stage emitter + kill-switch matrix + resume fixture + adversarial gate walk + extended parity goldens + CI legs | R-0004 | t3 | `taskplane/tp.py`, `taskplane/tests/test_stage_waves.py`, `taskplane/tests/fixtures/briefs/**`, `.github/workflows/ci.yml` |
| t5 | Audit extraction, corpus captured BEFORE the move (D-0004) | R-0006 | — | `taskplane/audit.py` (new), `taskplane/loop.py`, `taskplane/tests/test_audit_extraction.py`, `taskplane/tests/fixtures/audit/**` |
| t6 | Fixtures-path discount (D-0002) + goldens regen ONCE via regen.py + routed-audit measurement & D-record (D-0003) | R-0006 | t2, t4, t5 | `taskplane/lens_signals.py`, `taskplane/tests/test_lens_signals*.py`, `taskplane/tests/test_debt_burndown.py`, `taskplane/tests/fixtures/detectors/**`, `taskplane/tests/fixtures/briefs/**` |
| t7 | Evaluate consumes routed briefs (stage='build'); em surface pinned untouched | R-0006 | t2, t5 | `taskplane/loop.py`, `taskplane/tests/test_evaluate_routing.py` |
| t8 | Install truth: README decision tree + quickstarts + `tp onboard` truth-up + CI grep/link legs (separable) | R-0005 | — | `README.md`, `taskplane/tp.py`, `taskplane/tests/test_onboarding_docs.py`, `.github/workflows/ci.yml`, `.claude-plugin/*.json`, `.codex-plugin/plugin.json` |
| t9 | v3 feature docs + what's-new 3-row pin + docs-drift CI extension | R-0005 | t1–t4, t6, t7, t8 | `README.md`, `docs/*.md`, `taskplane/tests/test_onboarding_docs.py`, `.github/workflows/ci.yml` |

Every task carries the full acceptance list of its anchored requirement as its
DoD criteria (the engine merges the R-record's acceptance at the gate), plus
the task-level criteria in `tasks.json` naming the exact design
acceptance-to-validation rows it realizes. Every enforcement-touching task
(all of t1–t7, plus the docs-side rule on t8/t9) carries **"no guardrail
loosened; guardrail battery green"** explicitly.

## Dependency diagram and waves

```
Wave 1 (scope-disjoint, parallel):   t1 (decompose engine)   t3 (stage wf files)   t5 (audit extraction)   t8 (install truth)
                                      |                        |                     |  \                     .
Wave 2:                              t2 (route consumption)   t4 (emitter+goldens)   |   \                    .
                                      |          \             /  |                  |    \                   .
Wave 3 (parallel):                    |           t6 (discount + regen + D-0003) <---+     t7 (evaluate wiring) <— t2, t5
                                      |          /             |                           |
Wave 4:                              t9 (feature docs) <— t1..t4, t6, t7, t8
```

- **Wave 1**: t1, t3, t5, t8 — pairwise scope-disjoint except that t1 and t8
  both touch `taskplane/tp.py` (t1: the `graph scan --decompose` flag; t8: the
  `tp onboard` truth-up). They carry **no logical dependency** — the overlap
  merely serializes their execution slots (t8 is never *blocked by the
  success* of any feature task, honoring the spec's "install truth must not
  be blocked if the feature streams slip"). t3 and t5 run fully parallel.
- **Wave 2**: t2 (needs t1's layer), t4 (needs t3's files; also serializes
  with t1/t8 on `tp.py` and with t8 on `ci.yml` — no logical dep).
- **Wave 3**: t6 and t7 are scope-disjoint and run parallel. t6 waits on
  **every brief-shape-affecting task** (t2 attribution, t4 stage goldens,
  t5 relocation) so the parity goldens are regenerated **exactly once**, at
  the end, via the documented `regen.py`. t7 waits on t2 (component assembly
  feeds `stage='build'` routing) and t5 (sole other owner of `loop.py`;
  extraction differential must be frozen before evaluate wiring lands).
- **Wave 4**: t9 documents shipped behavior, so it cites t1–t4, t6, t7 and
  extends t8's CI/docs surface.

### The two design sequencing constraints, honored mechanically

1. **Audit extraction corpus BEFORE the move** — t5 has `deps: []` and is the
   only wave-1 owner of `taskplane/loop.py`; its first criterion requires the
   differential corpus captured from the *unmodified* loop.py before any
   extraction edit. t7, the only other task that rewrites loop.py, declares
   `deps: [t5]`. Nothing can touch the audit region before the corpus is
   frozen.
2. **Goldens regenerated exactly once** — only t6's scope includes the regen
   output surface for a *shape-changing* regen, and t6 depends on t2, t4 and
   t5. t4 *adds* new stage-wave golden files (additive, via the same
   documented path) but does not regenerate the existing review goldens; the
   single behavior-driven regen (discount) happens in t6, last, reviewed as
   its own diff.

## Per-task rationale (riskiest first)

- **t2 — the killer risk lives here** (silent narrowing via bad clusters).
  The fail-open superset test, floors-on-real-diff-ctx rule,
  unevidenced-n/a refusal, and byte-identical-when-absent guarantee are all
  pinned in this task; the em audit backstop (untouched) auto-files any
  escape as a blocking router regression. The component path only ever
  narrows *candidates*, never floors, and only when every changed file maps.
- **t5 — extraction blast radius.** Byte-frozen differential (outcomes AND
  artifact bytes) captured pre-move inside the same task, pure move + thin
  re-exports, revert = one commit. Running it in wave 1 keeps the corpus
  honest and unblocks t6/t7.
- **t4 — parity drift, tripled surface.** One payload, two rails,
  wrap-not-rewrite; goldens + Codex CI leg + adversarial
  every-gate-without-workflows walk make the dispatch rail provably complete
  and byte-identical. Emitter stays in tp.py so the workflow-agnostic pin on
  loop.py/lens.py (extended to audit.py in t5) never weakens.
- **t6 — goldens churn can mask a routing change.** Regen only via regen.py
  with provenance banner, once, after all shape changes, as its own reviewed
  diff; CI parity leg must be green post-regen. The D-0003 measurement is
  recorded as a registry decision against the ≥30%/zero-escape bar (default
  DECLINE) — audit execution does not change this phase.
- **t1 — granularity floor mis-set.** Floor values are named constants with
  a `components.yaml` override; the dashboard ≥3-components test guards
  under-decomposition, a component-count ceiling assertion (O(tens)) guards
  explosion; first dogfood decomposition is reviewed before component-routed
  reviews are relied on (rollout note).
- **t7 — enforcement-adjacent wiring.** Adds routing at evaluate without
  touching the em literal, cadence, or the router-regression block; pins all
  three.
- **t3 — schema fidelity.** Per-stage static schemas (receipts vs
  findings-v2) keep the shipped static-scan test pattern intact; gate verbs
  scanned to zero.
- **t8 — the observed launch failure.** Fixes the README dead end for org
  members with CI-pinned truth; deliberately dependency-free so a feature
  slip cannot re-ship the broken install story.
- **t9 — docs drift.** Feature docs land with the features they document;
  drift fails CI, not readers.

## Total scope union (sign-off DoD checks against this)

```
taskplane/decompose.py            (new)      taskplane/depgraph.py
taskplane/audit.py                (new)      taskplane/lens.py
taskplane/lens_signals.py                    taskplane/loop.py
taskplane/tp.py                              taskplane/dashboard.py
workflows/execute-wave.js         (new)      workflows/evaluate-wave.js  (new)
workflows/fix-wave.js             (new)
taskplane/tests/test_decompose.py       (new)
taskplane/tests/test_stage_waves.py     (new)
taskplane/tests/test_audit_extraction.py(new)
taskplane/tests/test_evaluate_routing.py(new)
taskplane/tests/test_onboarding_docs.py (new)
taskplane/tests/test_debt_burndown.py   (new)
taskplane/tests/test_lens_signals.py         taskplane/tests/test_lens_signals_fixtures.py
taskplane/tests/fixtures/decompose/**  (new)  taskplane/tests/fixtures/audit/**  (new)
taskplane/tests/fixtures/detectors/**         taskplane/tests/fixtures/briefs/**
README.md    docs/*.md    .github/workflows/ci.yml
.claude-plugin/marketplace.json   .claude-plugin/plugin.json   .codex-plugin/plugin.json
```

Out of scope (unchanged from the spec fence, enforced by the hook):
`taskplane/kb.py`, `taskplane/requirements.py`, `taskplane/design_contract.py`,
`taskplane/regression.py`, lens catalog entries (stays at 26), hook/screen
enforcement semantics, `PRIVACY`/legal copy. Full-suite DoD:
`python3 -m unittest discover -s taskplane/tests -q` — no-loosening battery,
parity goldens, Codex fixture leg green; suite count only goes up from 954.

## Design conformance

- **Modules**: plan scope union covers all five proposed modules
  (`taskplane`, `workflows`, `taskplane/tests`, `docs`, `.github/workflows`);
  no new *graph modules* are proposed (decompose.py/audit.py are files inside
  `taskplane`; `(root)`/`.claude-plugin`/`.codex-plugin` are declared
  `new_modules` where scope touches them).
- **Contracts**: all four named contracts are carried by tasks —
  `contract:component-map` (t1, t2), `contract:lens-brief` (t2, t3, t4, t6,
  t7), `contract:wave-workflow` (t3, t4), `contract:findings-v2` (t3, t4,
  t5, t7).
- **Edges**: all 12 proposed design edges appear canonically in task
  `design_edges` (verified mechanically against `edge_key`).
- **Depth policy**: every task inherits the design's typed policy
  (local 2 / contract-only / contract 1 / requirement 1) — aggregate equals
  the approved policy, never narrower.
- **Acceptance**: all 7 anchored R-0003 rows map to t1 (rows 1–3) and t2
  (rows 4–7); all 7 R-0004 rows to t3/t4; all 6 R-0005 rows to t8 (1, 2, 4,
  6) and t9 (3, 5); all 7 R-0006 rows to t5 (5, 7-extraction), t6 (2, 3, 4,
  6) and t7 (1, 7-evaluate). Row 6 of R-0006 (debt records resolved) flips
  at sign-off per the design.

## Risks not owned by a single task

- **Workflow runtime variance** (host versions, org kill-switches, journal
  behavior): conservative detection reused verbatim; fallback always wired;
  resume fixture-tested against a frozen journal shape, not live runtime.
- **tp.py contention** (t1/t4/t8 all touch it): accepted — the three edits
  are in disjoint regions (graph CLI, emitter, onboard) and the wave
  scheduler serializes the slots; splitting tp.py is out of scope this phase.
- **Strategy note**: none — no direction question surfaced while planning; the
  four streams execute the approved v3 strategy doc as designed. (If the
  human wants a direction check, summon /tp-northstar.)
