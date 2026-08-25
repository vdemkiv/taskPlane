# R-0001 Plan — contract-first corrective delivery

## Authority and readiness

This Plan is bound to exact source `ecfc48ec2f5f4c25dd0d9bab4d6751bc2f130845`, approved Design content fingerprint `550aaeaacf8993baa53b2211a1c8d29b74fd143d1878704c30e1acfdfef08c03`, and graph `a6a3c1e72c0c268648e3727cdcec904f60c41442a1f77bf16231bbdb84cd90a6` (50 modules, 156 edges; complete and not degraded). The exact 16-commit CI repair through `ecfc48e` is integrated inventory and is not replayed. Before Build, the already-approved dirty Design artifacts and these Plan artifacts must be committed as governance evidence so execution starts from a clean exact SHA; no task below implements or rewrites that governance.

The single bounded impact call covered the approved owner, adapter, test, schema, documentation, package, manifest, and workflow paths. It returned the same graph fingerprint, depth 3 without truncation, and only `(root)` plus `design/schemas` as unknown identities; both are declared in `new_modules`. Every task uses the approved depth policy: local 3, contract-only boundary, contract 1, requirement 1.

## Delivery DAG

There are 20 agent tasks in 12 serial waves. Tasks in each brace dispatch simultaneously by direct assignment; every other pair is serialized by the named phase/barrier dependency. This rule classifies all 190 unordered task pairs: a pair is `parallel` only when it is in the same brace with disjoint scopes, otherwise it is `serialized` by the earliest named owner/phase barrier on its dependency path.

1. `{T01}` public injected ports and hermetic evidence-store harness.
2. `{T02, T03, T04}` disjoint A2 owners: mode/empty collection, human-only unstarted ReviewKernel rebind, and producer observation.
3. `{T05}` `review-kernel-owner` + `evaluation-output-owner` + `loop-transition-owner` adapter barrier; produces feature-green only.
4. `{T06, T07, T08}` disjoint forward-repair lanes: docs/security, fetched-default repository preparation, and release/compatibility authority.
5. `{T09}` `release-surface-owner` integration for v2.17.21 docs, manifests, packages, and CI. v2.17.20 and graph revision `2757822e` remain unchanged historical evidence.
6. `{T10}` wiring-closure owner.
7. `{T11}` `design-validation-owner` checkpoint/Design adapters.
8. `{T12, T13, T14}` disjoint delta-brief, pair-topology, and dispatch-telemetry owners.
9. `{T15}` `loop-transition-owner`, `runtime-event-owner`, and `retro-owner` integration. The incumbent 1,800-second event wait remains event-driven; immediate terminal wake, 256 pending events, 64 KiB event cap, idempotent reconciliation, partial-host attention, atomic admission, durable overflow, and immutable execution-DAG rules are mandatory.
10. `{T16}` same-SHA fresh-checkout pickup measurement; `<120s` is required before R-0013 may resume.
11. `{T17a, T17b, T17c}` disjoint verification fan-out for A2/live-host, release/wiring/repository, and performance/events.
12. `{T18}` one terminal full matrix and realized graph/W01-W32 closure proof.

Same-wave scopes are pairwise disjoint. Every repeated scope is ordered by an explicit dependency: A2 test/adapters through T05, docs through T09, wiring tests through T11, and performance adapters/tests through T15. No task has a per-task lens or automatic Build lens; Build mode carries `automatic_lenses=[]`. All tasks are agent-delivered. Long workers emit progress/completion/attention events, and ready disjoint work must not idle.

## Acceptance, contracts, modules, and edges

Every task owns at least one exact verbatim R-0001 acceptance string aligned to its responsibility; T18 retains all 12 and runs the terminal suite. The union of task contracts is exactly the six active requirement ids: `contract:delivery-mode-receipt`, `contract:review-kernel-authority`, `contract:producer-observation`, `contract:design-wiring-closure`, `contract:release-green-evidence`, and `contract:repository-preparation`.

The task set declares all approved graph modules: `(root)`, `.claude-plugin`, `.codex-plugin`, `.github/workflows`, `design`, `docs`, `exports`, `lenses`, `lenses/references`, `plan`, `scripts`, `skills/tp-go`, `taskplane`, `taskplane/tests`, and all nine exact new owner module identities. `design/schemas` and the verification evidence module `exports/verification` are additionally declared from bounded impact/gate authority. T18 copies all 47 approved proposed graph edges exactly; the executable wiring owner validates W01-W32 and every exact severed-edge/freshness selector.

The checked-in `design/compatibility.json` and `design/schemas/r0001-evidence-schemas.json` remain approved governance inputs. N=2.17.21/N-1=2.17.20 is emit-before-require with the four-cell host/plugin matrix. Closed schema ids, atomic prepare/commit/reconcile, exact predecessors, stable refusal ids, capability single-use/default-deny rules, no worker release credentials, and `cryptographic_authenticity_claimed=false` are binding. No signature, MAC, key, signer, verifier, trust file, or actor-authenticity claim is authorized.

## Budgets, release authority, and stop conditions

Normal `loop next` output is delta-shaped and strictly under 4,000 tokens. Before every dispatch, stop for human scope review at elapsed `>=8h`, sessions `>=60`, total tokens `>=150M`, or uncached input `>=25M`; `loop next` keeps less than 4,000 tokens. Admission is atomic and capped by disjoint-ready count, host free capacity, remaining sessions, and max-in-flight capacity. Retro must report parallelism factor and longest serial chain.

Feature-green advances Build only. Release-green additionally requires closed wiring, compatibility receipts, both package proofs, one terminal full matrix, an independently re-queried protected-platform proof for the exact pushed SHA, and an outside-model human recheck. A release override is only `released-unverified` with every skipped proof and never grants release authority. After T18, the workflow stops before push; any push, tag, install, publication, release credential, or origin mutation needs separate explicit human authority. A missing test/selector, broken W01-W32 edge, dirty/wrong SHA, stale platform proof, failed live canary, budget ceiling, or cold start `>=120s` blocks without widening scope.
