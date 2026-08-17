# Specification — host-capability parity and truthful governed evaluation

## Problem

taskPlane currently has overlapping Codex hook installation paths, Claude
workflow outputs that do not carry the complete leased ReviewKernel contract,
and host-routing/evaluation telemetry that can describe controls the host did
not actually apply. A governed run must execute each hook once, produce the
same verifiable evaluation evidence on Claude and Codex, and stop fail-closed
when a submission is missing without silently releasing evidence or weakening
the workflow.

## Users and context

The primary users are Claude Code, managed Claude, Codex CLI, and Codex desktop
users running taskPlane in personal or organization-controlled repositories.
They need onboarding to report what the current host can really enforce, and
they need a review or evaluation to mean the same thing regardless of whether
the host offers native plugin hooks, repository hooks, structured output,
model selection, reasoning-effort selection, or token-cache telemetry.

This batch builds on the existing ReviewKernel, host-portability, cost, and
review-honesty requirements. It also repairs the three failures in public CI
run 31858859934 at commit 92d05fd: two stale `tp-go` scenario-fingerprint
failures and the evidence-bundle refusal that no longer names the unproven
acceptance criterion when runtime guidance fires first. Windows, hook,
manifest, documentation, and dispatch-parity jobs already pass and remain
protected.

## In scope

- A single effective Codex hook path per lifecycle event, with deterministic
  native-plugin versus repository-bridge selection and duplicate detection.
- Truthful onboarding for hook availability, repository trust, managed hook
  policy, required authority, and the exact fallback in use.
- Exact leased ReviewKernel parity for shipped Claude review/evaluate workflow
  execution, including canonical leases, views, result paths, producer
  identity, schema, collection, and revisions.
- A canonical schema-driven evaluator-output contract on Claude and Codex,
  with a capability-aware validated-file fallback when the host cannot enforce
  a native output schema.
- Stop and subagent-stop enforcement for governed work that requires but has
  not recorded a valid submission, while preserving submitted evidence and
  orchestrator/human ownership of release and gates.
- Effective, host-portable model and reasoning-effort routing with observed
  receipts and explicit unsupported/inherit behavior.
- Evaluation observability and provider-correct cached-token accounting.
- The exact current CI failures and regression coverage for every behavior in
  this batch.
- User-facing and generated reference documentation directly affected by the
  changed behavior.

## Out of scope

- Version bumps, release notes/history, tags, publishing, marketplace or
  package submission, package builders, and plugin/package manifest changes.
- Replacing the 26-lens catalog, changing lens applicability policy, weakening
  architecture/security floors, or changing graph impact semantics.
- Auto-approving a gate, letting a worker validate its own submission, or
  treating model prose as completion evidence.
- Automatically clearing a submitted governed contract at Stop or
  SubagentStop; release remains an orchestrator or human action after the
  applicable gate.
- Bypassing repository trust, organization-managed policy, or host security
  controls in order to make hooks appear ready.
- Hard-coding a model identifier from one provider into another host, silently
  claiming an unsupported reasoning effort was applied, or requiring every
  host to expose identical controls.
- Provider price prediction, model ranking, new external telemetry services,
  or billing reconciliation.
- Persisting full prompts, transcripts, secrets, credentials, or user content
  merely to add observability.
- Unrelated CI cleanup, new skips/xfails/waivers, lowered test floors, or
  relaxed assertions outside the named failures.

## Acceptance criteria

1. **Exactly one effective Codex hook execution.** For every taskPlane hook
   action and lifecycle event, a Codex session invokes taskPlane exactly once.
   Native plugin hooks are preferred when the host can load them; the
   repository bridge is used only as a named fallback. A legacy workspace in
   which both paths are present is detected and converges to one effective
   execution without deleting or suppressing unrelated hooks.
   **Verify:** matrix fixtures for native-only, bridge-only, both-present,
   neither-present, reinstall, resume, and repeated onboarding assert one
   trace row/side effect per event and idempotent configuration bytes.

2. **Truthful trust and managed-policy onboarding.** Onboarding reports
   separately whether hooks are installed, allowed by repository trust,
   permitted by managed settings, loaded in the current session, and which
   path is effective. It never reports ready merely because a file exists,
   never instructs a user to bypass trust or managed policy, never overwrites
   organization-managed settings, and names when administrator action or a
   fresh session is required.
   **Verify:** personal/managed, trusted/untrusted, writable/read-only,
   enabled/disabled local-hook, and unknown-capability fixtures assert the
   readiness state, next action, absence of false-green output, and
   preservation of unrelated/managed hook rows.

3. **Leased ReviewKernel parity for Claude workflows.** Every shipped Claude
   workflow path that performs review or evaluation consumes the same
   immutable envelope, scoped view, routing decision, slot lease, producer
   contract, result path, `taskplane.lens-slot-output/v2` schema, and canonical
   revision as the Task/Codex path. Host child identity and the slot write are
   observed before collection, and collection occurs through the canonical
   ReviewKernel collector rather than a parallel findings convention.
   **Verify:** the same frozen review/evaluate payload run through Claude
   workflow and Task/Codex transports produces byte-identical canonical
   artifacts and equivalent collection results; wrong-slot, missing,
   duplicate, copied, stale-revision, wrong-view, and unobserved-producer cases
   fail identically on both paths.

