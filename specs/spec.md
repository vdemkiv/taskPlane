# Canonical operational settings, CI-first testing, and all-outcome cleanup

## Product authority

This specification is the Product authority for the delivery phase that starts
from Taskplane 2.18.2 on `codex/modular-settings-test-redesign`. It replaces the
previous contents of `specs/spec.md` for this new phase; prior requirements,
designs, evidence, and released behavior remain historical records rather than
being rewritten.

The P0 findings in `REL-2181-full-retro.md` are binding input. Wall-clock time
and recurring workflow, CI, and lifecycle glitches are the primary product
problem. Token reduction remains a guardrail, not a reason to restore broad
lens execution. Product and Design use only minimum-sufficient quick focused
lenses, Plan uses exactly three or four quick lenses, and Build, Fix, Evaluate,
and Engineering use zero lens workers.

## Problem

Taskplane operational behavior is controlled by duplicated constants,
environment reads, workflow literals, and stage-local defaults. This makes
delivery behavior hard to change coherently and helped turn the prior wave into
a 40-hour sequence of late CI, repeated validation, lifecycle leaks, and
replanning. The test portfolio and cleanup lifecycle also encode accumulated
history rather than one explicit current-product contract.

Taskplane needs one validated operational-settings authority, one CI-first test
strategy, an evidence-based current-contract test portfolio, and exact-owned
cleanup that runs on every terminal path. The resulting delivery must be faster
in elapsed time without weakening security, human authority, portability,
release truth, or audit evidence.

## Users and context

- Taskplane users need predictable governed delivery across Codex, Claude,
  local shells, linked worktrees, and GitHub-hosted CI.
- Product, Design, and Plan workers need one settings and test-strategy contract
  whose downstream consumers cannot silently diverge.
- Build workers need pairwise-disjoint tasks and validation jobs dispatched
  concurrently without editing-time lens fan-out.
- Maintainers need quick local feedback, one authoritative CI workflow with no
  synthetic join check,
  and failure batches classified once rather than green layers repeatedly run.
- Operators need truthful settings, validation, cleanup, usage, and duration
  receipts that survive failure and handoff without exposing secrets.
- Existing users need current CLI behavior and legacy environment deployments
  to migrate predictably rather than silently changing authority.

## Measured baseline and targets

The baseline is evidence, not a deletion quota. Candidate families are removed
only after the current contract they protect is adjudicated.

| Measure | Measured baseline | Delivery target |
| --- | ---: | ---: |
| Operational-settings spread | 258 code/docs/workflow files contain candidate timeout, budget, model, routing, transport, sharding, or cleanup literals | 100% of governed settings have one canonical key and owner; zero independently authoritative duplicate defaults |
| Test portfolio | 266 tracked test files, 4,909 collected cases, 95,601 test LOC | no more than 230 files and 4,200 cases; at least 6 demonstrably redundant families removed; zero protected-contract loss |
| Candidate historical families | 112 version/requirement-named files; 174 files mention history, replay, legacy, fixture, golden, snapshot, or ceremonial concepts | 100% of candidates adjudicated by current-product value; removal only with retained-selector or obsolete-contract evidence |
| Local feedback | one prior selector took 1,158 seconds; broad local suite was approximately 70 minutes; two commands hit 600-second defaults | exact-selector p95 at most 60 seconds; changed-file/proportional p95 at most 5 minutes; broad suite CI-only by default |
| Hosted CI | first matrix after 31h37m; 12 matrices, 9 red; approximately 15-minute matrix wall and 38 runner-minutes | first matrix within 2 hours of integration readiness; at most 3 authoritative matrices; p50 at most 10 minutes, p95 at most 15 minutes, at most 30 runner-minutes |
| Effective CI parallelism | approximately 2.59x from prior summed job time divided by matrix wall time | at least 4.0x when four or more pairwise-disjoint shards exist |
| Plan/validation churn | 21 returns to Plan; 12 matrices | at most 2 Plan returns before one consolidated stabilization successor; at most 3 matrices unless a named new failure class requires another |
| Cleanup leaks | 132 worktrees at Retro; 264 temporary artifacts plus about 17.3 GB of stale state later cleaned | exactly 0 owned leaks after success, failure, cancellation, interruption, and handoff; active worktrees no more than active shards plus 1 |
| Token telemetry | 540.3M root-session logged tokens; 1.292B archive upper bound; actual billing materially lower | target at most 100M total and 15M uncached observed tokens; hard ceiling 150M total and 25M uncached; billing and non-cumulative root/session truth reported separately |
| End-to-end duration | 39h28m first commit to merge; 40h35m session envelope | active post-authorization delivery at most 8 hours; phase start through Retro at most 12 hours; Design decision within 60 minutes |
| Worker/control-plane churn | 190 spawns, 1,270 waits, 132 worktrees | no inherited worker turns; planned sessions at most 24 and fail-closed ceiling 60; every serialization names its dependency or authority reason |

