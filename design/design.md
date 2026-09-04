# Stateless phase pickup from sealed repository handoffs

Requirement: R-0001

## Decision

Add a repository-native `taskplane.stage-handoff/v2` and a stateless phase
pickup coordinator. Normal phase completion publishes one closed v2 handoff;
an interrupted Design, Plan, or Build publishes the same contract with an
`interrupted` producer outcome and a same-phase target. The consumer verifies
the complete repository, source, artifact, authority, task, acceptance, and
receipt lineage before it creates a fresh attempt contract or performs any
other effect.

The v2 handoff is an evolution of the existing bounded stage-handoff concept,
not a serialized loop and not a second lifecycle authority. It carries only
canonical values and repository artifact references. It never carries a run
store, locator, claim, predecessor lease, conversation, worker, tool transcript,
host path, secret, or mutable runtime object. The normal stateful loop remains
the producer-side lifecycle; the handoff becomes the sole continuation input
after export.

The implementation is synchronous. Publication and validation are bounded
filesystem and Git operations, so an asynchronous API would add cancellation
and ownership complexity without useful concurrency. New code must remain
compatible with the repository's Python 3.10 runtime floor and pass the
separately approved Python 3.14 validation lane, strict type checking, clean
package installation, and import/package edge verification.

## Current system and the missing edges

- `taskplane/stage_handoff.py` already defines a closed, content-addressed,
  64-KiB `taskplane.stage-handoff/v1`, but its artifact resolution begins at
  `canonical_artifact_store()`, which loads a workspace locator and private
  `ArtifactStore`. It is suitable inside a live stage run, not after a fresh
  clone with an empty private home.
- `taskplane/stage_entities.py`, `taskplane/loop.py`, and
  `taskplane/taskplane_lite.py` already create bounded stage startup envelopes,
  fresh resume claims, terminal outcomes, and exact scope projections. Those
  semantics should be projected into the new repository contract rather than
  replaced, but their RunStore and locator reads must stay producer-side.
- `taskplane/pickup.py` provides the compatibility surface for approved shelf
  contracts and pickup receipts v1/v2. It validates a clean checkout, source
  lineage, operator trust, collision-free receipt chains, and then calls
  `build_c.run_pickup`. It is intentionally Build-only and its receipt and merge
  evidence include legacy shapes that cannot express Design or Plan.
- `taskplane/build_c.py` gives the current shelf pickup exact scoped checkpoint
  and repository integration, but `run_pickup` starts at checkpoint execution.
  The successor Build journey therefore must dispatch exact scoped authoring
  first and call BUILD-C only after an engine-validated authoring result.
- `taskplane/repository.py` owns the integration boundary, although the legacy
  pickup merge receipt includes absolute checkout paths. The successor public
  result must use a path-free repository receipt projection while preserving
  the full local receipt only inside the boundary that validates it.
- `taskplane/design_contract.py` and `taskplane/loop.py` already fingerprint
  Design evidence and record Design/Plan approvals. Portable export must bind
  those actual gate subjects and actors. It must reject `(unattributed)`, stale,
  foreign, replayed, or mechanically invented human authority.

The captured baseline is healthy at revision
`9562e1b8d6d6e5c8ccdb43cecf76a68c49d8d510`, graph fingerprint
`1c63d34f0d191e21c84151405d48f43bf4553d1e5863f81078b4a83e1db9afe3`,
with 57 modules and 171 edges. The bounded impact reaches `taskplane`, tests,
docs, and specs to local depth three; cross-entity traversal stops at named
contracts.

## Current Design-routing defect

The focused route selected and ordered 16 quick workers, but every
`design_lens_dispatches` entry supplied only `brief`, `contract`,
`dispatch_intent`, lens/model/output metadata, a role marker/reference, and a
task identity. It supplied no per-lens `contract_bootstrap`,
`producer_contract`, lease, scoped view, result schema, or reference to the
full immutable envelope. The lens workers therefore safely refused instead of
inventing authority or reading unbounded context. No refused worker output is
treated as evidence here.

The designer performed the mandatory solution-design check directly and marks
it self-attested in the contract so the human approval gate can surface that
fact. The contract also preserves all 26 focused-route dispositions and their
positive or negative evidence. A later implementation must correct worker
dispatches by issuing a fresh attempt-local bootstrap, producer contract,
lease, scoped view, closed result schema, and full-envelope reference after the
handoff has passed stateless validation. None of those mutable attempt values
may be exported from the predecessor.

