# Stateless harness integration and legacy Build continuity

This is implementation evidence for the approved R-0001/T19 repair. It is not
T19 completion, native main-wave acceptance, an Engineering judgment, final
human sign-off, or a release authorization.

The frozen repair was integrated from
`/Users/vdemkiv/.codex/worktrees/2c87/taskPlane`, based on
`c29a3e9daa6f2dd13c29557219ef042157b9e837`, into the newer
`1ad2f703e472fe40af75528fb6e163c56f7c8a7c` source. The full frozen design/RCA,
`design/stateless-harness-repair.md`, was read and verified at SHA-256
`5357997fe841e8f9f811ccf520d744dbf8a575d5c9a5073547e39f9418e6262f`.
The exact saved approval packet was verified at SHA-256
`de3d0bf0f620260bc795a891312afbc360807d14a223226a5cbe59d1c5fdc5bf`.

The integration retains the locator/configuration recovery, fresh invocation
context, native child identity and lifecycle observations, journaled retry,
pending/expired pickup, exact settings restoration, signed collection,
measured usage, and run-owned advisory resource policy from the frozen repair.
No phase edges, automatic lens declarations, evidence requirements, global
plugin settings, or installed package were changed by the legacy extension.

## Publication-only sequencing amendment

The source-only follow-up on `ebae5882d7e7320a9766f1babc656315ebf53051`
adds `loop amend-delivery`. It supports only the explicitly attributed legacy
FP-AC17 publication sequencing decision, not a general acceptance amendment.
J1, the real pre-merge J6 prefix, review, CI, all original journey tests and
remaining tasks stay mandatory. Publication remains pending, requiring a
separate current post-merge authorization and its ordinary artifact, destination,
freshness and signing checks. No existing pass satisfies that pending obligation.

Preparation uses current production owners and is read-only:

```python
from taskplane import loop, loop_recovery

packet = loop_recovery.prepare_publication_amendment(
    loop, workspace,
    by="human:vdemkiv",
    request=approved_request,
    approval={"path":current_approval_path, "sha256":current_approval_sha256},
    original_approval={"path":original_approval_path, "sha256":original_approval_sha256},
    terminal_slot="task_9f12293e",
)
fingerprint = loop_recovery._fingerprint(packet)
```

Run from the committed local source with its package importable. Preserve the
returned packet in a new durable regular JSON file; do not print its historical
payloads or observation authenticators. The closed schema is
`taskplane.publication-amendment/v1`. Preparation supplies exact run, requirement,
task, original baseline/Design/settings, predecessor continuation, current source,
actor/request, both immutable approval artifacts, semantic state fingerprint,
authenticated observation checkpoint, actual Design artifact hashes, signed
retired slot/contract/receipt fingerprints, stale submission/review binding hashes,
and full before/after requirement and Plan text with canonical and byte hashes.
Do not hand-edit generated fields, pre-edit the Plan, or reuse another candidate's
packet. The original approval for R-0001 is
`R0001-stateless-integration-scope.md` (`de3d0bf0f620260bc795a891312afbc360807d14a223226a5cbe59d1c5fdc5bf`);
the publication-only approval is `R0001-publication-post-merge-approval.md`
(`e88fab06458714d2b79d577d24d252b3ca97b872ad4adeb7fc3f1541e4146890`).

Use the same actor, request and canonical fingerprint for check and application:

```text
python3 taskplane/tp.py loop amend-delivery --workspace WORKSPACE --from PACKET --by human:vdemkiv --request REQUEST --fingerprint FINGERPRINT --check
python3 taskplane/tp.py loop amend-delivery --workspace WORKSPACE --from PACKET --by human:vdemkiv --request REQUEST --fingerprint FINGERPRINT
python3 taskplane/tp.py loop next --workspace WORKSPACE
```

The CLI uses the existing root observation authority, without creating one.
Check does not write the requirement, Plan, journal, observations or outbox.
Application refuses active workers and requires the exact signed adverse native
retirement. It journals before requirement/Plan writes. Interrupted application
must resume with the same packet; ordinary continuation refuses an incomplete
journal. Exact completed replay is read-only and renews no dispatch authority.
Validated monotonic observations arriving meanwhile are preserved, not rolled back.

The Plan's existing text is preserved with one bounded JSON annotation append.
Task declarations and all historical result/deferred records remain unchanged.
The requirement owner changes only the exact publication clause and records its
attribution, without renewing Product or Design approval. Original Design bytes
and fingerprint remain pinned, alongside the explicit narrow requirement delta.
Coverage consumers project the exact old FP-AC17 references without rewriting
T11/T14: only the existing remaining J6 owner can satisfy passed ownership of
the amended clause. Ordinary substantive acceptance checks are unchanged.

