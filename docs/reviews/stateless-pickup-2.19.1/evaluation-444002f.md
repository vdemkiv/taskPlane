# R-0001 Stateless Phase Pickup — Sealed Evaluation

## Evaluation identity

- Verdict: **PASS**
- Evaluator role: Taskplane `tp-evaluator`, zero-lens fallback evaluation
- Evaluation date: 2026-09-04
- Repository: `/private/tmp/taskplane-pickup-stateless`
- Evaluated base commit: `9562e1b8d6d6e5c8ccdb43cecf76a68c49d8d510`
- Evaluated base tree: `6113ca99470dc23095b2a3f3df859f963e2fa0cd`
- Evaluated source commit: `444002f9a2a23116d6911e885b875100369d99ac`
- Evaluated source tree: `ec836cfcb542b04c9b2c2aad8ea8cb10eafe5fbf`
- Evaluated range: `9562e1b8d6d6e5c8ccdb43cecf76a68c49d8d510..444002f9a2a23116d6911e885b875100369d99ac`
- Repository state after evaluation: clean
- Lens workers launched: 0
- Actionable findings: none

This report is sealed to the source commit and tree above. It evaluates the
R-0001 implementation only. Any later version bump, packaging change, or
2.19.1 metadata work is outside this evaluation and must be treated as a
separate post-evaluation delta; it does not retroactively change the identity
or scope of this PASS.

## Declared combined acceptance suite

The exact required suite was run once against the sealed source commit:

```text
python3 -m pytest -q taskplane/tests/test_stateless_phase_pickup.py taskplane/tests/test_pickup.py taskplane/tests/test_r0001_pickup_cold_start.py taskplane/tests/test_stage_handoff.py taskplane/tests/test_stage_handoff_security.py taskplane/tests/test_stage_non_build_handoffs.py taskplane/tests/test_stage_loop_integration.py taskplane/tests/test_build_quality.py
```

Result:

```text
165 passed in 476.57s (0:07:56)
```

No test in this suite failed, skipped due to an evaluation workaround, or was
replaced by inferred evidence.

## Static integrity checks

Changed Python modules were compiled using an external bytecode cache so the
repository remained read-only:

```text
git diff --name-only --diff-filter=AM 9562e1b..HEAD -- '*.py' |
xargs env PYTHONPYCACHEPREFIX=/private/tmp/taskplane-r0001-final-pycache python3 -m py_compile
```

Result: exit 0.

Diff whitespace validation:

```text
git diff --check 9562e1b..HEAD
```

Result: exit 0, no output.

The final `git status --short` was empty and `git rev-parse HEAD` returned the
sealed source commit.

## Bounded direct public-flow probes

These probes were run independently of the combined suite to close the former
public-surface and persistent-verification gaps. They created only temporary
clones and temporary evidence outside the evaluated repository.

### Public export and same-phase resume

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python3 /private/tmp/taskplane-r0001-probe.py
```

Observed results:

- Public Design completion export returned `complete/phase-exported`; its
  canonical handoff loaded and verified after a fresh clone.
- Public Plan completion export returned `complete/phase-exported`; its
  canonical handoff loaded and verified after a fresh clone.
- Public Build export with raw caller receipt evidence returned
  `refused/transition-invalid`, detail `Build handoffs are exported only by
  phase submit`; the publication tree did not change.
- Public Build export with a structurally valid lookalike progress receipt
  claiming producer `engine:taskplane.phase-pickup/v1` also returned
  `refused/transition-invalid`; the publication tree did not change.
- Design resume returned `ready/phase-ready`, completed count 1, remaining
  count 1, next obligation `AC2`, and scheduled only `AC2`.
- Plan resume returned `ready/phase-ready`, completed count 1, remaining count
  1, next obligation `AC2`, and scheduled only `AC2`.
- Build resume returned `ready/phase-ready`, completed count 1, remaining count
  1, task `T-002`, and scheduled only `T-002`.
- All three public startup values exposed usable exact work.
- Recursive scans of all three public startup values found no `lease`,
  `contract_bootstrap`, `assignment`, `authoring_result`, or absolute path.
- All three unrelated private homes remained empty.

### Source-advancing authority, real BUILD-C, and cross-phase lineage

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python3 /private/tmp/taskplane-r0001-final-flow-probe.py
```

Observed results:

- A real Design-to-Plan journey bound initial authorization to ancestor commit
  `444002f9a2a23116d6911e885b875100369d99ac` and bound Design approval to the
  later Design/source commit `8cb0427a208792bb4e76391e0f80457ed5e3a4ba`.
