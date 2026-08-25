# R-0002 Design — explicit attributed operator trust for repository-only pickup

## Decision

Implement the accepted human decision from KB 0008 as one narrow mode:

```text
tp pickup <design-contract> --trust-source <exact-source-sha>
```

`--trust-source` is an explicit operator assertion for a fresh repository-only resume. `taskplane/tp.py` passes the exact argument spelling and value to `taskplane/pickup.py`. Pickup validates that the value is one full lowercase 40- or 64-hex Git object id and equals the shelf's exact `source_sha`; it then applies the incumbent closed shelf-structure, Design-fingerprint, receipt digest/path/predecessor, history, checkpoint, and merge identity checks. Only after all preflight checks pass may the existing BUILD-C entry run.

The assertion does not prove who typed it or who produced the shelf. Every new receipt labels the mode `attributed-operator-trust`, records `--trust-source` and the full supplied SHA verbatim, and carries `cryptographic_authenticity_claimed: false`. CLI help, output, trace, tests, documentation, release notes, and reviews use operator-trust/structural-agreement language for this mode. KB 0009 remains inherited standing state: actor strings are not cryptographically identified and v1 shelf evidence uses a private symmetric secret. This delivery neither changes nor claims to solve that limitation.

## Baseline and retained inventory

The exact source baseline remains HEAD `a73f125e762670323d0e4a8fbbef3a1edf3ea958`. The original pre-approval Design graph was `901fccc66e02c780213d13a5647414487fe3b3961475f5da5c75a2180010da47`, 53 modules/175 edges. Product corrected the executable authority classification and removed only the two stale asymmetric requirement edges; the current Design graph baseline is `c6e3f9ed00e00c75fe07d3a4734633ea19d36d81b7a7ef4ce39d289025e26199`, 55 modules/177 edges, at the same source HEAD. This is authority representation, not product implementation. `specs/spec.md`, the private R-0002 record, and accepted decisions 0008/0009 govern this amendment. The prior blocked asymmetric Design is inventory only.

The accepted Product correction is bound to `specs/spec.md` SHA-256 `0ae3d5c0071e06b76cc67cd7216c6b9cc68231d18354634a8a6d8fa18984eda4`. Its executable authority is exactly three acceptance criteria and seven active canonical contracts, including `contract:pickup.operator-trust-source`. `contract:pickup.asymmetric-authenticity` and `resource:repository.pickup-public-verification-material` are absent from active Design contracts and proposed edges; they remain only non-authoritative historical backlog inventory. This is a representation-only correction because Taskplane 2.17.19 Plan DoR treats every acceptance row and proposed edge as executable. It changes none of the selected HOW, modules, receipt schemas, validation behavior, or delivery scope below.

Completed T01–T04, repository-only resume groundwork, and retry-safe atomic receipt publication are non-replay inventory. The Design preserves their implementation and evidence. AC1–AC3 may run only as unchanged regression coverage in the final AC5 suite. Task-owned acceptance is exactly AC4, AC5, and the bounded 2.17.20 release surfaces. After those pass, the loop—not Plan tasks or per-task lenses—owns mandatory concurrent quick security+QA, same-SHA EM, and the human push-authority stop.

Current owners are already sufficient:

- `taskplane/tp.py` owns the public parser and delegates to pickup.
- `taskplane/pickup.py` owns clean checkout/shelf selection, structural repository resume, exact source and Design identity, receipt lineage, the live BUILD-C call, and atomic receipt projection.
- `taskplane/tests/test_pickup.py` already owns the shelf fixture, second-checkout flow, severed BUILD-C seam, lineage tamper coverage, and publication/interruption evidence.
- `taskplane/design_contract.py` and its v1 private-secret verification path remain byte- and behavior-unchanged.
- BUILD-C, checkpoint, repository, storage, hooks, and the legacy loop remain protected incumbent boundaries.

## Alternatives

### A. Explicit attributed operator trust — selected

Add the optional public flag, but require it whenever pickup has no incumbent private shelf secret. Validate exact full-SHA syntax and equality, then retain every structural and lineage check. Project the assertion into only the next receipt using a strict versioned shape.

