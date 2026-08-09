# Design — v3 Phase 1: intelligent lens routing + workflow review wave

Anchored requirement: **R-0001** (primary; this loop). **R-0002** (review wave,
depends_on R-0001) is designed here as the same delta because its consumer
contract (`contract:lens-brief`) is shaped by the router redesign — its
acceptance criteria are mapped in the validation table below and in the
contract's `secondary_requirements` block.

## 1. Problem

Lens routing today is filename-level only. `lens.route()`
(taskplane/lens.py:164–274) selects deep lenses from file globs, task type,
artifact type, a `baseline: code` flag, and the graph hub score
(taskplane/lens.py:108–137) — nothing ever reads the *content* of the change.
The em step then mandates `breadth="all"` (taskplane/loop.py:853–856), so all
non-routed lenses sweep regardless of relevance (taskplane/lens.py:248–261):
an i18n lens runs against a stdlib CLI with zero user-facing strings. Measured
dogfood cost: ~1.3–1.5M tokens per full review, a large share on lenses with
no applicable surface. There is also no stage awareness — the catalog
(lenses/catalog.json, 26 lenses) has no notion of which lenses even make sense
at design vs build vs review.

Separately, the Claude host now ships Dynamic Workflows (deterministic,
journaled, resumable, schema-validated fan-out; docs/v3-strategy-flows-
lenses-onboarding.md, addendum). The review wave should use them where
available. **Codex has NO workflow runtime (verified, same addendum)** — the
current Task-based dispatch (`lens.dispatch_briefs`,
taskplane/lens.py:405–480) IS the Codex path and must remain byte-identical.

## 2. Alternatives compared

### A. Detector-per-lens applicability engine — **SELECTED**

A deterministic, stdlib-only detector per lens computes
`{score, evidence[], negative_evidence[]}` from three signal sources
(content, graph, requirement) and the router maps scores to a verdict
`deep | light | n/a-with-negative-evidence`, bounded by a budget and floors.

- Gains: deterministic and explainable (same inputs → same routing, every
  verdict carries evidence); 50–70% projected token reduction with *higher*
  precision; honesty is machine-checkable (n/a requires negative evidence;
  the audit sweep converts any miss into an auto-filed router regression);
  zero new dependencies; testable with plain fixtures.
- Costs: 26 detectors to write and maintain; detector false negatives are a
  new regression surface (mitigated: positive+negative fixtures per detector
  are part of DoD, audit sweep is the backstop); content scanning adds
  bounded I/O per review (<1s per component, enforced by byte/file caps).
- Revisit when: detector maintenance cost exceeds the token savings, or the
  audit loop sustains >0 escapes across releases.

### B. LLM-classifier routing per review — REJECTED

Ask a cheap model, per review, which lenses apply to the diff.

- Gains: no detector code to maintain; adapts to novel content without new
  detectors.
- Costs: **nondeterministic** — same diff can route differently across runs,
  which breaks the audit loop's ground truth, breaks fixture tests, and makes
  the coverage map unverifiable; **costs tokens to save tokens** (a classifier
  pass per review, on every review); negative evidence would be model prose,
  not machine-checkable; violates the standing rule that skipping a lens is
  only legal with machine-checkable negative evidence.
- Revisit when: hosts ship a free deterministic classification primitive with
  reproducible outputs.

### C. Static per-directory lens config — REJECTED

A checked-in map (e.g. `lens-map.yaml`) from directories to lens sets.

- Gains: trivially deterministic and cheap; easy to read.
- Costs: **rots** — the map encodes yesterday's contents, nothing invalidates
  it when code changes (a directory that gains SQL keeps its old lens set);
  **no evidence** — an n/a is a config line, not a checkable claim about the
  code, so the no-silent-skip rule cannot be enforced; per-repo authoring
  burden pushes users to over-broad maps (back to sweeping) or under-broad
  maps (silent narrowing — the killer risk).
- Revisit when: never as the primary mechanism; a config *override* layer on
  top of detectors may be added later if operators need pinning.

## 3. Selected architecture — the applicability engine

### 3.1 New module: `taskplane/lens_signals.py` (pure stdlib)

