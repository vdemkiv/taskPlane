# R-0005 Design — Evidence-efficient, graph-complete governed review

Status: proposed

Requirement: R-0005

Baseline graph: `bdf441f3d32db17cb57af6e649b549f2273fb4c05b71f23720f9c9fe51dbd633`

Scanned head: `a2139b1ed93cad6e550712506d780b543d7c4b1b`
Supersedes: R-0004 design binding (the R-0004 compliance kernel remains an invariant)

## Decision

Extend the R-0004 deterministic compliance kernel with a **quality-gated evidence pipeline**. A review has one canonical target, one canonical derivation, one content-addressed full envelope, one routing decision over all 26 lenses, and one monotonic findings revision. It dispatches only the exact deep set plus at most one light-sweep slot. It never substitutes breadth for uncertainty: if the graph plus one bounded caller expansion cannot establish the impact radius, the run stops as `impact_incomplete` with zero lens dispatch.

The host-neutral kernel remains authoritative. Claude and Codex adapters only start bounded native processes and translate transport. They consume byte-identical manifest, routing, view, provenance, and counter records. Normal Review, Evaluate, and final EM use the same kernel. The full catalog remains available only for explicit human `--all` or isolated evaluator calibration and is recorded as such.

This preserves the useful R-0004 instrumentation, absolute workflow assertions, immutable context binding, graph freshness/disposition, orchestrator gate, host authentication boundary, secret-scrubbed model shells, timeouts, process-tree cancellation, and comparable-run policy.

## Measured baseline and target

The optimization baseline is the measured 3.77M-token session: taskPlane 2.36M (63%), lens agents 754k, 52 CLI calls 601k, dashboards 548k, findings/docs 414k, and permanent taskPlane instructions 42k. The comparable frozen PR-9464 replay target is at most 1.18M taskPlane-attributed effective tokens, at most 12 top-level taskPlane CLI invocations, and zero duplicated dashboard HTML. These are not estimates to optimize around silently; the evaluator records them with a comparison key and rejects incomparable token claims.

The comparison key is exact: scenario id, frozen fixture/target id, before and after SHAs, taskPlane version, host, model, reasoning setting, token-telemetry method, and run mode. A mismatch or absent host telemetry yields `not_comparable`; structural efficiency and absolute workflow compliance still gate.

## Alternatives considered

| Alternative | Graph correctness | Read/token cost | Determinism | Decision |
|---|---:|---:|---:|---|
| Extend the R-0004 kernel with graph-quality gating, one bounded caller expansion, envelope/views, selective routing, and canonical revisions | High; uncertainty blocks | Low and measurable | High | **Selected** |
| Build a complete repository-wide language-server call graph before every review | Potentially high | High startup/index cost; weak polyglot portability | Medium | Rejected for normal runs; may be an explicit calibration tool |
| Let each lens retrieve diff/graph/requirements on demand | Inconsistent snapshots and hidden re-derivation | Repeats model reads and CLI work | Low | Rejected |
| Keep module-only routing and hand every lens the full shared envelope | Misses sparse-graph callers | Storage dedupes bytes but not model reads | Medium | Rejected; reproduces PR-9464 risk |
| Score transcripts with a model judge and infer completion | Cannot enforce ordering/provenance | Adds judge cost | Low | Retained only as secondary qualitative evidence, never compliance |

## Canonical records and contracts

All records use canonical JSON (UTF-8, sorted keys, normalized paths, no insignificant whitespace) and SHA-256 fingerprints.

### `TargetRecord`

Pins repository identity, target ref, base/head SHAs, dirty-state digest, host-independent scenario id, graph fingerprint, and `scanned_head`. `target_fingerprint` is immutable for the review.

### `GraphQualityRecord`

Produced before routing from the pinned target and graph. It records:

- scanner support and coverage for every changed source file;
- stale and truncated flags, graph fingerprint, and scanned head;
- unresolved repository-internal edges and boundary-only external edges;
- module-confidence evidence, not just a score;
- changed symbols and caller-coverage status;
- expansion state: `not_needed`, `completed`, `truncated`, `timed_out`, `unsupported`, or `failed`.