Gains:

- satisfies the accepted operator decision and fresh-checkout AC4 without private-home handoff;
- makes the trust assumption visible, exact-SHA-bound, durable, and testable;
- changes only the CLI, pickup projection, focused tests, and bounded release surfaces; and
- preserves the existing BUILD-C/checkpoint/merge and atomic publication owners.

Costs:

- the human assertion is not cryptographic evidence of actor or producer origin;
- a matching SHA proves only agreement among the supplied value and repository identities; and
- mixed receipt history must support an old v1 receipt followed by a new operator-trust v2 receipt.

Revisit when a second operator, an untrusted producer host, or an external evidence-verification requirement appears; use the shelved backlog rather than extending this mode.

### B. V1 private-secret path only; no repository-only resume

Keep `tp pickup <design-contract>` exactly as it is and require the original private Taskplane secret in every checkout.

Gains: zero source/receipt change and incumbent symmetric verification remains available.

Costs: fresh second-checkout AC4 cannot run without a private-store handoff, so the selected product decision is not delivered.

Revisit only if the human withdraws repository-only resume.

### C. Asymmetric approval authority now

Add producer signing and repository public verification material now.

Gains: could eventually support producer-origin evidence.

Costs: requires signer/verifier/key workflow, trust material, runtime guarantees, and protected surfaces explicitly excluded by KB 0008 and the amended requirement.

Revisit only at a backlog trigger. The preserved future direction is OpenSSH `ssh-keygen -Y` plus a committed allowed-signers file and fail-closed absence handling; it is not a current contract, module, edge, task, claim, or evaluation.

### D. Status quo structural resume without explicit operator input

Continue accepting shelf structure and receipt lineage when the private secret is absent.

Gains: current second-checkout test keeps passing without a new flag.

Costs: the trust assumption stays implicit and unattributed, directly contradicting decision 0008 and amended AC4.

Revisit never for current R-0002.

## Active data and runtime contracts

### CLI contract

`tp pickup <design-contract> [--trust-source <exact-source-sha>]`

- `tp.py` adds one `--trust-source` argument and forwards its exact Python string without `.strip()`, case folding, abbreviation, revision resolution, or inference.
- The `pickup` subparser alone is constructed with `allow_abbrev=False`, so every pickup option uses its canonical spelling. Canonical `--workspace` remains supported and displayed; abbreviated workspace spellings are unsupported. The root parser and every unrelated subparser retain their incumbent behavior; no handwritten `argv` scan, private argparse override, or root-wide parser-policy change is permitted.
- Existing calls without the flag continue down the incumbent v1 private-secret path when its authority file exists.
- When the private authority file is absent, missing `--trust-source` refuses before BUILD-C.
- When the flag is supplied, operator-trust mode is explicit even if private state happens to exist; this prevents environment-dependent interpretation. It reads the shelf through the existing closed structural loader and does not invoke or change the v1 verifier.

The human scope decision explicitly classifies argparse's incumbent unique-prefix acceptance (for example, shortened `--workspace`) as accidental, undocumented behavior outside the supported governed-caller contract. This delivery intentionally does not preserve it and introduces no compatibility shim. Canonical `--workspace` remains green; abbreviated pickup options refuse. This attributed disposition resolves the DEFINE integrability finding without widening the implementation.

The only accepted trust option token is exactly `--trust-source`. Parameterize all eleven proper prefixes from `--t` through `--trust-sourc`, plus alternate case, underscore, omitted-hyphen, and suffix lookalikes; each exits before `cmd_pickup`, `pickup.run`, or BUILD-C. Canonical help shows `--workspace` and `--trust-source`. The accepted trust value syntax is exactly lowercase `[0-9a-f]{40}` or `[0-9a-f]{64}` and must equal `authority["source_sha"]` byte-for-byte. A symbolic ref, abbreviated SHA value, uppercase value, surrounding whitespace, other length, or mismatched full SHA refuses.

### Typed operator-trust boundary

