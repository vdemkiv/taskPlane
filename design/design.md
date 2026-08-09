# Design — v3 Phase 2: graph decomposition + governed flows + onboarding overhaul + routed-review wiring

Anchored requirement: **R-0003** (primary; this loop). **R-0004**, **R-0005**
and **R-0006** ship in the same release (spec directive: implement all four
streams) and are designed here as one delta because they share the same
contract surface: R-0003 reshapes `contract:lens-brief` (component
attribution), R-0004's stage waves and R-0006's evaluate wiring consume those
briefs, and R-0005 documents all of it. Their acceptance criteria are mapped
in `design/contract.json` `secondary_requirements` (the strict top-level
`acceptance_map` is scoped to R-0003 by the DoD validator).

Baseline: Phase 1 (R-0001 routing v2 + R-0002 review wave) shipped as v2.4.0.
This design is a delta against that shipped state, grounded in the cited
file:line sources in the contract's `current_state`.

## Stream 1 · R-0003 — graph decomposition + per-component lens maps

### The choice

**Hybrid derivation: directory convention + import/reference cohesion, with
AST symbol clustering for oversized single files; components stored as a
layer inside `graph.json`; derivation code in a new file
`taskplane/decompose.py`; consumption in route v2 as a cached-map union with
a structural fail-open to the module route.**

- **Derivation.** A module decomposes into components along two axes:
  (a) file clusters — files grouped by sub-directory convention, then merged/
  split by import cohesion (files that import each other or share private
  imports cluster together); (b) intra-file symbol clusters for oversized
  files — top-level `def`/`class` groups (Python `ast`, already a depgraph
  dependency) clustered by shared name prefix (`render_*`, `lens_*`,
  `headline_*`) and by reference cohesion (a symbol that only calls into one
  cluster joins it). This is what makes the pinned acceptance real:
  `taskplane/dashboard.py` (3,379 lines, one file inside the one `taskplane`
  module) yields ≥3 components with distinct dependency sets. A
  `components.yaml` at the repo root overrides any derived clustering
  per module (explicit file/symbol → component pinning).
