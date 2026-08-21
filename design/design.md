# R-0006 Design — Five ratcheted remediation waves

Status: proposed HOW, awaiting the mechanical Design gate and explicit human approval. This is a design-only artifact. It does not implement a wave, mutate the as-built graph, approve itself, push, or release.

## Decision

Deliver R-0006 as five irreversible-quality, reversible-behavior waves. Each wave adds its gate before relying on it, and the next wave cannot start until the prior wave's executable acceptance set is green:

1. seal a fresh baseline and make `quick-only` a machine-readable review policy;
2. restore and ratchet CPython 3.10–3.13 compatibility before any tests or stateful command runs;
3. make graph and CLI degradation visible, put the zero-token corpus in CI, and distinguish local green from pushed-SHA green;
4. land a measured import-cycle ratchet in its own earlier commit, then remove the S1/S2 edges through explicit inputs and one acyclic graph-primitives boundary;
5. add bounded repeated evaluation, executable incomplete-work fixtures, selective corpus graph edges, and the narrowly labelled seeded-failure catch-rate.

The design extends current modules and contracts. It adds four focused product modules (`taskplane/graph_primitives.py`, `taskplane/graph_decomposition.py`, `taskplane/import_cycles.py`, and `taskplane/eval_sampling.py`), an explicit eval impact map, and focused tests. It does not move existing packages, redesign components, consolidate schemas, change workspace/plugin identity, change model-tier policy, add attestation/MCP/OS isolation, or make any other wave-six bet.

## Current-state evidence

The Taskplane current-state inventory supplied to Design is empty, so none of its absence is treated as evidence. The design is instead grounded in the following repository and run sources:

- The active baseline graph has fingerprint `e3e0d5f90de0f6e9d19526997e6899472c8f0430b86cdaec76e91e0cc97010af`, six nodes, five requirement-to-contract edges, no source modules, and no `scanned_head`. Wave 1 must refresh it before any later impact claim.
- `.github/workflows/ci.yml` declares CPython 3.10, 3.11, and 3.12, installs pytest, and runs tests without a preceding repository-wide compile/import step. It has no 3.13 compatibility leg and never invokes `scripts/ci_evals.py --corpus`.
- `taskplane/stage_entities.py:1742-1744` embeds a multiline dictionary in an f-string expression. It fails `python3.11 -m py_compile` with `SyntaxError` and parses on 3.12/3.13 because it relies on PEP 701.
- `taskplane/run_store.py:275-278`, `:728-731`, `:854-857`, and `:987-990` contain four lazy `stage_entities` import seams and catch only `ImportError`; syntax failure is therefore delayed until lineage/object work and is not translated to a Taskplane compatibility error.
- `taskplane/decompose.py:61-65` deliberately fails open per module and returns `stats.degraded` plus `stats.error`; `taskplane/depgraph.py:1423-1434` catches decomposition failure but does not place a complete degradation record into the public scan result.
- `taskplane/tp.py:7161-7203` already provides one user-layer error boundary and preserves tracebacks behind `TASKPLANE_DEBUG`; today only `taskplane_lite.StateError` and a missing Git executable are treated as known clean refusals.
- `taskplane/lens.py:1312-1317` imports `review` for one context-note call. `taskplane/taskplane_lite.py:2431-2441` imports `depgraph` only to derive regression impact. `taskplane/depgraph.py:1424` imports `decompose`, while `taskplane/decompose.py` locally imports `depgraph` and `lens_signals` at several helper seams. These are the explicit-input boundaries wave 4 cuts.
- `scripts/ci_evals.py:406-451` already has a deterministic zero-token corpus scorer and `:1486-1487` routes `--corpus` to it. CI simply does not call it.
- `scripts/eval_skills.py:489-558` records one `native-current` run per skill and `:560-585` offers no repeat or threshold arguments. `scripts/eval_record.py:1395-1462` calls the driver once and freezes one record. The current negative corpus tests workflow misuse, not incomplete work.
- `components.yaml` only contains scanner exclusions. There is no eval-corpus dependency map; `evals/*` data therefore has no selective relationship to the engine it validates.
- `taskplane/retro.py` and `taskplane/dashboard.py` expose evaluation and graph projections but no `seeded-failure catch-rate` contract or zero-sample state.
- The R-0004 design remains content-addressed in Git at revision `1464432a8b20620852ac23831517cdb28bc77206`: `design/design.md` SHA-256 `a5a682b3ebcefb6081b9600423ee0f8307ae0e56b21022626c84b948cabef5d8` and `design/contract.json` SHA-256 `577ae5048ceb0f1047c6cf691e1b4163351a016f99b711419b9be8614263c154`. Replacing the working files for R-0006 does not rewrite those committed blobs. Wave 1 verifies those exact historical objects and the external knowledge tree rather than incorrectly requiring the R-0006 working copy to remain byte-identical to R-0004.

