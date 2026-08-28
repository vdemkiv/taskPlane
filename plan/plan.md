# R-0002 Plan — remediate all 72 deep-EM findings

## Outcome and authority

This Plan realizes approved Design fingerprint
`ac5709a4a381d44556318207d8f9a34c98d9bdd69ff1fe74d7408a9f9505d8fe`
at reviewed source `00cd4f2c8183e57b6eae3f0cb6b0c580e00fe085`. It preserves the
canonical inventory of 34 high, 28 medium, and 10 low findings from
`.em-review/findings.json`, with one Build owner per finding and independent
high/final evaluation.

Codex native subagents are the only execution workers. Taskplane governs
dependency classification, scoped contracts, evidence checks, and gates.
Build, Fix, Evaluate, and execution-time EM use zero Taskplane lens workers.
For each ready set the orchestrator dispatches all pairwise-disjoint owners in
parallel with exact emitted identities, performs one event-driven wait for the
outstanding set, and merges only green task commits. No implementation, push,
tag, publication, release, PR merge, or `origin/main` mutation is authorized by
this Plan.

## Graph and boundary evidence

The required single bounded replan impact query covered the corrected H1-A
production-composition seam: `taskplane/loop.py`,
`taskplane/terminal_truth.py`, `taskplane/delivery_ports.py`, and
`taskplane/tests/test_em_h1_terminal.py`.
It reported 30 impacted modules and no unknown modules. The graph content
fingerprint is
`f5e97aa40e7a3566039da309b849050fb96446ea142412bf1c32f140164e8fca`
at the reviewed SHA. Its result ceiling was reached, but depth was not
truncated and scan quality was complete. The result confirms that the shipped
`taskplane.loop.retro` terminal transition caller and its terminal
implementation/test boundary are known graph surfaces; no new module or Design
edge is needed.

Every task uses the approved typed impact policy: local depth 3,
contract-only boundaries, contract depth 1, and requirement depth 1. The three
approved code modules are declared at their creating owners:
`taskplane/remediation_trace.py` at `H1-I`, `taskplane/glob_match.py` at
`HX-GRAPH`, and `taskplane/text_runtime.py` at `M2-A`. Plan DoR additionally
classifies `taskplane/locales` as new at `M2-A` and the remediation-evidence
surface `.em-review/remediation` as new at `HG-EVAL`, `FINAL-I`, and
`FINAL-EVAL`. The 23 approved Design edges are allocated exactly once across
their canonical owners.

## Delivery waves

### H1 — integrity and authority foundation

Dispatch `H1-A` through `H1-E` together. Their code, test, and interface scopes
are pairwise disjoint:

- `H1-A`: terminal composition through the shipped `taskplane.loop.retro`
  caller, restart
  authority, immutable publication, and exclusive CAS successor.
- `H1-B`: stage/journal atomicity and durable observation intent.
- `H1-C`: fsync-before-acknowledgement, migration authority, and read-only
  screening.
- `H1-D`: release authority plus current/N-1 compatibility.
- `H1-E`: bounded validation-sandbox process and preparation deadlines.

After all five receipts are green, `H1-I` alone creates the remediation-trace
boundary and proves the H1 contracts at one SHA. No aggregate suite runs here.

### H2 and H3 — concurrent remaining-high work

After `H1-I`, dispatch one combined ready set containing `H2-A`, `H2-B`,
`H2-C`, `HX-GRAPH`, `H3-A`, `H3-C`, and `H3-D`. These scopes are disjoint.
`HX-GRAPH` is deliberately the single cross-wave owner for `components.yaml`,
graph interaction, routing, and the shared dependency-neutral glob matcher.

- H2 closes CI quality enforcement, production reachability, preview/review
  bounds, architecture-map consumption, and native usage truth. `L-02` stays
  with `HX-GRAPH`; it has no separate low lane.
- H3 closes dashboard and graph accessibility, truthful bridge/fallback state,
  privacy retention/minimization, and exact-SHA terminal export. `L-01` stays
  with dashboard owner `H3-A`; `L-04` stays with privacy owner `H3-C`.

`H2-I` waits for H2 owners plus `HX-GRAPH`. `H3-I` waits for H3 owners plus
`HX-GRAPH`. The two integration tasks are pairwise disjoint and may run
concurrently once their own dependency sets are green.

### Independent high closure gate

`HG-EVAL` is a fresh independent evaluator after both high integrations. It
must prove exactly 34 unique high results at one clean candidate SHA. Missing,
open, suppressed, downgraded, duplicated, wrong-SHA, or Build-self-attested
rows fail closed. No M1 or M2 task is eligible before this gate passes.
Its executable check consumes the H2/H3 integration selectors produced by its
direct predecessors and invokes `taskplane.remediation_trace verify-high`
against the evaluator-owned high-gate result set. It does not consume the
downstream `FINAL-I` integration test artifact.