## In scope

1. A complete inventory of operational settings and consumers across production
   code, flow graphs, skills, agents, hooks, commands, workflows, CI, release
   tooling, tests, generators, fixtures, and packaging.
2. One repository-shipped, versioned canonical settings file as the sole
   persisted operational-settings authority, plus one typed, validated loader.
3. Settings for stage model and reasoning effort; focused-lens dispatch counts
   and routes; Build shard count and concurrency; local-versus-CI test backend;
   test selection and sharding; timeouts and budgets; workflow transport;
   cleanup policy; and explicitly safe overrides.
4. Deterministic precedence, defaults, schema migration, one-release legacy
   adapter behavior, cross-host resolution, fail-closed validation, and
   redacted/tamper-evident effective-settings receipts.
5. Initialization of every Taskplane flow, CLI entry point, skill flow, hook,
   workflow, stage startup/handoff, CI/release command, and compatibility
   adapter through the loader, with no second settings source of truth.
6. Evidence-based removal or consolidation of obsolete, duplicate,
   implementation-detail, history-replay, stale-fixture, and ceremonial tests,
   while retaining current contracts and explicit protected floors.
7. A Design and coding-phase test-strategy contract with exact acceptance
   selectors, producer/consumer freshness and severed-edge checks, same-slice
   fixture updates, product-versus-test failure classification, and progressive
   validation culminating in one exact-candidate authoritative CI workflow.
8. Outcome-independent cleanup for exactly owned temporary worktrees,
   contracts, processes/process groups, caches, generated state, and test
   artifacts, including evidence retention, unsafe-target refusal, idempotency,
   replay/recovery, and post-cleanup zero-leak proof.
9. CI concurrency, shard, timeout, candidate-freeze, failure-batching,
   cancellation, release-order, first-parent compatibility, and cleanup wiring.
10. Closed metrics and receipts for the baseline and every target in the table.

## Out of scope

- A second project, user, host, or environment settings file that can become an
  independent authority.
- Changing the 26-lens catalog or restoring lens workers in Build, Fix,
  Evaluate, or Engineering.
- Weakening consolidated human authorization, stage authority, task-slot
  isolation, protected-main truth, supply-chain controls, or fail-closed gates.
- Deleting tests merely to reach a count, or deleting security, authority,
  current-behavior, cross-host/encoding/path portability, release version/tag/
  provenance, cleanup safety, or high-signal regression coverage.
- Cleaning unrelated worktrees, processes, caches, stores, private knowledge,
  evidence, dirty user changes, or any target whose ownership is ambiguous.
- Redesigning unrelated user-facing features or replacing GitHub Actions as the
  broad authoritative validation backend.
- An actual push, merge, release tag, marketplace publication, or installation
  under this Product requirement without the later exact human/release gate.

## Functional requirements

1. Publish one complete, machine-checkable operational-settings inventory. Each
   operational literal or environment read has one canonical key and one
   disposition: canonicalized, runtime observation, derived value, immutable
   protocol constant, or justified non-setting.