The stale failed submission and attempt binding are retained verbatim in the
amendment journal and removed only from active use. The run remains Evaluate
with its failed-Build detection unchanged. `loop next` owns a fresh independent
attempt; this repair supplies neither missing acceptance evidence nor a direct
Execute/Fix transition. Original adverse receipts are not rewritten.

Verification: the missing preparation owner was reproduced red after a generated
real Git workspace, requirement mutation, failed submission and signed adverse
retirement. Focused current-reference, Design/preparation and interrupted-owner
checks passed (7 tests in 54.87 seconds). The final declared legacy regression
file passed **130 tests in 225.07 seconds**. Scoped Ruff, whitespace and public
parser checks passed. These are isolated source regressions, not native journey
acceptance; no original run or approval artifact was modified by the worker.

## Native terminal seam repair after the genuine classifier failure

This source-only follow-up is based on `854b0b72c603522727d4c45a3a45c0f847bb7108`
and the seven-path contract SHA-256
`f00c588bf1613856998eac0daf00fc1692ac17da95043a1d333b3d886b854dc8`.
It changes neither the original failed classifier verdict nor its historical
Stop, submission, contract, loop, usage, or gate records.

Three reproducible seams are repaired:

- The normal submission producer emits an absolute repository-owned verdict
  path, while legacy Stop validation previously rejected it. Stop now accepts
  only the exact incumbent stage-owned path after containment checks. The
  fingerprint owner also includes those actual bytes: its old absolute-path
  branch silently skipped repository-local extras. Wrong paths, traversal,
  symlink escape, foreign ownership and changed bytes still refuse.
- The terminal adapter selects and verifies the active lifecycle owner's
  exact Codex child metadata, then verifies the projected counter has the same
  child, root, parent and metadata digest. A generic parent transcript cannot
  select the terminal counter. Generated dual-field events reproduce the old
  selection defect, but the actual historical raw Stop event was not retained;
  its incidence remains unproven. Monotonicity checks, measured cache semantics,
  original counters and unavailable/unknown truth are unchanged.
- A producer contract establishes its observation obligation before fallible
  state loading. Errors fail closed and are reported. After an actual claimed
  Stop records its external observation, the loop owner consumes and attaches
  that exact receipt to a matching pending failed Evaluate submission under
  slot/state locks. It rechecks source, output bytes, attempt, owner, slot and
  submission. Interrupted consumption re-attests the existing durable marker;
  it does not mint or consume another observation. After normal retirement,
  the failure gate can validate only that submission's exact signed native
  terminal and quarantined contract. Missing, foreign, stale or tampered
  evidence remains a refusal. Ordinary pass acceptance is unchanged.

The real-Git composition regression exercises generated independent verdict
bytes through public submit, simulated claimed native Stop, authentic test-store
observation consumption, signed retirement and the public failure gate. Its
environment failure routes to the existing escalation owner while preserving
all 19 completed records. Independent missing/foreign/stale tests and a byte
race verify refusal; the interrupted consumption test verifies exact idempotent
pickup. These are local supporting tests, not actual-host or main-wave acceptance.
The focused composition cluster passed 15 tests in 58.30 seconds; the submission,
child-selection, measured-counter and early-error cluster passed 18 in 14.04
seconds. The declared three-file suite passed **166 tests in 195.94 seconds**:
`test_native_session_continuity.py`, `test_legacy_build_continuity.py`, and
`test_codex_child_identity.py`. Scoped Ruff and whitespace checks passed.

Supported recovery remains orchestrator-owned: preserve the old evidence,
continue the existing exact native classifier if the host and current contract
still admit it, and require fresh candidate-bound classification and ordinary
`loop submit fail` before a genuine native Stop. The old submission cannot be
restamped because its fingerprint omitted verdict bytes. Only after fresh
Stop evidence is validated may the orchestrator call ordinary `loop gate fail`
and follow its existing classification route. A stale contract/candidate or
unavailable native continuation remains a blocker; no synthetic hook replay,
manual receipt insertion, counter reset, contract clear or acceptance waiver is
provided by this repair.

Exact follow-up source SHA-256 (the frozen integration table below is historical):