`taskplane/pickup.py` defines one private immutable value object, `_OperatorTrust`, as a standard-library `@dataclass(frozen=True, slots=True)`. Its typed fields are the exact receipt values: `authority_mode`, `flag_name`, `flag_value`, and `cryptographic_authenticity_claimed`. No public constructor use is permitted. The only construction path is one fail-closed `_parse_operator_trust(raw, *, boundary, expected_source_sha, required)` factory:

- `boundary="cli"` accepts only the raw `str | None`, applies the exact required/full-SHA/equality rules, and returns `_OperatorTrust | None` (`None` only for the unchanged no-flag v1 path);
- `boundary="receipt"` accepts only a JSON mapping with the exact closed five-field serialization shown below, rejects unknown/missing fields and non-`false` claim values, and returns `_OperatorTrust`; and
- every other input type, boundary label, constant, or identity mismatch raises the named pre-BUILD-C refusal. No permissive constructor, `.strip()`, coercion, general mapping wrapper, or alternate validation path exists.

Only `_serialize_operator_trust(value: _OperatorTrust) -> dict[str, object]` projects the object to receipt JSON, by explicitly enumerating the five fields. Raw argparse strings and raw receipt mappings terminate at the factory. BUILD-C continues to receive only the incumbent normalized micro-plan; neither `_OperatorTrust` nor any raw authority mapping crosses that boundary.

The repository-wide generic preference for pydantic-style external-boundary models is deliberately overridden for this single bounded AC4 change by the governing standard-library-only/no-new-dependency delivery constraint and the protected-surface boundary. Revised Design approval is the scoped human policy disposition for the frozen dataclass plus one factory. It is not a standing waiver, a precedent for later boundaries, or permission to add another ad hoc parser.

### Receipt compatibility

Existing `taskplane.pickup-receipt/v1` bytes and validation remain accepted and unchanged. Operator-trust executions append `taskplane.pickup-receipt/v2`, whose closed field set is the v1 field set plus one `operator_trust` object:

```json
{
  "schema": "taskplane.pickup-operator-trust/v1",
  "authority_mode": "attributed-operator-trust",
  "flag_name": "--trust-source",
  "flag_value": "<exact unmodified full SHA>",
  "cryptographic_authenticity_claimed": false
}
```

The object participates in the existing canonical receipt digest and therefore in its content-addressed filename and successor predecessor digest. The v2 validator delegates the raw JSON object to the same `_parse_operator_trust` factory and requires the exact closed fields, constants, `false`, valid full-SHA syntax, and equality to the receipt/shelf authorized source SHA. A receipt directory may contain an initial contiguous sequence of v1 receipts followed by v2 receipts. A v1 receipt after the first v2 receipt is a mixed-mode lineage failure. Every v2 receipt must carry the same exact source value. No backfill or rewrite occurs.

The v1 private-secret path continues to write v1 receipts with precisely the incumbent fields. It never gains an operator object, mode switch, new secret behavior, or new claim.

### Runtime order

1. Resolve the repository-relative shelf regular file and clean checkout as today.
2. Select the mode from explicit `trust_source is not None`, not from ambient private state.
3. In operator mode, load the existing closed shelf structure and pass the raw CLI value plus its source SHA to the sole `_parse_operator_trust` factory; retain only the resulting frozen value object.
4. In v1 mode, call the existing `design_contract.load_approved_contract_for_pickup` unchanged.
5. Load and validate every tracked receipt, routing each raw v2 operator mapping through that same factory before applying v1/v2 transition rules, exact digests/paths, predecessor continuity, and unchanged BUILD-C checkpoint/merge evidence.
6. Validate receipt-explained Git history and dirty-state rules.
7. Emit `pickup.operator_trust.accepted` only after all operator preflight succeeds; then run the existing `build_c.run_pickup` seam for one criterion.
8. After green integration, explicitly serialize the frozen value into the v2 receipt and atomically publish it. Existing hard-link/fsync collision behavior remains unchanged.

Every missing/malformed/mismatched flag or shelf/lineage refusal occurs before BUILD-C. Refusal leaves prior receipt bytes unchanged, creates no authoritative partial receipt, and authorizes no checkpoint or merge.

## Active modules and boundaries

Changed product files for AC4 are only:

