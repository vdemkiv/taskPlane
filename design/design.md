# Design — focused dynamic lens routing

Requirement: **R-0001 — Focused dynamic lens routing across Product, Design,
Plan, and Evaluate**

## Outcome

Taskplane will keep the 26-lens catalog but stop treating catalog coverage as
catalog execution. A single deterministic policy will produce a complete
stage-specific route: every lens receives one evidenced disposition, while
only the smallest sufficient focused set runs. Product and Design have no
numeric cap beyond minimum sufficiency; non-trivial Plan and Evaluate routes
contain exactly three or four lenses. Build and Fix launch no lens workers.

The policy is synchronous, dependency-neutral, and implemented as a pure
Python module. Existing stage owners assemble typed context, invoke the policy,
persist its closed artifact, and enforce its result. Existing signal scoring,
ReviewKernel leases, native dispatch, contract screening, and artifact storage
remain the incumbents rather than being recreated.

## As-built constraints

The design is a delta against these repository facts:

- `taskplane/graph_primitives.py` already owns deterministic 26-lens signal
  scoring, negative evidence, security/architecture floors, and the current
  eight-deep budget.
- `taskplane/lens.py` turns those scores into `deep`, `light`, and `n/a`
  entries. Its automatic review projection currently selects four or five
  singleton sweep slots and always includes architecture.
- `lenses/catalog.json` has all 26 lenses but only `design`, `build`, and
  `review` stage profiles. It has no canonical Product or Plan profile.
- `taskplane/loop.py` uses legacy strategy routing for Product/Plan, forces a
  solution-design route for Design, primes Execute/Fix, and passes Evaluate to
  ReviewKernel as stage `build`.
- `taskplane/review.py` already seals routing input, complete dispositions,
  scoped evidence views, leases, dispatch slots, results, and provenance. Its
  decision schema currently accepts only `sweep`, `light`, and `n/a` and its
  automatic selection is four or five lenses.
- `taskplane/review_retry.py` can retry previously failed lenses, but it does
  not compare canonical per-lens input fingerprints and therefore cannot prove
  that unchanged evidence remains valid after a Fix.
- `taskplane/delivery_policy.py` and the Plan delivery receipt correctly prove
  zero-lens Build/Fix execution, but currently class Evaluate and EM as the same
  zero-lens execution family. Evaluate must instead run its focused route; EM
  remains synthesis-only.
- `taskplane/dispatch_telemetry.py` already provides immutable dispatch usage
  accounting, but has no closed stage-route record for estimated/actual lens
  cost, runtime, reuse, or invalidation.
- The runtime contract is standard-library-only CPython 3.10+, with separate
  Python 3.14 lint/type validation. New code must remain valid at the 3.10
  syntax floor while being designed and statically checked against Python
  3.14 behavior.

The Design baseline is dependency-graph fingerprint
`7fdc2cc45c225323046430570f04d0580d8868b3ed57132a627a636450adf76f`.
The graph remains read-only during Design.

## Alternatives

### A. Shared pure policy with incumbent adapters — selected

Add `taskplane/lens_route_policy.py` as the one dependency-neutral owner of
canonical context, disposition validation, deterministic ordering, caps,
overflow, and per-lens fingerprints. Keep signal detection in
`graph_primitives.py`; keep stage lifecycle in `loop.py`; keep leased Evaluate
execution in `review.py`; keep signing in `taskplane_lite.py` and telemetry in
`dispatch_telemetry.py`.

This introduces one abstraction at the actual policy boundary and lets every
stage reuse the same invariants without forcing ReviewKernel concerns into
Product or Plan.

### B. Extend each stage independently

Patch Product, Design, Plan, and Evaluate with local selection rules and teach
each artifact gate its own disposition shape. This is initially smaller, but
duplicates canonicalization, cap behavior, mandatory-risk handling, privacy,
and fingerprint semantics. Equal inputs could produce different answers at
different gates, directly contradicting AC-LR8.

### C. Replace the signal engine and ReviewKernel