| File | SHA-256 |
| --- | --- |
| `taskplane/codex_identity.py` | `bce53ad52c54eacc5350c2e85df4cb69ff5ec78d1b71501275bc7fdb3e7878fb` |
| `taskplane/loop.py` | `e27df017d7a3ad6ef99f08cc9b7d8b45cd7a74af20b7a49aaa8c1b3ce0a46fa0` |
| `taskplane/taskplane_lite.py` | `2a45274101cccb72885fe02adfe134061cc8c6dd7a2b63dacefad4806bd660fd` |
| `taskplane/tp.py` | `36bc4b695ec47308361e7d8e550c8659298c76e2ff4a52944f72b734c5d8c03c` |
| `taskplane/tests/test_native_session_continuity.py` | `1f3c1b27d4d0937ca5b96b8d2a7d81006bbf68c9ca1c53ee4100b9504cfcfa5d` |
| `taskplane/tests/test_legacy_build_continuity.py` | `0d81506d6bc5015b6c5baf54d5a8ca55afdd53054f832c811929e661787f3c8c` |

## Earlier night delivery: Evaluate refusal and gate cleanup

The current source-only repair is bound to night-delivery approval SHA-256
`80bd480ea9b1843e7e9be37d714120c5132e992c35212431ea45244f9f02d440` and the
separate bounded capacity decision SHA-256
`b976a39490dabde2388d9503fee99ee55b3b7e13f7f4d9451638ea6d2f0e8d7d`.
The original approval packet and all historical runtime receipts remain unchanged.

The actual T19-scoped tracked diff was 977,211 bytes; the full original-baseline
EM artifact was 1,553,457 bytes across 79 files. Delivery's default 400,000-byte
diff call therefore failed before creating a ReviewKernel identity, and the
catch-and-continue handler hid that cause behind a missing evaluator identity.
The delivery caller now passes the authorized finite 2,000,000-byte artifact
limit. Standalone review retains its 400,000-byte default, scoped views retain
their 16 KiB bound, and no diff is truncated or inlined. Kernel failures now
return their original reason before recording a null identity or preparing
evidence children. Tests cover both measured artifact sizes and 2,000,001-byte
refusal, plus the actual public-next error propagation path.

Serial and parallel gate cleanup now pass the actual gate result to the
existing lifecycle owner (`failure`, `gated:fail` for a failed gate). Previously
they omitted those arguments and inherited `success`, `gated`. Any already
signed terminal receipt remains byte-for-byte unchanged, including an adverse
historical wrong-success receipt. No task result is upgraded by cleanup.

The historical native Stop failure is **not resolved retrospectively**. Its
minimized error identifies `DispatchTelemetryError`, but its detailed reason
was not retained. Read-only counter projection and the present original ledger
validate. Both the current local and installed handlers admit the actual child
snapshot in isolated in-memory copies with the real persistence owner replaced.
A generated disposable-store regression also seals and idempotently replays the
actual pending-ledger shape (zero recorded start/end, no events, unknown usage)
through the current terminal adapter. These checks do not establish what failed
at Stop time, repair historical observations, prove native success, or authorize
a fabricated hook replay. No telemetry counter/source implementation was changed
without a reproducible defect; a fresh authentic terminal is still required.

After this source commit, the root's supported continuation is ordinary local
`loop next` against the same original run, using its existing saved settings and
host admission. No new cancellation, scope amendment, identity fabrication,
Plan edit, or global installation is required for this repair. Any next genuine
evidence refusal remains a gate, not permission to skip validation. Implementation
hashes: loop SHA-256
`ac8650d9e482f55518a4e1e70043050b3637748f31ec861b6ba7bcf3d2de2b45`;
legacy regressions SHA-256
`cd6ced1522c4f8af68588d02e88663d1b7c4784312647ad139a76fd3005748cd`.
The cap, causal-error and gate-failure tests failed first on the original
implementation, then passed after the bounded fix. Declared verification:
`python3 -m pytest -q -x taskplane/tests/test_legacy_build_continuity.py taskplane/tests/test_native_session_continuity.py taskplane/tests/test_r0001_telemetry_seal.py`
completed **137 passed in 109.18 seconds**. Scoped Ruff and whitespace checks
passed. These are isolated source tests, not T19 acceptance or a native Stop
receipt; no broad repository suite or unchanged six-file check was repeated.

### Failed Build classification is not acceptance

After a genuine failed Build, `loop next` uses the existing independent
Evaluate failure-classification route. Its contract explicitly says
`failure-classification-only`, retains the real ReviewKernel attempt and
native producer observation, and binds the current candidate separately from
the gate-owned historical detection. It does not dispatch acceptance-quality
children or invent approved selectors and producer/consumer edges. Ordinary
successful-Build Evaluate still requires those exact inputs unchanged.

