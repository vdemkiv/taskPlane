# R-0003 design — proof-carrying enforcement, isolation, and cleanup

## Decision

Extend the v2.17.11 kernel with three additive, canonical services:

1. `taskplane/enforcement.py` computes and persists the only
   `live|unproven|advisory` decision for an exact repository, workspace,
   session, run, and revision. Entry, dispatch, gates, status, dashboard,
   artifacts, recommendations, and retro project that same record.
2. `taskplane/collision.py` applies one versioned registry to skill calls,
   agent dispatches, foreign-state signatures, write-authority exclusions,
   and a durable interference ledger. It is a no-op without exact-workspace
   governed state.
3. `taskplane/worktree_cleanup.py` owns a two-phase post-merge protocol: a
   durable orchestrator merge receipt first, then an independently locked and
   immediately revalidated cleanup attempt. It removes only the exact
   registered linked worktree with plain `git worktree remove -- <path>`;
   never a branch, evidence, a symlink, or a tree whose safety is uncertain.

The ReviewKernel normalization, narrow retry, evaluator-unavailable,
shape-safe status, Python compatibility, live-manifest lookup, and exact
claimed-worktree fixes already present in v2.17.11 remain regression
obligations. This design does not replace or weaken them.

## Why this shape

The three new concerns share a trust pattern: derive a small decision from
structural evidence, persist it atomically with identity and revision, then
make adapters render or enforce the record without inventing another truth.
Keeping the decisions in kernel modules avoids divergent Claude, Codex,
Slack, hook, CLI, and dashboard interpretations. Separating the destructive
cleanup boundary from ordinary repository preparation keeps the safe default
obvious and testable.

### Alternatives considered

- **Selected — canonical kernel records plus two-phase cleanup.** Gains one
  authority decision, deterministic cross-host projections, bounded hook
  work, and crash-recoverable cleanup. It adds three small modules, one data
  registry, and fixture matrices. Revisit if hosts provide an authenticated,
  repository-scoped enforcement and plugin-exclusion protocol with equivalent
  receipts and audit semantics.
- **Distributed adapter checks plus immediate removal.** Each entry command,
  hook, dashboard, and merge driver would implement its own liveness/collision
  checks and remove a worktree immediately after `git merge`. This has fewer
  new modules but recreates the contradictions R-0003 forbids and cannot prove
  the merge record survived before deletion. Revisit only if Taskplane becomes
  single-host and single-entry-point.
- **Status quo: warnings, manual plugin discipline, manual cleanup.** This is
  maximally reversible and makes no destructive engine change, but absent
  hooks remain silent, competing drivers remain structurally allowed, and
  merged worktrees accumulate. Revisit as the rollback mode, not as the target
  behavior.

## Current-state anchors

- `host_capabilities.py` already writes bounded hook receipts, but
  `runtime_hook_observations` can accept an old session-bound receipt when the
  current session is unknown. `taskplane_lite.screen_liveness` already derives
  active-contract meter evidence but only emits a warning.
- `tp.py:cmd_screen_dispatch` is opt-in and treats a dispatch with no emitted
  expectation as allowed. Neither hook manifest routes the `Skill` tool.
- `taskplane_lite.build_contract` centralizes `scope_paths`,
  `out_of_scope_paths`, and read-only `write_allow`, so signed foreign roots can
  be excluded once at contract compilation.
- `storage.py` deterministically derives managed task-worktree paths and writes
  worker locators; `run_store.py` provides revision-checked atomic manifests
  and journals. Neither records a task worktree's branch/tip lifecycle or owns
  post-merge cleanup today.
- `loop.py` instructs the orchestrator to run `git merge tp/<task>` and remove
  a worktree after Evaluate passes; it marks the task passed but has no durable
  merge receipt or cleanup transaction. EM already has a regression proving it
  can run after worker trees disappear.
- v2.17.11 tests already cover ReviewKernel summary normalization, substantive
  narrow retry, provenance/conservation, evaluator outage identity, dashboard
  shape safety, live run lookup, and claimed-worktree evaluation. Those tests
  remain floors.

The three supplied documents were used only as field/specification evidence.
Product authority comes from R-0003 and `specs/spec.md`.

## Runtime contracts

### Enforcement decision