Module impact is sufficient without expansion only when every changed source file is covered, the graph is fresh and untruncated, no changed-symbol-relevant internal edge is unresolved, and the evidence-backed module confidence is at least 0.90. Otherwise the kernel performs **exactly one** caller-expansion pass over the canonical snapshot: at most 128 changed symbols, six caller hops, 512 repository-internal caller edges, and 10 seconds. Language adapters implement one common protocol and return callers, contracts, terminals, unresolved edges, truncation, and coverage; production code contains no repository-specific symbol names.

After the pass, confidence is sufficient only if each changed symbol is traced to a declared repository entry point, contract boundary, or proven terminal within the bounds, with no truncation, timeout, stale graph, unsupported changed source, or unresolved relevant internal edge. Otherwise the run records `impact_incomplete`, emits a compact diagnostic manifest, and dispatches zero lenses. There is no fallback to all lenses and no claim of a small radius.

### `RoutingInput` and `RoutingDecision`

One `RoutingInput` is assembled from the exact diff bytes, complete graph impact (dependents, expanded caller paths, boundary contracts, affected/dependent requirements, graph quality and fingerprints), requirement text and acceptance criteria, contract changes, task/change type, runnability, and component evidence. It is created before routing and included in the full envelope.

The signal engine maps every catalog lens to `deep`, `light`, or `n/a`, with evidence and reason. Architecture and security are minimum floors: applicable uncertainty can promote them, and routing/budget/narrow component evidence cannot demote them below their required floor. `solution-design` follows its normal evidence; approved-design conformance is an orchestrator review obligation, not an artificial reason to execute that lens.

The complete 26-lens disposition is persisted and bound to the context fingerprint. Dispatch-set equality is exact:

`expanded(dispatched_slots) == deep_lens_ids union light_lens_ids`

Every deep lens gets one individual slot/view. All light lenses share at most one bounded sweep slot/view. An `n/a` lens gets no brief, no view, and no result slot. Normal Review, Evaluate, and final EM never set `breadth=all`; the full catalog is only explicit human `--all` or isolated evaluator calibration with `routing_mode` recorded distinctly. Total mapper failure is `mapper_unavailable` and dispatches zero. A component-cache failure may use module-signal routing only when the graph-quality record proves that mapping trustworthy.

### `EvidenceEnvelope` and `LensView`

Exactly one content-addressed `EvidenceEnvelope` is written per target/context key, before lens fan-out, using exclusive creation and atomic rename. It contains target, immutable diff, graph quality, complete impact, runnability, requirements/acceptance, contracts, task type, component evidence, routing input/decision, and all constituent digests. `context_fingerprint` is the envelope digest.

Shared storage does **not** eliminate model-read cost. The view builder therefore creates a deterministic `LensView` for each deep slot and one for the light-sweep slot. A view includes only evidence relevant to that lens set: selected diff hunks with stable offsets, directly relevant impact nodes and caller paths, applicable requirements/contracts, runnability, routing reasons, and provenance fields. Required architecture/security-floor evidence and affected requirements/contracts cannot be filtered out. The full envelope remains verifiably accessible by fingerprinted path and indexed byte ranges on demand, but is never pasted into every prompt. A lens may read more from that immutable envelope; it may not run git diff, derive graph impact, or replace canonical facts.

`contract:shared-review-context-v2` defines the envelope/view schema, byte identity, filter determinism, indexed access, and no-rederivation rule. `contract:lens-brief-v2` binds slot, lens ids, target/context/view fingerprints, canonical revision base, output path, result schema, and deadline.

### Slot-authored results and canonical revision

`ReviewStart` allocates an unguessable dispatch lease and exclusive result path for every deep slot and the optional sweep slot. Hooks bind the active agent contract to that slot. A result row includes producer host/session, lease id, slot id, lens id, target/context/view fingerprints, base revision, content digest, and hook-observed write provenance. Sweep rows bind both the sweep slot and their individual lens id. The collector rejects missing results, duplicate lenses, reconstructed orchestrator output, copied or wrong-slot output, reused leases, fingerprint mismatch, and unexpected lenses. This is enforceable provenance, not a claim that model prose is cryptographically authored.

Under a repository lock, `ReviewCollect` validates all expected slots, computes the canonical findings body and `findings_fingerprint`, and appends revision `n+1` with the prior revision link. The report and dashboard are projections from that same immutable findings record. Findings, report, dashboard, and gate must cite exactly `{target_fingerprint, context_fingerprint, findings_fingerprint, revision}`. The current-revision pointer advances only after every projection is written and fingerprinted. Stale, skipped, non-monotonic, or contradictory projections block the orchestrator gate.

