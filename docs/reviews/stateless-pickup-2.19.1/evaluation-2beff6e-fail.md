# Taskplane 2.19.1 Final-Candidate Bounded Evaluation

## Verdict and identity

- Overall candidate verdict: **FAIL**
- Blocking findings: 1
- Evaluator: Taskplane `tp-evaluator`, zero lenses
- Candidate repository: `/Users/vdemkiv/.codex/worktrees/a522/taskPlane`
- Candidate commit: `2beff6eb1432ab70225b0c7db53bc62dfbb95dd4`
- Candidate tree: `1056356c82abde9fb02bfee7b6b0bd78e64f6445`
- Merge base: `refs/remotes/origin/main` at
  `6db69cdc81d92eafbeed950fee62ac393ecba89a`
- Merge-base tree: `5c8400080855b5d20272495208492bd15938e103`
- Bootstrap/source-coverage boundary: commit
  `9562e1b8d6d6e5c8ccdb43cecf76a68c49d8d510`, tree
  `6113ca99470dc23095b2a3f3df859f963e2fa0cd`
- Inherited pickup PASS boundary: commit
  `444002f9a2a23116d6911e885b875100369d99ac`, tree
  `ec836cfcb542b04c9b2c2aad8ea8cb10eafe5fbf`
- Worktree status during evaluation: clean
- Lens workers launched: 0
- Repository files changed by this evaluation: 0

The verdict is sealed against the candidate commit and tree above. It covers
only the previously unevaluated ranges:

1. `6db69cdc81d92eafbeed950fee62ac393ecba89a..9562e1b8d6d6e5c8ccdb43cecf76a68c49d8d510`
2. `444002f9a2a23116d6911e885b875100369d99ac..2beff6eb1432ab70225b0c7db53bc62dfbb95dd4`

The 165-test R-0001 suite was not rerun.

## Inherited sealed pickup evidence

The original stateless-pickup implementation remains covered by the prior
sealed report:

- Report: [`/private/tmp/taskplane-pickup-evaluation-444002f.md`](/private/tmp/taskplane-pickup-evaluation-444002f.md)
- SHA-256:
  `499769ea6746d24296ce814a33daefc7c7ac99dcbbdb6990ddcd032b9280273d`
- Original result: `165 passed in 476.57s (0:07:56)` plus the documented
  public export, resume, authority, BUILD-C, lineage, leakage, replay,
  collision, and partial-publication probes.

The following implementation owners are byte-unchanged from `444002f` to the
final candidate:

- `taskplane/build_c.py`
- `taskplane/checkpoint.py`
- `taskplane/design_contract.py`
- `taskplane/loop.py`
- `taskplane/phase_handoff.py`
- `taskplane/phase_pickup.py`
- `taskplane/repository.py`
- `taskplane/review_evidence.py`
- `taskplane/stage_entities.py`
- `taskplane/stage_handoff.py`
- `taskplane/taskplane_lite.py`
- `taskplane/tp.py`

`git diff --quiet 444002f..2beff6e -- <the twelve paths above>` returned 0,
and `444002f` is an ancestor of the candidate. Therefore the prior PASS is
carried forward only for that exact pickup scope; it is not reused as evidence
for the bootstrap, source-touchpoint, release metadata, or package-fixture
deltas evaluated here.

## Newly evaluated delta inventory

### Merge base through `9562e1b`

Three commits were inspected:

- `6ecf90d976271e84ee0fd08bf9b5f52a1eb697fc` — fresh Codex native-hook
  bootstrap and installed-package wiring.
- `8d02d85` — Taskplane 2.19.0 candidate metadata and release-history
  disposition.
- `9562e1b8d6d6e5c8ccdb43cecf76a68c49d8d510` — bounded source-touchpoint
  coverage and its lower-level no-follow source reader.

Range size: 16 files, 821 insertions, 41 deletions. The production behavior
is concentrated in `taskplane/storage.py`, `taskplane/tp.py`,
`scripts/package_openai.py`, `taskplane/graph_primitives.py`, and
`taskplane/depgraph.py`, plus release metadata and focused tests.

### Pickup PASS through final 2.19.1 candidate

Two commits were inspected:

- `aa924bf` — 2.19.1 manifests, release/compatibility metadata, documentation,
  and release-history disposition.
- `2beff6eb1432ab70225b0c7db53bc62dfbb95dd4` — package-journey fixture reads
  the canonical manifest version instead of retaining a stale filename
  constant.

Range size: 9 files, 32 insertions, 23 deletions. No pickup runtime owner was
changed.

`git diff --check` was clean for both bounded ranges.

## New test and audit evidence

### Release freshness and tag policy

Exact parent-run command:

```text
python3 -m pytest -q -x taskplane/tests/test_release_freshness.py taskplane/tests/test_release_tags.py
```

Result:

```text
11 passed in 5.20s
```

The parent also reported:

- `taskplane/tp.py version --verify` passed.
- `scripts/ci_release_tags.py --json` returned `ok: true` with no problems.

Inspection confirmed that the marketplace, Claude, and Codex manifests,
`release_evidence.CURRENT_VERSION`, README, changelog, and compatibility matrix
name 2.19.1. Release history explicitly records 2.19.0 as an unreleased
candidate superseded by 2.19.1 at `scripts/ci_release_tags.py:86-102`.

### Bootstrap, source coverage, and initial package run

Exact parent-run command:

```text
python3 -m pytest -q -x taskplane/tests/test_depgraph.py taskplane/tests/test_r0013_bootstrap_home.py taskplane/tests/test_r0002_release_package_journey.py
```

Result before the fixture correction:

```text
16 passed, then the first package test failed in 12.45s
```

