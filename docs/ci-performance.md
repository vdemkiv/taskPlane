# Functional CI and evidence

The supported runner executes functional tests in disposable host stores:

```sh
python scripts/ci_local.py --check tests --junitxml /tmp/taskplane-tests.xml --report-json /tmp/taskplane-execution.json
python -m unittest taskplane.tests.test_runner_isolation.TestUnittestRunnerIsolation -v
```

The standalone canary verifies imports, collection, calls, child processes, failing
calls and failing collection against sentinel store hashes, modes and file census.
The wrapper runs it before collecting candidate tests. Direct pytest has a later
autouse-fixture boundary and is not equivalent. Shipped archives omit tests.

## Functional selection and platform coverage

The normal wrapper and remediation composite use the complete collected inventory.
`PERFORMANCE_ONLY_TESTS` names each excluded legacy workload and its reason by exact
module/function; parametrizations share that reviewed function identity. The runner
keeps original collection rows and marks the final selection separately. A capacity
marker alone cannot exclude a functional test. No mixed module is dropped.

`matrix_selections` and `verify_partition` require an exactly-once union of all
functional nodes across portability, core4, native2, host-specific and hostless
package cases, and browser. Excluded history remains in `complete_inventory` and
`excluded_history`; it is never a passing test. Missing or duplicate functional
nodes and incorrect suite/host/shard ownership fail. Hostless discovery errors
cannot become an empty partition. The explicit legacy `capacity` suite remains a
manual historical interface and is not invoked by required CI or remediation.

GitHub Actions retains Linux and representative Windows functional jobs. Existing
15-minute and Windows-core 20-minute job caps remain operational configuration.
There is no headroom percentage, duration target, repeated-matrix target, benchmark
sample quota or capacity dependency. `capacity.yml` is a disabled historical stub,
with no schedule. Its former failures and all retained historical logs remain
excluded history, not success. Local execution cannot certify Windows.

Optional skips need an exact `OPTIONAL_SKIP_RULES` function binding, the original
reason, actual platform/capability evidence, and any required parameter/native error.
A reason prefix or module exemption is insufficient. Required assertion bindings
reject skips, xfails, absent nodes/phases, duplicate calls, failed and unknown results.

## Accepted functional remediation

Run from the invocation workspace after all producers are integrated:

```sh
python work/taskplane-remediation/scripts/ci_local.py --check remediation --remediation-manifest .taskplane/remediation-v5/build/source-manifest.json
```

This reads the manifest's sibling `plan/verification-strategy.json`, `plan/output.json`
and `build/native-evidence.json`. The v5 `required_local_subchecks` and v6 `required_engineering_subchecks`
interfaces both require all 30 functional groups with their exact 18 criterion
bindings. V5 has 33 assertions; v6 has 34. Schema, group ownership, assertion
counts and the six v6 external gates are validated before interface normalization. `LOCAL-SCA-01`, `LOCAL-SCA-02` and
`LOCAL-CI-PROD-02` are retired; `LOCAL-STARTUP-EXECUTION` binds the v3 execution-only
regressions. Every required assertion is bound to explicit test functions or named
executable audits. Final root-reviewed bindings are authoritative; a green module
cannot substitute for missing bindings. Release supplies the exact forward interfaces
`test_dashboard_browser.py::test_dashboard_browser_functional_contract` and
`test_host_compatibility_watch.py::test_host_watch_observation_contract`; absent
functions/files remain failures until materialized and executed.

V3 regressions prove that missing timing providers, arbitrary elapsed telemetry,
more than eight framed responses and aggregate bodies above former startup targets
do not change functional readiness. Native identity, complete mandatory delivery,
actual output, committed hooks, stop handling and transport integrity still fail
closed. Configured retry safety policy remains functional; it is not a new resource
efficiency target. Legacy benchmark measurement helpers remain readable for old
reports and explicit historical use; functional CI never invokes their validators.

The composite emits complete JSON on stdout and never writes formal `results.json`
or `checks.json`. It retains per-group coverage, actual process records, source and
archive identities, failures and unknowns. Full behavioral validation of this
candidate is deferred to Engineering with the proper version installed. Authored
regressions and static checks are not execution, Windows, browser or native proof.

