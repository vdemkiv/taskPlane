# R-0001 Plan — focused dynamic lens routing

**Status: blocked on Design drift `DD-LR03-PROTECTED-HOST-AUTHORITY`.**

## Outcome and authority

This Plan realizes approved Design fingerprint
`6be9206b3e78138c37ea7557b422d0593f4a217b87dc8584e6b131108fab1525`
for R-0001. It replaces the stale R-0002 remediation plan; it does not reopen
the approved shared-policy design or authorize release work.

Codex native workers implement only scoped Build tasks. Build and Fix launch
zero lens workers on success, failure, cancellation, interruption, and
handoff. Evaluate is a separate decision boundary: every non-trivial task or
wave recomputes and executes three or four focused quick lenses from actual
implementation evidence. Product and Design remain minimum-sufficient focused
quick routes. No push, merge to main, tag, package, publication, release, or CI
wait is part of this Plan.

Canonical security finding `6ea9aff0107f4bb6` proves that LR-03 cannot satisfy
AC-LR5 inside its approved worker-controlled two-file scope. LR-01, LR-02,
LR-04, and LR-05 remain passed and archived; they are not recreated or rerun.
All LR-03 commits and evidence are preserved in place. This amendment records
the Design blocker and does not authorize implementation until Design approves
a real protected boundary. Final human signoff remains the only human approval
after governed delivery resumes.

## Plan route and complete disposition

This non-trivial Plan uses four quick lenses: security, testability,
architecture, and cost-finops. They independently cover authenticated overflow
and privacy, mutation/lifecycle evidence, the 23-edge adapter topology, and the
fan-out/token/reuse objective. `plan/tasks.json#/plan_route` records all 26
catalog dispositions in catalog order. Non-selected lenses are either covered
by one of those four with concrete evidence or carry machine-readable negative
evidence; no full-catalog execution is implied.

The selected route maps every AC-LR1..10 criterion to concrete tasks. More
than four independent mandatory risks did not remain after grouping: privacy
and authority share the security boundary; implementation quality and QA share
the testability proof; stage integration, runtime behavior, and operational
lifecycle share the architecture boundary; delivery speed and worker fan-out
share the cost boundary. If implementation evidence later separates any of
those risks, Evaluate must split scope or stop for an exact authenticated
expanded-route capability. It may not silently discard a mandatory lens.

## Graph and Design coverage

The single bounded replan impact query covered all 23 proposed module
surfaces. It reported 19 impacted modules, no unknown modules, complete scan
quality, and current graph content fingerprint
`357ad892013154c667251f14ff9733bd59873367a438ffa3883cc08397929ddf` at
scanned implementation revision `4031515ece2080897e863c6f9bc096115b370f9e`.
Neither the result ceiling nor dependency depth was truncated. This is the
post-LR-02 replan snapshot; the approved Design baseline remains
`7fdc2cc45c225323046430570f04d0580d8868b3ed57132a627a636450adf76f`.
Every task retains the approved typed policy: local depth 3, contract-only
boundaries, contract depth 1, and requirement depth 1.

The task set covers every existing Design module plus new
`taskplane/lens_route_policy.py`, all six named contracts/resources, and every
one of the 23 proposed edges exactly once. The current dependency graph maps
exact existing `taskplane/*.py` file scopes to the aggregate `taskplane`
module, while the approved Design names nine of those files as exact module
ids. Their owning tasks therefore repeat those exact ids in `new_modules` as
gate-compatibility coverage declarations; this does not classify the existing
files as newly created and does not change their scope or owner. LR-01 remains
the only creator of a genuinely new module. The bounded impact query reported
no unknown module. Any implementation need outside these modules or any
undeclared cross-boundary edge is Design drift and returns to Design rather
than widening a Build scope.

## Protected authority Design blocker

The required single bounded authority-replan impact query covered
`taskplane/taskplane_lite.py`, `taskplane/tp.py`,
`taskplane/tests/test_expanded_lens_route_authority.py`, `hooks`, and `.codex`.
It reported 28 impacted modules, no unknown modules, no external or boundary
nodes, no result/depth truncation, graph fingerprint
`431ddff0d899c3b6122cf2d005f3065148e0f2aaeb919e562c7da5a2fca56739`, and
scanned revision `e5183c36da43a3c945adfbc39d329b0b127cf107`. The query retained the
approved depth-3, contract-only policy. That evidence proves the repository
graph has no protected host/control-plane provider available to this Plan.

`taskplane/taskplane_lite.py` is an approved module, but its source and Python
interpreter are both worker-controlled. A worker can therefore monkeypatch its
locator, clock, RSA verification, issuance, and consumption helpers. The other
examined candidates—`taskplane/tp.py`, `hooks`, and `.codex`—are likewise in
the worktree and are not approved feature modules. Reusing any of them as a
nominal authority would not create isolation.

Design must now name the smallest genuine host/control-plane provider outside
the worker-controlled checkout and interpreter, and revise the graph so that
provider—not `taskplane/taskplane_lite.py`—provides
`contract:authority.expanded-lens-route`. The Design amendment must also name:

- a protected source locator outside the worker workspace that fails closed
  when missing, altered, relocated, or symlinked;
- 0600 custody outside that workspace;
- host-owned issuance and an authoritative host clock;
- exact RSA verification and atomic one-use consumption; and
- a worker adapter that can request and consume only the host decision, without
  supplying or replacing authority inputs.

No host module or new Design edge is invented in this Plan. LR-03 keeps its
approved two-file scope and is explicitly blocked until the Design contract
names the protected provider, resource, and edges. Its existing commits
`8af3d8e`, `03a6c8f`, `3736e7c`, `000704a`, and `76dc79d`, plus all associated
evidence, remain retained for the later in-place amendment.