No accepted governing decision was supplied for R-0006. Earlier R-0004 approvals remain historical evidence, not authority to widen this requirement.

## Alternatives considered

### A. Patch each defect independently and merge once

Fix the f-string, print the degradation warning, add a CI line, remove imports, and add repeat flags without a cross-wave contract.

Gains: smallest apparent diff and fewest new records. Costs: it repeats the failure mode in the improvement register—feature work can land between fixes, cycle expectations can be updated after growth, local green can be mistaken for remote green, and evaluation reporting can drift across CLI, Retro, and dashboard. It cannot mechanically prove that the cycle ratchet preceded the cuts or that quick findings never promoted.

Revisit only if R-0006 is narrowed to one isolated defect with no sequencing, graph, CI-SHA, or reporting acceptance criteria.

### B. Five contract-ratcheted waves — selected

Add the narrow evidence/quality contracts first, then make behavior changes behind them. Reuse `preflight`, `depgraph`, the CLI boundary, existing eval recorder/rubric, Retro, dashboard, and the current CI workflow. Introduce only an acyclic graph primitive module and a sampling aggregate module where no incumbent owner can meet the requirement without recreating a cycle.

Gains: every subsequent step is protected by a gate already present; rollback is wave-local; source-of-truth ownership is explicit; all 18 criteria map to executable checks; package and schema redesign stay deferred. Costs: requires multiple commits and temporary additive fields; the baseline/ratchet artifacts must be maintained; live evaluation takes N bounded model calls when explicitly invoked.

Revisit when the five waves are green and the separately approved wave-six architecture decides whether to move packages, centralize schemas, or change trust/isolation boundaries.

### C. Perform the broad package and governance redesign first

Move foundation/kernel/graph/review/orchestration/CLI packages, introduce a schema registry, and solve identity/attestation/isolation while repairing the defects.

Gains: may reduce later migration work and could eliminate more cycles. Costs: it mixes high-risk architecture and security decisions with a release-blocking parse defect, makes regression attribution poor, violates the explicit wave-six deferral, and requires decisions R-0006 does not authorize.

Revisit only under the separate sixth-wave requirement after the compatibility, visibility, cycle, corpus, and sampling ratchets are green.

## Contract ownership

### Existing contracts changed

- `contract:runtime.python-compatibility`: supported runtime is CPython 3.10–3.13; all shipped Python compiles, stage dependencies import at startup, and incompatibility refuses before governed-state mutation.
- `contract:review.collection`: a requirement-bound `quick-only` policy emits quick slots only; substantive quick findings block/return and cannot create a deep promotion.
- `contract:status.run-observability`: graph degradation, CI-SHA proof, evaluation sample state, and seeded-failure metrics have one machine record and text/Markdown/dashboard projections.
- `contract:governance.enforcement-status`: compatibility, graph strictness, cycle bounds, and quick-only dispatch are visible as proven/degraded/refused evidence rather than silent best effort.
- `contract:governance.delivery-authority`: `local_green` and `pushed_green` are distinct; only exact-SHA remote required checks plus zero commits ahead may be described as pushed green.

### Focused contracts provided

- `contract:governance-baseline/v1`: a read-only baseline record binds run id, branch/revision, source-graph fingerprint, active pointer status, prior Git blob identities, and external knowledge-tree fingerprint.
- `contract:graph-scan-quality/v1`: scan output always carries `degraded`, affected modules, reasons, mode, and scanned revision; strict consumers refuse degraded evidence.
- `contract:cli-error-envelope/v1`: known engine errors map to a stable headline, executable recovery, exit class, and optional debug cause.
- `contract:import-cycle-ratchet/v1`: a checked-in file-level SCC inventory permits shrinkage, rejects new cyclic members/edges or larger member/edge/LOC bounds, and records before/after inventories.
- `contract:evaluation-sample/v1`: one bounded sample owns N distinct trial records, pass threshold, observed scenario/model identity, completeness, and evaluation revision.
- `contract:evaluation-corpus-impact/v1`: an explicit eval impact map creates selective corpus-to-engine validation edges and fails degraded when invalid.
- `contract:seeded-failure-catch-rate/v1`: caught seeded failures divided by the declared seeded sample, never a production defect or universal reliability measure.