- `taskplane/tp.py`: pickup-subparser-only `allow_abbrev=False`, canonical `--workspace`/`--trust-source` help, exact trust forwarding, and neutral pickup help text;
- `taskplane/pickup.py`: one frozen operator-trust value, one shared fail-closed factory, explicit mode selection, v1/v2 receipt validation/projection, and operator-trust trace/refusals; and
- `taskplane/tests/test_pickup.py`: positive, negative, receipt compatibility, CLI forwarding, terminology, and v1 regression proofs.

Later downstream release changes are limited to README, CHANGELOG, and the three plugin manifests. Generated `exports/` receipts realize the existing resource contract; no static trust file or key material is added.

Protected and unchanged: `taskplane/design_contract.py`, `taskplane/loop.py`, `taskplane/taskplane_lite.py`, `taskplane/build_c.py`, `taskplane/checkpoint.py`, `taskplane/repository.py`, `taskplane/storage.py`, hooks, `.taskplane/codex-hook.py`, Plan, backlog, graph producer, CI, deploy, and all signing/key surfaces.

The active new boundary is `contract:pickup.operator-trust-source`. It connects the public CLI input to pickup preflight and the v2 receipt projection. The old ids `contract:pickup.asymmetric-authenticity` and `resource:repository.pickup-public-verification-material` survive only in the non-authoritative inventory at `design/backlog/asymmetric-approval-authority.md`. They appear in neither active Design contracts nor `graph.proposed_edges` and have no runtime provider/consumer, module realization, Plan task, implementation claim, acceptance claim, evaluation claim, or release claim.

## Executable validation

### AC4 positive

Extend the existing second-checkout test rather than replaying completed work:

- run criterion one through the unchanged v1 private-secret path and commit its existing v1 receipt;
- delete access to the first checkout's `.taskplane` state and clone a fresh second checkout;
- invoke the public `tp pickup` parser with `--trust-source` equal to the exact shelf source SHA;
- assert the next existing BUILD-C checkpoint/integration runs, zero private coordination state is created, the v1 predecessor remains byte-identical, and the new receipt is strict v2;
- assert `flag_name` and `flag_value` exactly equal the original CLI tokens, mode is explicit, the denial field is `false`, and SHA/Design/lineage/predecessor identities agree; and
- assert raw CLI/JSON inputs are converted only by `_parse_operator_trust`, the returned dataclass is frozen/slotted, explicit serialization reproduces the closed object, and BUILD-C receives only the unchanged micro-plan; and
- assert public result/trace/help/docs use operator-trust language and make none of the prohibited positive claims.

Alongside the end-to-end SHA-1-repository case, directly parameterize the sole factory and serializer with matching lowercase 40-hex and 64-hex values. Each must produce the same frozen value, exact five-field serialization, and receipt-boundary round trip without normalization.

### AC4 negative matrix

Run the same public seam with BUILD-C/checkpoint/merge calls instrumented to fail the test if reached. Cover:

- missing flag in repository-only mode;
- every proper prefix of `--trust-source` (`--t`, `--tr`, `--tru`, `--trus`, `--trust`, `--trust-`, `--trust-s`, `--trust-so`, `--trust-sou`, `--trust-sour`, `--trust-sourc`) plus `--Trust-source`, `--TRUST-SOURCE`, `--trust_source`, `--trustsource`, `--trust-sources`, and other alternate spellings; argparse must exit nonzero before `cmd_pickup`, `pickup.run`, or BUILD-C, with no receipt or private state;
- 39-, 41-, 63-, and 65-character hex, uppercase, whitespace, symbolic ref, and non-hex values;
- a different well-formed 40- or 64-hex value;
- missing, non-regular, malformed, or structurally changed shelf;
- stale Design fingerprint, mixed shelf source, approval/engine digest mismatch;
- receipt digest/path mismatch, predecessor mismatch, fork, gap, collision, unrelated/mixed lineage history;
- malformed v2 operator object, a `true` denial field, mismatched receipt flag value, v1 after v2, and mixed source SHA; and
- direct factory inputs with wrong boundary labels/types, missing/extra mapping fields, attempted coercion, and attempted mutation of the frozen value; and
- injected receipt publication interruption/collision using the existing atomic seam.