`contract:findings-provenance-v1` governs slot provenance and revision identity. `contract:review-efficiency-v1` governs counters, artifact emission, CLI limits, and comparison policy.

## Coarse operations and output discipline

Two coarse APIs replace chatty orchestration:

1. `tp review start` validates target/graph, performs the at-most-once expansion, derives diff/impact/runnability/requirements/contracts once, routes, writes the envelope/views/briefs, allocates leases, and returns a compact start manifest.
2. `tp review collect` validates slot results, closes leases, appends the canonical findings revision, renders report/dashboard once, evaluates readiness, and returns a compact collection manifest.

Internal read-only control substeps are one observation bundle and count once at the top-level CLI boundary. This does not weaken governed work accounting: model-issued work actions, writes outside leased result paths, contract activation, denials, and host tool calls keep their existing budget and hook enforcement. A no-retry standalone review is designed for six taskPlane CLI calls or fewer and must never exceed 12; the counter counts every host-observed top-level `tp.py`/taskPlane CLI process, including subagent processes.

Normal stdout is canonical JSON no larger than 16 KiB and contains status, fingerprints, counters, and artifact references. Full diff, impact, briefs, findings, and HTML are never emitted on stdout. Each large artifact is written once by content/revision key. Hosts with artifact transport receive an attachment reference; other hosts receive a canonical repository-relative path and digest. Any preview is at most 2 KiB and is not the artifact body. Dashboard/graph HTML render caches are keyed by input fingerprints, so one revision produces one file and zero duplicate HTML emission.

Structural counters are: top-level CLI count, emitted bytes, repeated-derivation bytes, dispatched-agent count, prompt-view bytes, artifact-render bytes, duplicate-artifact bytes/count, envelope/view counts, diff/impact derivation counts, and caller-expansion count. Effective tokens are recorded only when the host exposes supported telemetry; otherwise they are unavailable rather than estimated.

## PR-9464 frozen correctness oracle

The evaluator fixture contains frozen repository inputs and an expected graph/routing/finding oracle; it is not imported by production routing. The sparse Go module graph must be augmented from changed symbols so both provisioning and NodeClass-validation callers are present, including:

`EnsureAll → ensureLaunchTemplate → createLaunchTemplate → Bottlerocket.Script → MarshalTOML`

The oracle requires the caller path that shows malformed non-boolean TOML errors escaping the userdata serialization path into the validation controller’s presumed-unreachable error branch, creating reconcile-loop risk. Routing must select the evidence-relevant backend and code-quality lenses while retaining architecture and security floors; the canonical findings must include the known Blocker with no lower severity. Fixture mutations prove that removing the validation caller makes impact incomplete or fails the oracle, and that production contains none of these symbol names.

## Proposed modules and graph edges

- `taskplane/graph_quality.py` — deterministic graph-quality record and bounded language-adapter caller expansion.
- `taskplane/review_evidence.py` — canonical envelope, scoped views, leases, slot provenance, revision ledger, compact manifests, and counters.
- `taskplane/review.py` — coarse start/collect orchestration only; delegates deterministic mechanics.
- `taskplane/lens_signals.py` and `taskplane/lens.py` — consume the single routing input, emit 26 dispositions, exact deep/light dispatch, floors, and mapper failure.
- `taskplane/depgraph.py`, `taskplane/decompose.py`, `taskplane/runnability.py`, `taskplane/target.py` — pure producers called once before routing.
- `taskplane/views.py` and `taskplane/dashboard.py` — render fingerprinted projections once and return references.
- `taskplane/yield_meter.py` — distinguish observation bundles from governed work without hiding either.
- `taskplane/eval_drivers.py`, `taskplane/eval_scenario.py`, `taskplane/eval_rubric.py`, `scripts/eval_record.py`, `scripts/ci_evals.py` — native bounded Claude/Codex runs, canonical run schema, absolute gates, and comparable metrics.
- `evals/frozen-pr-9464/` — immutable fixture, mutations, routing oracle, blocker oracle, and comparison key.
- `skills/tp-engineering`, `skills/tp-go`, `skills/tp-build`, `agents`, and routing/help documentation — describe the same selective kernel, coarse commands, and artifact references.

Contract-only boundaries are: host adapters → canonical run manifest; derivation producers → evidence builder; graph quality → routing; routing → dispatch planner; slot agents → result schema; collector → findings ledger; projections/gate → revision identity. No internal implementation object crosses a host or agent boundary.