- **Granularity floor (decided — the pm's open question 1).** A module is a
  decomposition CANDIDATE only when it has ≥8 code files or any single code
  file ≥600 physical lines; a candidate cluster earns a component node only
  with ≥2 files, or (intra-file) ≥4 top-level symbols spanning ≥120 lines.
  Everything below the floor folds into one residual component
  `<module>::core`, and a module below the candidate threshold IS its single
  component. Rationale: the floor caps the component count at O(tens) on this
  repo (not O(files) ≈ 100s — the graph-explosion failure), while the
  600-line file trigger guarantees the known hot spots (dashboard.py 3,379,
  loop.py 3,191, tp.py 2,354, depgraph.py 1,370) decompose instead of
  under-decomposing into one-node modules. The floor values live as named
  constants in `decompose.py` and are overridable in `components.yaml`.
- **Where the map lives.** `graph.json` gains a top-level `components`
  section (the graph is the one persistent, fingerprint-stamped,
  atomically-written KB structure we already have — a second store would
  duplicate its locking/corruption discipline for no gain). The modules/edges
  sections are untouched: components are a LAYER, and every existing consumer
  (impact, hub_signal, DoR/DoD, dashboards) is byte-identical when the key is
  absent. Derivation lives in the NEW file `taskplane/decompose.py` — not
  inside depgraph.py — because D-0004 is this phase's own lesson about module
  accretion; `depgraph.scan(ws, decompose=True)` (CLI:
  `tp graph scan --decompose`) calls it behind the flag.
- **Per-component lens maps.** Reuse — not duplicate — the Phase 1 engine:
  `lens_signals.route_verdicts` runs over each component's file set as the
  ctx (content signals per component; graph payload from the component's own
  dependency set), and the resulting `{lens: {verdict, score, evidence}}` map
  is stored on the component node with its content fingerprint. Maps
  recompute ONLY when the fingerprint changes (a recompute counter is traced,
  and the no-change-rescan-is-a-no-op criterion pins it).
- **Review assembly (route v2 consumption).** `route_git_diff` maps changed
  files → touched components via the component file index. When every changed
  file maps to a fingerprint-current component, candidate verdicts are the
  union of the touched components' cached maps, re-evidenced against the live
  diff ctx; the R-0001 budget (5–7 deep target, hard cap 8,
  demote-never-drop) and the security/architecture floors run AFTER assembly
  on the REAL diff ctx — floors are never served from cache. Findings meta
  gains `component_attribution` (`{lens: [component ids]}`), carried
  additively on `contract:lens-brief`.
- **Fail-open (load-bearing).** Any miss — component layer absent, a changed
  file with no component, a stale fingerprint, any exception — WIDENS routing
  to the Phase 1 module-level route (traced `component_layer_failed`), which
  itself fails open to legacy breadth=all. The superset property is
  structural, not aspirational: every detector signal is existential or
  count-monotone over the file set, and each component's file set is a subset
  of the whole diff, so whole-diff scoring can only score ≥ the union of
  per-component scores. A pinned test asserts the routed set under a broken
  layer is a superset of the component-routed set.
- **Dashboard.** `depgraph.to_html` renders the component layer (component
  nodes grouped within their module, distinct visual class); HEADLINE and
  coverage-map FORMAT are unchanged (pinned).

### Rejected alternatives

- **Pure graph clustering** (community detection over import edges only):
  language-blind and convention-blind — it cannot decompose a single large
  file at all (imports are file-level), so the pinned dashboard.py criterion
  is unreachable; cluster boundaries drift with every scan, making the
  fingerprint cache useless and lens-map attribution unstable.
- **Pure path-prefix heuristics**: deterministic and cheap but blind to
  cohesion — `taskplane/` stays one component forever (this repo's actual
  shape: one directory, 15 files, 3 of them >2,000 lines), which is exactly
  the under-decomposition R-0003 exists to fix.
- **Status quo** (module-level routing only): remains the permanent fallback
  rung and the byte-identical behavior when decomposition is absent — but as
  the only behavior it leaves a one-renderer-component diff reviewed as if it
  touched all of `taskplane/` (the measured over-review cost from the
  strategy doc).

## Stream 2 · R-0004 — governed flows: execute/evaluate/fix stage waves

### The choice

**One workflow FILE per stage (`workflows/execute-wave.js`,
`workflows/evaluate-wave.js`, `workflows/fix-wave.js`), each following the
review-wave.js pattern exactly (pure-literal `export const meta`,
schema-pinned `agent()` outputs, deterministic — no clock/random/dynamic
import); ONE workflow RUN per stage between human gates; the stage emitter
lives in tp.py behind the SAME `workflow_available()` gate; the Task-dispatch
path stays the byte-identical reference implementation and the only Codex
path.**

- **Compilation model.** A governed stage between human gates compiles to at
  most one journaled workflow run. Human gates are NEVER inside a run
  (workflows cannot pause for humans): plan approval → [execute-wave run] →
  conversation-level task gates → [evaluate-wave run] → … → sign-off. A test
  scans every generated stage run for gate verbs (extends
  `TestNoWorkflowOnlyGate`'s pinning style).
- **Emitter placement.** tp.py — NOT loop.py or lens.py — because
  `TestNoWorkflowOnlyGate.test_loop_and_lens_have_zero_workflow_coupling`
  pins those modules workflow-agnostic, and that pin is a guardrail this
  design extends (audit.py joins the pinned set), never relaxes. The stage
  emitter (`--emit auto|workflow|task` on the stage dispatch surfaces,
  mirroring `tp lens dispatch`) reuses `workflow_available()` verbatim: Codex
  markers always win, `TASKPLANE_WORKFLOWS=0/false/no/off` always forces
  dispatch, the default is conservatively unavailable, and the chosen path is
  traced (`stage_dispatch_path {stage, path, reason}`).
- **Workers under the harness.** Each workflow `agent()` receives the SAME
  prompt text the Task path emits, verbatim — including per-task
  `export TASKPLANE_TASK=<slot>` activation and the claim/submit/CLEAR
  protocol. Execute agents still claim into `.tp-work/<task>` worktrees via
  `tp loop claim` (worktree isolation is the engine's, not the workflow's);
  the PreToolUse contract screen fires inside workflow agents unchanged;
  workers submit evidence and NEVER advance state — the orchestrator gates at
  conversation level after the run returns. The `agent()` schema per stage
  pins a submission receipt (`{task, outcome, note}`) for execute/fix and the
  findings-v2 shape for evaluate agents; a violation retries instead of
  surfacing an invalid result.
- **Byte-identity.** The fallback IS the reference: CI parity goldens extend
  the R-0002 review-wave goldens to the three stage waves (frozen stage
  payloads captured through the emitter with the R-0002 scrub rules,
  regenerated only via the documented regen path). `--emit task` and the bare
  default produce stdout byte-identical to today's payloads; the Codex CI
  fixture leg passes with today's outputs and asserts no workflow runtime is
  invoked on Codex hosts.
- **Resume.** A killed-mid-stage run resumes from the journal; completed
  agents return cached results (the v2.3.0 credit-cutoff redo becomes a
  resume). On dispatch-only hosts this failure class is unchanged from today.

### Rejected alternatives

- **One generic parameterized `stage-wave.js`**: fewer files, but the shipped
  test pattern pins each workflow's `meta` as a pure static literal and its
  schema as a static constant checked without a JS runtime; a generic file
  needs runtime schema/phase selection, which weakens exactly those static
  determinism pins — and per-stage schemas genuinely differ (receipt vs
  findings).
- **Flow-as-data executor now (`taskplane.flow/v1`)**: explicitly out of
  scope (spec); rewriting loop.py's transition logic multiplies risk before
  the audit-extraction debt (D-0004) is paid — this phase does only the R-W3
  stage-per-run compilation.
- **Status quo (Task dispatch only)**: remains permanently wired and
  CI-guarded, but as the only path every mid-stage death stays a full redo —
  the measured dogfood cost this stream exists to remove.

## Stream 3 · R-0005 — README + onboarding overhaul

### The choice

**One README with an install decision tree by account type, per-host
quickstarts, v3 feature docs; `tp onboard` truth-up; every claim pinned by a
named CI/test check.** Docs-only stream — no new code contracts; the only
code touched is `tp.py`'s onboarding report (context-aware install guidance)
plus CI legs.

- **Install decision tree** ("Which account are you on?"): (1) *Personal* —
  marketplace / GitHub URL flow, as today; (2) *Organization admin* —
  Organization settings → Plugins → GitHub sync → `vdemkiv/taskPlane` → set
  Available/Required, with the auto-update note; (3) *Organization member* —
  the plain, honest statement that members CANNOT install plugins from
  GitHub: install from the org's curated list or admin file-upload only, with
  a link to the admin section ("ask your admin") and the try-on-personal
  fallback. Zero dead-end instructions on the member path.
- **Per-host quickstarts** (Claude Code / Cowork / Codex), each runnable as
  written on that host; the Codex quickstart never references workflow-only
  features (dispatch is the Codex path; workflows are an optimization
  elsewhere).
- **v3 feature docs**: routing v2 (coverage map, n/a-with-evidence, `--lens`),
  review wave + mandatory dispatch fallback, component decomposition
  (`graph scan --decompose`, components.yaml, fail-open), governed stage
  flows (one run per stage, kill-switch) — each with one honest dogfood
  example. What's-new table stays at exactly 3 rows; CHANGELOG stays
  authoritative.
- **`tp onboard` truth-up**: where the host exposes org-managed install
  context, print the matching install/update path; where undetectable, print
  the by-account-type triage instead of the individual-only flow.
- **Named acceptance validation** (docs streams still get mechanical DoD):
  (1) a CI grep leg for the forbidden member-installs-from-GitHub claim,
  repo-wide across public copy; (2) the existing CI docs-drift check extended
  to the new sections; (3) a taskplane/tests check pinning the what's-new
  table at exactly 3 rows; (4) onboard fixture tests per detected context;
  (5) a README link check in CI; (6) a Codex-quickstart grep asserting no
  workflow-only feature appears.

### Rejected alternatives

- **Split per-audience install docs** (`docs/install-org.md` + thin README):
  the failed user journey starts and dies on the README at GitHub — the
  observed launch failure was people not reaching a working path at all;
  adding a hop moves the dead end, it does not remove it. Feature DEPTH goes
  to docs/; install truth stays on the one page everyone lands on.
- **Status quo + a warning box**: keeps documenting an install path most of
  the target audience cannot use; a warning next to a dead end is still a
  dead end.

## Stream 4 · R-0006 — routed-review wiring + debt burn-down

### The choice

- **Evaluate consumes routed briefs.** The evaluate step passes
  `stage="build"` into the routing call (route v2 engages: build-profile
  candidates, R-0001 verdicts, component assembly from R-0003) and its brief
  carries the dispatch payload (deep briefs + one light batch) exactly like
  the em step's does today. **Decided (spec open question 2): the evaluate
  wave inherits the R-0001 budget verbatim** — 5–7 deep target, hard cap 8,
  demote-never-drop — with the `build` stage profile as the candidate set
  (that is what stage profiles are for, and floored lenses survive profile
  narrowing by the shipped route v2 rule). The em step's full-catalog mandate
  and the `"all" if step == "em" else "routed"` wiring are UNTOUCHED, as are
  the audit cadence (`TASKPLANE_AUDIT_EVERY`) and the router-regression
  sign-off block.
- **Fixtures-path discount (D-0002).** `lens_signals` gains a fixture-path
  classifier (path segments `fixtures`, `testdata`, `goldens`; extensions
  like `.golden`); in `_spec_detect`, path/content/density signal hits whose
  ONLY support is fixture-path files are discounted ×0.25, with the discount
  named in the evidence string (honesty: the evidence says why the score is
  low). A fixture-only diff drops i18n/mobile below deep (negative fixture
  test); a real locale-file diff scores full weight and still routes i18n
  deep (positive fixture unchanged). Parity goldens are regenerated ONLY via
  the documented `taskplane/tests/fixtures/briefs/regen.py`, reviewed as a
  diff; hand-edited goldens remain a finding.
- **Routed-audit hybrid (D-0003) — decision designed, not the hybrid.** This
  phase ships the MEASUREMENT and records the decision; it does not change
  audit execution. During this phase's own em review, run the comparison:
  full breadth=all audit (current) vs the hybrid shape (routed deep + one
  batched verification sweep whose brief checks each n/a lens's
  negative-evidence claims only), on the dogfood corpus. **Adoption bar
  (confirming the spec's suggestion): adopt only if measured audit tokens
  drop ≥30% AND the hybrid files every router regression the full audit files
  (zero escaped n/a-lens findings) on the replay corpus.** The outcome is
  recorded in the decision registry either way — adopt-with-follow-up
  requirement, or decline with the measured numbers. Default is DECLINE
  unless the bar is met; the em full-catalog mandate is not weakened in this
  phase in either branch.
- **Audit extraction (D-0004) — byte-frozen.** The audit machinery
  (loop.py:1426–1680: cadence state, `audit_every`/`audit_counter`/
  `record_audit_review`/`audit_due`, `router_audit`, `_router_audit_gate`,
  decision extraction, `_audit_brief`) moves to the new file
  `taskplane/audit.py`; loop.py delegates through thin imports that preserve
  the public names (`loop.audit_due` etc. re-exported — zero caller churn).
  Byte-frozen proof: a differential test captures, BEFORE the move, golden
  outputs of the audit surfaces over a scenario corpus (cadence states ×
  release flags × meta shapes × findings sets → the `_audit_brief` dict, the
  `router_audit` list, the `_router_audit_gate` error list AND the rewritten
  findings.json bytes) and asserts the post-extraction outputs are identical;
  a loop.py line-count assertion pins the shrink; audit.py joins the
  workflow-agnostic module pin.

### Rejected alternatives

- **Evaluate keeps its legacy route** (no stage): leaves Phase 1's router
  unused at the step that pays for it and blocks R-0003's per-task component
  precision — the wiring gap is the debt.
- **Blanket fixture-path exclusion** (drop fixture files from ctx entirely):
  simpler, but a diff that ONLY touches fixtures would route almost nothing —
  discounting keeps a weak signal (light) instead of manufacturing silence;
  exclusion is a narrowing, discount is a re-weighting.
- **Adopt the routed-audit hybrid now, measure later**: weakens the one
  backstop (full-catalog audit) that polices every other narrowing decision
  in this phase — the audit is the last thing allowed to get cheaper, and
  only with measured proof.
- **Leave the audit code in loop.py**: loop.py is 3,191 lines and this phase
  adds evaluate wiring to it; the extraction seam is exactly the
  "evidence-validation seam" precondition the strategy names for Phase 3 —
  deferring again compounds the risk this debt records.

## Guardrail statement — nothing loosened

Every enforcement surface this phase touches, and why each change is
strict-or-stricter:

1. **Security/architecture floors** — unchanged rules, now applied AFTER
   component assembly on the REAL diff ctx (never served from cached maps);
   they hold at component granularity and survive stage-profile narrowing
   (shipped route v2 rule, retained). Stricter surface area, same rule.
2. **n/a-with-negative-evidence** — unchanged; component-assembled n/a
   entries carry the union of per-component negative evidence plus live-diff
   re-evidence; the em-gate block on bare/empty n/a moves verbatim into
   audit.py under the byte-frozen differential.
3. **Cap-8 demote-never-drop and `--lens` force** — unchanged, applied
   identically on every routing rung.
4. **Fail-open ladder** — new rung only ever WIDENS: component layer →
   module route v2 → legacy breadth=all; each drop is traced and
   test-pinned (superset test).
5. **em full-catalog mandate + audit cadence + router-regression sign-off
   block** — untouched (the `"all" if step == "em"` wiring literal, the
   cadence, and the auto-filing gate all survive extraction byte-frozen).
6. **Worker protocol** — workflow agents submit and never advance state;
   gates stay conversation-level; a test scans generated runs for gate verbs;
   the adversarial every-gate-with-workflows-disabled test extends to the
   three stage waves.
7. **PreToolUse contract screen + per-brief TASKPLANE_TASK slots** — govern
   workflow agents unchanged (verbatim prompts carry the slot exports);
   contract-screen semantics are out of scope (verify-only).
8. **Codex parity** — dispatch stdout stays byte-identical; goldens extended
   (review + three stages), regenerated only via regen.py; the Codex CI leg
   additionally asserts no workflow runtime is invoked.
9. **Workflow-agnostic module pin** — loop.py and lens.py stay
   workflow-free; audit.py JOINS the pin (the pinned set grows, never
   shrinks).
10. **Graph governance** — components are an overlay layer; module graph,
    DoR/DoD, impact and fingerprints are byte-identical when the layer is
    absent; decomposition is read-only toward code and never blocks a gate by
    failing (it widens routing instead).
11. **Docs** — no step in any new doc instructs disabling the contract
    screen, hooks, or gates; org-member guidance never routes around admin
    curation (CI grep pins the forbidden claim).

## Validation map

Every R-0003 criterion is mapped 1:1 in `design/contract.json`
`acceptance_map`; every R-0004/R-0005/R-0006 criterion is mapped in
`secondary_requirements` with the concrete test file that will prove it.
Headline proofs: `taskplane/tests/test_decompose.py` (≥3 dashboard.py
components, no-op rescan, single-map recompute, fail-open superset),
`taskplane/tests/test_stage_waves.py` (static workflow pins, emitter
byte-identity, kill-switch matrix, gate-verb scan, adversarial
gates-without-workflows), `taskplane/tests/test_onboarding_docs.py`
(member-path grep, 3-row pin, onboard fixtures), and
`taskplane/tests/test_audit_extraction.py` (differential replay, loop.py
shrink) — plus the extended parity goldens and the full suite
(`python3 -m unittest discover -s taskplane/tests -q`, count only goes up
from 954).

## Visualization

`design/visual.html` (data-flow): the two load-bearing structures — the
three-rung routing fail-open ladder and the dual stage-dispatch rails with
gates outside runs — drawn with their failure edges, because those two
invariants (never narrower; never workflow-only) are what the human is
approving.
