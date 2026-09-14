# Review and planning recovery

**Review state: Product, Design, and Plan presented; implementation completed and verified locally; v2.23.10 source commit, push, and package builds authorized.**

Earlier local edits were made prematurely under the preceding “fix it” instruction. Build was paused and these phases were presented. The subsequent instruction to follow this document authorized the three batches below. The later instruction to finalize, bump the version, build packages, and push a commit to main authorizes source delivery as v2.23.10. Package provenance records identify the actual built commit, and the task's delivery response records the push outcome. The verification record below replaces the preliminary seven-test result; no installed-host or canonical EM acceptance is claimed.

## Product: intended behavior

The user requested a whole-repository code review, then Product → Design → Plan for its findings and the failures encountered during that work. The installed 2.23.9 workflow failed both requests. The user removed the plugin and explicitly authorized RCA, fixes, and continued model-led Product → Design → Plan. This document is that working package; it is not a fabricated plugin gate or canonical EM sign-off.

The product outcome is a usable native-tool workflow with truthful evidence and recoverable failures. Source review must work on a clean repository without inventing a diff. A prepared worker must be able to read its exact input in its actual native child session. A child that stops before producing output must remain a failed attempt, with a supported recovery path. Existing authorization, isolation, and evidence checks must remain effective.

| Requirement | Acceptance |
| --- | --- |
| P1: native child input | An authenticated, started child reads its pinned phase input from the parent-owned run. Native session IDs remain unchanged. Unrelated, unstarted, foreign-workspace, and retired children cannot inherit the run. |
| P2: failed-attempt recovery | Missing output is a typed failure after an observed terminal, not an uncaught file error or success. Exact-operation recovery preserves the old attempt and evidence, releases its contract, and permits an attributable bounded retry. No synthetic success, unlimited retry, or resource waiver. |
| P3: native review launch | The host's observed collaboration tool names normalize through one explicit adapter. Role restrictions and signed dispatch bindings still apply. |
| P4: whole-repository scope | A clean repository can be selected explicitly as a pinned source snapshot. An empty comparison cannot silently stand in for it. |
| P5: evidence truth | A later failed validation remains visible after a prior pass. Failure history survives repeated successes. Genuine sandbox evidence is recognized by its consumer; unsupported booleans remain insufficient. |
| P6: lifecycle truth | Prepared reviewer slots are distinguished from observed native starts and collected results. A blocked launch does not count as a started reviewer. |
| P7: dependency correctness | Known Python submodule imports create dependency edges. Repairing a tool at the same PATH does not leave a cached negative runnability result indefinitely. |
| P8: operating discipline | Reuse reported green CI as the baseline. Run targeted regression checks for changed behavior. Keep token measurements attributable and distinguish cached input, output, and extraction work. Avoid duplicate setup, blanket suite reruns, and restoring a separate controller. |

H1–H5 and the two dependency findings are retained from the source review of commit `7130773bdb2c3cdfac93dcff2ccf5477495275c9`. H6 remains withdrawn: minimal dashboard acknowledgment succeeded. An environmental Git fixture failure is excluded from product defects. The failed review sign-off transition is a recovery incident, not evidence of successful sign-off.

## RCA

The planning run `338c7d324d0343acba7840447c62817d` emitted Product operation `phase-attempt-8b1307404670865d09bca48535b8aa92`. Native child metadata identifies a distinct child conversation and the correct parent conversation, task name, and checkout. Its recorded command supplied the emitted startup unchanged and failed with `phase input refused: phase input has no current stage`.

The phase-input consumer locates the run using the workspace locator. Storage partitions that locator by the process's native conversation ID. Native lifecycle hooks bind the worker in the parent session, but the child's ordinary CLI command does not restore that authenticated storage binding or its exact contract slot. A path and slot in a prompt do not establish a process binding. This disconnect is the startup failure mechanism; regression coverage must cross the parent/child session boundary through the actual CLI entry.

The subsequent native terminal was recorded. Both exact-operation collection and reconciliation tried to read `specs/requirement.json`, which the failed child never created. The existing retry path handled expired, unbound pending workers only. Together these paths trapped a started, terminated worker behind its missing success artifact. Transport completion must not be confused with a valid Product result.