Build a new end-to-end router, artifact store, dispatcher, and retry engine.
This offers a clean vocabulary but discards mature graph floors, scoped views,
host-observed leases, collection atomicity, and current review provenance. The
migration and regression surface is disproportionate to the requirement.

## Selected architecture

### 1. Canonical stage context

Each routed stage builds `taskplane.lens-route-context/v1`. It contains:

- `stage`, `target`, `policy_version`, and catalog fingerprint;
- fingerprints of the requirement, exact acceptance rows, declared
  constraints, and stage-owned evidence;
- bounded semantic signals and named graph modules/edges;
- stage-specific inputs: Product risks; Design components/interfaces/trust and
  rollback boundaries; Plan tasks/owners/selectors; or Evaluate diff hashes,
  changed files, impact, tests, and unresolved findings;
- a redacted evidence manifest, never raw prompts, raw diffs, secrets,
  workstation identity, or absolute private paths.

Context adapters validate their closed schemas before invoking policy. The
pure policy performs no filesystem, process, network, clock, or global-state
access. Canonical JSON uses sorted keys, stable list ordering where order is
not semantic, finite numbers, and explicit policy/catalog versions.

### 2. One complete route decision

`taskplane.lens-route-policy/v1` contains exactly 26 ordered disposition rows:

- `execute_deep` — run a dedicated deep producer;
- `execute_light` — run the bounded quick producer used by the stage;
- `covered_by` — do not run; identify the selected lens that covers the same
  material risk and cite evidence for the equivalence;
- `not_applicable` — do not run; carry machine-readable negative evidence.

Only the two `execute_*` values may create work. A duplicate/missing catalog
id, unsupported disposition, empty evidence, invalid `covered_by`, coverage
cycle, or selected/disposition mismatch fails closed before dispatch.

Selection first applies security, architecture, solution-design, and other
evidenced mandatory floors. It then groups materially duplicate contributions
by independent risk and selects the highest-ranked representative using score,
floor priority, and lens id as the final stable tie-break. It never invents
work merely to fill Product/Design. For non-trivial Plan/Evaluate it fills to
three only from positively evidenced independent risks and may select a fourth
when its contribution is material. A route with fewer than three is legal only
with a closed trivial-target record and negative evidence for each omitted
slot.

### 3. Stage ownership and execution

| Stage | Context owner | Execution | Gate |
|---|---|---|---|
| Product | Product worker + Product gate | minimum-sufficient focused quick route | canonical spec and 26 dispositions agree |
| Design | Designer + Design gate | minimum-sufficient focused route including solution-design | proposed solution mutations recompute route; Design Contract records evidence |
| Plan | Planner + Plan gate | exactly 3–4 quick lenses for non-trivial plans | task/AC coverage and count are mechanical |
| Build | loop/build delivery policy | zero lens workers | native start/terminal trace proves zero |
| Fix | loop/build delivery policy | zero lens workers | native start/terminal trace proves zero |
| Evaluate | ReviewKernel | exactly 3–4 quick singleton producers per task/wave unless valid reuse applies | sealed route, lease, result, and provenance conservation |
| EM | engineering synthesis | zero new lens workers; consume canonical Evaluate evidence | final conformance and human sign-off |

The current delivery receipt is split semantically: its zero-lens authority
continues to govern Build, Fix, and EM, but cannot zero Evaluate. Evaluate must
carry a valid focused route or stop.

### 4. Overflow and authenticated expansion

If more than four independent mandatory Plan/Evaluate risks remain after
deduplication, policy returns no dispatchable route. It returns either:

1. deterministic split candidates whose evidence scopes and task/AC ownership
   are disjoint; or
2. `expanded_approval_required` naming the target fingerprint, extra lens ids,
   reasons, estimated token cost, and expiry.

`taskplane_lite.py` issues and verifies a signed
`taskplane.expanded-lens-route-action/v1` capability. It is bound to workspace,
stage, target, canonical context fingerprint, exact extra lenses, expected
cost, policy version, expiry, and one action id. The control-plane command can
authorize only that route; it cannot clear contracts, alter mandatory floors,
or weaken screening. Tampering, replay, stale context, or scope mismatch fails
closed.