Legacy detection retained a workspace fingerprint, not a unique attempt or
the full Build submission. Such input is explicitly marked
`unavailable-in-legacy-detection`; no missing submission is reconstructed.
Future failed gates retain their complete submission in the existing failure
record. The evaluator must disclose historical limits and obtain bounded
current independent evidence before assigning ownership. Detection alone
does not establish a product defect.

The existing gates remain authoritative: PASS and unavailable cannot erase
a detected failed Build, stale/foreign failure inventories refuse, and only
a complete product-only classification opens Fix. Other classes retain their
existing recovery or hold route. The public continuation remains `loop next`,
followed by genuine independent evaluator submission and orchestrator gating;
this source repair performs none of those real-run actions.

## Supported legacy continuation

### Administrative cancellation before continuation

`loop cancel-worker` retires only an exact unavailable/stopped, unbound,
submission-required legacy Build reservation. The human authorizes cancellation;
the current controller separately attests its host observation. A missing hook
does not mean the worker never launched. Cancellation preserves the original
dispatch ledger, unknown usage, completed task results, and source commits. It
does not cancel a "never-launched" intent, flush an outbox, grant a new launch,
or claim native completion. Existing launch/evidence gates remain separate.

Prepare a bounded JSON packet using read-only owners, without copying secrets:

| Field | Value or read-only producer |
| --- | --- |
| `schema` | `taskplane.legacy-worker-cancellation/v1` |
| `run_id`, `task_id` | Original saved run and current pending task |
| `slot`, `expected_worker` | Exact active lifecycle slot and `expected_task_name` (not a prefixed host path) |
| `contract_fingerprint` | `loop_recovery._fingerprint(contract)` of the complete loaded active contract; publish only the hash |
| `before_state_fingerprint` | `loop_recovery.legacy_state_fingerprint(loop._load_raw(workspace))` |
| `candidate`, `source_fingerprint` | `taskplane_lite.git_head(workspace)` and `workspace_fingerprint(workspace)` |
| `plan_sha256` | SHA-256 of current exact `plan/tasks.json` bytes |
| `observation_checkpoint` | `loop_recovery.legacy_observation_checkpoint(saved_state)` |
| `host_attestation` | `{ "status": "unavailable", "session_id": "<current controller session>", "evidence": "<actual exact host observation and historical-launch uncertainty>" }`; `stopped` also accepted |

The session must equal current `TASKPLANE_SESSION_ID`, then `CODEX_THREAD_ID`,
then `CLAUDE_SESSION_ID` in that precedence order. Do not attribute the host
observation to the human. Canonical packet SHA-256 is
`loop_recovery._fingerprint(packet)`. Read the active contract through
`taskplane_lite.active_contract_path(workspace, slot)` and the existing JSON
loader; never print or put its release authenticator into the request packet.

```sh
PYTHONPATH="$PWD:$PWD/taskplane" python3 taskplane/tp.py loop cancel-worker \
  --from <cancellation.json> --by human:vdemkiv \
  --request '<actual human permission to administratively cancel this worker>' \
  --fingerprint <canonical-packet-sha256> --check \
  --workspace /Users/vdemkiv/.codex/worktrees/fa56/taskPlane
```

After a successful check, use the same command without `--check`. The existing
loop journal commits permission before the lifecycle owner signs administrative
`cancellation` with `orphan-recovery` authority and quarantines the exact slot.
Interrupted cleanup replays the exact request, including a receipt persisted
before its active-file update or quarantine completed before journal completion.
Different attribution, a live/bound owner, foreign task/run, active effects,
changed source/Plan/policy, or a replacement slot refuses. Refresh the separate
continuation packet only after cancellation completes: its semantic before-state
must include the cancellation journal. Neither command launches a worker.

