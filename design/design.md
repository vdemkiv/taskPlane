# R-0006 Design — Capability-bound host parity

Status: proposed

Requirement: R-0006

Baseline graph: `b2e530770c71c400f23caeb7da95ed3638662d5bc42c83b6e6e8f50c75934d3c`

Scanned head: `92d05fddb745cd02eafe6f910c3a3df886060b16`

## Decision

Keep the accepted host-neutral ReviewKernel and bounded host adapters, and put a single versioned `HostCapabilitySnapshot` in front of every host-dependent choice. The snapshot is derived only from host/version probes, plugin and repository configuration, trust state, managed policy, and host-observed receipts. It is never inferred from a model response or from file presence alone.

That snapshot drives four deterministic decisions:

1. choose one effective Codex hook path per lifecycle event;
2. choose native structured output or the same governed-file validation fallback;
3. resolve abstract model tiers and effort only to values the current host accepts; and
4. label telemetry as observed, unsupported, unknown, or unavailable.

All transports then converge on existing authorities: ReviewKernel leases and `taskplane.lens-slot-output/v2` for leased reviewers, loop submission records for stage workers, and orchestrator/human gates for state transitions. A workflow, Task dispatch, or native Codex task is only a transport. None may merge findings, infer a submission from prose, clear a contract, gate, approve, or advance loop state.

## Grounding and inherited decisions

The as-built inventory document is present but empty. This design therefore cites and verifies repository sources directly instead of inventing a greenfield state:

- `hooks/hooks.json` packages all lifecycle hooks, while `.codex/hooks.json` currently repeats those events through `.taskplane/codex-hook.py`.
- `taskplane/tp.py::_codex_hooks_report` currently treats matching files as ready and `_install_codex_hooks` installs the bridge without native-plugin, trust, managed-policy, or loaded-session facts.
- `workflows/review-wave.js` and `workflows/evaluate-wave.js` currently pin a legacy findings schema and return merged values instead of the full leased slot contract.
- `taskplane/eval_drivers.py` provides bounded Claude/Codex transports but declares no native-output schema or validated-file fallback.
- `taskplane/tp.py::cmd_subagent_stop` only traces completion; `cmd_session_verify` checks obligations, not exact worker submissions.
- `taskplane/taskplane_lite.py` has per-task contract slots and dispatch expectation queues, but model defaults and effort values are resolved before host capability validation; requested values are not equivalent to an effective host receipt.
- `taskplane/spend.py` applies single-sourced weights, but treats every provider's `input_tokens` as uncached and can add cached input a second time when provider totals already include it.

Accepted decision 0005 remains governing: one host-neutral compliance kernel, one immutable envelope and scoped views, exact leased slots, canonical collection and revisions, bounded adapters, and comparable tokens only when telemetry is honest. Accepted decision 0006 approved that implementation boundary. R-0006 extends those decisions; it does not replace ReviewKernel or create a second findings authority.

The referenced CI run at the pinned head has exactly three failing tests across macOS and Python 3.10/3.11/3.12: the two `tp-go` scenario fingerprint assertions expect `4dbea5…b93b` but record `c8dcc3…8f0`, and `test_the_refusal_names_the_unproven_criteria` is masked by runtime guidance before the refusal names the unproven acceptance criterion. Windows, Codex native hooks, dispatch parity, manifests/packages, docs truth, and release history pass and are regression floors.

## Alternatives considered

| Alternative | Gains | Costs | Decision |
|---|---|---|---|
| One capability snapshot plus canonical hook, output, receipt, and telemetry contracts | One authority; deterministic fallback; portable routing; shared negative cases; ReviewKernel remains unchanged | Adds two small cross-cutting modules and requires migration fixtures | **Selected** |
| Patch each Claude workflow, Codex bridge, native task adapter, and recorder independently | Small local diffs; few new types | Preserves parallel authorities, duplicated schemas, incompatible retry rules, and false-green capability claims | Revisit only if the product deliberately drops cross-host equivalence |
| Require native hooks, native structured output, and exact model controls everywhere; remove all fallbacks | Smallest steady-state surface and strongest host receipts | Excludes older, unknown, managed, or restricted hosts that R-0006 explicitly supports | Revisit when every supported host version guarantees those capabilities and managed deployments expose them |

