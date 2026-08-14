# Routing v2 and governed flows — the v2.4.0 + Phase 2 feature reference

This page documents the user-facing surface added by the v3 routing and
flows streams: intelligent lens routing (R-0001), component decomposition
(R-0003), the review wave and the governed stage waves with their mandatory
Task-dispatch fallback (R-0002/R-0004), the audit cadence, and evaluate's
build-stage routing (R-0006). Every claim below matches the shipped code;
each feature carries one honest dogfood example from taskplane's own
repository. Environment variables are cross-referenced in
`docs/configuration.md` (the complete table); the lens catalog itself is
generated into `docs/lens-catalog.md` — never hand-edited.

None of this loosens enforcement, and nothing on this page can: incomplete
impact or routing evidence stops before dispatch, workflow rails are
transport-only, and
no gate is reachable only via workflows. Do not look here for a way to
disable a guardrail — there isn't one, by design.

## Routing v2 — the diff picks the reviewers, with evidence

Normal standalone Review, per-task Evaluate, and final engineering review all
start from the same canonical routing decision inside one canonical review
context. The context binds one target, one canonical diff, graph quality and
blast radius, requirements/contracts and DoR/DoD evidence, and runnability.
All 26 lenses receive a disposition; only `deep` slots plus at most one
bounded `light` sweep receive briefs. No lens receives permission to derive
its own diff, graph impact, routing decision, or runnability result.

Graph quality runs first. Sparse module evidence gets at most one bounded
changed-symbol caller expansion. Stale, truncated, unresolved, or still
insufficient coverage produces `impact_incomplete` and zero lens dispatch.
Mapper failure likewise produces `mapper_unavailable` and zero lens dispatch.
Neither condition recovers through `breadth=all`.

`tp lens route` selects lenses for a change. Since v2.4.0 the selection is
a **signal engine** (`taskplane/lens_signals.py`), not a filename glob
match: each catalog lens is scored against the actual diff — paths,
content, density, and the dependency graph — and receives one of three
verdicts:

- `deep` — a governed read-only lens agent reviews the change in depth;
- `light` — a quick sweep pass;
- `n/a` — **only with stated negative evidence** (e.g. "0 i18n markers in
  the diff"). A bare, unevidenced `n/a` is refused; at the final (`em`)
  review gate, bare `n/a` coverage blocks sign-off.

The routing decision is honest and inspectable: the dashboard coverage map
and the `HEADLINE:` line report `N deep · M light · K n/a (evidenced)`
across the full catalog, and the machine-readable `routing_decision`
object rides on every dispatched brief.

Guardrails that hold at every granularity:

- **Cap-8, demote-never-drop.** The deep set is hard-capped at 8; overflow
  is demoted to `light` (with the demotion recorded in the evidence) —
  never dropped from the review.
- **Floors after the budget.** `security` may not be `n/a` when the diff
  touches enforcement/boundary surface; `architecture` runs at least
  `light` on any code change. Floors are applied *after* budget capping,
  so a cap can never squeeze them out.
- **Forced lenses.** `tp lens route --only <ids>` runs the named lenses
  deep regardless of the engine's verdict — the evidence records the
  force. The force holds at component granularity too.
- **Stage profiles.** `lenses/catalog.json` carries `stage_profiles`
  (design 8 · build 5 · review 26); a stage restricts the *candidate* set
  only. An unknown or absent stage uses the explicit `fail-open` policy and
  widens to the full catalog.
- **Fail closed before dispatch.** Incomplete graph evidence or an unavailable
  applicability mapper emits no briefs. `breadth=all` is reserved for an
  explicit human request or an isolated calibration/audit, never recovery.

Dogfood example (this repository — the reviews that shipped routing v2
routed their own diffs; both full-codebase runs settled on 7 deep):

```bash
python3 taskplane/tp.py lens route --base main
# 26 dispositions for N files changed; only deep + light dispatch:
#   ▸ subagent  security       ← ...
#   · inline    performance    ← ...
#   ○ n/a       i18n           ← n/a: 0 i18n markers in the diff
```

The `○ n/a` rows are the coverage honesty: skips stay visible, each with
its negative evidence.

## Component decomposition — `tp graph scan --decompose`

Directory-level modules are too coarse for routing: one 3,000-line file
can contain a renderer, a store, and a CLI. `tp graph scan --decompose`
derives a **component layer** stored under the top-level `components` key
of `graph.json` (contract:component-map). Derivation is hybrid:

- directory convention — files grouped by sub-directory under their
  module;
- import/reference cohesion — root-level loners join the cluster they
  import;
- AST symbol clustering — a Python file of 600+ lines is split by
  top-level def/class groups sharing a name prefix (`render_*`, `db_*`).

Floors (defaults; override via a repo-root `components.yaml`): a module
decomposes at >= 8 code files or a >= 600-line file; a file cluster earns
a component at >= 2 files; an intra-file symbol cluster needs >= 4
top-level symbols spanning >= 120 lines. Everything below a floor folds
into the residual `<module>::core`. `components.yaml` is optional,
stdlib-parsed (flat `floors:` mapping only), and a malformed file fails
OPEN to the defaults:

```yaml
floors:
  candidate_min_files: 8
  big_file_lines: 600
  cluster_min_files: 2
  cluster_min_symbols: 4
  cluster_min_lines: 120
```

Each component node carries a content fingerprint and a **cached lens
map** computed by the same shipped signal engine over the component's file
span. The cache is fingerprint-gated: re-running `tp graph scan
--decompose` with no content changes is a no-op (zero recomputes,
`graph.json` bytes unchanged); touching one component recomputes only that
component's map. A plain `tp graph scan` never invokes decomposition and
never disturbs an existing layer.

Routing consumes the layer as a **capped union**: changed files map to
touched components, the candidate lens set is the union of their cached
maps — but cached maps only *propose*; final verdicts are re-evidenced on
the live diff, and the cap-8 budget and security/architecture floors run
after assembly. Every routed lens names which component(s) proposed it via
`component_attribution`, which rides additively on each `contract:lens-brief`
and into the findings meta. The dashboard graph view renders component
nodes inside their module grouping.

**Component degradation does not authorize broad dispatch.** A missing, stale,
or corrupt component layer falls back only to the module-level signal engine
(traced `component_layer_failed`). If that engine or its graph-quality input
cannot produce a complete decision, routing stops with zero briefs. It never
falls through to legacy `breadth=all`. Component derivation itself may degrade
a module to one `::core` component, but that evidence remains visible.

Dogfood example (this repository):

```bash
python3 taskplane/tp.py graph scan --decompose
# {"modules": ..., "edges": ..., "files": ..., "components": N, ...}
```

On taskplane's own tree, `taskplane/dashboard.py` (3,000+ lines) yields
three or more components with distinct dependency sets — pinned by
`taskplane/tests/test_decompose.py`, so the example cannot silently rot.

## Fixture-path discount (D-0002)

Checked-in test fixtures look like the surfaces they imitate: a locale
file under `tests/fixtures/` used to score like a real locale file and
inflate i18n/mobile to deep. Signal hits whose ONLY support is
fixture-class paths (any `fixtures`/`testdata`/`goldens` path segment, or
a `.golden` extension) are re-weighted x0.25 — **never suppressed**: the
evidence line survives and names the discount
(`(fixture-path discount x0.25)`), `n/a` semantics are untouched, and the
floors still apply after scoring. A hit with any real product-file support
keeps full weight.

Dogfood example: a diff touching only `taskplane/tests/fixtures/` no
longer inflates i18n/mobile to deep on this repo; the surviving evidence
line names the discount. Pinned by
`taskplane/tests/test_lens_signals.py` (discount named in evidence, real
product-file support keeps full weight, n/a semantics untouched).

## The review wave and the stage waves — one workflow run, mandatory fallback

Reviews and the governed loop stages can each dispatch as **one journaled
Claude Dynamic Workflow run** (resumable: a killed run re-uses completed
agents' cached results). Four workflow files ship, all on the same
pattern — deterministic, schema-pinned receipts, transport-only:

| Stage | Workflow file | Emitting CLI |
| --- | --- | --- |
| engineering review | `workflows/review-wave.js` | `tp lens dispatch --emit auto\|workflow\|task` |
| execute wave | `workflows/execute-wave.js` | `tp loop wave --emit auto\|workflow\|task` |
| evaluate | `workflows/evaluate-wave.js` | `tp loop next --emit auto\|workflow\|task` |
| fix | `workflows/fix-wave.js` | `tp loop next --emit auto\|workflow\|task` |

The rules that make this safe:

- **Claude/Codex semantic parity.** The Task path is mandatory. With `--emit task`
  (or whenever no workflow runtime is detected) the CLI prints today's
  Task-dispatch payload byte-for-byte — the reference implementation,
  pinned by CI parity goldens (regenerated only via
  `taskplane/tests/fixtures/briefs/regen.py`). The workflow path wraps the
  *unmodified* payload as a single `workflow {name, args}` invocation;
  agent prompts are consumed verbatim on both rails. Both hosts consume the
  same canonical context/view fingerprints, routing decision, leases,
  provenance rules, DoR/DoD gates, and artifact-by-reference records. Only
  dispatch and artifact-delivery transport differ.
- **Codex uses native subagent tasks.** Codex has no Claude Dynamic Workflow
  runtime; on Codex hosts (`CODEX_HOME`/`CODEX_THREAD_ID` present) the
  portable task payload is ALWAYS chosen and no workflow opt-in can override
  that. Each brief carries a collision-safe `task_name`, taskplane role and
  exact `role_marker`, absolute `role_instructions` file path, optional model and
  tier-derived `reasoning_effort`; execute-wave emission registers every one
  of those expected identities before spawn. Independent briefs may fan out
  concurrently, but the driver waits in bounded intervals and collects every
  requested result before synthesis.
- **Kill-switch, all conventional spellings.** `TASKPLANE_WORKFLOWS` set
  to any of `0`, `false`, `no`, `off` disables the workflow path
  everywhere; `1`, `true`, `yes`, `on` opts in explicitly; unset falls
  back to the `CLAUDE_CODE_WORKFLOWS` runtime marker, else the
  conservative default: Task path. Detection lives in ONE function
  (`workflow_available()` in `taskplane/tp.py`) — no second parse.
- **Traced, both rails.** The chosen path and reason land in the trace as
  `review_dispatch_path` (review) and `stage_dispatch_path` (stages) —
  never printed on the Task path's stdout.
- **Gates stay at conversation level.** A generated run contains agents,
  never an approval step: at most one workflow run per stage *between*
  human gates. Workers inside workflow agents still claim per-task
  contract slots (`TASKPLANE_TASK`), are screened by the PreToolUse hook
  unchanged, and submit evidence without ever advancing loop state. Every
  gate is reachable with workflows disabled (adversarial-tested).
- **Lifecycle is observable, not self-certifying.** Codex
  `SubagentStart`/`SubagentStop` hooks trace the agent lifecycle and inject
  bounded active-contract context. They never replace PreToolUse screening,
  a worker submission, evaluator evidence, or the orchestrator/human gates.

Dogfood example (this repository, forcing each rail):

```bash
TASKPLANE_WORKFLOWS=off python3 taskplane/tp.py lens dispatch --base main --emit auto
# task-path payload, byte-identical to pre-workflow output;
# trace: review_dispatch_path {path: "task", reason: "disabled by TASKPLANE_WORKFLOWS=off"}
python3 taskplane/tp.py lens dispatch --base main --emit workflow
# same payload wrapped as {"workflow": {"name": "review-wave", "args": ...}}
```

## Audit cadence — the router is itself reviewed

Routing that skips lenses must be audited, so every Nth completed
engineering review (default 5, `TASKPLANE_AUDIT_EVERY`, floor 1 — a
garbage value falls back to the default) runs the **full-catalog audit
sweep** (`breadth=all`). Its findings are diffed against the review's
routing decision: any finding from a lens the router marked `n/a` is
auto-filed as a **router regression** that blocks sign-off until resolved.
The cadence variable tunes only how OFTEN the audit runs; the audit itself
and the auto-filing cannot be disabled through it. The machinery lives in
`taskplane/audit.py` (extracted from `loop.py` in Phase 2 under a
byte-frozen differential — behavior identical by construction). A routed
hybrid audit (skipping evidenced-n/a lenses in the sweep) was measured
against a >=30%-reduction / zero-escape bar and **declined** (D-0003):
the sweep stays full-catalog.

Dogfood example: `TASKPLANE_AUDIT_EVERY=1` makes EVERY review carry the
sweep — useful while tuning a new repo's routing signals. taskplane's own
loop runs under the default cadence; the sweep, the auto-filing, and the
byte-frozen extraction are pinned by
`taskplane/tests/test_audit_sweep.py` and
`taskplane/tests/test_audit_extraction.py`.

## Evaluate and final engineering review use one selective kernel

The loop's `evaluate` step and final engineering review consume the same
canonical review context and complete 26-lens disposition. Stage and persona
change which signals are relevant, not the evidence source: both dispatch
exactly their mapped deep lenses plus at most one light sweep, retain the
architecture/security floors, and keep every n/a backed by negative evidence.
The final engineering review adds synthesis and human sign-off; it does not
re-read the repository or broaden to all lenses.

The canonical Evaluate routing input records `stage="build"`; final
engineering review records the review stage. This distinction changes the
stage profile, never the shared diff, graph impact, or evidence identity.

## Go, Python, and TypeScript references are workflow inputs

Language guidance is attached after lens applicability is decided, so it
cannot widen the review. Detection uses changed source files and bounded
workspace manifests; generic Build scopes receive one representative path per
detected language, while Design also uses the requirement's actual context
files. The same content-bound records then travel through serial Execute/Fix,
parallel waves, Design, Evaluate, and final engineering review.

Each record identifies its owning lens, plugin-relative path, optional section,
and SHA-256. Workers resolve it against the plugin root, verify the digest, and
read only the named section. Selective reviewers must return the exact records
as `references_applied`; collection rejects an omitted or changed path,
section, or digest before canonical revision commit. Reference bodies remain
on disk and are not copied into every brief.

The language layer complements rather than replaces executable evidence:

- Go imports and declared module paths feed the dependency graph; runnability
  probes `go list ./...` once and states whether `go test ./...` can start.
- Python imports feed the graph and regression-radius discovery; runnability
  verifies that the checkout's Python/pytest path is available.
- TypeScript/JavaScript imports feed the graph; `tsconfig.json` activates a
  first-class local TypeScript compiler probe, while `package.json` retains the
  generic Node/npm check.

Canonical sources and the pinned upstream Go reference revision are recorded
in `lenses/references/SOURCES.md`. Missing reference files or named sections
fail closed at routing time rather than silently falling back to model memory.
