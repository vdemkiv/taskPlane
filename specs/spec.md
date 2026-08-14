# Specification — R-0005 complete and efficient governed reviews

Requirement: **R-0005 — Make governed reviews provably complete and materially
cheaper**. R-0005 supersedes the R-0004 product artifact, depends on R-0004,
and preserves every R-0004 acceptance requirement while adding graph-quality,
provenance, artifact, and measured efficiency guarantees.

## Problem

taskPlane must prevent models from skipping contracts, DoR, graph blast radius,
shared context, DoD, evidence, and human gates. It must also avoid enforcing
those guarantees through repeated derivation, oversized prompt duplication, or
full-catalog review fan-out. A review is successful only when its impact is
provably complete, its findings are independently attributable, and its
structural and comparable token costs stay within explicit bounds without
losing defects.

## Users and context

The primary users are heavy Claude and Codex users who repeatedly design,
build, implement, and review new or existing code. They need four independent
answers:

1. Did the model follow the governed workflow?
2. Did graph evidence cover the actual behavioral blast radius?
3. Did each required lens independently produce its own result?
4. Did taskPlane avoid structurally duplicated work, and—when comparable—use
   materially fewer effective tokens?

No efficiency result may compensate for a workflow, impact, provenance, or
finding-quality failure.

## Measured baseline and target

The measured reference review consumed **3.77M session effective tokens**.
taskPlane-attributed activity consumed **2.36M effective tokens (63%)**:

- four lens agents: **754k**;
- 52 taskPlane CLI calls: **601k**;
- dashboards and graphs: **548k**;
- findings and skill documentation: **414k**.

taskPlane permanent instructions accounted for only **42k**. Claude standing
instructions are outside plugin control and are not attributed to taskPlane.
The product opportunity is therefore orchestration, artifact, context, and
dispatch efficiency—not weakening the permanent guardrails.

The frozen PR-9464 replay is the comparable acceptance workload. A compliant
no-retry replay must use at most **1.18M taskPlane-attributed effective tokens**,
at most **12 top-level taskPlane CLI invocations**, and **zero duplicate HTML
emissions**. It must preserve the known **blocking NodeClass-validation
reconcile-loop regression** with no loss or severity reduction.

## Efficiency model

### Absolute structural efficiency

Structural efficiency gates every run, even when token telemetry is absent:

- one immutable full context envelope per target snapshot;
- one canonical diff and one impact derivation for each immutable input key;
- no lens-side re-derivation;
- exactly the mapped lens dispatch set, with at most one bounded light sweep;
- at most 12 top-level taskPlane CLI calls for a successful no-retry standalone
  review;
- read-only observation bundled without repeatedly consuming governed work
  actions;
- each large artifact written and fingerprinted once, then delivered by
  reference where the host supports artifacts;
- zero duplicate dashboard or graph HTML emission.

Failure of a structural invariant is an absolute compliance failure.

### Comparable token efficiency

Effective-token comparison is valid only when workload and cohort keys match:
scenario, frozen fixture/target, start and evaluated SHAs, taskPlane version,
host, model, reasoning effort, telemetry method, and run mode. A comparable
PR-9464 replay must meet the 1.18M taskPlane-attributed target. Missing token
telemetry or mismatched keys produces `not_comparable`; structural efficiency
and workflow compliance still gate absolutely.

## Preserved R-0004 requirements

R-0005 preserves these requirements explicitly for both Claude and Codex:

1. A native Codex out-of-band driver runs `codex exec` in a disposable fixture,
   captures JSONL and final output, proves the taskPlane hook fired, and records
   host, model, reasoning effort, plugin version, scenario fingerprint, and
   repository SHAs.
2. A Claude out-of-band driver uses the same interface and canonical schema;
   unavailable Claude capability is a named unavailable result, never pass or
   broken-run ambiguity.
3. Missing, out-of-order, self-approved, failed-DoR, or failed-DoD stages make a
   run ineligible; repository state and evidence—not model prose—decide
   completion.
4. Graph `scanned_head` matches the evaluated head; impact exists before
   implementation and review; every impacted node and affected requirement has
   an evidence-backed disposition.
5. The same immutable input has at most one diff and one impact derivation;
   shared context exists before lens dispatch; every brief cites the same
   fingerprint; no lens runs diff or graph impact itself.
