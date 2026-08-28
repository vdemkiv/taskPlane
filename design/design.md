# Design: remediate all 72 deep-EM findings

Status: proposed for human Design approval

Requirement: `R-0002`

Reviewed baseline: `00cd4f2c8183e57b6eae3f0cb6b0c580e00fe085`

Canonical finding inventory: `.em-review/findings.json` (34 high, 28 medium, 10 low)

## Decision

Use a **contract-first, severity-gated remediation train**. First stabilize the
durability, authority, compatibility, and timeout contracts in H1. Once those
interfaces are fixed, execute H2 (production wiring and operating bounds) and
H3 (human-facing, privacy, and terminal truth) as concurrent, pairwise-disjoint
native waves. An independent, fail-closed high gate must prove all 34 high
findings closed at one candidate SHA before any medium-only task is eligible.
M1 and M2 then run concurrently through disjoint owners, followed by one
integration/evaluation pass.

All 10 low findings are companions of the earliest related H2, H3, M1, or M2
owner. There is no low-only tail:

- H2: `L-02` (dependency-neutral glob matcher).
- H3: `L-01` (metadata contrast) and `L-04` (workstation identity minimization).
- M1: `L-06` (development-only Pillow pin) and `L-10` (scoped runtime bindings).
- M2: `L-03`, `L-05`, `L-07`, `L-08`, and `L-09` (graphemes, help, and docs).

The authoritative 72-row finding map is `finding_map` in
`design/contract.json`. It preserves every canonical finding independently,
even where one root fix closes corroborating rows.

The wave and ownership diagram is [visual.html](visual.html).

## Current-state grounding

This is a bounded overlay on the current Python implementation, not a rewrite.
The baseline already contains useful controls that should be repaired and
wired rather than replaced:

- `taskplane/terminal_truth.py` has immutable objects, a CAS head, ordered
  terminal surfaces, fsync helpers, and a coordinator, but the coordinator has
  no supported composition root and its in-memory issuer cannot survive a
  restart.
- `taskplane/delivery_ports.py`, `taskplane/run_store.py`, and
  `taskplane/producer_observation.py` already use prepare/commit-style evidence,
  immutable rows, and reconciliation concepts; their acknowledgement order and
  successor exclusivity are incomplete at crash boundaries.
- `taskplane/taskplane_lite.py` owns the file-level runtime state, knowledge
  migration, read-only command screening, store metadata, and append-only
  trace. It is therefore the single H1 owner for durability, migration,
  read-only mutation safety, audit sanitization, and shared-store metadata.
- `taskplane/dashboard.py` is a server-rendered, self-contained dashboard with
  host-bridge and fallback paths. Its present tab, action, state, message,
  motion, contrast, and truncation behavior share one generated HTML/JS surface
  and need one owner per wave.
- `taskplane/depgraph.py` renders interactive SVG graph nodes;
  `taskplane/graph_primitives.py` and the lens router contain parallel glob
  semantics. The accepted `components.yaml` architecture map is recorded but
  is not an enforced scanner input.
- `taskplane/native_authority.py`, `taskplane/design_sweep.py`, and
  `taskplane/preview_runtime.py` contain production-capable validators or
  entrypoints that are not reached from supported flows. Preview materialization
  fingerprints an unbounded tree even though runtime lifetime/CPU/memory limits
  already exist.
- `taskplane/review.py` and `taskplane/tp.py` provide routing and cost controls,
  but validation-sandbox Git operations can wait indefinitely, transcript
  discovery scans unrelated history, and standalone review does not set a
  token ceiling unless the caller supplies one.
- `.github/workflows/ci.yml` runs the suite but does not enforce lint/strict
  typing and resolves moving test dependencies; some checkout jobs retain
  credentials. `CONTRIBUTING.md` says the runtime is stdlib-only but omits the
  Pillow asset-generation dependency.
- `taskplane/tests/__init__.py` and `taskplane/build_c.py` mutate process-global
  runner/service state. The former is especially broad because import alone
  rewires `tempfile`, environment variables, `shutil`, and `unittest`.
- `design/compatibility.json`, `PRIVACY.md`, `README.md`,
  `docs/loop-design.md`, `docs/onboarding.md`, `docs/cli-reference.md`,
  `.codex-plugin/plugin.json`, and `skills/tp-help/SKILL.md` contain the
  reviewed compatibility, privacy, product, and documentation claims.

These premises are sourced from the exact files above, `specs/spec.md`, the
canonical review, and baseline graph fingerprint
`42b140d0b8125932f316693a7efb2c24f71853f5a3aea822bf7c9558cf6aca0b`.

## Decision drivers and measurable targets

