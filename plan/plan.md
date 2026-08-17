# R-0007 plan — event-driven command completion

This plan realizes the human-approved Design Contract in
`design/contract.json` (artifact SHA-256
`03abbda518095e5866af3ecd22578a1b465383e4582e15a07ec9f53ec33b4a78`) against
the current graph baseline at HEAD
`3c9ffbbe0aebf74fdb350675f796910c75643142`. It preserves the selected
Taskplane-owned durable broker, thin Claude/Codex adapters, additive feature
flag, synchronous compatibility, and non-destructive rollback. The previous
unstaged R-0006 plan extension is preserved verbatim in
`plan/r0006-plan-extension.pre-r0007.md` and
`plan/r0006-tasks-extension.pre-r0007.json`; it is not part of this delivery.

## Bounded impact and design fidelity

The one bounded graph-impact call covered every proposed implementation module
using the approved policy: local depth 3, `contract-only` boundaries, contract
depth 1, and requirement depth 1. It returned 26 impacted nodes, affected
R-0001/R-0005/R-0006, dependent R-0002, and no unknown module IDs. Therefore
every task declares `new_modules: []`. The five tasks collectively cover all
13 designed code/test surfaces, all 16 proposed edges, all four exact contract
IDs, the complete depth policy, and AC1–AC14 verbatim.

## Delivery order

1. **t1 — durable lifecycle first.** Implement opaque workspace/auth-bound
   128-bit handles, revisioned/fsynced transitions, bounded redacted artifacts,
   idempotent delivery leases, interrupt-safe wait, reconnect without relaunch,
   repeat-safe cancellation, wave state, and efficiency counters. Deterministic
   fake-clock/process tests own AC1–AC7 and AC14; they use no real sleeps.
2. **t2 — host adapters.** Put Claude and Codex behind canonical `launch`,
   `wait_next`, `cancel`, `reconnect`, and `snapshot` behavior. Preserve the
   synchronous `NativeAdapter.run` surface and prove that a host without native
   completion events performs one runtime-side blocking wait and zero model
   polls (AC9).
3. **t3 — governed-flow integration.** Persist handles before yielding,
   reconnect on resume/compaction, keep approval/input visible, and aggregate
   ordinary child completion into at most one wave wake. This owns AC8 and
   AC13 after the lifecycle and adapter seams are stable.
4. **t4 — telemetry and hard budgets.** Freeze counters/hashes rather than raw
   argv, environment, or logs; fail closed when totals are unavailable; enforce
   zero unchanged polls and polling raw tokens below 1% (AC11–AC12).
5. **t5 — exactly one end-to-end validation.** After the single deterministic
   repair batch is green, run the long-command scenario once and require at
   least 90% polling-token reduction (AC10). Do not repeat a repository-wide or
   end-to-end loop to chase failures; repair deterministically, then rerun only
   the affected targeted selector unless a new human decision broadens scope.

The dependency chain is intentionally serial because each layer consumes the
previous layer's contract. Implementation scopes are otherwise disjoint, so
the contract prevents workers from opportunistically changing adjacent layers.

## Acceptance and runnable validation map

| Criteria | Owner | Runnable command |
|---|---|---|
| AC1–AC7, AC14 | t1 | `python3 -m pytest -q taskplane/tests/test_command_runtime.py` |
| AC9 | t2 | `python3 -m pytest -q taskplane/tests/test_command_adapters.py` |
| AC8, AC13 | t3 | `python3 -m pytest -q taskplane/tests/test_command_wave.py` |
| AC11–AC12 | t4 | `python3 -m pytest -q taskplane/tests/test_command_efficiency.py` |
| AC10 | t5 | `python3 -m pytest -q taskplane/tests/test_command_completion_e2e.py` |

The deterministic batch must explicitly cover five-minute fake time with zero
intermediate delivery; one terminal delivery and replay; failed, cancelled,
timed-out, approval and input transitions; identical-output suppression;
secret-bearing oversized output with one redacted artifact and a combined
event delta no larger than 16 KiB; user interrupt; crash/restart/reconnect;
cross-workspace/auth binding rejection; every three-member wave completion
ordering; attention/completion races; blocking fallback; timeout, repeated
cancel, lost binding and audit behavior. Golden Claude/Codex events must be
byte-equivalent after normalization.

## Risks and controls

- **Duplicate launch or delivery across a crash.** Persist intent and adapter
  binding before notification, use monotonic revisions and consumer delivery
  leases, and fail `binding_lost` once rather than auto-relaunching.
- **Handle or output leakage.** Keep identifiers opaque and workspace/auth
  bound; store no argv/environment in handles or telemetry; redact before one
  bounded artifact and expose only hash/reference plus a <=16 KiB delta.
- **Host semantic drift.** Canonicalize both adapters and require the no-event
  fallback fixture to prove one runtime wait, zero model polls, and one result.
- **Attention hidden by wave aggregation.** Approval, input, failure, timeout,
  and cancellation always wake; suppress only ordinary child completion.
- **Efficiency claimed without evidence.** Mark missing denominators
  `unproven` and block; require zero unchanged polls, >=90% reduction, and <1%
  raw polling-token share.

## Rollout and rollback

Roll out additively behind `TASKPLANE_EVENT_COMMANDS`: dual-record deterministic
telemetry while synchronous execution remains authoritative, enable evaluator
and validation commands on each host after parity proof, then enable waves.
Rollback disables new asynchronous launches, retains the v1 reader until live
handles finish or are cancelled through the runtime, and routes new launches
through the synchronous wrapper. No state deletion, live-process migration,
or simultaneous host deployment is allowed.

Cross-machine live-process migration remains known debt. If remote execution
enters scope, return to Design for a remote-executor lease rather than widening
this plan.
