# Harness refactor: corrected assessment

Date: September 14, 2026. Comparison: `1c4b63e7f2f0caecca5e6810c238ee598783f657` → `146104cc089872394bfa9db648635d06efd07577`, with the PR #22 integration snapshot and later 2.23.1 checked separately.

## Conclusion

**Keep the refactor's consolidation. Withdraw the earlier recommendation to restore `1c4b63e`.** Its large deletion count and passing older tests were insufficient grounds for that recommendation. The older snapshot contained stateless machinery behind rollout controls; the refactor made the current stateless phase runtime the normal path and removed the competing singleton runtime.

The cleanup was explicitly requested. The original task also records severe harness failures before the recommended older snapshot. No evidence establishes `1c4b63e` as the user's last usable version. Restoring it would reintroduce machinery and tests the user deliberately retired.

This does not establish that the refactor completed every objective or that the installed workflow is usable. It narrows the diagnosis: retain the useful architecture, trace specific remaining failures, and repair their actual owners. Tests verify the user's intended behavior; old assertions do not define it.

## 1. Intended work and original decisions

The [refactor specification](../backlog/harness-refactor-spec.md) explicitly calls for:

- One active v4 aggregate instead of a singleton loop plus a stage mirror.
- A bounded startup envelope containing only selected phase inputs.
- Removal of unreleased-state migrations, compatibility switches, fixed historical guards, repeated journeys, source-text assertions, and wording tests.
- Shared artifact, serialization, dispatch, and validation mechanisms.
- Continued human gates and evidence-backed completion, with the host retaining native execution and lifecycle responsibilities.

