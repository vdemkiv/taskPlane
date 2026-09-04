# Taskplane 2.19.1 — Final Post-4c52 Bounded Evaluation

## Disposition

- Verdict: **PASS** for source candidate
  `0f3f305c8b6ef29a6ea3ed4cc879fac81fc7eb00`.
- Candidate tree: `36596b8b6c1f9a6756424f772e0ad580c0fd43b5`.
- Evaluation base: `4c52d580b1b6a2abc420057a70d13add249be9a9`,
  tree `9d3010eb0ed2da4122d17617700369ab2f9637bf`.
- Merge base with `refs/remotes/origin/main`:
  `6db69cdc81d92eafbeed950fee62ac393ecba89a`, tree
  `5c8400080855b5d20272495208492bd15938e103`.
- Evaluated range: `4c52d58..0f3f305` (four commits).
- Actionable source findings: none.
- Merge disposition: recommended only after hosted checks are green on the
  final PR head and the pending documentation-index publication is validated.
- Mode: zero lenses; repository source remained evaluator-read-only.

This is a bounded delta judgment. It does not rerun or replace the original
pickup evaluation, the transparent later FAIL, or the correction evaluation.

## Inherited sealed evidence

1. Original R-0001 pickup PASS:
   `/private/tmp/taskplane-pickup-evaluation-444002f.md`, SHA-256
   `499769ea6746d24296ce814a33daefc7c7ac99dcbbdb6990ddcd032b9280273d`,
   source `444002f9a2a23116d6911e885b875100369d99ac`. It records the exact
   declared 165-test suite passing in 476.57 seconds and the public-flow
   Design/Plan/Build, same-phase resume, BUILD-C, lineage, authority,
   fail-before-effects, recovery, and leakage probes.
2. Transparent candidate FAIL:
   `/private/tmp/taskplane-pickup-evaluation-2.19.1.md`, SHA-256
   `68d03d9ca615bd0170867961de9cc1c7a56bbfdf8322559fe28c34007bfaaf42`,
   source `2beff6eb1432ab70225b0c7db53bc62dfbb95dd4`. It remains unchanged and
   records the duplicate source-touchpoint identity defect.
3. Corrected 2.19.1 PASS:
   `/private/tmp/taskplane-pickup-evaluation-final-2.19.1.md`, SHA-256
   `ce2d38d8b8911b85f9c76f4c8a77227497d8c2b97fb2798554d9907677b75eb3`,
   source `4c52d580b1b6a2abc420057a70d13add249be9a9`. It records independent
   duplicate-ID refusal before reads, `test_depgraph.py` 14 passing in 7.68
   seconds, strict mypy/Ruff evidence, and the exact focused handoff/pickup
   command passing 96 tests in 219.26 seconds.

Commit `1923285baeed96e8ac52a907ab005a905e5eef31` adds a retrospective and
byte-identical repository copies of these three reports. Their verified
repository-copy hashes are, in order, `499769ea...273d`, `68d03d9c...af42`,
and `ce2d38d8...75eb3`. This documentation commit creates no new runtime
claim and does not rewrite earlier candidate identities.

## Post-4c52 correction inventory

- `7c96883eb3bb466d117fd713e3f1fadf7d3235ee`: tests only. Eight subprocess
  calls now decode explicitly as UTF-8 with replacement, and one stale
  diagnostic regex now matches the existing fingerprint refusal. Production
  behavior is unchanged.
- `59a2b6d9bc0b8772b37f49c9e1ccf89d2a5d052f`: only
  `acceptance_map[*].tests` selectors changed in `design/contract.json`; the
  replacements name existing public and legacy tests. No acceptance
  criterion, design element, module, edge, contract, depth policy, risk, or
  scope changed.
- `1923285baeed96e8ac52a907ab005a905e5eef31`: documentation only, as noted
  above.
- `0f3f305c8b6ef29a6ea3ed4cc879fac81fc7eb00`: five-file runtime correction
  in `design_contract.py`, `phase_handoff.py`, `stage_entities.py`,
  `taskplane_lite.py`, and the focused non-Build handoff test.

