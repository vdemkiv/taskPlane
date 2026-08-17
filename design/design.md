# Design Contract: Event-driven command completion

Requirement: R-0007  
Status: proposed; requires human Design approval before Plan or implementation

## Context

TaskPlane already owns durable run/loop state (`taskplane/run_store.py`, `taskplane/loop.py`) and bounded Claude/Codex process execution (`taskplane/eval_drivers.py`). However, `run_process` blocks its caller and polls a local `Popen`; other command sites use `subprocess.run`. Terminal outcomes are normalized, but there is no durable workspace-bound handle, transition journal, reconnect operation, wave-completion authority, idempotent delivery lease, or hard wake/token budget. Host session/cell identifiers are transport details, not a canonical identity.

The design preserves synchronous callers, timeout/cancellation semantics, visible approval/input requests, bounded redacted evidence, and the accepted rule that host unavailability is evidence rather than automatically a product defect. Hosts without native completion events must work without model polling.

## Alternatives

### A. Canonical durable command broker (selected)

TaskPlane owns opaque handles, process bindings, an append-only transition journal, artifact-backed output, blocking waits, delivery leases, wave aggregation, and efficiency counters. Claude and Codex adapters translate native capabilities into the same contract; adapters without events block inside runtime code.

Gains: one authority for replay, resume, budgets, redaction, and parity; deterministic tests. Costs: a new persistent lifecycle and compatibility wrapper. Revisit when every supported host offers the complete durable, workspace-bound, replay-safe, aggregating, observable contract—not merely a session identifier.

### B. Host-native handles with a thin facade

Persist Codex/Claude native identifiers and delegate waiting and summaries to each host. Gains: smaller TaskPlane layer and direct native UI integration. Costs: divergent persistence, output, cancellation and aggregation semantics; no-event hosts regress to polling; budgets are outside TaskPlane authority. Revisit when both hosts publish equivalent stable completion/resume APIs and delivery receipts.

### C. In-memory background extension

Move current polling to background threads and retain `ProcessOutcome`. Gains: smallest diff and simple synchronous compatibility. Costs: restart loses identity; completion can be delivered twice or never; waves have no durable authority. Revisit only for commands explicitly classified as short and synchronous.

## Decision and contracts

Select A. Add `taskplane/command_runtime.py` as lifecycle authority and `taskplane/command_adapters.py` as the host boundary. `RunStore` references command/wave snapshots. Existing `NativeAdapter.run` remains synchronous by delegating to launch plus runtime wait; governed long-command flows launch, persist the handle, yield the model turn, and receive one event later.

`contract:runtime:command-state/v1` defines an opaque 128-bit handle bound to workspace and authorization fingerprints; command fingerprint, timestamps/deadline, state revision, adapter binding, artifact references, and optional wave id; states `created`, `running`, `approval_required`, `input_required`, `milestone`, `succeeded`, `failed`, `timed_out`, `cancelled`; structured events with wake reason, exit code, elapsed time, <=16 KiB redacted output delta, artifact reference, and delivery key. `(handle, revision, consumer)` is leased and acknowledged once; replay never launches.

`contract:runtime:host-command-adapter` defines `launch`, `wait_next`, `cancel`, `reconnect`, and `snapshot`. Native events may satisfy `wait_next`; otherwise the adapter blocks runtime-side. Neither path emits running conversation events.

`contract:runtime:command-wave/v1` defines sealed membership, attention/fail-fast policy, and one ordinary aggregate completion delivery. Failure, approval, input, timeout, or cancellation may wake early; ordinary child completions remain suppressed.

`contract:runtime:command-efficiency/v1` records launches, duration, wake reasons, unchanged polls prevented/observed, estimated raw/effective tokens avoided, polling token share, timeouts and cancellations. Gates require zero unchanged polls, >=90% reduction, and <1% raw-token share.

## Persistence, safety, resume, and output

Transitions are appended and fsynced before notification; revision-checked snapshots support fast reads. Raw argv/environment never enter handles or telemetry. Output streams through redaction into one per-command bounded artifact; model events get a hash/reference and <=16 KiB delta. UI progress reads snapshots out of band.

Resume loads the snapshot and reconnects using the adapter's private binding. Terminal state replays idempotently; a live binding reattaches; an unverifiable binding becomes one `failed/binding_lost` event and is never relaunched. Cancellation is revision checked and repeat safe. A user message interrupts only the blocking consumer wait, preserving process and handle.

## Sequence and ownership

1. Runtime owner: schemas, store, fake clock/process, launch/wait/cancel/reconnect, artifacts, leases, synchronous wrapper.
2. Host owner: Codex/Claude adapters with native-event and blocking-fallback paths.
3. Workflow owner: persist-before-yield, resume, validation-wave aggregation.
4. Observability owner: bounded counters and hard budget evaluation.
5. QA owner: one targeted deterministic batch, then exactly one end-to-end long-command validation; no real sleeps or repeated full suite.

## Failure handling

Duplicate events replay the same delivery. Crash around launch reconciles persisted intent/binding without silent retry. Lost binding fails once. Oversized/sensitive output is redacted into one artifact and a bounded event. Revision ordering preserves approval/input races. A timed-out wave member produces attention and at most one later aggregate completion. Missing efficiency totals yield `unproven`, never a manufactured pass.

## Rollout and rollback

Ship additively behind `TASKPLANE_EVENT_COMMANDS`. First dual-record fake/telemetry evidence with synchronous authority; then enable evaluator/validation commands on both hosts; then waves after parity/resume proofs. Rollback disables new async launches, retains the v1 reader until handles expire, lets live processes finish or cancels them through the runtime, and returns new launches to the synchronous wrapper. No destructive migration or simultaneous host deployment is required.

## Validation trace

AC1/2/4/11: fake clock advances five minutes with zero deliveries, then one structured terminal event and no running record. AC3 parameterizes failure/cancel/timeout/approval/input for one delivery each. AC5 streams oversized secret-bearing output and proves one redacted artifact and <=16 KiB event. AC6 interrupts wait and proves same live handle and one launch. AC7 reconstructs from disk and proves reconnect without relaunch. AC8 completes multi-process waves in every order and proves <=1 ordinary aggregate wake. AC9 proves one runtime blocking wait and zero model polls without native events. AC10-12 run exactly one end-to-end scenario and enforce >=90%, zero, and <1%. AC13 races attention against completion. AC14 covers timeout, repeated cancel, crash/replay, lost binding and audit. Claude/Codex golden fixtures normalize shared cases byte-equivalently.

## Known debt

This slice uses the local filesystem journal and local runtime process ownership already present in TaskPlane; it does not migrate live processes across machines. If remote execution enters scope, record a remote-executor lease while preserving `contract:runtime:command-state/v1`.

The state/data-flow visual is included because lifecycle transitions, interrupt/resume, and wave suppression materially clarify the design.