2. Store every configurable default exactly once in the canonical settings
   file. The typed loader validates schema version, types, enums, ranges,
   cross-field constraints, stage availability, and unknown keys before any
   Taskplane state write or dispatch.
3. Use deterministic precedence: explicitly allowlisted safe CLI override,
   explicitly allowlisted safe environment override, then canonical value.
   Overlays are ephemeral inputs, never second config stores. Authority-bearing
   or governance-weakening changes require exact approved authority; conflicts,
   unsupported values, and unsafe overrides fail closed.
4. Emit an immutable normalized effective-settings receipt with schema/version,
   canonical source digest, precedence sources, engine/package identity, host
   capability facts, override authority, and redaction evidence. Bind its digest
   into stage handoffs, worker contracts, test/CI requests, suite-cache keys,
   cleanup manifests, dashboards, and final evidence.
5. Resolve equivalent settings byte-identically across supported hosts. A host
   that cannot honor the effective contract stops with a named incompatibility;
   it does not silently inherit, downgrade, or select another backend.
6. Provide a one-release compatibility adapter for documented legacy variables
   and artifacts. It emits a deprecation/source receipt, never owns defaults,
   and refuses legacy/canonical conflicts. Cache-busted and numeric package
   version forms must resolve through the same typed version contract.
7. Preserve the approved delivery routing: minimum-sufficient quick Product and
   Design; exactly three or four quick Plan lenses; zero Build, Fix, Evaluate,
   and Engineering lens workers. Settings may select permitted values but may
   not create new authority or escape these policy bounds.
8. Require every acceptance criterion and Build slice to name its exact test
   files/selectors. Changed producers enumerate consumers and prove fresh-edge
   and deliberately severed-edge behavior; interface changes update fixtures in
   the same slice; every failure is classified product, test, infrastructure, or
   environment before a correction is authorized.
9. Execute validation once per unchanged evidence layer in this order: static,
   exact selector, changed-file/radius, proportional suite, then one terminal
   exact-candidate authoritative CI workflow. Broad validation defaults to GitHub
   Actions; local broad execution occurs only when approved settings explicitly
   select it.
10. Dispatch all pairwise-disjoint Build tasks and CI validation jobs in
    parallel. Serialize only for an explicit dependency, shared owner/state, or
    authority transition, and record the reason. Freeze the candidate for the
    authoritative workflow; a source/test change invalidates its receipts, while unchanged green
    layer fingerprints are cited rather than rerun.
11. On every red workflow, collect and classify all direct failures once, assign
    one owner per failure cluster, and issue one correction wave. After two Plan
    returns, consolidate remaining coupled generators, goldens, checksums,
    fixtures, manifests, and history ledgers into one bounded stabilization
    successor.
12. Register every owned cleanup target before use with exact run/task identity,
    stable path/ref/process identity, containment, creation receipt, and cleanup
    policy. Cleanup runs on success, failure, cancellation, interruption,
    timeout, handoff, and fail-safe recovery; it retains evidence, refuses dirty,
    foreign, symlinked, PID-reused, or ambiguous targets, and proves zero leaks.
13. Make alternate-worktree preflight atomic across repository/workspace
    identity, hook receipt, stable launcher, session identity, settings digest,
    and contract store. Dispatch workers with no inherited conversation turns,
    only the bounded engine stage envelope and selected artifacts.
14. Preserve CI least privilege, immutable action pins, hash-locked dependencies,
    credential-empty untrusted PR jobs, exact-head proof, pre-merge first-parent
    compatibility, and release refusal until the exact protected-main SHA is
    green. Superseded PR heads may cancel; protected-main/release runs may not.
15. Emit one closed wave-metrics receipt covering suite inventory, redundant
    families removed, local feedback, CI wall/runner time and parallelism,
    cleanup leaks, tokens, stage/phase duration, Plan returns, matrices, workers,
    worktrees, and every target/ceiling decision.

## Acceptance criteria