The failure was a stale test-only `VERSION = "2.19.0"` filename expectation
after the manifest advanced to 2.19.1. Commit `2beff6e` replaced the constant
with a read from `.codex-plugin/plugin.json`; it did not change production
package or pickup behavior. This initial failure is retained here rather than
discarded.

### Corrected complete installed-package journey

Exact parent-run command:

```text
python3 -m pytest -q -x taskplane/tests/test_r0002_release_package_journey.py
```

Result on the final candidate:

```text
8 passed in 81.55s
```

This executed the extracted OpenAI and Claude packages and covered the real
native SessionStart onboarding/link-worktree journey, host/hook-path rejection,
package parity/reproducibility, Plan approval and root preparation, and the
current manifest-derived archive filename. The native no-locator exception is
narrowly limited to `hook_path="native"` and the canonical default home at
`taskplane/storage.py:461-497`; bridge and conflicting custom-home cases remain
fail closed. The installed OpenAI packager projects SessionStart as native at
`scripts/package_openai.py:1043-1055`, and the real archive journey verifies
the native receipt without creating a workspace locator at
`taskplane/tests/test_r0002_release_package_journey.py:245-280`.

## Blocking finding

### [P1] Duplicate source-touchpoint ids can hide requested work while claiming exhaustive completion

Location: `taskplane/depgraph.py:3300-3307`, with the false terminal state
formed at `taskplane/depgraph.py:3535-3575`.

`build_source_touchpoint_coverage` sets `counters.requested` from the original
request length, then silently skips every later row whose `input_id` already
appeared:

```python
used_ids = set()
...
if input_id in used_ids:
    continue
```

The skipped row is absent from both the canonical `requested` value and the
per-input `results`. No duplicate/conflict reason is added, `non_verified`
does not increase, and the final state can be `complete` with
`exhausted=true`.

A bounded direct probe supplied two requests sharing `input_id="same"`: the
first referenced existing `a.py`; the second referenced missing `missing.py`.
Observed output:

```json
{
  "input_count": 2,
  "serialized_requested_count": 1,
  "result_count": 1,
  "counters": {
    "requested": 2,
    "verified": 1,
    "non_verified": 0,
    "files_read": 1,
    "bytes_read": 10,
    "symbols_seen": 0,
    "edges_examined": 0,
    "unexplored_edges": 0
  },
  "coverage": {
    "state": "complete",
    "complete": true,
    "exhausted": true,
    "frontier": [],
    "unresolved_input_ids": []
  },
  "reason_codes": []
}
```

This contradicts the source-coverage contract's claim to verify feature
touchpoints exhaustively: a requested missing touchpoint disappeared while the
record asserted complete and exhausted coverage. The focused test at
`taskplane/tests/test_depgraph.py:104-152` asserts every requested id is present
but uses only unique ids, so it does not exercise this conflict.

Impact: an ambiguous or malformed producer can cause dependency/source-impact
evidence to omit an explicitly requested touchpoint without any visible
truncation, unresolved state, or refusal. That is fail-open evidence at a
governance boundary and blocks candidate acceptance even though no current
consumer was found outside the new producer/tests.

Required correction:

1. Reject duplicate `input_id` values before coverage, or retain deterministic
   conflict rows and force a non-complete state.
2. Enforce an invariant that every input request has a visible result or a
   stable named refusal and that counters reconcile with serialized rows.
3. Add a regression containing two different touchpoints with the same id and
   assert coverage can never be `complete`/`exhausted`.

No source fix was made by this evaluator.

## Design and acceptance carry-forward

All 12 stateless-pickup acceptance criteria and the approved R-0001 Design
Contract remain carried forward from the sealed `444002f` report because the
entire pickup runtime owner set is byte-unchanged. Specifically:

- public Design/Plan export and fresh pickup remain covered;
- public Build still derives committed scoped authoring, crosses real BUILD-C,
  and auto-publishes the next handoff;
- same-phase Design/Plan/Build resume still schedules only remaining work;
- source-advancing authority remains ancestor-validated;
- cross-phase receipts remain lineage rather than successor completion;
- no private startup state/path is exposed;
- v1/v2 compatibility and deterministic publication evidence remain inherited.

The 2.19.1 metadata truthfully describes that inherited behavior, and the
corrected package fixture now follows the manifest version. No metadata delta
widens scope or reinterprets a pickup contract.

The overall candidate nevertheless fails because the merge-base-to-R-0001
baseline delta contains the independent source-touchpoint completeness defect
above. Passing R-0001 criteria cannot authorize a false-complete graph evidence
producer outside R-0001's original evaluated range.

## Native review limitation

The native review opener could not load the full candidate patch because its
530,022-byte size exceeded the tool's 400,000-byte bound. This is a tooling
limit, not a successful ReviewKernel gate, and no ReviewKernel PASS is claimed.

The limitation was mitigated by:

- splitting inspection into the two explicitly bounded Git ranges;
- inspecting each new commit and changed-file inventory;
- proving pickup runtime owners unchanged from their sealed PASS;
- using the exact focused parent-run test evidence above;
- inspecting bootstrap, packaging, version, release-history, source-reader,
  and source-coverage boundaries directly; and
- running the bounded duplicate-id contract probe that found the blocking
  defect.

Evidence confidence is high for the unchanged pickup carry-forward, version
metadata, and installed-package flow. Full-patch native review coverage is
unavailable and remains an explicit limitation. It does not weaken or create
uncertainty around the FAIL disposition because the blocking defect is directly
reproducible in the bounded changed code.

## Final disposition

The final candidate at
`2beff6eb1432ab70225b0c7db53bc62dfbb95dd4` is **not ready for EM approval or
merge to main**. The inherited stateless-pickup implementation remains PASS,
and the 2.19.1 metadata/package correction is green, but the candidate must
first make duplicate source-touchpoint identities fail closed and add the
missing regression.