6. Normal per-task Evaluate, final engineering review, and standalone Review
   map applicability before dispatch from the canonical changed-file diff, full
   dependency impact including dependents and affected requirements,
   requirement and acceptance text, and declared task/change type.
7. Only `deep` and `light` lenses receive briefs: deep lenses receive individual
   agents, light lenses may share one bounded sweep, and every `n/a` lens has
   machine-checkable negative evidence and no brief.
8. Normal governed delivery never requests `breadth=all`; mapper failure blocks
   before dispatch rather than running all 26. Full-catalog execution is limited
   to explicit human `--all` or isolated evaluator calibration outside delivery.
9. Architecture maps `deep` for structural or contract-boundary changes and at
   least `light` for ordinary code changes; security maps at least `light` for
   enforcement or trust-boundary changes. Component narrowing and agent budget
   cannot demote either floor.
10. Dispatched lens ids equal exactly mapped `deep + light`, contain no
    duplicates, and all briefs and findings cite the shared-context fingerprint.
11. Codex `exec_command`/`cmd` has the same read-only, scope, deny, trace,
    release, completion, and derivation behavior as Claude `Bash`/`command`.
12. A first live baseline is eligible only when every absolute invariant passes;
    failed or no-evidence invariants can never be baselined as acceptable.
13. External runs are bounded, cancellable, credential-safe, and report missing
    host capability explicitly.
14. Focused evaluator, routing, Codex compatibility, regression, and complete
    suites pass, and generated Evaluation DoR names only external blockers.

## Complete routing input and graph quality

Before any lens mapping, one full immutable envelope records:

- canonical diff and changed symbols;
- full dependency impact, including dependents and affected requirements;
- graph fingerprint and `scanned_head`;
- scanner coverage for relevant languages and files;
- unresolved internal edges, stale/truncated status, and module confidence;
- changed-symbol caller coverage and bounded-expansion status;
- runnability evidence;
- requirement, acceptance criteria, declared task/change type, contract ids,
  target SHA, and applicable DoR/DoD evidence.

When module impact is insufficient, taskPlane performs exactly one bounded
changed-symbol caller expansion from the canonical snapshot. Its callers and
contracts become part of the same envelope before routing. The bound and any
unresolved callers are explicit evidence; the system must not imply complete
coverage from a sparse module graph.

If module impact plus bounded caller expansion cannot establish sufficient
confidence, review terminates as `impact_incomplete` and dispatches zero lenses.
It does not silently run all lenses or claim a small radius.

For PR-9464, graph evidence must follow both the provisioning callers and the
NodeClass-validation callers through the changed userdata serialization path,
even when module-level graph edges are sparse. The known reconcile-loop defect
is the frozen correctness oracle.

## Shared envelope, scoped views, and provenance

The full envelope is immutable and has a canonical context fingerprint. Each
dispatched slot receives a deterministic scoped view derived from that envelope
and identified by both envelope and view fingerprints. Views may omit irrelevant
payload, but cannot alter shared facts or hide affected requirements/contracts.
Claude and Codex receive byte-equivalent canonical manifests, routing decisions,
views, provenance requirements, and efficiency counters; only transport differs.

Each dispatched lens slot authors its own fingerprint-bound result. The result
binds lens id, slot id, target fingerprint, full-context fingerprint, scoped-view
fingerprint, and canonical artifact revision. Missing, reconstructed, copied,
wrong-slot, duplicate, or mismatched results fail provenance and cannot satisfy
review completion.

Findings, report, dashboard, and gate cite one target fingerprint, context
fingerprint, findings fingerprint, and monotonically increasing canonical
revision. A new revision supersedes earlier summaries without rewriting history.
Stale, contradictory, or mixed-revision artifacts block rather than allowing a
plausible prose summary to pass.

Large diff, impact, view, brief, findings, dashboard, graph, or HTML payloads are
written once, fingerprinted, and delivered by artifact reference where supported.
Normal orchestration output is a compact manifest plus paths/fingerprints—not
repeated bodies. Hosts without artifact transport may use a bounded equivalent,
but must preserve canonical bytes and report transport mode.

## In scope

- R-0004 cross-host workflow, selective routing, command parity, baseline, and
  external-run guarantees.
