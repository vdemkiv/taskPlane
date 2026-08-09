# Spec — taskPlane v3 Phase 2: graph decomposition + governed flows + onboarding overhaul + routed-review wiring

Requirements: **R-0003, R-0004, R-0005, R-0006** (store: `knowledge/requirements/`).
Strategy source: `docs/v3-strategy-flows-lenses-onboarding.md` (approved).
Phase 1 (R-0001 routing v2 + R-0002 review-wave workflow) shipped as **v2.4.0**
(CHANGELOG top row). Human directive for this phase: **implement all 4 streams.**

## Problem

Phase 1 made lens routing signal-driven and the review wave resumable, but four
gaps remain. (1) Routing resolves at module granularity — the graph has no
component layer, so a diff touching one renderer component is reviewed as if it
touched all of `taskplane/`. (2) Only the review wave runs as a journaled
workflow; the execute/evaluate/fix waves still die unjournaled mid-run (the
v2.3.0 credit-cutoff redo). (3) The README documents an install path most of
the target audience — org members — cannot use; this blocked real users.
(4) Phase 1 left three tracked debt records (D-0002 detector fixture inflation,
D-0003 audit sweep cost, D-0004 loop.py accretion) and routed briefs are not
yet wired into evaluate.

## Users & context

- **taskPlane developers** (dogfood): every review/build wave on this repo pays
  the routing and resumability costs directly.
- **Org adopters** — admins and members on team/enterprise Claude accounts —
  plus individual-account users, on Claude Code, Cowork, and Codex. The
  onboarding stream exists because org members could not even install the
  product (members cannot install plugins from GitHub; only admin-published
  marketplace or file-upload).

## In scope — the four streams

### Stream 1 · R-0003 — Graph decomposition + per-component lens maps (WS2 layer 3)

Decompose the dependency graph into **components** — sub-module clusters
derived from directory convention + import cohesion, overridable via
`components.yaml`. Each component node carries its file/symbol span,
component-level dependency edges, a lens map computed from the R-0001 detector
signals, and a content fingerprint (maps recompute only on fingerprint change).
Reviews assemble the 5–7 most relevant lenses as the capped union of touched
components' maps. Decomposition renders in the dashboard graph view.
**Fail-open is load-bearing: decomposition failure WIDENS routing to the
module-level route — it never narrows a review.**

Acceptance (mirrors R-0003; these are the DoD):
1. `graph scan --decompose` on this repo yields ≥3 components for
   `taskplane/dashboard.py` with distinct dependency sets (pinned depgraph test).
2. Re-scan with no content changes is a no-op — fingerprint cache hit, zero
   lens-map recomputes.
3. Lens maps recompute only for the component whose fingerprint changed.
4. A single-component diff routes that component's lenses, not the repo
   default; findings meta names which component contributed each routed lens.
5. Dashboard graph view renders the component layer (renderer test).
6. Fail-open test: the routed set under a broken/absent component layer is a
   **superset** of the component-routed set — never narrower.
7. **No guardrail loosened**: security/architecture floors, n/a-with-evidence,
   cap-8 demote-never-drop, and `--lens` force hold at component granularity;
   legacy module-level routing stays byte-identical when decomposition is absent.

### Stream 2 · R-0004 — Governed flows beyond review (WS1)

The execute/evaluate/fix waves each become dispatchable as **ONE Claude Dynamic
Workflow run per stage between human gates** (workflows cannot pause mid-run
for humans — gates stay conversation-level), journaled and resumable.
**MANDATORY byte-identical Task-dispatch fallback. Codex behavior unchanged —
no workflow runtime there, verified.** Capability detection reuses the existing
`workflow_available()` / `TASKPLANE_WORKFLOWS` logic in tp.py; no gate is
reachable only via workflows.

Acceptance (mirrors R-0004):
1. One workflow run per stage; no generated run contains an approval/gate step
   (design/plan/signoff gates remain conversation-level).
2. A killed-mid-stage run resumes from the journal, reusing completed agents'
   cached results.
3. The Task-dispatch fallback produces **byte-identical artifacts** — CI parity
   goldens extended from R-0002's review-wave goldens to the stage waves.