1. **No inventory loss:** machine comparison must return exactly 72 unique ids,
   split 34/28/10, with no missing or extra trace row.
2. **High first:** the high gate consumes 34 independently checkable results at
   one SHA; one missing, open, suppressed, downgraded, or self-attested result
   makes eligibility false.
3. **Crash consistency:** every H1 persistence boundary must pass its positive
   selector and a fault-injection matrix covering every acknowledged write
   boundary; a restart yields either the predecessor or one complete successor,
   never a partial authoritative state.
4. **Bounded work:** standalone review defaults to a finite 25,000,000-token
   ceiling; transcript routing reads only the selected session/run and at most
   64 MiB; preview inventory refuses more than 100,000 entries or 2 GiB of
   regular-file bytes; repository acquisition has one retry owner, at most
   three routine attempts, and an absolute 600-second operation deadline;
   validation-sandbox subprocesses have a 120-second per-command deadline and
   a 600-second preparation deadline.
5. **Accessible truth:** interactive dashboard/graph controls meet their stated
   keyboard and ARIA contracts, asynchronous actions never claim success before
   bridge confirmation, reduced-motion disables nonessential infinite motion,
   and small metadata text has a WCAG AA contrast ratio of at least 4.5:1.
6. **Python quality:** CI supports the documented Python floor (3.10+) and the
   project validation target (3.14), runs the selected linter and strict type
   checker on production modules, permits no unexplained bare suppression, and
   installs a hash-locked test/tool dependency set. Runtime remains stdlib-only;
   Pillow is development/asset-generation only.
7. **Test determinism:** concurrency tests use events/conditions, not sleeps;
   scoped bindings restore in LIFO order for nesting and isolate concurrent
   contexts; importing a test package does not mutate global runner state.
8. **Final proof:** focused selectors run per task; the full
   `python3 -m pytest taskplane/tests -q` suite runs once, after integration, at
   the exact clean candidate SHA.

## Alternatives considered

### A. Contract-first severity train (selected)

Stabilize shared authority/durability contracts in H1, then use file/interface
ownership to fan out H2/H3 and later M1/M2. Add only three narrow supporting
modules: a remediation-trace validator, a dependency-neutral glob matcher, and
a locale/text boundary.

Gains: preserves high-first priority; makes parallelism mechanically safe;
reuses the existing architecture; provides a fail-closed gate and exact
finding traceability; keeps rollback additive. Costs: shared integration joins
remain serial, and broad files such as `dashboard.py` and `taskplane_lite.py`
must have one owner per wave. Revisit when two consecutive releases show that
one broad owner is still the dominant delivery bottleneck after the fixes.

### B. Lens-by-lens remediation

Give each of the 26 review lenses its own task and merge their corrections in
severity order.

Gains: mirrors review authorship and makes lens reporting simple. Costs: the
same production files and contracts would have many simultaneous owners,
corroborating findings would duplicate fixes, and severity would not imply a
safe dependency order. Revisit only if future review artifacts emit a
file/interface ownership partition instead of lens ownership.

### C. One monolithic remediation change

Fix all findings in one branch and validate at the end.

Gains: no intermediate integration protocol. Costs: no safe parallelism,
high-severity evidence is delayed behind docs and cosmetic work, rollback is
all-or-nothing, and one regression obscures 72 obligations. Revisit only for a
small emergency patch with a single root cause; this inventory is not one.

### D. Suppress or defer medium/low findings

Close only the 34 highs and retain the remaining review as backlog.

Gains: shortest high-only path. Costs: directly contradicts `R-0002`, loses
the user's explicit low-companion requirement, and leaves known production and
documentation defects. Revisit only through a new attributed Product scope
decision.

## Proposed modules and contracts

No service, database, queue, or framework is added. Local traversal remains at
depth 3 and stops at named contract/resource nodes.

New narrow modules:

- `taskplane/remediation_trace.py` validates the immutable 72-row map, severity
  counts, candidate-SHA result set, high-gate eligibility, and final closure.
- `taskplane/glob_match.py` provides the dependency-neutral matching primitive
  consumed by graph and lens routing; it imports neither router.
- `taskplane/text_runtime.py` owns locale selection, CLDR-capable plural
  formatting through an optional boundary, deterministic English fallback,
  and grapheme-safe visible truncation. The core runtime must remain usable
  without optional packages.

Named contracts:

- `contract:runtime.durable-state-and-authority`: prepare/durable-commit,
  exclusive successor, restart recovery, migration cutover, and authority
  ownership.
- `contract:delivery.production-wiring`: supported composition roots,
  bounded preview/review/repository execution, and live reachability.
- `contract:quality.review-remediation`: code-quality, boundedness, test,
  documentation, and no-dead-surface closure rules.
