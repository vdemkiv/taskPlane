# R-0002 delivery plan

## Outcome

Wire the existing Taskplane capabilities into one current-run control plane, prove the public lifecycle, remove tests that do not protect current product behavior, simplify CI to one unsharded Python 3.12 suite plus genuinely independent validations, and ship the next marketplace version with an installed-runtime proof and complete Retro archive.

This plan incorporates four parallel QUICK reviews: Architecture, Security, Testability, and Cost/FinOps. Build, Fix, Evaluate, and Engineering use zero lens workers. Design may select up to sixteen justified quick lenses, but sixteen is a cap rather than a fill target. The selected set must be automatically assigned to real host workers and every lifecycle event must be logged.

## Binding decisions

- One validated settings document and one active-run identity are pinned before any stage effect. The default JSON is prepopulated and usable without source edits.
- Every Design entry runs current-run decomposition before dynamic selection. Selected lenses, assigned workers, starts, activity, terminals, usage, and accepted results must agree exactly.
- The dashboard, host surface, and dependency graph render from one sealed current-run snapshot. Delayed or unacknowledged publications cannot become current or enable mutation.
- Build, Evaluate, and CI produce one typed failure record before correction. Only an exclusive product failure may enter Fix; test, infrastructure, environment, mixed, and unknown failures follow their owned recovery or hold route.
- Build closes a quality receipt immediately after implementation: import/static checks, exact selector collection, exact behavior, changed consumer radius, semantic severed-edge proof, and applicable fixture co-change.
- Run evidence lives beside the private run in seven separately manifested classes: `dashboard`, `dependency-graphs`, `telemetry`, `agent-activity`, `validation`, `cleanup`, and `retro`.
- Cleanup seals evidence before deletion, removes only exact-owned ephemera, rejects ambiguous or overlapping targets, preserves all run artifacts, and verifies zero leaks after success, failure, cancellation, interruption, handoff, timeout, and recovery.
- Tests are kept for current behavior, security/authority, cleanup safety, portability, release provenance, real browser rendering, graph provenance, typed routing, and telemetry truth. Source-shape, byte-identity-as-correctness, line/count ratchets, stale history, duplicates, stale fixtures, and ceremony are removed after replacement public-journey proof.
- GitHub Actions runs the complete pytest inventory once on Python 3.12. Only truly independent browser, quality/package, interpreter-import, OS/portability, security, and no-egress validations run beside it. There are no pytest shards and no join pseudo-test disguised as `tests`.

## Implementation waves

### W0 — authority foundations, parallel

- `FAILURE-BUILD-QUALITY`: canonical failure contract and Build quality receipt.
- `RUN-ARTIFACT-BOUNDARY`: private per-run artifact manifest and safe cleanup boundary.

These tasks are pairwise disjoint.

### W1 — disjoint producers, parallel

After the artifact API exists:

- `SETTINGS-DESIGN-GRAPH`: simple defaults, dynamic Design policy, automatic decomposition, sealed stage graph.
- `DASHBOARD-GRAPH-PUBLICATION`: snapshot-only dashboard/native rendering, visible responsive graph, monotonic current head.
- `TERMINAL-METRICS`: measured-or-unavailable usage and evaluator truth.

Their source and test ownership is disjoint. They serialize only behind the artifact boundary they consume.

### W2 — control-plane integration, serialized

`CONTROL-PLANE-HOST-WIRING` owns `loop.py`, `tp.py`, the host hooks, and the Design host role. This is the only intentional single-task wave because these are the shared lifecycle composition and native dispatch surfaces. It binds all W0/W1 producers once, executes the exact selected worker set, records complete activity, closes Build quality before Evaluate, and routes failures before correction.

### W3 — terminal lifecycle proof, serialized

`RUN-ARTIFACT-LIFECYCLE` exercises every terminal outcome after all producers are wired. Cleanup cannot be safely proven while producer evidence is still being defined.

### W4 — test-value adjudication, serialized

`TEST-VALUE-ADJUDICATION` audits the entire tracked test portfolio, preserves the protected floors, and removes or rewrites low-value families only after replacement journeys pass. It is serialized because it has one repository-wide inventory and deletion authority.

### W5 — CI and release, parallel

- `CI-TOPOLOGY`: one unsharded Python 3.12 suite, direct typed receipts, independent validations only.
- `RELEASE-PACKAGE`: next version, package membership, marketplace archive, installation behavior, provenance.