## Security, failure, and cancellation

- Resolve all artifact paths relative to a pinned repository root; reject absolute, escaping, symlink, junction/reparse, hard-link alias, and post-validation replacement targets. Use exclusive files, restrictive permissions, atomic rename, canonical digests, and read-back verification.
- The trusted host uses existing CLI authentication to start Claude/Codex. Model-launched shells inherit no secrets and receive only an include-listed environment. Codex keeps saved CLI auth while using a scrubbed model shell; an isolated `CODEX_HOME` is not required unless auth is explicitly seeded.
- Host adapters use process groups/job objects, bounded stdout/stderr, absolute deadlines, graceful stop followed by process-tree kill, and cleanup of active leases. Timeout, cancellation, capability-unavailable, auth failure, and mapper unavailable are named canonical states.
- Claude CLI absence is `capability_unavailable`, not missing implementation. Codex and Claude driver records differ only in transport metadata.
- Graph expansion timeout/truncation/unsupported language and total mapper failure stop before dispatch. A missing/wrong slot blocks collection. An interrupted projection leaves the current-revision pointer unchanged and is safely rerunnable by content key.
- Full-envelope access is read-only and auditable. Scoped views reduce exposure and model reads but do not relax a lens’s ability to inspect the referenced full evidence.

## Observability

Every run emits a compact event sequence with target/context/revision identity, graph-quality reasons, expansion bounds/coverage, routing mode and 26 dispositions, expected/dispatched/collected slot sets, artifact references/digests, provenance failures, structural counters, driver state, timeout/cancellation state, and comparison status. No event contains full source hunks, findings prose, HTML, credentials, or raw host environment.

## Rollout and rollback

1. Land canonical records, graph-quality assessment, fixture, and counters behind `review_kernel_v2=shadow`; compare routing and artifacts without dispatch changes.
2. Enable scoped views and exact selective dispatch for frozen evaluator scenarios; keep R-0004 corpus mandatory.
3. Enable standalone Review, then Evaluate, then final EM for Claude and Codex after byte-parity and PR-9464 gates pass.
4. Make v2 default; retain one release of read-only v1 artifact decoding for audit, not v1 dispatch fallback.

Rollback disables v2 before `review start` and returns to the last released R-0004 kernel. An in-flight v2 review is either collected by v2 or cancelled; it is never silently converted. R-0005 schema additions are additive/content-addressed, and rollback does not rewrite the revision ledger. If v1 cannot meet an active correctness or efficiency floor, reviews fail closed instead of widening to all lenses.

## Graph DoR

- Baseline graph fingerprint and scanned head equal the emitted action binding.
- Every changed implementation module is represented; new graph-quality/evidence/driver/fixture modules and contract-only edges are declared.
- The R-0004 acceptance bundle and all 16 exact R-0005 criteria have executable validation mappings.
- PR-9464 fixture licensing/provenance and frozen SHAs are recorded; its changed symbols are fixture data only.
- Claude/Codex executables may be capability-unavailable, but both adapter contracts and offline transcript fixtures exist.
- No open design question remains.

## Design DoD

- Graph quality is assessed before routing; the one-pass caller expansion and `impact_incomplete` zero-dispatch outcomes are deterministic and tested.
- One immutable envelope, deterministic scoped views, exact 26-lens dispatch, floor preservation, no-rederivation, and slot provenance are mechanically enforced.
- One revision identity binds findings/report/dashboard/gate monotonically.
- Coarse operations, compact output, artifact references, budgets, counters, comparison policy, and no duplicate HTML are executable gates.
- Frozen PR-9464 replay retains the Blocker and meets the comparable 1.18M/12-call/zero-duplicate target.
- The named unchanged R-0004 suite plus focused R-0005 and complete regression suites pass on Claude and Codex or record explicit capability-unavailable where execution is externally impossible.

## Solution-design lens

The design is proportional because it extends the existing compliance kernel and reuse points instead of replacing the CLI or lens system. Correctness uncertainty fails closed; the only expensive derivation is bounded and conditional. The main operational cost is content-addressed artifact lifecycle and language-adapter maintenance, offset by eliminating repeated derivation, over-dispatch, repeated full-context reads, and duplicate rendering. The decision remains reversible at the pre-start feature flag; artifact schemas are additive and auditable. No unresolved question prevents Build.