## Canonical records and authority

### `HostCapabilitySnapshot` — `taskplane.host-capabilities/v1`

`taskplane/host_capabilities.py` produces one immutable snapshot per host session and workspace. Each capability row contains `status` (`supported`, `unsupported`, `unknown`, or `contradictory`), `source`, `confidence`, `observed_at`, and a bounded reason. The snapshot covers host and version, native plugin hook installation/loading, repository bridge configuration/loading, repository trust, managed-policy permission, workflow availability, native structured output, model selection, supported model aliases, effort selection, and supported effort values.

Detection reads bounded local configuration and host receipts only; it performs no network access and never edits managed settings. Corrupt or contradictory evidence stays explicit and selects the safer path. Snapshots are passed by fingerprint to hook selection, dispatch, evaluation, and telemetry rather than re-probed independently.

### Exactly-once hook lifecycle — `contract:host-hook-lifecycle`

Native plugin hooks are preferred only when they are installed, allowed, and observed loaded in the current session. The repository bridge is a named fallback only when it is configured, trusted, policy-permitted, and observed loaded. Unknown trust, policy, or load state is not ready.

Onboarding owns an idempotent migration state machine:

- `native_effective`: remove only taskPlane-owned bridge rows from future workspace configuration; preserve unrelated rows byte-for-byte.
- `bridge_effective`: retain/repair only taskPlane-owned bridge rows and name why native is unavailable.
- `transitioning`: keep the currently observed effective path, state the pending fresh-session action, and do not claim the next path is loaded yet.
- `blocked`: configure neither bypass nor override; name repository trust, administrator, or fresh-session recovery.

For a legacy session where both paths are already loaded, both entry points pass the same bounded host event identity into a shared idempotency boundary. A per-workspace exclusive claim keyed by `{host session, lifecycle event, turn/tool or child identity, task slot, workspace fingerprint}` executes the action once and replays the same safe hook response to a duplicate. The journal stores only the digest, status, timestamps, and response class, is capped at 512 live entries with a 24-hour expiry, and never stores tool arguments or prompts. If a stable event identity cannot be established, the both-loaded state is not declared ready; onboarding requires a fresh session.

### Leased workflow parity — `contract:review-kernel-slot`

`review-wave.js` consumes ReviewKernel-generated slot briefs, not a parallel `FINDINGS_SCHEMA`. Each brief carries the immutable envelope, scoped view, routing decision, lease, producer contract, exact result path, `taskplane.lens-slot-output/v2`, and canonical base revision. The workflow passes the declared schema to the host when supported, activates the exact producer slot, and requires the child to write the leased result path. `SubagentStart` binds host child identity; the write hook records the exact slot write before the existing collector accepts it.

The workflow returns only slot receipts. `taskplane/review.py` remains the sole collector and revision authority. `evaluate-wave.js` similarly consumes the evaluator brief and its embedded ReviewKernel citation; the outer evaluator output uses the canonical evaluator schema below, while its leased review results still enter through ReviewKernel.

Resume keys bind `{target, context, view, lease, schema, slot, producer, canonical revision}`. A journal/cache hit is reusable only after re-reading the canonical result and host-observed write receipt. Any mismatch invalidates the cache and permits at most one fresh retry; a second failure is terminal and named. Workflow agents have no gate, approval, loop-advance, or contract-release API.

### Evaluator output — `contract:evaluation-output`

`taskplane/evaluation_output.py` defines strict versioned schemas and one `EvaluationOutputContract` per dispatch. The contract names schema id and digest, exact governed result path, task/stage/slot/lease identity, transport selection, write-observation requirement, maximum bytes, and attempt bound.

