# R-0001 Plan — D-0014 zero-lens Evaluate

**Status: drafted for mechanical Plan validation against approved Design
fingerprint `0a29e35a5b6ba341affbca95e123da35f9a84a4a1831d18fec9ae6c3859e257b`
and accepted human drift D-0014.**

## Decision and unchanged boundaries

Human decision D-0014, accepted by `human:vdemkiv`, supersedes only the
Evaluate execution surface of the approved R-0001 Design. Evaluate performs
no lens routing, creates no ReviewKernel lens slots, launches zero Taskplane
lens workers, emits no all-26 disposition ledger, and emits no lens-verdict
output. It collects and judges only the exact diff, bound tests, acceptance
criteria, dependency graph and impact, affected requirements and contracts,
approved Design conformance, provenance, findings, and overall result.

Product and Design remain minimum-sufficient quick routes. Every non-trivial
Plan still runs exactly three or four quick lenses and records all 26
dispositions. Expanded-route authority becomes Plan-only. Build, Fix,
Evaluate, and EM launch zero Taskplane lens workers. No deep lens execution is
authorized. The approved spec and Design artifacts remain immutable; this
Plan records the attributed departure rather than rewriting history. Final EM
must surface `accepted_drift: D-0014` and `accepted_by: human:vdemkiv`.

Root may apply the already-authorized night-mode intermediate gates, commit
scoped work, and push the isolated delivery branch. Root may not merge main,
tag, publish, or release. Final delivery signoff remains human-only.

## Preserved evidence and reanchor

LR-01 through LR-06 remain passed at integration commit
`75c58ad07cf51db53ad3f1f81b259a3fc3ae8108`. Their task objects remain only
for immutable completion evidence and mechanical Design/module/edge/contract
coverage; they receive no dispatch authority.

LR-07 resumes only from `tp/LR-07` commit
`b23100d2547643b8addcad11610e80b850e77a9a`, tree
`9d38678d9fbe27e7b5b8cb3ded60e78ff03aad80`. LR-08 resumes only from
`tp/LR-08` commit `7d2bcfecd1b1bb3751a62b179f31811dbf8ad103`,
tree `91c260ffb9042a7170e8c9779f36d06e576d4b38`. Their passed product work is
preserved; later amendments add only D-0014 truth and never discard or rebuild
those bases.

The old requirement acceptance strings remain verbatim in task criteria for
mechanical traceability. Where AC-LR4, AC-LR5, AC-LR6, AC-LR7, AC-LR8,
AC-LR9, or AC-LR10 names routed Evaluate behavior, D-0014 is the explicit
human-accepted successor contract. Each affected task therefore also carries
`accepted_drift_dod` with executable zero-lens behavior.

## Dependency-aware delivery

The remaining chain is:

`LR-10 Build → integrate exact LR-10 commit → LR-10 zero-lens Evaluate →
(LR-07 ∥ LR-08) → (LR-07 zero-lens Evaluate ∥ LR-08 zero-lens Evaluate) →
LR-09 → LR-09 zero-lens Evaluate → EM accepted-drift synthesis`.

### LR-10 — zero-lens Evaluate runtime and contract

LR-10 is the only new implementation task. It changes the delivery policy,
focused policy boundary, telemetry, expanded authority, ReviewKernel,
evaluation output/evidence, loop gates, runtime guidance, golden Evaluate
brief, and their exact tests as one atomic interface change.

Its DoD is:

- delivery policy classifies Evaluate as zero-lens, and historical approved
  `stage_policy.evaluate.selection=focused` cannot promote it back to routed;
- ReviewKernel takes an early zero-slot Evaluate path before catalog scoring,
  disposition construction, retry/invalidation, provider/adapter access,
  lease creation, dispatch, or result collection;
- expanded-route issuance and consumption are Plan-only;
- Evaluate evidence/output schemas contain no route, disposition ledger,
  slots, leases, lens results, or lens verdicts, while retaining exact direct
  evidence and a fail-closed overall judgment;
- loop gates, runtime guidance, golden briefs, telemetry, and success/failure/
  cancellation/interruption/handoff traces prove zero Evaluate lens starts;
- Product/Design quick routing and Plan 3–4 quick routing with 26 dispositions
  remain green.

### Bounded self-hosting bootstrap

