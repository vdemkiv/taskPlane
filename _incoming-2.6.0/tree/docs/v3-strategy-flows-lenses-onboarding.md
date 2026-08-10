# taskPlane v3 — strategy & requirements for review

Three workstreams: **adaptive flows** (the loop is too rigid), **intelligent
lens routing with graph decomposition** (26 lenses ≠ 26 every time), and
**honest onboarding** (README must serve org accounts). Written to be fed into
taskPlane as requirements after human review — every requirement carries
testable acceptance criteria.

---

## Evidence base (what the code does today — verified at v2.3.1)

**The loop.** Steps are hardcoded strings (`pm, design, design_approval, plan,
plan_approval, execute, evaluate, fix, escalated, selection, em, signoff,
done, failed`) with transitions written inline inside `gate()`, `approve()`,
`resolve()` (loop.py). There is no flow definition anywhere — the sequence IS
the code. Consequences observed in practice:

- The A/B `selection` gate had to be bolted in as a special-cased step —
  proof that new flow shapes require engine surgery, not configuration.
- One flow fits all sizes: a one-line docs fix and a distributed-contract
  feature walk the same pm→plan→approve→build→em→signoff spine. The only
  flexibilities are opt-in design, `--parallel`, checkpoints (plan/em), and
  fix-cycle count.
- No lighter path exists: review-only work fakes it with a read-only
  contract outside the loop entirely (that's how our own /tp-engineering
  runs — outside the state machine it's supposed to be governed by).
- No mid-flight adaptation: a "small fix" that grows into a feature can't
  escalate into a design phase; a spike can't graduate into governed build.

**Lens routing.** `lens.route()` selects deep lenses from: file globs, task
type, artifact type, a "code baseline", and the graph hub score. That's it —
**filename-level signals only**. There is no content awareness: nothing reads
the diff or the component to ask "does this code have user-facing strings /
SQL / concurrency / PII at all?" `breadth=all` (mandated for reviews) then
sweeps ALL remaining lenses regardless of relevance. Measured cost in our own
dogfood: two full-codebase reviews ran ~7 deep + 19 swept lenses across 7–8
subagents at roughly 1.3–1.5M subagent tokens each; a large share of sweep
spend went to lenses with no applicable surface (i18n/mobile/dba on a
stdlib CLI with one HTML renderer). The routed-deep set was 7 both times —
the user's 5–7 intuition matches the empirical shape.

**The graph.** depgraph nodes are directory-level modules plus contract/
resource boundary nodes. No sub-module decomposition (component/feature
level), and the graph stores no lens-relevance information.

**Onboarding.** README documents only the individual-account marketplace
flow. Org members (the majority of the target audience) cannot add plugins
from GitHub at all — admins must use Organization settings → Plugins
(GitHub sync / upload / marketplace) and mark plugins available. Result:
most people who saw the launch posts could not try the product.

---

## WS1 — Adaptive flows (flow-as-data)

### Direction

Make the flow a **declared artifact the engine executes**, not a sequence the
engine *is*. Governance invariants move from "properties of the fixed
sequence" to **properties of stage types**, so any flow shape keeps the same
guarantees.

Stage types (the invariant carriers):

| Stage type | Invariant (non-negotiable, enforced by the engine) |
|---|---|
| `define` | produces a scored requirement; fail-closed on missing spec |
| `design` | read-only toward code; ends in a human `gate` |
| `plan` | produces tasks.json; graph DoR fail-closed |
| `build` | scoped contract; worker submits, never advances |
| `verify` | independent evidence; **regression gate always on** |
| `review` | read-only; lens routing (WS2); severity/class discipline |
| `gate` (human) | can NEVER be skipped, auto-passed, or self-approved |
| `custom` | declared read/write scope; cannot weaken any sibling invariant |

A flow = a DAG of typed stages with transitions (pass/fail/escalate edges),
declared in `flows/<name>.json` (`taskplane.flow/v1`). The engine validates a
flow at load: unreachable stages, a `build` without a downstream `verify`, a
missing human `gate` before `done`, or any stage granting itself scope the
type forbids → the flow is REJECTED (fail closed).

Preset catalog (ships with the plugin):

- `feature` — today's full loop (pm→[design]→plan→approve→build⇄verify→em→signoff). The default; byte-compatible behavior.
- `quick-fix` — define-lite → build → verify (regression gate) → review-lite (routed lenses only) → signoff. For small, low-blast-radius changes.
- `design-only` — today's `--design-only`, as a flow.
- `review-only` — the /tp-engineering read-only review INSIDE the state machine (contract, lens wave, findings, sign-off gate) instead of beside it.
- `spike` — sandboxed build with NO merge stage; exit = keep-as-reference or graduate → `feature` (carries the spike as design input).
- `docs` — build+verify scoped to docs globs; no design; review-lite.

**Flow recommendation, not flow guessing.** At init, the engine computes
complexity signals — graph blast radius of the stated scope, requirement
score, file count, task type, boundary-contract involvement — and RECOMMENDS
a flow with stated reasons. The human confirms (or overrides with `--flow`).
Unattended/scripted runs default to the recommendation and record it.

**Mid-flow escalation only upward.** If during `build` the graph detects
scope growth (new modules, boundary contracts touched) beyond the flow's
entry criteria, the engine BLOCKS at the next gate with "this outgrew
quick-fix — escalate to feature (design phase added)". Downgrading mid-flow
is not allowed (never silently reduce governance).

### Requirements

- **R-F1 Flow schema + executor.** `taskplane.flow/v1` (stages: id, type,
  config; edges: on pass/fail/escalate). Engine executes any VALID flow;
  invalid flows rejected with named violation. *Accept:* the `feature`
  preset reproduces the current loop's transition table exactly (existing
  loop tests pass unchanged against the preset); a hand-written flow that
  omits a human gate before `done`, or gives a `review` stage write access,
  is rejected at load with the specific rule named.