- `contract:review.high-closure-gate`: exact-SHA independent closure of all 34
  high rows before medium-only eligibility.
- `contract:dashboard.accessible-truthful-actions`: semantic controls, honest
  action/state feedback, WCAG contrast, reduced motion, and accessible text.
- `contract:i18n.locale-and-grapheme`: locale/plural selection, deterministic
  fallback, grapheme-safe display, and full accessible values.
- `contract:privacy.retention-and-disclosure`: data minimization,
  pseudonymized shared metadata, bounded raw-diff retention, and accurate
  notices.
- `contract:release.compatibility-and-authority`: current/N-1 compatibility,
  release-authority receipt, exact-SHA export, and additive cutover.
- `contract:ci.reproducible-python-quality`: locked CI dependencies, least
  checkout privilege, lint, strict typing, canonical proofs, and dev-only
  asset tooling.
- `contract:runtime.scoped-dependency-binding`: context-scoped, nested and
  concurrent-safe runtime seams with deterministic restoration.
- `contract:docs.generated-truth`: generated CLI/help/docs navigation and
  onboarding claims match supported behavior.
- `resource:review.finding-traceability`: immutable source-to-result map.
- `resource:review.exact-candidate-evidence`: focused and integrated result set
  bound to one candidate SHA.

## Delivery waves and native parallelism

Taskplane supplies dependency and scope intent only. Codex native subagents own
execution. For every ready set, the orchestrator dispatches the exact emitted
identities concurrently, then performs one event-driven wait for that
outstanding set. No Taskplane scheduler, lens-worker runtime, polling loop,
capacity reservation, or execution DAG is introduced.

| Phase | Native-ready lanes | Join / eligibility |
| --- | --- | --- |
| H1 foundation | `H1-A` terminal/CAS; `H1-B` journal/observation; `H1-C` durable store/read-only safety; `H1-D` release compatibility/authority; `H1-E` sandbox deadlines | Pairwise-disjoint leaf scopes; `H1-I` alone owns shared integration and compatibility fixtures. |
| H2 + H3 | H2: `H2-A` CI quality, `H2-B` production roots/preview bounds, `H2-C` review/budget bounds. H3: `H3-A` dashboard controls, `H3-C` privacy/retention, `H3-D` exact-SHA export. `HX-GRAPH` is the one shared owner for H2 architecture/glob and H3 graph keyboard work. | Starts only after `H1-I`. Distinct H2/H3 leaves share no files; `HX-GRAPH` is dispatched once from the combined ready set. `H2-I` and `H3-I` own their respective shared tests/contracts. |
| High gate | `HG-EVAL` independent evaluator only | Requires H1/H2/H3 joins and 34 exact-SHA high results; injected missing/open/self-attested row must fail. |
| M1 + M2 | M1: `M1-A` scanner/design proof, `M1-B` typing/cost, `M1-C` CI/dependencies/assets, `M1-D` proof paths, `M1-E` repository retry, `M1-F` scoped test/runtime. M2: `M2-A` dashboard locale/state, `M2-B` privacy defaults/notices, `M2-C` product/docs/help, `M2-D` deterministic concurrency, `M2-E` priced debt. `MX-DOCS-ARCH` is the one shared owner for loop-design obligations in both branches. | Starts only after `HG-EVAL`. Distinct M1/M2 leaves run together when scopes are disjoint; `MX-DOCS-ARCH` is dispatched once. Each group has one join (`M1-I`, `M2-I`). |
| Final | `FINAL-I` integration, then `FINAL-EVAL` | Reconcile shared boundaries, run all focused evidence, machine-check 72 rows, then run the full suite exactly once. |

`design/contract.json` declares exact file/interface owners and dependencies for
all 72 rows. If Plan discovers that two proposed lanes need the same file,
fixture, schema, public contract, or composition root, it must combine them
under one named owner or add a dependency; it may not dispatch them in the same
ready set.

## High closure gate

`taskplane/remediation_trace.py` consumes:

1. the immutable Design/Plan finding map;
2. the exact candidate Git SHA and clean-tree receipt;
3. one independent result per `H-01` through `H-34`;
4. the focused selector fingerprint and outcome for each row; and
5. the H1/H2/H3 integration receipts.

Eligibility is true only for exactly 34 unique high ids, all `closed`, all on
the same candidate SHA, all selectors passing, no missing/suppressed/downgraded
row, and an evaluator identity different from every owning Build identity. A
mutation test removes or changes one row and proves the gate refuses medium
eligibility.

## Compatibility, migration, and rollback

Durability and evidence changes use expand/contract:

1. readers accept the current durable shape and the new versioned shape;
2. writers emit the new prepared/committed/reconciled shape;
3. restart and crash-injection evidence proves either predecessor or one full
   successor is authoritative;
4. the compatibility matrix tests current and the last released generation;
5. only after mixed-version evidence is green may old writes be retired.

Knowledge-store migration writes a durable manifest and copy-complete marker in
the destination, verifies content, fsyncs files/directories, then atomically
switches the locator. An interrupted copy remains non-authoritative and can be
resumed or discarded. Shared metadata carries repository/public identifiers
and a pseudonymous workspace key; absolute paths and raw workstation identity
remain only in the private locator.

Dashboard, docs, CI, and production-wiring changes are additive until their
focused tests pass. A production surface may be removed only together with its
public claims and callers. Terminal bundles are immutable: rollback never
rewrites an old bundle; it disables the candidate, restores the last green
reader/writer path, and creates a successor at a new SHA. Packaging remains
blocked without current/N-1 compatibility and explicit release-authority
evidence. This Design grants no push, tag, publication, release, or main-merge
authority.

## Failure modes and recovery

- A crash between prepare and commit is detected by the durable-intent/reconcile
  signal; the owning coordinator resumes idempotently within the next startup
  and exposes only predecessor or full successor.
- A split-brain successor is detected by the CAS-lineage signal; the writer
  refuses both competing completion claims and requires operator inspection.
- A migration interruption is detected by the copy-complete/locator signal;
  startup keeps the legacy store authoritative and resumes or removes the
  incomplete destination.
- A bridge rejection is detected by the dashboard action-delivery signal; the
  UI restores the enabled/error state and retains the exact user action.
- A review/preview/repository deadline is detected by the bounded-operation
  signal; the owner cancels/terminates the child operation, records a typed
  timeout, and returns control within the declared deadline.
- A locale resource is missing or malformed is detected by the locale-fallback
  signal; the renderer uses deterministic English without dropping the full
  accessible value.
- A CI lock or hash does not match is detected by the dependency-integrity
  signal; CI stops before tests instead of resolving a moving tree.
- A missing finding result is detected by the remediation-cardinality signal;
  the high or final gate refuses eligibility and names the missing ids.

The corresponding signal strings and actionable alerts are normative in
`design/contract.json`.

## Python structure and test strategy

- Keep protocols at consuming boundaries. `remediation_trace`, glob matching,
  locale/text services, clocks, persistence, subprocess runners, and runtime
  bindings receive dependencies explicitly; untrusted JSON/environment/store
  data is validated at the boundary.
- Keep domain validation separate from CLI/dashboard composition roots. Avoid
  new import-time clients, settings, or service locators.
- Synchronous ownership remains the default. Existing host/event asynchronous
  paths must surface pending/success/failure and propagate cancellation; no new
  unowned background task is introduced.
- Mutable state is protected by file/process locks or context-local bindings;
  no correctness claim relies on the GIL, so the focused suite must also cover
  concurrent access paths suitable for free-threaded Python.
- Public runtime imports remain stdlib-only. Development dependencies are
  hash-locked in a separate group; Pillow is present only there. A clean wheel
  install and package-content inspection prove the boundary.
- Each finding has one named focused selector. Atomicity/authority/privacy/
  timeout findings also have adversarial mutation or fault-injection cases.
  Integration validates each wave join. `HG-EVAL` is independent. Final
  evaluation runs focused selectors and static/accessibility/docs checks, then
  the full suite once.

## Graph DoR and DoD

Design DoR requires the exact baseline fingerprint, complete canonical review,
all 72 mapped rows, named contracts, local-depth 3/contract-only boundary
policy, and no open Product question. Plan DoR must cover every proposed edge,
preserve the owner/dependency partition, carry all 72 finding rows, and prevent
medium-only eligibility before the high gate.

Review DoD compares the realized graph with the approved proposal, checks every
module/edge/contract, requires an empty drift list, machine-compares the 72-row
map to the canonical review, verifies the high gate and final exact-SHA result,
and runs SCC/import/package checks. Any drift returns to Design or requires an
explicit attributed human acceptance; it cannot be explained away in Review.

## Solution-design lens disposition

The proposed HOW is grounded in the cited as-built files; alternatives are
materially distinct; numeric quality bounds and executable evidence are named;
all failure detections correspond to declared signals; migration and terminal
rollback are additive; three new modules have direct acceptance traceability;
all 72 findings have owners and selectors; and Plan can decompose the work
without inventing a contract or scope decision. The lens is self-attested by
this exact Design worker and must be surfaced as such at the human approval
gate, not represented as independent review.

## Open questions

None. Implementation and release authority remain outside this Design gate.