The installed engine predates D-0014 and would otherwise route LR-10
Evaluate. After LR-10 Build passes its single declared pytest command, root
must integrate that exact LR-10 commit into the isolated integration branch
before requesting LR-10 Evaluate. No pre-bootstrap Evaluate lens slot may be
run. The updated engine must then prove all of the following at the integrated
commit: `ReviewKernel slots == []`, native Taskplane lens starts equal zero,
no Evaluate router/retry/provider call occurs, and the direct evidence judge
still checks diff, tests, criteria, graph, requirements/contracts, Design, and
provenance. A different or unbound commit fails closed.

This is a narrowly bounded self-hosting exception, not approval to bypass
Build tests, Evaluate evidence, exact commit binding, or final human signoff.

### LR-07 and LR-08 — current truth from preserved bases

After LR-10 passes its zero-lens Evaluate, LR-07 and LR-08 may amend in
parallel.

- LR-07 updates evaluator, lens, engineering, and orchestrator roles plus the
  Taskplane facade/go/engineering skills. They must say Evaluate is a direct
  evidence collector/judge with zero lens execution and no lens ledger or
  verdict output.
- LR-08 preserves its generator `--check` work and updates the generated
  catalog, routing/knowledge docs, README, and product-truth tests. Current
  truth attributes D-0014 without altering historical specs or Design.

Both tasks retain Product/Design minimum-sufficient quick routes, Plan exactly
three or four quick lenses with 26 dispositions, Plan-only overflow authority,
and Build/Fix/EM zero-lens behavior.

### LR-09 — final conformance

LR-09 joins LR-07 and LR-08. Its one argv-safe pytest command verifies the
runtime, output/evidence, runtime guidance, agent/skill truth, generated
product truth, and integration contract. It must prove zero Evaluate routing,
slots, starts, retries, expansion, lens results, and lens verdict output for
every terminal lifecycle outcome while proving direct evidence judgment still
fails closed. It also binds the exact LR-10 bootstrap commit and requires
final EM drift attribution.

## Quick-only Plan route

This non-trivial Plan executes exactly four quick/light lenses concurrently:
architecture, security, testability, and cost-finops. Architecture owns the
accepted-drift runtime topology and bootstrap order. Security owns exact-commit
bootstrap binding and Plan-only protected expansion. Testability owns the
zero-slot, zero-start, lens-free schema, evidence-judge, and lifecycle matrix.
Cost-finops owns elimination of Evaluate fan-out, leases, retry, and provider
work without reducing direct evidence coverage.

`plan/tasks.json#/plan_route/dispositions` retains exactly one evidenced row
for every catalog lens. Only the four selected `execute_light` rows launch
Plan workers; zero deep workers ran. All four native read-only agents executed
concurrently at quick/light depth and passed with zero findings. Their signed
child identities, verdicts, canonical input fingerprints, and result digests
are bound under `plan/tasks.json#/plan_route/lens_receipts`.

## Graph impact and Design compatibility

The single bounded D-0014 impact call covered 30 exact runtime, role, skill,
documentation, and test paths. It touched eight graph modules, found 25
impacted modules, zero unknowns, and no result or depth truncation. Graph
fingerprint is
`455a46dc0842e2fb45c3a5ff13ad9dc679c49859206e2d8bb7d43f20b838eee6`;
scan-quality fingerprint is
`9e73c6918145b13c8320ce0bdfa7861798fa511c85f8fc9a27bdff359fe73168`;
scanned revision is `3b0054a8f26262a4b363fdf577d0edabc38db7e9`.

The unchanged typed policy is local depth 3, contract-only boundaries,
contract depth 1, and requirement depth 1. Retained tasks still cover every
approved module, all seven contracts/resources, and all 29 historical Design
edges. LR-10 declares no invented Design edge; D-0014 is explicitly recorded
as accepted drift over Evaluate execution and must be surfaced by final EM.

## Risks and stop conditions

- If any Evaluate path constructs a route, disposition ledger, fingerprinted
  lens set, retry set, provider request, lease, slot, or lens result, LR-10
  fails.
- If zero-slot Evaluate skips or weakens diff, bound-test, acceptance, graph,
  requirement/contract, Design, provenance, finding, or final-judgment checks,
  LR-10 fails.
- If the LR-10 Build commit is not the exact commit integrated before its
  Evaluate, the bootstrap fails closed.
- If Product/Design routing, Plan 3–4 quick routing, Plan's 26 dispositions,
  or Build/Fix/EM zero-lens behavior regresses, conformance fails.
- If LR-07 or LR-08 loses prior product work or rewrites historical spec/Design
  truth, reanchor fails.
- If final EM omits D-0014 or its human attribution, delivery cannot reach
  final signoff.