### M1 and M2 — concurrent medium work with low companions

After `HG-EVAL`, dispatch `M1-A` through `M1-F`, `M2-A` through `M2-E`, and
`MX-DOCS-ARCH` in one pairwise-disjoint native ready set. `MX-DOCS-ARCH` is
the sole owner of `docs/loop-design.md`; `M2-C` owns the remaining product and
documentation surfaces.

M1 owns engineering foundations: scanner/design decisions, typing and
fail-closed cost behavior, CI/dependency integrity, mandatory production
proofs, repository retries, and scoped test/runtime bindings. `L-06` shares
the CI/dependency owner `M1-C`; `L-10` shares scoped runtime owner `M1-F`.

M2 owns user-facing truth: dashboard locale/state, privacy defaults/notices,
product/docs/help, deterministic concurrency, and priced debt traceability.
`L-03` shares dashboard/text owner `M2-A`; `L-05`, `L-07`, `L-08`, and `L-09`
share product/docs owner `M2-C`.

`M1-I` and `M2-I` are independent joins and may run concurrently after their
respective leaves. There is no low-only tail.

### Final integration and evaluation

`FINAL-I` runs after both medium joins. It owns the tight `specs/spec.md`
surface required by the approved
`specs->resource:review.finding-traceability:provides` edge and reconciles the
immutable 72-row map, graph and contract edges, focused receipts, high-gate
evidence, and exact candidate SHA. Its focused command runs only the AC1 and
AC8 integration selectors.

`FINAL-EVAL` then independently checks all 72 dispositions at the exact clean
candidate and runs `python3 -m pytest taskplane/tests -q` exactly once. Earlier
tasks run only their focused selectors, so the complete suite is not repeated
while parallel edits are in flight.

## Shared owners and serialization barriers

- `H1-I` owns the new remediation-trace foundation after all five H1 leaves.
- `H1-A` owns `taskplane/loop.py` for the shipped terminal-truth composition
  correction; later H3 privacy/retention edits to that same caller remain
  serialized through the existing `H1-A` → `H1-I` → `H3-C` dependency path.
- `HX-GRAPH` owns every H2/H3 graph, architecture-map, graph-keyboard, routing,
  and glob-matcher edit.
- `H2-I` and `H3-I` own only their disjoint integration tests.
- `HG-EVAL` is the only high-closure authority and never edits product code.
- `MX-DOCS-ARCH` owns `docs/loop-design.md` across M1/M2.
- `M1-I` and `M2-I` own only their disjoint integration tests.
- `FINAL-I` is the only final remediation-trace/integration owner.
- `FINAL-EVAL` is independent and writes only final evidence.

Known repeated production paths are serialized by dependencies: `review.py`
H1→H2; `taskplane_lite.py` H1→H3→M2; CI H2→M1; dashboard H3→M2; graph/lens
H2/H3→M1; and remediation trace H1→high gate→M2→final. A task whose actual
implementation would need another ready owner's file, fixture, schema,
composition root, or public contract stops for reclassification instead of
widening scope.

## Verification and merge discipline

Each Build task carries one runnable focused command and owns its test changes
in the same task commit. Validation progresses from exact selectors to wave
integration. A failure is classified before edits; a fixture/test correction
reruns only its exact selector unless it can affect a wider boundary. The
orchestrator merges a task only after independent Evaluate is green and binds
the receipt to its exact commit SHA. The complete suite is reserved for
`FINAL-EVAL` on the integrated clean candidate.

## Risks and stop conditions

- A broad owner can become a bottleneck. The Plan contains it with leaf-only
  scopes and explicit join owners rather than concurrent edits to broad files.
- Low companions can distract from high closure. They share the earliest
  related owner and never create a gating low-only lane.
- Persistence and migration fixes can become one-way. H1 requires additive
  current/N-1 readers, prepare/commit markers, fault injection, and predecessor
  or one-successor recovery before dependent work.
- Production wiring can introduce cycles. Composition roots depend inward on
  protocols; graph/import/SCC checks gate joins.
- Bounds can reject legitimate large repositories. Refusal remains typed and
  measurable; only an explicit human-authorized bounded override may proceed.
- Dashboard and docs can drift. One owner controls each shared surface and
  parity/generated-content checks protect the join.
- Any inventory mismatch, unplanned graph edge, scope overlap in a ready set,
  failed high mutation, unresolved finding, mixed SHA, or repeated final full
  suite blocks progression and returns to the owning task or human gate.
