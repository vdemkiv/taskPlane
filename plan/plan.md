# Plan — v3 Phase 3: engine correctness + routing precision + host portability + docs currency + hygiene

**Requirements:** R-0007 (anchored) + R-0008..R-0011 · **Design:** approved contract
`design/contract.json` (fingerprint `df3871a6…`, verified current against the loop state;
decision 0018 = A4 option c; decision 0019 = design approval). **Baseline:** HEAD `43253c2`.
26 backlog items, 29 acceptance criteria, six named contracts, zero new contract nodes,
zero new designed modules. Ten tasks, five waves.

Everything lands as one of the design's three moves: **refuse loudly**, **degrade to the
reference rail**, or **surface what was silent**. No guardrail loosens anywhere; every task
carries the guardrail-battery criterion.

---

## Task table

| id | items | req | deps | wave | owner files (engine surfaces) |
|----|-------|-----|------|------|-------------------------------|
| t1 | A1 A2 A3 A5 + B2 rule | R-0007 | — | 1 | loop.py, taskplane_lite.py, audit.py |
| t3 | A6 A7 C3 E5 | R-0007 | — | 1 | tp.py, decompose.py |
| t4 | B1 | R-0008 | — | 1 | requirements.py, fixtures/calibration |
| t7 | D1 D2 D3 D4 | R-0010 | — | 1 | skills/*, references/, freshness+onboarding tests |
| t2 | A4 (skew refusal) | R-0007 | t1, t3 | 2 | loop.py, tp.py, taskplane_lite.py |
| t5 | B3 B4 B5 + E3 E4 | R-0008 | t3 | 2 | decompose.py, lens.py, lens_signals.py, depgraph.py |
| t6 | C2 | R-0009 | t2 | 3 | taskplane_lite.py |
| t8 | D5 | R-0010 | t2, t7 | 3 | tp.py, docs/cli-reference.md, ci.yml |
| t9 | E1 E2 E6 | R-0011 | t4, t6, t8 | 4 | loop.py (comment), scripts/, ci.yml, tests sweep |
| t10 | C1 + goldens regen | R-0009 | t2, t5, t8, t9 | 5 | tp.py, fixtures/briefs/** |

## Waves and dependency diagram

```
wave 1            wave 2            wave 3            wave 4          wave 5
------            ------            ------            ------          ------
t1 engine gates ->+-> t2 A4 skew -->+-> t6 C2 deny --->+
   (loop, lite,   |   (loop, tp,    |   (lite)         |
    audit)        |    lite)        |                  |
                  |                 +-> t8 D5 CLI ref->+-> t9 E1/E2/E6 ->  t10 C1 +
t3 emitters ----->+                 |   (tp, docs,     |   (loop cmnt,     GOLDENS
   (tp, decompose)+-> t5 B3-B5,     |    ci.yml)       |    scripts,       REGEN
                  |   E3/E4 ------->+                  |    ci.yml,        (tp, briefs/**)
t4 B1 forecast    |   (decompose,                      |    tests sweep)   deps: t2,t5,t8,t9
   (requirements) |    lens, signals,                  |
                  |    depgraph)                       |
t7 D1-D4 skills ->+------------------------------------+
   (skills/**, references/, freshness+onboarding tests)
```

- **Wave 1** (4 parallel, scope-disjoint): t1, t3, t4, t7.
- **Wave 2** (2 parallel): t2 (deps t1, t3), t5 (deps t3).
- **Wave 3** (2 parallel, disjoint): t6 (deps t2 — taskplane_lite chain t1→t2→t6),
  t8 (deps t2 — tp.py chain; t7 — test_release_freshness.py chain).
- **Wave 4**: t9 (deps t4, t6, t8 — its suite-wide env sweep glob overlaps every test file,
  so it runs after all test-file owners; ci.yml after t8).
- **Wave 5**: t10 — the single goldens regeneration, last, after **every** brief-shape task.

**Single-ownership per wave, per engine file** (mechanically checked, no same-wave scope
overlap anywhere):
`loop.py`: t1(w1) → t2(w2) → t9(w4, comment only) · `tp.py`: t3(w1) → t2(w2) → t8(w3) →
t10(w5) · `taskplane_lite.py`: t1(w1) → t2(w2) → t6(w3) · `decompose.py`: t3(w1) → t5(w2) ·
`lens.py`/`lens_signals.py`/`depgraph.py`: t5 only · `audit.py`: t1 only ·
`requirements.py`: t4 only · `ci.yml`: t8(w3) → t9(w4).

## Lesson B2 — honored by this plan, then shipped by this plan

The t6∥t7 Phase 2 sequencing gap becomes an engine rule in t1 (`_plan_dor_errors`), and this
plan obeys it **now, manually**: the brief/dispatch-surface tasks (t3, t2, t5, t8 — tp.py;
t5 — lens.py + lens_signals.py) are all transitive dependencies of t10, the only task whose
scope touches `taskplane/tests/fixtures/briefs/**`. Goldens regenerate **exactly once**, at
the end, in t10, only via `regen.py` (provenance banner; hand-edited goldens are a finding).
Verified mechanically: brief-shape tasks {t2, t3, t5, t8} ⊆ ancestors(t10); no other task
touches the goldens. C1 — the phase's only sanctioned brief-byte change — deliberately lives
in t10 itself so the byte change and its regeneration land as one reviewed diff; t3's
emitter work (A6/C3/E5) changes only abnormal paths and is proven byte-unchanged against the
*existing* goldens (its tests include `test_dispatch_parity`).

## Per-task rationale (riskiest first)

- **t2 · A4 (the phase's named top risk)** — isolated in its own task, wave 2, so the only
  other loop.py owner (t1) has already landed and its tests are the differential base. The
  t7-replay criterion is pinned (skew submission refused with the merge-then-resubmit
  remedy + trace; post-merge the same submission path passes), and the equal-fingerprint
  case must be byte-identical — the comparison is a pure pre-check before
  `_evaluation_errors`, refuse-more-never-validate-less. Stamp + comparison land together.
  `taskplane_lite.py` is in scope because the fingerprint helper may live there and be
  re-exported as `tp.engine_fingerprint` (the design's edge evidence names both homes).
- **t1 · engine gate fixes** — A1/A2/A3/A5 are the four gate-side refusal/exclusion/leak
  fixes, plus B2's plan-gate ordering rule: B2 rides here (not in the A4 task, keeping t2
  minimal; not in t5, which would put a second loop.py owner into wave 2) because it is the
  same file, the same kind of change (a gate refusal), and the same test file as A1.
- **t3 · emitter fail-open + refusals** — A6/C3/E5 + A7: everything in tp.py's emitters that
  refuses or degrades WITHOUT changing well-formed bytes, plus decompose floor clamping.
  Keeping C1 out of t3 keeps every wave-1..4 gate green against existing goldens.
- **t5 · component-map precision** — B3/B4/B5 widen-or-restore only, plus E3/E4 (the
  consume-side rendering and docstring truth in the same files). Runs after t3's decompose
  changes; sole owner of lens.py/lens_signals.py/depgraph.py in the whole plan.
- **t4 · B1 recalibration** — corpus-pinned class weights; the no-under-warn negative corpus
  makes flattening (the risk strictly worse than the miss) mechanically impossible.
- **t6 · C2 deny widening** — one literal list entry + the deny AND plan-minted
  literal-override cases in one file (the anti-deadlock pair). Marked `model: cheap`:
  fully specified, mechanical, two tests whose text the design already dictates.
- **t7 · D1-D4 skills truth-up** — prose + freshness/pointer/drift tests; graph-invisible
  by design (skills/references are not code modules — declared honestly via `new_modules`
  and validated by named tests in taskplane/tests, per the design's DoR note).
- **t8 · D5 CLI reference** — `tp help --md`, committed reference, CI drift leg; burns the
  34-flag exemption to empty and makes the ratchet stricter. ci.yml as exact literal
  (plan-minted override of `.github/**`).
- **t9 · E1/E2/E6 hygiene** — floor script + manifest ratchet (convert nothing — the
  decision), env-mutation sweep (the `test_*.py` glob is as tight as a suite-wide sweep
  allows; that breadth is why t9 runs alone in wave 4 after every test-file owner), and the
  seam-comment fix with the real-seam late-binding test (loop.py comment-only).
- **t10 · C1 + single goldens regen** — see Lesson B2 above; also pins the union-screen
  fallback so the Windows form's failure mode is documented, not silent.

## Risks

1. **A4 touches the evaluate gate** — mitigations as designed: pure pre-check placement,
   pinned t7 replay, explicit equal-fingerprint byte-identity, existing evaluate-gate suites
   green unchanged, v2.3.1 regression gate on every increment. Dogfood note: once t2 lands,
   waves 3-5 of THIS plan become subject to skew refusals when tasks touch engine files —
   the remedy (merge first, resubmit) is the documented flow and validates the mechanism.
2. **B1 under-warns risky work** — no-under-warn corpus pinned; security/data-safety never
   discounted; discount only when the functional axis is complete.
3. **C2 scope-precedence deadlock** — components.yaml joins the default family, NOT the
   sacred family; the positive override test ships beside the deny test in the same file.
4. **Goldens churn masking an unintended byte change** — one regen, one task, regen.py only,
   CI parity + Codex legs green after; every other task proves byte-identity against the
   existing goldens.
5. **E1's manifest vs this phase's own new tests** — every new test file/class this phase
   adds must be unittest.TestCase-style (criterion on t7/t8/t9); the manifest only shrinks.
6. **Suite-wide env sweep breadth (t9)** — the glob is honest about a sweep's true surface;
   confined to wave 4, serialized behind all test-file owners, ahead of only t10 (disjoint
   files but overlapping glob — hence t10 depends on t9).

## Scope union (total surface)

`taskplane/{loop,tp,taskplane_lite,audit,decompose,lens,lens_signals,depgraph,requirements}.py` ·
`taskplane/tests/**` (named test files + conftest + fixtures/{calibration,decompose,detectors,briefs}) ·
`scripts/ci_unittest_floor.py` · `.github/workflows/ci.yml` (exact literal, twice, different
waves) · `docs/cli-reference.md` · `skills/{taskplane,tp-go,tp-status,tp-product,tp-tag,tp-northstar}/SKILL.md` ·
`references/harness-rules.md`. Out-of-scope fence respected: no `kb.py`, `design_contract.py`,
`regression.py`, lens catalog, workflow runtime semantics beyond the C3 error path.

## Design coverage — verified mechanically

Ran `design_contract.design_plan_errors` (the engine's plan-conformance helper) over this
task set with the loop state's design fingerprint, plus per-task
`depgraph.modules_for_scope` unknown-surface and `aggregate_impact_policy` checks:

- missing designed modules: **[]** (taskplane, taskplane/tests, docs, .github/workflows all
  covered) · missing contracts: **[]** (all six) · missing design edges: **[]** (all 13
  contract edges + the imports edge, copied canonically as `FROM->TO:KIND`)
- depth policy: aggregate = local 2 / contract-only / contract 1 / requirement 1 — equal to
  the approved design policy, no narrowing
- undeclared unknown modules: none (skills/* and references declared via `new_modules`;
  the design's DoR row 2 records why they are validated by tests, not graph nodes)
- acceptance coverage: all 29 rows map to task criteria —
  R-0007: A1/A2/A3/A5→t1, A4→t2, A6/A7→t3, stream-wide→all · R-0008: B1→t4, B2→t1,
  B3/B4/B5→t5, stream-wide→t5/t10 · R-0009: C1→t10, C2→t6, C3→t3 · R-0010: D1-D4→t7,
  D5→t8 · R-0011: E1/E2/E6→t9, E3/E4→t5, E5→t3, stream-wide→t9/t10
- B2 lesson conformance and same-wave scope disjointness: mechanically checked (see above).

Fix-cycle budget: max 2 (loop state). The plan goes to the human at the plan gate; the
orchestrator alone runs `loop gate`.
