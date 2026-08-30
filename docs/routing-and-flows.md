# Routing v2 and governed flows — the v2.4.0 + Phase 2 feature reference

This page documents the user-facing surface added by the v3 routing and
flows streams: intelligent lens routing (R-0001), component decomposition
(R-0003), the review wave and the governed stage waves with their mandatory
Task-dispatch fallback (R-0002/R-0004), the audit cadence, and Evaluate's
direct-evidence judgment (R-0006). Every claim below matches the shipped code;
each feature carries one honest dogfood example from taskplane's own
repository. Environment variables are cross-referenced in
`docs/configuration.md` (the complete table); the lens catalog itself is
generated into `docs/lens-catalog.md` — never hand-edited.

None of this loosens enforcement, and nothing on this page can: incomplete
impact or routing evidence stops before dispatch, workflow rails are
transport-only, and
no gate is reachable only via workflows. Do not look here for a way to
disable a guardrail — there isn't one, by design.

## Repository precondition — before graph and routing

Every flow that names a local path, repository URL, ref, or PR begins with
`tp repository prepare`; `tp review start` invokes it internally. The kernel
normalizes repository identity, acquires/reuses a managed mirror and worktree,
verifies the intended head/base/merge-base/diff, and writes a durable run
manifest before graph work. Missing auth, tools, or storage permission returns
one `needs_user` action. The host asks that exact question and calls
`repository resume` in the same conversation after explicit approval.

Source checkout, run-private state, graph/evidence, lens outputs, and
deliverables use distinct external roots. Only shared project knowledge may
live in `.taskplane-kb/`. Contract activation happens after repository and
graph/routing readiness, so a precondition or `impact_incomplete` refusal
cannot leave a misleading active review contract. Full layout and migration:
[`storage-and-repositories.md`](storage-and-repositories.md).

## Focused stage routing — complete disclosure, bounded execution

Product, Design, Plan, standalone Review, Evaluate, and final engineering
review derive their evidence from one canonical context. The context binds one
target, one canonical change, graph quality and blast radius,
requirements/contracts, DoR/DoD evidence, and runnability. Every routed
Product, Design, Plan, or standalone Review stage emits exactly one evidenced
disposition for all 26 catalog lenses:
`execute_deep`, `execute_light`, `covered_by`, or `not_applicable`. Only the
two `execute_*` dispositions may launch a worker. Missing, duplicate,
unsupported, cyclic, or unevidenced rows fail closed before dispatch.

Product and Design choose the minimum-sufficient focused quick route. Every
non-trivial Plan executes exactly 3–4 quick lenses and records all 26
dispositions. Build, Fix, Evaluate, and final engineering review launch zero
lens workers across success, failure, cancellation, interruption, and handoff.
Evaluate acts only as a direct evidence collector and judge: it creates no lens
route, slots, workers, disposition ledger, lens verdict, retry/invalidation
state, or expanded-route authority. Final engineering review consumes that
direct evidence, adds synthesis and the human sign-off boundary, and launches
no replacement fan-out. `execute_deep` remains in the versioned schema for
compatibility and separately authorized audit/calibration work; normal delivery
is quick-only. D-0014, accepted by `human:vdemkiv`, is the attributed successor
contract for this lens-free Evaluate boundary.

Graph quality runs first. Sparse module evidence gets at most one bounded
changed-symbol caller expansion. In standalone PR Review, remaining graph
uncertainty is recorded and routing continues from the immutable diff with
architecture/security floors; the graph is enrichment, not a substitute for
the PR bytes. In governed Evaluate/EM, insufficient impact remains
`impact_incomplete` and blocks direct judgment; neither stage invokes a lens
mapper. For routed stages, mapper failure produces `mapper_unavailable` and
zero lens dispatch. Neither condition recovers through `breadth=all`.

`tp lens route` uses the signal engine in `taskplane/lens_signals.py` to score
the actual change: paths, content, density, graph impact, requirements, tests,
and unresolved findings. `taskplane/lens_route_policy.py` then canonicalizes
those signals, groups overlapping risks, applies mandatory floors, and produces
the smallest deterministic route plus the complete disposition ledger. A bare
`not_applicable` or `covered_by` row is refused.

Guardrails that hold at every granularity:

- **Bounded focus.** Non-trivial Plan routes contain 3–4 independent evidenced
  risks; Product/Design use the minimum sufficient count.
- **No silent overflow.** More than four independent mandatory Plan risks
  returns zero dispatch and either deterministic scope splits or an
  exact `expanded_approval_required` request.
- **Protected expansion.** An expanded route requires the separately executed,
  content-addressed provider, external 0600 custody, an exact authenticated
  approval, and atomic one-use consumption. A worktree cannot mint or consume
  this authority. Expanded-route authority is Plan-only.
- **Stage boundaries.** Product, Design, and Plan may execute their selected
  quick route. Build, Fix, Evaluate, and final engineering review always
  dispatch zero lenses.
- **Fail closed before dispatch.** Incomplete graph evidence or an unavailable
  applicability mapper emits no briefs. `breadth=all` is reserved for an
  explicit human request or an isolated calibration/audit, never recovery.

Dogfood example (this repository — the reviews that shipped routing v2
routed their own diffs; both full-codebase runs settled on 7 deep):

```bash
python3 taskplane/tp.py lens route --base main
# 26 evidenced dispositions; only execute_deep/execute_light dispatch.
```

Skipped rows remain visible as `covered_by` or evidenced `not_applicable`.

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
the live diff, and focused selection plus mandatory floors run after assembly.
Every routed lens names which component(s) proposed it via
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
- **Lifecycle cleanup is authoritative, not self-certifying.** Codex
  `SubagentStart` binds one pending worker slot; `SubagentStop` terminalizes
  and quarantines it for every terminal outcome. Committed gates and
  SessionStart sweep completed leftovers. None of those lifecycle actions
  replace PreToolUse screening, a worker submission, evaluator evidence, or
  the orchestrator/human gates.

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

## Evaluate and final engineering review are lens-free judges

The loop's `evaluate` step collects and judges direct acceptance, diff, graph,
requirement, contract, and test evidence against one exact target. It launches
zero lenses and creates no route, slots, workers, 26-row disposition ledger,
lens verdict, retry/invalidation record, or expanded-route authority. Its
verdict is direct evaluation evidence, not a synthesis of lens outputs.

Final engineering review consumes the same canonical direct evidence, adds
engineering synthesis, and stops at human sign-off. It also launches zero lens
workers and cannot recreate any artifact that lens-free Evaluate intentionally
does not produce.

## Review convergence and adjudication memory

Canonical collection admits two finite kinds of finding: a `defect` with a
structural trigger/outcome/repro claim, or a `violation` naming a repository
declaration the engine can resolve (requirement, decision, config key, budget,
or language-reference section). Other commentary is recorded as a durable
`note`; it stays measurable but does not enter the findings headline or gate.

The yield ledger's line-independent finding identity is fed back into the next
review as one bounded, file-scoped artifact reference. Human dispositions
`resolved`, `accepted`, `closed`, `deferred`, and `not-a-defect` are settled.
A lens may re-file one only when `recurrence` names materially new evidence,
such as a reverted fix or a changed repro. A frozen two-pass scenario pins the
result: after first-pass findings are settled, an unchanged second pass adds
zero admissible findings.

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