- Graph-quality evidence and bounded changed-symbol caller expansion.
- Immutable shared context, deterministic scoped views, lens provenance, and
  canonical artifact revisions.
- Compact orchestration manifests, artifact-by-reference, structural counters,
  and comparable token accounting.
- Frozen PR-9464 correctness and efficiency replay.

## Out of scope

- Version, manifest, release, tag, marketplace submission, or package changes.
- Weakening or bypassing contracts, DoR, DoD, graph evidence, lens floors,
  provenance, final engineering judgment, or human approval.
- Changing the 26-lens catalog or substituting full-catalog fan-out for impact
  confidence.
- Provider ranking, production billing prediction, or comparing unmatched host,
  model, fixture, telemetry, or repository cohorts.
- Optimizing Claude standing instructions or provider-controlled prompts outside
  taskPlane.
- Treating reduced tokens, fewer agents, compact artifacts, or a scalar score as
  success when the blocking PR-9464 regression is missed or reduced in severity.

## Acceptance criteria

1. **R-0004 preservation.** All 14 preserved R-0004 requirements above remain
   enforced and pass unchanged for Claude and Codex.
   **Verify:** the complete R-0004 positive/negative corpus and host-parity suites
   pass without waiver, removed assertion, or weakened expected result.
2. **Graph-quality input.** Before any lens is mapped, routing input records
   scanner coverage, unresolved internal edges, stale/truncated state, module
   confidence, and changed-symbol caller coverage.
   **Verify:** complete, partial, stale, truncated, unresolved-edge, and low-
   confidence fixtures assert fields and block premature mapping.
3. **Bounded caller expansion.** When module impact is insufficient, exactly one
   bounded changed-symbol caller expansion runs against the canonical snapshot
   and adds discovered callers/contracts to the impact envelope before routing.
   **Verify:** sparse-module fixtures assert one expansion, its declared bound,
   deterministic callers, contracts, and no second derivation for the same input.
4. **Fail-closed impact.** If graph plus bounded caller expansion cannot establish
   sufficient impact confidence, review returns `impact_incomplete` and emits
   zero lens dispatches.
   **Verify:** unresolved and out-of-bound caller fixtures assert the named state,
   zero briefs/agents, and absence of `breadth=all` fallback.
5. **Frozen PR-9464 correctness.** The frozen fixture follows provisioning and
   NodeClass-validation callers through changed userdata serialization and routes
   the lenses required to report the known validation reconcile-loop regression.
   **Verify:** replay asserts both caller-path evidence, a blocking finding with
   the pinned identity/severity, and failure if either path or finding is absent.
6. **Selective review parity.** Every normal Review, Evaluate, and final EM maps
   all 26 lenses to `deep`, `light`, or `n/a` from one complete input, then
   dispatches exactly every deep lens plus at most one bounded light sweep while
   preserving architecture/security floors.
   **Verify:** entry-point parity fixtures assert identical mapping, exact unique
   ids, complete `n/a` dispositions, floor behavior, and no implicit `all`.
7. **One envelope and deterministic views.** Exactly one immutable diff, impact,
   graph-quality, runnability, requirement, acceptance, and contract envelope is
   created per snapshot; every lens receives a deterministic scoped view by
   reference and no lens re-derives those facts.
   **Verify:** multi-lens replay asserts one derivation/input key, one envelope
   fingerprint, stable per-lens view fingerprints, and forbidden lens commands.
8. **Lens-authored provenance.** Every dispatched slot writes its own result
   bound to lens, slot, target, context, view, and revision fingerprints;
   missing, reconstructed, copied, duplicate, or wrong-slot findings cannot
   satisfy completion.
   **Verify:** one negative fixture per provenance failure and positive distinct
   results from individual deep slots and the bounded light sweep.
9. **Canonical artifact consistency.** Findings, report, dashboard, and gate cite
   the same target, context, findings fingerprint, and monotonically increasing
   revision; stale or contradictory summaries block.
   **Verify:** same-revision positive replay plus stale, mixed, rollback,
   contradictory, and altered-artifact fixtures.
10. **Command budget.** A successful no-retry standalone review uses at most 12
    top-level taskPlane CLI calls, and bundled read-only observation does not
    consume governed work-action budget multiple times.
    **Verify:** frozen command transcript counts top-level calls and work actions;
    13 calls or repeated observation charging fails structural compliance.