## Wave 1 — Baseline and quick-only governance

Extend `taskplane/preflight.py` with a read-only baseline projection over incumbent repository/run/storage facts. It emits one canonical record into the external run artifact store. It does not copy or rewrite Design or knowledge evidence.

The authoritative knowledge store is the canonical `knowledge` root resolved from the active workspace locator/project identity (currently `/Users/vdemkiv/.taskplane/projects/github.com-vdemkiv-taskplane-43a0a10bba/knowledge`), never a caller-supplied or discovered lookalike directory. Before cleanup, a `taskplane.knowledge-preservation-manifest/v1` artifact binds repository/project identity, canonical resolved root identity, sorted relative paths, byte counts, per-file SHA-256 digests, explicit exclusions (locks only), and a canonical manifest digest. Baseline verification requires that pre-cleanup manifest identity as input. A missing/moved expected path, byte/digest mismatch, unexpected non-lock entry, root-identity mismatch, empty manifest for a previously non-empty store, or absent trusted pre-cleanup manifest blocks preservation; hashing an empty or wrong tree can never pass. For cleanup that predates R-0006, only a retained immutable audit artifact with the same closed manifest fields is acceptable—otherwise Wave 1 reports preservation as unproven and stops rather than reconstructing a false baseline.

The baseline record also contains the current branch and SHA, fresh governed run id and workspace locator, refreshed graph fingerprint/scanned SHA, absence of an active `plan/**` payload and obsolete run pointer, and Git object ids plus byte digests for prior committed Design files. Preservation means the prior Git objects remain readable with the same bytes and the canonical post-cleanup knowledge manifest matches the trusted pre-cleanup manifest under the closed rules; the new R-0006 Design is a later artifact, not a mutation of R-0004 history.

Record enforcement independently as `status=live|unproven|advisory`, with enforcement evidence id, Codex session id, and a verified hook-path receipt (loaded hook/bridge path, content fingerprint, host observation, and observation time). Only `live` may be rendered as “enforced.” `unproven` blocks mutating wave work by default. A human may convert it only to `advisory` through an attributable record containing actor, reason, exact wave/scope, expiry, and accepted limitations; advisory work remains visibly advisory and never satisfies a live-enforcement claim. The current session's unproven receipt is therefore evidence of non-live enforcement, not permission to describe initialization as enforced.

At the same time, carry `review_policy.depth=quick-only` from R-0006 into routing and collection. `lens.py` may emit only the single quick sweep slot (or equivalent quick slots); `review_progression` returns a substantive concern to correction instead of promoting; `review.py` rejects any `deep.*` slot in a manifest bound to this requirement. Product/Design reasoning effort is not a lens depth and is unaffected.

Wave 1 passes only when the graph is refreshed at the current SHA, the active locator names the fresh run, the authoritative-root pre/post knowledge manifest and prior Design hashes match, no missing/moved/unexpected entry exists, no stale Plan authority is active, enforcement is honestly `live` or has a bounded attributable `advisory` authorization, and a seeded quick finding blocks without a deep dispatch.

## Wave 2 — CPython compatibility before state or tests

First replace the PEP-701-only f-string in `stage_entities.py` with a precomputed request dictionary/fingerprint. Then make `run_store.py` load `stage_entities` at module startup through one dual package/script import seam. Convert `ImportError` or `SyntaxError` at that seam to one named `TaskplaneCompatibilityError` carrying the failing module, supported range, and recovery. Remove all four copied lazy imports at the lineage, commit-summary, put-object, and read-object seams.

The CLI catches that named error at its existing user boundary. Normal mode prints a concise compatibility headline and recovery and exits nonzero; `TASKPLANE_DEBUG=1` re-raises with the traceback. Because the eager import occurs before a RunStore instance or mutation path exists, the deliberate broken-dependency fixture can snapshot run, graph, contract, review, and requirement stores and prove no byte changed.