## Handoff and artifact contracts

`taskplane.stage-handoff/v2` is a closed canonical JSON value capped at 64 KiB.
Its fields are:

- a deterministic handoff identity and canonical fingerprint;
- repository identity plus the exact producer source commit and tree;
- requirement identity, fingerprint, and artifact reference;
- applicable Design and Plan fingerprints and artifact references;
- producer phase and outcome, successor phase and mode;
- the complete ordered obligation set and completed/remaining partition;
- ordered Build tasks with exact scopes, dependencies, contracts, acceptance
  obligations, and proof commands (empty only before Plan exists);
- named contract relations and the exact acceptance map;
- selected repository artifacts, gate-authority receipts, progress receipts,
  predecessor handoff fingerprint, and receipt-chain head; and
- explicit exclusions for every forbidden hidden/runtime class.

A `taskplane.repository-artifact-reference/v1` contains only kind, SHA-256
digest, byte count, media type, repository destination, and a deterministic
`repo-artifact://sha256/<digest>` locator. The blob lives at the fixed
repository path `exports/pickup/artifacts/sha256/<digest>`; the locator contains
no host path. Export rejects unsafe destinations, symlinks, untracked inputs,
digest mismatch, duplicate references, more than 64 references, or a combined
manifest over the limit.

The handoff identity is the fingerprint of repository identity, producer source
commit/tree, requirement fingerprint, producer phase/outcome, successor
phase/mode, and predecessor lineage head. The canonical storage path is
`exports/pickup/phases/<handoff-id>/handoff.json`. Exact bytes at that identity
are an idempotent replay; different bytes at the same identity are
`publication-conflict`. Publication uses a same-directory temporary file,
flush/fsync, no-follow checks, and create-if-absent semantics. The repository
owner records only exact declared export paths. A clean export commit may be
shared and verified by a fresh clone; the manifest continues to name the
pre-publication source commit/tree to avoid self-reference.

Human gate evidence uses `taskplane.human-gate-receipt/v1`. Each receipt names
the real gate (`initial-authorization`, `design-approval`, or `plan-approval`),
the `human:<identity>` actor and context, exact subject fingerprint, source and
repository identity, decision, predecessor authority fingerprint, and its own
fingerprint. It records `cryptographic_authenticity_claimed: false` unless an
independently verifiable repository public-key proof is actually present.
Content hashing must never be presented as actor authentication. Mechanical
progress uses `taskplane.phase-progress-receipt/v1` with an engine producer and
never a human actor.

## Phase journeys

### Requirement to Design

An approved requirement/spec export contains the complete R-0001 record,
contracts, acceptance obligations, source identity, selected spec artifact,
and attributable initial authorization. Stateless pickup validates those bytes
and returns a Design dispatch whose producer contract is read-only toward
product code with `design/**` write authority. It reads no loop, RunStore,
track, claim, locator, lease, conversation, or predecessor runtime state.

### Design to Plan

Design completion occurs only after the existing mechanical Design DoD and the
actual human Design approval. Export binds the Design content/evidence
fingerprint and approval receipt. Plan pickup refuses before dispatch if the
Design artifact or approval is absent, stale, foreign, reused for another
subject, or no longer matches the handoff. The Plan producer contract is
read-only toward product code with `plan/**` write authority and carries the
full contract/acceptance map.

### Plan to Build

Plan completion exports the attributed Plan approval and the canonical ordered
tasks. Build pickup chooses only tasks whose declared dependencies have green
progress receipts. Its dispatch contains one exact task scope, named contracts,
acceptance references, and proof commands. It also contains a fresh
attempt-local `contract_bootstrap`, `producer_contract`, lease, scoped view,
closed result schema, and full-envelope reference. A caller-requested scope,
task order, dependency, contract, or proof change is rejected before dispatch.

The worker authors code under that contract and returns an engine-validatable
authoring result. Only then may `taskplane/phase_pickup.py` call the existing
`build_c.run_pickup`/checkpoint/repository path. A green BUILD-C checkpoint and
repository integration receipt are mandatory inputs to the progress receipt;
removing that edge or failing focused proof prevents task completion and the
next task from becoming ready.

### Interrupted same-phase resume

