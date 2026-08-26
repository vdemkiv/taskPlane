# R-0001 Plan — remaining contract-first corrective delivery

## Authority, baseline, and settled inventory

This amended Plan is bound to clean integration source `ee01936617eeb58ba2552464c106ee9715dfbc6a`, the approved Design action fingerprint `74c2e2422a5232290c91eb18ae2e74355906a3b3839b9c5968dcc433839a535e`, approved Design content fingerprint `550aaeaacf8993baa53b2211a1c8d29b74fd143d1878704c30e1acfdfef08c03`, and the Design's authoritative baseline graph `a6a3c1e72c0c268648e3727cdcec904f60c41442a1f77bf16231bbdb84cd90a6` (50 modules, 156 edges; complete and not degraded). The approved Design remains structurally identical to `design/contract.json`; this Plan does not reinterpret or narrow it.

T01–T11 are completed and integrated inventory at `ee01936`; they are not executable tasks and must not be replayed. This includes the safe T10 fail-closed host boundary and canonical producer-edge correction plus T11's exact approved-criterion enforcement in Design/Plan/checkpoint adapters. W31 remains deliberately unclosed until the external live-host proof at T17a; T17b and T18 may close the global changed-producer map only after that proof. Candidate `27619bb` remains rejected evidence because its recorded-double path did not reach the real Codex host.

Candidate commits `8f6d9ae` (bounded brief-projection seam, 5/5) and `d39f0d8` (bounded plan-topology seam, 25/25 including false-ready artifact dependency and event atomicity) are reuse inputs only. They are neither integrated baseline nor task authority and do not make T12 or T13 passed. Separate T12–T14 tasks are removed because their full approved criteria require the later runtime/Retro wiring, while T14's declared test files are owned by and absent until the T12/T13 seam work exists. The already-claimed T14 worktree is clean and unused. T15 must integrate or reconstruct the admissible candidate deltas, independently re-attest both full approved performance criteria, implement `dispatch_telemetry`, and close real loop/runtime/Retro wiring in one governed task.

The one required bounded impact derivation for the aggregate T15 scope ran from clean integration source `ee01936617eeb58ba2552464c106ee9715dfbc6a`; the engine's complete graph source reports scanned revision `72734babcac7e857e37f06a74a3ea0dbf4d0064a`, graph fingerprint `fbf84162feb35f1647afa4e12e4554cdb7ea4c4c5539bdfc2042f0f44d1db862`, touched modules `taskplane` and `taskplane/tests`, unknown modules none, depth 3, and no truncation or degradation. Every remaining task retains the approved depth policy: local 3, contract-only boundary, contract 1, requirement 1.

## Remaining delivery DAG

There are six executable tasks in four waves. The only parallel pairs are the three pairings within `{T17a, T17b, T17c}`; the other 12 of 15 unordered task pairs are serialized by the explicit dependency path.

1. `{T15}` `performance-runtime-integration`: own brief projection, executable plan topology, dispatch telemetry, loop/runtime/event integration, and Retro metrics together. Preserve the incumbent 1,800-second event wait; immediate terminal wake, 256 pending events, 64 KiB event cap, idempotent reconciliation, partial-host attention, atomic admission, durable overflow, false-ready artifact-dependency refusal, event atomicity, and immutable execution-DAG rules are mandatory.
2. `{T16}` `same-sha-cold-start-gate`: measure an exact-SHA fresh checkout reaching its first executing pickup checkpoint in `<120s` before R-0013 resumes.
3. `{T17a, T17b, T17c}` disjoint verification fan-out for A2/live-host, release/wiring/repository, and performance/events. T17a exclusively owns the native exact-bound live Codex evaluator/EM receipt. T17b may verify the non-W31 closure surface in parallel but cannot publish global map closure from a self-minted or recorded substitute; T18 can terminally close W31 only after consuming T17a's external proof.
4. `{T18}` `terminal-full-matrix-and-closure`: run one terminal full matrix and seal the realized graph plus W01–W32 closure proof from the three verification predecessors.