## Root producer interfaces

`BUILD-FINAL-PRODUCER-RECONCILIATION` uses
`taskplane.remediation-producer-reconciliation/v2`, with `run`, `visit`, `revision`,
`source_manifest_sha256`, `plan_sha256`, `candidate_root`, the complete `files` map,
`task_results`, `native_observations`, `owner_manifests` and `owner_evidence`.
The source/Plan digests pin immutable inputs. Every source path must match its
frozen owner and its current bytes/mode; passive preservation remains enforced.

`task_results` preserves actual accepted records and their original manifest subsets.
`owner_manifests` maps every owner's exact candidate paths to final hashes, with
nonempty actual handback/root inventory references in `owner_evidence`. Those
additional hashes are reconciled identity, not additional native acceptance.
Accepted outputs outside source are also checked against their actual bytes.
Root-owned bootstrap requires the frozen Plan's root exception and genuine accepted
record; it does not acquire a fabricated grant. Each `native_required` owner needs
its real accepted grant/worker, terminal observation and complete delivery receipt
reference. Before/after source maps and ledger bytes must remain unchanged.

`BUILD-FINAL-SCENARIO-REGISTRY` retains schema
`taskplane.remediation-scenario-registry/v1` and pins source-manifest SHA256,
verification-strategy SHA256, `candidate_map_sha256`, root reviewer,
`review_references` and every assertion key across 30 groups in `bindings` (33 for v5, 34 for v6).
Unknown audits, undeclared modules, excluded performance selectors, empty bindings
and absent/failed required test executions refuse. `SCENARIO_REGISTRY` is a concrete
source fallback for diagnostics and producer coordination, never final approval.

`BUILD-FINAL-OBLIGATION-DISPOSITIONS` retains schema
`taskplane.remediation-obligation-dispositions/v1`. Its `criteria` contains all 18
accepted IDs; `residuals` contains the six accepted later-stage categories:
NATIVE-01, WIN-01, BROWSER-01, MONITOR-01, HISTORICAL-01 and REVIEW-01. Every row
preserves a concrete Plan owner, dependencies, evidence class, actual observations,
pass/fail/blocked/unknown disposition, limits and next action. Disposition
completeness does not establish external closure or erase historical obligations.

## Lossless evidence transport

Both stdout remediation reports and `--report-json` use
`evidence_encoding.schema = taskplane.raw-evidence/zlib-base64-v1`. Commands,
statuses, bindings and inventories remain JSON fields. Raw bodies and complete
sample arrays are content-addressed original bytes; no global evidence-size gate
is imposed. `pack_evidence` encodes after functional verdicts are determined.

Each blob has its original SHA256 and byte count, compressed length, and ordered
base64 chunks of at most 49152 compressed bytes. Decoding requires exactly one
complete zlib stream and checks the original digest and length. Corruption,
truncation, trailing data, unresolved references and inconsistent size accounting
fail. A reference has exactly `{"$evidence":"<sha256>","view":"utf8|base64|json"}`.
Opaque native dictionaries and surrogate text round-trip without reinterpretation;
only runner-owned byte-envelope fields are treated as base64.

```python
import json
from scripts.ci_local import decode_evidence, raw_evidence_bytes
report = decode_evidence(json.loads(report_path.read_bytes()))
record = report['executions'][0]
original_junit = raw_evidence_bytes(record, 'junit.xml')
original_stdout = raw_evidence_bytes(record, 'stdout')
```

Original stdout/stderr, JUnit, collection/profile JSON and screenshots survive
disposable-directory cleanup. Semantic parsing uses original bytes; display
replacement characters cannot repair invalid JSON/XML into a pass. Existing
measurement accessors remain available for decoding historical reports. Readable
sample-counter projections and transport byte counts are descriptive only.

## Historical failures and remaining evidence

