# Plan — v3 Phase 1: intelligent lens routing + workflow review wave

Anchored requirements: **R-0001** (primary), **R-0002** (depends_on R-0001).
Approved Design Contract: `design/contract.json` (fingerprint `18bb1c89`),
selected approach **A** — detector-per-lens applicability engine
(`taskplane/lens_signals.py`), stage profiles as catalog data, coverage
honesty in the dashboard, audit-sweep backstop, and the workflow review wave
(`workflows/review-wave.js`) with a MANDATORY byte-identical dispatch
fallback (Codex has no workflow runtime).

## Task table

| id | title | scope (owner files) | req | deps | proves |
|----|-------|---------------------|-----|------|--------|
| t1 | Applicability engine (detectors + fixtures) | `taskplane/lens_signals.py`, `taskplane/tests/test_lens_signals*.py`, `taskplane/tests/fixtures/detectors/**` | R-0001 | — | Deterministic per-lens verdicts with evidence; n/a impossible without negative evidence; budget/floors/cap ranker; <1s; registry covers all 26 catalog ids; every detector has positive+negative fixtures |
| t2 | Stage profiles + route() v2 + brief verdict fields | `taskplane/lens.py`, `lenses/catalog.json`, `lenses/_generate_catalog.py`, `docs/lens-catalog.md`, `taskplane/tests/test_lens_route_v2.py` | R-0001 | t1 | Router restricted to the active stage profile; legacy path byte-identical when `stage_profiles` absent; fail-open to breadth=all on engine failure; `--lens` force; briefs carry verdict/score/evidence additively |
| t3 | Coverage honesty (dashboard + HEADLINE) | `taskplane/dashboard.py`, `taskplane/tests/test_dashboard_coverage_v2.py` | R-0001 | t2 | All 26 lenses rendered deep/light/n-a-with-reason; dual-shape (v2 + legacy) rendering; pinned HEADLINE segment |
| t4 | Audit sweep + router-regression auto-filing | `taskplane/loop.py`, `taskplane/tests/test_audit_sweep.py` | R-0001 | t2 | Every-5th/release audit runs breadth=all; findings-vs-routing diff auto-files n/a-lens findings as class:regression owner:router; em/evaluate pass the stage to route v2 |
| t5 | Workflow review wave + capability detection + fallback | `workflows/**`, `taskplane/tp.py`, `taskplane/tests/test_review_wave.py` | R-0002 | t2 | review-wave.js consumes briefs via args, one agent() per deep brief with schema-pinned findings + verbatim prompts (TASKPLANE_TASK slots intact); capability probe; traced path choice; dispatch fallback byte-identical |
| t6 | CI parity + completeness legs | `.github/workflows/ci.yml`, `taskplane/tests/test_dispatch_parity.py`, `taskplane/tests/fixtures/briefs/**` | R-0002 | t1,t2,t5 | Frozen briefs replayed through the dispatch path byte-compare against goldens (Codex parity guard); detector-fixture completeness enforced as an explicit CI leg; workflow-vs-dispatch parity fixture |

## Dependency order / waves (loop runs --parallel; scopes are pairwise disjoint)

- **Wave 1:** t1 (the heart — no deps; stdlib-only engine + all fixtures)
- **Wave 2:** t2 (wires the engine into `route()`; defines the v2 shapes everything downstream consumes)
- **Wave 3:** t3, t4, t5 **in parallel** (disjoint scopes: dashboard.py / loop.py / tp.py+workflows)
- **Wave 4:** t6 (freezes briefs + goldens once t2's brief shape and t5's workflow path exist)

Riskiest first: t1 carries the detector false-negative surface (the killer
risk) and is deliberately first with the full fixture discipline; t2 carries
the legacy byte-identity pin.

## What each task does (detail)

### t1 — `taskplane/lens_signals.py` (new module, pure stdlib)
Detector interface `detect(ctx) -> Signal {lens, score 0..1, evidence[],
negative_evidence[]}` (negative_evidence non-empty iff score==0, enforced in
the Signal constructor — constructing an n/a without it raises). Registry
`DETECTORS` covering all 26 catalog ids (detector | "always" | ("inherit",
src)); drift test asserts registry keys == catalog ids. Three signal
sources: content (bounded scans — 64KB/file, 200 files, `re` only, sorted
iteration), graph (reuses `hub_signal` semantics + boundary contract nodes),
requirement (acceptance-criteria keywords). Verdict thresholds deep>=0.6,
light>=0.2. Budget ranker: target 5–7 deep, hard cap 8, overflow demoted to
light never dropped; floors applied post-ranking (security floor on
enforcement/boundary surfaces; architecture >= light on code). Tests:
per-detector positive+negative fixtures under
`taskplane/tests/fixtures/detectors/<lens>/{positive,negative}/`, a
completeness test (every registry id has both fixture dirs, >=1 case each),
determinism test (two identical runs byte-identical), perf test <1s per
component on the largest fixture, max-signal fixture yields exactly 8 deep
with the rest light/n-a (none absent), i18n negative fixture is taskPlane's
own repo surface with the exact negative-evidence string.