For each case, snapshot every prior receipt and `exports/` file first; assert byte equality afterward, no authoritative partial file, no BUILD-C/checkpoint/merge call, no private-state mutation, and a named refusal boundary.

### V1 compatibility

Keep the existing signed-shelf fixture on the incumbent private secret and invoke pickup without `--trust-source`. Assert it still calls `design_contract.load_approved_contract_for_pickup`, produces the exact v1 receipt field set with no `operator_trust`, and reaches the unchanged BUILD-C/checkpoint/merge path. Run existing v1 tamper selectors unchanged. No v1 golden byte or semantic expectation is edited merely to fit v2.

### Downstream validation

After the bounded AC4 behavior-and-test commit is accepted, update only the five 2.17.20 release surfaces. On the exact clean final SHA run `python -m pytest taskplane/tests -q`; zero new failures are required, including unchanged pickup, v1, atomic publication, hook, and legacy-loop regressions. Those three rows—AC4, AC5, and bounded release surfaces—are the complete task-owned acceptance set.

### Post-acceptance release gate — loop owned

After AC5 and the bounded release-surface criterion pass, the loop must run quick security and QA concurrently against that exact SHA and evidence set, then engineering-manager review against the identical SHA and evidence fingerprints, then stop before every external release mutation for explicit human push authority. This sequence is mandatory release authority, but it is not an acceptance criterion, Plan task, proposed module/edge, implementation/evaluation claim, or per-task lens.

## Python, tooling policy, and packaging

The repository root has no lint, formatter, or strict-type configuration: no ruff, black, mypy, pyright, `pyproject.toml`, `setup.cfg`, or `tox.ini` governs the shipped Taskplane code. The only discovered `pyproject.toml` is under `corpus/polyglot-app/services/pricing/`, an unrelated test fixture. This is inherited standing tooling state, not debt introduced by R-0002, and no debt record is authorized.

Revised Design approval is the scoped human policy disposition for this bounded standard-library AC4 change: introduce no ruff/black/mypy/pyright dependency or configuration. Validation uses the repository's incumbent tracked-surface compile/import gate, parser/version import smoke, focused pytest, and exact final full pytest; focused introspection with `inspect.signature`, `typing.get_type_hints`, `dataclasses.is_dataclass`, `dataclasses.fields`, and frozen-mutation refusal checks the new typed boundary. Manual review of the active Python diff requires annotations on every new/changed boundary and zero new `Any`, `cast`, `# type: ignore`, `# noqa`, untyped `dict` boundary, or equivalent escape hatch unless a line-specific new human disposition is recorded. This one-delivery policy is neither a standing waiver nor precedent for other work.

The flow is synchronous; there is no async task ownership, cancellation, mutable shared global state, or free-threaded coordination surface. Runtime validation remains at the CLI/JSON/receipt trust boundaries. Raw values stop at the one factory, the frozen value crosses only pickup-internal functions, explicit serialization owns persistence, and BUILD-C receives the existing micro-plan only.

No runtime or development dependency, subprocess, key library, lockfile, native extension, import namespace, or plugin packaging rule changes. The code stays within the repository's CPython 3.10–3.13 support and Python 3.14 syntax/design guidance. Package/archive checks must contain only existing modules and documentation—no signer, verifier, allowed-signers file, public key, private key, or cryptography dependency.

## Failure signals and recovery

- `operator-trust: --trust-source is required` — the operator re-invokes with the exact full shelf source SHA; no retry is automatic.
- `operator-trust: --trust-source is malformed` — the operator supplies one exact lowercase 40- or 64-hex SHA; refs/abbreviations are not resolved.
- `operator-trust: --trust-source does not match shelf source SHA` — the operator selects the intended shelf/source or stops for human review; pickup never edits either.
- existing `checkout-clean`, `approved-design`, `source-sha`, and `receipt-lineage` refusals remain authoritative for dirty/malformed/mixed evidence; the operator restores the exact tracked lineage or stops.
- `pickup.operator_trust.accepted` records only successful structural/source agreement before BUILD-C; it never uses a producer-origin claim.
- existing `pickup.receipt.lineage`, `pickup.checkpoint.started`, `pickup.integration.outcome`, and `pickup.storage.audit` continue to describe the incumbent execution path.

