# Taskplane 2.19.1 — Final Bounded Evaluation

## Final disposition

- Verdict: **PASS**
- Actionable findings: none in the correction delta
- Candidate commit: `4c52d580b1b6a2abc420057a70d13add249be9a9`
- Candidate tree: `9d3010eb0ed2da4122d17617700369ab2f9637bf`
- Correction base commit: `2beff6eb1432ab70225b0c7db53bc62dfbb95dd4`
- Correction base tree: `1056356c82abde9fb02bfee7b6b0bd78e64f6445`
- Duplicate-ID fix commit: `66d76d975ba4810a4ed0a08ac78dff5d19d08348`
- Duplicate-ID fix tree: `6c410eaad7fd85c1286255cd5bc30a3cbbb7a080`
- Evaluated correction range: `2beff6eb1432ab70225b0c7db53bc62dfbb95dd4..4c52d580b1b6a2abc420057a70d13add249be9a9`
- Evaluator mode: zero lenses, read-only toward repository code

This evaluation covers only the two correction commits after the prior
candidate FAIL. No unchanged broad suite was rerun.

## Evidence chain

### Original R-0001 pickup PASS

- Report: [`/private/tmp/taskplane-pickup-evaluation-444002f.md`](/private/tmp/taskplane-pickup-evaluation-444002f.md)
- SHA-256:
  `499769ea6746d24296ce814a33daefc7c7ac99dcbbdb6990ddcd032b9280273d`
- Sealed source: `444002f9a2a23116d6911e885b875100369d99ac`
- Evidence: exact 165-test suite passed in 476.57 seconds, plus direct public
  export, resume, authority, BUILD-C, leakage, lineage, replay, collision, and
  partial-publication probes.

### Transparent prior final-candidate FAIL

- Report: [`/private/tmp/taskplane-pickup-evaluation-2.19.1.md`](/private/tmp/taskplane-pickup-evaluation-2.19.1.md)
- SHA-256:
  `68d03d9ca615bd0170867961de9cc1c7a56bbfdf8322559fe28c34007bfaaf42`
- Sealed source: `2beff6eb1432ab70225b0c7db53bc62dfbb95dd4`
- Finding: duplicate source-touchpoint `input_id` values silently discarded a
  requested touchpoint while coverage reported `complete=true` and
  `exhausted=true`.

The FAIL report remains unchanged. Its direct reproduction and all valid
bootstrap, release metadata, package, and inherited pickup evidence remain
part of the audit trail. This report supersedes only its candidate disposition
after directly validating the bounded correction.

## Correction inventory

The correction range changes exactly four files:

- `taskplane/depgraph.py`
- `taskplane/tests/test_depgraph.py`
- `taskplane/phase_handoff.py`
- `taskplane/phase_pickup.py`

Range size: 99 insertions and 51 deletions. `git diff --check
2beff6e..4c52d58` returned no errors.

An unrelated untracked file,
`docs/retrospective-2026-09-04-stateless-pickup.md`, was visible in the shared
worktree during this evaluation. It is not present in candidate tree
`9d3010eb...`, was not read as candidate evidence, and is not covered by this
PASS. If it is later committed, it is a new post-evaluation delta.

## Disposition of the prior blocking finding

### Duplicate source-touchpoint identity: RESOLVED

At `taskplane/depgraph.py:3300-3307`, the former silent skip:

```python
if input_id in used_ids:
    continue
```

is replaced by:

```python
if input_id in used_ids:
    raise ValueError("duplicate source touchpoint input_id")
```

The rejection happens while canonicalizing request identities, before
`read_cache` and `read_source` are created and before any call to
`graph_primitives.bounded_source_read`.

The added regression at `taskplane/tests/test_depgraph.py:155-171` covers:

- valid then missing touchpoint under the same explicit id;
- missing then valid touchpoint under the same explicit id; and
- collision between an automatically generated `input-0000` id and an
  explicitly supplied `input-0000` id.

It replaces the source reader with a failure sentinel, proving rejection
precedes repository reads. The owner demonstrated the regression red before
the production edit and then ran:

```text
python3 -m pytest -q taskplane/tests/test_depgraph.py
```

Result:

```text
14 passed in 7.68s
```

### Independent correction probe

The evaluator independently exercised the same three conflict classes on
candidate `4c52d58`, with a counting source-read sentinel. Result:

```json
{
  "results": [
    "duplicate source touchpoint input_id",
    "duplicate source touchpoint input_id",
    "duplicate source touchpoint input_id"
  ],
  "source_reads": 0
}
```

The previously hidden missing touchpoint can no longer be omitted behind a
false complete/exhausted record. The correction is minimal, deterministic,
fail-closed, and does not add a new schema or execution mechanism.

## Strict-typing correction review

Commit `4c52d580b1b6a2abc420057a70d13add249be9a9` changes only
`taskplane/phase_handoff.py` and `taskplane/phase_pickup.py`.

Inspection found the changes behavior-equivalent:

- `typing.cast` calls are added only after existing runtime validators have
  already normalized and closed the relevant values; casts are erased at
  runtime.
- Obligation and acceptance ordinals remain the exact integers constructed by
  `_validate_obligations` and `_validate_acceptance`; replacing `int(...)`
  with `cast(int, ...)` cannot admit unvalidated input.
- Task scope normalization was split into a local variable, but preserves the
  same behavior for lists and malformed non-list inputs.