They both consume the final test inventory but own disjoint workflow/runner and release/package surfaces.

## Acceptance selectors

1. Current Design graph and dashboard:
   - `taskplane/tests/test_r0002_control_plane_journey.py::test_fresh_design_derives_current_run_graph_before_settings_driven_lens_dispatch`
   - `taskplane/tests/test_dashboard_browser.py::test_real_browser_production_refresh_styles_and_shows_dependency_graph`

2. Automatic dynamic Design execution:
   - `taskplane/tests/test_r0002_control_plane_journey.py::test_settings_change_reselects_actual_workers_and_receipts_candidates_workers_and_waves`
   - `taskplane/tests/test_r0002_control_plane_journey.py::test_host_executes_exact_selected_worker_set_once_and_conserves_activity_events`

3. Failure classification before correction:
   - `taskplane/tests/test_r0002_failure_correction_journey.py::test_build_ci_and_evaluate_persist_one_typed_failure_record_before_any_correction`
   - `taskplane/tests/test_r0002_failure_correction_journey.py::test_test_infrastructure_environment_mixed_and_unknown_failures_never_enter_product_fix`

4. Terminal telemetry truth:
   - `taskplane/tests/test_r0002_terminal_journey.py::test_retro_exports_measured_or_unavailable_usage_with_evaluator_identities_before_cleanup`

5. Build quality and test value:
   - `taskplane/tests/test_r0002_build_quality_journey.py::test_build_quality_receipt_collects_exact_public_edge_selectors_before_evaluate`
   - `taskplane/tests/test_r0002_test_value_journey.py::test_changed_producer_names_consumers_fixtures_freshness_and_severed_edge`

6. CI topology:
   - `taskplane/tests/test_r0002_ci_journey.py::test_github_actions_runs_one_unsharded_python312_suite_plus_only_independent_validations`

7. Durable artifact and cleanup lifecycle:
   - `taskplane/tests/test_r0002_run_artifacts_journey.py::test_separate_dashboard_graph_telemetry_and_agent_activity_artifacts_survive_all_cleanup_outcomes`
   - `taskplane/tests/test_r0002_run_artifacts_journey.py::test_activity_log_is_bound_append_only_and_complete_for_every_worker_outcome`

Every changed producer enumerates its consumers, freshness binding, semantic severed-edge proof, and applicable same-slice fixture rule in `plan/tasks.json`.

## Validation progression

For each implementation slice:

1. Compile/import and focused static checks.
2. Collect its exact selectors.
3. Run its exact selectors once.
4. Run changed producer-consumer radius and semantic severed-edge proofs.
5. Run one proportional protected-floor suite after slices converge.
6. Run one frozen-candidate authoritative GitHub Actions execution.

Every red is classified before any code or test correction. An unchanged green layer is cited rather than rerun only when candidate, files/tests, settings, selectors, tool/runtime, environment, and outcome fingerprints are equal.

## Baseline and target

| Metric | Baseline | Target |
|---|---:|---:|
| Test portfolio | 229 files / 4,059 cases / 84,104 LOC | Classify 100%; remove every evidenced low-value family; no arbitrary count/line cap |
| Focused feedback | 39 cases in 4.01s | exact-selector p95 under 60s |
| Changed-radius feedback | not yet attributable | p95 under 5m |
| CI critical path | about 13m10s | p95 at or below 15m |
| Runner time | current value unavailable | at or below 28 minutes |
| Parallelism factor | 2.11× | dispatch every ready disjoint task; at least 2.5× when three are ready |
| Cleanup leaks | not validly proven | exactly zero owned leaks |
| Token use | unavailable, not attributable | every attempt measured or explicitly unavailable; never false zero |
| End-to-end wave | 24h58m09s | at or below 12h; active implementation at or below 8h |

## Finalization

After the implementation tasks pass: one proportional suite, one authoritative GitHub Actions matrix, zero-lens Engineering sign-off, merge to clean `main`, next-version consistency and package build, removal of the installed stale version, installation and executable verification of the new marketplace candidate, terminal metrics seal, evidence-preserving cleanup, and one Retro ZIP containing dashboard, graphs, telemetry, agent activity, validation, cleanup, release provenance, and measured-or-unavailable token/evaluator evidence.