1. **AC-SET1 — Complete settings inventory.** The inventory covers code, flow
   graph, skills, agents, hooks, commands, workflows, CI, release tooling,
   tests, fixtures, generators, and packaging; every discovered setting-like
   value has exactly one canonical key or justified non-setting disposition,
   and duplicate authoritative defaults fail. Verify with
   `taskplane/tests/test_settings_inventory.py::test_every_operational_setting_has_one_canonical_owner`.
2. **AC-SET2 — Typed canonical authority.** Valid canonical settings load into
   the complete typed contract, while an unknown key, malformed type, invalid
   enum/range, unsupported stage model/effort/backend/transport, or cross-field
   conflict blocks before any state write or dispatch. Verify with
   `taskplane/tests/test_settings.py::test_valid_canonical_settings_load_typed`
   and `taskplane/tests/test_settings.py::test_invalid_or_unknown_settings_fail_closed`.
3. **AC-SET3 — Precedence and safe overrides.** Allowlisted CLI and environment
   overlays resolve in the declared order, never own defaults, and are
   receipted; a setting that weakens governance, changes authoritative backend/
   transport/store, expands scope, disables proof/cleanup, or raises a budget
   blocks without exact authority. Verify with
   `taskplane/tests/test_settings.py::test_precedence_migration_and_safe_override_contract`.
4. **AC-SET4 — Universal flow initialization.** Every Taskplane flow and named
   operational consumer initializes through the loader, binds one effective
   settings digest, and contains no prohibited direct governed-variable/default
   read. Verify with
   `taskplane/tests/test_settings_flow_wiring.py::test_every_flow_initializes_from_canonical_settings`.
5. **AC-SET5 — Cross-host, migration, and receipts.** Equal canonical input and
   safe overlays produce byte-identical effective settings across supported
   hosts; incompatible hosts stop; legacy adapters warn and receipt for exactly
   one release without owning defaults; numeric and cache-busted package versions
   resolve consistently; receipts are tamper-evident and secret-free. Verify with
   `taskplane/tests/test_settings_cross_host.py::test_effective_settings_are_portable_and_safely_observable`
   and `taskplane/tests/test_settings_migration.py::test_legacy_and_version_forms_migrate_without_second_authority`.
6. **AC-TST1 — Test-strategy contract.** Every acceptance criterion maps to
   exact selectors; every changed producer lists consumers plus freshness and
   severed-edge checks; interface/fixture changes share one slice; and failures
   cannot enter correction without product-versus-test/infrastructure/environment
   classification. Verify with
   `taskplane/tests/test_test_strategy_contract.py::test_design_and_build_contract_is_complete`.
7. **AC-TST2 — Evidence-based portfolio cleanup.** Every removed test maps to a
   retained exact selector protecting the same current contract or to an
   explicitly obsolete contract; at least six redundant families are removed,
   the suite reaches at most 230 files/4,200 collected cases, and the protected
   regression floor remains complete. Verify with
   `taskplane/tests/test_test_portfolio_contract.py::test_removed_tests_preserve_current_contract_coverage`
   and `taskplane/tests/test_test_portfolio_contract.py::test_portfolio_targets_are_met_without_count_only_deletion`.
8. **AC-TST3 — Progressive CI-first validation.** Validation advances static,
   exact selector, changed-file/radius, proportional suite, and one frozen-SHA
   GitHub Actions workflow; unchanged green fingerprints are cited without
   execution, broad local runs are refused by default, and any candidate source/
   test change invalidates direct check authority. Verify with
   `taskplane/tests/test_ci_execution_policy.py::test_validation_progression_requires_one_authoritative_ci_run`.
9. **AC-CI1 — Parallel bounded CI.** One pytest suite runs alongside
   pairwise-disjoint quality/package and browser jobs with settings-derived
   budgets and timeouts; every
   serialization has a recorded dependency/shared-owner/authority reason; PR
   supersession cancellation cannot cancel protected-main/release runs; and CI
   meets the p50/p95, runner-minute, first-matrix, matrix-count, and at-least-4x
   parallelism targets. Verify with
   `taskplane/tests/test_ci_execution_policy.py::test_ci_shards_cleanup_and_candidate_freeze_are_authoritative`
   and `taskplane/tests/test_ci_execution_policy.py::test_ci_metrics_meet_declared_targets`.