This bounded follow-up is authorized by
`.taskplane/human-gates/R0001-stale-worker-continuation.md`, SHA-256
`a897552bee26999f6821959d9ee84f71f6615361afdca7ad808d0be587cb12c7`.
It modifies only the recovery owner, loop/CLI adapters, this document and the
existing legacy regression file; the frozen integration and later J1 additions
remain intact. This follow-up reconciles the loop/CLI hashes in the source
table below; its added recovery owner is SHA-256
`85566e99a57b618fe18e745c976203122c837d39b9f565269c71ea0e8a29ab46`
and legacy regression file is SHA-256
`dc10078734955c1e784049860eba2e46d532511b1bdd29518286686ca5133466`.
Validation: the missing-API regression failed first; 19 initial cancellation
selectors passed. The CLI apply probe then exposed a dashboard-loader outbox
side effect; publication is now deferred while cancellation cleanup is pending.
The corrected 13 retry/journal/CLI selectors passed, followed by the complete
legacy file: **70 passed in 44.58 seconds**. Scoped Ruff and whitespace checks
passed. No five-file aggregate was repeated and no real-run action was taken.

### Scope amendment

The compatibility command is separate from `loop replan`. Replan's existing
independent-pass/reanchor authority checks are unchanged. Legacy continuation
preserves exact historical non-judged Build results instead of trying to turn
them into independent passes.

Prepare a reviewed amendment packet and the exact amended `plan/tasks.json`
with only the pending current task's scope list appended. Keep every other
Plan field, task order, test command, acceptance criterion, dependency and
completed declaration unchanged. Preparing a file grants no execution
authority. Until continuation commits, fresh legacy effects refuse rather
than silently adopting changed Plan bytes.

Run the read-only check against those exact prepared files:

```sh
python3 taskplane/tp.py loop continue-build \
  --from <amendment.json> \
  --by human:vdemkiv \
  --request '<explicit approved scope append and advisory-resource instruction>' \
  --fingerprint <canonical-amendment-sha256> \
  --check --workspace /Users/vdemkiv/.codex/worktrees/fa56/taskPlane
```

After a successful check, invoke the same command without `--check`.
The command commits one atomic loop-state change. It does not write Plan
files, clear workers, submit T19, dispatch, approve an Engineering gate, or
start another run. It refuses an existing pending Build worker contract.
Ordinary `loop resume` and guarded `loop next` remain the subsequent pickup
surfaces; their original host/evidence admission requirements still apply.

### Amendment packet: taskplane.legacy-build-amendment/v1

The JSON object is closed and contains these fields:

| Field | Exact value or producer |
| --- | --- |
| `schema` | `taskplane.legacy-build-amendment/v1` |
| `run_id`, `requirement_id`, `task_id` | Existing legacy run, its requirement and its pending current task |
| `baseline`, `design_fingerprint`, `settings_digest` | Unchanged saved authority values |
| `before_state_fingerprint` | `loop_recovery.legacy_state_fingerprint(saved_state)` |
| `candidate` | Current committed `taskplane_lite.git_head(workspace)` |
| `source_fingerprint` | `taskplane_lite.workspace_fingerprint(workspace)` after preparing the exact Plan |
| `before_plan` | Complete original approved Plan object, matching the saved delivery receipt |
| `after_plan_sha256` | SHA-256 of the exact prepared `plan/tasks.json` bytes |
| `settings_snapshot` | Complete original normalized settings, matching the original digest |
| `resource_limits` | `advisory` |
| `observation_checkpoint` | `loop_recovery.legacy_observation_checkpoint(saved_state)` |
| `requirement_fingerprint` | SHA-256 of the current requirement's semantic fields: id, title, functional, nfr, acceptance, open_questions, contracts, depends_on, context_files, review_policy; include absent fields as null |

Packet/semantic hashes use sorted-key compact UTF-8 JSON, `ensure_ascii=False`
and `allow_nan=False`. Settings retain their original settings-owner digest
semantics. Do not copy native authenticators, private keys, host receipts,
or another run's authority into the packet. The observation checkpoint
contains public counters and identity fingerprints only.

For this original run, the preserved settings file is
`.taskplane/continuity-plan-sources/r0001-original-operational-settings.json`,
digest `e4e9a573bf0006479b82444f9cc9edb46f4dba64c695ca860e86b07c90061385`.
It predates `phase_definitions`. Restoration validates its complete original
shape without filling it from current defaults or selecting a stage runtime.

The original requirement-first contract expansion is reused from the Plan
gate. A shorter task declaration and its exact saved expanded representation
are both retained; arbitrary additional runtime contracts are refused. Their
complete saved fingerprints and the requirement content are bound to the
continuation receipt.

### Interruption, measurements and approval

An interruption after preparing Plan but before committing state leaves the
original task state intact. Replaying the same packet completes the one
atomic transition. An interruption after commit returns the original receipt
without writing, renewing authority or repeating effects. A different packet,
actor or request cannot reuse that receipt.