This is a synchronous CLI, so there is no always-on alert. Every refusal is immediate/nonzero and the committed receipt is the durable signal.

## Rollout, rollback, and debt disposition

Rollout is one AC4 implementation commit containing behavior and focused tests, followed only after green by the bounded 2.17.20 metadata commit and AC5 exact-SHA validation. Those three task-owned criteria complete delivery acceptance. The Taskplane loop then owns the mandatory concurrent quick security+QA, same-SHA EM, and human push-authority stop; none becomes a Plan task or per-task lens. No data migration or backfill occurs. Existing v1 receipts remain valid; the first operator-trust continuation appends v2.

Rollback is a normal forward revert of the optional parser argument, operator mode, v2 read/write support, focused tests, and release metadata. Never delete or rewrite a committed v2 receipt. A reverted 2.17.19 consumer may continue incumbent v1 private-secret pickup only on a pure-v1 lineage; any lineage already containing v2 fails closed until the complete v2 reader/writer is restored. It never claims to resume v2 evidence.

The lack of producer-origin evidence is inherited standing state registered by KB 0009, not new R-0002 debt, and no debt requirement is created. The future asymmetric direction is intentionally shelved in `design/backlog/asymmetric-approval-authority.md` under KB 0008 triggers.

## Graph DoR

- Exact HEAD, original pre-approval graph, current post-approval contract graph, accepted decisions 0008/0009, current owners, and non-replay inventory are captured.
- The active changed modules are limited to `tp.py`, `pickup.py`, and `test_pickup.py`; later release surfaces are separately bounded; every protected surface is explicit.
- Pickup-subparser-only `allow_abbrev=False`, canonical help, the attributed unsupported-abbreviation disposition, exact trust-option recognition, mode selection, exact string preservation, v1/v2 receipt schemas, mixed-lineage compatibility, failure order, and rollback are settled.
- The frozen/slotted value object, sole CLI/receipt parse factory, explicit serializer, raw-value stopping point, and unchanged BUILD-C payload are settled.
- Revised Design approval explicitly disposes the inherited no-lint/formatter/strict-type tooling limitation for this one delivery without dependency/config additions or precedent.
- Active contracts and the historical metadata-only ids are classified separately; depth is local 3, contract-only, contract 1, requirement 1.
- The governing spec hash, exactly seven active contracts, and separately non-authoritative two-id historical inventory are recorded; the Product correction requires no HOW change.
- Exactly three task-owned acceptance strings map to executable validation; the mandatory post-acceptance security+QA → EM → human authority sequence is loop-owned, and Product has no open question.

## Graph DoD

- Final graph realizes only `tp.py` → `contract:pickup.operator-trust-source` → `pickup.py`, existing pickup → BUILD-C/checkpoint/merge/exports edges, and focused test edges.
- Any signer, verifier, OpenSSH, cryptography, key, allowed-signers, or public-trust runtime/module edge fails proportionality and delivery.
- Historical asymmetric/public-trust ids remain outside active contracts and proposed edges; any Plan task, implementation/evaluation claim, runtime edge, or release claim for them fails delivery.
- Git diff shows zero protected-surface changes, especially `design_contract.py`, loop, taskplane_lite, BUILD-C, checkpoint, repository, storage, hooks, graph, CI, and deploy.
- Positive/negative/v1 focused tests prove canonical pickup-option enforcement, canonical `--workspace` remains supported, all eleven trust proper-prefix refusals, canonical help, exact assertion projection, matching 40/64-hex factory/serialization, all pre-BUILD-C refusals, byte-identical prior receipts, zero private state, and unchanged v1 behavior.
- Introspection/factory tests and manual changed-line review prove the immutable typed boundary, single construction path, explicit closed serialization, complete annotations, and zero new typing/lint escape hatches.
- Final clean-SHA full suite and later reviews satisfy only downstream AC5/release authority.