### t2 — stage profiles + route v2 (`taskplane/lens.py`, catalog)
`stage_profiles` top-level key added via `lenses/_generate_catalog.py`
(catalog.json is GENERATED — CI regenerates and diffs, so the generator is
the source of truth; `docs/lens-catalog.md` is in scope in case its
generator picks up the new key). `route()` gains `stage: str|None`; with
`stage_profiles` present + stage given, candidates = the profile and
verdicts come from `lens_signals`; `stage=None` or key absent = legacy path
byte-identical (existing `test_lens.py` passes unchanged — the regression
pin). Unknown stage falls open to the full catalog. Engine exception →
fall back to breadth=all + stderr warning + `lens_engine_failed` trace
(hub_signal precedent). `--lens` forces deep, recorded `deep (forced)`;
`--breadth all` sweep path untouched. `dispatch_briefs` extended additively
with verdict/score/evidence per brief (realizes
`taskplane->contract:lens-brief:provides`). Tests: design-stage route over a
code diff never yields code-quality; modified test catalog proves adding a
lens to a profile changes routing with zero code change; catalog drift test
pins profile ids to real lens ids; enforcement-touching diff can never route
security n/a; any code diff keeps architecture >= light; fail-open test;
forced-lens test; n/a-without-negative-evidence rejected at the route
boundary.

### t3 — coverage honesty (`taskplane/dashboard.py`)
`lens_coverage`/`render_lens_coverage` accept BOTH legacy
`{id: 'deep'|'sweep'}` and v2 `{id: {verdict, score,
evidence|negative_evidence}}` shapes; every n/a chip renders its reason
string; new pinned HEADLINE form `lenses N deep · N light · N n/a
(evidenced) of 26` alongside the legacy segment for legacy metas. Tests:
all-26-chips render test with a reason per n/a; HEADLINE format pinned;
legacy-shape compat test; existing dashboard suites pass unchanged.

### t4 — audit sweep (`taskplane/loop.py`)
em/evaluate wiring passes the stage to route v2; em keeps breadth=all on
audit reviews only (every Nth review, default 5, configurable, or any
release review), routed+light otherwise. Post-merge audit diff: any finding
from an n/a-routed lens auto-filed as `class: regression`, `owner: router`
into findings + KB, `router_regression_filed` trace, audit marker in
findings meta (`audit=true`) for token-baseline measurement. Test: a
deliberately-broken detector on a fixture repo auto-files the router
regression; cadence test; existing `test_loop.py` passes (breadth=all
mandate preserved on audit path).

### t5 — workflow wave (`workflows/review-wave.js` + `taskplane/tp.py`)
review-wave.js ingests the brief set via workflow args (consumes
`contract:lens-brief`); one `agent()` per deep brief using the brief prompt
VERBATIM (slot activation + CLEAR_ALWAYS already inside the prompt); agent()
output schema = `contract:findings-v2` with retry-on-mismatch; sweep brief
as one more agent(); merge to `.em-review/`; journaled stop/resume.
Capability probe (host workflow feature + org toggle) lives at the dispatch
seam in `tp.py lens dispatch` (tp.py:927–956) — available → payload points
at `/taskplane:review-wave` with briefs as args; absent/disabled/Codex →
today's Task instruction over the SAME briefs, zero behavior change.
`review_dispatch_path {path, reason}` traced on every wave. Tests: workflow
fixture run (one agent per deep brief, slot per prompt, injected schema
violation retries, merge lands in `.em-review/`); full review journey with
workflows disabled AND on the Codex fixture produces today's outputs
unchanged; trace asserted; property test walks gates and asserts every gate
reachable on the dispatch path alone; `test_codex_compat.py` passes
unchanged.