4. **Safe workflow resume and retry.** A journaled or cached Claude workflow
   result is reusable only when its target, context, view, lease, schema, slot,
   producer, and canonical revision all still match. A mismatch is invalid
   evidence and causes a bounded retry or named failure; it is never collected
   as a pass. Workflow agents still cannot gate, approve, or advance loop
   state.
   **Verify:** unchanged-resume, changed-target, changed-view, stale-lease,
   schema-mismatch, retry-exhaustion, and forbidden-gate fixtures.

5. **Schema-driven Claude and Codex evaluation output.** Every live skill,
   loop evaluation, and leased review evaluator dispatch declares one
   versioned output schema and validates the returned artifact before it can
   enter an evaluation record, canonical findings revision, or gate. When the
   host supports native structured output, the dispatch uses it. When it does
   not, the brief requires the same canonical JSON at an exact governed path
   and taskPlane validates it after a host-observed write. Free-form prose,
   missing output, or merely parseable but schema-invalid JSON cannot pass.
   **Verify:** Claude-native, Codex-native where available, validated-file
   fallback, malformed JSON, missing required fields, extra incompatible
   fields, wrong schema version, no observed write, and bounded retry cases.

6. **Capability-aware fallback is explicit.** Host capability detection is a
   versioned, machine-readable input to dispatch and is recorded with source
   and confidence. Unknown or unavailable structured-output capability selects
   the governed file-validation fallback and reports why; it never disables
   evaluation, widens write scope, drops the schema, or converts unavailable
   evidence to zero/pass.
   **Verify:** supported, unsupported, unknown, contradictory, and corrupt
   capability fixtures assert deterministic selection, fail-closed validation,
   and equivalent final evidence requirements.

7. **Unsubmitted governed work blocks at stop.** When a contract declares a
   worker submission required and Stop or SubagentStop observes no valid
   submission for that exact workspace/task/slot/stage, the hook returns a
   blocking result that names the contract, slot, missing artifact, and the
   safe retry or orchestrator/human recovery action. The contract remains
   active and the missing submission cannot be inferred from a final message
   or result file alone.
   **Verify:** serial, parallel, review-lens, evaluate, crash, wrong-slot,
   wrong-workspace, corrupt-submission, and repeated-stop fixtures assert the
   block, diagnostic, unchanged contract, and no false completion.

8. **Submitted work is never auto-cleared by stop handling.** A valid governed
   submission is preserved byte-for-byte through Stop and SubagentStop so the
   orchestrator can validate it. Stop hooks neither gate nor clear submitted
   worker contracts, sibling slots, canonical review results, or submission
   records. Standalone contracts and contracts that explicitly do not require
   submission retain their documented lifecycle without being mislabeled as
   leaks.
   **Verify:** valid-submit, sibling-slot, post-submit crash, repeated-stop,
   standalone, and no-submission-required fixtures assert no mutation and
   orchestrator-owned release after gate.

9. **Model and effort routing changes the real dispatch.** Each host adapter
   resolves configured tiers only into model identifiers and effort values
   that the current host accepts, passes supported values to the actual child
   invocation, and records planned and host-observed effective values. A
   supported non-inherit selection must be visible in the host receipt; it is
   insufficient to include it only in a brief or expected-dispatch record.
   **Verify:** Claude Task/workflow and Codex native-dispatch fixtures for
   cheap/standard/deep tiers assert the actual invocation and observed receipt,
   including explicit model aliases and every supported effort value.

10. **Portable routing degradation.** No host receives a foreign provider
    model id or unsupported argument. Unsupported/unknown model or effort
    control resolves to an explicit `inherit` or `unsupported_fallback` result
    with reason and capability evidence. Strict dispatch blocks before work if
    a required explicit route cannot be honored; non-strict mode may use the
    recorded fallback but cannot claim exact-route verification.
    **Verify:** cross-provider ids, unsupported effort, old-host capability,
    corrupt capability, strict, warn, and default configurations.

11. **Evaluation observability is complete and bounded.** Each evaluation
    record exposes host, host version when available, capability source,
    dispatch transport, schema transport, fallback reason, task/slot/lease,
    planned and observed model/effort, attempts, start/end/duration, terminal
    status, validation outcome, token-telemetry availability, and a bounded
    diagnostic. Events and records share a versioned schema and stable run id;
    secrets, command arguments, full prompts, and full transcripts are not
    copied into telemetry.
    **Verify:** successful, fallback, retry, timeout, cancellation, malformed
    output, unavailable-host, and redaction fixtures validate schema and
    lifecycle ordering.

