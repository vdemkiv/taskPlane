# Plan — amended AC3 checkpoint execution for stateless `tp pickup`

## Authority and completed evidence

This minimum replan remains anchored to R-0001 and the approved Design fingerprint `f3ccf610cb8b6cc17c8ce68a087f5edf691693e42ddc3c3441d354026952970d`. The approved graph baseline remains `e0d54a84434269c488941a265f865803c90c7e8adaa0796159ea60a3257adc8b`; the human amendment changes no Design module, contract, edge, or depth policy.

Completed work is audit history, not executable inventory. AC1 and AC2 retain their accepted exact-commit evidence at `70f311ad75de33a530a6ba43ac213883a1e95c3f` and `5cc647cabd8bd8528b3044184e38d3317f593f27`. The released-tip prerequisite T03 is passed and audited at `5c28165f800fffcac20aa2004d9a2b38efb195cf`. T01 through T03 are deliberately absent from `plan/tasks.json`; none may be replayed.

T04 failed with a production defect in `taskplane/checkpoint.py::run_and_mint_stateless`: dereferencing `sys.executable` through `os.path.realpath` escaped the active authoritative virtual environment to `/usr/local/opt/python@3.12/bin/python3.12`, where pytest was unavailable. Preserving the active virtual-environment executable path runs pytest 8 and produces the required revision attestation. The human approved only the smallest repair: `taskplane/checkpoint.py` plus its direct regression in `taskplane/tests/test_pickup.py`.

The one required bounded impact query covered exactly those two paths at `5c28165f800fffcac20aa2004d9a2b38efb195cf`. It returned 27 impacted modules, zero unknown modules, no truncation or degraded scan, affected requirement `req:R-0001`, graph fingerprint `0e269f67a62c3caab0a41b9377bdb920f7a6be72cd808640416be8b474a0d942`, and scan-quality fingerprint `409cbc082b9fcfbefd746c3622d3ae8b2c36f315bf1e928150ce5dec4a665ee8`. Every remaining task keeps the approved local-depth 3, contract-only, contract-depth 1, requirement-depth 1 policy.

## Remaining serial delivery

The executable graph is `T04 → T05 → T06 → T07`. Each functional checkpoint owns one acceptance criterion. Production behavior and its focused proof land in the same implementation commit. A failed or incomplete checkpoint cannot unlock the next task.

### T04 — AC3: interpreter-bound stateless checkpoint and live BUILD-C seam

T04 is now the root task. Its only writable source/test paths are:

- `taskplane/checkpoint.py`
- `taskplane/tests/test_pickup.py`

Repair `run_and_mint_stateless` so it preserves the active engine's absolute virtual-environment interpreter path without dereferencing the symlink to a base interpreter. The interpreter identity, argv, environment, pytest result, revision attestation, and checkpoint receipt remain bound to engine evidence. The caller's `PATH` must not select or substitute another interpreter.

The same commit adds `test_stateless_checkpoint_preserves_active_virtualenv_interpreter_symlink_and_attests_revision`. It must exercise the actual pytest/revision-attestation path, not merely inspect a fixture or mock away execution. T04 then runs that focused regression, the severed pickup-to-BUILD-C proof, the full pickup suite, and the unchanged legacy loop/governed-command selectors using portable `python -m pytest`.

The protected-diff check freezes these surfaces against `381ee41c34a10ae7a6eb029b4f4851db5ba9c8b9`:

- `taskplane/tp.py`
- `taskplane/pickup.py`
- `taskplane/build_c.py`
- `taskplane/design_contract.py`
- `taskplane/repository.py`
- `taskplane/storage.py`
- `taskplane/taskplane_lite.py`
- `hooks/**`
- `.taskplane/codex-hook.py`

No timeout increase, skip, xfail, assertion weakening, PATH-selected interpreter, fixture-only workaround, alternate checkpoint authority, or receipt weakening can satisfy T04. AC4 and later work remain locked until the exact AC3 command is green.

### T05 — AC4: repository-only receipt resume

After T04 passes, prove that a second checkout resumes from Git-tracked Design authority and `exports/` receipts without access to the first private Taskplane home. The receipt chain remains keyed by exact authorized source SHA plus Design fingerprint, append-only, tamper-evident, contiguous, and collision/fork/gap resistant. Interrupted or red checkpoint evidence preserves prior receipts but authorizes neither the next criterion nor merge.

### T06 — AC5: final functional suite

After AC4 passes, run `python -m pytest taskplane/tests -q` on the exact clean functional SHA and compare with the exact baseline. Any new failure blocks release metadata. This verification checkpoint grants no broad repair authority; a defect returns to its owning bounded scope.

### T07 — 2.17.20 release surfaces

Only after functional AC1 through AC5 are green, update README, CHANGELOG, `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, and `.claude-plugin/marketplace.json` consistently to 2.17.20. Revalidate all three JSON manifests and rerun the full portable suite on the final clean candidate SHA. This task performs no push, tag, publication, marketplace release, package release, or `origin/main` mutation.

## Design and contract coverage

The remaining tasks preserve all fourteen approved proposed-module identities, all sixteen proposed edges, the five acceptance-map criteria through accepted history plus current executable ownership, and all six canonical boundaries:

- `contract:pickup.stateless-front-door`
- `contract:design.approved-contract`
- `contract:build-c.direct-assignment`
- `contract:build-c.acceptance-checkpoint`
- `contract:repository.merge-on-green`
- `resource:exports.pickup-receipts`

T04 changes only the incumbent checkpoint owner and its direct test. The existing pickup, BUILD-C, Design authority, repository, private-state witnesses, legacy loop, and hook surfaces remain consumption or protected-verification boundaries rather than redesign scope. There is no new module, second checkpoint engine, alternate interpreter authority, private receipt store, or expanded graph edge.

## Risks, exclusions, and final stop

- Interpreter escape: preserve the authoritative active virtual-environment path and bind its absolute identity, argv, and environment into checkpoint evidence; never resolve through caller PATH or dereference to a base interpreter that lacks the governed runtime.
- False-green fixture: the regression must execute real pytest and produce revision attestation.
- Security drift: protected pickup and hook bytes remain frozen, and the severed-edge proof must still fail when pickup no longer reaches BUILD-C.
- Premature progression: T04 gates AC4; AC4 gates AC5; functional green gates 2.17.20 metadata.
- Scope drift: Phase 0, E0, R-0011, every other R-0013 item, hook changes, unrelated legacy-loop work, timeout changes, broad redesign, and external release mutations remain excluded.

After T07 is green on one exact clean SHA, run exactly two quick reviews concurrently against that SHA and evidence set: security and QA. After both finish, run the engineering-manager review against the identical SHA. Report the 2.17.20 candidate and stop for explicit human push authority.