### t6 — CI parity + completeness (`.github/workflows/ci.yml` + fixtures)
Frozen brief fixtures (`taskplane/tests/fixtures/briefs/`) + golden
artifacts; `test_dispatch_parity.py` replays them through the dispatch path
and byte-compares against goldens, and byte-compares workflow-path vs
dispatch-path artifacts for the same briefs (validates
`contract:findings-v2`). CI gains: a required Codex-parity leg running the
parity test independently of the workflow-path tests, and an explicit
detector-fixture completeness leg (running t1's completeness test as a named
step). A deliberate dispatch-path change must fail the leg even when
workflow-path tests pass.

## Acceptance criterion → task mapping

**R-0001**
1. Stage profiles as catalog data; router restricted to active profile → **t2**
2. Deterministic verdict deep/light/n-a from content+graph+requirement, <1s → **t1** (engine, determinism, perf), **t2** (wired into route)
3. n/a always carries machine-checkable negative evidence → **t1** (Signal enforcement), **t2** (route refuses evidence-less n/a)
4. Cap 8, overflow→light never dropped; security + architecture floors → **t1** (ranker + floor rules), **t2** (route-level floor tests)
5. Coverage map + HEADLINE all 26 with reasons; meta carries routing; --lens force; --breadth all → **t3** (map + HEADLINE), **t2** (--lens force, breadth=all preserved, meta emission)
6. Detector positive+negative fixtures; audit run auto-files n/a-lens findings as router regressions → **t1** (fixtures + completeness test), **t4** (audit sweep + auto-filing), **t6** (completeness as explicit CI leg)

**R-0002**
1. review-wave.js ships, briefs via args, agent per lens, schema-pinned findings, TASKPLANE_TASK slots → **t5**
2. Workflow-path artifacts byte-equivalent to dispatch-path (fixture-verified) → **t5** (parity behavior), **t6** (parity fixtures + goldens)
3. Capability detection, zero-behavior-change fallback, traced path, no workflow-only gate → **t5**
4. CI leg: dispatch artifacts stay identical (Codex parity guard) → **t6**

## Design-contract coverage

- Proposed modules: `taskplane` (t1,t2,t3,t4,t5 via lens.py/dashboard.py/loop.py/tp.py/lens_signals.py), `lenses` (t2), `workflows` (t5, new), `taskplane/tests` (every task).
- Named contracts: `contract:stage-profiles` (t2), `contract:lens-brief` (t2 provides, t5 consumes, t6 fixtures), `contract:findings-v2` (t5 provides, t3+t4 consume, t6 validates).
- Proposed edges: union of task `design_edges` == the contract's 8 proposed edges exactly (mechanically checked; see gate).
- Depth policy: every task carries the contract's typed policy (local_depth 2, contract-only, contract_depth 1, requirement_depth 1).

## Risks per task

- **t1** (highest): detector false negatives = silent narrowing, the killer risk. Contained by: n/a-requires-negative-evidence enforced in the Signal type itself, mandatory positive+negative fixtures per detector, and t4's audit backstop auto-filing misses. 26 detectors is the largest single work item — the fixture completeness test keeps partial delivery visible.
- **t2**: legacy byte-identity is the pin — `test_lens.py` must pass UNCHANGED; any edit to it is a red flag. Catalog is generated: forgetting `_generate_catalog.py` fails CI's drift step, which is why the generator (and `docs/lens-catalog.md`, defensively) is in scope.
- **t3**: dual-shape rendering must not break old persisted metas — compat test is the guard.
- **t4**: loop.py is the largest file touched; the audit cadence must not weaken the em breadth=all guarantee on audit/release reviews (strict-or-stricter). Cadence config read from state/env inside loop.py to keep tp.py out of scope.
- **t5**: review-wave.js cannot be executed in CI (no workflow runtime in the test env) — its behavior is verified via fixture-level simulation (parse/replay of its agent() plan + schema) plus the byte-equivalence construction (verbatim prompts). The MANDATORY fallback is the non-negotiable: `test_codex_compat.py` unchanged is the pin.
- **t6**: goldens freeze the dispatch path — any later intentional change requires regenerating goldens deliberately (that friction is the point of the parity guard).

## Notes / boundaries

- Scope-disjointness is deliberate for the parallel wave: lens.py (t2) vs dashboard.py (t3) vs loop.py (t4) vs tp.py+workflows (t5) never overlap; capability detection was placed at the `tp.py lens dispatch` seam specifically so t5 does not touch loop.py (t4's file).
- No guardrail weakens: breadth=all preserved (t2/t4), architecture floor preserved (t1/t2), security floor added (t1/t2), fail-open to more coverage (t2), Codex path byte-identical (t5/t6).
- No direction questions surfaced that would need a north-star review; the plan realizes the approved design without drift.