The original task, **[Start flow for today’s backlog](codex://threads/01a086fc-ee17-70d0-880f-f9f8d15b9990)**, confirms that these were active user decisions, not requirements inferred from existing tests:

| Original record | Consequence for this review |
| --- | --- |
| September 9: repeated Design sweeps, blocked reviewers, and no Build progress; the user requested that the harness be turned off. | The principal usability problem already existed before the large cleanup commit. |
| At `1c4b63e`: the prior response states phase-runtime cutover and full retirement remained unfinished. | This snapshot is not evidence of a completed, usable stateless product. |
| The user then requested full refactoring, old-test removal, stateless phases, parallel isolation, and the complete EM/Retro flow. | Preserving those outcomes takes precedence over retaining the old implementation. |
| September 10: the user explicitly requested removal of the v3 runtime, including its tests, and stated that tests must not define product behavior. | Restoring v3 or its obsolete test expectations would contradict the requested direction. |
| September 10: the user confirmed fresh starts were acceptable and no upgrade/migration code was needed. | Removal of old-run compatibility is intentional, not a regression merely because old state stops loading. |
| Structural targets were deferred while functional CI remained blocking. | Size, file counts, or import-cycle debt cannot alone establish that this integration violated the accepted release gate. |

The [retirement map](../reports/harness-refactor/test-retirement.md), [deleted-function inventory](../reports/harness-refactor/retired-tests.json), [build report](../reports/harness-refactor/direct-build.md), and [test-maintenance review](../reports/flow-wiring-review-20260910/full-suite-review.md) were available and should have been examined before the earlier rollback recommendation. They distinguish intended retirement from incomplete acceptance and explicitly avoid claiming one-to-one replacement of every removed test.

## 2. What the refactor actually changed

| Area | Before | After | Assessment |
| --- | --- | --- | --- |
| Normal phase runtime | `_stage_mode()` defaults to `disabled` for new/unbound runs; stage rollout and singleton paths coexist. | New runs select the v4 aggregate and `agent-runtime`; public dispatch contains exactly `schema`, `stage_runtime_dispatch`, and `obligations`. | **Retain.** Presence of old stateless helpers was not equivalent to stateless execution by default. |
| Runtime state | Singleton state, mirrored stage state, rollout/migration binding and recovery adapters. | Current aggregate drives progression; dashboard projection is not execution input. | **Retain.** Restoring the old snapshot would restore the competing paths. |
| Historical authority | R-0013-specific authority validation and retained Design-sweep checks contain fixed historical identities and assumptions. | Those historical owners and their tests are retired. | **Retain retirement.** These are not general native-host capabilities that need to be restored. |
| Common mechanisms | Storage, trace, model-selection and startup helpers sit inside the enforcement module; serializers/manifests repeat across owners. | Responsibilities move to storage, audit, host capability, handoff and shared primitive modules. | **Retain.** Many deleted lines are extraction or consolidation, not lost behavior. |
| Lenses and phase results | Separate historical paths and fixtures for Design, review and other phases. | Current phase dispatch/collection carries selected evidence through the downstream flow. | **Retain the shared path.** Evaluate/EM behavior must follow their current direct-evidence contract, not obsolete all-lens assumptions. |
| Native-host overlap | Taskplane adds its own tool admission, contract and hook-readiness rules around native tools. | Those rules substantially remain. The examined contract screener and release predicate are unchanged across the refactor. | **Incomplete simplification remains here.** It is not evidence that removing the old runtime caused the mismatch. |

For the exact source comparison, the syntax trees of `taskplane_lite.screen_tool` and `tp._is_release_command` are identical. `skills/tp-product/SKILL.md` and `hooks/hooks.json` are byte-identical. The native delivery adapter's diff consolidates serialization; it does not replace native spawning or waiting with a new executor.

The changed repository hook bridge also stops trying fallback execution in a folder with no generated launcher. That is a scope/installation behavior change, separate from phase/test retirement; it should be judged through actual installation and hook-path incidents.

The native-tool simplification objective is therefore only partly addressed. Remaining overlapping responsibilities include the tool whitelist around native file/command access, receipt-based admission around native lifecycle events, and the governed-command launch/wait/output/cleanup machinery. These also carry domain-specific scope and evidence bindings, so merely counting wrappers or deleting them is insufficient. The bounded design question is which responsibilities the native host can supply while Taskplane retains only the required domain checks. No new scheduler, tool broker, or proof framework is proposed here.

## 3. DoR and DoD: retirement is not disappearance

The earlier statement “17 tests became six” is numerically correct but was an inadequate product conclusion.

| Removed group | What the old tests actually did | Current behavior / implication |
| --- | --- | --- |
| Eight sign-off tests | Constructed mutable singleton state, changed workspace files and old `.em-review` findings, then called `_signoff_dod` directly. Some pinned the old lens-disposition representation. | Sign-off now consumes immutable Engineering-sealed evidence. Missing evidence returns a failed verdict; it does not reconstruct approval from mutable workspace files. Restoring the old tests verbatim would demand retired behavior. |
| Two payload/trace tests | Started the old loop without the current selected requirement and manually moved state to later steps. | DoR still runs in `dispatch._prepare_phase_contract`, including blockers and warnings. Product's requirement gate still calls `requirements.product_dor`. Current phase startup and transition checks exercise the replacement path. |
| One dashboard visibility test | Manually forced the loop to sign-off and asserted that the HTML contained the strings `DoR` and `DoD`. | Useful intent, weak proof: it did not establish correct criterion-level verdicts through the installed stateless journey. The renderer remains present. A missing equivalent presentation check is an evidence gap, not proof that the feature was deleted. |
| Four non-Build handoff tests in a deleted file | Compared flow JSON dictionaries and exact skill phrases, including retired rollout modes. | These were configuration/prose pins, not executed non-Build handoff journeys. Their passing count should not have supported the earlier usability recommendation. |

Source verification after the refactor:

- `dispatch._prepare_phase_contract` computes DoR, records its verdict/blockers/warnings, and refuses an unready phase.
- `gates._validate_product_gate` checks Product requirement readiness and links its context files to the graph.
- `gates._compute_signoff_dod` still checks requirement coverage, aggregate scope, tests, Engineering evidence, and knowledge lint.
- `gates.signoff_dod` reads the sealed terminal verdict and fails when required integration evidence is missing.
- The dashboard's `_widget_dor` and sign-off renderer remain. The helper bodies were not deleted with the old test.

These implementation facts do not resolve the user's current presentation complaint. The acceptance criterion should be **correct, visible DoR/DoD for the current phase**, with evidence and unresolved gaps. It should be verified against the current workflow without resurrecting the old singleton or treating the words “DoR” and “DoD” as sufficient proof.

## 4. Where the observed friction belongs

| Failure or change | Precise attribution |
| --- | --- |
| Product contract rejects document writing | The Product skill's activation example is unchanged in `146104c`; it lacks a documentation write allowance. The earlier six-snapshot policy probe reproduced this before and after the refactor. |
| Read-only contract blocks Codex shell-based access | The relevant screener is unchanged across `146104c`. History traces the shell boundary to `73dd54e` on August 28. Its suitability for native Codex tools remains an integration problem. |
| Ordinary contract release is unavailable | The release predicate is unchanged across this refactor. Current scope/completion recovery should be repaired where it is enforced. |
| Product graph linking is refused in current 2.24.0 | The current control-operation whitelist was introduced in `61db54c` on September 14 and omits `graph link`. Earlier snapshots also reject it through their broader shell wall. These are distinct implementations of the mismatch. |
| Hook readiness, trust and stale installation errors | The September 10 [onboarding RCA](onboarding-rca-2026-09-10.md) records competing event identities, launcher/readiness disagreement, false-success display, stale contracts and failed acknowledgment persistence. These are concrete incidents; removing unrelated legacy tests is not their established cause. |
| Stricter budget and advisory policy | `e8a8db4` on September 11 separately strengthens budget enforcement and removes the advisory route. Its user impact must be evaluated as that policy change; it is not part of `146104c`. |
| Quality tools pass readiness but cannot start in Evaluate | **Confirmed integration defect exposed by the refactor's evidence-execution path.** The quality probe inherits the invoking environment. The new evidence-command boundary instead uses an empty home, disables Python user-site packages, and clears `PYTHONPATH`. A tool installed in the user's site directory can therefore pass readiness and be unavailable during execution. The sanitized checkpoint environment predates the refactor, but applying it to this evidence-command path is new in `146104c`. |

The remaining design issue is that Taskplane's permission and readiness layers can reject work already supported by the host and required by the phase. Preserve meaningful domain checks—selected inputs, scope, evidence and decisions—while removing or correcting overlapping controls that cannot perform their own workflow. This reassessment does not authorize disabling safeguards or implementing a new control system.

### Concrete finding: quality readiness uses a different environment from execution

**Priority: P1 — blocks Evaluate when required Python tools are installed in the user's site directory.** In the checked post-refactor snapshot, all four Python quality probes reported `runs`; execution then failed with `No module named ruff`. The same mismatch is reproduced independently by running the reported interpreter with the execution environment. Current source retains the inconsistent boundary: [quality probe](../taskplane/runnability.py#L266), [evidence command environment](../taskplane/governed_commands.py#L1267), and [isolated environment](../taskplane/governed_commands.py#L503).

The fix direction is to resolve and check tools in the exact environment that will execute them, and report missing dependencies before dispatch. This preserves isolation and meaningful quality checks. It does not require restoring the singleton runtime or obsolete tests. This is a specific integration defect associated with the refactor, not evidence that its overall consolidation should be undone.

## 5. Verification and its limits

The retained test results from the first comparison remain valid as local test executions, but they do not establish that the pre-refactor snapshot is the better product.

For this reassessment, current-runtime checks were selected for fresh v4 initialization, exclusion of unrelated Design/context, projection independence, cross-host startup, refusal of missing sign-off evidence, and serial/parallel delivery through EM and Retro. The tested PR #22 snapshot has byte-identical dispatch, gates, loop, phase harness, screener, and integration-test files to `146104c`.

The first executions stopped at the isolated Evaluate command's lint dependency. The failed command was Python `-m ruff check app.py`; its retained output digest exactly matched `No module named ruff` with user-site loading disabled. Ruff existed only in the invoking user's site directory. Missing dependencies alone are not proof of a regression; the contradictory readiness verdict is the specific defect identified above. An isolated interpreter was prepared from the already-installed dependencies, without changing Taskplane code, its tests, or its enforcement policy. Results and any remaining gaps are recorded with the diagnostic evidence.

| Snapshot | Initial selected checks | With required tools available in the execution environment |
| --- | --- | --- |
| PR #22, `7a67072` | 13 passed, 2 failed at Evaluate tool execution | **15 passed**, 45 deselected |
| 2.23.1, `a76e7de` | 14 passed, 2 failed at Evaluate tool execution | **16 passed**, 69 deselected |

The passing selection includes serial and parallel delivery through Engineering and Retro, bounded startup, stable phase identity, and refusal of missing integration evidence. The counts differ because the selected existing tests changed between revisions. These are focused checks, not full-suite results. Intermediate environment-setup failures are also retained: copying Python packages alone initially omitted Ruff's executable and Bandit's dependencies. The final isolated environment verified Ruff 0.12.9, mypy 1.17.1, Bandit 1.8.6, and pytest 9.1.1 before the passing runs. No dependency was downloaded and no product/test source was patched to obtain those passes.

All phase-worker messages, native lifecycle events, and human decisions in these local tests are simulated test inputs. They are not an installed Codex/Claude canary or approval of a release. No live-host usability certification is claimed.

Commands, results, source comparisons, the readiness mismatch, dependency versions, and a file-hash index are retained in [the reassessment evidence directory](../.taskplane/diagnostics/refactor-reassessment-2026-09-14/).

## 6. Revised recovery direction

1. **Retain `146104c`'s active stateless runtime and intended retirements.** No blanket revert is supported by this investigation.
2. Correct the current Product contract, required controls, artifact presentation and normal completion as one bounded user journey. Preserve native tool access and meaningful scope restrictions.
3. Restore explicit DoR/DoD presentation from current phase evidence. Do not recreate obsolete state to make an old test pass.
4. Inspect later enforcement changes independently. If a historical comparison is useful, a **post-refactor** snapshot such as 2.23.1 `a76e7de` is a more relevant comparison point than `1c4b63e`; it is not yet a certified restoration target.
5. Accept recovery when the installed Product draft–save–finish journey and one small stateless governed change complete without manual harness repair, with visible readiness/completion and a meaningful scope refusal.

## 7. Explicit review readiness and completion

| Gate | Verdict | Evidence / remaining work |
| --- | --- | --- |
| This source check's DoR | **Met** | Exact before/after revisions, original user decisions, retirement records, and executable replacement tests are available. |
| This source check's DoD | **Met for the bounded comparison** | Intent and implementation were distinguished; removed DoR/DoD tests and replacement behavior were checked; remaining overlap and one concrete integration defect were identified; focused tests and corrected reports are saved. This is not a one-to-one audit of every retired test. |
| Product recovery DoD | **Not satisfied** | Required Product operations, visible phase DoR/DoD, consistent readiness, normal completion, and an installed governed journey still require repair and acceptance. |

**Review result:** the earlier rollback recommendation is withdrawn. No rollback or plugin replacement was performed. The current Product usability problems remain valid work, with corrected attribution.
