# R-0001 Plan — remaining contract-first corrective delivery

## Authority, baseline, and settled inventory

This amended Plan is bound to clean execution source `4407dfc19ee24af5870eacd198a33af6aa8178b5`, the approved Design action fingerprint `74c2e2422a5232290c91eb18ae2e74355906a3b3839b9c5968dcc433839a535e`, approved Design content fingerprint `550aaeaacf8993baa53b2211a1c8d29b74fd143d1878704c30e1acfdfef08c03`, and the Design's authoritative baseline graph `a6a3c1e72c0c268648e3727cdcec904f60c41442a1f77bf16231bbdb84cd90a6` (50 modules, 156 edges; complete and not degraded). The approved Design remains structurally identical to `design/contract.json`; this Plan does not reinterpret or narrow it.

T01–T09 are completed, integrated, and verified inventory at `4407dfc`; they are not executable tasks and must not be replayed. That settled inventory comprises public delivery ports and hermetic harnesses; build mode and explicit empty-lens receipts; human-only unstarted ReviewKernel rebind; evaluator/EM producer observations; their shared adapters; v2.17.21 docs/security, repository-default, release-authority/compatibility, and final release-surface integration. Their realized modules and contracts remain Design inputs and terminal verification targets, not replacement work.

The required bounded impact derivation already completed once for this replan and is reused without repetition: graph fingerprint `b160c08039bb1898973c5ba515b70c6dc54d8a076cdedd821ebf6a5e8abaf8be`; touched modules `exports/verification`, `taskplane`, and `taskplane/tests`; unknown modules none; depth 3; not truncated or degraded. Every remaining task retains the approved depth policy: local 3, contract-only boundary, contract 1, requirement 1.

## Remaining delivery DAG

There are 11 executable tasks in seven serial waves. Tasks in each brace dispatch simultaneously by direct assignment; every other pair is serialized by the earliest named phase or shared-owner barrier on its dependency path. This classifies all 55 unordered pairs: a pair is `parallel` only when it appears in the same brace with disjoint scopes; otherwise it is `serialized`.

1. `{T10}` `wiring-closure-owner`: implement the closed AC selector and W01–W32 producer-consumer map. T09 is a satisfied predecessor outside the executable DAG.
2. `{T11}` `design-validation-owner`: bind Design/Plan/checkpoint adapters to the closure owner.
3. `{T12, T13, T14}` disjoint `delta-brief-owner`, `plan-topology-owner`, and `dispatch-telemetry-owner` work.
4. `{T15}` `loop-transition-owner`, `runtime-event-owner`, and `retro-owner` integration. Preserve the incumbent 1,800-second event wait; immediate terminal wake, 256 pending events, 64 KiB event cap, idempotent reconciliation, partial-host attention, atomic admission, durable overflow, and immutable execution-DAG rules are mandatory.
5. `{T16}` `pickup-owner`: same-SHA fresh-checkout measurement. The settled T07 repository-default work is a satisfied predecessor outside the executable DAG; `<120s` remains required before R-0013 may resume.
6. `{T17a, T17b, T17c}` disjoint verification fan-out for A2/live-host, release/wiring/repository, and performance/events.
7. `{T18}` `terminal-verification-owner`: one terminal full matrix plus realized graph and W01–W32 closure proof.

Same-wave scopes are pairwise disjoint. Every repeated executable scope is ordered by an explicit dependency: wiring tests through T11, performance adapters/tests through T15, pickup through T16, then disjoint verification and terminal closure. No task has a per-task lens or automatic Build lens; Build mode carries `automatic_lenses=[]`. Long workers emit progress/completion/attention events, and ready disjoint work must not idle.

## Acceptance, contracts, modules, and edges

Every remaining task owns at least one exact verbatim R-0001 acceptance string aligned to its responsibility. T18 retains all 12 criteria and runs the terminal suite, so already-realized and remaining behavior are re-attested together on one final SHA. The task-set contract union is exactly the six active requirement ids: `contract:delivery-mode-receipt`, `contract:review-kernel-authority`, `contract:producer-observation`, `contract:design-wiring-closure`, `contract:release-green-evidence`, and `contract:repository-preparation`.

T10–T16 declare the remaining owner modules they can change. T18 carries the settled realized module identities solely as terminal graph-verification coverage; this does not authorize reimplementation of T01–T09. Across the remaining tasks, the Plan covers every approved module: `(root)`, `.claude-plugin`, `.codex-plugin`, `.github/workflows`, `design`, `docs`, `exports`, `lenses`, `lenses/references`, `plan`, `scripts`, `skills/tp-go`, `taskplane`, `taskplane/tests`, and all nine exact new owner module identities. `exports/verification` remains the bounded verification-evidence module. T18 copies all 47 approved proposed graph edges exactly.

The checked-in `design/compatibility.json` and `design/schemas/r0001-evidence-schemas.json` remain approved governance inputs. N=2.17.21/N-1=2.17.20 is emit-before-require with the four-cell host/plugin matrix. Closed schema ids, atomic prepare/commit/reconcile, exact predecessors, stable refusal ids, capability single-use/default-deny rules, no worker release credentials, and `cryptographic_authenticity_claimed=false` are binding. No signature, MAC, key, signer, verifier, trust file, or actor-authenticity claim is authorized.

## Risks, budgets, release authority, and stop conditions

The highest remaining risk is false wiring closure: T10 and T11 therefore precede performance work, and T18 re-runs every exact selector and severed edge. The next risks are shared `loop.py` integration serializing disjoint owners, incomplete dispatch telemetry under host limits, event-ledger duplication or partial-host promotion, and a cold-start receipt bound to the wrong SHA/home. Named barriers, atomic receipts, deterministic event tests, disjoint verification fan-out, and the final same-SHA matrix contain those risks.

Normal `loop next` output is delta-shaped and strictly under 4,000 tokens. Before every dispatch, stop for human scope review at elapsed `>=8h`, sessions `>=60`, total tokens `>=150M`, or uncached input `>=25M`. Admission is atomic and capped by disjoint-ready count, host free capacity, remaining sessions, and max-in-flight capacity. Retro must report parallelism factor and longest serial chain.

Feature-green advances Build only. Release-green additionally requires closed wiring, compatibility receipts, both package proofs, one terminal full matrix, an independently re-queried protected-platform proof for the exact pushed SHA, and an outside-model human recheck. A release override is only `released-unverified` with every skipped proof and never grants release authority. After T18, the workflow stops before push; any push, tag, install, publication, release credential, or origin mutation needs separate explicit human authority. A missing test/selector, broken W01-W32 edge, dirty/wrong SHA, stale platform proof, failed live canary, budget ceiling, or cold start `>=120s` blocks without widening scope.