12. **Cached-token accounting is provider-correct.** Raw usage reports
    uncached input, cached input/read, cache creation/write when the provider
    exposes it, output, total raw, and effective tokens. When a provider total
    already includes cached input, cached tokens are not counted again as
    ordinary input. Existing effective weights remain single-sourced. Missing
    or malformed cache telemetry is `unavailable` with a reason, never
    fabricated as zero, and totals reconcile to provider semantics.
    **Verify:** representative Claude and Codex usage payloads, nested usage,
    duplicated messages, torn JSONL, absent fields, negative/corrupt values,
    and golden arithmetic cases assert no double counting and exact totals.

13. **Current CI failures are repaired without weakening controls.** The
    `tp-go` scenario records the current canonical flow fingerprint so both
    stale-fingerprint tests pass while mutations still prove the stale gate is
    live. An unchanged engine-authored evidence bundle remains ineligible and
    its refusal still names each unproven acceptance criterion even when
    runtime drift guidance also fires; guidance cannot mask the underlying DoD
    failure.
    **Verify:** rerun the three failures from CI run 31858859934, mutation-test
    the fingerprint guard, and assert both runtime-guidance and unproven-
    criterion diagnostics are present without advancing loop state.

14. **Cross-host regression suite stays green.** The complete suite passes on
    Python 3.10, 3.11, and 3.12 and on macOS and Windows, while the currently
    green hooks, package/manifest validation, documentation truth, release
    history, and dispatch-parity jobs remain green. No test is removed,
    skipped, xfailed, loosened, or moved out of a gating job to achieve this.
    **Verify:** local focused suites plus the full GitHub Actions matrix and a
    test-manifest/floor diff showing no governance-coverage reduction.

## Non-functional requirements

- `security`: Hook selection, capability detection, lifecycle validation, and
  output validation fail closed wherever they guard governance. No untrusted
  hook path, model-authored capability claim, trust bypass, managed-policy
  override, secret, credential, or full transcript may become authority or
  telemetry.
- `architecture`: One canonical host-capability/dispatch/evaluation contract
  and one ReviewKernel source of truth serve Claude workflows, Claude tasks,
  Codex native tasks, lifecycle hooks, fallbacks, collectors, and evaluators;
  host transports cannot create parallel governance authorities.
- `integrability`: Capability and evaluation schemas are versioned and
  additive or explicitly migrated; existing supported host paths and canonical
  ReviewKernel consumers retain deterministic compatibility.
- `sre`: Every unavailable capability, fallback, retry, timeout, stop block,
  routing mismatch, and telemetry gap has a stable status and actionable
  diagnostic, with bounded work and no infinite stop/retry loop.
- `cost-finops`: Token categories and weights are single-sourced, cached tokens
  are never double counted, and missing telemetry is distinguished from zero
  without estimating provider billing.
- `privacy-compliance`: Observability minimizes captured data and excludes
  prompts, transcripts, secrets, credentials, and unnecessary absolute user
  paths; retained identifiers are bounded to governance provenance.
- `qa`: Positive, negative, mutation, parity, resume, lifecycle, and host-matrix
  fixtures prove the controls and their failure directions across supported
  Python versions and operating systems.
- `tech-writer`: Onboarding, configuration, routing, evaluation, and CLI
  references describe the actual detected capability, effective path,
  fallback, trust/managed-policy boundary, and recovery action without
  claiming unsupported behavior.

## Contract handoff

- `scope_paths`:
  - `taskplane/tp.py`
  - `taskplane/taskplane_lite.py`
  - `taskplane/review.py`
  - `taskplane/review_evidence.py`
  - `taskplane/runtime_eval.py`
  - `taskplane/evidence.py`
  - `taskplane/spend.py`
  - `taskplane/loop.py`
  - `scripts/eval_skills.py`
  - `scripts/eval_record.py`
  - `workflows/*.js`
  - `hooks/hooks.json`
  - `.codex/hooks.json`
  - `evals/scenarios/**`
  - `skills/taskplane/references/runtime-evals.json`
  - `agents/tp-evaluator.md`
  - `agents/tp-engineering.md`
  - `agents/tp-lens.md`
  - `docs/onboarding.md`
  - `docs/configuration.md`
  - `docs/model-evaluation.md`
  - `docs/routing-and-flows.md`
  - `docs/cli-reference.md`
  - `taskplane/tests/**`
  - `specs/spec.md`
- `out_of_scope`:
  - release/version/tag/publishing surfaces;
  - marketplace, package builders/output, and plugin/package manifests;
  - lens-catalog or graph-routing policy changes;
  - host trust/managed-policy bypasses;
  - provider pricing/model-ranking services;
  - worker-owned gate approval or automatic clearing of governed submissions;
  - unrelated product behavior and unrelated CI cleanup.
- `dod.test_command`: `python3 -m pytest taskplane/tests -q`
- dependencies: `R-0005`, `R-0009`, `R-0012`, `R-0013`.
- contracts:
  - `contract:host-hook-lifecycle`
  - `contract:review-kernel-slot`
  - `contract:evaluation-output`
  - `contract:host-dispatch-routing`
  - `contract:evaluation-telemetry`

This work is cross-module, cross-host, contract-changing, security-sensitive,
and failure-path heavy. It requires Design before Build, including an explicit
host capability matrix and migration/compatibility treatment for every
changed contract. There are no blocking product questions.