- Native path: when `structured_output=supported`, the adapter passes the exact schema through the host's supported option and validates the returned canonical JSON again before use.
- Fallback path: unsupported, unknown, contradictory, or corrupt capability selects `validated_file`; the brief names one exact repository-relative path already present in `write_allow`, the ordinary host write hook records the producer and digest, and the same validator reads that file.

The common evaluator schema is `taskplane.evaluator-output/v1`; leased lenses retain `taskplane.lens-slot-output/v2`. Schemas reject incompatible extra fields, malformed JSON, wrong versions, missing required fields, path escape, oversized output, and unobserved writes. Free-form stdout is diagnostic only and can never become a record, canonical revision, submission, or gate input. Validation allows at most two total attempts and never widens write scope.

### Submission-aware Stop and SubagentStop

Worker activation copies an immutable `submission_contract` into the exact active slot: `required`, workspace fingerprint, task, stage, slot, expected artifact or loop submission locator, and validation rule. Review slots point to the leased result plus producer-write receipt; execute/evaluate/fix slots point to the engine-owned loop submission and its evidence fingerprint.

One read-only `submission_status` function validates the exact contract identity and is called by Stop, SubagentStop, orphan checks, and the orchestrator gate. Missing, corrupt, wrong-workspace, wrong-slot, wrong-stage, stale, or unobserved evidence blocks Stop with the contract, slot, missing artifact, and one safe retry or orchestrator/human recovery action. A final message and an otherwise plausible result file do not count.

Lifecycle handling is observational only: it may append a redacted check event, but it never calls `clear`, `loop gate`, `loop approve`, collector publication, or state mutation. Submission-required contracts are excluded from PID/TTL auto-release whether their submission is missing or valid; only the orchestrator/human releases them after the applicable gate. Sibling slots are never inspected as substitutes and are never changed. Standalone and `submission_required=false` contracts keep their existing orphan lifecycle.

### Portable model and effort routing — `contract:host-dispatch-routing`

Abstract tiers remain `cheap`, `standard`, and `deep`. `resolve_dispatch_route(snapshot, tier, configured_model, configured_effort, mode)` returns planned tier, resolved model/effort or inherit, capability evidence, `exact`, `inherit`, or `unsupported_fallback`, and a reason. Host-scoped aliases and supported effort values come from the snapshot; a generic or foreign provider id is never passed merely because it appeared in configuration.

Task, workflow, and native Codex adapters receive only supported non-null values. Their child-start/session receipt records host-observed effective model and effort separately from the plan. Strict mode blocks before the child starts when an explicit route is required but cannot be honored. Warn/default mode may inherit, but records that exact-route verification is false. The accepted `model: inherit` agent frontmatter remains portable.

### Evaluation lifecycle and token accounting — `contract:evaluation-telemetry`

Every attempt emits `taskplane.evaluation-lifecycle/v1` events sharing a stable run id and containing host/version, capability source/fingerprint, dispatch and schema transport, fallback reason, task/stage/slot/lease, planned and observed route, attempt number, start/end/duration, terminal status, validation result, token availability, and a diagnostic code plus at most 512 redacted bytes. No event or record copies prompts, full transcripts, command arguments, credentials, secrets, or unnecessary absolute user paths. Raw model stdout/stderr is not frozen as a transcript artifact; only digest, byte count, bounded redacted diagnostic, and validated canonical output reference remain.

`taskplane/spend.py` normalizes provider usage into `taskplane.token-usage/v2`: uncached input, cached read/input, cache creation/write, output, provider raw total, effective total, availability, reason, and dedup count. Claude-style totals add disjoint categories. Codex/OpenAI-style `input_tokens` is treated as inclusive when provider semantics say so, so uncached input is `input - cached` and cached input is never added twice to raw total. Effective weights remain single-sourced in `WEIGHTS`. Negative, nonnumeric, irreconcilable, provider-unknown, or identity-ambiguous duplicate usage is `unavailable`, never zero. Duplicate rows are deduplicated only by a stable provider event/message id and usage digest.