The earlier review exposed related integration gaps: incompatible aliases at the native tool boundary (H1), diff-only scope on a clean checkout (H2), evidence transitions that reject a later failure (H3), mismatched sandbox evidence schemas (H4), and prepared slots counted as dispatched (H5). Existing tests exercised isolated interfaces and simulated same-session paths; passing those tests did not establish this native journey.

My handling amplified the incident: I reran broad validation despite reported passing CI, spent excessive context on repeated control calls, and presented a skill's coordinating-model terminology as if a separate orchestrator component remained installed. The separate controller was removed; it must stay removed.

## Design: native adapters and existing owners

### Child binding

Extend the existing Codex identity adapter. Resolve only the current child UUID's bounded host metadata, verify parent, task, and checkout, and match its exact active lifecycle-bound contract. Verify the existing signed contract authority before selecting the parent storage namespace and child slot for one CLI invocation. Use invocation-local context so native environment identity and unrelated sessions remain unchanged. A prompt, role name, or environment slot alone cannot select a parent run.

Apply the binding before ordinary CLI storage/settings lookup. Hooks retain their existing event binding. Reuse the same binding in flat and package imports, and fail closed on conflicting explicit slot selection. Do not introduce a scheduler, provider wrapper, file reader, subprocess supervisor, or replacement controller.

### Native ownership and simplification

| Responsibility | Owner | Taskplane's permitted role |
| --- | --- | --- |
| Model selection, agent creation, dispatch, interruption, waiting | Native model tools | Supply a task and consume the returned identity/outcome. Do not wrap or replace the native lifecycle or impose a second wait-duration policy. |
| Commands, file operations, process management | Native execution/file tools | Declare the operation and relate its result to the selected task. No new executor, parser framework, file reader, or supervisor. |
| Permissions, sandboxing, escalation | Native host | Request the supported native operation with its actual scope. Do not invent another permission or sandbox system. |
| Usage observations | Native usage records | Attribute observed counters to the existing task and preserve unknowns. Do not recreate a model-side billing meter. |
| Requirements, review scope, artifact validity, dependency relationships | Repository domain code | Keep only the rules needed to decide whether the requested repository work is complete. |
| Product → Design → Plan presentation | This conversation | Present concrete documents and await the user's Build decision. No removed plugin or separate controller is needed to conduct these phases. |

The preferred change extends existing identity and evidence consumers. Restoring a separate controller, adding a generic host abstraction framework, or removing all domain evidence checks are rejected: each would enlarge the change or weaken the requested review. An existing hook that merely duplicates a native capability should be removed or reduced to recording its result. Any hook retained must name the repository-specific rule it enforces.

### Failure recovery

Keep native terminal observations separate from semantic candidate validation. Reconciliation records a typed missing/invalid-output refusal, preserves usage and original preparation, and releases the exact stopped child using its observed terminal. A bounded, attributable retry uses the existing phase retry journal and routing owner. Active workers, foreign operations, Build effects, stale authority, and exhausted budgets retain their existing restrictions. No failure path advances to Design or marks Product passed.

### Review scope, evidence, and status

Use an explicit snapshot scope backed by a pinned tracked-file inventory for repository review. Keep base/head comparisons explicit. Normalize supported native tool aliases at the existing host boundary, without permissive suffix matching. Retain validation attempt history and consume the recorder's verified sandbox evidence through the existing receipt authority. Count prepared slots separately from lifecycle-confirmed starts. Preserve incomplete-review and approval guards.

### Dependencies and validation

Resolve imported names only when they correspond to known Python submodules; retain ordinary symbol-import semantics. Reprobe cached negative runnability outcomes when the resolved executable or dependency state changes, so a repaired tool can recover without changing PATH. The initial draft reprobed every negative result; checking executable identity instead preserves sharing of unchanged results and avoids the repeated setup cost identified in the review. Add narrowly scoped regression journeys at these boundaries, then run only the affected module groups and configured source checks.

## Plan: execution order and evidence