- The local `tasks` selection was renamed `matched_tasks`; the same single-task
  and Build-transition checks remain in place.
- Publication locals were renamed and typed; manifest bytes, receipt fields,
  artifact iteration, fingerprints, and return values are unchanged.
- The `TYPE_CHECKING` import branch supplies relative types to mypy. At
  runtime `TYPE_CHECKING` is false and the prior package-versus-direct import
  branches execute unchanged.
- `selected: JsonObject` in Build task selection is an annotation only.
- No schema constant, public result field, refusal code, canonicalization rule,
  authority rule, scope rule, BUILD-C call, or publication path changed.

The supported direct-module runtime import path was checked independently and
still exposes:

```text
taskplane.stage-handoff/v2 taskplane.phase-pickup-result/v1
```

## Owner-run correction evidence

The correction owner reported these final-candidate results:

- Pinned mypy 1.17.1 strict validation: 109 files green.
- Ruff 0.12.9 across `taskplane`, hooks, and scripts: green.
- Exact focused behavioral command:

```text
python3 -m pytest -q taskplane/tests/test_stage_handoff.py taskplane/tests/test_stage_handoff_security.py taskplane/tests/test_pickup.py taskplane/tests/test_stateless_phase_pickup.py
```

Result:

```text
96 passed in 219.26s
```

This focused set exercises the handoff schema, repository/authority/security
validation, legacy pickup compatibility, public successor CLI behavior,
source-advancing authority, same-phase pickup, genuine BUILD-C submission,
fail-before-effects, and fresh-clone behavior affected by the typed files.

The prior package and release evidence remains valid because the correction
range does not touch packaging, manifests, release metadata, or package
fixtures:

- release freshness/tags: 11 passed in 5.20 seconds;
- corrected installed-package journey: 8 passed in 81.55 seconds;
- version verification passed;
- release-history audit returned `ok: true`, no problems.

The Taskplane graph engine was also rerun against the exact final code
candidate and reported:

- scanned head:
  `4c52d580b1b6a2abc420057a70d13add249be9a9`;
- 51 modules, 162 edges, and 664 files;
- `degraded=false`;
- graph artifact:
  `/Users/vdemkiv/.taskplane/projects/-Users-vdemkiv-codex-worktrees-a522-taskPlane-418ed73e/knowledge/graph.json`;
- artifact SHA-256:
  `f716ca940b1defb5a810253a7ebbe14b7b1bb894c21503b053d39ab6c24a5922`;
- graph content fingerprint:
  `7e44d9f3fe730a9a5e36b8cda2e44d2dd614c3772564bb2a68dfe212023bf867`.

This is exact-candidate graph evidence, not a native ReviewKernel verdict.

## Acceptance and Design carry-forward

All 12 R-0001 acceptance criteria remain met.

- AC1–AC5: public completion, Design/Plan/Build continuation, and same-phase
  remaining-only resume are behaviorally unchanged and included in the
  focused 96-test correction run.
- AC6–AC8: closed lineage, truthful authority, and fail-before-effects remain
  unchanged; added casts occur after existing closure checks, and focused
  schema/security tests pass.
- AC9: the authoring-to-BUILD-C edge and engine-generated checkpoint and
  integration evidence are unchanged; public pickup tests pass.
- AC10: legacy v1/v2 paths are unchanged; pickup compatibility tests pass.
- AC11: hidden-state and public redaction behavior are unchanged; no typed
  change widens a public projection or import dependency.
- AC12: canonical fingerprints, replay, conflict, and partial-publication
  behavior are unchanged; publication typing is a local variable/cast-only
  transformation.

The approved R-0001 module graph, 22 edges, seven contracts, and contract-only
depth policy remain conformant. The correction adds no module, lifecycle,
schema authority, orchestration engine, private-state dependency, or scope
expansion. The duplicate-id repair strengthens the independent bounded graph
evidence producer without modifying the R-0001 pickup graph.

## Remaining limitations and risks

1. The native review opener still cannot load the full candidate patch because
   the 530,022-byte patch exceeds its 400,000-byte bound. This is a tooling
   refusal, not a successful ReviewKernel gate; no ReviewKernel PASS is
   claimed. Confidence comes from the sealed prior reports, bounded commit
   inspection, direct duplicate probe, pinned static checks, and focused
   behavioral tests.
2. The evaluator did not rerun the unchanged 165-test combined suite, package
   suite, release tests, mypy, or Ruff. Their exact previously reported results
   are carried forward only where the correction range leaves their owners
   unchanged; the affected handoff/pickup files received the focused 96-test
   rerun plus strict typing and lint evidence.
3. The untracked retrospective file is not part of candidate commit `4c52d58`
   and is not evaluated. Any later commit, merge resolution, version change,
   or metadata edit requires a delta check against this pinned candidate.

These limitations are disclosed but do not identify a remaining code defect
in the evaluated correction range.

## Sealed verdict

The release-blocking duplicate-ID finding from candidate `2beff6e` is fixed and
independently verified before source reads. The strict-typing changes are
behavior-equivalent and supported by green pinned static validation plus 96
focused behavioral tests. The original pickup PASS, corrected 2.19.1
metadata/package evidence, and all R-0001 acceptance and Design dispositions
carry forward transparently.

Candidate `4c52d580b1b6a2abc420057a70d13add249be9a9` is **PASS** for final EM review,
subject to the explicitly stated candidate pin and native-review tooling
limitation.