- Public Plan pickup from a fresh clone returned `ready`, phase `plan`, exit 0,
  without creating the configured private home.
- A sibling/non-ancestor authority chain returned exit 1 with stable code
  `authority-stale`.
- Public Build pickup returned `ready` with exact task `T-001`, scope
  `taskplane/phase_handoff.py`, contract
  `contract:stateless-phase-pickup`, and proof
  `python3 -m pytest -q taskplane/tests/test_stage_handoff.py`.
- After committing only that scoped authoring diff, public `phase submit`
  returned exit 0, `complete/build-integrated`, and terminal outcome `done`.
- Submission produced a progress receipt from
  `engine:taskplane.phase-pickup/v1` with non-empty checkpoint receipt digest
  and integration receipt fingerprint.
- Submission auto-published a repository-relative terminal handoff; the public
  result contained no lease, bootstrap, assignment, authoring result, checkout
  path, or absolute host path.
- After committing the exported handoff and cloning again, repository-only
  load verified the exact authored commit/tree, successor
  `terminal/terminal-evidence`, and no remaining Build work.
- Mixed Design and Plan green receipts remained preserved in lineage while an
  interrupted Build handoff retained `completed=[]` and `remaining=[AC1]`;
  predecessor-phase receipts did not satisfy successor work.