| Task | Dependencies | Scope | Required evidence | State |
| --- | --- | --- | --- | --- |
| T1: authenticated child CLI binding | none | Codex identity, storage namespace, contract slot, CLI entry | Parent/child entry succeeds; foreign/unbound/retired contexts refuse; identities unchanged | Verified locally |
| T2: missing-output recovery | T1 interface settled | phase collection/retry, terminal release | Stop without output remains failed, exact recovery and bounded retry work, no phase advance | Verified locally |
| T3: native tool names and redundant restrictions | none | host tool adapter and screening | Observed collaboration names work; native wait arguments pass through; domain role restrictions remain | Verified locally |
| T4: repository scope | none | review target and startup inventory | Clean snapshot includes tracked source; incompatible diff options refuse; reviewer input actually exposes selected source | Verified locally |
| T5: validation evidence | none | review execution recorder and projection | pass→fail and fail→pass→pass preserve failures; producer record verifies at consumer; renderer evidence stays independent | Verified locally |
| T6: reviewer status | T3 | review lifecycle projection | prepared≠started; blocked launch has zero starts; observed start increments once, including concurrent starts | Verified locally |
| T7: dependency and runnability defects | none | dependency imports and negative cache | Known submodules produce edges; same-PATH tool repair recovers; unchanged successful probes remain shared | Verified locally |
| T8: documentation and verification | T1–T7 | this package, native dispatch instructions, targeted checks | Record exact checks and limitations; remove misleading recovery/terminology guidance; audit each retained hook for a domain-specific purpose | Verified locally |

Execution followed three batches: (1) native integration and recovery, T1–T3; (2) review scope and evidence, T4–T6; (3) dependency/cache fixes and final documentation, T7–T8. The final audit refined T2's handling of parseable but incomplete output and removed the remaining Taskplane wait-duration/reissue rules from T3's emitted instructions. Those boundaries were rechecked before accepting the local implementation. Simulated native metadata and real isolated CLI calls do not prove a live installed-host journey.

Before accepting the implementation, verify that no new native-tool replacement or second controller was introduced, all confirmed findings have a test or evidence disposition, the unsuccessful historical runs remain unsuccessful, and the proposed scope does not overwrite unrelated planning artifacts. Reuse reported green CI as baseline evidence, then run affected tests and configured source checks. A new full-suite rerun requires a concrete change-related reason.

Implementation follows this plan. Reinstalling the plugin, publishing a release, pushing changes, and asserting live installed-host acceptance are not implied by local test success. Existing runtime history and unrelated planning artifacts remain evidence of their original runs.

## Implementation dispositions

T1/T2 resolve the observed Product startup and recovery incidents. T3 resolves H1; T4 resolves H2; T5 resolves H3/H4; T6 resolves H5; T7 resolves D1/D2. H6 remains withdrawn. No prior failed run was marked successful.

Repository review is explicit: `review start --scope repository --workspace <local-checkout>`. It inventories the pinned commit's tracked files, rejects changed tracked content, and excludes untracked files. PR/base comparisons retain diff scope. Remote snapshot acquisition is not a second startup path: acquire the repository first, then select its local checkout. Reviewer views carry the selected files and revision, and briefs direct native source reads despite the intentionally empty patch.

Reviewer start counts are read from existing native producer assignments; Start does not update another lifecycle ledger. Concurrent or repeated observations count each slot once. Valid leased artifacts can still be collected under the existing evidence rules when host receipts are unavailable, while the recorded native start count remains zero. Artifact validation and approval guards remain intact.

## Retained hook audit

No hook registration, controller, executor, file reader, permission system, or usage ingester was added. The installed plugin remains removed. Existing Codex command/file/profile adapters remain the native boundary; this patch does not restore the retired controller or general process runtime. Non-Codex compatibility transports were not rewritten by this remediation.