## Quality bounds

- One effective hook action and one side effect/trace row per stable lifecycle event; duplicate replay waits at most 2 seconds inside the existing 10/15-second host hook timeout.
- Capability probing performs no network access and reads at most 256 KiB per local source; hook idempotency state is at most 512 live entries and 24 hours.
- Evaluator output is at most 1 MiB, diagnostics at most 512 bytes, and validation at most two attempts.
- Telemetry persists zero prompt, full-transcript, command-argument, secret, or credential bytes.
- This is a local CLI/plugin, not an online service: availability, throughput, RPO, and RTO targets are not applicable. Recovery is deterministic restart/resume from immutable files; no stored product data is migrated.

## Module ownership and build sequence

1. **Capability foundation — host/platform owner.** Add `taskplane/host_capabilities.py`, capability fixtures, and truthful onboarding projection before changing any dispatch path.
2. **Canonical output and lifecycle — evaluation/governance owner.** Add `taskplane/evaluation_output.py`; bind active contracts to submission expectations; add read-only Stop/SubagentStop validation and exact-once hook claims.
3. **Transport adoption — ReviewKernel/evaluation owner.** Migrate Claude workflows, bounded adapters, loop dispatch, recorder, and token normalization to the two canonical modules. ReviewKernel result and revision schemas stay authoritative.
4. **Compatibility and proof — QA/docs owner.** Migrate only taskPlane-owned bridge rows, refresh the `tp-go` scenario fingerprint, compose runtime-guidance and unproven-criterion diagnostics, run the focused matrices, then the complete Python/macOS/Windows matrix and generated-doc checks.

Required fixtures include native-only, bridge-only, both, neither, managed/trust states, old/unknown/corrupt capabilities, Claude workflow and Task, Codex native task, schema native/file fallback, resume mismatch, lifecycle stop, routing strict/warn/default, provider usage arithmetic, and the three pinned CI failures.

## Failure, rollout, and rollback

Roll out additively. First ship readers/validators and shadow capability/route/telemetry records while current paths remain effective. Next enable exactly-once claims and truthful hook selection, then switch workflow/evaluator outputs to canonical validation, then enable submission-aware stop blocks and exact-route strict enforcement. Advance each phase only after its negative fixtures and the unchanged green CI floors pass.

Existing active ReviewKernel v2 runs must finish with v2 or be cancelled and restarted; never translate a lease or revision. Legacy evaluator outputs remain readable for historical records but cannot satisfy a new gate. Repository hook migration changes only rows containing the taskPlane marker and preserves all unrelated bytes/rows. A current session stays on its observed path until restart rather than claiming a future configuration is loaded.

Rollback never means accepting prose, unvalidated JSON, duplicate hook side effects, or an unsupported explicit route. Native preference may fall back only to a trusted, policy-permitted, loaded bridge with the exactly-once boundary still active; otherwise hooks fail closed. Model routing may fall back to recorded inherit in non-strict mode. In-flight output, submission, and ReviewKernel identities remain on their original schema until completion or explicit cancellation. Append-only lifecycle/usage records are preserved.

## Known debt

The repository bridge remains as compatibility debt for hosts without observed native plugin hooks. Pay it down only after every supported Codex version and managed deployment reports native hook installation, policy permission, and loaded-session receipts for two consecutive supported releases. Proposed debt record, not executed during Design:

`tp req debt "Retire the Codex repository hook bridge" --req R-0006 --reason "Compatibility fallback for hosts without observed native plugin hooks" --follow-up "Remove after two supported releases have universal native hook load receipts and migration telemetry shows no bridge users" --files ".codex/hooks.json,taskplane/tp.py,docs/onboarding.md"`

## Visualization

`design/visual.html` is required because the selected design has three independent host-capability branches that must converge on one validation and authority path, plus a lifecycle block that deliberately does not clear state. The data-flow view makes those authority and fallback boundaries reviewable in one screen.