4. The Codex CI fixture leg passes with today's outputs and asserts no workflow
   runtime is invoked on Codex hosts.
5. `workflow_available()`/`TASKPLANE_WORKFLOWS` reused, not duplicated;
   kill-switch values (`0/false/no/off`) route to dispatch; path choice traced.
6. Adversarial test reaches every gate with workflows disabled.
7. **No guardrail loosened**: workers submit and never advance state; the
   PreToolUse contract screen governs workflow agents unchanged; per-brief
   TASKPLANE_TASK slots honored; the no-loosening battery stays green.

### Stream 3 · R-0005 — README + onboarding overhaul (WS3)

Honest install paths by account type: **individual** (marketplace/GitHub URL),
**org admin** (Organization settings → Plugins → GitHub sync →
`vdemkiv/taskPlane` → set Available/Required, auto-update note), **org member**
(admin-published marketplace or file-upload only — members cannot install
plugins from GitHub; say so plainly, link the admin section, offer the
personal-account fallback). Per-host quickstarts (Claude Code / Cowork /
Codex). Document the v3 features (routing v2, review wave, decomposition,
stage flows). Truth-up `tp onboard` to print context-matched guidance.
What's-new table stays at exactly 3 rows; CHANGELOG remains authoritative.

Acceptance (mirrors R-0005):
1. No install path in the README is inaccessible to the reader it addresses;
   the member path has zero dead-end instructions (CI grep for the forbidden
   member-installs-from-GitHub claim).
2. Three runnable per-host quickstarts; the Codex quickstart never references
   workflow-only features.
3. Every v3 user-facing flag/command/artifact documented; the existing CI
   docs-drift check covers the new sections.
4. `tp onboard` never instructs an org member to use a flow they don't have;
   fixture tests per detected context.
5. What's-new table pinned at exactly 3 rows (CI/test check).
6. No public copy in the repo implies an org member can self-install from
   GitHub (repo-wide grep check).

### Stream 4 · R-0006 — Routed-review wiring + debt burn-down

Wire routed briefs into **evaluate**; the em step keeps its full-catalog
mandate via the existing audit cadence (`TASKPLANE_AUDIT_EVERY`). Add the
**fixtures-path discount** to detector scoring — kills the i18n/mobile
inflation from fixture-only diffs (D-0002) — and regen parity goldens **only**
via the documented `taskplane/tests/fixtures/briefs/regen.py`. Evaluate the
**routed-audit hybrid** and record the decision (D-0003). **Extract the audit
machinery from loop.py into `taskplane/audit.py` with byte-frozen gate
behavior** (D-0004). D-0002/D-0003/D-0004 are marked addressed-by-R-0006 in
the store (done at spec time) and resolve when this ships.

Acceptance (mirrors R-0006):
1. Evaluate dispatches the routed set (deep + light batch); em full-catalog
   audit cadence and the router-regression sign-off block are pinned by tests.
2. Fixture-only diff no longer routes i18n/mobile deep (negative fixture); a
   real locale-file diff still routes i18n deep (positive fixture) — D-0002.
3. Goldens regenerated via regen.py; the CI parity leg (dispatch byte-identity,
   Codex fixtures) green after regen.
4. Routed-audit hybrid decision recorded in the decision registry with measured
   token/coverage data (adopt with follow-up, or decline with reasons) — D-0003.
5. Audit extraction proven by a differential replay of gate scenarios pre/post
   with identical outcomes and artifacts; loop.py shrinks — D-0004.
6. Debt records D-0002/D-0003/D-0004 linked addressed-by-R-0006, resolved on ship.
7. **No guardrail loosened**: n/a-without-evidence still blocks the em gate,
   router-regression findings still block sign-off, extraction changes zero
   gate outcomes; the no-loosening suite stays green.

## Out of scope

- **No new lens catalog entries** — the catalog stays at 26.
- **No host-specific UI** — dashboard stays host-portable (HEADLINE + widgets +
  HTML fallback); no Claude-only or Codex-only surfaces.