`taskplane.enforcement-status/v1` contains the normalized repository and exact
workspace fingerprints, session fingerprint (or explicit unknown), run and
revision, host/mode, status, one stable `evidence_id`, receipt/meter evidence,
reasons, and optional advisory `{actor, acknowledged_at, decision_id}`.

The decision is synchronous and read-only until accepted for a run. The CLI
guard executes before `new`, `loop init`, `review start`, stage emission, or
claim can create state. On a live Claude host, the entry command's own
PreToolUse path has already written a fresh exact-workspace receipt, so the
guard adds zero probes and zero model calls. In strict mode `unproven` refuses
without writing run state. `--advisory --by <actor>` records one attributable
decision and changes the canonical status to `advisory`. Closing gates
recompute from structural evidence; loss of live proof blocks closure until
fresh proof or a recorded advisory acknowledgment.

If the current session identity is unknown, every foreign/session-bound
receipt is freshness-limited to 300 seconds. A known current session requires
an exact session match. Both paths require exact workspace compatibility.

### Delivery isolation and interference

`taskplane.delivery-isolation-registry/v1` is packaged JSON with a version and
content fingerprint. It lists Taskplane namespaces/roles, host built-ins,
non-delivery helper patterns, known competing delivery namespaces, and
versioned foreign-state signatures. The seed conflict is
`orchestrator-supaconductor`; helper seeds cover document, spreadsheet,
presentation, and visualization capabilities.

`taskplane.foreign-interference/v1` records bounded identity, disposition,
registry fingerprint, exact governed run/step/workspace evidence, actor/session
fingerprints when available, and time. Known delivery competitors are denied
even when `TASKPLANE_ENFORCE_DISPATCH` is unset. Unknown foreign invocations
are advised and recorded, or denied under strict isolation. Advisory runs may
record what was observed but never claim an inactive hook denied it.

Onboarding and governed entry perform bounded signature discovery. A directory
name alone never matches. Signed roots are persisted and appended to compiled
`out_of_scope_paths`; an override requires an attributable decision and exact
root. Status reads the durable summary and performs no rediscovery.

### Merge receipt and cleanup record

The orchestrator uses an engine-owned merge boundary rather than free-form
merge prose. After a non-variant task passes Evaluate, the boundary resolves
the primary checkout and recorded primary branch, executes the ordinary merge,
and only after Git reports success atomically commits
`taskplane.task-merge/v1` with repository identity, run/task, exact managed
path, branch ref, pre-merge branch tip, primary ref, resulting primary tip,
and time. A crash before this receipt leaves cleanup ineligible.

`taskplane.worktree-cleanup/v1` is keyed by merge-receipt id and has
`pending|preserved|removed|already-clean|manual-attention` outcomes plus every
checked identity and reason. A locked cleanup reads only the registered
candidate named by the receipt, never scans arbitrary worktrees, and then
re-resolves immediately before removal:

- repository identity and primary checkout;
- exact Git worktree registration and linked-worktree type;
- `lstat` directory identity (no symlink/reparse point) and exact derived
  managed path;
- task/run locator, recorded branch ref, and unchanged recorded branch tip;
- unambiguous primary ref and current local primary tip (no fetch);
- empty porcelain-v2 status including untracked/staged/unmerged bytes;
- no Git/worktree/task lock or merge/rebase/sequencer state;
- inactive, released, passed-and-merge-recorded lifecycle;
- no variant (selected or otherwise), failed state, or evidence-retention flag;
- `git merge-base --is-ancestor <recorded-tip> <current-primary-tip>` success.

Any missing, ambiguous, changed, or failing fact produces `preserved` before
the removal call. Removal uses no force and no branch deletion. Immediately
after success, the engine verifies both path absence and registration absence,
then commits `removed`. If a crash occurs after Git removal but before the
outcome commit, the single maintenance replay recognizes the exact receipt,
absent exact registration, and absent exact directory as `already-clean`.
Conflicting or partial absence becomes `manual-attention` and is never retried
with a stronger primitive.

## Sequencing and ownership

1. Preserve all v2.17.11 ReviewKernel regression fixtures as a pre-change
   floor.
2. Add the enforcement decision and receipt trust fix; wire entry and closing
   gates before status/artifact projections.
