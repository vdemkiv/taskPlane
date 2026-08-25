# Plan — remaining attributed operator-trust pickup delivery

## Authority, baseline, and non-replay inventory

This Plan replaces all stale Plan authority and is bound to governing `specs/spec.md` SHA-256 `0ae3d5c0071e06b76cc67cd7216c6b9cc68231d18354634a8a6d8fa18984eda4`, approved Design content fingerprint `2cb1e7c7bd38e0f6ea3566fe0f454a757d60d488527c57ebb4026b6709ec34d9`, and engine-approved Design fingerprint `cc7060089b88e213736d2a584e30abad72e621f6a68b47399cc5dc13f62f76ea`. The exact product baseline is `a73f125e762670323d0e4a8fbbef3a1edf3ea958`; the approved post-Design graph fingerprint is `c6e3f9ed00e00c75fe07d3a4734633ea19d36d81b7a7ef4ce39d289025e26199` (55 modules / 177 edges).

Completed commits are inventory only and receive no task, checkpoint, implementation, or evaluation replay:

- T01 / AC1: `70f311ad75de33a530a6ba43ac213883a1e95c3f`
- T02 / AC2: `5cc647cabd8bd8528b3044184e38d3317f593f27`
- T03 released-tip prerequisite: `5c28165f800fffcac20aa2004d9a2b38efb195cf`
- T04 / AC3: `0c19087e3eb28d70869f2752f12c2d3742f33810`
- repository-only resume groundwork: `3ee3a17a695bf059f90504507a8eb5fe690fb52d`
- retry-safe atomic receipt publication: `a73f125e762670323d0e4a8fbbef3a1edf3ea958`

AC1 through AC3 may run only as unchanged regressions inside the final AC5 suite. Phase 0, E0, R-0011, unrelated R-0013, asymmetric/signing work, atomic-publication repair, protected surfaces, new dependencies/tooling, and external release mutation remain excluded.

The spec, Design, backlog Design inventory, and this Plan are governance evidence. Their already-dirty bytes must be committed only after Plan authorization in a governance-only commit, separate from the AC4 behavior-and-test commit. This planner does not commit them.

## Bounded graph result

The single required Taskplane 2.17.19 graph-impact call used the pinned Python 3.12/pytest 8 runtime and exactly the remaining eight implementation/release paths. It returned:

- graph fingerprint `c6e3f9ed00e00c75fe07d3a4734633ea19d36d81b7a7ef4ce39d289025e26199`;
- scan-quality fingerprint `588762304418faac88c9b244efa50eb756e2cafe9a871bcdcaef7ca1100f57c6`;
- source SHA `a73f125e762670323d0e4a8fbbef3a1edf3ea958`;
- 27 impacted modules, zero unknown modules, and no degraded producer;
- the approved depth policy: local 3, contract-only boundary, contract 1, requirement 1; and
- policy-bounded truncation only at the R-0001 → R-0002 requirement-depth boundary, with no local depth truncation.

No new module or dependency is introduced.

## Remaining serial graph

Only three executable tasks remain:

`T05 AC4 operator trust → T06 2.17.20 surfaces → T07 AC5 exact-SHA proof`

There are zero per-task lenses. DEFINE's five-lens sweep remains the sole DEFINE sweep. The only planned concurrency is the loop-owned final quick security plus QA pair after AC5.

### T05 — one AC4 implementation commit

Writable scope is exactly:

- `taskplane/tp.py`
- `taskplane/pickup.py`
- `taskplane/tests/test_pickup.py`

The pickup subparser alone disables option abbreviation for every pickup option. Canonical `--workspace` remains supported and visible. The human decision classifies shortened pickup spellings as accidental and unsupported, so no alias, shim, shortened-workspace test inventory, handwritten argv scan, private argparse override, root-parser change, or unrelated-subparser change is authorized.

The only accepted trust token is exact `--trust-source`. The public seam must reject all eleven proper prefixes and the named case, underscore, omitted-hyphen, and suffix lookalikes before `cmd_pickup`, `pickup.run`, BUILD-C, receipt publication, or state mutation.

`taskplane/pickup.py` adds exactly one frozen/slotted private `_OperatorTrust`, one sole `_parse_operator_trust` construction factory shared by raw CLI and raw receipt input, and one explicit closed serializer. It accepts only exact unmodified lowercase 40- or 64-hex values matching the shelf source SHA. Raw mappings and the typed trust value stop inside pickup; BUILD-C receives only the incumbent normalized micro-plan.

