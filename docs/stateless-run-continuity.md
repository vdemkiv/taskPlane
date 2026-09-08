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

## Supported legacy continuation

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
| `taskplane/loop.py` | `2026ec0a48d2c0173308fea872fab198215c875a1b26bc3cb3086a77fa277253` | `5b5ed610cc7a4f94ba634e3b2514a4ecc0c3b925d7441f5d2147730862f98845` | reconciled |
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
| `taskplane/tp.py` | `6714d1c9f7b829a977fcde7bc0f629cba712668c7d542c9f9be8679fffa0adaf` | `650b6b105cb635b957b7fa51e620686bb5e22aad4a3e49acbac6032507bb6163` | reconciled |
| `taskplane/codex_identity.py` | `e9d53de94188c4e24be8f7cc3aa69f56a6139f9f75a3f4ee2d4fa24a9a84b62c` | `e9d53de94188c4e24be8f7cc3aa69f56a6139f9f75a3f4ee2d4fa24a9a84b62c` | exact |
| `taskplane/phase_harness.py` | `da85cf4e163d9ec6d98fb107f43ecf3a771e1c31c3ea5d5325159dfab5d8a051` | `da85cf4e163d9ec6d98fb107f43ecf3a771e1c31c3ea5d5325159dfab5d8a051` | exact |
| `taskplane/run_context.py` | `64d06821fabe85a7db37ae39e8d53f61248fc518dba5e67d188127f7cc6979de` | `a8f9bb35941b2f249eca821266ac1d900d6e1df5f3217191dc4f1b0f8c520690` | reconciled |
| `taskplane/tests/test_codex_child_identity.py` | `534975d65311446bb8102679e596ec2f0eb79ac9bca714274b97689b9789a768` | `534975d65311446bb8102679e596ec2f0eb79ac9bca714274b97689b9789a768` | exact |
| `taskplane/tests/test_native_session_continuity.py` | `34e4070c140f019b5df0c914b0d1af760c13290528af5b254cda32b2bd146ecc` | `34e4070c140f019b5df0c914b0d1af760c13290528af5b254cda32b2bd146ecc` | exact |
| `taskplane/tests/test_phase_retry.py` | `4f09eac4d527b80c58fa391c6c983c005243287c1b6822341cfcc51c31f5d503` | `4f09eac4d527b80c58fa391c6c983c005243287c1b6822341cfcc51c31f5d503` | exact |
| `taskplane/tests/test_run_context.py` | `c34506609b3259447ad75ba23f36d3f14a58c53c60597f54c14377f2e79e460f` | `c34506609b3259447ad75ba23f36d3f14a58c53c60597f54c14377f2e79e460f` | exact |
