# Plan — bounded R-0002 acceptance proof after inherited-red baseline decision

## Authority and disposition

This replacement Plan is bound to governing `specs/spec.md` SHA-256 `0ae3d5c0071e06b76cc67cd7216c6b9cc68231d18354634a8a6d8fa18984eda4`, approved Design content fingerprint `2cb1e7c7bd38e0f6ea3566fe0f454a757d60d488527c57ebb4026b6709ec34d9`, and engine-approved Design fingerprint `cc7060089b88e213736d2a584e30abad72e621f6a68b47399cc5dc13f62f76ea`. The approved Design graph baseline remains `c6e3f9ed00e00c75fe07d3a4734633ea19d36d81b7a7ef4ce39d289025e26199` (55 modules / 177 edges).

Human decision 0019 is the authoritative replan disposition: accept the isolated pickup wave because every observed T07 failure reproduces on the released main/CI baseline, replace the broad AC5 rerun with a bounded no-pickup-regression proof, preserve T05 and T06 as accepted non-replay history, and defer inherited red-CI remediation to a separate post-2.17.20 delivery. This does not waive an unknown pickup failure; none was observed. It classifies the 42 observed failures as inherited rather than granting repair or scope authority.

Completed inventory receives no executable replay:

- T01 / AC1: `70f311ad75de33a530a6ba43ac213883a1e95c3f`
- T02 / AC2: `5cc647cabd8bd8528b3044184e38d3317f593f27`
- T03 released-tip prerequisite: `5c28165f800fffcac20aa2004d9a2b38efb195cf`
- T04 / AC3: `0c19087e3eb28d70869f2752f12c2d3742f33810`
- repository-only resume groundwork: `3ee3a17a695bf059f90504507a8eb5fe690fb52d`
- retry-safe atomic publication: `a73f125e762670323d0e4a8fbbef3a1edf3ea958`
- T05 / AC4 implementation: `4c5e40aa18a5d7ca3709ea8cbf274f7c6c2132e9`
- corrected T05 governance authority: `2a4adb5b8e8dd8228a7ee95f7168fee955cb2de3`
- T06 / bounded 2.17.20 release surfaces: `f15588137a0b2470494318836798820200d63a56`

The append-only replan history records T05 and T06 as passed with attributed human resolutions and reanchor authority. T05 retained 37 focused tests and 54 full pickup tests green, plus successful `py_compile`, diff hygiene, protected-surface comparison against `a73f125e762670323d0e4a8fbbef3a1edf3ea958`, and manual escape review. T06 retained a green bounded execute gate, consistent 2.17.20 release metadata, and complete graph/contract dispositions. Neither task may be replayed.

## Exact inherited-red comparison

The prior T07 branch run at exact candidate `f15588137a0b2470494318836798820200d63a56` stopped at 42% with 42 failed, 1781 passed, and 1 skipped. All 42 selected failing nodeids reproduce at released main/CI SHA `726acd108d3ca431e680183de129918842202eda` across six isolated clean-clone checks. Every observed failure identity belongs to legacy governance, routing, evidence, graph, golden, or loop surfaces; no `taskplane/tests/test_pickup.py` failure was observed.

GitHub CI #106 is already red at the same released SHA: <https://github.com/vdemkiv/taskPlane/actions/runs/32774798956>. Its public logs hide individual nodeids, but the job-surface mapping accounts for 42 of 42 observed failures. The human decision therefore accepts zero new pickup failures while deferring the inherited red-CI repair as a separate post-2.17.20 delivery. This Plan neither claims the inherited suite is green nor authorizes changing it.

## Bounded graph result

The single required Taskplane 2.17.19 impact call covered only `exports/**` and returned:

- graph fingerprint `6125ffb3e500f9efadb5b33ca6fbee845d865f0b3f32409ae182b913be674e8f`;
- scan-quality fingerprint `bf7190f11c14ab8b0f4b73bd8016994cae1dfec46b4d00de57eb8401916dfe3c`;
- scanned revision `f15588137a0b2470494318836798820200d63a56`;
- 2 impacted requirements (`R-0001`, `R-0002`), zero unknown modules, and no degraded producer; and
- the approved depth policy: local 3, contract-only boundary, contract 1, requirement 1, with only the expected requirement-depth stop.

No new module, dependency, product edge, or write scope is introduced.

## Sole remaining executable task

`T07 bounded R-0002 acceptance proof`

The task has `deps: []` because T05 and T06 are accepted archived evidence, not executable dependencies. Its only write allowance is `exports/**`, and no repository change is required. If the engine requires a repository evidence artifact, only an explicitly authorized receipt under that scope may be persisted.

Its one shell-control-free pinned-Python command must, in order:

1. require a clean checkout, capture the current exact HEAD, and print it as `exact_head=<sha>`;
2. require `git merge-base --is-ancestor f15588137a0b2470494318836798820200d63a56 HEAD`;
3. require the complete changed-file set from `f15588137a0b2470494318836798820200d63a56` through current HEAD to be a subset of only `plan/plan.md` and `plan/tasks.json`; this freezes every product, test, README/CHANGELOG/manifest, hook, graph-config, CI, deploy, spec, Design, and backlog surface while permitting the required governance-only Plan commit;
4. run the complete `taskplane/tests/test_pickup.py` suite;
5. parse all three plugin JSON documents and require every shipped version field to be `2.17.20`;
6. verify the pinned CLI version plus canonical pickup `--workspace` and `--trust-source` help; and
7. verify README and CHANGELOG contain 2.17.20 and truthful attributed-operator-trust/no-cryptographic-authentication wording.

The command does not run `python -m pytest taskplane/tests -q`, any selected legacy nodeid, or any automatic lens sweep. A pickup, release-metadata, clean-SHA, help, wording, or protected-diff failure blocks. The task has no implementation or broad repair authority.

## Acceptance, contract, and graph coverage

The one task copies all three active R-0002 acceptance strings verbatim:

- AC4 is owned through accepted T05 evidence and the bounded pickup-suite recheck.
- The 2.17.20 release criterion is owned through accepted T06 evidence and the JSON/version/help/wording recheck.
- AC5's “zero new failures” outcome is owned through the already-executed T07 full-suite evidence, the 42/42 six-clone released-baseline comparison, the attributed human baseline exception, and the bounded pickup/protected-surface proof. No raw green claim is made for the inherited full suite.

The task declares exactly the seven active canonical ids, all eleven approved Design edges, all fourteen approved module identities as evidence coverage, and the exact 3/1/1 contract-only policy. Module and edge declarations describe accepted evidence coverage; they do not widen the sole `exports/**` write scope. There are zero per-task lenses.

Historical `contract:pickup.asymmetric-authenticity` and `resource:repository.pickup-public-verification-material` remain backlog-only negative-drift inventory. They create no task, edge, runtime, evaluation, or release claim.

## Exclusions and final authority stop

This Plan schedules no T05/T06 replay, product or test edit, version-metadata edit, full serial suite, automatic lens sweep, inherited-CI repair, Phase 0, E0, R-0011, unrelated R-0013, hook/legacy-loop work, signer/verifier work, dependency/tooling change, push, tag, publication, release, or `origin/main` mutation.

After the bounded proof passes, the Taskplane loop—not this task—runs quick security and quick QA concurrently against the identical candidate SHA and evidence fingerprints. EM follows on that same SHA/evidence. The workflow then stops for explicit human push authority. These stages remain mandatory loop-owned governance, not task criteria or per-task lenses.