### Deterministic replay and partial-publication recovery

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python3 /private/tmp/taskplane-r0001-publication-probe.py
```

Observed results:

- First publication status: `published`.
- Exact replay status: `replayed`.
- First publication, replay, and handoff used the same fingerprint.
- Different bytes at the same identity returned `publication-conflict`.
- A simulated atomic link failure returned `publication-conflict`, created no
  destination, and left no temporary publication file.

## Acceptance-criterion disposition

### AC1 — Normal completion exports: MET

Public Design and Plan completion exported and verified after fresh clones.
Public Build completed only through committed authoring plus `phase submit`,
then auto-published a terminal handoff that reloaded from a fresh clone. The
Build publication path is implemented at `taskplane/tp.py:8215-8285`; direct
raw and lookalike export attempts were refused before publication.

### AC2 — Fresh Design continuation: MET

Fresh-clone/empty-home public continuation is covered at
`taskplane/tests/test_stateless_phase_pickup.py:220-251`. The direct Design
resume probe exposed exact producer/scoped contract work, scheduled only the
remaining obligation, leaked no private value, and did not populate the
private home.

### AC3 — Fresh Plan continuation: MET

The public fresh-clone Plan path and source-advancing authority behavior are
covered at `taskplane/tests/test_stateless_phase_pickup.py:178-217` and
`:258-315`. The direct ancestor/later-Design probe succeeded; the direct
non-ancestor probe refused as `authority-stale`.

### AC4 — Fresh Build continuation: MET

Exact Build selection and assignment are enforced at
`taskplane/phase_pickup.py:386-397`. Public output from the direct probe named
only the sealed task id, dependency-ready scope, contract, acceptance, and
proof. Scope-widening is rejected before BUILD-C by
`taskplane/tests/test_build_quality.py:367-390`.

### AC5 — Interrupted same-phase resume: MET

Successor work normalization is at `taskplane/review_evidence.py:158-180`.
The direct Design, Plan, and Build probes each preserved one completed
obligation and scheduled only the one remaining obligation/task. Non-Build
resume coverage is at
`taskplane/tests/test_stage_non_build_handoffs.py:202-228`; Build committed
resume behavior is exercised by the Build-quality and public-flow selectors.

### AC6 — Complete closed lineage: MET

Canonical handoff identity, schema closure, fingerprint, and byte bound are
enforced at `taskplane/phase_handoff.py:1020-1056`. Repository, source,
artifact, receipt, and canonical stored-byte validation are enforced at
`taskplane/phase_handoff.py:1088-1143`. The mutation/effect-counter matrix at
`taskplane/tests/test_stateless_phase_pickup.py:383-435` passed.

### AC7 — Authority remains truthful: MET

Each gate receipt is validated against its own canonical source binding at
`taskplane/design_contract.py:208-238`. Ordered Git ancestry from each
authority commit to the later gate and handoff source is enforced at
`taskplane/phase_handoff.py:1102-1118`. The direct source-advancing journey
succeeded and the sibling/non-ancestor chain failed closed.

### AC8 — Fail closed before effects: MET

Public refusal results explicitly report zero dispatch, authoring, checkpoint,
publication, and integration effects at `taskplane/tp.py:8058-8073`. The
parameterized malformed, tampered, stale, foreign, ambiguous, dirty,
incomplete, artifact, collision, and scope matrix at
`taskplane/tests/test_stateless_phase_pickup.py:383-435` passed with all
downstream counters zero. Direct raw/lookalike Build export and atomic failure
probes confirmed refusal before publication.

### AC9 — BUILD-C is not bypassed: MET

Authoring validation, the required BUILD-C call, checkpoint/integration
evidence validation, and engine-authored progress receipt are at
`taskplane/phase_pickup.py:409-472`. Public committed derivation is at
`taskplane/phase_pickup.py:498-520`. The direct real journey returned
`build-integrated` with genuine checkpoint and integration identifiers, while
the combined suite included the severed-edge and failing-proof cases at
`taskplane/tests/test_pickup.py:417-455`.

### AC10 — v1/v2 compatibility: MET

The exact combined suite included the unchanged legacy pickup, trust-source,
receipt-chain, collision, interrupted-publication, repository-resume,
cold-start, stage-handoff, loop-integration, and Build-quality selectors. All
165 tests passed. The legacy CLI remains on its separate route at
`taskplane/tp.py:8291-8303`.

### AC11 — No hidden-state dependency or leakage: MET

The public startup projection deliberately omits private lease/bootstrap data
at `taskplane/tp.py:8087-8140`. Recursive direct scans of Design, Plan, and
Build public startups found no forbidden key/value or absolute path. The real
Build result scan found no assignment, authoring envelope, private path, or
runtime state. Fresh clones succeeded with empty or uncreated private homes.

### AC12 — Deterministic public contract and recovery: MET

Atomic create-if-absent, exact replay, byte-conflict detection, fsync, and
temporary cleanup are implemented at `taskplane/phase_handoff.py:425-463`.
The direct publication probe proved stable fingerprint replay, deterministic
`replayed` status, named conflict refusal, and clean recovery after simulated
partial publication. Stable safe recovery text is defined at
`taskplane/phase_pickup.py:56-91`.

## Approved Design conformance

### Modules and scope

The approved Design declares existing and new owners at
`design/contract.json:92-120`. All 12 changed production modules are members
of the approved proposed-module graph, and no changed production module falls
outside it. All three approved new modules exist:

- `taskplane/phase_handoff.py`
- `taskplane/phase_pickup.py`
- `taskplane/tests/test_stateless_phase_pickup.py`

No remote transport, replacement stateful loop, synthetic human gate,
replacement BUILD-C engine, scope widening, or legacy pickup removal was
introduced.

### Edges and contracts

All seven named Design contracts at `design/contract.json:122-157` are
realized. The 22 proposed edges at `design/contract.json:292-424` are present
in their approved ownership groups:

- loop/public producer to the single phase-handoff publication owner;
- human-gate authority validation to the handoff consumer;
- public CLI to the phase handoff and pickup coordinators;
- pickup validation and startup/public-result projections;
- post-authoring pickup to existing BUILD-C, checkpoint, and repository owners;
- path-free repository receipt projection;
- predecessor-linked progress receipts back into the one handoff lineage;
- unchanged legacy pickup v1/v2 routing and focused contract verification.

No approved edge is missing and no competing continuation authority was
introduced.

### Depth policy

The implementation conforms to local depth 3 with contract-only boundary
traversal and contract/requirement depth 1 as declared at
`design/contract.json:426-430`. The stateless coordinator depends directly on
the handoff, authority, scoped-view, checkpoint, BUILD-C, and repository
owners; it does not read RunStore, loop state, tracks, workspace locators,
predecessor leases, conversations, or private homes.

### Simplicity and modularity

The implementation introduces the two approved runtime modules, not a second
lifecycle engine:

- `phase_handoff.py` owns the one closed repository schema, canonical
  validation, lineage, artifact verification, and atomic publication.
- `phase_pickup.py` owns exact Build selection, committed authoring derivation,
  and the bridge into the existing BUILD-C boundary.
- Existing Design/Plan startup owners project non-Build work.
- `tp.py` remains a thin public adapter and the incumbent stateful loop remains
  producer-side.

There is no duplicate lifecycle, schema authority, or orchestration engine.
The separation is consistent with the user's requirement for small units and
a simple modular system.

## Final disposition

The implementation at source commit
`444002f9a2a23116d6911e885b875100369d99ac` satisfies all 12 acceptance
criteria and the approved Design Contract. It is suitable for downstream EM
review as a sealed PASS. Later 2.19.1 version or metadata changes are not
covered by this evaluation report.