10. **AC-CI2 — One classified correction wave.** One red workflow produces a
    complete direct-failure inventory with product/test/infrastructure/environment
    classification and one owner per cluster; unchanged green layers do not
    rerun, and a third Plan return is mechanically converted to one bounded
    stabilization successor. Verify with
    `taskplane/tests/test_ci_failure_batching.py::test_red_matrix_is_classified_once_and_corrected_as_one_wave`
    and `taskplane/tests/test_ci_failure_batching.py::test_third_plan_return_consolidates_coupled_surfaces`.
11. **AC-CLN1 — All-outcome owned cleanup.** Success, failure, cancellation,
    interruption, timeout, and handoff each execute cleanup for every registered
    owned worktree, contract, process/process group, cache, generated state, and
    test artifact; evidence survives and the post-check proves exactly zero
    owned leaks. Verify with
    `taskplane/tests/test_owned_cleanup.py::test_cleanup_runs_on_every_terminal_outcome`
    and `taskplane/tests/test_owned_cleanup.py::test_cleanup_preserves_evidence_and_proves_zero_leaks`.
12. **AC-CLN2 — Unsafe cleanup refusal and recovery.** Cleanup is idempotent and
    replayable, refuses any foreign, dirty, symlinked, relocated, PID-reused,
    containment-invalid, or ambiguous target, reports cleanup failure without
    masking the original outcome, and never infers ownership from a prefix,
    branch, age, or process name alone. Verify with
    `taskplane/tests/test_owned_cleanup.py::test_cleanup_refuses_ambiguous_or_unowned_targets`
    and `taskplane/tests/test_owned_cleanup.py::test_cleanup_replay_is_exact_and_idempotent`.
13. **AC-P0 — Atomic startup and bounded workers.** Alternate-worktree startup
    either atomically proves workspace, hook, stable launcher, session, settings,
    and store identity before dispatch or creates no live contract/worktree; each
    worker has zero inherited turns and only its bounded stage envelope. Verify
    with `taskplane/tests/test_atomic_governed_preflight.py::test_preflight_is_atomic_before_any_worker_or_worktree`
    and `taskplane/tests/test_stage_bounded_handoff.py::test_worker_receives_no_inherited_conversation_turns`.
14. **AC-REL — Protected-main release truth.** PR validation retains least
    privilege, immutable action/dependency pins, credential-empty untrusted jobs,
    exact-head proof, and pre-merge first-parent simulation; release tooling
    refuses a tag until the exact protected-main SHA has terminal green CI.
    Verify with `taskplane/tests/test_release_tags.py::test_tag_requires_exact_protected_main_green`
    and `taskplane/tests/test_release_provenance.py::test_premerge_first_parent_topology_matches_release_gate`.
15. **AC-MET — Measurable outcome receipt.** One closed, redacted wave receipt
    records every baseline and target in this specification from non-cumulative
    sources, distinguishes billing truth from log upper bounds, blocks sign-off
    on a nonzero owned leak or unclassified ceiling breach, and names every
    serialization. Verify with
    `taskplane/tests/test_wave_metrics.py::test_wave_receipt_covers_baselines_targets_and_guardrails`.
16. **AC-REG — Protected current-contract floor.** Test pruning and settings/
    cleanup rewiring retain exact selectors for security and human authority,
    host/session/store identity, malformed/stale receipt refusal, cross-host/
    encoding/path portability, cache freshness, cleanup containment and races,
    CI pins/locks/permissions, and release tag/version/provenance. Verify with
    `taskplane/tests/test_governance_invariants.py`,
    `taskplane/tests/test_consolidated_authority.py`,
    `taskplane/tests/test_windows_portability.py`,
    `taskplane/tests/test_stage_cross_host.py`,
    `taskplane/tests/test_worker_contract_lifecycle.py`,
    `taskplane/tests/test_worktree_cleanup.py`,
    `taskplane/tests/test_release_tags.py`, and
    `taskplane/tests/test_release_provenance.py`.