| Task | Bounded owner | Inputs and barrier |
| --- | --- | --- |
| T15 | Brief projection, topology, telemetry, runtime, event flow, Retro | Reuse `8f6d9ae`/`d39f0d8` only after integration and full-criterion re-attestation |
| T16 | Same-SHA pickup measurement | Requires T15 |
| T17a | A2 and external live-host receipt | Requires T16; sole W31 producer proof |
| T17b | Wiring, release, repository verification | Requires T16; global closure remains conditional on T17a proof |
| T17c | Performance and event verification | Requires T16 |
| T18 | Terminal matrix and graph closure | Requires T17a, T17b, and T17c |

Same-wave scopes are pairwise disjoint. No task has a per-task lens or automatic Build lens; Build mode carries `automatic_lenses=[]`. Long workers emit progress/completion/attention events, and ready disjoint work must not idle.

## Acceptance, contracts, modules, and edges

Every executable task criterion is copied byte-for-byte from the approved Design acceptance map. T15 retains the two full performance criteria previously assigned to runtime integration; no seam-only criterion is invented. T17a remains the sole pre-terminal owner of the live-host criterion, T17b owns the global changed-producer map criterion subject to that external proof, and T18 retains all 12 criteria for same-SHA terminal re-attestation. The task-set contract union remains exactly the six active requirement ids: `contract:delivery-mode-receipt`, `contract:review-kernel-authority`, `contract:producer-observation`, `contract:design-wiring-closure`, `contract:release-green-evidence`, and `contract:repository-preparation`.

T15 declares the remaining `brief_projection`, `plan_topology`, `dispatch_telemetry`, and `plan` owner identities and their real runtime/Retro adapters. T16 and T17a/b/c remain unchanged. T18 changes only its terminal `new_modules` inventory by carrying forward the settled `taskplane/wiring_closure.py` identity for final re-attestation; this expands no scope and does not authorize reimplementation of T01–T11. Across the six executable tasks, the Plan covers every approved module, all six contracts, the approved depth policy, all 12 acceptance criteria, and all 47 proposed graph edges exactly. `exports/verification` remains the bounded verification-evidence module.

The checked-in `design/compatibility.json` and `design/schemas/r0001-evidence-schemas.json` remain approved governance inputs. N=2.17.21/N-1=2.17.20 is emit-before-require with the four-cell host/plugin matrix. Closed schema ids, atomic prepare/commit/reconcile, exact predecessors, stable refusal ids, capability single-use/default-deny rules, no worker release credentials, and `cryptographic_authenticity_claimed=false` are binding. No signature, MAC, key, signer, verifier, trust file, or actor-authenticity claim is authorized.

## Risks, budgets, release authority, and stop conditions

The highest remaining risk is accepting isolated seam evidence without the real runtime and Retro wiring required by the two full performance criteria. T15 therefore owns the complete integration radius and must re-attest candidate behavior, false-ready artifact dependencies, event atomicity, dispatch telemetry, and runtime/Retro consumption together. The next risks are fabricated W31 provenance, event-ledger duplication or partial-host promotion, host-capacity/budget races, and a cold-start receipt bound to the wrong SHA/home. The T17a external checkpoint, named barriers, atomic receipts, deterministic event tests, disjoint verification fan-out, and final same-SHA matrix contain those risks.

Normal `loop next` output is delta-shaped and strictly under 4,000 tokens. Before every dispatch, stop for human scope review at elapsed `>=8h`, sessions `>=60`, total tokens `>=150M`, or uncached input `>=25M`. Admission is atomic and capped by disjoint-ready count, host free capacity, remaining sessions, and max-in-flight capacity. Retro must report parallelism factor and longest serial chain.

Feature-green advances Build only. Release-green additionally requires closed wiring, compatibility receipts, both package proofs, one terminal full matrix, an independently re-queried protected-platform proof for the exact pushed SHA, and an outside-model human recheck. A release override is only `released-unverified` with every skipped proof and never grants release authority. After T18, the workflow stops before push; any push, tag, install, publication, release credential, or origin mutation needs separate explicit human authority. A missing test/selector, broken W01-W32 edge, dirty/wrong SHA, stale platform proof, failed live canary, budget ceiling, or cold start `>=120s` blocks without widening scope.