- **No breaking changes to governed-loop semantics** — step names, gate
  ordering, human-gate authority, worker-submits-never-advances, and existing
  `loop.json` compatibility untouched.
- **No change to Codex parity** — the dispatch path's artifacts remain the
  reference; any Codex behavior change is a defect, not a feature.
- WS1 flow-as-data (`taskplane.flow/v1` schema, preset catalog, flow
  recommendation, upward escalation — R-F1..R-F6): later phase. Phase 2 does
  only stage-per-run workflow compilation (the R-W3 scope).
- New scheduled-automation surfaces (R-W4) beyond what exists.
- Marketing/launch copy rewrites beyond install-truth alignment.

## Risks

- **Workflow runtime variance** (host versions, org kill-switches, journal
  drift): mitigated by the mandatory byte-identical dispatch fallback as the
  reference implementation, parity goldens in CI, and the adversarial
  every-gate-without-workflows test.
- **Decomposition precision** (bad clusters → wrong lens maps → silent
  narrowing, the killer risk): mitigated by fail-open widening, the
  `components.yaml` override, per-detector positive/negative fixtures, and the
  Nth-review full-catalog audit auto-filing router regressions.
- **Goldens churn** (the fixtures discount invalidates parity goldens): regen
  only via the documented regen.py, reviewed as a diff; CI parity leg must be
  green post-regen; hand-edited goldens are a finding.
- **loop.py extraction blast radius**: byte-frozen differential replay pre/post
  extraction; the v2.3.1 regression gate runs on every increment of this build.

## Contract handoff (→ Design / Plan)

- `scope_paths`: `taskplane/depgraph.py`, `taskplane/lens.py`,
  `taskplane/lens_signals.py`, `taskplane/loop.py`, `taskplane/tp.py`,
  `taskplane/audit.py` (new), `taskplane/dashboard.py`, `workflows/**`,
  `taskplane/tests/**`, `README.md`, `docs/**`, `.github/**` (CI legs).
- `out_of_scope`: `taskplane/kb.py`, `taskplane/requirements.py`,
  `taskplane/design_contract.py`, `taskplane/regression.py`, lens catalog
  entries, hook/screen enforcement semantics (verify-only, never weakened),
  `PRIVACY`/legal copy.
- `dod.test_command`: `python3 -m unittest discover -s taskplane/tests -q`
  (full suite; the no-loosening battery, parity goldens, and Codex fixture leg
  must be green; suite count only goes up from 954).

Named contracts Phase 2 touches (recorded on the requirement graph):

| Contract | Relation | Stream |
| --- | --- | --- |
| `contract:component-map` | **provides** (NEW) — component nodes: span, edges, lens map, fingerprint | R-0003 |
| `contract:lens-brief` | **changes** — component attribution added; routed briefs into evaluate | R-0003, R-0006 |
| `contract:wave-workflow` | **provides** (NEW) — one-run-per-stage compilation, journal/resume, fallback byte-equivalence | R-0004 |
| `contract:findings-v2` | **consumes** — schema-pinned findings shape unchanged | R-0004, R-0006 |

Dependency spine: R-0003 ← R-0001 · R-0004 ← R-0002 · R-0006 ← R-0001, R-0003 ·
R-0005 ← R-0003, R-0004 (documents them; the install-truth half of R-0005 is
independent and must not be blocked if the feature streams slip).

## Open questions (non-blocking, for Design)

1. Component granularity floor: minimum cluster size before a file group earns
   a component node (avoid one-file components exploding the graph)?
2. Does the evaluate wave's routed set inherit the review budget (5–7, cap 8)
   verbatim, or a tighter build-stage profile? Default: inherit R-0001 budget.
3. Routed-audit hybrid adoption bar: suggest ≥30% audit-cost reduction with
   zero escaped n/a-lens findings on the dogfood corpus; Design to confirm.

## Recommendation

**Design phase required** — cross-module, contract-changing (two new named
contracts, one changed), touches loop.py and enforcement-adjacent routing.
The plan-approval recommendation is this seat's; the final sign-off
recommendation is tp-engineering's; both decisions belong to the human.