Only authenticated root-meter progression and the corresponding ledger
revision may advance independently of the semantic workflow fingerprint.
Root identity, policy, settings, worker bindings and all other workflow
fields remain bound. The command verifies the original observation authority
without creating a new key, re-reads under the existing state lock, and keeps
the newest meter bytes. Unknown usage remains unknown. Original numeric
limits stay in the saved settings and ledger; the original loop record owns
the separately attributed advisory decision.

Context rent is the native meter's cumulative cached input divided by turns;
it is an average, not a cumulative counter. Its authenticated value is retained
even when the average falls. Monotonicity still applies to sequence, ledger
revision, turns, peak context and every cumulative usage counter.

The receipt explicitly names the new human scope approval, old/new Plan
fingerprints, source candidate, settings, requirement, retained Build
results and before/after semantic state fingerprints. The old Plan's approval
is preserved as history, not silently stamped onto a different Plan.
Revocation or scope/identity/policy/evidence drift remains a refusal.

Subsequent successful Build gates may retain the original deferred-review
timing after the exact passing tests are verified. Such results remain
`non-judged`, with no invented target commit or independent reanchor receipt.
Engineering receives the whole original review baseline and all deferred
work. Current zero-lens phase declarations and substantive Engineering
validation remain unchanged; final human sign-off is still required.

## Verification and native evidence boundary

The legacy regression first failed because the continuation API did not
exist. A second regression reproduced the missing deferred-Build boundary.
The regression set covers the generated 19-of-23 saved shape, 17 deferred
records, exact phase-less configuration recovery, public CLI check/apply,
changed scope/tests/Plan, forged passes/contracts, stale/foreign policy and
requirement, revocation, interruption, replay, concurrent scope changes,
authenticated meter progression and negative meter identity/usage cases.

The separately required repair DoD is:

```sh
python3 -m pytest -q -x taskplane/tests/test_legacy_build_continuity.py taskplane/tests/test_run_context.py taskplane/tests/test_phase_retry.py taskplane/tests/test_native_session_continuity.py taskplane/tests/test_r0001_j1_native.py
```

Final declared regression result: **155 passed in 343.83 seconds**. The
separate settings/flow/telemetry-signature/native-identity cluster passed
78 tests; existing replan/reanchor checks passed 8 tests and 15 subtests.
Scoped Ruff, strict run_context type checking, generated CLI comparison and
source provenance checks passed. A final CLI workspace-option correction
was checked with its exact parser invocation and two focused passing tests.

A bounded follow-up reproduced the first real continuation check's refusal:
between turns 50 and 52, context rent fell from 191262.72 to 187392.0 while
all cumulative usage increased and peak context stayed 862271. A generated
authenticated regression reproduces these exact counters through the native
meter owner. It failed before removing the average's inappropriate monotonic
constraint, then passed. The complete legacy suite passed **41 tests in
17.69 seconds**; scoped Ruff and whitespace checks passed. No aggregate suite
was repeated for this change. Only `loop_recovery.py`, its paired legacy test
and this document changed; all 31 frozen integration hashes below remain
unchanged. The real Plan, amendment packet and loop state were not edited by
the follow-up worker.

Those are supporting local regressions. They do not replace the original
T19 journey selectors or actual main-wave native evidence.

The frozen record documents actual collected diagnostic Product operation
`phase-attempt-2c6e9c0d6db7864d0641fee3ca57ecef` in diagnostic run
`e2a2776aab7f4d5b8ea03ebe5e143cf7`, runtime-result fingerprint
`f581cbfd09417ff491d1ded197a93987a71b1f989cda93e2e357b74ab04a0d64`,
and 334,755 measured tokens. These identify that candidate/operation only.
The later source fixes were newer than diagnostic package 10. This
integration copies no diagnostic requirement, approval, locator, authority,
execution history or acceptance into the original run.

The frozen record also reports baseline release/version, eager-import and
strict type-check failures. This integration does not reclassify them as
passing or repair unrelated release behavior.

## File-level frozen integration provenance

Twenty-five files match their frozen SHA-256 exactly. Six are reconciled:
the CLI reference adds the supported command; loop, run_context, settings
and CLI add the bounded legacy compatibility; J1 combines the frozen fix
with all newer onboarding coverage from HEAD. The plugin metadata and
`release_evidence.py` from the newer HEAD are preserved.

Additional paired compatibility files are `taskplane/loop_recovery.py`,
`taskplane/tests/test_legacy_build_continuity.py`, and this document.