The CI matrix becomes CPython 3.10, 3.11, 3.12, and 3.13. Every leg runs, in this order:

1. a repository-wide in-memory compile over the exact NUL-safe result of `git ls-files '*.py'`, including tracked `evals/**`, `corpus/**`, scripts, generators, hooks, and tests—not a hand-maintained directory subset;
2. a closed-set import smoke that statically discovers every production import of `stage_entities`, asserts the exact expected set (`run_store.py`, `loop.py`, `stage_migration.py`, and `taskplane_lite.py` around its stage-module seam), imports each consumer, then runs representative version, graph-read, status-read, stage/run-store, and corpus flows in an isolated temporary home;
3. that leg's pytest suite.

Workflow-order tests parse `ci.yml` and seed syntax errors both in `taskplane/` and in a tracked Python sentinel outside the formerly named four directories (under `corpus/`) in disposable checkouts, proving the exact tracked-file compile exits before a sentinel test command runs. Any newly tracked `.py` is included automatically. The plugin remains stdlib-only. No async path, background task, packaging namespace, dependency, or GIL/free-threading claim is added. The Python solution-design reference targets 3.14 language guidance; R-0006's runtime contract deliberately remains 3.10–3.13 and forbids 3.12-only syntax.

## Wave 3 — Visible degradation, clean CLI errors, zero-token CI, pushed-SHA truth

The base Python import scanner in `depgraph.scan` becomes the first producer of structured per-file parse failures, independent of component decomposition. Its record contains file, resolved module, parser, error class, bounded reason, and file fingerprint. When `decompose=False`, these producer failures still set `contract:graph-scan-quality/v1.degraded=true`. When `decompose=True`, decomposition contributes a second named producer section; `depgraph.scan` combines, never overwrites, base-scanner and decomposition failures. `decompose.derive` remains fail-open in normal scanning. `tp graph scan --strict` returns nonzero on any producer failure. Design/Plan readiness and applicable Review/DoD consume the same quality record and refuse degraded evidence; they do not re-derive quality privately. Normal scan remains successful but must print `degraded=true`, every file/module/producer/reason, and the strict recovery command. Fixtures run both with and without decomposition.

`tp.py` keeps one CLI boundary and exports one public `PUBLIC_ENGINE_ERROR_REGISTRY` protocol: each entry binds an exception class to headline, executable recovery, exit class, and debug-cause policy. `KNOWN_ENGINE_ERRORS` is derived from that registry rather than separately authored. A drift fixture asserts exact class-set equality between the exported registry and `KNOWN_ENGINE_ERRORS`, then parameterizes every entry. Normal mode never prints a traceback for registered classes; debug mode re-raises. A separate unexpected-exception fixture proves unregistered defects retain the current exit-70 diagnostic traceback behavior.

Add a credential-empty, no-egress CI job that runs `python3 scripts/ci_evals.py --corpus` under `env -i` with only an explicit non-secret allowlist (`PATH`, isolated `HOME`, locale, and a prepended no-egress `PYTHONPATH`). That path contains a `sitecustomize.py` loaded before scorer imports which makes process-wide socket creation/connect, `create_connection`, and DNS resolution fail and records any attempt. The job first proves an intentional connection and DNS lookup fail, then runs the valid corpus twice and requires byte-identical canonical output, then corrupts one temporary `expected.json` and requires nonzero. Clearing the whole environment rather than naming a few secrets removes model, cloud, proxy, and credential variables. Live model evaluation remains excluded from push/pull-request CI.

Required check names for Python 3.10–3.13 compatibility, graph/CLI fixtures, and the zero-token corpus are bound to `checked_sha`. After an explicit fetch, pushed-green requires exact equality `HEAD == refs/remotes/origin/main == checked_sha`, `origin/main..HEAD` count zero, `HEAD..origin/main` count zero, and every required-check receipt bound to that same SHA. Ahead, behind, diverged, stale-ref, local-only, or receipt-SHA mismatch remains `local_green`/refused and can never be labelled `pushed_green`. No release/tag/publish operation is added in this requirement.

## Wave 4 — Cycle ratchet first, then four edge cuts

This wave has two ordered commits and CI verifies the ordering with full repository history.