Operator mode appends strict `taskplane.pickup-receipt/v2` after an incumbent contiguous v1 prefix. The exact flag and full value are recorded verbatim as attributed operator authority with `cryptographic_authenticity_claimed: false`. It never claims human, producer, shelf, engine, or repository cryptographic authenticity. V1 after v2, malformed v2 assertions, source/Design mismatch, shelf structural failure, digest/path/predecessor mismatch, fork, gap, collision, and mixed lineage refuse before BUILD-C, preserve all prior receipts byte-for-byte, and create no authoritative partial receipt.

The no-flag private-secret path, v1 verifier/key source and exact receipt shape, direct assignment, BUILD-C, checkpoint, merge-on-green, retry-safe atomic publication, one-criterion discipline, and zero run/track/claim/lease/wave/private-handoff state remain unchanged. AC4 behavior plus its focused positive, negative, type-boundary, CLI, v1/v2, and v1-compatibility proofs land in the same commit.

Validation is increasing-cost: focused fail-fast selectors, the full pickup file, compile/import smoke, diff hygiene, then exact protected-surface comparison. Manual changed-line review requires complete boundary annotations and zero new `Any`, `cast`, type-ignore, noqa, untyped-dict boundary, or equivalent escape. No pydantic, ruff, black, mypy, pyright, formatter, lint, strict-type dependency/configuration, debt record, or precedent is added.

### T06 — bounded 2.17.20 release surfaces

After AC4 is committed and green, change only README, CHANGELOG, Codex manifest, Claude manifest, and marketplace manifest. All five identify 2.17.20; the three JSON files parse; the pinned CLI version and pickup-help smoke pass; and README/CHANGELOG truthfully state that attributed operator trust is structural agreement, not cryptographic authenticity.

This is a metadata-only commit. It changes no product, test, protected, signing, or tooling surface and performs no push, tag, package/marketplace publication, release, or `origin/main` mutation.

### T07 — AC5 clean exact-SHA proof

After T06, the checkout must be clean. Record its exact HEAD and run exactly:

`/private/tmp/taskplane-py312-pytest8-20260824/bin/python -m pytest taskplane/tests -q`

Zero new failures are required, including unchanged pickup, v1 symmetric, atomic-publication, hook, and legacy-loop regressions. A failure blocks and grants no product or broad repair authority. T07 changes no product, test, documentation, manifest, hook, or release file. It may persist only explicitly authorized delivery proof under `exports/**` if the approved engine requires a repository artifact; otherwise the governed command receipt is external loop evidence.

## Contracts, graph coverage, and fail-closed representation

Active task contracts are limited to the seven governing canonical ids:

- `contract:pickup.stateless-front-door`
- `contract:pickup.operator-trust-source`
- `contract:design.approved-contract`
- `contract:build-c.direct-assignment`
- `contract:build-c.acceptance-checkpoint`
- `contract:repository.merge-on-green`
- `resource:exports.pickup-receipts`

The task set declares all fourteen exact approved module identities, all eleven active proposed edges, exactly three task-owned acceptance strings, exactly seven active canonical contract ids, and the exact 3/1/1 contract-only policy. Protected incumbent modules appear only as Design coverage and diff witnesses; they are outside write scope.

`contract:pickup.asymmetric-authenticity` and `resource:repository.pickup-public-verification-material` remain historical negative-drift inventory only. They are intentionally absent from active task contracts and `design_edges`; no task, runtime, implementation, release, evaluation, or as-built claim realizes their metadata-only relation.

The historical ids are absent from the active requirement contract registry, approved Design contracts, proposed edges, task contracts, and task edge claims. They remain only non-authoritative backlog inventory. The mandatory final quick security/QA, same-SHA EM, and push-authority stop are separately classified as loop-owned post-acceptance governance, not a fourth acceptance criterion, executable task, proposed edge, implementation claim, or per-task lens.

## Final governed review and authority stop

After AC5 passes, the loop dispatches quick security and quick QA concurrently against the identical AC5 SHA and evidence fingerprints. Only after both complete does EM review that same SHA/evidence. The workflow then stops for explicit human push authority.

These are governed review stages/checkpoints, not per-task lenses or implementation tasks. No push, tag, publication, package/marketplace release, or `origin/main` mutation is authorized by this Plan.