| File | Frozen SHA-256 | Integrated SHA-256 | Reconciliation |
| --- | --- | --- | --- |
| `agents/spec-phase-definitions.json` | `5740cbc1ac112570bc42a9e34f52a9dde12abc759fa8930231d4985c8a5e3bee` | `5740cbc1ac112570bc42a9e34f52a9dde12abc759fa8930231d4985c8a5e3bee` | exact |
| `docs/cli-reference.md` | `b9102c64c9c3ebfbb8ec17a25b7d2209341d0d36ae2306c14db9dbb7729f3792` | `e19577366aa90833820b64f6e7b3b033b3c3e65885ec7bb62ac3848bfdff7318` | reconciled |
| `skills/taskplane/SKILL.md` | `6c3860e7b3a6b8beeb5ee4b41a14c825ff923135c3df5dd8eee3c9cf184359e7` | `6c3860e7b3a6b8beeb5ee4b41a14c825ff923135c3df5dd8eee3c9cf184359e7` | exact |
| `skills/tp-go/SKILL.md` | `e634bb60a7f05e683906c7d7b3a78e6577f3829f4d86abeaa9fc91f0ed0c5810` | `e634bb60a7f05e683906c7d7b3a78e6577f3829f4d86abeaa9fc91f0ed0c5810` | exact |
| `taskplane/agent_runtime.py` | `591562f444fe4ce360ce47041aada5d9081dc21e90479fbe37d1895c8c6590cb` | `591562f444fe4ce360ce47041aada5d9081dc21e90479fbe37d1895c8c6590cb` | exact |
| `taskplane/design_host_transport.py` | `0f0a002cd0d4e984bcebc3a56f33e35f6287ff26562ec99b2cba51083ad6ef16` | `0f0a002cd0d4e984bcebc3a56f33e35f6287ff26562ec99b2cba51083ad6ef16` | exact |
| `taskplane/dispatch_telemetry.py` | `574c56f068a41cfe259000e3670843339ebf0f68235764044ec48dc6240be61f` | `574c56f068a41cfe259000e3670843339ebf0f68235764044ec48dc6240be61f` | exact |
| `taskplane/host_capabilities.py` | `3222731240e9f209a307f97cee51209c3137344a9f7236c839894c4ce14f25b7` | `3222731240e9f209a307f97cee51209c3137344a9f7236c839894c4ce14f25b7` | exact |
| `taskplane/loop.py` | `2026ec0a48d2c0173308fea872fab198215c875a1b26bc3cb3086a77fa277253` | `9f07b11de387a4e22b5b445ae6f09c3217bb27b49f2bd28ebea9f1af412afef2` | reconciled |
| `taskplane/operational-settings.json` | `5d8f8227a8f320168ea511b4f5b6480bc77fd0b7e45bae081f99fb5b09ca9293` | `5d8f8227a8f320168ea511b4f5b6480bc77fd0b7e45bae081f99fb5b09ca9293` | exact |
| `taskplane/producer_observation.py` | `cf1709640d4541bf5f84d56fafc5244fd08b035726fe1c3abd0d98bea1b2d660` | `cf1709640d4541bf5f84d56fafc5244fd08b035726fe1c3abd0d98bea1b2d660` | exact |
| `taskplane/settings.py` | `88547c6e0e1c149ea5fd723e2d6f7c0c0426de5e5ded1a484bcfd0977a771418` | `369dfbaf79ca2c20abdfffaf4399646716a524c2886763588e6b5b25ef9b0f68` | reconciled |
| `taskplane/stage_migration.py` | `5303defb3394da8fae688b9d0f64e21adb3c502ee0fb68ad772779a121b49de3` | `5303defb3394da8fae688b9d0f64e21adb3c502ee0fb68ad772779a121b49de3` | exact |
| `taskplane/storage.py` | `a7a3dea8ab7110d49acb6ba82d898276b46a0dc7af06c1bc4247cbbe8994315d` | `a7a3dea8ab7110d49acb6ba82d898276b46a0dc7af06c1bc4247cbbe8994315d` | exact |
| `taskplane/taskplane_lite.py` | `e83eefad1e865c01508e04a5db1438bdecc15c1b1ba18c55866221cb359ff075` | `e83eefad1e865c01508e04a5db1438bdecc15c1b1ba18c55866221cb359ff075` | exact |
| `taskplane/tests/test_host_capabilities.py` | `9a1a4409c41644e870248177037a56e812b2648a01fd9820b83b9b4134230b53` | `9a1a4409c41644e870248177037a56e812b2648a01fd9820b83b9b4134230b53` | exact |
| `taskplane/tests/test_r0001_agent_runtime.py` | `8856cbbcd847f52ae39c396bf810f73029e7c8b539aa4b54e767c14a0ef21b3c` | `8856cbbcd847f52ae39c396bf810f73029e7c8b539aa4b54e767c14a0ef21b3c` | exact |
| `taskplane/tests/test_r0001_j1_native.py` | `ba2b8bdf6fdbd4f93f30857aa12d5aad55e01f9c3f0c084ace57dc033df168fe` | `bf51409b40a87e140a9f4c0a379f09a95434f601cd7374a92fc61b320461818b` | reconciled |
| `taskplane/tests/test_r0001_phase_cutover.py` | `e64356f1dc3563bc0c3e4273823a9e3cccd6ed118d8ef314665a5902e0399cb0` | `e64356f1dc3563bc0c3e4273823a9e3cccd6ed118d8ef314665a5902e0399cb0` | exact |
| `taskplane/tests/test_r0001_telemetry_seal.py` | `458d69cbb8b3b3b5a0d1f8307bf0568bbd6ceaac02db2886c017305349925b40` | `458d69cbb8b3b3b5a0d1f8307bf0568bbd6ceaac02db2886c017305349925b40` | exact |
| `taskplane/tests/test_r0013_bootstrap_home.py` | `06e738c88fb4467d7fdc6fab2ca2fee7eac23a78ab4319255dc1c2c6306def80` | `06e738c88fb4467d7fdc6fab2ca2fee7eac23a78ab4319255dc1c2c6306def80` | exact |
| `taskplane/tests/test_r0013_native_budget.py` | `0e0e61d7bbc652a67eca8986de7ec12a11b36a2eae4fc5d5c32d35c006f662d1` | `0e0e61d7bbc652a67eca8986de7ec12a11b36a2eae4fc5d5c32d35c006f662d1` | exact |
| `taskplane/tests/test_stage_loop_integration.py` | `e842adfe7006b44fd9e70bae596bfe4bb4e0f081f3d97b4beb3712507433ca68` | `e842adfe7006b44fd9e70bae596bfe4bb4e0f081f3d97b4beb3712507433ca68` | exact |
| `taskplane/tp.py` | `6714d1c9f7b829a977fcde7bc0f629cba712668c7d542c9f9be8679fffa0adaf` | `8ebd5d6c0a60a6d67755339188e21b7d5de9b0e0540bd22d77fae86f34278b93` | reconciled |
| `taskplane/codex_identity.py` | `e9d53de94188c4e24be8f7cc3aa69f56a6139f9f75a3f4ee2d4fa24a9a84b62c` | `e9d53de94188c4e24be8f7cc3aa69f56a6139f9f75a3f4ee2d4fa24a9a84b62c` | exact |
| `taskplane/phase_harness.py` | `da85cf4e163d9ec6d98fb107f43ecf3a771e1c31c3ea5d5325159dfab5d8a051` | `da85cf4e163d9ec6d98fb107f43ecf3a771e1c31c3ea5d5325159dfab5d8a051` | exact |
| `taskplane/run_context.py` | `64d06821fabe85a7db37ae39e8d53f61248fc518dba5e67d188127f7cc6979de` | `a8f9bb35941b2f249eca821266ac1d900d6e1df5f3217191dc4f1b0f8c520690` | reconciled |
| `taskplane/tests/test_codex_child_identity.py` | `534975d65311446bb8102679e596ec2f0eb79ac9bca714274b97689b9789a768` | `534975d65311446bb8102679e596ec2f0eb79ac9bca714274b97689b9789a768` | exact |
| `taskplane/tests/test_native_session_continuity.py` | `34e4070c140f019b5df0c914b0d1af760c13290528af5b254cda32b2bd146ecc` | `34e4070c140f019b5df0c914b0d1af760c13290528af5b254cda32b2bd146ecc` | exact |
| `taskplane/tests/test_phase_retry.py` | `4f09eac4d527b80c58fa391c6c983c005243287c1b6822341cfcc51c31f5d503` | `4f09eac4d527b80c58fa391c6c983c005243287c1b6822341cfcc51c31f5d503` | exact |
| `taskplane/tests/test_run_context.py` | `c34506609b3259447ad75ba23f36d3f14a58c53c60597f54c14377f2e79e460f` | `c34506609b3259447ad75ba23f36d3f14a58c53c60597f54c14377f2e79e460f` | exact |