### 5. Fingerprinted reuse after Fix

Each selected lens has a `lens_input_fingerprint` derived only from its
canonical relevant inputs: applicable AC/spec records, Design edges,
content-hashed changed files, dependency impact, test evidence, unresolved
findings, catalog definition, and policy version. The route also has a complete
`route_fingerprint`.

When Fix finishes, no lens runs. Evaluate rebuilds the canonical context and
compares each newly selected lens with the prior canonical result:

- equal input fingerprint + sealed passing result + valid host provenance =
  `reused`, with no lease or worker;
- changed input, new selection, prior failure, missing result, stale policy, or
  invalid provenance = `invalidated`, with a new lease and worker;
- no selected-input change = all eligible evidence reused and zero duplicate
  lens starts.

ReviewKernel conserves the union of reused evidence and newly collected slots.
It never treats the whole candidate SHA as a reuse key; that would invalidate
everything after any Fix and defeat AC-LR7.

### 6. Persistence and telemetry

Route input, route decision, per-lens fingerprints, approval receipt, reuse
decision, and final usage are immutable content-addressed records written
atomically through existing Taskplane storage primitives. The stage pointer is
updated only after the complete record is durable.

`resource:telemetry.lens-route` records stage, target pseudonym, selected
count, bounded per-lens reason codes, estimated and actual tokens, runtime,
cache reuse, invalidation cause, terminal status, and route fingerprint.
Reasons are capped at 512 UTF-8 bytes, path evidence is repository-relative,
and raw content is represented only by SHA-256 fingerprints. Route selection
must complete under 50 ms p95 for 26 prepared lens rows; end-to-end context
assembly must complete under 500 ms p95 on the frozen repository corpus. A
route artifact is capped at 128 KiB.

### 7. Python/runtime boundaries

The new policy is synchronous: no async task ownership or cancellation
contract is needed. Stage dispatch retains existing cancellation,
interruption, and handoff ownership. The new module has no mutable global
state and no dependency on GIL serialization, so it remains safe under
free-threaded Python when callers provide immutable input values.

Protocols sit at consuming boundaries: stage adapters pass mappings to the
pure policy; trust-boundary JSON is validated at load; signed authority is
validated in `taskplane_lite.py`; ReviewKernel validates stored references and
leases. There are no new runtime or development dependencies. The module is
included by the existing plugin/package scripts and is import-smoke-tested in
a clean packaged artifact. CI retains the Python 3.10 compatibility floor and
runs lint/strict type checks on Python 3.14.

## Rollout and rollback

Delivery is additive and versioned:

1. add the pure policy and exhaustive unit/mutation tests;
2. wire Product/Design/Plan artifacts and gates;
3. wire Evaluate selection and per-lens reuse while retaining read support for
   prior `deep/light/n/a` evidence as non-authoritative migration input;
4. narrow zero-lens enforcement to Build/Fix/EM and prove all terminal paths;
5. add signed overflow authority, telemetry, docs, skills, and agent guidance;
6. run focused selectors, compatibility tests, clean-package smoke, graph
   verification, and the full Taskplane test suite before final review.

No stage silently falls back to the old full-catalog or zero-Evaluate behavior.
If the new policy is unavailable, routed stages stop with
`routing_policy_unavailable`. Before release, rollback is a commit revert. Once
versioned route artifacts exist, rollback restores the previous code reader
but leaves immutable evidence intact and prevents new governed delivery until
an operator selects a compatible policy version; it never rewrites history.

## Known temporary compatibility work

The one-generation reader for legacy `deep/light/n/a` route artifacts is
intentional migration debt. It cannot authorize dispatch or reuse; it only
renders prior history. Remove it after one released compatibility generation
once telemetry shows no legacy reads for two consecutive releases.

## Visualization

`design/visual.html` shows the stage-specific route flow, the overflow refusal,
and the Fix-to-Evaluate selective-reuse loop. These relationships materially
clarify which stages execute lenses and why Build/Fix remain zero-lens.