`git diff --check 4c52d58..0f3f305` returned no errors. The range changes no
version, package manifest, phase-handoff schema, public result schema,
BUILD-C path, source-touchpoint logic, or publication behavior.

## Design identity and selector correction

The approved Design source remains:

- commit `b3f6a71ff886a40c138d8f672fc1de1ea008b455`;
- contract blob `5d17f073b57cc320ff1adb2859eca19a47983ca4`;
- original file SHA-256
  `fee21e5c2cd7afb560ed0d4ed2f7792e099a4d96ed7db3cfcf70032de68ba03d`;
- recorded approval fingerprint
  `87c9df00fc004a2e4a6ba20b5bf241e77636bdd223f0cc001221be6e343d497c`.

The corrected file SHA-256 is
`6e96446ddd177666db6cd02f8aea149844b5af82cb175fc81e4c5e5dad0705a7`;
the Design owner reports its recomputed body fingerprint as
`b8acbce1283ca11284e3f09a56afc2198185e8dcfbfa3e99bb111382667f65cd`.
The evaluator independently verified both file SHA values, the original blob,
and that the exact diff changes only test-selector arrays. This report does
not reinterpret that metadata correction as a new Design approval. Approved
Design semantics are carried from the original identity; corrected selectors
are evidence-index metadata.

## Resolved intermediate dependency regression

The moving candidate at `59a2b6d` temporarily joined the existing import
cycles through new `stage_entities -> design_contract` and
`taskplane_lite -> review_evidence` dependencies. This was a real regression,
not waived evidence. The final correction resolves it without changing the
cycle policy:

- strict attributable-human validation is consolidated in the dependency-light
  phase-handoff owner at `taskplane/phase_handoff.py:528-552`;
- the Design adapter delegates to that owner and preserves
  `PhaseGateAuthorityError` plus prior whitespace normalization at
  `taskplane/design_contract.py:77-82`;
- startup projection validates the complete sealed handoff through the
  phase-handoff owner at `taskplane/stage_entities.py:121-175`;
- five narrow stage-owned facades delegate scoped-view/result-schema work to
  the existing review-evidence owner at `taskplane/stage_entities.py:238-270`;
- `taskplane_lite.py:4377-4390` and `:4494-4564` consume those stage facades.

There is no dynamic-import concealment, copied validator, new lifecycle,
second schema authority, policy relaxation, or scope expansion.

The evaluator ran exactly:

```text
python3 taskplane/import_cycles.py --root . --policy taskplane/tests/fixtures/import-cycles.json --check
```

Result: `status=pass`, `violations=[]`, no added or removed members/edges, and
the two policy SCCs remain exactly 17 members/49 edges and 7 members/13 edges.

## Independent behavior probes

1. Human-authority owner and adapter:

```text
human:synthetic-reviewer phase_handoff REFUSED PhaseHandoffError authority-missing
human:synthetic-reviewer design_contract REFUSED PhaseGateAuthorityError authority-missing
human:mechanical_reviewer phase_handoff REFUSED PhaseHandoffError authority-missing
human:mechanical_reviewer design_contract REFUSED PhaseGateAuthorityError authority-missing
normalized-human human:vdemkiv
```

2. A complete Design resume handoff was resealed with a syntactically valid
   `human:synthetic-reviewer` receipt and adjusted downstream fingerprints.
   `stateless_phase_startup` refused before dispatch:

```text
StageDispatchError: stateless phase handoff refused before dispatch:
authority-missing: gate actor is not an attributable human
```

3. Each of the five new stage facades was compared with its existing
   `review_evidence` implementation on a valid Design-resume handoff. Creation
   and validation results were equal in all five cases.

4. Valid Design and Plan same-phase resume startups both retained
   `completed=['O1']`, `remaining=['O2']`, and scoped only obligation `O2`.

These probes directly establish strict authority, public exception
compatibility, behavior-equivalent delegation, and remaining-only scheduling.

## Additional exact-candidate evidence

The correction owner reported:

- `python3 -m pytest -q taskplane/tests/test_stage_non_build_handoffs.py`:
  10 passed in 0.43 seconds; the two synthetic/mechanical cases reproduced red
  before the production correction.
- pinned strict mypy over 109 files: green.
- pinned Ruff over Taskplane, hooks, and scripts: green.
- all three internal package builds repeated twice with byte-identical output;
  OpenAI package SHA-256
  `c16b6ba65d34d08ce39e3f9c74198de62860f1e9c4e56d5bb5555cd1970aaf5c`
  and Claude zip/plugin SHA-256
  `2f270056005e2e160e85b17446124561c7a08fae991c359ebeb8e19d8864e261`.

The evaluator did not rerun those owner commands. They supplement, rather
than replace, the independent cycle, actor, facade, and resume probes above.

The exact-candidate graph artifact was independently hash-checked at
`/Users/vdemkiv/.taskplane/projects/-Users-vdemkiv-codex-worktrees-a522-taskPlane-418ed73e/knowledge/graph.json`:

- SHA-256 `5d93d15a0eddda12f697eef77db620273aff7192e180592e174eec24197e7168`;
- scanned head `0f3f305c8b6ef29a6ea3ed4cc879fac81fc7eb00`;
- 52 modules, 167 edges, 667 files, `degraded=false`;
- content fingerprint
  `28a33591c1fa2021000cbabb491ef9f70f5f53d751741174055c8a7967b6225e`.

The scoped impact artifact
`/private/tmp/taskplane-pickup-impact-0f3f305.json` has SHA-256
`f435fb7c4d93d4c881f1fd9fd953fdd3a2dd144e56b4633cf013872082288a07`.
It reports 27 impacted nodes, `truncated=false`, no policy blocks, and an
unknown root limited to documentation/metadata. Its product/requirement link
sets are empty, so it is graph context, not proof of requirement closure.

## Acceptance and Design carry-forward

- **AC1-AC5: MET.** Original public completion/fresh-clone journeys and exact
  remaining-only Design/Plan/Build resume evidence carry forward. The current
  Design/Plan resume path was rechecked directly.
- **AC6-AC8: MET.** Closed lineage, truthful authority, and pre-effect refusal
  carry forward; the final correction strengthens actor rejection in the
  canonical handoff owner and directly refuses a fully resealed synthetic
  receipt before startup.
- **AC9: MET.** Authoring-to-BUILD-C behavior is untouched.
- **AC10: MET.** v1/v2 public compatibility is untouched; the Design adapter's
  normalization and exception class were rechecked directly.
- **AC11: MET.** No private-state or path projection changed. Removing the two
  backward dependencies narrows, rather than widens, startup coupling.
- **AC12: MET.** Canonical identity, replay, conflict, and partial-publication
  behavior are unchanged; the post-4c52 production correction does not touch
  those owners.

The approved module set, 22 proposed edges, seven contracts, and depth policy
remain conformant. The final architecture uses one phase-handoff authority
owner and thin stage facades over the existing evidence owner. It satisfies
the user's simplicity constraint: small local units, no duplicate engine, no
new orchestration framework, and no hidden import workaround.

## Limitations and merge condition

1. The native standalone review opener refused the 530,022-byte full patch at
   its 400,000-byte bound (`canonical diff derivation failed`). This remains a
   tooling limitation, not a successful ReviewKernel gate. No native gate PASS
   is claimed.
2. The unchanged 165-test suite, 96-test focused suite, release tests, package
   journey, depgraph suite, mypy, and Ruff were not broadly rerun by this
   bounded evaluation. Their sealed or owner-reported identities are carried
   transparently only where the delta leaves behavior unchanged.
3. Hosted CI for the final PR head is pending and is mandatory before merge.
   This report does not state or imply that pending CI passed.
4. After source `0f3f305` was sealed, the root owner began a documentation-only
   update to the retrospective and evidence index. Those uncommitted bytes are
   outside this source verdict and must be validated before their separate
   publication commit.

Subject to green hosted checks and validation of that docs-only publication,
the source candidate is suitable for final Engineering review and merge.