**Detector interface.** A detector is a pure function
`detect(ctx) -> Signal`:

```
ctx     = {files: [relpath], read: fn(relpath)->str (byte-capped),
           graph: {hub_dependents:int, boundary_contracts:[node],
                   node_kinds:{module:kind}},
           requirement: {acceptance:[str], title:str} | None,
           task_type: str|None}
Signal  = {lens: str, score: float 0..1, evidence: [str],
           negative_evidence: [str]}   # negative_evidence non-empty iff score==0
```

The registry `DETECTORS: {lens_id: detector | "always" | ("inherit", src)}`
covers all 26 catalog ids; a drift test asserts registry keys == catalog ids
(same discipline as `_HARD_LENSES`, taskplane/lens.py:29–33).

**Signal sources (three, per the strategy):**

- *content* — bounded scans over the changed files' text: i18n calls, locale
  files, user-facing string density; UI markup; SQL/migrations; HTTP/queue
  clients; auth/PII markers; concurrency primitives; platform APIs. Bounds:
  first 64KB per file, max 200 files per route, `re` module only, sorted
  iteration — deterministic and <1s per component (perf fixture enforces).
- *graph* — component kind, hub score (`hub_signal`,
  taskplane/lens.py:108–137, reused as-is including its fail-toward-more-
  coverage behavior), boundary contract nodes (`contract:`/`resource:`/
  `svc:`/`ext:` prefixes, taskplane/depgraph.py:273–274).
- *requirement* — acceptance-criteria keyword sets per lens (e.g. "PII",
  "migration", "locale") matched against the anchored requirement record.

**Verdict + evidence model.** For EVERY lens in the active stage profile:
`score >= 0.6 → deep`, `>= 0.2 → light`, else `n/a` — and an n/a verdict
MUST carry the detector's machine-generated negative evidence, e.g.
`"0 i18n signals: no locale files, no i18n imports, no user-facing string
literals in scope"`. A lens with marker `"always"` (e.g. architecture on
code) never goes below its floor.

**Budget, floors, cap.** Deep candidates ranked by score; target 5–7, hard
cap 8; overflow is demoted to `light`, never dropped. Floors applied after
ranking and exempt from demotion below their floor:

- `security` — never n/a when the diff touches enforcement/boundary surfaces
  (hooks/**, auth globs, contract-screen code, or any boundary-contract edge
  in the diff's graph impact); floor = light, strong signal still → deep.
- `architecture` — ≥ light on any code change (today's governance floor,
  taskplane/lens.py:216–219, preserved verbatim and made cheaper).

`--lens <id>` forces a lens to deep regardless of verdict (recorded as
`"deep (forced)"` with the forcing reason). `--breadth all` keeps today's
full-catalog sweep for audits, byte-identical (taskplane/lens.py:248–261).

**Coverage map contract.** The routing decision is a first-class artifact:
`{lens_id: {verdict, score, evidence[] | negative_evidence[]}}` carried in
findings `meta.lens_coverage`. `dashboard.lens_coverage`
(taskplane/dashboard.py:1076–1094) and `render_lens_coverage`
(taskplane/dashboard.py:1097–1128) accept BOTH the legacy
`{id: 'deep'|'sweep'}` shape and the v2 shape (compat: old metas still
render). Every n/a chip renders its reason string. HEADLINE
(taskplane/dashboard.py:201, 804–806) gains the new form
`lenses 6 deep · 3 light · 17 n/a (evidenced) of 26`, pinned by tests; the
legacy deep/sweep segment still renders for legacy metas.

**Router integration (route v2).** `lens.route()` gains
`stage: str | None = None`. When the catalog has `stage_profiles` and a
stage is given, the candidate set is the profile; verdicts come from
`lens_signals`. With `stage=None` or no `stage_profiles` key in the catalog,
behavior is the legacy path unchanged — existing tests keep passing.
`loop.py` em/evaluate wiring (taskplane/loop.py:844–856) passes the stage;
em keeps `breadth="all"` only on audit reviews (every Nth, default 5, or
release), otherwise routed+light. Engine failure inside `lens_signals`
fails toward MORE coverage (fall back to `breadth="all"`, warn on stderr,
trace) — the exact precedent of `hub_signal`'s fail-safe
(taskplane/lens.py:121–137). Never silently narrower.

### 3.2 Stage profiles — data in `lenses/catalog.json`

New top-level key `stage_profiles` (pure data, no code change to add a lens
to a profile):

```
"stage_profiles": {
  "design":  ["solution-design","architecture","tradeoffs","scalability",
              "security","data-safety","services-selection","cost-finops"],
  "build":   ["code-quality","testability","backend","frontend","security"],
  "review":  [ all 26 ids ]
}
```

The router is restricted to the active stage's profile: a design-stage route
can never select `code-quality`. Unknown stage → full catalog (fail open to
more coverage). A catalog drift test asserts every profile id is a real lens
id.

### 3.3 Detector test discipline + audit loop

Every detector ships ≥1 positive and ≥1 negative fixture
(`taskplane/tests/fixtures/detectors/<lens>/{positive,negative}/`): the
positive slice must fire, the negative slice must produce the negative-
evidence string. The i18n negative fixture is taskPlane's own repo surface.
A perf test asserts <1s per component on the largest fixture.

**Audit sweep:** every Nth review (default 5, configurable) or any release
review runs `breadth="all"`; after merge, findings are diffed against the
routing decision — any finding from an n/a-routed lens is auto-filed as a
router regression (`class: regression`, `owner: router`) into the findings
set and the KB. Skipping is never silent, and a detector miss becomes a
tracked defect automatically.

## 4. Workflow review wave (R-0002)

### 4.1 `workflows/review-wave.js` (new module, ships in the plugin)

Consumes the OUTPUT of `lens.dispatch_briefs` (taskplane/lens.py:405–480)
via workflow `args` — the brief set is the **handoff contract
(`contract:lens-brief`)**, produced identically for both paths. Per deep
brief: one `agent()` with (a) the brief's `prompt` VERBATIM — the same text
the Task dispatch path uses, including the per-task slot activation
(`_slot_instr`, taskplane/lens.py:374–385: `export TASKPLANE_TASK=lens-<id>`)
and the CLEAR_ALWAYS finally-block (taskplane/lens.py:360–371) — and (b) a
**schema pinning the findings shape (`contract:findings-v2`)**: severity,
class, file, line, title, scenario, fix + lens id (formalizing the shape in
taskplane/lens.py:397–401). A schema violation retries rather than writing
an invalid findings file. The sweep brief runs as one more `agent()`. Agents
write their own `.em-review/lens-<id>/findings.json` under their read-only
contracts (hooks still fire inside workflow agents — the PreToolUse contract
screen governs them unchanged); the workflow merges and validates into
`.em-review/`. Runs are journaled: stop/resume re-uses completed lenses'
cached results (the v2.3.0 credit-cutoff incident becomes a resume, not a
redo).

### 4.2 Capability detection + MANDATORY dispatch fallback

The engine probes workflow availability (host feature + org toggle — orgs
can disable workflows entirely). Available → the em step's dispatch payload
points at `/taskplane:review-wave` with the briefs as args. Absent, disabled,
or Codex (**no workflow runtime — verified**, docs/v3-strategy-flows-lenses-
onboarding.md addendum) → the IDENTICAL briefs go through today's Task-based
dispatch with zero behavior change. The chosen path is traced
(`review_dispatch_path` event: path + reason). Because both paths consume
the same `contract:lens-brief` payload and the workflow path reuses the
brief prompts byte-for-byte, artifacts are byte-equivalent — fixture-
verified. No gate is reachable only via workflows.

### 4.3 CI parity guard

A CI leg replays frozen brief fixtures through the dispatch path and asserts
the produced artifacts are byte-identical to goldens. A change that breaks
the dispatch path fails CI even when the workflow path passes (Codex parity
guard, R-0002 c4).

## 5. Migration / compatibility

- `breadth="all"` preserved verbatim for audits and `--breadth all` users.
- `--lens` forces any lens regardless of verdict.
- No `stage_profiles` key / `stage=None` → legacy routing byte-identical;
  existing lens/loop/dashboard tests keep passing unchanged.
- Legacy `meta.lens_coverage` shape still renders (dashboard dual-shape).
- The Codex/Task-dispatch path is untouched behavior-wise: same briefs, same
  prompts, same artifacts (CI-guarded).
- No guardrail weakens: n/a requires machine-checkable negative evidence and
  is always visible in the coverage map + HEADLINE; security floor on
  enforcement/boundary diffs; architecture ≥ light on code; engine failure
  fails toward more coverage; the audit sweep auto-files misses.

## 6. Validation map (every acceptance criterion → concrete validation)

| Req | Criterion (abridged) | Design element | Validation |
|---|---|---|---|
| R-0001 | stage profiles as catalog data; router restricted to stage | `stage_profiles` in catalog.json + route v2 `stage` param | test: design-stage route over a code diff never yields code-quality; adding a lens to a profile in a test catalog changes routing with no code change |
| R-0001 | deterministic verdict deep/light/n-a from content+graph+requirement, <1s | `lens_signals.py` detector registry + verdict thresholds | fixture tests per detector; determinism test (two runs, identical output); perf test <1s/component on largest fixture |
| R-0001 | n/a always carries machine-checkable negative evidence | Signal.negative_evidence required when score==0; router refuses evidence-less n/a | unit test: n/a without negative_evidence raises; i18n on taskPlane repo yields the exact negative-evidence string |
| R-0001 | cap 8, overflow→light never dropped; security + architecture floors | budget ranker + floor rules in lens_signals | test: max-signal fixture yields exactly 8 deep, rest light/n-a, none dropped; enforcement-touching diff can never route security n/a; any code diff keeps architecture ≥ light |
| R-0001 | coverage map + HEADLINE all 26 with reasons; meta carries routing; --lens force; --breadth all | coverage-map v2 shape + dashboard dual-shape render + pinned HEADLINE msg | rendered coverage map lists a reason for every n/a chip (test); HEADLINE format pinned; forced lens runs despite n/a (test); breadth=all path byte-identical (existing tests) |
| R-0001 | detector fixtures + audit auto-files n/a-lens findings as router regressions | fixtures/detectors/<lens>/{positive,negative} + audit sweep differ | CI asserts every registry id has both fixtures; audit test with a deliberately-broken detector auto-files a class:regression finding owned by router |
| R-0002 | review-wave.js ships, briefs via args, agent per lens, schema-pinned findings, TASKPLANE_TASK slots | workflows/review-wave.js + contract:lens-brief + contract:findings-v2 | workflow fixture run: one agent per deep brief, slot exported per brief prompt, invalid findings retried by schema, merge lands in .em-review/ |
| R-0002 | workflow-path artifacts byte-equivalent to dispatch-path | shared briefs + verbatim prompts on both paths | fixture: same briefs through both paths → byte-compare artifacts |
| R-0002 | capability detection; fallback zero behavior change; path traced; no workflow-only gate | capability probe + review_dispatch_path trace + shared contract:lens-brief | full review journey test with workflows disabled AND Codex fixture → today's outputs; grep/property test: every gate reachable on dispatch path |
| R-0002 | CI leg: dispatch artifacts stay identical | Codex parity guard job | CI leg replays frozen briefs through dispatch, byte-compares goldens; break dispatch → CI fails |

## 7. Risks, failure modes, observability, rollout

See `design/contract.json` (risks, failure_modes, observability, rollout) —
headline items: detector false negatives (fixtures + audit backstop), silent
narrowing (negative evidence + coverage map + auto-filed regressions),
engine failure (fail open to breadth=all), workflow-path divergence (parity
fixtures + CI guard), rollback = remove `stage_profiles` key + revert (router
falls back to legacy path by design, no data migration).

## 8. Visualization

Skipped deliberately: the material structure is the brief/findings contract
flow, which is fully captured as the proposed graph edges
(`design/contract.json` graph.proposed_edges) and the two tables above; a
rendered diagram would restate the edge list without adding
decision-relevant information.