| Hook entry | Repository-specific purpose retained | Native responsibility |
| --- | --- | --- |
| `screen` | Active task scope, read-only source/scoped result writes, explicit task budgets, artifact provenance | Host permissions, sandbox enforcement, command and file execution. Native coordination aliases use one adapter; Taskplane's wait minimum and list prohibition were removed. |
| `screen-dispatch` | Match the exact signed task/lease and its approved role/scope | Native agent creation, model execution, interruption, and waiting |
| `screen-skill` | Detect a skill conflicting with the current Taskplane role or phase authority | Native skill loading and user authorization; no new approval policy was added |
| `screen-render` | Record which required Taskplane artifact was presented; observation only | Native rendering and panels |
| `host-native-check` | Discover the package's configured presentation surfaces and report readiness | Host surface availability and enablement |
| `context` | Present the saved goal/phase and recover contracts only from existing terminal/committed evidence | Native session startup and resume |
| `subagent-start` | Bind an observed child to its existing task input and result slot | Native child identity and Start event |
| `subagent-stop` | Associate terminal evidence with authored outputs; preserve typed refusals for incomplete pre-build work | Native Stop event and process lifecycle |
| `session-verify` | Check required artifacts/submissions and issue the existing bounded delivery reminder | Native conversation lifetime; no polling service or retry engine added |

The emitted wait description now names only the outstanding task set and native ownership. Timeout, polling, and reissue rules are not enforced by Taskplane. Reviewer role restrictions remain repository workflow rules, not alternative native tool implementations.

## Verification record

Reported green CI remains the baseline. These local checks target the changed boundaries; the repository's full suite was not rerun. Tests use isolated stores and simulated native records, with real source CLI invocations, Git inventories, disposable validation commands, and concurrent producer registration where relevant.

| Check | Evidence |
| --- | --- |
| Native boundary group: child identity, session isolation, live budget, bounded handoff, native file tools, phase recovery, phase retry | 99 passed in the final combined run. Includes missing output, malformed JSON, list/object candidates without complete evidence, unchanged native identity, structured CLI refusals for retired/foreign/unbound authority, bounded retry and replay. |
| Final review regressions + production integration + two affected routing cases + sandbox validation journey | 22 passed. Clean repository source reaches reviewer views; incompatible scope refuses; pass/fail history, sandbox receipt mismatches, independent rendering, concurrent starts, and artifacts without invented starts are covered. |
| `test_dependency_review_regressions.py` + `test_depgraph.py` + `test_runnability_probe.py` | 33 passed, 7 subtests passed, 1 skipped because Go is not installed. Same-PATH repair triggers one fresh probe; unchanged results remain shared. |
| Ruff over `taskplane`, `hooks`, `scripts` | Passed |
| Configured strict mypy | Passed across all 114 production source files |
| Skill definitions, generated CLI reference, whitespace | Passed |
| Phase registry after engineering-instruction update | 34 passed. Both phase definitions were regenerated through the existing contract producer with the updated skill content hash. |
| v2.23.10 preparation | 21 release freshness, compatibility, and release-boundary tests passed. Version surfaces agree; both extracted package layouts load the exact version and settings; plugin and engineering skill validators passed. Final archive checksums and source provenance are generated after the source commit. |

The larger affected review group produced 116 passes and two failures. The truthful dispatch-count change required preserving the existing artifact-only collection rule; that regression now passes with zero invented starts. The other failure occurred while source reload used a different module than the fixture's mock; it passed alone and again in the final review group. No full-suite rerun was used to hide these dispositions.

The documentation update initially exposed stale phase-definition hashes. Both affected definitions were regenerated with the existing contract producer; the 34 registry tests and final 99-test native group passed afterward.

CI for `e7bd9ce` then exposed a second binding omitted from that update: `evals/scenarios/tp-engineering.json` still named the flow before the `--scope` and `--workspace` instructions were added. The recorder and scenario-validation checks failed; 4,401 tests and 524 subtests passed, and all seven other CI jobs passed. Both failures reproduced locally. Regenerating only the scenario's input fingerprint through `eval_scenario.fingerprint` fixed them; the complete recorder and scenario groups passed 108 tests and 203 subtests. Evaluation assertions and workflow checks remain unchanged.

The remaining acceptance limit is a live installed-plugin journey. Local fixes and tests do not establish installation, release, host hook enablement, or EM sign-off. The original telemetry and failed runtime history were preserved; this implementation work is not added to the original review's reported token counters.