Retain the original Windows cancellation and retry separately: GitHub run
38403885257, artifacts 11818658038 and 11819871880, first-attempt log SHA256
`3dfb13d0709ba3d94c0c41f6fc391c1c57238bf771dda9080fa262913689d238`.
`main-ci-attempt1.json`, `main-ci.json`, `pr-ci.json` and raw cancellation output
must not be replaced by a later green run. Variation alone does not establish a
cause. Historical context/usage benchmark outcomes are excluded, not retroactive
passes. Native lifecycle/restart, actual final-candidate Windows/browser evidence,
future authorized monitor wake and independent Engineering review remain distinct
obligations with honest unknowns until observed.

## Direct verification after a workflow is retired

A user-authorized repair can use a separate release manifest without reopening the
retired run or inventing accepted native worker records. From the invocation
workspace, run:

```sh
python work/taskplane-remediation/scripts/ci_local.py --check release --release-manifest work/release-2.33.11/final-manifest.json
```

The command emits a lossless `taskplane.release-execution/v1` JSON report on stdout.
Exit zero and `local_status: pass` mean all local functional checks passed.
`release_status` remains `pending_external_verification`: even supplied external
reports require their independent review. Installation, publication and native-host
certification are separate. Unknown or unsupported gates stay explicit. The
retired governed command retains its original native producer preflight.

The manifest uses `taskplane.release-source-manifest/v1` and these fields:

| Field | Required content |
| --- | --- |
| `release`, `candidate_root` | Candidate version and invocation-relative source path. |
| `strategy` | `{path, sha256}` for the unchanged accepted v6 strategy. |
| `baseline.inventory`, `baseline.archive` | Hash-bound original inventory JSON and ZIP. The inventory has `files`, `count`, and `archive_sha256`. |
| `files` | Complete final `tree_map(candidate)` output, including SHA-256, byte length, regular-file kind and four-digit octal mode. |
| `changes` | Exactly one row for every modified or added path: `path`, canonical `before` identity (null for additions), `after` identity, `reason`, `reviewer`, and nonempty hash-bound `review_references`. |
| `retained_artifacts` | Hash-bound original `prior-package` entries with `host: openai` and `host: claude`, `historical-manifest`, and additive installer `maintenance-provenance`. If the baseline ZIP metadata was corrected, retain the original ZIP digest too. |
| `legacy_reader` | Invocation-relative `path`, complete canonical `files` map, `reviewer`, and hash-bound `review_references` for an authentic separately retained runtime incapable of v3. |
| `scenario_registry` | `reviewer`, hash-bound `review_references`, and `bindings` for all 34 v6 assertion keys. |
| `external_gates` | All six gate IDs, each with `status` (`unknown`, `unsupported`, `pass`, `fail`), `owner`, `reason`, `next_action` and an `evidence` array of hash-bound references. Passing declarations require evidence and are still not certified by this runner. |

Every reference uses `{ "path": "invocation/relative/path", "sha256": "64 lowercase hex digits" }`.
The final manifest lives outside source. `normalized_source_inventory()` converts
an original inventory's integer or octal-string modes to the canonical `tree_map`
format when constructing change records. Unchanged paths must match that original
inventory exactly. Removing an original file, omitting a changed-file review,
changing any bound evidence, or changing source during checks fails verification.
The original ZIP is inspected for exact members, bytes and modes without extraction.
Prior package bytes and additive maintenance provenance remain retained evidence;
they are never compared to the repaired package as if no repair were authorized.

Use `audit:release-preservation` for the reviewed repair accounting assertion.
`audit:producer-reconciliation` is refused by this direct path because it denotes
retired native acceptance. All other node/audit binding rules remain enforced.
The shared execution path runs the complete inventory, all 30 targeted functional
groups, all broad partitions, graph, quality, both reproducible package builds and
member audits. Every process and package report is bound to the final manifest and
source-map digest. Performance exclusions and required-skip rules are unchanged.

The legacy reader is required, not an optional skip. Shell-provided
`TEST_TASKPLANE_LEGACY_RUNTIME` is stripped. Only the reviewed manifest path is
passed to isolated children after preflight. Each process and the final audit
record the reader tree fingerprint; drift fails verification. The required actual
reader test still establishes v2 readability and v3 refusal before and after
compatible rollback. A synthetic implementation or another v3 reader cannot
satisfy that obligation.