- **R-F2 Preset catalog.** The six presets above ship as data files with
  entry criteria (max blast radius, max files, allowed task types).
  *Accept:* each preset runs end-to-end in tests; `review-only` produces the
  same artifacts as today's standalone tp-engineering run.
- **R-F3 Flow recommendation.** `loop init` (no `--flow`) computes signals
  and recommends, with reasons, requiring confirmation at the first gate;
  `--flow <name>` overrides; the recommendation and the choice are traced.
  *Accept:* docs-only diff → `docs` recommended; new boundary contract in
  scope → `feature` with design; the reasons name the signals.
- **R-F4 Invariants as stage-type properties.** Property-style tests
  enumerate every preset + adversarial flow definitions and assert: no flow
  can skip a human gate, no worker can advance state, every `build` path
  crosses a `verify` with the regression gate, read-only stages cannot gain
  write scope. *Accept:* the adversarial suite (≥10 hostile flow defs) all
  reject; mutation of a preset to drop `verify` fails validation.
- **R-F5 Upward escalation.** Scope growth beyond entry criteria blocks the
  next gate with the escalation offer; accepting migrates state into the
  bigger flow preserving history; declining requires explicit human
  `--force` recorded as a decision. *Accept:* a quick-fix that adds a new
  module blocks and escalates cleanly; the trace shows the migration.
- **R-F6 Back-compat.** Existing `loop.json` files (no flow field) resume as
  the `feature` preset. *Accept:* a v2.3.1-created loop.json resumes and
  completes under v3.

---

## WS2 — Intelligent lenses + graph decomposition

### Direction

Three layers, each independently valuable:

**1. Stage profiles.** Each flow stage type declares its lens universe:
`design` → solution-design, architecture, tradeoffs, scalability, security,
data-safety, services-selection, cost-finops; `build` (verify stage) →
code-quality, testability, backend/frontend (by surface), security;
`review` → the full catalog as the *candidate* set. Profiles bound what can
even be considered per stage — the router then selects within them.

**2. Applicability engine (the core).** Per lens, a **detector**: a cheap,
deterministic scanner producing `{score, evidence[]}` from three signal
sources — content (greps/AST-lite over the component: i18n calls & locale
files & user-facing string density; UI markup; SQL/migrations; HTTP/queue
clients; auth/PII markers; concurrency primitives; platform APIs),
graph (component kind, hub score, boundary contracts, dependents), and
requirement (acceptance-criteria keywords). The router outputs, for EVERY
lens in the stage profile, one of:

- `deep` — strong signal; runs as its own pass;
- `light` — weak signal; batched quick pass;
- `n/a` — **with stated negative evidence** ("0 i18n signals: no locale
  files, no i18n imports, no user-facing string literals in scope").

**Skipping is never silent** — this is the review-discipline principle
applied to routing itself. The coverage map (already rendered on the
findings dashboard) shows all 26 lenses as deep / light / n-a-with-reason,
and the HEADLINE reads e.g. `lenses 6 deep · 3 light · 17 n/a (evidenced) of
26`. `--lens <id>` forces any lens regardless of verdict.

Budget: **5–7 deep lenses per task** (hard cap 8, floor rules below), light
batch ≤1 agent. Floors: `security` may not be n/a when the diff touches
enforcement/boundary/auth surfaces; `architecture` drops from always-deep to
always-≥light on code (its current governance floor, made cheaper).

**Detector honesty is a regression surface.** A detector that misses real
i18n is precisely the "regression a test-diff can't see". Therefore:
every detector ships with positive AND negative fixtures (a repo slice WITH
i18n must fire; one without must produce the negative evidence string), and
a **periodic full-catalog audit** (every Nth review, or on release reviews)
runs ALL 26 lenses and diffs findings against what the router would have
selected — any finding from an n/a lens is a router regression, auto-filed.

**3. Graph decomposition.** New graph layer: **components** — sub-module
clusters derived from directory convention + import cohesion + requirement
links (e.g. `taskplane/dashboard.py` decomposes into `renderer.widget`,
`renderer.findings`, `renderer.paged`, …). Each component node stores: its
files/symbols span, dependencies (component-level edges), the lens map from
the applicability engine, and a content fingerprint. Lens maps recompute
only when the fingerprint changes (cached, cheap). Reviews then assemble
their lens set as the UNION of touched components' maps (capped per budget),
and impact/blast-radius reporting gains component precision ("touches
renderer.paged; dependents: findings dashboard, wave board" instead of
"touches taskplane").

### Requirements

- **R-L1 Stage lens profiles.** Profiles as catalog data (`stage_profiles`
  in catalog.json); router restricted to the active stage's profile.
  *Accept:* a design-stage route never selects code-quality; profiles are
  data, adding a lens to a profile requires no code change.
- **R-L2 Applicability detectors.** Detector per lens (or explicit
  `always`/`inherit` marker), deterministic, <1s per component, producing
  score + evidence; n/a requires machine-checkable negative evidence.
  *Accept:* the i18n detector on taskPlane's own repo yields n/a with the
  negative-evidence string; on a fixture with locale files it yields deep;
  every detector has ≥1 positive and ≥1 negative fixture test.
- **R-L3 Budget + floors + override.** 5–7 deep target, hard cap 8 (ranked
  by score; overflow demoted to light, never dropped), security/architecture
  floor rules, `--lens` force, `--breadth all` still available for audits.
  *Accept:* a max-signal diff yields exactly 8 deep + rest light/n-a; a
  forced lens runs despite n/a; enforcement-touching diff can never route
  security to n/a.
- **R-L4 Coverage honesty.** Dashboard + HEADLINE show deep/light/n-a-with-
  reason for all 26; findings.json meta carries the full routing decision
  with evidence. *Accept:* rendered coverage map lists a reason string for
  every n/a lens; headline format updated and pinned by tests.
- **R-L5 Component decomposition.** `graph scan --decompose` builds
  component nodes (directory + import cohesion heuristics; overridable via
  a `components.yaml`); component-level edges; content fingerprints.
  *Accept:* taskPlane's own dashboard.py yields ≥3 components with distinct
  dependency sets; re-scan without changes is a no-op (fingerprint cache).
- **R-L6 Per-component lens maps + review assembly.** Lens maps stored on
  component nodes, recomputed on fingerprint change; review lens set =
  capped union over touched components; per-component findings attribution.
  *Accept:* a diff touching only `renderer.paged` routes frontend/design
  deep and i18n per that component's map, not the repo-wide default; the
  review's meta names which component contributed each routed lens.
- **R-L7 Router audit loop.** Every Nth review (configurable, default 5) or
  any release review runs full-catalog; findings from n/a-routed lenses are
  auto-filed as router regressions (class: regression, owner: router).
  *Accept:* an audit sweep on a fixture with a deliberately-broken detector
  files the router regression automatically.

**Expected impact** (measured against our own dogfood baseline of ~1.3–1.5M
tokens per full review): typical task reviews run 5–7 deep + 1 light batch
instead of 7 deep + 19 sweeps → **50–70% token reduction and roughly halved
wall-clock**, with *higher* precision (fewer observation-class findings from
irrelevant lenses) and audit-guaranteed honesty about what was skipped.

---

## WS3 — Onboarding & README (ships with the same release)

### Direction

Lead with reality by account type; document the new capabilities; make
in-product onboarding detect context.

### Requirements

- **R-D1 Install by account type.** README install section restructured:
  (1) *Individual account* — marketplace / GitHub URL flow; (2)
  *Organization admin* — Organization settings → Plugins → GitHub sync →
  `vdemkiv/taskPlane` → set Available/Required, with auto-update note; (3)
  *Organization member* — plain statement that members install from the
  org's curated list; "ask your admin (link this section)" + try-on-personal
  fallback. *Accept:* no install path in the README is inaccessible to the
  reader it addresses; the member path contains zero dead-end instructions.
- **R-D2 Feature docs.** New sections: adaptive flows (presets + when each
  fires), intelligent lensing (the coverage map, the n/a-with-evidence
  contract, `--lens`), regression gate + review discipline (from v2.3.1),
  component graph. Each with one honest example from the dogfood runs.
  *Accept:* every user-facing flag and preset added in v3 appears; the CI
  docs-drift check covers the new sections.
- **R-D3 Context-aware onboarding.** `tp onboard` detects (where the host
  exposes it) whether it runs under an org-managed install and prints the
  matching install/update path; where undetectable, it prints the
  by-account-type triage instead of the individual-only flow. *Accept:*
  onboarding output never instructs an org member to use a flow they don't
  have; fixture tests for each context.
- **R-D4 Launch copy alignment.** The repo's launch/marketing snippets
  (posts, marketplace descriptions) state who can install today (individual
  accounts; org admins) and that org-wide one-click arrives with the Claude
  marketplace listing. *Accept:* no public copy in the repo implies an org
  member can self-install from GitHub.

---

## Sequencing, dependencies, risks

**Recommended order:**

1. **Phase 1 — WS2 layers 1+2** (stage profiles + applicability engine +
   coverage honesty + audit loop). Biggest cost/precision win, independent
   of the loop rewrite, immediately dogfoodable on the next review.
   WS3 R-D1/R-D4 ship here too (they're cheap and overdue).
2. **Phase 2 — WS2 layer 3** (component decomposition + per-component maps).
   Depends only on depgraph; makes layer-2 sharper.
3. **Phase 3 — WS1 flows.** The largest surgery (loop.py transition logic →
   flow executor). Do it AFTER lensing so the `review`/`verify` stage types
   can consume the new router from day one. Precondition: extract the
   evidence-validation seam from loop.py first (the standing architecture
   debt) — rewriting flows inside a 2,800-line module multiplies risk.
   WS3 R-D2/R-D3 ship with the v3 release.

**Risks & mitigations:**

- *Silent narrowing* (the killer risk for WS2): mitigated three ways —
  n/a requires machine-checkable negative evidence, coverage map renders
  every lens's disposition, and the Nth-review full-catalog audit converts
  any miss into an auto-filed router regression.
- *Detector false negatives*: fixture tests per detector (positive AND
  negative) are part of R-L2's DoD, and the audit loop is the backstop.
- *Flow flexibility eroding governance* (the killer risk for WS1):
  invariants live on stage types with an adversarial rejection suite
  (R-F4); no flow definition can express less governance than its stages'
  types guarantee.
- *loop.py blast radius*: Phase-3 precondition extraction; the regression
  gate (already shipped) runs on every increment — dogfood it.

**Success metrics:** tokens per review (target −50% typical), wall-clock per
review (target −40%), blocker-precision (share of findings that gate:
expect ↑ as observation noise from irrelevant lenses drops), detector audit
escapes (target 0 sustained), onboarding conversion (qualitative until the
marketplace listing lands: no more "couldn't even try it" reports).

---

## Addendum — host orchestration landscape (verified Aug 2026)

**Claude: Dynamic Workflows** (Claude Code ≥2.1.154, all paid plans; CLI,
Desktop, IDE, headless, Agent SDK). Deterministic JavaScript orchestration
scripts run in an isolated background runtime: `agent()` (with **schema** —
validated structured output with retry-on-mismatch), `pipeline()` (no-barrier
streaming), `parallel()` (barrier), `phase()`, `budget`, `args`. Runs are
**journaled and resumable** (completed agents return cached results); 16
concurrent / 1,000 agents caps; large-run warning at >25 agents or >1.5M
projected tokens. **Plugins ship workflows** via a `workflows/` directory,
namespaced `/taskplane:<name>`. Constraint that shapes our design: **no
mid-run user input** — official guidance is one workflow per stage between
human sign-offs. Workflow subagents run acceptEdits but inherit the tool
allowlist and **hooks still fire** — taskPlane's PreToolUse contract screen
governs workflow agents unchanged. Orgs can disable workflows entirely
(managed settings), so nothing may depend on them exclusively.

**Codex: no workflow runtime.** The Codex app (Feb/Mar 2026) has parallel
agents on isolated worktrees, Skills, and **Automations** (scheduled
background tasks with a review queue) — but no deterministic script
orchestration, no schema-validated agent output, no resumable plugin-visible
runs. **Consequence: the current skill-driven agent dispatch IS the Codex
path and must keep working exactly as it does today.**

### Strategic consequence for WS1

taskPlane must NOT build its own orchestration runtime. The layering:
**taskPlane owns the governance state machine** (flow state, contracts,
fail-closed gates, evidence, human approvals — the things host workflows
deliberately lack); **the host's engine executes the fan-out inside a
stage**. On Claude, each governed stage between human gates compiles to one
workflow run (matching the platform's own one-workflow-per-stage guidance);
on Codex and anywhere workflows are absent/disabled, the same stage runs
through today's Task-based dispatch. R-F1's executor scope shrinks
accordingly: the flow engine sequences stages and gates; it never re-invents
agent scheduling.

Dogfood evidence for why this matters: during the v2.3.0 review, four
hand-dispatched lens agents died mid-run on a model-credit cutoff with no
journal — everything re-ran from scratch. Under a workflow run that is a
resume, not a redo.

### Additional requirements (R-W group)

- **R-W1 Review wave as a plugin workflow (Claude hosts).** Ship
  `workflows/review-wave.js` in the plugin: consumes `lens dispatch` briefs
  via `args`, runs one `agent()` per routed lens with a **schema pinning the
  findings.json shape** (severity/class/file/line/title/scenario/fix +
  lens id), honors per-brief contracts (TASKPLANE_TASK slot exported by each
  agent), merges results, writes `.em-review/`. *Accept:* `/taskplane:
  review-wave` produces byte-equivalent findings artifacts to the dispatch
  path on the same briefs; a schema violation retries rather than writing an
  invalid findings file; stopping and resuming the run re-uses completed
  lenses' cached results.
- **R-W2 Capability detection + fallback (MANDATORY).** The engine detects
  workflow availability (host + org toggle). Absent/disabled/Codex → the
  identical stage runs via today's Task-based dispatch with zero behavior
  change; the chosen path is traced. *Accept:* the full review journey
  passes on a host with workflows disabled AND on Codex fixtures with
  today's outputs; no code path exists where workflows are the only route to
  a gate.
- **R-W3 Stage-per-run compilation (feeds R-F1).** Flow stages between
  human gates compile to at most one workflow run each; human gates are
  never inside a run. *Accept:* no generated script contains an approval
  step; the design/plan/signoff gates remain conversation-level.
- **R-W4 Audit sweep as scheduled automation.** The R-L7 full-catalog audit
  can run as a Claude Routine / Codex Automation (parameterized, results to
  the review queue), falling back to the every-Nth-review trigger where
  scheduling is unavailable. *Accept:* one audit definition, three trigger
  modes, identical artifacts.
- **R-W5 Codex parity guard.** CI carries a Codex-fixture leg asserting the
  dispatch path's artifacts remain identical as workflow support lands.
  *Accept:* a change that breaks the dispatch path fails CI even when the
  workflow path passes.