Every accepted phase result creates one repository-resident progress receipt
whose digest points to the prior receipt digest. An interrupted export has
`producer.outcome=interrupted`, `successor.mode=same-phase-resume`, and an equal
producer/successor phase. A new checkout verifies the chain, preserves the
completed/remaining partition, creates a fresh attempt-local lease, and
schedules only remaining obligations. Predecessor attempts and leases are
never reopened or imported. A `done` producer can only feed the next phase (or
Build terminal evidence), never same-phase resume.

## Fail-closed order and public results

The successor consumer uses one fixed pre-effect order: input size/JSON/schema,
canonical fingerprint and ordering, repository/source/clean checkout, selected
artifact integrity, receipt lineage and replay conflict, gate authority,
phase-transition rules, then scope/dependency/proof closure. Only after all
checks pass may it publish a receipt, issue an attempt contract, dispatch,
author, checkpoint, or integrate.

Refusals use stable codes (`handoff-malformed`, `handoff-integrity`,
`repository-foreign`, `source-stale`, `checkout-dirty`, `artifact-integrity`,
`receipt-lineage`, `authority-missing`, `authority-stale`,
`transition-invalid`, `scope-widened`, `dependency-unmet`, `proof-invalid`, or
`publication-conflict`) and a safe recovery that re-exports or restores exact
evidence without trusting a new source or widening scope. Public success and
refusal values contain the handoff fingerprint, source identity, phase/mode,
lineage status, and path-free receipts only.

## Alternatives

1. Selected: evolve the stage handoff and add a pure stateless coordinator.
   This reuses the current stage, artifact, authority, and BUILD-C boundaries,
   provides one phase contract, and isolates repository-only validation from
   private lifecycle state. It costs a v2 schema, repository artifact store,
   and explicit producer adapters.
2. Bundle the RunStore/stage aggregate and import it in the successor. This is
   smaller at the transition call sites and preserves all current fields, but
   it violates the core requirement by copying locator, lease, run identity,
   and predecessor lifecycle authority. It also turns mutable private state
   into portable truth and cannot pass empty-home tests.
3. Stretch the legacy approved-shelf pickup v1/v2 format across all phases.
   This keeps one old command and less new schema code, but its model is one
   Design element and one acceptance criterion at a time; it lacks Plan task
   ordering, phase outcomes, Design/Plan approval lineage, and author-before-
   checkpoint behavior. Adding those fields would silently reinterpret legacy
   receipts and their refusal order.

The selected approach should be revisited only if Taskplane adopts an external
artifact registry or cross-repository trust service. At that point the
repository artifact resolver can be replaced behind the same digest/reference
contract without changing phase semantics.

## Rollout and rollback

Ship in three slices: first the pure v2 schema, canonical repository artifact
store, negative fixtures, and manual public export/pickup; second the
Design/Plan/Build producer adapters and same-phase receipts; third the normal
loop completion hooks and Build authoring-to-BUILD-C bridge. Each slice keeps
legacy `tp.py pickup <approved-design>` and pickup receipt v1/v2 byte and refusal
semantics unchanged.

Abort rollout on any legacy suite regression, nonzero hidden-state read,
pre-effect counter increment on a refused fixture, authority mismatch accepted,
or BUILD-C severed-edge completion. Roll back by disabling/removing the normal
v2 producer hook and reverting its exact export commit. Previously published v2
artifacts remain immutable audit evidence; older releases ignore them, and no
legacy artifact is rewritten or downgraded. Consumption of v2 remains fail-
closed if the implementation is absent.

## Validation and observability

The planned `test_stateless_phase_pickup.py` owns parameterized fresh-clone,
empty-home, all-phase, schema mutation, authority, effect-counter, deterministic
replay, collision, and same-phase crash journeys. Existing pickup, cold-start,
stage-handoff, stage-loop, and BUILD-C tests remain the compatibility floor.
Focused proof must include a semantic severed edge from phase pickup to
BUILD-C, not a source-shape assertion.

Machine-readable results expose only stable boundary code, phase, mode,
handoff/source fingerprints, receipt-chain head, next eligible obligation, and
whether BUILD-C checkpoint and integration receipts verified. Alerts are not a
runtime service concern for this local synchronous feature; CI fails on any
refusal-order drift, hidden-state access, nonzero pre-effect counter, artifact
leak, or compatibility regression.

No separate visual is created. The core decision is a linear trust sequence
with one Build-only branch, and the explicit phase sections plus proposed graph
are clearer than an additional diagram; only `design/design.md` and
`design/contract.json` are authored by this contract.