### Commit 4A: measurement and ratchet only

Add `taskplane/import_cycles.py`, a stdlib AST/Tarjan file-level scanner, and `taskplane/tests/fixtures/import-cycles.json`. The policy stores every SCC with more than one module, sorted member and internal-edge lists, member count, internal-edge count, physical LOC, and source revision. It permits a known SCC to shrink. It rejects a new cyclic member, a new SCC, or growth above any recorded member/edge/LOC bound. Its failure names affected modules, edges, and measured sizes.

The first checked-in inventory is generated from the tree at the start of wave 4—not copied from the 2.17.14 report. CI runs the checker before structural tests. A history check proves the policy commit exists and passed while all four targeted deferred imports were still present.

### Commit 4B: S1/S2 cuts

Add `taskplane/graph_primitives.py` below `depgraph`, `decompose`, and `lens_signals`, plus a focused `taskplane/graph_decomposition.py` algorithm owner. Graph primitives contains closed graph payload normalization, module-id resolution, floor/quality value records, and consuming-side protocols; it imports none of the higher owners. `graph_decomposition` consumes only graph primitives and contains the mechanically moved decomposition algorithm needed by the compatibility API.

- `depgraph.scan(ws, decompose: bool=False)` keeps its public signature and return semantics. With `decompose=True` it calls `graph_decomposition.derive` (not the compatibility `decompose` module), so direct callers remain valid while `depgraph` no longer imports `decompose`. `tp.py` graph/review paths, `loop.py` refresh paths, and `retro.py` are named production composition roots and retain their current calls; `tp.py` and `retro.py` exercise the `True` path.
- `decompose.derive` becomes a backward-compatible facade over `graph_decomposition.derive`. The algorithm consumes graph resolver/payload and low-level lens signal inputs from `graph_primitives`; `decompose` no longer imports `depgraph` or `lens_signals` in any scope.
- `lens_signals` consumes the same graph-primitives contract. Golden graph/component payloads prove external meaning is byte-equivalent except for the additive quality fields from wave 3.
- `review.py` and the `tp.py` standalone review adapter own `review → lens` context-note composition: every governed `dispatch_briefs` call supplies the already-rendered note (or an explicit empty note when no context exists). `lens_signals` parity probes also pass an explicit note. `lens.py` no longer imports `review`.
- `loop.py` and `tp.py`, the only governed callers of `taskplane_lite.dod_check`, own `host/orchestrator → taskplane_lite` graph-impact composition and pass the exact graph result for non-sparse graphs. `taskplane_lite` no longer imports `depgraph`. Sparse fallback is permitted only when the supplied graph-quality record proves zero modules/edges or missing scan; omission on a non-sparse/complete graph is a DoD error, never an implicit fallback.

After the cuts, regenerate the inventory with the same scanner. The build evidence contains complete before/after SCCs, including the orchestration, lens, and collision/regression/review-evidence/stage-handoff/taskplane-lite cycles. Direct `depgraph.scan(ws, decompose=True)` pre/post goldens prove backward compatibility, and caller audits prove every governed review/DoD composition input is supplied. Only decreases are accepted without a new human-approved Design change. No existing file is moved or renamed.

## Wave 5 — Sampling, incomplete-work evidence, selective corpus impact, catch-rate

Add `taskplane/eval_sampling.py` as the pure aggregate owner. `scripts/eval_skills.py` accepts `--repeat N` (1–100) and `--threshold T` (0.0–1.0), creates a stable sample id, and calls the existing recorder once per `(scenario, observed-model-version, trial-index)`. Each accepted trial has a distinct id and immutable record. Retrying the same exact key reuses a valid record; mismatched or duplicate evidence refuses. Missing/cancelled trials mark the sample incomplete and no pass rate is emitted as a passing result.

A trial passes only when the incumbent evaluation/rubric result is complete and non-blocking. A complete sample reports `pass_count / repeat_count`; ten fake deterministic trials with seven passes report `0.7` and fail threshold `0.9`. Ordinary CI uses fake drivers only. Live sampling is scheduled or explicitly invoked, never part of push/pull-request CI, and persists no credentials or transcript beyond existing bounded evidence.

Extend the frozen evaluation record with closed incomplete-work evidence derived from the requirement, submission, suite result, and diff/test manifest. Add three executable negative fixtures with distinct refusal codes:

- `incomplete.acceptance-coverage`: exactly three of five criteria are evidenced;
- `incomplete.build-error`: a nonzero build result is followed by a completion claim;
- `incomplete.test-deletion`: a failing test is deleted with no approved replacement evidence.

Each fixture must reach the intended completeness check, not fail earlier for ordering. The corpus scorer pins both the refusal code and explanatory evidence.

Add `evals/impact-map.json`, a closed selective mapping from corpus modules to exact engine/evaluator files. Each row has one canonical corpus prefix matching only `evals/<name>` or `evals/negative/<name>` (no wildcard, `..`, symlink escape, or repository root) and 1–32 exact tracked `.py` engine files under `taskplane/` or `scripts/`; the whole map has at most 128 file relations. Directory-only sources and any wildcard/prefix such as `taskplane/**`, `taskplane/`, `scripts/**`, or `**` are invalid. `depgraph` turns each row into `validates` edges; invalid, unmatched, duplicate, escaped, or over-bound entries mark graph scan quality degraded. Fixtures change mapped evaluation/governance files and also unrelated product (`stage_entities.py`), CLI (`tp.py`), and reporting (`dashboard.py`) files to prove no near-catch-all corpus blast radius.

`eval_sampling` computes `contract:seeded-failure-catch-rate/v1` over one explicit counted unit: a unique `(scenario_id, seeded_failure_id, observed_model_id, observed_model_version, trial_index, evaluation_revision)` trial. The denominator is the count of unique complete declared seeded units, the numerator is the subset for which the harness emitted the intended refusal, and `sample_size == denominator` is invariant. Repeated trials of the same scenario count separately only through distinct validated trial indexes; duplicates or mixed model/revision identity refuse. A fixture with five trials of one scenario and three of another, six caught, must report numerator 6, denominator/sample_size 8, rate 0.75. Zero samples produce `status=unavailable`, `reason=zero-sample`, and the label/identity only; numeric fields `numerator`, `denominator`, `sample_size`, `rate`, and `threshold` are omitted rather than set to zero. `retro.py` and `dashboard.py` consume the same record for machine, Markdown, text, and accessible HTML. The label is always `seeded-failure catch-rate`; no projection calls it production defect rate or model reliability.

## Failure, observability, and recovery

Every contract emits one canonical machine record before rendering. Signals include authoritative-root pre/post manifest identity and entry deltas; live/unproven/advisory enforcement plus evidence/session/hook receipt; exact tracked-Python and closed-consumer identities; per-producer/file graph failures in both scan modes; public-registry/known-error equality; fetched HEAD/origin/checked SHA with ahead and behind counts; SCC inventory and direct/caller parity; bounded corpus-impact selectivity; quick-only dispatch depth; eval trial/sample completeness; and counted-unit catch-rate arithmetic. Text and HTML remain usable without color.

Failures are fail-closed at the boundary they protect: a wrong/empty knowledge root, manifest delta, or unproven enforcement stops wave 1 unless an attributable bounded advisory receipt exists; compatibility stops before state/tests; any base-scanner or decomposition producer degradation stops strict readiness/review; CLI registry drift or swallowed unexpected errors fail fixtures; ahead/behind/stale/mismatched CI proof cannot become pushed-green; cycle growth, direct-API drift, or missing governed composition inputs stop merge; broad corpus mappings and inconsistent counted units refuse; incomplete samples cannot pass a threshold; zero seeded samples render unavailable with numeric fields absent; substantive quick findings return to correction without a deep worker.

Recovery is bounded and attributable. The named owner repairs the offending source/evidence and reruns the same deterministic check in both relevant modes; preservation cannot be reconstructed from a post-cleanup empty tree and enforcement cannot be relabelled. Cycle-bound increases, corpus catch-all edges, quick-to-deep promotion, and wave-six scope require a new human-approved Design change rather than an inline waiver.

## Rollout and rollback

Roll out one wave at a time on `main`, with the preceding wave's gates required before the next branch/commit begins. Wave 4 is explicitly two commits: ratchet first, cuts second. Wave 5 fake-driver fixtures land before any scheduled live sample is trusted. The final push is accepted only after the exact remote SHA has every required check green.

Rollback is additive and wave-local:

- Wave 1 authoritative manifest evidence is immutable; disabling its projection does not delete or substitute prior evidence, and unproven/advisory enforcement is never relabelled live. Quick-only remains required for R-0006 and cannot be rolled back to deep promotion inside this requirement.
- Wave 2 can revert the eager import/error adapter only together with the dependent stage changes; exact tracked-Python compilation and the closed four-consumer import smoke remain as the floor.
- Wave 3 may revert rendering while retaining base/decomposition machine graph quality, the public error registry, no-egress corpus CI, and fetched exact-SHA proof; it may never relabel degraded/local/ahead/behind evidence as complete/pushed.
- Wave 4 may revert a cut to the pre-cut tree only if the already-landed ratchet, direct `scan(..., decompose=True)` golden, and governed caller-input audits still pass. The ratchet itself is not removed in this wave.
- Wave 5 can disable live repeat execution while retaining bounded impact mappings, counted trial records, arithmetic/selectivity fixtures, and zero-token fixtures; readers tolerate zero samples only by rendering unavailable with numeric fields omitted. No stored trial or sample is rewritten.

There is no data migration, destructive conversion, external event cutover, or new service. All persistence additions are versioned, additive records. This makes rollback possible without fabricating history.

## Python solution-design application

The Python solution-design reference was read in full and SHA-256 verified as `9ad8935fadef92c06bfbd4338750debdd612a8391a54ba0ba026424edf7db4b7`.

| Concern | Disposition |
| --- | --- |
| Runtime and packaging | Runtime support is CPython 3.10–3.13 despite the reference's 3.14 guidance target. The plugin remains stdlib-only; compile/import checks cover tracked package, script, lens-generator, and hook Python plus clean packaged entry points. |
| Sync/async ownership | All added paths are synchronous. No event loop, task, cancellation, or `ExceptionGroup` contract is introduced. Live trial interruption leaves an incomplete sample and immutable completed trials. |
| Boundary typing | Runtime-validate graph quality, impact-map JSON, trial/sample records, thresholds/repeats, CI proof, and catch-rate inputs. Type annotations never substitute for validation. |
| Framework separation | Pure SCC and sample aggregation live in focused modules; CI/CLI/Retro/dashboard are adapters. No import-time settings, global client, or service locator is introduced. |
| Concurrency and free threading | Trial ids and records are immutable and idempotent; no safety claim relies on the GIL. Existing atomic file/lock boundaries own concurrent persistence. |
| Failure and cleanup | Compatibility/quality/sample failures refuse before governed mutation or reporting success. Temporary fixtures use isolated roots and existing cleanup; no destructive recovery is introduced. |
| Verification | CPython matrix, compile-before-test sentinel, clean entry imports, AST cycle ratchet, graph payload goldens, fake 7/10 trials, incomplete-work corpus, impact selectivity, and projection parity are executable. |

## Graph DoR and DoD

The overlay is bounded to local depth 3, `contract-only` boundaries, contract depth 1, and requirement depth 1. The baseline graph fingerprint is pinned even though its missing source scan is an explicit DoR warning. Wave 1 must refresh it and record the current scanned SHA before Plan/Build can claim source impact.

Design DoR is satisfied because R-0006 has exactly 18 criteria and no open questions; every current premise above cites repository or run evidence; module/contract ownership is named; numeric bounds are fixed; sequencing and rollback are settled; and wave six is explicit out of scope.

Build/Review DoD requires realization evidence for every proposed edge and contract, a refreshed as-built graph, complete acceptance-map execution, before/after SCC inventories, exact-SHA CI proof, quick-only manifests, and zero unexplained design drift. Scanner-invisible contracts and eval validation edges need explicit recorded evidence. Any new module, cycle-bound increase, catch-all corpus mapping, deep lens slot, persistence replacement, or package move returns to Design.

## Deferred debt

R-0006 intentionally leaves the remaining measured SCCs in place and adds a small explicit input seam rather than performing the package/component redesign. Record this after approval:

`tp req debt "Resolve residual post-R-0006 import cycles in the separate wave-six architecture" --req R-0006 --reason "R-0006 ratchets and shrinks cycles but package moves and orchestration ownership are explicitly deferred" --follow-up "Approve the wave-six package/component design, then reduce every retained SCC without raising the ratchet" --files "taskplane/**/*.py"`

No other design question remains open.