## Non-functional requirements

- **security:** Settings and overrides never mint authority; malformed,
  unsupported, tampered, stale, conflicting, or governance-weakening values fail
  before state/dispatch. Selectors are structured and repo-contained rather than
  shell-evaluated. CI retains least privilege and immutable supply-chain inputs.
- **architecture:** One canonical settings document owns every configurable
  default and one typed loader is the only operational-consumer boundary. Flow,
  test, cleanup, cache, dashboard, and release consumers bind the same effective
  digest; no compatibility adapter becomes a second source of truth.
- **data-safety:** Cleanup and migration are atomic, idempotent, exact-owned,
  evidence-preserving, anti-symlink/PID-reuse, and fail closed on uncertainty.
- **sre:** Timeouts, budgets, concurrency, cancellation, retries, cleanup,
  terminal states, and recovery are bounded and observable for all outcomes;
  original failures are never hidden by cleanup failures.
- **integrability:** Supported hosts, CLI/environment overlays, hooks, workflows,
  CI, package-version forms, and one-release legacy artifacts share a versioned
  schema and deterministic incompatibility behavior.
- **privacy-compliance:** Receipts store bounded identifiers, digests, metrics,
  and redacted source classes, never secrets, raw environment values, full
  prompts/diffs, workstation identity, or unrelated private paths.
- **cost-finops:** Broad validation is CI-first; duplicate green execution is
  prevented; shards and worker sessions are bounded; the declared elapsed,
  runner-minute, token, worktree, and matrix targets/ceilings are enforced and
  reported.

## Contract handoff

```yaml
scope_paths:
  - taskplane/**
  - hooks/**
  - agents/**
  - skills/**
  - workflows/**
  - scripts/**
  - .github/workflows/**
  - .codex-plugin/**
  - .claude-plugin/**
  - docs/**
  - README.md
  - pyproject.toml
  - requirements-dev.lock
  - components.yaml
  - lenses/**
  - specs/spec.md
  - design/**
  - plan/**
out_of_scope:
  - unrelated user-facing feature redesign
  - private knowledge deletion
  - unowned or ambiguous cleanup targets
  - a second authoritative settings store
  - actual push, merge, tag, publish, or installation without its later gate
contracts:
  - contract:configuration.effective-settings
  - contract:delivery.flow-initialization
  - contract:validation.test-strategy
  - contract:ci.authoritative-validation
  - contract:lifecycle.owned-cleanup
  - contract:release.protected-main-green
  - resource:configuration.effective-settings-receipt
  - resource:lifecycle.cleanup-receipt
  - resource:delivery.wave-metrics
dod:
  local_test_command: >-
    python3 -m pytest <exact selector(s) from the approved task> -q
  authoritative_test_command: >-
    GitHub Actions exact-candidate matrix selected by the approved settings
```

## Dependencies and open questions

- Requirement dependencies: none.
- Material serialization reasons: Product must precede Design; Design must
  precede Plan; consolidated human authorization must precede Build; producing
  slices must precede their consumer freshness checks; implementation must
  precede direct zero-lens Evaluate; Evaluate must precede Engineering; exact
  protected-main green must precede any release tag. All other pairwise-disjoint
  work and CI shards execute concurrently.
- Open questions: none. Design owns the canonical file path, schema shape,
  compatibility adapter placement, test-family adjudication ledger, and cleanup
  component boundaries without changing this Product contract.

## Product focused-lens route

Taskplane selected a deterministic quick route of `product`, `security`, and
`cost-finops`. Those three quick lenses executed in parallel. The remaining 23
catalog lenses were not separately executed at Product because their Product
risks are either absent or expressed as binding acceptance/NFR constraints for
Design and Plan; the complete machine disposition ledger remains in the
Taskplane route receipt.