## Dependency-aware delivery

### Foundation

Run LR-01 first. It introduces the dependency-neutral pure policy, canonical
serialization, complete disposition validation, deterministic ordering,
mandatory floors, overflow result, and route/per-lens fingerprints. Its focused
tests own the 26-row conservation and equal-input determinism invariants.

### Parallel adapters

After LR-01 passes, dispatch LR-02, LR-04, and LR-05 together. Their production
and test scopes are pairwise disjoint. LR-03 remains scope-disjoint but is now
dependency-sequenced after LR-02 because its Evaluate path consumes the
canonical dependency-impact route:

- LR-02 adapts ReviewKernel and Fix-to-Evaluate reuse. It recomputes a three-or-
  four-lens Evaluate route, normalizes the ordinary integer-depth dependency
  impact at that adapter boundary, and leases only invalidated/new evidence.
- LR-03 is blocked by `DD-LR03-PROTECTED-HOST-AUTHORITY`. After Design names
  the protected provider, it amends the preserved exact
  target/context/lens/cost/expiry/policy capability in place and retains its
  tamper/replay matrix. Its Evaluate gate still waits for LR-02 so the canonical
  dependency-impact route is available.
- LR-04 persists bounded redacted terminal route telemetry for every lifecycle
  outcome.
- LR-05 narrows zero-lens delivery enforcement to Build, Fix, and EM while
  keeping Evaluate eligible for its focused route.

Each task runs one focused selector. Build and Fix remain zero-lens throughout;
these are implementation workers, not review workers.

### Stage integration

LR-06 waits for all four adapters because `taskplane/loop.py` is the shared
composition root. It wires fresh Product, Design, and Plan contexts, enforces
minimum-sufficient Product/Design routes, proves Plan accepts only three/four
for non-trivial targets, connects overflow authority, and confines lens starts
to Product, Design, Plan, and Evaluate. Keeping the loop edit in one join
prevents overlapping ready workers from racing on lifecycle authority.
Because LR-06 depends on LR-03, it and all later tasks remain blocked until the
Design amendment is approved and LR-03 passes. Their task topology and scopes
are unchanged.

### Parallel truth surfaces

After LR-06, LR-07 and LR-08 run together:

- LR-07 updates the exact agent and skill contracts so Product/Design use
  minimum-sufficient quick routes, Plan/Evaluate use three or four quick
  lenses, and Build/Fix never spawn lenses.
- LR-08 keeps Product, approved Design, this Plan, documentation, onboarding,
  catalog guidance, configuration, and README truthful about dispositions,
  overflow, reuse, telemetry, and the separate explicit all-deep audit.

Their scopes are disjoint. Approved Design decisions remain immutable; LR-08
may make only truth-preserving artifact adjustments required by the realized
implementation and must return to Design if behavior would drift.

### Conformance

LR-09 waits for both truth tasks and adds the integrated proof. It runs the
focused selector set once, verifies all ten exact acceptance strings and all
six contracts at one candidate, checks Build/Fix zero-lens traces for every
terminal path, and verifies fresh Evaluate routing plus conservative reuse.
The independent Evaluate phase then routes three or four quick lenses from the
actual diff and test evidence; it does not inherit this Plan's selected route.

## Regression ownership

- LR-01: closed 26-row dispositions and canonical determinism.
- LR-02: Evaluate three/four routing, ordinary integer-depth dependency-impact
  normalization, and single/multiple/unchanged Fix invalidation.
- LR-03: overflow refusal, exact authorization, tamper, replay, expiry, and
  target/context mismatch; worker monkeypatch attempts cannot replace host
  issuance, clock, exact RSA verification, locator, or one-use consumption;
  missing, non-0600, symlinked, relocated, or altered locator/custody state
  fails closed with zero issuance or consumption.
- LR-04: terminal telemetry completeness, 512-byte reasons, 128-KiB artifacts,
  redaction, tokens, runtime, reuse, and invalidation.
- LR-05: zero Build/Fix lens starts on success, failure, cancellation,
  interruption, and handoff, plus positive Evaluate eligibility.
- LR-06: Product/Design minimum sufficiency, Plan 3/4 acceptance and 2/5
  refusal, stage isolation, and complete route conservation.
- LR-07/LR-08: machine-checked agent, skill, spec, Design, Plan, docs, and
  README truth.
- LR-09: exact integrated AC-LR1..10 conformance and compatibility behavior.

## Risks and stop conditions

- A stage adapter may normalize semantically equal evidence differently. Equal
  bytes, key-order mutation, relevant-input mutation, and policy-version tests
  block the owning task.
- Deduplication may hide independent mandatory risks. The five-risk mutation
  must produce a deterministic split or zero dispatch pending exact authority.
- Reuse may accept stale evidence. Missing provenance, prior failure, policy or
  catalog change, and any relevant-input uncertainty invalidate that lens.
- Expansion authority may become a general bypass. The capability cannot clear
  contracts or override floors; any field mutation, replay, or stale target
  blocks dispatch.
- Delivery zero-lens enforcement may suppress Evaluate or allow Build/Fix
  fan-out. Positive Evaluate and negative five-outcome Build/Fix traces are one
  coupled gate.
- Telemetry may leak private content. Absolute paths, secrets, workstation
  identity, prompts, and raw diffs are refused or redacted before persistence.
- Legacy `deep/light/n/a` history is read-only compatibility input. It never
  authorizes new dispatch or reuse and remains tracked debt for removal after
  two compatible released generations.

Any scope overlap inside a ready set, failed selector, missing catalog row,
unplanned graph edge, unbounded reason/artifact, unauthenticated expansion,
new runtime dependency, release-surface edit, or need to weaken a mandatory
floor stops the owning task and returns to the orchestrator.