3. Add the collision registry, Skill matcher, dispatch check, signed-root
   exclusion, and durable interference projection.
4. Add managed task registration fields and the orchestrator merge receipt.
5. Add cleanup eligibility/removal/replay, then enable automatic cleanup only
   after the merge receipt is durable.
6. Update dashboard, skills, docs, hook manifests, and cross-host parity
   fixtures from the canonical APIs.

`enforcement.py`, `collision.py`, and `worktree_cleanup.py` own domain rules;
`tp.py` and `loop.py` own CLI/orchestration wiring; `run_store.py` owns atomic
persistence; `dashboard.py`, `runtime_eval.py`, agents, skills, and docs are
projections only. No simultaneous deployment across independent services and
no stored-data migration are required.

## Failure, observability, and bounds

All records use existing atomic-write/file-lock conventions. Hook and collision
decisions are synchronous, registry-bounded, network-free, and model-free; the
target is p95 <= 100 ms in local fixture runs. A live entry adds 0 probes.
Receipt freshness is 300 seconds only when exact current-session identity is
unavailable. Cleanup performs one attempt after a durable merge and at most one
idempotent maintenance replay per unchanged outcome fingerprint.

Durable signals are `enforcement_decision`, `enforcement_gate_refused`,
`foreign_interference_event`, `foreign_state_detected`, `merge_recorded`,
`worktree_cleanup_preserved`, `worktree_cleanup_removed`,
`worktree_cleanup_already_clean`, and `worktree_cleanup_manual_attention`.
Strict entry/gate refusals, known collision denials, and manual-attention
cleanup outcomes are actionable dashboard/CLI alerts; preserved cleanup is a
status reason, not a failure alert.

This local CLI has no service availability/throughput SLO. Canonical decision,
merge, and cleanup records have RPO 0 after the command reports success via
fsync-backed atomic commit; cleanup recovery has RTO one maintenance pass.

## Rollout and rollback

Roll out additively: land regression floors; introduce record schemas and
read-only projections; enable strict Claude entry where hook support is
declared; enable known-collision denial with unknowns advisory; then enable
automatic cleanup after merge-matrix tests pass. No external dependency or
data migration is introduced.

Rollback may change Claude enforcement to `warn`, disable collision screening,
and return cleanup to manual mode. It must retain truthful `unproven` or
`advisory` records, interference/merge/cleanup audits, all ReviewKernel floors,
orchestrator-only approvals, and the prohibition on force removal and branch or
evidence deletion.

## Declared debt

The competing-delivery registry is intentionally curated because current hosts
do not expose authenticated per-project plugin exclusion or a universal
delivery-skill taxonomy. Unknown foreign invocations therefore remain advised
by default and denyable in strict isolation. Pay this debt down when hosts ship
repository-scoped plugin controls with signed plugin identity; at that point the
registry becomes a compatibility fallback rather than primary classification.
The downstream debt record, if Product tracks the host dependency separately,
is: `tp req debt "Replace curated collision registry with authenticated host identity" --req R-0003 --reason "Hosts lack per-project plugin exclusion and a signed delivery-skill taxonomy" --follow-up "Adopt the host protocol and retain the registry only as compatibility fallback" --files "taskplane/collision.py,taskplane/collision_registry.json,hooks/**,.codex/**"`.

## Python and packaging constraints

All call paths remain synchronous; there is no async cancellation or
`ExceptionGroup` contract. JSON, CLI/event, environment, Git, and persisted
records are runtime-validated trust boundaries, preferably with `TypedDict`
projections at consumers. Mutable run state remains under existing file locks
and atomic revision checks; no free-threaded safety is inferred from the GIL.

The public namespace remains `taskplane`; no runtime dependency is added. The
collision registry must be included in plugin/wheel contents. Although the
language reference targets Python 3.14, R-0003's compatibility contract is the
binding floor: code and imports must parse and run on supported Python
3.10/3.11/3.12, with clean-wheel install/import, strict type checks at the new
boundaries, failure-injection tests, and dependency-graph verification.

## Visualization

`design/visual.html` is required because three related state machines and the
merge-before-cleanup ordering are easier to review together than as prose.
It is dependency-free, keyboard-readable, and carries the same negative-case
boundaries as this design.
