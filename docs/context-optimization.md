# Context delivery and execution readiness

Taskplane supplies current task inputs with exact source and consumer bindings. Context receipts prove returned data, not approval or model attention. The host conversation is not removed or replayed into worker briefs.

## Consume current inputs

Use the loaded runtime's `flow context --workspace PATH --run RUN`. Supply `--task ID` for a current task and execute the returned next action. Consume every required body and index child until `done: true`; accumulate all tool/process chunks until exit before parsing. A reference alone is not delivery. Missing, stale, foreign, unsafe or truncated inputs refuse.

For phase submission obtain a fresh whole-phase receipt without `--task` after source, scope, revision, task or accepted-input changes. Put the returned `context_receipt` object in the output. Receipts cannot widen scope or approve a phase. Supporting artifacts remain available through verified references; required information cannot be relabeled optional merely to omit it.

Task briefs include the actual request, assigned criteria, relevant findings, write/read boundaries, prerequisite interfaces and immutable coverage mappings. Exact declared `.taskplane` inputs are included. Selectors use versioned UTF-8 byte ranges or JSON pointers, never evaluation or fuzzy selection. Fresh consumers require current delivered bodies even when a cache contains them. Keep native worker instructions task-specific and use `fork_turns: none` on Codex.

## Execution-only startup

New bounded/v3 attempts use `taskplane.startup-account/v3` with `contract: execution_only`. There is no timing provider/mode, elapsed-time threshold, aggregate body-size target, total-response quota or performance exhaustion state. Continue through as many valid framed responses as the required inputs need. Existing per-response native transport, regular-file, no-symlink, parser and output-integrity limits remain enforced; they are not total delivery targets.

Prepare freezes the run/visit/task/grant/attempt, scope and input binding, requirements/task epochs, expected runtime and control epoch. Each native operation reserves an ID and request digest durably before output. Matching actual output digest/byte length and successful automatic post-hook completion resolve that operation. Incomplete or conflicting output remains incomplete. A returned response or a post-hook entry alone cannot prove completed execution.

After genuine claim and complete current context, the declared read-only source probe may run. Its actual result and completed hook establish readiness. First useful worker readiness permits the remaining ready cohort to use observed capacity. No throwaway startup probe or silent root substitute supplies missing native work.

`flow worker --operation startup-status|stop|omit-optional` uses the exact bound typed request. Status/stop work before full context delivery; stop records a fence and retains uncertain in-flight work. Late completion cannot remove it. Only frozen supporting inputs can be omitted. Mandatory changes require a supported reviewed revision. A cancellation request does not prove process exit or release a reservation.

## Reuse and safe recovery

Checks are reusable only with matching source, dependencies, requirements, tests, runtime, environment and exact command fingerprints. Missing dependency coverage disables affected reuse. Cache availability, current delivery, verification and approval remain separate. Preserve failed and unknown results, partial contributions and configured retry safety policy; do not infer tokens from bytes or fill unknown counters with zero.

Immutable indexed delivery binds every page and distinct required role to the current input manifest. Corrupt caches, changed source, stale cursors, changed consumer identity and incomplete manifests refuse. Existing object/record safety bounds remain in force. Full native recovery may read large histories through resumable bounded operations, but validates the complete required history before releasing ownership. It never copies the transcript into a worker prompt.

## Supported contract transitions

`flow context-upgrade --upgrade-mode preview|commit --upgrade-request PATH --upgrade-sha256 SHA256 --run RUN --workspace PATH` upgrades a quiescent unsealed Build from bounded/v2 to bounded/v3. Commit also needs the returned `--preview-digest`. Bind the executing runtime, exact package members, compatibility checks, accepted checkpoints and complete input selection. Installation and hook loading are separate actions.

`flow context-rollback --rollback-mode preview|commit --rollback-request PATH --rollback-sha256 SHA256 --run RUN --workspace PATH` rebinds a quiescent v3 run to an explicitly verified compatible runtime. Commit requires its preview digest. Preserve current history and obligations; advance generation and invalidate old delivery/readiness. An incapable older runtime refuses mutation. Copying an old controller over newer work is not rollback.

Supported selective Product revision freezes the exact request and affected dependency closure, pauses dispatch and requires real quiescence. Opening the affected review preserves unrelated valid contributions and historical decisions while requiring fresh affected Product, Design and Plan acceptance. Stale, conflicting or changed closure evidence refuses; stopped work and approval cannot be inferred from absence of observations.

## Historical compatibility and evidence

Existing startup-account v1/v2 attempts keep their original immutable contracts and failed observations. They are not converted in place or revived. New v3 accounts omit performance policy entirely. Runtime pins prevent an incompatible loaded implementation from mutating an upgraded run. Unsupported host authority is reported, never bypassed.

Former performance findings, thresholds and benchmarks are outside this delivery and are not labeled repaired or passed. Retain five functional review lenses, correct CI execution, source/package identity and actual native/platform evidence. Fixtures verify behavior locally; they do not certify an installed host, process exit, timing source, external monitor or release.