11. **Artifact by reference.** Normal commands emit compact manifests and paths
    rather than full diff, impact, brief, findings, or HTML bodies; each large
    artifact is written/fingerprinted once and delivered by reference where the
    host supports artifacts.
    **Verify:** output-size and digest fixtures assert one write, canonical path,
    reference transport, bounded fallback, and zero duplicate HTML emissions.
12. **Efficiency evidence.** The evaluator records CLI count, emitted bytes,
    repeated-derivation bytes, dispatched-agent count, prompt-view bytes,
    artifact-render bytes, effective tokens when available, and a named
    comparability result.
    **Verify:** schema validation and missing/corrupt-counter negative fixtures;
    absent token telemetry is explicit rather than zero.
13. **Comparable PR-9464 target.** With matching comparison keys, frozen PR-9464
    uses at most 1.18M taskPlane-attributed effective tokens versus 2.36M,
    produces zero duplicate dashboard HTML, and preserves the known blocking
    regression without severity reduction.
    **Verify:** evaluator pins baseline/target, telemetry attribution, output
    digests, finding identity, and severity; exceeding any bound fails.
14. **Non-comparable telemetry.** Missing host token telemetry or mismatched
    keys yields `not_comparable`; structural efficiency and workflow compliance
    still gate absolutely.
    **Verify:** one matrix fixture per missing or mismatched key proves token
    status is non-comparable while structural failures still fail.
15. **Claude/Codex semantic parity.** Both drivers consume byte-equivalent
    manifests, mapping, view fingerprints, provenance rules, and counters; only
    adapter transport differs.
    **Verify:** captured pre-transport bytes and normalized records are identical
    for the same fixture, with host-specific transport fields explicitly ignored.
16. **Regression closure.** Focused graph-quality, routing, shared-context,
    provenance, artifact-reference, command-budget, frozen-PR, Claude, Codex,
    evaluation, and complete repository suites pass.
    **Verify:** the contract test command and CI matrix pass, and generated
    Evaluation DoR contains only genuine external capability blockers.

## Contract handoff

- `scope_paths`: `scripts/ci_evals.py`, `scripts/eval_record.py`,
  `taskplane/eval_*.py`, `taskplane/derivation.py`, `taskplane/review.py`,
  `taskplane/lens.py`, `taskplane/lens_signals.py`, `taskplane/decompose.py`,
  `taskplane/depgraph.py`, `taskplane/loop.py`, `taskplane/taskplane_lite.py`,
  `taskplane/tp.py`, review-context/artifact modules under `taskplane/`,
  `lenses/catalog.json`, relevant `taskplane/tests/test_eval*.py`,
  `taskplane/tests/test_graph*.py`, `taskplane/tests/test_depgraph*.py`,
  `taskplane/tests/test_lens*.py`, `taskplane/tests/test_review*.py`,
  `taskplane/tests/test_loop.py`, `taskplane/tests/test_codex_compat.py`,
  efficiency/provenance/artifact/frozen-PR fixtures under `evals/**`,
  `hooks/hooks.json`, affected `skills/tp-engineering/**`, `skills/tp-go/**`,
  `skills/tp-build/**`, affected agent instructions, `docs/routing-and-flows.md`,
  generated Evaluation DoR, directly affected README guidance, and
  `specs/spec.md`.
- `out_of_scope`: version and manifest files, release notes/history, tags,
  marketplace and package builders/output, unrelated lenses/skills/docs,
  provider account configuration, and unrelated product behavior.
- `dod.test_command`: `python -m pytest -q taskplane/tests && python
  scripts/ci_evals.py --corpus`.
- dependency: `R-0004`.
- contracts: `changes:contract:host-hook-event-v2`,
  `changes:contract:lens-applicability-v2`,
  `provides:contract:shared-review-context-v2`,
  `provides:contract:review-efficiency-v1`, and
  `provides:contract:findings-provenance-v1`.

This is cross-module, contract-changing, security-sensitive work and requires
Design before Build. Design starts only with a current graph, maps every one of
the 16 R-0005 acceptance criteria, and may not weaken the preserved R-0004
requirements. There are no open product questions.
